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
    assert "showChangeMgmtTab(CHANGE_MGMT_ACTIVE_TAB || 'requests')" in render_region
    assert "CHANGE_MGMT_ACTIVE_TAB = tab || 'requests'" in render_region
    assert html.index("id=\"cm-tab-requests\"") < html.index("id=\"cm-tab-alerts\"")


def test_queue_uses_customer_identity_stage_readiness_and_primary_action_columns():
    html = _html()
    queue_region = _function_region(html, "loadChangeRequests", "viewRequestDetail")

    for header in (
        "Request ID",
        "Customer / Application",
        "Requested Change",
        "Stage",
        "Readiness",
        "Age",
        "Primary Action",
    ):
        assert header in html

    assert "cmCustomerApplicationLabel(req)" in queue_region
    assert "cmStageMeta(req)" in queue_region
    assert "cmRequestReadinessMeta(req)" in queue_region
    assert "Open detail" in queue_region
    assert "application_id||'').substring(0,10)" not in queue_region


def test_queue_and_detail_use_meaningful_readiness_not_generic_blocking_review():
    html = _html()

    assert "Blocking review" not in html
    for label in (
        "Review details",
        "Evidence / Agent 1",
        "Screening / Risk",
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

    assert "Evidence missing / not yet linked" in html
    assert "Agent 1 not available until evidence is linked" in html
    assert "Screening/risk readiness not yet recorded" in html
    assert "Audit reconstruction unavailable for this request" in html
    assert "No review decisions recorded yet" in html


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

    for region in (approve, reject, implement):
        assert "prompt(" not in region
        assert "confirm(" not in region
        assert "openCmDecisionModal" in region

    assert "cm-request-decision-modal" in html
    assert "cm-decision-notes" in html
    assert "submitCmDecisionAction" in html


def test_backend_detail_exposes_read_only_implementation_readiness():
    backend = CM_BACKEND.read_text(encoding="utf-8")

    assert "result[\"implementation\"]" in backend
    assert "implementation_blockers(db, result)" in backend
    assert "\"can_implement\"" in backend
    assert "\"blockers\"" in backend
