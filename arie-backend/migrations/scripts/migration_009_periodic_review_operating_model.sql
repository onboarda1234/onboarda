-- Migration 009: Periodic Review Operating Model (PR-03)
-- =======================================================
-- Adds the minimum nullable columns required to turn periodic_reviews
-- from a thin pending/completed placeholder into a real lifecycle
-- review object with explicit outcome semantics and structured
-- required-item generation.
--
-- SCOPE: additive only. No column renames. No FKs. No CHECK constraints
-- (SQLite cannot add CHECK via ALTER TABLE; the application layer
-- enforces the vocabulary -- see periodic_review_engine.py). No changes
-- to PROTECTED_FILES. No memo / artifact pointer is added to lifecycle
-- rows in PR-03 -- onboarding memo identity remains per-application
-- per-version and is intentionally separate from periodic-review
-- lifecycle context.
--
-- The existing `status` column on periodic_reviews carries the
-- operational state. PR-03 extends the in-code vocabulary from the
-- legacy {pending, completed} pair to include {in_progress,
-- awaiting_information, pending_senior_review, completed}. There is no
-- DB-level CHECK on `status` today so this is safe and additive.
--
-- The existing `decision` column is left untouched for backward
-- compatibility with the legacy PeriodicReviewDecisionHandler. The new
-- explicit `outcome` column is the source of truth for review outcome
-- going forward; legacy callers continue to work unchanged.
--
-- DIALECT: ALTER TABLE ... ADD COLUMN (without IF NOT EXISTS) is
-- supported by both SQLite (>=3.2) and PostgreSQL (>=9.x).
-- CREATE INDEX IF NOT EXISTS is portable.
--
-- IDEMPOTENCY: provided by the migration runner via schema_version.
-- Manual re-execution outside the runner is not supported.
--
-- EX-CONTROL IMPACT: none. No file in PROTECTED_FILES is modified by
-- this migration. No existing column is altered. No existing row is
-- mutated. EX-01..EX-13 regressions are impossible by construction.

-- periodic_reviews -------------------------------------------------
ALTER TABLE periodic_reviews ADD COLUMN outcome TEXT;
ALTER TABLE periodic_reviews ADD COLUMN outcome_reason TEXT;
ALTER TABLE periodic_reviews ADD COLUMN outcome_recorded_at TIMESTAMP;
ALTER TABLE periodic_reviews ADD COLUMN required_items TEXT;
ALTER TABLE periodic_reviews ADD COLUMN required_items_generated_at TIMESTAMP;
ALTER TABLE periodic_reviews ADD COLUMN state_changed_at TIMESTAMP;

-- Lookup indexes ---------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_periodic_reviews_status
    ON periodic_reviews(status);
CREATE INDEX IF NOT EXISTS idx_periodic_reviews_outcome
    ON periodic_reviews(outcome);
