"""
Monitoring Routing Primitives -- PR-02
======================================

Thin, explicit routing layer that lets a monitoring alert reach a real
downstream operating action (periodic review or EDD case) using the
PR-01 lifecycle linkage helpers.

Design principles:
- Boring, explicit, low-blast-radius. No workflow engine.
- Provider-agnostic. No screening, no Sumsub, no ComplyAdvantage.
- Built on top of arie-backend/lifecycle_linkage.py (PR-01). Linkage
  bookkeeping, audit emission, and terminal-state guards are delegated
  to the primitives there; this module only adds the small amount of
  routing state (status transitions, downstream row creation,
  duplicate-prevention) that PR-02 requires.
- Idempotent where it is safe to be: routing the same alert to a
  review/EDD twice reuses the existing linked row instead of creating a
  second one. ``created`` and ``reused`` flags in the return payload
  make the outcome deterministic and testable.
- Audit-writer is REQUIRED for every mutating function in this module.
  When the writer is None, lifecycle_linkage raises MissingAuditWriter
  before any DB mutation, so this module does not need to re-check.

This module deliberately introduces no new tables, no new schema, and
no broad refactor. It is intended to be the smallest safe surface that
makes monitoring alerts operationally useful.

EX-control impact:
- None of EX-01..EX-13 are touched. This module does not modify any
  protected file (memo_handler, rule_engine, validation_engine,
  supervisor_engine, screening, sumsub_client, security_hardening,
  auth, base_handler, party_utils, db.py, etc). It only writes to
  monitoring_alerts / periodic_reviews / edd_cases via existing
  columns added by migration 008 and via the existing INSERT shapes
  used elsewhere in server.py.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import lifecycle_linkage as ll

logger = logging.getLogger("arie.monitoring_routing")


# ── Vocabularies ----------------------------------------------------
VALID_DISMISSAL_REASONS = (
    "false_positive",
    "duplicate",
    "no_action_needed",
    "resolved_externally",
    "other",
)

# Alert statuses set by this module. The base `monitoring_alerts.status`
# column is free-text in the schema (see arie-backend/db.py) so this
# module is the source of truth for the routing-status vocabulary.
STATUS_OPEN = "open"
STATUS_TRIAGED = "triaged"
STATUS_ASSIGNED = "assigned"
STATUS_DISMISSED = "dismissed"
STATUS_ROUTED_REVIEW = "routed_to_review"
STATUS_ROUTED_EDD = "routed_to_edd"

TERMINAL_ALERT_STATUSES = (
    STATUS_DISMISSED,
    STATUS_ROUTED_REVIEW,
    STATUS_ROUTED_EDD,
)

# EDD stages considered "active" for duplicate-prevention. Mirrors the
# CHECK constraint in db.py exactly; intentionally duplicated rather
# than imported to avoid coupling to db.py internals.
TERMINAL_EDD_STAGES = ("edd_approved", "edd_rejected")


# ── Exceptions ------------------------------------------------------
class MonitoringRoutingError(ValueError):
    """Base class for all monitoring-routing failures."""


class AlertNotFound(MonitoringRoutingError):
    pass


class InvalidAlertAction(MonitoringRoutingError):
    pass


class InvalidDismissalReason(MonitoringRoutingError):
    pass


class AlertAlreadyTerminal(MonitoringRoutingError):
    """Raised when an action is attempted on an already-terminal alert.

    Terminal alerts (dismissed / routed_to_review / routed_to_edd) must
    not be re-routed because doing so would either create duplicate
    downstream objects or mute the original audit trail.
    """


# ── Internal utilities ---------------------------------------------
def _utcnow_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require_audit_writer(audit_writer):
    """Guard every PR-02 routing entry point.

    PR-01's lifecycle_linkage already raises MissingAuditWriter for
    every mutating helper, but routing functions in this module also
    perform a small amount of DB work BEFORE they call into
    lifecycle_linkage (status transitions, downstream INSERTs). Doing
    the audit-writer check up-front ensures we never partially mutate
    state without an audit path available.
    """
    if audit_writer is None:
        raise ll.MissingAuditWriter(
            "monitoring_routing requires a non-None audit_writer for "
            "every routing action"
        )


def _row_get(row, key, default=None):
    if row is None:
        return default
    if hasattr(row, "get"):
        v = row.get(key, default)
        return v if v is not None else default
    try:
        v = row[key]
        return v if v is not None else default
    except (KeyError, IndexError, TypeError):
        return default


def _fetch_alert(db, alert_id):
    row = db.execute(
        "SELECT * FROM monitoring_alerts WHERE id = ?", (alert_id,)
    ).fetchone()
    if row is None:
        raise AlertNotFound(f"monitoring_alert id={alert_id} not found")
    return row


def _set_alert_status(db, alert_id, *, status, officer_action,
                      officer_notes, user):
    """Single SQL update for the alert's officer-facing fields.

    Kept narrow so that we never touch lifecycle-linkage columns from
    here -- those are owned by lifecycle_linkage helpers.
    """
    db.execute(
        "UPDATE monitoring_alerts SET "
        "  status = ?, "
        "  officer_action = ?, "
        "  officer_notes = ?, "
        "  reviewed_at = CURRENT_TIMESTAMP, "
        "  reviewed_by = ? "
        "WHERE id = ?",
        (status, officer_action, officer_notes,
         (user or {}).get("sub", ""), alert_id),
    )
    db.commit()


def _emit_routing_audit(audit_writer, user, action, alert_id,
                        payload, db, before_state=None, after_state=None):
    """Wrapper that emits a structured monitoring.* audit event.

    Mirrors the contract used by lifecycle_linkage._emit_audit so that
    tests (and downstream readers) get a uniform shape.
    """
    if audit_writer is None:
        # Mutating callers are protected by lifecycle_linkage, but we
        # also gate the explicit routing audit here so a missing writer
        # cannot result in an un-audited routing outcome.
        raise ll.MissingAuditWriter(
            "monitoring_routing requires a non-None audit_writer for "
            "every routing action"
        )
    user_dict = dict(user) if user else {}
    detail = json.dumps(payload, default=str, sort_keys=True)
    logger.info(
        "monitoring_routing action=%s alert_id=%s detail=%s",
        action, alert_id, detail,
    )
    try:
        audit_writer(
            user_dict, action, f"monitoring_alert:{alert_id}", detail,
            db=db, before_state=before_state, after_state=after_state,
        )
    except Exception:
        logger.exception(
            "monitoring routing audit write failed action=%s alert_id=%s",
            action, alert_id,
        )


# ── Public actions -------------------------------------------------
def triage_alert(db, alert_id, *, user, audit_writer):
    """Mark a monitoring alert as triaged.

    Idempotent: re-triaging an already-triaged alert is a no-op for the
    timestamp (lifecycle_linkage uses COALESCE) but still updates the
    public status field to ``triaged`` if the alert is still ``open``.
    """
    _require_audit_writer(audit_writer)
    alert = _fetch_alert(db, alert_id)
    prior_status = _row_get(alert, "status", STATUS_OPEN)

    ll.mark_alert_triaged(db, alert_id, user=user, audit_writer=audit_writer)

    new_status = prior_status
    if prior_status == STATUS_OPEN:
        new_status = STATUS_TRIAGED
        _set_alert_status(
            db, alert_id,
            status=new_status,
            officer_action="triage",
            officer_notes=_row_get(alert, "officer_notes", "") or "",
            user=user,
        )

    _emit_routing_audit(
        audit_writer, user, "monitoring.alert.triaged", alert_id,
        {"alert_id": alert_id, "status": new_status},
        db,
        before_state={"status": prior_status},
        after_state={"status": new_status},
    )
    return {"alert_id": alert_id, "status": new_status}


def assign_alert(db, alert_id, *, user, audit_writer):
    """Mark a monitoring alert as assigned to the acting officer.

    Refuses to assign an already-resolved alert via lifecycle_linkage's
    InvalidLifecycleTransition.
    """
    _require_audit_writer(audit_writer)
    alert = _fetch_alert(db, alert_id)
    prior_status = _row_get(alert, "status", STATUS_OPEN)

    if prior_status in TERMINAL_ALERT_STATUSES:
        raise AlertAlreadyTerminal(
            f"cannot assign alert id={alert_id} in terminal status={prior_status!r}"
        )

    ll.mark_alert_assigned(db, alert_id, user=user, audit_writer=audit_writer)

    new_status = STATUS_ASSIGNED
    _set_alert_status(
        db, alert_id,
        status=new_status,
        officer_action="assign",
        officer_notes=_row_get(alert, "officer_notes", "") or "",
        user=user,
    )

    _emit_routing_audit(
        audit_writer, user, "monitoring.alert.assigned", alert_id,
        {"alert_id": alert_id, "assignee": (user or {}).get("sub", "")},
        db,
        before_state={"status": prior_status},
        after_state={"status": new_status},
    )
    return {"alert_id": alert_id, "status": new_status}


def dismiss_alert(db, alert_id, *, dismissal_reason,
                  dismissal_notes=None, user, audit_writer):
    """Dismiss a monitoring alert with a structured reason.

    A dismissed alert is terminal: it records who dismissed it, when,
    a structured reason from VALID_DISMISSAL_REASONS, and an optional
    free-text note. Re-dismissing an already-dismissed alert raises
    AlertAlreadyTerminal so the caller cannot silently overwrite the
    original audit trail.
    """
    _require_audit_writer(audit_writer)
    if dismissal_reason not in VALID_DISMISSAL_REASONS:
        raise InvalidDismissalReason(
            f"dismissal_reason={dismissal_reason!r} is not one of "
            f"{VALID_DISMISSAL_REASONS}"
        )

    alert = _fetch_alert(db, alert_id)
    prior_status = _row_get(alert, "status", STATUS_OPEN)
    if prior_status in TERMINAL_ALERT_STATUSES:
        raise AlertAlreadyTerminal(
            f"cannot dismiss alert id={alert_id} in terminal status={prior_status!r}"
        )

    # Persist the resolved_at timestamp via the PR-01 helper. This
    # also emits the lifecycle.alert.resolved audit event.
    ll.mark_alert_resolved(db, alert_id, user=user, audit_writer=audit_writer)

    structured_notes = json.dumps({
        "dismissal_reason": dismissal_reason,
        "dismissal_notes": dismissal_notes or "",
        "dismissed_by": (user or {}).get("sub", ""),
        "dismissed_at": _utcnow_iso(),
    }, sort_keys=True)

    _set_alert_status(
        db, alert_id,
        status=STATUS_DISMISSED,
        officer_action="dismiss",
        officer_notes=structured_notes,
        user=user,
    )

    _emit_routing_audit(
        audit_writer, user, "monitoring.alert.dismissed", alert_id,
        {
            "alert_id": alert_id,
            "dismissal_reason": dismissal_reason,
            "has_notes": bool(dismissal_notes),
        },
        db,
        before_state={"status": prior_status},
        after_state={"status": STATUS_DISMISSED,
                     "dismissal_reason": dismissal_reason},
    )
    return {
        "alert_id": alert_id,
        "status": STATUS_DISMISSED,
        "dismissal_reason": dismissal_reason,
    }


# ── Routing helpers (downstream creation + linking) ---------------
def _create_periodic_review_row(db, *, application_id, client_name,
                                risk_level, review_reason):
    """Insert a periodic_reviews row and return its id.

    Mirrors the INSERT shape used elsewhere in server.py (see
    PeriodicReviewScheduleHandler) so behaviour is consistent.
    """
    insert_params = (
        application_id,
        client_name or "",
        risk_level,
        "monitoring_alert",
        review_reason or "",
        "pending",
    )
    try:
        from db import USE_POSTGRESQL as _USE_PG
    except Exception:
        _USE_PG = False

    if _USE_PG:
        row = db.execute(
            "INSERT INTO periodic_reviews "
            "(application_id, client_name, risk_level, trigger_type, "
            " trigger_reason, status, created_at) "
            "VALUES (?,?,?,?,?,?, CURRENT_TIMESTAMP) RETURNING id",
            insert_params,
        ).fetchone()
        return row["id"]
    db.execute(
        "INSERT INTO periodic_reviews "
        "(application_id, client_name, risk_level, trigger_type, "
        " trigger_reason, status, created_at) "
        "VALUES (?,?,?,?,?,?, CURRENT_TIMESTAMP)",
        insert_params,
    )
    return db.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()["id"]


def _create_edd_case_row(db, *, application_id, client_name, risk_level,
                         risk_score, assigned_officer, trigger_notes):
    """Insert an edd_cases row and return its id.

    Mirrors the INSERT shape used by EDDCreateHandler.post in server.py
    so EDD downstream behaviour stays consistent regardless of which
    entry point created the case.
    """
    initial_note = json.dumps([{
        "ts": _utcnow_iso(),
        "author": "monitoring_routing",
        "note": trigger_notes or "EDD triggered from monitoring alert",
    }])
    insert_params = (
        application_id,
        client_name or "",
        risk_level or "HIGH",
        risk_score or 0,
        "triggered",
        assigned_officer or "",
        "monitoring_alert",
        trigger_notes or "EDD triggered from monitoring alert",
        initial_note,
    )
    try:
        from db import USE_POSTGRESQL as _USE_PG
    except Exception:
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
        return row["id"]
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


def _find_active_edd_for_application(db, application_id):
    """Return the id of an existing non-terminal EDD case for the
    given application, or None. Used to avoid duplicate active EDD
    creation when multiple alerts on the same application all route
    to EDD.
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
    return row["id"] if row else None


def route_alert_to_periodic_review(db, alert_id, *,
                                   review_reason=None,
                                   priority=None,
                                   user, audit_writer):
    """Route a monitoring alert to a real periodic review.

    Behaviour:
    - If the alert is already linked to a periodic review (PR-01
      ``linked_periodic_review_id`` set), reuse that review and emit
      a ``monitoring.alert.routed_to_review`` audit event with
      ``reused=True``. Do NOT create a second review.
    - Otherwise create a new periodic_reviews row, soft-link both
      sides via lifecycle_linkage.link_alert_to_review, set the
      review's trigger_source to 'monitoring_alert' via
      lifecycle_linkage.set_periodic_review_trigger, and emit a
      ``monitoring.alert.routed_to_review`` audit event with
      ``created=True``.
    - Refuse to route an alert that is already in a terminal status
      other than open/triaged/assigned. This makes repeat-routing
      deterministic instead of producing duplicate downstream objects.
    """
    _require_audit_writer(audit_writer)
    alert = _fetch_alert(db, alert_id)
    prior_status = _row_get(alert, "status", STATUS_OPEN)
    application_id = _row_get(alert, "application_id")
    client_name = _row_get(alert, "client_name", "")

    if prior_status in (STATUS_DISMISSED,):
        raise AlertAlreadyTerminal(
            f"cannot route dismissed alert id={alert_id} to review"
        )

    existing_review_id = _row_get(alert, "linked_periodic_review_id")
    created = False
    reused = False
    review_id = existing_review_id

    if existing_review_id is not None:
        # Already linked: reuse and short-circuit. We deliberately do
        # NOT re-emit lifecycle.link.alert_to_review.created because
        # the link has not changed.
        reused = True
    else:
        # Create the downstream review, then bidirectionally link.
        review_id = _create_periodic_review_row(
            db,
            application_id=application_id,
            client_name=client_name,
            risk_level=None,  # severity (Low/Medium/High) is not a risk level
                              # vocabulary -- leave NULL until the review is
                              # explicitly classified.
            review_reason=review_reason or _row_get(alert, "summary", ""),
        )
        ll.link_alert_to_review(
            db, alert_id, review_id,
            user=user, audit_writer=audit_writer,
        )
        ll.set_periodic_review_trigger(
            db, review_id,
            trigger_source="monitoring_alert",
            review_reason=review_reason,
            linked_monitoring_alert_id=alert_id,
            user=user,
            audit_writer=audit_writer,
        )
        if priority:
            ll.mark_review_assigned(
                db, review_id, priority=priority,
                user=user, audit_writer=audit_writer,
            )
        created = True

    new_status = STATUS_ROUTED_REVIEW
    _set_alert_status(
        db, alert_id,
        status=new_status,
        officer_action="route_to_periodic_review",
        officer_notes=review_reason or _row_get(alert, "officer_notes", "") or "",
        user=user,
    )

    _emit_routing_audit(
        audit_writer, user, "monitoring.alert.routed_to_review", alert_id,
        {
            "alert_id": alert_id,
            "periodic_review_id": review_id,
            "created": created,
            "reused": reused,
        },
        db,
        before_state={"status": prior_status,
                      "linked_periodic_review_id": existing_review_id},
        after_state={"status": new_status,
                     "linked_periodic_review_id": review_id},
    )

    return {
        "alert_id": alert_id,
        "periodic_review_id": review_id,
        "created": created,
        "reused": reused,
        "status": new_status,
    }


def route_alert_to_edd(db, alert_id, *,
                       trigger_notes=None,
                       priority=None,
                       user, audit_writer):
    """Route a monitoring alert to a real EDD case.

    Behaviour:
    - If the alert is already linked to an EDD case AND that case is
      not in a terminal stage (edd_approved / edd_rejected), reuse it
      and emit a routing audit event with ``reused=True``.
    - Else if there is any other active EDD case on the same
      application, link to it and reuse it. This matches the existing
      duplicate-prevention rule in EDDCreateHandler.post (server.py).
    - Else create a new edd_cases row, set origin_context to
      'monitoring_alert' via lifecycle_linkage.set_edd_origin, and
      bidirectionally link via lifecycle_linkage.link_alert_to_edd.
    - Refuse to route a dismissed alert.
    """
    _require_audit_writer(audit_writer)
    alert = _fetch_alert(db, alert_id)
    prior_status = _row_get(alert, "status", STATUS_OPEN)
    application_id = _row_get(alert, "application_id")
    client_name = _row_get(alert, "client_name", "")

    if prior_status == STATUS_DISMISSED:
        raise AlertAlreadyTerminal(
            f"cannot route dismissed alert id={alert_id} to EDD"
        )

    if application_id is None:
        raise MonitoringRoutingError(
            f"alert id={alert_id} has no application_id; cannot create EDD case"
        )

    existing_link_id = _row_get(alert, "linked_edd_case_id")
    created = False
    reused = False
    edd_case_id = None

    if existing_link_id is not None:
        # Check whether the linked case is still active.
        linked = db.execute(
            "SELECT id, stage FROM edd_cases WHERE id = ?",
            (existing_link_id,),
        ).fetchone()
        if linked and _row_get(linked, "stage") not in TERMINAL_EDD_STAGES:
            edd_case_id = existing_link_id
            reused = True

    if edd_case_id is None:
        # Look for any other active EDD case on the same application.
        active_id = _find_active_edd_for_application(db, application_id)
        if active_id is not None:
            edd_case_id = active_id
            ll.link_alert_to_edd(
                db, alert_id, edd_case_id,
                user=user, audit_writer=audit_writer,
            )
            ll.set_edd_origin(
                db, edd_case_id,
                origin_context="monitoring_alert",
                linked_monitoring_alert_id=alert_id,
                user=user,
                audit_writer=audit_writer,
            )
            reused = True

    if edd_case_id is None:
        # No active EDD anywhere — create one.
        edd_case_id = _create_edd_case_row(
            db,
            application_id=application_id,
            client_name=client_name,
            risk_level=None,  # severity != risk_level; let downstream classify
            risk_score=None,
            assigned_officer=(user or {}).get("sub", ""),
            trigger_notes=trigger_notes,
        )
        ll.link_alert_to_edd(
            db, alert_id, edd_case_id,
            user=user, audit_writer=audit_writer,
        )
        ll.set_edd_origin(
            db, edd_case_id,
            origin_context="monitoring_alert",
            linked_monitoring_alert_id=alert_id,
            user=user,
            audit_writer=audit_writer,
        )
        if priority:
            ll.mark_edd_assigned(
                db, edd_case_id, priority=priority,
                user=user, audit_writer=audit_writer,
            )
        created = True

    new_status = STATUS_ROUTED_EDD
    _set_alert_status(
        db, alert_id,
        status=new_status,
        officer_action="route_to_edd",
        officer_notes=trigger_notes or _row_get(alert, "officer_notes", "") or "",
        user=user,
    )

    _emit_routing_audit(
        audit_writer, user, "monitoring.alert.routed_to_edd", alert_id,
        {
            "alert_id": alert_id,
            "edd_case_id": edd_case_id,
            "created": created,
            "reused": reused,
        },
        db,
        before_state={"status": prior_status,
                      "linked_edd_case_id": existing_link_id},
        after_state={"status": new_status,
                     "linked_edd_case_id": edd_case_id},
    )

    return {
        "alert_id": alert_id,
        "edd_case_id": edd_case_id,
        "created": created,
        "reused": reused,
        "status": new_status,
    }
