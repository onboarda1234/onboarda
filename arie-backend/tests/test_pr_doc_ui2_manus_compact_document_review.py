from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _backoffice_html() -> str:
    return (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")


def _portal_html() -> str:
    return (ROOT / "arie-portal.html").read_text(encoding="utf-8")


def _function_region(html: str, start_name: str, next_name: str) -> str:
    start = html.index(f"function {start_name}")
    end = html.index(f"function {next_name}", start)
    return html[start:end]


def _kyc_region(html: str) -> str:
    start = html.index('id="detail-tab-kyc-docs"')
    end = html.index('id="detail-tab-screening"', start)
    return html[start:end]


def test_application_review_default_rows_are_compact_and_not_audit_heavy():
    html = _backoffice_html()
    card_renderer = _function_region(html, "renderUnifiedKycDocumentCard", "enhancedRequirementBackOfficeGroup")
    default_row = card_renderer.split("renderDocumentAuditDetails", 1)[0]

    assert "document-review-identity" in default_row
    assert "document-review-status-actions" in default_row
    assert "renderDocumentCompactSummary(issue, blocker, nextAction, relianceState)" in default_row
    assert "renderDocumentDirectActions(app, doc, groupKey, relianceState, expectedSlot)" in default_row
    assert "document-review-fields" not in default_row
    assert "renderDocumentReviewField(" not in default_row

    hidden_by_default = [
        "Policy ID/version",
        "Agent run ID",
        "Evidence hash",
        "Confidence",
        "Lifecycle context",
        "Verification timestamp",
        "Uploaded by",
        "Full check result list",
        "buildVerificationResultsHtml",
    ]
    for label in hidden_by_default:
        assert label not in default_row


def test_details_are_collapsed_by_default_and_retain_audit_fields():
    html = _backoffice_html()
    details = _function_region(html, "renderDocumentAuditDetails", "renderUnifiedKycDocumentCard")
    actions = _function_region(html, "renderDocumentDirectActions", "buildVerificationResultsHtml")
    secondary = _function_region(html, "renderDocumentSecondaryActions", "renderDocumentDirectActions")

    assert '<details class="document-review-details">' in details
    assert '<details class="document-review-details" open' not in details
    for audit_field in [
        "Verification details",
        "Policy ID/version",
        "Lifecycle context",
        "Agent run ID",
        "Evidence hash",
        "Verification timestamp",
        "Uploaded by",
        "Officer action history",
        "buildVerificationResultsHtml",
    ]:
        assert audit_field in details
    assert "More ▾" in secondary
    assert "Re-Verify" in secondary
    assert "renderVerificationCoverageSummary(doc, policy)" in details
    assert "buildVerificationResultsHtml(doc.verification_results, coverage)" in details


def test_large_ai_advisory_banner_is_reduced_in_kyc_documents_tab():
    html = _backoffice_html()
    region = _kyc_region(html)

    assert "document-review-helper-note" in region
    assert "Agent 1 assists document review. Officers must review exceptions before relying on documents." in region
    assert "AI Verification Results — Advisory Only" not in region
    assert "ai-advisory-banner" not in region


def test_action_grouping_and_backoffice_sections_are_preserved():
    html = _backoffice_html()
    renderer = _function_region(html, "renderStandardKycDocumentTaxonomy", "buildEnhancedRequirementsPanelSummary")

    for group in ["Action required", "Missing", "Verified", "Optional / additional"]:
        assert group in html

    for section in [
        "A — Corporate Entity Documents",
        "B — Directors & UBO Identity Documents",
        "D — Other Documents",
    ]:
        assert section in renderer

    assert "C — Enhanced Evidence Documents" in html
    assert "E — Portal Disclosures" in html
    assert "F — Internal Controls" in html
    assert "G — Verification History" in html


def test_portal_slot_documents_show_expected_slot_not_unclassified():
    html = _backoffice_html()
    type_helper = _function_region(html, "documentExpectedTypeLabel", "documentPrimaryIssue")
    card_renderer = _function_region(html, "renderUnifiedKycDocumentCard", "enhancedRequirementBackOfficeGroup")
    needs_type_helper = _function_region(html, "documentNeedsTypeSelection", "documentExpectedTypeLabel")

    assert "if (expectedSlot && expectedSlot.label) return expectedSlot.label;" in type_helper
    assert "if (expectedSlot || linkedRequirement) return false;" in needs_type_helper
    assert "Expected from portal slot" in card_renderer
    assert "Unclassified" not in card_renderer


def test_unknown_ad_hoc_documents_can_still_show_needs_document_type():
    html = _backoffice_html()
    type_helper = _function_region(html, "documentExpectedTypeLabel", "documentPrimaryIssue")
    needs_type_helper = _function_region(html, "documentNeedsTypeSelection", "documentExpectedTypeLabel")

    assert "Needs document type" in type_helper
    assert "supporting_document" in needs_type_helper
    assert "unknown" in needs_type_helper
    assert "unclassified" in needs_type_helper


def test_view_download_are_direct_for_uploaded_docs_and_disabled_only_when_no_file():
    html = _backoffice_html()
    actions = _function_region(html, "renderDocumentDirectActions", "renderDocumentAuditDetails")
    missing_renderer = _function_region(html, "renderMissingKycDocumentRow", "renderDocumentActionGroupShell")

    assert "documentHasUploadedFile(doc)" in actions
    assert "viewBackofficeDocument" in actions
    assert "downloadBackofficeDocument" in actions
    assert ">View</button>" in actions
    assert ">Download</button>" in actions
    assert "disabled>View</button>" in actions
    assert "disabled>Download</button>" in actions
    assert ">Upload</button>" in actions
    assert "Request from client" in actions
    assert "No document uploaded" in missing_renderer


def test_officer_friendly_labels_replace_internal_default_row_language():
    html = _backoffice_html()
    card_renderer = _function_region(html, "renderUnifiedKycDocumentCard", "enhancedRequirementBackOfficeGroup")
    default_row = card_renderer.split("renderDocumentAuditDetails", 1)[0]

    for old_label in [
        "Automated reliance",
        "Verification policy is not mapped for this expected document type",
        "Reliance status",
        "Red Flags",
        "Material issues and reliance evidence",
        "Technical / audit details",
    ]:
        assert old_label not in default_row

    assert "System setup issue: verification policy missing." in html
    assert "Document status" in html
    assert "Issue" in html
    assert "Details" in html


def test_system_file_access_issue_is_marked_as_system_issue_not_document_failure():
    html = _backoffice_html()
    state_helper = _function_region(html, "documentRelianceDisplayState", "renderRelianceBadge")
    issue_helper = _function_region(html, "documentPrimaryIssue", "documentBlocksDisplay")

    assert "file_not_accessible" in state_helper
    assert "System issue" in state_helper
    assert "system-issue" in html
    assert "System issue — document file was not accessible for analysis." in issue_helper
    assert "Approval blocked" in html


def test_portal_upload_ui_contract_is_not_redesigned():
    portal = _portal_html()

    assert "data-reliance-state" in portal
    assert "document_reliance_state" in portal
    assert "Upload received - verification pending." in portal


def test_top_action_bar_keeps_primary_actions_visible_and_moves_secondary_into_more_menu():
    html = _backoffice_html()
    detail_view = html[html.index('id="view-app-detail"'):html.index('<div id="detail-case-command-centre">')]

    assert "Approve" in detail_view
    assert "More Info" in detail_view
    assert "More ▾" in detail_view
    assert "rejectApplication()" in detail_view
    assert "openOverrideModal()" in detail_view
    assert "escalateCase()" in detail_view
    assert "reassignCase()" in detail_view
    assert "openExportPackModal()" in detail_view


def test_sumsub_admin_reconciliation_notice_is_suppressed_from_application_review():
    html = _backoffice_html()
    notice_helper = _function_region(html, "canViewUnmatchedSumsubWebhookNotice", "renderSumsubIdvPanel")

    assert "return false;" in notice_helper
