# PR Ledger

## Upload Latency Remediation

| Slot | Concern | Status | Notes |
| --- | --- | --- | --- |
| PR 0 | Governance playbook and memory skeleton | Merged, deployed, staging-verified | PR #179. Merge SHA `e5d35499a2a3c5d63c312affd90c2f80d847e7ab`. No runtime code. |
| PR 1 | Flag foundation and exposure contract | Merged, deployed, staging-verified | PR #180. Merge SHA `9b8b9dbdea0153b5b0b4073679338d5e60a5599c`. Upload-latency client allowlist locked. |
| PR 2 | Upload/verify contract tests | Merged, deployed, staging-verified | PR #181. Merge SHA `c8dd5f3e2f85b02b91d7638342e6715b59595fcf`. Includes GATE-03 and audit event shape. |
| PR 3 | Telemetry and CloudWatch query prep | Merged, deployed, staging-verified | PR #182. Merge SHA `8f3751ee31105dc671a48724949da6707612e29c`. Telemetry code and query material deployed. |
| PR 3.1 | CloudWatch query syntax fix | Merged, deployed, staging-verified | PR #183. Merge SHA `397ee3c115d15e0bc71f0b035bdaab21298b8781`. Fixed Logs Insights sort syntax. |
| PR 4 | BO polling slowdown | Merged, deployed, staging-verified | PR #192. Merge SHA `402c8dbaa0d377af3f9e3172eec552c7911f1ab6`. Claude verdict: mergeable. `FF_POLLING_SLOW` remained backend-only. |
| PR 5 | BO 10 MB client-side rejection | Planned | Behind `FF_SIZE_CAP_CLIENT_REJECT`. |
| PR 5.5 | Stabilization window | Planned | 48-72h soak, no risky merges. |

## Process Guard Maintenance

| PR | Concern | Status | Notes |
| --- | --- | --- | --- |
| PR #194 | Protected-files guard override handling | Merged | Merge SHA `5f34106da8e741f5dfcbdbcd4b8a992c22f5f5b6`. The `protected-file-override` label now allows the guard job to pass only with explicit written project-lead approval. |

## Verification Ledger

- 2026-04-28: PR0-3 verified on staging with ECS image `8f3751ee31105dc671a48724949da6707612e29c`, readiness ok, portal/backoffice HTTP 200, and `upload_latency_flags` present with the exact client allowlist.
- 2026-04-28: PR183 verified on staging with ECS image `397ee3c115d15e0bc71f0b035bdaab21298b8781`, readiness ok, portal/backoffice HTTP 200, and corrected CloudWatch queries accepted by Logs Insights.
- 2026-04-29: PR192 verified on staging with ECS task definition `regmind-staging:139` and image `782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:402c8dbaa0d377af3f9e3172eec552c7911f1ab6`. Readiness returned `ready: true` with encryption/database/config ok, health returned `ok` with PostgreSQL connected, portal/backoffice returned HTTP 200, browser opened `https://staging.regmind.co/backoffice`, and the served backoffice artifact contains `_applicationsRefreshMs = 120000` plus `_STALE_THRESHOLD_S = 180` with no `FF_POLLING_SLOW` token.
