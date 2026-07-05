"""PR-20 — persist the memo hard-block verdict (compliance_memos.blocked).

memo_handler computes a block verdict into memo['metadata']['blocked'] /
['block_reason'] (contradiction + mandatory-escalation cases). The approval
gate (security_hardening.ApprovalGateValidator) reads the PERSISTED
memo_row['blocked'] — so if the write path omits the columns they default to
FALSE/NULL and a memo that should hard-block an approval silently doesn't.

These tests lock: the value helper is correct on both backends, the production
INSERT and the supervisor-rewrite UPDATE both persist the columns, and a
persisted block verdict is honored by the gate — on SQLite and live PostgreSQL.
"""
import importlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _FakePG:
    """Stand-in for a PostgreSQL DBConnection: only is_postgres is consulted."""
    is_postgres = True


# ── value helper (adversarial-verifier: the boolean idiom must match the column type) ──

def test_block_columns_helper_sqlite_uses_int():
    import server
    val, reason = server._memo_block_columns(
        object(), {"metadata": {"blocked": True, "block_reason": "contradiction"}}
    )  # object() has no is_postgres attr -> treated as SQLite
    assert val == 1 and reason == "contradiction"
    val0, reason0 = server._memo_block_columns(object(), {"metadata": {"blocked": False}})
    assert val0 == 0 and reason0 is None
    # missing metadata / empty memo must not raise and must read as not-blocked
    assert server._memo_block_columns(object(), {}) == (0, None)
    assert server._memo_block_columns(object(), None) == (0, None)


def test_block_columns_helper_postgres_uses_real_bool():
    import server
    val, reason = server._memo_block_columns(
        _FakePG(), {"metadata": {"blocked": True, "block_reason": "veto"}}
    )
    assert val is True and reason == "veto"
    val0, reason0 = server._memo_block_columns(_FakePG(), {"metadata": {"blocked": None}})
    assert val0 is False and reason0 is None


# ── the two write sites must persist the columns (guards the wiring) ──

def test_production_insert_persists_blocked_columns():
    src = open(os.path.join(BACKEND, "server.py"), encoding="utf-8").read()
    idx = src.find(
        "INSERT INTO compliance_memos (application_id, memo_data, generated_by, "
        "ai_recommendation, review_status, quality_score"
    )
    assert idx > 0, "primary compliance_memos INSERT not found"
    stmt = src[idx:idx + 700]
    assert "blocked" in stmt and "block_reason" in stmt, \
        "primary memo INSERT does not persist blocked/block_reason (PR-20 regressed)"


def test_supervisor_rewrite_update_syncs_blocked_columns():
    src = open(os.path.join(BACKEND, "server.py"), encoding="utf-8").read()
    assert "UPDATE compliance_memos SET memo_data = ?, supervisor_status = ?, " \
           "supervisor_summary = ?, blocked = ?, block_reason = ?" in src, \
        "Run-Memo-Supervisor rewrite does not keep the blocked column in sync (PR-20)"


# ── functional: a persisted block verdict is honored by the approval gate (SQLite) ──

def test_persisted_block_verdict_blocks_approval(db):
    import server
    from tests.test_phase4_verification_hardening import _insert_app_and_memo
    from security_hardening import ApprovalGateValidator

    app = _insert_app_and_memo(db)  # otherwise-clean memo, blocked defaulting to 0
    app_id = app["id"]

    # simulate a memo_handler block verdict flowing through the persistence helper
    memo = {"metadata": {"blocked": True, "block_reason": "Rule violation + INCONSISTENT supervisor"}}
    val, reason = server._memo_block_columns(db, memo)
    assert val == 1
    db.execute(
        "UPDATE compliance_memos SET blocked = ?, block_reason = ? WHERE application_id = ?",
        (val, reason, app_id),
    )
    db.commit()

    can, err = ApprovalGateValidator.validate_approval(app, db)
    assert can is False
    assert "blocked" in (err or "").lower()

    # control: a non-blocked verdict does not raise the memo-blocked bar
    val0, reason0 = server._memo_block_columns(db, {"metadata": {"blocked": False}})
    db.execute(
        "UPDATE compliance_memos SET blocked = ?, block_reason = ? WHERE application_id = ?",
        (val0, reason0, app_id),
    )
    db.commit()
    row = db.execute("SELECT blocked FROM compliance_memos WHERE application_id = ?", (app_id,)).fetchone()
    assert row["blocked"] in (0, False)


# ── PostgreSQL round-trip: the column persists as a real BOOLEAN and reads back true ──

def _pg_dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")


@pytest.fixture()
def fresh_pg(monkeypatch):
    base_dsn = _pg_dsn()
    if not base_dsn:
        pytest.skip("No PostgreSQL DSN available")
    import psycopg2
    from urllib.parse import urlsplit, urlunsplit
    db_name = f"pr20_{uuid.uuid4().hex[:12]}"
    parts = urlsplit(base_dsn)
    admin = psycopg2.connect(base_dsn)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{db_name}"')
    except Exception:
        admin.close()
        raise
    fresh_dsn = urlunsplit((parts.scheme, parts.netloc, "/" + db_name, parts.query, parts.fragment))
    orig = os.environ.get("DATABASE_URL")
    try:
        monkeypatch.setenv("DATABASE_URL", fresh_dsn)
        monkeypatch.setenv("ENVIRONMENT", "development")
        import config as config_module
        import db as db_module
        importlib.reload(config_module)
        importlib.reload(db_module)
        db_module.init_db()
        conn = db_module.get_db()
        db_module.seed_initial_data(conn)
        conn.commit()
        yield db_module
    finally:
        if orig is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = orig
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


def test_pg_blocked_column_round_trips_as_real_boolean(fresh_pg):
    import server
    db = fresh_pg.get_db()
    try:
        app_id = "pr20-pg-app"
        db.execute(
            "INSERT INTO applications (id, ref, company_name, status) VALUES (?, ?, 'Co', 'submitted_to_compliance')",
            (app_id, "R-PR20PG"),
        )
        val, reason = server._memo_block_columns(db, {"metadata": {"blocked": True, "block_reason": "veto"}})
        assert val is True  # real boolean on PG
        db.execute(
            "INSERT INTO compliance_memos (application_id, version, memo_data, review_status, "
            "validation_status, supervisor_status, blocked, block_reason) "
            "VALUES (?, 1, ?, 'approved', 'pass', 'INCONSISTENT', ?, ?)",
            (app_id, json.dumps({"metadata": {"blocked": True}}), val, reason),
        )
        db.commit()
        row = db.execute(
            "SELECT blocked, block_reason FROM compliance_memos WHERE application_id = ?", (app_id,)
        ).fetchone()
        assert row["blocked"] is True          # persisted as a real PG BOOLEAN, not 1
        assert row["block_reason"] == "veto"
        # a reader that keys on the persisted column sees the block
        assert server._memo_final_status(dict(row)) == "blocked"
    finally:
        db.close()
