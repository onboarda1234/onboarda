"""M1.1 REFRESH-STATUS-DECOUPLING tests.

1) Unit: monitoring_status effective-status + high-risk helpers.
2) Static: the refresh overload writes are gone from the source.
3) API: interim high-risk (sanctions/PEP) false-positive dismissal guard on
   BOTH dismissal paths (action=dismiss and save_decision/false_positive),
   including the blocked-attempt audit event; low-risk dismissal unaffected.
"""
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time

import pytest
import requests
import tornado.httpserver
import tornado.ioloop

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

import monitoring_status as ms

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 1. Unit: effective status ───────────────────────────────────────────────

def test_effective_status_derives_from_refresh_request():
    assert ms.effective_status("open", "requested") == "document_requested"
    assert ms.effective_status("open", "uploaded") == "client_uploaded"
    assert ms.effective_status("open", "under_review") == "under_review"
    assert ms.effective_status("open", "rejected") == "document_requested"
    # No active request → alert's own status.
    assert ms.effective_status("open", None) == "open"
    assert ms.effective_status("assigned", "accepted") == "assigned"


def test_effective_status_terminal_alert_ignores_refresh_state():
    assert ms.effective_status("resolved", "requested") == "resolved"
    assert ms.effective_status("waived", "uploaded") == "waived"
    assert ms.effective_status("open", "requested", resolved_at="2026-07-01") == "open"


def test_effective_status_tolerates_legacy_overloaded_rows():
    # Pre-M1.1 rows still carry the overload in alert.status.
    assert ms.effective_status("client_uploaded", None) == "client_uploaded"
    assert ms.effective_status("document_requested", None) == "document_requested"


def test_extended_statuses_reserve_in_review_and_escalated():
    assert "in_review" in ms.EXTENDED_ALERT_STATUSES
    assert "escalated" in ms.EXTENDED_ALERT_STATUSES
    assert set(ms.CANONICAL_ALERT_STATUSES).issubset(set(ms.EXTENDED_ALERT_STATUSES))


# ── 1b. Unit: high-risk classification ──────────────────────────────────────

def test_high_risk_screening_alert_detection():
    assert ms.is_high_risk_screening_alert({"alert_type": "Sanctions Match"}) is True
    assert ms.is_high_risk_screening_alert({"alert_type": "pep"}) is True
    assert ms.is_high_risk_screening_alert({"alert_type": "pep_change"}) is True
    assert ms.is_high_risk_screening_alert({"alert_type": "watchlist"}) is True
    assert ms.is_high_risk_screening_alert({"alert_type": "document_expired"}) is False
    assert ms.is_high_risk_screening_alert({"alert_type": "media"}) is False
    # Adverse media stays out of guard scope even when the summary mentions a PEP.
    assert ms.is_high_risk_screening_alert({"alert_type": "adverse_media", "summary": "PEP mentioned"}) is False
    # Summary fallback when type is missing.
    assert ms.is_high_risk_screening_alert({"alert_type": "", "summary": "New sanctions listing"}) is True
    assert ms.is_high_risk_screening_alert(None) is False


# ── 2. Static: overload writes removed from source ──────────────────────────

def test_refresh_flow_no_longer_writes_overloaded_alert_statuses():
    refresh_src = open(os.path.join(BACKEND_DIR, "monitoring_document_refresh.py")).read()
    for banned_status in ("document_requested", "client_uploaded", "under_review"):
        # Whitespace-tolerant, case-insensitive, quote-agnostic: survives SQL
        # reformatting that a literal string match would silently miss.
        pattern = re.compile(
            r"SET\s+status\s*=\s*['\"]" + re.escape(banned_status) + r"['\"]",
            re.IGNORECASE,
        )
        assert not pattern.search(refresh_src), banned_status


def test_decision_outcome_request_updated_document_no_longer_stores_status():
    import server as server_module

    cfg = server_module.MONITORING_DECISION_OUTCOMES["request_updated_document"]
    assert cfg["status"] is None


# ── 3. API fixture (isolated sqlite + live tornado) ─────────────────────────

def _free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _patch_attr(module, name, value, restore):
    sentinel = object()
    old_value = getattr(module, name, sentinel)
    restore.append((module, name, old_value, sentinel))
    setattr(module, name, value)


def _restore_attrs(restore):
    for module, name, old_value, sentinel in reversed(restore):
        if old_value is sentinel:
            try:
                delattr(module, name)
            except AttributeError:
                pass
        else:
            setattr(module, name, old_value)


def _configure_isolated_sqlite(db_path):
    import config as config_module
    import db as db_module

    restore = []
    _patch_attr(config_module, "DATABASE_URL", "", restore)
    _patch_attr(config_module, "DB_PATH", db_path, restore)
    _patch_attr(config_module, "ENVIRONMENT", "testing", restore)
    _patch_attr(db_module, "DATABASE_URL", "", restore)
    _patch_attr(db_module, "DB_PATH", db_path, restore)
    _patch_attr(db_module, "USE_POSTGRESQL", False, restore)
    _patch_attr(db_module, "_CFG_ENVIRONMENT", "testing", restore)

    server_module = sys.modules.get("server")
    if server_module is not None:
        _patch_attr(server_module, "DATABASE_URL", "", restore)
        _patch_attr(server_module, "DB_PATH", db_path, restore)
        _patch_attr(server_module, "USE_POSTGRES", False, restore)
        _patch_attr(server_module, "USE_POSTGRESQL", False, restore)
        _patch_attr(server_module, "db_get_db", db_module.get_db, restore)
        _patch_attr(server_module, "db_init_db", db_module.init_db, restore)
    return db_module, restore


def _seed(conn):
    for user_id, email, name, role in [
        ("admin_g", "admin-g@example.test", "Admin Guard", "admin"),
        ("sco_g", "sco-g@example.test", "SCO Guard", "sco"),
        ("co_g", "co-g@example.test", "CO Guard", "co"),
    ]:
        conn.execute(
            "INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status) VALUES (?, ?, ?, ?, ?, 'active')",
            (user_id, email, "unused", name, role),
        )
    conn.execute(
        "INSERT OR REPLACE INTO applications (id, ref, company_name, status, is_fixture) VALUES (?, ?, ?, 'approved', 0)",
        ("app_guard", "GUARD-REF", "Guard Client Ltd", ),
    )


def _reset_alerts(conn):
    conn.execute("DELETE FROM monitoring_alerts WHERE id BETWEEN 9501 AND 9506")
    rows = [
        (9501, "Sanctions Match", "critical", "New sanctions listing for director"),
        (9502, "pep_change", "medium", "PEP status change detected"),
        (9503, "document_expired", "medium", "Passport expired"),
        (9504, "media", "medium", "New adverse media coverage"),
        (9505, "Sanctions Match", "critical", "Second sanctions alert"),
        (9506, "pep", "medium", "Declared PEP re-screen"),
        (9507, "Sanctions Match", "critical", "Dedicated audit-contents sanctions alert"),
    ]
    for alert_id, alert_type, severity, summary in rows:
        conn.execute(
            """
            INSERT INTO monitoring_alerts
                (id, application_id, client_name, alert_type, severity, status,
                 detected_by, summary, discovered_via, source_reference)
            VALUES (?, 'app_guard', 'Guard Client Ltd', ?, ?, 'open', 'test', ?, 'manual', ?)
            """,
            (alert_id, alert_type, severity, summary, json.dumps({"seed": alert_id})),
        )
    conn.commit()


@pytest.fixture(scope="module")
def guard_server():
    db_path = os.path.join(
        tempfile.gettempdir(),
        f"onboarda_refresh_decoupling_{os.getpid()}_{time.time_ns()}.db",
    )
    restore = []
    thread = None
    server_ref = {}
    previous_env = {
        "DB_PATH": os.environ.get("DB_PATH"),
        "DATABASE_URL": os.environ.get("DATABASE_URL"),
    }
    os.environ["DB_PATH"] = db_path
    os.environ["DATABASE_URL"] = ""

    db_module, restore = _configure_isolated_sqlite(db_path)
    db_module.init_db()
    conn = db_module.get_db()
    _seed(conn)
    _reset_alerts(conn)
    conn.commit()
    conn.close()

    import server as server_module

    _patch_attr(server_module, "DATABASE_URL", "", restore)
    _patch_attr(server_module, "DB_PATH", db_path, restore)
    _patch_attr(server_module, "USE_POSTGRES", False, restore)
    _patch_attr(server_module, "USE_POSTGRESQL", False, restore)
    _patch_attr(server_module, "db_get_db", db_module.get_db, restore)
    _patch_attr(server_module, "db_init_db", db_module.init_db, restore)
    from server import make_app

    app = make_app()
    port = _free_port()
    started = threading.Event()

    def run_server():
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        io_loop = tornado.ioloop.IOLoop.current()
        server = tornado.httpserver.HTTPServer(app)
        server.listen(port, "127.0.0.1")
        server_ref["server"] = server
        server_ref["loop"] = io_loop
        started.set()
        io_loop.start()

    try:
        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        started.wait(timeout=3)
        time.sleep(0.2)
        yield f"http://127.0.0.1:{port}", db_module
    finally:
        loop = server_ref.get("loop")
        server = server_ref.get("server")
        if loop and server:
            loop.add_callback(server.stop)
            loop.add_callback(loop.stop)
        if thread:
            thread.join(timeout=2)
        if previous_env["DB_PATH"] is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = previous_env["DB_PATH"]
        if previous_env["DATABASE_URL"] is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_env["DATABASE_URL"]
        _restore_attrs(restore)
        try:
            os.unlink(db_path)
        except FileNotFoundError:
            pass


def _token(user_id, role, name):
    from auth import create_token

    return create_token(user_id, role, name, "officer")


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _patch_alert(base_url, token, alert_id, payload):
    return requests.patch(
        f"{base_url}/api/monitoring/alerts/{alert_id}",
        headers=_headers(token),
        json=payload,
        timeout=5,
    )


def _alert_status(db_module, alert_id):
    conn = db_module.get_db()
    try:
        return conn.execute(
            "SELECT status FROM monitoring_alerts WHERE id = ?", (alert_id,)
        ).fetchone()["status"]
    finally:
        conn.close()



def _action_count(db_module, alert_id, action):
    conn = db_module.get_db()
    try:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE action = ? AND target = ?",
            (action, f"monitoring_alert:{alert_id}"),
        ).fetchone()["c"]
    finally:
        conn.close()


# ── M2.2 senior-override behaviour (updated from the M1.1 interim guard) ─────
# The interim high-risk guard (monitoring.alert.high_risk_dismissal_blocked,
# 403 for CO) was replaced by the four-eyes senior-override control. These
# tests assert the NEW behaviour on the same seeded alerts.

def test_co_tier1_sanctions_clear_now_creates_request_not_403(guard_server):
    base_url, db_module = guard_server
    co = _token("co_g", "co", "CO Guard")
    # Without evidence, a Tier-1 request is rejected with 400 (evidence required)...
    no_ev = _patch_alert(base_url, co, 9501, {
        "action": "dismiss", "dismissal_reason": "false_positive",
        "reason": "Name-only match",
    })
    assert no_ev.status_code == 400
    assert "evidence" in no_ev.json()["error"].lower()
    assert _alert_status(db_module, 9501) == "open"
    # ...with evidence it becomes a pending senior-approval request (not a 403).
    with_ev = _patch_alert(base_url, co, 9501, {
        "action": "dismiss", "dismissal_reason": "false_positive",
        "reason": "Name-only match", "evidence_ref": "DOB mismatch",
    })
    assert with_ev.status_code == 200, with_ev.text
    assert with_ev.json()["status"] == "review_requested"
    assert _alert_status(db_module, 9501) == "open"
    assert _action_count(db_module, 9501, "monitoring.alert.dismissal_requested") == 1


def test_sco_direct_clear_tier1_succeeds_with_enhanced_rationale(guard_server):
    base_url, db_module = guard_server
    sco = _token("sco_g", "sco", "SCO Guard")
    # 9505 is a Tier-1 sanctions alert. Evidence required for a senior direct clear.
    no_ev = _patch_alert(base_url, sco, 9505, {
        "action": "save_decision", "outcome": "false_positive", "note": "mismatch",
    })
    assert no_ev.status_code == 400
    assert _alert_status(db_module, 9505) == "open"
    ok = _patch_alert(base_url, sco, 9505, {
        "action": "save_decision", "outcome": "false_positive",
        "note": "DOB and nationality mismatch confirmed", "evidence_ref": "passport p.2",
    })
    assert ok.status_code == 200, ok.text
    assert _alert_status(db_module, 9505) == "dismissed"
    assert _action_count(db_module, 9505, "monitoring.alert.dismissal_senior_cleared") == 1


def test_tier2_identity_document_clear_is_controlled(guard_server):
    base_url, db_module = guard_server
    co = _token("co_g", "co", "CO Guard")
    # 9503 is document_expired "Passport expired" -> Tier 2 (identity). A CO
    # false-positive clear is now controlled (creates a request), not a silent
    # single-officer clear. (waive_with_reason routes through the separate
    # document-refresh workflow, so false_positive is used here.)
    resp = _patch_alert(base_url, co, 9503, {
        "action": "save_decision", "outcome": "false_positive",
        "note": "Expiry date captured wrong; passport is valid",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "review_requested"
    assert _alert_status(db_module, 9503) == "open"


def test_tier3_adverse_media_medium_is_single_officer(guard_server):
    base_url, db_module = guard_server
    co = _token("co_g", "co", "CO Guard")
    # 9504 adverse media at medium severity -> Tier 3 -> single-officer close.
    resp = _patch_alert(base_url, co, 9504, {
        "action": "dismiss", "dismissal_reason": "false_positive",
        "reason": "Article refers to an unrelated company",
    })
    assert resp.status_code == 200, resp.text
    assert _alert_status(db_module, 9504) == "dismissed"
    assert _action_count(db_module, 9504, "monitoring.alert.dismissal_requested") == 0


def test_self_approval_blocked_and_audited(guard_server):
    base_url, db_module = guard_server
    sco = _token("sco_g", "sco", "SCO Guard")
    # SCO elects a second review on 9506 (Tier-1 pep) -> pending request...
    r = _patch_alert(base_url, sco, 9506, {
        "action": "save_decision", "outcome": "false_positive",
        "note": "please double-check", "evidence_ref": "note",
        "send_for_second_review": True,
    })
    assert r.json()["status"] == "review_requested"
    req_id = r.json()["result"]["review_request_id"]
    # ...and cannot approve their own request.
    import requests as _rq
    ap = _rq.post(
        f"{base_url}/api/monitoring/review-requests/{req_id}/approve",
        headers={"Authorization": f"Bearer {sco}", "Content-Type": "application/json"},
        json={"approval_note": "self"}, timeout=10,
    )
    assert ap.status_code == 403
    assert _alert_status(db_module, 9506) == "open"
    assert _action_count(db_module, 9506, "monitoring.alert.dismissal_blocked") == 1
