"""
Phase A2 — Fresh-install PostgreSQL migration chain tests
=========================================================
Three tests that lock the Phase A2 fix in place.

Test 1  (PG — skipped when no PostgreSQL service is available in the
         sandbox, with a TODO referencing Phase A3)
         Bring up a fresh PostgreSQL 15 database, run ``init_db()`` then
         the file-based migration runner, and assert that all 13
         migrations are applied with zero failures.

Test 2  (always runs — SQLite)
         Same end-to-end flow on a fresh SQLite database.
         Regression coverage: the Phase A2 fix must not break SQLite.

Test 3  (always runs — static schema parity)
         Parse the PG and SQLite ``CREATE TABLE … periodic_reviews``
         strings from ``_get_postgres_schema()`` / ``_get_sqlite_schema()``
         and assert that both branches declare the same set of column
         names.  This catches future drift at unit-test level even when
         no PostgreSQL service container is available.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOTAL_MIGRATIONS = 19


def _extract_periodic_reviews_ddl(schema_sql: str) -> str:
    """Return the CREATE TABLE … periodic_reviews (…) block from a schema
    string.  Raises ``ValueError`` if not found."""
    # Match from CREATE TABLE IF NOT EXISTS periodic_reviews ( to the matching )
    m = re.search(
        r"CREATE TABLE IF NOT EXISTS periodic_reviews\s*\((.+?)\)\s*;",
        schema_sql,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        raise ValueError("periodic_reviews CREATE TABLE not found in schema SQL")
    return m.group(1)


def _column_names_from_ddl(ddl_body: str) -> set:
    """Extract column names from the body of a CREATE TABLE (…) block.

    Each non-empty, non-comment line that starts with a word character is
    treated as a column or constraint declaration.  Lines starting with a
    SQL constraint keyword (PRIMARY, UNIQUE, CHECK, FOREIGN, CONSTRAINT)
    are skipped.  The first identifier on each remaining line is the
    column name.
    """
    skip_prefixes = ("primary", "unique", "check", "foreign", "constraint")
    names: set = set()
    for raw_line in ddl_body.splitlines():
        line = raw_line.strip().rstrip(",").strip()
        if not line or line.startswith("--"):
            continue
        low = line.lower()
        if any(low.startswith(kw) for kw in skip_prefixes):
            continue
        # First token is the column name (strip any leading/trailing quotes)
        first_token = re.split(r"\s+", line)[0].strip('"').strip("`").strip("'")
        if first_token:
            names.add(first_token.lower())
    return names


# ---------------------------------------------------------------------------
# Test 1 — PostgreSQL fresh-install end-to-end (skipped if PG unavailable)
# ---------------------------------------------------------------------------

def _try_get_pg_dsn() -> "str | None":
    """Return a DSN for a fresh PostgreSQL test database, or None."""
    # Attempt 1: testing.postgresql (lightweight ephemeral PG)
    try:
        import testing.postgresql  # type: ignore
        pg = testing.postgresql.Postgresql()
        dsn = pg.url()
        # Keep a reference so the server stays alive for the test.
        _try_get_pg_dsn._pg_server = pg  # type: ignore[attr-defined]
        return dsn
    except Exception:
        pass

    # Attempt 2: honour an explicit env var (useful in CI with a PG service)
    dsn = os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")
    if dsn:
        return dsn

    return None


def test_fresh_pg_init_and_migration_chain(tmp_path, monkeypatch, caplog):
    """Test 1 — PostgreSQL: init_db + full migration chain applies cleanly.

    Skipped when no PostgreSQL service is available in the agent sandbox.
    TODO (Phase A3): add a Postgres service container to CI so this test
    always executes without manual setup.
    """
    pg_dsn = _try_get_pg_dsn()
    if pg_dsn is None:
        pytest.skip(
            "No PostgreSQL service available in this sandbox. "
            "To reproduce manually: \n"
            "  1. Create a fresh PG 15 database.\n"
            "  2. Set TEST_POSTGRES_DSN=<dsn> in the environment.\n"
            "  3. Re-run: pytest tests/test_fresh_install_pg_chain.py::test_fresh_pg_init_and_migration_chain\n"
            "TODO (Phase A3): wire a postgres:15 service container to CI."
        )

    # Patch DATABASE_URL so db.py uses PG
    orig_db_url = os.environ.get("DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    monkeypatch.setenv("ENVIRONMENT", "development")

    try:
        import config as config_module
        importlib.reload(config_module)
        import db as db_module
        importlib.reload(db_module)

        # init_db must succeed on a fresh PG database
        db_module.init_db()

        db = db_module.get_db()
        try:
            from migrations.runner import run_all_migrations_with_connection

            with caplog.at_level(logging.INFO, logger="arie.migrations"):
                applied = run_all_migrations_with_connection(db)

            messages = [
                r.getMessage()
                for r in caplog.records
                if r.name == "arie.migrations"
            ]

            # Zero failures
            assert not any("failed" in m.lower() for m in messages), (
                f"Migration runner emitted a FAILED log on PG: {messages}"
            )
            # Fresh init_db pre-populates schema_version, so runner is a no-op.
            assert applied == 0, (
                f"Expected 0 migrations applied; got {applied}. "
                f"Messages: {messages}"
            )
            # Migration 003 specifically must be in the applied list
            rows = db.execute(
                "SELECT version FROM schema_version ORDER BY version"
            ).fetchall()
            applied_versions = [r["version"] for r in rows]
            assert "003" in applied_versions, (
                f"Migration 003 not in applied list: {applied_versions}"
            )
            # All known versions present
            expected_versions = [str(i).zfill(3) for i in range(1, _TOTAL_MIGRATIONS + 1)]
            assert applied_versions == expected_versions, (
                f"Applied versions mismatch: got {applied_versions}"
            )
        finally:
            db.close()
    finally:
        # Restore env
        for var, value in (("DATABASE_URL", orig_db_url),):
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
        try:
            importlib.reload(config_module)  # type: ignore[possibly-undefined]
            importlib.reload(db_module)  # type: ignore[possibly-undefined]
        except Exception:
            pass
        # Stop ephemeral PG server if we started one
        pg_server = getattr(_try_get_pg_dsn, "_pg_server", None)
        if pg_server is not None:
            try:
                pg_server.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Test 2 — SQLite fresh-install end-to-end (always runs)
# ---------------------------------------------------------------------------

def test_fresh_sqlite_init_and_migration_chain(tmp_path, monkeypatch, caplog):
    """Test 2 — SQLite: init_db + full migration chain applies cleanly.

    This is regression coverage: the Phase A2 fix (adding columns to the
    PG branch only) must not break the SQLite path.
    """
    db_file = str(tmp_path / "test_a2.db")

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
        from migrations.runner import run_all_migrations_with_connection

        with caplog.at_level(logging.INFO, logger="arie.migrations"):
            applied = run_all_migrations_with_connection(db)

        messages = [
            r.getMessage()
            for r in caplog.records
            if r.name == "arie.migrations"
        ]

        # Zero failures
        assert not any("failed" in m.lower() for m in messages), (
            f"Migration runner emitted a FAILED log on SQLite: {messages}"
        )
        # Fresh init_db pre-populates schema_version, so runner is a no-op.
        assert applied == 0, (
            f"Expected 0 migrations applied on SQLite; got {applied}. "
            f"Messages: {messages}"
        )
        # Migration 003 must be in the applied list
        rows = db.execute(
            "SELECT version FROM schema_version ORDER BY version"
        ).fetchall()
        applied_versions = [r["version"] for r in rows]
        assert "003" in applied_versions, (
            f"Migration 003 not in applied list on SQLite: {applied_versions}"
        )
        # All known versions present
        expected_versions = [str(i).zfill(3) for i in range(1, _TOTAL_MIGRATIONS + 1)]
        assert applied_versions == expected_versions, (
            f"Applied versions mismatch on SQLite: got {applied_versions}"
        )
    finally:
        try:
            db.close()
        except Exception:
            pass
        # Restore env
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


# ---------------------------------------------------------------------------
# Test 3 — Static PG/SQLite parity check for periodic_reviews (always runs)
# ---------------------------------------------------------------------------

def test_periodic_reviews_pg_sqlite_column_parity():
    """Test 3 — Static parity: PG and SQLite periodic_reviews have the same
    column set.

    Parses the CREATE TABLE strings from ``_get_postgres_schema()`` and
    ``_get_sqlite_schema()`` without connecting to any database.  Any future
    drift between the two branches will cause this test to fail immediately,
    without needing a live PG service.
    """
    import db as db_module

    pg_schema = db_module._get_postgres_schema()
    sqlite_schema = db_module._get_sqlite_schema()

    pg_ddl = _extract_periodic_reviews_ddl(pg_schema)
    sqlite_ddl = _extract_periodic_reviews_ddl(sqlite_schema)

    pg_cols = _column_names_from_ddl(pg_ddl)
    sqlite_cols = _column_names_from_ddl(sqlite_ddl)

    pg_only = pg_cols - sqlite_cols
    sqlite_only = sqlite_cols - pg_cols

    assert not pg_only and not sqlite_only, (
        "periodic_reviews column parity failure between PG and SQLite branches:\n"
        f"  Columns in PG only:     {sorted(pg_only)}\n"
        f"  Columns in SQLite only: {sorted(sqlite_only)}\n"
        "Fix: bring both CREATE TABLE statements into sync in db.py."
    )
