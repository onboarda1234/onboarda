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
    from auth import create_token
    from db import get_db

    temp_dir = tempfile.mkdtemp(prefix="portal-submit-")
    file_path = os.path.join(temp_dir, "coi.pdf")
    with open(file_path, "wb") as handle:
        handle.write(b"certificate")

    conn = get_db()
    _ensure_client(conn)
    conn.execute(
        """
        INSERT INTO applications (id, ref, client_id, company_name, country, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("submit_incomplete", "ARF-SUBMIT-INCOMPLETE", "portalclient001", "Submit Incomplete Ltd", "Mauritius", "pricing_review", json.dumps({
            "registered_entity_name": "Submit Incomplete Ltd",
            "country_of_incorporation": "Mauritius"
        })),
    )
    conn.execute(
        """
        INSERT INTO directors (id, application_id, person_key, first_name, last_name, full_name, nationality, is_pep, pep_declaration, date_of_birth)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("dir_submit_incomplete", "submit_incomplete", "dir1", "Jane", "Director", "Jane Director", "Mauritius", "No", "{}", "1985-01-01"),
    )
    conn.execute(
        """
        INSERT INTO documents (id, application_id, doc_type, doc_name, file_path, verification_status, verification_results)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("doc_submit_incomplete", "submit_incomplete", "cert_inc", "coi.pdf", file_path, "flagged", json.dumps({"warnings": ["Name mismatch"]})),
    )
    conn.commit()
    conn.close()

    token = create_token("portalclient001", "client", "Portal Client", "client")
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
    from auth import create_token
    from db import get_db

    conn = get_db()
    _ensure_client(conn)
    conn.execute(
        """
        INSERT INTO applications (id, ref, client_id, company_name, country, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("submit_no_docs", "ARF-SUBMIT-NO-DOCS", "portalclient001", "Submit No Docs Ltd", "Mauritius", "pricing_review", json.dumps({
            "registered_entity_name": "Submit No Docs Ltd",
            "country_of_incorporation": "Mauritius"
        })),
    )
    conn.execute(
        """
        INSERT INTO directors (id, application_id, person_key, first_name, last_name, full_name, nationality, is_pep, pep_declaration, date_of_birth)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("dir_submit_no_docs", "submit_no_docs", "dir1", "Jane", "Director", "Jane Director", "Mauritius", "No", "{}", "1985-01-01"),
    )
    conn.commit()
    conn.close()

    token = create_token("portalclient001", "client", "Portal Client", "client")
    resp = http_requests.post(
        f"{api_server}/api/applications/ARF-SUBMIT-NO-DOCS/submit-kyc",
        headers={"Authorization": f"Bearer {token}"},
        timeout=3,
    )
    assert resp.status_code == 400
    assert "upload at least one document" in resp.json()["error"].lower()
