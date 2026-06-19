# PR-PRS-C2 Test Results

## Local Suite (periodic-review memo + engine)

- Command: `python -m pytest tests/test_periodic_review_memo.py tests/test_periodic_review_engine.py -v`
- Result: **98 passed in 86.54s** (Python 3.11.15, pytest 9.0.2)
- Per-test log: `logs/local_test_run.txt`
- New C2 coverage:
  - `TestCompleteHandlerHook::test_generator_failure_quarantines_completion_pending_memo`
  - `TestCompleteHandlerHook::test_recovery_after_quarantine_finalizes_review`
  - `TestMemoGateQuarantine::test_quarantine_defers_next_cycle_scheduling`
  - `TestMemoGateQuarantine::test_quarantine_elevates_canonical_risk_immediately`
  - `TestMemoGateQuarantine::test_re_completion_of_quarantined_review_is_blocked`
  - `TestMemoGateQuarantine::test_recovery_sweep_finalizes_quarantined_review`
  - `TestMemoGateQuarantine::test_staleness_flag_when_review_mutated_after_memo`

## Post-Merge Main CI

- Workflow: `Onboarda CI` — run `27816794118`
- Head SHA: `cda0cc77967c052f0bab90081701123d4119cda5`
- Status: `completed` / `success`

| Job | Status | Conclusion | Started (UTC) | Completed (UTC) |
| --- | --- | --- | --- | --- |
| `lint-and-test` | completed | success | 2026-06-19T09:13:59Z | 2026-06-19T09:32:11Z |
| `docker-validate` | completed | success | 2026-06-19T09:32:14Z | 2026-06-19T09:33:07Z |
| `pdf-tests` | completed | success | 2026-06-19T09:32:14Z | 2026-06-19T09:32:51Z |

- Full-suite gate `Test count check (minimum 3800)`: `success`
- `Coverage threshold check`: `success`

## Staging Deploy Workflow

- Workflow: `Deploy to Staging` — run `27816794109`
- Head SHA: `cda0cc77967c052f0bab90081701123d4119cda5`
- Status: `completed` / `success`

| Job | Status | Conclusion | Started (UTC) | Completed (UTC) |
| --- | --- | --- | --- | --- |
| `ci / lint-and-test` | completed | success | 2026-06-19T09:14:02Z | 2026-06-19T09:32:26Z |
| `ci / docker-validate` | completed | success | 2026-06-19T09:32:28Z | 2026-06-19T09:33:19Z |
| `ci / pdf-tests` | completed | success | 2026-06-19T09:32:28Z | 2026-06-19T09:33:14Z |
| `deploy` | completed | success | 2026-06-19T09:33:21Z | 2026-06-19T09:45:47Z |

Deploy job steps of note (all `success`): "Build and push Docker image", "Register new task definition with SHA-pinned image", "Deploy to ECS (rolling update)", "Verify deployment health", "Verify portal and backoffice".

## Notes

- CI runs the full backend suite (≥3800-test gate + coverage threshold), so C2 is proven not only by the focused 98-test run above but by the complete suite at the merge SHA.
- The local focused run is captured for per-test granularity tied to the required-evidence matrix.
