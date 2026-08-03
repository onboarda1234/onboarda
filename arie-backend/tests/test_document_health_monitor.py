import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def monitor_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))

    monkeypatch.setattr("config.DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr("db.DB_PATH", str(tmp_path / "test.db"))
    import db as db_module
    db_module.init_db()
    conn = db_module.get_db()

    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "version TEXT UNIQUE NOT NULL, "
        "filename TEXT NOT NULL, "
        "description TEXT DEFAULT '', "
        "applied_at TEXT DEFAULT (datetime('now')), "
        "checksum TEXT)"
    )
    for v, fn in [
        ("001", "migration_001_initial.sql"),
        ("002", "migration_002_supervisor_tables.sql"),
        ("003", "migration_003_monitoring_indexes.sql"),
        ("004", "migration_004_documents_s3_key.sql"),
        ("005", "migration_005_applications_truth_schema.sql"),
        ("006", "migration_006_person_dob.sql"),
        ("007", "migration_007_screening_reports_normalized.sql"),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, filename) VALUES (?, ?)",
            (v, fn),
        )
    conn.execute(
        "INSERT INTO applications (id, ref, company_name, risk_level, status) "
        "VALUES (?, ?, ?, ?, ?)",
        ("app-doc-health", "APP-DOC", "Document Health Co", "MEDIUM", "approved"),
    )
    conn.commit()

    from migrations.runner import run_all_migrations_with_connection
    run_all_migrations_with_connection(conn)
    yield conn
    conn.close()


@pytest.fixture
def audit_sink():
    events = []

    def writer(user, action, target, detail, db=None,
               before_state=None, after_state=None, commit=True):
        events.append({
            "user": dict(user) if user else {},
            "action": action,
            "target": target,
            "detail": detail,
            "before_state": before_state,
            "after_state": after_state,
            "commit": commit,
        })

    writer.events = events
    return writer


USER = {"sub": "officer-1", "name": "Officer", "role": "co"}


def _insert_doc(conn, *, doc_id, doc_type, uploaded_at=None, expiry_date=None,
                valid_until=None, is_current=True, superseded_at=None):
    conn.execute(
        """
        INSERT INTO documents
            (id, application_id, doc_type, doc_name, file_path, uploaded_at,
             expiry_date, valid_until, is_current, superseded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            "app-doc-health",
            doc_type,
            f"{doc_type}.pdf",
            f"/tmp/{doc_type}.pdf",
            uploaded_at or datetime.now(timezone.utc).isoformat(),
            expiry_date,
            valid_until,
            1 if is_current else 0,
            superseded_at,
        ),
    )
    conn.commit()


def _alerts(conn):
    return conn.execute(
        "SELECT * FROM monitoring_alerts ORDER BY id ASC"
    ).fetchall()


class TestDocumentHealthMonitor:
    def test_requires_audit_writer_before_mutation(self, monitor_db):
        from document_health_monitor import sync_document_health_alerts_for_application
        from lifecycle_linkage import MissingAuditWriter

        _insert_doc(
            monitor_db,
            doc_id="doc-no-audit",
            doc_type="passport",
            expiry_date=(datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat(),
        )
        with pytest.raises(MissingAuditWriter):
            sync_document_health_alerts_for_application(
                monitor_db, "app-doc-health", user=USER, audit_writer=None,
            )
        assert _alerts(monitor_db) == []

    def test_current_document_sql_matches_db_dialect(self):
        from document_health_monitor import _current_document_sql

        assert _current_document_sql(type("DB", (), {"is_postgres": True})()) == "COALESCE(is_current, TRUE) = TRUE"
        assert _current_document_sql(type("DB", (), {"is_postgres": False})()) == "COALESCE(is_current, 1) = 1"

    def test_creates_alert_for_expired_document(self, monitor_db, audit_sink):
        from document_health_monitor import sync_document_health_alerts_for_application

        _insert_doc(
            monitor_db,
            doc_id="doc-expired",
            doc_type="passport",
            expiry_date=(datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat(),
        )
        result = sync_document_health_alerts_for_application(
            monitor_db, "app-doc-health", user=USER, audit_writer=audit_sink,
        )
        row = _alerts(monitor_db)[0]
        assert result["created"] == 1
        assert row["alert_type"] == "document_expired"
        assert row["severity"] == "high"
        assert row["discovered_via"] == "document_health"

    def test_creates_alert_for_expiring_soon_document(self, monitor_db, audit_sink):
        from document_health_monitor import sync_document_health_alerts_for_application

        _insert_doc(
            monitor_db,
            doc_id="doc-soon",
            doc_type="passport",
            expiry_date=(datetime.now(timezone.utc) + timedelta(days=10)).date().isoformat(),
        )
        sync_document_health_alerts_for_application(
            monitor_db, "app-doc-health", user=USER, audit_writer=audit_sink,
        )
        assert _alerts(monitor_db)[0]["alert_type"] == "document_expiring_soon"

    def test_creates_alert_for_stale_document(self, monitor_db, audit_sink):
        from document_health_monitor import sync_document_health_alerts_for_application

        _insert_doc(
            monitor_db,
            doc_id="doc-stale",
            doc_type="bankref",
            uploaded_at=(datetime.now(timezone.utc) - timedelta(days=400)).isoformat(),
        )
        sync_document_health_alerts_for_application(
            monitor_db, "app-doc-health", user=USER, audit_writer=audit_sink,
        )
        assert _alerts(monitor_db)[0]["alert_type"] == "document_stale"

    def test_creates_alert_for_missing_expiry(self, monitor_db, audit_sink):
        from document_health_monitor import sync_document_health_alerts_for_application

        _insert_doc(monitor_db, doc_id="doc-missing-expiry", doc_type="passport")
        sync_document_health_alerts_for_application(
            monitor_db, "app-doc-health", user=USER, audit_writer=audit_sink,
        )
        assert _alerts(monitor_db)[0]["alert_type"] == "document_expiry_missing"

    def test_does_not_duplicate_open_alert(self, monitor_db, audit_sink):
        from document_health_monitor import sync_document_health_alerts_for_application

        _insert_doc(
            monitor_db,
            doc_id="doc-repeat",
            doc_type="passport",
            expiry_date=(datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat(),
        )
        sync_document_health_alerts_for_application(
            monitor_db, "app-doc-health", user=USER, audit_writer=audit_sink,
        )
        sync_document_health_alerts_for_application(
            monitor_db, "app-doc-health", user=USER, audit_writer=audit_sink,
        )
        assert len(_alerts(monitor_db)) == 1

    def test_reports_repeatable_resolution_candidate_without_mutation(
        self, monitor_db, audit_sink,
    ):
        from document_health_monitor import sync_document_health_alerts_for_application

        expired = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        _insert_doc(monitor_db, doc_id="doc-old", doc_type="passport", expiry_date=expired)
        sync_document_health_alerts_for_application(
            monitor_db, "app-doc-health", user=USER, audit_writer=audit_sink,
        )
        monitor_db.execute(
            "UPDATE documents SET is_current = 0, superseded_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), "doc-old"),
        )
        _insert_doc(
            monitor_db,
            doc_id="doc-new",
            doc_type="passport",
            expiry_date=(datetime.now(timezone.utc) + timedelta(days=365)).date().isoformat(),
        )
        first = sync_document_health_alerts_for_application(
            monitor_db, "app-doc-health", user=USER, audit_writer=audit_sink,
        )
        first_row = dict(_alerts(monitor_db)[0])
        second = sync_document_health_alerts_for_application(
            monitor_db, "app-doc-health", user=USER, audit_writer=audit_sink,
        )
        second_row = dict(_alerts(monitor_db)[0])

        assert first["resolved"] == 0
        assert first["resolution_deferred"] == 1
        assert first["resolution_candidates"] == second["resolution_candidates"]
        assert first["resolution_candidates"] == [{
            "alert_id": first_row["id"],
            "application_id": "app-doc-health",
            "alert_type": "document_expired",
            "source_reference": "document:doc-old",
            "current_status": "open",
            "reason": "document_issue_no_longer_current",
        }]
        assert first_row == second_row
        assert first_row["status"] == "open"
        assert first_row["officer_action"] is None
        assert first_row["officer_notes"] is None
        assert first_row["resolved_at"] is None
        assert first_row["reviewed_at"] is None
        assert not any(
            event["action"] == "monitoring.document_health_alert.resolved"
            for event in audit_sink.events
        )

    def test_does_not_alert_for_non_current_superseded_document(self, monitor_db, audit_sink):
        from document_health_monitor import sync_document_health_alerts_for_application

        _insert_doc(
            monitor_db,
            doc_id="doc-superseded",
            doc_type="passport",
            expiry_date=(datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat(),
            is_current=False,
            superseded_at=datetime.now(timezone.utc).isoformat(),
        )
        sync_document_health_alerts_for_application(
            monitor_db, "app-doc-health", user=USER, audit_writer=audit_sink,
        )
        assert _alerts(monitor_db) == []

    def test_writes_audit_event(self, monitor_db, audit_sink):
        from document_health_monitor import sync_document_health_alerts_for_application

        _insert_doc(
            monitor_db,
            doc_id="doc-audit",
            doc_type="passport",
            expiry_date=(datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat(),
        )
        sync_document_health_alerts_for_application(
            monitor_db, "app-doc-health", user=USER, audit_writer=audit_sink,
        )
        assert any(e["action"] == "monitoring.document_health_alert.created"
                   for e in audit_sink.events)
        assert all(e["commit"] is False for e in audit_sink.events)

    def test_update_audit_is_atomic_and_disappearance_is_deferred(
        self, monitor_db, audit_sink,
    ):
        from document_health_monitor import sync_document_health_alerts_for_application

        _insert_doc(
            monitor_db,
            doc_id="doc-audit-update",
            doc_type="passport",
            expiry_date=(datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat(),
        )
        monitor_db.execute(
            """
            INSERT INTO monitoring_alerts
                (application_id, client_name, alert_type, severity, detected_by,
                 summary, source_reference, ai_recommendation, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "app-doc-health",
                "Document Health Co",
                "document_expired",
                "low",
                "document_health_monitor",
                "stale summary",
                "document:doc-audit-update",
                "old recommendation",
                "open",
            ),
        )
        monitor_db.commit()
        sync_document_health_alerts_for_application(
            monitor_db, "app-doc-health", user=USER, audit_writer=audit_sink,
        )
        monitor_db.execute(
            "UPDATE documents SET is_current = 0, superseded_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), "doc-audit-update"),
        )
        monitor_db.commit()
        result = sync_document_health_alerts_for_application(
            monitor_db, "app-doc-health", user=USER, audit_writer=audit_sink,
        )

        update_event = next(e for e in audit_sink.events if e["action"] == "monitoring.document_health_alert.updated")
        assert update_event["before_state"]["summary"] != update_event["after_state"]["summary"]
        assert update_event["commit"] is False
        assert result["resolved"] == 0
        assert result["resolution_deferred"] == 1
        assert _alerts(monitor_db)[0]["status"] == "open"
        assert not any(
            event["action"] == "monitoring.document_health_alert.resolved"
            for event in audit_sink.events
        )

    def test_audit_failure_rolls_back_alert_creation(self, monitor_db):
        from document_health_monitor import sync_document_health_alerts_for_application

        _insert_doc(
            monitor_db,
            doc_id="doc-audit-failure",
            doc_type="passport",
            expiry_date=(datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat(),
        )
        calls = []

        def failing_audit_writer(
            user, action, target, detail, db=None,
            before_state=None, after_state=None, commit=True,
        ):
            calls.append(commit)
            raise RuntimeError("audit unavailable")

        with pytest.raises(RuntimeError, match="audit unavailable"):
            sync_document_health_alerts_for_application(
                monitor_db,
                "app-doc-health",
                user=USER,
                audit_writer=failing_audit_writer,
            )

        assert calls == [False]
        assert _alerts(monitor_db) == []
