# Gotchas

## Upload Latency Remediation

- `docs/IMPLEMENTATION_PLAYBOOK.md` existed locally before PR0 but was not present on GitHub `main`; GitHub `main` remains the source of truth.
- Backend upload limit is already 10 MB on GitHub `main`.
- Back office client-side size check still references 25 MB before PR5.
- Portal and back office currently chain upload and verification.
- CloudWatch Logs Insights rejects `| sort bin(5m) desc` after a `stats ... by bin(5m)` aggregation. Alias the bin first: `by bin(5m) as window, ... | sort window desc`.
- `FF_POLLING_SLOW` is backend-only under the current flag-exposure contract. PR4 must either keep the polling slowdown server-driven or explicitly lock a new client-exposure decision and test before frontend code reads that flag.
- PR3 telemetry logs only upload/verify request-boundary duration. It does not yet split validation, S3, DB, or verifier phase timings.

## Verification Truthfulness Remediation

- Step 0 moved reliability remediation ahead of async rollout. Do not proceed
  from PR5 directly to PR6/PR7; PR8 must land before async foundation/flip.
- PR5 intentionally touches protected files (`db.py`, `arie-portal.html`,
  `arie-backoffice.html`). The guard can pass with `protected-file-override`,
  but merge still requires explicit written project-lead approval.
- Portal final review must gate both company documents and person/intermediary
  KYC documents on backend `verification_success`; stored/uploaded alone is not
  sufficient.
- The staging PostgreSQL database is VPC-private. Direct local DB connections
  time out; use a read-only ECS one-off task in the staging network for schema
  verification.
- `/api/version` is auth-gated. Image/SHA verification can be done through ECS
  task definition metadata when no staging token is available.
- PR8 intentionally touched protected verification files
  `arie-backend/claude_client.py` and `arie-backend/document_verification.py`;
  the override was approved and recorded on PR #354 before merge.
- PR8 runtime verification does not need a live Claude call. A safe ECS one-off
  can import `verification_failure_taxonomy` and classify a synthetic invalid
  PDF error to prove the deployed artifact is present.
- GitHub does not allow the PR author to approve their own PR. When Claude review
  tokens are unavailable, use a structured self-review comment plus green CI and
  staging verification as the review record.
- The project lead granted standing approval on 2026-05-20 to apply the
  `protected-file-override` label for the remaining remediation PRs when
  protected files are intentionally in scope. Still keep every PR one-concern,
  tested, and staging-verified.
- ECS one-off task stdout can be checked in `/ecs/regmind-staging`; if output is
  hard to retrieve, use assert-only one-off commands and rely on container exit
  code `0` plus task definition/image SHA matching for runtime artifact proof.
- The current GitHub OAuth credential cannot push changes to
  `.github/workflows/*`; GitHub rejects workflow-file updates without `workflow`
  scope. Use the GitHub UI/token with workflow scope for workflow changes, or
  avoid workflow edits.
- `FF_ASYNC_VERIFY=true` is unsafe with only the PR6 foundation because the API
  queues `verification_jobs` but no ECS worker runtime is currently deployed to
  claim and complete them. PR7 must not be retried until the worker is actually
  running in staging.
- PR7A created the separate staging worker service
  `regmind-verification-worker`; subsequent backend deploys update only the API
  service unless worker deployment automation is added. Until then, manually
  register/update the worker task definition from the deployed API task
  definition so image SHA, task role, secrets, and networking stay aligned.
- When deriving the worker task definition from the API task definition, remove
  the API container health check, set the command to
  `python verification_worker.py --poll-interval 5`, set log stream prefix to
  `worker`, keep `VERIFICATION_WORKER_ID=staging-worker-1`, and keep
  `FF_ASYNC_VERIFY` absent/off until PR7.
- Staging PostgreSQL uses the real Postgres schema; do not assume local test
  columns exist. `verification_jobs` has `last_error` and `run_after`, not
  `error_code` or `next_run_at`; `documents` has `verification_status`,
  `verification_results`, and `verified_at`; `audit_log` stores actor identity
  in `user_id/user_name/user_role` plus JSON `detail`, not standalone
  `actor_type` columns.
- Controlled PR7A runtime probes intentionally used missing file paths, so
  terminal `flagged` was the expected verification result. The runtime gate was
  queue claim, document compatibility-field update, audit lifecycle, and stale
  lock reclaim, not content-level document success.
- Worker stale-lock recovery logs
  `verification_async_job_health stuck_jobs=1 requeued_jobs=1 failed_jobs=0`
  as a warning during the controlled reclaim test. Treat that specific event as
  expected evidence for PR7A, not a production regression.
