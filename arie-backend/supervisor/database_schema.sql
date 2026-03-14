-- ═══════════════════════════════════════════════════════════════════
-- ARIE Finance — AI Agent Supervisor: PostgreSQL Schema
-- ═══════════════════════════════════════════════════════════════════
-- Purpose: Complete database schema for the Quality Control / AI Agent
--          Supervisor framework. Designed for regulatory auditability,
--          append-only logging, and full traceability.
--
-- Compatible with: PostgreSQL 14+
-- Also includes SQLite-compatible variant comments for dev environments.
--
-- Tables:
--   1.  supervisor_agent_registry      — Agent version catalog
--   2.  supervisor_prompt_registry      — Prompt version catalog
--   3.  supervisor_runs                 — Every agent execution
--   4.  supervisor_run_outputs          — Structured JSON outputs per run
--   5.  supervisor_validation_results   — Schema validation verdicts
--   6.  supervisor_confidence_scores    — Per-run + aggregate confidence
--   7.  supervisor_contradictions       — Cross-agent contradictions
--   8.  supervisor_rule_evaluations     — Rules engine trigger log
--   9.  supervisor_escalations          — Escalation routing decisions
--  10.  supervisor_human_reviews        — Officer review decisions
--  11.  supervisor_overrides            — AI recommendation overrides
--  12.  supervisor_audit_log            — Append-only master audit trail
--  13.  supervisor_agent_metrics        — Rolling quality metrics
--  14.  supervisor_case_aggregates      — Case-level aggregate scores
--  15.  supervisor_rules_config         — Configurable compliance rules
-- ═══════════════════════════════════════════════════════════════════

-- Enable UUID generation (PostgreSQL)
-- CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ───────────────────────────────────────────────────────────
-- 1. AGENT REGISTRY — Version-controlled agent catalog
-- ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supervisor_agent_registry (
    id              TEXT PRIMARY KEY,                          -- e.g. "agent_identity_doc_v2.1"
    agent_number    INTEGER NOT NULL,                          -- 1-10 matching the 10 agents
    agent_name      TEXT NOT NULL,                             -- Human-readable name
    agent_type      TEXT NOT NULL CHECK(agent_type IN (
        'identity_document_integrity',
        'external_database_verification',
        'corporate_structure_ubo',
        'business_model_plausibility',
        'fincrime_screening',
        'compliance_memo_risk',
        'periodic_review_preparation',
        'adverse_media_pep_monitoring',
        'behaviour_risk_drift',
        'ongoing_compliance_review'
    )),
    version         TEXT NOT NULL,                             -- Semantic version "2.1.0"
    model_name      TEXT NOT NULL,                             -- e.g. "claude-sonnet-4-20250514"
    prompt_version  TEXT NOT NULL,                             -- Reference to prompt_registry
    description     TEXT,
    config_json     TEXT DEFAULT '{}',                         -- Agent-specific configuration
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now')),
    deprecated_at   TEXT,
    deprecated_by   TEXT,
    UNIQUE(agent_number, version)
);

-- ───────────────────────────────────────────────────────────
-- 2. PROMPT REGISTRY — Version-controlled prompt catalog
-- ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supervisor_prompt_registry (
    id              TEXT PRIMARY KEY,                          -- e.g. "prompt_identity_v3.2"
    agent_type      TEXT NOT NULL,
    version         TEXT NOT NULL,
    prompt_hash     TEXT NOT NULL,                             -- SHA-256 of prompt text
    prompt_text     TEXT NOT NULL,                             -- Full prompt (for audit)
    system_prompt   TEXT,                                      -- System prompt if separate
    parameters_json TEXT DEFAULT '{}',                         -- Temperature, max_tokens, etc.
    created_at      TEXT DEFAULT (datetime('now')),
    created_by      TEXT,
    change_reason   TEXT,
    is_active       INTEGER DEFAULT 1,
    UNIQUE(agent_type, version)
);

-- ───────────────────────────────────────────────────────────
-- 3. SUPERVISOR RUNS — Every agent execution record
-- ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supervisor_runs (
    id              TEXT PRIMARY KEY,                          -- UUID run_id
    application_id  TEXT NOT NULL,                             -- FK to applications
    pipeline_id     TEXT NOT NULL,                             -- Groups runs in same pipeline
    agent_id        TEXT NOT NULL REFERENCES supervisor_agent_registry(id),
    agent_name      TEXT NOT NULL,
    agent_type      TEXT NOT NULL,
    agent_version   TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    model_name      TEXT NOT NULL,
    trigger_type    TEXT NOT NULL CHECK(trigger_type IN (
        'onboarding', 'periodic_review', 'monitoring_alert',
        'manual_trigger', 'rerun', 'qa_test'
    )),
    trigger_source  TEXT,                                      -- Who/what initiated
    status          TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending', 'running', 'completed', 'failed',
        'timeout', 'quarantined', 'skipped'
    )),
    input_hash      TEXT,                                      -- SHA-256 of input data
    input_summary   TEXT,                                      -- Brief description of input
    output_json     TEXT,                                      -- Raw agent output
    error_message   TEXT,                                      -- If failed
    error_type      TEXT,                                      -- Classification of error
    runtime_ms      INTEGER,                                   -- Execution time
    token_count     INTEGER,                                   -- LLM tokens used
    retry_count     INTEGER DEFAULT 0,
    max_retries     INTEGER DEFAULT 2,
    started_at      TEXT,
    completed_at    TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_supervisor_runs_app
    ON supervisor_runs(application_id);
CREATE INDEX IF NOT EXISTS idx_supervisor_runs_pipeline
    ON supervisor_runs(pipeline_id);
CREATE INDEX IF NOT EXISTS idx_supervisor_runs_status
    ON supervisor_runs(status);
CREATE INDEX IF NOT EXISTS idx_supervisor_runs_agent_type
    ON supervisor_runs(agent_type);

-- ───────────────────────────────────────────────────────────
-- 4. RUN OUTPUTS — Structured validated outputs
-- ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supervisor_run_outputs (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES supervisor_runs(id),
    agent_type      TEXT NOT NULL,
    application_id  TEXT NOT NULL,
    confidence_score REAL,                                     -- 0.0 to 1.0
    status          TEXT NOT NULL CHECK(status IN (
        'clean', 'issues_found', 'inconclusive', 'error', 'partial'
    )),
    findings_json   TEXT NOT NULL DEFAULT '[]',                -- Array of finding objects
    evidence_json   TEXT NOT NULL DEFAULT '[]',                -- Supporting evidence
    issues_json     TEXT NOT NULL DEFAULT '[]',                -- Detected issues
    recommendation  TEXT,                                      -- Agent recommendation
    risk_indicators TEXT DEFAULT '[]',                         -- Risk flags
    escalation_flag INTEGER DEFAULT 0,
    escalation_reason TEXT,
    metadata_json   TEXT DEFAULT '{}',                         -- Extra agent-specific data
    validated       INTEGER DEFAULT 0,                         -- Schema validation passed
    quarantined     INTEGER DEFAULT 0,                         -- Output quarantined
    quarantine_reason TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_run_outputs_run
    ON supervisor_run_outputs(run_id);
CREATE INDEX IF NOT EXISTS idx_run_outputs_app
    ON supervisor_run_outputs(application_id);

-- ───────────────────────────────────────────────────────────
-- 5. VALIDATION RESULTS — Schema validation verdicts
-- ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supervisor_validation_results (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES supervisor_runs(id),
    output_id       TEXT REFERENCES supervisor_run_outputs(id),
    agent_type      TEXT NOT NULL,
    application_id  TEXT NOT NULL,
    is_valid        INTEGER NOT NULL,                          -- 1 = passed, 0 = failed
    validation_errors TEXT DEFAULT '[]',                       -- Array of error objects
    missing_fields  TEXT DEFAULT '[]',                         -- Missing required fields
    type_errors     TEXT DEFAULT '[]',                         -- Type mismatch errors
    constraint_violations TEXT DEFAULT '[]',                   -- Business constraint violations
    warnings        TEXT DEFAULT '[]',                         -- Non-blocking warnings
    schema_version  TEXT NOT NULL,
    validator_version TEXT NOT NULL,
    validated_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_validation_run
    ON supervisor_validation_results(run_id);

-- ───────────────────────────────────────────────────────────
-- 6. CONFIDENCE SCORES — Per-run + aggregate tracking
-- ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supervisor_confidence_scores (
    id              TEXT PRIMARY KEY,
    run_id          TEXT REFERENCES supervisor_runs(id),
    pipeline_id     TEXT,
    application_id  TEXT NOT NULL,
    agent_type      TEXT,                                      -- NULL for aggregate
    score_type      TEXT NOT NULL CHECK(score_type IN (
        'agent_output',                                        -- Single agent score
        'case_aggregate',                                      -- Weighted case-level
        'agent_rolling_avg',                                   -- Rolling avg per agent
        'pipeline_aggregate'                                   -- Full pipeline score
    )),
    confidence_score REAL NOT NULL CHECK(
        confidence_score >= 0.0 AND confidence_score <= 1.0
    ),
    routing_decision TEXT CHECK(routing_decision IN (
        'normal',                                              -- > 0.85
        'human_review',                                        -- 0.65 - 0.85
        'mandatory_escalation'                                 -- < 0.65
    )),
    component_scores TEXT DEFAULT '{}',                        -- Breakdown by sub-check
    calculation_method TEXT,                                   -- How score was calculated
    window_size     INTEGER,                                   -- For rolling averages
    calculated_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_confidence_app
    ON supervisor_confidence_scores(application_id);
CREATE INDEX IF NOT EXISTS idx_confidence_routing
    ON supervisor_confidence_scores(routing_decision);

-- ───────────────────────────────────────────────────────────
-- 7. CONTRADICTIONS — Cross-agent contradiction records
-- ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supervisor_contradictions (
    id              TEXT PRIMARY KEY,
    pipeline_id     TEXT NOT NULL,
    application_id  TEXT NOT NULL,
    contradiction_type TEXT NOT NULL,                          -- Taxonomy code
    contradiction_category TEXT NOT NULL CHECK(contradiction_category IN (
        'identity_vs_registry',                                -- Doc integrity vs registry
        'ubo_vs_risk',                                         -- UBO mapping vs risk recommendation
        'screening_vs_plausibility',                           -- Screening vs business model
        'registry_vs_memo',                                    -- Registry vs compliance memo
        'document_vs_identity',                                -- Document vs identity check
        'monitoring_vs_onboarding',                            -- Ongoing monitoring vs initial data
        'risk_level_mismatch',                                 -- Agents disagree on risk level
        'temporal_inconsistency',                              -- Time-based contradictions
        'data_completeness_conflict',                          -- One says complete, another says missing
        'other'
    )),
    severity        TEXT NOT NULL CHECK(severity IN (
        'critical', 'high', 'medium', 'low'
    )),
    severity_score  REAL NOT NULL CHECK(
        severity_score >= 0.0 AND severity_score <= 1.0
    ),
    agent_a_run_id  TEXT NOT NULL REFERENCES supervisor_runs(id),
    agent_a_type    TEXT NOT NULL,
    agent_a_finding TEXT NOT NULL,                             -- What agent A said
    agent_b_run_id  TEXT NOT NULL REFERENCES supervisor_runs(id),
    agent_b_type    TEXT NOT NULL,
    agent_b_finding TEXT NOT NULL,                             -- What agent B said
    description     TEXT NOT NULL,                             -- Human-readable description
    resolution_required INTEGER DEFAULT 1,
    resolution_status TEXT DEFAULT 'open' CHECK(resolution_status IN (
        'open', 'under_review', 'resolved', 'dismissed', 'escalated'
    )),
    resolved_by     TEXT,
    resolution_notes TEXT,
    resolved_at     TEXT,
    detected_at     TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_contradictions_app
    ON supervisor_contradictions(application_id);
CREATE INDEX IF NOT EXISTS idx_contradictions_severity
    ON supervisor_contradictions(severity);
CREATE INDEX IF NOT EXISTS idx_contradictions_status
    ON supervisor_contradictions(resolution_status);

-- ───────────────────────────────────────────────────────────
-- 8. RULE EVALUATIONS — Rules engine trigger log
-- ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supervisor_rule_evaluations (
    id              TEXT PRIMARY KEY,
    pipeline_id     TEXT NOT NULL,
    application_id  TEXT NOT NULL,
    run_id          TEXT REFERENCES supervisor_runs(id),
    rule_id         TEXT NOT NULL REFERENCES supervisor_rules_config(id),
    rule_name       TEXT NOT NULL,
    rule_category   TEXT NOT NULL,
    triggered       INTEGER NOT NULL,                          -- 1 = triggered, 0 = not
    trigger_data    TEXT,                                      -- Data that triggered the rule
    action_taken    TEXT CHECK(action_taken IN (
        'escalate', 'block_approval', 'require_review',
        'flag_warning', 'reject', 'hold', 'no_action'
    )),
    overrides_ai    INTEGER DEFAULT 0,                         -- Did this override AI?
    ai_recommendation TEXT,                                    -- What AI originally said
    rule_recommendation TEXT,                                  -- What the rule says
    severity        TEXT CHECK(severity IN (
        'critical', 'high', 'medium', 'low', 'info'
    )),
    evaluated_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rule_evals_app
    ON supervisor_rule_evaluations(application_id);
CREATE INDEX IF NOT EXISTS idx_rule_evals_triggered
    ON supervisor_rule_evaluations(triggered);

-- ───────────────────────────────────────────────────────────
-- 9. ESCALATIONS — Escalation routing decisions
-- ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supervisor_escalations (
    id              TEXT PRIMARY KEY,
    pipeline_id     TEXT NOT NULL,
    application_id  TEXT NOT NULL,
    escalation_source TEXT NOT NULL CHECK(escalation_source IN (
        'low_confidence', 'contradiction', 'rule_trigger',
        'agent_failure', 'manual', 'timeout', 'validation_failure'
    )),
    source_id       TEXT,                                      -- FK to source record
    escalation_level TEXT NOT NULL CHECK(escalation_level IN (
        'compliance_officer',                                  -- Standard CO review
        'senior_compliance',                                   -- Senior CO / team lead
        'mlro',                                                -- Money Laundering Reporting Officer
        'management'                                           -- C-level / board
    )),
    priority        TEXT NOT NULL CHECK(priority IN (
        'critical', 'high', 'medium', 'low'
    )),
    reason          TEXT NOT NULL,
    context_json    TEXT DEFAULT '{}',                         -- Supporting context
    assigned_to     TEXT,                                      -- Assigned officer user_id
    status          TEXT DEFAULT 'pending' CHECK(status IN (
        'pending', 'assigned', 'in_review', 'resolved', 'expired'
    )),
    sla_deadline    TEXT,                                      -- Review SLA
    resolved_at     TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_escalations_app
    ON supervisor_escalations(application_id);
CREATE INDEX IF NOT EXISTS idx_escalations_status
    ON supervisor_escalations(status);
CREATE INDEX IF NOT EXISTS idx_escalations_level
    ON supervisor_escalations(escalation_level);

-- ───────────────────────────────────────────────────────────
-- 10. HUMAN REVIEWS — Officer review decisions
-- ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supervisor_human_reviews (
    id              TEXT PRIMARY KEY,
    pipeline_id     TEXT NOT NULL,
    application_id  TEXT NOT NULL,
    escalation_id   TEXT REFERENCES supervisor_escalations(id),
    review_type     TEXT NOT NULL CHECK(review_type IN (
        'onboarding_decision',
        'periodic_review',
        'monitoring_alert',
        'contradiction_resolution',
        'escalation_review',
        'quality_review'
    )),
    reviewer_id     TEXT NOT NULL,
    reviewer_name   TEXT NOT NULL,
    reviewer_role   TEXT NOT NULL,
    -- What the AI recommended
    ai_recommendation TEXT,
    ai_confidence   REAL,
    ai_risk_level   TEXT,
    -- What the rules engine said
    rules_recommendation TEXT,
    rules_triggered TEXT DEFAULT '[]',
    -- Contradictions present
    contradictions_json TEXT DEFAULT '[]',
    -- Officer decision
    decision        TEXT NOT NULL CHECK(decision IN (
        'approve', 'reject', 'request_information',
        'escalate', 'enhanced_monitoring', 'exit_relationship',
        'defer'
    )),
    decision_reason TEXT NOT NULL,
    risk_level_assigned TEXT,
    conditions      TEXT,                                      -- Conditions on approval
    follow_up_required INTEGER DEFAULT 0,
    follow_up_details TEXT,
    -- Override tracking
    is_ai_override  INTEGER DEFAULT 0,
    override_reason TEXT,
    -- Timestamps
    review_started_at TEXT,
    decision_at     TEXT DEFAULT (datetime('now')),
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_human_reviews_app
    ON supervisor_human_reviews(application_id);
CREATE INDEX IF NOT EXISTS idx_human_reviews_reviewer
    ON supervisor_human_reviews(reviewer_id);
CREATE INDEX IF NOT EXISTS idx_human_reviews_decision
    ON supervisor_human_reviews(decision);

-- ───────────────────────────────────────────────────────────
-- 11. OVERRIDES — Dedicated AI override tracking
-- ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supervisor_overrides (
    id              TEXT PRIMARY KEY,
    review_id       TEXT NOT NULL REFERENCES supervisor_human_reviews(id),
    application_id  TEXT NOT NULL,
    agent_type      TEXT,                                      -- Which agent was overridden
    override_type   TEXT NOT NULL CHECK(override_type IN (
        'risk_level_change',
        'approval_despite_escalation',
        'rejection_despite_approval',
        'confidence_override',
        'rule_exception',
        'contradiction_dismissal'
    )),
    original_value  TEXT NOT NULL,                             -- What AI/rules said
    override_value  TEXT NOT NULL,                             -- What officer decided
    reason          TEXT NOT NULL,
    officer_id      TEXT NOT NULL,
    officer_name    TEXT NOT NULL,
    officer_role    TEXT NOT NULL,
    approver_id     TEXT,                                      -- If second-level approval needed
    approver_name   TEXT,
    approved_at     TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_overrides_app
    ON supervisor_overrides(application_id);
CREATE INDEX IF NOT EXISTS idx_overrides_officer
    ON supervisor_overrides(officer_id);

-- ───────────────────────────────────────────────────────────
-- 12. AUDIT LOG — Append-only master audit trail
-- ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supervisor_audit_log (
    id              TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL DEFAULT (datetime('now')),
    event_type      TEXT NOT NULL CHECK(event_type IN (
        'agent_run_started',
        'agent_run_completed',
        'agent_run_failed',
        'schema_validation_passed',
        'schema_validation_failed',
        'confidence_calculated',
        'confidence_routing',
        'contradiction_detected',
        'contradiction_resolved',
        'rule_triggered',
        'rule_overridden',
        'escalation_created',
        'escalation_assigned',
        'escalation_resolved',
        'human_review_started',
        'human_review_completed',
        'ai_override',
        'pipeline_started',
        'pipeline_completed',
        'pipeline_failed',
        'config_changed',
        'agent_version_changed',
        'prompt_version_changed',
        'system_error'
    )),
    severity        TEXT DEFAULT 'info' CHECK(severity IN (
        'critical', 'error', 'warning', 'info', 'debug'
    )),
    pipeline_id     TEXT,
    application_id  TEXT,
    run_id          TEXT,
    agent_type      TEXT,
    actor_type      TEXT CHECK(actor_type IN (
        'system', 'agent', 'officer', 'admin', 'scheduler'
    )),
    actor_id        TEXT,
    actor_name      TEXT,
    actor_role      TEXT,
    action          TEXT NOT NULL,
    detail          TEXT,                                      -- Human-readable description
    data_json       TEXT DEFAULT '{}',                         -- Structured event data
    ip_address      TEXT,
    session_id      TEXT,
    -- Integrity
    previous_hash   TEXT,                                      -- Hash chain for tamper detection
    entry_hash      TEXT                                       -- SHA-256 of this entry
);

-- Audit log should be append-only. In production, enforce via:
-- - Database triggers preventing UPDATE/DELETE
-- - Application-level write-only access
-- - Periodic hash chain verification

CREATE INDEX IF NOT EXISTS idx_audit_timestamp
    ON supervisor_audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_event_type
    ON supervisor_audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_app
    ON supervisor_audit_log(application_id);
CREATE INDEX IF NOT EXISTS idx_audit_pipeline
    ON supervisor_audit_log(pipeline_id);
CREATE INDEX IF NOT EXISTS idx_audit_actor
    ON supervisor_audit_log(actor_id);

-- ───────────────────────────────────────────────────────────
-- 13. AGENT METRICS — Rolling quality metrics per agent
-- ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supervisor_agent_metrics (
    id              TEXT PRIMARY KEY,
    agent_type      TEXT NOT NULL,
    agent_version   TEXT NOT NULL,
    period_start    TEXT NOT NULL,                             -- Start of measurement window
    period_end      TEXT NOT NULL,
    period_type     TEXT NOT NULL CHECK(period_type IN (
        'hourly', 'daily', 'weekly', 'monthly'
    )),
    total_runs      INTEGER DEFAULT 0,
    successful_runs INTEGER DEFAULT 0,
    failed_runs     INTEGER DEFAULT 0,
    timeout_runs    INTEGER DEFAULT 0,
    quarantined_runs INTEGER DEFAULT 0,
    validation_pass_rate REAL,                                 -- 0.0 to 1.0
    avg_confidence  REAL,
    min_confidence  REAL,
    max_confidence  REAL,
    stddev_confidence REAL,
    avg_runtime_ms  REAL,
    p95_runtime_ms  REAL,
    p99_runtime_ms  REAL,
    escalation_rate REAL,                                      -- % escalated
    override_rate   REAL,                                      -- % overridden by humans
    contradiction_rate REAL,                                   -- % involved in contradictions
    false_positive_rate REAL,                                  -- Requires labelled data
    false_negative_rate REAL,                                  -- Requires labelled data
    avg_token_count REAL,
    total_tokens    INTEGER DEFAULT 0,
    calculated_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_metrics_agent
    ON supervisor_agent_metrics(agent_type, period_type);

-- ───────────────────────────────────────────────────────────
-- 14. CASE AGGREGATES — Case-level summary scores
-- ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supervisor_case_aggregates (
    id              TEXT PRIMARY KEY,
    pipeline_id     TEXT NOT NULL,
    application_id  TEXT NOT NULL,
    total_agents_run INTEGER DEFAULT 0,
    successful_agents INTEGER DEFAULT 0,
    failed_agents   INTEGER DEFAULT 0,
    aggregate_confidence REAL,
    min_agent_confidence REAL,
    max_agent_confidence REAL,
    confidence_routing TEXT,                                   -- normal/human_review/mandatory_escalation
    total_contradictions INTEGER DEFAULT 0,
    critical_contradictions INTEGER DEFAULT 0,
    total_rules_triggered INTEGER DEFAULT 0,
    blocking_rules_triggered INTEGER DEFAULT 0,
    escalation_required INTEGER DEFAULT 0,
    escalation_level TEXT,
    ai_recommendation TEXT,                                    -- Aggregate AI recommendation
    ai_risk_level   TEXT,
    pipeline_status TEXT DEFAULT 'running' CHECK(pipeline_status IN (
        'running', 'completed', 'failed',
        'awaiting_review', 'reviewed', 'quarantined'
    )),
    started_at      TEXT,
    completed_at    TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_case_agg_app
    ON supervisor_case_aggregates(application_id);
CREATE INDEX IF NOT EXISTS idx_case_agg_status
    ON supervisor_case_aggregates(pipeline_status);

-- ───────────────────────────────────────────────────────────
-- 15. RULES CONFIG — Configurable compliance rules
-- ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supervisor_rules_config (
    id              TEXT PRIMARY KEY,
    rule_name       TEXT NOT NULL UNIQUE,
    rule_category   TEXT NOT NULL CHECK(rule_category IN (
        'sanctions', 'pep', 'ubo', 'registry',
        'document_integrity', 'jurisdiction',
        'risk_level', 'data_completeness',
        'regulatory', 'custom'
    )),
    description     TEXT NOT NULL,
    condition_json  TEXT NOT NULL,                             -- Machine-readable condition
    action          TEXT NOT NULL CHECK(action IN (
        'escalate', 'block_approval', 'require_review',
        'flag_warning', 'reject', 'hold', 'no_action'
    )),
    severity        TEXT NOT NULL CHECK(severity IN (
        'critical', 'high', 'medium', 'low', 'info'
    )),
    overrides_ai    INTEGER DEFAULT 0,                        -- Can override AI recommendation
    applies_to      TEXT DEFAULT '["all"]',                   -- Agent types this applies to
    is_active       INTEGER DEFAULT 1,
    priority        INTEGER DEFAULT 100,                      -- Lower = higher priority
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    created_by      TEXT,
    updated_by      TEXT
);

-- ───────────────────────────────────────────────────────────
-- SEED: Default compliance rules
-- ───────────────────────────────────────────────────────────
INSERT OR IGNORE INTO supervisor_rules_config (id, rule_name, rule_category, description, condition_json, action, severity, overrides_ai, priority) VALUES
    ('rule_sanctions_hit', 'sanctions_hit_auto_escalate',
     'sanctions', 'Any sanctions match triggers automatic escalation to MLRO',
     '{"field": "findings", "contains": "sanctions_match", "confidence_min": 0.7}',
     'escalate', 'critical', 1, 10),

    ('rule_confirmed_pep', 'confirmed_pep_enhanced_review',
     'pep', 'Confirmed PEP status requires enhanced due diligence review',
     '{"field": "findings", "contains": "pep_confirmed", "confidence_min": 0.8}',
     'require_review', 'high', 1, 20),

    ('rule_missing_ubo', 'missing_ubo_block_approval',
     'ubo', 'Missing ultimate beneficial owner identification blocks approval',
     '{"field": "issues", "contains": "ubo_not_identified"}',
     'block_approval', 'critical', 1, 15),

    ('rule_company_not_found', 'company_not_in_registry',
     'registry', 'Company not found in official registry requires hold for clarification',
     '{"field": "findings", "contains": "company_not_found"}',
     'hold', 'high', 1, 25),

    ('rule_doc_tampering', 'document_tampering_detected',
     'document_integrity', 'Document tampering signals trigger rejection or escalation',
     '{"field": "issues", "contains": "tampering_detected", "confidence_min": 0.75}',
     'reject', 'critical', 1, 5),

    ('rule_high_risk_jurisdiction', 'high_risk_jurisdiction_review',
     'jurisdiction', 'Exposure to high-risk jurisdictions requires mandatory review',
     '{"field": "risk_indicators", "contains": "high_risk_jurisdiction"}',
     'require_review', 'high', 1, 30),

    ('rule_directors_mismatch', 'directors_registry_mismatch',
     'registry', 'Directors listed do not match official registry records',
     '{"field": "issues", "contains": "directors_mismatch"}',
     'hold', 'high', 1, 35),

    ('rule_expired_documents', 'expired_identity_documents',
     'document_integrity', 'Expired identity or incorporation documents',
     '{"field": "issues", "contains": "document_expired"}',
     'flag_warning', 'medium', 0, 50),

    ('rule_shell_company_indicators', 'shell_company_risk',
     'risk_level', 'Business model analysis indicates shell company characteristics',
     '{"field": "risk_indicators", "contains": "shell_company_indicators"}',
     'escalate', 'critical', 1, 12),

    ('rule_adverse_media_severe', 'severe_adverse_media',
     'sanctions', 'Severe adverse media findings require immediate escalation',
     '{"field": "findings", "contains": "adverse_media_severe"}',
     'escalate', 'critical', 1, 8);

-- ───────────────────────────────────────────────────────────
-- VIEWS: Governance reporting
-- ───────────────────────────────────────────────────────────

-- Active contradictions requiring resolution
CREATE VIEW IF NOT EXISTS v_open_contradictions AS
SELECT
    c.*,
    ra.agent_name AS agent_a_name,
    rb.agent_name AS agent_b_name
FROM supervisor_contradictions c
LEFT JOIN supervisor_runs ra ON c.agent_a_run_id = ra.id
LEFT JOIN supervisor_runs rb ON c.agent_b_run_id = rb.id
WHERE c.resolution_status IN ('open', 'under_review')
ORDER BY
    CASE c.severity
        WHEN 'critical' THEN 1
        WHEN 'high' THEN 2
        WHEN 'medium' THEN 3
        WHEN 'low' THEN 4
    END;

-- Override audit report
CREATE VIEW IF NOT EXISTS v_override_audit AS
SELECT
    o.*,
    hr.decision,
    hr.ai_recommendation,
    hr.ai_confidence,
    hr.review_type
FROM supervisor_overrides o
JOIN supervisor_human_reviews hr ON o.review_id = hr.id
ORDER BY o.created_at DESC;

-- Agent performance dashboard view
CREATE VIEW IF NOT EXISTS v_agent_performance AS
SELECT
    agent_type,
    agent_version,
    period_type,
    period_start,
    total_runs,
    successful_runs,
    validation_pass_rate,
    avg_confidence,
    escalation_rate,
    override_rate,
    contradiction_rate,
    avg_runtime_ms
FROM supervisor_agent_metrics
WHERE period_type = 'daily'
ORDER BY period_start DESC, agent_type;
