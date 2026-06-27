from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = REPO_ROOT / "arie-backoffice.html"


def test_backoffice_lifecycle_surfaces_conditional_periodic_review_documents():
    html = BACKOFFICE_HTML.read_text()
    assert "function renderLifecyclePeriodicReviewDocumentRequests(reviewDetail, requiredItems, evidenceLinks, documents)" in html
    assert "Periodic review documents and evidence" in html
    assert "Conditional PR document requests" in html
    assert "Required-item evidence blockers" in html
    assert "Triggering question:" in html
    assert "Resolution remains in KYC Documents & Verifications." in html
    assert "Upload status:" in html
    assert 'data-prs-doc1-row="conditional-request"' in html
    assert 'data-prs-doc1-row="required-item"' in html
    assert "Resolve in KYC & Documents" in html
    assert "Review in KYC & Documents" in html
