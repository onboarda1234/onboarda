-- Migration 041: Monitoring overdue escalation ledger (M2.1 PR-4)
-- =================================================================
-- Additive metadata ledger for officer-triggered overdue escalations.
--
-- SCOPE: additive only.
--   * No new monitoring_alerts.status values.
--   * No CHECK constraint on monitoring_alerts.
--   * No scheduler, email, provider, Agent, risk, approval, or document flow.
--   * The alert status transition remains the existing escalate_to_sco
--     decision-action path; this table only records overdue-specific context.

CREATE TABLE IF NOT EXISTS monitoring_alert_escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL REFERENCES monitoring_alerts(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    escalated_by TEXT,
    escalated_by_role TEXT,
    escalated_at TEXT DEFAULT (datetime('now')),
    prior_status TEXT,
    new_status TEXT,
    sla_state TEXT,
    days_overdue INTEGER,
    sla_due_at TEXT,
    sla_days INTEGER,
    alert_severity_at_escalation TEXT
);

CREATE INDEX IF NOT EXISTS idx_monitoring_alert_escalations_alert
    ON monitoring_alert_escalations(alert_id);

CREATE INDEX IF NOT EXISTS idx_monitoring_alert_escalations_actor
    ON monitoring_alert_escalations(escalated_by);
