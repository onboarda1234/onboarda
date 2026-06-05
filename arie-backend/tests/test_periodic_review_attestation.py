import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone

from tornado.testing import AsyncHTTPTestCase


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from periodic_review_attestation import prepare_attestation_submission_update


class _PeriodicReviewAttestationBase(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_prs2_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)
        os.environ["DB_PATH"] = self._db_path

        import config as config_module
        import db as db_module

        self._orig_config_db_path = config_module.DB_PATH
        self._orig_db_db_path = db_module.DB_PATH
        config_module.DB_PATH = self._db_path
        db_module.DB_PATH = self._db_path

        db_module.init_db()
        conn = db_module.get_db()
        conn.execute(
            """
            INSERT INTO users (id, email, password_hash, full_name, role, status)
            VALUES
            ('admin001', 'admin@test.com', 'x', 'Admin User', 'admin', 'active'),
            ('co001', 'co@test.com', 'x', 'Compliance Officer', 'co', 'active')
            """
        )
        conn.execute(
            """
            INSERT INTO clients (id, email, password_hash, company_name, status)
            VALUES
            ('client001', 'client1@test.com', 'x', 'Portal Client One', 'active'),
            ('client002', 'client2@test.com', 'x', 'Portal Client Two', 'active')
            """
        )
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, status, risk_level, risk_score, created_at, updated_at)
            VALUES
            ('app-owned', 'ARF-PRS2-OWNED', 'client001', 'Owned Co Ltd', 'Mauritius', 'Fintech', 'approved', 'HIGH', 78, datetime('now'), datetime('now')),
            ('app-other', 'ARF-PRS2-OTHER', 'client002', 'Other Co Ltd', 'Mauritius', 'Fintech', 'approved', 'LOW', 20, datetime('now'), datetime('now'))
            """
        )
        conn.execute(
            """
            INSERT INTO periodic_reviews
            (application_id, client_name, risk_level, status, due_date, assigned_officer, created_at)
            VALUES
            ('app-owned', 'Owned Co Ltd', 'HIGH', 'awaiting_information', '2026-06-20', 'co001', datetime('now')),
            ('app-other', 'Other Co Ltd', 'LOW', 'pending', '2026-06-21', 'co001', datetime('now'))
            """
        )
        conn.commit()
        self._owned_review_id = conn.execute(
            "SELECT id FROM periodic_reviews WHERE application_id = 'app-owned'"
        ).fetchone()["id"]
        self._other_review_id = conn.execute(
            "SELECT id FROM periodic_reviews WHERE application_id = 'app-other'"
        ).fetchone()["id"]
        conn.close()

        from server import make_app
        import server as server_module

        self._server = server_module
        return make_app()

    def setUp(self):
        super().setUp()
        from db import get_db

        self._conn = get_db()
        self.client_token = self._server.create_token("client001", "client", "Portal Client One", "client")
        self.other_client_token = self._server.create_token("client002", "client", "Portal Client Two", "client")
        self.admin_token = self._server.create_token("admin001", "admin", "Admin User", "officer")

    def tearDown(self):
        try:
            self._conn.close()
        except Exception:
            pass
        try:
            os.unlink(self._db_path)
        except Exception:
            pass
        try:
            import config as config_module
            import db as db_module
            config_module.DB_PATH = self._orig_config_db_path
            db_module.DB_PATH = self._orig_db_db_path
        except Exception:
            pass
        super().tearDown()

    def _get(self, path, token):
        return self.fetch(
            path,
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
        )

    def _post(self, path, body, token):
        return self.fetch(
            path,
            method="POST",
            body=json.dumps(body),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )


class TestPeriodicReviewAttestationHandlers(_PeriodicReviewAttestationBase):
    def test_portal_list_returns_owned_periodic_review_task_without_risk_fields(self):
        resp = self._get("/api/portal/applications", self.client_token)
        assert resp.code == 200
        body = json.loads(resp.body)
        assert body["total"] == 1
        app = body["applications"][0]
        assert app["id"] == "app-owned"
        assert "risk_level" not in app
        assert "risk_score" not in app
        task = app["periodic_review_task"]
        assert task["review_reference"].startswith("PR-")
        assert task["task_status"] == "not_started"

    def test_client_can_fetch_owned_attestation_and_response_is_client_safe(self):
        resp = self._get("/api/portal/applications/app-owned/periodic-review", self.client_token)
        assert resp.code == 200
        body = json.loads(resp.body)
        assert body["application_id"] == "app-owned"
        assert body["attestation"]["status"] == "not_started"
        assert len(body["questions"]) == 8
        assert body["read_only"] is False
        assert "risk_level" not in body
        assert "risk_score" not in body

    def test_client_cannot_fetch_another_clients_review(self):
        resp = self._get("/api/portal/applications/app-other/periodic-review", self.client_token)
        assert resp.code == 403

    def test_client_can_save_draft_with_partial_answers_and_reload(self):
        draft_payload = {
            "answers": {
                "directors_changed": {"answer": "yes", "comment": "One director resigned."},
                "shareholders_changed": {"answer": "no"},
            },
            "declaration_accepted": False,
        }
        save_resp = self._post(
            "/api/portal/applications/app-owned/periodic-review/save-draft",
            draft_payload,
            self.client_token,
        )
        assert save_resp.code == 200
        save_body = json.loads(save_resp.body)
        assert save_body["attestation"]["status"] == "draft"
        assert save_body["attestation"]["questions"][0]["answer"] == "yes"
        assert save_body["attestation"]["questions"][0]["comment"] == "One director resigned."

        reload_resp = self._get("/api/portal/applications/app-owned/periodic-review", self.client_token)
        reload_body = json.loads(reload_resp.body)
        assert reload_body["attestation"]["status"] == "draft"
        assert reload_body["attestation"]["questions"][0]["answer"] == "yes"

        audit = self._conn.execute(
            "SELECT action, detail FROM audit_log WHERE action = 'periodic_review_attestation_draft_saved' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None
        detail = json.loads(audit["detail"])
        assert detail["periodic_review_id"] == self._owned_review_id
        assert "directors_changed" in detail["material_change_question_keys"]

    def test_submit_rejects_missing_answers_declaration_and_required_comments(self):
        missing_answers = self._post(
            "/api/portal/applications/app-owned/periodic-review/submit",
            {"answers": {"directors_changed": {"answer": "no"}}, "declaration_accepted": True},
            self.client_token,
        )
        assert missing_answers.code == 400

        missing_declaration = self._post(
            "/api/portal/applications/app-owned/periodic-review/submit",
            {
                "answers": {
                    "directors_changed": {"answer": "no"},
                    "shareholders_changed": {"answer": "no"},
                    "ubos_changed": {"answer": "no"},
                    "business_activity_changed": {"answer": "no"},
                    "jurisdictions_changed": {"answer": "no"},
                    "transaction_volume_changed": {"answer": "no"},
                    "licence_regulatory_status_changed": {"answer": "no"},
                    "company_contact_details_correct": {"answer": "yes"},
                },
                "declaration_accepted": False,
            },
            self.client_token,
        )
        assert missing_declaration.code == 400

        missing_comment = self._post(
            "/api/portal/applications/app-owned/periodic-review/submit",
            {
                "answers": {
                    "directors_changed": {"answer": "yes", "comment": ""},
                    "shareholders_changed": {"answer": "no"},
                    "ubos_changed": {"answer": "no"},
                    "business_activity_changed": {"answer": "no"},
                    "jurisdictions_changed": {"answer": "no"},
                    "transaction_volume_changed": {"answer": "no"},
                    "licence_regulatory_status_changed": {"answer": "no"},
                    "company_contact_details_correct": {"answer": "yes"},
                },
                "declaration_accepted": True,
            },
            self.client_token,
        )
        assert missing_comment.code == 400

    def test_submit_succeeds_then_client_view_becomes_read_only(self):
        submit_payload = {
            "answers": {
                "directors_changed": {"answer": "yes", "comment": "One director resigned."},
                "shareholders_changed": {"answer": "no", "comment": ""},
                "ubos_changed": {"answer": "no", "comment": ""},
                "business_activity_changed": {"answer": "no", "comment": ""},
                "jurisdictions_changed": {"answer": "no", "comment": ""},
                "transaction_volume_changed": {"answer": "no", "comment": ""},
                "licence_regulatory_status_changed": {"answer": "no", "comment": ""},
                "company_contact_details_correct": {"answer": "yes", "comment": ""},
            },
            "declaration_accepted": True,
        }
        resp = self._post(
            "/api/portal/applications/app-owned/periodic-review/submit",
            submit_payload,
            self.client_token,
        )
        assert resp.code == 200
        body = json.loads(resp.body)
        assert body["attestation"]["status"] == "submitted"
        assert body["read_only"] is True
        assert body["attestation"]["declaration_accepted"] is True
        assert body["attestation"]["submitted_at"]

        second_save = self._post(
            "/api/portal/applications/app-owned/periodic-review/save-draft",
            submit_payload,
            self.client_token,
        )
        assert second_save.code == 409

        audit = self._conn.execute(
            "SELECT action, detail FROM audit_log WHERE action = 'periodic_review_attestation_submitted' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None
        detail = json.loads(audit["detail"])
        assert detail["periodic_review_id"] == self._owned_review_id
        assert detail["declaration_accepted"] is True
        assert "directors_changed" in detail["material_change_question_keys"]
        assert isinstance(body["attestation"]["saved_at"], str)
        assert isinstance(body["attestation"]["submitted_at"], str)

    def test_backoffice_review_detail_includes_read_only_attestation_summary_and_risk(self):
        submit_payload = {
            "answers": {
                "directors_changed": {"answer": "no", "comment": ""},
                "shareholders_changed": {"answer": "no", "comment": ""},
                "ubos_changed": {"answer": "no", "comment": ""},
                "business_activity_changed": {"answer": "no", "comment": ""},
                "jurisdictions_changed": {"answer": "no", "comment": ""},
                "transaction_volume_changed": {"answer": "no", "comment": ""},
                "licence_regulatory_status_changed": {"answer": "no", "comment": ""},
                "company_contact_details_correct": {"answer": "no", "comment": "New registered office and contact email."},
            },
            "declaration_accepted": True,
        }
        self._post(
            "/api/portal/applications/app-owned/periodic-review/submit",
            submit_payload,
            self.client_token,
        )
        detail_resp = self._get(f"/api/monitoring/reviews/{self._owned_review_id}", self.admin_token)
        assert detail_resp.code == 200
        detail = json.loads(detail_resp.body)
        assert detail["queue_status"] == "awaiting_client"
        assert detail["risk_level"] == "HIGH"
        assert detail["client_attestation_status"] == "submitted"
        assert detail["client_attestation"]["has_material_changes"] is True
        flagged = [q for q in detail["client_attestation"]["questions"] if q["material_change"]]
        assert [q["key"] for q in flagged] == ["company_contact_details_correct"]

    def test_submit_after_draft_with_datetime_saved_at_serializes_timestamps_and_writes_audit(self):
        draft_payload = {
            "answers": {
                "directors_changed": {"answer": "yes", "comment": "One director resigned."},
                "shareholders_changed": {"answer": "no", "comment": ""},
            },
            "declaration_accepted": False,
        }
        save_resp = self._post(
            "/api/portal/applications/app-owned/periodic-review/save-draft",
            draft_payload,
            self.client_token,
        )
        assert save_resp.code == 200

        draft_ts = datetime(2026, 6, 5, 14, 34, 15, 394102, tzinfo=timezone.utc)
        self._conn.execute(
            "UPDATE periodic_reviews SET client_attestation_saved_at = ? WHERE id = ?",
            (draft_ts, self._owned_review_id),
        )
        self._conn.commit()

        submit_payload = {
            "answers": {
                "directors_changed": {"answer": "yes", "comment": "One director resigned."},
                "shareholders_changed": {"answer": "no", "comment": ""},
                "ubos_changed": {"answer": "no", "comment": ""},
                "business_activity_changed": {"answer": "no", "comment": ""},
                "jurisdictions_changed": {"answer": "no", "comment": ""},
                "transaction_volume_changed": {"answer": "no", "comment": ""},
                "licence_regulatory_status_changed": {"answer": "no", "comment": ""},
                "company_contact_details_correct": {"answer": "yes", "comment": ""},
            },
            "declaration_accepted": True,
        }
        submit_resp = self._post(
            "/api/portal/applications/app-owned/periodic-review/submit",
            submit_payload,
            self.client_token,
        )
        assert submit_resp.code == 200
        body = json.loads(submit_resp.body)
        assert body["attestation"]["status"] == "submitted"
        assert body["attestation"]["saved_at"] == draft_ts.isoformat()
        assert isinstance(body["attestation"]["submitted_at"], str)

        stored = self._conn.execute(
            "SELECT client_attestation_status, client_attestation_payload, client_attestation_saved_at, client_attestation_submitted_at "
            "FROM periodic_reviews WHERE id = ?",
            (self._owned_review_id,),
        ).fetchone()
        assert stored["client_attestation_status"] == "submitted"
        payload = json.loads(stored["client_attestation_payload"])
        assert payload["saved_at"] == draft_ts.isoformat()
        assert isinstance(payload["submitted_at"], str)

        audit = self._conn.execute(
            "SELECT action, detail FROM audit_log WHERE action = 'periodic_review_attestation_submitted' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None
        detail = json.loads(audit["detail"])
        assert detail["periodic_review_id"] == self._owned_review_id
        assert detail["submitted_at"] == body["attestation"]["submitted_at"]


def test_prepare_attestation_submission_update_serializes_datetime_saved_at_from_existing_draft():
    draft_ts = datetime(2026, 6, 5, 14, 34, 15, 394102, tzinfo=timezone.utc)
    review_row = {
        "id": 18,
        "client_attestation_status": "draft",
        "client_attestation_saved_at": draft_ts,
        "client_attestation_submitted_at": None,
        "client_attestation_submitted_by": None,
        "client_attestation_payload": json.dumps({
            "questionnaire_version": "prs2_v1",
            "answers": {
                "directors_changed": {"answer": "yes", "comment": "One director resigned."},
                "shareholders_changed": {"answer": "no", "comment": ""},
                "ubos_changed": {"answer": "no", "comment": ""},
                "business_activity_changed": {"answer": "no", "comment": ""},
                "jurisdictions_changed": {"answer": "no", "comment": ""},
                "transaction_volume_changed": {"answer": "no", "comment": ""},
                "licence_regulatory_status_changed": {"answer": "no", "comment": ""},
                "company_contact_details_correct": {"answer": "yes", "comment": ""},
            },
            "declaration_accepted": False,
            "saved_at": draft_ts.isoformat(),
            "submitted_at": None,
        }),
    }
    submit_payload = {
        "answers": {
            "directors_changed": {"answer": "yes", "comment": "One director resigned."},
            "shareholders_changed": {"answer": "no", "comment": ""},
            "ubos_changed": {"answer": "no", "comment": ""},
            "business_activity_changed": {"answer": "no", "comment": ""},
            "jurisdictions_changed": {"answer": "no", "comment": ""},
            "transaction_volume_changed": {"answer": "no", "comment": ""},
            "licence_regulatory_status_changed": {"answer": "no", "comment": ""},
            "company_contact_details_correct": {"answer": "yes", "comment": ""},
        },
        "declaration_accepted": True,
    }

    update = prepare_attestation_submission_update(review_row, submit_payload, submitted_by="client001")
    payload = json.loads(update["payload_json"])

    assert update["status"] == "submitted"
    assert update["saved_at"] == draft_ts.isoformat()
    assert payload["saved_at"] == draft_ts.isoformat()
    assert isinstance(update["submitted_at"], str)
    assert isinstance(payload["submitted_at"], str)
    assert update["snapshot"]["saved_at"] == draft_ts.isoformat()
    assert update["snapshot"]["submitted_by"] == "client001"
