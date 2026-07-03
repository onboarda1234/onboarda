import json
import os
import sys
import threading
import time

import pytest
import requests
import tornado.httpserver
import tornado.ioloop
import tornado.netutil


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _profile_with_raw(company_number="12345678", *, name="Registry Verified Ltd", secret="server-only"):
    endpoint = f"/company/{company_number}"
    response_hash = f"hash-{company_number}"
    profile = {
        "provider": "companies_house",
        "jurisdiction": "GB",
        "company_name": name,
        "company_number": company_number,
        "company_status": "active",
        "entity_type": "ltd",
        "incorporation_date": "2020-02-03",
        "registered_address": {
            "address_line_1": "1 Registry Road",
            "locality": "London",
            "country": "United Kingdom",
            "full_address": "1 Registry Road, London, United Kingdom",
        },
        "sic_codes": ["62012"],
        "officers": [],
        "beneficial_owners": [],
        "source_metadata": {
            "fetched_at": "2026-06-22T00:00:00+00:00",
            "endpoint": endpoint,
            "response_hash": response_hash,
            "simulation": False,
        },
    }
    return {
        "provider": "companies_house",
        "raw_response": {
            "_endpoint": endpoint,
            "company_name": name,
            "company_number": company_number,
            "company_status": "active",
            "type": "ltd",
            "date_of_creation": "2020-02-03",
            "registered_office_address": {"address_line_1": "1 Registry Road", "locality": "London"},
            "sic_codes": ["62012"],
            "raw_secret_marker": secret,
        },
        "normalized": profile,
        "response_hash": response_hash,
        "fetched_at": "2026-06-22T00:00:00+00:00",
        "source_endpoint": endpoint,
        "simulation_used": False,
    }


@pytest.fixture
def intake_api(monkeypatch, tmp_path):
    db_path = str(tmp_path / "company_intake_write_path.db")

    import db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setenv("DB_PATH", db_path)

    from db import get_db, init_db

    init_db()
    conn = get_db()
    conn.execute(
        "INSERT INTO clients (id, email, password_hash, company_name, status) VALUES (?,?,?,?,?)",
        ("client-intake-1", "client1@example.test", "hash", "Client One", "active"),
    )
    conn.execute(
        "INSERT INTO clients (id, email, password_hash, company_name, status) VALUES (?,?,?,?,?)",
        ("client-intake-2", "client2@example.test", "hash", "Client Two", "active"),
    )
    conn.commit()
    conn.close()

    import server
    from server import make_app

    calls = []

    def fake_profile(company_number):
        calls.append(company_number)
        return _profile_with_raw(company_number)

    monkeypatch.setattr(server, "get_companies_house_profile_with_raw", fake_profile)

    app = make_app()
    server_ref = {}
    port_ref = {}
    started = threading.Event()

    def run_server():
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        io_loop = tornado.ioloop.IOLoop.current()
        sockets = tornado.netutil.bind_sockets(0, address="127.0.0.1")
        port_ref["port"] = sockets[0].getsockname()[1]
        srv = tornado.httpserver.HTTPServer(app)
        srv.add_sockets(sockets)
        server_ref["server"] = srv
        server_ref["loop"] = io_loop
        started.set()
        io_loop.start()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    assert started.wait(timeout=3), "Test server failed to start within timeout"
    time.sleep(0.2)

    from auth import create_token

    yield {
        "base_url": f"http://127.0.0.1:{port_ref['port']}",
        "client1": create_token("client-intake-1", "client", "Client One", "client"),
        "client2": create_token("client-intake-2", "client", "Client Two", "client"),
        "calls": calls,
        "db_path": db_path,
    }

    io_loop = server_ref.get("loop")
    srv = server_ref.get("server")
    if io_loop and srv:
        io_loop.add_callback(srv.stop)
        io_loop.add_callback(io_loop.stop)
    thread.join(timeout=2)


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _start_intake(ctx, *, token=None, company_number="12345678", expected_status=201):
    resp = requests.post(
        f"{ctx['base_url']}/api/company-intake/start",
        headers=_headers(token or ctx["client1"]),
        json={
            "country_of_incorporation": "France",
            "provider": "companies_house",
            "company_number": company_number,
            "selected_registry_result": {
                "jurisdiction": "FR",
                "company_name": "Frontend Spoof Ltd",
                "company_number": company_number,
            },
        },
        timeout=5,
    )
    assert resp.status_code == expected_status
    return resp.json()


def _db_rows(query, params=()):
    from db import get_db

    conn = get_db()
    try:
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def _db_one(query, params=()):
    from db import get_db

    conn = get_db()
    try:
        return conn.execute(query, params).fetchone()
    finally:
        conn.close()


def _hydrated_parties(application_id):
    from db import get_db
    from party_utils import get_application_parties

    conn = get_db()
    try:
        return get_application_parties(conn, application_id)
    finally:
        conn.close()


def _save_resume(ctx, form_data, *, application_id=None, token=None, expected_status=200):
    payload = {"form_data": form_data, "last_step": 1}
    if application_id:
        payload["application_id"] = application_id
    resp = requests.post(
        f"{ctx['base_url']}/api/save-resume",
        headers=_headers(token or ctx["client1"]),
        json=payload,
        timeout=5,
    )
    assert resp.status_code == expected_status, resp.text
    return resp.json()


def _application_detail(ctx, app_id, *, token=None, expected_status=200):
    resp = requests.get(
        f"{ctx['base_url']}/api/applications/{app_id}",
        headers=_headers(token or ctx["client1"]),
        timeout=5,
    )
    assert resp.status_code == expected_status, resp.text
    return resp.json()


def _store_parties(application_id, *, directors=None, ubos=None, intermediaries=None):
    from db import get_db
    from server import store_application_parties

    conn = get_db()
    try:
        store_application_parties(
            conn,
            application_id,
            directors=directors,
            ubos=ubos,
            intermediaries=intermediaries,
        )
        conn.commit()
    finally:
        conn.close()


def test_db_ensure_creates_registry_tables_and_thin_session(monkeypatch, tmp_path):
    import db as db_module
    from db import get_db, init_db

    monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "schema.db"))
    init_db()
    conn = get_db()
    try:
        lookup_cols = {row["name"] for row in conn.execute("PRAGMA table_info(company_registry_lookups)").fetchall()}
        session_cols = {row["name"] for row in conn.execute("PRAGMA table_info(company_intake_sessions)").fetchall()}
        director_cols = {row["name"] for row in conn.execute("PRAGMA table_info(directors)").fetchall()}
        ubo_cols = {row["name"] for row in conn.execute("PRAGMA table_info(ubos)").fetchall()}
        intermediary_cols = {row["name"] for row in conn.execute("PRAGMA table_info(intermediaries)").fetchall()}
    finally:
        conn.close()

    assert {
        "raw_response_json",
        "normalized_json",
        "response_hash",
        "simulation_used",
        "application_id",
    }.issubset(lookup_cols)
    assert {
        "application_id",
        "client_user_id",
        "registry_lookup_id",
        "stage",
        "completion_score",
        "missing_answers_json",
        "document_checklist_json",
    }.issubset(session_cols)
    assert "confirmed_officers_json" not in session_cols
    assert "confirmed_pscs_json" not in session_cols
    assert {"officer_entity_type", "requires_individual_kyc", "registry_lookup_id", "response_hash"}.issubset(director_cols)
    assert {"country_of_residence", "residential_address", "date_of_appointment"}.issubset(director_cols)
    assert {"psc_state", "registry_statement_type", "psc_status_reason", "registry_lookup_id", "response_hash"}.issubset(ubo_cols)
    assert {"country_of_residence", "residential_address"}.issubset(ubo_cols)
    assert {
        "registration_number",
        "registered_address",
        "ownership_pct",
        "owned_or_controlled_by",
        "source",
        "registry_lookup_id",
        "response_hash",
    }.issubset(intermediary_cols)


def test_start_intake_refetches_profile_creates_draft_session_and_persists_evidence(intake_api):
    body = _start_intake(intake_api)

    assert intake_api["calls"] == ["12345678"]
    assert body["success"] is True
    assert body["session_reused"] is False
    assert body["application"]["created"] is True
    assert body["company"]["company_name"] == "Registry Verified Ltd"
    assert "Frontend Spoof" not in json.dumps(body)
    assert "raw_response_json" not in json.dumps(body)
    assert "server-only" not in json.dumps(body)

    app = _db_one("SELECT * FROM applications WHERE id = ?", (body["application"]["id"],))
    prescreening = json.loads(app["prescreening_data"])
    assert app["status"] == "draft"
    assert app["company_name"] == "Registry Verified Ltd"
    assert prescreening["registry_provenance"]["provider"] == "companies_house"
    assert prescreening["registry_provenance"]["jurisdiction"] == "GB"
    assert prescreening["registry_provenance"]["company_number"] == "12345678"
    assert prescreening["registry_provenance"]["response_hash"] == "hash-12345678"

    lookup = _db_one("SELECT * FROM company_registry_lookups WHERE id = ?", (body["registry_lookup"]["id"],))
    assert lookup["provider"] == "companies_house"
    assert lookup["company_number"] == "12345678"
    assert lookup["response_hash"] == "hash-12345678"
    assert "server-only" in lookup["raw_response_json"]
    assert lookup["application_id"] == body["application"]["id"]

    session = _db_one("SELECT * FROM company_intake_sessions WHERE id = ?", (body["session"]["id"],))
    assert session["application_id"] == body["application"]["id"]
    assert session["registry_lookup_id"] == lookup["id"]
    assert session["client_user_id"] == "client-intake-1"

    audits = _db_rows("SELECT action, detail FROM audit_log WHERE action = 'Company Intake Start'")
    assert len(audits) == 1
    assert "registry_lookup_id" in audits[0]["detail"]


def test_start_intake_reuses_existing_draft_for_same_client_and_company(intake_api):
    first = _start_intake(intake_api)
    second = _start_intake(intake_api, expected_status=200)

    assert second["application"]["id"] == first["application"]["id"]
    assert second["application"]["created"] is False
    assert second["session"]["id"] == first["session"]["id"]
    assert second["session_reused"] is True
    assert intake_api["calls"] == ["12345678"]

    apps = _db_rows(
        "SELECT id FROM applications WHERE client_id = ? AND brn = ?",
        ("client-intake-1", "12345678"),
    )
    sessions = _db_rows(
        "SELECT id, application_id FROM company_intake_sessions WHERE client_user_id = ?",
        ("client-intake-1",),
    )
    lookups = _db_rows(
        "SELECT id, application_id, raw_response_json FROM company_registry_lookups WHERE company_number = ?",
        ("12345678",),
    )
    active_sessions = _db_rows(
        """
        SELECT id FROM company_intake_sessions
        WHERE client_user_id = ?
          AND application_id = ?
          AND provider = ?
          AND company_number = ?
          AND stage IN ('profile_verified', 'profile_confirmed', 'officers_confirmed', 'pscs_confirmed')
        """,
        ("client-intake-1", first["application"]["id"], "companies_house", "12345678"),
    )
    assert len(apps) == 1
    assert len(sessions) == 1
    assert {row["application_id"] for row in sessions} == {first["application"]["id"]}
    assert len(active_sessions) == 1
    assert active_sessions[0]["id"] == first["session"]["id"]
    assert len(lookups) == 1
    assert all(row["application_id"] == first["application"]["id"] for row in lookups)


def test_confirm_profile_writes_company_fields_and_tracks_override(intake_api):
    started = _start_intake(intake_api)
    session_id = started["session"]["id"]
    resp = requests.post(
        f"{intake_api['base_url']}/api/company-intake/confirm-profile",
        headers=_headers(intake_api["client1"]),
        json={
            "session_id": session_id,
            "profile": {
                "provider": "companies_house",
                "jurisdiction": "GB",
                "company_name": "Untrusted Frontend Profile Ltd",
                "company_number": "12345678",
            },
            "overrides": {
                "registered_entity_name": {
                    "value": "Client Edited Ltd",
                    "override_reason": "Client corrected display name",
                }
            },
        },
        timeout=5,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["override_count"] == 1

    app = _db_one("SELECT * FROM applications WHERE id = ?", (started["application"]["id"],))
    prescreening = json.loads(app["prescreening_data"])
    assert app["company_name"] == "Client Edited Ltd"
    assert app["brn"] == "12345678"
    assert app["country"] == "United Kingdom"
    assert prescreening["registered_entity_name"] == "Client Edited Ltd"
    assert prescreening["registry_sourced_values"]["registered_entity_name"] == "Registry Verified Ltd"
    assert "Untrusted Frontend Profile" not in json.dumps(prescreening)
    override = prescreening["registry_field_overrides"][0]
    assert override["field_name"] == "registered_entity_name"
    assert override["registry_value"] == "Registry Verified Ltd"
    assert override["user_value"] == "Client Edited Ltd"
    assert override["overridden_by"] == "client-intake-1"


def test_confirm_officers_imports_directors_with_provenance_and_dedupes(intake_api):
    started = _start_intake(intake_api)
    session_id = started["session"]["id"]
    officers = [
        {
            "provider": "companies_house",
            "jurisdiction": "GB",
            "name": "Active Director",
            "officer_role": "director",
            "officer_entity_type": "individual",
            "requires_individual_kyc": True,
            "requires_corporate_structure_review": False,
            "status": "active",
            "appointed_on": "2020-01-01",
            "date_of_birth": {"month": 9, "year": 1949},
            "country_of_residence": "United Kingdom",
            "source_metadata": {"endpoint": "/company/12345678/officers", "response_hash": "officer-hash"},
        },
        {
            "provider": "companies_house",
            "jurisdiction": "GB",
            "name": "Corporate Director Ltd",
            "officer_role": "corporate-director",
            "officer_entity_type": "corporate",
            "requires_individual_kyc": False,
            "requires_corporate_structure_review": True,
            "status": "active",
            "source_metadata": {"endpoint": "/company/12345678/officers", "response_hash": "officer-hash"},
        },
    ]
    for _ in range(2):
        resp = requests.post(
            f"{intake_api['base_url']}/api/company-intake/confirm-officers",
            headers=_headers(intake_api["client1"]),
            json={"session_id": session_id, "officers": officers},
            timeout=5,
        )
        assert resp.status_code == 200
        body = resp.json()

    director_payload = json.dumps(body["directors"], sort_keys=True, default=str)
    assert "Active Director" in director_payload
    assert "officer_role" in director_payload
    assert "registry_lookup_id" not in director_payload
    assert "response_hash" not in director_payload
    assert "source_metadata_json" not in director_payload
    assert "imported_by" not in director_payload

    directors = _db_rows("SELECT * FROM directors WHERE application_id = ? ORDER BY full_name", (started["application"]["id"],))
    assert len(directors) == 2
    corporate = next(row for row in directors if row["full_name"] == "Corporate Director Ltd")
    individual = next(row for row in directors if row["full_name"] == "Active Director")
    individual_response = next(row for row in body["directors"] if row["full_name"] == "Active Director")
    assert individual_response["country_of_residence"] == "United Kingdom"
    assert individual["officer_entity_type"] == "individual"
    assert individual["date_of_appointment"] == "2020-01-01"
    assert individual["date_of_birth"] in ("", None)
    assert bool(individual["requires_individual_kyc"]) is True
    hydrated_directors, _hydrated_ubos, _hydrated_intermediaries = _hydrated_parties(started["application"]["id"])
    hydrated_individual = next(row for row in hydrated_directors if row["full_name"] == "Active Director")
    assert hydrated_individual["country_of_residence"] == "United Kingdom"
    assert corporate["officer_entity_type"] == "corporate"
    assert bool(corporate["requires_individual_kyc"]) is False
    assert bool(corporate["requires_corporate_structure_review"]) is True
    assert corporate["source"] == "companies_house"
    assert corporate["registry_lookup_id"]
    assert corporate["response_hash"] == "officer-hash"


def test_confirm_officers_imports_llp_members_with_legal_role_and_review_flags(intake_api):
    started = _start_intake(intake_api, company_number="OC381818")
    session_id = started["session"]["id"]
    officers = [
        {
            "provider": "companies_house",
            "jurisdiction": "GB",
            "name": "MARGOLIS, Stephen Howard",
            "officer_role": "llp-designated-member",
            "officer_entity_type": "individual",
            "candidate_type": "llp_member_candidate",
            "is_candidate_director": False,
            "is_candidate_llp_member": True,
            "country_of_residence": "United Kingdom",
            "requires_individual_kyc": True,
            "requires_corporate_structure_review": False,
            "status": "active",
            "source_metadata": {"endpoint": "/company/OC381818/officers", "response_hash": "llp-officer-hash"},
        },
        {
            "provider": "companies_house",
            "jurisdiction": "GB",
            "name": "TAURUS (DM) LIMITED",
            "officer_role": "corporate-llp-designated-member",
            "officer_entity_type": "corporate",
            "candidate_type": "corporate_structure_review",
            "is_candidate_director": False,
            "is_candidate_llp_member": True,
            "requires_individual_kyc": False,
            "requires_corporate_structure_review": True,
            "status": "active",
            "source_metadata": {"endpoint": "/company/OC381818/officers", "response_hash": "llp-officer-hash"},
        },
        {
            "provider": "companies_house",
            "jurisdiction": "GB",
            "name": "Resigned LLP Member",
            "officer_role": "llp-designated-member",
            "candidate_type": "llp_member_candidate",
            "resigned_on": "2020-01-01",
        },
        {
            "provider": "companies_house",
            "jurisdiction": "GB",
            "name": "Secretary",
            "officer_role": "secretary",
        },
    ]

    resp = requests.post(
        f"{intake_api['base_url']}/api/company-intake/confirm-officers",
        headers=_headers(intake_api["client1"]),
        json={"session_id": session_id, "officers": officers},
        timeout=5,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["imported_count"] == 2
    assert body["skipped_count"] == 2
    directors = _db_rows("SELECT * FROM directors WHERE application_id = ? ORDER BY full_name", (started["application"]["id"],))
    assert [row["full_name"] for row in directors] == ["MARGOLIS, Stephen Howard", "TAURUS (DM) LIMITED"]
    individual = directors[0]
    corporate = directors[1]
    individual_response = next(row for row in body["directors"] if row["full_name"] == "MARGOLIS, Stephen Howard")
    assert individual_response["country_of_residence"] == "United Kingdom"
    assert individual["officer_role"] == "llp-designated-member"
    assert individual["officer_entity_type"] == "individual"
    assert bool(individual["requires_individual_kyc"]) is True
    hydrated_directors, _hydrated_ubos, _hydrated_intermediaries = _hydrated_parties(started["application"]["id"])
    hydrated_individual = next(row for row in hydrated_directors if row["full_name"] == "MARGOLIS, Stephen Howard")
    assert hydrated_individual["country_of_residence"] == "United Kingdom"
    assert corporate["officer_role"] == "corporate-llp-designated-member"
    assert corporate["officer_entity_type"] == "corporate"
    assert bool(corporate["requires_individual_kyc"]) is False
    assert bool(corporate["requires_corporate_structure_review"]) is True
    assert corporate["response_hash"] == "llp-officer-hash"


def test_confirm_pscs_imports_found_candidates_and_dedupes(intake_api):
    started = _start_intake(intake_api)
    session_id = started["session"]["id"]
    psc_result = {
        "provider": "companies_house",
        "jurisdiction": "GB",
        "company_number": "12345678",
        "psc_state": "psc_found",
        "registry_statement_type": "active_individual_psc",
        "psc_status_reason": "One active PSC returned.",
        "beneficial_owners": [
            {
                "provider": "companies_house",
                "name": "Beneficial Owner",
                "kind": "individual",
                "nationality": "British",
                "country_of_residence": "United Kingdom",
                "date_of_birth": "1980-05-15",
                "natures_of_control": ["ownership-of-shares-75-to-100-percent"],
                "is_candidate_beneficial_owner": True,
                "candidate_type": "beneficial_owner_candidate",
            }
        ],
        "source_metadata": {"endpoint": "/company/12345678/persons-with-significant-control", "response_hash": "psc-hash"},
    }
    for _ in range(2):
        resp = requests.post(
            f"{intake_api['base_url']}/api/company-intake/confirm-pscs",
            headers=_headers(intake_api["client1"]),
            json={"session_id": session_id, "pscs": psc_result},
            timeout=5,
        )
        assert resp.status_code == 200
        body = resp.json()

    ubos = _db_rows("SELECT * FROM ubos WHERE application_id = ?", (started["application"]["id"],))
    assert len(ubos) == 1
    assert ubos[0]["full_name"] == "Beneficial Owner"
    assert ubos[0]["source"] == "companies_house"
    assert ubos[0]["psc_state"] == "psc_found"
    assert ubos[0]["registry_statement_type"] == "active_individual_psc"
    assert bool(ubos[0]["is_candidate_ubo"]) is True
    assert ubos[0]["response_hash"] == "psc-hash"
    assert body["ubos"][0]["country_of_residence"] == "United Kingdom"
    assert body["ubos"][0]["date_of_birth"] == "1980-05-15"
    assert body["intermediaries"] == []
    ubo_payload = json.dumps(body["ubos"], sort_keys=True, default=str)
    assert "registry_lookup_id" not in ubo_payload
    assert "response_hash" not in ubo_payload
    assert "source_metadata_json" not in ubo_payload
    assert "imported_by" not in ubo_payload


def test_save_resume_syncs_imported_director_edit_to_party_table_and_detail(intake_api):
    started = _start_intake(intake_api)
    app_id = started["application"]["id"]
    imported_at = "2026-06-22T12:00:00+00:00"
    metadata = {"provider": "companies_house", "endpoint": "/company/12345678/officers"}
    _store_parties(
        app_id,
        directors=[{
            "person_key": "officer-1",
            "first_name": "James Benjamin",
            "last_name": "HEATH",
            "nationality": "United Kingdom",
            "country_of_residence": "United Kingdom",
            "date_of_appointment": "2020-01-01",
            "is_pep": "No",
            "source": "companies_house",
            "officer_role": "director",
            "officer_entity_type": "individual",
            "requires_individual_kyc": True,
            "requires_corporate_structure_review": False,
            "registry_lookup_id": "lookup-director-1",
            "response_hash": "director-hash",
            "source_metadata_json": metadata,
            "imported_at": imported_at,
            "imported_by": "client-intake-1",
        }],
    )

    body = _save_resume(
        intake_api,
        {
            "prescreening": {"f-reg-name": "Registry Verified Ltd"},
            "directors": [{
                "person_key": "officer-1",
                "first_name": "test",
                "last_name": "HEATH Test",
                "nationality": "United Kingdom",
                "country_of_residence": "United Kingdom",
                "residential_address": "1 Edited Road",
                "date_of_appointment": "2021-02-03",
                "is_pep": "Yes",
            }],
            "ubos": [],
            "intermediaries": [],
        },
        application_id=app_id,
    )

    assert body["status"] == "saved"
    row = _db_one(
        """
        SELECT first_name, last_name, full_name, source, officer_role,
               officer_entity_type, requires_individual_kyc,
               requires_corporate_structure_review, registry_lookup_id,
               response_hash, source_metadata_json, imported_at, imported_by,
               is_pep, date_of_appointment
        FROM directors WHERE application_id = ? AND person_key = ?
        """,
        (app_id, "officer-1"),
    )
    assert row["first_name"] == "test"
    assert row["last_name"] == "HEATH Test"
    assert row["full_name"] == "test HEATH Test"
    assert row["is_pep"] == "Yes"
    assert row["date_of_appointment"] == "2021-02-03"
    assert row["source"] == "companies_house"
    assert row["officer_role"] == "director"
    assert row["officer_entity_type"] == "individual"
    assert bool(row["requires_individual_kyc"]) is True
    assert bool(row["requires_corporate_structure_review"]) is False
    assert row["registry_lookup_id"] == "lookup-director-1"
    assert row["response_hash"] == "director-hash"
    assert json.loads(row["source_metadata_json"]) == metadata
    assert row["imported_at"] == imported_at
    assert row["imported_by"] == "client-intake-1"

    directors, _ubos, _intermediaries = _hydrated_parties(app_id)
    assert directors[0]["residential_address"] == "1 Edited Road"
    detail = _application_detail(intake_api, app_id)
    assert detail["directors"][0]["full_name"] == "test HEATH Test"
    assert detail["prescreening_data"]["directors"][0]["first_name"] == "test"
    assert _db_one("SELECT status FROM applications WHERE id = ?", (app_id,))["status"] == "draft"


def test_save_resume_syncs_imported_llp_member_edit_and_preserves_role(intake_api):
    started = _start_intake(intake_api, company_number="OC381818")
    app_id = started["application"]["id"]
    imported_at = "2026-06-23T12:00:00+00:00"
    _store_parties(
        app_id,
        directors=[{
            "person_key": "llp-member-1",
            "first_name": "Stephen Howard",
            "last_name": "MARGOLIS",
            "nationality": "United Kingdom",
            "country_of_residence": "United Kingdom",
            "is_pep": "No",
            "source": "companies_house",
            "officer_role": "llp-designated-member",
            "officer_entity_type": "individual",
            "requires_individual_kyc": True,
            "requires_corporate_structure_review": False,
            "registry_lookup_id": "lookup-llp-1",
            "response_hash": "llp-hash",
            "source_metadata_json": {"provider": "companies_house", "endpoint": "/company/OC381818/officers"},
            "imported_at": imported_at,
            "imported_by": "client-intake-1",
        }],
    )

    _save_resume(
        intake_api,
        {
            "prescreening": {"f-reg-name": "Registry Verified Ltd"},
            "directors": [{
                "person_key": "llp-member-1",
                "first_name": "Edited Stephen",
                "last_name": "MARGOLIS Test",
                "nationality": "United Kingdom",
                "country_of_residence": "United Kingdom",
                "is_pep": "No",
            }],
        },
        application_id=app_id,
    )

    row = _db_one(
        "SELECT first_name, last_name, full_name, source, officer_role, registry_lookup_id, response_hash, imported_at FROM directors WHERE application_id=? AND person_key=?",
        (app_id, "llp-member-1"),
    )
    assert row["first_name"] == "Edited Stephen"
    assert row["last_name"] == "MARGOLIS Test"
    assert row["full_name"] == "Edited Stephen MARGOLIS Test"
    assert row["source"] == "companies_house"
    assert row["officer_role"] == "llp-designated-member"
    assert row["registry_lookup_id"] == "lookup-llp-1"
    assert row["response_hash"] == "llp-hash"
    assert row["imported_at"] == imported_at
    assert _application_detail(intake_api, app_id)["directors"][0]["full_name"] == "Edited Stephen MARGOLIS Test"


def test_save_resume_syncs_imported_ubo_edit_and_preserves_psc_provenance(intake_api):
    started = _start_intake(intake_api)
    app_id = started["application"]["id"]
    imported_at = "2026-06-24T12:00:00+00:00"
    _store_parties(
        app_id,
        ubos=[{
            "person_key": "psc-1",
            "first_name": "Beneficial",
            "last_name": "Owner",
            "nationality": "British",
            "country_of_residence": "United Kingdom",
            "ownership_pct": 75,
            "is_pep": "No",
            "source": "companies_house",
            "psc_state": "psc_found",
            "psc_kind": "individual-person-with-significant-control",
            "is_candidate_ubo": True,
            "registry_lookup_id": "lookup-psc-1",
            "response_hash": "psc-hash",
            "source_metadata_json": {"provider": "companies_house", "endpoint": "/psc"},
            "imported_at": imported_at,
            "imported_by": "client-intake-1",
        }],
    )

    _save_resume(
        intake_api,
        {
            "prescreening": {"f-reg-name": "Registry Verified Ltd"},
            "ubos": [{
                "person_key": "psc-1",
                "first_name": "Edited",
                "last_name": "Owner Test",
                "nationality": "United Kingdom",
                "country_of_residence": "France",
                "residential_address": "2 Owner Road",
                "ownership_pct": "55.5",
                "is_pep": "Yes",
            }],
        },
        application_id=app_id,
    )

    row = _db_one(
        """
        SELECT first_name, last_name, full_name, ownership_pct, is_pep, source,
               psc_state, psc_kind, is_candidate_ubo, registry_lookup_id,
               response_hash, imported_at
        FROM ubos WHERE application_id=? AND person_key=?
        """,
        (app_id, "psc-1"),
    )
    assert row["first_name"] == "Edited"
    assert row["last_name"] == "Owner Test"
    assert row["full_name"] == "Edited Owner Test"
    assert row["ownership_pct"] == 55.5
    assert row["is_pep"] == "Yes"
    assert row["source"] == "companies_house"
    assert row["psc_state"] == "psc_found"
    assert row["psc_kind"] == "individual-person-with-significant-control"
    assert bool(row["is_candidate_ubo"]) is True
    assert row["registry_lookup_id"] == "lookup-psc-1"
    assert row["response_hash"] == "psc-hash"
    assert row["imported_at"] == imported_at

    _directors, ubos, _intermediaries = _hydrated_parties(app_id)
    assert ubos[0]["country_of_residence"] == "France"
    assert ubos[0]["residential_address"] == "2 Owner Road"
    assert _application_detail(intake_api, app_id)["ubos"][0]["full_name"] == "Edited Owner Test"


def test_save_resume_syncs_imported_intermediary_edit_and_preserves_provenance(intake_api):
    started = _start_intake(intake_api)
    app_id = started["application"]["id"]
    imported_at = "2026-06-25T12:00:00+00:00"
    _store_parties(
        app_id,
        intermediaries=[{
            "person_key": "corp-psc-1",
            "entity_name": "Corporate PSC Ltd",
            "jurisdiction": "United Kingdom",
            "registration_number": "99999999",
            "registered_address": "1 Holdco Street",
            "ownership_pct": 80,
            "owned_or_controlled_by": "Dana Owner",
            "source": "companies_house",
            "psc_state": "corporate_psc",
            "psc_kind": "corporate-entity-person-with-significant-control",
            "is_candidate_intermediary": True,
            "requires_corporate_structure_review": True,
            "registry_lookup_id": "lookup-corp-1",
            "response_hash": "corp-hash",
            "source_metadata_json": {"provider": "companies_house", "endpoint": "/psc"},
            "imported_at": imported_at,
            "imported_by": "client-intake-1",
        }],
    )

    _save_resume(
        intake_api,
        {
            "prescreening": {"f-reg-name": "Registry Verified Ltd"},
            "intermediaries": [{
                "person_key": "corp-psc-1",
                "entity_name": "Edited Corporate PSC Ltd",
                "jurisdiction": "Ireland",
                "registration_number": "IE-123",
                "registered_address": "9 Edited Street",
                "ownership_pct": "62",
                "owned_or_controlled_by": "Edited Owner",
            }],
        },
        application_id=app_id,
    )

    row = _db_one(
        """
        SELECT entity_name, jurisdiction, registration_number, registered_address,
               ownership_pct, source, psc_state, psc_kind,
               is_candidate_intermediary, requires_corporate_structure_review,
               registry_lookup_id, response_hash, imported_at
        FROM intermediaries WHERE application_id=? AND person_key=?
        """,
        (app_id, "corp-psc-1"),
    )
    assert row["entity_name"] == "Edited Corporate PSC Ltd"
    assert row["jurisdiction"] == "Ireland"
    assert row["registration_number"] == "IE-123"
    assert row["registered_address"] == "9 Edited Street"
    assert row["ownership_pct"] == 62
    assert row["source"] == "companies_house"
    assert row["psc_state"] == "corporate_psc"
    assert row["psc_kind"] == "corporate-entity-person-with-significant-control"
    assert bool(row["is_candidate_intermediary"]) is True
    assert bool(row["requires_corporate_structure_review"]) is True
    assert row["registry_lookup_id"] == "lookup-corp-1"
    assert row["response_hash"] == "corp-hash"
    assert row["imported_at"] == imported_at

    _directors, _ubos, intermediaries = _hydrated_parties(app_id)
    assert intermediaries[0]["owned_or_controlled_by"] == "Edited Owner"
    assert _application_detail(intake_api, app_id)["intermediaries"][0]["entity_name"] == "Edited Corporate PSC Ltd"


def test_save_resume_populates_manual_party_rows_and_detail(intake_api):
    body = _save_resume(
        intake_api,
        {
            "prescreening": {"f-reg-name": "Manual Draft Ltd", "f-inc-country": "United Kingdom"},
            "directors": [{
                "person_key": "dir-manual",
                "first_name": "Manual",
                "last_name": "Director",
                "nationality": "United Kingdom",
                "country_of_residence": "United Kingdom",
                "residential_address": "1 Manual Road",
                "date_of_appointment": "2024-01-01",
                "is_pep": "No",
            }],
            "ubos": [{
                "person_key": "ubo-manual",
                "first_name": "Manual",
                "last_name": "Owner",
                "nationality": "United Kingdom",
                "country_of_residence": "United Kingdom",
                "residential_address": "2 Manual Road",
                "ownership_pct": "100",
                "is_pep": "No",
            }],
            "intermediaries": [{
                "person_key": "int-manual",
                "entity_name": "Manual Holdco Ltd",
                "jurisdiction": "BVI",
                "registration_number": "BVI-1",
                "registered_address": "3 Manual Road",
                "ownership_pct": "100",
                "owned_or_controlled_by": "Manual Owner",
            }],
        },
    )
    app_id = body["application_id"]

    app = _db_one("SELECT status FROM applications WHERE id = ?", (app_id,))
    assert app["status"] == "draft"
    directors, ubos, intermediaries = _hydrated_parties(app_id)
    assert directors[0]["full_name"] == "Manual Director"
    assert directors[0]["residential_address"] == "1 Manual Road"
    assert ubos[0]["full_name"] == "Manual Owner"
    assert ubos[0]["ownership_pct"] == 100
    assert intermediaries[0]["entity_name"] == "Manual Holdco Ltd"
    assert intermediaries[0]["owned_or_controlled_by"] == "Manual Owner"

    detail = _application_detail(intake_api, app_id)
    assert detail["directors"][0]["full_name"] == "Manual Director"
    assert detail["ubos"][0]["full_name"] == "Manual Owner"
    assert detail["intermediaries"][0]["entity_name"] == "Manual Holdco Ltd"


def test_save_resume_without_party_arrays_still_succeeds_without_workflow_side_effects(intake_api):
    body = _save_resume(
        intake_api,
        {
            "prescreening": {
                "f-reg-name": "No Party Draft Ltd",
                "f-inc-country": "United Kingdom",
            }
        },
    )
    app_id = body["application_id"]

    app = _db_one("SELECT status, prescreening_data FROM applications WHERE id = ?", (app_id,))
    assert app["status"] == "draft"
    prescreening = json.loads(app["prescreening_data"])
    assert "screening_report" not in prescreening
    assert _db_rows("SELECT * FROM directors WHERE application_id = ?", (app_id,)) == []
    assert _db_rows("SELECT * FROM ubos WHERE application_id = ?", (app_id,)) == []
    assert _db_rows("SELECT * FROM intermediaries WHERE application_id = ?", (app_id,)) == []
    assert _db_rows("SELECT * FROM documents WHERE application_id = ?", (app_id,)) == []


@pytest.mark.parametrize(
    "psc_state,statement_key,expected_prescreening_key",
    [
        ("no_psc", "no_active_psc_entries", "psc_review"),
        ("psc_exempt", "psc_exempt_statement", "psc_review"),
        ("corporate_psc", "active_corporate_psc", "corporate_ownership_review"),
    ],
)
def test_psc_state_metadata_branches(intake_api, psc_state, statement_key, expected_prescreening_key):
    started = _start_intake(intake_api, company_number=f"0000{len(psc_state):04d}")
    psc_result = {
        "provider": "companies_house",
        "jurisdiction": "GB",
        "company_number": started["registry_lookup"]["company_number"],
        "psc_state": psc_state,
        "registry_statement_type": statement_key,
        "psc_status_reason": f"Reason for {psc_state}",
        "beneficial_owners": [] if psc_state != "corporate_psc" else [
            {
                "name": "Corporate PSC Ltd",
                "kind": "corporate",
                "country_of_incorporation": "United Kingdom",
                "registration_number": "99999999",
                "registered_address": {"address_line_1": "1 Holdco Street", "locality": "London"},
                "ownership_pct": "not-exact",
                "owned_or_controlled_by": "Dana Owner",
                "is_candidate_beneficial_owner": True,
            }
        ],
        "source_metadata": {"endpoint": "/psc", "response_hash": f"hash-{psc_state}"},
    }

    resp = requests.post(
        f"{intake_api['base_url']}/api/company-intake/confirm-pscs",
        headers=_headers(intake_api["client1"]),
        json={"session_id": started["session"]["id"], "pscs": psc_result},
        timeout=5,
    )

    assert resp.status_code == 200
    body = resp.json()
    app = _db_one("SELECT prescreening_data FROM applications WHERE id = ?", (started["application"]["id"],))
    prescreening = json.loads(app["prescreening_data"])
    review = prescreening[expected_prescreening_key]
    assert review["psc_state"] == psc_state
    assert review["registry_statement_type"] == statement_key
    assert review["psc_status_reason"] == f"Reason for {psc_state}"
    if psc_state in {"no_psc", "psc_exempt"}:
        assert _db_rows("SELECT * FROM ubos WHERE application_id = ?", (started["application"]["id"],)) == []
    if psc_state == "corporate_psc":
        ubos = _db_rows("SELECT * FROM ubos WHERE application_id = ?", (started["application"]["id"],))
        intermediaries = _db_rows("SELECT * FROM intermediaries WHERE application_id = ?", (started["application"]["id"],))
        assert ubos == []
        assert len(intermediaries) == 1
        assert intermediaries[0]["entity_name"] == "Corporate PSC Ltd"
        assert intermediaries[0]["registration_number"] == "99999999"
        assert intermediaries[0]["registered_address"] == "1 Holdco Street, London"
        assert intermediaries[0]["ownership_pct"] is None
        assert intermediaries[0]["source"] == "companies_house"
        assert intermediaries[0]["response_hash"] == f"hash-{psc_state}"
        assert intermediaries[0]["owned_or_controlled_by"] != "Dana Owner"
        from db import get_db
        from party_utils import extract_fernet_token, get_application_parties

        assert extract_fernet_token(intermediaries[0]["owned_or_controlled_by"])
        conn = get_db()
        try:
            directors_out, ubos_out, intermediaries_out = get_application_parties(conn, started["application"]["id"])
        finally:
            conn.close()
        assert directors_out == []
        assert ubos_out == []
        assert intermediaries_out[0]["owned_or_controlled_by"] == "Dana Owner"
        assert body["intermediaries"][0]["owned_or_controlled_by"] == "Dana Owner"
        intermediary_payload = json.dumps(body["intermediaries"], sort_keys=True, default=str)
        assert "registry_lookup_id" not in intermediary_payload
        assert "response_hash" not in intermediary_payload
        assert "source_metadata_json" not in intermediary_payload
        assert "imported_by" not in intermediary_payload


def test_cross_client_cannot_access_or_confirm_session(intake_api):
    started = _start_intake(intake_api)
    session_id = started["session"]["id"]

    get_resp = requests.get(
        f"{intake_api['base_url']}/api/company-intake/session/{session_id}",
        headers=_headers(intake_api["client2"]),
        timeout=5,
    )
    assert get_resp.status_code == 404

    confirm_resp = requests.post(
        f"{intake_api['base_url']}/api/company-intake/confirm-profile",
        headers=_headers(intake_api["client2"]),
        json={"session_id": session_id},
        timeout=5,
    )
    assert confirm_resp.status_code == 404

    officers_resp = requests.post(
        f"{intake_api['base_url']}/api/company-intake/confirm-officers",
        headers=_headers(intake_api["client2"]),
        json={"session_id": session_id, "officers": [{"name": "Active Director", "officer_role": "director"}]},
        timeout=5,
    )
    assert officers_resp.status_code == 404

    pscs_resp = requests.post(
        f"{intake_api['base_url']}/api/company-intake/confirm-pscs",
        headers=_headers(intake_api["client2"]),
        json={"session_id": session_id, "pscs": {"psc_state": "no_psc", "beneficial_owners": []}},
        timeout=5,
    )
    assert pscs_resp.status_code == 404


def test_session_endpoint_does_not_return_raw_response(intake_api):
    started = _start_intake(intake_api)
    resp = requests.get(
        f"{intake_api['base_url']}/api/company-intake/session/{started['session']['id']}",
        headers=_headers(intake_api["client1"]),
        timeout=5,
    )

    assert resp.status_code == 200
    payload = resp.json()
    text = json.dumps(payload, sort_keys=True)
    assert payload["session"]["registry_lookup"]["response_hash"] == "hash-12345678"
    assert "raw_response_json" not in text
    assert "server-only" not in text
