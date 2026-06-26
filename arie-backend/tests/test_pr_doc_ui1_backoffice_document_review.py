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
    card_renderer = _function_region(html, "renderUnifiedKycDocumentCard", "renderMissingKycDocumentRow")

    assert "if (expectedSlot && expectedSlot.label) return expectedSlot.label;" in type_helper
    assert "documentReviewContextLine(app, doc, linkedRequirement, expectedSlot)" in card_renderer
    assert "Expected from portal slot" not in card_renderer
    assert "Needs document type" in type_helper
    assert "Unclassified" not in card_renderer


def test_uploaded_document_actions_move_into_more_menu_while_primary_cta_stays_compact():
    html = _backoffice_html()
    actions = _function_region(html, "renderDocumentDirectActions", "renderDocumentAuditDetails")
    audit_toggle = _function_region(html, "renderDocumentAuditToggleAction", "toggleDocumentTechnicalAudit")
    toggle = _function_region(html, "toggleDocumentTechnicalAudit", "renderDocumentPrimaryAction")
    secondary = _function_region(html, "renderDocumentSecondaryActions", "renderDocumentAuditToggleAction")
    primary = _function_region(html, "renderDocumentPrimaryAction", "renderDocumentDirectActions")

    assert "renderDocumentAuditToggleAction(doc)" in actions
    assert actions.index("renderDocumentAuditToggleAction(doc)") < actions.index("renderDocumentPrimaryAction(app, doc, state, expectedSlot)")
    assert "renderDocumentPrimaryAction(app, doc, state, expectedSlot)" in actions
    assert "openBoDocUploadForExpectedSlot" in primary
    assert "renderDocumentPrimaryAction(app, doc, state, expectedSlot)" in actions
    assert "viewBackofficeDocument" in primary
    assert "downloadBackofficeDocument" in secondary
    assert ">View<" in primary
    assert ">Download<" in secondary
    assert "Accept with reason" in secondary
    assert "Request replacement" in secondary
    assert "Reject" in secondary
    assert "Technical audit details" not in secondary
    assert "Technical audit details" in audit_toggle
    assert 'aria-expanded="false"' in audit_toggle
    assert "panel.hidden = !willOpen;" in toggle
    assert "Review required" not in primary
    assert ">Upload</button>" in primary


def test_missing_documents_disable_view_and_download():
    html = _backoffice_html()
    missing_renderer = _function_region(html, "renderMissingKycDocumentRow", "renderDocumentActionGroupShell")
    actions = _function_region(html, "renderDocumentDirectActions", "renderDocumentAuditDetails")
    primary = _function_region(html, "renderDocumentPrimaryAction", "renderDocumentDirectActions")
    secondary = _function_region(html, "renderDocumentSecondaryActions", "renderDocumentAuditToggleAction")

    assert "No document uploaded" in missing_renderer
    assert "Corporate entity document" in missing_renderer
    assert "disabled>View</button>" in secondary
    assert "disabled>Download</button>" in secondary
    assert ">Upload</button>" in primary


def test_backoffice_upload_state_is_session_bound_and_reset_on_navigation():
    html = _backoffice_html()
    upload_helpers = _function_region(html, "setBoDocUploadStatus", "refreshCurrentKycDocumentsDetail")
    submit = _function_region(html, "submitBoDocUpload", "viewBackofficeDocument")
    detail = _function_region(html, "renderAuthoritativeAppDetail", "openAppDetail")
    tabs = _function_region(html, "switchDetailTab", "safeParseAuditDetail")
    show_view = _function_region(html, "showView", "normalizeRiskLevel")

    assert 'id="bo-upload-session-app-id"' in html
    assert 'id="bo-upload-session-app-ref"' in html
    assert "var BO_DOC_UPLOAD_SESSION = { appId: '', appRef: '' };" in html
    assert "function resetBoDocUploadState" in upload_helpers
    assert "function beginBoDocUploadSession" in upload_helpers
    assert "function isBoDocUploadSessionBoundToActiveApp" in upload_helpers
    assert "BO_DOC_UPLOAD_SESSION = { appId: '', appRef: '' };" in upload_helpers
    assert "bo-upload-file" in upload_helpers
    assert "bo-upload-doc-type" in upload_helpers
    assert "bo-upload-notes" in upload_helpers
    assert "bo-upload-person-id" in upload_helpers
    assert "bo-upload-person-type" in upload_helpers
    assert "bo-upload-session-app-id" in upload_helpers
    assert "bo-upload-session-app-ref" in upload_helpers

    assert "if (typeof resetBoDocUploadState === 'function') resetBoDocUploadState({ reason: 'application_changed' });" in detail
    assert "if (tab !== 'kyc-docs' && typeof resetBoDocUploadState === 'function') resetBoDocUploadState({ reason: 'detail_tab_changed' });" in tabs
    assert "if (name !== 'app-detail' && typeof resetBoDocUploadState === 'function') resetBoDocUploadState({ reason: 'view_changed' });" in show_view
    assert "Upload cancelled because the active application changed. Please reopen upload for this application." in submit
    assert "if (!isBoDocUploadSessionBoundToActiveApp())" in submit
    assert "formData.append('upload_session_app_id', sessionAppId || '')" in submit
    assert "formData.append('upload_session_app_ref', sessionAppRef || '')" in submit


def test_generic_backoffice_upload_is_gated_by_ordinary_kyc_status():
    html = _backoffice_html()
    gate_helpers = _function_region(html, "boNormalizedStatus", "loadEnvironment")
    panel_state = _function_region(html, "updateKycDocumentsPanelState", "buildDocumentVerificationHistorySummary")
    primary = _function_region(html, "renderDocumentPrimaryAction", "renderDocumentDirectActions")
    upload_helpers = _function_region(html, "setBoDocUploadStatus", "refreshCurrentKycDocumentsDetail")

    assert "function isBoOrdinaryKycUploadAllowed" in gate_helpers
    assert "boNormalizedStatus(app) === 'kyc_documents'" in gate_helpers
    assert "boKycUploadPreApprovalSatisfied(app)" in gate_helpers
    assert "Officer upload is available only while the application is in KYC Documents." in gate_helpers
    assert "updateBoDocUploadAvailability(app)" in panel_state
    assert "if (!isBoOrdinaryKycUploadAllowed(app))" in primary
    assert "disabled title=" in primary
    assert "if (!isBoOrdinaryKycUploadAllowed(currentApp))" in upload_helpers
    assert "btn.disabled = !allowed" in upload_helpers
    assert "resetBoDocUploadState({ skipAvailability: true, reason: 'upload_not_allowed' })" in upload_helpers


def test_default_row_is_action_first_and_details_hold_audit_fields():
    html = _backoffice_html()
    card_renderer = _function_region(html, "renderUnifiedKycDocumentCard", "renderMissingKycDocumentRow")
    default_row = card_renderer.split("renderDocumentAuditDetails", 1)[0]
    details = _function_region(html, "renderDocumentAuditDetails", "documentReviewContextLine")
    technical = _function_region(html, "buildVerificationResultsHtml", "renderDocumentAuditDetails")

    for visible in ["renderDocumentCompactSummary", "document-review-status-actions", "documentReviewContextLine", "renderDocumentPrimaryAction"]:
        assert visible in default_row or visible in _function_region(html, "renderDocumentDirectActions", "renderDocumentAuditDetails")

    for hidden_by_default in ["Policy ID/version", "Agent run ID", "Evidence hash", "Verification timestamp", "Uploaded by", "Lifecycle context", "Verification details", "Expected from portal slot"]:
        assert hidden_by_default not in default_row

    for audit_field in [
        "Verification timestamp",
        "Uploaded by",
        "Technical audit details",
    ]:
        assert audit_field in details or audit_field in technical or audit_field in html
    assert "Portal slot/source" not in technical
    assert "Lifecycle context" not in technical
    assert "<summary>Details</summary>" not in details


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
