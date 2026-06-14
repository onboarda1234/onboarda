import json
import inspect


def test_redact_provider_payload_removes_secrets_but_preserves_mesh_refs():
    from screening_complyadvantage.evidence_policy import redact_provider_payload

    payload = {
        "Authorization": "Bearer secret-token",
        "webhook-signature": "signed-payload",
        "case_identifier": "case-redaction",
        "alert_id": "alert-redaction",
        "risk_id": "risk-redaction",
        "profile_id": "profile-redaction",
        "nested": {
            "api_key": "secret-api-key",
            "client_secret": "secret-client",
            "customer_id": "customer-redaction",
            "workflow_id": "workflow-redaction",
        },
        "items": [
            {
                "cookie": "session=secret",
                "provider_reference": "mesh-ref-kept",
            }
        ],
    }

    redacted = redact_provider_payload(payload)

    assert redacted["Authorization"] == "[redacted]"
    assert redacted["webhook-signature"] == "[redacted]"
    assert redacted["nested"]["api_key"] == "[redacted]"
    assert redacted["nested"]["client_secret"] == "[redacted]"
    assert redacted["items"][0]["cookie"] == "[redacted]"
    assert redacted["case_identifier"] == "case-redaction"
    assert redacted["alert_id"] == "alert-redaction"
    assert redacted["risk_id"] == "risk-redaction"
    assert redacted["profile_id"] == "profile-redaction"
    assert redacted["nested"]["customer_id"] == "customer-redaction"
    assert redacted["nested"]["workflow_id"] == "workflow-redaction"
    assert redacted["items"][0]["provider_reference"] == "mesh-ref-kept"


def test_ca_screening_audit_detail_preserves_refs_and_evidence_quality():
    from server import _ca_screening_audit_detail

    detail = json.loads(_ca_screening_audit_detail(
        "ca_result_received",
        {"id": "app_audit_refs", "ref": "ARF-AUDIT-REFS"},
        report={
            "provider": "complyadvantage",
            "screened_at": "2026-06-03T10:00:00Z",
            "total_hits": 1,
            "company_screening": {
                "provider_references": {
                    "case_ids": ["case-audit"],
                    "customer_ids": ["customer-audit"],
                    "workflow_ids": ["workflow-audit"],
                    "alert_ids": ["alert-audit"],
                    "risk_ids": ["risk-audit"],
                    "profile_ids": ["profile-audit"],
                },
                "results": [{
                    "provider_case_identifier": "case-audit",
                    "provider_alert_identifier": "alert-audit",
                    "provider_risk_identifier": "risk-audit",
                    "provider_profile_identifier": "profile-audit",
                    "evidence_quality": "partial",
                }],
            },
            "director_screenings": [],
            "ubo_screenings": [],
            "intermediary_screenings": [],
        },
    ))

    assert detail["provider_event_category"] == "ca_mesh_screening"
    assert detail["provider"] == "complyadvantage"
    assert detail["provider_display_name"] == "ComplyAdvantage Mesh"
    assert detail["ca_event_type"] == "ca_result_received"
    assert detail["provider_references"]["case_ids"] == ["case-audit"]
    assert detail["provider_references"]["customer_ids"] == ["customer-audit"]
    assert detail["provider_references"]["workflow_ids"] == ["workflow-audit"]
    assert detail["provider_references"]["alert_ids"] == ["alert-audit"]
    assert detail["provider_references"]["risk_ids"] == ["risk-audit"]
    assert detail["provider_references"]["profile_ids"] == ["profile-audit"]
    assert detail["evidence_quality"]["overall"] == "partial"


def test_ca_screening_review_event_type_maps_officer_dispositions():
    from server import _ca_screening_review_event_type

    assert _ca_screening_review_event_type("false_positive_cleared") == "ca_hit_cleared_false_positive"
    assert _ca_screening_review_event_type("confirmed_match") == "ca_hit_confirmed_true_match"
    assert _ca_screening_review_event_type("escalated_to_edd") == "ca_hit_escalated"
    assert _ca_screening_review_event_type("needs_more_information") == "ca_follow_up_requested"
    assert _ca_screening_review_event_type("other") == "ca_hit_reviewed"


def test_application_audit_log_supports_ca_mesh_category_filter():
    from server import ApplicationAuditLogHandler

    source = inspect.getsource(ApplicationAuditLogHandler.get)
    assert "ca_mesh" in source
    assert "ca_screening" in source
    assert "ca_mesh_screening" in source
    assert "complyadvantage mesh" in source
    assert "category_params" in source
    assert "LIKE '%ca_screening%'" not in source
    assert "LIKE '%complyadvantage%'" not in source
