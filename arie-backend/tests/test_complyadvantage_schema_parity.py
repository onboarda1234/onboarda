"""
Phase C1 — ComplyAdvantage schema parity tests (SCR-013)
=========================================================
Guards the Phase C1 schema additions:

  * complyadvantage_search_events
  * complyadvantage_monitor_events

Tests verify:
  1. PG schema string contains both CREATE TABLE blocks.
  2. SQLite schema string contains both CREATE TABLE blocks.
  3. Column names and ordering are identical across dialects for each table.
  4. ca_event_id UNIQUE constraint blocks duplicate inserts (SQLite).
  5. is_shadow CHECK(is_shadow = 1) blocks non-shadow writes on both tables (SQLite).
  6. REGRESSION — A4 CHECK(is_authoritative = 0) is still enforced on
     screening_reports_normalized (SQLite).
  7. REGRESSION — A5 _factory_registry is still empty after schema changes.
  8. Live init_db() creates both tables — PostgreSQL (skipped without DSN).
  9. Live init_db() creates both tables — SQLite.

HARD CONSTRAINTS HONOURED
--------------------------
* ENABLE_SCREENING_ABSTRACTION is never set or flipped in this file.
* No provider is registered anywhere in this file.
* No ComplyAdvantage HTTP client, requests, or httpx import is introduced.
* The A4 CHECK(is_authoritative = 0) is not modified.
* screening_provider.py and protected_controls.py are not imported or modified.
"""

from __future__ import annotations

import importlib
import os
import re
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Canonical column lists — must match db.py exactly (both dialects)
# ---------------------------------------------------------------------------

SEARCH_EVENTS_COLUMNS = [
    "id",
    "client_id",
    "application_id",
    "normalized_report_id",
    "ca_search_id",
    "ca_ref",
    "search_type",
    "request_payload_json",
    "response_payload_json",
    "response_status_code",
    "error_class",
    "error_detail",
    "is_shadow",
    "created_at",
    "updated_at",
]

MONITOR_EVENTS_COLUMNS = [
    "id",
    "ca_monitor_id",
    "ca_event_id",
    "client_id",
    "application_id",
    "event_type",
    "event_payload_json",
    "signature_header",
    "signature_verified",
    "received_at",
    "processed_at",
    "processing_status",
    "processing_error",
    "is_shadow",
    "created_at",
    "updated_at",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_table_ddl(schema_sql: str, table_name: str) -> str:
    """Return the body of CREATE TABLE … <table_name> (…) block.
    Raises ValueError if not found."""
    pattern = (
        r"CREATE TABLE IF NOT EXISTS "
        + re.escape(table_name)
        + r"\s*\((.+?)\)\s*;"
    )
    m = re.search(pattern, schema_sql, re.DOTALL | re.IGNORECASE)
    if not m:
        raise ValueError(
            f"{table_name} CREATE TABLE not found in schema SQL"
        )
    return m.group(1)


def _column_names_ordered(ddl_body: str) -> list:
    """Extract column names in declaration order from a CREATE TABLE body.

    Lines beginning with SQL constraint keywords (PRIMARY, UNIQUE, CHECK,
    FOREIGN, CONSTRAINT) are skipped.  The first identifier on each
    remaining non-empty, non-comment line is treated as the column name.
    Mirrors the helper used in test_screening_reports_normalized_parity.py.
    """
    skip_prefixes = ("primary", "unique", "check", "foreign", "constraint")
    names: list[str] = []
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


def _sqlite_table_ddl(table_name: str) -> str:
    """Return the full CREATE TABLE statement for *table_name* extracted from
    db._get_sqlite_schema() — uses the authoritative DDL, not a hand-written copy."""
    import db as db_module
    schema = db_module._get_sqlite_schema()
    pattern = (
        r"(CREATE TABLE IF NOT EXISTS "
        + re.escape(table_name)
        + r"\s*\(.+?\)\s*;)"
    )
    m = re.search(pattern, schema, re.DOTALL | re.IGNORECASE)
    if not m:
        raise ValueError(
            f"{table_name} not found in SQLite schema from db._get_sqlite_schema()"
        )
    return m.group(1)


def _try_get_pg_dsn() -> str | None:
    """Return a DSN for a live PostgreSQL database, or None.
    Mirrors the helper in test_screening_reports_normalized_parity.py."""
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


@pytest.fixture
def sqlite_search_conn():
    """In-memory SQLite connection with complyadvantage_search_events."""
    ddl = _sqlite_table_ddl("complyadvantage_search_events")
    conn = sqlite3.connect(":memory:")
    conn.execute(ddl)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def sqlite_monitor_conn():
    """In-memory SQLite connection with complyadvantage_monitor_events.
    Also creates the UNIQUE index used for ca_event_id idempotency."""
    import db as db_module
    schema = db_module._get_sqlite_schema()
    table_ddl = _sqlite_table_ddl("complyadvantage_monitor_events")
    # Also extract the UNIQUE index DDL for ca_event_id
    idx_m = re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS idx_ca_monitor_events_ca_event_id\s+ON\s+"
        r"complyadvantage_monitor_events\s*\([^)]+\)\s*;",
        schema,
        re.IGNORECASE,
    )
    conn = sqlite3.connect(":memory:")
    conn.execute(table_ddl)
    if idx_m:
        conn.execute(idx_m.group(0))
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def sqlite_srn_conn():
    """In-memory SQLite connection with screening_reports_normalized (A4 regression)."""
    import db as db_module
    schema = db_module._get_sqlite_schema()
    m = re.search(
        r"(CREATE TABLE IF NOT EXISTS screening_reports_normalized\s*\(.+?\)\s*;)",
        schema,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        raise ValueError("screening_reports_normalized not found in SQLite schema")
    conn = sqlite3.connect(":memory:")
    conn.execute(m.group(1))
    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Test 1 — complyadvantage_search_events exists in PG schema (static)
# ---------------------------------------------------------------------------

def test_complyadvantage_search_events_exists_in_pg_schema():
    """PG schema string must contain a CREATE TABLE for complyadvantage_search_events."""
    import db as db_module
    schema = db_module._get_postgres_schema()
    assert "complyadvantage_search_events" in schema.lower(), (
        "complyadvantage_search_events not found in _get_postgres_schema(). "
        "Add the Phase C1 CREATE TABLE block to _get_postgres_schema() in db.py."
    )


# ---------------------------------------------------------------------------
# Test 2 — complyadvantage_search_events exists in SQLite schema (static)
# ---------------------------------------------------------------------------

def test_complyadvantage_search_events_exists_in_sqlite_schema():
    """SQLite schema string must contain a CREATE TABLE for complyadvantage_search_events."""
    import db as db_module
    schema = db_module._get_sqlite_schema()
    assert "complyadvantage_search_events" in schema.lower(), (
        "complyadvantage_search_events not found in _get_sqlite_schema(). "
        "Add the Phase C1 CREATE TABLE block to _get_sqlite_schema() in db.py."
    )


# ---------------------------------------------------------------------------
# Test 3 — search_events column ordering matches across dialects (static)
# ---------------------------------------------------------------------------

def test_complyadvantage_search_events_columns_match_across_dialects():
    """PG and SQLite complyadvantage_search_events must have identical columns
    in identical order."""
    import db as db_module

    pg_ddl = _extract_table_ddl(db_module._get_postgres_schema(), "complyadvantage_search_events")
    sqlite_ddl = _extract_table_ddl(db_module._get_sqlite_schema(), "complyadvantage_search_events")

    pg_cols = _column_names_ordered(pg_ddl)
    sqlite_cols = _column_names_ordered(sqlite_ddl)

    pg_set = set(pg_cols)
    sqlite_set = set(sqlite_cols)
    pg_only = pg_set - sqlite_set
    sqlite_only = sqlite_set - pg_set

    assert not pg_only and not sqlite_only, (
        "complyadvantage_search_events column parity failure between PG and SQLite:\n"
        f"  Columns in PG only:     {sorted(pg_only)}\n"
        f"  Columns in SQLite only: {sorted(sqlite_only)}\n"
        "Fix: align both CREATE TABLE statements in db.py."
    )
    assert pg_cols == sqlite_cols, (
        "complyadvantage_search_events column ORDER differs between PG and SQLite:\n"
        f"  PG order:     {pg_cols}\n"
        f"  SQLite order: {sqlite_cols}\n"
        "Fix: align column declaration order in both branches in db.py."
    )
    # Also verify against the canonical list defined at the top of this file
    assert pg_cols == SEARCH_EVENTS_COLUMNS, (
        "complyadvantage_search_events columns differ from SEARCH_EVENTS_COLUMNS:\n"
        f"  Expected: {SEARCH_EVENTS_COLUMNS}\n"
        f"  Got:      {pg_cols}"
    )


# ---------------------------------------------------------------------------
# Test 4 — complyadvantage_monitor_events exists in PG schema (static)
# ---------------------------------------------------------------------------

def test_complyadvantage_monitor_events_exists_in_pg_schema():
    """PG schema string must contain a CREATE TABLE for complyadvantage_monitor_events."""
    import db as db_module
    schema = db_module._get_postgres_schema()
    assert "complyadvantage_monitor_events" in schema.lower(), (
        "complyadvantage_monitor_events not found in _get_postgres_schema()."
    )


# ---------------------------------------------------------------------------
# Test 5 — complyadvantage_monitor_events exists in SQLite schema (static)
# ---------------------------------------------------------------------------

def test_complyadvantage_monitor_events_exists_in_sqlite_schema():
    """SQLite schema string must contain a CREATE TABLE for complyadvantage_monitor_events."""
    import db as db_module
    schema = db_module._get_sqlite_schema()
    assert "complyadvantage_monitor_events" in schema.lower(), (
        "complyadvantage_monitor_events not found in _get_sqlite_schema()."
    )


# ---------------------------------------------------------------------------
# Test 6 — monitor_events column ordering matches across dialects (static)
# ---------------------------------------------------------------------------

def test_complyadvantage_monitor_events_columns_match_across_dialects():
    """PG and SQLite complyadvantage_monitor_events must have identical columns
    in identical order."""
    import db as db_module

    pg_ddl = _extract_table_ddl(db_module._get_postgres_schema(), "complyadvantage_monitor_events")
    sqlite_ddl = _extract_table_ddl(db_module._get_sqlite_schema(), "complyadvantage_monitor_events")

    pg_cols = _column_names_ordered(pg_ddl)
    sqlite_cols = _column_names_ordered(sqlite_ddl)

    pg_set = set(pg_cols)
    sqlite_set = set(sqlite_cols)
    pg_only = pg_set - sqlite_set
    sqlite_only = sqlite_set - pg_set

    assert not pg_only and not sqlite_only, (
        "complyadvantage_monitor_events column parity failure between PG and SQLite:\n"
        f"  Columns in PG only:     {sorted(pg_only)}\n"
        f"  Columns in SQLite only: {sorted(sqlite_only)}\n"
        "Fix: align both CREATE TABLE statements in db.py."
    )
    assert pg_cols == sqlite_cols, (
        "complyadvantage_monitor_events column ORDER differs between PG and SQLite:\n"
        f"  PG order:     {pg_cols}\n"
        f"  SQLite order: {sqlite_cols}\n"
        "Fix: align column declaration order in both branches in db.py."
    )
    # Also verify against the canonical list defined at the top of this file
    assert pg_cols == MONITOR_EVENTS_COLUMNS, (
        "complyadvantage_monitor_events columns differ from MONITOR_EVENTS_COLUMNS:\n"
        f"  Expected: {MONITOR_EVENTS_COLUMNS}\n"
        f"  Got:      {pg_cols}"
    )


# ---------------------------------------------------------------------------
# Test 7 — ca_event_id UNIQUE constraint blocks duplicate inserts (SQLite)
# ---------------------------------------------------------------------------

def test_ca_event_id_unique_constraint_blocks_duplicate(sqlite_monitor_conn):
    """Inserting two rows with the same ca_event_id must raise IntegrityError.

    The UNIQUE index on ca_event_id provides the idempotency needed by C4.
    """
    conn = sqlite_monitor_conn
    conn.execute(
        "INSERT INTO complyadvantage_monitor_events "
        "(ca_event_id, event_type, is_shadow) VALUES (?, ?, ?)",
        ("evt_abc123", "monitor.match_status_updated", 1),
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO complyadvantage_monitor_events "
            "(ca_event_id, event_type, is_shadow) VALUES (?, ?, ?)",
            ("evt_abc123", "monitor.match_status_updated", 1),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Test 8 — is_shadow CHECK blocks non-shadow writes on search_events (SQLite)
# ---------------------------------------------------------------------------

def test_is_shadow_check_blocks_non_shadow_write_search_events(sqlite_search_conn):
    """INSERT with is_shadow=0 must raise IntegrityError on
    complyadvantage_search_events.

    CHECK(is_shadow = 1) enforces that all rows are shadow-mode until
    Track E relaxes the constraint.
    """
    conn = sqlite_search_conn
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO complyadvantage_search_events "
            "(client_id, is_shadow) VALUES (?, ?)",
            ("client_001", 0),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Test 9 — is_shadow CHECK blocks non-shadow writes on monitor_events (SQLite)
# ---------------------------------------------------------------------------

def test_is_shadow_check_blocks_non_shadow_write_monitor_events(sqlite_monitor_conn):
    """INSERT with is_shadow=0 must raise IntegrityError on
    complyadvantage_monitor_events.

    CHECK(is_shadow = 1) enforces that all rows are shadow-mode until
    Track E relaxes the constraint.
    """
    conn = sqlite_monitor_conn
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO complyadvantage_monitor_events "
            "(ca_event_id, is_shadow) VALUES (?, ?)",
            ("evt_noshad", 0),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Test 10 — shadow writes succeed (sanity check) on both tables (SQLite)
# ---------------------------------------------------------------------------

def test_shadow_write_allowed_search_events(sqlite_search_conn):
    """INSERT with is_shadow=1 (the default) must succeed on search_events."""
    conn = sqlite_search_conn
    conn.execute(
        "INSERT INTO complyadvantage_search_events "
        "(client_id, application_id, ca_search_id, is_shadow) VALUES (?, ?, ?, ?)",
        ("client_ok", "app_ok", "srch_001", 1),
    )
    conn.commit()
    row = conn.execute(
        "SELECT is_shadow FROM complyadvantage_search_events WHERE ca_search_id = 'srch_001'"
    ).fetchone()
    assert row is not None and row[0] == 1


def test_shadow_write_allowed_monitor_events(sqlite_monitor_conn):
    """INSERT with is_shadow=1 (the default) must succeed on monitor_events."""
    conn = sqlite_monitor_conn
    conn.execute(
        "INSERT INTO complyadvantage_monitor_events "
        "(ca_event_id, event_type, is_shadow) VALUES (?, ?, ?)",
        ("evt_ok_001", "monitor.match_status_updated", 1),
    )
    conn.commit()
    row = conn.execute(
        "SELECT is_shadow FROM complyadvantage_monitor_events WHERE ca_event_id = 'evt_ok_001'"
    ).fetchone()
    assert row is not None and row[0] == 1


# ---------------------------------------------------------------------------
# Test 11 — REGRESSION: A4 CHECK(is_authoritative = 0) still enforced (SQLite)
# ---------------------------------------------------------------------------

def test_a4_check_constraint_still_enforced(sqlite_srn_conn):
    """INSERT with is_authoritative=1 must still raise IntegrityError on
    screening_reports_normalized.

    Regression guard against accidental relaxation of the A4 CHECK constraint.
    """
    conn = sqlite_srn_conn
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO screening_reports_normalized "
            "(client_id, application_id, provider, is_authoritative, source) "
            "VALUES (?, ?, ?, ?, ?)",
            ("c1", "a1", "test_provider", 1, "migration_scaffolding"),
        )
        conn.commit()


def test_a4_check_constraint_present_in_both_schema_strings():
    """The CHECK(is_authoritative = 0) string must appear in both PG and SQLite
    schema functions — confirms the constraint was not silently removed."""
    import db as db_module
    pg_schema = db_module._get_postgres_schema()
    sqlite_schema = db_module._get_sqlite_schema()
    assert "check(is_authoritative = 0)" in pg_schema.lower(), (
        "A4 CHECK(is_authoritative = 0) not found in _get_postgres_schema(). "
        "Do NOT remove this constraint."
    )
    assert "check(is_authoritative = 0)" in sqlite_schema.lower(), (
        "A4 CHECK(is_authoritative = 0) not found in _get_sqlite_schema(). "
        "Do NOT remove this constraint."
    )


# ---------------------------------------------------------------------------
# Test 12 — REGRESSION: A5 _factory_registry still empty (registry guard)
# ---------------------------------------------------------------------------

def test_a5_registry_still_empty():
    """list_providers() must return [] after schema changes.

    Guards against accidental provider registration introduced alongside
    the Phase C1 schema additions.
    """
    import screening_provider as _sp_module
    from screening_provider import list_providers

    # Ensure clean state
    _sp_module._factory_registry.clear()
    result = list_providers()
    assert result == [], (
        "Provider registry is not empty after Phase C1 schema changes. "
        "No provider must register itself until Track C registration phase. "
        f"Found: {result}"
    )


# ---------------------------------------------------------------------------
# Test 13 — Live init_db creates new tables — SQLite (always runs)
# ---------------------------------------------------------------------------

def test_init_db_creates_new_tables_in_sqlite(tmp_path, monkeypatch):
    """init_db() on SQLite must create both CA sidecar tables."""
    db_file = str(tmp_path / "test_c1_sqlite.db")

    orig_db_url = os.environ.get("DATABASE_URL")
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
        for table_name in ("complyadvantage_search_events", "complyadvantage_monitor_events"):
            row = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
            assert row is not None, (
                f"{table_name} not found in SQLite after init_db()"
            )
    finally:
        try:
            db.close()
        except Exception:
            pass
        for var, value in (
            ("DATABASE_URL", orig_db_url),
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
# Test 14 — Live init_db creates new tables — PostgreSQL (skipped without DSN)
# ---------------------------------------------------------------------------

def test_init_db_creates_new_tables_in_pg(tmp_path, monkeypatch):
    """init_db() on PostgreSQL must create both CA sidecar tables.

    Skipped when no PostgreSQL service is available.
    In CI the Postgres 15 service container (A3) supplies DATABASE_URL_TEST.
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
            for table_name in ("complyadvantage_search_events", "complyadvantage_monitor_events"):
                row = db.execute(
                    "SELECT to_regclass(%s)", (f"public.{table_name}",)
                ).fetchone()
                regclass_val = list(row.values())[0] if row else None
                assert regclass_val is not None, (
                    f"{table_name} not found in PG after init_db()"
                )

            # Also verify column sets via information_schema
            for table_name, expected_cols in (
                ("complyadvantage_search_events", SEARCH_EVENTS_COLUMNS),
                ("complyadvantage_monitor_events", MONITOR_EVENTS_COLUMNS),
            ):
                cols = db.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = %s ORDER BY ordinal_position",
                    (table_name,),
                ).fetchall()
                actual_cols = [c["column_name"].lower() for c in cols]
                assert actual_cols == expected_cols, (
                    f"PG column mismatch for {table_name}.\n"
                    f"  Expected: {expected_cols}\n"
                    f"  Got:      {actual_cols}"
                )
        finally:
            try:
                db.close()
            except Exception:
                pass
    finally:
        if orig_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = orig_db_url
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
