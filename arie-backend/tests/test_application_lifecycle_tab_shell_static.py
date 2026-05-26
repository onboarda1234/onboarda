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


def test_lifecycle_workspace_surfaces_agent_signals_as_decision_support_only():
    html = _read_backoffice()
    assert "function lifecycleAgentSignalsPanel(summary)" in html
    assert "function lifecycleAgentSignalRow(signal)" in html
    assert "Agent 6/7/8/10 decision-support signals" in html
    assert "Source:" in html
    assert "Confidence " in html
    assert "Linked object:" in html
    assert "Destination:" in html
    assert "recommended owner module" in html
    assert "They do not write officer-owned review fields." in html


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


def test_ongoing_monitoring_review_surface_is_signal_only_launchpad():
    html = _read_backoffice()
    start = html.index("<!-- Review Signals Tab: signal-only launchpad.")
    end = html.index("<!-- Agents Tab -->", start)
    section = html[start:end]
    assert "Periodic Review Signals" in section
    assert "Signal-only portfolio view" in section
    assert "Open Lifecycle Queue" in section
    assert "monitoring-review-due-count" in section
    assert "monitoring-review-overdue-count" in section
    assert "monitoring-review-blocked-count" in section
    assert "Schedule Due Reviews" not in section
    assert "schedulePeriodicReviews" not in section
    assert "Complete Review" not in section


def test_ongoing_monitoring_review_rows_open_application_lifecycle_not_review_editor():
    html = _read_backoffice()
    start = html.index("function renderPeriodicReviews()")
    end = html.index("// \u2500\u2500 Monitoring-stage AI agent catalog", start)
    section = html[start:end]
    assert "openMonitoringReviewLifecycle(review.ref)" in section
    assert "Open Lifecycle" in section
    assert "openPeriodicReview(review.ref)" not in section
    assert "openPeriodicReview(\\'" not in section
    assert "function openMonitoringReviewLifecycle(ref)" in html
    assert "openAppDetail(ref, { initialTab: 'lifecycle' })" in html


def test_ongoing_monitoring_keeps_alerts_and_agents_tabs():
    html = _read_backoffice()
    start = html.index('<div class="view" id="view-monitoring">')
    end = html.index("<!-- Unified operator queue", start)
    section = html[start:end]
    assert "Monitoring Alerts" in section
    assert "Review Signals" in section
    assert "Monitoring Agents" in section
    assert 'id="monitoring-alerts-body"' in section
    assert 'id="agents-status-list"' in section


def test_legacy_periodic_review_modal_is_read_only_not_completion_surface():
    html = _read_backoffice()
    assert 'id="review-modal-decide-btn"' not in html
    assert "function showReviewDecisionForm()" not in html
    assert "async function submitReviewDecision()" not in html
    start = html.index("function renderPrcReviewDetailSection(detail)")
    end = html.index("async function refreshOpenPeriodicReview()", start)
    section = html[start:end]
    assert "var canMutate = false;" in section
    assert "Read-only projection" in section
    assert "Periodic review work is completed in the Application Lifecycle tab" in section


def test_lifecycle_queue_rows_are_launchpad_deep_links_to_application_lifecycle():
    html = _read_backoffice()
    start = html.index("function renderLifecycleRows(items, include)")
    end = html.index("// AC5: Application detail surface", start)
    section = html[start:end]
    helper_start = html.index("function lifecycleQueueTargetAttributes(item)")
    helper_end = html.index("function lifecycleQueueTargetFromElement(el)", helper_start)
    helper_section = html[helper_start:helper_end]
    assert "class=\"lifecycle-queue-row\"" in section
    assert "lifecycleQueueTargetAttributes(it)" in section
    assert "data-application-id" in helper_section
    assert "openLifecycleQueueItemFromElement(this)" in section
    assert "Open Lifecycle" in section
    assert "Complete review" not in section
    assert "/complete" not in section


def test_lifecycle_queue_chips_open_application_lifecycle_not_old_modals():
    html = _read_backoffice()
    start = html.index("function lifecycleQueueChipHtml(item, label, kind, id)")
    end = html.index("function lifecycleQuarantineChips", start)
    section = html[start:end]
    assert "openLifecycleQueueItemFromElement(this)" in section
    assert "Open Application Lifecycle item" in section
    assert "prcChipHtml" not in section
    assert "openReviewDetailById" not in section
    assert "openEDDDetail" not in section


def test_lifecycle_queue_deep_link_focuses_exact_lifecycle_item():
    html = _read_backoffice()
    helper_start = html.index("function lifecycleQueueItemDomId(type, id)")
    helper_end = html.index("function lifecycleQuarantineChips", helper_start)
    helper_section = html[helper_start:helper_end]
    detail_start = html.index("function lifecycleDetailItemRow(item)")
    detail_end = html.index("var LIFECYCLE_MATERIAL_CHANGE_OPTIONS", detail_start)
    detail_section = html[detail_start:detail_end]
    current_start = html.index("function lifecycleCurrentReviewWorkspaceBody")
    current_end = html.index("function toggleLifecycleMaterialChangeCategories", current_start)
    current_section = html[current_start:current_end]
    assert "window._lifecycleDeepLinkTarget" in html
    assert "focusLifecycleDeepLinkTarget" in helper_section
    assert "scrollIntoView" in helper_section
    assert "lifecycleQueueItemDomId(type, itemId)" in detail_section
    assert "data-lifecycle-item-type" in detail_section
    assert "lifecycleQueueItemDomId('review', reviewId)" in current_section
    assert "data-lifecycle-item-type=\"review\"" in current_section


def test_lifecycle_queue_preserves_active_historical_and_legacy_buckets():
    html = _read_backoffice()
    start = html.index("function switchLifecycleTab(include)")
    end = html.index("async function loadLifecycleQueue", start)
    section = html[start:end]
    assert "active" in section
    assert "historical" in section
    assert "legacy_unmapped" in section
    assert "include=active" in html
    assert "Legacy (unmapped)" in html
