"""M2.1 PR-4 — human overdue escalation API tests.

The endpoint is deliberately narrow: allowed officers can manually escalate an
actively overdue Monitoring Alert through the existing ``escalate_to_sco``
decision transition. The wrapper adds an SLA-state gate, a tighter role gate,
an overdue-specific audit event, and an additive escalation ledger.
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

import pytest
import requests
import tornado.httpserver
import tornado.ioloop

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _patch_attr(module, name, value, restore):
    sentinel = object()
    old = getattr(module, name, sentinel)
    restore.append((module, name, old, sentinel))
    setattr(module, name, value)


def _restore_attrs(restore):
    for module, name, old, sentinel in reversed(restore):
        if old is sentinel:
            try:
                delattr(module, name)
            except AttributeError:
                pass
        else:
            setattr(module, name, old)


@pytest.fixture(scope="module")
def overdue_escalation_server():
    db_path = os.path.join(tempfile.gettempdir(), f"monitoring_overdue_escalation_{os.getpid()}_{time.time_ns()}.db")
    restore = []
    thread = None
    server_ref = {}
    prev = {"DB_PATH": os.environ.get("DB_PATH"), "DATABASE_URL": os.environ.get("DATABASE_URL")}
    os.environ["DB_PATH"] = db_path
    os.environ["DATABASE_URL"] = ""

    import config as config_module
    import db as db_module

    _patch_attr(config_module, "DATABASE_URL", "", restore)
    _patch_attr(config_module, "DB_PATH", db_path, restore)
    _patch_attr(config_module, "ENVIRONMENT", "testing", restore)
    _patch_attr(db_module, "DATABASE_URL", "", restore)
    _patch_attr(db_module, "DB_PATH", db_path, restore)
    _patch_attr(db_module, "USE_POSTGRESQL", False, restore)
    _patch_attr(db_module, "_CFG_ENVIRONMENT", "testing", restore)

    db_module.init_db()
    conn = db_module.get_db()
    for uid, email, name, role in [
        ("admin_oe", "admin-oe@example.test", "Admin OE", "admin"),
        ("sco_oe", "sco-oe@example.test", "SCO OE", "sco"),
        ("co_oe", "co-oe@example.test", "CO OE", "co"),
        ("analyst_oe", "analyst-oe@example.test", "Analyst OE", "analyst"),
    ]:
        conn.execute(
            "INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status) VALUES (?,?,?,?,?, 'active')",
            (uid, email, "unused", name, role),
        )
    conn.execute(
        "INSERT OR REPLACE INTO applications (id, ref, company_name, status, is_fixture) VALUES ('app-oe','OE-REF','OE Ltd','approved',0)"
    )
    conn.commit()
    conn.close()

    import server as server_module

    _patch_attr(server_module, "DATABASE_URL", "", restore)
    _patch_attr(server_module, "DB_PATH", db_path, restore)
    _patch_attr(server_module, "USE_POSTGRES", False, restore)
    _patch_attr(server_module, "USE_POSTGRESQL", False, restore)
    _patch_attr(server_module, "db_get_db", db_module.get_db, restore)
    _patch_attr(server_module, "db_init_db", db_module.init_db, restore)
    _patch_attr(server_module, "send_portal_email", lambda *a, **k: (_ for _ in ()).throw(AssertionError("email must not be sent")), restore)
    from server import make_app

    app = make_app()
    port = _free_port()
    started = threading.Event()

    def run():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        io = tornado.ioloop.IOLoop.current()
        srv = tornado.httpserver.HTTPServer(app)
        srv.listen(port, "127.0.0.1")
        server_ref["server"] = srv
        server_ref["loop"] = io
        started.set()
        io.start()

    try:
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        if not started.wait(timeout=3):
            raise RuntimeError("overdue_escalation_server failed to start within 3s")
        time.sleep(0.2)
        yield f"http://127.0.0.1:{port}", db_module
    finally:
        loop = server_ref.get("loop")
        srv = server_ref.get("server")
        if loop and srv:
            loop.add_callback(srv.stop)
            loop.add_callback(loop.stop)
        if thread:
            thread.join(timeout=2)
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _restore_attrs(restore)
        try:
            os.unlink(db_path)
        except FileNotFoundError:
            pass


_next_alert_id = [9900]


def _seed_alert(db_module, *, status="open", severity="high",
                discovered_at="2026-01-05 09:00:00", resolved_at=None):
    aid = _next_alert_id[0]
    _next_alert_id[0] += 1
    conn = db_module.get_db()
    try:
        conn.execute(
            """
            INSERT INTO monitoring_alerts
                (id, application_id, client_name, alert_type, severity, status,
                 detected_by, summary, discovered_via, source_reference,
                 discovered_at, created_at, resolved_at)
            VALUES (?, 'app-oe', 'OE Ltd', 'adverse_media', ?, ?, 'test',
                    'Overdue escalation test alert', 'manual', ?, ?, ?, ?)
            """,
            (
                aid,
                severity,
                status,
                json.dumps({"seed": aid}),
                discovered_at,
                discovered_at,
                resolved_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return aid


def _seed_followup(db_module, aid, due_at="2099-12-31 09:00:00"):
    conn = db_module.get_db()
    try:
        conn.execute(
            """
            INSERT INTO monitoring_alert_followups (alert_id, action, note, due_at, created_by)
            VALUES (?, 'next_step', 'future follow-up', ?, 'co_oe')
            """,
            (aid, due_at),
        )
        conn.commit()
    finally:
        conn.close()
    return due_at


def _tok(uid, role, name):
    from auth import create_token
    return create_token(uid, role, name, "officer")


def _hdr(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _post_escalate(base, token, aid, reason="QA overdue escalation"):
    return requests.post(
        f"{base}/api/monitoring/alerts/{aid}/escalate-overdue",
        headers=_hdr(token),
        json={"reason": reason},
        timeout=10,
    )


def _detail(base, token, aid):
    return requests.get(f"{base}/api/monitoring/alerts/{aid}", headers=_hdr(token), timeout=10)


def _db_alert(db_module, aid):
    conn = db_module.get_db()
    try:
        return dict(conn.execute("SELECT * FROM monitoring_alerts WHERE id = ?", (aid,)).fetchone())
    finally:
        conn.close()


def _audit_actions(db_module, aid):
    conn = db_module.get_db()
    try:
        return [r["action"] for r in conn.execute(
            "SELECT action FROM audit_log WHERE target = ? ORDER BY id ASC",
            (f"monitoring_alert:{aid}",),
        ).fetchall()]
    finally:
        conn.close()


def _ledger_rows(db_module, aid):
    conn = db_module.get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM monitoring_alert_escalations WHERE alert_id = ? ORDER BY id ASC",
            (aid,),
        ).fetchall()]
    finally:
        conn.close()


@pytest.mark.parametrize("uid,role,name", [
    ("admin_oe", "admin", "Admin OE"),
    ("sco_oe", "sco", "SCO OE"),
    ("co_oe", "co", "CO OE"),
])
def test_allowed_roles_escalate_active_overdue_alert(overdue_escalation_server, uid, role, name):
    base, dbm = overdue_escalation_server
    aid = _seed_alert(dbm)
    r = _post_escalate(base, _tok(uid, role, name), aid, reason=f"Escalated by {role}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "overdue_escalated"
    assert body["new_status"] == "escalated"
    alert = _db_alert(dbm, aid)
    assert alert["status"] == "escalated"
    assert alert["officer_action"] == "escalate_to_sco"


def test_analyst_is_blocked_even_if_legacy_escalation_allows_it(overdue_escalation_server):
    base, dbm = overdue_escalation_server
    aid = _seed_alert(dbm)
    r = _post_escalate(base, _tok("analyst_oe", "analyst", "Analyst OE"), aid)
    assert r.status_code == 403, r.text
    assert _db_alert(dbm, aid)["status"] == "open"


def test_non_overdue_alert_cannot_be_escalated(overdue_escalation_server):
    base, dbm = overdue_escalation_server
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    aid = _seed_alert(dbm, discovered_at=now)
    r = _post_escalate(base, _tok("co_oe", "co", "CO OE"), aid)
    assert r.status_code == 409, r.text
    assert "Only actively overdue" in r.text
    assert _db_alert(dbm, aid)["status"] == "open"


def test_closed_late_alert_cannot_be_escalated_even_when_sla_breached(overdue_escalation_server):
    base, dbm = overdue_escalation_server
    aid = _seed_alert(
        dbm,
        status="resolved",
        severity="critical",
        discovered_at="2026-01-05 09:00:00",
        resolved_at="2026-01-20 09:00:00",
    )
    detail = _detail(base, _tok("sco_oe", "sco", "SCO OE"), aid).json()
    assert detail["sla"]["sla_state"] == "closed"
    assert detail["sla"]["sla_breached"] is True
    r = _post_escalate(base, _tok("sco_oe", "sco", "SCO OE"), aid)
    assert r.status_code == 409, r.text
    assert _db_alert(dbm, aid)["status"] == "resolved"


@pytest.mark.parametrize("status", ["dismissed", "waived", "routed_to_edd", "routed_to_review", "closed"])
def test_terminal_alerts_cannot_be_escalated(overdue_escalation_server, status):
    base, dbm = overdue_escalation_server
    aid = _seed_alert(dbm, status=status, resolved_at="2026-01-20 09:00:00")
    r = _post_escalate(base, _tok("admin_oe", "admin", "Admin OE"), aid)
    assert r.status_code == 409, r.text
    assert _db_alert(dbm, aid)["status"] == status


def test_already_escalated_alert_does_not_create_duplicate_escalation(overdue_escalation_server):
    base, dbm = overdue_escalation_server
    aid = _seed_alert(dbm)
    co = _tok("co_oe", "co", "CO OE")
    first = _post_escalate(base, co, aid, reason="first escalation")
    assert first.status_code == 200, first.text
    second = _post_escalate(base, co, aid, reason="second escalation")
    assert second.status_code == 409, second.text
    assert "already escalated" in second.text
    assert len(_ledger_rows(dbm, aid)) == 1
    assert _audit_actions(dbm, aid).count("monitoring.alert.overdue_escalated") == 1


def test_blank_reason_is_rejected(overdue_escalation_server):
    base, dbm = overdue_escalation_server
    aid = _seed_alert(dbm)
    r = _post_escalate(base, _tok("co_oe", "co", "CO OE"), aid, reason="   ")
    assert r.status_code == 400, r.text
    assert _db_alert(dbm, aid)["status"] == "open"


def test_success_uses_canonical_escalation_transition_and_audits(overdue_escalation_server):
    base, dbm = overdue_escalation_server
    aid = _seed_alert(dbm, severity="critical")
    token = _tok("sco_oe", "sco", "SCO OE")
    before = _detail(base, token, aid).json()
    r = _post_escalate(base, token, aid, reason="SLA breached and senior review is required")
    assert r.status_code == 200, r.text
    after = _detail(base, token, aid).json()
    row = _db_alert(dbm, aid)
    assert row["status"] == "escalated"
    assert row["officer_action"] == "escalate_to_sco"
    assert row["status"] not in {"overdue", "overdue_escalated", "escalate_overdue"}
    assert after["sla"]["sla_due_at"] == before["sla"]["sla_due_at"]
    assert after["sla"]["sla_days"] == before["sla"]["sla_days"]
    actions = _audit_actions(dbm, aid)
    assert "monitoring.alert.escalated_to_sco" in actions
    assert "monitoring.alert.overdue_escalated" in actions
    ledger = _ledger_rows(dbm, aid)
    assert len(ledger) == 1
    assert ledger[0]["prior_status"] == "open"
    assert ledger[0]["new_status"] == "escalated"
    assert ledger[0]["sla_state"] == "overdue"
    assert ledger[0]["days_overdue"] is not None
    assert ledger[0]["sla_due_at"] == before["sla"]["sla_due_at"]
    assert ledger[0]["sla_days"] == before["sla"]["sla_days"]
    assert after["overdue_escalations"][0]["id"] == ledger[0]["id"]


def test_overdue_escalation_does_not_change_followup_due_date(overdue_escalation_server):
    base, dbm = overdue_escalation_server
    aid = _seed_alert(dbm)
    due_at = _seed_followup(dbm, aid)
    token = _tok("co_oe", "co", "CO OE")
    before = _detail(base, token, aid).json()["followups_summary"]["next_due_at"]
    assert before == due_at
    r = _post_escalate(base, token, aid, reason="Escalate while keeping follow-up due date")
    assert r.status_code == 200, r.text
    after = _detail(base, token, aid).json()["followups_summary"]["next_due_at"]
    assert after == before
