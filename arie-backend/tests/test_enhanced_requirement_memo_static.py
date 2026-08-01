import os


BACKOFFICE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "arie-backoffice.html"
)


def _backoffice_html():
    with open(BACKOFFICE_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


def test_backoffice_memo_workspace_does_not_duplicate_enhanced_review():
    html = _backoffice_html()
    start = html.index('id="compliance-memo-workspace"')
    end = html.index('</section>', start)
    region = html[start:end]

    assert 'id="memo-pdf-preview"' in region
    assert "enhanced_review_edd" not in region
    assert "Onboarding Enhanced Review" not in region


def test_backoffice_html_memo_renderer_is_a_noop():
    html = _backoffice_html()
    start = html.index("function renderMemoSections")
    end = html.index("function generateComplianceMemo", start)
    region = html[start:end]

    assert "return '';" in region
    assert "enhanced_review_edd" not in region
    assert "notification" not in region.lower()
