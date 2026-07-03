-- Migration 038: Monitoring Alert officer follow-up tracker (M2.1 PR-2)
-- =====================================================================
-- Additive annotation ledger for ongoing officer follow-up on monitoring
-- alerts (notes, next steps, snooze, client-contact, pending-review markers).
-- Authored in the repo's SQLite-portable convention:
--   * INTEGER PRIMARY KEY AUTOINCREMENT  -> translated to SERIAL on PostgreSQL
--   * DEFAULT (datetime('now'))          -> translated on PostgreSQL
-- by the migration runner's _translate_query (see db.py executescript).
--
-- SCOPE: additive only.
--   * No column added to or altered on monitoring_alerts (status stays
--     canonical; "open follow-up" / "next due" are DERIVED from these rows).
--   * No CHECK constraint on monitoring_alerts (M1.3 stays deferred).
--   * Soft reference to monitoring_alerts(id) with ON DELETE CASCADE.

CREATE TABLE IF NOT EXISTS monitoring_alert_followups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL REFERENCES monitoring_alerts(id) ON DELETE CASCADE,
    action TEXT NOT NULL DEFAULT 'note'
        CHECK(action IN ('note','next_step','snooze_until','contacted_client','pending_review','other')),
    note TEXT,
    due_at TEXT,
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    resolved_at TEXT,
    resolved_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_monitoring_followups_alert
    ON monitoring_alert_followups(alert_id);

CREATE INDEX IF NOT EXISTS idx_monitoring_followups_open
    ON monitoring_alert_followups(alert_id, resolved_at);
