-- Monitoring Sprint 3: link document expiry refresh requests to monitoring alerts.

ALTER TABLE application_enhanced_requirements
    ADD COLUMN monitoring_alert_id INTEGER;

ALTER TABLE application_enhanced_requirements
    ADD COLUMN monitoring_document_id TEXT;

ALTER TABLE application_enhanced_requirements
    ADD COLUMN due_date TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_monitoring_alert
    ON application_enhanced_requirements(monitoring_alert_id);

CREATE INDEX IF NOT EXISTS idx_app_enhanced_req_monitoring_doc
    ON application_enhanced_requirements(monitoring_document_id);
