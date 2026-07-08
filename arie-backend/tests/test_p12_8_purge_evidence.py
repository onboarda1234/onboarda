"""P12-8 (audit DCI-020 / DCI-021) — retention purge enforceability + evidence.

DCI-020: only audit_logs/monitoring_alerts had table mappings; every other
retention category returned a bare manual-purge error, so those policies
were documented but not demonstrably enforced. Unmapped categories are now
EXPLICITLY manual-with-procedure (gdpr.MANUAL_PURGE_CATEGORIES, per-category
reason + docs/compliance/MANUAL_PURGE_PROCEDURE.md), reported by the expired
summary rather than silently skipped, flagged loudly when misconfigured with
auto_purge=TRUE, and evidenced through gdpr.record_manual_purge (+ the
scripts/record_manual_purge.py CLI).

DCI-021: data_purge_log gains subject_id, application_id, tables_affected,
per_table_counts, purge_batch_id and evidence_json (fresh DDL both engines +
additive v2.48 repair); the automatic purge writes DELETE + evidence row in
ONE transaction (an evidence write failure rolls the deletion back), records
the ACTUAL deleted rowcount, and a scheduled run shares one batch id across
its categories so a regulator can reconstruct the run from the log alone.
"""

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EVIDENCE_COLUMNS = (
    "subject_id", "application_id", "tables_affected",
    "per_table_counts", "purge_batch_id", "evidence_json",
)


@pytest.fixture
def gdpr_db(temp_db):
    """GDPR-seeded connection on the conftest-managed temp database (fresh
    per process; avoids the import-time DB_PATH binding trap)."""
    from db import seed_initial_data, get_db
    conn = get_db()
    try:
        seed_initial_data(conn)
        conn.commit()
    except Exception:
        pass
    yield conn
    conn.close()


def _old_iso(days=4000):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def _insert_old_audit_rows(db, n=3):
    old = _old_iso()
    for i in range(n):
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address, timestamp) "
            "VALUES ('u','U','admin','test-old','t', ?, '127.0.0.1', ?)",
            (f"p128-old-{i}", old),
        )
    db.commit()
    return old


# ---------------------------------------------------------------------------
# DCI-021 — schema + enriched, atomic evidence
# ---------------------------------------------------------------------------

class TestPurgeEvidenceSchema:

    def test_fresh_schema_has_evidence_columns(self, gdpr_db):
        cols = {r["name"] for r in gdpr_db.execute("PRAGMA table_info(data_purge_log)").fetchall()}
        for col in EVIDENCE_COLUMNS:
            assert col in cols, f"data_purge_log.{col} missing from fresh schema"

    def test_repair_helper_adds_columns_to_legacy_table(self):
        """v2.48: a long-lived DB with the old data_purge_log shape gains the
        evidence columns additively."""
        from db import DBConnection, _ensure_data_purge_log_evidence_columns
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE data_purge_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data_category TEXT NOT NULL,
                record_count INTEGER NOT NULL,
                oldest_record_date TEXT,
                newest_record_date TEXT,
                retention_policy_id INTEGER,
                purge_reason TEXT NOT NULL,
                purged_by TEXT,
                purged_at TEXT DEFAULT (datetime('now'))
            )"""
        )
        db = DBConnection(conn, is_postgres=False)
        _ensure_data_purge_log_evidence_columns(db)
        _ensure_data_purge_log_evidence_columns(db)  # idempotent
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(data_purge_log)").fetchall()}
        for col in EVIDENCE_COLUMNS:
            assert col in cols
        conn.close()


class TestEnrichedAtomicPurgeLog:

    def test_purge_writes_full_evidence_row(self, gdpr_db):
        from gdpr import purge_expired_data
        _insert_old_audit_rows(gdpr_db, n=3)
        result = purge_expired_data(gdpr_db, "audit_logs", purged_by="admin001", dry_run=False)
        assert result["records_deleted"] >= 3
        assert result["purge_batch_id"].startswith("purge-")

        log = dict(gdpr_db.execute(
            "SELECT * FROM data_purge_log WHERE purge_batch_id = ?",
            (result["purge_batch_id"],),
        ).fetchone())
        assert json.loads(log["tables_affected"]) == ["audit_log"]
        counts = json.loads(log["per_table_counts"])
        assert counts == {"audit_log": log["record_count"]}
        evidence = json.loads(log["evidence_json"])
        assert evidence["engine"] == "gdpr.purge_expired_data"
        assert evidence["deleted_rowcount"] == log["record_count"]
        assert evidence["retention_days"] == result["retention_days"]
        assert log["subject_id"] is None and log["application_id"] is None

    def test_record_count_is_actual_deleted_rowcount(self, gdpr_db):
        from gdpr import purge_expired_data
        _insert_old_audit_rows(gdpr_db, n=5)
        result = purge_expired_data(gdpr_db, "audit_logs", purged_by="admin001", dry_run=False)
        log = dict(gdpr_db.execute(
            "SELECT record_count FROM data_purge_log WHERE purge_batch_id = ?",
            (result["purge_batch_id"],),
        ).fetchone())
        assert log["record_count"] == result["records_deleted"] >= 5

    def test_evidence_write_failure_rolls_back_the_deletion(self, gdpr_db):
        """DCI-021 atomicity: a purge whose evidence row cannot be written
        must not delete anything."""
        from gdpr import purge_expired_data
        old = _insert_old_audit_rows(gdpr_db, n=2)
        before = gdpr_db.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE timestamp = ?", (old,)
        ).fetchone()["c"]
        assert before >= 2

        gdpr_db.execute("ALTER TABLE data_purge_log RENAME TO data_purge_log_broken")
        gdpr_db.commit()
        try:
            with pytest.raises(Exception):
                purge_expired_data(gdpr_db, "audit_logs", purged_by="admin001", dry_run=False)
            gdpr_db.rollback()
            after = gdpr_db.execute(
                "SELECT COUNT(*) AS c FROM audit_log WHERE timestamp = ?", (old,)
            ).fetchone()["c"]
            assert after == before, (
                "rows were deleted although the evidence row could not be "
                "written — purge and evidence must be atomic"
            )
        finally:
            gdpr_db.execute("ALTER TABLE data_purge_log_broken RENAME TO data_purge_log")
            gdpr_db.commit()

    def test_shared_batch_id_reconstructs_a_run(self, gdpr_db):
        from gdpr import purge_expired_data
        _insert_old_audit_rows(gdpr_db, n=2)
        import uuid as _uuid
        shared = f"sched-test{_uuid.uuid4().hex[:8]}"
        r1 = purge_expired_data(
            gdpr_db, "audit_logs", purged_by="admin001", dry_run=False,
            purge_batch_id=shared,
        )
        assert r1["purge_batch_id"] == shared
        rows = gdpr_db.execute(
            "SELECT data_category FROM data_purge_log WHERE purge_batch_id = ?",
            (shared,),
        ).fetchall()
        assert [r["data_category"] for r in rows] == ["audit_logs"]

    def test_scheduled_run_uses_one_sched_batch_id(self, gdpr_db):
        from gdpr import run_scheduled_purge
        # monitoring_alerts is the only mappable category the scheduler may
        # purge; enable it and give it an expired row.
        old = _old_iso()
        gdpr_db.execute(
            "UPDATE data_retention_policies SET auto_purge = 1 WHERE data_category = 'monitoring_alerts'"
        )
        gdpr_db.execute(
            "INSERT INTO monitoring_alerts (application_id, alert_type, severity, status, created_at) "
            "VALUES ('p128-app', 'risk_drift', 'low', 'new', ?)",
            (old,),
        )
        gdpr_db.commit()
        results = run_scheduled_purge(gdpr_db, purged_by="system-scheduler")
        ma = next(r for r in results if r.get("category") == "monitoring_alerts")
        assert ma["records_deleted"] >= 1
        assert ma["purge_batch_id"].startswith("sched-")


# ---------------------------------------------------------------------------
# DCI-020 — manual-with-procedure categories
# ---------------------------------------------------------------------------

class TestManualPurgeCategories:

    def test_every_seeded_unmapped_category_is_documented(self):
        """Every DEFAULT-SEEDED retention category must be EITHER mapped for
        auto purge OR explicitly documented as manual-with-procedure.
        (Checks the seed constant, not DB rows — test suites insert ad-hoc
        policies into the shared test database.)"""
        from db import _DEFAULT_RETENTION_POLICIES
        from gdpr import CATEGORY_TABLE_MAP, MANUAL_PURGE_CATEGORIES
        for policy in _DEFAULT_RETENTION_POLICIES:
            category = policy[0]
            assert category in CATEGORY_TABLE_MAP or category in MANUAL_PURGE_CATEGORIES, (
                f"retention category {category!r} is neither auto-purgeable "
                f"nor documented manual-with-procedure — DCI-020 regression"
            )

    def test_manual_category_returns_structured_manual_result(self, gdpr_db):
        from gdpr import purge_expired_data, MANUAL_PURGE_PROCEDURE_REF
        result = purge_expired_data(gdpr_db, "client_pii", purged_by="admin001", dry_run=False)
        assert "error" not in result
        assert result["status"] == "manual_purge_required"
        assert result["auto_purge_supported"] is False
        assert result["manual_procedure"] == MANUAL_PURGE_PROCEDURE_REF
        assert result["records_deleted"] == 0
        assert result["manual_reason"]

    def test_unknown_category_still_errors(self, gdpr_db):
        from gdpr import purge_expired_data
        result = purge_expired_data(gdpr_db, "nonexistent_category", dry_run=True)
        assert "error" in result

    def test_summary_reports_manual_categories(self, gdpr_db):
        from gdpr import get_expired_data_summary
        summary = get_expired_data_summary(gdpr_db)
        manual = [e for e in summary if e.get("manual_purge_required")]
        categories = {e["category"] for e in manual}
        assert "client_pii" in categories
        assert "sar_reports" in categories
        for entry in manual:
            assert entry["auto_purge_supported"] is False
            assert entry["manual_procedure"]
            assert entry["manual_reason"]

    def test_scheduler_flags_misconfigured_auto_purge_on_manual_category(self, gdpr_db, caplog):
        from gdpr import run_scheduled_purge
        gdpr_db.execute(
            "UPDATE data_retention_policies SET auto_purge = 1 WHERE data_category = 'client_pii'"
        )
        gdpr_db.commit()
        import logging
        with caplog.at_level(logging.ERROR):
            results = run_scheduled_purge(gdpr_db, purged_by="system-scheduler")
        entry = next(r for r in results if r.get("category") == "client_pii")
        assert entry["misconfigured_auto_purge_flag"] is True
        assert entry["records_deleted"] == 0
        assert any("misconfiguration" in rec.message.lower() for rec in caplog.records)


# ---------------------------------------------------------------------------
# DCI-020/021 — manual purge evidence recording
# ---------------------------------------------------------------------------

class TestRecordManualPurge:

    def test_happy_path_writes_enriched_row(self, gdpr_db):
        from gdpr import record_manual_purge
        result = record_manual_purge(
            gdpr_db,
            category="client_pii",
            per_table_counts={"clients": 2, "applications": 2, "directors": 5},
            purge_reason="Q3 retention review: relationships ended 2019-06",
            purged_by="ops-1",
            approved_by="sco-1",
            subject_id="client-x",
            application_id="app-x",
            evidence={"change_ticket": "OPS-123"},
        )
        assert result["status"] == "recorded"
        assert result["purge_batch_id"].startswith("manual-")
        assert result["record_count"] == 9

        log = dict(gdpr_db.execute(
            "SELECT * FROM data_purge_log WHERE purge_batch_id = ?",
            (result["purge_batch_id"],),
        ).fetchone())
        assert log["subject_id"] == "client-x"
        assert log["application_id"] == "app-x"
        assert json.loads(log["tables_affected"]) == ["applications", "clients", "directors"]
        assert json.loads(log["per_table_counts"])["directors"] == 5
        evidence = json.loads(log["evidence_json"])
        assert evidence["approved_by"] == "sco-1"
        assert evidence["operator_evidence"]["change_ticket"] == "OPS-123"
        assert "MANUAL purge" in log["purge_reason"]

    def test_validation_failures(self, gdpr_db):
        from gdpr import record_manual_purge
        base = dict(
            category="client_pii",
            per_table_counts={"clients": 1},
            purge_reason="r", purged_by="ops-1", approved_by="sco-1",
        )
        assert "error" in record_manual_purge(gdpr_db, **{**base, "per_table_counts": {}})
        assert "error" in record_manual_purge(gdpr_db, **{**base, "per_table_counts": {"clients": -1}})
        assert "error" in record_manual_purge(gdpr_db, **{**base, "purge_reason": "  "})
        assert "error" in record_manual_purge(gdpr_db, **{**base, "approved_by": ""})
        assert "error" in record_manual_purge(gdpr_db, **{**base, "category": "not_a_category"})

    def test_never_purge_table_refused(self, gdpr_db):
        from gdpr import record_manual_purge
        before = gdpr_db.execute(
            "SELECT COUNT(*) AS c FROM data_purge_log WHERE purge_batch_id LIKE 'manual-%'"
        ).fetchone()["c"]
        result = record_manual_purge(
            gdpr_db,
            category="audit_logs",
            per_table_counts={"supervisor_audit_log": 1},
            purge_reason="r", purged_by="ops-1", approved_by="sco-1",
        )
        assert "error" in result
        assert "never-purge" in result["error"]
        after = gdpr_db.execute(
            "SELECT COUNT(*) AS c FROM data_purge_log WHERE purge_batch_id LIKE 'manual-%'"
        ).fetchone()["c"]
        assert after == before, "refused manual purge must not write an evidence row"
