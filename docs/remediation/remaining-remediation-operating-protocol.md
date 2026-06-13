# Remaining Remediation Operating Protocol

This protocol governs every remaining RegMind remediation PR after the master remaining reconciliation on 2026-06-13.

PR-0 creates process controls only. It does not close any product defect.

## Source of Truth

GitHub `origin/main` is the source of truth for remediation work.

Every remediation PR must:

1. Start from latest `origin/main`.
2. Record the current `origin/main` SHA before diagnosis.
3. Diagnose the issue against current `origin/main`.
4. Implement the fix on a clean branch from current `origin/main`.
5. Open the PR against `main`.
6. Merge to `main` only after review and required tests.
7. Deploy merged `main` to staging.
8. Confirm staging `/api/version` equals the merged `main` SHA.
9. Run staging API and browser validation as applicable.
10. Mark an issue closed only if merged-main staging evidence proves closure.

Branch-level validation is useful but not sufficient for closure. Local validation is useful but not sufficient for closure. Staging validation against an old SHA is invalid.

Do not close issues using stale reports, old screenshots, local-only evidence, branch-only evidence, or historic staging behavior.

## Mandatory Lifecycle

Each remediation PR must follow this lifecycle:

1. Diagnose again from current `origin/main`.
2. Confirm whether the issue still exists.
3. Identify the exact root cause.
4. Implement the smallest safe fix.
5. Add or update regression tests.
6. Run targeted tests.
7. Run the full relevant backend test suite.
8. Run frontend/static checks where relevant.
9. Run frontend/browser testing where UI, client, officer, or workflow behavior is affected.
10. Deploy merged `main` to staging.
11. Confirm staging `/api/version` matches the merged `main` SHA.
12. Run staging API smoke tests.
13. Run staging browser smoke tests where applicable.
14. Save the evidence pack.
15. Only then mark the issue closed.

No remediation item may be marked complete merely because code changed or tests passed locally.

## Severity-Based Definition of Done

### P0 Definition of Done

A P0 issue is closed only when all of the following are true:

- Current `origin/main` diagnosis confirms the issue and root cause.
- The fix is minimal and directly tied to the root cause.
- Negative regression tests are added for security, authorization, session, data-boundary, and terminal-state defects.
- Targeted tests pass.
- The full relevant backend suite passes.
- Browser tests pass when a UI, portal, officer, approval, or workflow surface is affected.
- The PR is merged to `main`.
- Merged `main` is deployed to staging.
- Staging `/api/version` equals the merged `main` SHA.
- Staging API smoke tests prove the P0 failure no longer reproduces.
- Staging browser smoke tests prove affected user workflows behave correctly, if applicable.
- Evidence is saved in the standard evidence-pack folder.

### P1 Definition of Done

A P1 issue is closed only when all of the following are true:

- Current `origin/main` diagnosis confirms the issue state and root cause.
- Regression tests cover the corrected behavior and the prior failure mode.
- Targeted tests pass.
- The full relevant backend suite passes unless the issue is documentation-only and no code path is touched.
- Browser evidence is captured for affected officer, client, or supervisor workflows.
- Merged `main` is deployed to staging.
- Staging `/api/version` equals the merged `main` SHA.
- Staging API/browser smoke tests prove closure for the affected workflow.
- Remaining limitations are explicitly documented.

### P2/P3 Definition of Done

A P2 or P3 issue is closed only when all of the following are true:

- The issue is rechecked against current `origin/main`.
- The fix or disposition is documented.
- Appropriate targeted tests or static checks pass.
- Staging proof is captured when the issue is runtime-visible.
- Browser evidence is captured when the issue affects UI, portal, or officer workflows.
- The remediation tracker/report is updated with evidence level and closure rationale.

## Evidence Requirements

Every remediation PR must save an evidence pack under:

`docs/audits/evidence/remediation_sprints/<PR-ID>_<short-name>_<YYYYMMDDTHHMMSSZ>/`

Each pack should include applicable files:

- `diagnosis.md`
- `root_cause.md`
- `test_results.md`
- `full_suite_results.md`
- `staging_deploy.md`
- `api_smoke.md`
- `browser_smoke.md`
- `screenshots/`
- `runtime_json/`
- `closure_report.md`

Evidence must include command names, timestamps where useful, relevant SHA values, runtime URLs/endpoints tested, and a clear pass/fail result. Redact tokens, secrets, passwords, cookies, CSRF values, and provider credentials.

## Branch Naming Convention

Use:

`codex/pr<N>-<short-remediation-name>`

Examples:

- `codex/pr0-remediation-control-framework`
- `codex/pr1-security-client-api-boundary-hardening`
- `codex/pr2-auth-logout-token-revocation-enforcement`

## PR Naming Convention

Use:

`PR-<N> - <Title Case Remediation Name>`

Example:

`PR-1 - Security Client API Boundary Hardening`

The PR description must state:

- Linked remediation IDs.
- Whether the PR closes defects or only prepares process/evidence.
- Current `origin/main` SHA used for diagnosis.
- Branch commit SHA.
- Targeted and full-suite validation.
- Staging deployment SHA after merge.
- API/browser smoke evidence paths.
- Final closure verdict.

## Required Staging Validation

For every defect-closing PR:

1. Confirm the PR is merged to `main`.
2. Record the merged `main` SHA.
3. Deploy merged `main` to staging.
4. Authenticate using approved test credentials or approved secure mechanism.
5. Call `/api/version`.
6. Confirm `git_sha` and `image_tag` match the merged `main` SHA.
7. Run safe API smoke tests that prove the defect no longer reproduces.
8. Save raw redacted JSON responses under `runtime_json/`.
9. Record results in `api_smoke.md` and `closure_report.md`.

If staging does not match merged `main`, the issue cannot be closed.

## Required Browser Validation

Browser validation is mandatory when a PR affects:

- Client portal workflows.
- Back-office officer workflows.
- Application Review or Case Command Centre behavior.
- Screening Queue or screening cards.
- Identity Verification visibility.
- Memo generation, validation, supervisor, or approval workflows.
- Periodic review, monitoring, lifecycle, reports, roles, or user-management UI.
- Any button state, route, copy, action path, blocker display, or count visible to users.

Browser evidence must include:

- Tested URL.
- User role used.
- Browser steps.
- Expected behavior.
- Actual behavior.
- Screenshot path when visual state matters.
- Any console/network failures observed.

## Special Closure Rules

Runtime-verified issues must be revalidated at runtime before closure.

Browser-affecting issues require browser evidence before closure.

Security, authorization, authentication, session, tenant isolation, and client-boundary issues require negative tests and staging smoke tests proving forbidden access is denied.

Role issues require live validation with each affected role. Synthetic tests alone are not enough when the issue is about real role behavior.

Provider and integration issues require either live proof or explicit simulated/out-of-scope labelling. If a provider is simulated, unavailable, sandbox-only, or not configured, product and officer surfaces must say so clearly.

Terminal approved/rejected record issues require evidence that historical/legacy records are either valid under current gates or explicitly quarantined/labelled so officers cannot misread them.

Memo, supervisor, approval, and export issues require proof that the latest canonical memo state is used consistently in backend, UI, staging smoke tests, and export outputs where applicable.

## Closure Discipline

The remediation tracker may only mark an issue `CLOSED` when the closure report includes:

- Current `origin/main` diagnosis.
- Root cause.
- Files changed.
- Tests added or updated.
- Targeted test results.
- Full relevant suite results.
- Staging deploy evidence.
- `/api/version` evidence matching merged `main`.
- API smoke evidence.
- Browser smoke evidence if applicable.
- Remaining risks.
- Explicit final closure verdict.

If any required evidence is missing, use `PARTIALLY FIXED`, `BLOCKED / NEEDS EVIDENCE`, or `OPEN`. Do not use `CLOSED`.
