"""P10-7 (RDI-013 non-SAR, code half) — DB-level append-only audit_log.

Engine triggers block UPDATE/DELETE on audit_log unless a row exists in
audit_maintenance_window. Deployed environments boot armed (window empty);
test environments auto-open a standing window so the existing fixture-cleanup
and item-27 tamper-simulation suites keep working; the sanctioned manual
retention purge opens a transient window. INSERTs are never touched.
"""

import json
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

TEST_WINDOW_REASON = "nonprod_test_default"


def _open_db():
    from db import get_db
    return get_db()


def _window_count(db):
    return db.execute(
        "SELECT COUNT(*) AS c FROM audit_maintenance_window"
    ).fetchone()["c"]


@pytest.fixture
def armed_audit_log(temp_db):
    """Close the standing test window (arming the triggers), restore after."""
    db = _open_db()
    rows = db.execute(
        "SELECT id, reason, opened_by FROM audit_maintenance_window"
    ).fetchall()
    db.execute("DELETE FROM audit_maintenance_window")
    db.commit()
    db.close()
    yield
    db = _open_db()
    for r in rows:
        db.execute(
            "INSERT INTO audit_maintenance_window (reason, opened_by) VALUES (?, ?)",
            (r["reason"], r["opened_by"]),
        )
    db.commit()
    db.close()


# ── The armed state ──────────────────────────────────────────────────


def test_window_auto_opened_in_testing_env(temp_db):
    db = _open_db()
    row = db.execute(
        "SELECT COUNT(*) AS c FROM audit_maintenance_window WHERE reason = ?",
        (TEST_WINDOW_REASON,),
    ).fetchone()
    db.close()
    assert row["c"] >= 1, (
        "ENVIRONMENT=testing must auto-open a standing maintenance window "
        "so existing fixture cleanups keep working"
    )


def test_armed_triggers_block_update_and_delete_allow_insert(armed_audit_log):
    db = _open_db()
    try:
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail) "
            "VALUES (?,?,?,?,?,?)",
            ("p107", "P10-7", "system", "p107_probe", "t", "{}"),
        )
        db.commit()
        row_id = db.execute(
            "SELECT id FROM audit_log WHERE action='p107_probe'"
        ).fetchone()["id"]

        with pytest.raises(Exception, match="append-only"):
            db.execute("UPDATE audit_log SET detail='TAMPERED' WHERE id=?", (row_id,))
        db.rollback()

        with pytest.raises(Exception, match="append-only"):
            db.execute("DELETE FROM audit_log WHERE id=?", (row_id,))
        db.rollback()

        # INSERT stays open while armed.
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail) "
            "VALUES (?,?,?,?,?,?)",
            ("p107", "P10-7", "system", "p107_probe_2", "t", "{}"),
        )
        db.commit()
        survived = db.execute(
            "SELECT detail FROM audit_log WHERE id=?", (row_id,)
        ).fetchone()
        assert survived["detail"] == "{}", "blocked UPDATE must not have altered the row"
    finally:
        db.close()


def test_open_window_permits_mutation(armed_audit_log):
    """The maintenance-window context transiently disarms, then re-arms."""
    from regulated_deletion import audit_log_maintenance_window

    db = _open_db()
    try:
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail) "
            "VALUES (?,?,?,?,?,?)",
            ("p107", "P10-7", "system", "p107_window_probe", "t", "{}"),
        )
        db.commit()

        with audit_log_maintenance_window(db, actor_id="p107-test", reason="unit probe"):
            db.execute("DELETE FROM audit_log WHERE action='p107_window_probe'")
        db.commit()

        assert _window_count(db) == 0, "window row must be removed on exit"
        gone = db.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE action='p107_window_probe'"
        ).fetchone()
        assert gone["c"] == 0

        # Re-armed after the context exits. NOTE: row-level triggers fire only
        # when a row actually matches, so target an existing row.
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail) "
            "VALUES (?,?,?,?,?,?)",
            ("p107", "P10-7", "system", "p107_rearm_probe", "t", "{}"),
        )
        db.commit()
        with pytest.raises(Exception, match="append-only"):
            db.execute("DELETE FROM audit_log WHERE action='p107_rearm_probe'")
        db.rollback()
    finally:
        db.close()


# ── Sanctioned retention purge keeps working while armed ─────────────


def test_manual_retention_purge_of_audit_log_survives_armed_triggers(armed_audit_log):
    """The one legitimate production deletion path (gdpr.purge_expired_data)
    must open its transient window and succeed against ARMED triggers."""
    from gdpr import purge_expired_data

    db = _open_db()
    try:
        # data_category is UNIQUE and usually pre-seeded — upsert, don't insert.
        existing = db.execute(
            "SELECT id FROM data_retention_policies WHERE data_category='audit_logs'"
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE data_retention_policies SET retention_days=30, auto_purge=0 "
                "WHERE data_category='audit_logs'"
            )
        else:
            db.execute(
                "INSERT INTO data_retention_policies "
                "(data_category, retention_days, legal_basis, auto_purge, requires_review) "
                "VALUES (?,?,?,?,?)",
                ("audit_logs", 30, "P10-7 test", 0, 1),
            )
        db.execute(
            "INSERT INTO audit_log (timestamp, user_id, user_name, user_role, action, target, detail) "
            "VALUES (?,?,?,?,?,?,?)",
            ("2020-01-01T00:00:00", "p107", "P10-7", "system", "p107_expired", "t", "{}"),
        )
        db.commit()

        result = purge_expired_data(db, "audit_logs", purged_by="p107-test", dry_run=False)
        assert result["records_deleted"] >= 1

        left = db.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE action='p107_expired'"
        ).fetchone()
        assert left["c"] == 0
        assert _window_count(db) == 0, "transient purge window must be closed"
    finally:
        db.close()

    # Audit finding (blocker) regression: the marker must not survive on a
    # SECOND connection either — a same-connection read sees its own
    # uncommitted DELETE and cannot detect a committed leak that would leave
    # the triggers permanently disarmed.
    db2 = _open_db()
    try:
        assert _window_count(db2) == 0, (
            "maintenance-window marker row leaked COMMITTED after the purge — "
            "append-only triggers would be permanently disarmed"
        )
        # And the triggers must actually still be armed: an existing row's
        # delete has to abort.
        db2.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail) "
            "VALUES (?,?,?,?,?,?)",
            ("p107", "P10-7", "system", "p107_post_purge_probe", "t", "{}"),
        )
        db2.commit()
        with pytest.raises(Exception, match="append-only"):
            db2.execute("DELETE FROM audit_log WHERE action='p107_post_purge_probe'")
        db2.rollback()
    finally:
        db2.close()


def test_fixture_cleanup_audit_log_paths_use_window():
    """Audit finding: the two staging-sanctioned fixture cleanups delete
    audit_log rows and must open the window (they'd fail closed on staging
    otherwise — invisible under the testing auto-open)."""
    src = (BACKEND / "fixtures" / "cleanup.py").read_text(encoding="utf-8")
    assert src.count("audit_log_maintenance_window(") >= 2
    # Both audit_log removes must sit inside a window block.
    assert "with audit_log_maintenance_window(" in src


def test_pg_armed_triggers_block_and_window_permits():
    """PG-armed enforcement (audit gap): the plpgsql trigger must RAISE on a
    live PostgreSQL when the window is empty, and pass within a window —
    including same-transaction visibility of the uncommitted marker row."""
    import os
    dsn = os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")
    if not dsn:
        pytest.skip("Set TEST_POSTGRES_DSN or DATABASE_URL_TEST for live PostgreSQL validation.")

    import psycopg2
    import db as db_module
    from regulated_deletion import audit_log_maintenance_window, sanctioned_delete_context

    # NON-DESTRUCTIVE by design: this runs against the shared CI PostgreSQL
    # database mid-suite — no DDL drops, real audit_log table, window rows
    # saved and restored. (An earlier version DROPped audit_log and broke
    # every later PG test in the run.)
    raw = psycopg2.connect(dsn)
    db = db_module.DBConnection(raw, is_postgres=True)
    saved_windows = []
    try:
        db_module._ensure_audit_log_append_only(db)  # idempotent, no drops
        db.commit()
        saved_windows = [dict(r) for r in db.execute(
            "SELECT reason, opened_by FROM audit_maintenance_window"
        ).fetchall()]
        db.execute("DELETE FROM audit_maintenance_window")  # arm
        db.commit()

        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail) "
            "VALUES ('p107','P10-7','system','p107_pg_probe','t','{}')"
        )
        db.commit()

        # Armed: the plpgsql trigger must RAISE. (UPDATE is not covered by the
        # P12-1 Python guard, so this exercises the DB trigger specifically.)
        with pytest.raises(Exception, match="append-only"):
            db.execute("UPDATE audit_log SET detail='TAMPERED' WHERE action='p107_pg_probe'")
        db.rollback()

        # Window permits — the DELETE needs BOTH layers opened: the P12-1
        # Python guard (sanctioned context) AND the DB trigger (maintenance
        # window, uncommitted same-transaction visibility).
        with sanctioned_delete_context(
            "fixture_cleanup_nonprod", actor_id="p107-pg", role="system",
            reason="P10-7 PG probe cleanup", allowed_tables=("audit_log",),
            is_fixture=True, confirmed=True,
        ):
            with audit_log_maintenance_window(db, actor_id="p107-pg", reason="pg unit probe"):
                db.execute("DELETE FROM audit_log WHERE action='p107_pg_probe'")
        db.commit()
        row = db.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE action='p107_pg_probe'"
        ).fetchone()
        assert row["c"] == 0
        row = db.execute("SELECT COUNT(*) AS c FROM audit_maintenance_window").fetchone()
        assert row["c"] == 0, "marker must not survive the commit"
    finally:
        try:
            for r in saved_windows:
                db.execute(
                    "INSERT INTO audit_maintenance_window (reason, opened_by) VALUES (?, ?)",
                    (r["reason"], r["opened_by"]),
                )
            db.commit()
        except Exception:
            pass
        db.close()


def test_window_context_tolerates_missing_table():
    """Hand-built test schemas (no init_db) have neither the window table nor
    the triggers — the context must degrade to a no-op, not raise."""
    import sqlite3
    from db import DBConnection
    from regulated_deletion import audit_log_maintenance_window

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    db = DBConnection(raw, is_postgres=False)
    db.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, detail TEXT)")
    db.execute("INSERT INTO audit_log (detail) VALUES ('x')")
    with audit_log_maintenance_window(db, actor_id="t", reason="no table"):
        db.execute("UPDATE audit_log SET detail='y'")
    assert db.execute("SELECT detail FROM audit_log").fetchone()["detail"] == "y"
    raw.close()


# ── Ledger + wiring pins ─────────────────────────────────────────────


def test_migration_051_ledger_and_wiring():
    ledger = BACKEND / "migrations" / "scripts" / "migration_051_audit_log_append_only.sql"
    assert ledger.exists()
    text = ledger.read_text(encoding="utf-8")
    assert "SELECT 1;" in text and "RDI-013" in text

    import db as db_module
    # Schema migration: must NOT require the file runner (stays pre-marked on
    # fresh installs; the inline helper does the real work on every boot).
    assert "051" not in db_module.FILE_MIGRATIONS_REQUIRING_RUNNER

    src = (BACKEND / "db.py").read_text(encoding="utf-8")
    assert "_ensure_audit_log_append_only(db)" in src
    assert "trg_audit_log_append_only_upd" in src
    assert "trg_audit_log_append_only_del" in src


def test_scope_excludes_sar_tables():
    """RDI-013 SAR slices stay deferred (RDI-005/DCI-002): no trigger on
    sar_reports."""
    src = (BACKEND / "db.py").read_text(encoding="utf-8")
    helper = src[src.index("def _ensure_audit_log_append_only"):
                 src.index("def _run_migrations")]
    assert "sar_reports" not in helper
