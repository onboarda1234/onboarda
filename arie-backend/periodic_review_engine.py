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
  (``memo_handler.py`` is protected and untouched)
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
This module is additive and lives outside ``PROTECTED_FILES``. It does
not modify any protected file. It reuses ``lifecycle_linkage`` (PR-01)
for the bidirectional alert/EDD/review linkage it needs and reuses the
existing duplicate-prevention predicate for active EDD lookup. No
EX-01..EX-13 control surface is touched.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

import lifecycle_linkage as ll
from lifecycle_linkage import (
    InvalidLifecycleTransition,
    LifecycleLinkageError,
    MissingAuditWriter,
    ReferencedRowNotFound,
    _row_get,  # internal but stable helper -- mirror PR-01 conventions
)

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

VALID_REVIEW_OUTCOMES = (
    OUTCOME_NO_CHANGE,
    OUTCOME_ENHANCED_MONITORING,
    OUTCOME_EDD_REQUIRED,
    OUTCOME_EXIT_RECOMMENDED,
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
    "ownership_change_review",
    "business_activity_review",
    "jurisdiction_review",
    "source_of_funds_refresh",
    "source_of_wealth_refresh",
    "licensing_refresh",
    "document_expiry_refresh",
    "monitoring_alert_followup",
    "prior_outcome_followup",
)

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


def _coerce_state(value: Optional[str]) -> str:
    """Normalise the stored status value to a known state.

    Reviews created before PR-03 only ever stored 'pending' or
    'completed'. Anything else is treated as the legacy default of
    'pending' so the new state machine has a deterministic anchor.
    """
    if value in VALID_REVIEW_STATES:
        return value
    return STATE_PENDING


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
    raw = _row_get(review, "required_items")
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return items if isinstance(items, list) else []


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


def _generate_items_for_context(db, review: Dict[str, Any],
                                application: Optional[Dict[str, Any]],
                                ) -> List[Dict[str, Any]]:
    """Pure-ish helper that produces the required-items list.

    Reads from the same DB connection but performs no writes. The
    selection logic is intentionally small and deterministic so it is
    easy to test and easy to extend without a generic workflow engine.
    """
    items: List[Dict[str, Any]] = []

    risk_level = (
        _row_get(review, "risk_level")
        or (_row_get(application, "risk_level") if application else None)
    )
    trigger_source = _row_get(review, "trigger_source")
    review_reason = _row_get(review, "review_reason") or _row_get(
        review, "trigger_reason"
    )
    application_id = _row_get(review, "application_id")
    linked_alert_id = _row_get(review, "linked_monitoring_alert_id")

    # --- 1. Baseline KYC + UBO refresh ---
    items.append({
        "code": "kyc_refresh",
        "label": "Refresh client KYC pack",
        "rationale": "Baseline periodic review item",
    })
    items.append({
        "code": "ubo_confirmation",
        "label": "Confirm UBO and control structure unchanged",
        "rationale": "Baseline periodic review item",
    })

    # --- 2. Risk-tier driven items ---
    if risk_level in ("HIGH", "VERY_HIGH"):
        items.append({
            "code": "source_of_funds_refresh",
            "label": "Refresh source-of-funds evidence",
            "rationale": f"Risk tier {risk_level} requires SoF refresh",
        })
        items.append({
            "code": "source_of_wealth_refresh",
            "label": "Refresh source-of-wealth evidence",
            "rationale": f"Risk tier {risk_level} requires SoW refresh",
        })
    if risk_level == "VERY_HIGH":
        items.append({
            "code": "licensing_refresh",
            "label": "Confirm licensing / regulatory standing",
            "rationale": "VERY_HIGH risk tier requires licensing refresh",
        })

    # --- 3. Jurisdiction context (if available on application) ---
    if application is not None:
        jurisdiction = _row_get(application, "country") or _row_get(
            application, "jurisdiction"
        )
        if jurisdiction:
            items.append({
                "code": "jurisdiction_review",
                "label": "Confirm jurisdictional exposure unchanged",
                "rationale": (
                    f"Application registered in {jurisdiction}; confirm no "
                    "jurisdiction change since last review"
                ),
            })
        sector = _row_get(application, "sector")
        if sector:
            items.append({
                "code": "business_activity_review",
                "label": "Confirm business activity unchanged",
                "rationale": (
                    f"Recorded sector: {sector}; confirm activity unchanged"
                ),
            })
        ownership_struct = _row_get(application, "ownership_structure")
        if ownership_struct:
            items.append({
                "code": "ownership_change_review",
                "label": "Confirm ownership structure unchanged",
                "rationale": "Ownership structure recorded; confirm no change",
            })

    # --- 4. Document staleness ---
    if application_id:
        try:
            doc_rows = db.execute(
                "SELECT doc_type, doc_name, uploaded_at "
                "FROM documents WHERE application_id = ?",
                (application_id,),
            ).fetchall()
        except Exception:
            doc_rows = []
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(days=_DOCUMENT_STALENESS_DAYS)
        ).timestamp()
        stale_types = []
        for doc in doc_rows:
            uploaded = _doc_uploaded_at_dt(doc)
            if uploaded is None:
                continue
            if uploaded.timestamp() < cutoff:
                doc_type = _row_get(doc, "doc_type") or "unknown"
                if doc_type not in stale_types:
                    stale_types.append(doc_type)
        if stale_types:
            items.append({
                "code": "document_expiry_refresh",
                "label": "Refresh stale supporting documents",
                "rationale": (
                    "Stale documents detected: "
                    + ", ".join(sorted(stale_types))
                ),
            })

    # --- 5. Monitoring-alert origin context (PR-02 contract) ---
    if trigger_source == "monitoring_alert" and linked_alert_id is not None:
        items.append({
            "code": "monitoring_alert_followup",
            "label": "Investigate monitoring alert that triggered this review",
            "rationale": (
                f"Triggered by monitoring alert id={linked_alert_id}: "
                + (review_reason or "no rationale recorded")
            ),
        })

    # --- 6. Prior outcome follow-up ---
    if application_id:
        try:
            prior = db.execute(
                "SELECT outcome, decision FROM periodic_reviews "
                "WHERE application_id = ? AND id != ? "
                "AND status = 'completed' "
                "ORDER BY id DESC LIMIT 1",
                (application_id, _row_get(review, "id")),
            ).fetchone()
        except Exception:
            prior = None
        prior_outcome = _row_get(prior, "outcome") or _row_get(
            prior, "decision"
        )
        if prior_outcome and prior_outcome in (
            OUTCOME_ENHANCED_MONITORING,
            OUTCOME_EDD_REQUIRED,
            "enhanced_monitoring",
            "request_info",
        ):
            items.append({
                "code": "prior_outcome_followup",
                "label": "Follow up on prior review outcome",
                "rationale": (
                    f"Previous review concluded with outcome={prior_outcome}; "
                    "confirm follow-up actions are complete"
                ),
            })

    # Deduplicate by (code, label) preserving insertion order.
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for it in items:
        key = (it.get("code"), it.get("label"))
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
    items = _generate_items_for_context(db, review, application)
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

    Refuses to escalate a completed review.

    NOTE on the PR-02 reverse-link displacement contract:
    ``edd_cases.linked_monitoring_alert_id`` is owned by the alert
    routing path and is not cleared here. Callers reading EDD reverse
    links must treat them as last-write-wins, never as symmetric to
    every alert/review that pointed at this EDD.
    """
    _require_audit_writer(audit_writer)
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
        if priority:
            ll.mark_edd_assigned(
                db, edd_case_id, priority=priority,
                user=user, audit_writer=audit_writer,
            )
        created = True

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
def record_review_outcome(db, review_id, *,
                          outcome: str,
                          outcome_reason: str,
                          user=None, audit_writer=None) -> Dict[str, Any]:
    """Close a periodic review with an explicit outcome.

    Writes ``outcome``, ``outcome_reason``, ``outcome_recorded_at``,
    sets ``status='completed'`` and ``completed_at``, and stamps
    ``closed_at`` via ``lifecycle_linkage.mark_review_closed`` so the
    PR-01 closure audit trail is preserved.

    The legacy ``decision`` column is left untouched (it is only
    written by the legacy ``PeriodicReviewDecisionHandler``); ``outcome``
    is the new source of truth. Onboarding memo history (``compliance_memos``)
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
    if not outcome_reason or not str(outcome_reason).strip():
        raise PeriodicReviewEngineError("outcome_reason is required")

    review = _fetch_review(db, review_id)
    current_state = _coerce_state(_row_get(review, "status"))
    if current_state == STATE_COMPLETED:
        raise ReviewClosedError(
            f"periodic_review id={review_id} is already completed"
        )

    ts = _utc_now_iso()
    before = {
        "status": current_state,
        "outcome": _row_get(review, "outcome"),
        "outcome_reason": _row_get(review, "outcome_reason"),
        "outcome_recorded_at": _row_get(review, "outcome_recorded_at"),
        "completed_at": _row_get(review, "completed_at"),
    }
    db.execute(
        "UPDATE periodic_reviews "
        "SET status = ?, "
        "    outcome = ?, "
        "    outcome_reason = ?, "
        "    outcome_recorded_at = ?, "
        "    completed_at = ?, "
        "    state_changed_at = ? "
        "WHERE id = ?",
        (STATE_COMPLETED, outcome, outcome_reason, ts, ts, ts, review_id),
    )
    db.commit()
    # Stamp PR-01 closed_at + emit lifecycle.review.closed audit.
    ll.mark_review_closed(
        db, review_id, user=user, audit_writer=audit_writer,
    )
    after = {
        "status": STATE_COMPLETED,
        "outcome": outcome,
        "outcome_reason": outcome_reason,
        "outcome_recorded_at": ts,
        "completed_at": ts,
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
    return {
        "review_id": review_id,
        "status": STATE_COMPLETED,
        "outcome": outcome,
        "outcome_recorded_at": ts,
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
    # Required-item vocabulary
    "REQUIRED_ITEM_CODES",
    # Exceptions
    "PeriodicReviewEngineError",
    "InvalidReviewState",
    "InvalidReviewOutcome",
    "InvalidReviewTransition",
    "ReviewNotFound",
    "ReviewClosedError",
    # Public helpers
    "get_review_state",
    "get_required_items",
    "transition_review_state",
    "generate_required_items",
    "escalate_review_to_edd",
    "record_review_outcome",
]
