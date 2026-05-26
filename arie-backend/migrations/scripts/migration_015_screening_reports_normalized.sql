-- Migration 015: Screening Reports Normalized A4 Backfill
-- =======================================================
-- Creates the A4 screening_reports_normalized table on long-lived databases
-- that pre-date the init_db() schema update. Fresh installs already receive
-- this table from db.py; this file makes production resume deterministic and
-- preserves a complete file-based audit trail.
--
-- SCOPE: additive only. No existing table is altered or dropped. The table is
-- created only if absent, and lookup indexes use IF NOT EXISTS.
--
-- DIALECT: INTEGER PRIMARY KEY AUTOINCREMENT and datetime('now') follow the
-- repo's portable migration convention and are translated by the runner for
-- PostgreSQL. CHECK constraints are shared by SQLite and PostgreSQL.
--
-- IDEMPOTENCY: CREATE TABLE/INDEX IF NOT EXISTS protects manual replays;
-- schema_version remains the runner-level source of truth.

-- Phase E will lift this temporary non-authoritative CHECK; see ADR 0008.
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
CREATE INDEX IF NOT EXISTS idx_screening_normalized_client_app ON screening_reports_normalized(client_id, application_id);
CREATE INDEX IF NOT EXISTS idx_screening_normalized_app_id ON screening_reports_normalized(application_id);
