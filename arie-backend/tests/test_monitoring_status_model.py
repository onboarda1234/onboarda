"""Tests for monitoring_status — the consolidated Monitoring Alert status model.

Covers: canonical set, parity with the historical inline mapping, terminal set,
label/group coverage, document-refresh separation, and derived display states.
No DB, no schema, no workflow — pure mapping/helper behaviour.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitoring_status as ms


# Frozen copy of the historical server._monitoring_list_canonical_status alias
# map. This is the parity lock: consolidation must not change filter behaviour.
_HISTORICAL_ALIAS_MAP = {
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


def test_canonical_alert_statuses_are_exactly_the_nine():
    assert ms.CANONICAL_ALERT_STATUSES == (
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


def test_filter_status_parity_with_history():
    # Every historical input must fold identically under the new module.
    for raw, expected in _HISTORICAL_ALIAS_MAP.items():
        assert ms.canonical_filter_status(raw) == expected, raw
    # Unknown tokens pass through (or default to open) exactly as before.
    assert ms.canonical_filter_status("something_new") == "something_new"
    assert ms.canonical_filter_status(None) == "open"
    assert ms.canonical_filter_status("  Routed-To-EDD ") == "routed_to_edd"


def test_terminal_set_matches_server_expectation():
    assert ms.TERMINAL_STATUSES == frozenset(
        {"resolved", "closed", "dismissed", "waived", "cancelled", "routed_to_edd", "routed_to_review"}
    )
    assert ms.is_terminal("dismissed") is True
    assert ms.is_terminal("open") is False
    # resolved_at forces terminal regardless of status text.
    assert ms.is_terminal("open", resolved_at="2026-07-01T00:00:00Z") is True


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


def test_lifecycle_status_always_returns_a_canonical_value():
    for raw in list(_HISTORICAL_ALIAS_MAP) + list(ms.CANONICAL_ALERT_STATUSES):
        assert ms.lifecycle_status(raw) in ms.CANONICAL_ALERT_STATUSES


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
