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


def test_verification_coverage_summary_exposes_pass_fail_skip_not_run_and_system_state():
    html = _backoffice_html()
    summary = _function_region(html, "renderVerificationCoverageSummary", "renderDocumentCompactSummary")

    for expected in [
        "Verification coverage",
        "Checks passed",
        "Checks failed",
        "Warnings",
        "Skipped",
        "Not run",
        "System-blocked",
        "Expected checks:",
        "Persisted checks:",
    ]:
        assert expected in summary


def test_default_document_row_stays_compact_and_avoids_repeating_audit_payloads():
    html = _backoffice_html()
    card = _function_region(html, "renderUnifiedKycDocumentCard", "enhancedRequirementBackOfficeGroup")
    default_row = card.split("renderDocumentAuditDetails", 1)[0]

    for expected in [
        "renderDocumentCompactSummary(issue, blocker, nextAction, relianceState)",
        "renderDocumentDirectActions(app, doc, groupKey, relianceState)",
        "Expected from portal slot",
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
    ]:
        assert hidden not in default_row


def test_uploaded_document_actions_include_view_download_and_reverify():
    html = _backoffice_html()
    actions = _function_region(html, "renderDocumentDirectActions", "buildVerificationResultsHtml")

    for expected in [
        "viewBackofficeDocument",
        "downloadBackofficeDocument",
        "verifyBackofficeDocument",
        ">View</button>",
        ">Download</button>",
        ">Re-Verify</button>",
    ]:
        assert expected in actions


def test_audit_details_use_technical_drawer_not_repeated_issue_boxes():
    html = _backoffice_html()
    details = _function_region(html, "renderDocumentAuditDetails", "renderUnifiedKycDocumentCard")
    technical = _function_region(html, "buildVerificationResultsHtml", "renderDocumentAuditDetails")

    assert "renderVerificationCoverageSummary(doc, policy)" in details
    assert "buildVerificationResultsHtml(doc.verification_results, coverage)" in details
    assert "Technical audit details" in technical
    assert "<summary>Technical audit details</summary>" in technical
    assert "Why review is required" not in technical
    assert "Document status" not in technical
