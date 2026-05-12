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
    assert "Why this needs review" in drawer_region
    assert "Evidence summary" in drawer_region
    assert "Technical traceability" in html
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
    assert "Article / source title" in drawer_region
    assert "Publisher / source" in drawer_region
    assert "Publication date" in drawer_region
    assert "Snippet" in drawer_region
    assert "Open source" in drawer_region


def test_backoffice_screening_evidence_drawer_is_evidence_first_before_traceability():
    html = BACKOFFICE_HTML.read_text()

    drawer_region = _function_region(html, "openScreeningEvidenceDrawer", "providerResultHighlights")

    assert drawer_region.index("Why this needs review") < drawer_region.index("Evidence summary")
    assert drawer_region.index("Evidence summary") < drawer_region.index("evidenceTraceabilitySection")
    assert drawer_region.index("Evidence summary") < drawer_region.index("Provider risk ID")
    assert "function evidenceTraceabilitySection" in html
    assert "<details" in html
    assert "<summary" in html
    assert "Technical traceability" in html


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

    assert "Provider screening hit" in categories_region
    assert "Potential provider match" not in categories_region
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
    grouped_ids_region = _function_region(html, "evidenceGroupedIdentifiers", "evidenceReviewCategoryLabel")

    assert "function providerEvidenceGroupKey" in html
    assert "function groupProviderEvidenceHits" in html
    assert "function evidenceGroupedIdentifiers" in html
    assert "_group_count" in grouping_region
    assert "_grouped_risk_identifiers" in grouping_region
    assert "_grouped_alert_identifiers" in grouping_region
    assert "var seen = {};" in grouped_ids_region
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

    category_label_region = _function_region(html, "evidenceReviewCategoryLabel", "evidenceReviewRationale")
    rationale_region = _function_region(html, "evidenceReviewRationale", "evidencePrimaryLabel")
    drawer_region = _function_region(html, "openScreeningEvidenceDrawer", "providerResultHighlights")

    assert "var EVIDENCE_REVIEW_GUIDANCE" in html
    assert "function evidenceReviewCategoryLabel" in html
    assert "function evidenceReviewRationale" in html
    assert "return 'PEP';" in category_label_region
    assert "return 'adverse media';" in category_label_region
    assert "return 'provider screening';" in category_label_region
    assert "found a potential PEP match" in rationale_region
    assert "found potential adverse media" in rationale_region
    assert "returned a provider screening hit" in rationale_region
    assert "var reviewRationale = evidenceReviewRationale(provider, categories, hit, matchedName);" in drawer_region
    assert "escapeHtml(reviewRationale)" in drawer_region


def test_backoffice_screening_evidence_drawer_prioritizes_human_readable_fields():
    html = BACKOFFICE_HTML.read_text()

    drawer_region = _function_region(html, "openScreeningEvidenceDrawer", "providerResultHighlights")

    assert "Matched person/company" in drawer_region
    assert "Role / scope" in drawer_region
    assert "Provider', provider" in drawer_region
    assert "Role / title" in drawer_region
    assert "Country" in drawer_region
    assert "Provider risk ID" in drawer_region
    assert drawer_region.index("Matched person/company") < drawer_region.index("Provider risk ID")
    assert drawer_region.index("Role / scope") < drawer_region.index("Provider profile ID")


def test_backoffice_screening_evidence_sorts_decision_useful_hits_first():
    html = BACKOFFICE_HTML.read_text()

    priority_region = _function_region(html, "evidenceCategoryPriority", "screeningEvidenceKey")
    provider_region = _function_region(html, "providerResultHighlights", "providerIndicatorDetails")

    assert "function evidenceCategoryPriority" in html
    assert "text.indexOf('sanction')" in priority_region
    assert "text.indexOf('pep')" in priority_region
    assert "text.indexOf('adverse')" in priority_region
    assert ".sort(function(a, b)" in provider_region
    assert "evidenceCategoryPriority(a, context) - evidenceCategoryPriority(b, context)" in provider_region


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
