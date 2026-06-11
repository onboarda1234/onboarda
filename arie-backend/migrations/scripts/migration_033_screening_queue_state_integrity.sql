-- SQ-1 Screening Queue State Integrity
-- This migration intentionally does not mutate application screening records.
-- Legacy contradictory rows are normalized at read time by screening_state.py.
-- The table below gives future controlled backfill jobs an audited place to
-- record findings before any explicit data-repair sprint mutates records.

CREATE TABLE IF NOT EXISTS screening_state_integrity_backfill_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT,
    application_ref TEXT,
    subject_type TEXT,
    subject_name TEXT,
    finding_code TEXT NOT NULL,
    raw_status_json TEXT,
    canonical_status TEXT,
    recommended_action TEXT NOT NULL DEFAULT 'review_before_data_mutation',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_screening_state_integrity_backfill_log_app
    ON screening_state_integrity_backfill_log(application_id);

CREATE INDEX IF NOT EXISTS idx_screening_state_integrity_backfill_log_finding
    ON screening_state_integrity_backfill_log(finding_code);
