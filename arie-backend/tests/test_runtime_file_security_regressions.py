"""Runtime document and evidence security regressions.

These tests intentionally cover product security controls that must remain
independent from the removed developer edit-blocking workflow.
"""

import hashlib
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

from tornado.testing import AsyncHTTPTestCase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ENVIRONMENT"] = "testing"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"


def _sync_test_db_path(path):
    os.environ["DB_PATH"] = path
    for module_name in ("config", "db", "server"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "DB_PATH"):
            setattr(module, "DB_PATH", path)
        if module_name == "server" and module is not None and hasattr(module, "_CFG_DB_PATH"):
            setattr(module, "_CFG_DB_PATH", path)


def _multipart(fields, files):
    boundary = f"----regmind-{uuid.uuid4().hex}"
    chunks = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(str(value).encode())
        chunks.append(b"\r\n")
    for name, filename, content_type, body in files:
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode()
        )
        chunks.append(body)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class RuntimeFileSecurityRegressionTest(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"regmind_runtime_file_security_{os.getpid()}_{uuid.uuid4().hex}.db",
        )
        try:
            os.unlink(self._db_path)
        except OSError:
            pass
        _sync_test_db_path(self._db_path)

        from db import get_db, init_db, seed_initial_data

        init_db()
        conn = get_db()
        seed_initial_data(conn)
        conn.commit()
        conn.close()

        from server import make_app

        return make_app()

    def setUp(self):
        super().setUp()
        from auth import create_token
        from db import get_db

        self.conn = get_db()
        self._seed_client("client_owner", "Owner Client")
        self._seed_client("client_other", "Other Client")
        self._seed_user("admin_file_security", "admin", "Admin File Security")
        self._seed_user("co_file_security", "co", "CO File Security")
        self._seed_user("analyst_file_security", "analyst", "Analyst File Security")
        self.conn.commit()

        self.owner_token = create_token("client_owner", "client", "Owner Client", "client")
        self.other_client_token = create_token("client_other", "client", "Other Client", "client")
        self.admin_token = create_token("admin_file_security", "admin", "Admin File Security", "officer")
        self.co_token = create_token("co_file_security", "co", "CO File Security", "officer")
        self.analyst_token = create_token("analyst_file_security", "analyst", "Analyst File Security", "officer")

    def tearDown(self):
        self.conn.close()
        try:
            os.unlink(self._db_path)
        except OSError:
            pass
        super().tearDown()

    def _seed_user(self, user_id, role, name):
        self.conn.execute(
            """
            INSERT OR REPLACE INTO users
                (id, email, password_hash, full_name, role, status)
            VALUES (?, ?, ?, ?, ?, 'active')
            """,
            (user_id, f"{user_id}@test.local", "test-only", name, role),
        )

    def _seed_client(self, client_id, name):
        self.conn.execute(
            """
            INSERT OR REPLACE INTO clients
                (id, email, password_hash, company_name, status)
            VALUES (?, ?, ?, ?, 'active')
            """,
            (client_id, f"{client_id}@test.local", "test-only", name),
        )

    def _seed_application(self, app_id="app_file_security", client_id="client_owner", status="rmi_sent"):
        ref = f"ARF-{app_id}"
        self.conn.execute(
            """
            INSERT OR REPLACE INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type,
                 status, risk_level, risk_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (app_id, ref, client_id, "Runtime Security Ltd", "Mauritius", "Fintech", "SME", status, "MEDIUM", 50),
        )
        self.conn.commit()
        return app_id, ref

    def _seed_document(self, app_id, doc_id="doc_file_security", content=b"runtime document"):
        from server import UPLOAD_DIR

        upload_dir = Path(UPLOAD_DIR)
        upload_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{doc_id}.txt"
        (upload_dir / filename).write_bytes(content)
        self.conn.execute(
            """
            INSERT OR REPLACE INTO documents
                (id, application_id, doc_type, doc_name, file_path,
                 verification_status, mime_type, is_current)
            VALUES (?, ?, ?, ?, ?, 'pending', 'text/plain', TRUE)
            """,
            (doc_id, app_id, "supporting_document", filename, filename),
        )
        self.conn.commit()
        return doc_id

    def _auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_unauthenticated_user_cannot_download_client_document(self):
        app_id, _ = self._seed_application()
        doc_id = self._seed_document(app_id)

        response = self.fetch(f"/api/documents/{doc_id}/download")

        self.assertEqual(response.code, 401)

    def test_client_cannot_download_another_clients_document(self):
        app_id, _ = self._seed_application(client_id="client_owner")
        doc_id = self._seed_document(app_id)

        response = self.fetch(
            f"/api/documents/{doc_id}/download",
            headers=self._auth(self.other_client_token),
        )

        self.assertEqual(response.code, 403)

    def test_authorized_client_can_download_own_document(self):
        app_id, _ = self._seed_application(client_id="client_owner")
        doc_id = self._seed_document(app_id, content=b"owned document")

        response = self.fetch(
            f"/api/documents/{doc_id}/download",
            headers=self._auth(self.owner_token),
        )

        self.assertEqual(response.code, 200)
        self.assertEqual(response.body, b"owned document")

    def test_officer_role_restrictions_apply_to_evidence_access(self):
        app_id, _ = self._seed_application()
        self._seed_document(app_id)

        analyst_response = self.fetch(
            f"/api/applications/{app_id}/evidence-pack",
            headers=self._auth(self.analyst_token),
        )
        client_response = self.fetch(
            f"/api/applications/{app_id}/evidence-pack",
            headers=self._auth(self.owner_token),
        )

        self.assertEqual(analyst_response.code, 200)
        self.assertEqual(client_response.code, 403)

    def test_evidence_pack_export_requires_authorized_role(self):
        app_id, _ = self._seed_application()
        payload = {
            "export_type": "auditor",
            "reason": "Runtime file security regression",
            "redaction_level": "full_internal",
            "include_sections": ["audit_trail"],
        }

        unauthenticated = self.fetch(
            f"/api/applications/{app_id}/export-pack",
            method="POST",
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        co_forbidden = self.fetch(
            f"/api/applications/{app_id}/export-pack",
            method="POST",
            body=json.dumps(payload),
            headers={**self._auth(self.co_token), "Content-Type": "application/json"},
        )

        self.assertEqual(unauthenticated.code, 401)
        self.assertEqual(co_forbidden.code, 403)

    def test_evidence_pack_export_hash_matches_response_body(self):
        app_id, _ = self._seed_application()
        payload = {
            "export_type": "auditor",
            "reason": "Runtime file security regression",
            "redaction_level": "full_internal",
            "include_sections": ["audit_trail"],
        }

        response = self.fetch(
            f"/api/applications/{app_id}/export-pack",
            method="POST",
            body=json.dumps(payload),
            headers={**self._auth(self.admin_token), "Content-Type": "application/json"},
        )

        self.assertEqual(response.code, 200)
        response_hash = response.headers.get("X-Evidence-Pack-SHA256")
        self.assertRegex(response_hash or "", r"^[a-f0-9]{64}$")
        self.assertEqual(response_hash, hashlib.sha256(response.body).hexdigest())

    def test_invalid_file_type_upload_is_rejected(self):
        app_id, _ = self._seed_application(status="rmi_sent")
        body, content_type = _multipart(
            {"doc_type": "passport"},
            [("file", "payload.exe", "application/octet-stream", b"MZnot-a-document")],
        )

        response = self.fetch(
            f"/api/applications/{app_id}/documents",
            method="POST",
            body=body,
            headers={**self._auth(self.owner_token), "Content-Type": content_type},
        )

        self.assertEqual(response.code, 400)
        self.assertIn("File rejected", response.body.decode())
