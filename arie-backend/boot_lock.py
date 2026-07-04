"""Cross-task boot/migration advisory lock (audit B3 / production condition PC-3).

A rolling ECS deploy boots two or more tasks concurrently, and each task runs
the same boot mutation phase: init_db (CREATE/ALTER + seeds) followed by the
file-migration runner. Unserialized, those tasks race — schema_version UNIQUE
violations, concurrent ALTERs — and a deploy can fall over halfway.

acquire_boot_migration_lock() serializes the whole phase with a PostgreSQL
session advisory lock held on a DEDICATED (non-pooled) connection:

* The first task acquires and runs the phase; later tasks block in
  pg_advisory_lock until it finishes, then run the now-idempotent phase.
* The wait is bounded (statement_timeout on the dedicated session). On
  timeout the lease raises and startup FAILS LOUDLY — never proceeds
  unlocked, because racing the mutation phase is the bug this exists to fix.
  ECS restarts the task and it retries.
* Process exit — including a crash mid-migration — closes the dedicated
  connection, which releases the lock unconditionally. No lease can outlive
  its holder.
* Without PostgreSQL (single-process dev/test on SQLite) the lease is a
  no-op: there is no second task to race.

The supervisor-chain append lock (supervisor/audit.py, key 8674309921) and
this key must stay distinct.

Operational note: a waiting task blocks BEFORE it starts listening, so
container/ALB health-check grace periods shorter than the first task's
mutation phase will kill-and-restart waiters (safe — the waiter holds
nothing, disconnect is clean, ECS retries — but noisy). The typical phase
is seconds; if a long migration is expected, ensure the ECS healthCheck
startPeriod / ALB grace period accommodates it.
"""

import logging

logger = logging.getLogger("arie")

BOOT_MIGRATION_LOCK_KEY = 8674309941


class BootLockLease:
    """Holds the boot-migration advisory lock via a dedicated connection."""

    def __init__(self, conn):
        self._conn = conn

    def release(self):
        if self._conn is not None:
            try:
                self._conn.close()  # disconnecting releases the session lock
            except Exception:
                pass
            self._conn = None


def acquire_boot_migration_lock(timeout_seconds: int = 300, dsn: str = None) -> BootLockLease:
    """Block until this process holds the boot-migration lock, then return a lease.

    Raises RuntimeError if the lock cannot be acquired within
    ``timeout_seconds`` (or the lock connection fails) — boot must fail
    loudly rather than run the schema mutation phase unserialized.
    """
    from db import DATABASE_URL, USE_POSTGRESQL

    dsn = dsn or (DATABASE_URL if USE_POSTGRESQL else None)
    if not dsn:
        return BootLockLease(None)

    import psycopg2

    conn = None
    try:
        conn = psycopg2.connect(dsn, sslmode="require", connect_timeout=10)
        conn.autocommit = True
        with conn.cursor() as cur:
            # Bound the advisory-lock wait on this dedicated session only.
            # (SET does not accept bound parameters; set_config does.)
            cur.execute(
                "SELECT set_config('statement_timeout', %s, false)",
                (str(int(timeout_seconds * 1000)),),
            )
            cur.execute("SELECT pg_advisory_lock(%s)", (BOOT_MIGRATION_LOCK_KEY,))
            cur.execute("SELECT set_config('statement_timeout', '0', false)")
        return BootLockLease(conn)
    except Exception as e:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        raise RuntimeError(
            f"boot-migration lock not acquired within {timeout_seconds}s — "
            "another task may be mid-migration or stuck; failing startup "
            "loudly rather than racing the schema mutation phase "
            f"(audit B3 / PC-3): {e}"
        ) from e
