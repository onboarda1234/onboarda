from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = REPO_ROOT / "arie-backoffice.html"
PORTAL_HTML = REPO_ROOT / "arie-portal.html"


def test_backoffice_periodic_review_workspace_sections_exist():
    html = BACKOFFICE_HTML.read_text()

    assert "Review Overview" in html
    assert "Client Attestation Summary" in html
    assert "Documents & Evidence" in html
    assert "Active Blockers / Readiness" in html
    assert "Screening / Monitoring Context" in html
    assert "Officer Findings Draft" in html
    assert "Future Actions" in html
    assert "Save draft findings" in html
    assert "/monitoring/reviews/' + encodeURIComponent(reviewId) + '/findings" in html


def test_periodic_review_queue_routes_into_lifecycle_workspace():
    html = BACKOFFICE_HTML.read_text()

    assert "function openMonitoringReviewLifecycle(ref, reviewId)" in html
    assert "window._lifecycleDeepLinkTarget = { type: 'review', id: reviewId };" in html
    assert "openMonitoringReviewLifecycle(review.ref, review.id)" in html
    assert "Open review case" in html


def test_simplified_baseline_box_uses_legacy_toggle_and_derived_cadence():
    html = BACKOFFICE_HTML.read_text()

    assert 'id="detail-periodic-review-baseline"' in html
    assert "Is this a legacy file?" in html
    assert "Last review date" in html
    assert "Derived cadence" in html
    assert "Next review due" in html
    assert "periodicReviewBaselineLegacyOptions" in html
    assert "legacy_file" in html
    assert "last_review_date" in html
    assert "Cadence is derived from the current officer-visible risk level." in html
    assert "overview-periodic-review-baseline-cadence-" not in html


def test_portal_still_does_not_expose_officer_workspace_or_baseline():
    html = PORTAL_HTML.read_text()

    assert "Periodic Review Workspace" not in html
    assert "Officer Findings Draft" not in html
    assert "Periodic Review Baseline" not in html
