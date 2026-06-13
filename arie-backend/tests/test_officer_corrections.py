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
        "registered_entity_name": "Officer Correction Test Ltd",
        "trading_name": "Original Trading Name",
        "referrer_name": "Original Referrer",
        "country_of_incorporation": country,
        "country": country,
        "sector": sector,
        "entity_type": "Limited Company",
        "ownership_structure": "Simple",
        "introduction_method": "Direct application — client initiated",
        "monthly_volume": "Under USD 50,000 per month",
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


def _post_correction_raw(base_url, app_id, payload, role="admin", headers=None):
    return requests.post(
        f"{base_url}/api/applications/{app_id}/corrections",
        headers=headers or _headers(role),
        json=payload,
        timeout=5,
    )


def _json_has_key(value, forbidden):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in forbidden:
                return True
            if _json_has_key(child, forbidden):
                return True
    if isinstance(value, list):
        return any(_json_has_key(item, forbidden) for item in value)
    return False


def _post_prescreening_correction(base_url, app_id, payload, role="admin"):
    return requests.post(
        f"{base_url}/api/applications/{app_id}/officer-corrections",
        headers=_headers(role),
        json=payload,
        timeout=5,
    )


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


def test_pr410a_prescreening_correction_rejects_unauthenticated(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    resp = requests.post(
        f"{base_url}/api/applications/{case['app_id']}/officer-corrections",
        json={
            "field_changes": {"trading_name": "Corrected Trading Name"},
            "correction_reason": "Registry evidence confirms updated trading name.",
        },
        timeout=5,
    )
    assert resp.status_code in (401, 403)


def test_pr410a_prescreening_correction_rejects_client_user(officer_correction_api_server):
    from auth import create_token

    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    token = create_token("client-test", "client", "Portal Client", "client")
    resp = requests.post(
        f"{base_url}/api/applications/{case['app_id']}/officer-corrections",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "field_changes": {"trading_name": "Corrected Trading Name"},
            "correction_reason": "Registry evidence confirms updated trading name.",
        },
        timeout=5,
    )
    assert resp.status_code == 403


def test_pr410a_prescreening_correction_rejects_disallowed_field(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    resp = _post_prescreening_correction(
        base_url,
        case["app_id"],
        {
            "field_changes": {"country": "Iran"},
            "correction_reason": "Attempt to edit risk-relevant jurisdiction.",
        },
    )
    assert resp.status_code == 400
    assert "Unsupported controlled correction field" in resp.text


def test_pr410a_prescreening_correction_rejects_missing_reason(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    resp = _post_prescreening_correction(
        base_url,
        case["app_id"],
        {"field_changes": {"trading_name": "Corrected Trading Name"}},
    )
    assert resp.status_code == 400
    assert "correction_reason is required" in resp.text


def test_pr410a_prescreening_correction_persists_history_audit_and_overlay_without_downstream_side_effects(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    before_app = conn.execute(
        "SELECT risk_level, risk_score, risk_computed_at, prescreening_data FROM applications WHERE id = ?",
        (case["app_id"],),
    ).fetchone()
    conn.close()

    resp = _post_prescreening_correction(
        base_url,
        case["app_id"],
        {
            "field_changes": {
                "trading_name": "Corrected Trading Name",
                "referrer_name": "Corrected Referrer",
            },
            "correction_reason": "Officer verified factual display fields from registry evidence.",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["target_type"] == "prescreening_field"
    assert body["materiality"] == "tier3"
    assert body["before_state"]["trading_name"] == "Original Trading Name"
    assert body["after_state"]["trading_name"] == "Corrected Trading Name"
    assert body["downstream_state"]["risk_relevant"] is False
    assert body["downstream_state"]["risk_recomputed"] is False
    assert body["downstream_state"]["memo_requires_regeneration"] is False

    detail = _detail(base_url, case["app_id"])
    assert detail["prescreening_data"]["trading_name"] == "Original Trading Name"
    assert detail["officer_correction_display_values"]["trading_name"] == "Corrected Trading Name"
    assert detail["officer_correction_display_metadata"]["trading_name"]["original_client_value"] == "Original Trading Name"

    corrections = _corrections(base_url, case["app_id"])
    latest = corrections[0]
    assert latest["target_type"] == "prescreening_field"
    assert latest["field_scope"] == "referrer_name,trading_name"
    assert latest["downstream_state"]["risk_impact"] == "No risk recomputation required"

    conn = _db_conn(db_path)
    after_app = conn.execute(
        "SELECT risk_level, risk_score, risk_computed_at, prescreening_data FROM applications WHERE id = ?",
        (case["app_id"],),
    ).fetchone()
    memo = conn.execute(
        "SELECT is_stale FROM compliance_memos WHERE application_id = ? ORDER BY id DESC LIMIT 1",
        (case["app_id"],),
    ).fetchone()
    audit = conn.execute(
        """
        SELECT action, detail, before_state, after_state
        FROM audit_log
        WHERE target = ? AND action = 'officer_correction_created'
        ORDER BY id DESC LIMIT 1
        """,
        (case["ref"],),
    ).fetchone()
    conn.close()
    assert after_app["risk_level"] == before_app["risk_level"]
    assert after_app["risk_score"] == before_app["risk_score"]
    assert after_app["risk_computed_at"] == before_app["risk_computed_at"]
    assert json.loads(after_app["prescreening_data"])["trading_name"] == "Original Trading Name"
    assert memo["is_stale"] in (None, 0, False)
    assert audit is not None
    assert "Officer Correction Mode" in audit["detail"]
    assert json.loads(audit["before_state"])["trading_name"] == "Original Trading Name"
    assert json.loads(audit["after_state"])["trading_name"] == "Corrected Trading Name"


def test_pr410b_controlled_sector_correction_recomputes_risk_marks_memo_stale_and_preserves_original_submission(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn, sector="Technology")
    before_app = conn.execute(
        "SELECT risk_level, risk_score, risk_dimensions, prescreening_data FROM applications WHERE id = ?",
        (case["app_id"],),
    ).fetchone()
    conn.close()

    resp = _post_prescreening_correction(
        base_url,
        case["app_id"],
        {
            "field_changes": {"sector": "Crypto / Digital Assets Exchange"},
            "correction_reason": "Officer verified VASP activity from business model evidence.",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["materiality"] == "tier1"
    assert body["before_state"]["sector"] == "Technology"
    assert body["after_state"]["sector"] == "Crypto / Digital Assets Exchange"
    assert body["before_state"]["risk_before"]["risk_score"] == before_app["risk_score"]
    assert body["after_state"]["risk_after"]["risk_score"] != before_app["risk_score"]
    assert body["downstream_state"]["risk_relevant"] is True
    assert body["downstream_state"]["risk_recomputed"] is True
    assert body["downstream_state"]["memo_requires_regeneration"] is True
    assert body["downstream_state"]["memo_impact"] == "Memo marked stale"

    detail = _detail(base_url, case["app_id"])
    assert detail["sector"] == "Crypto / Digital Assets Exchange"
    assert detail["officer_correction_display_values"]["sector"] == "Crypto / Digital Assets Exchange"
    assert detail["officer_correction_display_metadata"]["sector"]["original_client_value"] == "Technology"
    assert detail["memo_requires_regeneration"] is True
    assert detail["memo_is_stale"] is True
    assert detail["risk_score"] != before_app["risk_score"]

    conn = _db_conn(db_path)
    after_app = conn.execute(
        "SELECT sector, risk_level, risk_score, prescreening_data FROM applications WHERE id = ?",
        (case["app_id"],),
    ).fetchone()
    memo = conn.execute(
        "SELECT is_stale, stale_trigger, review_status, validation_status, supervisor_status FROM compliance_memos WHERE application_id = ? ORDER BY id DESC LIMIT 1",
        (case["app_id"],),
    ).fetchone()
    correction = conn.execute(
        """
        SELECT before_state, after_state, downstream_state
        FROM application_corrections
        WHERE application_id = ? AND field_scope = 'sector'
        ORDER BY id DESC LIMIT 1
        """,
        (case["app_id"],),
    ).fetchone()
    audit = conn.execute(
        """
        SELECT detail, before_state, after_state
        FROM audit_log
        WHERE target = ? AND action = 'officer_correction_created'
        ORDER BY id DESC LIMIT 1
        """,
        (case["ref"],),
    ).fetchone()
    conn.close()
    assert after_app["sector"] == "Crypto / Digital Assets Exchange"
    assert json.loads(after_app["prescreening_data"])["sector"] == "Technology"
    assert memo["is_stale"] in (1, True)
    assert memo["stale_trigger"] == "controlled_prescreening_officer_correction"
    assert memo["review_status"] == "draft"
    assert memo["validation_status"] == "pending"
    assert memo["supervisor_status"] == "pending"
    correction_after = json.loads(correction["after_state"])
    correction_downstream = json.loads(correction["downstream_state"])
    assert correction_after["risk_after"]["risk_score"] == after_app["risk_score"]
    assert correction_downstream["risk_recomputed"] is True
    assert "Risk recomputed" in correction_downstream["risk_impact"]
    assert "memo impact: Memo marked stale" in audit["detail"]
    assert json.loads(audit["before_state"])["risk_before"]["risk_score"] == before_app["risk_score"]
    assert json.loads(audit["after_state"])["risk_after"]["risk_score"] == after_app["risk_score"]

    approve = requests.patch(
        f"{base_url}/api/applications/{case['app_id']}",
        headers=_headers("admin"),
        json={"status": "approved"},
        timeout=5,
    )
    assert approve.status_code == 400, approve.text
    assert "memo" in approve.text.lower() and "stale" in approve.text.lower()


@pytest.mark.parametrize(
    "field_path,new_value",
    [
        ("country_of_incorporation", "Iran"),
        ("entity_type", "Trust"),
        ("ownership_structure", "Complex multi-jurisdiction / opaque structure"),
        ("introduction_method", "Introduced by non-regulated intermediary"),
        ("monthly_volume", "Over USD 5,000,000 per month"),
    ],
)
def test_pr410b_selected_risk_fields_trigger_backend_recompute(officer_correction_api_server, field_path, new_value):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn, country="United Kingdom", sector="Technology")
    before = conn.execute(
        "SELECT risk_score, risk_level, prescreening_data FROM applications WHERE id = ?",
        (case["app_id"],),
    ).fetchone()
    conn.close()

    resp = _post_prescreening_correction(
        base_url,
        case["app_id"],
        {
            "field_changes": {field_path: new_value},
            "correction_reason": f"Officer verified corrected {field_path}.",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["downstream_state"]["risk_relevant"] is True
    assert body["downstream_state"]["risk_recomputed"] is True
    assert body["before_state"]["risk_before"]["risk_score"] == before["risk_score"]
    assert "risk_after" in body["after_state"]

    detail = _detail(base_url, case["app_id"])
    assert detail["officer_correction_display_values"][field_path] == new_value
    assert detail["memo_is_stale"] is True
    assert detail["officer_correction_display_metadata"][field_path]["risk_relevant"] is True

    conn = _db_conn(db_path)
    stored = conn.execute(
        "SELECT country, entity_type, ownership_structure, prescreening_data FROM applications WHERE id = ?",
        (case["app_id"],),
    ).fetchone()
    conn.close()
    prescreening_after = json.loads(stored["prescreening_data"])
    assert prescreening_after == json.loads(before["prescreening_data"])
    if field_path == "country_of_incorporation":
        assert stored["country"] == new_value
    if field_path == "entity_type":
        assert stored["entity_type"] == new_value
    if field_path == "ownership_structure":
        assert stored["ownership_structure"] == new_value


def test_pr410b_controlled_endpoint_rejects_analyst_for_risk_relevant_correction(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    conn.close()

    resp = _post_prescreening_correction(
        base_url,
        case["app_id"],
        {
            "field_changes": {"sector": "Crypto / Digital Assets Exchange"},
            "correction_reason": "Analyst should not be allowed to mutate risk fields.",
        },
        role="analyst",
    )
    assert resp.status_code == 403


def test_pr410a_prescreening_correction_endpoint_rejects_invalid_application(officer_correction_api_server):
    base_url, _ = officer_correction_api_server
    resp = _post_prescreening_correction(
        base_url,
        "missing-application",
        {
            "field_changes": {"trading_name": "Corrected Trading Name"},
            "correction_reason": "No such application.",
        },
    )
    assert resp.status_code == 404


def test_pr410a_portal_applications_do_not_expose_correction_history_or_internal_metadata(officer_correction_api_server):
    from auth import create_token

    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    client_id = conn.execute("SELECT client_id FROM applications WHERE id = ?", (case["app_id"],)).fetchone()["client_id"]
    conn.close()

    resp = _post_prescreening_correction(
        base_url,
        case["app_id"],
        {
            "field_changes": {"trading_name": "Corrected Trading Name"},
            "correction_reason": "Officer verified factual display field.",
        },
    )
    assert resp.status_code == 201, resp.text

    token = create_token(client_id, "client", "Portal Client", "client")
    portal = requests.get(
        f"{base_url}/api/portal/applications",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    assert portal.status_code == 200, portal.text
    apps = portal.json()["applications"]
    app = next(item for item in apps if item["id"] == case["app_id"])
    forbidden = {
        "officer_corrections",
        "officer_correction_display_values",
        "officer_correction_display_metadata",
        "audit_log",
        "internal_blockers",
        "risk_score",
        "risk_level",
    }
    assert forbidden.isdisjoint(app.keys())


def test_pr410b_portal_does_not_expose_risk_correction_or_memo_stale_metadata(officer_correction_api_server):
    from auth import create_token

    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    client_id = conn.execute("SELECT client_id FROM applications WHERE id = ?", (case["app_id"],)).fetchone()["client_id"]
    conn.close()

    resp = _post_prescreening_correction(
        base_url,
        case["app_id"],
        {
            "field_changes": {"sector": "Crypto / Digital Assets Exchange"},
            "correction_reason": "Officer verified corrected sector.",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["downstream_state"]["risk_recomputed"] is True

    token = create_token(client_id, "client", "Portal Client", "client")
    portal = requests.get(
        f"{base_url}/api/portal/applications",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    assert portal.status_code == 200, portal.text
    app = next(item for item in portal.json()["applications"] if item["id"] == case["app_id"])
    forbidden = {
        "officer_corrections",
        "officer_correction_display_values",
        "officer_correction_display_metadata",
        "audit_log",
        "risk_score",
        "risk_level",
        "final_risk_level",
        "risk_dimensions",
        "memo_is_stale",
        "memo_stale_reason",
        "memo_requires_regeneration",
        "internal_blockers",
        "enhanced_review_summary",
    }
    assert forbidden.isdisjoint(app.keys())
    assert "Crypto / Digital Assets Exchange" not in json.dumps(app)
    assert "officer_correction" not in json.dumps(app).lower()


def test_pr410b_client_application_detail_is_safe_for_portal_resume(officer_correction_api_server):
    from auth import create_token

    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    row = conn.execute(
        "SELECT client_id, prescreening_data FROM applications WHERE id = ?",
        (case["app_id"],),
    ).fetchone()
    prescreening = json.loads(row["prescreening_data"])
    prescreening["pricing"] = {
        "monthly_fee": 250,
        "setup_fee": 1000,
        "risk_score": 45,
        "risk_level": "MEDIUM",
        "risk_dimensions": {"d4": 2.0},
    }
    conn.execute(
        "UPDATE applications SET prescreening_data = ? WHERE id = ?",
        (json.dumps(prescreening), case["app_id"]),
    )
    conn.commit()
    client_id = row["client_id"]
    conn.close()

    resp = _post_prescreening_correction(
        base_url,
        case["app_id"],
        {
            "field_changes": {"sector": "Crypto / Digital Assets Exchange"},
            "correction_reason": "Officer verified corrected sector.",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["downstream_state"]["risk_recomputed"] is True
    assert resp.json()["downstream_state"]["memo_requires_regeneration"] is True

    token = create_token(client_id, "client", "Portal Client", "client")
    detail = requests.get(
        f"{base_url}/api/applications/{case['app_id']}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    assert detail.status_code == 200, detail.text
    app = detail.json()
    forbidden_keys = {
        "officer_corrections",
        "officer_correction_display_values",
        "officer_correction_display_metadata",
        "risk_score",
        "risk_level",
        "final_risk_level",
        "risk_dimensions",
        "latest_memo",
        "latest_memo_data",
        "memo_is_stale",
        "memo_stale_reason",
        "memo_stale_reasons",
        "memo_requires_regeneration",
        "supervisor_requires_rerun",
        "screening_truth_summary",
        "enhanced_review_summary",
        "periodic_review_baseline_status",
    }
    assert forbidden_keys.isdisjoint(app.keys())
    pricing = (app.get("prescreening_data") or {}).get("pricing") or {}
    assert {"risk_score", "risk_level", "risk_dimensions"}.isdisjoint(pricing.keys())

    serialized = json.dumps(app).lower()
    for term in (
        "officer_correction",
        "correction_reason",
        "before_state",
        "after_state",
        "memo_stale",
        "memo_requires_regeneration",
        "risk_score",
        "risk_level",
        "risk_dimensions",
        "audit_log",
        "blockers",
    ):
        assert term not in serialized
    assert app["sector"] == "Crypto / Digital Assets Exchange"


def test_pr410c_director_nationality_correction_recomputes_risk_audits_and_stales_memo(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    before = conn.execute(
        "SELECT risk_score, risk_level FROM applications WHERE id = ?",
        (case["app_id"],),
    ).fetchone()
    conn.close()

    body = _post_correction(
        base_url,
        case["app_id"],
        {
            "target_type": "director",
            "target_id": case["director_id"],
            "field_changes": {"nationality": "Iran"},
            "correction_reason": "Passport evidence confirms Iranian nationality.",
            "evidence_source": "Certified passport copy",
            "correction_note": "Director nationality corrected from legacy GB value.",
            "correction_source": "application_overview_party_correction_mode",
        },
    )
    assert body["target_type"] == "director"
    assert body["before_state"]["nationality"] == "GB"
    assert body["after_state"]["nationality"] == "Iran"
    assert body["before_state"]["risk_before"]["risk_score"] == before["risk_score"]
    assert "risk_after" in body["after_state"]
    assert body["downstream_state"]["risk_recomputed"] is True
    assert "Risk recomputed" in body["downstream_state"]["risk_impact"]
    assert body["downstream_state"]["memo_impact"] == "Memo marked stale"

    detail = _detail(base_url, case["app_id"])
    director = next(item for item in detail["directors"] if item["id"] == case["director_id"])
    assert director["nationality"] == "Iran"
    correction = detail["officer_corrections"][0]
    assert correction["target_type"] == "director"
    assert correction["target_id"] == case["director_id"]
    assert correction["downstream_state"]["source_surface"] == "application_overview_party_correction_mode"
    assert correction["downstream_state"]["memo_impact"] == "Memo marked stale"

    conn = _db_conn(db_path)
    audit = conn.execute(
        """
        SELECT before_state, after_state
        FROM audit_log
        WHERE target = ? AND action = 'Officer Correction'
        ORDER BY id DESC LIMIT 1
        """,
        (case["ref"],),
    ).fetchone()
    memo = conn.execute(
        "SELECT is_stale, stale_trigger FROM compliance_memos WHERE application_id = ? ORDER BY id DESC LIMIT 1",
        (case["app_id"],),
    ).fetchone()
    conn.close()
    assert json.loads(audit["before_state"])["risk_before"]["risk_score"] == before["risk_score"]
    assert "risk_after" in json.loads(audit["after_state"])
    assert memo["is_stale"] in (1, True)
    assert memo["stale_trigger"] == "backoffice_correction:director"

    approve = requests.patch(
        f"{base_url}/api/applications/{case['app_id']}",
        headers=_headers("admin"),
        json={"status": "approved"},
        timeout=5,
    )
    assert approve.status_code == 400, approve.text
    assert "memo" in approve.text.lower() and "stale" in approve.text.lower()


def test_pr410c_ubo_declared_pep_correction_preserves_original_and_does_not_write_officer_verification_metadata(officer_correction_api_server):
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
            "field_changes": {"is_pep": "Yes"},
            "correction_reason": "Client declaration form shows the UBO selected PEP.",
            "evidence_source": "Signed PEP declaration",
            "correction_note": "Correcting client-declared PEP value only.",
            "correction_source": "application_overview_party_correction_mode",
        },
    )
    assert body["before_state"]["is_pep"] == "No"
    assert body["before_state"]["client_declared_pep"] is False
    assert body["after_state"]["is_pep"] == "Yes"
    assert body["after_state"]["client_declared_pep"] is True
    assert "officer_verified_pep" not in body["after_state"]
    assert body["downstream_state"]["risk_recomputed"] is True

    detail = _detail(base_url, case["app_id"])
    ubo = next(item for item in detail["ubos"] if item["id"] == case["ubo_id"])
    assert ubo["is_pep"] == "Yes"
    assert ubo["client_declared_pep"] is True
    assert ubo["officer_verified_pep"] is None
    assert ubo["pep_declaration"]["client_declared_pep"] is True
    assert "correction_reason" not in ubo["pep_declaration"]

    corrections = _corrections(base_url, case["app_id"])
    latest = corrections[0]
    assert latest["target_type"] == "ubo"
    assert latest["before_state"]["is_pep"] == "No"
    assert latest["after_state"]["is_pep"] == "Yes"


def test_pr410c_party_correction_rejects_invalid_values_disallowed_fields_and_wrong_party(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    other = _insert_case(conn)
    conn.close()

    invalid_country = _post_correction_raw(
        base_url,
        case["app_id"],
        {
            "target_type": "director",
            "target_id": case["director_id"],
            "field_changes": {"nationality": "Atlantis"},
            "correction_reason": "Invalid value should fail.",
            "evidence_source": "Test",
            "correction_note": "Test",
        },
    )
    assert invalid_country.status_code == 400
    assert "controlled portal option" in invalid_country.text

    screening_fact = _post_correction_raw(
        base_url,
        case["app_id"],
        {
            "target_type": "ubo",
            "target_id": case["ubo_id"],
            "field_changes": {"screening_confirmed_pep": "Yes"},
            "correction_reason": "Screening provider facts must not be editable here.",
            "evidence_source": "Test",
            "correction_note": "Test",
        },
    )
    assert screening_fact.status_code == 400
    assert "Unsupported field" in screening_fact.text

    wrong_party = _post_correction_raw(
        base_url,
        case["app_id"],
        {
            "target_type": "ubo",
            "target_id": other["ubo_id"],
            "field_changes": {"ownership_pct": 40},
            "correction_reason": "Wrong application party should fail.",
            "evidence_source": "Test",
            "correction_note": "Test",
        },
    )
    assert wrong_party.status_code == 400
    assert "Correction target not found" in wrong_party.text


def test_pr410c_client_cannot_correct_party_and_portal_detail_strips_party_internal_metadata(officer_correction_api_server):
    from auth import create_token

    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(conn)
    row = conn.execute(
        "SELECT client_id FROM applications WHERE id = ?",
        (case["app_id"],),
    ).fetchone()
    client_id = row["client_id"]
    conn.close()

    client_token = create_token(client_id, "client", "Portal Client", "client")
    client_attempt = _post_correction_raw(
        base_url,
        case["app_id"],
        {
            "target_type": "director",
            "target_id": case["director_id"],
            "field_changes": {"nationality": "Mauritius"},
            "correction_reason": "Client must not access officer correction endpoint.",
            "evidence_source": "Test",
            "correction_note": "Test",
        },
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert client_attempt.status_code == 403

    body = _post_correction(
        base_url,
        case["app_id"],
        {
            "target_type": "ubo",
            "target_id": case["ubo_id"],
            "field_changes": {"is_pep": "Yes"},
            "correction_reason": "Client-safe portal test.",
            "evidence_source": "Signed PEP declaration",
            "correction_note": "Correct client-declared PEP only.",
            "correction_source": "application_overview_party_correction_mode",
        },
    )
    assert body["status"] == "corrected"

    detail = requests.get(
        f"{base_url}/api/applications/{case['app_id']}",
        headers={"Authorization": f"Bearer {client_token}"},
        timeout=5,
    )
    assert detail.status_code == 200, detail.text
    app = detail.json()
    forbidden = {
        "officer_corrections",
        "officer_correction_display_values",
        "officer_correction_display_metadata",
        "risk_score",
        "risk_level",
        "memo_is_stale",
        "memo_stale_reason",
        "officer_verified_pep",
        "officer_verified_pep_display",
        "pep_verification_source",
        "correction_reason",
        "correction_source",
        "evidence_source",
        "before_state",
        "after_state",
    }
    assert not _json_has_key(app, forbidden)
    client_ubo = next(item for item in app["ubos"] if item["id"] == case["ubo_id"])
    assert client_ubo["is_pep"] == "Yes"
    assert client_ubo["pep_declaration"]["client_declared_pep"] is True


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
            "evidence_source": "Screening provider report",
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
    assert detail["memo_is_stale"] is True
    assert "Back-office correction" in detail["memo_stale_reason"]

    corrections = _corrections(base_url, case["app_id"])
    assert corrections[0]["before_state"]["declared_pep"] is False
    assert corrections[0]["after_state"]["verified_pep"] is True

    conn = _db_conn(db_path)
    memo = conn.execute(
        "SELECT is_stale, stale_reason, stale_trigger, review_status, validation_status, supervisor_status, approved_by "
        "FROM compliance_memos WHERE application_id = ? ORDER BY id DESC LIMIT 1",
        (case["app_id"],),
    ).fetchone()
    audit = conn.execute(
        "SELECT action, detail FROM audit_log WHERE target = ? AND action = 'Memo Marked Stale' ORDER BY id DESC LIMIT 1",
        (case["ref"],),
    ).fetchone()
    conn.close()
    assert memo["is_stale"] in (1, True)
    assert memo["stale_trigger"] == "backoffice_correction:pep_status"
    assert memo["review_status"] == "draft"
    assert memo["validation_status"] == "pending"
    assert memo["supervisor_status"] == "pending"
    assert memo["approved_by"] is None
    assert audit is not None
    assert "officer_reapproval" in audit["detail"]


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
            "evidence_source": "Screening provider report",
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


def test_party_with_no_pep_data_stays_unknown_and_unverified(officer_correction_api_server):
    base_url, db_path = officer_correction_api_server
    conn = _fresh_db(db_path)
    case = _insert_case(
        conn,
        director_is_pep=None,
        ubo_is_pep=None,
        director_pep_declaration={},
        ubo_pep_declaration={},
    )
    conn.close()

    detail = _detail(base_url, case["app_id"])
    ubo = next(item for item in detail["ubos"] if item["id"] == case["ubo_id"])
    assert ubo["client_declared_pep"] is None
    assert ubo["officer_verified_pep"] is None
    assert ubo["client_declared_pep_display"] == "Not captured"
    assert ubo["officer_verified_pep_display"] == "Not verified yet"
    assert ubo["pep_status"] == "not_verified"
    assert ubo["pep_verification_source"] == "not_verified"


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
            "field_changes": {"sector": "Crypto / Digital Assets Exchange"},
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
    assert body["after_state"]["sector"] == "Crypto / Digital Assets Exchange"
    assert body["downstream_state"]["risk_recomputed"] is True

    detail = _detail(base_url, case["app_id"])
    assert detail["sector"] == "Crypto / Digital Assets Exchange"


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
            "field_changes": {"sector": "Crypto / Digital Assets Exchange"},
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
            "evidence_source": "Screening provider report",
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
        "evidence_source": "Screening provider report",
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
    topbar_start = src.index("<!-- Top bar: back button + action buttons (horizontal) -->")
    topbar_end = src.index('<div id="detail-case-command-centre">', topbar_start)
    assert "openOfficerCorrectionModal()" not in src[topbar_start:topbar_end]
    assert "Add correction" in src
    assert "btn-prescreen-correction-mode" in src
    assert "modal-officer-correction" in src
    assert "detail-officer-corrections" in src
    assert "detail-correction-warning" in src
    assert "Company details" in src
    assert "Person / PEP information" in src
    assert "Risk information" in src
    assert "Reason / Evidence" in src
    assert "Correction saved. RegMind updated the risk, Enhanced Review requirements, memo status, and approval blockers where required." in src
    assert "Correction Target" not in src
    assert "Evidence Source" not in src
    assert "Correction Note" not in src


def test_backoffice_html_uses_tri_state_pep_copy():
    html_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "arie-backoffice.html",
    )
    with open(html_path, "r", encoding="utf-8") as handle:
        src = handle.read()
    assert "Not captured" in src
    assert "Not verified yet" in src
    assert "client_declared_pep_display" in src
    assert "officer_verified_pep_display" in src
    assert "subject.declared_pep ? 'Yes' : 'No'" not in src
    assert "subject.verified_pep ? 'Yes' : 'No'" not in src
    assert "!!d.declared_pep" not in src
    assert "!!u.verified_pep" not in src


def test_backoffice_html_simplifies_correction_history_copy():
    html_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "arie-backoffice.html",
    )
    with open(html_path, "r", encoding="utf-8") as handle:
        src = handle.read()
    assert "<strong>Field corrected:</strong>" in src
    assert "<strong>Officer:</strong>" in src
    assert "<strong>Date:</strong>" in src
    assert "<strong>Reason / Evidence:</strong>" in src
    assert "escapeHtml((item.materiality || '').toUpperCase())" not in src
