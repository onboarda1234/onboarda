# PR Closure Template

## PR name

`PR-4 - Screening Truth Safe Readiness Labels`

## Linked remediation IDs

- `FSI-007`

## Original issue summary

Screening truth summary could communicate approval-ready semantics while screening still blocked approval, especially for terminal `completed_match` results that had not been cleared by an officer with evidence.

## Re-diagnosis result

- Current `origin/main` SHA: `e61800bedc61752885313ab9e70718f6c4a021f3`
- Branch name: `codex/pr4-screening-truth-safe-readiness-labels`
- Branch commit SHA: pending
- Does the issue still exist on current `origin/main`? Yes
- Evidence: `diagnosis.md`, `runtime_json/local_screening_truth_semantic_check.json`

## Root cause

`build_screening_truth_summary()` and the back-office fallback `deriveScreeningTruthSummary()` treated terminal `completed_match` as approval-ready, while unresolved hits still produced approval blockers. UI consumers used the ambiguous `approval_ready` alias instead of explicit blocker semantics.

## Files changed

- `arie-backend/screening_state.py`
- `arie-backend/memo_handler.py`
- `arie-backend/security_hardening.py`
- `arie-backoffice.html`
- `arie-backend/tests/test_screening_state_priority_a.py`
- `arie-backend/tests/test_screening_clearance_validation_supervisor.py`
- `arie-backend/tests/test_backoffice_ca_truthflow_static.py`
- `arie-backend/tests/test_case_command_centre_runtime.py`
- `docs/audits/evidence/remediation_sprints/PR-4_screening-truth-safe-readiness-labels_20260613T152509Z/*`

## Behaviour before fix

An uncleared live terminal screening match produced:

```text
approval_ready=true
approval_blocking=true
```

Back-office fallback logic could also derive the same state for terminal matches.

## Behaviour after fix

The screening summary separates:

- `screening_terminal`
- `screening_provider_clear`
- `defensible_clear`
- `screening_gate_ready`
- `approval_blocked_reasons`

The legacy `approval_ready` field is retained only as a safe alias for `screening_gate_ready`. It is false whenever screening blockers remain.

## Tests added/updated

- Added backend regression for uncleared completed match not being approval-ready.
- Updated false-positive clearance expectations so formally cleared matches are defensibly clear with evidence.
- Updated back-office static test to assert unsafe fallback expression is gone.
- Added Case Command Centre runtime test for an uncleared terminal match blocker.

## Targeted test results

Command:

```bash
PYTHONPATH=arie-backend pytest -q arie-backend/tests/test_screening_state_priority_a.py arie-backend/tests/test_screening_clearance_validation_supervisor.py arie-backend/tests/test_backoffice_ca_truthflow_static.py arie-backend/tests/test_case_command_centre_runtime.py
```

Result:

```text
117 passed in 3.47s
```

Frontend/static note:

```text
No configured frontend lint/build command exists. Back-office HTML/JS changes are covered by static and Node runtime tests, including CodeRabbit follow-up coverage for generic non-review screening blockers.
```

Regression commands:

```bash
PYTHONPATH=arie-backend pytest -q arie-backend/tests/test_pr1_client_api_boundary.py arie-backend/tests/test_pr1b_client_notification_boundary.py arie-backend/tests/test_pr3_terminal_record_gate_reconciliation.py
PYTHONPATH=arie-backend pytest -q arie-backend/tests/test_sprint35.py::TestLogout
```

Result:

```text
16 passed in 6.37s
13 passed in 1.45s
```

## Full suite results

Command:

```bash
PYTHONPATH=arie-backend pytest -q arie-backend/tests
```

Result:

```text
BLOCKED locally by native WeasyPrint/Pango CFFI segmentation fault during evidence_pack_export.py import.
GitHub CI must be used as authoritative full relevant backend suite evidence if it passes.
```

## Browser test results, if applicable

- Browser: pending merged-main staging validation
- URL: pending
- Role: back-office officer and client
- Steps: see `browser_smoke.md`
- Result: pending
- Screenshot path: pending

## Staging deploy evidence

- Merged main SHA: pending
- Deployment mechanism: GitHub Actions deploy-staging / ECS rolling update
- ECS/task/image evidence, if applicable: pending
- Deployed at: pending

## /api/version evidence

Endpoint:

```text
https://staging.regmind.co/api/version
```

Result:

```json
{
  "git_sha": "pending",
  "image_tag": "pending"
}
```

Verdict:

- [ ] `git_sha` equals merged main SHA
- [ ] `image_tag` equals merged main SHA

## API smoke test evidence

- Endpoint(s): pending staging application detail / screening queue checks
- Role/token type: back-office and client regression tokens
- Expected: no approval-ready state or copy when screening blockers remain
- Actual: pending
- Raw evidence path: `runtime_json/`

## Browser smoke test evidence, if applicable

- URL: pending
- Role: back-office officer, client
- Expected: screening guidance is non-contradictory; no client leak/regression
- Actual: pending
- Screenshot path: `screenshots/`
- Console/network notes: pending

## Screenshots/evidence folder path

`docs/audits/evidence/remediation_sprints/PR-4_screening-truth-safe-readiness-labels_20260613T152509Z/`

## Remaining risks

- Full local suite remains blocked by native WeasyPrint/Pango CFFI crash.
- Staging API and browser smoke are pending until the PR is merged and deployed.

## Items not closed by this PR

- `FSI-007` remains `PARTIALLY FIXED` until merged-main staging `/api/version`, API smoke, and browser smoke are complete.
- No other remediation item is closed by this PR.

## Final closure verdict

`PARTIALLY FIXED`

Rationale:

Branch implementation and local targeted/regression tests are complete. Closure requires merged-main staging deployment, `/api/version` SHA alignment, staging API smoke, browser smoke, and completed evidence.
