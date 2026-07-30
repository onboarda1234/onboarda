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
- None of EX-01..EX-13 runtime control surfaces are changed. This
  module only writes to monitoring_alerts / periodic_reviews /
  edd_cases via existing columns added by migration 008 and via the
  existing INSERT shapes used elsewhere in server.py.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import lifecycle_linkage as ll
import monitoring_alert_state_machine as sm

logger = logging.getLogger("arie.monitoring_routing")


# ── Vocabularies ----------------------------------------------------
VALID_DISMISSAL_REASONS = (
    "false_positive",
    "duplicate",
    "no_action_needed",
    "resolved_externally",
    "other",
)

# RDI-008: severities whose dismissal is gated behind a senior/four-eyes
# disposition. A CRITICAL alert must not be dismissed via the ordinary path.
CRITICAL_SEVERITIES = ("critical",)
# Roles accepted as a senior disposition authority for a critical dismissal.
_SENIOR_DISPOSITION_ROLES = ("admin", "sco")

# Backwards-compatible names for callers of this routing facade. The canonical
# vocabulary and transition authority live in monitoring_alert_state_machine.
STATUS_OPEN = "open"
STATUS_TRIAGED = "triaged"
STATUS_ASSIGNED = "assigned"
STATUS_DISMISSED = "dismissed"
STATUS_ROUTED_REVIEW = "routed_to_review"
STATUS_ROUTED_EDD = "routed_to_edd"
STATUS_RESOLVED = "resolved"
TERMINAL_ALERT_STATUSES = tuple(sorted(sm.TERMINAL_STATUSES))
HANDOFF_ALERT_STATUSES = tuple(sorted(sm.HANDOFF_STATUSES))

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
    """Raised when an action is attempted on an action-locked alert.

    Terminal decisions and downstream handoffs must not be re-routed because
    doing so would either create duplicate downstream objects or mute the
    original audit trail. Handoffs remain nonterminal lifecycle states.
    """


class CriticalAlertDismissalBlocked(MonitoringRoutingError):
    """Raised when a CRITICAL-severity alert is dismissed without the required
    senior/four-eyes disposition (RDI-008).

    A CRITICAL monitoring alert must NOT be dismissed through the ordinary
    single-officer path: doing so could suppress an EDD/SAR escalation. It
    requires a senior (admin/SCO) disposition with documented evidence — the
    M2.2 monitoring-dismissal control — or must instead be routed to EDD/SAR
    assessment.
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


def _safe_rollback(db):
    """Best-effort rollback that never masks the original exception (RDI-007)."""
    try:
        db.rollback()
    except Exception:
        logger.exception("monitoring_routing rollback failed")


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


def is_alert_terminal(alert_or_status, *, resolved_at=None):
    """Return True only for an authoritative canonical terminal status.

    ``resolved_at`` is retained in the signature for compatibility but is no
    longer an independent lifecycle authority. Routed states are handoffs, not
    terminal decisions.
    """
    status = alert_or_status
    if isinstance(alert_or_status, dict) or hasattr(alert_or_status, "keys"):
        status = _row_get(alert_or_status, "status", STATUS_OPEN)
    return sm.is_terminal_status(status)


def is_alert_unresolved(alert_or_status, *, resolved_at=None):
    return not is_alert_terminal(alert_or_status, resolved_at=resolved_at)


def _fetch_alert(db, alert_id):
    row = db.execute(
        "SELECT * FROM monitoring_alerts WHERE id = ?", (alert_id,)
    ).fetchone()
    if row is None:
        raise AlertNotFound(f"monitoring_alert id={alert_id} not found")
    return row


def _is_critical_severity(alert):
    return str(_row_get(alert, "severity", "") or "").strip().lower() in CRITICAL_SEVERITIES


def _valid_critical_clearance(critical_clearance, user):
    """Return True when the caller has certified a valid senior/four-eyes
    disposition for dismissing a CRITICAL alert (RDI-008).

    A valid clearance is a mapping carrying a senior (admin/SCO) ``approver``
    and a non-empty ``evidence_ref``. For ``via='approved_review'`` the server
    threads the STORED request evidence after re-validating it against the
    CURRENT alert at approval time (``_mdc.assert_evidence_current`` — the
    Codex round-2 TOCTOU fix); requiring the evidence here as well means even
    a severity escalation that lands in the tiny window between that recheck
    and this gate's fresh alert fetch cannot produce an evidence-less critical
    dismissal.

    RDI-008 follow-up: the bare ``via='senior_clear'`` marker is NO LONGER
    sufficient on its own — a direct senior clear without evidence would let a
    CRITICAL tier-2/3 alert be dismissed with no documented evidence.
    """
    if not isinstance(critical_clearance, dict):
        return False
    approver = critical_clearance.get("approver") or {}
    approver_role = str((approver or {}).get("role") or "").strip().lower()
    if approver_role not in _SENIOR_DISPOSITION_ROLES:
        return False
    evidence_ref = str(critical_clearance.get("evidence_ref") or "").strip()
    return bool(evidence_ref)


def _enforce_critical_dismissal_gate(alert_id, dismissal_notes, critical_clearance, user):
    """Refuse dismissal of a CRITICAL alert without documented evidence and a
    senior/four-eyes disposition (RDI-008)."""
    if not str(dismissal_notes or "").strip():
        raise CriticalAlertDismissalBlocked(
            f"CRITICAL alert id={alert_id} cannot be dismissed without a "
            f"documented justification (dismissal_notes)."
        )
    if not _valid_critical_clearance(critical_clearance, user):
        raise CriticalAlertDismissalBlocked(
            f"CRITICAL alert id={alert_id} cannot be dismissed through the "
            f"ordinary path. It requires a senior (admin/SCO) disposition with "
            f"documented evidence (the M2.2 monitoring-dismissal control), or "
            f"must be routed to EDD/SAR assessment instead."
        )


def _set_alert_metadata(db, alert_id, *, officer_action, officer_notes, user,
                        reviewed_by=None):
    """Update officer metadata without changing lifecycle state."""
    db.execute(
        "UPDATE monitoring_alerts SET "
        "  officer_action = ?, "
        "  officer_notes = ?, "
        "  reviewed_at = CURRENT_TIMESTAMP, "
        "  reviewed_by = ? "
        "WHERE id = ?",
        (
            officer_action,
            officer_notes,
            reviewed_by if reviewed_by is not None else (user or {}).get("sub", ""),
            alert_id,
        ),
    )


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
    # RDI-007: the routing audit joins the caller's OPEN transaction
    # (commit=False) and its failure PROPAGATES. A monitoring compliance
    # action must not persist without its final routing audit evidence — if
    # this write fails, the public action rolls back the whole unit.
    audit_writer(
        user_dict, action, f"monitoring_alert:{alert_id}", detail,
        db=db, before_state=before_state, after_state=after_state,
        commit=False,
    )


def _alert_identity_evidence(alert):
    source_reference = str(_row_get(alert, "source_reference", "") or "").strip()
    if source_reference:
        return {"source_reference": source_reference}
    case_identifier = str(_row_get(alert, "case_identifier", "") or "").strip()
    if case_identifier:
        return {"case_identifier": case_identifier}
    raise sm.MissingEvidence(
        "The alert has no source_reference or case_identifier for controlled triage."
    )


def _finish(db, *, commit):
    if commit:
        db.commit()


# ── Public actions -------------------------------------------------
def triage_alert(db, alert_id, *, user, audit_writer, commit=True):
    """Mark a monitoring alert as triaged.

    Idempotent: re-triaging an already-triaged alert is a no-op for the
    timestamp (lifecycle_linkage uses COALESCE) but still updates the
    public status field to ``triaged`` if the alert is still ``open``.
    """
    _require_audit_writer(audit_writer)
    try:
        alert = sm.lock_alert_for_transition(db, alert_id)
        prior_status = _row_get(alert, "status", STATUS_OPEN)
        if prior_status == STATUS_TRIAGED:
            _finish(db, commit=commit)
            return {
                "alert_id": alert_id,
                "status": STATUS_TRIAGED,
                "changed": False,
                "state_machine_version": sm.STATE_MACHINE_VERSION,
            }
        result = sm.transition_alert_status(
            db,
            alert_id,
            expected_status=prior_status,
            target_status=STATUS_TRIAGED,
            actor=user,
            source_workflow="monitoring",
            reason_code="triage",
            reason="Monitoring Alert triaged by an authorised officer.",
            evidence=_alert_identity_evidence(alert),
            commit=False,
        )
        _set_alert_metadata(
            db,
            alert_id,
            officer_action="triage",
            officer_notes=_row_get(alert, "officer_notes", "") or "",
            user=user,
        )
        _emit_routing_audit(
            audit_writer, user, "monitoring.alert.triaged", alert_id,
            {
                "alert_id": alert_id,
                "status": STATUS_TRIAGED,
                "state_machine_version": sm.STATE_MACHINE_VERSION,
            },
            db,
            before_state={"status": prior_status},
            after_state={"status": STATUS_TRIAGED},
        )
        _finish(db, commit=commit)
        return {
            "alert_id": alert_id,
            "status": STATUS_TRIAGED,
            "changed": result["changed"],
            "state_machine_version": sm.STATE_MACHINE_VERSION,
        }
    except sm.AlertNotFound as exc:
        _safe_rollback(db)
        raise AlertNotFound(f"monitoring_alert id={alert_id} not found") from exc
    except Exception:
        _safe_rollback(db)
        raise


def assign_alert(db, alert_id, *, user, audit_writer, assignee_id=None,
                 commit=True):
    """Assign an active alert without rewriting later workflow states."""
    _require_audit_writer(audit_writer)
    assignee = str(assignee_id or (user or {}).get("sub", "") or "").strip()
    try:
        alert = sm.lock_alert_for_transition(db, alert_id)
        prior_status = str(_row_get(alert, "status", STATUS_OPEN) or "")
        if prior_status in sm.TERMINAL_STATUSES or prior_status in sm.HANDOFF_STATUSES:
            raise AlertAlreadyTerminal(
                f"cannot assign alert id={alert_id} in status={prior_status!r}"
            )
        sm.validate_assignment_authority(db, user, assignee)
        changed = prior_status in {STATUS_OPEN, STATUS_TRIAGED}
        if changed:
            sm.transition_alert_status(
                db,
                alert_id,
                expected_status=prior_status,
                target_status=STATUS_ASSIGNED,
                actor=user,
                source_workflow="monitoring",
                reason_code="assign",
                reason="Monitoring Alert assigned to an active officer.",
                evidence={"officer_id": assignee},
                commit=False,
            )
            new_status = STATUS_ASSIGNED
        elif prior_status in {STATUS_ASSIGNED, "in_review", "escalated"}:
            new_status = prior_status
        else:
            raise sm.InvalidTransition(
                f"Assignment is prohibited from status {prior_status!r}."
            )
        _set_alert_metadata(
            db,
            alert_id,
            officer_action="assign",
            officer_notes=_row_get(alert, "officer_notes", "") or "",
            user=user,
            reviewed_by=assignee,
        )
        _emit_routing_audit(
            audit_writer, user, "monitoring.alert.assigned", alert_id,
            {
                "alert_id": alert_id,
                "assignee": assignee,
                "status_changed": changed,
                "state_machine_version": sm.STATE_MACHINE_VERSION,
            },
            db,
            before_state={"status": prior_status},
            after_state={"status": new_status},
        )
        _finish(db, commit=commit)
        return {
            "alert_id": alert_id,
            "status": new_status,
            "changed": changed,
            "owner_id": assignee,
            "state_machine_version": sm.STATE_MACHINE_VERSION,
        }
    except sm.AlertNotFound as exc:
        _safe_rollback(db)
        raise AlertNotFound(f"monitoring_alert id={alert_id} not found") from exc
    except Exception:
        _safe_rollback(db)
        raise


def dismiss_alert(db, alert_id, *, dismissal_reason,
                  dismissal_notes=None, user, audit_writer,
                  critical_clearance=None, review_request_id=None,
                  screening_case_id=None, document_id=None,
                  document_request_id=None, transition_evidence=None,
                  commit=True):
    """Dismiss a monitoring alert with a structured reason.

    A dismissed alert is terminal: it records who dismissed it, when,
    a structured reason from VALID_DISMISSAL_REASONS, and an optional
    free-text note. Re-dismissing an already-dismissed alert raises
    AlertAlreadyTerminal so the caller cannot silently overwrite the
    original audit trail.

    RDI-008: a CRITICAL-severity alert may NOT be dismissed through the
    ordinary path. It requires a senior (admin/SCO) disposition with
    documented evidence — pass ``critical_clearance`` (see
    ``_valid_critical_clearance``) — or must be routed to EDD/SAR instead;
    otherwise CriticalAlertDismissalBlocked is raised BEFORE any mutation.
    """
    _require_audit_writer(audit_writer)
    if dismissal_reason not in VALID_DISMISSAL_REASONS:
        raise InvalidDismissalReason(
            f"dismissal_reason={dismissal_reason!r} is not one of "
            f"{VALID_DISMISSAL_REASONS}"
        )

    structured_notes = json.dumps({
        "dismissal_reason": dismissal_reason,
        "dismissal_notes": dismissal_notes or "",
        "dismissed_by": (user or {}).get("sub", ""),
        "dismissed_at": _utcnow_iso(),
    }, sort_keys=True)

    try:
        alert = sm.lock_alert_for_transition(db, alert_id)
        prior_status = str(_row_get(alert, "status", STATUS_OPEN) or "")
        if prior_status in sm.TERMINAL_STATUSES or prior_status in sm.HANDOFF_STATUSES:
            raise AlertAlreadyTerminal(
                f"cannot dismiss alert id={alert_id} in status={prior_status!r}"
            )

        is_critical = _is_critical_severity(alert)
        if is_critical:
            _enforce_critical_dismissal_gate(
                alert_id, dismissal_notes, critical_clearance, user
            )

        evidence = dict(transition_evidence or {})
        evidence.update({
            "dismissal_reason": dismissal_reason,
            "officer_rationale": str(dismissal_notes or "").strip()
            or f"Dismissed as {dismissal_reason}.",
        })
        for key, value in (
            ("review_request_id", review_request_id),
            ("screening_case_id", screening_case_id),
            ("document_id", document_id),
            ("document_request_id", document_request_id),
        ):
            if value not in (None, ""):
                evidence[key] = value
        owner = sm.alert_owner(db, alert)
        stored_case = str(_row_get(alert, "case_identifier", "") or "").strip()
        if owner == "screening_review" and stored_case:
            evidence["case_identifier"] = stored_case
        source_reference = str(
            _row_get(alert, "source_reference", "") or ""
        ).strip()
        if (
            owner == "documents"
            and "document_id" not in evidence
            and "document_request_id" not in evidence
            and source_reference.startswith("document:")
        ):
            linked_document_id = source_reference.split(":", 1)[1].strip()
            if linked_document_id:
                evidence["document_id"] = linked_document_id

        transition = sm.transition_alert_status(
            db,
            alert_id,
            expected_status=prior_status,
            target_status=STATUS_DISMISSED,
            actor=user,
            source_workflow="monitoring",
            reason_code=f"dismiss_{dismissal_reason}",
            reason=str(dismissal_notes or "").strip()
            or f"Monitoring Alert dismissed as {dismissal_reason}.",
            evidence=evidence,
            commit=False,
        )
        _set_alert_metadata(
            db,
            alert_id,
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
                "critical_gate_applied": is_critical,
                "state_machine_version": sm.STATE_MACHINE_VERSION,
            },
            db,
            before_state={"status": prior_status},
            after_state={"status": STATUS_DISMISSED,
                         "dismissal_reason": dismissal_reason},
        )
        _finish(db, commit=commit)
    except sm.AlertNotFound as exc:
        _safe_rollback(db)
        raise AlertNotFound(f"monitoring_alert id={alert_id} not found") from exc
    except Exception:
        _safe_rollback(db)
        raise
    return {
        "alert_id": alert_id,
        "status": STATUS_DISMISSED,
        "dismissal_reason": dismissal_reason,
        "changed": transition["changed"],
        "state_machine_version": sm.STATE_MACHINE_VERSION,
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
    """Return one locked, unowned active EDD case for reuse, or ``None``.

    An EDD case already linked to another Monitoring Alert or Periodic Review
    has an authoritative owner and must never be claimed by a second alert.
    PostgreSQL skips concurrently claimed candidates.
    """
    if application_id is None:
        return None
    placeholders = ",".join("?" for _ in TERMINAL_EDD_STAGES)
    lock_clause = (
        " FOR UPDATE SKIP LOCKED"
        if getattr(db, "is_postgres", False)
        else ""
    )
    rows = db.execute(
        f"SELECT * FROM edd_cases "
        f"WHERE application_id = ? AND stage NOT IN ({placeholders}) "
        "AND linked_monitoring_alert_id IS NULL "
        "AND linked_periodic_review_id IS NULL "
        f"ORDER BY id ASC{lock_clause}",
        (application_id, *TERMINAL_EDD_STAGES),
    ).fetchall()
    try:
        from investigation_scope import is_routine_onboarding_policy_case
    except Exception:
        is_routine_onboarding_policy_case = None
    formal_reusable_sources = {
        "monitoring_alert",
        "periodic_review",
        "change_request",
        "manual",
        "manual_onboarding_escalation",
        "onboarding_escalation",
        "officer_decision",
        "officer_correction",
        "screening_update",
    }
    for row in rows:
        if is_routine_onboarding_policy_case and is_routine_onboarding_policy_case(row):
            continue
        origin = str(_row_get(row, "origin_context", "") or "").strip().lower()
        trigger_source = str(_row_get(row, "trigger_source", "") or "").strip().lower()
        linked_alert = _row_get(row, "linked_monitoring_alert_id")
        linked_review = _row_get(row, "linked_periodic_review_id")
        if (
            origin not in formal_reusable_sources
            and trigger_source not in formal_reusable_sources
            and linked_alert in (None, "")
            and linked_review in (None, "")
        ):
            # Legacy active rows without an origin/source are ambiguous. Do
            # not let a real monitoring alert attach to routine onboarding
            # policy debris; create a monitoring-origin case instead.
            continue
        return dict(row)
    return None


def _load_periodic_review_for_routing(db, review_id):
    lock_clause = " FOR UPDATE" if getattr(db, "is_postgres", False) else ""
    row = db.execute(
        f"SELECT * FROM periodic_reviews WHERE id = ?{lock_clause}",
        (review_id,),
    ).fetchone()
    if row is None:
        raise sm.LinkedObjectMissing("The linked periodic review does not exist.")
    return dict(row)


def _load_edd_for_routing(db, edd_case_id):
    lock_clause = " FOR UPDATE" if getattr(db, "is_postgres", False) else ""
    row = db.execute(
        f"SELECT * FROM edd_cases WHERE id = ?{lock_clause}",
        (edd_case_id,),
    ).fetchone()
    if row is None:
        raise sm.LinkedObjectMissing("The linked EDD case does not exist.")
    return dict(row)


def _validate_periodic_review_link(alert, review, *, allow_closed):
    alert_id = _row_get(alert, "id")
    review_id = _row_get(review, "id")
    alert_application_id = _row_get(alert, "application_id")
    review_application_id = _row_get(review, "application_id")
    if (
        alert_application_id in (None, "")
        or review_application_id in (None, "")
        or str(alert_application_id) != str(review_application_id)
    ):
        raise sm.EvidenceLinkMismatch(
            "The linked periodic review has missing or conflicting application "
            "linkage."
        )
    if str(_row_get(alert, "linked_periodic_review_id") or "") != str(review_id):
        raise sm.EvidenceLinkMismatch(
            "The Monitoring Alert does not point to the linked periodic review."
        )
    if str(_row_get(review, "linked_monitoring_alert_id") or "") != str(alert_id):
        raise sm.EvidenceLinkMismatch(
            "The periodic review does not point back to this Monitoring Alert."
        )
    review_status = str(_row_get(review, "status") or "").strip().lower()
    if not allow_closed and (
        _row_get(review, "closed_at") not in (None, "")
        or review_status in {"completed", "cancelled", "canceled"}
    ):
        raise sm.InvalidTransition(
            "A closed periodic review cannot receive a Monitoring Alert handoff."
        )


def _validate_edd_link(alert, edd, *, allow_terminal):
    alert_id = _row_get(alert, "id")
    edd_case_id = _row_get(edd, "id")
    alert_application_id = _row_get(alert, "application_id")
    edd_application_id = _row_get(edd, "application_id")
    if (
        alert_application_id in (None, "")
        or edd_application_id in (None, "")
        or str(alert_application_id) != str(edd_application_id)
    ):
        raise sm.EvidenceLinkMismatch(
            "The linked EDD case has missing or conflicting application linkage."
        )
    if str(_row_get(alert, "linked_edd_case_id") or "") != str(edd_case_id):
        raise sm.EvidenceLinkMismatch(
            "The Monitoring Alert does not point to the linked EDD case."
        )
    if str(_row_get(edd, "linked_monitoring_alert_id") or "") != str(alert_id):
        raise sm.EvidenceLinkMismatch(
            "The EDD case does not point back to this Monitoring Alert."
        )
    if not allow_terminal and _row_get(edd, "stage") in TERMINAL_EDD_STAGES:
        raise sm.InvalidTransition(
            "A terminal EDD case cannot receive a Monitoring Alert handoff."
        )


def route_alert_to_periodic_review(db, alert_id, *,
                                   review_reason=None,
                                   priority=None,
                                   user, audit_writer, commit=True):
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
    try:
        alert = sm.lock_alert_for_transition(db, alert_id)
        prior_status = str(_row_get(alert, "status", STATUS_OPEN) or "")
        application_id = _row_get(alert, "application_id")
        client_name = _row_get(alert, "client_name", "")
        existing_review_id = _row_get(alert, "linked_periodic_review_id")
        created = False
        reused = False
        review_id = existing_review_id
        new_status = STATUS_ROUTED_REVIEW

        if prior_status == STATUS_ROUTED_REVIEW:
            if existing_review_id in (None, ""):
                raise sm.EvidenceLinkMismatch(
                    "The routed Monitoring Alert has no periodic review linkage."
                )
            existing_review = _load_periodic_review_for_routing(
                db, existing_review_id
            )
            _validate_periodic_review_link(
                alert,
                existing_review,
                allow_closed=True,
            )
            _finish(db, commit=commit)
            return {
                "alert_id": alert_id,
                "periodic_review_id": existing_review_id,
                "created": False,
                "reused": True,
                "status": new_status,
                "changed": False,
                "state_machine_version": sm.STATE_MACHINE_VERSION,
            }
        if prior_status in sm.TERMINAL_STATUSES or prior_status in sm.HANDOFF_STATUSES:
            raise AlertAlreadyTerminal(
                f"cannot route alert id={alert_id} from status={prior_status!r}"
            )

        if existing_review_id is not None:
            existing_review = _load_periodic_review_for_routing(
                db, existing_review_id
            )
            _validate_periodic_review_link(
                alert,
                existing_review,
                allow_closed=False,
            )
            # The exact bidirectional link is already present; reuse it without
            # emitting another link-created event.
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
                user=user, audit_writer=audit_writer, commit=False,
            )
            ll.set_periodic_review_trigger(
                db, review_id,
                trigger_source="monitoring_alert",
                review_reason=review_reason,
                linked_monitoring_alert_id=alert_id,
                user=user,
                audit_writer=audit_writer,
                commit=False,
            )
            if priority:
                ll.mark_review_assigned(
                    db, review_id, priority=priority,
                    user=user, audit_writer=audit_writer, commit=False,
                )
            created = True

        transition = sm.transition_alert_status(
            db,
            alert_id,
            expected_status=prior_status,
            target_status=new_status,
            actor=user,
            source_workflow="monitoring",
            reason_code="route_to_periodic_review",
            reason=review_reason or "Monitoring Alert routed to Periodic Review.",
            evidence={"periodic_review_id": review_id},
            commit=False,
        )
        _set_alert_metadata(
            db,
            alert_id,
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
                "state_machine_version": sm.STATE_MACHINE_VERSION,
            },
            db,
            before_state={"status": prior_status,
                          "linked_periodic_review_id": existing_review_id},
            after_state={"status": new_status,
                         "linked_periodic_review_id": review_id},
        )
        _finish(db, commit=commit)
    except sm.AlertNotFound as exc:
        _safe_rollback(db)
        raise AlertNotFound(f"monitoring_alert id={alert_id} not found") from exc
    except Exception:
        _safe_rollback(db)
        raise

    return {
        "alert_id": alert_id,
        "periodic_review_id": review_id,
        "created": created,
        "reused": reused,
        "status": new_status,
        "changed": transition["changed"],
        "state_machine_version": sm.STATE_MACHINE_VERSION,
    }


def route_alert_to_edd(db, alert_id, *,
                       trigger_notes=None,
                       priority=None,
                       user, audit_writer, commit=True):
    """Route a monitoring alert to a real EDD case.

    Behaviour:
    - If the alert is already linked to an EDD case AND that case is
      not in a terminal stage (edd_approved / edd_rejected), reuse it
      and emit a routing audit event with ``reused=True``.
    - Else reuse only an active, genuinely unowned EDD case on the same
      application. A case linked to another alert/review is never overwritten.
    - Else create a new edd_cases row, set origin_context to
      'monitoring_alert' via lifecycle_linkage.set_edd_origin, and
      bidirectionally link via lifecycle_linkage.link_alert_to_edd.
    - Refuse to route a dismissed alert.
    """
    _require_audit_writer(audit_writer)
    try:
        alert = sm.lock_alert_for_transition(db, alert_id)
        prior_status = str(_row_get(alert, "status", STATUS_OPEN) or "")
        application_id = _row_get(alert, "application_id")
        client_name = _row_get(alert, "client_name", "")
        existing_link_id = _row_get(alert, "linked_edd_case_id")
        created = False
        reused = False
        edd_case_id = None
        new_status = STATUS_ROUTED_EDD

        if prior_status == STATUS_ROUTED_EDD:
            if existing_link_id in (None, ""):
                raise sm.EvidenceLinkMismatch(
                    "The routed Monitoring Alert has no EDD case linkage."
                )
            existing_edd = _load_edd_for_routing(db, existing_link_id)
            _validate_edd_link(alert, existing_edd, allow_terminal=True)
            _finish(db, commit=commit)
            return {
                "alert_id": alert_id,
                "edd_case_id": existing_link_id,
                "created": False,
                "reused": True,
                "status": new_status,
                "changed": False,
                "state_machine_version": sm.STATE_MACHINE_VERSION,
            }
        if prior_status in sm.TERMINAL_STATUSES or prior_status in sm.HANDOFF_STATUSES:
            raise AlertAlreadyTerminal(
                f"cannot route alert id={alert_id} from status={prior_status!r}"
            )
        if application_id is None:
            raise MonitoringRoutingError(
                f"alert id={alert_id} has no application_id; cannot create EDD case"
            )

        if existing_link_id is not None:
            linked = _load_edd_for_routing(db, existing_link_id)
            _validate_edd_link(alert, linked, allow_terminal=False)
            edd_case_id = existing_link_id
            reused = True

        if edd_case_id is None:
            # Reuse only a locked, unowned active EDD. A case whose reverse
            # pointer names another workflow is authoritative and cannot be
            # displaced.
            active_edd = _find_active_edd_for_application(db, application_id)
            if active_edd is not None:
                edd_case_id = active_edd["id"]
                ll.link_alert_to_edd(
                    db, alert_id, edd_case_id,
                    user=user, audit_writer=audit_writer, commit=False,
                )
                ll.set_edd_origin(
                    db, edd_case_id,
                    origin_context="monitoring_alert",
                    linked_monitoring_alert_id=alert_id,
                    user=user,
                    audit_writer=audit_writer,
                    commit=False,
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
                user=user, audit_writer=audit_writer, commit=False,
            )
            ll.set_edd_origin(
                db, edd_case_id,
                origin_context="monitoring_alert",
                linked_monitoring_alert_id=alert_id,
                user=user,
                audit_writer=audit_writer,
                commit=False,
            )
            if priority:
                ll.mark_edd_assigned(
                    db, edd_case_id, priority=priority,
                    user=user, audit_writer=audit_writer, commit=False,
                )
            created = True

        transition = sm.transition_alert_status(
            db,
            alert_id,
            expected_status=prior_status,
            target_status=new_status,
            actor=user,
            source_workflow="monitoring",
            reason_code="route_to_edd",
            reason=trigger_notes or "Monitoring Alert routed to EDD.",
            evidence={"edd_case_id": edd_case_id},
            commit=False,
        )
        _set_alert_metadata(
            db,
            alert_id,
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
                "state_machine_version": sm.STATE_MACHINE_VERSION,
            },
            db,
            before_state={"status": prior_status,
                          "linked_edd_case_id": existing_link_id},
            after_state={"status": new_status,
                         "linked_edd_case_id": edd_case_id},
        )
        _finish(db, commit=commit)
    except sm.AlertNotFound as exc:
        _safe_rollback(db)
        raise AlertNotFound(f"monitoring_alert id={alert_id} not found") from exc
    except Exception:
        _safe_rollback(db)
        raise

    return {
        "alert_id": alert_id,
        "edd_case_id": edd_case_id,
        "created": created,
        "reused": reused,
        "status": new_status,
        "changed": transition["changed"],
        "state_machine_version": sm.STATE_MACHINE_VERSION,
    }
