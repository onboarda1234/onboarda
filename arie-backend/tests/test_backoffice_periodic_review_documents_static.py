from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = REPO_ROOT / "arie-backoffice.html"


def test_backoffice_lifecycle_surfaces_conditional_periodic_review_documents():
    html = BACKOFFICE_HTML.read_text()
    assert "function renderLifecyclePeriodicReviewDocumentRequests(reviewDetail)" in html
    assert "Conditional periodic review document requests" in html
    assert "Triggering question:" in html
    assert "Material changes and supporting documents will be routed to Change Management / officer outcome in a later step." in html
    assert "Linked review" in html
