# PRS-1 Staging QA Validation Report

Date: 2026-06-05
Validation workspace: `/tmp/onboarda-prs1-case-shell`
Validation mode: authenticated staging deploy plus browser/API revalidation

## 1. Executive Verdict

**PASS WITH MINOR ISSUES**

PRS-1 is now deployed to staging and the deployed environment matches the PRS-1 commit under review.

Authenticated `/api/version` confirms the exact PRS-1 SHA and image tag, the live back-office now presents **Periodic Review Queue** instead of **Periodic Review Signals**, canonical queue fields are present in the authenticated review payload, queue filters materially work, and three live queue rows successfully routed into Application Detail / Lifecycle.

No P0 or P1 PRS-1 regressions were found.

Two non-blocking P2 issues were observed during adjacent regression checking:

- an existing generic back-office `404` console noise event still appears;
- the Screening Queue surface showed `Authentication required` text during the adjacent navigation sweep, which appears unrelated to PRS-1 and did not affect Periodic Review Queue validation.

## 2. Deployment Source Of Truth

- PRS-1 branch: `codex/prs1-case-shell-canonicalization`
- PRS-1 commit SHA: `0da6c9cc8b1b8f1751fce47f981aaf94c9686642`
- PRS-1 commit short SHA: `0da6c9c`
- Deployment workflow run: [GitHub Actions run 27009891151](https://github.com/onboarda1234/onboarda/actions/runs/27009891151)
- Workflow conclusion: `success`
- ECS task definition before deploy: `arn:aws:ecs:af-south-1:782913119880:task-definition/regmind-staging:426`
- ECS task definition after deploy: `arn:aws:ecs:af-south-1:782913119880:task-definition/regmind-staging:427`
- ECS service status after deploy:
  - desired count: `2`
  - running count: `2`
  - pending count: `0`
  - rollout state: `COMPLETED`
- `/api/version` git SHA: `0da6c9cc8b1b8f1751fce47f981aaf94c9686642`
- `/api/version` image tag: `0da6c9cc8b1b8f1751fce47f981aaf94c9686642`
- `/api/version` build time: `2026-06-05T10:43:46Z`
- `/api/version` environment: `staging`
- Did staging match PRS-1? **Yes**

## 3. Tests Run

### Focused local PRS-1 validation before deployment

Passed:

- `pytest -q arie-backend/tests/test_periodic_review_handlers.py`
- `pytest -q arie-backend/tests/test_periodic_review_phase1_handlers.py`
- `pytest -q arie-backend/tests/test_periodic_review_phase1_canonical.py`
- `pytest -q arie-backend/tests/test_monitoring_routing.py`
- `pytest -q arie-backend/tests/test_monitoring_enrollment.py`
- `pytest -q arie-backend/tests/test_backoffice_monitoring_navigation_static.py arie-backend/tests/test_application_lifecycle_tab_shell_static.py`
- `python3 -m py_compile arie-backend/periodic_review_projection_service.py arie-backend/server.py`

### Deployment workflow gates

Passed in workflow run `27009891151`:

- `ci / lint-and-test`
- `ci / pdf-tests`
- `ci / docker-validate`
- `deploy`

Important workflow timings:

- deploy job started: `2026-06-05T10:43:37Z`
- image build/push finished: `2026-06-05T10:44:36Z`
- new task definition registered: `2026-06-05T10:44:41Z`
- ECS rollout step completed: `2026-06-05T10:47:57Z`
- deployment job completed: `2026-06-05T10:48:07Z`

## 4. Browser And API Validation

Browser automation used a Playwright fallback because the in-app browser control runtime was not callable in this session.

### Product framing

Confirmed in live authenticated staging:

- nav label: `Periodic Review Queue`
- page heading: `Periodic Review Queue`
- queue description: `Officer queue for canonical periodic review cases with due-date, owner, status, and trigger truth.`
- supporting copy: `Monitoring Alerts remains the signal workspace. Review execution, attestations, evidence, memo, and completion stay in the Application Lifecycle workspace.`
- Monitoring Alerts remains separately labelled as `Monitoring Alerts`

Primary screenshot set:

- [01-periodic-review-queue.png](/tmp/regmind-prs1-postdeploy/01-periodic-review-queue.png)
- [02-overdue-filter.png](/tmp/regmind-prs1-postdeploy/02-overdue-filter.png)
- [03-monitoring-alerts.png](/tmp/regmind-prs1-postdeploy/03-monitoring-alerts.png)
- [report.json](/tmp/regmind-prs1-postdeploy/report.json)

### Queue contract

Authenticated API validation confirmed staging now exposes canonical PRS-1 fields directly on review rows and inside nested `projection`.

Observed top-level fields on live rows include:

- `application_ref`
- `client_name`
- `risk_level`
- `queue_status`
- `queue_status_label`
- `due_state`
- `is_overdue`
- `is_due_date_missing`
- `owner_display_name`
- `assigned_officer_name`
- `last_activity_at`
- `updated_at`
- `audit_reference`
- `primary_action_label`

Observed nested `projection` fields include:

- `review_reference`
- `queue_status`
- `queue_status_label`
- `due_state`
- `is_overdue`
- `is_due_date_missing`
- `owner_display_name`
- `assigned_officer_name`
- `last_activity_at`
- `updated_at`
- `audit_reference`
- `primary_action_label`

Sample live row after deployment:

- review reference: `PR-5`
- application ref: `ARF-2026-900011`
- client: `FIX-SCEN11 Agent6 RichPrep Holdings Ltd`
- risk: `VERY_HIGH`
- queue status: `overdue`
- queue status label: `Overdue`
- due state: `overdue`
- overdue: `true`
- owner display: `Marie Dubois`
- last activity: `2026-05-19 06:34:44`
- action: `Open review case`
- audit reference: `periodic_review:5`

Visible browser table columns matched PRS-1 framing:

- `Client`
- `Risk Level`
- `Due Date`
- `Case Status`
- `Owner`
- `Last Activity`
- `Action`

### Filters

Authenticated API filter results after deployment:

- `GET /api/monitoring/reviews` → `41` rows
- `GET /api/monitoring/reviews?queue=overdue` → `1` row
- `GET /api/monitoring/reviews?queue=open` → `32` rows
- `GET /api/monitoring/reviews?queue=in_review` → `2` rows
- `GET /api/monitoring/reviews?assigned_to_me=true` → `0` rows

Observed queue-status distribution from the live unfiltered payload:

- `overdue`: `1`
- `in_review`: `2`
- `completed`: `9`
- `open`: `29`

Browser-side queue filtering also behaved materially:

- setting the status filter to `overdue` reduced the visible queue table to `1` row

### Routing

Three real queue rows were opened through the live Periodic Review Queue action, and all three landed in Application Detail with the Lifecycle tab visible:

1. `PR-5` → `ARF-2026-900011`
2. `PR-41` → `ARF-PR4-AUTO-7f861903`
3. `PR-14` → `ARF-2026-MON311-05151513094D77-MEDIUM`

For each route:

- active view became `view-app-detail`
- `currentAppRef` matched the clicked queue row target
- Lifecycle panel was visible
- no dead end or broken tab was hit

Routing evidence:

- [route-1.png](/tmp/regmind-prs1-postdeploy/route-1.png)
- [route-2.png](/tmp/regmind-prs1-postdeploy/route-2.png)
- [route-3.png](/tmp/regmind-prs1-postdeploy/route-3.png)

### Regression surfaces checked

Validated directly:

- Monitoring Alerts view loaded and remained separately labelled
- Application Detail loaded from Periodic Review Queue actions
- Lifecycle tab loaded from Periodic Review Queue actions
- Change Management view activated successfully
- Lifecycle Queue view activated successfully
- Audit access for the `sco` QA role still returned `200` at `GET /api/audit?limit=1`

Visible-surface evidence:

- [change-mgmt.png](/tmp/regmind-prs1-surface-check/change-mgmt.png)
- [lifecycle.png](/tmp/regmind-prs1-surface-check/lifecycle.png)
- [monitoring.png](/tmp/regmind-prs1-surface-check/monitoring.png)

Screening Queue note:

- the underlying surface activated via `showView('screening')`
- the live view displayed `Authentication required` text while still rendering the queue shell
- this appears adjacent and unrelated to PRS-1, but it is recorded below as a P2 finding
- evidence: [screening.png](/tmp/regmind-prs1-surface-check/screening.png)

### Console and network

Observed during browser validation:

- one console error persisted:
  - `Failed to load resource: the server responded with a status of 404 ()`
- no unexpected `500` responses were captured during the PRS-1 queue walkthrough
- no unexpected `403` responses were captured during the PRS-1 queue walkthrough
- no Periodic Review Queue route `404` was observed

Additional note on the broad smoke harness:

- `arie-backend/scripts/qa/staging_browser_smoke.js` authenticated successfully and loaded the Applications page, but its follow-on row click failed because another rendered element intercepted pointer events during the scripted click path
- this is not treated as a PRS-1 product regression because Periodic Review routing into Application Detail / Lifecycle succeeded repeatedly in the targeted validation
- supporting artifact: [report.json](/tmp/regmind-staging-browser-smoke-prs1/report.json)

## 5. Issues Found

### P2 — Screening Queue shows auth-required text during adjacent regression sweep

- Severity: `P2`
- Classification: adjacent staging issue, not evidenced as a PRS-1 regression
- Expected behavior:
  - Screening Queue should render its authenticated queue content cleanly for the staging QA `sco` role
- Actual behavior:
  - the `view-screening` surface activated, but visible text included `Authentication required`
- Reproduction steps:
  1. Authenticate to staging back office as the QA `sco` user
  2. Activate the Screening Queue surface (`showView('screening')`)
  3. Observe the queue shell text
- Evidence:
  - [screening.png](/tmp/regmind-prs1-surface-check/screening.png)
- Blocks PRS-2:
  - **No**

### P2 — Existing generic back-office 404 console noise persists

- Severity: `P2`
- Classification: existing unrelated staging noise
- Expected behavior:
  - no browser console resource failures during routine navigation
- Actual behavior:
  - a generic `404` resource error still appears during authenticated browser validation
- Reproduction steps:
  1. Authenticate to staging back office
  2. Navigate the shell normally
  3. Inspect the browser console
- Evidence:
  - captured in [report.json](/tmp/regmind-prs1-postdeploy/report.json)
- Blocks PRS-2:
  - **No**

## 6. Final Recommendation

Can PRS-1 be considered fully validated?

- **Yes, with minor adjacent staging issues noted above**

Can PRS-2 start?

- **Yes**

Exact conclusion:

- staging is aligned to the PRS-1 patchset
- `/api/version` and the ECS task definition match the deployed PRS-1 SHA
- Periodic Review Queue is live in the authenticated browser
- canonical PRS-1 queue fields are present in the live API and visible in the queue UX
- queue filters work materially
- routing into Application Detail / Lifecycle works
- no P0/P1 PRS-1 regressions were found

Follow-up recommended, but not blocking PRS-2:

1. clean up the generic back-office `404` console noise
2. investigate the adjacent Screening Queue `Authentication required` rendering for the `sco` role
