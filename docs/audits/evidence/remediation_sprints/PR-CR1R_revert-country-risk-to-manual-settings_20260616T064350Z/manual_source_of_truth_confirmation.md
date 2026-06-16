# Manual Source Of Truth Confirmation

## Active Source

Manual Risk Scoring Model settings are active for pilot.

Primary storage:

- table: `risk_config`
- column: `country_risk_scores`
- shape: `{country_key: score}`
- one-time startup data repair: missing default manual country keys are added to existing rows without overwriting existing manual values, then marked in `data_migration_markers`

## Confirmed Active Paths

- risk scoring: `rule_engine.classify_country()` and `compute_risk_score()`
- elevation/floor helper: manual score map first; fallback lists only when no manual entry exists
- memo evidence: `memo_handler.build_compliance_memo()`
- API: `GET /api/config/country-risk`
- UI: Risk Scoring Model country-risk section in `arie-backoffice.html`

## Snapshot Status

The PR-CR1 imported snapshot is not operational:

- not used by scoring
- not used by memo evidence
- not included in risk config versioning
- not loaded into active UI country groups
- not seeded on startup after PR-CR1R

Existing deployed snapshot rows may remain in database tables as inert historical/reference data.

## Post-Deploy Validation Finding And Repair

Initial staging smoke on merge SHA `452fbd7f52f38c1cf107d5853ceb6decd47a13d4` confirmed that `/api/config/country-risk?country=Mauritius` returned `mode=manual_settings`, `risk_score=2`, and `active_for_scoring=true`, but `/api/config/risk-model` did not include `mauritius` in the persisted `country_risk_scores` map.

That was not acceptable for the PR-CR1R requirement that manually configured countries remain visible in manual settings. The follow-up repair backfills missing default manual scoring keys, including `mauritius`, into existing rows while preserving any officer/admin override already present. The repair is one-time so future deliberate manual removals are not silently reversed on every startup.
