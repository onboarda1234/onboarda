"""
Phase A4 — dialect-safe screening_reports_normalized parity tests
==================================================================
Three tests that lock the Phase A4 fix in place.

Test 1  (always runs — SQLite end-to-end)
         Bring up a fresh SQLite database via init_db(), assert that the
         screening_reports_normalized table exists, and assert that the
         column set matches the canonical list.

Test 2  (PG — skipped when TEST_POSTGRES_DSN / DATABASE_URL_TEST is absent)
         Bring up a fresh PostgreSQL database via init_db(), assert table
         exists, assert column set matches the same canonical list.

Test 3  (always runs — static schema parity)
         Parse the PG and SQLite ``CREATE TABLE … screening_reports_normalized``
         strings directly from ``_get_postgres_schema()`` / ``_get_sqlite_schema()``
         without connecting to any database.  Asserts identical column set and
         identical declared order.  Mirrors the Test 3 pattern from Phase A2
         (test_fresh_install_pg_chain.py).
"""

from __future__ import annotations

import importlib
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Canonical column list (derived from screening_storage._CREATE_TABLE_SQL
# and verified against every SCR-010/011/013 read/write site)
# ---------------------------------------------------------------------------

CANONICAL_COLUMNS = [
    "id",
    "client_id",
    "application_id",
    "provider",
    "normalized_version",
    "source_screening_report_hash",
    "normalized_report_json",
    "normalization_status",
    "normalization_error",
    "is_authoritative",
    "source",
    "created_at",
    "updated_at",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_screening_normalized_ddl(schema_sql: str) -> str:
    """Return the CREATE TABLE … screening_reports_normalized (…) block.
    Raises ``ValueError`` if not found."""
    m = re.search(
        r"CREATE TABLE IF NOT EXISTS screening_reports_normalized\s*\((.+?)\)\s*;",
        schema_sql,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        raise ValueError(
            "screening_reports_normalized CREATE TABLE not found in schema SQL"
        )
    return m.group(1)


def _column_names_ordered(ddl_body: str) -> list:
    """Extract column names in declaration order from the body of a
    CREATE TABLE (…) block.

    Lines that start with a SQL constraint keyword are skipped.  The first
    identifier on each remaining non-empty, non-comment line is the column
    name.
    """
    skip_prefixes = ("primary", "unique", "check", "foreign", "constraint")
    names: list = []
    for raw_line in ddl_body.splitlines():
        line = raw_line.strip().rstrip(",").strip()
        if not line or line.startswith("--"):
            continue
        low = line.lower()
        if any(low.startswith(kw) for kw in skip_prefixes):
            continue
        first_token = re.split(r"\s+", line)[0].strip('"').strip("`").strip("'")
        if first_token:
            names.append(first_token.lower())
    return names


def _try_get_pg_dsn() -> "str | None":
    """Return a DSN for a fresh PostgreSQL test database, or None."""
    try:
        import testing.postgresql  # type: ignore
        pg = testing.postgresql.Postgresql()
        dsn = pg.url()
        _try_get_pg_dsn._pg_server = pg  # type: ignore[attr-defined]
        return dsn
    except Exception:
        pass
    dsn = os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")
    if dsn:
        return dsn
    return None


# ---------------------------------------------------------------------------
# Test 1 — SQLite fresh-install end-to-end (always runs)
# ---------------------------------------------------------------------------

def test_sqlite_screening_reports_normalized_init(tmp_path, monkeypatch):
    """Test 1 — SQLite: init_db() creates screening_reports_normalized with
    the canonical column set."""
    db_file = str(tmp_path / "test_a4_sqlite.db")

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
        # Table must exist
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name='screening_reports_normalized'"
        ).fetchone()
        assert row is not None, (
            "screening_reports_normalized table not found in SQLite after init_db()"
        )

        # Column set must match canonical list
        cols = db.execute(
            "PRAGMA table_info(screening_reports_normalized)"
        ).fetchall()
        actual_cols = [c["name"].lower() for c in cols]
        assert actual_cols == CANONICAL_COLUMNS, (
            f"SQLite column set mismatch.\n"
            f"  Expected: {CANONICAL_COLUMNS}\n"
            f"  Got:      {actual_cols}"
        )
    finally:
        try:
            db.close()
        except Exception:
            pass
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
# Test 2 — PostgreSQL fresh-install end-to-end (skipped if PG unavailable)
# ---------------------------------------------------------------------------

def test_pg_screening_reports_normalized_init(tmp_path, monkeypatch):
    """Test 2 — PostgreSQL: init_db() creates screening_reports_normalized
    with the canonical column set.

    Skipped when no PostgreSQL service is available.
    """
    pg_dsn = _try_get_pg_dsn()
    if pg_dsn is None:
        pytest.skip(
            "No PostgreSQL service available. "
            "Set TEST_POSTGRES_DSN=<dsn> or DATABASE_URL_TEST=<dsn> to enable."
        )

    orig_db_url = os.environ.get("DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    monkeypatch.setenv("ENVIRONMENT", "development")

    try:
        import config as config_module
        importlib.reload(config_module)
        import db as db_module
        importlib.reload(db_module)

        db_module.init_db()

        db = db_module.get_db()
        try:
            # Table must exist
            row = db.execute(
                "SELECT to_regclass('public.screening_reports_normalized')"
            ).fetchone()
            regclass_val = list(row.values())[0] if row else None
            assert regclass_val is not None, (
                "screening_reports_normalized not found in PG after init_db()"
            )

            # Column set must match canonical list
            cols = db.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'screening_reports_normalized' "
                "ORDER BY ordinal_position"
            ).fetchall()
            actual_cols = [c["column_name"].lower() for c in cols]
            assert actual_cols == CANONICAL_COLUMNS, (
                f"PG column set mismatch.\n"
                f"  Expected: {CANONICAL_COLUMNS}\n"
                f"  Got:      {actual_cols}"
            )
        finally:
            try:
                db.close()
            except Exception:
                pass
    finally:
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
        pg_server = getattr(_try_get_pg_dsn, "_pg_server", None)
        if pg_server is not None:
            try:
                pg_server.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Test 3 — Static PG/SQLite parity check (always runs)
# ---------------------------------------------------------------------------

def test_screening_normalized_pg_sqlite_column_parity():
    """Test 3 — Static parity: PG and SQLite screening_reports_normalized
    have the same column set in the same declared order.

    Parses CREATE TABLE strings from ``_get_postgres_schema()`` and
    ``_get_sqlite_schema()`` without connecting to any database.  Future
    drift between the two branches will fail this test immediately.
    """
    import db as db_module

    pg_schema = db_module._get_postgres_schema()
    sqlite_schema = db_module._get_sqlite_schema()

    pg_ddl = _extract_screening_normalized_ddl(pg_schema)
    sqlite_ddl = _extract_screening_normalized_ddl(sqlite_schema)

    pg_cols = _column_names_ordered(pg_ddl)
    sqlite_cols = _column_names_ordered(sqlite_ddl)

    pg_set = set(pg_cols)
    sqlite_set = set(sqlite_cols)
    pg_only = pg_set - sqlite_set
    sqlite_only = sqlite_set - pg_set

    assert not pg_only and not sqlite_only, (
        "screening_reports_normalized column parity failure between PG and SQLite:\n"
        f"  Columns in PG only:     {sorted(pg_only)}\n"
        f"  Columns in SQLite only: {sorted(sqlite_only)}\n"
        "Fix: bring both CREATE TABLE statements into sync in db.py."
    )

    assert pg_cols == sqlite_cols, (
        "screening_reports_normalized column ORDER differs between PG and SQLite:\n"
        f"  PG order:     {pg_cols}\n"
        f"  SQLite order: {sqlite_cols}\n"
        "Fix: align column declaration order in both branches in db.py."
    )
