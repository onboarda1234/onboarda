-- Migration 047: index monitoring_alert_evidence(application_id).
-- ==========================================================================
-- Evidence-mode screening-queue hydration batch-loads monitoring evidence
-- for the applications on the returned page
-- (server._load_monitoring_evidence_batch). The table only carried indexes
-- on monitoring_alert_id and (provider, case_identifier), so that lookup was
-- a full table scan per request and degraded without bound as monitoring
-- evidence accumulated (staging measured p50 21s on
-- /api/screening/queue?include_evidence=1).
--
-- Fresh schemas create this index directly in db.py; long-lived databases
-- are also repaired by an inline CREATE INDEX IF NOT EXISTS during startup.
-- This file keeps the migration ledger required by ADR 0008 and is
-- idempotent on both SQLite and PostgreSQL.
CREATE INDEX IF NOT EXISTS idx_monitoring_alert_evidence_app
    ON monitoring_alert_evidence(application_id);
