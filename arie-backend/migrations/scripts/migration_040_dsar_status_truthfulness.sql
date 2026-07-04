-- Migration 040: DSAR status truthfulness columns (H2A)
-- =====================================================
-- Adds explicit erasure/retention truth fields to data_subject_requests so
-- status='completed' can mean "response workflow completed" without implying
-- that data was erased. This migration is additive only and performs no data
-- deletion, anonymisation, purge, or erasure execution.
--
-- The migration runner executes this file transactionally on PostgreSQL. If an
-- ALTER fails on PostgreSQL, the runner rolls back and halts startup; operators
-- should fix the schema mismatch, verify these columns, and rerun migrations.

ALTER TABLE data_subject_requests ADD COLUMN IF NOT EXISTS erasure_executed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE data_subject_requests ADD COLUMN IF NOT EXISTS retention_outcome TEXT;
ALTER TABLE data_subject_requests ADD COLUMN IF NOT EXISTS retained_until TEXT;
ALTER TABLE data_subject_requests ADD COLUMN IF NOT EXISTS retained_categories TEXT;
ALTER TABLE data_subject_requests ADD COLUMN IF NOT EXISTS erasure_notes TEXT;
