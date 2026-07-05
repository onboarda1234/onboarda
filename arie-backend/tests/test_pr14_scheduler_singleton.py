"""PR-14 (audit H9) — scheduled ticks must run on exactly one ECS task.

Every task registers the same Tornado PeriodicCallbacks, so before this fix
each scheduled job (GDPR purge, monitoring automation, document health, memo
recovery, PRS-6 notifications) executed once PER TASK per interval —
duplicate purges and duplicate client notifications with 2+ tasks.

The fix: db.acquire_scheduler_lock(name) takes a PostgreSQL session advisory
lock on a dedicated NON-POOLED connection (pooled connections would leak the
session lock to the next borrower); server._singleton_tick wraps each tick so
non-holders skip. PostgreSQL tests run when TEST_POSTGRES_DSN /
DATABASE_URL_TEST is set.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pg_dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")


# ---------------------------------------------------------------------------
# Lease semantics without PostgreSQL (dev/test single-process)
# ---------------------------------------------------------------------------

def test_no_postgres_lease_always_acquired(monkeypatch):
    import db as db_module

    monkeypatch.setattr(db_module, "USE_POSTGRESQL", False)
    lease = db_module.acquire_scheduler_lock("gdpr_purge")
    assert lease.acquired is True
    lease.release()  # must be a no-op, not an error
    lease.release()  # idempotent


def test_unknown_scheduler_name_is_loud():
    import db as db_module

    with pytest.raises(KeyError):
        db_module.acquire_scheduler_lock("not_a_registered_scheduler")


def test_lock_keys_are_distinct_and_avoid_supervisor_chain_key():
    import db as db_module

    keys = list(db_module.SCHEDULER_LOCK_KEYS.values())
    assert len(keys) == len(set(keys)), "scheduler lock keys must be distinct"
    from supervisor.audit import _SUPERVISOR_CHAIN_LOCK_KEY

    assert _SUPERVISOR_CHAIN_LOCK_KEY not in keys, (
        "scheduler lock keys must not collide with the supervisor chain lock"
    )


# ---------------------------------------------------------------------------
# Mutual exclusion on real PostgreSQL
# ---------------------------------------------------------------------------

@pytest.fixture()
def pg_dsn():
    dsn = _pg_dsn()
    if not dsn:
        pytest.skip("No PostgreSQL DSN (TEST_POSTGRES_DSN / DATABASE_URL_TEST) available")
    return dsn


def test_pg_mutual_exclusion_and_release(pg_dsn):
    import db as db_module

    first = db_module.acquire_scheduler_lock("gdpr_purge", dsn=pg_dsn)
    assert first.acquired is True
    try:
        second = db_module.acquire_scheduler_lock("gdpr_purge", dsn=pg_dsn)
        assert second.acquired is False, (
            "two tasks acquired the same scheduler lock simultaneously"
        )
        second.release()
    finally:
        first.release()

    # Releasing (closing the dedicated connection) frees the lock.
    third = db_module.acquire_scheduler_lock("gdpr_purge", dsn=pg_dsn)
    assert third.acquired is True
    third.release()


def test_pg_different_schedulers_do_not_block_each_other(pg_dsn):
    import db as db_module

    a = db_module.acquire_scheduler_lock("gdpr_purge", dsn=pg_dsn)
    b = db_module.acquire_scheduler_lock("prs6_notifications", dsn=pg_dsn)
    try:
        assert a.acquired is True
        assert b.acquired is True
    finally:
        a.release()
        b.release()


def test_pg_unreachable_lock_service_skips_not_runs(monkeypatch):
    """Fail-closed: if the lock connection cannot be made, the tick is
    SKIPPED (running unlocked would reintroduce the duplicate-run bug)."""
    import db as db_module

    lease = db_module.acquire_scheduler_lock(
        "gdpr_purge",
        dsn="postgresql://postgres:wrong@127.0.0.1:1/nonexistent?connect_timeout=1",
    )
    assert lease.acquired is False
    lease.release()


# ---------------------------------------------------------------------------
# The decorator (server._singleton_tick)
# ---------------------------------------------------------------------------

class _FakeLease:
    def __init__(self, acquired):
        self.acquired = acquired
        self.released = False

    def release(self):
        self.released = True


def test_singleton_tick_runs_and_releases_when_acquired(monkeypatch):
    import server
    import db as db_module

    lease = _FakeLease(acquired=True)
    monkeypatch.setattr(db_module, "acquire_scheduler_lock", lambda name, dsn=None: lease)

    calls = []

    @server._singleton_tick("gdpr_purge")
    def tick():
        calls.append(1)
        return "ran"

    assert tick() == "ran"
    assert calls == [1]
    assert lease.released is True


def test_singleton_tick_skips_when_not_acquired(monkeypatch):
    import server
    import db as db_module

    lease = _FakeLease(acquired=False)
    monkeypatch.setattr(db_module, "acquire_scheduler_lock", lambda name, dsn=None: lease)

    calls = []

    @server._singleton_tick("gdpr_purge")
    def tick():
        calls.append(1)

    assert tick() is None
    assert calls == [], "tick body ran even though the lock was not acquired"


def test_singleton_tick_releases_lease_when_tick_raises(monkeypatch):
    import server
    import db as db_module

    lease = _FakeLease(acquired=True)
    monkeypatch.setattr(db_module, "acquire_scheduler_lock", lambda name, dsn=None: lease)

    @server._singleton_tick("gdpr_purge")
    def tick():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        tick()
    assert lease.released is True


# ---------------------------------------------------------------------------
# Static wiring — all five schedulers must be guarded
# ---------------------------------------------------------------------------

def test_all_five_schedulers_are_decorated():
    with open(os.path.join(BACKEND, "server.py"), encoding="utf-8") as fh:
        src = fh.read()

    import db as db_module

    decorated = set(re.findall(r"@_singleton_tick\(\"([a-z0-9_]+)\"\)", src))
    expected = set(db_module.SCHEDULER_LOCK_KEYS)
    assert decorated == expected, (
        f"scheduler ticks guarded {sorted(decorated)} != registered locks "
        f"{sorted(expected)} — every PeriodicCallback tick must be wrapped "
        "and every registered lock must be used"
    )

    # No PeriodicCallback tick may be registered without a guard: each known
    # tick function definition must be immediately preceded by the decorator.
    for tick_fn in (
        "_gdpr_purge_tick",
        "_monitoring_automation_tick",
        "_document_health_tick",
        "_memo_recovery_tick",
        "_periodic_review_notification_tick",
    ):
        m = re.search(rf"^(\s*)def {tick_fn}\(", src, re.MULTILINE)
        assert m, f"{tick_fn} not found in server.py"
        before = src[: m.start()].rstrip().splitlines()[-1].strip()
        assert before.startswith("@_singleton_tick("), (
            f"{tick_fn} is not guarded by @_singleton_tick — it would run on "
            "every ECS task simultaneously (audit H9)"
        )


def test_every_periodic_callback_registration_is_guarded():
    """Structural guard: a SIXTH scheduler added without @_singleton_tick must
    fail this test, not slip past a pinned five-name list (adversarial-review
    finding). Every function handed to PeriodicCallback — and every *_tick
    handed to call_later for an initial run — must be decorated."""
    with open(os.path.join(BACKEND, "server.py"), encoding="utf-8") as fh:
        src = fh.read()

    import db as db_module

    registered = re.findall(r"PeriodicCallback\(\s*(\w+)", src)
    assert registered, "no PeriodicCallback registrations found"
    assert len(registered) == len(db_module.SCHEDULER_LOCK_KEYS), (
        f"{len(registered)} PeriodicCallback registrations but "
        f"{len(db_module.SCHEDULER_LOCK_KEYS)} scheduler locks — a new "
        "scheduler must get its own lock key and @_singleton_tick guard"
    )

    initial_runs = [
        fn for fn in re.findall(r"call_later\(\s*[^,]+,\s*(\w+)", src)
        if fn.endswith("_tick") or fn.endswith("_notification_tick")
    ]

    for fn_name in set(registered) | set(initial_runs):
        m = re.search(rf"^(\s*)def {fn_name}\(", src, re.MULTILINE)
        assert m, f"{fn_name} is registered as a scheduler but has no def in server.py"
        before = src[: m.start()].rstrip().splitlines()[-1].strip()
        assert before.startswith("@_singleton_tick("), (
            f"{fn_name} is registered with PeriodicCallback/call_later but is "
            "not guarded by @_singleton_tick — it would run on every ECS task "
            "simultaneously (audit H9)"
        )
