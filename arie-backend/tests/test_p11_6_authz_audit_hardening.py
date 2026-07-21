"""P11-6 (BSA-003 / BSA-009) — admin reset re-auth + authz-denial audit routing.

BSA-003: the two live admin password-reset endpoints must demand the acting
admin's OWN password (re-auth) and rate-limit the attempt — a stolen or idle
admin session alone must not rotate credentials.

BSA-009 residual: authorization denials that previously returned 403 with no
audit trail now route through ``log_authz_denial`` with byte-identical
response bodies. (The ``require_auth`` role-denial core was already audited;
governance-audited sites — memo approve, screening disposition, EDD, export
pack — deliberately keep their existing ``log_governance_attempt``/
``log_audit`` mechanisms.)
"""

import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest
import requests as http_requests
import tornado.httpserver
import tornado.ioloop

BACKEND = Path(__file__).resolve().parents[1]

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def api_server():
    """Real Tornado server on a background IOLoop — same pattern/DB path as
    test_api.py so no collision with other suites."""
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

    from tests.conftest import shutdown_test_http_server
    shutdown_test_http_server(thread, server_ref)

# Every denial site this PR routed through log_authz_denial, by its
# machine-readable source tag.
NEW_DENIAL_SOURCES = (
    "idv_resolution",
    "application_update_status",
    "application_delete",
    "document_manual_accept",
    "document_download",
    "draft_persistence",
    "company_intake",
    "client_notifications",
    "notification_mark_read",
)

# The response bodies of the audited sites must stay byte-identical.
UNCHANGED_DENIAL_BODIES = (
    "Only officers can change application status",
    "Only portal clients can delete applications.",
    "Only senior compliance officers (admin/sco) may manually accept unverified documents",
    "Draft persistence is only available to portal clients.",
    "Company intake is available to authenticated client users only.",
    "Only clients can retrieve notifications",
)


def _server_source():
    return (BACKEND / "server.py").read_text(encoding="utf-8")


def _region(text, start, end):
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


# ── BSA-003: re-auth + rate limit (static) ───────────────────────────


def test_admin_reset_handlers_require_reauth_and_rate_limit():
    src = _server_source()
    client_h = _region(src, "class AdminResetPasswordHandler", "\nclass ")
    officer_h = _region(src, "class AdminOfficerPasswordResetHandler", "\nclass ")
    for region, action in ((client_h, "admin_client_reset"), (officer_h, "admin_officer_reset")):
        assert "_admin_reauth_ok(" in region, "re-auth gate missing"
        assert f'"{action}"' in region, f"rate-limit action {action} missing"
        assert "check_sensitive_rate_limit(" in region
    # Ordering: missing-env 503 stays first (EX-01 pins it — config state,
    # not a secret), then re-auth, and only then the token COMPARISON so a
    # session-only attacker gains no oracle on the ops token value.
    for region in (client_h, officer_h):
        assert region.index("required_confirm:") < region.index("_admin_reauth_ok(")
        assert region.index("_admin_reauth_ok(") < region.index("confirm != required_confirm")


def test_reauth_helper_is_fail_closed():
    src = _server_source()
    helper = _region(src, "def _admin_reauth_ok", "\nclass AdminResetPasswordHandler")
    assert "bcrypt.checkpw" in helper
    assert '"authz_denied_reauth"' in helper
    # Empty stored hash, missing row, and malformed hash all deny.
    assert "if row else" in helper
    # AttributeError included: non-string admin_password (JSON number) must
    # deny via the audited 401, not escape as a 500.
    assert "except (ValueError, TypeError, AttributeError)" in helper
    assert helper.count("return False") >= 2


# ── BSA-003: re-auth (functional) ────────────────────────────────────


def test_admin_reset_rejects_missing_and_wrong_reauth_password(api_server, monkeypatch):
    import bcrypt
    from auth import create_token
    from db import get_db

    monkeypatch.setenv("ADMIN_CLIENT_RESET_CONFIRMATION", "p116-confirm")
    admin_token = create_token("admin001", "admin", "Test Admin", "officer")

    # audit_log is a REGULATED table (P12-1): never DELETE from it in tests.
    # Use a high-watermark instead.
    conn = get_db()
    conn.execute(
        "UPDATE users SET password_hash=? WHERE id='admin001'",
        (bcrypt.hashpw(b"CorrectAdmin123!", bcrypt.gensalt()).decode(),),
    )
    conn.commit()
    watermark = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS m FROM audit_log"
    ).fetchone()["m"]
    conn.close()

    base = {"email": "nobody@example.com", "new_password": "StrongPass123!",
            "confirm": "p116-confirm"}

    missing = http_requests.post(
        f"{api_server}/api/admin/reset-password",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=base, timeout=3,
    )
    assert missing.status_code == 401
    assert "re-authentication" in missing.text

    wrong = http_requests.post(
        f"{api_server}/api/admin/reset-password",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={**base, "admin_password": "WrongPassword1!"}, timeout=3,
    )
    assert wrong.status_code == 401
    assert "incorrect" in wrong.text

    # Audit finding: a non-string admin_password (JSON number) must deny with
    # the audited 401, not escape as an unhandled 500.
    nonstring = http_requests.post(
        f"{api_server}/api/admin/reset-password",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={**base, "admin_password": 12345}, timeout=3,
    )
    assert nonstring.status_code == 401

    conn = get_db()
    rows = conn.execute(
        "SELECT detail FROM audit_log WHERE id > ? AND action='authz_denied_reauth'",
        (watermark,),
    ).fetchall()
    conn.close()
    assert len(rows) == 2, (
        "wrong and non-string re-auth passwords must each write a denial audit row"
    )
    detail = json.loads(rows[0]["detail"])
    assert detail["source"] == "admin_client_reset"


def test_admin_reset_rate_limit_trips_after_five_attempts(api_server, monkeypatch):
    """Audit follow-up: the 5/600s shared limit must actually return 429."""
    import bcrypt
    from auth import create_token
    from db import get_db

    monkeypatch.setenv("ADMIN_CLIENT_RESET_CONFIRMATION", "p116-confirm")
    admin_token = create_token("admin001", "admin", "Test Admin", "officer")

    conn = get_db()
    conn.execute(
        "UPDATE users SET password_hash=? WHERE id='admin001'",
        (bcrypt.hashpw(b"CorrectAdmin123!", bcrypt.gensalt()).decode(),),
    )
    conn.commit()
    conn.close()

    body = {"email": "nobody@example.com", "new_password": "StrongPass123!",
            "confirm": "p116-confirm", "admin_password": "WrongPassword1!"}
    codes = []
    for _ in range(6):
        r = http_requests.post(
            f"{api_server}/api/admin/reset-password",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=body, timeout=3,
        )
        codes.append(r.status_code)
    assert codes[:5] == [401] * 5
    assert codes[5] == 429


def test_officer_reset_rejects_wrong_reauth_password(api_server, monkeypatch):
    import bcrypt
    from auth import create_token
    from db import get_db

    monkeypatch.setenv("ADMIN_OFFICER_RESET_CONFIRMATION", "p116-officer-confirm")
    admin_token = create_token("admin001", "admin", "Test Admin", "officer")

    conn = get_db()
    conn.execute(
        "UPDATE users SET password_hash=? WHERE id='admin001'",
        (bcrypt.hashpw(b"CorrectAdmin123!", bcrypt.gensalt()).decode(),),
    )
    conn.commit()
    conn.close()

    wrong = http_requests.post(
        f"{api_server}/api/admin/officer-reset-password",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"email": "nobody@example.com", "new_password": "StrongPass123!",
              "confirm": "p116-officer-confirm", "admin_password": "WrongPassword1!"},
        timeout=3,
    )
    assert wrong.status_code == 401


# ── BSA-009: denial routing (functional, representative site) ────────


def test_notifications_type_gate_denial_is_audited(api_server):
    from auth import create_token
    from db import get_db

    # Watermark instead of DELETE — audit_log is regulated (P12-1).
    conn = get_db()
    watermark = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS m FROM audit_log"
    ).fetchone()["m"]
    conn.close()

    officer_token = create_token("admin001", "admin", "Test Admin", "officer")
    resp = http_requests.get(
        f"{api_server}/api/notifications",
        headers={"Authorization": f"Bearer {officer_token}"},
        timeout=3,
    )
    assert resp.status_code == 403
    assert "Only clients can retrieve notifications" in resp.text

    conn = get_db()
    rows = conn.execute(
        "SELECT detail FROM audit_log WHERE id > ? AND action='authz_denied_role' "
        "AND detail LIKE '%client_notifications%'",
        (watermark,),
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    detail = json.loads(rows[0]["detail"])
    assert detail["source"] == "client_notifications"
    assert detail["required"] == "client"


# ── BSA-009: denial routing (static, all sites) ──────────────────────


def test_all_new_denial_sites_route_through_log_authz_denial():
    src = _server_source()
    for source in NEW_DENIAL_SOURCES:
        assert f'"source": "{source}"' in src, (
            f"denial site '{source}' lost its log_authz_denial routing (P11-6 / BSA-009)"
        )
    # draft_persistence covers TWO sites under one tag — both must stay routed.
    assert src.count('"source": "draft_persistence"') == 2
    # 9 denial sites + the re-auth helper = at least 10 routing calls.
    assert src.count("log_authz_denial(") >= 10


def test_denial_response_bodies_unchanged():
    """Audit is additive-only: the 403 bodies must stay byte-identical."""
    src = _server_source()
    for body in UNCHANGED_DENIAL_BODIES:
        assert body in src, f"denial body changed: {body!r}"
