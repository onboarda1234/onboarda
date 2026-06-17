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
            timestamp TEXT
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
    conn.execute(
        """
        CREATE TABLE idv_resolutions (
            id TEXT PRIMARY KEY,
            application_id TEXT,
            application_ref TEXT,
            person_id TEXT,
            person_type TEXT,
            person_name TEXT,
            prior_provider_status TEXT,
            prior_review_answer TEXT,
            resolution_status TEXT,
            resolution_outcome TEXT,
            reason_code TEXT,
            evidence_reviewed TEXT,
            rationale TEXT,
            confirmation_text TEXT,
            senior_approver_id TEXT,
            resolved_by TEXT,
            resolved_by_name TEXT,
            resolved_by_role TEXT,
            ip_address TEXT,
            user_agent TEXT,
            created_at TEXT
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


def test_red_webhook_returns_failed_unresolved_with_rejection_labels():
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

    assert item["verification_status"] == "failed"
    assert item["idv_resolution_status"] == "failed"
    assert item["approval_ready"] is False
    assert item["review_answer"] == "RED"
    assert item["rejection_labels"] == ["FORGERY"]
    assert "sumsub_idv_failed" in item["blocking_flags"]


def test_manual_verified_resolution_allows_idv_gate_without_changing_provider_status():
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
        """
        INSERT INTO idv_resolutions
        (id, application_id, application_ref, person_id, person_type, person_name,
         prior_provider_status, prior_review_answer, resolution_status, resolution_outcome,
         reason_code, evidence_reviewed, rationale, confirmation_text, senior_approver_id,
         resolved_by, resolved_by_name, resolved_by_role, ip_address, user_agent, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "res-1",
            "app-1",
            "APP-1",
            "dir-1",
            "director",
            "Jane Director",
            "failed",
            "RED",
            "manual_verified",
            "manual_verification_completed",
            "provider_coverage_limitation",
            json.dumps(["passport", "certified_copy"]),
            "Officer reviewed certified identity evidence.",
            "confirmed",
            "",
            "co-1",
            "Case Officer",
            "co",
            "127.0.0.1",
            "pytest",
            "2026-06-11T10:10:00Z",
        ),
    )

    payload = build_sumsub_idv_statuses(conn, _application(), directors=[_director()])
    item = _status(payload)

    assert item["provider_verification_status"] == "failed"
    assert item["idv_resolution_status"] == "manual_verified"
    assert item["approval_ready"] is True
    assert item["manual_resolution"]["reason_code"] == "provider_coverage_limitation"
    assert payload["gate_summary"]["approval_ready"] is True


def test_unable_to_verify_resolution_remains_approval_blocking():
    conn = _db()
    conn.execute(
        "INSERT INTO idv_resolutions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "res-2",
            "app-1",
            "APP-1",
            "dir-1",
            "director",
            "Jane Director",
            "not_started",
            "unavailable",
            "unable_to_verify",
            "provider_unable_to_verify",
            "mauritius_id_not_supported",
            json.dumps(["national_id"]),
            "Provider cannot verify this document.",
            "confirmed",
            "",
            "co-1",
            "Case Officer",
            "co",
            "127.0.0.1",
            "pytest",
            "2026-06-11T10:10:00Z",
        ),
    )

    payload = build_sumsub_idv_statuses(conn, _application(), directors=[_director()])
    item = _status(payload)

    assert item["idv_resolution_status"] == "unable_to_verify"
    assert item["approval_ready"] is False
    assert payload["gate_summary"]["approval_ready"] is False
    assert "Identity verification unable to verify" in payload["gate_summary"]["blocking_reasons"]


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


def test_audit_lookup_uses_parameterized_like_for_postgres_safety():
    class GuardedConnection:
        def __init__(self, conn):
            self.conn = conn
            self.saw_parameterized_review_like = False

        def execute(self, sql, params=()):
            assert "LIKE 'KYC applicantReviewed:%'" not in sql
            if "action LIKE ?" in sql:
                assert params == ("KYC applicantReviewed:%",)
                self.saw_parameterized_review_like = True
            return self.conn.execute(sql, params)

    conn = _db()
    guarded = GuardedConnection(conn)

    payload = build_sumsub_idv_statuses(guarded, _application(), directors=[_director()])

    assert guarded.saw_parameterized_review_like is True
    assert _status(payload)["verification_status"] == "not_started"


def test_audit_lookup_uses_audit_log_timestamp_column_for_deployed_schema():
    class GuardedConnection:
        def __init__(self, conn):
            self.conn = conn
            self.saw_timestamp_audit_lookup = False

        def execute(self, sql, params=()):
            if "FROM audit_log" in sql:
                assert "SELECT action, target, detail, created_at FROM audit_log" not in sql
                assert "ORDER BY created_at" not in sql
                assert "timestamp AS created_at" in sql
                assert "ORDER BY timestamp DESC" in sql
                self.saw_timestamp_audit_lookup = True
            return self.conn.execute(sql, params)

    conn = _db()
    guarded = GuardedConnection(conn)

    payload = build_sumsub_idv_statuses(guarded, _application(), directors=[_director()])

    assert guarded.saw_timestamp_audit_lookup is True
    assert _status(payload)["verification_status"] == "not_started"


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


def test_backoffice_places_sumsub_identity_verification_under_section_b_compactly():
    html = (REPO_ROOT / "arie-backoffice.html").read_text()
    assert "Individual Identity Verification" in html
    assert "Identity verification provider: Sumsub. Financial-crime screening is handled separately." in html
    assert "renderSumsubIdvPanel(app) + renderPartySection(app)" not in html
    assert "document.getElementById('detail-persons').innerHTML = renderPartySection(app)" in html
    assert "B — Directors & UBO Identity Documents" in html
    assert "if (includeIdv) personBody += renderSumsubIdvPanel(app);" in html
    assert "personBody" in html
    assert "id=\"individual-identity-verification\"" in html
    assert "data-section=\"section-b-identity-verification\"" in html
    assert "idv-compact-table" in html
    assert "idv-compact-row header" in html
    assert "Person</div><div>Role</div><div>IDV status</div><div>Evidence</div><div>Last update</div><div>Action" in html
    assert "Resolve IDV Exception" in html
    assert "I confirm I have reviewed the evidence and accept responsibility for this IDV resolution." in html
    assert "Sumsub KYC Verification" not in html


def test_backoffice_idv_default_rows_hide_operational_fields_inside_details():
    html = (REPO_ROOT / "arie-backoffice.html").read_text()
    panel_start = html.index("function renderSumsubIdvPanel(app)")
    panel_end = html.index("function canResolveIdvException()", panel_start)
    panel = html[panel_start:panel_end]

    row_start = panel.index("'<div class=\"idv-compact-row\"")
    details_start = panel.index("'<details class=\"idv-compact-details\"", row_start)
    default_row = panel[row_start:details_start]
    details = panel[details_start:]

    hidden_by_default = [
        "Provider outcome:",
        "Applicant ID:",
        "Applicant created:",
        "Webhook received:",
        "Evidence basis:",
        "Evidence-backed:",
    ]
    for label in hidden_by_default:
        assert label not in default_row
        assert label in details
    assert "Technical details" in details


def test_backoffice_unmatched_webhook_notice_is_compact_admin_sco_only():
    html = (REPO_ROOT / "arie-backoffice.html").read_text()
    assert "function canViewUnmatchedSumsubWebhookNotice()" in html
    assert "idv-admin-notice" in html
    assert "Admin notice:" in html
    assert "Open reconciliation" in html
    helper = html[html.index("function canViewUnmatchedSumsubWebhookNotice()"):html.index("function renderSumsubIdvPanel(app)")]
    assert "return false;" in helper
    assert "Unmatched Sumsub webhook events:" not in html


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
