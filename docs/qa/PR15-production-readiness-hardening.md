# PR15 Production Readiness Hardening

Date: 2026-05-20

## Executive Verdict

Controlled pilot readiness is conditional on the PR15 merge, AWS staging
deployment, and final validation gates passing on the merged `main` SHA.

Broad production rollout is not approved by this report. Production requires a
separate production-environment review covering production DNS, production RDS,
multi-task ECS capacity, production secrets, alert ownership, and rollback
authority.

## Scope

PR15 is a hardening and evidence-freeze PR. It does not add Lifecycle features,
does not change officer workflows, and does not touch protected UI files.

Starting SHA:

- `cc1708bbb0851505b3239d47c3d8ae9a5e32e19d`

Source-of-truth rules:

- GitHub `main` is the code source of truth.
- AWS staging is the runtime source of truth after merge.
- Render demo evidence is not acceptable.
- Local evidence is acceptable only when checked out to the exact merged SHA.

## Required Final Evidence

The final PR15 validation comment must record these values after the PR is
merged and deployed:

| Evidence | Required result |
|---|---|
| GitHub main SHA | Exact merged PR15 SHA |
| Deployed SHA | Same SHA as GitHub main |
| ECS task definition | `regmind-staging:<revision>` recorded |
| ECR image | SHA-tagged `regmind-backend:<merged-sha>` |
| `/api/version` | `git_sha` and `image_tag` match merged SHA |
| `/api/health` | `200`, safe public health response |
| `/api/liveness` | `200`, safe public liveness response |
| Targeted backend tests | Pass on exact merged SHA |
| Authenticated browser smoke | Pass using approved staging credentials |
| CloudWatch review | No new P0/P1/P2 errors after deploy |
| Rollback target | Previous ECS task definition recorded |

## Production Readiness Checklist

Security and permissions:

- Back-office and Lifecycle APIs remain authenticated and role-gated.
- Client users cannot access officer-only Lifecycle, reports, EDD, audit, or
  monitoring endpoints.
- SCO/admin-only actions remain SCO/admin-only.
- Agent outputs may surface signals but must not write officer-owned fields.
- No bearer token, QA password, or staging credential is committed.

Auditability:

- Periodic-review mutating actions write audit events with actor, timestamp,
  target, and before/after state where applicable.
- Assignment, reassignment, import setup, SCO acknowledgement, material-change
  attestation, risk-change action, evidence link, rationale, outcome
  completion, and memo generation remain auditable.
- Legacy `/decision` completion remains fenced from modern `outcome`.

Lifecycle architecture:

- `periodic_reviews` remains the canonical periodic-review state owner.
- Application detail Lifecycle tab remains the officer workspace.
- Lifecycle Queue remains a launchpad, not an editor.
- Case Management remains assigned work only.
- Ongoing Monitoring remains monitoring signals and agents only.
- Screening, EDD, Change Management, and KYC Documents remain owner workflows.
- Evidence links use `periodic_review_evidence_links` and do not duplicate
  document records or blobs.
- Reports and analytics count each periodic review once.

Operational readiness:

- `/api/version`, `/api/health`, and `/api/liveness` pass after deploy.
- ECS service reaches steady state with deployed SHA matching GitHub main.
- ECR image uses immutable SHA tag, not `latest`.
- CloudWatch `/ecs/regmind-staging` has no new post-deploy runtime error spike.
- Previous ECS task definition is recorded as rollback target.
- Database migrations remain backward-compatibility reviewed before rollback.

Browser validation:

- Browser smoke uses the real back-office login form.
- Browser smoke does not inject tokens or bypass authentication.
- Applications, Application Detail, Lifecycle tab, KYC Documents, Screening
  Review, Compliance Supervisor, Case Management, Ongoing Monitoring,
  Lifecycle Queue, EDD, and Change Management load on AWS staging.
- Screenshots, console errors, failed requests, unexpected API responses, and
  role-denial observations are recorded.

## Defect Severity Ledger

| Severity | Current status | PR15 rule |
|---|---|---|
| P0 | None known after PR14B validation | Stop roadmap, hotfix before acceptance |
| P1 | None known after PR14B validation | Stop roadmap, hotfix before acceptance |
| P2 | None known after PR14B validation | Stop roadmap, hotfix before acceptance |
| P3 | Non-blocking follow-ups may be logged | May proceed unless auditability, security, duplicate state, or user confusion is affected |

Known non-blocking follow-ups:

- Promote the PR13 full Lifecycle E2E validation script into a reusable staging
  job if this scenario should run on every deployment.
- Update GitHub Actions dependencies before the platform-mandated Node 20
  deprecation window.

## Launch Blockers

Staging acceptance blockers:

- Deployed SHA differs from merged GitHub `main`.
- CI or deploy workflow fails.
- Authenticated browser smoke cannot be completed with approved credentials.
- `/api/reports/analytics`, `/api/monitoring/dashboard`, Lifecycle Queue,
  Application Detail, EDD, Screening, or KYC Documents return unexpected 500s.
- Projection drift reappears between review detail, Application Detail,
  Lifecycle Queue, lifecycle summary, or reports.
- Any P0/P1/P2 defect is found.

Production blockers:

- Production infrastructure has not been separately validated.
- Single-task staging capacity is not evidence of production high availability.
- Production rollback ownership and incident-response authorization must be
  assigned before production launch.
- Production secrets, DNS, RDS backup/deletion-protection, logging retention,
  and alert routing require production-specific evidence.

## Pilot Readiness Verdict

If PR15 final validation passes, RegMind is ready for a controlled AWS staging
pilot of the Lifecycle / Periodic Review workflow.

The pilot must remain controlled because production HA, production incident
response, and production environment evidence are outside this PR.

## Rollback Plan

Use the staging rollback process in `docs/DEPLOYMENT_RUNBOOK.md`.

Rollback trigger examples:

- `/api/version` does not match the deployed SHA after ECS steady state.
- Authenticated browser smoke fails on a core page.
- Reports or monitoring dashboard return 500.
- Lifecycle completion, evidence linking, EDD linkage, or audit logging regresses.
- CloudWatch shows a new runtime error spike after deployment.

Rollback steps:

1. Record the failed task definition and deployed SHA.
2. Identify the previous known-good `regmind-staging:<revision>`.
3. Confirm the previous task definition points to an immutable SHA-tagged image.
4. Update the ECS service to the previous task definition with force deployment.
5. Re-run `/api/version`, `/api/health`, `/api/liveness`, targeted runtime API
   checks, and browser smoke.
6. If the failed deploy included a migration, assess backward compatibility
   before rolling back code.

## Final Validation Commands

Run targeted tests on the exact merged SHA:

```bash
DB_PATH=/tmp/regmind-pr15-postmerge.db python3.11 -m pytest -q \
  arie-backend/tests/test_pr15_production_readiness_report.py \
  arie-backend/tests/test_authenticated_staging_browser_smoke.py \
  arie-backend/tests/test_pr13_lifecycle_e2e_report.py \
  arie-backend/tests/test_phase4_reporting_evidence.py \
  arie-backend/tests/test_report_analytics.py \
  arie-backend/tests/test_lifecycle_queue.py \
  arie-backend/tests/test_lifecycle_queue_handlers.py \
  arie-backend/tests/test_periodic_review_phase1_canonical.py \
  arie-backend/tests/test_periodic_review_phase1_handlers.py \
  arie-backend/tests/test_periodic_review_engine.py \
  arie-backend/tests/test_periodic_review_handlers.py \
  arie-backend/tests/test_periodic_review_memo.py
```

Run authenticated AWS staging browser smoke only with approved credentials:

```bash
STAGING_QA_EMAIL="$STAGING_QA_EMAIL" \
STAGING_QA_PASSWORD="$STAGING_QA_PASSWORD" \
PLAYWRIGHT_NODE_MODULES=/tmp/regmind-browser-smoke/node_modules \
STAGING_BASE_URL=https://staging.regmind.co \
STAGING_SMOKE_OUT_DIR=/tmp/regmind-pr15-browser-smoke \
node arie-backend/scripts/qa/staging_browser_smoke.js
```

Do not paste credentials, bearer tokens, screenshots containing secrets, or
session storage into GitHub comments or repository files.
