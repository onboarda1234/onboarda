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
import uuid
from datetime import datetime, timedelta, timezone
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
    from tests.conftest import shutdown_test_http_server
    shutdown_test_http_server(thread, server_ref)


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

    def test_readiness_reports_degraded_when_db_pool_unavailable(self, monkeypatch):
        """Readiness must fail closed when the DB/pool cannot provide a connection."""
        import server

        def exhausted_pool():
            raise RuntimeError("connection pool exhausted")

        monkeypatch.setattr(server, "get_db", exhausted_pool)

        ready, payload = server._readiness_status_payload()

        assert ready is False
        assert payload["checks"]["database"]["status"] == "failed"
        assert "connection pool exhausted" in payload["checks"]["database"]["detail"]

    def test_health_liveness_process_ok_but_readiness_db_degraded(self, api_server, monkeypatch):
        """Health must not report ok when DB-backed authenticated workflows are down."""
        import server

        def exhausted_pool():
            raise RuntimeError("connection pool exhausted")

        monkeypatch.setattr(server, "get_db", exhausted_pool)

        health = http_requests.get(f"{api_server}/api/health", timeout=3)
        assert health.status_code == 503
        health_body = health.json()
        assert health_body["status"] == "degraded"
        assert health_body["readiness"] == "degraded"
        assert "database" not in health_body

        liveness = http_requests.get(f"{api_server}/api/liveness", timeout=3)
        assert liveness.status_code == 200
        assert liveness.json()["status"] == "ok"

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


def _walk_json_keys(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield str(key)
            yield from _walk_json_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_json_keys(item)


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

    def test_version_requires_authentication(self, api_server):
        resp = http_requests.get(f"{api_server}/api/version", timeout=3)
        assert resp.status_code == 401

    def test_authenticated_version_returns_required_release_keys(self, api_server, monkeypatch):
        from auth import create_token

        monkeypatch.setenv("GIT_SHA", "abcdef1234567890")
        monkeypatch.setenv("IMAGE_TAG", "abcdef1234567890")
        monkeypatch.setenv("BUILD_TIME", "2026-06-16T12:00:00Z")
        monkeypatch.setenv("ENVIRONMENT", "staging")
        monkeypatch.setenv("SERVICE_NAME", "regmind-backend")

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/version",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )

        assert resp.status_code == 200
        body = resp.json()
        for key in ("git_sha", "git_sha_short", "image_tag", "build_time", "environment", "service", "provider_status"):
            assert key in body
        assert body["git_sha"] == "abcdef1234567890"
        assert body["git_sha_short"] == "abcdef1"
        assert body["image_tag"] == "abcdef1234567890"
        assert body["build_time"] == "2026-06-16T12:00:00Z"
        assert body["environment"] == "staging"
        assert body["service"] == "regmind-backend"
        assert set(body["provider_status"]) == {"aml_screening", "identity_verification", "registry_kyb"}

    def test_version_does_not_return_secret_like_fields_or_values(self, api_server, monkeypatch):
        from auth import create_token

        secret_values = {
            "COMPLYADVANTAGE_PASSWORD": "super-secret-password-value",
            "COMPLYADVANTAGE_USERNAME": "secret-user@example.test",
            "SUMSUB_SECRET_KEY": "sumsub-secret-value",
            "ANTHROPIC_API_KEY": "anthropic-secret-value",
            "JWT_SECRET": "jwt-secret-value",
        }
        for key, value in secret_values.items():
            monkeypatch.setenv(key, value)

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/version",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )

        assert resp.status_code == 200
        body = resp.json()
        serialized = json.dumps(body).lower()
        for value in secret_values.values():
            assert value.lower() not in serialized
        forbidden_key_fragments = ("secret", "password", "credential", "authorization", "bearer", "access_token", "refresh_token", "jwt")
        leaked_keys = [key for key in _walk_json_keys(body) if any(fragment in key.lower() for fragment in forbidden_key_fragments)]
        assert leaked_keys == []

    def test_version_missing_env_vars_fail_safe_without_500(self, api_server, monkeypatch):
        from auth import create_token

        for key in ("GIT_SHA", "IMAGE_TAG", "BUILD_TIME", "SERVICE_NAME"):
            monkeypatch.delenv(key, raising=False)

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/version",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["git_sha"] == "unknown"
        assert body["git_sha_short"] == "unknown"
        assert body["image_tag"] == "unknown"
        assert body["build_time"] == "unknown"
        assert body["service"] == "regmind-backend"

    def test_screening_status_does_not_expose_unused_provider(self, api_server):
        """Provider status must not advertise deprecated or unused screening providers."""
        from auth import create_token

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/screening/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        payload = resp.json()
        body = payload.get("data", payload)
        serialized = json.dumps(body).lower()
        assert ("open" + "sanctions") not in serialized
        assert ("open_" + "sanctions") not in serialized
        assert ("sumsub " + "aml") not in serialized
        assert "entitlement-proven sumsub" not in serialized
        assert "sumsub_aml_entitlement_proven" not in serialized
        assert "aml_screening_enabled" not in body["sumsub"]
        assert "complyadvantage" in body
        assert body["provider_truth"]["active_aml_screening_provider"] in {"ComplyAdvantage Mesh", "Not active"}
        assert body["provider_truth"]["identity_verification_provider"] == "Sumsub IDV/KYC"
        assert body["provider_truth"]["identity_verification_provider_key"] == "sumsub"
        assert body["provider_truth"]["registry_kyb_provider"] == "OpenCorporates registry/enrichment"
        assert body["provider_truth"]["screening_abstraction_required_for_ca"] is True
        assert body["provider_truth"]["simulation_fallback_enabled"] is False
        assert "active_aml_screening_mode" in body["provider_truth"]
        assert "active_aml_workspace_label" in body["provider_truth"]
        assert "active_aml_screening_config_id" in body["provider_truth"]
        assert "active_aml_screening_config_label" in body["provider_truth"]
        assert "last_provider_health_result" in body["provider_truth"]
        assert "last_token_auth_probe_result" in body["provider_truth"]
        assert "last_error_category" in body["provider_truth"]
        assert "username" not in serialized
        assert "password" not in serialized
        assert "access_token" not in serialized
        assert "authorization" not in serialized
        assert body["sumsub"]["description"] == "Individual identity verification and KYC (document + selfie + liveness)"

    def test_admin_health_does_not_expose_unused_provider(self, api_server):
        """Authenticated health inventory must not list unused screening providers."""
        from auth import create_token

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/health",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        payload = resp.json()
        serialized = json.dumps(payload).lower()
        assert ("open" + "sanctions") not in serialized
        assert ("open_" + "sanctions") not in serialized
        integrations = payload.get("integrations", {})
        assert "sumsub_identity_verification" in integrations
        assert "complyadvantage" in integrations

    def test_runtime_config_resource_status_payloads_do_not_expose_removed_provider(self, api_server):
        """Protected runtime config/resource/status payloads must not leak unused provider labels."""
        from auth import create_token

        token = create_token("admin001", "admin", "Test Admin", "officer")
        headers = {"Authorization": f"Bearer {token}"}
        paths = [
            "/api/screening/status",
            "/api/health",
            "/api/resources",
            "/api/config/system-settings",
            "/api/config/ai-agents",
        ]
        for path in paths:
            resp = http_requests.get(f"{api_server}{path}", headers=headers, timeout=5)
            assert resp.status_code == 200, f"{path}: {resp.status_code} {resp.text[:200]}"
            serialized = json.dumps(resp.json()).lower()
            assert ("open" + "sanctions") not in serialized
            assert ("open " + "sanctions") not in serialized
            assert ("open-" + "sanctions") not in serialized
            assert ("open_" + "sanctions") not in serialized
            assert "entitlement-proven sumsub" not in serialized
            assert ("sumsub " + "aml") not in serialized

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

    def test_applications_list_view_returns_bounded_lightweight_rows(self, api_server):
        """Applications list view should return paginated summary rows without detail-grade enrichment."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        conn.execute("DELETE FROM applications WHERE id IN (?, ?)", ("app_list_a", "app_list_b"))
        conn.execute(
            """
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                status, risk_level, final_risk_level, risk_score, onboarding_lane,
                created_at, is_fixture
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("app_list_a", "ARF-LIST-001", "client_list", "Pilot Search One Ltd", "Mauritius", "Technology", "company", "submitted", "LOW", "LOW", 22, "Standard Review", "2026-05-03T10:00:00Z", 0),
        )
        conn.execute(
            """
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                status, risk_level, final_risk_level, risk_score, onboarding_lane,
                created_at, is_fixture
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("app_list_b", "ARF-LIST-002", "client_list", "Pilot Search Two Ltd", "Mauritius", "Fintech", "company", "approved", "MEDIUM", "MEDIUM", 44, "Enhanced Due Diligence", "2026-05-03T10:01:00Z", 0),
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/applications?view=list&limit=1&offset=0&q=pilot+search",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["view"] == "list"
        assert body["limit"] == 1
        assert body["offset"] == 0
        assert body["returned"] == 1
        assert body["total"] >= 2
        assert body["pagination"]["has_next"] is True
        row = body["applications"][0]
        assert row["company_name"] in {"Pilot Search One Ltd", "Pilot Search Two Ltd"}
        assert "documents" not in row
        assert "directors" not in row
        assert "ubos" not in row

    def _seed_case_management_rows(self, officer_id="cm_co"):
        from db import get_db

        today = datetime.now(timezone.utc).date()
        overdue = (today - timedelta(days=1)).isoformat()
        due_soon = (today + timedelta(days=7)).isoformat()
        current = "2020-01-01"
        client_id = f"{officer_id}_client"

        conn = get_db()
        ids = [
            f"{officer_id}_pricing",
            f"{officer_id}_preapproval",
            f"{officer_id}_review_overdue_app",
            f"{officer_id}_review_due_app",
            f"{officer_id}_other_app",
            f"{officer_id}_alert_only_app",
            f"{officer_id}_edd_app",
        ]
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM monitoring_alerts WHERE application_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM edd_cases WHERE application_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM periodic_reviews WHERE application_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM applications WHERE id IN ({placeholders})", ids)
        conn.execute("INSERT OR IGNORE INTO clients (id, email, password_hash, company_name, status) VALUES (?, ?, ?, ?, 'active')",
                     (client_id, f"{client_id}@example.test", "x", "CM Client"))
        rows = [
            (ids[0], f"ARF-CM-{officer_id}-PRICING", "CM Pricing Ltd", "pricing_review", "LOW", officer_id),
            (ids[1], f"ARF-CM-{officer_id}-PRE", "CM Pre Approval Ltd", "pre_approval_review", "HIGH", officer_id),
            (ids[2], f"ARF-CM-{officer_id}-RO", "CM Review Overdue Ltd", "kyc_documents", "HIGH", officer_id),
            (ids[3], f"ARF-CM-{officer_id}-RD", "CM Review Due Ltd", "kyc_documents", "MEDIUM", officer_id),
            (ids[4], f"ARF-CM-{officer_id}-OTHER", "CM Other Officer Ltd", "pricing_review", "LOW", "cm_other"),
            (ids[5], f"ARF-CM-{officer_id}-ALERT", "CM Alert Only Ltd", "kyc_documents", "HIGH", officer_id),
            (ids[6], f"ARF-CM-{officer_id}-EDD", "CM Lifecycle Only Ltd", "edd_required", "VERY_HIGH", officer_id),
        ]
        for app_id, ref, company, status, risk, assigned_to in rows:
            conn.execute(
                """
                INSERT INTO applications (
                    id, ref, client_id, company_name, country, status, risk_level,
                    final_risk_level, assigned_to, created_at, updated_at, is_fixture
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (app_id, ref, client_id, company, "Mauritius", status, risk, risk, assigned_to, current, current),
            )
        conn.execute(
            """
            INSERT INTO periodic_reviews (
                application_id, client_name, risk_level, status, due_date,
                assigned_officer, trigger_source, review_reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ids[2], "CM Review Overdue Ltd", "HIGH", "pending", overdue, officer_id, "schedule", "Scheduled review", overdue),
        )
        conn.execute(
            """
            INSERT INTO periodic_reviews (
                application_id, client_name, risk_level, status, due_date,
                assigned_officer, trigger_source, review_reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ids[3], "CM Review Due Ltd", "MEDIUM", "pending", due_soon, officer_id, "schedule", "Scheduled review", due_soon),
        )
        overdue_id = conn.execute(
            "SELECT id FROM periodic_reviews WHERE application_id = ? ORDER BY id DESC LIMIT 1",
            (ids[2],),
        ).fetchone()["id"]
        due_id = conn.execute(
            "SELECT id FROM periodic_reviews WHERE application_id = ? ORDER BY id DESC LIMIT 1",
            (ids[3],),
        ).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO monitoring_alerts (
                application_id, client_name, alert_type, severity, detected_by,
                summary, status, linked_periodic_review_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ids[5], "CM Alert Only Ltd", "pep", "high", "monitor", "Alert must not become PR work", "open", overdue_id),
        )
        conn.execute(
            """
            INSERT INTO edd_cases (
                application_id, client_name, risk_level, risk_score, stage,
                assigned_officer, trigger_source, trigger_notes, origin_context
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ids[6], "CM Lifecycle Only Ltd", "VERY_HIGH", 88, "triggered", officer_id, "monitoring_alert", "Lifecycle item only", "monitoring_alert"),
        )
        conn.commit()
        conn.close()
        return {
            "pricing_ref": rows[0][1],
            "preapproval_ref": rows[1][1],
            "overdue_review_ref": f"PR-{overdue_id}",
            "due_review_ref": f"PR-{due_id}",
        }

    def test_case_management_worklist_rejects_unauthenticated_and_client(self, api_server, client_token):
        resp = http_requests.get(f"{api_server}/api/case-management/worklist", timeout=3)
        assert resp.status_code == 401

        client_resp = http_requests.get(
            f"{api_server}/api/case-management/worklist",
            headers={"Authorization": f"Bearer {client_token}"},
            timeout=3,
        )
        assert client_resp.status_code == 403

    def test_case_management_worklist_returns_backend_owned_assigned_rows(self, api_server):
        from auth import create_token

        token = create_token("cm_co", "co", "CM Compliance Officer", "officer")
        refs = self._seed_case_management_rows("cm_co")

        resp = http_requests.get(
            f"{api_server}/api/case-management/worklist?filter=all",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        refs_seen = {item["reference"] for item in body["items"]}
        assert refs["pricing_ref"] in refs_seen
        assert refs["preapproval_ref"] in refs_seen
        assert refs["overdue_review_ref"] in refs_seen
        assert refs["due_review_ref"] in refs_seen
        assert "ARF-CM-cm_co-OTHER" not in refs_seen
        assert all(item["source"] in {"applications", "periodic_reviews"} for item in body["items"])
        assert "monitoring_alert" not in {item["type"] for item in body["items"]}
        assert "lifecycle" not in {item["type"] for item in body["items"]}
        pricing = next(item for item in body["items"] if item["reference"] == refs["pricing_ref"])
        assert pricing["type"] == "application"
        assert pricing["status"] == "Pricing Under Review"
        assert pricing["open_target"]["kind"] == "application_detail"
        preapproval = next(item for item in body["items"] if item["reference"] == refs["preapproval_ref"])
        assert preapproval["type"] == "application"
        assert preapproval["open_target"]["kind"] == "pre_approval"
        overdue_review = next(item for item in body["items"] if item["reference"] == refs["overdue_review_ref"])
        assert overdue_review["type"] == "periodic_review"
        assert overdue_review["source"] == "periodic_reviews"
        assert overdue_review["due_state"] == "overdue"
        assert overdue_review["open_target"]["kind"] == "periodic_review_workspace"
        assert body["counts"]["applications"] == 6
        assert body["counts"]["periodic_reviews"] == 2
        assert body["counts"]["pre_approval"] == 1
        assert body["counts"]["overdue"] == 1
        assert body["counts"]["due_soon"] == 1
        assert body["counts"]["all"] == body["pagination"]["total"]

    def test_case_management_worklist_filters_counts_and_empty_shape(self, api_server):
        from auth import create_token

        token = create_token("cm_filter", "analyst", "CM Analyst", "officer")
        self._seed_case_management_rows("cm_filter")

        for filter_name, expected_type in (("applications", "application"), ("periodic_reviews", "periodic_review")):
            resp = http_requests.get(
                f"{api_server}/api/case-management/worklist?filter={filter_name}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=3,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["pagination"]["total"] == body["counts"][filter_name]
            assert body["items"]
            assert {item["type"] for item in body["items"]} == {expected_type}

        overdue = http_requests.get(
            f"{api_server}/api/case-management/worklist?filter=overdue",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        ).json()
        assert overdue["items"]
        assert {item["due_state"] for item in overdue["items"]} == {"overdue"}

        due_soon = http_requests.get(
            f"{api_server}/api/case-management/worklist?filter=due_soon",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        ).json()
        assert due_soon["items"]
        assert {item["due_state"] for item in due_soon["items"]} == {"due_soon"}

        empty_token = create_token("cm_empty", "analyst", "CM Empty", "officer")
        empty = http_requests.get(
            f"{api_server}/api/case-management/worklist?filter=all",
            headers={"Authorization": f"Bearer {empty_token}"},
            timeout=3,
        )
        assert empty.status_code == 200
        assert empty.json()["items"] == []
        assert empty.json()["counts"] == {
            "all": 0,
            "applications": 0,
            "periodic_reviews": 0,
            "pre_approval": 0,
            "submitted_to_compliance": 0,
            "overdue": 0,
            "due_soon": 0,
        }

    def test_case_management_defaults_to_my_assigned_work_for_admin(self, api_server):
        from auth import create_token

        token = create_token("cm_admin", "admin", "CM Admin", "officer")
        self._seed_case_management_rows("cm_admin")
        self._seed_case_management_rows("cm_visible_other")

        resp = http_requests.get(
            f"{api_server}/api/case-management/worklist?filter=all",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200, resp.text
        refs_seen = {item["reference"] for item in resp.json()["items"]}
        assert "ARF-CM-cm_admin-PRICING" in refs_seen
        assert "ARF-CM-cm_visible_other-PRICING" not in refs_seen

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
            f"{api_server}/api/applications?view=full&limit=5000",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=3,
        )
        assert default_resp.status_code == 200
        default_refs = {a["ref"] for a in default_resp.json()["applications"]}
        assert real_ref in default_refs
        assert fixture_ref not in default_refs

        include_resp = http_requests.get(
            f"{api_server}/api/applications?view=full&limit=5000&include_fixtures=1",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=3,
        )
        assert include_resp.status_code == 200
        include_refs = {a["ref"] for a in include_resp.json()["applications"]}
        assert {real_ref, fixture_ref}.issubset(include_refs)

        co_include = http_requests.get(
            f"{api_server}/api/applications?view=full&limit=5000&include_fixtures=1",
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
        assert client_default.status_code == 403

        client_portal = http_requests.get(
            f"{api_server}/api/portal/applications",
            headers={"Authorization": f"Bearer {client_token}"},
            timeout=3,
        )
        assert client_portal.status_code == 200
        client_refs = {a["ref"] for a in client_portal.json()["applications"]}
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

    def test_application_audit_log_filters_by_immutable_application_id(self, api_server):
        """Case audit reconstruction must not leak rows from deleted same-ref apps."""
        from auth import create_token
        from db import get_db

        old_app_id = "app_p3_audit_old"
        current_app_id = "app_p3_audit_current"
        app_ref = "ARF-P3-AUDIT"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE action LIKE ?", ("P3 Immutable Audit%",))
        conn.execute("DELETE FROM applications WHERE id IN (?, ?) OR ref = ?", (old_app_id, current_app_id, app_ref))
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (old_app_id, app_ref, "testclient001", "Old Audit Reconstruction Ltd",
             "Mauritius", "Technology", "SME", "in_review"),
        )
        conn.execute(
            """
            INSERT INTO audit_log
            (user_id, user_name, user_role, action, target, application_id, detail, ip_address)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                "admin001", "Test Admin", "admin", "P3 Immutable Audit Old",
                app_ref, old_app_id, json.dumps({"application_id": old_app_id}), "127.0.0.1",
            ),
        )
        conn.execute("DELETE FROM applications WHERE id = ?", (old_app_id,))
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (current_app_id, app_ref, "testclient001", "Current Audit Reconstruction Ltd",
             "Mauritius", "Technology", "SME", "in_review"),
        )
        conn.execute(
            """
            INSERT INTO audit_log
            (user_id, user_name, user_role, action, target, application_id, detail, ip_address)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                "admin001", "Test Admin", "admin", "P3 Immutable Audit Current",
                app_ref, current_app_id, json.dumps({"application_id": current_app_id}), "127.0.0.1",
            ),
        )
        conn.execute(
            """
            INSERT INTO audit_log
            (user_id, user_name, user_role, action, target, detail, ip_address)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                "admin001", "Test Admin", "admin", "P3 Immutable Audit Legacy Current",
                f"application:{app_ref}", json.dumps({"application_id": current_app_id, "event": "legacy-current"}),
                "127.0.0.1",
            ),
        )
        conn.execute(
            """
            INSERT INTO audit_log
            (user_id, user_name, user_role, action, target, detail, ip_address)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                "admin001", "Test Admin", "admin", "P3 Immutable Audit Legacy Ref Only",
                f"application:{app_ref}", json.dumps({"application_ref": app_ref, "event": "legacy-ref-only"}),
                "127.0.0.1",
            ),
        )
        conn.execute(
            """
            INSERT INTO audit_log
            (user_id, user_name, user_role, action, target, detail, ip_address)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                "admin001", "Test Admin", "admin", "P3 Immutable Audit Legacy Old",
                f"application:{app_ref}", json.dumps({"application_id": old_app_id, "event": "legacy-old"}),
                "127.0.0.1",
            ),
        )
        conn.execute(
            """
            INSERT INTO audit_log
            (user_id, user_name, user_role, action, target, application_id, detail, ip_address)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                "admin001", "Test Admin", "admin", "P3 Immutable Audit Conflicting Target",
                current_app_id, old_app_id, json.dumps({"application_id": old_app_id, "event": "conflict"}),
                "127.0.0.1",
            ),
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/applications/{app_ref}/audit-log?limit=20",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        actions = {e["action"] for e in data["entries"]}
        assert actions == {"P3 Immutable Audit Current", "P3 Immutable Audit Legacy Current"}

        by_id = http_requests.get(
            f"{api_server}/api/applications/{current_app_id}/audit-log?limit=20",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert by_id.status_code == 200
        assert {e["action"] for e in by_id.json()["entries"]} == actions

        client_token = create_token("testclient001", "client", "Test Client", "client")
        client_resp = http_requests.get(
            f"{api_server}/api/applications/{current_app_id}/audit-log?limit=20",
            headers={"Authorization": f"Bearer {client_token}"},
            timeout=3,
        )
        assert client_resp.status_code == 403

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
        conn.execute("DELETE FROM audit_log WHERE action LIKE ?", ("P3 Pack Audit%",))
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
            """
            INSERT INTO audit_log
            (user_id, user_name, user_role, action, target, application_id, detail, ip_address)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                "admin001", "Test Admin", "admin", "P3 Pack Audit Generate Memo",
                app_ref, app_id, json.dumps({"memo_version": "v1", "application_id": app_id}), "127.0.0.1",
            ),
        )
        conn.execute(
            """
            INSERT INTO audit_log
            (user_id, user_name, user_role, action, target, application_id, detail, ip_address)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                "admin001", "Test Admin", "admin", "P3 Pack Audit EDD Routed",
                f"application:{app_ref}", app_id, "prefixed", "127.0.0.1",
            ),
        )
        conn.execute(
            """
            INSERT INTO audit_log
            (user_id, user_name, user_role, action, target, detail, ip_address)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                "admin001", "Test Admin", "admin", "P3 Pack Audit Legacy Ref Only",
                app_ref, json.dumps({"application_ref": app_ref, "event": "legacy-ref-only"}), "127.0.0.1",
            ),
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
        assert all(entry["action"] != "P3 Pack Audit Legacy Ref Only" for entry in pack["audit_log"]["entries"])

    def test_application_export_pack_zip_succeeds_and_audits(self, api_server):
        """Admin/SCO can download an audited ZIP with PDFs, CSV, and app-scoped documents."""
        import io
        import zipfile
        from pathlib import Path

        from auth import create_token
        from db import get_db
        from server import UPLOAD_DIR

        app_id = "app_export_pack"
        app_ref = "ARF-EXPORT-1"
        other_app_id = "app_export_pack_other"
        encrypted_value = "gAAAAABmVeryLongEncryptedLookingValueThatMustNeverRenderInOfficerCorrectionPdf000000000000000000000000000000"
        upload_dir = Path(UPLOAD_DIR)
        upload_dir.mkdir(parents=True, exist_ok=True)
        doc_path = upload_dir / "app_export_pack_doc.pdf"
        other_doc_path = upload_dir / "app_export_pack_other_doc.pdf"
        doc_path.write_bytes(b"%PDF-1.4\nexport pack evidence\n")
        other_doc_path.write_bytes(b"%PDF-1.4\nother app evidence\n")

        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target IN (?, ?)", (app_ref, f"application:{app_ref}"))
        conn.execute("DELETE FROM application_corrections WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM screening_reviews WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM compliance_memos WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM documents WHERE application_id IN (?, ?)", (app_id, other_app_id))
        conn.execute("DELETE FROM directors WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM ubos WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM intermediaries WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id IN (?, ?)", (app_id, other_app_id))
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, ownership_structure,
             status, risk_level, risk_score, risk_dimensions, prescreening_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                app_id,
                app_ref,
                "testclient001",
                "Export Pack Ltd",
                "Mauritius",
                "Fintech",
                "LLC",
                "Direct ownership",
                "under_review",
                "HIGH",
                78,
                json.dumps({"country": 20, "sector": 30}),
                json.dumps({
                    "registered_entity_name": "Export Pack Ltd",
                    "trading_name": "Export Original",
                    "expected_activity": "Cross-border payment services",
                    "screening_report": {
                        "status": "completed",
                        "provider_reference": "safe-provider-ref",
                        "raw_provider_json": {"must": "not render"},
                    },
                }),
            ),
        )
        conn.execute(
            "INSERT INTO applications (id, ref, client_id, company_name, status) VALUES (?, ?, ?, ?, ?)",
            (other_app_id, "ARF-EXPORT-OTHER", "testclient001", "Other Export Ltd", "under_review"),
        )
        conn.execute(
            "INSERT INTO directors (id, application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?, ?)",
            ("dir_export_1", app_id, "Dana Director", "Mauritius", "No"),
        )
        conn.execute(
            "INSERT INTO ubos (id, application_id, full_name, nationality, ownership_pct, is_pep) VALUES (?, ?, ?, ?, ?, ?)",
            ("ubo_export_1", app_id, "Uma Owner", "UAE", 75, "Yes"),
        )
        conn.execute(
            "INSERT INTO intermediaries (id, application_id, entity_name, jurisdiction, ownership_pct) VALUES (?, ?, ?, ?, ?)",
            ("int_export_1", app_id, "HoldCo Export", "BVI", 25),
        )
        conn.execute(
            """
            INSERT INTO documents
            (id, application_id, doc_type, doc_name, file_path, file_size, mime_type, is_current)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("doc_export_1", app_id, "cert_inc", "../../certificate export.pdf", str(doc_path), doc_path.stat().st_size, "application/pdf", 1),
        )
        conn.execute(
            """
            INSERT INTO documents
            (id, application_id, doc_type, doc_name, file_path, file_size, mime_type, is_current)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("doc_export_other", other_app_id, "passport", "other-app.pdf", str(other_doc_path), other_doc_path.stat().st_size, "application/pdf", 1),
        )
        conn.execute(
            """
            INSERT INTO screening_reviews
            (application_id, subject_type, subject_name, disposition, disposition_code, rationale, reviewer_name)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (app_id, "entity", "Export Pack Ltd", "cleared", "false_positive", "Name-only match cleared.", "Test Admin"),
        )
        conn.execute(
            """
            INSERT INTO application_corrections
            (application_id, target_type, target_id, subject_type, field_scope, materiality,
             correction_reason, before_state, after_state, downstream_state,
             corrected_by, corrected_by_name, corrected_by_role)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                app_id,
                "prescreening_field",
                None,
                "application",
                "trading_name",
                "risk_relevant",
                "Registry evidence confirmed the trading name.",
                json.dumps({"trading_name": encrypted_value, "original_client_value": "Export Original"}),
                json.dumps({"trading_name": "Export Corrected"}),
                json.dumps({"risk_impact": "No risk recomputation required", "memo_impact": "No memo impact"}),
                "admin001",
                "Test Admin",
                "admin",
            ),
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        payload = {
            "export_type": "regulator",
            "reason": "Requested by auditor",
            "include_sections": [
                "client_submission",
                "documents",
                "risk_assessment",
                "screening_summary",
                "compliance_memo",
                "officer_corrections",
                "audit_trail",
            ],
            "redaction_level": "full_internal",
        }
        resp = http_requests.post(
            f"{api_server}/api/applications/{app_ref}/export-pack",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=10,
        )
        assert resp.status_code == 200, resp.text
        assert "application/zip" in resp.headers.get("Content-Type", "")
        assert f"RegMind_Evidence_Pack_{app_ref}_" in resp.headers.get("Content-Disposition", "")

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
            root = f"RegMind_Evidence_Pack_{app_ref}/"
            assert f"{root}00_manifest.pdf" in names
            assert f"{root}01_case_summary.pdf" in names
            assert f"{root}02_client_submission.pdf" in names
            assert f"{root}03_risk_assessment.pdf" in names
            assert f"{root}04_screening_summary.pdf" in names
            assert f"{root}05_officer_corrections.pdf" in names
            assert f"{root}06_compliance_memo.pdf" in names
            assert f"{root}07_audit_trail.csv" in names
            uploaded = [name for name in names if name.startswith(f"{root}08_uploaded_documents/")]
            assert uploaded == [f"{root}08_uploaded_documents/cert_inc_certificate_export.pdf"]
            assert all(".." not in name for name in names)
            assert not any("other-app.pdf" in name for name in names)
            combined = b"".join(zf.read(name) for name in names)
            assert encrypted_value.encode() not in combined
            audit_csv = zf.read(f"{root}07_audit_trail.csv").decode("utf-8")
            assert "evidence_pack_export_requested" in audit_csv

        conn = get_db()
        audit_rows = conn.execute(
            "SELECT action, detail FROM audit_log WHERE target = ? AND action LIKE 'evidence_pack_export_%' ORDER BY id ASC",
            (app_ref,),
        ).fetchall()
        conn.close()
        actions = [row["action"] for row in audit_rows]
        assert "evidence_pack_export_requested" in actions
        assert "evidence_pack_export_completed" in actions
        completed = json.loads(audit_rows[-1]["detail"])
        assert completed["result"] == "success"
        assert completed["file_count"] >= 9
        assert completed["pack_sha256"]

    def test_application_export_pack_sco_can_export(self, api_server):
        from auth import create_token
        from db import get_db

        app_id = "app_export_pack_sco"
        app_ref = "ARF-EXPORT-SCO"
        conn = get_db()
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute(
            "INSERT INTO applications (id, ref, client_id, company_name, status) VALUES (?, ?, ?, ?, ?)",
            (app_id, app_ref, "testclient001", "SCO Export Ltd", "under_review"),
        )
        conn.commit()
        conn.close()

        token = create_token("sco001", "sco", "Test SCO", "officer")
        resp = http_requests.post(
            f"{api_server}/api/applications/{app_ref}/export-pack",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "export_type": "internal_case",
                "reason": "SCO case review",
                "include_sections": ["audit_trail"],
                "redaction_level": "external_redacted",
            },
            timeout=10,
        )
        assert resp.status_code == 200, resp.text
        assert "application/zip" in resp.headers.get("Content-Type", "")

    @pytest.mark.parametrize(
        "token_user, expected_status",
        [
            (("testclient001", "client", "Test Client", "client"), 403),
            (("co001", "co", "Test CO", "officer"), 403),
            (("analyst001", "analyst", "Test Analyst", "officer"), 403),
        ],
    )
    def test_application_export_pack_rejects_unauthorized_roles(self, api_server, token_user, expected_status):
        from auth import create_token
        from db import get_db

        app_id = "app_export_pack_rbac"
        app_ref = "ARF-EXPORT-RBAC"
        conn = get_db()
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute(
            "INSERT INTO applications (id, ref, client_id, company_name, status) VALUES (?, ?, ?, ?, ?)",
            (app_id, app_ref, "testclient001", "RBAC Export Ltd", "under_review"),
        )
        conn.commit()
        conn.close()

        token = create_token(*token_user)
        resp = http_requests.post(
            f"{api_server}/api/applications/{app_ref}/export-pack",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "export_type": "regulator",
                "reason": "RBAC test",
                "include_sections": ["audit_trail"],
                "redaction_level": "full_internal",
            },
            timeout=5,
        )
        assert resp.status_code == expected_status

    @pytest.mark.parametrize(
        "payload, expected_error",
        [
            ({"export_type": "regulator", "reason": "", "include_sections": ["audit_trail"], "redaction_level": "full_internal"}, "reason is required"),
            ({"export_type": "bad", "reason": "x", "include_sections": ["audit_trail"], "redaction_level": "full_internal"}, "invalid export_type"),
            ({"export_type": "regulator", "reason": "x", "include_sections": ["audit_trail"], "redaction_level": "bad"}, "invalid redaction_level"),
            ({"export_type": "regulator", "reason": "x", "include_sections": ["raw_provider_json"], "redaction_level": "full_internal"}, "unknown include_section"),
            ({"export_type": "regulator", "reason": "x", "include_sections": [], "redaction_level": "full_internal"}, "include_sections must include at least one section"),
        ],
    )
    def test_application_export_pack_validates_request_body(self, api_server, payload, expected_error):
        from auth import create_token
        from db import get_db

        app_id = "app_export_pack_validation"
        app_ref = "ARF-EXPORT-VALIDATION"
        conn = get_db()
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute(
            "INSERT INTO applications (id, ref, client_id, company_name, status) VALUES (?, ?, ?, ?, ?)",
            (app_id, app_ref, "testclient001", "Validation Export Ltd", "under_review"),
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/applications/{app_ref}/export-pack",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=5,
        )
        assert resp.status_code == 400
        assert expected_error in resp.text

    def test_application_export_pack_invalid_application_returns_404(self, api_server):
        from auth import create_token

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/applications/no_such_export_app/export-pack",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "export_type": "regulator",
                "reason": "Not found test",
                "include_sections": ["audit_trail"],
                "redaction_level": "full_internal",
            },
            timeout=5,
        )
        assert resp.status_code == 404

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
             "Upload Reject Ltd", "Mauritius", "Technology", "SME", "kyc_documents"),
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

    def test_screening_evidence_upload_reuses_secure_document_endpoint(self, api_server):
        """Backoffice screening evidence upload can use supporting_document outside KYC state."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        conn.execute("DELETE FROM documents WHERE application_id = ?", ("app_screening_upload",))
        conn.execute("DELETE FROM applications WHERE id = ?", ("app_screening_upload",))
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("app_screening_upload", "ARF-SCREENING-UPLOAD", "screening_client",
             "Screening Upload Ltd", "Mauritius", "Technology", "SME", "in_review"),
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/applications/app_screening_upload/documents?doc_type=supporting_document&screening_evidence=true",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("screening-evidence.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
            timeout=3,
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["doc_type"] == "supporting_document"
        assert body["doc_name"] == "screening-evidence.pdf"

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
        assert "riskBadgeForRecord(c)" in html
        assert "Risk unavailable — recalculation required" in html

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

    def test_screening_queue_paginates_rows_without_changing_metrics(self, api_server):
        from auth import create_token
        from db import get_db

        app_defs = [
            ("app_prb_screen_page_1", "ARF-PRB-SCREEN-001", "2026-06-02T09:00:00Z"),
            ("app_prb_screen_page_2", "ARF-PRB-SCREEN-002", "2026-06-02T09:01:00Z"),
        ]
        report = {
            "screening_report": {
                "screened_at": "2026-06-02T09:05:00Z",
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
        conn.execute("DELETE FROM applications WHERE id IN (?, ?)", (app_defs[0][0], app_defs[1][0]))
        for app_id, app_ref, created_at in app_defs:
            conn.execute(
                """
                INSERT INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data, is_fixture, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    app_id,
                    app_ref,
                    "testclient001",
                    f"{app_ref} Ltd",
                    "Mauritius",
                    "Technology",
                    "SME",
                    "in_review",
                    json.dumps(report),
                    0,
                    created_at,
                ),
            )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/screening/queue?limit=1&offset=1",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["rows"]) == 1
        assert body["pagination"]["limit"] == 1
        assert body["pagination"]["offset"] == 1
        assert body["pagination"]["returned"] == 1
        assert body["pagination"]["total_rows"] >= 2
        assert body["pagination"]["has_prev"] is True
        assert body["metrics"]["applications_screened"] >= 2

    def test_screening_queue_supports_q_search_alias_and_empty_results(self, api_server):
        from auth import create_token
        from db import get_db

        app_id = "app_prb_screen_q_alias"
        app_ref = "ARF-PRB-SCREEN-Q-ALIAS"
        report = {
            "screening_report": {
                "screened_at": "2026-06-02T09:10:00Z",
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
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data, is_fixture, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                app_id,
                app_ref,
                "testclient001",
                "PRB Screening Q Alias Ltd",
                "Mauritius",
                "Technology",
                "SME",
                "in_review",
                json.dumps(report),
                0,
                "2026-06-02T09:10:00Z",
            ),
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        match_resp = http_requests.get(
            f"{api_server}/api/screening/queue?q={app_ref}&limit=20",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert match_resp.status_code == 200
        match_body = match_resp.json()
        assert {row["application_ref"] for row in match_body["rows"]} == {app_ref}
        assert match_body["pagination"]["returned"] == len(match_body["rows"])
        assert match_body["pagination"]["total_rows"] == len(match_body["rows"])

        empty_resp = http_requests.get(
            f"{api_server}/api/screening/queue?q=NO-SUCH-REF-QUEUE-AUDIT&limit=20",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert empty_resp.status_code == 200
        empty_body = empty_resp.json()
        assert empty_body["rows"] == []
        assert empty_body["pagination"]["returned"] == 0
        assert empty_body["pagination"]["total_rows"] == 0

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

    def test_investigation_workspace_linked_source_persistence_and_audit(self, api_server):
        """Formal investigation workspace exposes source metadata and audit-attributed saves."""
        from auth import create_token
        from db import get_db

        app_id = "p5cworkspace0001"
        app_ref = "ARF-P5C-WORKSPACE"
        alert_id = 950501
        conn = get_db()
        conn.execute("DELETE FROM edd_findings WHERE edd_case_id IN (SELECT id FROM edd_cases WHERE application_id = ?)", (app_id,))
        conn.execute("DELETE FROM edd_cases WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM monitoring_alerts WHERE id = ? OR application_id = ?", (alert_id, app_id))
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute(
            """
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score, is_fixture)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                app_id,
                app_ref,
                "clientp5cworkspace",
                "Workspace Monitoring Holdings Ltd",
                "Mauritius",
                "Investment Services",
                "Company",
                "approved",
                "HIGH",
                82,
                0,
            ),
        )
        conn.execute(
            """
            INSERT INTO monitoring_alerts
            (id, application_id, provider, case_identifier, discovered_via, client_name,
             alert_type, severity, detected_by, summary, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alert_id,
                app_id,
                "qa_monitor",
                "ALERT-P5C-WORKSPACE",
                "manual",
                "Workspace Monitoring Holdings Ltd",
                "adverse_media",
                "high",
                "QA",
                "Monitoring alert requires a formal narrative investigation.",
                "open",
            ),
        )
        conn.execute(
            """
            INSERT INTO edd_cases
            (application_id, client_name, risk_level, risk_score, stage, assigned_officer,
             senior_reviewer, priority, sla_due_at, trigger_source, trigger_notes,
             origin_context, linked_monitoring_alert_id, edd_notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                app_id,
                "Workspace Monitoring Holdings Ltd",
                "HIGH",
                82,
                "analysis",
                "co001",
                "sco001",
                "high",
                "2026-12-31",
                "monitoring_alert",
                "Formal monitoring-linked investigation workspace test.",
                "monitoring_alert",
                alert_id,
                "[]",
            ),
        )
        case_id = conn.execute(
            "SELECT id FROM edd_cases WHERE application_id = ? ORDER BY id DESC LIMIT 1",
            (app_id,),
        ).fetchone()["id"]
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        headers = {"Authorization": f"Bearer {token}"}

        listed = http_requests.get(
            f"{api_server}/api/edd/cases",
            headers=headers,
            timeout=3,
        )
        assert listed.status_code == 200, listed.text
        assert any(row["id"] == case_id for row in listed.json()["cases"])

        detail = http_requests.get(
            f"{api_server}/api/edd/cases/{case_id}",
            headers=headers,
            timeout=3,
        )
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["linked_source"]["type"] == "monitoring_alert"
        assert body["linked_source"]["id"] == alert_id
        assert body["linked_source"]["label"] == f"Monitoring Alert #{alert_id}"
        assert body["linked_source"]["severity"] == "high"
        assert body["linked_source"]["summary"] == "Monitoring alert requires a formal narrative investigation."

        findings = http_requests.patch(
            f"{api_server}/api/edd/cases/{case_id}/findings",
            headers=headers,
            json={
                "source_surface": "investigation_case_workspace",
                "findings": {
                    "recommended_outcome": "approve_with_conditions",
                    "findings_summary": "Formal investigation findings recorded in the workspace.",
                    "key_concerns": ["Post-onboarding alert requires review"],
                    "mitigating_evidence": ["Monitoring context reviewed"],
                    "rationale": "Relationship can continue with enhanced monitoring conditions.",
                },
            },
            timeout=3,
        )
        assert findings.status_code == 200, findings.text
        assert findings.json()["findings"]["rationale"] == "Relationship can continue with enhanced monitoring conditions."
        assert findings.json()["case_status"]["findings_complete"] is True

        patch = http_requests.patch(
            f"{api_server}/api/edd/cases/{case_id}",
            headers=headers,
            json={
                "priority": "urgent",
                "sla_due_at": "2026-12-31",
                "senior_reviewer": "sco001",
                "source_surface": "investigation_case_workspace",
            },
            timeout=3,
        )
        assert patch.status_code == 200, patch.text

        audit = http_requests.get(
            f"{api_server}/api/audit?ref={app_ref}&limit=50",
            headers=headers,
            timeout=3,
        )
        assert audit.status_code == 200, audit.text
        entries = audit.json()["entries"]
        findings_entries = [e for e in entries if e["action"] == "edd.findings.created"]
        assert findings_entries
        findings_detail = json.loads(findings_entries[0]["detail"])
        assert findings_detail["source_surface"] == "investigation_case_workspace"
        assert findings_detail["application_ref"] == app_ref
        assert findings_detail["application_id"] == app_id
        assert any(
            e["action"] == "EDD Update"
            and "Source surface: investigation_case_workspace" in (e.get("detail") or "")
            for e in entries
        )

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

    def test_document_reject_requires_non_empty_reason_without_mutation(self, api_server):
        """Rejecting a document without a reason must fail before document state changes."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        conn.execute("DELETE FROM documents WHERE id = ?", ("doc_reject_blank_reason",))
        conn.execute("DELETE FROM applications WHERE id = ?", ("app_reject_blank_reason",))
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, country, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "app_reject_blank_reason",
            "ARF-2026-REJBLANK",
            "testclient001",
            "Reject Blank Ltd",
            "Mauritius",
            "in_review",
        ))
        conn.execute("""
            INSERT INTO documents (
                id, application_id, doc_type, doc_name, file_path, verification_status, review_status, review_comment
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "doc_reject_blank_reason",
            "app_reject_blank_reason",
            "cert_inc",
            "certificate.pdf",
            "/tmp/certificate.pdf",
            "flagged",
            "pending",
            None,
        ))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/documents/doc_reject_blank_reason/review",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "rejected", "comment": "   "},
            timeout=3,
        )

        assert resp.status_code == 400, resp.text
        assert resp.json()["error"] == "rejection_reason_required"

        conn = get_db()
        doc = conn.execute(
            "SELECT review_status, review_comment, reviewed_by FROM documents WHERE id=?",
            ("doc_reject_blank_reason",),
        ).fetchone()
        audit_count = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE target = ?",
            ("ARF-2026-REJBLANK",),
        ).fetchone()["c"]
        conn.close()
        assert doc["review_status"] == "pending"
        assert doc["review_comment"] is None
        assert doc["reviewed_by"] is None
        assert audit_count == 0

    def test_document_reject_valid_reason_persists_and_audits_context(self, api_server):
        """A valid rejection reason must persist and be visible in audit context."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", ("ARF-2026-REJVALID",))
        conn.execute("DELETE FROM documents WHERE id = ?", ("doc_reject_valid_reason",))
        conn.execute("DELETE FROM applications WHERE id = ?", ("app_reject_valid_reason",))
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, country, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "app_reject_valid_reason",
            "ARF-2026-REJVALID",
            "testclient001",
            "Reject Valid Ltd",
            "Mauritius",
            "in_review",
        ))
        conn.execute("""
            INSERT INTO documents (
                id, application_id, doc_type, doc_name, file_path, verification_status, review_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            "doc_reject_valid_reason",
            "app_reject_valid_reason",
            "proof_addr",
            "address-proof.pdf",
            "/tmp/address-proof.pdf",
            "flagged",
            "pending",
        ))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        reason = "Document is expired and cannot support approval."
        resp = http_requests.post(
            f"{api_server}/api/documents/doc_reject_valid_reason/review",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "rejected", "comment": reason},
            timeout=3,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["review_status"] == "rejected"
        assert body["review_comment"] == reason

        conn = get_db()
        audit = conn.execute(
            "SELECT action, target, detail, after_state, user_id, timestamp FROM audit_log WHERE target = ? ORDER BY id DESC LIMIT 1",
            ("ARF-2026-REJVALID",),
        ).fetchone()
        doc = conn.execute(
            "SELECT review_status, review_comment, reviewed_by FROM documents WHERE id=?",
            ("doc_reject_valid_reason",),
        ).fetchone()
        conn.close()

        assert doc["review_status"] == "rejected"
        assert doc["review_comment"] == reason
        assert doc["reviewed_by"] == "admin001"
        assert audit is not None
        assert audit["action"] == "Document Review"
        assert audit["target"] == "ARF-2026-REJVALID"
        assert "doc_reject_valid_reason" in audit["detail"]
        assert "app_reject_valid_reason" in audit["detail"]
        assert reason in audit["detail"]
        after_state = json.loads(audit["after_state"])
        assert after_state["review_status"] == "rejected"
        assert after_state["review_comment"] == reason
        assert after_state["reviewed_by"] == "admin001"
        assert audit["user_id"] == "admin001"
        assert audit["timestamp"]

    def test_document_evidence_classification_persists_and_audits(self, api_server):
        """Compliance officers can classify pilot evidence without altering review/verification gates."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", ("ARF-2026-EVIDCLASS",))
        conn.execute("DELETE FROM documents WHERE application_id = ?", ("app_evidence_class",))
        conn.execute("DELETE FROM applications WHERE id = ?", ("app_evidence_class",))
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, country, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "app_evidence_class",
            "ARF-2026-EVIDCLASS",
            "testclient001",
            "Evidence Class Ltd",
            "Mauritius",
            "in_review",
        ))
        conn.execute("""
            INSERT INTO documents (
                id, application_id, person_id, doc_type, doc_name, file_path, verification_status, review_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "doc_evidence_class_1",
            "app_evidence_class",
            None,
            "cert_inc",
            "certificate-source.pdf",
            "/tmp/certificate-source.pdf",
            "verified",
            "pending",
        ))
        conn.commit()
        conn.close()

        token = create_token("co001", "co", "Test CO", "officer")
        resp = http_requests.post(
            f"{api_server}/api/documents/doc_evidence_class_1/evidence-classification",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "evidence_class": "certified_copy",
                "note": "Certified copy reviewed against onboarding evidence pack.",
            },
            timeout=3,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["document"]["evidence_class"] == "certified_copy"
        assert body["document"]["evidence_class_label"] == "Certified copy"
        assert body["document"]["pilot_proof_eligible"] is True

        detail_resp = http_requests.get(
            f"{api_server}/api/applications/ARF-2026-EVIDCLASS",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert detail_resp.status_code == 200, detail_resp.text
        doc = detail_resp.json()["documents"][0]
        assert doc["evidence_class"] == "certified_copy"
        assert doc["evidence_classification_note"] == "Certified copy reviewed against onboarding evidence pack."
        assert doc["verification_status"] == "verified"
        assert doc["review_status"] == "pending"

        conn = get_db()
        audit = conn.execute(
            "SELECT action, before_state, after_state FROM audit_log WHERE target = ? ORDER BY id DESC LIMIT 1",
            ("ARF-2026-EVIDCLASS",),
        ).fetchone()
        conn.close()
        assert audit is not None
        assert audit["action"] == "Document Evidence Classified"
        after_state = json.loads(audit["after_state"])
        assert after_state["evidence_class"] == "certified_copy"

    def test_document_evidence_classification_requires_compliance_role(self, api_server):
        """Analysts may review documents but cannot classify pilot-proof evidence."""
        from auth import create_token
        from db import get_db

        conn = get_db()
        conn.execute("DELETE FROM documents WHERE application_id = ?", ("app_evidence_rbac",))
        conn.execute("DELETE FROM applications WHERE id = ?", ("app_evidence_rbac",))
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, country, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "app_evidence_rbac",
            "ARF-2026-EVIDRBAC",
            "testclient001",
            "Evidence RBAC Ltd",
            "Mauritius",
            "in_review",
        ))
        conn.execute("""
            INSERT INTO documents (id, application_id, doc_type, doc_name, file_path, verification_status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "doc_evidence_rbac_1",
            "app_evidence_rbac",
            "cert_inc",
            "certificate.pdf",
            "/tmp/certificate.pdf",
            "verified",
        ))
        conn.commit()
        conn.close()

        analyst_token = create_token("analyst001", "analyst", "Test Analyst", "officer")
        resp = http_requests.post(
            f"{api_server}/api/documents/doc_evidence_rbac_1/evidence-classification",
            headers={"Authorization": f"Bearer {analyst_token}"},
            json={"evidence_class": "authoritative_source_document"},
            timeout=3,
        )
        assert resp.status_code == 403

    def test_pilot_evidence_summary_requires_all_required_docs_to_be_real_classes(self, api_server):
        """A case becomes approval-proof only when every required current document is class 1/2/3."""
        from auth import create_token
        from db import get_db

        app_id = "app_evidence_summary_real"
        app_ref = "ARF-2026-EVIDREAL"
        required_doc_types = [
            "cert_inc", "memarts", "reg_sh", "reg_dir",
            "fin_stmt", "poa", "board_res", "structure_chart",
        ]
        conn = get_db()
        conn.execute("DELETE FROM documents WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                risk_level, final_risk_level, status, prescreening_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id, app_ref, "testclient001", "Evidence Real Ltd", "Mauritius",
            "Technology", "SME", "LOW", "LOW", "in_review", json.dumps({}),
        ))
        for idx, doc_type in enumerate(required_doc_types, start=1):
            conn.execute("""
                INSERT INTO documents (
                    id, application_id, doc_type, doc_name, file_path,
                    verification_status, evidence_class
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                f"doc_evidence_real_{idx}", app_id, doc_type, f"{doc_type}.pdf",
                f"/tmp/{doc_type}.pdf", "verified", "authoritative_source_document",
            ))
        conn.commit()
        conn.close()

        token = create_token("co001", "co", "Test CO", "officer")
        resp = http_requests.get(
            f"{api_server}/api/applications/{app_ref}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200, resp.text
        summary = resp.json()["pilot_evidence_summary"]
        assert summary["pilot_evidence_classification"] == "approval_proof"
        assert summary["can_count_as_pilot_approval_proof"] is True
        assert summary["synthetic_required_count"] == 0
        assert summary["unclassified_required_count"] == 0

    def test_synthetic_required_document_forces_workflow_only_pilot_evidence(self, api_server):
        """Synthetic required evidence can exercise workflow mechanics but cannot count as pilot proof."""
        from auth import create_token
        from db import get_db

        app_id = "app_evidence_summary_synth"
        app_ref = "ARF-2026-EVIDSYN"
        required_doc_types = [
            "cert_inc", "memarts", "reg_sh", "reg_dir",
            "fin_stmt", "poa", "board_res", "structure_chart",
        ]
        conn = get_db()
        conn.execute("DELETE FROM documents WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                risk_level, final_risk_level, status, prescreening_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id, app_ref, "testclient001", "Evidence Synthetic Ltd", "Mauritius",
            "Technology", "SME", "LOW", "LOW", "in_review", json.dumps({}),
        ))
        for idx, doc_type in enumerate(required_doc_types, start=1):
            evidence_class = "test_only_synthetic" if doc_type == "fin_stmt" else "internal_genuine_business_document"
            conn.execute("""
                INSERT INTO documents (
                    id, application_id, doc_type, doc_name, file_path,
                    verification_status, evidence_class
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                f"doc_evidence_synth_{idx}", app_id, doc_type, f"{doc_type}.pdf",
                f"/tmp/{doc_type}.pdf", "verified", evidence_class,
            ))
        conn.commit()
        conn.close()

        token = create_token("co001", "co", "Test CO", "officer")
        resp = http_requests.get(
            f"{api_server}/api/applications/{app_ref}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200, resp.text
        summary = resp.json()["pilot_evidence_summary"]
        assert summary["pilot_evidence_classification"] == "workflow_only"
        assert summary["can_count_as_pilot_approval_proof"] is False
        assert summary["synthetic_required_count"] == 1
        assert summary["synthetic_required"][0]["doc_type"] == "fin_stmt"

    def test_synthetic_enhanced_requirement_document_forces_workflow_only_pilot_evidence(self, api_server):
        """Enhanced requirement evidence is also required evidence for pilot-proof classification."""
        from auth import create_token
        from db import get_db

        app_id = "app_evidence_summary_enhanced"
        app_ref = "ARF-2026-EVIDENH"
        required_doc_types = [
            "cert_inc", "memarts", "reg_sh", "reg_dir",
            "fin_stmt", "poa", "board_res", "structure_chart",
        ]
        conn = get_db()
        conn.execute("DELETE FROM application_enhanced_requirements WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM documents WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                risk_level, final_risk_level, status, prescreening_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id, app_ref, "testclient001", "Evidence Enhanced Ltd", "Mauritius",
            "Technology", "SME", "HIGH", "HIGH", "in_review", json.dumps({}),
        ))
        for idx, doc_type in enumerate(required_doc_types, start=1):
            conn.execute("""
                INSERT INTO documents (
                    id, application_id, doc_type, doc_name, file_path,
                    verification_status, evidence_class
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                f"doc_evidence_enh_kyc_{idx}", app_id, doc_type, f"{doc_type}.pdf",
                f"/tmp/{doc_type}.pdf", "verified", "certified_copy",
            ))
        conn.execute("""
            INSERT INTO documents (
                id, application_id, doc_type, doc_name, file_path,
                verification_status, evidence_class
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            "doc_evidence_enhanced_synthetic",
            app_id,
            "bankref",
            "synthetic-bank-reference.pdf",
            "/tmp/synthetic-bank-reference.pdf",
            "verified",
            "test_only_synthetic",
        ))
        conn.execute("""
            INSERT INTO application_enhanced_requirements (
                application_id, trigger_key, trigger_label, trigger_category,
                requirement_key, requirement_label, requirement_type,
                mandatory, blocking_approval, status, active, linked_document_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id,
            "high_risk",
            "High risk",
            "risk",
            "bank_reference",
            "Bank reference letter",
            "document",
            1,
            1,
            "accepted",
            1,
            "doc_evidence_enhanced_synthetic",
        ))
        conn.commit()
        conn.close()

        token = create_token("co001", "co", "Test CO", "officer")
        resp = http_requests.get(
            f"{api_server}/api/applications/{app_ref}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200, resp.text
        summary = resp.json()["pilot_evidence_summary"]
        assert summary["pilot_evidence_classification"] == "workflow_only"
        assert summary["can_count_as_pilot_approval_proof"] is False
        assert summary["synthetic_required_count"] >= 1
        enhanced_synthetic = [
            item for item in summary["synthetic_required"]
            if item.get("source") == "enhanced_requirement"
        ]
        assert len(enhanced_synthetic) == 1
        assert enhanced_synthetic[0]["linked_document_id"] == "doc_evidence_enhanced_synthetic"

    def test_workflow_test_acceptance_can_resolve_enhanced_requirement_without_verifying_document(self, api_server, monkeypatch):
        """Staging workflow acceptance can close an enhanced document requirement but keeps proof workflow-only."""
        from auth import create_token
        from db import get_db
        import server as server_module

        monkeypatch.setattr(server_module, "ENVIRONMENT", "staging")
        app_id = "app_workflow_accept_enhanced"
        app_ref = "ARF-2026-WORKFLOWENH"
        conn = get_db()
        conn.execute("DELETE FROM application_enhanced_requirements WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM documents WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                risk_level, final_risk_level, status, prescreening_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id, app_ref, "testclient001", "Workflow Enhanced Ltd", "Mauritius",
            "Technology", "SME", "HIGH", "HIGH", "in_review", json.dumps({}),
        ))
        conn.execute("""
            INSERT INTO documents (
                id, application_id, doc_type, doc_name, file_path,
                verification_status, verification_results, evidence_class
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "doc_workflow_enhanced_synthetic",
            app_id,
            "bankref",
            "synthetic-bank-reference.pdf",
            "/tmp/synthetic-bank-reference.pdf",
            "flagged",
            json.dumps({"overall": "flagged"}),
            "test_only_synthetic",
        ))
        conn.execute("""
            INSERT INTO application_enhanced_requirements (
                application_id, trigger_key, trigger_label, trigger_category,
                requirement_key, requirement_label, requirement_type,
                mandatory, blocking_approval, status, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id,
            "high_risk",
            "High risk",
            "risk",
            "bank_reference",
            "Bank reference letter",
            "document",
            1,
            1,
            "uploaded",
            1,
        ))
        req_id = conn.execute(
            "SELECT id FROM application_enhanced_requirements WHERE application_id=?",
            (app_id,),
        ).fetchone()["id"]
        conn.commit()
        conn.close()

        token = create_token("sco001", "sco", "Test SCO", "officer")
        resp = http_requests.post(
            f"{api_server}/api/documents/doc_workflow_enhanced_synthetic/workflow-test-acceptance",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "reason": "Synthetic evidence used only to complete staging workflow mechanics.",
                "enhanced_requirement_id": req_id,
            },
            timeout=3,
        )
        assert resp.status_code == 200, resp.text

        conn = get_db()
        doc = conn.execute(
            "SELECT verification_status, workflow_test_accepted FROM documents WHERE id=?",
            ("doc_workflow_enhanced_synthetic",),
        ).fetchone()
        req = conn.execute(
            """
            SELECT status, linked_document_id, workflow_test_accepted, workflow_test_acceptance_reason
            FROM application_enhanced_requirements
            WHERE id=?
            """,
            (req_id,),
        ).fetchone()
        audit = conn.execute(
            "SELECT action, detail FROM audit_log WHERE target=? AND action='Workflow Test Evidence Accepted' ORDER BY id DESC LIMIT 1",
            (app_ref,),
        ).fetchone()
        conn.close()
        assert doc["verification_status"] == "flagged"
        assert doc["workflow_test_accepted"] in (1, True)
        assert req["status"] == "accepted"
        assert req["linked_document_id"] == "doc_workflow_enhanced_synthetic"
        assert req["workflow_test_accepted"] in (1, True)
        assert "staging workflow" in req["workflow_test_acceptance_reason"]
        audit_detail = json.loads(audit["detail"])
        assert audit_detail["workflow_only"] is True
        assert audit_detail["can_count_as_pilot_approval_proof"] is False

        detail_resp = http_requests.get(
            f"{api_server}/api/applications/{app_ref}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert detail_resp.status_code == 200, detail_resp.text
        summary = detail_resp.json()["pilot_evidence_summary"]
        assert summary["pilot_evidence_classification"] == "workflow_only"
        assert summary["can_count_as_pilot_approval_proof"] is False

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

    def test_document_verify_persists_extracted_expiry_metadata(self, api_server, monkeypatch):
        from auth import create_token
        from db import get_db
        import server as server_module

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as handle:
            handle.write(b"%PDF-1.4 expiry persistence test")
            file_path = handle.name

        conn = get_db()
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, country, status, prescreening_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            "app_doc_verify_expiry",
            "ARF-2026-VERIFYEXPIRY",
            "testclient001",
            "Verify Expiry Ltd",
            "Mauritius",
            "draft",
            json.dumps({"registered_entity_name": "Verify Expiry Ltd", "country_of_incorporation": "Mauritius"})
        ))
        conn.execute("""
            INSERT INTO documents (id, application_id, doc_type, doc_name, file_path, file_size, mime_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            "doc_verify_expiry",
            "app_doc_verify_expiry",
            "licence",
            "licence.pdf",
            file_path,
            os.path.getsize(file_path),
            "application/pdf"
        ))
        conn.commit()
        conn.close()

        def fake_verify_document_layered(**kwargs):
            return {
                "checks": [{"label": "Expiry", "result": "pass", "message": "ok"}],
                "overall": "verified",
                "extracted_fields": {
                    "expiry_date": "2030-01-01",
                    "valid_until": "2030-02-01",
                },
            }

        monkeypatch.setattr(server_module, "verify_document_layered", fake_verify_document_layered)
        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/documents/doc_verify_expiry/verify",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        assert resp.status_code == 200

        conn = get_db()
        stored = conn.execute(
            "SELECT expiry_date, valid_until FROM documents WHERE id = ?",
            ("doc_verify_expiry",),
        ).fetchone()
        conn.close()
        assert stored["expiry_date"] == "2030-01-01"
        assert stored["valid_until"] == "2030-02-01"

    def test_document_verify_preserves_existing_expiry_when_no_extracted_fields(self, api_server, monkeypatch):
        from auth import create_token
        from db import get_db
        import server as server_module

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as handle:
            handle.write(b"%PDF-1.4 expiry preserve test")
            file_path = handle.name

        conn = get_db()
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, country, status, prescreening_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            "app_doc_verify_existing_expiry",
            "ARF-2026-VERIFYEXISTING",
            "testclient001",
            "Verify Existing Expiry Ltd",
            "Mauritius",
            "draft",
            json.dumps({"registered_entity_name": "Verify Existing Expiry Ltd", "country_of_incorporation": "Mauritius"})
        ))
        conn.execute("""
            INSERT INTO documents
                (id, application_id, doc_type, doc_name, file_path, file_size, mime_type, expiry_date, valid_until)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "doc_verify_existing_expiry",
            "app_doc_verify_existing_expiry",
            "licence",
            "licence.pdf",
            file_path,
            os.path.getsize(file_path),
            "application/pdf",
            "2031-01-01",
            "2031-02-01",
        ))
        conn.commit()
        conn.close()

        def fake_verify_document_layered(**kwargs):
            return {
                "checks": [{"id": "GATE-01", "label": "Gate", "result": "fail", "message": "bad file"}],
                "overall": "flagged",
                "extracted_fields": {},
            }

        monkeypatch.setattr(server_module, "verify_document_layered", fake_verify_document_layered)
        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/documents/doc_verify_existing_expiry/verify",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        assert resp.status_code == 200

        conn = get_db()
        stored = conn.execute(
            "SELECT expiry_date, valid_until FROM documents WHERE id = ?",
            ("doc_verify_existing_expiry",),
        ).fetchone()
        conn.close()
        assert stored["expiry_date"] == "2031-01-01"
        assert stored["valid_until"] == "2031-02-01"


class TestMemoSupervisorAuditSchema:
    def test_supervisor_run_writes_severity_and_verify_endpoint_works(self, api_server):
        """POST /memo/supervisor/run must persist the modern hash-chain row shape."""
        from auth import create_token
        from db import get_db
        from tests.conftest import make_base_memo

        app_id = f"app_supervisor_schema_{uuid.uuid4().hex[:8]}"
        app_ref = f"ARF-SUP-SCHEMA-{uuid.uuid4().hex[:6]}"
        memo_data = make_base_memo({
            "metadata": {
                "risk_rating": "LOW",
                "risk_score": 12,
                "approval_recommendation": "REVIEW",
                "agent5_input_contract": {
                    "final_risk_level": "LOW",
                    "composite_score": 12,
                    "declared_pep_present": False,
                    "sector": "Software",
                    "sector_label": "Software",
                    "sector_risk_tier": "low",
                    "country": "Mauritius",
                    "jurisdiction_risk_tier": "low",
                    "ownership_transparency_status": "clear",
                    "screening_terminality_summary": {
                        "terminal": True,
                        "has_terminal_match": False,
                        "has_non_terminal": False,
                    },
                    "edd_trigger_flags": [],
                },
            }
        })

        conn = get_db()
        conn.execute("DELETE FROM supervisor_audit_log WHERE application_id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                status, risk_level, risk_score, assigned_to
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id,
            app_ref,
            "testclient001",
            "Supervisor Schema Ltd",
            "Mauritius",
            "Software",
            "SME",
            "compliance_review",
            "LOW",
            12,
            "admin001",
        ))
        conn.execute("""
            INSERT INTO compliance_memos (application_id, memo_data, review_status)
            VALUES (?, ?, ?)
        """, (app_id, json.dumps(memo_data), "draft"))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        headers = {"Authorization": f"Bearer {token}"}
        run_resp = http_requests.post(
            f"{api_server}/api/applications/{app_id}/memo/supervisor/run",
            headers=headers,
            timeout=10,
        )
        assert run_resp.status_code == 200, run_resp.text

        conn = get_db()
        row = conn.execute(
            """
            SELECT event_type, severity, application_id, entry_hash
            FROM supervisor_audit_log
            WHERE application_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (app_id,),
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["event_type"] == "supervisor_verdict"
        assert row["severity"] == "info"
        assert row["entry_hash"]

        # Production startup initializes the supervisor singleton before
        # routes are served; this module-level HTTP fixture calls make_app()
        # directly, so initialize the same singleton before exercising verify.
        from supervisor.api import setup_supervisor
        setup_supervisor(os.environ["DB_PATH"])

        verify_resp = http_requests.get(
            f"{api_server}/api/supervisor/audit/verify?limit=10",
            headers=headers,
            timeout=3,
        )
        assert verify_resp.status_code == 200, verify_resp.text
        verify_body = verify_resp.json()
        assert verify_body["verified"] is True
        assert verify_body["entries_checked"] >= 1


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

    def test_csp_report_only_header_is_stricter_and_does_not_touch_enforcing(self, api_server):
        """PR-22a: a Report-Only CSP measures the inline surface WITHOUT enforcing.

        Guarantees: (1) the report-only header is present and is the stricter,
        measuring policy — it drops 'unsafe-inline' from script-src/style-src;
        (2) the enforcing header is UNCHANGED — still present and still permissive
        (still carries 'unsafe-inline'), i.e. the app cannot break; (3) the two
        are distinct headers (report-only is not a replacement of the enforcing
        one)."""
        resp = http_requests.get(f"{api_server}/api/health", timeout=3)
        enforcing = resp.headers.get("Content-Security-Policy", "")
        report_only = resp.headers.get("Content-Security-Policy-Report-Only", "")

        # (1) report-only present and stricter: no 'unsafe-inline' anywhere in it
        assert report_only, "Content-Security-Policy-Report-Only header must be set"
        assert "default-src 'self'" in report_only
        assert "'unsafe-inline'" not in report_only, \
            "report-only policy must be the stricter measuring policy (no unsafe-inline)"
        assert "script-src 'self' https://cdnjs.cloudflare.com" in report_only

        # (2) enforcing header untouched — still permissive so nothing breaks
        assert enforcing, "enforcing Content-Security-Policy must still be present"
        assert "'unsafe-inline'" in enforcing, \
            "enforcing CSP must NOT have been tightened (no-break guarantee)"

        # (3) distinct headers — report-only did not replace the enforcing one
        assert enforcing != report_only

    def test_csp_report_only_present_on_portal_and_backoffice(self, api_server):
        """The measuring policy must land on the HTML surfaces (the only ones with
        inline scripts/styles), delivered via BaseHandler subclasses."""
        for path in ("/portal", "/backoffice"):
            resp = http_requests.get(f"{api_server}{path}", timeout=3)
            assert resp.status_code == 200
            ro = resp.headers.get("Content-Security-Policy-Report-Only", "")
            assert ro and "'unsafe-inline'" not in ro, f"missing/weak report-only CSP on {path}"


def _seed_document_verification_case(app_id, ref, doc_id, doc_type="cert_inc"):
    from db import get_db

    file_path = os.path.join(tempfile.gettempdir(), f"{doc_id}.pdf")
    with open(file_path, "wb") as handle:
        handle.write(
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Count 0>>endobj\n"
            b"trailer<</Root 1 0 R>>\n%%EOF\n"
        )

    conn = get_db()
    conn.execute(
        """
        INSERT INTO applications (
            id, ref, client_id, company_name, country, sector, entity_type,
            status, risk_level, risk_score, prescreening_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            ref,
            "testclient001",
            "Verification Reliability Corp",
            "Mauritius",
            "Technology",
            "Private Company",
            "kyc_documents",
            "LOW",
            25,
            json.dumps({
                "registered_entity_name": "Verification Reliability Corp",
                "country_of_incorporation": "Mauritius",
                "brn": "VR-001",
                "ubos": [{"full_name": "Uma Owner", "ownership_pct": 100}],
                "directors": [{"full_name": "John Director"}],
            }),
        ),
    )
    conn.execute(
        """
        INSERT INTO documents (
            id, application_id, doc_type, doc_name, file_path, file_size,
            mime_type, verification_status, is_current, version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            app_id,
            doc_type,
            f"{doc_type}.pdf",
            file_path,
            os.path.getsize(file_path),
            "application/pdf",
            "pending",
            1,
            1,
        ),
    )
    conn.commit()
    conn.close()
    return file_path


class TestDocumentVerificationRuntimeReliability:
    def test_document_verification_string_payload_does_not_raise_attribute_error(self, api_server, monkeypatch):
        """String verifier payloads are flagged for review instead of calling .get on str."""
        import server
        from auth import create_token

        uid = uuid.uuid4().hex[:8]
        app_id = f"verify_str_{uid}"
        doc_id = f"doc_str_{uid}"
        _seed_document_verification_case(app_id, f"ARF-VERIFY-STR-{uid}", doc_id)

        monkeypatch.setattr(server, "HAS_DOC_VERIFICATION", True)
        monkeypatch.setattr(server, "verify_document_layered", lambda **_: "unstructured verifier response")

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/documents/{doc_id}/verify",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["verification_status"] == "flagged"
        assert body["verification_state"] == "flagged"
        assert body["verification_success"] is False
        assert body["verification_results"]["verification_failure_classification"] is None
        assert isinstance(body["checks"], list)
        assert isinstance(body["checks"][0], dict)
        assert "unstructured str check payload" in body["checks"][0]["message"]

        from db import get_db

        conn = get_db()
        transitions = conn.execute(
            """
            SELECT before_state, after_state
            FROM audit_log
            WHERE target=? AND action='Document Verification State Changed'
            ORDER BY id ASC
            """,
            (f"ARF-VERIFY-STR-{uid}",),
        ).fetchall()
        conn.close()
        assert len(transitions) >= 2
        assert json.loads(transitions[0]["after_state"])["verification_status"] == "in_progress"
        assert json.loads(transitions[-1]["after_state"])["verification_status"] == "flagged"

    def test_document_verification_failure_rolls_back_and_releases_db_connection(self, api_server, monkeypatch):
        """Forced verifier exceptions must not poison the next DB-backed auth request."""
        import bcrypt
        import server
        from auth import create_token
        from db import get_db

        uid = uuid.uuid4().hex[:8]
        app_id = f"verify_fail_{uid}"
        doc_id = f"doc_fail_{uid}"
        _seed_document_verification_case(app_id, f"ARF-VERIFY-FAIL-{uid}", doc_id)

        def verifier_failure(**_):
            raise RuntimeError("synthetic verifier failure")

        monkeypatch.setattr(server, "HAS_DOC_VERIFICATION", True)
        monkeypatch.setattr(server, "verify_document_layered", verifier_failure)

        token = create_token("admin001", "admin", "Test Admin", "officer")
        verify = http_requests.post(
            f"{api_server}/api/documents/{doc_id}/verify",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        assert verify.status_code == 200, verify.text
        assert verify.json()["verification_status"] == "flagged"

        login_email = f"verify-login-{uid}@example.test"
        login_password = "StrongPass123!"
        conn = get_db()
        pw_hash = bcrypt.hashpw(login_password.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, ?)",
            (f"user_{uid}", login_email, pw_hash, "Verification Login", "admin", "active"),
        )
        conn.commit()
        conn.close()

        login = http_requests.post(
            f"{api_server}/api/auth/officer/login",
            json={"email": login_email, "password": login_password},
            timeout=5,
        )
        assert login.status_code == 200, login.text

        me = http_requests.get(
            f"{api_server}/api/auth/me",
            headers={"Authorization": f"Bearer {login.json()['token']}"},
            timeout=5,
        )
        assert me.status_code == 200, me.text

    def test_officer_login_still_works_after_document_verification_exception(self, api_server, monkeypatch):
        """Regression label: officer login remains DB-backed after verifier exceptions."""
        import bcrypt
        import server
        from auth import create_token
        from db import get_db

        uid = uuid.uuid4().hex[:8]
        app_id = f"verify_login_{uid}"
        doc_id = f"doc_login_{uid}"
        _seed_document_verification_case(app_id, f"ARF-VERIFY-LOGIN-{uid}", doc_id)
        monkeypatch.setattr(server, "HAS_DOC_VERIFICATION", True)
        monkeypatch.setattr(server, "verify_document_layered", lambda **_: (_ for _ in ()).throw(RuntimeError("boom")))

        token = create_token("admin001", "admin", "Test Admin", "officer")
        verify = http_requests.post(
            f"{api_server}/api/documents/{doc_id}/verify",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        assert verify.status_code == 200, verify.text

        email = f"verify-login-exact-{uid}@example.test"
        password = "StrongPass123!"
        conn = get_db()
        conn.execute(
            "INSERT INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"login_user_{uid}",
                email,
                bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
                "Verification Login Exact",
                "admin",
                "active",
            ),
        )
        conn.commit()
        conn.close()

        login = http_requests.post(
            f"{api_server}/api/auth/officer/login",
            json={"email": email, "password": password},
            timeout=5,
        )
        assert login.status_code == 200, login.text

    def test_auth_me_still_works_after_document_verification_exception(self, api_server, monkeypatch):
        """Regression label: /api/auth/me remains available after verifier exceptions."""
        import server
        from auth import create_token

        uid = uuid.uuid4().hex[:8]
        app_id = f"verify_me_{uid}"
        doc_id = f"doc_me_{uid}"
        _seed_document_verification_case(app_id, f"ARF-VERIFY-ME-{uid}", doc_id)
        monkeypatch.setattr(server, "HAS_DOC_VERIFICATION", True)
        monkeypatch.setattr(server, "verify_document_layered", lambda **_: (_ for _ in ()).throw(RuntimeError("boom")))

        token = create_token("admin001", "admin", "Test Admin", "officer")
        verify = http_requests.post(
            f"{api_server}/api/documents/{doc_id}/verify",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        assert verify.status_code == 200, verify.text

        me = http_requests.get(
            f"{api_server}/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        assert me.status_code == 200, me.text

    def test_async_verify_flag_does_not_change_synchronous_verify_contract(self, api_server, monkeypatch):
        """FF_ASYNC_VERIFY foundation is dark; /verify remains synchronous and authoritative."""
        import server
        from auth import create_token
        from db import get_db

        uid = uuid.uuid4().hex[:8]
        app_id = f"verify_async_{uid}"
        doc_id = f"doc_async_{uid}"
        _seed_document_verification_case(app_id, f"ARF-VERIFY-ASYNC-{uid}", doc_id)

        original_is_enabled = server.flags.is_enabled
        monkeypatch.setattr(
            server.flags,
            "is_enabled",
            lambda flag: True if flag == "FF_ASYNC_VERIFY" else original_is_enabled(flag),
        )
        monkeypatch.setattr(server, "HAS_DOC_VERIFICATION", True)
        monkeypatch.setattr(
            server,
            "verify_document_layered",
            lambda **_: {
                "overall": "verified",
                "checks": [{
                    "label": "AI Verification",
                    "type": "validity",
                    "result": "pass",
                    "message": "Synchronous verification completed.",
                }],
            },
        )

        token = create_token("admin001", "admin", "Test Admin", "officer")
        queued = http_requests.post(
            f"{api_server}/api/documents/{doc_id}/verify",
            headers={"Authorization": f"Bearer {token}", "X-Request-ID": f"req-{uid}"},
            timeout=5,
        )
        assert queued.status_code == 200, queued.text
        body = queued.json()
        assert body["verification_status"] == "verified"
        assert body["verification_terminal"] is True

        conn = get_db()
        try:
            stored = conn.execute("SELECT verification_status FROM documents WHERE id=?", (doc_id,)).fetchone()
            job_count = conn.execute(
                "SELECT COUNT(*) AS c FROM verification_jobs WHERE document_id=?",
                (doc_id,),
            ).fetchone()["c"]
        finally:
            conn.close()
        assert stored["verification_status"] == "verified"
        assert job_count == 0

    def test_multi_document_verification_does_not_exhaust_pool(self, api_server, monkeypatch):
        """Repeated malformed verifier payloads remain bounded and auth keeps working."""
        import server
        from auth import create_token

        monkeypatch.setattr(server, "HAS_DOC_VERIFICATION", True)
        monkeypatch.setattr(
            server,
            "verify_document_layered",
            lambda **_: {"overall": "flagged", "checks": ["string warning", None]},
        )

        token = create_token("admin001", "admin", "Test Admin", "officer")
        uid = uuid.uuid4().hex[:8]
        for idx in range(8):
            app_id = f"verify_multi_{uid}_{idx}"
            doc_id = f"doc_multi_{uid}_{idx}"
            _seed_document_verification_case(app_id, f"ARF-VERIFY-MULTI-{uid}-{idx}", doc_id)
            resp = http_requests.post(
                f"{api_server}/api/documents/{doc_id}/verify",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["verification_status"] == "flagged"

        me = http_requests.get(
            f"{api_server}/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        assert me.status_code == 200, me.text

    def test_periodic_review_evidence_document_runs_agent1_verification(self, api_server, monkeypatch):
        """Periodic-review evidence documents use the same Agent 1 verification path."""
        import server
        from auth import create_token
        from db import get_db

        uid = uuid.uuid4().hex[:8]
        app_id = f"verify_pr_{uid}"
        doc_id = f"doc_pr_{uid}"
        _seed_document_verification_case(app_id, f"ARF-VERIFY-PR-{uid}", doc_id)

        conn = get_db()
        conn.execute(
            """
            INSERT INTO periodic_reviews (application_id, client_name, risk_level, status, required_items)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                app_id,
                "Verification Reliability Corp",
                "LOW",
                "in_progress",
                json.dumps([{
                    "id": "req-pr-doc",
                    "item_type": "kyc_refresh",
                    "label": "Refresh KYC evidence",
                    "severity": "high",
                    "status": "open",
                }]),
            ),
        )
        review_id = conn.execute("SELECT id FROM periodic_reviews ORDER BY id DESC LIMIT 1").fetchone()["id"]
        conn.execute(
            """
            INSERT INTO periodic_review_evidence_links
            (periodic_review_id, requirement_id, document_id, link_type, linked_by, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (review_id, "req-pr-doc", doc_id, "requirement_evidence", "admin001", "Periodic review upload"),
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(server, "HAS_DOC_VERIFICATION", True)
        monkeypatch.setattr(
            server,
            "verify_document_layered",
            lambda **_: {
                "overall": "verified",
                "checks": [{"label": "Agent 1 document check", "result": "pass", "message": "ok"}],
            },
        )

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/documents/{doc_id}/verify",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["verification_status"] == "verified"

        conn = get_db()
        try:
            execution = conn.execute(
                """
                SELECT agent_name, agent_number, status
                FROM agent_executions
                WHERE application_id = ? AND agent_number = 1
                ORDER BY id DESC LIMIT 1
                """,
                (app_id,),
            ).fetchone()
            link = conn.execute(
                """
                SELECT l.document_id, d.verification_status
                FROM periodic_review_evidence_links l
                JOIN documents d ON d.id = l.document_id
                WHERE l.periodic_review_id = ? AND l.requirement_id = ?
                """,
                (review_id, "req-pr-doc"),
            ).fetchone()
        finally:
            conn.close()

        assert execution is not None
        assert execution["agent_name"] == "verify_document"
        assert execution["agent_number"] == 1
        assert execution["status"] == "verified"
        assert link["document_id"] == doc_id
        assert link["verification_status"] == "verified"

    def test_audit_write_failure_does_not_leak_transaction(self, monkeypatch):
        """Agent execution audit failures must rollback/close instead of leaking connections."""
        import db as db_module

        class FailingDb:
            def __init__(self):
                self.rolled_back = False
                self.closed = False

            def execute(self, *_args, **_kwargs):
                raise RuntimeError("insert failed")

            def commit(self):
                raise AssertionError("commit should not be reached")

            def rollback(self):
                self.rolled_back = True

            def close(self):
                self.closed = True

        fake_db = FailingDb()
        monkeypatch.setattr(db_module, "get_db", lambda: fake_db)

        db_module.log_agent_execution(
            application_id="app",
            agent_name="verify_document",
            agent_number=1,
            status="flagged",
            checks=[{"result": "warn", "message": "warning"}],
        )

        assert fake_db.rolled_back is True
        assert fake_db.closed is True


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
        from tests.conftest import clean_ca_prescreening
        return json.dumps(clean_ca_prescreening(
            screened_at="2026-04-30T10:00:00",
            company_name="Phase 1B Screening Ltd",
        ))

    def _insert_approved_memo(self, conn, app_id):
        conn.execute("""
            INSERT INTO compliance_memos (
                application_id, memo_data, generated_by, ai_recommendation,
                review_status, quality_score, validation_status, supervisor_status, approval_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            "Fixture approval reason",
        ))

    def _seed_pending_second_review_approval_app(self, conn, app_id, app_ref):
        from tests.conftest import insert_verified_required_documents

        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM agent_executions WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM documents WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM compliance_memos WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM screening_reviews WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        prescreening = {
            "company_name": "Second Review Gate Ltd",
            "screening_report": {
                "screening_mode": "live",
                "screened_at": "2026-04-30T10:00:00",
                "company_screening": {
                    "found": True,
                    "sanctions": {
                        "matched": True,
                        "results": [{"name": "Second Review Gate Ltd", "is_sanctioned": True}],
                        "source": "sumsub",
                        "provider": "sumsub",
                        "api_status": "live",
                    },
                },
                "director_screenings": [],
                "ubo_screenings": [],
            },
            "screening_valid_until": "2026-07-29T10:00:00",
            "screening_validity_days": 90,
        }
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                status, risk_level, final_risk_level, risk_score, prescreening_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id,
            app_ref,
            f"{app_id}_client",
            "Second Review Gate Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "in_review",
            "MEDIUM",
            "MEDIUM",
            42,
            json.dumps(prescreening),
        ))
        self._insert_approved_memo(conn, app_id)
        insert_verified_required_documents(conn, app_id)
        conn.execute(
            """
            INSERT INTO screening_reviews (
                application_id, subject_type, subject_name, disposition, notes,
                disposition_code, rationale, sensitivity_flags, requires_four_eyes,
                reviewer_id, reviewer_name
            ) VALUES (?, 'entity', 'Second Review Gate Ltd', 'cleared', ?, 'false_positive_cleared', ?, ?, 1, 'co001', 'Compliance Officer')
            """,
            (
                app_id,
                "Provider case CA-CASE7-001 and registry evidence retained.",
                "Officer reviewed provider hit and marked it false positive pending SCO second review.",
                json.dumps(["provider_hit"]),
            ),
        )
        conn.execute(
            """
            INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address)
            VALUES ('co001', 'Compliance Officer', 'co', 'Screening Review', ?, ?, '127.0.0.1')
            """,
            (
                app_ref,
                json.dumps({
                    "subject_type": "entity",
                    "subject_name": "Second Review Gate Ltd",
                    "disposition": "cleared",
                    "disposition_code": "false_positive_cleared",
                    "evidence_reference": "Provider case CA-CASE7-001 and registry evidence retained.",
                }, sort_keys=True),
            ),
        )
        conn.commit()

    def _insert_enhanced_requirement(self, conn, app_id, *, status="accepted", mandatory=1, blocking_approval=1):
        suffix = uuid.uuid4().hex[:8]
        conn.execute(
            """
            INSERT INTO application_enhanced_requirements (
                application_id, trigger_key, trigger_label, trigger_category,
                requirement_key, requirement_label, requirement_description,
                audience, requirement_type, subject_scope, blocking_approval,
                waivable, waiver_roles, mandatory, status, generation_source,
                trigger_reason, trigger_context, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                app_id,
                "high_or_very_high_risk",
                "HIGH / VERY_HIGH risk",
                "risk",
                f"api_approval_gate_{suffix}",
                "Enhanced approval gate evidence",
                "Evidence required for enhanced review approval.",
                "client",
                "document",
                "application",
                blocking_approval,
                1,
                json.dumps(["admin", "sco"]),
                mandatory,
                status,
                "test",
                "Approval gate API test trigger",
                "{}",
                1,
            ),
        )

    def _completed_match_prescreening(self, company_name="Screening Workflow Ltd"):
        return {
            "screening_report": {
                "screening_mode": "live",
                "screened_at": "2026-04-30T10:00:00Z",
                "company_screening": {
                    "found": True,
                    "sanctions": {
                        "matched": True,
                        "results": [{"name": company_name, "is_sanctioned": True}],
                        "source": "sumsub",
                        "provider": "sumsub",
                        "api_status": "live",
                    },
                },
                "director_screenings": [],
                "ubo_screenings": [],
                "total_hits": 1,
            }
        }

    def _insert_screening_workflow_app(
        self,
        conn,
        app_id,
        app_ref,
        *,
        company_name="Screening Workflow Ltd",
        status="in_review",
        lane="Standard Review",
        risk_level="LOW",
        final_risk_level="LOW",
    ):
        conn.execute("DELETE FROM audit_log WHERE target IN (?, ?)", (app_ref, f"application:{app_ref}"))
        conn.execute("DELETE FROM edd_cases WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM screening_reviews WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute(
            """
            INSERT INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type,
                 status, onboarding_lane, risk_level, base_risk_level,
                 final_risk_level, risk_score, prescreening_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                app_id,
                app_ref,
                f"client_{app_id}",
                company_name,
                "United Kingdom",
                "Technology",
                "Listed Company",
                status,
                lane,
                risk_level,
                "LOW",
                final_risk_level,
                18,
                json.dumps(self._completed_match_prescreening(company_name)),
            ),
        )

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
            "submitted_to_compliance",
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

    def test_pending_screening_second_review_blocks_decision_with_structured_audit(self, api_server):
        """Final decision approval must expose and audit pending screening second-review blockers."""
        from auth import create_token
        from db import get_db

        app_id = "app_screening_second_review_decision_block"
        app_ref = "ARF-SCREENING-SECOND-DECISION"
        conn = get_db()
        self._seed_pending_second_review_approval_app(conn, app_id, app_ref)
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/applications/{app_id}/decision",
            json={
                "decision": "approve",
                "decision_reason": "Testing pending screening second-review approval block.",
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
        body = resp.json()
        assert body["code"] == "screening_second_review_pending"
        assert body["blockers"][0]["title"] == "Screening second review pending"
        assert body["blockers"][0]["required_reviewer_role"] == "SCO/admin"
        assert body["blockers"][0]["screening_review_id"]

        conn = get_db()
        audit = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE target = ? AND action = 'approval_blocked_screening_second_review_pending'
            ORDER BY id DESC LIMIT 1
            """,
            (app_ref,),
        ).fetchone()
        decision = conn.execute(
            "SELECT status FROM applications WHERE id = ?",
            (app_id,),
        ).fetchone()
        conn.close()

        assert decision["status"] == "in_review"
        assert audit is not None
        detail = json.loads(audit["detail"])
        assert detail["application_id"] == app_id
        assert detail["application_ref"] == app_ref
        assert detail["actor_user_id"] == "admin001"
        assert detail["actor_role"] == "admin"
        assert detail["source_surface"] == "application_decision"
        assert detail["pending_screening_review_ids"]

    def test_pending_screening_second_review_blocks_legacy_status_transition(self, api_server):
        """Legacy direct status approval is blocked outright (PR-APPROVAL-AUTHORITY-MATRIX-1).

        Previously PATCH /applications/:id could attempt approval and was stopped by
        the screening second-review gate (400). The terminal-decision guard now blocks
        ANY approve/reject via generic status PATCH (409) before gate evaluation,
        routing the actor to /decision. The invariant is stronger: no bypass, status
        unchanged, attempt audited as application.decision_blocked.
        """
        from auth import create_token
        from db import get_db

        app_id = "app_screening_second_review_status_block"
        app_ref = "ARF-SCREENING-SECOND-STATUS"
        conn = get_db()
        self._seed_pending_second_review_approval_app(conn, app_id, app_ref)
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.patch(
            f"{api_server}/api/applications/{app_id}",
            json={"status": "approved", "notes": "Legacy status approval attempt."},
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )

        assert resp.status_code == 409
        assert "Terminal decision blocked" in resp.json()["error"]

        conn = get_db()
        audit = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE target = ? AND action = 'Governance Attempt'
            ORDER BY id DESC LIMIT 1
            """,
            (app_ref,),
        ).fetchone()
        app = conn.execute("SELECT status FROM applications WHERE id = ?", (app_id,)).fetchone()
        conn.close()

        # No bypass: terminal status was not written.
        assert app["status"] == "in_review"
        assert audit is not None
        assert json.loads(audit["detail"])["action"] == "application.decision_blocked"

    def test_approval_document_gate_failure_returns_structured_blockers(self, api_server):
        """Final approval document evidence failures must expose blocker payloads."""
        from auth import create_token
        from db import get_db
        from tests.conftest import insert_verified_required_documents

        app_id = "app_prdoc1_approval_structured_blockers"
        app_ref = "ARF-PRDOC1-APPROVAL-BLOCKERS"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM agent_executions WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM documents WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM compliance_memos WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                status, risk_level, final_risk_level, risk_score, prescreening_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id,
            app_ref,
            "prdoc1_client",
            "PR-DOC1 Approval Blockers Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "in_review",
            "MEDIUM",
            "MEDIUM",
            42,
            self._live_prescreening(),
        ))
        self._insert_approved_memo(conn, app_id)
        inserted_docs = insert_verified_required_documents(conn, app_id)
        pending_doc_id = inserted_docs[0]
        conn.execute(
            """
            UPDATE documents
               SET verification_status = 'pending',
                   verification_results = ?,
                   verified_at = NULL
             WHERE id = ?
            """,
            (json.dumps({"overall": "pending", "checks": []}), pending_doc_id),
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/applications/{app_id}/decision",
            json={
                "decision": "approve",
                "decision_reason": "Testing structured PR-DOC1 document blockers.",
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
        body = resp.json()
        assert body["document_evidence_gate"]["passed"] is False
        assert body["document_evidence_gate"]["reliance_status"] == "blocked"
        assert body["document_blockers"]
        first_blocker = body["document_blockers"][0]
        assert first_blocker["code"] == "document_pending_verification"
        assert first_blocker["document_id"] == pending_doc_id
        assert first_blocker["doc_type"] == "cert_inc"

    def test_memo_json_serialization_normalizes_datetime_date_decimal_and_rows(self):
        """Memo persistence must recursively preserve evidence while making it JSON-safe."""
        from decimal import Decimal
        from server import _json_dumps_strict, _json_ready_value

        class RowLike:
            def __init__(self):
                self.data = {
                    "created_at": datetime(2026, 5, 18, 10, 30, tzinfo=timezone.utc),
                    "score": Decimal("12.50"),
                }

            def keys(self):
                return self.data.keys()

            def __getitem__(self, key):
                return self.data[key]

        payload = {
            "edd_case": RowLike(),
            "findings": [{"reviewed_on": datetime(2026, 5, 18, 11, 0, tzinfo=timezone.utc)}],
            "effective_date": datetime(2026, 5, 18, tzinfo=timezone.utc).date(),
            "amount": Decimal("42.75"),
        }

        normalized = _json_ready_value(payload)
        encoded = _json_dumps_strict(payload, sort_keys=True)
        decoded = json.loads(encoded)

        assert normalized["edd_case"]["created_at"].startswith("2026-05-18T10:30:00")
        assert decoded["findings"][0]["reviewed_on"].startswith("2026-05-18T11:00:00")
        assert decoded["effective_date"] == "2026-05-18"
        assert decoded["amount"] == 42.75
        assert decoded["edd_case"]["score"] == 12.5

    @pytest.mark.parametrize(
        "scenario, sector, director_is_pep",
        [
            ("pep", "Consulting", True),
            ("crypto", "Crypto / VASP", False),
        ],
    )
    def test_edd_memo_generation_handles_datetime_enhanced_evidence(
        self, api_server, monkeypatch, scenario, sector, director_is_pep
    ):
        """EDD/enhanced memo evidence with native datetimes must not 500 during memo persistence."""
        from auth import create_token
        from tests.conftest import insert_verified_required_documents
        from db import get_db
        import server

        app_id = f"app_edd_memo_datetime_{scenario}"
        app_ref = f"ARF-2026-EDD-MEMO-DT-{scenario.upper()}"
        conn = get_db()
        conn.execute("DELETE FROM compliance_memos WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM documents WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM directors WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM ubos WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        prescreening = {
            "registered_entity_name": "EDD Memo Datetime Ltd",
            "source_of_funds": "Operating revenue",
            "expected_volume": "100000",
            "operating_countries": "Mauritius",
            "business_activity": "Consulting",
            "screening_report": {
                "screening_mode": "live",
                "company_screening": {
                    "sanctions": {
                        "matched": False,
                        "api_status": "live",
                        "provider": "sumsub",
                        "source": "sumsub",
                    }
                },
                "director_screenings": [],
                "ubo_screenings": [],
                "total_hits": 0,
            },
        }
        conn.execute(
            """
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                status, onboarding_lane, risk_level, base_risk_level, final_risk_level,
                risk_score, prescreening_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                app_id,
                app_ref,
                f"client_edd_memo_datetime_{scenario}",
                f"EDD Memo Datetime {scenario.title()} Ltd",
                "Mauritius",
                sector,
                "SME",
                "kyc_submitted",
                "EDD",
                "HIGH",
                "LOW",
                "HIGH",
                70,
                json.dumps(prescreening),
            ),
        )
        if director_is_pep:
            conn.execute(
                """
                INSERT INTO directors (application_id, first_name, last_name, full_name, nationality, is_pep)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (app_id, "Priya", "Raman", "Priya Raman", "Mauritius", "Yes"),
            )
        insert_verified_required_documents(conn, app_id)
        conn.commit()
        conn.close()

        def datetime_enhanced_summary(_db, _application_id):
            ts = datetime(2026, 5, 18, 12, 15, tzinfo=timezone.utc)
            item = {
                "id": 9001,
                "trigger_key": "declared_pep_present",
                "trigger_label": "Declared PEP",
                "trigger_category": "pep",
                "requirement_key": "pep_senior_review",
                "requirement_label": "PEP senior review",
                "requirement_type": "review_task",
                "subject_scope": "application",
                "mandatory": True,
                "blocking_approval": True,
                "status": "accepted",
                "reviewed_at": ts,
                "reviewed_by": "admin001",
            }
            return {
                "triggered": True,
                "total_requirements": 1,
                "by_trigger": [{"trigger_key": "declared_pep_present", "requirements": [item]}],
                "accepted": [item],
                "requested": [],
                "submitted": [],
                "rejected": [],
                "waived": [],
                "outstanding": [],
                "mandatory_outstanding_count": 0,
                "blocking_outstanding_count": 0,
                "overall_status": "complete",
                "warnings": [],
                "closed_at": ts,
            }

        monkeypatch.setattr(server, "build_enhanced_review_memo_summary", datetime_enhanced_summary)

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/applications/{app_id}/memo",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        accepted = body["metadata"]["enhanced_review_summary"]["accepted"][0]
        assert accepted["reviewed_at"].startswith("2026-05-18T12:15:00")

        conn = get_db()
        row = conn.execute(
            "SELECT memo_data FROM compliance_memos WHERE application_id = ? ORDER BY id DESC LIMIT 1",
            (app_id,),
        ).fetchone()
        conn.close()
        stored = json.loads(row["memo_data"])
        assert stored["metadata"]["enhanced_review_summary"]["closed_at"].startswith("2026-05-18T12:15:00")

    def test_decision_lock_timeout_returns_controlled_409_and_closes_db(self, api_server, monkeypatch):
        """FOR UPDATE lock timeouts must not leak a transaction or surface as a 500."""
        from auth import create_token
        import server

        class FakeLockTimeout(Exception):
            pgcode = "55P03"

        class LockingDb:
            is_postgres = True

            def __init__(self):
                self.closed = False
                self.rolled_back = False
                self.committed = False
                self.audit_written = False

            def execute(self, sql, params=()):
                if "FOR UPDATE" in sql:
                    raise FakeLockTimeout("canceling statement due to lock timeout")
                if "INSERT INTO audit_log" in sql:
                    self.audit_written = True
                    return self
                return self

            def fetchone(self):
                return None

            def rollback(self):
                self.rolled_back = True

            def commit(self):
                self.committed = True

            def close(self):
                self.closed = True

        fake_db = LockingDb()
        monkeypatch.setattr(server, "get_db", lambda: fake_db)

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/applications/locked-app/decision",
            json={
                "decision": "approve",
                "decision_reason": "Testing controlled lock timeout response.",
                "officer_signoff": {
                    "acknowledged": True,
                    "scope": "decision",
                    "source_context": "ai_advisory",
                },
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )

        assert resp.status_code == 409, resp.text
        assert "temporarily locked" in resp.json().get("error", "")
        assert fake_db.rolled_back is True
        assert fake_db.audit_written is True
        assert fake_db.committed is True
        assert fake_db.closed is True

    def test_enhanced_requirement_approval_block_is_audited(self, api_server):
        """Enhanced requirement approval blockers must emit their focused audit event."""
        from auth import create_token
        from db import get_db
        from tests.conftest import insert_verified_required_documents

        app_id = "app_step7_enhanced_block"
        app_ref = "ARF-2026-STEP7-BLOCK"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target IN (?, ?)", (app_ref, f"application:{app_ref}"))
        conn.execute("DELETE FROM compliance_memos WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM application_enhanced_requirements WHERE application_id = ?", (app_id,))
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
            "Step 7 Enhanced Block Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "submitted_to_compliance",
            "MEDIUM",
            45,
            self._live_prescreening(),
        ))
        self._insert_approved_memo(conn, app_id)
        insert_verified_required_documents(conn, app_id)
        self._insert_enhanced_requirement(conn, app_id, status="generated", mandatory=1, blocking_approval=1)
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/applications/{app_id}/decision",
            json={
                "decision": "approve",
                "decision_reason": "Testing enhanced requirement approval block audit.",
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
        assert "Enhanced Review requirements remain unresolved" in resp.json().get("error", "")

        conn = get_db()
        row = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE target = ? AND action = 'approval.blocked.enhanced_requirements'
            ORDER BY id DESC LIMIT 1
            """,
            (f"application:{app_ref}",),
        ).fetchone()
        conn.close()

        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["event"] == "approval.blocked.enhanced_requirements"
        assert detail["application_id"] == app_id
        assert detail["unresolved_count"] == 1
        assert detail["mandatory_unresolved_count"] == 1
        assert detail["blocking_unresolved_count"] == 1

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

    def test_co_pre_approval_decision_is_role_blocked_with_specific_403(self, api_server):
        from auth import create_token
        from db import get_db

        app_id = "app_phase1b_preapproval_co_blocked"
        app_ref = "ARF-2026-PHASE1B-PRE-CO"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                status, risk_level, risk_score, prescreening_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id, app_ref, "phase1b_client", "Phase 1B Pre Blocked Ltd",
            "Mauritius", "Technology", "SME", "pre_approval_review", "HIGH", 72,
            self._live_prescreening(),
        ))
        conn.commit()
        conn.close()

        token = create_token("co001", "co", "Test CO", "officer")
        resp = http_requests.post(
            f"{api_server}/api/applications/{app_id}/pre-approval-decision",
            json={"decision": "PRE_APPROVE", "notes": "CO should not be allowed to pre-approve."},
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 403
        assert "Pre-approval blocked" in resp.text
        assert "Onboarding Officer" in resp.text

    def test_reassignment_empty_reason_returns_400_and_does_not_persist(self, api_server):
        from auth import create_token
        from db import get_db

        app_id = "app_phase1b_assign_empty_reason"
        app_ref = "ARF-2026-PHASE1B-ASG-EMPTY"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                status, risk_level, risk_score, assigned_to, prescreening_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id, app_ref, "phase1b_client", "Phase 1B Assign Empty Reason Ltd",
            "Mauritius", "Technology", "SME", "pre_approval_review", "HIGH", 72,
            "admin001", self._live_prescreening(),
        ))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.patch(
            f"{api_server}/api/applications/{app_id}",
            json={"assigned_to": "co001", "reassignment_reason": ""},
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 400
        assert "reassignment_reason_required" in resp.text

        conn = get_db()
        app_row = conn.execute("SELECT assigned_to FROM applications WHERE id = ?", (app_id,)).fetchone()
        audit_count = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE target = ? AND action = 'Reassign'",
            (app_ref,),
        ).fetchone()["c"]
        conn.close()

        assert app_row["assigned_to"] == "admin001"
        assert audit_count == 0

    def test_reassignment_whitespace_reason_returns_400_and_does_not_persist(self, api_server):
        from auth import create_token
        from db import get_db

        app_id = "app_phase1b_assign_whitespace_reason"
        app_ref = "ARF-2026-PHASE1B-ASG-SPACE"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                status, risk_level, risk_score, assigned_to, prescreening_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id, app_ref, "phase1b_client", "Phase 1B Assign Space Reason Ltd",
            "Mauritius", "Technology", "SME", "pre_approval_review", "HIGH", 72,
            "admin001", self._live_prescreening(),
        ))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.patch(
            f"{api_server}/api/applications/{app_id}",
            json={"assigned_to": "co001", "reassignment_reason": "   \n\t  "},
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 400
        assert "reassignment_reason_required" in resp.text

        conn = get_db()
        app_row = conn.execute("SELECT assigned_to FROM applications WHERE id = ?", (app_id,)).fetchone()
        audit_count = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE target = ? AND action = 'Reassign'",
            (app_ref,),
        ).fetchone()["c"]
        conn.close()

        assert app_row["assigned_to"] == "admin001"
        assert audit_count == 0

    def test_admin_can_assign_preapproval_review_application_and_audit_it(self, api_server):
        from auth import create_token
        from db import get_db

        app_id = "app_phase1b_assign_preapproval_admin"
        app_ref = "ARF-2026-PHASE1B-ASG-ADMIN"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                status, risk_level, risk_score, assigned_to, prescreening_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id, app_ref, "phase1b_client", "Phase 1B Assign Admin Ltd",
            "Mauritius", "Technology", "SME", "pre_approval_review", "HIGH", 72,
            "admin001", self._live_prescreening(),
        ))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.patch(
            f"{api_server}/api/applications/{app_id}",
            json={"assigned_to": "co001", "reassignment_reason": "Workload balancing for urgent review"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200, resp.text

        conn = get_db()
        app_row = conn.execute(
            "SELECT assigned_to FROM applications WHERE id = ?",
            (app_id,),
        ).fetchone()
        audit_row = conn.execute(
            """
            SELECT action, detail, before_state, after_state FROM audit_log
            WHERE target = ?
            ORDER BY id DESC LIMIT 1
            """,
            (app_ref,),
        ).fetchone()
        conn.close()

        assert app_row["assigned_to"] == "co001"
        assert audit_row is not None
        assert audit_row["action"] == "Reassign"
        detail = json.loads(audit_row["detail"])
        before_state = json.loads(audit_row["before_state"])
        after_state = json.loads(audit_row["after_state"])
        assert detail["event"] == "application_reassigned"
        assert detail["application_id"] == app_id
        assert detail["application_ref"] == app_ref
        assert detail["previous_assignee_id"] == "admin001"
        assert detail["new_assignee_id"] == "co001"
        assert detail["actor_user_id"] == "admin001"
        assert detail["actor_email"]
        assert detail["actor_role"] == "admin"
        assert detail["reassignment_reason"] == "Workload balancing for urgent review"
        assert detail["source_surface"] == "backoffice/application_review"
        assert detail["timestamp"]
        assert before_state["assigned_to"] == "admin001"
        assert after_state["assigned_to"] == "co001"

    def test_co_assignment_is_role_blocked_with_specific_403(self, api_server):
        from auth import create_token
        from db import get_db

        app_id = "app_phase1b_assign_preapproval_co"
        app_ref = "ARF-2026-PHASE1B-ASG-CO"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, country, sector, entity_type,
                status, risk_level, risk_score, assigned_to, prescreening_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id, app_ref, "phase1b_client", "Phase 1B Assign CO Ltd",
            "Mauritius", "Technology", "SME", "pre_approval_review", "HIGH", 72,
            None, self._live_prescreening(),
        ))
        conn.commit()
        conn.close()

        token = create_token("co001", "co", "Test CO", "officer")
        resp = http_requests.patch(
            f"{api_server}/api/applications/{app_id}",
            json={"assigned_to": "admin001"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 403
        assert "Assignment blocked" in resp.text
        assert "Onboarding Officer" in resp.text

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
                "evidence_reference": "Provider case CA-AUDIT-001 reviewed against registry evidence.",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        assert resp.json()["review"]["canonical_disposition"] == "false_positive_cleared"
        assert resp.json()["review"]["review_evidence_reference"] == (
            "Provider case CA-AUDIT-001 reviewed against registry evidence."
        )

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
        assert review["disposition_code"] == "false_positive_cleared"
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

        no_match_without_evidence = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_id,
                "subject_type": "entity",
                "subject_name": "Phase 1C Required Fields Ltd",
                "disposition": "no_match",
                "rationale": "Officer reviewed the provider hit and confirmed it is not the same entity.",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert no_match_without_evidence.status_code == 200
        assert no_match_without_evidence.json()["review"]["canonical_disposition"] == "false_positive_cleared"

        conn = get_db()
        review = conn.execute(
            "SELECT id, disposition, disposition_code FROM screening_reviews WHERE application_id = ?",
            (app_id,),
        ).fetchone()
        row = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE target = ? AND action = 'Screening Review'
            ORDER BY id DESC LIMIT 1
            """,
            (app_ref,),
        ).fetchone()
        conn.close()

        assert review is not None
        assert review["disposition"] == "cleared"
        assert review["disposition_code"] == "false_positive_cleared"
        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["canonical_disposition"] == "false_positive_cleared"
        assert detail["evidence_reference_provided"] is False
        assert detail["evidence_file_uploaded"] is False

    def test_no_match_clearance_allows_empty_evidence_reference(self, api_server):
        """No Match maps to false-positive/cleared semantics and may omit evidence text."""
        from auth import create_token
        from db import get_db

        app_id = "app_screening_fp_optional_evidence"
        app_ref = "ARF-2026-SCREEN-FP-EVIDENCE"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM screening_reviews WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, status)
            VALUES (?, ?, ?, ?, ?)
        """, (app_id, app_ref, "screening_fp_client", "Screening FP Evidence Ltd", "in_review"))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_ref,
                "subject_type": "entity",
                "subject_name": "Screening FP Evidence Ltd",
                "disposition": "no_match",
                "rationale": "Officer confirmed this provider hit relates to a different entity.",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["review"]["review_disposition"] == "cleared"
        assert body["review"]["canonical_disposition"] == "false_positive_cleared"

    def test_analyst_cannot_clear_screening_match(self, api_server):
        """Analyst role may not formally clear a completed_match false positive."""
        from auth import create_token
        from db import get_db

        app_id = "app_screening_analyst_clear_forbidden"
        app_ref = "ARF-2026-SCREEN-ANALYST-FORBIDDEN"
        prescreening = {
            "screening_report": {
                "screening_mode": "live",
                "company_screening": {
                    "found": True,
                    "sanctions": {
                        "matched": True,
                        "results": [{"name": "Screening FP Evidence Ltd", "is_sanctioned": True}],
                        "source": "sumsub",
                        "api_status": "live",
                    },
                },
                "director_screenings": [],
                "ubo_screenings": [],
            }
        }
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM screening_reviews WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, status, prescreening_data)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            app_id,
            app_ref,
            "screening_analyst_client",
            "Analyst Forbidden Ltd",
            "in_review",
            json.dumps(prescreening),
        ))
        conn.commit()
        conn.close()

        token = create_token("analyst_screening_fp", "analyst", "Screening Analyst", "officer")
        resp = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_ref,
                "subject_type": "entity",
                "subject_name": "Analyst Forbidden Ltd",
                "disposition": "false_positive_cleared",
                "rationale": "Officer confirmed this provider hit relates to a different entity after detailed review.",
                "evidence_reference": "Provider case CA-ANALYST-FORBIDDEN-001.",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )

        assert resp.status_code == 403

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
            "evidence_reference": "Director passport and provider case CA-SENSITIVE-001.",
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

        co_token = create_token("co_phase1c_second", "co", "Second CO", "officer")
        co_payload = dict(first_payload)
        co_payload["disposition_code"] = "identity_mismatch"
        co_payload["rationale"] = "A CO cannot satisfy the senior four-eyes second review requirement."
        co_payload["evidence_reference"] = "Second-review identity pack and CA-SENSITIVE-001."
        co_second = http_requests.post(
            f"{api_server}/api/screening/review",
            json=co_payload,
            headers={"Authorization": f"Bearer {co_token}"},
            timeout=3,
        )
        assert co_second.status_code == 403
        assert "Senior Compliance Officer" in co_second.json()["error"]

        second_token = create_token("sco_phase1c", "sco", "Second Officer", "officer")
        second_payload = dict(first_payload)
        second_payload["disposition_code"] = "identity_mismatch"
        second_payload["rationale"] = "Independent review confirms the provider hit is not the same individual."
        second_payload["evidence_reference"] = "Second-review identity pack and CA-SENSITIVE-001."
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

        assert review["disposition_code"] == "false_positive_cleared"
        assert review["rationale"] == first_payload["rationale"]
        assert review["second_disposition_code"] == "false_positive_cleared"
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
        assert body["review"]["review_actionable"] is False

        queue = http_requests.get(
            f"{api_server}/api/screening/queue",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert queue.status_code == 200
        row = next(r for r in queue.json()["rows"] if r["application_ref"] == app_ref and r["subject_name"] == subject_name)
        assert row["review_required"] is False
        assert row["review_actionable"] is False
        assert row["status_key"] == "escalated"
        assert row["status_label"] == "Escalated"

        retry = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_ref,
                "subject_type": "director",
                "subject_name": subject_name,
                "disposition": "escalated",
                "disposition_code": "potential_sanctions_match",
                "rationale": "Retry should be rejected because the screening item is already reviewed.",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert retry.status_code == 409

    def test_screening_follow_up_marks_queue_row_non_actionable(self, api_server):
        """Follow-up dispositions should clear queue actionability while preserving workflow blockers elsewhere."""
        from auth import create_token
        from db import get_db

        app_id = "app_phase1c_follow_up"
        app_ref = "ARF-2026-PHASE1C-FOLLOW-UP"
        subject_name = "Follow Up Director"
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
                        "results": [{"name": subject_name, "is_sanctioned": False, "is_pep": True}],
                        "api_status": "live",
                        "screened_at": "2026-04-30T10:00:00Z",
                    },
                    "undeclared_pep": True,
                }],
                "ubo_screenings": [],
                "ip_geolocation": {"risk_level": "LOW"},
                "overall_flags": ["Director PEP match"],
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
            app_id, app_ref, "phase1c_client", "Phase 1C Follow Up Ltd",
            "Mauritius", "Technology", "SME", "in_review", json.dumps(prescreening),
        ))
        conn.execute(
            "INSERT INTO directors (application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?)",
            (app_id, subject_name, "Mauritius", "No"),
        )
        conn.commit()
        conn.close()

        token = create_token("analyst_phase1c", "analyst", "Analyst Reviewer", "officer")
        resp = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_ref,
                "subject_type": "director",
                "subject_name": subject_name,
                "disposition": "follow_up_required",
                "disposition_code": "needs_more_information",
                "rationale": "Additional customer clarification is required before a final screening disposition can be made.",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        assert resp.json()["review"]["review_actionable"] is False

        queue = http_requests.get(
            f"{api_server}/api/screening/queue",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert queue.status_code == 200
        row = next(r for r in queue.json()["rows"] if r["application_ref"] == app_ref and r["subject_name"] == subject_name)
        assert row["review_required"] is False
        assert row["review_actionable"] is False
        assert row["status_key"] == "follow_up_required"
        assert row["status_label"] == "Follow-up Required"

    def test_screening_review_subject_must_belong_to_application(self, api_server):
        """Disposition requests must fail closed when the subject is not part of the application."""
        from auth import create_token
        from db import get_db

        app_id = "app_phase1c_subject_mismatch"
        app_ref = "ARF-2026-PHASE1C-SUBJECT-MISMATCH"
        conn = get_db()
        conn.execute("DELETE FROM screening_reviews WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, status)
            VALUES (?, ?, ?, ?, ?)
        """, (app_id, app_ref, "phase1c_client", "Subject Mismatch Ltd", "in_review"))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_ref,
                "subject_type": "entity",
                "subject_name": "Different Company Name Ltd",
                "disposition": "escalated",
                "disposition_code": "material_concern",
                "rationale": "This should fail because the subject is not part of the application.",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["error"].lower()

    def test_screening_review_audit_captures_source_surface(self, api_server):
        """Inline application-detail reviews should stamp the audit source surface."""
        from auth import create_token
        from db import get_db

        app_id = "app_phase1c_screening_source_surface"
        app_ref = "ARF-2026-PHASE1C-SOURCE-SURFACE"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM screening_reviews WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, status)
            VALUES (?, ?, ?, ?, ?)
        """, (app_id, app_ref, "phase1c_client", "Source Surface Ltd", "in_review"))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_ref,
                "subject_type": "entity",
                "subject_name": "Source Surface Ltd",
                "disposition": "escalated",
                "disposition_code": "material_concern",
                "rationale": "Officer escalated the screening match directly from application detail.",
                "source_surface": "application_detail_screening_tab",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200

        conn = get_db()
        row = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE target = ? AND action = 'Screening Review'
            ORDER BY id DESC LIMIT 1
            """,
            (app_ref,),
        ).fetchone()
        conn.close()

        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["source_surface"] == "application_detail_screening_tab"

    def test_screening_review_links_evidence_document_metadata(self, api_server):
        """Screening review can link an existing application document as uploaded evidence."""
        from auth import create_token
        from db import get_db

        app_id = "app_phase1c_screening_evidence_doc"
        app_ref = "ARF-2026-PHASE1C-EVIDENCE-DOC"
        doc_id = "doc_screening_evidence_001"
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target = ?", (app_ref,))
        conn.execute("DELETE FROM screening_reviews WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM documents WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, status)
            VALUES (?, ?, ?, ?, ?)
        """, (app_id, app_ref, "phase1c_client", "Evidence Document Ltd", "in_review"))
        conn.execute("""
            INSERT INTO documents
            (id, application_id, doc_type, doc_name, file_path, file_size, mime_type, verification_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (doc_id, app_id, "supporting_document", "screening-evidence.pdf", "/tmp/screening-evidence.pdf", 128, "application/pdf", "pending"))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_ref,
                "subject_type": "entity",
                "subject_name": "Evidence Document Ltd",
                "disposition": "match",
                "rationale": "Officer confirmed this provider hit appears to relate to the subject.",
                "evidence_document_id": doc_id,
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["review"]["canonical_disposition"] == "confirmed_match"
        assert body["review"]["review_evidence_documents"][0]["id"] == doc_id

        conn = get_db()
        row = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE target = ? AND action = 'Screening Review'
            ORDER BY id DESC LIMIT 1
            """,
            (app_ref,),
        ).fetchone()
        conn.close()
        detail = json.loads(row["detail"])
        assert detail["evidence_file_uploaded"] is True
        assert detail["evidence_document"]["id"] == doc_id

    def test_screening_escalated_to_edd_routes_application(self, api_server, monkeypatch):
        """Canonical escalated_to_edd disposition must actuate/preserve EDD workflow."""
        from auth import create_token
        from db import get_db
        import routing_actuator

        real_apply_routing_decision = routing_actuator.apply_routing_decision

        def apply_routing_with_postgres_timestamp_payload(**kwargs):
            result = real_apply_routing_decision(**kwargs)
            result["postgres_timestamp_regression"] = datetime.now(timezone.utc)
            return result

        monkeypatch.setattr(
            routing_actuator,
            "apply_routing_decision",
            apply_routing_with_postgres_timestamp_payload,
        )

        app_id = "app_screening_escalated_to_edd"
        app_ref = "ARF-2026-SCREEN-ESCALATE-EDD"
        prescreening = {
            "screening_report": {
                "screening_mode": "live",
                "company_screening": {
                    "found": True,
                    "sanctions": {
                        "matched": True,
                        "results": [{"name": "Material Match Ltd", "is_sanctioned": True}],
                        "source": "sumsub",
                        "api_status": "live",
                    },
                },
                "director_screenings": [],
                "ubo_screenings": [],
                "total_hits": 1,
            }
        }
        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target IN (?, ?)", (app_ref, f"application:{app_ref}"))
        conn.execute("DELETE FROM edd_cases WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM screening_reviews WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type,
             status, risk_level, risk_score, prescreening_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            app_id,
            app_ref,
            "screening_edd_client",
            "Screening EDD Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "compliance_review",
            "MEDIUM",
            45,
            json.dumps(prescreening),
        ))
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_ref,
                "subject_type": "entity",
                "subject_name": "Screening EDD Ltd",
                "disposition": "escalated_to_edd",
                "rationale": "Officer escalated this material provider match to EDD for enhanced review.",
                "evidence_reference": "Provider case CA-ESC-EDD-001.",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["review"]["canonical_disposition"] == "escalated_to_edd"
        assert body["routing_outcome"]["route"] == "edd"
        assert isinstance(body["routing_outcome"]["postgres_timestamp_regression"], str)

        conn = get_db()
        app = conn.execute(
            "SELECT status, onboarding_lane FROM applications WHERE id = ?",
            (app_id,),
        ).fetchone()
        edd = conn.execute(
            "SELECT id, stage FROM edd_cases WHERE application_id = ?",
            (app_id,),
        ).fetchone()
        audit = conn.execute(
            """
            SELECT id FROM audit_log
            WHERE target = ? AND action = 'edd_routing.actuated'
            ORDER BY id DESC LIMIT 1
            """,
            (f"application:{app_ref}",),
        ).fetchone()
        conn.close()

        assert app["onboarding_lane"] == "EDD"
        assert app["status"] == "edd_required"
        assert edd is not None
        assert audit is not None

    @pytest.mark.parametrize(
        "disposition_code",
        ["true_match", "material_concern", "needs_more_information"],
    )
    def test_blocking_screening_dispositions_normalize_to_edd_status_and_lane(
        self,
        api_server,
        disposition_code,
    ):
        """Blocking screening dispositions must not leave Standard Review + edd_required drift."""
        from auth import create_token
        from db import get_db

        suffix = disposition_code.replace("_", "-")
        app_id = f"app_screening_workflow_{disposition_code}"
        app_ref = f"ARF-2026-SCREEN-WORKFLOW-{suffix}".upper()
        company_name = f"Screening Workflow {suffix} Ltd"
        conn = get_db()
        self._insert_screening_workflow_app(
            conn,
            app_id,
            app_ref,
            company_name=company_name,
            status="in_review",
            lane="Standard Review",
            risk_level="LOW",
            final_risk_level="LOW",
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_ref,
                "subject_type": "entity",
                "subject_name": company_name,
                "disposition": disposition_code,
                "rationale": "Officer disposition keeps this live provider match unresolved for controlled workflow testing.",
                "evidence_reference": f"Provider case CA-{suffix}-001.",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["review"]["canonical_disposition"] == disposition_code
        assert body["workflow_normalization"]["new_status"] == "edd_required"
        assert body["workflow_normalization"]["new_lane"] == "EDD"

        conn = get_db()
        app = conn.execute(
            "SELECT status, onboarding_lane, final_risk_level FROM applications WHERE id = ?",
            (app_id,),
        ).fetchone()
        edd = conn.execute(
            "SELECT id, stage FROM edd_cases WHERE application_id = ?",
            (app_id,),
        ).fetchone()
        audit = conn.execute(
            """
            SELECT detail, before_state, after_state FROM audit_log
            WHERE target = ? AND action = 'screening.status_lane_normalized'
            ORDER BY id DESC LIMIT 1
            """,
            (f"application:{app_ref}",),
        ).fetchone()
        conn.close()

        assert app["status"] == "edd_required"
        assert app["onboarding_lane"] == "EDD"
        expected_final_risk = "MEDIUM" if disposition_code == "needs_more_information" else "HIGH"
        assert app["final_risk_level"] == expected_final_risk
        assert edd is not None
        assert audit is not None
        detail = json.loads(audit["detail"])
        assert detail["disposition_code"] == disposition_code
        assert detail["previous_status"] == "in_review"
        assert detail["new_status"] == "edd_required"
        assert json.loads(audit["after_state"])["onboarding_lane"] == "EDD"

    def test_false_positive_clearance_normalizes_clean_case_out_of_edd_required(self, api_server):
        """A complete evidenced false-positive clearance should clear stale EDD workflow state."""
        from auth import create_token
        from db import get_db

        app_id = "app_screening_workflow_false_positive"
        app_ref = "ARF-2026-SCREEN-WORKFLOW-FP"
        company_name = "Screening Workflow False Positive Ltd"
        conn = get_db()
        self._insert_screening_workflow_app(
            conn,
            app_id,
            app_ref,
            company_name=company_name,
            status="edd_required",
            lane="EDD",
            risk_level="MEDIUM",
            final_risk_level="MEDIUM",
        )
        conn.commit()
        conn.close()

        first_token = create_token("admin001", "admin", "Test Admin", "officer")
        first_payload = {
            "application_id": app_ref,
            "subject_type": "entity",
            "subject_name": company_name,
            "disposition": "false_positive_cleared",
            "rationale": "Officer matched provider evidence against registry records and confirmed a different entity.",
            "evidence_reference": "Registry extract and provider case CA-FP-001.",
        }
        first = http_requests.post(
            f"{api_server}/api/screening/review",
            json=first_payload,
            headers={"Authorization": f"Bearer {first_token}"},
            timeout=5,
        )
        assert first.status_code == 202, first.text

        second_token = create_token("sco_phase1c", "sco", "Second Officer", "officer")
        second_payload = dict(first_payload)
        second_payload["rationale"] = "Independent second review confirmed the provider hit belongs to another company."
        second_payload["evidence_reference"] = "Second-review registry pack and provider case CA-FP-001."
        second = http_requests.post(
            f"{api_server}/api/screening/review",
            json=second_payload,
            headers={"Authorization": f"Bearer {second_token}"},
            timeout=5,
        )

        assert second.status_code == 200, second.text
        body = second.json()
        assert body["review"]["canonical_disposition"] == "false_positive_cleared"
        assert body["workflow_normalization"]["new_status"] == "in_review"
        assert body["workflow_normalization"]["new_lane"] != "EDD"

        conn = get_db()
        app = conn.execute(
            "SELECT status, onboarding_lane, final_risk_level FROM applications WHERE id = ?",
            (app_id,),
        ).fetchone()
        review = conn.execute(
            """
            SELECT disposition_code, second_reviewer_id
            FROM screening_reviews WHERE application_id = ?
            """,
            (app_id,),
        ).fetchone()
        audit = conn.execute(
            """
            SELECT detail, before_state, after_state FROM audit_log
            WHERE target = ? AND action = 'screening.status_lane_normalized'
            ORDER BY id DESC LIMIT 1
            """,
            (f"application:{app_ref}",),
        ).fetchone()
        conn.close()

        assert app["status"] == "in_review"
        assert app["onboarding_lane"] != "EDD"
        assert app["final_risk_level"] == "LOW"
        assert review["disposition_code"] == "false_positive_cleared"
        assert review["second_reviewer_id"] == "sco_phase1c"
        assert audit is not None
        detail = json.loads(audit["detail"])
        assert detail["disposition_code"] == "false_positive_cleared"
        assert detail["previous_status"] == "edd_required"
        assert detail["new_status"] == "in_review"

    def test_false_positive_clearance_does_not_advance_to_kyc_when_another_floor_remains(self, api_server):
        """False-positive clearance must not skip pre-approval while another EDD floor remains."""
        from auth import create_token
        from db import get_db

        app_id = "app_screening_workflow_fp_other_floor"
        app_ref = "ARF-2026-SCREEN-WORKFLOW-FP-FLOOR"
        company_name = "Screening Workflow False Positive Floor Ltd"
        conn = get_db()
        self._insert_screening_workflow_app(
            conn,
            app_id,
            app_ref,
            company_name=company_name,
            status="pre_approval_review",
            lane="EDD",
            risk_level="HIGH",
            final_risk_level="HIGH",
        )
        conn.execute(
            """
            UPDATE applications
            SET sector = ?,
                risk_escalations = ?,
                elevation_reason_text = ?
            WHERE id = ?
            """,
            (
                "Crypto VASP exchange",
                json.dumps(["floor_rule_high_risk_sector", "material_screening_disposition_floor"]),
                "High-risk sector floor and unresolved screening match require EDD.",
                app_id,
            ),
        )
        conn.commit()
        conn.close()

        first_token = create_token("admin001", "admin", "Test Admin", "officer")
        first_payload = {
            "application_id": app_ref,
            "subject_type": "entity",
            "subject_name": company_name,
            "disposition": "false_positive_cleared",
            "rationale": "Officer matched provider evidence against registry records and confirmed a different entity.",
            "evidence_reference": "Registry extract and provider case CA-FP-FLOOR-001.",
        }
        first = http_requests.post(
            f"{api_server}/api/screening/review",
            json=first_payload,
            headers={"Authorization": f"Bearer {first_token}"},
            timeout=5,
        )
        assert first.status_code == 202, first.text

        second_token = create_token("sco_phase1c_floor", "sco", "Second Officer", "officer")
        second_payload = dict(first_payload)
        second_payload["rationale"] = "Independent second review confirmed the provider hit belongs to another company."
        second_payload["evidence_reference"] = "Second-review registry pack and provider case CA-FP-FLOOR-001."
        second = http_requests.post(
            f"{api_server}/api/screening/review",
            json=second_payload,
            headers={"Authorization": f"Bearer {second_token}"},
            timeout=5,
        )

        assert second.status_code == 200, second.text
        body = second.json()
        assert body["review"]["canonical_disposition"] == "false_positive_cleared"
        assert body["risk_recomputed"] is True
        assert "workflow_normalization" not in body

        conn = get_db()
        app = conn.execute(
            """
            SELECT status, onboarding_lane, base_risk_level, final_risk_level,
                   risk_escalations, elevation_reason_text
            FROM applications WHERE id = ?
            """,
            (app_id,),
        ).fetchone()
        review = conn.execute(
            """
            SELECT disposition_code, second_reviewer_id
            FROM screening_reviews WHERE application_id = ?
            """,
            (app_id,),
        ).fetchone()
        conn.close()

        escalations = set(json.loads(app["risk_escalations"] or "[]"))
        assert app["status"] != "kyc_documents"
        assert app["status"] in {"pre_approval_review", "edd_required"}
        assert app["onboarding_lane"] == "EDD"
        assert app["base_risk_level"] == "LOW"
        assert app["final_risk_level"] == "HIGH"
        assert "floor_rule_high_risk_sector" in escalations
        assert "material_screening_disposition_floor" not in escalations
        assert "High-risk sector floor" in app["elevation_reason_text"]
        assert review["disposition_code"] == "false_positive_cleared"
        assert review["second_reviewer_id"] == "sco_phase1c_floor"

    def test_provider_only_pep_false_positive_clearance_recalculates_to_base_low(self, api_server):
        """Provider-only PEP clearance must remove the temporary PEP/EDD floor."""
        from auth import create_token
        from db import get_db
        from security_hardening import classify_approval_route, collect_approval_gate_blockers

        app_id = "app_provider_only_pep_false_positive_recalc"
        app_ref = "ARF-2026-PROVIDER-PEP-FP-LOW"
        company_name = "Provider Only PEP False Positive Ltd"
        director_id = "dir_provider_pep_fp"
        director_name = "Provider Only PEP Director"
        ubo_id = "ubo_provider_pep_clean"
        ubo_name = "Clean Beneficial Owner"
        screened_at = "2026-06-20T10:00:00Z"
        provider_case_id = "CA-PEP-FP-001"
        provider_risk_id = "CA-RISK-PEP-FP-001"
        pep_declaration = {
            "declared_pep": False,
            "client_declared_pep": False,
            "officer_verified_pep": False,
            "pep_status": "declared_no",
        }
        prescreening = {
            "operating_countries": ["United Kingdom"],
            "target_markets": ["United Kingdom"],
            "source_of_wealth": "Operating revenue",
            "source_of_funds": "Client subscription revenue",
            "monthly_volume": "0-50000",
            "cross_border": False,
            "screening_valid_until": "2026-09-20T10:00:00Z",
            "screening_report": {
                "provider": "complyadvantage",
                "screening_provider": "complyadvantage",
                "screening_mode": "live",
                "screened_at": screened_at,
                "total_hits": 1,
                "any_pep_hits": True,
                "any_sanctions_hits": False,
                "has_adverse_media_hit": False,
                "company_screening": {
                    "company_name": company_name,
                    "provider": "complyadvantage",
                    "source": "complyadvantage",
                    "api_status": "live",
                    "matched": False,
                    "results": [],
                },
                "director_screenings": [
                    {
                        "source_id": director_id,
                        "person_name": director_name,
                        "declared_pep": False,
                        "provider_detected_pep": True,
                        "has_pep_hit": True,
                        "has_sanctions_hit": False,
                        "has_adverse_media_hit": False,
                        "undeclared_pep": True,
                        "screening": {
                            "provider": "complyadvantage",
                            "source": "complyadvantage",
                            "api_status": "live",
                            "screened_at": screened_at,
                            "matched": True,
                            "results": [
                                {
                                    "id": "pep-provider-hit-1",
                                    "name": director_name,
                                    "is_pep": True,
                                    "is_sanctioned": False,
                                    "is_adverse_media": False,
                                    "provider": "complyadvantage",
                                    "provider_case_identifier": provider_case_id,
                                    "provider_risk_identifier": provider_risk_id,
                                    "match_categories": ["pep"],
                                }
                            ],
                        },
                    }
                ],
                "ubo_screenings": [
                    {
                        "source_id": ubo_id,
                        "person_name": ubo_name,
                        "declared_pep": False,
                        "provider_detected_pep": False,
                        "has_pep_hit": False,
                        "has_sanctions_hit": False,
                        "has_adverse_media_hit": False,
                        "screening": {
                            "provider": "complyadvantage",
                            "source": "complyadvantage",
                            "api_status": "live",
                            "screened_at": screened_at,
                            "matched": False,
                            "results": [],
                        },
                    }
                ],
                "ip_geolocation": {"source": "ipapi", "api_status": "live", "risk_level": "LOW"},
            },
        }

        conn = get_db()
        conn.execute("DELETE FROM audit_log WHERE target IN (?, ?)", (app_ref, f"application:{app_ref}"))
        conn.execute("DELETE FROM edd_cases WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM screening_reviews WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM directors WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM ubos WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.execute(
            """
            INSERT INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type,
                 ownership_structure, status, onboarding_lane, risk_level,
                 base_risk_level, final_risk_level, risk_score, risk_dimensions,
                 risk_escalations, elevation_reason_text, prescreening_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                app_id,
                app_ref,
                "client_provider_pep_fp",
                company_name,
                "United Kingdom",
                "Technology",
                "Listed Company",
                "Simple - direct identifiable UBOs",
                "pre_approval_review",
                "EDD",
                "HIGH",
                "LOW",
                "HIGH",
                55.0,
                json.dumps({"d1": 1.0, "d2": 1.0, "d3": 1.0, "d4": 1.0, "d5": 1.0}),
                json.dumps(["floor_rule_edd_routing", "material_screening_disposition_floor"]),
                "EDD routing floor: deterministic routing required EDD (material_screening_concern)",
                json.dumps(prescreening),
            ),
        )
        conn.execute(
            """
            INSERT INTO directors
                (id, application_id, person_key, first_name, last_name, full_name,
                 nationality, is_pep, pep_declaration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                director_id,
                app_id,
                "director-provider-pep-fp",
                "Provider",
                "Director",
                director_name,
                "United Kingdom",
                "No",
                json.dumps(pep_declaration),
            ),
        )
        conn.execute(
            """
            INSERT INTO ubos
                (id, application_id, person_key, first_name, last_name, full_name,
                 nationality, ownership_pct, is_pep, pep_declaration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ubo_id,
                app_id,
                "ubo-clean-owner",
                "Clean",
                "Owner",
                ubo_name,
                "United Kingdom",
                100.0,
                "No",
                json.dumps({
                    "declared_pep": False,
                    "client_declared_pep": False,
                    "officer_verified_pep": False,
                    "pep_status": "declared_no",
                }),
            ),
        )
        conn.commit()
        before_app = dict(conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())
        before_route = classify_approval_route(before_app, conn)
        before_blockers = collect_approval_gate_blockers(before_app, conn)
        conn.close()

        admin_token = create_token("admin001", "admin", "Test Admin", "officer")
        detail_before = http_requests.get(
            f"{api_server}/api/applications/{app_ref}",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=5,
        )
        assert detail_before.status_code == 200, detail_before.text
        before_body = detail_before.json()
        before_truth = before_body["screening_adverse_truth_summary"]

        assert before_body["status"] == "pre_approval_review"
        assert before_body["onboarding_lane"] == "EDD"
        assert before_body["final_risk_level"] == "HIGH"
        assert before_body["directors"][0]["is_pep"] in ("No", False, 0)
        assert before_body["directors"][0]["pep_declaration"]["client_declared_pep"] is False
        assert before_body["directors"][0]["pep_declaration"]["officer_verified_pep"] is False
        assert before_body["directors"][0]["pep_declaration"]["pep_status"] == "declared_no"
        assert before_truth["approval_effect"] == "submit_to_compliance_required"
        assert "pep_detected" in set(before_truth["states"])
        assert "provider_detected_pep" in {
            component.get("reason") for component in before_truth.get("components", [])
        }
        assert not ({"adverse_media_hit", "sanctions_hit", "material_concern"} & set(before_truth["states"]))
        assert "provider_pep_match_unresolved" in before_route["escalation_reasons"]
        assert "screening_adverse_truth" in {blocker.get("id") for blocker in before_blockers}

        first = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_ref,
                "subject_type": "director",
                "subject_name": director_name,
                "disposition": "false_positive_cleared",
                "rationale": "Officer compared provider PEP evidence with identity records and confirmed a false positive.",
                "evidence_reference": "Provider case CA-PEP-FP-001 and identity pack reviewed.",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=5,
        )
        assert first.status_code == 202, first.text
        assert first.json()["status"] == "second_review_required"

        sco_token = create_token("sco_provider_pep_fp", "sco", "Second Review SCO", "officer")
        second = http_requests.post(
            f"{api_server}/api/screening/review",
            json={
                "application_id": app_ref,
                "subject_type": "director",
                "subject_name": director_name,
                "disposition": "false_positive_cleared",
                "rationale": "Independent SCO review confirmed the possible PEP profile belongs to a different person.",
                "evidence_reference": "Second-review pack for provider case CA-PEP-FP-001 retained.",
            },
            headers={"Authorization": f"Bearer {sco_token}"},
            timeout=5,
        )
        assert second.status_code == 200, second.text
        second_body = second.json()
        assert second_body["review"]["canonical_disposition"] == "false_positive_cleared"
        assert second_body["review"]["second_reviewer_id"] == "sco_provider_pep_fp"
        assert second_body["risk_recomputed"] is True
        assert second_body["workflow_normalization"]["previous_status"] == "pre_approval_review"
        assert second_body["workflow_normalization"]["new_status"] == "kyc_documents"
        assert second_body["workflow_normalization"]["new_lane"] != "EDD"

        detail_after = http_requests.get(
            f"{api_server}/api/applications/{app_ref}",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=5,
        )
        assert detail_after.status_code == 200, detail_after.text
        after_body = detail_after.json()
        after_truth = after_body["screening_adverse_truth_summary"]

        conn = get_db()
        app = conn.execute(
            """
            SELECT *
            FROM applications WHERE id = ?
            """,
            (app_id,),
        ).fetchone()
        director = conn.execute(
            "SELECT is_pep, pep_declaration FROM directors WHERE id = ?",
            (director_id,),
        ).fetchone()
        review = conn.execute(
            """
            SELECT disposition, disposition_code, rationale, reviewer_id,
                   second_reviewer_id, second_rationale
            FROM screening_reviews WHERE application_id = ? AND subject_type = 'director'
            """,
            (app_id,),
        ).fetchone()
        audit = conn.execute(
            """
            SELECT detail, before_state, after_state FROM audit_log
            WHERE target = ? AND action = 'Screening Review'
            ORDER BY id DESC LIMIT 1
            """,
            (app_ref,),
        ).fetchone()
        after_route = classify_approval_route(dict(app), conn)
        after_blockers = collect_approval_gate_blockers(dict(app), conn)
        conn.close()

        app = dict(app)
        director_pep = json.loads(director["pep_declaration"])
        risk_escalations = set(json.loads(app["risk_escalations"] or "[]"))
        audit_detail = json.loads(audit["detail"])
        retained_report = json.loads(app["prescreening_data"])["screening_report"]
        retained_hit = retained_report["director_screenings"][0]["screening"]["results"][0]

        assert app["status"] == "kyc_documents"
        assert after_body["status"] == "kyc_documents"
        assert app["onboarding_lane"] != "EDD"
        assert app["final_risk_level"] == "LOW"
        assert app["risk_level"] == "LOW"
        assert app["base_risk_level"] == "LOW"
        assert after_body["final_risk_level"] == "LOW"
        assert "floor_rule_declared_pep" not in risk_escalations
        assert "floor_rule_edd_routing" not in risk_escalations
        assert "material_screening_disposition_floor" not in risk_escalations
        assert "false_positive" not in (app["elevation_reason_text"] or "")
        assert director["is_pep"] in ("No", False, 0)
        assert director_pep["client_declared_pep"] is False
        assert director_pep["officer_verified_pep"] is False
        assert director_pep["pep_status"] in {"declared_no", "false_positive", "not_pep"}
        assert review["disposition"] == "cleared"
        assert review["disposition_code"] == "false_positive_cleared"
        assert review["reviewer_id"] == "admin001"
        assert review["second_reviewer_id"] == "sco_provider_pep_fp"
        assert "different person" in review["second_rationale"]
        assert audit_detail["canonical_disposition"] == "false_positive_cleared"
        assert audit_detail["rationale"]
        assert audit_detail["provider_references"]["case_ids"] == [provider_case_id]
        assert retained_hit["is_pep"] is True
        assert retained_hit["provider_case_identifier"] == provider_case_id
        assert after_truth["approval_effect"] == "allow_direct_approval"
        assert "pep_detected" not in set(after_truth["states"])
        assert "provider_detected_pep" not in {
            component.get("reason") for component in after_truth.get("components", [])
        }
        assert "provider_pep_match_unresolved" not in after_route["escalation_reasons"]
        assert "material_screening_concern" not in after_route["escalation_reasons"]
        assert "screening_adverse_truth" not in {blocker.get("id") for blocker in after_blockers}

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
                "evidence_reference": "Provider case CA-CONTEXT-ERR-001.",
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
        from tests.conftest import insert_verified_required_documents
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
        self._insert_enhanced_requirement(conn, app_id, status="accepted")
        insert_verified_required_documents(conn, app_id)
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
        rows = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE target = ? AND action = 'Governance Attempt'
            ORDER BY id DESC
            """,
            (app_ref,),
        ).fetchall()
        conn.close()

        details = [json.loads(r["detail"]) for r in rows]
        # The ownership gate (PR-APP-ACTION-OWNERSHIP-SCOPE-1) writes an
        # ownership_claimed row alongside the 202 on an unassigned case, so
        # select the decision row explicitly instead of assuming it is newest.
        decision_rows = [d for d in details if d["action"] == "application.decision"]
        assert decision_rows, details
        detail = decision_rows[0]
        assert detail["outcome"] == "accepted"
        assert detail["response_code"] == 202
        # And the auto-claim itself is audited (unassigned case, first leg).
        assert any(d["action"].endswith(".ownership_claimed") for d in details)

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


class TestMonitoringEnrollmentActuation:
    def _live_clear_prescreening(self):
        from tests.conftest import clean_ca_prescreening_json

        return clean_ca_prescreening_json(company_name="Monitoring Approval Fixture Ltd")

    def _insert_approvable_application(self, risk_level="LOW"):
        from tests.conftest import insert_verified_required_documents
        from db import get_db

        suffix = uuid.uuid4().hex[:8]
        app_id = f"monitoring_approval_{suffix}"
        app_ref = f"ARF-MONITORING-{suffix}"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        conn = get_db()
        conn.execute(
            """
            INSERT INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type,
                 status, risk_level, final_risk_level, risk_score,
                 prescreening_data, screening_mode, submitted_at, created_at,
                 updated_at, inputs_updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                app_id,
                app_ref,
                f"client_{suffix}",
                f"Monitoring Approval {suffix} Ltd",
                "Mauritius",
                "Technology",
                "SME",
                "compliance_review",
                risk_level,
                risk_level,
                25 if risk_level == "LOW" else 55,
                self._live_clear_prescreening(),
                "live",
                now,
                now,
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO compliance_memos
                (application_id, memo_data, generated_by, ai_recommendation,
                 review_status, quality_score, validation_status, supervisor_status, approval_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                app_id,
                json.dumps({
                    "ai_source": "deterministic",
                    "metadata": {
                        "ai_source": "deterministic",
                        "edd_routing": {"route": "standard", "triggers": []},
                    },
                    "supervisor": {
                        "verdict": "CONSISTENT",
                        "can_approve": True,
                        "mandatory_escalation": False,
                    },
                }),
                "system",
                "APPROVE",
                "approved",
                9.0,
                "pass",
                "CONSISTENT",
                "Fixture approval reason",
            ),
        )
        insert_verified_required_documents(conn, app_id)
        conn.commit()
        conn.close()
        return app_id, app_ref

    def test_application_approval_enrolls_monitoring_and_periodic_review(self, api_server):
        from auth import create_token
        from db import get_db

        app_id, app_ref = self._insert_approvable_application("LOW")
        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/applications/{app_id}/decision",
            json={
                "decision": "approve",
                "decision_reason": "Approve and enroll monitoring.",
                "officer_signoff": {
                    "acknowledged": True,
                    "scope": "decision",
                    "source_context": "ai_advisory",
                },
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        enrollment = body["monitoring_enrollment"]
        assert enrollment["status"] == "created"
        assert enrollment["risk_level"] == "LOW"

        conn = get_db()
        app_row = conn.execute(
            "SELECT decided_at FROM applications WHERE id = ?",
            (app_id,),
        ).fetchone()
        review_rows = conn.execute(
            "SELECT * FROM periodic_reviews WHERE application_id = ?",
            (app_id,),
        ).fetchall()
        audit = conn.execute(
            "SELECT detail FROM audit_log WHERE action='Monitoring Enrollment' AND target=?",
            (app_ref,),
        ).fetchone()
        conn.close()
        assert len(review_rows) == 1
        assert review_rows[0]["due_date"] == enrollment["due_date"]
        decided_date = datetime.fromisoformat(app_row["decided_at"].replace("Z", "+00:00")).date()
        expected_days = (
            datetime.fromisoformat(enrollment["due_date"]).date() - decided_date
        ).days
        assert enrollment["interval_days"] == expected_days
        assert audit is not None
        assert json.loads(audit["detail"])["periodic_review_id"] == enrollment["periodic_review_id"]

        clients_resp = http_requests.get(
            f"{api_server}/api/monitoring/clients",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert clients_resp.status_code == 200
        approved = clients_resp.json()["clients_by_status"]["approved"]
        row = next(item for item in approved if item["id"] == app_id)
        assert row["monitoring_enrolled"] is True
        assert row["periodic_review"]["id"] == enrollment["periodic_review_id"]

    def test_schedule_endpoint_backfills_existing_approved_application(self, api_server):
        from auth import create_token
        from db import get_db

        app_id, app_ref = self._insert_approvable_application("MEDIUM")
        conn = get_db()
        conn.execute(
            "UPDATE applications SET status='approved', decided_at=datetime('now') WHERE id=?",
            (app_id,),
        )
        conn.commit()
        conn.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.post(
            f"{api_server}/api/monitoring/reviews/schedule",
            headers={"Authorization": f"Bearer {token}"},
            json={},
            timeout=5,
        )
        assert resp.status_code == 200, resp.text

        conn = get_db()
        rows = conn.execute(
            "SELECT risk_level, priority FROM periodic_reviews WHERE application_id=?",
            (app_id,),
        ).fetchall()
        audit = conn.execute(
            "SELECT detail FROM audit_log WHERE action='Monitoring Enrollment' AND target=?",
            (app_ref,),
        ).fetchone()
        conn.close()

        assert len(rows) == 1
        assert rows[0]["risk_level"] == "MEDIUM"
        assert rows[0]["priority"] == "normal"
        assert audit is not None


class TestRiskModelAdminConfigSafety:
    """Regression coverage for paid-pilot risk-model admin controls."""

    def _admin_headers(self):
        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        return {"Authorization": f"Bearer {token}"}

    def _risk_config(self, api_server, headers):
        resp = http_requests.get(
            f"{api_server}/api/config/risk-model",
            headers=headers,
            timeout=5,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def _restore_risk_config(self, config):
        from db import get_db
        conn = get_db()
        try:
            conn.execute(
                """
                UPDATE risk_config
                   SET dimensions=?,
                       thresholds=?,
                       country_risk_scores=?,
                       sector_risk_scores=?,
                       entity_type_scores=?
                 WHERE id=1
                """,
                (
                    json.dumps(config["dimensions"]),
                    json.dumps(config["thresholds"]),
                    json.dumps(config["country_risk_scores"]),
                    json.dumps(config["sector_risk_scores"]),
                    json.dumps(config["entity_type_scores"]),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _assert_risk_rejected_unchanged(self, api_server, payload, expected_code):
        headers = self._admin_headers()
        before = self._risk_config(api_server, headers)
        rejected = http_requests.put(
            f"{api_server}/api/config/risk-model",
            headers=headers,
            json=payload,
            timeout=5,
        )
        assert rejected.status_code == 400, rejected.text
        body = rejected.json()
        assert body["code"] == "risk_config_invalid"
        assert any(e["code"] == expected_code for e in body["errors"])
        after = self._risk_config(api_server, headers)
        assert after == before

    def test_partial_score_update_preserves_dimensions_and_thresholds(self, api_server):
        headers = self._admin_headers()

        before_body = self._risk_config(api_server, headers)

        try:
            update = http_requests.put(
                f"{api_server}/api/config/risk-model",
                headers=headers,
                json={"country_risk_scores": {"testland": 2}},
                timeout=5,
            )
            assert update.status_code == 200, update.text

            after_body = self._risk_config(api_server, headers)

            assert after_body["dimensions"] == before_body["dimensions"]
            assert after_body["thresholds"] == before_body["thresholds"]
            assert after_body["country_risk_scores"] == {"testland": 2}
            assert after_body["sector_risk_scores"] == before_body["sector_risk_scores"]
            assert after_body["entity_type_scores"] == before_body["entity_type_scores"]
        finally:
            self._restore_risk_config(before_body)

    def test_country_risk_endpoint_exposes_manual_settings_source(self, api_server):
        headers = self._admin_headers()
        before_body = self._risk_config(api_server, headers)
        try:
            seed_resp = http_requests.put(
                f"{api_server}/api/config/risk-model",
                headers=headers,
                json={"country_risk_scores": {"mauritius": 2, "nigeria": 3, "france": 1}},
                timeout=5,
            )
            assert seed_resp.status_code == 200, seed_resp.text

            resp = http_requests.get(
                f"{api_server}/api/config/country-risk?country=Mauritius",
                headers=headers,
                timeout=5,
            )
            assert resp.status_code == 200, resp.text
            country_risk = resp.json()["country_risk"]
            assert country_risk["country_key"] == "mauritius"
            assert country_risk["risk_score"] == 2
            assert country_risk["mode"] == "manual_settings"
            assert country_risk["active_for_scoring"] is True
            assert country_risk["source"] == "risk_config.country_risk_scores"

            list_resp = http_requests.get(
                f"{api_server}/api/config/country-risk",
                headers=headers,
                timeout=5,
            )
            assert list_resp.status_code == 200, list_resp.text
            body = list_resp.json()
            assert body["mode"] == "manual_settings"
            assert body["active_source"] == "risk_config.country_risk_scores"
            assert body["snapshot"] is None
            assert body["reference_snapshot_active_for_scoring"] is False
            assert any(entry["country_key"] == "mauritius" for entry in body["entries"])
        finally:
            self._restore_risk_config(before_body)

    def test_country_risk_endpoint_dedupes_manual_aliases(self, api_server):
        headers = self._admin_headers()
        before_body = self._risk_config(api_server, headers)
        try:
            seed_resp = http_requests.put(
                f"{api_server}/api/config/risk-model",
                headers=headers,
                json={
                    "country_risk_scores": {
                        "uk": 1,
                        "united kingdom": 1,
                        "usa": 1,
                        "united states": 1,
                        "bvi": 4,
                        "british virgin islands": 4,
                        "mauritius": 2,
                    }
                },
                timeout=5,
            )
            assert seed_resp.status_code == 200, seed_resp.text

            list_resp = http_requests.get(
                f"{api_server}/api/config/country-risk",
                headers=headers,
                timeout=5,
            )
            assert list_resp.status_code == 200, list_resp.text
            entries = list_resp.json()["entries"]
            country_keys = [entry["country_key"] for entry in entries]
            assert country_keys.count("united kingdom") == 1
            assert country_keys.count("united states") == 1
            assert country_keys.count("british virgin islands") == 1
            assert country_keys.count("mauritius") == 1
            assert len(country_keys) == len(set(country_keys))
        finally:
            self._restore_risk_config(before_body)

    def test_grouped_manual_country_payload_is_saved_as_score_map(self, api_server):
        headers = self._admin_headers()
        before_body = self._risk_config(api_server, headers)
        payload = {
            "country_risk_scores": {
                "FATF_BLACK": ["Iran"],
                "SANCTIONED": ["Syria"],
                "FATF_GREY": ["Nigeria", "Syria"],
                "MEDIUM_RISK": ["Mauritius"],
                "LOW_RISK": ["France"],
            }
        }
        try:
            resp = http_requests.put(
                f"{api_server}/api/config/risk-model",
                headers=headers,
                json=payload,
                timeout=5,
            )
            assert resp.status_code == 200, resp.text

            config_resp = http_requests.get(
                f"{api_server}/api/config/risk-model",
                headers=headers,
                timeout=5,
            )
            scores = config_resp.json()["country_risk_scores"]
            assert scores["mauritius"] == 2
            assert scores["nigeria"] == 3
            assert scores["syria"] == 4
            assert scores["france"] == 1
            assert all(not isinstance(value, list) for value in scores.values())
        finally:
            self._restore_risk_config(before_body)

    def test_incomplete_dimension_payload_is_rejected_without_mutation(self, api_server):
        self._assert_risk_rejected_unchanged(
            api_server,
            {
                "dimensions": [{"id": "BAD", "name": "Invalid", "weight": 1, "subcriteria": []}],
                "thresholds": [],
            },
            "risk_dimension_missing",
        )


    def test_unknown_dimension_id_returns_400(self, api_server):
        headers = self._admin_headers()
        config = self._risk_config(api_server, headers)
        dims = [dict(d) for d in config["dimensions"]]
        dims[0] = dict(dims[0], id="BAD")
        self._assert_risk_rejected_unchanged(
            api_server,
            {"dimensions": dims, "thresholds": config["thresholds"]},
            "risk_dimension_missing",
        )

    def test_total_weight_not_100_returns_400(self, api_server):
        headers = self._admin_headers()
        config = self._risk_config(api_server, headers)
        dims = [dict(d) for d in config["dimensions"]]
        dims[0] = dict(dims[0], weight=dims[0]["weight"] + 1)
        self._assert_risk_rejected_unchanged(
            api_server,
            {"dimensions": dims, "thresholds": config["thresholds"]},
            "risk_dimension_weight_total_invalid",
        )

    def test_missing_thresholds_returns_400(self, api_server):
        headers = self._admin_headers()
        config = self._risk_config(api_server, headers)
        self._assert_risk_rejected_unchanged(
            api_server,
            {"dimensions": config["dimensions"]},
            "risk_thresholds_required",
        )

    def test_empty_thresholds_returns_400(self, api_server):
        headers = self._admin_headers()
        config = self._risk_config(api_server, headers)
        self._assert_risk_rejected_unchanged(
            api_server,
            {"dimensions": config["dimensions"], "thresholds": []},
            "risk_thresholds_required",
        )

    def test_empty_score_maps_are_rejected(self, api_server):
        headers = self._admin_headers()
        config = self._risk_config(api_server, headers)
        self._assert_risk_rejected_unchanged(
            api_server,
            {
                "dimensions": config["dimensions"],
                "thresholds": config["thresholds"],
                "country_risk_scores": {},
            },
            "risk_score_map_required",
        )

    def test_invalid_update_does_not_call_recompute_or_mutate(self, api_server, monkeypatch):
        import server

        calls = []

        def fail_if_called(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("recompute must not be called for invalid risk config")

        monkeypatch.setattr(server, "recompute_risk_for_active_apps", fail_if_called)
        headers = self._admin_headers()
        before = self._risk_config(api_server, headers)
        resp = http_requests.put(
            f"{api_server}/api/config/risk-model",
            headers=headers,
            json={"dimensions": [], "thresholds": []},
            timeout=5,
        )
        assert resp.status_code == 400, resp.text
        assert calls == []
        assert self._risk_config(api_server, headers) == before

    def test_valid_full_update_still_succeeds(self, api_server):
        headers = self._admin_headers()
        config = self._risk_config(api_server, headers)
        resp = http_requests.put(
            f"{api_server}/api/config/risk-model",
            headers=headers,
            json={
                "dimensions": config["dimensions"],
                "thresholds": config["thresholds"],
                "country_risk_scores": config["country_risk_scores"],
                "sector_risk_scores": config["sector_risk_scores"],
                "entity_type_scores": config["entity_type_scores"],
            },
            timeout=5,
        )
        assert resp.status_code == 200, resp.text


class TestAdminPilotMutationAuditabilityAndRBAC:
    """Paid-pilot admin control auditability, validation, and RBAC coverage."""

    def _headers(self, role="admin", sub=None, name=None):
        from auth import create_token
        sub = sub or f"{role}001"
        name = name or f"Test {role.upper()}"
        token = create_token(sub, role, name, "officer")
        return {"Authorization": f"Bearer {token}"}

    def _audit_row(self, action, target):
        from db import get_db
        conn = get_db()
        row = conn.execute(
            "SELECT detail, before_state, after_state FROM audit_log WHERE action=? AND target=? ORDER BY id DESC LIMIT 1",
            (action, target),
        ).fetchone()
        conn.close()
        assert row is not None
        return row

    def _authz_denial_row(self, target, actor_id):
        from db import get_db
        conn = get_db()
        row = conn.execute(
            """
            SELECT detail FROM audit_log
            WHERE action='authz_denied_internal_api' AND target=? AND user_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (target, actor_id),
        ).fetchone()
        conn.close()
        assert row is not None
        return json.loads(row["detail"])

    def _assert_before_after_no_secrets(self, row):
        assert row["after_state"]
        blob = f"{row['before_state'] or ''} {row['after_state'] or ''} {row['detail'] or ''}".lower()
        assert "password_hash" not in blob
        assert "api_key" not in blob
        assert "secret" not in blob
        assert "token" not in blob

    def test_unauthenticated_admin_apis_return_401(self, api_server):
        checks = [
            ("PUT", "/api/config/risk-model", {}),
            ("POST", "/api/config/ai-agents", {}),
            ("PUT", "/api/config/verification-checks", {}),
            ("PUT", "/api/config/system-settings", {}),
            ("POST", "/api/users", {}),
        ]
        for method, path, payload in checks:
            resp = http_requests.request(method, f"{api_server}{path}", json=payload, timeout=5)
            assert resp.status_code == 401, (method, path, resp.text)

    def test_lower_roles_cannot_mutate_admin_control_endpoints(self, api_server):
        payloads = [
            ("PUT", "/api/config/risk-model", {"dimensions": []}),
            ("POST", "/api/config/ai-agents", {"agent_number": 901, "name": "Blocked", "stage": "Monitoring", "enabled": True, "checks": []}),
            ("PUT", "/api/config/verification-checks", {"category": "entity", "doc_type": "blocked_doc", "doc_name": "Blocked", "checks": []}),
            ("PUT", "/api/config/system-settings", {"company_name": "Blocked"}),
            ("POST", "/api/users", {"email": "blocked@example.test", "full_name": "Blocked", "role": "analyst"}),
        ]
        for role in ("sco", "co", "analyst"):
            headers = self._headers(role)
            for method, path, payload in payloads:
                resp = http_requests.request(method, f"{api_server}{path}", headers=headers, json=payload, timeout=5)
                assert resp.status_code == 403, (role, method, path, resp.text)

    def test_sco_co_analyst_read_policy_is_server_side(self, api_server):
        assert http_requests.get(f"{api_server}/api/users", headers=self._headers("sco"), timeout=5).status_code == 200
        assert http_requests.get(f"{api_server}/api/users", headers=self._headers("co"), timeout=5).status_code == 403
        assert http_requests.get(f"{api_server}/api/config/risk-model", headers=self._headers("analyst"), timeout=5).status_code == 200
        assert http_requests.get(f"{api_server}/api/config/country-risk", headers=self._headers("analyst"), timeout=5).status_code == 200
        assert http_requests.get(f"{api_server}/api/config/ai-agents", headers=self._headers("analyst"), timeout=5).status_code == 200
        assert http_requests.get(f"{api_server}/api/config/verification-checks", headers=self._headers("analyst"), timeout=5).status_code == 200
        assert http_requests.get(f"{api_server}/api/settings/enhanced-requirements", headers=self._headers("co"), timeout=5).status_code == 200
        assert http_requests.get(f"{api_server}/api/settings/enhanced-requirements", headers=self._headers("analyst"), timeout=5).status_code == 403

    def test_analyst_risk_config_mutation_returns_403_and_is_audited(self, api_server):
        headers = self._headers("analyst")
        resp = http_requests.put(
            f"{api_server}/api/config/risk-model",
            headers=headers,
            json={"country_risk_scores": {"blockedland": 4}},
            timeout=5,
        )
        assert resp.status_code == 403, resp.text
        detail = self._authz_denial_row("config/risk-model", "analyst001")
        assert detail["event"] == "authz_denied_internal_api"
        assert detail["actor_role"] == "analyst"
        assert detail["allowed_roles"] == ["admin"]
        assert detail["method"] == "PUT"

    def test_analyst_ai_config_mutation_returns_403_and_is_audited(self, api_server):
        agents = http_requests.get(f"{api_server}/api/config/ai-agents", headers=self._headers(), timeout=5).json()["agents"]
        agent = agents[0]
        headers = self._headers("analyst")
        resp = http_requests.put(
            f"{api_server}/api/config/ai-agents/{agent['id']}",
            headers=headers,
            json={"enabled": not bool(agent["enabled"])},
            timeout=5,
        )
        assert resp.status_code == 403, resp.text
        detail = self._authz_denial_row("config/ai-agents", "analyst001")
        assert detail["event"] == "authz_denied_internal_api"
        assert detail["actor_role"] == "analyst"
        assert detail["allowed_roles"] == ["admin"]
        assert detail["method"] == "PUT"

    def test_read_only_roles_cannot_mutate_alternate_ai_config_endpoints(self, api_server):
        agents = http_requests.get(f"{api_server}/api/config/ai-agents", headers=self._headers(), timeout=5).json()["agents"]
        agent = agents[0]
        headers = self._headers("analyst")
        checks = [
            ("POST", "/api/config/ai-agents", {"agent_number": 902, "name": "Blocked", "stage": "Monitoring", "enabled": True, "checks": []}),
            ("PUT", f"/api/config/ai-agents/{agent['id']}", {"enabled": not bool(agent["enabled"])}),
            ("DELETE", f"/api/config/ai-agents/{agent['id']}", None),
            ("PUT", "/api/config/verification-checks", {"category": "entity", "doc_type": "blocked_doc", "doc_name": "Blocked", "checks": []}),
        ]
        for method, path, payload in checks:
            resp = http_requests.request(
                method,
                f"{api_server}{path}",
                headers=headers,
                json=payload,
                timeout=5,
            )
            assert resp.status_code == 403, (method, path, resp.text)

    def test_ai_agent_update_creates_before_after_audit(self, api_server):
        agents = http_requests.get(f"{api_server}/api/config/ai-agents", headers=self._headers(), timeout=5).json()["agents"]
        agent = agents[0]
        payload = {
            "agent_number": agent["agent_number"],
            "name": agent["name"],
            "icon": agent.get("icon") or "",
            "stage": agent["stage"],
            "description": "ADMIN-AUDIT synthetic update",
            "enabled": agent["enabled"],
            "checks": agent.get("checks") or [],
        }
        resp = http_requests.put(
            f"{api_server}/api/config/ai-agents/{agent['id']}",
            headers=self._headers(),
            json=payload,
            timeout=5,
        )
        assert resp.status_code == 200, resp.text
        row = self._audit_row("Config Update", "AI Agents")
        self._assert_before_after_no_secrets(row)
        assert "ADMIN-AUDIT synthetic update" in row["after_state"]

    def test_ai_agent_partial_toggle_creates_before_after_audit(self, api_server):
        agents = http_requests.get(f"{api_server}/api/config/ai-agents", headers=self._headers(), timeout=5).json()["agents"]
        agent = agents[0]
        resp = http_requests.put(
            f"{api_server}/api/config/ai-agents/{agent['id']}",
            headers=self._headers(),
            json={"enabled": not bool(agent["enabled"])},
            timeout=5,
        )
        assert resp.status_code == 200, resp.text
        row = self._audit_row("Config Update", "AI Agents")
        self._assert_before_after_no_secrets(row)
        assert '"enabled":' in row["before_state"]
        assert '"enabled":' in row["after_state"]

    def test_ai_agent_delete_soft_disables_and_audits(self, api_server):
        from db import get_db

        conn = get_db()
        max_num = conn.execute("SELECT COALESCE(MAX(agent_number), 0) AS m FROM ai_agents").fetchone()["m"]
        conn.close()
        agent_number = min(max_num + 1, 998)
        create = http_requests.post(
            f"{api_server}/api/config/ai-agents",
            headers=self._headers(),
            json={
                "agent_number": agent_number,
                "name": f"ADMIN-AUDIT Agent {agent_number}",
                "icon": "A",
                "stage": "Monitoring",
                "description": "Synthetic audit agent",
                "enabled": True,
                "checks": ["Synthetic check"],
            },
            timeout=5,
        )
        assert create.status_code == 201, create.text
        conn = get_db()
        agent = conn.execute("SELECT id FROM ai_agents WHERE agent_number=?", (agent_number,)).fetchone()
        conn.close()

        deleted = http_requests.delete(
            f"{api_server}/api/config/ai-agents/{agent['id']}",
            headers=self._headers(),
            timeout=5,
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["status"] == "disabled"
        conn = get_db()
        row = conn.execute("SELECT enabled FROM ai_agents WHERE id=?", (agent["id"],)).fetchone()
        conn.close()
        assert row is not None
        assert int(row["enabled"]) == 0
        audit = self._audit_row("Config Disable", "AI Agents")
        self._assert_before_after_no_secrets(audit)
        assert json.loads(audit["before_state"])["enabled"] is True
        assert json.loads(audit["after_state"])["enabled"] is False

    def test_ai_verification_check_update_creates_before_after_audit(self, api_server):
        payload = {
            "category": "entity",
            "doc_type": "admin_audit_test",
            "doc_name": "ADMIN AUDIT Test Document",
            "checks": [
                {
                    "id": "admin_audit_check",
                    "label": "ADMIN-AUDIT label",
                    "rule": "Synthetic rule for auditability",
                    "type": "rule",
                    "classification": "rule",
                    "severity": "medium",
                    "active": True,
                }
            ],
        }
        resp = http_requests.put(
            f"{api_server}/api/config/verification-checks",
            headers=self._headers(),
            json=payload,
            timeout=5,
        )
        assert resp.status_code == 200, resp.text
        row = self._audit_row("Config Update", "AI Checks")
        self._assert_before_after_no_secrets(row)
        assert "ADMIN-AUDIT label" in row["after_state"]

    def test_system_settings_update_creates_before_after_audit(self, api_server):
        current = http_requests.get(f"{api_server}/api/config/system-settings", headers=self._headers(), timeout=5)
        assert current.status_code == 200, current.text
        body = current.json()
        payload = {
            "company_name": "Onboarda Ltd",
            "licence_number": body.get("licence_number") or "FSC-PIS-2024-001",
            "default_retention_years": 7,
            "auto_approve_max_score": 35,
            "edd_threshold_score": 60,
            "confirm_dangerous_change": True,
        }
        resp = http_requests.put(
            f"{api_server}/api/config/system-settings",
            headers=self._headers(),
            json=payload,
            timeout=5,
        )
        assert resp.status_code == 200, resp.text
        row = self._audit_row("Config", "System Settings")
        self._assert_before_after_no_secrets(row)
        assert json.loads(row["after_state"])["edd_threshold_score"] == 60

    def test_user_role_status_update_creates_before_after_audit(self, api_server):
        email = f"admin-audit-{uuid.uuid4().hex[:8]}@example.test"
        create = http_requests.post(
            f"{api_server}/api/users",
            headers=self._headers(),
            json={"email": email, "full_name": "ADMIN-AUDIT User", "role": "analyst"},
            timeout=5,
        )
        assert create.status_code == 201, create.text
        user_id = create.json()["id"]
        update = http_requests.put(
            f"{api_server}/api/users/{user_id}",
            headers=self._headers(),
            json={"role": "co", "status": "inactive", "full_name": "ADMIN-AUDIT User"},
            timeout=5,
        )
        assert update.status_code == 200, update.text
        row = self._audit_row("Update User", email)
        self._assert_before_after_no_secrets(row)
        before = json.loads(row["before_state"])
        after = json.loads(row["after_state"])
        assert before["role"] == "analyst"
        assert after["role"] == "co"
        assert after["status"] == "inactive"


def test_h1_live_memo_route_is_deterministic(api_server, monkeypatch):
    """H1 (PR-10): the live memo route must be deterministic — no Claude in the path.

    With ENABLE_CLAUDE_MEMO unset, the memo produced by the real HTTP route must
    carry the provenance marker ai_source == "deterministic" (not "demo", not any
    LLM identifier), both in the response body and in the persisted memo row.
    A 200 response alone is not sufficient evidence.
    """
    monkeypatch.delenv("ENABLE_CLAUDE_MEMO", raising=False)
    from auth import create_token
    from tests.conftest import insert_verified_required_documents
    from db import get_db

    # Poison Claude construction for the duration of this test: the
    # deterministic route must never build a ClaudeClient. This catches a
    # future wiring that calls Claude directly while still stamping
    # ai_source="deterministic" — the marker alone is producer-controlled.
    import claude_client as claude_client_module

    def _no_claude(*_args, **_kwargs):
        raise AssertionError("ClaudeClient constructed during the deterministic memo route")

    monkeypatch.setattr(claude_client_module.ClaudeClient, "__init__", _no_claude)

    app_id = "app_h1_deterministic_memo"
    conn = get_db()
    conn.execute("DELETE FROM compliance_memos WHERE application_id = ?", (app_id,))
    conn.execute("DELETE FROM documents WHERE application_id = ?", (app_id,))
    conn.execute("DELETE FROM directors WHERE application_id = ?", (app_id,))
    conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
    prescreening = {
        "registered_entity_name": "H1 Deterministic Ltd",
        "source_of_funds": "Operating revenue",
        "expected_volume": "50000",
        "operating_countries": "Mauritius",
        "business_activity": "Consulting",
        "screening_report": {
            "screening_mode": "live",
            "company_screening": {
                "sanctions": {
                    "matched": False,
                    "api_status": "live",
                    "provider": "sumsub",
                    "source": "sumsub",
                }
            },
            "director_screenings": [],
            "ubo_screenings": [],
            "total_hits": 0,
        },
    }
    conn.execute(
        """
        INSERT INTO applications (
            id, ref, client_id, company_name, country, sector, entity_type,
            status, risk_level, risk_score, prescreening_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            "ARF-2026-H1-DET",
            "client_h1_det",
            "H1 Deterministic Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "kyc_submitted",
            "LOW",
            20,
            json.dumps(prescreening),
        ),
    )
    insert_verified_required_documents(conn, app_id)
    conn.commit()
    conn.close()

    token = create_token("admin001", "admin", "Test Admin", "officer")
    resp = http_requests.post(
        f"{api_server}/api/applications/{app_id}/memo",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["metadata"]["ai_source"] == "deterministic"

    conn = get_db()
    row = conn.execute(
        "SELECT memo_data FROM compliance_memos WHERE application_id = ? ORDER BY id DESC LIMIT 1",
        (app_id,),
    ).fetchone()
    conn.close()
    assert row is not None
    persisted = json.loads(row["memo_data"])
    assert persisted["metadata"]["ai_source"] == "deterministic"
