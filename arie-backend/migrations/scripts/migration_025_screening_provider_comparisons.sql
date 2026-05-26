-- Migration 025: D2 provider-pair comparison artifacts
-- Creates durable Sumsub-primary / ComplyAdvantage-shadow comparison storage.

CREATE TABLE IF NOT EXISTS screening_provider_comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    primary_provider TEXT NOT NULL,
    shadow_provider TEXT NOT NULL,
    comparison_kind TEXT NOT NULL DEFAULT 'screening_shadow',
    primary_normalized_record_id INTEGER,
    shadow_normalized_record_id INTEGER,
    mismatch_class TEXT NOT NULL,
    comparison_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_provider_comparisons_app
    ON screening_provider_comparisons(application_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_comparisons_app_pair
    ON screening_provider_comparisons(application_id, primary_provider, shadow_provider, comparison_kind);
