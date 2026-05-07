-- Migration 024: Document upload slot versioning
-- ==============================================
--
-- Adds the durable versioning fields used to retain historical document rows
-- while exposing only one current document per logical upload slot.
--
-- The data repair itself is performed by db.py:repair_document_current_versions
-- during startup. That code path is intentionally shared by PostgreSQL and
-- SQLite, resolves typed person slots against directors/UBOs/intermediaries,
-- logs ambiguous legacy person references, marks older duplicate rows as
-- superseded, and creates the partial unique current-slot index only after
-- cleanup.

ALTER TABLE documents ADD COLUMN slot_key TEXT;
ALTER TABLE documents ADD COLUMN is_current BOOLEAN DEFAULT TRUE;
ALTER TABLE documents ADD COLUMN version INTEGER DEFAULT 1;
ALTER TABLE documents ADD COLUMN superseded_at TIMESTAMP;
ALTER TABLE documents ADD COLUMN superseded_by_document_id TEXT REFERENCES documents(id);
ALTER TABLE documents ADD COLUMN replaced_reason TEXT;
ALTER TABLE documents ADD COLUMN replaced_by_user_id TEXT;

CREATE INDEX IF NOT EXISTS idx_documents_current_slot
    ON documents(application_id, slot_key, is_current);
