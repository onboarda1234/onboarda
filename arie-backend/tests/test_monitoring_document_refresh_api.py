import json
import os
import socket
import sys
import threading
import time

import pytest
import requests
import tornado.httpserver
import tornado.ioloop

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def monitoring_doc_refresh_server(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("monitoring_doc_refresh") / "onboarda_monitoring_doc_refresh.db")
    os.environ["DB_PATH"] = db_path
    os.environ["DATABASE_URL"] = ""

    import importlib
    import db as db_module

    importlib.reload(db_module)
    db_module._DB_PATH = db_path
    db_module.init_db()
    conn = db_module.get_db()
    db_module.seed_initial_data(conn)
    _reset_document_refresh_case(conn)
    conn.commit()
    conn.close()

    import server as server_module

    importlib.reload(server_module)
    app = server_module.make_app()
    port = _free_port()
    server_ref = {}
    started = threading.Event()

    def run_server():
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        io_loop = tornado.ioloop.IOLoop.current()
        server = tornado.httpserver.HTTPServer(app)
        server.listen(port, "127.0.0.1")
        server_ref["server"] = server
        server_ref["loop"] = io_loop
        started.set()
        io_loop.start()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    started.wait(timeout=3)
    time.sleep(0.2)
    yield f"http://127.0.0.1:{port}", db_module

    loop = server_ref.get("loop")
    server = server_ref.get("server")
    if loop and server:
        loop.add_callback(server.stop)
        loop.add_callback(loop.stop)
    thread.join(timeout=2)


@pytest.fixture(autouse=True)
def reset_monitoring_doc_refresh_case(monitoring_doc_refresh_server):
    _base_url, db_module = monitoring_doc_refresh_server
    conn = db_module.get_db()
    try:
        _reset_document_refresh_case(conn)
        conn.commit()
    finally:
        conn.close()


def _reset_document_refresh_case(conn):
    conn.execute("DELETE FROM application_enhanced_requirements WHERE application_id = ? OR monitoring_alert_id = ?", ("app_m3", 9301))
    conn.execute("DELETE FROM client_notifications WHERE application_id = ?", ("app_m3",))
    conn.execute("DELETE FROM notifications WHERE message LIKE ?", ("%9301%",))
    conn.execute("DELETE FROM audit_log WHERE target = ? OR target = ?", ("monitoring_alert:9301", "ARF-M3"))
    conn.execute("DELETE FROM monitoring_alerts WHERE id = 9301")
    conn.execute("DELETE FROM documents WHERE application_id = ?", ("app_m3",))
    conn.execute("DELETE FROM applications WHERE id = ?", ("app_m3",))
    conn.execute("DELETE FROM clients WHERE id = ?", ("client_m3",))

    users = [
        ("admin_m3", "admin-m3@example.test", "Admin M3", "admin"),
        ("sco_m3", "sco-m3@example.test", "SCO M3", "sco"),
        ("co_m3", "co-m3@example.test", "CO M3", "co"),
    ]
    for user_id, email, name, role in users:
        conn.execute(
            "INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, 'active')",
            (user_id, email, "unused", name, role),
        )
    conn.execute(
        "INSERT OR REPLACE INTO clients (id, email, password_hash, company_name, status) VALUES (?, ?, ?, ?, 'active')",
        ("client_m3", "client-m3@example.test", "unused", "Monitoring Three Client Ltd"),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO applications
            (id, ref, client_id, company_name, status, risk_level)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("app_m3", "ARF-M3", "client_m3", "Monitoring Three Client Ltd", "approved", "MEDIUM"),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO documents
            (id, application_id, doc_type, doc_name, file_path, file_size, mime_type,
             slot_key, is_current, version, expiry_date, verification_status, review_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, 'verified', 'accepted')
        """,
        (
            "doc_expired_m3",
            "app_m3",
            "passport",
            "Expired passport.pdf",
            "/tmp/expired-passport.pdf",
            20,
            "application/pdf",
            "passport:company",
            "2026-01-01",
        ),
    )
    conn.execute("DELETE FROM monitoring_alerts WHERE id = 9301")
    conn.execute(
        """
        INSERT INTO monitoring_alerts
            (id, application_id, client_name, alert_type, severity, detected_by,
             summary, source_reference, status, discovered_via)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            9301,
            "app_m3",
            "Monitoring Three Client Ltd",
            "document_expired",
            "medium",
            "Document Health Monitor",
            "Passport expired for Monitoring Three Client Ltd",
            "document:doc_expired_m3",
            "open",
            "document_health",
        ),
    )


def _token(user_id, role, name, user_type="officer"):
    from auth import create_token

    return create_token(user_id, role, name, user_type)


def _json_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


class _FakeCursor:
    def __init__(self, *, one=None, many=None):
        self._one = one
        self._many = many or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many


class _NotificationInsertDb:
    def __init__(self):
        self.insert_params = None

    def execute(self, sql, params=()):
        if "SELECT id, documents_list" in sql:
            return _FakeCursor(many=[])
        if "INSERT INTO client_notifications" in sql:
            self.insert_params = params
            return _FakeCursor()
        if "SELECT * FROM client_notifications" in sql:
            return _FakeCursor(one={"id": 991, "read_status": self.insert_params[6]})
        raise AssertionError(f"unexpected SQL: {sql}")


def test_updated_document_notification_uses_boolean_read_status_parameter():
    from monitoring_document_refresh import _insert_client_notification

    db = _NotificationInsertDb()
    row = _insert_client_notification(
        db,
        {"application_id": "app_m3", "application_client_id": "client_m3"},
        {"id": 9301},
        "Updated Passport required",
        "Please upload an updated copy.",
        "2026-06-24",
    )

    assert db.insert_params[6] is False
    assert row["read_status"] is False


def _request_updated_document(base_url, token):
    return requests.patch(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_json_headers(token),
        json={"action": "request_updated_document"},
        timeout=5,
    )


def _upload_client_document(base_url, client_token, requirement_id):
    return requests.post(
        f"{base_url}/api/portal/applications/app_m3/enhanced-requirements/{requirement_id}/upload",
        headers=_auth_headers(client_token),
        files={"file": ("renewed-passport.pdf", b"%PDF-1.4\n% renewed document\n%%EOF", "application/pdf")},
        timeout=5,
    )


def _upload_backoffice_replacement(base_url, officer_token, *, source_note="Received by email from client on 2026-06-10"):
    return requests.post(
        f"{base_url}/api/monitoring/alerts/9301/replacement-upload",
        headers=_auth_headers(officer_token),
        data={"source_note": source_note},
        files={"file": ("backoffice-renewed-passport.pdf", b"%PDF-1.4\n% backoffice renewed\n%%EOF", "application/pdf")},
        timeout=5,
    )


def test_request_updated_document_creates_linked_portal_task_notification_and_audit(monitoring_doc_refresh_server):
    base_url, db_module = monitoring_doc_refresh_server
    token = _token("admin_m3", "admin", "Admin M3")

    resp = _request_updated_document(base_url, token)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["new_status"] == "document_requested"
    assert body["result"]["created"] is True
    assert body["result"]["request"]["monitoring_alert_id"] == 9301
    assert body["result"]["request"]["monitoring_document_id"] == "doc_expired_m3"

    detail = requests.get(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_auth_headers(token),
        timeout=5,
    )
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["document_refresh"]["document"]["type_label"] == "Passport"
    assert detail_body["document_refresh"]["request"]["status"] == "requested"
    assert detail_body["document_refresh"]["client_id"] == "client_m3"
    assert detail_body["document_refresh"]["notification"]["status"] in {"sent", "failed"}

    conn = db_module.get_db()
    try:
        req_count = conn.execute(
            "SELECT COUNT(*) AS c FROM application_enhanced_requirements WHERE monitoring_alert_id = 9301"
        ).fetchone()["c"]
        assert req_count == 1
        notification = conn.execute(
            "SELECT notification_type, documents_list FROM client_notifications WHERE application_id = ?",
            ("app_m3",),
        ).fetchone()
        assert notification["notification_type"] == "updated_document_required"
        assert json.loads(notification["documents_list"]) == ["Updated Passport required"]
        audit_actions = [
            row["action"]
            for row in conn.execute(
                "SELECT action FROM audit_log WHERE target = ?",
                ("monitoring_alert:9301",),
            ).fetchall()
        ]
        assert "updated_document_requested" in audit_actions
        assert "document_request_notification_failed" in audit_actions or "document_request_notification_sent" in audit_actions
    finally:
        conn.close()


def test_duplicate_updated_document_request_reuses_active_requirement(monitoring_doc_refresh_server):
    base_url, db_module = monitoring_doc_refresh_server
    token = _token("admin_m3", "admin", "Admin M3")

    first = _request_updated_document(base_url, token)
    second = _request_updated_document(base_url, token)
    assert first.status_code == 200
    assert second.status_code == 200, second.text
    assert second.json()["result"]["reused"] is True

    conn = db_module.get_db()
    try:
        req_count = conn.execute(
            "SELECT COUNT(*) AS c FROM application_enhanced_requirements WHERE monitoring_alert_id = 9301"
        ).fetchone()["c"]
        notif_count = conn.execute(
            "SELECT COUNT(*) AS c FROM client_notifications WHERE application_id = ?",
            ("app_m3",),
        ).fetchone()["c"]
        assert req_count == 1
        assert notif_count == 1
    finally:
        conn.close()


def test_client_portal_task_upload_updates_alert_without_internal_language(monitoring_doc_refresh_server):
    base_url, _db_module = monitoring_doc_refresh_server
    officer_token = _token("admin_m3", "admin", "Admin M3")
    client_token = _token("client_m3", "client", "Monitoring Three Client Ltd", "client")

    assert _request_updated_document(base_url, officer_token).status_code == 200
    tasks_resp = requests.get(
        f"{base_url}/api/portal/applications/app_m3/enhanced-requirements",
        headers=_auth_headers(client_token),
        timeout=5,
    )
    assert tasks_resp.status_code == 200, tasks_resp.text
    tasks_body = tasks_resp.json()
    task = tasks_body["requirements"][0]
    assert task["label"] == "Updated Passport required"
    assert task["requirement_type"] == "document"
    assert task["due_date"]
    serialized_task = json.dumps(task).lower()
    assert "monitoring alert" not in serialized_task
    assert "9301" not in serialized_task

    upload = _upload_client_document(base_url, client_token, task["id"])
    assert upload.status_code == 201, upload.text

    detail = requests.get(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_auth_headers(officer_token),
        timeout=5,
    )
    assert detail.status_code == 200
    detail_body = detail.json()
    # M1.1 decoupling: the stored alert status stays canonical; the refresh
    # sub-state is carried by the requirement and surfaced as derived state.
    assert detail_body["status"] == "open"
    assert detail_body["status_key"] == "client_uploaded"
    assert detail_body["status_label"] == "Awaiting Officer"
    assert detail_body["document_refresh"]["request"]["status"] == "uploaded"
    assert detail_body["document_refresh"]["request"]["linked_document"]["doc_name"] == "renewed-passport.pdf"
    assert detail_body["document_refresh"]["request"]["linked_document"]["doc_type"] == "passport"
    assert detail_body["document_refresh"]["request"]["linked_document"]["upload_source"] == "client_portal"
    assert any(item["action"] == "client_document_upload_received" for item in detail_body["audit_history"])

    app_docs = requests.get(
        f"{base_url}/api/applications/app_m3/documents?include_history=true",
        headers=_auth_headers(officer_token),
        timeout=5,
    )
    assert app_docs.status_code == 200
    docs = app_docs.json()
    replacement = next(doc for doc in docs if doc["id"] == detail_body["document_refresh"]["request"]["linked_document"]["id"])
    expired = next(doc for doc in docs if doc["id"] == "doc_expired_m3")
    assert replacement["doc_type"] == "passport"
    assert replacement["review_status"] == "pending"
    assert replacement["is_current"] in (1, True)
    assert expired["is_current"] in (0, False)
    assert expired["superseded_by_document_id"] == replacement["id"]


def test_backoffice_upload_replacement_requires_note_and_links_application_documents(monitoring_doc_refresh_server):
    base_url, _db_module = monitoring_doc_refresh_server
    officer_token = _token("admin_m3", "admin", "Admin M3")
    client_token = _token("client_m3", "client", "Monitoring Three Client Ltd", "client")

    missing_note = requests.post(
        f"{base_url}/api/monitoring/alerts/9301/replacement-upload",
        headers=_auth_headers(officer_token),
        data={"source_note": ""},
        files={"file": ("backoffice-renewed-passport.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
        timeout=5,
    )
    assert missing_note.status_code == 400

    upload = _upload_backoffice_replacement(base_url, officer_token)
    assert upload.status_code == 201, upload.text
    uploaded_doc = upload.json()["document"]
    assert uploaded_doc["doc_type"] == "passport"
    assert uploaded_doc["upload_source"] == "back_office_upload"

    detail = requests.get(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_auth_headers(officer_token),
        timeout=5,
    ).json()
    # M1.1 decoupling: stored alert status stays canonical ('open'); the
    # requirement carries 'under_review' and drives the derived display state.
    assert detail["status"] == "open"
    assert detail["status_key"] == "under_review"
    assert detail["document_refresh"]["request"]["status"] == "under_review"
    assert detail["document_refresh"]["request"]["linked_document"]["upload_source"] == "back_office_upload"
    assert any(item["action"] == "backoffice_replacement_uploaded" for item in detail["audit_history"])

    portal_tasks = requests.get(
        f"{base_url}/api/portal/applications/app_m3/enhanced-requirements",
        headers=_auth_headers(client_token),
        timeout=5,
    )
    assert portal_tasks.status_code == 200
    assert portal_tasks.json()["requirements"] == []

    app_docs = requests.get(
        f"{base_url}/api/applications/app_m3/documents?include_history=true",
        headers=_auth_headers(officer_token),
        timeout=5,
    )
    docs = app_docs.json()
    replacement = next(doc for doc in docs if doc["id"] == uploaded_doc["id"])
    expired = next(doc for doc in docs if doc["id"] == "doc_expired_m3")
    assert replacement["doc_type"] == "passport"
    assert replacement["review_status"] == "pending"
    assert expired["is_current"] in (0, False)
    assert expired["superseded_by_document_id"] == replacement["id"]


def test_officer_accepts_uploaded_document_and_resolves_alert(monitoring_doc_refresh_server):
    base_url, _db_module = monitoring_doc_refresh_server
    officer_token = _token("admin_m3", "admin", "Admin M3")
    client_token = _token("client_m3", "client", "Monitoring Three Client Ltd", "client")

    assert _request_updated_document(base_url, officer_token).status_code == 200
    tasks = requests.get(
        f"{base_url}/api/portal/applications/app_m3/enhanced-requirements",
        headers=_auth_headers(client_token),
        timeout=5,
    ).json()["requirements"]
    upload = _upload_client_document(base_url, client_token, tasks[0]["id"])
    assert upload.status_code == 201, upload.text

    accept = requests.patch(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_json_headers(officer_token),
        json={"action": "accept_updated_document", "note": "Renewal document is acceptable."},
        timeout=5,
    )
    assert accept.status_code == 200, accept.text
    assert accept.json()["new_status"] == "resolved"

    detail = requests.get(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_auth_headers(officer_token),
        timeout=5,
    ).json()
    assert detail["status"] == "resolved"
    assert detail["document_refresh"]["request"]["status"] == "accepted"
    assert any(item["action"] == "updated_document_accepted" for item in detail["audit_history"])
    assert any(item["action"] == "monitoring_alert_resolved" for item in detail["audit_history"])


def test_accept_requires_uploaded_replacement_document(monitoring_doc_refresh_server):
    base_url, _db_module = monitoring_doc_refresh_server
    officer_token = _token("admin_m3", "admin", "Admin M3")

    assert _request_updated_document(base_url, officer_token).status_code == 200
    accept = requests.patch(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_json_headers(officer_token),
        json={"action": "accept_updated_document", "note": "Accept before upload should fail."},
        timeout=5,
    )
    assert accept.status_code == 409
    assert "uploaded replacement document" in accept.json()["error"].lower()


def test_reject_requires_reason_and_reopens_request(monitoring_doc_refresh_server):
    base_url, _db_module = monitoring_doc_refresh_server
    officer_token = _token("admin_m3", "admin", "Admin M3")
    client_token = _token("client_m3", "client", "Monitoring Three Client Ltd", "client")

    assert _request_updated_document(base_url, officer_token).status_code == 200
    task = requests.get(
        f"{base_url}/api/portal/applications/app_m3/enhanced-requirements",
        headers=_auth_headers(client_token),
        timeout=5,
    ).json()["requirements"][0]
    upload = _upload_client_document(base_url, client_token, task["id"])
    assert upload.status_code == 201, upload.text

    no_reason = requests.patch(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_json_headers(officer_token),
        json={"action": "reject_updated_document", "note": ""},
        timeout=5,
    )
    assert no_reason.status_code == 400

    reject = requests.patch(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_json_headers(officer_token),
        json={"action": "reject_updated_document", "note": "Expiry date is not visible."},
        timeout=5,
    )
    assert reject.status_code == 200, reject.text
    assert reject.json()["new_status"] == "document_requested"

    detail = requests.get(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_auth_headers(officer_token),
        timeout=5,
    ).json()
    assert detail["document_refresh"]["request"]["status"] == "rejected"
    assert any(item["action"] == "updated_document_rejected" for item in detail["audit_history"])

    docs = requests.get(
        f"{base_url}/api/applications/app_m3/documents?include_history=true",
        headers=_auth_headers(officer_token),
        timeout=5,
    ).json()
    rejected_doc_id = detail["document_refresh"]["request"]["linked_document"]["id"]
    rejected_doc = next(doc for doc in docs if doc["id"] == rejected_doc_id)
    old_doc = next(doc for doc in docs if doc["id"] == "doc_expired_m3")
    assert rejected_doc["review_status"] == "rejected"
    assert rejected_doc["is_current"] in (0, False)
    assert old_doc["is_current"] in (1, True)


def test_waive_requires_reason_and_authorized_role(monitoring_doc_refresh_server):
    base_url, _db_module = monitoring_doc_refresh_server
    admin_token = _token("admin_m3", "admin", "Admin M3")
    co_token = _token("co_m3", "co", "CO M3")

    assert _request_updated_document(base_url, admin_token).status_code == 200

    co_waive = requests.patch(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_json_headers(co_token),
        json={"action": "waive_updated_document", "note": "Temporary waiver."},
        timeout=5,
    )
    assert co_waive.status_code == 403

    no_reason = requests.patch(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_json_headers(admin_token),
        json={"action": "waive_updated_document", "note": ""},
        timeout=5,
    )
    assert no_reason.status_code == 400

    waived = requests.patch(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_json_headers(admin_token),
        json={"action": "waive_updated_document", "note": "Passport renewal not required for dormant client."},
        timeout=5,
    )
    assert waived.status_code == 200, waived.text
    assert waived.json()["new_status"] == "waived"
