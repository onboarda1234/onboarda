import importlib
import json
import os
import sqlite3
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

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
def officer_correction_api_server(tmp_path):
    db_path = str(tmp_path / "officer_corrections.db")
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

    user_id = {
        "admin": "admin001",
        "sco": "sco001",
        "co": "co001",
        "analyst": "analyst001",
    }.get(role, role)
    token = create_token(user_id, role, f"Test {role}", "officer")
    return {"Authorization": f"Bearer {token}"}


def _timestamp_with_offset(offset_hours=0):
    return (datetime.now(timezone.utc) + timedelta(hours=offset_hours)).strftime("%Y-%m-%d %H:%M:%S")


def _boolish(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in ("1", "true", "yes", "y")


def _insert_case(
    db,
    *,
    country="United Kingdom",
    sector="Technology",
    brn="BRN-001",
    director_is_pep="No",
    ubo_is_pep="No",
    director_pep_declaration=None,
    ubo_pep_declaration=None,
):
    suffix = uuid.uuid4().hex[:8]
    client_id = f"client_{suffix}"
    app_id = f"app_{suffix}"
    ref = f"ARF-CORR-{suffix}"
    director_id = f"dir_{suffix}"
    ubo_id = f"ubo_{suffix}"
    prescreening = {
        "screening_report": {
            "screening_mode": "live",
            "screened_at": datetime.now(timezone.utc).isoformat(),
            "sanctions": {"api_status": "live", "matched": False},
            "kyc": {"api_status": "live"},
        },
        "source_of_funds": "Salary income",
        "expected_volume": "Under 50,000",
    }

    db.execute(
        "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
        (client_id, f"{client_id}@example.com", "hash", "Officer Correction Test Ltd"),
    )
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, brn, country, sector, entity_type,
         ownership_structure, status, risk_level, final_risk_level, risk_score,
         onboarding_lane, prescreening_data, submitted_at, updated_at,
         inputs_updated_at, risk_computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            ref,
            client_id,
            "Officer Correction Test Ltd",
            brn,
            country,
            sector,
            "Limited Company",
            "Simple",
            "under_review",
            "MEDIUM",
            "MEDIUM",
            45,
            "Standard Review",
            json.dumps(prescreening),
            _timestamp_with_offset(-4),
            _timestamp_with_offset(-3),
            _timestamp_with_offset(-3),
            _timestamp_with_offset(-3),
        ),
    )
    db.execute(
        """
        INSERT INTO directors
        (id, application_id, person_key, first_name, last_name, full_name, nationality, is_pep, pep_declaration, date_of_birth)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            director_id,
            app_id,
            "dir1",
            "Alice",
            "Director",
            "Alice Director",
            "GB",
            director_is_pep,
            json.dumps({"declared_pep": False} if director_pep_declaration is None else director_pep_declaration),
            "1980-02-01",
        ),
    )
    db.execute(
        """
        INSERT INTO ubos
        (id, application_id, person_key, first_name, last_name, full_name, nationality, ownership_pct, is_pep, pep_declaration, date_of_birth)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ubo_id,
            app_id,
            "ubo1",
            "Bob",
            "Owner",
            "Bob Owner",
            "GB",
            25.0,
            ubo_is_pep,
            json.dumps({"declared_pep": False} if ubo_pep_declaration is None else ubo_pep_declaration),
            "1985-04-10",
        ),
    )
    memo_data = json.dumps({
        "metadata": {
            "ai_source": "deterministic",
            "approval_recommendation": "APPROVE_WITH_CONDITIONS",
        },
        "supervisor": {"verdict": "CONSISTENT", "can_approve": True},
    })
    db.execute(
        """
        INSERT INTO compliance_memos
        (application_id, version, memo_data, generated_by, ai_recommendation,
         review_status, quality_score, validation_status, supervisor_status,
         blocked, created_at)
        VALUES (?, 1, ?, 'admin001', 'APPROVE_WITH_CONDITIONS',
                'approved', 8.8, 'pass', 'CONSISTENT', 0, ?)
        """,
        (app_id, memo_data, _timestamp_with_offset(-2)),
    )
    db.commit()
    return {"app_id": app_id, "ref": ref, "director_id": director_id, "ubo_id": ubo_id}


def _detail(base_url, app_id):
    resp = requests.get(
        f"{base_url}/api/applications/{app_id}",
        headers=_headers("admin"),
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _corrections(base_url, app_id):
    resp = requests.get(
        f"{base_url}/api/applications/{app_id}/corrections",
        headers=_headers("admin"),
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["corrections"]


def _enhanced(base_url, app_id):
    resp = requests.get(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements",
        headers=_headers("admin"),
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _post_correction(base_url, app_id, payload):
    resp = requests.post(
        f"{base_url}/api/applications/{app_id}/corrections",
        headers=_headers("admin"),
        json=payload,
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _active_edd_cases(db, app_id):
    return db.execute(
        """
        SELECT *
        FROM edd_cases
        WHERE application_id = ?
          AND stage NOT IN ('edd_approved', 'edd_rejected')
        ORDER BY id
        """,
        (app_id,),
    ).fetchall()


def _db_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def test_pep_correction_preserves_declared_pep_and_marks_workflow_stale(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    resp = requests.post(
        f"{base_url}/api/applications/{case['app_id']}/corrections",
        headers=_headers("admin"),
        json={
            "target_type": "pep_status",
            "subject_type": "ubo",
            "target_id": case["ubo_id"],
            "field_changes": {"verified_pep": True},
            "correction_reason": "Screening hit confirms the UBO is a PEP.",
            "evidence_source": "Sumsub screening report",
            "correction_note": "Officer verified PEP exposure during review.",
            "correction_source": "screening_review",
        },
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["materiality"] == "tier1"
    assert body["downstream_state"]["risk_recomputed"] is True
    assert body["downstream_state"]["memo_requires_regeneration"] is True
    assert body["downstream_state"]["supervisor_requires_rerun"] is True

    detail = _detail(base_url, case["app_id"])
    ubo = next(item for item in detail["ubos"] if item["id"] == case["ubo_id"])
    assert ubo["client_declared_pep"] is False
    assert ubo["officer_verified_pep"] is True
    assert ubo["declared_pep"] is False
    assert ubo["verified_pep"] is True
    assert ubo["pep_status"] == "confirmed_pep"
    assert ubo["pep_verification_source"] == "screening_review"
    assert _boolish(ubo["is_pep"]) is True
    assert detail["memo_requires_regeneration"] is True
    assert detail["supervisor_requires_rerun"] is True

    corrections = _corrections(base_url, case["app_id"])
    assert corrections[0]["before_state"]["declared_pep"] is False
    assert corrections[0]["after_state"]["verified_pep"] is True


def test_pep_correction_requested_tier3_still_forces_tier1(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    body = _post_correction(
        base_url,
        case["app_id"],
        {
            "target_type": "pep_status",
            "subject_type": "ubo",
            "target_id": case["ubo_id"],
            "materiality": "tier3",
            "field_changes": {"verified_pep": True},
            "correction_reason": "Screening hit confirms the UBO is a PEP.",
            "evidence_source": "Sumsub screening report",
            "correction_note": "Officer verified PEP exposure during review.",
        },
    )
    assert body["materiality"] == "tier1"


def test_historical_party_without_verified_pep_stays_unverified(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    detail = _detail(base_url, case["app_id"])
    ubo = next(item for item in detail["ubos"] if item["id"] == case["ubo_id"])
    assert ubo["client_declared_pep"] is False
    assert ubo["officer_verified_pep"] is None
    assert ubo["verified_pep"] is None
    assert ubo["officer_verified_pep_display"] == "Not verified yet"
    assert ubo["pep_status"] == "declared_no"
    assert ubo["pep_verification_source"] == "client_declaration"


def test_missing_declared_pep_is_not_rendered_as_no(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(
        conn,
        director_is_pep=None,
        director_pep_declaration={},
    )
    conn.close()

    detail = _detail(base_url, case["app_id"])
    director = next(item for item in detail["directors"] if item["id"] == case["director_id"])
    assert director["client_declared_pep"] is None
    assert director["declared_pep"] is None
    assert director["client_declared_pep_display"] == "Not captured"
    assert director["officer_verified_pep"] is None
    assert director["officer_verified_pep_display"] == "Not verified yet"
    assert director["pep_status"] == "not_verified"


def test_untouched_declared_non_pep_keeps_declaration_separate_from_verification(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    detail = _detail(base_url, case["app_id"])
    director = next(item for item in detail["directors"] if item["id"] == case["director_id"])
    assert director["client_declared_pep"] is False
    assert director["client_declared_pep_display"] == "No"
    assert director["officer_verified_pep"] is None
    assert director["officer_verified_pep_display"] == "Not verified yet"
    assert director["pep_verification_source"] == "client_declaration"


def test_sector_correction_recomputes_risk_and_audits_before_after(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn, sector="Technology")
    conn.close()

    resp = requests.post(
        f"{base_url}/api/applications/{case['app_id']}/corrections",
        headers=_headers("admin"),
        json={
            "target_type": "application",
            "field_changes": {"sector": "Crypto Exchange"},
            "correction_reason": "Registry evidence shows the applicant operates a VASP business.",
            "evidence_source": "Business model review",
            "correction_note": "Sector corrected from portal declaration to verified sector.",
        },
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["materiality"] == "tier1"
    assert body["before_state"]["sector"] == "Technology"
    assert body["after_state"]["sector"] == "Crypto Exchange"
    assert body["downstream_state"]["risk_recomputed"] is True

    detail = _detail(base_url, case["app_id"])
    assert detail["sector"] == "Crypto Exchange"


def test_sector_correction_requested_tier3_still_forces_tier1(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn, sector="Technology")
    conn.close()

    body = _post_correction(
        base_url,
        case["app_id"],
        {
            "target_type": "application",
            "materiality": "tier3",
            "field_changes": {"sector": "Crypto Exchange"},
            "correction_reason": "Registry evidence shows the applicant operates a VASP business.",
            "evidence_source": "Business model review",
            "correction_note": "Sector corrected from portal declaration to verified sector.",
        },
    )
    assert body["materiality"] == "tier1"


def test_jurisdiction_correction_refreshes_high_risk_requirements(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn, country="United Kingdom")
    conn.close()

    resp = requests.post(
        f"{base_url}/api/applications/{case['app_id']}/corrections",
        headers=_headers("admin"),
        json={
            "target_type": "application",
            "field_changes": {"country": "Iran"},
            "correction_reason": "Incorporation documents show the entity is registered in Iran.",
            "evidence_source": "Certificate of incorporation",
            "correction_note": "Jurisdiction corrected after officer document review.",
        },
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["materiality"] == "tier1"

    enhanced = _enhanced(base_url, case["app_id"])
    trigger_keys = {item["trigger_key"] for item in enhanced["requirements"]}
    assert "high_risk_jurisdiction" in trigger_keys


def test_high_risk_country_requested_tier3_still_forces_tier1(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn, country="United Kingdom")
    conn.close()

    body = _post_correction(
        base_url,
        case["app_id"],
        {
            "target_type": "application",
            "materiality": "tier3",
            "field_changes": {"country": "Iran"},
            "correction_reason": "Incorporation documents show the entity is registered in Iran.",
            "evidence_source": "Certificate of incorporation",
            "correction_note": "Jurisdiction corrected after officer document review.",
        },
    )
    assert body["materiality"] == "tier1"


def test_ownership_percentage_correction_preserves_before_after_states(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    resp = requests.post(
        f"{base_url}/api/applications/{case['app_id']}/corrections",
        headers=_headers("admin"),
        json={
            "target_type": "ubo",
            "target_id": case["ubo_id"],
            "field_changes": {"ownership_pct": 80},
            "correction_reason": "Share register shows an 80% holding.",
            "evidence_source": "Certified shareholder register",
            "correction_note": "Ownership corrected to verified percentage.",
        },
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["materiality"] == "tier1"
    assert body["before_state"]["ownership_pct"] == 25.0
    assert body["after_state"]["ownership_pct"] == 80.0

    detail = _detail(base_url, case["app_id"])
    ubo = next(item for item in detail["ubos"] if item["id"] == case["ubo_id"])
    assert float(ubo["ownership_pct"]) == 80.0


def test_ownership_percentage_requested_tier3_still_forces_tier1(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    body = _post_correction(
        base_url,
        case["app_id"],
        {
            "target_type": "ubo",
            "target_id": case["ubo_id"],
            "materiality": "tier3",
            "field_changes": {"ownership_pct": 80},
            "correction_reason": "Share register shows an 80% holding.",
            "evidence_source": "Certified shareholder register",
            "correction_note": "Ownership corrected to verified percentage.",
        },
    )
    assert body["materiality"] == "tier1"


def test_source_of_funds_requested_tier3_still_forces_tier1(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    body = _post_correction(
        base_url,
        case["app_id"],
        {
            "target_type": "risk_field",
            "materiality": "tier3",
            "field_changes": {"source_of_funds": "PEP linked transfer"},
            "correction_reason": "Officer verified updated source of funds.",
            "evidence_source": "Source of funds review",
            "correction_note": "Material source of funds correction.",
        },
    )
    assert body["materiality"] == "tier1"


def test_source_of_wealth_requested_tier3_still_forces_tier1(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    body = _post_correction(
        base_url,
        case["app_id"],
        {
            "target_type": "risk_field",
            "materiality": "tier3",
            "field_changes": {"source_of_wealth": "Politically exposed holdings"},
            "correction_reason": "Officer verified updated source of wealth.",
            "evidence_source": "Source of wealth review",
            "correction_note": "Material source of wealth correction.",
        },
    )
    assert body["materiality"] == "tier1"


def test_non_material_typo_correction_audits_without_memo_staleness(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn, brn="BRN-001")
    conn.close()

    resp = requests.post(
        f"{base_url}/api/applications/{case['app_id']}/corrections",
        headers=_headers("admin"),
        json={
            "target_type": "application",
            "materiality": "tier3",
            "field_changes": {"brn": "BRN-002"},
            "correction_reason": "Administrative typo correction.",
            "correction_note": "Corrected BRN typo with no risk impact.",
            "evidence_source": "Officer data-entry check",
        },
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["materiality"] == "tier3"
    assert body["downstream_state"]["risk_recomputed"] is False
    assert body["downstream_state"]["memo_requires_regeneration"] is False

    detail = _detail(base_url, case["app_id"])
    assert detail["brn"] == "BRN-002"
    assert detail["memo_requires_regeneration"] is False


def test_material_correction_blocks_approval_until_refresh_complete(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    resp = requests.post(
        f"{base_url}/api/applications/{case['app_id']}/corrections",
        headers=_headers("admin"),
        json={
            "target_type": "pep_status",
            "subject_type": "director",
            "target_id": case["director_id"],
            "field_changes": {"verified_pep": True},
            "correction_reason": "Officer review confirmed a PEP relationship.",
            "evidence_source": "Screening escalation pack",
            "correction_note": "Director reclassified as verified PEP.",
        },
        timeout=5,
    )
    assert resp.status_code == 200, resp.text

    approve = requests.patch(
        f"{base_url}/api/applications/{case['app_id']}",
        headers=_headers("admin"),
        json={"status": "approved"},
        timeout=5,
    )
    assert approve.status_code == 400, approve.text
    assert "memo" in approve.text.lower() or "approval gate failed" in approve.text.lower()


def test_document_checklist_refreshes_after_material_risk_change(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    resp = requests.post(
        f"{base_url}/api/applications/{case['app_id']}/corrections",
        headers=_headers("admin"),
        json={
            "target_type": "pep_status",
            "subject_type": "ubo",
            "target_id": case["ubo_id"],
            "field_changes": {"verified_pep": True},
            "correction_reason": "PEP exposure confirmed by screening.",
            "evidence_source": "Screening review findings",
            "correction_note": "Trigger enhanced review requirements for PEP.",
        },
        timeout=5,
    )
    assert resp.status_code == 200, resp.text

    enhanced = _enhanced(base_url, case["app_id"])
    trigger_keys = {item["trigger_key"] for item in enhanced["requirements"]}
    assert "pep" in trigger_keys
    assert enhanced["enhanced_review_summary"]["next_action_code"] != "none"

    apps_resp = requests.get(
        f"{base_url}/api/applications?limit=20",
        headers=_headers("admin"),
        timeout=5,
    )
    assert apps_resp.status_code == 200, apps_resp.text
    apps = {item["id"]: item for item in apps_resp.json()["applications"]}
    assert apps[case["app_id"]]["enhanced_review_summary"]["next_action_code"] != "none"


def test_verified_pep_correction_explicitly_routes_to_edd_and_audits(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    body = _post_correction(
        base_url,
        case["app_id"],
        {
            "target_type": "pep_status",
            "subject_type": "ubo",
            "target_id": case["ubo_id"],
            "field_changes": {"verified_pep": True},
            "correction_reason": "Screening hit confirms the UBO is a PEP.",
            "evidence_source": "Sumsub screening report",
            "correction_note": "Officer verified PEP exposure during review.",
        },
    )
    assert body["downstream_state"]["edd_routing_evaluated"] is True
    assert body["downstream_state"]["edd_routing_route"] == "edd"
    assert body["downstream_state"]["edd_routing_actuated"] is True
    assert body["downstream_state"]["status"] == "edd_required"
    assert body["downstream_state"]["onboarding_lane"] == "EDD"

    conn = _db_conn(db_path)
    cases = _active_edd_cases(conn, case["app_id"])
    assert len(cases) == 1
    assert cases[0]["stage"] == "triggered"
    evaluated = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'edd_routing.evaluated' AND target = ?",
        (f"application:{case['ref']}",),
    ).fetchone()
    actuated = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'edd_routing.actuated' AND target = ?",
        (f"application:{case['ref']}",),
    ).fetchone()
    assert evaluated["c"] >= 1
    assert actuated["c"] >= 1
    conn.close()


def test_officer_correction_verified_false_requires_evidence_and_sets_verified_not_pep(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn, ubo_is_pep=None, ubo_pep_declaration={})
    conn.close()

    body = _post_correction(
        base_url,
        case["app_id"],
        {
            "target_type": "pep_status",
            "subject_type": "ubo",
            "target_id": case["ubo_id"],
            "field_changes": {"verified_pep": False},
            "correction_reason": "Officer confirmed the screening alert was not a PEP match.",
            "evidence_source": "Manual screening disposition review",
            "correction_note": "False PEP alert resolved by officer review.",
            "correction_source": "officer_correction",
        },
    )
    assert body["after_state"]["officer_verified_pep"] is False
    assert body["after_state"]["pep_status"] == "not_pep"
    assert body["after_state"]["pep_verification_source"] == "officer_correction"

    detail = _detail(base_url, case["app_id"])
    ubo = next(item for item in detail["ubos"] if item["id"] == case["ubo_id"])
    assert ubo["officer_verified_pep"] is False
    assert ubo["officer_verified_pep_display"] == "No"
    assert ubo["pep_status"] == "not_pep"
    assert ubo["pep_verification_source"] == "officer_correction"


def test_high_risk_jurisdiction_correction_routes_to_edd(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn, country="United Kingdom")
    conn.close()

    body = _post_correction(
        base_url,
        case["app_id"],
        {
            "target_type": "application",
            "field_changes": {"country": "Iran"},
            "correction_reason": "Incorporation documents show the entity is registered in Iran.",
            "evidence_source": "Certificate of incorporation",
            "correction_note": "Jurisdiction corrected after officer document review.",
        },
    )
    assert body["downstream_state"]["edd_routing_evaluated"] is True
    assert body["downstream_state"]["edd_routing_route"] == "edd"
    assert body["downstream_state"]["status"] == "edd_required"
    assert body["downstream_state"]["onboarding_lane"] == "EDD"


def test_repeated_material_correction_reuses_edd_case_idempotently(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    payload = {
        "target_type": "pep_status",
        "subject_type": "ubo",
        "target_id": case["ubo_id"],
        "field_changes": {"verified_pep": True},
        "correction_reason": "Screening hit confirms the UBO is a PEP.",
        "evidence_source": "Sumsub screening report",
        "correction_note": "Officer verified PEP exposure during review.",
    }
    first = _post_correction(base_url, case["app_id"], payload)
    second = _post_correction(base_url, case["app_id"], payload)
    assert first["downstream_state"]["edd_routing_route"] == "edd"
    assert second["downstream_state"]["edd_routing_route"] == "edd"

    conn = _db_conn(db_path)
    cases = _active_edd_cases(conn, case["app_id"])
    assert len(cases) == 1
    conn.close()


def test_backoffice_html_exposes_officer_correction_controls():
    html_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "arie-backoffice.html",
    )
    with open(html_path, "r", encoding="utf-8") as handle:
        src = handle.read()
    assert "btn-open-officer-correction" in src
    assert "modal-officer-correction" in src
    assert "detail-officer-corrections" in src
    assert "detail-correction-warning" in src


def test_backoffice_html_uses_tri_state_pep_copy():
    html_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "arie-backoffice.html",
    )
    with open(html_path, "r", encoding="utf-8") as handle:
        src = handle.read()
    assert "Not captured" in src
    assert "Not verified yet" in src
    assert "subject.declared_pep ? 'Yes' : 'No'" not in src
    assert "subject.verified_pep ? 'Yes' : 'No'" not in src
    assert "!!d.declared_pep" not in src
    assert "!!u.verified_pep" not in src
