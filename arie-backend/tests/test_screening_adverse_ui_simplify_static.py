from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _html() -> str:
    return BACKOFFICE_HTML.read_text(encoding="utf-8")


def _function_region(html: str, name: str, next_name: str) -> str:
    start = html.index(f"function {name}")
    end = html.index(f"function {next_name}", start)
    return html[start:end]


def test_screening_subject_list_prioritizes_review_and_deemphasizes_clear_subjects():
    html = _html()
    item = _function_region(html, "screeningTriageSubjectListItem", "setScreeningReviewFocus")
    ordering = _function_region(html, "buildScreeningTriageSubjects", "renderScreeningReviewPanel")

    assert "subjects.sort" in ordering
    assert "screeningTriagePriority" in ordering
    assert 'data-screening-subject-card="' in item
    assert "'clear' : 'review'" in item
    assert "opacity:0.72" in item


def test_screening_review_decision_summary_and_actions_precede_evidence_detail():
    html = _html()
    entity = _function_region(html, "buildEntityScreeningReviewCard", "buildPersonScreeningReviewCard")
    person = _function_region(html, "buildPersonScreeningReviewCard", "buildScreeningTriageSubjects")

    for region in (entity, person):
        assert "screeningDecisionSummaryPanel" in region
        assert "renderInlineScreeningDispositionPanel" in region
        assert "providerResultHighlights" in region
        assert region.index("screeningDecisionSummaryPanel") < region.index("renderInlineScreeningDispositionPanel")
        assert region.index("renderInlineScreeningDispositionPanel") < region.index("providerResultHighlights")


def test_screening_evidence_is_grouped_by_business_category_with_technical_ids_out_of_main_view():
    html = _html()
    group_key = _function_region(html, "providerEvidenceGroupKey", "groupProviderEvidenceHits")
    record_card = _function_region(html, "providerEvidenceRecordCard", "firstEvidenceIndicatorValue")
    highlights = _function_region(html, "providerResultHighlights", "screeningEvidenceArrayText")
    drawer = _function_region(html, "openScreeningEvidenceDrawer", "providerResultHighlights")

    assert "'category'" in group_key
    assert "profileIdentifier" not in group_key
    assert "Provider risk match - review context" in html
    assert "Unclassified Provider Risk" not in html
    assert "Provider case ID" not in record_card
    assert "Provider alert ID" not in record_card
    assert "Provider risk ID" not in record_card
    assert "JSON.stringify" not in highlights
    assert "Evidence groups" in highlights
    assert "Source article link is not available from the ComplyAdvantage payload" in highlights
    assert "Provider case ID" in drawer
    assert "Provider alert ID" in drawer


def test_onboarding_adverse_media_is_in_screening_review_while_monitoring_detail_remains_accessible():
    html = _html()
    company_media = _function_region(html, "companyMonitoringMediaFacts", "declaredPepFromScreeningRecord")
    entity = _function_region(html, "buildEntityScreeningReviewCard", "buildPersonScreeningReviewCard")
    monitoring_detail = _function_region(html, "renderMonitoringAlertDetailView", "openMonitoringAlertDetail")

    assert "provider === 'complyadvantage'" in company_media
    assert "type === 'media' || type === 'adverse_media'" in company_media
    assert "monitoringMedia.results" in entity
    assert "Adverse media" in entity
    assert "monitoringDetailCard('Issue / Evidence'" in monitoring_detail
    assert "renderMonitoringTechnicalDetails" in monitoring_detail


def test_monitoring_adverse_main_view_is_clean_and_technical_payloads_are_collapsed():
    html = _html()
    evidence = _function_region(html, "monitoringAlertProviderEvidenceRows", "monitoringDocumentRefreshContext")
    decision = _function_region(html, "renderMonitoringDecisionSection", "renderMonitoringAssignmentSection")
    technical = _function_region(html, "renderMonitoringTechnicalDetails", "renderMonitoringAlertDetailView")

    assert "Provider case ID" not in evidence
    assert "Provider alert ID" not in evidence
    assert "Source title / reference" in evidence
    assert "Detected date" in evidence
    assert "Start Review" not in decision
    assert "Save Decision" in decision
    assert "<details" in technical
    assert "Raw alert payload" in technical
    assert "Raw provider evidence" in technical
    assert "JSON.stringify(raw, null, 2)" in technical
