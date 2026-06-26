from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"
CM_BACKEND = ROOT / "arie-backend" / "change_management.py"


def _html() -> str:
    return BACKOFFICE_HTML.read_text(encoding="utf-8")


def _function_region(html: str, start_name: str, next_name: str) -> str:
    start = html.index(f"function {start_name}")
    end = html.index(f"function {next_name}", start)
    return html[start:end]


def test_change_requests_is_default_operational_surface():
    html = _html()

    render_region = _function_region(html, "renderChangeMgmt", "populateCMApplicationSelects")
    assert "loadChangeManagementWorkspace();" in render_region
    assert "filter: 'all'" in render_region
    assert "id=\"cm-workspace-dashboard\"" in html
    assert "Unified CM queue" in html
    assert "id=\"cm-work-filter-requests\"" in html
    assert "id=\"cm-work-filter-alerts\"" in html
    assert "id=\"cm-tab-requests\"" not in html
    assert "id=\"cm-tab-alerts\"" not in html


def test_queue_uses_customer_identity_stage_readiness_and_primary_action_columns():
    html = _html()
    queue_region = _function_region(html, "cmRenderWorkQueue", "loadChangeManagementWorkspace")

    for header in (
        "Work item",
        "Customer / Application",
        "Requested change / alert",
        "Stage",
        "Readiness",
        "Age",
        "Primary Action",
    ):
        assert header in html

    assert "cmCustomerApplicationLabel(req)" in html
    assert "cmAlertCustomerApplicationLabel(alert)" in html
    assert "cmNormalizeAlertWorkItem" in html
    assert "cmNormalizeRequestWorkItem" in html
    assert "cmStageMeta(req)" in html
    assert "cmRequestReadinessMeta(req)" in html
    assert "cmWorkItemRowHtml(item)" in queue_region
    assert "application_id||'').substring(0,10)" not in queue_region


def test_dashboard_cards_and_unified_filters_replace_tab_heavy_workspace():
    html = _html()

    for label in (
        "All CM work",
        "Alerts to triage",
        "Pending approval",
        "Ready to implement",
        "Blocked",
        "Converted / closed",
    ):
        assert label in html

    for filter_id in (
        "cm-work-filter-all",
        "cm-work-filter-requests",
        "cm-work-filter-alerts",
        "cm-work-filter-my",
        "cm-work-filter-pending_approval",
        "cm-work-filter-ready_implement",
        "cm-work-filter-blocked",
        "cm-work-filter-closed",
    ):
        assert filter_id in html

    assert "cm-alerts-tab" not in html
    assert "cm-requests-tab" not in html
    assert "cm-stats-tab" not in html


def test_queue_and_detail_use_meaningful_readiness_not_generic_blocking_review():
    html = _html()

    assert "Blocking review" not in html
    assert "Backend gates remain authoritative" not in html
    for label in (
        "Review requested change",
        "Evidence / Agent 1",
        "Screening / Risk",
        "Screening/risk review required",
        "Approval",
        "Implementation",
        "Closed / Audit",
    ):
        assert label in html


def test_request_detail_promotes_old_value_requested_new_value_and_readiness_cards():
    html = _html()
    detail_region = _function_region(html, "viewRequestDetail", "cmAuditTimelinePreview")

    assert "Old value vs requested new value" in detail_region
    assert "Requested new value" in detail_region
    assert "Readiness checklist" in detail_region
    assert "cmReadinessCard('Evidence'" in detail_region
    assert "cmReadinessCard('Agent 1'" in detail_region
    assert "cmReadinessCard('Screening / risk'" in detail_region
    assert "cmReadinessCard('Approval readiness'" in detail_region
    assert "cmReadinessCard('Implementation readiness'" in detail_region


def test_missing_data_states_remain_visible_in_request_detail():
    html = _html()

    assert "Evidence required — add or link evidence" in html
    assert "Agent 1 not available until evidence is linked" in html
    assert "Screening/risk readiness not yet recorded" in html
    assert "Audit reconstruction unavailable for this request" in html
    assert "Review decision history will appear here after approval or rejection" in html


def test_audit_reconstruction_link_calls_existing_endpoint():
    html = _html()

    assert "View audit reconstruction" in html
    assert "viewCmAuditReconstruction" in html
    assert "/api/change-management/requests/' + reqId + '/audit-reconstruction" in html
    assert "Raw reconstruction JSON" in html


def test_approve_reject_implement_use_in_app_modal_not_browser_prompts():
    html = _html()

    approve = _function_region(html, "approveRequest", "rejectRequest")
    reject = _function_region(html, "rejectRequest", "implementRequest")
    implement = _function_region(html, "implementRequest", "showCreateAlertModal")
    close_modal = _function_region(html, "closeCmModal", "closeCmAuditReconstructionModal")
    keydown = _function_region(html, "cmHandleModalKeydown", "closeCmDecisionModal")

    for region in (approve, reject, implement):
        assert "prompt(" not in region
        assert "confirm(" not in region
        assert "openCmDecisionModal" in region

    assert 'id="cm-request-decision-modal" role="dialog" aria-modal="true"' in html
    assert 'id="cm-audit-reconstruction-modal" role="dialog" aria-modal="true"' in html
    assert "cmModalFocusableElements" in html
    assert "CM_MODAL_LAST_FOCUS" in close_modal
    assert "event.key === 'Escape'" in keydown
    assert "event.key !== 'Tab'" in keydown
    assert "cm-decision-notes" in html
    assert "submitCmDecisionAction" in html


def test_unknown_implementation_readiness_is_not_presented_as_green_ready():
    html = _html()

    request_readiness = _function_region(html, "cmRequestReadinessMeta", "cmEvidenceReadiness")
    implementation_readiness = _function_region(html, "cmImplementationReadiness", "cmReadinessCard")

    assert "implementation.can_implement === true" in request_readiness
    assert "Implementation check pending" in request_readiness
    assert "implementation.can_implement === true" in implementation_readiness
    assert "Implementation readiness will be confirmed by system checks" in implementation_readiness
    assert "Available after approval" in implementation_readiness
    assert "Ready for backend validation" not in implementation_readiness


def test_backend_detail_exposes_read_only_implementation_readiness():
    backend = CM_BACKEND.read_text(encoding="utf-8")

    assert "result[\"implementation\"]" in backend
    assert "implementation_blockers(db, result)" in backend
    assert "\"can_implement\"" in backend
    assert "\"blockers\"" in backend
