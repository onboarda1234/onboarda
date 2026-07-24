"""Focused P0 regressions for enhanced-document association integrity."""

import importlib
import json
import os
import socket
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


def _fresh_db(path):
    os.environ["DATABASE_URL"] = ""
    os.environ["DB_PATH"] = path

    import config as config_module
    import db as db_module

    importlib.reload(config_module)
    importlib.reload(db_module)
    db_module.init_db()
    conn = db_module.get_db()
    db_module.seed_initial_data(conn)
    conn.commit()
    conn.close()
    return db_module


def _find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def p0_api_server(tmp_path_factory):
    db_path = str(
        tmp_path_factory.mktemp("p0_backend_document_links")
        / "p0_backend_document_links.db"
    )
    db_module = _fresh_db(db_path)

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
        http_server = tornado.httpserver.HTTPServer(app)
        http_server.listen(port, "127.0.0.1")
        server_ref["server"] = http_server
        server_ref["loop"] = io_loop
        started.set()
        io_loop.start()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    assert started.wait(timeout=3), "P0 regression API server did not start"
    time.sleep(0.2)

    yield f"http://127.0.0.1:{port}", db_module, server_module

    from tests.conftest import shutdown_test_http_server

    shutdown_test_http_server(thread, server_ref)


def _officer_headers(role="admin"):
    from auth import create_token

    user_id = {
        "admin": "admin001",
        "sco": "sco001",
        "co": "co001",
    }[role]
    token = create_token(user_id, role, f"P0 {role}", "officer")
    return {"Authorization": f"Bearer {token}"}


def _client_headers(client_id="client001"):
    from auth import create_token

    token = create_token(client_id, "client", "Northstar Client", "client")
    return {"Authorization": f"Bearer {token}"}


def _insert_application(
    db,
    *,
    status="draft",
    risk_level="LOW",
    company_name="Northstar Holdings Ltd",
):
    suffix = uuid.uuid4().hex[:12]
    app_id = f"p0doc{suffix}"
    ref = f"ARF-2026-{suffix.upper()}"
    db.execute(
        """
        INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, entity_type,
             ownership_structure, prescreening_data, status, risk_level,
             final_risk_level, risk_score, pre_approval_decision)
        VALUES (?, ?, 'client001', ?, 'Mauritius', 'Technology', 'SME',
                'Simple', ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            ref,
            company_name,
            json.dumps({"existing_bank_account": "Yes"}),
            status,
            risk_level,
            risk_level,
            70 if risk_level == "HIGH" else 20,
            "PRE_APPROVE" if risk_level == "HIGH" else None,
        ),
    )
    db.commit()
    return app_id


def _insert_document(
    db,
    app_id,
    *,
    doc_type,
    slot_key,
    person_id=None,
    person_type=None,
    verification_status="pending",
    evidence_class=None,
):
    doc_id = f"doc{uuid.uuid4().hex[:13]}"
    verification_results = None
    verified_at = None
    if verification_status == "verified":
        verified_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        verification_results = json.dumps(
            {
                "overall": "verified",
                "checks": [{"result": "pass"}],
                "verified_at": verified_at,
            }
        )
    db.execute(
        """
        INSERT INTO documents
            (id, application_id, person_id, person_type, doc_type, doc_name,
             file_path, slot_key, is_current, version, verification_status,
             verification_results, verified_at, review_status, evidence_class)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, 'pending', ?)
        """,
        (
            doc_id,
            app_id,
            person_id,
            person_type,
            doc_type,
            f"{doc_type}.pdf",
            f"/tmp/{doc_id}.pdf",
            slot_key,
            verification_status,
            verification_results,
            verified_at,
            evidence_class,
        ),
    )
    db.commit()
    return doc_id


def _insert_requirement(
    db,
    app_id,
    *,
    requirement_key="company_bank_reference",
    status="generated",
    generation_source="manual_api",
    linked_document_id=None,
    monitoring_document_id=None,
    monitoring_alert_id=None,
    trigger_context=None,
):
    suffix = uuid.uuid4().hex[:10]
    cursor = db.execute(
        """
        INSERT INTO application_enhanced_requirements
            (application_id, trigger_key, trigger_label, trigger_category,
             requirement_key, requirement_label, requirement_description,
             audience, requirement_type, subject_scope, blocking_approval,
             waivable, waiver_roles, mandatory, status, generation_source,
             trigger_reason, trigger_context, linked_document_id,
             monitoring_alert_id, monitoring_document_id, created_by, updated_by)
        VALUES (?, ?, 'P0 evidence trigger', 'risk', ?, 'P0 evidence',
                'Focused regression evidence', 'backoffice', 'document',
                'application', 1, 1, '["admin","sco"]', 1, ?, ?,
                'Focused P0 regression', ?, ?, ?, ?, 'admin001', 'admin001')
        """,
        (
            app_id,
            f"p0_trigger_{suffix}",
            requirement_key,
            status,
            generation_source,
            json.dumps(trigger_context or {}),
            linked_document_id,
            monitoring_alert_id,
            monitoring_document_id,
        ),
    )
    db.commit()
    return cursor.lastrowid


def test_strict_enhanced_link_contract_and_historical_link_block_approval_and_pilot(
    p0_api_server,
):
    _base_url, db_module, server_module = p0_api_server
    from enhanced_requirements import (
        enhanced_requirement_document_policy,
        serialize_application_requirement,
        update_application_enhanced_requirement,
        validate_enhanced_requirement_document_link,
        validate_enhanced_requirements_for_approval,
    )

    db = db_module.get_db()
    try:
        app_id = _insert_application(db, status="compliance_review", risk_level="HIGH")
        requirement_id = _insert_requirement(db, app_id)
        requirement = serialize_application_requirement(
            db.execute(
                "SELECT * FROM application_enhanced_requirements WHERE id=?",
                (requirement_id,),
            ).fetchone()
        )
        canonical_type = enhanced_requirement_document_policy(
            requirement["requirement_key"]
        )["document_type"]
        base_document_id = _insert_document(
            db,
            app_id,
            doc_type=canonical_type,
            slot_key=f"entity:{canonical_type}",
            evidence_class="authoritative_live",
        )

        _document, integrity = validate_enhanced_requirement_document_link(
            db,
            app_id,
            requirement,
            base_document_id,
        )
        assert integrity["valid"] is False
        assert integrity["reason"] == "document_slot_mismatch"
        assert integrity["expected_slot_key"] == f"enhanced_requirement:{requirement_id}"

        result, error, status_code = update_application_enhanced_requirement(
            db,
            app_id,
            requirement_id,
            {"linked_document_id": base_document_id},
            actor={"sub": "admin001", "name": "P0 Admin", "role": "admin"},
        )
        assert result is None
        assert status_code == 400
        assert "exact evidence slot" in error

        # Reproduce a historical row created before the strict association
        # contract existed. Status alone must not make that evidence approvable.
        db.execute(
            """
            UPDATE application_enhanced_requirements
               SET status='accepted', linked_document_id=?
             WHERE id=?
            """,
            (base_document_id, requirement_id),
        )
        db.commit()

        approval = validate_enhanced_requirements_for_approval(db, app_id)
        assert approval["passed"] is False
        assert approval["document_integrity_error_count"] == 1
        assert approval["invalid_document_links"][0]["id"] == requirement_id
        assert (
            approval["invalid_document_links"][0]["document_integrity"]["reason"]
            == "document_slot_mismatch"
        )

        app = dict(
            db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
        )
        pilot = server_module._pilot_evidence_classification_summary(db, app)
        invalid_enhanced = [
            item
            for item in pilot["invalid_required"]
            if item.get("enhanced_requirement_id") == requirement_id
        ]
        assert pilot["can_count_as_pilot_approval_proof"] is False
        assert pilot["invalid_required_count"] >= 1
        assert len(invalid_enhanced) == 1
        assert (
            invalid_enhanced[0]["document_link_integrity"]["reason"]
            == "document_slot_mismatch"
        )

        valid_document_id = _insert_document(
            db,
            app_id,
            doc_type=canonical_type,
            slot_key=f"enhanced_requirement:{requirement_id}",
        )
        _document, valid_integrity = validate_enhanced_requirement_document_link(
            db,
            app_id,
            requirement,
            valid_document_id,
        )
        assert valid_integrity["valid"] is True
    finally:
        db.close()


def test_document_requirement_acceptance_requires_link_and_historical_no_link_blocks(
    p0_api_server,
):
    _base_url, db_module, _server_module = p0_api_server
    from enhanced_requirements import (
        update_application_enhanced_requirement,
        validate_enhanced_requirements_for_approval,
    )

    db = db_module.get_db()
    try:
        app_id = _insert_application(
            db,
            status="compliance_review",
            risk_level="HIGH",
        )
        requirement_id = _insert_requirement(
            db,
            app_id,
            status="generated",
        )

        result, error, status_code = update_application_enhanced_requirement(
            db,
            app_id,
            requirement_id,
            {"status": "accepted"},
            actor={"sub": "admin001", "name": "P0 Admin", "role": "admin"},
        )
        assert result is None
        assert status_code == 400
        assert "valid linked document is required" in error
        assert (
            db.execute(
                "SELECT status FROM application_enhanced_requirements WHERE id=?",
                (requirement_id,),
            ).fetchone()["status"]
            == "generated"
        )

        # Persisted pre-fix corruption must fail the read-only approval gate,
        # even though its lifecycle status says accepted.
        db.execute(
            """
            UPDATE application_enhanced_requirements
               SET status='accepted', linked_document_id=NULL
             WHERE id=?
            """,
            (requirement_id,),
        )
        db.commit()
        approval = validate_enhanced_requirements_for_approval(db, app_id)
        assert approval["passed"] is False
        assert approval["document_integrity_error_count"] == 1
        assert approval["unresolved_count"] == 1
        assert (
            approval["invalid_document_links"][0]["document_integrity"]["reason"]
            == "linked_document_missing"
        )
    finally:
        db.close()


def test_monitoring_refresh_backoffice_upload_preserves_original_typed_slot(
    p0_api_server,
):
    base_url, db_module, server_module = p0_api_server
    db = db_module.get_db()
    try:
        app_id = _insert_application(db, status="kyc_documents", risk_level="LOW")
        director_id = f"director{uuid.uuid4().hex[:8]}"
        db.execute(
            """
            INSERT INTO directors
                (id, application_id, person_key, full_name, first_name, last_name)
            VALUES (?, ?, 'director_1', 'Maya North', 'Maya', 'North')
            """,
            (director_id, app_id),
        )
        canonical_slot = f"person:director:{director_id}:passport"
        old_document_id = _insert_document(
            db,
            app_id,
            doc_type="passport",
            slot_key=canonical_slot,
            person_id=director_id,
            person_type="director",
            verification_status="verified",
        )
        requirement_id = _insert_requirement(
            db,
            app_id,
            requirement_key=f"updated_passport_{uuid.uuid4().hex[:6]}",
            status="requested",
            generation_source="monitoring_document_expiry_refresh",
            monitoring_document_id=old_document_id,
            trigger_context={
                "document_id": old_document_id,
                "document_type": "passport",
            },
        )
    finally:
        db.close()

    original_has_s3 = server_module.HAS_S3
    server_module.HAS_S3 = False
    try:
        response = requests.post(
            f"{base_url}/api/applications/{app_id}/enhanced-requirements/"
            f"{requirement_id}/upload",
            headers=_officer_headers("co"),
            files={
                "file": (
                    "renewed-passport.pdf",
                    b"%PDF-1.4\n% renewed typed passport\n%%EOF",
                    "application/pdf",
                )
            },
            timeout=5,
        )
    finally:
        server_module.HAS_S3 = original_has_s3

    assert response.status_code == 201, response.text
    new_document_id = response.json()["document"]["id"]

    db = db_module.get_db()
    try:
        old_document = dict(
            db.execute("SELECT * FROM documents WHERE id=?", (old_document_id,)).fetchone()
        )
        new_document = dict(
            db.execute("SELECT * FROM documents WHERE id=?", (new_document_id,)).fetchone()
        )
        requirement = dict(
            db.execute(
                "SELECT * FROM application_enhanced_requirements WHERE id=?",
                (requirement_id,),
            ).fetchone()
        )

        assert new_document["doc_type"] == old_document["doc_type"] == "passport"
        assert new_document["slot_key"] == old_document["slot_key"] == canonical_slot
        assert new_document["person_id"] == old_document["person_id"] == director_id
        assert (
            new_document["person_type"]
            == old_document["person_type"]
            == "director"
        )
        assert new_document["is_current"] in (1, True)
        assert old_document["is_current"] in (0, False)
        assert old_document["superseded_by_document_id"] == new_document_id
        assert requirement["linked_document_id"] == new_document_id
        assert requirement["status"] == "under_review"

        from enhanced_requirements import (
            serialize_application_requirement,
            validate_enhanced_requirement_document_link,
        )

        _document, integrity = validate_enhanced_requirement_document_link(
            db,
            app_id,
            serialize_application_requirement(requirement),
            new_document_id,
        )
        assert integrity["valid"] is True
    finally:
        db.close()


def test_delete_active_enhanced_link_is_denied_and_association_is_preserved(
    p0_api_server,
):
    base_url, db_module, _server_module = p0_api_server
    db = db_module.get_db()
    try:
        app_id = _insert_application(db, status="draft", risk_level="LOW")
        requirement_id = _insert_requirement(
            db,
            app_id,
            requirement_key="company_bank_reference",
            status="under_review",
        )
        document_id = _insert_document(
            db,
            app_id,
            doc_type="bankref",
            slot_key=f"enhanced_requirement:{requirement_id}",
        )
        db.execute(
            """
            UPDATE application_enhanced_requirements
               SET linked_document_id=?
             WHERE id=?
            """,
            (document_id, requirement_id),
        )
        db.commit()
    finally:
        db.close()

    response = requests.delete(
        f"{base_url}/api/applications/{app_id}/documents/{document_id}",
        headers=_officer_headers("admin"),
        timeout=5,
    )
    assert response.status_code == 409, response.text
    assert "regulated verification or review evidence" in response.json()["error"]

    db = db_module.get_db()
    try:
        assert (
            db.execute(
                "SELECT COUNT(*) AS c FROM documents WHERE id=?",
                (document_id,),
            ).fetchone()["c"]
            == 1
        )
        requirement = db.execute(
            """
            SELECT linked_document_id, active
              FROM application_enhanced_requirements
             WHERE id=?
            """,
            (requirement_id,),
        ).fetchone()
        assert requirement["linked_document_id"] == document_id
        assert requirement["active"] in (1, True)
        denied_audit = db.execute(
            """
            SELECT detail
              FROM audit_log
             WHERE application_id=? AND action='Regulated Delete Denied'
             ORDER BY id DESC
             LIMIT 1
            """,
            (app_id,),
        ).fetchone()
        assert denied_audit is not None
        assert "enhanced_requirement" in denied_audit["detail"]
    finally:
        db.close()


def test_full_applications_projection_includes_document_person_type(p0_api_server):
    base_url, db_module, _server_module = p0_api_server
    db = db_module.get_db()
    try:
        app_id = _insert_application(db, status="draft", risk_level="LOW")
        director_id = f"director{uuid.uuid4().hex[:8]}"
        db.execute(
            """
            INSERT INTO directors
                (id, application_id, person_key, full_name, first_name, last_name)
            VALUES (?, ?, 'director_1', 'Nadia Vale', 'Nadia', 'Vale')
            """,
            (director_id, app_id),
        )
        document_id = _insert_document(
            db,
            app_id,
            doc_type="passport",
            slot_key=f"person:director:{director_id}:passport",
            person_id=director_id,
            person_type="director",
        )
    finally:
        db.close()

    response = requests.get(
        f"{base_url}/api/applications?view=full&include_fixtures=true",
        headers=_officer_headers("admin"),
        timeout=8,
    )
    assert response.status_code == 200, response.text
    application = next(
        item
        for item in response.json()["applications"]
        if item["id"] == app_id
    )
    document = next(
        item for item in application["documents"] if item["id"] == document_id
    )
    assert document["person_id"] == director_id
    assert document["person_type"] == "director"


def test_preapproval_list_uses_fixture_marker_not_e2e_name_heuristic(
    p0_api_server,
):
    """A real E2E-named pilot remains visible while a marked fixture stays hidden."""
    base_url, db_module, _server_module = p0_api_server
    db = db_module.get_db()
    try:
        real_id = _insert_application(
            db,
            status="pre_approval_review",
            risk_level="HIGH",
            company_name="E2E-20260724-150642-S03-Geographic-Risk",
        )
        fixture_id = _insert_application(
            db,
            status="pre_approval_review",
            risk_level="HIGH",
            company_name="E2E-20260724-150642-Fixture-Control",
        )
        db.execute(
            "UPDATE applications SET is_fixture=1 WHERE id=?",
            (fixture_id,),
        )
        db.commit()
    finally:
        db.close()

    response = requests.get(
        f"{base_url}/api/applications",
        params={
            "view": "list",
            "status": "pre_approval_review",
            "q": "E2E-20260724-150642",
            "limit": 100,
        },
        headers=_officer_headers("admin"),
        timeout=8,
    )
    assert response.status_code == 200, response.text
    listed_ids = {item["id"] for item in response.json()["applications"]}
    assert real_id in listed_ids
    assert fixture_id not in listed_ids


def test_document_reliance_ignores_enhanced_special_slot_for_base_resolution(
    p0_api_server,
):
    _base_url, db_module, _server_module = p0_api_server
    from document_reliance_gate import evaluate_document_reliance_gate

    db = db_module.get_db()
    try:
        app_id = _insert_application(db, status="compliance_review", risk_level="LOW")
        base_document_id = _insert_document(
            db,
            app_id,
            doc_type="cert_inc",
            slot_key="entity:cert_inc",
            verification_status="verified",
        )
        _insert_document(
            db,
            app_id,
            doc_type="cert_inc",
            slot_key=f"enhanced_requirement:{uuid.uuid4().hex[:8]}",
            verification_status="verified",
        )
        app = dict(
            db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
        )

        gate = evaluate_document_reliance_gate(
            db,
            app,
            stage="p0_regression",
            require_agent_execution=False,
        )
        certificate = next(
            item
            for item in gate["documents"]
            if item["required_document_type"] == "cert_inc"
        )
        assert certificate["document_id"] == base_document_id
        assert certificate["reliance_state"] == "verified"
        assert not any(
            blocker["code"] == "document_slot_integrity_error"
            and blocker.get("doc_type") == "cert_inc"
            for blocker in gate["blockers"]
        )
    finally:
        db.close()


def test_portal_repairs_historical_base_link_without_superseding_base_document(
    p0_api_server,
):
    base_url, db_module, server_module = p0_api_server
    from enhanced_requirements import list_portal_application_enhanced_requirements

    db = db_module.get_db()
    try:
        app_id = _insert_application(db, status="submitted", risk_level="LOW")
        requirement_id = _insert_requirement(
            db,
            app_id,
            requirement_key="company_bank_reference",
            status="uploaded",
        )
        base_document_id = _insert_document(
            db,
            app_id,
            doc_type="bankref",
            slot_key="entity:bankref",
            verification_status="verified",
        )
        db.execute(
            """
            UPDATE application_enhanced_requirements
               SET audience='client',
                   linked_document_id=?,
                   uploaded_at=datetime('now')
             WHERE id=?
            """,
            (base_document_id, requirement_id),
        )
        db.commit()

        portal_items = list_portal_application_enhanced_requirements(db, app_id)
        item = next(row for row in portal_items if row["id"] == requirement_id)
        assert item["status"] == "required"
        assert item["status_label"] == "Action required"
        assert item["linked_document_integrity_valid"] is False
        assert item["linked_document_integrity"]["valid"] is False
        assert "upload the requested document again" in item[
            "linked_document_integrity_error"
        ].lower()
        assert "linked_document" not in item
        assert "slot" not in json.dumps(item["linked_document_integrity"]).lower()
    finally:
        db.close()

    original_has_s3 = server_module.HAS_S3
    server_module.HAS_S3 = False
    try:
        response = requests.post(
            f"{base_url}/api/portal/applications/{app_id}/"
            f"enhanced-requirements/{requirement_id}/upload",
            headers=_client_headers(),
            files={
                "file": (
                    "replacement-bank-reference.pdf",
                    b"%PDF-1.4\n% repaired enhanced evidence\n%%EOF",
                    "application/pdf",
                )
            },
            timeout=5,
        )
    finally:
        server_module.HAS_S3 = original_has_s3

    assert response.status_code == 201, response.text
    body = response.json()
    replacement_document_id = body["document"]["id"]
    assert body["document"]["slot_key"] == f"enhanced_requirement:{requirement_id}"
    assert body["requirement"]["linked_document_integrity_valid"] is True

    db = db_module.get_db()
    try:
        base_document = dict(
            db.execute(
                "SELECT * FROM documents WHERE id=?",
                (base_document_id,),
            ).fetchone()
        )
        replacement_document = dict(
            db.execute(
                "SELECT * FROM documents WHERE id=?",
                (replacement_document_id,),
            ).fetchone()
        )
        requirement = dict(
            db.execute(
                "SELECT * FROM application_enhanced_requirements WHERE id=?",
                (requirement_id,),
            ).fetchone()
        )
        assert base_document["is_current"] in (1, True)
        assert base_document["superseded_at"] in (None, "")
        assert base_document["superseded_by_document_id"] in (None, "")
        assert replacement_document["is_current"] in (1, True)
        assert (
            replacement_document["slot_key"]
            == f"enhanced_requirement:{requirement_id}"
        )
        assert replacement_document["doc_type"] == "bankref"
        assert requirement["linked_document_id"] == replacement_document_id
        assert requirement["status"] == "uploaded"
    finally:
        db.close()


def test_monitoring_validator_enforces_successor_lineage_and_canonical_owner(
    p0_api_server,
):
    _base_url, db_module, _server_module = p0_api_server
    from enhanced_requirements import (
        serialize_application_requirement,
        validate_enhanced_requirement_document_link,
    )

    db = db_module.get_db()
    try:
        app_id = _insert_application(db, status="kyc_documents", risk_level="LOW")

        self_target_id = _insert_document(
            db,
            app_id,
            doc_type="cert_inc",
            slot_key="entity:cert_inc",
        )
        self_requirement_id = _insert_requirement(
            db,
            app_id,
            requirement_key=f"updated_certificate_{uuid.uuid4().hex[:6]}",
            status="requested",
            generation_source="monitoring_document_expiry_refresh",
            monitoring_document_id=self_target_id,
        )
        self_requirement = serialize_application_requirement(
            db.execute(
                "SELECT * FROM application_enhanced_requirements WHERE id=?",
                (self_requirement_id,),
            ).fetchone()
        )
        _document, self_integrity = validate_enhanced_requirement_document_link(
            db,
            app_id,
            self_requirement,
            self_target_id,
        )
        assert self_integrity["valid"] is False
        assert self_integrity["reason"] == "monitoring_replacement_self_link"

        director_id = f"director{uuid.uuid4().hex[:8]}"
        legacy_person_key = "director_legacy_1"
        db.execute(
            """
            INSERT INTO directors
                (id, application_id, person_key, full_name, first_name, last_name)
            VALUES (?, ?, ?, 'Leila Rowan', 'Leila', 'Rowan')
            """,
            (director_id, app_id, legacy_person_key),
        )
        legacy_target_id = _insert_document(
            db,
            app_id,
            doc_type="passport",
            slot_key=f"person:{legacy_person_key}:passport",
            person_id=legacy_person_key,
            person_type="director",
        )
        lineage_requirement_id = _insert_requirement(
            db,
            app_id,
            requirement_key=f"updated_passport_{uuid.uuid4().hex[:6]}",
            status="requested",
            generation_source="monitoring_document_expiry_refresh",
            monitoring_document_id=legacy_target_id,
        )
        legacy_requirement = serialize_application_requirement(
            db.execute(
                "SELECT * FROM application_enhanced_requirements WHERE id=?",
                (lineage_requirement_id,),
            ).fetchone()
        )
        _document, legacy_integrity = validate_enhanced_requirement_document_link(
            db,
            app_id,
            legacy_requirement,
            self_target_id,
        )
        assert legacy_integrity["valid"] is False
        assert (
            legacy_integrity["reason"]
            == "monitoring_target_owner_id_noncanonical"
        )

        canonical_target_id = _insert_document(
            db,
            app_id,
            doc_type="passport",
            slot_key=f"person:director:{director_id}:passport",
            person_id=director_id,
            person_type="director",
        )
        canonical_requirement_id = _insert_requirement(
            db,
            app_id,
            requirement_key=f"updated_passport_{uuid.uuid4().hex[:6]}",
            status="requested",
            generation_source="monitoring_document_expiry_refresh",
            monitoring_document_id=canonical_target_id,
        )
        lineage_requirement = serialize_application_requirement(
            db.execute(
                "SELECT * FROM application_enhanced_requirements WHERE id=?",
                (canonical_requirement_id,),
            ).fetchone()
        )

        db.execute(
            """
            UPDATE documents
               SET is_current=0,
                   superseded_at=datetime('now'),
                   superseded_by_document_id='unrelated-successor'
             WHERE id=?
            """,
            (canonical_target_id,),
        )
        db.commit()
        successor_id = _insert_document(
            db,
            app_id,
            doc_type="passport",
            slot_key=f"person:director:{director_id}:passport",
            person_id=director_id,
            person_type="director",
        )
        db.execute(
            "UPDATE documents SET version=2 WHERE id=?",
            (successor_id,),
        )
        db.commit()
        _document, wrong_lineage_integrity = (
            validate_enhanced_requirement_document_link(
                db,
                app_id,
                lineage_requirement,
                successor_id,
            )
        )
        assert wrong_lineage_integrity["valid"] is False
        assert (
            wrong_lineage_integrity["reason"]
            == "monitoring_replacement_lineage_mismatch"
        )

        db.execute(
            """
            UPDATE documents
               SET superseded_by_document_id=?
             WHERE id=?
            """,
            (successor_id, canonical_target_id),
        )
        db.commit()
        _document, valid_integrity = validate_enhanced_requirement_document_link(
            db,
            app_id,
            lineage_requirement,
            successor_id,
        )
        assert valid_integrity["valid"] is True
        assert valid_integrity["expected_slot_key"] == (
            f"person:director:{director_id}:passport"
        )

        db.execute(
            """
            UPDATE documents
               SET superseded_by_document_id='unexpected-next-document'
             WHERE id=?
            """,
            (successor_id,),
        )
        db.commit()
        _document, inconsistent_current_integrity = (
            validate_enhanced_requirement_document_link(
                db,
                app_id,
                lineage_requirement,
                successor_id,
            )
        )
        assert inconsistent_current_integrity["valid"] is False
        assert (
            inconsistent_current_integrity["reason"]
            == "current_document_has_successor"
        )
        db.execute(
            "UPDATE documents SET superseded_by_document_id=NULL WHERE id=?",
            (successor_id,),
        )
        db.commit()

        missing_requirement_id = _insert_requirement(
            db,
            app_id,
            requirement_key=f"updated_passport_{uuid.uuid4().hex[:6]}",
            status="requested",
            generation_source="monitoring_document_expiry_refresh",
            monitoring_document_id="missing-monitoring-target",
        )
        missing_requirement = serialize_application_requirement(
            db.execute(
                "SELECT * FROM application_enhanced_requirements WHERE id=?",
                (missing_requirement_id,),
            ).fetchone()
        )
        _document, missing_integrity = validate_enhanced_requirement_document_link(
            db,
            app_id,
            missing_requirement,
            successor_id,
        )
        assert missing_integrity["valid"] is False
        assert missing_integrity["reason"] == "monitoring_target_document_missing"
    finally:
        db.close()


def test_monitoring_refresh_target_helper_refuses_noncanonical_metadata(
    p0_api_server,
):
    _base_url, db_module, server_module = p0_api_server
    db = db_module.get_db()
    try:
        app_id = _insert_application(db, status="kyc_documents", risk_level="LOW")
        director_id = f"director{uuid.uuid4().hex[:8]}"
        legacy_person_key = f"director_key_{uuid.uuid4().hex[:6]}"
        db.execute(
            """
            INSERT INTO directors
                (id, application_id, person_key, full_name, first_name, last_name)
            VALUES (?, ?, ?, 'Mina Sol', 'Mina', 'Sol')
            """,
            (director_id, app_id, legacy_person_key),
        )
        targets = [
            (
                _insert_document(
                    db,
                    app_id,
                    doc_type="certificate_of_incorporation",
                    slot_key="entity:cert_inc",
                ),
                "stored document type is not canonical",
            ),
            (
                _insert_document(
                    db,
                    app_id,
                    doc_type="cert_inc",
                    slot_key="entity:certificate_of_incorporation",
                ),
                "stored entity document slot is not canonical",
            ),
            (
                _insert_document(
                    db,
                    app_id,
                    doc_type="passport",
                    slot_key=f"person:director:{director_id}:passport",
                    person_id=legacy_person_key,
                    person_type="director",
                ),
                "stable row ID",
            ),
            (
                _insert_document(
                    db,
                    app_id,
                    doc_type="poa",
                    slot_key=f"person:director:{director_id}:poa",
                    person_id=director_id,
                    person_type="directors",
                ),
                "party type metadata is not canonical",
            ),
        ]
        for target_id, expected_error in targets:
            requirement_id = _insert_requirement(
                db,
                app_id,
                requirement_key=f"updated_document_{uuid.uuid4().hex[:6]}",
                status="requested",
                generation_source="monitoring_document_expiry_refresh",
                monitoring_document_id=target_id,
            )
            requirement = dict(
                db.execute(
                    "SELECT * FROM application_enhanced_requirements WHERE id=?",
                    (requirement_id,),
                ).fetchone()
            )
            with pytest.raises(ValueError, match=expected_error):
                server_module._monitoring_refresh_upload_target(
                    db,
                    app_id,
                    requirement,
                )
    finally:
        db.close()


def test_monitoring_refresh_invalid_targets_return_409_without_document_write(
    p0_api_server,
):
    base_url, db_module, server_module = p0_api_server
    db = db_module.get_db()
    try:
        officer_app_id = _insert_application(
            db,
            status="kyc_documents",
            risk_level="LOW",
        )
        officer_target_id = _insert_document(
            db,
            officer_app_id,
            doc_type="cert_inc",
            slot_key=f"rmi:{uuid.uuid4().hex[:8]}",
            verification_status="verified",
        )
        officer_requirement_id = _insert_requirement(
            db,
            officer_app_id,
            requirement_key=f"updated_certificate_{uuid.uuid4().hex[:6]}",
            status="requested",
            generation_source="monitoring_document_expiry_refresh",
            monitoring_document_id=officer_target_id,
        )

        portal_app_id = _insert_application(
            db,
            status="submitted",
            risk_level="LOW",
        )
        portal_target_id = _insert_document(
            db,
            portal_app_id,
            doc_type="cert_inc",
            slot_key=f"enhanced_requirement:{uuid.uuid4().hex[:8]}",
            verification_status="verified",
        )
        portal_requirement_id = _insert_requirement(
            db,
            portal_app_id,
            requirement_key=f"updated_certificate_{uuid.uuid4().hex[:6]}",
            status="requested",
            generation_source="monitoring_document_expiry_refresh",
            monitoring_document_id=portal_target_id,
        )
        db.execute(
            "UPDATE application_enhanced_requirements SET audience='client' WHERE id=?",
            (portal_requirement_id,),
        )

        alert_app_id = _insert_application(
            db,
            status="approved",
            risk_level="LOW",
        )
        alert_target_id = _insert_document(
            db,
            alert_app_id,
            doc_type="passport",
            slot_key="entity:passport",
            verification_status="verified",
        )
        alert_cursor = db.execute(
            """
            INSERT INTO monitoring_alerts
                (application_id, client_name, alert_type, severity, detected_by,
                 summary, source_reference, status, discovered_via)
            VALUES (?, 'Northstar Holdings Ltd', 'document_expired', 'medium',
                    'Document Health Monitor', 'Identity document requires refresh',
                    ?, 'open', 'document_health')
            """,
            (alert_app_id, f"document:{alert_target_id}"),
        )
        alert_id = alert_cursor.lastrowid
        initial_document_count = db.execute(
            "SELECT COUNT(*) AS c FROM documents"
        ).fetchone()["c"]
        initial_requirement_count = db.execute(
            "SELECT COUNT(*) AS c FROM application_enhanced_requirements"
        ).fetchone()["c"]
        db.commit()
    finally:
        db.close()

    original_has_s3 = server_module.HAS_S3
    server_module.HAS_S3 = False
    try:
        officer_response = requests.post(
            f"{base_url}/api/applications/{officer_app_id}/enhanced-requirements/"
            f"{officer_requirement_id}/upload",
            headers=_officer_headers("co"),
            files={
                "file": (
                    "replacement.pdf",
                    b"%PDF-1.4\n% special-slot replacement\n%%EOF",
                    "application/pdf",
                )
            },
            timeout=5,
        )
        portal_response = requests.post(
            f"{base_url}/api/portal/applications/{portal_app_id}/"
            f"enhanced-requirements/{portal_requirement_id}/upload",
            headers=_client_headers(),
            files={
                "file": (
                    "replacement.pdf",
                    b"%PDF-1.4\n% portal special-slot replacement\n%%EOF",
                    "application/pdf",
                )
            },
            timeout=5,
        )
        alert_response = requests.post(
            f"{base_url}/api/monitoring/alerts/{alert_id}/replacement-upload",
            headers=_officer_headers("admin"),
            data={"source_note": "Replacement supplied by the client."},
            files={
                "file": (
                    "replacement.pdf",
                    b"%PDF-1.4\n% invalid entity identity replacement\n%%EOF",
                    "application/pdf",
                )
            },
            timeout=5,
        )
    finally:
        server_module.HAS_S3 = original_has_s3

    for response in (officer_response, portal_response, alert_response):
        assert response.status_code == 409, response.text
        assert "requires repair before replacement" in response.json()["error"]

    db = db_module.get_db()
    try:
        assert (
            db.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"]
            == initial_document_count
        )
        assert (
            db.execute(
                "SELECT COUNT(*) AS c FROM application_enhanced_requirements"
            ).fetchone()["c"]
            == initial_requirement_count
        )
        for target_id in (
            officer_target_id,
            portal_target_id,
            alert_target_id,
        ):
            target = db.execute(
                "SELECT is_current, superseded_by_document_id FROM documents WHERE id=?",
                (target_id,),
            ).fetchone()
            assert target["is_current"] in (1, True)
            assert target["superseded_by_document_id"] in (None, "")
    finally:
        db.close()


def test_periodic_review_rejects_verified_historical_base_document_link(
    p0_api_server,
):
    _base_url, db_module, _server_module = p0_api_server
    from periodic_review_blockers import evaluate_review_readiness
    from periodic_review_engine import (
        OUTCOME_NO_CHANGE,
        ReviewCompletionBlocked,
        record_review_outcome,
    )
    from periodic_review_projection_service import build_review_projection

    db = db_module.get_db()
    try:
        app_id = _insert_application(db, status="approved", risk_level="LOW")
        cursor = db.execute(
            """
            INSERT INTO periodic_reviews
                (application_id, client_name, risk_level, status, due_date,
                 client_attestation_status, baseline_status, officer_rationale,
                 outcome, required_items, priority)
            VALUES (?, 'Northstar Holdings Ltd', 'LOW', 'in_progress',
                    '2027-12-31', 'submitted', 'not_applicable',
                    'Periodic review evidence assessed.', 'no_change', '[]',
                    'medium')
            """,
            (app_id,),
        )
        review_id = cursor.lastrowid
        requirement_id = _insert_requirement(
            db,
            app_id,
            requirement_key="company_bank_reference",
            status="accepted",
        )
        base_document_id = _insert_document(
            db,
            app_id,
            doc_type="bankref",
            slot_key="entity:bankref",
            verification_status="verified",
        )
        db.execute(
            """
            UPDATE application_enhanced_requirements
               SET linked_document_id=?,
                   linked_periodic_review_id=?
             WHERE id=?
            """,
            (base_document_id, review_id, requirement_id),
        )
        db.commit()
        review = dict(
            db.execute(
                "SELECT * FROM periodic_reviews WHERE id=?",
                (review_id,),
            ).fetchone()
        )

        readiness = evaluate_review_readiness(db, review)
        invalid_request_blockers = [
            blocker
            for blocker in readiness["operational_blockers"]
            if blocker.get("source") == "application_enhanced_requirements"
            and blocker.get("source_id") == requirement_id
        ]
        assert readiness["operational_ready"] is False
        assert readiness["completion_ready"] is False
        assert len(invalid_request_blockers) == 1
        assert "invalid evidence association" in invalid_request_blockers[0][
            "label"
        ].lower()

        projection = build_review_projection(db, review)
        assert projection["completion_ready"] is False
        assert projection["is_blocked"] is True
        assert (
            projection["periodic_review_documents_pending_review_count"] == 1
        )
        assert any(
            "invalid evidence association" in label.lower()
            for label in projection["blocker_summary"]
        )

        def audit_writer(*_args, **_kwargs):
            return None

        with pytest.raises(ReviewCompletionBlocked) as completion_error:
            record_review_outcome(
                db,
                review_id,
                outcome=OUTCOME_NO_CHANGE,
                outcome_reason="No material change was identified.",
                rationale="No material change was identified.",
                officer_acknowledgement=True,
                enforce_prs5_gates=True,
                user={"sub": "admin001", "name": "P0 Admin", "role": "admin"},
                audit_writer=audit_writer,
            )
        assert any(
            blocker.get("source") == "application_enhanced_requirements"
            and blocker.get("source_id") == requirement_id
            for blocker in completion_error.value.blocking_items
        )
        persisted_review = db.execute(
            "SELECT status, completed_at FROM periodic_reviews WHERE id=?",
            (review_id,),
        ).fetchone()
        assert persisted_review["status"] == "in_progress"
        assert persisted_review["completed_at"] in (None, "")
    finally:
        db.close()


def test_monitoring_alert_replacement_upload_builds_multi_hop_successor_chain(
    p0_api_server,
):
    base_url, db_module, server_module = p0_api_server
    db = db_module.get_db()
    try:
        app_id = _insert_application(db, status="approved", risk_level="LOW")
        root_document_id = _insert_document(
            db,
            app_id,
            doc_type="cert_inc",
            slot_key="entity:cert_inc",
            verification_status="verified",
        )
        db.execute(
            """
            UPDATE documents
               SET expiry_date='2026-01-01'
             WHERE id=?
            """,
            (root_document_id,),
        )
        alert_cursor = db.execute(
            """
            INSERT INTO monitoring_alerts
                (application_id, client_name, alert_type, severity, detected_by,
                 summary, source_reference, status, discovered_via)
            VALUES (?, 'Northstar Holdings Ltd', 'document_expired', 'medium',
                    'Document Health Monitor', 'Certificate requires refresh',
                    ?, 'open', 'document_health')
            """,
            (app_id, f"document:{root_document_id}"),
        )
        alert_id = alert_cursor.lastrowid
        db.commit()
    finally:
        db.close()

    original_has_s3 = server_module.HAS_S3
    server_module.HAS_S3 = False
    try:
        first = requests.post(
            f"{base_url}/api/monitoring/alerts/{alert_id}/replacement-upload",
            headers=_officer_headers("admin"),
            data={"source_note": "First renewed certificate received by email."},
            files={
                "file": (
                    "renewed-certificate-v2.pdf",
                    b"%PDF-1.4\n% first monitoring successor\n%%EOF",
                    "application/pdf",
                )
            },
            timeout=5,
        )
        assert first.status_code == 201, first.text

        second = requests.post(
            f"{base_url}/api/monitoring/alerts/{alert_id}/replacement-upload",
            headers=_officer_headers("admin"),
            data={"source_note": "Corrected renewed certificate received by email."},
            files={
                "file": (
                    "renewed-certificate-v3.pdf",
                    b"%PDF-1.4\n% second monitoring successor\n%%EOF",
                    "application/pdf",
                )
            },
            timeout=5,
        )
    finally:
        server_module.HAS_S3 = original_has_s3

    assert second.status_code == 201, second.text
    first_body = first.json()
    second_body = second.json()
    first_document_id = first_body["document"]["id"]
    second_document_id = second_body["document"]["id"]
    requirement_id = first_body["document_request"]["id"]
    assert second_body["document_request"]["id"] == requirement_id
    assert first_body["document"]["version"] == 2
    assert second_body["document"]["version"] == 3
    assert first_body["document"]["replaced_document_ids"] == [root_document_id]
    assert second_body["document"]["replaced_document_ids"] == [first_document_id]

    db = db_module.get_db()
    try:
        root_document = dict(
            db.execute(
                "SELECT * FROM documents WHERE id=?",
                (root_document_id,),
            ).fetchone()
        )
        first_document = dict(
            db.execute(
                "SELECT * FROM documents WHERE id=?",
                (first_document_id,),
            ).fetchone()
        )
        second_document = dict(
            db.execute(
                "SELECT * FROM documents WHERE id=?",
                (second_document_id,),
            ).fetchone()
        )
        requirement = dict(
            db.execute(
                "SELECT * FROM application_enhanced_requirements WHERE id=?",
                (requirement_id,),
            ).fetchone()
        )

        assert root_document["version"] == 1
        assert root_document["is_current"] in (0, False)
        assert root_document["superseded_by_document_id"] == first_document_id
        assert first_document["version"] == 2
        assert first_document["is_current"] in (0, False)
        assert first_document["superseded_by_document_id"] == second_document_id
        assert second_document["version"] == 3
        assert second_document["is_current"] in (1, True)
        assert second_document["superseded_by_document_id"] in (None, "")
        assert {
            root_document["slot_key"],
            first_document["slot_key"],
            second_document["slot_key"],
        } == {"entity:cert_inc"}
        assert requirement["monitoring_document_id"] == root_document_id
        assert requirement["linked_document_id"] == second_document_id
        assert requirement["status"] == "under_review"

        from enhanced_requirements import (
            serialize_application_requirement,
            validate_enhanced_requirement_document_link,
        )

        _document, integrity = validate_enhanced_requirement_document_link(
            db,
            app_id,
            serialize_application_requirement(requirement),
            second_document_id,
        )
        assert integrity["valid"] is True
    finally:
        db.close()


def test_periodic_review_consumers_treat_invalid_verified_base_link_as_missing(
    p0_api_server,
):
    _base_url, db_module, _server_module = p0_api_server
    from periodic_review_memo import build_memo_data
    from periodic_review_notifications import (
        notification_projection_from_review,
        periodic_review_document_notification_summary,
    )
    from periodic_review_risk_reassessment import build_reassessment_snapshot

    db = db_module.get_db()
    try:
        app_id = _insert_application(db, status="approved", risk_level="LOW")
        review_cursor = db.execute(
            """
            INSERT INTO periodic_reviews
                (application_id, client_name, risk_level, status, due_date,
                 client_attestation_status, baseline_status, officer_rationale,
                 required_items, priority)
            VALUES (?, 'Northstar Holdings Ltd', 'LOW', 'in_progress',
                    '2027-12-31', 'submitted', 'not_applicable',
                    'Consumer projection integrity regression.', '[]', 'medium')
            """,
            (app_id,),
        )
        review_id = review_cursor.lastrowid
        requirement_id = _insert_requirement(
            db,
            app_id,
            requirement_key="company_bank_reference",
            status="accepted",
        )
        base_document_id = _insert_document(
            db,
            app_id,
            doc_type="bankref",
            slot_key="entity:bankref",
            verification_status="verified",
        )
        db.execute(
            """
            UPDATE application_enhanced_requirements
               SET linked_document_id=?,
                   linked_periodic_review_id=?,
                   audience='client'
             WHERE id=?
            """,
            (base_document_id, review_id, requirement_id),
        )
        db.commit()
        review = dict(
            db.execute(
                "SELECT * FROM periodic_reviews WHERE id=?",
                (review_id,),
            ).fetchone()
        )

        notification_summary = periodic_review_document_notification_summary(
            db,
            review_id,
        )
        assert notification_summary["required_count"] == 1
        assert notification_summary["missing_count"] == 1
        assert notification_summary["review_required_count"] == 0
        assert notification_summary["outstanding_labels"] == ["P0 evidence"]
        notification_projection = notification_projection_from_review(
            review,
            document_summary=notification_summary,
        )
        assert (
            notification_projection["client_action_required"]
            == "documents_required"
        )
        assert (
            notification_projection["client_action_required_label"]
            == "Upload requested periodic review documents"
        )

        memo = build_memo_data(db, review_id)
        documents_summary = memo["documents_summary"]
        assert documents_summary["requested_count"] == 1
        assert documents_summary["uploaded_count"] == 0
        assert documents_summary["outstanding_count"] == 1
        assert documents_summary["items"][0]["uploaded"] is False
        assert (
            documents_summary["items"][0]["linked_document_integrity_valid"]
            is False
        )

        reassessment = build_reassessment_snapshot(db, review_id)
        evidence_counts = reassessment["suggested"]["evidence_counts"]
        assert evidence_counts["document_request_count"] == 1
        assert evidence_counts["missing_document_count"] == 1
        assert evidence_counts["uploaded_document_count"] == 0
        assert not any(
            "periodic-review document(s) were uploaded" in reason
            for reason in reassessment["suggested"]["reason_summary"]
        )
    finally:
        db.close()
