-- PR-CR1R: Dormant country-risk source governance snapshot schema.
-- The PR-CR1 imported snapshot is not active for pilot. Manual
-- risk_config.country_risk_scores is the operational source of truth.

CREATE TABLE IF NOT EXISTS country_risk_snapshots (
    id TEXT PRIMARY KEY,
    version TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    source_name TEXT NOT NULL,
    source_url TEXT,
    source_publication_date TEXT,
    effective_date TEXT NOT NULL,
    imported_at TEXT DEFAULT (datetime('now')),
    imported_by TEXT NOT NULL DEFAULT 'system',
    last_checked_at TEXT DEFAULT (datetime('now')),
    checksum TEXT NOT NULL,
    freshness_days INTEGER NOT NULL DEFAULT 180,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS country_risk_entries (
    id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES country_risk_snapshots(id),
    country_name TEXT NOT NULL,
    country_key TEXT NOT NULL,
    iso_alpha2 TEXT,
    iso_alpha3 TEXT,
    risk_rating TEXT NOT NULL,
    risk_score INTEGER NOT NULL CHECK(risk_score BETWEEN 1 AND 4),
    fatf_status TEXT NOT NULL DEFAULT 'none',
    sanctions_status TEXT NOT NULL DEFAULT 'none',
    high_risk_status TEXT NOT NULL DEFAULT 'none',
    source_name TEXT NOT NULL,
    source_url TEXT,
    source_publication_date TEXT,
    effective_date TEXT NOT NULL,
    imported_at TEXT DEFAULT (datetime('now')),
    imported_by TEXT NOT NULL DEFAULT 'system',
    status TEXT NOT NULL DEFAULT 'active',
    checksum TEXT NOT NULL,
    notes TEXT,
    previous_risk_rating TEXT,
    previous_fatf_status TEXT,
    UNIQUE(snapshot_id, country_key)
);

CREATE INDEX IF NOT EXISTS idx_country_risk_entries_lookup
    ON country_risk_entries(snapshot_id, country_key, status);

CREATE INDEX IF NOT EXISTS idx_country_risk_entries_fatf
    ON country_risk_entries(fatf_status, status);
