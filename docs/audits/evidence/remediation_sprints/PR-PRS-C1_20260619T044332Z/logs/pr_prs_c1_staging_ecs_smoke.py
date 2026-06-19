#!/usr/bin/env python3
"""Run PR-PRS-C1 staging smoke from inside the ECS backend task.

Evidence-only helper. It seeds fixture-marked synthetic applications/reviews,
exercises the deployed Tornado HTTP handlers on localhost, and verifies the
canonical-risk/audit/database outcomes against the staging database.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timezone
from typing import Any

import requests

from auth import create_token
from db import get_db


BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
PUBLIC_BASE_URL = os.environ.get("SMOKE_PUBLIC_BASE_URL", "https://staging.regmind.co").rstrip("/")
EXPECTED_SHA = os.environ.get("EXPECTED_SHA", "dd162525aa07c64660f70ca8336c3834ebdfb898")
PREFIX = os.environ.get("SMOKE_PREFIX") or f"PRPRSC1-STAGING-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
SCO_TOKEN = create_token("sco001", "sco", "Raj Patel", "officer")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def day(value: str) -> str:
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
    all_cols = table_columns(db, table)
    cols = [col for col in values if col in all_cols]
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


def normalize(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)) or value is None:
        return normalize(value)
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                return normalize(json.loads(text))
            except json.JSONDecodeError:
                return value
    return normalize(value)


def app_id(name: str) -> str:
    raw = f"{PREFIX.lower()}-{name}"
    return re.sub(r"[^a-z0-9_-]+", "-", raw)[:64]


def insert_app(
    db,
    key: str,
    *,
    company: str,
    risk: str,
    score: int,
    approved_on: str = "2025-01-01",
) -> str:
    aid = app_id(key)
    prescreening = {
        "screening_report": {"screened_at": iso_now(), "result": "clear"},
        "countries_of_operation": ["Mauritius"],
        "target_markets": ["Mauritius"],
        "services_required": ["Corporate treasury"],
        "monthly_volume": "10000",
        "source_of_funds": "Trading revenue",
        "source_of_wealth": "Operating income",
        "cross_border": False,
    }
    insert_filtered(
        db,
        "applications",
        {
            "id": aid,
            "ref": f"{PREFIX}-{key}".upper()[:64],
            "company_name": company,
            "country": "Mauritius",
            "sector": "Professional services",
            "entity_type": "Company",
            "ownership_structure": "single-tier",
            "risk_level": risk,
            "final_risk_level": risk,
            "base_risk_level": risk,
            "risk_score": score,
            "status": "approved",
            "pre_approval_decision": "PRE_APPROVE",
            "approved_at": day(approved_on),
            "first_approved_at": day(approved_on),
            "decided_at": day(approved_on),
            "prescreening_data": json.dumps(prescreening),
            "is_fixture": True,
        },
    )
    db.commit()
    return aid


def insert_review(
    db,
    application_id: str,
    *,
    company: str,
    risk: str,
    due_on: str = "2026-01-01",
    status: str = "in_progress",
) -> int:
    months = {"LOW": 36, "MEDIUM": 24, "HIGH": 12, "VERY_HIGH": 6}.get(risk, 24)
    rid = insert_filtered(
        db,
        "periodic_reviews",
        {
            "application_id": application_id,
            "client_name": company,
            "risk_level": risk,
            "status": status,
            "trigger_type": "time_based",
            "trigger_source": "schedule",
            "review_reason": "PR-PRS-C1 staging smoke review",
            "due_date": day(due_on),
            "next_review_date": day(due_on),
            "review_cycle_number": 1,
            "review_type": "scheduled",
            "policy_version": "periodic_review_policy_v1",
            "frequency_months": months,
            "calculation_basis": "risk_based_anniversary",
            "client_attestation_status": "submitted",
            "client_attestation_submitted_at": iso_now(),
            "baseline_status": "not_applicable",
            "officer_rationale": "PR-PRS-C1 staging smoke completion rationale.",
            "priority": "high" if risk in {"HIGH", "VERY_HIGH"} else "normal",
            "required_items": "[]",
        },
    )
    db.commit()
    return int(rid)


def api(method: str, path: str, *, body: dict[str, Any] | None = None, base_url: str = BASE_URL):
    resp = requests.request(
        method,
        f"{base_url}{path}",
        headers={"Authorization": f"Bearer {SCO_TOKEN}", "Accept": "application/json"},
        json=body,
        timeout=90,
    )
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        payload = {"raw": resp.text[:500]}
    return resp.status_code, payload


def complete(review_id: int, body: dict[str, Any]):
    return api("POST", f"/api/monitoring/reviews/{review_id}/complete", body=body)


def completion_payload(outcome: str, reason: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "outcome": outcome,
        "rationale": reason,
        "officer_acknowledgement": True,
    }
    payload.update(extra)
    return payload


def row(db, table: str, where: str, params: tuple[Any, ...]) -> dict[str, Any]:
    found = db.execute(f"SELECT * FROM {table} WHERE {where}", params).fetchone()
    if not found:
        raise AssertionError(f"missing {table} row for {where} {params}")
    return normalize(dict(found))


def review_row(db, review_id: int) -> dict[str, Any]:
    return row(db, "periodic_reviews", "id = ?", (review_id,))


def app_row(db, app: str) -> dict[str, Any]:
    return row(db, "applications", "id = ?", (app,))


def next_cycle(db, app: str, review_id: int) -> dict[str, Any]:
    found = db.execute(
        """
        SELECT *
          FROM periodic_reviews
         WHERE application_id = ?
           AND id != ?
           AND status IN ('pending', 'in_progress', 'awaiting_information', 'pending_senior_review', 'awaiting_edd')
         ORDER BY review_cycle_number DESC, id DESC
         LIMIT 1
        """,
        (app, review_id),
    ).fetchone()
    if not found:
        raise AssertionError(f"missing next cycle for {app}")
    return normalize(dict(found))


def audit_event(db, review_id: int, action: str) -> dict[str, Any]:
    found = db.execute(
        """
        SELECT *
          FROM audit_log
         WHERE action = ?
           AND target = ?
         ORDER BY id DESC
         LIMIT 1
        """,
        (action, f"periodic_review:{review_id}"),
    ).fetchone()
    if not found:
        raise AssertionError(f"missing audit {action} for review {review_id}")
    out = normalize(dict(found))
    for key in ("detail", "before_state", "after_state"):
        if key in out:
            out[key] = parse_jsonish(out.get(key))
    return out


def scenario_elevation(db) -> dict[str, Any]:
    app = insert_app(db, "elevate", company=f"{PREFIX} Elevation Ltd", risk="MEDIUM", score=48)
    review = insert_review(db, app, company=f"{PREFIX} Elevation Ltd", risk="MEDIUM")
    status, payload = complete(
        review,
        completion_payload(
            "risk_rating_changed",
            "Confirmed material risk elevation to HIGH.",
            risk_changed=True,
            new_risk_level="HIGH",
            risk_impact="Periodic review identified higher residual risk requiring HIGH cadence.",
        ),
    )
    app_after = app_row(db, app)
    review_after = review_row(db, review)
    audit = audit_event(db, review, "periodic_review.canonical_risk_recomputed")
    cycle = next_cycle(db, app, review)
    passed = (
        status == 200
        and app_after.get("risk_level") == "HIGH"
        and app_after.get("final_risk_level") == "HIGH"
        and audit["detail"].get("previous_canonical_risk") == "MEDIUM"
        and audit["detail"].get("officer_confirmed_risk") == "HIGH"
        and audit["detail"].get("final_applied_risk") == "HIGH"
        and review_after.get("status") == "completed"
    )
    return {
        "passed": passed,
        "application_id": app,
        "review_id": review,
        "completion_status": status,
        "completion_payload": payload,
        "application_risk_after": {
            "risk_level": app_after.get("risk_level"),
            "final_risk_level": app_after.get("final_risk_level"),
        },
        "review_status": review_after.get("status"),
        "canonical_audit": audit["detail"],
        "next_cycle": {
            "id": cycle.get("id"),
            "risk_level": cycle.get("risk_level"),
            "frequency_months": cycle.get("frequency_months"),
            "due_date": cycle.get("due_date"),
            "next_review_date": cycle.get("next_review_date"),
        },
    }


def scenario_no_downgrade(db) -> dict[str, Any]:
    app = insert_app(db, "downgrade-floor", company=f"{PREFIX} Downgrade Floor Ltd", risk="HIGH", score=48)
    review = insert_review(db, app, company=f"{PREFIX} Downgrade Floor Ltd", risk="HIGH")
    status, payload = complete(
        review,
        completion_payload(
            "risk_rating_changed",
            "Officer confirmed MEDIUM but previous HIGH must remain the floor.",
            risk_changed=True,
            new_risk_level="MEDIUM",
            risk_impact="Periodic review found partial mitigation; senior downgrade path not invoked.",
        ),
    )
    app_after = app_row(db, app)
    audit = audit_event(db, review, "periodic_review.canonical_risk_recomputed")
    passed = (
        status == 200
        and app_after.get("risk_level") == "HIGH"
        and app_after.get("final_risk_level") == "HIGH"
        and audit["detail"].get("previous_canonical_risk") == "HIGH"
        and audit["detail"].get("officer_confirmed_risk") == "MEDIUM"
        and audit["detail"].get("final_applied_risk") == "HIGH"
        and audit["detail"].get("downgrade_prevented") is True
    )
    return {
        "passed": passed,
        "application_id": app,
        "review_id": review,
        "completion_status": status,
        "completion_payload": payload,
        "application_risk_after": {
            "risk_level": app_after.get("risk_level"),
            "final_risk_level": app_after.get("final_risk_level"),
        },
        "canonical_audit": audit["detail"],
    }


def scenario_material_gate(db) -> dict[str, Any]:
    app = insert_app(db, "material-gate", company=f"{PREFIX} Material Gate Ltd", risk="MEDIUM", score=48)
    review = insert_review(db, app, company=f"{PREFIX} Material Gate Ltd", risk="MEDIUM")
    blocked_status, blocked_payload = complete(
        review,
        completion_payload("material_change_identified", "Material change identified without risk decision."),
    )
    blocked_codes = [item.get("item_type") for item in blocked_payload.get("blocking_items") or []]
    after_block = review_row(db, review)
    clean_status, clean_payload = complete(
        review,
        completion_payload(
            "material_change_identified",
            "Material change reviewed with documented risk decision.",
            risk_impact="Risk decision documented: rating remains MEDIUM after ownership review.",
        ),
    )
    after_clean = review_row(db, review)
    passed = (
        blocked_status == 409
        and "material_change_risk_decision_required" in blocked_codes
        and after_block.get("status") == "in_progress"
        and clean_status == 200
        and after_clean.get("status") == "completed"
    )
    return {
        "passed": passed,
        "application_id": app,
        "review_id": review,
        "blocked_status": blocked_status,
        "blocked_items": blocked_payload.get("blocking_items") or [],
        "status_after_block": after_block.get("status"),
        "completion_status_after_rationale": clean_status,
        "completion_payload_after_rationale": clean_payload,
        "final_review_status": after_clean.get("status"),
    }


def scenario_no_change_regression(db) -> dict[str, Any]:
    app = insert_app(db, "no-change", company=f"{PREFIX} No Change Ltd", risk="HIGH", score=72)
    review = insert_review(db, app, company=f"{PREFIX} No Change Ltd", risk="HIGH")
    before = app_row(db, app)
    status, payload = complete(
        review,
        completion_payload("no_change", "No material changes identified; risk remains unchanged."),
    )
    after = app_row(db, app)
    audit_count = db.execute(
        """
        SELECT COUNT(*) AS c
          FROM audit_log
         WHERE action = 'periodic_review.canonical_risk_recomputed'
           AND target = ?
        """,
        (f"periodic_review:{review}",),
    ).fetchone()["c"]
    passed = (
        status == 200
        and before.get("risk_level") == after.get("risk_level")
        and before.get("final_risk_level") == after.get("final_risk_level")
        and int(audit_count) == 0
    )
    return {
        "passed": passed,
        "application_id": app,
        "review_id": review,
        "completion_status": status,
        "completion_payload": payload,
        "risk_before": {
            "risk_level": before.get("risk_level"),
            "final_risk_level": before.get("final_risk_level"),
        },
        "risk_after": {
            "risk_level": after.get("risk_level"),
            "final_risk_level": after.get("final_risk_level"),
        },
        "canonical_recompute_audit_count": int(audit_count),
    }


def main() -> int:
    wait_for_server()
    version_status, version_payload = api("GET", "/api/version")
    public_version_status, public_version_payload = api("GET", "/api/version", base_url=PUBLIC_BASE_URL)
    db = get_db()
    results: dict[str, Any] = {
        "base_url": BASE_URL,
        "public_base_url": PUBLIC_BASE_URL,
        "prefix": PREFIX,
        "ran_at": iso_now(),
        "expected_sha": EXPECTED_SHA,
        "version": {"status": version_status, "payload": version_payload},
        "public_version": {"status": public_version_status, "payload": public_version_payload},
        "credential_handling": "SCO JWT generated inside staging backend task; token omitted.",
        "scenarios": {},
    }
    failures: list[str] = []
    try:
        if version_status != 200 or version_payload.get("git_sha") != EXPECTED_SHA:
            failures.append("version")
        if public_version_status != 200 or public_version_payload.get("git_sha") != EXPECTED_SHA:
            failures.append("public_version")
        scenarios = [
            ("confirmed_risk_elevation_propagates", scenario_elevation),
            ("no_automatic_downgrade", scenario_no_downgrade),
            ("material_change_rescore_gate", scenario_material_gate),
            ("next_cycle_cadence_follows_final_risk", lambda db: {
                **results["scenarios"]["confirmed_risk_elevation_propagates"]["next_cycle"],
                "passed": (
                    results["scenarios"]["confirmed_risk_elevation_propagates"]["next_cycle"].get("risk_level") == "HIGH"
                    and int(results["scenarios"]["confirmed_risk_elevation_propagates"]["next_cycle"].get("frequency_months") or 0) == 12
                    and str(results["scenarios"]["confirmed_risk_elevation_propagates"]["next_cycle"].get("due_date"))[:10] == "2027-01-01"
                ),
            }),
            ("no_change_does_not_alter_canonical_risk", scenario_no_change_regression),
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
        print("PR_PRS_C1_STAGING_SMOKE_JSON_START")
        print(json.dumps(results, indent=2, sort_keys=True, default=str))
        print("PR_PRS_C1_STAGING_SMOKE_JSON_END")
        return 1 if failures else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
