#!/usr/bin/env python3
"""PR-PRS-B local/staging-compatible API smoke helper.

The runner seeds synthetic periodic-review cases into the configured local DB
and exercises the live Tornado HTTP handlers for login, upload, verification,
document review, enhanced-requirement acceptance, and periodic-review
completion. It writes a JSON result file that browser smoke can render for
screenshot evidence.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:10000").rstrip("/")
DB_PATH = os.environ["SMOKE_DB_PATH"]
PASSWORD = os.environ["SMOKE_PASSWORD"]
EVIDENCE_DIR = Path(os.environ["SMOKE_EVIDENCE_DIR"])
PREFIX = os.environ.get("SMOKE_PREFIX") or f"PRPRSB-{datetime.now(timezone.utc).strftime('%H%M%S')}"
TOKEN: str | None = None
CURRENT_USER: dict[str, Any] | None = None


PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
    b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 120] "
    b"/Contents 4 0 R >> endobj\n"
    b"4 0 obj << /Length 44 >> stream\n"
    b"BT /F1 12 Tf 30 70 Td (PR-PRS-B smoke) Tj ET\n"
    b"endstream endobj\n"
    b"trailer << /Root 1 0 R >>\n%%EOF\n"
)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def insert_filtered(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> Any:
    cols = [col for col in values if col in table_columns(conn, table)]
    placeholders = ",".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
        [values[col] for col in cols],
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def wait_for_server() -> None:
    deadline = time.time() + 60
    last_error = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/api/health", timeout=2) as resp:
                if resp.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"server did not become ready: {last_error}")


def http(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read().decode() or "{}"
            return resp.status, json.loads(payload)
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode() or "{}"
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            parsed = {"raw": payload}
        return exc.code, parsed


def multipart_upload(path: str, *, field_name: str, filename: str, content_type: str, content: bytes) -> tuple[int, dict[str, Any]]:
    boundary = f"----prprsb{uuid.uuid4().hex}"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{filename}"\r\n'
            ).encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    headers = {
        "Accept": "application/json",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(f"{BASE_URL}{path}", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read().decode() or "{}"
            return resp.status, json.loads(payload)
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode() or "{}"
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            parsed = {"raw": payload}
        return exc.code, parsed


def login(email: str) -> dict[str, Any]:
    global TOKEN, CURRENT_USER
    status, payload = http("POST", "/api/auth/officer/login", {"email": email, "password": PASSWORD})
    assert status == 200, (email, status, payload)
    TOKEN = payload["token"]
    CURRENT_USER = payload["user"]
    return CURRENT_USER


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def app_id(name: str) -> str:
    return f"{PREFIX.lower()}-{name}".replace("_", "-")


def insert_app(conn: sqlite3.Connection, key: str, *, company: str, risk: str = "HIGH", status: str = "kyc_documents") -> str:
    aid = app_id(key)
    prescreening = json.dumps({"screening_report": {"screened_at": iso_now(), "result": "clear"}})
    insert_filtered(
        conn,
        "applications",
        {
            "id": aid,
            "ref": f"{PREFIX}-{key}".upper()[:48],
            "company_name": company,
            "country": "Mauritius",
            "sector": "Fintech",
            "entity_type": "Company",
            "ownership_structure": "single-tier",
            "risk_level": risk,
            "final_risk_level": risk,
            "base_risk_level": risk,
            "risk_score": 72 if risk in ("HIGH", "VERY_HIGH") else 48,
            "onboarding_lane": "EDD" if risk in ("HIGH", "VERY_HIGH") else "Standard",
            "status": status,
            "pre_approval_decision": "PRE_APPROVE",
            "approved_at": "2025-01-01",
            "first_approved_at": "2025-01-01",
            "decided_at": "2025-01-01",
            "prescreening_data": prescreening,
            "is_fixture": 0,
        },
    )
    conn.commit()
    return aid


def insert_review(conn: sqlite3.Connection, application_id: str, *, company: str, status: str = "in_progress") -> int:
    rid = insert_filtered(
        conn,
        "periodic_reviews",
        {
            "application_id": application_id,
            "client_name": company,
            "risk_level": "HIGH",
            "status": status,
            "trigger_type": "time_based",
            "trigger_source": "schedule",
            "review_reason": "PR-PRS-B smoke review",
            "due_date": "2026-01-01",
            "next_review_date": "2026-01-01",
            "review_cycle_number": 1,
            "review_type": "scheduled",
            "policy_version": "periodic_review_policy_v1",
            "frequency_months": 12,
            "calculation_basis": "risk_based_anniversary",
            "client_attestation_status": "submitted",
            "client_attestation_submitted_at": iso_now(),
            "baseline_status": "not_applicable",
            "officer_rationale": "PR-PRS-B smoke completion rationale.",
            "priority": "high",
            "required_items": "[]",
        },
    )
    conn.commit()
    return int(rid)


def insert_requirement(
    conn: sqlite3.Connection,
    application_id: str,
    review_id: int | None,
    *,
    key: str,
    label: str,
    status: str = "under_review",
    linked_document_id: str | None = None,
) -> int:
    rid = insert_filtered(
        conn,
        "application_enhanced_requirements",
        {
            "application_id": application_id,
            "trigger_key": "periodic_review_attestation" if review_id else "edd",
            "trigger_label": "Periodic review attestation" if review_id else "EDD enhanced review",
            "trigger_category": "periodic_review_attestation" if review_id else "edd",
            "requirement_key": key,
            "requirement_label": label,
            "requirement_description": f"Smoke requirement for {label}",
            "audience": "both",
            "requirement_type": "document",
            "subject_scope": "company",
            "blocking_approval": 1,
            "waivable": 0,
            "waiver_roles": json.dumps(["admin", "sco"]),
            "mandatory": 1,
            "status": status,
            "generation_source": "pr_prs_b_smoke",
            "trigger_reason": "PR-PRS-B smoke",
            "trigger_context": json.dumps({"smoke": True}),
            "linked_document_id": linked_document_id,
            "active": 1,
            "created_by": "smoke",
            "updated_by": "smoke",
            "linked_periodic_review_id": review_id,
        },
    )
    conn.commit()
    return int(rid)


def insert_doc(
    conn: sqlite3.Connection,
    application_id: str,
    *,
    doc_type: str,
    name: str,
    verification_status: str,
    review_status: str = "pending",
    reviewer_role: str = "",
    review_comment: str = "",
    is_current: int = 1,
) -> str:
    doc_id = uuid.uuid4().hex[:16]
    results = {
        "overall": verification_status,
        "checks": [{"label": "Seeded smoke check", "result": "pass"}] if verification_status == "verified" else [],
        "source": "pr_prs_b_smoke_seed",
    }
    insert_filtered(
        conn,
        "documents",
        {
            "id": doc_id,
            "application_id": application_id,
            "doc_type": doc_type,
            "doc_name": name,
            "file_path": "",
            "file_size": len(PDF_BYTES),
            "mime_type": "application/pdf",
            "file_sha256": uuid.uuid4().hex,
            "slot_key": f"enhanced_requirement:{doc_type}:{uuid.uuid4().hex[:8]}",
            "is_current": is_current,
            "version": 1,
            "verification_status": verification_status,
            "verification_results": json.dumps(results),
            "verified_at": iso_now() if verification_status == "verified" else None,
            "review_status": review_status,
            "review_comment": review_comment,
            "reviewer_role": reviewer_role,
            "reviewed_by": "sco001" if reviewer_role in ("sco", "admin") else ("co001" if reviewer_role == "co" else None),
            "reviewed_at": iso_now() if review_status in ("accepted", "approved") else None,
            "uploaded_by": "backoffice",
            "uploaded_by_actor_type": "user",
            "uploaded_by_actor_id": "smoke",
            "uploaded_by_display": "PR-PRS-B smoke",
            "upload_source": "smoke_seed",
        },
    )
    conn.commit()
    return doc_id


def fetch_doc(conn: sqlite3.Connection, doc_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert row is not None, doc_id
    out = dict(row)
    if out.get("verification_results"):
        out["verification_results"] = json.loads(out["verification_results"])
    return out


def complete(review_id: int, *, as_email: str = "m.dubois@onboarda.com") -> tuple[int, dict[str, Any]]:
    login(as_email)
    return http(
        "POST",
        f"/api/monitoring/reviews/{review_id}/complete",
        {
            "outcome": "no_change",
            "rationale": "PR-PRS-B smoke outcome rationale.",
            "officer_acknowledgement": True,
        },
    )


def patch_requirement(app: str, req_id: int, payload: dict[str, Any], *, email: str) -> tuple[int, dict[str, Any]]:
    login(email)
    return http("PATCH", f"/api/applications/{urllib.parse.quote(app)}/enhanced-requirements/{req_id}", payload)


def review_document(doc_id: str, payload: dict[str, Any], *, email: str) -> tuple[int, dict[str, Any]]:
    login(email)
    return http("POST", f"/api/documents/{doc_id}/review", payload)


def scenario_agent1_runs(conn: sqlite3.Connection) -> dict[str, Any]:
    app = insert_app(conn, "agent1", company="PR-PRS-B Agent One Ltd")
    review = insert_review(conn, app, company="PR-PRS-B Agent One Ltd")
    req = insert_requirement(
        conn,
        app,
        review,
        key="updated_register_of_directors",
        label="Updated Register of Directors",
        status="under_review",
    )
    login("raj.patel@onboarda.com")
    status, upload = multipart_upload(
        f"/api/applications/{urllib.parse.quote(app)}/enhanced-requirements/{req}/upload",
        field_name="file",
        filename="updated-register-of-directors.pdf",
        content_type="application/pdf",
        content=PDF_BYTES,
    )
    assert status == 201, upload
    doc_id = upload["document"]["id"]
    status, verify = http("POST", f"/api/documents/{doc_id}/verify", {})
    assert status == 200, verify
    doc = fetch_doc(conn, doc_id)
    checks = doc.get("verification_results", {}).get("checks") or []
    return {
        "application_id": app,
        "review_id": review,
        "requirement_id": req,
        "document_id": doc_id,
        "upload_status": upload["document"].get("verification_status"),
        "upload_agent1": upload.get("agent1_verification"),
        "verify_status": verify.get("verification_status"),
        "persisted_status": doc.get("verification_status"),
        "verified_at": doc.get("verified_at"),
        "checks_count": len(checks),
        "manual_only": doc.get("verification_status") == "skipped",
        "passed": doc.get("verification_status") != "skipped" and bool(doc.get("verified_at")) and len(checks) > 0,
    }


def scenario_accepted_not_verified_blocks(conn: sqlite3.Connection) -> dict[str, Any]:
    app = insert_app(conn, "co-accepted", company="PR-PRS-B CO Accepted Ltd")
    review = insert_review(conn, app, company="PR-PRS-B CO Accepted Ltd")
    doc = insert_doc(conn, app, doc_type="reg_dir", name="manual-directors.pdf", verification_status="skipped")
    req = insert_requirement(conn, app, review, key="updated_register_of_directors", label="Updated Register of Directors", linked_document_id=doc)
    patch_status, patch_payload = patch_requirement(
        app,
        req,
        {"status": "accepted", "review_notes": "Plain officer accepted before verification."},
        email="m.dubois@onboarda.com",
    )
    complete_status, complete_payload = complete(review, as_email="m.dubois@onboarda.com")
    return {
        "application_id": app,
        "review_id": review,
        "requirement_id": req,
        "document_id": doc,
        "co_accept_status": patch_status,
        "co_accept_payload_status": (patch_payload.get("requirement") or {}).get("status"),
        "completion_status": complete_status,
        "blocking_items": complete_payload.get("blocking_items") or [],
        "passed": patch_status == 200 and complete_status == 409 and bool(complete_payload.get("blocking_items")),
    }


def scenario_verified_satisfies(conn: sqlite3.Connection) -> dict[str, Any]:
    app = insert_app(conn, "verified", company="PR-PRS-B Verified Ltd")
    review = insert_review(conn, app, company="PR-PRS-B Verified Ltd")
    doc = insert_doc(conn, app, doc_type="reg_dir", name="verified-directors.pdf", verification_status="verified")
    req = insert_requirement(conn, app, review, key="updated_register_of_directors", label="Updated Register of Directors", status="accepted", linked_document_id=doc)
    status, payload = complete(review, as_email="m.dubois@onboarda.com")
    row = conn.execute("SELECT status, outcome FROM periodic_reviews WHERE id = ?", (review,)).fetchone()
    return {
        "application_id": app,
        "review_id": review,
        "requirement_id": req,
        "document_id": doc,
        "completion_status": status,
        "review_status": dict(row),
        "passed": status == 200 and row["status"] == "completed",
    }


def scenario_senior_manual_exception(conn: sqlite3.Connection) -> dict[str, Any]:
    app = insert_app(conn, "senior-manual", company="PR-PRS-B Senior Manual Ltd")
    review = insert_review(conn, app, company="PR-PRS-B Senior Manual Ltd")
    doc = insert_doc(conn, app, doc_type="supporting_document", name="manual-source.pdf", verification_status="skipped")
    req = insert_requirement(conn, app, review, key="jurisdiction_rationale", label="Jurisdiction rationale evidence", linked_document_id=doc)
    co_status, co_payload = review_document(doc, {"status": "accepted", "comment": "Plain officer cannot clear."}, email="m.dubois@onboarda.com")
    sco_status, sco_payload = review_document(
        doc,
        {"status": "accepted", "comment": "Senior manual acceptance after source-register review."},
        email="raj.patel@onboarda.com",
    )
    patch_status, _patch_payload = patch_requirement(
        app,
        req,
        {"status": "accepted", "review_notes": "Senior manual acceptance recorded."},
        email="raj.patel@onboarda.com",
    )
    complete_status, complete_payload = complete(review, as_email="raj.patel@onboarda.com")
    row = conn.execute("SELECT status, outcome FROM periodic_reviews WHERE id = ?", (review,)).fetchone()
    return {
        "application_id": app,
        "review_id": review,
        "requirement_id": req,
        "document_id": doc,
        "co_document_accept_status": co_status,
        "co_error": co_payload.get("error"),
        "sco_document_accept_status": sco_status,
        "sco_reviewer_role": sco_payload.get("reviewer_role"),
        "requirement_accept_status": patch_status,
        "completion_status": complete_status,
        "review_status": dict(row),
        "blocking_items": complete_payload.get("blocking_items") or [],
        "passed": co_status == 403 and sco_status == 200 and patch_status == 200 and complete_status == 200 and row["status"] == "completed",
    }


def scenario_stale_reblocks(conn: sqlite3.Connection) -> dict[str, Any]:
    app = insert_app(conn, "stale", company="PR-PRS-B Stale Ltd")
    review = insert_review(conn, app, company="PR-PRS-B Stale Ltd")
    doc = insert_doc(conn, app, doc_type="reg_dir", name="old-verified-directors.pdf", verification_status="verified", review_status="accepted", reviewer_role="sco", review_comment="Previously accepted.")
    req = insert_requirement(conn, app, review, key="updated_register_of_directors", label="Updated Register of Directors", status="accepted", linked_document_id=doc)
    conn.execute(
        "UPDATE documents SET is_current = 0, superseded_at = datetime('now'), superseded_by_document_id = ? WHERE id = ?",
        ("replacement-doc", doc),
    )
    conn.commit()
    complete_status, complete_payload = complete(review, as_email="raj.patel@onboarda.com")
    return {
        "application_id": app,
        "review_id": review,
        "requirement_id": req,
        "document_id": doc,
        "document_is_current": fetch_doc(conn, doc).get("is_current"),
        "completion_status": complete_status,
        "blocking_items": complete_payload.get("blocking_items") or [],
        "passed": complete_status == 409 and bool(complete_payload.get("blocking_items")),
    }


def scenario_onboarding_edd_regression(conn: sqlite3.Connection) -> dict[str, Any]:
    app = insert_app(conn, "edd-regression", company="PR-PRS-B EDD Regression Ltd")
    req = insert_requirement(
        conn,
        app,
        None,
        key="licence_or_registration_certificate",
        label="Licence or registration certificate",
        status="under_review",
    )
    login("raj.patel@onboarda.com")
    status, upload = multipart_upload(
        f"/api/applications/{urllib.parse.quote(app)}/enhanced-requirements/{req}/upload",
        field_name="file",
        filename="licence-or-registration.pdf",
        content_type="application/pdf",
        content=PDF_BYTES,
    )
    assert status == 201, upload
    doc_id = upload["document"]["id"]
    status, verify = http("POST", f"/api/documents/{doc_id}/verify", {})
    assert status == 200, verify
    doc = fetch_doc(conn, doc_id)
    checks = doc.get("verification_results", {}).get("checks") or []
    return {
        "application_id": app,
        "requirement_id": req,
        "document_id": doc_id,
        "upload_doc_type": upload["document"].get("doc_type"),
        "upload_status": upload["document"].get("verification_status"),
        "upload_agent1": upload.get("agent1_verification"),
        "verify_status": verify.get("verification_status"),
        "persisted_status": doc.get("verification_status"),
        "verified_at": doc.get("verified_at"),
        "checks_count": len(checks),
        "passed": doc.get("verification_status") != "skipped" and bool(doc.get("verified_at")) and len(checks) > 0,
    }


def main() -> int:
    wait_for_server()
    conn = connect()
    conn.execute("UPDATE ai_agents SET enabled = 1 WHERE agent_number = 1")
    conn.commit()
    results = {
        "base_url": BASE_URL,
        "prefix": PREFIX,
        "ran_at": iso_now(),
        "scenarios": {},
    }
    scenarios = [
        ("agent1_runs", scenario_agent1_runs),
        ("accepted_not_verified_blocks", scenario_accepted_not_verified_blocks),
        ("verified_satisfies", scenario_verified_satisfies),
        ("senior_manual_exception", scenario_senior_manual_exception),
        ("stale_reblocks", scenario_stale_reblocks),
        ("onboarding_edd_regression", scenario_onboarding_edd_regression),
    ]
    failures = []
    for name, fn in scenarios:
        try:
            result = fn(conn)
            results["scenarios"][name] = result
            if not result.get("passed"):
                failures.append(name)
        except Exception as exc:  # noqa: BLE001
            results["scenarios"][name] = {"passed": False, "error": repr(exc)}
            failures.append(name)
    output = EVIDENCE_DIR / "logs" / "api_smoke_results.json"
    output.write_text(json.dumps(results, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")
    print(json.dumps(results, indent=2, sort_keys=True, default=_json_default))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
