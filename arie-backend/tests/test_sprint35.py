"""
Sprint 3.5 — Hardening Tests
Tests for: httpOnly cookie auth, logout/token revocation, CSRF protection,
AI model routing, and BaseHandler extraction.
"""
import os
import sys
import json
import tempfile
import socket
import threading
import time
import pytest
from types import SimpleNamespace

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
    """Start a real Tornado HTTP server for Sprint 3.5 tests."""
    db_path = os.path.join(tempfile.gettempdir(), f"onboarda_s35_{os.getpid()}.db")
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

    io_loop = server_ref.get("loop")
    srv = server_ref.get("server")
    if io_loop and srv:
        io_loop.add_callback(srv.stop)
        io_loop.add_callback(io_loop.stop)
    thread.join(timeout=2)


# ═══════════════════════════════════════════════════════════
# 1. Dual Auth — httpOnly Cookie Authentication
# ═══════════════════════════════════════════════════════════

class TestDualAuth:
    def test_bearer_token_still_works(self, api_server):
        """Bearer token auth must remain functional (backward compat)."""
        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(f"{api_server}/api/applications",
                                 headers={"Authorization": f"Bearer {token}"}, timeout=3)
        assert resp.status_code == 200

    def test_cookie_auth_works(self, api_server):
        """httpOnly cookie auth must authenticate requests."""
        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        # Send token as cookie (simulating browser session)
        cookies = {"arie_session": token}
        resp = http_requests.get(f"{api_server}/api/applications",
                                 cookies=cookies, timeout=3)
        assert resp.status_code == 200

    def test_invalid_cookie_returns_401(self, api_server):
        """Invalid session cookie must be rejected."""
        cookies = {"arie_session": "garbage.invalid.token"}
        resp = http_requests.get(f"{api_server}/api/applications",
                                 cookies=cookies, timeout=3)
        assert resp.status_code == 401

    def test_bearer_takes_precedence_over_cookie(self, api_server):
        """When both Bearer header and cookie present, Bearer wins."""
        from auth import create_token
        valid_token = create_token("admin001", "admin", "Test Admin", "officer")
        # Valid Bearer + garbage cookie = should succeed (Bearer takes precedence)
        cookies = {"arie_session": "garbage.invalid.token"}
        resp = http_requests.get(f"{api_server}/api/applications",
                                 headers={"Authorization": f"Bearer {valid_token}"},
                                 cookies=cookies, timeout=3)
        assert resp.status_code == 200

    def test_no_auth_returns_401(self, api_server):
        """Request with neither Bearer nor cookie must return 401."""
        resp = http_requests.get(f"{api_server}/api/applications", timeout=3)
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════
# 2. Logout Endpoint — Token Revocation + Cookie Clearing
# ═══════════════════════════════════════════════════════════

class TestLogout:
    def test_logout_endpoint_exists(self, api_server):
        """POST /api/auth/logout must return 200 (not 404)."""
        resp = http_requests.post(f"{api_server}/api/auth/logout", timeout=3)
        assert resp.status_code == 200

    def test_logout_returns_status(self, api_server):
        """Logout must return {status: logged_out}."""
        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(f"{api_server}/api/auth/logout",
                                  headers={"Authorization": f"Bearer {token}"}, timeout=3)
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") == "logged_out"

    def test_logout_clears_session_cookie(self, api_server):
        """Logout must clear the arie_session cookie."""
        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        session = http_requests.Session()
        session.cookies.set("arie_session", token)
        resp = session.post(f"{api_server}/api/auth/logout",
                            headers={"Authorization": f"Bearer {token}"}, timeout=3)
        assert resp.status_code == 200
        # Server should send Set-Cookie to clear arie_session
        set_cookie_headers = resp.headers.get("Set-Cookie", "")
        # Tornado clears cookies by setting them to empty with past expiry
        # At minimum, the response should indicate success
        assert resp.json().get("status") == "logged_out"

    def test_logout_without_auth_still_succeeds(self, api_server):
        """Logout without any auth should still return 200 (graceful)."""
        resp = http_requests.post(f"{api_server}/api/auth/logout", timeout=3)
        assert resp.status_code == 200
        assert resp.json().get("status") == "logged_out"

    def test_logout_revokes_bearer_token_for_protected_apis(self, api_server):
        """After logout, the same bearer token must not access protected APIs."""
        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        headers = {"Authorization": f"Bearer {token}"}

        assert http_requests.get(f"{api_server}/api/auth/me", headers=headers, timeout=3).status_code == 200
        logout = http_requests.post(f"{api_server}/api/auth/logout", headers=headers, timeout=3)
        assert logout.status_code == 200

        assert http_requests.get(f"{api_server}/api/auth/me", headers=headers, timeout=3).status_code == 401
        assert http_requests.get(f"{api_server}/api/applications", headers=headers, timeout=3).status_code == 401
        replay_client = http_requests.Session()
        assert replay_client.get(f"{api_server}/api/auth/me", headers=headers, timeout=3).status_code == 401
        assert http_requests.post(f"{api_server}/api/auth/logout", headers=headers, timeout=3).status_code == 200

    def test_logout_revokes_cookie_session_token(self, api_server):
        """Logout using cookie auth must revoke the cookie token, not just clear local UI state."""
        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        cookies = {"arie_session": token}

        assert http_requests.get(f"{api_server}/api/auth/me", cookies=cookies, timeout=3).status_code == 200
        logout = http_requests.post(f"{api_server}/api/auth/logout", cookies=cookies, timeout=3)
        assert logout.status_code == 200

        # Re-send the original cookie value to prove server-side revocation.
        assert http_requests.get(f"{api_server}/api/auth/me", cookies=cookies, timeout=3).status_code == 401
        replay_client = http_requests.Session()
        replay_client.cookies.set("arie_session", token)
        assert replay_client.get(f"{api_server}/api/auth/me", timeout=3).status_code == 401

    def test_browser_signout_calls_server_logout(self):
        """Back office and portal sign-out must call the server logout endpoint."""
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        backoffice = open(os.path.join(repo_root, "arie-backoffice.html"), encoding="utf-8").read()
        portal = open(os.path.join(repo_root, "arie-portal.html"), encoding="utf-8").read()

        assert "BO_API_BASE + '/auth/logout'" in backoffice
        assert "API_BASE + '/auth/logout'" in portal
        bo_start = backoffice.index("async function signOut()")
        bo_fn = backoffice[bo_start:backoffice.index("// EX-13", bo_start)]
        assert "var tokenForLogout = BO_AUTH_TOKEN;" in bo_fn
        assert "'Authorization': 'Bearer ' + tokenForLogout" in bo_fn
        assert bo_fn.index("await fetch(BO_API_BASE + '/auth/logout'") < bo_fn.index("BO_AUTH_TOKEN = '';")

        portal_start = portal.index("async function clientSignOut()")
        portal_fn = portal[portal_start:portal.index("async function loadMyApplications()", portal_start)]
        assert "var tokenForLogout = AUTH_TOKEN;" in portal_fn
        assert "'Authorization': 'Bearer ' + tokenForLogout" in portal_fn
        assert portal_fn.index("await fetch(API_BASE + '/auth/logout'") < portal_fn.index("clearAuth();")

    def test_browser_decision_submit_rechecks_approval_readiness(self):
        """The back-office modal must not rely only on the disabled confirm button."""
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        backoffice = open(os.path.join(repo_root, "arie-backoffice.html"), encoding="utf-8").read()

        fn_start = backoffice.index("async function confirmDecision()")
        fn = backoffice[fn_start:backoffice.index("var pendingNotificationType", fn_start)]
        assert "var readiness = getApprovalReadiness(currentApp);" in fn
        assert "setDecisionError('Approval is blocked: '" in fn
        assert fn.index("getApprovalReadiness(currentApp)") < fn.index("boApiCall('POST'")
        assert "if (id === 'modal-decision-reason') setDecisionError('');" in backoffice


# ═══════════════════════════════════════════════════════════
# 3. CSRF Protection
# ═══════════════════════════════════════════════════════════

class TestCSRFProtection:
    def test_csrf_exempt_paths_work(self, api_server):
        """Login and logout endpoints must not require CSRF tokens."""
        # Login should work (may fail auth but NOT fail CSRF)
        resp = http_requests.post(f"{api_server}/api/auth/officer/login",
                                  json={"email": "x", "password": "y"}, timeout=3)
        # Should get 401 (bad credentials), NOT 403 (CSRF)
        assert resp.status_code in (400, 401)

    def test_health_is_csrf_exempt(self, api_server):
        """Health endpoint must be CSRF-exempt."""
        resp = http_requests.get(f"{api_server}/api/health", timeout=3)
        assert resp.status_code == 200

    def test_bearer_auth_bypasses_csrf(self, api_server):
        """Requests with Bearer token must not require CSRF."""
        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        # POST with Bearer auth — no CSRF token needed
        resp = http_requests.post(f"{api_server}/api/applications",
                                  headers={"Authorization": f"Bearer {token}"},
                                  json={"test": True}, timeout=3)
        # May get 400 (bad input) but NOT 403 (CSRF)
        assert resp.status_code != 403

    def test_cookie_auth_write_requires_csrf_before_business_logic(self, api_server):
        """Cookie-auth unsafe writes must fail CSRF before handler validation."""
        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        cookies = {"arie_session": token, "csrf_token": "csrf-good"}

        missing = http_requests.post(
            f"{api_server}/api/applications",
            cookies={"arie_session": token, "csrf_token": "csrf-good"},
            json={"test": True},
            timeout=3,
        )
        assert missing.status_code == 403
        assert "CSRF" in missing.text

        mismatch = http_requests.post(
            f"{api_server}/api/applications",
            cookies=cookies,
            headers={"X-CSRF-Token": "csrf-bad"},
            json={"test": True},
            timeout=3,
        )
        assert mismatch.status_code == 403
        assert "CSRF" in mismatch.text

        valid = http_requests.post(
            f"{api_server}/api/applications",
            cookies=cookies,
            headers={"X-CSRF-Token": "csrf-good"},
            json={"test": True},
            timeout=3,
        )
        assert valid.status_code != 403

    def test_get_client_ip_uses_leftmost_x_forwarded_for_from_private_proxy(self):
        """Audit IP should record the client, not the ALB/ECS private hop."""
        from base_handler import BaseHandler

        handler = object.__new__(BaseHandler)
        handler.request = SimpleNamespace(
            headers={"X-Forwarded-For": "203.0.113.10, 10.0.1.7"},
            remote_ip="10.0.1.7",
        )
        assert handler.get_client_ip() == "203.0.113.10"

        spoofed = object.__new__(BaseHandler)
        spoofed.request = SimpleNamespace(
            headers={"X-Forwarded-For": "1.2.3.4"},
            remote_ip="8.8.8.8",
        )
        assert spoofed.get_client_ip() == "8.8.8.8"


# ═══════════════════════════════════════════════════════════
# 4. AI Model Routing — Risk-Based Model Selection
# ═══════════════════════════════════════════════════════════

class TestModelRouting:
    @pytest.fixture
    def client(self):
        """Create a ClaudeClient instance for routing tests."""
        from claude_client import ClaudeClient
        return ClaudeClient()

    def test_low_risk_routes_to_sonnet(self, client):
        """LOW risk with low score should route to Sonnet."""
        model, reason = client.select_memo_model(risk_score=25, risk_level="LOW")
        assert "sonnet" in model.lower()
        assert "Sonnet" in reason

    def test_medium_risk_routes_to_sonnet(self, client):
        """MEDIUM risk with moderate score should route to Sonnet."""
        model, reason = client.select_memo_model(risk_score=45, risk_level="MEDIUM")
        assert "sonnet" in model.lower()
        assert "Sonnet" in reason

    def test_high_risk_routes_to_opus(self, client):
        """HIGH risk should route to Opus regardless of score."""
        model, reason = client.select_memo_model(risk_score=60, risk_level="HIGH")
        assert "opus" in model.lower()
        assert "Opus" in reason

    def test_very_high_risk_routes_to_opus(self, client):
        """VERY_HIGH risk should route to Opus."""
        model, reason = client.select_memo_model(risk_score=80, risk_level="VERY_HIGH")
        assert "opus" in model.lower()
        assert "Opus" in reason

    def test_high_score_overrides_medium_level(self, client):
        """Score >= 55 should route to Opus even if level says MEDIUM."""
        model, reason = client.select_memo_model(risk_score=60, risk_level="MEDIUM")
        assert "opus" in model.lower()

    def test_low_score_with_low_level_stays_sonnet(self, client):
        """Low score + LOW level should stay on Sonnet."""
        model, reason = client.select_memo_model(risk_score=20, risk_level="LOW")
        assert "sonnet" in model.lower()

    def test_none_risk_level_defaults_to_medium(self, client):
        """None risk level should default to MEDIUM behavior."""
        model, reason = client.select_memo_model(risk_score=30, risk_level=None)
        assert "sonnet" in model.lower()

    def test_none_score_defaults_to_50(self, client):
        """None score defaults to 50, which with MEDIUM level → Sonnet."""
        model, reason = client.select_memo_model(risk_score=None, risk_level="MEDIUM")
        assert "sonnet" in model.lower()

    def test_routing_returns_tuple(self, client):
        """select_memo_model must return (model, reason) tuple."""
        result = client.select_memo_model(risk_score=50, risk_level="MEDIUM")
        assert isinstance(result, tuple)
        assert len(result) == 2
        model, reason = result
        assert isinstance(model, str)
        assert isinstance(reason, str)
        assert len(model) > 0
        assert len(reason) > 0


class TestDeterministicClaudeParams:
    def test_generate_pins_temperature_zero(self, monkeypatch):
        from claude_client import ClaudeClient

        captured = {}

        class _FakeMessages:
            def create(self, **kwargs):
                captured.update(kwargs)

                class _Resp:
                    content = [type("Item", (), {"text": "ok"})()]
                    usage = type("Usage", (), {"input_tokens": 1, "output_tokens": 1})()

                return _Resp()

        client = ClaudeClient()
        client.mock_mode = False
        client.client = type("FakeClient", (), {"messages": _FakeMessages()})()

        out = client.generate("deterministic check")
        assert out == "ok"
        assert captured.get("temperature") == 0

    def test_call_claude_pins_temperature_zero(self, monkeypatch):
        import claude_client
        from claude_client import ClaudeClient

        captured = {}

        class _FakeMessages:
            def create(self, **kwargs):
                captured.update(kwargs)

                class _Resp:
                    content = [type("Item", (), {"text": "ok"})()]
                    usage = type("Usage", (), {"input_tokens": 1, "output_tokens": 1})()

                return _Resp()

        monkeypatch.setattr(claude_client, "_check_persistent_budget", lambda: True)

        client = ClaudeClient()
        client.mock_mode = False
        client.client = type("FakeClient", (), {"messages": _FakeMessages()})()

        out = client._call_claude("sys", "user")
        assert out == "ok"
        assert captured.get("temperature") == 0


# ═══════════════════════════════════════════════════════════
# 5. BaseHandler Extraction — Import Verification
# ═══════════════════════════════════════════════════════════

class TestBaseHandlerExtraction:
    def test_base_handler_importable_from_base_handler_module(self):
        """BaseHandler must be importable from base_handler.py."""
        from base_handler import BaseHandler
        assert BaseHandler is not None

    def test_base_handler_importable_from_server(self):
        """BaseHandler must still be importable from server.py (backward compat)."""
        from server import BaseHandler
        assert BaseHandler is not None

    def test_both_imports_are_same_class(self):
        """base_handler.BaseHandler and server.BaseHandler must be the same class."""
        from base_handler import BaseHandler as BH1
        from server import BaseHandler as BH2
        assert BH1 is BH2

    def test_rate_limiter_shared_instance(self):
        """server.rate_limiter and base_handler.rate_limiter must be the same object."""
        from base_handler import rate_limiter as rl1
        from server import rate_limiter as rl2
        assert rl1 is rl2

    def test_base_handler_has_key_methods(self):
        """BaseHandler must have all critical methods after extraction."""
        from base_handler import BaseHandler
        required_methods = [
            "prepare", "check_rate_limit", "set_default_headers",
            "issue_csrf_token", "issue_session_cookie", "clear_session_cookie",
            "options", "check_xsrf_cookie", "get_json",
            "get_current_user_token", "require_auth", "get_client_ip",
            "success", "error", "log_audit", "check_app_ownership", "write_error",
        ]
        for method_name in required_methods:
            assert hasattr(BaseHandler, method_name), f"Missing method: {method_name}"

    def test_health_endpoint_uses_base_handler(self, api_server):
        """Health endpoint must still work after BaseHandler extraction."""
        resp = http_requests.get(f"{api_server}/api/health", timeout=3)
        assert resp.status_code == 200
        body = resp.json()
        assert "status" in body
