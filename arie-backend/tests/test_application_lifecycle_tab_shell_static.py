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
