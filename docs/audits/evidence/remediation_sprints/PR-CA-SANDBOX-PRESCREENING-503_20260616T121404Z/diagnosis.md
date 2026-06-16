# PR-CA-SANDBOX-PRESCREENING-503 Diagnosis

## Scope

Diagnose and restore staging portal prescreening availability for the retryable 503:

`Screening provider temporarily unavailable. Please retry in a moment.`

The workstream did not run the ten-scenario E2E, did not activate SAR/STR, did not test CA Production, and did not mark PR-7 or pilot readiness complete.

## Source Of Truth

- Base branch: `origin/main`
- Current base SHA after rebase to latest `origin/main`: `c40025b17daa80a35466a55b688fe7e571110cb3`
- Original failing staging SHA: `3c093d6fec18dc8331ceb8be701360bbddb198d8`
- Feature branch: `codex/pr-ca-sandbox-prescreening-503`

## Failing Smoke Context

- Synthetic portal application: `ARF-2026-900303`
- Staging version at failure: `3c093d6fec18dc8331ceb8be701360bbddb198d8`
- Back office visibility: confirmed
- Portal submit result: 503 twice

## Code Path

1. Portal calls `POST /api/applications/:id/submit`.
2. Tornado route maps to `SubmitApplicationHandler`.
3. `_do_submit()` calls `run_full_screening(...)`.
4. `server.py` routes screening to `run_screening_for_active_provider(...)`.
5. `screening_routing.py` selects active provider `complyadvantage`.
6. `ComplyAdvantageScreeningAdapter` builds strict and relaxed customer payloads.
7. `ComplyAdvantageScreeningOrchestrator.screen_customer_two_pass()` posts both passes to `/v2/workflows/create-and-screen`.
8. `ComplyAdvantageClient` obtains an OAuth token through `/v2/token`, then sends create-and-screen requests.
9. One strict create-and-screen request returned provider 400.
10. `CABadRequest` propagated to submit handling and was converted to the controlled 503.

## Runtime Evidence Summary

- CA OAuth token call returned 200.
- Relaxed create-and-screen request returned 200.
- Strict create-and-screen request returned 400.
- The handler logged `ComplyAdvantage API request rejected`.
- Direct Sandbox probe reproduced 400 for rich strict company/person payloads and 200 for reduced provider-compatible payloads.
