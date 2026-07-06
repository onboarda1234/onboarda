import json
import os
import sys
import tempfile
import uuid

from tornado.testing import AsyncHTTPTestCase


def _sync_test_db_path(path):
    os.environ["DB_PATH"] = path
    for module_name in ("config", "db", "server"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "DB_PATH"):
            setattr(module, "DB_PATH", path)
        if module_name == "server" and module is not None and hasattr(module, "_CFG_DB_PATH"):
            setattr(module, "_CFG_DB_PATH", path)


class PR1ClientApiBoundaryTest(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_pr1_boundary_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
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
        self.owner_client_id = "pr1_client_owner"
        self.other_client_id = "pr1_client_other"
        self.owner_app_id = "pr1_owner_app"
        self.owner_ref = "PR1-OWNER"
        self.other_app_id = "pr1_other_app"
        self.other_ref = "PR1-OTHER"

        for client_id, company in (
            (self.owner_client_id, "PR1 Owner Ltd"),
            (self.other_client_id, "PR1 Other Ltd"),
        ):
            self.db.execute(
                """
                INSERT OR REPLACE INTO clients
                    (id, email, password_hash, company_name, status)
                VALUES (?, ?, ?, ?, 'active')
                """,
                (client_id, f"{client_id}@example.test", "test-only", company),
            )

        for user_id, role, name in (
            ("admin001", "admin", "Test Admin"),
            ("sco001", "sco", "Test SCO"),
            ("co001", "co", "Test CO"),
            ("analyst001", "analyst", "Test Analyst"),
        ):
            self.db.execute(
                """
                INSERT OR REPLACE INTO users
                    (id, email, password_hash, full_name, role, status)
                VALUES (?, ?, ?, ?, ?, 'active')
                """,
                (user_id, f"{user_id}@example.test", "test-only", name, role),
            )

        prescreening = {
            "company_name": "PR1 Owner Ltd",
            "screening_report": {
                "provider": "ComplyAdvantage",
                "total_hits": 3,
                "raw_provider_response": {"secret": "provider-internal"},
            },
            "sumsub_applicant_ids": {"director-1": "sumsub-applicant-123"},
            "pricing": {
                "tier": "standard",
                "risk_score": 88,
                "risk_dimensions": {"jurisdiction": "HIGH"},
                "amount": 1000,
            },
        }
        self.db.execute(
            """
            INSERT OR REPLACE INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type,
                 status, prescreening_data, risk_score, risk_level,
                 risk_dimensions, onboarding_lane, assigned_to, decision_by,
                 decision_notes, screening_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.owner_app_id,
                self.owner_ref,
                self.owner_client_id,
                "PR1 Owner Ltd",
                "Mauritius",
                "Fintech",
                "Company",
                "kyc_submitted",
                json.dumps(prescreening),
                88,
                "HIGH",
                json.dumps({"jurisdiction": "HIGH", "sector": "MEDIUM"}),
                "edd",
                "co001",
                "sco001",
                "Internal decision rationale",
                "live",
            ),
        )
        self.db.execute(
            """
            INSERT OR REPLACE INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type,
                 status, risk_score, risk_level)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.other_app_id,
                self.other_ref,
                self.other_client_id,
                "PR1 Other Ltd",
                "Mauritius",
                "Technology",
                "Company",
                "submitted",
                25,
                "LOW",
            ),
        )
        self.db.execute(
            """
            INSERT OR REPLACE INTO documents
                (id, application_id, doc_type, doc_name, file_path, file_size,
                 verification_status, verification_results, review_status,
                 review_comment, reviewed_by, reviewed_at, evidence_class,
                 evidence_classification_note, evidence_classified_by,
                 evidence_classified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pr1_doc_owner",
                self.owner_app_id,
                "passport",
                "passport.pdf",
                "/tmp/passport.pdf",
                1234,
                "verified",
                json.dumps({"provider": "internal-ai", "raw": {"score": 0.97}}),
                "accepted",
                "Officer-only review comment",
                "co001",
                "2026-06-13T06:00:00Z",
                "approval_proof",
                "Officer-only evidence note",
                "sco001",
                "2026-06-13T06:05:00Z",
            ),
        )
        self.db.commit()

        self.client_token = create_token(
            self.owner_client_id, "client", "PR1 Owner Ltd", "client"
        )
        self.other_client_token = create_token(
            self.other_client_id, "client", "PR1 Other Ltd", "client"
        )
        self.admin_token = create_token("admin001", "admin", "Test Admin", "officer")
        self.sco_token = create_token("sco001", "sco", "Test SCO", "officer")
        self.co_token = create_token("co001", "co", "Test CO", "officer")
        self.analyst_token = create_token(
            "analyst001", "analyst", "Test Analyst", "officer"
        )

    def tearDown(self):
        self.db.close()
        super().tearDown()

    def _auth_headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def _json(self, response):
        return json.loads(response.body.decode() or "{}")

    def test_client_token_is_denied_internal_application_list_and_screening_surfaces(self):
        cases = (
            "/api/applications",
            "/api/applications?view=list&limit=1",
            "/api/screening/queue",
            "/api/screening/status",
            f"/api/applications/{self.owner_app_id}/memo/validation",
            f"/api/applications/{self.owner_app_id}/memo/supervisor",
            f"/api/applications/{self.owner_app_id}/audit-log",
            f"/api/applications/{self.owner_app_id}/evidence-pack",
            f"/api/applications/{self.owner_app_id}/kyc/identity-verifications",
        )
        for path in cases:
            response = self.fetch(path, headers=self._auth_headers(self.client_token))
            assert response.code == 403, f"{path} returned {response.code}: {response.body!r}"
            body = self._json(response)
            assert body.get("error") in {"Insufficient permissions", "Unauthorized"}
            body_text = response.body.decode()
            assert self.client_token not in body_text
            assert "provider-internal" not in body_text
            assert "Internal decision rationale" not in body_text

        audit = self.db.execute(
            """
            SELECT detail FROM audit_log
            WHERE user_id = ? AND action = 'authz_denied_internal_api'
            ORDER BY id DESC LIMIT 1
            """,
            (self.owner_client_id,),
        ).fetchone()
        assert audit is not None
        audit_detail = json.loads(audit["detail"])
        assert audit_detail["actor_type"] == "client"
        assert "token" not in json.dumps(audit_detail).lower()
        assert "cookie" not in json.dumps(audit_detail).lower()

    def test_client_owned_detail_is_portal_safe_and_excludes_internal_fields(self):
        response = self.fetch(
            f"/api/applications/{self.owner_app_id}",
            headers=self._auth_headers(self.client_token),
        )
        assert response.code == 200, response.body.decode()
        body = self._json(response)

        forbidden_top_level = {
            "assigned_to",
            "assigned_name",
            "decision_by",
            "decision_by_name",
            "decision_notes",
            "final_risk_level",
            "gate_blocker_count",
            "gate_blockers",
            "idv_gate_summary",
            "latest_memo",
            "latest_memo_data",
            "memo_is_stale",
            "monitoring_alerts",
            "officer_corrections",
            "onboarding_lane",
            "risk_dimensions",
            "risk_level",
            "risk_score",
            "screening_mode",
            "screening_reviews",
            "screening_truth_summary",
            "sumsub_idv_statuses",
            # PR-18: internal compliance-handoff columns now hidden by the
            # top-level allow-list (previously leaked through the denylist).
            "submitted_to_compliance_at",
            "submitted_to_compliance_by",
            "submission_blocker_snapshot",
            "submission_basis",
            "submission_kind",
            "submission_note",
            "is_fixture",
            "screening_adverse_truth_summary",
        }
        assert forbidden_top_level.isdisjoint(body.keys())

        prescreening = body["prescreening_data"]
        assert "company_name" in prescreening
        assert "screening_report" not in prescreening
        assert "sumsub_applicant_ids" not in prescreening
        assert "risk_score" not in prescreening["pricing"]
        assert prescreening["pricing"]["amount"] == 1000

        assert body["documents"], "Owned client detail should still show uploaded documents"
        document = body["documents"][0]
        forbidden_document_fields = {
            "application_id",
            "evidence_class",
            "evidence_classification_note",
            "evidence_classified_by",
            "evidence_classified_at",
            "review_comment",
            "review_status",
            "reviewed_at",
            "reviewed_by",
            "reviewed_by_name",
            "verification_results",
            # PR-18: internal storage locators now hidden from the nested
            # document projection.
            "file_path",
            "s3_key",
            "file_sha256",
            "replaced_by_user_id",
            "superseded_by_document_id",
        }
        assert forbidden_document_fields.isdisjoint(document.keys())
        assert document["verification_status"] == "verified"

    def test_client_cannot_access_another_clients_application_by_id_or_ref(self):
        for identifier in (self.owner_app_id, self.owner_ref):
            response = self.fetch(
                f"/api/applications/{identifier}",
                headers=self._auth_headers(self.other_client_token),
            )
            assert response.code == 403, response.body.decode()
            body_text = response.body.decode()
            assert self.owner_app_id not in body_text
            assert self.owner_ref not in body_text
            assert "Internal decision rationale" not in body_text

    def test_client_portal_list_returns_only_own_safe_projection(self):
        response = self.fetch(
            "/api/portal/applications",
            headers=self._auth_headers(self.client_token),
        )
        assert response.code == 200, response.body.decode()
        body = self._json(response)
        refs = {item["ref"] for item in body["applications"]}
        assert self.owner_ref in refs
        assert self.other_ref not in refs
        for item in body["applications"]:
            assert "risk_score" not in item
            assert "assigned_to" not in item
            assert "decision_notes" not in item

    def test_backoffice_roles_keep_internal_application_and_screening_access(self):
        for token in (self.admin_token, self.sco_token, self.co_token, self.analyst_token):
            app_response = self.fetch(
                "/api/applications?view=list&limit=5",
                headers=self._auth_headers(token),
            )
            assert app_response.code == 200, app_response.body.decode()
            app_body = self._json(app_response)
            assert "applications" in app_body
            assert any("risk_score" in app for app in app_body["applications"])

            queue_response = self.fetch(
                "/api/screening/queue",
                headers=self._auth_headers(token),
            )
            assert queue_response.code == 200, queue_response.body.decode()
            queue_body = self._json(queue_response)
            assert "rows" in queue_body
            assert "metrics" in queue_body

            status_response = self.fetch(
                "/api/screening/status",
                headers=self._auth_headers(token),
            )
            assert status_response.code == 200, status_response.body.decode()
            status_body = self._json(status_response)
            assert status_body["provider_truth"]["active_aml_screening_provider"]
