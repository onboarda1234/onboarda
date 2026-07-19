-- Migration 049: foreign-key index coverage, batch 2 (DCI-104).
-- ==========================================================================
-- DCI-104 flagged foreign-key columns with no covering index: joins and
-- ON DELETE CASCADE integrity scans over them were full table scans that
-- degrade as the referenced parents grow. Batch 1 added
-- idx_agent_executions_document_id (#771) and idx_monitoring_alert_evidence_app
-- (migration 047). This batch adds the next highest-value FK indexes.
--
-- Indexes are additive — they never change query results — so they are safe on
-- regulated / change-controlled tables (P12-1). Fresh schemas pick these up via
-- the same idempotent CREATE INDEX IF NOT EXISTS block in db._run_migrations();
-- this file keeps the ADR-0008 migration ledger entry. Idempotent and
-- dialect-neutral (SQLite + PostgreSQL).
--
-- The high-fan-in audit-actor FKs (*_by / *_reviewer / *_officer -> users) are
-- deliberately NOT indexed (write overhead, no hot reverse-lookup path). Four
-- lower-value navigational FKs on change-management tables are deferred to a
-- later batch because their tables are created by an _ensure_* step that runs
-- after this migration on a fresh DB.

-- Tier A — hot read paths / cascade parents:
CREATE INDEX IF NOT EXISTS idx_screening_reviews_application_id ON screening_reviews(application_id);
CREATE INDEX IF NOT EXISTS idx_client_sessions_application_id ON client_sessions(application_id);
CREATE INDEX IF NOT EXISTS idx_verification_jobs_application_id ON verification_jobs(application_id);
CREATE INDEX IF NOT EXISTS idx_sar_reports_alert_id ON sar_reports(alert_id);

-- Tier B — coverage + cascade/integrity scans:
CREATE INDEX IF NOT EXISTS idx_aer_linked_document_id ON application_enhanced_requirements(linked_document_id);
CREATE INDEX IF NOT EXISTS idx_supervisor_human_reviews_escalation ON supervisor_human_reviews(escalation_id);
CREATE INDEX IF NOT EXISTS idx_data_purge_log_retention_policy ON data_purge_log(retention_policy_id);
CREATE INDEX IF NOT EXISTS idx_documents_superseded_by ON documents(superseded_by_document_id);
