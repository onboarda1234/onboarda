"""
Upload latency contract guards.

These tests intentionally lock existing upload-facing behavior before the
latency refactor work starts. They should fail if a later change quietly alters
the upload response shape, audit trail, size cap behavior, or duplicate gate.
"""
import hashlib
import json
import os
import socket
import sys
import threading
import time

import pytest
import requests as http_requests
import tornado.httpserver
import tornado.ioloop

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
CLIENT_ID = "upload_contract_client"
APPLICATION_ID = "upload_contract_app"
APPLICATION_REF = "ARF-2026-UPLCONTRACT"


def _find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def upload_contract_server(tmp_path, monkeypatch):
    """Run a real API server against an isolated SQLite DB and upload folder."""
    db_path = tmp_path / "upload_contracts.db"
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-testing-only")
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import config
    import db
    import environment
    import server

    config.ENVIRONMENT = "testing"
    config.DATABASE_URL = ""
    config.DB_PATH = str(db_path)
    db.DB_PATH = str(db_path)
    db.USE_POSTGRESQL = False
    environment.ENV = "testing"
    server.ENVIRONMENT = "testing"
    server.DATABASE_URL = ""
    server.USE_POSTGRES = False
    server.HAS_S3 = False
    server.UPLOAD_DIR = str(upload_dir)

    db.init_db()
    app = server.make_app()
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

    from tests.conftest import shutdown_test_http_server
    shutdown_test_http_server(thread, server_ref)


@pytest.fixture
def upload_contract_application(upload_contract_server):
    from auth import create_token
    from db import get_db

    conn = get_db()
    conn.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (CLIENT_ID, "upload-contract@example.com", "unused-in-contract-test", "Upload Contract Ltd"),
    )
    conn.execute(
        """
        INSERT INTO applications (
            id, ref, client_id, company_name, country, sector, entity_type,
            status, risk_level, risk_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            APPLICATION_ID,
            APPLICATION_REF,
            CLIENT_ID,
            "Upload Contract Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "kyc_documents",
            "MEDIUM",
            50,
        ),
    )
    conn.commit()
    conn.close()

    return {
        "id": APPLICATION_ID,
        "ref": APPLICATION_REF,
        "token": create_token(CLIENT_ID, "client", "Upload Contract Client", "client"),
    }


def test_upload_201_response_document_row_and_audit_shape(
    upload_contract_server,
    upload_contract_application,
):
    resp = http_requests.post(
        f"{upload_contract_server}/api/applications/{APPLICATION_ID}/documents?doc_type=passport",
        headers={"Authorization": f"Bearer {upload_contract_application['token']}"},
        files={"file": ("passport.pdf", PDF_BYTES, "application/pdf")},
        timeout=5,
    )

    assert resp.status_code == 201
    body = resp.json()
    assert set(body.keys()) == {
        "id",
        "doc_name",
        "doc_type",
        "file_size",
        "s3_key",
        "person_type",
        "slot_key",
        "is_current",
        "version",
        "replaced_document_ids",
        "verification_status",
        "verification_state",
        "verification_status_label",
        "verification_status_tone",
        "verification_success",
        "verification_terminal",
        "verification_queued",
    }
    assert body["doc_name"] == "passport.pdf"
    assert body["doc_type"] == "passport"
    assert body["file_size"] == len(PDF_BYTES)
    assert body["s3_key"] is None
    assert body["person_type"] is None
    assert body["slot_key"] == "entity:passport"
    assert body["is_current"] is True
    assert body["version"] == 1
    assert body["replaced_document_ids"] == []
    assert body["verification_status"] == "pending"
    assert body["verification_state"] == "pending"
    assert body["verification_success"] is False
    assert body["verification_queued"] is True

    from db import get_db

    conn = get_db()
    doc = conn.execute(
        """
        SELECT id, application_id, doc_type, doc_name, file_size, mime_type,
               file_sha256, verification_status, verification_results, review_status,
               uploaded_by, uploaded_by_actor_type, uploaded_by_actor_id,
               uploaded_by_display, upload_source
        FROM documents
        WHERE id = ?
        """,
        (body["id"],),
    ).fetchone()
    assert dict(doc) == {
        "id": body["id"],
        "application_id": APPLICATION_ID,
        "doc_type": "passport",
        "doc_name": "passport.pdf",
        "file_size": len(PDF_BYTES),
        "mime_type": "application/pdf",
        "file_sha256": hashlib.sha256(PDF_BYTES).hexdigest(),
        "verification_status": "pending",
        "verification_results": doc["verification_results"],
        "review_status": "pending",
        "uploaded_by": None,
        "uploaded_by_actor_type": "client",
        "uploaded_by_actor_id": CLIENT_ID,
        "uploaded_by_display": "Upload Contract Ltd",
        "upload_source": "client_portal",
    }
    verification_results = json.loads(doc["verification_results"])
    assert verification_results["client_submitted"] is True
    assert verification_results["upload_source"] == "client_portal"
    assert verification_results["verification_queued"] is True

    job = conn.execute(
        "SELECT status, created_by FROM verification_jobs WHERE document_id=?",
        (body["id"],),
    ).fetchone()
    assert dict(job) == {"status": "pending", "created_by": CLIENT_ID}

    status_resp = http_requests.get(
        f"{upload_contract_server}/api/documents/{body['id']}/verification-status",
        headers={"Authorization": f"Bearer {upload_contract_application['token']}"},
        timeout=5,
    )
    assert status_resp.status_code == 200
    status_body = status_resp.json()
    assert status_body["verification_status"] == "pending"
    assert status_body["verification_terminal"] is False
    assert status_body["verification_job"]["status"] == "pending"
    assert status_body["uploaded_by_name"] == "Upload Contract Ltd"

    audit = conn.execute(
        """
        SELECT *
        FROM audit_log
        WHERE action = 'Upload' AND target = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (APPLICATION_REF,),
    ).fetchone()
    conn.close()

    assert audit is not None
    assert {
        "id",
        "timestamp",
        "user_id",
        "user_name",
        "user_role",
        "action",
        "target",
        "detail",
        "ip_address",
        "before_state",
        "after_state",
    } <= set(audit.keys())
    assert audit["user_id"] == CLIENT_ID
    assert audit["user_name"] == "Upload Contract Ltd"
    assert audit["user_role"] == "client"
    assert audit["action"] == "Upload"
    assert audit["target"] == APPLICATION_REF
    assert audit["detail"] == "Document uploaded: passport.pdf (passport)"
    assert audit["ip_address"] == "127.0.0.1"
    assert audit["before_state"] is None
    assert audit["after_state"] is None


def test_upload_does_not_run_full_verification_inline(
    upload_contract_server,
    upload_contract_application,
    monkeypatch,
):
    import server

    def fail_if_inline_verify(*_args, **_kwargs):
        raise AssertionError("portal upload must not run DocumentVerifyHandler inline")

    monkeypatch.setattr(server.DocumentVerifyHandler, "_post_with_db", fail_if_inline_verify)

    resp = http_requests.post(
        f"{upload_contract_server}/api/applications/{APPLICATION_ID}/documents?doc_type=cert_inc",
        headers={"Authorization": f"Bearer {upload_contract_application['token']}"},
        files={"file": ("coi.pdf", PDF_BYTES, "application/pdf")},
        timeout=5,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["verification_status"] == "pending"
    assert body["verification_queued"] is True


def test_backoffice_upload_stores_valid_officer_uploaded_by(
    upload_contract_server,
    upload_contract_application,
):
    from auth import create_token
    from db import get_db

    token = create_token("admin001", "admin", "Test Admin", "officer")
    resp = http_requests.post(
        f"{upload_contract_server}/api/applications/{APPLICATION_ID}/documents?doc_type=cert_inc",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("officer-coi.pdf", PDF_BYTES, "application/pdf")},
        timeout=5,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    conn = get_db()
    try:
        doc = conn.execute(
            """
            SELECT uploaded_by, uploaded_by_actor_type, uploaded_by_actor_id,
                   uploaded_by_display, upload_source
            FROM documents
            WHERE id=?
            """,
            (body["id"],),
        ).fetchone()
        job_count = conn.execute(
            "SELECT COUNT(*) AS c FROM verification_jobs WHERE document_id=?",
            (body["id"],),
        ).fetchone()["c"]
    finally:
        conn.close()

    assert dict(doc) == {
        "uploaded_by": "admin001",
        "uploaded_by_actor_type": "user",
        "uploaded_by_actor_id": "admin001",
        "uploaded_by_display": "Test Admin",
        "upload_source": "back_office_upload",
    }
    assert job_count == 0


def test_backoffice_upload_rejects_mismatched_upload_session_app(
    upload_contract_server,
    upload_contract_application,
):
    from auth import create_token
    from db import get_db

    token = create_token("admin001", "admin", "Test Admin", "officer")
    resp = http_requests.post(
        f"{upload_contract_server}/api/applications/{APPLICATION_ID}/documents?doc_type=cert_inc",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "upload_session_app_id": "other_application",
            "upload_session_app_ref": "ARF-OTHER-APPLICATION",
        },
        files={"file": ("wrong-app-coi.pdf", PDF_BYTES, "application/pdf")},
        timeout=5,
    )

    assert resp.status_code == 409, resp.text
    assert "Upload cancelled because the active application changed" in resp.text

    conn = get_db()
    try:
        doc_count = conn.execute(
            "SELECT COUNT(*) AS c FROM documents WHERE application_id=? AND doc_name=?",
            (APPLICATION_ID, "wrong-app-coi.pdf"),
        ).fetchone()["c"]
        audit = conn.execute(
            """
            SELECT detail
            FROM audit_log
            WHERE target=? AND action='Upload Rejected: upload_session_app_mismatch'
            ORDER BY id DESC
            LIMIT 1
            """,
            (APPLICATION_REF,),
        ).fetchone()
    finally:
        conn.close()

    assert doc_count == 0
    assert audit is not None
    detail = json.loads(audit["detail"])
    assert detail["reason_code"] == "upload_session_app_mismatch"
    assert detail["response_code"] == 409
    assert detail["doc_type"] == "cert_inc"


def test_portal_upload_persists_checks_after_async_verification_completes(
    upload_contract_server,
    upload_contract_application,
):
    from db import get_db
    from verification_worker import run_once

    upload = http_requests.post(
        f"{upload_contract_server}/api/applications/{APPLICATION_ID}/documents?doc_type=cert_inc",
        headers={"Authorization": f"Bearer {upload_contract_application['token']}"},
        files={"file": ("async-coi.pdf", PDF_BYTES, "application/pdf")},
        timeout=5,
    )
    assert upload.status_code == 201, upload.text
    doc_id = upload.json()["id"]

    def deterministic_executor(_db, job, _worker_id):
        assert job["document_id"] == doc_id
        return {
            "verification_status": "verified",
            "verification_results": {
                "overall": "verified",
                "checks": [
                    {
                        "label": "Entity Name Match",
                        "result": "pass",
                        "message": "Matched expected entity.",
                    }
                ],
            },
        }

    conn = get_db()
    try:
        result = run_once(
            db=conn,
            worker_id="worker-upload-contract",
            verification_executor=deterministic_executor,
        )
    finally:
        conn.close()

    assert result["outcome"] == "succeeded"

    status_resp = http_requests.get(
        f"{upload_contract_server}/api/documents/{doc_id}/verification-status",
        headers={"Authorization": f"Bearer {upload_contract_application['token']}"},
        timeout=5,
    )
    assert status_resp.status_code == 200
    status = status_resp.json()
    assert status["verification_status"] == "verified"
    assert status["verification_success"] is True
    assert status["verification_results"]["checks"][0]["label"] == "Entity Name Match"
    assert status["verification_job"]["status"] == "succeeded"


def test_upload_size_cap_rejects_before_validation(
    upload_contract_server,
    upload_contract_application,
    monkeypatch,
):
    import server

    monkeypatch.setattr(server, "MAX_UPLOAD_MB", 1)
    oversized_pdf = b"%PDF" + (b"x" * (1024 * 1024))

    resp = http_requests.post(
        f"{upload_contract_server}/api/applications/{APPLICATION_ID}/documents?doc_type=passport",
        headers={"Authorization": f"Bearer {upload_contract_application['token']}"},
        files={"file": ("oversized.pdf", oversized_pdf, "application/pdf")},
        timeout=5,
    )

    assert resp.status_code == 400
    assert resp.json() == {"error": "File exceeds 1MB limit"}


def test_gate_03_duplicate_detection_contract(tmp_path):
    from document_verification import run_gate_checks

    file_path = tmp_path / "passport.pdf"
    file_path.write_bytes(PDF_BYTES)
    file_hash = hashlib.sha256(PDF_BYTES).hexdigest()

    duplicate_results = run_gate_checks(
        str(file_path),
        len(PDF_BYTES),
        "application/pdf",
        [file_hash],
    )
    duplicate_gate = next(result for result in duplicate_results if result["id"] == "GATE-03")

    assert {
        "id",
        "label",
        "classification",
        "type",
        "result",
        "message",
        "source",
    } <= set(duplicate_gate.keys())
    assert duplicate_gate["label"] == "Duplicate Detection"
    assert duplicate_gate["classification"] == "rule"
    assert duplicate_gate["type"] == "hash"
    assert duplicate_gate["result"] == "warn"
    assert "already been uploaded" in duplicate_gate["message"]
    assert duplicate_gate["source"] == "rule"

    fresh_results = run_gate_checks(
        str(file_path),
        len(PDF_BYTES),
        "application/pdf",
        [],
    )
    fresh_gate = next(result for result in fresh_results if result["id"] == "GATE-03")
    assert fresh_gate["label"] == "Duplicate Detection"
    assert fresh_gate["type"] == "hash"
    assert fresh_gate["result"] == "pass"
    assert fresh_gate["message"] == "No duplicate detected"
