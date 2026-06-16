from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _backoffice_html() -> str:
    return (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")


def _function_region(html: str, start_name: str, next_name: str) -> str:
    start = html.index(f"function {start_name}")
    end = html.index(f"function {next_name}", start)
    return html[start:end]


def test_application_review_documents_are_grouped_by_officer_action():
    html = _backoffice_html()
    renderer = _function_region(html, "renderStandardKycDocumentTaxonomy", "buildEnhancedRequirementsPanelSummary")

    assert "renderDocumentReviewActionGroups(sections.entity)" in renderer
    assert "Action required" in html
    assert "Missing" in html
    assert "Verified" in html
    assert "Optional / additional" in html
    assert "renderDocumentActionGroupShell" in html
    assert "document-action-groups" in html


def test_application_review_preserves_backoffice_kyc_taxonomy_sections():
    html = _backoffice_html()
    renderer = _function_region(html, "renderStandardKycDocumentTaxonomy", "buildEnhancedRequirementsPanelSummary")

    for section in [
        "A — Corporate Entity Documents",
        "B — Directors & UBO Identity Documents",
        "D — Other Documents",
    ]:
        assert section in renderer

    assert "C — Enhanced Evidence Documents" in html
    assert "renderEnhancedEvidenceDocumentsGroupHtml(requirements)" in renderer
    assert "E — Portal Disclosures" in html
    assert "F — Internal Controls" in html
    assert "G — Verification History" in html
    assert "renderDocumentReviewActionGroups(sections.entity)" in renderer
    assert "renderDocumentReviewActionGroups(sections.person)" in renderer
    assert "renderDocumentReviewActionGroups(sections.other)" in renderer
    assert renderer.index("A — Corporate Entity Documents") < renderer.index("B — Directors & UBO Identity Documents")
    assert renderer.index("B — Directors & UBO Identity Documents") < renderer.index("renderEnhancedEvidenceDocumentsGroupHtml")
    assert renderer.index("renderEnhancedEvidenceDocumentsGroupHtml") < renderer.index("D — Other Documents")


def test_portal_slot_document_shows_expected_type_not_unclassified():
    html = _backoffice_html()
    type_helper = _function_region(html, "documentExpectedTypeLabel", "documentPrimaryIssue")
    card_renderer = _function_region(html, "renderUnifiedKycDocumentCard", "enhancedRequirementBackOfficeGroup")

    assert "if (expectedSlot && expectedSlot.label) return expectedSlot.label;" in type_helper
    assert "Expected from portal slot" in card_renderer
    assert "Needs document type" in type_helper
    assert "Unclassified" not in card_renderer


def test_view_and_download_are_visible_for_uploaded_documents():
    html = _backoffice_html()
    actions = _function_region(html, "renderDocumentDirectActions", "renderDocumentAuditDetails")

    assert "documentHasUploadedFile(doc)" in actions
    assert "viewBackofficeDocument" in actions
    assert "downloadBackofficeDocument" in actions
    assert ">View<" in actions
    assert ">Download<" in actions
    assert "Accept with reason" in actions
    assert "Request replacement" in actions
    assert "Reject" in actions


def test_missing_documents_disable_view_and_download():
    html = _backoffice_html()
    missing_renderer = _function_region(html, "renderMissingKycDocumentRow", "renderDocumentActionGroupShell")
    actions = _function_region(html, "renderDocumentDirectActions", "renderDocumentAuditDetails")

    assert "No document uploaded" in missing_renderer
    assert "disabled>View</button>" in actions
    assert "disabled>Download</button>" in actions


def test_default_row_is_action_first_and_details_hold_audit_fields():
    html = _backoffice_html()
    card_renderer = _function_region(html, "renderUnifiedKycDocumentCard", "enhancedRequirementBackOfficeGroup")
    default_row = card_renderer.split("renderDocumentAuditDetails", 1)[0]
    details = _function_region(html, "renderDocumentAuditDetails", "renderUnifiedKycDocumentCard")

    for visible in ["renderDocumentCompactSummary", "document-review-status-actions", "View", "Download"]:
        assert visible in default_row or visible in _function_region(html, "renderDocumentDirectActions", "renderDocumentAuditDetails")

    for hidden_by_default in ["Policy ID/version", "Agent run ID", "Evidence hash", "Verification timestamp", "Uploaded by", "Lifecycle context"]:
        assert hidden_by_default not in default_row

    for audit_field in [
        "Verification details",
        "Policy ID/version",
        "Agent run ID",
        "Evidence hash",
        "<summary>Details</summary>",
    ]:
        assert audit_field in details or audit_field in html


def test_approval_blocking_uses_warning_or_error_styling_not_green():
    html = _backoffice_html()

    assert ".document-review-field-value.blocking { color:#991b1b;" in html
    assert "var blockedTone = summary.approval_blocked ? 'red' : '';" in html
    assert "Approval blocked: ' + blockedText, blockedTone" in html


def test_existing_portal_and_sar_scope_are_protected():
    backoffice = _backoffice_html()
    portal = (ROOT / "arie-portal.html").read_text(encoding="utf-8")

    assert "sar_str_active: false" in backoffice
    assert "Future / enterprise. SAR/STR implementation is not active in pilot scope." in backoffice
    assert "data-reliance-state" in portal
    assert "document_reliance_state" in portal
