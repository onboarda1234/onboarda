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
    card_renderer = _function_region(html, "renderUnifiedKycDocumentCard", "renderMissingKycDocumentRow")
    default_row = card_renderer.split("renderDocumentAuditDetails", 1)[0]

    assert "document-review-identity" in default_row
    assert "document-review-status-actions" in default_row
    assert "renderDocumentCompactSummary(issue, blocker, nextAction, relianceState)" in default_row
    assert "renderDocumentDirectActions(app, doc, groupKey, relianceState, expectedSlot)" in default_row
    assert "documentReviewContextLine(app, doc, linkedRequirement, expectedSlot)" in default_row
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
        "Expected from portal slot",
        "Verification details",
    ]
    for label in hidden_by_default:
        assert label not in default_row


def test_details_are_collapsed_by_default_and_retain_technical_audit_fields_only():
    html = _backoffice_html()
    details = _function_region(html, "renderDocumentAuditDetails", "documentReviewContextLine")
    secondary = _function_region(html, "renderDocumentSecondaryActions", "renderDocumentAuditToggleAction")
    audit_toggle = _function_region(html, "renderDocumentAuditToggleAction", "toggleDocumentTechnicalAudit")
    technical = _function_region(html, "buildVerificationResultsHtml", "renderDocumentAuditDetails")

    assert '<div class="document-review-audit-panel" hidden>' in details
    for audit_field in [
        "Verification timestamp",
        "Uploaded by",
        "Officer action history",
        "buildVerificationResultsHtml(doc.verification_results, coverage, auditContext)",
    ]:
        assert audit_field in details or audit_field in technical
    assert "More ▾" in secondary
    assert "Re-verify" in secondary
    assert "Technical audit details" not in secondary
    assert "Technical audit details" in audit_toggle
    assert 'aria-expanded="false"' in audit_toggle
    assert "renderVerificationCoverageSummary(doc, policy)" not in details
    assert "Verification coverage" not in details
    assert "Verification details" not in details
    assert "Portal slot/source" not in technical
    assert "Lifecycle context" not in technical
    assert "Document type" not in technical


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
    card_renderer = _function_region(html, "renderUnifiedKycDocumentCard", "renderMissingKycDocumentRow")
    needs_type_helper = _function_region(html, "documentNeedsTypeSelection", "documentExpectedTypeLabel")

    assert "if (expectedSlot && expectedSlot.label) return expectedSlot.label;" in type_helper
    assert "if (expectedSlot || linkedRequirement) return false;" in needs_type_helper
    assert "documentReviewContextLine(app, doc, linkedRequirement, expectedSlot)" in card_renderer
    assert "Expected from portal slot" not in card_renderer
    assert "Unclassified" not in card_renderer


def test_unknown_ad_hoc_documents_can_still_show_needs_document_type():
    html = _backoffice_html()
    type_helper = _function_region(html, "documentExpectedTypeLabel", "documentPrimaryIssue")
    needs_type_helper = _function_region(html, "documentNeedsTypeSelection", "documentExpectedTypeLabel")

    assert "Needs document type" in type_helper
    assert "supporting_document" in needs_type_helper
    assert "unknown" in needs_type_helper
    assert "unclassified" in needs_type_helper


def test_secondary_menu_contains_view_download_and_missing_state_keeps_disabled_items():
    html = _backoffice_html()
    actions = _function_region(html, "renderDocumentDirectActions", "renderDocumentAuditDetails")
    primary = _function_region(html, "renderDocumentPrimaryAction", "renderDocumentDirectActions")
    audit_toggle = _function_region(html, "renderDocumentAuditToggleAction", "toggleDocumentTechnicalAudit")
    secondary = _function_region(html, "renderDocumentSecondaryActions", "renderDocumentAuditToggleAction")
    missing_renderer = _function_region(html, "renderMissingKycDocumentRow", "renderDocumentActionGroupShell")

    assert "renderDocumentAuditToggleAction(doc)" in actions
    assert actions.index("renderDocumentAuditToggleAction(doc)") < actions.index("renderDocumentPrimaryAction(app, doc, state, expectedSlot)")
    assert "renderDocumentPrimaryAction(app, doc, state, expectedSlot)" in actions
    assert "openBoDocUploadForExpectedSlot" in primary
    assert "renderDocumentPrimaryAction(app, doc, state, expectedSlot)" in actions
    assert "viewBackofficeDocument" in primary
    assert "downloadBackofficeDocument" in secondary
    assert '>View</button>' in primary
    assert '>Download</button>' in secondary
    assert 'disabled>View</button>' in secondary
    assert 'disabled>Download</button>' in secondary
    assert "Technical audit details" not in secondary
    assert "Technical audit details" in audit_toggle
    assert ">Upload</button>" in primary
    assert "Request from client" in secondary
    assert "No document uploaded" in missing_renderer
    assert "Required document missing. Request from client or upload document before approval." in html
    assert "KYC_MISSING_REQUIRED_DOCUMENT_MESSAGE" in missing_renderer
    assert "renderDocumentCompactSummary(KYC_MISSING_REQUIRED_DOCUMENT_MESSAGE, 'None', 'No action required', state)" in missing_renderer
    assert "renderDocumentCompactSummary('No document uploaded.', 'Approval', 'Request from client', state)" not in missing_renderer


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
        "Portal slot/source",
        "Lifecycle context",
    ]:
        assert old_label not in default_row

    assert "Verification policy missing. Admin setup is required before automated verification can run. Manual review is required before relying on this document." in html
    assert "System setup issue: verification policy missing." not in html
    assert "Issue" in html
    assert "<summary>Details</summary>" not in html


def test_kyc_documents_policy_and_missing_copy_are_standalone_messages():
    html = _backoffice_html()
    issue_helper = _function_region(html, "documentPrimaryIssue", "documentBlocksDisplay")
    summary = _function_region(html, "renderDocumentCompactSummary", "renderDocumentSecondaryActions")
    missing_renderer = _function_region(html, "renderMissingKycDocumentRow", "renderDocumentActionGroupShell")

    assert "KYC_MISSING_REQUIRED_DOCUMENT_MESSAGE" in issue_helper
    assert "KYC_VERIFICATION_POLICY_MISSING_MESSAGE" in issue_helper
    assert "Required document missing. Request from client or upload document before approval." in html
    assert "Verification policy missing. Admin setup is required before automated verification can run. Manual review is required before relying on this document." in html
    assert "isStandaloneKycDocumentIssue(issueText)" in summary
    assert "summaryParts.push(issueText);" in summary
    assert "renderDocumentCompactSummary(KYC_MISSING_REQUIRED_DOCUMENT_MESSAGE, 'None', 'No action required', state)" in missing_renderer
    assert "No document uploaded. Approval. Next: Request from client." not in html
    assert "System setup issue: verification policy missing. Approval blocked. Next: Request replacement or reject." not in html
    assert "Approval. Next:" not in html


def test_show_more_is_available_for_long_issue_copy_only_inside_compact_summary():
    html = _backoffice_html()
    summary = _function_region(html, "renderDocumentCompactSummary", "renderDocumentSecondaryActions")
    finding_helpers = _function_region(html, "collapseDocumentReviewFindingText", "renderDocumentCompactSummary")
    compact_summary_surface = finding_helpers + summary

    assert "Show more" in compact_summary_surface
    assert "document-review-issue-text clamped" in compact_summary_surface
    assert "document-review-issue-more" in compact_summary_surface


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
