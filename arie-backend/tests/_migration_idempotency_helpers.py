"""
Shared helpers for per-migration idempotency tests
(test_migration_004_idempotency.py, ..._005_..., ..._006_..., ..._007_...)
and the full-chain validation tests (test_migration_chain_full.py).

Each per-migration test module asserts the same two contract conditions
against a fresh SQLite database:

  1. ``test_fresh_init_db_then_runner_completes_cleanly`` -- a brand-new
     SQLite database (init_db has just run) followed by the file-based
     migration runner completes with no FAILED log line, emits
     ``Applied N migration(s) successfully``, records the migration
     version in ``schema_version``, and -- where applicable -- the
     post-condition column the migration was historically responsible
     for is present (because inline v2.x in db.py adds it).

  2. ``test_rerun_when_already_applied_is_noop`` -- on a database where
     the version is already recorded in ``schema_version``, re-running
     the runner reports zero applied migrations, no FAILED log line, and
     emits ``Database schema is up to date``.

These contracts mirror the bug repro for each migration:

  * 004 originally failed with ``duplicate column name: s3_key``.
  * 005 originally failed with ``near "EXISTS": syntax error``.
  * 006 originally failed with ``near "EXISTS": syntax error``.
  * 007 originally failed with ``near "AT": syntax error``.

After rewriting the four files as documented no-ops, both contract
conditions hold for every migration.

TEST-ISOLATION
--------------
The ``fresh_migration_db`` context manager reloads the ``config`` and
``db`` modules so they pick up the per-test ``DB_PATH``, and it
restores the original env vars and re-reloads the modules at exit so
later tests in the same pytest process see canonical configuration.
Without this teardown the rest of the backend test suite breaks ("no
such table: clients", etc.) because ``DB_PATH`` would otherwise stay
pointed at the deleted tmp_path file from a previous test.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
import os
import sys

# Make arie-backend importable regardless of pytest's cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@contextlib.contextmanager
def fresh_migration_db(tmp_path, monkeypatch):
    """Yield a DBConnection against a fresh SQLite database with
    ``init_db`` applied. Closes the connection and restores config /
    db module state at exit."""
    db_file = str(tmp_path / "test.db")

    orig_database_url = os.environ.get("DATABASE_URL")
    orig_environment = os.environ.get("ENVIRONMENT")
    orig_db_path = os.environ.get("DB_PATH")

    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DB_PATH", db_file)

    import config as config_module
    importlib.reload(config_module)
    import db as db_module
    importlib.reload(db_module)
    db_module.DB_PATH = db_file
    db_module.init_db()

    db = db_module.get_db()
    try:
        yield db
    finally:
        try:
            db.close()
        except Exception:
            pass
        # Restore env to canonical state and reload modules so later
        # tests see the same config they would on a fresh process.
        for var, value in (
            ("DATABASE_URL", orig_database_url),
            ("ENVIRONMENT", orig_environment),
            ("DB_PATH", orig_db_path),
        ):
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
        try:
            importlib.reload(config_module)
            importlib.reload(db_module)
        except Exception:
            pass


def _captured_migration_logs(caplog):
    """Return only the records emitted by the migrations runner logger."""
    return [r for r in caplog.records if r.name == "arie.migrations"]


def assert_fresh_init_then_runner_clean(
    tmp_path,
    monkeypatch,
    caplog,
    version,
    post_condition=None,
):
    """Run init_db then the file-based runner against a fresh SQLite
    database. Assert no FAILED log, "Applied N migration(s)
    successfully" emitted, ``version`` recorded in schema_version, and
    ``post_condition(db)`` (if provided) returns truthy."""
    with fresh_migration_db(tmp_path, monkeypatch) as db:
        from migrations.runner import run_all_migrations_with_connection

        with caplog.at_level(logging.INFO, logger="arie.migrations"):
            applied = run_all_migrations_with_connection(db)

        records = _captured_migration_logs(caplog)
        messages = [r.getMessage() for r in records]

        # No FAILED log line for any migration.
        assert not any("failed" in m.lower() for m in messages), (
            f"Migration runner emitted a FAILED log: {messages}"
        )
        # Summary log present.
        assert any(
            m.startswith(f"Applied {applied} migration(s) successfully")
            for m in messages
        ), (
            "Expected 'Applied N migration(s) successfully' summary log; "
            f"got: {messages}"
        )
        # Target version recorded in schema_version.
        rows = db.execute(
            "SELECT version FROM schema_version WHERE version = ?",
            (version,),
        ).fetchall()
        assert rows, f"Migration {version} not recorded in schema_version"

        if post_condition is not None:
            assert post_condition(db), (
                f"Post-condition for migration {version} not satisfied"
            )


def assert_already_applied_rerun_is_noop(
    tmp_path,
    monkeypatch,
    caplog,
    version,
    filename,
):
    """Apply the full chain once (so the target version is recorded),
    then re-run the runner. Assert zero migrations applied, no FAILED
    log, and the 'up to date' summary log line is emitted."""
    with fresh_migration_db(tmp_path, monkeypatch) as db:
        from migrations.runner import (
            ensure_schema_version_table,
            run_all_migrations_with_connection,
        )

        ensure_schema_version_table(db)
        run_all_migrations_with_connection(db)

        # Pre-condition: target version is recorded.
        pre = db.execute(
            "SELECT version FROM schema_version WHERE version = ?",
            (version,),
        ).fetchall()
        assert pre, (
            f"Pre-condition: expected version {version} ({filename}) to be "
            "recorded after the initial chain run"
        )

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="arie.migrations"):
            applied = run_all_migrations_with_connection(db)

        records = _captured_migration_logs(caplog)
        messages = [r.getMessage() for r in records]

        assert applied == 0, (
            f"Expected 0 migrations applied on re-run; got {applied}"
        )
        assert not any("failed" in m.lower() for m in messages), (
            f"Re-run emitted a FAILED log: {messages}"
        )
        assert any("up to date" in m.lower() for m in messages), (
            f"Expected 'Database schema is up to date' log; got: {messages}"
        )


def column_exists(db, table, column):
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    names = {r["name"] for r in rows}
    return column in names


def table_exists(db, table):
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None
