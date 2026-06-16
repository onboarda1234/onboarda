# E2E-PILOT-READINESS-1 Closure Report

Run ID: 20260616T104719Z

## Final Status

BLOCKED - PRESCREENING SMOKE FAILED.

The current `origin/main` SHA was deployed to staging and `/api/version` matched both `git_sha` and `image_tag`. Provider status was checked and recorded. The audit then created one clean low-risk synthetic portal smoke application, but prescreening submit returned 503 twice. Per the workstream gate, the ten-scenario E2E was not run.

## Source Of Truth

| Item | Value |
| --- | --- |
| Latest `origin/main` SHA | `3c093d6fec18dc8331ceb8be701360bbddb198d8` |
| Staging `git_sha` | `3c093d6fec18dc8331ceb8be701360bbddb198d8` |
| Staging `image_tag` | `3c093d6fec18dc8331ceb8be701360bbddb198d8` |
| Staging build time | `2026-06-16T11:12:37Z` |
| Deploy workflow run | `27611628454` |
| Deploy result | success |

## Provider Mode

| Check | Result |
| --- | --- |
| Active AML provider | ComplyAdvantage Mesh (`complyadvantage`) |
| CA runtime active/configured | PASS |
| CA fallback/simulation | disabled / `false` |
| CA Sandbox workspace/config | Operator-confirmed in workstream instruction; API does not expose workspace identifier |
| Sumsub IDV | live / configured |
| OpenCorporates / registry enrichment | simulated / not configured |

## Prescreening Smoke

| Field | Value |
| --- | --- |
| Application | `E2E 20260616T104719Z GATE Smoke Ltd` |
| Reference | `ARF-2026-900303` |
| Portal create | 201 |
| Back-office visible | true |
| Prescreening submit | 503, 503 |
| Error | `Screening provider temporarily unavailable. Please retry in a moment.` |

## Ten-Scenario Summary

| Scenario | Portal created? | Back office visible? | Risk score OK? | Docs OK? | Agent 1 OK? | EDD OK? | Screening OK? | Memo/pre-approval OK? | Approval gate OK? | Defects | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S01 Clean low-risk standard company | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S02 Missing required corporate document | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S03 Expired or stale document | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S04 Director/UBO person KYC issue | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S05 PEP declared | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S06 High-risk jurisdiction / country-risk scenario | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S07 High-risk business activity / regulated activity | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S08 Complex ownership / multiple UBOs | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S09 Intermediary / introducer involved | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |
| S10 Manual acceptance / override path | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Smoke gate failed | BLOCKED |

## Defects / Gaps

- P0 `prescreening_smoke_503`: aligned staging build still returns 503 from portal prescreening submit with ComplyAdvantage Mesh active and fallback disabled.
- Provider status API does not expose the CA workspace identifier. Sandbox was recorded from operator confirmation plus runtime status, not from a visible workspace field.

## Actions Not Performed

- Ten-scenario E2E not run.
- No document uploads.
- No Agent 1 document verification workload triggered.
- No approval, manual acceptance, SAR, or STR action performed.
- No product logic changed.
- No remediation item marked closed.

## Final Pilot-Readiness Verdict

FAIL / NOT READY.
