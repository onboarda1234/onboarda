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
    "routed_to_review", "routed_to_edd", "dismissed", "resolved", "waived",
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
    # RDI-008 reconciliation: a CRITICAL alert is ALWAYS controlled, even a
    # tier-2 obvious-duplicate or an otherwise tier-3 shape, so M2.2 and the
    # service-layer critical-dismissal gate always agree.
    crit_dup = {"alert_type": "document_expired", "severity": "critical",
                "summary": "licence has expired"}
    assert mdc.requires_control(crit_dup, action="dismiss", dismissal_reason="duplicate") is True
    crit_odd = {"alert_type": "misc_flag", "severity": "critical", "summary": "n/a"}
    assert mdc.requires_control(crit_odd, action="dismiss", dismissal_reason="false_positive") is True


def test_requires_evidence_matrix():
    """Codex-validation follow-up (2026-07-25): evidence is mandatory for Tier 1
    AND for ANY critical-severity alert — a senior direct-clear of a critical
    tier-2/3 alert without evidence was a fail-open."""
    tier1 = {"alert_type": "sanctions_change", "severity": "critical"}
    crit_t2 = {"alert_type": "document_expired", "severity": "critical",
               "summary": "licence has expired"}
    crit_t3 = {"alert_type": "misc_flag", "severity": "critical", "summary": "n/a"}
    plain_t2 = {"alert_type": "document_expired", "severity": "high",
                "summary": "passport has expired"}
    assert mdc.requires_evidence(1, tier1) is True
    assert mdc.requires_evidence(2, crit_t2) is True   # critical forces evidence
    assert mdc.requires_evidence(3, crit_t3) is True   # critical forces evidence
    assert mdc.requires_evidence(2, plain_t2) is False  # non-critical tier 2 unchanged


def test_senior_clear_of_critical_tier2_requires_evidence(db):
    """record_senior_clear / create_pending_request must refuse a
    critical-severity clear with no evidence, regardless of tier (the Codex
    fail-open scenario: severity=critical, tier-2 duplicate, senior actor,
    notes but no evidence)."""
    crit_t2 = {"id": "ma-crit-t2", "alert_type": "document_expired",
               "severity": "critical", "summary": "licence has expired"}
    with pytest.raises(mdc.DismissalControlError):
        mdc.record_senior_clear(
            db, alert=crit_t2, tier=2, requested_outcome="dismiss",
            dismissal_reason="duplicate", rationale="looks duplicate",
            evidence_ref="",  # no evidence → must refuse
            user={"sub": "sco-1", "role": "sco"},
            audit_writer=lambda *a, **k: None,
        )
    with pytest.raises(mdc.DismissalControlError):
        mdc.create_pending_request(
            db, alert=crit_t2, tier=2, requested_outcome="dismiss",
            dismissal_reason="duplicate", rationale="looks duplicate",
            evidence_ref="",  # no evidence → must refuse
            user={"sub": "co-1", "role": "co"},
            audit_writer=lambda *a, **k: None,
        )


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
        if thread:
            from tests.conftest import shutdown_test_http_server
            shutdown_test_http_server(thread, server_ref)
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


def _seed_screening_case(db_module, aid):
    case_identifier = f"provider-case-fe-{aid}"
    provider = "complyadvantage"
    conn = db_module.get_db()
    try:
        conn.execute(
            """
            INSERT INTO screening_reviews
                (application_id, subject_type, subject_name, disposition,
                 disposition_code, rationale, reviewer_id, reviewer_name)
            VALUES ('app-fe', 'company', ?, 'cleared', 'false_positive',
                    'Controlled state-machine fixture', 'sco_fe', 'SCO FE')
            """,
            (f"Alert {aid}",),
        )
        conn.execute(
            "UPDATE monitoring_alerts "
            "SET provider = ?, case_identifier = ?, source_reference = ? "
            "WHERE id = ?",
            (
                provider,
                case_identifier,
                json.dumps(
                    {
                        "provider": provider,
                        "case_identifier": case_identifier,
                    },
                    sort_keys=True,
                ),
                aid,
            ),
        )
        conn.execute(
            """
            INSERT INTO monitoring_alert_evidence
                (monitoring_alert_id, application_id, provider,
                 case_identifier, evidence_type, evidence_json, evidence_hash)
            VALUES (?, 'app-fe', ?, ?, 'screening_case', ?, ?)
            """,
            (
                aid,
                provider,
                case_identifier,
                json.dumps(
                    {
                        "provider": provider,
                        "case_identifier": case_identifier,
                    },
                    sort_keys=True,
                ),
                f"fixture-screening-evidence-{aid}",
            ),
        )
        conn.commit()
        return case_identifier
    finally:
        conn.close()


def _seed_document(db_module, aid):
    document_id = f"doc-fe-{aid}"
    conn = db_module.get_db()
    try:
        conn.execute(
            """
            INSERT INTO documents
                (id, application_id, doc_type, doc_name, file_path)
            VALUES (?, 'app-fe', 'passport', ?, ?)
            """,
            (document_id, f"Passport {aid}", f"/fixtures/{document_id}.pdf"),
        )
        conn.execute(
            "UPDATE monitoring_alerts SET source_reference = ? WHERE id = ?",
            (f"document:{document_id}", aid),
        )
        conn.commit()
        return document_id
    finally:
        conn.close()


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


def _review_requests(db_module, aid):
    conn = db_module.get_db()
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM monitoring_alert_review_requests "
                "WHERE alert_id = ? ORDER BY id",
                (aid,),
            ).fetchall()
        ]
    finally:
        conn.close()


def _patch(base, tok, aid, body):
    return requests.patch(f"{base}/api/monitoring/alerts/{aid}", headers=_hdr(tok), json=body, timeout=10)


def test_pending_request_requires_authoritative_nonblank_maker(
    four_eyes_server,
):
    _base, db_module = four_eyes_server
    alert_id = _seed_alert(
        db_module,
        "manual",
        "medium",
        "Direct helper maker validation.",
    )
    conn = db_module.get_db()
    try:
        alert = dict(
            conn.execute(
                "SELECT * FROM monitoring_alerts WHERE id = ?",
                (alert_id,),
            ).fetchone()
        )
        with pytest.raises(
            mdc.DismissalControlError,
            match="active Compliance Officer",
        ):
            mdc.create_pending_request(
                conn,
                alert=alert,
                tier=3,
                requested_outcome="dismiss",
                dismissal_reason="other",
                rationale="This request has no authenticated maker.",
                evidence_ref="",
                transition_evidence={},
                user={},
                audit_writer=lambda *_args, **_kwargs: None,
            )
        assert conn.execute(
            "SELECT COUNT(*) AS count FROM monitoring_alert_review_requests "
            "WHERE alert_id = ?",
            (alert_id,),
        ).fetchone()["count"] == 0
    finally:
        conn.rollback()
        conn.close()


# ── CO → pending request ─────────────────────────────────────────────────────

def test_co_tier1_clear_creates_request_and_does_not_dismiss(four_eyes_server):
    base, dbm = four_eyes_server
    aid = _seed_alert(dbm, "sanctions_change", "critical", "New sanctions hit")
    _seed_screening_case(dbm, aid)
    co = _tok("co_fe", "co", "CO FE")
    r = _patch(base, co, aid, {"action": "save_decision", "outcome": "false_positive",
                               "note": "Looks like a name-only match",
                               "evidence_ref": "DOB mismatch"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "review_requested"
    assert body["result"]["pending_second_review"] is True
    assert _status(dbm, aid) == "open"  # NOT dismissed
    assert "monitoring.alert.dismissal_requested" in _audit_actions(dbm, aid)


def test_co_tier1_request_requires_evidence(four_eyes_server):
    base, dbm = four_eyes_server
    aid = _seed_alert(dbm, "sanctions_change", "critical", "New sanctions hit")
    _seed_screening_case(dbm, aid)
    co = _tok("co_fe", "co", "CO FE")
    r = _patch(base, co, aid, {"action": "dismiss", "dismissal_reason": "false_positive",
                               "reason": "no evidence given"})
    assert r.status_code == 400
    assert "evidence" in r.json()["error"].lower()


# ── SCO/admin direct clear ───────────────────────────────────────────────────

def test_sco_direct_clear_dismisses_and_audits_senior_cleared(four_eyes_server):
    base, dbm = four_eyes_server
    aid = _seed_alert(dbm, "sanctions_change", "critical", "New sanctions hit")
    _seed_screening_case(dbm, aid)
    sco = _tok("sco_fe", "sco", "SCO FE")
    r = _patch(base, sco, aid, {"action": "save_decision", "outcome": "false_positive",
                                "note": "DOB and nationality mismatch confirmed",
                                "evidence_ref": "passport p.2"})
    assert r.status_code == 200, r.text
    assert _status(dbm, aid) == "dismissed"
    actions = _audit_actions(dbm, aid)
    assert "monitoring.alert.dismissal_senior_cleared" in actions
    assert "monitoring.alert.status_transition" in actions
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
    _seed_screening_case(dbm, aid)
    sco = _tok("sco_fe", "sco", "SCO FE")
    r = _patch(base, sco, aid, {"action": "save_decision", "outcome": "false_positive",
                                "note": "mismatch"})
    assert r.status_code == 400
    assert "evidence" in r.json()["error"].lower()
    assert _status(dbm, aid) == "open"


def test_direct_senior_clear_helper_rejects_forged_co_role(four_eyes_server):
    """Reusable service callers cannot bypass endpoint RBAC with a forged role."""
    _base, dbm = four_eyes_server
    aid = _seed_alert(
        dbm,
        "misc_flag",
        "critical",
        "Direct helper role-integrity probe",
    )
    audit_calls = []
    conn = dbm.get_db()
    try:
        with pytest.raises(mdc.DismissalControlError) as excinfo:
            mdc.record_senior_clear(
                conn,
                alert={"id": aid},
                tier=3,
                requested_outcome="dismiss",
                dismissal_reason="false_positive",
                rationale="Forged senior direct-clear attempt.",
                evidence_ref="Officer evidence note.",
                transition_evidence={},
                user={"sub": "co_fe", "role": "sco"},
                audit_writer=lambda *args, **kwargs: audit_calls.append(
                    (args, kwargs)
                ),
                commit=False,
            )
        assert excinfo.value.status_code == 403
        conn.rollback()
    finally:
        conn.close()

    assert audit_calls == []
    assert _review_requests(dbm, aid) == []
    assert _status(dbm, aid) == "open"


# ── Approval flow + same-user block ──────────────────────────────────────────

def test_different_sco_can_approve_and_execute(four_eyes_server):
    base, dbm = four_eyes_server
    aid = _seed_alert(dbm, "pep_change", "high", "PEP hit")
    case_identifier = _seed_screening_case(dbm, aid)
    co = _tok("co_fe", "co", "CO FE")
    sco = _tok("sco_fe", "sco", "SCO FE")
    r = _patch(base, co, aid, {"action": "save_decision", "outcome": "false_positive",
                               "note": "different person",
                               "evidence_ref": "registry extract"})
    req_id = r.json()["result"]["review_request_id"]
    ap = requests.post(f"{base}/api/monitoring/review-requests/{req_id}/approve",
                       headers=_hdr(sco), json={"approval_note": "concur"}, timeout=10)
    assert ap.status_code == 200, ap.text
    assert _status(dbm, aid) == "dismissed"
    assert "monitoring.alert.dismissal_approved" in _audit_actions(dbm, aid)
    conn = dbm.get_db()
    try:
        transition_detail = json.loads(
            conn.execute(
                "SELECT detail FROM audit_log WHERE target = ? "
                "AND action = 'monitoring.alert.status_transition' "
                "ORDER BY id DESC LIMIT 1",
                (f"monitoring_alert:{aid}",),
            ).fetchone()["detail"]
        )
    finally:
        conn.close()
    assert transition_detail["evidence"]["review_request_id"] == req_id
    assert transition_detail["evidence"]["case_identifier"] == case_identifier
    assert "screening_case_id" not in transition_detail["evidence"]


def test_approval_rejects_request_after_alert_source_status_changes(
    four_eyes_server,
):
    """An open-state request cannot approve after a separate escalation.

    Approval locks the alert first and then the request, revalidates the exact
    creation-time source status, and returns 409 without changing either row.
    """
    base, dbm = four_eyes_server
    aid = _seed_alert(
        dbm,
        "misc_flag",
        "critical",
        "Manual material concern",
    )
    co = _tok("co_fe", "co", "CO FE")
    sco = _tok("sco_fe", "sco", "SCO FE")

    requested = _patch(
        base,
        co,
        aid,
        {
            "action": "save_decision",
            "outcome": "false_positive",
            "note": "Potential false positive after manual review.",
            "evidence_ref": "Registry extract",
        },
    )
    assert requested.status_code == 200, requested.text
    request_id = requested.json()["result"]["review_request_id"]
    assert _review_requests(dbm, aid)[0]["source_alert_status"] == "open"

    escalated = _patch(
        base,
        co,
        aid,
        {
            "action": "save_decision",
            "outcome": "escalate_to_sco",
            "note": "Material concern requires senior review.",
        },
    )
    assert escalated.status_code == 200, escalated.text
    assert _status(dbm, aid) == "escalated"

    conn = dbm.get_db()
    try:
        alert_before = dict(
            conn.execute(
                "SELECT status, officer_action, officer_notes, reviewed_at, "
                "reviewed_by, resolved_at FROM monitoring_alerts WHERE id = ?",
                (aid,),
            ).fetchone()
        )
        request_before = dict(
            conn.execute(
                "SELECT state, source_alert_status, approved_by, approved_at, "
                "approval_note, rejection_reason "
                "FROM monitoring_alert_review_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
        )
    finally:
        conn.close()

    approved = requests.post(
        f"{base}/api/monitoring/review-requests/{request_id}/approve",
        headers=_hdr(sco),
        json={"approval_note": "Concur"},
        timeout=10,
    )
    assert approved.status_code == 409, approved.text
    assert "status changed" in approved.text.lower()

    conn = dbm.get_db()
    try:
        alert_after = dict(
            conn.execute(
                "SELECT status, officer_action, officer_notes, reviewed_at, "
                "reviewed_by, resolved_at FROM monitoring_alerts WHERE id = ?",
                (aid,),
            ).fetchone()
        )
        request_after = dict(
            conn.execute(
                "SELECT state, source_alert_status, approved_by, approved_at, "
                "approval_note, rejection_reason "
                "FROM monitoring_alert_review_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
        )
    finally:
        conn.close()
    assert alert_after == alert_before
    assert request_after == request_before == {
        "state": "pending",
        "source_alert_status": "open",
        "approved_by": None,
        "approved_at": None,
        "approval_note": None,
        "rejection_reason": None,
    }
    assert "monitoring.alert.dismissal_approved" not in _audit_actions(dbm, aid)


def test_senior_clear_rolls_back_control_record_when_linked_evidence_is_missing(
    four_eyes_server,
):
    base, dbm = four_eyes_server
    aid = _seed_alert(dbm, "sanctions_change", "critical", "New sanctions hit")
    _seed_screening_case(dbm, aid)
    sco = _tok("sco_fe", "sco", "SCO FE")
    response = _patch(
        base,
        sco,
        aid,
        {
            "action": "save_decision",
            "outcome": "false_positive",
            "note": "False-positive rationale",
            "evidence_ref": "Officer evidence note",
            "screening_case_id": 999999999,
        },
    )
    assert response.status_code == 404, response.text
    assert _status(dbm, aid) == "open"
    assert _review_requests(dbm, aid) == []
    assert "monitoring.alert.dismissal_senior_cleared" not in _audit_actions(
        dbm, aid
    )


def test_approval_failure_rolls_back_approved_marker_and_transition(
    four_eyes_server,
):
    base, dbm = four_eyes_server
    aid = _seed_alert(dbm, "pep_change", "high", "PEP hit")
    _seed_screening_case(dbm, aid)
    co = _tok("co_fe", "co", "CO FE")
    sco = _tok("sco_fe", "sco", "SCO FE")
    requested = _patch(
        base,
        co,
        aid,
        {
            "action": "save_decision",
            "outcome": "false_positive",
            "note": "Potential false positive",
            "evidence_ref": "Registry note",
            "screening_case_id": 999999998,
        },
    )
    assert requested.status_code == 200, requested.text
    request_id = requested.json()["result"]["review_request_id"]

    approved = requests.post(
        f"{base}/api/monitoring/review-requests/{request_id}/approve",
        headers=_hdr(sco),
        json={"approval_note": "Concur"},
        timeout=10,
    )
    assert approved.status_code == 404, approved.text
    assert _status(dbm, aid) == "open"
    assert _review_requests(dbm, aid)[0]["state"] == "pending"
    assert "monitoring.alert.dismissal_approved" not in _audit_actions(dbm, aid)


def test_same_user_cannot_approve_own_request(four_eyes_server):
    base, dbm = four_eyes_server
    aid = _seed_alert(dbm, "sanctions_change", "critical", "sanctions hit")
    _seed_screening_case(dbm, aid)
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
    _seed_screening_case(dbm, aid)
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
    document_id = _seed_document(dbm, aid)
    co = _tok("co_fe", "co", "CO FE")
    r = _patch(base, co, aid, {"action": "dismiss", "dismissal_reason": "no_action_needed",
                               "reason": "renewed offline", "document_id": document_id})
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


def test_severity_escalation_between_request_and_approval_blocks_evidence_less_clear(four_eyes_server):
    """Codex round-2 TOCTOU: a request created while the alert was NON-critical
    (evidence optional) must NOT execute an evidence-less clear after the alert
    escalates to CRITICAL. Approval must re-check requires_evidence against the
    CURRENT alert and refuse (409), leaving the alert open and the request
    pending."""
    base, dbm = four_eyes_server
    # Tier-2 shape, non-critical at request time.
    aid = _seed_alert(dbm, "document_expired", "high", "passport has expired")
    document_id = _seed_document(dbm, aid)
    co = _tok("co_fe", "co", "CO FE")
    sco = _tok("sco_fe", "sco", "SCO FE")

    # CO requests a controlled clear with rationale but NO evidence — accepted
    # because the alert is non-critical tier 2 at creation.
    r = _patch(base, co, aid, {"action": "dismiss", "dismissal_reason": "false_positive",
                               "reason": "expired doc superseded",
                               "document_id": document_id})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "review_requested"
    req_id = r.json()["result"]["review_request_id"]

    # The alert escalates to CRITICAL before approval.
    conn = dbm.get_db()
    conn.execute("UPDATE monitoring_alerts SET severity = 'critical' WHERE id = ?", (aid,))
    conn.commit()
    conn.close()

    # A different SCO approves → must now be refused, not executed.
    ap = requests.post(f"{base}/api/monitoring/review-requests/{req_id}/approve",
                       headers=_hdr(sco), json={"approval_note": "concur"}, timeout=10)
    assert ap.status_code == 409, ap.text
    assert "evidence" in ap.text.lower()
    assert _status(dbm, aid) == "open", "the now-CRITICAL alert must not be dismissed"

    # The request survives as pending so it can be rejected + resubmitted with evidence.
    conn = dbm.get_db()
    state = conn.execute(
        "SELECT state FROM monitoring_alert_review_requests WHERE id = ?", (req_id,)
    ).fetchone()["state"]
    conn.close()
    assert state == "pending"


def test_approval_with_evidence_still_executes_after_escalation(four_eyes_server):
    """Counterpart: a request that DID carry evidence executes normally even if
    the alert escalated to CRITICAL before approval."""
    base, dbm = four_eyes_server
    aid = _seed_alert(dbm, "document_expired", "high", "passport has expired")
    document_id = _seed_document(dbm, aid)
    co = _tok("co_fe", "co", "CO FE")
    sco = _tok("sco_fe", "sco", "SCO FE")

    r = _patch(base, co, aid, {"action": "dismiss", "dismissal_reason": "false_positive",
                               "reason": "expired doc superseded",
                               "evidence_ref": "replacement passport on file",
                               "document_id": document_id})
    assert r.status_code == 200, r.text
    req_id = r.json()["result"]["review_request_id"]

    conn = dbm.get_db()
    conn.execute("UPDATE monitoring_alerts SET severity = 'critical' WHERE id = ?", (aid,))
    conn.commit()
    conn.close()

    ap = requests.post(f"{base}/api/monitoring/review-requests/{req_id}/approve",
                       headers=_hdr(sco), json={"approval_note": "concur"}, timeout=10)
    assert ap.status_code == 200, ap.text
    assert _status(dbm, aid) == "dismissed"


def test_assert_evidence_current_unit():
    """Unit: assert_evidence_current refuses a critical current-alert with empty
    stored evidence (409) and passes with evidence or a non-critical alert."""
    crit = {"alert_type": "document_expired", "severity": "critical",
            "summary": "licence has expired"}
    noncrit = {"alert_type": "document_expired", "severity": "high",
               "summary": "passport has expired"}
    with pytest.raises(mdc.DismissalControlError) as excinfo:
        mdc.assert_evidence_current(crit, "")
    assert excinfo.value.status_code == 409
    with pytest.raises(mdc.DismissalControlError):
        mdc.assert_evidence_current(crit, "   ")
    mdc.assert_evidence_current(crit, "SAR ref 42")  # must not raise
    mdc.assert_evidence_current(noncrit, "")  # non-critical tier-2 → no evidence needed


def test_request_source_status_binding_unit():
    request = {"source_alert_status": "open"}
    mdc.assert_request_source_status_current(request, {"status": "open"})

    with pytest.raises(mdc.DismissalControlError) as changed:
        mdc.assert_request_source_status_current(
            request,
            {"status": "in_review"},
        )
    assert changed.value.status_code == 409
    assert "status changed" in str(changed.value).lower()

    with pytest.raises(mdc.DismissalControlError) as legacy:
        mdc.assert_request_source_status_current(
            {"source_alert_status": None},
            {"status": "open"},
        )
    assert legacy.value.status_code == 409
    assert "not bound" in str(legacy.value).lower()
