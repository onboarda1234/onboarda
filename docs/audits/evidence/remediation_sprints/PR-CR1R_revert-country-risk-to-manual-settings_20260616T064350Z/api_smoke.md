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

## Final Smoke Requirements

Required checks after deploy:

- `/api/version` returns merged main SHA
- image tag contains merged main SHA
- `/api/config/risk-model` exposes manual `country_risk_scores`
- `/api/config/risk-model` includes `mauritius`
- `/api/config/country-risk?country=Mauritius` reports `mode=manual_settings`, `risk_score=2`, and `active_for_scoring=true`
- `/api/config/country-risk` reports `snapshot=null` and `reference_snapshot_active_for_scoring=false`
- health/liveness endpoints return 200
