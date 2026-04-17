-- Migration 007: Create screening_reports_normalized table
-- Sprint 2 SCR-005: Separate normalized screening storage
-- This table stores non-authoritative normalized screening reports
-- for the ComplyAdvantage migration scaffolding.
--
-- SAFETY: is_authoritative defaults to 0 (false) and must not be set to 1 in Sprint 1-2.
-- SAFETY: No EX-validated control reads this table.
-- SAFETY: source defaults to 'migration_scaffolding'.

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
);

-- Index for tenant-scoped queries
CREATE INDEX IF NOT EXISTS idx_screening_normalized_client_app
    ON screening_reports_normalized(client_id, application_id);

-- Index for parity checks
CREATE INDEX IF NOT EXISTS idx_screening_normalized_app_id
    ON screening_reports_normalized(application_id);
