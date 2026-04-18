-- Migration 008: Lifecycle Linkage Foundation (PR-01)
-- =====================================================
-- Adds nullable linkage, lifecycle-timestamp, and priority fields to
-- edd_cases, periodic_reviews, and monitoring_alerts so that EDD cases,
-- periodic reviews, and monitoring alerts can explicitly reference each
-- other and record provenance without free-text notes.
--
-- SCOPE: additive only. No column renames. No FKs (soft references only
-- in PR-01 to keep SQLite/PostgreSQL parity trivial and to avoid schema
-- ordering surprises). No modification of compliance_memos. No memo
-- pointer on lifecycle rows in PR-01 -- memo identity is per-application
-- per-version and there is no "active memo" convention in the repo
-- today; wiring that is deferred to a later PR.
--
-- DIALECT: ALTER TABLE ... ADD COLUMN (without IF NOT EXISTS) is
-- supported by both SQLite (>=3.2) and PostgreSQL (>=9.x). We do NOT
-- use "ADD COLUMN IF NOT EXISTS" because SQLite does not accept that
-- clause. CREATE INDEX IF NOT EXISTS is portable and is used below.
--
-- ENUM ENFORCEMENT: SQLite cannot add CHECK constraints via ALTER TABLE.
-- Application-layer validation is the source of truth
-- (see arie-backend/lifecycle_linkage.py). Named CHECK constraints on
-- PostgreSQL can be added in a follow-up migration once the runner
-- supports dialect-specific blocks.
--
-- IDEMPOTENCY: idempotency is provided by the migration runner, which
-- records applied versions in schema_version and will not re-run this
-- file. Manual re-execution outside the runner is NOT supported and
-- will raise "duplicate column name" -- this matches the contract of
-- migrations 001..007 in this repo.
--
-- EX-CONTROL IMPACT: none. No file in PROTECTED_FILES is modified.
-- No existing column is altered. No existing row is mutated. No
-- existing index is dropped. EX-01..EX-13 regressions are impossible
-- by construction.

-- edd_cases --------------------------------------------------------
ALTER TABLE edd_cases ADD COLUMN origin_context TEXT;
ALTER TABLE edd_cases ADD COLUMN linked_monitoring_alert_id INTEGER;
ALTER TABLE edd_cases ADD COLUMN linked_periodic_review_id INTEGER;
ALTER TABLE edd_cases ADD COLUMN assigned_at TIMESTAMP;
ALTER TABLE edd_cases ADD COLUMN escalated_at TIMESTAMP;
ALTER TABLE edd_cases ADD COLUMN closed_at TIMESTAMP;
ALTER TABLE edd_cases ADD COLUMN sla_due_at TIMESTAMP;
ALTER TABLE edd_cases ADD COLUMN priority TEXT;

-- periodic_reviews -------------------------------------------------
-- NOTE: existing columns trigger_type and trigger_reason are NOT
-- renamed. trigger_source is a new, disjoint field that captures
-- lifecycle origin (schedule / monitoring_alert / change_request /
-- manual), not the nature of the change.
ALTER TABLE periodic_reviews ADD COLUMN trigger_source TEXT;
ALTER TABLE periodic_reviews ADD COLUMN linked_monitoring_alert_id INTEGER;
ALTER TABLE periodic_reviews ADD COLUMN linked_edd_case_id INTEGER;
ALTER TABLE periodic_reviews ADD COLUMN review_reason TEXT;
ALTER TABLE periodic_reviews ADD COLUMN assigned_at TIMESTAMP;
ALTER TABLE periodic_reviews ADD COLUMN closed_at TIMESTAMP;
ALTER TABLE periodic_reviews ADD COLUMN sla_due_at TIMESTAMP;
ALTER TABLE periodic_reviews ADD COLUMN priority TEXT;

-- monitoring_alerts ------------------------------------------------
ALTER TABLE monitoring_alerts ADD COLUMN linked_periodic_review_id INTEGER;
ALTER TABLE monitoring_alerts ADD COLUMN linked_edd_case_id INTEGER;
ALTER TABLE monitoring_alerts ADD COLUMN triaged_at TIMESTAMP;
ALTER TABLE monitoring_alerts ADD COLUMN assigned_at TIMESTAMP;
ALTER TABLE monitoring_alerts ADD COLUMN resolved_at TIMESTAMP;

-- Soft-reference lookup indexes -----------------------------------
CREATE INDEX IF NOT EXISTS idx_edd_cases_linked_alert
    ON edd_cases(linked_monitoring_alert_id);
CREATE INDEX IF NOT EXISTS idx_edd_cases_linked_review
    ON edd_cases(linked_periodic_review_id);
CREATE INDEX IF NOT EXISTS idx_edd_cases_origin_context
    ON edd_cases(origin_context);
CREATE INDEX IF NOT EXISTS idx_periodic_reviews_linked_alert
    ON periodic_reviews(linked_monitoring_alert_id);
CREATE INDEX IF NOT EXISTS idx_periodic_reviews_linked_edd
    ON periodic_reviews(linked_edd_case_id);
CREATE INDEX IF NOT EXISTS idx_periodic_reviews_trigger_source
    ON periodic_reviews(trigger_source);
CREATE INDEX IF NOT EXISTS idx_monitoring_alerts_linked_edd
    ON monitoring_alerts(linked_edd_case_id);
CREATE INDEX IF NOT EXISTS idx_monitoring_alerts_linked_review
    ON monitoring_alerts(linked_periodic_review_id);
