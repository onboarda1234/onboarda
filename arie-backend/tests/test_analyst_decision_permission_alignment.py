"""APP-CONF-001 regression: UI-advertised permissions must match backend authority.

The confirmation audit found the published RBAC matrix granted analysts
``request_more_information`` and ``escalate_to_sco``, so the back office showed
both controls as active — but both actions submit through
``ApplicationDecisionHandler``, which enforces ``roles=["admin", "sco", "co"]``
and returned 403. Approved fix (option 1): align the matrix and UI to the
existing server policy; analysts do not get these actions.

These tests pin the contract from three sides so the surfaces cannot drift
apart again: the matrix, the decision endpoint, and the button gating.
"""
import os
import re

import pytest

DECISION_ENDPOINT_ROLES = {"admin", "sco", "co"}
DECISION_SUBMITTED_PERMISSIONS = [
    "approve_low_medium",
    "reject_applications",
    "request_more_information",
    "escalate_to_sco",
]


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(path):
    with open(os.path.join(_repo_root(), path), "r", encoding="utf-8") as handle:
        return handle.read()


@pytest.mark.parametrize("perm_id", DECISION_SUBMITTED_PERMISSIONS)
def test_matrix_never_advertises_decision_actions_beyond_endpoint_roles(temp_db, perm_id):
    """Every role granted a decision-submitting permission must be accepted by
    the decision endpoint — a wider grant recreates APP-CONF-001."""
    from server import ROLE_PERMISSION_MATRIX

    perm = next(p for p in ROLE_PERMISSION_MATRIX if p["id"] == perm_id)
    extra = set(perm["roles"]) - DECISION_ENDPOINT_ROLES
    assert not extra, (
        f"{perm_id} is advertised to {sorted(extra)}, but ApplicationDecisionHandler "
        f"only accepts {sorted(DECISION_ENDPOINT_ROLES)} — the UI would show an "
        "action the backend denies (APP-CONF-001)."
    )


def test_analyst_not_granted_rmi_or_escalate(temp_db):
    from server import ROLE_PERMISSION_MATRIX

    for perm_id in ("request_more_information", "escalate_to_sco"):
        perm = next(p for p in ROLE_PERMISSION_MATRIX if p["id"] == perm_id)
        assert "analyst" not in perm["roles"], perm


def test_decision_endpoint_role_anchor_unchanged():
    """The contract above hard-codes the endpoint's roles; if the handler's
    require_auth roles ever change, this anchor forces a deliberate review."""
    server_py = _read("arie-backend/server.py")
    handler_start = server_py.index("class ApplicationDecisionHandler")
    handler_region = server_py[handler_start : handler_start + 2000]
    match = re.search(r'require_auth\(roles=\[([^\]]+)\]\)', handler_region)
    assert match, "ApplicationDecisionHandler must gate on require_auth(roles=[...])"
    roles = {r.strip().strip("'\"") for r in match.group(1).split(",")}
    assert roles == DECISION_ENDPOINT_ROLES, roles


def test_ui_gates_rmi_and_escalate_visibility_static():
    """The controls must be hidden for roles without the permission (same
    fail-open pattern as btn-override: an unloaded matrix never hides them)."""
    html = _read("arie-backoffice.html")
    assert 'id="btn-escalate"' in html
    assert (
        "setDetailActionVisibility('btn-rmi', !rolePermissionsLoaded() || "
        "hasPermission('request_more_information'))" in html
    )
    assert (
        "setDetailActionVisibility('btn-escalate', !rolePermissionsLoaded() || "
        "hasPermission('escalate_to_sco'))" in html
    )
