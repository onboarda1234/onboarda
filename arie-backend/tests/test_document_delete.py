"""
DocumentDeleteHandler API Tests
Tests for DELETE /api/applications/:app_id/documents/:doc_id endpoint.
Covers: ownership enforcement, status gating, successful deletion, and 404 handling.
"""
import os
import sys
import tempfile
import socket
import threading
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

import requests as http_requests
import tornado.ioloop
import tornado.httpserver


def _find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def api_server():
    """Start a real Tornado HTTP server for DocumentDeleteHandler tests."""
    db_path = os.path.join(tempfile.gettempdir(), f"onboarda_test_{os.getpid()}.db")
    os.environ["DB_PATH"] = db_path

    from db import init_db, seed_initial_data, get_db
    init_db()
    try:
        conn = get_db()
        seed_initial_data(conn)
        conn.commit()
        conn.close()
    except Exception:
        pass

    from server import make_app
    app = make_app()
    port = _find_free_port()

    server_ref = {}
    started = threading.Event()

    def run_server():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        io_loop = tornado.ioloop.IOLoop.current()
        srv = tornado.httpserver.HTTPServer(app)
        srv.listen(port, "127.0.0.1")
        server_ref["server"] = srv
        server_ref["loop"] = io_loop
        started.set()
        io_loop.start()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    started.wait(timeout=3)
    time.sleep(0.2)

    base_url = f"http://127.0.0.1:{port}"
    yield base_url

    io_loop = server_ref.get("loop")
    srv = server_ref.get("server")
    if io_loop and srv:
        io_loop.add_callback(srv.stop)
        io_loop.add_callback(io_loop.stop)
    thread.join(timeout=2)


def _seed_app_and_doc(app_id, app_ref, client_id, status, doc_id):
    """Insert a test application and document into the database."""
    from db import get_db
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO applications (id, ref, client_id, company_name, country, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (app_id, app_ref, client_id, "Test Co", "Mauritius", status),
    )
    conn.execute(
        "INSERT OR IGNORE INTO documents (id, application_id, doc_type, doc_name, file_path, verification_status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, app_id, "cert_inc", "certificate.pdf", "/tmp/fake_cert.pdf", "pending"),
    )
    conn.commit()
    conn.close()


class TestDocumentDeleteHandler:
    """Tests for DELETE /api/applications/:app_id/documents/:doc_id"""

    def test_delete_requires_auth(self, api_server):
        """DELETE without token returns 401."""
        resp = http_requests.delete(
            f"{api_server}/api/applications/app1/documents/doc1",
            timeout=3,
        )
        assert resp.status_code == 401

    def test_delete_document_success(self, api_server):
        """DELETE in a pre-submission status should remove the document."""
        from auth import create_token

        _seed_app_and_doc("app_del_ok", "ARF-DEL-OK", "client_del", "draft", "doc_del_ok")
        token = create_token("client_del", "client", "Del Client", "client")

        resp = http_requests.delete(
            f"{api_server}/api/applications/app_del_ok/documents/doc_del_ok",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True
        assert data["id"] == "doc_del_ok"

    def test_delete_blocked_after_submission(self, api_server):
        """DELETE when app is in_review should return 403."""
        from auth import create_token

        _seed_app_and_doc("app_del_sub", "ARF-DEL-SUB", "client_sub", "in_review", "doc_del_sub")
        token = create_token("client_sub", "client", "Sub Client", "client")

        resp = http_requests.delete(
            f"{api_server}/api/applications/app_del_sub/documents/doc_del_sub",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 403

    def test_delete_document_not_found(self, api_server):
        """DELETE for a non-existent document returns 404."""
        from auth import create_token

        _seed_app_and_doc("app_del_nf", "ARF-DEL-NF", "client_nf", "draft", "doc_del_nf")
        token = create_token("client_nf", "client", "NF Client", "client")

        resp = http_requests.delete(
            f"{api_server}/api/applications/app_del_nf/documents/nonexistent",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 404

    def test_delete_application_not_found(self, api_server):
        """DELETE for a non-existent application returns 404."""
        from auth import create_token

        token = create_token("client_x", "client", "X Client", "client")
        resp = http_requests.delete(
            f"{api_server}/api/applications/nonexistent_app/documents/doc1",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 404

    def test_delete_ownership_enforcement(self, api_server):
        """Client A cannot delete documents belonging to Client B's application."""
        from auth import create_token

        _seed_app_and_doc("app_del_own", "ARF-DEL-OWN", "client_owner", "draft", "doc_del_own")
        # Token for a different client
        token = create_token("client_other", "client", "Other Client", "client")

        resp = http_requests.delete(
            f"{api_server}/api/applications/app_del_own/documents/doc_del_own",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 403

    def test_delete_allowed_in_kyc_documents_status(self, api_server):
        """DELETE is allowed when application is in kyc_documents status."""
        from auth import create_token

        _seed_app_and_doc("app_del_kyc", "ARF-DEL-KYC", "client_kyc", "kyc_documents", "doc_del_kyc")
        token = create_token("client_kyc", "client", "KYC Client", "client")

        resp = http_requests.delete(
            f"{api_server}/api/applications/app_del_kyc/documents/doc_del_kyc",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True

    def test_officer_can_delete_document(self, api_server):
        """Officers (admin) should be able to delete documents in allowed statuses."""
        from auth import create_token

        _seed_app_and_doc("app_del_off", "ARF-DEL-OFF", "client_off", "draft", "doc_del_off")
        token = create_token("admin001", "admin", "Test Admin", "officer")

        resp = http_requests.delete(
            f"{api_server}/api/applications/app_del_off/documents/doc_del_off",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True

    def test_delete_idempotent_second_call_returns_404(self, api_server):
        """Deleting the same document twice should return 404 on second call."""
        from auth import create_token

        _seed_app_and_doc("app_del_idem", "ARF-DEL-IDEM", "client_idem", "draft", "doc_del_idem")
        token = create_token("client_idem", "client", "Idem Client", "client")

        # First delete succeeds
        resp1 = http_requests.delete(
            f"{api_server}/api/applications/app_del_idem/documents/doc_del_idem",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp1.status_code == 200

        # Second delete returns 404
        resp2 = http_requests.delete(
            f"{api_server}/api/applications/app_del_idem/documents/doc_del_idem",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp2.status_code == 404
