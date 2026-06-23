"""
PR-APP-PRICING-TO-COMPLIANCE-CTA-1.

Regression coverage for the narrow Pricing Review -> Compliance Review action.
The endpoint is a non-terminal stage transition only; it must not broaden
submit-to-compliance or final approval gates.
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


class PricingToComplianceReviewTest(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path_state = _capture_db_path_state()
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"pricing_to_compliance_test_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        try:
            os.unlink(self._db_path)
        except OSError:
            pass
        _sync_test_db_path(self._db_path)

        from db import get_db, init_db, seed_initial_data
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
        for uid, role in (("admin001", "admin"), ("sco001", "sco"), ("co001", "co"), ("analyst001", "analyst")):
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
        self.client_token = create_token("ptc_client", "client", "PTC Client", "client")
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

    def _seed_app(self, app_id, ref, status, risk_level="HIGH", client_id=None):
        client_id = client_id or f"{app_id}_client"
        self.db.execute(
            "INSERT OR REPLACE INTO clients (id, email, password_hash, company_name, status) "
            "VALUES (?, ?, 'test-only', ?, 'active')",
            (client_id, f"{client_id}@example.test", f"{ref} Ltd"),
        )
        self.db.execute(
            """
            INSERT OR REPLACE INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type,
                 status, risk_score, risk_level, final_risk_level, prescreening_data)
            VALUES (?, ?, ?, ?, 'Mauritius', 'Technology', 'Company', ?, 72, ?, ?, ?)
            """,
            (app_id, ref, client_id, f"{ref} Ltd", status, risk_level, risk_level, json.dumps({"jurisdiction": risk_level})),
        )
        self.db.commit()

    def _seed_fixtures(self):
        self._seed_app("ptc_pricing", "PTC-PRICING", "pricing_review")
        self._seed_app("ptc_pricing_admin", "PTC-PRICING-ADMIN", "pricing_review", "MEDIUM")
        self._seed_app("ptc_pricing_sco", "PTC-PRICING-SCO", "pricing_review", "LOW")
        self._seed_app("ptc_kyc", "PTC-KYC", "kyc_documents", "LOW")

    def _row(self, app_id):
        return self.db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()

    def _move(self, app_id, token, note="Pricing accepted; move to compliance review"):
        return self.fetch(
            f"/api/applications/{app_id}/move-to-compliance-review",
            method="POST",
            headers=self._headers(token),
            body=json.dumps({"transition_note": note}),
        )

    def test_co_can_move_pricing_review_to_compliance_review_and_audit(self):
        resp = self._move("ptc_pricing", self.co_token)
        assert resp.code == 200, resp.body.decode()
        body = self._json(resp)
        assert body["previous_status"] == "pricing_review"
        assert body["status"] == "compliance_review"
        row = self._row("ptc_pricing")
        assert row["status"] == "compliance_review"
        assert not row["decided_at"]
        assert not row["decision_by"]

        audit_rows = self.db.execute(
            "SELECT action, user_role, detail FROM audit_log WHERE target = ? ORDER BY id DESC",
            ("PTC-PRICING",),
        ).fetchall()
        business_rows = [r for r in audit_rows if r["action"] == "Move to Compliance Review"]
        assert business_rows
        detail = json.loads(business_rows[0]["detail"])
        assert detail["event"] == "application.move_to_compliance_review"
        assert detail["from_status"] == "pricing_review"
        assert detail["to_status"] == "compliance_review"
        assert business_rows[0]["user_role"] == "co"
        assert any(
            r["action"] == "Governance Attempt"
            and "application.move_to_compliance_review" in (r["detail"] or "")
            for r in audit_rows
        )

    def test_pricing_review_gate_blocker_has_clear_move_cta(self):
        from security_hardening import collect_approval_gate_blockers

        blockers = collect_approval_gate_blockers(dict(self._row("ptc_pricing")), self.db)
        stage = next(b for b in blockers if b["id"] == "case_stage")
        assert stage["ctaLabel"] == "Move to Compliance Review"
        assert stage["action_key"] == "pricing.move_to_compliance_review"
        assert stage["action_label"] == "Move to Compliance Review"

    def test_admin_and_sco_can_move_pricing_review(self):
        assert self._move("ptc_pricing_admin", self.admin_token).code == 200
        assert self._row("ptc_pricing_admin")["status"] == "compliance_review"
        assert self._move("ptc_pricing_sco", self.sco_token).code == 200
        assert self._row("ptc_pricing_sco")["status"] == "compliance_review"

    def test_analyst_cannot_move_pricing_review(self):
        resp = self._move("ptc_pricing", self.analyst_token)
        assert resp.code == 403, resp.body.decode()
        assert self._row("ptc_pricing")["status"] == "pricing_review"

    def test_client_cannot_move_pricing_review(self):
        resp = self._move("ptc_pricing", self.client_token)
        assert resp.code == 403, resp.body.decode()
        assert self._row("ptc_pricing")["status"] == "pricing_review"

    def test_non_pricing_status_rejected(self):
        resp = self._move("ptc_kyc", self.co_token)
        assert resp.code == 409, resp.body.decode()
        body = self._json(resp)
        assert "only available from pricing_review" in body["error"]
        assert self._row("ptc_kyc")["status"] == "kyc_documents"

    def test_submit_to_compliance_still_rejects_pricing_review(self):
        resp = self.fetch(
            "/api/applications/ptc_pricing/submit-to-compliance",
            method="POST",
            headers=self._headers(self.co_token),
            body=json.dumps({"submission_note": "Routing to compliance from pricing"}),
        )
        assert resp.code == 409, resp.body.decode()
        assert self._row("ptc_pricing")["status"] == "pricing_review"
