-- CA-1: ComplyAdvantage webhook idempotency and structured monitoring evidence.

CREATE TABLE IF NOT EXISTS complyadvantage_webhook_deliveries (
    webhook_id TEXT PRIMARY KEY,
    first_received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    webhook_type TEXT,
    case_identifier TEXT,
    customer_identifier TEXT,
    processing_status TEXT NOT NULL DEFAULT 'processing',
    processing_result TEXT,
    failure_reason TEXT,
    trace_id TEXT,
    processed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ca_webhook_deliveries_status
    ON complyadvantage_webhook_deliveries(processing_status);

CREATE INDEX IF NOT EXISTS idx_ca_webhook_deliveries_case
    ON complyadvantage_webhook_deliveries(case_identifier);

CREATE TABLE IF NOT EXISTS monitoring_alert_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monitoring_alert_id INTEGER NOT NULL,
    application_id TEXT,
    provider TEXT NOT NULL,
    case_identifier TEXT,
    alert_identifier TEXT,
    match_identifier TEXT,
    risk_identifier TEXT,
    profile_identifier TEXT,
    evidence_type TEXT,
    matched_subject_name TEXT,
    relationship_to_client TEXT,
    match_category TEXT,
    risk_indicator TEXT,
    match_confidence TEXT,
    source_title TEXT,
    source_name TEXT,
    source_url TEXT,
    source_url_available BOOLEAN DEFAULT false,
    source_url_unavailable_reason TEXT,
    publication_date TEXT,
    snippet TEXT,
    provider_case_url TEXT,
    evidence_json TEXT,
    raw_provider_reference TEXT,
    evidence_status TEXT DEFAULT 'fetched',
    evidence_hash TEXT NOT NULL,
    fetched_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(monitoring_alert_id, evidence_hash)
);

CREATE INDEX IF NOT EXISTS idx_monitoring_alert_evidence_alert
    ON monitoring_alert_evidence(monitoring_alert_id);

CREATE INDEX IF NOT EXISTS idx_monitoring_alert_evidence_case
    ON monitoring_alert_evidence(provider, case_identifier);
