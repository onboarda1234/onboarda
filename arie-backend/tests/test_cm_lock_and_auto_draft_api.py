"""
API-level behavioural tests for PR-CM-LOCK-AND-AUTO-DRAFT-1.

These exercise the three real bypass endpoints over HTTP against a running
server and assert the approved profile is NOT mutated:

    PUT  /api/applications/:id
    POST /api/applications/:id/submit
    POST /api/applications/:id/corrections

Service-layer coverage lives in test_cm_lock_and_auto_draft.py; this file is
the proof that the handlers themselves are safe.
"""

import importlib
import json
import os
import socket
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

import pytest
import requests
import tornado.httpserver
import tornado.ioloop

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _sync_db_path(path):
    os.environ["DATABASE_URL"] = ""
    os.environ["DB_PATH"] = path
    for module_name in ("config", "db", "server"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "DB_PATH"):
            setattr(module, "DB_PATH", path)
        if module_name == "server" and module is not None and hasattr(module, "_CFG_DB_PATH"):
            setattr(module, "_CFG_DB_PATH", path)


def _fresh_db(path):
    _sync_db_path(path)
    import config as config_module
    import db as db_module

    importlib.reload(config_module)
    importlib.reload(db_module)
    db_module.init_db()
    conn = db_module.get_db()
    db_module.seed_initial_data(conn)
    conn.commit()
    return conn


def _find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def api_server(tmp_path):
    db_path = str(tmp_path / "cm_lock.db")
    conn = _fresh_db(db_path)
    conn.close()

    import server as server_module

    importlib.reload(server_module)
    app = server_module.make_app()
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
    yield f"http://127.0.0.1:{port}", db_path

    io_loop = server_ref.get("loop")
    srv = server_ref.get("server")
    if io_loop and srv:
        io_loop.add_callback(srv.stop)
        io_loop.add_callback(io_loop.stop)
    thread.join(timeout=2)


def _headers(role="admin"):
    from auth import create_token

    user_id = {"admin": "admin001", "sco": "sco001", "co": "co001"}.get(role, role)
    token = create_token(user_id, role, f"Test {role}", "officer")
    return {"Authorization": f"Bearer {token}"}


def _insert_case(db, *, status="approved", country="GB", sector="Technology", brn="BRN-001"):
    suffix = uuid.uuid4().hex[:8]
    client_id = f"client_{suffix}"
    app_id = f"app_{suffix}"
    ref = f"CM-LOCK-{suffix}"
    prescreening = {
        "screening_report": {"screening_mode": "live", "sanctions": {"matched": False}},
        "country_of_incorporation": country,
        "country": country,
        "sector": sector,
        "entity_type": "Limited Company",
        "ownership_structure": "Simple",
    }
    db.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"{client_id}@example.com", "hash", "CM Lock Test Ltd"),
    )
    db.execute(
        """INSERT INTO applications
           (id, ref, client_id, company_name, brn, country, sector, entity_type,
            ownership_structure, status, risk_level, final_risk_level, risk_score,
            prescreening_data, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            app_id, ref, client_id, "CM Lock Test Ltd", brn, country, sector,
            "Limited Company", "Simple", status, "MEDIUM", "MEDIUM", 45,
            json.dumps(prescreening),
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    db.execute(
        """INSERT INTO directors
           (id, application_id, person_key, first_name, last_name, full_name, nationality, is_pep, date_of_birth)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (f"dir_{suffix}", app_id, "dir1", "Alice", "Director", "Alice Director", "GB", "No", "1980-02-01"),
    )
    db.commit()
    return {"app_id": app_id, "ref": ref, "director_id": f"dir_{suffix}"}


def _live(db_path, app_id, column):
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        row = conn.execute(f"SELECT {column} FROM applications WHERE id = ?", (app_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _cr_count(db_path, app_id):
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM change_requests WHERE application_id = ?", (app_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def _cr_source(db_path, app_id):
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        row = conn.execute(
            "SELECT source, source_channel FROM change_requests WHERE application_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (app_id,),
        ).fetchone()
        return (row[0], row[1]) if row else (None, None)
    finally:
        conn.close()


def _latest_cr_materiality(db_path, app_id):
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """SELECT cr.materiality AS request_materiality,
                      cr.screening_required,
                      cr.risk_review_required,
                      cr.memo_addendum_hook,
                      cr.periodic_review_acceleration_hook,
                      cri.materiality AS item_materiality
                 FROM change_requests cr
                 JOIN change_request_items cri ON cri.request_id = cr.id
                WHERE cr.application_id = ?
                ORDER BY cr.created_at DESC, cri.id ASC
                LIMIT 1""",
            (app_id,),
        ).fetchone()
    finally:
        conn.close()


def _live_director(db_path, app_id, column):
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        row = conn.execute(
            f"SELECT {column} FROM directors WHERE application_id = ?", (app_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _audit_actions(db_path, target):
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        return [
            r[0] for r in conn.execute(
                "SELECT action FROM audit_log WHERE target = ?", (target,)
            ).fetchall()
        ]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PUT /api/applications/:id
# ---------------------------------------------------------------------------

def test_change_request_api_ignores_client_materiality_downgrade(api_server):
    base_url, db_path = api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    resp = requests.post(
        f"{base_url}/api/change-management/requests",
        headers=_headers("sco"),
        json={
            "application_id": case["app_id"],
            "source": "backoffice_manual",
            "source_channel": "backoffice",
            "reason": "API materiality downgrade regression",
            "items": [{
                "change_type": "ubo_change",
                "field_name": "ownership_pct",
                "old_value": "20",
                "new_value": "80",
                "materiality": "tier3",
            }],
        },
        timeout=5,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["materiality"] == "tier1"
    assert body["items"][0]["materiality"] == "tier1"
    assert body["downstream_actions"]["screening_required"] is True
    assert body["downstream_actions"]["risk_review_required"] is True
    assert body["downstream_actions"]["memo_addendum_hook"] is True
    assert body["downstream_actions"]["periodic_review_acceleration_hook"] is True

    persisted = _latest_cr_materiality(db_path, case["app_id"])
    assert persisted["request_materiality"] == "tier1"
    assert persisted["item_materiality"] == "tier1"
    assert persisted["screening_required"] == 1
    assert persisted["risk_review_required"] == 1
    assert persisted["memo_addendum_hook"] == 1
    assert persisted["periodic_review_acceleration_hook"] == 1


def test_put_material_edit_on_approved_drafts_cr(api_server):
    base_url, db_path = api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn, country="GB")
    conn.close()

    resp = requests.put(
        f"{base_url}/api/applications/{case['app_id']}",
        headers=_headers("co"),
        json={"country": "MT"},
        timeout=5,
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["action"] == "change_request_drafted"
    assert body["request_id"].startswith("CR-")

    # Live profile NOT mutated.
    assert _live(db_path, case["app_id"], "country") == "GB"
    assert _cr_count(db_path, case["app_id"]) == 1

    # Created CR uses the canonical CM taxonomy — no invented channel/source.
    import change_management as cm
    source, channel = _cr_source(db_path, case["app_id"])
    assert channel in cm.CHANGE_CHANNELS, channel
    assert source in cm.CHANGE_SOURCES, source

    # The auto-draft is audited.
    assert "Change Request Auto-Drafted" in _audit_actions(db_path, case["ref"])


def test_put_repeat_edit_is_idempotent(api_server):
    base_url, db_path = api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn, country="GB")
    conn.close()

    first = requests.put(
        f"{base_url}/api/applications/{case['app_id']}",
        headers=_headers("co"), json={"country": "MT"}, timeout=5,
    ).json()
    second = requests.put(
        f"{base_url}/api/applications/{case['app_id']}",
        headers=_headers("co"), json={"country": "MT"}, timeout=5,
    )
    assert second.status_code == 409, second.text
    body = second.json()
    assert body["action"] == "change_request_exists"
    assert body["request_id"] == first["request_id"]
    assert _cr_count(db_path, case["app_id"]) == 1


def test_put_on_non_locked_app_still_updates(api_server):
    base_url, db_path = api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn, status="under_review", country="GB")
    conn.close()

    resp = requests.put(
        f"{base_url}/api/applications/{case['app_id']}",
        headers=_headers("co"), json={"country": "MT"}, timeout=5,
    )
    assert resp.status_code == 200, resp.text
    # Existing behaviour preserved — non-locked app is updated in place.
    assert _live(db_path, case["app_id"], "country") == "MT"
    assert _cr_count(db_path, case["app_id"]) == 0


# ---------------------------------------------------------------------------
# POST /api/applications/:id/submit
# ---------------------------------------------------------------------------

def test_submit_on_approved_is_blocked(api_server):
    base_url, db_path = api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    resp = requests.post(
        f"{base_url}/api/applications/{case['app_id']}/submit",
        headers=_headers("co"), json={}, timeout=5,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["action"] == "submit_blocked_profile_locked"
    # Status must remain approved — not reset into pricing/submitted.
    assert _live(db_path, case["app_id"], "status") == "approved"
    # The blocked submit is audited.
    assert "Submit Blocked: Approved Profile Locked" in _audit_actions(db_path, case["ref"])


# ---------------------------------------------------------------------------
# POST /api/applications/:id/corrections
# ---------------------------------------------------------------------------

def test_material_application_correction_drafts_cr(api_server):
    base_url, db_path = api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn, sector="Technology")
    conn.close()

    resp = requests.post(
        f"{base_url}/api/applications/{case['app_id']}/corrections",
        headers=_headers("co"),
        json={
            "target_type": "application",
            "field_changes": {"sector": "Crypto / Digital Assets Exchange"},
            "correction_reason": "Registry evidence shows VASP business.",
            "evidence_source": "Business model review",
            "correction_note": "Sector corrected to verified sector.",
        },
        timeout=5,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["action"] == "change_request_drafted"
    assert _live(db_path, case["app_id"], "sector") == "Technology"
    assert _cr_count(db_path, case["app_id"]) == 1


def test_material_person_correction_is_blocked_with_guidance(api_server):
    base_url, db_path = api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    resp = requests.post(
        f"{base_url}/api/applications/{case['app_id']}/corrections",
        headers=_headers("co"),
        json={
            "target_type": "director",
            "target_id": case["director_id"],
            "field_changes": {"last_name": "Smith"},
            "correction_reason": "Passport shows corrected surname.",
            "evidence_source": "Passport",
            "correction_note": "Surname corrected.",
        },
        timeout=5,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["action"] == "change_request_required"
    # No auto-draft for person targets in PR-1, and the directors row is
    # genuinely unchanged (uses the real director id, not the person_key).
    assert _cr_count(db_path, case["app_id"]) == 0
    assert _live_director(db_path, case["app_id"], "last_name") == "Director"


def test_brn_correction_on_approved_routes_to_cm(api_server):
    base_url, db_path = api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn, brn="BRN-001")
    conn.close()

    # BRN / registration number is a legal-identity field. Even though the
    # officer-correction heuristic tiers it as tier3, it MUST route through
    # Change Management on an approved profile — never a direct mutation.
    resp = requests.post(
        f"{base_url}/api/applications/{case['app_id']}/corrections",
        headers=_headers("co"),
        json={"target_type": "application", "field_changes": {"brn": "BRN-999"}},
        timeout=5,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["action"] == "change_request_drafted"
    assert _live(db_path, case["app_id"], "brn") == "BRN-001"
    assert _cr_count(db_path, case["app_id"]) == 1


# ---------------------------------------------------------------------------
# Fail-closed: CM module unavailable must NOT open the bypass
# ---------------------------------------------------------------------------

def test_put_on_approved_fails_closed_when_cm_unavailable(api_server):
    base_url, db_path = api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn, country="GB")
    conn.close()

    import server as server_module
    saved_flag, saved_cm = server_module.HAS_CHANGE_MANAGEMENT, server_module.cm
    server_module.HAS_CHANGE_MANAGEMENT = False
    server_module.cm = None
    try:
        resp = requests.put(
            f"{base_url}/api/applications/{case['app_id']}",
            headers=_headers("co"), json={"country": "MT"}, timeout=5,
        )
    finally:
        server_module.HAS_CHANGE_MANAGEMENT = saved_flag
        server_module.cm = saved_cm

    assert resp.status_code == 409, resp.text
    assert resp.json()["action"] == "approved_profile_locked_cm_unavailable"
    # Approved profile must remain untouched even with CM down.
    assert _live(db_path, case["app_id"], "country") == "GB"


def test_submit_on_approved_blocked_even_when_cm_unavailable(api_server):
    base_url, db_path = api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    import server as server_module
    saved_flag, saved_cm = server_module.HAS_CHANGE_MANAGEMENT, server_module.cm
    server_module.HAS_CHANGE_MANAGEMENT = False
    server_module.cm = None
    try:
        resp = requests.post(
            f"{base_url}/api/applications/{case['app_id']}/submit",
            headers=_headers("co"), json={}, timeout=5,
        )
    finally:
        server_module.HAS_CHANGE_MANAGEMENT = saved_flag
        server_module.cm = saved_cm

    assert resp.status_code == 409, resp.text
    assert resp.json()["action"] == "submit_blocked_profile_locked"
    assert _live(db_path, case["app_id"], "status") == "approved"


def test_approved_correction_fails_closed_when_cm_unavailable(api_server):
    base_url, db_path = api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn, sector="Technology")
    conn.close()

    import server as server_module
    saved_flag, saved_cm = server_module.HAS_CHANGE_MANAGEMENT, server_module.cm
    server_module.HAS_CHANGE_MANAGEMENT = False
    server_module.cm = None
    try:
        resp = requests.post(
            f"{base_url}/api/applications/{case['app_id']}/corrections",
            headers=_headers("co"),
            json={
                "target_type": "application",
                "field_changes": {"sector": "Crypto / Digital Assets Exchange"},
                "correction_reason": "Registry evidence shows VASP business.",
                "evidence_source": "Business model review",
                "correction_note": "Sector corrected to verified sector.",
            },
            timeout=5,
        )
    finally:
        server_module.HAS_CHANGE_MANAGEMENT = saved_flag
        server_module.cm = saved_cm

    assert resp.status_code == 409, resp.text
    assert resp.json()["action"] == "approved_profile_locked_cm_unavailable"
    # Approved profile untouched and no draft created when CM is down.
    assert _live(db_path, case["app_id"], "sector") == "Technology"
    assert _cr_count(db_path, case["app_id"]) == 0


def test_risk_precondition_missing_evidence_returns_structured_409(api_server):
    base_url, db_path = api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn, sector="Technology")
    conn.execute(
        "UPDATE applications SET risk_level = NULL, risk_score = NULL, risk_computed_at = NULL WHERE id = ?",
        (case["app_id"],),
    )
    conn.commit()
    conn.close()

    create_resp = requests.post(
        f"{base_url}/api/change-management/requests",
        headers=_headers("co"),
        json={
            "application_id": case["app_id"],
            "source": "backoffice_manual",
            "source_channel": "backoffice",
            "reason": "API regression: missing persisted risk evidence",
            "items": [{
                "change_type": "business_activity_change",
                "field_name": "sector",
                "old_value": "Technology",
                "new_value": "Virtual assets",
                "materiality": "tier1",
            }],
        },
        timeout=5,
    )
    assert create_resp.status_code == 201, create_resp.text
    request_id = create_resp.json()["id"]

    precondition_resp = requests.post(
        f"{base_url}/api/change-management/requests/{request_id}/preconditions",
        headers=_headers("sco"),
        json={"kind": "risk"},
        timeout=5,
    )
    assert precondition_resp.status_code == 409, precondition_resp.text
    body = precondition_resp.json()
    assert body["action"] == "precondition_blocked"
    assert body["kind"] == "risk"
    assert body["code"] == "risk_result_evidence_missing"
    assert body["blockers"][0]["code"] == "risk_result_evidence_missing"
