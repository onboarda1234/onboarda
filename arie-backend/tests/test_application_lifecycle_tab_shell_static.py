import os
import re


BACKOFFICE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "arie-backoffice.html",
)


def _read_backoffice():
    with open(BACKOFFICE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _function_region(html, start_name, next_name):
    start = html.index(f"function {start_name}")
    end = html.index(f"function {next_name}", start)
    return html[start:end]


def test_application_detail_tabs_include_periodic_reviews_and_alerts_in_required_order():
    html = _read_backoffice()
    tabs_start = html.index("<!-- Content tabs -->")
    tabs_end = html.index("<!-- Tab: Overview (full width) -->", tabs_start)
    tabs_html = html[tabs_start:tabs_end]
    tab_ids = re.findall(r'id="tab-([^"]+)"', tabs_html)
    assert tab_ids[:7] == [
        "overview",
        "kyc-docs",
        "screening",
        "supervisor",
        "lifecycle",
        "alerts",
        "activity",
    ]
    assert ">Periodic Reviews</button>" in tabs_html
    assert ">Alerts</button>" in tabs_html


def test_application_review_action_bar_and_overview_do_not_duplicate_notes_or_lifecycle_work():
    html = _read_backoffice()
    topbar_start = html.index("<!-- Top bar: back button + action buttons (horizontal) -->")
    topbar_end = html.index('<div id="detail-case-command-centre">', topbar_start)
    topbar_html = html[topbar_start:topbar_end]

    assert "showView('applications')" in topbar_html
    assert 'id="btn-approve"' in topbar_html
    assert 'id="btn-reject"' in topbar_html
    assert 'id="btn-rmi"' in topbar_html
    assert 'openOfficerCorrectionModal()' not in topbar_html
    assert 'id="btn-override"' in topbar_html
    assert "escalateCase()" in topbar_html
    assert 'id="btn-reassign"' in topbar_html
    assert 'id="internal-note"' not in topbar_html
    assert 'placeholder="Add note..."' not in topbar_html
    assert "addNote()" not in topbar_html

    overview_start = html.index('id="detail-tab-overview"')
    overview_end = html.index('id="detail-tab-kyc-docs"', overview_start)
    overview_html = html[overview_start:overview_end]
    assert 'id="detail-lifecycle-summary"' in overview_html
    assert "Related lifecycle items" not in html
    assert "Related monitoring alert" not in html
    assert "Review lifecycle" not in html
    assert "overview-compact-lifecycle" not in html

    renderer_start = html.index("function renderLifecycleApplicationSummary(resp)")
    renderer_end = html.index("function periodicReviewBaselineLegacyOptions", renderer_start)
    renderer_html = html[renderer_start:renderer_end]
    assert "renderCaseCommandCentre(window._currentDetailApp)" in renderer_html
    assert "container.innerHTML = '';" in renderer_html
    assert "lifecycleObjectLabel(item)" not in renderer_html


def test_lifecycle_tab_shell_sections_match_prs4_workspace():
    html = _read_backoffice()
    assert 'id="detail-tab-lifecycle"' in html
    for title in (
        "Review Overview",
        "Active Blockers / Readiness",
        "Client Attestation Summary",
        "Documents & Evidence",
        "Monitoring Alerts Considered In This Review",
        "Periodic Review Decision",
        "Review Context",
        "Review History",
    ):
        assert title in html
    assert "Officer Findings Draft" not in html
    assert "Save draft findings" not in html
    assert "Future Actions" not in html
    assert "Completion Gate" not in html
    assert "Periodic Reviews owns the review cockpit." not in html
    assert "Review Setup Summary" not in html


def test_lifecycle_tab_loader_uses_existing_projection_endpoints():
    html = _read_backoffice()
    assert "async function loadLifecycleDetailTab(force)" in html
    assert "/lifecycle/applications/" in html
    assert "/summary" in html
    assert "/monitoring/reviews/" in html
    assert "Refresh Periodic Reviews" in html


def test_switch_detail_tab_supports_lifecycle_alerts_and_activity_without_regressions():
    html = _read_backoffice()
    start = html.index("function switchDetailTab(tab)")
    section = html[start:start + 1200]
    assert "'lifecycle'" in section
    assert "'alerts'" in section
    assert "loadLifecycleDetailTab()" in section
    assert "loadApplicationAlertsDetailTab()" in section
    assert "loadDecisionRecords()" in section
    assert "loadActivityLog()" in section
    assert "loadNotes()" in section


def test_alerts_tab_shell_uses_existing_application_monitoring_alert_data():
    html = _read_backoffice()
    assert 'id="detail-tab-alerts"' in html
    assert "function renderApplicationAlertsDetailTab(context)" in html
    assert "async function loadApplicationAlertsDetailTab(force)" in html
    assert "fetchLifecycleApplicationSummary(app.id" in html
    assert "monitoringAlerts: detail.monitoring_alerts || []" in html
    assert "No active alerts linked to this application." in html
    assert "showView('monitoring')" in html
    assert "Active EDD / Lifecycle Work" in html
    assert "No active non-review EDD or lifecycle investigation work is linked to this application." in html


def test_alerts_tab_surfaces_non_review_edd_from_lifecycle_summary():
    html = _read_backoffice()
    start = html.index("function renderApplicationAlertsDetailTab(context)")
    end = html.index("async function loadApplicationAlertsDetailTab(force)", start)
    section = html[start:end]
    assert "activeLifecycleItems" in section
    assert "item.type === 'edd' && !item.linked_periodic_review_id" in section
    assert "activeEddItems.map(lifecycleDetailItemRow)" in section
    assert "Active EDD / Lifecycle Work" in section
    assert "Periodic-review-linked work remains in Periodic Reviews." in section


def test_alerts_tab_owner_workflow_buttons_use_safe_data_handlers():
    html = _read_backoffice()
    start = html.index("function renderApplicationAlertsDetailTab(context)")
    end = html.index("async function loadApplicationAlertsDetailTab(force)", start)
    section = html[start:end]

    assert "ownerWorkflowButtonHtml('Open Monitoring Alerts', 'monitoring-alerts-application'" in section
    assert 'onclick="openMonitoringAlertsForApplication(' not in section
    assert 'onclick=openMonitoringAlertsForApplication(' not in html
    assert 'data-owner-workflow-action="' in html
    assert "monitoring-alerts-application" in html
    assert "function handleOwnerWorkflowButtonClick(event)" in html
    assert "openMonitoringAlertsForApplication(appId, appRef, alertId)" in html


def test_lifecycle_detail_item_rows_do_not_render_fragile_edd_inline_handlers():
    html = _read_backoffice()
    start = html.index("function lifecycleDetailItemRow(item)")
    end = html.index("var LIFECYCLE_MATERIAL_CHANGE_OPTIONS", start)
    section = html[start:end]

    assert "lifecycleOwnerWorkflowButtonForItem(item, 'Open ' + sourceModule)" in section
    assert "openEDDCaseFromApplication(" not in section
    assert "event.stopPropagation();" not in section
    assert "edd-case" in html
    assert "openEDDCaseFromApplication(caseId, appId, appRef)" in html
    assert "Investigation case context is unavailable" in html
    assert "openEDDCaseFromApplication(254," not in html


def test_periodic_reviews_tab_does_not_promote_non_review_work_without_active_review():
    html = _read_backoffice()
    start = html.index("function renderLifecycleDetailTab(context)")
    end = html.index("async function loadLifecycleDetailTab(force)", start)
    section = html[start:end]
    assert "No Active Periodic Review" in section
    assert "Periodic Reviews shows the review workflow, review evidence, readiness, decision, risk reassessment, and review history only." in section
    assert "Active non-review work exists:" in section
    assert "Open Alerts tab" in section
    assert "Open Monitoring Alerts" in section
    assert "Open EDD queue" in section
    assert "Active reviews" in section
    assert "Related Lifecycle Work" not in section
    assert "activeWorkItems.map(lifecycleDetailItemRow)" not in section


def test_lifecycle_workspace_renders_prs4_read_only_attestation_documents_and_decision_helpers():
    html = _read_backoffice()
    assert "function renderLifecycleClientAttestationPanel(reviewDetail, reviewProjection)" in html
    assert "function renderLifecyclePeriodicReviewDocumentRequests(reviewDetail, requiredItems, evidenceLinks, documents)" in html
    assert "function renderPeriodicReviewWorkspaceReadiness(reviewDetail)" in html
    assert "function renderPeriodicReviewWorkspaceMonitoring(reviewDetail)" in html
    assert "function renderPeriodicReviewWorkspaceDecision(reviewDetail)" in html
    assert "async function completePeriodicReviewDecision(reviewId)" in html
    assert "periodic-review-decision-complete-btn-" in html
    assert "/complete" in html
    assert "renderPeriodicReviewWorkspaceFindingsDraft" not in html
    assert "savePeriodicReviewWorkspaceFindings" not in html
    assert "Officer Findings Draft" not in html
    assert "Save draft findings" not in html
    assert "Complete periodic review" in html
    assert "Final outcome controls arrive in PRS-5." not in html


def test_periodic_review_documents_summary_surfaces_conditional_and_required_item_evidence():
    html = _read_backoffice()
    section = _function_region(html, "periodicReviewKycFallbackAnchor", "renderLifecycleClientAttestationPanel")
    detail_start = html.index("function renderLifecycleDetailTab(context)")
    detail_end = html.index("async function loadLifecycleDetailTab(force)", detail_start)
    detail_section = html[detail_start:detail_end]

    assert "periodic_review_document_requests" in section
    assert "required_items" in section
    assert "evidence_links" in section
    assert 'data-prs-doc1-row="conditional-request"' in section
    assert 'data-prs-doc1-row="required-item"' in section
    assert "Conditional PR document requests" in section
    assert "Required-item evidence blockers" in section
    assert "Evidence required:" in section
    assert "Blocks completion:" in section
    assert "Missing evidence" in section
    assert "periodicReviewRequiredItemId(item)" in section
    assert (
        "renderLifecyclePeriodicReviewDocumentRequests(activeReview, activeReview && activeReview.required_items, "
        "activeReview && activeReview.evidence_links, currentApp && currentApp._documents)"
    ) in detail_section


def test_periodic_review_documents_summary_deep_links_to_kyc_documents_without_shell_change():
    html = _read_backoffice()
    section = _function_region(html, "periodicReviewKycFallbackAnchor", "renderLifecycleClientAttestationPanel")
    enhanced_section = _function_region(html, "renderEnhancedEvidenceDocumentsGroupHtml", "renderUnifiedEnhancedEvidenceDocuments")

    assert "function openPeriodicReviewKycDocuments(anchorId, contextLabel)" in section
    assert "activateCaseCommandTarget('kyc-docs', targetId)" in section
    assert 'data-prs-doc1-kyc-deeplink="true"' in section
    assert "Resolve in KYC & Documents" in section
    assert "Review in KYC & Documents" in section
    assert "View in KYC & Documents" in section
    assert "detail-enhanced-evidence-documents-group" in section
    assert "detail-kyc-documents-panel" in section
    assert "enhancedRequirementDomId(req.id || req.requirement_key || ('evidence_' + idx))" in enhanced_section
    assert 'data-enhanced-requirement-id="' in enhanced_section


def test_periodic_review_documents_summary_keeps_mutating_document_workflow_out_of_pr_tab():
    html = _read_backoffice()
    section = _function_region(html, "periodicReviewKycFallbackAnchor", "renderLifecycleClientAttestationPanel")

    assert "viewBackofficeDocument" in section
    assert "downloadBackofficeDocument" in section
    assert "Terminal review evidence is read-only here." in section
    assert "['completed', 'cancelled', 'canceled'].indexOf(statusKey) >= 0" in section
    assert "Review in KYC & Documents" in section

    for forbidden in (
        "submitLifecycleEvidenceUpload",
        "linkLifecycleEvidenceDocument",
        "addLifecycleCustomEvidenceRequirement",
        "handleApplicationEnhancedRequirementUpload",
        "saveApplicationEnhancedRequirement",
        "reviewBackofficeDocument(",
        "verifyBackofficeDocument(",
        "triggerAgent1",
        "Agent 1",
        ">Upload</button>",
        "Upload and link evidence",
        "Accept with reason",
        ">Accept</button>",
        ">Reject</button>",
        "Request replacement",
        "Re-Verify",
    ):
        assert forbidden not in section


def test_overview_periodic_review_baseline_box_is_simplified_backoffice_only_and_auditable():
    html = _read_backoffice()
    assert 'id="detail-periodic-review-baseline"' in html
    assert "function renderOverviewPeriodicReviewBaseline" in html
    assert "function loadOverviewPeriodicReviewBaseline" in html
    assert "function saveOverviewPeriodicReviewBaseline" in html
    assert "canEditPeriodicReviewLegacyBaseline" in html
    assert "Periodic Review Baseline" not in html
    assert "Officer-only setup metadata" not in html
    assert "periodic-baseline-row" in html
    assert "Is this a legacy file?" in html
    assert "Last review date" in html
    assert "Derived cadence" in html
    assert "/periodic-review-baseline" in html
    assert "legacy_file" in html
    assert "last_review_date" in html
    assert "['n/a', 'N/A']" in html
    assert "Use N/A only when no manual baseline applies." not in html
    assert "legacyEl.disabled" in html
    assert "overview-periodic-review-baseline-cadence-" not in html


def test_overview_periodic_review_baseline_loads_from_application_detail_without_review_gate():
    html = _read_backoffice()
    summary_start = html.index("async function loadLifecycleApplicationSummary(applicationId)")
    summary_end = html.index("function fetchLifecycleApplicationSummary", summary_start)
    summary_section = html[summary_start:summary_end]
    assert "loadOverviewPeriodicReviewBaseline(applicationId, resp);" in summary_section
    baseline_start = html.index("async function loadOverviewPeriodicReviewBaseline(applicationId, summary)")
    baseline_end = html.index("function lifecycleDetailBadge", baseline_start)
    baseline_section = html[baseline_start:baseline_end]
    assert "var detail = await boApiCall('GET', '/applications/' + encodeURIComponent(applicationId));" in baseline_section
    assert "No periodic review case is available yet for baseline setup on this application." not in baseline_section
    assert "Periodic review baseline can be configured after onboarding approval." in html


def test_overview_memo_download_is_disabled_until_memo_exists():
    html = _read_backoffice()
    assert "function setMemoDownloadState(enabled, reason)" in html
    assert "No compliance memo exists yet. Generate the memo before downloading a PDF." in html
    assert "Generate a compliance memo before downloading the PDF." in html
    assert "setMemoDownloadState(false" in html
    assert "setMemoDownloadState(true" in html


def test_lifecycle_workspace_uses_scrollable_responsive_layout():
    html = _read_backoffice()
    assert ".lifecycle-detail-root" in html
    assert ".lifecycle-section-stack" in html
    assert "grid-template-columns:repeat(6,minmax(0,1fr))" in html
    assert "max-height:440px; overflow:auto" in html
    assert "@media (max-width:1180px)" in html
    assert "@media (max-width:760px)" in html


def test_ongoing_monitoring_review_rows_open_application_lifecycle_with_review_deep_link():
    html = _read_backoffice()
    start = html.index("function renderPeriodicReviews()")
    end = html.index("// ── Monitoring-stage AI agent catalog", start)
    section = html[start:end]
    assert "openMonitoringReviewLifecycle(review.ref, review.id)" in section
    assert "Open review case" in section
    assert "function openMonitoringReviewLifecycle(ref, reviewId)" in html
    assert "window._lifecycleDeepLinkTarget = { type: 'review', id: reviewId };" in html
    assert "openAppDetail(ref, { initialTab: 'lifecycle' })" in html


def test_ongoing_monitoring_review_surface_is_signal_only_launchpad():
    html = _read_backoffice()
    start = html.index('<div class="view" id="view-periodic-review-signals">')
    end = html.index('<div class="view" id="view-monitoring">', start)
    section = html[start:end]
    assert "Periodic Review Queue" in section
    assert "Officer queue for canonical periodic review cases with due-date, owner, status, and trigger truth." in section
    assert "Open Lifecycle Queue" in section
    assert "monitoring-review-due-count" in section
    assert "monitoring-review-overdue-count" in section
    assert "monitoring-review-blocked-count" in section
    assert "Schedule Due Reviews" not in section


def test_periodic_review_cleanup_removes_internal_banner_and_linked_item_wording():
    html = _read_backoffice()
    assert "Lifecycle: 1 active linked item" not in html
    assert "Periodic Reviews owns the review cockpit." not in html
    assert "Lifecycle:" not in html or "Lifecycle: 1 active linked item" not in html
    assert "Related monitoring alert" not in html


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


def test_lifecycle_queue_deep_link_focuses_exact_review_or_linked_item():
    html = _read_backoffice()
    helper_start = html.index("function lifecycleQueueItemDomId(type, id)")
    helper_end = html.index("function lifecycleQuarantineChips", helper_start)
    helper_section = html[helper_start:helper_end]
    detail_start = html.index("function renderPeriodicReviewWorkspaceOverview(reviewDetail)")
    detail_end = html.index("function renderPeriodicReviewWorkspaceReadiness(reviewDetail)", detail_start)
    detail_section = html[detail_start:detail_end]
    assert "window._lifecycleDeepLinkTarget" in html
    assert "focusLifecycleDeepLinkTarget" in helper_section
    assert "scrollIntoView" in helper_section
    assert "lifecycleQueueItemDomId('review', reviewDetail && reviewDetail.id)" in detail_section
