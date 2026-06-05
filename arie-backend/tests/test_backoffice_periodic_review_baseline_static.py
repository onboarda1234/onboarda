from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"
PORTAL_HTML = ROOT / "arie-portal.html"


def test_overview_contains_compact_periodic_review_baseline_box():
    html = BACKOFFICE_HTML.read_text()

    assert 'id="detail-periodic-review-baseline"' in html
    assert "Periodic Review Baseline" in html
    assert "Officer-only setup metadata" in html
    assert "overview-periodic-review-baseline-status-" in html
    assert "overview-periodic-review-baseline-date-" in html
    assert "overview-periodic-review-baseline-cadence-" in html
    assert "overview-periodic-review-baseline-next-due-" in html
    assert "Save baseline" in html
    assert "Not scheduled yet" in html
    assert "Missing baseline date" in html
    assert "N/A disables the baseline date" in html


def test_portal_does_not_render_officer_baseline_box():
    html = PORTAL_HTML.read_text()

    assert "Periodic Review Baseline" not in html
    assert "overview-periodic-review-baseline" not in html
