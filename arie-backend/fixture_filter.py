"""fixture_filter.py — Canonical fixture/demo/test containment.

Priority D patch: exclude fixture, demo, and test records from default
operational surfaces (dashboard, applications, EDD pipeline, lifecycle
queue, monitoring, analytics).

Fixture identification strategy
--------------------------------
No schema-level ``is_fixture`` flag exists.  Identification is via
reserved-namespace application IDs and refs designed to be visually
obvious in logs and to never collide with live records:

  * Fixture scenarios (SCEN-01..11):
      - application.id   : starts with ``f1xed`` (e.g. ``f1xed00000000001``)
      - application.ref  : starts with ``ARF-2026-9`` (e.g. ``ARF-2026-900001``)
      - company_name     : starts with ``FIX-SCEN``
      - alert sentinel   : source_reference LIKE ``FIX_SCEN%_ALERT``
      - review sentinel  : trigger_reason   LIKE ``FIX_SCEN%_REVIEW%``
      - edd sentinel     : trigger_notes    LIKE ``FIX_SCEN%_EDD%``

  * Demo scenarios (DEMO01..05):
      - application.id  : starts with ``demo-``  (e.g. ``demo-scenario-01``)
      - application.ref : starts with ``ARF-2026-DEMO``

This module exposes SQL fragment builders that return **parameterised**
LIKE conditions.  Using ``?`` placeholders (translated to ``%s`` for
PostgreSQL by ``db._translate_query``) keeps the ``%`` wildcard in the
parameter value rather than the SQL text, which is safe under both the
SQLite and psycopg2 pyformat adapters.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

# ── Canonical fixture ID patterns ─────────────────────────────────────────
# Passed as SQL parameters (LIKE ?) rather than embedded in SQL text so
# that the % wildcard character is never present as a literal in the SQL
# string, avoiding psycopg2 pyformat-collision bugs.

#: Application IDs that identify fixture/demo rows.
_FIXTURE_ID_PATTERNS: Tuple[str, ...] = ("f1xed%", "demo-%")

#: Application refs that identify fixture/demo rows.
_FIXTURE_REF_PATTERNS: Tuple[str, ...] = ("ARF-2026-9%", "ARF-2026-DEMO%")


def build_exclude_apps_sql(alias: str = "a") -> Tuple[str, List[Any]]:
    """Return ``(sql_fragment, params)`` to append to a WHERE clause.

    The fragment excludes fixture/demo application rows from a query
    that references the applications table with the given *alias*.
    Pass ``alias=""`` for queries where the table is unaliased.

    Example::

        frag, fparams = build_exclude_apps_sql("a")
        query += " AND " + frag
        params += fparams
    """
    dot = f"{alias}." if alias else ""
    parts: List[str] = []
    params: List[Any] = []
    for pat in _FIXTURE_ID_PATTERNS:
        parts.append(f"{dot}id NOT LIKE ?")
        params.append(pat)
    for pat in _FIXTURE_REF_PATTERNS:
        parts.append(f"{dot}ref NOT LIKE ?")
        params.append(pat)
    return " AND ".join(parts), params


def build_exclude_lifecycle_sql(
    *,
    alert_sentinel: bool = False,
    review_sentinel: bool = False,
    edd_sentinel: bool = False,
) -> Tuple[str, List[Any]]:
    """Return ``(sql_fragment, params)`` for lifecycle table exclusion.

    Works for ``monitoring_alerts``, ``periodic_reviews``, and
    ``edd_cases`` — all of which carry an ``application_id`` FK that
    matches the fixture application ID patterns.  NULLs are preserved:
    rows with ``application_id IS NULL`` are never fixture rows and must
    not be excluded.

    Optional sentinel flags add belt-and-suspenders conditions on the
    type-specific marker columns:

    * ``alert_sentinel``  — also exclude ``source_reference LIKE 'FIX_SCEN%_ALERT'``
    * ``review_sentinel`` — also exclude ``trigger_reason LIKE 'FIX_SCEN%_REVIEW%'``
    * ``edd_sentinel``    — also exclude ``trigger_notes LIKE 'FIX_SCEN%_EDD%'``
    """
    parts: List[str] = []
    params: List[Any] = []
    # Null-safe: rows with no application_id (manual/standalone records)
    # must never be excluded.
    id_parts: List[str] = []
    for pat in _FIXTURE_ID_PATTERNS:
        id_parts.append("application_id NOT LIKE ?")
        params.append(pat)
    # Wrap the whole id group in IS NULL OR (...) guard
    parts.append("(application_id IS NULL OR (" + " AND ".join(id_parts) + "))")

    if alert_sentinel:
        parts.append("(source_reference IS NULL OR source_reference NOT LIKE ?)")
        params.append("FIX_SCEN%_ALERT")
    if review_sentinel:
        parts.append("(trigger_reason IS NULL OR trigger_reason NOT LIKE ?)")
        params.append("FIX_SCEN%_REVIEW%")
    if edd_sentinel:
        parts.append("(trigger_notes IS NULL OR trigger_notes NOT LIKE ?)")
        params.append("FIX_SCEN%_EDD%")

    return " AND ".join(parts), params


def is_fixture_app(app_id: str, app_ref: str = "") -> bool:
    """Return ``True`` if the application row is a fixture/demo/test record."""
    if not app_id:
        return False
    aid = str(app_id).lower()
    if aid.startswith("f1xed") or aid.startswith("demo-"):
        return True
    if app_ref:
        ref = str(app_ref).upper()
        if ref.startswith("ARF-2026-9") or ref.startswith("ARF-2026-DEMO"):
            return True
    return False


def mark_fixture(record: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *record* with ``is_fixture=True`` if it is a fixture row.

    Checks both ``id``/``application_id`` and ``ref`` fields so the same
    helper works for application rows, lifecycle rows, and alert rows.
    """
    app_id = str(record.get("id") or record.get("application_id") or "")
    app_ref = str(record.get("ref") or "")
    if is_fixture_app(app_id, app_ref):
        record = dict(record)
        record["is_fixture"] = True
    return record
