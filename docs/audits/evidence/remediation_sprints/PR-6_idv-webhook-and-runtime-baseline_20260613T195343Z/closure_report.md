# PR Closure Report

## PR name

`PR-6 - IDV Webhook and Runtime Baseline`

## Linked remediation IDs

- `FSI-011`
- `POST-INFRA`

## Original issue summary

- `FSI-011`: Sumsub webhook post-commit renormalization used an unsafe closed-DB-handle call contract.
- `POST-INFRA`: Backend and verification worker runtime baseline needed proof under the current AWS staging posture.

## Re-diagnosis result

- Base `origin/main` SHA: `b061c52f147b6fa42398629bb2b5dd2502682f3d`
- Branch: `codex/pr6-idv-webhook-and-runtime-baseline`
- FSI-011 still existed on current main.
- POST-INFRA still existed on current staging: backend was aligned to current main, but worker was still on `15b281fa620d19c8a475f5d3e94e78edcf976f5a`.

## Root cause

See `root_cause.md`.

Summary:

- FSI-011: stale helper signature allowed the webhook to pass a closed legacy DB connection into a post-commit helper.
- POST-INFRA: staging deploy workflow updated only the backend ECS service, leaving the verification worker on an old SHA-tagged task definition.
- Worker runtime: the real `DocumentVerifyHandler._post_with_db()` contract did not accept the arguments used by `verification_worker.default_verification_executor()`.

## Files changed

- `.github/workflows/deploy-staging.yml`
- `arie-backend/server.py`
- `arie-backend/screening_storage.py`
- `arie-backend/scripts/staging_runtime_baseline.py`
- `arie-backend/scripts/verification_worker_smoke.py`
- `arie-backend/tests/test_webhook_normalized_upsert.py`
- `arie-backend/tests/test_pr7a_async_verification_worker_runtime.py`
- `arie-backend/tests/test_pr6_idv_webhook_runtime_baseline.py`
- `arie-backend/docs/screening/complyadvantage/C4-step1-webhook-design.md`
- `docs/observability/async-verification-foundation.md`
- `docs/audits/evidence/remediation_sprints/PR-6_idv-webhook-and-runtime-baseline_20260613T195343Z/*`

## Behaviour before fix

- Webhook renormalization was invoked with a closed DB handle.
- Worker service could remain healthy while running an old image.
- Deployment workflow did not advance worker task definition to merged main.
- Worker default executor path was not tested against the real document verification handler.

## Behaviour after fix

- Webhook renormalization helper takes only `application_id`.
- Webhook post-commit call passes only `app_id`.
- Helper still opens a fresh committed-read DB connection.
- Deploy workflow registers and deploys a SHA-pinned worker task definition using the same image/build provenance as backend.
- Runtime baseline helper reports backend/worker service health, image tags, provenance env, and alignment.
- Synthetic worker smoke helper proves safe queue/job processing without live provider calls.
- Document verification handler accepts worker-owned audit metadata and DB close ownership.

## Tests added/updated

- Added `arie-backend/tests/test_pr6_idv_webhook_runtime_baseline.py`.
- Updated `arie-backend/tests/test_webhook_normalized_upsert.py`.
- Updated `arie-backend/tests/test_pr7a_async_verification_worker_runtime.py`.

## Targeted test results

See `test_results.md`.

Summary:

- Focused PR-6 tests: `25 passed`.
- Broad IDV/Sumsub/worker tests: `206 passed`.
- Closed-remediation regression subset: `39 passed`.
- `git diff --check`: PASS.
- Python compile: PASS.
- Deploy workflow YAML parse: PASS.

## Full suite results

See `full_suite_results.md`.

Summary:

- Local full suite: `1 failed, 5284 passed, 25 skipped`.
- Lone failure: timezone-sensitive RMI deadline test outside PR-6 touched code.
- Rerun under `TZ=UTC`: PASS.
- GitHub CI is required as authoritative full-suite evidence before closure.

## Browser test results

Branch-stage browser smoke: not applicable; no client/officer UI changed.

Post-merge browser smoke remains conditional on whether staging runtime smoke changes visible IDV/document verification state.

## Staging deploy evidence

Pending PR merge and deployment.

## `/api/version` evidence

Pending PR merge and deployment.

## API smoke test evidence

Branch-stage pre-fix runtime baseline evidence saved under `runtime_json/`.

Merged-main staging API/runtime smoke is pending.

## Worker runtime smoke evidence

Branch-stage local synthetic worker smoke passed through tests.

Merged-main staging worker smoke is pending.

## Screenshots/evidence folder path

`docs/audits/evidence/remediation_sprints/PR-6_idv-webhook-and-runtime-baseline_20260613T195343Z/`

## Remaining risks

- `.github/workflows/deploy-staging.yml` was modified. Pushing this PR may require a GitHub token with `workflow` scope.
- POST-INFRA cannot close until merged-main staging deploy updates the worker task definition and worker smoke passes.
- FSI-011 cannot close until merged-main staging `/api/version` alignment and webhook/runtime smoke evidence exist.

## Items not closed by this PR

- `FSI-011` remains `PARTIALLY FIXED` at branch stage.
- `POST-INFRA` remains `PARTIALLY FIXED` at branch stage.
- No other remediation item is closed by this PR.

## Final closure verdict

`PARTIALLY FIXED`

Rationale:

Code fixes and targeted tests are complete on the branch. Closure still requires PR merge, merged-main staging deployment, `/api/version` alignment, backend/worker ECS runtime alignment, staging API/runtime smoke, worker job smoke, and completed evidence.
