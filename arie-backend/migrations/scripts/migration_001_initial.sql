-- Migration 001: Mark initial schema as applied
-- The initial tables are created by init_db() in server.py.
-- This migration exists as a baseline marker.

-- Add indexes for common queries
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_client_id ON applications(client_id);
CREATE INDEX IF NOT EXISTS idx_applications_ref ON applications(ref);
CREATE INDEX IF NOT EXISTS idx_applications_risk_level ON applications(risk_level);
CREATE INDEX IF NOT EXISTS idx_directors_application_id ON directors(application_id);
CREATE INDEX IF NOT EXISTS idx_ubos_application_id ON ubos(application_id);
CREATE INDEX IF NOT EXISTS idx_documents_application_id ON documents(application_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id);
