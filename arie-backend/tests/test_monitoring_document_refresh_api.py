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

    from tests.conftest import shutdown_test_http_server
    shutdown_test_http_server(thread, server_ref)


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
    from regulated_deletion import sanctioned_delete_context

    with sanctioned_delete_context(
        "fixture_cleanup_nonprod",
        actor_id="pytest:monitoring-document-refresh",
        role="system",
        reason="Reset the isolated synthetic Monitoring document-refresh fixture.",
        allowed_tables=(
            "application_enhanced_requirements",
            "audit_log",
            "monitoring_alerts",
            "monitoring_alert_review_requests",
        ),
        environment="testing",
        is_fixture=True,
        confirmed=True,
    ):
        conn.execute(
            "DELETE FROM monitoring_alert_review_requests WHERE alert_id = ?",
            (9301,),
        )
        conn.execute("DELETE FROM application_enhanced_requirements WHERE application_id = ? OR monitoring_alert_id = ?", ("app_m3", 9301))
        conn.execute("DELETE FROM client_notifications WHERE application_id = ?", ("app_m3",))
        conn.execute("DELETE FROM notifications WHERE message LIKE ?", ("%9301%",))
        conn.execute("DELETE FROM audit_log WHERE target = ? OR target = ?", ("monitoring_alert:9301", "ARF-M3"))
        conn.execute("DELETE FROM monitoring_alerts WHERE id = 9301")
        conn.execute("DELETE FROM documents WHERE application_id = ?", ("app_m3",))
        conn.execute("DELETE FROM directors WHERE application_id = ?", ("app_m3",))
        conn.execute(
            "DELETE FROM applications WHERE id IN (?, ?)",
            ("app_m3", "app_cross_m3"),
        )
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
        INSERT INTO directors
            (id, application_id, person_key, full_name)
        VALUES ('director_m3', 'app_m3', 'director_m3', 'Monitoring Director')
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO documents
            (id, application_id, person_id, person_type, doc_type, doc_name,
             file_path, file_size, mime_type, slot_key, is_current, version,
             expiry_date, verification_status, review_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, 'verified', 'accepted')
        """,
        (
            "doc_expired_m3",
            "app_m3",
            "director_m3",
            "director",
            "passport",
            "Expired passport.pdf",
            "/tmp/expired-passport.pdf",
            20,
            "application/pdf",
            "person:director:director_m3:passport",
            "2026-01-01",
        ),
    )
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


@pytest.mark.parametrize(
    ("outcome", "requested_outcome", "status_code"),
    (
        ("accept", "waive_with_reason", 409),
        ("waive", "accept_updated_document", 409),
        ("waive", "mark_already_updated", 409),
        ("reject", "accept_updated_document", 400),
    ),
)
def test_document_transition_rejects_mismatched_requested_outcome_before_write(
    monkeypatch,
    outcome,
    requested_outcome,
    status_code,
):
    import monitoring_document_refresh as refresh

    transition_calls = []
    monkeypatch.setattr(
        refresh.monitoring_state_machine,
        "transition_alert_status",
        lambda *_args, **_kwargs: transition_calls.append(True),
    )

    with pytest.raises(refresh.MonitoringDocumentRefreshError) as exc_info:
        refresh._transition_document_alert(
            object(),
            alert={"id": 9301},
            expected_status="open",
            outcome=outcome,
            requested_outcome=requested_outcome,
            request_id=73,
            document_id="doc-73",
            note="Controlled document decision.",
            user={"sub": "admin_m3", "role": "admin"},
        )

    assert exc_info.value.status_code == status_code
    assert transition_calls == []


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


def _prepare_accepted_document_refresh(base_url):
    officer_token = _token("admin_m3", "admin", "Admin M3")
    client_token = _token(
        "client_m3",
        "client",
        "Monitoring Three Client Ltd",
        "client",
    )
    assert _request_updated_document(base_url, officer_token).status_code == 200
    task = requests.get(
        f"{base_url}/api/portal/applications/app_m3/enhanced-requirements",
        headers=_auth_headers(client_token),
        timeout=5,
    ).json()["requirements"][0]
    assert _upload_client_document(
        base_url, client_token, task["id"]
    ).status_code == 201
    accepted = requests.patch(
        f"{base_url}/api/applications/app_m3/enhanced-requirements/{task['id']}",
        headers=_json_headers(officer_token),
        json={
            "status": "accepted",
            "review_notes": "Accepted from the application requirement review.",
        },
        timeout=5,
    )
    assert accepted.status_code == 200, accepted.text
    return officer_token, task


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
    base_url, db_module = monitoring_doc_refresh_server
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

    conn = db_module.get_db()
    try:
        canonical_rows = conn.execute(
            """
            SELECT detail, before_state, after_state, entry_hash
              FROM audit_log
             WHERE target = ?
               AND action = 'monitoring.alert.status_transition'
            """,
            ("monitoring_alert:9301",),
        ).fetchall()
        assert len(canonical_rows) == 1
        canonical = dict(canonical_rows[0])
        evidence = json.loads(canonical["detail"])
        assert evidence["previous_status"] == "open"
        assert evidence["new_status"] == "resolved"
        assert evidence["source_workflow"] == "kyc_documents"
        assert evidence["reason_code"] == "document_accepted"
        assert evidence["transition_matrix_version"] == "monitoring_alert_state_machine_v1"
        assert evidence["evidence"]["document_request_id"] == tasks[0]["id"]
        assert evidence["evidence"]["document_id"] == upload.json()["document"]["id"]
        control = conn.execute(
            "SELECT id, state, requested_outcome, second_review_bypassed "
            "FROM monitoring_alert_review_requests WHERE alert_id = ?",
            (9301,),
        ).fetchone()
        assert control["state"] == "senior_cleared"
        assert control["requested_outcome"] == "accept_updated_document"
        assert control["second_review_bypassed"] in (1, True)
        assert evidence["evidence"]["review_request_id"] == control["id"]
        assert json.loads(canonical["before_state"]) == {"status": "open"}
        assert json.loads(canonical["after_state"]) == {"status": "resolved"}
        assert canonical["entry_hash"]
    finally:
        conn.close()


def test_controlled_document_acceptance_is_pending_then_atomic(
    monitoring_doc_refresh_server,
):
    base_url, db_module = monitoring_doc_refresh_server
    admin_token = _token("admin_m3", "admin", "Admin M3")
    co_token = _token("co_m3", "co", "CO M3")
    client_token = _token(
        "client_m3",
        "client",
        "Monitoring Three Client Ltd",
        "client",
    )

    assert _request_updated_document(base_url, admin_token).status_code == 200
    task = requests.get(
        f"{base_url}/api/portal/applications/app_m3/enhanced-requirements",
        headers=_auth_headers(client_token),
        timeout=5,
    ).json()["requirements"][0]
    upload = _upload_client_document(base_url, client_token, task["id"])
    assert upload.status_code == 201, upload.text

    requested = requests.patch(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_json_headers(co_token),
        json={
            "action": "accept_updated_document",
            "note": "Replacement passport evidence reviewed by the maker.",
        },
        timeout=5,
    )
    assert requested.status_code == 200, requested.text
    assert requested.json()["status"] == "review_requested"
    review_request_id = requested.json()["result"]["review_request_id"]

    conn = db_module.get_db()
    try:
        alert = conn.execute(
            "SELECT status FROM monitoring_alerts WHERE id = 9301"
        ).fetchone()
        requirement = conn.execute(
            "SELECT status FROM application_enhanced_requirements WHERE id = ?",
            (task["id"],),
        ).fetchone()
        replacement = conn.execute(
            "SELECT review_status FROM documents WHERE id = ?",
            (upload.json()["document"]["id"],),
        ).fetchone()
        control = conn.execute(
            "SELECT state, requested_outcome FROM "
            "monitoring_alert_review_requests WHERE id = ?",
            (review_request_id,),
        ).fetchone()
        assert alert["status"] == "open"
        assert requirement["status"] == "uploaded"
        assert replacement["review_status"] == "pending"
        assert control["state"] == "pending"
        assert control["requested_outcome"] == "accept_updated_document"
    finally:
        conn.close()

    approved = requests.post(
        f"{base_url}/api/monitoring/review-requests/"
        f"{review_request_id}/approve",
        headers=_json_headers(admin_token),
        json={"approval_note": "Independent senior acceptance approved."},
        timeout=5,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["new_status"] == "resolved"

    conn = db_module.get_db()
    try:
        assert conn.execute(
            "SELECT status FROM monitoring_alerts WHERE id = 9301"
        ).fetchone()["status"] == "resolved"
        assert conn.execute(
            "SELECT status FROM application_enhanced_requirements WHERE id = ?",
            (task["id"],),
        ).fetchone()["status"] == "accepted"
        assert conn.execute(
            "SELECT review_status FROM documents WHERE id = ?",
            (upload.json()["document"]["id"],),
        ).fetchone()["review_status"] == "accepted"
        assert conn.execute(
            "SELECT state FROM monitoring_alert_review_requests WHERE id = ?",
            (review_request_id,),
        ).fetchone()["state"] == "approved"
        assert conn.execute(
            "SELECT COUNT(*) AS count FROM audit_log WHERE target = ? "
            "AND action = 'monitoring.alert.status_transition'",
            ("monitoring_alert:9301",),
        ).fetchone()["count"] == 1
    finally:
        conn.close()


def test_mark_already_updated_alias_preserves_control_outcome_and_reason(
    monitoring_doc_refresh_server,
):
    base_url, db_module = monitoring_doc_refresh_server
    admin_token = _token("admin_m3", "admin", "Admin M3")
    co_token = _token("co_m3", "co", "CO M3")
    client_token = _token(
        "client_m3",
        "client",
        "Monitoring Three Client Ltd",
        "client",
    )
    assert _request_updated_document(base_url, admin_token).status_code == 200
    task = requests.get(
        f"{base_url}/api/portal/applications/app_m3/enhanced-requirements",
        headers=_auth_headers(client_token),
        timeout=5,
    ).json()["requirements"][0]
    assert _upload_client_document(
        base_url, client_token, task["id"]
    ).status_code == 201

    requested = requests.patch(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_json_headers(co_token),
        json={
            "action": "save_decision",
            "outcome": "mark_already_updated",
            "note": "Replacement was already received and reviewed.",
        },
        timeout=5,
    )
    assert requested.status_code == 200, requested.text
    review_request_id = requested.json()["result"]["review_request_id"]

    approved = requests.post(
        f"{base_url}/api/monitoring/review-requests/"
        f"{review_request_id}/approve",
        headers=_json_headers(admin_token),
        json={"approval_note": "Independent review confirms the replacement."},
        timeout=5,
    )
    assert approved.status_code == 200, approved.text

    conn = db_module.get_db()
    try:
        control = conn.execute(
            "SELECT requested_outcome, state FROM "
            "monitoring_alert_review_requests WHERE id = ?",
            (review_request_id,),
        ).fetchone()
        transition = conn.execute(
            "SELECT detail FROM audit_log WHERE target = ? "
            "AND action = 'monitoring.alert.status_transition'",
            ("monitoring_alert:9301",),
        ).fetchone()
    finally:
        conn.close()
    assert control["requested_outcome"] == "mark_already_updated"
    assert control["state"] == "approved"
    assert json.loads(transition["detail"])["reason_code"] == (
        "document_already_updated"
    )


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
    base_url, db_module = monitoring_doc_refresh_server
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
    assert detail["status"] == "open"
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

    conn = db_module.get_db()
    try:
        assert conn.execute(
            """
            SELECT COUNT(*) AS c
              FROM audit_log
             WHERE target = ?
               AND action = 'monitoring.alert.status_transition'
            """,
            ("monitoring_alert:9301",),
        ).fetchone()["c"] == 0
    finally:
        conn.close()


def test_waive_requires_reason_and_controlled_senior_approval(
    monitoring_doc_refresh_server,
):
    base_url, db_module = monitoring_doc_refresh_server
    admin_token = _token("admin_m3", "admin", "Admin M3")
    co_token = _token("co_m3", "co", "CO M3")

    assert _request_updated_document(base_url, admin_token).status_code == 200

    no_reason = requests.patch(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_json_headers(admin_token),
        json={"action": "waive_updated_document", "note": ""},
        timeout=5,
    )
    assert no_reason.status_code == 400

    co_waive = requests.patch(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_json_headers(co_token),
        json={"action": "waive_updated_document", "note": "Temporary waiver."},
        timeout=5,
    )
    assert co_waive.status_code == 200, co_waive.text
    assert co_waive.json()["status"] == "review_requested"
    request_id = co_waive.json()["result"]["review_request_id"]

    waived = requests.post(
        f"{base_url}/api/monitoring/review-requests/{request_id}/approve",
        headers=_json_headers(admin_token),
        json={"approval_note": "Approve the documented temporary waiver."},
        timeout=5,
    )
    assert waived.status_code == 200, waived.text
    assert waived.json()["new_status"] == "waived"

    conn = db_module.get_db()
    try:
        row = conn.execute(
            """
            SELECT detail
              FROM audit_log
             WHERE target = ?
               AND action = 'monitoring.alert.status_transition'
            """,
            ("monitoring_alert:9301",),
        ).fetchone()
        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["previous_status"] == "open"
        assert detail["new_status"] == "waived"
        assert detail["actor_role"] == "admin"
        assert detail["reason_code"] == "document_waived"
        assert detail["evidence"]["document_request_id"]
        assert detail["evidence"]["waiver_reason"] == (
            "Temporary waiver."
        )
    finally:
        conn.close()


def test_oversized_document_waiver_reason_creates_no_control_or_transition(
    monitoring_doc_refresh_server,
):
    base_url, db_module = monitoring_doc_refresh_server
    admin_token = _token("admin_m3", "admin", "Admin M3")
    co_token = _token("co_m3", "co", "CO M3")

    assert _request_updated_document(base_url, admin_token).status_code == 200

    refused = requests.patch(
        f"{base_url}/api/monitoring/alerts/9301",
        headers=_json_headers(co_token),
        json={"action": "waive_updated_document", "note": "x" * 1001},
        timeout=5,
    )
    assert refused.status_code == 400, refused.text
    assert "1000-character limit" in refused.json()["error"]

    conn = db_module.get_db()
    try:
        assert conn.execute(
            "SELECT status FROM monitoring_alerts WHERE id = 9301"
        ).fetchone()["status"] == "open"
        assert conn.execute(
            "SELECT status FROM application_enhanced_requirements "
            "WHERE monitoring_alert_id = 9301"
        ).fetchone()["status"] == "requested"
        assert conn.execute(
            "SELECT COUNT(*) AS count FROM monitoring_alert_review_requests "
            "WHERE alert_id = 9301"
        ).fetchone()["count"] == 0
        assert conn.execute(
            "SELECT COUNT(*) AS count FROM audit_log WHERE target = ? "
            "AND action IN ("
            "'monitoring.alert.dismissal_requested', "
            "'monitoring.alert.status_transition'"
            ")",
            ("monitoring_alert:9301",),
        ).fetchone()["count"] == 0
    finally:
        conn.close()


def test_metadata_audit_failure_rolls_back_requirement_document_status_and_canonical_transition(
    monitoring_doc_refresh_server,
):
    from monitoring_document_refresh import (
        prepare_document_refresh_clearance,
        review_document_refresh,
    )

    base_url, db_module = monitoring_doc_refresh_server
    officer_token = _token("admin_m3", "admin", "Admin M3")
    client_token = _token(
        "client_m3",
        "client",
        "Monitoring Three Client Ltd",
        "client",
    )

    assert _request_updated_document(base_url, officer_token).status_code == 200
    task = requests.get(
        f"{base_url}/api/portal/applications/app_m3/enhanced-requirements",
        headers=_auth_headers(client_token),
        timeout=5,
    ).json()["requirements"][0]
    upload = _upload_client_document(base_url, client_token, task["id"])
    assert upload.status_code == 201, upload.text
    replacement_id = upload.json()["document"]["id"]

    def fail_metadata_audit(*_args, **_kwargs):
        raise RuntimeError("injected metadata audit failure")

    conn = db_module.get_db()
    try:
        alert_before = dict(conn.execute(
            "SELECT * FROM monitoring_alerts WHERE id = ?",
            (9301,),
        ).fetchone())
        request_before = dict(conn.execute(
            "SELECT * FROM application_enhanced_requirements WHERE id = ?",
            (task["id"],),
        ).fetchone())
        disposition, control = prepare_document_refresh_clearance(
            conn,
            alert=alert_before,
            request=request_before,
            outcome="accept",
            requested_outcome="accept_updated_document",
            note="This outcome must roll back.",
            evidence_ref="",
            user={
                "sub": "admin_m3",
                "name": "Admin M3",
                "role": "admin",
            },
            audit_writer=lambda *_args, **_kwargs: None,
        )
        assert disposition == "execute"
        with pytest.raises(RuntimeError, match="injected metadata audit failure"):
            review_document_refresh(
                conn,
                9301,
                outcome="accept",
                note="This outcome must roll back.",
                user={
                    "sub": "admin_m3",
                    "name": "Admin M3",
                    "role": "admin",
                },
                audit_writer=fail_metadata_audit,
                requested_outcome="accept_updated_document",
                review_request_id=control["id"],
            )

        alert = conn.execute(
            "SELECT status, officer_action, resolved_at FROM monitoring_alerts WHERE id = ?",
            (9301,),
        ).fetchone()
        requirement = conn.execute(
            "SELECT status FROM application_enhanced_requirements WHERE id = ?",
            (task["id"],),
        ).fetchone()
        document = conn.execute(
            "SELECT review_status FROM documents WHERE id = ?",
            (replacement_id,),
        ).fetchone()
        canonical_count = conn.execute(
            """
            SELECT COUNT(*) AS c
              FROM audit_log
             WHERE target = ?
               AND action = 'monitoring.alert.status_transition'
            """,
            ("monitoring_alert:9301",),
        ).fetchone()["c"]

        assert alert["status"] == "open"
        assert alert["officer_action"] == "client_document_uploaded"
        assert alert["resolved_at"] is None
        assert requirement["status"] == "uploaded"
        assert document["review_status"] == "pending"
        assert canonical_count == 0
        assert conn.execute(
            "SELECT COUNT(*) AS count FROM monitoring_alert_review_requests "
            "WHERE alert_id = ?",
            (9301,),
        ).fetchone()["count"] == 0
    finally:
        conn.close()


def test_application_requirement_acceptance_sync_uses_canonical_transition(
    monitoring_doc_refresh_server,
):
    base_url, db_module = monitoring_doc_refresh_server
    officer_token = _token("admin_m3", "admin", "Admin M3")
    client_token = _token(
        "client_m3",
        "client",
        "Monitoring Three Client Ltd",
        "client",
    )

    assert _request_updated_document(base_url, officer_token).status_code == 200
    task = requests.get(
        f"{base_url}/api/portal/applications/app_m3/enhanced-requirements",
        headers=_auth_headers(client_token),
        timeout=5,
    ).json()["requirements"][0]
    assert _upload_client_document(
        base_url, client_token, task["id"]
    ).status_code == 201

    accepted = requests.patch(
        f"{base_url}/api/applications/app_m3/enhanced-requirements/{task['id']}",
        headers=_json_headers(officer_token),
        json={
            "status": "accepted",
            "review_notes": "Accepted from the application requirement review.",
        },
        timeout=5,
    )
    assert accepted.status_code == 200, accepted.text

    conn = db_module.get_db()
    try:
        alert = conn.execute(
            "SELECT status, resolved_at FROM monitoring_alerts WHERE id = ?",
            (9301,),
        ).fetchone()
        transition = conn.execute(
            """
            SELECT detail
              FROM audit_log
             WHERE target = ?
               AND action = 'monitoring.alert.status_transition'
            """,
            ("monitoring_alert:9301",),
        ).fetchone()
        assert alert["status"] == "resolved"
        assert alert["resolved_at"]
        assert transition is not None
        detail = json.loads(transition["detail"])
        assert detail["reason_code"] == "document_accepted"
        assert detail["evidence"]["document_request_id"] == task["id"]
        assert detail["evidence"]["document_id"]
        control = conn.execute(
            "SELECT id, state, requested_outcome FROM "
            "monitoring_alert_review_requests WHERE alert_id = ?",
            (9301,),
        ).fetchone()
        assert control["state"] == "senior_cleared"
        assert control["requested_outcome"] == "accept_updated_document"
        assert detail["evidence"]["review_request_id"] == control["id"]
    finally:
        conn.close()


@pytest.mark.parametrize("invalid_application_id", ("foreign_application", None))
def test_requirement_sync_rejects_foreign_application_before_any_mutation(
    monitoring_doc_refresh_server,
    invalid_application_id,
):
    from monitoring_document_refresh import (
        MonitoringDocumentRefreshError,
        sync_requirement_review_to_monitoring_alert,
    )

    base_url, db_module = monitoring_doc_refresh_server
    admin_token = _token("admin_m3", "admin", "Admin M3")
    client_token = _token(
        "client_m3",
        "client",
        "Monitoring Three Client Ltd",
        "client",
    )

    assert _request_updated_document(base_url, admin_token).status_code == 200
    task = requests.get(
        f"{base_url}/api/portal/applications/app_m3/enhanced-requirements",
        headers=_auth_headers(client_token),
        timeout=5,
    ).json()["requirements"][0]
    upload = _upload_client_document(base_url, client_token, task["id"])
    assert upload.status_code == 201, upload.text
    replacement_id = upload.json()["document"]["id"]

    conn = db_module.get_db()
    try:
        requirement = dict(
            conn.execute(
                "SELECT * FROM application_enhanced_requirements WHERE id = ?",
                (task["id"],),
            ).fetchone()
        )
        requirement.update(
            {
                "status": "accepted",
                "application_id": invalid_application_id,
                "review_notes": "A foreign requirement must fail closed.",
            }
        )

        with pytest.raises(MonitoringDocumentRefreshError) as exc_info:
            sync_requirement_review_to_monitoring_alert(
                conn,
                requirement,
                user={"sub": "admin_m3", "role": "admin"},
                audit_writer=lambda *_args, **_kwargs: None,
            )

        assert exc_info.value.status_code == 409
        assert "not linked" in str(exc_info.value).lower()
        assert conn.execute(
            "SELECT status FROM monitoring_alerts WHERE id = ?",
            (9301,),
        ).fetchone()["status"] == "open"
        assert conn.execute(
            "SELECT status FROM application_enhanced_requirements WHERE id = ?",
            (task["id"],),
        ).fetchone()["status"] == "uploaded"
        assert conn.execute(
            "SELECT review_status FROM documents WHERE id = ?",
            (replacement_id,),
        ).fetchone()["review_status"] == "pending"
        assert conn.execute(
            "SELECT COUNT(*) AS count FROM audit_log WHERE target = ? "
            "AND action = 'monitoring.alert.status_transition'",
            ("monitoring_alert:9301",),
        ).fetchone()["count"] == 0
    finally:
        conn.close()


def test_application_requirement_controlled_acceptance_stays_unchanged_pending_review(
    monitoring_doc_refresh_server,
):
    base_url, db_module = monitoring_doc_refresh_server
    admin_token = _token("admin_m3", "admin", "Admin M3")
    co_token = _token("co_m3", "co", "CO M3")
    client_token = _token(
        "client_m3",
        "client",
        "Monitoring Three Client Ltd",
        "client",
    )

    assert _request_updated_document(base_url, admin_token).status_code == 200
    task = requests.get(
        f"{base_url}/api/portal/applications/app_m3/enhanced-requirements",
        headers=_auth_headers(client_token),
        timeout=5,
    ).json()["requirements"][0]
    assert _upload_client_document(
        base_url, client_token, task["id"]
    ).status_code == 201

    requested = requests.patch(
        f"{base_url}/api/applications/app_m3/"
        f"enhanced-requirements/{task['id']}",
        headers=_json_headers(co_token),
        json={
            "status": "accepted",
            "review_notes": "Maker reviewed the replacement passport.",
        },
        timeout=5,
    )
    assert requested.status_code == 200, requested.text
    assert requested.json()["status"] == "review_requested"
    review_request_id = requested.json()["result"]["review_request_id"]

    conn = db_module.get_db()
    try:
        assert conn.execute(
            "SELECT status FROM application_enhanced_requirements WHERE id = ?",
            (task["id"],),
        ).fetchone()["status"] == "uploaded"
        assert conn.execute(
            "SELECT status FROM monitoring_alerts WHERE id = 9301"
        ).fetchone()["status"] == "open"
        assert conn.execute(
            "SELECT state FROM monitoring_alert_review_requests WHERE id = ?",
            (review_request_id,),
        ).fetchone()["state"] == "pending"
    finally:
        conn.close()

    approved = requests.post(
        f"{base_url}/api/monitoring/review-requests/"
        f"{review_request_id}/approve",
        headers=_json_headers(admin_token),
        json={"approval_note": "Independent application-review approval."},
        timeout=5,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["new_status"] == "resolved"


def test_cross_application_requirement_route_cannot_create_review_control(
    monitoring_doc_refresh_server,
):
    base_url, db_module = monitoring_doc_refresh_server
    admin_token = _token("admin_m3", "admin", "Admin M3")
    co_token = _token("co_m3", "co", "CO M3")
    client_token = _token(
        "client_m3",
        "client",
        "Monitoring Three Client Ltd",
        "client",
    )

    conn = db_module.get_db()
    try:
        conn.execute(
            """
            INSERT INTO applications
                (id, ref, company_name, status, risk_level)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "app_cross_m3",
                "ARF-CROSS-M3",
                "Unrelated Application Ltd",
                "approved",
                "LOW",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    assert _request_updated_document(base_url, admin_token).status_code == 200
    task = requests.get(
        f"{base_url}/api/portal/applications/app_m3/enhanced-requirements",
        headers=_auth_headers(client_token),
        timeout=5,
    ).json()["requirements"][0]
    assert _upload_client_document(
        base_url, client_token, task["id"]
    ).status_code == 201

    conn = db_module.get_db()
    try:
        review_count_before = conn.execute(
            "SELECT COUNT(*) AS c FROM monitoring_alert_review_requests "
            "WHERE alert_id = ?",
            (9301,),
        ).fetchone()["c"]
        audit_count_before = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE target = ?",
            ("monitoring_alert:9301",),
        ).fetchone()["c"]
    finally:
        conn.close()

    response = requests.patch(
        f"{base_url}/api/applications/app_cross_m3/"
        f"enhanced-requirements/{task['id']}",
        headers=_json_headers(co_token),
        json={
            "status": "accepted",
            "review_notes": "This cross-application route must be rejected.",
        },
        timeout=5,
    )
    assert response.status_code == 404, response.text

    conn = db_module.get_db()
    try:
        requirement = conn.execute(
            "SELECT status FROM application_enhanced_requirements WHERE id = ?",
            (task["id"],),
        ).fetchone()
        alert = conn.execute(
            "SELECT status FROM monitoring_alerts WHERE id = ?",
            (9301,),
        ).fetchone()
        assert requirement["status"] == "uploaded"
        assert alert["status"] == "open"
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM monitoring_alert_review_requests "
            "WHERE alert_id = ?",
            (9301,),
        ).fetchone()["c"] == review_count_before
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE target = ?",
            ("monitoring_alert:9301",),
        ).fetchone()["c"] == audit_count_before
    finally:
        conn.close()


def test_document_review_approval_returns_safe_4xx_when_link_is_stale(
    monitoring_doc_refresh_server,
):
    base_url, db_module = monitoring_doc_refresh_server
    admin_token = _token("admin_m3", "admin", "Admin M3")
    co_token = _token("co_m3", "co", "CO M3")
    client_token = _token(
        "client_m3",
        "client",
        "Monitoring Three Client Ltd",
        "client",
    )

    assert _request_updated_document(base_url, admin_token).status_code == 200
    task = requests.get(
        f"{base_url}/api/portal/applications/app_m3/enhanced-requirements",
        headers=_auth_headers(client_token),
        timeout=5,
    ).json()["requirements"][0]
    assert _upload_client_document(
        base_url, client_token, task["id"]
    ).status_code == 201
    requested = requests.patch(
        f"{base_url}/api/applications/app_m3/"
        f"enhanced-requirements/{task['id']}",
        headers=_json_headers(co_token),
        json={
            "status": "accepted",
            "review_notes": "Maker reviewed the replacement passport.",
        },
        timeout=5,
    )
    assert requested.status_code == 200, requested.text
    review_request_id = requested.json()["result"]["review_request_id"]

    conn = db_module.get_db()
    try:
        conn.execute(
            "UPDATE application_enhanced_requirements "
            "SET monitoring_alert_id = NULL WHERE id = ?",
            (task["id"],),
        )
        conn.commit()
    finally:
        conn.close()

    approval = requests.post(
        f"{base_url}/api/monitoring/review-requests/"
        f"{review_request_id}/approve",
        headers=_json_headers(admin_token),
        json={"approval_note": "Independent approval on stale linkage."},
        timeout=5,
    )
    assert approval.status_code == 404, approval.text
    assert "linked" in approval.json()["error"].lower()

    conn = db_module.get_db()
    try:
        assert conn.execute(
            "SELECT state FROM monitoring_alert_review_requests WHERE id = ?",
            (review_request_id,),
        ).fetchone()["state"] == "pending"
        assert conn.execute(
            "SELECT status FROM monitoring_alerts WHERE id = ?",
            (9301,),
        ).fetchone()["status"] == "open"
        assert conn.execute(
            "SELECT status FROM application_enhanced_requirements WHERE id = ?",
            (task["id"],),
        ).fetchone()["status"] == "uploaded"
        assert conn.execute(
            """
            SELECT COUNT(*) AS c
              FROM audit_log
             WHERE target = ?
               AND action = 'monitoring.alert.status_transition'
            """,
            ("monitoring_alert:9301",),
        ).fetchone()["c"] == 0
        assert conn.execute(
            """
            SELECT COUNT(*) AS c
              FROM audit_log
             WHERE target = ?
               AND action = 'monitoring.alert.dismissal_blocked'
            """,
            ("monitoring_alert:9301",),
        ).fetchone()["c"] == 1
    finally:
        conn.close()


def test_terminal_requirement_note_edits_and_repeat_outcome_are_idempotent(
    monitoring_doc_refresh_server,
):
    base_url, db_module = monitoring_doc_refresh_server
    officer_token, task = _prepare_accepted_document_refresh(base_url)
    endpoint = (
        f"{base_url}/api/applications/app_m3/"
        f"enhanced-requirements/{task['id']}"
    )

    repeated = requests.patch(
        endpoint,
        headers=_json_headers(officer_token),
        json={
            "status": "accepted",
            "review_notes": "Acceptance note clarified without changing outcome.",
        },
        timeout=5,
    )
    assert repeated.status_code == 200, repeated.text

    note_only = requests.patch(
        endpoint,
        headers=_json_headers(officer_token),
        json={"review_notes": "Final metadata-only clarification."},
        timeout=5,
    )
    assert note_only.status_code == 200, note_only.text

    conn = db_module.get_db()
    try:
        requirement = conn.execute(
            "SELECT status, review_notes FROM application_enhanced_requirements "
            "WHERE id = ?",
            (task["id"],),
        ).fetchone()
        alert = conn.execute(
            "SELECT status FROM monitoring_alerts WHERE id = ?",
            (9301,),
        ).fetchone()
        canonical_count = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE target = ? "
            "AND action = 'monitoring.alert.status_transition'",
            ("monitoring_alert:9301",),
        ).fetchone()["c"]
        outcome_count = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE target = ? "
            "AND action = 'updated_document_accepted'",
            ("monitoring_alert:9301",),
        ).fetchone()["c"]

        assert requirement["status"] == "accepted"
        assert requirement["review_notes"] == "Final metadata-only clarification."
        assert alert["status"] == "resolved"
        assert canonical_count == 1
        assert outcome_count == 1
    finally:
        conn.close()


def test_terminal_linked_requirement_reopen_fails_and_rolls_back(
    monitoring_doc_refresh_server,
):
    base_url, db_module = monitoring_doc_refresh_server
    officer_token, task = _prepare_accepted_document_refresh(base_url)

    reopened = requests.patch(
        (
            f"{base_url}/api/applications/app_m3/"
            f"enhanced-requirements/{task['id']}"
        ),
        headers=_json_headers(officer_token),
        json={
            "status": "under_review",
            "reopen_reason": "A later concern requires a new controlled alert.",
        },
        timeout=5,
    )
    assert reopened.status_code == 409, reopened.text

    conn = db_module.get_db()
    try:
        requirement = conn.execute(
            "SELECT status FROM application_enhanced_requirements WHERE id = ?",
            (task["id"],),
        ).fetchone()
        alert = conn.execute(
            "SELECT status FROM monitoring_alerts WHERE id = ?",
            (9301,),
        ).fetchone()
        canonical_count = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE target = ? "
            "AND action = 'monitoring.alert.status_transition'",
            ("monitoring_alert:9301",),
        ).fetchone()["c"]

        assert requirement["status"] == "accepted"
        assert alert["status"] == "resolved"
        assert canonical_count == 1
    finally:
        conn.close()
