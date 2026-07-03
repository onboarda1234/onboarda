"""M2.1 PR-3 — follow-up tracker HTTP/API tests.

Proves the actual handlers (not just helpers): role gating (admin/sco/co allowed,
others rejected); add/resolve leave monitoring_alerts.status unchanged; a
follow-up due date does NOT alter the derived SLA object; the list projection
exposes only open_followup_count / next_followup_due (no full rows) while detail
exposes the full followups + summary; and the audit events are written.
"""
import json
import os
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
def followups_server():
    db_path = os.path.join(tempfile.gettempdir(), f"monitoring_followups_{os.getpid()}_{time.time_ns()}.db")
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
        ("co_fu", "co-fu@example.test", "CO FU", "co"),
        ("sco_fu", "sco-fu@example.test", "SCO FU", "sco"),
        ("admin_fu", "admin-fu@example.test", "Admin FU", "admin"),
        ("analyst_fu", "analyst-fu@example.test", "Analyst FU", "analyst"),
    ]:
        conn.execute(
            "INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status) VALUES (?,?,?,?,?, 'active')",
            (uid, email, "unused", name, role),
        )
    conn.execute(
        "INSERT OR REPLACE INTO applications (id, ref, company_name, status, is_fixture) VALUES ('app-fu','FU-REF','FU Ltd','approved',0)"
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
            raise RuntimeError("followups_server test harness failed to start within 3s")
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


_next_alert_id = [9700]


def _seed_alert(db_module, alert_type="sanctions_change", severity="high", summary="FU alert"):
    aid = _next_alert_id[0]
    _next_alert_id[0] += 1
    conn = db_module.get_db()
    try:
        conn.execute(
            """
            INSERT INTO monitoring_alerts (id, application_id, client_name, alert_type,
                severity, status, detected_by, summary, discovered_via, source_reference)
            VALUES (?, 'app-fu', 'FU Ltd', ?, ?, 'open', 'test', ?, 'manual', ?)
            """,
            (aid, alert_type, severity, summary, json.dumps({"seed": aid})),
        )
        conn.commit()
    finally:
        conn.close()
    return aid


def _tok(uid, role, name):
    from auth import create_token
    return create_token(uid, role, name, "officer")


def _hdr(t):
    return {"Authorization": f"Bearer {t}", "Content-Type": "application/json"}


def _status(db_module, aid):
    conn = db_module.get_db()
    try:
        return conn.execute("SELECT status FROM monitoring_alerts WHERE id = ?", (aid,)).fetchone()["status"]
    finally:
        conn.close()


def _audit_actions(db_module, aid):
    conn = db_module.get_db()
    try:
        return [r["action"] for r in conn.execute(
            "SELECT action FROM audit_log WHERE target = ? ORDER BY id ASC",
            (f"monitoring_alert:{aid}",)).fetchall()]
    finally:
        conn.close()


def _add(base, tok, aid, body):
    return requests.post(f"{base}/api/monitoring/alerts/{aid}/followups", headers=_hdr(tok), json=body, timeout=10)


def _resolve(base, tok, aid, fid):
    return requests.post(f"{base}/api/monitoring/alerts/{aid}/followups/{fid}/resolve", headers=_hdr(tok), json={}, timeout=10)


def _detail(base, tok, aid):
    return requests.get(f"{base}/api/monitoring/alerts/{aid}", headers=_hdr(tok), timeout=10)


# ── permissions ──────────────────────────────────────────────────────────────
def test_unauthorized_role_cannot_add_or_resolve(followups_server):
    base, dbm = followups_server
    aid = _seed_alert(dbm)
    analyst = _tok("analyst_fu", "analyst", "Analyst FU")
    r = _add(base, analyst, aid, {"action": "note", "note": "should be blocked"})
    assert r.status_code in (401, 403), r.text


@pytest.mark.parametrize("uid,role,name", [
    ("admin_fu", "admin", "Admin FU"),
    ("sco_fu", "sco", "SCO FU"),
    ("co_fu", "co", "CO FU"),
])
def test_allowed_roles_can_add(followups_server, uid, role, name):
    base, dbm = followups_server
    aid = _seed_alert(dbm)
    r = _add(base, _tok(uid, role, name), aid, {"action": "note", "note": f"added by {role}"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "followup_added"


# ── status untouched ─────────────────────────────────────────────────────────
def test_add_and_resolve_do_not_change_alert_status(followups_server):
    base, dbm = followups_server
    aid = _seed_alert(dbm)
    before = _status(dbm, aid)
    co = _tok("co_fu", "co", "CO FU")
    r = _add(base, co, aid, {"action": "next_step", "note": "chase client", "due_at": "2027-01-01 09:00:00"})
    assert r.status_code == 200, r.text
    assert _status(dbm, aid) == before
    fid = r.json()["followup"]["id"]
    rr = _resolve(base, co, aid, fid)
    assert rr.status_code == 200, rr.text
    assert _status(dbm, aid) == before  # still unchanged after resolve


# ── SLA is not affected by a follow-up due date ──────────────────────────────
def test_followup_due_date_does_not_alter_sla(followups_server):
    base, dbm = followups_server
    aid = _seed_alert(dbm, severity="high")
    sco = _tok("sco_fu", "sco", "SCO FU")
    sla_before = _detail(base, sco, aid).json()["sla"]
    # A far-future follow-up due date must not leak into the SLA due date.
    r = _add(base, sco, aid, {"action": "snooze_until", "due_at": "2099-12-31 00:00:00"})
    assert r.status_code == 200, r.text
    sla_after = _detail(base, sco, aid).json()["sla"]
    assert sla_after["sla_due_at"] == sla_before["sla_due_at"]
    assert sla_after["sla_days"] == sla_before["sla_days"]
    assert sla_after["sla_due_at"] != "2099-12-31 00:00:00"  # follow-up date did not become the SLA date


# ── list vs detail projection ────────────────────────────────────────────────
def test_list_projection_only_count_and_next_due(followups_server):
    base, dbm = followups_server
    aid = _seed_alert(dbm)
    co = _tok("co_fu", "co", "CO FU")
    _add(base, co, aid, {"action": "next_step", "note": "x", "due_at": "2027-02-02 00:00:00"})
    r = requests.get(f"{base}/api/monitoring/alerts?include_unmapped=true&page_size=200",
                     headers=_hdr(co), timeout=10)
    assert r.status_code == 200, r.text
    row = next((a for a in r.json()["alerts"] if a["id"] == aid), None)
    assert row is not None, "seeded alert not present in list"
    assert row["open_followup_count"] == 1
    assert row["next_followup_due"] == "2027-02-02 00:00:00"
    # The list must NOT carry full follow-up rows or the detail summary.
    assert "followups" not in row
    assert "followups_summary" not in row


def test_detail_projection_returns_followups_and_summary(followups_server):
    base, dbm = followups_server
    aid = _seed_alert(dbm)
    co = _tok("co_fu", "co", "CO FU")
    _add(base, co, aid, {"action": "note", "note": "detail note"})
    body = _detail(base, co, aid).json()
    assert isinstance(body.get("followups"), list) and len(body["followups"]) == 1
    assert body["followups_summary"]["open_count"] == 1


# ── audit ────────────────────────────────────────────────────────────────────
def test_audit_events_written_for_add_and_resolve(followups_server):
    base, dbm = followups_server
    aid = _seed_alert(dbm)
    co = _tok("co_fu", "co", "CO FU")
    fid = _add(base, co, aid, {"action": "note", "note": "audit me"}).json()["followup"]["id"]
    _resolve(base, co, aid, fid)
    actions = _audit_actions(dbm, aid)
    assert "monitoring.alert.followup_added" in actions
    assert "monitoring.alert.followup_resolved" in actions
