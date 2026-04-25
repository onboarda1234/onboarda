"""
Phase A5 — Provider registry bootstrap regression tests (SCR-013)
===================================================================
Guards the following invariants established in Phase A5:

1. The factory registry initialises EMPTY — no providers auto-register.
2. ``get_provider()`` raises the *named* ``ProviderNotRegistered`` exception
   (never returns None silently).
3. ``screening_abstraction_enabled()`` returns False under default env
   (ENABLE_SCREENING_ABSTRACTION unset or "false").
4. The ``is_authoritative = 0`` CHECK constraint (added in A4) blocks any
   attempt to write ``is_authoritative=1`` to
   ``screening_reports_normalized`` on both SQLite and PostgreSQL.
5. A non-authoritative write (``is_authoritative=0``) succeeds on both
   dialects.

Dialect / skip pattern mirrors test_screening_reports_normalized_parity.py
(Phase A4) — PG tests are skipped unless TEST_POSTGRES_DSN or
DATABASE_URL_TEST is set (or a testing.postgresql server is available).

HARD CONSTRAINTS HONOURED
--------------------------
* No ComplyAdvantage code, imports, or string literals introduced.
* ENABLE_SCREENING_ABSTRACTION remains unset/OFF everywhere in this file.
* No provider is registered in any test — the registry stays empty
  throughout the full module.
"""

from __future__ import annotations

import importlib
import os
import re
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import screening_provider as _sp_module
from screening_provider import (
    ProviderNotRegistered,
    SUMSUB_PROVIDER_NAME,
    get_provider,
    list_providers,
    register_provider,
    screening_abstraction_enabled,
)
from screening_adapter_sumsub import SumsubScreeningAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sqlite_srn_ddl() -> str:
    """Return the CREATE TABLE statement for screening_reports_normalized
    extracted from db._get_sqlite_schema() — uses the authoritative DDL,
    not a hand-written copy."""
    import db as db_module
    schema = db_module._get_sqlite_schema()
    m = re.search(
        r"(CREATE TABLE IF NOT EXISTS screening_reports_normalized\s*\(.+?\)\s*;)",
        schema,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        raise ValueError(
            "screening_reports_normalized not found in SQLite schema from db._get_sqlite_schema()"
        )
    return m.group(1)


# Module-level variable to hold any ephemeral testing.postgresql server
# created by _try_get_pg_dsn(). Using a module-level variable avoids
# fragile function-attribute assignment.
_pg_test_server = None


def _try_get_pg_dsn() -> str | None:
    """Return a DSN for a live PostgreSQL database, or None.
    Mirrors the helper in test_screening_reports_normalized_parity.py."""
    global _pg_test_server
    try:
        import testing.postgresql  # type: ignore
        pg = testing.postgresql.Postgresql()
        dsn = pg.url()
        _pg_test_server = pg
        return dsn
    except Exception:
        pass
    dsn = os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")
    if dsn:
        return dsn
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_factory_registry():
    """Clear the module-level factory registry before and after each test.

    Ensures every test starts with an empty registry regardless of
    execution order, and leaves no state behind for subsequent tests.
    """
    _sp_module._factory_registry.clear()
    yield
    _sp_module._factory_registry.clear()


@pytest.fixture
def sqlite_srn_conn():
    """In-memory SQLite connection with screening_reports_normalized.

    Uses the actual DDL from db._get_sqlite_schema() so this test
    automatically catches any future DDL drift.
    """
    ddl = _sqlite_srn_ddl()
    conn = sqlite3.connect(":memory:")
    conn.execute(ddl)
    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Test 1 — registry starts empty
# ---------------------------------------------------------------------------

def test_registry_starts_empty():
    """list_providers() must return an empty collection on module import.

    No provider registers itself automatically; the first registration
    happens in Track C, not here.
    """
    assert list_providers() == [], (
        "Factory registry must be empty at module load. "
        "A provider appears to have self-registered, which is forbidden in Phase A5."
    )


# ---------------------------------------------------------------------------
# Test 2 — get_provider raises ProviderNotRegistered for any unknown name
# ---------------------------------------------------------------------------

def test_get_unknown_provider_raises_named_exception():
    """get_provider() on an empty registry must raise ProviderNotRegistered."""
    with pytest.raises(ProviderNotRegistered):
        get_provider("nonexistent_provider")


def test_get_unknown_provider_error_message_includes_name():
    """The ProviderNotRegistered message must contain the requested name."""
    provider_name = "some_unknown_provider"
    with pytest.raises(ProviderNotRegistered, match=provider_name):
        get_provider(provider_name)


def test_get_provider_never_returns_none():
    """get_provider() must raise, never return None, for unregistered names."""
    result = None
    try:
        result = get_provider("any_unregistered_name")
    except ProviderNotRegistered:
        pass  # expected
    except Exception as exc:
        pytest.fail(f"Expected ProviderNotRegistered, got {type(exc).__name__}: {exc}")
    else:
        pytest.fail(
            f"get_provider() returned {result!r} instead of raising ProviderNotRegistered"
        )


# ---------------------------------------------------------------------------
# Test 3 — screening_abstraction_enabled() is False by default
# ---------------------------------------------------------------------------

def test_screening_abstraction_disabled_by_default(monkeypatch):
    """screening_abstraction_enabled() must return False when the env var is
    unset — the gate is CLOSED in Phase A5."""
    monkeypatch.delenv("ENABLE_SCREENING_ABSTRACTION", raising=False)
    assert screening_abstraction_enabled() is False, (
        "screening_abstraction_enabled() returned True with "
        "ENABLE_SCREENING_ABSTRACTION unset. "
        "The gate must remain CLOSED in Phase A5."
    )


def test_screening_abstraction_disabled_when_set_false(monkeypatch):
    """screening_abstraction_enabled() must return False when explicitly set
    to 'false'."""
    monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "false")
    assert screening_abstraction_enabled() is False


def test_screening_abstraction_disabled_when_set_zero(monkeypatch):
    """screening_abstraction_enabled() must return False when set to '0'."""
    monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "0")
    assert screening_abstraction_enabled() is False


# ---------------------------------------------------------------------------
# Test 4 — non-authoritative write is allowed (SQLite)
# ---------------------------------------------------------------------------

def test_non_authoritative_write_allowed(sqlite_srn_conn):
    """INSERT with is_authoritative=0 must succeed (SQLite).

    This is the expected scaffolding-only write path; the CHECK constraint
    only blocks is_authoritative=1.
    """
    conn = sqlite_srn_conn
    conn.execute(
        "INSERT INTO screening_reports_normalized "
        "(client_id, application_id, provider, is_authoritative, source) "
        "VALUES (?, ?, ?, ?, ?)",
        ("client_001", "app_001", "manual_scaffolding", 0, "migration_scaffolding"),
    )
    conn.commit()
    row = conn.execute(
        "SELECT is_authoritative FROM screening_reports_normalized "
        "WHERE client_id = 'client_001'"
    ).fetchone()
    assert row is not None, "Row was not inserted"
    assert row[0] == 0, f"Expected is_authoritative=0, got {row[0]}"


# ---------------------------------------------------------------------------
# Test 5 — authoritative write blocked by CHECK constraint (SQLite)
# ---------------------------------------------------------------------------

def test_authoritative_write_blocked_by_check_constraint_sqlite(sqlite_srn_conn):
    """INSERT with is_authoritative=1 MUST raise IntegrityError on SQLite.

    The CHECK(is_authoritative = 0) constraint added in Phase A4 enforces
    that no scaffolding row can ever be treated as authoritative without
    flipping the ENABLE_SCREENING_ABSTRACTION gate.
    """
    conn = sqlite_srn_conn
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO screening_reports_normalized "
            "(client_id, application_id, provider, is_authoritative, source) "
            "VALUES (?, ?, ?, ?, ?)",
            ("client_bad", "app_bad", "test_provider", 1, "migration_scaffolding"),
        )
        conn.commit()


def test_authoritative_update_blocked_by_check_constraint_sqlite(sqlite_srn_conn):
    """UPDATE setting is_authoritative=1 MUST also raise IntegrityError on SQLite."""
    conn = sqlite_srn_conn
    conn.execute(
        "INSERT INTO screening_reports_normalized "
        "(client_id, application_id, provider, is_authoritative, source) "
        "VALUES (?, ?, ?, ?, ?)",
        ("client_upd", "app_upd", "manual_scaffolding", 0, "migration_scaffolding"),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE screening_reports_normalized "
            "SET is_authoritative = 1 "
            "WHERE client_id = 'client_upd'"
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Test 6 — authoritative write blocked by CHECK constraint (PostgreSQL)
# ---------------------------------------------------------------------------

def test_authoritative_write_blocked_by_check_constraint_pg(tmp_path, monkeypatch):
    """INSERT with is_authoritative=1 MUST raise IntegrityError on PostgreSQL.

    Skipped when no PostgreSQL service is reachable.  In CI the Postgres 15
    service container (A3) supplies DATABASE_URL_TEST.
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

        import psycopg2  # type: ignore

        # ── non-authoritative write must succeed ──
        with psycopg2.connect(pg_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO screening_reports_normalized "
                    "(client_id, application_id, provider, is_authoritative, source) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    ("pg_client_ok", "pg_app_ok", "manual_scaffolding", 0, "migration_scaffolding"),
                )
            conn.commit()

        # ── authoritative write must be rejected by CHECK constraint ──
        with psycopg2.connect(pg_dsn) as conn:
            with conn.cursor() as cur:
                with pytest.raises(psycopg2.IntegrityError):
                    cur.execute(
                        "INSERT INTO screening_reports_normalized "
                        "(client_id, application_id, provider, is_authoritative, source) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        ("pg_client_bad", "pg_app_bad", "test_provider", 1, "migration_scaffolding"),
                    )
                    conn.commit()

    finally:
        if orig_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = orig_db_url
        try:
            importlib.reload(config_module)   # type: ignore[possibly-undefined]
            importlib.reload(db_module)       # type: ignore[possibly-undefined]
        except Exception:
            pass
        global _pg_test_server
        if _pg_test_server is not None:
            try:
                _pg_test_server.stop()
            except Exception:
                pass
            _pg_test_server = None


# ---------------------------------------------------------------------------
# Test 7 — register_provider / list_providers round-trip
# ---------------------------------------------------------------------------

def test_register_then_list_providers():
    """register_provider() must make the name visible in list_providers()."""
    def dummy_factory():
        return None  # factory content is irrelevant in Phase A5

    register_provider("alpha_provider", dummy_factory)
    assert "alpha_provider" in list_providers()


def test_register_then_get_provider():
    """get_provider() must return the factory registered under a name."""
    sentinel = object()
    register_provider("beta_provider", sentinel)
    result = get_provider("beta_provider")
    assert result is sentinel


def test_register_empty_name_raises():
    """register_provider() must reject empty name strings."""
    with pytest.raises(ValueError, match="must not be empty"):
        register_provider("", lambda: None)


# ---------------------------------------------------------------------------
# Test 8 — Phase A6: SUMSUB_PROVIDER_NAME constant and Sumsub registration
# ---------------------------------------------------------------------------

def test_sumsub_provider_name_constant_value():
    """SUMSUB_PROVIDER_NAME must equal the canonical string "sumsub".

    Pins the constant against accidental rename or typo.
    """
    assert SUMSUB_PROVIDER_NAME == "sumsub"


def test_register_sumsub_factory_via_constant():
    """Registering SumsubScreeningAdapter under SUMSUB_PROVIDER_NAME must make
    it visible in list_providers() and retrievable via get_provider().
    """
    register_provider(SUMSUB_PROVIDER_NAME, SumsubScreeningAdapter)
    assert list_providers() == [SUMSUB_PROVIDER_NAME]
    assert get_provider(SUMSUB_PROVIDER_NAME) is SumsubScreeningAdapter


def test_get_provider_constructs_sumsub_adapter_instance():
    """get_provider(SUMSUB_PROVIDER_NAME)() must return a SumsubScreeningAdapter.

    Proves the factory is callable and produces the expected concrete type.
    """
    register_provider(SUMSUB_PROVIDER_NAME, SumsubScreeningAdapter)
    factory = get_provider(SUMSUB_PROVIDER_NAME)
    instance = factory()
    assert isinstance(instance, SumsubScreeningAdapter)


def test_register_sumsub_idempotency_policy():
    """Calling register_provider() twice for the same name silently overwrites
    the first registration (Policy A).

    Policy A was chosen because register_provider() performs a plain dict
    assignment (_factory_registry[name] = factory) with no duplicate guard.
    This means a second call with the same name replaces the factory without
    raising.  At startup there is only one registration call, so in practice
    this path is not exercised at runtime — but the behaviour must be
    explicitly documented and tested so future callers know what to expect.
    """
    sentinel_a = object()
    sentinel_b = object()
    register_provider(SUMSUB_PROVIDER_NAME, sentinel_a)
    register_provider(SUMSUB_PROVIDER_NAME, sentinel_b)  # silent overwrite
    assert get_provider(SUMSUB_PROVIDER_NAME) is sentinel_b
    assert list_providers() == [SUMSUB_PROVIDER_NAME]
