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


POLICY_VERSION = "document_reliance_gate_v1"
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
    return person.get("person_key") or person.get("id")


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
                "SELECT * FROM directors WHERE application_id=?",
                (app_id,),
            ).fetchall()]
        except Exception:
            directors = []
        try:
            ubos = [_row_to_dict(row) for row in db.execute(
                "SELECT * FROM ubos WHERE application_id=?",
                (app_id,),
            ).fetchall()]
        except Exception:
            ubos = []
        try:
            intermediaries = [_row_to_dict(row) for row in db.execute(
                "SELECT * FROM intermediaries WHERE application_id=?",
                (app_id,),
            ).fetchall()]
        except Exception:
            intermediaries = []

        for party_type, people in (("director", directors), ("ubo", ubos)):
            for person in people:
                person_id = _party_requirement_key(person)
                if not person_id:
                    continue
                owner = person.get("full_name") or person_id
                expectations.append({
                    "doc_type": "passport",
                    "label": f"Passport / Government ID for {owner}",
                    "person_id": person_id,
                    "person_type": party_type,
                })
                expectations.append({
                    "doc_type": "poa",
                    "label": f"Proof of Address for {owner}",
                    "person_id": person_id,
                    "person_type": party_type,
                })
        for person in intermediaries:
            person_id = _party_requirement_key(person)
            if not person_id:
                continue
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
        slot = doc_dict.get("slot_key") or document_slot_key(doc_dict.get("doc_type"), doc_dict.get("person_id"))
        by_slot[slot] = doc_dict
        legacy_slot = _legacy_person_slot_key(doc_dict.get("doc_type"), doc_dict.get("person_id"))
        if legacy_slot and legacy_slot not in by_slot:
            by_slot[legacy_slot] = doc_dict
        if doc_dict.get("id"):
            by_id[doc_dict.get("id")] = doc_dict
    return by_slot, by_id


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
    return {
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
    docs_by_slot, _docs_by_id = _index_documents(docs)

    snapshots: List[Dict[str, Any]] = []
    blockers: List[Dict[str, Any]] = []
    satisfied = 0

    for expectation in expectations:
        doc = docs_by_slot.get(expectation["slot_key"])
        if not doc:
            legacy_slot = _legacy_person_slot_key(expectation.get("doc_type"), expectation.get("person_id"))
            doc = docs_by_slot.get(legacy_slot) if legacy_slot else None
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
