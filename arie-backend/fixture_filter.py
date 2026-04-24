"""
fixture_filter.py — Canonical fixture exclusion helpers.
=========================================================

Fixture rows are identified by the compound rule::

    applications.id LIKE 'f1xed%'  OR  applications.is_fixture

**Signal 1 — ID namespace** (``id LIKE 'f1xed%'``):
All current staging scenarios use IDs in the reserved ``f1xed...``
16-char hex namespace (e.g. ``f1xed00000000001`` through
``f1xed00000000011``).  Real application IDs are
``uuid.uuid4().hex[:16]`` and will never start with ``f1xed``.

**Signal 2 — explicit column** (``is_fixture``):
``applications.is_fixture`` is a boolean/integer column (``FALSE``/
``0`` by default) set to ``TRUE``/``1`` for any fixture or historical
test row.  This column was introduced to handle 8 historical test rows
that pre-date the ``f1xed`` namespace (ARF-2026-100421, 100424, 100427,
100428, 100430, 100454, 100455, 100456) whose IDs are normal UUID hex
values and would bypass the ID-pattern check alone.

Both signals are checked; a row is a fixture if *either* condition is
true.  New seeded rows must set ``is_fixture = 1`` (the seeder does
this automatically).  Future rows with IDs outside the ``f1xed``
namespace are caught by ``is_fixture``.

This is the ONLY authoritative fixture identification module for the
back-office query layer.  All query layers must use these helpers rather
than duplicating the rule.

Design contract
---------------
* No DB connection dependency — pure SQL fragment generators.
* All fragments use parameterised ``?`` placeholders so they are
  portable to both SQLite (``?``) and PostgreSQL (translated to ``%s``
  by ``db.DBConnection._translate_query``).
* ``fixture_app_exclude_clause`` is for the ``applications`` table.
* ``fixture_app_id_exclude_clause`` is for related tables
  (``monitoring_alerts``, ``periodic_reviews``, ``edd_cases``) that
  store the application's primary key in an ``application_id`` FK column.
  It includes a NULL-safe guard for tables where ``application_id`` is
  nullable (e.g. ``monitoring_alerts``).
* ``should_show_fixtures`` encodes the access policy: ``show_fixtures=true``
  query param, honoured ONLY for ``admin`` or ``sco`` users; silently
  ignored for all others.

Public surface
--------------
* :data:`FIXTURE_APP_ID_PATTERN`
* :func:`fixture_app_exclude_clause`
* :func:`fixture_app_id_exclude_clause`
* :func:`should_show_fixtures`
"""

from __future__ import annotations

from typing import List, Optional, Tuple

# The reserved application-id prefix.  All fixture scenario rows share
# IDs that start with this literal string.  Real UUIDs (hex-only) cannot
# collide with it because ``f1xed`` contains the digit ``1`` as its second
# character (index 1), making it visually distinct from a random UUID hex,
# and because the seeder documentation explicitly marks this as a
# *reserved namespace*.
FIXTURE_APP_ID_PATTERN: str = "f1xed%"

# Stable ``ref`` values of the 8 historical test rows that pre-date the
# ``f1xed`` namespace.  These rows are marked ``is_fixture = 1`` by
# migration v2.29.  This tuple is kept here for documentation and test
# coverage; the authoritative marking lives in the DB column.
ROGUE_FIXTURE_REFS: tuple = (
    "ARF-2026-100454",  # EX06 DualApproval Test Corp
    "ARF-2026-100456",  # EX06 Validation TestCo Ltd
    "ARF-2026-100455",  # HighRisk Dual Approval Test Ltd
    "ARF-2026-100421",  # Pipeline Test Corp Ltd
    "ARF-2026-100424",  # Portal Audit Test Ltd
    "ARF-2026-100430",  # Probe Test Co
    "ARF-2026-100428",  # test 2
    "ARF-2026-100427",  # test [QA-R10-mnyuuv7q]
)


def fixture_app_exclude_clause(table_alias: str = "a") -> Tuple[str, List[str]]:
    """Return ``(sql_fragment, params)`` that excludes fixture applications.

    For use in queries on the ``applications`` table where the table is
    aliased (default alias ``"a"``).  Pass an empty string to omit the
    alias (bare column reference).

    The fragment uses the compound fixture rule:
    ``NOT (id LIKE ? OR is_fixture)``

    * ``id LIKE 'f1xed%'`` — catches all seeded fixture rows.
    * ``is_fixture`` — catches historical rogue rows marked by migration
      v2.29.  In PostgreSQL this is a BOOLEAN column; in SQLite it is
      INTEGER 0/1; both dialects treat the bare column in a WHERE clause
      as a truthy check.  NULL values evaluate as falsy (not a fixture).

    Example::

        excl, excl_params = fixture_app_exclude_clause()
        query += f" AND {excl}"
        params.extend(excl_params)
    """
    id_col = f"{table_alias}.id" if table_alias else "id"
    fix_col = f"{table_alias}.is_fixture" if table_alias else "is_fixture"
    return (
        f"{id_col} NOT LIKE ? AND ({fix_col} IS NULL OR NOT {fix_col})",
        [FIXTURE_APP_ID_PATTERN],
    )


def fixture_app_id_exclude_clause(
    col_name: str = "application_id",
) -> Tuple[str, List[str]]:
    """Return ``(sql_fragment, params)`` that excludes fixture-linked rows.

    For use in queries on related tables (``monitoring_alerts``,
    ``periodic_reviews``, ``edd_cases``) where the fixture app ID is
    stored in a FK/reference column.

    The fragment uses the compound fixture rule via a correlated subquery:

    .. code-block:: sql

        (application_id IS NULL
         OR (application_id NOT LIKE ?
             AND application_id NOT IN
               (SELECT id FROM applications WHERE is_fixture)))

    * The ``NOT LIKE`` arm catches ``f1xed%`` IDs without a join.
    * The ``NOT IN`` subquery catches rows whose linked application has
      ``is_fixture = 1`` (the 8 rogue historical test rows).
    * The ``IS NULL`` guard preserves manually created alerts/reviews
      with no application link.

    Example::

        excl, excl_params = fixture_app_id_exclude_clause()
        query += f" AND {excl}"
        params.extend(excl_params)
    """
    return (
        f"({col_name} IS NULL OR "
        f"({col_name} NOT LIKE ? AND "
        f"{col_name} NOT IN (SELECT id FROM applications WHERE is_fixture)))",
        [FIXTURE_APP_ID_PATTERN],
    )


def should_show_fixtures(
    user: Optional[dict],
    query_param_value: Optional[str],
) -> bool:
    """Return True only when an admin/sco user explicitly opts in.

    Policy:
    * The ``show_fixtures=true`` query parameter must be present.
    * The authenticated user must have role ``admin`` or ``sco``.
    * Any other combination silently returns False (fixtures excluded).
    * ``user=None`` is treated as non-admin (returns False).

    Args:
        user:               The decoded JWT payload dict from
                            ``BaseHandler.require_auth()``.
        query_param_value:  The raw query-string value for
                            ``show_fixtures`` (None if absent).

    Returns:
        bool — True iff fixtures should be visible in the response.
    """
    if not user:
        return False
    if str(query_param_value or "").lower() != "true":
        return False
    role = user.get("role") or user.get("type") or ""
    return role in ("admin", "sco")
