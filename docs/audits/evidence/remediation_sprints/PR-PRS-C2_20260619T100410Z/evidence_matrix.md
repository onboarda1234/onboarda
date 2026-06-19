# PR-PRS-C2 — Required-Evidence Matrix

Maps each required closure item to its proof. Evidence classes:

- **TEST** — proven by an automated test (98 passed locally; full suite green in post-merge CI).
- **CODE** — verified at a specific source line; exercised by the integration tests but not asserted by name.
- **CI** — proven by the post-merge GitHub Actions run on the merge SHA.
- **OPERATOR** — requires authenticated staging access; left as a runtime check with exact expected value.

Merge SHA under test: `cda0cc77967c052f0bab90081701123d4119cda5` (PR #543, `main`).

| # | Required evidence | Class | Proof |
|---|-------------------|-------|-------|
| 1 | Full merge SHA for #543 | CI | `cda0cc77967c052f0bab90081701123d4119cda5` — `main` tip; CI run `27816794118`, Deploy run `27816794109`, both `success`. |
| 2 | `/api/version` `git_sha`/`image_tag` matches deployed SHA | OPERATOR | Deploy job shipped a **SHA-pinned image** for `cda0cc779…` and ran "Verify deployment health" + "Verify portal and backoffice" (both `success`, run `27816794109`). Live authenticated probe of `https://staging.regmind.co/api/version` pending operator — expected `git_sha == image_tag == cda0cc77967c052f0bab90081701123d4119cda5`. See `api_smoke.md`. |
| 3 | Forced memo failure goes to `completion_pending_memo` | TEST | `test_periodic_review_memo.py::TestCompleteHandlerHook::test_generator_failure_quarantines_completion_pending_memo` — asserts `status == "completion_pending_memo"`, response `memo_gate.quarantined == True`. |
| 4 | Review is NOT `completed` while memo missing | TEST | Same test — asserts `completed_at IS NULL` and a `generation_failed` memo row persisted; review never reaches `completed`. |
| 5 | Canonical risk elevation still applies during quarantine | TEST | `TestMemoGateQuarantine::test_quarantine_elevates_canonical_risk_immediately` — `risk_rating_changed` → app `final_risk_level ∈ {HIGH, VERY_HIGH}` while review stays `completion_pending_memo`. |
| 6 | No next cycle scheduled while quarantined | TEST | `TestMemoGateQuarantine::test_quarantine_defers_next_cycle_scheduling` — only the original review row exists (count == 1); no next-cycle row created. |
| 7 | Manual `/memo/recover` finalises review | TEST | `TestCompleteHandlerHook::test_recovery_after_quarantine_finalizes_review` — `POST …/memo/recover` → `200`, `status == "completed"`, `memo_gate.finalized == True`, `completed_at` set, `generated` memo row exists. |
| 8 | Next cycle scheduled only after memo succeeds | TEST | Deferral proven by #6 (none while pending); finalisation-on-success proven by #7 and `test_recovery_sweep_finalizes_quarantined_review`. `record_review_outcome` defers scheduling; `finalize_review_memo_completion` schedules the cycle only on the `pending → completed` transition (`periodic_review_engine.py`). |
| 9 | Background sweep tested/simulated | TEST | `TestMemoGateQuarantine::test_recovery_sweep_finalizes_quarantined_review` — `prm.run_memo_recovery_sweep(...)` returns `finalized == 1`, `rid ∈ finalized_ids`, review → `completed`. |
| 10 | Active-review query includes quarantined review | CODE | `periodic_review_management.py:331` — active filter includes `'completion_pending_memo'` (fail-closed visibility; quarantined reviews stay in the work queue). |
| 11 | Completed/terminal filter excludes quarantined review | CODE | `periodic_review_projection_service.py:30` — `TERMINAL_QUEUE_STATUSES = {"completed","cancelled","canceled"}` deliberately omits `completion_pending_memo`; used at lines 219, 456 to mark terminal/can-take-action. |
| 12 | Audit events present | CODE | `periodic_review_engine.py:2446` `periodic_review.completion_pending_memo`; `:2679` `periodic_review.completion_finalized`; `periodic_review_memo.py:818` `periodic_review.memo_generation_quarantined`; CloudWatch metric logs `periodic_review_memo_gate` / `periodic_review_memo_quarantine` (`memo.py:726,730,735`); loud health alert after `MEMO_QUARANTINE_ALERT_THRESHOLD` (`memo.py:61`). Exercised end-to-end by the integration tests in rows 3–9. |
| 13 | Closure report | DOC | `closure_report.md`. |

## Staleness (P1-MEMO2, folded into C2)

| Item | Class | Proof |
|------|-------|-------|
| Memo flagged stale after later review mutation | TEST | `TestMemoGateQuarantine::test_staleness_flag_when_review_mutated_after_memo` — GET memo returns `staleness.is_stale == True` once `state_changed_at` post-dates the memo. |

## Re-entrancy / safety

| Item | Class | Proof |
|------|-------|-------|
| Quarantined review cannot be re-completed | TEST | `TestMemoGateQuarantine::test_re_completion_of_quarantined_review_is_blocked` — second `POST …/complete` → `409`. |
| Happy path still generates memo + completes | TEST | `TestCompleteHandlerHook::test_outcome_recorded_generates_memo_row` — `memo.status == "generated"`, version 1. |
| Generator never touches `compliance_memos` | TEST | `TestGenerator::test_compliance_memos_never_written`; `TestEDDIntegrationIsolation`. |
| Deterministic (zero AI calls) | TEST | `TestDeterminism::test_no_ai_client_calls_during_generation`. |
