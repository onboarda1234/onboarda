"""Tests for monitoring_status — the consolidated Monitoring Alert status model.

Covers: canonical set, parity with the historical inline mapping, terminal set,
label/group coverage, document-refresh separation, and derived display states.
No DB, no schema, no workflow — pure mapping/helper behaviour.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitoring_status as ms


# Read-compatibility aliases. Legacy values remain readable, but normalize into
# the canonical v1 lifecycle or a derived document-request display state.
_HISTORICAL_ALIAS_MAP = {
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


def test_canonical_alert_statuses_are_exactly_the_governed_ten():
    from monitoring_alert_state_machine import CANONICAL_STATUSES

    assert ms.CANONICAL_ALERT_STATUSES == CANONICAL_STATUSES
    assert len(ms.CANONICAL_ALERT_STATUSES) == 10
    assert set(ms.CANONICAL_ALERT_STATUSES) == {
        "open", "triaged", "assigned", "in_review", "escalated",
        "routed_to_review", "routed_to_edd",
        "dismissed", "resolved", "waived",
    }
    assert ms.EXTENDED_ALERT_STATUSES == ms.CANONICAL_ALERT_STATUSES
    assert "closed" not in ms.CANONICAL_ALERT_STATUSES
    assert "cancelled" not in ms.CANONICAL_ALERT_STATUSES
    assert ms.is_canonical_alert_status("open")
    assert not ms.is_canonical_alert_status("Open")
    assert not ms.is_canonical_alert_status(" open ")


def test_filter_status_parity_with_history():
    # Every historical input must fold identically under the new module.
    for raw, expected in _HISTORICAL_ALIAS_MAP.items():
        assert ms.canonical_filter_status(raw) == expected, raw
    # Unknown filter tokens remain visible rather than being relabelled open.
    assert ms.canonical_filter_status("something_new") == "something_new"
    assert ms.canonical_filter_status(None) == "open"
    assert ms.canonical_filter_status("  Routed-To-EDD ") == "routed_to_edd"


def test_terminal_set_is_decision_only_and_ignores_resolved_at_drift():
    assert ms.TERMINAL_STATUSES == frozenset({"resolved", "dismissed", "waived"})
    assert ms.is_terminal("dismissed") is True
    assert ms.is_terminal("open") is False
    assert ms.is_terminal("routed_to_edd") is False
    assert ms.is_terminal("routed_to_review") is False
    assert ms.is_terminal("escalated") is False
    # A stale timestamp cannot silently override authoritative lifecycle state.
    assert ms.is_terminal("open", resolved_at="2026-07-01T00:00:00Z") is False
    # Legacy aliases remain readable without widening the stored vocabulary.
    assert ms.is_terminal("closed") is True
    assert ms.is_terminal("cancelled") is True


def test_action_lock_is_separate_from_terminality():
    assert ms.is_action_locked("routed_to_edd") is True
    assert ms.is_action_locked("routed_to_review") is True
    assert ms.is_action_locked("resolved") is True
    assert ms.is_action_locked("open") is False
    assert ms.is_action_locked("unknown_future_status") is True
    assert ms.is_action_locked("") is True
    assert ms.is_action_locked(None) is True
    assert ms.is_terminal("routed_to_edd") is False


def test_labels_cover_every_reachable_status():
    reachable = set(_HISTORICAL_ALIAS_MAP.values()) | set(ms.CANONICAL_ALERT_STATUSES)
    for status in reachable:
        assert ms.label(status)  # non-empty
        assert ms.group(status)


def test_document_refresh_statuses_are_separate_from_alert_statuses():
    # Refresh request lifecycle values must NOT be canonical alert statuses.
    for refresh_status in ms.REFRESH_REQUEST_STATUSES:
        if refresh_status in ("waived",):
            continue  # 'waived' is a shared terminal outcome, legitimately both
        assert refresh_status not in ms.CANONICAL_ALERT_STATUSES
    assert not ms.is_canonical_alert_status("requested")
    assert not ms.is_canonical_alert_status("uploaded")


def test_refresh_overloaded_statuses_render_as_awaiting_labels():
    # Today the refresh flow overloads alert.status; those must read cleanly.
    assert ms.label("document_requested") == "Awaiting Client"
    assert ms.label("client_uploaded") == "Awaiting Officer"
    assert ms.label("under_review") == "Awaiting Officer"
    # And they fold back to the 'open' alert lifecycle (alert hasn't advanced).
    assert ms.lifecycle_status("document_requested") == "open"
    assert ms.lifecycle_status("client_uploaded") == "open"


def test_known_lifecycle_values_return_canonical_and_unknown_fails_closed():
    for raw in [
        value
        for value in list(_HISTORICAL_ALIAS_MAP) + list(ms.CANONICAL_ALERT_STATUSES)
        if value
    ]:
        assert ms.lifecycle_status(raw) in ms.CANONICAL_ALERT_STATUSES
    assert ms.lifecycle_status("escalated") == "escalated"
    assert ms.lifecycle_status("routed_to_edd") == "routed_to_edd"
    assert ms.lifecycle_status(None) is None
    assert ms.lifecycle_status("") is None
    assert ms.lifecycle_status("   ") is None
    assert ms.lifecycle_status("something_new") is None
    assert ms.group("something_new") == "unknown"
    unknown = ms.derive_display_state(
        "something_new", refresh_request_status="requested"
    )
    assert unknown["lifecycle_status"] is None
    assert unknown["status_group"] == "unknown"
    assert unknown["status_label"] == "Something New"


def test_handoffs_are_active_projection_groups_not_terminal():
    assert ms.group("routed_to_review") == "handoff"
    assert ms.group("routed_to_edd") == "handoff"
    assert ms.is_terminal("routed_to_review") is False
    assert ms.is_terminal("routed_to_edd") is False


def test_derive_display_state_awaiting_and_ready_to_close():
    # From the linked refresh request status.
    awaiting_client = ms.derive_display_state("open", refresh_request_status="requested")
    assert awaiting_client["status_label"] == "Awaiting Client"
    assert awaiting_client["status_group"] == "awaiting_client"

    awaiting_officer = ms.derive_display_state("open", refresh_request_status="uploaded")
    assert awaiting_officer["status_label"] == "Awaiting Officer"

    # Ready to Close is derived, only for non-terminal alerts.
    ready = ms.derive_display_state("open", all_required_items_cleared=True)
    assert ready["status_label"] == "Ready to Close"
    assert ready["status_group"] == "ready_to_close"

    # Terminal alerts never become "Ready to Close".
    terminal = ms.derive_display_state("resolved", all_required_items_cleared=True)
    assert terminal["status_label"] == "Resolved"
    assert terminal["is_terminal"] is True


def test_derived_labels_are_never_canonical_stored_statuses():
    # The proposed friendly labels must not leak into the stored vocabulary.
    for friendly in ("Awaiting Client", "Awaiting Officer", "Ready to Close"):
        assert ms.token(friendly) not in ms.CANONICAL_ALERT_STATUSES
