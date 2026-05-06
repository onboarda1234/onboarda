-- Application-specific generated Enhanced / EDD requirements.
-- DDL only. Generation remains manual/internal in Step 2 and does not create
-- portal prompts, RMI requests, approval blockers, memo content, or EDD routes.

CREATE TABLE IF NOT EXISTS application_enhanced_requirements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    source_rule_id INTEGER REFERENCES enhanced_requirement_rules(id),
    trigger_key TEXT NOT NULL,
    trigger_label TEXT NOT NULL,
    trigger_category TEXT NOT NULL DEFAULT 'risk',
    requirement_key TEXT NOT NULL,
    requirement_label TEXT NOT NULL,
    requirement_description TEXT,
    audience TEXT NOT NULL DEFAULT 'client' CHECK(audience IN ('client','backoffice','both')),
    requirement_type TEXT NOT NULL DEFAULT 'document' CHECK(requirement_type IN ('document','declaration','review_task','explanation','internal_control')),
    subject_scope TEXT NOT NULL DEFAULT 'application' CHECK(subject_scope IN ('company','ubo','director','controller','application','screening_subject')),
    blocking_approval INTEGER NOT NULL DEFAULT 1 CHECK(blocking_approval IN (0,1)),
    waivable INTEGER NOT NULL DEFAULT 1 CHECK(waivable IN (0,1)),
    waiver_roles TEXT DEFAULT '[]',
    mandatory INTEGER NOT NULL DEFAULT 1 CHECK(mandatory IN (0,1)),
    status TEXT NOT NULL DEFAULT 'generated' CHECK(status IN ('generated','requested','uploaded','under_review','accepted','rejected','waived','cancelled')),
    generation_source TEXT NOT NULL DEFAULT 'manual_api',
    trigger_reason TEXT,
    trigger_context TEXT DEFAULT '{}',
    linked_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
    linked_rmi_item_id TEXT,
    requested_at TEXT,
    requested_by TEXT REFERENCES users(id),
    uploaded_at TEXT,
    reviewed_at TEXT,
    reviewed_by TEXT REFERENCES users(id),
    review_notes TEXT,
    waived_at TEXT,
    waived_by TEXT REFERENCES users(id),
    waiver_reason TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_at TEXT DEFAULT (datetime('now')),
    created_by TEXT REFERENCES users(id),
    updated_at TEXT DEFAULT (datetime('now')),
    updated_by TEXT REFERENCES users(id),
    UNIQUE(application_id, trigger_key, requirement_key)
);

CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_app
    ON application_enhanced_requirements(application_id);
CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_rule
    ON application_enhanced_requirements(source_rule_id);
CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_trigger
    ON application_enhanced_requirements(trigger_key);
CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_status
    ON application_enhanced_requirements(status);
CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_active
    ON application_enhanced_requirements(active);
