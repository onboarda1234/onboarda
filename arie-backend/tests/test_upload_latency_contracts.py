"""
Upload latency contract guards.

These tests intentionally lock existing upload-facing behavior before the
latency refactor work starts. They should fail if a later change quietly alters
the upload response shape, audit trail, size cap behavior, or duplicate gate.
"""
import hashlib
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

    io_loop = server_ref.get("loop")
    srv = server_ref.get("server")
    if io_loop and srv:
        io_loop.add_callback(srv.stop)
        io_loop.add_callback(io_loop.stop)
    thread.join(timeout=2)


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
    assert set(body.keys()) == {"id", "doc_name", "doc_type", "file_size", "s3_key"}
    assert body["doc_name"] == "passport.pdf"
    assert body["doc_type"] == "passport"
    assert body["file_size"] == len(PDF_BYTES)
    assert body["s3_key"] is None

    from db import get_db

    conn = get_db()
    doc = conn.execute(
        """
        SELECT id, application_id, doc_type, doc_name, file_size, mime_type,
               verification_status, review_status
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
        "verification_status": "pending",
        "review_status": "pending",
    }

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
    assert audit["user_name"] == "Upload Contract Client"
    assert audit["user_role"] == "client"
    assert audit["action"] == "Upload"
    assert audit["target"] == APPLICATION_REF
    assert audit["detail"] == "Document uploaded: passport.pdf (passport)"
    assert audit["ip_address"] == "127.0.0.1"
    assert audit["before_state"] is None
    assert audit["after_state"] is None


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
