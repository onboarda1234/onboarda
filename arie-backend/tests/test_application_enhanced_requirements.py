import importlib
import json
import os
import socket
import sys
import threading
import time
import uuid

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


@pytest.fixture
def enhanced_app_db(tmp_path):
    db_path = str(tmp_path / "application_enhanced_requirements.db")
    conn = _fresh_db(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def enhanced_app_api_server(tmp_path):
    db_path = str(tmp_path / "application_enhanced_requirements_api.db")
    conn = _fresh_db(db_path)
    conn.close()

    import server as server_module

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


def _headers(role="admin", token_type="officer"):
    from auth import create_token

    user_id = {
        "admin": "admin001",
        "sco": "sco001",
        "co": "co001",
        "analyst": "analyst001",
        "client": "client001",
    }.get(role, role)
    token = create_token(user_id, role, f"Test {role}", token_type)
    return {"Authorization": f"Bearer {token}"}


def _insert_application(
    db,
    *,
    risk_level="LOW",
    country="United Kingdom",
    sector="Technology",
    ownership_structure="Simple",
    prescreening=None,
):
    app_id = "app_" + uuid.uuid4().hex[:10]
    ref = "ARF-2026-" + uuid.uuid4().hex[:8]
    db.execute(
        """
        INSERT INTO applications
        (id, ref, company_name, country, sector, entity_type,
         ownership_structure, prescreening_data, risk_score, risk_level,
         base_risk_level, final_risk_level, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            app_id,
            ref,
            "Enhanced Test Ltd",
            country,
            sector,
            "SME",
            ownership_structure,
            json.dumps(prescreening or {}),
            20 if risk_level == "LOW" else 65,
            risk_level,
            risk_level,
            risk_level,
            "submitted",
        ),
    )
    db.commit()
    return app_id


def _generate(db, app_id, source="test"):
    from enhanced_requirements import generate_application_enhanced_requirements

    result = generate_application_enhanced_requirements(
        db,
        app_id,
        actor={"sub": "admin001", "name": "Test Admin", "role": "admin"},
        generation_source=source,
    )
    db.commit()
    return result


def _count_rules(db, trigger_key):
    return db.execute(
        "SELECT COUNT(*) AS c FROM enhanced_requirement_rules WHERE trigger_key=? AND active=1",
        (trigger_key,),
    ).fetchone()["c"]


def _count_app_reqs(db, app_id, trigger_key):
    return db.execute(
        "SELECT COUNT(*) AS c FROM application_enhanced_requirements WHERE application_id=? AND trigger_key=?",
        (app_id, trigger_key),
    ).fetchone()["c"]


def test_application_enhanced_requirements_table_constraints(enhanced_app_db):
    db = enhanced_app_db
    assert db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='application_enhanced_requirements'"
    ).fetchone()

    app_id = _insert_application(db, risk_level="HIGH")
    result = _generate(db, app_id)
    assert result["generated_count"] == _count_rules(db, "high_or_very_high_risk")

    row = db.execute(
        "SELECT * FROM application_enhanced_requirements WHERE application_id=? LIMIT 1",
        (app_id,),
    ).fetchone()
    with pytest.raises(Exception):
        db.execute(
            """
            INSERT INTO application_enhanced_requirements
            (application_id, source_rule_id, trigger_key, trigger_label,
             trigger_category, requirement_key, requirement_label, audience,
             requirement_type, subject_scope)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                app_id,
                row["source_rule_id"],
                row["trigger_key"],
                row["trigger_label"],
                row["trigger_category"],
                row["requirement_key"],
                row["requirement_label"],
                row["audience"],
                row["requirement_type"],
                row["subject_scope"],
            ),
        )
    db.rollback()

    with pytest.raises(Exception):
        db.execute(
            "UPDATE application_enhanced_requirements SET status='invalid' WHERE id=?",
            (row["id"],),
        )
    db.rollback()


def test_diagnostics_pass_and_fail_when_trigger_group_inactive(enhanced_app_db):
    from enhanced_requirements import diagnose_enhanced_requirement_config

    db = enhanced_app_db
    ok = diagnose_enhanced_requirement_config(db)
    assert ok["config_ok"] is True

    db.execute("UPDATE enhanced_requirement_rules SET active=0 WHERE trigger_key='pep'")
    db.commit()
    failed = diagnose_enhanced_requirement_config(db)
    assert failed["config_ok"] is False
    assert "pep" in failed["inactive_trigger_groups"]


def test_low_application_generates_zero_requirements(enhanced_app_db):
    app_id = _insert_application(enhanced_app_db, risk_level="LOW")
    result = _generate(enhanced_app_db, app_id)
    assert result["config_ok"] is True
    assert result["triggers"] == []
    assert result["generated_count"] == 0


@pytest.mark.parametrize(
    "app_kwargs,setup,trigger_key",
    [
        ({"risk_level": "HIGH"}, None, "high_or_very_high_risk"),
        ({"risk_level": "LOW"}, "pep", "pep"),
        ({"risk_level": "LOW", "sector": "Crypto / Digital Assets Exchange"}, None, "crypto_vasp"),
        ({"risk_level": "LOW", "ownership_structure": "Complex nominee trust"}, None, "opaque_ownership"),
        ({"risk_level": "LOW", "country": "Iran"}, None, "high_risk_jurisdiction"),
        (
            {"risk_level": "LOW", "prescreening": {"screening_report": {"total_hits": 1}}},
            None,
            "screening_concern",
        ),
        (
            {"risk_level": "LOW", "prescreening": {"monthly_volume": "Over USD 5,000,000 per month"}},
            None,
            "high_volume",
        ),
    ],
)
def test_generation_maps_supported_triggers(enhanced_app_db, app_kwargs, setup, trigger_key):
    db = enhanced_app_db
    app_id = _insert_application(db, **app_kwargs)
    if setup == "pep":
        db.execute(
            "INSERT INTO directors (application_id, full_name, is_pep) VALUES (?,?,?)",
            (app_id, "PEP Director", "Yes"),
        )
        db.commit()

    result = _generate(db, app_id)
    assert result["config_ok"] is True
    assert trigger_key in result["triggers"]
    assert _count_app_reqs(db, app_id, trigger_key) == _count_rules(db, trigger_key)


def test_disabled_rules_are_not_generated(enhanced_app_db):
    db = enhanced_app_db
    db.execute(
        "UPDATE enhanced_requirement_rules SET active=0 WHERE trigger_key='high_or_very_high_risk' AND requirement_key='company_bank_reference'"
    )
    db.commit()
    app_id = _insert_application(db, risk_level="HIGH")
    result = _generate(db, app_id)
    keys = {req["requirement_key"] for req in result["requirements"]}
    assert "company_bank_reference" not in keys
    assert result["generated_count"] == _count_rules(db, "high_or_very_high_risk")


def test_generation_is_idempotent_and_preserves_reviewed_records(enhanced_app_db):
    db = enhanced_app_db
    app_id = _insert_application(db, risk_level="HIGH")
    first = _generate(db, app_id)
    assert first["generated_count"] > 0

    db.execute(
        """
        UPDATE application_enhanced_requirements
        SET status='accepted', review_notes='officer accepted', updated_by='co001'
        WHERE application_id=? AND requirement_key='company_sof_evidence'
        """,
        (app_id,),
    )
    db.commit()

    second = _generate(db, app_id)
    assert second["generated_count"] == 0
    assert second["existing_count"] == first["generated_count"]
    row = db.execute(
        """
        SELECT status, review_notes, updated_by
        FROM application_enhanced_requirements
        WHERE application_id=? AND requirement_key='company_sof_evidence'
        """,
        (app_id,),
    ).fetchone()
    assert row["status"] == "accepted"
    assert row["review_notes"] == "officer accepted"
    assert row["updated_by"] == "co001"


def test_missing_config_returns_config_not_ok_and_audits(enhanced_app_db):
    db = enhanced_app_db
    db.execute("UPDATE enhanced_requirement_rules SET active=0 WHERE trigger_key='screening_concern'")
    db.commit()
    app_id = _insert_application(db, risk_level="HIGH")
    result = _generate(db, app_id)
    assert result["config_ok"] is False
    assert result["generated_count"] == 0
    assert any("screening_concern" in error for error in result["errors"])
    audit = db.execute(
        "SELECT COUNT(*) AS c FROM audit_log WHERE action='application_enhanced_requirements.config_invalid'"
    ).fetchone()
    assert audit["c"] == 1


def test_generation_audit_events_include_created_state(enhanced_app_db):
    db = enhanced_app_db
    app_id = _insert_application(db, risk_level="HIGH")
    result = _generate(db, app_id)
    assert result["generated_count"] > 0
    rows = db.execute(
        """
        SELECT action, detail, after_state
        FROM audit_log
        WHERE action LIKE 'application_enhanced_requirement%'
        ORDER BY id
        """
    ).fetchall()
    actions = [row["action"] for row in rows]
    assert "application_enhanced_requirements.generation_attempted" in actions
    assert "application_enhanced_requirement.generated" in actions
    assert "application_enhanced_requirements.generation_completed" in actions
    generated_rows = [row for row in rows if row["action"] == "application_enhanced_requirement.generated"]
    assert generated_rows
    assert all(row["after_state"] for row in generated_rows)
    detail = json.loads(generated_rows[0]["detail"])
    assert detail["application_id"] == app_id
    assert detail["requirement_key"]


def test_api_permissions_for_diagnostics_read_and_generate(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    conn = get_db()
    app_id = _insert_application(conn, risk_level="HIGH")
    conn.close()

    diag_admin = requests.get(
        f"{base_url}/api/settings/enhanced-requirements/diagnostics",
        headers=_headers("admin"),
        timeout=5,
    )
    assert diag_admin.status_code == 200, diag_admin.text
    assert diag_admin.json()["diagnostics"]["config_ok"] is True

    diag_client = requests.get(
        f"{base_url}/api/settings/enhanced-requirements/diagnostics",
        headers=_headers("client", token_type="client"),
        timeout=5,
    )
    assert diag_client.status_code == 403

    admin_generate = requests.post(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/generate",
        headers=_headers("admin"),
        timeout=5,
    )
    assert admin_generate.status_code == 200, admin_generate.text
    assert admin_generate.json()["generated_count"] > 0

    co_generate = requests.post(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/generate",
        headers=_headers("co"),
        timeout=5,
    )
    assert co_generate.status_code == 403

    analyst_generate = requests.post(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/generate",
        headers=_headers("analyst"),
        timeout=5,
    )
    assert analyst_generate.status_code == 403

    co_read = requests.get(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements",
        headers=_headers("co"),
        timeout=5,
    )
    assert co_read.status_code == 200, co_read.text
    assert co_read.json()["total"] > 0

    client_read = requests.get(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements",
        headers=_headers("client", token_type="client"),
        timeout=5,
    )
    assert client_read.status_code == 403
