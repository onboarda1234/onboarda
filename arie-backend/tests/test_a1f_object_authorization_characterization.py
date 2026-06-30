"""A1F object-level authorization characterization tests.

These tests are intentionally preparation-only. Cross-object client tests assert
the target 403 behavior and remain strict xfail until A1F-1 adds the production
object-authorization checks.
"""

import json
import socket
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import requests as http_requests
import tornado.httpserver
import tornado.ioloop


A1F_XFAIL_REASON = "object-level authorization fix pending A1F-1"


def _find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def a1f_api_server(temp_db):
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
    time.sleep(0.1)

    yield f"http://127.0.0.1:{port}"

    io_loop = server_ref.get("loop")
    srv = server_ref.get("server")
    if io_loop and srv:
        io_loop.add_callback(srv.stop)
        io_loop.add_callback(io_loop.stop)
    thread.join(timeout=2)


def _token(user_id, role="client", name="A1F User", token_type="client"):
    from auth import create_token

    return create_token(user_id, role, name, token_type)


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _seed_client(conn, client_id):
    conn.execute(
        """
        INSERT OR IGNORE INTO clients (id, email, password_hash, company_name, status)
        VALUES (?, ?, ?, ?, 'active')
        """,
        (client_id, f"{client_id}@a1f.test", "test-password-hash", f"{client_id} Ltd"),
    )


def _seed_application(conn, app_id, ref, client_id, *, status="kyc_documents", prescreening=None):
    _seed_client(conn, client_id)
    conn.execute(
        """
        INSERT OR REPLACE INTO applications
            (id, ref, client_id, company_name, country, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            ref,
            client_id,
            f"{ref} Company",
            "Mauritius",
            status,
            json.dumps(prescreening or {}),
        ),
    )


def _upload_file(name, content=b"a1f local document"):
    from server import UPLOAD_DIR

    upload_dir = Path(UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / name
    path.write_bytes(content)
    return name


def _seed_document(conn, app_id, doc_id, *, doc_type="cert_inc", file_name=None):
    file_name = file_name or f"{doc_id}.txt"
    file_path = _upload_file(file_name)
    conn.execute(
        """
        INSERT OR REPLACE INTO documents
            (id, application_id, doc_type, doc_name, file_path, verification_status, mime_type)
        VALUES (?, ?, ?, ?, ?, 'pending', 'text/plain')
        """,
        (doc_id, app_id, doc_type, file_name, file_path),
    )


def _seed_application_with_document(conn, *, owner_id, app_id, ref, doc_id):
    _seed_application(conn, app_id, ref, owner_id)
    _seed_document(conn, app_id, doc_id)
    conn.commit()


def _disable_agent1(conn):
    conn.execute(
        """
        INSERT OR IGNORE INTO ai_agents (agent_number, name, stage, enabled, checks)
        VALUES (1, 'Agent 1', 'Document Verification', 0, '[]')
        """
    )
    conn.execute("UPDATE ai_agents SET enabled=0 WHERE agent_number=1")
    conn.commit()


def _seed_sumsub_mapping(conn, *, app_id, client_id, external_user_id, applicant_id):
    _seed_application(
        conn,
        app_id,
        f"ARF-A1F-{app_id}",
        client_id,
        prescreening={"sumsub_applicant_ids": {external_user_id: applicant_id}},
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO sumsub_applicant_mappings
            (application_id, applicant_id, external_user_id, person_name, person_type)
        VALUES (?, ?, ?, 'A1F Person', 'director')
        """,
        (app_id, applicant_id, external_user_id),
    )
    conn.commit()


class _FakeClaudeClient:
    def __init__(self, *args, **kwargs):
        pass

    def verify_document(self, **kwargs):
        return {
            "overall": "verified",
            "confidence": 0.99,
            "checks": [
                {
                    "label": "A1F fake verification",
                    "type": "validity",
                    "result": "pass",
                    "message": "offline characterization path",
                }
            ],
        }


def test_document_download_allows_owning_client(a1f_api_server, db):
    _seed_application_with_document(
        db,
        owner_id="a1f_owner_download",
        app_id="a1f_app_download_owner",
        ref="ARF-A1F-DOWNLOAD-OWNER",
        doc_id="a1f_doc_download_owner",
    )

    token = _token("a1f_owner_download", "client", "A1F Owner", "client")
    resp = http_requests.get(
        f"{a1f_api_server}/api/documents/a1f_doc_download_owner/download",
        headers=_headers(token),
        timeout=5,
    )

    assert resp.status_code == 200, resp.text
    assert resp.content == b"a1f local document"


def test_document_download_denies_cross_object_client(a1f_api_server, db):
    _seed_application_with_document(
        db,
        owner_id="a1f_owner_download_b",
        app_id="a1f_app_download_b",
        ref="ARF-A1F-DOWNLOAD-B",
        doc_id="a1f_doc_download_b",
    )

    token = _token("a1f_other_download_client", "client", "A1F Other", "client")
    resp = http_requests.get(
        f"{a1f_api_server}/api/documents/a1f_doc_download_b/download",
        headers=_headers(token),
        timeout=5,
    )

    assert resp.status_code == 403, resp.text


def test_documents_ai_verify_officer_success_path(a1f_api_server, db):
    _seed_application_with_document(
        db,
        owner_id="a1f_owner_ai_success",
        app_id="a1f_app_ai_success",
        ref="ARF-A1F-AI-SUCCESS",
        doc_id="a1f_doc_ai_success",
    )

    token = _token("a1f_sco_ai_success", "sco", "A1F SCO", "officer")
    with patch("server.HAS_CLAUDE_CLIENT", True), patch("server.ClaudeClient", _FakeClaudeClient):
        resp = http_requests.post(
            f"{a1f_api_server}/api/documents/ai-verify",
            headers={**_headers(token), "Content-Type": "application/json"},
            json={
                "doc_type": "cert_inc",
                "file_name": "a1f_doc_ai_success.txt",
                "doc_id": "a1f_doc_ai_success",
                "application_id": "a1f_app_ai_success",
                "doc_category": "company",
            },
            timeout=5,
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["overall"] == "verified"
    assert body["authoritative"] is False


@pytest.mark.xfail(strict=True, reason=A1F_XFAIL_REASON)
def test_documents_ai_verify_cross_object_client_expected_403(a1f_api_server, db):
    _seed_application_with_document(
        db,
        owner_id="a1f_owner_ai_b",
        app_id="a1f_app_ai_b",
        ref="ARF-A1F-AI-B",
        doc_id="a1f_doc_ai_b",
    )

    token = _token("a1f_client_ai_a", "client", "A1F Client A", "client")
    with patch("server.HAS_CLAUDE_CLIENT", True), patch("server.ClaudeClient", _FakeClaudeClient):
        resp = http_requests.post(
            f"{a1f_api_server}/api/documents/ai-verify",
            headers={**_headers(token), "Content-Type": "application/json"},
            json={
                "doc_type": "cert_inc",
                "file_name": "a1f_doc_ai_b.txt",
                "doc_id": "a1f_doc_ai_b",
                "application_id": "a1f_app_ai_b",
                "doc_category": "company",
            },
            timeout=5,
        )

    assert resp.status_code == 403, resp.text


def test_sumsub_applicant_officer_success_path_records_mapping(a1f_api_server, db):
    _seed_application(
        db,
        "a1f_app_sumsub_success",
        "ARF-A1F-SUMSUB-SUCCESS",
        "a1f_sumsub_owner",
    )
    db.commit()

    token = _token("a1f_sco_sumsub_success", "sco", "A1F SCO", "officer")
    with patch(
        "server.sumsub_create_applicant",
        return_value={
            "applicant_id": "a1f_applicant_success",
            "status": "init",
            "source": "sumsub-test",
            "api_status": "mocked",
        },
    ):
        resp = http_requests.post(
            f"{a1f_api_server}/api/kyc/applicant",
            headers={**_headers(token), "Content-Type": "application/json"},
            json={
                "external_user_id": "a1f_external_success",
                "first_name": "A1F",
                "last_name": "Officer",
                "application_id": "a1f_app_sumsub_success",
                "person_type": "director",
            },
            timeout=5,
        )

    assert resp.status_code == 200, resp.text
    row = db.execute(
        """
        SELECT applicant_id FROM sumsub_applicant_mappings
        WHERE application_id=? AND external_user_id=?
        """,
        ("a1f_app_sumsub_success", "a1f_external_success"),
    ).fetchone()
    assert row["applicant_id"] == "a1f_applicant_success"


@pytest.mark.xfail(strict=True, reason=A1F_XFAIL_REASON)
def test_sumsub_applicant_cross_object_client_expected_403(a1f_api_server, db):
    _seed_application(
        db,
        "a1f_app_sumsub_b",
        "ARF-A1F-SUMSUB-B",
        "a1f_sumsub_owner_b",
    )
    db.commit()

    token = _token("a1f_sumsub_client_a", "client", "A1F Client A", "client")
    with patch(
        "server.sumsub_create_applicant",
        return_value={
            "applicant_id": "a1f_applicant_cross",
            "status": "init",
            "source": "sumsub-test",
            "api_status": "mocked",
        },
    ):
        resp = http_requests.post(
            f"{a1f_api_server}/api/kyc/applicant",
            headers={**_headers(token), "Content-Type": "application/json"},
            json={
                "external_user_id": "a1f_external_cross",
                "first_name": "A1F",
                "last_name": "Cross",
                "application_id": "a1f_app_sumsub_b",
                "person_type": "director",
            },
            timeout=5,
        )

    assert resp.status_code == 403, resp.text


def test_document_verify_officer_success_path_agent_disabled(a1f_api_server, db):
    _seed_application_with_document(
        db,
        owner_id="a1f_owner_docverify_success",
        app_id="a1f_app_docverify_success",
        ref="ARF-A1F-DOCVERIFY-SUCCESS",
        doc_id="a1f_docverify_success",
    )
    _disable_agent1(db)

    token = _token("a1f_sco_docverify_success", "sco", "A1F SCO", "officer")
    resp = http_requests.post(
        f"{a1f_api_server}/api/documents/a1f_docverify_success/verify",
        headers=_headers(token),
        timeout=5,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verification_status"] == "skipped"
    assert body["requires_review"] is True


@pytest.mark.xfail(strict=True, reason=A1F_XFAIL_REASON)
def test_document_verify_cross_object_client_expected_403(a1f_api_server, db):
    _seed_application_with_document(
        db,
        owner_id="a1f_owner_docverify_b",
        app_id="a1f_app_docverify_b",
        ref="ARF-A1F-DOCVERIFY-B",
        doc_id="a1f_docverify_b",
    )
    _disable_agent1(db)

    token = _token("a1f_docverify_client_a", "client", "A1F Client A", "client")
    resp = http_requests.post(
        f"{a1f_api_server}/api/documents/a1f_docverify_b/verify",
        headers=_headers(token),
        timeout=5,
    )

    assert resp.status_code == 403, resp.text


def test_sumsub_access_token_owning_client_success_path(a1f_api_server, db):
    _seed_sumsub_mapping(
        db,
        app_id="a1f_app_token_owner",
        client_id="a1f_token_owner",
        external_user_id="a1f_external_token_owner",
        applicant_id="a1f_applicant_token_owner",
    )

    token = _token("a1f_token_owner", "client", "A1F Token Owner", "client")
    with patch(
        "server.sumsub_generate_access_token",
        return_value={
            "token": "mocked-owner-token",
            "external_user_id": "a1f_external_token_owner",
            "source": "sumsub-test",
        },
    ):
        resp = http_requests.post(
            f"{a1f_api_server}/api/kyc/token",
            headers={**_headers(token), "Content-Type": "application/json"},
            json={"external_user_id": "a1f_external_token_owner"},
            timeout=5,
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["token"] == "mocked-owner-token"


@pytest.mark.xfail(strict=True, reason=A1F_XFAIL_REASON)
def test_sumsub_access_token_cross_object_client_expected_403(a1f_api_server, db):
    _seed_sumsub_mapping(
        db,
        app_id="a1f_app_token_b",
        client_id="a1f_token_owner_b",
        external_user_id="a1f_external_token_b",
        applicant_id="a1f_applicant_token_b",
    )

    token = _token("a1f_token_client_a", "client", "A1F Client A", "client")
    with patch(
        "server.sumsub_generate_access_token",
        return_value={
            "token": "mocked-cross-token",
            "external_user_id": "a1f_external_token_b",
            "source": "sumsub-test",
        },
    ):
        resp = http_requests.post(
            f"{a1f_api_server}/api/kyc/token",
            headers={**_headers(token), "Content-Type": "application/json"},
            json={"external_user_id": "a1f_external_token_b"},
            timeout=5,
        )

    assert resp.status_code == 403, resp.text


def test_sumsub_document_officer_success_path(a1f_api_server):
    token = _token("a1f_sco_sumsub_doc_success", "sco", "A1F SCO", "officer")
    with patch(
        "server.sumsub_add_document",
        return_value={
            "applicant_id": "a1f_applicant_doc_success",
            "status": "uploaded",
            "source": "sumsub-test",
        },
    ):
        resp = http_requests.post(
            f"{a1f_api_server}/api/kyc/document",
            headers={**_headers(token), "Content-Type": "application/json"},
            json={
                "applicant_id": "a1f_applicant_doc_success",
                "doc_type": "PASSPORT",
                "country": "MU",
                "file_data": "ZHVtbXk=",
                "file_name": "passport.pdf",
            },
            timeout=5,
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "uploaded"


@pytest.mark.xfail(strict=True, reason=A1F_XFAIL_REASON)
def test_sumsub_document_cross_object_client_expected_403(a1f_api_server, db):
    _seed_sumsub_mapping(
        db,
        app_id="a1f_app_sumsub_doc_b",
        client_id="a1f_sumsub_doc_owner_b",
        external_user_id="a1f_external_sumsub_doc_b",
        applicant_id="a1f_applicant_sumsub_doc_b",
    )

    token = _token("a1f_sumsub_doc_client_a", "client", "A1F Client A", "client")
    with patch(
        "server.sumsub_add_document",
        return_value={
            "applicant_id": "a1f_applicant_sumsub_doc_b",
            "status": "uploaded",
            "source": "sumsub-test",
        },
    ):
        resp = http_requests.post(
            f"{a1f_api_server}/api/kyc/document",
            headers={**_headers(token), "Content-Type": "application/json"},
            json={
                "applicant_id": "a1f_applicant_sumsub_doc_b",
                "doc_type": "PASSPORT",
                "country": "MU",
                "file_data": "ZHVtbXk=",
                "file_name": "passport.pdf",
            },
            timeout=5,
        )

    assert resp.status_code == 403, resp.text
