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
    assert "function monitoringAlertSubjectScope" in html
    assert "function companyMonitoringMediaFacts" in html

    region = _function_region(html, "buildEntityScreeningReviewCard", "buildPersonScreeningReviewCard")
    assert "companyMonitoringMediaFacts(app)" in region
    assert "monitoringMedia.matched" in region
    assert ".concat(monitoringMedia.results || [])" in region
    assert "Provider adverse-media evidence is persisted and listed above." in region

    media_region = _function_region(html, "companyMonitoringMediaFacts", "declaredPepFromScreeningRecord")
    assert "monitoringAlertSubjectScope(alert) === 'entity'" in media_region


def test_backoffice_company_review_includes_top_level_company_results():
    html = BACKOFFICE_HTML.read_text()

    facts_region = _function_region(html, "screeningResultFacts", "screeningResultIdentity")
    assert "hitFacts.total > 0" in facts_region

    entity_region = _function_region(html, "buildEntityScreeningReviewCard", "buildPersonScreeningReviewCard")
    assert "var companyRecords = [company, companySanctions, companyAdverse]" in entity_region
    assert ".concat((company && company.results) || [])" in entity_region
    assert "companyResults = dedupScreeningResults(companyResults)" in entity_region


def test_backoffice_screening_review_renders_provider_evidence_details():
    html = BACKOFFICE_HTML.read_text()

    assert "Provider evidence details" in html
    assert "modal-screening-evidence" in html
    assert "screening-evidence-body" in html
    assert "function openScreeningEvidenceDrawer" in html
    assert "View evidence" in html
    assert "Case ID" in html
    assert "Alert ID" in html
    assert "Risk ID" in html
    assert "Profile ID" in html

    region = _function_region(html, "providerResultHighlights", "providerIndicatorDetails")
    assert "provider_case_identifier" in region
    assert "provider_alert_identifier" in region
    assert "provider_risk_identifier" in region
    assert "provider_profile_identifier" in region
    assert "media_title" in region
    assert "media_snippet" in region
    assert "registerScreeningEvidence" in region
    assert "openScreeningEvidenceDrawer" in region
    assert "target=\"_blank\" rel=\"noopener\"" in region


def test_backoffice_screening_evidence_drawer_renders_structured_review_fields():
    html = BACKOFFICE_HTML.read_text()

    drawer_region = _function_region(html, "openScreeningEvidenceDrawer", "providerResultHighlights")
    assert "Match Semantics" in drawer_region
    assert "Media Evidence" in drawer_region
    assert "PEP Evidence" in drawer_region
    assert "Sanctions / Watchlist Evidence" in drawer_region
    assert "Provider case ID" in drawer_region
    assert "Provider alert ID" in drawer_region
    assert "Provider risk ID" in drawer_region
    assert "Provider profile ID" in drawer_region
    assert "Subject scope" in drawer_region
    assert "Declared PEP" in drawer_region
    assert "Provider PEP match" in drawer_region
    assert "Undeclared PEP" in drawer_region
    assert "Media title" in drawer_region
    assert "Media snippet" in drawer_region
    assert "Media URL" in drawer_region
    assert "Open media source" in drawer_region


def test_backoffice_screening_review_uses_backend_provider_evidence_payload():
    html = BACKOFFICE_HTML.read_text()

    entity_region = _function_region(html, "buildEntityScreeningReviewCard", "buildPersonScreeningReviewCard")
    person_region = _function_region(html, "buildPersonScreeningReviewCard", "renderScreeningReviewPanel")

    assert "reviewRow && reviewRow.provider_evidence" in entity_region
    assert "reviewRow && reviewRow.provider_evidence" in person_region
    assert "providerResultHighlights(companyResults, {" in entity_region
    assert "providerResultHighlights([].concat(screening.results || []).concat((reviewRow && reviewRow.provider_evidence) || []), {" in person_region


def test_backoffice_person_review_prefers_screening_declared_pep_truth():
    html = BACKOFFICE_HTML.read_text()

    assert "function declaredPepFromScreeningRecord" in html
    region = _function_region(html, "buildPersonScreeningReviewCard", "renderScreeningReviewPanel")
    assert "declaredPepFromScreeningRecord(screeningRecord, person.pep)" in region
    assert "screeningBadge(personDeclaredPep ? 'declared' : 'not_declared')" in region
    assert "Declared PEP:</strong> ' + escapeHtml(personDeclaredPep ? 'Yes' : 'No')" in region
    assert "Declared PEP:</strong> ' + escapeHtml(person.pep === 'Yes' ? 'Yes' : 'No')" not in region
