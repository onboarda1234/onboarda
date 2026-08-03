# Ops Hardening Runbook — 2026-07 pack

Operator-executed halves of three register items. Each is idempotent, staging-
scoped, and changes **no application workflow** (verification queries included).
Register rows: P9-12 · Phase-5/6 screening-queue ops ticket (p95 alarm) ·
P10-7 (RDS-grants half) · item 33 (pilot-scope guard — no-op by default,
documented here for the IaC pin and the verification command).

Prerequisites: AWS CLI v2 with credentials for `af-south-1` (ECR/CloudWatch/
Logs admin), and the RDS master-user DSN for `regmind-staging-db`.

---

## 1. P9-12 — ECR immutable image tags

The staging release path uses one full Git SHA tag per commit and never uses a
mutable convenience tag as release evidence. The workflow now refuses to build,
push, register a task definition, or update ECS unless the repository reports
exactly `IMMUTABLE` with scan-on-push enabled.

Use the pinned, dry-run-first operator control. It verifies the AWS account,
region, repository identity, and current state before proposing a change:

```bash
cd arie-backend
python3 scripts/ops/enforce_ecr_immutability.py

# After reviewing the exact dry-run:
python3 scripts/ops/enforce_ecr_immutability.py \
  --apply \
  --confirm SET-regmind-backend-IMMUTABLE
```

Re-running the tool after activation is a verified no-op. It deliberately has
no `MUTABLE` or rollback option: weakening this control is a security incident,
not a normal deployment recovery action.
Internally, the only mutating AWS operation is the exact scoped
`aws ecr put-image-tag-mutability --image-tag-mutability IMMUTABLE` call,
followed by a fresh postcondition read.

**Workflow reruns:** an immutable repository returns
`ImageTagAlreadyExistsException` for any attempt to push an existing SHA tag.
The workflow therefore checks first:

1. If the full-SHA tag is absent, build and push it once.
2. If it exists, resolve its digest, pull by digest, and require its `git.sha`,
   `git.ref`, `GIT_SHA`, `IMAGE_TAG`, OS, and architecture provenance to match.
3. If provenance differs, stop. Never delete, overwrite, or retag the SHA.

The historical `latest` alias is not release evidence and must never be moved.
Removing a historical alias is a separately reviewed registry-cleanup action;
it is not part of a deploy or rerun.

Every release also gates both the ECR registry scan and a pinned Trivy scan of
the exact digest before ECS mutation. Trivy must report OS-package coverage and
Python-package coverage (`python-pkg` in the pinned 0.72.0 schema; legacy
supported reports may use `pip`). This deployment-local gate is mandatory even
when the separate pull-request scan succeeded. CRITICAL findings are never
accepted. A HIGH finding requires an exact package/version match in the
versioned acceptance manifest, a named owner, technical and reachability
analysis, compensating controls, explicit approval, and a future expiry date.
The current manifest is intentionally empty.

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
maintenance role receives ONLY what the sanctioned purge uses — DELETE (plus
the reads/marker/evidence set), never UPDATE or TRUNCATE. Re-running the
script converges a previously-applied broader revision to this minimal set.
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
`docs/compliance/MANUAL_PURGE_PROCEDURE.md` habits): the manual audit-log
retention purge must now execute over an **admin DSN** with
`SET ROLE regmind_audit_maint` on the SAME connection — the app DSN no longer
holds DELETE. The script grants the maintenance role the minimal purge-path
set (policy read, window marker INSERT/SELECT/DELETE + sequence USAGE,
audit_log SELECT+DELETE, data_purge_log evidence INSERT + sequence USAGE —
completed per Codex 2026-07-25 item 5(b), minimised per Codex 2026-07-26; the
evidence VERIFY read in MANUAL_PURGE_PROCEDURE.md step 6 runs as the admin
role, since the maintenance role deliberately cannot read data_purge_log), and `tests/test_audit_log_grants_pg.py` proves the
literal script + this exact invocation end-to-end on PostgreSQL in CI.
Run it from a one-off task container (app code present):

```python
import os, psycopg2, db, gdpr
conn = psycopg2.connect(os.environ["ADMIN_DATABASE_URL"])   # RDS master DSN
dbw = db.DBConnection(conn, is_postgres=True, database_identity="admin-maint")
dbw.execute('SET ROLE regmind_audit_maint')                  # session role; one connection throughout
print(gdpr.purge_expired_data(dbw, "audit_logs", purged_by="<operator>", dry_run=True))
# review the dry-run counts, then repeat with dry_run=False
```

Rollback (emergency only): `GRANT UPDATE, DELETE, TRUNCATE ON TABLE audit_log
TO <app_role>;`

---

## 4. Item 33 — pilot-scope guard (`PILOT_SCOPE`)

Server-side veto above the enterprise feature flags: when active, the
enterprise modules (SAR/STR, Regulatory Intelligence, AI Compliance
Supervisor, Supervisor Audit) are refused **regardless of individual flag
values**, including values set outside version control.

**No operator action is required to activate it** — the guard defaults ON in
staging and production. It converts the enterprise exclusion from a convention
into an enforced, testable control.

**Two behaviour notes — read before deploying:**

1. The veto is a no-op *only for the default flag values*. Every enterprise
   flag defaults False in staging/production, but deployed values can come from
   the ECS task definition or the Render dashboard (`render.yaml` pins three
   `sync: false`), and those are not visible in this repo. **Before deploying,
   confirm `ENABLE_AI_SUPERVISOR`, `ENABLE_REGULATORY_INTELLIGENCE_FULL` and
   `ENABLE_SAR_WORKFLOW` are not set true** in the live `regmind-staging` task
   definition or the Render dashboard — if any is, the veto will newly refuse
   that module (which is the intent, but it should be a deliberate flip).
2. Independently of `PILOT_SCOPE`, this change adds the missing enterprise gate
   to `POST /api/applications/:id/memo/supervisor/run` and
   `GET /api/applications/:id/memo/supervisor`. Those two routes had no gate at
   all; on staging/production (`ENABLE_AI_SUPERVISOR` False by default) they
   now return 403 where they previously served. No UI calls them — verified
   against `arie-backoffice.html` and `arie-portal.html` — and memo approval
   does not depend on them (the supervisor result is embedded during memo
   generation and read from there by the approve gate).

Pin it explicitly in IaC so the control is reviewable (recommended, optional):

```yaml
# render.yaml, per service envVars — an explicit value, NOT `sync: false`
- key: PILOT_SCOPE
  value: "true"
```

**To verify on staging** (expect HTTP 403 with
`{"code":"enterprise_module_inactive"}`):

```bash
curl -si -H "Authorization: Bearer $STAGING_OFFICER_TOKEN" \
  https://staging.regmind.co/api/sar | head -1
```

**Parsing is fail-closed and inverted on purpose:** the guard turns off only
on an explicit `false`/`0`/`no`/`off`. Any other value — including a typo like
`PILOT_SCOPE=ture` — leaves it ON, because a stuck-on guard refuses an
enterprise module while a stuck-off guard exposes one.

To deliberately run an enterprise-scope environment, set `PILOT_SCOPE=false`;
the per-module flags then decide as before.

---

## 5. Staging test-login quarantine (staging-SHA gate row, ops half)

Origin: ADMIN-AUDIT-006 — "several test-like users in staging", recommended fix
"remove or quarantine stale test accounts". The audit-trail half already
shipped; this is the account half.

```bash
cd arie-backend
# 1. dry-run — lists exactly what would be deactivated, changes nothing
python scripts/quarantine_staging_test_logins.py

# 2. apply — ALL FIVE conditions are required; any missing one refuses.
#    QUARANTINE_ALLOWED_DB_HOST must be the EXACT staging RDS hostname the
#    resolved connection points at (find it: the host part of DATABASE_URL in
#    the regmind-staging task definition). A denylist alone proved bypassable —
#    it accepted the demo DB and an arbitrary PG host (Codex 2026-07-26).
ENVIRONMENT=staging ALLOW_TEST_LOGIN_QUARANTINE=1 \
QUARANTINE_ALLOWED_DB_HOST=<staging-rds-hostname> \
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

**Mandatory before running:** `raj.patel@onboarda.com` and
`m.dubois@onboarda.com` are the identities prior staging QA evidence runs used
(`STAGING_QA_EMAIL`, `scripts/qa/staging_browser_smoke.js`). Repoint the
`STAGING_QA_EMAIL` secret to an approved replacement account and complete one
successful authenticated smoke with that replacement **before** quarantine.
Do not accept a smoke gap and do not count this row complete without that
evidence.

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

> ⚠️ **The executable commands live in ONE place: the
> [operator runsheet §2](OPERATOR_RUNSHEET_REMAINING_OPS.md#2-dr-posture-and-timed-point-in-time-restore-drill).
> Run them from there, not from this section.**
>
> This section previously carried its own copy of the drill using a *fixed*
> instance id (`regmind-dr-drill`), while the runsheet creates a *timestamped*
> one (`regmind-dr-drill-<UTC>`). An operator who created the instance with the
> runsheet and then tore down with this section would have targeted an
> identifier that does not exist — leaving a **live RDS instance holding a full
> copy of staging data running and billing indefinitely**. The command bodies
> are therefore deliberately NOT duplicated here; a single source of truth is
> the only way this cannot drift again.

The shape of the drill (for review purposes — do not execute from this list):

1. Note the start time and restore to a **new, uniquely named** instance
   (`regmind-dr-drill-<UTC timestamp>`), never over the source.
2. Verify integrity against the **restored** instance only, asserting non-zero
   row counts — never against staging, and never by repointing the staging
   service at the drill instance.
3. The **measured RTO** is wall-clock from step 1 to a *verified* step 2 (not
   merely "instance available"); the RPO is the PITR lag from Step 1's posture
   check.
4. Tear down: `--apply-immediately` does **not** block, so poll until
   `DeletionProtection=False`, then delete, then `wait db-instance-deleted`.
   The restored copy bills — and holds real data — until this completes.
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
python scripts/provision_production_monitoring.py \
  --environment production --log-group "$PRODUCTION_LOG_GROUP" \
  --alarm-action-arn "$ALARM_ACTION_ARN"                    # dry-run
python scripts/provision_production_monitoring.py \
  --environment production --log-group "$PRODUCTION_LOG_GROUP" \
  --alarm-action-arn "$ALARM_ACTION_ARN" \
  --apply --confirm-production
```

`PRODUCTION_LOG_GROUP` must be the deployed production backend/worker log
group; do not substitute the staging group and call P9-10 complete.
`ALARM_ACTION_ARN` must be an SNS topic in the target account/region with a
confirmed subscription into the named on-call rota.

**Without `--alarm-action-arn` alarms page nobody.** The script now refuses
`--apply` without it. The SNS topic must have a confirmed subscription; an
unconfirmed subscription is indistinguishable from working until the first
real incident.

Two alarms are worth understanding before you tune them:

* `*-verification-worker-heartbeat-missing` uses `TreatMissingData: breaching`,
  the opposite of every other alarm here. The workers publish their gauges at
  least once per ~60s even when idle, so *absence of data is the signal*. This
  is what catches a task that is running but whose loop is wedged — the
  existing ECS `LiveTaskCount` alarm cannot see that.
* `*-application-error-rate-high` counts `$.level = "ERROR"` records. 4xx is
  logged at WARNING deliberately, so this counts genuine server-side failures
  and cannot be driven by unauthenticated callers. The metric is
  **dimensionless** (ERROR records carry no `environment` field) and scoped by
  metric name: `ApplicationErrorCount-<environment>`.

**Verify by checking the METRIC has datapoints, not the alarm state.** With
`TreatMissingData: notBreaching`, an alarm whose filter never matches also
reads `OK` — so "the alarm is OK" is exactly the reading a dead filter
produces:

```bash
aws cloudwatch get-metric-statistics --region af-south-1 \
  --namespace RegMind/Pilot --metric-name ScreeningQueueDepth \
  --dimensions Name=Environment,Value=production Name=Service,Value=verification-worker \
  --start-time $(date -u -d '30 minutes ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) --period 300 --statistics SampleCount
```

An empty `Datapoints` list means the filter is not matching — usually because
`$.environment` does not equal what the service actually emits. Repeat for
`ApplicationErrorCount-production` (no dimensions).

**On-call:** alarm actions must route to a rota with a confirmed subscription.
Recording the rota itself is a commercial/process step outside this repo.

The exact commands, prerequisites, and evidence templates for §§5–7 are
consolidated in
[`OPERATOR_RUNSHEET_REMAINING_OPS.md`](OPERATOR_RUNSHEET_REMAINING_OPS.md).

---

**Evidence:** after executing, record command outputs against the register rows
in `docs/REMEDIATION_MASTER_LIST.md` (P9-12 → ✅; ops-ticket row → ✅; P10-7 →
✅ closing the ◐, with the documented residual that the app role remains
table OWNER — full owner separation is future work).
