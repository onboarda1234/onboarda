"""Authoritative, read-only Monitoring Alert linkage contracts.

This module deliberately resolves only the two linkage contracts approved for
the Monitoring pilot: document-expiry signals and screening-hit signals.  It
does not mutate Monitoring or downstream workflow data, infer identities from
names, or choose among ambiguous candidates.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, TypedDict

import document_health_monitor as _document_health
import document_reliance_gate as _document_reliance
import monitoring_routing as _monitoring_routing


CONTRACT_VERSION = "monitoring_alert_linkage_v1"

# Exact, case-sensitive values emitted by the current audited producers.  New
# values require an explicit contract/version review; aliases are not accepted.
DOCUMENT_EXPIRY_ALERT_TYPES = frozenset(
    {
        "document_expired",
        "document_expiring_soon",
        "document_expiry_missing",
    }
)
SCREENING_HIT_ALERT_TYPES = frozenset(
    {
        "sanctions",
        "watchlist",
        "pep",
        "media",
        "adverse_media",
    }
)

_DOCUMENT_REFERENCE_RE = re.compile(r"^document:([A-Za-z0-9_-]{1,200})$")
_READ_ONLY_SQL_PREFIX_RE = re.compile(r"^\s*(?:SELECT|WITH|SHOW)\b", re.IGNORECASE)
_MUTATING_SQL_TOKEN_RE = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|MERGE|ALTER|DROP|CREATE|TRUNCATE|GRANT|"
    r"REVOKE|CALL|DO|COPY|VACUUM|ANALYZE|REFRESH|REINDEX|CLUSTER|COMMENT|"
    r"LOCK|SET)\b",
    re.IGNORECASE,
)
_PARTY_TABLES = {
    "director": ("directors", "full_name"),
    "ubo": ("ubos", "full_name"),
    "intermediary": ("intermediaries", "entity_name"),
}
_ENTITY_KINDS = frozenset({"entity"})
_PERSON_KINDS = frozenset({"director", "ubo", "subject"})


class NavigationContract(TypedDict, total=False):
    target_view: str
    target_tab: str
    target_section: str
    action_mode: str
    application_id: str
    application_ref: str
    customer_id: str
    entity_id: str
    document_id: str
    document_version: int
    person_id: str
    person_type: str
    subject_id: str
    subject_person_key: str
    subject_type: str
    provider: str
    case_identifier: str
    provider_reference: str
    normalized_snapshot_id: str


class LinkageEnvelope(TypedDict, total=False):
    contract_version: str
    alert_id: int
    linkage_type: str
    linkage_status: str
    read_only: bool
    mutation_controls: bool
    application: Dict[str, Any]
    owner: Dict[str, Any]
    navigation: NavigationContract
    document: Dict[str, Any]
    subject: Dict[str, Any]
    screening_case: Dict[str, Any]
    provenance: Dict[str, Any]


class LinkageError(ValueError):
    """Controlled, public-safe linkage failure."""

    def __init__(
        self,
        code: str,
        public_message: str,
        *,
        http_status: int = 409,
        linkage_type: Optional[str] = None,
        owner_module: Optional[str] = None,
        reasons: Optional[List[str]] = None,
    ) -> None:
        self.code = code
        self.public_message = public_message
        self.http_status = http_status
        self.linkage_type = linkage_type
        self.owner_module = owner_module
        self.reasons = list(reasons or [code])
        super().__init__(public_message)

    def payload(self, alert_id: Any = None) -> Dict[str, Any]:
        error = {
            "code": self.code,
            "message": self.public_message,
            "linkage_status": "manual_review_required",
            "reasons": self.reasons,
        }
        if alert_id not in (None, ""):
            error["alert_id"] = alert_id
        if self.linkage_type:
            error["linkage_type"] = self.linkage_type
        if self.owner_module:
            error["owner_module"] = self.owner_module
        return {
            "contract_version": CONTRACT_VERSION,
            "read_only": True,
            "mutation_controls": False,
            "error": error,
        }


def _row_dict(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return None


def _rows(db: Any, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    if (
        not isinstance(sql, str)
        or not _READ_ONLY_SQL_PREFIX_RE.match(sql)
        or ";" in sql
        or _MUTATING_SQL_TOKEN_RE.search(sql)
    ):
        raise ValueError("Canonical linkage query helper permits read-only SQL only.")
    return [dict(row) for row in db.execute(sql, params).fetchall()]


def _load_json_object(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return None
    return dict(decoded) if isinstance(decoded, Mapping) else None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "t", "yes", "y"}


def _date_only(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value).strip().split("T", 1)[0].split(" ", 1)[0] or None


def _timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif value not in (None, ""):
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _alert_context(db: Any, alert_id: Any) -> Dict[str, Any]:
    row = db.execute(
        """
        SELECT ma.*,
               app.id AS linked_application_id,
               app.ref AS application_ref,
               app.client_id AS application_client_id,
               app.company_name AS application_company_name,
               c.id AS linked_client_id
          FROM monitoring_alerts ma
     LEFT JOIN applications app ON app.id = ma.application_id
     LEFT JOIN clients c ON c.id = app.client_id
         WHERE ma.id = ?
        """,
        (alert_id,),
    ).fetchone()
    alert = _row_dict(row)
    if not alert:
        raise LinkageError(
            "monitoring_alert_not_found",
            "Monitoring Alert not found.",
            http_status=404,
        )
    return alert


def _require_application(alert: Mapping[str, Any], linkage_type: str, owner: str) -> None:
    application_id = str(alert.get("application_id") or "").strip()
    if not application_id or str(alert.get("linked_application_id") or "") != application_id:
        raise LinkageError(
            "linkage_missing",
            "Authoritative application linkage is missing.",
            linkage_type=linkage_type,
            owner_module=owner,
            reasons=["missing_application_link"],
        )
    client_id = str(alert.get("application_client_id") or "").strip()
    if not client_id or str(alert.get("linked_client_id") or "") != client_id:
        raise LinkageError(
            "linkage_missing",
            "Authoritative customer linkage is missing.",
            linkage_type=linkage_type,
            owner_module=owner,
            reasons=["missing_customer_link"],
        )


def _application_payload(alert: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": alert.get("application_id"),
        "ref": alert.get("application_ref"),
        "customer_id": alert.get("application_client_id"),
        "entity_id": alert.get("application_id"),
    }


def _document_owner(db: Any, document: Mapping[str, Any]) -> Dict[str, Any]:
    person_id = str(document.get("person_id") or "").strip()
    person_type = str(document.get("person_type") or "").strip()
    application_id = document.get("application_id")
    if not person_id and not person_type:
        return {
            "scope": "entity",
            "person_id": None,
            "person_type": None,
            "identity_strategy": "application_entity_id",
        }
    if not person_id or person_type not in _PARTY_TABLES:
        raise LinkageError(
            "linkage_owner_mismatch",
            "Document ownership cannot be proven from canonical identifiers.",
            linkage_type="document_expiry",
            owner_module="kyc_documents",
            reasons=["invalid_document_owner_identity"],
        )
    table, _name_column = _PARTY_TABLES[person_type]
    matches = _rows(
        db,
        f"SELECT id, person_key FROM {table} WHERE application_id = ? AND id = ?",
        (application_id, person_id),
    )
    if len(matches) != 1:
        raise LinkageError(
            "linkage_owner_mismatch",
            "Document ownership cannot be proven from canonical identifiers.",
            linkage_type="document_expiry",
            owner_module="kyc_documents",
            reasons=[
                "missing_document_owner" if not matches else "ambiguous_document_owner"
            ],
        )
    return {
        "scope": "person",
        "person_id": matches[0]["id"],
        "person_type": person_type,
        "identity_strategy": "exact_party_row_id",
    }


def _document_chain_reaches(
    db: Any,
    source: Mapping[str, Any],
    current: Mapping[str, Any],
) -> bool:
    if str(source.get("id")) == str(current.get("id")):
        return True
    application_id = source.get("application_id")
    previous_version = _canonical_document_version(
        source.get("version"),
        reason="invalid_trigger_document_version",
    )
    seen = {str(source.get("id"))}
    next_id = str(source.get("superseded_by_document_id") or "").strip()
    for _ in range(100):
        if not next_id or next_id in seen:
            return False
        seen.add(next_id)
        row = db.execute(
            """
            SELECT id, application_id, person_id, person_type, doc_type, slot_key,
                   version, superseded_by_document_id
              FROM documents
             WHERE id = ?
            """,
            (next_id,),
        ).fetchone()
        item = _row_dict(row)
        if not item or any(
            str(item.get(key) or "") != str(source.get(key) or "")
            for key in (
                "application_id",
                "person_id",
                "person_type",
                "doc_type",
                "slot_key",
            )
        ):
            return False
        item_version = _canonical_document_version(
            item.get("version"),
            reason="invalid_document_chain_version",
        )
        if item_version <= previous_version:
            return False
        if str(item.get("id")) == str(current.get("id")):
            return True
        previous_version = item_version
        next_id = str(item.get("superseded_by_document_id") or "").strip()
    return False


def _canonical_document_version(
    value: Any,
    *,
    reason: str,
) -> int:
    """Return an exact persisted document version or fail closed.

    A missing version is not version 1: inventing that value would make the
    owner deep link point at an unproven record version.
    """
    if isinstance(value, bool):
        version = 0
    elif isinstance(value, int):
        version = value
    elif isinstance(value, str) and value.isdigit():
        version = int(value)
    else:
        version = 0
    if version < 1:
        raise LinkageError(
            "linkage_integrity_error",
            "The canonical document version is missing or invalid.",
            linkage_type="document_expiry",
            owner_module="kyc_documents",
            reasons=[reason],
        )
    return version


def _document_request(
    db: Any,
    alert_id: Any,
    application_id: Any,
    trigger_document_id: Any,
    current_document_id: Any,
) -> Optional[Dict[str, Any]]:
    rows = _rows(
        db,
        """
        SELECT id, application_id, status, monitoring_document_id,
               linked_document_id
          FROM application_enhanced_requirements
         WHERE monitoring_alert_id = ? AND active = 1
         ORDER BY id DESC
        """,
        (alert_id,),
    )
    if len(rows) > 1:
        raise LinkageError(
            "linkage_ambiguous",
            "The document request linkage is ambiguous.",
            linkage_type="document_expiry",
            owner_module="kyc_documents",
            reasons=["ambiguous_document_request"],
        )
    if not rows:
        return None
    request = rows[0]
    if (
        str(request.get("application_id") or "") != str(application_id or "")
        or str(request.get("monitoring_document_id") or "")
        != str(trigger_document_id or "")
        or str(request.get("linked_document_id") or "")
        != str(current_document_id or "")
    ):
        raise LinkageError(
            "linkage_integrity_error",
            "The document request crosses the canonical document boundary.",
            linkage_type="document_expiry",
            owner_module="kyc_documents",
            reasons=["document_request_identity_mismatch"],
        )
    return request


def _document_reliance_projection(
    db: Any,
    document: Mapping[str, Any],
) -> Dict[str, Any]:
    expectation = {
        "doc_type": document.get("doc_type"),
        "label": "Linked current document",
        "slot_key": document.get("slot_key"),
        "person_id": document.get("person_id"),
        "person_type": document.get("person_type"),
        "source": "monitoring_canonical_linkage",
    }
    try:
        result = _document_reliance._evaluate_document(
            db,
            expectation,
            document,
            require_agent_execution=True,
            stale_days=_document_reliance.DEFAULT_STALE_DAYS,
        )
    except Exception:
        return {
            "allowed": False,
            "reliance_state": "unavailable",
            "verification_method": None,
        }
    snapshot = result.get("snapshot") or {}
    return {
        "allowed": result.get("allowed") is True,
        "reliance_state": str(snapshot.get("reliance_state") or "unavailable"),
        "verification_method": snapshot.get("verification_method"),
    }


def _canonical_document_expiry(document: Mapping[str, Any]) -> Optional[datetime]:
    """Use the KYC & Documents owner module's canonical expiry extraction."""
    try:
        dates = _document_health._extract_document_expiry(document)
        if not isinstance(dates, Mapping):
            raise TypeError("document expiry result is not an object")
        return dates.get("expiry_date") or dates.get("valid_until")
    except Exception as exc:
        raise LinkageError(
            "linkage_broken",
            "The linked document has invalid canonical expiry evidence.",
            linkage_type="document_expiry",
            owner_module="kyc_documents",
            reasons=["invalid_document_expiry_evidence"],
        ) from exc


def _document_owner_state(
    db: Any,
    alert: Mapping[str, Any],
    source: Mapping[str, Any],
    current: Mapping[str, Any],
    request: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    request_status = str((request or {}).get("status") or "").strip()
    source_is_current = str(source.get("id")) == str(current.get("id"))
    verification = str(current.get("verification_status") or "").strip()
    review = str(current.get("review_status") or "").strip()
    reliance = _document_reliance_projection(db, current)
    expiry_at = _canonical_document_expiry(current)
    today = datetime.now(timezone.utc).date()

    def verified_or_expiry_failure() -> str:
        """Never present an expired replacement as successful remediation."""
        if expiry_at is None:
            return "unavailable"
        if expiry_at.date() < today:
            return "expired"
        if (
            str(alert.get("alert_type")) == "document_expiring_soon"
            and expiry_at.date()
            <= today + timedelta(days=_document_health.DOCUMENT_EXPIRING_SOON_DAYS)
        ):
            return "request_not_started"
        return "verified"

    def relied_or_expiry_failure() -> str:
        expiry_state = verified_or_expiry_failure()
        if expiry_state != "verified":
            return expiry_state
        if reliance["reliance_state"] == "manual_accepted":
            return "manual_accepted"
        if reliance["reliance_state"] == "verified":
            return "verified"
        return "unavailable"

    if request and request_status not in {
        "generated",
        "requested",
        "uploaded",
        "under_review",
        "rejected",
        "accepted",
        "waived",
        "cancelled",
    }:
        key = "unavailable"
    elif request_status in {"waived", "cancelled"}:
        # These owner outcomes are intentionally outside the approved minimal
        # projection vocabulary. Do not reinterpret them as verified/resolved.
        key = "unavailable"
    elif request_status == "accepted" and source_is_current:
        key = (
            relied_or_expiry_failure()
            if reliance["allowed"]
            else "officer_review_required"
        )
    elif request_status == "generated" and source_is_current:
        key = "request_not_started"

    elif not source_is_current:
        if reliance["allowed"]:
            key = relied_or_expiry_failure()
        elif verification == "in_progress":
            key = "verifying"
        elif verification == "pending":
            key = "replacement_received"
        elif verification in {"flagged", "failed"} or review in {
            "pending",
            "info_requested",
            "rejected",
        }:
            key = "officer_review_required"
        else:
            key = "unavailable"
    elif request_status in {"requested", "rejected"}:
        key = "awaiting_client"
    elif request_status == "uploaded":
        key = "replacement_received"
    elif request_status == "under_review":
        key = "verifying"
    elif str(alert.get("alert_type")) == "document_expired":
        if expiry_at is None:
            key = "unavailable"
        elif expiry_at.date() < today:
            key = "expired"
        else:
            key = "stale"
    elif str(alert.get("alert_type")) == "document_expiring_soon":
        if expiry_at is None:
            key = "unavailable"
        elif expiry_at.date() < today:
            key = "expired"
        elif expiry_at.date() <= today + timedelta(
            days=_document_health.DOCUMENT_EXPIRING_SOON_DAYS
        ):
            key = "request_not_started"
        else:
            key = "stale"
    elif str(alert.get("alert_type")) == "document_expiry_missing":
        key = "request_not_started" if expiry_at is None else "stale"
    else:
        key = "request_not_started"

    labels = {
        "expired": "Expired",
        "request_not_started": "Request not started",
        "awaiting_client": "Awaiting client",
        "replacement_received": "Replacement received",
        "verifying": "Verifying",
        "officer_review_required": "Officer review required",
        "manual_accepted": "Manually accepted",
        "verified": "Verified",
        "stale": "Stale",
        "unavailable": "Unavailable",
    }
    return {
        "key": key,
        "label": labels[key],
        "source": "kyc_documents",
        "authoritative": True,
        "reliance_policy_version": _document_reliance.POLICY_VERSION,
        "reliance_state": reliance["reliance_state"],
        "verification_method": reliance["verification_method"],
    }


def resolve_document_linkage(db: Any, alert: Mapping[str, Any]) -> LinkageEnvelope:
    _require_application(alert, "document_expiry", "kyc_documents")
    match = _DOCUMENT_REFERENCE_RE.fullmatch(
        str(alert.get("source_reference") or "").strip()
    )
    if not match:
        raise LinkageError(
            "linkage_missing",
            "Canonical document linkage is missing.",
            linkage_type="document_expiry",
            owner_module="kyc_documents",
            reasons=["missing_exact_document_reference"],
        )
    document_id = match.group(1)
    source_row = db.execute(
        "SELECT * FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    source = _row_dict(source_row)
    if not source:
        raise LinkageError(
            "linkage_broken",
            "The linked document record does not exist.",
            linkage_type="document_expiry",
            owner_module="kyc_documents",
            reasons=["missing_document_record"],
        )
    if str(source.get("application_id")) != str(alert.get("application_id")):
        raise LinkageError(
            "linkage_application_mismatch",
            "The linked document does not belong to this application.",
            linkage_type="document_expiry",
            owner_module="kyc_documents",
            reasons=["document_application_mismatch"],
        )
    owner = _document_owner(db, source)
    slot_key = str(source.get("slot_key") or "").strip()
    if not slot_key:
        raise LinkageError(
            "linkage_missing",
            "The document version chain has no canonical slot identifier.",
            linkage_type="document_expiry",
            owner_module="kyc_documents",
            reasons=["missing_document_slot_key"],
        )
    current_rows = _rows(
        db,
        """
        SELECT * FROM documents
         WHERE application_id = ?
           AND slot_key = ?
           AND COALESCE(is_current, TRUE) = TRUE
        """,
        (alert.get("application_id"), slot_key),
    )
    if len(current_rows) != 1:
        raise LinkageError(
            "linkage_ambiguous" if current_rows else "linkage_broken",
            "The current document version cannot be resolved uniquely.",
            linkage_type="document_expiry",
            owner_module="kyc_documents",
            reasons=[
                "ambiguous_current_document" if current_rows else "missing_current_document"
            ],
        )
    current = current_rows[0]
    if (
        current.get("superseded_by_document_id") not in (None, "")
        or current.get("superseded_at") not in (None, "")
    ):
        raise LinkageError(
            "linkage_integrity_error",
            "The selected current document is not the terminal version.",
            linkage_type="document_expiry",
            owner_module="kyc_documents",
            reasons=["document_version_chain_broken"],
        )
    if (
        str(current.get("doc_type") or "") != str(source.get("doc_type") or "")
        or str(current.get("person_id") or "") != str(source.get("person_id") or "")
        or str(current.get("person_type") or "")
        != str(source.get("person_type") or "")
    ):
        raise LinkageError(
            "linkage_integrity_error",
            "The document version chain changes canonical ownership.",
            linkage_type="document_expiry",
            owner_module="kyc_documents",
            reasons=["document_version_owner_drift"],
        )
    source_version = _canonical_document_version(
        source.get("version"),
        reason="invalid_trigger_document_version",
    )
    current_version = _canonical_document_version(
        current.get("version"),
        reason="invalid_current_document_version",
    )
    if not _document_chain_reaches(db, source, current):
        raise LinkageError(
            "linkage_integrity_error",
            "The document version chain is incomplete or stale.",
            linkage_type="document_expiry",
            owner_module="kyc_documents",
            reasons=["document_version_chain_broken"],
        )
    request = _document_request(
        db,
        alert.get("id"),
        alert.get("application_id"),
        source.get("id"),
        current.get("id"),
    )
    state = _document_owner_state(db, alert, source, current, request)
    source_expiry = _canonical_document_expiry(source)
    current_expiry = _canonical_document_expiry(current)
    return {
        "contract_version": CONTRACT_VERSION,
        "alert_id": alert.get("id"),
        "linkage_type": "document_expiry",
        "linkage_status": "linked",
        "read_only": True,
        "mutation_controls": False,
        "application": _application_payload(alert),
        "owner": {
            "module": "kyc_documents",
            "state": state,
            "decision_controls": False,
        },
        "document": {
            "trigger_document_id": source.get("id"),
            "trigger_document_version": source_version,
            "trigger_expiry_date": _date_only(source_expiry),
            "canonical_document_id": current.get("id"),
            "current_document_version": current_version,
            "current_expiry_date": _date_only(current_expiry),
            "document_type": source.get("doc_type"),
            "slot_key": slot_key,
            "person_id": owner.get("person_id"),
            "person_type": owner.get("person_type"),
            "source_is_current": str(source.get("id")) == str(current.get("id")),
        },
        "navigation": {
            "target_view": "application_review",
            "target_tab": "kyc-docs",
            "target_section": "detail-kyc-documents-panel",
            "action_mode": "focus_document",
            "application_id": str(alert.get("application_id")),
            "application_ref": str(alert.get("application_ref") or ""),
            "customer_id": str(alert.get("application_client_id") or ""),
            "entity_id": str(alert.get("application_id")),
            "document_id": str(current.get("id")),
            "document_version": current_version,
            "person_id": str(owner.get("person_id") or ""),
            "person_type": str(owner.get("person_type") or ""),
        },
        "provenance": {
            "link_strategy": "exact_document_source_reference",
            "owner_identity_strategy": owner["identity_strategy"],
            "request_projection_present": bool(request),
        },
    }


def _screening_source(alert: Mapping[str, Any]) -> Dict[str, Any]:
    source = _load_json_object(alert.get("source_reference"))
    if not source:
        raise LinkageError(
            "linkage_missing",
            "Canonical screening linkage is missing.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["missing_structured_screening_reference"],
        )
    provider = str(alert.get("provider") or "").strip()
    case_id = str(alert.get("case_identifier") or "").strip()
    if (
        not provider
        or not case_id
        or str(source.get("provider") or "").strip() != provider
        or str(source.get("case_identifier") or "").strip() != case_id
    ):
        raise LinkageError(
            "linkage_integrity_error",
            "Screening provider and case identifiers do not agree.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["screening_case_identity_mismatch"],
        )
    return source


def _subject_contract(source: Mapping[str, Any]) -> Dict[str, str]:
    nested = source.get("screening_subject")
    nested = dict(nested) if isinstance(nested, Mapping) else {}
    nested_kind = str(nested.get("kind") or "").strip()
    nested_scope = str(nested.get("scope") or "").strip()
    nested_person_key = str(nested.get("person_key") or "").strip()
    top_kind_values = {
        str(source.get(key) or "").strip()
        for key in ("subject_kind", "screening_subject_kind")
        if str(source.get(key) or "").strip()
    }
    top_person_key_values = {
        str(source.get(key) or "").strip()
        for key in (
            "person_key",
            "subject_person_key",
            "screening_subject_person_key",
        )
        if str(source.get(key) or "").strip()
    }
    if len(top_kind_values) > 1 or len(top_person_key_values) > 1:
        raise LinkageError(
            "linkage_integrity_error",
            "The screening source contains conflicting subject identities.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["screening_source_subject_identity_conflict"],
        )
    top_kind = next(iter(top_kind_values), "")
    top_scope = str(source.get("subject_scope") or "").strip()
    top_person_key = next(iter(top_person_key_values), "")
    if (nested_kind and top_kind and nested_kind != top_kind) or (
        nested_scope and top_scope and nested_scope != top_scope
    ) or (
        nested_person_key
        and top_person_key
        and nested_person_key != top_person_key
    ):
        raise LinkageError(
            "linkage_integrity_error",
            "The screening source contains conflicting subject identities.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["screening_source_subject_identity_conflict"],
        )
    kind = nested_kind or top_kind
    scope = nested_scope or top_scope
    person_key = nested_person_key or top_person_key
    if kind in _ENTITY_KINDS and scope == "entity" and not person_key:
        return {"kind": "entity", "scope": "entity", "person_key": ""}
    if kind == "intermediary" and scope == "entity" and person_key:
        return {"kind": kind, "scope": scope, "person_key": person_key}
    if kind in _PERSON_KINDS and scope == "person" and person_key:
        return {"kind": kind, "scope": "person", "person_key": person_key}
    raise LinkageError(
        "linkage_missing",
        "The screening subject lacks a stable canonical identifier.",
        linkage_type="screening_hit",
        owner_module="screening_review",
        reasons=["missing_stable_screening_subject"],
    )


def _party_subject(
    db: Any,
    application_id: str,
    subject: Mapping[str, str],
) -> Dict[str, Any]:
    person_key = subject["person_key"]
    kinds = (
        [subject["kind"]]
        if subject["kind"] in _PARTY_TABLES
        else ["director", "ubo"]
    )
    matches: List[Dict[str, Any]] = []
    for kind in kinds:
        table, name_column = _PARTY_TABLES[kind]
        for row in _rows(
            db,
            f"""
            SELECT id, person_key, {name_column} AS subject_name
              FROM {table}
             WHERE application_id = ? AND person_key = ?
            """,
            (application_id, person_key),
        ):
            row["subject_type"] = kind
            matches.append(row)
    if len(matches) != 1:
        raise LinkageError(
            "linkage_ambiguous" if matches else "linkage_broken",
            "The screening subject cannot be resolved uniquely.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=[
                "ambiguous_screening_subject" if matches else "missing_screening_subject"
            ],
        )
    resolved = matches[0]
    owner_subject_rows = _rows(
        db,
        f"""
        SELECT id, {_PARTY_TABLES[resolved['subject_type']][1]} AS subject_name
          FROM {_PARTY_TABLES[resolved['subject_type']][0]}
         WHERE application_id = ?
        """,
        (application_id,),
    )

    def owner_join_name(value: Any) -> str:
        return " ".join(str(value or "").strip().lower().split())

    def owner_token_name(value: Any) -> str:
        normalized = re.sub(r"[^a-z0-9 ]+", " ", str(value or "").lower())
        return " ".join(sorted(normalized.split()))

    resolved_join = owner_join_name(resolved.get("subject_name"))
    resolved_tokens = owner_token_name(resolved.get("subject_name"))
    if not resolved_join and not resolved_tokens:
        raise LinkageError(
            "linkage_missing",
            "The screening subject lacks a canonical name.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["missing_screening_subject_name"],
        )
    owner_ui_matches = [
        row
        for row in owner_subject_rows
        if (
            resolved_join
            and owner_join_name(row.get("subject_name")) == resolved_join
        )
        or (
            resolved_tokens
            and owner_token_name(row.get("subject_name")) == resolved_tokens
        )
    ]
    if len(owner_ui_matches) != 1:
        raise LinkageError(
            "linkage_ambiguous",
            "Screening Review cannot distinguish colliding subject names.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["screening_review_name_collision"],
        )
    return resolved


def _screening_subject(
    db: Any,
    alert: Mapping[str, Any],
    subject: Mapping[str, str],
) -> Dict[str, Any]:
    if subject["kind"] == "entity" and subject["scope"] == "entity":
        return {
            "id": str(alert.get("application_id")),
            "person_key": "",
            "subject_type": "entity",
            "subject_name": alert.get("application_company_name"),
            "scope": "entity",
        }
    resolved = _party_subject(db, str(alert.get("application_id")), subject)
    return {
        "id": resolved.get("id"),
        "person_key": resolved.get("person_key"),
        "subject_type": resolved.get("subject_type"),
        "subject_name": resolved.get("subject_name"),
        "scope": subject["scope"],
    }


def _screening_evidence(
    db: Any,
    alert: Mapping[str, Any],
) -> Dict[str, Any]:
    rows = _rows(
        db,
        """
        SELECT application_id, provider, case_identifier, alert_identifier,
               match_identifier, risk_identifier, profile_identifier,
               evidence_status, fetched_at, created_at
          FROM monitoring_alert_evidence
         WHERE monitoring_alert_id = ?
         ORDER BY id ASC
        """,
        (alert.get("id"),),
    )
    expected_application = str(alert.get("application_id") or "")
    expected_provider = str(alert.get("provider") or "")
    expected_case = str(alert.get("case_identifier") or "")
    for row in rows:
        row_application = str(row.get("application_id") or "")
        if (
            row_application != expected_application
            or str(row.get("provider") or "") != expected_provider
            or str(row.get("case_identifier") or "") != expected_case
        ):
            raise LinkageError(
                "linkage_integrity_error",
                "Stored screening evidence crosses the canonical case boundary.",
                linkage_type="screening_hit",
                owner_module="screening_review",
                reasons=["screening_evidence_identity_mismatch"],
            )

    def identifiers(key: str) -> List[str]:
        return sorted(
            {
                str(row.get(key))
                for row in rows
                if row.get(key) not in (None, "")
            }
        )

    statuses = sorted(
        {str(row.get("evidence_status")) for row in rows if row.get("evidence_status")}
    )
    parsed_evidence_times = [
        _timestamp(row.get("fetched_at") or row.get("created_at"))
        for row in rows
    ]
    timestamps_complete = bool(rows) and all(
        value is not None for value in parsed_evidence_times
    )
    evidence_times = [
        value for value in parsed_evidence_times if value is not None
    ]
    return {
        "evidence_count": len(rows),
        "evidence_available": bool(rows)
        and timestamps_complete
        and all(str(row.get("evidence_status") or "") == "fetched" for row in rows),
        "evidence_timestamps_complete": timestamps_complete,
        "alert_identifiers": identifiers("alert_identifier"),
        "match_identifiers": identifiers("match_identifier"),
        "risk_identifiers": identifiers("risk_identifier"),
        "profile_identifiers": identifiers("profile_identifier"),
        "evidence_statuses": statuses,
        "latest_evidence_at": (
            max(evidence_times).isoformat() if evidence_times else None
        ),
    }


def _screening_review(
    db: Any,
    application_id: str,
    subject: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    rows = _rows(
        db,
        """
        SELECT id, subject_type, subject_name, disposition, disposition_code,
               rationale, notes, requires_four_eyes, reviewer_id, reviewer_name,
               second_reviewer_id, second_disposition_code, second_rationale,
               second_reviewed_at, created_at, updated_at
          FROM screening_reviews
         WHERE application_id = ? AND subject_type = ? AND subject_name = ?
        """,
        (
            application_id,
            subject.get("subject_type"),
            subject.get("subject_name"),
        ),
    )
    if len(rows) > 1:
        raise LinkageError(
            "linkage_ambiguous",
            "Screening Review state is ambiguous for the canonical subject.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["ambiguous_screening_review"],
        )
    return rows[0] if rows else None


def _screening_review_audit_evidence(
    db: Any,
    alert: Mapping[str, Any],
    review: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    disposition_code = str(review.get("disposition_code") or "").strip()
    disposition = str(review.get("disposition") or "").strip()
    subject_name = str(review.get("subject_name") or "")
    subject_type = str(review.get("subject_type") or "").strip()
    first_reviewer_id = str(review.get("reviewer_id") or "").strip()
    second_reviewer_id = str(review.get("second_reviewer_id") or "").strip()
    requires_four_eyes = _truthy(review.get("requires_four_eyes"))
    if (
        not disposition_code
        or not subject_name
        or not subject_type
        or not first_reviewer_id
    ):
        return None
    reviewer_ids = [first_reviewer_id]
    if second_reviewer_id and second_reviewer_id not in reviewer_ids:
        reviewer_ids.append(second_reviewer_id)
    placeholders = ",".join("?" for _ in reviewer_ids)
    try:
        reviewer_rows = _rows(
            db,
            f"SELECT id, role, status FROM users WHERE id IN ({placeholders})",
            tuple(reviewer_ids),
        )
    except Exception:
        return None
    reviewer_roles = {
        str(row.get("id")): str(row.get("role") or "").strip().lower()
        for row in reviewer_rows
        if str(row.get("status") or "").strip().lower() == "active"
    }
    first_reviewer_roles = (
        {"admin", "sco", "co"}
        if disposition == "cleared" or disposition_code == "false_positive_cleared"
        else {"admin", "sco", "co", "analyst"}
    )
    if reviewer_roles.get(first_reviewer_id) not in first_reviewer_roles:
        return None

    expected_audits: List[Dict[str, Any]]
    if requires_four_eyes and second_reviewer_id:
        first_reviewed_at = _timestamp(review.get("created_at"))
        current_review_updated_at = _timestamp(review.get("updated_at"))
        second_reviewed_at = _timestamp(review.get("second_reviewed_at"))
        first_rationale = str(review.get("rationale") or "").strip()
        second_rationale = str(review.get("second_rationale") or "").strip()
        if (
            second_reviewer_id == first_reviewer_id
            or reviewer_roles.get(second_reviewer_id) not in {"admin", "sco"}
            or str(review.get("second_disposition_code") or "").strip()
            != disposition_code
            or not first_rationale
            or not second_rationale
            or first_reviewed_at is None
            or current_review_updated_at is None
            or second_reviewed_at is None
            or second_reviewed_at < first_reviewed_at.replace(microsecond=0)
            or current_review_updated_at.replace(microsecond=0)
            > second_reviewed_at
        ):
            return None
        expected_audits = [
            {
                "actor_id": first_reviewer_id,
                "actor_role": reviewer_roles[first_reviewer_id],
                # The protected Screening Review audit contract intentionally
                # stores the first 1,000 characters while the owner row retains
                # the complete rationale. Match that exact persisted contract;
                # do not treat a valid long rationale as unaudited.
                "rationale": first_rationale[:1000],
                "four_eyes_status": "second_review_required",
                "not_before": first_reviewed_at,
                "not_after": second_reviewed_at,
            },
            {
                "actor_id": second_reviewer_id,
                "actor_role": reviewer_roles[second_reviewer_id],
                "rationale": second_rationale[:1000],
                "four_eyes_status": "second_review_complete",
                "not_before": second_reviewed_at,
                "not_after": None,
            },
        ]
    elif requires_four_eyes:
        expected_audits = [
            {
                "actor_id": first_reviewer_id,
                "actor_role": reviewer_roles[first_reviewer_id],
                "rationale": str(review.get("rationale") or "").strip()[:1000],
                "four_eyes_status": "second_review_required",
                "not_before": _timestamp(
                    review.get("created_at") or review.get("updated_at")
                ),
                "not_after": None,
            }
        ]
    else:
        if second_reviewer_id:
            return None
        expected_audits = [
            {
                "actor_id": first_reviewer_id,
                "actor_role": reviewer_roles[first_reviewer_id],
                "rationale": str(review.get("rationale") or "").strip()[:1000],
                "four_eyes_status": "complete",
                "not_before": _timestamp(
                    review.get("updated_at") or review.get("created_at")
                ),
                "not_after": None,
            }
        ]
    if any(
        not expectation["rationale"] or expectation["not_before"] is None
        for expectation in expected_audits
    ):
        return None

    targets = []
    for candidate in (alert.get("application_ref"), alert.get("application_id")):
        value = str(candidate or "").strip()
        if value and value not in targets:
            targets.append(value)
    if not targets:
        return None
    placeholders = ",".join("?" for _ in targets)
    rows = _rows(
        db,
        f"""
        SELECT id, timestamp, detail
          FROM audit_log
         WHERE action = 'Screening Review'
           AND target IN ({placeholders})
         ORDER BY id DESC
        """,
        tuple(targets),
    )
    matched_audits: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        detail = _load_json_object(row.get("detail"))
        if not detail:
            continue
        payload_subject = str(detail.get("subject_name") or "")
        payload_code = str(
            detail.get("disposition_code")
            or detail.get("canonical_disposition")
            or ""
        ).strip()
        payload_case = str(
            detail.get("case_identifier")
            or detail.get("provider_case_identifier")
            or ""
        ).strip()
        payload_provider = str(detail.get("provider") or "").strip()
        payload_application = str(detail.get("application_id") or "").strip()
        payload_subject_type = str(detail.get("subject_type") or "").strip()
        payload_actor_id = str(detail.get("actor") or "").strip()
        payload_actor_role = str(detail.get("actor_role") or "").strip().lower()
        payload_rationale = str(detail.get("rationale") or "").strip()
        payload_four_eyes_status = str(
            detail.get("four_eyes_status") or ""
        ).strip()
        provider_references = detail.get("provider_references")
        provider_references = (
            dict(provider_references)
            if isinstance(provider_references, Mapping)
            else {}
        )

        def exact_reference_list(key: str) -> List[str]:
            values = provider_references.get(key)
            if not isinstance(values, list):
                return []
            return [str(value) for value in values if value not in (None, "")]

        audit_case_ids = {
            str(value)
            for value in exact_reference_list("case_ids")
        }
        for key in ("case_id", "case_identifier"):
            if provider_references.get(key) not in (None, ""):
                audit_case_ids.add(str(provider_references[key]))
        if payload_case:
            audit_case_ids.add(payload_case)
        audit_alert_ids = {
            str(value)
            for value in exact_reference_list("alert_ids")
        }
        for key in ("alert_id", "alert_identifier"):
            if provider_references.get(key) not in (None, ""):
                audit_alert_ids.add(str(provider_references[key]))
        expected_case = str(alert.get("case_identifier") or "").strip()
        source = _load_json_object(alert.get("source_reference")) or {}
        expected_alert = str(source.get("alert_identifier") or "").strip()
        audit_at = _timestamp(row.get("timestamp"))
        event_at = _timestamp(detail.get("timestamp"))
        common_match = (
            payload_subject == subject_name
            and payload_subject_type == subject_type
            and payload_code == disposition_code
            and expected_case in audit_case_ids
            and (not expected_alert or expected_alert in audit_alert_ids)
            and payload_provider == str(alert.get("provider") or "").strip()
            and payload_application == str(alert.get("application_id") or "")
            and _truthy(detail.get("requires_four_eyes")) == requires_four_eyes
            and audit_at is not None
            and event_at is not None
            # PostgreSQL CURRENT_TIMESTAMP is the transaction-start time, so
            # the audit row timestamp may legitimately predate the Python event
            # timestamp recorded in the typed detail. The typed timestamp is
            # the review event clock used for chronology below.
            and audit_at.replace(microsecond=0) <= event_at
        )
        if not common_match:
            continue
        for index, expectation in enumerate(expected_audits):
            not_after = expectation["not_after"]
            if (
                payload_actor_id == expectation["actor_id"]
                and payload_actor_role == expectation["actor_role"]
                and payload_rationale == expectation["rationale"]
                and payload_four_eyes_status == expectation["four_eyes_status"]
                and event_at >= expectation["not_before"].replace(microsecond=0)
                and (
                    not_after is None
                    or event_at <= not_after.replace(microsecond=0)
                )
            ):
                if index not in matched_audits:
                    matched_audits[index] = {
                        "audit_id": row.get("id"),
                        "event_at": event_at,
                    }
    if len(matched_audits) != len(expected_audits):
        return None
    final = matched_audits[len(expected_audits) - 1]
    return {
        "confirmed": True,
        "audit_ids": [
            matched_audits[index]["audit_id"]
            for index in range(len(expected_audits))
        ],
        "final_audit_id": final["audit_id"],
        "final_event_at": final["event_at"],
    }


def _screening_hit_mutation_candidates(
    db: Any,
    alert: Mapping[str, Any],
    review: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    targets = []
    for candidate in (alert.get("application_ref"), alert.get("application_id")):
        value = str(candidate or "").strip()
        if value and value not in targets:
            targets.append(value)
    if not targets:
        return []
    placeholders = ",".join("?" for _ in targets)
    rows = _rows(
        db,
        f"""
        SELECT id, timestamp, detail
          FROM audit_log
         WHERE action = 'Screening Hit Disposition'
           AND target IN ({placeholders})
         ORDER BY id DESC
        """,
        tuple(targets),
    )
    candidates: List[Dict[str, Any]] = []
    for row in rows:
        detail = _load_json_object(row.get("detail"))
        if not detail:
            candidates.append(
                {
                    "audit_id": row.get("id"),
                    "event_at": None,
                    "identity_unproven": True,
                }
            )
            continue
        payload_identity = {
            "application_id": str(detail.get("application_id") or ""),
            "subject_type": str(detail.get("subject_type") or "").strip(),
            "subject_name": str(detail.get("subject_name") or ""),
        }
        expected_identity = {
            "application_id": str(alert.get("application_id") or ""),
            "subject_type": str(review.get("subject_type") or "").strip(),
            "subject_name": str(review.get("subject_name") or ""),
        }
        if not all(payload_identity.values()):
            candidates.append(
                {
                    "audit_id": row.get("id"),
                    "event_at": None,
                    "identity_unproven": True,
                }
            )
            continue
        if payload_identity != expected_identity:
            continue
        event_at = _timestamp(detail.get("timestamp"))
        audit_at = _timestamp(row.get("timestamp"))
        if (
            event_at is None
            or audit_at is None
            or audit_at.replace(microsecond=0) > event_at
        ):
            # This is the newest exact-subject mutation record. Once identity
            # matches, malformed or impossible chronology is itself material:
            # a deleted/undone per-hit row may leave only this audit evidence.
            candidates.append(
                {
                    "audit_id": row.get("id"),
                    "event_at": None,
                    "identity_unproven": False,
                }
            )
            continue
        candidates.append(
            {
                "audit_id": row.get("id"),
                "event_at": event_at,
                "identity_unproven": False,
            }
        )
    return candidates


def _screening_hit_state_changed_after_review(
    db: Any,
    alert: Mapping[str, Any],
    review: Mapping[str, Any],
    review_audit: Optional[Mapping[str, Any]],
    *,
    require_all_cleared: bool,
) -> bool:
    if not review_audit:
        return False
    mutations = _screening_hit_mutation_candidates(db, alert, review)
    final_event_at = review_audit.get("final_event_at")
    if not isinstance(final_event_at, datetime):
        return True
    for mutation in mutations:
        # Without typed subject identity or chronology, sequence allocation
        # cannot prove this same-target owner mutation committed before the
        # review. Preserve no-clear confidence and fail closed.
        if mutation.get("identity_unproven"):
            return True
        try:
            if int(mutation["audit_id"]) > int(review_audit["final_audit_id"]):
                return True
        except (KeyError, TypeError, ValueError):
            return True
        latest_event_at = mutation.get("event_at")
        if not isinstance(latest_event_at, datetime):
            return True
        # Sequence allocation and transaction timestamps do not prove commit
        # order.  The typed event chronology catches an overlapping mutation
        # whose audit id was allocated before the subject review completed.
        if latest_event_at >= final_event_at:
            return True
    rows = _rows(
        db,
        """
        SELECT disposition, created_at, updated_at
          FROM screening_hit_dispositions
         WHERE application_id = ?
           AND subject_type = ?
           AND subject_name = ?
        """,
        (
            alert.get("application_id"),
            review.get("subject_type"),
            review.get("subject_name"),
        ),
    )
    for row in rows:
        # Screening Review owns the decision. A subject-level clear cannot be
        # projected while any persisted per-hit decision remains open, matched,
        # escalated, unknown, or otherwise non-cleared.
        if require_all_cleared and str(row.get("disposition") or "") != "cleared":
            return True
        row_at = _timestamp(row.get("updated_at") or row.get("created_at"))
        # A persisted per-hit decision without provable chronology must never
        # coexist with a projected clear state. Legacy columns are nullable,
        # so fail closed instead of treating an unknown timestamp as old.
        if row_at is None:
            return True
        if row_at.replace(microsecond=0) >= final_event_at:
            return True
    return False


def _screening_owner_state(
    db: Any,
    alert: Mapping[str, Any],
    review: Optional[Mapping[str, Any]],
    evidence: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> Dict[str, Any]:
    evidence_unavailable = bool(evidence.get("evidence_count")) and not bool(
        evidence.get("evidence_available")
    )
    current_snapshot_unavailable = bool(
        snapshot.get("current_snapshot_id")
        and (
            snapshot.get("current_snapshot_status") != "success"
            or not snapshot.get("current_snapshot_at")
        )
    )
    if not review:
        exact_case_present = bool(
            evidence.get("evidence_count") or snapshot.get("linked_snapshot_id")
        )
        key = (
            "unavailable"
            if evidence_unavailable or current_snapshot_unavailable
            else "review_required"
            if exact_case_present
            else "unavailable"
        )
        return {
            "key": key,
            "label": "Review required" if key == "review_required" else "Unavailable",
            "source": "screening_review",
            "authoritative": True,
            "four_eyes_status": "not_recorded",
        }
    requires_four_eyes = _truthy(review.get("requires_four_eyes"))
    second_reviewer_id = str(review.get("second_reviewer_id") or "").strip()
    second_complete = bool(
        second_reviewer_id
        and str(review.get("second_disposition_code") or "").strip()
    )
    disposition = str(review.get("disposition") or "").strip()
    disposition_code = str(review.get("disposition_code") or "").strip()
    review_audit = _screening_review_audit_evidence(db, alert, review)
    audit_confirmed = bool(review_audit)
    per_hit_state_changed = _screening_hit_state_changed_after_review(
        db,
        alert,
        review,
        review_audit,
        require_all_cleared=(
            disposition == "cleared"
            and disposition_code == "false_positive_cleared"
        ),
    )
    review_at = _timestamp(review.get("updated_at") or review.get("created_at"))
    authoritative_review_at = (
        review_audit.get("final_event_at") if review_audit else review_at
    )
    evidence_at = _timestamp(evidence.get("latest_evidence_at"))
    snapshot_at = _timestamp(snapshot.get("current_snapshot_at"))
    newest_evidence_at = max(
        [value for value in (evidence_at, snapshot_at) if value is not None],
        default=None,
    )
    clearance_complete = bool(
        disposition == "cleared"
        and disposition_code == "false_positive_cleared"
        and str(review.get("reviewer_id") or review.get("reviewer_name") or "").strip()
        and str(review.get("rationale") or "").strip()
        and (review.get("created_at") or review.get("updated_at"))
        and audit_confirmed
        and (
            not requires_four_eyes
            or (
                second_complete
                and str(review.get("second_disposition_code") or "").strip()
                == "false_positive_cleared"
            )
        )
    )
    if evidence_unavailable or current_snapshot_unavailable or not audit_confirmed:
        key = "unavailable"
    elif per_hit_state_changed:
        key = "stale"
    elif newest_evidence_at and (
        not isinstance(authoritative_review_at, datetime)
        or newest_evidence_at.replace(microsecond=0)
        >= authoritative_review_at.replace(microsecond=0)
    ):
        key = "stale"
    elif requires_four_eyes and not second_complete:
        key = "pending_second_review"
    elif disposition == "escalated" and disposition_code:
        key = "escalated"
    elif clearance_complete:
        key = "resolved_in_screening_review"
    elif disposition == "follow_up_required" and disposition_code:
        key = "review_required"
    else:
        key = "unavailable"
    labels = {
        "pending_second_review": "Pending second review",
        "escalated": "Escalated",
        "resolved_in_screening_review": "Resolved in Screening Review",
        "review_required": "Review required",
        "unavailable": "Unavailable",
        "stale": "Stale",
    }
    if requires_four_eyes:
        if audit_confirmed and second_complete:
            four_eyes_status = "complete"
        elif second_reviewer_id:
            four_eyes_status = "unavailable"
        else:
            four_eyes_status = "pending_second_review"
    else:
        four_eyes_status = "not_required"
    return {
        "key": key,
        "label": labels[key],
        "source": "screening_review",
        "authoritative": True,
        "review_id": review.get("id"),
        "four_eyes_status": four_eyes_status,
        "audit_confirmed": audit_confirmed,
    }


def _snapshot_report(row: Mapping[str, Any]) -> Dict[str, Any]:
    report = _load_json_object(row.get("normalized_report_json"))
    if report is None:
        if str(row.get("normalization_status") or "") == "success":
            raise LinkageError(
                "linkage_broken",
                "The successful screening snapshot payload is invalid.",
                linkage_type="screening_hit",
                owner_module="screening_review",
                reasons=["screening_snapshot_payload_invalid"],
            )
        return {}
    return report


def _snapshot_subject(row: Mapping[str, Any]) -> Dict[str, str]:
    report = _snapshot_report(row)
    providers = report.get("provider_specific")
    providers = dict(providers) if isinstance(providers, Mapping) else {}
    provider = providers.get("complyadvantage")
    provider = dict(provider) if isinstance(provider, Mapping) else {}
    nested = provider.get("screening_subject")
    nested = dict(nested) if isinstance(nested, Mapping) else {}
    nested_values = {
        "kind": str(nested.get("kind") or "").strip(),
        "scope": str(nested.get("scope") or "").strip(),
        "person_key": str(nested.get("person_key") or "").strip(),
    }
    top_values = {
        "kind": str(report.get("screening_subject_kind") or "").strip(),
        "scope": str(report.get("subject_scope") or "").strip(),
        "person_key": str(
            report.get("screening_subject_person_key") or ""
        ).strip(),
    }
    if any(
        nested_values[key]
        and top_values[key]
        and nested_values[key] != top_values[key]
        for key in nested_values
    ):
        raise LinkageError(
            "linkage_integrity_error",
            "The screening snapshot contains conflicting subject identities.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["screening_snapshot_subject_identity_conflict"],
        )
    return {
        key: nested_values[key]
        or top_values[key]
        for key in ("kind", "scope", "person_key")
    }


def _json_string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        values = value
    elif isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return []
        values = decoded if isinstance(decoded, list) else []
    else:
        values = []
    return sorted({str(item) for item in values if item not in (None, "")})


def _snapshot_provider_references(row: Mapping[str, Any]) -> Dict[str, List[str]]:
    report = _snapshot_report(row)
    providers = report.get("provider_specific")
    providers = dict(providers) if isinstance(providers, Mapping) else {}
    provider = providers.get("complyadvantage")
    provider = dict(provider) if isinstance(provider, Mapping) else {}
    references = provider.get("provider_references")
    references = dict(references) if isinstance(references, Mapping) else {}
    return {
        "case_ids": _json_string_list(references.get("case_ids")),
        "alert_ids": _json_string_list(references.get("alert_ids")),
    }


def _screening_snapshot_projection(
    db: Any,
    alert: Mapping[str, Any],
    source: Mapping[str, Any],
    subject_contract: Mapping[str, str],
) -> Dict[str, Any]:
    normalized_snapshot_id = source.get("normalized_record_id")
    has_linked_snapshot_id = normalized_snapshot_id not in (None, "")
    cache = getattr(db, "_monitoring_linkage_snapshot_cache", None)
    cache_key = (
        str(alert.get("application_id") or ""),
        str(alert.get("application_client_id") or ""),
        str(alert.get("provider") or ""),
    )
    rows = cache.get(cache_key) if isinstance(cache, dict) else None
    if rows is None:
        snapshot_sql = """
            SELECT id, normalization_status, created_at, updated_at,
                   normalized_report_json
              FROM screening_reports_normalized
             WHERE application_id = ? AND client_id = ? AND provider = ?
             ORDER BY id DESC
        """
        rows = _rows(
            db,
            snapshot_sql,
            (
                alert.get("application_id"),
                alert.get("application_client_id"),
                alert.get("provider"),
            ),
        )
        if isinstance(cache, dict):
            cache[cache_key] = rows
    linked = (
        next(
            (
                row
                for row in rows
                if str(row.get("id")) == str(normalized_snapshot_id)
            ),
            None,
        )
        if has_linked_snapshot_id
        else None
    )
    if has_linked_snapshot_id and not linked:
        raise LinkageError(
            "linkage_broken",
            "The linked screening evidence snapshot does not exist.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["missing_screening_evidence_snapshot"],
        )
    if linked and str(linked.get("normalization_status") or "") != "success":
        raise LinkageError(
            "linkage_broken",
            "The linked screening snapshot is unavailable for authoritative review.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["screening_snapshot_not_successful"],
        )
    linked_subject = _snapshot_subject(linked) if linked else None
    if linked and linked_subject != dict(subject_contract):
        raise LinkageError(
            "linkage_integrity_error",
            "The linked screening snapshot belongs to a different subject.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["screening_snapshot_subject_mismatch"],
        )
    provider_references = _snapshot_provider_references(linked or {})
    expected_case = str(alert.get("case_identifier") or "").strip()
    expected_alert = str(source.get("alert_identifier") or "").strip()
    if linked and (
        expected_case not in provider_references["case_ids"]
        or (
            expected_alert
            and expected_alert not in provider_references["alert_ids"]
        )
    ):
        raise LinkageError(
            "linkage_integrity_error",
            "The linked screening snapshot does not prove the exact provider case.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["screening_snapshot_case_mismatch"],
        )
    subject_rows = []
    for row in rows:
        if _snapshot_subject(row) == dict(subject_contract):
            subject_rows.append(row)
    current = subject_rows[0] if subject_rows else None
    current_scope = "exact_subject"
    latest_app_provider = rows[0] if rows else None
    latest_subject = _snapshot_subject(latest_app_provider or {})
    if (
        latest_app_provider
        and str(latest_app_provider.get("normalization_status") or "") != "success"
        and not any(latest_subject.values())
    ):
        current = latest_app_provider
        current_scope = "application_provider_unattributed"
    if not current and has_linked_snapshot_id:
        raise LinkageError(
            "linkage_broken",
            "No current screening snapshot exists for the canonical subject.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["missing_current_screening_snapshot"],
        )
    if not current:
        return {
            "linked_snapshot_id": None,
            "current_snapshot_id": None,
            "snapshot_role": "unavailable",
            "normalization_status": "unavailable",
            "current_snapshot_status": "unavailable",
            "current_snapshot_at": None,
            "current_snapshot_scope": "unavailable",
        }
    return {
        "linked_snapshot_id": linked.get("id") if linked else None,
        "current_snapshot_id": current.get("id"),
        "snapshot_role": (
            "current"
            if linked and str(current.get("id")) == str(linked.get("id"))
            else "historical"
            if linked
            else "current_unlinked"
        ),
        "normalization_status": "success" if linked else "unavailable",
        "current_snapshot_status": str(
            current.get("normalization_status") or "unavailable"
        ),
        "current_snapshot_at": (
            _timestamp(current.get("updated_at") or current.get("created_at")).isoformat()
            if _timestamp(current.get("updated_at") or current.get("created_at"))
            else None
        ),
        "current_snapshot_scope": current_scope,
    }


def resolve_screening_linkage(db: Any, alert: Mapping[str, Any]) -> LinkageEnvelope:
    _require_application(alert, "screening_hit", "screening_review")
    source = _screening_source(alert)
    subject_contract = _subject_contract(source)
    subject = _screening_subject(db, alert, subject_contract)
    evidence = _screening_evidence(db, alert)
    review = _screening_review(db, str(alert.get("application_id")), subject)
    provider_reference = str(source.get("alert_identifier") or "").strip() or None
    if (
        provider_reference
        and evidence.get("evidence_count")
        and provider_reference not in evidence.get("alert_identifiers", [])
    ):
        raise LinkageError(
            "linkage_integrity_error",
            "The screening provider reference does not match the stored case evidence.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["screening_provider_reference_mismatch"],
        )
    snapshot = _screening_snapshot_projection(db, alert, source, subject_contract)
    if not evidence.get("evidence_count") and not snapshot.get("linked_snapshot_id"):
        raise LinkageError(
            "linkage_broken",
            "No exact local record proves the screening case linkage.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["missing_screening_case_record"],
        )
    state = _screening_owner_state(db, alert, review, evidence, snapshot)
    return {
        "contract_version": CONTRACT_VERSION,
        "alert_id": alert.get("id"),
        "linkage_type": "screening_hit",
        "linkage_status": "linked",
        "read_only": True,
        "mutation_controls": False,
        "application": _application_payload(alert),
        "owner": {
            "module": "screening_review",
            "state": state,
            "decision_controls": False,
        },
        "subject": {
            "id": subject.get("id"),
            "person_key": subject.get("person_key") or None,
            "type": subject.get("subject_type"),
            "scope": subject.get("scope"),
        },
        "screening_case": {
            "provider": alert.get("provider"),
            "case_identifier": alert.get("case_identifier"),
            "provider_reference": provider_reference,
            "review_id": (review or {}).get("id"),
            **snapshot,
            **evidence,
        },
        "navigation": {
            "target_view": "application_review",
            "target_tab": "screening",
            "target_section": "detail-screening-review",
            "action_mode": "focus_screening_subject",
            "application_id": str(alert.get("application_id")),
            "application_ref": str(alert.get("application_ref") or ""),
            "customer_id": str(alert.get("application_client_id") or ""),
            "entity_id": str(alert.get("application_id")),
            "subject_id": str(subject.get("id") or ""),
            "subject_person_key": str(subject.get("person_key") or ""),
            "subject_type": str(subject.get("subject_type") or ""),
            "provider": str(alert.get("provider") or ""),
            "case_identifier": str(alert.get("case_identifier") or ""),
            "provider_reference": str(provider_reference or ""),
            "normalized_snapshot_id": str(snapshot.get("linked_snapshot_id") or ""),
        },
        "provenance": {
            "link_strategy": "exact_provider_case_and_stable_subject",
            "review_projection_present": bool(review),
            "normalized_snapshot_is_authoritative": False,
        },
    }


def _canonical_screening_subject_identity(
    db: Any,
    alert: Mapping[str, Any],
    subject: Mapping[str, str],
) -> Optional[Dict[str, str]]:
    """Resolve only the stable owner identity needed for duplicate detection."""
    application_id = str(alert.get("application_id") or "").strip()
    if subject["kind"] == "entity" and subject["scope"] == "entity":
        return {
            "subject_id": application_id,
            "subject_type": "entity",
            "subject_person_key": "",
            "subject_scope": "entity",
        }
    kinds = (
        [subject["kind"]]
        if subject["kind"] in _PARTY_TABLES
        else ["director", "ubo"]
    )
    matches: List[Dict[str, Any]] = []
    for kind in kinds:
        table, _name_column = _PARTY_TABLES[kind]
        for row in _rows(
            db,
            f"SELECT id, person_key FROM {table} "
            "WHERE application_id = ? AND person_key = ?",
            (application_id, subject["person_key"]),
        ):
            matches.append({**row, "subject_type": kind})
    if len(matches) != 1:
        return None
    match = matches[0]
    return {
        "subject_id": str(match.get("id") or ""),
        "subject_type": str(match.get("subject_type") or ""),
        "subject_person_key": str(match.get("person_key") or ""),
        "subject_scope": subject["scope"],
    }


def _monitoring_linkage_identity(db: Any, alert: Mapping[str, Any]) -> str:
    """Build the exact producer identity shared by runtime and audit planning."""
    alert_type = str(alert.get("alert_type") or "")
    if alert_type in DOCUMENT_EXPIRY_ALERT_TYPES:
        match = _DOCUMENT_REFERENCE_RE.fullmatch(
            str(alert.get("source_reference") or "").strip()
        )
        application_id = str(alert.get("application_id") or "").strip()
        return (
            f"document:{application_id}:{alert_type}:{match.group(1)}"
            if application_id and match
            else ""
        )
    if alert_type in SCREENING_HIT_ALERT_TYPES:
        try:
            source = _screening_source(alert)
            subject = _subject_contract(source)
        except LinkageError:
            return ""
        application_id = str(alert.get("application_id") or "").strip()
        provider = str(alert.get("provider") or "").strip()
        case_identifier = str(alert.get("case_identifier") or "").strip()
        if not all((application_id, provider, case_identifier)):
            return ""
        canonical_subject = _canonical_screening_subject_identity(
            db,
            alert,
            subject,
        )
        if not canonical_subject:
            return ""
        return "screening:" + json.dumps(
            {
                "application_id": application_id,
                "case_identifier": case_identifier,
                "provider": provider,
                **canonical_subject,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    return ""


def _duplicate_monitoring_alert_ids(
    db: Any,
    alert: Mapping[str, Any],
) -> List[Any]:
    """Return unresolved alerts with the same exact producer identity."""
    if not _monitoring_routing.is_alert_unresolved(alert):
        return []
    target_identity = _monitoring_linkage_identity(db, alert)
    if not target_identity:
        return []
    candidates = _rows(
        db,
        """
        SELECT id, application_id, alert_type, source_reference, provider,
               case_identifier, status, resolved_at
          FROM monitoring_alerts
         WHERE application_id = ?
           AND id <> ?
         ORDER BY id ASC
        """,
        (
            alert.get("application_id"),
            alert.get("id"),
        ),
    )
    duplicates: List[Any] = []
    for candidate in candidates:
        if not _monitoring_routing.is_alert_unresolved(candidate):
            continue
        if _monitoring_linkage_identity(db, candidate) == target_identity:
            duplicates.append(candidate.get("id"))
    return duplicates


def _resolve_alert_linkage(
    db: Any,
    alert_id: Any,
    *,
    enforce_unique_monitoring_link: bool,
) -> LinkageEnvelope:
    alert = _alert_context(db, alert_id)
    alert_type = str(alert.get("alert_type") or "")
    if alert_type in DOCUMENT_EXPIRY_ALERT_TYPES:
        resolved = resolve_document_linkage(db, alert)
    elif alert_type in SCREENING_HIT_ALERT_TYPES:
        resolved = resolve_screening_linkage(db, alert)
    else:
        raise LinkageError(
            "unsupported_alert_type",
            "This Monitoring Alert type has no canonical linkage contract.",
            http_status=422,
            reasons=["unsupported_exact_alert_type"],
        )
    if enforce_unique_monitoring_link:
        duplicates = _duplicate_monitoring_alert_ids(db, alert)
        if duplicates:
            raise LinkageError(
                "linkage_duplicate",
                "Multiple Monitoring Alerts resolve to the same owner record.",
                linkage_type=resolved["linkage_type"],
                owner_module=resolved["owner"]["module"],
                reasons=["duplicate_monitoring_owner_linkage"],
            )
    return resolved


def resolve_alert_linkage(db: Any, alert_id: Any) -> LinkageEnvelope:
    return _resolve_alert_linkage(
        db,
        alert_id,
        enforce_unique_monitoring_link=True,
    )


def resolve_alert_linkage_envelope(db: Any, alert_id: Any) -> Dict[str, Any]:
    try:
        return dict(resolve_alert_linkage(db, alert_id))
    except LinkageError as exc:
        return exc.payload(alert_id)


def _safe_plan_row(db: Any, alert: Mapping[str, Any]) -> Dict[str, Any]:
    alert_id = alert.get("id")
    alert_type = str(alert.get("alert_type") or "")
    supported = alert_type in (
        DOCUMENT_EXPIRY_ALERT_TYPES | SCREENING_HIT_ALERT_TYPES
    )
    context = _alert_context(db, alert_id)
    if not context.get("linked_application_id"):
        return {
            "alert_id": alert_id,
            "stored_alert_type": alert_type,
            "scope_status": "supported" if supported else "unsupported",
            "linkage_type": (
                "document_expiry"
                if alert_type in DOCUMENT_EXPIRY_ALERT_TYPES
                else "screening_hit"
                if alert_type in SCREENING_HIT_ALERT_TYPES
                else None
            ),
            "linkage_classification": "orphaned_alert",
            "review_disposition": (
                "manual_review_required" if supported else "out_of_scope"
            ),
            "backfill_disposition": (
                "manual_review_required" if supported else "not_required"
            ),
            "error_code": "linkage_missing",
            "reasons": ["missing_application_link"],
            "proposed_linkage": None,
            "duplicate_linkage": False,
            "duplicate_group": None,
            "data_change_required": False,
        }
    if not supported:
        if not context.get("application_client_id"):
            classification = "missing_linkage"
            error_code = "linkage_missing"
            reasons = ["missing_customer_link"]
        elif not context.get("linked_client_id"):
            classification = "broken_linkage"
            error_code = "linkage_broken"
            reasons = ["broken_customer_link"]
        else:
            classification = "not_applicable"
            error_code = "unsupported_alert_type"
            reasons = ["unsupported_exact_alert_type"]
        return {
            "alert_id": alert_id,
            "stored_alert_type": alert_type,
            "scope_status": "unsupported",
            "linkage_type": None,
            "linkage_classification": classification,
            "review_disposition": "out_of_scope",
            "backfill_disposition": "not_required",
            "error_code": error_code,
            "reasons": reasons,
            "proposed_linkage": None,
            "duplicate_linkage": False,
            "duplicate_group": None,
            "data_change_required": False,
        }
    try:
        # The complete plan classifies exact duplicate groups as a separate
        # dimension after resolving each row. Runtime callers use the public
        # resolver above, which fails closed on those same duplicates.
        resolved = _resolve_alert_linkage(
            db,
            alert_id,
            enforce_unique_monitoring_link=False,
        )
    except LinkageError as exc:
        if exc.code == "linkage_ambiguous":
            classification = "ambiguous_linkage"
        elif exc.code == "linkage_missing":
            classification = "missing_linkage"
        else:
            classification = "broken_linkage"
        return {
            "alert_id": alert_id,
            "stored_alert_type": alert_type,
            "scope_status": "supported",
            "linkage_type": exc.linkage_type,
            "linkage_classification": classification,
            "review_disposition": "manual_review_required",
            "backfill_disposition": "manual_review_required",
            "error_code": exc.code,
            "reasons": sorted(exc.reasons),
            "proposed_linkage": None,
            "duplicate_linkage": False,
            "duplicate_group": None,
            "data_change_required": False,
        }
    proposed: Dict[str, Any]
    if resolved["linkage_type"] == "document_expiry":
        document = resolved["document"]
        proposed = {
            "application_id": resolved["application"]["id"],
            "customer_id": resolved["application"]["customer_id"],
            "owner_module": resolved["owner"]["module"],
            "owner_state": resolved["owner"]["state"]["key"],
            "trigger_document_id": document["trigger_document_id"],
            "trigger_document_version": document["trigger_document_version"],
            "trigger_expiry_date": document["trigger_expiry_date"],
            "canonical_document_id": document["canonical_document_id"],
            "current_document_version": document["current_document_version"],
            "current_expiry_date": document["current_expiry_date"],
            "document_type": document["document_type"],
            "person_id": document.get("person_id"),
            "person_type": document.get("person_type"),
            "navigation": dict(resolved["navigation"]),
        }
    else:
        screening = resolved["screening_case"]
        subject = resolved["subject"]
        proposed = {
            "application_id": resolved["application"]["id"],
            "customer_id": resolved["application"]["customer_id"],
            "owner_module": resolved["owner"]["module"],
            "owner_state": resolved["owner"]["state"]["key"],
            "four_eyes_status": resolved["owner"]["state"]["four_eyes_status"],
            "subject_id": subject["id"],
            "subject_person_key": str(subject.get("person_key") or ""),
            "subject_type": subject["type"],
            "provider": screening["provider"],
            "case_identifier": screening["case_identifier"],
            "provider_reference": screening.get("provider_reference"),
            "review_id": screening.get("review_id"),
            "linked_snapshot_id": screening.get("linked_snapshot_id"),
            "current_snapshot_id": screening.get("current_snapshot_id"),
            "snapshot_role": screening.get("snapshot_role"),
            "current_snapshot_status": screening.get("current_snapshot_status"),
            "navigation": dict(resolved["navigation"]),
        }
    return {
        "alert_id": alert_id,
        "stored_alert_type": alert_type,
        "scope_status": "supported",
        "linkage_type": resolved["linkage_type"],
        "linkage_classification": "linked_correctly",
        "review_disposition": "no_action",
        "backfill_disposition": "not_required",
        "error_code": None,
        "reasons": [],
        "proposed_linkage": proposed,
        "duplicate_linkage": False,
        "duplicate_group": None,
        "data_change_required": False,
    }


def build_linkage_plan(db: Any) -> Dict[str, Any]:
    """Return a deterministic, read-only linkage inventory and fingerprint."""
    alerts = _rows(
        db,
        "SELECT id, application_id, alert_type, source_reference, provider, "
        "case_identifier, status, resolved_at "
        "FROM monitoring_alerts ORDER BY id ASC",
    )
    unresolved_by_id = {
        row.get("id"): _monitoring_routing.is_alert_unresolved(row)
        for row in alerts
    }
    rows = [_safe_plan_row(db, alert) for alert in alerts]
    duplicate_keys: Dict[str, List[Any]] = {}
    alert_by_id = {row.get("id"): row for row in alerts}
    for row in rows:
        alert_id = row.get("alert_id")
        if not unresolved_by_id.get(alert_id, False):
            continue
        key = _monitoring_linkage_identity(db, alert_by_id[alert_id])
        if key:
            duplicate_keys.setdefault(key, []).append(row.get("alert_id"))
    duplicate_groups = [
        {"linkage_identity": key, "alert_ids": ids}
        for key, ids in sorted(duplicate_keys.items())
        if len(ids) > 1
    ]
    duplicate_alert_ids = {
        alert_id
        for group in duplicate_groups
        for alert_id in group["alert_ids"]
    }
    group_by_alert_id = {
        alert_id: group["linkage_identity"]
        for group in duplicate_groups
        for alert_id in group["alert_ids"]
    }
    for row in rows:
        if row.get("alert_id") in duplicate_alert_ids:
            row["duplicate_linkage"] = True
            row["duplicate_group"] = group_by_alert_id[row["alert_id"]]
            row["review_disposition"] = "manual_review_required"
            row["backfill_disposition"] = "manual_review_required"
    counts: Dict[str, int] = {
        "total_alerts": len(rows),
        "supported": 0,
        "unsupported": 0,
        "linked_correctly": 0,
        "missing_linkage": 0,
        "broken_linkage": 0,
        "ambiguous_linkage": 0,
        "orphaned_alert": 0,
        "not_applicable": 0,
        "manual_review_required": 0,
        "safely_backfillable": 0,
        "data_changes_planned": 0,
        "duplicate_linkage_groups": len(duplicate_groups),
        "duplicate_linkage_alerts": len(duplicate_alert_ids),
    }
    for row in rows:
        scope_status = str(row.get("scope_status"))
        if scope_status in {"supported", "unsupported"}:
            counts[scope_status] += 1
        classification = str(row.get("linkage_classification"))
        if classification in counts:
            counts[classification] += 1
        if row.get("review_disposition") == "manual_review_required":
            counts["manual_review_required"] += 1
    fingerprint_payload = {
        "contract_version": CONTRACT_VERSION,
        "rows": rows,
        "duplicate_groups": duplicate_groups,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "read_only": True,
        "apply_supported": False,
        "counts": counts,
        "duplicate_groups": duplicate_groups,
        "rows": rows,
        "fingerprint": fingerprint,
    }


def plan_without_timestamp(plan: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the deterministic portion used by repeatability tests."""
    return {key: value for key, value in plan.items() if key != "generated_at"}
