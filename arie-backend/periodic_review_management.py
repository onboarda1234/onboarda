from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import lifecycle_linkage as ll
from lifecycle_linkage import MissingAuditWriter, _row_get
from periodic_review_policy import normalize_risk_level, policy_snapshot_for_risk

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


def _require_audit_writer(audit_writer):
    if audit_writer is None:
        raise MissingAuditWriter("Periodic review management requires audit_writer")



def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")



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
    policy = policy_snapshot_for_risk(risk_level, anchor_date=last_review_date)
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
            "legacy_confidence",
            "legacy_entered_by",
            "legacy_entered_at",
            "import_requires_ack",
        )
    }
    db.execute(
        "UPDATE periodic_reviews SET last_review_date = ?, next_review_date = ?, due_date = ?, assigned_officer = ?, assigned_by = ?, assigned_at = ?, review_type = ?, policy_version = ?, frequency_months = ?, calculation_basis = ?, legacy_import = ?, legacy_source_type = ?, legacy_source_note = ?, legacy_confidence = ?, legacy_entered_by = COALESCE(legacy_entered_by, ?), legacy_entered_at = COALESCE(legacy_entered_at, ?), import_requires_ack = ?, review_cycle_number = COALESCE(review_cycle_number, 1) WHERE id = ?",
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
            1,
            source_type,
            source_note,
            confidence,
            (user or {}).get("sub"),
            ts,
            1 if risk_level in {"HIGH", "VERY_HIGH"} else 0,
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
            "override_reason": str(override_reason or "").strip() or None,
        },
        db,
        before_state=before,
        after_state=after,
    )
    return after



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
    policy = policy_snapshot_for_risk(new_risk_level, anchor_date=anchor_date)
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
    }
    db.execute(
        "UPDATE periodic_reviews SET previous_risk_level = ?, new_risk_level = ?, risk_change_attestation = ?, risk_rerate_reason = ?, risk_rerated_by = ?, risk_rerated_at = ?, policy_version = ?, frequency_months = ?, calculation_basis = ?, next_review_date = ?, due_date = COALESCE(due_date, ?) WHERE id = ?",
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
            policy["next_review_date"],
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
