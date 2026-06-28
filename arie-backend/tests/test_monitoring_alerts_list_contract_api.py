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
def monitoring_list_server():
    db_path = os.path.join(
        tempfile.gettempdir(),
        f"onboarda_monitoring_list_contract_{os.getpid()}_{time.time_ns()}.db",
    )
    os.environ["DB_PATH"] = db_path
    os.environ["DATABASE_URL"] = ""

    import importlib
    import db as db_module

    importlib.reload(db_module)
    db_module._DB_PATH = db_path
    db_module.init_db()
    conn = db_module.get_db()
    _seed_users_applications_and_alerts(conn)
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

    loop = server_ref.get("loop")
    server = server_ref.get("server")
    if loop and server:
        loop.add_callback(server.stop)
        loop.add_callback(loop.stop)
    thread.join(timeout=2)


def _seed_users_applications_and_alerts(conn):
    for user_id, email, name, role in [
        ("admin_list", "admin-list@example.test", "Admin List", "admin"),
        ("sco_list", "sco-list@example.test", "SCO List", "sco"),
        ("co_list", "co-list@example.test", "CO List", "co"),
    ]:
        conn.execute(
            "INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, 'active')",
            (user_id, email, "unused", name, role),
        )

    for app_id, ref, name, is_fixture in [
        ("app_list_alpha", "ALPHA-REF", "Alpha Monitoring Ltd", 0),
        ("app_list_beta", "BETA-REF", "Beta Monitoring Ltd", 0),
        ("app_list_fixture", "ARF-2026-900099", "RegMind E2E Fixture Ltd", 1),
    ]:
        conn.execute(
            "INSERT OR REPLACE INTO applications (id, ref, company_name, status, is_fixture) VALUES (?, ?, ?, 'approved', ?)",
            (app_id, ref, name, is_fixture),
        )

    conn.execute("DELETE FROM monitoring_alerts WHERE id BETWEEN 9401 AND 9412")
    rows = [
        (9401, "app_list_alpha", "Alpha Monitoring Ltd", "media", "High", "open", "complyadvantage", "case-alpha-media", "CA media alert", "manual"),
        (9402, "app_list_alpha", "Alpha Monitoring Ltd", "document_expired", "medium", "open", "document_health_monitor", "case-alpha-doc", "Passport expired", "document_health"),
        (9403, "app_list_alpha", "Alpha Monitoring Ltd", "document_expiry_missing", "Medium", "open", "document_health_monitor", "case-alpha-missing", "Passport is missing an expiry date", "document_health"),
        (9404, "app_list_alpha", "Alpha Monitoring Ltd", "media", "high", "resolved", "complyadvantage", "case-alpha-closed", "Closed media alert", "manual"),
        (9405, "app_list_alpha", "Alpha Monitoring Ltd", "pep", "medium", "dismissed", "complyadvantage", "case-alpha-dismissed", "Dismissed PEP alert", "manual"),
        (9406, "app_list_alpha", "Alpha Monitoring Ltd", "Risk Drift", "Low", "open", "risk_agent", "case-alpha-risk", "Risk drift legacy alert", "manual"),
        (9407, "app_list_fixture", "RegMind E2E Fixture Ltd", "media", "high", "open", "complyadvantage", "case-fixture", "Fixture media alert", "manual"),
        (9408, "app_list_beta", "app_list_beta", "media", "High", "open", "complyadvantage", "case-beta-media", "Beta media alert", "manual"),
        (9409, "app_list_beta", "Beta Monitoring Ltd", "pep", "medium", "assigned", "complyadvantage", "case-beta-pep", "Beta PEP alert", "manual"),
        (9410, "app_list_beta", "Beta Monitoring Ltd", "Sanctions Match", "Critical", "open", "complyadvantage", "case-beta-sanctions", "Beta sanctions alert", "manual"),
        (9411, "app_list_beta", "Beta Monitoring Ltd", "media", "medium", "routed_to_edd", "complyadvantage", "case-beta-routed", "Routed alert", "manual"),
        (9412, "app_list_alpha", "Alpha Monitoring Ltd", "manual_pr290_schema_validation", "medium", "open", "manual_pr290", "case-pr290", "manual_pr290_schema_validation smoke row", "manual"),
    ]
    for row in rows:
        conn.execute(
            """
            INSERT INTO monitoring_alerts
                (id, application_id, client_name, alert_type, severity, status,
                 detected_by, case_identifier, summary, discovered_via, source_reference)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*row, json.dumps({"case_identifier": row[7]})),
        )


def _token(user_id, role, name):
    from auth import create_token

    return create_token(user_id, role, name, "officer")


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _get(base_url, token, query=""):
    resp = requests.get(
        f"{base_url}/api/monitoring/alerts{query}",
        headers=_headers(token),
        timeout=5,
    )
    assert resp.status_code == 200
    return resp.json()


def test_default_monitoring_alert_list_is_active_paginated_and_clean(monitoring_list_server):
    base_url, _db_module = monitoring_list_server
    token = _token("admin_list", "admin", "Admin List")

    body = _get(base_url, token, "?page=1&page_size=2")

    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["total"] == 6
    assert body["total_pages"] == 3
    assert body["has_next"] is True
    assert body["has_previous"] is False
    assert len(body["alerts"]) == 2
    assert body["filters"]["include_closed"] is False
    assert body["filters"]["show_fixtures"] is False
    ids = {item["id"] for item in body["alerts"]}
    assert 9404 not in ids
    assert 9405 not in ids
    assert 9406 not in ids
    assert 9407 not in ids
    assert 9412 not in ids
    assert all(not item["is_terminal"] for item in body["alerts"])


def test_monitoring_alert_list_page_two_returns_next_records(monitoring_list_server):
    base_url, _db_module = monitoring_list_server
    token = _token("admin_list", "admin", "Admin List")

    page_one = _get(base_url, token, "?page=1&page_size=2")
    page_two = _get(base_url, token, "?page=2&page_size=2")

    assert page_two["page"] == 2
    assert page_two["has_previous"] is True
    assert {item["id"] for item in page_one["alerts"]}.isdisjoint(
        {item["id"] for item in page_two["alerts"]}
    )


def test_closed_alerts_require_explicit_filter(monitoring_list_server):
    base_url, _db_module = monitoring_list_server
    token = _token("admin_list", "admin", "Admin List")

    default = _get(base_url, token, "?page_size=50")
    include_closed = _get(base_url, token, "?include_closed=true&page_size=50")
    routed = _get(base_url, token, "?status=routed_to_edd&page_size=50")

    assert {item["id"] for item in default["alerts"]}.isdisjoint({9404, 9405, 9411})
    assert {9404, 9405, 9411}.issubset({item["id"] for item in include_closed["alerts"]})
    assert [item["id"] for item in routed["alerts"]] == [9411]


def test_canonical_type_and_severity_filters_are_server_side(monitoring_list_server):
    base_url, _db_module = monitoring_list_server
    token = _token("admin_list", "admin", "Admin List")

    doc_expiry = _get(base_url, token, "?type=document_expiry&page_size=50")
    missing_refresh = _get(base_url, token, "?type=missing_document_refresh&page_size=50")
    adverse = _get(base_url, token, "?type=adverse_media&page_size=50")
    sanctions = _get(base_url, token, "?type=sanctions_change&page_size=50")
    risk_drift = _get(base_url, token, "?type=risk_drift&page_size=50")
    high = _get(base_url, token, "?severity=high&page_size=50")

    assert {item["id"] for item in doc_expiry["alerts"]} == {9402}
    assert {item["id"] for item in missing_refresh["alerts"]} == {9403}
    assert {item["id"] for item in adverse["alerts"]} == {9401, 9408}
    assert {item["id"] for item in sanctions["alerts"]} == {9410}
    assert risk_drift["total"] == 0
    assert {item["id"] for item in high["alerts"]} == {9401, 9408}


def test_search_and_client_display_use_application_company(monitoring_list_server):
    base_url, _db_module = monitoring_list_server
    token = _token("admin_list", "admin", "Admin List")

    body = _get(base_url, token, "?q=BETA-REF&page_size=50")

    ids = {item["id"] for item in body["alerts"]}
    assert {9408, 9409, 9410}.issubset(ids)
    beta_media = next(item for item in body["alerts"] if item["id"] == 9408)
    assert beta_media["application_company_name"] == "Beta Monitoring Ltd"
    assert beta_media["client_display_name"] == "Beta Monitoring Ltd"
    assert beta_media["mapping_status"] == "mapped"


def test_fixture_opt_in_is_authorized_and_read_only(monitoring_list_server):
    base_url, db_module = monitoring_list_server
    admin_token = _token("admin_list", "admin", "Admin List")
    co_token = _token("co_list", "co", "CO List")

    conn = db_module.get_db()
    try:
        before_alert_count = conn.execute("SELECT COUNT(*) AS c FROM monitoring_alerts").fetchone()["c"]
        before_audit_count = conn.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]
    finally:
        conn.close()

    default = _get(base_url, admin_token, "?page_size=50")
    admin_fixtures = _get(base_url, admin_token, "?show_fixtures=true&page_size=50")
    co_fixtures = _get(base_url, co_token, "?show_fixtures=true&page_size=50")

    assert 9407 not in {item["id"] for item in default["alerts"]}
    assert 9407 in {item["id"] for item in admin_fixtures["alerts"]}
    assert admin_fixtures["show_fixtures"] is True
    assert 9407 not in {item["id"] for item in co_fixtures["alerts"]}
    assert co_fixtures["show_fixtures"] is False

    conn = db_module.get_db()
    try:
        after_alert_count = conn.execute("SELECT COUNT(*) AS c FROM monitoring_alerts").fetchone()["c"]
        after_audit_count = conn.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]
    finally:
        conn.close()
    assert after_alert_count == before_alert_count
    assert after_audit_count == before_audit_count
