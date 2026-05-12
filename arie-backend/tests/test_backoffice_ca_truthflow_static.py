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
    assert "Provider case ID" in html
    assert "Provider alert ID" in html
    assert "Provider risk ID" in html
    assert "Provider profile ID" in html

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
    assert "Traceability" in drawer_region
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


def test_backoffice_screening_evidence_drawer_uses_review_friendly_fallbacks():
    html = BACKOFFICE_HTML.read_text()

    drawer_region = _function_region(html, "openScreeningEvidenceDrawer", "providerResultHighlights")
    register_region = _function_region(html, "registerScreeningEvidence", "evidenceInfoGrid")
    title_region = _function_region(html, "evidencePrimaryLabel", "isPepEvidenceRelevant")

    assert "function formatProviderName" in html
    assert "|| 'ComplyAdvantage'" in register_region
    assert "var provider = formatProviderName(hit.provider || hit.source || hit._provider) || 'ComplyAdvantage'" in drawer_region
    assert "Provider', provider" in drawer_region
    assert "Not recorded" not in drawer_region
    assert "function isUuidLike" in html
    assert "evidencePrimaryLabel(hit, hit)" in drawer_region
    assert "!isUuidLike(candidate)" in title_region
    assert "Screening Evidence — ' + matchedName" in drawer_region


def test_backoffice_screening_evidence_drawer_normalizes_categories_and_sections():
    html = BACKOFFICE_HTML.read_text()

    category_region = _function_region(html, "normalizeEvidenceCategoryLabel", "evidenceCategories")
    categories_region = _function_region(html, "evidenceCategories", "evidenceGroupedIdentifiers")
    drawer_region = _function_region(html, "openScreeningEvidenceDrawer", "providerResultHighlights")

    assert "Potential provider match" in categories_region
    assert "Unclassified provider hit" not in categories_region
    assert "key === 'other'" in category_region
    assert "Adverse media" in category_region
    assert "Sanctions" in category_region
    assert "Watchlist / warning" in category_region
    assert "Regulatory" in category_region
    assert "function isPepEvidenceRelevant" in html
    assert "if (isPepEvidenceRelevant(hit, categories, riskLabels))" in drawer_region
    assert "if (mediaTitle || mediaSnippet || mediaUrl)" in drawer_region


def test_backoffice_screening_evidence_groups_repeated_hits_before_rendering():
    html = BACKOFFICE_HTML.read_text()

    grouping_region = _function_region(html, "groupProviderEvidenceHits", "openScreeningEvidenceDrawer")
    provider_region = _function_region(html, "providerResultHighlights", "providerIndicatorDetails")
    drawer_region = _function_region(html, "openScreeningEvidenceDrawer", "providerResultHighlights")
    grouped_ids_region = _function_region(html, "evidenceGroupedIdentifiers", "evidenceReviewRationale")

    assert "function providerEvidenceGroupKey" in html
    assert "function groupProviderEvidenceHits" in html
    assert "function evidenceGroupedIdentifiers" in html
    assert "_group_count" in grouping_region
    assert "_grouped_risk_identifiers" in grouping_region
    assert "_grouped_alert_identifiers" in grouping_region
    assert "return grouped.length > 1 ? grouped : [];" in grouped_ids_region
    assert "var hits = groupProviderEvidenceHits" in provider_region
    assert "evidence records grouped for this profile/category" in provider_region
    assert "Evidence records grouped" in drawer_region
    assert "Grouped alert IDs" in drawer_region
    assert "Grouped risk IDs" in drawer_region
    assert "var groupedAlertIdentifiers = evidenceGroupedIdentifiers(hit._grouped_alert_identifiers);" in drawer_region
    assert "var groupedRiskIdentifiers = evidenceGroupedIdentifiers(hit._grouped_risk_identifiers);" in drawer_region
    assert "['Grouped alert IDs', groupedAlertIdentifiers]" in drawer_region
    assert "['Grouped risk IDs', groupedRiskIdentifiers]" in drawer_region


def test_backoffice_screening_evidence_drawer_adds_officer_review_rationale():
    html = BACKOFFICE_HTML.read_text()

    rationale_region = _function_region(html, "evidenceReviewRationale", "evidencePrimaryLabel")
    drawer_region = _function_region(html, "openScreeningEvidenceDrawer", "providerResultHighlights")

    assert "function evidenceReviewRationale" in html
    assert "categoryLabel = 'PEP'" in rationale_region
    assert "categoryLabel = 'adverse media'" in rationale_region
    assert "categoryLabel = 'provider'" in rationale_region
    assert "potential ' + categoryLabel + ' match" in rationale_region
    assert "Review the evidence and traceability details before recording a decision." in rationale_region
    assert "var reviewRationale = evidenceReviewRationale(categories);" in drawer_region
    assert "escapeHtml(reviewRationale)" in drawer_region


def test_backoffice_screening_review_uses_backend_provider_evidence_payload():
    html = BACKOFFICE_HTML.read_text()

    entity_region = _function_region(html, "buildEntityScreeningReviewCard", "buildPersonScreeningReviewCard")
    person_region = _function_region(html, "buildPersonScreeningReviewCard", "renderScreeningReviewPanel")

    assert "reviewRow && reviewRow.provider_evidence" in entity_region
    assert "reviewRow && reviewRow.provider_evidence" in person_region
    assert "providerResultHighlights(companyResults, {" in entity_region
    assert "providerResultHighlights([].concat(screening.results || []).concat((reviewRow && reviewRow.provider_evidence) || []), {" in person_region
    assert "provider: company.source || company.provider || screeningSummary.provider" in entity_region
    assert "provider: facts.source || screeningSummary.provider" in person_region


def test_backoffice_person_review_prefers_screening_declared_pep_truth():
    html = BACKOFFICE_HTML.read_text()

    assert "function declaredPepFromScreeningRecord" in html
    region = _function_region(html, "buildPersonScreeningReviewCard", "renderScreeningReviewPanel")
    assert "declaredPepFromScreeningRecord(screeningRecord, person.pep)" in region
    assert "screeningBadge(personDeclaredPep ? 'declared' : 'not_declared')" in region
    assert "Declared PEP:</strong> ' + escapeHtml(personDeclaredPep ? 'Yes' : 'No')" in region
    assert "Declared PEP:</strong> ' + escapeHtml(person.pep === 'Yes' ? 'Yes' : 'No')" not in region
