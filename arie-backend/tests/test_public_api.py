"""
Public API v1 — Tests
=====================
Tests for the versioned external API endpoints under /api/v1/.
"""
import os
import sys
import json
import tempfile
import socket
import threading
import time
import uuid
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
    """Start a real Tornado HTTP server on a background IOLoop."""
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
    srv = server_ref.get("server")
    if io_loop and srv:
        io_loop.add_callback(srv.stop)
        io_loop.add_callback(io_loop.stop)
    thread.join(timeout=2)


def _admin_token():
    from auth import create_token
    return create_token("admin001", "admin", "Test Admin", "officer")


def _client_token():
    from auth import create_token
    return create_token("testclient_v1", "client", "V1 Client", "client")


# ═══════════════════════════════════════════════════════════
# 1. Public Health Endpoint
# ═══════════════════════════════════════════════════════════

class TestPublicHealth:
    def test_health_returns_ok(self, api_server):
        """GET /api/v1/health returns status: ok."""
        resp = http_requests.get(f"{api_server}/api/v1/health", timeout=3)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"

    def test_health_no_auth_required(self, api_server):
        """Health endpoint should not require auth."""
        resp = http_requests.get(f"{api_server}/api/v1/health", timeout=3)
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════
# 2. Application Status Endpoint
# ═══════════════════════════════════════════════════════════

class TestPublicApplicationStatus:
    def test_status_requires_auth(self, api_server):
        """GET /api/v1/applications/:ref/status returns 401 without auth."""
        resp = http_requests.get(
            f"{api_server}/api/v1/applications/ARF-FAKE/status",
            timeout=3,
        )
        assert resp.status_code == 401

    def test_status_returns_clean_payload(self, api_server):
        """GET /api/v1/applications/:ref/status returns only client-safe fields."""
        from db import get_db
        uid = uuid.uuid4().hex[:8]
        ref = f"ARF-2026-V1S-{uid}"

        conn = get_db()
        conn.execute("""
            INSERT INTO applications (id, ref, company_name, status, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (f"v1s_{uid}", ref, "Status Test Corp", "in_review", "2026-04-01T10:00:00"))
        conn.commit()
        conn.close()

        token = _admin_token()
        resp = http_requests.get(
            f"{api_server}/api/v1/applications/{ref}/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        body = resp.json()

        # Must contain exactly these fields
        assert body["application_ref"] == ref
        assert body["status"] == "in_review"
        assert body["last_updated"] == "2026-04-01T10:00:00"

        # Must NOT expose internal fields
        assert "company_name" not in body
        assert "risk_score" not in body
        assert "decision_notes" not in body

    def test_status_not_found(self, api_server):
        """GET /api/v1/applications/:ref/status returns 404 for unknown ref."""
        token = _admin_token()
        resp = http_requests.get(
            f"{api_server}/api/v1/applications/ARF-NONEXISTENT/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════
# 3. Application Decision Endpoint
# ═══════════════════════════════════════════════════════════

class TestPublicApplicationDecision:
    def test_decision_requires_auth(self, api_server):
        """GET /api/v1/applications/:ref/decision returns 401 without auth."""
        resp = http_requests.get(
            f"{api_server}/api/v1/applications/ARF-FAKE/decision",
            timeout=3,
        )
        assert resp.status_code == 401

    def test_decision_returns_latest_record(self, api_server):
        """GET /api/v1/applications/:ref/decision returns the latest decision record."""
        from db import get_db
        uid = uuid.uuid4().hex[:8]
        ref = f"ARF-2026-V1D-{uid}"

        conn = get_db()
        conn.execute("""
            INSERT INTO applications (id, ref, company_name, status)
            VALUES (?, ?, ?, ?)
        """, (f"v1d_{uid}", ref, "Decision Test Corp", "approved"))

        # Insert two decision records — handler should return the latest
        conn.execute("""
            INSERT INTO decision_records (id, application_ref, decision_type, risk_level,
                confidence_score, source, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (f"dr_old_{uid}", ref, "reject", "HIGH", 0.60, "rule_engine", "2026-03-01T09:00:00"))

        conn.execute("""
            INSERT INTO decision_records (id, application_ref, decision_type, risk_level,
                confidence_score, source, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (f"dr_new_{uid}", ref, "approve", "MEDIUM", 0.85, "supervisor", "2026-04-01T12:00:00"))

        conn.commit()
        conn.close()

        token = _admin_token()
        resp = http_requests.get(
            f"{api_server}/api/v1/applications/{ref}/decision",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        body = resp.json()

        # Must contain exactly decision fields
        assert body["decision_type"] == "approve"
        assert body["risk_level"] == "MEDIUM"
        assert body["confidence_score"] == 0.85
        assert "timestamp" in body

        # Must NOT expose internal fields
        assert "source" not in body
        assert "actor_user_id" not in body
        assert "key_flags" not in body
        assert "extra_json" not in body
        assert "override_reason" not in body

    def test_decision_not_found_no_app(self, api_server):
        """GET /api/v1/applications/:ref/decision returns 404 for unknown app."""
        token = _admin_token()
        resp = http_requests.get(
            f"{api_server}/api/v1/applications/ARF-NOAPP/decision",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 404

    def test_decision_not_found_no_records(self, api_server):
        """GET /api/v1/applications/:ref/decision returns 404 when no decision records exist."""
        from db import get_db
        uid = uuid.uuid4().hex[:8]
        ref = f"ARF-2026-V1E-{uid}"

        conn = get_db()
        conn.execute("""
            INSERT INTO applications (id, ref, company_name, status)
            VALUES (?, ?, ?, ?)
        """, (f"v1e_{uid}", ref, "Empty Decision Corp", "draft"))
        conn.commit()
        conn.close()

        token = _admin_token()
        resp = http_requests.get(
            f"{api_server}/api/v1/applications/{ref}/decision",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 404
        body = resp.json()
        assert "error" in body
