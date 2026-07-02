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

Explicitly NOT in scope (later PRs):
- No DB schema, no CHECK constraint, no migration.
- No change to how any writer sets ``monitoring_alerts.status``.
- No workflow / routing / document-refresh behaviour change.

Because the document-refresh flow currently *overloads* ``monitoring_alerts.status``
with ``document_requested`` / ``client_uploaded`` / ``under_review``, this module
must (for now) recognise those values and present them as derived display states.
De-coupling that overload is a separate, later PR.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# ── Canonical Monitoring ALERT lifecycle statuses ────────────────────────────
# These 9 are the governed alert-lifecycle vocabulary. Document-refresh request
# states (requested/uploaded/under_review/rejected/...) are NOT in this set.
CANONICAL_ALERT_STATUSES = (
    "open",
    "triaged",
    "assigned",
    "routed_to_review",
    "routed_to_edd",
    "dismissed",
    "resolved",
    "closed",
    "waived",
)

# Terminal statuses (an alert here should no longer be treated as actionable).
# Mirrors server._MONITORING_LIST_TERMINAL_STATUSES exactly so guards agree.
TERMINAL_STATUSES = frozenset(
    {
        "resolved",
        "closed",
        "dismissed",
        "waived",
        "cancelled",
        "routed_to_edd",
        "routed_to_review",
    }
)

# ── Document-refresh REQUEST lifecycle (separate object) ─────────────────────
# Home: application_enhanced_requirements.status. Listed here ONLY so callers can
# assert separation; these must never be treated as canonical alert statuses.
REFRESH_REQUEST_STATUSES = frozenset(
    {"generated", "requested", "uploaded", "under_review", "rejected", "accepted", "waived"}
)

# Alert-status values that the refresh flow currently overloads onto the alert
# row. Recognised so we can render derived "Awaiting …" labels until the refresh
# lifecycle is decoupled in a later PR.
_REFRESH_OVERLOADED_ALERT_STATUSES = frozenset(
    {"document_requested", "client_uploaded", "under_review", "awaiting_review", "notification_failed"}
)


def token(value: Any) -> str:
    """Normalise a raw status into a comparable token.

    Behaviour-compatible with server._monitoring_list_token.
    """
    return re.sub(r"[^a-z0-9_]+", "", str(value or "").strip().lower().replace("-", "_").replace(" ", "_"))


# ── Filter-status aliases ────────────────────────────────────────────────────
# Copied verbatim from the previous inline server map so delegation is a no-op
# in behaviour. A parity test locks this to the historical mapping.
FILTER_STATUS_ALIASES: Dict[str, str] = {
    "": "open",
    "new": "open",
    "opened": "open",
    "open": "open",
    "triaged": "in_review",
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
    "closed": "closed",
    "waived": "waived",
    "waiver": "waived",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}


def canonical_filter_status(value: Any) -> str:
    """Fold any raw/legacy status into the stable filter vocabulary.

    This preserves the exact behaviour of the previous inline
    ``_monitoring_list_canonical_status`` so list filtering / counts are
    unchanged. It is intentionally broader than CANONICAL_ALERT_STATUSES because
    it must still distinguish refresh-overloaded states for the UI.
    """
    tok = token(value or "open")
    return FILTER_STATUS_ALIASES.get(tok, tok or "open")


# ── Canonical lifecycle mapping ──────────────────────────────────────────────
# Best-effort fold of any status into one of the 9 canonical alert statuses.
# Refresh-overloaded states fold back to the underlying alert lifecycle (open),
# because the alert itself has not advanced — only the refresh sub-flow has.
_LIFECYCLE_MAP: Dict[str, str] = {
    "open": "open",
    "new": "open",
    "opened": "open",
    "in_review": "triaged",
    "triaged": "triaged",
    "review": "triaged",
    "assigned": "assigned",
    "document_requested": "open",
    "client_uploaded": "open",
    "under_review": "open",
    "awaiting_review": "open",
    "notification_failed": "open",
    "routed_to_review": "routed_to_review",
    "routed_to_edd": "routed_to_edd",
    "escalated": "routed_to_edd",  # SCO/MLRO escalation ~ EDD-style routing
    "dismissed": "dismissed",
    "resolved": "resolved",
    "closed": "closed",
    "cancelled": "closed",
    "waived": "waived",
}


def lifecycle_status(value: Any) -> str:
    """Return the canonical alert-lifecycle status (one of CANONICAL_ALERT_STATUSES)."""
    tok = canonical_filter_status(value)
    return _LIFECYCLE_MAP.get(tok, "open")


def is_canonical_alert_status(value: Any) -> bool:
    return token(value) in CANONICAL_ALERT_STATUSES


def is_terminal(value: Any, resolved_at: Any = None) -> bool:
    """True when an alert should no longer be treated as actionable."""
    if resolved_at not in (None, ""):
        return True
    return canonical_filter_status(value) in TERMINAL_STATUSES


# ── Display labels + groups ──────────────────────────────────────────────────
# Labels are keyed on the canonical FILTER status so every current stored value
# (including refresh-overloaded ones) resolves to a clean, officer-facing label.
STATUS_LABELS: Dict[str, str] = {
    "open": "Open",
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
    "closed": "Closed",
    "waived": "Waived",
    "cancelled": "Cancelled",
}

# Display group buckets used for grouping/soft-colouring in the UI.
_STATUS_GROUPS: Dict[str, str] = {
    "open": "active",
    "in_review": "active",
    "assigned": "active",
    "document_requested": "awaiting_client",
    "notification_failed": "awaiting_client",
    "client_uploaded": "awaiting_officer",
    "under_review": "awaiting_officer",
    "awaiting_review": "awaiting_officer",
    "escalated": "escalated",
    "routed_to_review": "terminal",
    "routed_to_edd": "terminal",
    "dismissed": "terminal",
    "resolved": "terminal",
    "closed": "terminal",
    "waived": "terminal",
    "cancelled": "terminal",
}


def label(value: Any) -> str:
    tok = canonical_filter_status(value)
    return STATUS_LABELS.get(tok, (str(value or "open").replace("_", " ").title() or "Open"))


def group(value: Any) -> str:
    return _STATUS_GROUPS.get(canonical_filter_status(value), "active")


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
    canonical = canonical_filter_status(alert_status)
    display_label = label(canonical)
    display_group = group(canonical)

    # Refine "Awaiting …" from the linked refresh request when provided.
    if refresh_request_status is not None:
        rtok = token(refresh_request_status)
        if rtok in {"requested"}:
            display_label, display_group = "Awaiting Client", "awaiting_client"
        elif rtok in {"uploaded", "under_review"}:
            display_label, display_group = "Awaiting Officer", "awaiting_officer"

    # Ready to Close: non-terminal alert whose linked required items are all done.
    if all_required_items_cleared and not is_terminal(canonical):
        display_label, display_group = "Ready to Close", "ready_to_close"

    return {
        "status_label": display_label,
        "status_group": display_group,
        "lifecycle_status": lifecycle_status(canonical),
        "is_terminal": is_terminal(canonical),
    }
