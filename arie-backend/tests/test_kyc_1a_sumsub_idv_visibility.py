import json
import sqlite3
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from sumsub_idv_status import build_sumsub_idv_statuses  # noqa: E402


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE sumsub_applicant_mappings (
            application_id TEXT,
            applicant_id TEXT,
            external_user_id TEXT,
            person_name TEXT,
            person_type TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE webhook_processed_events (
            event_type TEXT,
            applicant_id TEXT,
            external_user_id TEXT,
            review_answer TEXT,
            received_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE audit_log (
            action TEXT,
            target TEXT,
            detail TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE sumsub_unmatched_webhooks (
            applicant_id TEXT,
            external_user_id TEXT,
            event_type TEXT,
            review_answer TEXT,
            status TEXT,
            received_at TEXT
        )
        """
    )
    return conn


def _application(prescreening_data=None):
    return {
        "id": "app-1",
        "ref": "APP-1",
        "client_id": "client-1",
        "prescreening_data": json.dumps(prescreening_data or {}),
    }


def _director(name="Jane Director"):
    return {"id": "dir-1", "full_name": name, "date_of_birth": "1980-01-01"}


def _status(payload, name="Jane Director"):
    matches = [item for item in payload["statuses"] if item["person_name"] == name]
    assert matches, payload
    return matches[0]


def test_missing_mapping_returns_not_started_not_approved():
    conn = _db()

    payload = build_sumsub_idv_statuses(conn, _application(), directors=[_director()])
    item = _status(payload)

    assert item["provider"] == "sumsub"
    assert item["provider_label"] == "Sumsub Identity Verification"
    assert item["provider_scope"] == "individual_kyc_identity_verification"
    assert item["verification_status"] == "not_started"
    assert item["review_answer"] == "unavailable"
    assert item["officer_action_required"] is True
    assert item["raw_provider_payload_exposed"] is False


def test_mapping_only_returns_applicant_created_not_approved():
    conn = _db()
    conn.execute(
        "INSERT INTO sumsub_applicant_mappings VALUES (?,?,?,?,?,?)",
        ("app-1", "sumsub-applicant-123456", "dir-1", "Jane Director", "director", "2026-06-11T10:00:00Z"),
    )

    payload = build_sumsub_idv_statuses(conn, _application(), directors=[_director()])
    item = _status(payload)

    assert item["verification_status"] == "applicant_created"
    assert item["review_answer"] == "pending"
    assert item["evidence_backed"] is False
    assert item["source_of_truth"] == "sumsub_applicant_mappings"
    assert item["applicant_id"] != "sumsub-applicant-123456"


def test_green_webhook_returns_approved_with_evidence():
    conn = _db()
    conn.execute(
        "INSERT INTO sumsub_applicant_mappings VALUES (?,?,?,?,?,?)",
        ("app-1", "sumsub-green-123456", "dir-1", "Jane Director", "director", "2026-06-11T10:00:00Z"),
    )
    conn.execute(
        "INSERT INTO webhook_processed_events VALUES (?,?,?,?,?)",
        ("applicantReviewed", "sumsub-green-123456", "dir-1", "GREEN", "2026-06-11T10:05:00Z"),
    )

    payload = build_sumsub_idv_statuses(conn, _application(), directors=[_director()])
    item = _status(payload)

    assert item["verification_status"] == "approved"
    assert item["review_answer"] == "GREEN"
    assert item["evidence_backed"] is True
    assert item["officer_action_required"] is False
    assert item["source_of_truth"] == "webhook_processed_events"


def test_red_webhook_returns_rejected_with_rejection_labels():
    conn = _db()
    conn.execute(
        "INSERT INTO sumsub_applicant_mappings VALUES (?,?,?,?,?,?)",
        ("app-1", "sumsub-red-123456", "dir-1", "Jane Director", "director", "2026-06-11T10:00:00Z"),
    )
    conn.execute(
        "INSERT INTO webhook_processed_events VALUES (?,?,?,?,?)",
        ("applicantReviewed", "sumsub-red-123456", "dir-1", "RED", "2026-06-11T10:05:00Z"),
    )
    conn.execute(
        "INSERT INTO audit_log VALUES (?,?,?,?)",
        (
            "KYC applicantReviewed: RED",
            "sumsub-red-123456",
            json.dumps({"review_answer": "RED", "rejection_labels": ["FORGERY"]}),
            "2026-06-11T10:05:01Z",
        ),
    )

    payload = build_sumsub_idv_statuses(conn, _application(), directors=[_director()])
    item = _status(payload)

    assert item["verification_status"] == "rejected"
    assert item["review_answer"] == "RED"
    assert item["rejection_labels"] == ["FORGERY"]
    assert "sumsub_idv_rejected" in item["blocking_flags"]


def test_legacy_prescreening_webhook_is_visible_but_marked_legacy_source():
    conn = _db()
    prescreening = {
        "sumsub_applicant_ids": {"dir-1": "legacy-applicant-123456"},
        "screening_report": {
            "sumsub_webhook": {
                "sumsub_applicant_id": "legacy-applicant-123456",
                "external_user_id": "dir-1",
                "review_answer": "GREEN",
                "received_at": "2026-06-11T10:05:00Z",
            }
        },
    }

    payload = build_sumsub_idv_statuses(conn, _application(prescreening), directors=[_director()])
    item = _status(payload)

    assert item["verification_status"] == "approved"
    assert item["source_of_truth"] == "prescreening_data"
    assert item["evidence_backed"] is True
    assert item["raw_provider_payload_exposed"] is False


def test_unmatched_webhook_summary_is_admin_visible_without_raw_payload():
    conn = _db()
    conn.execute(
        "INSERT INTO sumsub_unmatched_webhooks VALUES (?,?,?,?,?,?)",
        ("unmatched-applicant-123456", "unknown-user", "applicantReviewed", "RED", "pending", "2026-06-11T10:05:00Z"),
    )

    payload = build_sumsub_idv_statuses(conn, _application(), directors=[], include_unmatched=True)

    assert payload["unmatched_webhooks"]["count"] == 1
    item = payload["unmatched_webhooks"]["items"][0]
    assert item["verification_status"] == "unmatched"
    assert item["person_type"] == "unknown"
    assert item["raw_provider_payload_exposed"] is False


def test_optional_tables_missing_returns_not_started_not_error():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    payload = build_sumsub_idv_statuses(conn, _application(), directors=[_director()])
    item = _status(payload)

    assert item["verification_status"] == "not_started"
    assert payload["provider_scope"] == "individual_kyc_identity_verification"


def test_application_idv_endpoint_is_registered_and_does_not_call_live_sumsub():
    server = (BACKEND_ROOT / "server.py").read_text()
    assert "/api/applications/([^/]+)/kyc/identity-verifications" in server
    assert "ApplicationIdentityVerificationsHandler" in server
    handler_region = server[
        server.index("class ApplicationIdentityVerificationsHandler"):
        server.index("class ApplicationDetailHandler")
    ]
    assert "build_sumsub_idv_statuses" in handler_region or "_build_application_sumsub_idv_payload" in handler_region
    assert "sumsub_get_applicant_status" not in handler_region
    assert "get_sumsub_client" not in handler_region


def test_backoffice_renders_separate_sumsub_identity_verification_panel():
    html = (REPO_ROOT / "arie-backoffice.html").read_text()
    assert "Individual Identity Verification" in html
    assert "Sumsub Identity Verification" in html
    assert "individual_kyc_identity_verification" in html
    assert "renderSumsubIdvPanel(app) + renderPartySection(app)" in html
    assert "Sumsub KYC Verification" not in html


def test_backoffice_sumsub_labels_do_not_claim_screening_responsibility():
    html = (REPO_ROOT / "arie-backoffice.html").read_text().lower()
    provider = "sum" + "sub"
    prohibited = [
        provider + " aml",
        provider + " sanctions",
        provider + " sanction",
        provider + " watchlist",
        provider + " pep",
        provider + " adverse media",
        provider + " customer screening",
        provider + " company screening",
        provider + " monitoring",
    ]
    for label in prohibited:
        assert label not in html
    assert ("open" + "sanctions") not in html
