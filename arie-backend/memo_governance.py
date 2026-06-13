"""Canonical compliance memo governance helpers.

This module deliberately has no dependency on ``server.py`` so memo consumers
can share the same selector without creating circular imports.
"""

from __future__ import annotations

from typing import Any


MEMO_SELECTOR_VERSION = "pr5_canonical_v1"
CANONICAL_MEMO_ORDER_SQL = "ORDER BY COALESCE(version, 0) DESC, created_at DESC, id DESC"
CANONICAL_MEMO_SELECTION_ORDER = (
    CANONICAL_MEMO_ORDER_SQL
    .removeprefix("ORDER BY ")
    .replace("COALESCE(version, 0)", "version")
)
_COMPLIANCE_MEMO_COLUMNS = {
    "id",
    "application_id",
    "version",
    "memo_data",
    "generated_by",
    "ai_recommendation",
    "review_status",
    "reviewed_by",
    "review_notes",
    "quality_score",
    "validation_status",
    "validation_issues",
    "validation_run_at",
    "memo_version",
    "raw_output_hash",
    "approved_by",
    "approved_at",
    "approval_reason",
    "supervisor_status",
    "supervisor_summary",
    "supervisor_contradictions",
    "rule_violations",
    "rule_engine_status",
    "blocked",
    "block_reason",
    "is_stale",
    "stale_reason",
    "stale_reasons",
    "stale_trigger",
    "stale_marked_at",
    "pdf_generated_at",
    "created_at",
}


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return row


def _validate_columns(columns: str) -> str:
    if columns == "*":
        return columns
    column_names = [column.strip() for column in columns.split(",")]
    if not column_names or any(not column for column in column_names):
        raise ValueError("Invalid compliance memo column selector")
    unsafe = [column for column in column_names if column not in _COMPLIANCE_MEMO_COLUMNS]
    if unsafe:
        raise ValueError(f"Invalid compliance memo column selector: {', '.join(unsafe)}")
    return ", ".join(column_names)


def latest_compliance_memo_row(db, application_id: Any, columns: str = "*"):
    """Return the authoritative latest onboarding compliance memo row.

    Canonical ordering is:

    1. highest explicit memo ``version``
    2. newest ``created_at`` within the same version
    3. highest ``id`` as a deterministic final tie-breaker
    """

    if application_id is None:
        return None
    columns = _validate_columns(columns)
    return db.execute(
        f"""
        SELECT {columns}
          FROM compliance_memos
         WHERE application_id = ?
        {CANONICAL_MEMO_ORDER_SQL}
         LIMIT 1
        """,
        (application_id,),
    ).fetchone()


def latest_compliance_memo_row_for_identifier(db, application_id_or_ref: Any, columns: str = "*"):
    """Return the canonical memo row for an application id or reference."""

    if application_id_or_ref is None:
        return None
    columns = _validate_columns(columns)
    return db.execute(
        f"""
        SELECT {columns}
          FROM compliance_memos
         WHERE application_id = ?
            OR application_id = (SELECT id FROM applications WHERE ref = ?)
        {CANONICAL_MEMO_ORDER_SQL}
         LIMIT 1
        """,
        (application_id_or_ref, application_id_or_ref),
    ).fetchone()


def memo_selection_metadata(memo_row: Any) -> dict[str, Any]:
    """Return trace metadata for the canonical selector."""

    row = _row_to_dict(memo_row) or {}
    memo_id = row.get("id")
    return {
        "memo_id": memo_id,
        "canonical_memo_id": memo_id,
        "selected_memo_id": memo_id,
        "is_current": bool(memo_id),
        "is_historical": False,
        "selector": MEMO_SELECTOR_VERSION,
        "selection_order": CANONICAL_MEMO_SELECTION_ORDER,
    }
