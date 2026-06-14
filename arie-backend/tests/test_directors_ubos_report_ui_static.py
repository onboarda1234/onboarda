from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = REPO_ROOT / "arie-backoffice.html"


def _html():
    return BACKOFFICE_HTML.read_text(encoding="utf-8")


def test_reports_section_exposes_directors_ubos_report_tab_and_panel():
    html = _html()

    assert 'data-tab="directors-ubos"' in html
    assert "Directors &amp; UBOs" in html
    assert 'id="rpt-panel-directors-ubos"' in html
    assert 'id="rpt-du-summary-cards"' in html
    assert 'id="rpt-du-tbody"' in html
    assert 'id="rpt-du-pagination"' in html
    assert "loadDirectorsUboReport({ force:" in html


def test_directors_ubos_ui_has_required_filter_controls():
    html = _html()
    required_ids = [
        "rpt-du-role",
        "rpt-du-nationality",
        "rpt-du-residence",
        "rpt-du-pep-status",
        "rpt-du-sanctions-status",
        "rpt-du-adverse-status",
        "rpt-du-screening-status",
        "rpt-du-screening-review",
        "rpt-du-ownership-min",
        "rpt-du-ownership-max",
        "rpt-du-missing-dob",
        "rpt-du-missing-nationality",
        "rpt-du-missing-ownership",
        "rpt-du-missing-docs",
        "rpt-du-expired-docs",
        "rpt-du-failed-doc-verification",
        "rpt-du-pending-doc-verification",
        "rpt-du-app-status",
        "rpt-du-risk-level",
        "rpt-du-assigned",
        "rpt-du-created-from",
        "rpt-du-created-to",
        "rpt-du-updated-from",
        "rpt-du-updated-to",
    ]

    for element_id in required_ids:
        assert f'id="{element_id}"' in html
    assert "setDirectorsUboView('combined')" in html
    assert "setDirectorsUboView('directors')" in html
    assert "setDirectorsUboView('ubos')" in html


def test_directors_ubos_ui_uses_backend_endpoint_and_csv_export():
    html = _html()
    report_js = html[html.index("var DIRECTORS_UBO_REPORT") : html.index("function buildReportDataFromApplications")]

    assert "/reports/directors-ubos?" in report_js
    assert "buildDirectorsUboQuery('json')" in report_js
    assert "buildDirectorsUboQuery('csv')" in report_js
    assert "exportDirectorsUboCSV" in report_js
    assert "X-Report-Record-Count" in report_js
    assert "Directors & UBOs report exported" in report_js


def test_directors_ubos_drilldowns_only_render_from_backend_links():
    html = _html()
    report_js = html[html.index("function renderDirectorsUboActions") : html.index("function renderDirectorsUboTable")]

    assert "row.links && row.links.screening_review" in report_js
    assert "row.links && row.links.documents" in report_js
    assert "row.links && row.links.periodic_review" in report_js
    assert "openAppDetail" in report_js
    assert "openDirectorsUboScreening" in html
    assert "openDirectorsUboDocuments" in html
    assert "initialTab: 'kyc-docs'" in html
    assert "initialTab: 'lifecycle'" in html


def test_directors_ubos_ui_role_visibility_and_denial_are_present():
    html = _html()

    assert 'class="snav-item role-reporting" data-view="reports"' in html
    assert "body.role-admin .role-reporting" in html
    assert "body.role-sco .role-reporting" in html
    assert "body.role-co .role-reporting" in html
    assert "function canViewDirectorsUboReport()" in html
    assert "reportingViews = ['reports']" in html
    assert "Access restricted to compliance officers" in html

