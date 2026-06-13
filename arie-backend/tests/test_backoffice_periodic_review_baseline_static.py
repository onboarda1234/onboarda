from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"
PORTAL_HTML = ROOT / "arie-portal.html"


def test_overview_contains_compact_periodic_review_baseline_box():
    html = BACKOFFICE_HTML.read_text()
    baseline_region = html[
        html.index("function renderOverviewPeriodicReviewBaseline"):
        html.index("async function loadOverviewPeriodicReviewBaseline")
    ]

    assert 'id="detail-periodic-review-baseline"' in html
    assert "Periodic Review Baseline" not in html
    assert "Officer-only setup metadata" not in html
    assert "periodic-baseline-compact" in baseline_region
    assert "periodic-baseline-row" in baseline_region
    assert "periodic-baseline-actions" in baseline_region
    assert "overview-periodic-review-baseline-legacy-" in baseline_region
    assert "overview-periodic-review-baseline-last-review-" in baseline_region
    assert "overview-periodic-review-baseline-next-due-" in baseline_region
    assert "Save baseline" in baseline_region
    assert "Is this a legacy file?" in baseline_region
    assert "['n/a', 'N/A']" in html
    assert "Not applicable - no periodic-review baseline will be scheduled" in html
    assert "Derived cadence" in baseline_region
    assert "Cadence is derived from the current officer-visible risk level." not in baseline_region
    assert "Officer note" not in baseline_region
    assert 'type="hidden" value="' in baseline_region
    assert "/periodic-review-baseline" in html
    assert "Periodic review baseline can be configured after onboarding approval." in html
    assert "No periodic review case is available yet for baseline setup on this application." not in html


def test_portal_does_not_render_officer_baseline_box():
    html = PORTAL_HTML.read_text()

    assert "Periodic Review Baseline" not in html
    assert "overview-periodic-review-baseline" not in html
