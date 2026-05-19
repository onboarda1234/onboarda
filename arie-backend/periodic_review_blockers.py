from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

from environment import get_screening_validity_days

TERMINAL_EDD_STAGES = {"edd_approved", "edd_rejected"}
TERMINAL_ALERT_STATES = {"dismissed", "resolved", "routed_to_review", "routed_to_edd"}
RESOLVED_ITEM_STATUSES = {"cleared", "not_applicable"}
DOCUMENT_EVIDENCE_ITEM_TYPES = {
    "kyc_refresh",
    "ubo_confirmation",
    "document_expired",
    "document_expiring_soon",
    "document_stale",
    "document_expiry_missing",
    "licensing_refresh",
    "source_of_funds_refresh",
    "source_of_wealth_refresh",
}
OPERATIONAL_ITEM_TYPES = {
    "screening_refresh",
    "monitoring_alert_followup",
    "edd_followup",
}
HIGH_SEVERITY_ITEM_TYPES = DOCUMENT_EVIDENCE_ITEM_TYPES | OPERATIONAL_ITEM_TYPES
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


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


def _severity_rank(value: Any) -> int:
    return SEVERITY_RANK.get(str(value or "medium").strip().lower(), 2)


def decode_required_items(value: Any) -> List[Dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    return [dict(item) for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def load_evidence_links(db, review_id: int) -> List[Dict[str, Any]]:
    try:
        rows = db.execute(
            "SELECT id, requirement_id, document_id, link_type, linked_by, linked_at, note "
            "FROM periodic_review_evidence_links WHERE periodic_review_id = ? ORDER BY id ASC",
            (review_id,),
        ).fetchall()
    except Exception:
        return []
    return [dict(row) for row in rows]


def _linked_requirement_ids(evidence_links: Iterable[Dict[str, Any]]) -> Set[str]:
    return {
        str(link.get("requirement_id"))
        for link in evidence_links
        if link.get("requirement_id") is not None and str(link.get("requirement_id")).strip()
    }


def _blocker(item_type: str, label: str, *, severity: str = "high", source: str, source_id: Any = None, completion_only: bool) -> Dict[str, Any]:
    return {
        "item_type": item_type,
        "label": label,
        "severity": severity,
        "source": source,
        "source_id": source_id,
        "completion_only": completion_only,
    }


def _active_edd_exists(db, application_id: Any) -> bool:
    if not application_id:
        return False
    placeholders = ",".join("?" for _ in TERMINAL_EDD_STAGES)
    row = db.execute(
        f"SELECT id FROM edd_cases WHERE application_id = ? AND stage NOT IN ({placeholders}) ORDER BY id ASC LIMIT 1",
        (application_id, *sorted(TERMINAL_EDD_STAGES)),
    ).fetchone()
    return row is not None


def _parse_ts(value: Any):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _screening_blocker_for_application(db, application_id: Any):
    if not application_id:
        return _blocker(
            "screening_refresh",
            "Run screening refresh",
            source="applications",
            source_id=application_id,
            completion_only=False,
        )
    application = db.execute(
        "SELECT id, prescreening_data FROM applications WHERE id = ?",
        (application_id,),
    ).fetchone()
    prescreening_raw = _row_get(application, "prescreening_data") or "{}"
    try:
        prescreening = json.loads(prescreening_raw) if isinstance(prescreening_raw, str) else (prescreening_raw or {})
    except Exception:
        prescreening = {}
    screening_report = prescreening.get("screening_report") or {}
    if not screening_report:
        return _blocker(
            "screening_refresh",
            "Run screening refresh",
            source="applications",
            source_id=application_id,
            completion_only=False,
        )
    valid_until = _parse_ts(prescreening.get("screening_valid_until"))
    if valid_until is None:
        screened_at = _parse_ts(screening_report.get("screened_at") or screening_report.get("timestamp"))
        valid_until = screened_at + timedelta(days=get_screening_validity_days()) if screened_at is not None else None
    if valid_until is None or valid_until < datetime.now(timezone.utc):
        return _blocker(
            "screening_refresh",
            "Run screening refresh",
            source="applications",
            source_id=application_id,
            completion_only=False,
        )
    return None


def evaluate_operational_blockers(
    db,
    review,
    *,
    required_items: Optional[List[Dict[str, Any]]] = None,
    evidence_links: Optional[List[Dict[str, Any]]] = None,
    outcome: Optional[str] = None,
) -> List[Dict[str, Any]]:
    review_id = _row_get(review, "id")
    required_items = decode_required_items(required_items if required_items is not None else _row_get(review, "required_items"))
    evidence_links = load_evidence_links(db, review_id) if evidence_links is None else evidence_links
    blockers: List[Dict[str, Any]] = []

    if _row_get(review, "import_requires_ack") and not _row_get(review, "legacy_sco_acknowledged_at"):
        blockers.append(
            _blocker(
                "legacy_import_ack_required",
                "SCO/admin acknowledgement required for imported high-risk review",
                source="periodic_reviews",
                source_id=review_id,
                completion_only=False,
            )
        )

    linked_alert_id = _row_get(review, "linked_monitoring_alert_id")
    if linked_alert_id:
        alert = db.execute(
            "SELECT status, severity, summary FROM monitoring_alerts WHERE id = ?",
            (linked_alert_id,),
        ).fetchone()
        if alert is not None:
            alert_status = str(_row_get(alert, "status") or "").strip().lower()
            if alert_status not in TERMINAL_ALERT_STATES and _severity_rank(_row_get(alert, "severity")) >= _severity_rank("high"):
                blockers.append(
                    _blocker(
                        "monitoring_alert_followup",
                        _row_get(alert, "summary") or "High-severity linked monitoring alert unresolved",
                        severity=str(_row_get(alert, "severity") or "high").strip().lower(),
                        source="monitoring_alerts",
                        source_id=linked_alert_id,
                        completion_only=False,
                    )
                )

    linked_edd_case_id = _row_get(review, "linked_edd_case_id")
    if linked_edd_case_id:
        edd = db.execute(
            "SELECT stage FROM edd_cases WHERE id = ?",
            (linked_edd_case_id,),
        ).fetchone()
        if (
            edd is not None
            and str(_row_get(edd, "stage") or "").strip().lower() not in TERMINAL_EDD_STAGES
            and outcome != "edd_required"
        ):
            blockers.append(
                _blocker(
                    "active_linked_edd",
                    "Active linked EDD case is still open",
                    source="edd_cases",
                    source_id=linked_edd_case_id,
                    completion_only=False,
                )
            )

    linked_requirements = _linked_requirement_ids(evidence_links)
    application_id = _row_get(review, "application_id")
    has_screening_requirement = any(
        str(item.get("item_type") or item.get("code") or "").strip() == "screening_refresh"
        for item in required_items
    )
    if has_screening_requirement:
        screening_blocker = _screening_blocker_for_application(db, application_id)
        if screening_blocker is not None:
            blockers.append(screening_blocker)

    has_monitoring_requirement = any(
        str(item.get("item_type") or item.get("code") or "").strip() == "monitoring_alert_followup"
        for item in required_items
    )
    if has_monitoring_requirement and application_id:
        rows = db.execute(
            "SELECT id, severity, summary, status FROM monitoring_alerts WHERE application_id = ?",
            (application_id,),
        ).fetchall()
        for alert in rows:
            alert_status = str(_row_get(alert, "status") or "").strip().lower()
            if alert_status in TERMINAL_ALERT_STATES or _severity_rank(_row_get(alert, "severity")) < _severity_rank("high"):
                continue
            blockers.append(
                _blocker(
                    "monitoring_alert_followup",
                    _row_get(alert, "summary") or "High-severity monitoring alert unresolved",
                    severity=str(_row_get(alert, "severity") or "high").strip().lower(),
                    source="monitoring_alerts",
                    source_id=_row_get(alert, "id"),
                    completion_only=False,
                )
            )

    for item in required_items:
        status = str(item.get("status") or "open").strip().lower()
        if status in RESOLVED_ITEM_STATUSES:
            continue
        item_type = str(item.get("item_type") or item.get("code") or "").strip()
        severity = str(item.get("severity") or "medium").strip().lower()
        if item_type not in HIGH_SEVERITY_ITEM_TYPES or _severity_rank(severity) < _severity_rank("high"):
            continue
        if item_type in {"screening_refresh", "monitoring_alert_followup"}:
            continue
        if item_type == "edd_followup" and outcome == "edd_required":
            continue
        if item_type in DOCUMENT_EVIDENCE_ITEM_TYPES:
            requirement_id = str(item.get("id") or "").strip()
            if requirement_id and requirement_id in linked_requirements:
                continue
            blockers.append(
                _blocker(
                    item_type,
                    item.get("label") or "Required evidence is not linked",
                    severity=severity,
                    source="required_items",
                    source_id=item.get("id"),
                    completion_only=False,
                )
            )
            continue
        blockers.append(
            _blocker(
                item_type,
                item.get("label") or item_type or "Operational review blocker",
                severity=severity,
                source="required_items",
                source_id=item.get("id"),
                completion_only=False,
            )
        )

    return _dedupe(blockers)


def evaluate_completion_blockers(
    db,
    review,
    *,
    required_items: Optional[List[Dict[str, Any]]] = None,
    evidence_links: Optional[List[Dict[str, Any]]] = None,
    outcome: Optional[str] = None,
    outcome_reason: Optional[str] = None,
    include_completion_fields: bool = True,
) -> List[Dict[str, Any]]:
    # Signature mirrors evaluate_operational_blockers/evaluate_review_readiness
    # so both call sites can pass the same prepared inputs without branching.
    del required_items, evidence_links
    review_id = _row_get(review, "id")
    blockers: List[Dict[str, Any]] = []
    effective_outcome = str(outcome if outcome is not None else (_row_get(review, "outcome") or "")).strip()

    if include_completion_fields and not str(_row_get(review, "officer_rationale") or "").strip():
        blockers.append(
            _blocker(
                "officer_rationale_required",
                "Officer rationale is required",
                source="periodic_reviews",
                source_id=review_id,
                completion_only=True,
            )
        )
    if include_completion_fields and not effective_outcome:
        blockers.append(
            _blocker(
                "review_outcome_required",
                "Review outcome is required before completion",
                source="periodic_reviews",
                source_id=review_id,
                completion_only=True,
            )
        )
    if outcome is not None and not str(outcome_reason or "").strip():
        blockers.append(
            _blocker(
                "outcome_reason_required",
                "Outcome reason is required",
                source="periodic_reviews",
                source_id=review_id,
                completion_only=True,
            )
        )
    if effective_outcome == "edd_required" and not (
        _row_get(review, "linked_edd_case_id") or _active_edd_exists(db, _row_get(review, "application_id"))
    ):
        blockers.append(
            _blocker(
                "edd_case_required",
                "EDD outcome selected but no linked EDD case exists",
                source="edd_cases",
                source_id=_row_get(review, "application_id"),
                completion_only=True,
            )
        )
    return _dedupe(blockers)


def evaluate_review_readiness(
    db,
    review,
    *,
    required_items: Optional[List[Dict[str, Any]]] = None,
    evidence_links: Optional[List[Dict[str, Any]]] = None,
    outcome: Optional[str] = None,
    outcome_reason: Optional[str] = None,
    include_completion_fields: bool = True,
) -> Dict[str, Any]:
    required_items = decode_required_items(required_items if required_items is not None else _row_get(review, "required_items"))
    evidence_links = load_evidence_links(db, _row_get(review, "id")) if evidence_links is None else evidence_links
    operational = evaluate_operational_blockers(
        db,
        review,
        required_items=required_items,
        evidence_links=evidence_links,
        outcome=outcome,
    )
    completion = evaluate_completion_blockers(
        db,
        review,
        required_items=required_items,
        evidence_links=evidence_links,
        outcome=outcome,
        outcome_reason=outcome_reason,
        include_completion_fields=include_completion_fields,
    )
    all_completion = _dedupe([*operational, *completion])
    return {
        "required_items": required_items,
        "evidence_links": evidence_links,
        "operational_blockers": operational,
        "operational_blocker_count": len(operational),
        "operational_ready": not operational,
        "completion_blockers": completion,
        "completion_blocker_count": len(completion),
        "completion_ready": not all_completion,
        "blocking_items_for_completion": all_completion,
    }


def _dedupe(blockers: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for blocker in blockers:
        key = (
            blocker.get("item_type"),
            blocker.get("label"),
            blocker.get("severity"),
            blocker.get("source"),
            blocker.get("source_id"),
            blocker.get("completion_only"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(blocker))
    return deduped
