"""
Periodic Review Memo generator (PR-D).

Lightweight, deterministic, template-driven artifact generated AFTER a
periodic review's outcome is recorded. Explicitly NOT the onboarding
memo (``compliance_memos``). NOT AI-backed -- the memo is assembled
mechanically from structured data already on the review row, the
application, the linked monitoring alert (if any), and the linked EDD
case (if any).

Separation-of-concerns contract
-------------------------------
* Does not read, write, or otherwise consult ``compliance_memos``.
* Does not modify ``periodic_reviews`` -- outcome recording is owned
  by ``periodic_review_engine.record_review_outcome`` and has already
  committed by the time this module runs.
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
            "       risk_level "
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


# ─────────────────────────────────────────────────────────────────
# Section builders (pure; no side effects)
# ─────────────────────────────────────────────────────────────────
def _build_header(review, application) -> Dict[str, Any]:
    return {
        "review_id": _row_get(review, "id"),
        "application_id": _row_get(review, "application_id"),
        "application_name": (
            _row_get(application, "company_name")
            or _row_get(review, "client_name")
            or ""
        ),
        "generated_at": None,  # filled in at persist time
        "trigger_source": _row_get(review, "trigger_source")
                          or _row_get(review, "trigger_type"),
        "reviewer": _row_get(review, "decided_by"),
        "current_risk_level": _row_get(review, "new_risk_level")
                              or _row_get(review, "risk_level"),
    }


def _build_review_purpose(review) -> Dict[str, Any]:
    trigger_source = _row_get(review, "trigger_source")
    trigger_type = _row_get(review, "trigger_type")
    trigger_reason = _row_get(review, "trigger_reason") \
                     or _row_get(review, "review_reason")

    source_label = trigger_source or trigger_type or "scheduled"
    why = (
        "Periodic review triggered by %s." % source_label
        if source_label
        else "Periodic review."
    )
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
    ownership_reference = (
        f"See application {app_ref}" if app_ref else
        (f"See application {app_id}" if app_id else "")
    )
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


def _build_risk_reassessment(review) -> Dict[str, Any]:
    return {
        "outcome": _row_get(review, "outcome"),
        "rationale": _row_get(review, "outcome_reason") or "",
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


# ─────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────
def build_memo_data(db, review_id: int) -> Dict[str, Any]:
    """Assemble the 9-section memo payload. Pure (no DB writes).

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

    return {
        "header": _build_header(review, application),
        "review_purpose": _build_review_purpose(review),
        "current_profile_snapshot": _build_profile_snapshot(review, application),
        "monitoring_screening_summary": _build_monitoring_summary(review, alert),
        "required_items": _build_required_items(review),
        "edd_summary": _build_edd_summary(review, edd_case, edd_findings),
        "risk_reassessment": _build_risk_reassessment(review),
        "conclusion": _build_conclusion(review),
        "artifact_references": _build_artifact_references(review, alert),
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
                     status: str, version: Optional[int] = None) -> int:
    review = _fetch_review(db, review_id)
    application_id = _row_get(review, "application_id") if review else None
    if version is None:
        version = _next_version(db, review_id)
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
    db.commit()
    return version


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
        version = _insert_memo_row(
            db, review_id, memo_data, STATUS_GENERATED,
        )
        return {
            "review_id": review_id,
            "version": version,
            "status": STATUS_GENERATED,
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
        "review_id": _row_get(row, "periodic_review_id"),
        "version": _row_get(row, "version"),
        "generated_at": _row_get(row, "generated_at"),
        "generated_by": _row_get(row, "generated_by"),
        "status": _row_get(row, "status"),
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
