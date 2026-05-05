"""
Sprint 2.5 — Minimal HTTP/API Test Layer
Tests critical API paths: health, auth, security headers, and invalid request handling.
Runs a real Tornado HTTP server in a background thread for true HTTP-level validation.
"""
import os
import sys
import json
import tempfile
import socket
import threading
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

import requests as http_requests
import tornado.ioloop
import tornado.httpserver


def _find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def api_server():
    """Start a real Tornado HTTP server on a background IOLoop for API testing.
    Uses the same DB path pattern as conftest.py to avoid stomping other tests."""
    # Use the SAME db path convention as conftest.temp_db so no collision
    db_path = os.path.join(tempfile.gettempdir(), f"onboarda_test_{os.getpid()}.db")
    os.environ["DB_PATH"] = db_path

    from db import init_db, seed_initial_data, get_db
    init_db()
    try:
        conn = get_db()
        seed_initial_data(conn)
        conn.commit()
        conn.close()
    except Exception:
        pass

    from server import make_app
    app = make_app()
    port = _find_free_port()

    # Run server in a dedicated thread with its own event loop
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

    base_url = f"http://127.0.0.1:{port}"
    yield base_url

    # Shutdown
    io_loop = server_ref.get("loop")
    srv = server_ref.get("server")
    if io_loop and srv:
        io_loop.add_callback(srv.stop)
        io_loop.add_callback(io_loop.stop)
    thread.join(timeout=2)


# ═══════════════════════════════════════════════════════════
# 1. Health Endpoint — load balancer/uptime critical
# ═══════════════════════════════════════════════════════════

class TestHealthAPI:
    def test_health_returns_200(self, api_server):
        """GET /api/health must return 200 with status field."""
        resp = http_requests.get(f"{api_server}/api/health", timeout=3)
        assert resp.status_code == 200
        body = resp.json()
        assert "status" in body

    def test_health_returns_json_content_type(self, api_server):
        """Health response must have application/json content-type."""
        resp = http_requests.get(f"{api_server}/api/health", timeout=3)
        assert "application/json" in resp.headers.get("Content-Type", "")

    def test_public_health_does_not_leak_internal_inventory(self, api_server):
        """Unauthenticated health must not expose DB type or provider config."""
        resp = http_requests.get(f"{api_server}/api/health", timeout=3)
        assert resp.status_code == 200
        body = resp.json()
        assert "database" not in body
        assert "integrations" not in body
        assert "metrics_enabled" not in body

    def test_portal_and_backoffice_have_browser_security_headers(self, api_server):
        """Static HTML entry points must carry the same security posture as APIs."""
        required = {
            "Content-Security-Policy",
            "Strict-Transport-Security",
            "X-Frame-Options",
            "Referrer-Policy",
            "Permissions-Policy",
            "X-Content-Type-Options",
        }
        for path in ("/portal", "/backoffice"):
            resp = http_requests.get(f"{api_server}{path}", timeout=3)
            assert resp.status_code == 200
            missing = [h for h in required if not resp.headers.get(h)]
            assert missing == []
            assert "TornadoServer" not in resp.headers.get("Server", "")

    def test_public_liveness_is_hardened(self, api_server):
        """Public liveness replaces deep unauthenticated readiness checks."""
        resp = http_requests.get(f"{api_server}/api/liveness", timeout=3)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.headers.get("Server") == "RegMind"
        assert resp.headers.get("Content-Security-Policy")
        assert resp.headers.get("Strict-Transport-Security")

    def test_readiness_requires_auth(self, api_server):
        """Deep readiness must not expose encryption/database/config status publicly."""
        resp = http_requests.get(f"{api_server}/api/readiness", timeout=3)
        assert resp.status_code == 401
        assert "checks" not in resp.text
        assert resp.headers.get("Server") == "RegMind"

    def test_metrics_requires_auth(self, api_server):
        """Prometheus exposition must not be available anonymously."""
        resp = http_requests.get(f"{api_server}/metrics", timeout=3)
        assert resp.status_code == 401
        assert "python_gc_objects_collected_total" not in resp.text
        assert resp.headers.get("Server") == "RegMind"

    def test_default_404_has_hardened_headers(self, api_server):
        """Unmatched routes must not fall through to Tornado's default 404."""
        resp = http_requests.get(f"{api_server}/no-such-phase5-path", timeout=3)
        assert resp.status_code == 404
        assert resp.headers.get("Server") == "RegMind"
        assert resp.headers.get("Content-Security-Policy")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"


# ═══════════════════════════════════════════════════════════
# 2. Auth Rejection — unauthenticated requests must be blocked
# ═══════════════════════════════════════════════════════════

class TestAuthRejection:
    def test_no_token_returns_401(self, api_server):
        """GET /api/applications without token must return 401."""
        resp = http_requests.get(f"{api_server}/api/applications", timeout=3)
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, api_server):
        """GET /api/applications with garbage token must return 401."""
        resp = http_requests.get(f"{api_server}/api/applications",
                                 headers={"Authorization": "Bearer garbage.invalid.token"}, timeout=3)
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════
# 3. Authenticated Success Path
# ═══════════════════════════════════════════════════════════

class TestAuthenticatedAccess:
    def test_valid_token_returns_200(self, api_server):
        """GET /api/applications with valid admin token must return 200."""
        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(f"{api_server}/api/applications",
                                 headers={"Authorization": f"Bearer {token}"}, timeout=3)
        assert resp.status_code == 200

    def test_applications_endpoint_returns_true_total_with_pagination(self, api_server):
        """Application list pagination must not redefine total as returned-row count."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        conn.execute("DELETE FROM applications WHERE id IN (?, ?)", ("app_page_1", "app_page_2"))
        conn.execute(
            "INSERT INTO applications (id, ref, client_id, company_name, country, status, created_at, is_fixture) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("app_page_1", "ARF-PAGE-001", "client_page", "Phase Page One Ltd", "Mauritius", "submitted", "2026-05-03T10:00:00Z", 0),
        )
        conn.execute(
            "INSERT INTO applications (id, ref, client_id, company_name, country, status, created_at, is_fixture) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("app_page_2", "ARF-PAGE-002", "client_page", "Phase Page Two Ltd", "Mauritius", "submitted", "2026-05-03T10:01:00Z", 0),
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/applications?limit=1&offset=0",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 1
        assert body["offset"] == 0
        assert body["returned"] == 1
        assert len(body["applications"]) == 1
        assert body["total"] >= 2

    def test_dashboard_returns_200_for_officer_with_fixture_filter(self, api_server):
        """Officer dashboard must not use ambiguous columns in joined recent query."""
        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/dashboard",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "recent" in body
        assert "total" in body

    def test_login_with_empty_body_does_not_crash(self, api_server):
        """POST /api/auth/officer/login with empty JSON must not crash (4xx expected)."""
        resp = http_requests.post(f"{api_server}/api/auth/officer/login",
                                  json={}, timeout=3)
        assert resp.status_code in (400, 401)

    def test_admin_client_password_reset_requires_confirm_policy_audit_and_revokes(self, api_server, monkeypatch):
        """Client admin reset must be confirmed, audited, policy-checked, and revoke sessions."""
        import bcrypt
        from auth import create_token, decode_token
        from db import get_db

        monkeypatch.setenv("ADMIN_CLIENT_RESET_CONFIRMATION", "phase5-confirm")
        admin_token = create_token("admin001", "admin", "Test Admin", "officer")
        client_id = "phase5_client_reset"
        email = "phase5-client-reset@example.com"
        old_hash = bcrypt.hashpw("OldStrong123!".encode(), bcrypt.gensalt()).decode()

        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (f"client:{email}",))
        conn.execute("DELETE FROM clients WHERE id = ? OR LOWER(email) = ?", (client_id, email))
        conn.execute(
            "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
            (client_id, email, old_hash, "Phase 5 Client Reset Ltd"),
        )
        conn.commit()
        conn.close()

        stale_client_token = create_token(client_id, "client", "Phase 5 Client Reset Ltd", "client")
        assert decode_token(stale_client_token) is not None

        missing_confirm = http_requests.post(
            f"{api_server}/api/admin/reset-password",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"email": email, "new_password": "StrongPass123!"},
            timeout=3,
        )
        assert missing_confirm.status_code == 403

        weak = http_requests.post(
            f"{api_server}/api/admin/reset-password",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"email": email, "new_password": "short", "confirm": "phase5-confirm"},
            timeout=3,
        )
        assert weak.status_code == 400
        assert "Password policy violation" in weak.text

        ok = http_requests.post(
            f"{api_server}/api/admin/reset-password",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"email": email, "new_password": "StrongPass123!", "confirm": "phase5-confirm"},
            timeout=3,
        )
        assert ok.status_code == 200, ok.text
        assert decode_token(stale_client_token) is None

        audit = http_requests.get(
            f"{api_server}/api/audit?ref=client:{email}",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=3,
        )
        assert audit.status_code == 200
        entries = audit.json()["entries"]
        assert any(e["action"] == "Admin Password Reset" and e["target"] == f"client:{email}" for e in entries)

    def test_admin_officer_password_reset_audits_and_revokes(self, api_server, monkeypatch):
        """Officer reset must use policy, audit the reset, and revoke old officer tokens."""
        import bcrypt
        from auth import create_token, decode_token
        from db import get_db

        monkeypatch.setenv("ADMIN_OFFICER_RESET_CONFIRMATION", "phase5-officer-confirm")
        admin_token = create_token("admin001", "admin", "Test Admin", "officer")
        officer_id = "phase5_sco_reset"
        officer_email = "phase5-sco-reset@example.com"
        old_hash = bcrypt.hashpw("OldStrong123!".encode(), bcrypt.gensalt()).decode()

        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (f"officer:{officer_email}",))
        conn.execute("DELETE FROM users WHERE id = ? OR LOWER(email) = ?", (officer_id, officer_email))
        conn.execute(
            "INSERT INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, ?)",
            (officer_id, officer_email, old_hash, "Phase 5 SCO Reset", "sco", "active"),
        )
        conn.commit()
        conn.close()

        stale_officer_token = create_token(officer_id, "sco", "Phase 5 SCO Reset", "officer")
        assert decode_token(stale_officer_token) is not None

        weak = http_requests.post(
            f"{api_server}/api/admin/officer-reset-password",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"email": officer_email, "new_password": "short", "confirm": "phase5-officer-confirm"},
            timeout=3,
        )
        assert weak.status_code == 400
        assert "Password policy violation" in weak.text

        ok = http_requests.post(
            f"{api_server}/api/admin/officer-reset-password",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"email": officer_email, "new_password": "StrongPass123!", "confirm": "phase5-officer-confirm"},
            timeout=3,
        )
        assert ok.status_code == 200, ok.text
        assert decode_token(stale_officer_token) is None

        conn = get_db()
        audit = conn.execute(
            "SELECT action, target FROM audit_log WHERE target = ? ORDER BY timestamp DESC LIMIT 1",
            (f"officer:{officer_email}",),
        ).fetchone()
        conn.close()
        assert audit is not None
        assert audit["action"] == "Admin Password Reset"

    def test_application_detail_returns_authoritative_payload(self, api_server):
        """GET /api/applications/:ref should return parsed persisted detail data for back office review."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                ownership_structure, prescreening_data, risk_level, risk_score,
                risk_dimensions, status, assigned_to
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "app_detail_api",
            "ARF-2026-DETAIL",
            "testclient001",
            "Detail Corp Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "Layered ownership",
            json.dumps({
                "registered_entity_name": "Detail Corp Ltd",
                "trading_name": "Detail Portal",
                "services_required": ["Multi-currency corporate accounts"],
                "source_of_funds": "Initial treasury transfer",
                "business_overview": "Cross-border payments software."
            }),
            "MEDIUM",
            58,
            json.dumps({"d1": 2.0, "d2": 2.5}),
            "in_review",
            "admin001"
        ))
        conn.execute("""
            INSERT INTO directors (id, application_id, person_key, first_name, last_name, full_name, nationality, is_pep, pep_declaration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "dir_detail_1", "app_detail_api", "dir101", "Jane", "Doe", "Jane Doe",
            "Mauritius", "No", json.dumps({})
        ))
        conn.execute("""
            INSERT INTO ubos (id, application_id, person_key, first_name, last_name, full_name, nationality, ownership_pct, is_pep, pep_declaration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "ubo_detail_1", "app_detail_api", "ubo202", "Ali", "Khan", "Ali Khan",
            "United Kingdom", 55.0, "Yes", json.dumps({"public_function": "MP"})
        ))
        conn.execute("""
            INSERT INTO intermediaries (id, application_id, person_key, entity_name, jurisdiction, ownership_pct)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "int_detail_1", "app_detail_api", "int303", "North HoldCo Ltd", "BVI", 100.0
        ))
        conn.execute("""
            INSERT INTO documents (
                id, application_id, person_id, doc_type, doc_name, file_path,
                verification_status, verification_results
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "doc_detail_1", "app_detail_api", "ubo202", "passport", "ubo-passport.pdf",
            "/tmp/ubo-passport.pdf", "verified",
            json.dumps({"document_type": "passport", "quality_score": 0.99})
        ))
        conn.execute("""
            INSERT INTO compliance_memos (
                application_id, version, memo_data, review_status, validation_status,
                blocked, block_reason, quality_score, memo_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "app_detail_api", 3, json.dumps({"sections": {"executive_summary": {"content": "Stored memo"}}}),
            "reviewed", "pass", 0, None, 0.93, "v3"
        ))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/applications/ARF-2026-DETAIL",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["company_name"] == "Detail Corp Ltd"
        assert data["assigned_name"]
        assert data["prescreening_data"]["trading_name"] == "Detail Portal"
        assert data["risk_dimensions"]["d2"] == 2.5
        assert data["directors"][0]["person_key"] == "dir101"
        assert data["ubos"][0]["pep_declaration"]["public_function"] == "MP"
        assert data["intermediaries"][0]["entity_name"] == "North HoldCo Ltd"
        assert data["documents"][0]["verification_results"]["document_type"] == "passport"
        assert data["latest_memo"]["version"] == 3
        assert data["latest_memo"]["review_status"] == "reviewed"
        assert data["latest_memo_data"]["sections"]["executive_summary"]["content"] == "Stored memo"
        assert data["latest_memo_data"]["memo_version"] == "v3"
        assert data["latest_memo_data"]["application_ref"] == "ARF-2026-DETAIL"

    def test_audit_endpoint_filters_by_application_ref_and_prefixed_target(self, api_server):
        """Global audit reconstruction must support both target conventions."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        conn.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
            ("admin001", "Test Admin", "admin", "Generate Memo", "ARF-FILTER", "bare", "127.0.0.1"),
        )
        conn.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
            ("admin001", "Test Admin", "admin", "edd_routing.evaluated", "application:ARF-FILTER", "prefixed", "127.0.0.1"),
        )
        conn.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
            ("admin001", "Test Admin", "admin", "Login", "System", "other", "127.0.0.1"),
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/audit?ref=ARF-FILTER&limit=50",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        targets = {e["target"] for e in entries}
        assert "ARF-FILTER" in targets
        assert "application:ARF-FILTER" in targets
        assert "System" not in targets

        export = http_requests.get(
            f"{api_server}/api/audit/export?format=json&ref=ARF-FILTER",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert export.status_code == 200
        export_targets = {e["target"] for e in export.json()["entries"]}
        assert export_targets == {"ARF-FILTER", "application:ARF-FILTER"}

    def test_audit_list_and_export_exclude_fixture_application_targets_by_default(self, api_server):
        """Global audit list/export must not leak fixture-linked rows by default."""
        from auth import create_token
        from db import get_db

        real_id = "app_pr1_audit_real"
        real_ref = "ARF-PR1-AUDIT-REAL"
        fixture_id = "app_pr1_audit_fixture"
        fixture_ref = "ARF-PR1-AUDIT-FIXTURE"
        action = "PR1 Fixture Audit"

        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE action = ?", (action,))
        conn.execute("DELETE FROM applications WHERE id IN (?, ?)", (real_id, fixture_id))
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, is_fixture)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (real_id, real_ref, "testclient001", "PR1 Audit Real Ltd",
             "Mauritius", "Technology", "SME", "in_review", 0),
        )
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, is_fixture)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (fixture_id, fixture_ref, "testclient001", "PR1 Audit Fixture Ltd",
             "Mauritius", "Technology", "SME", "in_review", 1),
        )
        for target, detail in (
            (real_ref, "real-ref"),
            (fixture_ref, "fixture-ref"),
            (f"application:{fixture_ref}", "fixture-prefixed"),
            (fixture_id, "fixture-id"),
        ):
            conn.execute(
                "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                ("admin001", "Test Admin", "admin", action, target, detail, "127.0.0.1"),
            )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        default_resp = http_requests.get(
            f"{api_server}/api/audit?action={action}&limit=50",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert default_resp.status_code == 200
        default_targets = {e["target"] for e in default_resp.json()["entries"]}
        assert real_ref in default_targets
        assert fixture_ref not in default_targets
        assert f"application:{fixture_ref}" not in default_targets
        assert fixture_id not in default_targets

        include_resp = http_requests.get(
            f"{api_server}/api/audit?action={action}&include_fixtures=1&limit=50",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert include_resp.status_code == 200
        include_targets = {e["target"] for e in include_resp.json()["entries"]}
        assert {real_ref, fixture_ref, f"application:{fixture_ref}", fixture_id}.issubset(include_targets)
        assert include_resp.json()["show_fixtures"] is True

        export_resp = http_requests.get(
            f"{api_server}/api/audit/export?format=json&action={action}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert export_resp.status_code == 200
        export_targets = {e["target"] for e in export_resp.json()["entries"]}
        assert real_ref in export_targets
        assert fixture_ref not in export_targets
        assert f"application:{fixture_ref}" not in export_targets
        assert fixture_id not in export_targets

    def test_applications_endpoint_excludes_fixtures_by_default_and_supports_alias_opt_in(self, api_server):
        """Applications list should hide fixtures by default for officers and clients."""
        from auth import create_token
        from db import get_db

        real_id = "app_pr1_apps_real"
        real_ref = "ARF-PR1-APPS-REAL"
        fixture_id = "app_pr1_apps_fixture"
        fixture_ref = "ARF-PR1-APPS-FIXTURE"
        client_id = "testclient001"

        conn = get_db()
        conn.execute("DELETE FROM applications WHERE id IN (?, ?)", (real_id, fixture_id))
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, is_fixture)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (real_id, real_ref, client_id, "PR1 Apps Real Ltd",
             "Mauritius", "Technology", "SME", "in_review", 0),
        )
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, is_fixture)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (fixture_id, fixture_ref, client_id, "PR1 Apps Fixture Ltd",
             "Mauritius", "Technology", "SME", "in_review", 1),
        )
        conn.commit()
        conn.close()

        admin_token = create_token("admin001", "admin", "Test Admin", "officer")
        co_token = create_token("co001", "co", "Test CO", "officer")
        client_token = create_token(client_id, "client", "Test Client", "client")

        default_resp = http_requests.get(
            f"{api_server}/api/applications?limit=5000",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=3,
        )
        assert default_resp.status_code == 200
        default_refs = {a["ref"] for a in default_resp.json()["applications"]}
        assert real_ref in default_refs
        assert fixture_ref not in default_refs

        include_resp = http_requests.get(
            f"{api_server}/api/applications?limit=5000&include_fixtures=1",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=3,
        )
        assert include_resp.status_code == 200
        include_refs = {a["ref"] for a in include_resp.json()["applications"]}
        assert {real_ref, fixture_ref}.issubset(include_refs)

        co_include = http_requests.get(
            f"{api_server}/api/applications?limit=5000&include_fixtures=1",
            headers={"Authorization": f"Bearer {co_token}"},
            timeout=3,
        )
        assert co_include.status_code == 200
        co_refs = {a["ref"] for a in co_include.json()["applications"]}
        assert fixture_ref not in co_refs

        client_default = http_requests.get(
            f"{api_server}/api/applications?limit=5000",
            headers={"Authorization": f"Bearer {client_token}"},
            timeout=3,
        )
        assert client_default.status_code == 200
        client_refs = {a["ref"] for a in client_default.json()["applications"]}
        assert real_ref in client_refs
        assert fixture_ref not in client_refs

        client_dashboard = http_requests.get(
            f"{api_server}/api/dashboard",
            headers={"Authorization": f"Bearer {client_token}"},
            timeout=3,
        )
        assert client_dashboard.status_code == 200
        client_recent_refs = {a["ref"] for a in client_dashboard.json()["recent"]}
        assert fixture_ref not in client_recent_refs

        admin_dashboard = http_requests.get(
            f"{api_server}/api/dashboard?include_fixtures=1",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=3,
        )
        assert admin_dashboard.status_code == 200
        assert admin_dashboard.json()["show_fixtures"] is True
        admin_recent_refs = {a["ref"] for a in admin_dashboard.json()["recent"]}
        assert fixture_ref in admin_recent_refs

    def test_application_audit_log_includes_prefixed_application_targets(self, api_server):
        """Case audit reconstruction must include bare and application: prefixed targets."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        for target in ("ARF-P3-AUDIT", "application:ARF-P3-AUDIT", "ARF-P3-OTHER"):
            conn.execute("DELETE FROM audit_log WHERE target = ?", (target,))
        conn.execute("DELETE FROM applications WHERE id = ?", ("app_p3_audit",))
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("app_p3_audit", "ARF-P3-AUDIT", "testclient001", "Audit Reconstruction Ltd",
             "Mauritius", "Technology", "SME", "in_review"),
        )
        conn.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
            ("admin001", "Test Admin", "admin", "Generate Memo", "ARF-P3-AUDIT", "bare", "127.0.0.1"),
        )
        conn.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
            ("admin001", "Test Admin", "admin", "edd_routing.evaluated", "application:ARF-P3-AUDIT", "prefixed", "127.0.0.1"),
        )
        conn.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
            ("admin001", "Test Admin", "admin", "Login", "ARF-P3-OTHER", "other", "127.0.0.1"),
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/applications/ARF-P3-AUDIT/audit-log?limit=20",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        data = resp.json()
        targets = {e["target"] for e in data["entries"]}
        assert data["total"] == 2
        assert targets == {"ARF-P3-AUDIT", "application:ARF-P3-AUDIT"}

    def test_dashboard_reports_surface_unknown_risk_bucket(self, api_server):
        """Unknown risk must be explicit, not coerced into low/zero reporting."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        conn.execute("DELETE FROM applications WHERE id IN (?, ?)", ("app_p3_unknown", "app_p3_low"))
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("app_p3_unknown", "ARF-P3-UNKNOWN", "testclient001", "Mauritius Alpha Holdings Ltd",
             "Mauritius", "Technology", "SME", "in_review", None, None),
        )
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("app_p3_low", "ARF-P3-LOW", "testclient001", "Mauritius Beta Holdings Ltd",
             "Mauritius", "Manufacturing", "SME", "in_review", "LOW", 12),
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        dashboard = http_requests.get(
            f"{api_server}/api/dashboard",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert dashboard.status_code == 200
        dash = dashboard.json()
        risk_sum = (
            dash.get("risk_low", 0) + dash.get("risk_medium", 0) +
            dash.get("risk_high", 0) + dash.get("risk_very_high", 0) +
            dash.get("risk_unknown", 0)
        )
        assert dash["risk_unknown"] >= 1
        assert risk_sum == dash["total"]

        analytics = http_requests.get(
            f"{api_server}/api/reports/analytics",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert analytics.status_code == 200
        body = analytics.json()
        dist = body["risk_distribution"]
        assert dist["UNKNOWN"] >= 1
        assert sum(dist.values()) == body["summary"]["total"]

        report = http_requests.get(
            f"{api_server}/api/reports/generate?fields=ref,risk_level,risk_score",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert report.status_code == 200
        row = next(r for r in report.json()["data"] if r["ref"] == "ARF-P3-UNKNOWN")
        assert row["risk_level"] == "UNKNOWN"
        assert row["risk_score"] is None

    def test_application_evidence_pack_reconstructs_case(self, api_server):
        """Evidence pack should consolidate application, memo, decision, EDD, and audit data."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        app_id = "app_p3_pack"
        app_ref = "ARF-P3-PACK"
        conn.execute("DELETE FROM compliance_memos WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM edd_cases WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM decision_records WHERE application_ref = ?", (app_ref,))
        conn.execute("DELETE FROM audit_log WHERE target IN (?, ?, ?)", (app_ref, f"application:{app_ref}", "ARF-P3-PACK-OTHER"))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score, prescreening_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (app_id, app_ref, "testclient001", "Evidence Pack Ltd", "Mauritius", "Fintech",
             "SME", "edd_required", "HIGH", 72, json.dumps({"registered_entity_name": "Evidence Pack Ltd"})),
        )
        conn.execute(
            """
            INSERT INTO compliance_memos
            (application_id, version, memo_version, memo_data, review_status, validation_status, quality_score, raw_output_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (app_id, 1, "v1", json.dumps({"metadata": {"memo_version": "v1"}}), "draft", "pass", 8.2, "hash123"),
        )
        conn.execute(
            """
            INSERT INTO decision_records
            (id, application_ref, decision_type, risk_level, confidence_score, source, actor_user_id, actor_role, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("dec_p3_pack", app_ref, "escalate_edd", "HIGH", 0.88, "manual", "admin001", "admin", "2026-05-03T10:00:00Z"),
        )
        conn.execute(
            """
            INSERT INTO edd_cases
            (application_id, client_name, risk_level, risk_score, stage, trigger_source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (app_id, "Evidence Pack Ltd", "HIGH", 72, "triggered", "officer_decision"),
        )
        conn.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
            ("admin001", "Test Admin", "admin", "Generate Memo", app_ref, json.dumps({"memo_version": "v1"}), "127.0.0.1"),
        )
        conn.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
            ("admin001", "Test Admin", "admin", "edd_routing.actuated", f"application:{app_ref}", "prefixed", "127.0.0.1"),
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/applications/{app_ref}/evidence-pack",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        pack = resp.json()
        assert pack["scope"]["application_ref"] == app_ref
        assert pack["application"]["company_name"] == "Evidence Pack Ltd"
        assert pack["compliance_memos"][0]["memo_version"] == "v1"
        assert pack["decision_records"][0]["decision_type"] == "escalate_edd"
        assert pack["edd_cases"][0]["stage"] == "triggered"
        assert pack["audit_log"]["total"] == 2
        targets = {e["target"] for e in pack["audit_log"]["entries"]}
        assert targets == {app_ref, f"application:{app_ref}"}
        assert pack["audit_log"]["entries"][0]["detail_json"]["memo_version"] == "v1"

    def test_failed_document_upload_is_audited(self, api_server):
        """Rejected uploads are security events and must appear in case audit."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("app_upload_reject", "ARF-UPLOAD-REJECT", "testclient001",
             "Upload Reject Ltd", "Mauritius", "Technology", "SME", "draft"),
        )
        conn.commit()
        conn.close()

        token = create_token("testclient001", "client", "Test Client", "client")
        resp = http_requests.post(
            f"{api_server}/api/applications/app_upload_reject/documents?doc_type=cert_inc",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("bad.exe", b"MZ fake executable", "application/octet-stream")},
            timeout=3,
        )
        assert resp.status_code == 400

        admin_token = create_token("admin001", "admin", "Test Admin", "officer")
        audit = http_requests.get(
            f"{api_server}/api/applications/app_upload_reject/audit-log?limit=20",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=3,
        )
        assert audit.status_code == 200
        entries = audit.json()["entries"]
        rejection = next(e for e in entries if e["action"].startswith("Upload Rejected"))
        detail = json.loads(rejection["detail"])
        assert detail["reason_code"] == "disallowed_extension"
        assert detail["filename"] == "bad.exe"
        assert detail["doc_type"] == "cert_inc"
        assert detail["duration_ms"] is not None

    def test_edd_creation_preserves_unknown_risk_and_ui_has_no_high_fallback(self, api_server):
        """EDD must not fabricate HIGH/0 when parent application risk is unknown."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        app_id = "app_p4_edd_unknown"
        app_ref = "ARF-P4-EDD-UNKNOWN"
        conn.execute("DELETE FROM edd_cases WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (app_id, app_ref, "testclient001", "Phase 4 Unknown Risk Ltd",
             "Mauritius", "Technology", "SME", "edd_required", None, None),
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        created = http_requests.post(
            f"{api_server}/api/edd/cases",
            headers={"Authorization": f"Bearer {token}"},
            json={"application_id": app_id, "trigger_source": "phase4_test"},
            timeout=3,
        )
        assert created.status_code == 201, created.text

        listed = http_requests.get(
            f"{api_server}/api/edd/cases",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert listed.status_code == 200
        row = next(c for c in listed.json()["cases"] if c["application_id"] == app_id)
        assert row["risk_level"] is None
        assert row["risk_score"] is None

        html_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-backoffice.html")
        with open(html_path, encoding="utf-8") as f:
            html = f.read()
        assert "c.risk_level || 'HIGH'" not in html
        assert "riskBadge(c.risk_level)" in html

    def test_edd_list_excludes_fixture_rows_by_default_and_supports_include_fixtures(self, api_server):
        """Operational EDD queue should hide fixture/smoke rows unless explicitly opted in."""
        from auth import create_token
        from db import get_db

        app_id = "app_p5_edd_fixture"
        app_ref = "ARF-P5-EDD-FIXTURE"
        conn = get_db()
        conn.execute("DELETE FROM edd_cases WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, is_fixture)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (app_id, app_ref, "testclient001", "PHASE1 Memo Truth Smoke Fixture Ltd",
             "Mauritius", "Technology", "SME", "edd_required", 0),
        )
        conn.execute(
            """
            INSERT INTO edd_cases
            (application_id, client_name, risk_level, risk_score, trigger_source, trigger_notes, stage, assigned_officer)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (app_id, "PHASE1 Memo Truth Smoke Fixture Ltd", None, None,
             "phase5_test", "fixture visibility test", "triggered", "admin001"),
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        default_resp = http_requests.get(
            f"{api_server}/api/edd/cases",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert default_resp.status_code == 200
        assert all(c["application_id"] != app_id for c in default_resp.json()["cases"])

        include_resp = http_requests.get(
            f"{api_server}/api/edd/cases?include_fixtures=1",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert include_resp.status_code == 200
        assert any(c["application_id"] == app_id for c in include_resp.json()["cases"])

    def test_edd_stats_honours_fixture_opt_in_aliases_and_role_gate(self, api_server):
        """EDD KPI stats should match the fixture policy used by the EDD list."""
        from auth import create_token
        from db import get_db

        app_id = "app_pr1_edd_stats_fixture"
        conn = get_db()
        conn.execute("DELETE FROM edd_cases WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, is_fixture)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (app_id, "ARF-PR1-EDD-STATS-FIX", "testclient001", "PR1 EDD Stats Fixture Ltd",
             "Mauritius", "Technology", "SME", "edd_required", 1),
        )
        conn.execute(
            """
            INSERT INTO edd_cases
            (application_id, client_name, risk_level, risk_score, trigger_source, trigger_notes, stage, assigned_officer)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (app_id, "PR1 EDD Stats Fixture Ltd", None, None,
             "pr1_test", "fixture stats test", "triggered", "admin001"),
        )
        conn.commit()
        conn.close()

        admin_token = create_token("admin001", "admin", "Test Admin", "officer")
        co_token = create_token("co001", "co", "Test CO", "officer")
        default_resp = http_requests.get(
            f"{api_server}/api/edd/stats",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=3,
        )
        assert default_resp.status_code == 200
        include_resp = http_requests.get(
            f"{api_server}/api/edd/stats?include_fixtures=1",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=3,
        )
        assert include_resp.status_code == 200
        assert include_resp.json()["show_fixtures"] is True
        assert include_resp.json()["active"] >= default_resp.json()["active"] + 1

        co_include = http_requests.get(
            f"{api_server}/api/edd/stats?include_fixtures=1",
            headers={"Authorization": f"Bearer {co_token}"},
            timeout=3,
        )
        assert co_include.status_code == 200
        assert co_include.json()["show_fixtures"] is False
        assert co_include.json()["active"] == default_resp.json()["active"]

    def test_screening_queue_excludes_fixture_apps_by_default_and_supports_admin_opt_in(self, api_server):
        """Screening queue rows inherit the canonical fixture exclusion policy."""
        from auth import create_token
        from db import get_db

        real_id = "app_pr1_screen_real"
        real_ref = "ARF-PR1-SCREEN-REAL"
        fixture_id = "app_pr1_screen_fixture"
        fixture_ref = "ARF-PR1-SCREEN-FIXTURE"
        report = {
            "screening_report": {
                "screened_at": "2026-05-04T08:00:00Z",
                "screening_mode": "live",
                "company_screening": {
                    "found": True,
                    "sanctions": {"matched": False, "results": [], "api_status": "live", "source": "sumsub"},
                },
                "director_screenings": [],
                "ubo_screenings": [],
                "ip_geolocation": {"risk_level": "LOW"},
                "kyc_applicants": [],
                "overall_flags": [],
                "total_hits": 0,
            }
        }

        conn = get_db()
        conn.execute("DELETE FROM applications WHERE id IN (?, ?)", (real_id, fixture_id))
        for app_id, app_ref, name, is_fixture in (
            (real_id, real_ref, "PR1 Screening Real Ltd", 0),
            (fixture_id, fixture_ref, "PR1 Screening Fixture Ltd", 1),
        ):
            conn.execute(
                """
                INSERT INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data, is_fixture)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (app_id, app_ref, "testclient001", name, "Mauritius", "Technology",
                 "SME", "in_review", json.dumps(report), is_fixture),
            )
        conn.commit()
        conn.close()

        admin_token = create_token("admin001", "admin", "Test Admin", "officer")
        co_token = create_token("co001", "co", "Test CO", "officer")
        default_resp = http_requests.get(
            f"{api_server}/api/screening/queue",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=3,
        )
        assert default_resp.status_code == 200
        default_refs = {r["application_ref"] for r in default_resp.json()["rows"]}
        assert real_ref in default_refs
        assert fixture_ref not in default_refs
        assert default_resp.json()["show_fixtures"] is False

        include_resp = http_requests.get(
            f"{api_server}/api/screening/queue?include_fixtures=1",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=3,
        )
        assert include_resp.status_code == 200
        include_refs = {r["application_ref"] for r in include_resp.json()["rows"]}
        assert {real_ref, fixture_ref}.issubset(include_refs)
        assert include_resp.json()["show_fixtures"] is True

        co_include = http_requests.get(
            f"{api_server}/api/screening/queue?include_fixtures=1",
            headers={"Authorization": f"Bearer {co_token}"},
            timeout=3,
        )
        assert co_include.status_code == 200
        co_refs = {r["application_ref"] for r in co_include.json()["rows"]}
        assert fixture_ref not in co_refs
        assert co_include.json()["show_fixtures"] is False

    def test_edd_findings_sla_dual_control_and_audit_ref_target(self, api_server):
        """EDD can advance through legitimate gates and its audit is case-reconstructable."""
        from auth import create_token
        from db import get_db

        app_id = "app_p4_edd_gate"
        app_ref = "ARF-P4-EDD-GATE"
        conn = get_db()
        conn.execute("DELETE FROM edd_findings WHERE edd_case_id IN (SELECT id FROM edd_cases WHERE application_id = ?)", (app_id,))
        conn.execute("DELETE FROM edd_cases WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM audit_log WHERE target IN (?, ?, ?)", (app_ref, f"application:{app_ref}", "EDD-999999"))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (app_id, app_ref, "testclient001", "Phase 4 EDD Gate Ltd",
             "Mauritius", "Fintech", "SME", "edd_required", "HIGH", 80),
        )
        conn.execute(
            """
            INSERT INTO edd_cases
            (application_id, client_name, risk_level, risk_score, stage, assigned_officer, trigger_source, edd_notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (app_id, "Phase 4 EDD Gate Ltd", "HIGH", 80, "analysis", "admin001", "phase4_test", "[]"),
        )
        case_id = conn.execute(
            "SELECT id FROM edd_cases WHERE application_id = ? ORDER BY id DESC LIMIT 1",
            (app_id,),
        ).fetchone()["id"]
        conn.commit()
        conn.close()

        admin_token = create_token("admin001", "admin", "Test Admin", "officer")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        future_due = "2026-12-31"

        blocked = http_requests.patch(
            f"{api_server}/api/edd/cases/{case_id}",
            headers=admin_headers,
            json={"stage": "pending_senior_review", "sla_due_at": future_due, "senior_reviewer": "sco001"},
            timeout=3,
        )
        assert blocked.status_code == 400
        assert "Structured EDD findings" in blocked.text

        findings = http_requests.patch(
            f"{api_server}/api/edd/cases/{case_id}/findings",
            headers=admin_headers,
            json={
                "findings": {
                    "recommended_outcome": "approve_with_conditions",
                    "findings_summary": "EDD findings support senior review.",
                    "key_concerns": ["Opaque ownership reviewed"],
                    "mitigating_evidence": ["Source of wealth evidence obtained"],
                    "rationale": "Residual risk acceptable with conditions.",
                }
            },
            timeout=3,
        )
        assert findings.status_code == 200, findings.text
        assert findings.json()["case_status"]["findings_complete"] is True

        same_officer = http_requests.patch(
            f"{api_server}/api/edd/cases/{case_id}",
            headers=admin_headers,
            json={"stage": "pending_senior_review", "sla_due_at": future_due, "senior_reviewer": "admin001"},
            timeout=3,
        )
        assert same_officer.status_code == 400
        assert "different from the assigned officer" in same_officer.text

        ineligible_senior = http_requests.patch(
            f"{api_server}/api/edd/cases/{case_id}",
            headers=admin_headers,
            json={"stage": "pending_senior_review", "sla_due_at": future_due, "senior_reviewer": "co001"},
            timeout=3,
        )
        assert ineligible_senior.status_code == 400
        assert "Senior Compliance Officer or Admin" in ineligible_senior.text

        accepted = http_requests.patch(
            f"{api_server}/api/edd/cases/{case_id}",
            headers=admin_headers,
            json={"stage": "pending_senior_review", "sla_due_at": future_due, "senior_reviewer": "sco001"},
            timeout=3,
        )
        assert accepted.status_code == 200, accepted.text

        co_token = create_token("co001", "co", "Compliance Officer", "officer")
        co_close = http_requests.patch(
            f"{api_server}/api/edd/cases/{case_id}",
            headers={"Authorization": f"Bearer {co_token}"},
            json={"stage": "edd_approved", "decision_reason": "EDD reviewed and approved."},
            timeout=3,
        )
        assert co_close.status_code == 403
        assert "Senior Compliance Officer or Admin" in co_close.text

        assigned_close = http_requests.patch(
            f"{api_server}/api/edd/cases/{case_id}",
            headers=admin_headers,
            json={"stage": "edd_approved", "decision_reason": "EDD reviewed and approved."},
            timeout=3,
        )
        assert assigned_close.status_code == 403
        assert "different officer" in assigned_close.text

        sco_token = create_token("sco001", "sco", "Senior Officer", "officer")
        closed = http_requests.patch(
            f"{api_server}/api/edd/cases/{case_id}",
            headers={"Authorization": f"Bearer {sco_token}"},
            json={"stage": "edd_approved", "decision_reason": "EDD reviewed and approved by SCO."},
            timeout=3,
        )
        assert closed.status_code == 200, closed.text

        audit = http_requests.get(
            f"{api_server}/api/audit?ref={app_ref}&limit=50",
            headers=admin_headers,
            timeout=3,
        )
        assert audit.status_code == 200
        entries = audit.json()["entries"]
        assert any(e["action"] == "edd.findings.created" and e["target"] == app_ref for e in entries)
        assert any(e["action"] == "EDD Update" and e["target"] == app_ref for e in entries)
        assert any(e["action"] == "EDD Closure (dual-control)" and e["target"] == app_ref for e in entries)
        assert not any(str(e["target"]).startswith("EDD-") for e in entries)

    def test_evidence_pack_includes_notes_and_edd_findings(self, api_server):
        """Single-call evidence pack must include officer notes and structured EDD findings."""
        from auth import create_token
        from db import get_db

        app_id = "app_p4_pack_complete"
        app_ref = "ARF-P4-PACK-COMPLETE"
        conn = get_db()
        conn.execute("DELETE FROM edd_findings WHERE edd_case_id IN (SELECT id FROM edd_cases WHERE application_id = ?)", (app_id,))
        conn.execute("DELETE FROM edd_cases WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM application_notes WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (app_id, app_ref, "testclient001", "Phase 4 Evidence Pack Ltd",
             "Mauritius", "Technology", "SME", "edd_required", "HIGH", 75),
        )
        conn.execute(
            "INSERT INTO application_notes (application_id, user_id, user_name, user_role, content) VALUES (?, ?, ?, ?, ?)",
            (app_id, "admin001", "Test Admin", "admin", "Officer note for evidence pack."),
        )
        conn.execute(
            """
            INSERT INTO edd_cases
            (application_id, client_name, risk_level, risk_score, stage, assigned_officer, senior_reviewer, sla_due_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (app_id, "Phase 4 Evidence Pack Ltd", "HIGH", 75, "pending_senior_review", "co001", "sco001", "2026-12-31"),
        )
        case_id = conn.execute(
            "SELECT id FROM edd_cases WHERE application_id = ? ORDER BY id DESC LIMIT 1",
            (app_id,),
        ).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO edd_findings
            (edd_case_id, findings_summary, key_concerns, mitigating_evidence, recommended_outcome)
            VALUES (?, ?, ?, ?, ?)
            """,
            (case_id, "EDD evidence pack findings are complete.",
             json.dumps(["Ownership concern reviewed"]),
             json.dumps(["Mitigating bank evidence obtained"]),
             "approve_with_conditions"),
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/applications/{app_ref}/evidence-pack",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200, resp.text
        pack = resp.json()
        assert len(pack["application_notes"]) == 1
        assert pack["application_notes"][0]["content"] == "Officer note for evidence pack."
        assert pack["edd_cases"][0]["findings"]["recommended_outcome"] == "approve_with_conditions"
        assert pack["edd_cases"][0]["case_status"]["findings_complete"] is True

    def test_document_review_persists_and_survives_detail_reload(self, api_server):
        """POST /api/documents/:id/review should persist officer review truth on the document record."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, country, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "app_doc_review",
            "ARF-2026-DOCREV",
            "testclient001",
            "Docs Review Ltd",
            "Mauritius",
            "in_review"
        ))
        conn.execute("""
            INSERT INTO documents (
                id, application_id, person_id, doc_type, doc_name, file_path, verification_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            "doc_review_1",
            "app_doc_review",
            "dir99",
            "passport",
            "director-passport.pdf",
            "/tmp/director-passport.pdf",
            "verified"
        ))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        review_resp = http_requests.post(
            f"{api_server}/api/documents/doc_review_1/review",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "info_requested", "comment": "Need clearer scan of the passport MRZ."},
            timeout=3
        )
        assert review_resp.status_code == 200
        review_data = review_resp.json()
        assert review_data["review_status"] == "info_requested"
        assert review_data["review_comment"] == "Need clearer scan of the passport MRZ."

        detail_resp = http_requests.get(
            f"{api_server}/api/applications/ARF-2026-DOCREV",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3
        )
        assert detail_resp.status_code == 200
        detail_data = detail_resp.json()
        assert detail_data["documents"][0]["review_status"] == "info_requested"
        assert detail_data["documents"][0]["review_comment"] == "Need clearer scan of the passport MRZ."
        assert detail_data["documents"][0]["reviewed_by"] == "admin001"
        assert detail_data["documents"][0]["reviewed_by_name"]

    def test_application_detail_backfills_sparse_prescreening_from_saved_session(self, api_server):
        """Authoritative detail should backfill sparse legacy prescreening JSON from saved portal session data."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                ownership_structure, prescreening_data, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "app_detail_backfill",
            "ARF-2026-BACKFILL",
            "testclient001",
            "Legacy Backfill Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "Simple ownership",
            json.dumps({"registered_entity_name": "Legacy Backfill Ltd"}),
            "in_review"
        ))
        conn.execute("""
            INSERT INTO client_sessions (client_id, application_id, form_data, last_step)
            VALUES (?, ?, ?, ?)
        """, (
            "testclient001",
            "app_detail_backfill",
            json.dumps({
                "prescreening": {
                    "f-trade-name": "Legacy Trade Name",
                    "f-source-wealth-type": "Business revenue / trading profits",
                    "f-source-wealth": "Generated from software revenues.",
                    "f-intro-method": "Introduced by partner",
                    "f-mgmt": "Founder-led management team"
                }
            }),
            2
        ))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/applications/ARF-2026-BACKFILL",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["prescreening_data"]["trading_name"] == "Legacy Trade Name"
        assert data["prescreening_data"]["source_of_wealth_type"] == "Business revenue / trading profits"
        assert data["prescreening_data"]["introduction_method"] == "Introduced by partner"
        assert data["prescreening_data"]["management_overview"] == "Founder-led management team"

    def test_document_verify_returns_persisted_authoritative_contract(self, api_server):
        """POST /api/documents/:id/verify should return the persisted verification payload the portal/back office reload later consume."""
        from auth import create_token
        from db import get_db

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as handle:
            handle.write(b"%PDF-1.4 authoritative verification test")
            file_path = handle.name

        conn = get_db()
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, country, status, prescreening_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            "app_doc_verify",
            "ARF-2026-DOCVERIFY",
            "testclient001",
            "Verify Corp Ltd",
            "Mauritius",
            "draft",
            json.dumps({"registered_entity_name": "Verify Corp Ltd", "country_of_incorporation": "Mauritius"})
        ))
        conn.execute("""
            INSERT INTO documents (id, application_id, doc_type, doc_name, file_path, file_size, mime_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            "doc_verify_1",
            "app_doc_verify",
            "cert_inc",
            "verify.pdf",
            file_path,
            os.path.getsize(file_path),
            "application/pdf"
        ))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/documents/doc_verify_1/verify",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["verification_status"] in ("verified", "flagged")
        assert isinstance(body["verification_results"]["checks"], list)
        assert body["verification_results"]["overall"] == body["verification_status"]
        assert body["verification_results"]["subject_type"] in ("application_company", "director", "ubo", "intermediary_company", "person")
        assert body["verified_at"]

        conn = get_db()
        stored = conn.execute("""
            SELECT verification_status, verification_results, verified_at
            FROM documents WHERE id = ?
        """, ("doc_verify_1",)).fetchone()
        conn.close()

        stored_results = json.loads(stored["verification_results"])
        assert stored["verification_status"] == body["verification_status"]
        assert stored_results == body["verification_results"]
        assert stored["verified_at"]


# ═══════════════════════════════════════════════════════════
# 4. Security Headers — must be present on every response
# ═══════════════════════════════════════════════════════════

class TestSecurityHeaders:
    def test_security_headers_present(self, api_server):
        """X-Content-Type-Options, X-Frame-Options, X-XSS-Protection must be set."""
        resp = http_requests.get(f"{api_server}/api/health", timeout=3)
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("X-XSS-Protection") == "1; mode=block"

    def test_csp_header_present(self, api_server):
        """Content-Security-Policy header must be set."""
        resp = http_requests.get(f"{api_server}/api/health", timeout=3)
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "default-src" in csp


# ═══════════════════════════════════════════════════════════
# 5. Sprint 3 — PDF Download Endpoint
# ═══════════════════════════════════════════════════════════

class TestMemoPDFEndpoint:
    def test_pdf_requires_auth(self, api_server):
        """GET /api/applications/:id/memo/pdf without token must return 401."""
        resp = http_requests.get(f"{api_server}/api/applications/nonexistent/memo/pdf", timeout=3)
        assert resp.status_code == 401

    def test_pdf_returns_404_no_memo(self, api_server):
        """GET /api/applications/:id/memo/pdf with valid token but no memo must return 404."""
        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(f"{api_server}/api/applications/nonexistent/memo/pdf",
                                 headers={"Authorization": f"Bearer {token}"}, timeout=3)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════
# 6. Decision Records Endpoint
# ═══════════════════════════════════════════════════════════

class TestDecisionRecordsEndpoint:
    def test_decision_records_requires_auth(self, api_server):
        """GET /api/applications/:id/decision-records without token must return 401."""
        resp = http_requests.get(f"{api_server}/api/applications/nonexistent/decision-records", timeout=3)
        assert resp.status_code == 401

    def test_decision_records_returns_404_for_unknown_app(self, api_server):
        """GET /api/applications/:id/decision-records for non-existent app must return 404."""
        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(f"{api_server}/api/applications/nonexistent-app/decision-records",
                                 headers={"Authorization": f"Bearer {token}"}, timeout=3)
        assert resp.status_code == 404

    def test_decision_records_returns_empty_list(self, api_server):
        """GET /api/applications/:id/decision-records for app with no records returns empty list."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        conn.execute("""
            INSERT OR IGNORE INTO applications (id, ref, company_name, status)
            VALUES (?, ?, ?, ?)
        """, ("app_dec_rec_test", "ARF-2026-DECREC", "DecRec Test Corp", "in_review"))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(f"{api_server}/api/applications/app_dec_rec_test/decision-records",
                                 headers={"Authorization": f"Bearer {token}"}, timeout=3)
        assert resp.status_code == 200
        body = resp.json()
        assert "records" in body
        assert body["count"] == 0
        assert body["records"] == []

    def test_decision_records_invalid_limit_returns_400(self, api_server):
        """GET /api/applications/:id/decision-records?limit=abc must return 400."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        conn.execute("""
            INSERT OR IGNORE INTO applications (id, ref, company_name, status)
            VALUES (?, ?, ?, ?)
        """, ("app_dec_rec_test", "ARF-2026-DECREC", "DecRec Test Corp", "in_review"))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(f"{api_server}/api/applications/app_dec_rec_test/decision-records?limit=abc",
                                 headers={"Authorization": f"Bearer {token}"}, timeout=3)
        assert resp.status_code == 400

    def test_decision_records_negative_limit_returns_400(self, api_server):
        """GET /api/applications/:id/decision-records?limit=-5 must return 400."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        conn.execute("""
            INSERT OR IGNORE INTO applications (id, ref, company_name, status)
            VALUES (?, ?, ?, ?)
        """, ("app_dec_rec_test", "ARF-2026-DECREC", "DecRec Test Corp", "in_review"))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(f"{api_server}/api/applications/app_dec_rec_test/decision-records?limit=-5",
                                 headers={"Authorization": f"Bearer {token}"}, timeout=3)
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════
# A8. Password Rotation Regression Guard
# ═══════════════════════════════════════════════════════════

class TestPasswordRotationGuard:
    """A8: PUT /api/users/{id} must reject the password field with 400."""

    def test_put_user_rejects_password_field(self, api_server):
        """PUT /api/users/{id} with {"password":"new"} must return 400
        with a message about the dedicated password-change flow."""
        from auth import create_token
        from db import get_db

        # Ensure a target user exists
        conn = get_db()
        conn.execute("""
            INSERT OR IGNORE INTO users (id, email, password_hash, full_name, role, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("pwd_test_user", "pwd_test@test.com", "hash", "PwdTest User", "analyst", "active"))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.put(
            f"{api_server}/api/users/pwd_test_user",
            json={"password": "newpassword123"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 400, (
            f"Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "dedicated password-change flow" in body.get("error", ""), (
            f"Expected password-change flow message, got: {body}"
        )


# ═══════════════════════════════════════════════════════════
# Phase 1B — Governance Rejection Audit Logging
# ═══════════════════════════════════════════════════════════

class TestGovernanceAttemptAudit:
    def _live_prescreening(self):
        return json.dumps({
            "screening_report": {
                "screening_mode": "live",
                "screened_at": "2026-04-30T10:00:00",
                "sanctions": {"api_status": "live"},
                "company_registry": {"api_status": "live"},
                "ip_geolocation": {"api_status": "live"},
                "kyc": {"api_status": "live"},
            },
            "screening_valid_until": "2026-07-29T10:00:00",
            "screening_validity_days": 90,
        })

    def _insert_approved_memo(self, conn, app_id):
        conn.execute("""
            INSERT INTO compliance_memos (
                application_id, memo_data, generated_by, ai_recommendation,
                review_status, quality_score, validation_status, supervisor_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id,
            json.dumps({
                "ai_source": "deterministic",
                "metadata": {"ai_source": "deterministic"},
                "supervisor": {"verdict": "CONSISTENT", "can_approve": True},
            }),
            "system",
            "APPROVE_WITH_CONDITIONS",
            "approved",
            8.5,
            "pass",
            "CONSISTENT",
        ))

    def test_governance_attempt_audit_failure_is_best_effort(self, monkeypatch, caplog):
        """Audit insert failures must log the marker and not raise to the handler."""
        import logging
        import base_handler
        from base_handler import BaseHandler

        class FailingDb:
            closed = False

            def execute(self, *_args, **_kwargs):
                raise RuntimeError("forced audit insert failure")

            def commit(self):
                raise AssertionError("commit should not run after failed insert")

            def close(self):
                self.closed = True

        failing_db = FailingDb()
        monkeypatch.setattr(base_handler, "get_db", lambda: failing_db)
        handler = object.__new__(BaseHandler)

        caplog.set_level(logging.ERROR)
        handler.log_governance_attempt(
            {"sub": "admin001", "name": "Test Admin", "role": "admin"},
            "application.decision",
            "ARF-TEST",
            "rejected",
            400,
            "forced rejection",
        )

        assert failing_db.closed is True
        assert "governance_audit_write_failed=true" in caplog.text
        assert "application.decision" in caplog.text

    def test_governance_attempt_rejection_reason_is_capped(self, monkeypatch):
        """Long rejection reasons must not defeat the bounded audit detail size."""
        import base_handler
        from base_handler import BaseHandler

        class CapturingDb:
            params = None
            committed = False
            closed = False

            def execute(self, _sql, params):
                self.params = params

            def commit(self):
                self.committed = True

            def close(self):
                self.closed = True

        capture_db = CapturingDb()
        monkeypatch.setattr(base_handler, "get_db", lambda: capture_db)
        handler = object.__new__(BaseHandler)

        handler.log_governance_attempt(
            {"sub": "admin001", "name": "Test Admin", "role": "admin"},
            "application.decision",
            "ARF-TEST",
            "rejected",
            400,
            "r" * 2000,
        )

        assert capture_db.committed is True
        assert capture_db.closed is True
        detail = json.loads(capture_db.params[5])
        assert len(detail["rejection_reason"]) == 512
        assert detail["rejection_reason_truncated"] is True

    def test_failed_approval_attempt_is_audited(self, api_server):
        """Approval gate rejections must be visible in audit_log with outcome=rejected."""
        from auth import create_token
        from db import get_db

        app_id = "app_phase1b_failed_approval"
        app_ref = "ARF-2026-PHASE1B-FAIL"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                status, risk_level, risk_score, prescreening_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id,
            app_ref,
            "phase1b_client",
            "Phase 1B Failed Approval Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "in_review",
            "LOW",
            20,
            self._live_prescreening(),
        ))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/applications/{app_id}/decision",
            json={
                "decision": "approve",
                "decision_reason": "Testing approval gate rejection audit.",
                "officer_signoff": {
                    "acknowledged": True,
                    "scope": "decision",
                    "source_context": "ai_advisory",
                },
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 400
        assert "compliance memo" in resp.json().get("error", "").lower()

        conn = get_db()
        row = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE target = ? AND action = 'Governance Attempt'
            ORDER BY id DESC LIMIT 1
            """,
            (app_ref,),
        ).fetchone()
        conn.close()

        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["action"] == "application.decision"
        assert detail["outcome"] == "rejected"
        assert detail["response_code"] == 400
        assert "compliance memo" in detail["rejection_reason"].lower()

    def test_failed_screening_review_attempt_is_audited(self, api_server):
        """Screening disposition rejections must leave an audit row."""
        from auth import create_token
        from db import get_db

        app_id = "app_phase1b_screening_review"
        app_ref = "ARF-2026-PHASE1B-SCREEN"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, status)
            VALUES (?, ?, ?, ?, ?)
        """, (app_id, app_ref, "phase1b_client", "Phase 1B Screening Ltd", "in_review"))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_id,
                "subject_type": "company",
                "subject_name": "Phase 1B Screening Ltd",
                "disposition": "unsupported",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 400

        conn = get_db()
        row = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE target = ? AND action = 'Governance Attempt'
            ORDER BY id DESC LIMIT 1
            """,
            (app_id,),
        ).fetchone()
        conn.close()

        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["action"] == "screening.review_disposition"
        assert detail["outcome"] == "rejected"
        assert detail["response_code"] == 400

    def test_pre_approval_rejection_attempt_is_audited(self, api_server):
        """Pre-approval gate rejections must write a Governance Attempt row."""
        from auth import create_token
        from db import get_db

        app_id = "app_phase1b_preapproval_reject"
        app_ref = "ARF-2026-PHASE1B-PRE-REJ"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                status, risk_level, risk_score, prescreening_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id, app_ref, "phase1b_client", "Phase 1B Pre Reject Ltd",
            "Mauritius", "Technology", "SME", "draft", "HIGH", 72,
            self._live_prescreening(),
        ))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/applications/{app_id}/pre-approval-decision",
            json={"decision": "PRE_APPROVE", "notes": "Testing rejected pre-approval audit."},
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 400

        conn = get_db()
        row = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE target = ? AND action = 'Governance Attempt'
            ORDER BY id DESC LIMIT 1
            """,
            (app_ref,),
        ).fetchone()
        conn.close()

        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["action"] == "application.pre_approval_decision"
        assert detail["outcome"] == "rejected"
        assert detail["response_code"] == 400

    def test_accepted_pre_approval_attempt_is_audited(self, api_server):
        """Accepted pre-approval decisions must be audited in the same handler path."""
        from auth import create_token
        from db import get_db

        app_id = "app_phase1b_preapproval_accept"
        app_ref = "ARF-2026-PHASE1B-PRE-OK"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                status, risk_level, risk_score, prescreening_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id, app_ref, "phase1b_client", "Phase 1B Pre Accept Ltd",
            "Mauritius", "Technology", "SME", "pre_approval_review", "HIGH", 72,
            self._live_prescreening(),
        ))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/applications/{app_id}/pre-approval-decision",
            json={"decision": "PRE_APPROVE", "notes": "Testing accepted pre-approval audit."},
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 201

        conn = get_db()
        row = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE target = ? AND action = 'Governance Attempt'
            ORDER BY id DESC LIMIT 1
            """,
            (app_ref,),
        ).fetchone()
        conn.close()

        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["action"] == "application.pre_approval_decision"
        assert detail["outcome"] == "accepted"
        assert detail["response_code"] == 201

    def test_accepted_screening_review_attempt_is_audited(self, api_server):
        """Accepted screening dispositions must write a Governance Attempt row."""
        from auth import create_token
        from db import get_db

        app_id = "app_phase1b_screening_accept"
        app_ref = "ARF-2026-PHASE1B-SCREEN-OK"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM screening_reviews WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, status)
            VALUES (?, ?, ?, ?, ?)
        """, (app_id, app_ref, "phase1b_client", "Phase 1B Screening Accept Ltd", "in_review"))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_id,
                "subject_type": "company",
                "subject_name": "Phase 1B Screening Accept Ltd",
                "disposition": "cleared",
                "disposition_code": "provider_no_relevant_match",
                "rationale": "Testing accepted screening audit with a recorded rationale.",
                "notes": "Testing accepted screening audit.",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200

        conn = get_db()
        row = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE target = ? AND action = 'Governance Attempt'
            ORDER BY id DESC LIMIT 1
            """,
            (app_ref,),
        ).fetchone()
        review = conn.execute(
            "SELECT subject_type, disposition_code, rationale FROM screening_reviews WHERE application_id = ?",
            (app_id,),
        ).fetchone()
        conn.close()

        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["action"] == "screening.review_disposition"
        assert detail["outcome"] == "accepted"
        assert detail["response_code"] == 200
        assert review is not None
        assert review["subject_type"] == "entity"
        assert review["disposition_code"] == "provider_no_relevant_match"
        assert review["rationale"] == "Testing accepted screening audit with a recorded rationale."

    def test_screening_review_requires_code_and_rationale(self, api_server):
        """Screening dispositions must reject missing code/rationale before state changes."""
        from auth import create_token
        from db import get_db

        app_id = "app_phase1c_screening_required_fields"
        app_ref = "ARF-2026-PHASE1C-SCREEN-REQ"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_id,))
        conn.execute("DELETE FROM screening_reviews WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, status)
            VALUES (?, ?, ?, ?, ?)
        """, (app_id, app_ref, "phase1c_client", "Phase 1C Required Fields Ltd", "in_review"))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_id,
                "subject_type": "entity",
                "subject_name": "Phase 1C Required Fields Ltd",
                "disposition": "cleared",
                "notes": "Legacy note only.",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 400
        assert "disposition_code" in resp.json()["error"]

        invalid_code = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_id,
                "subject_type": "entity",
                "subject_name": "Phase 1C Required Fields Ltd",
                "disposition": "cleared",
                "disposition_code": "not_a_real_code",
                "rationale": "This rationale is long enough but the code is invalid.",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert invalid_code.status_code == 400
        assert "disposition_code" in invalid_code.json()["error"]

        short_rationale = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_id,
                "subject_type": "entity",
                "subject_name": "Phase 1C Required Fields Ltd",
                "disposition": "cleared",
                "disposition_code": "provider_no_relevant_match",
                "rationale": "Too short",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert short_rationale.status_code == 400
        assert "rationale" in short_rationale.json()["error"]

        thin_clear_rationale = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_id,
                "subject_type": "entity",
                "subject_name": "Phase 1C Required Fields Ltd",
                "disposition": "cleared",
                "disposition_code": "provider_no_relevant_match",
                "rationale": "False positive",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert thin_clear_rationale.status_code == 400
        assert "40 characters" in thin_clear_rationale.json()["error"]
        assert "8 words" in thin_clear_rationale.json()["error"]

        conn = get_db()
        review = conn.execute("SELECT id FROM screening_reviews WHERE application_id = ?", (app_id,)).fetchone()
        row = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE target = ? AND action = 'Governance Attempt'
            ORDER BY id DESC LIMIT 1
            """,
            (app_id,),
        ).fetchone()
        conn.close()

        assert review is None
        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["action"] == "screening.review_disposition"
        assert detail["outcome"] == "rejected"
        assert detail["response_code"] == 400

    def test_sensitive_screening_clear_requires_second_reviewer(self, api_server):
        """Director/UBO sensitive clears require two distinct officer sign-offs."""
        from auth import create_token
        from db import get_db

        app_id = "app_phase1c_sensitive_screening"
        app_ref = "ARF-2026-PHASE1C-SENSITIVE"
        subject_name = "Alice Sensitive"
        prescreening = {
            "screening_report": {
                "screened_at": "2026-04-30T10:00:00Z",
                "screening_mode": "live",
                "company_screening": {
                    "found": True,
                    "sanctions": {"matched": False, "results": [], "api_status": "live"},
                },
                "director_screenings": [{
                    "person_name": subject_name,
                    "person_type": "director",
                    "screening": {
                        "matched": True,
                        "results": [{"name": "Alice Sensitive", "is_sanctioned": True, "is_pep": False}],
                        "api_status": "live",
                        "screened_at": "2026-04-30T10:00:00Z",
                    },
                }],
                "ubo_screenings": [],
                "ip_geolocation": {"risk_level": "LOW"},
                "kyc_applicants": [],
                "overall_flags": ["Director sanctions match"],
                "total_hits": 1,
            }
        }

        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM screening_reviews WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM directors WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id, app_ref, "phase1c_client", "Phase 1C Sensitive Ltd",
            "Mauritius", "Technology", "SME", "in_review", json.dumps(prescreening),
        ))
        conn.execute(
            "INSERT INTO directors (application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?)",
            (app_id, subject_name, "Mauritius", "No"),
        )
        conn.commit()
        conn.close()

        first_token = create_token("admin001", "admin", "Test Admin", "officer")
        first_payload = {
            "application_id": app_ref,
            "subject_type": "director",
            "subject_name": subject_name,
            "disposition": "cleared",
            "disposition_code": "false_positive",
            "rationale": "Provider hit reviewed against identity documents and assessed as false positive.",
        }
        resp = http_requests.post(
            f"{api_server}/api/screening/review",
            json=first_payload,
            headers={"Authorization": f"Bearer {first_token}"},
            timeout=3,
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "second_review_required"

        queue = http_requests.get(
            f"{api_server}/api/screening/queue",
            headers={"Authorization": f"Bearer {first_token}"},
            timeout=3,
        )
        assert queue.status_code == 200
        row = next(r for r in queue.json()["rows"] if r["application_ref"] == app_ref and r["subject_name"] == subject_name)
        assert row["review_four_eyes_status"] == "pending_second_review"
        assert row["review_resolved"] is False

        retry = http_requests.post(
            f"{api_server}/api/screening/review",
            json=first_payload,
            headers={"Authorization": f"Bearer {first_token}"},
            timeout=3,
        )
        assert retry.status_code == 409

        conn = get_db()
        row = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE target = ? AND action = 'Governance Attempt'
            ORDER BY id DESC LIMIT 1
            """,
            (app_ref,),
        ).fetchone()
        conn.close()

        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["action"] == "screening.review_disposition"
        assert detail["outcome"] == "rejected"
        assert detail["response_code"] == 409

        second_token = create_token("sco_phase1c", "sco", "Second Officer", "officer")
        second_payload = dict(first_payload)
        second_payload["disposition_code"] = "identity_mismatch"
        second_payload["rationale"] = "Independent review confirms the provider hit is not the same individual."
        second = http_requests.post(
            f"{api_server}/api/screening/review",
            json=second_payload,
            headers={"Authorization": f"Bearer {second_token}"},
            timeout=3,
        )
        assert second.status_code == 200
        assert second.json()["status"] == "second_review_complete"

        conn = get_db()
        review = conn.execute(
            """
            SELECT disposition_code, rationale, second_disposition_code, second_rationale, second_reviewer_id
            FROM screening_reviews
            WHERE application_id = ? AND subject_type = ? AND subject_name = ?
            """,
            (app_id, "director", subject_name),
        ).fetchone()
        conn.close()

        assert review["disposition_code"] == "false_positive"
        assert review["rationale"] == first_payload["rationale"]
        assert review["second_disposition_code"] == "identity_mismatch"
        assert review["second_rationale"] == second_payload["rationale"]
        assert review["second_reviewer_id"] == "sco_phase1c"

        third_token = create_token("analyst_phase1c", "analyst", "Third Officer", "officer")
        third_payload = dict(second_payload)
        third_payload["rationale"] = "A third review should not overwrite completed two-officer review evidence."
        third = http_requests.post(
            f"{api_server}/api/screening/review",
            json=third_payload,
            headers={"Authorization": f"Bearer {third_token}"},
            timeout=3,
        )
        assert third.status_code == 409

        queue = http_requests.get(
            f"{api_server}/api/screening/queue",
            headers={"Authorization": f"Bearer {first_token}"},
            timeout=3,
        )
        row = next(r for r in queue.json()["rows"] if r["application_ref"] == app_ref and r["subject_name"] == subject_name)
        assert row["review_four_eyes_status"] == "complete"
        assert row["review_resolved"] is True
        assert row["second_reviewed_by"] == "Second Officer"

    def test_sensitive_screening_escalation_is_single_reviewer(self, api_server):
        """Escalating a sensitive screening hit should not require a second reviewer."""
        from auth import create_token
        from db import get_db

        app_id = "app_phase1c_sensitive_escalation"
        app_ref = "ARF-2026-PHASE1C-ESCALATE"
        subject_name = "Erin Escalated"
        prescreening = {
            "screening_report": {
                "screened_at": "2026-04-30T10:00:00Z",
                "screening_mode": "live",
                "company_screening": {
                    "found": True,
                    "sanctions": {"matched": False, "results": [], "api_status": "live"},
                },
                "director_screenings": [{
                    "person_name": subject_name,
                    "person_type": "director",
                    "screening": {
                        "matched": True,
                        "results": [{"name": subject_name, "is_sanctioned": True, "is_pep": False}],
                        "api_status": "live",
                        "screened_at": "2026-04-30T10:00:00Z",
                    },
                }],
                "ubo_screenings": [],
                "ip_geolocation": {"risk_level": "LOW"},
                "overall_flags": ["Director sanctions match"],
                "total_hits": 1,
            }
        }

        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM screening_reviews WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM directors WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id, app_ref, "phase1c_client", "Phase 1C Escalation Ltd",
            "Mauritius", "Technology", "SME", "in_review", json.dumps(prescreening),
        ))
        conn.execute(
            "INSERT INTO directors (application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?)",
            (app_id, subject_name, "Mauritius", "No"),
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_ref,
                "subject_type": "director",
                "subject_name": subject_name,
                "disposition": "escalated",
                "disposition_code": "potential_sanctions_match",
                "rationale": "Provider sanctions result requires escalation to compliance for investigation.",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "complete"
        assert body["requires_four_eyes"] is False
        assert body["sensitivity_flags"] == []

    def test_screening_review_context_error_fails_closed(self, api_server, monkeypatch):
        """A context derivation error should require four-eyes rather than single-officer clear."""
        import server as server_module
        from auth import create_token
        from db import get_db

        app_id = "app_phase1c_context_error"
        app_ref = "ARF-2026-PHASE1C-CONTEXT-ERR"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM screening_reviews WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, country, sector, entity_type, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id, app_ref, "phase1c_client", "Phase 1C Context Error Ltd",
            "Mauritius", "Technology", "SME", "in_review",
        ))
        conn.commit()
        conn.close()

        def fail_context(*_args, **_kwargs):
            raise RuntimeError("forced context failure")

        monkeypatch.setattr(server_module, "_screening_review_subject_context", fail_context)

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_ref,
                "subject_type": "entity",
                "subject_name": "Phase 1C Context Error Ltd",
                "disposition": "cleared",
                "disposition_code": "provider_no_relevant_match",
                "rationale": "Clear decision should fail closed when sensitivity context is unavailable.",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "second_review_required"
        assert body["requires_four_eyes"] is True
        assert "sensitivity_context_unavailable" in body["sensitivity_flags"]

    def test_first_approval_202_attempt_is_audited(self, api_server):
        """Dual-approval first approval must leave a 202 Governance Attempt row."""
        from auth import create_token
        from db import get_db

        app_id = "app_phase1b_dual_approval"
        app_ref = "ARF-2026-PHASE1B-DUAL"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM compliance_memos WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                status, risk_level, risk_score, prescreening_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id, app_ref, "phase1b_client", "Phase 1B Dual Approval Ltd",
            "Mauritius", "Banking", "NBFI", "compliance_review", "HIGH", 80,
            self._live_prescreening(),
        ))
        self._insert_approved_memo(conn, app_id)
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/applications/{app_id}/decision",
            json={
                "decision": "approve",
                "decision_reason": "Testing first dual-approval audit.",
                "officer_signoff": {
                    "acknowledged": True,
                    "scope": "decision",
                    "source_context": "ai_advisory",
                },
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 202

        conn = get_db()
        row = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE target = ? AND action = 'Governance Attempt'
            ORDER BY id DESC LIMIT 1
            """,
            (app_ref,),
        ).fetchone()
        conn.close()

        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["action"] == "application.decision"
        assert detail["outcome"] == "accepted"
        assert detail["response_code"] == 202

    def test_failed_memo_approval_attempt_is_audited(self, api_server):
        """Memo approval gate rejections must leave a Governance Attempt row."""
        from auth import create_token
        from db import get_db

        app_id = "app_day2_memo_approval_audit"
        app_ref = "ARF-2026-DAY2-MEMO-AUDIT"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM compliance_memos WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                status, risk_level, risk_score, prescreening_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id, app_ref, "phase1b_client", "Day 2 Memo Approval Audit Ltd",
            "Mauritius", "Technology", "SME", "compliance_review", "LOW", 20,
            self._live_prescreening(),
        ))
        self._insert_approved_memo(conn, app_id)
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/applications/{app_id}/memo/approve",
            json={},
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 400
        assert "officer_signoff" in resp.text

        conn = get_db()
        row = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE target = ? AND action = 'Governance Attempt'
            ORDER BY id DESC LIMIT 1
            """,
            (app_ref,),
        ).fetchone()
        conn.close()

        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["action"] == "memo.approve"
        assert detail["outcome"] == "rejected"
        assert detail["response_code"] == 400
        assert "officer_signoff" in detail["rejection_reason"]

    def test_failed_edd_update_attempt_is_audited(self, api_server):
        """EDD stage/update gate rejections must leave a Governance Attempt row."""
        from auth import create_token
        from db import get_db

        app_id = "app_day2_edd_attempt_audit"
        app_ref = "ARF-2026-DAY2-EDD-AUDIT"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM edd_cases WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (app_id, app_ref, "phase1b_client", "Day 2 EDD Attempt Audit Ltd",
             "Mauritius", "Fintech", "SME", "edd_required", "HIGH", 80),
        )
        conn.execute(
            """
            INSERT INTO edd_cases
            (application_id, client_name, risk_level, risk_score, stage, assigned_officer, trigger_source, edd_notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (app_id, "Day 2 EDD Attempt Audit Ltd", "HIGH", 80, "analysis", "admin001", "day2_test", "[]"),
        )
        case_id = conn.execute(
            "SELECT id FROM edd_cases WHERE application_id = ? ORDER BY id DESC LIMIT 1",
            (app_id,),
        ).fetchone()["id"]
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.patch(
            f"{api_server}/api/edd/cases/{case_id}",
            json={"stage": "not_a_stage"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 400
        assert "Invalid stage" in resp.text

        conn = get_db()
        row = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE target = ? AND action = 'Governance Attempt'
            ORDER BY id DESC LIMIT 1
            """,
            (app_ref,),
        ).fetchone()
        conn.close()

        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["action"] == "edd.case_update"
        assert detail["outcome"] == "rejected"
        assert detail["response_code"] == 400
        assert "Invalid stage" in detail["rejection_reason"]

    def test_governance_attempt_target_is_sanitized_for_missing_app(self, api_server):
        """Client-controlled app identifiers must be capped before audit persistence."""
        from auth import create_token
        from db import get_db

        raw_app_id = "missing-" + ("x" * 260)
        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/applications/{raw_app_id}/decision",
            json={
                "decision": "approve",
                "decision_reason": "Testing missing-app target capping.",
                "officer_signoff": {
                    "acknowledged": True,
                    "scope": "decision",
                    "source_context": "ai_advisory",
                },
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 404

        conn = get_db()
        row = conn.execute(
            """
            SELECT target, detail FROM audit_log
            WHERE action = 'Governance Attempt'
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        conn.close()

        assert row is not None
        assert len(row["target"]) <= 160
        assert row["target"] == raw_app_id[:160]
        detail = json.loads(row["detail"])
        assert detail["action"] == "application.decision"
        assert detail["response_code"] == 404
