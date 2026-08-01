import inspect
import json
from pathlib import Path

import pytest

import monitoring_alert_linkage as linkage
import server
from scripts.qa import monitoring_canonical_linkage_audit as audit


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent


def _function_region(source, start, end):
    return source[source.index(start) : source.index(end, source.index(start))]


def test_linkage_handler_is_authenticated_get_only_and_route_is_numeric_first():
    assert "get" in server.MonitoringAlertLinkageHandler.__dict__
    for method in ("post", "put", "patch", "delete"):
        assert method not in server.MonitoringAlertLinkageHandler.__dict__
    handler_source = inspect.getsource(server.MonitoringAlertLinkageHandler)
    assert 'roles=["admin", "sco", "co"]' in handler_source
    assert "resolve_alert_linkage" in handler_source
    assert "source_reference" not in handler_source
    server_source = (BACKEND_ROOT / "server.py").read_text(encoding="utf-8")
    numeric = server_source.index(
        '(r"/api/monitoring/alerts/([0-9]+)/linkage", MonitoringAlertLinkageHandler)'
    )
    generic = server_source.index(
        '(r"/api/monitoring/alerts/([^/]+)", MonitoringAlertDetailHandler)'
    )
    assert numeric < generic


def test_linkage_contract_has_no_mutation_surface_or_future_owner_type():
    source = (BACKEND_ROOT / "monitoring_alert_linkage.py").read_text(
        encoding="utf-8"
    )
    assert linkage.DOCUMENT_EXPIRY_ALERT_TYPES == {
        "document_expired",
        "document_expiring_soon",
        "document_expiry_missing",
    }
    assert linkage.SCREENING_HIT_ALERT_TYPES == {
        "sanctions",
        "watchlist",
        "pep",
        "media",
        "adverse_media",
    }
    for statement in (
        "UPDATE monitoring_alerts",
        "INSERT INTO monitoring_alerts",
        "DELETE FROM monitoring_alerts",
        "UPDATE documents",
        "UPDATE screening_reviews",
    ):
        assert statement not in source
    for future_owner in ("edd_case_id", "periodic_review_id", "change_request_id"):
        assert future_owner not in source


def test_backoffice_adds_owner_card_without_removing_protected_decision_controls():
    html = (REPO_ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    kind_region = _function_region(
        html,
        "function monitoringCanonicalLinkageKind",
        "function monitoringCanonicalLinkage(",
    )
    assert ".toLowerCase(" not in kind_region
    for alert_type in sorted(
        linkage.DOCUMENT_EXPIRY_ALERT_TYPES | linkage.SCREENING_HIT_ALERT_TYPES
    ):
        assert f"'{alert_type}'" in html
    decision_region = _function_region(
        html,
        "function renderMonitoringDecisionSection",
        "function renderMonitoringPendingReviewSection",
    )
    legacy_document = decision_region.index("renderMonitoringDocumentDecisionSection")
    generic_outcome = decision_region.index("monitoringOutcomeOptions")
    assert legacy_document < generic_outcome
    assert "monitoringCanonicalLinkageKind(alert)" not in decision_region
    assert "renderMonitoringCanonicalOwnerSection(alert)" not in decision_region

    detail_region = _function_region(
        html,
        "function renderMonitoringAlertDetailView",
        "async function openMonitoringAlertDetail",
    )
    assert detail_region.count("renderMonitoringCanonicalOwnerSection(alert)") == 2
    assert detail_region.count("renderMonitoringDecisionSection(alert)") == 2
    assert (
        detail_region.index("renderMonitoringCanonicalOwnerSection(alert)")
        < detail_region.index("renderMonitoringDecisionSection(alert)")
    )


def test_backoffice_navigation_uses_stable_ids_without_name_fallback():
    html = (REPO_ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    open_region = _function_region(
        html,
        "async function openMonitoringCanonicalOwner",
        "function renderMonitoringCanonicalOwnerSection",
    )
    assert "openScreeningReview(" not in open_region
    assert "nav.document_id" in open_region
    assert "data-document-id" in open_region
    assert "nav.subject_id" in open_region
    assert "nav.subject_person_key" in open_region
    assert "fetchMonitoringCanonicalScreeningContext" in open_region
    assert "screeningContext.provider" in open_region
    assert "screeningContext.provider_reference" in open_region
    assert "context_proven: true" in open_region
    assert "matches.length !== 1" in open_region
    assert "querySelectorAll('[data-document-id]')" in open_region
    assert "data-document-id=\"" in html
    assert "data-document-version=\"" in html
    assert "data-canonical-screening-context" in html
    assert "doc.version || 1" not in html


def test_screening_owner_handoff_proves_exact_case_with_read_only_get():
    html = (REPO_ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    proof_region = _function_region(
        html,
        "function monitoringCanonicalScreeningContextRows",
        "async function openMonitoringCanonicalOwner",
    )
    assert "row.application_ref" in proof_region
    assert "row.subject_type" in proof_region
    assert "row.subject_name" in proof_region
    assert "summary.providers" in proof_region
    assert "nav.provider" in proof_region
    assert "nav.provider_reference" in proof_region
    assert "summary.provider_case_ids" in proof_region
    assert "summary.provider_alert_ids" in proof_region
    assert "exact.length !== 1" in proof_region
    assert "boApiCall('GET', '/screening/queue?" in proof_region
    assert "exact_application_ref=" in proof_region
    assert "'application_ref=' +" not in proof_region
    for mutation in ("boApiCall('POST'", "boApiCall('PUT'", "boApiCall('PATCH'", "boApiCall('DELETE'"):
        assert mutation not in proof_region

    panel_region = _function_region(
        html,
        "function renderScreeningReviewPanel",
        "function screeningQueueRowHasFullEvidence",
    )
    assert "monitoringCanonicalScreeningContextRows" in panel_region
    assert "canonicalContextRows.length === 1" in panel_region
    assert "data-canonical-screening-context-error" in panel_region


def test_canonical_owner_handoffs_suppress_transitive_profile_hydration_write():
    html = (REPO_ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    open_region = _function_region(
        html,
        "async function openMonitoringCanonicalOwner",
        "function renderMonitoringCanonicalOwnerSection",
    )
    detail_region = _function_region(
        html,
        "function renderAuthoritativeAppDetail",
        "async function openAppDetail",
    )
    panel_region = _function_region(
        html,
        "function renderScreeningReviewPanel",
        "function screeningQueueRowHasFullEvidence",
    )

    assert open_region.count("canonicalLinkageHandoff: true") == 2
    assert "currentCanonicalLinkageReadOnlyNavigation = options.canonicalLinkageHandoff === true" in detail_region
    hydration_call = panel_region.index(
        "ensureSubjectProfileHydration(app, selectedSubject)"
    )
    read_only_guard = panel_region.rfind(
        "if (!currentCanonicalLinkageReadOnlyNavigation)",
        0,
        hydration_call,
    )
    assert read_only_guard >= 0
    assert "boApiCall('POST', '/screening/hydrate-profiles'" in html


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE monitoring_alerts SET status='resolved'",
        "DELETE FROM monitoring_alerts",
        "INSERT INTO monitoring_alerts(id) VALUES (1)",
        "COMMIT",
        "WITH changed AS (UPDATE monitoring_alerts SET status='open' RETURNING *) SELECT * FROM changed",
    ],
)
def test_audit_sql_guard_rejects_mutation(sql):
    with pytest.raises(ValueError):
        audit.assert_select_only(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM monitoring_alerts",
        "SHOW transaction_read_only",
        "WITH rows AS (SELECT id FROM monitoring_alerts) SELECT * FROM rows",
    ],
)
def test_audit_sql_guard_accepts_read_only_statements(sql):
    audit.assert_select_only(sql)


def test_audit_evidence_shape_is_sanitised_and_has_no_apply_path(tmp_path):
    plan = {
        "contract_version": linkage.CONTRACT_VERSION,
        "generated_at": "2026-08-01T00:00:00+00:00",
        "read_only": True,
        "apply_supported": False,
        "counts": {
            "total_alerts": 1,
            "linked_correctly": 0,
            "manual_review_required": 1,
            "duplicate_linkage_groups": 0,
        },
        "duplicate_groups": [],
        "rows": [
            {
                "alert_id": 1,
                "stored_alert_type": "media",
                "scope_status": "supported",
                "linkage_classification": "missing_linkage",
                "review_disposition": "manual_review_required",
                "reasons": ["missing_stable_screening_subject"],
                "duplicate_linkage": False,
                "data_change_required": False,
                "proposed_linkage": None,
            }
        ],
        "fingerprint": "a" * 64,
    }
    snapshot = {
        "audit_id": audit.AUDIT_ID,
        "contract_version": linkage.CONTRACT_VERSION,
        "captured_at": "2026-08-01T00:00:00+00:00",
        "environment": "staging",
        "deployed_sha": "b" * 40,
        "database_safety": {
            "transaction_read_only": "on",
            "transaction_isolation": "repeatable read",
            "completion": "ROLLBACK",
            "commit_path": False,
            "source_fingerprints_stable": True,
        },
        "feature_governance": {
            "features": {name: "OFF" for name in audit.MONITORING_FEATURE_FLAGS},
            "all_monitoring_flags_off": True,
            "activation_controls": False,
            "evaluation_source": "collector_environment_with_staging_defaults",
            "deployed_runtime_observed": False,
        },
        "source_fingerprints": [],
        "plan": plan,
        "mutation_gate_required": False,
        "no_monitoring_or_downstream_data_modified": True,
    }
    paths = audit.write_artifacts(snapshot, tmp_path)
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    parsed = json.loads((tmp_path / "canonical_linkage_inventory.json").read_text())
    assert parsed["plan"]["apply_supported"] is False
    assert parsed["no_monitoring_or_downstream_data_modified"] is True
    assert parsed["feature_governance"]["deployed_runtime_observed"] is False
    assert (
        parsed["feature_governance"]["evaluation_source"]
        == "collector_environment_with_staging_defaults"
    )
    assert "not deployed-runtime evidence" in (
        tmp_path / "LINKAGE_AUDIT_REPORT.md"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "company_name",
        "subject_name",
        "officer_notes",
        "source_reference",
        "normalized_report_json",
        "DATABASE_URL",
    ):
        assert forbidden not in combined


def test_all_four_governed_flags_are_required_off(monkeypatch):
    for flag in audit.MONITORING_FEATURE_FLAGS:
        monkeypatch.delenv(flag, raising=False)
    state = audit._governed_flags("staging")
    assert state["all_monitoring_flags_off"] is True
    assert set(state["features"]) == set(audit.MONITORING_FEATURE_FLAGS)
    monkeypatch.setenv("ENABLE_MONITORING_SCREENING_CHANGE", "true")
    with pytest.raises(RuntimeError):
        audit._governed_flags("staging")
