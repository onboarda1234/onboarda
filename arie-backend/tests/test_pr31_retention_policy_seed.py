"""PR-31 — GDPR retention-policy seed must reach already-seeded databases.

Root cause (staging's empty data_retention_policies table): the retention seed
lived at the BOTTOM of seed_initial_data(), after the "Database already seeded"
early return. Any database whose core tables (users / ai_agents / ai_checks /
risk_config) were populated before the block was added — i.e. staging — exited
early on every boot, so the retention table stayed empty forever.

The fix extracts the seed into db._ensure_retention_policies() and calls it on
BOTH paths. These tests cover: the staging-mimic regression (the actual bug),
fresh-install population, idempotency, operator-change preservation, the B1
session_tokens auto_purge invariant, and the non-gating readiness count probe.
PostgreSQL variants run when TEST_POSTGRES_DSN / DATABASE_URL_TEST is set and
create their own throwaway database (they must not depend on shared DB state).
"""

from __future__ import annotations

import importlib
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _policy_count(db):
    row = db.execute("SELECT COUNT(*) AS c FROM data_retention_policies").fetchone()
    return int(dict(row).get("c") or 0) if row else 0


def _expected_count():
    import db as db_module
    return len(db_module._DEFAULT_RETENTION_POLICIES)


# ---------------------------------------------------------------------------
# SQLite (always runs)
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_sqlite(tmp_path, monkeypatch):
    """A fresh SQLite database with init_db applied, module-level reloads."""
    db_file = str(tmp_path / "pr31_seed.db")
    orig = {var: os.environ.get(var) for var in ("DATABASE_URL", "ENVIRONMENT", "DB_PATH")}
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DB_PATH", db_file)

    import config as config_module
    importlib.reload(config_module)
    import db as db_module
    importlib.reload(db_module)
    db_module.DB_PATH = db_file
    db_module.init_db()
    yield db_module
    for var, value in orig.items():
        if value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = value
    try:
        importlib.reload(config_module)
        importlib.reload(db_module)
    except Exception:
        pass


def test_fresh_sqlite_seed_populates_retention_policies(fresh_sqlite):
    db = fresh_sqlite.get_db()
    try:
        fresh_sqlite.seed_initial_data(db)
        db.commit()
        assert _policy_count(db) == _expected_count()
        # B1 invariant: session_tokens must never seed with auto_purge on.
        row = db.execute(
            "SELECT auto_purge FROM data_retention_policies WHERE data_category = 'session_tokens'"
        ).fetchone()
        assert row is not None
        assert not row["auto_purge"], "session_tokens seeded with auto_purge enabled (B1 regression)"
    finally:
        db.close()


def test_staging_mimic_reseed_populates_empty_retention_table(fresh_sqlite):
    """THE regression test: core tables seeded, retention table empty, one boot
    must populate it via the early-return path."""
    db = fresh_sqlite.get_db()
    try:
        fresh_sqlite.seed_initial_data(db)
        db.commit()
        db.execute("DELETE FROM data_retention_policies")
        db.commit()
        assert _policy_count(db) == 0

        # Second seed run takes the "Database already seeded" early return —
        # exactly what staging does on every boot.
        fresh_sqlite.seed_initial_data(db)
        db.commit()
        assert _policy_count(db) == _expected_count(), (
            "retention policies not re-seeded on the early-return path — "
            "staging bug (PR-31) has regressed"
        )
    finally:
        db.close()


def test_reseed_is_idempotent_and_preserves_operator_changes(fresh_sqlite):
    db = fresh_sqlite.get_db()
    try:
        fresh_sqlite.seed_initial_data(db)
        db.commit()
        count_first = _policy_count(db)

        # Operator modifies a policy; re-seed must not touch existing rows.
        db.execute(
            "UPDATE data_retention_policies SET retention_days = 999 WHERE data_category = 'client_pii'"
        )
        db.commit()

        fresh_sqlite.seed_initial_data(db)
        db.commit()
        assert _policy_count(db) == count_first, "re-seed changed the row count"
        row = db.execute(
            "SELECT retention_days FROM data_retention_policies WHERE data_category = 'client_pii'"
        ).fetchone()
        assert row["retention_days"] == 999, "re-seed overwrote an operator-modified policy"

        rows = db.execute(
            "SELECT data_category, COUNT(*) AS n FROM data_retention_policies "
            "GROUP BY data_category HAVING COUNT(*) > 1"
        ).fetchall()
        assert rows == [], f"duplicate categories after re-seed: {rows}"
    finally:
        db.close()


def test_ensure_function_returns_inserted_count(fresh_sqlite):
    db = fresh_sqlite.get_db()
    try:
        first = fresh_sqlite._ensure_retention_policies(db)
        assert first == _expected_count()
        second = fresh_sqlite._ensure_retention_policies(db)
        assert second == 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Readiness probe (non-gating)
# ---------------------------------------------------------------------------

def test_readiness_reports_retention_policy_count(temp_db):
    """/api/readiness payload must expose the count; empty must NOT flip ready
    (gating semantics are deferred to the screening-readiness work, B6-B5)."""
    import server
    from db import get_db

    db = get_db()
    try:
        db.execute("DELETE FROM data_retention_policies")
        db.commit()
    finally:
        db.close()

    ready_empty, payload_empty = server._readiness_status_payload()
    rp = payload_empty["checks"]["retention_policies"]
    assert rp["status"] == "empty"
    assert rp["count"] == 0
    # Non-gating in PR-31: the empty table is visible but must not fail
    # readiness by itself (database/encryption/config may still gate).
    assert payload_empty["checks"]["database"]["status"] == "ok"

    import db as db_module
    db = get_db()
    try:
        db_module._ensure_retention_policies(db)
    finally:
        db.close()

    ready_ok, payload_ok = server._readiness_status_payload()
    rp_ok = payload_ok["checks"]["retention_policies"]
    assert rp_ok["status"] == "ok"
    assert rp_ok["count"] == _expected_count()

    # The non-gating invariant, asserted directly: the retention check must
    # contribute nothing to the overall ready flag — empty vs populated table
    # must leave `ready` identical (whatever the other checks decided).
    assert ready_empty == ready_ok, (
        "retention_policies check changed the overall ready flag — it must "
        "stay non-gating until the screening-readiness work (B6-B5) defines "
        "fail/degrade semantics for it"
    )


# ---------------------------------------------------------------------------
# PostgreSQL (skipped when no DSN; creates its own throwaway database)
# ---------------------------------------------------------------------------

def _pg_admin_dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")


@pytest.fixture()
def fresh_pg(monkeypatch):
    """A brand-new PostgreSQL database (init_db applied), dropped afterwards."""
    base_dsn = _pg_admin_dsn()
    if not base_dsn:
        pytest.skip("No PostgreSQL DSN (TEST_POSTGRES_DSN / DATABASE_URL_TEST) available")

    import psycopg2
    from urllib.parse import urlsplit, urlunsplit

    db_name = f"pr31_seed_{uuid.uuid4().hex[:12]}"
    parts = urlsplit(base_dsn)
    admin = psycopg2.connect(base_dsn)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{db_name}"')
    fresh_dsn = urlunsplit((parts.scheme, parts.netloc, "/" + db_name, parts.query, parts.fragment))

    orig_db_url = os.environ.get("DATABASE_URL")
    # Everything after CREATE DATABASE runs inside try/finally so a setup
    # failure (reload/init_db) still drops the throwaway database and closes
    # the admin connection.
    try:
        monkeypatch.setenv("DATABASE_URL", fresh_dsn)
        monkeypatch.setenv("ENVIRONMENT", "development")

        import config as config_module
        importlib.reload(config_module)
        import db as db_module
        importlib.reload(db_module)
        db_module.init_db()
        yield db_module
    finally:
        if orig_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = orig_db_url
        try:
            import config as config_module
            import db as db_module
            importlib.reload(config_module)
            importlib.reload(db_module)
        except Exception:
            pass
        try:
            with admin.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        except Exception:
            pass
        admin.close()


def test_fresh_pg_seed_populates_retention_policies(fresh_pg):
    """Acceptance assertion 1: fresh empty PostgreSQL + seed path => count > 0.
    Also the first-ever execution of these INSERTs against PG BOOLEAN columns."""
    db = fresh_pg.get_db()
    try:
        fresh_pg.seed_initial_data(db)
        db.commit()
        assert _policy_count(db) == _expected_count()
        row = db.execute(
            "SELECT auto_purge, requires_review FROM data_retention_policies "
            "WHERE data_category = 'session_tokens'"
        ).fetchone()
        assert row["auto_purge"] is False, "PG BOOLEAN did not bind Python bool correctly"
        assert row["requires_review"] is False
    finally:
        db.close()


def test_pg_staging_mimic_reseed_populates_empty_retention_table(fresh_pg):
    """The staging bug, reproduced and fixed on real PostgreSQL."""
    db = fresh_pg.get_db()
    try:
        fresh_pg.seed_initial_data(db)
        db.commit()
        db.execute("DELETE FROM data_retention_policies")
        db.commit()
        assert _policy_count(db) == 0

        fresh_pg.seed_initial_data(db)
        db.commit()
        assert _policy_count(db) == _expected_count(), (
            "retention policies not re-seeded on the early-return path on PostgreSQL"
        )
    finally:
        db.close()
