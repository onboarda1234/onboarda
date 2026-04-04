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
