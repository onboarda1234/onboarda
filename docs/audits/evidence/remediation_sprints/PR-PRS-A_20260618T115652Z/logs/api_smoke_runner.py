#!/usr/bin/env python3
"""PR-PRS-A local smoke runner.

Seeds synthetic periodic-review records into the configured local SQLite DB,
then exercises the live Tornado HTTP handlers on localhost. The output JSON is
rendered in the authenticated back-office browser session for screenshot
evidence.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:10000").rstrip("/")
DB_PATH = os.environ["SMOKE_DB_PATH"]
PASSWORD = os.environ["SMOKE_PASSWORD"]
EVIDENCE_DIR = Path(os.environ["SMOKE_EVIDENCE_DIR"])
PREFIX = os.environ.get("SMOKE_PREFIX") or f"PRPRS-A-{datetime.now(timezone.utc).strftime('%H%M%S')}"
TOKEN: str | None = None


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = resp.read().decode() or "{}"
            return resp.status, json.loads(payload)
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode() or "{}"
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            parsed = {"raw": payload}
        return exc.code, parsed


def login() -> dict[str, Any]:
    global TOKEN
    status, payload = http(
        "POST",
        "/api/auth/officer/login",
        {"email": "raj.patel@onboarda.com", "password": PASSWORD},
    )
    assert status == 200, payload
    TOKEN = payload["token"]
    return payload["user"]


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


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def app_id(name: str) -> str:
    return f"{PREFIX.lower()}-{name}".replace("_", "-")


def insert_app(
    conn: sqlite3.Connection,
    key: str,
    *,
    company: str,
    risk: str = "HIGH",
    approved_on: str = "2025-01-01",
) -> str:
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
            "ownership_structure": "single-tier",
            "risk_level": risk,
            "final_risk_level": risk,
            "risk_score": 72 if risk in ("HIGH", "VERY_HIGH") else 48,
            "status": "approved",
            "approved_at": approved_on,
            "first_approved_at": approved_on,
            "decided_at": approved_on,
            "prescreening_data": prescreening,
            "is_fixture": 0,
        },
    )
    conn.commit()
    return aid


def insert_review(
    conn: sqlite3.Connection,
    application_id: str,
    *,
    company: str,
    status: str = "in_progress",
    risk: str = "HIGH",
    due_date: str = "2026-01-01",
    cycle: int = 1,
    client_attestation_status: str = "submitted",
    baseline_status: str = "not_applicable",
    rationale: str = "Smoke review rationale recorded.",
) -> int:
    rid = insert_filtered(
        conn,
        "periodic_reviews",
        {
            "application_id": application_id,
            "client_name": company,
            "risk_level": risk,
            "status": status,
            "trigger_type": "time_based",
            "trigger_source": "schedule",
            "review_reason": "PR-PRS-A smoke review",
            "due_date": due_date,
            "next_review_date": due_date,
            "review_cycle_number": cycle,
            "review_type": "scheduled",
            "policy_version": "periodic_review_policy_v1",
            "frequency_months": 12 if risk == "HIGH" else 24,
            "calculation_basis": "risk_based_anniversary",
            "client_attestation_status": client_attestation_status,
            "baseline_status": baseline_status,
            "officer_rationale": rationale,
            "priority": "high" if risk == "HIGH" else "normal",
        },
    )
    conn.commit()
    return int(rid)


def review_row(conn: sqlite3.Connection, review_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM periodic_reviews WHERE id = ?", (review_id,)).fetchone()
    assert row is not None, review_id
    return dict(row)


def reviews_for_app(conn: sqlite3.Connection, application_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM periodic_reviews WHERE application_id = ? ORDER BY review_cycle_number, id",
        (application_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def audit_events(conn: sqlite3.Connection, action: str, target: str) -> list[dict[str, Any]]:
    cols = table_columns(conn, "audit_log")
    selected = ["action", "target", "detail", "before_state", "after_state"]
    if "created_at" in cols:
        selected.append("created_at")
    elif "timestamp" in cols:
        selected.append("timestamp")
    rows = conn.execute(
        f"SELECT {', '.join(selected)} FROM audit_log "
        "WHERE action = ? AND target = ? ORDER BY id",
        (action, target),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for key in ("detail", "before_state", "after_state"):
            if item.get(key):
                try:
                    item[key] = json.loads(item[key])
                except Exception:
                    pass
        out.append(item)
    return out


def completion_payload(reason: str, **extra: Any) -> dict[str, Any]:
    body = {
        "outcome": "no_change",
        "outcome_reason": reason,
        "officer_acknowledgement": True,
    }
    body.update(extra)
    return body


def complete_review(review_id: int, body: dict[str, Any]) -> dict[str, Any]:
    status, payload = http("POST", f"/api/monitoring/reviews/{review_id}/complete", body)
    assert status == 200, payload
    return payload


def prepare_next_cycle_for_completion(conn: sqlite3.Connection, review_id: int) -> None:
    conn.execute(
        """
        UPDATE periodic_reviews
           SET status = 'in_progress',
               client_attestation_status = 'submitted',
               baseline_status = 'not_applicable',
               officer_rationale = 'Second smoke cycle rationale.'
         WHERE id = ?
        """,
        (review_id,),
    )
    conn.commit()


def scenario_queue(conn: sqlite3.Connection) -> dict[str, Any]:
    app = insert_app(conn, "queue", company="PR-PRS-A Queue Ltd", risk="MEDIUM")
    pending_id = insert_review(conn, app, company="PR-PRS-A Queue Ltd", status="pending", risk="MEDIUM", due_date="2026-07-01")
    completed_id = insert_review(conn, app, company="PR-PRS-A Queue Ltd", status="completed", risk="MEDIUM", due_date="2025-07-01", cycle=0)
    cancelled_id = insert_review(conn, app, company="PR-PRS-A Queue Ltd", status="cancelled", risk="MEDIUM", due_date="2025-08-01", cycle=0)

    default_status, default_payload = http("GET", "/api/monitoring/reviews")
    completed_status, completed_payload = http("GET", "/api/monitoring/reviews?status=completed")
    assert default_status == 200, default_payload
    assert completed_status == 200, completed_payload
    default_ids = {item["id"] for item in default_payload["reviews"]}
    completed_ids = {item["id"] for item in completed_payload["reviews"]}
    assert pending_id in default_ids
    assert completed_id not in default_ids
    assert cancelled_id not in default_ids
    assert completed_id in completed_ids
    return {
        "passed": True,
        "pending_review_id": pending_id,
        "completed_review_id": completed_id,
        "cancelled_review_id": cancelled_id,
        "default_contains": sorted(default_ids.intersection({pending_id, completed_id, cancelled_id})),
        "completed_filter_contains": sorted(completed_ids.intersection({pending_id, completed_id, cancelled_id})),
    }


def scenario_anchoring(conn: sqlite3.Connection) -> dict[str, Any]:
    app = insert_app(conn, "anchor", company="PR-PRS-A Anchor Ltd", risk="HIGH", approved_on="2025-01-01")
    first_id = insert_review(conn, app, company="PR-PRS-A Anchor Ltd", risk="HIGH", due_date="2026-01-01", cycle=1)
    first = complete_review(first_id, completion_payload("Late cycle completed; preserve anniversary."))
    rows_after_first = reviews_for_app(conn, app)
    second = next(row for row in rows_after_first if row["id"] != first_id and row["status"] == "pending")
    assert second["due_date"] == "2027-01-01", second
    assert second["next_review_date"] == "2027-01-01", second
    count_after_first = sum(1 for row in rows_after_first if row["status"] in ("pending", "in_progress", "awaiting_information", "pending_senior_review", "awaiting_edd"))
    replay_status, replay_payload = http("POST", f"/api/monitoring/reviews/{first_id}/complete", completion_payload("Replay should fail."))
    assert replay_status == 409, replay_payload
    assert count_after_first == 1

    prepare_next_cycle_for_completion(conn, int(second["id"]))
    second_complete = complete_review(int(second["id"]), completion_payload("Early cycle completed; do not pull schedule earlier."))
    rows_after_second = reviews_for_app(conn, app)
    third = next(row for row in rows_after_second if row["status"] == "pending")
    assert third["due_date"] == "2028-01-01", third

    audit = audit_events(conn, "periodic_review.next_cycle_scheduled", f"periodic_review:{first_id}")[-1]
    detail = audit["detail"]
    assert detail["anchor_date"] == "2025-01-01", detail
    assert detail["next_review_date"] == "2027-01-01", detail
    assert detail["late_completion_days"] is not None, detail
    assert "skipped_anniversary_count" in detail, detail

    skip_app = insert_app(conn, "skip", company="PR-PRS-A Skip Ltd", risk="HIGH", approved_on="2023-01-01")
    skip_id = insert_review(conn, skip_app, company="PR-PRS-A Skip Ltd", risk="HIGH", due_date="2024-01-01", cycle=1)
    skip_result = complete_review(skip_id, completion_payload("Very late cycle completed; skip missed anniversaries."))
    skip_audit = audit_events(conn, "periodic_review.next_cycle_scheduled", f"periodic_review:{skip_id}")[-1]["detail"]
    assert skip_result["result"]["next_cycle"]["skipped_anniversary_count"] >= 1, skip_result

    return {
        "passed": True,
        "first_review_id": first_id,
        "first_completion_status": first["status"],
        "first_next_cycle": first["result"]["next_cycle"],
        "recompletion_status": replay_status,
        "second_review_id": second["id"],
        "second_next_cycle": second_complete["result"]["next_cycle"],
        "schedule_dates": [second["due_date"], third["due_date"]],
        "first_audit": detail,
        "skip_review_id": skip_id,
        "skip_next_cycle": skip_result["result"]["next_cycle"],
        "skip_audit": skip_audit,
    }


def scenario_frozen(conn: sqlite3.Connection) -> dict[str, Any]:
    app = insert_app(conn, "frozen", company="PR-PRS-A Frozen Ltd", risk="HIGH")
    review_id = insert_review(conn, app, company="PR-PRS-A Frozen Ltd", risk="HIGH")
    complete_review(review_id, completion_payload("Complete review before frozen checks."))
    doc_id = f"{app}-doc"
    insert_filtered(
        conn,
        "documents",
        {
            "id": doc_id,
            "application_id": app,
            "doc_type": "periodic_review_attestation",
            "doc_name": "periodic_review_attestation.pdf",
            "file_path": f"/tmp/{doc_id}.pdf",
            "uploaded_at": iso_now(),
        },
    )
    conn.commit()
    checks = {
        "findings": http("POST", f"/api/monitoring/reviews/{review_id}/findings", {"officer_findings_note": "mutate"}),
        "risk_change": http("POST", f"/api/monitoring/reviews/{review_id}/risk-change", {"new_risk_level": "VERY_HIGH", "reason_code": "smoke"}),
        "rationale": http("POST", f"/api/monitoring/reviews/{review_id}/officer-rationale", {"officer_rationale": "mutate"}),
        "evidence_link": http("POST", f"/api/monitoring/reviews/{review_id}/evidence-links", {"document_id": doc_id, "link_type": "supporting"}),
    }
    statuses = {name: status for name, (status, _payload) in checks.items()}
    assert statuses == {name: 409 for name in statuses}, checks
    return {
        "passed": True,
        "review_id": review_id,
        "statuses": statuses,
        "errors": {name: payload.get("error") for name, (_status, payload) in checks.items()},
    }


def scenario_legacy(conn: sqlite3.Connection) -> dict[str, Any]:
    blocked_app = insert_app(conn, "legacy-blocked", company="PR-PRS-A Legacy Blocked Ltd", risk="HIGH")
    blocked_id = insert_review(
        conn,
        blocked_app,
        company="PR-PRS-A Legacy Blocked Ltd",
        risk="HIGH",
        client_attestation_status="not_started",
    )
    blocked_status, blocked_payload = http(
        "POST",
        f"/api/monitoring/reviews/{blocked_id}/decision",
        {"decision": "continue", "decision_reason": "Legacy blocked smoke", "officer_acknowledgement": True},
    )
    blocked_row = review_row(conn, blocked_id)
    assert blocked_status == 409, blocked_payload
    assert blocked_payload.get("blocking_items"), blocked_payload
    assert blocked_row["status"] == "in_progress"
    assert blocked_row["decision"] is None
    assert blocked_row["outcome"] is None

    clean_app = insert_app(conn, "legacy-clean", company="PR-PRS-A Legacy Clean Ltd", risk="HIGH")
    clean_id = insert_review(conn, clean_app, company="PR-PRS-A Legacy Clean Ltd", risk="HIGH")
    clean_status, clean_payload = http(
        "POST",
        f"/api/monitoring/reviews/{clean_id}/decision",
        {"decision": "continue", "decision_reason": "Legacy canonical smoke", "officer_acknowledgement": True},
    )
    clean_row = review_row(conn, clean_id)
    assert clean_status == 200, clean_payload
    assert clean_row["status"] == "completed", clean_row
    assert clean_row["outcome"] == "no_change", clean_row
    assert clean_row["decision"] is None, clean_row
    assert clean_row["closed_at"], clean_row
    return {
        "passed": True,
        "blocked_review_id": blocked_id,
        "blocked_status": blocked_status,
        "blocking_items": blocked_payload.get("blocking_items", []),
        "blocked_row": {key: blocked_row[key] for key in ("status", "decision", "outcome")},
        "clean_review_id": clean_id,
        "clean_status": clean_status,
        "clean_row": {key: clean_row[key] for key in ("status", "decision", "outcome", "closed_at", "next_review_date")},
    }


def scenario_edd(conn: sqlite3.Connection) -> dict[str, Any]:
    app = insert_app(conn, "edd", company="PR-PRS-A EDD Ltd", risk="HIGH")
    review_id = insert_review(conn, app, company="PR-PRS-A EDD Ltd", risk="HIGH")
    esc_status, esc_payload = http("POST", f"/api/monitoring/reviews/{review_id}/escalate", {"trigger_notes": "Smoke EDD escalation", "assigned_officer": "co001", "priority": "high"})
    assert esc_status == 200, esc_payload
    edd_id = esc_payload["result"]["edd_case_id"]
    complete_status, complete_payload = http(
        "POST",
        f"/api/monitoring/reviews/{review_id}/complete",
        completion_payload(
            "EDD is required before closure.",
            outcome="edd_required",
            edd_required=True,
            risk_impact="EDD rationale recorded for smoke.",
        ),
    )
    assert complete_status == 200, complete_payload
    awaiting_row = review_row(conn, review_id)
    assert complete_payload["status"] == "awaiting_edd", complete_payload
    assert awaiting_row["status"] == "awaiting_edd", awaiting_row
    assert awaiting_row["completed_at"] is None

    future_sla = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(timespec="seconds")
    analysis_status, analysis_payload = http(
        "PATCH",
        f"/api/edd/cases/{edd_id}",
        {"stage": "analysis", "assigned_officer": "co001", "priority": "high", "sla_due_at": future_sla},
    )
    assert analysis_status == 200, analysis_payload
    findings_status, findings_payload = http(
        "PATCH",
        f"/api/edd/cases/{edd_id}/findings",
        {
            "findings_summary": "Structured EDD findings support approval.",
            "key_concerns": ["Periodic review EDD trigger"],
            "mitigating_evidence": ["Senior review evidence accepted"],
            "rationale": "No residual blocker after EDD analysis.",
            "recommended_outcome": "approve",
        },
    )
    assert findings_status == 200, findings_payload
    review_status, review_payload = http(
        "PATCH",
        f"/api/edd/cases/{edd_id}",
        {
            "stage": "pending_senior_review",
            "assigned_officer": "co001",
            "senior_reviewer": "sco001",
            "sla_due_at": future_sla,
            "note": "Ready for senior approval.",
        },
    )
    assert review_status == 200, review_payload
    approve_status, approve_payload = http(
        "PATCH",
        f"/api/edd/cases/{edd_id}",
        {
            "stage": "edd_approved",
            "assigned_officer": "co001",
            "senior_reviewer": "sco001",
            "sla_due_at": future_sla,
            "decision_reason": "EDD approved for PR-PRS-A smoke.",
        },
    )
    assert approve_status == 200, approve_payload
    final_row = review_row(conn, review_id)
    next_cycles = [
        row for row in reviews_for_app(conn, app)
        if row["id"] != review_id and row["status"] == "pending"
    ]
    assert final_row["status"] == "completed", final_row
    assert final_row["completed_at"], final_row
    assert len(next_cycles) == 1, next_cycles
    edd_row = dict(conn.execute("SELECT * FROM edd_cases WHERE id = ?", (edd_id,)).fetchone())
    assert edd_row["linked_periodic_review_id"] == review_id, edd_row
    return {
        "passed": True,
        "review_id": review_id,
        "edd_case_id": edd_id,
        "awaiting_status": complete_payload["status"],
        "awaiting_row": {key: awaiting_row[key] for key in ("status", "completed_at", "closed_at", "linked_edd_case_id")},
        "approval_status": approve_status,
        "final_row": {key: final_row[key] for key in ("status", "outcome", "completed_at", "closed_at", "linked_edd_case_id")},
        "next_cycle": {key: next_cycles[0][key] for key in ("id", "status", "due_date", "next_review_date", "review_cycle_number")},
        "edd_row": {key: edd_row[key] for key in ("stage", "decision", "linked_periodic_review_id")},
    }


def write_markdown(results: dict[str, Any], user: dict[str, Any]) -> None:
    lines = [
        "# PR-PRS-A API Smoke",
        "",
        f"- Base URL: `{BASE_URL}`",
        f"- DB path: `{DB_PATH}`",
        f"- Authenticated user: `{user['name']}` (`{user['role']}`)",
        f"- Synthetic prefix: `{PREFIX}`",
        "",
        "## Scenario Results",
        "",
    ]
    for key, value in results.items():
        lines.append(f"- `{key}`: {'PASS' if value.get('passed') else 'FAIL'}")
    lines.extend([
        "",
        "## Details",
        "",
        "```json",
        json.dumps(results, indent=2, sort_keys=True, default=_json_default),
        "```",
        "",
    ])
    (EVIDENCE_DIR / "api_smoke.md").write_text("\n".join(lines), encoding="utf-8")
    (EVIDENCE_DIR / "logs" / "api_smoke_results.json").write_text(
        json.dumps(
            {
                "base_url": BASE_URL,
                "db_path": DB_PATH,
                "user": user,
                "prefix": PREFIX,
                "results": results,
            },
            indent=2,
            sort_keys=True,
            default=_json_default,
        ),
        encoding="utf-8",
    )


def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / "logs").mkdir(exist_ok=True)
    wait_for_server()
    user = login()
    with connect() as conn:
        results = {
            "default_queue_actionable_only": scenario_queue(conn),
            "completion_next_cycle_anniversary_anchor": scenario_anchoring(conn),
            "completed_reviews_frozen": scenario_frozen(conn),
            "legacy_decision_canonical_gates": scenario_legacy(conn),
            "edd_awaiting_and_feedback_completion": scenario_edd(conn),
        }
    write_markdown(results, user)
    print(json.dumps({"status": "passed", "results": results}, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
        raise
