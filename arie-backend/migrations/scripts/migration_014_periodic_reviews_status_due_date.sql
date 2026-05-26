-- Migration 014: Periodic Review A2 Backfill
-- =====================================================
-- Adds the A2 periodic_reviews.status and due_date columns to long-lived
-- databases that pre-date the init_db() schema update. Fresh installs already
-- receive these columns from db.py; this file closes the production resume gap
-- for existing databases through the file-based migration chain.
--
-- SCOPE: additive only. No table rebuilds, no data mutation, no constraints,
-- and no changes to any legacy inline migration path.
--
-- DIALECT: ALTER TABLE ... ADD COLUMN (without IF NOT EXISTS) is supported by
-- both SQLite (>=3.2) and PostgreSQL (>=9.x). We do NOT use ADD COLUMN IF NOT
-- EXISTS because SQLite does not accept that clause.
--
-- IDEMPOTENCY: idempotency is provided by the migration runner, which records
-- applied versions in schema_version and will not re-run this file. Manual
-- re-execution outside the runner is NOT supported and will raise duplicate
-- column errors; this matches the existing migration convention.

-- periodic_reviews -------------------------------------------------
ALTER TABLE periodic_reviews ADD COLUMN status TEXT DEFAULT 'pending';
ALTER TABLE periodic_reviews ADD COLUMN due_date TEXT;
