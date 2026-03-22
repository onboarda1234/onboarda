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

    def test_login_with_empty_body_does_not_crash(self, api_server):
        """POST /api/auth/officer/login with empty JSON must not crash (4xx expected)."""
        resp = http_requests.post(f"{api_server}/api/auth/officer/login",
                                  json={}, timeout=3)
        assert resp.status_code in (400, 401)


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
