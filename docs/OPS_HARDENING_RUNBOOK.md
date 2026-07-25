# Ops Hardening Runbook — 2026-07 pack

Operator-executed halves of three register items. Each is idempotent, staging-
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

```bash
# identify the app role if unsure: run SELECT current_user; over the app DSN
psql "$ADMIN_DATABASE_URL" \
  -v app_role=<app_role_name> \
  -v maint_role=regmind_audit_maint \
  -f arie-backend/scripts/apply_audit_log_append_only_grants.sql
```

The script ends with a `has_table_privilege` verification row — expect the app
role `f/f/f` for UPDATE/DELETE/TRUNCATE and `t/t` for INSERT/SELECT. Follow
with one end-to-end check: perform any audited back-office action on staging
and confirm the new `audit_log` row appears (INSERT path unaffected).

Rollback (emergency only): `GRANT UPDATE, DELETE, TRUNCATE ON TABLE audit_log
TO <app_role>;`

---

**Evidence:** after executing, record command outputs against the register rows
in `docs/REMEDIATION_MASTER_LIST.md` (P9-12 → ✅; ops-ticket row → ✅; P10-7 →
fully ✅ closing the ◐).
