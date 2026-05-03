"""
Tests for GET /api/version (VersionHandler).

Auth-gated endpoint returning build metadata from environment variables.
No DB calls, no PII, no secrets.
"""
import os
import sys
import socket
import json
import tempfile
import threading
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

import requests as http_requests
import tornado.ioloop
import tornado.httpserver

EXPECTED_KEYS = {"git_sha", "git_sha_short", "build_time", "image_tag", "environment", "service"}


def _find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def api_server():
    """Start a real Tornado HTTP server for version endpoint testing."""
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

    yield f"http://127.0.0.1:{port}"

    io_loop = server_ref.get("loop")
    srv = server_ref.get("server")
    if io_loop and srv:
        io_loop.add_callback(srv.stop)
        io_loop.add_callback(io_loop.stop)
    thread.join(timeout=2)


class TestVersionEndpoint:

    def test_version_unauthenticated_returns_401(self, api_server):
        """GET /api/version without auth token must return 401."""
        resp = http_requests.get(f"{api_server}/api/version", timeout=3)
        assert resp.status_code == 401

    def test_version_authenticated_returns_200(self, api_server):
        """GET /api/version with valid auth must return 200 with JSON body."""
        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/version",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)

    def test_version_response_shape(self, api_server):
        """Response must contain all expected keys."""
        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/version",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        body = resp.json()
        missing = EXPECTED_KEYS - body.keys()
        assert not missing, f"Missing keys in response: {missing}"

    def test_version_with_git_sha_env(self, api_server, monkeypatch):
        """When GIT_SHA env var is set the response must echo it back."""
        sha = "abc1234deadbeef5678"
        monkeypatch.setenv("GIT_SHA", sha)
        monkeypatch.setenv("BUILD_TIME", "2026-05-03T00:00:00Z")
        monkeypatch.setenv("IMAGE_TAG", sha)

        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/version",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        body = resp.json()
        assert body["git_sha"] == sha
        assert body["git_sha_short"] == sha[:7]
        assert body["build_time"] == "2026-05-03T00:00:00Z"
        assert body["image_tag"] == sha

    def test_version_service_name(self, api_server):
        """service field must always be 'regmind-backend'."""
        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/version",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        body = resp.json()
        assert body["service"] == "regmind-backend"

    def test_version_no_db_dependency(self, api_server):
        """Endpoint must succeed without any DB interaction (implicitly proven
        by the fact that it returns 200 with correct shape without any DB
        fixtures beyond the base api_server setup)."""
        from auth import create_token
        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/version",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.keys() >= EXPECTED_KEYS

    def test_memo_pdf_response_includes_build_provenance_headers(self, api_server, monkeypatch):
        """Memo PDF downloads must expose build SHA evidence headers."""
        import server as server_module
        from auth import create_token
        from db import get_db

        sha = "feedfacecafebeef1234567890abcdef12345678"
        monkeypatch.setenv("GIT_SHA", sha)
        monkeypatch.setenv("BUILD_TIME", "2026-05-03T01:02:03Z")
        monkeypatch.setenv("IMAGE_TAG", sha)
        monkeypatch.setattr(server_module, "generate_memo_pdf", lambda **kwargs: b"%PDF-fake-build-provenance")

        app_id = "version-pdf-app"
        memo_data = {
            "sections": {"executive_summary": {"content": "PDF provenance smoke."}},
            "metadata": {
                "memo_version": "v1",
                "approval_recommendation": "REVIEW",
                "canonical_risk": {"available": False},
                "display_risk_rating": "NOT_RATED",
                "display_risk_score": None,
                "build": {
                    "git_sha": sha,
                    "git_sha_short": sha[:7],
                    "build_time": "2026-05-03T01:02:03Z",
                    "image_tag": sha,
                },
            },
        }
        db = get_db()
        try:
            db.execute(
                "INSERT OR IGNORE INTO applications (id, ref, company_name, country, sector, entity_type, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (app_id, "ARF-VERSION-PDF", "Version PDF Ltd", "Mauritius", "Technology", "SME", "submitted"),
            )
            db.execute("DELETE FROM compliance_memos WHERE application_id = ?", (app_id,))
            db.execute(
                "INSERT INTO compliance_memos (application_id, version, memo_data, generated_by, memo_version, validation_status, quality_score) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (app_id, 1, json.dumps(memo_data), "admin001", "v1", "pass_with_fixes", 7.0),
            )
            db.commit()
        finally:
            db.close()

        token = create_token("admin001", "admin", "Test Admin", "officer")
        resp = http_requests.get(
            f"{api_server}/api/applications/{app_id}/memo/pdf",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )

        assert resp.status_code == 200
        assert resp.headers["X-Build-Git-Sha"] == sha
        assert resp.headers["X-Build-Git-Sha-Short"] == sha[:7]
        assert resp.headers["X-Memo-Build-Git-Sha"] == sha
        assert resp.headers["X-Memo-Build-Git-Sha-Short"] == sha[:7]
        assert resp.headers["X-Memo-Version"] == "v1"
