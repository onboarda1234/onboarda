import json
import os
import socket
import tempfile
import threading
import time

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

    io_loop = server_ref.get("loop")
    srv = server_ref.get("server")
    if io_loop and srv:
        io_loop.add_callback(srv.stop)
        io_loop.add_callback(io_loop.stop)
    thread.join(timeout=2)


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
):
    _ensure_client(db)
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, status, risk_level, risk_score,
         onboarding_lane, pre_approval_decision, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            ref,
            "portalclient001",
            f"{ref} Ltd",
            "Mauritius",
            status,
            risk_level,
            risk_score,
            onboarding_lane,
            pre_approval_decision,
            json.dumps({
                "registered_entity_name": f"{ref} Ltd",
                "country_of_incorporation": "Mauritius",
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
            "No",
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


def _insert_uploaded_document(db, app_id, *, doc_id=None):
    temp_dir = tempfile.mkdtemp(prefix="portal-submit-")
    file_path = os.path.join(temp_dir, f"{app_id}.pdf")
    with open(file_path, "wb") as handle:
        handle.write(b"%PDF-1.4\n%EOF\n")
    db.execute(
        """
        INSERT INTO documents
        (id, application_id, doc_type, doc_name, file_path, verification_status, verification_results)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id or f"doc_{app_id}",
            app_id,
            "cert_inc",
            f"{app_id}.pdf",
            file_path,
            "flagged",
            json.dumps({"warnings": ["Name mismatch"]}),
        ),
    )


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


def test_client_delete_cleans_child_rows_and_document_artifacts(api_server):
    from auth import create_token
    from db import get_db

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
        INSERT INTO documents (id, application_id, doc_type, doc_name, file_path)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("doc_delete_children", "delete_children", "cert_inc", "draft-doc.pdf", file_path),
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
    conn.commit()
    conn.close()

    token = create_token("portalclient001", "client", "Portal Client", "client")
    resp = http_requests.delete(
        f"{api_server}/api/applications/ARF-DELETE-CHILDREN",
        headers={"Authorization": f"Bearer {token}"},
        timeout=3,
    )
    assert resp.status_code == 200

    conn = get_db()
    assert conn.execute("SELECT id FROM applications WHERE id=?", ("delete_children",)).fetchone() is None
    assert conn.execute("SELECT id FROM documents WHERE application_id=?", ("delete_children",)).fetchone() is None
    assert conn.execute("SELECT id FROM client_sessions WHERE application_id=?", ("delete_children",)).fetchone() is None
    assert conn.execute("SELECT id FROM client_notifications WHERE application_id=?", ("delete_children",)).fetchone() is None
    assert conn.execute("SELECT id FROM periodic_reviews WHERE application_id=?", ("delete_children",)).fetchone() is None
    conn.close()
    assert not os.path.exists(file_path)


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
    assert "btn.textContent = '⚠️ Submit for Compliance Review';" in src
    assert "Submitted for Review — Issues Visible" in src
    assert "Incomplete / Warning-State Submission Logged" in src


def test_backoffice_incomplete_submission_banner_is_present():
    backoffice_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-backoffice.html")
    with open(backoffice_path, "r", encoding="utf-8") as handle:
        src = handle.read()

    assert "function computeDocumentReadinessSummary(app)" in src
    assert "⚠ Incomplete / Warning-State" in src
    assert "This case is reviewable but should not be treated as clean." in src


def test_kyc_submit_allows_incomplete_documents_with_at_least_one_upload(api_server):
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
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "kyc_submitted"
    assert body["documents_uploaded"] == 1

    conn = get_db()
    row = conn.execute("SELECT status FROM applications WHERE id=?", ("submit_incomplete",)).fetchone()
    conn.close()
    assert row["status"] == "kyc_submitted"


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

    submit = http_requests.post(
        f"{api_server}/api/applications/{ref}/submit-kyc",
        headers={"Authorization": f"Bearer {_portal_client_token()}"},
        timeout=3,
    )
    assert submit.status_code == 200, submit.text
    assert submit.json()["status"] == "kyc_submitted"
    conn = get_db()
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
