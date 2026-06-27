# CM-E2E-PILOT-READINESS-1 Report

## 1. Executive verdict

PASS

Validation-only E2E completed against AWS staging. No code was changed, no PR was opened, no production data was mutated, no ComplyAdvantage production workspace was tested, and no uncontrolled live provider screening was run.

Run prefix: `CME2E-20260626-PILOT-READINESS-1-1782495506778`
Browser/API run: `2026-06-26T17:38:26.778Z` to `2026-06-26T17:39:16.770Z` UTC

## 2. Audited origin/main SHA

`27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`

Source-of-truth sync was performed from a fresh checkout under `/tmp/regmind-cm-e2e-pilot-readiness/source` because the Desktop worktree was not readable by the shell. Required commands were run against the fresh checkout:

- `git fetch origin`
- `git checkout main`
- `git reset --hard origin/main`
- `git status --short` returned empty output
- `git rev-parse HEAD` = `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- `git rev-parse origin/main` = `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`

## 3. Deployed staging SHA

- Authenticated `/api/version.git_sha`: `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- Authenticated `/api/version.image_tag`: `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- ECS backend task image tag: `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- ECS worker task image tag: `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- ECS backend env `GIT_SHA` / `IMAGE_TAG`: `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb` / `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- ECS worker env `GIT_SHA` / `IMAGE_TAG`: `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb` / `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- Runtime alignment with `origin/main`: `true`

Health/version evidence:

- `/tmp/regmind-cm-e2e-pilot-readiness/runtime/health.json`
- `/tmp/regmind-cm-e2e-pilot-readiness/runtime/liveness.json`
- `/tmp/regmind-cm-e2e-pilot-readiness/runtime/version.json`
- `/tmp/regmind-cm-e2e-pilot-readiness/runtime/runtime_baseline.json`

## 4. Browser automation method

Playwright Core with local Chrome was used against `https://staging.regmind.co`. The harness captured required screenshots, console events, page errors, failed browser requests, browser-observed 4xx/5xx responses, HAR, and API before/after JSON.

Artifacts:

- Harness: `/tmp/regmind-cm-e2e-pilot-readiness/cm-e2e-harness.js`
- Summary: `/tmp/regmind-cm-e2e-pilot-readiness/cm-e2e-harness-summary.json`
- HAR: `/tmp/regmind-cm-e2e-pilot-readiness/browser/cm-e2e.har`

## 5. Users/roles

- Primary back-office officer/admin path: real staging QA officer login through the back-office UI form; role `sco`; credential keys `STAGING_QA_EMAIL` and `STAGING_QA_PASSWORD` were read from the approved staging secret source; raw credential values and tokens were not written.
- Secondary maker-checker approver path: documented short-lived staging smoke token; role `sco`; token value not written.
- Lower-privileged checks: short-lived staging JWTs minted for active staging DB users `analyst001` and `co001`; token values not written.
- Client access check: client token used only to verify Back Office CM denial.

Role matrix artifact: `/tmp/regmind-cm-e2e-pilot-readiness/user-role-matrix.json`

## 6. Alerts, requests, applications tested

Fixture applications/entities:

- lowClean: `ARF-2026-900350` / `814067abc14246ee` / RegMind E2E 20260617T175336Z C01 PR2 Low Clean Ltd
- screeningMatch: `ARF-2026-900356` / `49f4d6133d374ed7` / RegMind E2E 20260617T175336Z C07 PR2 Screening Match Ltd
- highRisk: `ARF-2026-900172` / `0ba87749ffdd4bf1` / Pilot CryptoEdd 20260520052312 Ltd
- propagation: `ARF-2026-900336` / `b0da61396eec46fb` / RegMind E2E 20260617T143757Z C01 Low Risk Local Ltd

Alerts created/used:

- Scenario 2 convert: `CA-260626-36F1B7E0` on `ARF-2026-900350`
- Scenario 3 dismiss: `CA-260626-CC7AFF6A` on `ARF-2026-900350`
- Scenario 4 escalate: `CA-260626-B73361FB` on `ARF-2026-900172`

Requests created/used:

- Scenario 2 converted request: `CR-260626-419125FD` from alert `CA-260626-36F1B7E0`
- Scenario 5 intake request: `CR-260626-BB6A4DCF`
- Scenario 6/8/9/10 ready material request: `CR-260626-DA6DD22E`
- Scenario 7 screening/risk request: `CR-260626-0E0233F2`
- Scenario 9 blocked implementation request: `CR-260626-7C5E7892`

## 7. Scenario-by-scenario result table

| # | Scenario | Result | Key evidence |
|---|---|---|---|
| 1 | CM dashboard and unified queue smoke | PASS | Dashboard cards, unified queue, filters/toggles, no old Stats main surface, clean browser logs. |
| 2 | New alert triage: create change request | PASS | Alert `CA-260626-36F1B7E0` converted to request `CR-260626-419125FD`; duplicate conversion safe. |
| 3 | New alert triage: dismiss alert | PASS | Status `new` to `dismissed`; no request created. |
| 4 | New alert triage: escalate alert | PASS | Alert `CA-260626-B73361FB` escalated and queue state updated. |
| 5 | Client/officer-originated intake | PASS | Request `CR-260626-BB6A4DCF` appeared in queue; old/new diff visible. |
| 6 | Evidence and Agent 1 readiness gate | PASS | Request `CR-260626-DA6DD22E` blocked before evidence; upload `201`, Agent 1 verify `200`. |
| 7 | Screening/risk readiness gate | PASS | Request `CR-260626-0E0233F2` approval blocked `409` with blockers cm_evidence_not_linked, screening_unresolved_match. |
| 8 | Approval workflow and blocked approval | PASS | Blocked request returned `409`; ready request `CR-260626-DA6DD22E` approved `200`; repeat approval `400`. |
| 9 | Implementation workflow | PASS | Blocked implementation `400`; ready implementation `200`; repeat implementation `200`; profile name updated. |
| 10 | Audit reconstruction and lifecycle trace | PASS | Request `CR-260626-DA6DD22E` timeline captured 13 actions and 1 evidence item. |

## 8. Dashboard/unified queue result

PASS. Dashboard cards rendered: `6`. Unified queue rows visible: `72`. The old weak Stats tab was not the main surface: `false`.

Filter counts observed:

- all work: `72`
- alerts: `22`
- requests: `50`
- blocked: `2`
- pending approval: `23`
- ready for implementation/review: `7`
- implemented/closed: `13`

Screenshots: `cm-01-dashboard.png`, `cm-02-unified-queue.png`, `cm-filter-*.png`.

## 9. Alert triage result

PASS.

- Convert: alert `CA-260626-36F1B7E0` converted to request `CR-260626-419125FD`; duplicate conversion was blocked or idempotent.
- Dismiss: alert transitioned `new` to `dismissed` with no accidental request creation.
- Escalate: alert `CA-260626-B73361FB` transitioned to escalated and queue state reflected it.
- Audit reconstruction existed for conversion.

Evidence: `scenario2-*`, `scenario3-*`, `scenario4-*` JSON files and screenshots `cm-03` through `cm-06`.

## 10. Change request intake result

PASS. Officer-originated legal-name request `CR-260626-BB6A4DCF` entered the unified queue, detail opened correctly, old/requested values were clearly represented, source was preserved, and no broken buttons were observed.

Evidence: `/tmp/regmind-cm-e2e-pilot-readiness/api/scenario5-intake-request-detail-created.json`, screenshot `cm-07-request-detail-old-new-diff.png`.

## 11. Evidence/Agent 1 result

PASS. Request `CR-260626-DA6DD22E` requiring evidence was blocked before evidence/preconditions were satisfied. Dummy safe evidence upload returned `201` and Agent 1/evidence verification path returned `200`. Readiness cleared only after valid evidence/precondition evidence was recorded. Audit reconstruction includes the uploaded evidence item.

Evidence: `scenario6-*` API artifacts and screenshot `cm-08-evidence-agent-readiness.png`.

## 12. Screening/risk result

PASS. Request `CR-260626-0E0233F2` required screening/risk readiness. Direct approval was blocked with status `409` and blocker codes `cm_evidence_not_linked, screening_unresolved_match`. A high/material concern remained blocked after unresolved screening/risk evidence was recorded.

Evidence: `scenario7-*` API artifacts and screenshot `cm-09-screening-risk-readiness.png`.

## 13. Approval result

PASS. Blocked approval returned controlled status `409`. Ready request `CR-260626-DA6DD22E` approved with status `200` after evidence, screening, and risk prerequisites were satisfied. Repeat approval returned controlled status `400`. Implementation did not bypass the approval checks.

Evidence: `scenario8-*` API artifacts and screenshots `cm-10-approval-blocked.png`, `cm-11-approval-success.png`.

## 14. Implementation result

PASS. Unsafe implementation was blocked with controlled status `400`. Ready implementation succeeded with status `200` and repeat implementation returned `200` idempotently. Profile data changed from `RegMind E2E 20260617T143757Z C01 Low Risk Local Ltd` to `CME2E-20260626-PILOT-READINESS-1-1782495506778 Evidence Legal Name Ltd`.

Evidence: `scenario9-*` API artifacts and screenshots `cm-12-implementation-blocked.png`, `cm-13-implementation-success.png`.

## 15. Audit reconstruction result

PASS. Audit reconstruction for `CR-260626-DA6DD22E` opened successfully and included source/request creation, old/new values, evidence, screening/risk preconditions, officer decisions, implementation, timestamps, and actors.

Timeline actions captured:

- Change Request Created
- Change Request Submitted
- Change Request Status Updated
- Change Request Status Updated
- Change Request Status Updated
- CM Approval Blocked
- Change Request Document Uploaded
- CM Approval Blocked
- CM Precondition Recorded
- CM Precondition Recorded
- Change Request Approved
- Change Request Implemented
- CM Implementation Idempotent Reuse

Evidence: `/tmp/regmind-cm-e2e-pilot-readiness/api/scenario10-lifecycle-trace-audit-reconstruction.json`, screenshot `cm-14-audit-reconstruction.png`.

## 16. Role/access result

PASS.

- Unauthenticated `/change-management/requests`: expected `401`, got `401`.
- Client `/change-management/requests`: expected `401/403`, got `403`.
- Analyst list `/change-management/requests`: expected `200`, got `200`.
- Analyst approve `CR-260626-0E0233F2`: expected `403`, got `403`.
- Analyst implement `CR-260626-DA6DD22E`: expected `403`, got `403`.
- CO implement `CR-260626-DA6DD22E`: expected `403`, got `403`.
- Officer identity was recorded in audit reconstruction for creation, approval, preconditions, and implementation.

## 17. Browser console/page/network findings

PASS.

- Console events: `0` (`/tmp/regmind-cm-e2e-pilot-readiness/browser/console-events.json`)
- Page errors: `0` (`/tmp/regmind-cm-e2e-pilot-readiness/browser/page-errors.json`)
- Failed browser requests: `0` (`/tmp/regmind-cm-e2e-pilot-readiness/browser/failed-requests.json`)
- Browser-observed 4xx/5xx responses: `0` (`/tmp/regmind-cm-e2e-pilot-readiness/browser/responses-4xx-5xx.json`)
- HAR captured: `/tmp/regmind-cm-e2e-pilot-readiness/browser/cm-e2e.har`

## 18. CloudWatch/log scan result

PASS. Reviewed CloudWatch window: `2026-06-26T17:18:26.778000+00:00` to `2026-06-26T17:59:16.770000+00:00` against `/ecs/regmind-staging` in `af-south-1`. Events scanned: `3421`.

- HTTP 5xx: `0`
- Unexpected 4xx/409/400: `0`
- Expected controlled 400/403/409 negative checks: `11`
- Unexpected exceptions: `0`
- DB errors: `0`
- Duplicate conversion errors: `0`
- Worker crashes: `0`
- Mock fallback: `0`
- Failed traces: `0`

One deliberate unsafe-field implementation negative test emitted an `ERROR`/traceback and returned controlled `400`; it is recorded as controlled exception evidence, not an unhandled backend failure. Optional log-hygiene cleanup is noted below.

Evidence:

- `/tmp/regmind-cm-e2e-pilot-readiness/logs/cloudwatch-summary-reviewed.json`
- `/tmp/regmind-cm-e2e-pilot-readiness/logs/cloudwatch-error-candidates.json`
- `/tmp/regmind-cm-e2e-pilot-readiness/logs/cloudwatch-traceback-context.json`
- `/tmp/regmind-cm-e2e-pilot-readiness/logs/cloudwatch-run-prefix-events.json`

## 19. Defect list

P0 blocker: none.

P1 pilot issue: none.

P2 polish: none blocking pilot readiness.

Non-blocking observation: the controlled unsafe-field implementation denial logs an `ERROR` and stack trace even though the API returns a controlled `400`. This did not weaken backend gates, corrupt state, or affect the happy path.

## 20. Recommended PRs

No required PR is needed for pilot readiness.

Optional follow-up PR: downgrade expected implementation validation denials from `ERROR` plus traceback to a warning-level validation log without stack trace, so operational log scans stay cleaner.

## 21. Evidence index

- Test plan: `/tmp/regmind-cm-e2e-pilot-readiness/CM-E2E-PILOT-READINESS-1-test-plan.md`
- Runtime health/liveness/version/ECS evidence: `/tmp/regmind-cm-e2e-pilot-readiness/runtime/`
- Application/entity refs: `/tmp/regmind-cm-e2e-pilot-readiness/application-entity-refs.json`, `/tmp/regmind-cm-e2e-pilot-readiness/application-entity-refs-approved.json`
- User/role matrix: `/tmp/regmind-cm-e2e-pilot-readiness/user-role-matrix.json`
- Browser screenshots: `/tmp/regmind-cm-e2e-pilot-readiness/screenshots/`
- Required screenshot names present: `true`
- Browser logs/HAR: `/tmp/regmind-cm-e2e-pilot-readiness/browser/`
- API before/after JSON: `/tmp/regmind-cm-e2e-pilot-readiness/api/`
- Evidence/document JSON: `/tmp/regmind-cm-e2e-pilot-readiness/api/scenario6-*`
- Risk/screening JSON: `/tmp/regmind-cm-e2e-pilot-readiness/api/scenario7-*`, `/tmp/regmind-cm-e2e-pilot-readiness/api/scenario8-ready-material-precondition-*`
- Approval gate responses: `/tmp/regmind-cm-e2e-pilot-readiness/api/scenario8-*approval-response.json`
- Implementation before/after JSON: `/tmp/regmind-cm-e2e-pilot-readiness/api/scenario9-*`
- Audit reconstruction evidence: `/tmp/regmind-cm-e2e-pilot-readiness/api/scenario10-lifecycle-trace-audit-reconstruction.json`
- CloudWatch/log scan output: `/tmp/regmind-cm-e2e-pilot-readiness/logs/`
- Defect list: this report, section 19

Required screenshots:

- `cm-01-dashboard.png` - present
- `cm-02-unified-queue.png` - present
- `cm-03-alert-detail-new.png` - present
- `cm-04-alert-converted.png` - present
- `cm-05-alert-dismissed.png` - present
- `cm-06-alert-escalated.png` - present
- `cm-07-request-detail-old-new-diff.png` - present
- `cm-08-evidence-agent-readiness.png` - present
- `cm-09-screening-risk-readiness.png` - present
- `cm-10-approval-blocked.png` - present
- `cm-11-approval-success.png` - present
- `cm-12-implementation-blocked.png` - present
- `cm-13-implementation-success.png` - present
- `cm-14-audit-reconstruction.png` - present

## 22. Final co-founder verdict

CM ready for pilot scope.

The Change Management module supports officer management of profile changes from alert/request intake through review, evidence, screening/risk readiness, approval, implementation, and regulator-grade audit reconstruction without approval bypass, implementation bypass, duplicate active conversion, wrong profile data, unauthorized approve/implement access, or missing critical audit evidence in the tested staging scope.
