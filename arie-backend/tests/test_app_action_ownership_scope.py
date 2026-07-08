"""PR-APP-ACTION-OWNERSHIP-SCOPE-1 / Audit-4 FEO-013 — terminal sign-off owner gate.

The pilot runbook's "only the named owner works a case" control was manual, not
code-enforced: any officer could approve/reject any application. This gate
enforces named-owner accountability for TERMINAL sign-off actions only —
final approve/reject, pre-approval approve/reject, and memo approval — while
leaving collaborative / risk-tightening actions (document review, RMI/request,
screening review + clearance, escalate_edd, memo generation/validation) open.

These tests exercise the shared helper `authorize_signoff_ownership` directly
(all branches) plus a static guard proving the three handlers call it.
"""
import os
import re
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


class _FakeHandler:
    """Minimal stand-in for a BaseHandler: records governance audit calls the
    way the real handler would persist them, without needing a live request."""

    def __init__(self):
        self.governance_calls = []

    def log_governance_attempt(self, user, action, target, outcome, status_code,
                               reason="", payload_summary=None, db=None, commit=True,
                               best_effort=True):
        self.governance_calls.append({
            "action": action, "outcome": outcome, "status_code": status_code,
            "reason": reason, "commit": commit,
        })

    def events(self, suffix):
        return [c for c in self.governance_calls if c["action"].endswith(suffix)]


def _mk_user(db, role):
    uid = "u-" + uuid.uuid4().hex[:8]
    db.execute(
        "INSERT INTO users (id, email, password_hash, full_name, role) VALUES (?,?,?,?,?)",
        (uid, uid + "@t.co", "x", "User " + uid, role),
    )
    return uid


def _mk_app(db, assigned_to=None):
    aid = "a-" + uuid.uuid4().hex[:8]
    db.execute(
        "INSERT INTO applications (id, ref, company_name, assigned_to) VALUES (?,?,?,?)",
        (aid, "ARF-" + aid, "Test Ltd", assigned_to),
    )
    return aid


def _app_row(db, aid):
    return db.execute("SELECT * FROM applications WHERE id=?", (aid,)).fetchone()


def _owner(db, aid):
    row = db.execute("SELECT assigned_to FROM applications WHERE id=?", (aid,)).fetchone()
    return (row["assigned_to"] or "") if row else ""


@pytest.fixture
def db(temp_db):
    from db import get_db
    conn = get_db()
    yield conn
    conn.close()


import server  # noqa: E402  (after sys.path insert + env)


def _authorize(handler, db, app_row, actor_id, role, **kw):
    user = {"sub": actor_id, "role": role, "name": actor_id}
    return server.authorize_signoff_ownership(
        handler, db, app_row, user,
        action=kw.pop("action", "application.decision.approve"),
        override_reason=kw.pop("override_reason", None),
        attempt_summary={},
        **kw,
    )


# ══════════════════════════════════════════════════════════
# Decision-mode gate (final approve/reject): full ownership + auto-claim
# ══════════════════════════════════════════════════════════

class TestDecisionModeGate:
    def test_owner_allowed(self, db):
        owner = _mk_user(db, "co")
        aid = _mk_app(db, assigned_to=owner)
        h = _FakeHandler()
        assert _authorize(h, db, _app_row(db, aid), owner, "co") is None
        assert _owner(db, aid) == owner  # unchanged

    def test_non_owner_co_denied(self, db):
        owner = _mk_user(db, "co")
        other = _mk_user(db, "co")
        aid = _mk_app(db, assigned_to=owner)
        h = _FakeHandler()
        result = _authorize(h, db, _app_row(db, aid), other, "co")
        assert result is not None and result[0] == 403
        assert "assigned to another officer" in result[1]
        assert h.events(".ownership_denied")
        assert _owner(db, aid) == owner  # not stolen

    def test_supervisor_non_owner_without_reason_denied(self, db):
        owner = _mk_user(db, "co")
        sco = _mk_user(db, "sco")
        aid = _mk_app(db, assigned_to=owner)
        h = _FakeHandler()
        result = _authorize(h, db, _app_row(db, aid), sco, "sco")
        assert result is not None and result[0] == 403
        assert "override reason is required" in result[1].lower()
        assert h.events(".ownership_denied")

    def test_supervisor_non_owner_with_reason_allowed_and_audited(self, db):
        owner = _mk_user(db, "co")
        admin = _mk_user(db, "admin")
        aid = _mk_app(db, assigned_to=owner)
        h = _FakeHandler()
        result = _authorize(h, db, _app_row(db, aid), admin, "admin",
                            override_reason="covering while owner is on leave")
        assert result is None
        assert h.events(".ownership_override")
        assert h.events(".ownership_override")[0]["commit"] is False

    def test_unassigned_auto_claims_actor(self, db):
        co = _mk_user(db, "co")
        aid = _mk_app(db, assigned_to=None)
        h = _FakeHandler()
        result = _authorize(h, db, _app_row(db, aid), co, "co")
        assert result is None
        assert _owner(db, aid) == co  # first-touch ownership
        claimed = h.events(".ownership_claimed")
        assert claimed and claimed[0]["commit"] is False

    def test_blank_assigned_to_treated_as_unassigned(self, db):
        co = _mk_user(db, "co")
        aid = _mk_app(db, assigned_to="   ")
        h = _FakeHandler()
        assert _authorize(h, db, _app_row(db, aid), co, "co") is None
        assert _owner(db, aid) == co


# ══════════════════════════════════════════════════════════
# Peer-supervisor-only gate (pre-approval, memo approval)
# ══════════════════════════════════════════════════════════

class TestPeerSupervisorOnlyGate:
    def test_line_officer_owner_not_gated(self, db):
        """A supervisor approving a memo on a CO-owned case = normal four-eyes
        flow → allowed with NO reason, and the case is NOT reassigned."""
        co_owner = _mk_user(db, "co")
        sco = _mk_user(db, "sco")
        aid = _mk_app(db, assigned_to=co_owner)
        h = _FakeHandler()
        result = _authorize(h, db, _app_row(db, aid), sco, "sco",
                            action="memo.approve", peer_supervisor_only=True)
        assert result is None
        assert _owner(db, aid) == co_owner  # untouched
        assert not h.events(".ownership_override")

    def test_peer_supervisor_owner_requires_reason(self, db):
        owner_sco = _mk_user(db, "sco")
        other_sco = _mk_user(db, "sco")
        aid = _mk_app(db, assigned_to=owner_sco)
        h = _FakeHandler()
        result = _authorize(h, db, _app_row(db, aid), other_sco, "sco",
                            action="memo.approve", peer_supervisor_only=True)
        assert result is not None and result[0] == 403

    def test_peer_supervisor_owner_with_reason_allowed(self, db):
        owner_sco = _mk_user(db, "sco")
        admin = _mk_user(db, "admin")
        aid = _mk_app(db, assigned_to=owner_sco)
        h = _FakeHandler()
        result = _authorize(h, db, _app_row(db, aid), admin, "admin",
                            action="memo.approve", peer_supervisor_only=True,
                            override_reason="second supervisor review")
        assert result is None

    def test_unassigned_not_claimed_in_peer_mode(self, db):
        sco = _mk_user(db, "sco")
        aid = _mk_app(db, assigned_to=None)
        h = _FakeHandler()
        result = _authorize(h, db, _app_row(db, aid), sco, "sco",
                            action="memo.approve", peer_supervisor_only=True)
        assert result is None
        assert _owner(db, aid) == ""  # NOT auto-claimed — line owner keeps their case
        assert not h.events(".ownership_claimed")

    def test_owner_supervisor_acts_on_own_case(self, db):
        sco = _mk_user(db, "sco")
        aid = _mk_app(db, assigned_to=sco)
        h = _FakeHandler()
        assert _authorize(h, db, _app_row(db, aid), sco, "sco",
                          action="memo.approve", peer_supervisor_only=True) is None


class TestLookupUserRole:
    def test_known_and_unknown(self, db):
        sco = _mk_user(db, "sco")
        assert server._lookup_user_role(db, sco) == "sco"
        assert server._lookup_user_role(db, "nonexistent") == ""
        assert server._lookup_user_role(db, "") == ""
        assert server._lookup_user_role(db, None) == ""


# ══════════════════════════════════════════════════════════
# Static guard: the three sign-off handlers must call the helper
# ══════════════════════════════════════════════════════════

class TestHandlersWireTheGate:
    def _server_src(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py")
        return open(path, encoding="utf-8").read()

    def _handler_body(self, src, class_name):
        m = re.search(r"\nclass " + re.escape(class_name) + r"\b", src)
        assert m, f"{class_name} not found"
        nxt = re.search(r"\nclass ", src[m.end():])
        return src[m.start(): m.end() + (nxt.start() if nxt else len(src))]

    def test_application_decision_handler_gates_approve_reject(self):
        body = self._handler_body(self._server_src(), "ApplicationDecisionHandler")
        assert "authorize_signoff_ownership" in body, \
            "ApplicationDecisionHandler must call the ownership gate for approve/reject"

    def test_decision_handler_exempts_dual_approval_second_leg(self):
        """HIGH/VERY_HIGH dual approval REQUIRES a distinct second approver, so
        the completing approve leg must NOT demand an ownership override —
        accountability there is the dual-approval control itself. The reject
        verb gets no such exemption."""
        body = self._handler_body(self._server_src(), "ApplicationDecisionHandler")
        assert "_is_dual_second_leg" in body
        assert "first_approver_id" in body
        assert 'decision == "approve"' in body  # exemption is approve-only

    def test_pre_approval_handler_gates_signoff(self):
        body = self._handler_body(self._server_src(), "PreApprovalDecisionHandler")
        assert "authorize_signoff_ownership" in body
        assert "peer_supervisor_only=True" in body

    def test_memo_approve_handler_gates_signoff(self):
        body = self._handler_body(self._server_src(), "MemoApproveHandler")
        assert "authorize_signoff_ownership" in body
        assert "peer_supervisor_only=True" in body
