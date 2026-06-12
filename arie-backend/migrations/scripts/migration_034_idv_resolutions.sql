-- Sprint 1 IDV resolution gate
-- Durable officer/senior IDV resolution records. Approval gates use this table
-- to distinguish provider status from authorised manual resolution.

CREATE TABLE IF NOT EXISTS idv_resolutions (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    application_ref TEXT,
    person_id TEXT,
    person_type TEXT,
    person_name TEXT,
    prior_provider_status TEXT,
    prior_review_answer TEXT,
    resolution_status TEXT NOT NULL,
    resolution_outcome TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    evidence_reviewed TEXT NOT NULL DEFAULT '[]',
    rationale TEXT NOT NULL,
    confirmation_text TEXT,
    senior_approver_id TEXT,
    resolved_by TEXT NOT NULL,
    resolved_by_name TEXT,
    resolved_by_role TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_idv_resolutions_app
    ON idv_resolutions(application_id);

CREATE INDEX IF NOT EXISTS idx_idv_resolutions_subject
    ON idv_resolutions(application_id, person_type, person_id, person_name);

CREATE INDEX IF NOT EXISTS idx_idv_resolutions_status
    ON idv_resolutions(resolution_status);
