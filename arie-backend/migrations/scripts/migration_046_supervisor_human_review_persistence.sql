-- Migration 046 (BSA-003B): durable supervisor human-review persistence.
-- ==========================================================================
-- Fresh PostgreSQL and SQLite schemas carry supervisor_human_reviews,
-- supervisor_overrides, and supervisor_escalations directly in db.py.
-- Long-lived databases are repaired by dialect-aware inline migration v2.52,
-- which runs during startup and uses portable CURRENT_TIMESTAMP defaults.
-- This marker preserves the file migration ledger required by ADR 0008.
SELECT 1;
