import json
import os
import socket
import sys
import tempfile
import threading
import time
from datetime import date, timedelta

import pytest
import requests
import tornado.httpserver
import tornado.ioloop


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def rmi_api_server():
    db_path = os.path.join(tempfile.gettempdir(), f"onboarda_rmi_test_{os.getpid()}.db")
    os.environ["DB_PATH"] = db_path
    try:
        os.unlink(db_path)
    except OSError:
        pass

    from db import get_db, init_db

    init_db()
    conn = get_db()
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


def _seed_client_application(app_id="app_rmi_structured", app_ref="ARF-2026-RMI-001"):
    from db import get_db

    client_id = f"client_{app_id}"
    conn = get_db()
    conn.execute("DELETE FROM rmi_request_items WHERE request_id IN (SELECT id FROM rmi_requests WHERE application_id=?)", (app_id,))
    conn.execute("DELETE FROM rmi_requests WHERE application_id=?", (app_id,))
    conn.execute("DELETE FROM client_notifications WHERE application_id=?", (app_id,))
    conn.execute("DELETE FROM documents WHERE application_id=?", (app_id,))
    conn.execute("DELETE FROM audit_log WHERE target=?", (app_ref,))
    conn.execute("DELETE FROM applications WHERE id=?", (app_id,))
    conn.execute("DELETE FROM clients WHERE id=?", (client_id,))
    conn.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"{client_id}@example.com", "hash", "RMI Client Ltd"),
    )
    conn.execute(
        """INSERT INTO applications
           (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            app_id,
            app_ref,
            client_id,
            "RMI Client Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "in_review",
            "MEDIUM",
            44,
        ),
    )
    conn.commit()
    conn.close()
    return client_id, app_id, app_ref


def _officer_headers():
    from auth import create_token

    token = create_token("admin001", "admin", "Test Admin", "officer")
    return {"Authorization": f"Bearer {token}"}


def _client_headers(client_id):
    from auth import create_token

    token = create_token(client_id, "client", "RMI Client", "client")
    return {"Authorization": f"Bearer {token}"}


def _future_deadline(days=14):
    return (date.today() + timedelta(days=days)).isoformat()


def _create_rmi_request(base_url, app_id, deadline=None, items=None):
    return requests.post(
        f"{base_url}/api/applications/{app_id}/decision",
        json={
            "decision": "request_documents",
            "decision_reason": "Additional source of funds evidence is required for review.",
            "rmi_deadline": deadline or _future_deadline(),
            "rmi_items": items or [
                {"doc_type": "source_funds", "label": "Source of Funds Evidence"},
                {"doc_type": "bank_statements", "label": "Bank Statements"},
            ],
            "officer_signoff": {
                "acknowledged": True,
                "scope": "decision",
                "source_context": "ai_advisory",
            },
        },
        headers=_officer_headers(),
        timeout=5,
    )


def test_request_documents_creates_structured_rmi_and_notification(rmi_api_server):
    client_id, app_id, app_ref = _seed_client_application()
    deadline = _future_deadline()

    resp = _create_rmi_request(rmi_api_server, app_id, deadline=deadline)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["application_status"] == "rmi_sent"
    assert body["rmi_request_id"]

    from db import get_db

    conn = get_db()
    app = conn.execute("SELECT status, decision_notes FROM applications WHERE id=?", (app_id,)).fetchone()
    req = conn.execute("SELECT * FROM rmi_requests WHERE id=?", (body["rmi_request_id"],)).fetchone()
    items = conn.execute("SELECT doc_type, label, status FROM rmi_request_items WHERE request_id=? ORDER BY created_at", (body["rmi_request_id"],)).fetchall()
    notification = conn.execute("SELECT * FROM client_notifications WHERE rmi_request_id=?", (body["rmi_request_id"],)).fetchone()
    audit = conn.execute(
        """SELECT detail FROM audit_log
           WHERE target=? AND action='Governance Attempt'
           ORDER BY id DESC LIMIT 1""",
        (app_ref,),
    ).fetchone()
    conn.close()

    assert app["status"] == "rmi_sent"
    assert json.loads(app["decision_notes"])["rmi_request_id"] == body["rmi_request_id"]
    assert req["status"] == "open"
    assert req["deadline"] == deadline
    assert [item["doc_type"] for item in items] == ["source_funds", "bank_statements"]
    assert all(item["status"] == "requested" for item in items)
    assert notification["notification_type"] == "documents_required"
    assert notification["client_id"] == client_id
    assert audit is not None
    detail = json.loads(audit["detail"])
    assert detail["action"] == "application.decision"
    assert detail["outcome"] == "accepted"

    client_resp = requests.get(
        f"{rmi_api_server}/api/applications/{app_ref}/rmi",
        headers=_client_headers(client_id),
        timeout=5,
    )
    assert client_resp.status_code == 200
    labels = {item["label"] for item in client_resp.json()["requests"][0]["items"]}
    assert "Source of Funds Evidence" in labels


def test_client_notifications_expose_rmi_and_upload_fulfills_item(rmi_api_server):
    client_id, app_id, _ = _seed_client_application(
        app_id="app_rmi_upload",
        app_ref="ARF-2026-RMI-002",
    )
    resp = _create_rmi_request(rmi_api_server, app_id)
    assert resp.status_code == 201, resp.text
    rmi_request_id = resp.json()["rmi_request_id"]

    notif_resp = requests.get(
        f"{rmi_api_server}/api/notifications",
        headers=_client_headers(client_id),
        timeout=5,
    )
    assert notif_resp.status_code == 200
    notifications = notif_resp.json()["notifications"]
    rmi_requests = notif_resp.json()["rmi_requests"]
    assert notifications[0]["rmi_request_id"] == rmi_request_id
    assert rmi_requests[0]["id"] == rmi_request_id
    first_item = rmi_requests[0]["items"][0]

    upload_resp = requests.post(
        f"{rmi_api_server}/api/applications/{app_id}/documents"
        f"?doc_type={first_item['doc_type']}&rmi_item_id={first_item['id']}",
        headers=_client_headers(client_id),
        files={"file": ("source-funds.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        timeout=5,
    )
    assert upload_resp.status_code == 201, upload_resp.text
    assert upload_resp.json()["rmi_item_id"] == first_item["id"]

    from db import get_db

    conn = get_db()
    item = conn.execute("SELECT status, document_id FROM rmi_request_items WHERE id=?", (first_item["id"],)).fetchone()
    request_row = conn.execute("SELECT status FROM rmi_requests WHERE id=?", (rmi_request_id,)).fetchone()
    conn.close()

    assert item["status"] == "uploaded"
    assert item["document_id"] == upload_resp.json()["id"]
    assert request_row["status"] == "partially_fulfilled"

    delete_resp = requests.delete(
        f"{rmi_api_server}/api/applications/{app_id}/documents/{upload_resp.json()['id']}",
        headers=_client_headers(client_id),
        timeout=5,
    )
    assert delete_resp.status_code == 200, delete_resp.text

    conn = get_db()
    item = conn.execute("SELECT status, document_id, uploaded_at, reviewed_at FROM rmi_request_items WHERE id=?", (first_item["id"],)).fetchone()
    request_row = conn.execute("SELECT status FROM rmi_requests WHERE id=?", (rmi_request_id,)).fetchone()
    doc = conn.execute("SELECT id FROM documents WHERE id=?", (upload_resp.json()["id"],)).fetchone()
    conn.close()

    assert doc is None
    assert item["status"] == "requested"
    assert item["document_id"] is None
    assert item["uploaded_at"] is None
    assert item["reviewed_at"] is None
    assert request_row["status"] == "open"


def test_rmi_upload_requires_matching_item_and_application(rmi_api_server):
    client_id, app_id, _ = _seed_client_application(
        app_id="app_rmi_upload_validation",
        app_ref="ARF-2026-RMI-003",
    )
    resp = _create_rmi_request(rmi_api_server, app_id)
    assert resp.status_code == 201, resp.text

    from db import get_db

    conn = get_db()
    item = conn.execute(
        """SELECT i.id, i.doc_type
           FROM rmi_request_items i
           JOIN rmi_requests r ON r.id = i.request_id
           WHERE r.application_id=?
           ORDER BY i.created_at LIMIT 1""",
        (app_id,),
    ).fetchone()
    conn.close()

    generic_upload_resp = requests.post(
        f"{rmi_api_server}/api/applications/{app_id}/documents?doc_type={item['doc_type']}",
        headers=_client_headers(client_id),
        files={"file": ("generic-source-funds.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        timeout=5,
    )
    assert generic_upload_resp.status_code == 201, generic_upload_resp.text
    assert "rmi_item_id" not in generic_upload_resp.json()

    conn = get_db()
    unchanged_item = conn.execute("SELECT status, document_id FROM rmi_request_items WHERE id=?", (item["id"],)).fetchone()
    conn.close()
    assert unchanged_item["status"] == "requested"
    assert unchanged_item["document_id"] is None

    wrong_type_resp = requests.post(
        f"{rmi_api_server}/api/applications/{app_id}/documents"
        f"?doc_type=poa&rmi_item_id={item['id']}",
        headers=_client_headers(client_id),
        files={"file": ("proof-address.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        timeout=5,
    )
    assert wrong_type_resp.status_code == 400
    assert "does not match" in wrong_type_resp.text

    other_client_id, other_app_id, _ = _seed_client_application(
        app_id="app_rmi_cross_upload",
        app_ref="ARF-2026-RMI-004",
    )
    cross_app_resp = requests.post(
        f"{rmi_api_server}/api/applications/{other_app_id}/documents"
        f"?doc_type={item['doc_type']}&rmi_item_id={item['id']}",
        headers=_client_headers(other_client_id),
        files={"file": ("source-funds.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        timeout=5,
    )
    assert cross_app_resp.status_code == 400
    assert "not found for this application" in cross_app_resp.text


def test_request_documents_rejects_invalid_or_past_deadline(rmi_api_server):
    _, app_id, app_ref = _seed_client_application(
        app_id="app_rmi_deadline_validation",
        app_ref="ARF-2026-RMI-005",
    )

    invalid_resp = _create_rmi_request(rmi_api_server, app_id, deadline="9999-13-45")
    assert invalid_resp.status_code == 400
    assert "valid calendar date" in invalid_resp.text

    past_resp = _create_rmi_request(
        rmi_api_server,
        app_id,
        deadline=(date.today() - timedelta(days=1)).isoformat(),
    )
    assert past_resp.status_code == 400
    assert "cannot be in the past" in past_resp.text

    from db import get_db

    conn = get_db()
    requests_count = conn.execute("SELECT COUNT(*) AS c FROM rmi_requests WHERE application_id=?", (app_id,)).fetchone()["c"]
    audit_rows = conn.execute(
        """SELECT COUNT(*) AS c FROM audit_log
           WHERE target=? AND action='Governance Attempt'""",
        (app_ref,),
    ).fetchone()["c"]
    conn.close()

    assert requests_count == 0
    assert audit_rows >= 2
