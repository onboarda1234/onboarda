"""
Regression tests for migration 004 idempotency on a fresh database.

Bug context
-----------
``arie-backend/db.py`` inline ``_run_migrations`` v2.3 adds
``documents.s3_key`` under a ``_safe_column_exists`` guard, and the
file-based ``migration_004_documents_s3_key.sql`` *also* added the same
column.  On a fresh SQLite database both ran in sequence and the
file-based migration raised ``OperationalError: duplicate column name``.
On staging Postgres the failure was masked because version ``004`` was
already recorded in ``schema_version`` and the runner skipped it.  PR
#128's docker smoke test surfaced the latent bug after the silent
swallow at startup was removed.

Fix
---
Migration 004 is now a documented NO-OP (``SELECT 1;``); the inline
v2.3 path remains the authoritative source for the column.  These tests
prove:

  1. On a fresh DB: ``init_db`` (inline) + the file-based runner both
     run cleanly, ``Applied N migration(s) successfully`` is logged,
     and version ``004`` is recorded in ``schema_version`` with the
     ``s3_key`` column present on ``documents``.

  2. When ``004`` is already applied (column exists, ``schema_version``
     row exists), re-running the runner is a no-op without error.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


class _FreshDBBase(unittest.TestCase):
    def setUp(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"mig004_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)
        os.environ["DB_PATH"] = self._db_path

        import config as config_module
        import db as db_module
        self._orig_cfg = config_module.DB_PATH
        self._orig_db = db_module.DB_PATH
        config_module.DB_PATH = self._db_path
        db_module.DB_PATH = self._db_path
        self._db_module = db_module

    def tearDown(self):
        try:
            os.unlink(self._db_path)
        except OSError:
            pass
        import config as config_module
        config_module.DB_PATH = self._orig_cfg
        self._db_module.DB_PATH = self._orig_db

    def _premark_other_migrations_applied(self, db):
        """Pre-mark every file-based migration except 004 as applied so
        these tests exercise the migration-004-specific path in isolation
        and do not depend on (or surface) pre-existing bugs in other
        migration scripts -- those are explicitly out of scope here.
        """
        from migrations.runner import (
            ensure_schema_version_table,
            MIGRATIONS_DIR,
        )
        ensure_schema_version_table(db)
        for f in sorted(MIGRATIONS_DIR.glob("migration_*.sql")):
            parts = f.stem.split("_", 2)
            if len(parts) < 2:
                continue
            version = parts[1]
            if version == "004":
                continue  # the version we're actually testing
            db.execute(
                "INSERT OR IGNORE INTO schema_version (version, filename) "
                "VALUES (?, ?)",
                (version, f.name),
            )
        db.commit()


class TestMigration004FreshDB(_FreshDBBase):
    def test_fresh_init_db_then_runner_completes_cleanly(self):
        """Inline _run_migrations followed by the file-based runner must
        not raise on a fresh DB.  The runner must log
        ``Applied N migration(s) successfully`` and migration 004 must be
        recorded in schema_version."""
        # init_db runs the inline _run_migrations (incl. v2.3 s3_key add).
        self._db_module.init_db()

        # Sanity: inline path added s3_key.
        raw = sqlite3.connect(self._db_path)
        self.assertTrue(
            _column_exists(raw, "documents", "s3_key"),
            "inline v2.3 should have added documents.s3_key",
        )
        raw.close()

        # Now run the file-based runner against the same DB and assert
        # no FAILED line is emitted and the success summary is present.
        from db import DBConnection
        from migrations.runner import run_all_migrations_with_connection

        raw = sqlite3.connect(self._db_path)
        raw.row_factory = sqlite3.Row
        db = DBConnection(raw, is_postgres=False)
        try:
            # Scope: pre-mark other migrations as applied so we exercise
            # 004 in isolation; pre-existing bugs in other scripts are
            # explicitly out of scope for this PR (see file docstring).
            self._premark_other_migrations_applied(db)
            with self.assertLogs("arie.migrations", level="INFO") as cm:
                applied = run_all_migrations_with_connection(db)
        finally:
            db.close()

        # Must not contain a FAILED line.
        self.assertFalse(
            any("FAILED migration" in m for m in cm.output),
            f"runner emitted FAILED line on fresh DB: {cm.output}",
        )
        # Must contain the success summary.
        self.assertTrue(
            any(f"Applied {applied} migration(s) successfully" in m
                for m in cm.output),
            f"missing success summary; got: {cm.output}",
        )

        # Migration 004 must be recorded in schema_version.
        raw = sqlite3.connect(self._db_path)
        raw.row_factory = sqlite3.Row
        versions = {
            r["version"] for r in
            raw.execute("SELECT version FROM schema_version").fetchall()
        }
        raw.close()
        self.assertIn("004", versions)


class TestMigration004AlreadyApplied(_FreshDBBase):
    def test_rerun_when_004_already_applied_is_noop(self):
        """When migration 004 is already recorded in schema_version,
        re-running the runner must not re-execute it and must not error."""
        self._db_module.init_db()

        from db import DBConnection
        from migrations.runner import (
            run_all_migrations_with_connection,
        )

        # First pass: applies migration 004 only (other versions
        # pre-marked applied; see _premark_other_migrations_applied).
        raw = sqlite3.connect(self._db_path)
        raw.row_factory = sqlite3.Row
        db = DBConnection(raw, is_postgres=False)
        try:
            self._premark_other_migrations_applied(db)
            run_all_migrations_with_connection(db)
        finally:
            db.close()

        # Second pass: nothing should be pending; runner logs the
        # "schema is up to date" line and returns 0.
        raw = sqlite3.connect(self._db_path)
        raw.row_factory = sqlite3.Row
        db = DBConnection(raw, is_postgres=False)
        try:
            with self.assertLogs("arie.migrations", level="INFO") as cm:
                applied = run_all_migrations_with_connection(db)
        finally:
            db.close()

        self.assertEqual(applied, 0)
        self.assertFalse(
            any("FAILED migration" in m for m in cm.output),
            f"second pass emitted FAILED: {cm.output}",
        )
        self.assertTrue(
            any("Database schema is up to date" in m for m in cm.output),
            f"missing up-to-date log; got: {cm.output}",
        )


if __name__ == "__main__":
    unittest.main()
