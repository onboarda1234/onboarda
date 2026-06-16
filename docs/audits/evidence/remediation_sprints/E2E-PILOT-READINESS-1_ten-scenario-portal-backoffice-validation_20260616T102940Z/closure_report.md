# E2E-PILOT-READINESS-1 Closure Report

Run ID: 20260616T102940Z

## Verdict

BLOCKED - VERSION MISMATCH.

The resumed staging audit did not proceed to scenario creation because the authenticated staging `/api/version` SHA did not match current `origin/main`.

| Item | Value |
| --- | --- |
| Current `origin/main` SHA | `e127b971e3678d3041fe2514186f58f3d4aa39b3` |
| Staging `/api/version` SHA | `b8deec2ed4ba89fea6630f1339cb650ec56f4a94` |
| Staging build time | `2026-06-16T10:12:39Z` |
| Staging environment | `staging` |
| Source of truth check | FAIL |

## Scope Actually Executed

- Authenticated portal/back-office audit runner started.
- Authenticated back-office `/api/version` was fetched successfully.
- No portal applications were created.
- No documents were uploaded.
- No screening, memo, approval, manual acceptance, SAR, or STR action was triggered.

## Provider Mode

Not evaluated after the version gate failed. The runner stopped before environment/health/provider metadata collection to avoid testing against a stale deployment.

## Credentials Used

- Portal account: `asudally@gmail.com`
- Back-office account: `asudally@ariefinance.mu`
- Secrets omitted.

## Master Table

| Scenario | Portal created? | Back office visible? | Risk score OK? | Docs OK? | Agent 1 OK? | EDD OK? | Screening OK? | Memo/pre-approval OK? | Approval gate OK? | Defects | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S01 Clean low-risk standard company | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S02 Missing required corporate document | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S03 Expired or stale document | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S04 Director/UBO person KYC issue | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S05 PEP declared | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S06 High-risk jurisdiction / country-risk scenario | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S07 High-risk business activity / regulated activity | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S08 Complex ownership / multiple UBOs | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S09 Intermediary / introducer involved | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |
| S10 Manual acceptance / override path | No | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Not tested | Version mismatch | BLOCKED |

## Defects / Gaps

- P0 environment readiness blocker: staging is not deployed at current `origin/main`, so the source-of-truth precondition for this audit is not met.

## Recommended Next Action

Deploy current `origin/main` (`e127b971e3678d3041fe2514186f58f3d4aa39b3`) to staging, confirm `/api/version` matches, then rerun the ten-scenario portal-to-back-office validation.

## Final Pilot-Readiness Verdict

Not ready from this resumed pass because the required staging version gate failed before testing.
