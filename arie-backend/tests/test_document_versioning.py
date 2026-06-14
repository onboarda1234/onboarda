import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
import tornado.httpserver
import tornado.ioloop


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _sync_db_path(path):
    os.environ["DB_PATH"] = path
    for module_name in ("config", "db", "server"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "DB_PATH"):
            setattr(module, "DB_PATH", path)
        if module_name == "server" and module is not None and hasattr(module, "_CFG_DB_PATH"):
            setattr(module, "_CFG_DB_PATH", path)


def _find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture()
def document_versioning_server(tmp_path):
    db_path = str(tmp_path / "document_versioning.db")
    _sync_db_path(db_path)

    from db import get_db, init_db, seed_initial_data

    init_db()
    conn = get_db()
    seed_initial_data(conn)
    conn.commit()
    conn.close()

    import server as server_module

    server_module.HAS_S3 = False
    app = server_module.make_app()
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


def _client_headers(client_id):
    from auth import create_token

    token = create_token(client_id, "client", "Versioning Client", "client")
    return {"Authorization": f"Bearer {token}"}


def _officer_headers():
    from auth import create_token

    token = create_token("admin001", "admin", "Test Admin", "officer")
    return {"Authorization": f"Bearer {token}"}


def _seed_application(app_id, client_id, status="kyc_documents"):
    from db import get_db

    conn = get_db()
    conn.execute("DELETE FROM audit_log WHERE target = ?", (f"ARF-{app_id}",))
    conn.execute("DELETE FROM documents WHERE application_id = ?", (app_id,))
    conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
    conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    conn.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"{client_id}@example.test", "hash", "Versioning Ltd"),
    )
    conn.execute(
        """INSERT INTO applications
           (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, f"ARF-{app_id}", client_id, "Versioning Ltd", "Mauritius", "Technology", "SME", status, "MEDIUM", 42),
    )
    conn.commit()
    conn.close()


def _upload_document(base_url, app_id, client_id, filename, doc_type="cert_inc", person_id=None, person_type=None):
    query = f"doc_type={doc_type}"
    if person_id is not None:
        query += f"&person_id={person_id}"
    if person_type is not None:
        query += f"&person_type={person_type}"
    response = requests.post(
        f"{base_url}/api/applications/{app_id}/documents?{query}",
        headers=_client_headers(client_id),
        files={"file": (filename, b"%PDF-1.4\n%EOF\n", "application/pdf")},
        timeout=5,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_verification(doc_id, label):
    from db import get_db

    results = {
        "checks": [{"label": label, "result": "pass", "message": f"{label} verified"}],
        "confidence": 0.91,
    }
    conn = get_db()
    conn.execute(
        "UPDATE documents SET verification_status='verified', verification_results=?, verified_at=datetime('now') WHERE id=?",
        (json.dumps(results), doc_id),
    )
    conn.commit()
    conn.close()


def _insert_approved_memo(app_id):
    from db import get_db

    memo_data = {
        "ai_source": "deterministic",
        "metadata": {"ai_source": "deterministic"},
        "supervisor": {"verdict": "CONSISTENT", "can_approve": True},
    }
    conn = get_db()
    conn.execute(
        """
        INSERT INTO compliance_memos
        (application_id, memo_data, review_status, validation_status,
         supervisor_status, quality_score, approved_by, approved_at, approval_reason)
        VALUES (?, ?, 'approved', 'pass', 'CONSISTENT', 9.2, 'admin001', datetime('now'), ?)
        """,
        (app_id, json.dumps(memo_data), "Fixture approval reason"),
    )
    conn.commit()
    conn.close()


def _document_rows(app_id):
    from db import get_db

    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM documents WHERE application_id = ? ORDER BY version, uploaded_at, id",
        (app_id,),
    ).fetchall()]
    conn.close()
    return rows


def test_existing_schema_without_slot_key_can_start_and_repair(tmp_path):
    import sqlite3

    db_path = str(tmp_path / "legacy_documents.db")
    raw = sqlite3.connect(db_path)
    raw.execute(
        """
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            application_id TEXT,
            person_id TEXT,
            doc_type TEXT,
            doc_name TEXT,
            file_path TEXT,
            s3_key TEXT,
            file_size INTEGER,
            mime_type TEXT,
            verification_status TEXT DEFAULT 'pending',
            verification_results TEXT,
            verified_at TEXT,
            review_status TEXT DEFAULT 'pending',
            review_comment TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            uploaded_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    raw.commit()
    raw.close()

    _sync_db_path(db_path)
    from db import get_db, init_db

    init_db()
    conn = get_db()
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
    indexes = {row["name"] for row in conn.execute("PRAGMA index_list(documents)").fetchall()}
    conn.close()

    assert "slot_key" in columns
    assert "is_current" in columns
    assert "version" in columns
    assert "expiry_date" in columns
    assert "valid_until" in columns
    assert "idx_documents_current_slot" in indexes
    assert "idx_documents_one_current_slot" in indexes


def test_repeated_init_keeps_existing_document_rows_and_expiry_columns(tmp_path):
    db_path = str(tmp_path / "repeat_init_documents.db")
    _sync_db_path(db_path)
    from db import get_db, init_db

    init_db()
    conn = get_db()
    conn.execute(
        "INSERT INTO documents (id, application_id, doc_type, doc_name, file_path, expiry_date, valid_until) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("doc-repeat", "app-repeat", "passport", "passport.pdf", "/tmp/passport.pdf", "2030-01-01", "2030-02-01"),
    )
    conn.commit()
    conn.close()

    init_db()
    conn = get_db()
    row = conn.execute(
        "SELECT doc_name, expiry_date, valid_until FROM documents WHERE id = ?",
        ("doc-repeat",),
    ).fetchone()
    conn.close()

    assert row["doc_name"] == "passport.pdf"
    assert row["expiry_date"] == "2030-01-01"
    assert row["valid_until"] == "2030-02-01"


def test_replacement_supersedes_previous_document_and_active_apis_hide_history(document_versioning_server):
    app_id = "docver_app_api"
    client_id = "docver_client_api"
    _seed_application(app_id, client_id)

    doc_a = _upload_document(document_versioning_server, app_id, client_id, "a.pdf")
    _set_verification(doc_a["id"], "A")
    active_after_a = requests.get(
        f"{document_versioning_server}/api/applications/{app_id}/documents",
        headers=_client_headers(client_id),
        timeout=5,
    )
    assert [d["id"] for d in active_after_a.json()] == [doc_a["id"]]

    doc_b = _upload_document(document_versioning_server, app_id, client_id, "b.pdf")
    _set_verification(doc_b["id"], "B")

    rows = {row["id"]: row for row in _document_rows(app_id)}
    assert rows[doc_a["id"]]["is_current"] in (0, False)
    assert rows[doc_a["id"]]["superseded_by_document_id"] == doc_b["id"]
    assert rows[doc_a["id"]]["superseded_at"]
    assert rows[doc_b["id"]]["is_current"] in (1, True)
    assert rows[doc_b["id"]]["version"] == 2

    active_docs = requests.get(
        f"{document_versioning_server}/api/applications/{app_id}/documents",
        headers=_client_headers(client_id),
        timeout=5,
    )
    assert active_docs.status_code == 200, active_docs.text
    assert [d["id"] for d in active_docs.json()] == [doc_b["id"]]
    assert "B verified" in json.dumps(active_docs.json())
    assert "A verified" not in json.dumps(active_docs.json())

    active_detail = requests.get(
        f"{document_versioning_server}/api/applications/{app_id}",
        headers=_officer_headers(),
        timeout=5,
    )
    assert active_detail.status_code == 200, active_detail.text
    assert [d["id"] for d in active_detail.json()["documents"]] == [doc_b["id"]]

    history_detail = requests.get(
        f"{document_versioning_server}/api/applications/{app_id}?include_history=true",
        headers=_officer_headers(),
        timeout=5,
    )
    assert history_detail.status_code == 200, history_detail.text
    history_ids = [d["id"] for d in history_detail.json()["document_history"]]
    assert doc_a["id"] in history_ids
    assert doc_b["id"] in history_ids

    audit_rows = requests.get(
        f"{document_versioning_server}/api/applications/{app_id}/evidence-pack?include_history=true",
        headers=_officer_headers(),
        timeout=5,
    ).json()["audit_log"]["entries"]
    replacement_audits = [r for r in audit_rows if r["action"] == "document.replaced"]
    assert replacement_audits
    detail = replacement_audits[-1]["detail_json"]
    assert detail["old_document_id"] == doc_a["id"]
    assert detail["old_document_ids"] == [doc_a["id"]]
    assert detail["new_document_id"] == doc_b["id"]
    assert detail["application_id"] == app_id


def test_remove_targets_only_current_document_and_keeps_superseded_history(document_versioning_server):
    app_id = "docver_app_delete"
    client_id = "docver_client_delete"
    _seed_application(app_id, client_id)
    doc_a = _upload_document(document_versioning_server, app_id, client_id, "a.pdf")
    doc_b = _upload_document(document_versioning_server, app_id, client_id, "b.pdf")

    response = requests.delete(
        f"{document_versioning_server}/api/applications/{app_id}/documents/{doc_b['id']}",
        headers=_client_headers(client_id),
        timeout=5,
    )
    assert response.status_code == 200, response.text

    rows = {row["id"]: row for row in _document_rows(app_id)}
    assert doc_b["id"] not in rows
    assert doc_a["id"] in rows
    assert rows[doc_a["id"]]["is_current"] in (0, False)

    active_docs = requests.get(
        f"{document_versioning_server}/api/applications/{app_id}/documents",
        headers=_client_headers(client_id),
        timeout=5,
    )
    assert active_docs.json() == []


def test_evidence_pack_and_compliance_memo_use_only_current_documents(document_versioning_server, monkeypatch):
    from tests.conftest import insert_verified_required_documents
    from db import get_db

    app_id = "docver_app_memo"
    client_id = "docver_client_memo"
    _seed_application(app_id, client_id)
    doc_a = _upload_document(document_versioning_server, app_id, client_id, "a.pdf")
    doc_b = _upload_document(document_versioning_server, app_id, client_id, "b.pdf")
    verified_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    conn = get_db()
    conn.execute(
        """
        UPDATE documents
           SET verification_status='verified',
               verification_results=?,
               verified_at=?
         WHERE id=?
        """,
        (
            json.dumps({"overall": "verified", "checks": [{"result": "pass"}], "verified_at": verified_at}),
            verified_at,
            doc_b["id"],
        ),
    )
    conn.execute(
        """
        INSERT INTO agent_executions
        (application_id, document_id, agent_name, agent_number, status, checks_json, requires_review)
        VALUES (?, ?, 'verify_document', 1, 'verified', ?, 0)
        """,
        (app_id, doc_b["id"], json.dumps([{"result": "pass"}])),
    )
    insert_verified_required_documents(conn, app_id)
    conn.close()

    evidence_pack = requests.get(
        f"{document_versioning_server}/api/applications/{app_id}/evidence-pack",
        headers=_officer_headers(),
        timeout=5,
    )
    assert evidence_pack.status_code == 200, evidence_pack.text
    evidence_document_ids = [d["id"] for d in evidence_pack.json()["documents"]]
    assert doc_b["id"] in evidence_document_ids
    assert doc_a["id"] not in evidence_document_ids

    import server as server_module

    captured = {}

    def fake_build_compliance_memo(app, directors, ubos, documents):
        captured["document_ids"] = [d["id"] for d in documents]
        memo = {
            "sections": {"document_verification": {"content": "Active documents only."}},
            "metadata": {"approval_recommendation": "APPROVE", "risk_rating": "MEDIUM", "risk_score": 42},
        }
        return (
            memo,
            {"violations": [], "engine_status": "CLEAN"},
            {"verdict": "PASS", "recommendation": "Proceed"},
            {"quality_score": 9, "validation_status": "pass"},
        )

    monkeypatch.setattr(server_module, "build_compliance_memo", fake_build_compliance_memo)

    memo = requests.post(
        f"{document_versioning_server}/api/applications/{app_id}/memo",
        headers=_officer_headers(),
        timeout=5,
    )
    assert memo.status_code == 200, memo.text
    assert doc_b["id"] in captured["document_ids"]
    assert doc_a["id"] not in captured["document_ids"]


def test_person_type_keeps_same_person_id_doc_type_in_distinct_slots(document_versioning_server):
    app_id = "docver_app_person_type"
    client_id = "docver_client_person_type"
    _seed_application(app_id, client_id)

    from db import get_db

    conn = get_db()
    conn.execute(
        "INSERT INTO directors (id, application_id, person_key, first_name, last_name, full_name) VALUES (?, ?, ?, ?, ?, ?)",
        ("shared_person", app_id, "shared_person", "Director", "Shared", "Director Shared"),
    )
    conn.execute(
        "INSERT INTO ubos (id, application_id, person_key, first_name, last_name, full_name, ownership_pct) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("shared_person", app_id, "shared_person", "UBO", "Shared", "UBO Shared", 25),
    )
    conn.commit()
    conn.close()

    director_doc = _upload_document(
        document_versioning_server,
        app_id,
        client_id,
        "director.pdf",
        doc_type="passport",
        person_id="shared_person",
        person_type="director",
    )
    ubo_doc = _upload_document(
        document_versioning_server,
        app_id,
        client_id,
        "ubo.pdf",
        doc_type="passport",
        person_id="shared_person",
        person_type="ubo",
    )

    assert director_doc["slot_key"] == "person:director:shared_person:passport"
    assert ubo_doc["slot_key"] == "person:ubo:shared_person:passport"

    active_docs = requests.get(
        f"{document_versioning_server}/api/applications/{app_id}/documents",
        headers=_client_headers(client_id),
        timeout=5,
    )
    assert active_docs.status_code == 200, active_docs.text
    active_ids = {d["id"] for d in active_docs.json()}
    assert active_ids == {director_doc["id"], ubo_doc["id"]}


def test_normalized_doc_type_maps_equivalent_labels_to_same_slot_type():
    from server import _document_slot_key, _normalize_document_type

    equivalents = [
        "doc-coi",
        "certificate_of_incorporation",
        "Certificate of Incorporation",
        "incorporation certificate",
    ]
    assert {_normalize_document_type(value) for value in equivalents} == {"cert_inc"}
    assert _normalize_document_type("Proof of Address") == "poa"
    assert _normalize_document_type("Memorandum and Articles") == "memarts"
    assert (
        _document_slot_key("Proof of Address", "person-1", person_type="director")
        == "person:director:person-1:poa"
    )


def test_completeness_check_excludes_superseded_documents(document_versioning_server):
    app_id = "docver_app_completeness"
    client_id = "docver_client_completeness"
    _seed_application(app_id, client_id)
    doc_a = _upload_document(document_versioning_server, app_id, client_id, "a.pdf")
    doc_b = _upload_document(document_versioning_server, app_id, client_id, "b.pdf")

    delete_current = requests.delete(
        f"{document_versioning_server}/api/applications/{app_id}/documents/{doc_b['id']}",
        headers=_client_headers(client_id),
        timeout=5,
    )
    assert delete_current.status_code == 200, delete_current.text

    rows = {row["id"]: row for row in _document_rows(app_id)}
    assert rows[doc_a["id"]]["is_current"] in (0, False)

    submit = requests.post(
        f"{document_versioning_server}/api/applications/{app_id}/submit-kyc",
        headers=_client_headers(client_id),
        timeout=5,
    )
    assert submit.status_code == 400, submit.text
    assert "Please upload at least one document" in submit.text


def test_document_replacement_marks_memo_stale_and_blocks_memo_approval(document_versioning_server):
    app_id = "docver_app_memo_stale"
    client_id = "docver_client_memo_stale"
    _seed_application(app_id, client_id)
    first_doc = _upload_document(document_versioning_server, app_id, client_id, "first.pdf")
    _insert_approved_memo(app_id)

    second_doc = _upload_document(document_versioning_server, app_id, client_id, "replacement.pdf")
    rows = {row["id"]: row for row in _document_rows(app_id)}
    assert rows[first_doc["id"]]["is_current"] in (0, False)
    assert rows[second_doc["id"]]["is_current"] in (1, True)

    from db import get_db

    conn = get_db()
    memo = conn.execute(
        "SELECT is_stale, stale_trigger, review_status, validation_status, supervisor_status, approved_by "
        "FROM compliance_memos WHERE application_id = ? ORDER BY id DESC LIMIT 1",
        (app_id,),
    ).fetchone()
    audit = conn.execute(
        "SELECT detail FROM audit_log WHERE target = ? AND action = 'Memo Marked Stale' ORDER BY id DESC LIMIT 1",
        (f"ARF-{app_id}",),
    ).fetchone()
    conn.close()
    assert memo["is_stale"] in (1, True)
    assert memo["stale_trigger"] == "document_replaced"
    assert memo["review_status"] == "draft"
    assert memo["validation_status"] == "pending"
    assert memo["supervisor_status"] == "pending"
    assert memo["approved_by"] is None
    assert audit is not None
    assert "Document replacement changed memo evidence" in audit["detail"]

    approve = requests.post(
        f"{document_versioning_server}/api/applications/{app_id}/memo/approve",
        headers=_officer_headers(),
        json={
            "officer_signoff": {
                "acknowledged": True,
                "scope": "memo",
                "source_context": "ai_advisory",
            }
        },
        timeout=5,
    )
    assert approve.status_code == 409, approve.text
    assert "stale memo" in approve.text.lower()


def test_approval_gate_excludes_superseded_flagged_documents(tmp_path):
    db_path = str(tmp_path / "approval_gate.db")
    _sync_db_path(db_path)

    from tests.conftest import insert_verified_required_documents
    from db import get_db, init_db
    from security_hardening import ApprovalGateValidator

    init_db()
    app_id = "docver_approval_app"
    now = datetime.now(timezone.utc)
    prescreening_data = {
        "screening_report": {
            "screening_mode": "live",
            "screened_at": now.isoformat(),
            "company_screening": {
                "found": True,
                "source": "opencorporates",
                "sanctions": {"matched": False, "results": [], "source": "sumsub", "api_status": "live"},
            },
            "director_screenings": [
                {
                    "person_name": "Approval Director",
                    "screening": {"matched": False, "results": [], "source": "sumsub", "api_status": "live"},
                }
            ],
            "ubo_screenings": [],
            "kyc_applicants": [
                {"person_name": "Approval Director", "source": "sumsub", "api_status": "live", "review_answer": "GREEN"}
            ],
        },
        "screening_valid_until": (now + timedelta(days=30)).isoformat(),
    }
    memo_data = {
        "metadata": {},
        "supervisor": {},
        "ai_source": "live",
    }
    conn = get_db()
    conn.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        ("docver_approval_client", "approval@example.test", "hash", "Approval Ltd"),
    )
    conn.execute(
        """INSERT INTO applications
           (id, ref, client_id, company_name, country, status, prescreening_data, risk_level, risk_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            app_id,
            "ARF-DOCVER-APPROVAL",
            "docver_approval_client",
            "Approval Ltd",
            "Mauritius",
            "in_review",
            json.dumps(prescreening_data),
            "MEDIUM",
            42,
        ),
    )
    conn.execute(
        """INSERT INTO compliance_memos
           (application_id, memo_data, review_status, validation_status, supervisor_status, quality_score, approval_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (app_id, json.dumps(memo_data), "approved", "pass", "CONSISTENT", 9, "Fixture approval reason"),
    )
    conn.execute(
        """
        INSERT INTO idv_resolutions
        (id, application_id, application_ref, person_id, person_type, person_name,
         prior_provider_status, prior_review_answer, resolution_status, resolution_outcome,
         reason_code, evidence_reviewed, rationale, confirmation_text, senior_approver_id,
         resolved_by, resolved_by_name, resolved_by_role, ip_address, user_agent, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "docver-idv-resolution",
            app_id,
            "ARF-DOCVER-APPROVAL",
            "docver_approval_client",
            "client",
            "Approval Ltd",
            "pending",
            "",
            "manual_verified",
            "manual_verification_completed",
            "other",
            json.dumps(["corporate_documents"]),
            "Manual IDV resolution recorded for document versioning approval fixture.",
            "confirmed",
            "",
            "admin001",
            "Test Admin",
            "admin",
            "127.0.0.1",
            "pytest",
            now.isoformat(),
        ),
    )
    conn.execute(
        """INSERT INTO documents
           (id, application_id, doc_type, doc_name, file_path, slot_key, is_current,
            version, verification_status, superseded_at, superseded_by_document_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)""",
        ("old_flagged", app_id, "cert_inc", "old.pdf", "/tmp/old.pdf", "entity:cert_inc", False, 1, "flagged", "new_verified"),
    )
    conn.execute(
        """INSERT INTO documents
           (id, application_id, doc_type, doc_name, file_path, slot_key, is_current, version,
            verification_status, verification_results, verified_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "new_verified",
            app_id,
            "cert_inc",
            "new.pdf",
            "/tmp/new.pdf",
            "entity:cert_inc",
            True,
            2,
            "verified",
            json.dumps({"overall": "verified", "checks": [{"result": "pass"}], "verified_at": now.isoformat()}),
            now.isoformat(),
        ),
    )
    conn.execute(
        """
        INSERT INTO agent_executions
        (application_id, document_id, agent_name, agent_number, status, checks_json, requires_review)
        VALUES (?, 'new_verified', 'verify_document', 1, 'verified', ?, 0)
        """,
        (app_id, json.dumps([{"result": "pass"}])),
    )
    insert_verified_required_documents(conn, app_id)
    conn.commit()

    app = dict(conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())
    allowed, message = ApprovalGateValidator.validate_approval(app, conn)
    conn.close()

    assert allowed is True, message


def test_repair_marks_latest_duplicate_per_slot_current_and_preserves_people(tmp_path):
    db_path = str(tmp_path / "repair.db")
    _sync_db_path(db_path)

    from db import get_db, init_db, repair_document_current_versions

    init_db()
    app_id = "docver_repair_app"
    conn = get_db()
    conn.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        ("docver_repair_client", "repair@example.test", "hash", "Repair Ltd"),
    )
    conn.execute(
        "INSERT INTO applications (id, ref, client_id, company_name, country, status) VALUES (?, ?, ?, ?, ?, ?)",
        (app_id, "ARF-DOCVER-REPAIR", "docver_repair_client", "Repair Ltd", "Mauritius", "draft"),
    )
    conn.execute("DROP INDEX IF EXISTS idx_documents_one_current_slot")
    docs = [
        ("dup_old", None, "cert_inc", "old.pdf", "2026-01-01 00:00:00"),
        ("dup_new", None, "cert_inc", "new.pdf", "2026-01-02 00:00:00"),
        ("dir1_passport", "dir1", "passport", "dir1.pdf", "2026-01-01 00:00:00"),
        ("dir2_passport", "dir2", "passport", "dir2.pdf", "2026-01-01 00:00:00"),
        ("ubo1_passport", "ubo1", "passport", "ubo1.pdf", "2026-01-01 00:00:00"),
        ("legacy_poa", None, "Proof of Address", "poa.pdf", "2026-01-01 00:00:00"),
    ]
    for doc_id, person_id, doc_type, name, uploaded_at in docs:
        conn.execute(
            """INSERT INTO documents
               (id, application_id, person_id, doc_type, doc_name, file_path, uploaded_at, is_current)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, app_id, person_id, doc_type, name, f"/tmp/{name}", uploaded_at, True),
        )
    conn.commit()

    repair_document_current_versions(conn)
    conn.commit()
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM documents WHERE application_id = ?", (app_id,)).fetchall()}
    conn.close()

    assert rows["dup_old"]["is_current"] in (0, False)
    assert rows["dup_old"]["superseded_by_document_id"] == "dup_new"
    assert rows["dup_new"]["is_current"] in (1, True)
    assert rows["dup_new"]["version"] == 2
    assert rows["dir1_passport"]["is_current"] in (1, True)
    assert rows["dir2_passport"]["is_current"] in (1, True)
    assert rows["ubo1_passport"]["is_current"] in (1, True)
    assert rows["dir1_passport"]["slot_key"] == "person:director:dir1:passport"
    assert rows["ubo1_passport"]["slot_key"] == "person:ubo:ubo1:passport"
    assert rows["legacy_poa"]["doc_type"] == "poa"
    assert rows["legacy_poa"]["slot_key"] == "entity:poa"
    assert rows["dir1_passport"]["slot_key"] != rows["dir2_passport"]["slot_key"]
    assert rows["dir1_passport"]["slot_key"] != rows["ubo1_passport"]["slot_key"]


def test_partial_unique_index_blocks_two_current_documents_in_same_slot(tmp_path):
    db_path = str(tmp_path / "unique_slot.db")
    _sync_db_path(db_path)

    from db import get_db, init_db

    init_db()
    app_id = "docver_unique_app"
    conn = get_db()
    conn.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        ("docver_unique_client", "unique@example.test", "hash", "Unique Ltd"),
    )
    conn.execute(
        "INSERT INTO applications (id, ref, client_id, company_name, country, status) VALUES (?, ?, ?, ?, ?, ?)",
        (app_id, "ARF-DOCVER-UNIQUE", "docver_unique_client", "Unique Ltd", "Mauritius", "draft"),
    )
    conn.execute(
        """INSERT INTO documents
           (id, application_id, doc_type, doc_name, file_path, slot_key, is_current, version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("current_one", app_id, "cert_inc", "one.pdf", "/tmp/one.pdf", "entity:cert_inc", True, 1),
    )

    with pytest.raises(Exception):
        conn.execute(
            """INSERT INTO documents
               (id, application_id, doc_type, doc_name, file_path, slot_key, is_current, version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("current_two", app_id, "cert_inc", "two.pdf", "/tmp/two.pdf", "entity:cert_inc", True, 2),
        )
    conn.close()


def test_portal_slots_clear_and_track_only_current_document_source():
    portal = Path(__file__).resolve().parents[2] / "arie-portal.html"
    source = portal.read_text(encoding="utf-8")

    assert "function clearVerificationForSlot" in source
    assert "card.setAttribute('data-doc-slot', slotKey)" in source
    assert "function getKYCPersonType" in source
    assert "&person_type=" in source
    assert "uploadedDocIds[docId] = latest && latest.doc_id ? [latest.doc_id] : [];" in source
    assert "uploadedDocIds[slotId] = [doc.id];" in source
    assert "Duplicate current documents returned for upload slot" in source
