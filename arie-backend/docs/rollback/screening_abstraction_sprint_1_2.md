# Screening Abstraction — Sprint 1–2 Rollback Playbook (AWS ECS)

**Tag:** `v5.0-pre-screening-abstraction`
**Scope:** ComplyAdvantage migration scaffolding (non-authoritative normalized storage)
**Platform:** AWS ECS (Fargate) — `af-south-1`

---

## AWS ECS Staging Environment

| Resource        | Value                      |
| --------------- | -------------------------- |
| AWS Account     | `782913119880`             |
| Region          | `af-south-1`              |
| ECS Cluster     | `regmind-staging`          |
| ECS Service     | `regmind-backend`          |
| Database        | PostgreSQL (Amazon RDS)    |
| Rollback Tag    | `v5.0-pre-screening-abstraction` |

> **Note:** `render.yaml` remains in the repository root for historical reference.
> It is no longer used for deployments. All staging and production infrastructure
> runs on AWS ECS. Do not modify `render.yaml` expecting deployment side-effects.

---

## Flag-Off Procedure

### Development / Testing
```bash
export ENABLE_SCREENING_ABSTRACTION=false
# Or simply unset — defaults to false in all environments
unset ENABLE_SCREENING_ABSTRACTION
```

### Staging (AWS ECS)
1. Update the `ENABLE_SCREENING_ABSTRACTION` environment variable to `false` in the
   ECS task definition for `regmind-backend`:
   ```bash
   aws ecs describe-task-definition \
     --task-definition regmind-backend \
     --region af-south-1 \
     --query 'taskDefinition' > task-def.json
   # Edit task-def.json: set ENABLE_SCREENING_ABSTRACTION to "false"
   aws ecs register-task-definition \
     --cli-input-json file://task-def.json \
     --region af-south-1
   ```
2. Force a new deployment to pick up the updated task definition:
   ```bash
   aws ecs update-service \
     --cluster regmind-staging \
     --service regmind-backend \
     --force-new-deployment \
     --region af-south-1
   ```
3. Wait for the service to reach steady state:
   ```bash
   aws ecs wait services-stable \
     --cluster regmind-staging \
     --services regmind-backend \
     --region af-south-1
   ```
4. Verify: no new rows appear in `screening_reports_normalized` (query via RDS).
5. Verify: existing screening flow unchanged (run a test submission).

### Production (AWS ECS)
1. Update `ENABLE_SCREENING_ABSTRACTION=false` in the production ECS task definition
   using the same procedure as staging (substitute cluster/service names accordingly).
2. Force a new deployment and wait for steady state.
3. Verify: screening submission flow unchanged.
4. Verify: no new rows in `screening_reports_normalized`.

### Expected Observable Behavior After Flag-Off
- Screening submissions proceed exactly as before.
- No normalized records are written.
- Existing normalized records remain in the database (RDS) but are not read by any control.
- Memo generation, risk scoring, approval gates, and backoffice display are unaffected.
- All EX-01 through EX-13 controls remain operational with no change.
- `ENABLE_SCREENING_ABSTRACTION` must remain `false` until the activation gate passes.

---

## Code Revert Decision Tree

```
Is the flag OFF and behavior is normal?
  ├── YES → No code revert needed. Flag-off is sufficient.
  │         Monitor for 48 hours, then decide on cleanup.
  └── NO  → Is there a bug in the screening abstraction code
            that affects runtime even with flag OFF?
              ├── YES → Revert to tag v5.0-pre-screening-abstraction
              │         Build & push image from that tag, then
              │         update the ECS service (see "Image Rollback" below).
              └── NO  → Investigate further. The abstraction layer
                        should have zero effect when flag is OFF.
```

### Image Rollback via ECS

If a code revert is required, deploy the pre-abstraction image:

```bash
# 1. Check out the rollback tag and build the image
git checkout v5.0-pre-screening-abstraction

# 2. Build and push to ECR (substitute your ECR repo URI)
ECR_REPO="782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend"
docker build -t "$ECR_REPO:v5.0-pre-screening-abstraction" ./arie-backend
aws ecr get-login-password --region af-south-1 | \
  docker login --username AWS --password-stdin "$ECR_REPO"
docker push "$ECR_REPO:v5.0-pre-screening-abstraction"

# 3. Register a new task definition pointing to the rollback image tag
# 4. Update the ECS service
aws ecs update-service \
  --cluster regmind-staging \
  --service regmind-backend \
  --force-new-deployment \
  --region af-south-1

aws ecs wait services-stable \
  --cluster regmind-staging \
  --services regmind-backend \
  --region af-south-1
```

### When to Revert to Tag vs When to Disable the Flag
- **Disable the flag** when: the abstraction layer has a bug that only manifests when enabled (e.g., normalized write failure, performance degradation during dual-write).
- **Revert to tag** when: the abstraction code causes import errors, startup failures, test failures, or affects runtime behavior even with the flag disabled.

---

## Normalized Screening Records (RDS / PostgreSQL)

### When to Retain
- If flag-off resolves the issue and you plan to re-enable after a fix.
- Records are non-authoritative and do not affect any control.

### When to Delete
- If reverting to tag and permanently abandoning the abstraction.
- If records contain corrupted data that could confuse future re-enablement.
- Connect to the RDS instance and run:
  ```sql
  DELETE FROM screening_reports_normalized;
  ```

### When to Ignore
- In most cases. The `screening_reports_normalized` table is non-authoritative.
- No EX-validated control reads it. No backoffice display uses it.
- No memo, risk score, or approval decision depends on it.

---

## Migration 007 Verification Checks

Before re-enabling the feature flag or after a rollback, run the following
verification checks against the RDS PostgreSQL database to confirm migration 007
state is consistent.

| Check | Description | SQL / Method |
| ----- | ----------- | ------------ |
| **V1 — Table exists** | `screening_reports_normalized` table must exist. | `SELECT to_regclass('public.screening_reports_normalized');` — must return non-NULL. |
| **V2 — Schema columns correct** | All expected columns are present with correct types. | `SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'screening_reports_normalized' ORDER BY ordinal_position;` — compare against migration 007 definition. |
| **V3 — is_authoritative constraint** | The `is_authoritative` column has a CHECK or DEFAULT constraint ensuring it defaults to `false`. | `SELECT column_default FROM information_schema.columns WHERE table_name = 'screening_reports_normalized' AND column_name = 'is_authoritative';` — must return `false`. |
| **V4 — Indexes exist** | Required indexes on the normalized table are present. | `SELECT indexname FROM pg_indexes WHERE tablename = 'screening_reports_normalized';` — verify expected indexes. |
| **V5 — Schema version tracked** | Migration 007 is recorded in the schema version tracking table. | `SELECT * FROM schema_migrations WHERE version = '007';` — must return a row. |
| **V6 — No data integrity issues** | No orphaned or corrupted rows in the normalized table. | `SELECT COUNT(*) FROM screening_reports_normalized WHERE application_id IS NULL OR provider IS NULL;` — must return `0`. |

All six checks (V1–V6) must pass before the activation gate is considered satisfied.

---

## Audit-Log Implications of Rollback

- Flag-off: audit logs continue to record all screening operations normally. No audit gap.
- Code revert to tag: no audit entries are lost. The abstraction layer only adds new audit-adjacent records (normalized writes). Reverting removes the dual-write path but does not alter existing audit entries.
- The `screening_reports_normalized` table is explicitly excluded from audit before_state/after_state diffs (it is non-authoritative scaffolding).

---

## Staging Rollback Test Steps (AWS ECS)

1. **Pre-test:** Record a test application ID that has both a legacy `screening_report` and a `screening_reports_normalized` row in the RDS database.
2. **Flag off:** Update the ECS task definition to set `ENABLE_SCREENING_ABSTRACTION=false`.
3. **Redeploy:** Force a new deployment on the `regmind-staging` cluster:
   ```bash
   aws ecs update-service \
     --cluster regmind-staging \
     --service regmind-backend \
     --force-new-deployment \
     --region af-south-1
   aws ecs wait services-stable \
     --cluster regmind-staging \
     --services regmind-backend \
     --region af-south-1
   ```
4. **Submit a new application** through the portal and trigger screening.
5. **Verify:**
   - Application screening completes normally.
   - `prescreening_data.screening_report` is populated correctly (query RDS).
   - No new row appears in `screening_reports_normalized`.
   - Memo generation works.
   - Approval gates evaluate correctly.
   - Backoffice displays screening data correctly.
6. **Check existing data:** Confirm the pre-test application's legacy screening report is unchanged in RDS.
7. **Log review:** Check ECS task logs (CloudWatch) for errors related to screening abstraction:
   ```bash
   aws logs filter-log-events \
     --log-group-name /ecs/regmind-backend \
     --filter-pattern "screening_abstraction" \
     --region af-south-1
   ```

---

## Emergency Rollback Approval

**Contact:** Project lead or designated on-call compliance engineer.

If no specific contact is defined, follow the standard incident response process:
1. Disable the flag immediately via ECS task definition update (no approval needed for flag-off).
2. Notify the project lead within 1 hour.
3. If code revert (image rollback) is needed, obtain approval before deploying the reverted tag.

---

## Activation Gate

`ENABLE_SCREENING_ABSTRACTION` must remain `false` (OFF) in all environments until
the following activation gate criteria are satisfied:

1. All migration 007 verification checks (V1–V6) pass on the target environment.
2. Staging shadow-parity results show zero drift for ≥ 48 hours.
3. Project lead signs off on activation.

Do **not** set the flag to `true` without completing the gate.

---

## Known Sprint 2 Limitations

### Webhook-Driven Drift

Sumsub webhook-triggered updates may mutate the legacy `screening_report` (in `prescreening_data`) after the initial normalization, leaving the `screening_reports_normalized` record stale.

**This is accepted only because:**
- Normalized storage is non-authoritative in Sprint 2.
- No EX-validated control reads it.
- Sprint 2 is migration scaffolding only.

**Sprint 3 must resolve webhook-driven drift** before any downstream consumer reads normalized records. Options include:
- Re-normalizing on webhook receipt.
- Listening for `prescreening_data` mutations and updating the normalized copy.
- Making webhook updates write to both stores atomically.

### No Scheduled Parity Job

The staging shadow-parity check is a manual script (`scripts/staging_shadow_parity.py`). No external scheduler (Celery, APScheduler, cron, EventBridge) exists in the current infrastructure. Automated scheduled execution requires infrastructure changes and is deferred to Sprint 3.

### No External Alerting

Parity failures are logged to stdout/stderr (CloudWatch via ECS) only. No external alerting mechanism (PagerDuty, Slack webhook, SNS, email) is wired. External alerting integration is deferred to Sprint 3.
