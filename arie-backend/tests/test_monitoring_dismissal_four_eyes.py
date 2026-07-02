"""M2.2 DISMISSAL-RISK-TIERING-FOUR-EYES (senior-override) tests.

Unit: tier classification (incl. ambiguous→Tier-1) and control gating.
API: CO→pending request; SCO/admin direct clear + senior_cleared audit;
same-user approval blocked; different approver executes terminal dismissal;
rejection keeps alert actionable; Tier-3 single-officer; escalate unaffected;
no new monitoring_alerts.status values.
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

import monitoring_dismissal_control as mdc

CANONICAL_STATUSES = {
    "open", "triaged", "assigned", "in_review", "escalated",
    "routed_to_review", "routed_to_edd", "dismissed", "resolved", "closed", "waived",
}


# ── Unit: tier classification ────────────────────────────────────────────────

def test_classify_tier_screening_and_documents():
    assert mdc.classify_alert_tier({"alert_type": "Sanctions Match", "severity": "critical"}) == 1
    assert mdc.classify_alert_tier({"alert_type": "pep_change", "severity": "medium"}) == 1
    assert mdc.classify_alert_tier({"alert_type": "watchlist", "severity": "high"}) == 1
    assert mdc.classify_alert_tier({"alert_type": "adverse_media", "severity": "high"}) == 1
    assert mdc.classify_alert_tier({"alert_type": "adverse_media", "severity": "low"}) == 3
    assert mdc.classify_alert_tier({"alert_type": "other", "summary": "Proliferation financing concern"}) == 1
    assert mdc.classify_alert_tier({"alert_type": "other", "summary": "terrorist financing exposure"}) == 1
    # ambiguous screening-ish → Tier 1 fail-safe
    assert mdc.classify_alert_tier({"alert_type": "screening_match_unknown"}) == 1
    # document-material identity → Tier 2
    assert mdc.classify_alert_tier({"alert_type": "document_expired", "summary": "passport (x) has expired"}) == 2
    # non-identity expired → Tier 3
    assert mdc.classify_alert_tier({"alert_type": "document_expired", "summary": "utility_bill (x) has expired"}) == 3
    assert mdc.classify_alert_tier({"alert_type": "document_expiring_soon", "summary": "passport expires soon"}) == 3
    assert mdc.classify_alert_tier({"alert_type": "document_stale", "summary": "brochure is stale"}) == 3
    assert mdc.classify_alert_tier(None) == 3


def test_requires_control_matrix():
    sanctions = {"alert_type": "sanctions_change", "severity": "critical"}
    docid = {"alert_type": "document_expired", "summary": "passport has expired"}
    lowdoc = {"alert_type": "document_expiring_soon", "summary": "passport expires soon"}
    # clearing on tier 1 → controlled
    assert mdc.requires_control(sanctions, action="save_decision", outcome="false_positive") is True
    assert mdc.requires_control(sanctions, action="dismiss", dismissal_reason="other") is True
    # escalation/info are not clearing → never controlled
    assert mdc.requires_control(sanctions, action="save_decision", outcome="route_to_edd") is False
    assert mdc.requires_control(sanctions, action="save_decision", outcome="request_further_information") is False
    # tier 2 waive controlled; tier 2 obvious duplicate single-officer
    assert mdc.requires_control(docid, action="save_decision", outcome="waive_with_reason") is True
    assert mdc.requires_control(docid, action="dismiss", dismissal_reason="duplicate") is False
    # tier 3 never controlled
    assert mdc.requires_control(lowdoc, action="dismiss", dismissal_reason="false_positive") is False


# ── API harness (isolated sqlite + live tornado) ─────────────────────────────

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
def four_eyes_server():
    db_path = os.path.join(tempfile.gettempdir(), f"monitoring_4eyes_{os.getpid()}_{time.time_ns()}.db")
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
        ("co_fe", "co-fe@example.test", "CO FE", "co"),
        ("sco_fe", "sco-fe@example.test", "SCO FE", "sco"),
        ("sco2_fe", "sco2-fe@example.test", "SCO2 FE", "sco"),
        ("admin_fe", "admin-fe@example.test", "Admin FE", "admin"),
    ]:
        conn.execute(
            "INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status) VALUES (?,?,?,?,?, 'active')",
            (uid, email, "unused", name, role),
        )
    conn.execute(
        "INSERT OR REPLACE INTO applications (id, ref, company_name, status, is_fixture) VALUES ('app-fe','FE-REF','FE Ltd','approved',0)"
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
            raise RuntimeError("four_eyes_server test harness failed to start within 3s")
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


_next_alert_id = [9600]


def _seed_alert(db_module, alert_type, severity, summary):
    aid = _next_alert_id[0]
    _next_alert_id[0] += 1
    conn = db_module.get_db()
    try:
        conn.execute(
            """
            INSERT INTO monitoring_alerts (id, application_id, client_name, alert_type,
                severity, status, detected_by, summary, discovered_via, source_reference)
            VALUES (?, 'app-fe', 'FE Ltd', ?, ?, 'open', 'test', ?, 'manual', ?)
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


def _patch(base, tok, aid, body):
    return requests.patch(f"{base}/api/monitoring/alerts/{aid}", headers=_hdr(tok), json=body, timeout=10)


# ── CO → pending request ─────────────────────────────────────────────────────

def test_co_tier1_clear_creates_request_and_does_not_dismiss(four_eyes_server):
    base, dbm = four_eyes_server
    aid = _seed_alert(dbm, "sanctions_change", "critical", "New sanctions hit")
    co = _tok("co_fe", "co", "CO FE")
    r = _patch(base, co, aid, {"action": "save_decision", "outcome": "false_positive",
                               "note": "Looks like a name-only match", "evidence_ref": "DOB mismatch"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "review_requested"
    assert body["result"]["pending_second_review"] is True
    assert _status(dbm, aid) == "open"  # NOT dismissed
    assert "monitoring.alert.dismissal_requested" in _audit_actions(dbm, aid)


def test_co_tier1_request_requires_evidence(four_eyes_server):
    base, dbm = four_eyes_server
    aid = _seed_alert(dbm, "sanctions_change", "critical", "New sanctions hit")
    co = _tok("co_fe", "co", "CO FE")
    r = _patch(base, co, aid, {"action": "dismiss", "dismissal_reason": "false_positive", "reason": "no evidence given"})
    assert r.status_code == 400
    assert "evidence" in r.json()["error"].lower()


# ── SCO/admin direct clear ───────────────────────────────────────────────────

def test_sco_direct_clear_dismisses_and_audits_senior_cleared(four_eyes_server):
    base, dbm = four_eyes_server
    aid = _seed_alert(dbm, "sanctions_change", "critical", "New sanctions hit")
    sco = _tok("sco_fe", "sco", "SCO FE")
    r = _patch(base, sco, aid, {"action": "save_decision", "outcome": "false_positive",
                                "note": "DOB and nationality mismatch confirmed", "evidence_ref": "passport p.2"})
    assert r.status_code == 200, r.text
    assert _status(dbm, aid) == "dismissed"
    actions = _audit_actions(dbm, aid)
    assert "monitoring.alert.dismissal_senior_cleared" in actions
    # verify the bypass flag is recorded
    conn = dbm.get_db()
    try:
        detail = conn.execute(
            "SELECT detail FROM audit_log WHERE target = ? AND action = 'monitoring.alert.dismissal_senior_cleared'",
            (f"monitoring_alert:{aid}",)).fetchone()["detail"]
    finally:
        conn.close()
    assert json.loads(detail)["second_review_bypassed"] is True


def test_sco_direct_clear_requires_evidence_on_tier1(four_eyes_server):
    base, dbm = four_eyes_server
    aid = _seed_alert(dbm, "sanctions_change", "critical", "New sanctions hit")
    sco = _tok("sco_fe", "sco", "SCO FE")
    r = _patch(base, sco, aid, {"action": "save_decision", "outcome": "false_positive", "note": "mismatch"})
    assert r.status_code == 400
    assert "evidence" in r.json()["error"].lower()
    assert _status(dbm, aid) == "open"


# ── Approval flow + same-user block ──────────────────────────────────────────

def test_different_sco_can_approve_and_execute(four_eyes_server):
    base, dbm = four_eyes_server
    aid = _seed_alert(dbm, "pep_change", "high", "PEP hit")
    co = _tok("co_fe", "co", "CO FE")
    sco = _tok("sco_fe", "sco", "SCO FE")
    r = _patch(base, co, aid, {"action": "save_decision", "outcome": "false_positive",
                               "note": "different person", "evidence_ref": "registry extract"})
    req_id = r.json()["result"]["review_request_id"]
    ap = requests.post(f"{base}/api/monitoring/review-requests/{req_id}/approve",
                       headers=_hdr(sco), json={"approval_note": "concur"}, timeout=10)
    assert ap.status_code == 200, ap.text
    assert _status(dbm, aid) == "dismissed"
    assert "monitoring.alert.dismissal_approved" in _audit_actions(dbm, aid)


def test_same_user_cannot_approve_own_request(four_eyes_server):
    base, dbm = four_eyes_server
    aid = _seed_alert(dbm, "sanctions_change", "critical", "sanctions hit")
    sco = _tok("sco_fe", "sco", "SCO FE")
    # SCO elects second review on their own clear → creates a pending request
    r = _patch(base, sco, aid, {"action": "save_decision", "outcome": "false_positive",
                                "note": "please double-check", "evidence_ref": "note",
                                "send_for_second_review": True})
    assert r.json()["status"] == "review_requested"
    req_id = r.json()["result"]["review_request_id"]
    ap = requests.post(f"{base}/api/monitoring/review-requests/{req_id}/approve",
                       headers=_hdr(sco), json={"approval_note": "self"}, timeout=10)
    assert ap.status_code == 403
    assert _status(dbm, aid) == "open"
    assert "monitoring.alert.dismissal_blocked" in _audit_actions(dbm, aid)


def test_rejection_keeps_alert_actionable(four_eyes_server):
    base, dbm = four_eyes_server
    aid = _seed_alert(dbm, "pep_change", "high", "PEP hit")
    co = _tok("co_fe", "co", "CO FE")
    admin = _tok("admin_fe", "admin", "Admin FE")
    r = _patch(base, co, aid, {"action": "save_decision", "outcome": "false_positive",
                               "note": "maybe fp", "evidence_ref": "note"})
    req_id = r.json()["result"]["review_request_id"]
    rej = requests.post(f"{base}/api/monitoring/review-requests/{req_id}/reject",
                        headers=_hdr(admin), json={"rejection_reason": "insufficient evidence"}, timeout=10)
    assert rej.status_code == 200, rej.text
    assert _status(dbm, aid) == "open"
    assert "monitoring.alert.dismissal_rejected" in _audit_actions(dbm, aid)


# ── Tier-3 single-officer + escalation unaffected ────────────────────────────

def test_tier3_low_risk_dismissal_is_single_officer(four_eyes_server):
    base, dbm = four_eyes_server
    aid = _seed_alert(dbm, "document_expiring_soon", "low", "passport expires soon")
    co = _tok("co_fe", "co", "CO FE")
    r = _patch(base, co, aid, {"action": "dismiss", "dismissal_reason": "no_action_needed", "reason": "renewed offline"})
    assert r.status_code == 200, r.text
    assert _status(dbm, aid) == "dismissed"
    assert "monitoring.alert.dismissal_requested" not in _audit_actions(dbm, aid)


def test_escalation_on_tier1_is_single_officer(four_eyes_server):
    base, dbm = four_eyes_server
    aid = _seed_alert(dbm, "sanctions_change", "critical", "sanctions hit")
    co = _tok("co_fe", "co", "CO FE")
    r = _patch(base, co, aid, {"action": "save_decision", "outcome": "route_to_edd", "note": "escalating for EDD"})
    assert r.status_code == 200, r.text
    assert _status(dbm, aid) == "routed_to_edd"
    assert "monitoring.alert.dismissal_requested" not in _audit_actions(dbm, aid)


# ── No new stored status values ──────────────────────────────────────────────

def test_no_new_monitoring_alert_status_values(four_eyes_server):
    _base, dbm = four_eyes_server
    conn = dbm.get_db()
    try:
        rows = conn.execute("SELECT DISTINCT status FROM monitoring_alerts").fetchall()
    finally:
        conn.close()
    for r in rows:
        assert r["status"] in CANONICAL_STATUSES, r["status"]
