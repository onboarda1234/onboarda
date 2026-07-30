from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _function_region(html: str, name: str, next_name: Optional[str] = None) -> str:
    start = html.index(f"function {name}")
    if next_name:
        end = html.index(f"function {next_name}", start)
    else:
        end = start + 4000
    return html[start:end]


def _view_region(html: str, view_id: str, next_view_id: str) -> str:
    start = html.index(f'<div class="view" id="{view_id}">')
    end = html.index(f'<div class="view" id="{next_view_id}">', start)
    return html[start:end]


def _sidebar_item_region(html: str, view: str) -> str:
    marker = html.index(f'data-view="{view}"')
    start = html.rfind('<div class="snav-item', 0, marker)
    end = html.index('</div>', marker) + len('</div>')
    return html[start:end]


def test_sidebar_lists_periodic_review_signals_above_monitoring_alerts():
    html = BACKOFFICE_HTML.read_text()

    assert 'data-view="periodic-review-signals"' in html
    assert "showView('periodic-review-signals',this)" in html
    assert 'data-view="monitoring"' in html
    assert "showView('monitoring',this)" in html

    periodic_index = html.index('data-view="periodic-review-signals"')
    monitoring_index = html.index('data-view="monitoring"')
    assert periodic_index < monitoring_index
    assert '> Periodic Reviews</div>' in html
    assert '> Monitoring</div>' in html


def test_phase1_primary_navigation_hides_scoped_items_without_removing_modules():
    html = BACKOFFICE_HTML.read_text()

    hidden_views = {
        "kpis": "view-kpis",
        "lifecycle": "view-lifecycle",
        "reg-intel": "view-reg-intel",
        "supervisor-audit": "view-supervisor-audit",
    }
    for view, view_id in hidden_views.items():
        item = _sidebar_item_region(html, view)
        assert 'data-pilot-hidden="phase1-primary-navigation"' in item
        assert f"showView('{view}',this)" in item
        assert f'id="{view_id}"' in html

    for visible_view in (
        "dashboard",
        "applications",
        "cases",
        "screening-queue",
        "periodic-review-signals",
        "monitoring",
        "change-mgmt",
        "reports",
        "supervisor",
    ):
        assert 'data-pilot-hidden=' not in _sidebar_item_region(html, visible_view)

    supervisor_item = _sidebar_item_region(html, "supervisor")
    assert "Supervisor Dashboard" in supervisor_item
    assert ">Enterprise</span>" in supervisor_item
    assert '<div class="snav-section role-admin-only">Administration</div>' in html
    assert "[data-pilot-hidden], body .sidebar .sidebar-nav .snav-item[data-pilot-hidden] { display:none !important; }" in html


def test_periodic_review_signals_is_a_standalone_view():
    html = BACKOFFICE_HTML.read_text()

    view_region = _view_region(html, "view-periodic-review-signals", "view-monitoring")
    show_view_region = _function_region(html, "showView", "signOut")
    route_region = _function_region(html, "applyBackofficeHashRoute", "renderKPIDashboard")

    assert "Scheduled review workflow for periodic and annual client reviews" in view_region
    assert "Monitoring Alerts remains a separate event-based signal inbox" in view_region
    assert 'id="review-status-filter"' in view_region
    assert 'id="review-risk-filter"' in view_region
    assert 'id="periodic-reviews-body"' in view_region
    assert "Open Lifecycle Queue" in view_region
    assert "'periodic-review-signals':'RegMind Periodic Reviews'" in show_view_region
    assert "if (name === 'periodic-review-signals')" in show_view_region
    assert "renderPeriodicReviewQueue();" in show_view_region
    assert "if (loaded) renderPeriodicReviewQueue();" in show_view_region
    assert "if (name === 'periodic-review-signals') {\n    renderMonitoring();" not in show_view_region
    assert "ensureMonitoringDataLoaded()" in show_view_region
    assert "if (route.view === 'periodic-review-signals')" in route_region
    assert "if (route.view === 'monitoring')" in route_region


def test_monitoring_alerts_view_keeps_agents_and_drops_review_signals_tab():
    html = BACKOFFICE_HTML.read_text()

    view_region = _view_region(html, "view-monitoring", "view-lifecycle")
    show_view_region = _function_region(html, "showView", "signOut")
    switch_tab_region = _function_region(html, "switchMonitoringTab", "renderMonitoringAlerts")
    lifecycle_region = _function_region(html, "lifecycleSourceModuleLabel", "lifecycleObjectLabel")

    assert "RegMind Monitoring</h3>" in view_region
    assert "Track event-based monitoring alerts between formal reviews" in view_region
    assert "document expiry and financial-crime screening changes" in view_region
    assert "Periodic reviews are managed in the Periodic Review module" in view_region
    assert "Client profile changes are managed in Change Management" in view_region
    assert "Transaction monitoring is not included in this pilot scope" in view_region
    assert "switchMonitoringTab('alerts',this)" in view_region
    assert "switchMonitoringTab('agents',this)" in view_region
    assert ">Pilot Scope</div>" in view_region
    assert "switchMonitoringTab('reviews',this)" not in view_region
    assert 'id="monitoring-reviews-tab"' not in view_region
    assert "'Monitoring Alerts'" in show_view_region
    assert "switchMonitoringTab('alerts');" in show_view_region
    assert "if (tab === 'alerts')" in switch_tab_region
    assert "if (tab === 'agents')" in switch_tab_region
    assert "reviews" not in switch_tab_region
    assert "if (type === 'alert') return 'Monitoring Alerts';" in lifecycle_region


def test_monitoring_pilot_scope_excludes_periodic_review_transaction_and_risk_drift_sources():
    html = BACKOFFICE_HTML.read_text()

    view_region = _view_region(html, "view-monitoring", "view-lifecycle")
    catalog_start = html.index("var MONITORING_AGENT_CATALOG")
    catalog_end = html.index("function monitoringAgentNumberFromRuntime", catalog_start)
    catalog_region = html[catalog_start:catalog_end]
    render_region = _function_region(html, "renderMonitoringAgents", "triggerAgentRun")

    assert "Adverse Media & PEP Monitoring Agent" in catalog_region
    assert "Financial-crime screening change detection between formal reviews" in catalog_region
    assert "Periodic Review Preparation Agent" not in catalog_region
    assert "Behaviour & Risk Drift Agent" not in catalog_region
    assert "Ongoing Compliance Review Agent" not in catalog_region

    assert "Monitoring pilot scope" in render_region
    assert "document-expiry alerts and financial-crime screening changes" in render_region
    assert "Periodic reviews are managed in the Periodic Review module" in render_region
    assert "client profile changes in Change Management" in render_region
    assert "transaction monitoring or broad risk drift are not active in this pilot" in render_region
    assert "if (isMonitoringScopeExcludedAgentId(agentNumber)) return;" in render_region

    assert "Risk Drift" not in view_region
    assert "Unusual Activity" not in view_region
    assert "Regulatory Impact" not in view_region


def test_agent_health_hidden_until_real_telemetry_is_active():
    html = BACKOFFICE_HTML.read_text()

    assert 'data-pilot-hidden="agent-health"' in html
    assert "[data-pilot-hidden], body .sidebar .sidebar-nav .snav-item[data-pilot-hidden] { display:none !important; }" in html
    assert "var AGENT_HEALTH_ACTIVE = false;" in html
    assert "Agent Health Monitoring Unavailable" in html
    assert "hidden from paid-pilot navigation" in html
    render_region = _function_region(html, "renderAgentHealth", "toggleHealthCard")
    assert "if (!AGENT_HEALTH_ACTIVE || APP_ENV !== 'demo')" in render_region


def test_audit_chain_navigation_matches_admin_sco_backend_policy():
    html = BACKOFFICE_HTML.read_text()
    show_view_region = _function_region(html, "showView", "signOut")

    assert 'data-view="supervisor-audit" data-enterprise-coming-soon="true"' in html
    assert "var scoOnlyViews = ['audit'];" in show_view_region


def test_support_reference_preload_does_not_fetch_users_for_lower_roles():
    html = BACKOFFICE_HTML.read_text()
    region = _function_region(html, "loadSupportReferenceData", "ensureScreeningQueueLoaded")

    assert "var role = (currentUser && currentUser.role) || (BO_AUTH_USER && BO_AUTH_USER.role) || '';" in region
    assert "if (role === 'admin' || role === 'sco')" in region
    assert "supportLoads.unshift(loadUsersFromAPI(options)" in region
    assert "Promise.all(supportLoads)" in region


def test_admin_pages_do_not_create_generic_client_ip_audit_rows():
    html = BACKOFFICE_HTML.read_text()

    assert "AUDIT_LOG.unshift" not in html
    assert "ip:'client'" not in html
    assert 'ip: "client"' not in html
    assert "refreshAdminAuditEvidence()" in html
