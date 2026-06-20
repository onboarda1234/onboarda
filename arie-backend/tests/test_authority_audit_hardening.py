"""
PR-AUTHORITY-AUDIT-HARDENING-1 — audit-trail hardening tests.

Behavioural coverage for all three new audit events:
  - authz_denied_role: decorator-level role denials are now audited (one row, no
    double-log, no false positives) and the 403 response is byte-identical (additive).
  - application.override_used: a real override decision writes a first-class row;
    a non-override decision writes none.
  - application.waiver_used: a real requirement waiver writes a first-class row with
    before/after status; a non-waiver update writes none.
Plus light static assertions of the single-point require_auth audit wiring.
"""
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

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


class AuthzDenialAuditTest(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path_state = _capture_db_path_state()
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"authz_audit_test_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
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
        for uid, role in (("sco001", "sco"), ("co001", "co"), ("analyst001", "analyst")):
            self.db.execute(
                "INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status) "
                "VALUES (?, ?, 'test-only', ?, ?, 'active')",
                (uid, f"{uid}@example.test", f"{uid} Officer", role),
            )
        self.db.commit()
        self.admin_token = create_token("admin001", "admin", "Test Admin", "officer")
        self.sco_token = create_token("sco001", "sco", "Test SCO", "officer")
        self.co_token = create_token("co001", "co", "Test CO", "officer")
        self.analyst_token = create_token("analyst001", "analyst", "Test Analyst", "officer")
        import base_handler
        base_handler.rate_limiter._attempts.clear()
        self.db.execute(
            "INSERT OR REPLACE INTO clients (id, email, password_hash, company_name, status) "
            "VALUES ('aaa_c', 'aaa@example.test', 'x', 'AAA Ltd', 'active')"
        )
        self.db.execute(
            "INSERT OR REPLACE INTO applications (id, ref, client_id, company_name, country, sector, "
            "entity_type, status, risk_level, risk_score) "
            "VALUES ('aaa_app', 'AAA-1', 'aaa_c', 'AAA Ltd', 'Mauritius', 'Tech', 'Company', "
            "'in_review', 'LOW', 20)"
        )
        self.db.commit()

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

    def _h(self, token):
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _denial_rows(self):
        return self.db.execute(
            "SELECT user_role, target, detail FROM audit_log WHERE action = 'authz_denied_role' ORDER BY id"
        ).fetchall()

    def test_analyst_decision_denial_is_audited(self):
        resp = self.fetch(
            "/api/applications/aaa_app/decision", method="POST",
            headers=self._h(self.analyst_token),
            body=json.dumps({"decision": "reject", "decision_reason": "x"}),
        )
        assert resp.code == 403, resp.body.decode()
        rows = self._denial_rows()
        assert len(rows) == 1, "expected exactly one authz_denied_role row (no double-log)"
        row = rows[0]
        assert row["user_role"] == "analyst"
        detail = json.loads(row["detail"])
        assert detail["event"] == "authz_denied_role"
        assert detail["source"] == "require_auth"
        assert detail["response_code"] == 403
        assert detail["method"] == "POST"
        assert "/api/applications/aaa_app/decision" in detail["path"]
        assert "admin" in detail["allowed_roles"] and "sco" in detail["allowed_roles"]

    def test_co_memo_approve_denial_is_audited(self):
        # MemoApproveHandler is admin/SCO only — a CO is denied and audited.
        resp = self.fetch(
            "/api/applications/aaa_app/memo/approve", method="POST",
            headers=self._h(self.co_token), body=json.dumps({}),
        )
        assert resp.code == 403, resp.body.decode()
        rows = [r for r in self._denial_rows() if r["user_role"] == "co"]
        assert rows, "expected an authz_denied_role row for the CO memo-approve denial"

    def test_authorized_call_writes_no_denial_row(self):
        # Admin is allowed on /decision; it may fail later for other reasons, but the
        # role gate must NOT log a denial (no false positives).
        self.fetch(
            "/api/applications/aaa_app/decision", method="POST",
            headers=self._h(self.admin_token),
            body=json.dumps({"decision": "reject", "decision_reason": "authorized attempt"}),
        )
        assert self._denial_rows() == [], "authorized call must not write authz_denied_role"

    def test_denial_response_body_is_unchanged(self):
        # Audit is additive only — the 403 client contract is identical.
        resp = self.fetch(
            "/api/applications/aaa_app/decision", method="POST",
            headers=self._h(self.analyst_token),
            body=json.dumps({"decision": "approve", "decision_reason": "x"}),
        )
        assert resp.code == 403
        assert json.loads(resp.body.decode()) == {"error": "Insufficient permissions"}

    # ── Behavioural proof: override_used is actually written ──

    _SIGNOFF_OVERRIDE = {"acknowledged": True, "scope": "override", "source_context": "ai_advisory"}
    _SIGNOFF_DECISION = {"acknowledged": True, "scope": "decision", "source_context": "ai_advisory"}

    def _override_rows(self, ref):
        return self.db.execute(
            "SELECT user_role, detail FROM audit_log WHERE action = 'Override Used' AND target = ?",
            (ref,),
        ).fetchall()

    def test_override_decision_writes_override_used_event(self):
        # A reject with override_ai=true by SCO succeeds (reject skips the approval
        # gate stack) and must emit a first-class Override Used row.
        resp = self.fetch(
            "/api/applications/aaa_app/decision", method="POST",
            headers=self._h(self.sco_token),
            body=json.dumps({
                "decision": "reject",
                "decision_reason": "Overriding AI recommendation after manual review",
                "override_ai": True,
                "override_reason": "Manual evidence contradicts the AI risk call",
                "officer_signoff": self._SIGNOFF_OVERRIDE,
            }),
        )
        assert resp.code in (200, 201), resp.body.decode()
        rows = self._override_rows("AAA-1")
        assert len(rows) == 1, "expected exactly one Override Used row"
        assert rows[0]["user_role"] == "sco"
        detail = json.loads(rows[0]["detail"])
        assert detail["event"] == "application.override_used"
        assert detail["decision"] == "reject"
        assert detail["override_reason"] == "Manual evidence contradicts the AI risk call"
        assert detail["override_by_role"] == "sco"

    def test_decision_without_override_writes_no_override_used_event(self):
        resp = self.fetch(
            "/api/applications/aaa_app/decision", method="POST",
            headers=self._h(self.sco_token),
            body=json.dumps({
                "decision": "reject",
                "decision_reason": "Routine rejection, no override",
                "officer_signoff": self._SIGNOFF_DECISION,
            }),
        )
        assert resp.code in (200, 201), resp.body.decode()
        assert self._override_rows("AAA-1") == [], "no Override Used row without override_ai"

    # ── Behavioural proof: waiver_used is actually written ──

    def _seed_enhanced_requirement(self, app_id, status="under_review"):
        self.db.execute(
            "INSERT INTO application_enhanced_requirements "
            "(application_id, trigger_key, trigger_label, requirement_key, requirement_label, "
            " requirement_type, waivable, blocking_approval, mandatory, status) "
            "VALUES (?, 'high_risk', 'High Risk', 'src_of_wealth', 'Source of Wealth', "
            " 'document', 1, 1, 1, ?)",
            (app_id, status),
        )
        self.db.commit()
        row = self.db.execute(
            "SELECT id FROM application_enhanced_requirements WHERE application_id = ? ORDER BY id DESC LIMIT 1",
            (app_id,),
        ).fetchone()
        return dict(row)["id"]

    def _waiver_rows(self, ref):
        return self.db.execute(
            "SELECT user_role, detail FROM audit_log WHERE action = 'Waiver Used' AND target = ?",
            (ref,),
        ).fetchall()

    def test_waiving_requirement_writes_waiver_used_event(self):
        req_id = self._seed_enhanced_requirement("aaa_app", status="under_review")
        resp = self.fetch(
            f"/api/applications/aaa_app/enhanced-requirements/{req_id}", method="PATCH",
            headers=self._h(self.sco_token),
            body=json.dumps({"status": "waived", "waiver_reason": "Mitigated by independent evidence pack"}),
        )
        assert resp.code == 200, resp.body.decode()
        rows = self._waiver_rows("AAA-1")
        assert len(rows) == 1, "expected exactly one Waiver Used row"
        assert rows[0]["user_role"] == "sco"
        detail = json.loads(rows[0]["detail"])
        assert detail["event"] == "application.waiver_used"
        assert str(detail["requirement_id"]) == str(req_id)
        assert detail["waiver_reason"] == "Mitigated by independent evidence pack"
        assert detail["previous_status"] == "under_review"
        assert detail["new_status"] == "waived"

    def test_non_waiver_requirement_update_writes_no_waiver_event(self):
        req_id = self._seed_enhanced_requirement("aaa_app", status="generated")
        resp = self.fetch(
            f"/api/applications/aaa_app/enhanced-requirements/{req_id}", method="PATCH",
            headers=self._h(self.sco_token),
            body=json.dumps({"status": "under_review"}),
        )
        assert resp.code == 200, resp.body.decode()
        assert self._waiver_rows("AAA-1") == [], "no Waiver Used row for a non-waiver update"


# ── Static assertions: first-class override/waiver events are emitted ──

def _server_src():
    return (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")


def test_override_used_event_is_emitted():
    src = _server_src()
    # First-class, filterable action + canonical event key + structured reason.
    assert '"Override Used"' in src
    assert '"event": "application.override_used"' in src
    assert '"override_reason": override_reason' in src
    # Gated on override_ai (only emitted when the AI recommendation was overridden).
    assert "if override_ai:" in src


def test_waiver_used_event_is_emitted():
    src = _server_src()
    assert '"Waiver Used"' in src
    assert '"event": "application.waiver_used"' in src
    assert '"requirement_id": requirement_id' in src
    assert '"waiver_reason": str(data.get("waiver_reason")' in src


def test_require_auth_audits_role_denial_at_single_point():
    base = (Path(__file__).resolve().parents[1] / "base_handler.py").read_text(encoding="utf-8")
    fn = base.split("def require_auth(", 1)[1].split("def require_backoffice_auth(", 1)[0]
    assert 'log_authz_denial(' in fn
    assert '"authz_denied_role"' in fn
    assert '"source": "require_auth"' in fn
