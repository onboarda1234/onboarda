"""
Normalized Screening Storage — Persistence Helper
==================================================
Functions for persisting and querying normalized screening reports
in the screening_reports_normalized table.

SAFETY: This table is non-authoritative in Sprint 1-2.
SAFETY: No EX-validated control reads this storage.
SAFETY: Does not modify db.py (protected file).

GDPR / DSAR Treatment (Sprint 3 Obj 2b):
    screening_reports_normalized is EXCLUDED from DSAR/export because it
    contains a derived, non-authoritative copy of data already present in
    prescreening_data.screening_report.  Any DSAR or data-export request is
    satisfied by the legacy prescreening_data column, which is the single
    source of truth.  Including the normalized copy would duplicate data and
    risk confusion.  When normalized storage becomes authoritative
    (post-activation gate), DSAR treatment must be revisited.
"""

import hashlib
import json
import logging

logger = logging.getLogger("arie.screening_storage")

# Table DDL for creating the screening_reports_normalized table
# Used by migration script and by test setup
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS screening_reports_normalized (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL,
    application_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'sumsub',
    normalized_version TEXT NOT NULL DEFAULT '1.0',
    source_screening_report_hash TEXT,
    normalized_report_json TEXT,
    normalization_status TEXT NOT NULL DEFAULT 'success' CHECK(normalization_status IN ('success', 'failed')),
    normalization_error TEXT,
    is_authoritative INTEGER NOT NULL DEFAULT 0 CHECK(is_authoritative = 0),
    source TEXT NOT NULL DEFAULT 'migration_scaffolding',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
)
"""

_CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_screening_normalized_client_app ON screening_reports_normalized(client_id, application_id)",
    "CREATE INDEX IF NOT EXISTS idx_screening_normalized_app_id ON screening_reports_normalized(application_id)",
]


def ensure_normalized_table(db) -> None:
    """
    Ensure the screening_reports_normalized table exists.
    Safe to call multiple times (uses IF NOT EXISTS).

    NOTE: This is a standalone DDL setup function that commits its own work.
    DDL statements (CREATE TABLE / CREATE INDEX) are structural changes that
    must be committed immediately and cannot participate in caller-owned
    data transactions.  Do NOT use this as a pattern for DML helpers.
    """
    db.execute(_CREATE_TABLE_SQL)
    for idx_sql in _CREATE_INDEXES_SQL:
        db.execute(idx_sql)
    db.commit()


def compute_report_hash(report: dict) -> str:
    """
    Compute a stable hash of a screening report for change detection.
    Uses JSON serialization with sorted keys for determinism.
    """
    serialized = json.dumps(report, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:32]


def persist_normalized_report(
    db,
    client_id: str,
    application_id: str,
    normalized_report: dict,
    source_report_hash: str,
    provider: str = "sumsub",
    normalized_version: str = "1.0",
) -> int:
    """
    Persist a normalized screening report.

    Returns the row ID of the inserted record.

    Raises on database errors (caller must handle).
    """
    report_json = json.dumps(normalized_report, default=str)

    cursor = db.execute(
        """INSERT INTO screening_reports_normalized
           (client_id, application_id, provider, normalized_version,
            source_screening_report_hash, normalized_report_json,
            normalization_status, source)
           VALUES (?, ?, ?, ?, ?, ?, 'success', 'migration_scaffolding')""",
        (client_id, application_id, provider, normalized_version,
         source_report_hash, report_json),
    )
    return cursor.lastrowid


def persist_normalization_failure(
    db,
    client_id: str,
    application_id: str,
    source_report_hash: str,
    error_message: str,
    provider: str = "sumsub",
) -> int:
    """
    Persist a record of a failed normalization attempt.

    Returns the row ID of the inserted record.
    """
    cursor = db.execute(
        """INSERT INTO screening_reports_normalized
           (client_id, application_id, provider, normalized_version,
            source_screening_report_hash, normalization_status,
            normalization_error, source)
           VALUES (?, ?, ?, '1.0', ?, 'failed', ?, 'migration_scaffolding')""",
        (client_id, application_id, provider, source_report_hash, error_message),
    )
    return cursor.lastrowid


def get_normalized_report(db, application_id: str, client_id: str = None) -> dict:
    """
    Retrieve the latest normalized screening report for an application.
    Always tenant-scoped if client_id is provided.

    Returns None if no record exists.
    """
    if client_id:
        row = db.execute(
            """SELECT * FROM screening_reports_normalized
               WHERE application_id=? AND client_id=?
               ORDER BY id DESC LIMIT 1""",
            (application_id, client_id),
        ).fetchone()
    else:
        row = db.execute(
            """SELECT * FROM screening_reports_normalized
               WHERE application_id=?
               ORDER BY id DESC LIMIT 1""",
            (application_id,),
        ).fetchone()

    if row is None:
        return None

    result = dict(row)
    if result.get("normalized_report_json"):
        result["normalized_report"] = json.loads(result["normalized_report_json"])
    return result


def _is_missing_table_error(exc: Exception) -> bool:
    """
    Return True iff the exception indicates the screening_reports_normalized
    table is absent.  Matches the dialect-specific error wording used by the
    drivers we run against:

        * SQLite (sqlite3):  "no such table"
        * PostgreSQL (psycopg2): "does not exist" / "undefined table"

    All other exceptions are surfaced to the caller — we do NOT swallow
    arbitrary DB errors.
    """
    msg = str(exc).lower()
    return (
        "no such table" in msg
        or "does not exist" in msg
        or "undefined table" in msg
    )


def delete_normalized_reports_for_application(db, application_id: str) -> int:
    """
    Delete all normalized screening reports for an application.

    Used by application-delete cascade to prevent orphan records.
    Does NOT call commit — the caller owns the transaction boundary.

    Missing-table handling (Sprint 3 fixup H3):
        If `screening_reports_normalized` does not exist (e.g. migration 007
        has not been applied in this environment), the function logs and
        returns 0 instead of aborting the caller's cascade.  The transaction
        is rolled back first so subsequent statements in the same caller-owned
        transaction are not poisoned (PostgreSQL aborts the whole transaction
        on the first error).  All other exceptions are re-raised — we never
        broadly swallow DB errors.

    Returns the number of rows deleted (0 if table does not exist).
    """
    try:
        cursor = db.execute(
            "DELETE FROM screening_reports_normalized WHERE application_id=?",
            (application_id,),
        )
        # rowcount may be on the cursor (raw sqlite3 / psycopg2) or on the
        # underlying cursor exposed by the production DBConnection wrapper.
        # Fall back to -1 ("unknown") if neither path exposes it — callers
        # that need an exact count can query before/after.
        rowcount = getattr(cursor, "rowcount", None)
        if rowcount is None:
            inner = getattr(cursor, "_cursor", None)
            rowcount = getattr(inner, "rowcount", -1)
        return rowcount if rowcount is not None else -1
    except Exception as exc:
        if not _is_missing_table_error(exc):
            # Unknown DB error — surface to the caller, do NOT swallow.
            raise
        # Table is absent in this environment.  Roll back so the caller's
        # transaction is not left in an aborted state on PostgreSQL, then
        # report zero rows deleted so the cascade can proceed.
        try:
            db.rollback()
        except Exception:
            pass
        logger.info(
            "screening_reports_normalized absent — skipping delete for "
            "application_id=%s (migration 007 not applied here)",
            application_id,
        )
        return 0
