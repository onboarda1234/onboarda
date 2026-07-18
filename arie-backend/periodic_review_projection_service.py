from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from periodic_review_blockers import (
    NON_OVERRIDABLE_VERIFICATION_STATES,
    SENIOR_DOCUMENT_REVIEW_ROLES,
    decode_required_items,
    evaluate_review_readiness,
    load_evidence_links,
)
from periodic_review_attestation import (
    ATTESTATION_STATUS_NOT_STARTED,
    attestation_snapshot_from_review,
    task_primary_action_label,
    task_status_label,
)
from periodic_review_notifications import (
    fixture_notification_suppression,
    notification_projection_from_review,
)

ACTIVE_REVIEW_STATES = (
    "pending",
    "in_progress",
    "awaiting_information",
    "pending_senior_review",
    "awaiting_edd",
)
COMPLETED_REVIEW_STATE = "completed"
CANCELLED_REVIEW_STATE = "cancelled"
CANCELED_REVIEW_STATE = "canceled"

TERMINAL_QUEUE_STATUSES = {"completed", "cancelled", "canceled"}
OPEN_QUEUE_FILTER_STATUSES = {
    "open",
    "due",
    "overdue",
    "awaiting_client",
    "in_review",
    "awaiting_edd",
}

OPERATIONAL_STATUS_LABELS = {
    "no_active_review": "No active review",
    "due": "Due",
    "awaiting_client_attestation": "Awaiting client attestation",
    "awaiting_documents": "Awaiting documents",
    "officer_review_required": "Officer review required",
    "blocked": "Blocked",
    "awaiting_edd": "Awaiting EDD",
    "ready_for_decision": "Ready for decision",
    "completed": "Completed",
    "historical_superseded": "Historical / Superseded",
}


def _row_get(row, key, default=None):
    if row is None:
        return default
    try:
        if key in row.keys():
            value = row[key]
            return default if value is None else value
    except Exception:
        pass
    if isinstance(row, dict):
        value = row.get(key, default)
        return default if value is None else value
    return default


def _table_columns(db, table: str) -> Set[str]:
    try:
        rows = db.execute(f"PRAGMA table_info({table})").fetchall()
        if rows:
            return {str(row["name"]) for row in rows}
    except Exception:
        pass
    try:
        rows = db.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
        return {str(row["column_name"]) for row in rows}
    except Exception:
        return set()


def _parse_iso_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _effective_risk_level(review) -> Optional[str]:
    return _row_get(review, "new_risk_level") or _row_get(review, "risk_level")


def _load_memo_status(db, review_id: int, review_row) -> Optional[str]:
    status = _row_get(review_row, "memo_status")
    if status:
        return status
    row = db.execute(
        "SELECT id, status FROM periodic_review_memos WHERE periodic_review_id = ? "
        "ORDER BY version DESC, id DESC LIMIT 1",
        (review_id,),
    ).fetchone()
    if row:
        return _row_get(row, "status")
    if _row_get(review_row, "outcome"):
        return "pending"
    return None


def derive_operational_review_status(
    *,
    raw_status: str,
    due_state: Optional[str] = None,
    blocker_count: int = 0,
    linked_edd_case_id: Any = None,
    attestation_status: Optional[str] = None,
    has_missing_documents: bool = False,
    has_documents_pending_review: bool = False,
    findings_present: bool = False,
    historical: bool = False,
) -> Dict[str, str]:
    status = str(raw_status or "pending").strip().lower() or "pending"
    attestation = str(attestation_status or "").strip().lower()
    if historical:
        key = "historical_superseded"
    elif status == COMPLETED_REVIEW_STATE:
        key = "completed"
    elif status in {CANCELLED_REVIEW_STATE, CANCELED_REVIEW_STATE}:
        key = "historical_superseded"
    elif status == "awaiting_edd":
        key = "awaiting_edd"
    elif attestation and attestation != "submitted":
        key = "awaiting_client_attestation"
    elif has_missing_documents:
        key = "awaiting_documents"
    elif has_documents_pending_review:
        key = "officer_review_required"
    elif blocker_count or linked_edd_case_id:
        key = "blocked"
    elif findings_present or (
        status in {"in_progress", "pending_senior_review"} and attestation == "submitted"
    ):
        key = "ready_for_decision"
    elif status in {"in_progress", "pending_senior_review", "awaiting_information"}:
        key = "officer_review_required"
    elif status == "pending" or due_state in {"due", "overdue", "scheduled", "missing_due_date"}:
        key = "due"
    else:
        key = "no_active_review"
    return {
        "status_key": key,
        "status_label": OPERATIONAL_STATUS_LABELS[key],
    }


def _trigger_source_label(value: Any) -> str:
    key = str(value or "").strip().lower()
    mapping = {
        "schedule": "Scheduled cadence",
        "time_based": "Scheduled cadence",
        "monitoring_alert": "Monitoring escalation",
        "manual": "Manual review",
        "officer_decision": "Manual review",
        "policy_routing": "Policy trigger",
        "policy_trigger": "Policy trigger",
        "screening_update": "Policy trigger",
        "manual_backfill": "Manual backfill",
        "legacy_import": "Legacy import",
    }
    if key in mapping:
        return mapping[key]
    if not key:
        return "Periodic review"
    return key.replace("_", " ").title()


def _review_due_state(review, *, raw_status: str) -> Dict[str, Any]:
    due_text = _row_get(review, "due_date") or _row_get(review, "next_review_date")
    due_date = _parse_iso_date(due_text)
    if raw_status in TERMINAL_QUEUE_STATUSES:
        return {
            "due_date": due_text,
            "due_state": "completed" if raw_status == COMPLETED_REVIEW_STATE else "cancelled",
            "due_status_label": "Completed" if raw_status == COMPLETED_REVIEW_STATE else "Cancelled",
            "days_until_due": None,
            "is_overdue": False,
            "is_due_date_missing": due_date is None,
        }
    if due_date is None:
        return {
            "due_date": due_text,
            "due_state": "missing_due_date",
            "due_status_label": "Missing Due Date",
            "days_until_due": None,
            "is_overdue": False,
            "is_due_date_missing": True,
        }
    today = datetime.now(timezone.utc).date()
    days_until_due = (due_date - today).days
    if days_until_due < 0:
        return {
            "due_date": due_text,
            "due_state": "overdue",
            "due_status_label": "Overdue",
            "days_until_due": days_until_due,
            "is_overdue": True,
            "is_due_date_missing": False,
        }
    if days_until_due == 0:
        return {
            "due_date": due_text,
            "due_state": "due",
            "due_status_label": "Due",
            "days_until_due": 0,
            "is_overdue": False,
            "is_due_date_missing": False,
        }
    return {
        "due_date": due_text,
        "due_state": "scheduled",
        "due_status_label": "Scheduled",
        "days_until_due": days_until_due,
        "is_overdue": False,
        "is_due_date_missing": False,
    }


def _queue_status(raw_status: str, due_state: str) -> str:
    if raw_status == COMPLETED_REVIEW_STATE:
        return "completed"
    if raw_status in {CANCELLED_REVIEW_STATE, CANCELED_REVIEW_STATE}:
        return "cancelled"
    if raw_status == "awaiting_edd":
        return "awaiting_edd"
    if raw_status == "awaiting_information":
        return "awaiting_client"
    if raw_status in {"in_progress", "pending_senior_review"}:
        return "in_review"
    if due_state == "overdue":
        return "overdue"
    if due_state == "due":
        return "due"
    return "open"


def _queue_status_label(queue_status: str) -> str:
    return {
        "due": "Due",
        "open": "Open",
        "awaiting_client": "Awaiting Client",
        "in_review": "In Review",
        "overdue": "Overdue",
        "awaiting_edd": "Awaiting EDD",
        "completed": "Completed",
        "cancelled": "Cancelled",
    }.get(queue_status, "Open")


def _is_legacy_completed_review(review, raw_status: str) -> bool:
    if raw_status != COMPLETED_REVIEW_STATE:
        return False
    if _row_get(review, "outcome"):
        return False
    return bool(_row_get(review, "decision"))


def _prefetch_rows_by_id(db, table: str, *, id_column: str, ids: Sequence[Any], columns: str) -> Dict[Any, Dict[str, Any]]:
    cleaned_ids = [value for value in dict.fromkeys(ids) if value not in (None, "")]
    if not cleaned_ids:
        return {}
    placeholders = ",".join("?" for _ in cleaned_ids)
    rows = db.execute(
        f"SELECT {columns} FROM {table} WHERE {id_column} IN ({placeholders})",
        tuple(cleaned_ids),
    ).fetchall()
    return {row[id_column]: dict(row) for row in rows}


def _last_activity_timestamp(review) -> Optional[str]:
    candidates = []
    for field in (
        "client_attestation_submitted_at",
        "client_attestation_saved_at",
        "state_changed_at",
        "required_items_generated_at",
        "risk_rerated_at",
        "assigned_at",
        "started_at",
        "outcome_recorded_at",
        "completed_at",
        "closed_at",
        "legacy_entered_at",
        "created_at",
    ):
        raw_value = _row_get(review, field)
        parsed = _parse_iso_datetime(raw_value)
        if parsed is not None:
            candidates.append((parsed, raw_value))
    if not candidates:
        return _row_get(review, "created_at")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _periodic_review_doc_request_ready(requirement: Dict[str, Any]) -> bool:
    linked = requirement.get("linked_document") if isinstance(requirement.get("linked_document"), dict) else {}
    verification_status = str(
        linked.get("verification_status") or requirement.get("document_verification_status") or ""
    ).strip().lower()
    review_status = str(
        linked.get("review_status") or requirement.get("document_review_status") or ""
    ).strip().lower()
    reviewer_role = str(
        linked.get("reviewer_role") or requirement.get("document_reviewer_role") or ""
    ).strip().lower()
    review_comment = str(
        linked.get("review_comment") or requirement.get("document_review_comment") or ""
    ).strip()
    if verification_status == "verified":
        return True
    reliance_state = str(
        linked.get("document_reliance_state") or requirement.get("document_reliance_state") or ""
    ).strip().lower()
    reliance_status = str(
        linked.get("document_reliance_status") or requirement.get("document_reliance_status") or ""
    ).strip().lower()
    if reliance_state in {"verified", "manual_accepted"} or reliance_status in {"ready", "verified"}:
        return True
    return (
        review_status in {"accepted", "approved"}
        and reviewer_role in SENIOR_DOCUMENT_REVIEW_ROLES
        and bool(review_comment)
        and verification_status not in NON_OVERRIDABLE_VERIFICATION_STATES
    )


def _periodic_review_document_request_status(db, review_id: int) -> Dict[str, int]:
    if review_id in (None, ""):
        return {"count": 0, "required_count": 0, "missing_count": 0, "review_required_count": 0}
    req_columns = _table_columns(db, "application_enhanced_requirements")
    requirement_display_select = (
        "aer.requirement_display_type"
        if "requirement_display_type" in req_columns
        else "'evidence'"
    )
    active_filter = "AND COALESCE(aer.active, 1) = 1" if "active" in req_columns else ""
    rows = db.execute(
        f"""
        SELECT aer.id,
               aer.mandatory,
               aer.linked_document_id,
               {requirement_display_select} AS requirement_display_type,
               d.verification_status AS document_verification_status,
               d.review_status AS document_review_status,
               d.reviewer_role AS document_reviewer_role,
               d.review_comment AS document_review_comment
        FROM application_enhanced_requirements aer
        LEFT JOIN documents d ON d.id = aer.linked_document_id
        WHERE aer.linked_periodic_review_id = ?
          {active_filter}
        ORDER BY aer.id ASC
        """,
        (review_id,),
    ).fetchall()
    requests = [dict(row) for row in rows]
    evidence_requests = [
        item for item in requests
        if str(item.get("requirement_display_type") or "evidence").strip().lower() == "evidence"
    ]
    required_requests = [item for item in evidence_requests if bool(item.get("mandatory"))]
    missing_requests = [item for item in required_requests if not item.get("linked_document_id")]
    review_pending_requests = [
        item for item in required_requests
        if item.get("linked_document_id") and not _periodic_review_doc_request_ready(item)
    ]
    return {
        "count": len(requests),
        "required_count": len(required_requests),
        "missing_count": len(missing_requests),
        "review_required_count": len(review_pending_requests),
    }


def build_review_projection(
    db,
    review_row,
    *,
    evidence_links: Optional[List[Dict[str, Any]]] = None,
    application_row: Optional[Dict[str, Any]] = None,
    assigned_officer_row: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    review = dict(review_row)
    review_id = _row_get(review, "id")
    app_id = _row_get(review, "application_id")
    application = application_row
    if application is None and app_id:
        app_row = db.execute(
            "SELECT id, ref, company_name, risk_level, final_risk_level, is_fixture "
            "FROM applications WHERE id = ?",
            (app_id,),
        ).fetchone()
        application = dict(app_row) if app_row is not None else None
    assigned_officer = _row_get(review, "assigned_officer")
    owner_row = assigned_officer_row
    if owner_row is None and assigned_officer:
        officer_row = db.execute(
            "SELECT id, full_name FROM users WHERE id = ?",
            (assigned_officer,),
        ).fetchone()
        owner_row = dict(officer_row) if officer_row is not None else None

    evidence_links = evidence_links if evidence_links is not None else load_evidence_links(db, review_id)
    readiness = evaluate_review_readiness(
        db,
        review,
        required_items=decode_required_items(_row_get(review, "required_items")),
        evidence_links=evidence_links,
    )
    blockers = readiness["operational_blockers"]
    raw_status = str(_row_get(review, "status") or "pending").strip().lower() or "pending"
    due_meta = _review_due_state(review, raw_status=raw_status)
    queue_status = _queue_status(raw_status, due_meta["due_state"])
    completion_readiness_applicable = not _is_legacy_completed_review(review, raw_status)
    if completion_readiness_applicable:
        completion_blocker_count = readiness["completion_blocker_count"]
        completion_blockers = readiness["completion_blockers"]
        completion_ready = readiness["completion_ready"]
    else:
        completion_blocker_count = 0
        completion_blockers = []
        completion_ready = None

    owner_display_name = (
        _row_get(owner_row, "full_name")
        or assigned_officer
        or "Unassigned"
    )
    updated_at = _last_activity_timestamp(review)
    is_terminal = queue_status in TERMINAL_QUEUE_STATUSES
    trigger_source = _row_get(review, "trigger_source") or _row_get(review, "trigger_type")
    attestation = attestation_snapshot_from_review(review)
    attestation_status = str(attestation.get("status") or ATTESTATION_STATUS_NOT_STARTED)
    document_request_status = _periodic_review_document_request_status(db, review_id)
    notification_suppression = fixture_notification_suppression(
        review, application or {}
    )
    notification_summary = notification_projection_from_review(
        review,
        document_summary=document_request_status,
        suppression=notification_suppression,
    )
    findings_present = any(
        str(_row_get(review, field) or "").strip()
        for field in ("officer_findings_note", "officer_deficiencies_note", "officer_internal_review_note")
    )
    operational_status = derive_operational_review_status(
        raw_status=raw_status,
        due_state=due_meta["due_state"],
        blocker_count=len(blockers),
        linked_edd_case_id=_row_get(review, "linked_edd_case_id"),
        attestation_status=attestation_status,
        has_missing_documents=bool(document_request_status["missing_count"]),
        has_documents_pending_review=bool(document_request_status["review_required_count"]),
        findings_present=findings_present,
    )

    return {
        "review_id": review_id,
        "review_reference": f"PR-{review_id}" if review_id is not None else "",
        "application_id": app_id,
        "application_ref": _row_get(application, "ref") or app_id,
        "is_fixture": bool(_row_get(application, "is_fixture")),
        "client_name": _row_get(application, "company_name") or _row_get(review, "client_name") or "",
        "status": raw_status,
        "operational_status": operational_status["status_key"],
        "status_label": operational_status["status_label"],
        "queue_status": queue_status,
        "queue_status_label": operational_status["status_label"],
        "assigned_officer": assigned_officer,
        "assigned_officer_name": _row_get(owner_row, "full_name"),
        "owner_display_name": owner_display_name,
        "owner_state": "assigned" if assigned_officer else "unassigned",
        "linked_edd_case_id": _row_get(review, "linked_edd_case_id"),
        "linked_monitoring_alert_id": _row_get(review, "linked_monitoring_alert_id"),
        "due_date": due_meta["due_date"],
        "due_state": due_meta["due_state"],
        "due_status_label": due_meta["due_status_label"],
        "is_overdue": due_meta["is_overdue"],
        "is_due_date_missing": due_meta["is_due_date_missing"],
        "days_until_due": due_meta["days_until_due"],
        "priority": _row_get(review, "priority"),
        "trigger_source": trigger_source,
        "trigger_source_label": _trigger_source_label(trigger_source),
        "trigger_reason": _row_get(review, "trigger_reason") or _row_get(review, "review_reason"),
        "last_review_date": _row_get(review, "last_review_date"),
        "next_review_date": _row_get(review, "next_review_date") or _row_get(review, "due_date"),
        "risk_level": _effective_risk_level(review) or _row_get(application, "final_risk_level") or _row_get(application, "risk_level"),
        "blocker_count": len(blockers),
        "blocker_summary": [blocker["label"] for blocker in blockers],
        "is_blocked": bool(blockers),
        "completion_blocker_count": completion_blocker_count,
        "completion_blocker_summary": [blocker["label"] for blocker in completion_blockers],
        "completion_ready": completion_ready,
        "completion_readiness_applicable": completion_readiness_applicable,
        "outcome": _row_get(review, "outcome"),
        "memo_status": _load_memo_status(db, review_id, review),
        "attestation_status": attestation_status,
        "attestation_status_label": {
            "not_started": "Not started",
            "draft": "Draft saved",
            "submitted": "Submitted",
        }.get(attestation_status, "Not started"),
        "attestation_task_status_label": task_status_label(attestation, is_overdue=due_meta["is_overdue"]),
        "attestation_primary_action_label": task_primary_action_label(attestation),
        "attestation_saved_at": attestation.get("saved_at"),
        "attestation_submitted_at": attestation.get("submitted_at"),
        "attestation_submitted_by": attestation.get("submitted_by"),
        "attestation_has_material_changes": bool(attestation.get("has_material_changes")),
        "attestation_material_change_question_keys": attestation.get("material_change_question_keys", []),
        "periodic_review_document_request_count": document_request_status["count"],
        "periodic_review_required_document_request_count": document_request_status["required_count"],
        "periodic_review_missing_document_request_count": document_request_status["missing_count"],
        "periodic_review_documents_pending_review_count": document_request_status["review_required_count"],
        "client_notification_status": notification_summary["client_notification_status"],
        "client_notification_status_label": notification_summary["client_notification_status_label"],
        "initial_notification_sent_at": notification_summary["initial_notification_sent_at"],
        "last_reminder_sent_at": notification_summary["last_reminder_sent_at"],
        "reminder_count": notification_summary["reminder_count"],
        "last_notification_error": notification_summary["last_notification_error"],
        "officer_alert_status": notification_summary["officer_alert_status"],
        "officer_alerted_at": notification_summary["officer_alerted_at"],
        "notification_channel": notification_summary["notification_channel"],
        "next_reminder_due_at": notification_summary["next_reminder_due_at"],
        "client_action_required": notification_summary["client_action_required"],
        "client_action_required_label": notification_summary["client_action_required_label"],
        "notification_suppressed": notification_summary["notification_suppressed"],
        "notification_suppression_reason": notification_summary.get("notification_suppression_reason"),
        "notification_suppression_evidence": notification_summary.get("notification_suppression_evidence"),
        "is_client_action_overdue": notification_summary["is_client_action_overdue"],
        "notification_summary": notification_summary,
        "created_at": _row_get(review, "created_at"),
        "updated_at": updated_at,
        "last_activity_at": updated_at,
        "completed_at": _row_get(review, "completed_at"),
        "audit_reference": f"periodic_review:{review_id}" if review_id is not None else None,
        "can_take_action": not is_terminal,
        "is_terminal": is_terminal,
        "primary_action_label": "View review case" if is_terminal else "Open review case",
        "lifecycle_link": {
            "type": "periodic_review",
            "review_id": review_id,
            "path": f"/api/monitoring/reviews/{review_id}",
        },
        "source": "periodic_reviews",
        "evidence_links": evidence_links,
    }


def get_review_projection(db, review_id: int) -> Optional[Dict[str, Any]]:
    row = db.execute("SELECT * FROM periodic_reviews WHERE id = ?", (review_id,)).fetchone()
    if row is None:
        return None
    return build_review_projection(db, row)


def list_review_projections(
    db,
    *,
    application_id: Optional[str] = None,
    application_ids: Optional[Iterable[str]] = None,
    review_ids: Optional[Iterable[int]] = None,
    statuses: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM periodic_reviews WHERE 1=1"
    params: List[Any] = []
    if application_id:
        sql += " AND application_id = ?"
        params.append(application_id)
    if application_ids:
        app_ids = [app_id for app_id in dict.fromkeys(application_ids) if app_id]
        if app_ids:
            sql += " AND application_id IN (" + ",".join("?" for _ in app_ids) + ")"
            params.extend(app_ids)
    if review_ids:
        cleaned_review_ids = [int(review_id) for review_id in dict.fromkeys(review_ids)]
        if cleaned_review_ids:
            sql += " AND id IN (" + ",".join("?" for _ in cleaned_review_ids) + ")"
            params.extend(cleaned_review_ids)
    if statuses:
        cleaned = [str(status).strip().lower() for status in statuses if str(status).strip()]
        if cleaned:
            sql += " AND LOWER(COALESCE(status, 'pending')) IN (" + ",".join("?" for _ in cleaned) + ")"
            params.extend(cleaned)
    columns = _table_columns(db, "periodic_reviews")
    order_parts = []
    if "due_date" in columns:
        order_parts.append("due_date ASC")
    if "created_at" in columns:
        order_parts.append("created_at DESC")
    order_parts.append("id DESC")
    sql += " ORDER BY " + ", ".join(order_parts)
    rows = db.execute(sql, tuple(params)).fetchall()
    review_ids = [row["id"] for row in rows]
    app_ids = [_row_get(row, "application_id") for row in rows]
    officer_ids = [_row_get(row, "assigned_officer") for row in rows]
    applications_by_id = _prefetch_rows_by_id(
        db,
        "applications",
        id_column="id",
        ids=app_ids,
        columns="id, ref, company_name, risk_level, final_risk_level, is_fixture",
    )
    officers_by_id = _prefetch_rows_by_id(
        db,
        "users",
        id_column="id",
        ids=officer_ids,
        columns="id, full_name",
    )

    evidence_by_review: Dict[int, List[Dict[str, Any]]] = {rid: [] for rid in review_ids}
    if review_ids:
        placeholders = ",".join("?" for _ in review_ids)
        link_rows = db.execute(
            f"SELECT l.id, l.periodic_review_id, l.requirement_id, l.document_id, l.link_type, l.linked_by, l.linked_at, l.note, "
            "d.doc_type AS document_type, d.doc_name AS document_name, d.verification_status AS document_verification_status, "
            "d.review_status AS document_review_status, d.review_comment AS document_review_comment, "
            "d.reviewer_role AS document_reviewer_role, d.reviewed_at AS document_reviewed_at, "
            "d.verified_at AS document_verified_at, d.is_current AS document_is_current "
            f"FROM periodic_review_evidence_links l "
            "LEFT JOIN documents d ON d.id = l.document_id "
            f"WHERE l.periodic_review_id IN ({placeholders}) ORDER BY l.id ASC",
            tuple(review_ids),
        ).fetchall()
        for row in link_rows:
            evidence_by_review.setdefault(row["periodic_review_id"], []).append(dict(row))

    return [
        build_review_projection(
            db,
            row,
            evidence_links=evidence_by_review.get(row["id"], []),
            application_row=applications_by_id.get(_row_get(row, "application_id")),
            assigned_officer_row=officers_by_id.get(_row_get(row, "assigned_officer")),
        )
        for row in rows
    ]


def projection_matches_queue_filter(projection: Dict[str, Any], queue_filter: Optional[str]) -> bool:
    queue = str(queue_filter or "").strip().lower()
    if not queue:
        return True
    queue_status = str(projection.get("queue_status") or "").strip().lower()
    if queue == "open":
        return queue_status in OPEN_QUEUE_FILTER_STATUSES
    return queue_status == queue


def latest_active_review_summary(db, application_id: str) -> Optional[Dict[str, Any]]:
    projections = list_review_projections(db, application_id=application_id, statuses=ACTIVE_REVIEW_STATES)
    return projections[0] if projections else None
