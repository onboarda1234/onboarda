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

# Clearing outcomes subject to tier control (save_decision outcomes + the
# dismiss action). Escalation / routing / info-request are intentionally absent.
CLEARING_SAVE_DECISION_OUTCOMES = {
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
    tier = classify_alert_tier(alert)
    if tier == 3:
        return False
    # Tier 2: an obvious duplicate is single-officer; other clears controlled.
    if tier == 2 and _token(action) == "dismiss" and _token(dismissal_reason) == "duplicate":
        return False
    return True


def approver_roles_for_tier(tier: int) -> set:
    return TIER1_APPROVER_ROLES if tier == 1 else TIER2_APPROVER_ROLES


def is_senior(user: Any) -> bool:
    return str((user or {}).get("role") or "").strip().lower() in SENIOR_ROLES


# ── Request table CRUD ───────────────────────────────────────────────────────
def open_request_for_alert(db, alert_id) -> Optional[Dict[str, Any]]:
    row = db.execute(
        "SELECT * FROM monitoring_alert_review_requests "
        "WHERE alert_id = ? AND state = 'pending' ORDER BY id DESC LIMIT 1",
        (alert_id,),
    ).fetchone()
    return dict(row) if row else None


def has_pending_request(db, alert_id) -> bool:
    return open_request_for_alert(db, alert_id) is not None


def _insert_request(db, *, alert_id, tier, requested_outcome, dismissal_reason,
                    rationale, evidence_ref, initiated_by, state,
                    second_review_bypassed=0,
                    approved_by=None, approval_note=None):
    db.execute(
        """
        INSERT INTO monitoring_alert_review_requests
            (alert_id, tier, requested_outcome, dismissal_reason, rationale,
             evidence_ref, state, initiated_by, second_review_bypassed,
             approved_by, approval_note, approved_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?, CASE WHEN ? IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END)
        """,
        (
            alert_id, tier, requested_outcome, dismissal_reason, rationale,
            evidence_ref, state, initiated_by, 1 if second_review_bypassed else 0,
            approved_by, approval_note, approved_by,
        ),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM monitoring_alert_review_requests WHERE alert_id = ? ORDER BY id DESC LIMIT 1",
        (alert_id,),
    ).fetchone()
    return dict(row) if row else None


def create_pending_request(db, *, alert, tier, requested_outcome, dismissal_reason,
                           rationale, evidence_ref, user, audit_writer) -> Dict[str, Any]:
    """CO/officer (or a senior electing second review) initiates a pending request.
    Validates mandatory rationale (+ evidence for Tier 1). Does NOT clear the alert."""
    alert_id = _get(alert, "id")
    if not (rationale or "").strip():
        raise DismissalControlError("A rationale is required to request clearance of this alert.", 400)
    if tier == 1 and not (evidence_ref or "").strip():
        raise DismissalControlError("An evidence note is required for sanctions/PEP/watchlist/adverse-media clearance requests.", 400)
    if has_pending_request(db, alert_id):
        raise DismissalControlError("A review request is already pending for this alert.", 409)

    request = _insert_request(
        db, alert_id=alert_id, tier=tier, requested_outcome=requested_outcome,
        dismissal_reason=dismissal_reason, rationale=rationale, evidence_ref=evidence_ref,
        initiated_by=(user or {}).get("sub", ""), state="pending",
    )
    audit_writer(
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
            "initiated_by": (user or {}).get("sub", ""),
            "actor_role": (user or {}).get("role", ""),
        }, sort_keys=True),
        db=db,
        after_state={"review_request_state": "pending"},
    )
    db.commit()
    return request


def record_senior_clear(db, *, alert, tier, requested_outcome, dismissal_reason,
                        rationale, evidence_ref, user, audit_writer) -> Dict[str, Any]:
    """SCO/admin direct clear: validate enhanced rationale, record a
    `senior_cleared` ledger row + `dismissal_senior_cleared` audit with the
    bypass flag. Caller then runs the terminal action."""
    alert_id = _get(alert, "id")
    if not (rationale or "").strip():
        raise DismissalControlError("Senior direct clearance requires an enhanced rationale.", 400)
    if tier == 1 and not (evidence_ref or "").strip():
        raise DismissalControlError("Senior direct clearance of a sanctions/PEP/watchlist/adverse-media alert requires an evidence note.", 400)
    if has_pending_request(db, alert_id):
        raise DismissalControlError(
            "A review request is already pending for this alert; approve or reject it instead of clearing directly.",
            409,
        )

    request = _insert_request(
        db, alert_id=alert_id, tier=tier, requested_outcome=requested_outcome,
        dismissal_reason=dismissal_reason, rationale=rationale, evidence_ref=evidence_ref,
        initiated_by=(user or {}).get("sub", ""), state="senior_cleared",
        second_review_bypassed=1,
        approved_by=(user or {}).get("sub", ""), approval_note="senior_direct_clear",
    )
    audit_writer(
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
            "outcome": requested_outcome,
            "dismissal_reason": dismissal_reason,
            "alert_type": _get(alert, "alert_type"),
            "severity": _get(alert, "severity"),
            "second_review_bypassed": True,
        }, default=str, sort_keys=True),
        db=db,
        after_state={"second_review_bypassed": True},
    )
    db.commit()
    return request


def assert_can_review(request, approver) -> None:
    """Validate approver eligibility WITHOUT mutating. Raises on wrong role or
    self-review (four-eyes). Used before any terminal action so approval and
    clearing can be sequenced atomically by the caller."""
    approver_id = (approver or {}).get("sub", "")
    role = str((approver or {}).get("role") or "").strip().lower()
    tier = int(request.get("tier") or 1)
    if role not in approver_roles_for_tier(tier):
        raise DismissalControlError("Only a Senior Compliance Officer or Administrator can action this clearance.", 403)
    if approver_id and approver_id == (request.get("initiated_by") or ""):
        raise DismissalControlError("The same user cannot review their own clearance request (four-eyes).", 403)


def _current_state(db, request_id):
    row = db.execute(
        "SELECT state FROM monitoring_alert_review_requests WHERE id = ?", (request_id,)
    ).fetchone()
    return (dict(row).get("state") if row else None)


def mark_request_approved(db, *, request, approver, approval_note, audit_writer) -> None:
    """Transition a still-pending request to approved. Verifies the row was
    actually pending (DBConnection has no rowcount) so a race/duplicate cannot
    record a false approval. Caller must run the terminal clear FIRST so the two
    are sequenced together."""
    assert_can_review(request, approver)
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
    db.commit()
    if _current_state(db, request.get("id")) != "approved":
        raise DismissalControlError("This review request is no longer pending.", 409)
    audit_writer(
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
    )
    db.commit()


def reject_request(db, *, request, approver, rejection_reason, audit_writer) -> None:
    assert_can_review(request, approver)
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
    db.commit()
    if _current_state(db, request.get("id")) != "rejected":
        raise DismissalControlError("This review request is no longer pending.", 409)
    audit_writer(
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
    )
    db.commit()


def audit_blocked(db, *, alert_id, user, reason, detail, audit_writer) -> None:
    """Emit the retained dismissal_blocked signal (self-approval / wrong role /
    missing rationale)."""
    audit_writer(
        dict(user or {}),
        "monitoring.alert.dismissal_blocked",
        f"monitoring_alert:{alert_id}",
        json.dumps({"alert_id": alert_id, "reason": reason, **(detail or {})}, sort_keys=True),
        db=db,
        after_state={"blocked": True},
    )
    db.commit()


def fetch_request(db, request_id) -> Optional[Dict[str, Any]]:
    row = db.execute(
        "SELECT * FROM monitoring_alert_review_requests WHERE id = ?", (request_id,)
    ).fetchone()
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
    "TIER1_APPROVER_ROLES",
    "approver_roles_for_tier",
    "assert_can_review",
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
]
