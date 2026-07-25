"""P13-3 / FEO-004 — defensive API response parsing in the back office.

The defect: `boApiCall` called `res.json()` BEFORE any status or Content-Type
check. A 502 HTML error page, an nginx plaintext body or a 204 therefore threw
`SyntaxError: Unexpected token '<'` at the parse — which meant the `!res.ok`
branch that builds `apiErr.status` / `.payload` / `.cmGuidance` never ran, and
every downstream `err.status === 403` / `=== 404` check silently
mis-classified a bare SyntaxError carrying no status at all.

That helper is the transport for 161 call sites including every frozen
Application Review decision, memo and detail-load path, so fixing it there is
both the highest-leverage change and the only one that repairs error-object
fidelity on the frozen surfaces.

These are RUNTIME tests: the real functions are extracted from
arie-backoffice.html and driven under node against mocked responses, so they
fail if the guard is removed rather than merely asserting the source text.
"""

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKOFFICE = REPO / "arie-backoffice.html"
HTML = BACKOFFICE.read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is required for the runtime harness")


def _extract(name):
    """Pull one async function out of the single-file back office."""
    match = re.search(
        r"^async function " + re.escape(name) + r"\(.*?^}", HTML, re.S | re.M)
    assert match, f"{name} not found in arie-backoffice.html"
    return match.group(0)


READER = _extract("readApiResponseBody")
BO_API_CALL = _extract("boApiCall")


def _run(scenario_js):
    script = "\n".join([
        # Minimal ambient stubs the helper touches.
        "let toasts = [];",
        "function showToast(msg, level) { toasts.push({ msg, level }); }",
        "function getCookieValue() { return ''; }",
        "function cmLockGuidanceFor() { return null; }",
        "function showCmGuidanceToast() {}",
        "function clearBoAuthState() {}",
        "function showLoginScreen() {}",
        "let BO_AUTH_TOKEN = 'tok';",
        "let BO_API_BASE = '/api';",
        "let BO_ACTIVE_SESSION_ID = 1;",
        "let BO_AUTH_SESSION_SEQ = 1;",
        READER,
        BO_API_CALL,
        scenario_js,
    ])
    proc = subprocess.run(["node", "-e", script], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _response(*, status=200, ok=None, content_type="application/json",
              body="", json_throws=False, omit_text=False):
    """Build a mock Response.

    `body` is passed through VERBATIM — an earlier version coerced "" to "{}",
    which made the 204 test pass against a mutant that removed the 204 guard
    (caught in review). `omit_text` reproduces the repo's house `okJson()` mock
    shape, which exposes only json().
    """
    ok_js = "true" if (status < 400 if ok is None else ok) else "false"
    throws = "throw new SyntaxError('Unexpected token');" if json_throws else ""
    headers_js = ("null" if content_type is None
                  else "{ get(name) { return %s; } }" % json.dumps(content_type))
    json_body = body if body else "{}"
    text_member = "" if omit_text else (
        "async text() { return %s; }" % json.dumps(body))
    return textwrap.dedent(f"""
        {{
          ok: {ok_js},
          status: {status},
          headers: {headers_js},
          async json() {{ {throws} return JSON.parse({json.dumps(json_body)}); }}{"," if text_member else ""}
          {text_member}
        }}
    """)


class TestErrorObjectFidelity:
    """The regression that mattered: a non-JSON error body must still produce
    an error carrying the HTTP status."""

    def _call_with(self, response_js):
        return _run(textwrap.dedent(f"""
            global.fetch = async () => ({response_js});
            (async () => {{
              try {{
                const data = await boApiCall('GET', '/x');
                console.log(JSON.stringify({{ threw: false, data }}));
              }} catch (err) {{
                console.log(JSON.stringify({{
                  threw: true, name: err.name, status: err.status || null,
                  message: String(err.message || ''),
                  hasPayload: !!err.payload
                }}));
              }}
            }})();
        """))

    def test_html_error_page_yields_a_status_bearing_error(self):
        """Before the fix this threw a bare SyntaxError with no .status, so
        callers checking err.status === 403 mis-classified it."""
        result = self._call_with(_response(
            status=502, content_type="text/html",
            body="<html><body>502 Bad Gateway</body></html>"))
        assert result["threw"] is True
        assert result["status"] == 502
        assert result["name"] != "SyntaxError"
        assert result["hasPayload"] is True

    def test_plaintext_error_body_yields_a_status_bearing_error(self):
        result = self._call_with(_response(
            status=503, content_type="text/plain", body="upstream unavailable"))
        assert result["threw"] is True
        assert result["status"] == 503

    def test_403_still_carries_its_status_for_permission_branches(self):
        result = self._call_with(_response(
            status=403, body=json.dumps({"error": "Insufficient permissions"})))
        assert result["threw"] is True
        assert result["status"] == 403
        assert "Insufficient permissions" in result["message"]

    def test_malformed_json_with_a_json_content_type_does_not_leak_syntaxerror(self):
        result = self._call_with(_response(
            status=500, json_throws=True, body="{"))
        assert result["threw"] is True
        assert result["status"] == 500
        assert result["name"] != "SyntaxError"

    def test_missing_headers_object_does_not_crash(self):
        """Several runtime harnesses mock responses without a headers object;
        a naive res.headers.get() would TypeError."""
        result = self._call_with(_response(
            status=500, content_type=None, body="boom"))
        assert result["threw"] is True
        assert result["status"] == 500


class TestSuccessPathUnchanged:
    """Frozen-surface safety: a well-formed JSON 2xx must behave exactly as
    before, byte for byte."""

    def test_json_success_body_is_returned_verbatim(self):
        payload = {"applications": [{"id": "a1"}], "total": 1}
        result = _run(textwrap.dedent(f"""
            global.fetch = async () => ({_response(body=json.dumps(payload))});
            (async () => {{
              const data = await boApiCall('GET', '/applications');
              console.log(JSON.stringify({{ data }}));
            }})();
        """))
        assert result["data"] == payload

    def test_json_body_without_a_json_content_type_is_still_parsed(self):
        payload = {"ok": True}
        result = _run(textwrap.dedent(f"""
            global.fetch = async () => ({_response(
                content_type="text/plain", body=json.dumps(payload))});
            (async () => {{
              const data = await boApiCall('GET', '/x');
              console.log(JSON.stringify({{ data }}));
            }})();
        """))
        assert result["data"] == payload

    def test_204_no_content_returns_an_empty_object_not_a_throw(self):
        """A real 204 carries a JSON content-type and an EMPTY body, so
        res.json() throws. Without the 204 guard this yields a spurious
        "Malformed JSON" error object."""
        result = _run(textwrap.dedent(f"""
            global.fetch = async () => ({_response(
                status=204, body="", json_throws=True)});
            (async () => {{
              const data = await boApiCall('DELETE', '/x');
              console.log(JSON.stringify({{ data }}));
            }})();
        """))
        assert result["data"] == {}

    def test_a_response_exposing_only_json_still_works(self):
        """The repo's house mock shape (`{ json: async () => ... }`). Returning
        {} for these would be silent data loss — and it broke a pre-existing
        XSS guard test whose renderer reads through this helper."""
        payload = {"entries": [{"id": 1}]}
        result = _run(textwrap.dedent(f"""
            global.fetch = async () => ({_response(
                content_type=None, body=json.dumps(payload), omit_text=True)});
            (async () => {{
              const data = await boApiCall('GET', '/x');
              console.log(JSON.stringify({{ data }}));
            }})();
        """))
        assert result["data"] == payload


class TestSessionExpiryStillWorks:
    def test_401_triggers_the_session_reset_path(self):
        result = _run(textwrap.dedent(f"""
            global.fetch = async () => ({_response(
                status=401, body=json.dumps({"error": "expired"}))});
            (async () => {{
              try {{ await boApiCall('GET', '/x'); }} catch (err) {{
                console.log(JSON.stringify({{
                  message: String(err.message || ''), toasts
                }}));
              }}
            }})();
        """))
        assert "expired" in result["message"].lower()
        assert any("sign in again" in t["msg"].lower() for t in result["toasts"])


class TestParseOrderIsPinned:
    """Static guard: the parse must not drift back above the status checks."""

    def test_body_is_read_through_the_defensive_reader(self):
        body = BO_API_CALL
        assert "readApiResponseBody(res)" in body
        assert "await res.json()" not in body, (
            "boApiCall must not parse the body directly")

    def test_reader_never_calls_json_without_a_content_type_check(self):
        assert "Content-Type" in READER
        content_type_at = READER.index("Content-Type")
        json_at = READER.index("res.json()")
        assert content_type_at < json_at

    def test_frozen_memo_validation_checks_status_before_parsing(self):
        match = re.search(
            r"^async function fetchValidationResults\(.*?^}", HTML, re.S | re.M)
        assert match
        body = match.group(0)
        assert "if (!res.ok) return null;" in body
        assert "await res.json()" not in body
