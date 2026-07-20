-- Migration 049 (SRP per-hit disposition): screening_hit_dispositions —
-- granular per-hit screening decisions.
-- ==========================================================================
-- Each individual screening hit is dispositioned on its own (Confirm true match
-- / Clear false positive / Escalate / Request more information) with a per-hit
-- materiality call recorded on a true match. The subject-level rollup that feeds
-- the frozen approval gates is still written through screening_reviews (via the
-- existing /api/screening/review flow) — this table is the granular per-hit
-- record backing the review UI and the audit trail. hit_id is the stable
-- provider record identifier. An "undo" deletes the row, so 'pending' is never
-- stored.
--
-- Fresh schemas create this table directly in db.py (_get_postgres_schema /
-- _get_sqlite_schema); this file keeps the migration ledger required by
-- ADR 0008. The migration runner's connection wrapper rewrites AUTOINCREMENT /
-- datetime for PostgreSQL.

CREATE TABLE IF NOT EXISTS screening_hit_dispositions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    subject_type TEXT NOT NULL,
    subject_name TEXT NOT NULL,
    hit_id TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK(disposition IN ('match','cleared','escalated','follow_up_required')),
    materiality TEXT CHECK(materiality IN ('high','moderate','nonmaterial','insufficient')),
    rationale TEXT,
    reviewer_id TEXT REFERENCES users(id),
    reviewer_name TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(application_id, subject_type, subject_name, hit_id)
);

CREATE INDEX IF NOT EXISTS idx_screening_hit_dispositions_subject
    ON screening_hit_dispositions(application_id, subject_type, subject_name);
