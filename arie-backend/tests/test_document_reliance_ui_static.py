from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _function_region(html: str, start_name: str, next_name: str) -> str:
    start = html.index(f"function {start_name}")
    end = html.index(f"function {next_name}", start)
    return html[start:end]


def test_backoffice_reads_backend_document_reliance_summary():
    html = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")

    assert "documentRelianceSummary" in html
    assert "document_evidence_gate" in html
    assert "document evidence blocker(s)" in html
    assert "Document evidence is not reliance-ready." in html


def test_portal_does_not_treat_blocked_reliance_as_verified():
    html = (ROOT / "arie-portal.html").read_text(encoding="utf-8")

    assert "data-reliance-state" in html
    assert "document_reliance_state" in html
    assert "Verification skipped - manual review required" in html
    assert "Blocked from use" in html


def test_kyc_manual_acceptance_precedes_missing_policy_display():
    html = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    ready_helper = _function_region(html, "documentReadyDisplayState", "documentRelianceDisplayState")
    state_helper = _function_region(html, "documentRelianceDisplayState", "renderRelianceBadge")
    action_helper = _function_region(html, "documentRequiredAction", "documentLifecycleLabel")
    coverage_helper = _function_region(html, "buildDocumentVerificationCoverage", "renderVerificationCoverageChip")

    assert "document_reliance_state" in ready_helper
    assert "manual_accepted" in ready_helper
    assert "['accepted', 'approved'].indexOf(reviewStatus) >= 0" in ready_helper
    assert state_helper.index("documentReadyDisplayState(doc)") < state_helper.index("if (!policy)")
    assert action_helper.index("label === 'Manual accepted'") < action_helper.index("if (!policy)")
    assert "Manual acceptance is recorded; automated policy coverage is not required" in coverage_helper


def test_kyc_unaccepted_missing_policy_still_shows_setup_issue():
    html = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    state_helper = _function_region(html, "documentRelianceDisplayState", "renderRelianceBadge")
    issue_helper = _function_region(html, "documentPrimaryIssue", "documentBlocksDisplay")
    summary_helper = _function_region(html, "renderDocumentCompactSummary", "renderDocumentSecondaryActions")

    assert "if (!policy) return { label: 'Review required', tone: 'review-required' };" in state_helper
    assert "Verification policy missing. Admin setup is required before automated verification can run. Manual review is required before relying on this document." in html
    assert "KYC_VERIFICATION_POLICY_MISSING_MESSAGE" in issue_helper
    assert "System setup issue: verification policy missing." not in issue_helper
    assert "isStandaloneKycDocumentIssue(issueText)" in summary_helper
    assert "summaryParts.push(issueText);" in summary_helper


def test_enhanced_requirement_and_periodic_review_use_ready_manual_status_semantics():
    html = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    enhanced_helper = _function_region(html, "enhancedRequirementVerificationBadge", "enhancedRequirementPortalRequestCopy")
    lifecycle_ready = _function_region(html, "lifecycleEvidenceLinkReady", "lifecycleEvidenceLinkStatusBadge")
    lifecycle_badge = _function_region(html, "lifecycleEvidenceLinkStatusBadge", "lifecycleRequirementEvidenceSatisfied")

    assert "documentReadyDisplayState(doc)" in enhanced_helper
    assert "Accepted evidence" in enhanced_helper
    assert "document_reliance_state" in lifecycle_ready
    assert "manual_accepted" in lifecycle_ready
    assert "Manual accepted evidence" in lifecycle_badge


def test_change_management_evidence_status_is_visible_in_detail_modal():
    html = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    detail_modal = _function_region(html, "viewRequestDetail", "submitChangeRequest")

    assert "function cmEvidenceStatusBadge" in html
    assert "Accepted / ready" in html
    assert "evidence_summary" in detail_modal
    assert "agent1_verification" in detail_modal


def test_overview_and_case_command_share_backend_document_readiness_truth():
    html = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
    panel_summary = _function_region(html, "buildKycDocumentPanelSummary", "updateKycDocumentsPanelState")
    approval_blockers = _function_region(html, "getApplicationApprovalBlockers", "getApprovalReadiness")
    case_command = _function_region(html, "renderCaseCommandCentre", "renderApprovalBlockersPanel")

    assert "computeDocumentReadinessSummary(app)" in panel_summary
    assert "documentRelianceDisplayState(doc, policy)" in panel_summary
    assert "gateBlockers" in approval_blockers
    assert "computeDocumentReadinessSummary(app)" in approval_blockers
    assert "getCaseCommandBlockers(app)" in case_command


def test_portal_accepted_enhanced_requirement_does_not_show_required():
    html = (ROOT / "arie-portal.html").read_text(encoding="utf-8")
    tone_helper = _function_region(html, "portalEnhancedRequirementTone", "portalEnhancedRequirementActionText")

    assert "s === 'accepted'" in tone_helper
    assert "text: 'Accepted'" in tone_helper
