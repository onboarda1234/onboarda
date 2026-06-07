from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from lifecycle_linkage import MissingAuditWriter, _row_get
from periodic_review_policy import normalize_risk_level, parse_review_date, policy_snapshot_for_application

ASSIGNABLE_REVIEW_ROLES = {"admin", "sco", "co"}
LEGACY_SOURCE_TYPES = {
    "internal_register",
    "prior_file_note",
    "email_record",
    "system_export",
    "verbal_attestation",
    "unknown",
    "other",
}
LEGACY_CONFIDENCE_VALUES = {"high", "medium", "low"}
BASELINE_STATUS_NOT_SET = "not_set"
BASELINE_STATUS_NOT_APPLICABLE = "not_applicable"
BASELINE_STATUS_LAST_ONBOARDING = "last_onboarding_date"
BASELINE_STATUS_LAST_PERIODIC = "last_periodic_review_date"
BASELINE_STATUS_IMPORTED_LEGACY = "imported_legacy_review"
BASELINE_STATUSES = {
    BASELINE_STATUS_NOT_SET,
    BASELINE_STATUS_NOT_APPLICABLE,
    BASELINE_STATUS_LAST_ONBOARDING,
    BASELINE_STATUS_LAST_PERIODIC,
    BASELINE_STATUS_IMPORTED_LEGACY,
}
BASELINE_CADENCE_RISK_DEFAULT = "risk_default"
BASELINE_ALLOWED_CADENCE_MONTHS = {6, 12, 24, 36}
BASELINE_SOURCE_SURFACE = "backoffice_application_overview_periodic_review_baseline"
WORKSPACE_SOURCE_SURFACE = "backoffice_periodic_review_workspace"
LEGACY_FILE_NO = "no"
LEGACY_FILE_YES = "yes"
LEGACY_FILE_NA = "n/a"
MATERIAL_CHANGE_ATTESTATION_NONE = "no_material_change"
MATERIAL_CHANGE_ATTESTATION_PRESENT = "material_change_identified"
MATERIAL_CHANGE_ATTESTATIONS = {
    MATERIAL_CHANGE_ATTESTATION_NONE,
    MATERIAL_CHANGE_ATTESTATION_PRESENT,
}
MATERIAL_CHANGE_CATEGORIES = {
    "directors",
    "shareholders",
    "beneficial_owners",
    "business_activity",
    "jurisdiction_exposure",
    "target_market_products",
}
IMMUTABLE_SETUP_FIELDS = (
    "review_cycle_number",
    "policy_version",
    "risk_level",
    "last_review_date",
    "frequency_months",
    "calculation_basis",
    "legacy_import",
    "legacy_source_type",
    "legacy_source_note",
    "legacy_review_evidence_note",
    "legacy_confidence",
    "legacy_entered_by",
    "legacy_entered_at",
)


class PeriodicReviewManagementError(RuntimeError):
    pass


class ImmutablePeriodicReviewFieldError(PeriodicReviewManagementError):
    pass


class InvalidPeriodicReviewInput(PeriodicReviewManagementError):
    pass


class ReviewNotFound(PeriodicReviewManagementError):
    pass


class UnauthorizedReviewOverride(PeriodicReviewManagementError):
    pass


class UnsupportedRiskWriteGap(PeriodicReviewManagementError):
    pass


class EvidenceLinkError(PeriodicReviewManagementError):
    pass


RISK_WRITE_GAP_MESSAGE = (
    "Application/client risk write is not performed from periodic review inline re-rate "
    "because the repository does not expose a safe manual canonical risk override path; "
    "the review records and audit trail are updated, but authoritative application risk remains unchanged."
)

logger = logging.getLogger("arie.periodic_review_management")


def _require_audit_writer(audit_writer):
    if audit_writer is None:
        raise MissingAuditWriter("Periodic review management requires audit_writer")



def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _db_bool(db, value: Any) -> Union[bool, int]:
    return bool(value) if getattr(db, "is_postgres", False) else (1 if value else 0)



def _json_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]



def _emit_audit(audit_writer, user, action, review_id: int, detail: Dict[str, Any], db, *, before_state=None, after_state=None):
    audit_writer(
        user or {"sub": "system", "name": "System", "role": "system"},
        action,
        f"periodic_review:{review_id}",
        json.dumps(detail, default=str, sort_keys=True),
        db=db,
        before_state=before_state,
        after_state=after_state,
        commit=False,
    )



def _fetch_review(db, review_id: int):
    review = db.execute("SELECT * FROM periodic_reviews WHERE id = ?", (review_id,)).fetchone()
    if review is None:
        raise ReviewNotFound(f"periodic_review id={review_id} not found")
    return review



def _effective_risk_level(db, review) -> str:
    risk = _row_get(review, "new_risk_level") or _row_get(review, "risk_level")
    if risk:
        return normalize_risk_level(risk)
    app_id = _row_get(review, "application_id")
    app = db.execute(
        "SELECT final_risk_level, risk_level FROM applications WHERE id = ?",
        (app_id,),
    ).fetchone()
    return normalize_risk_level(_row_get(app, "final_risk_level") or _row_get(app, "risk_level"))


def _application_row(db, review) -> Optional[Dict[str, Any]]:
    app_id = _row_get(review, "application_id")
    if not app_id:
        return None
    app = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    return dict(app) if app is not None else None


def _clean_optional_text(value: Any, *, limit: Optional[int] = None) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if limit is not None:
        text = text[:limit]
    return text


def _normalize_baseline_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    if status not in BASELINE_STATUSES:
        raise InvalidPeriodicReviewInput("baseline_status is invalid")
    return status


def _normalize_baseline_cadence(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return BASELINE_CADENCE_RISK_DEFAULT
    if raw == BASELINE_CADENCE_RISK_DEFAULT:
        return raw
    try:
        months = int(raw)
    except (TypeError, ValueError):
        raise InvalidPeriodicReviewInput("baseline_cadence must be risk_default, 6, 12, 24, or 36")
    if months not in BASELINE_ALLOWED_CADENCE_MONTHS:
        raise InvalidPeriodicReviewInput("baseline_cadence must be risk_default, 6, 12, 24, or 36")
    return str(months)


def _baseline_months_value(cadence: str) -> Optional[int]:
    if cadence == BASELINE_CADENCE_RISK_DEFAULT:
        return None
    return int(cadence)


def _normalize_legacy_file(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"na", "n.a.", "not_applicable", "not applicable"}:
        text = LEGACY_FILE_NA
    if text not in {LEGACY_FILE_NO, LEGACY_FILE_YES, LEGACY_FILE_NA}:
        raise InvalidPeriodicReviewInput("legacy_file must be yes, no, or n/a")
    return text


def _application_approval_anchor(app: Optional[Dict[str, Any]]) -> Optional[str]:
    app = dict(app or {})
    return (
        app.get("first_approved_at")
        or app.get("approved_at")
        or app.get("completed_at")
        or app.get("decided_at")
    )


def _baseline_anchor_for_application(review, app: Optional[Dict[str, Any]]) -> str:
    app = dict(app or {})
    anchor = (
        _application_approval_anchor(app)
        or app.get("created_at")
        or _row_get(review, "created_at")
        or _utc_now_iso()
    )
    return parse_review_date(anchor).isoformat()


def _legacy_file_from_review(review) -> str:
    status = str(_row_get(review, "baseline_status") or "").strip().lower()
    if status in {BASELINE_STATUS_LAST_PERIODIC, BASELINE_STATUS_IMPORTED_LEGACY}:
        return LEGACY_FILE_YES
    return LEGACY_FILE_NO


def _serialize_baseline_state(review) -> Dict[str, Any]:
    return {
        "baseline_status": _row_get(review, "baseline_status"),
        "baseline_date": _row_get(review, "baseline_date"),
        "baseline_cadence_months": _row_get(review, "baseline_cadence_months"),
        "baseline_note": _row_get(review, "baseline_note"),
        "legacy_file": _legacy_file_from_review(review),
        "last_review_date": _row_get(review, "last_review_date"),
        "next_review_date": _row_get(review, "next_review_date"),
        "due_date": _row_get(review, "due_date"),
        "frequency_months": _row_get(review, "frequency_months"),
        "calculation_basis": _row_get(review, "calculation_basis"),
        "policy_version": _row_get(review, "policy_version"),
        "legacy_import": bool(_row_get(review, "legacy_import")),
    }


def _serialize_application_baseline_state(app) -> Dict[str, Any]:
    return {
        "application_baseline_status": _row_get(app, "periodic_review_baseline_status"),
        "application_baseline_date": _row_get(app, "periodic_review_baseline_date"),
        "application_baseline_cadence_months": _row_get(app, "periodic_review_baseline_cadence_months"),
        "application_baseline_note": _row_get(app, "periodic_review_baseline_note"),
        "legacy_file": (
            LEGACY_FILE_YES
            if str(_row_get(app, "periodic_review_baseline_status") or "").strip().lower()
            in {BASELINE_STATUS_LAST_PERIODIC, BASELINE_STATUS_IMPORTED_LEGACY}
            else (
                LEGACY_FILE_NA
                if str(_row_get(app, "periodic_review_baseline_status") or "").strip().lower()
                == BASELINE_STATUS_NOT_APPLICABLE
                else LEGACY_FILE_NO
            )
        ),
        "last_review_date": _row_get(app, "periodic_review_last_review_date"),
        "next_review_due": _row_get(app, "periodic_review_next_review_due"),
        "frequency_months": _row_get(app, "periodic_review_baseline_cadence_months"),
        "calculation_basis": _row_get(app, "periodic_review_baseline_calculation_basis"),
        "policy_version": _row_get(app, "periodic_review_baseline_policy_version"),
    }


def _application_baseline_columns_present(app) -> bool:
    if not app:
        return False
    return any(
        _row_get(app, key) not in (None, "")
        for key in (
            "periodic_review_baseline_status",
            "periodic_review_baseline_date",
            "periodic_review_baseline_cadence_months",
            "periodic_review_baseline_note",
            "periodic_review_last_review_date",
            "periodic_review_next_review_due",
        )
    )


def _latest_active_review_for_application(db, application_id: Any):
    if not application_id:
        return None
    return db.execute(
        """
        SELECT * FROM periodic_reviews
        WHERE application_id = ?
          AND COALESCE(status, 'pending') IN ('pending','in_progress','awaiting_information','pending_senior_review')
        ORDER BY due_date ASC, created_at DESC, id DESC
        LIMIT 1
        """,
        (application_id,),
    ).fetchone()


def _persist_application_baseline(
    db,
    application_id: Any,
    *,
    status: str,
    anchor_date: Optional[str],
    frequency_months: Optional[int],
    note: Optional[str],
    stored_last_review_date: Optional[str],
    next_due: Optional[str],
    calculation_basis: Optional[str],
    policy_version: Optional[str],
):
    db.execute(
        """
        UPDATE applications
        SET periodic_review_baseline_status = ?,
            periodic_review_baseline_date = ?,
            periodic_review_baseline_cadence_months = ?,
            periodic_review_baseline_note = ?,
            periodic_review_last_review_date = ?,
            periodic_review_next_review_due = ?,
            periodic_review_baseline_calculation_basis = ?,
            periodic_review_baseline_policy_version = ?
        WHERE id = ?
        """,
        (
            status,
            anchor_date,
            frequency_months,
            note,
            stored_last_review_date,
            next_due,
            calculation_basis,
            policy_version,
            application_id,
        ),
    )


def _persist_review_baseline(
    db,
    review_id: int,
    *,
    status: str,
    anchor_date: Optional[str],
    frequency_months: Optional[int],
    note: Optional[str],
    stored_last_review_date: Optional[str],
    next_due: Optional[str],
    calculation_basis: Optional[str],
    policy_version: Optional[str],
    legacy_import: bool,
):
    db.execute(
        "UPDATE periodic_reviews SET baseline_status = ?, baseline_date = ?, baseline_cadence_months = ?, baseline_note = ?, "
        "last_review_date = ?, next_review_date = ?, due_date = ?, frequency_months = ?, calculation_basis = ?, policy_version = ?, legacy_import = ? "
        "WHERE id = ?",
        (
            status,
            anchor_date,
            frequency_months,
            note,
            stored_last_review_date,
            next_due,
            next_due,
            frequency_months,
            calculation_basis,
            policy_version,
            _db_bool(db, legacy_import),
            review_id,
        ),
    )


def _validate_officer(db, officer_id: Optional[str]) -> Optional[str]:
    officer_id = str(officer_id or "").strip()
    if not officer_id:
        return None
    row = db.execute(
        "SELECT id, role, status FROM users WHERE id = ? LIMIT 1",
        (officer_id,),
    ).fetchone()
    if row is None:
        raise InvalidPeriodicReviewInput("assigned_officer must reference an existing user")
    if str(_row_get(row, "status") or "active").strip().lower() != "active":
        raise InvalidPeriodicReviewInput("assigned_officer must reference an active user")
    if str(_row_get(row, "role") or "").strip().lower() not in ASSIGNABLE_REVIEW_ROLES:
        raise InvalidPeriodicReviewInput("assigned_officer must reference an officer, SCO, or admin user")
    return officer_id



def _setup_change_requires_override(review, proposed: Dict[str, Any]) -> bool:
    has_existing_manual_baseline = any(
        _row_get(review, field) not in (None, "", 0, "0", False)
        for field in ("last_review_date", "legacy_entered_at", "legacy_import")
    )
    if not has_existing_manual_baseline:
        return False
    for field in IMMUTABLE_SETUP_FIELDS:
        if field not in proposed:
            continue
        current = _row_get(review, field)
        new_value = proposed[field]
        if field == "legacy_import":
            current = bool(current)
            new_value = bool(new_value)
        elif field in {"frequency_months", "review_cycle_number"}:
            current = int(current or 0) if current is not None else None
            new_value = int(new_value or 0) if new_value is not None else None
        if current not in (None, "", []) and current != new_value:
            return True
    return False



def _require_override_if_needed(review, proposed: Dict[str, Any], user, override_reason: Optional[str]):
    if not _setup_change_requires_override(review, proposed):
        return
    role = str((user or {}).get("role") or "").strip().lower()
    if role not in {"admin", "sco"}:
        raise UnauthorizedReviewOverride("Only SCO/admin can override locked periodic review setup")
    if not str(override_reason or "").strip():
        raise ImmutablePeriodicReviewFieldError("override_reason is required to change locked periodic review setup")



def assign_review(db, review_id: int, *, assigned_officer: str, user, audit_writer, priority: Optional[str] = None, reassigned_reason: Optional[str] = None) -> Dict[str, Any]:
    _require_audit_writer(audit_writer)
    review = _fetch_review(db, review_id)
    assigned_officer = _validate_officer(db, assigned_officer)
    current_assigned = _row_get(review, "assigned_officer")
    if current_assigned and current_assigned != assigned_officer and not str(reassigned_reason or "").strip():
        raise InvalidPeriodicReviewInput("reassigned_reason is required when changing assigned_officer")
    ts = _utc_now_iso()
    before = {
        "assigned_officer": current_assigned,
        "assigned_by": _row_get(review, "assigned_by"),
        "assigned_at": _row_get(review, "assigned_at"),
        "reassigned_reason": _row_get(review, "reassigned_reason"),
        "priority": _row_get(review, "priority"),
        "decided_by": _row_get(review, "decided_by"),
    }
    db.execute(
        "UPDATE periodic_reviews SET assigned_officer = ?, assigned_by = ?, assigned_at = ?, reassigned_reason = ?, priority = COALESCE(?, priority) WHERE id = ?",
        (
            assigned_officer,
            (user or {}).get("sub"),
            ts,
            str(reassigned_reason or "").strip() or None,
            priority,
            review_id,
        ),
    )
    after = {
        "assigned_officer": assigned_officer,
        "assigned_by": (user or {}).get("sub"),
        "assigned_at": ts,
        "reassigned_reason": str(reassigned_reason or "").strip() or None,
        "priority": priority or _row_get(review, "priority"),
        "decided_by": _row_get(review, "decided_by"),
    }
    _emit_audit(
        audit_writer,
        user,
        "periodic_review.assignment_updated",
        review_id,
        {
            "review_id": review_id,
            "assigned_officer": assigned_officer,
            "reassigned": bool(current_assigned and current_assigned != assigned_officer),
            "reassigned_reason": str(reassigned_reason or "").strip() or None,
        },
        db,
        before_state=before,
        after_state=after,
    )
    return after



def save_legacy_import_setup(
    db,
    review_id: int,
    *,
    last_review_date: Any,
    source_type: str,
    confidence: str,
    source_note: Optional[str] = None,
    review_evidence_note: Optional[str] = None,
    assigned_officer: Optional[str] = None,
    user=None,
    audit_writer=None,
    override_reason: Optional[str] = None,
) -> Dict[str, Any]:
    _require_audit_writer(audit_writer)
    review = _fetch_review(db, review_id)
    source_type = str(source_type or "").strip()
    confidence = str(confidence or "").strip().lower()
    if source_type not in LEGACY_SOURCE_TYPES:
        raise InvalidPeriodicReviewInput("source_type is invalid")
    if confidence not in LEGACY_CONFIDENCE_VALUES:
        raise InvalidPeriodicReviewInput("confidence must be high, medium, or low")
    assigned_officer = _validate_officer(db, assigned_officer) if assigned_officer is not None else _row_get(review, "assigned_officer")
    risk_level = _effective_risk_level(db, review)
    app = _application_row(db, review) or {}
    policy = policy_snapshot_for_application(
        app,
        anchor_date=last_review_date,
        override_risk_level=risk_level,
    )
    ts = _utc_now_iso()
    proposed = {
        "last_review_date": str(last_review_date),
        "review_cycle_number": int(_row_get(review, "review_cycle_number") or 1),
        "policy_version": policy["policy_version"],
        "frequency_months": policy["frequency_months"],
        "calculation_basis": policy["calculation_basis"],
        "legacy_import": True,
        "legacy_source_type": source_type,
        "legacy_source_note": source_note,
        "legacy_review_evidence_note": review_evidence_note,
        "legacy_confidence": confidence,
        "legacy_entered_by": (user or {}).get("sub"),
        "legacy_entered_at": ts,
        "risk_level": _row_get(review, "risk_level") or risk_level,
    }
    _require_override_if_needed(review, proposed, user, override_reason)
    before = {
        field: _row_get(review, field)
        for field in (
            "last_review_date",
            "next_review_date",
            "assigned_officer",
            "assigned_by",
            "assigned_at",
            "review_type",
            "policy_version",
            "frequency_months",
            "calculation_basis",
            "legacy_import",
            "legacy_source_type",
            "legacy_source_note",
            "legacy_review_evidence_note",
            "legacy_confidence",
            "legacy_entered_by",
            "legacy_entered_at",
            "import_requires_ack",
        )
    }
    db.execute(
        "UPDATE periodic_reviews SET last_review_date = ?, next_review_date = ?, due_date = ?, assigned_officer = ?, assigned_by = ?, assigned_at = ?, review_type = ?, policy_version = ?, frequency_months = ?, calculation_basis = ?, legacy_import = ?, legacy_source_type = ?, legacy_source_note = ?, legacy_review_evidence_note = ?, legacy_confidence = ?, legacy_entered_by = COALESCE(legacy_entered_by, ?), legacy_entered_at = COALESCE(legacy_entered_at, ?), import_requires_ack = ?, review_cycle_number = COALESCE(review_cycle_number, 1) WHERE id = ?",
        (
            str(last_review_date),
            policy["next_review_date"],
            policy["next_review_date"],
            assigned_officer,
            (user or {}).get("sub"),
            ts,
            "legacy_import",
            policy["policy_version"],
            policy["frequency_months"],
            policy["calculation_basis"],
            _db_bool(db, True),
            source_type,
            source_note,
            review_evidence_note,
            confidence,
            (user or {}).get("sub"),
            ts,
            _db_bool(db, risk_level in {"HIGH", "VERY_HIGH"}),
            review_id,
        ),
    )
    after = {
        **before,
        "last_review_date": str(last_review_date),
        "next_review_date": policy["next_review_date"],
        "assigned_officer": assigned_officer,
        "assigned_by": (user or {}).get("sub"),
        "assigned_at": ts,
        "review_type": "legacy_import",
        "policy_version": policy["policy_version"],
        "frequency_months": policy["frequency_months"],
        "calculation_basis": policy["calculation_basis"],
        "legacy_import": True,
        "legacy_source_type": source_type,
        "legacy_source_note": source_note,
        "legacy_review_evidence_note": review_evidence_note,
        "legacy_confidence": confidence,
        "legacy_entered_by": _row_get(review, "legacy_entered_by") or (user or {}).get("sub"),
        "legacy_entered_at": _row_get(review, "legacy_entered_at") or ts,
        "import_requires_ack": risk_level in {"HIGH", "VERY_HIGH"},
    }
    _emit_audit(
        audit_writer,
        user,
        "periodic_review.legacy_import_saved",
        review_id,
        {
            "review_id": review_id,
            "source_type": source_type,
            "confidence": confidence,
            "assigned_officer": assigned_officer,
            "review_evidence_note_present": bool(str(review_evidence_note or "").strip()),
            "override_reason": str(override_reason or "").strip() or None,
        },
        db,
        before_state=before,
        after_state=after,
    )
    return after


def save_periodic_review_baseline(
    db,
    review_id: int,
    *,
    baseline_status: Any = None,
    baseline_date: Any = None,
    baseline_cadence: Any = None,
    officer_note: Any = None,
    legacy_file: Any = None,
    last_review_date: Any = None,
    user=None,
    audit_writer=None,
) -> Dict[str, Any]:
    _require_audit_writer(audit_writer)
    review = _fetch_review(db, review_id)
    return save_application_periodic_review_baseline(
        db,
        _row_get(review, "application_id"),
        baseline_status=baseline_status,
        baseline_date=baseline_date,
        baseline_cadence=baseline_cadence,
        officer_note=officer_note,
        legacy_file=legacy_file,
        last_review_date=last_review_date,
        user=user,
        audit_writer=audit_writer,
        preferred_review_id=review_id,
        override_risk_level=_effective_risk_level(db, review),
    )


def save_application_periodic_review_baseline(
    db,
    application_id: Any,
    *,
    baseline_status: Any = None,
    baseline_date: Any = None,
    baseline_cadence: Any = None,
    officer_note: Any = None,
    legacy_file: Any = None,
    last_review_date: Any = None,
    user=None,
    audit_writer=None,
    preferred_review_id: Optional[int] = None,
    override_risk_level: Optional[str] = None,
) -> Dict[str, Any]:
    _require_audit_writer(audit_writer)
    app = db.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
    if app is None:
        raise ReviewNotFound(f"application id={application_id} not found")
    app = dict(app)
    note = _clean_optional_text(officer_note, limit=500)
    compatibility_status = _clean_optional_text(baseline_status)
    legacy_choice = legacy_file
    if legacy_choice in (None, "") and compatibility_status:
        status = _normalize_baseline_status(compatibility_status)
        if status in {BASELINE_STATUS_LAST_PERIODIC, BASELINE_STATUS_IMPORTED_LEGACY}:
            legacy_choice = LEGACY_FILE_YES
            last_review_date = last_review_date or baseline_date
        else:
            legacy_choice = LEGACY_FILE_NO
    legacy_choice = _normalize_legacy_file(legacy_choice or LEGACY_FILE_NO)
    entered_last_review = _clean_optional_text(last_review_date or baseline_date)
    if legacy_choice == LEGACY_FILE_YES and not entered_last_review:
        raise InvalidPeriodicReviewInput("last_review_date is required when legacy_file is yes")
    if entered_last_review:
        entered_last_review = parse_review_date(entered_last_review).isoformat()

    before = _serialize_application_baseline_state(app)
    existing_next_due = before.get("next_review_due")
    review = None
    if preferred_review_id is not None:
        candidate = db.execute("SELECT * FROM periodic_reviews WHERE id = ?", (preferred_review_id,)).fetchone()
        if candidate is not None and _row_get(candidate, "application_id") == application_id:
            review = candidate
    if review is None:
        review = _latest_active_review_for_application(db, application_id)
    review_id = _row_get(review, "id") if review is not None else None
    review_before = _serialize_baseline_state(review) if review is not None else None
    approval_anchor = _application_approval_anchor(app)
    if not approval_anchor and review_id is None:
        raise InvalidPeriodicReviewInput("Periodic review baseline can be configured after onboarding approval.")

    if legacy_choice == LEGACY_FILE_NA:
        anchor_date = None
        status = BASELINE_STATUS_NOT_APPLICABLE
        next_due = None
        frequency_months = None
        calculation_basis = None
        policy_version = None
        stored_last_review_date = None
    else:
        anchor_date = (
            entered_last_review
            if legacy_choice == LEGACY_FILE_YES
            else _baseline_anchor_for_application(review or {}, app)
        )
        risk_level = normalize_risk_level(override_risk_level or app.get("final_risk_level") or app.get("risk_level"))
        policy = policy_snapshot_for_application(
            app,
            anchor_date=anchor_date,
            override_risk_level=risk_level,
        )
        status = (
            BASELINE_STATUS_LAST_PERIODIC
            if legacy_choice == LEGACY_FILE_YES
            else BASELINE_STATUS_LAST_ONBOARDING
        )
        next_due = policy["next_review_date"]
        frequency_months = policy["frequency_months"]
        calculation_basis = policy["calculation_basis"]
        policy_version = policy["policy_version"]
        stored_last_review_date = entered_last_review if legacy_choice == LEGACY_FILE_YES else None

    _persist_application_baseline(
        db,
        application_id,
        status=status,
        anchor_date=anchor_date,
        frequency_months=frequency_months,
        note=note,
        stored_last_review_date=stored_last_review_date,
        next_due=next_due,
        calculation_basis=calculation_basis,
        policy_version=policy_version,
    )

    if review_id is not None:
        if not existing_next_due:
            existing_next_due = review_before.get("next_review_date") or review_before.get("due_date")
        legacy_import = bool(_row_get(review, "legacy_import")) and legacy_choice == LEGACY_FILE_YES
        _persist_review_baseline(
            db,
            int(review_id),
            status=status,
            anchor_date=anchor_date,
            frequency_months=frequency_months,
            note=note,
            stored_last_review_date=stored_last_review_date,
            next_due=next_due,
            calculation_basis=calculation_basis,
            policy_version=policy_version,
            legacy_import=legacy_import,
        )

    audit_detail = {
        "application_id": application_id,
        "periodic_review_id": review_id,
        "actor_officer_user_id": (user or {}).get("sub"),
        "legacy_file": legacy_choice,
        "last_review_date": stored_last_review_date,
        "derived_cadence": frequency_months,
        "next_review_due": next_due,
        "old_baseline_status": before.get("application_baseline_status") or (review_before or {}).get("baseline_status"),
        "old_baseline_date": before.get("application_baseline_date") or (review_before or {}).get("baseline_date"),
        "old_baseline_cadence": before.get("application_baseline_cadence_months") or (review_before or {}).get("baseline_cadence_months"),
        "new_baseline_status": status,
        "new_baseline_date": anchor_date,
        "new_baseline_cadence": frequency_months,
        "next_review_due_before": existing_next_due,
        "next_review_due_after": next_due,
        "has_active_periodic_review": bool(review_id),
        "source_surface": BASELINE_SOURCE_SURFACE,
    }
    after = {
        **before,
        "application_baseline_status": status,
        "application_baseline_date": anchor_date,
        "application_baseline_cadence_months": frequency_months,
        "application_baseline_note": note,
        "legacy_file": legacy_choice,
        "last_review_date": stored_last_review_date,
        "next_review_due": next_due,
        "frequency_months": frequency_months,
        "calculation_basis": calculation_basis,
        "policy_version": policy_version,
    }
    prior_status = str(
        before.get("application_baseline_status") or (review_before or {}).get("baseline_status") or ""
    ).strip().lower()
    if prior_status in {BASELINE_STATUS_NOT_SET, BASELINE_STATUS_NOT_APPLICABLE}:
        prior_status = ""
    action = "periodic_review_baseline_saved" if not prior_status else "periodic_review_baseline_updated"
    audit_target = f"periodic_review:{int(review_id)}" if review_id is not None else f"application:{application_id}"
    audit_writer(
        user or {"sub": "system", "name": "System", "role": "system"},
        action,
        audit_target,
        json.dumps(audit_detail, default=str, sort_keys=True),
        db=db,
        before_state=review_before or before,
        after_state=after,
        commit=False,
    )
    return {
        "application_id": application_id,
        "review_id": review_id,
        "baseline_status": status,
        "legacy_file": legacy_choice,
        "baseline_date": anchor_date,
        "last_review_date": stored_last_review_date,
        "baseline_cadence": BASELINE_CADENCE_RISK_DEFAULT,
        "baseline_cadence_months": frequency_months,
        "baseline_note": note,
        "next_review_due": next_due,
        "has_active_periodic_review": bool(review_id),
        "source_surface": BASELINE_SOURCE_SURFACE,
    }


def acknowledge_legacy_import(db, review_id: int, *, user, audit_writer) -> Dict[str, Any]:
    _require_audit_writer(audit_writer)
    review = _fetch_review(db, review_id)
    role = str((user or {}).get("role") or "").strip().lower()
    if role not in {"admin", "sco"}:
        raise InvalidPeriodicReviewInput("Only SCO/admin can acknowledge imported review setup")
    if not _row_get(review, "import_requires_ack"):
        return {
            "review_id": review_id,
            "legacy_sco_acknowledged_by": _row_get(review, "legacy_sco_acknowledged_by"),
            "legacy_sco_acknowledged_at": _row_get(review, "legacy_sco_acknowledged_at"),
            "import_requires_ack": False,
        }
    ts = _utc_now_iso()
    before = {
        "legacy_sco_acknowledged_by": _row_get(review, "legacy_sco_acknowledged_by"),
        "legacy_sco_acknowledged_at": _row_get(review, "legacy_sco_acknowledged_at"),
    }
    db.execute(
        "UPDATE periodic_reviews SET legacy_sco_acknowledged_by = ?, legacy_sco_acknowledged_at = ? WHERE id = ?",
        ((user or {}).get("sub"), ts, review_id),
    )
    after = {
        "legacy_sco_acknowledged_by": (user or {}).get("sub"),
        "legacy_sco_acknowledged_at": ts,
    }
    _emit_audit(
        audit_writer,
        user,
        "periodic_review.legacy_import_acknowledged",
        review_id,
        {"review_id": review_id},
        db,
        before_state=before,
        after_state=after,
    )
    return {"review_id": review_id, **after, "import_requires_ack": True}



def save_officer_rationale(db, review_id: int, *, rationale: str, user, audit_writer) -> Dict[str, Any]:
    _require_audit_writer(audit_writer)
    if not str(rationale or "").strip():
        raise InvalidPeriodicReviewInput("officer_rationale is required")
    review = _fetch_review(db, review_id)
    before = {"officer_rationale": _row_get(review, "officer_rationale")}
    db.execute(
        "UPDATE periodic_reviews SET officer_rationale = ? WHERE id = ?",
        (str(rationale).strip(), review_id),
    )
    after = {"officer_rationale": str(rationale).strip()}
    _emit_audit(
        audit_writer,
        user,
        "periodic_review.officer_rationale_saved",
        review_id,
        {"review_id": review_id},
        db,
        before_state=before,
        after_state=after,
    )
    return {"review_id": review_id, **after}


def save_workspace_findings(
    db,
    review_id: int,
    *,
    findings_note: Any = None,
    deficiencies_note: Any = None,
    internal_review_note: Any = None,
    user=None,
    audit_writer=None,
) -> Dict[str, Any]:
    _require_audit_writer(audit_writer)
    review = _fetch_review(db, review_id)
    findings_note = _clean_optional_text(findings_note, limit=4000)
    deficiencies_note = _clean_optional_text(deficiencies_note, limit=4000)
    internal_review_note = _clean_optional_text(internal_review_note, limit=4000)
    if not any((findings_note, deficiencies_note, internal_review_note)):
        raise InvalidPeriodicReviewInput("At least one findings field is required")
    before = {
        "officer_findings_note": _row_get(review, "officer_findings_note"),
        "officer_deficiencies_note": _row_get(review, "officer_deficiencies_note"),
        "officer_internal_review_note": _row_get(review, "officer_internal_review_note"),
        "findings_updated_by": _row_get(review, "findings_updated_by"),
        "findings_updated_at": _row_get(review, "findings_updated_at"),
    }
    ts = _utc_now_iso()
    db.execute(
        "UPDATE periodic_reviews SET officer_findings_note = ?, officer_deficiencies_note = ?, "
        "officer_internal_review_note = ?, findings_updated_by = ?, findings_updated_at = ? WHERE id = ?",
        (
            findings_note,
            deficiencies_note,
            internal_review_note,
            (user or {}).get("sub"),
            ts,
            review_id,
        ),
    )
    after = {
        "officer_findings_note": findings_note,
        "officer_deficiencies_note": deficiencies_note,
        "officer_internal_review_note": internal_review_note,
        "findings_updated_by": (user or {}).get("sub"),
        "findings_updated_at": ts,
    }
    changed_fields = sorted(
        key for key in (
            "officer_findings_note",
            "officer_deficiencies_note",
            "officer_internal_review_note",
        )
        if before.get(key) != after.get(key)
    )
    action = (
        "periodic_review_findings_saved"
        if not any(
            before.get(key)
            for key in ("officer_findings_note", "officer_deficiencies_note", "officer_internal_review_note")
        )
        else "periodic_review_findings_updated"
    )
    _emit_audit(
        audit_writer,
        user,
        action,
        review_id,
        {
            "review_id": review_id,
            "periodic_review_id": review_id,
            "application_id": _row_get(review, "application_id"),
            "actor_officer_user_id": (user or {}).get("sub"),
            "changed_fields": changed_fields,
            "source_surface": WORKSPACE_SOURCE_SURFACE,
        },
        db,
        before_state=before,
        after_state=after,
    )
    return {"review_id": review_id, **after, "changed_fields": changed_fields}



def save_material_change_attestation(
    db,
    review_id: int,
    *,
    attestation: str,
    categories: Optional[List[str]],
    user,
    audit_writer,
) -> Dict[str, Any]:
    _require_audit_writer(audit_writer)
    attestation = str(attestation or "").strip()
    categories = [str(category).strip() for category in (categories or []) if str(category).strip()]
    invalid = [category for category in categories if category not in MATERIAL_CHANGE_CATEGORIES]
    if attestation not in MATERIAL_CHANGE_ATTESTATIONS:
        raise InvalidPeriodicReviewInput("material_change_attestation is invalid")
    if invalid:
        raise InvalidPeriodicReviewInput("material_change_categories contains invalid values")
    if attestation == MATERIAL_CHANGE_ATTESTATION_NONE and categories:
        raise InvalidPeriodicReviewInput("no_material_change cannot include material change categories")
    if attestation == MATERIAL_CHANGE_ATTESTATION_PRESENT and not categories:
        raise InvalidPeriodicReviewInput("material_change_identified requires at least one category")
    review = _fetch_review(db, review_id)
    before = {
        "material_change_attestation": _row_get(review, "material_change_attestation"),
        "material_change_categories": _json_list(_row_get(review, "material_change_categories")),
    }
    db.execute(
        "UPDATE periodic_reviews SET material_change_attestation = ?, material_change_categories = ? WHERE id = ?",
        (attestation, json.dumps(categories), review_id),
    )
    after = {
        "material_change_attestation": attestation,
        "material_change_categories": categories,
    }
    _emit_audit(
        audit_writer,
        user,
        "periodic_review.material_change_attested",
        review_id,
        {"review_id": review_id, **after},
        db,
        before_state=before,
        after_state=after,
    )
    return {"review_id": review_id, **after}



def record_risk_change(
    db,
    review_id: int,
    *,
    new_risk_level: str,
    reason_code: str,
    officer_note: Optional[str],
    user,
    audit_writer,
) -> Dict[str, Any]:
    _require_audit_writer(audit_writer)
    reason_code = str(reason_code or "").strip()
    if not reason_code:
        raise InvalidPeriodicReviewInput("reason_code is required")
    review = _fetch_review(db, review_id)
    prior_risk = _effective_risk_level(db, review)
    new_risk_level = normalize_risk_level(new_risk_level)
    ts = _utc_now_iso()
    anchor_date = _row_get(review, "last_review_date") or _row_get(review, "created_at") or ts
    app = _application_row(db, review) or {}
    policy = policy_snapshot_for_application(
        app,
        anchor_date=anchor_date,
        override_risk_level=new_risk_level,
    )
    attestation_payload = {
        "prior_risk": prior_risk,
        "new_risk": new_risk_level,
        "reason_code": reason_code,
        "officer_note": str(officer_note or "").strip(),
        "application_risk_write_status": "unsafe_gap",
        "timestamp": ts,
    }
    before = {
        "previous_risk_level": _row_get(review, "previous_risk_level"),
        "new_risk_level": _row_get(review, "new_risk_level"),
        "risk_change_attestation": _row_get(review, "risk_change_attestation"),
        "risk_rerate_reason": _row_get(review, "risk_rerate_reason"),
        "risk_rerated_by": _row_get(review, "risk_rerated_by"),
        "risk_rerated_at": _row_get(review, "risk_rerated_at"),
        "policy_version": _row_get(review, "policy_version"),
        "frequency_months": _row_get(review, "frequency_months"),
        "calculation_basis": _row_get(review, "calculation_basis"),
        "next_review_date": _row_get(review, "next_review_date"),
        "due_date": _row_get(review, "due_date"),
    }
    db.execute(
        "UPDATE periodic_reviews SET previous_risk_level = ?, new_risk_level = ?, risk_change_attestation = ?, risk_rerate_reason = ?, risk_rerated_by = ?, risk_rerated_at = ?, policy_version = ?, frequency_months = ?, calculation_basis = ?, next_review_date = ?, due_date = ? WHERE id = ?",
        (
            prior_risk,
            new_risk_level,
            json.dumps(attestation_payload, sort_keys=True),
            reason_code,
            (user or {}).get("sub"),
            ts,
            policy["policy_version"],
            policy["frequency_months"],
            policy["calculation_basis"],
            policy["next_review_date"],
            policy["due_date"],
            review_id,
        ),
    )
    after = {
        **before,
        "previous_risk_level": prior_risk,
        "new_risk_level": new_risk_level,
        "risk_change_attestation": attestation_payload,
        "risk_rerate_reason": reason_code,
        "risk_rerated_by": (user or {}).get("sub"),
        "risk_rerated_at": ts,
        "policy_version": policy["policy_version"],
        "frequency_months": policy["frequency_months"],
        "calculation_basis": policy["calculation_basis"],
        "next_review_date": policy["next_review_date"],
        "due_date": policy["due_date"],
    }
    _emit_audit(
        audit_writer,
        user,
        "periodic_review.risk_rerated",
        review_id,
        {
            "review_id": review_id,
            "prior_risk": prior_risk,
            "new_risk": new_risk_level,
            "reason_code": reason_code,
            "officer_note": str(officer_note or "").strip(),
            "application_risk_write_status": "unsafe_gap",
        },
        db,
        before_state=before,
        after_state=after,
    )
    logger.warning(
        "periodic_review.risk_rerated review_id=%s application risk write skipped: %s",
        review_id,
        RISK_WRITE_GAP_MESSAGE,
    )
    return {
        "review_id": review_id,
        "prior_risk": prior_risk,
        "new_risk": new_risk_level,
        "reason_code": reason_code,
        "next_review_date": policy["next_review_date"],
        "policy_version": policy["policy_version"],
        "frequency_months": policy["frequency_months"],
        "application_risk_write_status": "unsafe_gap",
        "application_risk_write_message": RISK_WRITE_GAP_MESSAGE,
        "authoritative_application_risk_changed": False,
    }



def add_evidence_link(
    db,
    review_id: int,
    *,
    requirement_id: Optional[str],
    document_id: str,
    link_type: Optional[str],
    note: Optional[str],
    user,
    audit_writer,
) -> Dict[str, Any]:
    _require_audit_writer(audit_writer)
    review = _fetch_review(db, review_id)
    app_id = _row_get(review, "application_id")
    document = db.execute(
        "SELECT id, application_id FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    if document is None:
        raise EvidenceLinkError("document_id must reference an existing document")
    if app_id and _row_get(document, "application_id") != app_id:
        raise EvidenceLinkError("document_id must belong to the same application as the periodic review")
    existing = db.execute(
        "SELECT * FROM periodic_review_evidence_links WHERE periodic_review_id = ? AND COALESCE(requirement_id, '') = COALESCE(?, '') AND document_id = ? AND COALESCE(link_type, '') = COALESCE(?, '') LIMIT 1",
        (review_id, requirement_id, document_id, link_type),
    ).fetchone()
    if existing is not None:
        return dict(existing)
    ts = _utc_now_iso()
    if getattr(db, "is_postgres", False):
        row = db.execute(
            "INSERT INTO periodic_review_evidence_links (periodic_review_id, requirement_id, document_id, link_type, linked_by, linked_at, note) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (review_id, requirement_id, document_id, link_type, (user or {}).get("sub"), ts, note),
        ).fetchone()
        link_id = _row_get(row, "id")
    else:
        db.execute(
            "INSERT INTO periodic_review_evidence_links (periodic_review_id, requirement_id, document_id, link_type, linked_by, linked_at, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (review_id, requirement_id, document_id, link_type, (user or {}).get("sub"), ts, note),
        )
        link_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    payload = {
        "id": link_id,
        "periodic_review_id": review_id,
        "requirement_id": requirement_id,
        "document_id": document_id,
        "link_type": link_type,
        "linked_by": (user or {}).get("sub"),
        "linked_at": ts,
        "note": note,
    }
    _emit_audit(
        audit_writer,
        user,
        "periodic_review.evidence_link_added",
        review_id,
        payload,
        db,
        before_state=None,
        after_state=payload,
    )
    return payload
