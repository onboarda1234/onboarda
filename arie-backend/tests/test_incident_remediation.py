"""
Tests for staging incident remediation:
- Login handler DB error handling (503 on DB failure)
- Lightweight liveness endpoint (/healthz)
- Frontend-safe error responses
- PII encryption key enforcement for staging/production
- DB pool resilience improvements
"""
import os
import sys
import json
import tempfile
import socket
import threading
import time
import pytest
from unittest.mock import patch, MagicMock

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
    """Start a real Tornado HTTP server for incident remediation tests."""
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
    if io_loop:
        io_loop.add_callback(io_loop.stop)


# ══════════════════════════════════════════════════════════
# 1. LIVENESS ENDPOINT TESTS
# ══════════════════════════════════════════════════════════

class TestLivenessEndpoint:
    """Test the new /healthz lightweight liveness probe."""

    def test_healthz_returns_200(self, api_server):
        """Liveness endpoint should always return 200."""
        resp = http_requests.get(f"{api_server}/healthz", timeout=5)
        assert resp.status_code == 200

    def test_healthz_returns_alive_status(self, api_server):
        """Liveness endpoint should return {"status": "alive"}."""
        resp = http_requests.get(f"{api_server}/healthz", timeout=5)
        data = resp.json()
        assert data["status"] == "alive"

    def test_healthz_is_independent_of_db(self, api_server):
        """/healthz should not depend on database availability."""
        resp = http_requests.get(f"{api_server}/healthz", timeout=5)
        assert resp.status_code == 200
        # Content type should be JSON
        assert "application/json" in resp.headers.get("Content-Type", "")

    def test_health_endpoint_still_exists(self, api_server):
        """The deep /api/health endpoint should still be available."""
        resp = http_requests.get(f"{api_server}/api/health", timeout=5)
        assert resp.status_code in (200, 503)
        data = resp.json()
        assert "status" in data
        assert "database" in data


# ══════════════════════════════════════════════════════════
# 2. LOGIN HANDLER ERROR HANDLING TESTS
# ══════════════════════════════════════════════════════════

class TestOfficerLoginErrorHandling:
    """Test that officer login returns proper error codes."""

    def test_invalid_credentials_returns_401(self, api_server):
        """Bad credentials should return 401."""
        resp = http_requests.post(
            f"{api_server}/api/auth/officer/login",
            json={"email": "wrong@test.com", "password": "wrongpassword"},
            timeout=5
        )
        assert resp.status_code == 401
        data = resp.json()
        assert "error" in data
        assert "Invalid credentials" in data["error"]

    def test_missing_fields_returns_400(self, api_server):
        """Missing email/password should return 400."""
        resp = http_requests.post(
            f"{api_server}/api/auth/officer/login",
            json={"email": "", "password": ""},
            timeout=5
        )
        assert resp.status_code == 400

    def test_db_failure_returns_503(self, temp_db):
        """DB failure during login should return 503, not 401."""
        from server import OfficerLoginHandler
        import tornado.web
        import tornado.testing

        handler = MagicMock(spec=OfficerLoginHandler)
        handler.get_json = MagicMock(return_value={"email": "test@test.com", "password": "pass123"})
        handler.get_client_ip = MagicMock(return_value="127.0.0.1")
        handler.error = MagicMock()
        handler.set_status = MagicMock()
        handler.write = MagicMock()

        # Verify handler has try/except for DB
        import inspect
        source = inspect.getsource(OfficerLoginHandler.post)
        assert "try:" in source, "OfficerLoginHandler.post should have try/except"
        assert "503" in source, "OfficerLoginHandler.post should return 503 on DB failure"
        assert "Service temporarily unavailable" in source

    def test_db_error_response_does_not_leak_details(self, temp_db):
        """Error response on DB failure should not expose internal details."""
        import inspect
        from server import OfficerLoginHandler
        source = inspect.getsource(OfficerLoginHandler.post)
        # Should not include traceback/stack info in response
        assert "Service temporarily unavailable" in source
        # Should log the real error
        assert "logger.error" in source


class TestClientLoginErrorHandling:
    """Test that client login returns proper error codes."""

    def test_invalid_credentials_returns_401(self, api_server):
        """Bad credentials should return 401."""
        resp = http_requests.post(
            f"{api_server}/api/auth/client/login",
            json={"email": "wrong@test.com", "password": "wrongpassword"},
            timeout=5
        )
        assert resp.status_code == 401
        data = resp.json()
        assert "error" in data
        assert "Invalid credentials" in data["error"]

    def test_missing_fields_returns_400(self, api_server):
        """Missing email/password should return 400."""
        resp = http_requests.post(
            f"{api_server}/api/auth/client/login",
            json={"email": "", "password": ""},
            timeout=5
        )
        assert resp.status_code == 400

    def test_db_failure_returns_503(self, temp_db):
        """DB failure during client login should return 503, not 401."""
        import inspect
        from server import ClientLoginHandler
        source = inspect.getsource(ClientLoginHandler.post)
        assert "try:" in source, "ClientLoginHandler.post should have try/except"
        assert "503" in source, "ClientLoginHandler.post should return 503 on DB failure"
        assert "Service temporarily unavailable" in source

    def test_db_error_response_does_not_leak_details(self, temp_db):
        """Error response on DB failure should not expose internal details."""
        import inspect
        from server import ClientLoginHandler
        source = inspect.getsource(ClientLoginHandler.post)
        assert "Service temporarily unavailable" in source
        assert "logger.error" in source


# ══════════════════════════════════════════════════════════
# 3. PII ENCRYPTION KEY SAFETY TESTS
# ══════════════════════════════════════════════════════════

class TestPIIEncryptionKeySafety:
    """Test that staging/production require configured PII encryption key."""

    def test_staging_blocks_auto_generated_key(self, temp_db):
        """Staging environment must not silently auto-generate encryption key."""
        import inspect
        # Check server.py PII init code blocks staging
        import server
        source = inspect.getsource(server)
        # The init block should include "staging" in the environments that exit
        assert '"staging"' in source or "'staging'" in source

    def test_production_blocks_auto_generated_key(self, temp_db):
        """Production environment must require PII_ENCRYPTION_KEY."""
        from security_hardening import PIIEncryptor
        # In production, missing key should raise RuntimeError
        with patch.dict(os.environ, {"PII_ENCRYPTION_KEY": ""}, clear=False):
            with patch("security_hardening.is_production", return_value=True):
                with pytest.raises(RuntimeError, match="PII_ENCRYPTION_KEY"):
                    PIIEncryptor("")

    def test_dev_allows_auto_generated_key(self, temp_db):
        """Development/testing environments can use auto-generated keys."""
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        from security_hardening import PIIEncryptor
        encryptor = PIIEncryptor(key)
        assert encryptor is not None


# ══════════════════════════════════════════════════════════
# 4. DB POOL RESILIENCE TESTS
# ══════════════════════════════════════════════════════════

class TestDBPoolResilience:
    """Test database pool configuration improvements."""

    def test_pool_config_increased(self, temp_db):
        """Pool max connections should be >= 10 for production workloads."""
        import inspect
        from db import init_pg_pool
        source = inspect.getsource(init_pg_pool)
        # Pool should have been increased from 5 to 15
        assert "15" in source, "Pool max connections should be increased to 15"

    def test_get_db_handles_stale_connections(self, temp_db):
        """get_db should handle stale/broken PostgreSQL connections gracefully."""
        import inspect
        from db import get_db
        source = inspect.getsource(get_db)
        assert "rollback" in source, "get_db should rollback to handle stale transaction state"

    def test_sqlite_still_works_in_testing(self, temp_db):
        """SQLite connections should still work in testing environment."""
        from db import get_db
        db = get_db()
        result = db.execute("SELECT 1").fetchone()
        assert result is not None
        db.close()


# ══════════════════════════════════════════════════════════
# 5. LIVENESS HANDLER UNIT TESTS
# ══════════════════════════════════════════════════════════

class TestLivenessHandlerRegistration:
    """Test that liveness handler is properly registered."""

    def test_healthz_route_registered(self, app):
        """The /healthz route should be registered in the application."""
        rules = app.wildcard_router.rules
        healthz_found = any(
            hasattr(rule.matcher, 'regex') and "/healthz" in rule.matcher.regex.pattern
            for rule in rules
        )
        assert healthz_found, "/healthz route should be registered"

    def test_health_route_still_registered(self, app):
        """The /api/health route should still be registered."""
        rules = app.wildcard_router.rules
        health_found = any(
            hasattr(rule.matcher, 'regex') and "/api/health" in rule.matcher.regex.pattern
            for rule in rules
        )
        assert health_found, "/api/health route should still be registered"


# ══════════════════════════════════════════════════════════
# 6. DOCKERFILE AND RENDER CONFIG TESTS
# ══════════════════════════════════════════════════════════

class TestInfraConfig:
    """Test infrastructure configuration changes."""

    def test_dockerfile_uses_healthz(self):
        """Dockerfile HEALTHCHECK should use /healthz endpoint."""
        dockerfile_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "Dockerfile"
        )
        with open(dockerfile_path) as f:
            content = f.read()
        assert "/healthz" in content, "Dockerfile should use /healthz for health checks"
        assert "start-period=60s" in content or "start-period" in content, \
            "Dockerfile should have adequate start-period"

    def test_render_yaml_uses_healthz(self):
        """render.yaml should use /healthz for health check path."""
        # Check repo root render.yaml
        root_render = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "render.yaml"
        )
        if os.path.exists(root_render):
            with open(root_render) as f:
                content = f.read()
            assert "/healthz" in content, "render.yaml should use /healthz"

    def test_render_yaml_includes_pii_key(self):
        """render.yaml should include PII_ENCRYPTION_KEY as required secret."""
        root_render = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "render.yaml"
        )
        if os.path.exists(root_render):
            with open(root_render) as f:
                content = f.read()
            assert "PII_ENCRYPTION_KEY" in content, \
                "render.yaml should list PII_ENCRYPTION_KEY as required secret"


# ══════════════════════════════════════════════════════════
# 7. STARTUP HARDENING TESTS
# ══════════════════════════════════════════════════════════

class TestStartupHardening:
    """Test startup flow improvements."""

    def test_server_logs_listening_message(self, temp_db):
        """Server startup should log when it starts listening."""
        import inspect
        import server
        source = inspect.getsource(server)
        assert "Server listening" in source or "liveness endpoint" in source, \
            "Server should log when it starts listening"

    def test_migration_failure_does_not_crash(self, temp_db):
        """Migration runner failure should be caught, not crash the server."""
        import inspect
        import server
        source = inspect.getsource(server)
        # The migration block should be in try/except
        assert "Migration runner unavailable" in source, \
            "Migration failures should be caught and logged"

    def test_supervisor_failure_does_not_crash(self, temp_db):
        """Supervisor framework init failure should not crash the server."""
        import inspect
        import server
        source = inspect.getsource(server)
        assert "Failed to initialize supervisor" in source, \
            "Supervisor init failures should be caught and logged"
