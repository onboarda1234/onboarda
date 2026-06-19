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


def test_sync_hides_approve_and_gates_reject_override():
    html = _html()
    sync = html.split("function syncApplicationActionPermissions(", 1)[1].split("\nfunction ", 1)[0]
    # Approve is hidden when the backend would reject it.
    assert "approveBackendBlockReason(app)" in sync
    assert "setDetailActionVisibility('btn-approve', !approveBlock)" in sync
    # Reject / Override gate strictly when the matrix is loaded, but fail OPEN when it
    # is not (a matrix-load failure must never hide a senior officer's controls).
    assert "setDetailActionVisibility('btn-reject', !rolePermissionsLoaded() || hasPermission('reject_applications'))" in sync
    assert "setDetailActionVisibility('btn-override', !rolePermissionsLoaded() || hasPermission('override_ai_risk_score'))" in sync
    # Submit-to-Compliance offered, including from edd_required (mirrors backend).
    assert "btn-submit-compliance" in sync
    assert "'edd_required'" in sync
    # Role + raw status are normalized for the submit-visibility check (robustness).
    assert "String(currentUserRole() || '').toLowerCase()" in sync
    assert "indexOf(submitRole) >= 0" in sync


def test_blocker_hint_element_and_messaging():
    html = _html()
    assert 'id="approval-authority-hint"' in html
    sync = html.split("function syncApplicationActionPermissions(", 1)[1].split("\nfunction ", 1)[0]
    # Short explanation that points the officer to the forward action.
    assert "Use Submit to Compliance." in sync


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
    assert "approveBackendBlockReason(currentApp)" in handler
    assert "Use Submit to Compliance." in handler


def test_no_backend_or_status_scope_creep_in_this_change():
    # PR3 is UI-only. The new authority-mirror logic must live in the back office,
    # and must not redefine backend permission roles client-side (it reads
    # ROLE_PERMISSIONS via hasPermission).
    html = _html()
    sync = html.split("function syncApplicationActionPermissions(", 1)[1].split("\nfunction ", 1)[0]
    assert "hasPermission('reject_applications')" in sync
    assert "hasPermission('override_ai_risk_score')" in sync
