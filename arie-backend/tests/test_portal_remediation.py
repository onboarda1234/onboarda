import json
import os
import socket
import tempfile
import threading
import time
from datetime import datetime, timezone

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


@pytest.fixture(scope="module")
def api_server():
    db_path = os.path.join(tempfile.gettempdir(), f"onboarda_test_{os.getpid()}.db")
    os.environ["DB_PATH"] = db_path
    try:
        os.unlink(db_path)
    except OSError:
        pass

    import db as db_module
    db_module.DB_PATH = db_path
    from db import get_db, init_db, seed_initial_data

    init_db()
    try:
        conn = get_db()
        seed_initial_data(conn)
        conn.commit()
        conn.close()
    except Exception as exc:
        raise RuntimeError(f"Failed to seed remediation test database: {exc}") from exc

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

    from tests.conftest import shutdown_test_http_server
    shutdown_test_http_server(thread, server_ref)
    try:
        os.unlink(db_path)
    except OSError:
        pass


def _ensure_client(db, client_id="portalclient001", email="portal@test.com"):
    import bcrypt

    db.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, email, bcrypt.hashpw("TestPass123!".encode(), bcrypt.gensalt()).decode(), "Portal Test Co"),
    )
    db.commit()


def _portal_client_token(client_id="portalclient001"):
    from auth import create_token

    return create_token(client_id, "client", "Portal Client", "client")


def _officer_token():
    from auth import create_token

    return create_token("admin001", "admin", "Test Admin", "officer")


def _seed_kyc_application(
    db,
    *,
    app_id,
    ref,
    status="kyc_documents",
    risk_level="MEDIUM",
    risk_score=45,
    onboarding_lane="STANDARD",
    pre_approval_decision=None,
    sector="Technology Services",
    director_is_pep="No",
):
    _ensure_client(db)
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, status, risk_level, risk_score,
         onboarding_lane, pre_approval_decision, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            ref,
            "portalclient001",
            f"{ref} Ltd",
            "Mauritius",
            sector,
            status,
            risk_level,
            risk_score,
            onboarding_lane,
            pre_approval_decision,
            json.dumps({
                "registered_entity_name": f"{ref} Ltd",
                "country_of_incorporation": "Mauritius",
                "sector": sector,
            }),
        ),
    )
    db.execute(
        """
        INSERT INTO directors
        (id, application_id, person_key, first_name, last_name, full_name, nationality,
         is_pep, pep_declaration, date_of_birth)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"dir_{app_id}",
            app_id,
            "dir1",
            "Jane",
            "Director",
            "Jane Director",
            "Mauritius",
            director_is_pep,
            "{}",
            "1985-01-01",
        ),
    )


def _mark_edd_preapproval_required(
    db,
    app_id,
    *,
    final_risk_level="MEDIUM",
    triggers=None,
    reason="EDD routing requires pre-approval before KYC.",
):
    db.execute(
        """
        UPDATE applications
        SET final_risk_level=?,
            onboarding_lane='EDD',
            risk_escalations=?,
            elevation_reason_text=?
        WHERE id=?
        """,
        (
            final_risk_level,
            json.dumps(triggers or ["declared_pep_present"]),
            reason,
            app_id,
        ),
    )


def _cleanup_application(db, app_id, ref):
    for table, column in (
        ("documents", "application_id"),
        ("application_enhanced_requirements", "application_id"),
        ("compliance_memos", "application_id"),
        ("edd_cases", "application_id"),
        ("directors", "application_id"),
        ("ubos", "application_id"),
        ("applications", "id"),
    ):
        db.execute(f"DELETE FROM {table} WHERE {column}=?", (app_id,))
    db.execute("DELETE FROM audit_log WHERE target=?", (ref,))
    db.execute("DELETE FROM audit_log WHERE target=?", (f"application:{ref}",))


def _insert_uploaded_document(db, app_id, *, doc_id=None, doc_type="cert_inc",
                              person_id=None, person_type=None,
                              verification_status="flagged"):
    from server import _document_slot_key

    temp_dir = tempfile.mkdtemp(prefix="portal-submit-")
    suffix = str(person_id or "entity").replace(":", "_")
    file_path = os.path.join(temp_dir, f"{app_id}_{doc_type}_{suffix}.pdf")
    with open(file_path, "wb") as handle:
        handle.write(b"%PDF-1.4\n%EOF\n")
    slot_key = _document_slot_key(doc_type, person_id, person_type=person_type)
    final_doc_id = doc_id or f"doc_{app_id}_{doc_type}_{suffix}"
    verified_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    results_payload = {
        "overall": verification_status,
        "checks": [{"result": "pass"}] if verification_status == "verified" else [],
        "verified_at": verified_at if verification_status == "verified" else None,
    }
    db.execute(
        """
        INSERT INTO documents
        (id, application_id, person_id, person_type, doc_type, doc_name,
         file_path, slot_key, is_current, verification_status,
         verification_results, verified_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            final_doc_id,
            app_id,
            person_id,
            person_type,
            doc_type,
            os.path.basename(file_path),
            file_path,
            slot_key,
            True,
            verification_status,
            json.dumps(results_payload),
            verified_at if verification_status == "verified" else None,
        ),
    )
    if verification_status == "verified":
        db.execute(
            """
            INSERT INTO agent_executions
            (application_id, document_id, agent_name, agent_number, status, checks_json, requires_review)
            VALUES (?, ?, 'verify_document', 1, 'verified', ?, 0)
            """,
            (app_id, final_doc_id, json.dumps([{"result": "pass"}])),
        )


def _ensure_verified_document(db, app_id, *, doc_type, person_id=None, person_type=None):
    from server import _document_slot_key

    slot_key = _document_slot_key(doc_type, person_id, person_type=person_type)
    existing = db.execute(
        "SELECT id FROM documents WHERE application_id=? AND slot_key=? AND COALESCE(is_current, TRUE)=TRUE",
        (app_id, slot_key),
    ).fetchone()
    if existing:
        db.execute(
            "UPDATE documents SET person_type=?, verification_status=?, "
            "verification_results=?, verified_at=datetime('now') WHERE id=?",
            (
                person_type,
                "verified",
                json.dumps({
                    "overall": "verified",
                    "checks": [{"result": "pass"}],
                    "verified_at": datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    ),
                }),
                existing["id"],
            ),
        )
        db.execute(
            """
            INSERT INTO agent_executions
            (application_id, document_id, agent_name, agent_number, status, checks_json, requires_review)
            VALUES (?, ?, 'verify_document', 1, 'verified', ?, 0)
            """,
            (app_id, existing["id"], json.dumps([{"result": "pass"}])),
        )
        return
    _insert_uploaded_document(
        db,
        app_id,
        doc_type=doc_type,
        person_id=person_id,
        person_type=person_type,
        verification_status="verified",
    )


def _ensure_verified_required_documents(db, app_id, *, include_bankref=False):
    for doc_type in (
        "cert_inc",
        "memarts",
        "reg_sh",
        "reg_dir",
        "fin_stmt",
        "poa",
        "board_res",
        "structure_chart",
    ):
        _ensure_verified_document(db, app_id, doc_type=doc_type)
    if include_bankref:
        _ensure_verified_document(db, app_id, doc_type="bankref")
    _ensure_verified_document(db, app_id, doc_type="passport", person_id="dir1", person_type="director")
    _ensure_verified_document(db, app_id, doc_type="poa", person_id="dir1", person_type="director")


def test_application_detail_ignores_other_application_saved_session(api_server):
    from auth import create_token
    from db import get_db

    conn = get_db()
    _ensure_client(conn)
    conn.execute(
        """
        INSERT INTO applications (id, ref, client_id, company_name, country, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("draft_a", "ARF-DRAFT-A", "portalclient001", "Draft A Ltd", "Mauritius", "draft", json.dumps({
            "registered_entity_name": "Draft A Ltd",
            "country_of_incorporation": "Mauritius"
        })),
    )
    conn.execute(
        """
        INSERT INTO applications (id, ref, client_id, company_name, country, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("draft_b", "ARF-DRAFT-B", "portalclient001", "Draft B Ltd", "Singapore", "draft", json.dumps({
            "registered_entity_name": "Draft B Ltd",
            "country_of_incorporation": "Singapore"
        })),
    )
    conn.execute(
        """
        INSERT INTO client_sessions (client_id, application_id, form_data, last_step)
        VALUES (?, ?, ?, ?)
        """,
        ("portalclient001", "draft_a", json.dumps({
            "prescreening": {
                "f-contact-first": "Leaked",
                "f-contact-last": "User",
                "f-email": "leaked@example.com"
            }
        }), 1),
    )
    conn.commit()
    conn.close()

    token = create_token("portalclient001", "client", "Portal Client", "client")
    resp = http_requests.get(
        f"{api_server}/api/applications/ARF-DRAFT-B",
        headers={"Authorization": f"Bearer {token}"},
        timeout=3,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["prescreening_data"]["registered_entity_name"] == "Draft B Ltd"
    assert data["prescreening_data"].get("entity_contact_first") in ("", None)
    assert data["prescreening_data"].get("entity_contact_email") in ("", None)


def test_client_can_delete_draft_application(api_server):
    from auth import create_token
    from db import get_db

    conn = get_db()
    _ensure_client(conn)
    conn.execute(
        """
        INSERT INTO applications (id, ref, client_id, company_name, country, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("delete_draft", "ARF-DELETE-DRAFT", "portalclient001", "Delete Me Ltd", "Mauritius", "draft"),
    )
    conn.execute(
        """
        INSERT INTO client_sessions (client_id, application_id, form_data, last_step)
        VALUES (?, ?, ?, ?)
        """,
        ("portalclient001", "delete_draft", json.dumps({"prescreening": {"f-reg-name": "Delete Me Ltd"}}), 0),
    )
    conn.commit()
    conn.close()

    token = create_token("portalclient001", "client", "Portal Client", "client")
    resp = http_requests.delete(
        f"{api_server}/api/applications/ARF-DELETE-DRAFT",
        headers={"Authorization": f"Bearer {token}"},
        timeout=3,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "deleted"

    conn = get_db()
    assert conn.execute("SELECT id FROM applications WHERE id=?", ("delete_draft",)).fetchone() is None
    assert conn.execute("SELECT id FROM client_sessions WHERE application_id=?", ("delete_draft",)).fetchone() is None
    conn.close()


def test_client_delete_refuses_regulated_children_without_partial_artifact_cleanup(api_server, monkeypatch):
    from auth import create_token
    from db import get_db
    import server as server_module

    s3_delete_calls = []

    class _FakeS3:
        def delete_document(self, key):
            s3_delete_calls.append(key)
            return True, "deleted"

    monkeypatch.setattr(server_module, "HAS_S3", True)
    monkeypatch.setattr(server_module, "get_s3_client", lambda: _FakeS3())

    temp_dir = tempfile.mkdtemp(prefix="portal-delete-")
    file_path = os.path.join(temp_dir, "draft-doc.pdf")
    with open(file_path, "wb") as handle:
        handle.write(b"draft document")

    conn = get_db()
    _ensure_client(conn)
    conn.execute(
        """
        INSERT INTO applications (id, ref, client_id, company_name, country, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("delete_children", "ARF-DELETE-CHILDREN", "portalclient001", "Delete Children Ltd", "Mauritius", "draft"),
    )
    conn.execute(
        """
        INSERT INTO documents (id, application_id, doc_type, doc_name, file_path, s3_key)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("doc_delete_children", "delete_children", "cert_inc", "draft-doc.pdf", file_path, "fixtures/draft-doc.pdf"),
    )
    conn.execute(
        """
        INSERT INTO client_sessions (client_id, application_id, form_data, last_step)
        VALUES (?, ?, ?, ?)
        """,
        ("portalclient001", "delete_children", json.dumps({"prescreening": {"f-reg-name": "Delete Children Ltd"}}), 0),
    )
    conn.execute(
        """
        INSERT INTO client_notifications (application_id, client_id, notification_type, title, message)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("delete_children", "portalclient001", "info", "Draft notice", "Draft cleanup"),
    )
    conn.execute(
        """
        INSERT INTO periodic_reviews (application_id, client_name, status)
        VALUES (?, ?, ?)
        """,
        ("delete_children", "Delete Children Ltd", "pending"),
    )
    conn.execute(
        "UPDATE documents SET verification_status='verified', verification_results=? WHERE id=?",
        (json.dumps({"overall": "verified"}), "doc_delete_children"),
    )
    conn.execute(
        "INSERT INTO compliance_memos (application_id, memo_data) VALUES (?, ?)",
        ("delete_children", json.dumps({"summary": "regulated memo evidence"})),
    )
    conn.execute(
        "INSERT INTO screening_reviews (application_id, subject_type, subject_name, disposition) "
        "VALUES (?,?,?,?)",
        ("delete_children", "entity", "Delete Children Ltd", "cleared"),
    )
    conn.execute(
        "INSERT INTO decision_records (id, application_ref, decision_type, source, timestamp) "
        "VALUES (?,?,?,?,?)",
        ("decision-delete-children", "ARF-DELETE-CHILDREN", "request_documents", "manual", "2026-01-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO audit_log (user_id, user_name, user_role, action, target, application_id, detail) "
        "VALUES (?,?,?,?,?,?,?)",
        ("admin001", "Test Admin", "admin", "Evidence Recorded", "ARF-DELETE-CHILDREN", "delete_children", "regulated audit evidence"),
    )
    conn.commit()
    conn.close()

    token = create_token("portalclient001", "client", "Portal Client", "client")
    resp = http_requests.delete(
        f"{api_server}/api/applications/ARF-DELETE-CHILDREN",
        headers={"Authorization": f"Bearer {token}"},
        timeout=3,
    )
    assert resp.status_code == 409
    assert "regulated compliance evidence exists" in resp.json()["error"]

    conn = get_db()
    assert conn.execute("SELECT id FROM applications WHERE id=?", ("delete_children",)).fetchone() is not None
    assert conn.execute("SELECT id FROM documents WHERE application_id=?", ("delete_children",)).fetchone() is not None
    assert conn.execute("SELECT id FROM client_sessions WHERE application_id=?", ("delete_children",)).fetchone() is not None
    assert conn.execute("SELECT id FROM client_notifications WHERE application_id=?", ("delete_children",)).fetchone() is not None
    assert conn.execute("SELECT id FROM periodic_reviews WHERE application_id=?", ("delete_children",)).fetchone() is not None
    assert conn.execute("SELECT id FROM compliance_memos WHERE application_id=?", ("delete_children",)).fetchone() is not None
    assert conn.execute("SELECT id FROM screening_reviews WHERE application_id=?", ("delete_children",)).fetchone() is not None
    assert conn.execute("SELECT id FROM decision_records WHERE application_ref=?", ("ARF-DELETE-CHILDREN",)).fetchone() is not None
    denial = conn.execute(
        "SELECT detail FROM audit_log WHERE application_id=? AND action='Regulated Delete Denied'",
        ("delete_children",),
    ).fetchone()
    assert denial is not None
    assert json.loads(denial["detail"])["event"] == "regulated_delete_denied"
    assert os.path.exists(file_path)
    assert s3_delete_calls == []

    # Isolated test teardown only; production code cannot use this bypass.
    conn.execute("DELETE FROM decision_records WHERE application_ref=?", ("ARF-DELETE-CHILDREN",))
    conn.execute("DELETE FROM screening_reviews WHERE application_id=?", ("delete_children",))
    conn.execute("DELETE FROM compliance_memos WHERE application_id=?", ("delete_children",))
    conn.execute("DELETE FROM periodic_reviews WHERE application_id=?", ("delete_children",))
    conn.execute("DELETE FROM audit_log WHERE application_id=?", ("delete_children",))
    conn.execute("DELETE FROM client_notifications WHERE application_id=?", ("delete_children",))
    conn.execute("DELETE FROM client_sessions WHERE application_id=?", ("delete_children",))
    conn.execute("DELETE FROM documents WHERE application_id=?", ("delete_children",))
    conn.execute("DELETE FROM applications WHERE id=?", ("delete_children",))
    conn.commit()
    conn.close()
    os.unlink(file_path)


def test_client_cannot_delete_submitted_application(api_server):
    from auth import create_token
    from db import get_db

    conn = get_db()
    _ensure_client(conn)
    conn.execute(
        """
        INSERT INTO applications (id, ref, client_id, company_name, country, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("delete_blocked", "ARF-DELETE-BLOCKED", "portalclient001", "Keep Me Ltd", "Mauritius", "pricing_review"),
    )
    conn.commit()
    conn.close()

    token = create_token("portalclient001", "client", "Portal Client", "client")
    resp = http_requests.delete(
        f"{api_server}/api/applications/ARF-DELETE-BLOCKED",
        headers={"Authorization": f"Bearer {token}"},
        timeout=3,
    )
    assert resp.status_code == 403


def test_portal_new_application_bootstrap_is_explicit():
    portal_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
    with open(portal_path, "r", encoding="utf-8") as handle:
        src = handle.read()

    assert "function startNewApplication()" in src
    assert 'onclick="startNewApplication()"' in src


def test_portal_license_toggle_and_review_summary_cleanup_are_present():
    portal_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
    with open(portal_path, "r", encoding="utf-8") as handle:
        src = handle.read()

    assert 'id="f-is-licensed"' in src
    assert 'id="licence-fields-group"' in src
    assert 'id="review-ai-summary"' not in src
    assert 'id="review-submit-note"' in src
    assert "btn.textContent = 'Resolve Required Verification';" in src
    assert "Submission Blocked — Verification Required" in src
    assert "Verification Gate Active" in src


def test_backoffice_incomplete_submission_banner_is_present():
    backoffice_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-backoffice.html")
    with open(backoffice_path, "r", encoding="utf-8") as handle:
        src = handle.read()

    assert "function computeDocumentReadinessSummary(app)" in src
    assert "⚠ Incomplete / Warning-State" in src
    assert "This case is reviewable but should not be treated as clean." in src


def test_backoffice_workflow_test_evidence_ui_is_staging_only_and_truthful():
    backoffice_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-backoffice.html")
    with open(backoffice_path, "r", encoding="utf-8") as handle:
        src = handle.read()

    assert "function canAcceptWorkflowTestEvidence()" in src
    assert "APP_ENV === 'staging'" in src
    assert "['admin', 'sco']" in src
    assert "Accept for workflow testing only" in src
    assert "Accept linked synthetic evidence for workflow only" in src
    assert "Verification remains" in src
    assert "does not count as approval proof" in src


def test_kyc_submit_blocks_unverified_required_documents(api_server):
    from db import get_db

    conn = get_db()
    _seed_kyc_application(
        conn,
        app_id="submit_incomplete",
        ref="ARF-SUBMIT-INCOMPLETE",
        status="kyc_documents",
    )
    _insert_uploaded_document(conn, "submit_incomplete", doc_id="doc_submit_incomplete")
    conn.commit()
    conn.close()

    token = _portal_client_token()
    resp = http_requests.post(
        f"{api_server}/api/applications/ARF-SUBMIT-INCOMPLETE/submit-kyc",
        headers={"Authorization": f"Bearer {token}"},
        timeout=3,
    )
    assert resp.status_code == 400
    assert "required kyc documents" in resp.json()["error"].lower()

    conn = get_db()
    row = conn.execute("SELECT status FROM applications WHERE id=?", ("submit_incomplete",)).fetchone()
    audit = conn.execute(
        "SELECT action, detail FROM audit_log WHERE target=? ORDER BY timestamp DESC, id DESC LIMIT 1",
        ("ARF-SUBMIT-INCOMPLETE",),
    ).fetchone()
    conn.close()
    assert row["status"] == "kyc_documents"
    assert audit["action"] == "KYC Transition Blocked: required_documents_not_verified"
    assert json.loads(audit["detail"])["reason_code"] == "required_documents_not_verified"


@pytest.mark.parametrize("verification_status", ["pending", "in_progress", "flagged", "failed", "skipped"])
def test_kyc_submit_refuses_each_non_verified_required_document_state(api_server, verification_status):
    from db import get_db

    app_id = f"submit_state_{verification_status}"
    ref = f"ARF-SUBMIT-{verification_status.replace('_', '-').upper()}"
    conn = get_db()
    _seed_kyc_application(conn, app_id=app_id, ref=ref, status="kyc_documents")
    _ensure_verified_required_documents(conn, app_id)
    conn.execute(
        "UPDATE documents SET verification_status=? WHERE application_id=? AND doc_type='cert_inc'",
        (verification_status, app_id),
    )
    conn.commit()
    conn.close()

    resp = http_requests.post(
        f"{api_server}/api/applications/{ref}/submit-kyc",
        headers={"Authorization": f"Bearer {_portal_client_token()}"},
        timeout=3,
    )

    assert resp.status_code == 400
    assert "not verified: 1" in resp.json()["error"].lower()


def test_staging_workflow_test_acceptance_remains_blocked_for_kyc_reliance(api_server, monkeypatch):
    from auth import create_token
    from db import get_db
    import server as server_module

    monkeypatch.setattr(server_module, "ENVIRONMENT", "staging")
    app_id = "submit_workflow_test_accept"
    ref = "ARF-SUBMIT-WORKFLOW-TEST"
    conn = get_db()
    _cleanup_application(conn, app_id, ref)
    _seed_kyc_application(conn, app_id=app_id, ref=ref, status="kyc_documents", risk_level="LOW", risk_score=12)
    _ensure_verified_required_documents(conn, app_id)
    conn.execute(
        """
        UPDATE documents
        SET verification_status='flagged',
            verification_results=?,
            evidence_class='test_only_synthetic'
        WHERE application_id=? AND doc_type='cert_inc'
        """,
        (json.dumps({"overall": "flagged", "checks": [{"result": "warn"}]}), app_id),
    )
    doc = conn.execute(
        "SELECT id FROM documents WHERE application_id=? AND doc_type='cert_inc'",
        (app_id,),
    ).fetchone()
    conn.commit()
    conn.close()

    admin_token = create_token("admin001", "admin", "Test Admin", "officer")
    accept_resp = http_requests.post(
        f"{api_server}/api/documents/{doc['id']}/workflow-test-acceptance",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"reason": "Synthetic staging pack used for workflow mechanics only."},
        timeout=3,
    )
    assert accept_resp.status_code == 200, accept_resp.text
    assert accept_resp.json()["document"]["verification_status"] == "flagged"

    detail_resp = http_requests.get(
        f"{api_server}/api/applications/{ref}",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=3,
    )
    assert detail_resp.status_code == 200, detail_resp.text
    summary = detail_resp.json()["pilot_evidence_summary"]
    assert summary["pilot_evidence_classification"] == "workflow_only"
    assert summary["can_count_as_pilot_approval_proof"] is False
    assert summary["workflow_test_accepted_required_count"] == 1

    submit_resp = http_requests.post(
        f"{api_server}/api/applications/{ref}/submit-kyc",
        headers={"Authorization": f"Bearer {_portal_client_token()}"},
        timeout=3,
    )
    assert submit_resp.status_code == 400, submit_resp.text
    submit_body = submit_resp.json()
    assert submit_body["kyc_verification_blocked"] is True
    assert submit_body["document_evidence_gate"]["passed"] is False
    assert submit_body["document_evidence_gate"]["blocker_count"] >= 1

    conn = get_db()
    stored = conn.execute(
        """
        SELECT verification_status, workflow_test_accepted, workflow_test_acceptance_reason
        FROM documents
        WHERE id=?
        """,
        (doc["id"],),
    ).fetchone()
    audit = conn.execute(
        "SELECT action, detail FROM audit_log WHERE target=? AND action='Workflow Test Evidence Accepted' ORDER BY id DESC LIMIT 1",
        (ref,),
    ).fetchone()
    conn.close()
    assert stored["verification_status"] == "flagged"
    assert stored["workflow_test_accepted"] in (1, True)
    assert "workflow mechanics" in stored["workflow_test_acceptance_reason"]
    assert audit is not None
    audit_detail = json.loads(audit["detail"])
    assert audit_detail["workflow_only"] is True


def test_memo_generation_blocks_pending_required_document(api_server):
    from db import get_db

    app_id = "memo_doc_gate_pending"
    ref = "ARF-MEMO-DOC-GATE-PENDING"
    conn = get_db()
    _cleanup_application(conn, app_id, ref)
    _seed_kyc_application(conn, app_id=app_id, ref=ref, status="compliance_review")
    _ensure_verified_required_documents(conn, app_id)
    conn.execute(
        """
        UPDATE documents
        SET verification_status='pending',
            verification_results='{}',
            verified_at=NULL
        WHERE application_id=? AND doc_type='cert_inc'
        """,
        (app_id,),
    )
    conn.commit()
    conn.close()

    resp = http_requests.post(
        f"{api_server}/api/applications/{ref}/memo",
        headers={"Authorization": f"Bearer {_officer_token()}"},
        timeout=3,
    )

    assert resp.status_code == 409
    body = resp.json()
    assert body["memo_reliance_status"] == "blocked"
    assert body["document_evidence_gate"]["passed"] is False
    assert any(
        blocker["code"] == "document_pending_verification"
        for blocker in body["document_evidence_gate"]["blockers"]
    )


def test_memo_approval_blocks_pending_required_document(api_server):
    from db import get_db

    app_id = "memo_approval_doc_gate_pending"
    ref = "ARF-MEMO-APPROVAL-DOC-GATE-PENDING"
    conn = get_db()
    _cleanup_application(conn, app_id, ref)
    _seed_kyc_application(conn, app_id=app_id, ref=ref, status="compliance_review")
    _ensure_verified_required_documents(conn, app_id)
    conn.execute(
        """
        UPDATE documents
        SET verification_status='pending',
            verification_results='{}',
            verified_at=NULL
        WHERE application_id=? AND doc_type='cert_inc'
        """,
        (app_id,),
    )
    conn.execute(
        """
        INSERT INTO compliance_memos
        (application_id, memo_data, generated_by, ai_recommendation, review_status,
         quality_score, validation_status, supervisor_status)
        VALUES (?, ?, 'system', 'APPROVE_WITH_CONDITIONS', 'draft', 9.0, 'pass', 'CONSISTENT')
        """,
        (
            app_id,
            json.dumps({
                "ai_source": "deterministic",
                "metadata": {"ai_source": "deterministic"},
                "supervisor": {"verdict": "CONSISTENT", "can_approve": True},
            }),
        ),
    )
    conn.commit()
    conn.close()

    resp = http_requests.post(
        f"{api_server}/api/applications/{ref}/memo/approve",
        headers={"Authorization": f"Bearer {_officer_token()}"},
        json={
            "approval_reason": "All memo findings reviewed for test.",
            "officer_signoff": {
                "acknowledged": True,
                "scope": "memo",
                "source_context": "ai_advisory",
            },
        },
        timeout=3,
    )

    assert resp.status_code == 409
    body = resp.json()
    assert body["memo_reliance_status"] == "blocked"
    assert any(
        blocker["code"] == "document_pending_verification"
        for blocker in body["document_evidence_gate"]["blockers"]
    )


def test_workflow_test_acceptance_is_staging_only(api_server, monkeypatch):
    from auth import create_token
    from db import get_db
    import server as server_module

    monkeypatch.setattr(server_module, "ENVIRONMENT", "production")
    app_id = "workflow_accept_prod_guard"
    ref = "ARF-WORKFLOW-PROD-GUARD"
    conn = get_db()
    _cleanup_application(conn, app_id, ref)
    _seed_kyc_application(conn, app_id=app_id, ref=ref, status="kyc_documents")
    _insert_uploaded_document(conn, app_id, doc_id="doc_workflow_prod_guard", verification_status="flagged")
    conn.execute(
        "UPDATE documents SET evidence_class='test_only_synthetic' WHERE id='doc_workflow_prod_guard'"
    )
    conn.commit()
    conn.close()

    resp = http_requests.post(
        f"{api_server}/api/documents/doc_workflow_prod_guard/workflow-test-acceptance",
        headers={"Authorization": f"Bearer {create_token('admin001', 'admin', 'Test Admin', 'officer')}"},
        json={"reason": "Should not be available outside staging."},
        timeout=3,
    )
    assert resp.status_code == 403


def test_workflow_test_acceptance_requires_admin_sco_and_reason(api_server, monkeypatch):
    from auth import create_token
    from db import get_db
    import server as server_module

    monkeypatch.setattr(server_module, "ENVIRONMENT", "staging")
    app_id = "workflow_accept_rbac"
    ref = "ARF-WORKFLOW-RBAC"
    conn = get_db()
    _cleanup_application(conn, app_id, ref)
    _seed_kyc_application(conn, app_id=app_id, ref=ref, status="kyc_documents")
    _insert_uploaded_document(conn, app_id, doc_id="doc_workflow_rbac", verification_status="flagged")
    conn.execute("UPDATE documents SET evidence_class='test_only_synthetic' WHERE id='doc_workflow_rbac'")
    conn.commit()
    conn.close()

    analyst_resp = http_requests.post(
        f"{api_server}/api/documents/doc_workflow_rbac/workflow-test-acceptance",
        headers={"Authorization": f"Bearer {create_token('analyst001', 'analyst', 'Test Analyst', 'officer')}"},
        json={"reason": "Analyst should not be able to accept synthetic evidence."},
        timeout=3,
    )
    assert analyst_resp.status_code == 403

    missing_reason_resp = http_requests.post(
        f"{api_server}/api/documents/doc_workflow_rbac/workflow-test-acceptance",
        headers={"Authorization": f"Bearer {create_token('sco001', 'sco', 'Test SCO', 'officer')}"},
        json={"reason": "   "},
        timeout=3,
    )
    assert missing_reason_resp.status_code == 400


def test_workflow_test_acceptance_requires_synthetic_class(api_server, monkeypatch):
    from auth import create_token
    from db import get_db
    import server as server_module

    monkeypatch.setattr(server_module, "ENVIRONMENT", "staging")
    app_id = "workflow_accept_real_class"
    ref = "ARF-WORKFLOW-REAL-CLASS"
    conn = get_db()
    _cleanup_application(conn, app_id, ref)
    _seed_kyc_application(conn, app_id=app_id, ref=ref, status="kyc_documents")
    _insert_uploaded_document(conn, app_id, doc_id="doc_workflow_real_class", verification_status="flagged")
    conn.execute("UPDATE documents SET evidence_class='certified_copy' WHERE id='doc_workflow_real_class'")
    conn.commit()
    conn.close()

    resp = http_requests.post(
        f"{api_server}/api/documents/doc_workflow_real_class/workflow-test-acceptance",
        headers={"Authorization": f"Bearer {create_token('admin001', 'admin', 'Test Admin', 'officer')}"},
        json={"reason": "Real evidence classes must not use the synthetic workflow control."},
        timeout=3,
    )
    assert resp.status_code == 400
    assert "test-only synthetic" in resp.json()["error"].lower()


def test_kyc_submit_still_blocks_when_no_documents_uploaded(api_server):
    from db import get_db

    conn = get_db()
    _seed_kyc_application(
        conn,
        app_id="submit_no_docs",
        ref="ARF-SUBMIT-NO-DOCS",
        status="kyc_documents",
    )
    conn.commit()
    conn.close()

    token = _portal_client_token()
    resp = http_requests.post(
        f"{api_server}/api/applications/ARF-SUBMIT-NO-DOCS/submit-kyc",
        headers={"Authorization": f"Bearer {token}"},
        timeout=3,
    )
    assert resp.status_code == 400
    assert "upload at least one document" in resp.json()["error"].lower()

    conn = get_db()
    audit = conn.execute(
        "SELECT action, detail FROM audit_log WHERE target=? ORDER BY timestamp DESC, id DESC LIMIT 1",
        ("ARF-SUBMIT-NO-DOCS",),
    ).fetchone()
    conn.close()
    assert audit["action"] == "KYC Transition Blocked: missing_required_documents"
    assert json.loads(audit["detail"])["reason_code"] == "missing_required_documents"


def test_kyc_upload_from_pricing_review_is_blocked_and_audited(api_server):
    from db import get_db

    conn = get_db()
    _seed_kyc_application(
        conn,
        app_id="upload_pricing_review",
        ref="ARF-UPLOAD-PRICING-REVIEW",
        status="pricing_review",
    )
    conn.commit()
    conn.close()

    resp = http_requests.post(
        f"{api_server}/api/applications/ARF-UPLOAD-PRICING-REVIEW/documents?doc_type=cert_inc",
        headers={"Authorization": f"Bearer {_portal_client_token()}"},
        files={"file": ("coi.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        timeout=3,
    )
    assert resp.status_code == 409
    assert "pricing" in resp.json()["error"].lower()

    conn = get_db()
    audit = conn.execute(
        "SELECT action, detail FROM audit_log WHERE target=? ORDER BY timestamp DESC, id DESC LIMIT 1",
        ("ARF-UPLOAD-PRICING-REVIEW",),
    ).fetchone()
    conn.close()
    assert audit["action"] == "Upload Rejected: pricing_not_accepted"
    assert json.loads(audit["detail"])["reason_code"] == "pricing_not_accepted"


def test_kyc_submit_from_pricing_review_is_blocked_and_audited(api_server):
    from db import get_db

    conn = get_db()
    _seed_kyc_application(
        conn,
        app_id="submit_pricing_review",
        ref="ARF-SUBMIT-PRICING-REVIEW",
        status="pricing_review",
    )
    _insert_uploaded_document(conn, "submit_pricing_review")
    conn.commit()
    conn.close()

    resp = http_requests.post(
        f"{api_server}/api/applications/ARF-SUBMIT-PRICING-REVIEW/submit-kyc",
        headers={"Authorization": f"Bearer {_portal_client_token()}"},
        timeout=3,
    )
    assert resp.status_code == 409
    assert "pricing" in resp.json()["error"].lower()

    conn = get_db()
    app_status = conn.execute("SELECT status FROM applications WHERE id=?", ("submit_pricing_review",)).fetchone()["status"]
    audit = conn.execute(
        "SELECT action, detail FROM audit_log WHERE target=? ORDER BY timestamp DESC, id DESC LIMIT 1",
        ("ARF-SUBMIT-PRICING-REVIEW",),
    ).fetchone()
    conn.close()
    assert app_status == "pricing_review"
    assert audit["action"] == "KYC Transition Blocked: pricing_not_accepted"
    assert json.loads(audit["detail"])["current_status"] == "pricing_review"


def test_kyc_submit_before_pricing_acceptance_is_blocked(api_server):
    from db import get_db

    conn = get_db()
    _seed_kyc_application(
        conn,
        app_id="submit_before_pricing",
        ref="ARF-SUBMIT-BEFORE-PRICING",
        status="submitted",
    )
    _insert_uploaded_document(conn, "submit_before_pricing")
    conn.commit()
    conn.close()

    resp = http_requests.post(
        f"{api_server}/api/applications/ARF-SUBMIT-BEFORE-PRICING/submit-kyc",
        headers={"Authorization": f"Bearer {_portal_client_token()}"},
        timeout=3,
    )
    assert resp.status_code == 409
    assert "pricing" in resp.json()["error"].lower()


def test_high_risk_kyc_submit_without_preapproval_is_blocked(api_server):
    from db import get_db

    conn = get_db()
    _seed_kyc_application(
        conn,
        app_id="submit_high_no_preapproval",
        ref="ARF-SUBMIT-HIGH-NO-PRE",
        status="kyc_documents",
        risk_level="HIGH",
        risk_score=78,
        onboarding_lane="EDD",
        pre_approval_decision=None,
    )
    _insert_uploaded_document(conn, "submit_high_no_preapproval")
    conn.commit()
    conn.close()

    resp = http_requests.post(
        f"{api_server}/api/applications/ARF-SUBMIT-HIGH-NO-PRE/submit-kyc",
        headers={"Authorization": f"Bearer {_portal_client_token()}"},
        timeout=3,
    )
    assert resp.status_code == 409
    assert "pre-approval" in resp.json()["error"].lower()

    conn = get_db()
    status = conn.execute("SELECT status FROM applications WHERE id=?", ("submit_high_no_preapproval",)).fetchone()["status"]
    audit = conn.execute(
        "SELECT action, detail FROM audit_log WHERE target=? ORDER BY timestamp DESC, id DESC LIMIT 1",
        ("ARF-SUBMIT-HIGH-NO-PRE",),
    ).fetchone()
    conn.close()
    assert status == "kyc_documents"
    assert audit["action"] == "KYC Transition Blocked: pre_approval_required"
    assert json.loads(audit["detail"])["pre_approval_decision"] is None


def test_declared_pep_edd_required_still_allows_preapproval_decision(api_server):
    from db import get_db

    app_id = "preapprove_edd_pep"
    ref = "ARF-PREAPPROVE-EDD-PEP"
    conn = get_db()
    _cleanup_application(conn, app_id, ref)
    _seed_kyc_application(
        conn,
        app_id=app_id,
        ref=ref,
        status="edd_required",
        risk_level="MEDIUM",
        risk_score=55,
        onboarding_lane="EDD",
        pre_approval_decision=None,
        director_is_pep="Yes",
    )
    _mark_edd_preapproval_required(
        conn,
        app_id,
        final_risk_level="MEDIUM",
        triggers=["declared_pep_present"],
        reason="Declared PEP routes to EDD and requires pre-approval before KYC.",
    )
    conn.commit()
    conn.close()

    resp = http_requests.post(
        f"{api_server}/api/applications/{ref}/pre-approval-decision",
        headers={"Authorization": f"Bearer {_officer_token()}"},
        json={"decision": "PRE_APPROVE", "notes": "Declared PEP reviewed for KYC collection."},
        timeout=3,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["application_status"] == "kyc_documents"

    conn = get_db()
    app = conn.execute(
        "SELECT status, pre_approval_decision FROM applications WHERE id=?",
        (app_id,),
    ).fetchone()
    audit = conn.execute(
        """
        SELECT action, before_state, after_state
        FROM audit_log
        WHERE target=? AND action='Pre-Approval: PRE_APPROVE'
        ORDER BY timestamp DESC, id DESC LIMIT 1
        """,
        (ref,),
    ).fetchone()

    assert app["status"] == "kyc_documents"
    assert app["pre_approval_decision"] == "PRE_APPROVE"
    assert audit is not None
    assert json.loads(audit["before_state"])["status"] == "edd_required"
    assert json.loads(audit["after_state"]) == {
        "status": "kyc_documents",
        "pre_approval_decision": "PRE_APPROVE",
    }
    conn.close()

    upload = http_requests.post(
        f"{api_server}/api/applications/{ref}/documents?doc_type=cert_inc",
        headers={"Authorization": f"Bearer {_portal_client_token()}"},
        files={"file": ("pep-coi.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        timeout=3,
    )
    assert upload.status_code == 201, upload.text
    conn = get_db()
    _ensure_verified_required_documents(conn, app_id, include_bankref=True)
    conn.commit()
    conn.close()

    submit = http_requests.post(
        f"{api_server}/api/applications/{ref}/submit-kyc",
        headers={"Authorization": f"Bearer {_portal_client_token()}"},
        timeout=3,
    )
    assert submit.status_code == 200, submit.text
    assert submit.json()["status"] == "kyc_submitted"

    conn = get_db()
    app = conn.execute(
        "SELECT status, risk_level, final_risk_level, onboarding_lane "
        "FROM applications WHERE id=?",
        (app_id,),
    ).fetchone()
    assert app["status"] == "kyc_submitted"
    assert app["final_risk_level"] != "LOW"
    assert app["onboarding_lane"] == "EDD"
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM edd_cases WHERE application_id=?",
        (app_id,),
    ).fetchone()["c"] == 0
    attestation = conn.execute(
        "SELECT detail FROM audit_log WHERE target=? AND action='KYC Attestation Submitted' ORDER BY id DESC LIMIT 1",
        (ref,),
    ).fetchone()
    assert attestation is not None
    assert json.loads(attestation["detail"])["actor_type"] == "user"
    _cleanup_application(conn, app_id, ref)
    conn.commit()
    conn.close()


def test_crypto_edd_required_after_preapproval_can_upload_and_submit_kyc(api_server):
    from db import get_db

    app_id = "preapprove_edd_crypto"
    ref = "ARF-PREAPPROVE-EDD-CRYPTO"
    conn = get_db()
    _cleanup_application(conn, app_id, ref)
    _seed_kyc_application(
        conn,
        app_id=app_id,
        ref=ref,
        status="edd_required",
        risk_level="HIGH",
        risk_score=82,
        onboarding_lane="EDD",
        pre_approval_decision=None,
        sector="Crypto / Virtual Asset Service Provider",
    )
    _mark_edd_preapproval_required(
        conn,
        app_id,
        final_risk_level="HIGH",
        triggers=["crypto_or_virtual_asset_sector", "high_risk_sector"],
        reason="Crypto/VASP routes to EDD and requires pre-approval before KYC.",
    )
    conn.commit()
    conn.close()

    preapprove = http_requests.post(
        f"{api_server}/api/applications/{ref}/pre-approval-decision",
        headers={"Authorization": f"Bearer {_officer_token()}"},
        json={"decision": "PRE_APPROVE", "notes": "Crypto/VASP EDD pre-approved for KYC collection."},
        timeout=3,
    )
    assert preapprove.status_code == 201, preapprove.text

    upload = http_requests.post(
        f"{api_server}/api/applications/{ref}/documents?doc_type=cert_inc",
        headers={"Authorization": f"Bearer {_portal_client_token()}"},
        files={"file": ("crypto-coi.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        timeout=3,
    )
    assert upload.status_code == 201, upload.text
    conn = get_db()
    _ensure_verified_required_documents(conn, app_id, include_bankref=True)
    conn.commit()
    conn.close()

    submit = http_requests.post(
        f"{api_server}/api/applications/{ref}/submit-kyc",
        headers={"Authorization": f"Bearer {_portal_client_token()}"},
        timeout=3,
    )
    assert submit.status_code == 200, submit.text
    assert submit.json()["status"] == "kyc_submitted"
    conn = get_db()
    app = conn.execute(
        "SELECT status, risk_level, final_risk_level, onboarding_lane "
        "FROM applications WHERE id=?",
        (app_id,),
    ).fetchone()
    assert app["status"] == "kyc_submitted"
    assert app["final_risk_level"] != "LOW"
    assert app["onboarding_lane"] == "EDD"
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM edd_cases WHERE application_id=?",
        (app_id,),
    ).fetchone()["c"] == 0
    _cleanup_application(conn, app_id, ref)
    conn.commit()
    conn.close()


def test_edd_required_without_preapproval_cannot_upload_or_submit_kyc(api_server):
    from db import get_db

    app_id = "edd_no_preapproval"
    ref = "ARF-EDD-NO-PREAPPROVAL"
    conn = get_db()
    _cleanup_application(conn, app_id, ref)
    _seed_kyc_application(
        conn,
        app_id=app_id,
        ref=ref,
        status="edd_required",
        risk_level="MEDIUM",
        risk_score=58,
        onboarding_lane="EDD",
        pre_approval_decision=None,
    )
    _mark_edd_preapproval_required(
        conn,
        app_id,
        final_risk_level="MEDIUM",
        triggers=["declared_pep_present"],
    )
    _insert_uploaded_document(conn, app_id)
    conn.commit()
    conn.close()

    upload = http_requests.post(
        f"{api_server}/api/applications/{ref}/documents?doc_type=cert_inc",
        headers={"Authorization": f"Bearer {_portal_client_token()}"},
        files={"file": ("blocked.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        timeout=3,
    )
    assert upload.status_code == 409

    submit = http_requests.post(
        f"{api_server}/api/applications/{ref}/submit-kyc",
        headers={"Authorization": f"Bearer {_portal_client_token()}"},
        timeout=3,
    )
    assert submit.status_code == 409
    conn = get_db()
    _cleanup_application(conn, app_id, ref)
    conn.commit()
    conn.close()


def test_invalid_preapproval_state_still_rejects(api_server):
    from db import get_db

    app_id = "preapproval_invalid_state"
    ref = "ARF-PREAPPROVAL-INVALID-STATE"
    conn = get_db()
    _cleanup_application(conn, app_id, ref)
    _seed_kyc_application(
        conn,
        app_id=app_id,
        ref=ref,
        status="draft",
        risk_level="HIGH",
        risk_score=80,
        onboarding_lane="EDD",
        pre_approval_decision=None,
    )
    _mark_edd_preapproval_required(
        conn,
        app_id,
        final_risk_level="HIGH",
        triggers=["high_risk_sector"],
    )
    conn.commit()
    conn.close()

    resp = http_requests.post(
        f"{api_server}/api/applications/{ref}/pre-approval-decision",
        headers={"Authorization": f"Bearer {_officer_token()}"},
        json={"decision": "PRE_APPROVE", "notes": "Should not be allowed from draft."},
        timeout=3,
    )
    assert resp.status_code == 400
    assert "pre-approval decision not allowed" in resp.json()["error"].lower()
    conn = get_db()
    _cleanup_application(conn, app_id, ref)
    conn.commit()
    conn.close()


def test_manual_status_change_to_edd_required_syncs_lane_to_edd_or_rejects(api_server):
    from db import get_db

    app_id = "manual_edd_lane_sync"
    ref = "ARF-MANUAL-EDD-LANE-SYNC"
    conn = get_db()
    _cleanup_application(conn, app_id, ref)
    _seed_kyc_application(
        conn,
        app_id=app_id,
        ref=ref,
        status="compliance_review",
        risk_level="MEDIUM",
        risk_score=52,
        onboarding_lane="Standard Review",
    )
    conn.commit()
    conn.close()

    resp = http_requests.patch(
        f"{api_server}/api/applications/{ref}",
        headers={"Authorization": f"Bearer {_officer_token()}"},
        json={"status": "edd_required"},
        timeout=3,
    )
    assert resp.status_code == 200, resp.text

    conn = get_db()
    app = conn.execute(
        "SELECT status, onboarding_lane FROM applications WHERE id=?",
        (app_id,),
    ).fetchone()
    audit = conn.execute(
        """
        SELECT detail FROM audit_log
        WHERE target=? AND action='Status Change'
        ORDER BY id DESC LIMIT 1
        """,
        (ref,),
    ).fetchone()
    assert app["status"] == "edd_required"
    assert app["onboarding_lane"] == "EDD"
    assert audit is not None
    assert "onboarding_lane: Standard Review → EDD" in audit["detail"]
    _cleanup_application(conn, app_id, ref)
    conn.commit()
    conn.close()


def test_valid_kyc_documents_case_can_upload(api_server):
    from db import get_db

    conn = get_db()
    _seed_kyc_application(
        conn,
        app_id="upload_valid_kyc",
        ref="ARF-UPLOAD-VALID-KYC",
        status="kyc_documents",
    )
    conn.commit()
    conn.close()

    resp = http_requests.post(
        f"{api_server}/api/applications/ARF-UPLOAD-VALID-KYC/documents?doc_type=cert_inc",
        headers={"Authorization": f"Bearer {_portal_client_token()}"},
        files={"file": ("coi.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        timeout=3,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["doc_type"] == "cert_inc"
