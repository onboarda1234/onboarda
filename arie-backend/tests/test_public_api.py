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


# ═══════════════════════════════════════════════════════════
# 4. Client Ownership Enforcement
# ═══════════════════════════════════════════════════════════

class TestPublicAPIClientOwnership:
    """Verify that client-scoped tokens can only access their own applications."""

    def _setup_two_clients(self):
        """Create two clients and an application owned by client A."""
        from db import get_db
        from auth import create_token
        import bcrypt

        uid = uuid.uuid4().hex[:8]
        client_a_id = f"client_a_{uid}"
        client_b_id = f"client_b_{uid}"
        ref = f"ARF-2026-OWN-{uid}"

        pw = bcrypt.hashpw("Pass123!".encode(), bcrypt.gensalt()).decode()
        conn = get_db()
        conn.execute(
            "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
            (client_a_id, f"a_{uid}@test.com", pw, "Client A Corp"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
            (client_b_id, f"b_{uid}@test.com", pw, "Client B Corp"),
        )
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (f"own_{uid}", ref, client_a_id, "Owned Corp", "in_review", "2026-04-01T10:00:00"))
        conn.execute("""
            INSERT INTO decision_records (id, application_ref, decision_type, risk_level,
                confidence_score, source, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (f"dr_own_{uid}", ref, "approve", "LOW", 0.90, "supervisor", "2026-04-01T12:00:00"))
        conn.commit()
        conn.close()

        token_a = create_token(client_a_id, "client", "Client A", "client")
        token_b = create_token(client_b_id, "client", "Client B", "client")
        return ref, token_a, token_b

    def test_client_can_access_own_status(self, api_server):
        """Client A can fetch status of its own application."""
        ref, token_a, _ = self._setup_two_clients()
        resp = http_requests.get(
            f"{api_server}/api/v1/applications/{ref}/status",
            headers={"Authorization": f"Bearer {token_a}"},
            timeout=3,
        )
        assert resp.status_code == 200
        assert resp.json()["application_ref"] == ref

    def test_client_cannot_access_other_status(self, api_server):
        """Client B gets 403 when fetching Client A's application status."""
        ref, _, token_b = self._setup_two_clients()
        resp = http_requests.get(
            f"{api_server}/api/v1/applications/{ref}/status",
            headers={"Authorization": f"Bearer {token_b}"},
            timeout=3,
        )
        assert resp.status_code == 403

    def test_client_can_access_own_decision(self, api_server):
        """Client A can fetch decision of its own application."""
        ref, token_a, _ = self._setup_two_clients()
        resp = http_requests.get(
            f"{api_server}/api/v1/applications/{ref}/decision",
            headers={"Authorization": f"Bearer {token_a}"},
            timeout=3,
        )
        assert resp.status_code == 200
        assert resp.json()["decision_type"] == "approve"

    def test_client_cannot_access_other_decision(self, api_server):
        """Client B gets 403 when fetching Client A's application decision."""
        ref, _, token_b = self._setup_two_clients()
        resp = http_requests.get(
            f"{api_server}/api/v1/applications/{ref}/decision",
            headers={"Authorization": f"Bearer {token_b}"},
            timeout=3,
        )
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════
# 5. Dashboard Status Endpoint
# ═══════════════════════════════════════════════════════════

class TestPublicDashboardStatus:
    def test_dashboard_requires_auth(self, api_server):
        """GET /api/v1/dashboard/status returns 401 without auth."""
        resp = http_requests.get(
            f"{api_server}/api/v1/dashboard/status",
            timeout=3,
        )
        assert resp.status_code == 401

    def test_dashboard_returns_expected_shape(self, api_server):
        """GET /api/v1/dashboard/status returns all expected top-level keys."""
        token = _admin_token()
        resp = http_requests.get(
            f"{api_server}/api/v1/dashboard/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "total_applications" in body
        assert "applications_by_status" in body
        assert "applications_by_risk_level" in body
        assert "recent_activity" in body
        assert "last_updated" in body
        assert isinstance(body["total_applications"], int)
        assert isinstance(body["applications_by_status"], dict)
        assert isinstance(body["applications_by_risk_level"], dict)
        assert isinstance(body["recent_activity"], list)

    def test_dashboard_counts_match(self, api_server):
        """Dashboard total matches sum of by-status counts."""
        from db import get_db
        uid = uuid.uuid4().hex[:8]

        conn = get_db()
        conn.execute("""
            INSERT INTO applications (id, ref, company_name, status, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (f"dsh_a_{uid}", f"ARF-DSH-A-{uid}", "Dash A", "submitted", "2026-04-01T08:00:00"))
        conn.execute("""
            INSERT INTO applications (id, ref, company_name, status, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (f"dsh_b_{uid}", f"ARF-DSH-B-{uid}", "Dash B", "approved", "2026-04-01T09:00:00"))
        conn.execute("""
            INSERT INTO decision_records (id, application_ref, decision_type, risk_level,
                confidence_score, source, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (f"dr_dsh_{uid}", f"ARF-DSH-B-{uid}", "approve", "LOW", 0.95, "supervisor", "2026-04-01T09:00:00"))
        conn.commit()
        conn.close()

        token = _admin_token()
        resp = http_requests.get(
            f"{api_server}/api/v1/dashboard/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        body = resp.json()

        # Total must equal sum of all status counts
        assert body["total_applications"] == sum(body["applications_by_status"].values())
        # Verify risk level aggregation includes the inserted decision record
        assert body["applications_by_risk_level"].get("LOW", 0) >= 1

    def test_dashboard_recent_activity_limit(self, api_server):
        """Recent activity returns at most 5 entries."""
        token = _admin_token()
        resp = http_requests.get(
            f"{api_server}/api/v1/dashboard/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["recent_activity"]) <= 5

    def test_dashboard_recent_activity_fields(self, api_server):
        """Each recent_activity item has the expected fields."""
        from db import get_db
        uid = uuid.uuid4().hex[:8]

        conn = get_db()
        conn.execute("""
            INSERT INTO applications (id, ref, company_name, status, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (f"dsh_f_{uid}", f"ARF-DSH-F-{uid}", "Dash Fields", "in_review", "2026-04-02T10:00:00"))
        conn.commit()
        conn.close()

        token = _admin_token()
        resp = http_requests.get(
            f"{api_server}/api/v1/dashboard/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        body = resp.json()
        for item in body["recent_activity"]:
            assert "application_ref" in item
            assert "status" in item
            assert "timestamp" in item

    def test_dashboard_client_scoped(self, api_server):
        """Client token sees only their own applications in dashboard."""
        from db import get_db
        from auth import create_token
        import bcrypt

        uid = uuid.uuid4().hex[:8]
        client_id = f"dsh_client_{uid}"
        pw = bcrypt.hashpw("Pass123!".encode(), bcrypt.gensalt()).decode()

        conn = get_db()
        conn.execute(
            "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
            (client_id, f"dsh_{uid}@test.com", pw, "Dash Client Corp"),
        )
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (f"dsh_c_{uid}", f"ARF-DSH-C-{uid}", client_id, "Client App", "submitted", "2026-04-01T11:00:00"))
        conn.commit()
        conn.close()

        token = create_token(client_id, "client", "Dash Client", "client")
        resp = http_requests.get(
            f"{api_server}/api/v1/dashboard/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        body = resp.json()

        # Client sees exactly 1 application they just created
        assert body["total_applications"] == 1
        assert body["applications_by_status"].get("submitted") == 1
        assert len(body["recent_activity"]) == 1
        assert body["recent_activity"][0]["application_ref"] == f"ARF-DSH-C-{uid}"

    def test_dashboard_risk_level_uses_latest_decision_per_application(self, api_server):
        """applications_by_risk_level counts each application once using its latest decision."""
        from db import get_db
        uid = uuid.uuid4().hex[:8]

        conn = get_db()
        conn.execute("""
            INSERT INTO applications (id, ref, company_name, status, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (f"dsh_multi_{uid}", f"ARF-DSH-MULTI-{uid}", "Dash Multi", "approved", "2026-04-03T10:00:00"))
        conn.execute("""
            INSERT INTO decision_records (id, application_ref, decision_type, risk_level,
                confidence_score, source, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (f"dr_dsh_old_{uid}", f"ARF-DSH-MULTI-{uid}", "escalate_edd", "LOW", 0.70, "rule_engine", "2026-04-03T09:00:00"))
        conn.execute("""
            INSERT INTO decision_records (id, application_ref, decision_type, risk_level,
                confidence_score, source, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (f"dr_dsh_new_{uid}", f"ARF-DSH-MULTI-{uid}", "approve", "HIGH", 0.95, "supervisor", "2026-04-03T10:00:00"))
        conn.commit()
        conn.close()

        token = _admin_token()
        resp = http_requests.get(
            f"{api_server}/api/v1/dashboard/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        body = resp.json()

        # The application should be counted under the latest risk level only
        assert body["applications_by_risk_level"].get("HIGH", 0) >= 1
