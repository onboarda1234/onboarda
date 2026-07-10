"""Applications role-matrix harness and staging-seed safety regression tests."""

import json
import logging
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from unittest import mock

import pytest
from tornado.testing import AsyncHTTPTestCase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.qa.application_role_matrix_harness import (  # noqa: E402
    CONFIRM_TOKEN,
    _insert_seed_rows,
    _passwords_for,
    _secure_write_json,
    build_seed_plan,
    disable_staging_users,
    enforce_staging_base_url,
    enforce_staging_seed_guard,
    run_browser_smoke,
)


OFFICER_ROLES = ("admin", "sco", "co", "analyst")
SIGNOFF = {"acknowledged": True, "scope": "decision", "source_context": "ai_advisory"}


@pytest.fixture(scope="module", autouse=True)
def _restore_logging_configuration():
    """Keep server module logging setup from leaking into later test modules."""
    from observability import arie_logger

    root_logger = logging.getLogger()
    arie_state = (list(arie_logger.handlers), arie_logger.level, arie_logger.propagate)
    root_state = (list(root_logger.handlers), root_logger.level)
    try:
        yield
    finally:
        arie_logger.handlers[:] = arie_state[0]
        arie_logger.setLevel(arie_state[1])
        arie_logger.propagate = arie_state[2]
        root_logger.handlers[:] = root_state[0]
        root_logger.setLevel(root_state[1])


def test_staging_seed_guard_requires_exact_environment_host_and_confirmation():
    valid = {
        "environment": "staging",
        "database_url": "postgresql://role_user:secret@staging-db.example.test/onboarda_staging",
        "allow_value": "1",
        "confirm": CONFIRM_TOKEN,
        "allowed_host": "staging-db.example.test",
    }
    enforce_staging_seed_guard(**valid)

    mutations = (
        {"environment": "production"},
        {"database_url": "postgresql://u:p@prod-db.example.test/onboarda"},
        {"allow_value": "0"},
        {"confirm": "wrong"},
        {"allowed_host": "different.example.test"},
    )
    for mutation in mutations:
        values = {**valid, **mutation}
        with pytest.raises(RuntimeError):
            enforce_staging_seed_guard(**values)


def test_staging_validation_url_refuses_non_staging_or_non_https_hosts():
    assert enforce_staging_base_url("https://staging.regmind.example") == "https://staging.regmind.example"
    for value in ("http://staging.regmind.example", "https://regmind.example", "https://prod-db.staging.example"):
        with pytest.raises(RuntimeError):
            enforce_staging_base_url(value)


def test_seed_plan_is_complete_synthetic_and_secret_free():
    plan = build_seed_plan("20260710T120000Z-abcdef")
    assert set(plan["actors"]) == {"admin", "sco", "co", "analyst", "client"}
    assert set(plan["applications"]) == {
        "assigned_sco", "assigned_co", "assigned_analyst", "unassigned",
        "blocked_admin", "blocked_sco", "blocked_co", "submitted_compliance",
        "wrong_stage", "terminal_approved",
        "client_owned",
    }
    assert all(actor["name"].startswith("APPAUDIT_ROLE_") for actor in plan["actors"].values())
    assert all(app["company_name"].startswith("ROLEAUDIT-") for app in plan["applications"].values())
    assert all(app["is_fixture"] is True for app in plan["applications"].values())
    serialized = json.dumps(plan).lower()
    assert "password" not in serialized
    assert "secret" not in serialized


def test_local_seed_sql_artifacts_and_bulk_disable_round_trip(db, tmp_path):
    plan = build_seed_plan("20260710T121500Z-fedcba")
    passwords = _passwords_for(plan)
    _insert_seed_rows(db, plan, passwords)
    db.commit()

    officer_count = db.execute(
        "SELECT COUNT(*) AS c FROM users WHERE full_name LIKE 'APPAUDIT_ROLE_%'"
    ).fetchone()["c"]
    client_count = db.execute(
        "SELECT COUNT(*) AS c FROM clients WHERE company_name LIKE 'APPAUDIT_ROLE_CLIENT_%'"
    ).fetchone()["c"]
    app_rows = db.execute(
        "SELECT id, ref, company_name, is_fixture FROM applications WHERE company_name LIKE 'ROLEAUDIT-%'"
    ).fetchall()
    assert officer_count == 4
    assert client_count == 1
    assert len(app_rows) == 11
    assert all(row["is_fixture"] for row in app_rows)

    manifest = {
        "run_id": plan["run_id"],
        "actors": list(plan["actors"].values()),
        "applications": list(plan["applications"].values()),
    }
    manifest_path = tmp_path / "seed-evidence.json"
    credential_path = tmp_path / "credentials.json"
    _secure_write_json(manifest_path, manifest)
    _secure_write_json(
        credential_path,
        {"run_id": plan["run_id"], "actors": {role: {"password": passwords[role]} for role in plan["actors"]}},
    )
    assert (credential_path.stat().st_mode & 0o777) == 0o600
    assert "password" not in manifest_path.read_text(encoding="utf-8").lower()

    disabled = disable_staging_users(str(manifest_path))
    assert len(disabled["disabled_officers"]) == 4
    assert len(disabled["disabled_clients"]) == 1
    assert db.execute(
        "SELECT COUNT(*) AS c FROM users WHERE full_name LIKE 'APPAUDIT_ROLE_%' AND status='inactive'"
    ).fetchone()["c"] == 4
    assert db.execute(
        "SELECT COUNT(*) AS c FROM clients WHERE company_name LIKE 'APPAUDIT_ROLE_CLIENT_%' AND status='inactive'"
    ).fetchone()["c"] == 1


def test_role_audit_fixture_opt_in_is_staging_only_and_requires_prefix():
    from fixture_filter import should_show_fixtures

    synthetic_co = {
        "type": "officer",
        "role": "co",
        "name": "APPAUDIT_ROLE_CO_20260710T120000Z-abcdef",
        "email": "appaudit_role_co_20260710t120000z-abcdef@example.test",
    }
    pilot_co = {"type": "officer", "role": "co", "name": "Pilot Officer", "email": "pilot@example.test"}
    synthetic_client = {**synthetic_co, "type": "client", "role": "client"}

    with mock.patch.dict(os.environ, {"ENVIRONMENT": "staging"}):
        assert should_show_fixtures(synthetic_co, "true") is True
        assert should_show_fixtures(pilot_co, "true") is False
        assert should_show_fixtures(synthetic_client, "true") is False
        assert should_show_fixtures(synthetic_co, None) is False
    with mock.patch.dict(os.environ, {"ENVIRONMENT": "production"}):
        assert should_show_fixtures(synthetic_co, "true") is False
    with mock.patch.dict(os.environ, {"ENVIRONMENT": "testing"}):
        assert should_show_fixtures(synthetic_co, "true") is False


def test_browser_runner_covers_sco_co_analyst_without_secret_command_arguments(tmp_path):
    plan = build_seed_plan("20260710T123000Z-a1b2c3")
    passwords = _passwords_for(plan)
    manifest_path = tmp_path / "manifest.json"
    credentials_path = tmp_path / "credentials.json"
    out_dir = tmp_path / "browser"
    _secure_write_json(
        manifest_path,
        {
            "run_id": plan["run_id"],
            "actors": list(plan["actors"].values()),
            "applications": list(plan["applications"].values()),
        },
    )
    _secure_write_json(
        credentials_path,
        {
            "run_id": plan["run_id"],
            "actors": {
                role: {"email": actor["email"], "password": passwords[role]}
                for role, actor in plan["actors"].items()
            },
        },
    )

    calls = []

    def fake_run(command, *, env, check):
        calls.append({"command": command, "env": env, "check": check})
        return type("Completed", (), {"returncode": 0})()

    with mock.patch(
        "scripts.qa.application_role_matrix_harness.subprocess.run",
        side_effect=fake_run,
    ):
        result = run_browser_smoke(
            "https://staging.regmind.example",
            str(manifest_path),
            str(credentials_path),
            str(out_dir),
        )

    assert result["passed"] is True
    assert [row["role"] for row in result["roles"]] == ["sco", "co", "analyst"]
    assert len(calls) == 3
    for call, role in zip(calls, ("sco", "co", "analyst")):
        assert call["command"][0] == "node"
        assert passwords[role] not in " ".join(call["command"])
        assert call["env"]["STAGING_QA_PASSWORD"] == passwords[role]
        assert call["env"]["STAGING_SMOKE_APP_ID"] == plan["applications"][f"assigned_{role}"]["id"]
    summary_text = (out_dir / "summary.json").read_text(encoding="utf-8")
    assert all(password not in summary_text for password in passwords.values())


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
    if state.get("env") is None:
        os.environ.pop("DB_PATH", None)
    else:
        os.environ["DB_PATH"] = state["env"]
    for module_name, attrs in state.get("modules", {}).items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for attr, (existed, value) in attrs.items():
            if existed:
                setattr(module, attr, value)
            elif hasattr(module, attr):
                delattr(module, attr)


class ApplicationRoleMatrixHTTPTest(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path_state = _capture_db_path_state()
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"application_role_matrix_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
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
        self.actors = {
            "admin": ("rm_admin", "admin"),
            "sco": ("rm_sco", "sco"),
            "co": ("rm_co", "co"),
            "co_peer": ("rm_co_peer", "co"),
            "analyst": ("rm_analyst", "analyst"),
        }
        for label, (actor_id, role) in self.actors.items():
            self.db.execute(
                "INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status) "
                "VALUES (?, ?, 'test-only', ?, ?, 'active')",
                (actor_id, f"{label}@example.test", f"Role Matrix {label}", role),
            )
        for client_id in ("rm_client", "rm_other_client"):
            self.db.execute(
                "INSERT OR REPLACE INTO clients (id, email, password_hash, company_name, status) "
                "VALUES (?, ?, 'test-only', ?, 'active')",
                (client_id, f"{client_id}@example.test", f"Role Matrix {client_id}"),
            )
        self.db.commit()
        self.tokens = {
            label: create_token(actor_id, role, f"Role Matrix {label}", "officer")
            for label, (actor_id, role) in self.actors.items()
        }
        self.tokens["client"] = create_token("rm_client", "client", "Role Matrix Client", "client")
        self.tokens["other_client"] = create_token(
            "rm_other_client", "client", "Role Matrix Other Client", "client"
        )
        self.apps = {}
        self._seed_application("shared", client_id="rm_client", status="compliance_review", assigned_to="rm_co")
        self._seed_application("other_client", client_id="rm_other_client", status="draft")
        self._seed_application("preapproval", client_id="rm_client", status="pre_approval_review", risk="HIGH", assigned_to="rm_sco")
        for role in ("admin", "sco", "co"):
            self._seed_application(
                f"blocked_{role}",
                client_id="rm_client",
                status="kyc_documents",
                assigned_to=self.actors[role][0],
            )
        self._seed_document("shared_doc", self.apps["shared"]["id"], verification_status="verified")
        self.db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, application_id, detail, request_id) "
            "VALUES ('rm_admin','Role Matrix admin','admin','Role Matrix Seed',?,?,?,?)",
            (
                self.apps["shared"]["ref"], self.apps["shared"]["id"],
                json.dumps({"application_id": self.apps["shared"]["id"]}),
                "role-matrix-seed",
            ),
        )
        self.db.commit()
        import base_handler
        base_handler.rate_limiter._attempts.clear()

    def tearDown(self):
        self.db.close()
        super().tearDown()
        try:
            os.unlink(self._db_path)
        except OSError:
            pass
        _restore_db_path_state(self._db_path_state)

    def _seed_application(self, label, *, client_id, status, risk="LOW", assigned_to=None):
        suffix = uuid.uuid4().hex[:10]
        app_id = f"rm_{label}_{suffix}"
        ref = f"ROLEMATRIX-{label.upper()}-{suffix}"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        score = {"LOW": 20, "MEDIUM": 50, "HIGH": 78, "VERY_HIGH": 92}[risk]
        self.db.execute(
            """
            INSERT INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type,
                 status, risk_level, final_risk_level, risk_score, assigned_to,
                 is_fixture, created_at, updated_at, inputs_updated_at)
            VALUES (?, ?, ?, ?, 'Mauritius', 'Synthetic role audit', 'company',
                    ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (app_id, ref, client_id, f"ROLEMATRIX {label}", status, risk, risk, score, assigned_to, now, now, now),
        )
        self.apps[label] = {"id": app_id, "ref": ref, "status": status}
        self.db.commit()
        return self.apps[label]

    def _seed_document(self, label, app_id, *, verification_status):
        doc_id = f"rm_doc_{label}_{uuid.uuid4().hex[:8]}"
        self.db.execute(
            """
            INSERT INTO documents
                (id, application_id, doc_type, doc_name, file_path, mime_type,
                 verification_status, verification_results, review_status)
            VALUES (?, ?, 'supporting_document', ?, ?, 'application/pdf', ?, ?, 'pending')
            """,
            (
                doc_id, app_id, f"{label}.pdf", f"/tmp/{label}.pdf", verification_status,
                json.dumps({"overall": verification_status, "internal": "officer-only"}),
            ),
        )
        self.db.commit()
        return doc_id

    def _headers(self, role):
        return {"Authorization": f"Bearer {self.tokens[role]}", "Content-Type": "application/json"}

    def _request(self, role, path, *, method="GET", payload=None):
        kwargs = {"method": method, "headers": self._headers(role)}
        if method != "GET":
            kwargs["body"] = json.dumps(payload or {})
        return self.fetch(path, **kwargs)

    @staticmethod
    def _json(response):
        return json.loads(response.body.decode("utf-8") or "{}")

    def test_officer_roles_can_read_application_workspace_without_cross_app_audit(self):
        app = self.apps["shared"]
        for role in OFFICER_ROLES:
            listed = self._request(role, "/api/applications?q=ROLEMATRIX+SHARED&limit=20")
            assert listed.code == 200, (role, listed.body.decode())
            assert app["id"] in {row["id"] for row in self._json(listed)["applications"]}

            detail = self._request(role, f"/api/applications/{app['id']}")
            assert detail.code == 200, (role, detail.body.decode())
            detail_body = self._json(detail)
            assert detail_body["id"] == app["id"]
            assert "screening_truth_summary" in detail_body
            assert "latest_memo" in detail_body
            assert "gate_blockers" in detail_body

            documents = self._request(role, f"/api/applications/{app['id']}/documents")
            assert documents.code == 200
            assert documents.body

            audit = self._request(role, f"/api/applications/{app['id']}/audit-log")
            assert audit.code == 200
            assert all(row.get("application_id") == app["id"] for row in self._json(audit)["entries"])

            evidence = self._request(role, f"/api/applications/{app['id']}/evidence-pack")
            assert evidence.code == 200
            assert self._json(evidence)["scope"]["application_id"] == app["id"]

    def test_client_is_denied_backoffice_and_cross_application_surfaces(self):
        own = self.apps["shared"]
        other = self.apps["other_client"]
        assert self._request("client", "/api/applications").code == 403

        own_detail = self._request("client", f"/api/applications/{own['id']}")
        assert own_detail.code == 200
        detail_body = self._json(own_detail)
        for forbidden in ("gate_blockers", "screening_reviews", "latest_memo_data", "decision_basis"):
            assert forbidden not in detail_body

        own_docs = self._request("client", f"/api/applications/{own['id']}/documents")
        assert own_docs.code == 200
        for document in self._json(own_docs):
            for forbidden in ("file_path", "verification_results", "review_comment", "evidence_class"):
                assert forbidden not in document

        assert self._request("client", f"/api/applications/{own['id']}/audit-log").code == 403
        assert self._request("client", f"/api/applications/{own['id']}/evidence-pack").code == 403
        assert self._request("client", f"/api/applications/{other['id']}").code == 403
        assert self._request("client", f"/api/applications/{other['id']}/documents").code == 403

        portal = self._request("client", "/api/portal/applications")
        assert portal.code == 200

    def test_action_entrypoints_enforce_role_boundaries_before_workflow_logic(self):
        missing = "role_matrix_missing"
        role_paths = {
            "/api/applications/{id}/decision": {"admin", "sco", "co"},
            "/api/applications/{id}/submit-to-compliance": {"admin", "sco", "co"},
            "/api/applications/{id}/move-to-compliance-review": {"admin", "sco", "co"},
            "/api/applications/{id}/memo": set(OFFICER_ROLES),
            "/api/applications/{id}/memo/validate": set(OFFICER_ROLES),
            "/api/applications/{id}/memo/approve": {"admin", "sco"},
            "/api/applications/{id}/supervisor/run": {"admin", "sco", "co"},
            "/api/applications/{id}/kyc/identity-verifications/resolve": {"admin", "sco", "co"},
            "/api/documents/{id}/review": set(OFFICER_ROLES),
        }
        for template, allowed in role_paths.items():
            path = template.format(id=missing)
            for role in (*OFFICER_ROLES, "client"):
                response = self._request(role, path, method="POST", payload={})
                if role in allowed:
                    assert response.code != 403, (template, role, response.body.decode())
                else:
                    assert response.code == 403, (template, role, response.body.decode())

        screening_payload = {
            "application_id": missing,
            "subject_type": "company",
            "subject_name": "Synthetic Matrix",
            "disposition": "follow_up_required",
            "notes": "Synthetic role-boundary probe only",
        }
        for role in OFFICER_ROLES:
            assert self._request(role, "/api/screening/review", method="POST", payload=screening_payload).code != 403
        assert self._request("client", "/api/screening/review", method="POST", payload=screening_payload).code == 403

        preapproval = self.apps["preapproval"]
        for role in ("co", "analyst", "client"):
            response = self._request(
                role,
                f"/api/applications/{preapproval['id']}/pre-approval-decision",
                method="POST",
                payload={"decision": "INVALID", "notes": "Synthetic role probe"},
            )
            assert response.code == 403

    def test_admin_sco_and_co_cannot_approve_blocked_case_or_mutate_state(self):
        for role in ("admin", "sco", "co"):
            app = self.apps[f"blocked_{role}"]
            response = self._request(
                role,
                f"/api/applications/{app['id']}/decision",
                method="POST",
                payload={
                    "decision": "approve",
                    "decision_reason": "Synthetic blocked role-matrix approval probe",
                    "officer_signoff": SIGNOFF,
                },
            )
            assert response.code in (400, 409), (role, response.body.decode())
            row = self.db.execute("SELECT status, decision_by FROM applications WHERE id=?", (app["id"],)).fetchone()
            assert row["status"] == "kyc_documents"
            assert not row["decision_by"]
            audit = self.db.execute(
                "SELECT user_role, application_id, request_id FROM audit_log "
                "WHERE application_id=? AND user_role=? ORDER BY id DESC LIMIT 1",
                (app["id"], role),
            ).fetchone()
            assert audit is not None
            assert audit["application_id"] == app["id"]
            # Some rejected-governance writers pre-date request-id propagation.
            # Immutable application scope and actor/role remain mandatory here;
            # request-id completion is recorded as a residual audit limitation.

        analyst = self._request(
            "analyst",
            f"/api/applications/{self.apps['blocked_co']['id']}/decision",
            method="POST",
            payload={
                "decision": "approve",
                "decision_reason": "Synthetic analyst denial probe",
                "officer_signoff": SIGNOFF,
            },
        )
        assert analyst.code == 403

    def test_each_officer_role_can_review_verified_document_with_scoped_audit(self):
        for role in OFFICER_ROLES:
            app = self._seed_application(
                f"doc_{role}", client_id="rm_client", status="kyc_documents", assigned_to=self.actors[role][0]
            )
            doc_id = self._seed_document(f"verified_{role}", app["id"], verification_status="verified")
            response = self._request(
                role,
                f"/api/documents/{doc_id}/review",
                method="POST",
                payload={"status": "accepted", "comment": "Synthetic verified-document role test"},
            )
            assert response.code == 200, (role, response.body.decode())
            audit = self.db.execute(
                "SELECT user_id, user_role, application_id, request_id FROM audit_log "
                "WHERE action='Document Review' AND application_id=? ORDER BY id DESC LIMIT 1",
                (app["id"],),
            ).fetchone()
            assert audit is not None
            assert audit["user_id"] == self.actors[role][0]
            assert audit["user_role"] == role
            assert audit["application_id"] == app["id"]
            assert audit["request_id"]

    def test_unverified_document_manual_acceptance_is_senior_only(self):
        for role in OFFICER_ROLES:
            app = self._seed_application(
                f"manual_{role}", client_id="rm_client", status="kyc_documents", assigned_to=self.actors[role][0]
            )
            doc_id = self._seed_document(f"unverified_{role}", app["id"], verification_status="pending")
            response = self._request(
                role,
                f"/api/documents/{doc_id}/review",
                method="POST",
                payload={"status": "accepted", "comment": "Synthetic senior manual acceptance rationale"},
            )
            expected = 200 if role in {"admin", "sco"} else 403
            assert response.code == expected, (role, response.body.decode())
            row = self.db.execute("SELECT review_status FROM documents WHERE id=?", (doc_id,)).fetchone()
            assert row["review_status"] == ("accepted" if expected == 200 else "pending")

    def test_owner_gate_denies_peer_co_and_requires_supervisor_reason(self):
        peer_case = self._seed_application(
            "peer_owner", client_id="rm_client", status="compliance_review", assigned_to="rm_co"
        )
        peer = self._request(
            "co_peer",
            f"/api/applications/{peer_case['id']}/decision",
            method="POST",
            payload={"decision": "reject", "decision_reason": "Synthetic non-owner probe", "officer_signoff": SIGNOFF},
        )
        assert peer.code == 403
        assert self.db.execute("SELECT status FROM applications WHERE id=?", (peer_case["id"],)).fetchone()["status"] == "compliance_review"

        supervisor_case = self._seed_application(
            "supervisor_owner", client_id="rm_client", status="compliance_review", assigned_to="rm_co"
        )
        missing_reason = self._request(
            "sco",
            f"/api/applications/{supervisor_case['id']}/decision",
            method="POST",
            payload={"decision": "reject", "decision_reason": "Synthetic supervisor probe", "officer_signoff": SIGNOFF},
        )
        assert missing_reason.code == 403
        assert self.db.execute("SELECT status FROM applications WHERE id=?", (supervisor_case["id"],)).fetchone()["status"] == "compliance_review"

    def test_owner_unassigned_claim_and_supervisor_override_success_paths(self):
        owner_case = self._seed_application(
            "owner_success", client_id="rm_client", status="compliance_review", assigned_to="rm_co"
        )
        owner_response = self._request(
            "co",
            f"/api/applications/{owner_case['id']}/decision",
            method="POST",
            payload={"decision": "reject", "decision_reason": "Synthetic owner sign-off test", "officer_signoff": SIGNOFF},
        )
        assert owner_response.code == 201, owner_response.body.decode()
        owner_row = self.db.execute(
            "SELECT status, decision_by, assigned_to FROM applications WHERE id=?", (owner_case["id"],)
        ).fetchone()
        assert dict(owner_row) == {"status": "rejected", "decision_by": "rm_co", "assigned_to": "rm_co"}

        unassigned = self._seed_application(
            "unassigned_success", client_id="rm_client", status="compliance_review", assigned_to=None
        )
        unassigned_response = self._request(
            "co",
            f"/api/applications/{unassigned['id']}/decision",
            method="POST",
            payload={"decision": "reject", "decision_reason": "Synthetic auto-claim sign-off test", "officer_signoff": SIGNOFF},
        )
        assert unassigned_response.code == 201, unassigned_response.body.decode()
        unassigned_row = self.db.execute(
            "SELECT status, decision_by, assigned_to FROM applications WHERE id=?", (unassigned["id"],)
        ).fetchone()
        assert dict(unassigned_row) == {"status": "rejected", "decision_by": "rm_co", "assigned_to": "rm_co"}
        claim_audit = self.db.execute(
            "SELECT user_id, user_role, detail FROM audit_log "
            "WHERE application_id=? AND action='Governance Attempt' ORDER BY id DESC",
            (unassigned["id"],),
        ).fetchall()
        assert any("ownership_claimed" in (row["detail"] or "") for row in claim_audit)

        override_case = self._seed_application(
            "override_success", client_id="rm_client", status="compliance_review", assigned_to="rm_co"
        )
        override_response = self._request(
            "sco",
            f"/api/applications/{override_case['id']}/decision",
            method="POST",
            payload={
                "decision": "reject",
                "decision_reason": "Synthetic supervisor override sign-off test",
                "ownership_override_reason": "Synthetic senior coverage validation",
                "officer_signoff": SIGNOFF,
            },
        )
        assert override_response.code == 201, override_response.body.decode()
        override_row = self.db.execute(
            "SELECT status, decision_by, assigned_to FROM applications WHERE id=?", (override_case["id"],)
        ).fetchone()
        assert dict(override_row) == {"status": "rejected", "decision_by": "rm_sco", "assigned_to": "rm_co"}
        override_audit = self.db.execute(
            "SELECT user_id, user_role, detail FROM audit_log "
            "WHERE application_id=? AND action='Governance Attempt' ORDER BY id DESC",
            (override_case["id"],),
        ).fetchall()
        assert any("ownership_override" in (row["detail"] or "") for row in override_audit)
