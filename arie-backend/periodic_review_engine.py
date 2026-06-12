"""
Periodic Review Operating Model -- PR-03
========================================

Provider-agnostic engine that turns ``periodic_reviews`` from a thin
pending/completed placeholder into a real lifecycle review object with:

* explicit operational state model
* trigger provenance preserved (PR-01 + PR-02)
* structured required-item generation
* deterministic, audit-enforced escalation to EDD
* explicit outcome recording with audit trail
* clean separation from onboarding memo history

This module is **provider-agnostic** by design:

* it does not import or depend on screening providers
  (no Sumsub, no ComplyAdvantage)
* it does not flip ``ENABLE_SCREENING_ABSTRACTION``
* it does not read ``screening_reports_normalized`` as authoritative
* it does not generate or rewrite onboarding memos
  (``memo_handler.py`` is untouched)
* it does not implement client-facing information requests; it only
  emits the *review-side required-item generation contract* so a future
  PR can wire those items to a real information-request engine

Audit-writer contract
---------------------
Every mutating helper here requires a non-None ``audit_writer`` and
delegates to the PR-01 ``lifecycle_linkage`` audit-writer enforcement
where helpful, plus emits structured PR-03 audit events for state
transitions, required-item generation, escalation and outcome
recording. The contract mirrors ``BaseHandler.log_audit`` exactly:

    audit_writer(user, action, target, detail,
                 db=None, before_state=None, after_state=None)

EX-control posture
------------------
This module is additive and leaves the existing EX-01..EX-13 runtime
control surfaces unchanged. It reuses ``lifecycle_linkage`` (PR-01)
for the bidirectional alert/EDD/review linkage it needs and reuses the
existing duplicate-prevention predicate for active EDD lookup.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

import document_health_monitor as dhm
import lifecycle_linkage as ll
import monitoring_routing as mr
from environment import get_screening_validity_days
from lifecycle_linkage import (
    InvalidLifecycleTransition,
    LifecycleLinkageError,
    MissingAuditWriter,
    ReferencedRowNotFound,
    VALID_PRIORITIES,
    _row_get,  # internal but stable helper -- mirror PR-01 conventions
)
from periodic_review_blockers import evaluate_review_readiness

logger = logging.getLogger("arie.periodic_review_engine")


# ─────────────────────────────────────────────────────────────────
# State and outcome vocabularies (application-layer source of truth)
# ─────────────────────────────────────────────────────────────────

# Operational lifecycle state. These are stored in the existing
# ``periodic_reviews.status`` column. The legacy vocabulary was just
# {"pending", "completed"}; PR-03 extends it with three intermediate
# operational states. There is no DB-level CHECK on ``status`` today
# (see migration_009 header), so this expansion is additive.
STATE_PENDING = "pending"
STATE_IN_PROGRESS = "in_progress"
STATE_AWAITING_INFORMATION = "awaiting_information"
STATE_PENDING_SENIOR_REVIEW = "pending_senior_review"
STATE_COMPLETED = "completed"

VALID_REVIEW_STATES = (
    STATE_PENDING,
    STATE_IN_PROGRESS,
    STATE_AWAITING_INFORMATION,
    STATE_PENDING_SENIOR_REVIEW,
    STATE_COMPLETED,
)

# Allowed forward transitions. Backwards transitions are intentionally
# disallowed: a completed review is terminal, and an awaiting-info
# review must move back to in_progress before being escalated to senior
# review. This keeps the state machine boring and auditable.
STATE_TRANSITIONS: Dict[str, tuple] = {
    STATE_PENDING: (STATE_IN_PROGRESS,),
    STATE_IN_PROGRESS: (
        STATE_AWAITING_INFORMATION,
        STATE_PENDING_SENIOR_REVIEW,
        STATE_COMPLETED,
    ),
    STATE_AWAITING_INFORMATION: (
        STATE_IN_PROGRESS,
        STATE_PENDING_SENIOR_REVIEW,
        STATE_COMPLETED,
    ),
    STATE_PENDING_SENIOR_REVIEW: (
        STATE_IN_PROGRESS,
        STATE_COMPLETED,
    ),
    STATE_COMPLETED: (),
}

# Explicit outcome semantics, recorded separately from operational
# state. Kept disjoint from the legacy ``decision`` column so we never
# overload one field with both progress and outcome.
OUTCOME_NO_CHANGE = "no_change"
OUTCOME_ENHANCED_MONITORING = "enhanced_monitoring"
OUTCOME_EDD_REQUIRED = "edd_required"
OUTCOME_EXIT_RECOMMENDED = "exit_recommended"
OUTCOME_NO_MATERIAL_CHANGE = "no_material_change"
OUTCOME_MATERIAL_CHANGE_IDENTIFIED = "material_change_identified"
OUTCOME_RISK_RATING_UNCHANGED = "risk_rating_unchanged"
OUTCOME_RISK_RATING_CHANGED = "risk_rating_changed"
OUTCOME_CLIENT_FOLLOW_UP_REQUIRED = "client_follow_up_required"

VALID_REVIEW_OUTCOMES = (
    OUTCOME_NO_CHANGE,
    OUTCOME_ENHANCED_MONITORING,
    OUTCOME_EDD_REQUIRED,
    OUTCOME_EXIT_RECOMMENDED,
    OUTCOME_NO_MATERIAL_CHANGE,
    OUTCOME_MATERIAL_CHANGE_IDENTIFIED,
    OUTCOME_RISK_RATING_UNCHANGED,
    OUTCOME_RISK_RATING_CHANGED,
    OUTCOME_CLIENT_FOLLOW_UP_REQUIRED,
)

# Vocabulary for structured required items. Kept narrow and explicit;
# this is *not* a generic checklist engine. Each item is a small dict:
#
#     {
#         "code": "<one of REQUIRED_ITEM_CODES>",
#         "label": "<short human-readable summary>",
#         "rationale": "<why this item was generated for this review>",
#     }
#
# The full required-item set is stored as a JSON array on
# ``periodic_reviews.required_items``. PR-03 deliberately does not
# track per-item status here -- that belongs to the future
# information-request engine.
REQUIRED_ITEM_CODES = (
    "kyc_refresh",
    "ubo_confirmation",
    "jurisdiction_review",
    "document_expired",
    "document_expiring_soon",
    "document_stale",
    "document_expiry_missing",
    "screening_refresh",
    "risk_level_review",
    "risk_level_change_review",
    "source_of_funds_refresh",
    "source_of_wealth_refresh",
    "licensing_refresh",
    "monitoring_alert_followup",
    "prior_outcome_followup",
    "edd_followup",
    "review_outcome_recorded",
    "custom_evidence_requirement",
)

ITEM_CATEGORY_CLIENT_PROFILE = "Client Profile"
ITEM_CATEGORY_DOCUMENT_HEALTH = "Document Health"
ITEM_CATEGORY_SCREENING_RISK = "Screening & Risk"
ITEM_CATEGORY_MONITORING_ALERTS = "Monitoring Alerts"
ITEM_CATEGORY_FINAL_OUTCOME = "Final Outcome"
ITEM_CATEGORY_REQUIRED_EVIDENCE = "Required Evidence"

REQUIRED_ITEM_STATUS_OPEN = "open"
REQUIRED_ITEM_STATUS_CLEARED = "cleared"
REQUIRED_ITEM_STATUS_INFO_REQUESTED = "info_requested"
REQUIRED_ITEM_STATUS_ESCALATED = "escalated"
REQUIRED_ITEM_STATUS_NOT_APPLICABLE = "not_applicable"
VALID_REQUIRED_ITEM_STATUSES = (
    REQUIRED_ITEM_STATUS_OPEN,
    REQUIRED_ITEM_STATUS_CLEARED,
    REQUIRED_ITEM_STATUS_INFO_REQUESTED,
    REQUIRED_ITEM_STATUS_ESCALATED,
    REQUIRED_ITEM_STATUS_NOT_APPLICABLE,
)

VALID_REQUIRED_ITEM_SEVERITIES = ("low", "medium", "high", "critical")

# Terminal EDD stages -- mirrored from lifecycle_linkage so we never
# create or attempt to reuse a closed EDD case from this module.
TERMINAL_EDD_STAGES = ll.TERMINAL_EDD_STAGES


# ─────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────
class PeriodicReviewEngineError(ValueError):
    """Base class for periodic-review-engine validation failures."""


class InvalidReviewState(PeriodicReviewEngineError):
    pass


class InvalidReviewOutcome(PeriodicReviewEngineError):
    pass


class InvalidReviewTransition(PeriodicReviewEngineError):
    pass


class ReviewNotFound(PeriodicReviewEngineError):
    pass


class ReviewClosedError(PeriodicReviewEngineError):
    """Raised when a mutating action is attempted on a completed review."""


class InvalidRequiredItemStatus(PeriodicReviewEngineError):
    pass


class RequiredItemNotFound(PeriodicReviewEngineError):
    pass


class ReviewCompletionBlocked(PeriodicReviewEngineError):
    def __init__(self, blocking_items: List[Dict[str, Any]]):
        super().__init__("Periodic review cannot be completed")
        self.blocking_items = blocking_items


# ─────────────────────────────────────────────────────────────────
# Internal utilities
# ─────────────────────────────────────────────────────────────────
AuditWriter = Callable[..., None]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require_audit_writer(audit_writer):
    if audit_writer is None:
        raise MissingAuditWriter(
            "periodic_review_engine mutating helpers require a non-None "
            "audit_writer (canonical audit path). Refusing to mutate."
        )


def _detail(payload):
    try:
        return json.dumps(dict(payload), default=str, sort_keys=True)
    except (TypeError, ValueError):
        return json.dumps({"serialization_error": True})


def _emit_audit(audit_writer, user, action, target, detail_payload,
                db, before_state=None, after_state=None):
    user_dict = dict(user) if user else {}
    logger.info(
        "periodic_review_audit action=%s target=%s detail=%s",
        action, target, _detail(detail_payload),
    )
    if audit_writer is None:
        return
    try:
        audit_writer(
            user_dict, action, target, _detail(detail_payload),
            db=db, before_state=before_state, after_state=after_state,
        )
    except Exception:
        logger.exception("periodic_review audit write failed action=%s", action)


def _fetch_review(db, review_id):
    """Return a periodic review row as a dict, or raise ReviewNotFound."""
    row = db.execute(
        "SELECT * FROM periodic_reviews WHERE id = ?", (review_id,)
    ).fetchone()
    if row is None:
        raise ReviewNotFound(f"periodic_review id={review_id} not found")
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        try:
            return {k: row[k] for k in row.keys()}
        except Exception:
            raise ReviewNotFound(
                f"periodic_review id={review_id} could not be materialised"
            )


def _fetch_application(db, application_id):
    if application_id is None:
        return None
    row = db.execute(
        "SELECT * FROM applications WHERE id = ?", (application_id,)
    ).fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def _clean_text(value) -> str:
    return str(value or "").strip()


def _boolish(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _completion_blocker(item_type: str, label: str, review_id) -> Dict[str, Any]:
    return {
        "item_type": item_type,
        "label": label,
        "severity": "high",
        "source": "periodic_reviews",
        "source_id": review_id,
        "completion_only": True,
    }


def _normalise_requested_risk_level(value):
    text = str(value or "").strip().upper()
    if not text:
        return None
    if text not in {"LOW", "MEDIUM", "HIGH", "VERY_HIGH"}:
        return None
    return text


def _coerce_state(value: Optional[str]) -> str:
    """Normalise the stored status value to a known state.

    Reviews created before PR-03 only ever stored 'pending' or
    'completed'. Anything else is treated as the legacy default of
    'pending' so the new state machine has a deterministic anchor.
    """
    if value in VALID_REVIEW_STATES:
        return value
    return STATE_PENDING


def _parse_ts(value) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _severity_rank(value: Optional[str]) -> int:
    return {
        "low": 1,
        "medium": 2,
        "high": 3,
        "critical": 4,
    }.get(str(value or "medium").strip().lower(), 2)


def _stable_item_id(category: str, item_type: str, source: str,
                    source_id=None, label: Optional[str] = None) -> str:
    parts = [
        str(category or "").strip().lower().replace(" ", "_"),
        str(item_type or "item").strip().lower(),
        str(source or "").strip().lower().replace(" ", "_"),
    ]
    if source_id not in (None, ""):
        parts.append(str(source_id).strip())
    elif label:
        parts.append(str(label).strip().lower().replace(" ", "_"))
    return ":".join([p for p in parts if p])


def _make_item(*, category: str, item_type: str, label: str, severity: str,
               source: str, source_id=None, rationale: str,
               code: Optional[str] = None,
               status: str = REQUIRED_ITEM_STATUS_OPEN,
               officer_note: Optional[str] = None,
               resolved_by: Optional[str] = None,
               resolved_at: Optional[str] = None) -> Dict[str, Any]:
    item_id = _stable_item_id(category, item_type, source, source_id, label)
    return {
        "id": item_id,
        "code": code or item_type,
        "category": category,
        "item_type": item_type,
        "label": label,
        "severity": str(severity or "medium").strip().lower(),
        "source": source,
        "source_id": source_id,
        "status": status if status in VALID_REQUIRED_ITEM_STATUSES else REQUIRED_ITEM_STATUS_OPEN,
        "rationale": rationale,
        "officer_note": officer_note,
        "resolved_by": resolved_by,
        "resolved_at": resolved_at,
    }


def _normalize_required_item(item: Any, idx: int) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    label = item.get("label") or item.get("title") or item.get("item")
    code = item.get("code") or item.get("item_type") or f"item_{idx + 1}"
    category = item.get("category") or ITEM_CATEGORY_CLIENT_PROFILE
    source = item.get("source") or "review"
    source_id = item.get("source_id")
    normalized = _make_item(
        category=category,
        item_type=item.get("item_type") or code,
        label=label or code.replace("_", " "),
        severity=item.get("severity") or "medium",
        source=source,
        source_id=source_id,
        rationale=item.get("rationale") or item.get("reason") or "",
        code=code,
        status=item.get("status") or REQUIRED_ITEM_STATUS_OPEN,
        officer_note=item.get("officer_note"),
        resolved_by=item.get("resolved_by"),
        resolved_at=item.get("resolved_at"),
    )
    if item.get("id"):
        normalized["id"] = item["id"]
    return normalized


def _load_required_items(raw) -> List[Dict[str, Any]]:
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    normalized = []
    for idx, item in enumerate(items):
        parsed = _normalize_required_item(item, idx)
        if parsed is not None:
            normalized.append(parsed)
    return normalized


# ─────────────────────────────────────────────────────────────────
# Read helpers
# ─────────────────────────────────────────────────────────────────
def get_review_state(db, review_id) -> str:
    """Return the current operational state for a review."""
    review = _fetch_review(db, review_id)
    return _coerce_state(_row_get(review, "status"))


def get_required_items(db, review_id) -> List[Dict[str, Any]]:
    """Return the structured required items for a review (or [])."""
    review = _fetch_review(db, review_id)
    return _load_required_items(_row_get(review, "required_items"))


# ─────────────────────────────────────────────────────────────────
# State transitions
# ─────────────────────────────────────────────────────────────────
def transition_review_state(db, review_id, *, new_state: str,
                            reason: Optional[str] = None,
                            user=None, audit_writer=None) -> Dict[str, Any]:
    """Move a review from its current state to ``new_state``.

    Validates the transition against ``STATE_TRANSITIONS`` and persists
    both ``status`` and ``state_changed_at`` atomically. Emits a
    structured ``periodic_review.state_changed`` audit event.

    Refuses to transition a completed review. Raises:

    * ``InvalidReviewState`` if ``new_state`` is not in
      ``VALID_REVIEW_STATES``;
    * ``InvalidReviewTransition`` if the transition is not allowed
      from the review's current state;
    * ``ReviewClosedError`` if the review is already completed.

    NOTE: Use ``record_review_outcome`` to move a review to
    ``completed`` -- this helper deliberately refuses the terminal
    transition so completion always carries an explicit outcome.
    """
    _require_audit_writer(audit_writer)
    if new_state not in VALID_REVIEW_STATES:
        raise InvalidReviewState(
            f"new_state={new_state!r} is not one of {VALID_REVIEW_STATES}"
        )
    if new_state == STATE_COMPLETED:
        raise InvalidReviewTransition(
            "use record_review_outcome to move a review to 'completed'; "
            "completion must carry an explicit outcome"
        )

    review = _fetch_review(db, review_id)
    current_state = _coerce_state(_row_get(review, "status"))
    if current_state == STATE_COMPLETED:
        raise ReviewClosedError(
            f"periodic_review id={review_id} is already completed"
        )

    allowed = STATE_TRANSITIONS.get(current_state, ())
    if new_state not in allowed:
        raise InvalidReviewTransition(
            f"cannot transition periodic_review id={review_id} from "
            f"{current_state!r} to {new_state!r}; allowed: {allowed}"
        )

    ts = _utc_now_iso()
    before = {"status": current_state}
    db.execute(
        "UPDATE periodic_reviews "
        "SET status = ?, state_changed_at = ? "
        "WHERE id = ?",
        (new_state, ts, review_id),
    )
    db.commit()
    after = {"status": new_state, "state_changed_at": ts}
    payload = {"from": current_state, "to": new_state}
    if reason:
        payload["reason"] = reason
    _emit_audit(
        audit_writer, user, "periodic_review.state_changed",
        f"periodic_review:{review_id}", payload, db,
        before_state=before, after_state=after,
    )
    return {"review_id": review_id, "from": current_state, "to": new_state}


# ─────────────────────────────────────────────────────────────────
# Required-item generation
# ─────────────────────────────────────────────────────────────────
# Document staleness threshold (days) used by the structured generator.
# Kept conservative -- the goal is to produce explicit, testable signal
# rather than to enforce a regulatory cadence here. Real cadence lives
# in the rule engine and is intentionally out of scope for PR-03.
_DOCUMENT_STALENESS_DAYS = 365


def _doc_uploaded_at_dt(row) -> Optional[datetime]:
    raw = _row_get(row, "uploaded_at")
    if not raw:
        return None
    raw_str = str(raw)
    # Try common timestamp shapes used in the documents table. The
    # ``+ 4`` slice tolerates trailing fractional seconds or short
    # timezone suffixes that strptime cannot consume directly; the
    # fromisoformat() fallback handles everything else.
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw_str[: len(fmt) + 4], fmt).replace(
                tzinfo=timezone.utc,
            )
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw_str.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _merge_existing_item_state(items: List[Dict[str, Any]],
                               existing_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    existing_by_id = {it.get("id"): it for it in existing_items if it.get("id")}
    merged = []
    for item in items:
        prior = existing_by_id.get(item.get("id"))
        if prior:
            item["status"] = prior.get("status") or REQUIRED_ITEM_STATUS_OPEN
            item["officer_note"] = prior.get("officer_note")
            item["resolved_by"] = prior.get("resolved_by")
            item["resolved_at"] = prior.get("resolved_at")
        merged.append(item)
    return merged


def _screening_refresh_item(application: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    prescreening_raw = _row_get(application, "prescreening_data") or "{}"
    try:
        prescreening = json.loads(prescreening_raw) if isinstance(prescreening_raw, str) else (prescreening_raw or {})
    except (TypeError, ValueError):
        prescreening = {}
    screening_report = prescreening.get("screening_report") or {}
    validity_days = get_screening_validity_days()
    now = datetime.now(timezone.utc)

    label = "Review current screening status"
    severity = "low"
    rationale = "Stored screening validity is current."
    if not screening_report:
        label = "Run screening refresh"
        severity = "high"
        rationale = "No screening_report is stored in prescreening_data."
    else:
        valid_until = _parse_ts(prescreening.get("screening_valid_until"))
        if valid_until is None:
            screened_at = _parse_ts(
                screening_report.get("screened_at") or screening_report.get("timestamp")
            )
            valid_until = (
                screened_at + timedelta(days=validity_days)
                if screened_at is not None else None
            )
        if valid_until is None:
            label = "Run screening refresh"
            severity = "high"
            rationale = "Screening freshness cannot be determined from stored application data."
        elif valid_until < now:
            label = "Run screening refresh"
            severity = "high"
            rationale = f"Stored screening validity expired on {valid_until.date().isoformat()}."
        else:
            rationale = f"Stored screening validity remains current until {valid_until.date().isoformat()}."

    return _make_item(
        category=ITEM_CATEGORY_SCREENING_RISK,
        item_type="screening_refresh",
        label=label,
        severity=severity,
        source="application",
        source_id=_row_get(application, "id"),
        rationale=rationale,
    )


def _fetch_open_monitoring_alerts(db, application_id, *,
                                  include_document_health: bool) -> List[Any]:
    if not application_id:
        return []
    placeholders = ",".join("?" for _ in dhm.DOCUMENT_ALERT_TYPES)
    query = (
        "SELECT id, alert_type, severity, summary, status, resolved_at "
        "FROM monitoring_alerts WHERE application_id = ? "
    )
    params: List[Any] = [application_id]
    if include_document_health:
        query += f" AND alert_type IN ({placeholders})"
    else:
        query += f" AND alert_type NOT IN ({placeholders})"
    params.extend(list(dhm.DOCUMENT_ALERT_TYPES))
    rows = db.execute(query + " ORDER BY id ASC", params).fetchall()
    return [row for row in rows if mr.is_alert_unresolved(row)]


def _prior_completed_review(db, application_id, review_id):
    if not application_id:
        return None
    return db.execute(
        "SELECT id, risk_level, previous_risk_level, new_risk_level, outcome, decision "
        "FROM periodic_reviews WHERE application_id = ? AND id != ? "
        "AND status = 'completed' ORDER BY id DESC LIMIT 1",
        (application_id, review_id),
    ).fetchone()


def _generate_items_for_context(db, review: Dict[str, Any],
                                application: Optional[Dict[str, Any]],
                                ) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    application_id = _row_get(review, "application_id")
    review_id = _row_get(review, "id")
    risk_level = (
        _row_get(review, "risk_level")
        or (_row_get(application, "risk_level") if application else None)
        or "MEDIUM"
    )
    current_risk = _row_get(review, "risk_level") or _row_get(application, "risk_level")
    prior = _prior_completed_review(db, application_id, review_id)
    prior_risk = _row_get(prior, "new_risk_level") or _row_get(prior, "risk_level")

    items.append(_make_item(
        category=ITEM_CATEGORY_CLIENT_PROFILE,
        item_type="kyc_refresh",
        label="Confirm business activity and client profile unchanged",
        severity="medium",
        source="application",
        source_id=application_id,
        rationale="Baseline client profile review item.",
    ))
    items.append(_make_item(
        category=ITEM_CATEGORY_CLIENT_PROFILE,
        item_type="business_activity_review",
        label="Confirm business activity unchanged",
        severity="medium",
        source="application",
        source_id=application_id,
        rationale=(
            f"Recorded sector is {_row_get(application, 'sector') or 'not recorded'}; "
            "confirm no material business activity change."
        ),
    ))
    items.append(_make_item(
        category=ITEM_CATEGORY_CLIENT_PROFILE,
        item_type="ubo_confirmation",
        label="Confirm ownership / UBO structure unchanged",
        severity="medium",
        source="application",
        source_id=application_id,
        rationale="Baseline ownership review item.",
    ))
    items.append(_make_item(
        category=ITEM_CATEGORY_CLIENT_PROFILE,
        item_type="ownership_change_review",
        label="Confirm ownership structure unchanged",
        severity="medium",
        source="application",
        source_id=application_id,
        rationale="Recorded ownership structure should be re-confirmed during review.",
    ))
    items.append(_make_item(
        category=ITEM_CATEGORY_CLIENT_PROFILE,
        item_type="jurisdiction_review",
        label="Confirm jurisdiction exposure unchanged",
        severity="medium",
        source="application",
        source_id=application_id,
        rationale=(
            f"Recorded jurisdiction is "
            f"{_row_get(application, 'country') or _row_get(application, 'jurisdiction') or 'not recorded'}."
        ),
    ))

    linked_alert_id = _row_get(review, "linked_monitoring_alert_id")
    if _row_get(review, "trigger_source") == "monitoring_alert" and linked_alert_id is not None:
        items.append(_make_item(
            category=ITEM_CATEGORY_MONITORING_ALERTS,
            item_type="monitoring_alert_followup",
            label="Investigate monitoring alert that triggered this review",
            severity="medium",
            source="monitoring_alert",
            source_id=linked_alert_id,
            rationale=(
                f"Triggered by monitoring alert id={linked_alert_id}: "
                f"{_row_get(review, 'review_reason') or _row_get(review, 'trigger_reason') or 'no rationale recorded'}"
            ),
        ))

    for alert in _fetch_open_monitoring_alerts(db, application_id, include_document_health=True):
        items.append(_make_item(
            category=ITEM_CATEGORY_DOCUMENT_HEALTH,
            item_type=_row_get(alert, "alert_type") or "document_health",
            label=_row_get(alert, "summary") or "Review document health issue",
            severity=str(_row_get(alert, "severity") or "medium").lower(),
            source="monitoring_alert",
            source_id=_row_get(alert, "id"),
            rationale="Open document-health monitoring alert must be resolved in the review.",
        ))

    items.append(_screening_refresh_item(application))
    items.append(_make_item(
        category=ITEM_CATEGORY_SCREENING_RISK,
        item_type="risk_level_review",
        label=f"Review current risk level ({current_risk or 'unavailable'})",
        severity="low",
        source="review",
        source_id=review_id,
        rationale="Current risk level should be confirmed as part of the review.",
    ))
    if prior_risk and current_risk and prior_risk != current_risk:
        items.append(_make_item(
            category=ITEM_CATEGORY_SCREENING_RISK,
            item_type="risk_level_change_review",
            label=f"Assess risk level change from {prior_risk} to {current_risk}",
            severity="medium",
            source="periodic_review",
            source_id=_row_get(prior, "id"),
            rationale="Current risk differs from the prior completed periodic review.",
        ))
    if risk_level in ("HIGH", "VERY_HIGH"):
        items.append(_make_item(
            category=ITEM_CATEGORY_SCREENING_RISK,
            item_type="source_of_funds_refresh",
            label="Refresh source-of-funds evidence",
            severity="high",
            source="risk_level",
            source_id=risk_level,
            rationale=f"Risk tier {risk_level} requires a source-of-funds refresh.",
        ))
        items.append(_make_item(
            category=ITEM_CATEGORY_SCREENING_RISK,
            item_type="source_of_wealth_refresh",
            label="Refresh source-of-wealth evidence",
            severity="high",
            source="risk_level",
            source_id=risk_level,
            rationale=f"Risk tier {risk_level} requires a source-of-wealth refresh.",
        ))
    if risk_level == "VERY_HIGH":
        items.append(_make_item(
            category=ITEM_CATEGORY_SCREENING_RISK,
            item_type="licensing_refresh",
            label="Confirm current regulatory licence standing",
            severity="high",
            source="risk_level",
            source_id=risk_level,
            rationale="Very-high-risk review requires explicit licensing refresh.",
        ))

    for alert in _fetch_open_monitoring_alerts(db, application_id, include_document_health=False):
        items.append(_make_item(
            category=ITEM_CATEGORY_MONITORING_ALERTS,
            item_type="monitoring_alert_followup",
            label=_row_get(alert, "summary") or f"Review monitoring alert #{_row_get(alert, 'id')}",
            severity=str(_row_get(alert, "severity") or "medium").lower(),
            source="monitoring_alert",
            source_id=_row_get(alert, "id"),
            rationale=(
                f"Open monitoring alert type={_row_get(alert, 'alert_type') or 'unknown'} "
                "must be resolved or documented."
            ),
        ))
    prior_outcome = _row_get(prior, "outcome") or _row_get(prior, "decision")
    if prior_outcome in (
        OUTCOME_ENHANCED_MONITORING,
        OUTCOME_EDD_REQUIRED,
        "enhanced_monitoring",
        "request_info",
    ):
        items.append(_make_item(
            category=ITEM_CATEGORY_MONITORING_ALERTS,
            item_type="prior_outcome_followup",
            label="Follow up prior enhanced monitoring / EDD outcome",
            severity="medium",
            source="periodic_review",
            source_id=_row_get(prior, "id"),
            rationale=f"Previous review outcome was {prior_outcome}.",
        ))
    active_edd_id = _find_active_edd_for_application(db, application_id)
    if active_edd_id is not None:
        items.append(_make_item(
            category=ITEM_CATEGORY_MONITORING_ALERTS,
            item_type="edd_followup",
            label=f"Review open EDD follow-up (EDD #{active_edd_id})",
            severity="high",
            source="edd_case",
            source_id=active_edd_id,
            rationale="Active EDD follow-up exists for this application.",
        ))

    items.append(_make_item(
        category=ITEM_CATEGORY_FINAL_OUTCOME,
        item_type="review_outcome_recorded",
        label="Record periodic review outcome and rationale",
        severity="low",
        source="review",
        source_id=review_id,
        rationale="Periodic review must close with a documented outcome.",
    ))

    seen = set()
    deduped: List[Dict[str, Any]] = []
    for it in items:
        key = it.get("id")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)
    return deduped


def generate_required_items(db, review_id, *,
                            user=None, audit_writer=None) -> List[Dict[str, Any]]:
    """Generate the structured required-item list for a review.

    Persists the list as a JSON array on
    ``periodic_reviews.required_items`` and stamps
    ``required_items_generated_at``. Emits a structured
    ``periodic_review.required_items.generated`` audit event.

    Refuses to mutate a completed review.
    """
    _require_audit_writer(audit_writer)
    review = _fetch_review(db, review_id)
    if _coerce_state(_row_get(review, "status")) == STATE_COMPLETED:
        raise ReviewClosedError(
            f"cannot generate required items for completed review id={review_id}"
        )

    application = _fetch_application(db, _row_get(review, "application_id"))
    application_id = _row_get(review, "application_id")
    if application_id:
        dhm.sync_document_health_alerts_for_application(
            db,
            application_id,
            user=user,
            audit_writer=audit_writer,
        )
    items = _generate_items_for_context(db, review, application)
    items = _merge_existing_item_state(
        items,
        _load_required_items(_row_get(review, "required_items")),
    )
    payload = json.dumps(items, default=str)
    ts = _utc_now_iso()

    before = {
        "required_items": _row_get(review, "required_items"),
        "required_items_generated_at": _row_get(
            review, "required_items_generated_at"
        ),
    }
    db.execute(
        "UPDATE periodic_reviews "
        "SET required_items = ?, required_items_generated_at = ? "
        "WHERE id = ?",
        (payload, ts, review_id),
    )
    db.commit()
    after = {
        "required_items_generated_at": ts,
        "required_items_count": len(items),
        "required_items_codes": sorted({it.get("code") for it in items}),
    }
    _emit_audit(
        audit_writer, user, "periodic_review.required_items.generated",
        f"periodic_review:{review_id}",
        {
            "review_id": review_id,
            "count": len(items),
            "codes": after["required_items_codes"],
        },
        db, before_state=before, after_state=after,
    )
    return items


# ─────────────────────────────────────────────────────────────────
# Required-item updates
# ─────────────────────────────────────────────────────────────────
def update_required_item(db, review_id, item_id, *, status: str,
                         officer_note: Optional[str] = None,
                         user=None, audit_writer=None) -> Dict[str, Any]:
    _require_audit_writer(audit_writer)
    if status not in VALID_REQUIRED_ITEM_STATUSES:
        raise InvalidRequiredItemStatus(
            f"status={status!r} is not one of {VALID_REQUIRED_ITEM_STATUSES}"
        )
    if status == REQUIRED_ITEM_STATUS_NOT_APPLICABLE and not str(officer_note or "").strip():
        raise PeriodicReviewEngineError("officer_note is required when status='not_applicable'")

    review = _fetch_review(db, review_id)
    if _coerce_state(_row_get(review, "status")) == STATE_COMPLETED:
        raise ReviewClosedError(
            f"periodic_review id={review_id} is already completed"
        )

    items = _load_required_items(_row_get(review, "required_items"))
    updated_item = None
    ts = _utc_now_iso()
    for item in items:
        if str(item.get("id")) != str(item_id):
            continue
        if (
            item.get("source") == "monitoring_alert"
            and status in (
                REQUIRED_ITEM_STATUS_CLEARED,
                REQUIRED_ITEM_STATUS_NOT_APPLICABLE,
            )
        ):
            raise PeriodicReviewEngineError(
                "Linked monitoring-alert checklist items must be resolved via "
                "the monitoring alert workflow before they can be cleared."
            )
        if (
            item.get("item_type") == "edd_followup"
            and status in (
                REQUIRED_ITEM_STATUS_CLEARED,
                REQUIRED_ITEM_STATUS_NOT_APPLICABLE,
            )
        ):
            edd_case_id = item.get("source_id")
            edd_case = None
            if edd_case_id not in (None, ""):
                edd_case = db.execute(
                    "SELECT id, stage FROM edd_cases WHERE id = ?",
                    (edd_case_id,),
                ).fetchone()
            edd_stage = _row_get(edd_case, "stage")
            if edd_stage not in TERMINAL_EDD_STAGES and not str(officer_note or "").strip():
                raise PeriodicReviewEngineError(
                    "officer_note is required to clear an active EDD follow-up item"
                )
        before_state = dict(item)
        item["status"] = status
        item["officer_note"] = officer_note
        if status in (
            REQUIRED_ITEM_STATUS_CLEARED,
            REQUIRED_ITEM_STATUS_ESCALATED,
            REQUIRED_ITEM_STATUS_NOT_APPLICABLE,
        ):
            item["resolved_by"] = (user or {}).get("sub")
            item["resolved_at"] = ts
        else:
            item["resolved_by"] = None
            item["resolved_at"] = None
        updated_item = (before_state, dict(item))
        break
    if updated_item is None:
        raise RequiredItemNotFound(f"required item {item_id!r} not found on review {review_id}")

    db.execute(
        "UPDATE periodic_reviews SET required_items = ? WHERE id = ?",
        (json.dumps(items, default=str), review_id),
    )
    db.commit()
    before_state, after_state = updated_item
    _emit_audit(
        audit_writer,
        user,
        "periodic_review.required_item.updated",
        f"periodic_review:{review_id}",
        {
            "review_id": review_id,
            "item_id": item_id,
            "status": status,
        },
        db,
        before_state=before_state,
        after_state=after_state,
    )
    return after_state


def add_custom_required_item(db, review_id, *, label: str,
                             rationale: str,
                             severity: str = "high",
                             user=None, audit_writer=None) -> Dict[str, Any]:
    """Append a custom evidence requirement to ``required_items``.

    This is intentionally narrow: it adds an officer-defined evidence
    requirement onto the canonical periodic-review record without
    creating a separate request workflow or storage surface. The item
    participates in the same blocker/evidence-link model as the built-in
    documentary requirements.
    """
    _require_audit_writer(audit_writer)
    label = str(label or "").strip()
    rationale = str(rationale or "").strip()
    severity = str(severity or "high").strip().lower()
    if not label:
        raise PeriodicReviewEngineError("label is required")
    if not rationale:
        raise PeriodicReviewEngineError("rationale is required")
    if severity not in VALID_REQUIRED_ITEM_SEVERITIES:
        raise PeriodicReviewEngineError(
            f"severity={severity!r} is not one of {VALID_REQUIRED_ITEM_SEVERITIES}"
        )

    review = _fetch_review(db, review_id)
    if _coerce_state(_row_get(review, "status")) == STATE_COMPLETED:
        raise ReviewClosedError(
            f"periodic_review id={review_id} is already completed"
        )

    items = _load_required_items(_row_get(review, "required_items"))
    existing_count = sum(
        1 for item in items
        if str(item.get("item_type") or item.get("code") or "").strip()
        == "custom_evidence_requirement"
    )
    custom_item = _make_item(
        category=ITEM_CATEGORY_REQUIRED_EVIDENCE,
        item_type="custom_evidence_requirement",
        label=label,
        severity=severity,
        source="review",
        source_id=review_id,
        rationale=rationale,
        code="custom_evidence_requirement",
    )
    custom_item["id"] = f"custom_evidence_requirement:{review_id}:{existing_count + 1}"
    items.append(custom_item)

    before = {"required_items": _row_get(review, "required_items")}
    db.execute(
        "UPDATE periodic_reviews SET required_items = ? WHERE id = ?",
        (json.dumps(items, default=str), review_id),
    )
    db.commit()
    _emit_audit(
        audit_writer,
        user,
        "periodic_review.required_item.added",
        f"periodic_review:{review_id}",
        {
            "review_id": review_id,
            "item_id": custom_item["id"],
            "item_type": custom_item["item_type"],
            "label": custom_item["label"],
            "severity": custom_item["severity"],
        },
        db,
        before_state=before,
        after_state=custom_item,
    )
    return custom_item


def resolve_screening_refresh_item_if_current(db, review_id, *,
                                              user=None,
                                              audit_writer=None) -> Dict[str, Any]:
    """Clear the screening-refresh checklist item only after freshness is current."""
    _require_audit_writer(audit_writer)
    review = _fetch_review(db, review_id)
    if _coerce_state(_row_get(review, "status")) == STATE_COMPLETED:
        raise ReviewClosedError(
            f"periodic_review id={review_id} is already completed"
        )

    application = _fetch_application(db, _row_get(review, "application_id"))
    screening_item = _screening_refresh_item(application)
    if _severity_rank(screening_item.get("severity")) >= _severity_rank("high"):
        raise PeriodicReviewEngineError(
            "Screening refresh is still required: "
            + str(screening_item.get("rationale") or "freshness is not current")
        )

    items = _load_required_items(_row_get(review, "required_items"))
    if not items:
        items = generate_required_items(
            db, review_id, user=user, audit_writer=audit_writer,
        )
        review = _fetch_review(db, review_id)
        items = _load_required_items(_row_get(review, "required_items")) or items

    ts = _utc_now_iso()
    updated_item = None
    for item in items:
        if item.get("item_type") != "screening_refresh":
            continue
        before_state = dict(item)
        item["status"] = REQUIRED_ITEM_STATUS_CLEARED
        item["officer_note"] = screening_item.get("rationale")
        item["resolved_by"] = (user or {}).get("sub")
        item["resolved_at"] = ts
        updated_item = (before_state, dict(item))
        break
    if updated_item is None:
        raise RequiredItemNotFound(
            f"screening_refresh item not found on review {review_id}"
        )

    db.execute(
        "UPDATE periodic_reviews SET required_items = ? WHERE id = ?",
        (json.dumps(items, default=str), review_id),
    )
    db.commit()
    before_state, after_state = updated_item
    _emit_audit(
        audit_writer,
        user,
        "periodic_review.screening_refresh.resolved",
        f"periodic_review:{review_id}",
        {
            "review_id": review_id,
            "item_id": after_state.get("id"),
            "rationale": screening_item.get("rationale"),
        },
        db,
        before_state=before_state,
        after_state=after_state,
    )
    return after_state


# ─────────────────────────────────────────────────────────────────
# Escalation to EDD
# ─────────────────────────────────────────────────────────────────
def _find_active_edd_for_application(db, application_id):
    """Return the id of any active EDD case for the application, or None.

    Mirrors the predicate used by ``EDDCreateHandler.post`` in
    ``server.py`` and by ``monitoring_routing._find_active_edd_for_application``
    so duplicate-prevention semantics are consistent across all entry
    points (manual create, monitoring routing, periodic review
    escalation).
    """
    if application_id is None:
        return None
    placeholders = ",".join("?" for _ in TERMINAL_EDD_STAGES)
    row = db.execute(
        f"SELECT id FROM edd_cases "
        f"WHERE application_id = ? AND stage NOT IN ({placeholders}) "
        f"ORDER BY id ASC LIMIT 1",
        (application_id, *TERMINAL_EDD_STAGES),
    ).fetchone()
    return _row_get(row, "id") if row else None


def _create_edd_case_row(db, *, application_id, client_name, risk_level,
                         risk_score, assigned_officer, trigger_notes):
    """Insert an edd_cases row and return its id.

    Mirrors the INSERT shape used by ``EDDCreateHandler.post`` and by
    ``monitoring_routing._create_edd_case_row`` so EDD downstream
    behaviour is identical regardless of which entry point created the
    case. Trigger source is recorded as 'periodic_review' so it shows
    up in EDD lists alongside 'monitoring_alert' and 'officer_decision'.
    """
    initial_note = json.dumps([{
        "ts": _utc_now_iso(),
        "author": "periodic_review_engine",
        "note": trigger_notes or "EDD escalated from periodic review",
    }])
    insert_params = (
        application_id,
        client_name or "",
        risk_level or "HIGH",
        risk_score or 0,
        "triggered",
        assigned_officer or "",
        "periodic_review",
        trigger_notes or "EDD escalated from periodic review",
        initial_note,
    )
    try:
        from db import USE_POSTGRESQL as _USE_PG  # type: ignore
    except (ImportError, AttributeError):
        _USE_PG = False

    if _USE_PG:
        row = db.execute(
            "INSERT INTO edd_cases "
            "(application_id, client_name, risk_level, risk_score, "
            " stage, assigned_officer, trigger_source, trigger_notes, "
            " edd_notes) "
            "VALUES (?,?,?,?,?,?,?,?,?) RETURNING id",
            insert_params,
        ).fetchone()
        return _row_get(row, "id")
    db.execute(
        "INSERT INTO edd_cases "
        "(application_id, client_name, risk_level, risk_score, "
        " stage, assigned_officer, trigger_source, trigger_notes, "
        " edd_notes) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        insert_params,
    )
    return db.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()["id"]


def _link_review_to_edd(db, review_id, edd_case_id):
    """Soft-link a periodic review to an EDD case.

    The forward link is the review's ``linked_edd_case_id``; the
    reverse link is the EDD's ``linked_periodic_review_id``. This is
    explicit and additive, but note the PR-02 reverse-link displacement
    contract: ``edd_cases.linked_monitoring_alert_id`` and
    ``edd_cases.linked_periodic_review_id`` always point to the *most
    recent* originator. We deliberately do NOT clear the EDD's
    ``linked_monitoring_alert_id`` here -- the alert-side forward link
    is owned by ``monitoring_routing`` / ``lifecycle_linkage`` and
    asymmetry is part of the documented contract.
    """
    db.execute(
        "UPDATE periodic_reviews SET linked_edd_case_id = ? WHERE id = ?",
        (edd_case_id, review_id),
    )
    db.execute(
        "UPDATE edd_cases SET linked_periodic_review_id = ? WHERE id = ?",
        (review_id, edd_case_id),
    )
    db.commit()


def escalate_review_to_edd(db, review_id, *,
                           trigger_notes: Optional[str] = None,
                           priority: Optional[str] = None,
                           user=None, audit_writer=None) -> Dict[str, Any]:
    """Escalate a periodic review to a real EDD case.

    Behaviour:

    * If the review is already linked to an EDD case AND that case is
      not in a terminal stage, reuse it (``reused=True``, ``created=False``).
    * Else if any other active EDD case exists for the same
      application, link to it and reuse it. This matches the existing
      duplicate-prevention rule in ``EDDCreateHandler.post`` and in
      ``monitoring_routing.route_alert_to_edd``.
    * Else create a new ``edd_cases`` row, set its
      ``origin_context='periodic_review'`` and
      ``linked_periodic_review_id=review_id`` via PR-01
      ``lifecycle_linkage.set_edd_origin``, and bidirectionally link.
    * Emits ``periodic_review.escalated_to_edd`` with
      ``created`` / ``reused`` flags and the resolved ``edd_case_id``.

    Priority handling (PR-03a):

    * If ``priority`` is supplied (and is a member of
      ``lifecycle_linkage.VALID_PRIORITIES``), it is persisted to
      ``edd_cases.priority`` via ``lifecycle_linkage.mark_edd_assigned``
      regardless of whether the EDD case was created or reused. Prior
      to PR-03a, a ``priority`` argument was silently dropped on the
      reuse path; that hidden no-op is the bug PR-03a closes. Invalid
      priority values are rejected at the engine boundary as a
      ``PeriodicReviewEngineError`` (mapped to HTTP 400) before any DB
      writes occur. Passing ``priority=None`` remains a no-op and never
      overwrites the existing priority on a reused case.

    Refuses to escalate a completed review.

    NOTE on the PR-02 reverse-link displacement contract:
    ``edd_cases.linked_monitoring_alert_id`` is owned by the alert
    routing path and is not cleared here. Callers reading EDD reverse
    links must treat them as last-write-wins, never as symmetric to
    every alert/review that pointed at this EDD.
    """
    _require_audit_writer(audit_writer)
    # PR-03a: validate priority at the engine boundary so an invalid
    # value surfaces as a clean PeriodicReviewEngineError (mapped to 400
    # by the handler) instead of leaking through to ``mark_edd_assigned``
    # as an InvalidEnumValue and bubbling up as a 500. ``None`` is the
    # documented "do not change priority" sentinel and stays a no-op.
    if priority is not None and priority not in VALID_PRIORITIES:
        raise PeriodicReviewEngineError(
            "priority must be one of: "
            + ", ".join(VALID_PRIORITIES)
            + f"; got {priority!r}"
        )
    review = _fetch_review(db, review_id)
    if _coerce_state(_row_get(review, "status")) == STATE_COMPLETED:
        raise ReviewClosedError(
            f"cannot escalate completed review id={review_id}"
        )

    application_id = _row_get(review, "application_id")
    if application_id is None:
        raise PeriodicReviewEngineError(
            f"periodic_review id={review_id} has no application_id; "
            "cannot escalate to EDD"
        )
    client_name = _row_get(review, "client_name") or ""
    risk_level = _row_get(review, "risk_level")

    existing_link_id = _row_get(review, "linked_edd_case_id")
    created = False
    reused = False
    edd_case_id: Optional[int] = None

    if existing_link_id is not None:
        linked = db.execute(
            "SELECT id, stage FROM edd_cases WHERE id = ?",
            (existing_link_id,),
        ).fetchone()
        if linked and _row_get(linked, "stage") not in TERMINAL_EDD_STAGES:
            edd_case_id = existing_link_id
            reused = True

    if edd_case_id is None:
        active_id = _find_active_edd_for_application(db, application_id)
        if active_id is not None:
            edd_case_id = active_id
            _link_review_to_edd(db, review_id, edd_case_id)
            ll.set_edd_origin(
                db, edd_case_id,
                origin_context="periodic_review",
                linked_periodic_review_id=review_id,
                user=user, audit_writer=audit_writer,
            )
            reused = True

    if edd_case_id is None:
        edd_case_id = _create_edd_case_row(
            db,
            application_id=application_id,
            client_name=client_name,
            risk_level=risk_level,
            risk_score=None,
            assigned_officer=(user or {}).get("sub", ""),
            trigger_notes=trigger_notes,
        )
        _link_review_to_edd(db, review_id, edd_case_id)
        ll.set_edd_origin(
            db, edd_case_id,
            origin_context="periodic_review",
            linked_periodic_review_id=review_id,
            user=user, audit_writer=audit_writer,
        )
        created = True

    # PR-03a: persist priority into ``edd_cases.priority`` whenever the
    # caller supplied one, regardless of whether we created or reused
    # the EDD case. Previously this only ran on the create path, so a
    # ``priority`` argument was silently dropped on reuse. ``mark_edd_assigned``
    # uses ``COALESCE(?, priority)`` semantics: passing a non-NULL value
    # overwrites; we already gated with ``if priority`` so a None caller
    # never disturbs the existing priority on a reused case.
    if priority:
        ll.mark_edd_assigned(
            db, edd_case_id, priority=priority,
            user=user, audit_writer=audit_writer,
        )

    payload = {
        "review_id": review_id,
        "edd_case_id": edd_case_id,
        "created": created,
        "reused": reused,
    }
    if trigger_notes:
        payload["trigger_notes"] = trigger_notes
    _emit_audit(
        audit_writer, user, "periodic_review.escalated_to_edd",
        f"periodic_review:{review_id}", payload, db,
        before_state={"linked_edd_case_id": existing_link_id},
        after_state={"linked_edd_case_id": edd_case_id},
    )
    return {
        "review_id": review_id,
        "edd_case_id": edd_case_id,
        "created": created,
        "reused": reused,
    }


# ─────────────────────────────────────────────────────────────────
# Outcome recording (review completion)
# ─────────────────────────────────────────────────────────────────
def _auto_clear_outcome_item(items: List[Dict[str, Any]], *, outcome: str,
                             outcome_reason: str, user, ts: str) -> List[Dict[str, Any]]:
    for item in items:
        if item.get("item_type") != "review_outcome_recorded":
            continue
        item["status"] = REQUIRED_ITEM_STATUS_CLEARED
        item["officer_note"] = f"{outcome}: {outcome_reason}"
        item["resolved_by"] = (user or {}).get("sub")
        item["resolved_at"] = ts
    return items


def _blocking_items_for_completion(db, review, items, *, outcome: str,
                                   outcome_reason: str,
                                   enforce_prs5_gates: bool = False) -> List[Dict[str, Any]]:
    readiness = evaluate_review_readiness(
        db,
        review,
        required_items=items,
        outcome=outcome,
        outcome_reason=outcome_reason,
        include_periodic_review_closure_gates=enforce_prs5_gates,
    )
    return readiness["blocking_items_for_completion"]


def record_review_outcome(db, review_id, *,
                          outcome: str,
                          outcome_reason: Optional[str] = None,
                          findings_summary: Optional[str] = None,
                          rationale: Optional[str] = None,
                          risk_impact: Optional[str] = None,
                          risk_changed: Any = False,
                          new_risk_level: Optional[str] = None,
                          edd_required: Any = False,
                          follow_up_required: Any = False,
                          exit_recommended: Any = False,
                          follow_up_notes: Optional[str] = None,
                          senior_review_note: Optional[str] = None,
                          officer_acknowledgement: Any = False,
                          enforce_prs5_gates: bool = False,
                          user=None, audit_writer=None) -> Dict[str, Any]:
    """Close a periodic review with an explicit outcome.

    Writes ``outcome``, ``outcome_reason``, ``outcome_recorded_at``,
    sets ``status='completed'`` and ``completed_at``, and stamps
    ``closed_at`` via ``lifecycle_linkage.mark_review_closed`` so the
    PR-01 closure audit trail is preserved.

    PR-03 / PR-03a contract:

    * ``outcome`` (with ``outcome_reason`` + ``outcome_recorded_at``)
      is the **authoritative** field for new reads/writes against the
      PR-03 operating model. All new flows must read ``outcome``.
    * The legacy ``decision`` column is retained ONLY for backward
      compatibility with rows written by the pre-PR-03
      ``PeriodicReviewDecisionHandler``. New flows MUST NOT write
      ``decision`` and MUST NOT treat it as a co-authoritative source
      of truth alongside ``outcome``. This helper deliberately leaves
      ``decision`` untouched so dual-write drift cannot occur.

    Onboarding memo history (``compliance_memos``)
    is intentionally NOT touched -- onboarding memo identity is
    per-application per-version and remains separate from periodic
    review lifecycle context (see PR-01 design notes).

    Raises:

    * ``InvalidReviewOutcome`` if ``outcome`` is not in
      ``VALID_REVIEW_OUTCOMES``;
    * ``PeriodicReviewEngineError`` if ``outcome_reason`` is empty;
    * ``ReviewClosedError`` if the review is already completed
      (decision-replay protection, mirrors the C-03 fix in
      ``PeriodicReviewDecisionHandler``).
    """
    _require_audit_writer(audit_writer)
    if outcome not in VALID_REVIEW_OUTCOMES:
        raise InvalidReviewOutcome(
            f"outcome={outcome!r} is not one of {VALID_REVIEW_OUTCOMES}"
        )
    effective_reason = _clean_text(rationale or outcome_reason)
    if not effective_reason:
        raise PeriodicReviewEngineError("outcome_reason is required")
    review = _fetch_review(db, review_id)
    current_state = _coerce_state(_row_get(review, "status"))
    if current_state == STATE_COMPLETED:
        raise ReviewClosedError(
            f"periodic_review id={review_id} is already completed"
        )

    risk_changed_flag = _boolish(risk_changed) or outcome == OUTCOME_RISK_RATING_CHANGED
    edd_required_flag = _boolish(edd_required) or outcome == OUTCOME_EDD_REQUIRED
    follow_up_required_flag = _boolish(follow_up_required) or outcome == OUTCOME_CLIENT_FOLLOW_UP_REQUIRED
    exit_recommended_flag = _boolish(exit_recommended) or outcome == OUTCOME_EXIT_RECOMMENDED
    risk_impact_text = _clean_text(risk_impact)
    findings_text = _clean_text(findings_summary)
    follow_up_text = _clean_text(follow_up_notes)
    senior_note_text = _clean_text(senior_review_note)
    normalized_new_risk = _normalise_requested_risk_level(new_risk_level)
    strict_field_blockers: List[Dict[str, Any]] = []
    if enforce_prs5_gates:
        if not _boolish(officer_acknowledgement):
            strict_field_blockers.append(_completion_blocker(
                "officer_acknowledgement_required",
                "Officer acknowledgement is required",
                review_id,
            ))
        if risk_changed_flag and not normalized_new_risk:
            strict_field_blockers.append(_completion_blocker(
                "new_risk_level_required",
                "New risk level is required when risk changed",
                review_id,
            ))
        if risk_changed_flag and not risk_impact_text:
            strict_field_blockers.append(_completion_blocker(
                "risk_impact_required",
                "Risk impact explanation is required when risk changed",
                review_id,
            ))
        if edd_required_flag and not risk_impact_text:
            strict_field_blockers.append(_completion_blocker(
                "edd_rationale_required",
                "EDD rationale is required when EDD is required",
                review_id,
            ))
        if follow_up_required_flag and not follow_up_text:
            strict_field_blockers.append(_completion_blocker(
                "follow_up_note_required",
                "Follow-up note is required when client follow-up is required",
                review_id,
            ))
        if follow_up_required_flag:
            strict_field_blockers.append(_completion_blocker(
                "client_follow_up_open",
                "Client follow-up required must be resolved before closure",
                review_id,
            ))
        if exit_recommended_flag and not risk_impact_text:
            strict_field_blockers.append(_completion_blocker(
                "exit_rationale_required",
                "Exit/offboarding rationale is required when exit is recommended",
                review_id,
            ))

    application_id = _row_get(review, "application_id")
    if application_id:
        dhm.sync_document_health_alerts_for_application(
            db,
            application_id,
            user=user,
            audit_writer=audit_writer,
        )
    review_for_completion = dict(review)
    review_for_completion["officer_rationale"] = effective_reason
    review_for_completion["outcome"] = outcome
    review_for_completion["outcome_reason"] = effective_reason
    items = _load_required_items(_row_get(review, "required_items"))
    blocking_items = _blocking_items_for_completion(
        db,
        review_for_completion,
        items,
        outcome=outcome,
        outcome_reason=effective_reason,
        enforce_prs5_gates=enforce_prs5_gates,
    )
    blocking_items = [*strict_field_blockers, *blocking_items]
    if blocking_items:
        raise ReviewCompletionBlocked(blocking_items)

    ts = _utc_now_iso()
    completion_date = ts[:10]
    application = _fetch_application(db, application_id)
    policy = None
    policy_risk_level = normalized_new_risk if risk_changed_flag else (
        _row_get(review, "new_risk_level")
        or _row_get(review, "risk_level")
    )
    if application is not None:
        try:
            from periodic_review_policy import policy_snapshot_for_application
            policy = policy_snapshot_for_application(
                application,
                anchor_date=completion_date,
                override_risk_level=policy_risk_level,
            )
        except Exception:
            logger.exception("Periodic review policy calculation failed review_id=%s", review_id)
            policy = None
    next_review_date = (policy or {}).get("next_review_date") or _row_get(review, "next_review_date")
    due_date = (policy or {}).get("due_date") or next_review_date or _row_get(review, "due_date")
    frequency_months = (policy or {}).get("frequency_months") or _row_get(review, "frequency_months")
    calculation_basis = (policy or {}).get("calculation_basis") or _row_get(review, "calculation_basis")
    policy_version = (policy or {}).get("policy_version") or _row_get(review, "policy_version")
    risk_before = _row_get(review, "previous_risk_level") or _row_get(review, "risk_level")
    risk_after = normalized_new_risk if risk_changed_flag else (_row_get(review, "new_risk_level") or risk_before)
    risk_attestation = (
        "risk_change_required" if risk_changed_flag
        else ("risk_unchanged" if outcome == OUTCOME_RISK_RATING_UNCHANGED else _row_get(review, "risk_change_attestation"))
    )
    actor_id = (user or {}).get("sub") or (user or {}).get("id")
    items = _auto_clear_outcome_item(items, outcome=outcome, outcome_reason=effective_reason, user=user, ts=ts)
    before = {
        "status": current_state,
        "outcome": _row_get(review, "outcome"),
        "outcome_reason": _row_get(review, "outcome_reason"),
        "outcome_recorded_at": _row_get(review, "outcome_recorded_at"),
        "completed_at": _row_get(review, "completed_at"),
        "completed_by": _row_get(review, "decided_by"),
        "officer_rationale": _row_get(review, "officer_rationale"),
        "officer_findings_note": _row_get(review, "officer_findings_note"),
        "officer_deficiencies_note": _row_get(review, "officer_deficiencies_note"),
        "officer_internal_review_note": _row_get(review, "officer_internal_review_note"),
        "previous_risk_level": risk_before,
        "new_risk_level": _row_get(review, "new_risk_level"),
        "risk_change_attestation": _row_get(review, "risk_change_attestation"),
        "risk_rerate_reason": _row_get(review, "risk_rerate_reason"),
        "next_review_date": _row_get(review, "next_review_date"),
        "required_items": _row_get(review, "required_items"),
    }
    db.execute(
        "UPDATE periodic_reviews "
        "SET status = ?, "
        "    outcome = ?, "
        "    outcome_reason = ?, "
        "    outcome_recorded_at = ?, "
        "    completed_at = ?, "
        "    decided_by = ?, "
        "    officer_rationale = ?, "
        "    officer_findings_note = COALESCE(NULLIF(?, ''), officer_findings_note), "
        "    officer_deficiencies_note = COALESCE(NULLIF(?, ''), officer_deficiencies_note), "
        "    officer_internal_review_note = COALESCE(NULLIF(?, ''), officer_internal_review_note), "
        "    findings_updated_by = CASE WHEN NULLIF(?, '') IS NOT NULL OR NULLIF(?, '') IS NOT NULL OR NULLIF(?, '') IS NOT NULL THEN ? ELSE findings_updated_by END, "
        "    findings_updated_at = CASE WHEN NULLIF(?, '') IS NOT NULL OR NULLIF(?, '') IS NOT NULL OR NULLIF(?, '') IS NOT NULL THEN ? ELSE findings_updated_at END, "
        "    previous_risk_level = COALESCE(previous_risk_level, ?), "
        "    new_risk_level = COALESCE(?, new_risk_level), "
        "    risk_change_attestation = COALESCE(?, risk_change_attestation), "
        "    risk_rerate_reason = COALESCE(NULLIF(?, ''), risk_rerate_reason), "
        "    risk_rerated_by = CASE WHEN ? IS NOT NULL THEN ? ELSE risk_rerated_by END, "
        "    risk_rerated_at = CASE WHEN ? IS NOT NULL THEN ? ELSE risk_rerated_at END, "
        "    last_review_date = ?, "
        "    next_review_date = ?, "
        "    due_date = ?, "
        "    frequency_months = COALESCE(?, frequency_months), "
        "    calculation_basis = COALESCE(?, calculation_basis), "
        "    policy_version = COALESCE(?, policy_version), "
        "    state_changed_at = ?, "
        "    required_items = ? "
        # NB: ``decision`` is intentionally NOT included in this UPDATE.
        # PR-03a contract: ``outcome`` is the authoritative outcome
        # field for the periodic review operating model; ``decision``
        # remains read-only legacy state owned by the pre-PR-03
        # ``PeriodicReviewDecisionHandler``. Do not co-write both.
        "WHERE id = ?",
        (
            STATE_COMPLETED,
            outcome,
            effective_reason,
            ts,
            ts,
            actor_id,
            effective_reason,
            findings_text,
            follow_up_text,
            senior_note_text,
            findings_text,
            follow_up_text,
            senior_note_text,
            actor_id,
            findings_text,
            follow_up_text,
            senior_note_text,
            ts,
            risk_before,
            normalized_new_risk if risk_changed_flag else None,
            risk_attestation,
            risk_impact_text,
            actor_id if risk_changed_flag else None,
            actor_id,
            actor_id if risk_changed_flag else None,
            ts,
            completion_date,
            next_review_date,
            due_date,
            frequency_months,
            calculation_basis,
            policy_version,
            ts,
            json.dumps(items, default=str),
            review_id,
        ),
    )
    db.commit()
    # Stamp PR-01 closed_at + emit lifecycle.review.closed audit.
    ll.mark_review_closed(
        db, review_id, user=user, audit_writer=audit_writer,
    )
    after = {
        "status": STATE_COMPLETED,
        "outcome": outcome,
        "outcome_reason": effective_reason,
        "outcome_recorded_at": ts,
        "completed_at": ts,
        "completed_by": actor_id,
        "officer_rationale": effective_reason,
        "officer_findings_note": findings_text or _row_get(review, "officer_findings_note"),
        "officer_deficiencies_note": follow_up_text or _row_get(review, "officer_deficiencies_note"),
        "officer_internal_review_note": senior_note_text or _row_get(review, "officer_internal_review_note"),
        "previous_risk_level": risk_before,
        "new_risk_level": risk_after if risk_changed_flag else _row_get(review, "new_risk_level"),
        "risk_change_attestation": risk_attestation,
        "risk_rerate_reason": risk_impact_text or _row_get(review, "risk_rerate_reason"),
        "last_review_date": completion_date,
        "next_review_date": next_review_date,
        "due_date": due_date,
        "required_items": items,
    }
    _emit_audit(
        audit_writer, user, "periodic_review.outcome_recorded",
        f"periodic_review:{review_id}",
        {
            "review_id": review_id,
            "outcome": outcome,
            "from_state": current_state,
        },
        db, before_state=before, after_state=after,
    )
    _emit_audit(
        audit_writer, user, "periodic_review_completed",
        f"periodic_review:{review_id}",
        {
            "review_id": review_id,
            "application_id": application_id,
            "outcome": outcome,
            "risk_level_before": risk_before,
            "risk_level_after": risk_after,
            "risk_changed": risk_changed_flag,
            "next_review_date": next_review_date,
            "completed_by": actor_id,
        },
        db, before_state=before, after_state=after,
    )
    return {
        "review_id": review_id,
        "status": STATE_COMPLETED,
        "outcome": outcome,
        "outcome_reason": effective_reason,
        "outcome_recorded_at": ts,
        "completed_at": ts,
        "completed_by": actor_id,
        "next_review_date": next_review_date,
        "risk_level_before": risk_before,
        "risk_level_after": risk_after,
        "risk_changed": risk_changed_flag,
        "risk_governance_status": (
            "review_level_only_change_management_required"
            if risk_changed_flag else "unchanged"
        ),
    }


__all__ = [
    # State vocabulary
    "VALID_REVIEW_STATES",
    "STATE_TRANSITIONS",
    "STATE_PENDING",
    "STATE_IN_PROGRESS",
    "STATE_AWAITING_INFORMATION",
    "STATE_PENDING_SENIOR_REVIEW",
    "STATE_COMPLETED",
    # Outcome vocabulary
    "VALID_REVIEW_OUTCOMES",
    "OUTCOME_NO_CHANGE",
    "OUTCOME_ENHANCED_MONITORING",
    "OUTCOME_EDD_REQUIRED",
    "OUTCOME_EXIT_RECOMMENDED",
    "OUTCOME_NO_MATERIAL_CHANGE",
    "OUTCOME_MATERIAL_CHANGE_IDENTIFIED",
    "OUTCOME_RISK_RATING_UNCHANGED",
    "OUTCOME_RISK_RATING_CHANGED",
    "OUTCOME_CLIENT_FOLLOW_UP_REQUIRED",
    # Required-item vocabulary
    "REQUIRED_ITEM_CODES",
    # Exceptions
    "PeriodicReviewEngineError",
    "InvalidReviewState",
    "InvalidReviewOutcome",
    "InvalidReviewTransition",
    "ReviewNotFound",
    "ReviewClosedError",
    "InvalidRequiredItemStatus",
    "RequiredItemNotFound",
    "ReviewCompletionBlocked",
    # Public helpers
    "get_review_state",
    "get_required_items",
    "transition_review_state",
    "generate_required_items",
    "update_required_item",
    "add_custom_required_item",
    "resolve_screening_refresh_item_if_current",
    "escalate_review_to_edd",
    "record_review_outcome",
]
