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
    # Phase E: monitoring-media results flow into the consolidated workspace
    # evidence chain (ranked sections / legacy inline renderer) via companyResults.
    assert "legacyResults: companyResults" in region

    media_region = _function_region(html, "companyMonitoringMediaFacts", "declaredPepFromScreeningRecord")
    assert "monitoringAlertSubjectScope(alert) === 'entity'" in media_region
    assert "ref.media_url || ref.url || ref.source_url" in media_region
    assert "!ref.media_url && !ref.url && !ref.source_url" in media_region


def test_backoffice_company_review_includes_top_level_company_results():
    html = BACKOFFICE_HTML.read_text()

    facts_region = _function_region(html, "screeningResultFacts", "screeningResultIdentity")
    assert "hitFacts.total > 0" in facts_region

    entity_region = _function_region(html, "buildEntityScreeningReviewCard", "buildPersonScreeningReviewCard")
    assert "var companyRecords = [company, companySanctions, companyAdverse]" in entity_region
    assert ".concat((company && company.results) || [])" in entity_region
    assert "companyResults = dedupScreeningResults(companyResults)" in entity_region


def test_backoffice_screening_review_renders_provider_evidence_inline():
    """Phase E: screening evidence renders INLINE on the ranked hit cards and
    the legacy stored-report renderer — the View-evidence modal chain is
    retired for screening. The invariants survive relocated: media evidence
    (title/publisher/date/snippet) is shown, provider identifiers stay
    reachable under Technical details, and external links open safely."""
    html = BACKOFFICE_HTML.read_text()

    # Modal chain fully retired for screening evidence.
    assert "modal-screening-evidence" not in html
    assert "openScreeningEvidenceDrawer" not in html
    assert "View evidence" not in html

    # Enriched rows: media evidence inline on the hit card body.
    hit_body = _function_region(html, "screeningTriageHitEvidenceBody", "screeningTriageHitTechnicalDetails")
    assert "item.source_title" in hit_body
    assert "item.snippet" in hit_body
    assert "Open source" in hit_body
    assert 'target="_blank" rel="noopener"' in hit_body
    tech = _function_region(html, "screeningTriageHitTechnicalDetails", "screeningClearanceNeedsSecondReviewer")
    assert "Provider case ID" in tech
    assert "Provider alert ID" in tech
    assert "Provider risk ID" in tech
    assert "Provider profile ID" in tech

    # Legacy stored-report rows: grouped inline renderer, no popups.
    region = _function_region(html, "providerResultHighlights", "providerIndicatorDetails")
    assert "providerEvidenceRecordCard" in region
    assert "evidencePrimaryCategoryLabel" in region
    assert "evidenceSensitivityLabel" in region
    assert "Provider match records (stored report)" in region
    assert "Show details" in region
    assert "evidence records grouped for this" in region
    record_region = _function_region(html, "providerEvidenceRecordCard", "providerResultHighlights")
    title_region = _function_region(html, "evidenceSourceTitle", "evidenceSourcePublisher")
    assert "Open source" in record_region
    assert "target=\"_blank\" rel=\"noopener\"" in record_region
    assert "media_title" in title_region
    assert "media_snippet" in record_region


def test_backoffice_screening_review_adds_declared_vs_provider_comparison():
    html = BACKOFFICE_HTML.read_text()

    comparison_region = _function_region(html, "screeningComparisonAssessment", "screeningComparisonPrimaryHit")
    panel_region = _function_region(html, "buildScreeningComparisonPanel", "providerResultHighlights")
    entity_region = _function_region(html, "buildEntityScreeningReviewCard", "buildPersonScreeningReviewCard")
    person_region = _function_region(html, "buildPersonScreeningReviewCard", "renderScreeningReviewPanel")

    assert "Declared vs Provider Match" in html
    assert "Comparison shown only when provider profile attributes are available." in panel_region
    # Phase F (F6): when the provider payload carries no profile attributes to
    # compare, the panel renders NOTHING — no shell, no explanation line, and
    # nothing is fabricated for absent provider data. The truth invariant
    # (never render a comparison without provider attributes) survives as an
    # empty return instead of an explanatory shell.
    assert "if (!screeningComparisonHasProviderProfileAttributes(primaryHit))" in panel_region
    assert "return '';" in panel_region
    assert "Provider profile attributes unavailable" not in html
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

    status_label_region = _function_region(html, "screeningBusinessStatusLabel", "screeningQueueStatusBadge")
    triage_region = _function_region(html, "screeningSubjectTypeLabel", "screeningReviewCard")
    review_region = _function_region(html, "renderScreeningReviewPanel", "openScreeningReview")

    assert "function screeningTriageDisplayStatusLabel" in html
    assert "function screeningTriagePriority" in html
    assert "function screeningTriageSubjectListItem" in html
    assert "function screeningSubjectRoleCode" in html
    assert "function screeningSubjectRoleBadge" in html
    assert "function setScreeningReviewFocus" in html
    assert "function buildScreeningTriageSubjects" in html
    assert "Review Required" in status_label_region
    assert "Screening In Progress" in status_label_region
    assert "Provider Check Failed" in status_label_region
    assert "Screening Stale" in status_label_region
    assert "Second Review Required" in triage_region
    assert "Clear as False Positive" in html
    assert "Declared PEP · No provider matches" in triage_region
    assert "screeningSubjectRoleBadge(subject.subject_type)" in triage_region
    assert "screeningQueueCanonicalStatus(subject)" in triage_region
    assert "screeningQueueStatusBadge(status.key, status.label)" in triage_region
    # Provider provenance moved from the queue context cell (removed in the
    # queue slimming) to the review detail cards, which state the source for
    # the entity and per-person records.
    assert "'Company screening source'" in html
    assert "'Provider source'" in html
    assert "Screening Subjects" in review_region
    assert "Select one subject to review comparison, evidence, and disposition state." in review_region
    assert "screeningTriageSubjectListItem(subject" in review_region
    assert "selectedSubject.kind === 'entity'" in review_region
    assert "buildEntityScreeningReviewCard(app, selectedSubject.reviewRow, screeningSummary, focus)" in review_region
    assert "buildPersonScreeningReviewCard(selectedSubject.person, app, selectedSubject.reviewRow, focus)" in review_region


def test_backoffice_screening_review_omits_duplicate_document_readiness_banner():
    html = BACKOFFICE_HTML.read_text()

    review_region = _function_region(html, "renderScreeningReviewPanel", "openScreeningReview")
    detail_region = _function_region(html, "renderAuthoritativeAppDetail", "rmiStatusBadge")
    document_banner_region = _function_region(html, "renderDocumentReadinessBanner", "canEditPilotEvidenceClassification")
    document_summary_region = _function_region(html, "buildKycDocumentPanelSummary", "updateKycDocumentsPanelState")
    case_command_region = _function_region(html, "caseCommandOpenLifecycleItems", "approveApplication")

    assert "function renderDocumentReadinessBanner" in html
    assert "Incomplete / warning-state submission:" in document_banner_region

    assert "renderDocumentReadinessBanner" not in review_region
    assert "renderDocumentReadinessBanner(documentReadiness) + freshnessBanner + screeningMeta" not in detail_region
    assert "Stored provider AML/watchlist and PEP results for this application." in review_region
    assert "Screening Subjects" in review_region
    assert "buildEntityScreeningReviewCard" in review_region
    assert "buildPersonScreeningReviewCard" in review_region

    assert "renderIncompleteSubmissionBadge(documentReadiness)" in detail_region
    assert "renderStandardKycDocumentTaxonomy(app, { includeIdv:false })" in detail_region
    assert "document.getElementById('detail-docs-with-verification').innerHTML = docsHtml" in detail_region
    assert "'Required ' + summary.requiredCount" in document_summary_region
    assert "'Missing ' + summary.missingCount" in document_summary_region
    assert "'documents'" in case_command_region
    assert "'KYC Documents'" in case_command_region
    assert "'Review documents'" in case_command_region
    assert "'kyc-docs'" in case_command_region


def test_backoffice_screening_queue_sidebar_alias_and_audit_formatters_exist():
    html = BACKOFFICE_HTML.read_text()

    show_view_region = _function_region(html, "showView", "signOut")
    activity_region = _function_region(html, "safeParseAuditDetail", "loadNotes")
    render_screening_region = _function_region(html, "renderScreening", "mapEDDCaseFromApi")

    assert "data-view=\"screening-queue\"" in html
    assert "showView('screening-queue',this)" in html
    assert "if (name === 'screening-queue') name = 'screening';" in show_view_region
    assert "renderScreening({ force: SCREENING_QUEUE_DIRTY || !SCREENING_QUEUE.rows.length })" in show_view_region
    assert "function renderAuditEventCard" in html
    assert "function buildAuditSummary" in html
    assert "Show technical details" in activity_region
    assert "Screening Review Completed" in activity_region
    assert "screeningAuditSourceLabel" in activity_region
    # Queue slimming: watchlist/PEP signals render through the truthful
    # signals cell (chips only on real hits — 'review' means the hit is in
    # another dimension), and status/provenance through the status cell
    # (blocking provenance only; live is silent).
    assert "screeningQueueSignalsCell(row)" in render_screening_region
    assert "screeningQueueStatusCell(row)" in render_screening_region
    assert "screeningQueueSubjectCell(row)" in render_screening_region


def test_backoffice_audit_trail_has_filtered_ca_mesh_timeline():
    html = BACKOFFICE_HTML.read_text()

    activity_region = _function_region(html, "safeParseAuditDetail", "loadNotes")
    disposition_region = _function_region(html, "screeningAuditDispositionLabel", "screeningAuditFourEyesLabel")
    assert "'CA/Mesh'" in activity_region
    assert "ca_mesh_screening" in activity_region
    assert "ca_screening" in activity_region
    assert "provider_references" in activity_region
    assert "Mesh refs:" in activity_region
    assert "evidence quality:" in activity_region
    assert "code === 'confirmed_match' || code === 'true_match'" in disposition_region


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


def test_backoffice_screening_evidence_inline_renders_structured_review_fields():
    """Phase E re-anchor of the retired drawer pins: the structured review
    fields the drawer carried must all remain reachable inline — media
    evidence fields on the record cards, PEP/declared-PEP signals on the
    workspace, provider identifiers in the audit disclosures."""
    html = BACKOFFICE_HTML.read_text()

    record_region = _function_region(html, "providerEvidenceRecordCard", "providerResultHighlights")
    assert "Match name" in record_region
    assert "Role / scope" in record_region
    assert "Article / source title" in record_region
    assert "Publisher / source" in record_region
    assert "Publication date" in record_region
    assert "Why it matched" in record_region
    assert "Open source" in record_region
    tech = _function_region(html, "screeningTriageHitTechnicalDetails", "screeningClearanceNeedsSecondReviewer")
    assert "Provider case ID" in tech
    assert "Provider alert ID" in tech
    assert "Provider risk ID" in tech
    assert "Provider profile ID" in tech
    assert "Audit trace" in html
    person_region = _function_region(html, "buildPersonScreeningReviewCard", "renderScreeningReviewPanel")
    assert "'Declared PEP'" in person_region


def test_backoffice_screening_evidence_inline_is_evidence_first_before_traceability():
    """Evidence-first survives inline: the hit card renders the evidence body
    before the collapsed Technical details; the legacy group renderer keeps
    its Audit trace as a collapsed <details>."""
    html = BACKOFFICE_HTML.read_text()

    card_region = _function_region(html, "screeningTriageHitCard", "screeningTriageWeakTailSection")
    assert card_region.index("screeningTriageHitEvidenceBody(item)") < card_region.index("screeningTriageHitTechnicalDetails(item)")
    highlights_region = _function_region(html, "providerResultHighlights", "providerIndicatorDetails")
    assert "Audit trace" in highlights_region
    assert "<details" in highlights_region
    assert "<summary" in highlights_region


def test_backoffice_screening_evidence_uses_review_friendly_fallbacks():
    """Unknown providers stay 'Unknown Provider' (never silently rebranded
    ComplyAdvantage) and UUID-only matches get readable fallbacks — the same
    truth rules the retired drawer enforced, now on the inline surfaces."""
    html = BACKOFFICE_HTML.read_text()

    highlights_region = _function_region(html, "providerResultHighlights", "providerIndicatorDetails")
    title_region = _function_region(html, "evidencePrimaryLabel", "evidenceCategoryPriority")

    assert "function formatProviderName" in html
    assert "|| 'Unknown Provider'" in highlights_region
    assert "|| 'ComplyAdvantage'" not in highlights_region
    assert "function isUuidLike" in html
    assert "!isUuidLike(candidate)" in title_region
    display_region = _function_region(html, "screeningTriageHitDisplayName", "screeningTriageHitChips")
    assert "isUuidLike(name)" in display_region
    assert "'Unnamed provider match'" in display_region


def test_backoffice_screening_review_includes_intermediary_subjects():
    html = BACKOFFICE_HTML.read_text()

    summary_region = _function_region(html, "deriveScreeningTruthSummary", "screeningTruthBlockedReasons")
    join_region = _function_region(html, "findScreeningRecordForSubject", "buildPersonScreeningReviewCard")
    person_region = _function_region(html, "buildPersonScreeningReviewCard", "buildScreeningTriageSubjects")
    triage_region = _function_region(html, "buildScreeningTriageSubjects", "renderScreeningReviewPanel")

    assert "report.intermediary_screenings" in summary_region
    # Phase 2 subject-identity fix: the record lookup moved into the shared
    # findScreeningRecordForSubject helper (person_key first, normalized-name
    # fallback) which still spans director + ubo + intermediary entries.
    assert ".concat(report.intermediary_screenings || [])" in join_region
    assert "person_key" in join_region
    assert "findScreeningRecordForSubject(screeningSummary.report, person)" in person_region
    assert "(app.intermediaries || []).forEach" in triage_region
    assert "subject_type:'intermediary'" in triage_region


def test_backoffice_screening_evidence_drawer_normalizes_categories_and_sections():
    html = BACKOFFICE_HTML.read_text()

    category_region = _function_region(html, "normalizeEvidenceCategoryLabel", "evidenceCategories")
    categories_region = _function_region(html, "evidenceCategories", "evidenceSubjectLabel")

    assert "Provider risk match - review context" in categories_region
    assert "Potential provider match" not in categories_region
    assert "Unclassified provider hit" not in categories_region
    assert "key === 'other'" in category_region
    assert "Adverse media" in category_region
    assert "Sanctions" in category_region
    assert "Watchlist / warning" in category_region
    assert "Regulatory" in category_region


def test_backoffice_screening_evidence_groups_repeated_hits_before_rendering():
    html = BACKOFFICE_HTML.read_text()

    grouping_region = _function_region(html, "groupProviderEvidenceHits", "evidenceBirthDate")
    provider_region = _function_region(html, "providerResultHighlights", "providerIndicatorDetails")

    assert "function providerEvidenceGroupKey" in html
    assert "function groupProviderEvidenceHits" in html
    assert "_group_count" in grouping_region
    assert "_group_records" in grouping_region
    assert "_group_record_keys" in grouping_region
    assert "_grouped_profile_identifiers" in grouping_region
    assert "_grouped_risk_identifiers" in grouping_region
    assert "_grouped_alert_identifiers" in grouping_region
    assert "var hits = groupProviderEvidenceHits" in provider_region
    assert "providerEvidenceRecordCard(record, context, index, recordIndex)" in provider_region
    assert "evidence records grouped for this provider profile/category" in provider_region
    # Grouped provider identifiers stay reachable in the group Audit trace.
    # Phase F (F8): rendered as collapsed count + "Show IDs" disclosures (with
    # copy-to-clipboard) instead of comma-joined UUID walls — every ID remains
    # reachable and copyable.
    assert "screeningProviderIdListHtml('Grouped alert IDs', hit._grouped_alert_identifiers" in provider_region
    assert "screeningProviderIdListHtml('Grouped risk IDs', hit._grouped_risk_identifiers" in provider_region
    assert "screeningProviderIdListHtml('Grouped profile IDs', hit._grouped_profile_identifiers" in provider_region


def test_backoffice_screening_evidence_adds_officer_review_rationale():
    html = BACKOFFICE_HTML.read_text()

    category_label_region = _function_region(html, "evidenceReviewCategoryLabel", "evidenceReviewRationale")
    rationale_region = _function_region(html, "evidenceReviewRationale", "evidencePrimaryLabel")
    provider_region = _function_region(html, "providerResultHighlights", "providerIndicatorDetails")
    record_region = _function_region(html, "providerEvidenceRecordCard", "providerResultHighlights")

    assert "var EVIDENCE_REVIEW_GUIDANCE" in html
    assert "function evidenceReviewCategoryLabel" in html
    assert "function evidenceReviewRationale" in html
    assert "return 'PEP';" in category_label_region
    assert "return 'adverse media';" in category_label_region
    assert "return 'provider risk match';" in category_label_region
    assert "found a potential PEP match" in rationale_region
    assert "found potential adverse media" in rationale_region
    assert "returned provider risk context" in rationale_region
    assert "evidenceReviewRationale(providerName, categories, hit, evidencePrimaryLabel(hit, context))" in provider_region
    # Phase F (F4): the record card computes the primary label once (title +
    # conditional "Match name" fact) and passes it to the rationale.
    assert "evidenceReviewRationale(hit.provider || hit.source || (context || {}).provider, categories, hit, primaryLabel)" in record_region
    # Phase F (F7): a watchlist/warning hit with no list name, source or
    # snippet states honestly what is missing instead of the circular
    # "found a potential watchlist or warning match" sentence. Detail-carrying
    # watchlist hits keep the original sentence; nothing is fabricated.
    assert "The provider flagged a watchlist/warning match but supplied no list name or source detail. Review the provider record identifiers in Technical details." in rationale_region
    assert "found a potential watchlist or warning match" in rationale_region


def test_backoffice_screening_evidence_prioritizes_human_readable_fields():
    html = BACKOFFICE_HTML.read_text()

    record_region = _function_region(html, "providerEvidenceRecordCard", "providerResultHighlights")
    provider_region = _function_region(html, "providerResultHighlights", "providerIndicatorDetails")

    assert "Match name" in record_region
    assert "Role / scope" in record_region
    assert record_region.index("Match name") < record_region.index("Nationality / country")
    # The group renderer keeps identifiers inside the collapsed Audit trace,
    # after the human-readable summary content.
    assert provider_region.index("groupSummary") < provider_region.index("_grouped_alert_identifiers")


def test_backoffice_screening_evidence_sorts_decision_useful_hits_first():
    html = BACKOFFICE_HTML.read_text()

    priority_region = _function_region(html, "evidenceCategoryPriority", "evidencePrimaryCategoryLabel")
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
    # Phase E: backend provider evidence reaches the single evidence renderer
    # through the workspace config (ranked sections when triage exists,
    # inline legacy renderer otherwise).
    assert "legacyResults: companyResults" in entity_region
    assert "var personProviderResults = [].concat((screening && screening.results) || []).concat((reviewRow && reviewRow.provider_evidence) || []);" in person_region
    assert "legacyResults: personProviderResults" in person_region
    assert "provider: company.source || company.provider || screeningSummary.provider" in entity_region
    assert "provider: facts.source || screeningSummary.provider" in person_region


def test_backoffice_screening_queue_renders_structured_evidence_readiness_panel():
    """Phase E: the readiness panel is slimmed to evidence-state note +
    evidence + a collapsed provider-reference audit disclosure. The retired
    Mesh-Summary heading / subject-fact grid / Officer Decision note must NOT
    reappear (their facts live once in the header/status strip/disposition
    panel); the truth-state machinery (quality reasons, loading/error states,
    honest source-unavailable copy) survives."""
    html = BACKOFFICE_HTML.read_text()

    reason_region = _function_region(html, "screeningQueueEvidenceQualityReason", "screeningQueueEvidenceItemCard")
    panel_region = _function_region(html, "screeningQueueEvidenceReadinessPanel", "providerIndicatorDetails")
    body_region = _function_region(html, "screeningSubjectWorkspaceBody", "buildEntityScreeningReviewCard")
    entity_region = _function_region(html, "buildEntityScreeningReviewCard", "buildPersonScreeningReviewCard")
    person_region = _function_region(html, "buildPersonScreeningReviewCard", "renderScreeningReviewPanel")

    assert "function screeningQueueEvidenceReadinessPanel" in html
    assert "function screeningQueueEvidenceQualityReason" in html
    # Both builders render through the single Phase E workspace body, which
    # carries the evidence panel.
    assert "screeningSubjectWorkspaceBody" in entity_region
    assert "screeningSubjectWorkspaceBody" in person_region
    assert "screeningQueueEvidenceReadinessPanel(row, config.legacyResults, config.legacyContext)" in body_region
    # One fact, one place: the retired repeat-surfaces must not come back.
    assert "ComplyAdvantage Mesh Screening Summary" not in html
    assert "Officer Decision" not in panel_region
    assert "Client / application" not in panel_region
    assert "evidence_quality_reason" in reason_region
    assert "screeningQueueEvidenceQualityReason(row, qualityLabel, items)" in panel_region
    assert "Provider screening completed with no hits; detailed source evidence is not applicable." in reason_region
    assert "Provider screening failed before detailed evidence was available." in reason_region
    assert "Provider did not return source link." in reason_region
    assert "Evidence readiness: " in panel_region
    assert "View provider references" in panel_region
    assert "data-screening-technical-details" in panel_region
    assert "SCREENING_SOURCE_UNAVAILABLE_MESSAGE" in panel_region
    assert "Source unavailable from provider payload" in html
    assert "Detailed provider evidence is partial or unavailable for this screening result." in panel_region
    assert "Provider case IDs" in panel_region
    assert "Provider alert IDs" in panel_region
    assert "Provider risk IDs" in panel_region
    assert "Current risks" in panel_region
    assert "Unresolved current risks" in panel_region
    assert "JSON.stringify" not in panel_region


def test_backoffice_screening_detail_preserves_queue_evidence_when_merging_review_rows():
    html = BACKOFFICE_HTML.read_text()

    merge_region = _function_region(html, "getMergedScreeningReviewRow", "buildScreeningTriageSubjects")

    assert "function screeningQueueEvidenceObjectIsEmpty" in html
    assert "['screening_evidence', 'evidence_summary'].forEach" in merge_region
    assert "screeningQueueEvidenceObjectIsEmpty(merged[field])" in merge_region
    assert "screeningQueueEvidenceObjectIsEmpty(queueRow[field])" in merge_region
    assert "merged[field] = queueRow[field]" in merge_region


def test_backoffice_screening_queue_source_links_are_conditional():
    html = BACKOFFICE_HTML.read_text()

    card_region = _function_region(html, "screeningQueueEvidenceItemCard", "screeningQueueEvidenceReadinessPanel")

    assert "var sourceUrl = item.source_url || ''" in card_region
    assert "Open source" in card_region
    # PR-A (audit C2): the source link is gated on safeUrl(), so a provider
    # javascript:/data: URL falls through to the unavailable message instead of
    # rendering a clickable payload. Still conditional — an absent URL shows the
    # unavailable copy exactly as before.
    assert "safeUrl(sourceUrl) ? '<div" in card_region
    assert "escapeHtml(safeUrl(sourceUrl))" in card_region
    assert "SCREENING_SOURCE_UNAVAILABLE_MESSAGE" in card_region
    assert "Source unavailable from provider payload" in html
    assert "raw JSON" not in card_region.lower()


def test_backoffice_screening_queue_filter_bar_is_universal_and_not_redundant():
    html = BACKOFFICE_HTML.read_text()

    filter_region = html[html.index("<!-- ═══════════════ SCREENING QUEUE"):html.index("screening-provider-status-panel")]
    read_filters_region = _function_region(html, "readScreeningQueueFiltersFromDom", "screeningQueueQueryParams")
    directors_region = _function_region(html, "openDirectorsUboScreening", "openDirectorsUboDocuments")

    assert "Search subject, company, ARF, or Mesh reference" in filter_region
    assert "screening-filter-application-ref" not in filter_region
    assert "Application reference" not in filter_region
    assert "application_ref: val('screening-filter-application-ref')" not in read_filters_region
    assert "document.getElementById('screening-queue-search')" in directors_region


def test_backoffice_screening_queue_hides_individual_filter_until_backend_reports_other_people():
    html = BACKOFFICE_HTML.read_text()

    filter_region = html[html.index("<!-- ═══════════════ SCREENING QUEUE"):html.index("screening-provider-status-panel")]
    type_region = _function_region(html, "updateScreeningQueueTypeFilterOptions", "readScreeningQueueFiltersFromDom")

    assert '<option value="individual">Individual</option>' not in filter_region
    assert "Other person" in type_region
    assert "available_type_filters" in type_region
    assert "item.value === 'individual'" in type_region


def test_backoffice_screening_queue_lazy_loads_full_evidence_without_blocking_detail_view():
    html = BACKOFFICE_HTML.read_text()

    lazy_region = _function_region(html, "screeningQueueRowHasFullEvidence", "screeningDispositionLabel")
    open_region = _function_region(html, "openScreeningReviewByRow", "screeningDispositionLabel")

    assert "function ensureScreeningQueueRowEvidence" in lazy_region
    assert "include_evidence=1" in lazy_region
    assert "application_ref=' + encodeURIComponent(row.application_ref)" in lazy_region
    assert "search=' + encodeURIComponent(row.subject_name || row.company_name || '')" in lazy_region
    assert "screeningQueueRowsMatch(candidate, row)" in lazy_region
    assert "mergeScreeningQueueRowState(rowKey, Object.assign({}, fullRow" in lazy_region
    assert "refreshOpenScreeningReviewForRow(updated)" in lazy_region
    assert "function renderScreeningReviewOpeningShell" in lazy_region
    assert "function openScreeningReviewByRow" in lazy_region
    assert "async function openScreeningReviewByRow" not in lazy_region
    assert "row = await ensureScreeningQueueRowEvidence(rowKey)" not in lazy_region
    assert "ensureScreeningQueueRowEvidence(rowKey);" in open_region
    assert "renderScreeningReviewOpeningShell(row);" in open_region
    assert "openScreeningReview(row.application_ref, row.subject_type, row.subject_name);" in open_region
    assert open_region.index("ensureScreeningQueueRowEvidence(rowKey);") < open_region.index("openScreeningReview(row.application_ref")


def test_backoffice_screening_evidence_panel_has_loading_and_error_states():
    html = BACKOFFICE_HTML.read_text()

    panel_region = _function_region(html, "screeningQueueEvidenceReadinessPanel", "providerIndicatorDetails")

    assert "data-screening-evidence-loading" in panel_region
    assert "Detailed provider evidence is loading." in panel_region
    assert "data-screening-evidence-error" in panel_region
    assert "Summary evidence remains available." in panel_region


def test_backoffice_person_review_prefers_screening_declared_pep_truth():
    html = BACKOFFICE_HTML.read_text()

    assert "function declaredPepFromScreeningRecord" in html
    region = _function_region(html, "buildPersonScreeningReviewCard", "renderScreeningReviewPanel")
    assert "declaredPepFromScreeningRecord(screeningRecord, person.pep)" in region
    assert "screeningTagBadge('Declared PEP'" in region
    # Phase E: the explicit Yes/No answer renders once in the workspace status
    # strip, still sourced from the screening-record truth (never person.pep alone).
    assert "['Declared PEP', personDeclaredPep ? 'Yes' : 'No']" in region
    assert "['Declared PEP', person.pep" not in region


def test_backoffice_screening_disposition_modal_matches_api_contract():
    html = BACKOFFICE_HTML.read_text()

    modal_start = html.index("<!-- ═══════════════ SCREENING DISPOSITION MODAL")
    modal_end = html.index('<div class="modal-overlay" id="modal-idv-resolution">', modal_start)
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
    assert "Clear as False Positive requires Onboarding Officer, SCO, or Admin role" in submit_region
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
    assert "Clear as False Positive requires Onboarding Officer, SCO, or Admin role." in queue_region


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
    assert "screeningTruthBlocksApproval(screeningTruth)" in approval_region
    assert "caseCommandScreeningTruthCopy(screeningTruth)" in approval_region


def test_backoffice_screening_truth_fallback_does_not_mark_uncleared_matches_ready():
    html = BACKOFFICE_HTML.read_text()

    summary_region = _function_region(html, "deriveScreeningTruthSummary", "screeningTruthBlockedReasons")
    assert "screeningProviderClear" in summary_region
    assert "hasFormallyClearedMatch" in summary_region
    assert "hasUnclearedCompletedMatch" in summary_region
    assert "item.approval_blocking" in summary_region
    assert "screening_gate_ready: screeningGateReady" in summary_region
    assert "approval_ready: screeningGateReady" in summary_region
    assert "terminal && (canonicalState === 'completed_clear' || canonicalState === 'completed_match')" not in summary_region

    helper_region = _function_region(html, "screeningTruthBlocksApproval", "getApplicationScreeningSummary")
    assert "approval_blocking === true" in helper_region
    assert "screening_gate_ready === false" in helper_region


def test_backoffice_legacy_match_without_api_status_remains_terminal_match_fallback():
    html = BACKOFFICE_HTML.read_text()
    mode_region = _function_region(html, "screeningProviderModeFromRecord", "deriveScreeningTruth")

    assert "record.matched && Array.isArray(record.results) && record.results.length" in mode_region
    assert "return 'live_provider'" in mode_region


def test_phase_f_stored_result_sentence_only_when_non_duplicate_nuance():
    """Phase F (F5): the status-strip stored-result sentence renders ONLY when
    it states something no badge on the page states. The generic 'matches
    requiring officer review' variants are retired; the declared-PEP-with-no-
    provider-hits sentence (declared-PEP truthfulness invariant) and the
    missing person-level-record sentence survive."""
    html = BACKOFFICE_HTML.read_text()

    # Retired generic variants — duplicated the Review Required badge and the
    # disposition bar.
    assert "Provider recorded company/entity matches requiring officer review." not in html
    assert "Provider recorded one or more potential matches that require officer review." not in html
    assert "No company/entity provider matches recorded in the stored screening run." not in html
    assert "No provider matches were recorded for this screening subject." not in html
    assert "No screening run is recorded yet for this application." not in html

    # Surviving nuance variants — stated nowhere else on the page.
    person_region = _function_region(html, "buildPersonScreeningReviewCard", "renderScreeningReviewPanel")
    assert (
        "Self-declared PEP review is required even though no provider matches "
        "were recorded for this screening subject." in person_region
    )
    assert (
        "The stored screening report does not contain a person-level screening "
        "record for this subject." in person_region
    )
    # The declared-PEP sentence renders only in the declared-PEP-no-match case.
    assert "personDeclaredPep && !facts.matched" in person_region
