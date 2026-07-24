"""
Canonical KYC document reliance gate.

This module is intentionally limited to the existing ``documents`` table and
the onboarding/KYC document slots used by KYC submission, compliance memos, and
final approval. It does not model EDD, change-management, periodic-review, or
monitoring evidence.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from verification_state import (
    STATE_FAILED,
    STATE_FLAGGED,
    STATE_IN_PROGRESS,
    STATE_PENDING,
    STATE_SKIPPED,
    STATE_VERIFIED,
    normalize_verification_state,
)


POLICY_VERSION = "document_reliance_gate_v2"
ACTIVE_DOCUMENT_SQL = "COALESCE(is_current, TRUE) = TRUE"
MANUAL_ACCEPTANCE_ROLES = {"admin", "sco"}
DEFAULT_STALE_DAYS = int(os.environ.get("DOCUMENT_VERIFICATION_STALE_DAYS", "365") or "365")

ALLOWED_RELIANCE_STATES = ("verified", "manual_accepted")
BLOCKED_RELIANCE_STATES = (
    "missing",
    "uploaded",
    "pending",
    "running",
    "failed",
    "flagged",
    "skipped",
    "stale",
    "missing_verification_results",
    "missing_verified_at",
    "missing_agent_execution",
    "unsupported",
    "superseded",
)

DOCUMENT_TYPE_NORMALIZE = {
    "doc-coi": "cert_inc",
    "certificate-incorporation": "cert_inc",
    "certificate incorporation": "cert_inc",
    "certificate_of_incorporation": "cert_inc",
    "certificate of incorporation": "cert_inc",
    "incorporation_certificate": "cert_inc",
    "incorporation certificate": "cert_inc",
    "proof_of_address": "poa",
    "proof of address": "poa",
    "address_proof": "poa",
    "financial_statements": "fin_stmt",
    "financial statements": "fin_stmt",
    "source_of_wealth": "source_wealth",
    "source of wealth": "source_wealth",
    "source_of_funds": "source_funds",
    "source of funds": "source_funds",
    "doc-memarts": "memarts",
    "memorandum_of_association": "memarts",
    "memorandum of association": "memarts",
    "memorandum_and_articles": "memarts",
    "memorandum and articles": "memarts",
    "memorandum_articles": "memarts",
    "articles_of_association": "memarts",
    "articles of association": "memarts",
    "doc-shareholders": "reg_sh",
    "register_of_shareholders": "reg_sh",
    "register of shareholders": "reg_sh",
    "shareholder_register": "reg_sh",
    "shareholder register": "reg_sh",
    "doc-directors-reg": "reg_dir",
    "register_of_directors": "reg_dir",
    "register of directors": "reg_dir",
    "director_register": "reg_dir",
    "director register": "reg_dir",
    "doc-financials": "fin_stmt",
    "doc-proof-address": "poa",
    "doc-board-res": "board_res",
    "board_resolution": "board_res",
    "board resolution": "board_res",
    "doc-structure-chart": "structure_chart",
    "structure chart": "structure_chart",
    "ownership_structure_chart": "structure_chart",
    "doc-bank-ref": "bankref",
    "bank_reference": "bankref",
    "bank reference": "bankref",
    "license": "licence",
    "licence_certificate": "licence",
    "license_certificate": "licence",
    "general": "supporting_document",
}


def _row_to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, Mapping):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _parse_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    parsed = datetime.strptime(text[:19], fmt)
                    break
                except ValueError:
                    parsed = None
            if parsed is None:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_safe(value: Any) -> Any:
    """Return a JSON-serializable copy of gate payload values.

    PostgreSQL drivers return TIMESTAMP columns as ``datetime`` objects. The
    gate is returned directly by several Tornado handlers, so normalize here
    once instead of relying on every caller to remember ``default=str``.
    """
    if isinstance(value, datetime):
        iso_value = (
            value.astimezone(timezone.utc).isoformat()
            if value.tzinfo
            else value.isoformat()
        )
        return iso_value.replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(nested) for nested in value]
    if isinstance(value, set):
        return sorted(_json_safe(nested) for nested in value)
    return value


def _normalize_document_type(value: Any) -> str:
    raw = str(value or "general").strip()
    raw_lower = raw.lower()
    candidate = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw_lower).strip("_")
    normalized = DOCUMENT_TYPE_NORMALIZE.get(
        raw,
        DOCUMENT_TYPE_NORMALIZE.get(
            raw_lower,
            DOCUMENT_TYPE_NORMALIZE.get(candidate, candidate),
        ),
    )
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", normalized).strip("_").lower()
    return (normalized or "general")[:80]


def _normalize_person_type(value: Any) -> str:
    normalized = (
        str(value or "")
        .strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
    )
    if normalized in {"director", "directors", "dir"}:
        return "director"
    if normalized in {"ubo", "ubos", "beneficial_owner"}:
        return "ubo"
    if normalized in {"intermediary", "intermediaries", "inter"}:
        return "intermediary"
    return normalized


def document_slot_key(doc_type: Any, person_id: Any = None, *, person_type: Any = None) -> str:
    normalized = _normalize_document_type(doc_type)
    person = str(person_id or "").strip()
    if person:
        typed_person = _normalize_person_type(person_type) or "unknown"
        return f"person:{typed_person}:{person}:{normalized}"
    return f"entity:{normalized}"


def _legacy_person_slot_key(doc_type: Any, person_id: Any = None) -> Optional[str]:
    person = str(person_id or "").strip()
    if not person:
        return None
    return f"person:{person}:{_normalize_document_type(doc_type)}"


def _party_requirement_key(person: Mapping[str, Any]) -> Optional[str]:
    return person.get("id")


def build_required_document_expectations(db: Any, app: Mapping[str, Any]) -> List[Dict[str, Any]]:
    app_id = _row_get(app, "id")
    expectations: List[Dict[str, Any]] = [
        {"doc_type": "cert_inc", "label": "Certificate of Incorporation", "person_id": None},
        {"doc_type": "memarts", "label": "Memorandum of Association", "person_id": None},
        {"doc_type": "reg_sh", "label": "Shareholder Register", "person_id": None},
        {"doc_type": "reg_dir", "label": "Register of Directors", "person_id": None},
        {"doc_type": "fin_stmt", "label": "Financial Statements / Management Accounts", "person_id": None},
        {"doc_type": "poa", "label": "Proof of Registered Address", "person_id": None},
        {"doc_type": "board_res", "label": "Board Resolution", "person_id": None},
        {"doc_type": "structure_chart", "label": "Company Structure Chart", "person_id": None},
    ]

    if app_id and db is not None:
        try:
            directors = [_row_to_dict(row) for row in db.execute(
                "SELECT * FROM directors WHERE application_id=? ORDER BY person_key, id",
                (app_id,),
            ).fetchall()]
        except Exception:
            directors = []
        try:
            ubos = [_row_to_dict(row) for row in db.execute(
                "SELECT * FROM ubos WHERE application_id=? ORDER BY person_key, id",
                (app_id,),
            ).fetchall()]
        except Exception:
            ubos = []
        try:
            intermediaries = [_row_to_dict(row) for row in db.execute(
                "SELECT * FROM intermediaries WHERE application_id=? ORDER BY person_key, id",
                (app_id,),
            ).fetchall()]
        except Exception:
            intermediaries = []

        identity_owners: Dict[str, set] = {}
        typed_people = (
            [("director", person) for person in directors]
            + [("ubo", person) for person in ubos]
            + [("intermediary", person) for person in intermediaries]
        )
        for party_type, person in typed_people:
            stable_id = str(person.get("id") or "").strip()
            owner = (party_type, stable_id)
            for identifier in (
                stable_id,
                str(person.get("person_key") or "").strip(),
            ):
                if identifier:
                    identity_owners.setdefault(identifier, set()).add(owner)

        for party_type, people in (("director", directors), ("ubo", ubos)):
            for person in people:
                person_id = _party_requirement_key(person)
                if not person_id:
                    continue
                legacy_person_key = str(person.get("person_key") or "").strip()
                expected_owner = (party_type, str(person_id))
                if (
                    not legacy_person_key
                    or legacy_person_key == str(person_id)
                    or identity_owners.get(legacy_person_key) != {expected_owner}
                ):
                    legacy_person_key = None
                owner = person.get("full_name") or person_id
                expectations.append({
                    "doc_type": "passport",
                    "label": f"Passport / Government ID for {owner}",
                    "person_id": person_id,
                    "person_type": party_type,
                    "legacy_person_key": legacy_person_key,
                })
                expectations.append({
                    "doc_type": "poa",
                    "label": f"Proof of Address for {owner}",
                    "person_id": person_id,
                    "person_type": party_type,
                    "legacy_person_key": legacy_person_key,
                })
        for person in intermediaries:
            person_id = _party_requirement_key(person)
            if not person_id:
                continue
            legacy_person_key = str(person.get("person_key") or "").strip()
            expected_owner = ("intermediary", str(person_id))
            if (
                not legacy_person_key
                or legacy_person_key == str(person_id)
                or identity_owners.get(legacy_person_key) != {expected_owner}
            ):
                legacy_person_key = None
            owner = person.get("entity_name") or person.get("full_name") or person_id
            for doc_type, label in (
                ("cert_inc", "Certificate of Incorporation"),
                ("reg_dir", "Register of Directors"),
                ("reg_sh", "Register of Shareholders"),
                ("cert_gs", "Certificate of Good Standing"),
                ("fin_stmt", "Financial Statements"),
            ):
                expectations.append({
                    "doc_type": doc_type,
                    "label": f"{label} for {owner}",
                    "person_id": person_id,
                    "person_type": "intermediary",
                    "legacy_person_key": legacy_person_key,
                })

    for expectation in expectations:
        expectation["doc_type"] = _normalize_document_type(expectation["doc_type"])
        expectation["slot_key"] = document_slot_key(
            expectation["doc_type"],
            expectation.get("person_id"),
            person_type=expectation.get("person_type"),
        )
        expectation["source"] = "kyc_required_document"
    return expectations


def _load_active_documents(db: Any, app_id: Any) -> List[Dict[str, Any]]:
    if db is None or not app_id:
        return []
    rows = db.execute(
        f"SELECT * FROM documents WHERE application_id=? AND {ACTIVE_DOCUMENT_SQL}",
        (app_id,),
    ).fetchall()
    docs = []
    for row in rows:
        doc = _row_to_dict(row)
        doc["doc_type"] = _normalize_document_type(doc.get("doc_type"))
        doc["slot_key"] = doc.get("slot_key") or document_slot_key(
            doc.get("doc_type"),
            doc.get("person_id"),
            person_type=doc.get("person_type"),
        )
        docs.append(doc)
    return docs


def _index_documents(docs: Iterable[Mapping[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[Any, Dict[str, Any]]]:
    by_slot: Dict[str, Dict[str, Any]] = {}
    by_id: Dict[Any, Dict[str, Any]] = {}
    for doc in docs:
        doc_dict = dict(doc)
        slot = doc_dict.get("slot_key") or document_slot_key(
            doc_dict.get("doc_type"),
            doc_dict.get("person_id"),
            person_type=doc_dict.get("person_type"),
        )
        if slot in by_slot:
            existing = by_slot[slot]
            conflict_docs = list(existing.get("_conflict_documents") or [existing])
            conflict_docs.append(doc_dict)
            by_slot[slot] = {
                "_integrity_conflict": "duplicate_document_slot",
                "_conflict_documents": conflict_docs,
                "slot_key": slot,
            }
        else:
            by_slot[slot] = doc_dict
        if doc_dict.get("id"):
            doc_id = doc_dict.get("id")
            if doc_id in by_id:
                by_id[doc_id] = {
                    "_integrity_conflict": "duplicate_document_id",
                    "id": doc_id,
                }
            else:
                by_id[doc_id] = doc_dict
    return by_slot, by_id


def _expectation_slot_aliases(expectation: Mapping[str, Any]) -> List[str]:
    aliases = [str(expectation.get("slot_key") or "").strip()]
    person_id = str(expectation.get("person_id") or "").strip()
    person_type = _normalize_person_type(expectation.get("person_type"))
    if person_id:
        stable_legacy = _legacy_person_slot_key(expectation.get("doc_type"), person_id)
        if stable_legacy and stable_legacy not in aliases:
            aliases.append(stable_legacy)
    legacy_person_key = str(expectation.get("legacy_person_key") or "").strip()
    if legacy_person_key and person_type:
        for alias in (
            document_slot_key(
                expectation.get("doc_type"),
                legacy_person_key,
                person_type=person_type,
            ),
            _legacy_person_slot_key(expectation.get("doc_type"), legacy_person_key),
        ):
            if alias and alias not in aliases:
                aliases.append(alias)
    return [alias for alias in aliases if alias]


def _document_association_error(
    expectation: Mapping[str, Any],
    doc: Mapping[str, Any],
) -> Optional[str]:
    if doc.get("_integrity_conflict"):
        return "Multiple current documents claim the same required slot"

    expected_doc_type = _normalize_document_type(expectation.get("doc_type"))
    actual_doc_type = _normalize_document_type(doc.get("doc_type"))
    if actual_doc_type != expected_doc_type:
        return (
            f"Slot metadata names '{actual_doc_type}' instead of "
            f"'{expected_doc_type}'"
        )

    expected_person_id = str(expectation.get("person_id") or "").strip()
    actual_person_id = str(doc.get("person_id") or "").strip()
    expected_person_type = _normalize_person_type(expectation.get("person_type"))
    actual_person_type = _normalize_person_type(doc.get("person_type"))
    actual_slot = str(doc.get("slot_key") or "").strip()

    if not expected_person_id:
        if actual_person_id or actual_person_type:
            return "Entity document slot contains person ownership metadata"
        if actual_slot.startswith("rmi:") and isinstance(doc.get("_rmi_trace"), dict):
            if doc["_rmi_trace"].get("canonical_slot_key") != expectation.get("slot_key"):
                return "RMI document canonical slot metadata is inconsistent"
            return None
        if actual_slot and actual_slot != expectation.get("slot_key"):
            return "Entity document slot_key does not match its required document type"
        return None

    if actual_person_type != expected_person_type:
        return (
            "Person-scoped evidence is missing the exact persisted person_type "
            "required by this slot"
        )
    legacy_person_key = str(expectation.get("legacy_person_key") or "").strip()
    if actual_person_id not in {expected_person_id, legacy_person_key}:
        return "Person-scoped evidence references a different party"
    if actual_person_id == legacy_person_key and not legacy_person_key:
        return "Legacy party alias is not uniquely resolvable"

    allowed_slots = set(_expectation_slot_aliases(expectation))
    if actual_slot.startswith("rmi:") and isinstance(doc.get("_rmi_trace"), dict):
        if doc["_rmi_trace"].get("canonical_slot_key") != expectation.get("slot_key"):
            return "RMI document canonical slot metadata is inconsistent"
    elif actual_slot and actual_slot not in allowed_slots:
        return "Person-scoped evidence slot_key is inconsistent with its party metadata"
    return None


def _select_expected_document(
    expectation: Mapping[str, Any],
    docs: Iterable[Mapping[str, Any]],
    docs_by_slot: Mapping[str, Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    candidates: Dict[str, Dict[str, Any]] = {}
    for slot_key in _expectation_slot_aliases(expectation):
        candidate = docs_by_slot.get(slot_key)
        if candidate:
            candidate_dict = dict(candidate)
            candidate_id = str(candidate_dict.get("id") or f"conflict:{slot_key}")
            candidates[candidate_id] = candidate_dict

    expected_person_id = str(expectation.get("person_id") or "").strip()
    legacy_person_key = str(expectation.get("legacy_person_key") or "").strip()
    expected_doc_type = _normalize_document_type(expectation.get("doc_type"))
    for raw_doc in docs:
        doc = dict(raw_doc)
        if _normalize_document_type(doc.get("doc_type")) != expected_doc_type:
            continue
        actual_slot = str(doc.get("slot_key") or "").strip()
        # Special request slots are not base-KYC candidates. RMI evidence is
        # eligible only after the accepted request/item mapping above attaches
        # a canonical trace; enhanced-requirement evidence never borrows or
        # displaces the base entity/person slot.
        if actual_slot.startswith("rmi:") and not isinstance(doc.get("_rmi_trace"), dict):
            continue
        if actual_slot.startswith("enhanced_requirement:"):
            continue
        actual_person_id = str(doc.get("person_id") or "").strip()
        if expected_person_id:
            if actual_person_id not in {expected_person_id, legacy_person_key}:
                continue
        elif actual_person_id:
            continue
        candidate_id = str(doc.get("id") or f"row:{id(raw_doc)}")
        candidates.setdefault(candidate_id, doc)

    if not candidates:
        return None
    if len(candidates) > 1:
        return {
            "_integrity_conflict": "multiple_documents_for_expectation",
            "_conflict_documents": list(candidates.values()),
            "slot_key": expectation.get("slot_key"),
        }
    return next(iter(candidates.values()))


def _rmi_item_text(item: Mapping[str, Any]) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("label", "description")
        if item.get(key) is not None
    ).lower()


def resolve_rmi_replacement_slot(
    db: Any,
    app: Mapping[str, Any],
    rmi_item: Mapping[str, Any],
    *,
    doc_type: Any = None,
    expectations: Optional[Iterable[Mapping[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve an RMI item to the original required KYC slot when possible.

    Structured RMI items predate an explicit canonical-slot column. Existing
    requests therefore carry only doc_type plus human labels such as
    ``entity:reg_sh`` or ``ubo_1:passport``. This resolver is intentionally
    conservative: it only maps when the item can be matched to one required
    KYC expectation by exact canonical slot, person/doc shorthand, or an
    unambiguous entity/person doc_type fallback.
    """
    requested_doc_type = _normalize_document_type(doc_type or rmi_item.get("doc_type"))
    if not requested_doc_type:
        return None
    app_dict = _row_to_dict(app)
    expected_items = [
        dict(expectation)
        for expectation in (expectations or build_required_document_expectations(db, app_dict))
        if _normalize_document_type(expectation.get("doc_type")) == requested_doc_type
    ]
    if not expected_items:
        return None

    text = _rmi_item_text(rmi_item)
    exact_matches = [
        expectation for expectation in expected_items
        if str(expectation.get("slot_key") or "").lower()
        and str(expectation.get("slot_key") or "").lower() in text
    ]
    if len(exact_matches) == 1:
        return dict(exact_matches[0])

    shorthand_matches = []
    for expectation in expected_items:
        person_refs = [
            str(expectation.get("person_id") or "").strip(),
            str(expectation.get("legacy_person_key") or "").strip(),
        ]
        person_refs = [person_ref for person_ref in person_refs if person_ref]
        if not person_refs:
            continue
        if any(
            f"{person_ref}:{requested_doc_type}".lower() in text
            for person_ref in person_refs
        ):
            shorthand_matches.append(expectation)
    if len(shorthand_matches) == 1:
        return dict(shorthand_matches[0])

    entity_matches = [expectation for expectation in expected_items if not expectation.get("person_id")]
    if len(entity_matches) == 1 and (f"entity:{requested_doc_type}" in text or len(expected_items) == 1):
        return dict(entity_matches[0])

    person_matches = [expectation for expectation in expected_items if expectation.get("person_id")]
    if len(person_matches) == 1 and not entity_matches:
        return dict(person_matches[0])

    return None


def _apply_rmi_document_slot_aliases(
    db: Any,
    app: Mapping[str, Any],
    expectations: Iterable[Mapping[str, Any]],
    docs_by_slot: Dict[str, Dict[str, Any]],
    docs_by_id: Dict[Any, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    app_id = _row_get(app, "id")
    if db is None or not app_id or not docs_by_id:
        return []
    try:
        rows = db.execute(
            """
            SELECT i.id AS rmi_item_id,
                   i.request_id AS rmi_request_id,
                   i.doc_type,
                   i.label,
                   i.description,
                   i.status AS rmi_item_status,
                   i.document_id,
                   r.status AS rmi_request_status
              FROM rmi_request_items i
              JOIN rmi_requests r ON r.id = i.request_id
             WHERE r.application_id = ?
               AND i.document_id IS NOT NULL
               AND LOWER(COALESCE(r.status, 'open')) <> 'cancelled'
            """,
            (app_id,),
        ).fetchall()
    except Exception:
        return []

    aliases: List[Dict[str, Any]] = []
    for row in rows:
        item = _row_to_dict(row)
        if str(item.get("rmi_item_status") or "").strip().lower() == "rejected":
            continue
        doc = docs_by_id.get(item.get("document_id"))
        if not doc or doc.get("_integrity_conflict"):
            continue
        rmi_slot_key = f"rmi:{item.get('rmi_item_id')}"
        current_slot = str(doc.get("slot_key") or "").strip()
        if current_slot and current_slot != rmi_slot_key and current_slot in docs_by_slot:
            continue
        expectation = resolve_rmi_replacement_slot(
            db,
            app,
            item,
            doc_type=doc.get("doc_type") or item.get("doc_type"),
            expectations=expectations,
        )
        if not expectation:
            continue
        canonical_slot = expectation.get("slot_key")
        if not canonical_slot or canonical_slot in docs_by_slot:
            continue
        alias_doc = dict(doc)
        alias_doc["_rmi_trace"] = {
            "rmi_request_id": item.get("rmi_request_id"),
            "rmi_item_id": item.get("rmi_item_id"),
            "rmi_item_status": item.get("rmi_item_status"),
            "rmi_request_status": item.get("rmi_request_status"),
            "rmi_slot_key": rmi_slot_key,
            "canonical_slot_key": canonical_slot,
        }
        docs_by_slot[canonical_slot] = alias_doc
        aliases.append({
            **alias_doc["_rmi_trace"],
            "document_id": doc.get("id"),
            "doc_type": doc.get("doc_type"),
        })
    return aliases


def manual_acceptance_details(doc: Mapping[str, Any]) -> Dict[str, Any]:
    role = str(doc.get("reviewer_role") or "").strip().lower()
    reason = str(doc.get("review_comment") or "").strip()
    actor = str(doc.get("reviewed_by") or "").strip()
    accepted_at = str(doc.get("reviewed_at") or "").strip()
    accepted = (
        str(doc.get("review_status") or "").strip().lower() == "accepted"
        and role in MANUAL_ACCEPTANCE_ROLES
        and bool(reason)
        and bool(actor)
        and bool(accepted_at)
    )
    return {
        "accepted": accepted,
        "scope": "document_reliance",
        "role": role,
        "reason": reason,
        "actor": actor,
        "accepted_at": accepted_at,
        "audit_required": True,
    }


def _latest_agent_execution(db: Any, document_id: Any) -> Optional[Dict[str, Any]]:
    if db is None or not document_id:
        return None
    try:
        row = db.execute(
            """
            SELECT id, application_id, document_id, agent_name, agent_number, status,
                   requires_review, started_at, completed_at, error_message
              FROM agent_executions
             WHERE document_id = ?
               AND agent_number = 1
               AND LOWER(COALESCE(agent_name, '')) = 'verify_document'
             ORDER BY completed_at DESC, id DESC
             LIMIT 1
            """,
            (document_id,),
        ).fetchone()
    except Exception:
        return None
    return _row_to_dict(row) if row else None


def _agent_execution_satisfies(execution: Optional[Mapping[str, Any]]) -> bool:
    if not execution:
        return False
    status = str(execution.get("status") or "").strip().lower()
    if status in {"verified", "pass", "passed", "success", "approved"}:
        return True
    return normalize_verification_state(status) == STATE_VERIFIED


def _verification_results_satisfy(results: Any) -> bool:
    if not isinstance(results, dict) or not results:
        return False
    overall = str(results.get("overall") or results.get("status") or "").strip().lower()
    checks = results.get("checks")
    if not isinstance(checks, list) or not checks:
        return False
    if overall not in {"verified", "pass", "passed", "approved", "success"}:
        return False
    for check in checks:
        if not isinstance(check, dict):
            return False
        result = str(check.get("result") or check.get("status") or "").strip().lower()
        if result and result not in {"pass", "passed", "verified", "success", "ok"}:
            return False
    return True


def _blocker(
    code: str,
    expectation: Mapping[str, Any],
    doc: Optional[Mapping[str, Any]],
    reason: str,
    *,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    document_id = doc.get("id") if doc else None
    doc_type = expectation.get("doc_type") or (doc.get("doc_type") if doc else "")
    label = expectation.get("label") or doc_type or "Required document"
    return {
        "code": code,
        "category": "Document Evidence",
        "title": label,
        "description": reason,
        "reason": reason,
        "severity": "critical",
        "blocking": True,
        "source": "document_reliance_gate",
        "ctaLabel": "Resolve document evidence",
        "tab": "kyc-docs",
        "anchorId": "detail-kyc-documents-details",
        "blocker_group": "document_evidence",
        "blocker_group_label": "Document Evidence",
        "action_key": "documents.resolve",
        "doc_type": doc_type,
        "slot_key": expectation.get("slot_key") or (doc.get("slot_key") if doc else None),
        "document_id": document_id,
        "doc_name": doc.get("doc_name") if doc else None,
        "verification_status": status or (doc.get("verification_status") if doc else "missing"),
    }


def _snapshot_base(expectation: Mapping[str, Any], doc: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    snapshot = {
        "required_document_type": expectation.get("doc_type"),
        "label": expectation.get("label"),
        "slot_key": expectation.get("slot_key"),
        "person_id": expectation.get("person_id"),
        "person_type": expectation.get("person_type"),
        "source": expectation.get("source", "kyc_required_document"),
        "document_id": doc.get("id") if doc else None,
        "doc_name": doc.get("doc_name") if doc else None,
        "verification_status": doc.get("verification_status") if doc else "missing",
        "verified_at": doc.get("verified_at") if doc else None,
        "review_status": doc.get("review_status") if doc else None,
    }
    if doc and isinstance(doc.get("_rmi_trace"), dict):
        snapshot["rmi_trace"] = dict(doc["_rmi_trace"])
        snapshot["canonical_slot_satisfied_by_rmi"] = True
    return snapshot


def _evaluate_document(
    db: Any,
    expectation: Mapping[str, Any],
    doc: Optional[Mapping[str, Any]],
    *,
    require_agent_execution: bool,
    stale_days: int,
) -> Dict[str, Any]:
    snapshot = _snapshot_base(expectation, doc)
    blockers: List[Dict[str, Any]] = []

    if not doc:
        reason = f"{expectation.get('label')} is missing."
        blockers.append(_blocker("missing_required_document", expectation, None, reason, status="missing"))
        snapshot.update({
            "reliance_state": "missing",
            "verification_method": None,
            "blocker_reasons": [reason],
        })
        return {"allowed": False, "snapshot": snapshot, "blockers": blockers}

    if not _truthy(doc.get("is_current"), default=True) or doc.get("superseded_at") or doc.get("superseded_by_document_id"):
        reason = f"{expectation.get('label')} has been replaced or superseded."
        blockers.append(_blocker("superseded_document", expectation, doc, reason, status="superseded"))
        snapshot.update({"reliance_state": "superseded", "verification_method": None, "blocker_reasons": [reason]})
        return {"allowed": False, "snapshot": snapshot, "blockers": blockers}

    expected_doc_type = _normalize_document_type(expectation.get("doc_type"))
    actual_doc_type = _normalize_document_type(doc.get("doc_type"))
    if actual_doc_type != expected_doc_type:
        reason = (
            f"{expectation.get('label')} slot contains unsupported document type "
            f"'{actual_doc_type}' instead of '{expected_doc_type}'."
        )
        blockers.append(_blocker("unsupported_document_type", expectation, doc, reason, status="unsupported"))
        snapshot.update({"reliance_state": "unsupported", "verification_method": None, "blocker_reasons": [reason]})
        return {"allowed": False, "snapshot": snapshot, "blockers": blockers}

    association_error = _document_association_error(expectation, doc)
    if association_error:
        reason = f"{expectation.get('label')} has invalid party/document association metadata: {association_error}."
        blockers.append(
            _blocker(
                "document_party_association_integrity",
                expectation,
                doc,
                reason,
                status="unsupported",
            )
        )
        snapshot.update({
            "reliance_state": "association_integrity_failed",
            "verification_method": None,
            "blocker_reasons": [reason],
        })
        return {"allowed": False, "snapshot": snapshot, "blockers": blockers}

    manual = manual_acceptance_details(doc)
    snapshot["manual_acceptance"] = manual
    if manual["accepted"]:
        snapshot.update({
            "reliance_state": "manual_accepted",
            "verification_method": "manual_acceptance",
            "blocker_reasons": [],
        })
        return {"allowed": True, "snapshot": snapshot, "blockers": []}

    status = normalize_verification_state(doc.get("verification_status"))
    snapshot["verification_status"] = status
    if status != STATE_VERIFIED:
        state_code = {
            STATE_PENDING: "document_pending_verification",
            STATE_IN_PROGRESS: "document_verification_running",
            STATE_FAILED: "document_verification_failed",
            STATE_FLAGGED: "document_flagged",
            STATE_SKIPPED: "document_verification_skipped",
        }.get(status, "document_unsupported_verification_status")
        reason = {
            STATE_PENDING: f"{expectation.get('label')} is uploaded but verification is pending.",
            STATE_IN_PROGRESS: f"{expectation.get('label')} verification is still running.",
            STATE_FAILED: f"{expectation.get('label')} verification failed.",
            STATE_FLAGGED: f"{expectation.get('label')} is flagged for review.",
            STATE_SKIPPED: f"{expectation.get('label')} verification was skipped and requires manual acceptance.",
        }.get(status, f"{expectation.get('label')} has unsupported verification status '{status}'.")
        blockers.append(_blocker(state_code, expectation, doc, reason, status=status))
        snapshot.update({
            "reliance_state": "uploaded" if status == STATE_PENDING else status,
            "verification_method": None,
            "blocker_reasons": [reason],
        })
        return {"allowed": False, "snapshot": snapshot, "blockers": blockers}

    results = _parse_json(doc.get("verification_results"), {})
    snapshot["verification_results"] = results
    if not _verification_results_satisfy(results):
        reason = f"{expectation.get('label')} is marked verified but lacks clean verification_results evidence."
        blockers.append(_blocker("missing_verification_results", expectation, doc, reason, status=status))

    verified_at = doc.get("verified_at") or (results.get("verified_at") if isinstance(results, dict) else None)
    snapshot["verified_at"] = verified_at
    parsed_verified_at = _parse_timestamp(verified_at)
    if not parsed_verified_at:
        reason = f"{expectation.get('label')} is marked verified but verified_at is missing."
        blockers.append(_blocker("missing_verified_at", expectation, doc, reason, status=status))
    elif stale_days > 0 and parsed_verified_at < (datetime.now(timezone.utc) - timedelta(days=stale_days)):
        reason = f"{expectation.get('label')} verification is stale under the {stale_days}-day policy."
        blockers.append(_blocker("stale_verification", expectation, doc, reason, status="stale"))

    agent_execution = _latest_agent_execution(db, doc.get("id")) if require_agent_execution else None
    snapshot["agent_execution_id"] = agent_execution.get("id") if agent_execution else None
    snapshot["agent_execution_status"] = agent_execution.get("status") if agent_execution else None
    if require_agent_execution and not _agent_execution_satisfies(agent_execution):
        reason = f"{expectation.get('label')} is marked verified but lacks Agent 1 execution proof."
        blockers.append(_blocker("missing_agent_execution_proof", expectation, doc, reason, status=status))

    if blockers:
        snapshot.update({
            "reliance_state": blockers[0]["code"].replace("document_", "").replace("_proof", ""),
            "verification_method": "agent1" if agent_execution else None,
            "blocker_reasons": [blocker["reason"] for blocker in blockers],
        })
        return {"allowed": False, "snapshot": snapshot, "blockers": blockers}

    snapshot.update({
        "reliance_state": "verified",
        "verification_method": "agent1",
        "blocker_reasons": [],
    })
    return {"allowed": True, "snapshot": snapshot, "blockers": []}


def evaluate_document_reliance_gate(
    db: Any,
    app: Mapping[str, Any],
    *,
    stage: str,
    documents: Optional[Iterable[Mapping[str, Any]]] = None,
    require_agent_execution: bool = True,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> Dict[str, Any]:
    app_dict = _row_to_dict(app)
    app_id = app_dict.get("id")
    expectations = build_required_document_expectations(db, app_dict)
    docs = [dict(doc) for doc in documents] if documents is not None else _load_active_documents(db, app_id)
    docs_by_slot, docs_by_id = _index_documents(docs)
    rmi_slot_aliases = _apply_rmi_document_slot_aliases(
        db,
        app_dict,
        expectations,
        docs_by_slot,
        docs_by_id,
    )

    snapshots: List[Dict[str, Any]] = []
    blockers: List[Dict[str, Any]] = []
    satisfied = 0

    for expectation in expectations:
        doc = _select_expected_document(expectation, docs, docs_by_slot)
        result = _evaluate_document(
            db,
            expectation,
            doc,
            require_agent_execution=require_agent_execution,
            stale_days=stale_days,
        )
        snapshots.append(result["snapshot"])
        blockers.extend(result["blockers"])
        if result["allowed"]:
            satisfied += 1

    passed = not blockers
    gate = {
        "policy_version": POLICY_VERSION,
        "stage": stage,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reliance_status": "ready" if passed else "blocked",
        "passed": passed,
        "approval_ready": passed,
        "required_count": len(expectations),
        "satisfied_required_count": satisfied,
        "uploaded_count": len(docs),
        "blocker_count": len(blockers),
        "blockers": blockers,
        "documents": snapshots,
        "allowed_states": list(ALLOWED_RELIANCE_STATES),
        "blocked_states": list(BLOCKED_RELIANCE_STATES),
        "manual_acceptance_policy": {
            "roles": sorted(MANUAL_ACCEPTANCE_ROLES),
            "reason_required": True,
            "actor_required": True,
            "timestamp_required": True,
            "audit_required": True,
        },
        "rmi_slot_aliases": rmi_slot_aliases,
        "stale_days": stale_days,
        "require_agent_execution": require_agent_execution,
    }
    return _json_safe(gate)


def format_document_reliance_blockers(gate: Mapping[str, Any], *, limit: int = 6) -> str:
    blockers = list(gate.get("blockers") or [])
    if not blockers:
        return ""
    parts = []
    for blocker in blockers[:limit]:
        doc_id = blocker.get("document_id") or blocker.get("slot_key") or "missing"
        parts.append(f"{blocker.get('title') or blocker.get('doc_type')} (doc={doc_id}): {blocker.get('reason')}")
    if len(blockers) > limit:
        parts.append(f"{len(blockers) - limit} additional document blocker(s)")
    return "; ".join(parts)


def document_reliance_error_message(gate: Mapping[str, Any], *, action: str) -> str:
    detail = format_document_reliance_blockers(gate)
    prefix = f"Document evidence gate failed for {action}."
    return f"{prefix} {detail}" if detail else prefix


def document_reliance_blockers_for_approval(gate: Mapping[str, Any]) -> List[Dict[str, Any]]:
    blockers = []
    for blocker in gate.get("blockers") or []:
        mapped = dict(blocker)
        mapped.setdefault("source", "backend_approval_gate")
        mapped.setdefault("ctaLabel", "Resolve document evidence")
        mapped.setdefault("tab", "kyc-docs")
        mapped.setdefault("anchorId", "detail-kyc-documents-details")
        blockers.append(mapped)
    return blockers
