# Ops Hardening Runbook — 2026-07 pack

Operator-executed halves of the register items listed below. Each is idempotent, staging-
scoped, and changes **no application workflow** (verification queries included).
Register rows: P9-12 · Phase-5/6 screening-queue ops ticket (p95 alarm) ·
P10-7 (RDS-grants half).

Prerequisites: AWS CLI v2 with credentials for `af-south-1` (ECR/CloudWatch/
Logs admin), and the RDS master-user DSN for `regmind-staging-db`.

---

## 1. P9-12 — ECR immutable image tags

The deploy workflow already pushes one unique tag per commit
(`$ECR_REGISTRY/regmind-backend:<github.sha>`, no `:latest`), so immutability
is compatible with normal deploys as-is.

**Flip (staging ECR):**

```bash
aws ecr put-image-tag-mutability \
  --repository-name regmind-backend \
  --image-tag-mutability IMMUTABLE \
  --region af-south-1

# verify
aws ecr describe-repositories --repository-names regmind-backend \
  --region af-south-1 --query 'repositories[0].imageTagMutability'
# expect: "IMMUTABLE"
```

**Known operational caveat — workflow RE-RUNS:** Docker builds are not
byte-reproducible, so re-running the deploy workflow for the *same commit*
produces a different manifest under the same `<sha>` tag and the push will be
rejected (`ImageTagAlreadyExistsException`). Two sanctioned outs:

```bash
# preferred: delete the stale tag, then re-run the workflow
aws ecr batch-delete-image --repository-name regmind-backend \
  --image-ids imageTag=<sha> --region af-south-1
# or: land an empty commit so the re-deploy gets a fresh sha
```

Rollback of the setting itself: rerun with `--image-tag-mutability MUTABLE`.

---

## 2. Screening-queue p95 latency alarm

The backend emits `ScreeningQueueLatencyMs` (Milliseconds) as a
low-cardinality `cloudwatch_metric` log line from `GET /api/screening/queue`
(hard-isolated after the response; cannot affect the queue payload).

```bash
cd arie-backend
# inspect exactly what will be created
python scripts/provision_screening_queue_p95_alarm.py            # dry-run
# create metric filter + p95 alarm (threshold 2000 ms, 3×5-min periods)
python scripts/provision_screening_queue_p95_alarm.py --apply \
  --alarm-action-arn arn:aws:sns:af-south-1:<acct>:regmind-staging-pilot-alerts
```

Defaults: region `af-south-1`, environment `staging`, log group
`/ecs/regmind-staging`, threshold `--threshold-ms 2000`. Verify after ~15 min
of traffic: CloudWatch → Alarms → `staging-screening-queue-p95-latency-high`
shows datapoints (namespace `RegMind/Pilot`, metric `ScreeningQueueLatencyMs`).

---

## 3. P10-7 — append-only `audit_log` grants (staging RDS)

Redundant permission-layer enforcement beneath the merged trigger layer
(#837). The app role loses UPDATE/DELETE/TRUNCATE on `audit_log`; a NOLOGIN
maintenance role receives them for sanctioned retention purges only.
**TRIGGER is deliberately NOT revoked** — the app role owns the table and the
boot path re-creates the append-only triggers every start; revoking TRIGGER
would crash-loop the next deploy.

```bash
# identify the app role if unsure: run SELECT current_user; over the app DSN
psql "$ADMIN_DATABASE_URL" -1 \
  -v app_role=<app_role_name> \
  -v maint_role=regmind_audit_maint \
  -v admin_role=<rds_master_user> \
  -f arie-backend/scripts/apply_audit_log_append_only_grants.sql
# (-1 = single transaction: an aborted run leaves no partial grant state)
```

The script ends with a `has_table_privilege` verification row — expect the app
role `f/f/f` for UPDATE/DELETE/TRUNCATE and `t/t` for INSERT/SELECT. Follow
with TWO end-to-end checks:

1. Perform any audited back-office action on staging and confirm the new
   `audit_log` row appears (INSERT path unaffected).
2. **Redeploy staging** (or force a task restart) and confirm it boots — the
   boot path re-creates the append-only triggers as the app role, proving the
   TRIGGER privilege was preserved.

**Sanctioned purge procedure changes with these grants** (update your copy of
`docs/compliance/MANUAL_PURGE_PROCEDURE.md` habits): the manual
`purge_expired_data("audit_logs", dry_run=False)` run must now execute over an
**admin DSN** with `SET ROLE regmind_audit_maint;` first — the app DSN no
longer holds DELETE. The #837 trigger maintenance window is a table marker
(role-agnostic), so the purge works unchanged under the maintenance role.

Rollback (emergency only): `GRANT UPDATE, DELETE, TRUNCATE ON TABLE audit_log
TO <app_role>;`

---

## 5. Staging test-login quarantine (staging-SHA gate row, ops half)

Origin: ADMIN-AUDIT-006 — "several test-like users in staging", recommended fix
"remove or quarantine stale test accounts". The audit-trail half already
shipped; this is the account half.

**Quarantine, not delete.** ~20 tables carry `REFERENCES users(id)`, so hard-
deleting an officer who ever acted orphans referential history AML
recordkeeping depends on. `status='inactive'` blocks login and preserves every
audit and decision linkage.

```bash
cd arie-backend
# 1. dry-run — lists exactly what would be deactivated, changes nothing
python scripts/quarantine_staging_test_logins.py

# 2. apply — all four conditions are required; any missing one refuses
ENVIRONMENT=staging ALLOW_TEST_LOGIN_QUARANTINE=1 \
python scripts/quarantine_staging_test_logins.py --apply \
  --confirm I-UNDERSTAND-STAGING-LOGIN-QUARANTINE
```

**This script never deletes.** 22 tables carry 48 `REFERENCES users(id)`
columns (memo approver, SAR reviewer, screening second reviewer, EDD decider,
periodic-review decider); orphaning any of them destroys attribution AML
recordkeeping depends on. Deactivation satisfies the finding's "remove **or
quarantine**". Every change writes an `audit_log` row with before/after state.

Protected and never touched: `asudally@onboarda.com` (real operator login) and
the CI smoke account `github-actions-day6-staging-smoke@onboarda.internal` —
`db.ensure_qa_smoke_user()` re-creates **and force-reactivates** it on every
boot, so quarantining it would be silently undone by the next deploy.

**Before running:** `raj.patel@onboarda.com` and `m.dubois@onboarda.com` are the
identities prior staging QA evidence runs used (`STAGING_QA_EMAIL`,
`scripts/qa/staging_browser_smoke.js`). Deactivating them breaks that smoke
path until you point it at a replacement account. Do that first, or accept the
smoke gap for the window.

**Verify:** re-run the dry-run — the plan skips already-inactive accounts, so a
successful run leaves an empty match list. Then confirm login is refused for
one deactivated account, and that the `audit_log` rows exist:

```sql
SELECT action, target, detail FROM audit_log
WHERE request_id LIKE 'quarantine:%' ORDER BY id DESC;
```

Rollback: `UPDATE users SET status='active' WHERE id = '<id>';` (same for
`clients`) — nothing was deleted, so every account is recoverable.

---

## 6. P9-8 / DCI-027 — DR posture check and restore drill

DCI-027 is a **CRITICAL production blocker**. The posture check is scripted;
the timed restore drill is operator-executed and is what actually closes the
row.

**Step 1 — posture (read-only, safe to run any time / on a schedule):**

```bash
cd arie-backend
python scripts/verify_dr_posture.py \
  --instance-id regmind-staging-db --region af-south-1 \
  --evidence-out /tmp/dr-posture-$(date -u +%Y%m%dT%H%M%SZ).json
```

Exit 0 = baseline met: retention ≥ 7 days, deletion protection on, storage
encrypted, and — the check that matters — the **latest restorable time is
within the hour**, which proves point-in-time recovery is actually working
rather than merely configured. Multi-AZ is reported as an advisory (staging is
single-AZ by design; production is tracked separately).

**Step 2 — the timed restore drill (operator, in a change window):**

1. Note the start time (`date -u +%FT%TZ`) and restore to a NEW instance:

   ```bash
   aws rds restore-db-instance-to-point-in-time \
     --source-db-instance-identifier regmind-staging-db \
     --target-db-instance-identifier regmind-dr-drill \
     --use-latest-restorable-time --region af-south-1
   aws rds wait db-instance-available \
     --db-instance-identifier regmind-dr-drill --region af-south-1
   ```

2. Verify integrity against the RESTORED instance (never against staging):

   ```bash
   psql "$DRILL_DSN" -c "SELECT
     (SELECT COUNT(*) FROM applications)        AS applications,
     (SELECT COUNT(*) FROM audit_log)           AS audit_rows,
     (SELECT COUNT(*) FROM supervisor_audit_log) AS supervisor_rows;"
   ```

   The supervisor hash chain has no CLI — verification is
   `GET /api/supervisor/audit/verify`, which needs an app pointed at this DSN.
   Either run a throwaway task with `DATABASE_URL=$DRILL_DSN` and call that
   endpoint, or record row counts only and note the chain check as not
   performed. **Do not repoint the staging service at the drill instance.**

3. Record the wall-clock time from step 1 to a verified step 2 — **that is the
   measured RTO**. The RPO is bounded by the PITR lag reported in step 1.

4. Tear down (the restored instance bills until deleted, and RDS may copy the
   source's deletion protection):

   ```bash
   aws rds modify-db-instance --db-instance-identifier regmind-dr-drill \
     --no-deletion-protection --apply-immediately --region af-south-1
   aws rds delete-db-instance --db-instance-identifier regmind-dr-drill \
     --skip-final-snapshot --delete-automated-backups --region af-south-1
   ```

5. File the evidence JSON plus the measured RTO/RPO against the P9-8 row.

The script deliberately reports `rto_seconds_observed: null` — RTO can only
come from a timed drill, and the tooling must not imply it verified one.

Rollback: none required; the drill creates a separate instance and deletes it.

---

## 7. P9-10 / DCI-030 — production monitoring and alerting

Seven metrics the workers and backend **already emit** have no metric filter
and no alarm (all six `Screening*` metrics plus `SchemaDriftMissingObjects`),
and `VerificationWorkerFailures` has a filter but no alarm. No application
change is needed — the telemetry is already being written.

```bash
cd arie-backend
python scripts/provision_production_monitoring.py            # dry-run
python scripts/provision_production_monitoring.py --apply \
  --alarm-action-arn arn:aws:sns:af-south-1:<acct>:regmind-staging-pilot-alerts
```

**Without `--alarm-action-arn` the alarms are created with no actions and page
nobody** — the dry-run says so explicitly. The SNS topic must have a confirmed
subscription; an unconfirmed subscription is indistinguishable from working
until the first real incident.

Two alarms are worth understanding before you tune them:

* `*-verification-worker-heartbeat-missing` uses `TreatMissingData: breaching`,
  the opposite of every other alarm here. The workers publish their gauges at
  least once per ~60s even when idle, so *absence of data is the signal*. This
  is what catches a task that is running but whose loop is wedged — the
  existing ECS `LiveTaskCount` alarm cannot see that.
* `*-application-error-rate-high` counts `$.level = "ERROR"` records. 4xx is
  logged at WARNING deliberately, so this counts genuine server-side failures
  and cannot be driven by unauthenticated callers.

**Verify by checking the METRIC has datapoints, not the alarm state.** With
`TreatMissingData: notBreaching`, an alarm whose filter never matches also
reads `OK` — so "the alarm is OK" is exactly the reading a dead filter
produces:

```bash
aws cloudwatch get-metric-statistics --region af-south-1 \
  --namespace RegMind/Pilot --metric-name ScreeningQueueDepth \
  --dimensions Name=Environment,Value=staging Name=Service,Value=verification-worker \
  --start-time $(date -u -d '30 minutes ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) --period 300 --statistics SampleCount
```

An empty `Datapoints` list means the filter is not matching — usually because
`$.environment` does not equal what the service actually emits. Repeat for
`ApplicationErrorCount-staging` (no dimensions).

**On-call:** alarm actions must route to a rota with a confirmed subscription.
Recording the rota itself is a commercial/process step outside this repo.

---

**Evidence:** after executing, record command outputs against the register rows
in `docs/REMEDIATION_MASTER_LIST.md` (P9-12 → ✅; ops-ticket row → ✅; P10-7 →
✅ closing the ◐, with the documented residual that the app role remains
table OWNER — full owner separation is future work).
