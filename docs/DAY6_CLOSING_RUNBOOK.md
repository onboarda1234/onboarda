# Day 6 Closing Runbook

Scope: Day 6 hardening closure for staging, after the Day 5 reporting/KPI truthfulness work has landed on `main`.

This runbook is intentionally staging-only. PR #210 remains open and out of scope; do not merge, close, rebase, or retarget it as part of Day 6 closure.

## Merge Order

Merge the Day 6 PRs only after each PR is individually reviewed and all required checks are green:

1. **Temp DB import-order isolation** - stabilizes test database module state after early imports.
2. **Staging smoke workflow** - adds a manual, staging-scoped GitHub Actions smoke entrypoint with short-lived token handling.
3. **Browser KPI/export runtime validation** - executes real extracted back-office JavaScript for KPI and CSV export behavior.
4. **Deployment observability closure** - pins the deployment evidence ledger in `docs/DEPLOYMENT_RUNBOOK.md`.
5. **Day 6 closing runbook** - this document and its guard tests.

If a later PR must be merged before an earlier one, re-run the later PR's full CI after rebasing and explicitly confirm the changed ordering in the review note.

## Deploy To Staging

After the approved Day 6 PRs are merged to `main`, deploy the latest `main` SHA through the existing staging deployment workflow.

Record these values before marking the deployment complete:

- GitHub Actions deploy run URL and run number.
- Deployed `main` SHA.
- `/api/version` response, including `git_sha`, `image_tag`, and `build_time`.
- ECS cluster/service: `regmind-staging` / `regmind-backend`.
- New and previous ECS task definitions.
- CloudWatch log group: `/ecs/regmind-staging`.

The deployment is not closed until `/api/version` returns the reviewed `main` SHA and the ECS service reports steady state.

## Run The Smoke Harness

Use the Day 5 smoke harness against staging after the new task definition reaches steady state:

```bash
BACKOFFICE_TOKEN="$STAGING_BACKOFFICE_TOKEN" \
python3 arie-backend/scripts/qa/day5_closing_smoke.py \
  --api-base https://staging.regmind.co/api \
  --expected-sha "$GIT_SHA" \
  --expected-total 22 \
  --expected-pending 21 \
  --expected-edd 1
```

Expected result:

```json
{"ok": true}
```

The smoke output must show:

- analytics reconciliation holds: `pending + edd_required + approved + rejected + withdrawn == total`;
- `/api/dashboard` in-progress counts match `/api/reports/analytics.summary.pending`;
- CSV export row count matches `X-Report-Record-Count`;
- `canonical_view` is `applications_report_v1`;
- application-derived pending/EDD counts match the backend metadata contract.

Do not pass bearer tokens on the command line. Use `BACKOFFICE_TOKEN` or `--token-env`.

## Manual Browser Gates

Run these once per staging deploy:

1. Back-office login succeeds for an Administrator.
2. Dashboard "In Progress" tile equals the analytics pending count.
3. KPI "In Progress Applications" equals the dashboard in-progress count.
4. KPI "EDD Routing Rate" shows the expected EDD count and does not regress to zero.
5. Reports CSV downloads through `/api/reports/generate?format=csv`.
6. KPI CSV downloads through `/api/reports/generate?format=csv`.
7. Both CSV responses expose `X-Report-Record-Count`, `X-Report-Field-List`, `X-Report-Filename`, and `X-Report-Canonical-View`.
8. Browser console is clean on Dashboard, KPI Dashboard, and Reports.

## Rollback Signals

Rollback to the previous `regmind-staging:<REVISION>` if any of these occur:

- `/api/version` does not match the deployed SHA after ECS steady state;
- smoke harness returns `ok: false`;
- analytics lifecycle reconciliation fails;
- dashboard and reports pending counts diverge;
- EDD routing count regresses to zero while staging still has an EDD-routed application;
- CSV row count disagrees with `X-Report-Record-Count`;
- CloudWatch shows new `connection pool exhausted`, `falling back to mock mode`, or repeated 5xx errors after deployment.

Rollback does not roll back database migrations. If the failed deploy included a migration, assess backward compatibility before restoring the previous task definition.

## Closure Note Template

Use this shape for the final Day 6 closure comment:

```markdown
Day 6 closed on staging.

- Deployed SHA: <sha>
- Deploy workflow: <url>
- ECS task definition: regmind-staging:<revision>
- Previous rollback target: regmind-staging:<previous_revision>
- /api/version git_sha: <sha>
- Smoke harness: ok=true, total=22, pending=21, edd=1
- Browser gates: Dashboard, KPI Dashboard, Reports CSV, KPI CSV all green
- CloudWatch /ecs/regmind-staging: no new post-deploy error spike
- PR #210: still open, explicitly out of scope
```
