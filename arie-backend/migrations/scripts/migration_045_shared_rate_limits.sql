-- Migration 045 (BSA-002): shared fail-closed rate-limit table marker.
-- ==========================================================================
-- Fresh schemas carry shared_rate_limits inline. Long-lived databases are
-- repaired by db.py inline migration v2.51 so the dialect-specific DDL can be
-- created safely during startup. This file exists for schema_version
-- continuity and the repo migration policy gate.
SELECT 1;
