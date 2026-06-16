from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"
SERVER_PY = ROOT / "arie-backend" / "server.py"


def _function_region(html: str, name: str, next_name: Optional[str] = None) -> str:
    needles = (f"function {name}", f"async function {name}")
    start = min((html.index(needle) for needle in needles if needle in html), default=-1)
    if start < 0:
        raise ValueError(f"function {name} not found")
    if next_name:
        next_needles = (f"function {next_name}", f"async function {next_name}")
        end = min((html.index(needle, start) for needle in next_needles if needle in html[start:]), default=-1)
        if end < 0:
            raise ValueError(f"function {next_name} not found after {name}")
    else:
        end = start + 5000
    return html[start:end]


def _view_region(html: str, view_id: str, next_view_id: str) -> str:
    start = html.index(f'<div class="view" id="{view_id}">')
    end = html.index(f'<div class="view" id="{next_view_id}">', start)
    return html[start:end]


def test_enterprise_sidebar_items_are_visible_but_badged():
    html = BACKOFFICE_HTML.read_text()

    assert 'data-view="kpis" data-enterprise-coming-soon="true"' in html
    assert '<span class="snav-label">KPI Dashboard</span><span class="snav-badge">Coming Soon</span>' in html
    assert 'data-view="reg-intel" data-enterprise-coming-soon="true"' in html
    assert '<span class="snav-label">Regulatory Intelligence</span><span class="snav-badge">Coming Soon</span>' in html
    assert 'data-view="supervisor" data-enterprise-coming-soon="true"' in html
    assert '<span class="snav-label">Supervisor Dashboard</span><span class="snav-badge">Enterprise</span>' in html
    assert 'data-view="supervisor-audit" data-enterprise-coming-soon="true"' in html
    assert '<span class="snav-label">Audit Chain</span><span class="snav-badge">Enterprise</span>' in html


def test_enterprise_views_render_coming_soon_placeholders_not_operational_ui():
    html = BACKOFFICE_HTML.read_text()

    kpi_region = _view_region(html, "view-kpis", "view-applications")
    reg_region = _view_region(html, "view-reg-intel", "view-resources")
    supervisor_region = _view_region(html, "view-supervisor", "view-supervisor-audit")
    supervisor_audit_region = html[
        html.index('<div class="view" id="view-supervisor-audit">') :
        html.index("</div><!-- /content -->", html.index('<div class="view" id="view-supervisor-audit">'))
    ]

    for region in (kpi_region, reg_region, supervisor_region, supervisor_audit_region):
        assert "Coming Soon — Enterprise Module" in region
        assert "not active in the pilot environment" in region
        assert "Not active in pilot" in region

    assert "Enterprise Analytics will provide runtime-backed KPI reporting" in kpi_region
    assert "Regulatory Intelligence will support regulatory change tracking" in reg_region
    assert "The AI Compliance Supervisor will provide advanced supervisory oversight" in supervisor_region
    assert "The AI Compliance Supervisor will provide advanced supervisory oversight" in supervisor_audit_region
    assert "renderKPIDashboard()" not in kpi_region
    assert "exportKPIReport()" not in kpi_region
    assert "kpi-date-filter" not in kpi_region
    assert "kpi-card" not in kpi_region
    assert "kpi-section-ops" not in kpi_region
    assert "showRegUploadModal()" not in reg_region
    assert "supervisor-kpi-grid" not in supervisor_region
    assert "sv-audit-filter-type" not in supervisor_audit_region


def test_enterprise_routes_do_not_load_operational_enterprise_data():
    html = BACKOFFICE_HTML.read_text()
    show_view_region = _function_region(html, "showView", "signOut")
    route_region = _function_region(html, "applyBackofficeHashRoute", "renderKPIDashboard")
    preload_region = _function_region(html, "bootstrapBackofficeSession")

    assert "var ENTERPRISE_COMING_SOON_VIEWS" in html
    assert "'kpis': true" in html
    assert "'kpi-dashboard': 'kpis'" in html
    assert "'enterprise-analytics': 'kpis'" in html
    assert "'regulatory-intelligence': 'reg-intel'" in html
    assert "'supervisor-dashboard': 'supervisor'" in html
    assert "'audit-chain': 'supervisor-audit'" in html
    assert "typeof isEnterpriseComingSoonView === 'function' && isEnterpriseComingSoonView(name)" in show_view_region
    assert show_view_region.index("isEnterpriseComingSoonView(name)") < show_view_region.index("if (name === 'kpis') renderKPIDashboard();")
    assert "if (isEnterpriseComingSoonView(route.view))" in route_region
    assert "safeLoadModule('Regulatory intelligence'" not in html
    assert "boApiCall('GET', '/regulatory-intelligence')" not in preload_region


def test_kpi_dashboard_is_enterprise_coming_soon_under_pilot_defaults():
    html = BACKOFFICE_HTML.read_text()
    kpi_region = _view_region(html, "view-kpis", "view-applications")
    show_view_region = _function_region(html, "showView", "signOut")

    assert 'data-module="kpi-dashboard"' in kpi_region
    assert "Enterprise Analytics" in kpi_region
    assert "Coming Soon" in kpi_region
    assert "Not active in pilot" in kpi_region
    assert "Real-time performance metrics" not in kpi_region
    assert "Avg. Processing Time" not in kpi_region
    assert "Approval Rate" not in kpi_region
    assert "Dashboard shows sample data" not in kpi_region
    assert "FEATURE_FLAGS.ENABLE_KPI_DASHBOARD && FEATURE_FLAGS.ENABLE_KPI_DEMO_DATA" in html
    assert show_view_region.index("isEnterpriseComingSoonView(name)") < show_view_region.index("if (name === 'kpis') renderKPIDashboard();")


def test_ai_agents_8_9_10_are_marked_enterprise_and_not_active():
    html = BACKOFFICE_HTML.read_text()
    normalize_region = _function_region(html, "normalizeAIAgentConfig", "getAIAgentRecordId")
    render_region = _function_region(html, "renderAgentsPipeline", "toggleAgentPanel")
    toggle_region = _function_region(html, "toggleAgent", "addAgentCheck")

    assert "var ENTERPRISE_ROADMAP_AGENT_IDS = { 8: true, 9: true, 10: true };" in html
    assert "normalized.enabled = false;" in normalize_region
    assert "normalized.scope = 'Enterprise roadmap';" in normalize_region
    assert "normalized.availability = 'Not active in pilot';" in normalize_region
    assert "Coming Soon" in render_region
    assert "Enterprise roadmap" in render_region
    assert "Not active in pilot" in render_region
    assert "These agents are not active in the pilot environment. They are part of RegMind’s enterprise automation roadmap." in render_region
    assert "Operational controls disabled for pilot." in render_region
    assert "is Coming Soon and not active in pilot" in toggle_region
    assert "pilotActiveAgentCount()" in html


def test_monitoring_agent_run_surface_blocks_enterprise_agents():
    html = BACKOFFICE_HTML.read_text()
    render_region = _function_region(html, "renderMonitoringAgents", "triggerAgentRun")
    trigger_region = _function_region(html, "triggerAgentRun")

    assert "Agents 8, 9, and 10 are enterprise-roadmap modules for pilot scope." in html
    assert "enterpriseRoadmap: true" in html
    assert "Not active in pilot" in render_region
    assert "Future release" in render_region
    assert "isEnterpriseRoadmapAgentId(monitoringAgentNumberFromRuntime(runtimeAgent))" in trigger_region
    assert "This agent is Coming Soon and not active in pilot" in trigger_region


def test_backoffice_direct_path_aliases_serve_shell():
    server = SERVER_PY.read_text()

    assert "/backoffice/(?:kpis|kpi-dashboard|enterprise-analytics|regulatory-intelligence|reg-intel|ai-compliance-supervisor|supervisor-dashboard|supervisor|audit-chain|supervisor-audit|supervisor-audit-chain|ai-agents)" in server
