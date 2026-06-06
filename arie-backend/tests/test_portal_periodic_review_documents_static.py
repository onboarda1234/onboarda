from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PORTAL_HTML = REPO_ROOT / "arie-portal.html"


def test_portal_periodic_review_modal_surfaces_post_submit_document_request_summary():
    html = PORTAL_HTML.read_text()
    assert "function periodicReviewDocumentRequestSummaryHtml(task)" in html
    assert "Thank you. Based on the changes declared, additional documents may be required." in html
    assert "Use the requested documents card on your dashboard to upload these items using the existing secure upload flow." in html
    assert "await loadPortalEnhancedRequirements();" in html
    assert "await resumeApplication(targetRef, targetStage);" in html
    assert "if (!data.application_ref && taskMeta.application_ref) data.application_ref = taskMeta.application_ref;" in html
