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
| **Secrets** | AWS Secrets Manager (`regmind/staging`). Contains: `JWT_SECRET`, `PII_ENCRYPTION_KEY`, `DATABASE_URL`, `ANTHROPIC_API_KEY`, `SUMSUB_APP_TOKEN`, `SUMSUB_SECRET_KEY` |
| **Load balancer** | AWS ALB with HTTPS (ACM certificate), ports 80 + 443 |
| **DNS** | `staging.regmind.co` → ALB CNAME via GoDaddy |
| **Logs** | AWS CloudWatch (`/ecs/regmind-staging`) |
| **Health endpoints** | `GET /api/liveness` (public ALB/ECS liveness), `GET /api/health` (safe public health), `GET /api/readiness` (authenticated deep readiness) |
| **AI engine** | Claude API via `anthropic` SDK. Fail-closed in staging/production. |
| **KYC/AML** | Sumsub API (live credentials). Level name requires admin verification. |
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
| All tests pass locally | `python3.11 -m pytest tests/ -x -q --tb=short --ignore=tests/test_pdf_generator.py` → 365 passed |
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
- `SUMSUB_APP_TOKEN` + `SUMSUB_SECRET_KEY` — active Sumsub credentials
- `ADMIN_CLIENT_RESET_CONFIRMATION` — required for admin client-password reset endpoint
- `ADMIN_OFFICER_RESET_CONFIRMATION` — required for admin officer-password reset endpoint
- `METRICS_TOKEN` — optional bearer token for Prometheus scraping when `/metrics` is enabled

**Sumsub note:** `SUMSUB_LEVEL_NAME` (env var, currently `basic-kyc-level`) must match a level configured in the Sumsub dashboard. If KYC applicant creation returns 404, verify this first.

**Rollback awareness:** Before deploying, note the current task definition revision:
```bash
aws ecs describe-services --cluster regmind-staging --services regmind-backend \
  --region af-south-1 --query 'services[0].taskDefinition' --output text
```

---

## 4. Standard Staging Deployment Procedure

**Estimated time: 5-8 minutes**

### Step 1: Run tests
```bash
cd ~/Desktop/Onboarda/arie-backend
python3.11 -m pytest tests/ -x -q --tb=short --ignore=tests/test_pdf_generator.py
```
Expected: `365 passed`. Do not proceed if tests fail.

### Step 2: Build Docker image

> **Note:** HTML files (`arie-portal.html`, `arie-backoffice.html`) are copied from the repo root
> into `arie-backend/` automatically by CI/CD workflows and the Render build command.
> For local Docker builds, copy them manually first (from `arie-backend/`):
> `cp ../arie-portal.html . && cp ../arie-backoffice.html .`

```bash
cd arie-backend
docker build --platform linux/amd64 -t regmind-backend .
```
Must use `--platform linux/amd64` (dev machine is ARM, ECS runs amd64).

### Step 3: Tag and push to ECR
```bash
GIT_SHA=$(git rev-parse HEAD)
docker tag regmind-backend:latest 782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:$GIT_SHA

aws ecr get-login-password --region af-south-1 | docker login --username AWS \
  --password-stdin 782913119880.dkr.ecr.af-south-1.amazonaws.com

docker push 782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:$GIT_SHA
```
If push returns `403 Forbidden`, re-run the login command.

> **Note:** Do not push `:latest`. Staging deployments are SHA-tagged only so ECR can be configured with immutable tags and every running task can be traced to one commit.

### Step 4: Register new task definition and deploy to ECS

The CI/CD workflow now registers a new task definition with the SHA-pinned image automatically.
For manual deployment:
```bash
# Get current task def and update image
TASK_DEF=$(aws ecs describe-task-definition --task-definition regmind-staging \
  --region af-south-1 --query 'taskDefinition' --output json)

# Update container image to SHA-tagged version (use python or jq)
# Then register and deploy:
aws ecs update-service --cluster regmind-staging --service regmind-backend \
  --task-definition <NEW_TASK_DEF_ARN> \
  --force-new-deployment --region af-south-1
```

### Step 5: Wait for stabilisation
```bash
sleep 120
```

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

The staging E2E test script exists at `arie-backend/tests/test_staging_e2e.py`:
```bash
cd ~/Desktop/Onboarda/arie-backend
python3.11 tests/test_staging_e2e.py
```
Expected: 16/17+ checks pass. Covers: health, environment, auth, app creation, submit + risk scoring, page accessibility, save/resume.

### Manual validation

| # | Check | How | Expected |
|---|---|---|---|
| 1 | Build provenance | `curl https://staging.regmind.co/api/version` | `git_sha` and `image_tag` match the deployed commit; no `unknown` values |
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

1. **Version/SHA:** `/api/version` returns the deployed Git SHA, image tag, and build time.
2. **CSRF:** cookie-auth unsafe write without `X-CSRF-Token` returns `403`; same write with a valid token reaches business validation/success.
3. **Audit reconstruction:** `/api/audit?ref=<ARF>`, `/api/audit/export?format=csv&ref=<ARF>`, `/api/applications/<ARF>/audit-log`, and `/api/applications/<ARF>/evidence-pack` reconcile on the same case audit events.
4. **Evidence pack:** includes application notes, documents, RMI, memos, decision records, EDD cases, EDD findings/status/policy, and build metadata.
5. **UNKNOWN risk:** dashboard and reports include an explicit `UNKNOWN`/`NOT RATED` bucket; missing risk scores render as null/em dash, never `0` or default `50`.
6. **EDD lifecycle:** findings and SLA are required before senior review; closure requires SCO/admin and a different actor; `EDD Closure (dual-control)` audit rows target the ARF.
7. **Screening truthfulness:** `/api/screening/status` lists Sumsub as live, OpenSanctions/OpenCorporates as simulated unless configured, and does not advertise ComplyAdvantage as live while implementation is in progress.
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

Expected Phase 5 baseline: ECR tags immutable; no unaccepted HIGH image findings; RDS backup retention at least 7 days and deletion protection enabled; ALB access logs enabled; CloudWatch log retention set; alarms exist for ALB 5xx, ECS running task count, RDS CPU/storage, failed-login spike, memo/EDD failure, and `Invalid encryption token`.

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
| Deployed commit | `curl https://staging.regmind.co/api/version` | `git_sha` equals the reviewed `main` commit; `image_tag` contains the same SHA |
| Build provenance | GitHub Actions `deploy-staging.yml` run | Run URL, run number, and actor recorded |
| ECS service | `aws ecs describe-services --cluster regmind-staging --services regmind-backend --region af-south-1` | `deployments[0].rolloutState` is `COMPLETED`; task definition revision recorded |
| Runtime logs | CloudWatch log group `/ecs/regmind-staging` | No new `ERROR`, `connection pool exhausted`, or `falling back to mock mode` entries after deploy |
| Reporting smoke | `arie-backend/scripts/qa/day5_closing_smoke.py` | `ok: true`, `canonical_view: applications_report_v1`, and expected total/pending/EDD counts |
| Rollback handle | Previous ECS task definition | Previous `regmind-staging:<REVISION>` recorded before deployment |

Recommended smoke command:

```bash
BACKOFFICE_TOKEN="$STAGING_BACKOFFICE_TOKEN" \
python3 arie-backend/scripts/qa/day5_closing_smoke.py \
  --api-base https://staging.regmind.co/api \
  --expected-sha "$GIT_SHA" \
  --expected-total 22 \
  --expected-pending 21 \
  --expected-edd 1
```

Use `--token-env BACKOFFICE_TOKEN` if the token is stored under a different environment variable name. Do not paste bearer tokens into release notes, GitHub comments, or shell history.

---

## 6. Rollback Procedure

> **Rollback is now reliable:** With SHA-tagged images (introduced in CI/CD improvements), each deployment creates a uniquely-tagged image in ECR that is not overwritten. Rolling back to a previous task definition revision will use the correct image.
>
> **Note:** Database migrations are NOT rolled back. If a migration was applied during the failed deploy, the old code may encounter schema mismatches. Assess backward compatibility before rolling back.

### If a previous image is still available in ECR

**Step 1:** Identify the previous task definition revision:
```bash
aws ecs list-task-definitions --family-prefix regmind-staging \
  --region af-south-1 --query 'taskDefinitionArns' --output json
```

**Step 2:** Update service to previous revision:
```bash
aws ecs update-service --cluster regmind-staging --service regmind-backend \
  --task-definition regmind-staging:<PREVIOUS_REVISION> \
  --force-new-deployment --region af-south-1
```

**Step 3:** Wait and verify:
```bash
sleep 120
curl -s https://staging.regmind.co/api/liveness | python3 -m json.tool
```

### If the SHA image is missing

Treat this as a release incident. The ECR repository should have immutable SHA tags and no `:latest` dependency. Rebuild from the previous known-good commit, push that SHA tag, register a task definition with that SHA-tagged image, and redeploy.

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
[ ] 365 tests pass locally
[ ] Note current task definition revision: regmind-staging:___
[ ] Docker Desktop running
[ ] AWS CLI configured (af-south-1)
[ ] ECR repository is immutable
[ ] RDS backup retention >= 7 days and deletion protection enabled
[ ] ALB access logs enabled
[ ] CloudWatch log retention and baseline alarms configured

BUILD
[ ] cp ../arie-portal.html . && cp ../arie-backoffice.html .  (run from `arie-backend/`; local builds only; CI does this automatically)
[ ] docker build --platform linux/amd64 -t regmind-backend .
[ ] docker tag → 782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:$GIT_SHA
[ ] aws ecr get-login-password (if >12h since last login)
[ ] docker push SHA tag only

DEPLOY
[ ] Register task definition with SHA-tagged image
[ ] aws ecs update-service --task-definition <NEW_TASK_DEF_ARN> --force-new-deployment
[ ] sleep 120

VERIFY
[ ] /api/version → deployed SHA and image tag match
[ ] /api/liveness → ok, hardened headers
[ ] /api/readiness unauthenticated → 401
[ ] /metrics unauthenticated → 401
[ ] random 404 → Server: RegMind, hardened headers
[ ] python3.11 tests/test_staging_e2e.py → 16/17+ pass
[ ] Browser: dashboard shows "—" not demo stats
[ ] Logs: no "mock mode" or "connection pool exhausted"

ROLLBACK (if needed — reliable with SHA-tagged images)
[ ] aws ecs update-service --task-definition regmind-staging:___ (previous revision)
[ ] sleep 120
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
