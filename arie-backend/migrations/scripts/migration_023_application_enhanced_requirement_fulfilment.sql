-- Client fulfilment fields for requested Enhanced / EDD requirements.
-- Additive only: no approval gates, memo output, RMI rows, notifications,
-- EDD routes/case stage, screening, risk scoring, or standard KYC upload
-- behaviour changes.

ALTER TABLE application_enhanced_requirements
    ADD COLUMN client_response_text TEXT;

ALTER TABLE application_enhanced_requirements
    ADD COLUMN client_response_at TIMESTAMP;

ALTER TABLE application_enhanced_requirements
    ADD COLUMN client_response_by TEXT REFERENCES clients(id);
