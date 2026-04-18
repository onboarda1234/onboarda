"""
Lifecycle Linkage Helpers -- PR-01 Foundation
==============================================

Single entry point for cross-object linkage and lifecycle-timestamp
writes across edd_cases, periodic_reviews, and monitoring_alerts.

Design principles:
- Boring, explicit, low-blast-radius. No workflow engine.
- No business decisions inferred. Callers must pass the exact
  origin_context / trigger_source / priority they mean.
- Provider-agnostic. No screening, no Sumsub, no ComplyAdvantage.
- Uses the existing BaseHandler.log_audit contract via an injected
  audit_writer callable with the same signature. base_handler.py is
  NOT modified.
- Soft references only (PR-01). Helpers validate existence of the
  referenced IDs but do not create or promote DB-level foreign keys.
- Memo-pointer writes are intentionally NOT provided in PR-01.
  See docs/lifecycle_linkage_pr01.md for the deferred-decision
  rationale.

Audit-writer contract (PR-01):
- Every *mutating* helper in this module REQUIRES a non-None
  audit_writer. Omitting it raises MissingAuditWriter before any
  DB mutation occurs. This guarantees that no lifecycle state
  change can be persisted without a canonical audit path being
  available.
- The audit_writer signature mirrors BaseHandler.log_audit exactly.

Enum enforcement (PR-01):
- origin_context / trigger_source / priority are validated at the
  Python layer only. Neither SQLite nor PostgreSQL enforces these
  vocabularies at the DB level in PR-01. A later PR may add a
  PostgreSQL CHECK constraint.

All helpers are idempotent where it is safe to be and refuse to
proceed where it is not (linking a closed EDD, linking to a
non-existent row, writing a non-whitelisted enum value).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping, Optional

logger = logging.getLogger("arie.lifecycle_linkage")

# -- Enum vocabularies (application-layer source of truth) ----------
VALID_EDD_ORIGIN_CONTEXTS = (
    "onboarding",
    "periodic_review",
    "monitoring_alert",
    "change_request",
    "manual",
)

VALID_REVIEW_TRIGGER_SOURCES = (
    "schedule",
    "monitoring_alert",
    "change_request",
    "manual",
)

VALID_PRIORITIES = (
    "low",
    "normal",
    "high",
    "urgent",
)

# Stages at which an EDD is considered terminal. Taken verbatim from
# the existing edd_cases.stage CHECK constraint in db.py; intentionally
# duplicated here rather than imported to avoid coupling to protected
# module internals.
TERMINAL_EDD_STAGES = ("edd_approved", "edd_rejected")

# -- Exceptions -----------------------------------------------------
class LifecycleLinkageError(ValueError):
    """Base class for lifecycle-linkage validation failures."""


class InvalidEnumValue(LifecycleLinkageError):
    pass


class ReferencedRowNotFound(LifecycleLinkageError):
    pass


class InvalidLifecycleTransition(LifecycleLinkageError):
    pass


class MissingAuditWriter(LifecycleLinkageError):
    """Raised when a mutating helper is called without audit_writer.

    PR-01 policy: no lifecycle state change may be persisted unless
    the caller has supplied a canonical audit path. This is enforced
    BEFORE any DB write so that a missing audit_writer can never
    result in an un-audited mutation.
    """


# -- Internal utilities ---------------------------------------------
AuditWriter = Callable[..., None]
"""
Expected signature (mirrors BaseHandler.log_audit):

    audit_writer(user, action, target, detail,
                 db=None, before_state=None, after_state=None)
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_get(row, key):
    """Read a column from a sqlite3.Row or dict-like row, returning None."""
    if row is None:
        return None
    if hasattr(row, "get"):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _fetch_row(db, table, row_id):
    if row_id is None:
        return None
    cur = db.execute("SELECT * FROM " + table + " WHERE id = ?", (row_id,))
    row = cur.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        try:
            return {k: row[k] for k in row.keys()}
        except Exception:
            return None


def _assert_enum(value, allowed, field):
    if value is None:
        return
    if value not in allowed:
        raise InvalidEnumValue(
            f"{field}={value!r} is not one of {allowed}"
        )


def _assert_exists(db, table, row_id, label):
    if row_id is None:
        return None
    row = _fetch_row(db, table, row_id)
    if row is None:
        raise ReferencedRowNotFound(
            f"{label} id={row_id} does not exist in {table}"
        )
    return row


def _require_audit_writer(audit_writer):
    """Guard for every mutating helper.

    Raises MissingAuditWriter BEFORE any DB mutation when the caller
    omitted audit_writer. This is the single enforcement point for
    the PR-01 audit-writer contract.
    """
    if audit_writer is None:
        raise MissingAuditWriter(
            "lifecycle_linkage mutating helpers require a non-None "
            "audit_writer (canonical audit path). Refusing to mutate."
        )


def _detail(payload):
    try:
        return json.dumps(dict(payload), default=str, sort_keys=True)
    except (TypeError, ValueError):
        return json.dumps({"serialization_error": True})


def _emit_audit(audit_writer, user, action, target, detail_payload,
                db, before_state=None, after_state=None):
    """Write a structured audit row via the injected writer.

    A structured log line is always emitted in addition to the
    canonical audit_writer call, so that the event is reconstructable
    from application logs. audit_writer MUST be non-None when this
    function is called from a mutating helper (enforced upstream by
    _require_audit_writer).
    """
    user_dict = dict(user) if user else {}
    logger.info(
        "lifecycle_audit action=%s target=%s detail=%s",
        action, target, _detail(detail_payload),
    )
    if audit_writer is None:
        # Non-mutating callers (e.g. displacement no-op) may pass None.
        # Mutating helpers are guarded by _require_audit_writer.
        return
    try:
        audit_writer(
            user_dict, action, target, _detail(detail_payload),
            db=db, before_state=before_state, after_state=after_state,
        )
    except Exception:
        logger.exception("lifecycle audit write failed action=%s", action)


def _assert_edd_not_terminal(edd, edd_case_id):
    stage = _row_get(edd, "stage")
    if stage in TERMINAL_EDD_STAGES:
        raise InvalidLifecycleTransition(
            f"edd_case id={edd_case_id} is in terminal stage={stage!r}"
        )


# -- EDD case helpers -----------------------------------------------
def set_edd_origin(db, edd_case_id, *,
                   origin_context,
                   linked_monitoring_alert_id=None,
                   linked_periodic_review_id=None,
                   user=None,
                   audit_writer=None):
    """Record where an EDD came from.

    Requires a non-None audit_writer (raises MissingAuditWriter).
    """
    _require_audit_writer(audit_writer)
    _assert_enum(origin_context, VALID_EDD_ORIGIN_CONTEXTS, "origin_context")

    if origin_context == "monitoring_alert" and linked_monitoring_alert_id is None:
        raise LifecycleLinkageError(
            "origin_context='monitoring_alert' requires linked_monitoring_alert_id"
        )
    if origin_context == "periodic_review" and linked_periodic_review_id is None:
        raise LifecycleLinkageError(
            "origin_context='periodic_review' requires linked_periodic_review_id"
        )

    edd = _assert_exists(db, "edd_cases", edd_case_id, "edd_case")
    _assert_exists(db, "monitoring_alerts", linked_monitoring_alert_id, "monitoring_alert")
    _assert_exists(db, "periodic_reviews", linked_periodic_review_id, "periodic_review")

    before = {
        "origin_context": _row_get(edd, "origin_context"),
        "linked_monitoring_alert_id": _row_get(edd, "linked_monitoring_alert_id"),
        "linked_periodic_review_id": _row_get(edd, "linked_periodic_review_id"),
    }
    db.execute(
        "UPDATE edd_cases "
        "SET origin_context = ?, "
        "    linked_monitoring_alert_id = ?, "
        "    linked_periodic_review_id = ? "
        "WHERE id = ?",
        (origin_context, linked_monitoring_alert_id, linked_periodic_review_id, edd_case_id),
    )
    db.commit()
    after = {
        "origin_context": origin_context,
        "linked_monitoring_alert_id": linked_monitoring_alert_id,
        "linked_periodic_review_id": linked_periodic_review_id,
    }
    _emit_audit(audit_writer, user, "lifecycle.edd.origin_set",
                f"edd_case:{edd_case_id}", after, db,
                before_state=before, after_state=after)


def mark_edd_assigned(db, edd_case_id, *,
                      priority=None,
                      sla_due_at=None,
                      user=None,
                      audit_writer=None):
    """Set assigned_at / priority / sla_due_at on an EDD case.

    Requires a non-None audit_writer (raises MissingAuditWriter).
    """
    _require_audit_writer(audit_writer)
    _assert_enum(priority, VALID_PRIORITIES, "priority")
    edd = _assert_exists(db, "edd_cases", edd_case_id, "edd_case")
    _assert_edd_not_terminal(edd, edd_case_id)

    ts = _utc_now_iso()
    before = {
        "assigned_at": _row_get(edd, "assigned_at"),
        "priority": _row_get(edd, "priority"),
        "sla_due_at": _row_get(edd, "sla_due_at"),
    }
    db.execute(
        "UPDATE edd_cases "
        "SET assigned_at = COALESCE(assigned_at, ?), "
        "    priority    = COALESCE(?, priority), "
        "    sla_due_at  = COALESCE(?, sla_due_at) "
        "WHERE id = ?",
        (ts, priority, sla_due_at, edd_case_id),
    )
    db.commit()
    after = {"assigned_at": ts, "priority": priority, "sla_due_at": sla_due_at}
    _emit_audit(audit_writer, user, "lifecycle.edd.assigned",
                f"edd_case:{edd_case_id}", after, db,
                before_state=before, after_state=after)


def mark_edd_escalated(db, edd_case_id, *,
                       reason=None,
                       user=None,
                       audit_writer=None):
    """Set escalated_at on an EDD case.

    Requires a non-None audit_writer (raises MissingAuditWriter).
    """
    _require_audit_writer(audit_writer)
    edd = _assert_exists(db, "edd_cases", edd_case_id, "edd_case")
    _assert_edd_not_terminal(edd, edd_case_id)

    ts = _utc_now_iso()
    before = {"escalated_at": _row_get(edd, "escalated_at")}
    db.execute("UPDATE edd_cases SET escalated_at = ? WHERE id = ?", (ts, edd_case_id))
    db.commit()
    payload = {"timestamp": ts}
    if reason:
        payload["reason"] = reason
    _emit_audit(audit_writer, user, "lifecycle.edd.escalated",
                f"edd_case:{edd_case_id}", payload, db,
                before_state=before, after_state={"escalated_at": ts})


def mark_edd_closed(db, edd_case_id, *, user=None, audit_writer=None):
    """Set closed_at on an EDD case.

    Requires a non-None audit_writer (raises MissingAuditWriter).
    """
    _require_audit_writer(audit_writer)
    edd = _assert_exists(db, "edd_cases", edd_case_id, "edd_case")
    ts = _utc_now_iso()
    before = {"closed_at": _row_get(edd, "closed_at")}
    db.execute("UPDATE edd_cases SET closed_at = ? WHERE id = ?", (ts, edd_case_id))
    db.commit()
    _emit_audit(audit_writer, user, "lifecycle.edd.closed",
                f"edd_case:{edd_case_id}", {"timestamp": ts}, db,
                before_state=before, after_state={"closed_at": ts})


# -- Periodic review helpers ----------------------------------------
def set_periodic_review_trigger(db, review_id, *,
                                trigger_source,
                                review_reason=None,
                                linked_monitoring_alert_id=None,
                                linked_edd_case_id=None,
                                user=None,
                                audit_writer=None):
    """Record why a periodic review was triggered.

    Requires a non-None audit_writer (raises MissingAuditWriter).
    """
    _require_audit_writer(audit_writer)
    _assert_enum(trigger_source, VALID_REVIEW_TRIGGER_SOURCES, "trigger_source")

    if trigger_source == "monitoring_alert" and linked_monitoring_alert_id is None:
        raise LifecycleLinkageError(
            "trigger_source='monitoring_alert' requires linked_monitoring_alert_id"
        )

    review = _assert_exists(db, "periodic_reviews", review_id, "periodic_review")
    _assert_exists(db, "monitoring_alerts", linked_monitoring_alert_id, "monitoring_alert")
    _assert_exists(db, "edd_cases", linked_edd_case_id, "edd_case")

    before = {
        "trigger_source": _row_get(review, "trigger_source"),
        "review_reason": _row_get(review, "review_reason"),
        "linked_monitoring_alert_id": _row_get(review, "linked_monitoring_alert_id"),
        "linked_edd_case_id": _row_get(review, "linked_edd_case_id"),
    }
    db.execute(
        "UPDATE periodic_reviews "
        "SET trigger_source = ?, "
        "    review_reason = COALESCE(?, review_reason), "
        "    linked_monitoring_alert_id = ?, "
        "    linked_edd_case_id = ? "
        "WHERE id = ?",
        (trigger_source, review_reason, linked_monitoring_alert_id, linked_edd_case_id, review_id),
    )
    db.commit()
    after = {
        "trigger_source": trigger_source,
        "review_reason": review_reason,
        "linked_monitoring_alert_id": linked_monitoring_alert_id,
        "linked_edd_case_id": linked_edd_case_id,
    }
    _emit_audit(audit_writer, user, "lifecycle.review.trigger_set",
                f"periodic_review:{review_id}", after, db,
                before_state=before, after_state=after)


def mark_review_assigned(db, review_id, *,
                         priority=None,
                         sla_due_at=None,
                         user=None,
                         audit_writer=None):
    """Set assigned_at / priority / sla_due_at on a periodic review.

    Requires a non-None audit_writer (raises MissingAuditWriter).
    """
    _require_audit_writer(audit_writer)
    _assert_enum(priority, VALID_PRIORITIES, "priority")
    review = _assert_exists(db, "periodic_reviews", review_id, "periodic_review")
    if _row_get(review, "closed_at") is not None:
        raise InvalidLifecycleTransition(
            f"periodic_review id={review_id} is already closed"
        )
    ts = _utc_now_iso()
    before = {
        "assigned_at": _row_get(review, "assigned_at"),
        "priority": _row_get(review, "priority"),
        "sla_due_at": _row_get(review, "sla_due_at"),
    }
    db.execute(
        "UPDATE periodic_reviews "
        "SET assigned_at = COALESCE(assigned_at, ?), "
        "    priority    = COALESCE(?, priority), "
        "    sla_due_at  = COALESCE(?, sla_due_at) "
        "WHERE id = ?",
        (ts, priority, sla_due_at, review_id),
    )
    db.commit()
    after = {"assigned_at": ts, "priority": priority, "sla_due_at": sla_due_at}
    _emit_audit(audit_writer, user, "lifecycle.review.assigned",
                f"periodic_review:{review_id}", after, db,
                before_state=before, after_state=after)


def mark_review_closed(db, review_id, *, user=None, audit_writer=None):
    """Set closed_at on a periodic review.

    Requires a non-None audit_writer (raises MissingAuditWriter).
    """
    _require_audit_writer(audit_writer)
    review = _assert_exists(db, "periodic_reviews", review_id, "periodic_review")
    ts = _utc_now_iso()
    before = {"closed_at": _row_get(review, "closed_at")}
    db.execute("UPDATE periodic_reviews SET closed_at = ? WHERE id = ?", (ts, review_id))
    db.commit()
    _emit_audit(audit_writer, user, "lifecycle.review.closed",
                f"periodic_review:{review_id}", {"timestamp": ts}, db,
                before_state=before, after_state={"closed_at": ts})


# -- Monitoring alert helpers ---------------------------------------
def mark_alert_triaged(db, alert_id, *, user=None, audit_writer=None):
    """Set triaged_at on a monitoring alert.

    Requires a non-None audit_writer (raises MissingAuditWriter).
    """
    _require_audit_writer(audit_writer)
    alert = _assert_exists(db, "monitoring_alerts", alert_id, "monitoring_alert")
    ts = _utc_now_iso()
    before = {"triaged_at": _row_get(alert, "triaged_at")}
    db.execute(
        "UPDATE monitoring_alerts SET triaged_at = COALESCE(triaged_at, ?) WHERE id = ?",
        (ts, alert_id),
    )
    db.commit()
    _emit_audit(audit_writer, user, "lifecycle.alert.triaged",
                f"monitoring_alert:{alert_id}", {"timestamp": ts}, db,
                before_state=before, after_state={"triaged_at": ts})


def mark_alert_assigned(db, alert_id, *, user=None, audit_writer=None):
    """Set assigned_at on a monitoring alert.

    Requires a non-None audit_writer (raises MissingAuditWriter).
    """
    _require_audit_writer(audit_writer)
    alert = _assert_exists(db, "monitoring_alerts", alert_id, "monitoring_alert")
    if _row_get(alert, "resolved_at") is not None:
        raise InvalidLifecycleTransition(
            f"monitoring_alert id={alert_id} is already resolved"
        )
    ts = _utc_now_iso()
    before = {"assigned_at": _row_get(alert, "assigned_at")}
    db.execute(
        "UPDATE monitoring_alerts SET assigned_at = COALESCE(assigned_at, ?) WHERE id = ?",
        (ts, alert_id),
    )
    db.commit()
    _emit_audit(audit_writer, user, "lifecycle.alert.assigned",
                f"monitoring_alert:{alert_id}", {"timestamp": ts}, db,
                before_state=before, after_state={"assigned_at": ts})


def mark_alert_resolved(db, alert_id, *, user=None, audit_writer=None):
    """Set resolved_at on a monitoring alert.

    Requires a non-None audit_writer (raises MissingAuditWriter).
    """
    _require_audit_writer(audit_writer)
    alert = _assert_exists(db, "monitoring_alerts", alert_id, "monitoring_alert")
    ts = _utc_now_iso()
    before = {"resolved_at": _row_get(alert, "resolved_at")}
    db.execute("UPDATE monitoring_alerts SET resolved_at = ? WHERE id = ?", (ts, alert_id))
    db.commit()
    _emit_audit(audit_writer, user, "lifecycle.alert.resolved",
                f"monitoring_alert:{alert_id}", {"timestamp": ts}, db,
                before_state=before, after_state={"resolved_at": ts})


# -- Cross-object link helpers --------------------------------------
def link_alert_to_edd(db, alert_id, edd_case_id, *,
                      user=None, audit_writer=None):
    """Bidirectionally soft-link an alert and an EDD case.

    If the alert is already linked to a DIFFERENT EDD case, the old
    EDD's reverse pointer (linked_monitoring_alert_id) is cleared in
    the same transactional unit as the new link write, and a
    displacement audit event ('lifecycle.link.alert_to_edd.removed')
    is emitted for the old edge before the creation event for the
    new edge.

    Requires a non-None audit_writer (raises MissingAuditWriter).
    """
    _require_audit_writer(audit_writer)
    alert = _assert_exists(db, "monitoring_alerts", alert_id, "monitoring_alert")
    edd = _assert_exists(db, "edd_cases", edd_case_id, "edd_case")
    _assert_edd_not_terminal(edd, edd_case_id)

    prior_edd_id = _row_get(alert, "linked_edd_case_id")
    displaced = prior_edd_id is not None and prior_edd_id != edd_case_id

    before = {
        "alert.linked_edd_case_id": prior_edd_id,
        "edd.linked_monitoring_alert_id": _row_get(edd, "linked_monitoring_alert_id"),
    }

    # Clear the old EDD's reverse pointer so we do not leave a
    # dangling reference when re-linking to a different EDD.
    if displaced:
        db.execute(
            "UPDATE edd_cases SET linked_monitoring_alert_id = NULL "
            "WHERE id = ? AND linked_monitoring_alert_id = ?",
            (prior_edd_id, alert_id),
        )

    db.execute(
        "UPDATE monitoring_alerts SET linked_edd_case_id = ? WHERE id = ?",
        (edd_case_id, alert_id),
    )
    db.execute(
        "UPDATE edd_cases SET linked_monitoring_alert_id = ? WHERE id = ?",
        (alert_id, edd_case_id),
    )
    db.commit()

    after = {
        "alert.linked_edd_case_id": edd_case_id,
        "edd.linked_monitoring_alert_id": alert_id,
    }

    if displaced:
        _emit_audit(audit_writer, user,
                    "lifecycle.link.alert_to_edd.removed",
                    f"monitoring_alert:{alert_id}",
                    {"alert_id": alert_id,
                     "previous_edd_case_id": prior_edd_id,
                     "displaced_by_relink_to": edd_case_id},
                    db,
                    before_state={"alert.linked_edd_case_id": prior_edd_id},
                    after_state={"alert.linked_edd_case_id": edd_case_id})

    _emit_audit(audit_writer, user, "lifecycle.link.alert_to_edd.created",
                f"monitoring_alert:{alert_id}",
                {"alert_id": alert_id, "edd_case_id": edd_case_id},
                db, before_state=before, after_state=after)


def unlink_alert_from_edd(db, alert_id, *, user=None, audit_writer=None):
    """Break any existing soft-link between an alert and an EDD case.

    If the alert is not currently linked to any EDD, this is a no-op
    and NO audit event is emitted (to avoid misleading '.removed'
    noise). Requires a non-None audit_writer (raises MissingAuditWriter).
    """
    _require_audit_writer(audit_writer)
    alert = _assert_exists(db, "monitoring_alerts", alert_id, "monitoring_alert")
    prior_edd_id = _row_get(alert, "linked_edd_case_id")
    if prior_edd_id is None:
        # No-op: do not emit a misleading '.removed' event.
        return

    before = {"alert.linked_edd_case_id": prior_edd_id}
    db.execute(
        "UPDATE monitoring_alerts SET linked_edd_case_id = NULL WHERE id = ?",
        (alert_id,),
    )
    db.execute(
        "UPDATE edd_cases SET linked_monitoring_alert_id = NULL "
        "WHERE id = ? AND linked_monitoring_alert_id = ?",
        (prior_edd_id, alert_id),
    )
    db.commit()
    _emit_audit(audit_writer, user, "lifecycle.link.alert_to_edd.removed",
                f"monitoring_alert:{alert_id}",
                {"alert_id": alert_id, "previous_edd_case_id": prior_edd_id},
                db, before_state=before,
                after_state={"alert.linked_edd_case_id": None})


def link_alert_to_review(db, alert_id, review_id, *,
                         user=None, audit_writer=None):
    """Bidirectionally soft-link an alert and a periodic review.

    If the alert is already linked to a DIFFERENT periodic review,
    the old review's reverse pointer is cleared in the same
    transactional unit as the new link write, and a displacement
    audit event is emitted for the old edge before the creation
    event for the new edge.

    Requires a non-None audit_writer (raises MissingAuditWriter).
    """
    _require_audit_writer(audit_writer)
    alert = _assert_exists(db, "monitoring_alerts", alert_id, "monitoring_alert")
    review = _assert_exists(db, "periodic_reviews", review_id, "periodic_review")
    if _row_get(review, "closed_at") is not None:
        raise InvalidLifecycleTransition(
            f"cannot link alert to closed periodic_review id={review_id}"
        )

    prior_review_id = _row_get(alert, "linked_periodic_review_id")
    displaced = prior_review_id is not None and prior_review_id != review_id

    before = {
        "alert.linked_periodic_review_id": prior_review_id,
        "review.linked_monitoring_alert_id": _row_get(review, "linked_monitoring_alert_id"),
    }

    if displaced:
        db.execute(
            "UPDATE periodic_reviews SET linked_monitoring_alert_id = NULL "
            "WHERE id = ? AND linked_monitoring_alert_id = ?",
            (prior_review_id, alert_id),
        )

    db.execute(
        "UPDATE monitoring_alerts SET linked_periodic_review_id = ? WHERE id = ?",
        (review_id, alert_id),
    )
    db.execute(
        "UPDATE periodic_reviews SET linked_monitoring_alert_id = ? WHERE id = ?",
        (alert_id, review_id),
    )
    db.commit()

    after = {
        "alert.linked_periodic_review_id": review_id,
        "review.linked_monitoring_alert_id": alert_id,
    }

    if displaced:
        _emit_audit(audit_writer, user,
                    "lifecycle.link.alert_to_review.removed",
                    f"monitoring_alert:{alert_id}",
                    {"alert_id": alert_id,
                     "previous_periodic_review_id": prior_review_id,
                     "displaced_by_relink_to": review_id},
                    db,
                    before_state={"alert.linked_periodic_review_id": prior_review_id},
                    after_state={"alert.linked_periodic_review_id": review_id})

    _emit_audit(audit_writer, user, "lifecycle.link.alert_to_review.created",
                f"monitoring_alert:{alert_id}",
                {"alert_id": alert_id, "periodic_review_id": review_id},
                db, before_state=before, after_state=after)


def unlink_alert_from_review(db, alert_id, *, user=None, audit_writer=None):
    """Break any existing soft-link between an alert and a review.

    If the alert is not currently linked to any review, this is a
    no-op and NO audit event is emitted (to avoid misleading
    '.removed' noise). Requires a non-None audit_writer (raises
    MissingAuditWriter).
    """
    _require_audit_writer(audit_writer)
    alert = _assert_exists(db, "monitoring_alerts", alert_id, "monitoring_alert")
    prior_review_id = _row_get(alert, "linked_periodic_review_id")
    if prior_review_id is None:
        # No-op: do not emit a misleading '.removed' event.
        return

    before = {"alert.linked_periodic_review_id": prior_review_id}
    db.execute(
        "UPDATE monitoring_alerts SET linked_periodic_review_id = NULL WHERE id = ?",
        (alert_id,),
    )
    db.execute(
        "UPDATE periodic_reviews SET linked_monitoring_alert_id = NULL "
        "WHERE id = ? AND linked_monitoring_alert_id = ?",
        (prior_review_id, alert_id),
    )
    db.commit()
    _emit_audit(audit_writer, user, "lifecycle.link.alert_to_review.removed",
                f"monitoring_alert:{alert_id}",
                {"alert_id": alert_id, "previous_periodic_review_id": prior_review_id},
                db, before_state=before,
                after_state={"alert.linked_periodic_review_id": None})
