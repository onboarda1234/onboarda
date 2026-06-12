"""Officer-facing Sumsub identity-verification status projection.

KYC-1A keeps Sumsub identity verification separate from AML/PEP/sanctions
screening.  This module derives a per-person read-only projection from
existing durable sources; it does not call Sumsub or any live provider.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


PROVIDER = "sumsub"
PROVIDER_LABEL = "Sumsub Identity Verification"
PROVIDER_SCOPE = "individual_kyc_identity_verification"

VALID_STATUSES = {
    "not_started",
    "applicant_created",
    "pending",
    "approved",
    "rejected",
    "failed",
    "unmatched",
    "unavailable",
}


def _row_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _safe_json(value: Any, default: Any = None) -> Any:
    if default is None:
        default = {}
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _fetchall_optional(db: Any, query: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    """Read optional Sumsub projection sources without breaking legacy fixtures."""
    try:
        rows = db.execute(query, params).fetchall()
    except (sqlite3.Error, RuntimeError, AttributeError):
        return []
    return [_row_dict(row) for row in rows]


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _person_key(person_type: str, person_id: str, person_name: str) -> str:
    return "|".join([_norm(person_type), _norm(person_id), _norm(person_name)])


def _mask_applicant_id(applicant_id: Any) -> str:
    value = str(applicant_id or "").strip()
    if not value:
        return ""
    if len(value) <= 10:
        return value[:3] + "..." + value[-2:]
    return value[:6] + "..." + value[-4:]


def _status_label(status: str) -> str:
    return {
        "not_started": "Not Started",
        "applicant_created": "Applicant Created",
        "pending": "Pending Verification",
        "approved": "Verified",
        "rejected": "Rejected",
        "failed": "Failed",
        "unmatched": "Unmatched",
        "unavailable": "Unavailable",
    }.get(status, "Unavailable")


def _review_answer(value: Any) -> str:
    answer = str(value or "").strip().upper()
    if answer in {"GREEN", "RED"}:
        return answer
    if answer in {"PENDING", "YELLOW"}:
        return "pending"
    return "unavailable"


def _status_from_review_answer(answer: str) -> str:
    if answer == "GREEN":
        return "approved"
    if answer == "RED":
        return "rejected"
    if answer == "pending":
        return "pending"
    return ""


def _is_failure_text(value: Any) -> bool:
    text = _norm(value)
    return any(token in text for token in ("fail", "error", "rejected", "unavailable"))


def _status_payload(
    *,
    application_id: str,
    application_ref: str = "",
    person_id: str = "",
    person_type: str,
    person_name: str,
    applicant_id: str = "",
    verification_status: str,
    review_answer: str = "unavailable",
    rejection_labels: Optional[List[str]] = None,
    last_provider_event_at: str = "",
    applicant_created_at: str = "",
    webhook_received_at: str = "",
    evidence_backed: bool = False,
    source_of_truth: str,
    officer_action_required: bool = False,
    blocking_flags: Optional[List[str]] = None,
    warning_flags: Optional[List[str]] = None,
    audit_refs: Optional[List[Dict[str, Any]]] = None,
    external_user_id: str = "",
) -> Dict[str, Any]:
    if verification_status not in VALID_STATUSES:
        verification_status = "unavailable"
    blocking_flags = list(blocking_flags or [])
    warning_flags = list(warning_flags or [])
    rejection_labels = list(rejection_labels or [])
    if verification_status in {"not_started", "pending", "rejected", "failed", "unmatched", "unavailable"}:
        officer_action_required = True
    if verification_status == "not_started" and "sumsub_idv_not_started" not in warning_flags:
        warning_flags.append("sumsub_idv_not_started")
    if verification_status == "rejected" and "sumsub_idv_rejected" not in blocking_flags:
        blocking_flags.append("sumsub_idv_rejected")
    if verification_status == "failed" and "sumsub_idv_failed" not in blocking_flags:
        blocking_flags.append("sumsub_idv_failed")
    if verification_status == "unmatched" and "sumsub_idv_unmatched_webhook" not in warning_flags:
        warning_flags.append("sumsub_idv_unmatched_webhook")
    return {
        "application_id": application_id,
        "application_ref": application_ref,
        "person_id": person_id,
        "person_type": person_type or "unknown",
        "person_name": person_name or "Unknown person",
        "provider": PROVIDER,
        "provider_label": PROVIDER_LABEL,
        "provider_scope": PROVIDER_SCOPE,
        "applicant_id": _mask_applicant_id(applicant_id),
        "applicant_id_present": bool(applicant_id),
        "external_user_id": external_user_id,
        "verification_status": verification_status,
        "review_answer": review_answer,
        "rejection_labels": rejection_labels,
        "last_provider_event_at": last_provider_event_at,
        "applicant_created_at": applicant_created_at,
        "webhook_received_at": webhook_received_at,
        "evidence_backed": bool(evidence_backed),
        "source_of_truth": source_of_truth,
        "officer_action_required": bool(officer_action_required),
        "officer_label": _status_label(verification_status),
        "blocking_flags": blocking_flags,
        "warning_flags": warning_flags,
        "audit_refs": list(audit_refs or []),
        "raw_provider_payload_exposed": False,
    }


def _person_from_row(row: Mapping[str, Any], person_type: str) -> Dict[str, Any]:
    name = row.get("full_name") or row.get("entity_name") or row.get("company_name") or row.get("email") or ""
    return {
        "person_id": str(row.get("id") or row.get("person_key") or ""),
        "person_key": str(row.get("person_key") or row.get("id") or ""),
        "person_type": person_type,
        "person_name": str(name or ""),
        "date_of_birth": str(row.get("date_of_birth") or ""),
        "email": str(row.get("email") or ""),
    }


def _collect_people(
    application: Mapping[str, Any],
    directors: Iterable[Mapping[str, Any]],
    ubos: Iterable[Mapping[str, Any]],
    intermediaries: Iterable[Mapping[str, Any]],
    client: Optional[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    people: List[Dict[str, Any]] = []
    seen = set()

    def add(person: Dict[str, Any]) -> None:
        key = _person_key(person.get("person_type", ""), person.get("person_id", ""), person.get("person_name", ""))
        if key in seen:
            return
        seen.add(key)
        people.append(person)

    for row in directors or []:
        add(_person_from_row(_row_dict(row), "director"))
    for row in ubos or []:
        add(_person_from_row(_row_dict(row), "ubo"))
    for row in intermediaries or []:
        add(_person_from_row(_row_dict(row), "intermediary"))
    if client:
        c = _row_dict(client)
        add({
            "person_id": str(c.get("id") or application.get("client_id") or ""),
            "person_key": str(c.get("id") or application.get("client_id") or ""),
            "person_type": "client",
            "person_name": str(c.get("company_name") or c.get("email") or application.get("company_name") or "Client"),
            "date_of_birth": "",
            "email": str(c.get("email") or ""),
        })
    return people


def _match_mapping(person: Mapping[str, Any], mappings: List[Dict[str, Any]], applicant_ids: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    ptype = _norm(person.get("person_type"))
    pname = _norm(person.get("person_name"))
    pid = _norm(person.get("person_id"))
    pkey = _norm(person.get("person_key"))
    for mapping in mappings:
        mtype = _norm(mapping.get("person_type"))
        mname = _norm(mapping.get("person_name"))
        ext = _norm(mapping.get("external_user_id"))
        if mtype and mtype == ptype and mname and pname and mname == pname:
            return mapping
        if ext and ext in {pid, pkey}:
            return mapping
        if mname and pname and mname == pname:
            return mapping
    for ext, applicant_id in (applicant_ids or {}).items():
        ext_norm = _norm(ext)
        if ext_norm and ext_norm in {pid, pkey}:
            return {
                "application_id": "",
                "applicant_id": applicant_id,
                "external_user_id": ext,
                "person_name": person.get("person_name", ""),
                "person_type": person.get("person_type", ""),
                "created_at": "",
                "_legacy_prescreening": True,
            }
    return None


def _audit_refs_for_applicant(audits: List[Dict[str, Any]], applicant_id: str, external_user_id: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    latest_payload: Dict[str, Any] = {}
    needle_values = {_norm(applicant_id), _norm(external_user_id)}
    for audit in audits:
        target = _norm(audit.get("target"))
        detail_text = str(audit.get("detail") or "")
        if target not in needle_values and not any(v and v in _norm(detail_text) for v in needle_values):
            continue
        refs.append({
            "action": audit.get("action", ""),
            "target": _mask_applicant_id(audit.get("target", "")),
            "created_at": str(audit.get("created_at") or ""),
        })
        detail = _safe_json(detail_text, {})
        if isinstance(detail, dict) and detail.get("review_answer"):
            latest_payload = detail
    return refs[:10], latest_payload


def _event_for_applicant(events: List[Dict[str, Any]], applicant_id: str, external_user_id: str) -> Optional[Dict[str, Any]]:
    for event in events:
        if str(event.get("applicant_id") or "") == str(applicant_id or ""):
            return event
        if external_user_id and str(event.get("external_user_id") or "") == str(external_user_id):
            return event
    return None


def _legacy_webhook_for_mapping(webhook: Mapping[str, Any], applicant_id: str, external_user_id: str) -> Dict[str, Any]:
    if not isinstance(webhook, Mapping):
        return {}
    if applicant_id and str(webhook.get("sumsub_applicant_id") or "") == str(applicant_id):
        return dict(webhook)
    if external_user_id and str(webhook.get("external_user_id") or "") == str(external_user_id):
        return dict(webhook)
    if not applicant_id and not external_user_id and webhook.get("review_answer"):
        return dict(webhook)
    return {}


def _status_for_mapping(
    application_id: str,
    application_ref: str,
    person: Mapping[str, Any],
    mapping: Mapping[str, Any],
    events: List[Dict[str, Any]],
    audits: List[Dict[str, Any]],
    legacy_webhook: Mapping[str, Any],
) -> Dict[str, Any]:
    applicant_id = str(mapping.get("applicant_id") or "")
    external_user_id = str(mapping.get("external_user_id") or "")
    event = _event_for_applicant(events, applicant_id, external_user_id) or {}
    audit_refs, audit_payload = _audit_refs_for_applicant(audits, applicant_id, external_user_id)
    legacy_payload = _legacy_webhook_for_mapping(legacy_webhook, applicant_id, external_user_id)
    payload = audit_payload or legacy_payload or {}
    review_answer = _review_answer(event.get("review_answer") or payload.get("review_answer"))
    status = _status_from_review_answer(review_answer)
    source = "webhook_processed_events" if event else ""
    evidence_backed = bool(event or audit_payload or legacy_payload)
    if not status:
        if _is_failure_text(payload.get("api_status") or payload.get("status") or payload.get("error")):
            status = "failed"
            source = source or "prescreening_data"
        elif applicant_id:
            status = "applicant_created" if not evidence_backed else "pending"
            review_answer = "pending"
            source = source or ("prescreening_data" if mapping.get("_legacy_prescreening") else "sumsub_applicant_mappings")
        else:
            status = "unavailable"
            source = "derived"
    elif not source:
        source = "audit_log" if audit_payload else "prescreening_data"
    return _status_payload(
        application_id=application_id,
        application_ref=application_ref,
        person_id=str(person.get("person_id") or ""),
        person_type=str(person.get("person_type") or "unknown"),
        person_name=str(person.get("person_name") or mapping.get("person_name") or ""),
        applicant_id=applicant_id,
        verification_status=status,
        review_answer=review_answer,
        rejection_labels=payload.get("rejection_labels") if isinstance(payload.get("rejection_labels"), list) else [],
        last_provider_event_at=str(event.get("received_at") or payload.get("received_at") or ""),
        applicant_created_at=str(mapping.get("created_at") or ""),
        webhook_received_at=str(event.get("received_at") or payload.get("received_at") or ""),
        evidence_backed=evidence_backed,
        source_of_truth=source,
        officer_action_required=status != "approved",
        audit_refs=audit_refs,
        external_user_id=external_user_id,
    )


def _unmatched_summary(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    items = []
    for row in rows or []:
        r = _row_dict(row)
        items.append(_status_payload(
            application_id="",
            person_id="",
            person_type="unknown",
            person_name="Unmatched Sumsub webhook",
            applicant_id=str(r.get("applicant_id") or ""),
            verification_status="unmatched",
            review_answer=_review_answer(r.get("review_answer")),
            last_provider_event_at=str(r.get("received_at") or ""),
            webhook_received_at=str(r.get("received_at") or ""),
            evidence_backed=True,
            source_of_truth="sumsub_unmatched_webhooks",
            officer_action_required=True,
            warning_flags=["sumsub_idv_unmatched_webhook"],
            external_user_id=str(r.get("external_user_id") or ""),
        ))
    return {"count": len(items), "items": items}


def build_sumsub_idv_statuses(
    db: Any,
    application: Mapping[str, Any],
    *,
    directors: Iterable[Mapping[str, Any]] = (),
    ubos: Iterable[Mapping[str, Any]] = (),
    intermediaries: Iterable[Mapping[str, Any]] = (),
    client: Optional[Mapping[str, Any]] = None,
    include_unmatched: bool = False,
) -> Dict[str, Any]:
    """Build read-only per-person Sumsub IDV statuses for one application."""

    app = _row_dict(application)
    app_id = str(app.get("id") or "")
    app_ref = str(app.get("ref") or "")
    prescreening = _safe_json(app.get("prescreening_data"), {})
    screening_report = prescreening.get("screening_report") if isinstance(prescreening, dict) else {}
    if not isinstance(screening_report, dict):
        screening_report = {}
    applicant_ids = prescreening.get("sumsub_applicant_ids") if isinstance(prescreening, dict) else {}
    if not isinstance(applicant_ids, dict):
        applicant_ids = {}

    mappings = _fetchall_optional(
        db,
        "SELECT applicant_id, external_user_id, person_name, person_type, created_at "
        "FROM sumsub_applicant_mappings WHERE application_id=?",
        (app_id,),
    )

    events = _fetchall_optional(
        db,
        "SELECT event_type, applicant_id, external_user_id, review_answer, received_at "
        "FROM webhook_processed_events "
        "WHERE applicant_id IN (SELECT applicant_id FROM sumsub_applicant_mappings WHERE application_id=?) "
        "OR external_user_id IN (SELECT external_user_id FROM sumsub_applicant_mappings WHERE application_id=?) "
        "ORDER BY received_at DESC",
        (app_id, app_id),
    )

    audits = _fetchall_optional(
        db,
        "SELECT action, target, detail, created_at FROM audit_log "
        "WHERE action IN ('KYC Applicant Created', 'KYC Applicant Creation Failed') "
        "OR action LIKE 'KYC applicantReviewed:%' "
        "ORDER BY created_at DESC LIMIT 200"
    )

    people = _collect_people(app, directors, ubos, intermediaries, client)
    statuses: List[Dict[str, Any]] = []
    matched_mapping_ids = set()
    legacy_webhook = screening_report.get("sumsub_webhook") if isinstance(screening_report, dict) else {}

    for person in people:
        mapping = _match_mapping(person, mappings, applicant_ids)
        if mapping:
            matched_mapping_ids.add(str(mapping.get("applicant_id") or ""))
            statuses.append(_status_for_mapping(app_id, app_ref, person, mapping, events, audits, legacy_webhook))
        else:
            statuses.append(_status_payload(
                application_id=app_id,
                application_ref=app_ref,
                person_id=str(person.get("person_id") or ""),
                person_type=str(person.get("person_type") or "unknown"),
                person_name=str(person.get("person_name") or ""),
                verification_status="not_started",
                review_answer="unavailable",
                evidence_backed=False,
                source_of_truth="derived",
                officer_action_required=True,
            ))

    # Surface mapping rows that cannot be reconciled to current parties.
    for mapping in mappings:
        applicant_id = str(mapping.get("applicant_id") or "")
        if applicant_id in matched_mapping_ids:
            continue
        person = {
            "person_id": str(mapping.get("external_user_id") or ""),
            "person_type": str(mapping.get("person_type") or "unknown"),
            "person_name": str(mapping.get("person_name") or "Unmatched mapped person"),
        }
        statuses.append(_status_for_mapping(app_id, app_ref, person, mapping, events, audits, legacy_webhook))

    unmatched = {"count": 0, "items": []}
    if include_unmatched:
        unmatched_rows = _fetchall_optional(
            db,
            "SELECT applicant_id, external_user_id, event_type, review_answer, status, received_at "
            "FROM sumsub_unmatched_webhooks WHERE status='pending' ORDER BY received_at DESC LIMIT 25"
        )
        unmatched = _unmatched_summary(unmatched_rows)

    counts: Dict[str, int] = {}
    action_required = 0
    for status in statuses:
        key = status.get("verification_status", "unavailable")
        counts[key] = counts.get(key, 0) + 1
        if status.get("officer_action_required"):
            action_required += 1

    return {
        "provider": PROVIDER,
        "provider_label": PROVIDER_LABEL,
        "provider_scope": PROVIDER_SCOPE,
        "application_id": app_id,
        "application_ref": app_ref,
        "statuses": statuses,
        "summary": {
            "total": len(statuses),
            "status_counts": counts,
            "officer_action_required_count": action_required,
            "unmatched_webhook_count": unmatched.get("count", 0),
        },
        "unmatched_webhooks": unmatched,
        "raw_provider_payload_exposed": False,
    }
