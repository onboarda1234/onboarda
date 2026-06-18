#!/usr/bin/env python3
"""Run PR-PRS-B staging smoke from inside the ECS backend task.

This helper is evidence-only. It is intended to be compressed and executed via
ECS Exec inside the deployed staging backend container, where the private RDS
database and localhost Tornado service are available. It seeds fixture-marked
synthetic rows only, then exercises deployed HTTP handlers for the actual
periodic-review evidence gates.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import uuid
from datetime import date, datetime, timezone
from typing import Any

import requests

from auth import create_token
from db import get_db


BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
PREFIX = os.environ.get("SMOKE_PREFIX") or f"PRPRSB-STAGING-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
CO_TOKEN = create_token("co001", "co", "Marie Dubois", "officer")
SCO_TOKEN = create_token("sco001", "sco", "Raj Patel", "officer")

PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
    b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 420 180] "
    b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
    b"4 0 obj << /Length 154 >> stream\n"
    b"BT /F1 11 Tf 24 140 Td (Updated Register of Directors) Tj "
    b"0 -18 Td (PR-PRS-B staging smoke evidence) Tj "
    b"0 -18 Td (Director: Marie Dubois. Status: current.) Tj ET\n"
    b"endstream endobj\n"
    b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
    b"trailer << /Root 1 0 R >>\n%%EOF\n"
)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _date(value: str) -> str:
    return date.fromisoformat(value).isoformat()


def wait_for_server() -> None:
    deadline = time.time() + 45
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            resp = requests.get(f"{BASE_URL}/api/health", timeout=3)
            if resp.status_code < 500:
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(1)
    raise RuntimeError(f"server did not become ready: {last_error}")


def table_columns(db, table: str) -> set[str]:
    if getattr(db, "is_postgres", False):
        rows = db.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = ?
            """,
            (table,),
        ).fetchall()
        return {row["column_name"] for row in rows}
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def insert_filtered(db, table: str, values: dict[str, Any], *, returning: str = "id") -> Any:
    cols = [col for col in values if col in table_columns(db, table)]
    all_cols = table_columns(db, table)
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    params = [values[col] for col in cols]
    if getattr(db, "is_postgres", False) and returning in all_cols:
        return db.execute(f"{sql} RETURNING {returning}", tuple(params)).fetchone()[returning]
    db.execute(sql, tuple(params))
    if returning in cols:
        row = db.execute(f"SELECT {returning} FROM {table} ORDER BY {returning} DESC LIMIT 1").fetchone()
        return row[returning] if row else None
    return None


def app_id(name: str) -> str:
    raw = f"{PREFIX.lower()}-{name}"
    return re.sub(r"[^a-z0-9_-]+", "-", raw)[:64]


def insert_app(db, key: str, *, company: str, risk: str = "HIGH", status: str = "approved") -> str:
    aid = app_id(key)
    prescreening = json.dumps({"screening_report": {"screened_at": iso_now(), "result": "clear"}})
    insert_filtered(
        db,
        "applications",
        {
            "id": aid,
            "ref": f"{PREFIX}-{key}".upper()[:64],
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
            "approved_at": _date("2025-01-01"),
            "first_approved_at": _date("2025-01-01"),
            "decided_at": _date("2025-01-01"),
            "prescreening_data": prescreening,
            "is_fixture": True,
        },
    )
    db.commit()
    return aid


def insert_review(db, application_id: str, *, company: str, status: str = "in_progress") -> int:
    rid = insert_filtered(
        db,
        "periodic_reviews",
        {
            "application_id": application_id,
            "client_name": company,
            "risk_level": "HIGH",
            "status": status,
            "trigger_type": "time_based",
            "trigger_source": "schedule",
            "review_reason": "PR-PRS-B staging smoke review",
            "due_date": _date("2026-01-01"),
            "next_review_date": _date("2026-01-01"),
            "review_cycle_number": 1,
            "review_type": "scheduled",
            "policy_version": "periodic_review_policy_v1",
            "frequency_months": 12,
            "calculation_basis": "risk_based_anniversary",
            "client_attestation_status": "submitted",
            "client_attestation_submitted_at": iso_now(),
            "baseline_status": "not_applicable",
            "officer_rationale": "PR-PRS-B staging smoke completion rationale.",
            "priority": "high",
            "required_items": "[]",
        },
    )
    db.commit()
    return int(rid)


def insert_requirement(
    db,
    application_id: str,
    review_id: int | None,
    *,
    key: str,
    label: str,
    status: str = "under_review",
    linked_document_id: str | None = None,
) -> int:
    rid = insert_filtered(
        db,
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
            "generation_source": "pr_prs_b_staging_smoke",
            "trigger_reason": "PR-PRS-B staging smoke",
            "trigger_context": json.dumps({"smoke": True}),
            "linked_document_id": linked_document_id,
            "active": 1,
            "created_by": "sco001",
            "updated_by": "sco001",
            "linked_periodic_review_id": review_id,
        },
    )
    db.commit()
    return int(rid)


def insert_doc(
    db,
    application_id: str,
    *,
    doc_type: str,
    name: str,
    verification_status: str,
    review_status: str = "pending",
    reviewer_role: str = "",
    review_comment: str = "",
    is_current: bool = True,
) -> str:
    doc_id = uuid.uuid4().hex[:16]
    results = {
        "overall": verification_status,
        "checks": [{"label": "Seeded staging smoke check", "result": "pass"}] if verification_status == "verified" else [],
        "source": "pr_prs_b_staging_smoke_seed",
    }
    insert_filtered(
        db,
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
            "uploaded_by": "sco001",
            "uploaded_by_actor_type": "user",
            "uploaded_by_actor_id": "sco001",
            "uploaded_by_display": "PR-PRS-B staging smoke",
            "upload_source": "staging_smoke_seed",
        },
    )
    db.commit()
    return doc_id


def fetch_doc(db, doc_id: str) -> dict[str, Any]:
    row = db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not row:
        raise AssertionError(f"missing doc {doc_id}")
    out = dict(row)
    if isinstance(out.get("verification_results"), str):
        out["verification_results"] = json.loads(out["verification_results"])
    return out


def api(method: str, path: str, *, token: str = SCO_TOKEN, json_body: dict[str, Any] | None = None):
    resp = requests.request(
        method,
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        json=json_body,
        timeout=90,
    )
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        payload = {"raw": resp.text[:500]}
    return resp.status_code, payload


def upload(path: str, *, token: str = SCO_TOKEN):
    resp = requests.post(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        files={"file": ("updated-register-of-directors.pdf", PDF_BYTES, "application/pdf")},
        timeout=90,
    )
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        payload = {"raw": resp.text[:500]}
    return resp.status_code, payload


def complete(review_id: int, *, token: str = CO_TOKEN):
    return api(
        "POST",
        f"/api/monitoring/reviews/{review_id}/complete",
        token=token,
        json_body={
            "outcome": "no_change",
            "rationale": "PR-PRS-B staging smoke outcome rationale.",
            "officer_acknowledgement": True,
        },
    )


def patch_requirement(app: str, req_id: int, payload: dict[str, Any], *, token: str):
    return api(
        "PATCH",
        f"/api/applications/{urllib.parse.quote(app)}/enhanced-requirements/{req_id}",
        token=token,
        json_body=payload,
    )


def review_document(doc_id: str, payload: dict[str, Any], *, token: str):
    return api("POST", f"/api/documents/{doc_id}/review", token=token, json_body=payload)


def scenario_agent1_runs(db) -> dict[str, Any]:
    app = insert_app(db, "agent1", company=f"{PREFIX} Agent One Ltd", status="kyc_documents")
    review = insert_review(db, app, company=f"{PREFIX} Agent One Ltd")
    req = insert_requirement(db, app, review, key="updated_register_of_directors", label="Updated Register of Directors")
    status, uploaded = upload(f"/api/applications/{urllib.parse.quote(app)}/enhanced-requirements/{req}/upload")
    if status != 201:
        return {"passed": False, "upload_status_code": status, "upload_error": uploaded}
    doc_id = uploaded["document"]["id"]
    verify_status, verify = api("POST", f"/api/documents/{doc_id}/verify", json_body={})
    doc = fetch_doc(db, doc_id)
    checks = (doc.get("verification_results") or {}).get("checks") or []
    return {
        "application_id": app,
        "review_id": review,
        "requirement_id": req,
        "document_id": doc_id,
        "upload_status_code": status,
        "upload_verification_status": uploaded["document"].get("verification_status"),
        "upload_agent1": uploaded.get("agent1_verification"),
        "verify_status_code": verify_status,
        "verify_status": verify.get("verification_status"),
        "persisted_status": doc.get("verification_status"),
        "verified_at": str(doc.get("verified_at") or ""),
        "checks_count": len(checks),
        "passed": (
            verify_status == 200
            and doc.get("verification_status") != "skipped"
            and bool(doc.get("verified_at"))
            and len(checks) > 0
            and bool((uploaded.get("agent1_verification") or {}).get("triggered"))
        ),
    }


def scenario_accepted_not_verified_blocks(db) -> dict[str, Any]:
    app = insert_app(db, "co-accepted", company=f"{PREFIX} CO Accepted Ltd")
    review = insert_review(db, app, company=f"{PREFIX} CO Accepted Ltd")
    doc = insert_doc(db, app, doc_type="reg_dir", name="manual-directors.pdf", verification_status="skipped")
    req = insert_requirement(db, app, review, key="updated_register_of_directors", label="Updated Register of Directors", linked_document_id=doc)
    patch_status, patch_payload = patch_requirement(
        app,
        req,
        {"status": "accepted", "review_notes": "Plain officer accepted before verification."},
        token=CO_TOKEN,
    )
    complete_status, complete_payload = complete(review, token=CO_TOKEN)
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


def scenario_verified_satisfies(db) -> dict[str, Any]:
    app = insert_app(db, "verified", company=f"{PREFIX} Verified Ltd")
    review = insert_review(db, app, company=f"{PREFIX} Verified Ltd")
    doc = insert_doc(db, app, doc_type="reg_dir", name="verified-directors.pdf", verification_status="verified")
    req = insert_requirement(db, app, review, key="updated_register_of_directors", label="Updated Register of Directors", status="accepted", linked_document_id=doc)
    status, payload = complete(review, token=CO_TOKEN)
    row = db.execute("SELECT status, outcome FROM periodic_reviews WHERE id = ?", (review,)).fetchone()
    return {
        "application_id": app,
        "review_id": review,
        "requirement_id": req,
        "document_id": doc,
        "completion_status": status,
        "completion_payload_status": payload.get("status"),
        "review_status": dict(row) if row else None,
        "passed": status == 200 and row and row.get("status") == "completed",
    }


def scenario_senior_manual_exception(db) -> dict[str, Any]:
    app = insert_app(db, "senior-manual", company=f"{PREFIX} Senior Manual Ltd")
    review = insert_review(db, app, company=f"{PREFIX} Senior Manual Ltd")
    doc = insert_doc(db, app, doc_type="supporting_document", name="manual-source.pdf", verification_status="skipped")
    req = insert_requirement(db, app, review, key="jurisdiction_rationale", label="Jurisdiction rationale evidence", linked_document_id=doc)
    co_status, co_payload = review_document(doc, {"status": "accepted", "comment": "Plain officer cannot clear."}, token=CO_TOKEN)
    sco_status, sco_payload = review_document(
        doc,
        {"status": "accepted", "comment": "Senior manual acceptance after source-register review."},
        token=SCO_TOKEN,
    )
    patch_status, _patch_payload = patch_requirement(
        app,
        req,
        {"status": "accepted", "review_notes": "Senior manual acceptance recorded."},
        token=SCO_TOKEN,
    )
    complete_status, complete_payload = complete(review, token=SCO_TOKEN)
    row = db.execute("SELECT status, outcome FROM periodic_reviews WHERE id = ?", (review,)).fetchone()
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
        "review_status": dict(row) if row else None,
        "blocking_items": complete_payload.get("blocking_items") or [],
        "passed": co_status == 403 and sco_status == 200 and patch_status == 200 and complete_status == 200 and row and row.get("status") == "completed",
    }


def scenario_stale_reblocks(db) -> dict[str, Any]:
    app = insert_app(db, "stale", company=f"{PREFIX} Stale Ltd")
    review = insert_review(db, app, company=f"{PREFIX} Stale Ltd")
    doc = insert_doc(
        db,
        app,
        doc_type="reg_dir",
        name="old-verified-directors.pdf",
        verification_status="verified",
        review_status="accepted",
        reviewer_role="sco",
        review_comment="Previously accepted.",
    )
    req = insert_requirement(db, app, review, key="updated_register_of_directors", label="Updated Register of Directors", status="accepted", linked_document_id=doc)
    db.execute(
        "UPDATE documents SET is_current = ?, superseded_at = datetime('now'), superseded_by_document_id = NULL WHERE id = ?",
        (False, doc),
    )
    db.commit()
    complete_status, complete_payload = complete(review, token=SCO_TOKEN)
    return {
        "application_id": app,
        "review_id": review,
        "requirement_id": req,
        "document_id": doc,
        "document_is_current": fetch_doc(db, doc).get("is_current"),
        "completion_status": complete_status,
        "blocking_items": complete_payload.get("blocking_items") or [],
        "passed": complete_status == 409 and bool(complete_payload.get("blocking_items")),
    }


def scenario_onboarding_edd_regression(db) -> dict[str, Any]:
    app = insert_app(db, "edd-regression", company=f"{PREFIX} EDD Regression Ltd", status="kyc_documents")
    req = insert_requirement(
        db,
        app,
        None,
        key="licence_or_registration_certificate",
        label="Licence or registration certificate",
    )
    status, uploaded = upload(f"/api/applications/{urllib.parse.quote(app)}/enhanced-requirements/{req}/upload")
    if status != 201:
        return {"passed": False, "upload_status_code": status, "upload_error": uploaded}
    doc_id = uploaded["document"]["id"]
    verify_status, verify = api("POST", f"/api/documents/{doc_id}/verify", json_body={})
    doc = fetch_doc(db, doc_id)
    checks = (doc.get("verification_results") or {}).get("checks") or []
    return {
        "application_id": app,
        "requirement_id": req,
        "document_id": doc_id,
        "upload_status_code": status,
        "upload_doc_type": uploaded["document"].get("doc_type"),
        "upload_status": uploaded["document"].get("verification_status"),
        "upload_agent1": uploaded.get("agent1_verification"),
        "verify_status_code": verify_status,
        "verify_status": verify.get("verification_status"),
        "persisted_status": doc.get("verification_status"),
        "verified_at": str(doc.get("verified_at") or ""),
        "checks_count": len(checks),
        "passed": verify_status == 200 and doc.get("verification_status") != "skipped" and bool(doc.get("verified_at")) and len(checks) > 0,
    }


def main() -> int:
    wait_for_server()
    db = get_db()
    failures: list[str] = []
    results: dict[str, Any] = {
        "base_url": BASE_URL,
        "prefix": PREFIX,
        "ran_at": iso_now(),
        "git_sha": os.environ.get("GIT_SHA"),
        "image_tag": os.environ.get("IMAGE_TAG"),
        "agent1_enabled": None,
        "scenarios": {},
    }
    try:
        row = db.execute("SELECT enabled FROM ai_agents WHERE agent_number = 1").fetchone()
        results["agent1_enabled"] = bool(row and row.get("enabled"))
        scenarios = [
            ("agent1_runs", scenario_agent1_runs),
            ("accepted_not_verified_blocks", scenario_accepted_not_verified_blocks),
            ("verified_satisfies", scenario_verified_satisfies),
            ("senior_manual_exception", scenario_senior_manual_exception),
            ("stale_reblocks", scenario_stale_reblocks),
            ("onboarding_edd_regression", scenario_onboarding_edd_regression),
        ]
        for name, fn in scenarios:
            try:
                result = fn(db)
                results["scenarios"][name] = result
                if not result.get("passed"):
                    failures.append(name)
            except Exception as exc:  # noqa: BLE001
                results["scenarios"][name] = {"passed": False, "error": repr(exc)}
                failures.append(name)
        if failures:
            results["failures"] = failures
        print("PR_PRS_B_STAGING_SMOKE_JSON_START")
        print(json.dumps(results, indent=2, sort_keys=True, default=str))
        print("PR_PRS_B_STAGING_SMOKE_JSON_END")
        return 1 if failures else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
