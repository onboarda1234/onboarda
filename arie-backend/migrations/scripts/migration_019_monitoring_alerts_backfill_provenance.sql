-- Migration 019: Monitoring Alerts Backfill Provenance
-- ====================================================
-- Adds provenance columns used by the Agent 7 historical ComplyAdvantage
-- backfill side-PR. Fresh installs receive the same columns from db.py.
--
-- SCOPE: additive only. No columns beyond discovered_via, discovered_at, and
-- backfill_run_id are added.
--
-- DIALECT: ALTER TABLE ... ADD COLUMN without IF NOT EXISTS follows the
-- repository's SQLite/PostgreSQL migration convention. Idempotency is provided
-- by the schema_version runner.

ALTER TABLE monitoring_alerts ADD COLUMN discovered_via TEXT NOT NULL DEFAULT 'webhook_live'
    CHECK(discovered_via IN ('webhook_live','webhook_backfill','manual_backfill'));
ALTER TABLE monitoring_alerts ADD COLUMN discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE monitoring_alerts ADD COLUMN backfill_run_id TEXT;
