"""
Monitoring Alert status model — single source of truth (PR: STATUS-MODEL-CONSOLIDATION-1).

Purpose
-------
Consolidate the Monitoring Alert status/label vocabulary in ONE place so the
application, API, and UI stop growing a second, conflicting state machine.

Scope of this module (deliberately narrow):
- Define the canonical Monitoring **Alert** lifecycle statuses.
- Define the display labels + display groups officers see.
- Normalize the many legacy/raw status tokens that already exist in the data
  into a stable canonical filter value (behaviour-compatible with the previous
  inline ``_monitoring_list_canonical_status`` in server.py).
- Keep the document-refresh REQUEST lifecycle conceptually SEPARATE from the
  alert lifecycle (it lives on ``application_enhanced_requirements.status``).
- Expose ``derive_display_state`` so "Awaiting Client" / "Awaiting Officer" /
  "Ready to Close" can be shown as DERIVED labels — never stored.

Document-refresh request states and legacy read aliases are accepted only for
projection. They are never members of the canonical stored vocabulary.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from monitoring_alert_state_machine import (
    ACTIVE_STATUSES as _ACTIVE_STATUSES,
    CANONICAL_STATUSES as _CANONICAL_STATUSES,
    HANDOFF_STATUSES as _HANDOFF_STATUSES,
    TERMINAL_STATUSES as _TERMINAL_STATUSES,
)

# ── Canonical Monitoring ALERT lifecycle statuses ────────────────────────────
# Compatibility exports for existing read-only consumers. The state-machine
# module is authoritative; there is no longer a smaller "base" vocabulary plus
# an extended set.
CANONICAL_ALERT_STATUSES = _CANONICAL_STATUSES
EXTENDED_ALERT_STATUSES = CANONICAL_ALERT_STATUSES
TERMINAL_STATUSES = _TERMINAL_STATUSES

# ── Document-refresh REQUEST lifecycle (separate object) ─────────────────────
# Home: application_enhanced_requirements.status. Listed here ONLY so callers can
# assert separation; these must never be treated as canonical alert statuses.
REFRESH_REQUEST_STATUSES = frozenset(
    {"generated", "requested", "uploaded", "under_review", "rejected", "accepted", "waived"}
)

# Historical alert-row overloads are recognised for read compatibility only.
# New writes must keep these states on the linked refresh-request object.
_REFRESH_OVERLOADED_ALERT_STATUSES = frozenset(
    {"document_requested", "client_uploaded", "under_review", "awaiting_review", "notification_failed"}
)


def token(value: Any) -> str:
    """Normalise a raw status into a comparable token.

    Behaviour-compatible with server._monitoring_list_token.
    """
    return re.sub(r"[^a-z0-9_]+", "", str(value or "").strip().lower().replace("-", "_").replace(" ", "_"))


# ── Filter-status aliases ────────────────────────────────────────────────────
# Read aliases may normalize into a canonical stored status or a derived display
# status. They do not widen the canonical stored vocabulary.
FILTER_STATUS_ALIASES: Dict[str, str] = {
    "": "open",
    "new": "open",
    "opened": "open",
    "open": "open",
    "triaged": "triaged",
    "review": "in_review",
    "inreview": "in_review",
    "in_review": "in_review",
    "assigned": "assigned",
    "document_requested": "document_requested",
    "documents_requested": "document_requested",
    "client_document_requested": "document_requested",
    "requested": "document_requested",
    "client_uploaded": "client_uploaded",
    "uploaded": "client_uploaded",
    "under_review": "under_review",
    "underreview": "under_review",
    "awaiting_review": "awaiting_review",
    "awaitingreview": "awaiting_review",
    "notification_failed": "notification_failed",
    "notificationfailed": "notification_failed",
    "escalated": "escalated",
    "escalated_to_edd": "routed_to_edd",
    "routed_edd": "routed_to_edd",
    "routed_to_edd": "routed_to_edd",
    "route_to_edd": "routed_to_edd",
    "routed_to_review": "routed_to_review",
    "route_to_review": "routed_to_review",
    "dismissed": "dismissed",
    "closed_dismissed": "dismissed",
    "resolved": "resolved",
    "resolved_no_change": "resolved",
    "closed": "resolved",
    "waived": "waived",
    "waiver": "waived",
    "cancelled": "resolved",
    "canceled": "resolved",
}


def canonical_filter_status(value: Any) -> str:
    """Fold any raw/legacy status into the stable filter vocabulary.

    This preserves the exact behaviour of the previous inline
    ``_monitoring_list_canonical_status`` so list filtering / counts are
    unchanged. It is intentionally broader than CANONICAL_ALERT_STATUSES because
    it must still distinguish refresh-overloaded states for the UI.
    """
    tok = token(value)
    return FILTER_STATUS_ALIASES.get(tok, tok or "open")


# ── Canonical lifecycle mapping ──────────────────────────────────────────────
# Strict fold of known stored/read-alias values into the canonical lifecycle.
# Refresh-overloaded states fold back to the underlying alert lifecycle (open),
# because the alert itself has not advanced — only the refresh sub-flow has.
_LIFECYCLE_MAP: Dict[str, str] = {
    "open": "open",
    "in_review": "in_review",
    "triaged": "triaged",
    "assigned": "assigned",
    "escalated": "escalated",
    "document_requested": "open",
    "client_uploaded": "open",
    "under_review": "open",
    "awaiting_review": "open",
    "notification_failed": "open",
    "routed_to_review": "routed_to_review",
    "routed_to_edd": "routed_to_edd",
    "dismissed": "dismissed",
    "resolved": "resolved",
    "waived": "waived",
}


def lifecycle_status(value: Any) -> Optional[str]:
    """Return a canonical lifecycle status, or ``None`` for unknown values.

    Unknown/free-text values must remain visibly unmapped. Treating them as
    ``open`` would hide invalid persisted state and defeat fail-closed guards.
    """
    raw_token = token(value)
    if not raw_token:
        return None
    tok = canonical_filter_status(raw_token)
    return _LIFECYCLE_MAP.get(tok)


def is_canonical_alert_status(value: Any) -> bool:
    # This predicate answers whether a value is safe to persist, not whether it
    # can be interpreted as a read alias. Stored values are exact and lowercase.
    return isinstance(value, str) and value in CANONICAL_ALERT_STATUSES


def is_terminal(value: Any, resolved_at: Any = None) -> bool:
    """Return terminality from canonical status only.

    ``resolved_at`` remains accepted for call-site compatibility, but timestamp
    drift cannot turn an active/handoff status into a terminal decision.
    """
    del resolved_at
    return canonical_filter_status(value) in TERMINAL_STATUSES


def is_action_locked(value: Any) -> bool:
    """Return whether Monitoring officer actions are prohibited for the state.

    Handoffs are deliberately nonterminal, but their downstream owner controls
    the next decision.  Keeping this separate from terminality prevents list
    filtering and counters from misclassifying routed alerts merely to disable
    Monitoring controls.
    """
    lifecycle = lifecycle_status(value)
    return lifecycle in _HANDOFF_STATUSES or lifecycle in TERMINAL_STATUSES


# ── Display labels + groups ──────────────────────────────────────────────────
# Labels are keyed on the canonical FILTER status so every current stored value
# (including refresh-overloaded ones) resolves to a clean, officer-facing label.
STATUS_LABELS: Dict[str, str] = {
    "open": "Open",
    "triaged": "Triaged",
    "in_review": "In Review",
    "assigned": "Assigned",
    "document_requested": "Awaiting Client",
    "client_uploaded": "Awaiting Officer",
    "under_review": "Awaiting Officer",
    "awaiting_review": "Awaiting Officer",
    "notification_failed": "Notification Failed",
    "escalated": "Escalated",
    "routed_to_review": "Routed to Review",
    "routed_to_edd": "Routed to EDD",
    "dismissed": "Dismissed",
    "resolved": "Resolved",
    "waived": "Waived",
}

# Display group buckets used for grouping/soft-colouring in the UI.
_STATUS_GROUPS: Dict[str, str] = {
    "open": "active",
    "triaged": "active",
    "in_review": "active",
    "assigned": "active",
    "document_requested": "awaiting_client",
    "notification_failed": "awaiting_client",
    "client_uploaded": "awaiting_officer",
    "under_review": "awaiting_officer",
    "awaiting_review": "awaiting_officer",
    "escalated": "escalated",
    "routed_to_review": "handoff",
    "routed_to_edd": "handoff",
    "dismissed": "terminal",
    "resolved": "terminal",
    "waived": "terminal",
}


def label(value: Any) -> str:
    tok = canonical_filter_status(value)
    return STATUS_LABELS.get(tok, (str(value or "open").replace("_", " ").title() or "Open"))


def group(value: Any) -> str:
    return _STATUS_GROUPS.get(canonical_filter_status(value), "unknown")


# Refresh-request status -> effective alert display/filter status key.
# Used so list filters and detail views keep their historical behaviour after
# the refresh flow stopped overloading monitoring_alerts.status (M1.1).
REFRESH_REQUEST_TO_EFFECTIVE_STATUS: Dict[str, str] = {
    "requested": "document_requested",
    "uploaded": "client_uploaded",
    "under_review": "under_review",
    # A rejected replacement re-opens the request from the client's viewpoint.
    "rejected": "document_requested",
}


def effective_status(alert_status: Any, refresh_request_status: Optional[Any] = None,
                     resolved_at: Any = None) -> str:
    """Effective display/filter status key for an alert.

    When the alert is non-terminal and a linked refresh request is active,
    the request's state drives the effective key (Awaiting Client/Officer
    semantics). Otherwise the alert's own canonical filter status is used.
    Stored legacy overloads (document_requested etc.) fall through unchanged
    via canonical_filter_status, so pre-M1.1 rows keep working.
    """
    base = canonical_filter_status(alert_status)
    lifecycle = lifecycle_status(base)
    if is_terminal(base, resolved_at=resolved_at):
        return base
    if lifecycle in _ACTIVE_STATUSES and refresh_request_status is not None:
        mapped = REFRESH_REQUEST_TO_EFFECTIVE_STATUS.get(token(refresh_request_status))
        if mapped:
            return mapped
    return base


_HIGH_RISK_SANCTIONS_TOKENS = ("sanction", "watchlist")
_HIGH_RISK_PEP_TOKENS = ("pep",)


def is_high_risk_screening_alert(alert: Any) -> bool:
    """True when the alert is a sanctions/watchlist or PEP screening alert.

    Mirrors the sanctions/pep branches of the server's canonical-type rules
    (alert_type first, summary as fallback) so the interim high-risk dismissal
    guard fires on the same alerts the UI labels sanctions_change/pep_change.
    """
    if alert is None:
        return False
    get = alert.get if hasattr(alert, "get") else lambda k, d=None: d
    raw_type = token(get("alert_type") or get("type") or "")
    if raw_type and raw_type != "fixture":
        # Explicit type wins; 'media' alert types are adverse-media, not in
        # guard scope even if the summary mentions a PEP.
        if "media" in raw_type:
            return False
        return any(
            needle in raw_type
            for needle in _HIGH_RISK_SANCTIONS_TOKENS + _HIGH_RISK_PEP_TOKENS
        )
    summary = str(get("summary") or "").lower()
    if "adverse media" in summary:
        return False
    return "sanction" in summary or "watchlist" in summary or "pep" in summary


def derive_display_state(
    alert_status: Any,
    refresh_request_status: Optional[Any] = None,
    all_required_items_cleared: Optional[bool] = None,
) -> Dict[str, Any]:
    """Compute the officer-facing display state for an alert.

    Returns a dict of DERIVED, display-only values. Nothing here is stored.

    ``refresh_request_status`` (from the linked application_enhanced_requirements
    row, when known) refines the "Awaiting …" label. When it is not supplied we
    fall back to the refresh-overloaded alert status so behaviour is stable today.
    ``all_required_items_cleared=True`` on a non-terminal alert yields the derived
    "Ready to Close" state.
    """
    lifecycle = lifecycle_status(alert_status)
    canonical = canonical_filter_status(alert_status)
    if lifecycle is None:
        display_label = (
            str(alert_status or "Unknown").replace("_", " ").strip().title()
            or "Unknown"
        )
        display_group = "unknown"
    else:
        display_label = label(canonical)
        display_group = group(canonical)

    # Refine "Awaiting …" from the linked refresh request when provided.
    if lifecycle in _ACTIVE_STATUSES and refresh_request_status is not None:
        rtok = token(refresh_request_status)
        if rtok in {"requested"}:
            display_label, display_group = "Awaiting Client", "awaiting_client"
        elif rtok in {"uploaded", "under_review"}:
            display_label, display_group = "Awaiting Officer", "awaiting_officer"

    # Ready to Close: non-terminal alert whose linked required items are all done.
    if (
        all_required_items_cleared
        and lifecycle in _ACTIVE_STATUSES
        and lifecycle not in _HANDOFF_STATUSES
        and not is_terminal(canonical)
    ):
        display_label, display_group = "Ready to Close", "ready_to_close"

    return {
        "status_label": display_label,
        "status_group": display_group,
        "lifecycle_status": lifecycle,
        "is_terminal": is_terminal(canonical),
        "is_action_locked": is_action_locked(canonical),
    }
