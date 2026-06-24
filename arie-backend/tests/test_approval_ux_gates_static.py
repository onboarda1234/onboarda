"""
PR-APPROVAL-UX-GATES-1 — static assertions that the back-office UI aligns its
action buttons with backend authority (PR1 can_decide + PR2 submit-to-compliance).

These are static source assertions (the repo's established pattern for the large
single-file UIs). They verify the gating LOGIC is present; backend remains the
source of truth — the UI only reduces confusion and failed clicks. Live role-by-role
behaviour (CO HIGH / SCO / analyst) is covered by the Codex browser smoke on staging.
"""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _html():
    return BACKOFFICE_HTML.read_text(encoding="utf-8")


def test_authority_mirror_helpers_exist():
    html = _html()
    assert "function canApproveMemo(" in html
    assert "function approvalRoutePolicy(" in html
    assert "function approvalRouteName(" in html
    assert "function approveBackendBlockReason(" in html
    # Must be documented as a UX mirror, not a security boundary.
    assert "NOT a security boundary" in html


def test_ux_gates_fail_open_when_permission_matrix_unloaded():
    # Regression guard for the 0c smoke failure: a CO on a clean LOW/MEDIUM file must
    # see Approve. The "no approval authority" hide must only fire when the RBAC matrix
    # is actually loaded — otherwise hasPermission() fails closed and wrongly hides
    # Approve for legitimate approvers on a matrix-load race/failure.
    html = _html()
    assert "function rolePermissionsLoaded(" in html
    fn = html.split("function approveBackendBlockReason(", 1)[1].split("function setDetailActionVisibility(", 1)[0]
    assert "rolePermissionsLoaded() && !hasPermission('approve_low_medium')" in fn
    # The CO role/risk/status blocks must NOT be guarded by matrix load (they use the
    # role string + risk directly and stay reliable without the matrix).
    assert "role === 'co' && (risk === 'HIGH' || risk === 'VERY_HIGH')" in fn


def test_co_high_and_edd_are_backend_block_reasons():
    html = _html()
    fn = html.split("function approveBackendBlockReason(", 1)[1].split("function setDetailActionVisibility(", 1)[0]
    # Onboarding Officer blocked on HIGH/VERY_HIGH.
    assert "role === 'co'" in fn
    assert "'HIGH'" in fn and "'VERY_HIGH'" in fn
    assert "Requires SCO review" in fn
    assert "routeName === 'compliance_required'" in fn
    assert "routeName === 'dual_control_required'" in fn
    # Onboarding Officer blocked on EDD-required (EDD completion is senior-owned).
    assert "'edd_required'" in fn
    # Roles with no approval authority at all are blocked.
    assert "approve_low_medium" in fn


def test_risk_is_normalized_before_comparison():
    # "VERY HIGH" / "Very-High" / "very_high" must all map to VERY_HIGH so a CO is
    # never shown Approve on a high-risk case due to a formatting variant.
    html = _html()
    fn = html.split("function approveBackendBlockReason(", 1)[1].split("function setDetailActionVisibility(", 1)[0]
    assert ".toUpperCase().replace(/[\\s-]+/g, '_')" in fn


def test_co_blocked_on_submitted_to_compliance():
    # Once submitted, the case belongs to SCO review — CO must not see Approve.
    html = _html()
    fn = html.split("function approveBackendBlockReason(", 1)[1].split("function setDetailActionVisibility(", 1)[0]
    assert "'submitted_to_compliance'" in fn
    assert "Submitted to Compliance — SCO review required." in fn


def test_sync_keeps_terminal_actions_visible_but_disabled():
    html = _html()
    sync = html.split("function syncApplicationActionPermissions(", 1)[1].split("\nfunction ", 1)[0]
    assert "buildApplicationActionState(app)" in sync
    assert "setDetailActionState('btn-submit-compliance', actionState.submitToCompliance)" in sync
    assert "setDetailActionState('btn-approve', actionState.approve)" in sync
    assert "setDetailActionState('btn-reject', actionState.reject)" in sync
    # Override remains a permissions-only visibility gate.
    assert "setDetailActionVisibility('btn-override', !rolePermissionsLoaded() || hasPermission('override_ai_risk_score'))" in sync
    assert "disabled with clear reason text" in sync


def test_action_state_covers_clean_low_medium_optional_submission():
    html = _html()
    fn = html.split("function buildApplicationActionState(", 1)[1].split("\nfunction ", 1)[0]
    assert "approvalRouteName(app) === 'direct_low_medium'" in fn
    assert "readiness.ready" in fn
    assert "submitLabel = '📨 Submit to Compliance (Optional)'" in fn
    assert "Optional discretionary escalation to Compliance." in fn
    assert "approveBlock" in fn
    assert "!readiness.ready" in fn


def test_action_state_disables_terminal_and_submitted_states():
    html = _html()
    assert "Already approved — no further terminal action available." in html
    assert "Already rejected — no further terminal action available." in html
    assert "Already submitted to Compliance — awaiting compliance review." in html
    fn = html.split("function buildApplicationActionState(", 1)[1].split("\nfunction ", 1)[0]
    assert "if (terminalMessage)" in fn
    assert "statusRaw === 'submitted_to_compliance'" in fn
    assert "approveDisabled = true" in fn
    assert "rejectDisabled = true" in fn
    assert "submitDisabled = true" in fn
    assert "visible: true" in fn


def test_high_edd_and_material_concern_route_to_compliance_ui():
    html = _html()
    reason_fn = html.split("function complianceReviewReason(", 1)[1].split("\nfunction ", 1)[0]
    assert "material_screening_concern" in reason_fn
    assert "Material concern requires Compliance review." in reason_fn
    assert "edd_required" in reason_fn
    assert "Compliance review required before approval." in reason_fn
    assert "high_or_very_high_risk" in reason_fn
    assert "routeRequiresCompliance(app)" in reason_fn
    sync = html.split("function syncApplicationActionPermissions(", 1)[1].split("\nfunction ", 1)[0]
    assert "standardCard.style.display = isPreApproval && canSubmitPreApproval ? 'none' : 'flex'" in sync


def test_submit_to_compliance_states_mirror_backend_active_lanes():
    html = _html()
    fn = html.split("function buildApplicationActionState(", 1)[1].split("\nfunction ", 1)[0]
    submit_states = re.search(r"var submitEligibleStates = \[(.*?)\];", fn).group(1)
    assert "'pricing_review'" not in submit_states
    assert "'pre_approval_review'" in submit_states
    assert "'compliance_review'" in submit_states
    assert "'edd_required'" in submit_states
    assert "['admin', 'sco', 'co'].indexOf(role) >= 0" in fn


def test_pricing_review_move_cta_does_not_broaden_submit_to_compliance():
    html = _html()
    state_fn = html.split("function buildApplicationActionState(", 1)[1].split("\nfunction ", 1)[0]
    submit_states = re.search(r"var submitEligibleStates = \[(.*?)\];", state_fn).group(1)
    assert "'pricing_review'" not in submit_states
    assert "'pre_approval_review'" in submit_states
    assert "'compliance_review'" in submit_states and "'edd_required'" in submit_states
    assert "pricing.move_to_compliance_review" in html
    assert "function movePricingToComplianceReview(" in html
    assert "/move-to-compliance-review" in html
    # The standard action bar remains intact; the new CTA is in the blocker row.
    assert 'id="btn-approve"' in html
    assert 'id="btn-rmi"' in html
    assert 'class="topbar-more-menu"' in html


def test_blocker_hint_element_and_messaging():
    html = _html()
    assert 'id="approval-authority-hint"' in html
    sync = html.split("function syncApplicationActionPermissions(", 1)[1].split("\nfunction ", 1)[0]
    assert "actionState.helper" in sync
    assert "hintEl.textContent = actionState.helper" in sync


def test_memo_approve_is_senior_only_in_ui():
    html = _html()
    # canApproveMemo restricts to admin/SCO (matches backend MemoApproveHandler).
    fn = html.split("function canApproveMemo(", 1)[1].split("function approveBackendBlockReason(", 1)[0]
    assert "'admin'" in fn and "'sco'" in fn
    # The validation panel disables the memo-approve button for non-senior roles.
    assert "if (!canApproveMemo()) {" in html
    assert "Memo approval requires Senior Compliance Officer or Admin." in html


def test_approve_click_handler_has_defensive_guard():
    html = _html()
    handler = html.split("function approveApplication(", 1)[1].split("\nfunction ", 1)[0]
    assert "buildApplicationActionState(currentApp)" in handler
    assert "actionState.approve.disabled" in handler
    assert "disabledActionReason(actionState.approve" in handler


def test_reject_submit_and_confirm_handlers_respect_disabled_action_state():
    html = _html()
    reject_handler = html.split("function rejectApplication(", 1)[1].split("\nfunction ", 1)[0]
    submit_handler = html.split("async function submitToCompliance(", 1)[1].split("\nfunction ", 1)[0]
    confirm_handler = html.split("async function confirmDecision(", 1)[1].split("\nfunction ", 1)[0]
    assert "actionState.reject.disabled" in reject_handler
    assert "disabledActionReason(actionState.reject" in reject_handler
    assert "actionState.submitToCompliance.disabled" in submit_handler
    assert "disabledActionReason(actionState.submitToCompliance" in submit_handler
    assert "String(currentUserRole() || '').toLowerCase()" in submit_handler
    assert "decisionActionState.disabled" in confirm_handler
    assert "renderDecisionReadiness(pendingDecision)" in confirm_handler


def test_readiness_panel_does_not_reenable_approve():
    html = _html()
    panel_fn = html.split("function renderApprovalBlockersPanel(", 1)[1].split("\nfunction ", 1)[0]
    assert "approveBtn.disabled = false" not in panel_fn
    assert "syncApplicationActionPermissions(app)" in panel_fn


def test_no_backend_or_status_scope_creep_in_this_change():
    # PR2 is UI-only. The new authority-mirror logic must live in the back office,
    # and must not redefine backend permission roles client-side (it reads
    # ROLE_PERMISSIONS via hasPermission).
    html = _html()
    state_fn = html.split("function buildApplicationActionState(", 1)[1].split("\nfunction ", 1)[0]
    sync = html.split("function syncApplicationActionPermissions(", 1)[1].split("\nfunction ", 1)[0]
    assert "hasPermission('reject_applications')" in state_fn
    assert "hasPermission('override_ai_risk_score')" in sync


def test_admin_audit_refresh_is_gated_to_view_audit_trail():
    # Part B closure: the post-decision audit-evidence refresh hits the admin/SCO-only
    # GET /audit. It must not fire for roles lacking view_audit_trail (e.g. an
    # Onboarding Officer completing an approval) or it 403s and pollutes the console.
    html = _html()
    fn = html.split("function refreshAdminAuditEvidence(", 1)[1].split("\nfunction ", 1)[0]
    assert "if (!hasPermission('view_audit_trail')) return;" in fn
