"""
Monitoring enrollment and initial periodic-review scheduling.

Application approval is the lifecycle event that makes a client eligible for
ongoing monitoring. This module keeps that actuation deterministic and
idempotent: one active schedule row per approved application, risk-based due
dates, and an audit record for every create/update.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, Optional

from periodic_review_policy import policy_snapshot_for_application
from periodic_review_projection_service import latest_active_review_summary as _projection_latest_active_review_summary

ACTIVE_REVIEW_STATUSES = (
    "pending",
    "in_progress",
    "awaiting_information",
    "pending_senior_review",
)


def _row_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    return dict(row) if not isinstance(row, dict) else dict(row)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def final_risk_level_for_review(app: Dict[str, Any]) -> str:
    """Return the authoritative risk level used for review frequency."""
    anchor_date = app.get("decided_at") or app.get("updated_at") or date.today().isoformat()
    return policy_snapshot_for_application(app, anchor_date=anchor_date)["risk_level"]


def review_interval_days_for_application(
    app: Dict[str, Any],
    *,
    previous_status: Optional[str] = None,
) -> int:
    anchor_value = app.get("decided_at") or app.get("updated_at")
    anchor_date = _parse_anchor_date(anchor_value)
    policy = policy_snapshot_for_application(app, anchor_date=anchor_date, previous_status=previous_status)
    return int(policy["interval_days"])


def review_priority_for_application(
    app: Dict[str, Any],
    *,
    previous_status: Optional[str] = None,
) -> str:
    policy = policy_snapshot_for_application(
        app,
        anchor_date=_parse_anchor_date(app.get("decided_at") or app.get("updated_at")),
        previous_status=previous_status,
    )
    if policy["risk_level"] == "VERY_HIGH":
        return "urgent"
    risk = policy["risk_level"]
    if risk == "HIGH" or policy["enhanced_monitoring"]:
        return "high"
    if risk == "MEDIUM":
        return "normal"
    return "low"


def _parse_anchor_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                pass
    return datetime.now(timezone.utc).date()


def _latest_active_review(db, application_id: str) -> Optional[Dict[str, Any]]:
    row = db.execute(
        """
        SELECT * FROM periodic_reviews
        WHERE application_id = ?
          AND COALESCE(status, 'pending') IN ('pending','in_progress','awaiting_information','pending_senior_review')
        ORDER BY due_date ASC, created_at DESC, id DESC
        LIMIT 1
        """,
        (application_id,),
    ).fetchone()
    return _row_dict(row) if row else None


def latest_active_review_summary(db, application_id: str) -> Optional[Dict[str, Any]]:
    projection = _projection_latest_active_review_summary(db, application_id)
    if not projection:
        return None
    return {
        "id": projection.get("review_id"),
        "status": projection.get("status"),
        "status_label": projection.get("status_label"),
        "risk_level": projection.get("risk_level"),
        "due_date": projection.get("due_date"),
        "next_review_date": projection.get("next_review_date"),
        "priority": projection.get("priority"),
        "assigned_officer": projection.get("assigned_officer"),
        "linked_edd_case_id": projection.get("linked_edd_case_id"),
        "blocker_count": projection.get("blocker_count"),
        "blocker_summary": projection.get("blocker_summary"),
        "trigger_source": projection.get("trigger_source"),
        "trigger_reason": projection.get("trigger_reason"),
    }


def _audit(
    audit_writer,
    user: Optional[Dict[str, Any]],
    target: str,
    payload: Dict[str, Any],
    db,
    *,
    before_state=None,
    after_state=None,
):
    if audit_writer is None:
        raise RuntimeError("Monitoring enrollment requires an audit writer")
    audit_writer(
        user or {"sub": "system", "name": "System", "role": "system"},
        "Monitoring Enrollment",
        target,
        json.dumps(payload, default=str, sort_keys=True),
        db=db,
        before_state=before_state,
        after_state=after_state,
        commit=False,
    )


def _review_payload(
    app: Dict[str, Any],
    *,
    previous_status: Optional[str],
    approved_at: Optional[Any],
    review_cycle_number: int,
) -> Dict[str, Any]:
    anchor_date = _parse_anchor_date(approved_at or app.get("decided_at") or app.get("updated_at"))
    policy = policy_snapshot_for_application(app, anchor_date=anchor_date, previous_status=previous_status)
    risk_level = policy["risk_level"]
    interval_days = int(policy["interval_days"])
    priority = review_priority_for_application(app, previous_status=previous_status)
    due_date = policy["due_date"]
    next_review_date = policy["next_review_date"]
    cadence_label = f"{policy['frequency_months']}-month cadence"
    trigger_reason = (
        "Initial periodic review scheduled after application approval "
        f"({risk_level} final risk, {cadence_label})."
    )
    if policy["enhanced_monitoring"]:
        reasons = ", ".join(policy["enhanced_monitoring_reasons"])
        trigger_reason = (
            "Enhanced periodic review scheduled after application approval "
            f"({risk_level} final risk / {reasons}, {cadence_label})."
        )
    return {
        "risk_level": risk_level,
        "interval_days": interval_days,
        "due_date": due_date,
        "priority": priority,
        "trigger_type": "time_based",
        "trigger_source": "schedule",
        "trigger_reason": trigger_reason,
        "review_reason": trigger_reason,
        "review_cycle_number": review_cycle_number,
        "review_type": "scheduled",
        "policy_version": policy["policy_version"],
        "frequency_months": policy["frequency_months"],
        "calculation_basis": policy["calculation_basis"],
        "next_review_date": next_review_date,
        "enhanced_monitoring_reasons": policy["enhanced_monitoring_reasons"],
    }


def enroll_approved_application(
    db,
    app: Dict[str, Any],
    *,
    user: Optional[Dict[str, Any]] = None,
    audit_writer=None,
    approved_at: Optional[Any] = None,
    previous_status: Optional[str] = None,
    enrollment_source: str = "approval_decision",
) -> Dict[str, Any]:
    """Create or update the active periodic-review schedule for an approved app.

    The function does not commit. Callers own the surrounding transaction so
    approval and enrollment cannot diverge.
    """
    if audit_writer is None:
        raise RuntimeError("Monitoring enrollment requires an audit writer")

    app = _row_dict(app)
    app_id = app.get("id")
    app_ref = app.get("ref") or app_id
    status = str(app.get("status") or "").strip().lower()
    if not app_id:
        return {"status": "skipped", "reason": "missing_application_id"}
    if status != "approved":
        return {"status": "skipped", "reason": f"not_approved:{status or 'unknown'}"}
    if _truthy(app.get("is_fixture")):
        return {"status": "skipped", "reason": "fixture_application"}

    existing = _latest_active_review(db, app_id)
    cycle_row = db.execute(
        "SELECT COALESCE(MAX(review_cycle_number), 0) AS cycle_no FROM periodic_reviews WHERE application_id = ?",
        (app_id,),
    ).fetchone()
    existing_cycle = int(_row_dict(cycle_row).get("cycle_no") or 0)
    next_cycle_number = existing_cycle if existing else existing_cycle + 1
    payload = _review_payload(
        app,
        previous_status=previous_status,
        approved_at=approved_at,
        review_cycle_number=next_cycle_number or 1,
    )
    if existing:
        before = dict(existing)
        review_id = existing["id"]
        db.execute(
            """
            UPDATE periodic_reviews
            SET client_name = ?,
                risk_level = ?,
                trigger_type = ?,
                trigger_source = ?,
                trigger_reason = ?,
                review_reason = ?,
                due_date = ?,
                next_review_date = ?,
                priority = ?,
                review_cycle_number = COALESCE(review_cycle_number, ?),
                review_type = COALESCE(review_type, ?),
                policy_version = ?,
                frequency_months = ?,
                calculation_basis = ?,
                sla_due_at = ?,
                state_changed_at = datetime('now')
            WHERE id = ?
            """,
            (
                app.get("company_name"),
                payload["risk_level"],
                payload["trigger_type"],
                payload["trigger_source"],
                payload["trigger_reason"],
                payload["review_reason"],
                payload["due_date"],
                payload["next_review_date"],
                payload["priority"],
                payload["review_cycle_number"],
                payload["review_type"],
                payload["policy_version"],
                payload["frequency_months"],
                payload["calculation_basis"],
                payload["due_date"],
                review_id,
            ),
        )
        row = db.execute("SELECT * FROM periodic_reviews WHERE id = ?", (review_id,)).fetchone()
        after = _row_dict(row)
        action = "updated"
    else:
        if getattr(db, "is_postgres", False):
            row = db.execute(
                """
                INSERT INTO periodic_reviews
                    (application_id, client_name, risk_level, trigger_type,
                     trigger_reason, trigger_source, review_reason, status,
                     due_date, next_review_date, priority, review_cycle_number,
                     review_type, policy_version, frequency_months, calculation_basis,
                     sla_due_at, state_changed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                RETURNING *
                """,
                (
                    app_id,
                    app.get("company_name"),
                    payload["risk_level"],
                    payload["trigger_type"],
                    payload["trigger_reason"],
                    payload["trigger_source"],
                    payload["review_reason"],
                    payload["due_date"],
                    payload["next_review_date"],
                    payload["priority"],
                    payload["review_cycle_number"],
                    payload["review_type"],
                    payload["policy_version"],
                    payload["frequency_months"],
                    payload["calculation_basis"],
                    payload["due_date"],
                ),
            ).fetchone()
        else:
            db.execute(
                """
                INSERT INTO periodic_reviews
                    (application_id, client_name, risk_level, trigger_type,
                     trigger_reason, trigger_source, review_reason, status,
                     due_date, next_review_date, priority, review_cycle_number,
                     review_type, policy_version, frequency_months, calculation_basis,
                     sla_due_at, state_changed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    app_id,
                    app.get("company_name"),
                    payload["risk_level"],
                    payload["trigger_type"],
                    payload["trigger_reason"],
                    payload["trigger_source"],
                    payload["review_reason"],
                    payload["due_date"],
                    payload["next_review_date"],
                    payload["priority"],
                    payload["review_cycle_number"],
                    payload["review_type"],
                    payload["policy_version"],
                    payload["frequency_months"],
                    payload["calculation_basis"],
                    payload["due_date"],
                ),
            )
            row = db.execute(
                "SELECT * FROM periodic_reviews WHERE application_id = ? ORDER BY id DESC LIMIT 1",
                (app_id,),
            ).fetchone()
        after = _row_dict(row)
        before = None
        review_id = after.get("id")
        action = "created"

    audit_payload = {
        "event": "monitoring_enrollment",
        "action": action,
        "enrollment_source": enrollment_source,
        "application_id": app_id,
        "application_ref": app_ref,
        "periodic_review_id": review_id,
        "risk_level": payload["risk_level"],
        "interval_days": payload["interval_days"],
        "due_date": payload["due_date"],
        "next_review_date": payload["next_review_date"],
        "policy_version": payload["policy_version"],
        "frequency_months": payload["frequency_months"],
        "calculation_basis": payload["calculation_basis"],
        "enhanced_monitoring_reasons": payload["enhanced_monitoring_reasons"],
        "priority": payload["priority"],
        "trigger_source": payload["trigger_source"],
        "previous_status": previous_status,
    }
    _audit(
        audit_writer,
        user,
        app_ref,
        audit_payload,
        db,
        before_state=before,
        after_state=after,
    )
    return {
        "status": action,
        "application_id": app_id,
        "application_ref": app_ref,
        "periodic_review_id": review_id,
        "risk_level": payload["risk_level"],
        "interval_days": payload["interval_days"],
        "due_date": payload["due_date"],
        "priority": payload["priority"],
    }


def backfill_approved_applications(
    db,
    *,
    user: Optional[Dict[str, Any]] = None,
    audit_writer=None,
    applications: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Enroll existing approved applications missing an active review schedule."""
    if applications is None:
        rows = db.execute(
            """
            SELECT * FROM applications
            WHERE status = 'approved'
              AND (is_fixture IS NULL OR NOT is_fixture)
            ORDER BY decided_at ASC, created_at ASC
            """
        ).fetchall()
        applications = [_row_dict(row) for row in rows]

    result = {"created": 0, "updated": 0, "skipped": 0, "items": []}
    for app in applications:
        enrolled = enroll_approved_application(
            db,
            app,
            user=user,
            audit_writer=audit_writer,
            approved_at=app.get("decided_at") or app.get("updated_at"),
            previous_status=app.get("status"),
            enrollment_source="backfill_repair",
        )
        status = enrolled.get("status")
        if status == "created":
            result["created"] += 1
        elif status == "updated":
            result["updated"] += 1
        else:
            result["skipped"] += 1
        result["items"].append(enrolled)
    return result
