"""
Monitoring Alert dismissal control — risk-tiered four-eyes / senior-override (M2.2).

Replaces the M1.1 interim high-risk dismissal guard with a maker-checker model:

- Material alert *clears* (Tier 1 & 2) are controlled. Escalation / routing /
  information-seeking are NOT (they add scrutiny, not remove it).
- **Senior-override:** CO/officer clearing a controlled alert creates a PENDING
  review request (does not execute). SCO/admin may clear directly with mandatory
  enhanced rationale (recorded as `dismissal_senior_cleared`, `second_review_bypassed`).
- **Same-user rule (unconditional):** the approver of any pending request must be
  a different user than the initiator, and hold an approver role.
- No new `monitoring_alerts.status` value; the review request lives in its own
  table (`monitoring_alert_review_requests`). "Pending second review" is derived.

This module classifies tiers and manages the request rows + audit. It does NOT
reimplement terminal status transitions — the caller runs the existing
`monitoring_routing` / decision-outcome machinery on approval or senior clear.

CONFIRMED for pilot: TIER1_APPROVER_ROLES = {sco, admin}; admin is
MLRO-equivalent for pilot governance only (no dedicated MLRO role).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import monitoring_status as ms
from monitoring_alert_state_machine import MAX_TEXT_EVIDENCE_LENGTH

try:
    from document_health_monitor import CRITICAL_IDENTITY_DOC_TYPES, EXPIRY_REQUIRED_DOC_TYPES
except Exception:  # pragma: no cover - defensive
    CRITICAL_IDENTITY_DOC_TYPES = {
        "passport", "national_id", "id_card", "drivers_license", "director_id", "ubo_id",
    }
    EXPIRY_REQUIRED_DOC_TYPES = set(CRITICAL_IDENTITY_DOC_TYPES) | {"licence"}

# ── Config ───────────────────────────────────────────────────────────────────
TIER1_APPROVER_ROLES = {"sco", "admin"}
TIER2_APPROVER_ROLES = {"sco", "admin"}
SENIOR_ROLES = {"sco", "admin"}
INITIATOR_ROLES = {"co", "sco", "admin"}
PENDING_REVIEW_UNIQUE_INDEX = "uq_monitoring_review_requests_one_pending"
TRANSITION_EVIDENCE_KEYS = {
    "case_identifier",
    "screening_case_id",
    "document_id",
    "document_request_id",
}

# Clearing outcomes subject to tier control (save_decision outcomes + the
# dismiss action). Escalation / routing / info-request are intentionally absent.
CLEARING_SAVE_DECISION_OUTCOMES = {
    "accept_updated_document",
    "false_positive",
    "no_material_impact",
    "waive_with_reason",
    "mark_already_updated",
}

# Tier-1 taxonomy tokens (TF / PF / regulatory-reputational) that are not yet
# distinct alert_type values; matched against type+summary. Ambiguous screening
# defaults to Tier 1 (fail-safe: more control, never less).
_TF_PF_REG_TOKENS = (
    "terror", "terrorist_financing", "financing_of_terrorism",
    "prolifer", "proliferation",
    "regulatory", "reputational", "enforcement", "regulatory_action",
)
_SCREENING_ISH_TOKENS = (
    "screen", "watch", "match", "sanction", "pep", "terror", "prolifer",
    "adverse", "media",
)


class DismissalControlError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        self.status_code = status_code
        super().__init__(message)


def _token(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "", str(value or "").strip().lower().replace("-", "_").replace(" ", "_"))


def _get(alert: Any, key: str, default=None):
    if alert is None:
        return default
    if hasattr(alert, "get"):
        v = alert.get(key, default)
        return default if v is None else v
    try:
        v = alert[key]
        return default if v is None else v
    except (KeyError, IndexError, TypeError):
        return default


def _write_audit(audit_writer, *args, **kwargs) -> None:
    if not callable(audit_writer):
        from monitoring_alert_state_machine import AuditInfrastructureError
        raise AuditInfrastructureError(
            "Monitoring review-control audit infrastructure is unavailable."
        )
    try:
        audit_writer(*args, **kwargs)
    except Exception as exc:
        from monitoring_alert_state_machine import AuditInfrastructureError
        if isinstance(exc, AuditInfrastructureError):
            raise
        raise AuditInfrastructureError(
            "Monitoring review-control audit write failed."
        ) from exc


# ── Tier classification ──────────────────────────────────────────────────────
def classify_alert_tier(alert: Any) -> int:
    """Return 1 (highest/material screening), 2 (document-material identity),
    or 3 (low-risk). Ambiguous screening → Tier 1 (fail-safe)."""
    if alert is None:
        return 3
    atype = _token(_get(alert, "alert_type") or _get(alert, "type") or "")
    summary = str(_get(alert, "summary") or "").lower()
    sev = _token(_get(alert, "severity"))

    # Tier 1 — sanctions / watchlist / PEP (reuse M1.1 helper).
    if ms.is_high_risk_screening_alert(alert):
        return 1
    # Tier 1 — material adverse media (high/critical severity).
    if "adverse" in atype or "media" in atype or "adverse media" in summary:
        return 1 if sev in ("high", "critical") else 3
    # Tier 1 — TF / PF / regulatory-reputational.
    if any(t in atype or t in summary for t in _TF_PF_REG_TOKENS):
        return 1
    # Tier 1 — ambiguous screening-ish alert we could not cleanly classify.
    if any(t in atype or t in summary for t in _SCREENING_ISH_TOKENS):
        return 1
    # Tier 2 — document-material clears on identity/licence docs.
    if "expired" in atype or "stale" in atype or "document_expired" in atype or "document_stale" in atype:
        if any(idt in summary for idt in EXPIRY_REQUIRED_DOC_TYPES):
            return 2
    return 3


def is_clearing_action(*, action: str, outcome: Optional[str] = None) -> bool:
    """True when the requested action/outcome would CLEAR/CLOSE an alert."""
    act = _token(action)
    if act == "dismiss":
        return True
    if act == "save_decision":
        return _token(outcome) in CLEARING_SAVE_DECISION_OUTCOMES
    return False


def requires_control(alert: Any, *, action: str, outcome: Optional[str] = None,
                     dismissal_reason: Optional[str] = None) -> bool:
    """True when this clearing decision needs the senior-override control."""
    if not is_clearing_action(action=action, outcome=outcome):
        return False
    # RDI-008 reconciliation: any CRITICAL-severity clearing action always goes
    # through the senior/four-eyes control, so M2.2 and the service-layer
    # critical-dismissal gate (monitoring_routing.dismiss_alert) never disagree.
    # Without this, a CRITICAL alert that classifies tier-2-duplicate (or an
    # otherwise tier-3 shape) would slip the M2.2 control yet still be refused
    # by the service gate — surfacing a raw 400 to the officer instead of
    # routing them to a pending second review.
    if _token(_get(alert, "severity")) == "critical":
        return True
    tier = classify_alert_tier(alert)
    if tier == 3:
        return False
    # Tier 2: an obvious duplicate is single-officer; other clears controlled.
    if tier == 2 and _token(action) == "dismiss" and _token(dismissal_reason) == "duplicate":
        return False
    return True


def requires_evidence(tier: int, alert: Any) -> bool:
    """Documented evidence is mandatory for a Tier-1 clearance AND for ANY
    CRITICAL-severity alert (RDI-008 follow-up).

    Without the severity clause a CRITICAL alert that classifies Tier-2 (obvious
    document duplicate) or Tier-3 could be direct-cleared / requested by a senior
    with no evidence, defeating RDI-008's "senior four-eyes PLUS documented
    evidence" requirement."""
    return tier == 1 or _token(_get(alert, "severity")) == "critical"


def assert_evidence_current(alert: Any, evidence_ref: Any) -> None:
    """Approval-time evidence revalidation (Codex round-2, RDI-008 TOCTOU).

    A pending request's evidence was validated against the alert AS IT WAS at
    request creation. If the alert has since escalated (e.g. document-health
    re-scoring raised severity to critical) — or the request predates the
    critical-evidence rule — executing the approval would clear a CRITICAL
    alert with no documented evidence. Re-check ``requires_evidence`` against
    the CURRENT alert and the STORED request evidence immediately before the
    terminal clear; refuse (409, state changed) rather than execute.
    """
    tier = classify_alert_tier(alert)
    if requires_evidence(tier, alert) and not str(evidence_ref or "").strip():
        raise DismissalControlError(
            "This alert now requires documented evidence for clearance (its "
            "severity/tier escalated after the review request was created, or "
            "the request predates the evidence rule). Reject this request and "
            "submit a new clearance request with an evidence note.",
            409,
        )


def approver_roles_for_tier(tier: int) -> set:
    return TIER1_APPROVER_ROLES if tier == 1 else TIER2_APPROVER_ROLES


def is_senior(user: Any) -> bool:
    return str((user or {}).get("role") or "").strip().lower() in SENIOR_ROLES


def assert_authoritative_senior(db, user: Any) -> None:
    """Require an active authoritative SCO/admin record for direct clearance.

    ``record_senior_clear`` is a reusable service helper, so HTTP RBAC alone is
    insufficient. A caller-supplied role must match the current users row and
    that authoritative role must be senior before a ``senior_cleared`` ledger
    record can be minted.
    """
    user_id = str((user or {}).get("sub") or "").strip()
    claimed_role = _token((user or {}).get("role"))
    if not user_id:
        raise DismissalControlError(
            "Only an active Senior Compliance Officer or Administrator can "
            "clear this alert directly.",
            403,
        )
    row = db.execute(
        "SELECT id, role, status FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    authoritative_role = _token(_get(row, "role"))
    authoritative_status = _token(_get(row, "status", "active"))
    if (
        row is None
        or authoritative_status != "active"
        or authoritative_role not in SENIOR_ROLES
        or claimed_role != authoritative_role
    ):
        raise DismissalControlError(
            "Only an active Senior Compliance Officer or Administrator can "
            "clear this alert directly.",
            403,
        )


def _authoritative_active_officer(
    db,
    user: Any,
    *,
    allowed_roles: set,
    error_message: str,
) -> Dict[str, Any]:
    """Resolve an asserted actor against the active authoritative users row."""

    user_id = str((user or {}).get("sub") or "").strip()
    claimed_role = _token((user or {}).get("role"))
    if not user_id or claimed_role not in allowed_roles:
        raise DismissalControlError(error_message, 403)
    row = db.execute(
        "SELECT id, role, status FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    authoritative = dict(row) if row else {}
    if (
        not authoritative
        or _token(authoritative.get("status") or "active") != "active"
        or _token(authoritative.get("role")) not in allowed_roles
        or _token(authoritative.get("role")) != claimed_role
    ):
        raise DismissalControlError(error_message, 403)
    return authoritative


def assert_authoritative_initiator(db, user: Any) -> Dict[str, Any]:
    """Require a named active officer before minting four-eyes intent."""

    return _authoritative_active_officer(
        db,
        user,
        allowed_roles=INITIATOR_ROLES,
        error_message=(
            "Only an active Compliance Officer, Senior Compliance Officer, "
            "or Administrator can request alert clearance."
        ),
    )


# ── Request table CRUD ───────────────────────────────────────────────────────
def _control_text(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if len(text) > MAX_TEXT_EVIDENCE_LENGTH:
        raise DismissalControlError(
            f"{field} exceeds the "
            f"{MAX_TEXT_EVIDENCE_LENGTH}-character limit.",
            400,
        )
    return text


def _transition_evidence_json(evidence: Optional[Dict[str, Any]]) -> str:
    if evidence is None:
        return "{}"
    if not isinstance(evidence, dict):
        raise DismissalControlError("Transition evidence must be a keyed object.", 400)
    normalized = {}
    for raw_key, raw_value in evidence.items():
        key = str(raw_key or "").strip()
        if key not in TRANSITION_EVIDENCE_KEYS:
            raise DismissalControlError(
                f"Unsupported clearance evidence type: {key or '<blank>'}.",
                400,
            )
        if (
            raw_value is None
            or isinstance(raw_value, bool)
            or isinstance(raw_value, (dict, list, tuple, set))
            or not str(raw_value).strip()
        ):
            raise DismissalControlError(
                f"Clearance evidence {key} must be a non-blank identifier.",
                400,
            )
        if len(str(raw_value).strip()) > MAX_TEXT_EVIDENCE_LENGTH:
            raise DismissalControlError(
                f"Clearance evidence {key} exceeds the "
                f"{MAX_TEXT_EVIDENCE_LENGTH}-character limit.",
                400,
            )
        normalized[key] = raw_value
    return json.dumps(normalized, default=str, sort_keys=True)


def transition_evidence_for_request(request: Any) -> Dict[str, Any]:
    raw = _get(request, "transition_evidence", "{}")
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError) as exc:
        raise DismissalControlError(
            "The clearance request has invalid typed transition evidence.",
            409,
        ) from exc
    if not isinstance(parsed, dict):
        raise DismissalControlError(
            "The clearance request has invalid typed transition evidence.",
            409,
        )
    # Re-run the exact-key/type allowlist on replay so a malformed or
    # historically hand-edited row can never broaden transition authority.
    return json.loads(_transition_evidence_json(parsed))


def open_request_for_alert(db, alert_id) -> Optional[Dict[str, Any]]:
    row = db.execute(
        "SELECT * FROM monitoring_alert_review_requests "
        "WHERE alert_id = ? AND state = 'pending' ORDER BY id DESC LIMIT 1",
        (alert_id,),
    ).fetchone()
    return dict(row) if row else None


def has_pending_request(db, alert_id) -> bool:
    return open_request_for_alert(db, alert_id) is not None


def _lock_and_revalidate_alert(db, alert: Any) -> Dict[str, Any]:
    """Acquire the canonical Alert-first lock and revalidate current state.

    Review-control writers always take the Monitoring Alert row lock before
    reading or changing a review-request row. This serializes pending/pending
    and pending/senior-clear races and establishes one lock order shared with
    approval/rejection handlers.
    """
    from monitoring_alert_state_machine import (
        CANONICAL_STATUS_SET,
        HANDOFF_STATUSES,
        TERMINAL_STATUSES,
        AlertNotFound,
        lock_alert_for_transition,
    )

    alert_id = _get(alert, "id")
    if alert_id in (None, ""):
        raise DismissalControlError("Monitoring Alert not found.", 404)
    try:
        locked = lock_alert_for_transition(db, alert_id)
    except AlertNotFound as exc:
        raise DismissalControlError("Monitoring Alert not found.", 404) from exc

    status = str(_get(locked, "status") or "").strip()
    if status not in CANONICAL_STATUS_SET:
        raise DismissalControlError(
            "The stored Monitoring Alert status is not canonical.",
            409,
        )
    if status in TERMINAL_STATUSES or status in HANDOFF_STATUSES:
        raise DismissalControlError(
            "Closed or downstream-routed Monitoring Alerts cannot receive a "
            "clearance request.",
            409,
        )
    return locked


def _is_pending_unique_conflict(exc: Exception) -> bool:
    """Recognize only the narrow pending-review uniqueness violation."""
    diag = getattr(exc, "diag", None)
    constraint = str(getattr(diag, "constraint_name", "") or "")
    if constraint == PENDING_REVIEW_UNIQUE_INDEX:
        return True
    text = str(exc or "").lower()
    return (
        PENDING_REVIEW_UNIQUE_INDEX.lower() in text
        or (
            "unique constraint failed" in text
            and "monitoring_alert_review_requests.alert_id" in text
        )
    )


def _insert_request(db, *, alert_id, tier, requested_outcome, dismissal_reason,
                    rationale, evidence_ref, transition_evidence,
                    source_alert_status, initiated_by, state,
                    second_review_bypassed=0,
                    approved_by=None, approval_note=None):
    insert_sql = """
        INSERT INTO monitoring_alert_review_requests
            (alert_id, tier, requested_outcome, dismissal_reason, rationale,
             evidence_ref, transition_evidence, source_alert_status, state,
             initiated_by, second_review_bypassed, approved_by, approval_note,
             approved_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, CASE WHEN ? IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END)
    """
    params = (
        alert_id, tier, requested_outcome, dismissal_reason, rationale,
        evidence_ref, transition_evidence, source_alert_status, state, initiated_by,
        1 if second_review_bypassed else 0,
        approved_by, approval_note, approved_by,
    )
    if getattr(db, "is_postgres", False):
        row = db.execute(insert_sql + " RETURNING *", params).fetchone()
    else:
        db.execute(insert_sql, params)
        row = db.execute(
            "SELECT * FROM monitoring_alert_review_requests "
            "WHERE id = last_insert_rowid()"
        ).fetchone()
    if row is None or str(_get(row, "alert_id")) != str(alert_id):
        raise RuntimeError("Monitoring review-control insert could not be recovered.")
    return dict(row) if row else None


def create_pending_request(db, *, alert, tier, requested_outcome, dismissal_reason,
                           rationale, evidence_ref, user, audit_writer,
                           transition_evidence=None,
                           commit: bool = False) -> Dict[str, Any]:
    """CO/officer (or a senior electing second review) initiates a pending request.
    Validates mandatory rationale (+ evidence for Tier 1). Does NOT clear the
    alert. The caller owns the transaction; this helper never commits."""
    alert = _lock_and_revalidate_alert(db, alert)
    alert_id = _get(alert, "id")
    initiator = assert_authoritative_initiator(db, user)
    initiator_id = str(initiator.get("id") or "").strip()
    tier = classify_alert_tier(alert)
    rationale = _control_text(rationale, field="Clearance rationale")
    evidence_ref = _control_text(evidence_ref, field="Clearance evidence note")
    dismissal_reason = _control_text(
        dismissal_reason,
        field="Clearance dismissal reason",
    )
    requested_outcome = _control_text(
        requested_outcome,
        field="Clearance requested outcome",
    )
    if not rationale:
        raise DismissalControlError("A rationale is required to request clearance of this alert.", 400)
    if requires_evidence(tier, alert) and not evidence_ref:
        raise DismissalControlError("An evidence note is required to request clearance of a sanctions/PEP/watchlist/adverse-media or CRITICAL-severity alert.", 400)
    if has_pending_request(db, alert_id):
        raise DismissalControlError("A review request is already pending for this alert.", 409)
    evidence_json = _transition_evidence_json(transition_evidence)
    source_alert_status = str(_get(alert, "status") or "").strip()

    try:
        request = _insert_request(
            db, alert_id=alert_id, tier=tier, requested_outcome=requested_outcome,
            dismissal_reason=dismissal_reason, rationale=rationale, evidence_ref=evidence_ref,
            transition_evidence=evidence_json,
            source_alert_status=source_alert_status,
            initiated_by=initiator_id, state="pending",
        )
    except Exception as exc:
        if _is_pending_unique_conflict(exc):
            raise DismissalControlError(
                "A review request is already pending for this alert.",
                409,
            ) from exc
        raise
    _write_audit(
        audit_writer,
        dict(user or {}),
        "monitoring.alert.dismissal_requested",
        f"monitoring_alert:{alert_id}",
        json.dumps({
            "request_id": (request or {}).get("id"),
            "alert_id": alert_id,
            "tier": tier,
            "requested_outcome": requested_outcome,
            "dismissal_reason": dismissal_reason,
            "has_evidence": bool((evidence_ref or "").strip()),
            "transition_evidence_types": sorted(json.loads(evidence_json)),
            "source_alert_status": source_alert_status,
            "initiated_by": initiator_id,
            "actor_role": (user or {}).get("role", ""),
        }, sort_keys=True),
        db=db,
        after_state={"review_request_state": "pending"},
        commit=False,
    )
    return request


def record_senior_clear(db, *, alert, tier, requested_outcome, dismissal_reason,
                        rationale, evidence_ref, user, audit_writer,
                        transition_evidence=None,
                        commit: bool = False) -> Dict[str, Any]:
    """SCO/admin direct clear: validate enhanced rationale, record a
    `senior_cleared` ledger row + `dismissal_senior_cleared` audit with the
    bypass flag. Caller then runs the terminal action in the same transaction."""
    alert = _lock_and_revalidate_alert(db, alert)
    assert_authoritative_senior(db, user)
    alert_id = _get(alert, "id")
    tier = classify_alert_tier(alert)
    rationale = _control_text(rationale, field="Senior clearance rationale")
    evidence_ref = _control_text(
        evidence_ref,
        field="Senior clearance evidence note",
    )
    dismissal_reason = _control_text(
        dismissal_reason,
        field="Senior clearance dismissal reason",
    )
    requested_outcome = _control_text(
        requested_outcome,
        field="Senior clearance requested outcome",
    )
    if not rationale:
        raise DismissalControlError("Senior direct clearance requires an enhanced rationale.", 400)
    if requires_evidence(tier, alert) and not evidence_ref:
        raise DismissalControlError("Senior direct clearance of a sanctions/PEP/watchlist/adverse-media or CRITICAL-severity alert requires an evidence note.", 400)
    if has_pending_request(db, alert_id):
        raise DismissalControlError(
            "A review request is already pending for this alert; approve or reject it instead of clearing directly.",
            409,
        )
    evidence_json = _transition_evidence_json(transition_evidence)
    source_alert_status = str(_get(alert, "status") or "").strip()

    request = _insert_request(
        db, alert_id=alert_id, tier=tier, requested_outcome=requested_outcome,
        dismissal_reason=dismissal_reason, rationale=rationale, evidence_ref=evidence_ref,
        transition_evidence=evidence_json,
        source_alert_status=source_alert_status,
        initiated_by=(user or {}).get("sub", ""), state="senior_cleared",
        second_review_bypassed=1,
        approved_by=(user or {}).get("sub", ""), approval_note="senior_direct_clear",
    )
    _write_audit(
        audit_writer,
        dict(user or {}),
        "monitoring.alert.dismissal_senior_cleared",
        f"monitoring_alert:{alert_id}",
        json.dumps({
            "request_id": (request or {}).get("id"),
            "alert_id": alert_id,
            "tier": tier,
            "actor_role": (user or {}).get("role", ""),
            "rationale": rationale,
            "evidence_note": evidence_ref or "",
            "transition_evidence_types": sorted(json.loads(evidence_json)),
            "source_alert_status": source_alert_status,
            "outcome": requested_outcome,
            "dismissal_reason": dismissal_reason,
            "alert_type": _get(alert, "alert_type"),
            "severity": _get(alert, "severity"),
            "second_review_bypassed": True,
        }, default=str, sort_keys=True),
        db=db,
        after_state={"second_review_bypassed": True},
        commit=False,
    )
    return request


def assert_can_review(db, request, approver) -> None:
    """Validate approver eligibility WITHOUT mutating. Raises on wrong role or
    self-review (four-eyes). Used before any terminal action so approval and
    clearing can be sequenced atomically by the caller."""
    approver_row = _authoritative_active_officer(
        db,
        approver,
        allowed_roles=SENIOR_ROLES,
        error_message=(
            "Only an active Senior Compliance Officer or Administrator can "
            "action this clearance."
        ),
    )
    approver_id = str(approver_row.get("id") or "").strip()
    role = _token(approver_row.get("role"))
    tier = int(request.get("tier") or 1)
    if role not in approver_roles_for_tier(tier):
        raise DismissalControlError("Only a Senior Compliance Officer or Administrator can action this clearance.", 403)
    initiated_by = str(request.get("initiated_by") or "").strip()
    if not initiated_by:
        raise DismissalControlError(
            "This clearance request has no authoritative initiating officer; "
            "reject it and submit a new request.",
            409,
        )
    maker = db.execute(
        "SELECT id, role, status FROM users WHERE id = ?",
        (initiated_by,),
    ).fetchone()
    if (
        maker is None
        or _token(_get(maker, "status", "active")) != "active"
        or _token(_get(maker, "role")) not in INITIATOR_ROLES
    ):
        raise DismissalControlError(
            "This clearance request has no authoritative active initiating "
            "officer; reject it and submit a new request.",
            409,
        )
    if approver_id == initiated_by:
        raise DismissalControlError("The same user cannot review their own clearance request (four-eyes).", 403)


def assert_request_source_status_current(request: Any, alert: Any) -> None:
    """Fail closed unless approval is bound to the exact creation-time status.

    The caller must invoke this only after locking the authoritative alert row
    and the review-request row in the canonical alert-first order. Historical
    requests without the additive source-status field are intentionally not
    guessed; they must be rejected and recreated against current state.
    """
    from monitoring_alert_state_machine import CANONICAL_STATUS_SET

    requested_status = str(
        _get(request, "source_alert_status") or ""
    ).strip()
    current_status = str(_get(alert, "status") or "").strip()
    if requested_status not in CANONICAL_STATUS_SET:
        raise DismissalControlError(
            "This review request is not bound to a canonical source status. "
            "Reject it and submit a new clearance request.",
            409,
        )
    if current_status not in CANONICAL_STATUS_SET:
        raise DismissalControlError(
            "The stored Monitoring Alert status is not canonical.",
            409,
        )
    if requested_status != current_status:
        raise DismissalControlError(
            "The Monitoring Alert status changed after this review request "
            "was created. Reject it and submit a new clearance request.",
            409,
        )


def _current_state(db, request_id):
    row = db.execute(
        "SELECT state FROM monitoring_alert_review_requests WHERE id = ?", (request_id,)
    ).fetchone()
    return (dict(row).get("state") if row else None)


def mark_request_approved(db, *, request, approver, approval_note, audit_writer,
                          commit: bool = False) -> None:
    """Transition a still-pending request to approved. Verifies the row was
    actually pending (DBConnection has no rowcount) so a race/duplicate cannot
    record a false approval. The caller owns the transaction and must run the
    terminal transition in the same unit."""
    assert_can_review(db, request, approver)
    approver_id = (approver or {}).get("sub", "")
    tier = int(request.get("tier") or 1)
    db.execute(
        """
        UPDATE monitoring_alert_review_requests
           SET state = 'approved', approved_by = ?, approved_at = CURRENT_TIMESTAMP,
               approval_note = ?
         WHERE id = ? AND state = 'pending'
        """,
        (approver_id, (approval_note or "approved"), request.get("id")),
    )
    if _current_state(db, request.get("id")) != "approved":
        raise DismissalControlError("This review request is no longer pending.", 409)
    _write_audit(
        audit_writer,
        dict(approver or {}),
        "monitoring.alert.dismissal_approved",
        f"monitoring_alert:{request.get('alert_id')}",
        json.dumps({
            "request_id": request.get("id"),
            "alert_id": request.get("alert_id"),
            "tier": tier,
            "initiated_by": request.get("initiated_by"),
            "approved_by": approver_id,
            "outcome": request.get("requested_outcome"),
        }, sort_keys=True),
        db=db,
        after_state={"review_request_state": "approved"},
        commit=False,
    )


def reject_request(db, *, request, approver, rejection_reason, audit_writer,
                   commit: bool = False) -> None:
    assert_can_review(db, request, approver)
    if not (rejection_reason or "").strip():
        raise DismissalControlError("A rejection reason is required.", 400)
    approver_id = (approver or {}).get("sub", "")
    db.execute(
        """
        UPDATE monitoring_alert_review_requests
           SET state = 'rejected', approved_by = ?, approved_at = CURRENT_TIMESTAMP,
               rejection_reason = ?
         WHERE id = ? AND state = 'pending'
        """,
        (approver_id, rejection_reason, request.get("id")),
    )
    if _current_state(db, request.get("id")) != "rejected":
        raise DismissalControlError("This review request is no longer pending.", 409)
    _write_audit(
        audit_writer,
        dict(approver or {}),
        "monitoring.alert.dismissal_rejected",
        f"monitoring_alert:{request.get('alert_id')}",
        json.dumps({
            "request_id": request.get("id"),
            "alert_id": request.get("alert_id"),
            "rejected_by": approver_id,
            "rejection_reason": rejection_reason,
        }, sort_keys=True),
        db=db,
        after_state={"review_request_state": "rejected"},
        commit=False,
    )


def audit_blocked(db, *, alert_id, user, reason, detail, audit_writer,
                  commit: bool = False) -> None:
    """Emit the retained dismissal_blocked signal (self-approval / wrong role /
    missing rationale)."""
    _write_audit(
        audit_writer,
        dict(user or {}),
        "monitoring.alert.dismissal_blocked",
        f"monitoring_alert:{alert_id}",
        json.dumps({"alert_id": alert_id, "reason": reason, **(detail or {})}, sort_keys=True),
        db=db,
        after_state={"blocked": True},
        commit=False,
    )


def fetch_request(db, request_id, *, for_update: bool = False) -> Optional[Dict[str, Any]]:
    sql = "SELECT * FROM monitoring_alert_review_requests WHERE id = ?"
    if for_update and getattr(db, "is_postgres", False):
        sql += " FOR UPDATE"
    row = db.execute(sql, (request_id,)).fetchone()
    return dict(row) if row else None


def pending_requests_for_approver(db, approver, *, limit: int = 100) -> List[Dict[str, Any]]:
    """Approver queue: pending requests this approver is eligible to action
    (excludes their own-initiated requests)."""
    approver_id = (approver or {}).get("sub", "")
    rows = db.execute(
        """
        SELECT r.*, a.alert_type, a.severity, a.summary, a.application_id, a.client_name
          FROM monitoring_alert_review_requests r
          LEFT JOIN monitoring_alerts a ON a.id = r.alert_id
         WHERE r.state = 'pending' AND COALESCE(r.initiated_by,'') <> ?
         ORDER BY r.id ASC
         LIMIT ?
        """,
        (approver_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


__all__ = [
    "DismissalControlError",
    "PENDING_REVIEW_UNIQUE_INDEX",
    "TIER1_APPROVER_ROLES",
    "TRANSITION_EVIDENCE_KEYS",
    "approver_roles_for_tier",
    "assert_authoritative_senior",
    "assert_can_review",
    "assert_request_source_status_current",
    "audit_blocked",
    "classify_alert_tier",
    "create_pending_request",
    "fetch_request",
    "has_pending_request",
    "is_clearing_action",
    "is_senior",
    "mark_request_approved",
    "open_request_for_alert",
    "pending_requests_for_approver",
    "record_senior_clear",
    "reject_request",
    "requires_control",
    "transition_evidence_for_request",
]
