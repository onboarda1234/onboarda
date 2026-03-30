-- Migration 005: Applications truth schema
-- Adds ownership identity columns, intermediary shareholders, and durable
-- document review fields required by the Applications workflow.

ALTER TABLE directors ADD COLUMN IF NOT EXISTS person_key TEXT;
ALTER TABLE directors ADD COLUMN IF NOT EXISTS first_name TEXT;
ALTER TABLE directors ADD COLUMN IF NOT EXISTS last_name TEXT;
ALTER TABLE directors ADD COLUMN IF NOT EXISTS pep_declaration TEXT DEFAULT '{}';

ALTER TABLE ubos ADD COLUMN IF NOT EXISTS person_key TEXT;
ALTER TABLE ubos ADD COLUMN IF NOT EXISTS first_name TEXT;
ALTER TABLE ubos ADD COLUMN IF NOT EXISTS last_name TEXT;
ALTER TABLE ubos ADD COLUMN IF NOT EXISTS pep_declaration TEXT DEFAULT '{}';

CREATE TABLE IF NOT EXISTS intermediaries (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    person_key TEXT,
    entity_name TEXT NOT NULL,
    jurisdiction TEXT,
    ownership_pct REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE documents ADD COLUMN IF NOT EXISTS review_status TEXT DEFAULT 'pending';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS review_comment TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS reviewed_by TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS reviewed_at TEXT;

CREATE INDEX IF NOT EXISTS idx_intermediaries_application_id ON intermediaries(application_id);
