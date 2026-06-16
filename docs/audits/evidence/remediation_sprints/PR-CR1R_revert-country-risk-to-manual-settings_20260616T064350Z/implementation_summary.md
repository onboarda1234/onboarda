# PR-CR1R Implementation Summary

## Backend

- Restored manual country-risk scoring through `risk_config.country_risk_scores`.
- Removed operational scoring dependency on `country_risk.lookup_country_risk`.
- Removed country-risk snapshot checksum/version from `risk_config_version`.
- Memo jurisdiction evidence now uses manual country-risk settings and marks `source_mode=manual_settings`.
- `/api/config/country-risk` now reports active manual settings, `snapshot=null`, and `reference_snapshot_active_for_scoring=false`.
- Grouped UI country-risk payloads are normalised into numeric score maps before validation/storage.
- Country-risk snapshot tables remain as dormant schema; startup no longer seeds the flawed PR-CR1 snapshot.

## UI

- Risk Scoring Model country-risk section is editable again.
- Active source notice states manual settings are active and PR-CR1 snapshot data is reference-only/not active for pilot.
- Medium/standard countries are shown, including Mauritius.
- Active country chips are de-duplicated across sections.
- Save path persists numeric manual score map instead of grouped arrays.

## Tests

- Added/updated regression coverage for manual lookup, memo source, API payload, grouped-list save normalisation, UI manual mode, duplicate display guard, and risk config versioning.
