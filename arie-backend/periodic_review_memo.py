"""
Periodic Review Memo / Addendum generator (PR-D + PRS-7).

Lightweight, deterministic, template-driven artifact generated during
or after a periodic review's outcome/risk reassessment workflow.
Explicitly NOT the onboarding memo (``compliance_memos``). NOT AI-backed
-- the memo/addendum is assembled
mechanically from structured data already on the review row, the
application, the linked monitoring alert (if any), and the linked EDD
case (if any).

Separation-of-concerns contract
-------------------------------
* Does not read, write, or otherwise consult ``compliance_memos``.
* The generator itself does not modify ``periodic_reviews`` -- outcome
  recording is owned by ``periodic_review_engine.record_review_outcome``
  and has already committed by the time this module runs.
* Does not call ``edd_memo_integration``; the EDD summary section
  reads ``edd_cases`` / ``edd_findings`` directly and by soft-ref only.
* Does not call Anthropic, OpenAI, or any other AI provider. The memo
  is deterministic; a tests/test_periodic_review_memo.py case pins the
  zero-AI-calls invariant.

Failure semantics
-----------------
``generate_periodic_review_memo`` is safe to call directly. Callers
(currently ``PeriodicReviewCompleteHandler``) should invoke it AFTER
the outcome commit. If generation raises, the caller MUST NOT roll
back the outcome; it should instead call
``record_generation_failure`` to persist a ``status='generation_failed'``
row so the read endpoint and UI can differentiate "not yet completed"
(no row) from "completed but generation failed" (row with failure
status).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional


logger = logging.getLogger("arie.periodic_review_memo")

SYSTEM_ACTOR = "system:periodic-review-memo-generator"

MEMO_CONTEXT_KIND = "periodic_review"
MEMO_CONTEXT_JSON = json.dumps({"kind": MEMO_CONTEXT_KIND}, sort_keys=True)

STATUS_GENERATED = "generated"
STATUS_GENERATION_FAILED = "generation_failed"


# ─────────────────────────────────────────────────────────────────
# Row helpers (SQLite Row / psycopg2 DictRow / dict parity)
# ─────────────────────────────────────────────────────────────────
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


def _coerce_json(raw, default):
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _boolish(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


# ─────────────────────────────────────────────────────────────────
# Data assembly
# ─────────────────────────────────────────────────────────────────
def _fetch_review(db, review_id: int):
    return db.execute(
        "SELECT * FROM periodic_reviews WHERE id = ?", (review_id,)
    ).fetchone()


def _fetch_application(db, application_id):
    if application_id is None:
        return None
    try:
        return db.execute(
            "SELECT id, ref, company_name, country, sector, entity_type, "
            "       risk_level, final_risk_level, risk_score "
            "FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
    except Exception:  # pragma: no cover - defensive; some tables omit columns
        try:
            return db.execute(
                "SELECT * FROM applications WHERE id = ?", (application_id,)
            ).fetchone()
        except Exception:
            return None


def _fetch_alert(db, alert_id):
    if alert_id is None:
        return None
    try:
        return db.execute(
            "SELECT id, alert_type, severity, status, summary, created_at "
            "FROM monitoring_alerts WHERE id = ?", (alert_id,)
        ).fetchone()
    except Exception:
        return None


def _fetch_edd(db, edd_case_id):
    if edd_case_id is None:
        return None
    try:
        return db.execute(
            "SELECT id, stage, decision, decision_reason "
            "FROM edd_cases WHERE id = ?", (edd_case_id,)
        ).fetchone()
    except Exception:
        return None


def _fetch_edd_findings(db, edd_case_id):
    if edd_case_id is None:
        return None
    try:
        return db.execute(
            "SELECT findings_summary FROM edd_findings WHERE edd_case_id = ?",
            (edd_case_id,),
        ).fetchone()
    except Exception:
        return None


def _fetch_document_requests(db, review_id):
    try:
        rows = db.execute(
            "SELECT id, requirement_key, requirement_label, requirement_type, "
            "       status, mandatory, linked_document_id, uploaded_at, reviewed_at "
            "FROM application_enhanced_requirements "
            "WHERE linked_periodic_review_id = ? AND active = 1 "
            "ORDER BY id ASC",
            (review_id,),
        ).fetchall()
    except Exception:
        return []
    return [dict(row) for row in rows]


def _fetch_recent_audit_refs(db, review_id, application_id):
    targets = [f"periodic_review:{review_id}", f"Review {review_id}"]
    if application_id:
        targets.append(str(application_id))
    placeholders = ",".join(["?"] * len(targets))
    try:
        rows = db.execute(
            f"SELECT id, action, timestamp, user_id, user_role "
            f"FROM audit_log WHERE target IN ({placeholders}) "
            f"ORDER BY timestamp DESC, id DESC LIMIT 12",
            tuple(targets),
        ).fetchall()
    except Exception:
        return []
    return [dict(row) for row in rows]


# ─────────────────────────────────────────────────────────────────
# Section builders (pure; no side effects)
# ─────────────────────────────────────────────────────────────────
def _build_header(review, application) -> Dict[str, Any]:
    review_id = _row_get(review, "id")
    return {
        "memo_type": "periodic_review_memo_addendum",
        "review_id": _row_get(review, "id"),
        "review_reference": f"PR-{review_id}",
        "application_id": _row_get(review, "application_id"),
        "application_ref": _row_get(application, "ref"),
        "application_name": (
            _row_get(application, "company_name")
            or _row_get(review, "client_name")
            or ""
        ),
        "generated_at": None,  # filled in at persist time
        "trigger_source": _row_get(review, "trigger_source")
                          or _row_get(review, "trigger_type"),
        "reviewer": _row_get(review, "assigned_officer") or _row_get(review, "decided_by"),
        "current_risk_level": (
            _row_get(application, "final_risk_level")
            or _row_get(application, "risk_level")
            or _row_get(review, "risk_level")
        ),
        "review_period": {
            "started_at": _row_get(review, "started_at"),
            "completed_at": _row_get(review, "completed_at"),
            "outcome_recorded_at": _row_get(review, "outcome_recorded_at"),
        },
    }


def _build_review_purpose(review) -> Dict[str, Any]:
    trigger_source = _row_get(review, "trigger_source")
    trigger_type = _row_get(review, "trigger_type")
    trigger_reason = _row_get(review, "trigger_reason") \
                     or _row_get(review, "review_reason")

    source_label = trigger_source or trigger_type or "scheduled"
    if source_label:
        why = f"Periodic review triggered by {source_label}."
    else:
        why = "Periodic review."
    summary = trigger_reason or ""
    return {
        "why_review_exists": why,
        "trigger_summary": summary,
    }


def _build_profile_snapshot(review, application) -> Dict[str, Any]:
    # v1: ownership_reference is a POINTER to the application, not a
    # reconstructed ownership analysis. See PR-D brief Step 1.
    app_id = _row_get(review, "application_id")
    app_ref = _row_get(application, "ref")
    if app_ref:
        ownership_reference = f"See application {app_ref}"
    elif app_id:
        ownership_reference = f"See application {app_id}"
    else:
        ownership_reference = ""
    return {
        "entity_type": _row_get(application, "entity_type") or "",
        "jurisdiction": _row_get(application, "country") or "",
        "sector": _row_get(application, "sector") or "",
        "ownership_reference": ownership_reference,
    }


def _build_monitoring_summary(review, alert) -> Dict[str, Any]:
    linked_alerts = []
    if alert is not None:
        linked_alerts.append({
            "id": _row_get(alert, "id"),
            "alert_type": _row_get(alert, "alert_type"),
            "severity": _row_get(alert, "severity"),
            "status": _row_get(alert, "status"),
            "summary": _row_get(alert, "summary"),
        })
    return {
        "linked_alerts": linked_alerts,
        # PR-D v1: screening/material change deltas are not yet retrievable
        # in a single pass -- stubbed to null (documented in PR description).
        "screening_changes": None,
        "material_changes": None,
    }


def _build_attestation_summary(review) -> Dict[str, Any]:
    payload = _coerce_json(_row_get(review, "client_attestation_payload"), {})
    answers = payload.get("answers") if isinstance(payload, dict) else {}
    questions = payload.get("questions") if isinstance(payload, dict) else []
    material_changes = []
    comments = []
    if isinstance(questions, list):
        for item in questions:
            if not isinstance(item, dict):
                continue
            key = item.get("key") or item.get("question_key")
            answer = str(item.get("answer") or "").strip().lower()
            comment = str(item.get("comment") or "").strip()
            material = bool(item.get("is_material_change")) or (
                answer == "yes" and key != "company_contact_details_correct"
            ) or (
                answer == "no" and key == "company_contact_details_correct"
            )
            if material and key:
                material_changes.append(key)
                if comment:
                    comments.append({"key": key, "comment": comment})
    if isinstance(answers, dict):
        for key, value in answers.items():
            if not isinstance(value, dict):
                continue
            answer = str(value.get("answer") or "").strip().lower()
            comment = str(value.get("comment") or "").strip()
            material = (
                answer == "yes" and key != "company_contact_details_correct"
            ) or (
                answer == "no" and key == "company_contact_details_correct"
            )
            if material and key not in material_changes:
                material_changes.append(key)
                if comment:
                    comments.append({"key": key, "comment": comment})
    categories = _coerce_json(_row_get(review, "material_change_categories"), [])
    if isinstance(categories, list):
        for key in categories:
            if key and str(key) not in material_changes:
                material_changes.append(str(key))
    return {
        "status": _row_get(review, "client_attestation_status") or "not_started",
        "submitted_at": _row_get(review, "client_attestation_submitted_at"),
        "questionnaire_version": _row_get(review, "client_attestation_questionnaire_version"),
        "material_changes_declared": material_changes,
        "material_change_count": len(material_changes),
        "material_change_comments": comments,
    }


def _build_documents_summary(document_requests) -> Dict[str, Any]:
    requested = []
    for item in document_requests:
        requested.append({
            "id": item.get("id"),
            "requirement_key": item.get("requirement_key"),
            "requirement_label": item.get("requirement_label"),
            "status": item.get("status"),
            "mandatory": bool(item.get("mandatory")),
            "uploaded": bool(item.get("linked_document_id")),
            "linked_document_id": item.get("linked_document_id"),
        })
    return {
        "requested_count": len(requested),
        "uploaded_count": len([item for item in requested if item["uploaded"]]),
        "outstanding_count": len([
            item for item in requested
            if item["mandatory"] and not item["uploaded"]
            and str(item.get("status") or "").lower() not in {"accepted", "waived", "cancelled"}
        ]),
        "items": requested,
    }


def _build_required_items(review) -> list:
    raw = _row_get(review, "required_items")
    items = _coerce_json(raw, [])
    if not isinstance(items, list):
        return []
    normalized = []
    for it in items:
        if not isinstance(it, dict):
            continue
        normalized.append({
            "id": it.get("id") or it.get("item_id") or 0,
            "label": it.get("label") or it.get("title") or "",
            "rationale": it.get("rationale") or it.get("reason") or "",
            "status": it.get("status"),
        })
    return normalized


def _build_edd_summary(review, edd_case, edd_findings) -> Dict[str, Any]:
    linked_edd_id = _row_get(review, "linked_edd_case_id")
    outcome = _row_get(review, "outcome")
    triggered = bool(linked_edd_id) or outcome == "edd_required"
    key_findings = _row_get(edd_findings, "findings_summary")
    return {
        "triggered": triggered,
        "linked_edd_id": linked_edd_id,
        "key_findings_summary": key_findings,
    }


def _build_risk_reassessment(review, application, suggested) -> Dict[str, Any]:
    current_risk = (
        _row_get(application, "final_risk_level")
        or _row_get(application, "risk_level")
        or _row_get(review, "risk_level")
    )
    confirmed_risk = _row_get(review, "confirmed_risk_level") or current_risk
    return {
        "current_risk_rating_before_review": current_risk,
        "risk_score": _row_get(application, "risk_score"),
        "suggested_risk_impact": suggested.get("suggested_risk_impact"),
        "suggested_reason_summary": suggested.get("reason_summary", []),
        "officer_confirmed_risk_decision": _row_get(review, "officer_risk_decision"),
        "confirmed_risk_rating": confirmed_risk,
        "new_risk_rating": _row_get(review, "new_risk_level") or (
            confirmed_risk if confirmed_risk != current_risk else None
        ),
        "rationale": _row_get(review, "risk_reassessment_rationale")
        or _row_get(review, "risk_rerate_reason")
        or _row_get(review, "outcome_reason")
        or "",
        "senior_review_required": _boolish(_row_get(review, "senior_review_required")),
        "senior_review_note": _row_get(review, "senior_review_reason")
        or _row_get(review, "officer_internal_review_note"),
        "outcome": _row_get(review, "outcome"),
        "final_review_outcome_rationale": _row_get(review, "outcome_reason") or "",
    }


def _build_conclusion(review) -> Dict[str, Any]:
    outcome = _row_get(review, "outcome") or ""
    next_step = None
    # Deterministic next-step mapping from outcome vocabulary.
    if outcome == "no_change":
        next_step = "Continue relationship; resume standard monitoring."
    elif outcome == "enhanced_monitoring":
        next_step = "Apply enhanced monitoring; schedule next review per risk tier."
    elif outcome == "edd_required":
        next_step = "Route to EDD workflow; see linked EDD case."
    elif outcome == "exit_recommended":
        next_step = "Initiate exit / offboarding workflow."
    return {
        "outcome": outcome,
        "outcome_reason": _row_get(review, "outcome_reason") or "",
        "next_step": next_step,
        "next_review_date": _row_get(review, "next_review_date"),
    }


def _build_artifact_references(review, alert) -> Dict[str, Any]:
    linked_alert_id = _row_get(review, "linked_monitoring_alert_id")
    linked_alerts = []
    if linked_alert_id:
        linked_alerts.append(linked_alert_id)
    return {
        "linked_alerts": linked_alerts,
        "linked_edd": _row_get(review, "linked_edd_case_id"),
        # PR-D deliberately does NOT hard-link to compliance_memos.
        # Onboarding memo identity is per-application per-version and
        # unrelated to review lifecycle.
        "onboarding_memo_reference": None,
    }


def _build_officer_findings(review) -> Dict[str, Any]:
    return {
        "findings_summary": _row_get(review, "officer_findings_note") or "",
        "follow_up_points": _row_get(review, "officer_deficiencies_note") or "",
        "rationale": _row_get(review, "officer_rationale")
        or _row_get(review, "outcome_reason")
        or "",
        "senior_review_note": _row_get(review, "officer_internal_review_note")
        or _row_get(review, "senior_review_reason")
        or "",
    }


# ─────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────
def build_memo_data(db, review_id: int) -> Dict[str, Any]:
    """Assemble the periodic-review memo addendum payload. Pure (no DB writes).

    Raises ValueError if the review does not exist.
    """
    review = _fetch_review(db, review_id)
    if review is None:
        raise ValueError(f"periodic_review id={review_id} not found")

    application = _fetch_application(db, _row_get(review, "application_id"))
    alert = _fetch_alert(db, _row_get(review, "linked_monitoring_alert_id"))
    linked_edd_id = _row_get(review, "linked_edd_case_id")
    edd_case = _fetch_edd(db, linked_edd_id)
    edd_findings = _fetch_edd_findings(db, linked_edd_id)

    import periodic_review_risk_reassessment as prr

    document_requests = _fetch_document_requests(db, review_id)
    suggested = prr.derive_suggested_risk_impact(db, review, application=application)
    audit_refs = _fetch_recent_audit_refs(
        db, review_id, _row_get(review, "application_id"),
    )

    return {
        "header": _build_header(review, application),
        "review_purpose": _build_review_purpose(review),
        "current_profile_snapshot": _build_profile_snapshot(review, application),
        "attestation_summary": _build_attestation_summary(review),
        "documents_summary": _build_documents_summary(document_requests),
        "monitoring_screening_summary": _build_monitoring_summary(review, alert),
        "required_items": _build_required_items(review),
        "edd_summary": _build_edd_summary(review, edd_case, edd_findings),
        "officer_findings": _build_officer_findings(review),
        "risk_reassessment": _build_risk_reassessment(review, application, suggested),
        "conclusion": _build_conclusion(review),
        "artifact_references": _build_artifact_references(review, alert),
        "audit_references": audit_refs,
    }


def _next_version(db, review_id: int) -> int:
    row = db.execute(
        "SELECT COALESCE(MAX(version), 0) AS v "
        "FROM periodic_review_memos WHERE periodic_review_id = ?",
        (review_id,),
    ).fetchone()
    current = _row_get(row, "v", 0) or 0
    return int(current) + 1


def _insert_memo_row(db, review_id: int, memo_data: Dict[str, Any],
                     status: str, version: Optional[int] = None) -> Dict[str, int]:
    review = _fetch_review(db, review_id)
    application_id = _row_get(review, "application_id") if review else None
    if version is None:
        version = _next_version(db, review_id)
    if getattr(db, "is_postgres", False):
        row = db.execute(
            "INSERT INTO periodic_review_memos "
            "(periodic_review_id, application_id, version, memo_data, "
            " memo_context, generated_by, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                review_id, application_id, version,
                json.dumps(memo_data, sort_keys=True, default=str),
                MEMO_CONTEXT_JSON,
                SYSTEM_ACTOR,
                status,
            ),
        ).fetchone()
        memo_id = _row_get(row, "id")
    else:
        db.execute(
            "INSERT INTO periodic_review_memos "
            "(periodic_review_id, application_id, version, memo_data, "
            " memo_context, generated_by, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                review_id, application_id, version,
                json.dumps(memo_data, sort_keys=True, default=str),
                MEMO_CONTEXT_JSON,
                SYSTEM_ACTOR,
                status,
            ),
        )
        memo_id = db.execute(
            "SELECT last_insert_rowid() AS id"
        ).fetchone()["id"]
    db.execute(
        "UPDATE periodic_reviews SET memo_status = ?, periodic_review_memo_id = ? WHERE id = ?",
        (status, memo_id, review_id),
    )
    db.commit()
    return {"version": version, "id": memo_id}


def generate_periodic_review_memo(db, review_id: int) -> Dict[str, Any]:
    """Generate and persist a version=N memo row for ``review_id``.

    Returns ``{"review_id": ..., "version": N, "status": "generated"}``
    on success. On failure, persists a ``status='generation_failed'``
    row with whatever partial ``memo_data`` was available, logs at
    ERROR with full traceback, and re-raises so the caller can surface
    the error. The caller MUST NOT roll back the outcome commit.
    """
    try:
        memo_data = build_memo_data(db, review_id)
        persisted = _insert_memo_row(
            db, review_id, memo_data, STATUS_GENERATED,
        )
        return {
            "review_id": review_id,
            "version": persisted["version"],
            "memo_id": persisted["id"],
            "status": STATUS_GENERATED,
            "memo_addendum_status": "draft_generated",
        }
    except Exception as exc:
        logger.error(
            "Periodic review memo generation FAILED for review_id=%s: %s: %s",
            review_id, type(exc).__name__, exc,
            exc_info=True,
        )
        try:
            _insert_memo_row(
                db, review_id, {"error": str(exc)},
                STATUS_GENERATION_FAILED,
            )
        except Exception as persist_exc:  # pragma: no cover - defensive
            logger.error(
                "Failed to persist generation_failed row for review_id=%s: %s",
                review_id, persist_exc, exc_info=True,
            )
        raise


def fetch_latest_memo(db, review_id: int) -> Optional[Dict[str, Any]]:
    """Return the latest memo row for ``review_id`` as a dict, or None."""
    row = db.execute(
        "SELECT id, periodic_review_id, application_id, version, memo_data, "
        "       memo_context, generated_at, generated_by, status "
        "FROM periodic_review_memos "
        "WHERE periodic_review_id = ? "
        "ORDER BY version DESC "
        "LIMIT 1",
        (review_id,),
    ).fetchone()
    if row is None:
        return None
    memo_data = _coerce_json(_row_get(row, "memo_data"), {})
    memo_context = _coerce_json(_row_get(row, "memo_context"),
                                {"kind": MEMO_CONTEXT_KIND})
    return {
        "memo_id": _row_get(row, "id"),
        "review_id": _row_get(row, "periodic_review_id"),
        "version": _row_get(row, "version"),
        "generated_at": _row_get(row, "generated_at"),
        "generated_by": _row_get(row, "generated_by"),
        "status": _row_get(row, "status"),
        "memo_addendum_status": (
            "finalized" if _row_get(row, "status") == "finalized"
            else ("failed" if _row_get(row, "status") == STATUS_GENERATION_FAILED else "draft_generated")
        ),
        "memo_context": memo_context,
        "memo_data": memo_data,
    }


__all__ = [
    "SYSTEM_ACTOR",
    "MEMO_CONTEXT_KIND",
    "MEMO_CONTEXT_JSON",
    "STATUS_GENERATED",
    "STATUS_GENERATION_FAILED",
    "build_memo_data",
    "generate_periodic_review_memo",
    "fetch_latest_memo",
]
