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
* Existing lifecycle, monitoring, and EDD runtime contracts are not
  modified by this module being added.
* No invented linkage. We do not "fix" ghost rows by promoting them; we
  surface them as a third bucket alongside active and historical.

The two predicates
------------------
1. **vocabulary_ghost** -- the row's status is outside the canonical
   Monitoring Alert state-machine vocabulary. A downstream link cannot rescue
   an invalid status; invalid persisted state remains quarantined fail-closed.

2. **unscopable_no_application** -- the row has ``application_id IS
   NULL``. Such a row cannot be scoped to any application's lifecycle
   regardless of its state, and therefore cannot meaningfully appear
   in an application-scoped queue or summary.

A row matching ANY predicate is classified ``legacy_unmapped``. Both
predicates are intentionally orthogonal -- a row may match one or both,
and the materialised quarantine_reasons list reports which.

Public surface
--------------
* :data:`CANONICAL_ALERT_VOCABULARY` -- mirrors the canonical state machine.
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

from monitoring_alert_state_machine import CANONICAL_STATUSES

# Compatibility export used by the lifecycle queue and existing tests.
CANONICAL_ALERT_VOCABULARY: Tuple[str, ...] = CANONICAL_STATUSES

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


def is_legacy_unmapped(row) -> Tuple[bool, List[str]]:
    """Classify a single ``monitoring_alerts`` row.

    Returns ``(True, [reasons])`` if the row matches one or both
    quarantine predicates, ``(False, [])`` otherwise. The reasons list
    is in :data:`QUARANTINE_REASON_ORDER` order.
    """
    reasons: List[str] = []

    # Vocabulary-ghost: every missing/unknown status fails closed, regardless
    # of linkage. Link existence is not evidence that a stored status is valid.
    status = _row_get(row, "status", "")
    if status not in CANONICAL_ALERT_VOCABULARY:
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
        # vocabulary_ghost: status outside canonical, irrespective of linkage.
        f"(COALESCE(status, '') NOT IN ({placeholders}))"
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
    and a vocabulary-ghost row never accidentally appears as active.
    """
    placeholders = ",".join(["?"] * len(CANONICAL_ALERT_VOCABULARY))
    fragment = (
        "NOT ("
        f"(COALESCE(status, '') NOT IN ({placeholders}))"
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
