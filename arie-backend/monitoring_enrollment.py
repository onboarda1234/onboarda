"""
Monitoring enrollment and initial periodic-review scheduling.

Application approval is the lifecycle event that makes a client eligible for
ongoing monitoring. This module keeps that actuation deterministic and
idempotent: one active schedule row per approved application, risk-based due
dates, and an audit record for every create/update.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional


LOW_REVIEW_INTERVAL_DAYS = 1095      # 36 months
MEDIUM_REVIEW_INTERVAL_DAYS = 730    # 24 months
HIGH_REVIEW_INTERVAL_DAYS = 365      # 12 months
ENHANCED_REVIEW_INTERVAL_DAYS = 180  # 6 months for VERY_HIGH / EDD-routed cases

REVIEW_INTERVAL_DAYS = {
    "LOW": LOW_REVIEW_INTERVAL_DAYS,
    "MEDIUM": MEDIUM_REVIEW_INTERVAL_DAYS,
    "HIGH": HIGH_REVIEW_INTERVAL_DAYS,
    "VERY_HIGH": ENHANCED_REVIEW_INTERVAL_DAYS,
}

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


def _normalise_risk_level(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in REVIEW_INTERVAL_DAYS:
        return text
    return "MEDIUM"


def _json_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    try:
        return json.loads(text)
    except Exception:
        return value


def _nonempty_signal(value: Any) -> bool:
    value = _json_value(value)
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple, set)):
        return any(_nonempty_signal(item) for item in value)
    if isinstance(value, dict):
        return any(_nonempty_signal(item) for item in value.values())
    text = str(value).strip().lower()
    return text not in {"", "none", "null", "false", "0", "[]", "{}"}


def _decision_notes_indicate_edd(value: Any) -> bool:
    data = _json_value(value)
    if isinstance(data, dict):
        decision = str(data.get("decision") or "").strip().lower()
        if decision == "escalate_edd":
            return True
        status = str(data.get("status") or data.get("new_status") or "").strip().lower()
        if status in {"edd_required", "edd_approved"}:
            return True
        return any(
            _nonempty_signal(data.get(key))
            for key in (
                "edd_trigger_flags",
                "edd_triggers",
                "edd_requirements",
                "edd_findings",
            )
        )
    if isinstance(data, list):
        return any(_decision_notes_indicate_edd(item) for item in data)
    return "edd" in str(value or "").strip().lower()


def _value_mentions_edd(value: Any) -> bool:
    data = _json_value(value)
    if isinstance(data, dict):
        return any(_value_mentions_edd(item) for item in data.values())
    if isinstance(data, list):
        return any(_value_mentions_edd(item) for item in data)
    return "edd" in str(data or "").strip().lower()


def _is_edd_routed(app: Dict[str, Any], *, previous_status: Optional[str] = None) -> bool:
    previous = str(previous_status or "").strip().lower()
    status = str(app.get("status") or "").strip().lower()
    lane = str(app.get("onboarding_lane") or "").strip().lower()
    return (
        previous in {"edd_required", "edd_approved"}
        or status in {"edd_required", "edd_approved"}
        or lane == "edd"
        or _decision_notes_indicate_edd(app.get("decision_notes"))
        or _value_mentions_edd(app.get("risk_escalations"))
        or _value_mentions_edd(app.get("elevation_reason_text"))
    )


def final_risk_level_for_review(app: Dict[str, Any]) -> str:
    """Return the authoritative risk level used for review frequency."""
    return _normalise_risk_level(
        app.get("final_risk_level")
        or app.get("risk_level")
        or app.get("base_risk_level")
    )


def review_interval_days_for_application(
    app: Dict[str, Any],
    *,
    previous_status: Optional[str] = None,
) -> int:
    if _is_edd_routed(app, previous_status=previous_status):
        return ENHANCED_REVIEW_INTERVAL_DAYS
    return REVIEW_INTERVAL_DAYS[final_risk_level_for_review(app)]


def review_priority_for_application(
    app: Dict[str, Any],
    *,
    previous_status: Optional[str] = None,
) -> str:
    if _is_edd_routed(app, previous_status=previous_status):
        return "urgent"
    risk = final_risk_level_for_review(app)
    if risk in {"HIGH", "VERY_HIGH"}:
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
    row = _latest_active_review(db, application_id)
    if not row:
        return None
    return {
        "id": row.get("id"),
        "status": row.get("status"),
        "risk_level": row.get("risk_level"),
        "due_date": row.get("due_date"),
        "priority": row.get("priority"),
        "trigger_source": row.get("trigger_source"),
        "trigger_reason": row.get("trigger_reason") or row.get("review_reason"),
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
) -> Dict[str, Any]:
    risk_level = final_risk_level_for_review(app)
    interval_days = review_interval_days_for_application(app, previous_status=previous_status)
    due_date = _parse_anchor_date(approved_at or app.get("decided_at") or app.get("updated_at")) + timedelta(days=interval_days)
    priority = review_priority_for_application(app, previous_status=previous_status)
    trigger_reason = (
        "Initial periodic review scheduled after application approval "
        f"({risk_level} final risk, {interval_days}-day interval)."
    )
    if interval_days == ENHANCED_REVIEW_INTERVAL_DAYS and (
        risk_level == "VERY_HIGH" or _is_edd_routed(app, previous_status=previous_status)
    ):
        trigger_reason = (
            "Enhanced periodic review scheduled after application approval "
            f"({risk_level} final risk / EDD controls, {interval_days}-day interval)."
        )
    return {
        "risk_level": risk_level,
        "interval_days": interval_days,
        "due_date": due_date.isoformat(),
        "priority": priority,
        "trigger_type": "time_based",
        "trigger_source": "schedule",
        "trigger_reason": trigger_reason,
        "review_reason": trigger_reason,
    }


def enroll_approved_application(
    db,
    app: Dict[str, Any],
    *,
    user: Optional[Dict[str, Any]] = None,
    audit_writer=None,
    approved_at: Optional[Any] = None,
    previous_status: Optional[str] = None,
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

    payload = _review_payload(app, previous_status=previous_status, approved_at=approved_at)
    existing = _latest_active_review(db, app_id)
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
                priority = ?,
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
                payload["priority"],
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
                     due_date, priority, sla_due_at, state_changed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, datetime('now'), datetime('now'))
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
                    payload["priority"],
                    payload["due_date"],
                ),
            ).fetchone()
        else:
            db.execute(
                """
                INSERT INTO periodic_reviews
                    (application_id, client_name, risk_level, trigger_type,
                     trigger_reason, trigger_source, review_reason, status,
                     due_date, priority, sla_due_at, state_changed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, datetime('now'), datetime('now'))
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
                    payload["priority"],
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
        "application_id": app_id,
        "application_ref": app_ref,
        "periodic_review_id": review_id,
        "risk_level": payload["risk_level"],
        "interval_days": payload["interval_days"],
        "due_date": payload["due_date"],
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
