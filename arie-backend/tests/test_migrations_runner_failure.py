"""
Closes #127: the file-based migrations runner must be loud and
fail-closed by default.

These tests cover the four runner-side acceptance criteria:

  1. A failing migration logs ``FAILED migration NNN: <type>: <msg>`` at
     ERROR level with the exception details (and the traceback via
     ``exc_info=True``).
  2. The runner halts (raises ``MigrationFailure``) under the default
     fail-closed policy.
  3. ``MIGRATION_FAILURE_MODE=continue`` causes ``Skipped migration NNN
     due to earlier failure`` to be logged for unattempted migrations
     and lets startup proceed.
  4. A fully successful run still emits ``Applied N migration(s)
     successfully`` -- the baseline log format from healthy deploys.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _make_runner_with_dir(tmp_dir: Path):
    """Reload migrations.runner with MIGRATIONS_DIR pointed at tmp_dir.

    Returns ``(runner, original_dir)`` so the caller can restore it in
    teardown -- otherwise other tests that import ``run_all_migrations``
    will see an empty (or deleted) migrations directory.
    """
    import importlib

    import migrations.runner as runner
    importlib.reload(runner)
    original = runner.MIGRATIONS_DIR
    runner.MIGRATIONS_DIR = tmp_dir
    return runner, original


def _write_sql(tmp_dir: Path, version: str, body: str) -> Path:
    p = tmp_dir / f"migration_{version}_test.sql"
    p.write_text(body, encoding="utf-8")
    return p


class _RunnerFixtureBase(unittest.TestCase):
    def setUp(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"runner_test_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="runner_scripts_"))
        # Restore default failure mode at start of each test.
        self._old_env = os.environ.pop("MIGRATION_FAILURE_MODE", None)

        from db import DBConnection
        raw = sqlite3.connect(self._db_path)
        raw.row_factory = sqlite3.Row
        # Enable FK enforcement so SQLite mirrors Postgres semantics
        # for the tests in this module.
        raw.execute("PRAGMA foreign_keys = ON")
        self._conn = DBConnection(raw, is_postgres=False)
        self._runner, self._orig_migrations_dir = _make_runner_with_dir(self._tmp_dir)

    def tearDown(self):
        try:
            self._conn.close()
        except Exception:
            pass
        # Restore MIGRATIONS_DIR so later tests in the suite that import
        # ``run_all_migrations`` still see the real scripts directory.
        try:
            self._runner.MIGRATIONS_DIR = self._orig_migrations_dir
        except Exception:
            pass
        try:
            os.unlink(self._db_path)
        except OSError:
            pass
        for f in self._tmp_dir.glob("*"):
            f.unlink()
        self._tmp_dir.rmdir()
        if self._old_env is not None:
            os.environ["MIGRATION_FAILURE_MODE"] = self._old_env
        else:
            os.environ.pop("MIGRATION_FAILURE_MODE", None)


class TestRunnerHealthyRun(_RunnerFixtureBase):
    def test_clean_run_emits_applied_n_successfully(self):
        _write_sql(self._tmp_dir, "990", "CREATE TABLE t990 (id INTEGER);")
        _write_sql(self._tmp_dir, "991", "CREATE TABLE t991 (id INTEGER);")

        with self.assertLogs("arie.migrations", level="INFO") as cm:
            count = self._runner.run_all_migrations_with_connection(self._conn)

        self.assertEqual(count, 2)
        self.assertTrue(
            any("Applied 2 migration(s) successfully" in m for m in cm.output),
            f"Expected summary log; got: {cm.output}",
        )
        # Should not contain a FAILED line.
        self.assertFalse(any("FAILED migration" in m for m in cm.output))


class TestRunnerFailClosedDefault(_RunnerFixtureBase):
    def test_failed_migration_logs_failed_at_error_with_traceback(self):
        # 990 is good; 991 is bad SQL; 992 must be skipped.
        _write_sql(self._tmp_dir, "990", "CREATE TABLE t990 (id INTEGER);")
        _write_sql(self._tmp_dir, "991", "THIS IS NOT VALID SQL;")
        _write_sql(self._tmp_dir, "992", "CREATE TABLE t992 (id INTEGER);")

        with self.assertLogs("arie.migrations", level="DEBUG") as cm:
            with self.assertRaises(self._runner.MigrationFailure) as ctx:
                self._runner.run_all_migrations_with_connection(self._conn)

        # AC #1: FAILED migration NNN line at ERROR with type + msg.
        failed_lines = [r for r in cm.records if r.levelno == logging.ERROR
                        and "FAILED migration 991" in r.getMessage()]
        self.assertTrue(failed_lines, f"missing FAILED ERROR log: {cm.output}")
        msg = failed_lines[0].getMessage()
        self.assertIn("FAILED migration 991", msg)
        # Type and message must be part of the line.
        self.assertRegex(msg, r"FAILED migration 991: \w+:")
        # exc_info must be attached for the traceback.
        self.assertIsNotNone(failed_lines[0].exc_info)

        # AC #3 (default mode): Skipped migration line for 992.
        self.assertTrue(
            any("Skipped migration 992 due to earlier failure" in m for m in cm.output),
            f"missing skipped log for 992: {cm.output}",
        )

        # AC #2: MigrationFailure raised with structured payload.
        exc = ctx.exception
        self.assertEqual(exc.failed_versions, ["991"])
        self.assertEqual(exc.applied_count, 1)  # 990 succeeded
        self.assertEqual(exc.total_count, 3)

        # 990 should be recorded in schema_version, 991/992 must not be.
        rows = self._conn.execute(
            "SELECT version FROM schema_version ORDER BY version"
        ).fetchall()
        applied = {r["version"] for r in rows}
        self.assertIn("990", applied)
        self.assertNotIn("991", applied)
        self.assertNotIn("992", applied)


class TestRunnerContinueMode(_RunnerFixtureBase):
    def test_continue_mode_logs_skipped_and_returns_applied_count(self):
        os.environ["MIGRATION_FAILURE_MODE"] = "continue"

        _write_sql(self._tmp_dir, "990", "CREATE TABLE t990 (id INTEGER);")
        _write_sql(self._tmp_dir, "991", "THIS IS NOT VALID SQL;")
        _write_sql(self._tmp_dir, "992", "CREATE TABLE t992 (id INTEGER);")

        with self.assertLogs("arie.migrations", level="DEBUG") as cm:
            count = self._runner.run_all_migrations_with_connection(self._conn)

        # In continue mode the runner does NOT raise.
        # 990 and 992 should both have applied; 991 is the only failure.
        self.assertEqual(count, 2)
        applied = {r["version"] for r in self._conn.execute(
            "SELECT version FROM schema_version ORDER BY version"
        ).fetchall()}
        self.assertIn("990", applied)
        self.assertIn("992", applied)
        self.assertNotIn("991", applied)

        # FAILED line still present at ERROR.
        self.assertTrue(any(
            r.levelno == logging.ERROR and "FAILED migration 991" in r.getMessage()
            for r in cm.records
        ))
        # Continue-mode warning surfaced.
        self.assertTrue(any(
            "MIGRATION_FAILURE_MODE=continue" in m for m in cm.output
        ))
        # Summary line records the partial state.
        self.assertTrue(any(
            "Applied 2 of 3 migration(s); 1 failed: ['991']" in m
            for m in cm.output
        ))


class TestRunnerHandlesPgFailedTransactionShape(_RunnerFixtureBase):
    """Simulate a psycopg2 ``InFailedSqlTransaction`` -- the symptom #127
    blames -- by raising it from ``run_migration``.  The runner must log
    FAILED with the exception class name and re-raise as MigrationFailure.
    """

    def test_in_failed_sql_transaction_is_loud(self):
        _write_sql(self._tmp_dir, "990", "CREATE TABLE t990 (id INTEGER);")
        _write_sql(self._tmp_dir, "991", "CREATE TABLE t991 (id INTEGER);")

        class FakeInFailedSqlTransaction(Exception):
            pass

        original_run_migration = self._runner.run_migration

        def fake_run(db, version, filepath, description=""):
            if version == "991":
                # Mimic the runner's own failure path (log + rollback +
                # raise) so we exercise the wrapping caller behaviour.
                logger = logging.getLogger("arie.migrations")
                exc = FakeInFailedSqlTransaction(
                    "current transaction is aborted, "
                    "commands ignored until end of transaction block"
                )
                try:
                    raise exc
                except FakeInFailedSqlTransaction as e:
                    logger.error(
                        "FAILED migration %s: %s: %s",
                        version, type(e).__name__, e,
                        exc_info=True,
                    )
                    raise
            return original_run_migration(db, version, filepath, description)

        with mock.patch.object(self._runner, "run_migration", fake_run):
            with self.assertLogs("arie.migrations", level="ERROR") as cm:
                with self.assertRaises(self._runner.MigrationFailure):
                    self._runner.run_all_migrations_with_connection(self._conn)

        # FAILED line names the exception class.
        self.assertTrue(any(
            "FAILED migration 991: FakeInFailedSqlTransaction" in m
            for m in cm.output
        ))


if __name__ == "__main__":
    unittest.main()
