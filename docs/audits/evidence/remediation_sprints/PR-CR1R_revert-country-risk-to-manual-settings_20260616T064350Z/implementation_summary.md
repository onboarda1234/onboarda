# PR-CR1R Implementation Summary

## Backend

- Restored manual country-risk scoring through `risk_config.country_risk_scores`.
- Removed operational scoring dependency on `country_risk.lookup_country_risk`.
- Removed country-risk snapshot checksum/version from `risk_config_version`.
- Memo jurisdiction evidence now uses manual country-risk settings and marks `source_mode=manual_settings`.
- `/api/config/country-risk` now reports active manual settings, `snapshot=null`, and `reference_snapshot_active_for_scoring=false`.
- Grouped UI country-risk payloads are normalised into numeric score maps before validation/storage.
- Country-risk snapshot tables remain as dormant schema; startup no longer seeds the flawed PR-CR1 snapshot.
- Follow-up corrective repair: startup now runs a one-time data repair that backfills missing default manual country/sector/entity scoring keys into existing `risk_config` rows while preserving manual overrides. Completion is recorded in `data_migration_markers` so later deliberate manual removals are not re-added every startup. This closes the staging validation gap where a stored manual score map could omit Mauritius even though the single-country fallback remained safe.

## UI

- Risk Scoring Model country-risk section is editable again.
- Active source notice states manual settings are active and PR-CR1 snapshot data is reference-only/not active for pilot.
- Medium/standard countries are shown, including Mauritius.
- Active country chips are de-duplicated across sections.
- Save path persists numeric manual score map instead of grouped arrays.

## Tests

- Added/updated regression coverage for manual lookup, memo source, API payload, grouped-list save normalisation, UI manual mode, duplicate display guard, and risk config versioning.
- Added regression coverage that existing partial manual score maps are repaired by adding missing defaults without overwriting manually changed values.
- Fixed mutating API tests to restore the shared test DB risk-model row after intentional update scenarios.
- Follow-up dedupe repair: `/api/config/country-risk` now emits one active list entry per canonical country key, so aliases such as `uk`/`united kingdom`, `usa`/`united states`, `bvi`/`british virgin islands`, `drc`/`democratic republic of congo`, and `dprk`/`north korea` do not appear as duplicate operational countries.
