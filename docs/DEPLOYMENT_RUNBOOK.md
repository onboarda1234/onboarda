# Onboarda / RegMind — Deployment Runbook

> **SCOPE NOTICE:** This runbook is currently validated for the **staging environment only** (staging.regmind.co on AWS ECS af-south-1). It has NOT been validated for production deployment. Before using this runbook for production, a separate production readiness review must be completed covering: production RDS provisioning, production Secrets Manager configuration, production DNS, production ECS service creation, and production-specific security hardening.

---

## 1. Purpose and Scope

**What this covers:** End-to-end deployment procedure for the Onboarda / RegMind platform, including pre-deployment checks, staging deployment, post-deploy validation, rollback, and incident triage.

**Environments covered:**

| Environment | Platform | URL | Runbook Status |
|---|---|---|---|
| Staging | AWS ECS Fargate (af-south-1) | staging.regmind.co | **Validated** |
| Production | AWS ECS Fargate (af-south-1) | app.regmind.co | **Not validated — do not use this runbook for production without review** |
| Demo | Render | demo.regmind.co | Not covered by this runbook |

**When to use:**
- Every staging deployment
- After any code merge to `main`
- After any infrastructure change to ECS, RDS, or Secrets Manager

---

## 2. Current Architecture Summary

**Verified as of 28 March 2026. Staging environment only.**

| Component | Implementation |
|---|---|
| **App runtime** | Python 3.11 / Tornado — single-process, single-container |
| **Container** | Docker (linux/amd64), deployed on ECS Fargate |
| **Container registry** | AWS ECR (`782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend`) |
| **Database** | AWS RDS PostgreSQL 15 (`db.t3.micro`), encrypted, private subnet |
| **Connection pool** | `psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=5)` |
| **Document storage** | AWS S3 (`regmind-documents-staging`), encrypted, versioned. Cross-deploy persistence confirmed. |
| **Secrets** | AWS Secrets Manager (`regmind/staging`). Contains: `JWT_SECRET`, `PII_ENCRYPTION_KEY`, `DATABASE_URL`, `ANTHROPIC_API_KEY`, Sumsub IDV/KYC secrets, and ComplyAdvantage Mesh screening secrets |
| **Load balancer** | AWS ALB with HTTPS (ACM certificate), ports 80 + 443 |
| **DNS** | `staging.regmind.co` → ALB CNAME via GoDaddy |
| **Logs** | AWS CloudWatch (`/ecs/regmind-staging`) |
| **Health endpoints** | `GET /api/liveness` (public ALB/ECS liveness), `GET /api/health` (safe public health), `GET /api/readiness` (authenticated deep readiness) |
| **AI engine** | Claude API via `anthropic` SDK. Fail-closed in staging/production. |
| **KYC / IDV** | Sumsub API for individual identity verification. Level name requires admin verification. |
| **AML screening / monitoring** | ComplyAdvantage Mesh for sanctions, PEP/RCA, adverse-media, customer/company screening, and ongoing monitoring when `SCREENING_PROVIDER=complyadvantage` and `ENABLE_SCREENING_ABSTRACTION=true`. |
| **Registry / KYB enrichment** | OpenCorporates registry/enrichment when configured; simulated otherwise and not a defensible AML screening source. |
| **PII encryption** | Fernet AES-128-CBC. Key pinned in Secrets Manager. |
| **Rate limiting** | In-memory (per-container). Resets on restart. |
| **Token revocation** | DB-backed revocation list with in-memory cache. Password resets revoke user sessions. |
| **Frontend** | Two single-file HTML apps. Must be copied into `arie-backend/` before Docker build. |

---

## 3. Preconditions Before Deployment

### Code readiness

| Check | How to verify |
|---|---|
| Working branch is `main` | `git branch --show-current` → `main` |
| No uncommitted changes to tracked files | `git status --short` shows only untracked files |
| All tests pass locally | `python3.11 -m pytest tests/ -x -q --tb=short --ignore=tests/test_pdf_generator.py`; every collected non-PDF test passes under the current CI manifest |
| HTML files are up to date | Root `arie-portal.html` and `arie-backoffice.html` contain the latest changes |

### Infrastructure readiness

| Check | How to verify |
|---|---|
| Docker Desktop running | `docker --version` returns a version |
| AWS CLI configured for af-south-1 | `aws sts get-caller-identity` returns the correct account |
| ECR login valid (12-hour expiry) | Re-run login if last login was >12 hours ago |
| RDS instance available | `aws rds describe-db-instances --db-instance-identifier regmind-staging-db --region af-south-1 --query 'DBInstances[0].DBInstanceStatus'` → `available` |
| ECS service active | `aws ecs describe-services --cluster regmind-staging --services regmind-backend --region af-south-1 --query 'services[0].status'` → `ACTIVE` |

### Secrets readiness

All secrets must exist in `regmind/staging` in AWS Secrets Manager:
- `JWT_SECRET` — authentication
- `PII_ENCRYPTION_KEY` — must be stable across deploys (never change without migration plan)
- `DATABASE_URL` — PostgreSQL connection string
- `ANTHROPIC_API_KEY` — valid Anthropic key
- `SUMSUB_APP_TOKEN`, `SUMSUB_SECRET_KEY`, `SUMSUB_WEBHOOK_SECRET` — Sumsub IDV/KYC integration
- `SCREENING_PROVIDER=complyadvantage` and `ENABLE_SCREENING_ABSTRACTION=true` — CA Mesh AML screening cutover flags
- `COMPLYADVANTAGE_API_BASE_URL`, `COMPLYADVANTAGE_AUTH_URL`, `COMPLYADVANTAGE_REALM`, `COMPLYADVANTAGE_USERNAME`, `COMPLYADVANTAGE_PASSWORD`, `COMPLYADVANTAGE_SCREENING_CONFIG_ID` — ComplyAdvantage Mesh screening integration
- `SUMSUB_APP_TOKEN` + `SUMSUB_SECRET_KEY` — active Sumsub credentials
- `ADMIN_CLIENT_RESET_CONFIRMATION` — required for admin client-password reset endpoint
- `ADMIN_OFFICER_RESET_CONFIRMATION` — required for admin officer-password reset endpoint
- `METRICS_TOKEN` — optional bearer token for Prometheus scraping when `/metrics` is enabled

The `staging` GitHub environment must contain the Actions secrets
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`. Their values must never be
documented or uploaded as release evidence. The associated staging deployment
identity must be able to call `secretsmanager:GetSecretValue` for the exact AWS
secret ID `regmind/staging`. During the authenticated version gate, the
workflow extracts only `JWT_SECRET`, masks the derived bearer token, and mints
a 15-minute token in the same step that consumes it. No long-lived
`STAGING_VERSION_BEARER_TOKEN` is required or stored. A missing Actions secret,
missing IAM permission, missing `JWT_SECRET`, authentication failure, or
version mismatch is a hard deployment failure.

**Sumsub note:** `SUMSUB_LEVEL_NAME` (env var, currently `basic-kyc-level`) must match a level configured in the Sumsub dashboard. If KYC applicant creation returns 404, verify this first.

**Rollback awareness:** Before deploying, capture the exact task definitions
currently used by both live services, plus their immutable image digest. Do not
guess from the latest registered family revision:
```bash
aws ecs describe-services --cluster regmind-staging \
  --services regmind-backend regmind-verification-worker \
  --region af-south-1 \
  --query 'services[].{service:serviceName,taskDefinition:taskDefinition}' \
  --output json
```

---

## 4. Standard Staging Deployment Procedure

**Estimated time: 5-8 minutes**

### Step 1: Run tests
```bash
cd ~/Desktop/Onboarda/arie-backend
python3.11 -m pytest tests/ -x -q --tb=short --ignore=tests/test_pdf_generator.py
```
Expected: every collected non-PDF test passes. The exact count evolves with
the repository; use the current CI manifest rather than a stale fixed count.
Do not proceed if any test fails.

### Step 2: Validate the exact-SHA Docker build locally

> **Note:** HTML files (`arie-portal.html`, `arie-backoffice.html`) are copied from the repo root
> into `arie-backend/` automatically by CI/CD workflows and the Render build command.
> For local Docker builds, copy them manually first (from `arie-backend/`):
> `cp ../arie-portal.html . && cp ../arie-backoffice.html .`

```bash
cd arie-backend
GIT_SHA="$(git rev-parse HEAD)"
[[ "$GIT_SHA" =~ ^[0-9a-f]{40}$ ]] || exit 1
[[ "$(git branch --show-current)" == "main" ]] || exit 1
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
IMAGE="782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:$GIT_SHA"

docker build --platform linux/amd64 \
  --build-arg "GIT_SHA=$GIT_SHA" \
  --build-arg "IMAGE_TAG=$GIT_SHA" \
  --build-arg "BUILD_TIME=$BUILD_TIME" \
  --label "git.sha=$GIT_SHA" \
  --label "git.ref=refs/heads/main" \
  --label "build.time=$BUILD_TIME" \
  -t "$IMAGE" .
```
This is local validation only. Do not push the locally built image and do not
use a convenience tag. The approved staging release path is
`.github/workflows/deploy-staging.yml`.

### Step 3: Run the approved staging workflow

A full-SHA `main` push triggers the workflow. A manual dispatch is also allowed
only when its checked-out ref resolves to `refs/heads/main`. Do not deploy with
raw `docker push`, `aws ecs register-task-definition`, or
`aws ecs update-service` commands: those paths do not independently provide all
required scan, provenance, evidence, and rollback gates.

The workflow uses `release_image_control.py prepare` to resolve the exact tag.
If the immutable SHA tag exists, it verifies provenance and reuses its digest.
If absent, it builds and pushes the exact SHA once. It never deletes, retags,
or overwrites an immutable release image. An immutable-tag collision or
provenance mismatch fails closed.

### Step 4: Observe gated deployment and task registration

The workflow registers new task definitions from the exact revisions currently
used by the live backend and worker. It never uses the most recently registered
family revision as an implicit source. Before registration it requires:

- ECR reports exactly `IMMUTABLE`;
- the exact SHA image provenance matches;
- the exact image digest has a `COMPLETE` ECR registry scan;
- pinned Trivy analysis of that same digest contains both OS-package and
  Python-package targets;
- zero CRITICAL and zero unaccepted HIGH findings.

The separate pull-request container-security workflow is a required review
check, but it is not used as deployment evidence by timing alone. The staging
workflow independently repeats the comprehensive pinned Trivy gate against the
exact immutable ECR digest before registering either task definition. A
missing scanner database, missing raw report, missing OS/Python coverage, or
policy failure stops the deployment.

### Step 5: Wait for the workflow gates

Do not treat task registration or an ECS update as deployment completion. Wait
for both services to stabilize and for the runtime digest, ALB target health,
authenticated `/api/version`, CloudWatch review, and final evidence steps to
complete. A failed or incomplete evidence upload is diagnostic only and is not
a successful release.

### Step 6: Verify health
```bash
curl -s https://staging.regmind.co/api/liveness | python3 -m json.tool
```
Expected: `"status": "ok"` with no database or integration inventory.

Authenticated operators can run the deep readiness check with an admin/SCO token:
```bash
curl -s -H "Authorization: Bearer $ADMIN_OR_SCO_JWT" \
  https://staging.regmind.co/api/readiness | python3 -m json.tool
```
Expected: `"ready": true`, database `"status": "ok"`, encryption `"status": "ok"`.

---

## 5. Post-Deploy Validation Checklist

### Automated validation

Run public/runtime API checks first:

```bash
curl -s https://staging.regmind.co/api/health | python3 -m json.tool
curl -s https://staging.regmind.co/api/liveness | python3 -m json.tool
curl -s -H "Authorization: Bearer $BACKOFFICE_TOKEN" \
  https://staging.regmind.co/api/version | python3 -m json.tool
```

Expected: authenticated `/api/version` reports the deployed Git SHA and image tag, and health/liveness return safe public `ok` responses.

Deterministic staging smoke token path:

- GitHub Actions mints a short-lived JWT from Secrets Manager `regmind/staging` `JWT_SECRET`.
- The token subject is `github-actions:day6-staging-smoke`.
- The app seeds that subject as an active SCO smoke user in staging so DB-backed auth validation succeeds without weakening general authentication.
- Local operators should prefer a real staging QA login token or a short-lived token stored only in `BACKOFFICE_TOKEN`; do not put bearer tokens on the command line.

Recommended evidence collector:

```bash
EVIDENCE_DIR="docs/audits/evidence/remediation_sprints/<PR-ID>_<short-name>_<YYYYMMDDTHHMMSSZ>/runtime_json"
BACKOFFICE_TOKEN="$STAGING_BACKOFFICE_TOKEN" \
python3 arie-backend/scripts/qa/staging_release_evidence.py \
  --api-base https://staging.regmind.co/api \
  --expected-sha "$GIT_SHA" \
  --expected-environment staging \
  --evidence-dir "$EVIDENCE_DIR" \
  --run-api-smoke \
  --strict
```

The collector writes `health.json`, `liveness.json`, authenticated `version.json`, ECS/task/image `runtime_baseline.json`, optional `api_smoke.json`, and `summary.json`.
Use `--expected-total`, `--expected-pending`, and `--expected-edd` only for controlled fixture datasets with known counts.

### Authenticated staging browser smoke

Use `arie-backend/scripts/qa/staging_browser_smoke.js` for authenticated back-office browser evidence. The harness uses the real back-office login form with an approved staging QA user, captures screenshots, and records console, page, request, and HTTP response evidence in `report.json`.

Credential handling:

- Store the approved staging QA email/password in a local password manager or GitHub environment secrets.
- Pass credentials through `STAGING_QA_EMAIL` and `STAGING_QA_PASSWORD` only.
- Do not paste credentials into commits, PRs, release notes, shell history, or screenshots.
- Do not inject tokens, write browser storage manually, or bypass authentication.

Example local run:

```bash
cd ~/Desktop/Onboarda
mkdir -p /tmp/regmind-browser-smoke
npm --prefix /tmp/regmind-browser-smoke install playwright-core

STAGING_QA_EMAIL="$STAGING_QA_EMAIL" \
STAGING_QA_PASSWORD="$STAGING_QA_PASSWORD" \
PLAYWRIGHT_NODE_MODULES=/tmp/regmind-browser-smoke/node_modules \
STAGING_BASE_URL=https://staging.regmind.co \
STAGING_SMOKE_OUT_DIR=/tmp/regmind-staging-browser-smoke \
node arie-backend/scripts/qa/staging_browser_smoke.js
```

Expected: exit code `0`, `report.json` written, screenshots captured, no page errors, no failed requests, no blocking JavaScript console errors, and no unexpected API 4xx/5xx responses. Known officer-role `403` responses for admin-only APIs may be recorded as non-blocking role-denial evidence.

### Manual validation

| # | Check | How | Expected |
|---|---|---|---|
| 1 | Build provenance | `curl -H "Authorization: Bearer $BACKOFFICE_TOKEN" https://staging.regmind.co/api/version` | `git_sha` and `image_tag` match the deployed commit; no `unknown` values |
| 2 | Public liveness | `curl https://staging.regmind.co/api/liveness` | `ok`; no database/integration inventory |
| 3 | Public health | `curl https://staging.regmind.co/api/health` | Safe public keys only; no `database`, `integrations`, or `metrics_enabled` |
| 4 | Portal loads | Browser: staging.regmind.co/portal | Login page, no demo data |
| 5 | Back office loads | Browser: staging.regmind.co/backoffice | Login screen, no demo data before login |
| 6 | Dashboard stats | After login, scroll to monitoring section | "—" and "Monitoring not yet active" |
| 7 | Agent Health | Navigate to Agent Health | "Not Yet Active" placeholder |
| 8 | Regulatory page | Navigate to Regulatory Intelligence | "No regulatory documents yet" |
| 9 | KPI AI section | KPI Dashboard → AI Performance | Placeholder, not 87.3%/92.1%/99.8% |

### Phase 1-5 pilot close-out gates

Run these after every Phase hardening deployment before declaring the environment pilot-ready:

1. **Version/SHA:** authenticated `/api/version` returns the deployed Git SHA, image tag, build time, environment, service, and safe provider status summary.
2. **CSRF:** cookie-auth unsafe write without `X-CSRF-Token` returns `403`; same write with a valid token reaches business validation/success.
3. **Audit reconstruction:** `/api/audit?ref=<ARF>`, `/api/audit/export?format=csv&ref=<ARF>`, `/api/applications/<ARF>/audit-log`, and `/api/applications/<ARF>/evidence-pack` reconcile on the same case audit events.
4. **Evidence pack:** includes application notes, documents, RMI, memos, decision records, EDD cases, EDD findings/status/policy, and build metadata.
5. **UNKNOWN risk:** dashboard and reports include an explicit `UNKNOWN`/`NOT RATED` bucket; missing risk scores render as null/em dash, never `0` or default `50`.
6. **EDD lifecycle:** findings and SLA are required before senior review; closure requires SCO/admin and a different actor; `EDD Closure (dual-control)` audit rows target the ARF.
7. **Screening truthfulness:** `/api/screening/status` lists ComplyAdvantage Mesh AML screening, Sumsub IDV/KYC scope, OpenCorporates registry/enrichment status, fallback/simulation state, and IP geolocation without advertising deprecated or unused providers.
8. **Diagnostics exposure:** unauthenticated `/metrics` and `/api/readiness` return `401`; `/api/liveness` is public and hardened; random 404 paths return `Server: RegMind` plus hardened headers.
9. **Admin resets:** client/officer password-reset endpoints require confirmation token, enforce password policy, write audit rows, and revoke existing JWT sessions.
10. **Operational queues:** `/api/edd/cases` hides fixture/smoke rows by default; only admin/SCO with `include_fixtures=1` or `show_fixtures=true` can include them.

### Phase 5 infrastructure gates

Capture command output in the release evidence pack:

```bash
aws ecr describe-repositories --repository-names regmind-backend \
  --region af-south-1 --query 'repositories[0].imageTagMutability'

aws ecr describe-image-scan-findings --repository-name regmind-backend \
  --image-id imageTag=$GIT_SHA --region af-south-1

aws rds describe-db-instances --db-instance-identifier regmind-staging-db \
  --region af-south-1 --query 'DBInstances[0].{BackupRetentionPeriod:BackupRetentionPeriod,DeletionProtection:DeletionProtection}'

aws elbv2 describe-load-balancer-attributes --load-balancer-arn "$ALB_ARN" \
  --region af-south-1 --query 'Attributes[?starts_with(Key, `access_logs.s3.`)]'

aws logs describe-log-groups --log-group-name-prefix /ecs/regmind-staging \
  --region af-south-1 --query 'logGroups[].{name:logGroupName,retention:retentionInDays}'
```

Expected Phase 5 baseline: ECR tags immutable; zero CRITICAL image findings and no unaccepted HIGH image findings; RDS backup retention at least 7 days and deletion protection enabled; ALB access logs enabled; CloudWatch log retention set; alarms exist for ALB 5xx, ECS running task count, RDS CPU/storage, failed-login spike, memo/EDD failure, and `Invalid encryption token`.

### Log review
```bash
aws logs filter-log-events --log-group-name /ecs/regmind-staging \
  --region af-south-1 --filter-pattern "ERROR" \
  --start-time $(( $(date +%s) - 600 ))000 --limit 10 \
  --query 'events[*].message' --output text
```
Check for: `connection pool exhausted`, `falling back to mock mode`, `Sumsub create applicant failed: 404`.

### Day 6 deployment evidence ledger

Attach this ledger to every Day 6 staging deployment note before the deployment is marked closed:

| Evidence item | Source | Required value |
|---|---|---|
| Deployed commit | `staging_release_evidence.py` authenticated `version.json` | `git_sha` equals the reviewed `main` commit; `image_tag` contains the same SHA |
| Build provenance | GitHub Actions `deploy-staging.yml` run | Run URL, run number, and actor recorded |
| ECS services | `aws ecs describe-services --cluster regmind-staging --services regmind-backend regmind-verification-worker --region af-south-1` | Both services have one completed rollout at expected counts; both task-definition ARNs recorded |
| Runtime logs | CloudWatch log group `/ecs/regmind-staging` | No new `ERROR`, `connection pool exhausted`, or `falling back to mock mode` entries after deploy |
| Reporting smoke | `arie-backend/scripts/qa/day5_closing_smoke.py` | `ok: true`, reconciliation passes, CSV/report `canonical_view: applications_report_v1`, dashboard `canonical_view: dashboard_metrics_v2`, and live dashboard/report/application counts agree |
| Authenticated browser smoke | `arie-backend/scripts/qa/staging_browser_smoke.js` | Real QA login succeeds; required back-office pages/tabs load; screenshots and `report.json` attached; no token injection or auth bypass |
| Rollback handles | Sanitized pre-deployment evidence | Previous backend and worker task-definition ARNs plus their shared immutable image tag and digest recorded before deployment |

The automated evidence artifact must additionally contain the immutable image
digest, exact-digest scan summary, previous and new task definitions for both
backend and worker, all running-task image digests, ALB target-health counts,
CloudWatch error-window review, the authenticated version response, and the
paired rollback commands. A partial diagnostic artifact may be uploaded after
a failure, but only `workflow-status.json` with `status=complete` and a validated
`release-evidence.json` constitute release evidence.

Recommended evidence bundle command:

```bash
EVIDENCE_DIR="docs/audits/evidence/remediation_sprints/<PR-ID>_<short-name>_<YYYYMMDDTHHMMSSZ>/runtime_json"
BACKOFFICE_TOKEN="$STAGING_BACKOFFICE_TOKEN" \
python3 arie-backend/scripts/qa/staging_release_evidence.py \
  --api-base https://staging.regmind.co/api \
  --expected-sha "$GIT_SHA" \
  --expected-environment staging \
  --evidence-dir "$EVIDENCE_DIR" \
  --run-api-smoke \
  --strict
```

Recommended smoke command:

```bash
BACKOFFICE_TOKEN="$STAGING_BACKOFFICE_TOKEN" \
python3 arie-backend/scripts/qa/day5_closing_smoke.py \
  --api-base https://staging.regmind.co/api \
  --expected-sha "$GIT_SHA"
```

Use `--expected-total`, `--expected-pending`, and `--expected-edd` only when validating a controlled fixture dataset with known counts. Do not use stale fixed counts for live staging data.

Use `--token-env BACKOFFICE_TOKEN` if the token is stored under a different environment variable name. Do not paste bearer tokens into release notes, GitHub comments, or shell history.

---

## 6. Rollback Procedure

> **Rollback is now reliable:** Every reviewed SHA resolves to one immutable ECR tag; a rerun verifies and reuses the matching image rather than overwriting it. Rollback restores the exact captured backend and worker task-definition ARN pair after verifying their shared immutable image digest.
>
> **Note:** Database migrations are NOT rolled back. If a migration was applied during the failed deploy, the old code may encounter schema mismatches. Assess backward compatibility before rolling back.

### If the previous immutable image is still available in ECR

**Step 1:** Use the exact backend and worker task-definition ARNs captured
before the failed deployment. Do not choose `latest - 1`; an undeployed
registration may exist.

Verify that both captured task definitions' image tags still resolve to the
captured immutable digest.

**Step 2:** Restore both services:
```bash
aws ecs update-service --cluster regmind-staging --service regmind-backend \
  --task-definition <PREVIOUS_BACKEND_TASK_DEFINITION_ARN> \
  --force-new-deployment --region af-south-1
aws ecs update-service --cluster regmind-staging \
  --service regmind-verification-worker \
  --task-definition <PREVIOUS_WORKER_TASK_DEFINITION_ARN> \
  --force-new-deployment --region af-south-1
```

**Step 3:** Wait and verify:
```bash
aws ecs wait services-stable --cluster regmind-staging \
  --services regmind-backend regmind-verification-worker \
  --region af-south-1

# Then repeat authenticated /api/version, running-task imageDigest, ALB,
# CloudWatch, liveness, portal, and backoffice evidence.
```

### If the SHA image is missing

Treat this as a release incident. Do not recreate a deleted historical SHA tag
and present the newly built digest as the original artifact. Rebuild through a
separately reviewed recovery release, scan its new immutable digest, register
new task definitions, and record that provenance break explicitly.

**Database note:** Task definition rollback does NOT roll back database migrations. If a migration was applied during the failed deploy, the old code may encounter schema mismatches. Assess backward compatibility before rolling back.

---

## 7. Known Operational Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **DB connection pool exhaustion** | Medium | All endpoints return 500 | Restart ECS task. Pool is maxconn=5 on db.t3.micro (~20 total). |
| **Sumsub level-name mismatch** | Confirmed | KYC applicant creation fails (404) | Admin verifies level in Sumsub dashboard, updates ECS env var. |
| **JWT invalidation on deploy** | Low | Users must re-login if JWT secret changes or sessions are administratively revoked | JWT secret is stable in Secrets Manager; do not rotate without a migration/communications plan. |
| **Browser cache shows stale UI** | Medium | Old interface visible after deploy | Hard refresh (Cmd+Shift+R) or incognito. |
| **In-memory rate limiter resets** | Certain | Brute-force protection absent briefly | Acceptable for pilot. Redis is future. |
| **Token revocation cache resets** | Low | DB-backed revoked tokens are reloaded; only cache warmth is lost | Ensure `revoked_tokens` table is intact after DB maintenance. |
| **Single-container failure** | Low | ~2 min downtime during ECS restart | Production should have min 2 tasks. |
| **Mutable image tags prevent reliable rollback** | Mitigated by Phase 5 | Cannot prove which image ran | ECR tags must be immutable; deploy SHA-tagged images only. |

---

## 8. Incident Triage

### Deployment is unhealthy
```bash
curl -s https://staging.regmind.co/api/liveness
aws ecs describe-services --cluster regmind-staging --services regmind-backend \
  --region af-south-1 --query 'services[0].events[0:3].message' --output text
```

### DB connection pool exhausted
Symptom: `"connection pool exhausted"` in health response.
```bash
# Restart ECS task
aws ecs update-service --cluster regmind-staging --service regmind-backend \
  --desired-count 0 --region af-south-1
sleep 20
aws ecs update-service --cluster regmind-staging --service regmind-backend \
  --desired-count 1 --force-new-deployment --region af-south-1
```
If this doesn't resolve it:
```bash
aws rds reboot-db-instance --db-instance-identifier regmind-staging-db --region af-south-1
# Wait 60s, then restart ECS
```

### Document uploads fail
- Check logs for `S3 upload failed` → verify ECS task role has S3 permissions
- Check for `File upload validated` → upload reached server but S3 failed
- No upload log → check auth, CORS, ALB routing

### AI verification errors
- `Claude client initialized with Anthropic API` → good, real client
- `Anthropic library not available` → SDK missing, rebuild Docker image
- `Claude API error: 400` → invalid file format (not a real PDF/image)
- `FAIL-CLOSED` → expected when AI service genuinely unavailable
- `Returning mock document verification` → mock mode active, check `CLAUDE_MOCK_MODE` env

### Login fails
- Officer: after DB reset, password changes. Get from logs.
- `Internal Server Error` → likely connection pool exhaustion
- `Authentication required` on API calls → JWT expired or secret changed on deploy

### Back-office UI blank or shows demo data
- Hard refresh browser (Cmd+Shift+R)
- Check console for JavaScript errors
- If demo data visible (TechPay, 47 apps) → stale Docker image, verify build used latest HTML

---

## 9. Operator Checklist

```
PRE-DEPLOY
[ ] On branch main, no uncommitted tracked changes
[ ] Every collected test in the current CI manifest passes locally
[ ] Record both live service task-definition ARNs and their shared immutable image tag/digest
[ ] Docker Desktop running
[ ] AWS CLI configured (af-south-1)
[ ] ECR repository is immutable
[ ] RDS backup retention >= 7 days and deletion protection enabled
[ ] ALB access logs enabled
[ ] CloudWatch log retention and baseline alarms configured

BUILD
[ ] cp ../arie-portal.html . && cp ../arie-backoffice.html .  (run from `arie-backend/`; local builds only; CI does this automatically)
[ ] derive and validate the full `main` GIT_SHA before the local build
[ ] docker build linux/amd64 with exact-SHA tag, build args, and provenance labels
[ ] do not push the local validation image or create a convenience tag

DEPLOY
[ ] approved `deploy-staging.yml` workflow is running for the reviewed main SHA
[ ] exact immutable SHA image is verified/reused or pushed once
[ ] exact-digest scan gate passes before either ECS mutation
[ ] wait for every mandatory workflow evidence gate

VERIFY
[ ] ECR exact image digest scan → COMPLETE; 0 CRITICAL; 0 unaccepted HIGH
[ ] pinned Trivy exact-digest scan → OS + Python coverage; 0 CRITICAL; 0 unaccepted HIGH
[ ] every backend/worker running task imageDigest equals the scanned ECR digest
[ ] ECS desired == running, pending == 0, rollout complete for both services
[ ] every ALB target healthy
[ ] authenticated /api/version → deployed SHA and image tag match
[ ] /api/liveness → ok, hardened headers
[ ] /api/readiness unauthenticated → 401
[ ] /metrics unauthenticated → 401
[ ] random 404 → Server: RegMind, hardened headers
[ ] node arie-backend/scripts/qa/staging_browser_smoke.js → authenticated browser smoke pass with screenshots/report attached
[ ] Browser: dashboard shows "—" not demo stats
[ ] Logs: no "mock mode" or "connection pool exhausted"

ROLLBACK (if needed — reliable with SHA-tagged images)
[ ] Restore captured backend task definition: ___
[ ] Restore captured worker task definition: ___
[ ] Wait for both services, then repeat digest/version/ALB/CloudWatch evidence
[ ] Verify health
```

---

## 10. Recommended Future Improvements

*These are NOT part of the current validated runbook. They are improvements for future implementation.*

| Improvement | Benefit | Effort |
|---|---|---|
| **Versioned image tags** (`:$GIT_SHA`, no `:latest`) | Reliable rollback and provenance | ✅ Done |
| **Blue-green deployment** | Zero-downtime, automatic rollback | 1 day |
| **Redis-backed rate limiter + token revocation** | Survives restarts, scales | 1 day |
| **Automated E2E in CI/CD post-deploy step** | Catches deploy regressions automatically | 2 hours |
| **RDS upgrade to db.t3.small** | Doubles max connections (~45 vs ~20) | 15 min, +$15/month |
| **Production environment setup** | Separate infra for live clients | 1 day |
| **Secrets rotation procedure** | Documented key rotation without downtime | 2 hours |
| **Monitoring and alerting** (Sentry, CloudWatch alarms) | Proactive error detection | 2 hours |

---

*Runbook version: 1.0 — 28 March 2026*
*Validated for: staging (staging.regmind.co)*
*Not validated for: production*
