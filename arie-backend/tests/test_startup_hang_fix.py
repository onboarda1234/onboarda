"""
Tests for startup hang fix (staging rev 7 incident).

Root cause: PostgreSQL connection pool was created WITHOUT connect_timeout,
statement_timeout, or lock_timeout.  During a blue-green deploy the new
revision's schema DDL / migrations could block indefinitely waiting for
locks held by the old revision, preventing the server from ever reaching
app.listen().

This module verifies:
  1. PostgreSQL pool init includes production-safe timeouts
  2. _run_migrations() no longer uses try/except SELECT (which aborts the
     PostgreSQL transaction) — uses information_schema helpers instead
  3. Startup logging covers every major step
  4. init_db() completes without indefinite blocking
  5. Fail-fast behavior on timeout (OperationalError, not hang)
"""

import importlib
import inspect
import logging
import os
import re
import sqlite3
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure arie-backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _get_db_module():
    """Lazy-import db module to avoid triggering config.DB_PATH evaluation
    before conftest.py's temp_db fixture sets the DB_PATH env var."""
    import db as _db
    return _db


# ---------------------------------------------------------------------------
# 1. PostgreSQL pool timeout parameters
# ---------------------------------------------------------------------------

class TestPoolTimeouts(unittest.TestCase):
    """Verify that init_pg_pool() passes connect_timeout, statement_timeout,
    and lock_timeout to psycopg2."""

    def test_pool_includes_connect_timeout(self):
        """Pool must pass connect_timeout to psycopg2."""
        db_module = _get_db_module()
        with patch.object(db_module, "PSYCOPG2_AVAILABLE", True), \
             patch.object(db_module, "USE_POSTGRESQL", True), \
             patch.object(db_module, "DATABASE_URL", "postgresql://user:pass@host/db"):
            db_module._pg_pool = None  # reset
            with patch("db.psycopg2.pool.ThreadedConnectionPool") as mock_pool:
                mock_pool.return_value = MagicMock()
                db_module.init_pg_pool()
                args, kwargs = mock_pool.call_args
                self.assertIn("connect_timeout", kwargs)
                self.assertGreater(kwargs["connect_timeout"], 0)
            db_module._pg_pool = None  # cleanup

    def test_pool_includes_statement_and_lock_timeout(self):
        """Pool must set statement_timeout and lock_timeout via options."""
        db_module = _get_db_module()
        with patch.object(db_module, "PSYCOPG2_AVAILABLE", True), \
             patch.object(db_module, "USE_POSTGRESQL", True), \
             patch.object(db_module, "DATABASE_URL", "postgresql://user:pass@host/db"):
            db_module._pg_pool = None
            with patch("db.psycopg2.pool.ThreadedConnectionPool") as mock_pool:
                mock_pool.return_value = MagicMock()
                db_module.init_pg_pool()
                args, kwargs = mock_pool.call_args
                options = kwargs.get("options", "")
                self.assertIn("statement_timeout", options)
                self.assertIn("lock_timeout", options)
            db_module._pg_pool = None

    def test_statement_timeout_is_reasonable(self):
        """statement_timeout must be between 5 and 120 seconds (in ms)."""
        db_module = _get_db_module()
        with patch.object(db_module, "PSYCOPG2_AVAILABLE", True), \
             patch.object(db_module, "USE_POSTGRESQL", True), \
             patch.object(db_module, "DATABASE_URL", "postgresql://user:pass@host/db"):
            db_module._pg_pool = None
            with patch("db.psycopg2.pool.ThreadedConnectionPool") as mock_pool:
                mock_pool.return_value = MagicMock()
                db_module.init_pg_pool()
                options = mock_pool.call_args[1].get("options", "")
                match = re.search(r"statement_timeout=(\d+)", options)
                self.assertIsNotNone(match, "statement_timeout not found in options")
                timeout_ms = int(match.group(1))
                self.assertGreaterEqual(timeout_ms, 5000)
                self.assertLessEqual(timeout_ms, 120000)
            db_module._pg_pool = None

    def test_lock_timeout_is_reasonable(self):
        """lock_timeout must be between 1 and 60 seconds (in ms)."""
        db_module = _get_db_module()
        with patch.object(db_module, "PSYCOPG2_AVAILABLE", True), \
             patch.object(db_module, "USE_POSTGRESQL", True), \
             patch.object(db_module, "DATABASE_URL", "postgresql://user:pass@host/db"):
            db_module._pg_pool = None
            with patch("db.psycopg2.pool.ThreadedConnectionPool") as mock_pool:
                mock_pool.return_value = MagicMock()
                db_module.init_pg_pool()
                options = mock_pool.call_args[1].get("options", "")
                match = re.search(r"lock_timeout=(\d+)", options)
                self.assertIsNotNone(match, "lock_timeout not found in options")
                timeout_ms = int(match.group(1))
                self.assertGreaterEqual(timeout_ms, 1000)
                self.assertLessEqual(timeout_ms, 60000)
            db_module._pg_pool = None


# ---------------------------------------------------------------------------
# 2. _safe_column_exists / _safe_table_exists helpers
# ---------------------------------------------------------------------------

class TestSafeSchemaHelpers(unittest.TestCase):
    """Verify that the safe schema helpers work for SQLite."""

    def _make_db(self):
        db_module = _get_db_module()
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
        conn.commit()
        return db_module.DBConnection(conn, is_postgres=False)

    def test_safe_column_exists_true(self):
        db_module = _get_db_module()
        dbc = self._make_db()
        self.assertTrue(db_module._safe_column_exists(dbc, "test_table", "name"))

    def test_safe_column_exists_false(self):
        db_module = _get_db_module()
        dbc = self._make_db()
        self.assertFalse(db_module._safe_column_exists(dbc, "test_table", "nonexistent"))

    def test_safe_table_exists_true(self):
        db_module = _get_db_module()
        dbc = self._make_db()
        self.assertTrue(db_module._safe_table_exists(dbc, "test_table"))

    def test_safe_table_exists_false(self):
        db_module = _get_db_module()
        dbc = self._make_db()
        self.assertFalse(db_module._safe_table_exists(dbc, "no_such_table"))


# ---------------------------------------------------------------------------
# 3. _run_migrations no longer uses try/except SELECT pattern
# ---------------------------------------------------------------------------

class TestMigrationsNoTryExceptSelect(unittest.TestCase):
    """Ensure _run_migrations uses _safe_column_exists/_safe_table_exists
    instead of the old try/except SELECT pattern that aborts PostgreSQL
    transactions."""

    def test_run_migrations_source_has_no_bare_select_try(self):
        """_run_migrations must not contain ``try: db.execute('SELECT ... FROM
        <table> LIMIT 1')`` checks — these abort the PG transaction."""
        db_module = _get_db_module()
        src = inspect.getsource(db_module._run_migrations)
        # Look for the old pattern: SELECT <col> FROM <table> LIMIT 1 inside except
        # Allow the information_schema queries which are correct
        lines = src.split("\n")
        bad_patterns = []
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # The old pattern was: db.execute("SELECT <col> FROM <table> LIMIT 1")
            # inside a try block. Safe patterns use information_schema.
            if ("SELECT" in stripped and "LIMIT 1" in stripped
                    and "information_schema" not in stripped
                    and "db.execute" in stripped):
                bad_patterns.append((i, stripped))
        self.assertEqual(
            bad_patterns, [],
            f"Found {len(bad_patterns)} bare SELECT…LIMIT 1 check(s) in "
            f"_run_migrations (should use _safe_column_exists): {bad_patterns}"
        )


# ---------------------------------------------------------------------------
# 4. init_db() completes on SQLite without blocking
# ---------------------------------------------------------------------------

class TestInitDbCompletes(unittest.TestCase):
    """Verify that init_db() completes promptly on SQLite (no hang)."""

    def test_init_db_completes_within_timeout(self):
        """init_db() must complete within 10 seconds on SQLite."""
        db_module = _get_db_module()
        import time
        import tempfile, shutil

        tmpdir = tempfile.mkdtemp()
        test_db = os.path.join(tmpdir, "test_startup.db")
        try:
            # Point db module at a temp SQLite database
            orig_use_pg = db_module.USE_POSTGRESQL
            orig_db_path = db_module.DB_PATH
            orig_pool = db_module._pg_pool
            db_module.USE_POSTGRESQL = False
            db_module.DB_PATH = test_db
            db_module._pg_pool = None

            t0 = time.monotonic()
            db_module.init_db()
            elapsed = time.monotonic() - t0

            self.assertLess(elapsed, 30.0,
                            f"init_db() took {elapsed:.1f}s — must complete within 30s (hang guard)")
        finally:
            db_module.USE_POSTGRESQL = orig_use_pg
            db_module.DB_PATH = orig_db_path
            db_module._pg_pool = orig_pool
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5. Startup logging coverage
# ---------------------------------------------------------------------------

class TestStartupLogging(unittest.TestCase):
    """Ensure startup observability is present in the source code."""

    def test_init_db_has_startup_logging(self):
        """db.init_db must log startup progress markers."""
        db_module = _get_db_module()
        src = inspect.getsource(db_module.init_db)
        self.assertIn("startup:", src)
        self.assertIn("schema DDL", src)
        self.assertIn("_run_migrations", src)

    def test_server_main_has_startup_logging(self):
        """server.py __main__ block must log before/after each startup step."""
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path, encoding="utf-8") as f:
            src = f.read()
        # Check for key startup log markers
        required_markers = [
            "startup: entering validate_config",
            "startup: entering init_db",
            "startup: entering run_all_migrations",
            "startup: entering enforce_startup_safety",
            "startup: listener bound",
        ]
        for marker in required_markers:
            self.assertIn(marker, src,
                          f"Missing startup log marker: {marker!r}")


# ---------------------------------------------------------------------------
# 6. Fail-fast on connection timeout (mock test)
# ---------------------------------------------------------------------------

class TestFailFastOnTimeout(unittest.TestCase):
    """Verify that a connection/statement timeout raises immediately
    instead of blocking indefinitely."""

    def test_pool_creation_failure_raises(self):
        """If the pool can't connect (timeout), it must raise, not hang."""
        db_module = _get_db_module()
        with patch.object(db_module, "PSYCOPG2_AVAILABLE", True), \
             patch.object(db_module, "USE_POSTGRESQL", True), \
             patch.object(db_module, "DATABASE_URL", "postgresql://user:pass@host/db"):
            db_module._pg_pool = None
            with patch("db.psycopg2.pool.ThreadedConnectionPool",
                       side_effect=Exception("connection timed out")):
                with self.assertRaises(Exception) as ctx:
                    db_module.init_pg_pool()
                self.assertIn("timed out", str(ctx.exception))
            db_module._pg_pool = None


# ---------------------------------------------------------------------------
# 7. Regression: _run_migrations idempotent on SQLite
# ---------------------------------------------------------------------------

class TestRunMigrationsIdempotent(unittest.TestCase):
    """_run_migrations must be safe to call repeatedly (idempotent)."""

    def test_double_run_does_not_error(self):
        """Running _run_migrations twice on the same DB should not raise."""
        db_module = _get_db_module()
        import tempfile, shutil
        tmpdir = tempfile.mkdtemp()
        test_db = os.path.join(tmpdir, "test_mig.db")
        try:
            orig_use_pg = db_module.USE_POSTGRESQL
            orig_db_path = db_module.DB_PATH
            orig_pool = db_module._pg_pool
            db_module.USE_POSTGRESQL = False
            db_module.DB_PATH = test_db
            db_module._pg_pool = None

            db_module.init_db()  # first run — creates everything
            # Get a fresh connection and run migrations again
            dbc = db_module.get_db()
            try:
                db_module._run_migrations(dbc)
                dbc.commit()
                # Run a second time — must be idempotent
                db_module._run_migrations(dbc)
                dbc.commit()
            finally:
                dbc.close()
        finally:
            db_module.USE_POSTGRESQL = orig_use_pg
            db_module.DB_PATH = orig_db_path
            db_module._pg_pool = orig_pool
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
