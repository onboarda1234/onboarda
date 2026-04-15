"""
EX-11 — Label AI outputs as advisory in back office

Tests verify:
  Part A: AI-generated surfaces are identified (covered by grep-based assertions)
  Part B: Visible advisory labeling on all AI sections
  Part C: Officer sign-off gate on decision modals and memo approval
  Part D: Mock/simulated labeling
  Part E: Visual distinction between AI and rule-based content
"""
import os
import re
import pytest

BACKOFFICE_PATH = os.path.join(
    os.path.dirname(__file__), '..', '..', 'arie-backoffice.html'
)


@pytest.fixture(scope='module')
def backoffice_html():
    with open(BACKOFFICE_PATH, 'r', encoding='utf-8') as f:
        return f.read()


# ═══════════════════════════════════════════════════
# Part A — AI-generated surfaces are present
# ═══════════════════════════════════════════════════


class TestPartA_AISurfacesIdentified:
    """Verify key AI-generated surfaces exist in the back office HTML."""

    def test_compliance_memo_section_exists(self, backoffice_html):
        assert 'id="detail-memo"' in backoffice_html

    def test_memo_validation_panel_exists(self, backoffice_html):
        assert 'id="memo-validation-panel"' in backoffice_html

    def test_supervisor_pipeline_exists(self, backoffice_html):
        assert 'id="sv-pipeline-results"' in backoffice_html

    def test_ai_agent_pipeline_exists(self, backoffice_html):
        assert 'id="detail-agents"' in backoffice_html

    def test_verification_results_function(self, backoffice_html):
        assert 'function buildVerificationResultsHtml' in backoffice_html

    def test_render_memo_sections_function(self, backoffice_html):
        assert 'function renderMemoSections' in backoffice_html

    def test_render_supervisor_results_function(self, backoffice_html):
        assert 'function renderSupervisorResults' in backoffice_html


# ═══════════════════════════════════════════════════
# Part B — Visible advisory labeling
# ═══════════════════════════════════════════════════


class TestPartB_AdvisoryLabeling:
    """Every AI-generated section must have a visible advisory label."""

    def test_advisory_css_class_defined(self, backoffice_html):
        assert '.ai-advisory-banner' in backoffice_html
        assert '.ai-advisory-badge' in backoffice_html

    def test_memo_header_has_advisory_badge(self, backoffice_html):
        # The memo header should contain the advisory badge
        memo_header_match = re.search(
            r'Compliance Onboarding Memo.*?ai-advisory-badge.*?AI-Generated.*?Advisory Only',
            backoffice_html,
            re.DOTALL
        )
        assert memo_header_match is not None, \
            "Memo header should have 'AI-Generated — Advisory Only' badge"

    def test_memo_rendered_content_has_advisory_banner(self, backoffice_html):
        # renderMemoSections() must add an advisory banner
        assert "AI-Generated — Advisory Only" in backoffice_html
        # Verify it's inside the function (use next function as boundary)
        fn_start = backoffice_html.index('function renderMemoSections')
        fn_end = backoffice_html.index('function generateComplianceMemo', fn_start)
        fn_region = backoffice_html[fn_start:fn_end]
        assert 'ai-advisory-banner' in fn_region, \
            "renderMemoSections must include the AI advisory banner"

    def test_validation_panel_has_advisory_badge(self, backoffice_html):
        validation_match = re.search(
            r'memo-validation-panel.*?ai-advisory-badge.*?AI-Generated.*?Advisory Only',
            backoffice_html,
            re.DOTALL
        )
        assert validation_match is not None, \
            "Validation panel should have advisory badge"

    def test_supervisor_tab_has_advisory_banner(self, backoffice_html):
        sv_match = re.search(
            r'Compliance Supervisor Pipeline.*?ai-advisory-badge.*?AI-Generated.*?Advisory Only',
            backoffice_html,
            re.DOTALL
        )
        assert sv_match is not None, \
            "Supervisor tab header should have advisory badge"

    def test_ai_agent_pipeline_has_advisory_banner(self, backoffice_html):
        agent_match = re.search(
            r'AI Agent Pipeline Results.*?ai-advisory-badge.*?AI-Generated.*?Advisory Only',
            backoffice_html,
            re.DOTALL
        )
        assert agent_match is not None, \
            "AI Agent Pipeline Results should have advisory badge"

    def test_kyc_documents_tab_has_advisory(self, backoffice_html):
        kyc_match = re.search(
            r'KYC Documents.*?ai-advisory-badge.*?Advisory Only',
            backoffice_html,
            re.DOTALL
        )
        assert kyc_match is not None, \
            "KYC Documents tab should have advisory badge"

    def test_verification_checks_have_advisory_banner(self, backoffice_html):
        fn_start = backoffice_html.index('function buildVerificationResultsHtml')
        fn_end = backoffice_html.index('function buildStoredRiskComputation', fn_start)
        fn_region = backoffice_html[fn_start:fn_end]
        assert 'ai-advisory-banner' in fn_region, \
            "buildVerificationResultsHtml must include advisory banner"
        assert 'AI Verification — Advisory Only' in fn_region

    def test_supervisor_verdict_panel_has_advisory(self, backoffice_html):
        # The SUPERVISOR VERDICT label and ai-advisory-badge should co-occur
        # within the renderMemoSections function output
        fn_start = backoffice_html.index('function renderMemoSections')
        fn_end = backoffice_html.index('function generateComplianceMemo', fn_start)
        fn_region = backoffice_html[fn_start:fn_end]
        assert 'SUPERVISOR VERDICT' in fn_region
        # Find the section and check for advisory badge
        sv_idx = fn_region.index('SUPERVISOR VERDICT')
        sv_region = fn_region[sv_idx:sv_idx + 800]
        assert 'ai-advisory-badge' in sv_region, \
            "Supervisor verdict panel must have advisory badge"

    def test_supervisor_sub_panels_have_advisory(self, backoffice_html):
        """Case Aggregate, Agent Results, Contradictions panels have advisory badges."""
        assert re.search(
            r'Case Aggregate.*?ai-advisory-badge.*?Advisory',
            backoffice_html, re.DOTALL
        ), "Case Aggregate panel should have advisory badge"
        assert re.search(
            r'Agent Results.*?ai-advisory-badge.*?Advisory',
            backoffice_html, re.DOTALL
        ), "Agent Results panel should have advisory badge"
        assert re.search(
            r'Contradictions Detected.*?ai-advisory-badge.*?Advisory',
            backoffice_html, re.DOTALL
        ), "Contradictions panel should have advisory badge"


# ═══════════════════════════════════════════════════
# Part C — Officer sign-off gate
# ═══════════════════════════════════════════════════


class TestPartC_OfficerSignoffGate:
    """Officer sign-off is required before AI output can influence decisions."""

    def test_decision_modal_has_signoff_checkbox(self, backoffice_html):
        assert 'id="decision-officer-signoff"' in backoffice_html
        assert 'Officer Sign-Off' in backoffice_html

    def test_override_modal_has_signoff_checkbox(self, backoffice_html):
        assert 'id="override-officer-signoff"' in backoffice_html

    def test_memo_approval_has_signoff_checkbox(self, backoffice_html):
        assert 'id="memo-officer-signoff"' in backoffice_html

    def test_confirm_decision_checks_signoff(self, backoffice_html):
        fn_start = backoffice_html.index('async function confirmDecision()')
        fn_region = backoffice_html[fn_start:fn_start + 2000]
        assert 'decision-officer-signoff' in fn_region, \
            "confirmDecision must check the officer sign-off checkbox"
        assert 'Officer Sign-Off Required' in fn_region, \
            "confirmDecision must show an error message about sign-off"

    def test_confirm_override_checks_signoff(self, backoffice_html):
        fn_start = backoffice_html.index('async function confirmOverride()')
        fn_region = backoffice_html[fn_start:fn_start + 2000]
        assert 'override-officer-signoff' in fn_region, \
            "confirmOverride must check the officer sign-off checkbox"
        assert 'Officer Sign-Off Required' in fn_region, \
            "confirmOverride must show an error message about sign-off"

    def test_approve_memo_checks_signoff(self, backoffice_html):
        fn_start = backoffice_html.index('async function approveMemo()')
        fn_region = backoffice_html[fn_start:fn_start + 2000]
        assert 'memo-officer-signoff' in fn_region, \
            "approveMemo must check the memo officer sign-off checkbox"
        assert 'Officer Sign-Off Required' in fn_region, \
            "approveMemo must show an error message about sign-off"

    def test_signoff_reset_on_approve(self, backoffice_html):
        fn_start = backoffice_html.index('function approveApplication()')
        fn_region = backoffice_html[fn_start:fn_start + 500]
        assert 'decision-officer-signoff' in fn_region, \
            "approveApplication must reset the sign-off checkbox"

    def test_signoff_reset_on_reject(self, backoffice_html):
        fn_start = backoffice_html.index('function rejectApplication()')
        fn_region = backoffice_html[fn_start:fn_start + 500]
        assert 'decision-officer-signoff' in fn_region, \
            "rejectApplication must reset the sign-off checkbox"

    def test_signoff_reset_on_escalate(self, backoffice_html):
        fn_start = backoffice_html.index('async function escalateCase()')
        fn_region = backoffice_html[fn_start:fn_start + 500]
        assert 'decision-officer-signoff' in fn_region, \
            "escalateCase must reset the sign-off checkbox"

    def test_signoff_reset_on_request_docs(self, backoffice_html):
        fn_start = backoffice_html.index('function requestMoreInfo()')
        fn_region = backoffice_html[fn_start:fn_start + 500]
        assert 'decision-officer-signoff' in fn_region, \
            "requestMoreInfo must reset the sign-off checkbox"

    def test_signoff_reset_on_override(self, backoffice_html):
        fn_start = backoffice_html.index('function openOverrideModal()')
        fn_region = backoffice_html[fn_start:fn_start + 800]
        assert 'override-officer-signoff' in fn_region, \
            "openOverrideModal must reset the sign-off checkbox"

    def test_signoff_gate_css_exists(self, backoffice_html):
        assert '.officer-signoff-gate' in backoffice_html

    def test_signoff_text_content(self, backoffice_html):
        assert 'AI outputs are advisory only' in backoffice_html
        assert 'accept responsibility' in backoffice_html


# ═══════════════════════════════════════════════════
# Part D — Mock / simulated labeling
# ═══════════════════════════════════════════════════


class TestPartD_MockSimulatedLabeling:
    """Mock and simulated content must be explicitly labeled."""

    def test_simulated_css_class_defined(self, backoffice_html):
        assert '.ai-simulated-banner' in backoffice_html

    def test_ai_source_tag_css_defined(self, backoffice_html):
        assert '.ai-source-tag' in backoffice_html
        assert '.ai-source-tag.live' in backoffice_html
        assert '.ai-source-tag.mock' in backoffice_html
        assert '.ai-source-tag.deterministic' in backoffice_html
        assert '.ai-source-tag.demo' in backoffice_html
        assert '.ai-source-tag.fallback' in backoffice_html

    def test_mock_label_says_not_from_live_ai(self, backoffice_html):
        assert 'Simulated — Not From Live AI' in backoffice_html

    def test_demo_mode_labeled_as_simulated(self, backoffice_html):
        assert 'Simulated — Demo Mode Output' in backoffice_html

    def test_fallback_labeled_as_simulated(self, backoffice_html):
        assert 'Simulated — Not From Live AI' in backoffice_html

    def test_verification_mock_banner_enhanced(self, backoffice_html):
        fn_start = backoffice_html.index('function buildVerificationResultsHtml')
        fn_end = backoffice_html.index('function buildStoredRiskComputation', fn_start)
        fn_region = backoffice_html[fn_start:fn_end]
        assert 'Simulated — Not From Live AI' in fn_region, \
            "Verification checks must label mock results as simulated"

    def test_ai_source_surfaced_as_tag(self, backoffice_html):
        fn_start = backoffice_html.index('function renderMemoSections')
        fn_end = backoffice_html.index('function generateComplianceMemo', fn_start)
        fn_region = backoffice_html[fn_start:fn_end]
        assert 'ai-source-tag' in fn_region, \
            "renderMemoSections should surface ai_source as a tag"

    def test_screening_simulated_blocks_approval(self, backoffice_html):
        """Simulated screening badge indicates not from live + blocks approval."""
        fn_start = backoffice_html.index('function screeningModeBadge')
        fn_region = backoffice_html[fn_start:fn_start + 800]
        assert 'Not From Live Screening' in fn_region, \
            "Simulated screening must indicate it is not from live screening"
        assert 'Blocks Approval' in fn_region, \
            "Simulated screening badge must still indicate it blocks approval"


# ═══════════════════════════════════════════════════
# Part E — Visual distinction AI vs rule-based
# ═══════════════════════════════════════════════════


class TestPartE_VisualDistinction:
    """AI and rule-based checks should be visually distinguishable."""

    def test_check_type_legend_css_defined(self, backoffice_html):
        assert '.check-type-legend' in backoffice_html
        assert '.legend-dot.rule' in backoffice_html
        assert '.legend-dot.ai' in backoffice_html

    def test_check_type_legend_in_verification(self, backoffice_html):
        fn_start = backoffice_html.index('function buildVerificationResultsHtml')
        fn_end_marker = backoffice_html.index('function buildStoredRiskComputation', fn_start)
        fn_region = backoffice_html[fn_start:fn_end_marker]
        assert 'check-type-legend' in fn_region, \
            "Verification results should include a check type legend"
        assert 'Rule-Based (Deterministic)' in fn_region
        assert 'AI-Generated (Interpretive)' in fn_region

    def test_classification_badge_enhanced(self, backoffice_html):
        fn_start = backoffice_html.index('function buildVerificationResultsHtml')
        fn_end_marker = backoffice_html.index('function buildStoredRiskComputation', fn_start)
        fn_region = backoffice_html[fn_start:fn_end_marker]
        # Classification badges should now say "Rule-Based" instead of just "rule"
        assert "Rule-Based" in fn_region
        assert "AI-Generated" in fn_region

    def test_rule_engine_panel_labeled_deterministic(self, backoffice_html):
        fn_start = backoffice_html.index('function renderMemoSections')
        fn_end = backoffice_html.index('function generateComplianceMemo', fn_start)
        fn_region = backoffice_html[fn_start:fn_end]
        re_idx = fn_region.index('PRE-GENERATION RULE ENGINE')
        re_region = fn_region[re_idx:re_idx + 800]
        assert 'Deterministic' in re_region, \
            "Rule Engine panel should be labeled as Deterministic"

    def test_rules_triggered_panel_labeled_rule_based(self, backoffice_html):
        match = re.search(
            r'Compliance Rules Triggered.*?Rule-Based',
            backoffice_html,
            re.DOTALL
        )
        assert match is not None, \
            "Compliance Rules Triggered panel should be labeled Rule-Based"


# ═══════════════════════════════════════════════════
# Non-regression: existing gates not weakened
# ═══════════════════════════════════════════════════


class TestNonRegression:
    """Verify existing compliance gates are not weakened by EX-11 changes."""

    def test_existing_memo_gates_preserved(self, backoffice_html):
        fn_start = backoffice_html.index('async function approveMemo()')
        fn_region = backoffice_html[fn_start:fn_start + 3000]
        # All 5 original gates must still be present
        assert 'GATE 1' in fn_region
        assert 'GATE 2' in fn_region
        assert 'GATE 3' in fn_region
        assert 'GATE 4' in fn_region
        assert 'GATE 5' in fn_region

    def test_decision_still_requires_reason(self, backoffice_html):
        fn_start = backoffice_html.index('async function confirmDecision()')
        fn_region = backoffice_html[fn_start:fn_start + 1000]
        assert 'Please provide a reason' in fn_region

    def test_override_still_requires_reason(self, backoffice_html):
        fn_start = backoffice_html.index('async function confirmOverride()')
        fn_region = backoffice_html[fn_start:fn_start + 1000]
        assert 'Please provide a reason for the override' in fn_region

    def test_backend_approval_endpoint_unchanged(self, backoffice_html):
        assert '/decision' in backoffice_html
        assert '/memo/approve' in backoffice_html

    def test_supervisor_verdict_gating_preserved(self, backoffice_html):
        fn_start = backoffice_html.index('async function approveMemo()')
        fn_region = backoffice_html[fn_start:fn_start + 3000]
        assert 'INCONSISTENT' in fn_region
        assert 'can_approve' in fn_region
