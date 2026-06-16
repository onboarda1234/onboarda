# Implementation Summary

## Files Changed

- `.github/workflows/deploy-staging.yml`
- `arie-backend/screening_complyadvantage/client.py`
- `arie-backend/screening_complyadvantage/payloads.py`
- `arie-backend/server.py`
- `arie-backend/tests/test_api.py`
- `arie-backend/tests/test_complyadvantage_client.py`
- `arie-backend/tests/test_complyadvantage_payloads.py`
- `arie-backend/tests/test_phase6_complyadvantage_readiness.py`
- `arie-backend/tests/test_screening_adapter_complyadvantage.py`
- `arie-backend/tests/test_submit_resilience.py`

## Behavior

- CA strict company payload now avoids Sandbox-rejected rich fields while retaining `legal_name` and optional `industry`.
- CA strict person payload now avoids Sandbox-rejected address/contact fields while retaining identity fields.
- Provider 400 responses now preserve sanitized diagnostic context on `CABadRequest`.
- `/api/screening/status` exposes non-secret Sandbox/config labels and optional auth probe status.
- The status endpoint reads non-secret Sandbox labels from runtime environment when present.
- Provider unavailable behavior remains fail-closed: no false clear, no silent bypass, no simulation fallback.
