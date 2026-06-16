# Implementation Summary

## Backend

- Added `arie-backend/country_risk.py` as the canonical country-risk snapshot service.
- Added `country_risk_snapshots` and `country_risk_entries` schema creation to startup DB initialization and inline migrations.
- Added `migration_035_country_risk_governance.sql`.
- Seeded `country-risk-fatf-2026-02-13-v1` as the active snapshot with source URL, publication date, effective date, imported metadata, checksum, and freshness rule.
- Added `lookup_country_risk()` and `list_country_risk_entries()`.
- Updated `classify_country()` so canonical snapshot records win over legacy `risk_config.country_risk_scores` and hardcoded fallback lists.
- Updated elevation and very-high floor logic to use canonical FATF/sanctions/high-risk status.
- Updated risk recomputation `risk_config_version` to include both risk-config timestamp and country-risk snapshot version/checksum.
- Added `GET /api/config/country-risk` for full snapshot inventory and single-country lookup.

## Memo Provenance

- Updated memo jurisdiction evidence to include:
  - risk score,
  - FATF status,
  - sanctions/high-risk status,
  - source name,
  - source URL,
  - source publication date,
  - effective date,
  - snapshot ID/version/checksum,
  - entry checksum,
  - last checked/imported timestamp,
  - stale warning.

## UI

- Updated the Risk Scoring Model country-risk section to show governed source metadata from `/api/config/country-risk`.
- Replaced the misleading direct edit affordance with `Governed Source`.
- Displays source, version, effective date, last checked/imported date, freshness warning, and FATF/high-risk rows.
- Legacy static arrays remain fallback reference only and are repopulated from the canonical API when available.

## Fail-Safe Behavior

- Unknown countries default to MEDIUM with `is_unknown=true`, `defaulted=true`, `high_risk_status=unknown_country`, and a warning.
- Unknown countries do not silently default to LOW.
- If a canonical record exists, legacy stale FATF/config entries cannot override it.
