import json
import os
import socket
import sys
import tempfile
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
def monitoring_api_server():
    db_path = os.path.join(tempfile.gettempdir(), f"onboarda_monitoring_sprint2_{os.getpid()}_{time.time_ns()}.db")
    os.environ["DB_PATH"] = db_path
    os.environ["DATABASE_URL"] = ""

    import importlib
    import db as db_module

    importlib.reload(db_module)
    db_module._DB_PATH = db_path
    db_module.init_db()
    conn = db_module.get_db()
    db_module.seed_initial_data(conn)
    _seed_monitoring_users_and_alerts(conn)
    conn.commit()
    conn.close()

    from server import make_app

    app = make_app()
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


def _seed_monitoring_users_and_alerts(conn):
    users = [
        ("admin_s2", "admin-s2@example.test", "Admin S2", "admin"),
        ("sco_s2", "sco-s2@example.test", "SCO S2", "sco"),
        ("co_s2", "co-s2@example.test", "CO S2", "co"),
    ]
    for user_id, email, name, role in users:
        conn.execute(
            "INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, 'active')",
            (user_id, email, "unused", name, role),
        )
    try:
        conn.execute(
            "INSERT OR REPLACE INTO applications (id, ref, company_name, status) VALUES (?, ?, ?, ?)",
            ("app_s2", "ARF-S2", "Sprint Two Client Ltd", "approved"),
        )
    except Exception:
        conn.execute("INSERT OR IGNORE INTO applications (id, status) VALUES (?, ?)", ("app_s2", "approved"))
    conn.execute("DELETE FROM monitoring_alert_evidence WHERE monitoring_alert_id IN (?, ?)", (9201, 9202))
    conn.execute("DELETE FROM monitoring_alerts WHERE id IN (?, ?)", (9201, 9202))
    conn.execute(
        """
        INSERT INTO monitoring_alerts
            (id, application_id, client_name, alert_type, severity, detected_by,
             summary, source_reference, status, provider, case_identifier, discovered_via)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            9201,
            "app_s2",
            "Sprint Two Client Ltd",
            "media",
            "High",
            "complyadvantage",
            "CA case case-s2 surfaced 1 match(es); top indicator: media for customer Sprint Two Client Ltd",
            json.dumps({"provider": "complyadvantage", "case_identifier": "case-s2", "screening_subject": {"kind": "entity", "scope": "entity"}, "subject_scope": "entity"}),
            "open",
            "complyadvantage",
            "case-s2",
            "manual",
        ),
    )
    conn.execute(
        """
        INSERT INTO monitoring_alert_evidence
            (monitoring_alert_id, application_id, provider, case_identifier, alert_identifier,
             risk_identifier, profile_identifier, evidence_type, matched_subject_name,
             relationship_to_client, match_category, risk_indicator, match_confidence,
             source_title, source_name, source_url, source_url_available, publication_date,
             snippet, evidence_json, raw_provider_reference, evidence_status, evidence_hash, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            9201,
            "app_s2",
            "complyadvantage",
            "case-s2",
            "alert-s2",
            "risk-s2",
            "profile-s2",
            "adverse_media",
            "Sprint Two Client Ltd",
            "Company",
            "Adverse Media",
            "Adverse Media",
            "0.92",
            "Provider article title",
            "Provider News",
            "",
            0,
            "2026-05-01",
            "Provider snippet",
            json.dumps({"indicator": {"type": "CAMediaIndicator"}}),
            json.dumps({"risk_identifier": "risk-s2"}),
            "fetched",
            "hash-s2-evidence",
            "2026-06-09T00:00:00Z",
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
            9202,
            "app_s2",
            "Sprint Two Client Ltd",
            "document_expired",
            "medium",
            "Document Health Monitor",
            "Passport expired",
            json.dumps({"document_type": "Passport", "expiry_date": "2026-01-01"}),
            "open",
            "document_health",
        ),
    )


def _token(user_id, role, name):
    from auth import create_token

    return create_token(user_id, role, name, "officer")


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_alert_detail_returns_owner_application_and_audit_history(monitoring_api_server):
    base_url, _db_module = monitoring_api_server
    token = _token("admin_s2", "admin", "Admin S2")

    start = requests.patch(
        f"{base_url}/api/monitoring/alerts/9201",
        headers=_headers(token),
        json={"action": "start_review"},
        timeout=5,
    )
    assert start.status_code == 200

    detail = requests.get(
        f"{base_url}/api/monitoring/alerts/9201",
        headers=_headers(token),
        timeout=5,
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["application_ref"] == "ARF-S2"
    assert body["application_company_name"] == "Sprint Two Client Ltd"
    assert body["owner_name"] == "Admin S2"
    assert any(item["action"] == "monitoring.alert.review_started" for item in body["audit_history"])
    assert body["provider_evidence"][0]["source_title"] == "Provider article title"
    assert body["provider_evidence"][0]["source_url_available"] is False
    assert body["provider_evidence"][0]["raw_provider_reference"]["risk_identifier"] == "risk-s2"


def test_material_outcome_requires_note_server_side(monitoring_api_server):
    base_url, _db_module = monitoring_api_server
    token = _token("admin_s2", "admin", "Admin S2")

    resp = requests.patch(
        f"{base_url}/api/monitoring/alerts/9201",
        headers=_headers(token),
        json={"action": "save_decision", "outcome": "false_positive", "note": ""},
        timeout=5,
    )
    assert resp.status_code == 400
    assert "note is required" in resp.json()["error"].lower()


def test_admin_assigns_alert_to_another_officer_and_writes_audit(monitoring_api_server):
    base_url, db_module = monitoring_api_server
    token = _token("admin_s2", "admin", "Admin S2")

    resp = requests.patch(
        f"{base_url}/api/monitoring/alerts/9202",
        headers=_headers(token),
        json={"action": "assign", "assignee_id": "co_s2", "assignment_note": "Sprint 2 assignment"},
        timeout=5,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["new_status"] == "assigned"
    assert body["result"]["owner_id"] == "co_s2"

    conn = db_module.get_db()
    try:
        alert = conn.execute("SELECT reviewed_by, status FROM monitoring_alerts WHERE id = 9202").fetchone()
        assert alert["reviewed_by"] == "co_s2"
        audit = conn.execute(
            "SELECT detail FROM audit_log WHERE target = ? AND action = ? ORDER BY id DESC LIMIT 1",
            ("monitoring_alert:9202", "monitoring.alert.assigned"),
        ).fetchone()
        assert audit is not None
        detail = json.loads(audit["detail"])
        assert detail["previous_owner"] is None
        assert detail["new_owner"] == "co_s2"
        assert detail["assigned_by"] == "admin_s2"
        assert detail["note"] == "Sprint 2 assignment"
    finally:
        conn.close()


def test_co_cannot_assign_alert_to_another_officer(monitoring_api_server):
    base_url, _db_module = monitoring_api_server
    token = _token("co_s2", "co", "CO S2")

    resp = requests.patch(
        f"{base_url}/api/monitoring/alerts/9202",
        headers=_headers(token),
        json={"action": "assign", "assignee_id": "sco_s2"},
        timeout=5,
    )
    assert resp.status_code == 403
    assert "only administrator and senior co" in resp.json()["error"].lower()
