-- Migration 017: Screening Monitoring Subscriptions Table
-- =======================================================
-- Creates the screening_monitoring_subscriptions table for tracking
-- ComplyAdvantage monitoring subscription lifecycle. Subscriptions are
-- per-customer (not per-screening-snapshot) and have their own lifecycle
-- (active/paused/cancelled/expired) independent of any screening event.
-- This is why the data does NOT live in screening_reports_normalized.
--
-- SCOPE: additive only. Single new table. No existing tables altered.
--
-- DIALECT: SQLite-portable syntax; runner translates AUTOINCREMENT and
-- datetime() for PostgreSQL execution per existing convention.
--
-- IDEMPOTENCY: CREATE TABLE/INDEX IF NOT EXISTS protects manual replays;
-- schema_version remains the runner-level source of truth.
--
-- TRACK-A SCAFFOLDING LOCK: is_authoritative INTEGER NOT NULL DEFAULT 0
-- CHECK(is_authoritative = 0) preserves the activation-gate pattern A4/A8
-- established. Will be lifted by a future migration at Track E.

CREATE TABLE IF NOT EXISTS screening_monitoring_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL,
    application_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    person_key TEXT,
    customer_identifier TEXT NOT NULL,
    external_subscription_id TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'paused', 'cancelled', 'expired')),
    subscribed_at TEXT DEFAULT (datetime('now')),
    last_event_at TEXT,
    last_webhook_type TEXT,
    monitoring_event_count INTEGER NOT NULL DEFAULT 0,
    is_authoritative INTEGER NOT NULL DEFAULT 0
        CHECK(is_authoritative = 0),
    source TEXT NOT NULL DEFAULT 'migration_scaffolding',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_screening_monitoring_subs_app
    ON screening_monitoring_subscriptions (application_id);

CREATE INDEX IF NOT EXISTS idx_screening_monitoring_subs_client
    ON screening_monitoring_subscriptions (client_id, application_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_screening_monitoring_subs_customer
    ON screening_monitoring_subscriptions (client_id, provider, customer_identifier);
