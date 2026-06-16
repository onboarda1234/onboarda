# PR-CR1R API Smoke

Status: pending until merge and staging deploy.

Required checks after deploy:

- `/api/version` returns merged main SHA
- image tag contains merged main SHA
- `/api/config/risk-model` exposes manual `country_risk_scores`
- `/api/config/country-risk?country=Mauritius` reports `mode=manual_settings`, `risk_score=2`, and `active_for_scoring=true`
- `/api/config/country-risk` reports `snapshot=null` and `reference_snapshot_active_for_scoring=false`
- health/liveness endpoints return 200
