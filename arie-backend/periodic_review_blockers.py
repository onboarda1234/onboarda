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
    "custom_evidence_requirement",
}
OPERATIONAL_ITEM_TYPES = {
    "screening_refresh",
    "monitoring_alert_followup",
    "edd_followup",
}
HIGH_SEVERITY_ITEM_TYPES = DOCUMENT_EVIDENCE_ITEM_TYPES | OPERATIONAL_ITEM_TYPES
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
SENIOR_DOCUMENT_REVIEW_ROLES = {"admin", "sco"}
BASELINE_READY_STATUSES = {
    "last_onboarding_date",
    "last_periodic_review_date",
    "imported_legacy_review",
    "not_applicable",
}
ATTESTATION_NOT_REQUIRED_STATUSES = {"not_required", "not_applicable", "waived"}
DOCUMENT_REQUEST_TERMINAL_STATUSES = {"accepted", "waived", "cancelled"}
# PR-PRS-B (P0-EV1): a deliberate waiver/cancellation is a non-evidence
# disposition and remains terminal; officer "accepted" is NOT a free pass and
# must be backed by a verified or senior-accepted-with-reason document.
DOCUMENT_REQUEST_WAIVED_STATUSES = {"waived", "cancelled"}
# PR-PRS-B (P1-A2): verification states that must never be senior-overridden at
# completion -- a hard failure, or still in progress.
NON_OVERRIDABLE_VERIFICATION_STATES = {
    "failed",
    "pending",
    "running",
    "processing",
    "queued",
}


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
            "SELECT l.id, l.requirement_id, l.document_id, l.link_type, l.linked_by, l.linked_at, l.note, "
            "d.doc_type AS document_type, d.doc_name AS document_name, d.verification_status AS document_verification_status, "
            "d.review_status AS document_review_status, d.review_comment AS document_review_comment, "
            "d.reviewer_role AS document_reviewer_role, d.reviewed_at AS document_reviewed_at, "
            "d.verified_at AS document_verified_at, d.is_current AS document_is_current "
            "FROM periodic_review_evidence_links l "
            "LEFT JOIN documents d ON d.id = l.document_id "
            "WHERE l.periodic_review_id = ? ORDER BY l.id ASC",
            (review_id,),
        ).fetchall()
    except Exception:
        return []
    return [dict(row) for row in rows]


def _document_is_current(link: Dict[str, Any]) -> bool:
    value = link.get("document_is_current")
    if value in (None, ""):
        return True
    return str(value).strip().lower() not in {"0", "false", "no"}


def evidence_link_satisfies_requirement(link: Dict[str, Any]) -> bool:
    """Return whether a linked review evidence document is truly usable."""
    if not link or not _document_is_current(link):
        return False
    verification_status = str(link.get("document_verification_status") or "").strip().lower()
    if verification_status == "verified":
        return True
    review_status = str(link.get("document_review_status") or "").strip().lower()
    reviewer_role = str(link.get("document_reviewer_role") or "").strip().lower()
    review_comment = str(link.get("document_review_comment") or "").strip()
    return (
        verification_status == "flagged"
        and review_status == "accepted"
        and reviewer_role in SENIOR_DOCUMENT_REVIEW_ROLES
        and bool(review_comment)
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _document_request_ready(row: Dict[str, Any]) -> bool:
    status = str(row.get("status") or "").strip().lower()
    # Deliberate waiver / cancellation stays terminal.
    if status in DOCUMENT_REQUEST_WAIVED_STATUSES:
        return True
    if _truthy(row.get("workflow_test_accepted")):
        return True
    # PR-PRS-B (P0-EV1): officer "accepted" no longer satisfies completion on its
    # own. Evidence truth is decided by the linked document, separately from the
    # requirement's officer-set status.
    if not str(row.get("linked_document_id") or "").strip():
        return False
    # PR-PRS-B (P1-EV3): a stale / superseded document does not satisfy completion.
    if not _document_is_current(row):
        return False
    verification_status = str(row.get("document_verification_status") or "").strip().lower()
    if verification_status == "verified":
        return True
    # PR-PRS-B (P1-A2): controlled senior/manual exception. A senior reviewer
    # (admin/sco) must have accepted the document WITH a comment, and the document
    # must not be in a hard-failed or still-processing verification state. This
    # covers both Agent 1 "flagged" documents and manual-only document types, while
    # a plain officer ("co") acceptance never satisfies completion.
    review_status = str(row.get("document_review_status") or "").strip().lower()
    reviewer_role = str(row.get("document_reviewer_role") or "").strip().lower()
    review_comment = str(row.get("document_review_comment") or "").strip()
    return (
        review_status in {"accepted", "approved"}
        and reviewer_role in SENIOR_DOCUMENT_REVIEW_ROLES
        and bool(review_comment)
        and verification_status not in NON_OVERRIDABLE_VERIFICATION_STATES
    )


def _periodic_review_document_request_blockers(db, review) -> List[Dict[str, Any]]:
    review_id = _row_get(review, "id")
    application_id = _row_get(review, "application_id")
    if not review_id or not application_id:
        return []
    try:
        rows = db.execute(
            """
            SELECT aer.id,
                   aer.requirement_label,
                   aer.requirement_key,
                   aer.mandatory,
                   aer.status,
                   aer.linked_document_id,
                   aer.workflow_test_accepted,
                   d.verification_status AS document_verification_status,
                   d.review_status AS document_review_status,
                   d.reviewer_role AS document_reviewer_role,
                   d.review_comment AS document_review_comment,
                   d.is_current AS document_is_current
            FROM application_enhanced_requirements aer
            LEFT JOIN documents d ON d.id = aer.linked_document_id
            WHERE aer.application_id = ?
              AND aer.linked_periodic_review_id = ?
              AND aer.active = 1
              AND aer.requirement_type = 'document'
            ORDER BY aer.id ASC
            """,
            (application_id, review_id),
        ).fetchall()
    except Exception:
        return []
    blockers: List[Dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        if not _truthy(row.get("mandatory")):
            continue
        if _document_request_ready(row):
            continue
        label = row.get("requirement_label") or row.get("requirement_key") or "Required periodic review document"
        reason = "is still missing" if not row.get("linked_document_id") else "still requires officer review"
        blockers.append(
            _blocker(
                "periodic_review_document_required",
                f"{label} {reason}",
                source="application_enhanced_requirements",
                source_id=row.get("id"),
                completion_only=False,
            )
        )
    return blockers


def _periodic_review_attestation_blocker(review) -> Optional[Dict[str, Any]]:
    review_id = _row_get(review, "id")
    status = str(_row_get(review, "client_attestation_status") or "not_started").strip().lower()
    if status == "submitted" or status in ATTESTATION_NOT_REQUIRED_STATUSES:
        return None
    return _blocker(
        "client_attestation_required",
        "Client attestation has not been submitted",
        source="periodic_reviews",
        source_id=review_id,
        completion_only=False,
    )


def _periodic_review_baseline_blocker(db, review) -> Optional[Dict[str, Any]]:
    review_id = _row_get(review, "id")
    application_id = _row_get(review, "application_id")
    review_status = str(_row_get(review, "baseline_status") or "").strip().lower()
    app_status = ""
    if application_id:
        try:
            app = db.execute(
                "SELECT periodic_review_baseline_status FROM applications WHERE id = ?",
                (application_id,),
            ).fetchone()
            app_status = str(_row_get(app, "periodic_review_baseline_status") or "").strip().lower()
        except Exception:
            app_status = ""
    status = review_status or app_status
    if status in BASELINE_READY_STATUSES:
        return None
    return _blocker(
        "periodic_review_baseline_required",
        "Periodic review baseline is missing or not marked N/A",
        source="periodic_reviews",
        source_id=review_id,
        completion_only=False,
    )


def _satisfied_requirement_ids(evidence_links: Iterable[Dict[str, Any]]) -> Set[str]:
    return {
        str(link.get("requirement_id"))
        for link in evidence_links
        if link.get("requirement_id") is not None
        and str(link.get("requirement_id")).strip()
        and evidence_link_satisfies_requirement(link)
    }


def _has_linked_requirement(evidence_links: Iterable[Dict[str, Any]], requirement_id: str) -> bool:
    return any(
        str(link.get("requirement_id") or "").strip() == requirement_id
        for link in evidence_links
    )


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
    include_periodic_review_closure_gates: bool = True,
) -> List[Dict[str, Any]]:
    review_id = _row_get(review, "id")
    required_items = decode_required_items(required_items if required_items is not None else _row_get(review, "required_items"))
    evidence_links = load_evidence_links(db, review_id) if evidence_links is None else evidence_links
    blockers: List[Dict[str, Any]] = []

    if include_periodic_review_closure_gates:
        attestation_blocker = _periodic_review_attestation_blocker(review)
        if attestation_blocker is not None:
            blockers.append(attestation_blocker)
        baseline_blocker = _periodic_review_baseline_blocker(db, review)
        if baseline_blocker is not None:
            blockers.append(baseline_blocker)
        blockers.extend(_periodic_review_document_request_blockers(db, review))

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

    satisfied_requirements = _satisfied_requirement_ids(evidence_links)
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
            if requirement_id and requirement_id in satisfied_requirements:
                continue
            linked_but_not_ready = bool(requirement_id and _has_linked_requirement(evidence_links, requirement_id))
            blockers.append(
                _blocker(
                    item_type,
                    (
                        f"{item.get('label') or 'Required evidence'} is linked but not yet verified or senior-accepted"
                        if linked_but_not_ready
                        else item.get("label") or "Required evidence is not linked"
                    ),
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
    include_periodic_review_closure_gates: bool = True,
) -> Dict[str, Any]:
    required_items = decode_required_items(required_items if required_items is not None else _row_get(review, "required_items"))
    evidence_links = load_evidence_links(db, _row_get(review, "id")) if evidence_links is None else evidence_links
    operational = evaluate_operational_blockers(
        db,
        review,
        required_items=required_items,
        evidence_links=evidence_links,
        outcome=outcome,
        include_periodic_review_closure_gates=include_periodic_review_closure_gates,
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
