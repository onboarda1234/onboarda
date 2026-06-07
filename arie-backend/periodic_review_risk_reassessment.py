"""PRS-7 periodic-review risk reassessment helpers.

This module keeps risk reassessment inside the canonical
``periodic_reviews`` shell. It never mutates application risk ratings and
does not call external screening, memo, or AI services.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "VERY_HIGH")
RISK_LEVEL_RANK = {level: idx for idx, level in enumerate(RISK_LEVELS)}

IMPACT_NO_RISK = "no_risk_impact_identified"
IMPACT_PROFILE_UPDATE = "profile_update_only"
IMPACT_INCREASE = "potential_risk_increase"
IMPACT_DECREASE = "potential_risk_decrease"
IMPACT_SENIOR_REVIEW = "senior_review_required"
IMPACT_ESCALATION = "escalation_recommended"

VALID_RISK_IMPACT_CATEGORIES = {
    IMPACT_NO_RISK,
    IMPACT_PROFILE_UPDATE,
    IMPACT_INCREASE,
    IMPACT_DECREASE,
    IMPACT_SENIOR_REVIEW,
    IMPACT_ESCALATION,
}

DECISION_KEEP = "keep_current_risk_rating"
DECISION_INCREASE = "increase_risk_rating"
DECISION_DECREASE = "decrease_risk_rating"
DECISION_SENIOR_REVIEW = "senior_review_required_before_risk_change"
DECISION_ESCALATE = "escalate_for_separate_review"

VALID_RISK_DECISIONS = {
    DECISION_KEEP,
    DECISION_INCREASE,
    DECISION_DECREASE,
    DECISION_SENIOR_REVIEW,
    DECISION_ESCALATE,
}

STATUS_NOT_STARTED = "not_started"
STATUS_CONFIRMED = "confirmed"

ADDENDUM_NOT_GENERATED = "not_generated"
ADDENDUM_DRAFT = "draft_generated"
ADDENDUM_FINALIZED = "finalized"
ADDENDUM_FAILED = "failed"

SOURCE_SURFACE = "backoffice_periodic_review_risk_reassessment"


class RiskReassessmentError(ValueError):
    """Validation or persistence error for PRS-7 reassessment."""


class ReviewNotFound(RiskReassessmentError):
    """Periodic review does not exist."""


def _row_get(row, key, default=None):
    if row is None:
        return default
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        try:
            value = getattr(row, key)
        except AttributeError:
            return default
    return value if value is not None else default


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _json_value(raw, default):
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_risk_level(value: Any) -> Optional[str]:
    text = str(value or "").strip().upper().replace(" ", "_")
    return text if text in RISK_LEVEL_RANK else None


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _fetch_review(db, review_id: int):
    row = db.execute("SELECT * FROM periodic_reviews WHERE id = ?", (review_id,)).fetchone()
    if row is None:
        raise ReviewNotFound(f"periodic_review id={review_id} not found")
    return row


def _fetch_application(db, application_id):
    if not application_id:
        return None
    return db.execute(
        "SELECT id, ref, company_name, risk_level, final_risk_level, risk_score "
        "FROM applications WHERE id = ?",
        (application_id,),
    ).fetchone()


def _fetch_document_requests(db, review_id: int) -> List[Dict[str, Any]]:
    try:
        rows = db.execute(
            "SELECT id, requirement_key, requirement_label, status, mandatory, "
            "       linked_document_id, uploaded_at, reviewed_at "
            "FROM application_enhanced_requirements "
            "WHERE linked_periodic_review_id = ? AND active = 1 "
            "ORDER BY id ASC",
            (review_id,),
        ).fetchall()
    except Exception:
        return []
    return [dict(row) for row in rows]


def _fetch_linked_alerts(db, review) -> List[Dict[str, Any]]:
    application_id = _row_get(review, "application_id")
    linked_alert_id = _row_get(review, "linked_monitoring_alert_id")
    rows = []
    try:
        if linked_alert_id:
            rows = db.execute(
                "SELECT id, alert_type, severity, status, summary, created_at, resolved_at "
                "FROM monitoring_alerts WHERE id = ?",
                (linked_alert_id,),
            ).fetchall()
        elif application_id:
            rows = db.execute(
                "SELECT id, alert_type, severity, status, summary, created_at, resolved_at "
                "FROM monitoring_alerts WHERE application_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 10",
                (application_id,),
            ).fetchall()
    except Exception:
        rows = []
    return [dict(row) for row in rows]


def _latest_memo_row(db, review_id: int):
    try:
        return db.execute(
            "SELECT id, version, status, generated_at, generated_by "
            "FROM periodic_review_memos WHERE periodic_review_id = ? "
            "ORDER BY version DESC LIMIT 1",
            (review_id,),
        ).fetchone()
    except Exception:
        return None


def memo_addendum_status(review, latest_memo=None) -> str:
    explicit = _clean_text(_row_get(review, "memo_addendum_status")).lower()
    latest_status = _clean_text(_row_get(latest_memo, "status")).lower()
    if explicit == ADDENDUM_FINALIZED or latest_status == ADDENDUM_FINALIZED:
        return ADDENDUM_FINALIZED
    if explicit == ADDENDUM_FAILED or latest_status == "generation_failed":
        return ADDENDUM_FAILED
    if explicit == ADDENDUM_DRAFT or latest_status in {"generated", ADDENDUM_DRAFT}:
        return ADDENDUM_DRAFT
    return ADDENDUM_NOT_GENERATED


def _attestation_summary(review) -> Dict[str, Any]:
    payload = _json_value(_row_get(review, "client_attestation_payload"), {})
    questions = payload.get("questions") if isinstance(payload, dict) else None
    answers = payload.get("answers") if isinstance(payload, dict) else None
    material_keys = []
    comments = []
    source = questions if isinstance(questions, list) else []
    for item in source:
        if not isinstance(item, dict):
            continue
        key = item.get("key") or item.get("question_key")
        answer = str(item.get("answer") or "").strip().lower()
        comment = _clean_text(item.get("comment"))
        if answer in {"yes", "no"} and key:
            is_material = bool(item.get("is_material_change")) or (
                answer == "yes" and key != "company_contact_details_correct"
            ) or (answer == "no" and key == "company_contact_details_correct")
            if is_material:
                material_keys.append(key)
                if comment:
                    comments.append({"key": key, "comment": comment})
    if isinstance(answers, dict):
        for key, value in answers.items():
            if not isinstance(value, dict):
                continue
            answer = str(value.get("answer") or "").strip().lower()
            comment = _clean_text(value.get("comment"))
            is_material = (
                answer == "yes" and key != "company_contact_details_correct"
            ) or (answer == "no" and key == "company_contact_details_correct")
            if is_material and key not in material_keys:
                material_keys.append(key)
                if comment:
                    comments.append({"key": key, "comment": comment})
    categories = _json_value(_row_get(review, "material_change_categories"), [])
    if isinstance(categories, list):
        for key in categories:
            if key and str(key) not in material_keys:
                material_keys.append(str(key))
    return {
        "status": _row_get(review, "client_attestation_status") or "not_started",
        "submitted_at": _row_get(review, "client_attestation_submitted_at"),
        "material_change_keys": material_keys,
        "material_change_count": len(material_keys),
        "material_change_comments": comments,
    }


def derive_suggested_risk_impact(db, review, application=None) -> Dict[str, Any]:
    current = canonical_risk_level(
        _row_get(application, "final_risk_level")
        or _row_get(application, "risk_level")
        or _row_get(review, "risk_level")
    )
    proposed = canonical_risk_level(_row_get(review, "new_risk_level"))
    attestation = _attestation_summary(review)
    requests = _fetch_document_requests(db, _row_get(review, "id"))
    alerts = _fetch_linked_alerts(db, review)
    high_alerts = [
        alert for alert in alerts
        if str(alert.get("severity") or "").strip().lower() in {"high", "critical"}
        and str(alert.get("status") or "").strip().lower() not in {"resolved", "closed", "dismissed"}
    ]
    missing_docs = [
        item for item in requests
        if not item.get("linked_document_id")
        and str(item.get("status") or "").strip().lower() not in {"accepted", "waived", "cancelled"}
    ]
    uploaded_docs = [item for item in requests if item.get("linked_document_id")]
    outcome = str(_row_get(review, "outcome") or "").strip().lower()
    reasons = []
    category = IMPACT_NO_RISK

    if outcome in {"exit_recommended", "edd_required"}:
        category = IMPACT_ESCALATION if outcome == "exit_recommended" else IMPACT_SENIOR_REVIEW
        reasons.append(f"Final periodic-review outcome is {outcome.replace('_', ' ')}.")
    elif current and proposed and proposed != current:
        category = IMPACT_INCREASE if RISK_LEVEL_RANK[proposed] > RISK_LEVEL_RANK[current] else IMPACT_DECREASE
        reasons.append(f"Review-level proposed risk moved from {current} to {proposed}.")
    elif high_alerts:
        category = IMPACT_INCREASE
        reasons.append(f"{len(high_alerts)} high/critical monitoring alert(s) are linked or open.")
    elif attestation["material_change_count"]:
        category = IMPACT_INCREASE if missing_docs else IMPACT_PROFILE_UPDATE
        reasons.append(f"{attestation['material_change_count']} material-change answer(s) were declared.")
    elif uploaded_docs:
        category = IMPACT_PROFILE_UPDATE
        reasons.append(f"{len(uploaded_docs)} periodic-review document(s) were uploaded.")
    else:
        reasons.append("No material risk-changing evidence is projected from current review data.")

    senior_review_suggested = category in {IMPACT_SENIOR_REVIEW, IMPACT_ESCALATION}
    if category == IMPACT_INCREASE and current and proposed:
        senior_review_suggested = RISK_LEVEL_RANK[proposed] >= RISK_LEVEL_RANK["HIGH"]

    return {
        "suggested_risk_impact": category,
        "suggested_risk_impact_label": category.replace("_", " ").title(),
        "reason_summary": reasons,
        "senior_review_suggested": senior_review_suggested,
        "evidence_counts": {
            "material_change_count": attestation["material_change_count"],
            "document_request_count": len(requests),
            "missing_document_count": len(missing_docs),
            "uploaded_document_count": len(uploaded_docs),
            "linked_alert_count": len(alerts),
            "high_or_critical_alert_count": len(high_alerts),
        },
    }


def build_reassessment_snapshot(db, review_id: int) -> Dict[str, Any]:
    review = _fetch_review(db, review_id)
    application = _fetch_application(db, _row_get(review, "application_id"))
    latest_memo = _latest_memo_row(db, review_id)
    suggested = derive_suggested_risk_impact(db, review, application=application)
    current = canonical_risk_level(
        _row_get(application, "final_risk_level")
        or _row_get(application, "risk_level")
        or _row_get(review, "risk_level")
    )
    confirmed = canonical_risk_level(_row_get(review, "confirmed_risk_level"))
    impact = _row_get(review, "risk_impact_category") or suggested["suggested_risk_impact"]
    return {
        "periodic_review_id": _row_get(review, "id"),
        "review_reference": f"PR-{_row_get(review, 'id')}",
        "application_id": _row_get(review, "application_id"),
        "application_ref": _row_get(application, "ref"),
        "company_name": _row_get(application, "company_name") or _row_get(review, "client_name"),
        "current_risk_level": current,
        "risk_score": _row_get(application, "risk_score"),
        "suggested": suggested,
        "risk_reassessment_status": _row_get(review, "risk_reassessment_status") or STATUS_NOT_STARTED,
        "risk_impact_category": impact,
        "officer_risk_decision": _row_get(review, "officer_risk_decision"),
        "confirmed_risk_level": confirmed or current,
        "risk_reassessment_rationale": _row_get(review, "risk_reassessment_rationale")
        or _row_get(review, "risk_rerate_reason"),
        "senior_review_required": _boolish(_row_get(review, "senior_review_required")),
        "senior_review_reason": _row_get(review, "senior_review_reason"),
        "saved_at": _row_get(review, "risk_reassessment_saved_at"),
        "saved_by": _row_get(review, "risk_reassessment_saved_by"),
        "memo_addendum_status": memo_addendum_status(review, latest_memo),
        "memo_addendum_status_raw": _row_get(review, "memo_addendum_status") or ADDENDUM_NOT_GENERATED,
        "memo_addendum_id": _row_get(review, "periodic_review_memo_id") or _row_get(latest_memo, "id"),
        "memo_addendum_version": _row_get(latest_memo, "version"),
        "memo_addendum_generated_at": _row_get(review, "memo_addendum_generated_at")
        or _row_get(latest_memo, "generated_at"),
        "memo_addendum_finalized_at": _row_get(review, "memo_addendum_finalized_at"),
        "memo_addendum_finalized_by": _row_get(review, "memo_addendum_finalized_by"),
        "attestation_summary": _attestation_summary(review),
        "document_summary": suggested["evidence_counts"],
        "outcome": _row_get(review, "outcome"),
        "outcome_reason": _row_get(review, "outcome_reason"),
        "officer_findings": _row_get(review, "officer_findings_note"),
        "next_review_date": _row_get(review, "next_review_date"),
        "human_control_note": "Officer decision required; application risk is not changed automatically.",
    }


def _audit(audit_writer, user, action, review_id, application_id, payload, db,
           before_state=None, after_state=None):
    if audit_writer is None:
        return
    detail = dict(payload or {})
    detail.setdefault("periodic_review_id", review_id)
    detail.setdefault("application_id", application_id)
    detail.setdefault("source_surface", SOURCE_SURFACE)
    audit_writer(
        user or {},
        action,
        f"periodic_review:{review_id}",
        json.dumps(detail, default=str, sort_keys=True),
        db=db,
        before_state=before_state,
        after_state=after_state,
        commit=False,
    )


def save_risk_reassessment(db, review_id: int, *, payload: Dict[str, Any],
                           user=None, audit_writer=None) -> Dict[str, Any]:
    if audit_writer is None:
        raise RiskReassessmentError("audit_writer is required")
    review = _fetch_review(db, review_id)
    application = _fetch_application(db, _row_get(review, "application_id"))
    current = canonical_risk_level(
        _row_get(application, "final_risk_level")
        or _row_get(application, "risk_level")
        or _row_get(review, "risk_level")
    )
    decision = _clean_text(payload.get("officer_risk_decision"))
    if decision not in VALID_RISK_DECISIONS:
        raise RiskReassessmentError("officer_risk_decision is required")
    impact = _clean_text(payload.get("risk_impact_category")) or derive_suggested_risk_impact(
        db, review, application=application,
    )["suggested_risk_impact"]
    if impact not in VALID_RISK_IMPACT_CATEGORIES:
        raise RiskReassessmentError("risk_impact_category is invalid")
    rationale = _clean_text(payload.get("rationale") or payload.get("risk_reassessment_rationale"))
    if not rationale:
        raise RiskReassessmentError("rationale is required")

    requested_level = canonical_risk_level(payload.get("confirmed_risk_level") or payload.get("new_risk_level"))
    if decision == DECISION_KEEP:
        confirmed = current
    else:
        confirmed = requested_level
        if decision in {DECISION_INCREASE, DECISION_DECREASE} and not confirmed:
            raise RiskReassessmentError("confirmed_risk_level is required for a risk change")
    if decision == DECISION_INCREASE and current and confirmed and RISK_LEVEL_RANK[confirmed] <= RISK_LEVEL_RANK[current]:
        raise RiskReassessmentError("confirmed_risk_level must be higher than current risk for an increase")
    if decision == DECISION_DECREASE and current and confirmed and RISK_LEVEL_RANK[confirmed] >= RISK_LEVEL_RANK[current]:
        raise RiskReassessmentError("confirmed_risk_level must be lower than current risk for a decrease")

    changed = bool(current and confirmed and confirmed != current)
    senior_required = _boolish(payload.get("senior_review_required")) or decision in {
        DECISION_SENIOR_REVIEW,
        DECISION_ESCALATE,
    }
    if changed and confirmed and RISK_LEVEL_RANK[confirmed] >= RISK_LEVEL_RANK["HIGH"]:
        senior_required = True
    senior_reason = _clean_text(payload.get("senior_review_reason") or payload.get("senior_review_note"))
    if senior_required and not senior_reason:
        senior_reason = (
            "Senior review required for material risk increase."
            if changed else "Senior review required by officer risk decision."
        )

    actor_id = (user or {}).get("sub") or (user or {}).get("id")
    ts = _utc_now_iso()
    before = build_reassessment_snapshot(db, review_id)
    new_level_for_review = confirmed if changed else _row_get(review, "new_risk_level")
    risk_attestation = "risk_change_required" if changed else "risk_unchanged"
    db.execute(
        "UPDATE periodic_reviews SET "
        "risk_reassessment_status = ?, "
        "risk_impact_category = ?, "
        "officer_risk_decision = ?, "
        "confirmed_risk_level = ?, "
        "risk_reassessment_rationale = ?, "
        "risk_reassessment_saved_by = ?, "
        "risk_reassessment_saved_at = ?, "
        "senior_review_required = ?, "
        "senior_review_reason = ?, "
        "previous_risk_level = COALESCE(previous_risk_level, ?), "
        "new_risk_level = COALESCE(?, new_risk_level), "
        "risk_change_attestation = ?, "
        "risk_rerate_reason = ?, "
        "risk_rerated_by = CASE WHEN ? IS NOT NULL THEN ? ELSE risk_rerated_by END, "
        "risk_rerated_at = CASE WHEN ? IS NOT NULL THEN ? ELSE risk_rerated_at END "
        "WHERE id = ?",
        (
            STATUS_CONFIRMED,
            impact,
            decision,
            confirmed,
            rationale,
            actor_id,
            ts,
            bool(senior_required),
            senior_reason,
            current,
            new_level_for_review if changed else None,
            risk_attestation,
            rationale,
            actor_id if changed else None,
            actor_id,
            actor_id if changed else None,
            ts,
            review_id,
        ),
    )
    after = build_reassessment_snapshot(db, review_id)
    base_payload = {
        "previous_risk_rating": current,
        "proposed_new_risk_rating": confirmed,
        "risk_impact_category": impact,
        "officer_risk_decision": decision,
        "rationale": rationale,
        "senior_review_required": senior_required,
    }
    _audit(
        audit_writer, user, "periodic_review_risk_reassessment_saved",
        review_id, _row_get(review, "application_id"), base_payload, db,
        before_state=before, after_state=after,
    )
    _audit(
        audit_writer, user, "periodic_review_risk_decision_confirmed",
        review_id, _row_get(review, "application_id"), base_payload, db,
        before_state=before, after_state=after,
    )
    if changed:
        _audit(
            audit_writer, user, "periodic_review_risk_rating_changed",
            review_id, _row_get(review, "application_id"), base_payload, db,
            before_state=before, after_state=after,
        )
    if senior_required:
        _audit(
            audit_writer, user, "periodic_review_senior_review_required",
            review_id, _row_get(review, "application_id"),
            {**base_payload, "senior_review_reason": senior_reason}, db,
            before_state=before, after_state=after,
        )
    db.commit()
    return after


def mark_memo_addendum_generated(db, review_id: int, *, memo_result: Dict[str, Any],
                                 user=None, audit_writer=None) -> Dict[str, Any]:
    review = _fetch_review(db, review_id)
    status = ADDENDUM_DRAFT if memo_result.get("status") == "generated" else ADDENDUM_FAILED
    ts = _utc_now_iso()
    db.execute(
        "UPDATE periodic_reviews SET memo_addendum_status = ?, "
        "memo_addendum_generated_at = ?, periodic_review_memo_id = COALESCE(?, periodic_review_memo_id) "
        "WHERE id = ?",
        (status, ts, memo_result.get("memo_id"), review_id),
    )
    action = (
        "periodic_review_memo_addendum_generated"
        if status == ADDENDUM_DRAFT else "periodic_review_memo_addendum_failed"
    )
    _audit(
        audit_writer, user, action, review_id, _row_get(review, "application_id"),
        {
            "memo_addendum_id": memo_result.get("memo_id"),
            "memo_addendum_version": memo_result.get("version"),
            "memo_addendum_status": status,
            "failure_reason": memo_result.get("error"),
        },
        db,
    )
    db.commit()
    return build_reassessment_snapshot(db, review_id)


def finalize_memo_addendum(db, review_id: int, *, user=None, audit_writer=None) -> Dict[str, Any]:
    review = _fetch_review(db, review_id)
    latest = _latest_memo_row(db, review_id)
    if latest is None:
        raise RiskReassessmentError("memo addendum must be generated before finalization")
    if _clean_text(_row_get(latest, "status")).lower() == "generation_failed":
        raise RiskReassessmentError("failed memo addendum cannot be finalized")
    actor_id = (user or {}).get("sub") or (user or {}).get("id")
    ts = _utc_now_iso()
    before = build_reassessment_snapshot(db, review_id)
    db.execute(
        "UPDATE periodic_review_memos SET status = ? WHERE id = ?",
        (ADDENDUM_FINALIZED, _row_get(latest, "id")),
    )
    db.execute(
        "UPDATE periodic_reviews SET memo_addendum_status = ?, "
        "memo_addendum_finalized_at = ?, memo_addendum_finalized_by = ?, "
        "periodic_review_memo_id = ? WHERE id = ?",
        (ADDENDUM_FINALIZED, ts, actor_id, _row_get(latest, "id"), review_id),
    )
    after = build_reassessment_snapshot(db, review_id)
    _audit(
        audit_writer, user, "periodic_review_memo_addendum_finalized",
        review_id, _row_get(review, "application_id"),
        {
            "memo_addendum_id": _row_get(latest, "id"),
            "memo_addendum_version": _row_get(latest, "version"),
            "memo_addendum_status": ADDENDUM_FINALIZED,
        },
        db, before_state=before, after_state=after,
    )
    db.commit()
    return after


__all__ = [
    "ADDENDUM_NOT_GENERATED",
    "ADDENDUM_DRAFT",
    "ADDENDUM_FINALIZED",
    "ADDENDUM_FAILED",
    "DECISION_KEEP",
    "DECISION_INCREASE",
    "DECISION_DECREASE",
    "DECISION_SENIOR_REVIEW",
    "DECISION_ESCALATE",
    "RiskReassessmentError",
    "ReviewNotFound",
    "build_reassessment_snapshot",
    "derive_suggested_risk_impact",
    "save_risk_reassessment",
    "mark_memo_addendum_generated",
    "finalize_memo_addendum",
    "memo_addendum_status",
]
