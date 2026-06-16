# API Smoke

## Local Automated API Coverage

`tests/test_api.py::TestRiskModelAdminConfigSafety::test_country_risk_endpoint_exposes_snapshot_provenance` passed locally.

Coverage:
- `GET /api/config/country-risk?country=Kuwait` returns:
  - `country_key=kuwait`
  - `risk_score=3`
  - `fatf_status=increased_monitoring`
  - source URL for FATF increased monitoring
  - `snapshot_version=FATF-2026-02-13+REGMIND-POLICY-V1`
- `GET /api/config/country-risk` returns active snapshot and entries.

## Staging API Smoke

Pending merged-main staging deployment.
