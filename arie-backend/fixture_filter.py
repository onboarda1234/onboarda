"""
fixture_filter.py — Canonical fixture exclusion helpers.
=========================================================

Fixture rows are identified by ``applications.id LIKE 'f1xed%'``.

All 11 staging scenarios use IDs in the reserved ``f1xed...`` 16-char hex
namespace (e.g. ``f1xed00000000001`` through ``f1xed00000000011``, per
``fixtures/registry.py`` APP_ID map).  Real application IDs are
``uuid.uuid4().hex[:16]`` and will never start with ``f1xed``.

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


def fixture_app_exclude_clause(table_alias: str = "a") -> Tuple[str, List[str]]:
    """Return ``(sql_fragment, params)`` that excludes fixture applications.

    For use in queries on the ``applications`` table where the table is
    aliased (default alias ``"a"``).  Pass an empty string to omit the
    alias (bare column reference).

    Example::

        excl, excl_params = fixture_app_exclude_clause()
        query += f" AND {excl}"
        params.extend(excl_params)
    """
    col = f"{table_alias}.id" if table_alias else "id"
    return f"{col} NOT LIKE ?", [FIXTURE_APP_ID_PATTERN]


def fixture_app_id_exclude_clause(
    col_name: str = "application_id",
) -> Tuple[str, List[str]]:
    """Return ``(sql_fragment, params)`` that excludes fixture-linked rows.

    For use in queries on related tables (``monitoring_alerts``,
    ``periodic_reviews``, ``edd_cases``) where the fixture app ID is
    stored in a FK/reference column.

    Includes a NULL-safe guard so rows where *application_id IS NULL*
    (e.g. manually created monitoring alerts without an application
    association) are NOT accidentally excluded.

    Example::

        excl, excl_params = fixture_app_id_exclude_clause()
        query += f" AND {excl}"
        params.extend(excl_params)
    """
    return (
        f"({col_name} IS NULL OR {col_name} NOT LIKE ?)",
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
