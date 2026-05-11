from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _function_region(html, name, next_name=None):
    start = html.index(f"function {name}")
    if next_name:
        end = html.index(f"function {next_name}", start)
    else:
        end = start + 4000
    return html[start:end]


def test_backoffice_company_review_uses_monitoring_media_alerts():
    html = BACKOFFICE_HTML.read_text()

    assert "monitoringAlerts: detail.monitoring_alerts || []" in html
    assert "function companyMonitoringMediaFacts" in html

    region = _function_region(html, "buildEntityScreeningReviewCard", "buildPersonScreeningReviewCard")
    assert "companyMonitoringMediaFacts(app)" in region
    assert "monitoringMedia.matched" in region
    assert ".concat(monitoringMedia.results || [])" in region
    assert "Provider adverse-media evidence is persisted and listed above." in region


def test_backoffice_person_review_prefers_screening_declared_pep_truth():
    html = BACKOFFICE_HTML.read_text()

    assert "function declaredPepFromScreeningRecord" in html
    region = _function_region(html, "buildPersonScreeningReviewCard", "renderScreeningReviewPanel")
    assert "declaredPepFromScreeningRecord(screeningRecord, person.pep)" in region
    assert "screeningBadge(personDeclaredPep ? 'declared' : 'not_declared')" in region
    assert "Declared PEP:</strong> ' + escapeHtml(personDeclaredPep ? 'Yes' : 'No')" in region
    assert "Declared PEP:</strong> ' + escapeHtml(person.pep === 'Yes' ? 'Yes' : 'No')" not in region
