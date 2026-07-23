from pathlib import Path


HTML = Path(__file__).resolve().parents[2].joinpath("arie-backoffice.html").read_text(
    encoding="utf-8"
)


def test_sidebar_has_one_global_width_source_and_reflows_main_content():
    assert "--sidebar-expanded-width:240px" in HTML
    assert "--sidebar-collapsed-width:72px" in HTML
    assert "html.sidebar-collapsed { --sidebar-w:var(--sidebar-collapsed-width); }" in HTML
    assert ".main { margin-left:var(--sidebar-w)" in HTML


def test_sidebar_control_is_accessible_and_supports_both_states():
    assert 'id="sidebar-toggle"' in HTML
    assert 'aria-label="Collapse navigation sidebar"' in HTML
    assert "Collapse navigation sidebar" in HTML
    assert "Expand navigation sidebar" in HTML
    assert "function toggleSidebar()" in HTML


def test_sidebar_preference_is_namespaced_and_strictly_restored():
    assert HTML.count("regmind.backoffice.sidebarCollapsed") == 2
    assert "=== 'true'" in HTML
    assert "saved === 'true' || saved === 'false'" in HTML
    assert "try {" in HTML
    assert "catch (e)" in HTML


def test_navigation_items_gain_keyboard_names_without_route_changes():
    assert "item.setAttribute('role', 'button')" in HTML
    assert "item.setAttribute('tabindex', '0')" in HTML
    assert "item.setAttribute('aria-label', label)" in HTML
    assert "item.dataset.tooltip = label" in HTML
    assert "if (event.key === 'Enter' || event.key === ' ')" in HTML
    for destination in (
        "showView('dashboard',this)",
        "showView('applications',this)",
        "showView('cases',this)",
        "showView('screening-queue',this)",
        "showView('periodic-review-signals',this)",
        "showView('monitoring',this)",
        "showView('change-mgmt',this)",
    ):
        assert destination in HTML


def test_mobile_drawer_remains_independent_of_desktop_preference():
    assert "@media (max-width: 768px)" in HTML
    assert ".sidebar.mobile-open { transform:translateX(0); }" in HTML
    assert "function toggleMobileMenu()" in HTML
    assert ".sidebar-toggle { display:none; }" in HTML
