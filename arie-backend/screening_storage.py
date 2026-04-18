"""
Normalized Screening Storage — Persistence Helper
==================================================
Functions for persisting and querying normalized screening reports
in the screening_reports_normalized table.

SAFETY: This table is non-authoritative in Sprint 1-2.
SAFETY: No EX-validated control reads this storage.
SAFETY: Does not modify db.py (protected file).
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
