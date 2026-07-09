import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _html() -> str:
    return BACKOFFICE_HTML.read_text()


def _css_rule(html: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", html)
    assert match, f"CSS rule not found: {selector}"
    return match.group(1)


def _px_var(css: str, name: str) -> int:
    match = re.search(re.escape(name) + r":(\d+)px", css)
    assert match, f"CSS variable not found: {name}"
    return int(match.group(1))


def test_backoffice_content_reserves_scroll_clearance_for_floating_controls():
    html = _html()
    root = _css_rule(html, ":root")

    bottom = _px_var(root, "--bo-floating-bottom")
    size = _px_var(root, "--bo-floating-size")
    gap = _px_var(root, "--bo-floating-gap")
    clearance = _px_var(root, "--bo-floating-clearance")

    assert clearance >= bottom + size + gap
    assert ".content { padding-bottom:var(--bo-floating-clearance); }" in html
    assert "--bo-floating-right:24px" in root
    assert "--bo-floating-size:56px" in root

    tablet_root = html[html.index("@media (max-width: 1024px)") : html.index("/* ── Responsive: Mobile ── */")]
    mobile_root = html[html.index("@media (max-width: 768px)") : html.index(".content { padding-bottom:var(--bo-floating-clearance); }")]
    assert "--bo-floating-clearance:164px" in tablet_root
    assert "--bo-floating-clearance:152px" in mobile_root


def test_backoffice_toast_layer_does_not_block_bottom_right_controls():
    html = _html()
    toast = _css_rule(html, ".toast")
    toast_action = _css_rule(html, ".toast-action")

    assert "position:fixed" in toast
    assert "bottom:calc(var(--bo-floating-bottom) + var(--bo-floating-size) + var(--bo-floating-gap))" in toast
    assert "right:var(--bo-floating-right)" in toast
    assert "pointer-events:none" in toast
    assert "bottom:24px" not in toast
    assert "right:24px" not in toast
    assert "pointer-events:auto" in toast_action
    assert '<div class="toast" id="toast" role="alert" aria-live="polite"></div>' in html


def test_backoffice_ai_chat_removed_without_dangling_references():
    # The "Quick Reference" fake-AI chat (and its floating toggle) was removed
    # wholesale — its canned responses presented static text as AI assistance.
    # Guard against any resurrection or dangling reference.
    html = _html()

    assert "ai-chat-toggle" not in html
    assert "ai-chat-sidebar" not in html
    for fn in ("toggleAIChat", "sendAIMessage", "sendQuickQuestion",
               "addChatMessage", "generateAIResponse"):
        assert fn not in html, f"dangling chat reference: {fn}"
    # The floating-control geometry variables must survive for the toast layer.
    assert "--bo-floating-bottom" in html
    assert "--bo-floating-size" in html


def test_monitoring_alerts_pagination_markup_still_uses_existing_navigation():
    html = _html()
    start = html.index('id="monitoring-alerts-pagination"')
    pagination_fn = html[html.index("function renderMonitoringAlertsPagination") : html.index("function renderPeriodicReviews")]

    assert start > 0
    assert "setMonitoringAlertsPage(1)" in pagination_fn
    assert "Previous" in pagination_fn
    assert "Next" in pagination_fn
    assert "buildMonitoringAlertsApiPath" in html
