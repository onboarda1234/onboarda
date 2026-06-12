# PROVIDER-LABEL-CLEANUP-2 - Back-Office Runtime OpenSanctions Removal

Date: 2026-06-12

## Build and Branch Provenance

| Item | Value |
| --- | --- |
| Source of truth | `origin/main` |
| `origin/main` SHA at branch creation | `7f94117f5e8f75e9e3c4dce435b2bef6656523d5` |
| Branch | `codex/provider-label-cleanup-2-backoffice-runtime` |
| Branch base SHA | `7f94117f5e8f75e9e3c4dce435b2bef6656523d5` |
| Local HEAD SHA | Pending final commit |
| PR number | Pending |
| Merge commit SHA | Pending PR merge |
| Deployed `/api/version` SHA | Pending authenticated staging validation |
| ECS task definition | Pending post-deploy validation |

## Runtime OpenSanctions Findings Before Fix

Authenticated staging browser validation could not be completed in this run because no approved staging credentials or bearer token were present in the environment:

- `STAGING_QA_EMAIL`: missing
- `STAGING_QA_PASSWORD`: missing
- `ADMIN_OR_SCO_JWT`: missing
- `BACKOFFICE_TOKEN`: missing

Unauthenticated staging checks completed:

| Surface | Result |
| --- | --- |
| `GET https://staging.regmind.co/backoffice` | Downloaded 1,580,741 bytes; no removed-provider variants found in static HTML |
| `GET /api/health` | `200`; no removed-provider variants |
| `GET /api/config/environment` | `200`; no removed-provider variants |
| `GET /api/version` | `401`; endpoint is auth-gated |
| Protected endpoints (`/api/screening/status`, `/api/resources`, `/api/config/system-settings`, `/api/config/ai-agents`, `/api/screening/queue`) | `401`; authenticated payload validation pending |

No live Sumsub or ComplyAdvantage calls were triggered.

## Root Cause

Latest `origin/main` did not contain active OpenSanctions strings outside historical audit evidence. However, active back-office runtime provider status surfaces still exposed Sumsub as an AML-related status:

- `/api/screening/status` could return `Entitlement-proven Sumsub` as `active_aml_screening_provider`.
- `/api/screening/status` exposed `sumsub_aml_entitlement_proven` and `aml_screening_enabled` under the Sumsub object.
- Back-office provider status panels rendered `AML Entitlement (Sumsub)`.

This did not reintroduce OpenSanctions, but it violated the sprint provider policy because Sumsub must remain individual KYC / identity verification only.

## Files and Data Sources Changed

| File | Change |
| --- | --- |
| `arie-backend/server.py` | Removed Sumsub AML entitlement from `/api/screening/status`; active AML provider is now `ComplyAdvantage` or `Not active`; Sumsub payload is IDV/KYC-only. |
| `arie-backoffice.html` | Removed `AML Entitlement (Sumsub)` from provider status and API status modal; replaced with IDV status/scope. |
| `arie-backend/scripts/qa/staging_browser_smoke.js` | Added authenticated browser/runtime scan for removed-provider labels in visible DOM, `localStorage`, and `sessionStorage`. |
| `arie-backend/tests/test_api.py` | Added protected endpoint checks for removed-provider variants and Sumsub AML status leakage. |
| `arie-backend/tests/test_provider_label_policy.py` | Added explicit back-office HTML removed-provider variant regression and updated provider status expectations. |
| `arie-backend/tests/test_authenticated_staging_browser_smoke.py` | Added static assertions that the smoke harness records and fails removed-provider label findings. |
| `README.md`, `docs/DEPLOYMENT_RUNBOOK.md`, `arie-backend/virtual-team/TEAM.md`, `arie-backend/virtual-team/arie-backend-dev.md` | Updated provider responsibility text: Sumsub is individual IDV/KYC; ComplyAdvantage is screening/monitoring. |

No seed/demo data changes were required: current source search found no active OpenSanctions data outside historical audit reports.

## API Validation Evidence

Local authenticated API regression tests passed for:

- `/api/screening/status`
- `/api/health`
- `/api/resources`
- `/api/config/system-settings`
- `/api/config/ai-agents`

The tests assert:

- No OpenSanctions variants are serialized.
- No `Entitlement-proven Sumsub` active AML provider label is serialized.
- No `sumsub_aml_entitlement_proven` field is serialized.
- No `aml_screening_enabled` field is serialized under `sumsub`.
- `active_aml_screening_provider` is only `ComplyAdvantage` or `Not active`.

Authenticated staging API validation remains pending because credentials/tokens were unavailable.

## Browser Validation Evidence

Local/static browser-runtime guard added:

- `arie-backend/scripts/qa/staging_browser_smoke.js` now scans each authenticated back-office page/tab it visits for removed-provider variants in visible DOM and browser storage.
- The smoke fails on `noRemovedProviderLabels=false` and stores findings in `providerLabelFindings`.

Authenticated staging execution remains pending because approved login credentials were unavailable.

## Provider-Label Search Results

Command:

```bash
rg -n -i "OpenSanctions|OpenSanction|open sanctions|open-sanctions|open_sanctions|opensanctions|opensanction" . --glob '!docs/audits/*' --glob '!tmp*'
```

Result: no matches.

Historical audit reports under `docs/audits/` still mention OpenSanctions as prior evidence. They are not loaded into the back-office runtime UI or active API/provider/status/resource responses by this change.

## Tests Run

```bash
python3 -m py_compile server.py screening.py screening_state.py security_hardening.py base_handler.py sumsub_idv_status.py
```

Result: passed.

```bash
pytest -q tests/test_backoffice_monitoring_navigation_static.py tests/test_backoffice_ca_truthflow_static.py tests/test_provider_label_policy.py tests/test_authenticated_staging_browser_smoke.py
```

Result: `43 passed in 0.90s`.

```bash
pytest -q tests/test_api.py -k "provider or health or screening or resources or sumsub or kyc"
```

Result: `32 passed, 106 deselected in 2.68s`.

```bash
node --check arie-backend/scripts/qa/staging_browser_smoke.js
```

Result: passed.

## Remaining Historical References

OpenSanctions remains only in historical audit evidence under `docs/audits/`, including the prior provider cleanup report. These references are historical and should remain excluded from product/runtime scans unless those reports become user-visible resources.

## Remaining Work

1. Open PR and update this report with the PR number.
2. Merge PR and update this report with the merge commit SHA.
3. Deploy staging and validate authenticated `/api/version` matches final `main`.
4. Capture ECS task definition.
5. Run authenticated staging browser smoke with approved `STAGING_QA_EMAIL` and `STAGING_QA_PASSWORD`.
6. Run authenticated staging API scans for protected endpoints.
7. Repeat after clearing browser cache/storage.

## Final Verdict

FAIL.

Reason: local source cleanup and regression coverage passed, but mandatory authenticated staging browser/API validation, deployed `/api/version` SHA validation, ECS task-definition capture, PR merge, and deploy validation are not complete in this run.
