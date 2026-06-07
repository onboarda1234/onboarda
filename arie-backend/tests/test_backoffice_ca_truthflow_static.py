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

    assert "Evidence groups" in html
    assert "modal-screening-evidence" in html
    assert "screening-evidence-body" in html
    assert "function openScreeningEvidenceDrawer" in html
    assert "View evidence" in html
    assert "Provider case ID" in html
    assert "Provider alert ID" in html
    assert "Provider risk ID" in html
    assert "Provider profile ID" in html

    region = _function_region(html, "providerResultHighlights", "providerIndicatorDetails")
    assert "providerEvidenceRecordCard" in region
    assert "evidencePrimaryCategoryLabel" in region
    assert "evidenceSensitivityLabel" in region
    assert "Show evidence" in region
    assert "evidence records grouped for this" in region
    record_region = _function_region(html, "providerEvidenceRecordCard", "providerResultHighlights")
    drawer_region = _function_region(html, "openScreeningEvidenceDrawer", "providerResultHighlights")
    title_region = _function_region(html, "evidenceSourceTitle", "evidenceSourcePublisher")
    assert "provider_case_identifier" in record_region
    assert "provider_alert_identifier" in record_region
    assert "provider_risk_identifier" in record_region
    assert "provider_profile_identifier" in record_region
    assert "registerScreeningEvidence" in record_region
    assert "openScreeningEvidenceDrawer" in record_region
    assert "target=\"_blank\" rel=\"noopener\"" in record_region
    assert "media_title" in title_region
    assert "media_snippet" in drawer_region


def test_backoffice_screening_review_adds_declared_vs_provider_comparison():
    html = BACKOFFICE_HTML.read_text()

    comparison_region = _function_region(html, "screeningComparisonAssessment", "screeningComparisonPrimaryHit")
    panel_region = _function_region(html, "buildScreeningComparisonPanel", "providerResultHighlights")
    entity_region = _function_region(html, "buildEntityScreeningReviewCard", "buildPersonScreeningReviewCard")
    person_region = _function_region(html, "buildPersonScreeningReviewCard", "renderScreeningReviewPanel")

    assert "Declared vs Provider Match" in html
    assert "Comparison shown against highest-risk provider match." in html
    assert "Missing Declared Data" in comparison_region
    assert "Missing Provider Data" in comparison_region
    assert "Likely Match" in comparison_region
    assert "Conflict" in comparison_region
    assert "Not Comparable" in comparison_region
    assert "Provider match" in panel_region
    assert "Declared in application" in panel_region
    assert "Assessment" in panel_region
    assert "buildScreeningComparisonPanel('entity'" in entity_region
    assert "buildScreeningComparisonPanel('person'" in person_region


def test_backoffice_screening_review_renders_triage_cockpit_layout():
    html = BACKOFFICE_HTML.read_text()

    triage_region = _function_region(html, "screeningSubjectTypeLabel", "screeningReviewCard")
    review_region = _function_region(html, "renderScreeningReviewPanel", "openScreeningReview")

    assert "function screeningTriageDisplayStatusLabel" in html
    assert "function screeningTriagePriority" in html
    assert "function screeningTriageSubjectListItem" in html
    assert "function screeningSubjectRoleCode" in html
    assert "function screeningSubjectRoleBadge" in html
    assert "function setScreeningReviewFocus" in html
    assert "function buildScreeningTriageSubjects" in html
    assert "Second Review Required" in triage_region
    assert "No Match" in html
    assert "Declared PEP · No provider matches" in triage_region
    assert "screeningSubjectRoleBadge(subject.subject_type)" in triage_region
    assert "screeningQueueStatusBadge(subject.status_key, subject.display_status_label)" in triage_region
    assert "Screening Subjects" in review_region
    assert "Select one subject to review comparison, evidence, and disposition state." in review_region
    assert "screeningTriageSubjectListItem(subject" in review_region
    assert "selectedSubject.kind === 'entity'" in review_region
    assert "buildEntityScreeningReviewCard(app, selectedSubject.reviewRow, screeningSummary, focus)" in review_region
    assert "buildPersonScreeningReviewCard(selectedSubject.person, app, selectedSubject.reviewRow, focus)" in review_region


def test_backoffice_screening_queue_sidebar_alias_and_audit_formatters_exist():
    html = BACKOFFICE_HTML.read_text()

    show_view_region = _function_region(html, "showView", "signOut")
    activity_region = _function_region(html, "safeParseAuditDetail", "loadNotes")
    render_screening_region = _function_region(html, "renderScreening", "mapEDDCaseFromApi")

    assert "data-view=\"screening-queue\"" in html
    assert "showView('screening-queue',this)" in html
    assert "if (name === 'screening-queue') name = 'screening';" in show_view_region
    assert "renderScreening({ force: SCREENING_QUEUE_DIRTY || !SCREENING_QUEUE.rows.length })" in show_view_region
    assert "function renderScreeningAuditEntry" in html
    assert "Technical audit details" in activity_region
    assert "Screening Review Completed" in activity_region
    assert "screeningAuditSourceLabel" in activity_region
    assert "screeningQueueSignalBadge(row.watchlist_status, row)" in render_screening_region
    assert "screeningQueueSignalBadge(row.pep_screening_status || 'not_applicable', row)" in render_screening_region


def test_backoffice_pr_b_queue_and_detail_paths_stay_narrow():
    html = BACKOFFICE_HTML.read_text()

    open_detail_region = _function_region(html, "openAppDetail", "rmiStatusBadge")
    change_region = _function_region(html, "renderChangeMgmt", "showChangeMgmtTab")
    show_alert_region = _function_region(html, "showCreateAlertModal", "submitCreateAlert")
    show_request_region = _function_region(html, "showCreateRequestModal", "submitCreateRequest")
    screening_region = _function_region(html, "loadScreeningQueue", "screeningQueueStatusBadge")
    lifecycle_region = _function_region(html, "loadLifecycleQueue", "renderLifecycleRows")

    assert "await loadScreeningQueue()" not in open_detail_region
    assert "loadCMApplications();" not in change_region
    assert "ensureCMApplicationsLoaded" in show_alert_region
    assert "ensureCMApplicationsLoaded" in show_request_region
    assert "/screening/queue?" in screening_region
    assert "screeningQueueQueryParams(offset)" in screening_region
    assert "refresh=" in screening_region
    assert "/lifecycle/queue?include=" in lifecycle_region
    assert "&limit=" in lifecycle_region
    assert "&offset=" in lifecycle_region


def test_backoffice_screening_evidence_drawer_renders_structured_review_fields():
    html = BACKOFFICE_HTML.read_text()

    drawer_region = _function_region(html, "openScreeningEvidenceDrawer", "providerResultHighlights")
    assert "Why this needs review" in drawer_region
    assert "Evidence summary" in drawer_region
    assert "Technical provider details" in html
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
    assert "Technical provider details" in html


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
    assert "_group_records" in grouping_region
    assert "_group_record_keys" in grouping_region
    assert "_grouped_profile_identifiers" in grouping_region
    assert "_grouped_risk_identifiers" in grouping_region
    assert "_grouped_alert_identifiers" in grouping_region
    assert "var seen = {};" in grouped_ids_region
    assert "return grouped.length > 1 ? grouped : [];" in grouped_ids_region
    assert "var hits = groupProviderEvidenceHits" in provider_region
    assert "Evidence groups" in provider_region
    assert "providerEvidenceRecordCard(record, context, index, recordIndex)" in provider_region
    assert "evidence records grouped for this provider profile/category" in provider_region
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
    assert "var personProviderResults = [].concat(screening.results || []).concat((reviewRow && reviewRow.provider_evidence) || []);" in person_region
    assert "providerResultHighlights(personProviderResults, personProviderContext);" in person_region
    assert "provider: company.source || company.provider || screeningSummary.provider" in entity_region
    assert "provider: facts.source || screeningSummary.provider" in person_region


def test_backoffice_person_review_prefers_screening_declared_pep_truth():
    html = BACKOFFICE_HTML.read_text()

    assert "function declaredPepFromScreeningRecord" in html
    region = _function_region(html, "buildPersonScreeningReviewCard", "renderScreeningReviewPanel")
    assert "declaredPepFromScreeningRecord(screeningRecord, person.pep)" in region
    assert "screeningTagBadge('Declared PEP'" in region
    assert "Declared PEP:</strong> ' + escapeHtml(personDeclaredPep ? 'Yes' : 'No')" in region
    assert "Declared PEP:</strong> ' + escapeHtml(person.pep === 'Yes' ? 'Yes' : 'No')" not in region


def test_backoffice_screening_disposition_modal_matches_api_contract():
    html = BACKOFFICE_HTML.read_text()

    modal_start = html.index("<!-- ═══════════════ SCREENING DISPOSITION MODAL")
    modal_end = html.index('<div class="modal-overlay" id="modal-screening-evidence">', modal_start)
    modal_region = html[modal_start:modal_end]
    options_region = _function_region(html, "screeningDispositionCodeOptions", "screeningRationaleWordCount")
    submit_region = _function_region(html, "submitScreeningDisposition", "renderScreening")

    assert "screening-disposition-evidence" in modal_region
    assert "Evidence / Provider Reference" in modal_region
    assert "Upload supporting evidence (optional)" in modal_region
    assert "false_positive_cleared" in options_region
    assert "confirmed_match" in options_region
    assert "material_concern" in options_region
    assert "escalated_to_edd" in options_region
    assert "provider_no_relevant_match" not in options_region
    assert "potential_sanctions_match" not in options_region
    assert "screening_evidence=true" in html
    assert "evidence_reference: evidenceReference" in submit_region
    assert "evidence_document_id: uploadedEvidence ? uploadedEvidence.id : ''" in submit_region
    assert "No Match disposition requires Compliance Officer, SCO, or Admin role" in submit_region
    assert "False-positive clearance requires an evidence / provider reference" not in submit_region


def test_backoffice_screening_disposition_history_surfaces_evidence_reference():
    html = BACKOFFICE_HTML.read_text()

    badge_region = _function_region(html, "screeningReviewBadge", "screeningReviewHistory")
    history_region = _function_region(html, "screeningReviewHistory", "screeningReviewCard")
    queue_start = html.index("async function renderScreening")
    queue_end = html.index("function mapEDDCaseFromApi", queue_start)
    queue_region = html[queue_start:queue_end]

    assert "review_evidence_reference" in badge_region
    assert "Evidence/reference:" in badge_region
    assert "review_evidence_reference" in history_region
    assert "Evidence/reference:" in history_region
    assert "canClearScreeningDisposition()" in queue_region
    assert "No Match requires Compliance Officer, SCO, or Admin role." in queue_region


def test_backoffice_screening_truth_fallbacks_do_not_flatten_non_terminal_to_clear():
    html = BACKOFFICE_HTML.read_text()

    assert "function deriveScreeningTruth" in html
    assert "function deriveScreeningTruthSummary" in html
    assert "sandbox_provider" in html
    assert "simulated_fallback" in html
    assert "screening_truth_summary" in html

    person_region = _function_region(html, "getPersonScreeningResult", "screeningBadge")
    assert "truth.canonical_state === 'completed_clear'" in person_region
    assert "sanctions: sanctionsStatus" in person_region
    assert "pep: pepStatus" in person_region
    assert "hasMatchedRecord ? (hasSanctionsHit ? 'match' : (hasOtherHit ? 'review' : 'clear')) : 'clear'" not in person_region

    approval_region = _function_region(html, "getApplicationApprovalBlockers", "renderDecisionReadiness")
    assert "screening.screening_truth_summary" in approval_region
    assert "!screeningTruth.approval_ready" in approval_region


def test_backoffice_legacy_match_without_api_status_remains_terminal_match_fallback():
    html = BACKOFFICE_HTML.read_text()
    mode_region = _function_region(html, "screeningProviderModeFromRecord", "deriveScreeningTruth")

    assert "record.matched && Array.isArray(record.results) && record.results.length" in mode_region
    assert "return 'live_provider'" in mode_region
