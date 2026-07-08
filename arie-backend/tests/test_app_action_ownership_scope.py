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
        user = {"sub": admin, "role": "admin", "name": admin}
        result = _authorize(h, db, _app_row(db, aid), admin, "admin",
                            override_reason="covering while owner is on leave")
        assert result is None
        # B1 fold: the override audit is DEFERRED — nothing recorded until the
        # handler's success point applies pending intents.
        assert not h.events(".ownership_override")
        import server
        server.apply_pending_signoff_ownership(h, db, user)
        assert h.events(".ownership_override")
        assert h.events(".ownership_override")[0]["commit"] is False
        assert _owner(db, aid) == owner  # override never reassigns

    def test_unassigned_auto_claim_is_deferred_to_success(self, db):
        """B1 fold: passing the gate must NOT touch ownership. The claim is a
        pending intent, applied only by apply_pending_signoff_ownership() at
        the handler's success commit."""
        co = _mk_user(db, "co")
        aid = _mk_app(db, assigned_to=None)
        h = _FakeHandler()
        user = {"sub": co, "role": "co", "name": co}
        result = _authorize(h, db, _app_row(db, aid), co, "co")
        assert result is None
        assert _owner(db, aid) == ""            # NOT claimed yet
        assert not h.events(".ownership_claimed")
        import server
        server.apply_pending_signoff_ownership(h, db, user)
        assert _owner(db, aid) == co            # claimed at success point
        claimed = h.events(".ownership_claimed")
        assert claimed and claimed[0]["commit"] is False
        assert h._pending_signoff_ownership == []  # stash cleared

    def test_failed_decision_never_claims_ownership(self, db):
        """B1 regression: a gate-pass whose decision later FAILS (handler never
        reaches apply_pending_signoff_ownership) leaves ownership untouched —
        an officer cannot seize unassigned cases via failing decision attempts."""
        co = _mk_user(db, "co")
        aid = _mk_app(db, assigned_to=None)
        h = _FakeHandler()
        assert _authorize(h, db, _app_row(db, aid), co, "co") is None
        # ... downstream gate rejects; apply() is never called ...
        assert _owner(db, aid) == ""
        assert not h.events(".ownership_claimed")
        assert not h.events(".ownership_override")

    def test_blank_assigned_to_treated_as_unassigned(self, db):
        co = _mk_user(db, "co")
        aid = _mk_app(db, assigned_to="   ")
        h = _FakeHandler()
        assert _authorize(h, db, _app_row(db, aid), co, "co") is None
        import server
        server.apply_pending_signoff_ownership(h, db, {"sub": co, "role": "co", "name": co})
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
        import server
        server.apply_pending_signoff_ownership(h, db, {"sub": sco, "role": "sco", "name": sco})
        assert _owner(db, aid) == ""  # NOT auto-claimed — no claim intent in peer mode
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
        # S1 fold: indeterminate is None, NOT '' — callers must fail closed.
        assert server._lookup_user_role(db, "nonexistent") is None
        assert server._lookup_user_role(db, "") is None
        assert server._lookup_user_role(db, None) is None

    def test_peer_mode_fails_closed_on_dangling_owner(self, db):
        """S1 regression: a case assigned to a deleted/unknown user id must be
        treated as supervisor-owned (override reason required), never silently
        ungated."""
        sco = _mk_user(db, "sco")
        aid = _mk_app(db, assigned_to="ghost-user-id")
        h = _FakeHandler()
        result = _authorize(h, db, _app_row(db, aid), sco, "sco",
                            action="memo.approve", peer_supervisor_only=True)
        assert result is not None and result[0] == 403
        result = _authorize(h, db, _app_row(db, aid), sco, "sco",
                            action="memo.approve", peer_supervisor_only=True,
                            override_reason="owner record missing — supervisor takeover")
        assert result is None


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
        # B2 fold: exemption only while the dual control applies at CURRENT risk
        assert '_gate_risk_level in ("HIGH", "VERY_HIGH")' in body

    def test_pre_approval_handler_gates_signoff(self):
        body = self._handler_body(self._server_src(), "PreApprovalDecisionHandler")
        assert "authorize_signoff_ownership" in body
        assert "peer_supervisor_only=True" in body

    def test_memo_approve_handler_gates_signoff(self):
        body = self._handler_body(self._server_src(), "MemoApproveHandler")
        assert "authorize_signoff_ownership" in body
        assert "peer_supervisor_only=True" in body


# ══════════════════════════════════════════════════════════
# HTTP-level: the gate on the REAL decision endpoint (review fold S3)
# ══════════════════════════════════════════════════════════

import json
import tempfile

import tornado.testing

from tests.test_e2e_authority_matrix import (
    _SIGNOFF,
    _capture_db_path_state,
    _live_clear_prescreening,
    _sync_test_db_path,
)


class TestOwnershipGateHTTP(tornado.testing.AsyncHTTPTestCase):
    """Drives POST /api/applications/:id/decision end-to-end so the gate's
    placement, the B1 no-claim-on-failure property, the B2 stale-first-approver
    guard, and the open-verbs half of the policy are pinned at the endpoint —
    helper-level tests alone cannot detect the gate becoming unreachable."""

    def get_app(self):
        self._db_path_state = _capture_db_path_state()
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"ownership_gate_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        try:
            os.unlink(self._db_path)
        except OSError:
            pass
        _sync_test_db_path(self._db_path)
        from db import init_db, seed_initial_data, get_db
        from server import make_app
        init_db()
        db = get_db()
        seed_initial_data(db)
        db.commit()
        db.close()
        return make_app()

    def setUp(self):
        super().setUp()
        import base_handler
        base_handler.rate_limiter._attempts.clear()
        from db import get_db
        from server import create_token
        self.db = get_db()
        for uid, role in (("own_co1", "co"), ("own_co2", "co"),
                          ("own_sco1", "sco"), ("own_sco2", "sco")):
            self.db.execute(
                "INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status) "
                "VALUES (?, ?, 'test-only', ?, ?, 'active')",
                (uid, f"{uid}@example.test", f"{uid} Officer", role),
            )
        self.db.commit()
        self.co1 = create_token("own_co1", "co", "CO One", "officer")
        self.co2 = create_token("own_co2", "co", "CO Two", "officer")
        self.sco1 = create_token("own_sco1", "sco", "SCO One", "officer")
        self.admin = create_token("admin001", "admin", "Test Admin", "officer")

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        super().tearDown()
        try:
            os.unlink(self._db_path)
        except OSError:
            pass

    def _h(self, token):
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _seed(self, risk="LOW", assigned_to=None, first_approver=None,
              with_memo=True, documents_ready=True, status="compliance_review"):
        from tests.conftest import insert_verified_required_documents
        suffix = uuid.uuid4().hex[:8]
        app_id, ref = f"own_{suffix}", f"OWN-{suffix}"
        now = "2026-07-01 09:00:00"
        score = {"LOW": 20, "MEDIUM": 50}.get(risk, 78)
        self.db.execute(
            """INSERT INTO applications
                 (id, ref, client_id, company_name, country, sector, entity_type,
                  status, risk_level, final_risk_level, risk_score, assigned_to,
                  first_approver_id, prescreening_data, screening_mode,
                  submitted_at, created_at, updated_at, inputs_updated_at)
               VALUES (?, ?, ?, ?, 'Mauritius', 'Technology', 'SME', ?, ?, ?, ?, ?, ?, ?, 'live', ?, ?, ?, ?)""",
            (app_id, ref, f"{app_id}_c", f"{ref} Ltd", status, risk, risk, score,
             assigned_to, first_approver, _live_clear_prescreening(), now, now, now, now),
        )
        if with_memo:
            self.db.execute(
                """INSERT INTO compliance_memos
                     (application_id, memo_data, generated_by, ai_recommendation,
                      review_status, quality_score, validation_status, supervisor_status, approval_reason)
                   VALUES (?, ?, 'system', 'APPROVE', 'approved', 9.0, 'pass', 'CONSISTENT', 'Fixture approval reason')""",
                (app_id, json.dumps({
                    "ai_source": "deterministic",
                    "metadata": {"ai_source": "deterministic", "edd_routing": {"route": "standard", "triggers": []}},
                    "supervisor": {"verdict": "CONSISTENT", "can_approve": True, "mandatory_escalation": False},
                })),
            )
        if documents_ready:
            insert_verified_required_documents(self.db, app_id)
        if risk in ("HIGH", "VERY_HIGH"):
            self.db.execute(
                "INSERT INTO application_enhanced_requirements "
                "(application_id, trigger_key, trigger_label, requirement_key, requirement_label, "
                " requirement_type, waivable, blocking_approval, mandatory, status) "
                "VALUES (?, 'high_risk', 'High Risk', 'source_wealth', 'Source of Wealth', "
                " 'document', 1, 1, 1, 'accepted')",
                (app_id,),
            )
        self.db.commit()
        return app_id, ref

    def _decide(self, app_id, token, decision="approve", reason="Ownership gate test",
                override_reason=None):
        body = {"decision": decision, "decision_reason": reason, "officer_signoff": _SIGNOFF}
        if override_reason is not None:
            body["ownership_override_reason"] = override_reason
        return self.fetch(f"/api/applications/{app_id}/decision", method="POST",
                          headers=self._h(token), body=json.dumps(body))

    def _row(self, app_id):
        return dict(self.db.execute(
            "SELECT status, assigned_to FROM applications WHERE id=?", (app_id,)).fetchone())

    def _gov_rows(self, needle):
        rows = self.db.execute(
            "SELECT detail FROM audit_log WHERE action='Governance Attempt'").fetchall()
        return [r for r in rows if needle in (r["detail"] or "")]

    # ── Gated verbs ──

    def test_non_owner_co_approve_403_owner_and_status_unchanged(self):
        app_id, _ = self._seed(risk="LOW", assigned_to="own_co2")
        resp = self._decide(app_id, self.co1)
        assert resp.code == 403, resp.body.decode()
        assert "assigned to another officer" in json.loads(resp.body)["error"]
        row = self._row(app_id)
        assert row["assigned_to"] == "own_co2"
        assert row["status"] == "compliance_review"

    def test_sco_without_reason_403_with_reason_succeeds_owner_kept(self):
        app_id, _ = self._seed(risk="LOW", assigned_to="own_co2")
        blocked = self._decide(app_id, self.sco1)
        assert blocked.code == 403, blocked.body.decode()
        assert "override reason is required" in json.loads(blocked.body)["error"].lower()
        ok = self._decide(app_id, self.sco1, override_reason="owner on leave")
        assert ok.code in (200, 201), ok.body.decode()
        row = self._row(app_id)
        assert row["status"] == "approved"
        assert row["assigned_to"] == "own_co2"  # override never reassigns
        assert self._gov_rows("ownership_override"), "override must be audited on success"

    def test_failed_decision_never_claims_ownership_e2e(self):
        """B1 regression at the endpoint: unassigned HIGH case, approve fails a
        DOWNSTREAM gate (no memo) → 400, and ownership must remain unassigned
        with no ownership_claimed audit row."""
        app_id, _ = self._seed(risk="HIGH", assigned_to=None, with_memo=False)
        resp = self._decide(app_id, self.sco1)
        assert resp.code == 400, resp.body.decode()
        assert "memo" in json.loads(resp.body)["error"].lower()
        assert (self._row(app_id)["assigned_to"] or "") == ""
        assert not self._gov_rows("ownership_claimed")

    def test_successful_decision_claims_unassigned_case(self):
        app_id, _ = self._seed(risk="LOW", assigned_to=None)
        resp = self._decide(app_id, self.co1)
        assert resp.code in (200, 201), resp.body.decode()
        row = self._row(app_id)
        assert row["status"] == "approved"
        assert row["assigned_to"] == "own_co1"  # first-touch ownership at success
        assert self._gov_rows("ownership_claimed")

    def test_stale_first_approver_does_not_bypass_gate(self):
        """B2 regression: first_approver_id left over from an abandoned dual
        flow on a case whose CURRENT risk is MEDIUM must not exempt a non-owner
        from the gate (no dual control will run at this risk)."""
        app_id, _ = self._seed(risk="MEDIUM", assigned_to="own_co2",
                               first_approver="own_sco2")
        resp = self._decide(app_id, self.co1)
        assert resp.code == 403, resp.body.decode()
        assert "assigned to another officer" in json.loads(resp.body)["error"]

    def test_dual_second_leg_stays_frictionless_at_high_risk(self):
        """The exemption's intended case: owner SCO records the first HIGH
        approval; a DISTINCT admin completes it with NO override reason."""
        app_id, _ = self._seed(risk="HIGH", assigned_to="own_sco1")
        first = self._decide(app_id, self.sco1, reason="First senior approval")
        assert first.code == 202, first.body.decode()
        second = self._decide(app_id, self.admin, reason="Second senior approval")
        assert second.code in (200, 201), second.body.decode()
        assert self._row(app_id)["status"] == "approved"

    # ── Open verbs (the collaboration half of the policy) ──

    def test_escalate_edd_not_gated_for_non_owner(self):
        app_id, _ = self._seed(risk="MEDIUM", assigned_to="own_co2")
        resp = self._decide(app_id, self.co1, decision="escalate_edd",
                            reason="Escalating for enhanced due diligence")
        body = (resp.body or b"").decode()
        assert resp.code != 403 or "assigned to another officer" not in body, body
        assert not self._gov_rows("ownership_denied")

    def test_request_documents_not_gated_for_non_owner(self):
        app_id, _ = self._seed(risk="MEDIUM", assigned_to="own_co2")
        resp = self.fetch(
            f"/api/applications/{app_id}/decision", method="POST", headers=self._h(self.co1),
            body=json.dumps({
                "decision": "request_documents",
                "decision_reason": "Requesting additional documents",
                "officer_signoff": _SIGNOFF,
                "documents_list": ["Bank statement"],
                "rmi_deadline": "2026-08-01",
            }),
        )
        body = (resp.body or b"").decode()
        assert resp.code != 403 or "assigned to another officer" not in body, body
        assert not self._gov_rows("ownership_denied")
