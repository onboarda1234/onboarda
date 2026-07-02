-- Migration 037: Monitoring Alert four-eyes review requests (M2.2)
-- =====================================================================
-- Additive maker-checker table for material Monitoring Alert clears.
-- Authored in the repo's SQLite-portable convention:
--   * INTEGER PRIMARY KEY AUTOINCREMENT  -> translated to SERIAL on PostgreSQL
--   * DEFAULT (datetime('now'))          -> translated on PostgreSQL
-- by the migration runner's _translate_query (see db.py executescript).
--
-- SCOPE: additive only.
--   * No column added to or altered on monitoring_alerts (status stays
--     canonical; "pending second review" is derived from an open row here).
--   * No CHECK constraint on monitoring_alerts (M1.3 stays deferred).
--   * Soft reference to monitoring_alerts(id) with ON DELETE CASCADE.

CREATE TABLE IF NOT EXISTS monitoring_alert_review_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL REFERENCES monitoring_alerts(id) ON DELETE CASCADE,
    tier INTEGER,
    requested_outcome TEXT,
    dismissal_reason TEXT,
    rationale TEXT,
    evidence_ref TEXT,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','approved','rejected','senior_cleared')),
    initiated_by TEXT,
    initiated_at TEXT DEFAULT (datetime('now')),
    approved_by TEXT,
    approved_at TEXT,
    approval_note TEXT,
    rejection_reason TEXT,
    second_review_bypassed INTEGER DEFAULT 0,
    sampled_for_qa INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_monitoring_review_requests_alert
    ON monitoring_alert_review_requests(alert_id);

CREATE INDEX IF NOT EXISTS idx_monitoring_review_requests_state
    ON monitoring_alert_review_requests(state);
