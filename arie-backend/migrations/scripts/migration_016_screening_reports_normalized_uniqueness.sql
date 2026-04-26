-- Migration 016: Screening Reports Normalized Uniqueness Constraint
-- =================================================================
-- Adds a UNIQUE constraint on (application_id, provider,
-- source_screening_report_hash) to enforce idempotent upsert behavior on
-- the webhook re-normalization path.
--
-- Without this constraint, persist_normalized_report inserts duplicate
-- rows when Sumsub sends multiple webhooks per application lifecycle.
--
-- DIALECT: portable across PostgreSQL and SQLite.
-- IDEMPOTENCY: guarded by schema_version + IF NOT EXISTS pattern.

CREATE UNIQUE INDEX IF NOT EXISTS uq_screening_normalized_app_provider_hash
  ON screening_reports_normalized (application_id, provider, source_screening_report_hash);
