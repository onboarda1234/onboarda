"""
ARIE Finance — GDPR Data Retention & Purge Engine
Sprint 3: Implements data lifecycle management per Mauritius DPA 2017 + GDPR.

Provides:
    - Retention policy enforcement (identify expired data)
    - Safe purge with audit logging (immutable purge log)
    - Data Subject Access Request (DSAR) helpers
    - Scheduled purge function for cron/periodic invocation
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("arie")


# ══════════════════════════════════════════════════════════
# RETENTION POLICY QUERIES
# ══════════════════════════════════════════════════════════

# Maps data_category -> (table_name, date_column)
CATEGORY_TABLE_MAP = {
    "audit_logs": ("audit_log", "timestamp"),
    "session_tokens": ("audit_log", "timestamp"),  # Token-related audit entries
    "monitoring_alerts": ("monitoring_alerts", "created_at"),
}

# Explicit identifier allowlists derived from CATEGORY_TABLE_MAP.
# These are used by _assert_safe_sql_identifier() to guard every f-string SQL
# construction in this module.  Even though table/column names are always
# resolved from the hardcoded CATEGORY_TABLE_MAP dict (never from user input),
# an explicit check here protects against future regressions or accidental
# changes to the map that introduce an unexpected identifier.
_ALLOWED_GDPR_TABLES: frozenset = frozenset(v[0] for v in CATEGORY_TABLE_MAP.values())
_ALLOWED_GDPR_DATE_COLS: frozenset = frozenset(v[1] for v in CATEGORY_TABLE_MAP.values())


def _assert_safe_sql_identifier(value: str, allowed: frozenset, context: str) -> None:
    """Raise ValueError if *value* is not in the pre-approved *allowed* set.

    This is a defence-in-depth check: identifiers must already come from
    CATEGORY_TABLE_MAP, but this validates the resolved value explicitly so
    any future code-path change will surface immediately rather than silently
    executing arbitrary SQL.
    """
    if value not in allowed:
        raise ValueError(
            f"SQL identifier safety check failed for {context!r}: "
            f"{value!r} is not in the allowed set {sorted(allowed)!r}"
        )


def get_retention_policies(db) -> List[Dict]:
    """Fetch all active retention policies."""
    rows = db.execute("SELECT * FROM data_retention_policies ORDER BY data_category").fetchall()
    return [dict(r) for r in rows]


def get_expired_data_summary(db) -> List[Dict]:
    """
    Identify data categories with records past their retention period.
    Returns a list of {category, table, expired_count, oldest_record, retention_days}.
    Safe read-only query — does not modify anything.
    """
    policies = get_retention_policies(db)
    expired = []

    for policy in policies:
        category = policy["data_category"]
        retention_days = policy["retention_days"]
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%S")

        mapping = CATEGORY_TABLE_MAP.get(category)
        if not mapping:
            continue  # Skip categories without direct table mapping (handled manually)

        table, date_col = mapping
        _assert_safe_sql_identifier(table, _ALLOWED_GDPR_TABLES, "table")
        _assert_safe_sql_identifier(date_col, _ALLOWED_GDPR_DATE_COLS, "date_col")
        try:
            row = db.execute(
                f"SELECT COUNT(*) as cnt, MIN({date_col}) as oldest FROM {table} WHERE {date_col} < ?",  # noqa: S608
                (cutoff,)
            ).fetchone()
            count = row["cnt"] if row else 0
            oldest = row["oldest"] if row else None
            if count > 0:
                expired.append({
                    "category": category,
                    "table": table,
                    "expired_count": count,
                    "oldest_record": oldest,
                    "retention_days": retention_days,
                    "cutoff_date": cutoff,
                    "auto_purge": bool(policy.get("auto_purge")),
                    "requires_review": bool(policy.get("requires_review")),
                })
        except Exception as e:
            logger.warning("Error checking retention for %s: %s", category, str(e))

    return expired


# ══════════════════════════════════════════════════════════
# PURGE EXECUTION
# ══════════════════════════════════════════════════════════

def purge_expired_data(
    db,
    category: str,
    purged_by: Optional[str] = None,
    dry_run: bool = True,
) -> Dict:
    """
    Purge records past retention for a specific data category.

    Args:
        db: Database connection
        category: Data category (must match data_retention_policies.data_category)
        purged_by: User ID of the person authorizing the purge
        dry_run: If True, only count — do not delete. Default True for safety.

    Returns:
        {category, records_deleted, oldest_record, newest_record, dry_run}
    """
    # Fetch policy
    policy = db.execute(
        "SELECT * FROM data_retention_policies WHERE data_category = ?", (category,)
    ).fetchone()
    if not policy:
        return {"error": f"No retention policy found for category '{category}'"}

    retention_days = policy["retention_days"]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%S")

    mapping = CATEGORY_TABLE_MAP.get(category)
    if not mapping:
        return {"error": f"Category '{category}' has no direct table mapping — requires manual purge"}

    table, date_col = mapping
    _assert_safe_sql_identifier(table, _ALLOWED_GDPR_TABLES, "table")
    _assert_safe_sql_identifier(date_col, _ALLOWED_GDPR_DATE_COLS, "date_col")

    # Count and get date range
    stats = db.execute(
        f"SELECT COUNT(*) as cnt, MIN({date_col}) as oldest, MAX({date_col}) as newest FROM {table} WHERE {date_col} < ?",
        (cutoff,)
    ).fetchone()

    count = stats["cnt"] if stats else 0
    oldest = stats["oldest"] if stats else None
    newest = stats["newest"] if stats else None

    result = {
        "category": category,
        "records_found": count,
        "oldest_record": oldest,
        "newest_record": newest,
        "cutoff_date": cutoff,
        "retention_days": retention_days,
        "dry_run": dry_run,
        "records_deleted": 0,
    }

    if count == 0:
        return result

    if dry_run:
        return result

    # Execute purge
    db.execute(f"DELETE FROM {table} WHERE {date_col} < ?", (cutoff,))

    # Log purge in immutable audit trail
    db.execute(
        "INSERT INTO data_purge_log (data_category, record_count, oldest_record_date, newest_record_date, retention_policy_id, purge_reason, purged_by) VALUES (?,?,?,?,?,?,?)",
        (category, count, oldest, newest, policy["id"],
         f"Retention policy enforcement: {retention_days} days exceeded (cutoff: {cutoff})",
         purged_by or "system")
    )
    db.commit()

    result["records_deleted"] = count
    logger.info("GDPR purge: %d records deleted from %s (category: %s, cutoff: %s)",
                count, table, category, cutoff)
    return result


def run_scheduled_purge(db, purged_by: str = "system") -> List[Dict]:
    """
    Run purge for all auto_purge=True policies.
    Called by cron or periodic task scheduler.
    Returns list of purge results.
    """
    policies = db.execute(
        "SELECT * FROM data_retention_policies WHERE auto_purge = 1 OR auto_purge = true"
    ).fetchall()

    results = []
    for policy in policies:
        result = purge_expired_data(
            db, policy["data_category"], purged_by=purged_by, dry_run=False
        )
        results.append(result)

    return results


# ══════════════════════════════════════════════════════════
# DATA SUBJECT ACCESS REQUESTS (DSAR)
# ══════════════════════════════════════════════════════════

def create_dsar(
    db,
    request_type: str,
    requester_email: str,
    requester_name: Optional[str] = None,
    client_id: Optional[str] = None,
    description: Optional[str] = None,
) -> Dict:
    """
    Create a new Data Subject Access Request.
    GDPR Article 15-22 compliance: must respond within 30 days.
    """
    valid_types = ("access", "rectification", "erasure", "portability", "restriction", "objection")
    if request_type not in valid_types:
        return {"error": f"Invalid request type. Must be one of: {valid_types}"}

    due_at = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")

    db.execute(
        "INSERT INTO data_subject_requests (request_type, requester_email, requester_name, client_id, description, due_at) VALUES (?,?,?,?,?,?)",
        (request_type, requester_email, requester_name, client_id, description, due_at)
    )
    db.commit()

    # Get the inserted row
    row = db.execute(
        "SELECT * FROM data_subject_requests WHERE requester_email = ? ORDER BY created_at DESC LIMIT 1",
        (requester_email,)
    ).fetchone()

    logger.info("DSAR created: type=%s, email=%s, due=%s", request_type, requester_email, due_at)
    return dict(row) if row else {"status": "created", "due_at": due_at}


def get_pending_dsars(db) -> List[Dict]:
    """Get all pending/in-progress DSARs ordered by due date."""
    rows = db.execute(
        "SELECT * FROM data_subject_requests WHERE status IN ('pending', 'in_progress') ORDER BY due_at ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def complete_dsar(
    db,
    dsar_id: int,
    handled_by: str,
    response_notes: str,
    new_status: str = "completed",
) -> Dict:
    """Complete a DSAR with response notes."""
    if new_status not in ("completed", "rejected"):
        return {"error": "Status must be 'completed' or 'rejected'"}

    db.execute(
        "UPDATE data_subject_requests SET status = ?, handled_by = ?, response_notes = ?, completed_at = ? WHERE id = ?",
        (new_status, handled_by, response_notes, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), dsar_id)
    )
    db.commit()

    logger.info("DSAR %d marked as %s by %s", dsar_id, new_status, handled_by)
    return {"id": dsar_id, "status": new_status, "handled_by": handled_by}
