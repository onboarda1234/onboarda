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
| **Health endpoints** | `GET /api/health` (liveness), `GET /api/readiness` (deep readiness), `GET /api/config/environment` |
| **AI engine** | Claude API via `anthropic` SDK. Fail-closed in staging/production. |
| **KYC/AML** | Sumsub API (live credentials). Level name requires admin verification. |
| **PII encryption** | Fernet AES-128-CBC. Key pinned in Secrets Manager. |
| **Rate limiting** | In-memory (per-container). Resets on restart. |
| **Token revocation** | In-memory (per-container). Resets on restart. |
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
docker tag regmind-backend:latest 782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:latest

aws ecr get-login-password --region af-south-1 | docker login --username AWS \
  --password-stdin 782913119880.dkr.ecr.af-south-1.amazonaws.com

docker push 782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:$GIT_SHA
docker push 782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:latest
```
If push returns `403 Forbidden`, re-run the login command.

> **Note:** Always push both a SHA-tagged image AND `:latest`. The SHA tag enables deterministic rollback.

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
curl -s https://staging.regmind.co/api/readiness | python3 -m json.tool
```
Expected: `"ready": true`, database `"status": "ok"`, encryption `"status": "ok"`

Also check liveness:
```bash
curl -s https://staging.regmind.co/api/health | python3 -m json.tool
```
Expected: `"status": "ok"`, `"database": {"status": "connected", "type": "postgresql"}`

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
| 1 | Health | `curl https://staging.regmind.co/api/health` | `ok`, `connected`, `postgresql` |
| 2 | Environment | `curl https://staging.regmind.co/api/config/environment` | `staging`, `is_demo: false` |
| 3 | Portal loads | Browser: staging.regmind.co/portal | Login page, no demo data |
| 4 | Back office loads | Browser: staging.regmind.co/backoffice | Login screen, no demo data before login |
| 5 | Dashboard stats | After login, scroll to monitoring section | "—" and "Monitoring not yet active" |
| 6 | Agent Health | Navigate to Agent Health | "Not Yet Active" placeholder |
| 7 | Regulatory page | Navigate to Regulatory Intelligence | "No regulatory documents yet" |
| 8 | KPI AI section | KPI Dashboard → AI Performance | Placeholder, not 87.3%/92.1%/99.8% |

### Log review
```bash
aws logs filter-log-events --log-group-name /ecs/regmind-staging \
  --region af-south-1 --filter-pattern "ERROR" \
  --start-time $(( $(date +%s) - 600 ))000 --limit 10 \
  --query 'events[*].message' --output text
```
Check for: `connection pool exhausted`, `falling back to mock mode`, `Sumsub create applicant failed: 404`.

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
curl -s https://staging.regmind.co/api/health | python3 -m json.tool
```

### If `:latest` has been overwritten (rollback not possible via ECR)

1. Check out the previous known-good git commit locally
2. Rebuild the Docker image from that commit
3. Push as `:latest`
4. Force redeploy ECS

**Database note:** Task definition rollback does NOT roll back database migrations. If a migration was applied during the failed deploy, the old code may encounter schema mismatches. Assess backward compatibility before rolling back.

---

## 7. Known Operational Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **DB connection pool exhaustion** | Medium | All endpoints return 500 | Restart ECS task. Pool is maxconn=5 on db.t3.micro (~20 total). |
| **Sumsub level-name mismatch** | Confirmed | KYC applicant creation fails (404) | Admin verifies level in Sumsub dashboard, updates ECS env var. |
| **JWT invalidation on deploy** | Certain | All users must re-login | Expected. Sessions use 24h expiry. |
| **Browser cache shows stale UI** | Medium | Old interface visible after deploy | Hard refresh (Cmd+Shift+R) or incognito. |
| **In-memory rate limiter resets** | Certain | Brute-force protection absent briefly | Acceptable for pilot. Redis is future. |
| **In-memory token revocation resets** | Certain | Revoked tokens valid until natural 24h expiry | Acceptable for pilot. Redis is future. |
| **Single-container failure** | Low | ~2 min downtime during ECS restart | Production should have min 2 tasks. |
| **`:latest` tag prevents reliable rollback** | Mitigated | Cannot roll back to previous image | SHA-tagged images now pushed alongside `:latest`. Rollback is reliable. |

---

## 8. Incident Triage

### Deployment is unhealthy
```bash
curl -s https://staging.regmind.co/api/health
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

BUILD
[ ] cp ../arie-portal.html . && cp ../arie-backoffice.html .  (run from `arie-backend/`; local builds only; CI does this automatically)
[ ] docker build --platform linux/amd64 -t regmind-backend .
[ ] docker tag → 782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:latest
[ ] aws ecr get-login-password (if >12h since last login)
[ ] docker push

DEPLOY
[ ] aws ecs update-service --desired-count 0
[ ] sleep 30
[ ] aws ecs update-service --desired-count 1 --force-new-deployment
[ ] sleep 120

VERIFY
[ ] /api/readiness → ready: true, encryption: ok, database: ok
[ ] /api/health → ok, connected, postgresql
[ ] /api/config/environment → staging, is_demo: false
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
| **Versioned image tags** (`:$GIT_SHA` alongside `:latest`) | Reliable rollback | ✅ Done |
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
