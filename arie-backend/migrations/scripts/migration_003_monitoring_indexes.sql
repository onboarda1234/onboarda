-- Migration 003: Monitoring table indexes and improvements

CREATE INDEX IF NOT EXISTS idx_monitoring_alerts_app ON monitoring_alerts(application_id);
CREATE INDEX IF NOT EXISTS idx_monitoring_alerts_status ON monitoring_alerts(status);
CREATE INDEX IF NOT EXISTS idx_monitoring_alerts_severity ON monitoring_alerts(severity);
CREATE INDEX IF NOT EXISTS idx_periodic_reviews_app ON periodic_reviews(application_id);
CREATE INDEX IF NOT EXISTS idx_periodic_reviews_status ON periodic_reviews(status);
CREATE INDEX IF NOT EXISTS idx_periodic_reviews_due ON periodic_reviews(due_date);
CREATE INDEX IF NOT EXISTS idx_client_notifications_app ON client_notifications(application_id);
CREATE INDEX IF NOT EXISTS idx_client_notifications_client ON client_notifications(client_id);
CREATE INDEX IF NOT EXISTS idx_client_sessions_client ON client_sessions(client_id);

-- Add account lockout tracking
CREATE TABLE IF NOT EXISTS account_lockouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    failed_attempts INTEGER DEFAULT 0,
    locked_until TEXT,
    last_attempt TEXT DEFAULT (datetime('now')),
    ip_address TEXT
);
CREATE INDEX IF NOT EXISTS idx_account_lockouts_email ON account_lockouts(email);
