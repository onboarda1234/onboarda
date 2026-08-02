"""Canonical, flag-gated document-renewal request workflow.

This module owns only the orchestration record for a document-renewal request.
It never updates ``documents`` or ``monitoring_alerts``, never invokes document
verification, and never calls the legacy Monitoring document-refresh module.
Every write re-resolves the exact canonical linkage inside the transaction and
appends an audit record atomically with the domain event.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
import uuid
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from document_health_monitor import DOCUMENT_EXPIRING_SOON_DAYS
from monitoring_alert_state_machine import ACTIVE_STATUSES as MONITORING_ACTIVE_STATUSES
from monitoring_alert_linkage import LinkageError, resolve_alert_linkage


CONTRACT_VERSION = "monitoring_document_renewal_request_v1"
ORIGINATING_PR = "PR-MON-DOC-RENEWAL-REQUEST-1"
FEATURE_FLAG = "ENABLE_DOCUMENT_RENEWAL_AUTOMATION"
DEFAULT_DUE_DAYS = 14
EXPIRING_SOON_DAYS = DOCUMENT_EXPIRING_SOON_DAYS
DEFAULT_REMINDER_INTERVALS = (14, 7, 3, 1)

AUTOMATIC_ALERT_REASONS = {
    "document_expired": "expired",
    "document_expiring_soon": "expiring_soon",
}
MANUAL_ALERT_TYPES = frozenset(
    {"document_expired", "document_expiring_soon", "document_expiry_missing"}
)
REQUEST_REASONS = frozenset({"expired", "expiring_soon", "manual"})
REQUEST_STATUSES = frozenset(
    {"created", "awaiting_upload", "upload_received", "cancelled"}
)
ACTIVE_ALERT_STATUSES = MONITORING_ACTIVE_STATUSES
ACTIVE_REQUEST_STATUSES = frozenset(
    {"created", "awaiting_upload", "upload_received"}
)
# Protected legacy contract from monitoring_document_refresh. This duplicate is
# deliberate: the canonical service must not import or call the legacy module.
# A guard test requires the vocabularies to remain exactly aligned.
LEGACY_DOCUMENT_REFRESH_ACTIVE_STATUSES = frozenset(
    {"requested", "uploaded", "under_review", "rejected"}
)
MAX_TEXT_LENGTH = 1000
MAX_IDENTIFIER_LENGTH = 255
MAX_FILENAME_LENGTH = 255
MAX_STORAGE_KEY_LENGTH = 1000
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_REMINDER_LIMIT = 500
MAX_LIST_LIMIT = 100
MAX_LIST_OFFSET = 10_000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
OFFICER_ROLES = frozenset({"co", "sco", "admin"})


class RenewalError(RuntimeError):
    """Controlled, public-safe document-renewal failure."""

    def __init__(
        self,
        code: str,
        public_message: str,
        *,
        http_status: int = 409,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.code = code
        self.public_message = public_message
        self.http_status = http_status
        self.details = dict(details or {})
        super().__init__(public_message)

    def payload(self) -> Dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.public_message,
                **({"details": self.details} if self.details else {}),
            },
            "contract_version": CONTRACT_VERSION,
        }


def renewal_feature_enabled(feature_flags: Any = None) -> bool:
    """Return the single authoritative rollout flag for renewal writes."""

    if feature_flags is None:
        from environment import flags as feature_flags

    return bool(feature_flags.is_enabled(FEATURE_FLAG))


def _require_feature(feature_flags: Any) -> None:
    if not renewal_feature_enabled(feature_flags):
        raise RenewalError(
            "feature_disabled",
            "Document renewal automation is not enabled.",
            http_status=409,
        )


def _utc_now(value: Optional[datetime]) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).replace(microsecond=0)


def _iso_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _date(value: Any, *, field: str) -> date:
    if isinstance(value, datetime):
        result = _utc_now(value).date()
    elif isinstance(value, date):
        result = value
    elif isinstance(value, str):
        try:
            result = date.fromisoformat(value.strip())
        except ValueError as exc:
            raise RenewalError(
                "invalid_input", f"{field} must be an ISO date."
            ) from exc
    else:
        raise RenewalError("invalid_input", f"{field} must be an ISO date.")
    return result


def _timestamp_date(value: Any, *, field: str) -> date:
    if isinstance(value, datetime):
        return _utc_now(value).date()
    if isinstance(value, date):
        return value
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise RenewalError(
            "invalid_request_state",
            f"The persisted {field} timestamp is invalid.",
        ) from exc
    return _utc_now(parsed).date()


def _required_text(value: Any, *, field: str, maximum: int = MAX_TEXT_LENGTH) -> str:
    text = str(value or "").strip()
    if not text:
        raise RenewalError("invalid_input", f"{field} is required.")
    if len(text) > maximum:
        raise RenewalError(
            "invalid_input", f"{field} exceeds the {maximum}-character limit."
        )
    return text


def _actor(value: Mapping[str, Any]) -> Dict[str, str]:
    if not isinstance(value, Mapping):
        raise RenewalError("invalid_actor", "An authenticated actor is required.", http_status=403)
    actor_id = str(value.get("id") or value.get("sub") or "").strip()
    if not actor_id:
        raise RenewalError("invalid_actor", "An authenticated actor is required.", http_status=403)
    return {
        "id": actor_id[:MAX_IDENTIFIER_LENGTH],
        "name": str(value.get("name") or "").strip()[:MAX_IDENTIFIER_LENGTH],
        "role": str(value.get("role") or "").strip().lower()[:80],
        "type": str(value.get("type") or "").strip().lower()[:80],
        "customer_id": str(
            value.get("customer_id") or value.get("client_id") or ""
        ).strip()[:MAX_IDENTIFIER_LENGTH],
    }


def _require_officer(actor: Mapping[str, str]) -> None:
    if actor.get("role") not in OFFICER_ROLES or actor.get("type") != "officer":
        raise RenewalError(
            "insufficient_role",
            "A compliance officer is required for this renewal action.",
            http_status=403,
        )


def _require_request_creator(actor: Mapping[str, str], reason: str) -> None:
    """Allow officers, plus the bounded system detector for exact auto reasons."""

    if actor.get("type") == "officer" and actor.get("role") in OFFICER_ROLES:
        return
    if (
        actor.get("type") == "system"
        and actor.get("role") == "system"
        and reason in {"expired", "expiring_soon"}
    ):
        return
    raise RenewalError(
        "insufficient_role",
        "A compliance officer is required for manual renewal actions.",
        http_status=403,
    )


def _require_client(actor: Mapping[str, str], customer_id: Any) -> None:
    if actor.get("role") != "client" or actor.get("type") != "client":
        raise RenewalError(
            "insufficient_role",
            "An authenticated client is required for this upload.",
            http_status=403,
        )
    actor_customer_id = actor.get("customer_id") or actor.get("id")
    if str(actor_customer_id) != str(customer_id):
        raise RenewalError(
            "cross_customer_access",
            "This renewal request does not belong to the authenticated client.",
            http_status=403,
        )


def _require_system(actor: Mapping[str, str]) -> None:
    if actor.get("role") != "system" or actor.get("type") != "system":
        raise RenewalError(
            "insufficient_role",
            "The reminder operation requires the renewal scheduler identity.",
            http_status=403,
        )


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return _iso_timestamp(_utc_now(value))
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    raise RenewalError(
        "invalid_event_payload",
        "Renewal event payload contains an unsupported value.",
    )


def _json_dumps(value: Any) -> str:
    return json.dumps(
        _json_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _normalize_persisted_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(row)
    for field in ("due_date", "due_date_snapshot"):
        if isinstance(result.get(field), datetime):
            result[field] = _utc_now(result[field]).date().isoformat()
        elif isinstance(result.get(field), date):
            result[field] = result[field].isoformat()
    for field in (
        "created_at",
        "sent_at",
        "cancelled_at",
        "updated_at",
        "uploaded_at",
        "last_reminder_at",
    ):
        if isinstance(result.get(field), datetime):
            result[field] = _iso_timestamp(_utc_now(result[field]))
    return result


def _row(row: Any) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def _rows(cursor: Any) -> List[Dict[str, Any]]:
    return [dict(item) for item in cursor.fetchall()]


def _rowcount(cursor: Any) -> Optional[int]:
    direct = getattr(cursor, "rowcount", None)
    if direct is not None:
        return direct
    return getattr(getattr(cursor, "_cursor", None), "rowcount", None)


def _begin_write(db: Any) -> None:
    if getattr(db, "is_postgres", False):
        return
    raw = getattr(db, "conn", db)
    if not getattr(raw, "in_transaction", False):
        db.execute("BEGIN IMMEDIATE")


def _rollback(db: Any) -> None:
    try:
        db.rollback()
    except Exception:
        pass


def _lock_alert(db: Any, alert_id: Any) -> Dict[str, Any]:
    sql = "SELECT * FROM monitoring_alerts WHERE id = ?"
    if getattr(db, "is_postgres", False):
        sql += " FOR UPDATE"
    alert = _row(db.execute(sql, (alert_id,)).fetchone())
    if not alert:
        raise RenewalError("alert_not_found", "Monitoring Alert not found.", http_status=404)
    return alert


def _load_request(db: Any, request_id: Any, *, lock: bool = False) -> Dict[str, Any]:
    normalized = _required_text(
        request_id, field="request_id", maximum=MAX_IDENTIFIER_LENGTH
    )
    sql = "SELECT * FROM monitoring_document_renewal_requests WHERE request_id = ?"
    if lock and getattr(db, "is_postgres", False):
        sql += " FOR UPDATE"
    request = _row(db.execute(sql, (normalized,)).fetchone())
    if not request:
        raise RenewalError("request_not_found", "Renewal request not found.", http_status=404)
    return _normalize_persisted_row(request)


def _canonical_audit_writer() -> Callable[..., str]:
    from db import append_audit_log

    return append_audit_log


def _publish_portal_notification(
    db: Any,
    request: Mapping[str, Any],
    *,
    notification_type: str,
    title: str,
    message: str,
    action: str,
) -> str:
    del action  # Structured action evidence belongs in the renewal event/audit.
    subject_label = _subject_display_label(db, request)
    document_label = f"{request['document_type']} — {subject_label}"
    message = str(message).replace(
        str(request["document_type"]),
        document_label,
        1,
    )
    row = _row(
        db.execute(
            """
            INSERT INTO client_notifications
                (application_id, client_id, notification_type, title,
                 message, documents_list, read_status)
            VALUES (?, ?, ?, ?, ?, ?, FALSE)
            RETURNING id
            """,
            (
                request["application_id"],
                request["customer_id"],
                notification_type,
                title,
                message,
                _json_dumps([document_label]),
            ),
        ).fetchone()
    )
    notification_id = str((row or {}).get("id") or "").strip()
    if not notification_id:
        raise RenewalError(
            "notification_unavailable",
            "The client portal notification could not be published.",
            http_status=503,
        )
    return notification_id


def _event(
    db: Any,
    request: Mapping[str, Any],
    *,
    event_type: str,
    event_key: str,
    payload: Mapping[str, Any],
    actor: Mapping[str, str],
    timestamp: str,
    correlation_id: Optional[str],
    audit_writer: Optional[Callable[..., Any]],
    before_state: Optional[Mapping[str, Any]] = None,
    due_date_snapshot: Optional[str] = None,
    reminder_interval_days: Optional[int] = None,
) -> Dict[str, Any]:
    event_id = str(uuid.uuid4())
    sequence_row = _row(
        db.execute(
            """
            SELECT COALESCE(MAX(event_sequence), 0) + 1 AS event_sequence
              FROM monitoring_document_renewal_events
             WHERE request_id = ?
            """,
            (request["request_id"],),
        ).fetchone()
    ) or {}
    event_sequence = int(sequence_row.get("event_sequence") or 1)
    safe_payload = _json_safe(payload)
    serialized = _json_dumps(safe_payload)
    db.execute(
        """
        INSERT INTO monitoring_document_renewal_events
            (event_id, request_id, event_sequence, event_type, event_key, payload, created_at,
             created_by, due_date_snapshot, reminder_interval_days)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            request["request_id"],
            event_sequence,
            event_type,
            event_key,
            serialized,
            timestamp,
            actor["id"],
            due_date_snapshot,
            reminder_interval_days,
        ),
    )
    writer = audit_writer or _canonical_audit_writer()
    if not callable(writer):
        raise RenewalError(
            "audit_unavailable", "Renewal audit infrastructure is unavailable.", http_status=503
        )
    detail = {
        "contract_version": CONTRACT_VERSION,
        "originating_pr": ORIGINATING_PR,
        "event_id": event_id,
        "event_sequence": event_sequence,
        "event_type": event_type,
        "event_key": event_key,
        "request_id": request["request_id"],
        "monitoring_alert_id": request["monitoring_alert_id"],
        "payload": safe_payload,
    }
    audit_evidence = writer(
        db,
        action=f"monitoring.document_renewal.{event_type}",
        user_id=actor["id"],
        user_name=actor["name"],
        user_role=actor["role"],
        target=f"monitoring_renewal_request:{request['request_id']}",
        detail=_json_dumps(detail),
        before_state=dict(before_state or {}),
        after_state=dict(request),
        application_id=str(request["application_id"]),
        request_id=str(correlation_id or "") or None,
        commit=False,
    )
    if not isinstance(audit_evidence, str) or not audit_evidence.strip():
        raise RenewalError(
            "audit_unavailable",
            "Renewal audit infrastructure did not return append evidence.",
            http_status=503,
        )
    return {
        "event_id": event_id,
        "event_sequence": event_sequence,
        "event_type": event_type,
        "event_key": event_key,
        "payload": safe_payload,
        "created_at": timestamp,
        "created_by": actor["id"],
    }


def _linkage_fingerprint(
    *,
    alert: Mapping[str, Any],
    linkage: Mapping[str, Any],
    reason: str,
) -> str:
    document = linkage["document"]
    application = linkage["application"]
    payload = {
        "contract_version": CONTRACT_VERSION,
        "linkage_contract_version": linkage.get("contract_version"),
        "alert_id": str(alert.get("id")),
        "alert_type": str(alert.get("alert_type") or ""),
        "application_id": str(application.get("id") or ""),
        "customer_id": str(application.get("customer_id") or ""),
        "person_id": str(document.get("person_id") or ""),
        "person_type": str(document.get("person_type") or ""),
        "document_id": str(document.get("canonical_document_id") or ""),
        "document_type": str(document.get("document_type") or ""),
        "document_slot_key": str(document.get("slot_key") or ""),
        "document_version": document.get("current_document_version"),
        "current_expiry_date": document.get("current_expiry_date"),
        "reason": reason,
    }
    return hashlib.sha256(
        _json_dumps(payload).encode("utf-8")
    ).hexdigest()


def _lock_document_slot(db: Any, linkage: Mapping[str, Any]) -> None:
    """Share KYC & Documents' PostgreSQL slot lock for renewal mutations."""

    if not getattr(db, "is_postgres", False):
        return
    application = linkage.get("application") or {}
    document = linkage.get("document") or {}
    application_id = str(application.get("id") or "").strip()
    slot_key = str(document.get("slot_key") or "").strip()
    if not application_id or not slot_key:
        raise RenewalError(
            "linkage_not_authoritative",
            "The canonical document slot is unavailable and requires manual review.",
        )
    db.execute(
        "SELECT pg_advisory_xact_lock(hashtext(?)::bigint)",
        (f"{application_id}:{slot_key}",),
    )


def _validated_linkage(
    db: Any,
    alert: Mapping[str, Any],
    *,
    reason: Optional[str] = None,
    lock_slot: bool = False,
) -> Dict[str, Any]:
    status = str(alert.get("status") or "").strip()
    if status not in ACTIVE_ALERT_STATUSES:
        raise RenewalError(
            "alert_not_active",
            "Only an active Monitoring Alert can own a renewal request.",
            details={"status": status},
        )
    try:
        linkage = dict(resolve_alert_linkage(db, alert.get("id")))
    except LinkageError as exc:
        raise RenewalError(
            "linkage_not_authoritative",
            "The document linkage requires manual review.",
            http_status=exc.http_status,
            details={"linkage_code": exc.code},
        ) from exc
    if lock_slot:
        _lock_document_slot(db, linkage)
        try:
            linkage = dict(resolve_alert_linkage(db, alert.get("id")))
        except LinkageError as exc:
            raise RenewalError(
                "linkage_not_authoritative",
                "The document linkage changed and requires manual review.",
                http_status=exc.http_status,
                details={"linkage_code": exc.code},
            ) from exc
    if (
        linkage.get("linkage_type") != "document_expiry"
        or linkage.get("linkage_status") != "linked"
        or (linkage.get("owner") or {}).get("module") != "kyc_documents"
    ):
        raise RenewalError(
            "unsupported_alert_type",
            "This Monitoring Alert is not an authoritative document-renewal alert.",
            http_status=422,
        )
    document = linkage.get("document") or {}
    application = linkage.get("application") or {}
    required = {
        "application_id": application.get("id"),
        "customer_id": application.get("customer_id"),
        "document_id": document.get("canonical_document_id"),
        "document_type": document.get("document_type"),
        "document_version": document.get("current_document_version"),
    }
    if any(value in (None, "") for value in required.values()):
        raise RenewalError(
            "linkage_not_authoritative",
            "The document linkage is incomplete and requires manual review.",
        )
    if document.get("source_is_current") is not True:
        raise RenewalError(
            "stale_document_alert",
            "The alert does not point to the current canonical document version.",
        )
    if str(application["id"]) != str(alert.get("application_id")):
        raise RenewalError(
            "linkage_not_authoritative", "The document application linkage changed."
        )
    alert_type = str(alert.get("alert_type") or "")
    if reason is not None:
        if reason not in REQUEST_REASONS:
            raise RenewalError("invalid_reason", "Unsupported renewal request reason.")
        if reason == "manual":
            if alert_type not in MANUAL_ALERT_TYPES:
                raise RenewalError(
                    "unsupported_alert_type",
                    "Manual renewal is not allowed for this alert type.",
                    http_status=422,
                )
        elif AUTOMATIC_ALERT_REASONS.get(alert_type) != reason:
            raise RenewalError(
                "reason_alert_mismatch",
                "The automatic renewal reason does not match the exact alert type.",
                http_status=422,
            )
    return linkage


def _validate_eligibility(
    alert: Mapping[str, Any],
    linkage: Mapping[str, Any],
    *,
    reason: str,
    today: date,
) -> None:
    if reason == "manual":
        return
    expiry_value = (linkage.get("document") or {}).get("current_expiry_date")
    if not expiry_value:
        raise RenewalError(
            "missing_expiry_evidence",
            "The current document has no canonical expiry date.",
        )
    expiry = _date(expiry_value, field="current_expiry_date")
    if reason == "expired" and expiry >= today:
        raise RenewalError(
            "not_eligible", "The current document is not expired."
        )
    if reason == "expiring_soon" and not (
        today <= expiry <= today + timedelta(days=EXPIRING_SOON_DAYS)
    ):
        raise RenewalError(
            "not_eligible",
            "The current document is not within the approved expiry window.",
        )


def _validate_request_linkage(
    db: Any,
    request: Mapping[str, Any],
    alert: Mapping[str, Any],
    *,
    lock_slot: bool = False,
) -> Dict[str, Any]:
    linkage = _validated_linkage(db, alert, lock_slot=lock_slot)
    document = linkage["document"]
    application = linkage["application"]
    comparisons = {
        "application_id": (request.get("application_id"), application.get("id")),
        "customer_id": (request.get("customer_id"), application.get("customer_id")),
        "person_id": (request.get("person_id") or "", document.get("person_id") or ""),
        "person_type": (
            request.get("person_type") or "",
            document.get("person_type") or "",
        ),
        "document_id": (
            request.get("document_id"),
            document.get("canonical_document_id"),
        ),
        "document_type": (
            request.get("document_type"),
            document.get("document_type"),
        ),
        "document_slot_key": (
            request.get("document_slot_key"),
            document.get("slot_key"),
        ),
        "document_version": (
            str(request.get("document_version")),
            str(document.get("current_document_version")),
        ),
    }
    mismatches = [
        key for key, (stored, resolved) in comparisons.items() if str(stored) != str(resolved)
    ]
    if mismatches:
        raise RenewalError(
            "stale_linkage",
            "The canonical document linkage changed; refresh and review manually.",
            details={"mismatched_fields": mismatches},
        )
    expected_fingerprint = _linkage_fingerprint(
        alert=alert, linkage=linkage, reason=str(request.get("request_reason") or "")
    )
    if str(request.get("eligibility_fingerprint") or "") != expected_fingerprint:
        raise RenewalError(
            "stale_eligibility",
            "The audited renewal eligibility preconditions changed.",
        )
    return linkage


def _legacy_conflict(
    db: Any,
    *,
    alert_id: Any,
    application_id: Any,
    trigger_document_id: Any,
    document_slot_key: Any,
) -> Optional[Dict[str, Any]]:
    legacy_statuses = tuple(sorted(LEGACY_DOCUMENT_REFRESH_ACTIVE_STATUSES))
    status_placeholders = ",".join("?" for _ in legacy_statuses)
    conflict = _row(
        db.execute(
            f"""
            SELECT aer.id, aer.status, aer.monitoring_document_id
             FROM application_enhanced_requirements aer
             LEFT JOIN documents md
               ON md.id = aer.monitoring_document_id
              AND md.application_id = aer.application_id
             LEFT JOIN documents ld
               ON ld.id = aer.linked_document_id
              AND ld.application_id = aer.application_id
             WHERE active = 1
               AND LOWER(COALESCE(aer.status, '')) IN ({status_placeholders})
               AND (
                    aer.monitoring_alert_id = ?
                    OR (
                        aer.application_id = ?
                        AND (md.slot_key = ? OR ld.slot_key = ?)
                    )
               )
             ORDER BY aer.id ASC
             LIMIT 1
            """,
            (
                *legacy_statuses,
                alert_id,
                application_id,
                document_slot_key,
                document_slot_key,
            ),
        ).fetchone()
    )
    if conflict:
        conflict["document_mismatch"] = (
            str(conflict.get("monitoring_document_id") or "")
            != str(trigger_document_id or "")
        )
    return conflict


def _project(row: Mapping[str, Any]) -> Dict[str, Any]:
    result = _normalize_persisted_row(row)
    status = str(result.get("request_status") or "")
    result.update(
        {
            "contract_version": CONTRACT_VERSION,
            "read_only_projection": True,
            "document_mutation_controls": False,
            "verification_controls": False,
            "monitoring_resolution_controls": False,
            "display_status": {
                "created": "Renewal Requested",
                "awaiting_upload": "Awaiting Upload",
                "upload_received": "Upload Received",
                "cancelled": "Cancelled",
            }.get(status, "Unavailable"),
            "milestones": [
                {"key": "renewal_requested", "label": "Renewal Requested", "complete": True},
                {
                    "key": "request_sent",
                    "label": "Request Sent",
                    "complete": bool(result.get("sent_at")),
                },
                {
                    "key": "awaiting_upload",
                    "label": "Awaiting Upload",
                    "complete": bool(result.get("sent_at")),
                },
            ],
        }
    )
    return result


def _subject_display_label(db: Any, request: Mapping[str, Any]) -> str:
    """Derive client-safe context by exact application/person identity only."""

    application_id = str(request.get("application_id") or "").strip()
    person_id = str(request.get("person_id") or "").strip()
    person_type = str(request.get("person_type") or "").strip()
    if not person_id:
        application = _row(
            db.execute(
                "SELECT company_name FROM applications WHERE id = ?",
                (application_id,),
            ).fetchone()
        ) or {}
        company_name = str(application.get("company_name") or "").strip()
        return f"Entity — {company_name}" if company_name else "Application entity"
    owner_tables = {
        "director": ("directors", "full_name", "Director"),
        "ubo": ("ubos", "full_name", "UBO"),
        "intermediary": ("intermediaries", "entity_name", "Intermediary"),
    }
    owner = owner_tables.get(person_type)
    if not owner:
        type_label = "Application participant"
        display_name = ""
    else:
        table, label_column, type_label = owner
        row = _row(
            db.execute(
                f"SELECT {label_column} AS display_name FROM {table} "
                "WHERE id = ? AND application_id = ?",
                (person_id, application_id),
            ).fetchone()
        ) or {}
        display_name = str(row.get("display_name") or "").strip()
    safe_reference = hashlib.sha256(
        f"{application_id}:{person_type}:{person_id}".encode("utf-8")
    ).hexdigest()[:8].upper()
    visible = f"{type_label} — {display_name}" if display_name else type_label
    return f"{visible} · ref {safe_reference}"


def _project_for_read(db: Any, row: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a truthful projection after revalidating current owner linkage."""

    result = _project(row)
    result["subject_display_label"] = _subject_display_label(db, result)
    if result.get("request_status") == "cancelled":
        result.update(
            {
                "linkage_current": None,
                "upload_allowed": False,
                "manual_review_required": False,
            }
        )
        return result
    try:
        alert = _row(
            db.execute(
                "SELECT * FROM monitoring_alerts WHERE id = ?",
                (result["monitoring_alert_id"],),
            ).fetchone()
        )
        if not alert:
            raise RenewalError(
                "alert_not_found", "Monitoring Alert not found.", http_status=404
            )
        _validate_request_linkage(db, result, alert)
        result.update(
            {
                "linkage_current": True,
                "upload_allowed": result.get("request_status") == "awaiting_upload",
                "manual_review_required": False,
            }
        )
    except RenewalError as exc:
        result.update(
            {
                "linkage_current": False,
                "upload_allowed": False,
                "manual_review_required": True,
                "linkage_error": {
                    "code": exc.code,
                    "message": exc.public_message,
                },
            }
        )
    return result


def _history(db: Any, request_id: str) -> Dict[str, List[Dict[str, Any]]]:
    events = _rows(
        db.execute(
            """
            SELECT event_id, request_id, event_sequence, event_type, event_key, payload,
                   created_at, created_by, due_date_snapshot,
                   reminder_interval_days
              FROM monitoring_document_renewal_events
             WHERE request_id = ?
             ORDER BY event_sequence ASC
            """,
            (request_id,),
        )
    )
    for event in events:
        event.update(_normalize_persisted_row(event))
        payload = event.get("payload")
        if isinstance(payload, Mapping):
            event["payload"] = dict(payload)
        else:
            try:
                decoded = json.loads(payload or "{}")
                event["payload"] = dict(decoded) if isinstance(decoded, Mapping) else {}
            except (TypeError, ValueError):
                event["payload"] = {}
    uploads = _rows(
        db.execute(
            """
            SELECT upload_id, request_id, application_id, customer_id,
                   original_filename, file_size, mime_type, file_sha256,
                   uploaded_at, uploaded_by
              FROM monitoring_document_renewal_uploads
             WHERE request_id = ?
             ORDER BY uploaded_at ASC, upload_id ASC
            """,
            (request_id,),
        )
    )
    uploads = [_normalize_persisted_row(upload) for upload in uploads]
    return {"events": events, "uploads": uploads}


def _with_reminder_summary(
    db: Any, request: Mapping[str, Any]
) -> Dict[str, Any]:
    """Attach truthful generated-reminder evidence to an officer projection."""

    result = _project_for_read(db, request)
    summary = _row(
        db.execute(
            """
            SELECT COUNT(*) AS reminder_count, MAX(created_at) AS last_reminder_at
              FROM monitoring_document_renewal_events
             WHERE request_id = ? AND event_type = 'reminder_generated'
            """,
            (result["request_id"],),
        ).fetchone()
    ) or {}
    normalized = _normalize_persisted_row(summary)
    result["reminder_count"] = int(normalized.get("reminder_count") or 0)
    result["last_reminder_at"] = normalized.get("last_reminder_at")
    return result


def get_renewal_request(
    db: Any,
    request_id: Any,
    *,
    include_history: bool = True,
) -> Dict[str, Any]:
    result = _project_for_read(db, _load_request(db, request_id))
    if include_history:
        result.update(_history(db, result["request_id"]))
    return result


def renewal_request_for_alert(
    db: Any,
    alert_id: Any,
    *,
    include_cancelled: bool = False,
) -> Optional[Dict[str, Any]]:
    sql = (
        "SELECT * FROM monitoring_document_renewal_requests "
        "WHERE monitoring_alert_id = ?"
    )
    params: List[Any] = [alert_id]
    if not include_cancelled:
        sql += " AND request_status <> ?"
        params.append("cancelled")
    sql += (
        " ORDER BY CASE WHEN request_status <> 'cancelled' THEN 0 ELSE 1 END, "
        "created_at DESC, request_id DESC LIMIT 1"
    )
    request = _row(db.execute(sql, tuple(params)).fetchone())
    return _with_reminder_summary(db, request) if request else None


def _renewal_request_list_filter(
    *,
    application_id: Any,
    customer_id: Any,
    monitoring_alert_id: Any,
    include_cancelled: bool,
) -> tuple[List[str], List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    for field, value in (
        ("application_id", application_id),
        ("customer_id", customer_id),
        ("monitoring_alert_id", monitoring_alert_id),
    ):
        if value not in (None, ""):
            clauses.append(f"{field} = ?")
            params.append(value)
    if not include_cancelled:
        clauses.append("request_status <> ?")
        params.append("cancelled")
    return clauses, params


def count_renewal_requests(
    db: Any,
    *,
    application_id: Any = None,
    customer_id: Any = None,
    monitoring_alert_id: Any = None,
    include_cancelled: bool = False,
) -> int:
    clauses, params = _renewal_request_list_filter(
        application_id=application_id,
        customer_id=customer_id,
        monitoring_alert_id=monitoring_alert_id,
        include_cancelled=include_cancelled,
    )
    sql = "SELECT COUNT(*) AS request_count FROM monitoring_document_renewal_requests"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    result = _row(db.execute(sql, tuple(params)).fetchone()) or {}
    return int(result.get("request_count") or 0)


def list_renewal_requests(
    db: Any,
    *,
    application_id: Any = None,
    customer_id: Any = None,
    monitoring_alert_id: Any = None,
    include_cancelled: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    try:
        bounded_limit = int(limit)
        bounded_offset = int(offset)
    except (TypeError, ValueError) as exc:
        raise RenewalError(
            "invalid_pagination", "limit and offset must be integers.", http_status=400
        ) from exc
    if bounded_limit < 1 or bounded_limit > MAX_LIST_LIMIT:
        raise RenewalError(
            "invalid_pagination",
            f"limit must be between 1 and {MAX_LIST_LIMIT}.",
            http_status=400,
        )
    if bounded_offset < 0 or bounded_offset > MAX_LIST_OFFSET:
        raise RenewalError(
            "invalid_pagination",
            f"offset must be between 0 and {MAX_LIST_OFFSET}.",
            http_status=400,
        )
    clauses, params = _renewal_request_list_filter(
        application_id=application_id,
        customer_id=customer_id,
        monitoring_alert_id=monitoring_alert_id,
        include_cancelled=include_cancelled,
    )
    sql = "SELECT * FROM monitoring_document_renewal_requests"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC, request_id DESC LIMIT ? OFFSET ?"
    params.extend((bounded_limit, bounded_offset))
    return [
        _project_for_read(db, item)
        for item in _rows(db.execute(sql, tuple(params)))
    ]


def create_renewal_request(
    db: Any,
    alert_id: Any,
    *,
    reason: str,
    actor: Mapping[str, Any],
    due_date: Any = None,
    manual_reason: Any = None,
    now: Optional[datetime] = None,
    request_id: Optional[str] = None,
    feature_flags: Any = None,
    audit_writer: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    _require_feature(feature_flags)
    normalized_reason = str(reason or "").strip()
    if normalized_reason not in REQUEST_REASONS:
        raise RenewalError("invalid_reason", "Unsupported renewal request reason.")
    normalized_manual_reason = str(manual_reason or "").strip()
    if normalized_reason == "manual":
        normalized_manual_reason = _required_text(
            normalized_manual_reason, field="manual_reason"
        )
    elif normalized_manual_reason:
        raise RenewalError(
            "invalid_input", "manual_reason is permitted only for manual renewal requests."
        )
    actor_info = _actor(actor)
    _require_request_creator(actor_info, normalized_reason)
    timestamp = _utc_now(now)
    timestamp_text = _iso_timestamp(timestamp)
    due = (
        _date(due_date, field="due_date")
        if due_date is not None
        else timestamp.date() + timedelta(days=DEFAULT_DUE_DAYS)
    )
    if due < timestamp.date() or due > timestamp.date() + timedelta(days=365):
        raise RenewalError(
            "invalid_due_date", "due_date must be between today and 365 days from today."
        )
    normalized_request_id = (
        _required_text(request_id, field="request_id", maximum=MAX_IDENTIFIER_LENGTH)
        if request_id is not None
        else str(uuid.uuid4())
    )
    try:
        _begin_write(db)
        alert = _lock_alert(db, alert_id)
        original_alert_status = alert.get("status")
        linkage = _validated_linkage(db, alert, reason=normalized_reason)
        _validate_eligibility(
            alert, linkage, reason=normalized_reason, today=timestamp.date()
        )
        document = linkage["document"]
        application = linkage["application"]
        initial_slot_identity = (
            str(application.get("id") or ""),
            str(document.get("slot_key") or ""),
        )
        _lock_document_slot(db, linkage)
        linkage = _validated_linkage(db, alert, reason=normalized_reason)
        document = linkage["document"]
        application = linkage["application"]
        locked_slot_identity = (
            str(application.get("id") or ""),
            str(document.get("slot_key") or ""),
        )
        if locked_slot_identity != initial_slot_identity:
            raise RenewalError(
                "stale_linkage",
                "The canonical document slot changed; refresh and review manually.",
            )
        _validate_eligibility(
            alert, linkage, reason=normalized_reason, today=timestamp.date()
        )
        # The legacy path takes this same application+document-slot lock before
        # deciding whether it may create work. Recheck only after the shared
        # lock so different alerts for the same document cannot split ownership.
        legacy = _legacy_conflict(
            db,
            alert_id=alert_id,
            application_id=application.get("id"),
            trigger_document_id=document.get("trigger_document_id"),
            document_slot_key=document.get("slot_key"),
        )
        if legacy:
            raise RenewalError(
                "legacy_request_conflict",
                "An active legacy document request already exists for this alert.",
                details={
                    "legacy_request_id": legacy.get("id"),
                    "document_mismatch": bool(legacy.get("document_mismatch")),
                },
            )
        if normalized_reason == "expiring_soon":
            canonical_expiry = _date(
                document.get("current_expiry_date"),
                field="current_expiry_date",
            )
            if due_date is None:
                due = min(due, canonical_expiry)
            elif due > canonical_expiry:
                raise RenewalError(
                    "invalid_due_date",
                    "An expiring-document request cannot be due after the canonical expiry date.",
                )
        fingerprint = _linkage_fingerprint(
            alert=alert, linkage=linkage, reason=normalized_reason
        )
        existing_for_alert = _row(
            db.execute(
                """
                SELECT *
                  FROM monitoring_document_renewal_requests
                 WHERE monitoring_alert_id = ?
                   AND request_status <> 'cancelled'
                 ORDER BY created_at ASC, request_id ASC
                 LIMIT 1
                """,
                (alert_id,),
            ).fetchone()
        )
        if existing_for_alert:
            if str(existing_for_alert.get("eligibility_fingerprint") or "") == fingerprint:
                db.commit()
                result = _project(existing_for_alert)
                result["idempotent"] = True
                return result
            raise RenewalError(
                "active_alert_request_conflict",
                "This alert already has an active renewal request for different audited linkage.",
                details={"request_id": existing_for_alert.get("request_id")},
            )
        existing = _row(
            db.execute(
                """
                SELECT * FROM monitoring_document_renewal_requests
                 WHERE application_id = ?
                   AND COALESCE(person_id, '') = ?
                   AND document_slot_key = ?
                   AND request_status <> 'cancelled'
                 ORDER BY created_at ASC, request_id ASC
                 LIMIT 1
                """,
                (
                    application["id"],
                    str(document.get("person_id") or ""),
                    document["slot_key"],
                ),
            ).fetchone()
        )
        if existing:
            if (
                str(existing.get("monitoring_alert_id")) == str(alert_id)
                and str(existing.get("eligibility_fingerprint")) == fingerprint
            ):
                db.commit()
                result = _project(existing)
                result["idempotent"] = True
                return result
            raise RenewalError(
                "duplicate_active_request",
                "An active renewal request already exists for this document.",
                details={"request_id": existing.get("request_id")},
            )
        request = {
            "request_id": normalized_request_id,
            "application_id": str(application["id"]),
            "customer_id": str(application["customer_id"]),
            "person_id": document.get("person_id"),
            "person_type": document.get("person_type"),
            "document_id": str(document["canonical_document_id"]),
            "document_type": str(document["document_type"]),
            "document_slot_key": str(document["slot_key"]),
            "document_version": int(document["current_document_version"]),
            "monitoring_alert_id": alert_id,
            "request_reason": normalized_reason,
            "request_status": "created",
            "created_at": timestamp_text,
            "due_date": due.isoformat(),
            "created_by": actor_info["id"],
            "sent_at": None,
            "cancelled_at": None,
            "cancelled_by": None,
            "cancel_reason": None,
            "updated_at": timestamp_text,
            "updated_by": actor_info["id"],
            "revision": 1,
            "contract_version": CONTRACT_VERSION,
            "eligibility_fingerprint": fingerprint,
        }
        db.execute(
            """
            INSERT INTO monitoring_document_renewal_requests
                (request_id, application_id, customer_id, person_id,
                 person_type, document_id, document_type, document_slot_key,
                 document_version,
                 monitoring_alert_id, request_reason, request_status,
                 created_at, due_date, created_by, sent_at, cancelled_at,
                 cancelled_by, cancel_reason, updated_at, updated_by, revision,
                 contract_version, eligibility_fingerprint)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request["request_id"],
                request["application_id"],
                request["customer_id"],
                request["person_id"],
                request["person_type"],
                request["document_id"],
                request["document_type"],
                request["document_slot_key"],
                request["document_version"],
                request["monitoring_alert_id"],
                request["request_reason"],
                request["request_status"],
                request["created_at"],
                request["due_date"],
                request["created_by"],
                request["sent_at"],
                request["cancelled_at"],
                request["cancelled_by"],
                request["cancel_reason"],
                request["updated_at"],
                request["updated_by"],
                request["revision"],
                request["contract_version"],
                request["eligibility_fingerprint"],
            ),
        )
        creation_payload = {
            "reason": normalized_reason,
            "due_date": due.isoformat(),
            "document_id": request["document_id"],
            "document_version": request["document_version"],
            **(
                {"manual_reason": normalized_manual_reason}
                if normalized_reason == "manual"
                else {}
            ),
        }
        _event(
            db,
            request,
            event_type="request_created",
            event_key=f"request_created:{normalized_request_id}",
            payload=creation_payload,
            actor=actor_info,
            timestamp=timestamp_text,
            correlation_id=None,
            audit_writer=audit_writer,
            due_date_snapshot=due.isoformat(),
        )
        before_sent = dict(request)
        db.execute(
            """
            UPDATE monitoring_document_renewal_requests
               SET request_status = 'awaiting_upload', sent_at = ?,
                   updated_at = ?, updated_by = ?, revision = 2
             WHERE request_id = ? AND request_status = 'created' AND revision = 1
            """,
            (timestamp_text, timestamp_text, actor_info["id"], normalized_request_id),
        )
        if _rowcount(db) not in (None, -1, 1):
            raise RenewalError("stale_request", "Renewal request changed during creation.")
        request.update(
            {
                "request_status": "awaiting_upload",
                "sent_at": timestamp_text,
                "updated_at": timestamp_text,
                "revision": 2,
            }
        )
        portal_notification_id = _publish_portal_notification(
            db,
            request,
            notification_type="document_renewal_request",
            title="Document renewal requested",
            message=(
                f"Compliance requested a renewed {request['document_type']} "
                f"by {request['due_date']}. Open the application to upload it."
            ),
            action="upload_requested",
        )
        _event(
            db,
            request,
            event_type="request_sent",
            event_key=f"request_sent:{normalized_request_id}",
            payload={
                "due_date": due.isoformat(),
                "portal_notification_id": portal_notification_id,
                "delivery_channel": "client_portal",
            },
            actor=actor_info,
            timestamp=timestamp_text,
            correlation_id=None,
            audit_writer=audit_writer,
            before_state=before_sent,
            due_date_snapshot=due.isoformat(),
        )
        unchanged = _row(
            db.execute("SELECT status FROM monitoring_alerts WHERE id = ?", (alert_id,)).fetchone()
        )
        if not unchanged or unchanged.get("status") != original_alert_status:
            raise RenewalError(
                "protected_state_changed",
                "Monitoring Alert state changed during renewal request creation.",
            )
        db.commit()
        return _project(request)
    except Exception:
        _rollback(db)
        raise


def _mutable_request(
    db: Any,
    request_id: Any,
    *,
    expected_revision: Any,
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    initial = _load_request(db, request_id)
    alert = _lock_alert(db, initial["monitoring_alert_id"])
    request = _load_request(db, request_id, lock=True)
    _require_revision(request, expected_revision)
    linkage = _validate_request_linkage(db, request, alert, lock_slot=True)
    return request, alert, linkage


def _require_revision(request: Mapping[str, Any], expected_revision: Any) -> None:
    try:
        revision = int(expected_revision)
    except (TypeError, ValueError) as exc:
        raise RenewalError("invalid_revision", "expected_revision must be an integer.") from exc
    if int(request.get("revision") or 0) != revision:
        raise RenewalError(
            "stale_request",
            "The renewal request changed since it was read; refresh and retry.",
            details={"current_revision": request.get("revision")},
        )


def _require_single_update(cursor: Any) -> None:
    if _rowcount(cursor) not in (None, -1, 1):
        raise RenewalError(
            "stale_request", "The renewal request changed during the operation."
        )


def resend_renewal_request(
    db: Any,
    request_id: Any,
    *,
    actor: Mapping[str, Any],
    expected_revision: Any,
    now: Optional[datetime] = None,
    correlation_id: Optional[str] = None,
    feature_flags: Any = None,
    audit_writer: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    _require_feature(feature_flags)
    actor_info = _actor(actor)
    _require_officer(actor_info)
    timestamp = _iso_timestamp(_utc_now(now))
    try:
        _begin_write(db)
        request, alert, _linkage = _mutable_request(
            db, request_id, expected_revision=expected_revision
        )
        if request["request_status"] != "awaiting_upload":
            raise RenewalError(
                "invalid_request_state", "Only a request awaiting upload can be resent."
            )
        before = dict(request)
        next_revision = int(request["revision"]) + 1
        _require_single_update(
            db.execute(
                """
                UPDATE monitoring_document_renewal_requests
                   SET sent_at = ?, updated_at = ?, updated_by = ?, revision = ?
                 WHERE request_id = ? AND revision = ?
                   AND request_status = 'awaiting_upload'
                """,
                (
                    timestamp,
                    timestamp,
                    actor_info["id"],
                    next_revision,
                    request["request_id"],
                    request["revision"],
                ),
            )
        )
        request.update(
            {"sent_at": timestamp, "updated_at": timestamp, "updated_by": actor_info["id"], "revision": next_revision}
        )
        portal_notification_id = _publish_portal_notification(
            db,
            request,
            notification_type="document_renewal_request_resent",
            title="Document renewal request reminder",
            message=(
                f"Compliance re-sent the request for a renewed "
                f"{request['document_type']} due {request['due_date']}."
            ),
            action="request_resent",
        )
        _event(
            db,
            request,
            event_type="request_resent",
            event_key=f"request_resent:{request['request_id']}:{next_revision}",
            payload={
                "due_date": request["due_date"],
                "portal_notification_id": portal_notification_id,
                "delivery_channel": "client_portal",
            },
            actor=actor_info,
            timestamp=timestamp,
            correlation_id=correlation_id,
            audit_writer=audit_writer,
            before_state=before,
            due_date_snapshot=request["due_date"],
        )
        if _lock_alert(db, alert["id"])["status"] != alert["status"]:
            raise RenewalError("protected_state_changed", "Monitoring Alert state changed.")
        db.commit()
        return _project(request)
    except Exception:
        _rollback(db)
        raise


def cancel_renewal_request(
    db: Any,
    request_id: Any,
    *,
    actor: Mapping[str, Any],
    expected_revision: Any,
    cancel_reason: Any,
    now: Optional[datetime] = None,
    correlation_id: Optional[str] = None,
    feature_flags: Any = None,
    audit_writer: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    _require_feature(feature_flags)
    actor_info = _actor(actor)
    _require_officer(actor_info)
    reason = _required_text(cancel_reason, field="cancel_reason")
    timestamp = _iso_timestamp(_utc_now(now))
    try:
        _begin_write(db)
        initial = _load_request(db, request_id)
        alert = _lock_alert(db, initial["monitoring_alert_id"])
        request = _load_request(db, request_id, lock=True)
        _require_revision(request, expected_revision)
        if request["request_status"] not in {"awaiting_upload", "upload_received"}:
            raise RenewalError(
                "invalid_request_state",
                "Only an active renewal request can be cancelled.",
            )
        linkage_current = True
        linkage_error_code = None
        try:
            _validate_request_linkage(db, request, alert)
        except RenewalError as exc:
            # Cancellation is the audited escape hatch for a request whose
            # owner linkage or alert lifecycle changed after creation.  The
            # revision and rows remain locked, but stale owner state must not
            # make an active request impossible to stop.
            linkage_current = False
            linkage_error_code = exc.code
        before = dict(request)
        next_revision = int(request["revision"]) + 1
        _require_single_update(
            db.execute(
                """
                UPDATE monitoring_document_renewal_requests
                   SET request_status = 'cancelled', cancelled_at = ?,
                       cancelled_by = ?, cancel_reason = ?, updated_at = ?,
                       updated_by = ?, revision = ?
                 WHERE request_id = ? AND revision = ?
                   AND request_status IN ('awaiting_upload', 'upload_received')
                """,
                (
                    timestamp,
                    actor_info["id"],
                    reason,
                    timestamp,
                    actor_info["id"],
                    next_revision,
                    request["request_id"],
                    request["revision"],
                ),
            )
        )
        request.update(
            {
                "request_status": "cancelled",
                "cancelled_at": timestamp,
                "cancelled_by": actor_info["id"],
                "cancel_reason": reason,
                "updated_at": timestamp,
                "updated_by": actor_info["id"],
                "revision": next_revision,
            }
        )
        portal_notification_id = _publish_portal_notification(
            db,
            request,
            notification_type="document_renewal_request_cancelled",
            title="Document renewal request cancelled",
            message=(
                f"Compliance cancelled the renewal request for "
                f"{request['document_type']}. No further upload is requested."
            ),
            action="request_cancelled",
        )
        _event(
            db,
            request,
            event_type="request_cancelled",
            event_key=f"request_cancelled:{request['request_id']}",
            payload={
                "cancel_reason": reason,
                "linkage_current": linkage_current,
                "portal_notification_id": portal_notification_id,
                "delivery_channel": "client_portal",
                **(
                    {"linkage_error_code": linkage_error_code}
                    if linkage_error_code
                    else {}
                ),
            },
            actor=actor_info,
            timestamp=timestamp,
            correlation_id=correlation_id,
            audit_writer=audit_writer,
            before_state=before,
            due_date_snapshot=request["due_date"],
        )
        if _lock_alert(db, alert["id"])["status"] != alert["status"]:
            raise RenewalError("protected_state_changed", "Monitoring Alert state changed.")
        db.commit()
        return _project(request)
    except Exception:
        _rollback(db)
        raise


def update_renewal_due_date(
    db: Any,
    request_id: Any,
    *,
    actor: Mapping[str, Any],
    expected_revision: Any,
    due_date: Any,
    reason: Any,
    now: Optional[datetime] = None,
    correlation_id: Optional[str] = None,
    feature_flags: Any = None,
    audit_writer: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    _require_feature(feature_flags)
    actor_info = _actor(actor)
    _require_officer(actor_info)
    rationale = _required_text(reason, field="reason")
    current = _utc_now(now)
    timestamp = _iso_timestamp(current)
    new_due = _date(due_date, field="due_date")
    if new_due < current.date() or new_due > current.date() + timedelta(days=365):
        raise RenewalError(
            "invalid_due_date", "due_date must be between today and 365 days from today."
        )
    try:
        _begin_write(db)
        request, alert, linkage = _mutable_request(
            db, request_id, expected_revision=expected_revision
        )
        if request["request_status"] != "awaiting_upload":
            raise RenewalError(
                "invalid_request_state",
                "Only a request awaiting upload can have its due date changed.",
            )
        if request.get("request_reason") == "expiring_soon":
            canonical_expiry = _date(
                (linkage.get("document") or {}).get("current_expiry_date"),
                field="current_expiry_date",
            )
            if new_due > canonical_expiry:
                raise RenewalError(
                    "invalid_due_date",
                    "An expiring-document request cannot be due after the canonical expiry date.",
                )
        if str(request["due_date"]) == new_due.isoformat():
            db.commit()
            result = _project(request)
            result["idempotent"] = True
            return result
        before = dict(request)
        next_revision = int(request["revision"]) + 1
        _require_single_update(
            db.execute(
                """
                UPDATE monitoring_document_renewal_requests
                   SET due_date = ?, updated_at = ?, updated_by = ?, revision = ?
                 WHERE request_id = ? AND revision = ?
                   AND request_status = 'awaiting_upload'
                """,
                (
                    new_due.isoformat(),
                    timestamp,
                    actor_info["id"],
                    next_revision,
                    request["request_id"],
                    request["revision"],
                ),
            )
        )
        request.update(
            {
                "due_date": new_due.isoformat(),
                "updated_at": timestamp,
                "updated_by": actor_info["id"],
                "revision": next_revision,
            }
        )
        portal_notification_id = _publish_portal_notification(
            db,
            request,
            notification_type="document_renewal_due_date_changed",
            title="Document renewal due date changed",
            message=(
                f"The due date for your renewed {request['document_type']} "
                f"is now {request['due_date']}."
            ),
            action="due_date_changed",
        )
        _event(
            db,
            request,
            event_type="due_date_changed",
            event_key=f"due_date_changed:{request['request_id']}:{next_revision}",
            payload={
                "previous_due_date": before["due_date"],
                "new_due_date": new_due.isoformat(),
                "reason": rationale,
                "portal_notification_id": portal_notification_id,
                "delivery_channel": "client_portal",
            },
            actor=actor_info,
            timestamp=timestamp,
            correlation_id=correlation_id,
            audit_writer=audit_writer,
            before_state=before,
            due_date_snapshot=new_due.isoformat(),
        )
        if _lock_alert(db, alert["id"])["status"] != alert["status"]:
            raise RenewalError("protected_state_changed", "Monitoring Alert state changed.")
        db.commit()
        return _project(request)
    except Exception:
        _rollback(db)
        raise


def record_renewal_upload(
    db: Any,
    request_id: Any,
    *,
    actor: Mapping[str, Any],
    expected_revision: Any,
    artifact_reservation_id: Any,
    upload_id: Any,
    original_filename: Any,
    storage_key: Any,
    file_size: Any,
    mime_type: Any,
    file_sha256: Any,
    now: Optional[datetime] = None,
    correlation_id: Optional[str] = None,
    feature_flags: Any = None,
    audit_writer: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    _require_feature(feature_flags)
    actor_info = _actor(actor)
    normalized_upload_id = _required_text(
        upload_id, field="upload_id", maximum=MAX_IDENTIFIER_LENGTH
    )
    normalized_reservation_id = _required_text(
        artifact_reservation_id,
        field="artifact_reservation_id",
        maximum=MAX_IDENTIFIER_LENGTH,
    )
    filename = _required_text(
        original_filename, field="original_filename", maximum=MAX_FILENAME_LENGTH
    )
    key = _required_text(storage_key, field="storage_key", maximum=MAX_STORAGE_KEY_LENGTH)
    mime = _required_text(mime_type, field="mime_type", maximum=255)
    digest = str(file_sha256 or "").strip().lower()
    if not _SHA256_RE.fullmatch(digest):
        raise RenewalError("invalid_upload", "file_sha256 must be a lowercase SHA-256 digest.")
    try:
        size = int(file_size)
    except (TypeError, ValueError) as exc:
        raise RenewalError("invalid_upload", "file_size must be an integer.") from exc
    if size < 1 or size > MAX_UPLOAD_BYTES:
        raise RenewalError("invalid_upload", "file_size is outside the approved upload limit.")
    timestamp = _iso_timestamp(_utc_now(now))
    try:
        initial = _load_request(db, request_id)
        # Reject cross-customer callers before they can lock an alert, a
        # renewal row, or the shared canonical document slot. Re-check the
        # locked row below as a fail-closed defence against unexpected drift.
        _require_client(actor_info, initial["customer_id"])
        _begin_write(db)
        alert = _lock_alert(db, initial["monitoring_alert_id"])
        request = _load_request(db, request_id, lock=True)
        _require_client(actor_info, request["customer_id"])
        _validate_request_linkage(db, request, alert, lock_slot=True)
        existing = _row(
            db.execute(
                "SELECT * FROM monitoring_document_renewal_uploads WHERE request_id = ?",
                (request["request_id"],),
            ).fetchone()
        )
        if existing:
            if (
                existing.get("upload_id") == normalized_upload_id
                and existing.get("file_sha256") == digest
            ):
                attached = _row(
                    db.execute(
                        """
                        SELECT cleanup_id, artifact_state, storage_key
                          FROM monitoring_document_renewal_upload_cleanup
                         WHERE cleanup_id = ? AND upload_id = ?
                        """,
                        (normalized_reservation_id, normalized_upload_id),
                    ).fetchone()
                )
                if (
                    not attached
                    or attached.get("artifact_state") != "attached"
                    or attached.get("storage_key") != key
                ):
                    raise RenewalError(
                        "artifact_reservation_mismatch",
                        "The staged upload reservation does not match the recorded upload.",
                    )
                result = _project(request)
                result["idempotent"] = True
                normalized_existing = _normalize_persisted_row(existing)
                result["uploads"] = [
                    {
                        field: normalized_existing.get(field)
                        for field in (
                            "upload_id",
                            "request_id",
                            "application_id",
                            "customer_id",
                            "original_filename",
                            "file_size",
                            "mime_type",
                            "file_sha256",
                            "uploaded_at",
                            "uploaded_by",
                        )
                    }
                ]
                db.commit()
                return result
            raise RenewalError(
                "upload_already_received",
                "This renewal request already has a staged upload.",
            )
        _require_revision(request, expected_revision)
        if request["request_status"] != "awaiting_upload":
            raise RenewalError(
                "invalid_request_state", "This renewal request is not awaiting an upload."
            )
        artifact_sql = (
            "SELECT * FROM monitoring_document_renewal_upload_cleanup "
            "WHERE cleanup_id = ?"
        )
        if getattr(db, "is_postgres", False):
            artifact_sql += " FOR UPDATE"
        artifact = _row(
            db.execute(artifact_sql, (normalized_reservation_id,)).fetchone()
        )
        if (
            not artifact
            or artifact.get("upload_id") != normalized_upload_id
            or artifact.get("request_id") != request["request_id"]
            or artifact.get("application_id") != request["application_id"]
            or artifact.get("customer_id") != request["customer_id"]
            or artifact.get("storage_key") != key
            or artifact.get("artifact_state") != "stored"
        ):
            raise RenewalError(
                "artifact_reservation_mismatch",
                "The staged upload is not backed by an exact stored-artifact reservation.",
            )
        before = dict(request)
        db.execute(
            """
            INSERT INTO monitoring_document_renewal_uploads
                (upload_id, request_id, application_id, customer_id,
                 original_filename, storage_key, file_size, mime_type,
                 file_sha256, uploaded_at, uploaded_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_upload_id,
                request["request_id"],
                request["application_id"],
                request["customer_id"],
                filename,
                key,
                size,
                mime,
                digest,
                timestamp,
                actor_info["id"],
            ),
        )
        next_revision = int(request["revision"]) + 1
        _require_single_update(
            db.execute(
                """
                UPDATE monitoring_document_renewal_requests
                   SET request_status = 'upload_received', updated_at = ?,
                       updated_by = ?, revision = ?
                 WHERE request_id = ? AND revision = ?
                   AND request_status = 'awaiting_upload'
                """,
                (
                    timestamp,
                    actor_info["id"],
                    next_revision,
                    request["request_id"],
                    request["revision"],
                ),
            )
        )
        request.update(
            {
                "request_status": "upload_received",
                "updated_at": timestamp,
                "updated_by": actor_info["id"],
                "revision": next_revision,
            }
        )
        _event(
            db,
            request,
            event_type="upload_received",
            event_key=f"upload_received:{request['request_id']}:{normalized_upload_id}",
            payload={
                "upload_id": normalized_upload_id,
                "original_filename": filename,
                "file_size": size,
                "mime_type": mime,
                "file_sha256": digest,
                "staged_only": True,
            },
            actor=actor_info,
            timestamp=timestamp,
            correlation_id=correlation_id,
            audit_writer=audit_writer,
            before_state=before,
            due_date_snapshot=request["due_date"],
        )
        _require_single_update(
            db.execute(
                """
                UPDATE monitoring_document_renewal_upload_cleanup
                   SET artifact_state = 'attached', attached_at = ?,
                       updated_at = ?, lease_owner = NULL,
                       lease_expires_at = NULL, next_retry_at = NULL,
                       last_error_code = NULL
                 WHERE cleanup_id = ? AND upload_id = ?
                   AND request_id = ? AND storage_key = ?
                   AND artifact_state = 'stored'
                """,
                (
                    timestamp,
                    timestamp,
                    normalized_reservation_id,
                    normalized_upload_id,
                    request["request_id"],
                    key,
                ),
            )
        )
        if _lock_alert(db, alert["id"])["status"] != alert["status"]:
            raise RenewalError("protected_state_changed", "Monitoring Alert state changed.")
        result = _project(request)
        result["uploads"] = [
            {
                "upload_id": normalized_upload_id,
                "request_id": request["request_id"],
                "application_id": request["application_id"],
                "customer_id": request["customer_id"],
                "original_filename": filename,
                "file_size": size,
                "mime_type": mime,
                "file_sha256": digest,
                "uploaded_at": timestamp,
                "uploaded_by": actor_info["id"],
            }
        ]
        db.commit()
        return result
    except Exception:
        _rollback(db)
        raise


def validate_reminder_intervals(values: Iterable[Any]) -> tuple[int, ...]:
    try:
        normalized = tuple(sorted({int(value) for value in values}, reverse=True))
    except (TypeError, ValueError) as exc:
        raise RenewalError("invalid_reminders", "Reminder intervals must be integers.") from exc
    if not normalized or any(value < 1 or value > 365 for value in normalized):
        raise RenewalError(
            "invalid_reminders", "Reminder intervals must be between 1 and 365 days."
        )
    return normalized


def _scheduler_cursor(db: Any, scheduler_key: str) -> tuple[Optional[str], Optional[str]]:
    row = _row(
        db.execute(
            """
            SELECT cursor_primary, cursor_secondary
              FROM monitoring_document_renewal_scheduler_state
             WHERE scheduler_key = ?
            """,
            (scheduler_key,),
        ).fetchone()
    ) or {}
    return row.get("cursor_primary"), row.get("cursor_secondary")


def _save_scheduler_cursor(
    db: Any,
    scheduler_key: str,
    cursor_primary: Any,
    cursor_secondary: Any,
    timestamp: str,
) -> None:
    db.execute(
        """
        INSERT INTO monitoring_document_renewal_scheduler_state
            (scheduler_key, cursor_primary, cursor_secondary, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(scheduler_key) DO UPDATE SET
            cursor_primary = excluded.cursor_primary,
            cursor_secondary = excluded.cursor_secondary,
            updated_at = excluded.updated_at
        """,
        (
            scheduler_key,
            str(cursor_primary) if cursor_primary not in (None, "") else None,
            str(cursor_secondary) if cursor_secondary not in (None, "") else None,
            timestamp,
        ),
    )


def generate_eligible_renewal_requests(
    db: Any,
    *,
    actor: Mapping[str, Any],
    limit: int = 100,
    now: Optional[datetime] = None,
    feature_flags: Any = None,
    audit_writer: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Create exact, audited requests for eligible document-health alerts.

    This bounded consumer is the only automatic request-creation path.  It
    reads exact alert types, delegates every write to ``create_renewal_request``,
    and reports ambiguous/stale rows without changing alerts or documents.
    """

    _require_feature(feature_flags)
    actor_info = _actor(actor)
    _require_system(actor_info)
    try:
        bounded_limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise RenewalError("invalid_limit", "limit must be an integer.") from exc
    if bounded_limit < 1 or bounded_limit > MAX_REMINDER_LIMIT:
        raise RenewalError(
            "invalid_limit", f"limit must be between 1 and {MAX_REMINDER_LIMIT}."
        )
    alert_types = tuple(sorted(AUTOMATIC_ALERT_REASONS))
    alert_statuses = tuple(sorted(ACTIVE_ALERT_STATUSES))
    type_placeholders = ",".join("?" for _ in alert_types)
    status_placeholders = ",".join("?" for _ in alert_statuses)
    current = _utc_now(now)
    timestamp = _iso_timestamp(current)
    cursor_primary, _ = _scheduler_cursor(db, "eligibility")
    try:
        after_alert_id = int(cursor_primary) if cursor_primary is not None else None
    except (TypeError, ValueError):
        after_alert_id = None

    def fetch_candidates(after_id: Optional[int]) -> List[Dict[str, Any]]:
        cursor_clause = " AND a.id > ?" if after_id is not None else ""
        cursor_params: tuple[Any, ...] = (after_id,) if after_id is not None else ()
        return _rows(
            db.execute(
                f"""
            SELECT id, alert_type
              FROM monitoring_alerts a
             WHERE a.alert_type IN ({type_placeholders})
               AND a.status IN ({status_placeholders})
               AND NOT EXISTS (
                    SELECT 1
                      FROM monitoring_document_renewal_requests r
                     WHERE r.monitoring_alert_id = a.id
               )
               {cursor_clause}
             ORDER BY a.id ASC
             LIMIT ?
                """,
                (*alert_types, *alert_statuses, *cursor_params, bounded_limit),
            )
        )
    candidates = fetch_candidates(after_alert_id)
    if not candidates and after_alert_id is not None:
        candidates = fetch_candidates(None)
    db.commit()
    created_ids: List[str] = []
    already_active_ids: List[str] = []
    blocked: List[Dict[str, str]] = []
    business_block_codes = {
        "alert_not_found",
        "alert_not_active",
        "linkage_not_authoritative",
        "unsupported_alert_type",
        "stale_document_alert",
        "stale_linkage",
        "stale_eligibility",
        "not_eligible",
        "missing_expiry_evidence",
        "legacy_request_conflict",
        "duplicate_active_request",
        "active_alert_request_conflict",
    }
    for candidate in candidates:
        try:
            result = create_renewal_request(
                db,
                candidate["id"],
                reason=AUTOMATIC_ALERT_REASONS[str(candidate["alert_type"])],
                actor=actor_info,
                now=current,
                feature_flags=feature_flags,
                audit_writer=audit_writer,
            )
            target = str(result["request_id"])
            if result.get("idempotent"):
                already_active_ids.append(target)
            else:
                created_ids.append(target)
        except RenewalError as exc:
            if exc.code not in business_block_codes:
                raise
            blocked.append({"alert_id": str(candidate["id"]), "code": exc.code})
    if candidates:
        _save_scheduler_cursor(
            db,
            "eligibility",
            candidates[-1]["id"],
            None,
            timestamp,
        )
        db.commit()
    return {
        "scanned": len(candidates),
        "created": len(created_ids),
        "already_active": len(already_active_ids),
        "blocked": len(blocked),
        "request_ids": created_ids,
        "already_active_request_ids": already_active_ids,
        "blocked_alerts": blocked,
    }


def generate_due_reminders(
    db: Any,
    *,
    actor: Mapping[str, Any],
    intervals: Iterable[Any] = DEFAULT_REMINDER_INTERVALS,
    limit: int = 100,
    now: Optional[datetime] = None,
    correlation_id: Optional[str] = None,
    feature_flags: Any = None,
    audit_writer: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Generate bounded reminder intents using one short transaction per row."""

    _require_feature(feature_flags)
    actor_info = _actor(actor)
    _require_system(actor_info)
    reminder_intervals = validate_reminder_intervals(intervals)
    try:
        bounded_limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise RenewalError("invalid_limit", "limit must be an integer.") from exc
    if bounded_limit < 1 or bounded_limit > MAX_REMINDER_LIMIT:
        raise RenewalError(
            "invalid_limit", f"limit must be between 1 and {MAX_REMINDER_LIMIT}."
        )
    current = _utc_now(now)
    timestamp = _iso_timestamp(current)
    generated_ids: List[str] = []
    blocked: List[Dict[str, str]] = []
    degraded_failures: List[Dict[str, str]] = []
    due_dates = [
        (current.date() + timedelta(days=interval)).isoformat()
        for interval in reminder_intervals
    ]
    placeholders = ",".join("?" for _ in due_dates)
    cursor_due_date, cursor_request_id = _scheduler_cursor(db, "reminders")

    def fetch_candidates(use_cursor: bool) -> List[Dict[str, Any]]:
        cursor_clause = ""
        cursor_params: tuple[Any, ...] = ()
        if use_cursor and cursor_due_date and cursor_request_id:
            cursor_clause = (
                " AND (due_date > ? OR (due_date = ? AND request_id > ?))"
            )
            cursor_params = (
                cursor_due_date,
                cursor_due_date,
                cursor_request_id,
            )
        rows = _rows(
            db.execute(
                f"""
                SELECT request_id, due_date
                  FROM monitoring_document_renewal_requests
                 WHERE request_status = 'awaiting_upload'
                   AND due_date IN ({placeholders})
                   {cursor_clause}
                 ORDER BY due_date ASC, request_id ASC
                 LIMIT ?
                """,
                (*due_dates, *cursor_params, bounded_limit),
            )
        )
        return [_normalize_persisted_row(row) for row in rows]

    candidates = fetch_candidates(True)
    if not candidates and cursor_due_date and cursor_request_id:
        candidates = fetch_candidates(False)
    # End the bounded read before the first canonical audit writer acquires
    # its transaction-scoped serialization lock.
    db.commit()
    deferrable_block_codes = {
        "alert_not_found",
        "alert_not_active",
        "audit_unavailable",
        "linkage_not_authoritative",
        "notification_unavailable",
        "unsupported_alert_type",
        "stale_document_alert",
        "stale_linkage",
        "stale_eligibility",
    }
    for candidate in candidates:
        try:
            _begin_write(db)
            initial = _load_request(db, candidate["request_id"])
            alert = _lock_alert(db, initial["monitoring_alert_id"])
            request = _load_request(db, candidate["request_id"], lock=True)
            _validate_request_linkage(db, request, alert, lock_slot=True)
            # Re-check the state fetched under the row lock.  A portal upload
            # or officer cancellation may have committed after the bounded
            # candidate SELECT but before this request was locked.
            if request.get("request_status") != "awaiting_upload":
                db.commit()
                continue
            # Creation, resend, and due-date changes already publish a portal
            # notification. Do not generate a second reminder on the same UTC
            # day as that authoritative request update.
            if (
                _timestamp_date(request.get("updated_at"), field="updated_at")
                >= current.date()
            ):
                db.commit()
                continue
            days_until_due = (
                _date(request["due_date"], field="due_date") - current.date()
            ).days
            if days_until_due not in reminder_intervals:
                db.commit()
                continue
            event_key = (
                f"reminder:{request['request_id']}:{request['due_date']}:{days_until_due}"
            )
            exists = db.execute(
                "SELECT event_id FROM monitoring_document_renewal_events WHERE event_key = ?",
                (event_key,),
            ).fetchone()
            if exists:
                db.commit()
                continue
            portal_notification_id = _publish_portal_notification(
                db,
                request,
                notification_type="document_renewal_reminder",
                title="Document renewal reminder",
                message=(
                    f"Your renewed {request['document_type']} is due in "
                    f"{days_until_due} day{'s' if days_until_due != 1 else ''}."
                ),
                action="reminder_generated",
            )
            _event(
                db,
                request,
                event_type="reminder_generated",
                event_key=event_key,
                payload={
                    "days_until_due": days_until_due,
                    "due_date": request["due_date"],
                    "portal_notification_id": portal_notification_id,
                    "delivery_channel": "client_portal",
                    "email_delivery_performed": False,
                },
                actor=actor_info,
                timestamp=timestamp,
                correlation_id=correlation_id,
                audit_writer=audit_writer,
                before_state=request,
                due_date_snapshot=request["due_date"],
                reminder_interval_days=days_until_due,
            )
            generated_ids.append(request["request_id"])
            db.commit()
        except RenewalError as exc:
            _rollback(db)
            if exc.code not in deferrable_block_codes:
                raise
            blocked.append(
                {"request_id": str(candidate["request_id"]), "code": exc.code}
            )
            if exc.code in {"audit_unavailable", "notification_unavailable"}:
                degraded_failures.append(
                    {"request_id": str(candidate["request_id"]), "code": exc.code}
                )
        except Exception:
            _rollback(db)
            raise
    if candidates:
        _save_scheduler_cursor(
            db,
            "reminders",
            candidates[-1]["due_date"],
            candidates[-1]["request_id"],
            timestamp,
        )
        db.commit()
    return {
        "scanned": len(candidates),
        "generated": len(generated_ids),
        "blocked": len(blocked),
        "unchanged": len(candidates) - len(generated_ids) - len(blocked),
        "request_ids": generated_ids,
        "blocked_requests": blocked,
        "degraded": bool(degraded_failures),
        "degraded_failures": degraded_failures,
    }


__all__ = [
    "ACTIVE_ALERT_STATUSES",
    "CONTRACT_VERSION",
    "DEFAULT_DUE_DAYS",
    "DEFAULT_REMINDER_INTERVALS",
    "FEATURE_FLAG",
    "ORIGINATING_PR",
    "RenewalError",
    "cancel_renewal_request",
    "count_renewal_requests",
    "create_renewal_request",
    "generate_eligible_renewal_requests",
    "generate_due_reminders",
    "get_renewal_request",
    "list_renewal_requests",
    "record_renewal_upload",
    "renewal_feature_enabled",
    "renewal_request_for_alert",
    "resend_renewal_request",
    "update_renewal_due_date",
    "validate_reminder_intervals",
]
