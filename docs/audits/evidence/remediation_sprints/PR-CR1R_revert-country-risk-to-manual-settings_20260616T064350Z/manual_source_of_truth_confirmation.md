# Manual Source Of Truth Confirmation

## Active Source

Manual Risk Scoring Model settings are active for pilot.

Primary storage:

- table: `risk_config`
- column: `country_risk_scores`
- shape: `{country_key: score}`

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
