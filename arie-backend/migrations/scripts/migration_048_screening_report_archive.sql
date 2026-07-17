-- Migration 048 (SRP-2): screening_report_archive — superseded screening
-- report snapshots.
-- ==========================================================================
-- The governed stale-report refresh (scripts/ops/refresh_stale_screening_reports.py)
-- re-screens applications whose stored ComplyAdvantage reports predate the
-- enriched normalizer. Screening evidence is a regulated record: the outgoing
-- report is archived here BEFORE the fresh report replaces it, so no refresh
-- ever destroys evidence. The table is append-only and listed in
-- regulated_deletion.REGULATED_TABLES.
--
-- Fresh schemas create this table directly in db.py; long-lived databases are
-- also repaired by an inline idempotent migration at startup. This file keeps
-- the migration ledger required by ADR 0008. The migration runner's connection
-- wrapper rewrites AUTOINCREMENT/datetime for PostgreSQL.

CREATE TABLE IF NOT EXISTS screening_report_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL,
    application_ref TEXT,
    archived_at TEXT DEFAULT (datetime('now')),
    archived_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    report_hash TEXT NOT NULL,
    report_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_screening_report_archive_app
    ON screening_report_archive(application_id);
