import os


BACKOFFICE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "arie-backoffice.html"
)


def _backoffice_html():
    with open(BACKOFFICE_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


def test_backoffice_memo_renderer_includes_enhanced_review_section():
    html = _backoffice_html()
    start = html.index("function renderMemoSections")
    end = html.index("function generateComplianceMemo", start)
    region = html[start:end]

    assert "enhanced_review_edd" in region
    assert "Onboarding Enhanced Review" in region
    assert "deterministic lifecycle summary" in region


def test_backoffice_memo_renderer_does_not_call_workflow_side_effects():
    html = _backoffice_html()
    start = html.index("function renderMemoSections")
    end = html.index("function generateComplianceMemo", start)
    region = html[start:end]

    assert "/rmi" not in region.lower()
    assert "notification" not in region.lower()
    assert "/approval" not in region.lower()
    assert "/memo" not in region.lower().replace("rendermemosections", "")
