-- Migration 052: index screening_hit_dispositions(application_id, subject_type, subject_name).
-- ==========================================================================
-- The idx_screening_hit_dispositions_subject index backs the per-hit
-- disposition hydration hot paths (screening_hit_dispositions is looked up by
-- application_id on ~9 code paths). It was carried in migration_049 but was
-- MISSING from the db.py init_db base schema (_get_postgres_schema /
-- _get_sqlite_schema), so FRESH installs created the table WITHOUT the index.
--
-- db.py now creates the index directly for fresh schemas; this file keeps the
-- migration ledger required by ADR 0008 and repairs any long-lived database
-- that predates the index. Idempotent on both SQLite and PostgreSQL.
CREATE INDEX IF NOT EXISTS idx_screening_hit_dispositions_subject
    ON screening_hit_dispositions(application_id, subject_type, subject_name);
