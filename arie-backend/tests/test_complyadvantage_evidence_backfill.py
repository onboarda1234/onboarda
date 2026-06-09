import json
import sqlite3

from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME
from screening_complyadvantage.evidence_backfill import _candidate_alerts, backfill_monitoring_alert_evidence


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE monitoring_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT,
            case_identifier TEXT,
            application_id TEXT,
            client_name TEXT,
            alert_type TEXT,
            severity TEXT,
            detected_by TEXT,
            summary TEXT,
            source_reference TEXT,
            status TEXT DEFAULT 'open'
        );
        CREATE TABLE screening_reports_normalized (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            normalized_report_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE monitoring_alert_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            monitoring_alert_id INTEGER NOT NULL,
            application_id TEXT,
            provider TEXT NOT NULL,
            case_identifier TEXT,
            alert_identifier TEXT,
            match_identifier TEXT,
            risk_identifier TEXT,
            profile_identifier TEXT,
            evidence_type TEXT,
            matched_subject_name TEXT,
            relationship_to_client TEXT,
            match_category TEXT,
            risk_indicator TEXT,
            match_confidence TEXT,
            source_title TEXT,
            source_name TEXT,
            source_url TEXT,
            source_url_available INTEGER DEFAULT 0,
            source_url_unavailable_reason TEXT,
            publication_date TEXT,
            snippet TEXT,
            provider_case_url TEXT,
            evidence_json TEXT,
            raw_provider_reference TEXT,
            evidence_status TEXT DEFAULT 'fetched',
            evidence_hash TEXT NOT NULL,
            fetched_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(monitoring_alert_id, evidence_hash)
        );
        """
    )
    return conn


def _normalized_report(with_evidence=True):
    if not with_evidence:
        return {
            "provider": COMPLYADVANTAGE_PROVIDER_NAME,
            "provider_specific": {
                COMPLYADVANTAGE_PROVIDER_NAME: {
                    "matches": [],
                    "workflows": {"strict": {"alerts": [{"identifier": "alert-1"}]}},
                }
            },
        }
    indicator_value = {
        "title": "Provider article title",
        "source_name": "Provider source",
        "publication_date": "2026-01-02",
        "snippet": "Provider summary",
    }
    return {
        "provider": COMPLYADVANTAGE_PROVIDER_NAME,
        "provider_specific": {
            COMPLYADVANTAGE_PROVIDER_NAME: {
                "matches": [{
                    "risk_id": "risk-1",
                    "profile_identifier": "profile-1",
                    "profile": {"person": {"names": {"values": [{"name": "Matched Person"}]}}},
                    "screening_subject": {"kind": "director", "person_key": "dir-1"},
                    "indicators": [{
                        "type": "CAMediaIndicator",
                        "taxonomy_key": "r_adverse_media_general",
                        "taxonomy_label": "Adverse media",
                        "value": indicator_value,
                    }],
                }],
                "workflows": {"strict": {"alerts": [{"identifier": "alert-1"}]}},
            }
        },
    }


def _insert_alert(conn, *, normalized_record_id=None, case_identifier="case-1", alert_identifier="alert-1"):
    source_reference = {
        "provider": COMPLYADVANTAGE_PROVIDER_NAME,
        "case_identifier": case_identifier,
        "alert_identifier": alert_identifier,
    }
    if normalized_record_id:
        source_reference["normalized_record_id"] = normalized_record_id
    cur = conn.execute(
        """
        INSERT INTO monitoring_alerts
            (provider, case_identifier, application_id, client_name, alert_type, severity, detected_by, summary, source_reference)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            COMPLYADVANTAGE_PROVIDER_NAME,
            case_identifier,
            "app-1",
            "Client Ltd",
            "media",
            "high",
            COMPLYADVANTAGE_PROVIDER_NAME,
            "Media hit",
            json.dumps(source_reference),
        ),
    )
    return cur.lastrowid


def _insert_normalized(conn, report):
    cur = conn.execute(
        "INSERT INTO screening_reports_normalized (client_id, application_id, provider, normalized_report_json) VALUES (?, ?, ?, ?)",
        ("client-1", "app-1", COMPLYADVANTAGE_PROVIDER_NAME, json.dumps(report)),
    )
    return cur.lastrowid


def test_backfill_persists_structured_evidence_from_stored_normalized_report():
    conn = _db()
    normalized_id = _insert_normalized(conn, _normalized_report())
    alert_id = _insert_alert(conn, normalized_record_id=normalized_id)

    result = backfill_monitoring_alert_evidence(conn, dry_run=False, limit=10, trace_id="test-ca1b")

    assert result["alerts_checked"] == 1
    assert result["evidence_rows_inserted"] == 1
    row = conn.execute("SELECT * FROM monitoring_alert_evidence WHERE monitoring_alert_id = ?", (alert_id,)).fetchone()
    assert row["evidence_status"] == "fetched"
    assert row["matched_subject_name"] == "Matched Person"
    assert row["source_title"] == "Provider article title"
    assert row["source_url_available"] == 0
    assert row["source_url_unavailable_reason"] == "Source article link not available from ComplyAdvantage payload."


def test_backfill_is_idempotent_and_does_not_create_duplicate_alerts():
    conn = _db()
    normalized_id = _insert_normalized(conn, _normalized_report())
    _insert_alert(conn, normalized_record_id=normalized_id)

    backfill_monitoring_alert_evidence(conn, dry_run=False, limit=10, trace_id="test-ca1b")
    backfill_monitoring_alert_evidence(conn, dry_run=False, limit=10, alert_ids=[1], trace_id="test-ca1b")

    assert conn.execute("SELECT COUNT(*) AS count FROM monitoring_alerts").fetchone()["count"] == 1
    assert conn.execute("SELECT COUNT(*) AS count FROM monitoring_alert_evidence").fetchone()["count"] == 1


def test_backfill_marks_unavailable_when_provider_truth_has_no_evidence():
    conn = _db()
    normalized_id = _insert_normalized(conn, _normalized_report(with_evidence=False))
    alert_id = _insert_alert(conn, normalized_record_id=normalized_id)

    result = backfill_monitoring_alert_evidence(conn, dry_run=False, limit=10, trace_id="test-ca1b")

    assert result["evidence_rows_unavailable"] == 1
    row = conn.execute("SELECT evidence_status, source_url_unavailable_reason FROM monitoring_alert_evidence WHERE monitoring_alert_id = ?", (alert_id,)).fetchone()
    assert row["evidence_status"] == "unavailable"
    assert row["source_url_unavailable_reason"] == "Detailed provider evidence is not available for this alert."


def test_backfill_does_not_attach_same_application_evidence_to_non_provider_case_id():
    conn = _db()
    _insert_normalized(conn, _normalized_report())
    cur = conn.execute(
        """
        INSERT INTO monitoring_alerts
            (provider, case_identifier, application_id, client_name, alert_type, severity, detected_by, summary, source_reference)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            None,
            None,
            "app-1",
            "Manual audit alert",
            "media",
            "high",
            COMPLYADVANTAGE_PROVIDER_NAME,
            "Manual audit alert",
            json.dumps({
                "provider": COMPLYADVANTAGE_PROVIDER_NAME,
                "case_id": "AUDIT-SPRINT2-CASE-20260609T145509Z",
                "alert_identifier": "AUDIT-SPRINT2-20260609T145509Z",
            }),
        ),
    )
    alert_id = cur.lastrowid

    result = backfill_monitoring_alert_evidence(conn, dry_run=False, limit=10, trace_id="test-ca1b")

    assert result["evidence_rows_unavailable"] == 1
    rows = conn.execute("SELECT evidence_status, case_identifier, evidence_type FROM monitoring_alert_evidence WHERE monitoring_alert_id = ?", (alert_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["evidence_status"] == "unavailable"
    assert rows[0]["evidence_type"] == "provider_evidence_status"
    assert rows[0]["case_identifier"] == "AUDIT-SPRINT2-CASE-20260609T145509Z"


def test_backfill_records_safe_failure_when_live_detail_fetch_fails():
    class FailingClient:
        def get(self, path, params=None):
            raise RuntimeError("provider unavailable")

    conn = _db()
    alert_id = _insert_alert(
        conn,
        normalized_record_id=None,
        case_identifier="019ea5c2-9a39-7dfc-84dc-2910efe3e976",
        alert_identifier="019ea5c2-987d-797d-877e-53748602391f",
    )

    result = backfill_monitoring_alert_evidence(
        conn,
        ca_client=FailingClient(),
        dry_run=False,
        limit=10,
        fetch_live_details=True,
        trace_id="test-ca1b",
    )

    assert result["evidence_rows_failed"] == 1
    row = conn.execute("SELECT evidence_status, source_url_unavailable_reason FROM monitoring_alert_evidence WHERE monitoring_alert_id = ?", (alert_id,)).fetchone()
    assert row["evidence_status"] == "failed"
    assert row["source_url_unavailable_reason"] == "RuntimeError"


def test_backfill_dry_run_reports_would_update_without_persisting():
    conn = _db()
    normalized_id = _insert_normalized(conn, _normalized_report())
    _insert_alert(conn, normalized_record_id=normalized_id)

    result = backfill_monitoring_alert_evidence(conn, dry_run=True, limit=10, trace_id="test-ca1b")

    assert result["alerts_would_update"] == 1
    assert result["evidence_rows_inserted"] == 0
    assert conn.execute("SELECT COUNT(*) AS count FROM monitoring_alert_evidence").fetchone()["count"] == 0


def test_candidate_query_parameterizes_provider_wildcard_for_postgres_wrapper():
    class RecordingDB:
        def __init__(self):
            self.sql = ""
            self.params = ()

        def execute(self, sql, params=()):
            self.sql = sql
            self.params = params
            return self

        def fetchall(self):
            return []

    db = RecordingDB()

    _candidate_alerts(db, limit=25, alert_ids=None)

    assert "LIKE ?" in db.sql
    assert "LIKE '%complyadvantage%'" not in db.sql
    assert db.params[-2:] == ("%complyadvantage%", 25)
