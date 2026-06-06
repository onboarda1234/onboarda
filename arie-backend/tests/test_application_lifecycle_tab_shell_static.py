import os
import re


BACKOFFICE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "arie-backoffice.html",
)


def _read_backoffice():
    with open(BACKOFFICE_PATH, "r", encoding="utf-8") as f:
        return f.read()


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


def test_lifecycle_tab_shell_sections_match_prs4_workspace():
    html = _read_backoffice()
    assert 'id="detail-tab-lifecycle"' in html
    for title in (
        "Review Overview",
        "Active Blockers / Readiness",
        "Client Attestation Summary",
        "Documents & Evidence",
        "Screening / Monitoring Context",
        "Officer Findings Draft",
        "Future Actions",
        "Review Setup Summary",
        "History",
    ):
        assert title in html
    assert "Completion Gate" not in html


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
    assert "renderApplicationAlertsDetailTab()" in section
    assert "loadDecisionRecords()" in section
    assert "loadActivityLog()" in section
    assert "loadNotes()" in section


def test_alerts_tab_shell_uses_existing_application_monitoring_alert_data():
    html = _read_backoffice()
    assert 'id="detail-tab-alerts"' in html
    assert "function renderApplicationAlertsDetailTab()" in html
    assert "monitoringAlerts: detail.monitoring_alerts || []" in html
    assert "No active alerts linked to this application." in html
    assert "showView('monitoring')" in html


def test_lifecycle_workspace_renders_prs4_read_only_attestation_documents_and_findings_helpers():
    html = _read_backoffice()
    assert "function renderLifecycleClientAttestationPanel(reviewDetail, reviewProjection)" in html
    assert "function renderLifecyclePeriodicReviewDocumentRequests(reviewDetail)" in html
    assert "function renderPeriodicReviewWorkspaceReadiness(reviewDetail)" in html
    assert "function renderPeriodicReviewWorkspaceMonitoring(reviewDetail)" in html
    assert "function renderPeriodicReviewWorkspaceFindingsDraft(reviewDetail)" in html
    assert "async function savePeriodicReviewWorkspaceFindings(reviewId)" in html
    assert "/findings" in html
    assert "Save draft findings" in html
    assert "Final outcome controls arrive in PRS-5." in html


def test_overview_periodic_review_baseline_box_is_simplified_backoffice_only_and_auditable():
    html = _read_backoffice()
    assert 'id="detail-periodic-review-baseline"' in html
    assert "Periodic Review Baseline" in html
    assert "function renderOverviewPeriodicReviewBaseline" in html
    assert "function loadOverviewPeriodicReviewBaseline" in html
    assert "function saveOverviewPeriodicReviewBaseline" in html
    assert "canEditPeriodicReviewLegacyBaseline" in html
    assert "Officer-only setup metadata" in html
    assert "Is this a legacy file?" in html
    assert "Last review date" in html
    assert "Derived cadence" in html
    assert "/periodic-review-baseline" in html
    assert "legacy_file" in html
    assert "last_review_date" in html
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
