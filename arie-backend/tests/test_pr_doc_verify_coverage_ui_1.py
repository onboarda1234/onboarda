from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _backoffice_html() -> str:
    return (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")


def _function_region(html: str, start_name: str, next_name: str) -> str:
    start = html.index(f"function {start_name}")
    end = html.index(f"function {next_name}", start)
    return html[start:end]


def test_verification_coverage_helper_tracks_expected_checks_and_system_blockers():
    html = _backoffice_html()
    helper = _function_region(html, "buildDocumentVerificationCoverage", "renderVerificationCoverageChip")

    for expected in [
        "documentPolicyExpectedChecks(policy)",
        "missingExpectedChecks",
        "Verification incomplete — expected checks missing.",
        "Verification incomplete — file could not be accessed.",
        "Manual review only — automated runtime checks are not expected for this policy.",
        "verification policy missing for this document",
        "file_not_accessible",
    ]:
        assert expected in helper


def test_verification_coverage_helper_is_not_rendered_in_main_details_panel():
    html = _backoffice_html()
    details = _function_region(html, "renderDocumentAuditDetails", "documentReviewContextLine")

    assert "renderVerificationCoverageSummary(doc, policy)" not in details
    assert "Verification coverage" not in details


def test_default_document_row_stays_compact_and_avoids_repeating_audit_payloads():
    html = _backoffice_html()
    card = _function_region(html, "renderUnifiedKycDocumentCard", "renderMissingKycDocumentRow")
    default_row = card.split("renderDocumentAuditDetails", 1)[0]

    for expected in [
        "renderDocumentCompactSummary(issue, blocker, nextAction, relianceState)",
        "renderDocumentDirectActions(app, doc, groupKey, relianceState, expectedSlot)",
        "documentReviewContextLine(app, doc, linkedRequirement, expectedSlot)",
        "File: ",
    ]:
        assert expected in default_row

    for hidden in [
        "Verification coverage",
        "Technical audit details",
        "Policy ID/version",
        "Agent run ID",
        "Evidence hash",
        "Verification timestamp",
        "Uploaded by",
        "Confidence",
        "Full check result list",
        "Expected from portal slot",
        "Verification details",
    ]:
        assert hidden not in default_row


def test_row_actions_keep_one_visible_primary_action_and_move_secondary_actions_into_more():
    html = _backoffice_html()
    actions = _function_region(html, "renderDocumentDirectActions", "buildVerificationResultsHtml")
    primary = _function_region(html, "renderDocumentPrimaryAction", "renderDocumentDirectActions")

    for expected in [
        "renderDocumentPrimaryAction(app, doc, state, expectedSlot)",
        "renderDocumentSecondaryActions(app, doc, state)",
    ]:
        assert expected in actions
    secondary = _function_region(html, "renderDocumentSecondaryActions", "renderDocumentDirectActions")
    for expected in [
        "viewBackofficeDocument",
        "downloadBackofficeDocument",
        "Accept with reason",
        "Request replacement",
        "Reject",
        "Re-Verify",
        "More ▾",
    ]:
        assert expected in secondary
    assert "verifyBackofficeDocument" in secondary
    for label in ["Resolve issue", "Review required", "Upload", "Waiting", "Verified"]:
        assert label in primary


def test_audit_details_use_collapsed_technical_drawer_without_repeated_coverage_or_material_panels():
    html = _backoffice_html()
    details = _function_region(html, "renderDocumentAuditDetails", "documentReviewContextLine")
    technical = _function_region(html, "buildVerificationResultsHtml", "renderDocumentAuditDetails")

    assert "buildVerificationResultsHtml(doc.verification_results, coverage, auditContext)" in details
    assert "Technical audit details" in technical
    assert "Passed technical checks" in technical
    assert "Portal slot/source" in technical
    assert "Lifecycle context" in technical
    assert "Policy ID/version" in technical
    assert "Check ID:" in technical
    assert "Warnings:" not in technical
    assert "Issues:" not in technical
    assert "Material findings" not in technical
    assert "Verification coverage" not in technical


def test_backoffice_upload_supports_expected_slot_person_mapping_and_upload_context():
    html = _backoffice_html()
    upload_region = _function_region(html, "toggleBoDocUpload", "viewBackofficeDocument")
    primary_actions = _function_region(html, "renderDocumentPrimaryAction", "renderDocumentDirectActions")

    for expected in [
        "openBoDocUploadForExpectedSlot",
        "bo-upload-person-id",
        "bo-upload-person-type",
        "Expected slot:",
        "person_id=",
        "person_type=",
    ]:
        assert expected in upload_region
    assert "onclick=\\'openBoDocUploadForExpectedSlot(" in primary_actions
    assert "Corporate entity document" in html
