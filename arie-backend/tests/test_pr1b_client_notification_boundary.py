import json
import os
import sys
import tempfile
import uuid

from tornado.testing import AsyncHTTPTestCase

from branding import BRAND


TEST_DOMAIN = BRAND.get("domain", "example.test")
TEST_BRAND_TOKEN = "".join(
    ch.lower() if ch.isalnum() else "_" for ch in BRAND.get("name", "onboarda")
).strip("_") or "onboarda"


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
                attrs[attr] = (
                    hasattr(module, attr),
                    getattr(module, attr, None),
                )
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


class PR1BClientNotificationBoundaryTest(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path_state = _capture_db_path_state()
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"{TEST_BRAND_TOKEN}_pr1b_notifications_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
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
        self.client_id = "pr1b_client_owner"
        self.other_client_id = "pr1b_client_other"
        self.app_id = "pr1b_owner_app"
        self.other_app_id = "pr1b_other_app"
        self.app_ref = "PR1B-OWNER"

        for client_id, company in (
            (self.client_id, "PR1B Owner Ltd"),
            (self.other_client_id, "PR1B Other Ltd"),
        ):
            self.db.execute(
                """
                INSERT OR REPLACE INTO clients
                    (id, email, password_hash, company_name, status)
                VALUES (?, ?, ?, ?, 'active')
                """,
                (client_id, f"{client_id}@{TEST_DOMAIN}", "test-only", company),
            )

        self.db.execute(
            """
            INSERT OR REPLACE INTO users
                (id, email, password_hash, full_name, role, status)
            VALUES (?, ?, 'test-only', 'PR1B Admin', 'admin', 'active')
            """,
            (
                "admin001",
                f"admin001@{TEST_DOMAIN}",
            ),
        )

        for app_id, ref, client_id, company in (
            (self.app_id, self.app_ref, self.client_id, "PR1B Owner Ltd"),
            (self.other_app_id, "PR1B-OTHER", self.other_client_id, "PR1B Other Ltd"),
        ):
            self.db.execute(
                """
                INSERT OR REPLACE INTO applications
                    (id, ref, client_id, company_name, country, sector, entity_type,
                     status, risk_score, risk_level)
                VALUES (?, ?, ?, ?, 'Mauritius', 'Technology', 'Company', 'rmi_sent', 42, 'MEDIUM')
                """,
                (app_id, ref, client_id, company),
            )

        self.db.execute(
            """
            INSERT OR REPLACE INTO rmi_requests
                (id, application_id, client_id, status, reason, deadline, created_by, created_by_name)
            VALUES (?, ?, ?, 'open', ?, '2026-07-01', 'admin001', 'Internal Admin')
            """,
            (
                "pr1b_rmi_unsafe",
                self.app_id,
                self.client_id,
                "Internal risk score gate failed because provider raw status is needed for audit",
            ),
        )
        self.db.execute(
            """
            INSERT OR REPLACE INTO rmi_request_items
                (id, request_id, doc_type, label, description, status)
            VALUES (?, ?, 'bank_statement', ?, ?, 'requested')
            """,
            (
                "pr1b_rmi_item_unsafe",
                "pr1b_rmi_unsafe",
                "Provider raw status screenshot",
                "Review notes for memo supervisor contradiction",
            ),
        )
        self.db.execute(
            """
            INSERT INTO client_notifications
                (application_id, client_id, notification_type, title, message, documents_list, rmi_request_id)
            VALUES (?, ?, 'pre_approval_rmi', 'Additional Information Required', ?, NULL, NULL)
            """,
            (
                self.app_id,
                self.client_id,
                f"Our compliance team requires more information for {self.app_ref}. Officer notes: testing of PEP",
            ),
        )
        self.db.execute(
            """
            INSERT INTO client_notifications
                (application_id, client_id, notification_type, title, message, documents_list, rmi_request_id)
            VALUES (?, ?, 'documents_required', 'Additional Documents Required', ?, ?, ?)
            """,
            (
                self.app_id,
                self.client_id,
                f"Reason: runtime audit requires provider raw status for {self.app_ref}",
                json.dumps(["Bank statement", "Provider raw status screenshot"]),
                "pr1b_rmi_unsafe",
            ),
        )
        self.db.execute(
            """
            INSERT INTO client_notifications
                (application_id, client_id, notification_type, title, message)
            VALUES (?, ?, 'custom_safe', 'Application Update', 'Your application has been updated.')
            """,
            (self.app_id, self.client_id),
        )
        self.db.execute(
            """
            INSERT INTO client_notifications
                (application_id, client_id, notification_type, title, message)
            VALUES (?, ?, 'pre_approval_rmi', 'Other Client Internal', 'Officer notes: another client')
            """,
            (self.other_app_id, self.other_client_id),
        )
        self.db.commit()

        self.client_token = create_token(
            self.client_id, "client", "PR1B Owner Ltd", "client"
        )
        self.other_client_token = create_token(
            self.other_client_id, "client", "PR1B Other Ltd", "client"
        )
        self.admin_token = create_token("admin001", "admin", "PR1B Admin", "officer")

    def tearDown(self):
        self.db.close()
        super().tearDown()
        _restore_db_path_state(getattr(self, "_db_path_state", {}))

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def _json(self, response):
        return json.loads(response.body.decode() or "{}")

    def test_client_notifications_are_sanitized_at_read_time(self):
        response = self.fetch(
            "/api/notifications",
            headers=self._headers(self.client_token),
        )
        assert response.code == 200, response.body.decode()
        body = self._json(response)
        payload_text = json.dumps(body).lower()

        forbidden_terms = (
            "officer notes",
            "officer_notes",
            "internal notes",
            "internal_notes",
            "review notes",
            "review_notes",
            "compliance rationale",
            "assigned officer",
            "memo",
            "supervisor",
            "approval gate",
            "provider raw",
            "raw status",
            "audit",
            "internal risk",
            "risk score",
            "risk dimensions",
            "internal admin",
        )
        for term in forbidden_terms:
            assert term not in payload_text

        assert body["notifications"], "Client should still receive safe notifications"
        assert body["rmi_requests"], "Client should still receive safe RMI request state"

        by_type = {n["notification_type"]: n for n in body["notifications"]}
        assert by_type["pre_approval_rmi"]["message"] == (
            "Additional information is required to continue your application review."
        )
        assert by_type["documents_required"]["message"] == "A document requires your attention."
        assert by_type["custom_safe"]["message"] == "Your application has been updated."

        doc_notification = by_type["documents_required"]
        assert doc_notification["documents_list"] == ["Bank statement", "Requested document"]
        assert doc_notification["rmi_request"]["reason"] == (
            "Additional information is required to continue your application review."
        )
        rmi_item = doc_notification["rmi_request"]["items"][0]
        assert rmi_item["label"] == "Requested document"
        assert rmi_item["description"] == ""
        assert "created_by" not in doc_notification["rmi_request"]
        assert "created_by_name" not in doc_notification["rmi_request"]

    def test_client_notification_response_excludes_other_client_data(self):
        response = self.fetch(
            "/api/notifications",
            headers=self._headers(self.client_token),
        )
        assert response.code == 200, response.body.decode()
        body_text = response.body.decode()
        assert self.other_app_id not in body_text
        assert self.other_client_id not in body_text
        assert "another client" not in body_text.lower()
        assert "Other Client Internal" not in body_text

    def test_backoffice_user_cannot_use_client_notification_endpoint(self):
        response = self.fetch(
            "/api/notifications",
            headers=self._headers(self.admin_token),
        )
        assert response.code == 403
        body = self._json(response)
        assert body["error"] == "Only clients can retrieve notifications"
