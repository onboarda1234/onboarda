"""
E2E-AUTHORITY-MATRIX-1 — end-to-end authority regression lock.

Proves PR1-PR4 compose correctly across the full authority matrix, asserting both
behaviour AND the audit trail, including the success paths that no single PR covered:

  * CO can APPROVE a clean LOW/MEDIUM file (decision recorded, not privileged).
  * HIGH/VERY_HIGH dual approval: first senior -> 202, distinct second -> approved,
    with is_privileged_admin_action flagged for an admin approver.
  * CO cannot approve HIGH but CAN submit it to compliance (no decision written).
  * Generic PATCH terminal-status bypass stays closed (409 + decision_blocked).
  * Analyst cannot decide and the denial is audited (authz_denied_role).
  * A case's authority lifecycle is reconstructable from audit_log.

Adjacent matrix cells already locked by focused suites (not duplicated here):
  * same-user / non-SCO screening second review -> test_screening_review.py
  * authz_denied_role / override_used / waiver_used details -> test_authority_audit_hardening.py
  * PATCH terminal bypass unit matrix -> test_patch_decision_bypass.py
  * submit-to-compliance blocker/basis matrix -> test_submit_to_compliance.py

Part B (role x risk x status button/flow browser E2E on staging) is a Codex run,
delivered as evidence under pr5-e2e-authority-matrix-evidence-<UTC>.

This suite is the sign-off gate; it does not change production behaviour.
"""
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

from tornado.testing import AsyncHTTPTestCase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SIGNOFF = {"acknowledged": True, "scope": "decision", "source_context": "ai_advisory"}


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


def _live_clear_prescreening():
    now = datetime.now(timezone.utc)
    return json.dumps({
        "screening_report": {
            "screening_mode": "live",
            "screened_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "sanctions": {"api_status": "live", "matched": False, "results": []},
            "company_registry": {"api_status": "live"},
            "ip_geolocation": {"api_status": "live"},
            "kyc": {"api_status": "live", "matched": False, "results": []},
        },
        "screening_valid_until": (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S"),
        "screening_validity_days": 90,
    })


class E2EAuthorityMatrixTest(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path_state = _capture_db_path_state()
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"e2e_authority_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
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
        # admin001 is seeded; sco001 is the second distinct senior approver.
        for uid, role in (("sco001", "sco"), ("sco002", "sco"), ("co001", "co"), ("analyst001", "analyst")):
            self.db.execute(
                "INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status) "
                "VALUES (?, ?, 'test-only', ?, ?, 'active')",
                (uid, f"{uid}@example.test", f"{uid} Officer", role),
            )
        self.db.commit()
        self.admin_token = create_token("admin001", "admin", "Test Admin", "officer")
        self.sco_token = create_token("sco001", "sco", "Test SCO", "officer")
        self.sco2_token = create_token("sco002", "sco", "Second SCO", "officer")
        self.co_token = create_token("co001", "co", "Test CO", "officer")
        self.analyst_token = create_token("analyst001", "analyst", "Test Analyst", "officer")
        import base_handler
        base_handler.rate_limiter._attempts.clear()

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

    # ── helpers ──

    def _h(self, token):
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _json(self, r):
        return json.loads(r.body.decode() or "{}")

    def _status_of(self, app_id):
        row = self.db.execute("SELECT status, decided_at, decision_by FROM applications WHERE id=?", (app_id,)).fetchone()
        return dict(row) if row else None

    def _decision_notes(self, app_id):
        row = self.db.execute("SELECT decision_notes FROM applications WHERE id=?", (app_id,)).fetchone()
        raw = dict(row).get("decision_notes") if row else None
        try:
            return json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            return {}

    def _audit(self, ref, action=None):
        if action:
            return self.db.execute(
                "SELECT user_role, detail FROM audit_log WHERE target=? AND action=? ORDER BY id",
                (ref, action),
            ).fetchall()
        return self.db.execute(
            "SELECT action, user_role, detail FROM audit_log WHERE target=? ORDER BY id", (ref,),
        ).fetchall()

    def _seed_approvable(
        self,
        risk_level="LOW",
        status="compliance_review",
        *,
        with_memo=True,
        documents_ready=True,
        prescreening_data=None,
        director_pep=False,
    ):
        """Insert a fully-approvable application (all gates pass)."""
        from tests.conftest import insert_verified_required_documents
        suffix = uuid.uuid4().hex[:8]
        app_id = f"e2e_{suffix}"
        app_ref = f"E2E-{suffix}"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        score = {"LOW": 20, "MEDIUM": 50}.get(risk_level, 78)
        # NB: deliberately no client row — mirrors the proven approvable fixture in
        # test_api.py. A real client would require client-IDV resolution; this suite
        # tests decision-time AUTHORITY + audit, not IDV onboarding.
        self.db.execute(
            """
            INSERT INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type,
                 status, risk_level, final_risk_level, risk_score,
                 prescreening_data, screening_mode, submitted_at, created_at, updated_at, inputs_updated_at)
            VALUES (?, ?, ?, ?, 'Mauritius', 'Technology', 'SME', ?, ?, ?, ?, ?, 'live', ?, ?, ?, ?)
            """,
            (app_id, app_ref, f"{app_id}_c", f"{app_ref} Ltd", status, risk_level, risk_level,
             score, prescreening_data or _live_clear_prescreening(), now, now, now, now),
        )
        if director_pep:
            self.db.execute(
                "INSERT INTO directors (id, application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?, ?)",
                (f"{app_id}_pep_dir", app_id, "Jane PEP", "Mauritius", "Yes"),
            )
        if with_memo:
            self.db.execute(
                """
                INSERT INTO compliance_memos
                    (application_id, memo_data, generated_by, ai_recommendation,
                     review_status, quality_score, validation_status, supervisor_status, approval_reason)
                VALUES (?, ?, 'system', 'APPROVE', 'approved', 9.0, 'pass', 'CONSISTENT', 'Fixture approval reason')
                """,
                (app_id, json.dumps({
                    "ai_source": "deterministic",
                    "metadata": {"ai_source": "deterministic", "edd_routing": {"route": "standard", "triggers": []}},
                    "supervisor": {"verdict": "CONSISTENT", "can_approve": True, "mandatory_escalation": False},
                })),
            )
        if documents_ready:
            insert_verified_required_documents(self.db, app_id)
        # HIGH/VERY_HIGH applications require generated + resolved enhanced-review
        # requirements before approval; seed one already-accepted requirement so the
        # gate passes (this suite tests authority, not enhanced-requirement triage).
        if risk_level in ("HIGH", "VERY_HIGH"):
            self.db.execute(
                "INSERT INTO application_enhanced_requirements "
                "(application_id, trigger_key, trigger_label, requirement_key, requirement_label, "
                " requirement_type, waivable, blocking_approval, mandatory, status) "
                "VALUES (?, 'high_risk', 'High Risk', 'source_wealth', 'Source of Wealth', "
                " 'document', 1, 1, 1, 'accepted')",
                (app_id,),
            )
        self.db.commit()
        return app_id, app_ref

    def _approve(self, app_id, token, reason="E2E approval"):
        return self.fetch(
            f"/api/applications/{app_id}/decision", method="POST", headers=self._h(token),
            body=json.dumps({"decision": "approve", "decision_reason": reason, "officer_signoff": _SIGNOFF}),
        )

    # ── SUCCESS PATHS (the coverage gap) ──

    def test_co_approves_clean_low(self):
        app_id, ref = self._seed_approvable("LOW")
        resp = self._approve(app_id, self.co_token)
        assert resp.code in (200, 201), resp.body.decode()
        row = self._status_of(app_id)
        assert row["status"] == "approved"
        assert row["decision_by"] == "co001"
        decision_rows = self.db.execute(
            "SELECT user_role FROM audit_log WHERE target=? AND action='Decision' ORDER BY id",
            (ref,),
        ).fetchall()
        assert len(decision_rows) == 1
        assert decision_rows[0]["user_role"] == "co"
        # A CO LOW approval is a normal decision, not a privileged admin action.
        assert self._decision_notes(app_id).get("is_privileged_admin_action") in (False, None)

    def test_co_approves_clean_medium(self):
        app_id, ref = self._seed_approvable("MEDIUM")
        resp = self._approve(app_id, self.co_token)
        assert resp.code in (200, 201), resp.body.decode()
        assert self._status_of(app_id)["status"] == "approved"

    def test_co_approves_clean_low_without_compliance_memo(self):
        app_id, ref = self._seed_approvable("LOW", with_memo=False)
        resp = self._approve(app_id, self.co_token, reason="Direct clean LOW approval")
        assert resp.code in (200, 201), resp.body.decode()
        row = self._status_of(app_id)
        assert row["status"] == "approved"
        assert row["decision_by"] == "co001"
        assert self.db.execute(
            "SELECT COUNT(*) AS c FROM compliance_memos WHERE application_id=?", (app_id,),
        ).fetchone()["c"] == 0

    def test_co_approves_clean_medium_without_compliance_memo(self):
        app_id, ref = self._seed_approvable("MEDIUM", with_memo=False)
        resp = self._approve(app_id, self.co_token, reason="Direct clean MEDIUM approval")
        assert resp.code in (200, 201), resp.body.decode()
        assert self._status_of(app_id)["status"] == "approved"
        assert self.db.execute(
            "SELECT COUNT(*) AS c FROM compliance_memos WHERE application_id=?", (app_id,),
        ).fetchone()["c"] == 0

    def test_senior_roles_approve_clean_low_medium_without_compliance_memo(self):
        for risk_level, token in (("LOW", self.sco_token), ("MEDIUM", self.admin_token)):
            app_id, _ref = self._seed_approvable(risk_level, with_memo=False)
            resp = self._approve(app_id, token, reason=f"Direct clean {risk_level} senior approval")
            assert resp.code in (200, 201), resp.body.decode()
            assert self._status_of(app_id)["status"] == "approved"

    def test_low_missing_documents_returns_document_blocker_not_memo(self):
        app_id, _ref = self._seed_approvable("LOW", with_memo=False, documents_ready=False)
        resp = self._approve(app_id, self.co_token)
        assert resp.code in (400, 409), resp.body.decode()
        error = self._json(resp)["error"].lower()
        assert "document" in error
        assert "compliance memo" not in error
        assert self._status_of(app_id)["status"] == "compliance_review"

    def test_low_incomplete_idv_returns_idv_blocker_not_memo(self):
        app_id, _ref = self._seed_approvable("LOW", with_memo=False, director_pep=False)
        self.db.execute(
            "INSERT INTO directors (id, application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?, ?)",
            (f"{app_id}_idv_dir", app_id, "Jane IDV", "Mauritius", "No"),
        )
        self.db.commit()
        resp = self._approve(app_id, self.co_token)
        assert resp.code == 400, resp.body.decode()
        error = self._json(resp)["error"].lower()
        assert "identity verification" in error
        assert "memo" not in error
        assert self._status_of(app_id)["status"] == "compliance_review"

    def test_low_stale_screening_returns_screening_blocker_not_memo(self):
        old = datetime.now(timezone.utc) - timedelta(days=120)
        prescreening = json.dumps({
            "screening_report": {
                "screening_mode": "live",
                "screened_at": old.strftime("%Y-%m-%dT%H:%M:%S"),
                "sanctions": {"api_status": "live", "matched": False, "results": []},
                "company_registry": {"api_status": "live"},
                "ip_geolocation": {"api_status": "live"},
                "kyc": {"api_status": "live", "matched": False, "results": []},
            },
            "screening_valid_until": (old + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S"),
            "screening_validity_days": 90,
        })
        app_id, _ref = self._seed_approvable("LOW", with_memo=False, prescreening_data=prescreening)
        resp = self._approve(app_id, self.co_token)
        assert resp.code == 400, resp.body.decode()
        error = self._json(resp)["error"].lower()
        assert "screening" in error and ("expired" in error or "re-screen" in error)
        assert "memo" not in error

    def test_low_unresolved_screening_returns_screening_blocker_not_memo(self):
        now = datetime.now(timezone.utc)
        prescreening = json.dumps({
            "screening_report": {
                "screening_mode": "live",
                "screened_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
                "sanctions": {"api_status": "live", "matched": True, "results": [{"name": "Potential Match"}]},
                "company_registry": {"api_status": "live"},
                "ip_geolocation": {"api_status": "live"},
                "kyc": {"api_status": "live", "matched": False, "results": []},
            },
            "screening_valid_until": (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S"),
            "screening_validity_days": 90,
        })
        app_id, _ref = self._seed_approvable("LOW", with_memo=False, prescreening_data=prescreening)
        resp = self._approve(app_id, self.co_token)
        assert resp.code in (400, 403), resp.body.decode()
        error = self._json(resp)["error"].lower()
        assert "screening" in error or "compliance" in error
        assert "memo" not in error

    def test_low_material_screening_concern_cannot_be_direct_approved_by_co(self):
        now = datetime.now(timezone.utc)
        prescreening = json.dumps({
            "screening_report": {
                "screening_mode": "live",
                "screened_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
                "sanctions": {"api_status": "live", "matched": True, "results": [{"name": "Material Match"}]},
                "company_registry": {"api_status": "live"},
                "ip_geolocation": {"api_status": "live"},
                "kyc": {"api_status": "live", "matched": False, "results": []},
            },
            "screening_valid_until": (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S"),
            "screening_validity_days": 90,
            "screening_concern": "material_screening_concern",
        })
        app_id, _ref = self._seed_approvable("LOW", with_memo=False, prescreening_data=prescreening)
        resp = self._approve(app_id, self.co_token)
        assert resp.code == 403, resp.body.decode()
        assert "compliance" in self._json(resp)["error"].lower()
        assert self._status_of(app_id)["status"] == "compliance_review"

    def test_pep_case_cannot_be_direct_approved_by_co(self):
        app_id, _ref = self._seed_approvable("MEDIUM", with_memo=False, director_pep=True)
        resp = self._approve(app_id, self.co_token)
        assert resp.code == 403, resp.body.decode()
        assert "compliance" in self._json(resp)["error"].lower()
        assert self._status_of(app_id)["status"] == "compliance_review"

    def test_high_without_memo_still_requires_compliance_package_for_senior(self):
        app_id, _ref = self._seed_approvable("HIGH", with_memo=False)
        resp = self._approve(app_id, self.sco_token, reason="Senior high approval without memo")
        assert resp.code == 400, resp.body.decode()
        assert "memo" in self._json(resp)["error"].lower()
        assert self._status_of(app_id)["status"] == "compliance_review"

    def test_high_risk_requires_dual_approval_and_flags_privileged_admin(self):
        app_id, ref = self._seed_approvable("HIGH")
        # First senior approval → recorded, not yet approved.
        first = self._approve(app_id, self.sco_token, reason="First senior approval")
        assert first.code == 202, first.body.decode()
        assert self._json(first)["status"] == "first_approval_recorded"
        assert self._status_of(app_id)["status"] != "approved"
        # A distinct admin completes the second approval → approved + privileged flag.
        second = self._approve(app_id, self.admin_token, reason="Second senior approval")
        assert second.code in (200, 201), second.body.decode()
        assert self._status_of(app_id)["status"] == "approved"
        # The completing admin HIGH approval is flagged privileged + extra-audited.
        notes = self._decision_notes(app_id)
        assert notes.get("is_privileged_admin_action") is True
        # A Decision audit row exists for the completing (second) approval.
        assert self._audit(ref, "Decision"), "expected a Decision audit row"

    # ── CO ON HIGH: blocked from approve, but submit is the forward action ──

    def test_co_high_blocked_but_can_submit(self):
        app_id, ref = self._seed_approvable("HIGH", status="in_review")
        blocked = self._approve(app_id, self.co_token)
        assert blocked.code == 403, blocked.body.decode()
        assert "Onboarding Officers cannot approve HIGH" in self._json(blocked)["error"]
        # No decision was written.
        assert self._status_of(app_id)["status"] == "in_review"
        # Submit to compliance succeeds (mandatory; basis includes high_risk).
        submit = self.fetch(
            f"/api/applications/{app_id}/submit-to-compliance", method="POST", headers=self._h(self.co_token),
            body=json.dumps({"submission_note": "Escalating high-risk file for senior approval"}),
        )
        assert submit.code == 200, submit.body.decode()
        body = self._json(submit)
        assert body["submission_kind"] == "mandatory"
        assert "high_risk" in body["submission_basis"]
        row = self._status_of(app_id)
        assert row["status"] == "submitted_to_compliance"
        assert not row["decided_at"]  # still no decision

    # ── BYPASS LOCK ──

    def test_patch_terminal_bypass_stays_closed(self):
        app_id, ref = self._seed_approvable("LOW")
        resp = self.fetch(
            f"/api/applications/{app_id}", method="PATCH", headers=self._h(self.admin_token),
            body=json.dumps({"status": "approved"}),
        )
        assert resp.code == 409, resp.body.decode()
        assert "Terminal decision blocked" in self._json(resp)["error"]
        assert self._status_of(app_id)["status"] == "compliance_review"
        # The blocked attempt is audited.
        gov = [r for r in self._audit(ref, "Governance Attempt")
               if "application.decision_blocked" in (r["detail"] or "")]
        assert gov, "expected application.decision_blocked audit row"

    def test_analyst_cannot_decide_and_is_audited(self):
        app_id, ref = self._seed_approvable("LOW")
        resp = self._approve(app_id, self.analyst_token)
        assert resp.code == 403, resp.body.decode()
        assert self._status_of(app_id)["status"] == "compliance_review"
        # Scope the assertion to THIS app's decision endpoint, not any analyst denial.
        decision_path = f"/api/applications/{app_id}/decision"
        denials = self.db.execute(
            "SELECT detail FROM audit_log WHERE action='authz_denied_role' AND user_role='analyst' AND target=?",
            (decision_path,),
        ).fetchall()
        assert len(denials) == 1, "expected one analyst authz_denied_role row for this app's decision endpoint"
        detail = json.loads(denials[0]["detail"])
        assert detail["path"] == decision_path
        assert detail["method"] == "POST"
        assert detail["source"] == "require_auth"

    # ── AUDIT LIFECYCLE RECONSTRUCTION ──

    def test_authority_lifecycle_is_reconstructable(self):
        # CO submits to compliance, then SCO approves. The full chain (who/role/event)
        # must be reconstructable from audit_log.
        app_id, ref = self._seed_approvable("HIGH", status="in_review")
        submit = self.fetch(
            f"/api/applications/{app_id}/submit-to-compliance", method="POST", headers=self._h(self.co_token),
            body=json.dumps({"submission_note": "Officer escalation to senior compliance review"}),
        )
        assert submit.code == 200, submit.body.decode()
        # SCO begins dual approval from the submitted-to-compliance state.
        first = self._approve(app_id, self.sco_token, reason="Senior review first approval")
        assert first.code == 202, first.body.decode()
        second = self._approve(app_id, self.sco2_token, reason="Senior review second approval")
        assert second.code in (200, 201), second.body.decode()
        assert self._status_of(app_id)["status"] == "approved"

        # Reconstruct: submitter (CO) + first senior approver + final decision are all
        # present with the right roles. Use a list (multiple rows can share an action).
        rows = [dict(r) for r in self.db.execute(
            "SELECT action, user_role FROM audit_log WHERE target=? ORDER BY id", (ref,),
        ).fetchall()]
        assert any(r["action"] == "Submit to Compliance" and r["user_role"] == "co" for r in rows)
        assert any(r["action"] == "First Approval (Pending Second)" and r["user_role"] == "sco" for r in rows)
        assert any(r["action"] == "Decision" and r["user_role"] == "sco" for r in rows)
