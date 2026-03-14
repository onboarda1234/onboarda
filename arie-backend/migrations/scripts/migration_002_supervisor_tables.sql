-- Migration 002: Supervisor framework tables

CREATE TABLE IF NOT EXISTS supervisor_runs (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    trigger_source TEXT,
    status TEXT DEFAULT 'running',
    started_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    agent_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    aggregate_confidence REAL,
    routing_decision TEXT,
    needs_human_review INTEGER DEFAULT 0,
    context_data TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS supervisor_run_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES supervisor_runs(id),
    agent_type TEXT NOT NULL,
    agent_version TEXT DEFAULT '1.0.0',
    status TEXT DEFAULT 'pending',
    raw_output TEXT DEFAULT '{}',
    validated_output TEXT DEFAULT '{}',
    confidence_score REAL,
    execution_time_ms INTEGER,
    error_message TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS supervisor_validation_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    agent_type TEXT NOT NULL,
    is_valid INTEGER DEFAULT 1,
    errors TEXT DEFAULT '[]',
    warnings TEXT DEFAULT '[]',
    validated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS supervisor_contradictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    application_id TEXT NOT NULL,
    category TEXT NOT NULL,
    agent_a TEXT NOT NULL,
    finding_a TEXT,
    agent_b TEXT NOT NULL,
    finding_b TEXT,
    severity TEXT DEFAULT 'medium',
    severity_score REAL DEFAULT 0.5,
    description TEXT,
    resolved INTEGER DEFAULT 0,
    resolved_by TEXT,
    resolved_at TEXT,
    detected_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS supervisor_rule_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    application_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    triggered INTEGER DEFAULT 0,
    action_taken TEXT,
    details TEXT DEFAULT '{}',
    evaluated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS supervisor_escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL,
    pipeline_id TEXT NOT NULL,
    escalation_level TEXT NOT NULL,
    reason TEXT NOT NULL,
    escalated_by TEXT,
    escalated_by_role TEXT,
    assigned_to TEXT,
    status TEXT DEFAULT 'pending',
    resolved_at TEXT,
    resolved_by TEXT,
    resolution_notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS supervisor_human_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id TEXT NOT NULL,
    application_id TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    reviewer_name TEXT,
    reviewer_role TEXT,
    decision TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    risk_level_assigned TEXT,
    conditions TEXT,
    follow_up_required INTEGER DEFAULT 0,
    follow_up_details TEXT,
    override_ai INTEGER DEFAULT 0,
    override_reason TEXT,
    reviewed_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS supervisor_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id TEXT NOT NULL,
    application_id TEXT NOT NULL,
    override_type TEXT NOT NULL,
    ai_recommendation TEXT,
    officer_decision TEXT,
    officer_id TEXT NOT NULL,
    officer_name TEXT,
    reason TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS supervisor_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT (datetime('now')),
    event_type TEXT NOT NULL,
    application_id TEXT,
    pipeline_id TEXT,
    agent_type TEXT,
    actor TEXT,
    action TEXT NOT NULL,
    details TEXT DEFAULT '{}',
    prev_hash TEXT,
    entry_hash TEXT
);

CREATE TABLE IF NOT EXISTS supervisor_rules_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT UNIQUE NOT NULL,
    rule_name TEXT NOT NULL,
    rule_category TEXT,
    description TEXT,
    condition_field TEXT,
    condition_operator TEXT,
    condition_value TEXT,
    action TEXT DEFAULT 'escalate',
    severity TEXT DEFAULT 'high',
    overrides_ai INTEGER DEFAULT 0,
    priority INTEGER DEFAULT 50,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_supervisor_runs_app ON supervisor_runs(application_id);
CREATE INDEX IF NOT EXISTS idx_supervisor_runs_status ON supervisor_runs(status);
CREATE INDEX IF NOT EXISTS idx_supervisor_outputs_run ON supervisor_run_outputs(run_id);
CREATE INDEX IF NOT EXISTS idx_supervisor_contradictions_run ON supervisor_contradictions(run_id);
CREATE INDEX IF NOT EXISTS idx_supervisor_escalations_app ON supervisor_escalations(application_id);
CREATE INDEX IF NOT EXISTS idx_supervisor_reviews_pipeline ON supervisor_human_reviews(pipeline_id);
CREATE INDEX IF NOT EXISTS idx_supervisor_audit_app ON supervisor_audit_log(application_id);

-- Seed default compliance rules
INSERT OR IGNORE INTO supervisor_rules_config (rule_id, rule_name, rule_category, description, condition_field, condition_operator, condition_value, action, severity, overrides_ai, priority)
VALUES
    ('sanctions_hit', 'Sanctions Match Detected', 'screening', 'Any confirmed sanctions list match', 'screening.sanctions_matched', 'equals', 'true', 'reject', 'critical', 1, 10),
    ('confirmed_pep', 'Confirmed PEP Status', 'screening', 'Person confirmed as Politically Exposed', 'screening.pep_confirmed', 'equals', 'true', 'escalate', 'high', 1, 20),
    ('missing_ubo', 'Missing UBO Information', 'structure', 'UBO identification incomplete', 'ubo.completeness', 'less_than', '0.8', 'block_approval', 'high', 1, 30),
    ('company_not_found', 'Company Not in Registry', 'registry', 'Company not found in corporate registry', 'registry.found', 'equals', 'false', 'escalate', 'high', 0, 40),
    ('doc_tampering', 'Document Tampering Suspected', 'documents', 'AI detected potential document manipulation', 'documents.tampering_detected', 'equals', 'true', 'reject', 'critical', 1, 15),
    ('high_risk_jurisdiction', 'High-Risk Jurisdiction', 'geographic', 'Entity in FATF grey/blacklisted country', 'geography.risk_level', 'equals', 'VERY_HIGH', 'escalate', 'high', 0, 35),
    ('directors_mismatch', 'Directors Mismatch', 'registry', 'Declared directors dont match registry', 'registry.directors_match', 'equals', 'false', 'escalate', 'medium', 0, 45),
    ('expired_documents', 'Expired Documents', 'documents', 'One or more submitted documents have expired', 'documents.has_expired', 'equals', 'true', 'block_approval', 'medium', 1, 50),
    ('shell_company', 'Shell Company Indicators', 'structure', 'Multiple shell company indicators detected', 'structure.shell_indicators', 'greater_than', '2', 'reject', 'critical', 1, 12),
    ('adverse_media_severe', 'Severe Adverse Media', 'screening', 'Severe adverse media findings on key persons', 'screening.adverse_severity', 'equals', 'severe', 'escalate', 'high', 0, 25);
