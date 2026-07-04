"""PR-11 (audit B3 / production condition PC-3) — serialized boot mutation.

A rolling ECS deploy boots 2+ tasks concurrently; each runs init_db → seeds →
migrations. Unserialized, they race (schema_version UNIQUE violations,
concurrent ALTERs). boot_lock.acquire_boot_migration_lock serializes the
phase with a PostgreSQL session advisory lock on a dedicated non-pooled
connection: bounded wait, loud failure on timeout (never proceed unlocked),
release-on-disconnect (a crashed holder cannot wedge the fleet).
PostgreSQL tests run when TEST_POSTGRES_DSN / DATABASE_URL_TEST is set.
"""

import os
import re
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pg_dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")


# ---------------------------------------------------------------------------
# Without PostgreSQL (single-process dev/test) the lease is a no-op
# ---------------------------------------------------------------------------

def test_no_postgres_lease_is_noop(monkeypatch):
    import db as db_module
    from boot_lock import acquire_boot_migration_lock

    monkeypatch.setattr(db_module, "USE_POSTGRESQL", False)
    lease = acquire_boot_migration_lock()
    lease.release()  # no-op, no error
    lease.release()  # idempotent


def test_lock_key_distinct_from_supervisor_chain_key():
    from boot_lock import BOOT_MIGRATION_LOCK_KEY
    from supervisor.audit import _SUPERVISOR_CHAIN_LOCK_KEY

    assert BOOT_MIGRATION_LOCK_KEY != _SUPERVISOR_CHAIN_LOCK_KEY


# ---------------------------------------------------------------------------
# Real PostgreSQL semantics
# ---------------------------------------------------------------------------

@pytest.fixture()
def pg_dsn():
    dsn = _pg_dsn()
    if not dsn:
        pytest.skip("No PostgreSQL DSN (TEST_POSTGRES_DSN / DATABASE_URL_TEST) available")
    return dsn


def test_pg_timeout_fails_loudly_never_unlocked(pg_dsn):
    """Second task must RAISE on timeout — proceeding unlocked is the bug."""
    from boot_lock import acquire_boot_migration_lock

    holder = acquire_boot_migration_lock(dsn=pg_dsn)
    try:
        t0 = time.monotonic()
        with pytest.raises(RuntimeError) as excinfo:
            acquire_boot_migration_lock(timeout_seconds=2, dsn=pg_dsn)
        elapsed = time.monotonic() - t0
        assert elapsed >= 1.5, "timeout fired far too early"
        assert "B3" in str(excinfo.value)
    finally:
        holder.release()


def test_pg_waiter_proceeds_after_holder_releases(pg_dsn):
    """The rolling-deploy scenario: task B waits, task A finishes, B proceeds."""
    from boot_lock import acquire_boot_migration_lock

    holder = acquire_boot_migration_lock(dsn=pg_dsn)
    result = {}

    def waiter():
        t0 = time.monotonic()
        lease = acquire_boot_migration_lock(timeout_seconds=30, dsn=pg_dsn)
        result["elapsed"] = time.monotonic() - t0
        lease.release()

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(1.2)  # generous: waiter must reach pg_advisory_lock under CI load
    holder.release()
    thread.join(timeout=10)
    assert not thread.is_alive(), "waiter never acquired after release"
    assert result["elapsed"] >= 0.8, "waiter did not actually block"


def test_pg_crashed_holder_releases_via_disconnect(pg_dsn):
    """A crashed task must not wedge the fleet: dropping the connection frees
    the lock without an explicit release()."""
    from boot_lock import acquire_boot_migration_lock

    holder = acquire_boot_migration_lock(dsn=pg_dsn)
    holder._conn.close()  # simulate process death (no graceful release call)

    t0 = time.monotonic()
    lease = acquire_boot_migration_lock(timeout_seconds=10, dsn=pg_dsn)
    elapsed = time.monotonic() - t0
    lease.release()
    assert elapsed < 5, "lock was not released by holder disconnect"


def test_pg_unreachable_database_raises(pg_dsn):
    from boot_lock import acquire_boot_migration_lock

    with pytest.raises(RuntimeError):
        acquire_boot_migration_lock(
            timeout_seconds=2,
            dsn="postgresql://postgres:wrong@127.0.0.1:1/nonexistent?connect_timeout=1",
        )


# ---------------------------------------------------------------------------
# Static wiring — the lock must bracket the boot mutation phase
# ---------------------------------------------------------------------------

def test_boot_sequence_is_bracketed_by_the_lock():
    with open(os.path.join(BACKEND, "server.py"), encoding="utf-8") as fh:
        src = fh.read()

    main_start = src.index('if __name__ == "__main__":')
    main_src = src[main_start:]

    acquire_pos = main_src.index("acquire_boot_migration_lock()")
    init_pos = main_src.index("startup: entering init_db")
    migrations_done_pos = main_src.index("startup: completed run_all_migrations")
    release_pos = main_src.index("_boot_lock_lease.release()")

    assert acquire_pos < init_pos, (
        "boot lock must be acquired BEFORE init_db — otherwise the schema "
        "mutation race (B3) is still open"
    )
    assert migrations_done_pos < release_pos, (
        "boot lock must be released only AFTER the migration runner completes"
    )


def test_admin_reset_takes_the_lock_before_the_wipe():
    """The lock must bracket the WHOLE reset: acquired before the TRUNCATE
    loop (a timeout after the wipe would strand an empty, never-re-seeded
    database) and released only after the re-seed."""
    with open(os.path.join(BACKEND, "server.py"), encoding="utf-8") as fh:
        src = fh.read()

    handler = src.index("class AdminResetDBHandler")
    window = src[handler: handler + 8000]

    acquire = window.index("acquire_boot_migration_lock(timeout_seconds=60)")
    wipe = window.index("TRUNCATE TABLE")
    reseed = window.index("Re-seed directly")
    release = window.index("_reset_lease.release()")

    assert acquire < wipe, (
        "admin-reset acquires the boot lock AFTER the wipe — a lock timeout "
        "would strand a wiped, never-re-seeded database"
    )
    assert wipe < reseed < release, (
        "boot lock must be released only after the re-seed completes"
    )
