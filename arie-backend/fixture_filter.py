"""
Fixture Hygiene Filter — Priority D
=====================================

SQL WHERE-fragment helpers that exclude seeder/demo/test applications from
all normal officer-facing and externally-visible surfaces.

Identification rule
-------------------
Two ID namespaces are reserved for fixture/demo data and are collision-free
with the normal ``lower(hex(randomblob(8)))`` /
``encode(gen_random_bytes(8),'hex')`` generators (which always produce
16-char all-hex lowercase strings with no hyphens):

  * ``f1xed...``        — fixtures/seeder.py (SCEN-01 through SCEN-11)
  * ``demo-scenario-...`` — demo_pilot_data.py (DEMO01-DEMO05)

Both patterns are detectable via LIKE without schema changes and cannot
collide with real application IDs.

Usage
-----
All SQL fragments use ``?`` placeholders and are safe for both SQLite
(testing) and PostgreSQL (via the DBConnection abstraction).

Import the pre-built constants for common call sites::

    from fixture_filter import (
        EXCLUDE_FIXTURE_APPS_SQL,      # unaliased (FROM applications)
        EXCLUDE_FIXTURE_APPS_SQL_A,    # aliased   (FROM applications a)
        EXCLUDE_FIXTURE_LIFECYCLE_SQL, # lifecycle tables, unaliased
        FIXTURE_APP_FILTER_PARAMS,     # ["f1xed%", "demo-scenario-%"]
    )

    # Officer-side dashboard count:
    db.execute(
        f"SELECT COUNT(*) as c FROM applications WHERE {EXCLUDE_FIXTURE_APPS_SQL}",
        FIXTURE_APP_FILTER_PARAMS,
    )

    # Add to a dynamic conditions / params pair:
    conditions.append(EXCLUDE_FIXTURE_APPS_SQL_A)
    params.extend(FIXTURE_APP_FILTER_PARAMS)

For non-standard alias cases use the helper functions::

    from fixture_filter import exclude_fixture_applications_fragment
    frag, fparams = exclude_fixture_applications_fragment("b")   # alias b
    query += f" AND {frag}"
    params.extend(fparams)
"""

from __future__ import annotations

from typing import List, Tuple

# ---------------------------------------------------------------------------
# Fixture application ID patterns
# ---------------------------------------------------------------------------
# * 'f1xed%'           — fixtures/seeder.py reserved hex namespace
# * 'demo-scenario-%'  — demo_pilot_data.py scenario IDs
FIXTURE_APP_ID_PATTERNS: Tuple[str, ...] = ("f1xed%", "demo-scenario-%")

# Shared param list for all fragment constants below.
# Always a 2-element list matching FIXTURE_APP_ID_PATTERNS.
FIXTURE_APP_FILTER_PARAMS: List[str] = list(FIXTURE_APP_ID_PATTERNS)

# ---------------------------------------------------------------------------
# Pre-built SQL fragments (use with FIXTURE_APP_FILTER_PARAMS)
# ---------------------------------------------------------------------------

# Unaliased: for queries that reference applications without a table alias,
# e.g. SELECT COUNT(*) FROM applications WHERE ...
EXCLUDE_FIXTURE_APPS_SQL: str = "NOT (id LIKE ? OR id LIKE ?)"

# Aliased with 'a': for queries that use FROM applications a (most list queries)
EXCLUDE_FIXTURE_APPS_SQL_A: str = "NOT (a.id LIKE ? OR a.id LIKE ?)"

# Lifecycle tables (monitoring_alerts, periodic_reviews, edd_cases), unaliased.
# Rows where application_id IS NULL are kept (they are handled separately by
# the quarantine logic in lifecycle_quarantine.py).
EXCLUDE_FIXTURE_LIFECYCLE_SQL: str = (
    "NOT (application_id IS NOT NULL AND (application_id LIKE ? OR application_id LIKE ?))"
)


# ---------------------------------------------------------------------------
# Helper functions for non-standard alias cases
# ---------------------------------------------------------------------------

def exclude_fixture_applications_fragment(alias: str = "a") -> Tuple[str, List[str]]:
    """Return ``(where_fragment, params)`` excluding fixture/demo applications.

    Args:
        alias: table alias (e.g. ``"a"`` for ``FROM applications a``).
               Pass ``""`` or ``None`` for unaliased queries.

    Returns:
        Tuple of (SQL fragment string, params list).  The fragment uses ``?``
        placeholders and is safe for both SQLite and PostgreSQL.
    """
    col = f"{alias}.id" if alias else "id"
    clauses = " OR ".join(f"{col} LIKE ?" for _ in FIXTURE_APP_ID_PATTERNS)
    return f"NOT ({clauses})", list(FIXTURE_APP_ID_PATTERNS)


def exclude_fixture_lifecycle_fragment(alias: str = "") -> Tuple[str, List[str]]:
    """Return ``(where_fragment, params)`` excluding fixture lifecycle rows.

    Applicable to ``monitoring_alerts``, ``periodic_reviews``, and
    ``edd_cases`` that are linked to fixture applications via
    ``application_id``.

    Rows where ``application_id IS NULL`` are kept (handled by the
    quarantine logic in lifecycle_quarantine.py).

    Args:
        alias: table alias (e.g. ``"m"`` for ``FROM monitoring_alerts m``).
               Pass ``""`` or ``None`` for unaliased queries.

    Returns:
        Tuple of (SQL fragment string, params list).
    """
    col = f"{alias}.application_id" if alias else "application_id"
    clauses = " OR ".join(f"{col} LIKE ?" for _ in FIXTURE_APP_ID_PATTERNS)
    return (
        f"NOT ({col} IS NOT NULL AND ({clauses}))",
        list(FIXTURE_APP_ID_PATTERNS),
    )


__all__ = [
    "FIXTURE_APP_ID_PATTERNS",
    "FIXTURE_APP_FILTER_PARAMS",
    "EXCLUDE_FIXTURE_APPS_SQL",
    "EXCLUDE_FIXTURE_APPS_SQL_A",
    "EXCLUDE_FIXTURE_LIFECYCLE_SQL",
    "exclude_fixture_applications_fragment",
    "exclude_fixture_lifecycle_fragment",
]
