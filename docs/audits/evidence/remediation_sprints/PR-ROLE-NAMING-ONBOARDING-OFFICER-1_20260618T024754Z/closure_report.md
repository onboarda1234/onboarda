# PR-ROLE-NAMING-ONBOARDING-OFFICER-1 Closure Report

Timestamp: 2026-06-18T02:47:54Z

Status: **Not closed**

Recorded status update: 2026-06-18T02:53:06Z

Latest evidence update: 2026-06-18T03:06:20Z

Operating status: **IMPLEMENTED / PR READY / NOT CLOSED**

## Summary

Renamed user-facing `Compliance Officer` role labels to `Onboarding Officer` while retaining the internal `co` role key and existing permission/approval logic. `Senior Compliance Officer` and `SCO` wording remain in place.

Current blockers are only closure gates. If PR review requests permission, authority, approval-gate, or role-matrix behavior changes, those changes are out of scope for this PR and should move to the later authority-matrix PR.

## Current Closure Blockers

1. CI must pass after the narrow test-harness fix.
2. PR #527 must be merged.
3. Staging must deploy from merged main.
4. Authenticated `/api/version.git_sha` and `image_tag` must match the merge SHA.
5. API smoke must pass.
6. Browser smoke must cover user management role label, application assignment/reassignment label, audit trail role display, and no permission change.
7. Evidence and closure report must be updated after merge/deploy/smoke evidence is available.

## Closure Gate Status

| Gate | Status | Evidence |
| --- | --- | --- |
| PR marked ready | Complete | PR #527 was marked ready on 2026-06-18. |
| PR merged to main | Not complete | PR #527 remains open pending a passing CI run. |
| Staging deployed from merged main | Not complete | No merge/deploy performed. |
| Authenticated `/api/version` `git_sha` and `image_tag` match merge SHA | Not complete | No merged SHA or staging deploy exists yet. |
| CI passes | Not complete | GitHub `lint-and-test` run `27733581288` failed in two audit runtime tests because the isolated Node harness missed `formatRoleLabel`. A test-only harness fix was applied and verified locally; CI must pass on the pushed fix. |
| API smoke passes | Not complete | Local focused API tests passed; staging API smoke not run. |
| Browser smoke: user management role label | Not complete | Not run pre-merge. |
| Browser smoke: application assignment/reassignment label | Not complete | Not run pre-merge. |
| Browser smoke: audit trail role display | Not complete | Not run pre-merge. |
| Browser smoke: no permission change | Not complete | Not run pre-merge. |
| Evidence folder complete | Complete for pre-merge PR evidence | This folder contains inventory, tests, closure report, and summary JSON. |
| Closure report complete | Complete for pre-merge PR evidence | This file. |

## Tests Run

See `tests.md`.

## CI Failure Follow-Up

GitHub Actions run `27733581288`, job `82045590231`, failed in `Run tests with coverage`.

Observed failure:

- `tests/test_inline_screening_runtime.py::TestInlineScreeningRuntime::test_activity_log_formats_screening_reviews_for_officers`
- `tests/test_inline_screening_runtime.py::TestInlineScreeningRuntime::test_activity_log_filters_and_unknown_fallback_are_safe`
- Runtime error: `ReferenceError: formatRoleLabel is not defined`

Resolution:

- Added the display-only `ROLE_LABELS` / `formatRoleLabel` helper to the isolated audit activity Node harness.
- Added an assertion that the audit card renders `Aisha Sudally · Onboarding Officer`.
- Added an assertion that the old `Compliance Officer` label is absent from the rendered audit HTML.
- No permission, approval-gate, workflow, role-key, or role-matrix code was changed.

## Staging Deploy Proof

Not available. This branch has not been merged or deployed to staging.

## Authenticated `/api/version`

Not available. No staging deployment was performed for this pre-merge PR state.

## API Smoke Summary

Not run on staging. Local focused API tests passed for the unchanged pre-approval/reassignment gates and role-matrix structure.

## Browser Smoke Summary

Not run. No screenshots were produced.

## Residual Issues

| Classification | Issue |
| --- | --- |
| Blocker for closure | PR must be merged to main. |
| Blocker for closure | Staging must deploy from merged main. |
| Blocker for closure | Authenticated `/api/version` must prove `git_sha` and `image_tag` match the merge SHA. |
| Blocker for closure | CI must pass after the test-harness fix. |
| Blocker for closure | Staging API smoke must pass. |
| Blocker for closure | Browser smoke must cover user management role label, application assignment/reassignment label, audit trail role display, and no permission change. |
| Non-blocking for PR | System Python 3.9 cannot run import-heavy repo tests; repo requires Python >=3.11 and tests passed with `python3.11`. |

## Closure Decision

Do not mark closed. This evidence supports the PR implementation only; final closure requires merge, staging deployment, authenticated version proof, CI, API smoke, and browser smoke.
