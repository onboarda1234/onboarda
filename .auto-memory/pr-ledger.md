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

## Verification Truthfulness Remediation

| Slot | Concern | Status | Notes |
| --- | --- | --- | --- |
| Step 0 | Internal impact assessment | Done | Findings: 314 active docs; 94 verified, 152 flagged, 68 pending; at least 157 active non-verified docs in false-success risk zone; provider-error-driven current docs `29/314 = 9.2%`; provider-error-driven flagged docs `29/152 = 19.1%`; recommended order changed to `PR5 -> PR8 -> PR6 -> PR7 -> PR9`. |
| PR 5 | Truthful verification state model + coherent `submit-kyc` | Merged, deployed, staging-verified | PR #352. Merge SHA `2833e40f5608cbaeece06b89854788d78e8c66eb`. Adds backend-owned verification states, audited transitions, hard `submit-kyc` verified-doc gate, portal/BO truthful rendering, and server-driven BO polling config. |
| PR 8 | Claude/provider reliability remediation | Merged, deployed, staging-verified | PR #354. Merge SHA `564ece3f0747774c95dce62302658ca0bcad698c`. Classifies provider/request-path failures, persists provider failures as `failed`, keeps business review outcomes `flagged`, and adds PII-safe telemetry/query material. |
| PR 6 | Async verification foundation, dark | Merged, deployed, staging-verified | PR #357. Merge SHA `68d0804d795a1f4bae607ac24b9c461793571c35`. Adds `verification_jobs`, worker/job primitives, status endpoint, SLA/query material; `FF_ASYNC_VERIFY=false` and no screening-provider behavior changed. |
| PR 7 | Async verify staging flag flip + soak | Blocked before soak | 2026-05-20 staging-only flip was attempted via ECS task definition `regmind-staging:315`, then rolled back to `regmind-staging:314`. Blocker: `FF_ASYNC_VERIFY=true` queues jobs through the API, but no ECS worker process is wired to claim/complete `verification_jobs`. Do not re-enable until a worker runtime is deployed and smoke-tested. |
| PR 7A | Async verification worker runtime | Merged, deployed, staging-verified | PR #360 plus audit-detail follow-up PR #361. Merge SHAs `82df577cff39a57f8e6925f5e458afe71ad2c1b7` and `8a0e8fc390c848b680b8206e6d4ca400683c14f8`. Staging worker service `regmind-verification-worker` is running separately from API, desired/running `1/1`, same image as API, no ALB, `FF_ASYNC_VERIFY` still absent/off. Controlled queued-job and stale-lock reclaim validations passed. |
| PR 9 | Duplicate detection redesign | Planned after PR7 | Stored hash + indexed lookup, safe legacy handling. |

## PR5 Deployment Verification

- 2026-05-20: PR #352 merged to `main` at
  `2833e40f5608cbaeece06b89854788d78e8c66eb`.
- Deploy to Staging run `26155080692` passed: `lint-and-test`, `pdf-tests`,
  `docker-validate`, ECS deploy, liveness, portal, and backoffice checks.
- ECS service `regmind-backend` on cluster `regmind-staging` is running task
  definition `regmind-staging:309`, rollout `COMPLETED`, desired/running/pending
  `1/1/0`.
- Deployed image and task environment are SHA-pinned:
  `782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:2833e40f5608cbaeece06b89854788d78e8c66eb`;
  `GIT_SHA` and `IMAGE_TAG` match the merge SHA.
- Public staging checks passed: `/api/liveness` 200, `/api/health` 200,
  `/portal` 200, `/backoffice` 200.
- `/api/config/environment` returned `environment=staging`, upload-latency
  client flags still exact allowlist, and safe BO runtime config
  `applications_refresh_ms=30000`, `applications_stale_threshold_s=60`.
- Read-only ECS in-VPC schema probe confirmed
  `documents_verification_status_check` includes `in_progress`, invalid
  document status rows = `0`, and staging status counts are flagged `187`,
  pending `95`, verified `96`.
- Served portal artifact contains PR5 markers `Submission Blocked —
  Verification Required`, `Verification Gate Active`,
  `data-verification-success`, and `function kycDocumentVerificationState`.
- Served backoffice artifact contains `BACKOFFICE_CONFIG` and
  `applyBackofficeRuntimeConfig`.

## PR8 Deployment Verification

- 2026-05-20: PR #354 merged to `main` at
  `564ece3f0747774c95dce62302658ca0bcad698c`.
- Main CI run `26160280967` passed: `lint-and-test`, `pdf-tests`, and
  `docker-validate`.
- Deploy to Staging run `26160281051` passed: CI gates, ECS deploy, deployment
  health, portal, and backoffice checks.
- ECS service `regmind-backend` on cluster `regmind-staging` is running task
  definition `regmind-staging:311`, rollout `COMPLETED`, desired/running/pending
  `1/1/0`.
- Deployed image and task environment are SHA-pinned:
  `782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:564ece3f0747774c95dce62302658ca0bcad698c`;
  `GIT_SHA` and `IMAGE_TAG` match the merge SHA.
- Runtime artifact check passed with a one-off ECS task from
  `regmind-staging:311`: imported `verification_failure_taxonomy`, classified a
  Claude invalid-PDF error as `terminal_invalid_request` / `claude_invalid_pdf`,
  and exited `0`. No live Claude or screening-provider call was made.
- Public staging checks passed: `/api/liveness` 200, `/api/health` 200,
  `/api/config/environment` 200, `/portal` 200, `/backoffice` 200.
- `/api/version` returned 401 unauthenticated; build provenance was verified
  through ECS task definition image/env instead.
- CloudWatch check over the deploy window found no `/ecs/regmind-staging` events
  matching `ERROR`, `connection pool exhausted`, or `falling back to mock mode`.

## PR6 Deployment Verification

- 2026-05-20: PR #357 merged to `main` at
  `68d0804d795a1f4bae607ac24b9c461793571c35`.
- Main CI passed before merge and the post-merge Deploy to Staging run
  `26166398868` passed: `lint-and-test`, `pdf-tests`, `docker-validate`, and
  ECS deploy.
- ECS service `regmind-backend` on cluster `regmind-staging` is running task
  definition `regmind-staging:313`, rollout `COMPLETED`, desired/running/pending
  `1/1/0`.
- Deployed image and task environment are SHA-pinned:
  `782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:68d0804d795a1f4bae607ac24b9c461793571c35`;
  `GIT_SHA` and `IMAGE_TAG` match the merge SHA.
- Runtime artifact check passed with a one-off ECS task from
  `regmind-staging:313`: `verification_jobs` table count `1`,
  active-document unique index count `1`, `FF_ASYNC_VERIFY=false`, and async SLA
  config present with `stuck_job_threshold_seconds=1200`.
- Public staging checks passed: `/api/liveness` 200, `/api/health` 200,
  `/api/config/environment` 200, `/portal` 200, `/backoffice` 200.
- `/api/config/environment` did not expose `FF_ASYNC_VERIFY`; client-visible
  flags remained limited to the intended allowlist.
- CloudWatch deploy-window scan found no migration failures, connection pool
  exhaustion, or mock-mode fallback.

## PR7 Blocker And Rollback

- 2026-05-20: PR7 staging flag flip analysis found the explicit staging flag
  path in `.github/workflows/deploy-staging.yml`, but pushing workflow changes
  from the current OAuth credential failed because it lacks GitHub `workflow`
  scope.
- A runtime-only staging flip was attempted by registering ECS task definition
  `regmind-staging:315` from `regmind-staging:314` with
  `FF_ASYNC_VERIFY=true`. The service reached steady state and public smoke
  checks passed.
- Runtime checks confirmed `FF_ASYNC_VERIFY=true` was present in the ECS task
  definition and remained backend-only: `/api/config/environment` did not expose
  the flag.
- Gate failed before soak: current deployed code has async job primitives and an
  enqueue/status API path, but no running ECS worker process/entrypoint wired to
  claim and complete `verification_jobs`.
- Enabling the flag in that state would turn verification requests into queued
  jobs with no consumer, so PR7 was stopped and staging was rolled back to
  `regmind-staging:314`.
- Rollback verification passed: ECS rollout `COMPLETED`, `FF_ASYNC_VERIFY`
  absent from the active task definition, `/api/liveness`, `/api/health`,
  `/api/config/environment`, `/portal`, and `/backoffice` all returned 200, and
  CloudWatch showed no migration failures, connection pool exhaustion,
  mock-mode fallback, or traceback events during the flip/rollback window.

## PR7A Worker Runtime Verification

- 2026-05-20: PR #360 merged to `main` at
  `82df577cff39a57f8e6925f5e458afe71ad2c1b7`. It added the real
  `verification_worker.py` runtime entrypoint, reused PR6 job primitives, and
  documented the separate ECS Fargate worker-service shape.
- Initial staging runtime validation on PR #360 exposed an audit detail gap:
  queued jobs reached a terminal state, but the completion audit entry did not
  carry the async `job_id` and `worker_id`. PR #361 fixed this by passing
  worker audit metadata through the synchronous verification handler.
- 2026-05-20: PR #361 merged to `main` at
  `8a0e8fc390c848b680b8206e6d4ca400683c14f8` and the Deploy to Staging run
  `26177750399` passed.
- API staging service `regmind-backend` is running task definition
  `regmind-staging:319` with image/env SHA
  `8a0e8fc390c848b680b8206e6d4ca400683c14f8`.
- Worker staging service `regmind-verification-worker` is running task
  definition `regmind-verification-worker:2`, desired/running `1/1`, same image
  SHA as API, command `python verification_worker.py --poll-interval 5`, no
  load balancer, no container health check inherited from API, and
  `VERIFICATION_WORKER_ID=staging-worker-1`.
- `FF_ASYNC_VERIFY` remains absent/off in API `/api/config/environment` and in
  the worker task environment. PR7 soak has not started.
- Public smoke checks passed after deploy: `/api/liveness` 200, `/api/health`
  200, `/portal` 200, and `/backoffice` 200.
- Controlled queued-job validation passed for job
  `vjob_1b6c827cbb6b4f5683b9c17f37c8a984`: worker
  `staging-worker-1` claimed the job, completed it terminally as `succeeded`,
  updated document `doc_pr7a_runtime_0ac7397a78` to
  `verification_status=flagged`, populated `verification_results`, and wrote
  enqueue/start/complete audit entries with `actor_type=system`, `job_id`, and
  `worker_id`.
- Simulated killed-worker reclaim validation passed for stale job
  `vjob_reclaim_0c47e3067c`: stale `in_progress` lock was requeued through
  `async_verify_worker_reclaimed`, reclaimed by `staging-worker-1`, completed
  terminally, advanced attempt count from `1` to `2`, updated document
  `doc_pr7a_reclaim_0c47e3067c`, and wrote reclaim/start/complete audit entries
  with `actor_type=system`, `job_id`, and `worker_id`.
- Worker logs showed the expected `verification_worker_started` line on the new
  task definition and the expected `verification_async_job_health stuck_jobs=1
  requeued_jobs=1 failed_jobs=0` warning from the controlled stale-lock test.
- Screening-provider behavior remained frozen: no `FF_ASYNC_VERIFY` flag flip,
  no PR7 soak, no ComplyAdvantage activation, and no Sumsub provider-selection
  or workflow-timing change observed.
