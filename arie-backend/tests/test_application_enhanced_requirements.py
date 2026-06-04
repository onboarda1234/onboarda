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


def _client_headers(client_id="client001"):
    from auth import create_token

    token = create_token(client_id, "client", f"Client {client_id}", "client")
    return {"Authorization": f"Bearer {token}"}


def _insert_application(
    db,
    *,
    risk_level="LOW",
    country="United Kingdom",
    sector="Technology",
    ownership_structure="Simple",
    prescreening=None,
    status="submitted",
    onboarding_lane=None,
):
    app_id = "app_" + uuid.uuid4().hex[:10]
    ref = "ARF-2026-" + uuid.uuid4().hex[:8]
    db.execute(
        """
        INSERT INTO applications
        (id, ref, company_name, country, sector, entity_type,
         ownership_structure, prescreening_data, risk_score, risk_level,
         base_risk_level, final_risk_level, status, onboarding_lane)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            app_id,
            ref,
            "Enhanced Test Ltd",
            country,
            sector,
            "SME",
            ownership_structure,
            json.dumps(prescreening if prescreening is not None else {"existing_bank_account": "Yes"}),
            20 if risk_level == "LOW" else 65,
            risk_level,
            risk_level,
            risk_level,
            status,
            onboarding_lane,
        ),
    )
    db.commit()
    return app_id


def _generate_for_actor(db, app_id, actor, source="test"):
    from enhanced_requirements import generate_application_enhanced_requirements

    result = generate_application_enhanced_requirements(
        db,
        app_id,
        actor=actor,
        generation_source=source,
    )
    db.commit()
    return result


def _generate(db, app_id, source="test"):
    return _generate_for_actor(
        db,
        app_id,
        {"sub": "admin001", "name": "Test Admin", "role": "admin"},
        source=source,
    )


def _first_requirement_id(db, app_id, *, offset=0):
    row = db.execute(
        """
        SELECT id FROM application_enhanced_requirements
        WHERE application_id=?
        ORDER BY id
        LIMIT 1 OFFSET ?
        """,
        (app_id, offset),
    ).fetchone()
    assert row is not None
    return row["id"]


def _requirement_id_by_key(db, app_id, requirement_key):
    row = db.execute(
        """
        SELECT id FROM application_enhanced_requirements
        WHERE application_id=? AND requirement_key=?
        ORDER BY id
        LIMIT 1
        """,
        (app_id, requirement_key),
    ).fetchone()
    assert row is not None
    return row["id"]


def _requirement_id_by_key_prefix(db, app_id, requirement_key_prefix):
    row = db.execute(
        """
        SELECT id FROM application_enhanced_requirements
        WHERE application_id=? AND requirement_key LIKE ?
        ORDER BY id
        LIMIT 1
        """,
        (app_id, requirement_key_prefix + "%"),
    ).fetchone()
    assert row is not None
    return row["id"]


def _insert_document(db, app_id, doc_id=None, *, review_status="pending"):
    doc_id = doc_id or ("doc_" + uuid.uuid4().hex[:10])
    db.execute(
        """
        INSERT INTO documents
        (id, application_id, doc_type, doc_name, file_path, verification_status, review_status)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            doc_id,
            app_id,
            "bank_statement",
            "Bank statement.pdf",
            "/tmp/bank-statement.pdf",
            "pending",
            review_status,
        ),
    )
    db.commit()
    return doc_id


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


def _apply_auto_generation(
    db,
    app_id,
    *,
    source="prescreening_submit",
    risk_dict=None,
    user=None,
):
    import routing_actuator as ra

    app_row = db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    result = ra.apply_routing_decision(
        db=db,
        app_row=app_row,
        risk_dict=risk_dict,
        user=user or {"sub": "admin001", "name": "Test Admin", "role": "admin"},
        source=source,
    )
    db.commit()
    return result


def _req_actor_rows(db, app_id):
    return db.execute(
        """
        SELECT trigger_key, requirement_key, created_by, updated_by
        FROM application_enhanced_requirements
        WHERE application_id=?
        ORDER BY id
        """,
        (app_id,),
    ).fetchall()


def _last_generation_audit(db, app_id):
    rows = db.execute(
        """
        SELECT detail
        FROM audit_log
        WHERE action='application_enhanced_requirements.generation_completed'
        ORDER BY id DESC
        """
    ).fetchall()
    for row in rows:
        detail = json.loads(row["detail"])
        if detail.get("application_id") == app_id:
            return detail
    return {}


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
    assert {"client_response_text", "client_response_at", "client_response_by"}.issubset(set(row.keys()))
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


def test_generation_fk_safe_for_client_system_and_invalid_officer_actors(enhanced_app_db):
    db = enhanced_app_db
    db.execute("PRAGMA foreign_keys = ON")

    actor_cases = (
        {"sub": "client001", "name": "Portal Client", "role": "client"},
        {"sub": "system", "name": "system", "role": "system"},
        {"sub": "missing_co", "name": "Missing CO", "role": "co"},
    )
    for actor in actor_cases:
        app_id = _insert_application(db, risk_level="HIGH")
        result = _generate_for_actor(db, app_id, actor, source="prescreening_submit")

        assert result["config_ok"] is True
        assert result["generated_count"] == _count_rules(db, "high_or_very_high_risk")
        rows = _req_actor_rows(db, app_id)
        assert rows
        assert all(row["created_by"] is None for row in rows)
        assert all(row["updated_by"] is None for row in rows)
        audit = _last_generation_audit(db, app_id)
        assert audit["actor"] == actor["sub"]
        assert audit["actor_role"] == actor["role"]
        assert audit["actor_user_fk"] is None


def test_generation_records_valid_admin_actor_fk(enhanced_app_db):
    db = enhanced_app_db
    db.execute("PRAGMA foreign_keys = ON")
    app_id = _insert_application(db, risk_level="HIGH")

    result = _generate_for_actor(
        db,
        app_id,
        {"sub": "admin001", "name": "Test Admin", "role": "admin"},
        source="manual_api",
    )

    assert result["generated_count"] == _count_rules(db, "high_or_very_high_risk")
    rows = _req_actor_rows(db, app_id)
    assert rows
    assert all(row["created_by"] == "admin001" for row in rows)
    assert all(row["updated_by"] == "admin001" for row in rows)
    audit = _last_generation_audit(db, app_id)
    assert audit["actor_user_fk"] == "admin001"


def test_client_declared_pep_director_routes_and_generates_fk_safe_requirements(enhanced_app_db):
    from enhanced_requirements import build_enhanced_requirement_operational_summary

    db = enhanced_app_db
    db.execute("PRAGMA foreign_keys = ON")
    app_id = _insert_application(
        db,
        risk_level="MEDIUM",
        status="pricing_review",
        onboarding_lane="Standard Review",
    )
    db.execute(
        "INSERT INTO directors (id, application_id, full_name, is_pep) VALUES (?,?,?,?)",
        ("dir_pep_" + uuid.uuid4().hex[:8], app_id, "Priya Declared PEP", "Yes"),
    )
    db.commit()

    result = _apply_auto_generation(
        db,
        app_id,
        user={"sub": "client001", "name": "Portal Client", "role": "client"},
    )

    assert result["route"] == "edd"
    assert "declared_pep_present" in result["triggers"]
    assert _count_app_reqs(db, app_id, "pep") == _count_rules(db, "pep")
    rows = _req_actor_rows(db, app_id)
    assert rows
    assert all(row["created_by"] is None for row in rows)
    assert all(row["updated_by"] is None for row in rows)
    rows = _req_actor_rows(db, app_id)
    assert all(row["created_by"] is None for row in rows)
    assert all(row["updated_by"] is None for row in rows)
    assert db.execute(
        "SELECT COUNT(*) AS c FROM edd_cases WHERE application_id=?",
        (app_id,),
    ).fetchone()["c"] == 0
    summary = build_enhanced_requirement_operational_summary(db, app_id)
    assert summary["enhanced_review_active"] is True
    assert summary["status_label"] != "Clear"
    assert summary["total"] == _count_rules(db, "pep")


def test_client_declared_pep_ubo_routes_and_generates_fk_safe_requirements(enhanced_app_db):
    db = enhanced_app_db
    db.execute("PRAGMA foreign_keys = ON")
    app_id = _insert_application(
        db,
        risk_level="MEDIUM",
        status="pricing_review",
        onboarding_lane="Standard Review",
    )
    db.execute(
        "INSERT INTO ubos (id, application_id, full_name, ownership_pct, is_pep) VALUES (?,?,?,?,?)",
        ("ubo_pep_" + uuid.uuid4().hex[:8], app_id, "Uma Declared PEP", 100, "Yes"),
    )
    db.commit()

    result = _apply_auto_generation(
        db,
        app_id,
        user={"sub": "client001", "name": "Portal Client", "role": "client"},
    )

    assert result["route"] == "edd"
    assert "declared_pep_present" in result["triggers"]
    assert _count_app_reqs(db, app_id, "pep") == _count_rules(db, "pep")


def test_client_crypto_opaque_route_generates_requirements_and_reuses_edd_case(enhanced_app_db):
    db = enhanced_app_db
    db.execute("PRAGMA foreign_keys = ON")
    app_id = _insert_application(
        db,
        risk_level="MEDIUM",
        sector="Crypto / Virtual Assets",
        ownership_structure="Opaque multi-layered nominee structure",
        status="pricing_review",
        onboarding_lane="Standard Review",
    )

    user = {"sub": "client001", "name": "Portal Client", "role": "client"}
    first = _apply_auto_generation(db, app_id, user=user)
    second = _apply_auto_generation(db, app_id, user=user)

    assert first["route"] == "edd"
    assert "crypto_or_virtual_asset_sector" in first["triggers"]
    assert "opaque_or_incomplete_ownership" in first["triggers"]
    assert _count_app_reqs(db, app_id, "crypto_vasp") == _count_rules(db, "crypto_vasp")
    assert _count_app_reqs(db, app_id, "opaque_ownership") == _count_rules(db, "opaque_ownership")
    assert second["enhanced_requirements_generation"]["generated_count"] == 0
    assert second["enhanced_requirements_generation"]["existing_count"] >= (
        _count_rules(db, "crypto_vasp") + _count_rules(db, "opaque_ownership")
    )
    assert db.execute(
        "SELECT COUNT(*) AS c FROM edd_cases WHERE application_id=?",
        (app_id,),
    ).fetchone()["c"] == 0


def test_client_high_risk_route_generates_high_risk_requirements(enhanced_app_db):
    db = enhanced_app_db
    db.execute("PRAGMA foreign_keys = ON")
    app_id = _insert_application(
        db,
        risk_level="HIGH",
        status="pricing_review",
        onboarding_lane="EDD",
    )

    result = _apply_auto_generation(
        db,
        app_id,
        risk_dict={"score": 72, "level": "HIGH", "final_risk_level": "HIGH", "lane": "EDD"},
        user={"sub": "client001", "name": "Portal Client", "role": "client"},
    )

    assert result["route"] == "edd"
    assert "high_or_very_high_risk" in result["triggers"]
    assert _count_app_reqs(db, app_id, "high_or_very_high_risk") == _count_rules(db, "high_or_very_high_risk")


def test_operational_summary_counts_and_next_actions(enhanced_app_db):
    from enhanced_requirements import build_enhanced_requirement_operational_summary

    db = enhanced_app_db
    standard_app = _insert_application(db, risk_level="LOW")
    standard_summary = build_enhanced_requirement_operational_summary(db, standard_app)
    assert standard_summary["enhanced_review_active"] is False
    assert standard_summary["approval_blocked"] is False
    assert standard_summary["next_action_code"] == "none"

    missing_app = _insert_application(db, risk_level="HIGH")
    missing_summary = build_enhanced_requirement_operational_summary(db, missing_app)
    assert missing_summary["enhanced_review_active"] is True
    assert missing_summary["approval_blocked"] is True
    assert missing_summary["missing_generated_requirements"] is True
    assert missing_summary["next_action_code"] == "generate_requirements"

    app_id = _insert_application(db, risk_level="HIGH")
    _generate(db, app_id)
    requested_req = _first_requirement_id(db, app_id, offset=0)
    uploaded_req = _first_requirement_id(db, app_id, offset=1)
    accepted_req = _first_requirement_id(db, app_id, offset=2)
    waived_req = _first_requirement_id(db, app_id, offset=3)
    optional_req = _first_requirement_id(db, app_id, offset=4)
    db.execute(
        "UPDATE application_enhanced_requirements SET status='requested', audience='client' WHERE id=?",
        (requested_req,),
    )
    db.execute(
        "UPDATE application_enhanced_requirements SET status='uploaded', uploaded_at=datetime('now') WHERE id=?",
        (uploaded_req,),
    )
    db.execute(
        "UPDATE application_enhanced_requirements SET status='accepted' WHERE id=?",
        (accepted_req,),
    )
    db.execute(
        """
        UPDATE application_enhanced_requirements
        SET status='waived', waived_at=datetime('now'), waived_by='sco001', waiver_reason='Senior waiver'
        WHERE id=?
        """,
        (waived_req,),
    )
    db.execute(
        """
        UPDATE application_enhanced_requirements
        SET mandatory=0, blocking_approval=0, status='generated'
        WHERE id=?
        """,
        (optional_req,),
    )
    db.commit()

    summary = build_enhanced_requirement_operational_summary(db, app_id)
    assert summary["enhanced_review_active"] is True
    assert summary["pending_client_count"] == 1
    assert summary["submitted_awaiting_review_count"] == 1
    assert summary["accepted_count"] >= 1
    assert summary["waived_count"] >= 1
    assert summary["approval_blocked"] is True
    assert summary["next_action_code"] == "awaiting_client"

    db.execute(
        """
        UPDATE application_enhanced_requirements
        SET status='accepted'
        WHERE application_id=? AND (mandatory=1 OR blocking_approval=1)
        """,
        (app_id,),
    )
    db.commit()
    resolved = build_enhanced_requirement_operational_summary(db, app_id)
    assert resolved["approval_blocked"] is False
    assert resolved["next_action_code"] == "resolved"
    assert resolved["unresolved_count"] >= 1


def test_operational_summary_invalid_waiver_blocks(enhanced_app_db):
    from enhanced_requirements import build_enhanced_requirement_operational_summary

    db = enhanced_app_db
    app_id = _insert_application(db, risk_level="HIGH")
    _generate(db, app_id)
    req_id = _first_requirement_id(db, app_id)
    db.execute(
        "UPDATE application_enhanced_requirements SET status='waived', waived_by='', waived_at='', waiver_reason='' WHERE id=?",
        (req_id,),
    )
    db.commit()

    summary = build_enhanced_requirement_operational_summary(db, app_id)
    assert summary["approval_blocked"] is True
    assert summary["invalid_waiver_count"] == 1
    assert summary["next_action_code"] == "fix_invalid_waiver"


def _screening_report_for_terminality(*, state="completed_clear", material=False, pending=False):
    api_status = "pending" if pending else "live"
    person = {
        "person_name": "Screened Director",
        "person_type": "director",
        "declared_pep": "No",
        "has_pep_hit": bool(material),
        "has_sanctions_hit": False,
        "has_adverse_media_hit": None,
        "provider_detected_pep": bool(material),
        "screening_state": "pending_provider" if pending else state,
        "screening": {
            "source": "complyadvantage",
            "api_status": api_status,
            "matched": bool(material),
            "results": [{"name": "Screened Director", "is_pep": True}] if material else [],
        },
    }
    return {
        "screened_at": "2026-05-10T10:00:00Z",
        "screening_mode": "live",
        "any_pep_hits": bool(material),
        "any_sanctions_hits": False,
        "has_adverse_media_hit": None,
        "has_company_screening_hit": False,
        "total_hits": 1 if material else 0,
        "any_non_terminal_subject": bool(pending),
        "company_screening_state": "completed_clear",
        "company_screening": {
            "source": "complyadvantage",
            "api_status": "live",
            "matched": False,
            "results": [],
        },
        "director_screenings": [person],
        "ubo_screenings": [],
    }


def test_clean_low_risk_clear_screening_does_not_generate_screening_concern(enhanced_app_db):
    from enhanced_requirements import build_enhanced_requirement_operational_summary

    db = enhanced_app_db
    app_id = _insert_application(
        db,
        risk_level="LOW",
        prescreening={"screening_report": _screening_report_for_terminality()},
    )

    result = _generate(db, app_id)

    assert "screening_concern" not in result["triggers"]
    assert _count_app_reqs(db, app_id, "screening_concern") == 0
    summary = build_enhanced_requirement_operational_summary(db, app_id)
    assert summary["approval_blocked"] is False


def test_non_terminal_screening_does_not_generate_screening_concern(enhanced_app_db):
    db = enhanced_app_db
    app_id = _insert_application(
        db,
        risk_level="LOW",
        prescreening={"screening_report": _screening_report_for_terminality(pending=True)},
    )

    result = _generate(db, app_id)

    assert "screening_concern" not in result["triggers"]
    assert _count_app_reqs(db, app_id, "screening_concern") == 0


def test_non_terminal_possible_match_metadata_does_not_generate_screening_concern(enhanced_app_db):
    db = enhanced_app_db
    app_id = _insert_application(
        db,
        risk_level="LOW",
        prescreening={
            "screening_report": _screening_report_for_terminality(
                pending=True,
                material=True,
            )
        },
    )

    result = _generate(db, app_id)

    assert "screening_concern" not in result["triggers"]
    assert _count_app_reqs(db, app_id, "screening_concern") == 0


def test_terminal_material_screening_match_generates_screening_concern(enhanced_app_db):
    db = enhanced_app_db
    app_id = _insert_application(
        db,
        risk_level="LOW",
        prescreening={
            "screening_report": _screening_report_for_terminality(
                state="completed_match",
                material=True,
            )
        },
    )

    result = _generate(db, app_id)

    assert "screening_concern" in result["triggers"]
    assert _count_app_reqs(db, app_id, "screening_concern") == _count_rules(db, "screening_concern")


@pytest.mark.parametrize(
    "volume_value",
    [
        "0-50000",
        "0 - 50,000",
        "below 50000",
        "less than 50000",
        "50k",
        "50000+",
    ],
)
def test_low_expected_volume_formats_do_not_generate_high_volume_requirements(
    enhanced_app_db,
    volume_value,
):
    db = enhanced_app_db
    app_id = _insert_application(
        db,
        risk_level="LOW",
        country="United Kingdom",
        sector="Technology",
        prescreening={"expected_volume": volume_value},
    )

    result = _generate(db, app_id)

    assert "high_volume" not in result["triggers"]
    assert _count_app_reqs(db, app_id, "high_volume") == 0
    audit = _last_generation_audit(db, app_id)
    assert audit.get("triggers") == []
    assert audit.get("trigger_sources") == {}


def test_one_million_plus_expected_volume_generates_high_volume_with_audit_reason(enhanced_app_db):
    db = enhanced_app_db
    app_id = _insert_application(
        db,
        risk_level="LOW",
        prescreening={"expected_volume": "1m+", "existing_bank_account": "Yes"},
    )

    result = _generate(db, app_id)

    assert "high_volume" in result["triggers"]
    assert _count_app_reqs(db, app_id, "high_volume") == _count_rules(db, "high_volume")
    high_volume_sources = result["trigger_sources"]["high_volume"]
    assert len(high_volume_sources) == 1
    assert "expected_volume_lower_bound_gte_threshold" in high_volume_sources[0]
    assert "normalized_amount=1000000" in high_volume_sources[0]
    audit = _last_generation_audit(db, app_id)
    assert audit["trigger_sources"]["high_volume"] == high_volume_sources
    row = db.execute(
        """
        SELECT trigger_reason
        FROM application_enhanced_requirements
        WHERE application_id=? AND trigger_key='high_volume'
        ORDER BY id
        LIMIT 1
        """,
        (app_id,),
    ).fetchone()
    assert "normalized_amount=1000000" in row["trigger_reason"]


def test_high_volume_alone_generates_documents_but_not_edd(enhanced_app_db):
    db = enhanced_app_db
    app_id = _insert_application(
        db,
        risk_level="LOW",
        status="pricing_review",
        onboarding_lane="Standard Review",
        prescreening={"expected_volume": "1m+", "existing_bank_account": "Yes"},
    )

    result = _apply_auto_generation(
        db,
        app_id,
        user={"sub": "client001", "name": "Portal Client", "role": "client"},
    )

    assert result["route"] == "standard"
    assert "high_volume" in result["enhanced_requirements_generation"]["triggers"]
    assert _count_app_reqs(db, app_id, "high_volume") == _count_rules(db, "high_volume")
    assert db.execute(
        "SELECT COUNT(*) AS c FROM edd_cases WHERE application_id=?",
        (app_id,),
    ).fetchone()["c"] == 0


def test_high_volume_plus_pep_routes_edd_due_to_pep_not_volume(enhanced_app_db):
    db = enhanced_app_db
    app_id = _insert_application(
        db,
        risk_level="LOW",
        status="pricing_review",
        onboarding_lane="Standard Review",
        prescreening={"expected_volume": "1m+", "existing_bank_account": "Yes"},
    )
    db.execute(
        "INSERT INTO directors (application_id, full_name, is_pep) VALUES (?,?,?)",
        (app_id, "Priya Declared PEP", "Yes"),
    )
    db.commit()

    result = _apply_auto_generation(
        db,
        app_id,
        user={"sub": "client001", "name": "Portal Client", "role": "client"},
    )

    assert result["route"] == "edd"
    assert "declared_pep_present" in result["triggers"]
    assert "high_volume" not in result["triggers"]
    generation = result["enhanced_requirements_generation"]
    assert "pep" in generation["triggers"]
    assert "high_volume" in generation["triggers"]


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
            {"risk_level": "LOW", "prescreening": {"monthly_volume": "Over USD 5,000,000 per month", "existing_bank_account": "Yes"}},
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


def test_auto_generation_runs_after_high_risk_routing(enhanced_app_db):
    db = enhanced_app_db
    app_id = _insert_application(db, risk_level="HIGH")

    result = _apply_auto_generation(db, app_id)

    assert result["route"] == "edd"
    generation = result["enhanced_requirements_generation"]
    assert generation["config_ok"] is True
    assert generation["generated_count"] == _count_rules(db, "high_or_very_high_risk")
    assert _count_app_reqs(db, app_id, "high_or_very_high_risk") == _count_rules(
        db, "high_or_very_high_risk"
    )
    row = db.execute(
        """
        SELECT DISTINCT generation_source
        FROM application_enhanced_requirements
        WHERE application_id=? AND trigger_key='high_or_very_high_risk'
        """,
        (app_id,),
    ).fetchone()
    assert row["generation_source"] == "prescreening_submit"


def test_auto_generation_skips_low_standard_route(enhanced_app_db):
    db = enhanced_app_db
    app_id = _insert_application(db, risk_level="LOW")

    result = _apply_auto_generation(db, app_id)

    assert result["route"] == "standard"
    assert result["enhanced_requirements_generation"] is None
    total = db.execute(
        "SELECT COUNT(*) AS c FROM application_enhanced_requirements WHERE application_id=?",
        (app_id,),
    ).fetchone()["c"]
    assert total == 0


def test_auto_generation_maps_pep_and_crypto_triggers(enhanced_app_db):
    db = enhanced_app_db
    pep_app_id = _insert_application(db, risk_level="MEDIUM")
    pep_result = _apply_auto_generation(
        db,
        pep_app_id,
        risk_dict={"level": "MEDIUM", "final_risk_level": "MEDIUM", "declared_pep_present": True},
    )
    assert pep_result["route"] == "edd"
    assert "pep" in pep_result["enhanced_requirements_generation"]["triggers"]
    assert _count_app_reqs(db, pep_app_id, "pep") == _count_rules(db, "pep")

    crypto_app_id = _insert_application(
        db,
        risk_level="MEDIUM",
        sector="Crypto / Digital Assets Exchange",
    )
    crypto_result = _apply_auto_generation(db, crypto_app_id)
    assert crypto_result["route"] == "edd"
    assert "crypto_vasp" in crypto_result["enhanced_requirements_generation"]["triggers"]
    assert _count_app_reqs(db, crypto_app_id, "crypto_vasp") == _count_rules(db, "crypto_vasp")


def test_auto_generation_detects_declared_pep_director_before_memo(enhanced_app_db):
    db = enhanced_app_db
    app_id = _insert_application(db, risk_level="MEDIUM")
    db.execute(
        "INSERT INTO directors (application_id, full_name, is_pep) VALUES (?,?,?)",
        (app_id, "Priya Declared PEP", "Yes"),
    )
    db.commit()

    result = _apply_auto_generation(db, app_id)

    assert result["route"] == "edd"
    assert "declared_pep_present" in result["triggers"]
    generation = result["enhanced_requirements_generation"]
    assert generation["config_ok"] is True
    assert "pep" in generation["triggers"]
    assert _count_app_reqs(db, app_id, "pep") == _count_rules(db, "pep")


def test_auto_generation_detects_declared_pep_ubo_before_memo(enhanced_app_db):
    db = enhanced_app_db
    app_id = _insert_application(db, risk_level="MEDIUM")
    db.execute(
        "INSERT INTO ubos (application_id, full_name, ownership_pct, is_pep) VALUES (?,?,?,?)",
        (app_id, "Priya Declared PEP", 55, "true"),
    )
    db.commit()

    result = _apply_auto_generation(db, app_id)

    assert result["route"] == "edd"
    assert "declared_pep_present" in result["triggers"]
    generation = result["enhanced_requirements_generation"]
    assert generation["config_ok"] is True
    assert "pep" in generation["triggers"]
    assert _count_app_reqs(db, app_id, "pep") == _count_rules(db, "pep")


def test_auto_generation_is_idempotent_and_preserves_reviewed(enhanced_app_db):
    db = enhanced_app_db
    app_id = _insert_application(db, risk_level="HIGH")

    first = _apply_auto_generation(db, app_id, source="risk_recompute")
    assert first["enhanced_requirements_generation"]["generated_count"] > 0

    db.execute(
        """
        UPDATE application_enhanced_requirements
        SET status='waived', waiver_reason='approved exception', updated_by='sco001'
        WHERE application_id=? AND requirement_key='company_sof_evidence'
        """,
        (app_id,),
    )
    db.commit()

    second = _apply_auto_generation(db, app_id, source="risk_recompute")
    generation = second["enhanced_requirements_generation"]
    assert generation["generated_count"] == 0
    assert generation["existing_count"] == first["enhanced_requirements_generation"]["generated_count"]
    row = db.execute(
        """
        SELECT status, waiver_reason, updated_by
        FROM application_enhanced_requirements
        WHERE application_id=? AND requirement_key='company_sof_evidence'
        """,
        (app_id,),
    ).fetchone()
    assert row["status"] == "waived"
    assert row["waiver_reason"] == "approved exception"
    assert row["updated_by"] == "sco001"


def test_auto_generation_config_invalid_is_visible_and_fail_soft(enhanced_app_db):
    db = enhanced_app_db
    db.execute("UPDATE enhanced_requirement_rules SET active=0 WHERE trigger_key='pep'")
    db.commit()
    app_id = _insert_application(db, risk_level="HIGH")

    result = _apply_auto_generation(db, app_id)

    assert result["ran"] is True
    generation = result["enhanced_requirements_generation"]
    assert generation["config_ok"] is False
    assert generation["generated_count"] == 0
    assert any("pep" in error for error in generation["errors"])
    audit = db.execute(
        """
        SELECT COUNT(*) AS c
        FROM audit_log
        WHERE action='application_enhanced_requirements.config_invalid'
        """
    ).fetchone()
    assert audit["c"] == 1


def test_auto_generation_respects_disabled_rules(enhanced_app_db):
    db = enhanced_app_db
    db.execute(
        """
        UPDATE enhanced_requirement_rules
        SET active=0
        WHERE trigger_key='high_or_very_high_risk'
          AND requirement_key='company_bank_reference'
        """
    )
    db.commit()
    app_id = _insert_application(db, risk_level="HIGH")

    result = _apply_auto_generation(db, app_id)

    keys = {
        row["requirement_key"]
        for row in db.execute(
            "SELECT requirement_key FROM application_enhanced_requirements WHERE application_id=?",
            (app_id,),
        ).fetchall()
    }
    assert result["enhanced_requirements_generation"]["config_ok"] is True
    assert "company_bank_reference" not in keys
    assert result["enhanced_requirements_generation"]["generated_count"] == _count_rules(
        db, "high_or_very_high_risk"
    )


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
    assert co_read.json()["enhanced_review_summary"]["enhanced_review_active"] is True

    client_read = requests.get(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements",
        headers=_headers("client", token_type="client"),
        timeout=5,
    )
    assert client_read.status_code == 403


def test_pr6c_backoffice_enhanced_requirements_are_typed_and_enriched(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    conn = get_db()
    app_id = _insert_application(conn, risk_level="HIGH")
    conn.execute(
        """
        INSERT INTO directors
        (application_id, person_key, full_name, nationality, is_pep, pep_declaration, date_of_birth)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            app_id,
            "director-1",
            "Amina Public",
            "AE",
            "Yes",
            json.dumps({
                "declared_pep": True,
                "client_declared_pep": True,
                "pep_role_type": "Domestic PEP",
                "position_title": "Deputy Minister",
                "pep_country_jurisdiction": "United Arab Emirates",
                "relationship_type": "self",
                "source_of_wealth_detail": "Declared business holdings.",
            }),
            "1975-01-01",
        ),
    )
    conn.commit()
    _generate(conn, app_id)
    conn.close()

    resp = requests.get(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements",
        headers=_headers("admin"),
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_key = {item["requirement_key"]: item for item in body["requirements"]}

    assert by_key["company_sof_evidence"]["requirement_display_type"] == "evidence"
    assert by_key["company_sof_evidence"]["accepts_document_upload"] is True

    declaration = by_key["pep_declaration_details"]
    assert declaration["requirement_display_type"] == "portal_disclosure"
    assert declaration["accepts_document_upload"] is False
    assert declaration["portal_disclosure"]["status_label"] == "Captured from portal"
    assert declaration["status_display_label"] == "Pending officer review"
    assert declaration["status_display_label"] != "Not submitted in portal"
    rendered = json.dumps(declaration["portal_disclosure"])
    assert "Amina Public" in rendered
    assert "Deputy Minister" in rendered
    assert "United Arab Emirates" in rendered

    jurisdiction = by_key["pep_jurisdiction"]
    assert jurisdiction["requirement_display_type"] == "portal_disclosure"
    assert jurisdiction["portal_disclosure"]["fields"][0]["value"] == "United Arab Emirates"
    assert jurisdiction["status_display_label"] == "Pending officer review"

    role = by_key["pep_role_position"]
    assert role["requirement_display_type"] == "portal_disclosure"
    assert "Deputy Minister" in json.dumps(role["portal_disclosure"])
    assert role["status_display_label"] == "Pending officer review"

    senior = by_key["mandatory_senior_review"]
    assert senior["requirement_display_type"] == "internal_control"
    assert senior["accepts_document_upload"] is False
    assert senior["internal_control"]["resolve_label"] == "Open AI Compliance Supervisor"

    monitoring = by_key["ongoing_monitoring_flag"]
    assert monitoring["requirement_display_type"] == "internal_control"
    assert monitoring["accepts_document_upload"] is False
    assert monitoring["internal_control"]["resolve_label"] == "View monitoring status"

    type_counts = body["enhanced_review_summary"]["type_counts"]
    assert type_counts["evidence"] > 0
    assert type_counts["portal_disclosure"] >= 3
    assert type_counts["internal_control"] >= 2


def test_pr6g_pep_sow_evidence_is_person_specific_for_directors_and_ubos(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    conn = get_db()
    app_id = _insert_application(conn, risk_level="LOW")
    conn.execute(
        """
        INSERT INTO directors
        (id, application_id, person_key, full_name, is_pep, pep_declaration)
        VALUES (?,?,?,?,?,?)
        """,
        (
            "dir_pep_subject",
            app_id,
            "director-1",
            "Amina Public",
            "Yes",
            json.dumps({
                "declared_pep": True,
                "pep_role_type": "Domestic PEP",
                "position_title": "Deputy Minister",
                "pep_country_jurisdiction": "United Arab Emirates",
            }),
        ),
    )
    conn.execute(
        """
        INSERT INTO ubos
        (id, application_id, person_key, full_name, ownership_pct, is_pep, pep_declaration)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            "ubo_pep_subject",
            app_id,
            "ubo-1",
            "Uma Public",
            55,
            "Yes",
            json.dumps({
                "declared_pep": True,
                "pep_role_type": "Foreign PEP",
                "position_title": "Ambassador",
                "pep_country_jurisdiction": "France",
            }),
        ),
    )
    conn.commit()
    result = _generate(conn, app_id)
    assert "pep" in result["triggers"]

    rows = conn.execute(
        """
        SELECT requirement_key, requirement_label, subject_scope, trigger_context
        FROM application_enhanced_requirements
        WHERE application_id=? AND requirement_key LIKE 'pep_sow_evidence_%'
        ORDER BY requirement_key
        """,
        (app_id,),
    ).fetchall()
    assert len(rows) == 2
    labels = {row["requirement_label"] for row in rows}
    assert "Source of Wealth Evidence — Amina Public" in labels
    assert "Source of Wealth Evidence — Uma Public" in labels
    assert all(row["subject_scope"] in {"director", "ubo"} for row in rows)
    assert conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM application_enhanced_requirements
        WHERE application_id=? AND requirement_key='pep_sow_evidence'
        """,
        (app_id,),
    ).fetchone()["c"] == 0
    subjects = [json.loads(row["trigger_context"])["subject"] for row in rows]
    assert {subject["name"] for subject in subjects} == {"Amina Public", "Uma Public"}
    conn.close()

    resp = requests.get(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements",
        headers=_headers("admin"),
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    sow_rows = [
        item for item in resp.json()["requirements"]
        if item["requirement_key"].startswith("pep_sow_evidence_")
    ]
    assert len(sow_rows) == 2
    assert {row["subject_name"] for row in sow_rows} == {"Amina Public", "Uma Public"}
    assert all(row["requirement_display_type"] == "evidence" for row in sow_rows)


def test_pr6g_jurisdiction_rationale_is_backoffice_portal_disclosure(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    conn = get_db()
    app_id = _insert_application(
        conn,
        risk_level="LOW",
        country="Iran",
        prescreening={
            "country_of_incorporation": "Iran",
            "jurisdiction_exposure_rationale": "Manufacturing partner exposure requiring enhanced controls.",
        },
    )
    _generate(conn, app_id)
    conn.close()

    resp = requests.get(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements",
        headers=_headers("admin"),
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    by_key = {item["requirement_key"]: item for item in resp.json()["requirements"]}
    rationale = by_key["jurisdiction_exposure_rationale"]
    assert rationale["requirement_display_type"] == "portal_disclosure"
    assert rationale["accepts_document_upload"] is False
    assert rationale["portal_disclosure"]["status_label"] == "Captured from portal"
    assert rationale["status_display_label"] == "Pending officer review"
    rendered = json.dumps(rationale["portal_disclosure"])
    assert "Iran" in rendered
    assert "Manufacturing partner exposure requiring enhanced controls." in rendered


def test_pr6h_volume_rationale_is_backoffice_portal_disclosure_without_pep_leakage(
    enhanced_app_api_server,
):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    conn = get_db()
    app_id = _insert_application(
        conn,
        risk_level="LOW",
        prescreening={
            "monthly_volume": "USD 500,000 to USD 5,000,000 per month",
            "expected_volume": "USD 500,000 to USD 5,000,000 per month",
            "volume_rationale_vs_business_size": (
                "Expected throughput is proportionate to signed enterprise "
                "merchant contracts and projected payment cycles."
            ),
        },
    )
    conn.execute(
        """
        INSERT INTO directors
        (application_id, person_key, full_name, nationality, is_pep, pep_declaration, date_of_birth)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            app_id,
            "director-1",
            "Amina Public",
            "AE",
            "Yes",
            json.dumps({
                "declared_pep": True,
                "client_declared_pep": True,
                "position_title": "Deputy Minister",
                "pep_country_jurisdiction": "United Arab Emirates",
            }),
            "1975-01-01",
        ),
    )
    result = _generate(conn, app_id)
    assert "high_volume" in result["triggers"]
    assert "pep" in result["triggers"]
    conn.close()

    resp = requests.get(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements",
        headers=_headers("admin"),
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    by_key = {item["requirement_key"]: item for item in resp.json()["requirements"]}

    rationale = by_key["volume_rationale_vs_business_size"]
    assert rationale["requirement_display_type"] == "portal_disclosure"
    assert rationale["accepts_document_upload"] is False
    assert rationale["portal_disclosure"]["status_label"] == "Captured from portal"
    assert rationale["status_display_label"] == "Pending officer review"
    assert rationale["status"] in {"generated", "requested", "uploaded"}
    assert rationale["status"] != "accepted"
    rendered = json.dumps(rationale["portal_disclosure"])
    assert "Expected monthly volume" in rendered
    assert "USD 500,000 to USD 5,000,000 per month" in rendered
    assert "Expected throughput is proportionate" in rendered
    assert "Deputy Minister" not in rendered
    assert "United Arab Emirates" not in rendered

    counterparties = by_key["major_counterparties_explanation"]
    assert counterparties["requirement_display_type"] == "portal_disclosure"
    assert counterparties["portal_disclosure"]["status_label"] == "Not submitted in portal"
    leaked = json.dumps(counterparties["portal_disclosure"])
    assert "Deputy Minister" not in leaked
    assert "United Arab Emirates" not in leaked


def test_pr6g_portal_and_backoffice_static_copy_is_safe():
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    portal_html = open(os.path.join(repo_root, "arie-portal.html"), encoding="utf-8").read()
    backoffice_html = open(os.path.join(repo_root, "arie-backoffice.html"), encoding="utf-8").read()

    assert "C — Additional Required Documents" in portal_html
    assert "portalEnhancedRequirementPersonPanel" in portal_html
    assert "portal-enhanced-requirements" in portal_html
    assert "f-jurisdiction-rationale" in portal_html
    assert "jurisdiction_exposure_rationale" in portal_html
    assert "syncJurisdictionRationaleState" in portal_html
    assert "f-volume-rationale" in portal_html
    assert "volume_rationale_vs_business_size" in portal_html
    assert "Volume rationale vs business size" in portal_html
    assert "HIGH_VOLUME_RATIONALE_THRESHOLD = 500000" in portal_html
    assert "isHighVolumeSelection" in portal_html
    assert "syncVolumeRationaleState" in portal_html
    assert "Volume Rationale Required" in portal_html
    assert "textarea.required = required" in portal_html
    assert "Upload enhanced evidence" not in portal_html
    assert "Required due to enhanced review" not in portal_html
    assert "risk-triggered" not in portal_html
    assert "Upload Supporting Documents" not in portal_html
    assert "Upload supporting document" not in portal_html
    assert "handlePEPUpload" not in portal_html
    assert "supporting_document_names" not in portal_html
    assert "apiErr.status = res.status" in backoffice_html
    assert "Enhanced requirement details are restricted for this role" in backoffice_html


def test_portal_disclosure_without_capture_remains_not_submitted(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    conn = get_db()
    app_id = _insert_application(conn, risk_level="HIGH")
    conn.execute(
        """
        INSERT INTO application_enhanced_requirements
        (application_id, trigger_key, trigger_label, trigger_category,
         requirement_key, requirement_label, audience, requirement_type,
         subject_scope, mandatory, blocking_approval, status, active)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            app_id,
            "pep",
            "PEP",
            "pep",
            "pep_declaration_details",
            "PEP declaration details",
            "client",
            "declaration",
            "director",
            1,
            1,
            "generated",
            1,
        ),
    )
    conn.commit()
    conn.close()

    resp = requests.get(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements",
        headers=_headers("admin"),
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    by_key = {item["requirement_key"]: item for item in resp.json()["requirements"]}
    declaration = by_key["pep_declaration_details"]
    assert declaration["requirement_display_type"] == "portal_disclosure"
    assert declaration["portal_disclosure"]["status_label"] == "Not submitted in portal"
    assert declaration["status_display_label"] == "Not submitted in portal"


def test_applications_list_includes_enhanced_operational_summary_and_filters(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    conn = get_db()
    blocked_app = _insert_application(conn, risk_level="HIGH")
    _generate(conn, blocked_app)
    pending_req = _first_requirement_id(conn, blocked_app)
    conn.execute(
        "UPDATE application_enhanced_requirements SET status='requested', audience='client' WHERE id=?",
        (pending_req,),
    )
    uploaded_req = _first_requirement_id(conn, blocked_app, offset=1)
    conn.execute(
        "UPDATE application_enhanced_requirements SET status='uploaded', audience='client' WHERE id=?",
        (uploaded_req,),
    )
    resolved_app = _insert_application(conn, risk_level="HIGH")
    _generate(conn, resolved_app)
    conn.execute(
        """
        UPDATE application_enhanced_requirements
        SET status='accepted'
        WHERE application_id=? AND (mandatory=1 OR blocking_approval=1)
        """,
        (resolved_app,),
    )
    standard_app = _insert_application(conn, risk_level="LOW")
    conn.close()

    list_resp = requests.get(
        f"{base_url}/api/applications?view=list&limit=50",
        headers=_headers("admin"),
        timeout=5,
    )
    assert list_resp.status_code == 200, list_resp.text
    apps = {app["id"]: app for app in list_resp.json()["applications"]}
    assert apps[blocked_app]["enhanced_review_summary"]["approval_blocked"] is True
    assert apps[blocked_app]["enhanced_review_summary"]["pending_client_count"] == 1
    assert apps[resolved_app]["enhanced_review_summary"]["next_action_code"] == "resolved"
    assert apps[standard_app]["enhanced_review_summary"]["enhanced_review_active"] is False

    pending_resp = requests.get(
        f"{base_url}/api/applications?view=list&limit=50&enhanced_review=pending_client",
        headers=_headers("admin"),
        timeout=5,
    )
    assert pending_resp.status_code == 200, pending_resp.text
    pending_ids = {app["id"] for app in pending_resp.json()["applications"]}
    assert blocked_app in pending_ids
    assert resolved_app not in pending_ids

    active_resp = requests.get(
        f"{base_url}/api/applications?view=list&limit=50&enhanced_review=active",
        headers=_headers("admin"),
        timeout=5,
    )
    assert active_resp.status_code == 200, active_resp.text
    active_ids = {app["id"] for app in active_resp.json()["applications"]}
    assert blocked_app in active_ids
    assert resolved_app in active_ids
    assert standard_app not in active_ids

    awaiting_resp = requests.get(
        f"{base_url}/api/applications?view=list&limit=50&enhanced_review=awaiting_review",
        headers=_headers("admin"),
        timeout=5,
    )
    assert awaiting_resp.status_code == 200, awaiting_resp.text
    awaiting_ids = {app["id"] for app in awaiting_resp.json()["applications"]}
    assert blocked_app in awaiting_ids
    assert resolved_app not in awaiting_ids

    blocked_resp = requests.get(
        f"{base_url}/api/applications?view=list&limit=50&enhanced_review=approval_blocked",
        headers=_headers("admin"),
        timeout=5,
    )
    assert blocked_resp.status_code == 200, blocked_resp.text
    blocked_ids = {app["id"] for app in blocked_resp.json()["applications"]}
    assert blocked_app in blocked_ids
    assert resolved_app not in blocked_ids

    resolved_resp = requests.get(
        f"{base_url}/api/applications?view=list&limit=50&enhanced_review=resolved",
        headers=_headers("admin"),
        timeout=5,
    )
    assert resolved_resp.status_code == 200, resolved_resp.text
    resolved_ids = {app["id"] for app in resolved_resp.json()["applications"]}
    assert resolved_app in resolved_ids
    assert blocked_app not in resolved_ids

    client_resp = requests.get(
        f"{base_url}/api/applications?limit=50",
        headers=_client_headers("client001"),
        timeout=5,
    )
    assert client_resp.status_code == 200, client_resp.text
    assert all("enhanced_review_summary" not in app for app in client_resp.json()["applications"])


def test_applications_enhanced_filter_sql_uses_postgres_safe_flag_predicates():
    import inspect
    import server

    source = inspect.getsource(server.ApplicationsHandler.get)
    assert "aer.active = 1" in source
    assert "(aer.mandatory = 1 OR aer.blocking_approval = 1)" in source
    assert "AND aer.active)" not in source
    assert "AND aer.active " not in source
    assert "AND (aer.mandatory OR aer.blocking_approval)" not in source


def test_lifecycle_api_permissions_and_status_updates(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    conn = get_db()
    app_id = _insert_application(conn, risk_level="HIGH")
    _generate(conn, app_id)
    req_admin = _first_requirement_id(conn, app_id, offset=0)
    req_sco = _first_requirement_id(conn, app_id, offset=1)
    req_co = _first_requirement_id(conn, app_id, offset=2)
    req_denied = _first_requirement_id(conn, app_id, offset=3)
    conn.close()

    admin_update = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_admin}",
        headers=_headers("admin"),
        json={"status": "under_review", "review_notes": "Admin opened review"},
        timeout=5,
    )
    assert admin_update.status_code == 200, admin_update.text
    assert admin_update.json()["requirement"]["status"] == "under_review"
    assert admin_update.json()["requirement"]["review_notes"] == "Admin opened review"

    sco_update = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_sco}",
        headers=_headers("sco"),
        json={"status": "accepted", "review_notes": "SCO accepted"},
        timeout=5,
    )
    assert sco_update.status_code == 200, sco_update.text
    assert sco_update.json()["requirement"]["status"] == "accepted"

    co_update = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_co}",
        headers=_headers("co"),
        json={"status": "rejected", "review_notes": "CO rejected for follow-up"},
        timeout=5,
    )
    assert co_update.status_code == 200, co_update.text
    assert co_update.json()["requirement"]["status"] == "rejected"

    analyst_update = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_denied}",
        headers=_headers("analyst"),
        json={"status": "under_review"},
        timeout=5,
    )
    assert analyst_update.status_code == 403

    client_update = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_denied}",
        headers=_headers("client", token_type="client"),
        json={"status": "under_review"},
        timeout=5,
    )
    assert client_update.status_code == 403


def test_lifecycle_waiver_requires_senior_reason_and_audits(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    conn = get_db()
    app_id = _insert_application(conn, risk_level="HIGH")
    _generate(conn, app_id)
    req_id = _first_requirement_id(conn, app_id)
    non_waivable_req = _first_requirement_id(conn, app_id, offset=1)
    conn.execute(
        "UPDATE application_enhanced_requirements SET waivable=0 WHERE id=?",
        (non_waivable_req,),
    )
    conn.commit()
    conn.close()

    co_waive = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_id}",
        headers=_headers("co"),
        json={"status": "waived", "waiver_reason": "CO should not waive"},
        timeout=5,
    )
    assert co_waive.status_code == 403

    missing_reason = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_id}",
        headers=_headers("admin"),
        json={"status": "waived", "waiver_reason": ""},
        timeout=5,
    )
    assert missing_reason.status_code == 400

    non_waivable = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{non_waivable_req}",
        headers=_headers("admin"),
        json={"status": "waived", "waiver_reason": "Should not waive non-waivable control"},
        timeout=5,
    )
    assert non_waivable.status_code == 400

    admin_waive = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_id}",
        headers=_headers("admin"),
        json={"status": "waived", "waiver_reason": "Equivalent document already reviewed"},
        timeout=5,
    )
    assert admin_waive.status_code == 200, admin_waive.text
    waived = admin_waive.json()["requirement"]
    assert waived["status"] == "waived"
    assert waived["waiver_reason"] == "Equivalent document already reviewed"
    assert waived["waived_by"] == "admin001"
    assert waived["waived_at"]

    conn = get_db()
    actions = {
        row["action"]: row
        for row in conn.execute(
            """
            SELECT action, before_state, after_state
            FROM audit_log
            WHERE target=(SELECT 'application:' || ref FROM applications WHERE id=?)
              AND action LIKE 'application_enhanced_requirement.%'
            """,
            (app_id,),
        ).fetchall()
    }
    conn.close()
    assert "application_enhanced_requirement.updated" in actions
    assert "application_enhanced_requirement.status_changed" in actions
    assert "application_enhanced_requirement.waived" in actions
    before = json.loads(actions["application_enhanced_requirement.waived"]["before_state"])
    after = json.loads(actions["application_enhanced_requirement.waived"]["after_state"])
    assert before["status"] == "generated"
    assert after["status"] == "waived"
    assert after["waiver_reason"] == "Equivalent document already reviewed"


def test_lifecycle_document_link_validation_and_invalid_status(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    conn = get_db()
    app_id = _insert_application(conn, risk_level="HIGH")
    other_app_id = _insert_application(conn, risk_level="LOW")
    _generate(conn, app_id)
    req_id = _first_requirement_id(conn, app_id)
    valid_doc = _insert_document(conn, app_id, "doc_valid_link")
    other_doc = _insert_document(conn, other_app_id, "doc_wrong_app")
    conn.close()

    bad_status = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_id}",
        headers=_headers("admin"),
        json={"status": "requested"},
        timeout=5,
    )
    assert bad_status.status_code == 400

    wrong_doc = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_id}",
        headers=_headers("admin"),
        json={"linked_document_id": other_doc},
        timeout=5,
    )
    assert wrong_doc.status_code == 400

    valid_link = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_id}",
        headers=_headers("admin"),
        json={"linked_document_id": valid_doc, "review_notes": "Linked existing upload"},
        timeout=5,
    )
    assert valid_link.status_code == 200, valid_link.text
    assert valid_link.json()["requirement"]["linked_document_id"] == valid_doc

    conn = get_db()
    doc = conn.execute(
        "SELECT review_status, verification_status FROM documents WHERE id=?",
        (valid_doc,),
    ).fetchone()
    audit = conn.execute(
        """
        SELECT before_state, after_state
        FROM audit_log
        WHERE action='application_enhanced_requirement.document_linked'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()
    assert doc["review_status"] == "pending"
    assert doc["verification_status"] == "pending"
    assert json.loads(audit["before_state"])["linked_document_id"] is None
    assert json.loads(audit["after_state"])["linked_document_id"] == valid_doc


def test_backoffice_enhanced_requirement_upload_links_document_under_review_and_audits(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db
    import server as server_module

    original_has_s3 = server_module.HAS_S3
    server_module.HAS_S3 = False
    try:
        conn = get_db()
        app_id = _insert_application(conn, risk_level="HIGH", status="kyc_documents")
        conn.execute(
            "UPDATE applications SET pre_approval_decision='PRE_APPROVE' WHERE id=?",
            (app_id,),
        )
        _generate(conn, app_id)
        req_id = _first_requirement_id(conn, app_id)
        conn.commit()
        conn.close()

        resp = requests.post(
            f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_id}/upload",
            headers=_headers("co"),
            files={"file": ("source-of-funds.pdf", b"%PDF-1.4\n% officer enhanced evidence\n", "application/pdf")},
            timeout=5,
        )
    finally:
        server_module.HAS_S3 = original_has_s3

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "uploaded"
    assert body["requirement"]["status"] == "under_review"
    assert body["requirement"]["linked_document_id"] == body["document"]["id"]
    assert body["requirement"]["linked_document"]["id"] == body["document"]["id"]
    assert body["requirement"]["linked_document"]["verification_status"] == "pending"
    assert body["requirement"]["linked_document"]["verification_status_label"] == "Verification pending"
    assert body["document"]["doc_type"] == "enhanced_requirement"
    assert body["document"]["verification_status"] == "pending"
    assert body["document"]["verification_status_label"] == "Verification pending"
    assert body["agent1_verification"]["triggered"] is False

    conn = get_db()
    req = conn.execute("SELECT * FROM application_enhanced_requirements WHERE id=?", (req_id,)).fetchone()
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (req["linked_document_id"],)).fetchone()
    audit = conn.execute(
        """
        SELECT detail, before_state, after_state
        FROM audit_log
        WHERE action='application_enhanced_requirement.officer_uploaded'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert req["status"] == "under_review"
    assert req["linked_document_id"] == body["document"]["id"]
    assert req["uploaded_at"]
    assert doc["doc_name"] == "source-of-funds.pdf"
    assert doc["doc_type"] == "enhanced_requirement"
    assert doc["slot_key"] == f"enhanced_requirement:{req_id}"
    assert doc["verification_status"] == "pending"
    metadata = json.loads(doc["verification_results"])
    assert metadata["source_surface"] == "kyc_enhanced_requirement_row"
    assert metadata["enhanced_requirement_id"] == str(req_id)
    assert metadata["verification_triggered"] is False
    assert audit is not None
    detail = json.loads(audit["detail"])
    before = json.loads(audit["before_state"])
    after = json.loads(audit["after_state"])
    assert detail["application_id"] == app_id
    assert detail["requirement_id"] == req_id
    assert detail["document_id"] == doc["id"]
    assert detail["filename"] == "source-of-funds.pdf"
    assert detail["source_surface"] == "kyc_enhanced_requirement_row"
    assert detail["resulting_requirement_status"] == "under_review"
    assert detail["auto_accepted"] is False
    assert before["status"] == "generated"
    assert after["status"] == "under_review"

    detail_resp = requests.get(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements",
        headers=_headers("admin"),
        timeout=5,
    )
    assert detail_resp.status_code == 200, detail_resp.text
    rendered_req = next(item for item in detail_resp.json()["requirements"] if item["id"] == req_id)
    assert rendered_req["linked_document"]["id"] == body["document"]["id"]
    assert rendered_req["linked_document"]["verification_status_label"] == "Verification pending"


def test_enhanced_requirement_linked_document_verification_status_renders_in_api(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    conn = get_db()
    app_id = _insert_application(conn, risk_level="HIGH", status="kyc_documents")
    _generate(conn, app_id)
    req_id = _first_requirement_id(conn, app_id)
    doc_id = _insert_document(conn, app_id, "doc_verified_enhanced", review_status="pending")
    conn.execute(
        "UPDATE documents SET doc_type='enhanced_requirement', verification_status='verified', slot_key=? WHERE id=?",
        (f"enhanced_requirement:{req_id}", doc_id),
    )
    conn.execute(
        "UPDATE application_enhanced_requirements SET status='under_review', linked_document_id=? WHERE id=?",
        (doc_id, req_id),
    )
    conn.commit()
    conn.close()

    resp = requests.get(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements",
        headers=_headers("admin"),
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    req = next(item for item in resp.json()["requirements"] if item["id"] == req_id)
    assert req["status"] == "under_review"
    assert req["linked_document"]["id"] == doc_id
    assert req["linked_document"]["verification_status"] == "verified"
    assert req["linked_document"]["verification_status_label"] == "Verified"


def test_backoffice_enhanced_requirement_upload_validation_permissions_and_gates(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db
    import server as server_module

    original_has_s3 = server_module.HAS_S3
    original_max_upload_mb = server_module.MAX_UPLOAD_MB
    server_module.HAS_S3 = False
    try:
        conn = get_db()
        app_id = _insert_application(conn, risk_level="HIGH", status="kyc_documents")
        other_app_id = _insert_application(conn, risk_level="HIGH", status="kyc_documents")
        locked_app_id = _insert_application(conn, risk_level="HIGH", status="submitted")
        conn.execute(
            "UPDATE applications SET pre_approval_decision='PRE_APPROVE' WHERE id IN (?,?)",
            (app_id, other_app_id),
        )
        _generate(conn, app_id)
        _generate(conn, other_app_id)
        _generate(conn, locked_app_id)
        req_id = _first_requirement_id(conn, app_id)
        other_req_id = _first_requirement_id(conn, other_app_id)
        locked_req_id = _first_requirement_id(conn, locked_app_id)
        explanation_req = _first_requirement_id(conn, app_id, offset=1)
        conn.execute(
            "UPDATE application_enhanced_requirements SET requirement_type='explanation' WHERE id=?",
            (explanation_req,),
        )
        accepted_req = _first_requirement_id(conn, app_id, offset=2)
        conn.execute(
            "UPDATE application_enhanced_requirements SET status='accepted' WHERE id=?",
            (accepted_req,),
        )
        conn.commit()
        conn.close()

        analyst = requests.post(
            f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_id}/upload",
            headers=_headers("analyst"),
            files={"file": ("evidence.pdf", b"%PDF-1.4\n% evidence\n", "application/pdf")},
            timeout=5,
        )
        wrong_app = requests.post(
            f"{base_url}/api/applications/{app_id}/enhanced-requirements/{other_req_id}/upload",
            headers=_headers("co"),
            files={"file": ("evidence.pdf", b"%PDF-1.4\n% evidence\n", "application/pdf")},
            timeout=5,
        )
        missing_req = requests.post(
            f"{base_url}/api/applications/{app_id}/enhanced-requirements/999999/upload",
            headers=_headers("co"),
            files={"file": ("evidence.pdf", b"%PDF-1.4\n% evidence\n", "application/pdf")},
            timeout=5,
        )
        invalid_type = requests.post(
            f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_id}/upload",
            headers=_headers("co"),
            files={"file": ("malware.exe", b"MZ...", "application/octet-stream")},
            timeout=5,
        )
        server_module.MAX_UPLOAD_MB = 0
        oversized = requests.post(
            f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_id}/upload",
            headers=_headers("co"),
            files={"file": ("oversized.pdf", b"%PDF-1.4\n% evidence\n", "application/pdf")},
            timeout=5,
        )
        server_module.MAX_UPLOAD_MB = original_max_upload_mb
        text_req = requests.post(
            f"{base_url}/api/applications/{app_id}/enhanced-requirements/{explanation_req}/upload",
            headers=_headers("co"),
            files={"file": ("evidence.pdf", b"%PDF-1.4\n% evidence\n", "application/pdf")},
            timeout=5,
        )
        accepted = requests.post(
            f"{base_url}/api/applications/{app_id}/enhanced-requirements/{accepted_req}/upload",
            headers=_headers("co"),
            files={"file": ("evidence.pdf", b"%PDF-1.4\n% evidence\n", "application/pdf")},
            timeout=5,
        )
        locked = requests.post(
            f"{base_url}/api/applications/{locked_app_id}/enhanced-requirements/{locked_req_id}/upload",
            headers=_headers("co"),
            files={"file": ("evidence.pdf", b"%PDF-1.4\n% evidence\n", "application/pdf")},
            timeout=5,
        )
    finally:
        server_module.HAS_S3 = original_has_s3
        server_module.MAX_UPLOAD_MB = original_max_upload_mb

    assert analyst.status_code == 403
    assert wrong_app.status_code == 404
    assert missing_req.status_code == 404
    assert invalid_type.status_code == 400
    assert oversized.status_code == 400
    assert "File exceeds" in oversized.json()["error"]
    assert text_req.status_code == 400
    assert accepted.status_code == 409
    assert locked.status_code == 409
    assert "KYC upload is locked" in locked.json()["error"]


def test_lifecycle_reopen_waived_requires_admin_or_sco(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    conn = get_db()
    app_id = _insert_application(conn, risk_level="HIGH")
    _generate(conn, app_id)
    req_id = _first_requirement_id(conn, app_id)
    conn.close()

    waived = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_id}",
        headers=_headers("sco"),
        json={"status": "waived", "waiver_reason": "SCO waiver"},
        timeout=5,
    )
    assert waived.status_code == 200, waived.text

    co_reopen = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_id}",
        headers=_headers("co"),
        json={"status": "under_review"},
        timeout=5,
    )
    assert co_reopen.status_code == 403

    admin_reopen = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_id}",
        headers=_headers("admin"),
        json={"status": "under_review", "review_notes": "Reopened for review"},
        timeout=5,
    )
    assert admin_reopen.status_code == 200, admin_reopen.text
    reopened = admin_reopen.json()["requirement"]
    assert reopened["status"] == "under_review"
    assert reopened["waiver_reason"] is None


def test_lifecycle_reopen_accepted_requires_senior_reason_and_audits(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    conn = get_db()
    app_id = _insert_application(conn, risk_level="HIGH")
    _generate(conn, app_id)
    admin_req = _first_requirement_id(conn, app_id, offset=0)
    sco_req = _first_requirement_id(conn, app_id, offset=1)
    conn.execute(
        "UPDATE application_enhanced_requirements SET status='accepted' WHERE id IN (?,?)",
        (admin_req, sco_req),
    )
    conn.commit()
    conn.close()

    co_reopen = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{admin_req}",
        headers=_headers("co"),
        json={"status": "under_review", "review_notes": "CO correction attempt"},
        timeout=5,
    )
    assert co_reopen.status_code == 403

    missing_reason = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{admin_req}",
        headers=_headers("admin"),
        json={"status": "under_review", "review_notes": ""},
        timeout=5,
    )
    assert missing_reason.status_code == 400

    admin_reopen = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{admin_req}",
        headers=_headers("admin"),
        json={"status": "under_review", "review_notes": "Accepted in error; rechecking evidence"},
        timeout=5,
    )
    assert admin_reopen.status_code == 200, admin_reopen.text
    assert admin_reopen.json()["requirement"]["status"] == "under_review"
    assert admin_reopen.json()["requirement"]["review_notes"] == "Accepted in error; rechecking evidence"

    sco_reopen = requests.patch(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{sco_req}",
        headers=_headers("sco"),
        json={"status": "under_review", "reopen_reason": "SCO reopened after second review"},
        timeout=5,
    )
    assert sco_reopen.status_code == 200, sco_reopen.text
    assert sco_reopen.json()["requirement"]["status"] == "under_review"

    conn = get_db()
    audit = conn.execute(
        """
        SELECT before_state, after_state
        FROM audit_log
        WHERE action='application_enhanced_requirement.status_changed'
          AND before_state LIKE '%"status": "accepted"%'
          AND after_state LIKE '%"status": "under_review"%'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()
    assert audit is not None
    before = json.loads(audit["before_state"])
    after = json.loads(audit["after_state"])
    assert before["status"] == "accepted"
    assert after["status"] == "under_review"


def test_request_from_client_permissions_status_and_audit(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    conn = get_db()
    app_id = _insert_application(conn, risk_level="HIGH")
    _generate(conn, app_id)
    admin_req = _requirement_id_by_key(conn, app_id, "company_bank_reference")
    sco_req = _requirement_id_by_key(conn, app_id, "company_bank_statements_6m")
    co_req = _requirement_id_by_key(conn, app_id, "company_sof_evidence")
    denied_req = _requirement_id_by_key(conn, app_id, "material_ubo_sow_evidence")
    linked_req = _requirement_id_by_key(conn, app_id, "enhanced_business_activity_explanation")
    doc_id = _insert_document(conn, app_id, "doc_request_preserved", review_status="pending")
    conn.execute(
        """
        UPDATE application_enhanced_requirements
        SET status = CASE id
            WHEN ? THEN 'under_review'
            WHEN ? THEN 'rejected'
            ELSE status
        END
        WHERE id IN (?,?)
        """,
        (sco_req, co_req, sco_req, co_req),
    )
    conn.execute(
        "UPDATE application_enhanced_requirements SET linked_document_id=? WHERE id=?",
        (doc_id, linked_req),
    )
    conn.commit()
    conn.close()

    admin_resp = requests.post(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{admin_req}/request",
        headers=_headers("admin"),
        timeout=5,
    )
    assert admin_resp.status_code == 200, admin_resp.text
    admin_body = admin_resp.json()
    assert admin_body["requirement"]["status"] == "requested"
    assert admin_body["requirement"]["requested_by"] == "admin001"
    assert admin_body["requirement"]["requested_at"]
    assert admin_body["requirement"]["linked_rmi_item_id"] in (None, "")
    assert admin_body["client_request"]["label"]
    assert "trigger_context" not in admin_body["client_request"]
    assert admin_body["rmi_integration"] == "deferred"

    sco_resp = requests.post(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{sco_req}/request",
        headers=_headers("sco"),
        timeout=5,
    )
    assert sco_resp.status_code == 200, sco_resp.text
    assert sco_resp.json()["requirement"]["requested_by"] == "sco001"

    co_resp = requests.post(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{co_req}/request",
        headers=_headers("co"),
        timeout=5,
    )
    assert co_resp.status_code == 200, co_resp.text
    assert co_resp.json()["requirement"]["requested_by"] == "co001"

    analyst_resp = requests.post(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{denied_req}/request",
        headers=_headers("analyst"),
        timeout=5,
    )
    assert analyst_resp.status_code == 403

    client_resp = requests.post(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{denied_req}/request",
        headers=_headers("client", token_type="client"),
        timeout=5,
    )
    assert client_resp.status_code == 403

    linked_resp = requests.post(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{linked_req}/request",
        headers=_headers("admin"),
        timeout=5,
    )
    assert linked_resp.status_code == 200, linked_resp.text
    assert linked_resp.json()["requirement"]["linked_document_id"] == doc_id

    repeat_resp = requests.post(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{admin_req}/request",
        headers=_headers("admin"),
        timeout=5,
    )
    assert repeat_resp.status_code == 409

    conn = get_db()
    app = conn.execute("SELECT status, decision_notes FROM applications WHERE id=?", (app_id,)).fetchone()
    doc = conn.execute(
        "SELECT review_status, verification_status FROM documents WHERE id=?",
        (doc_id,),
    ).fetchone()
    rmi_count = conn.execute(
        "SELECT COUNT(*) AS c FROM rmi_requests WHERE application_id=?",
        (app_id,),
    ).fetchone()["c"]
    notification_count = conn.execute(
        "SELECT COUNT(*) AS c FROM client_notifications WHERE application_id=?",
        (app_id,),
    ).fetchone()["c"]
    audit = conn.execute(
        """
        SELECT detail, before_state, after_state
        FROM audit_log
        WHERE action='application_enhanced_requirement.requested_from_client'
          AND target=(SELECT 'application:' || ref FROM applications WHERE id=?)
        ORDER BY id DESC LIMIT 1
        """,
        (app_id,),
    ).fetchone()
    conn.close()

    assert app["status"] == "submitted"
    assert app["decision_notes"] in (None, "")
    assert doc["review_status"] == "pending"
    assert doc["verification_status"] == "pending"
    assert rmi_count == 0
    assert notification_count == 0
    assert audit is not None
    detail = json.loads(audit["detail"])
    before = json.loads(audit["before_state"])
    after = json.loads(audit["after_state"])
    assert detail["old_status"] in ("generated", "under_review", "rejected")
    assert detail["new_status"] == "requested"
    assert detail["requested_by"] == "admin001"
    assert detail["linked_rmi_item_id"] in (None, "")
    assert before["status"] in ("generated", "under_review", "rejected")
    assert after["status"] == "requested"


def test_request_from_client_rejects_ineligible_requirements(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    conn = get_db()
    app_id = _insert_application(conn, risk_level="HIGH")
    _generate(conn, app_id)
    accepted_req = _requirement_id_by_key(conn, app_id, "company_bank_reference")
    waived_req = _requirement_id_by_key(conn, app_id, "company_bank_statements_6m")
    cancelled_req = _requirement_id_by_key(conn, app_id, "company_sof_evidence")
    uploaded_req = _requirement_id_by_key(conn, app_id, "material_ubo_sow_evidence")
    unsafe_req = _requirement_id_by_key(conn, app_id, "enhanced_business_activity_explanation")
    conn.execute(
        """
        UPDATE application_enhanced_requirements
        SET status = CASE id
            WHEN ? THEN 'accepted'
            WHEN ? THEN 'waived'
            WHEN ? THEN 'cancelled'
            WHEN ? THEN 'uploaded'
            ELSE status
        END
        WHERE id IN (?,?,?,?)
        """,
        (
            accepted_req,
            waived_req,
            cancelled_req,
            uploaded_req,
            accepted_req,
            waived_req,
            cancelled_req,
            uploaded_req,
        ),
    )
    conn.execute(
        """
        UPDATE application_enhanced_requirements
        SET source_rule_id=NULL,
            requirement_label='Internal risk level screening concern'
        WHERE id=?
        """,
        (unsafe_req,),
    )

    pep_app_id = _insert_application(conn, risk_level="LOW")
    conn.execute(
        "INSERT INTO directors (application_id, full_name, is_pep) VALUES (?,?,?)",
        (pep_app_id, "PEP Director", "Yes"),
    )
    conn.commit()
    _generate(conn, pep_app_id)
    backoffice_req = _requirement_id_by_key(conn, pep_app_id, "mandatory_senior_review")
    conn.close()

    for req_id in (accepted_req, waived_req, cancelled_req, uploaded_req):
        resp = requests.post(
            f"{base_url}/api/applications/{app_id}/enhanced-requirements/{req_id}/request",
            headers=_headers("admin"),
            timeout=5,
        )
        assert resp.status_code == 400, resp.text

    unsafe_resp = requests.post(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements/{unsafe_req}/request",
        headers=_headers("admin"),
        timeout=5,
    )
    assert unsafe_resp.status_code == 200
    assert unsafe_resp.json()["requirement"]["status"] == "requested"

    backoffice_resp = requests.post(
        f"{base_url}/api/applications/{pep_app_id}/enhanced-requirements/{backoffice_req}/request",
        headers=_headers("admin"),
        timeout=5,
    )
    assert backoffice_resp.status_code == 400
    assert "Back-office-only" in backoffice_resp.text


def test_portal_enhanced_requirements_are_client_safe_and_owned(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    client_id = "client001"
    other_client_id = "client002"
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?,?,?,?)",
        (client_id, "client001@example.com", "hash", "Client One"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?,?,?,?)",
        (other_client_id, "client002@example.com", "hash", "Client Two"),
    )
    app_id = _insert_application(conn, risk_level="HIGH")
    conn.execute("UPDATE applications SET client_id=? WHERE id=?", (client_id, app_id))
    conn.execute(
        "INSERT INTO directors (application_id, full_name, is_pep) VALUES (?,?,?)",
        (app_id, "Declared Person", "Yes"),
    )
    conn.commit()
    _generate(conn, app_id)

    requested_req = _requirement_id_by_key(conn, app_id, "company_bank_reference")
    uploaded_req = _requirement_id_by_key(conn, app_id, "company_bank_statements_6m")
    under_review_req = _requirement_id_by_key(conn, app_id, "company_sof_evidence")
    rejected_req = _requirement_id_by_key(conn, app_id, "material_ubo_sow_evidence")
    accepted_req = _requirement_id_by_key_prefix(conn, app_id, "pep_sow_evidence_")
    waived_req = _requirement_id_by_key(conn, app_id, "pep_linked_sof_evidence")
    pep_requested_req = _requirement_id_by_key(conn, app_id, "pep_declaration_details")
    backoffice_req = _requirement_id_by_key(conn, app_id, "mandatory_senior_review")
    conn.execute(
        """
        UPDATE application_enhanced_requirements
        SET status = CASE id
            WHEN ? THEN 'requested'
            WHEN ? THEN 'uploaded'
            WHEN ? THEN 'under_review'
            WHEN ? THEN 'rejected'
            WHEN ? THEN 'accepted'
            WHEN ? THEN 'waived'
            WHEN ? THEN 'requested'
            WHEN ? THEN 'requested'
            ELSE status
        END,
        requested_at = CASE WHEN id IN (?,?,?,?,?,?) THEN datetime('now') ELSE requested_at END,
        requested_by = CASE WHEN id IN (?,?,?,?,?,?) THEN 'co001' ELSE requested_by END
        WHERE application_id=?
        """,
        (
            requested_req,
            uploaded_req,
            under_review_req,
            rejected_req,
            accepted_req,
            waived_req,
            pep_requested_req,
            backoffice_req,
            requested_req,
            uploaded_req,
            under_review_req,
            rejected_req,
            pep_requested_req,
            backoffice_req,
            requested_req,
            uploaded_req,
            under_review_req,
            rejected_req,
            pep_requested_req,
            backoffice_req,
            app_id,
        ),
    )
    conn.commit()
    conn.close()

    resp = requests.get(
        f"{base_url}/api/portal/applications/{app_id}/enhanced-requirements",
        headers=_client_headers(client_id),
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["application_id"] == app_id
    requirements = body["requirements"]
    returned_ids = {item["id"] for item in requirements}
    assert returned_ids == {requested_req, uploaded_req, under_review_req, rejected_req, pep_requested_req}
    assert {item["status"] for item in requirements} == {
        "required",
        "submitted",
        "under_review",
        "additional_information_needed",
    }
    assert body["total"] == 5

    forbidden_fields = {
        "trigger_key",
        "trigger_label",
        "trigger_category",
        "trigger_reason",
        "trigger_context",
        "internal_notes",
        "review_notes",
        "waiver_reason",
        "waived_by",
        "requested_by",
        "audit",
    }
    forbidden_text = (
        "screening concern",
        "screening",
        "sanctions concern",
        "pep",
        "politically exposed",
        "edd",
        "enhanced due diligence",
        "high risk",
        "very high",
        "approval blocker",
        "internal",
        "officer notes",
        "waiver",
    )
    for item in requirements:
        assert not forbidden_fields.intersection(item)
        assert item["label"]
        assert item["status_label"] in {
            "Required",
            "Submitted",
            "Under review",
            "Additional information needed",
        }
        rendered = json.dumps(item).lower()
        for term in forbidden_text:
            assert term not in rendered

    other_resp = requests.get(
        f"{base_url}/api/portal/applications/{app_id}/enhanced-requirements",
        headers=_client_headers(other_client_id),
        timeout=5,
    )
    assert other_resp.status_code == 403

    officer_resp = requests.get(
        f"{base_url}/api/portal/applications/{app_id}/enhanced-requirements",
        headers=_headers("admin"),
        timeout=5,
    )
    assert officer_resp.status_code == 403

    backoffice_resp = requests.get(
        f"{base_url}/api/applications/{app_id}/enhanced-requirements",
        headers=_client_headers(client_id),
        timeout=5,
    )
    assert backoffice_resp.status_code == 403

    conn = get_db()
    app = conn.execute("SELECT status, decision_notes FROM applications WHERE id=?", (app_id,)).fetchone()
    rmi_count = conn.execute(
        "SELECT COUNT(*) AS c FROM rmi_requests WHERE application_id=?",
        (app_id,),
    ).fetchone()["c"]
    notification_count = conn.execute(
        "SELECT COUNT(*) AS c FROM client_notifications WHERE application_id=?",
        (app_id,),
    ).fetchone()["c"]
    memo_count = conn.execute(
        "SELECT COUNT(*) AS c FROM compliance_memos WHERE application_id=?",
        (app_id,),
    ).fetchone()["c"]
    conn.close()

    assert app["status"] == "submitted"
    assert app["decision_notes"] in (None, "")
    assert rmi_count == 0
    assert notification_count == 0
    assert memo_count == 0


def test_portal_hides_requested_requirements_from_disabled_source_rules(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    client_id = "client001"
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?,?,?,?)",
        (client_id, "client001@example.com", "hash", "Client One"),
    )
    app_id = _insert_application(conn, risk_level="HIGH")
    conn.execute("UPDATE applications SET client_id=? WHERE id=?", (client_id, app_id))
    conn.commit()
    _generate(conn, app_id)
    requested_req = _requirement_id_by_key(conn, app_id, "company_bank_reference")
    uploaded_req = _requirement_id_by_key(conn, app_id, "company_bank_statements_6m")
    conn.execute(
        """
        UPDATE application_enhanced_requirements
        SET status='requested', requested_at=datetime('now'), requested_by='co001'
        WHERE id=?
        """,
        (requested_req,),
    )
    conn.execute(
        """
        UPDATE application_enhanced_requirements
        SET status='uploaded', uploaded_at=datetime('now'), requested_at=datetime('now'), requested_by='co001'
        WHERE id=?
        """,
        (uploaded_req,),
    )
    conn.execute(
        """
        UPDATE enhanced_requirement_rules
        SET active=0
        WHERE id IN (
            SELECT source_rule_id FROM application_enhanced_requirements
            WHERE id IN (?,?)
        )
        """,
        (requested_req, uploaded_req),
    )
    conn.commit()
    conn.close()

    resp = requests.get(
        f"{base_url}/api/portal/applications/{app_id}/enhanced-requirements",
        headers=_client_headers(client_id),
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    returned_ids = {item["id"] for item in resp.json()["requirements"]}
    assert requested_req not in returned_ids
    assert uploaded_req in returned_ids


def test_generation_skips_bank_requirements_without_existing_bank_account(enhanced_app_db):
    conn = enhanced_app_db
    app_id = _insert_application(
        conn,
        risk_level="HIGH",
        prescreening={"existing_bank_account": "No"},
    )

    result = _generate(conn, app_id)
    rows = conn.execute(
        "SELECT requirement_key FROM application_enhanced_requirements WHERE application_id=?",
        (app_id,),
    ).fetchall()
    keys = {row["requirement_key"] for row in rows}

    assert "company_bank_reference" not in keys
    assert "company_bank_statements_6m" not in keys
    assert "company_sof_evidence" in keys
    assert result["skipped_count"] >= 2


def test_generation_prefills_jurisdiction_rationale_from_prescreening(enhanced_app_db):
    conn = enhanced_app_db
    app_id = _insert_application(
        conn,
        risk_level="HIGH",
        country="Iran",
        prescreening={
            "country_of_incorporation": "Iran",
            "jurisdiction_exposure_rationale": "Legacy shareholders remain in jurisdiction while operations are outside Iran.",
            "existing_bank_account": "Yes",
        },
    )

    _generate(conn, app_id)
    req = conn.execute(
        """
        SELECT * FROM application_enhanced_requirements
        WHERE application_id=? AND requirement_key='jurisdiction_exposure_rationale'
        """,
        (app_id,),
    ).fetchone()

    assert req is not None
    assert req["status"] == "uploaded"
    assert req["client_response_text"] == "Legacy shareholders remain in jurisdiction while operations are outside Iran."
    assert req["client_response_at"]
    assert req["uploaded_at"]


def test_portal_document_upload_fulfils_requested_enhanced_requirement(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db
    import server as server_module

    original_has_s3 = server_module.HAS_S3
    server_module.HAS_S3 = False
    try:
        client_id = "client001"
        conn = get_db()
        conn.execute(
            "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?,?,?,?)",
            (client_id, "client001@example.com", "hash", "Client One"),
        )
        app_id = _insert_application(conn, risk_level="HIGH")
        conn.execute("UPDATE applications SET client_id=? WHERE id=?", (client_id, app_id))
        conn.execute(
            "INSERT INTO directors (application_id, full_name, is_pep) VALUES (?,?,?)",
            (app_id, "Declared Person", "Yes"),
        )
        conn.commit()
        _generate(conn, app_id)
        req_id = _requirement_id_by_key(conn, app_id, "company_bank_reference")
        conn.execute(
            """
            UPDATE application_enhanced_requirements
            SET status='requested', requested_at=datetime('now'), requested_by='co001'
            WHERE id=?
            """,
            (req_id,),
        )
        conn.commit()
        conn.close()

        resp = requests.post(
            f"{base_url}/api/portal/applications/{app_id}/enhanced-requirements/{req_id}/upload",
            headers=_client_headers(client_id),
            files={"file": ("evidence.pdf", b"%PDF-1.4\n% enhanced evidence\n", "application/pdf")},
            timeout=5,
        )
    finally:
        server_module.HAS_S3 = original_has_s3
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "submitted"
    assert body["requirement"]["status"] == "submitted"
    assert body["requirement"]["id"] == req_id
    assert body["document"]["doc_type"] == "enhanced_requirement"
    forbidden_fields = {"trigger_key", "trigger_reason", "trigger_context", "review_notes", "waiver_reason", "requested_by", "audit"}
    assert not forbidden_fields.intersection(body["requirement"])

    conn = get_db()
    req = conn.execute("SELECT * FROM application_enhanced_requirements WHERE id=?", (req_id,)).fetchone()
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (req["linked_document_id"],)).fetchone()
    app = conn.execute("SELECT status, decision_notes FROM applications WHERE id=?", (app_id,)).fetchone()
    rmi_count = conn.execute("SELECT COUNT(*) AS c FROM rmi_requests WHERE application_id=?", (app_id,)).fetchone()["c"]
    rmi_item_count = conn.execute(
        "SELECT COUNT(*) AS c FROM rmi_request_items WHERE request_id IN (SELECT id FROM rmi_requests WHERE application_id=?)",
        (app_id,),
    ).fetchone()["c"]
    notification_count = conn.execute(
        "SELECT COUNT(*) AS c FROM client_notifications WHERE application_id=?",
        (app_id,),
    ).fetchone()["c"]
    memo_count = conn.execute(
        "SELECT COUNT(*) AS c FROM compliance_memos WHERE application_id=?",
        (app_id,),
    ).fetchone()["c"]
    audit = conn.execute(
        """
        SELECT detail, before_state, after_state
        FROM audit_log
        WHERE action='application_enhanced_requirement.client_uploaded'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert req["status"] == "uploaded"
    assert req["uploaded_at"]
    assert doc["doc_type"] == "enhanced_requirement"
    assert doc["verification_status"] == "pending"
    assert doc["review_status"] == "pending"
    metadata = json.loads(doc["verification_results"])
    assert metadata["enhanced_requirement_id"] == str(req_id)
    assert app["status"] == "submitted"
    assert app["decision_notes"] in (None, "")
    assert rmi_count == 0
    assert rmi_item_count == 0
    assert notification_count == 0
    assert memo_count == 0
    assert audit is not None
    detail = json.loads(audit["detail"])
    before = json.loads(audit["before_state"])
    after = json.loads(audit["after_state"])
    assert detail["old_status"] == "requested"
    assert detail["new_status"] == "uploaded"
    assert detail["document_id"] == req["linked_document_id"]
    assert detail["client_id"] == client_id
    assert before["status"] == "requested"
    assert after["status"] == "uploaded"


def test_portal_text_response_fulfils_and_resubmits_requested_enhanced_requirement(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    client_id = "client001"
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?,?,?,?)",
        (client_id, "client001@example.com", "hash", "Client One"),
    )
    app_id = _insert_application(conn, risk_level="HIGH")
    conn.execute("UPDATE applications SET client_id=? WHERE id=?", (client_id, app_id))
    conn.execute(
        "INSERT INTO directors (application_id, full_name, is_pep) VALUES (?,?,?)",
        (app_id, "Declared Person", "Yes"),
    )
    conn.commit()
    _generate(conn, app_id)
    req_id = _requirement_id_by_key(conn, app_id, "enhanced_business_activity_explanation")
    conn.execute(
        """
        UPDATE application_enhanced_requirements
        SET status='requested', requested_at=datetime('now'), requested_by='co001'
        WHERE id=?
        """,
        (req_id,),
    )
    conn.commit()
    conn.close()

    first = requests.post(
        f"{base_url}/api/portal/applications/{app_id}/enhanced-requirements/{req_id}/response",
        headers=_client_headers(client_id),
        json={"response_text": "We provide payment workflow software for SME merchants."},
        timeout=5,
    )
    assert first.status_code == 200, first.text
    assert first.json()["requirement"]["status"] == "submitted"

    conn = get_db()
    req = conn.execute("SELECT * FROM application_enhanced_requirements WHERE id=?", (req_id,)).fetchone()
    assert req["status"] == "uploaded"
    assert req["client_response_text"] == "We provide payment workflow software for SME merchants."
    assert req["client_response_at"]
    assert req["client_response_by"] == client_id
    assert req["uploaded_at"]
    conn.execute("UPDATE application_enhanced_requirements SET status='rejected' WHERE id=?", (req_id,))
    conn.commit()
    conn.close()

    second = requests.post(
        f"{base_url}/api/portal/applications/{app_id}/enhanced-requirements/{req_id}/response",
        headers=_client_headers(client_id),
        json={"response_text": "Updated response with the requested operating details."},
        timeout=5,
    )
    assert second.status_code == 200, second.text

    conn = get_db()
    req_after = conn.execute("SELECT * FROM application_enhanced_requirements WHERE id=?", (req_id,)).fetchone()
    rmi_count = conn.execute("SELECT COUNT(*) AS c FROM rmi_requests WHERE application_id=?", (app_id,)).fetchone()["c"]
    notification_count = conn.execute(
        "SELECT COUNT(*) AS c FROM client_notifications WHERE application_id=?",
        (app_id,),
    ).fetchone()["c"]
    memo_count = conn.execute(
        "SELECT COUNT(*) AS c FROM compliance_memos WHERE application_id=?",
        (app_id,),
    ).fetchone()["c"]
    audit = conn.execute(
        """
        SELECT before_state, after_state
        FROM audit_log
        WHERE action='application_enhanced_requirement.client_response_submitted'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert req_after["status"] == "uploaded"
    assert req_after["client_response_text"] == "Updated response with the requested operating details."
    assert rmi_count == 0
    assert notification_count == 0
    assert memo_count == 0
    before = json.loads(audit["before_state"])
    after = json.loads(audit["after_state"])
    assert before["status"] == "rejected"
    assert after["status"] == "uploaded"
    assert "client_response_text" not in before
    assert "client_response_text" not in after
    assert before["client_response_text_present"] is True
    assert after["client_response_text_present"] is True


def test_portal_fulfilment_rejects_unauthorized_ineligible_and_wrong_type(enhanced_app_api_server):
    base_url, db_path = enhanced_app_api_server
    _sync_db_path(db_path)
    from db import get_db

    client_id = "client001"
    other_client_id = "client002"
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?,?,?,?)",
        (client_id, "client001@example.com", "hash", "Client One"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name) VALUES (?,?,?,?)",
        (other_client_id, "client002@example.com", "hash", "Client Two"),
    )
    app_id = _insert_application(conn, risk_level="HIGH")
    conn.execute("UPDATE applications SET client_id=? WHERE id=?", (client_id, app_id))
    conn.execute(
        "INSERT INTO directors (application_id, full_name, is_pep) VALUES (?,?,?)",
        (app_id, "Declared Person", "Yes"),
    )
    conn.commit()
    _generate(conn, app_id)
    generated_req = _requirement_id_by_key(conn, app_id, "company_bank_reference")
    document_req = _requirement_id_by_key(conn, app_id, "company_bank_statements_6m")
    explanation_req = _requirement_id_by_key(conn, app_id, "enhanced_business_activity_explanation")
    accepted_req = _requirement_id_by_key(conn, app_id, "company_sof_evidence")
    waived_req = _requirement_id_by_key(conn, app_id, "material_ubo_sow_evidence")
    cancelled_req = _requirement_id_by_key(conn, app_id, "pep_declaration_details")
    conn.execute(
        """
        UPDATE application_enhanced_requirements
        SET status = CASE id
            WHEN ? THEN 'requested'
            WHEN ? THEN 'requested'
            WHEN ? THEN 'accepted'
            WHEN ? THEN 'waived'
            WHEN ? THEN 'cancelled'
            ELSE status
        END,
        requested_at = CASE WHEN id IN (?,?) THEN datetime('now') ELSE requested_at END,
        requested_by = CASE WHEN id IN (?,?) THEN 'co001' ELSE requested_by END
        WHERE application_id=?
        """,
        (
            document_req,
            explanation_req,
            accepted_req,
            waived_req,
            cancelled_req,
            document_req,
            explanation_req,
            document_req,
            explanation_req,
            app_id,
        ),
    )
    pep_app_id = _insert_application(conn, risk_level="LOW")
    conn.execute("UPDATE applications SET client_id=? WHERE id=?", (client_id, pep_app_id))
    conn.execute(
        "INSERT INTO directors (application_id, full_name, is_pep) VALUES (?,?,?)",
        (pep_app_id, "PEP Director", "Yes"),
    )
    conn.commit()
    _generate(conn, pep_app_id)
    backoffice_req = _requirement_id_by_key(conn, pep_app_id, "mandatory_senior_review")
    conn.execute(
        "UPDATE application_enhanced_requirements SET status='requested', requested_at=datetime('now'), requested_by='co001' WHERE id=?",
        (backoffice_req,),
    )
    conn.commit()
    conn.close()

    pdf_file = {"file": ("evidence.pdf", b"%PDF-1.4\n% evidence\n", "application/pdf")}
    generated_resp = requests.post(
        f"{base_url}/api/portal/applications/{app_id}/enhanced-requirements/{generated_req}/upload",
        headers=_client_headers(client_id),
        files=pdf_file,
        timeout=5,
    )
    assert generated_resp.status_code == 404

    wrong_client = requests.post(
        f"{base_url}/api/portal/applications/{app_id}/enhanced-requirements/{document_req}/upload",
        headers=_client_headers(other_client_id),
        files={"file": ("evidence.pdf", b"%PDF-1.4\n% evidence\n", "application/pdf")},
        timeout=5,
    )
    assert wrong_client.status_code == 403

    officer_resp = requests.post(
        f"{base_url}/api/portal/applications/{app_id}/enhanced-requirements/{document_req}/upload",
        headers=_headers("admin"),
        files={"file": ("evidence.pdf", b"%PDF-1.4\n% evidence\n", "application/pdf")},
        timeout=5,
    )
    assert officer_resp.status_code == 403

    upload_to_text = requests.post(
        f"{base_url}/api/portal/applications/{app_id}/enhanced-requirements/{explanation_req}/upload",
        headers=_client_headers(client_id),
        files={"file": ("evidence.pdf", b"%PDF-1.4\n% evidence\n", "application/pdf")},
        timeout=5,
    )
    assert upload_to_text.status_code == 400

    response_to_doc = requests.post(
        f"{base_url}/api/portal/applications/{app_id}/enhanced-requirements/{document_req}/response",
        headers=_client_headers(client_id),
        json={"response_text": "Text for a document slot"},
        timeout=5,
    )
    assert response_to_doc.status_code == 400

    too_long = requests.post(
        f"{base_url}/api/portal/applications/{app_id}/enhanced-requirements/{explanation_req}/response",
        headers=_client_headers(client_id),
        json={"response_text": "x" * 10001},
        timeout=5,
    )
    assert too_long.status_code == 400

    unsafe_file = requests.post(
        f"{base_url}/api/portal/applications/{app_id}/enhanced-requirements/{document_req}/upload",
        headers=_client_headers(client_id),
        files={"file": ("malware.exe", b"MZ...", "application/octet-stream")},
        timeout=5,
    )
    assert unsafe_file.status_code == 400

    for req_id in (accepted_req, waived_req, cancelled_req):
        resp = requests.post(
            f"{base_url}/api/portal/applications/{app_id}/enhanced-requirements/{req_id}/upload",
            headers=_client_headers(client_id),
            files={"file": ("evidence.pdf", b"%PDF-1.4\n% evidence\n", "application/pdf")},
            timeout=5,
        )
        assert resp.status_code == 404

    backoffice_only = requests.post(
        f"{base_url}/api/portal/applications/{pep_app_id}/enhanced-requirements/{backoffice_req}/response",
        headers=_client_headers(client_id),
        json={"response_text": "Attempt to fulfil internal item"},
        timeout=5,
    )
    assert backoffice_only.status_code == 404

    conn = get_db()
    doc_count = conn.execute(
        "SELECT COUNT(*) AS c FROM documents WHERE application_id=? AND doc_type='enhanced_requirement'",
        (app_id,),
    ).fetchone()["c"]
    rmi_count = conn.execute("SELECT COUNT(*) AS c FROM rmi_requests WHERE application_id=?", (app_id,)).fetchone()["c"]
    notification_count = conn.execute(
        "SELECT COUNT(*) AS c FROM client_notifications WHERE application_id=?",
        (app_id,),
    ).fetchone()["c"]
    conn.close()
    assert doc_count == 0
    assert rmi_count == 0
    assert notification_count == 0
