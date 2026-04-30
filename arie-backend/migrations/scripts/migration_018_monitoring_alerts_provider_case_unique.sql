-- Migration 018: Monitoring Alerts Provider/Case Uniqueness
-- ==========================================================
-- Adds provider/case_identifier columns to monitoring_alerts so C4
-- ComplyAdvantage webhook processing can upsert one operational row per CA
-- case while preserving legacy Sumsub-shaped rows where both fields are NULL.
--
-- SCOPE: additive only. Existing rows remain valid because new columns are
-- nullable and the unique index is partial.
--
-- DIALECT: follows migration_014's cross-dialect ADD COLUMN convention: use
-- ALTER TABLE ... ADD COLUMN without IF NOT EXISTS because SQLite does not
-- accept ADD COLUMN IF NOT EXISTS. The partial UNIQUE INDEX syntax is shared by
-- PostgreSQL and SQLite and is guarded with IF NOT EXISTS for replay safety.
--
-- IDEMPOTENCY: column additions are protected by schema_version runner
-- discipline; index creation is additionally guarded by IF NOT EXISTS.

ALTER TABLE monitoring_alerts ADD COLUMN provider TEXT;
ALTER TABLE monitoring_alerts ADD COLUMN case_identifier TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_alerts_provider_case
    ON monitoring_alerts (provider, case_identifier)
    WHERE provider IS NOT NULL AND case_identifier IS NOT NULL;
