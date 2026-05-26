"""
Lifecycle Quarantine -- PR-A (Data Trust Hardening)
====================================================

Predicate-driven classifier that identifies legacy / ghost rows in
``monitoring_alerts`` that are neither lifecycle-active nor
lifecycle-historical and therefore would otherwise pollute the operator
queue or hide silently behind it.

Design contract
---------------
* Read-only. NEVER mutates the database.
* Additive. Does NOT change the shape or semantics of any existing endpoint.
* Predicate-driven, NOT id-driven. The classifier is environment-portable.
* Unioned predicates -- a row is quarantined if EITHER predicate is true.
* No protected file is modified by this module being added.
* No invented linkage. We do not "fix" ghost rows by promoting them; we
  surface them as a third bucket alongside active and historical.

The two predicates
------------------
1. **vocabulary_ghost** -- the row's status is OUTSIDE the canonical
   PR-02 routing vocabulary (open / triaged / assigned / dismissed /
   routed_to_review / routed_to_edd) AND the row carries no downstream
   linkage (no linked_periodic_review_id and no linked_edd_case_id).
   This catches rows in legacy/freeform states like ``escalated`` that
   never produced a downstream object.

2. **unscopable_no_application** -- the row has ``application_id IS
   NULL``. Such a row cannot be scoped to any application's lifecycle
   regardless of its state, and therefore cannot meaningfully appear
   in an application-scoped queue or summary.

A row matching ANY predicate is classified ``legacy_unmapped``. Both
predicates are intentionally orthogonal -- a row may match one or both,
and the materialised quarantine_reasons list reports which.

Public surface
--------------
* :data:`CANONICAL_ALERT_VOCABULARY` -- mirrors monitoring_routing.STATUS_*.
* :func:`is_legacy_unmapped` -- (bool, [reasons]) for an alert row.
* :func:`legacy_unmapped_where_clause` -- portable SQL fragment for use
  in alert SELECTs.
* :func:`active_or_historical_exclude_legacy_clause` -- the SQL fragment
  to AND into active/historical alert queries so legacy rows do not
  contaminate canonical buckets.
* :data:`QUARANTINE_REASON_VOCABULARY_GHOST` / ``..._UNSCOPABLE`` --
  reason vocabulary used in audit-log entries and UI labels.
"""

from __future__ import annotations

from typing import Any, List, Tuple

# Canonical PR-02 monitoring-alert status vocabulary. Sourced as a
# tuple literal here (and kept in sync by the parity test in
# tests/test_lifecycle_quarantine.py) rather than imported, so this
# module remains importable even if monitoring_routing fails to load
# in a test harness.
CANONICAL_ALERT_VOCABULARY: Tuple[str, ...] = (
    "open",
    "triaged",
    "assigned",
    "dismissed",
    "routed_to_review",
    "routed_to_edd",
)

QUARANTINE_REASON_VOCABULARY_GHOST = "vocabulary_ghost"
QUARANTINE_REASON_UNSCOPABLE = "unscopable_no_application"

# Stable ordering so that audit-log entries and tests can pin reason
# arrays deterministically.
QUARANTINE_REASON_ORDER: Tuple[str, ...] = (
    QUARANTINE_REASON_VOCABULARY_GHOST,
    QUARANTINE_REASON_UNSCOPABLE,
)


def _row_get(row, key, default=None):
    """Safe accessor matching the convention in lifecycle_queue."""
    if row is None:
        return default
    try:
        if key in row.keys():
            value = row[key]
            return default if value is None else value
    except (AttributeError, TypeError):
        pass
    if isinstance(row, dict):
        return row.get(key, default)
    return default


def _is_seeded_in_review(row) -> bool:
    """Treat seeded legacy 'in_review' alerts as mappable, not ghost."""
    status = str(_row_get(row, "status", "") or "").strip().lower()
    if status != "in_review":
        return False
    src = str(_row_get(row, "source_reference", "") or "").strip().upper()
    return src.startswith("FIX_SCEN")


def is_legacy_unmapped(row) -> Tuple[bool, List[str]]:
    """Classify a single ``monitoring_alerts`` row.

    Returns ``(True, [reasons])`` if the row matches one or both
    quarantine predicates, ``(False, [])`` otherwise. The reasons list
    is in :data:`QUARANTINE_REASON_ORDER` order.
    """
    reasons: List[str] = []

    # Vocabulary-ghost: state outside canonical AND no downstream linkage.
    status = _row_get(row, "status")
    linked_review = _row_get(row, "linked_periodic_review_id")
    linked_edd = _row_get(row, "linked_edd_case_id")
    if (
        status is not None
        and status not in CANONICAL_ALERT_VOCABULARY
        and not _is_seeded_in_review(row)
        and linked_review is None
        and linked_edd is None
    ):
        reasons.append(QUARANTINE_REASON_VOCABULARY_GHOST)

    # Unscopable: no application binding.
    application_id = _row_get(row, "application_id")
    if application_id is None:
        reasons.append(QUARANTINE_REASON_UNSCOPABLE)

    return (len(reasons) > 0, reasons)


def legacy_unmapped_where_clause() -> Tuple[str, List[Any]]:
    """Return ``(where_fragment, params)`` selecting only legacy_unmapped rows.

    The fragment is a parenthesised disjunction of the two predicates
    and uses ``?`` placeholders so it is portable across SQLite (used
    in tests) and the DBConnection abstraction's PostgreSQL adapter.
    Compose with ``WHERE 1=1 AND <fragment>``.
    """
    placeholders = ",".join(["?"] * len(CANONICAL_ALERT_VOCABULARY))
    fragment = (
        "("
        # vocabulary_ghost: status outside canonical AND no downstream linkage.
        f"(status NOT IN ({placeholders}) "
        "  AND NOT (LOWER(COALESCE(status, '')) = 'in_review' "
        "           AND UPPER(COALESCE(source_reference, '')) LIKE 'FIX_SCEN%') "
        "  AND linked_periodic_review_id IS NULL "
        "  AND linked_edd_case_id IS NULL)"
        " OR "
        # unscopable_no_application: no application binding.
        "(application_id IS NULL)"
        ")"
    )
    params: List[Any] = list(CANONICAL_ALERT_VOCABULARY)
    return fragment, params


def active_or_historical_exclude_legacy_clause() -> Tuple[str, List[Any]]:
    """Return ``(where_fragment, params)`` excluding legacy_unmapped rows.

    The negation of :func:`legacy_unmapped_where_clause`. Compose with
    the active/historical status filters so a row that is technically
    in a ``dismissed`` state but has ``application_id IS NULL`` is
    excluded from the ``historical`` bucket (because it's quarantined),
    and a vocabulary-ghost ``escalated`` row never accidentally appears
    as active.
    """
    placeholders = ",".join(["?"] * len(CANONICAL_ALERT_VOCABULARY))
    fragment = (
        "NOT ("
        f"(status NOT IN ({placeholders}) "
        "  AND NOT (LOWER(COALESCE(status, '')) = 'in_review' "
        "           AND UPPER(COALESCE(source_reference, '')) LIKE 'FIX_SCEN%') "
        "  AND linked_periodic_review_id IS NULL "
        "  AND linked_edd_case_id IS NULL)"
        " OR "
        "(application_id IS NULL)"
        ")"
    )
    params: List[Any] = list(CANONICAL_ALERT_VOCABULARY)
    return fragment, params


__all__ = [
    "CANONICAL_ALERT_VOCABULARY",
    "QUARANTINE_REASON_VOCABULARY_GHOST",
    "QUARANTINE_REASON_UNSCOPABLE",
    "QUARANTINE_REASON_ORDER",
    "is_legacy_unmapped",
    "legacy_unmapped_where_clause",
    "active_or_historical_exclude_legacy_clause",
]
