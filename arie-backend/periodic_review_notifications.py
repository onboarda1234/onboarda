from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from enhanced_requirements import validate_enhanced_requirement_document_link

CLIENT_STATUS_NOT_SENT = "not_sent"
CLIENT_STATUS_SENT = "sent"
CLIENT_STATUS_FAILED = "failed"
CLIENT_STATUS_REMINDER_DUE = "reminder_due"
CLIENT_STATUS_OVERDUE_NOTIFIED = "overdue_notified"
CLIENT_STATUS_SUPPRESSED = "suppressed"

OFFICER_ALERT_ACTIVE = "active"
OFFICER_ALERT_CLEARED = "cleared"

CHANNEL_PORTAL = "portal"
CHANNEL_EMAIL = "email"
CHANNEL_BOTH = "both"
DEFAULT_NOTIFICATION_CHANNEL = os.environ.get("PERIODIC_REVIEW_NOTIFICATION_CHANNEL", CHANNEL_PORTAL)

ACTIVE_REVIEW_STATES = {"pending", "in_progress", "awaiting_information", "pending_senior_review"}
TERMINAL_REVIEW_STATES = {"completed", "cancelled"}
ATTESTATION_READY_STATES = {"submitted", "not_required", "not_applicable", "waived"}
DOCUMENT_TERMINAL_STATUSES = {"accepted", "waived", "cancelled"}
REMINDER_INTERVAL_DAYS = (7, 14)

AUDIT_CLIENT_NOTIFICATION_SENT = "periodic_review_client_notification_sent"
AUDIT_CLIENT_NOTIFICATION_FAILED = "periodic_review_client_notification_failed"
AUDIT_CLIENT_REMINDER_SENT = "periodic_review_client_reminder_sent"
AUDIT_OVERDUE_NOTIFICATION_SENT = "periodic_review_overdue_notification_sent"
AUDIT_OFFICER_ALERT_CREATED = "periodic_review_officer_alert_created"
AUDIT_STATUS_UPDATED = "periodic_review_notification_status_updated"

FIXTURE_NOTIFICATION_SUPPRESSION_REASON = "fixture_application"
CANONICAL_FIXTURE_REVIEW_TRIGGER_TYPE = "pilot_canonical_fixture"
CANONICAL_FIXTURE_REVIEW_TRIGGER_SOURCE = "pilot_canonical_dataset"
CANONICAL_FIXTURE_REFERENCE_PREFIX = "RM-PILOT-"


logger = logging.getLogger(__name__)


def _row_get(row, key: str, default=None):
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


def _row_dict(row) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _table_columns(db, table: str) -> set:
    # DBConnection rolls back the active transaction after any Postgres
    # statement error. Avoid SQLite PRAGMA probes on Postgres connections so
    # audit column discovery cannot undo notification state updates.
    if getattr(db, "is_postgres", False):
        try:
            rows = db.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                (table,),
            ).fetchall()
            return {str(row["column_name"]) for row in rows}
        except Exception:
            return set()
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


def _now_utc(now: Optional[Any] = None) -> datetime:
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if isinstance(now, date):
        return datetime.combine(now, datetime.min.time(), tzinfo=timezone.utc)
    if isinstance(now, str) and now.strip():
        parsed = datetime.fromisoformat(now.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _iso(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _parse_dt(value: Any) -> Optional[datetime]:
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


def _parse_date(value: Any) -> Optional[date]:
    dt = _parse_dt(value)
    if dt:
        return dt.date()
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _json(value: Any) -> str:
    return json.dumps(value or {}, default=str, sort_keys=True)


def _audit_user(actor) -> Dict[str, str]:
    if isinstance(actor, dict):
        return {
            "sub": str(actor.get("sub") or actor.get("id") or "system"),
            "name": str(actor.get("name") or actor.get("full_name") or actor.get("sub") or "system"),
            "role": str(actor.get("role") or "system"),
        }
    text = str(actor or "system")
    return {"sub": text, "name": text, "role": "system"}


def _insert_audit(
    db,
    action: str,
    target: str,
    detail: Dict[str, Any],
    *,
    actor=None,
    before_state: Optional[Dict[str, Any]] = None,
    after_state: Optional[Dict[str, Any]] = None,
) -> None:
    user = _audit_user(actor)
    columns = _table_columns(db, "audit_log")
    if "before_state" in columns and "after_state" in columns:
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address, before_state, after_state) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                user["sub"],
                user["name"],
                user["role"],
                action,
                target,
                _json(detail),
                "",
                _json(before_state) if before_state is not None else None,
                _json(after_state) if after_state is not None else None,
            ),
        )
    else:
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) "
            "VALUES (?,?,?,?,?,?,?)",
            (user["sub"], user["name"], user["role"], action, target, _json(detail), ""),
        )


def _notification_state(review: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "client_notification_status": _row_get(review, "client_notification_status") or CLIENT_STATUS_NOT_SENT,
        "initial_notification_sent_at": _row_get(review, "initial_notification_sent_at"),
        "last_reminder_sent_at": _row_get(review, "last_reminder_sent_at"),
        "reminder_count": int(_row_get(review, "reminder_count", 0) or 0),
        "last_notification_error": _row_get(review, "last_notification_error"),
        "officer_alert_status": _row_get(review, "officer_alert_status"),
        "officer_alerted_at": _row_get(review, "officer_alerted_at"),
        "notification_channel": _row_get(review, "notification_channel") or DEFAULT_NOTIFICATION_CHANNEL,
        "next_reminder_due_at": _row_get(review, "next_reminder_due_at"),
    }


def _normalise_channel(channel: Optional[str]) -> str:
    value = str(channel or DEFAULT_NOTIFICATION_CHANNEL or CHANNEL_PORTAL).strip().lower()
    if value not in {CHANNEL_PORTAL, CHANNEL_EMAIL, CHANNEL_BOTH}:
        return CHANNEL_PORTAL
    return value


def _review_reference(review: Dict[str, Any]) -> str:
    return f"PR-{_row_get(review, 'id')}" if _row_get(review, "id") is not None else "Periodic Review"


def _is_terminal_review(review: Dict[str, Any]) -> bool:
    return str(_row_get(review, "status") or "").strip().lower() in TERMINAL_REVIEW_STATES


def _attestation_incomplete(review: Dict[str, Any]) -> bool:
    status = str(_row_get(review, "client_attestation_status") or "not_started").strip().lower()
    return status not in ATTESTATION_READY_STATES


def _document_ready(row: Dict[str, Any]) -> bool:
    status = str(row.get("status") or "").strip().lower()
    if status in {"waived", "cancelled"}:
        return True
    if row.get("linked_document_id") and row.get("linked_document_integrity_valid") is not True:
        return False
    if str(row.get("workflow_test_accepted") or "").strip().lower() in {"1", "true", "yes", "y"}:
        return True
    if not row.get("linked_document_id"):
        return False
    verification_status = str(row.get("document_verification_status") or "").strip().lower()
    review_status = str(row.get("document_review_status") or "").strip().lower()
    reviewer_role = str(row.get("document_reviewer_role") or "").strip().lower()
    review_comment = str(row.get("document_review_comment") or "").strip()
    if verification_status == "verified":
        return True
    return (
        verification_status == "flagged"
        and review_status in {"accepted", "approved"}
        and reviewer_role in {"admin", "sco"}
        and bool(review_comment)
    )


def periodic_review_document_notification_summary(db, review_id: int) -> Dict[str, Any]:
    if not review_id:
        return {
            "count": 0,
            "required_count": 0,
            "missing_count": 0,
            "review_required_count": 0,
            "outstanding_labels": [],
            "pending_review_labels": [],
        }
    columns = _table_columns(db, "application_enhanced_requirements")
    if not columns:
        return {
            "count": 0,
            "required_count": 0,
            "missing_count": 0,
            "review_required_count": 0,
            "outstanding_labels": [],
            "pending_review_labels": [],
        }
    display_select = "aer.requirement_display_type" if "requirement_display_type" in columns else "'evidence'"
    active_filter = "AND COALESCE(aer.active, 1) = 1" if "active" in columns else ""
    try:
        rows = db.execute(
            f"""
            SELECT aer.*,
                   {display_select} AS requirement_display_type,
                   d.verification_status AS document_verification_status,
                   d.review_status AS document_review_status,
                   d.reviewer_role AS document_reviewer_role,
                   d.review_comment AS document_review_comment
            FROM application_enhanced_requirements aer
            LEFT JOIN documents d
              ON d.id = aer.linked_document_id
             AND d.application_id = aer.application_id
            WHERE aer.linked_periodic_review_id = ?
              {active_filter}
            ORDER BY aer.id ASC
            """,
            (review_id,),
        ).fetchall()
    except Exception:
        rows = []
    evidence_rows = [
        _row_dict(row)
        for row in rows
        if str(_row_get(row, "requirement_display_type") or "evidence").strip().lower() == "evidence"
    ]
    required_rows = [row for row in evidence_rows if bool(row.get("mandatory"))]
    missing = []
    pending_review = []
    for row in required_rows:
        if row.get("linked_document_id"):
            _, link_integrity = validate_enhanced_requirement_document_link(
                db,
                row.get("application_id"),
                row,
                row.get("linked_document_id"),
            )
            row["linked_document_integrity_valid"] = bool(
                link_integrity.get("valid")
            )
        if _document_ready(row):
            continue
        label = row.get("requirement_label") or row.get("requirement_key") or "Required periodic review document"
        if (
            row.get("linked_document_id")
            and row.get("linked_document_integrity_valid") is not False
        ):
            pending_review.append(str(label))
        else:
            missing.append(str(label))
    return {
        "count": len(evidence_rows),
        "required_count": len(required_rows),
        "missing_count": len(missing),
        "review_required_count": len(pending_review),
        "outstanding_labels": missing,
        "pending_review_labels": pending_review,
    }


def _client_action_state(review: Dict[str, Any], document_summary: Dict[str, Any]) -> Optional[str]:
    if _is_terminal_review(review):
        return None
    if _attestation_incomplete(review):
        return "attestation_required"
    if int(document_summary.get("missing_count") or 0) > 0:
        return "documents_required"
    return None


def _client_action_label(action_state: Optional[str]) -> str:
    if action_state == "documents_required":
        return "Upload requested periodic review documents"
    if action_state == "attestation_required":
        return "Complete the periodic review attestation"
    return "No client action required"


def _reminder_due_at(initial_sent_at: Any, reminder_count: int) -> Optional[datetime]:
    initial = _parse_dt(initial_sent_at)
    if initial is None or reminder_count >= len(REMINDER_INTERVAL_DAYS):
        return None
    return initial + timedelta(days=REMINDER_INTERVAL_DAYS[reminder_count])


def _is_overdue(review: Dict[str, Any], now: datetime) -> bool:
    due = _parse_date(_row_get(review, "due_date") or _row_get(review, "next_review_date"))
    return bool(due and now.date() >= due)


def notification_projection_from_review(
    review_row,
    *,
    document_summary: Optional[Dict[str, Any]] = None,
    now: Optional[Any] = None,
    suppression: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    review = _row_dict(review_row)
    document_summary = document_summary or {}
    suppression = dict(suppression or {})
    current = _now_utc(now)
    state = _notification_state(review)
    action_state = _client_action_state(review, document_summary)
    effective_status = state["client_notification_status"] or CLIENT_STATUS_NOT_SENT
    next_due = _parse_dt(state.get("next_reminder_due_at"))
    if _is_terminal_review(review) or not action_state:
        effective_status = state["client_notification_status"] or CLIENT_STATUS_NOT_SENT
    elif effective_status not in {CLIENT_STATUS_FAILED, CLIENT_STATUS_OVERDUE_NOTIFIED} and next_due and current >= next_due:
        effective_status = CLIENT_STATUS_REMINDER_DUE
    workflow_suppressed = _is_terminal_review(review) or not action_state
    projection = {
        **state,
        "client_notification_status": effective_status,
        "client_notification_status_label": {
            CLIENT_STATUS_NOT_SENT: "Not sent",
            CLIENT_STATUS_SENT: "Sent",
            CLIENT_STATUS_FAILED: "Failed",
            CLIENT_STATUS_REMINDER_DUE: "Reminder due",
            CLIENT_STATUS_OVERDUE_NOTIFIED: "Overdue notified",
        }.get(effective_status, "Not sent"),
        "client_action_required": action_state,
        "client_action_required_label": _client_action_label(action_state),
        "notification_suppressed": workflow_suppressed or bool(suppression),
        "is_client_action_overdue": bool(action_state and _is_overdue(review, current)),
        "document_notification_summary": document_summary,
    }
    if suppression:
        # A fixture may retain historical failed-delivery fields from before
        # the guard existed.  Keep the database untouched, but do not present
        # that obsolete attempt as the current synthetic-fixture state.
        projection.update({
            "client_notification_status": CLIENT_STATUS_SUPPRESSED,
            "client_notification_status_label": "Suppressed — synthetic fixture",
            "initial_notification_sent_at": None,
            "last_reminder_sent_at": None,
            "reminder_count": 0,
            "last_notification_error": None,
            "next_reminder_due_at": None,
            "notification_suppression_reason": suppression.get("reason"),
            "notification_suppression_evidence": suppression,
        })
    return projection


def _load_application_context(db, review: Dict[str, Any]) -> Dict[str, Any]:
    row = db.execute(
        """
        SELECT a.id, a.ref, a.company_name, a.client_id, a.is_fixture,
               c.email AS client_email, c.company_name AS client_company_name
        FROM applications a
        LEFT JOIN clients c ON c.id = a.client_id
        WHERE a.id = ?
        """,
        (_row_get(review, "application_id"),),
    ).fetchone()
    return _row_dict(row)


def _canonical_fixture_review_marker_matches(
    review: Dict[str, Any], app: Dict[str, Any]
) -> bool:
    application_ref = str(app.get("ref") or "").strip()
    if not application_ref.startswith(CANONICAL_FIXTURE_REFERENCE_PREFIX):
        return False
    reference_number = application_ref[len(CANONICAL_FIXTURE_REFERENCE_PREFIX):]
    if len(reference_number) != 3 or not reference_number.isdigit():
        return False
    return (
        str(_row_get(review, "trigger_type") or "").strip()
        == CANONICAL_FIXTURE_REVIEW_TRIGGER_TYPE
        and str(_row_get(review, "trigger_source") or "").strip()
        == CANONICAL_FIXTURE_REVIEW_TRIGGER_SOURCE
        and str(_row_get(review, "trigger_reason") or "").strip()
        == f"{application_ref}:PERIODIC"
    )


def fixture_notification_suppression(
    review: Dict[str, Any], app: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Return non-sensitive evidence when fixture delivery must be suppressed."""
    if not _truthy(app.get("is_fixture")):
        return None
    return {
        "suppressed": True,
        "reason": FIXTURE_NOTIFICATION_SUPPRESSION_REASON,
        "policy": "fixture_applications_do_not_receive_periodic_review_notifications",
        "application_id": app.get("id") or _row_get(review, "application_id"),
        "application_ref": app.get("ref"),
        "periodic_review_id": _row_get(review, "id"),
        "canonical_review_marker_match": _canonical_fixture_review_marker_matches(
            review, app
        ),
        "trigger_type": _row_get(review, "trigger_type"),
        "trigger_source": _row_get(review, "trigger_source"),
    }


def _portal_link(application_id: str) -> Optional[str]:
    base = (
        os.environ.get("PORTAL_BASE_URL")
        or os.environ.get("PUBLIC_APP_URL")
        or os.environ.get("ALLOWED_ORIGIN")
        or ""
    ).rstrip("/")
    if not base:
        return None
    return f"{base}/portal?periodic_review_application={application_id}"


def _notification_payload(
    *,
    notification_type: str,
    action_state: str,
    review: Dict[str, Any],
    app: Dict[str, Any],
    document_summary: Dict[str, Any],
) -> Dict[str, Any]:
    company = app.get("company_name") or app.get("client_company_name") or _row_get(review, "client_name") or "your company"
    review_ref = _review_reference(review)
    due_date = _row_get(review, "due_date") or _row_get(review, "next_review_date") or "not set"
    app_ref = app.get("ref") or _row_get(review, "application_id")
    link = _portal_link(app.get("id") or _row_get(review, "application_id"))
    if notification_type == "periodic_review_documents_required":
        subject = "Action Required: Periodic Review Documents Outstanding"
        title = "Periodic Review Documents Required"
        action_text = "Please upload the requested documents in the client portal."
    elif notification_type == "periodic_review_reminder":
        subject = "Reminder: Periodic Review Pending"
        title = "Reminder: Periodic Review Pending"
        action_text = _client_action_label(action_state) + "."
    elif notification_type == "periodic_review_overdue":
        subject = "Periodic Review Overdue"
        title = "Periodic Review Overdue"
        action_text = _client_action_label(action_state) + "."
    else:
        subject = "Periodic Review Required"
        title = "Periodic Review Required"
        action_text = _client_action_label(action_state) + "."
    body_lines = [
        f"A periodic review is required for {company}.",
        f"Application: {app_ref}",
        f"Review reference: {review_ref}",
        f"Action required: {action_text}",
        f"Due date: {due_date}",
    ]
    if link:
        body_lines.append(f"Portal link: {link}")
    body_lines.append("This notification contains only Periodic Review action details.")
    return {
        "notification_type": notification_type,
        "subject": subject,
        "title": title,
        "message": "\n".join(body_lines),
        "documents_list": list(document_summary.get("outstanding_labels") or []),
    }


def _notification_already_sent(db, app: Dict[str, Any], review: Dict[str, Any], notification_type: str) -> bool:
    client_id = app.get("client_id")
    if not client_id:
        return False
    try:
        row = db.execute(
            """
            SELECT id FROM client_notifications
            WHERE application_id = ?
              AND client_id = ?
              AND notification_type = ?
              AND message LIKE ?
            ORDER BY id DESC LIMIT 1
            """,
            (app.get("id"), client_id, notification_type, f"%{_review_reference(review)}%"),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _insert_client_notification(db, app: Dict[str, Any], payload: Dict[str, Any], now: datetime) -> None:
    db.execute(
        """
        INSERT INTO client_notifications
            (application_id, client_id, notification_type, title, message, documents_list, read_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app.get("id"),
            app.get("client_id"),
            payload["notification_type"],
            payload["title"],
            payload["message"],
            json.dumps(payload.get("documents_list") or [], sort_keys=True) if payload.get("documents_list") else None,
            False,
            now.isoformat(),
        ),
    )


def _dispatch_client_notification(
    db,
    *,
    app: Dict[str, Any],
    payload: Dict[str, Any],
    channel: str,
    email_sender: Optional[Callable[[str, str, str], bool]],
    now: datetime,
) -> Tuple[bool, List[str], Optional[str], List[str]]:
    channel = _normalise_channel(channel)
    delivered_channels: List[str] = []
    attempted_channels: List[str] = []
    errors: List[str] = []
    if channel in {CHANNEL_PORTAL, CHANNEL_BOTH}:
        attempted_channels.append(CHANNEL_PORTAL)
        if not app.get("client_id"):
            errors.append("portal: no client is linked to this application")
        else:
            _insert_client_notification(db, app, payload, now)
            delivered_channels.append(CHANNEL_PORTAL)
    if channel in {CHANNEL_EMAIL, CHANNEL_BOTH}:
        attempted_channels.append(CHANNEL_EMAIL)
        email = app.get("client_email")
        if not email:
            errors.append("email: client email is missing")
        elif email_sender is None:
            errors.append("email: sender is not configured")
        else:
            try:
                if bool(email_sender(email, payload["subject"], payload["message"])):
                    delivered_channels.append(CHANNEL_EMAIL)
                else:
                    errors.append("email: sender returned false")
            except Exception as exc:
                errors.append(f"email: {exc}")
    delivered = bool(delivered_channels)
    return delivered, delivered_channels, "; ".join(errors) if errors else None, attempted_channels


def _update_review_notification_state(
    db,
    review: Dict[str, Any],
    changes: Dict[str, Any],
    *,
    app: Dict[str, Any],
    actor=None,
    source_surface: str,
) -> Dict[str, Any]:
    if not changes:
        return review
    before = _notification_state(review)
    assignments = ", ".join(f"{column} = ?" for column in changes.keys())
    params = list(changes.values()) + [_row_get(review, "id")]
    db.execute(
        f"UPDATE periodic_reviews SET {assignments}, state_changed_at = COALESCE(state_changed_at, ?) WHERE id = ?",
        list(changes.values()) + [changes.get("last_reminder_sent_at") or changes.get("initial_notification_sent_at") or _iso(_now_utc()), _row_get(review, "id")],
    )
    updated = dict(review)
    updated.update(changes)
    after = _notification_state(updated)
    _insert_audit(
        db,
        AUDIT_STATUS_UPDATED,
        app.get("ref") or f"periodic_review:{_row_get(review, 'id')}",
        {
            "periodic_review_id": _row_get(review, "id"),
            "application_id": app.get("id") or _row_get(review, "application_id"),
            "client_id": app.get("client_id"),
            "source_surface": source_surface,
            "before_status": before.get("client_notification_status"),
            "after_status": after.get("client_notification_status"),
            "reminder_count": after.get("reminder_count"),
        },
        actor=actor,
        before_state=before,
        after_state=after,
    )
    return updated


def _audit_notification(
    db,
    *,
    action: str,
    review: Dict[str, Any],
    app: Dict[str, Any],
    payload: Dict[str, Any],
    channel: str,
    delivered_channels: Sequence[str],
    error: Optional[str],
    actor=None,
    source_surface: str,
    now: datetime,
) -> None:
    _insert_audit(
        db,
        action,
        app.get("ref") or f"periodic_review:{_row_get(review, 'id')}",
        {
            "periodic_review_id": _row_get(review, "id"),
            "application_id": app.get("id") or _row_get(review, "application_id"),
            "client_id": app.get("client_id"),
            "notification_type": payload["notification_type"],
            "channel": channel,
            "delivered_channels": list(delivered_channels),
            "recipient_type": "client",
            "sent_at": now.isoformat(),
            "reminder_count": int(_row_get(review, "reminder_count", 0) or 0),
            "failure_reason": error,
            "source_surface": source_surface,
        },
        actor=actor,
    )


def _send_for_action(
    db,
    *,
    review: Dict[str, Any],
    app: Dict[str, Any],
    document_summary: Dict[str, Any],
    action_state: str,
    notification_type: str,
    channel: str,
    email_sender: Optional[Callable[[str, str, str], bool]],
    actor=None,
    source_surface: str,
    now: datetime,
) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    payload = _notification_payload(
        notification_type=notification_type,
        action_state=action_state,
        review=review,
        app=app,
        document_summary=document_summary,
    )
    delivered, delivered_channels, error, _attempted = _dispatch_client_notification(
        db,
        app=app,
        payload=payload,
        channel=channel,
        email_sender=email_sender,
        now=now,
    )
    if delivered:
        audit_action = (
            AUDIT_CLIENT_REMINDER_SENT
            if notification_type == "periodic_review_reminder"
            else AUDIT_OVERDUE_NOTIFICATION_SENT
            if notification_type == "periodic_review_overdue"
            else AUDIT_CLIENT_NOTIFICATION_SENT
        )
    else:
        audit_action = AUDIT_CLIENT_NOTIFICATION_FAILED
    _audit_notification(
        db,
        action=audit_action,
        review=review,
        app=app,
        payload=payload,
        channel=channel,
        delivered_channels=delivered_channels,
        error=error,
        actor=actor,
        source_surface=source_surface,
        now=now,
    )
    return delivered, error, payload


def _officer_alert_required(review: Dict[str, Any], document_summary: Dict[str, Any], action_state: Optional[str], now: datetime) -> bool:
    if _is_terminal_review(review):
        return False
    if int(document_summary.get("review_required_count") or 0) > 0:
        return True
    reminder_count = int(_row_get(review, "reminder_count", 0) or 0)
    return bool(action_state and (_is_overdue(review, now) or reminder_count >= len(REMINDER_INTERVAL_DAYS)))


def _maybe_update_officer_alert(
    db,
    *,
    review: Dict[str, Any],
    app: Dict[str, Any],
    document_summary: Dict[str, Any],
    action_state: Optional[str],
    actor=None,
    source_surface: str,
    now: datetime,
) -> Tuple[Dict[str, Any], Optional[str]]:
    required = _officer_alert_required(review, document_summary, action_state, now)
    current = str(_row_get(review, "officer_alert_status") or "").strip().lower()
    if required and current != OFFICER_ALERT_ACTIVE:
        updated = _update_review_notification_state(
            db,
            review,
            {
                "officer_alert_status": OFFICER_ALERT_ACTIVE,
                "officer_alerted_at": now.isoformat(),
            },
            app=app,
            actor=actor,
            source_surface=source_surface,
        )
        _insert_audit(
            db,
            AUDIT_OFFICER_ALERT_CREATED,
            app.get("ref") or f"periodic_review:{_row_get(review, 'id')}",
            {
                "periodic_review_id": _row_get(review, "id"),
                "application_id": app.get("id") or _row_get(review, "application_id"),
                "client_id": app.get("client_id"),
                "notification_type": "officer_alert",
                "channel": "queue",
                "recipient_type": "officer",
                "sent_at": now.isoformat(),
                "reminder_count": int(_row_get(updated, "reminder_count", 0) or 0),
                "source_surface": source_surface,
                "reason": "client_action_overdue_or_stuck" if action_state else "documents_awaiting_officer_review",
            },
            actor=actor,
        )
        return updated, OFFICER_ALERT_ACTIVE
    if not required and current == OFFICER_ALERT_ACTIVE:
        updated = _update_review_notification_state(
            db,
            review,
            {"officer_alert_status": OFFICER_ALERT_CLEARED},
            app=app,
            actor=actor,
            source_surface=source_surface,
        )
        return updated, OFFICER_ALERT_CLEARED
    return review, None


def process_periodic_review_notification(
    db,
    review_row,
    *,
    now: Optional[Any] = None,
    channel: Optional[str] = None,
    email_sender: Optional[Callable[[str, str, str], bool]] = None,
    actor=None,
    source_surface: str = "periodic_review_notification_service",
) -> Dict[str, Any]:
    now_dt = _now_utc(now)
    review = _row_dict(review_row)
    if not review:
        raise ValueError("review_row is required")
    app = _load_application_context(db, review)
    document_summary = periodic_review_document_notification_summary(db, int(_row_get(review, "id") or 0))
    action_state = _client_action_state(review, document_summary)
    channel = _normalise_channel(channel or _row_get(review, "notification_channel") or DEFAULT_NOTIFICATION_CHANNEL)
    sent_events: List[str] = []
    errors: List[str] = []

    suppression = fixture_notification_suppression(review, app)
    if suppression:
        # This guard intentionally precedes every dispatch, review-state update,
        # officer-alert update and audit path. Fixture sweeps are observable in
        # logs and the returned projection, but remain database-write-free.
        logger.info(
            "periodic-review-notification-suppressed %s",
            _json(suppression),
        )
        return {
            "review_id": _row_get(review, "id"),
            "application_id": app.get("id") or _row_get(review, "application_id"),
            "client_id": app.get("client_id"),
            "client_action_required": action_state,
            "client_action_required_label": _client_action_label(action_state),
            "sent_events": sent_events,
            "errors": errors,
            "officer_alert_event": None,
            "notification_suppressed": True,
            "notification_suppression_reason": suppression["reason"],
            "notification_suppression_evidence": suppression,
            "notification": notification_projection_from_review(
                review,
                document_summary=document_summary,
                now=now_dt,
                suppression=suppression,
            ),
            "next_reminder_due_at": None,
        }

    if _is_terminal_review(review) or not action_state:
        next_due = None
        if _row_get(review, "next_reminder_due_at"):
            review = _update_review_notification_state(
                db,
                review,
                {"next_reminder_due_at": None, "last_notification_error": None},
                app=app,
                actor=actor,
                source_surface=source_surface,
            )
        review, officer_event = _maybe_update_officer_alert(
            db,
            review=review,
            app=app,
            document_summary=document_summary,
            action_state=action_state,
            actor=actor,
            source_surface=source_surface,
            now=now_dt,
        )
        return {
            "review_id": _row_get(review, "id"),
            "client_action_required": action_state,
            "sent_events": sent_events,
            "errors": errors,
            "officer_alert_event": officer_event,
            "notification": notification_projection_from_review(review, document_summary=document_summary, now=now_dt),
            "next_reminder_due_at": next_due,
        }

    sent_primary_this_run = False
    initial_sent_at = _row_get(review, "initial_notification_sent_at")
    current_status = str(_row_get(review, "client_notification_status") or CLIENT_STATUS_NOT_SENT).strip().lower()
    primary_type = "periodic_review_documents_required" if action_state == "documents_required" else "periodic_review_required"

    if not initial_sent_at:
        delivered, error, _payload = _send_for_action(
            db,
            review=review,
            app=app,
            document_summary=document_summary,
            action_state=action_state,
            notification_type=primary_type,
            channel=channel,
            email_sender=email_sender,
            actor=actor,
            source_surface=source_surface,
            now=now_dt,
        )
        sent_primary_this_run = True
        if delivered:
            review = _update_review_notification_state(
                db,
                review,
                {
                    "client_notification_status": CLIENT_STATUS_SENT,
                    "initial_notification_sent_at": now_dt.isoformat(),
                    "last_notification_error": None,
                    "notification_channel": channel,
                },
                app=app,
                actor=actor,
                source_surface=source_surface,
            )
            sent_events.append(primary_type)
        else:
            review = _update_review_notification_state(
                db,
                review,
                {
                    "client_notification_status": CLIENT_STATUS_FAILED,
                    "last_notification_error": error or "Notification delivery failed",
                    "notification_channel": channel,
                },
                app=app,
                actor=actor,
                source_surface=source_surface,
            )
            errors.append(error or "Notification delivery failed")
    elif action_state == "documents_required" and not _notification_already_sent(db, app, review, "periodic_review_documents_required"):
        delivered, error, _payload = _send_for_action(
            db,
            review=review,
            app=app,
            document_summary=document_summary,
            action_state=action_state,
            notification_type="periodic_review_documents_required",
            channel=channel,
            email_sender=email_sender,
            actor=actor,
            source_surface=source_surface,
            now=now_dt,
        )
        sent_primary_this_run = True
        if delivered:
            review = _update_review_notification_state(
                db,
                review,
                {
                    "client_notification_status": CLIENT_STATUS_SENT,
                    "last_notification_error": None,
                    "notification_channel": channel,
                },
                app=app,
                actor=actor,
                source_surface=source_surface,
            )
            sent_events.append("periodic_review_documents_required")
        else:
            review = _update_review_notification_state(
                db,
                review,
                {
                    "client_notification_status": CLIENT_STATUS_FAILED,
                    "last_notification_error": error or "Notification delivery failed",
                    "notification_channel": channel,
                },
                app=app,
                actor=actor,
                source_surface=source_surface,
            )
            errors.append(error or "Notification delivery failed")

    reminder_count = int(_row_get(review, "reminder_count", 0) or 0)
    initial_sent_at = _row_get(review, "initial_notification_sent_at")
    overdue_due = bool(initial_sent_at and _is_overdue(review, now_dt))
    reminder_due = _reminder_due_at(initial_sent_at, reminder_count)
    if not sent_primary_this_run and initial_sent_at and current_status != CLIENT_STATUS_FAILED:
        if overdue_due and current_status != CLIENT_STATUS_OVERDUE_NOTIFIED:
            delivered, error, _payload = _send_for_action(
                db,
                review=review,
                app=app,
                document_summary=document_summary,
                action_state=action_state,
                notification_type="periodic_review_overdue",
                channel=channel,
                email_sender=email_sender,
                actor=actor,
                source_surface=source_surface,
                now=now_dt,
            )
            if delivered:
                review = _update_review_notification_state(
                    db,
                    review,
                    {
                        "client_notification_status": CLIENT_STATUS_OVERDUE_NOTIFIED,
                        "last_reminder_sent_at": now_dt.isoformat(),
                        "reminder_count": reminder_count + 1,
                        "last_notification_error": None,
                    },
                    app=app,
                    actor=actor,
                    source_surface=source_surface,
                )
                sent_events.append("periodic_review_overdue")
            else:
                review = _update_review_notification_state(
                    db,
                    review,
                    {
                        "client_notification_status": CLIENT_STATUS_FAILED,
                        "last_notification_error": error or "Notification delivery failed",
                    },
                    app=app,
                    actor=actor,
                    source_surface=source_surface,
                )
                errors.append(error or "Notification delivery failed")
        elif reminder_due and now_dt >= reminder_due:
            delivered, error, _payload = _send_for_action(
                db,
                review=review,
                app=app,
                document_summary=document_summary,
                action_state=action_state,
                notification_type="periodic_review_reminder",
                channel=channel,
                email_sender=email_sender,
                actor=actor,
                source_surface=source_surface,
                now=now_dt,
            )
            if delivered:
                review = _update_review_notification_state(
                    db,
                    review,
                    {
                        "client_notification_status": CLIENT_STATUS_SENT,
                        "last_reminder_sent_at": now_dt.isoformat(),
                        "reminder_count": reminder_count + 1,
                        "last_notification_error": None,
                    },
                    app=app,
                    actor=actor,
                    source_surface=source_surface,
                )
                sent_events.append("periodic_review_reminder")
            else:
                review = _update_review_notification_state(
                    db,
                    review,
                    {
                        "client_notification_status": CLIENT_STATUS_FAILED,
                        "last_notification_error": error or "Notification delivery failed",
                    },
                    app=app,
                    actor=actor,
                    source_surface=source_surface,
                )
                errors.append(error or "Notification delivery failed")

    reminder_count = int(_row_get(review, "reminder_count", 0) or 0)
    next_due = _reminder_due_at(_row_get(review, "initial_notification_sent_at"), reminder_count)
    if next_due and _is_overdue(review, now_dt):
        next_due = None
    if _iso(next_due) != _row_get(review, "next_reminder_due_at"):
        review = _update_review_notification_state(
            db,
            review,
            {"next_reminder_due_at": _iso(next_due)},
            app=app,
            actor=actor,
            source_surface=source_surface,
        )

    review, officer_event = _maybe_update_officer_alert(
        db,
        review=review,
        app=app,
        document_summary=document_summary,
        action_state=action_state,
        actor=actor,
        source_surface=source_surface,
        now=now_dt,
    )
    return {
        "review_id": _row_get(review, "id"),
        "application_id": app.get("id") or _row_get(review, "application_id"),
        "client_id": app.get("client_id"),
        "client_action_required": action_state,
        "client_action_required_label": _client_action_label(action_state),
        "sent_events": sent_events,
        "errors": errors,
        "officer_alert_event": officer_event,
        "notification": notification_projection_from_review(review, document_summary=document_summary, now=now_dt),
        "next_reminder_due_at": _iso(next_due),
    }


def run_periodic_review_notification_sweep(
    db,
    *,
    review_ids: Optional[Iterable[int]] = None,
    now: Optional[Any] = None,
    channel: Optional[str] = None,
    email_sender: Optional[Callable[[str, str, str], bool]] = None,
    actor=None,
    source_surface: str = "periodic_review_notification_service",
) -> Dict[str, Any]:
    params: List[Any] = []
    sql = "SELECT * FROM periodic_reviews WHERE LOWER(COALESCE(status, 'pending')) IN (" + ",".join("?" for _ in ACTIVE_REVIEW_STATES) + ")"
    params.extend(sorted(ACTIVE_REVIEW_STATES))
    cleaned_ids = [int(rid) for rid in dict.fromkeys(review_ids or []) if rid]
    if cleaned_ids:
        sql += " AND id IN (" + ",".join("?" for _ in cleaned_ids) + ")"
        params.extend(cleaned_ids)
    sql += " ORDER BY due_date ASC, id ASC"
    rows = db.execute(sql, tuple(params)).fetchall()
    results = [
        process_periodic_review_notification(
            db,
            row,
            now=now,
            channel=channel,
            email_sender=email_sender,
            actor=actor,
            source_surface=source_surface,
        )
        for row in rows
    ]
    return {
        "processed": len(results),
        "sent_count": sum(len(item.get("sent_events") or []) for item in results),
        "failed_count": sum(1 for item in results if item.get("errors")),
        "officer_alert_count": sum(1 for item in results if item.get("officer_alert_event") == OFFICER_ALERT_ACTIVE),
        "suppressed_count": sum(
            1 for item in results if item.get("notification_suppressed") is True
        ),
        "results": results,
    }
