from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = REPO_ROOT / "arie-backoffice.html"
PORTAL_HTML = REPO_ROOT / "arie-portal.html"


def test_backoffice_periodic_review_workspace_sections_exist():
    html = BACKOFFICE_HTML.read_text()
    start = html.index("function renderLifecycleDetailTab(context)")
    end = html.index("async function loadLifecycleDetailTab(force)", start)
    section = html[start:end]

    overview_idx = section.index("Review Overview")
    blockers_idx = section.index("Active Blockers / Readiness")
    attestation_idx = section.index("Client Attestation Summary")
    documents_idx = section.index("Documents & Evidence")
    decision_idx = section.index("decisionCardHtml +")
    monitoring_idx = section.index("Monitoring Alerts Considered In This Review")
    assert overview_idx < blockers_idx < attestation_idx < documents_idx < decision_idx < monitoring_idx
    assert "Future Actions" not in section
    assert "Review Context" in section
    assert "Review History" in section
    assert "Review Setup Summary" not in section
    assert "Officer Findings Draft" not in html
    assert "Save draft findings" not in html
    assert "renderPeriodicReviewWorkspaceFindingsDraft" not in html
    assert "savePeriodicReviewWorkspaceFindings" not in html
    assert "Complete periodic review" in html
    assert "function renderPeriodicReviewWorkspaceDecision(reviewDetail)" in html
    assert "async function completePeriodicReviewDecision(reviewId)" in html
    assert "/monitoring/reviews/' + encodeURIComponent(reviewId) + '/complete" in html


def test_application_detail_uses_periodic_reviews_label_and_alerts_tab():
    html = BACKOFFICE_HTML.read_text()

    assert 'id="tab-lifecycle"' in html
    assert ">Periodic Reviews</button>" in html
    assert 'id="tab-alerts"' in html
    assert ">Alerts</button>" in html
    assert 'id="detail-tab-alerts"' in html
    assert "No active alerts linked to this application." in html
    assert "Open in Monitoring Alerts" in html


def test_periodic_review_queue_routes_into_lifecycle_workspace():
    html = BACKOFFICE_HTML.read_text()

    assert "function openMonitoringReviewLifecycle(ref, reviewId)" in html
    assert "window._lifecycleDeepLinkTarget = { type: 'review', id: reviewId };" in html
    assert "openMonitoringReviewLifecycle(review.ref, review.id)" in html
    assert "Open review case" in html


def test_simplified_baseline_box_uses_legacy_toggle_and_derived_cadence():
    html = BACKOFFICE_HTML.read_text()

    assert 'id="detail-periodic-review-baseline"' in html
    assert "Is this a legacy file?" in html
    assert "Last review date" in html
    assert "Derived cadence" in html
    assert "Next review due" in html
    assert "periodicReviewBaselineLegacyOptions" in html
    assert "legacy_file" in html
    assert "last_review_date" in html
    assert "Cadence is derived from the current officer-visible risk level." not in html
    assert "periodic-baseline-row" in html
    assert "overview-periodic-review-baseline-cadence-" not in html


def test_portal_still_does_not_expose_officer_workspace_or_baseline():
    html = PORTAL_HTML.read_text()

    assert "Periodic Review Workspace" not in html
    assert "Officer Findings Draft" not in html
    assert "Periodic Review Baseline" not in html


def test_periodic_review_cleanup_text_removes_internal_banner_and_old_linkage_strip():
    html = BACKOFFICE_HTML.read_text()

    assert "Periodic Reviews owns the review cockpit." not in html
    assert "Lifecycle: 1 active linked item" not in html
    assert "Related monitoring alert" not in html


def test_periodic_review_decision_panel_has_required_fields_and_no_internal_copy():
    html = BACKOFFICE_HTML.read_text()
    start = html.index("function renderPeriodicReviewWorkspaceDecision(reviewDetail)")
    end = html.index("async function completePeriodicReviewDecision(reviewId)", start)
    section = html[start:end]

    assert "Periodic Review Decision" in html
    assert "Final outcome" in section
    assert "Review findings summary" in section
    assert "decision.findings_summary || reviewDetail.officer_findings_note" in section
    assert "Rationale for decision" in section
    assert "Risk / EDD / exit rationale" in section
    assert "Follow-up note" in section
    assert "decision.follow_up_notes || reviewDetail.officer_deficiencies_note" in section
    assert "Senior review note, if applicable" in section
    assert "decision.senior_review_note || reviewDetail.officer_internal_review_note" in section
    assert "Officer acknowledgement is required" in html
    assert "Completed reviews are read-only historical records." in section
    assert "Future actions will be added in later phases." not in html
    assert "backend" not in section.lower()


def test_periodic_review_completion_refreshes_queue_from_canonical_api():
    html = BACKOFFICE_HTML.read_text()
    start = html.index("async function completePeriodicReviewDecision(reviewId)")
    end = html.index("function applicationAlertsSeverityTone(severity)", start)
    section = html[start:end]

    assert "ensureMonitoringDataLoaded({ force: true })" in section
    assert "renderPeriodicReviewQueue()" in section
    assert "loadMonitoringReviews" not in section


def test_periodic_review_tab_ux1_summary_next_action_and_readiness_checklist():
    html = BACKOFFICE_HTML.read_text()
    overview_start = html.index("function renderPeriodicReviewWorkspaceOverview(reviewDetail)")
    overview_end = html.index("function renderPeriodicReviewWorkspaceNotifications(reviewDetail)", overview_start)
    overview_section = html[overview_start:overview_end]
    readiness_start = html.index("function renderPeriodicReviewWorkspaceReadiness(reviewDetail)")
    readiness_end = html.index("function renderPeriodicReviewWorkspaceMonitoring(reviewDetail)", readiness_start)
    readiness_section = html[readiness_start:readiness_end]

    assert "function periodicReviewWorkspaceNextAction(reviewDetail)" in html
    assert 'data-prs-ux1-summary="true"' in overview_section
    assert "Periodic Review Summary" in overview_section
    assert "Next action" in overview_section
    assert "Review ID" in overview_section
    assert "Risk level" in overview_section
    assert "Due date" in overview_section
    assert "Owner" in overview_section
    assert "data-prs-ux1-readiness-checklist" in readiness_section
    assert "data-prs-ux1b-blocker-summary" in readiness_section
    for checklist_label in (
        "Client attestation",
        "Required documents",
        "Screening / monitoring alerts",
        "Risk reassessment",
        "Officer decision",
    ):
        assert checklist_label in readiness_section
    assert "Awaiting client" in readiness_section
    assert "pending review" in readiness_section
    assert "blocker(s)" in readiness_section
    assert "Decision controls remain subject to completion gates." not in readiness_section
    assert "blockers.map(function(label)" not in readiness_section


def test_periodic_review_tab_ux1b_deduplicates_decision_blockers_without_removing_controls():
    html = BACKOFFICE_HTML.read_text()
    decision_start = html.index("function renderPeriodicReviewWorkspaceDecision(reviewDetail)")
    decision_end = html.index("async function completePeriodicReviewDecision(reviewId)", decision_start)
    decision_section = html[decision_start:decision_end]
    refresh_start = html.index("function refreshPeriodicReviewDecisionActions(reviewId)")
    refresh_end = html.index("function renderPeriodicReviewWorkspaceDecision(reviewDetail)", refresh_start)
    refresh_section = html[refresh_start:refresh_end]

    assert "function periodicReviewDecisionReadinessBlockers(reviewDetail, draft)" in html
    assert "function periodicReviewDecisionFieldBlockers(draft)" in html
    assert "data-prs-ux1b-decision-blocked" in decision_section
    assert "data-prs-ux1b-decision-controls" in decision_section
    assert "Decision draft controls" in decision_section
    assert "Officer decision unavailable until readiness blockers clear." in decision_section
    assert "Completion blockers currently shown:" not in decision_section
    assert "Completion blocked:</strong><ul" not in refresh_section
    assert "Officer decision blocked:" in refresh_section
    assert "Decision fields needed:" in refresh_section
    assert "periodic-review-decision-outcome-" in decision_section
    assert "periodic-review-decision-complete-btn-" in decision_section
    assert "completePeriodicReviewDecision" in decision_section


def test_periodic_review_tab_ux1_collapses_long_context_without_removing_sections():
    html = BACKOFFICE_HTML.read_text()
    detail_start = html.index("function renderLifecycleDetailTab(context)")
    detail_end = html.index("async function loadLifecycleDetailTab(force)", detail_start)
    detail_section = html[detail_start:detail_end]
    attestation_start = html.index("function renderLifecycleClientAttestationPanel(reviewDetail, reviewProjection)")
    attestation_end = html.index("function renderPeriodicReviewWorkspaceOverview(reviewDetail)", attestation_start)
    attestation_section = html[attestation_start:attestation_end]

    assert "function lifecycleDetailCollapsibleCard(title, subtitle, body, accent, openByDefault)" in html
    assert "Full attestation questions" in attestation_section
    assert "Questions with declared changes appear first when expanded." in attestation_section
    assert "sortedQuestions" in attestation_section
    assert "lifecycleDetailCollapsibleCard('Risk Reassessment & Memo Addendum'" in detail_section
    assert "periodicReviewRiskNeedsAttention(activeReview)" in detail_section
    assert "lifecycleDetailCollapsibleCard('Monitoring Alerts Considered In This Review'" in detail_section
    assert "periodicReviewMonitoringNeedsAttention(activeReview)" in detail_section
    assert "lifecycleDetailCollapsibleCard('Notification Status'" in detail_section
    assert "periodicReviewNotificationsNeedAttention(activeReview)" in detail_section
    assert "lifecycleDetailCollapsibleCard('Review Context'" in detail_section
    assert "lifecycleDetailCollapsibleCard('Review History'" in detail_section
    assert "Review Context" in detail_section
    assert "Review History" in detail_section
    assert "Notification Status" in detail_section


def test_periodic_review_tab_ux1_preserves_controls_links_and_shell_boundaries():
    html = BACKOFFICE_HTML.read_text()
    detail_start = html.index("function renderLifecycleDetailTab(context)")
    detail_end = html.index("async function loadLifecycleDetailTab(force)", detail_start)
    detail_section = html[detail_start:detail_end]
    decision_start = html.index("function renderPeriodicReviewWorkspaceDecision(reviewDetail)")
    decision_end = html.index("async function completePeriodicReviewDecision(reviewId)", decision_start)
    decision_section = html[decision_start:decision_end]
    risk_start = html.index("function renderPeriodicReviewWorkspaceRiskReassessment(reviewDetail)")
    risk_end = html.index("function applicationAlertsSeverityTone(severity)", risk_start)
    risk_section = html[risk_start:risk_end]

    assert "detail-case-command-centre" not in detail_section
    assert "renderCaseCommandCentre(" not in detail_section
    assert "switchDetailTab('kyc-docs')" in html
    assert "activateCaseCommandTarget('kyc-docs', targetId)" in html
    assert "Resolve in KYC & Documents" in html
    assert "Review in KYC & Documents" in html
    assert "periodic-review-decision-complete-btn-" in decision_section
    assert "completePeriodicReviewDecision" in decision_section
    assert "periodic-review-risk-save-btn-" in risk_section
    assert "generatePeriodicReviewMemoAddendum" in risk_section
    assert "finalizePeriodicReviewMemoAddendum" in risk_section
    assert "boApiCall('POST', '/monitoring/reviews/' + encodeURIComponent(reviewId) + '/complete'" in html
    assert "boApiCall('POST', '/monitoring/reviews/' + encodeURIComponent(reviewId) + '/risk-reassessment'" in html
    assert "boApiCall('POST', '/periodic-reviews/' + encodeURIComponent(reviewId) + '/memo'" in html
