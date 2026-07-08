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
from uuid import uuid4

logger = logging.getLogger("arie")

DSAR_ERASURE_OUTCOME_RESPONSE_COMPLETED_NO_EXECUTION = "response_completed_no_erasure_executed"
DSAR_ERASURE_OUTCOME_RETAINED_LEGAL = "retained_under_legal_obligation"
DSAR_ERASURE_OUTCOME_PARTIAL = "partially_erased"


def _truthy_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _parse_retained_categories(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return value


def _completed_erasure_marker_exists(db, *, client_id: Optional[str], dsar_request_id: Any = None,
                                     require_correlation: bool = False) -> bool:
    """Return whether a qualifying completed, non-dry-run erasure marker exists.

    When require_correlation=True, the marker must match dsar_request_id as well
    as client_id. Read formatting for legacy DSAR rows has no persisted
    correlation id, so it can fall back to a client-bound marker; the sanctioned
    write path (mark_dsar_erasure_executed) remains correlation-bound.
    """
    if client_id in (None, ""):
        return False
    params = [str(client_id)]
    where = [
        "client_id = ?",
        "COALESCE(dry_run, FALSE) = FALSE",
        "action = 'erasure_completed'",
        "outcome = 'completed'",
    ]
    if dsar_request_id not in (None, ""):
        where.append("dsar_request_id = ?")
        params.append(str(dsar_request_id))
    elif require_correlation:
        return False
    try:
        row = db.execute(
            "SELECT COUNT(*) AS c FROM gdpr_erasure_log WHERE " + " AND ".join(where),
            tuple(params),
        ).fetchone()
    except Exception:
        # No log table / query error ⇒ no evidence ⇒ not executed (fail-safe).
        return False
    count = int(row["c"] if not isinstance(row, tuple) else row[0]) if row else 0
    return count > 0


def _dsar_erasure_evidence_verified(db, payload: Dict) -> bool:
    if db is None:
        return False
    client_id = payload.get("client_id")
    dsar_request_id = (
        payload.get("dsar_request_id")
        or payload.get("erasure_request_id")
        or payload.get("erasure_correlation_id")
    )
    if dsar_request_id not in (None, ""):
        return _completed_erasure_marker_exists(
            db,
            client_id=client_id,
            dsar_request_id=dsar_request_id,
            require_correlation=True,
        )
    return _completed_erasure_marker_exists(
        db,
        client_id=client_id,
        require_correlation=False,
    )


def format_dsar_for_response(row: Optional[Dict], db=None) -> Optional[Dict]:
    """Return a DSAR API payload with legally honest erasure status wording."""
    if row is None:
        return None

    payload = dict(row)
    request_type = str(payload.get("request_type") or "").lower()
    status = str(payload.get("status") or "").lower()
    stored_erasure_executed = _truthy_bool(payload.get("erasure_executed"))
    erasure_evidence_missing = False
    if request_type == "erasure" and stored_erasure_executed:
        if _dsar_erasure_evidence_verified(db, payload):
            erasure_executed = True
        else:
            erasure_executed = False
            erasure_evidence_missing = True
            logger.warning(
                "DSAR %s has erasure_executed=true but no qualifying erasure evidence; "
                "suppressing executed status in response",
                payload.get("id"),
            )
    else:
        erasure_executed = stored_erasure_executed
    retention_outcome = payload.get("retention_outcome")
    if retention_outcome == DSAR_ERASURE_OUTCOME_PARTIAL and not erasure_executed:
        retention_outcome = "partial_retention_outcome_unverified"
        payload["retention_outcome"] = retention_outcome

    payload["erasure_executed"] = erasure_executed
    payload["erasure_evidence_missing"] = erasure_evidence_missing
    payload["retained_categories"] = _parse_retained_categories(payload.get("retained_categories"))

    if request_type == "erasure":
        if erasure_evidence_missing:
            status_label = "Erasure evidence missing"
            status_detail = (
                "A stored erasure flag is not backed by qualifying execution "
                "evidence. Treating erasure as not executed."
            )
        elif retention_outcome == DSAR_ERASURE_OUTCOME_PARTIAL and erasure_executed:
            status_label = "Partial erasure recorded; regulated data retained"
            status_detail = "The erasure executor recorded a partial outcome. Regulated categories remain retained as recorded."
        elif erasure_executed:
            status_label = "Erasure executed"
            status_detail = "The erasure executor has recorded an erasure action for this request."
        elif retention_outcome == DSAR_ERASURE_OUTCOME_RETAINED_LEGAL:
            status_label = "Request response completed; data retained under legal obligation"
            status_detail = "The response workflow is complete. Records remain retained under AML/legal retention."
        elif status == "completed":
            status_label = "Request response completed; erasure not executed"
            status_detail = "The response workflow is complete. No erasure executor ran in this workflow."
        elif status == "rejected":
            status_label = "Request rejected"
            status_detail = "The erasure request was rejected; no erasure executor ran in this workflow."
        else:
            status_label = "Request pending review"
            status_detail = "The erasure request is still in the response workflow. No erasure executor has run."
    elif status == "completed":
        status_label = "Request response completed"
        status_detail = "The response workflow is complete."
    elif status == "rejected":
        status_label = "Request rejected"
        status_detail = "The request was rejected."
    else:
        status_label = "Request pending review"
        status_detail = "The request is still in the response workflow."

    payload["status_label"] = status_label
    payload["status_detail"] = status_detail
    return payload


# ══════════════════════════════════════════════════════════
# RETENTION POLICY QUERIES
# ══════════════════════════════════════════════════════════

# Maps data_category -> (table_name, date_column)
#
# NOTE (audit finding B1): the "session_tokens" category previously mapped to
# ("audit_log", "timestamp") with a 1-day retention and auto_purge=1. Because
# the scheduled purge deletes *every* row older than the cutoff (no action/type
# predicate), that policy was silently destroying the entire generic audit trail
# down to the last 24 hours on a daily PeriodicCallback in staging/production.
# The mapping has been removed so token retention can never resolve to the audit
# trail, and the automatic purge additionally refuses the audit tables outright
# (see _NEVER_PURGE_TABLES / _NEVER_AUTO_PURGE_TABLES below).
CATEGORY_TABLE_MAP = {
    "audit_logs": ("audit_log", "timestamp"),
    "monitoring_alerts": ("monitoring_alerts", "created_at"),
}

# ══════════════════════════════════════════════════════════
# P12-8 / DCI-020: categories WITHOUT a direct table mapping are explicitly
# MANUAL-WITH-PROCEDURE — not silently unenforceable.  Each entry documents
# WHY an age-based automatic sweep cannot safely enforce the policy; the
# operational procedure (approval + evidence requirements + the
# record_manual_purge() evidence writer) lives in MANUAL_PURGE_PROCEDURE_REF.
# Deliberate posture: bulk deletion of client PII / KYC evidence is a
# subject- and relationship-anchored legal decision (see the H2B erasure
# engine, wired-but-OFF pending the PC-4 control pack), never an unattended
# age sweep.
# ══════════════════════════════════════════════════════════
MANUAL_PURGE_PROCEDURE_REF = "docs/compliance/MANUAL_PURGE_PROCEDURE.md"

MANUAL_PURGE_CATEGORIES = {
    "client_pii": (
        "Client PII spans clients/applications/directors/ubos rows plus "
        "uploaded files and S3 objects; retention anchors to relationship "
        "END (not row age), which no age-based sweep can compute. "
        "Subject-scoped deletion belongs to the erasure engine (H2B/PC-4)."
    ),
    "kyc_documents": (
        "Document rows reference physical files/S3 objects and anchor to "
        "relationship end; deleting DB rows without the artefacts (or vice "
        "versa) would corrupt evidence. Requires per-case legal-hold review."
    ),
    "screening_results": (
        "Screening results are decision evidence for onboarding outcomes; "
        "purge requires per-application legal-hold and SAR-linkage review."
    ),
    "compliance_memos": (
        "Memos are decision evidence anchored to decision date; approval "
        "chains reference them. Requires per-case review."
    ),
    "application_data": (
        "Application retention anchors to decision date and cascades to "
        "child tables (directors, ubos, documents, screenings); requires "
        "case-by-case verification and legal-hold review."
    ),
    "sar_reports": (
        "Suspicious Activity Reports: 10-year FIU obligation; deletion "
        "requires FIU-coordination sign-off. Never automatic."
    ),
    "session_tokens": (
        "Documentation-only policy (audit finding B1): deliberately mapped "
        "to no table so token retention can never resolve to the audit "
        "trail. Nothing to purge through this engine."
    ),
}

# Tables that hold AML-retention evidence and must be protected from this engine.
#   * _NEVER_PURGE_TABLES: never deletable by ANY path (manual or scheduled).
#     The supervisor audit log is the tamper-evident decision chain; deleting
#     from it would break the hash chain and destroy tamper-evidence.
#   * _NEVER_AUTO_PURGE_TABLES: never deletable by the AUTOMATIC scheduled purge,
#     regardless of any (mis)configured retention policy. A human may still run a
#     deliberate, audited manual retention purge of the generic audit_log, but no
#     unattended job may ever touch it (defends B1 and the "one flag-flip from
#     destroying AML history" risk).
_NEVER_PURGE_TABLES: frozenset = frozenset({"supervisor_audit_log"})
_NEVER_AUTO_PURGE_TABLES: frozenset = frozenset({"audit_log", "supervisor_audit_log"})

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
            # P12-8 / DCI-020: manual-with-procedure categories are REPORTED,
            # not silently skipped — the ops view must show that these
            # policies exist and are enforced by procedure, not scheduler.
            expired.append({
                "category": category,
                "table": None,
                "expired_count": None,
                "oldest_record": None,
                "retention_days": retention_days,
                "cutoff_date": cutoff,
                "auto_purge": bool(policy.get("auto_purge")),
                "requires_review": bool(policy.get("requires_review")),
                "auto_purge_supported": False,
                "manual_purge_required": True,
                "manual_reason": MANUAL_PURGE_CATEGORIES.get(
                    category,
                    "No direct table mapping; enforce via the manual procedure.",
                ),
                "manual_procedure": MANUAL_PURGE_PROCEDURE_REF,
            })
            continue

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
    purge_batch_id: Optional[str] = None,
) -> Dict:
    """
    Purge records past retention for a specific data category.

    Args:
        db: Database connection
        category: Data category (must match data_retention_policies.data_category)
        purged_by: User ID of the person authorizing the purge
        dry_run: If True, only count — do not delete. Default True for safety.
        purge_batch_id: Optional batch identifier shared across the categories
            of a single scheduled run (P12-8 / DCI-021) so a regulator can
            reconstruct the whole run from data_purge_log alone. Generated
            when omitted.

    Returns:
        {category, records_deleted, oldest_record, newest_record, dry_run}
        — or, for manual-with-procedure categories, a structured
        manual_purge_required result (P12-8 / DCI-020).
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
        manual_reason = MANUAL_PURGE_CATEGORIES.get(category)
        if manual_reason:
            # P12-8 / DCI-020: auto-purge is EXPLICITLY unsupported for this
            # category — a deliberate posture, not a gap. Enforcement is the
            # documented manual procedure (approval + record_manual_purge
            # evidence), not this engine.
            return {
                "category": category,
                "status": "manual_purge_required",
                "auto_purge_supported": False,
                "manual_reason": manual_reason,
                "manual_procedure": MANUAL_PURGE_PROCEDURE_REF,
                "retention_days": retention_days,
                "cutoff_date": cutoff,
                "records_deleted": 0,
                "dry_run": dry_run,
            }
        return {"error": f"Category '{category}' has no direct table mapping — requires manual purge"}

    table, date_col = mapping
    _assert_safe_sql_identifier(table, _ALLOWED_GDPR_TABLES, "table")
    _assert_safe_sql_identifier(date_col, _ALLOWED_GDPR_DATE_COLS, "date_col")

    # Hard protection: the tamper-evident supervisor chain may never be purged by
    # this engine, on any path. (audit finding B1 / tamper-evidence integrity)
    if table in _NEVER_PURGE_TABLES:
        logger.error(
            "Refusing GDPR purge on protected tamper-evidence table %r (category=%s)",
            table, category,
        )
        return {
            "category": category,
            "error": f"Category '{category}' resolves to protected table '{table}'; purge refused",
            "records_deleted": 0,
            "records_found": 0,
        }

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

    # Execute purge + evidence log in ONE transaction (P12-8 / DCI-021):
    # a purge whose evidence row cannot be written must not commit — the
    # exception propagates, the caller's connection rolls back, and the data
    # survives. The log row carries batch id, per-table counts and structured
    # evidence so a regulator can reconstruct the purge from the log alone.
    batch_id = purge_batch_id or f"purge-{uuid4().hex[:12]}"
    cur = db.execute(f"DELETE FROM {table} WHERE {date_col} < ?", (cutoff,))
    deleted = getattr(cur, "rowcount", None)
    if deleted is None:
        deleted = getattr(getattr(db, "_cursor", None), "rowcount", None)
    if deleted is None or deleted < 0:
        deleted = count  # engine did not report a rowcount — fall back to precount

    evidence = {
        "engine": "gdpr.purge_expired_data",
        "cutoff_date": cutoff,
        "retention_days": retention_days,
        "policy_id": policy["id"],
        "auto_purge_policy": bool(policy["auto_purge"]),
        "requires_review_policy": bool(policy["requires_review"]),
        "precount": count,
        "deleted_rowcount": deleted,
        "oldest_record_date": oldest,
        "newest_record_date": newest,
    }
    db.execute(
        "INSERT INTO data_purge_log (data_category, record_count, oldest_record_date, "
        "newest_record_date, retention_policy_id, purge_reason, purged_by, "
        "subject_id, application_id, tables_affected, per_table_counts, "
        "purge_batch_id, evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (category, deleted, oldest, newest, policy["id"],
         f"Retention policy enforcement: {retention_days} days exceeded (cutoff: {cutoff})",
         purged_by or "system",
         None, None,  # age-based bulk purge: not subject/application scoped
         json.dumps([table]), json.dumps({table: deleted}),
         batch_id, json.dumps(evidence, default=str))
    )
    db.commit()

    result["records_deleted"] = deleted
    result["purge_batch_id"] = batch_id
    logger.info("GDPR purge: %d records deleted from %s (category: %s, cutoff: %s, batch: %s)",
                deleted, table, category, cutoff, batch_id)
    return result


def run_scheduled_purge(db, purged_by: str = "system") -> List[Dict]:
    """
    Run purge for all auto_purge=True policies.
    Called by cron or periodic task scheduler.
    Returns list of purge results.
    """
    # auto_purge is BOOLEAN on PostgreSQL and INTEGER(0/1) on SQLite. `= TRUE` is
    # valid on both; the previous `auto_purge = 1 OR auto_purge = true` raised
    # "operator does not exist: boolean = integer" on PostgreSQL, so the daily
    # purge crashed on Postgres before reaching the audit-table guard below.
    policies = db.execute(
        "SELECT * FROM data_retention_policies WHERE auto_purge = TRUE"
    ).fetchall()

    # One batch id per scheduled run (P12-8 / DCI-021): every category purged
    # in this run shares it, so the whole run is reconstructable from
    # data_purge_log alone.
    batch_id = f"sched-{uuid4().hex[:12]}"

    results = []
    for policy in policies:
        category = policy["data_category"]

        # Defence-in-depth (audit finding B1): the unattended scheduler must never
        # delete from the audit tables, regardless of how a retention policy is
        # (mis)configured. Deliberate manual retention purges go through
        # purge_expired_data() directly and are still permitted for audit_log.
        mapping = CATEGORY_TABLE_MAP.get(category)
        resolved_table = mapping[0] if mapping else None
        if resolved_table in _NEVER_AUTO_PURGE_TABLES:
            logger.error(
                "Refusing AUTOMATIC purge of audit table %r (category=%s); "
                "audit trail is retained and may only be purged by a deliberate manual action",
                resolved_table, category,
            )
            results.append({
                "category": category,
                "error": f"automatic purge of protected audit table '{resolved_table}' refused",
                "records_deleted": 0,
            })
            continue

        # P12-8 / DCI-020: a manual-with-procedure category carrying
        # auto_purge=TRUE is a policy MISCONFIGURATION — the scheduler cannot
        # enforce it and silence would look like enforcement. Loud every run.
        if not mapping and category in MANUAL_PURGE_CATEGORIES:
            logger.error(
                "Retention policy misconfiguration: category %r has "
                "auto_purge=TRUE but is manual-with-procedure (no table "
                "mapping). The scheduler CANNOT enforce it — follow %s. "
                "Reason: %s",
                category, MANUAL_PURGE_PROCEDURE_REF,
                MANUAL_PURGE_CATEGORIES[category],
            )
            results.append({
                "category": category,
                "status": "manual_purge_required",
                "auto_purge_supported": False,
                "misconfigured_auto_purge_flag": True,
                "manual_procedure": MANUAL_PURGE_PROCEDURE_REF,
                "records_deleted": 0,
            })
            continue

        result = purge_expired_data(
            db, category, purged_by=purged_by, dry_run=False,
            purge_batch_id=batch_id,
        )
        results.append(result)

    return results


def record_manual_purge(
    db,
    category: str,
    per_table_counts: Dict[str, int],
    purge_reason: str,
    purged_by: str,
    approved_by: str,
    subject_id: Optional[str] = None,
    application_id: Optional[str] = None,
    oldest_record_date: Optional[str] = None,
    newest_record_date: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict:
    """Record evidence for an operator-performed MANUAL retention purge
    (P12-8 / DCI-020+021).

    The manual procedure (MANUAL_PURGE_PROCEDURE_REF) requires this call
    immediately after the approved deletion, on the same change window.  It
    writes the same enriched data_purge_log evidence the automatic engine
    writes — batch id, per-table counts, subject/application scoping and a
    structured evidence document naming the approver.

    Refuses to record deletions from _NEVER_PURGE_TABLES: those may not be
    deleted by ANY path, so evidence of such a deletion indicates an
    incident, not a procedure.
    """
    if not per_table_counts or not isinstance(per_table_counts, dict):
        return {"error": "per_table_counts must be a non-empty {table: count} mapping"}
    if not (purge_reason or "").strip():
        return {"error": "purge_reason is required"}
    if not (purged_by or "").strip() or not (approved_by or "").strip():
        return {"error": "purged_by and approved_by are both required"}
    forbidden = sorted(set(per_table_counts) & _NEVER_PURGE_TABLES)
    if forbidden:
        return {
            "error": (
                f"Tables {forbidden} are never-purge protected; a deletion "
                "there is an incident, not a manual purge — do not record it "
                "here"
            )
        }
    try:
        counts = {str(t): int(c) for t, c in per_table_counts.items()}
    except (TypeError, ValueError):
        return {"error": "per_table_counts values must be integers"}
    if any(c < 0 for c in counts.values()):
        return {"error": "per_table_counts values must be >= 0"}

    policy = db.execute(
        "SELECT * FROM data_retention_policies WHERE data_category = ?", (category,)
    ).fetchone()
    if not policy:
        return {"error": f"No retention policy found for category '{category}'"}

    batch_id = f"manual-{uuid4().hex[:12]}"
    total = sum(counts.values())
    evidence_doc = {
        "engine": "gdpr.record_manual_purge",
        "procedure": MANUAL_PURGE_PROCEDURE_REF,
        "approved_by": approved_by,
        "per_table_counts": counts,
        "operator_evidence": evidence or {},
    }
    db.execute(
        "INSERT INTO data_purge_log (data_category, record_count, oldest_record_date, "
        "newest_record_date, retention_policy_id, purge_reason, purged_by, "
        "subject_id, application_id, tables_affected, per_table_counts, "
        "purge_batch_id, evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (category, total, oldest_record_date, newest_record_date, policy["id"],
         f"MANUAL purge per {MANUAL_PURGE_PROCEDURE_REF}: {purge_reason.strip()}",
         purged_by, subject_id, application_id,
         json.dumps(sorted(counts.keys())), json.dumps(counts),
         batch_id, json.dumps(evidence_doc, default=str))
    )
    db.commit()
    logger.info(
        "GDPR manual purge recorded: category=%s total=%d tables=%s batch=%s "
        "purged_by=%s approved_by=%s",
        category, total, sorted(counts.keys()), batch_id, purged_by, approved_by,
    )
    return {
        "status": "recorded",
        "purge_batch_id": batch_id,
        "category": category,
        "record_count": total,
        "tables_affected": sorted(counts.keys()),
    }


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
    return format_dsar_for_response(dict(row), db=db) if row else {
        "status": "created",
        "due_at": due_at,
        "erasure_executed": False,
    }


def get_pending_dsars(db) -> List[Dict]:
    """Get all pending/in-progress DSARs ordered by due date."""
    rows = db.execute(
        "SELECT * FROM data_subject_requests WHERE status IN ('pending', 'in_progress') ORDER BY due_at ASC"
    ).fetchall()
    return [format_dsar_for_response(dict(r), db=db) for r in rows]


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

    existing = db.execute(
        "SELECT * FROM data_subject_requests WHERE id = ?",
        (dsar_id,),
    ).fetchone()
    if not existing:
        return {"error": f"DSAR {dsar_id} not found"}
    no_execution_notes = (
        "Response workflow completed. No erasure executor ran; underlying "
        "records may remain subject to AML/legal retention."
    )
    legal_retention_notes = (
        "Response workflow completed. Records remain retained under "
        "AML/legal retention."
    )

    db.execute(
        """
        UPDATE data_subject_requests
           SET status = ?,
               handled_by = ?,
               response_notes = ?,
               completed_at = ?,
               erasure_executed = COALESCE(erasure_executed, FALSE),
               retention_outcome = CASE
                   WHEN request_type = 'erasure'
                    AND ? = 'completed'
                    AND COALESCE(erasure_executed, FALSE) = FALSE
                    AND retention_outcome IS NULL
                   THEN ?
                   ELSE retention_outcome
               END,
               erasure_notes = CASE
                   WHEN request_type = 'erasure'
                    AND ? = 'completed'
                    AND COALESCE(erasure_executed, FALSE) = FALSE
                    AND erasure_notes IS NULL
                    AND retention_outcome = ?
                   THEN ?
                   WHEN request_type = 'erasure'
                    AND ? = 'completed'
                    AND COALESCE(erasure_executed, FALSE) = FALSE
                    AND erasure_notes IS NULL
                   THEN ?
                   ELSE erasure_notes
               END
         WHERE id = ?
        """,
        (
            new_status,
            handled_by,
            response_notes,
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            new_status,
            DSAR_ERASURE_OUTCOME_RESPONSE_COMPLETED_NO_EXECUTION,
            new_status,
            DSAR_ERASURE_OUTCOME_RETAINED_LEGAL,
            legal_retention_notes,
            new_status,
            no_execution_notes,
            dsar_id,
        )
    )
    db.commit()

    row = db.execute(
        "SELECT * FROM data_subject_requests WHERE id = ?",
        (dsar_id,),
    ).fetchone()
    logger.info("DSAR %d marked as %s by %s", dsar_id, new_status, handled_by)
    return format_dsar_for_response(dict(row), db=db) if row else {
        "id": dsar_id,
        "status": new_status,
        "handled_by": handled_by,
        "erasure_executed": False,
    }


def verify_dsar_erasure_evidence(db, dsar_request_id, client_id) -> bool:
    """Return True only if a COMPLETED erasure for this subject is on record (H2B).

    Caveats A+B: DSAR erasure status must be derived from evidence, not a
    trusted flag. A qualifying record is a gdpr_erasure_log row that (a) is the
    executor's ``erasure_completed`` marker — written ONLY on a fully-satisfied
    erasure with nothing refused/deferred, so a dry-run, generic, or PARTIAL run
    can never satisfy it (adversarial F4); (b) is not a dry run; AND (c) is bound
    to BOTH the DSAR correlation id and the subject ``client_id`` — so a shared
    or hostile correlation token cannot mark another subject's DSAR (F5).
    ``complete_dsar`` cannot set ``erasure_executed``; only an executor that
    wrote such a marker (via ``mark_dsar_erasure_executed``) may flip it.
    """
    return _completed_erasure_marker_exists(
        db,
        client_id=client_id,
        dsar_request_id=dsar_request_id,
        require_correlation=True,
    )


def mark_dsar_erasure_executed(db, dsar_id: int, dsar_request_id) -> bool:
    """Set erasure_executed=TRUE on a DSAR — ONLY with qualifying log evidence.

    The ONLY sanctioned path to flip erasure_executed. It binds the evidence
    check to the DSAR's own ``client_id`` (looked up here, not caller-supplied),
    and refuses unless ``verify_dsar_erasure_evidence`` confirms a completed,
    non-dry-run, subject-bound execution. Intended for the future
    executor→DSAR wiring; nothing in the live path calls it today (H2B OFF).
    Returns whether it flipped.
    """
    dsar = db.execute(
        "SELECT client_id FROM data_subject_requests WHERE id = ?", (dsar_id,)
    ).fetchone()
    dsar_client_id = (dsar["client_id"] if dsar and not isinstance(dsar, tuple) else (dsar[0] if dsar else None))
    if not verify_dsar_erasure_evidence(db, dsar_request_id, dsar_client_id):
        logger.warning(
            "refusing to mark DSAR %s erasure_executed: no qualifying completed, "
            "non-dry-run erasure evidence bound to client %s for correlation id %s",
            dsar_id, dsar_client_id, dsar_request_id,
        )
        return False
    db.execute(
        "UPDATE data_subject_requests SET erasure_executed = TRUE WHERE id = ?",
        (dsar_id,),
    )
    return True
