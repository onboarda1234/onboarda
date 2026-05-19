import os
import re


BACKOFFICE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "arie-backoffice.html",
)


def _read_backoffice():
    with open(BACKOFFICE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def test_application_detail_tabs_include_lifecycle_in_required_order():
    html = _read_backoffice()
    tabs_start = html.index("<!-- Content tabs -->")
    tabs_end = html.index("<!-- Tab: Overview (full width) -->", tabs_start)
    tabs_html = html[tabs_start:tabs_end]
    tab_ids = re.findall(r'id="tab-([^"]+)"', tabs_html)
    assert tab_ids[:6] == [
        "overview",
        "kyc-docs",
        "screening",
        "supervisor",
        "lifecycle",
        "activity",
    ]


def test_lifecycle_tab_shell_sections_exist():
    html = _read_backoffice()
    assert 'id="detail-tab-lifecycle"' in html
    for title in (
        "Review Setup Summary",
        "Active Work",
        "Current Review Workspace",
        "Completion Gate",
        "Required Evidence",
        "Cross-module Links",
        "Memo Status",
        "History",
    ):
        assert title in html


def test_lifecycle_tab_loader_uses_existing_projection_endpoints():
    html = _read_backoffice()
    assert "async function loadLifecycleDetailTab(force)" in html
    assert "/lifecycle/applications/" in html
    assert "/summary" in html
    assert "/monitoring/reviews/" in html
    assert "required_items" in html
    assert "Refresh Lifecycle" in html


def test_switch_detail_tab_supports_lifecycle_without_regressing_activity_loads():
    html = _read_backoffice()
    start = html.index("function switchDetailTab(tab)")
    section = html[start:start + 1200]
    assert "'lifecycle'" in section
    assert "loadLifecycleDetailTab()" in section
    assert "loadDecisionRecords()" in section
    assert "loadActivityLog()" in section
    assert "loadNotes()" in section


def test_lifecycle_workspace_adds_attestation_controls_and_system_panels():
    html = _read_backoffice()
    assert "Material Change Attestation" in html
    assert "Risk Rating Change Attestation" in html
    assert "Save material change attestation" in html
    assert "Record review-level risk change" in html
    assert "KYC Documents" in html
    assert "Screening" in html
    assert "Alerts / EDD / Changes" in html
    assert "/material-change-attestation" in html
    assert "/risk-change" in html


def test_lifecycle_required_evidence_renders_single_canonical_surface():
    html = _read_backoffice()
    assert "renderLifecycleRequiredEvidenceCard" in html
    assert "Canonical evidence requirements linked to the existing KYC document repository." in html
    assert "Evidence linking and uploads arrive in PR 5." not in html
    assert "Projected requirement snapshot only. PR 5 adds evidence-link controls." not in html
    assert "Required Evidence Snapshot" not in html


def test_lifecycle_required_evidence_wires_repository_link_upload_and_custom_requirement_controls():
    html = _read_backoffice()
    assert "async function linkLifecycleEvidenceDocument(reviewId, requirementId)" in html
    assert "async function submitLifecycleEvidenceUpload(reviewId)" in html
    assert "async function addLifecycleCustomEvidenceRequirement(reviewId)" in html
    assert "/evidence-links" in html
    assert "/required-items/custom" in html
    assert "Upload and link evidence" in html
    assert "Add custom requirement" in html
    assert "does not create a separate periodic-review document store" in html


def test_lifecycle_workspace_enforces_active_work_current_review_exclusivity():
    html = _read_backoffice()
    start = html.index("function renderLifecycleDetailTab(context)")
    end = html.index("async function loadLifecycleDetailTab(force)", start)
    section = html[start:end]
    assert "var currentReviewActive = !!activeReview;" in section
    assert "var activeWorkItems = currentReviewActive ?" in section
    assert "(currentReviewActive ? lifecycleDetailCard('Current Review Workspace'" in section
    assert ": lifecycleDetailCard('Active Work'" in section


def test_lifecycle_workspace_save_helpers_refresh_canonical_projection():
    html = _read_backoffice()
    assert "async function saveLifecycleMaterialChange(reviewId)" in html
    assert "async function saveLifecycleRiskChange(reviewId)" in html
    assert "await refreshLifecycleWorkspaceTab();" in html
    assert "window._detailLifecycleTabCache = null;" in html


def test_lifecycle_workspace_adds_owner_workflow_deep_links_without_duplicate_workflows():
    html = _read_backoffice()
    assert "function lifecycleOpenChangeManagementRequests()" in html
    assert "function lifecycleOpenOrCreateChangeRequest(reviewId)" in html
    assert "Open monitoring alert #" in html
    assert "Open linked EDD #" in html
    assert "Create / link EDD case" in html
    assert "Open Change Management" in html
    assert "Open / create change request" in html
    assert "Formal approval and implementation of material changes remain canonical in Change Management." in html
    assert "Owner-workflow actions stay in Monitoring, EDD, and Change Management" in html


def test_lifecycle_link_rows_surface_source_module_object_id_status_and_next_action():
    html = _read_backoffice()
    start = html.index("function lifecycleDetailItemRow(item)")
    end = html.index("var LIFECYCLE_MATERIAL_CHANGE_OPTIONS", start)
    section = html[start:end]
    assert "lifecycleSourceModuleLabel(item)" in section
    assert "Linked object:" in section
    assert "Next action:" in section
    assert "Open " in section
    assert "Owner:" in section


def test_lifecycle_edge_chips_are_clickable_owner_workflow_deep_links():
    html = _read_backoffice()
    start = html.index("function lifecycleDetailEdgeChip(edge)")
    end = html.index("function lifecycleDetailItemRow(item)", start)
    section = html[start:end]
    assert "lifecycleOpenItemAction(obj)" in section
    assert "event.stopPropagation();" in section
    assert "btn btn-outline btn-sm" in section


def test_lifecycle_memo_gate_adds_officer_rationale_outcome_and_completion_action():
    html = _read_backoffice()
    helper_start = html.index("window._lifecycleMemoDrafts = window._lifecycleMemoDrafts || {};")
    helper_end = html.index("async function saveLifecycleMaterialChange(reviewId)", helper_start)
    helper_section = html[helper_start:helper_end]
    memo_start = html.index("var memoBody = activeReview")
    memo_end = html.index("var historyItems = historical.filter", memo_start)
    memo_section = html[memo_start:memo_end]
    assert "window._lifecycleMemoDrafts = window._lifecycleMemoDrafts || {};" in helper_section
    assert "async function saveLifecycleOfficerRationale(reviewId)" in helper_section
    assert "async function completeLifecycleReviewAndGenerateMemo(reviewId)" in helper_section
    assert "Officer rationale" in memo_section
    assert "Save rationale" in memo_section
    assert "Continue — No Change" in memo_section
    assert "Continue — Enhanced Monitoring" in memo_section
    assert "Escalate to EDD" in memo_section
    assert "Complete review & generate memo" in memo_section
    assert "Complete review & regenerate memo" in memo_section
    assert "Periodic review memo generation remains deterministic" in memo_section
    assert "/officer-rationale" in helper_section
    assert "/complete" in helper_section
    assert "Recommend Exit" not in memo_section
    assert "Exit Recommended" not in memo_section
