"""P11-3 / BSA-006 + BSA-007 + BSA-013 — fail-closed inputs + AI budget.

BSA-006: BaseHandler.get_json silently converted a malformed JSON body to {},
so state-changing endpoints proceeded on defaults the client never sent. Now a
non-empty unparseable body returns a structured 400; an empty body (and JSON
null) still yields {}.

BSA-007: three list handlers cast pagination params with raw int(), turning
"?limit=abc" into a 500. They now use the platform-wide _bounded_int
convention (malformed -> default, clamped bounds) like every other list route.

BSA-013: Claude persistent-budget enforcement failed OPEN when the usage store
was unreadable. In staging/production it now fails CLOSED (request blocked);
dev/test keeps the fail-open so local runs don't require the store.
"""
import json
import os
import sys
import tempfile
import threading
import time

import pytest
import tornado.web

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ══════════════════════════════════════════════════════════
# BSA-006 — get_json unit behaviour
# ══════════════════════════════════════════════════════════

def _handler_with_body(body: bytes):
    from base_handler import BaseHandler

    class _Req:
        pass

    h = BaseHandler.__new__(BaseHandler)
    h.request = _Req()
    h.request.body = body
    return h


class TestGetJsonFailClosed:
    def test_empty_body_returns_empty_dict(self):
        assert _handler_with_body(b"").get_json() == {}

    def test_valid_object_parses(self):
        assert _handler_with_body(b'{"a": 1}').get_json() == {"a": 1}

    def test_json_null_returns_empty_dict(self):
        assert _handler_with_body(b"null").get_json() == {}

    def test_malformed_body_raises_structured_400(self):
        with pytest.raises(tornado.web.HTTPError) as exc:
            _handler_with_body(b"{not-json").get_json()
        assert exc.value.status_code == 400
        assert "JSON" in (exc.value.reason or "")

    def test_truncated_object_raises_400(self):
        with pytest.raises(tornado.web.HTTPError) as exc:
            _handler_with_body(b'{"a": ').get_json()
        assert exc.value.status_code == 400

    def test_array_body_passes_through(self):
        """A valid JSON array is still returned (no dict coercion) — endpoints
        that accept arrays keep working; object-expecting handlers fail the
        same way they always did on arrays."""
        assert _handler_with_body(b'[1, 2]').get_json() == [1, 2]

    def test_scalar_body_raises_400(self):
        """Review fold N1: a bare JSON scalar used to become an
        AttributeError-500 in every data.get() caller — now a clean 400."""
        for scalar in (b"42", b'"x"', b"true"):
            with pytest.raises(tornado.web.HTTPError) as exc:
                _handler_with_body(scalar).get_json()
            assert exc.value.status_code == 400


class TestSupervisorGetJsonFailClosed:
    """Review fold S1: the same fail-open bug class lived verbatim in
    supervisor/api.py's SupervisorBaseHandler.get_json_body."""

    def _sup_handler(self, body: bytes):
        from supervisor.api import SupervisorBaseHandler

        class _Req:
            pass

        h = SupervisorBaseHandler.__new__(SupervisorBaseHandler)
        h.request = _Req()
        h.request.body = body
        return h

    def test_malformed_body_raises_400(self):
        with pytest.raises(tornado.web.HTTPError) as exc:
            self._sup_handler(b"{nope").get_json_body()
        assert exc.value.status_code == 400

    def test_empty_and_null_return_empty_dict(self):
        assert self._sup_handler(b"").get_json_body() == {}
        assert self._sup_handler(b"null").get_json_body() == {}

    def test_valid_object_parses(self):
        assert self._sup_handler(b'{"a": 1}').get_json_body() == {"a": 1}

    def test_bounded_int_argument_defaults_on_malformed(self):
        h = self._sup_handler(b"")
        h.get_argument = lambda name, default=None: "abc"
        assert h.get_bounded_int_argument("limit", 50, 1, 500) == 50
        h.get_argument = lambda name, default=None: "99999"
        assert h.get_bounded_int_argument("limit", 50, 1, 500) == 500


# ══════════════════════════════════════════════════════════
# BSA-007 — no raw int(get_argument) pagination casts remain
# ══════════════════════════════════════════════════════════

_SERVER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py")


class TestBoundedPagination:
    def test_no_raw_int_get_argument_casts_remain(self):
        """Scans server.py AND supervisor/api.py AND production_controls.py
        (review fold S2 — the first cut scanned only server.py and missed five
        live supervisor routes)."""
        import re
        backend = os.path.dirname(_SERVER)
        offenders = []
        for rel in ("server.py", os.path.join("supervisor", "api.py"), "production_controls.py"):
            with open(os.path.join(backend, rel), encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    if re.search(r"\bint\(self\.get_argument\(", line) \
                            and "_bounded_int" not in line \
                            and "int(self.get_argument(name" not in line:
                        # allow the explicitly try/except-wrapped clamp pattern
                        offenders.append(f"{rel}:{n}: {line.strip()}")
        allowed = {"production_controls.py"}  # its one site is try/except-clamped
        real = [o for o in offenders if o.split(":")[0] not in allowed]
        assert real == [], f"raw int(get_argument) casts must use a bounded parse: {real}"

    def test_bounded_int_defaults_on_malformed(self):
        sys.path.insert(0, os.path.dirname(_SERVER))
        from server import _bounded_int
        assert _bounded_int("abc", 200, min_value=1, max_value=500) == 200
        assert _bounded_int(None, 50, min_value=1, max_value=500) == 50
        assert _bounded_int("999999", 200, min_value=1, max_value=500) == 500
        assert _bounded_int("-5", 0, min_value=0, max_value=100) == 0


# ══════════════════════════════════════════════════════════
# BSA-013 — Claude budget fails closed in staging/production
# ══════════════════════════════════════════════════════════

class TestBudgetFailClosed:
    def _force_store_error(self, monkeypatch):
        import production_controls

        def _boom(*a, **k):
            raise RuntimeError("usage store unreachable")

        monkeypatch.setattr(
            production_controls.usage_cap_manager, "check_budget", _boom)

    def test_store_outage_blocks_in_staging(self, monkeypatch):
        import claude_client
        self._force_store_error(monkeypatch)
        monkeypatch.setattr(claude_client, "_CFG_IS_STAGING", True)
        monkeypatch.setattr(claude_client, "_CFG_IS_PRODUCTION", False)
        assert claude_client._check_persistent_budget() is False

    def test_store_outage_blocks_in_production(self, monkeypatch):
        import claude_client
        self._force_store_error(monkeypatch)
        monkeypatch.setattr(claude_client, "_CFG_IS_STAGING", False)
        monkeypatch.setattr(claude_client, "_CFG_IS_PRODUCTION", True)
        assert claude_client._check_persistent_budget() is False

    def test_store_outage_blocks_in_demo(self, monkeypatch):
        """Review fold S3: demo is deployed + internet-facing and can carry a
        real API key — it fails closed like staging/production."""
        import claude_client
        self._force_store_error(monkeypatch)
        monkeypatch.setattr(claude_client, "_CFG_IS_STAGING", False)
        monkeypatch.setattr(claude_client, "_CFG_IS_PRODUCTION", False)
        monkeypatch.setattr(claude_client, "_CFG_IS_DEMO", True)
        assert claude_client._check_persistent_budget() is False

    def test_store_outage_allows_in_dev(self, monkeypatch):
        import claude_client
        self._force_store_error(monkeypatch)
        monkeypatch.setattr(claude_client, "_CFG_IS_STAGING", False)
        monkeypatch.setattr(claude_client, "_CFG_IS_PRODUCTION", False)
        monkeypatch.setattr(claude_client, "_CFG_IS_DEMO", False)
        assert claude_client._check_persistent_budget() is True

    def test_generate_path_honours_budget_gate(self, monkeypatch):
        """Review fold S4: generate() is a paid path too — over-budget (or
        store-outage-in-staging) must block it, not just _call_claude."""
        import claude_client
        monkeypatch.setattr(claude_client, "_check_persistent_budget", lambda *a, **k: False)
        client = claude_client.ClaudeClient.__new__(claude_client.ClaudeClient)
        client.mock_mode = False
        client._check_fail_closed = lambda ctx: None
        with pytest.raises(RuntimeError, match="budget"):
            client.generate("test prompt")

    def test_healthy_store_verdict_passes_through(self, monkeypatch):
        import claude_client
        import production_controls
        monkeypatch.setattr(
            production_controls.usage_cap_manager, "check_budget",
            lambda *a, **k: False)
        monkeypatch.setattr(claude_client, "_CFG_IS_STAGING", True)
        assert claude_client._check_persistent_budget() is False
        monkeypatch.setattr(
            production_controls.usage_cap_manager, "check_budget",
            lambda *a, **k: True)
        assert claude_client._check_persistent_budget() is True


# ══════════════════════════════════════════════════════════
# HTTP-level: malformed JSON → structured 400 (real server)
# ══════════════════════════════════════════════════════════

def _find_free_port():
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def inputs_server():
    import tornado.httpserver
    import tornado.ioloop

    # db.DB_PATH is bound at import time (the conftest already pointed it at a
    # session temp DB) — do NOT re-point env here; just ensure the schema and
    # seeds exist, tolerating an already-initialized database.
    from db import init_db, seed_initial_data, get_db as _get
    try:
        init_db()
    except Exception:
        pass  # schema already present from the session's conftest
    try:
        conn = _get()
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
        server_ref["loop"] = io_loop
        started.set()
        io_loop.start()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    started.wait(timeout=3)
    time.sleep(0.2)
    yield f"http://127.0.0.1:{port}"
    loop = server_ref.get("loop")
    if loop:
        loop.add_callback(loop.stop)


class TestMalformedJsonHttp:
    def test_malformed_body_returns_structured_400(self, inputs_server):
        import requests as http_requests
        resp = http_requests.post(
            f"{inputs_server}/api/auth/officer/login",
            data="{definitely-not-json",
            headers={"Content-Type": "application/json"},
            timeout=3,
        )
        assert resp.status_code == 400
        payload = resp.json()
        assert payload.get("status") == 400
        assert "JSON" in payload.get("error", "")

    def test_empty_body_keeps_prior_behaviour(self, inputs_server):
        import requests as http_requests
        resp = http_requests.post(
            f"{inputs_server}/api/auth/officer/login", data="", timeout=3)
        # empty body → {} → normal missing-credentials handling (not the
        # malformed-JSON 400)
        assert resp.status_code in (400, 401)
        assert "JSON" not in resp.json().get("error", "")

    def test_valid_body_unaffected(self, inputs_server):
        import requests as http_requests
        resp = http_requests.post(
            f"{inputs_server}/api/auth/officer/login",
            json={"email": "nobody@example.com", "password": "WrongPass123!"},
            timeout=3,
        )
        assert resp.status_code in (401, 403, 429)
