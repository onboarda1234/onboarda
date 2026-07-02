from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from enhanced_requirements import (
    decorate_application_requirements_for_backoffice,
    serialize_application_requirement,
    update_application_enhanced_requirement,
)

logger = logging.getLogger("arie.monitoring_document_refresh")


DOCUMENT_REFRESH_GENERATION_SOURCE = "monitoring_document_expiry_refresh"
DOCUMENT_REFRESH_TRIGGER_CATEGORY = "monitoring"
DEFAULT_DOCUMENT_REFRESH_DUE_DAYS = 14
DOCUMENT_ALERT_TYPES = {
    "document_expired",
    "document_expiring_soon",
    "document_stale",
    "document_expiry_missing",
    "document_expiry",
    "missing_document_refresh",
}
ACTIVE_REQUEST_STATUSES = {"requested", "uploaded", "under_review", "rejected"}
TERMINAL_ALERT_STATUSES = {"resolved", "waived", "dismissed", "routed_to_edd", "routed_to_review"}


class MonitoringDocumentRefreshError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        self.status_code = status_code
        super().__init__(message)


def _row_dict(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return row


def _row_get(row, key, default=None):
    if row is None:
        return default
    if hasattr(row, "get"):
        value = row.get(key, default)
        return value if value is not None else default
    try:
        value = row[key]
    except Exception:
        return default
    return value if value is not None else default


def _load_json(raw, default):
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _date_only(value):
    if value in (None, ""):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return text.split("T", 1)[0].split(" ", 1)[0]


def _canonical(value):
    return re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")


def _label(value):
    text = str(value or "").strip().replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    return text.title() if text else "Document"


def _document_id_from_alert(alert):
    source = str(_row_get(alert, "source_reference") or "").strip()
    if source.startswith("document:"):
        return source.split(":", 1)[1].strip() or None
    payload = _load_json(source, {})
    if isinstance(payload, dict):
        for key in ("document_id", "doc_id", "monitoring_document_id"):
            if payload.get(key):
                return str(payload[key]).strip()
    return None


def _alert_type(alert):
    return _canonical(_row_get(alert, "alert_type") or _row_get(alert, "type"))


def is_document_refresh_alert(alert) -> bool:
    alert_type = _alert_type(alert)
    if alert_type in DOCUMENT_ALERT_TYPES:
        return True
    summary = str(_row_get(alert, "summary") or "").lower()
    return "document expir" in summary or "document refresh" in summary or "missing document" in summary


def _fetch_alert(db, alert_id):
    row = db.execute(
        """
        SELECT ma.*,
               app.ref AS application_ref,
               app.company_name AS application_company_name,
               app.client_id AS application_client_id,
               c.email AS client_email,
               c.company_name AS client_company_name
          FROM monitoring_alerts ma
     LEFT JOIN applications app ON app.id = ma.application_id
     LEFT JOIN clients c ON c.id = app.client_id
         WHERE ma.id = ?
        """,
        (alert_id,),
    ).fetchone()
    return _row_dict(row)


def _fetch_document(db, document_id, application_id):
    if not document_id:
        return None
    row = db.execute(
        """
        SELECT *
          FROM documents
         WHERE id = ?
           AND application_id = ?
         LIMIT 1
        """,
        (document_id, application_id),
    ).fetchone()
    return _row_dict(row)


def _document_owner(db, document):
    document = document or {}
    person_id = str(document.get("person_id") or "").strip()
    if not person_id:
        return {"name": "", "type": "company", "id": ""}
    app_id = document.get("application_id")
    for table, person_type in (("directors", "director"), ("ubos", "ubo")):
        try:
            row = db.execute(
                f"""
                SELECT id, person_key, full_name
                  FROM {table}
                 WHERE application_id = ?
                   AND (id = ? OR person_key = ?)
                 LIMIT 1
                """,
                (app_id, person_id, person_id),
            ).fetchone()
        except Exception:
            row = None
        if row:
            return {
                "name": _row_get(row, "full_name") or person_id,
                "type": person_type,
                "id": _row_get(row, "id") or person_id,
                "person_key": _row_get(row, "person_key") or "",
            }
    return {"name": person_id, "type": "person", "id": person_id}


def _expiry_date(document, alert):
    document = document or {}
    for key in ("expiry_date", "valid_until"):
        if document.get(key):
            return _date_only(document.get(key))
    verification = _load_json(document.get("verification_results"), {})
    if isinstance(verification, dict):
        for key in ("expiry_date", "expiry", "validity_to", "valid_until"):
            if verification.get(key):
                return _date_only(verification.get(key))
    source_payload = _load_json(_row_get(alert, "source_reference"), {})
    if isinstance(source_payload, dict):
        for key in ("expiry_date", "expires_at", "document_expiry"):
            if source_payload.get(key):
                return _date_only(source_payload.get(key))
    return ""


def _request_reason(alert):
    alert_type = _alert_type(alert)
    if alert_type in ("document_expired", "document_expiry"):
        return "expired"
    if alert_type == "document_expiring_soon":
        return "expiring"
    if alert_type == "document_expiry_missing":
        return "expiry missing"
    return "refresh required"


def _active_request_for_alert(db, alert_id):
    row = db.execute(
        """
        SELECT aer.*
          FROM application_enhanced_requirements aer
         WHERE aer.monitoring_alert_id = ?
           AND aer.active = 1
           AND LOWER(COALESCE(aer.status, 'generated')) IN ('requested','uploaded','under_review','rejected')
         ORDER BY aer.id DESC
         LIMIT 1
        """,
        (alert_id,),
    ).fetchone()
    return serialize_application_requirement(row)


def _request_for_alert(db, alert_id):
    row = db.execute(
        """
        SELECT aer.*
          FROM application_enhanced_requirements aer
         WHERE aer.monitoring_alert_id = ?
           AND aer.active = 1
         ORDER BY aer.id DESC
         LIMIT 1
        """,
        (alert_id,),
    ).fetchone()
    return serialize_application_requirement(row)


def _linked_document_summary(db, requirement):
    doc_id = (requirement or {}).get("linked_document_id")
    app_id = (requirement or {}).get("application_id")
    if not doc_id or not app_id:
        return None
    row = db.execute(
        """
        SELECT id, doc_name, doc_type, uploaded_at, review_status, verification_status,
               verification_results, slot_key, is_current, version, superseded_at,
               superseded_by_document_id
          FROM documents
         WHERE id = ? AND application_id = ?
        """,
        (doc_id, app_id),
    ).fetchone()
    doc = _row_dict(row)
    if not doc:
        return None
    metadata = _load_json(doc.get("verification_results"), {})
    if isinstance(metadata, dict):
        doc["verification_results"] = metadata
        doc["upload_source"] = metadata.get("upload_source") or (
            "client_portal" if metadata.get("client_submitted") else "back_office" if metadata.get("officer_uploaded") else ""
        )
        doc["source_note"] = metadata.get("source_note") or ""
        doc["previous_document_id"] = metadata.get("previous_document_id") or metadata.get("monitoring_document_id") or ""
    return doc


def _latest_notification_status(db, alert_id):
    try:
        row = db.execute(
            """
            SELECT action, detail, timestamp
              FROM audit_log
             WHERE target = ?
               AND action IN ('document_request_notification_sent', 'document_request_notification_failed')
             ORDER BY id DESC
             LIMIT 1
            """,
            (f"monitoring_alert:{alert_id}",),
        ).fetchone()
    except Exception:
        row = None
    if not row:
        return {"status": "not_attempted", "reason": "no_notification_attempt_recorded"}
    detail = _load_json(_row_get(row, "detail"), {})
    status = str(detail.get("email_status") or "").strip().lower()
    if _row_get(row, "action") == "document_request_notification_sent":
        status = "sent"
    elif not status:
        status = "failed"
    return {
        "status": status,
        "reason": detail.get("email_reason") or detail.get("reason") or "",
        "client_notification_id": detail.get("notification_id"),
        "timestamp": _row_get(row, "timestamp"),
    }


def document_refresh_context(db, alert_id_or_row) -> Dict[str, Any]:
    alert = _row_dict(alert_id_or_row)
    if not isinstance(alert, dict) or "summary" not in alert:
        alert = _fetch_alert(db, alert_id_or_row)
    elif alert.get("id") and (
        not alert.get("application_client_id")
        or not alert.get("application_ref")
        or not alert.get("client_email")
    ):
        full_alert = _fetch_alert(db, alert.get("id"))
        if full_alert:
            merged = dict(full_alert)
            for key, value in alert.items():
                if value not in (None, ""):
                    merged[key] = value
            alert = merged
    if not alert:
        return {"available": False, "reason": "alert_not_found"}
    if not is_document_refresh_alert(alert):
        return {"available": False, "reason": "not_document_refresh_alert"}

    app_id = alert.get("application_id")
    document_id = _document_id_from_alert(alert)
    document = _fetch_document(db, document_id, app_id) if app_id else None
    owner = _document_owner(db, document) if document else {"name": "", "type": "company", "id": ""}
    doc_type = (document or {}).get("doc_type") or ""
    doc_type_label = _label(doc_type or "document")
    expiry_date = _expiry_date(document, alert)
    requirement = _request_for_alert(db, alert.get("id"))
    decorated = []
    if requirement:
        try:
            decorated = decorate_application_requirements_for_backoffice(db, {"id": app_id}, [requirement])
        except Exception:
            decorated = [requirement]
    request_item = decorated[0] if decorated else requirement
    if request_item:
        linked_doc = _linked_document_summary(db, request_item)
        if linked_doc:
            request_item["linked_document"] = linked_doc
    due_date = (request_item or {}).get("due_date") or (
        (request_item or {}).get("trigger_context") or {}
    ).get("due_date")
    return {
        "available": True,
        "alert_id": alert.get("id"),
        "application_id": app_id,
        "application_ref": alert.get("application_ref"),
        "client_id": alert.get("application_client_id"),
        "document": {
            "id": document_id or "",
            "type": doc_type or "",
            "type_label": doc_type_label,
            "name": (document or {}).get("doc_name") or doc_type_label,
            "owner": owner.get("name") or alert.get("application_company_name") or alert.get("client_name") or "",
            "owner_type": owner.get("type") or "company",
            "expiry_date": expiry_date,
        },
        "request": request_item,
        "request_status": (request_item or {}).get("status") or "",
        "due_date": due_date or "",
        "request_reason": _request_reason(alert),
        "notification": _latest_notification_status(db, alert.get("id")),
        "has_active_request": bool(request_item and str(request_item.get("status") or "").lower() in ACTIVE_REQUEST_STATUSES),
    }


def _audit(audit_writer, user, action, alert_id, payload, *, db, before_state=None, after_state=None):
    audit_writer(
        dict(user or {}),
        action,
        f"monitoring_alert:{alert_id}",
        json.dumps(payload, default=str, sort_keys=True),
        db=db,
        before_state=before_state,
        after_state=after_state,
        commit=False,
    )


def _notification_exists(db, app_id, client_id, label):
    if not client_id:
        return False
    rows = db.execute(
        """
        SELECT id, documents_list
          FROM client_notifications
         WHERE application_id = ?
           AND client_id = ?
           AND notification_type = 'updated_document_required'
         ORDER BY id DESC
        """,
        (app_id, client_id),
    ).fetchall()
    needle = str(label or "").strip()
    for row in rows:
        raw = _row_get(row, "documents_list") or ""
        items = _load_json(raw, [])
        if isinstance(items, list) and needle and needle in [str(item) for item in items]:
            return True
    return False


def _insert_client_notification(db, alert, requirement, label, message, due_date):
    client_id = alert.get("application_client_id")
    app_id = alert.get("application_id")
    if not client_id:
        return None
    if _notification_exists(db, app_id, client_id, label):
        return None
    documents_list = [label]
    db.execute(
        """
        INSERT INTO client_notifications
            (application_id, client_id, notification_type, title, message, documents_list, read_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            app_id,
            client_id,
            "updated_document_required",
            "Updated document required",
            message,
            json.dumps(documents_list, sort_keys=True),
            False,
        ),
    )
    row = db.execute("SELECT * FROM client_notifications ORDER BY id DESC LIMIT 1").fetchone()
    return _row_dict(row)


def request_updated_document(
    db,
    alert_id,
    *,
    user,
    audit_writer,
    email_sender=None,
    due_date=None,
    notify_client=True,
    audience="client",
):
    alert = _fetch_alert(db, alert_id)
    if not alert:
        raise MonitoringDocumentRefreshError("Alert not found", 404)
    if not is_document_refresh_alert(alert):
        raise MonitoringDocumentRefreshError("Request Updated Document is only available for document expiry alerts", 400)
    if str(alert.get("status") or "").lower() in TERMINAL_ALERT_STATUSES:
        raise MonitoringDocumentRefreshError("Cannot request a document for a resolved or waived alert", 409)
    if not alert.get("application_id"):
        raise MonitoringDocumentRefreshError("Alert is not linked to an application", 400)

    existing = _active_request_for_alert(db, alert.get("id"))
    if existing:
        return {
            "status": "document_requested",
            "reused": True,
            "created": False,
            "request": existing,
            "document_refresh": document_refresh_context(db, alert),
            "notification": {"status": "not_sent", "reason": "active_request_already_exists"},
        }
    existing_any = _request_for_alert(db, alert.get("id"))
    if existing_any:
        return {
            "status": alert.get("status") or "document_requested",
            "reused": True,
            "created": False,
            "request": existing_any,
            "document_refresh": document_refresh_context(db, alert),
            "notification": {"status": "not_sent", "reason": "linked_request_already_exists"},
        }

    ctx = document_refresh_context(db, alert)
    document = ctx.get("document") or {}
    due_date = _date_only(due_date) or (datetime.now(timezone.utc) + timedelta(days=DEFAULT_DOCUMENT_REFRESH_DUE_DAYS)).date().isoformat()
    doc_type = document.get("type") or "document"
    doc_label = document.get("type_label") or _label(doc_type)
    owner = document.get("owner") or alert.get("application_company_name") or alert.get("client_name") or "the client"
    request_reason = ctx.get("request_reason") or "refresh required"
    label = f"Updated {doc_label} required"
    description = (
        f"The document we hold for {owner} has expired or requires refresh. "
        f"Please upload an updated copy by {due_date}."
    )
    trigger_key = f"monitoring_document_refresh_{alert.get('id')}"
    requirement_key = f"updated_{_canonical(doc_type) or 'document'}_for_alert_{alert.get('id')}"
    now = _now_iso()
    actor_id = (user or {}).get("sub", "")
    trigger_context = {
        "source_surface": DOCUMENT_REFRESH_GENERATION_SOURCE,
        "monitoring_alert_id": alert.get("id"),
        "application_id": alert.get("application_id"),
        "application_ref": alert.get("application_ref"),
        "document_id": document.get("id") or "",
        "document_type": doc_type,
        "document_owner": owner,
        "document_owner_type": document.get("owner_type") or "company",
        "expiry_date": document.get("expiry_date") or "",
        "due_date": due_date,
        "request_reason": request_reason,
    }
    db.execute(
        """
        INSERT INTO application_enhanced_requirements
            (application_id, trigger_key, trigger_label, trigger_category,
             requirement_key, requirement_label, requirement_description,
             audience, requirement_type, subject_scope, blocking_approval,
             waivable, waiver_roles, mandatory, status, generation_source,
             trigger_reason, trigger_context, linked_document_id,
             monitoring_alert_id, monitoring_document_id, due_date,
             requested_at, requested_by, created_at, created_by, updated_at, updated_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            alert.get("application_id"),
            trigger_key,
            "Document refresh required",
            DOCUMENT_REFRESH_TRIGGER_CATEGORY,
            requirement_key,
            label,
            description,
            audience if audience in {"client", "both", "backoffice"} else "client",
            "document",
            document.get("owner_type") if document.get("owner_type") in {"company", "director", "ubo", "controller", "application", "screening_subject"} else "application",
            0,
            1,
            json.dumps(["admin", "sco"]),
            1,
            "requested",
            DOCUMENT_REFRESH_GENERATION_SOURCE,
            request_reason,
            json.dumps(trigger_context, sort_keys=True),
            None,
            alert.get("id"),
            document.get("id") or None,
            due_date,
            now,
            actor_id,
            now,
            actor_id,
            now,
            actor_id,
        ),
    )
    request = _active_request_for_alert(db, alert.get("id"))
    notification = None
    if notify_client:
        notification = _insert_client_notification(db, alert, request, label, description, due_date)

    before_state = {"status": alert.get("status"), "officer_action": alert.get("officer_action")}
    payload = {
        "event": "updated_document_requested",
        "alert_id": alert.get("id"),
        "application_id": alert.get("application_id"),
        "client_id": alert.get("application_client_id"),
        "application_ref": alert.get("application_ref"),
        "document_request_id": request.get("id"),
        "document_id": document.get("id") or "",
        "document_type": doc_type,
        "document_owner": owner,
        "due_date": due_date,
        "request_reason": request_reason,
        "request_channel": "client_portal" if notify_client else "back_office_upload",
        "requested_by": actor_id,
    }
    # M1.1 decoupling: the refresh sub-state lives on the enhanced-requirement
    # row (status='requested'); the alert's own lifecycle status is not
    # advanced by a document request. "Awaiting Client" is derived at display.
    db.execute(
        """
        UPDATE monitoring_alerts
           SET officer_action = 'request_updated_document',
               officer_notes = ?,
               reviewed_at = CURRENT_TIMESTAMP,
               reviewed_by = COALESCE(reviewed_by, ?)
         WHERE id = ?
        """,
        (json.dumps(payload, sort_keys=True), actor_id, alert.get("id")),
    )
    _audit(
        audit_writer,
        user,
        "updated_document_requested",
        alert.get("id"),
        payload,
        db=db,
        before_state=before_state,
        after_state={"document_request_status": "requested", "document_request_id": request.get("id")},
    )
    _audit(
        audit_writer,
        user,
        "monitoring.alert.document_update_requested",
        alert.get("id"),
        payload,
        db=db,
        before_state=before_state,
        after_state={"document_request_status": "requested", "document_request_id": request.get("id")},
    )

    email_status = {"status": "not_attempted", "reason": "client_id_missing"}
    if not notify_client:
        email_status = {"status": "not_sent", "reason": "backoffice_upload_no_client_email"}
        email_payload = dict(payload)
        email_payload.update({
            "notification_id": None,
            "email_status": email_status["status"],
            "email_reason": email_status["reason"],
        })
        _audit(
            audit_writer,
            user,
            "document_request_notification_failed",
            alert.get("id"),
            email_payload,
            db=db,
            after_state=email_payload,
        )
    elif alert.get("application_client_id"):
        subject = "Updated document required"
        body = (
            f"Updated {doc_label} required\n\n"
            f"Reason: {request_reason}.\n"
            f"Due date: {due_date}.\n\n"
            "Please sign in to the RegMind client portal and upload the updated document.\n"
            "If you need help, contact your compliance support contact."
        )
        if not alert.get("client_email"):
            email_status = {"status": "failed", "reason": "client_email_missing"}
        elif email_sender is None:
            email_status = {"status": "failed", "reason": "email_sender_not_configured"}
        else:
            sent = bool(email_sender(alert.get("client_email"), subject, body))
            email_status = {"status": "sent" if sent else "failed", "reason": "" if sent else "email_sender_returned_false"}
        email_payload = dict(payload)
        email_payload.update({
            "notification_id": (notification or {}).get("id"),
            "email_status": email_status["status"],
            "email_reason": email_status.get("reason") or "",
        })
        _audit(
            audit_writer,
            user,
            "document_request_notification_sent" if email_status["status"] == "sent" else "document_request_notification_failed",
            alert.get("id"),
            email_payload,
            db=db,
            after_state=email_payload,
        )

    return {
        "status": "document_requested",
        "created": True,
        "reused": False,
        "request": request,
        "document_refresh": document_refresh_context(db, alert.get("id")),
        "notification": {
            "portal_task_created": bool(request),
            "client_notification_id": (notification or {}).get("id"),
            "email": email_status,
        },
    }


def mark_client_upload_received_if_monitoring_linked(db, app, requirement, document_id, *, actor, audit_writer):
    requirement = serialize_application_requirement(requirement) if not isinstance(requirement, dict) else dict(requirement or {})
    alert_id = requirement.get("monitoring_alert_id")
    if not alert_id:
        return False
    alert = _fetch_alert(db, alert_id)
    if not alert:
        return False
    before_state = {"status": alert.get("status"), "officer_action": alert.get("officer_action")}
    payload = {
        "event": "client_document_upload_received",
        "alert_id": alert_id,
        "application_id": requirement.get("application_id"),
        "client_id": alert.get("application_client_id"),
        "document_request_id": requirement.get("id"),
        "old_document_id": requirement.get("monitoring_document_id") or "",
        "document_id": document_id,
        "document_type": requirement.get("trigger_context", {}).get("document_type") if isinstance(requirement.get("trigger_context"), dict) else "",
        "actor": (actor or {}).get("sub", ""),
        "timestamp": _now_iso(),
    }
    # M1.1 decoupling: requirement.status is already 'uploaded'; the alert's
    # lifecycle status is not advanced by a client upload. "Awaiting Officer"
    # is derived at display time from the linked requirement.
    db.execute(
        """
        UPDATE monitoring_alerts
           SET officer_action = 'client_document_uploaded',
               officer_notes = ?,
               reviewed_at = CURRENT_TIMESTAMP
         WHERE id = ?
        """,
        (json.dumps(payload, sort_keys=True), alert_id),
    )
    _audit(
        audit_writer,
        actor,
        "client_document_upload_received",
        alert_id,
        payload,
        db=db,
        before_state=before_state,
        after_state={"document_request_status": "uploaded", "document_id": document_id},
    )
    try:
        users = db.execute("SELECT id FROM users WHERE role IN ('admin','sco','co') AND COALESCE(status,'active') = 'active'").fetchall()
        for user_row in users:
            db.execute(
                "INSERT INTO notifications (user_id, title, message) VALUES (?,?,?)",
                (
                    _row_get(user_row, "id"),
                    "Updated document uploaded",
                    f"Updated document uploaded for monitoring alert #{alert_id}.",
                ),
            )
    except Exception:
        logger.warning("monitoring document upload officer notification failed", exc_info=True)
    return True


def mark_backoffice_upload_received(db, alert_id, requirement, document_id, *, source_note, actor, audit_writer):
    requirement = serialize_application_requirement(requirement) if not isinstance(requirement, dict) else dict(requirement or {})
    alert = _fetch_alert(db, alert_id)
    if not alert:
        return False
    before_state = {"status": alert.get("status"), "officer_action": alert.get("officer_action")}
    payload = {
        "event": "backoffice_replacement_uploaded",
        "alert_id": alert_id,
        "application_id": requirement.get("application_id") or alert.get("application_id"),
        "client_id": alert.get("application_client_id"),
        "document_request_id": requirement.get("id"),
        "old_document_id": requirement.get("monitoring_document_id") or "",
        "new_document_id": document_id,
        "document_type": requirement.get("trigger_context", {}).get("document_type") if isinstance(requirement.get("trigger_context"), dict) else "",
        "upload_source": "back_office_upload",
        "source_note": source_note,
        "actor": (actor or {}).get("sub", ""),
        "timestamp": _now_iso(),
    }
    # M1.1 decoupling: requirement.status is already 'under_review'; the
    # alert's lifecycle status is not advanced by a back-office upload.
    db.execute(
        """
        UPDATE monitoring_alerts
           SET officer_action = 'backoffice_replacement_uploaded',
               officer_notes = ?,
               reviewed_at = CURRENT_TIMESTAMP,
               reviewed_by = ?
         WHERE id = ?
        """,
        (json.dumps(payload, sort_keys=True), (actor or {}).get("sub", ""), alert_id),
    )
    _audit(
        audit_writer,
        actor,
        "backoffice_replacement_uploaded",
        alert_id,
        payload,
        db=db,
        before_state=before_state,
        after_state={"document_request_status": "under_review", "document_id": document_id},
    )
    return True


def _restore_old_document_after_rejection(db, app_id, old_document_id, rejected_document_id):
    if not old_document_id or not rejected_document_id:
        return
    old_doc = _fetch_document(db, old_document_id, app_id)
    rejected_doc = _fetch_document(db, rejected_document_id, app_id)
    if not old_doc or not rejected_doc:
        return
    db.execute(
        """
        UPDATE documents
           SET is_current = ?,
               replaced_reason = COALESCE(replaced_reason, 'monitoring_refresh_rejected')
         WHERE id = ? AND application_id = ?
        """,
        (False, rejected_document_id, app_id),
    )
    db.execute(
        """
        UPDATE documents
           SET is_current = ?,
               superseded_at = NULL,
               superseded_by_document_id = NULL,
               replaced_reason = NULL,
               replaced_by_user_id = NULL
         WHERE id = ? AND application_id = ?
        """,
        (True, old_document_id, app_id),
    )


def review_document_refresh(db, alert_id, *, outcome, note, user, audit_writer):
    alert = _fetch_alert(db, alert_id)
    if not alert:
        raise MonitoringDocumentRefreshError("Alert not found", 404)
    if not is_document_refresh_alert(alert):
        raise MonitoringDocumentRefreshError("Document refresh review is only available for document expiry alerts", 400)
    request = _request_for_alert(db, alert.get("id"))
    if not request:
        raise MonitoringDocumentRefreshError("No document refresh request is linked to this alert", 404)
    note = str(note or "").strip()
    if outcome in {"reject", "waive"} and not note:
        raise MonitoringDocumentRefreshError("A reason is required for this document outcome", 400)

    app_id = request.get("application_id")
    req_id = request.get("id")
    if outcome == "accept":
        if not request.get("linked_document_id"):
            raise MonitoringDocumentRefreshError("An uploaded replacement document is required before acceptance", 409)
        target_status = "accepted"
        alert_status = "resolved"
        officer_action = "accept_updated_document"
        audit_action = "updated_document_accepted"
    elif outcome == "reject":
        target_status = "rejected"
        # M1.1 decoupling: rejection re-opens the refresh request (requirement
        # goes back to 'requested'); the alert's lifecycle status is untouched.
        alert_status = None
        officer_action = "reject_updated_document"
        audit_action = "updated_document_rejected"
    elif outcome == "waive":
        target_status = "waived"
        alert_status = "waived"
        officer_action = "waive_with_reason"
        audit_action = "updated_document_waived"
    else:
        raise MonitoringDocumentRefreshError("Invalid document refresh outcome", 400)

    update_payload = {"status": target_status}
    if note:
        update_payload["review_notes"] = note
    if outcome == "waive":
        update_payload["waiver_reason"] = note
    result, error, status_code = update_application_enhanced_requirement(
        db,
        app_id,
        req_id,
        update_payload,
        actor=user,
    )
    if error:
        raise MonitoringDocumentRefreshError(error, status_code)
    after_req = (result or {}).get("requirement") or request
    linked_document_id = after_req.get("linked_document_id") or request.get("linked_document_id")
    doc_status = "accepted" if outcome == "accept" else "rejected" if outcome == "reject" else None
    if linked_document_id and doc_status:
        db.execute(
            """
            UPDATE documents
               SET review_status = ?,
                   review_comment = ?,
                   reviewed_by = ?,
                   reviewer_role = ?,
                   reviewed_at = datetime('now')
             WHERE id = ? AND application_id = ?
            """,
            (
                doc_status,
                note,
                (user or {}).get("sub", ""),
                (user or {}).get("role", ""),
                linked_document_id,
                app_id,
            ),
        )
    if outcome == "reject":
        _restore_old_document_after_rejection(
            db,
            app_id,
            request.get("monitoring_document_id"),
            linked_document_id,
        )

    before_state = {"status": alert.get("status"), "officer_action": alert.get("officer_action")}
    payload = {
        "event": audit_action,
        "alert_id": alert.get("id"),
        "application_id": app_id,
        "client_id": alert.get("application_client_id"),
        "application_ref": alert.get("application_ref"),
        "document_request_id": req_id,
        "old_document_id": request.get("monitoring_document_id") or "",
        "document_id": linked_document_id,
        "outcome": outcome,
        "note": note,
        "actor": (user or {}).get("sub", ""),
        "timestamp": _now_iso(),
    }
    resolved_clause = ", resolved_at = CURRENT_TIMESTAMP" if alert_status in {"resolved", "waived"} else ", resolved_at = NULL"
    if alert_status is None:
        # Reject: officer bookkeeping only — alert.status stays canonical.
        db.execute(
            f"""
            UPDATE monitoring_alerts
               SET officer_action = ?,
                   officer_notes = ?,
                   reviewed_at = CURRENT_TIMESTAMP,
                   reviewed_by = ?
                   {resolved_clause}
             WHERE id = ?
            """,
            (
                officer_action,
                json.dumps(payload, sort_keys=True),
                (user or {}).get("sub", ""),
                alert.get("id"),
            ),
        )
    else:
        db.execute(
            f"""
            UPDATE monitoring_alerts
               SET status = ?,
                   officer_action = ?,
                   officer_notes = ?,
                   reviewed_at = CURRENT_TIMESTAMP,
                   reviewed_by = ?
                   {resolved_clause}
             WHERE id = ?
            """,
            (
                alert_status,
                officer_action,
                json.dumps(payload, sort_keys=True),
                (user or {}).get("sub", ""),
                alert.get("id"),
            ),
        )
    after_state = {"document_request_status": target_status}
    if alert_status is not None:
        after_state["status"] = alert_status
    _audit(
        audit_writer,
        user,
        audit_action,
        alert.get("id"),
        payload,
        db=db,
        before_state=before_state,
        after_state=after_state,
    )
    if outcome == "accept":
        _audit(
            audit_writer,
            user,
            "monitoring_alert_resolved",
            alert.get("id"),
            payload,
            db=db,
            before_state=before_state,
            after_state={"status": "resolved"},
        )
    return {
        "alert_id": alert.get("id"),
        # Effective flow status for API/UI consumers: rejection re-opens the
        # document request even though the alert row stays canonical.
        "status": alert_status if alert_status is not None else "document_requested",
        "document_request": after_req,
        "document_refresh": document_refresh_context(db, alert.get("id")),
    }


def sync_requirement_review_to_monitoring_alert(db, requirement, *, user, audit_writer):
    requirement = serialize_application_requirement(requirement) if not isinstance(requirement, dict) else dict(requirement or {})
    alert_id = requirement.get("monitoring_alert_id")
    if not alert_id:
        return False
    status = str(requirement.get("status") or "").strip().lower()
    if status not in {"accepted", "rejected", "waived"}:
        return False
    alert = _fetch_alert(db, alert_id)
    if not alert or not is_document_refresh_alert(alert):
        return False

    if status == "accepted":
        alert_status = "resolved"
        officer_action = "accept_updated_document"
        audit_action = "updated_document_accepted"
        outcome = "accept"
        note = requirement.get("review_notes") or ""
    elif status == "rejected":
        # M1.1 decoupling: rejection re-opens the request; alert.status untouched.
        alert_status = None
        officer_action = "reject_updated_document"
        audit_action = "updated_document_rejected"
        outcome = "reject"
        note = requirement.get("review_notes") or ""
    else:
        alert_status = "waived"
        officer_action = "waive_with_reason"
        audit_action = "updated_document_waived"
        outcome = "waive"
        note = requirement.get("waiver_reason") or requirement.get("review_notes") or ""

    linked_document_id = requirement.get("linked_document_id")
    doc_status = "accepted" if outcome == "accept" else "rejected" if outcome == "reject" else None
    if linked_document_id and doc_status:
        db.execute(
            """
            UPDATE documents
               SET review_status = ?,
                   review_comment = ?,
                   reviewed_by = ?,
                   reviewer_role = ?,
                   reviewed_at = datetime('now')
             WHERE id = ? AND application_id = ?
            """,
            (
                doc_status,
                note,
                (user or {}).get("sub", ""),
                (user or {}).get("role", ""),
                linked_document_id,
                requirement.get("application_id"),
            ),
        )
    if outcome == "reject":
        _restore_old_document_after_rejection(
            db,
            requirement.get("application_id"),
            requirement.get("monitoring_document_id"),
            linked_document_id,
        )

    before_state = {"status": alert.get("status"), "officer_action": alert.get("officer_action")}
    payload = {
        "event": audit_action,
        "alert_id": alert_id,
        "application_id": requirement.get("application_id"),
        "client_id": alert.get("application_client_id"),
        "application_ref": alert.get("application_ref"),
        "document_request_id": requirement.get("id"),
        "old_document_id": requirement.get("monitoring_document_id") or "",
        "document_id": linked_document_id,
        "outcome": outcome,
        "note": note,
        "actor": (user or {}).get("sub", ""),
        "timestamp": _now_iso(),
        "source_surface": "application_enhanced_requirement_review",
    }
    resolved_clause = ", resolved_at = CURRENT_TIMESTAMP" if alert_status in {"resolved", "waived"} else ", resolved_at = NULL"
    if alert_status is None:
        db.execute(
            f"""
            UPDATE monitoring_alerts
               SET officer_action = ?,
                   officer_notes = ?,
                   reviewed_at = CURRENT_TIMESTAMP,
                   reviewed_by = ?
                   {resolved_clause}
             WHERE id = ?
            """,
            (
                officer_action,
                json.dumps(payload, sort_keys=True),
                (user or {}).get("sub", ""),
                alert_id,
            ),
        )
    else:
        db.execute(
            f"""
            UPDATE monitoring_alerts
               SET status = ?,
                   officer_action = ?,
                   officer_notes = ?,
                   reviewed_at = CURRENT_TIMESTAMP,
                   reviewed_by = ?
                   {resolved_clause}
             WHERE id = ?
            """,
            (
                alert_status,
                officer_action,
                json.dumps(payload, sort_keys=True),
                (user or {}).get("sub", ""),
                alert_id,
            ),
        )
    sync_after_state = {"document_request_status": status}
    if alert_status is not None:
        sync_after_state["status"] = alert_status
    _audit(
        audit_writer,
        user,
        audit_action,
        alert_id,
        payload,
        db=db,
        before_state=before_state,
        after_state=sync_after_state,
    )
    if outcome == "accept":
        _audit(
            audit_writer,
            user,
            "monitoring_alert_resolved",
            alert_id,
            payload,
            db=db,
            before_state=before_state,
            after_state={"status": "resolved"},
        )
    return True
