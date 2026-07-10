"""
Portal-to-backoffice application approval E2E coverage.

This file documents the approval checklist discovered from the production
approval path before exercising it.  The clean path intentionally uses normal
client/officer auth, public application/document/KYC endpoints, back-office
detail/list reads, and the real final decision endpoint.  Only nondeterministic
external-provider states are cleared by clearly labelled non-production
fixtures after the portal submission exists.

Approval checklist from code:
- Status/stage: the application must be in a decisionable review state
  (`kyc_submitted`, `compliance_review`, `submitted_to_compliance`,
  `in_review`, `under_review`, or `edd_required`), not a pre-KYC or terminal
  state.
- Risk: current `risk_score`, `risk_level`/`final_risk_level`,
  `risk_config_version`, and risk provenance must pass integrity and staleness
  checks.
- Route: clean LOW/MEDIUM applications use `direct_low_medium`; HIGH,
  VERY_HIGH, EDD, adverse media, PEP, or compliance-escalated routes require
  compliance package and/or dual-control gates.
- Documents: every mandatory entity/director/UBO document slot must be present
  as the current document, verified or senior-manual-accepted, fresh, with
  passing verification evidence and Agent 1 execution proof.
- Screening: ComplyAdvantage screening must be live, terminal, defensible clear,
  fresh, provider-reference backed, and free of unresolved second-review or
  adverse-media/SOT blockers.
- IDV: every required client/director/UBO subject must be `verified`,
  `manual_verified`, or `exception_approved`; pending, failed, rejected, or
  unable-to-verify states block approval.
- Memo/supervisor: mandatory only when `classify_approval_route` requires a
  compliance package; the direct LOW/MEDIUM route is valid without a memo.
- Other blockers: no open RMI, unresolved enhanced/EDD/SAR/change-management
  blocker, unauthorized role, or ownership/signoff violation may remain.
- Decision/audit: final approval must go through `/api/applications/:id/decision`
  and atomically write application status, audit log, officer signoff, governance
  attempt, and normalized decision record.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import pytest
import requests as http_requests
import tornado.httpserver
import tornado.ioloop


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")
os.environ.setdefault("SUMSUB_AML_ENTITLEMENT_PROVEN", "true")


APPROVAL_CHECKLIST = [
    "decisionable_status",
    "risk_current_and_integrity_checked",
    "direct_low_medium_route_or_compliance_package_required",
    "required_documents_verified_with_agent1_evidence",
    "screening_live_terminal_clear_and_fresh",
    "idv_verified_or_manual_resolution",
    "memo_supervisor_package_when_route_requires_it",
    "no_open_rmi_enhanced_edd_sar_or_change_management_blockers",
    "authorized_actor_and_signoff_ownership",
    "real_decision_endpoint_writes_audit_and_decision_record",
]

PORTALE2E_FIXTURE_ENVIRONMENTS = {"testing", "staging"}
PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
CLIENT_PASSWORD = "PortalE2EClient2026!"
OFFICER_PASSWORD = "PortalE2EBackoffice2026!"


def _find_free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture()
def portal_approval_server(tmp_path):
    """Start an isolated HTTP API server backed by a per-test SQLite DB."""

    db_path = str(tmp_path / "portal_to_approval_e2e.db")
    os.environ["ENVIRONMENT"] = "testing"
    os.environ["DB_PATH"] = db_path

    from tests.conftest import _sync_test_db_path, shutdown_test_http_server

    _sync_test_db_path(db_path)

    from db import get_db, init_db, seed_initial_data
    import server as server_module
    from server import make_app

    original_has_s3 = server_module.HAS_S3
    server_module.HAS_S3 = False

    init_db()
    conn = get_db()
    try:
        try:
            seed_initial_data(conn)
            conn.commit()
        except Exception:
            # The seeder is best-effort in the wider test suite; this E2E test
            # creates its own client/officer actors below.
            conn.rollback()
    finally:
        conn.close()

    app = make_app()
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

    try:
        yield {"base_url": f"http://127.0.0.1:{port}", "db_path": db_path}
    finally:
        try:
            shutdown_test_http_server(thread, server_ref)
        finally:
            server_module.HAS_S3 = original_has_s3

        try:
            cleanup = sqlite3.connect(db_path)
            cleanup.row_factory = sqlite3.Row
            for row in cleanup.execute("SELECT file_path FROM documents").fetchall():
                file_path = row["file_path"]
                if file_path and os.path.exists(file_path):
                    os.remove(file_path)
            cleanup.close()
        except Exception:
            pass


def _headers(token: str, request_id: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    if request_id:
        headers["X-Request-Id"] = request_id
    return headers


def _assert_ok(response, expected=200):
    assert response.status_code == expected, response.text
    return response.json()


def _post_json(base_url: str, path: str, token: str | None, payload: dict, *, expected=200):
    headers = _headers(token) if token else {}
    return _assert_ok(
        http_requests.post(f"{base_url}{path}", headers=headers, json=payload, timeout=10),
        expected,
    )


def _get_json(base_url: str, path: str, token: str, *, expected=200):
    return _assert_ok(http_requests.get(f"{base_url}{path}", headers=_headers(token), timeout=10), expected)


def _table_columns(db, table_name: str) -> set[str]:
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _update_columns(db, table_name: str, row_id: str, values: dict):
    columns = _table_columns(db, table_name)
    assignments = []
    params = []
    for key, value in values.items():
        if key in columns:
            assignments.append(f"{key}=?")
            params.append(value)
    if not assignments:
        return
    params.append(row_id)
    db.execute(f"UPDATE {table_name} SET {', '.join(assignments)} WHERE id=?", params)


def _insert_audit(
    db,
    *,
    user_id: str,
    user_name: str,
    user_role: str,
    action: str,
    target: str,
    application_id: str,
    detail,
    request_id: str,
    before_state=None,
    after_state=None,
):
    columns = _table_columns(db, "audit_log")
    payload = {
        "user_id": user_id,
        "user_name": user_name,
        "user_role": user_role,
        "action": action,
        "target": target,
        "application_id": application_id,
        "detail": json.dumps(detail, default=str, sort_keys=True)
        if not isinstance(detail, str)
        else detail,
        "ip_address": "127.0.0.1",
        "before_state": json.dumps(before_state, default=str, sort_keys=True) if before_state else None,
        "after_state": json.dumps(after_state, default=str, sort_keys=True) if after_state else None,
        "request_id": request_id,
    }
    insertable = {key: value for key, value in payload.items() if key in columns}
    placeholders = ",".join("?" for _ in insertable)
    db.execute(
        f"INSERT INTO audit_log ({', '.join(insertable)}) VALUES ({placeholders})",
        tuple(insertable.values()),
    )


def _assert_portale2e_fixture_environment():
    environment = os.environ.get("ENVIRONMENT", "").strip().lower()
    assert environment in PORTALE2E_FIXTURE_ENVIRONMENTS, (
        "PORTALE2E fixture-assisted clearance helpers are only allowed in "
        "testing/staging and must never be used in production or pilot runtime flows."
    )


def _seed_backoffice_user(db_path: str, suffix: str) -> dict:
    from tests.conftest import _sync_test_db_path
    from db import get_db

    _sync_test_db_path(db_path)
    user = {
        "id": f"portale2e_admin_{suffix[:8]}",
        "email": f"portale2e-admin-{suffix}@example.test",
        "name": f"PORTALE2E Admin {suffix}",
        "role": "admin",
    }
    password_hash = bcrypt.hashpw(OFFICER_PASSWORD.encode(), bcrypt.gensalt()).decode()
    db = get_db()
    try:
        db.execute(
            """
            INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status)
            VALUES (?, ?, ?, ?, 'admin', 'active')
            """,
            (user["id"], user["email"], password_hash, user["name"]),
        )
        db.commit()
    finally:
        db.close()
    return user


def _application_payload(prefix: str, suffix: str) -> dict:
    company_name = f"{prefix} Approval Clean Ltd {suffix}"
    return {
        "company_name": company_name,
        "registered_entity_name": company_name,
        "brn": f"BRN-{suffix[:10]}",
        "country": "United Kingdom",
        "country_of_incorporation": "United Kingdom",
        "sector": "Software",
        "entity_type": "Private Limited Company",
        "ownership_structure": "simple",
        "operating_countries": ["United Kingdom"],
        "countries_of_operation": ["United Kingdom"],
        "target_markets": ["United Kingdom"],
        "currencies": ["GBP"],
        "primary_service": "Software platform subscription",
        "service_required": "Software platform subscription",
        "source_of_wealth": "business revenue",
        "source_of_funds": "business revenue",
        "monthly_volume": "10000",
        "expected_volume": "10000",
        "payment_corridors": "Domestic UK",
        "introduction_method": "direct",
        "customer_interaction": "face-to-face",
        "entity_contact_email": f"portale2e-{suffix}@example.test",
        "directors": [
            {
                "person_key": "director-1",
                "first_name": "Portal",
                "last_name": "Director",
                "full_name": f"{prefix} Director {suffix}",
                "nationality": "United Kingdom",
                "country_of_residence": "United Kingdom",
                "residential_address": "1 Synthetic Approval Street, London",
                "date_of_birth": "1984-04-12",
                "date_of_appointment": "2024-01-15",
                "is_pep": "No",
            }
        ],
        "ubos": [
            {
                "person_key": "ubo-1",
                "first_name": "Portal",
                "last_name": "Owner",
                "full_name": f"{prefix} UBO {suffix}",
                "nationality": "United Kingdom",
                "country_of_residence": "United Kingdom",
                "residential_address": "2 Synthetic Approval Street, London",
                "date_of_birth": "1981-09-23",
                "ownership_pct": 100,
                "is_pep": "No",
            }
        ],
        "intermediaries": [],
    }


def _safe_slot_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")[:120] or "document"


def _required_document_expectations(db_path: str, app_id: str) -> list[dict]:
    from tests.conftest import _sync_test_db_path
    from db import get_db
    from document_reliance_gate import build_required_document_expectations

    _sync_test_db_path(db_path)
    db = get_db()
    try:
        app = db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
        return build_required_document_expectations(db, dict(app))
    finally:
        db.close()


def _upload_required_documents(base_url: str, token: str, app_id: str, prefix: str, expectations: list[dict]) -> list[str]:
    uploaded = []
    for expectation in expectations:
        params = {"doc_type": expectation["doc_type"]}
        data = {}
        if expectation.get("person_id"):
            params["person_id"] = expectation["person_id"]
            data["person_id"] = expectation["person_id"]
        if expectation.get("person_type"):
            params["person_type"] = expectation["person_type"]
            data["person_type"] = expectation["person_type"]
        filename = f"{prefix}-{_safe_slot_name(expectation['slot_key'])}.pdf"
        response = http_requests.post(
            f"{base_url}/api/applications/{app_id}/documents",
            headers=_headers(token),
            params=params,
            data=data,
            files={"file": (filename, PDF_BYTES, "application/pdf")},
            timeout=10,
        )
        body = _assert_ok(response, 201)
        assert body["doc_type"] == expectation["doc_type"]
        assert body["slot_key"] == expectation["slot_key"]
        uploaded.append(body["id"])
    return uploaded


def _mark_uploaded_documents_verified(db_path: str, app_id: str, officer: dict, request_id: str) -> list[str]:
    from tests.conftest import _sync_test_db_path
    from db import get_db

    _assert_portale2e_fixture_environment()
    _sync_test_db_path(db_path)
    verified_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    verification_results = {
        "overall": "verified",
        "status": "verified",
        "checks": [
            {"id": "PORTALE2E-DOC-STRUCTURE", "result": "pass"},
            {"id": "PORTALE2E-DOC-CONTENT", "result": "pass"},
        ],
        "verified_at": verified_at,
        "source": "PORTALE2E non-production document verification fixture",
    }
    db = get_db()
    try:
        docs = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM documents WHERE application_id=? AND COALESCE(is_current, 1)=1",
                (app_id,),
            ).fetchall()
        ]
        assert docs, "portal upload should create current document rows"
        for doc in docs:
            _update_columns(
                db,
                "documents",
                doc["id"],
                {
                    "verification_status": "verified",
                    "verification_results": json.dumps(verification_results, sort_keys=True),
                    "verified_at": verified_at,
                    "review_status": "accepted",
                    "review_comment": "PORTALE2E non-production fixture accepted after portal upload",
                    "reviewed_by": officer["id"],
                    "reviewer_role": officer["role"],
                    "reviewed_at": verified_at,
                },
            )
            db.execute(
                """
                INSERT INTO agent_executions
                    (application_id, document_id, agent_name, agent_number, status, checks_json, requires_review)
                VALUES (?, ?, 'verify_document', 1, 'verified', ?, 0)
                """,
                (app_id, doc["id"], json.dumps(verification_results["checks"], sort_keys=True)),
            )
        app = db.execute("SELECT ref FROM applications WHERE id=?", (app_id,)).fetchone()
        _insert_audit(
            db,
            user_id=officer["id"],
            user_name=officer["name"],
            user_role=officer["role"],
            action="PORTALE2E Document Verification Fixture",
            target=app["ref"],
            application_id=app_id,
            detail={
                "event": "synthetic_document_verification_clearance",
                "application_id": app_id,
                "document_ids": [doc["id"] for doc in docs],
                "limitation": "Agent/provider document callbacks are not deterministic in CI; portal upload and approval gates are real.",
            },
            request_id=request_id,
            after_state={"document_ids": [doc["id"] for doc in docs], "verification_status": "verified"},
        )
        db.commit()
        return [doc["id"] for doc in docs]
    finally:
        db.close()


def _provider_reference(app_id: str, label: str) -> dict:
    safe_label = _safe_slot_name(label).lower()
    return {
        "case_id": f"ca-{app_id}-{safe_label}",
        "customer_id": f"cust-{app_id}-{safe_label}",
        "workflow_id": f"wf-{app_id}-{safe_label}",
    }


def _clean_screening_subject(app_id: str, label: str, now_iso: str, valid_until_iso: str) -> dict:
    return {
        "provider": "complyadvantage",
        "source": "complyadvantage",
        "api_status": "live",
        "matched": False,
        "results": [],
        "screened_at": now_iso,
        "screening_valid_until": valid_until_iso,
        "evidence_quality": "complete",
        "provider_references": _provider_reference(app_id, label),
    }


def _clear_provider_gates(db_path: str, app_id: str, officer: dict, request_id: str) -> dict:
    from tests.conftest import _sync_test_db_path
    from db import get_db

    _assert_portale2e_fixture_environment()
    _sync_test_db_path(db_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    valid_until_iso = (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    db = get_db()
    try:
        app = dict(db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())
        client = dict(db.execute("SELECT * FROM clients WHERE id=?", (app["client_id"],)).fetchone())
        directors = [dict(row) for row in db.execute("SELECT * FROM directors WHERE application_id=?", (app_id,)).fetchall()]
        ubos = [dict(row) for row in db.execute("SELECT * FROM ubos WHERE application_id=?", (app_id,)).fetchall()]
        prescreening = json.loads(app.get("prescreening_data") or "{}")
        report = {
            "provider": "complyadvantage",
            "screening_provider": "complyadvantage",
            "screening_mode": "live",
            "screened_at": now_iso,
            "screening_valid_until": valid_until_iso,
            "company_screening": {
                **_clean_screening_subject(app_id, "company", now_iso, valid_until_iso),
                "company_name": app["company_name"],
            },
            "director_screenings": [
                {
                    "person_id": row.get("person_key") or row["id"],
                    "person_name": row["full_name"],
                    "screening": _clean_screening_subject(app_id, f"director-{idx}", now_iso, valid_until_iso),
                }
                for idx, row in enumerate(directors, start=1)
            ],
            "ubo_screenings": [
                {
                    "person_id": row.get("person_key") or row["id"],
                    "person_name": row["full_name"],
                    "screening": _clean_screening_subject(app_id, f"ubo-{idx}", now_iso, valid_until_iso),
                }
                for idx, row in enumerate(ubos, start=1)
            ],
            "ip_geolocation": {"source": "ipapi", "api_status": "live", "risk_level": "LOW"},
        }
        prescreening.update(
            {
                "screening_report": report,
                "screening_valid_until": valid_until_iso,
                "screening_validity_days": 90,
                "last_screened_at": now_iso,
                "screening_input_updated_at": now_iso,
            }
        )
        _update_columns(
            db,
            "applications",
            app_id,
            {
                "prescreening_data": json.dumps(prescreening, sort_keys=True),
                "screening_mode": "live",
                "screening_input_updated_at": now_iso,
                "risk_inputs_updated_at": now_iso,
                "updated_at": now_iso,
            },
        )
        try:
            db.execute(
                """
                UPDATE screening_jobs
                   SET status='succeeded', attempt_count=1, completed_at=?, updated_at=?,
                       job_metadata=?
                 WHERE application_id=?
                """,
                (
                    now_iso,
                    now_iso,
                    json.dumps({"PORTALE2E": "provider clearance fixture", "provider": "complyadvantage"}, sort_keys=True),
                    app_id,
                ),
            )
        except Exception:
            pass

        subjects = [
            {
                "person_id": client["id"],
                "person_type": "client",
                "person_name": client.get("company_name") or client["email"],
            }
        ]
        subjects.extend(
            {
                "person_id": row.get("id") or row.get("person_key"),
                "person_type": "director",
                "person_name": row["full_name"],
            }
            for row in directors
        )
        subjects.extend(
            {
                "person_id": row.get("id") or row.get("person_key"),
                "person_type": "ubo",
                "person_name": row["full_name"],
            }
            for row in ubos
        )
        for subject in subjects:
            db.execute(
                """
                INSERT INTO idv_resolutions (
                    id, application_id, application_ref, person_id, person_type, person_name,
                    prior_provider_status, prior_review_answer, resolution_status, resolution_outcome,
                    reason_code, evidence_reviewed, rationale, confirmation_text, senior_approver_id,
                    resolved_by, resolved_by_name, resolved_by_role, ip_address, user_agent, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex[:16],
                    app_id,
                    app["ref"],
                    subject["person_id"],
                    subject["person_type"],
                    subject["person_name"],
                    "not_started",
                    "unavailable",
                    "manual_verified",
                    "manual_verification_completed",
                    "provider_coverage_limitation",
                    json.dumps(["passport", "proof_of_address"]),
                    "PORTALE2E non-production fixture: external Sumsub sandbox callbacks are nondeterministic in CI.",
                    "Officer confirmed synthetic provider fixture review and responsibility",
                    "",
                    officer["id"],
                    officer["name"],
                    officer["role"],
                    "127.0.0.1",
                    "pytest",
                    now_iso,
                ),
            )
        _insert_audit(
            db,
            user_id=officer["id"],
            user_name=officer["name"],
            user_role=officer["role"],
            action="PORTALE2E Provider Clearance Fixture",
            target=app["ref"],
            application_id=app_id,
            detail={
                "event": "synthetic_provider_clearance",
                "application_id": app_id,
                "screening_provider": "complyadvantage",
                "idv_provider": "sumsub",
                "subjects": subjects,
                "limitation": "Provider clearances use a non-production fixture because external sandbox callbacks are not deterministic; final approval endpoint and audit trail remain real.",
            },
            request_id=request_id,
            after_state={"screening": "live_terminal_clear", "idv": "manual_verified"},
        )
        db.commit()
        return {"screened_at": now_iso, "subject_count": len(subjects)}
    finally:
        db.close()


def _insert_optional_direct_route_memo(db_path: str, app_id: str, officer: dict, request_id: str) -> None:
    """Record optional memo evidence without making it an approval shortcut."""

    from tests.conftest import _sync_test_db_path
    from db import get_db

    _assert_portale2e_fixture_environment()
    _sync_test_db_path(db_path)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    memo = {
        "metadata": {
            "ai_source": "deterministic_backend_fixture",
            "purpose": "PORTALE2E direct-route memo evidence; not required by approval route",
        },
        "sections": {
            "summary": {"content": "Synthetic LOW/MEDIUM direct-route memo evidence."},
        },
        "supervisor": {"verdict": "CONSISTENT", "recommendation": "Proceed"},
        "ai_source": "deterministic_backend_fixture",
    }
    db = get_db()
    try:
        db.execute(
            """
            INSERT INTO compliance_memos (
                application_id, version, memo_data, generated_by, ai_recommendation,
                review_status, reviewed_by, review_notes, quality_score,
                validation_status, validation_run_at, approved_by, approved_at,
                supervisor_status, supervisor_summary, blocked, is_stale
            ) VALUES (?, 1, ?, ?, 'APPROVE', 'approved', ?, ?, 9.0,
                      'pass', ?, ?, ?, 'CONSISTENT', ?, 0, 0)
            """,
            (
                app_id,
                json.dumps(memo, sort_keys=True),
                officer["id"],
                officer["id"],
                "PORTALE2E optional memo approved for evidence only; direct route does not require memo.",
                now_iso,
                officer["id"],
                now_iso,
                "No supervisor contradictions in synthetic direct-route evidence.",
            ),
        )
        app = db.execute("SELECT ref FROM applications WHERE id=?", (app_id,)).fetchone()
        _insert_audit(
            db,
            user_id=officer["id"],
            user_name=officer["name"],
            user_role=officer["role"],
            action="PORTALE2E Optional Memo Evidence",
            target=app["ref"],
            application_id=app_id,
            detail={
                "event": "synthetic_optional_memo_evidence",
                "route_note": "direct_low_medium does not require compliance package; memo evidence is retained for audit context only.",
            },
            request_id=request_id,
        )
        db.commit()
    finally:
        db.close()


def _decision_record_count(base_url: str, token: str, app_id: str) -> int:
    records = _get_json(base_url, f"/api/applications/{app_id}/decision-records", token)["records"]
    return len(records)


def _audit_rows_by_action(db_path: str, target: str, action_prefix: str) -> list[dict]:
    from tests.conftest import _sync_test_db_path
    from db import get_db

    _sync_test_db_path(db_path)
    db = get_db()
    try:
        return [
            dict(row)
            for row in db.execute(
                """
                SELECT * FROM audit_log
                WHERE target=? AND action LIKE ?
                ORDER BY timestamp DESC, id DESC
                """,
                (target, f"{action_prefix}%"),
            ).fetchall()
        ]
    finally:
        db.close()


def _create_submitted_portal_application(base_url: str, db_path: str, suffix: str, prefix: str) -> dict:
    client_email = f"portale2e-client-{suffix}@example.test"
    company_name = f"{prefix} Client Shell {suffix}"
    register = _post_json(
        base_url,
        "/api/auth/client/register",
        None,
        {"email": client_email, "password": CLIENT_PASSWORD, "company_name": company_name},
        expected=201,
    )
    login = _post_json(
        base_url,
        "/api/auth/client/login",
        None,
        {"email": client_email, "password": CLIENT_PASSWORD},
    )
    assert login["client"]["id"] == register["client"]["id"]
    client_token = login["token"]

    created = _post_json(
        base_url,
        "/api/applications",
        client_token,
        _application_payload(prefix, suffix),
        expected=201,
    )
    app_id = created["id"]
    submitted = _post_json(base_url, f"/api/applications/{app_id}/submit", client_token, {})
    assert submitted["status"] == "pricing_review"
    assert submitted["risk_level"] in {"LOW", "MEDIUM"}

    pricing = _post_json(base_url, f"/api/applications/{app_id}/accept-pricing", client_token, {})
    assert pricing["status"] == "kyc_documents"

    expectations = _required_document_expectations(db_path, app_id)
    assert {item["doc_type"] for item in expectations} >= {
        "cert_inc",
        "memarts",
        "reg_sh",
        "reg_dir",
        "fin_stmt",
        "poa",
        "board_res",
        "structure_chart",
        "passport",
    }
    uploaded_ids = _upload_required_documents(base_url, client_token, app_id, prefix, expectations)

    portal_list = _get_json(base_url, "/api/portal/applications", client_token)
    listed = [app for app in portal_list["applications"] if app["id"] == app_id]
    assert listed and listed[0]["status"] == "kyc_documents"
    return {
        "client_token": client_token,
        "client_id": login["client"]["id"],
        "application_id": app_id,
        "application_ref": created["ref"],
        "uploaded_document_ids": uploaded_ids,
    }


def _visible_in_backoffice(base_url: str, token: str, app_ref: str) -> dict:
    listing = _get_json(
        base_url,
        f"/api/applications?q={app_ref}&view=list&include_fixtures=1",
        token,
    )
    matches = [app for app in listing["applications"] if app["ref"] == app_ref]
    assert matches, listing
    return matches[0]


def _approval_payload(reason: str = "PORTALE2E clean synthetic approval path after zero blockers.") -> dict:
    return {
        "decision": "approve",
        "decision_reason": reason,
        "override_ai": False,
        "officer_signoff": {
            "acknowledged": True,
            "scope": "decision",
            "source_context": "ai_advisory",
        },
    }


def test_approval_checklist_is_documented_from_current_code():
    assert APPROVAL_CHECKLIST == [
        "decisionable_status",
        "risk_current_and_integrity_checked",
        "direct_low_medium_route_or_compliance_package_required",
        "required_documents_verified_with_agent1_evidence",
        "screening_live_terminal_clear_and_fresh",
        "idv_verified_or_manual_resolution",
        "memo_supervisor_package_when_route_requires_it",
        "no_open_rmi_enhanced_edd_sar_or_change_management_blockers",
        "authorized_actor_and_signoff_ownership",
        "real_decision_endpoint_writes_audit_and_decision_record",
    ]


def test_portal_to_backoffice_clean_approval_path_and_blocked_control(portal_approval_server):
    base_url = portal_approval_server["base_url"]
    db_path = portal_approval_server["db_path"]
    suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
    prefix = f"PORTALE2E-APPROVAL-{suffix}"
    request_id = f"{prefix}-decision"

    officer = _seed_backoffice_user(db_path, suffix)
    officer_login = _post_json(
        base_url,
        "/api/auth/officer/login",
        None,
        {"email": officer["email"], "password": OFFICER_PASSWORD},
    )
    assert officer_login["user"]["id"] == officer["id"]
    officer_token = officer_login["token"]

    clean = _create_submitted_portal_application(base_url, db_path, suffix, prefix)
    app_id = clean["application_id"]
    app_ref = clean["application_ref"]
    assert clean["client_id"]
    assert clean["uploaded_document_ids"]

    list_row = _visible_in_backoffice(base_url, officer_token, app_ref)
    assert list_row["id"] == app_id
    assert list_row["status"] == "kyc_documents"

    blocked_detail = _get_json(base_url, f"/api/applications/{app_id}?include_history=true", officer_token)
    assert blocked_detail["company_name"].startswith(prefix)
    assert blocked_detail["gate_blocker_count"] > 0
    blocked_ids = {
        blocker.get("id") or blocker.get("blocker_id") or blocker.get("key")
        for blocker in blocked_detail["gate_blockers"]
    }
    assert "case_stage" in blocked_ids or any(
        blocker.get("category") == "Case Stage" for blocker in blocked_detail["gate_blockers"]
    )
    assert any(
        str(blocker.get("id") or blocker.get("blocker_id") or blocker.get("key") or "").startswith("idv_")
        or blocker.get("category") == "Identity Verification"
        for blocker in blocked_detail["gate_blockers"]
    )

    verified_ids = _mark_uploaded_documents_verified(db_path, app_id, officer, f"{prefix}-doc-fixture")
    assert set(verified_ids) >= set(clean["uploaded_document_ids"])

    kyc_submit = _post_json(base_url, f"/api/applications/{app_id}/submit-kyc", clean["client_token"], {})
    assert kyc_submit["status"] == "kyc_submitted"
    assert kyc_submit["document_evidence_gate"]["passed"] is True
    assert kyc_submit["documents_uploaded"] == len(verified_ids)

    _clear_provider_gates(db_path, app_id, officer, f"{prefix}-provider-fixture")
    _insert_optional_direct_route_memo(db_path, app_id, officer, f"{prefix}-memo-evidence")

    ready_detail = _get_json(base_url, f"/api/applications/{app_id}?include_history=true", officer_token)
    assert ready_detail["status"] == "kyc_submitted"
    assert ready_detail["approval_route"]["route"] == "direct_low_medium"
    assert ready_detail["approval_route"]["requires_compliance_package"] is False
    assert ready_detail["risk_level"] in {"LOW", "MEDIUM"}
    assert ready_detail["screening_truth_summary"]["approval_ready"] is True
    assert ready_detail["screening_adverse_truth_summary"]["approval_effect"] == "allow_direct_approval"
    assert ready_detail["idv_gate_summary"]["approval_ready"] is True
    assert ready_detail["document_evidence_gate"]["passed"] is True
    assert ready_detail["latest_memo"]["review_status"] == "approved"
    assert ready_detail["latest_memo"]["validation_status"] == "pass"
    assert ready_detail["gate_blocker_count"] == 0, ready_detail["gate_blockers"]
    assert ready_detail["approval_gate_presentation"]["current_gate_blocker_count"] == 0

    decision_before_count = _decision_record_count(base_url, officer_token, app_id)
    decision = _assert_ok(
        http_requests.post(
            f"{base_url}/api/applications/{app_id}/decision",
            headers=_headers(officer_token, request_id=request_id),
            json=_approval_payload(),
            timeout=10,
        ),
        201,
    )
    assert decision["application_status"] == "approved"
    assert decision["approval_gate_snapshot"]["blocker_count"] == 0
    assert decision["approval_gate_snapshot"]["approval_route"]["route"] == "direct_low_medium"
    assert decision["approval_gate_snapshot"]["document_evidence_gate"]["passed"] is True

    approved_list_row = _visible_in_backoffice(base_url, officer_token, app_ref)
    assert approved_list_row["status"] == "approved"
    approved_detail = _get_json(base_url, f"/api/applications/{app_id}?include_history=true", officer_token)
    assert approved_detail["status"] == "approved"
    assert approved_detail["decision_by"] == officer["id"]

    portal_final = _get_json(base_url, "/api/portal/applications", clean["client_token"])
    portal_matches = [app for app in portal_final["applications"] if app["id"] == app_id]
    assert portal_matches and portal_matches[0]["status"] == "approved"

    decision_records = _get_json(base_url, f"/api/applications/{app_id}/decision-records", officer_token)
    assert decision_records["count"] == decision_before_count + 1
    approve_records = [row for row in decision_records["records"] if row["decision_type"] == "approve"]
    assert len(approve_records) == 1
    approval_record = approve_records[0]
    assert approval_record["actor"]["user_id"] == officer["id"]
    assert approval_record["actor"]["role"] == "admin"
    approval_extra = approval_record["extra"]
    assert approval_extra["approval_gate_snapshot"]["blocker_count"] == 0
    assert approval_extra["approval_gate_snapshot"]["approval_route"]["route"] == "direct_low_medium"

    audit = _get_json(base_url, f"/api/applications/{app_id}/audit-log?limit=200", officer_token)
    audit_actions = {entry["action"] for entry in audit["entries"]}
    assert "Decision" in audit_actions
    assert any(
        entry["action"] == "Governance Attempt" and "application.decision" in str(entry.get("detail", ""))
        for entry in audit["entries"]
    )
    assert "PORTALE2E Provider Clearance Fixture" in audit_actions
    assert "PORTALE2E Document Verification Fixture" in audit_actions
    decision_audit = next(entry for entry in audit["entries"] if entry["action"] == "Decision")
    assert decision_audit["user_id"] == officer["id"]
    assert decision_audit["user_role"] == "admin"
    assert decision_audit["application_id"] in (None, "", app_id)
    assert "Decision: approve" in decision_audit["detail"]
    assert "request_id" in decision_audit
    assert all(entry.get("target") in {app_ref, "System", app_id} or app_ref in str(entry.get("detail", "")) for entry in audit["entries"])
    signoff_rows = _audit_rows_by_action(db_path, app_ref, "Officer Sign-Off")
    assert len(signoff_rows) == 1
    assert signoff_rows[0]["user_id"] == officer["id"]
    assert signoff_rows[0]["user_role"] == "admin"
    assert signoff_rows[0]["timestamp"]
    signoff_detail = json.loads(signoff_rows[0]["detail"])
    assert signoff_detail["signoff_acknowledged"] is True
    assert signoff_detail["signoff_scope"] == "decision"

    replay = http_requests.post(
        f"{base_url}/api/applications/{app_id}/decision",
        headers=_headers(officer_token, request_id=f"{request_id}-replay"),
        json=_approval_payload("PORTALE2E replay should be blocked."),
        timeout=10,
    )
    assert replay.status_code == 409, replay.text
    assert _decision_record_count(base_url, officer_token, app_id) == decision_records["count"]

    negative_suffix = suffix + "-blocked"
    negative_prefix = f"PORTALE2E-APPROVAL-{negative_suffix}"
    blocked = _create_submitted_portal_application(base_url, db_path, negative_suffix, negative_prefix)
    negative_detail = _get_json(base_url, f"/api/applications/{blocked['application_id']}", officer_token)
    assert negative_detail["status"] == "kyc_documents"
    assert negative_detail["gate_blocker_count"] > 0
    negative_before = _decision_record_count(base_url, officer_token, blocked["application_id"])
    rejected_approval = http_requests.post(
        f"{base_url}/api/applications/{blocked['application_id']}/decision",
        headers=_headers(officer_token, request_id=f"{negative_prefix}-blocked-approval"),
        json=_approval_payload("PORTALE2E negative control must remain blocked."),
        timeout=10,
    )
    assert rejected_approval.status_code in {400, 409}, rejected_approval.text
    negative_after = _get_json(base_url, f"/api/applications/{blocked['application_id']}", officer_token)
    assert negative_after["status"] == "kyc_documents"
    assert negative_after["status"] != "approved"
    assert _decision_record_count(base_url, officer_token, blocked["application_id"]) == negative_before

    clean_audit_after_negative = _get_json(base_url, f"/api/applications/{app_id}/audit-log?limit=300", officer_token)
    assert all(blocked["application_ref"] not in str(entry) for entry in clean_audit_after_negative["entries"])
