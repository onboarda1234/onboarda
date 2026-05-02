"""Document download safety and ownership tests for Prompt 4 hardening."""
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import requests as http_requests
import tornado.httpserver
import tornado.ioloop


def _find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _seed_app_and_doc(app_id, app_ref, client_id, doc_id, file_path, doc_name="document.txt", mime_type="text/plain", s3_key=None):
    from db import get_db

    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO applications (id, ref, client_id, company_name, country, status) VALUES (?, ?, ?, ?, ?, ?)",
        (app_id, app_ref, client_id, "Test Co", "Mauritius", "draft"),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO documents
            (id, application_id, doc_type, doc_name, file_path, verification_status, mime_type, s3_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (doc_id, app_id, "supporting_doc", doc_name, file_path, "pending", mime_type, s3_key),
    )
    conn.commit()
    conn.close()


@pytest.fixture(scope="module")
def api_server():
    os.environ.setdefault("ENVIRONMENT", "testing")
    os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

    from db import get_db, init_db, seed_initial_data

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

    yield f"http://127.0.0.1:{port}"

    io_loop = server_ref.get("loop")
    srv = server_ref.get("server")
    if io_loop and srv:
        io_loop.add_callback(srv.stop)
        io_loop.add_callback(io_loop.stop)
    thread.join(timeout=2)


class TestDocumentDownloadSafety:
    def test_s3_presign_uses_open_db_connection(self, api_server):
        from auth import create_token

        _seed_app_and_doc(
            app_id="app_s3_open_db",
            app_ref="ARF-S3-OPEN-DB",
            client_id="client_s3_owner",
            doc_id="doc_s3_open_db",
            file_path="unused-local-path.txt",
            doc_name="s3-doc.pdf",
            mime_type="application/pdf",
            s3_key="documents/app_s3_open_db/s3-doc.pdf",
        )

        class FakeS3Client:
            def get_presigned_url_with_ownership(self, key, requesting_user_id, requesting_user_role, db_connection=None, expiry=900, response_filename=None):
                # This query will fail if the connection has already been closed.
                row = db_connection.execute("SELECT 1 AS ok").fetchone()
                assert row and row["ok"] == 1
                return True, "https://example.test/presigned"

        token = create_token("client_s3_owner", "client", "S3 Owner", "client")
        with patch("server.HAS_S3", True), patch("server.get_s3_client", return_value=FakeS3Client()):
            resp = http_requests.get(
                f"{api_server}/api/documents/doc_s3_open_db/download",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "s3"
        assert body["download_url"] == "https://example.test/presigned"

    def test_local_relative_file_resolves_under_upload_dir(self, api_server):
        from auth import create_token
        from server import UPLOAD_DIR

        rel_name = "doc_relative_download.txt"
        upload_path = Path(UPLOAD_DIR)
        upload_path.mkdir(parents=True, exist_ok=True)
        local_file = upload_path / rel_name
        local_file.write_text("safe local file", encoding="utf-8")

        _seed_app_and_doc(
            app_id="app_local_relative",
            app_ref="ARF-LOCAL-REL",
            client_id="client_local_rel",
            doc_id="doc_local_relative",
            file_path=rel_name,
            doc_name="local.txt",
            mime_type="text/plain",
            s3_key=None,
        )

        token = create_token("client_local_rel", "client", "Local Rel", "client")
        resp = http_requests.get(
            f"{api_server}/api/documents/doc_local_relative/download",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )

        assert resp.status_code == 200
        assert resp.text == "safe local file"

    def test_absolute_outside_upload_path_rejected(self, api_server):
        from auth import create_token

        outside_file = Path(tempfile.gettempdir()) / "outside_onboarda_download.txt"
        outside_file.write_text("outside", encoding="utf-8")

        _seed_app_and_doc(
            app_id="app_abs_outside",
            app_ref="ARF-ABS-OUT",
            client_id="client_abs_out",
            doc_id="doc_abs_outside",
            file_path=str(outside_file),
            doc_name="outside.txt",
            mime_type="text/plain",
            s3_key=None,
        )

        token = create_token("client_abs_out", "client", "Abs Out", "client")
        resp = http_requests.get(
            f"{api_server}/api/documents/doc_abs_outside/download",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )

        assert resp.status_code == 404

    def test_traversal_path_rejected(self, api_server):
        from auth import create_token

        _seed_app_and_doc(
            app_id="app_traversal",
            app_ref="ARF-TRAV",
            client_id="client_traversal",
            doc_id="doc_traversal",
            file_path="..\\..\\Windows\\win.ini",
            doc_name="traversal.txt",
            mime_type="text/plain",
            s3_key=None,
        )

        token = create_token("client_traversal", "client", "Traversal", "client")
        resp = http_requests.get(
            f"{api_server}/api/documents/doc_traversal/download",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )

        assert resp.status_code == 404
