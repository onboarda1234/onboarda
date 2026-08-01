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

import document_reliance_gate as _document_reliance


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
_PARTY_TABLES = {
    "director": ("directors", "full_name"),
    "ubo": ("ubos", "full_name"),
    "intermediary": ("intermediaries", "entity_name"),
}
_ENTITY_KINDS = frozenset({"entity"})
_PERSON_KINDS = frozenset({"director", "ubo", "intermediary", "subject"})


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
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


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
    seen = {str(source.get("id"))}
    next_id = str(source.get("superseded_by_document_id") or "").strip()
    for _ in range(100):
        if not next_id or next_id in seen:
            return False
        seen.add(next_id)
        row = db.execute(
            """
            SELECT id, application_id, person_id, person_type, doc_type, slot_key,
                   superseded_by_document_id
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
        if str(item.get("id")) == str(current.get("id")):
            return True
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
    """Read the same canonical expiry evidence used by document health.

    Persisted date columns take precedence. Verification evidence is accepted
    only as an exact structured field; an unparseable or absent value remains
    unavailable rather than making a stale Monitoring signal authoritative.
    """
    for value in (document.get("expiry_date"), document.get("valid_until")):
        parsed = _timestamp(value)
        if parsed is not None:
            return parsed
    verification = _load_json_object(document.get("verification_results")) or {}
    for key in ("expiry_date", "expiry", "validity_to", "valid_until"):
        parsed = _timestamp(verification.get(key))
        if parsed is not None:
            return parsed
    return None


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
        key = "verified" if reliance["allowed"] else "officer_review_required"
    elif request_status == "generated" and source_is_current:
        key = "request_not_started"

    elif not source_is_current:
        if reliance["allowed"]:
            key = "verified"
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
        elif expiry_at.date() <= today + timedelta(days=30):
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
    if not _document_chain_reaches(db, source, current):
        raise LinkageError(
            "linkage_integrity_error",
            "The document version chain is incomplete or stale.",
            linkage_type="document_expiry",
            owner_module="kyc_documents",
            reasons=["document_version_chain_broken"],
        )
    source_version = _canonical_document_version(
        source.get("version"),
        reason="invalid_trigger_document_version",
    )
    current_version = _canonical_document_version(
        current.get("version"),
        reason="invalid_current_document_version",
    )
    request = _document_request(
        db,
        alert.get("id"),
        alert.get("application_id"),
        source.get("id"),
        current.get("id"),
    )
    state = _document_owner_state(db, alert, source, current, request)
    source_expiry = source.get("expiry_date") or source.get("valid_until")
    current_expiry = current.get("expiry_date") or current.get("valid_until")
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
    kind = str(nested.get("kind") or source.get("subject_kind") or "").strip()
    scope = str(nested.get("scope") or source.get("subject_scope") or "").strip()
    person_key = str(nested.get("person_key") or "").strip()
    if kind in _ENTITY_KINDS and scope == "entity" and not person_key:
        return {"kind": "entity", "scope": "entity", "person_key": ""}
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
        else list(_PARTY_TABLES)
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
    duplicate_names = _rows(
        db,
        f"""
        SELECT id FROM {_PARTY_TABLES[resolved['subject_type']][0]}
         WHERE application_id = ?
           AND {_PARTY_TABLES[resolved['subject_type']][1]} = ?
        """,
        (application_id, resolved.get("subject_name")),
    )
    if len(duplicate_names) != 1:
        raise LinkageError(
            "linkage_ambiguous",
            "Screening Review cannot distinguish duplicate subject names.",
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
    if subject["scope"] == "entity":
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
        "scope": "person",
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


def _screening_review_audit_confirmed(
    db: Any,
    alert: Mapping[str, Any],
    review: Mapping[str, Any],
) -> bool:
    disposition_code = str(review.get("disposition_code") or "").strip()
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
        return False
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
        return False
    reviewer_roles = {
        str(row.get("id")): str(row.get("role") or "").strip().lower()
        for row in reviewer_rows
        if str(row.get("status") or "").strip().lower() == "active"
    }
    if reviewer_roles.get(first_reviewer_id) not in {"admin", "sco", "co"}:
        return False

    if requires_four_eyes and second_reviewer_id:
        if (
            second_reviewer_id == first_reviewer_id
            or reviewer_roles.get(second_reviewer_id) not in {"admin", "sco"}
            or str(review.get("second_disposition_code") or "").strip()
            != disposition_code
            or not str(review.get("second_rationale") or "").strip()
            or _timestamp(review.get("second_reviewed_at")) is None
        ):
            return False
        expected_actor_id = second_reviewer_id
        expected_actor_role = reviewer_roles[second_reviewer_id]
        expected_rationale = str(review.get("second_rationale") or "").strip()
        expected_four_eyes_status = "second_review_complete"
        expected_audit_at = _timestamp(review.get("second_reviewed_at"))
    elif requires_four_eyes:
        expected_actor_id = first_reviewer_id
        expected_actor_role = reviewer_roles[first_reviewer_id]
        expected_rationale = str(review.get("rationale") or "").strip()
        expected_four_eyes_status = "second_review_required"
        expected_audit_at = _timestamp(
            review.get("updated_at") or review.get("created_at")
        )
    else:
        if second_reviewer_id:
            return False
        expected_actor_id = first_reviewer_id
        expected_actor_role = reviewer_roles[first_reviewer_id]
        expected_rationale = str(review.get("rationale") or "").strip()
        expected_four_eyes_status = "complete"
        expected_audit_at = _timestamp(
            review.get("updated_at") or review.get("created_at")
        )
    if not expected_rationale or expected_audit_at is None:
        return False

    targets = []
    for candidate in (alert.get("application_ref"), alert.get("application_id")):
        value = str(candidate or "").strip()
        if value and value not in targets:
            targets.append(value)
    if not targets:
        return False
    placeholders = ",".join("?" for _ in targets)
    rows = _rows(
        db,
        f"""
        SELECT timestamp, detail
          FROM audit_log
         WHERE action = 'Screening Review'
           AND target IN ({placeholders})
         ORDER BY id DESC
        """,
        tuple(targets),
    )
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
        if (
            payload_subject == subject_name
            and payload_subject_type == subject_type
            and payload_code == disposition_code
            and expected_case in audit_case_ids
            and (not expected_alert or expected_alert in audit_alert_ids)
            and payload_provider == str(alert.get("provider") or "").strip()
            and payload_application == str(alert.get("application_id") or "")
            and payload_actor_id == expected_actor_id
            and payload_actor_role == expected_actor_role
            and payload_rationale == expected_rationale
            and _truthy(detail.get("requires_four_eyes")) == requires_four_eyes
            and payload_four_eyes_status == expected_four_eyes_status
            and audit_at is not None
            and audit_at >= expected_audit_at
        ):
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
        snapshot.get("linked_snapshot_id")
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
    audit_confirmed = _screening_review_audit_confirmed(db, alert, review)
    review_at = _timestamp(review.get("updated_at") or review.get("created_at"))
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
    elif newest_evidence_at and (not review_at or newest_evidence_at > review_at):
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


def _snapshot_subject(row: Mapping[str, Any]) -> Dict[str, str]:
    return {
        "kind": str(row.get("subject_kind") or "").strip(),
        "scope": str(row.get("subject_scope") or "").strip(),
        "person_key": str(row.get("subject_person_key") or "").strip(),
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
    return {
        "case_ids": _json_string_list(row.get("provider_case_ids_json")),
        "alert_ids": _json_string_list(row.get("provider_alert_ids_json")),
    }


def _screening_snapshot_projection(
    db: Any,
    alert: Mapping[str, Any],
    source: Mapping[str, Any],
    subject_contract: Mapping[str, str],
) -> Dict[str, Any]:
    normalized_snapshot_id = source.get("normalized_record_id")
    if normalized_snapshot_id in (None, ""):
        return {
            "linked_snapshot_id": None,
            "current_snapshot_id": None,
            "snapshot_role": "unavailable",
            "normalization_status": "unavailable",
            "current_snapshot_status": "unavailable",
            "current_snapshot_at": None,
            "current_snapshot_scope": "unavailable",
        }
    cache = getattr(db, "_monitoring_linkage_snapshot_cache", None)
    cache_key = (
        str(alert.get("application_id") or ""),
        str(alert.get("application_client_id") or ""),
        str(alert.get("provider") or ""),
    )
    rows = cache.get(cache_key) if isinstance(cache, dict) else None
    if rows is None:
        if getattr(db, "is_postgres", False):
            snapshot_sql = """
                SELECT id, normalization_status, created_at, updated_at,
                       CASE WHEN normalization_status = 'success' THEN COALESCE(
                           normalized_report_json::jsonb #>> '{provider_specific,complyadvantage,screening_subject,kind}',
                           normalized_report_json::jsonb ->> 'screening_subject_kind'
                       ) END AS subject_kind,
                       CASE WHEN normalization_status = 'success' THEN COALESCE(
                           normalized_report_json::jsonb #>> '{provider_specific,complyadvantage,screening_subject,scope}',
                           normalized_report_json::jsonb ->> 'subject_scope'
                       ) END AS subject_scope,
                       CASE WHEN normalization_status = 'success' THEN COALESCE(
                           normalized_report_json::jsonb #>> '{provider_specific,complyadvantage,screening_subject,person_key}',
                           normalized_report_json::jsonb ->> 'screening_subject_person_key'
                       ) END AS subject_person_key,
                       CASE WHEN normalization_status = 'success' THEN
                           (normalized_report_json::jsonb #> '{provider_specific,complyadvantage,provider_references,case_ids}')::text
                       END AS provider_case_ids_json,
                       CASE WHEN normalization_status = 'success' THEN
                           (normalized_report_json::jsonb #> '{provider_specific,complyadvantage,provider_references,alert_ids}')::text
                       END AS provider_alert_ids_json
                  FROM screening_reports_normalized
                 WHERE application_id = ? AND client_id = ? AND provider = ?
                 ORDER BY id DESC
            """
        else:
            snapshot_sql = """
                SELECT id, normalization_status, created_at, updated_at,
                       CASE WHEN normalization_status = 'success' THEN COALESCE(
                           json_extract(normalized_report_json, '$.provider_specific.complyadvantage.screening_subject.kind'),
                           json_extract(normalized_report_json, '$.screening_subject_kind')
                       ) END AS subject_kind,
                       CASE WHEN normalization_status = 'success' THEN COALESCE(
                           json_extract(normalized_report_json, '$.provider_specific.complyadvantage.screening_subject.scope'),
                           json_extract(normalized_report_json, '$.subject_scope')
                       ) END AS subject_scope,
                       CASE WHEN normalization_status = 'success' THEN COALESCE(
                           json_extract(normalized_report_json, '$.provider_specific.complyadvantage.screening_subject.person_key'),
                           json_extract(normalized_report_json, '$.screening_subject_person_key')
                       ) END AS subject_person_key,
                       CASE WHEN normalization_status = 'success' THEN
                           json_extract(normalized_report_json, '$.provider_specific.complyadvantage.provider_references.case_ids')
                       END AS provider_case_ids_json,
                       CASE WHEN normalization_status = 'success' THEN
                           json_extract(normalized_report_json, '$.provider_specific.complyadvantage.provider_references.alert_ids')
                       END AS provider_alert_ids_json
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
    linked = next(
        (row for row in rows if str(row.get("id")) == str(normalized_snapshot_id)),
        None,
    )
    if not linked:
        raise LinkageError(
            "linkage_broken",
            "The linked screening evidence snapshot does not exist.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["missing_screening_evidence_snapshot"],
        )
    if str(linked.get("normalization_status") or "") != "success":
        raise LinkageError(
            "linkage_broken",
            "The linked screening snapshot is unavailable for authoritative review.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["screening_snapshot_not_successful"],
        )
    linked_subject = _snapshot_subject(linked)
    if linked_subject != dict(subject_contract):
        raise LinkageError(
            "linkage_integrity_error",
            "The linked screening snapshot belongs to a different subject.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["screening_snapshot_subject_mismatch"],
        )
    provider_references = _snapshot_provider_references(linked)
    expected_case = str(alert.get("case_identifier") or "").strip()
    expected_alert = str(source.get("alert_identifier") or "").strip()
    if expected_case not in provider_references["case_ids"] or (
        expected_alert and expected_alert not in provider_references["alert_ids"]
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
    if not current:
        raise LinkageError(
            "linkage_broken",
            "No current screening snapshot exists for the canonical subject.",
            linkage_type="screening_hit",
            owner_module="screening_review",
            reasons=["missing_current_screening_snapshot"],
        )
    return {
        "linked_snapshot_id": linked.get("id"),
        "current_snapshot_id": current.get("id"),
        "snapshot_role": (
            "current"
            if str(current.get("id")) == str(linked.get("id"))
            else "historical"
        ),
        "normalization_status": "success",
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


def resolve_alert_linkage(db: Any, alert_id: Any) -> LinkageEnvelope:
    alert = _alert_context(db, alert_id)
    alert_type = str(alert.get("alert_type") or "")
    if alert_type in DOCUMENT_EXPIRY_ALERT_TYPES:
        return resolve_document_linkage(db, alert)
    if alert_type in SCREENING_HIT_ALERT_TYPES:
        return resolve_screening_linkage(db, alert)
    raise LinkageError(
        "unsupported_alert_type",
        "This Monitoring Alert type has no canonical linkage contract.",
        http_status=422,
        reasons=["unsupported_exact_alert_type"],
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
        resolved = resolve_alert_linkage(db, alert_id)
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
            "subject_person_key": subject.get("person_key"),
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
    alerts = _rows(db, "SELECT id, alert_type FROM monitoring_alerts ORDER BY id ASC")
    rows = [_safe_plan_row(db, alert) for alert in alerts]
    duplicate_keys: Dict[str, List[Any]] = {}
    for row in rows:
        proposed = row.get("proposed_linkage") or {}
        if row.get("linkage_type") == "document_expiry":
            identity = proposed.get("trigger_document_id")
            application_id = proposed.get("application_id")
            key = (
                f"document:{application_id}:{identity}"
                if application_id and identity
                else ""
            )
        elif row.get("linkage_type") == "screening_hit":
            application_id = proposed.get("application_id")
            provider = proposed.get("provider")
            case_id = proposed.get("case_identifier")
            provider_reference = proposed.get("provider_reference")
            subject_type = proposed.get("subject_type")
            subject_id = proposed.get("subject_id")
            person_key = proposed.get("subject_person_key") or ""
            key = (
                "screening:"
                + json.dumps(
                    {
                        "application_id": application_id,
                        "case_identifier": case_id,
                        "provider": provider,
                        "provider_reference": provider_reference,
                        "subject_id": subject_id,
                        "subject_person_key": person_key,
                        "subject_type": subject_type,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                if application_id
                and provider
                and case_id
                and provider_reference not in (None, "")
                and subject_type
                and subject_id
                else ""
            )
        else:
            key = ""
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
