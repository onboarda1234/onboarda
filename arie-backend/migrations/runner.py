"""
ARIE Finance — Database Migration Runner
=========================================
Automatically executes pending migrations at startup.
Tracks applied migrations in a schema_version table.
Supports both SQLite (development) and PostgreSQL (production).

Failure semantics (closes #127)
-------------------------------
The runner is **loud** and **fail-closed** by default:

* Every migration that raises emits a single
  ``FAILED migration NNN: <ExcType>: <msg>`` log line at ``ERROR`` level
  with the full traceback (``exc_info=True``).
* The connection is explicitly rolled back after a failure so the bad
  transaction state cannot leak into any later code path.
* After all migrations are attempted, the runner emits exactly one of:
    - ``Applied N migration(s) successfully`` — clean run, OR
    - ``Applied X of Y migration(s); Z failed: [NNN, ...]`` — partial.
* Default behaviour on any failure is to raise ``MigrationFailure`` so
  the application halts startup.  A regulated AML platform should not
  boot with un-applied schema migrations.
* Override the default via ``MIGRATION_FAILURE_MODE=continue`` (case
  insensitive).  In ``continue`` mode the runner emits
  ``Skipped migration NNN due to earlier failure`` for every migration
  that was not attempted and returns the count of successfully applied
  migrations instead of raising.  This override is intended only for
  non-production debugging and CI bring-up.
"""

import hashlib
import logging
import os
import traceback
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("arie.migrations")

MIGRATIONS_DIR = Path(__file__).parent / "scripts"

#: Environment variable name controlling the runner's failure policy.
#: Default behaviour (variable unset or any value other than ``continue``)
#: is fail-closed — a single migration failure raises ``MigrationFailure``
#: and halts startup.
MIGRATION_FAILURE_MODE_ENV = "MIGRATION_FAILURE_MODE"


class MigrationFailure(RuntimeError):
    """Raised when one or more migrations fail under the default
    fail-closed policy.  Carries the list of failed versions for
    structured handling by callers."""

    def __init__(self, failed_versions, applied_count, total_count):
        self.failed_versions = list(failed_versions)
        self.applied_count = applied_count
        self.total_count = total_count
        super().__init__(
            "Applied %d of %d migration(s); %d failed: %s"
            % (applied_count, total_count, len(failed_versions), failed_versions)
        )


def _failure_mode_continue() -> bool:
    """Return True iff ``MIGRATION_FAILURE_MODE=continue`` is set."""
    return (os.environ.get(MIGRATION_FAILURE_MODE_ENV, "") or "").strip().lower() == "continue"


def _safe_rollback(db) -> None:
    """Roll back the connection's current transaction, swallowing any
    secondary error.  Called after a migration raises so the failed
    transaction state does not leak into subsequent migrations or into
    later startup code paths.

    ``DBConnection`` does not expose a ``rollback()`` method (legacy
    omission); we go through ``db.conn`` directly, which both
    ``psycopg2`` and ``sqlite3`` connections support.
    """
    try:
        conn = getattr(db, "conn", None) or db
        conn.rollback()
    except Exception as rb_exc:  # pragma: no cover - defensive
        logger.warning("Migration runner: rollback after failure raised: %s", rb_exc)


def _get_schema_version_ddl(is_postgres: bool) -> str:
    """Return DDL for the schema_version tracking table."""
    if is_postgres:
        return """
        CREATE TABLE IF NOT EXISTS schema_version (
            id SERIAL PRIMARY KEY,
            version TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            description TEXT DEFAULT '',
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            checksum TEXT
        )
        """
    else:
        return """
        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            description TEXT DEFAULT '',
            applied_at TEXT DEFAULT (datetime('now')),
            checksum TEXT
        )
        """


def ensure_schema_version_table(db):
    """Create the schema_version tracking table if it doesn't exist."""
    is_postgres = getattr(db, 'is_postgres', False)
    ddl = _get_schema_version_ddl(is_postgres)
    if is_postgres:
        # Use execute for PostgreSQL (executescript uses cursor.execute for PG)
        cursor = db._cursor_or_create()
        cursor.execute(ddl)
    else:
        db.executescript(ddl)
    db.commit()

    # Migration: add description column if missing (for older schema_version tables)
    try:
        db.execute("SELECT description FROM schema_version LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE schema_version ADD COLUMN description TEXT DEFAULT ''")
            db.commit()
        except Exception:
            pass  # Column may already exist


def get_applied_versions(db):
    """Return set of already-applied migration versions."""
    rows = db.execute("SELECT version FROM schema_version ORDER BY id").fetchall()
    return {row["version"] if isinstance(row, dict) else row[0] for row in rows}


def get_pending_migrations(db):
    """Return list of (version, filepath, description) tuples for pending migrations, sorted."""
    applied = get_applied_versions(db)
    if not MIGRATIONS_DIR.exists():
        return []

    pending = []
    for f in sorted(MIGRATIONS_DIR.glob("migration_*.sql")):
        # Extract version from filename: migration_001_xxx.sql -> 001
        parts = f.stem.split("_", 2)
        if len(parts) >= 2:
            version = parts[1]
            description = parts[2].replace("_", " ") if len(parts) > 2 else ""
            if version not in applied:
                pending.append((version, f, description))
    return pending


def run_migration(db, version, filepath, description=""):
    """Execute a single migration file.

    On success: logs ``Migration NNN applied successfully``.
    On failure: logs ``FAILED migration NNN: <ExcType>: <msg>`` at ERROR
    with the full traceback, rolls the connection back, and re-raises.
    The caller decides whether to halt or continue.
    """
    logger.info("Applying migration %s: %s", version, filepath.name)
    sql = filepath.read_text(encoding="utf-8")

    checksum = hashlib.sha256(sql.encode()).hexdigest()[:16]

    try:
        db.executescript(sql)
        db.execute(
            "INSERT INTO schema_version (version, filename, description, checksum) VALUES (?, ?, ?, ?)",
            (version, filepath.name, description, checksum)
        )
        db.commit()
        logger.info("Migration %s applied successfully", version)
    except Exception as e:
        # Loud, structured failure log — exact format relied on by tests
        # and operational alerting.  exc_info=True attaches the traceback.
        logger.error(
            "FAILED migration %s: %s: %s",
            version, type(e).__name__, e,
            exc_info=True,
        )
        _safe_rollback(db)
        raise


def run_all_migrations_with_connection(db):
    """
    Run all pending database migrations using a DBConnection instance.
    Works with both SQLite and PostgreSQL via the DBConnection abstraction.

    Behaviour
    ---------
    Always emits a final summary line:

        ``Applied N migration(s) successfully``               -- clean run
        ``Applied X of Y migration(s); Z failed: [NNN, ...]`` -- partial

    Default policy on any failure is fail-closed: ``MigrationFailure`` is
    raised after the summary so the startup path halts.  Set the
    environment variable ``MIGRATION_FAILURE_MODE=continue`` to instead
    log ``Skipped migration NNN due to earlier failure`` for each
    unattempted migration and return the applied count.
    """
    ensure_schema_version_table(db)
    pending = get_pending_migrations(db)

    if not pending:
        logger.info("Database schema is up to date")
        return 0

    total = len(pending)
    logger.info("Found %d pending migration(s)", total)

    applied = 0
    failed = []   # list of failed version strings
    skipped = []  # list of unattempted version strings (continue mode)
    continue_on_failure = _failure_mode_continue()

    for index, (version, filepath, description) in enumerate(pending):
        if failed and not continue_on_failure:
            # Fail-closed default: stop attempting further migrations the
            # moment one fails.  We still want to log the unattempted ones
            # for operator visibility.
            for skip_version, skip_path, _desc in pending[index:]:
                skipped.append(skip_version)
                logger.error(
                    "Skipped migration %s due to earlier failure (%s)",
                    skip_version, skip_path.name,
                )
            break

        try:
            run_migration(db, version, filepath, description)
            applied += 1
        except Exception:
            failed.append(version)
            # In continue mode, fall through to the next migration so a
            # single bad script doesn't block unrelated later changes.
            # In fail-closed mode, the loop guard at the top of the next
            # iteration logs the remaining migrations as skipped and breaks.
            if not continue_on_failure:
                continue

    if failed:
        logger.error(
            "Applied %d of %d migration(s); %d failed: %s",
            applied, total, len(failed), failed,
        )
        if not continue_on_failure:
            raise MigrationFailure(failed, applied, total)
        # continue mode: surface a warning and return the applied count.
        logger.warning(
            "MIGRATION_FAILURE_MODE=continue: startup proceeding despite "
            "%d failed migration(s): %s",
            len(failed), failed,
        )
        return applied

    logger.info("Applied %d migration(s) successfully", applied)
    return applied


def run_all_migrations(db_path=None):
    """
    Run all pending database migrations.
    If db_path is provided, uses SQLite directly (backward compatible).
    Otherwise, uses the DBConnection abstraction from db module.

    Propagates :class:`MigrationFailure` to the caller under the
    fail-closed default; see module docstring.
    """
    if db_path:
        # Legacy SQLite-only path (backward compatible)
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            # Wrap in a minimal interface for compatibility
            from db import DBConnection
            db = DBConnection(conn, is_postgres=False)
            return run_all_migrations_with_connection(db)
        finally:
            conn.close()
    else:
        # Use DBConnection abstraction (supports both SQLite and PostgreSQL)
        from db import get_db
        db = get_db()
        try:
            return run_all_migrations_with_connection(db)
        finally:
            db.close()


def get_migration_status():
    """
    Return migration status information.
    Returns: { applied: [...], pending: [...], current_version: str }
    """
    from db import get_db
    db = get_db()
    try:
        ensure_schema_version_table(db)

        # Get applied migrations
        applied_rows = db.execute(
            "SELECT version, filename, description, applied_at, checksum FROM schema_version ORDER BY id"
        ).fetchall()

        applied = []
        for row in applied_rows:
            applied.append({
                "version": row["version"],
                "filename": row["filename"],
                "description": row.get("description", ""),
                "applied_at": row["applied_at"],
                "checksum": row.get("checksum", ""),
            })

        # Get pending migrations
        pending = get_pending_migrations(db)
        pending_list = [
            {"version": v, "filename": f.name, "description": d}
            for v, f, d in pending
        ]

        # Current version
        current = applied[-1]["version"] if applied else "none"

        return {
            "applied": applied,
            "pending": pending_list,
            "current_version": current,
            "total_applied": len(applied),
            "total_pending": len(pending_list),
        }
    finally:
        db.close()
