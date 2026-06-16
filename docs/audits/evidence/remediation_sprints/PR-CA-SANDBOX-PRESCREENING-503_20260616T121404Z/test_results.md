# Test Results

## Syntax

Command:

`/opt/homebrew/bin/python3.11 -m py_compile arie-backend/screening_complyadvantage/client.py arie-backend/screening_complyadvantage/payloads.py arie-backend/server.py`

Result: pass

## Focused CA And Submit Tests

Command:

`/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_complyadvantage_payloads.py arie-backend/tests/test_complyadvantage_client.py arie-backend/tests/test_phase6_complyadvantage_readiness.py arie-backend/tests/test_screening_adapter_complyadvantage.py arie-backend/tests/test_submit_resilience.py -q`

Initial result: `68 passed in 2.15s`

Rebased-current-main result including API status contract test:

`69 passed in 2.62s`

## API Status Contract

Command:

`/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_api.py::TestAuthenticatedAccess::test_screening_status_does_not_expose_unused_provider -q`

Result: `1 passed in 1.81s`

## CA Auth, Evidence, Audit, Orchestration

Command:

`/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_complyadvantage_auth.py arie-backend/tests/test_complyadvantage_config.py arie-backend/tests/test_complyadvantage_orchestrator.py arie-backend/tests/test_complyadvantage_observability.py arie-backend/tests/test_complyadvantage_evidence_backfill.py arie-backend/tests/test_complyadvantage_historical_backfill.py -q`

Result: `79 passed in 0.54s`

## Portal Prescreening Tests

Command:

`/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_prescreening_fixes.py arie-backend/tests/test_resume_draft_flow.py arie-backend/tests/test_draft_persistence.py::test_kyc_submit_clears_active_draft -q`

Result: `74 passed in 2.08s`

## Direct CA Sandbox Payload Probe

Result: strict company and strict person create-and-screen payloads returned workflow handles in CA Sandbox with synthetic data.
