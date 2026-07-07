"""chore-applications-deadcode-cleanup — the dead approval branches in
ApplicationDetailHandler.patch are removed and stay removed.

Behavioural proof that PATCH->approved/rejected is still hard-blocked (409) lives
in test_patch_decision_bypass.py — that suite is the characterization lock and
must stay green (this cleanup is behaviour-neutral). Here we add a static guard
so the unreachable approval stack cannot silently return to the PATCH handler,
and confirm the real approval path (/decision) still owns ApprovalGateValidator.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SERVER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _patch_handler_body(src):
    """Return the source of ApplicationDetailHandler.patch (up to the next class)."""
    start = src.index("class ApplicationDetailHandler(")
    body = src[start:]
    # cut at the next top-level class definition
    nxt = re.search(r"\nclass [A-Za-z_]", body[10:])
    return body[: nxt.start() + 10] if nxt else body


def test_patch_handler_has_no_approval_enforcement_deadcode():
    body = _patch_handler_body(_read(_SERVER))
    # The unreachable approval stack must be gone from the PATCH handler.
    assert 'if new_status == "approved":' not in body, (
        "dead approval branch reintroduced into ApplicationDetailHandler.patch")
    assert "ApprovalGateValidator.validate_approval(app_dict" not in body, (
        "approval-gate validation must not live in the PATCH handler")
    assert 'if new_status in ("approved", "rejected"):' not in body, (
        "dead terminal decided_at write reintroduced into the PATCH handler")
    # The terminal-transition block (P0-1) that makes the above unreachable stays.
    assert 'normalized_terminal_status in ("approved", "rejected")' in body
    assert "application.decision_blocked" in body


def test_approval_gate_still_owned_by_decision_path():
    """The deletion removed a DUPLICATE, not the control: ApprovalGateValidator
    must still be invoked on the real approval path elsewhere in server.py."""
    src = _read(_SERVER)
    assert src.count("ApprovalGateValidator.validate_approval(") >= 1
    # And the decision handler still runs it.
    decision = src.split("class ApplicationDecisionHandler(", 1)[1].split("\nclass ", 1)[0]
    assert "ApprovalGateValidator.validate_approval(" in decision
