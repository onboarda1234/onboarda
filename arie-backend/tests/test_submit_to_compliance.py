"""
PR-SUBMIT-TO-COMPLIANCE-WORKFLOW-1 — Submit to Compliance handoff tests.

Verifies:
1. An Onboarding Officer (and SCO/Admin) can submit a case to compliance from any
   active review lane.
2. Submission is allowed EVEN WHEN final approval is blocked (HIGH/VERY_HIGH,
   second-review pending, EDD) — the whole point of the workflow.
3. Submission does NOT approve, reject, or set a decision; it only routes.
4. Analyst/client cannot submit; invalid from-status and missing reason are rejected.
5. The handoff is audited (application.submit_to_compliance + "Submit to Compliance").
6. mandatory vs discretionary classification.
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


class SubmitToComplianceTest(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path_state = _capture_db_path_state()
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"submit_compliance_test_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
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
        self.client_token = create_token("stc_client", "client", "STC Client", "client")
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
                 status, risk_score, risk_level, final_risk_level, prescreening_data)
            VALUES (?, ?, ?, ?, 'Mauritius', 'Technology', 'Company', ?, ?, ?, ?, ?)
            """,
            (app_id, ref, f"{app_id}_client", f"{ref} Ltd", status, risk_score,
             risk_level, risk_level, json.dumps({"jurisdiction": risk_level})),
        )
        self.db.commit()

    def _seed_fixtures(self):
        self._seed_app("stc_low", "STC-LOW", "compliance_review", "LOW", 20)
        self._seed_app("stc_high", "STC-HIGH", "in_review", "HIGH", 80)
        self._seed_app("stc_draft", "STC-DRAFT", "draft", "LOW", 20)
        self._seed_app("stc_idem", "STC-IDEM", "compliance_review", "MEDIUM", 45)
        # EDD-required case: the lane where an Onboarding Officer most needs a
        # forward action (cannot complete EDD, cannot approve).
        self._seed_app("stc_edd", "STC-EDD", "edd_required", "HIGH", 80)
        # PEP-flagged case (MEDIUM so authority is not the driver — PEP basis is).
        self._seed_app("stc_pep", "STC-PEP", "compliance_review", "MEDIUM", 45)
        self.db.execute(
            "INSERT OR REPLACE INTO directors (id, application_id, full_name, is_pep, pep_declaration) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "stc_pep_dir",
                "stc_pep",
                "Jane PEP",
                "Yes",
                json.dumps({"declared_pep": True, "client_declared_pep": True, "pep_status": "declared_yes"}),
            ),
        )
        # Provider-only PEP match: this must remain screening evidence, not a
        # declared/officer-confirmed party PEP.
        self._seed_app("stc_provider_pep", "STC-PROVIDER-PEP", "compliance_review", "MEDIUM", 45)
        self.db.execute(
            """
            UPDATE applications SET prescreening_data=? WHERE id=?
            """,
            (
                json.dumps(
                    {
                        "screening_report": {
                            "director_screenings": [
                                {
                                    "person_name": "Provider Match",
                                    "name": "Provider Match",
                                    "undeclared_pep": True,
                                    "provider_detected_pep": True,
                                    "has_pep_hit": True,
                                }
                            ],
                            "ubo_screenings": [],
                        }
                    }
                ),
                "stc_provider_pep",
            ),
        )
        self.db.execute(
            "INSERT OR REPLACE INTO directors (id, application_id, full_name, is_pep, pep_declaration) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "stc_provider_pep_dir",
                "stc_provider_pep",
                "Provider Match",
                "No",
                json.dumps({"declared_pep": False, "client_declared_pep": False, "pep_status": "declared_no"}),
            ),
        )
        self.db.commit()

    def _app_row(self, app_id):
        return self.db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()

    def _submit(self, app_id, token, note="Routing to senior compliance for review"):
        return self.fetch(
            f"/api/applications/{app_id}/submit-to-compliance", method="POST",
            headers=self._headers(token),
            body=json.dumps({"submission_note": note}),
        )

    # ── Happy paths ──

    def test_co_can_submit_from_compliance_review(self):
        resp = self._submit("stc_low", self.co_token)
        assert resp.code == 200, resp.body.decode()
        body = self._json(resp)
        assert body["status"] == "submitted_to_compliance"
        row = self._app_row("stc_low")
        assert row["status"] == "submitted_to_compliance"
        assert row["submitted_to_compliance_by"] == "co001"
        assert row["submission_note"]
        # CO could approve a clean LOW file → discretionary escalation.
        assert body["submission_kind"] == "discretionary"
        assert body["submission_basis"] == []

    def test_submit_allowed_even_when_approval_blocked_high_risk(self):
        # The core rule: a CO who CANNOT approve a HIGH file can still submit it.
        resp = self._submit("stc_high", self.co_token)
        assert resp.code == 200, resp.body.decode()
        body = self._json(resp)
        assert body["submission_kind"] == "mandatory"
        assert "high_risk" in body["submission_basis"]
        assert "authority_blocked" in body["submission_basis"]
        assert self._app_row("stc_high")["status"] == "submitted_to_compliance"

    def test_co_can_submit_edd_required(self):
        # P0 regression guard: edd_required must NOT be a dead-end for an
        # Onboarding Officer. Submission must be allowed and tagged edd_required.
        resp = self._submit("stc_edd", self.co_token)
        assert resp.code == 200, resp.body.decode()
        body = self._json(resp)
        assert body["status"] == "submitted_to_compliance"
        assert "edd_required" in body["submission_basis"]
        assert body["submission_kind"] == "mandatory"
        row = self._app_row("stc_edd")
        assert row["status"] == "submitted_to_compliance"
        # No decision was written.
        assert not row["decided_at"]
        assert not row["decision_by"]

    def test_declared_pep_case_basis_is_explicit(self):
        resp = self._submit("stc_pep", self.co_token)
        assert resp.code == 200, resp.body.decode()
        basis = self._json(resp)["submission_basis"]
        assert "declared_pep_present" in basis
        assert "pep" not in basis

    def test_provider_pep_case_basis_is_screening_review_not_declared_pep(self):
        resp = self._submit("stc_provider_pep", self.co_token)
        assert resp.code == 200, resp.body.decode()
        basis = self._json(resp)["submission_basis"]
        assert "provider_pep_match_unresolved" in basis
        assert "screening_pep_review_required" in basis
        assert "declared_pep_present" not in basis
        assert "pep" not in basis

    def test_sco_and_admin_can_submit(self):
        assert self._submit("stc_low", self.sco_token).code == 200
        self._seed_app("stc_low2", "STC-LOW2", "in_review", "LOW", 20)
        assert self._submit("stc_low2", self.admin_token).code == 200

    # ── It does NOT decide ──

    def test_submit_does_not_approve_or_set_decision(self):
        self._submit("stc_high", self.co_token)
        row = self._app_row("stc_high")
        assert row["status"] == "submitted_to_compliance"  # not approved/rejected
        assert not row["decided_at"]
        assert not row["decision_by"]

    # ── Authorization ──

    def test_analyst_cannot_submit(self):
        resp = self._submit("stc_low", self.analyst_token)
        assert resp.code == 403, resp.body.decode()
        assert self._app_row("stc_low")["status"] == "compliance_review"

    def test_client_cannot_submit(self):
        resp = self._submit("stc_low", self.client_token)
        assert resp.code in (401, 403), resp.body.decode()
        assert self._app_row("stc_low")["status"] == "compliance_review"

    # ── Guards ──

    def test_submit_from_invalid_status_blocked(self):
        resp = self._submit("stc_draft", self.co_token)
        assert resp.code == 409, resp.body.decode()
        assert self._app_row("stc_draft")["status"] == "draft"

    def test_submission_note_required(self):
        resp = self._submit("stc_low", self.co_token, note="short")
        assert resp.code == 400, resp.body.decode()
        assert self._app_row("stc_low")["status"] == "compliance_review"

    def test_idempotent_resubmit(self):
        first = self._submit("stc_idem", self.co_token)
        assert first.code == 200
        second = self._submit("stc_idem", self.co_token)
        assert second.code == 200
        assert "already" in self._json(second)["message"].lower()

    # ── Audit ──

    def test_submit_is_audited(self):
        self._submit("stc_high", self.co_token)
        rows = self.db.execute(
            "SELECT user_role, action, detail FROM audit_log WHERE target = ? ORDER BY id DESC",
            ("STC-HIGH",),
        ).fetchall()
        # Governance attempt row (accepted) with the canonical action key.
        gov = [r for r in rows if r["action"] == "Governance Attempt"
               and "application.submit_to_compliance" in (r["detail"] or "")]
        assert gov, "expected application.submit_to_compliance governance row"
        assert gov[0]["user_role"] == "co"
        # Business-event row carrying before/after + basis.
        biz = [r for r in rows if r["action"] == "Submit to Compliance"]
        assert biz, "expected 'Submit to Compliance' business audit row"
        detail = json.loads(biz[0]["detail"])
        assert detail["from_status"] == "in_review"
        assert detail["to_status"] == "submitted_to_compliance"
        assert "high_risk" in detail["submission_basis"]


# ── Static checks: status label + portal neutrality + UI control ──

def test_status_label_registered():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import branding
    assert branding.STATUS_LABELS.get("submitted_to_compliance") == "Submitted to Compliance"


def test_portal_keeps_submitted_to_compliance_neutral():
    from pathlib import Path
    from tests.test_portal_pilot_boundary_static import _extract_js_object_property, _extract_js_var_object

    portal = (Path(__file__).resolve().parents[2] / "arie-portal.html").read_text(encoding="utf-8")
    projection_source = _extract_js_var_object(portal, "PORTAL_STATUS_PROJECTIONS")
    submitted_to_compliance = _extract_js_object_property(projection_source, "submitted_to_compliance")

    # The applicant-facing status label must be the neutral "Under Review", and the
    # status must route to the compliance-hold (neutral) view.
    assert "badge: 'Under Review'" in submitted_to_compliance
    assert "statusLabel: 'Under Review'" in submitted_to_compliance
    assert "view: 'compliance-hold'" in submitted_to_compliance
    # The internal status key must never be mapped to a label that leaks mechanics
    # (e.g. an applicant-facing 'Submitted to Compliance' status badge).
    assert "Submitted to Compliance" not in submitted_to_compliance


def test_backoffice_has_submit_to_compliance_control():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[2] / "arie-backoffice.html").read_text(encoding="utf-8")
    assert 'id="btn-submit-compliance"' in html
    assert "function submitToCompliance(" in html
    assert "/submit-to-compliance" in html
