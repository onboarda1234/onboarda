# PR-CR1R API Smoke

Status: pending final follow-up merge and staging deploy.

## Initial Merge Smoke

Merge SHA `452fbd7f52f38c1cf107d5853ceb6decd47a13d4` deployed successfully and `/api/version` matched the image tag. Authenticated API smoke confirmed:

- `/api/config/country-risk` reported `mode=manual_settings`, `active_source=risk_config.country_risk_scores`, `snapshot=null`, and `reference_snapshot_active_for_scoring=false`.
- `/api/config/country-risk?country=Mauritius` returned `risk_score=2`, `mode=manual_settings`, and `active_for_scoring=true`.
- `/api/config/risk-model` did not include `mauritius` in the persisted `country_risk_scores` map.

Verdict: failed PR-CR1R visibility requirement. The single-country lookup remained safe, but the active manual settings map was incomplete.

Runtime artifact:

- `runtime_json/staging_api_smoke_20260616T0808Z.json`

## Follow-Up Merge Smoke

Merge SHA `69f751cf12f7a7694ecfcd67ad5f6134c706f393` deployed successfully and `/api/version` matched the image tag. Authenticated API smoke confirmed:

- `/api/config/risk-model` included `mauritius=2`.
- `/api/config/country-risk?country=Mauritius` returned `source=risk_config.country_risk_scores`, `found=true`, `risk_score=2`, `mode=manual_settings`, and `active_for_scoring=true`.
- `/api/config/country-risk` still returned duplicate canonical country entries for alias pairs including `uk`/`united kingdom`, `usa`/`united states`, `bvi`/`british virgin islands`, `drc`/`democratic republic of congo`, and `dprk`/`north korea`.

Verdict: failed PR-CR1R duplicate-entry requirement. Scoring and manual-source behavior were correct, but the active API/UI list could still display duplicate operational countries.

Runtime artifact:

- `runtime_json/staging_api_smoke_20260616T0934Z.json`

## Final Smoke Requirements

Required checks after deploy:

- `/api/version` returns merged main SHA
- image tag contains merged main SHA
- `/api/config/risk-model` exposes manual `country_risk_scores`
- `/api/config/risk-model` includes `mauritius`
- `/api/config/country-risk?country=Mauritius` reports `mode=manual_settings`, `risk_score=2`, and `active_for_scoring=true`
- `/api/config/country-risk` reports `snapshot=null` and `reference_snapshot_active_for_scoring=false`
- `/api/config/country-risk` returns no duplicate canonical country entries
- health/liveness endpoints return 200
