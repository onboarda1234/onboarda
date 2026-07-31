-- Migration 055 / PR-MON-M1-STATE-MACHINE-1:
-- typed transition-evidence persistence for Monitoring dismissal controls.
--
-- This additive column stores a JSON object containing only the supported
-- typed evidence identifiers validated by the canonical transition service.
-- It is deliberately nullable and has no default. Existing review-request
-- rows, evidence_ref values, statuses, and audit history are never rewritten.
--
-- Normal application startup installs and verifies the same schema through
-- db._ensure_monitoring_review_transition_evidence_schema before marking this
-- file covered by init_db. The file remains executable for the standalone
-- migration runner. DBConnection's migration shim makes ADD COLUMN IF NOT
-- EXISTS idempotent on SQLite; PostgreSQL supports it natively.
ALTER TABLE monitoring_alert_review_requests
    ADD COLUMN IF NOT EXISTS transition_evidence TEXT;

-- Fail-closed concurrency backstop. PostgreSQL and SQLite both support
-- partial unique indexes; creation refuses to proceed if legacy data already
-- contains more than one pending request for an alert. No row is rewritten.
CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_review_requests_one_pending
    ON monitoring_alert_review_requests(alert_id)
    WHERE state = 'pending';

-- Narrow rollback (operator-run only, after checking no transition audit
-- depends on the typed evidence payload):
--
--   DROP INDEX IF EXISTS uq_monitoring_review_requests_one_pending;
--
--   ALTER TABLE monitoring_alert_review_requests
--       DROP COLUMN IF EXISTS transition_evidence;
--
-- Rollback removes only this additive persistence field. It must not modify
-- evidence_ref, delete review requests, or rewrite Monitoring Alert data.
