"""
Document health monitoring for ongoing monitoring alerts.

Small deterministic helper that scans current application documents and
creates / updates / resolves monitoring_alerts rows for document health
issues without introducing a new workflow engine.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import lifecycle_linkage as ll
import monitoring_routing as mr

logger = logging.getLogger("arie.document_health_monitor")

DOCUMENT_EXPIRING_SOON_DAYS = 30
DOCUMENT_STALE_AFTER_DAYS = 365
DOCUMENT_HEALTH_DETECTED_BY = "document_health_monitor"
DOCUMENT_HEALTH_DISCOVERED_VIA = "document_health"
ALERT_TYPE_EXPIRED = "document_expired"
ALERT_TYPE_EXPIRING_SOON = "document_expiring_soon"
ALERT_TYPE_STALE = "document_stale"
ALERT_TYPE_EXPIRY_MISSING = "document_expiry_missing"
DOCUMENT_ALERT_TYPES = (
    ALERT_TYPE_EXPIRED,
    ALERT_TYPE_EXPIRING_SOON,
    ALERT_TYPE_STALE,
    ALERT_TYPE_EXPIRY_MISSING,
)
CRITICAL_IDENTITY_DOC_TYPES = {
    "passport",
    "national_id",
    "id_card",
    "drivers_license",
    "director_id",
    "ubo_id",
}
EXPIRY_REQUIRED_DOC_TYPES = CRITICAL_IDENTITY_DOC_TYPES | {"licence"}
SYSTEM_USER = {
    "sub": "system:document-health-monitor",
    "name": "Document Health Monitor",
    "role": "system",
}


def _row_get(row, key, default=None):
    if row is None:
        return default
    if hasattr(row, "get"):
        value = row.get(key, default)
        return value if value is not None else default
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return value if value is not None else default


def _load_json(raw, default):
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _parse_dt(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    dt = None
            if dt is None:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _source_reference(document_id: Any) -> str:
    return f"document:{document_id}"


def _require_audit_writer(audit_writer):
    if audit_writer is None:
        raise ll.MissingAuditWriter(
            "document_health_monitor requires a non-None audit_writer for "
            "every alert mutation"
        )


def _get_document_type(doc) -> str:
    return str(_row_get(doc, "doc_type") or "").strip().lower()


def _document_label(doc) -> str:
    doc_type = _get_document_type(doc) or "document"
    doc_name = _row_get(doc, "doc_name") or ""
    if doc_name:
        return f"{doc_type} ({doc_name})"
    return doc_type


def _extract_document_expiry(doc) -> Dict[str, Optional[datetime]]:
    expiry_dt = _parse_dt(_row_get(doc, "expiry_date"))
    valid_until_dt = _parse_dt(_row_get(doc, "valid_until"))
    verification = _load_json(_row_get(doc, "verification_results"), {}) or {}
    if not expiry_dt:
        expiry_dt = _parse_dt(
            verification.get("expiry_date")
            or verification.get("expiry")
            or verification.get("validity_to")
        )
    if not valid_until_dt:
        valid_until_dt = _parse_dt(verification.get("valid_until"))
    return {"expiry_date": expiry_dt, "valid_until": valid_until_dt}


def _uploaded_at(doc) -> Optional[datetime]:
    return _parse_dt(_row_get(doc, "uploaded_at"))


def _issue_severity(alert_type: str, doc_type: str) -> str:
    if alert_type == ALERT_TYPE_EXPIRED:
        if doc_type == "licence":
            return "critical"
        if doc_type in CRITICAL_IDENTITY_DOC_TYPES:
            return "high"
        return "medium"
    if alert_type == ALERT_TYPE_EXPIRING_SOON:
        if doc_type == "licence":
            return "high"
        if doc_type in CRITICAL_IDENTITY_DOC_TYPES:
            return "medium"
        return "low"
    if alert_type == ALERT_TYPE_STALE:
        return "medium"
    if alert_type == ALERT_TYPE_EXPIRY_MISSING:
        return "medium"
    return "low"


def _issue_summary(alert_type: str, doc) -> str:
    label = _document_label(doc)
    if alert_type == ALERT_TYPE_EXPIRED:
        return f"{label} has expired"
    if alert_type == ALERT_TYPE_EXPIRING_SOON:
        return f"{label} expires soon"
    if alert_type == ALERT_TYPE_STALE:
        return f"{label} is stale and should be refreshed"
    return f"{label} is missing an expiry date"


def _issue_recommendation(alert_type: str, doc_type: str) -> str:
    if alert_type == ALERT_TYPE_EXPIRED:
        return "Obtain a current replacement document and verify it before clearing."
    if alert_type == ALERT_TYPE_EXPIRING_SOON:
        return "Request a refreshed document before expiry and monitor to closure."
    if alert_type == ALERT_TYPE_STALE:
        return "Refresh the document because it is policy-stale."
    if doc_type == "licence":
        return "Verify the regulatory licence expiry date and persist it on the current document."
    return "Capture and verify the document expiry date on the current document."


def _issue_rationale(alert_type: str, doc, *, today: date,
                     expiring_soon_days: int, stale_after_days: int) -> str:
    dates = _extract_document_expiry(doc)
    expiry_dt = dates["expiry_date"] or dates["valid_until"]
    if alert_type == ALERT_TYPE_EXPIRED and expiry_dt:
        return f"Expiry date {expiry_dt.date().isoformat()} is before {today.isoformat()}."
    if alert_type == ALERT_TYPE_EXPIRING_SOON and expiry_dt:
        return (
            f"Expiry date {expiry_dt.date().isoformat()} falls within "
            f"{expiring_soon_days} days."
        )
    if alert_type == ALERT_TYPE_STALE:
        uploaded_at = _uploaded_at(doc)
        uploaded_text = uploaded_at.date().isoformat() if uploaded_at else "unknown"
        return (
            f"Current document has been on file since {uploaded_text}, which is "
            f"older than the {stale_after_days}-day refresh threshold."
        )
    return (
        "This document type normally carries an expiry date but the current "
        "document does not have expiry_date or valid_until populated."
    )


def _current_document_sql(db) -> str:
    return (
        "COALESCE(is_current, TRUE) = TRUE"
        if getattr(db, "is_postgres", False)
        else "COALESCE(is_current, 1) = 1"
    )


def _emit_audit(audit_writer, *, user, action, target, detail,
                db, before_state=None, after_state=None):
    _require_audit_writer(audit_writer)
    try:
        audit_writer(
            dict(user or SYSTEM_USER),
            action,
            target,
            json.dumps(detail, default=str, sort_keys=True),
            db=db,
            before_state=before_state,
            after_state=after_state,
        )
    except Exception:
        logger.exception("document health audit failed action=%s target=%s", action, target)


def _active_documents_for_application(db, application_id) -> List[Any]:
    current_sql = _current_document_sql(db)
    return db.execute(
        f"""
        SELECT id, application_id, person_id, doc_type, doc_name, uploaded_at,
               expiry_date, valid_until, verification_results, is_current,
               superseded_at, superseded_by_document_id
          FROM documents
         WHERE application_id = ?
           AND {current_sql}
           AND superseded_at IS NULL
        """,
        (application_id,),
    ).fetchall()


def _document_issues(doc, *, today: date,
                     expiring_soon_days: int,
                     stale_after_days: int) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    doc_type = _get_document_type(doc)
    dates = _extract_document_expiry(doc)
    expiry_dt = dates["expiry_date"] or dates["valid_until"]
    if expiry_dt is not None:
        expiry_date = expiry_dt.date()
        if expiry_date < today:
            issues.append({"alert_type": ALERT_TYPE_EXPIRED})
        elif expiry_date <= today + timedelta(days=expiring_soon_days):
            issues.append({"alert_type": ALERT_TYPE_EXPIRING_SOON})
    else:
        uploaded_at = _uploaded_at(doc)
        if uploaded_at and uploaded_at.date() <= today - timedelta(days=stale_after_days):
            issues.append({"alert_type": ALERT_TYPE_STALE})
        if doc_type in EXPIRY_REQUIRED_DOC_TYPES:
            issues.append({"alert_type": ALERT_TYPE_EXPIRY_MISSING})

    materialized = []
    for issue in issues:
        alert_type = issue["alert_type"]
        materialized.append({
            "application_id": _row_get(doc, "application_id"),
            "document_id": _row_get(doc, "id"),
            "doc_type": doc_type,
            "alert_type": alert_type,
            "severity": _issue_severity(alert_type, doc_type),
            "summary": _issue_summary(alert_type, doc),
            "source_reference": _source_reference(_row_get(doc, "id")),
            "ai_recommendation": _issue_recommendation(alert_type, doc_type),
            "rationale": _issue_rationale(
                alert_type, doc,
                today=today,
                expiring_soon_days=expiring_soon_days,
                stale_after_days=stale_after_days,
            ),
        })
    return materialized


def _fetch_application(db, application_id):
    return db.execute(
        "SELECT id, company_name FROM applications WHERE id = ?",
        (application_id,),
    ).fetchone()


def _fetch_existing_document_alerts(db, application_id) -> List[Any]:
    placeholders = ",".join("?" for _ in DOCUMENT_ALERT_TYPES)
    return db.execute(
        f"""
        SELECT *
          FROM monitoring_alerts
         WHERE application_id = ?
           AND detected_by = ?
           AND alert_type IN ({placeholders})
         ORDER BY id ASC
        """,
        (application_id, DOCUMENT_HEALTH_DETECTED_BY, *DOCUMENT_ALERT_TYPES),
    ).fetchall()


def sync_document_health_alerts_for_application(
    db,
    application_id,
    *,
    user=None,
    audit_writer=None,
    today: Optional[date] = None,
    expiring_soon_days: int = DOCUMENT_EXPIRING_SOON_DAYS,
    stale_after_days: int = DOCUMENT_STALE_AFTER_DAYS,
) -> Dict[str, Any]:
    _require_audit_writer(audit_writer)
    if not application_id:
        return {"application_id": application_id, "created": 0, "updated": 0, "resolved": 0}

    acting_user = dict(user or SYSTEM_USER)
    today = today or datetime.now(timezone.utc).date()
    app = _fetch_application(db, application_id)
    client_name = _row_get(app, "company_name") or ""
    desired = {}
    for doc in _active_documents_for_application(db, application_id):
        for issue in _document_issues(
            doc,
            today=today,
            expiring_soon_days=expiring_soon_days,
            stale_after_days=stale_after_days,
        ):
            desired[(issue["document_id"], issue["alert_type"])] = issue

    existing_rows = _fetch_existing_document_alerts(db, application_id)
    open_existing = {}
    for row in existing_rows:
        source_reference = str(_row_get(row, "source_reference") or "")
        document_id = None
        if source_reference.startswith("document:"):
            parts = source_reference.split(":", 1)
            document_id = parts[1] if len(parts) == 2 and parts[1] else None
        key = (document_id, _row_get(row, "alert_type"))
        if mr.is_alert_unresolved(row):
            open_existing[key] = row

    created = updated = resolved = 0
    for key, issue in desired.items():
        existing = open_existing.pop(key, None)
        after_state = {
            "status": "open",
            "alert_type": issue["alert_type"],
            "severity": issue["severity"],
            "summary": issue["summary"],
            "source_reference": issue["source_reference"],
            "discovered_via": DOCUMENT_HEALTH_DISCOVERED_VIA,
        }
        if existing is None:
            db.execute(
                """
                INSERT INTO monitoring_alerts
                    (application_id, client_name, alert_type, severity, detected_by,
                     summary, source_reference, ai_recommendation, status, discovered_via)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    application_id,
                    client_name,
                    issue["alert_type"],
                    issue["severity"],
                    DOCUMENT_HEALTH_DETECTED_BY,
                    issue["summary"],
                    issue["source_reference"],
                    issue["ai_recommendation"],
                    "open",
                    DOCUMENT_HEALTH_DISCOVERED_VIA,
                ),
            )
            new_row = db.execute(
                "SELECT * FROM monitoring_alerts ORDER BY id DESC LIMIT 1"
            ).fetchone()
            created += 1
            _emit_audit(
                audit_writer,
                user=acting_user,
                action="monitoring.document_health_alert.created",
                target=f"monitoring_alert:{_row_get(new_row, 'id')}",
                detail={
                    "application_id": application_id,
                    "document_id": issue["document_id"],
                    "alert_type": issue["alert_type"],
                    "rationale": issue["rationale"],
                },
                db=db,
                before_state=None,
                after_state=after_state,
            )
            continue

        before_state = {
            "status": _row_get(existing, "status"),
            "severity": _row_get(existing, "severity"),
            "summary": _row_get(existing, "summary"),
            "ai_recommendation": _row_get(existing, "ai_recommendation"),
            "discovered_via": _row_get(existing, "discovered_via"),
        }
        if before_state["severity"] == issue["severity"] and before_state["summary"] == issue["summary"] and (
            _row_get(existing, "ai_recommendation") == issue["ai_recommendation"]
            and _row_get(existing, "discovered_via") == DOCUMENT_HEALTH_DISCOVERED_VIA
        ):
            continue

        db.execute(
            """
            UPDATE monitoring_alerts
               SET client_name = ?,
                   severity = ?,
                   summary = ?,
                   ai_recommendation = ?,
                   source_reference = ?,
                   discovered_via = ?
             WHERE id = ?
            """,
            (
                client_name,
                issue["severity"],
                issue["summary"],
                issue["ai_recommendation"],
                issue["source_reference"],
                DOCUMENT_HEALTH_DISCOVERED_VIA,
                _row_get(existing, "id"),
            ),
        )
        updated += 1
        _emit_audit(
            audit_writer,
            user=acting_user,
            action="monitoring.document_health_alert.updated",
            target=f"monitoring_alert:{_row_get(existing, 'id')}",
            detail={
                "application_id": application_id,
                "document_id": issue["document_id"],
                "alert_type": issue["alert_type"],
                "rationale": issue["rationale"],
            },
            db=db,
            before_state=before_state,
            after_state=after_state,
        )

    for existing in open_existing.values():
        resolved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        before_state = {
            "status": _row_get(existing, "status"),
            "resolved_at": _row_get(existing, "resolved_at"),
        }
        db.execute(
            """
            UPDATE monitoring_alerts
               SET status = 'resolved',
                   officer_action = 'auto_resolved',
                   officer_notes = ?,
                   resolved_at = CURRENT_TIMESTAMP,
                   reviewed_at = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (
                json.dumps({
                    "auto_resolved": True,
                    "reason": "document_issue_no_longer_current",
                    "resolution": "superseded_or_no_longer_current",
                }, sort_keys=True),
                _row_get(existing, "id"),
            ),
        )
        resolved += 1
        _emit_audit(
            audit_writer,
            user=acting_user,
            action="monitoring.document_health_alert.resolved",
            target=f"monitoring_alert:{_row_get(existing, 'id')}",
            detail={
                "application_id": application_id,
                "alert_type": _row_get(existing, "alert_type"),
                "source_reference": _row_get(existing, "source_reference"),
            },
                db=db,
                before_state=before_state,
                after_state={"status": "resolved", "resolved_at": resolved_at},
            )

    if created or updated or resolved:
        db.commit()
    return {
        "application_id": application_id,
        "created": created,
        "updated": updated,
        "resolved": resolved,
        "open_issues": len(desired),
    }


def sync_document_health_alerts(
    db,
    *,
    application_ids: Optional[Iterable[Any]] = None,
    user=None,
    audit_writer=None,
) -> Dict[str, Any]:
    _require_audit_writer(audit_writer)
    if application_ids is None:
        application_ids = [
            _row_get(r, "id")
            for r in db.execute("SELECT id FROM applications ORDER BY id").fetchall()
        ]
    totals = {"created": 0, "updated": 0, "resolved": 0, "applications": 0}
    for application_id in application_ids:
        result = sync_document_health_alerts_for_application(
            db,
            application_id,
            user=user,
            audit_writer=audit_writer,
        )
        totals["applications"] += 1
        totals["created"] += result["created"]
        totals["updated"] += result["updated"]
        totals["resolved"] += result["resolved"]
    if totals["created"] or totals["updated"] or totals["resolved"]:
        db.commit()
    return totals


__all__ = [
    "ALERT_TYPE_EXPIRED",
    "ALERT_TYPE_EXPIRING_SOON",
    "ALERT_TYPE_STALE",
    "ALERT_TYPE_EXPIRY_MISSING",
    "DOCUMENT_ALERT_TYPES",
    "DOCUMENT_EXPIRING_SOON_DAYS",
    "DOCUMENT_STALE_AFTER_DAYS",
    "DOCUMENT_HEALTH_DETECTED_BY",
    "DOCUMENT_HEALTH_DISCOVERED_VIA",
    "sync_document_health_alerts_for_application",
    "sync_document_health_alerts",
]
