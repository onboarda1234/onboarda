-- Enhanced / EDD requirement settings foundation.
-- Idempotent DDL only; default rows are inserted by db.py startup seed logic
-- so existing customized rules are never overwritten by this migration.

CREATE TABLE IF NOT EXISTS enhanced_requirement_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    sort_order INTEGER NOT NULL DEFAULT 100,
    applies_when TEXT DEFAULT '{}',
    client_safe_label TEXT,
    client_safe_description TEXT,
    internal_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT REFERENCES users(id),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT REFERENCES users(id),
    UNIQUE(trigger_key, requirement_key)
);

CREATE INDEX IF NOT EXISTS idx_enhanced_req_trigger
    ON enhanced_requirement_rules(trigger_key);
CREATE INDEX IF NOT EXISTS idx_enhanced_req_active
    ON enhanced_requirement_rules(active);
CREATE INDEX IF NOT EXISTS idx_enhanced_req_audience
    ON enhanced_requirement_rules(audience);
