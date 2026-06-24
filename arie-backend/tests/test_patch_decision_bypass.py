"""
PR-APPROVAL-AUTHORITY-MATRIX-1 — terminal-decision authority tests.

Proves the audit finding P0-1 fix:
1. Generic PATCH /api/applications/:id CANNOT set terminal status
   (approved/rejected) for ANY role — it is routed to /decision and the
   attempt is audited as application.decision_blocked.
2. The canonical /decision endpoint still enforces the authority matrix
   (analyst cannot decide; Onboarding Officer cannot approve HIGH/VERY_HIGH).
3. Non-terminal PATCH transitions still work (regression guard).
4. The centralized can_decide_application gate enforces the full
   role x current-risk x decision x override matrix (pure unit tests).
"""
import json
import os
import sys
import tempfile
import uuid

from tornado.testing import AsyncHTTPTestCase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _sync_test_db_path(path):
    os.environ["DB_PATH"] = path
    for module_name in ("config", "db", "server"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "DB_PATH"):
            setattr(module, "DB_PATH", path)
        if module_name == "server" and module is not None and hasattr(module, "_CFG_DB_PATH"):
            setattr(module, "_CFG_DB_PATH", path)


def _capture_db_path_state():
    state = {"env": os.environ.get("DB_PATH"), "modules": {}}
    for module_name in ("config", "db", "server"):
        module = sys.modules.get(module_name)
        attrs = {}
        if module is not None:
            for attr in ("DB_PATH", "_CFG_DB_PATH"):
                attrs[attr] = (hasattr(module, attr), getattr(module, attr, None))
        state["modules"][module_name] = attrs
    return state


def _restore_db_path_state(state):
    original_env = state.get("env")
    if original_env is None:
        os.environ.pop("DB_PATH", None)
    else:
        os.environ["DB_PATH"] = original_env
    for module_name, attrs in state.get("modules", {}).items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for attr, (existed, value) in attrs.items():
            if existed:
                setattr(module, attr, value)
            elif hasattr(module, attr):
                delattr(module, attr)


class PatchDecisionBypassTest(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path_state = _capture_db_path_state()
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"patch_decision_test_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
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
        from db import get_db
        from server import create_token

        self.db = get_db()
        self.admin_token = create_token("admin001", "admin", "Test Admin", "officer")
        # Seed non-default officer accounts so their tokens validate as active actors.
        for uid, role in (("sco001", "sco"), ("co001", "co"), ("analyst001", "analyst")):
            self.db.execute(
                "INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status) "
                "VALUES (?, ?, 'test-only', ?, ?, 'active')",
                (uid, f"{uid}@example.test", f"{uid} Officer", role),
            )
        self.db.commit()
        self.sco_token = create_token("sco001", "sco", "Test SCO", "officer")
        self.co_token = create_token("co001", "co", "Test CO", "officer")
        self.analyst_token = create_token("analyst001", "analyst", "Test Analyst", "officer")
        # /decision is rate-limited via a process-global limiter; reset it so a
        # decision-heavy suite ordering cannot trip an unrelated 429.
        import base_handler
        base_handler.rate_limiter._attempts.clear()
        self._seed_fixtures()

    def tearDown(self):
        self.db.close()
        super().tearDown()
        db_path = getattr(self, "_db_path", None)
        if db_path:
            try:
                os.unlink(db_path)
            except OSError:
                pass
        _restore_db_path_state(getattr(self, "_db_path_state", {}))

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _json(self, response):
        return json.loads(response.body.decode() or "{}")

    def _seed_app(self, app_id, ref, status, risk_level, risk_score):
        self.db.execute(
            "INSERT OR REPLACE INTO clients (id, email, password_hash, company_name, status) "
            "VALUES (?, ?, 'test-only', ?, 'active')",
            (f"{app_id}_client", f"{app_id}@example.test", f"{ref} Ltd"),
        )
        self.db.execute(
            """
            INSERT OR REPLACE INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type,
                 status, risk_score, risk_level, prescreening_data)
            VALUES (?, ?, ?, ?, 'Mauritius', 'Technology', 'Company', ?, ?, ?, ?)
            """,
            (app_id, ref, f"{app_id}_client", f"{ref} Ltd", status, risk_score, risk_level,
             json.dumps({"jurisdiction": risk_level})),
        )
        self.db.commit()

    def _seed_fixtures(self):
        self._seed_app("pdb_low", "PDB-LOW", "in_review", "LOW", 20)
        self._seed_app("pdb_high", "PDB-HIGH", "in_review", "HIGH", 80)
        self._seed_app("pdb_move", "PDB-MOVE", "compliance_review", "LOW", 20)

    def _status_of(self, app_id):
        row = self.db.execute("SELECT status FROM applications WHERE id = ?", (app_id,)).fetchone()
        return row["status"] if row else None

    # ── PATCH cannot perform terminal decisions (the P0-1 fix) ──

    def test_patch_cannot_approve_even_as_admin(self):
        resp = self.fetch(
            "/api/applications/pdb_low", method="PATCH",
            headers=self._headers(self.admin_token),
            body=json.dumps({"status": "approved"}),
        )
        assert resp.code == 409, resp.body.decode()
        assert "Terminal decision blocked" in self._json(resp)["error"]
        # Status must be unchanged — no terminal write occurred.
        assert self._status_of("pdb_low") == "in_review"

    def test_patch_cannot_reject_as_analyst(self):
        resp = self.fetch(
            "/api/applications/pdb_low", method="PATCH",
            headers=self._headers(self.analyst_token),
            body=json.dumps({"status": "rejected"}),
        )
        assert resp.code == 409, resp.body.decode()
        assert self._status_of("pdb_low") == "in_review"

    def test_patch_cannot_approve_high_as_co(self):
        resp = self.fetch(
            "/api/applications/pdb_high", method="PATCH",
            headers=self._headers(self.co_token),
            body=json.dumps({"status": "approved"}),
        )
        assert resp.code == 409, resp.body.decode()
        assert self._status_of("pdb_high") == "in_review"

    def test_patch_terminal_block_is_audited(self):
        self.fetch(
            "/api/applications/pdb_low", method="PATCH",
            headers=self._headers(self.admin_token),
            body=json.dumps({"status": "approved"}),
        )
        rows = self.db.execute(
            "SELECT detail FROM audit_log WHERE target = ? ORDER BY id DESC", ("PDB-LOW",),
        ).fetchall()
        assert any("application.decision_blocked" in (r["detail"] or "") for r in rows), \
            "expected an application.decision_blocked audit row"

    # ── Non-terminal PATCH still works (regression guard) ──

    def test_patch_non_terminal_transition_still_works(self):
        resp = self.fetch(
            "/api/applications/pdb_move", method="PATCH",
            headers=self._headers(self.admin_token),
            body=json.dumps({"status": "in_review"}),
        )
        assert resp.code == 200, resp.body.decode()
        assert self._status_of("pdb_move") == "in_review"

    # ── /decision still enforces the authority matrix ──

    def test_decision_blocks_analyst(self):
        resp = self.fetch(
            "/api/applications/pdb_low/decision", method="POST",
            headers=self._headers(self.analyst_token),
            body=json.dumps({"decision": "reject", "decision_reason": "test"}),
        )
        assert resp.code == 403, resp.body.decode()

    def test_decision_blocks_co_high_approve(self):
        signoff = {"acknowledged": True, "scope": "decision", "source_context": "ai_advisory"}
        resp = self.fetch(
            "/api/applications/pdb_high/decision", method="POST",
            headers=self._headers(self.co_token),
            body=json.dumps({
                "decision": "approve",
                "decision_reason": "Looks fine to me",
                "officer_signoff": signoff,
            }),
        )
        assert resp.code == 403, resp.body.decode()
        assert "Onboarding Officers cannot approve HIGH" in self._json(resp)["error"]
        assert self._status_of("pdb_high") == "in_review"

    def test_patch_sco_and_admin_also_cannot_set_terminal(self):
        # Terminal PATCH is blocked for EVERY role, including senior ones.
        for token in (self.sco_token, self.admin_token):
            resp = self.fetch(
                "/api/applications/pdb_low", method="PATCH",
                headers=self._headers(token),
                body=json.dumps({"status": "rejected"}),
            )
            assert resp.code == 409, resp.body.decode()
        assert self._status_of("pdb_low") == "in_review"

    def test_patch_terminal_block_audit_detail_is_rich(self):
        self.fetch(
            "/api/applications/pdb_high", method="PATCH",
            headers=self._headers(self.co_token),
            body=json.dumps({"status": "approved", "notes": "attempt"}),
        )
        row = self.db.execute(
            "SELECT user_role, detail FROM audit_log WHERE target = ? AND action = 'Governance Attempt' "
            "ORDER BY id DESC LIMIT 1",
            ("PDB-HIGH",),
        ).fetchone()
        assert row is not None
        # Actor role is captured as a first-class column.
        assert row["user_role"] == "co"
        detail = json.loads(row["detail"])
        assert detail["action"] == "application.decision_blocked"
        assert detail["response_code"] == 409
        ps = detail["payload_summary"]
        assert ps["attempted_status"] == "approved"
        assert ps["from_status"] == "in_review"
        assert ps["source_surface"] == "application_status_patch"

    def test_patch_terminal_block_normalizes_status_for_audit(self):
        resp = self.fetch(
            "/api/applications/pdb_low", method="PATCH",
            headers=self._headers(self.admin_token),
            body=json.dumps({"status": " Approved "}),
        )
        assert resp.code == 409, resp.body.decode()
        assert "Terminal decision blocked" in self._json(resp)["error"]
        assert self._status_of("pdb_low") == "in_review"

        row = self.db.execute(
            "SELECT detail FROM audit_log WHERE target = ? AND action = 'Governance Attempt' "
            "ORDER BY id DESC LIMIT 1",
            ("PDB-LOW",),
        ).fetchone()
        detail = json.loads(row["detail"])
        ps = detail["payload_summary"]
        assert ps["attempted_status"] == " Approved "
        assert ps["normalized_status"] == "approved"

    def test_decision_co_low_approve_is_not_role_blocked(self):
        # CO retains LOW/MEDIUM approval authority: the gate must NOT 403 a CO on a
        # LOW file. It proceeds to the readiness gates (here: missing screening/docs
        # -> 400), proving authority != readiness and LOW does not require memo first.
        signoff = {"acknowledged": True, "scope": "decision", "source_context": "ai_advisory"}
        resp = self.fetch(
            "/api/applications/pdb_low/decision", method="POST",
            headers=self._headers(self.co_token),
            body=json.dumps({
                "decision": "approve",
                "decision_reason": "Clean low-risk file",
                "officer_signoff": signoff,
            }),
        )
        assert resp.code != 403, resp.body.decode()
        assert resp.code == 400
        error = self._json(resp)["error"].lower()
        assert "screening" in error
        assert "memo" not in error
        assert self._status_of("pdb_low") == "in_review"


# ── Pure unit tests for the centralized authority gate ──

def _user(role):
    return {"sub": f"{role}001", "name": role, "role": role}


def test_can_decide_co_can_approve_low_and_medium():
    from security_hardening import can_decide_application
    for level in ("LOW", "MEDIUM"):
        allowed, code, _reason, meta = can_decide_application(
            _user("co"), {}, "approve", risk_level=level)
        assert allowed is True
        assert code == 200
        assert meta["requires_dual_approval"] is False


def test_can_decide_co_cannot_approve_high_or_very_high():
    from security_hardening import can_decide_application
    for level in ("HIGH", "VERY_HIGH"):
        allowed, code, reason, _meta = can_decide_application(
            _user("co"), {}, "approve", risk_level=level)
        assert allowed is False
        assert code == 403
        assert "Onboarding Officers cannot approve" in reason


def test_can_decide_fails_closed_when_current_risk_missing():
    from security_hardening import can_decide_application
    for missing_level in (None, ""):
        allowed, code, reason, meta = can_decide_application(
            _user("admin"), {}, "approve", risk_level=missing_level)
        assert allowed is False
        assert code == 400
        assert "Current risk level is required" in reason
        assert meta["risk_level"] is None


def test_can_decide_sco_can_approve_high_requires_dual():
    from security_hardening import can_decide_application
    allowed, code, _reason, meta = can_decide_application(
        _user("sco"), {}, "approve", risk_level="HIGH")
    assert allowed is True and code == 200
    assert meta["requires_dual_approval"] is True
    assert meta["is_privileged_admin_action"] is False


def test_can_decide_admin_high_is_privileged():
    from security_hardening import can_decide_application
    allowed, _code, _reason, meta = can_decide_application(
        _user("admin"), {}, "approve", risk_level="VERY_HIGH")
    assert allowed is True
    assert meta["is_privileged_admin_action"] is True


def test_can_decide_analyst_cannot_approve_or_reject():
    from security_hardening import can_decide_application
    for decision in ("approve", "reject"):
        allowed, code, reason, _meta = can_decide_application(
            _user("analyst"), {}, decision, risk_level="LOW")
        assert allowed is False
        assert code == 403
        assert "Admin, Senior Compliance Officer, or Onboarding Officer" in reason


def test_can_decide_client_cannot_decide():
    from security_hardening import can_decide_application
    allowed, code, _reason, _meta = can_decide_application(
        _user("client"), {}, "approve", risk_level="LOW")
    assert allowed is False and code == 403


def test_can_decide_co_and_sco_can_reject():
    from security_hardening import can_decide_application
    for role in ("co", "sco", "admin"):
        allowed, code, _reason, _meta = can_decide_application(
            _user(role), {}, "reject", risk_level="HIGH")
        assert allowed is True, role
        assert code == 200


def test_can_decide_override_is_senior_only():
    from security_hardening import can_decide_application
    blocked, code, reason, _meta = can_decide_application(
        _user("co"), {}, "approve", risk_level="LOW", override_ai=True)
    assert blocked is False
    assert code == 403
    assert "AI override requires Senior Compliance Officer or Admin role" in reason
    for role in ("sco", "admin"):
        allowed, _code, _reason, _meta = can_decide_application(
            _user(role), {}, "approve", risk_level="LOW", override_ai=True)
        assert allowed is True, role


def test_can_decide_rejects_unsupported_decision():
    from security_hardening import can_decide_application
    allowed, code, _reason, _meta = can_decide_application(
        _user("admin"), {}, "escalate_edd", risk_level="LOW")
    assert allowed is False
    assert code == 400


def test_can_decide_override_on_reject_is_senior_only():
    """Override semantics apply to reject too: CO override-reject is senior-only."""
    from security_hardening import can_decide_application
    blocked, code, reason, _meta = can_decide_application(
        _user("co"), {}, "reject", risk_level="LOW", override_ai=True)
    assert blocked is False
    assert code == 403
    assert "AI override requires Senior Compliance Officer or Admin role" in reason
    for role in ("sco", "admin"):
        allowed, ok_code, _reason, _meta = can_decide_application(
            _user(role), {}, "reject", risk_level="LOW", override_ai=True)
        assert allowed is True, role
        assert ok_code == 200
