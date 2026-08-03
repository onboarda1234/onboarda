# Operator runsheet — remaining quarantine, DR, and monitoring work

This is the executable handoff for the three remediation rows that remain
`◐`. Repository preparation is complete; none of the AWS, RDS, staging, or
production commands below have been executed or claimed by the repository
change that added this file.

Use the commands only after this PR is merged to `main`. Store outputs in the
change ticket or approved evidence store. Never commit credentials, database
URLs, tokens, passwords, or raw customer data.

## 0. Global prerequisites and evidence directory

Required for all items:

- an approved change ticket and named operator;
- a clean checkout of GitHub `main` (GitHub is the source of truth);
- Python 3.11+, AWS CLI v2, `psql`, and `jq`;
- **Node.js with `playwright-core` available** (set `PLAYWRIGHT_NODE_MODULES` if
  it is installed outside this repo) — item 1's *mandatory* gating smoke
  (`scripts/qa/staging_browser_smoke.js`) exits 2 without it, and it is the
  first step of that change window;
- UTC timestamps on the execution host;
- a durable evidence destination outside `/tmp`.

> ### ⚠️ HOW TO RUN THESE BLOCKS — read before executing anything
>
> **Save each fenced block to a file and run it with `bash -euo pipefail`.
> Do NOT paste blocks into an interactive shell.**
>
> ```bash
> # correct:
> cat > /tmp/step.sh <<'EOF'
> ...paste the block here...
> EOF
> bash -euo pipefail /tmp/step.sh
> ```
>
> Every safety check below is a bare `test ...` line. Without `errexit` a failed
> guard prints **nothing** and execution simply continues into the next command —
> so a pasted block can sail straight past `test "$ENVIRONMENT" = "staging"` or a
> failed datapoint assertion and produce evidence that looks clean. `exit` also
> behaves differently when pasted (it kills the shell and loses the exported
> context these steps depend on). Running each block as a script under
> `-euo pipefail` is what makes the guards actually stop the procedure.
>
> The two genuinely destructive operations fail closed on their own regardless
> (the quarantine script refuses in-process; the teardown re-validates the drill
> identifier), but every *verification* step depends on `errexit` to be trustworthy.

```bash
git fetch origin
git checkout main
git pull --ff-only origin main
export REPO_SHA="$(git rev-parse HEAD)"
git status --short
set -euo pipefail

export CHANGE_TICKET="REPLACE_WITH_CHANGE_TICKET"
export EVIDENCE_DIR="REPLACE_WITH_DURABLE_EVIDENCE_DIRECTORY"
test "$CHANGE_TICKET" != "REPLACE_WITH_CHANGE_TICKET"
test "$EVIDENCE_DIR" != "REPLACE_WITH_DURABLE_EVIDENCE_DIRECTORY"
mkdir -p "$EVIDENCE_DIR"

date -u +%FT%TZ | tee "$EVIDENCE_DIR/execution-started-at.txt"
printf '%s\n' "$REPO_SHA" | tee "$EVIDENCE_DIR/repository-sha.txt"
```

An empty `git status --short` is required. If `main` moves during the change,
continue on the recorded `REPO_SHA`; do not silently mix script versions.

---

## 1. Staging test-login quarantine

Register row: `ops-enforce-staging-sha-alignment-gate`, ADMIN-AUDIT-006.

### Prerequisites

All are mandatory:

1. Repoint the approved `STAGING_QA_EMAIL` secret away from both
   `raj.patel@onboarda.com` and `m.dubois@onboarda.com`.
2. Run the authenticated staging browser smoke successfully with that
   replacement identity and retain `report.json`.
3. Execute in the staging task/network context with the real staging
   `DATABASE_URL` injected from the approved secret source.
4. Obtain the exact hostname from that resolved staging DSN. Set
   `QUARANTINE_ALLOWED_DB_HOST` to that exact hostname, with no scheme, port,
   path, wildcard, or suffix match.
5. Have rollback authority to reactivate an account. The operation changes
   status to `inactive`; it never deletes a row.

Do not run quarantine while either old identity is still the smoke identity.

### Ordered commands

First prove the replacement smoke works:

```bash
cd arie-backend

test -n "$STAGING_QA_EMAIL"
test "$STAGING_QA_EMAIL" != "raj.patel@onboarda.com"
test "$STAGING_QA_EMAIL" != "m.dubois@onboarda.com"
test -n "$STAGING_QA_PASSWORD"

export STAGING_BASE_URL="https://staging.regmind.co"
export STAGING_SMOKE_OUT_DIR="$EVIDENCE_DIR/staging-smoke-replacement"

# The smoke MUST actually pass. `staging_browser_smoke.js` writes report.json
# from a `finally` block AND from its top-level catch, so the file exists (and
# is non-empty) even on a total failure — `test -s report.json` would happily
# green-light a broken replacement identity and let you proceed to the
# irreversible quarantine. Assert the exit code AND the per-check results.
node scripts/qa/staging_browser_smoke.js
smoke_rc=$?
test "$smoke_rc" -eq 0
jq -e '[.checks[]?] | length > 0 and all(.ok == true)' \
  "$STAGING_SMOKE_OUT_DIR/report.json" > /dev/null
```

If `jq` reports a different shape for `.checks`, open `report.json` and confirm
every check passed by inspection before continuing — do **not** fall back to a
mere existence test.

In the staging DB execution context, set and validate the positive guard:

```bash
test "$ENVIRONMENT" = "staging"
test -n "$DATABASE_URL"

export ALLOW_TEST_LOGIN_QUARANTINE="1"
export QUARANTINE_ALLOWED_DB_HOST="REPLACE_WITH_EXACT_STAGING_RDS_HOSTNAME"
test "$QUARANTINE_ALLOWED_DB_HOST" != "REPLACE_WITH_EXACT_STAGING_RDS_HOSTNAME"

python - <<'PY'
import os
from urllib.parse import urlparse

resolved = (urlparse(os.environ["DATABASE_URL"]).hostname or "").lower()
allowed = os.environ["QUARANTINE_ALLOWED_DB_HOST"].strip().lower()
assert resolved == allowed, (
    f"refused: DATABASE_URL resolves to {resolved!r}, allowlist is {allowed!r}"
)
print(f"exact staging DB host confirmed: {resolved}")
PY
```

Dry-run, review the complete account plan, then apply:

```bash
python scripts/quarantine_staging_test_logins.py \
  | tee "$EVIDENCE_DIR/test-login-quarantine-dry-run-before.txt"

python scripts/quarantine_staging_test_logins.py \
  --apply \
  --confirm I-UNDERSTAND-STAGING-LOGIN-QUARANTINE \
  --actor "$CHANGE_TICKET" \
  | tee "$EVIDENCE_DIR/test-login-quarantine-apply.txt"
```

Verify the sequential rerun is empty and the status/audit evidence exists:

```bash
python scripts/quarantine_staging_test_logins.py \
  | tee "$EVIDENCE_DIR/test-login-quarantine-dry-run-after.txt"

psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -c \
  "SELECT email, role, status FROM users
   WHERE lower(email) IN
     ('raj.patel@onboarda.com','m.dubois@onboarda.com','l.wei@onboarda.com')
   ORDER BY email;" \
  | tee "$EVIDENCE_DIR/test-login-quarantine-user-status.txt"

psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -c \
  "SELECT action, target, request_id, detail FROM audit_log
   WHERE request_id = 'quarantine:${CHANGE_TICKET}'
   ORDER BY id;" \
  | tee "$EVIDENCE_DIR/test-login-quarantine-audit.txt"
```

If a known quarantined password is available through the approved secret
source, verify one old login returns HTTP `401` without placing the password
in shell history:

```bash
read -r -s QUARANTINED_TEST_PASSWORD
printf '\n'
export QUARANTINED_TEST_PASSWORD
python - <<'PY' > "$EVIDENCE_DIR/quarantined-login-payload.json"
import json
import os

print(json.dumps({
    "email": "raj.patel@onboarda.com",
    "password": os.environ["QUARANTINED_TEST_PASSWORD"],
}))
PY
chmod 600 "$EVIDENCE_DIR/quarantined-login-payload.json"
curl -sS -o "$EVIDENCE_DIR/quarantined-login-response.json" \
  -w '%{http_code}\n' \
  -H 'Content-Type: application/json' \
  --data-binary "@$EVIDENCE_DIR/quarantined-login-payload.json" \
  https://staging.regmind.co/api/auth/officer/login \
  | tee "$EVIDENCE_DIR/quarantined-login-http-status.txt"
unset QUARANTINED_TEST_PASSWORD
rm "$EVIDENCE_DIR/quarantined-login-payload.json"
test "$(tr -d '\n' < "$EVIDENCE_DIR/quarantined-login-http-status.txt")" = "401"
```

The temporary request file contains a password and must be removed as shown.
Do not attach it to evidence.

### Evidence template

```yaml
item: staging-test-login-quarantine
change_ticket:
operator:
repository_sha:
started_at_utc:
completed_at_utc:
replacement_smoke_email: # address only; no password
replacement_secret_updated_at_utc:
replacement_smoke_report:
replacement_smoke_result: # PASS/FAIL
resolved_database_host:
quarantine_allowed_db_host:
exact_host_match: # true/false
dry_run_before_output:
planned_officer_count:
planned_client_count:
apply_output:
dry_run_after_output:
dry_run_after_empty: # true/false
database_status_output:
audit_output:
old_login_http_status: # 401, or "not performed — <reason>"
accounts_deleted: false
result: # PASS/FAIL
exceptions:
reviewer:
```

Do not change the register row to `✅` unless the replacement smoke, exact-host
guard, quarantine, empty rerun, and audit evidence all pass.

Rollback is status-only and requires an audited change:

```sql
UPDATE users SET status='active' WHERE id = 'REPLACE_WITH_EXACT_USER_ID';
UPDATE clients SET status='active' WHERE id = 'REPLACE_WITH_EXACT_CLIENT_ID';
```

---

## 2. DR posture and timed point-in-time restore drill

Register row: P9-8 / DCI-027 / FEO-009. This operator drill is the work that
actually closes the CRITICAL blocker.

### Prerequisites

- AWS credentials for read-only RDS posture plus restore, describe, modify,
  delete, and waiter actions in `af-south-1`;
- an approved change window and cost owner;
- network access from the execution host to the restored RDS security group;
- the staging DB name/user/password from the approved secret source;
- a unique drill identifier; never target or modify `regmind-staging-db`;
- enough account quota and subnet capacity for one temporary RDS instance.

### Ordered commands

Set explicit identifiers and validate that source and target cannot collide:

```bash
cd arie-backend

export AWS_REGION="af-south-1"
export SOURCE_DB_ID="regmind-staging-db"
export DRILL_ID="regmind-dr-drill-$(date -u +%Y%m%d%H%M%S)"
export DR_EVIDENCE="$EVIDENCE_DIR/$DRILL_ID"
mkdir -p "$DR_EVIDENCE"

test "$SOURCE_DB_ID" = "regmind-staging-db"
case "$DRILL_ID" in
  regmind-dr-drill-*) ;;
  *) echo "refused: unsafe drill identifier" >&2; exit 2 ;;
esac
test "$DRILL_ID" != "$SOURCE_DB_ID"
```

Run the read-only posture check immediately before the drill. Its
`rpo_seconds_observed` is the measured PITR lag:

```bash
python scripts/verify_dr_posture.py \
  --instance-id "$SOURCE_DB_ID" \
  --region "$AWS_REGION" \
  --evidence-out "$DR_EVIDENCE/dr-posture.json" \
  | tee "$DR_EVIDENCE/dr-posture-console.txt"

jq -e '.verdict == "PASS"' "$DR_EVIDENCE/dr-posture.json"
jq -e '.rpo_seconds_observed != null and .rpo_seconds_observed >= 0' \
  "$DR_EVIDENCE/dr-posture.json"
jq -r '.rpo_seconds_observed' "$DR_EVIDENCE/dr-posture.json" \
  | tee "$DR_EVIDENCE/rpo-seconds.txt"
```

Start the wall clock immediately before restore. RTO ends only after the
restored database has passed the integrity queries:

```bash
export RTO_START_EPOCH="$(date -u +%s)"
export RTO_START_UTC="$(date -u +%FT%TZ)"
printf '%s\n' "$RTO_START_UTC" | tee "$DR_EVIDENCE/rto-start-utc.txt"

aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier "$SOURCE_DB_ID" \
  --target-db-instance-identifier "$DRILL_ID" \
  --use-latest-restorable-time \
  --region "$AWS_REGION" \
  | tee "$DR_EVIDENCE/restore-request.json"

aws rds wait db-instance-available \
  --db-instance-identifier "$DRILL_ID" \
  --region "$AWS_REGION"

export PGHOST="$(aws rds describe-db-instances \
  --db-instance-identifier "$DRILL_ID" \
  --region "$AWS_REGION" \
  --query 'DBInstances[0].Endpoint.Address' --output text)"
export PGPORT="$(aws rds describe-db-instances \
  --db-instance-identifier "$DRILL_ID" \
  --region "$AWS_REGION" \
  --query 'DBInstances[0].Endpoint.Port' --output text)"
export PGUSER="REPLACE_WITH_STAGING_DB_USER"
export PGDATABASE="REPLACE_WITH_STAGING_DB_NAME"
test "$PGUSER" != "REPLACE_WITH_STAGING_DB_USER"
test "$PGDATABASE" != "REPLACE_WITH_STAGING_DB_NAME"
test -n "$PGPASSWORD"

# PGHOST/PGPORT come from an AWS query that can fail or return "None". If PGHOST
# were empty, psql would silently fall back to the LOCAL UNIX SOCKET and every
# check below would "pass" against the wrong database entirely.
test -n "$PGHOST"; test "$PGHOST" != "None"
test -n "$PGPORT"; test "$PGPORT" != "None"

# Prove WHICH endpoint answered. current_database()/pg_is_in_recovery() are
# IDENTICAL on the source and on an available PITR copy, so on their own they
# identify nothing — record the endpoint alongside them.
printf '%s:%s\n' "$PGHOST" "$PGPORT" | tee "$DR_EVIDENCE/restored-endpoint.txt"

psql -X -v ON_ERROR_STOP=1 -c \
  "SELECT current_database() AS restored_database,
          inet_server_addr() AS server_addr,
          pg_is_in_recovery() AS read_only_recovery,
          now() AT TIME ZONE 'UTC' AS verified_at_utc;" \
  | tee "$DR_EVIDENCE/restored-database-identity.txt"

# ASSERT, don't just record. `-v ON_ERROR_STOP=1` only proves the tables EXIST;
# a restore yielding applications=0 / audit_log=0 would otherwise be written up
# as a PASS. This is the step that closes a CRITICAL blocker — it must fail loudly.
psql -X -v ON_ERROR_STOP=1 -c \
  "SELECT
     (SELECT COUNT(*) FROM applications) AS applications,
     (SELECT COUNT(*) FROM audit_log) AS audit_rows,
     (SELECT COUNT(*) FROM supervisor_audit_log) AS supervisor_rows;" \
  | tee "$DR_EVIDENCE/restored-row-counts.txt"

restored_apps="$(psql -X -At -v ON_ERROR_STOP=1 -c 'SELECT COUNT(*) FROM applications;')"
restored_audit="$(psql -X -At -v ON_ERROR_STOP=1 -c 'SELECT COUNT(*) FROM audit_log;')"
test "$restored_apps"  -gt 0
test "$restored_audit" -gt 0
echo "restored_applications=$restored_apps restored_audit_rows=$restored_audit" \
  | tee "$DR_EVIDENCE/restored-row-count-assertions.txt"

export RTO_VERIFIED_EPOCH="$(date -u +%s)"
export RTO_VERIFIED_UTC="$(date -u +%FT%TZ)"
export RTO_SECONDS="$((RTO_VERIFIED_EPOCH - RTO_START_EPOCH))"
printf '%s\n' "$RTO_VERIFIED_UTC" | tee "$DR_EVIDENCE/rto-verified-utc.txt"
printf '%s\n' "$RTO_SECONDS" | tee "$DR_EVIDENCE/rto-seconds.txt"
test "$RTO_SECONDS" -ge 0
```

The first query is the required restore-integrity verification. If a throwaway
application task can safely point to the drill DSN, additionally call
`GET /api/supervisor/audit/verify` and retain its response. Never repoint the
staging service itself. If the chain check is not performed, say so explicitly
in the evidence; do not imply it passed.

Tear down even when verification failed. The source DB identifier must never
appear as the target of these commands:

```bash
case "$DRILL_ID" in
  regmind-dr-drill-*) ;;
  *) echo "refused: unsafe drill identifier" >&2; exit 2 ;;
esac
test "$DRILL_ID" != "$SOURCE_DB_ID"

aws rds modify-db-instance \
  --db-instance-identifier "$DRILL_ID" \
  --no-deletion-protection \
  --apply-immediately \
  --region "$AWS_REGION" \
  | tee "$DR_EVIDENCE/disable-deletion-protection-request.json"

protection=""
poll_attempt=1
while [ "$poll_attempt" -le 60 ]; do
  if ! protection="$(aws rds describe-db-instances \
      --db-instance-identifier "$DRILL_ID" \
      --region "$AWS_REGION" \
      --query 'DBInstances[0].DeletionProtection' \
      --output text)"; then
    echo "failed to read deletion-protection state" >&2
    exit 1
  fi
  [ "$protection" = "False" ] && break
  echo "waiting for deletion protection to clear ($poll_attempt/60)"
  sleep 10
  poll_attempt=$((poll_attempt + 1))
done
if [ "$protection" != "False" ]; then
  echo "timed out waiting for deletion protection to clear" >&2
  exit 1
fi
date -u +%FT%TZ | tee "$DR_EVIDENCE/deletion-protection-false-at.txt"

aws rds delete-db-instance \
  --db-instance-identifier "$DRILL_ID" \
  --skip-final-snapshot \
  --delete-automated-backups \
  --region "$AWS_REGION" \
  | tee "$DR_EVIDENCE/delete-request.json"

aws rds wait db-instance-deleted \
  --db-instance-identifier "$DRILL_ID" \
  --region "$AWS_REGION"
date -u +%FT%TZ | tee "$DR_EVIDENCE/db-instance-deleted-at.txt"
```

The deletion-protection poll and `db-instance-deleted` waiter are mandatory.
The drill instance is billable until the waiter succeeds.

### Evidence template

```yaml
item: p9-8-dci-027-dr-restore-drill
change_ticket:
operator:
reviewer:
repository_sha:
aws_account_id:
region:
source_db_instance:
drill_db_instance:
posture_evidence_json:
posture_verdict: # PASS/FAIL
backup_retention_days:
deletion_protection_source:
storage_encrypted:
latest_restorable_time_utc:
rpo_seconds_observed: # exact PITR lag from dr-posture.json
rto_started_at_utc: # immediately before restore request
rto_verified_at_utc: # after restored DB integrity queries succeed
rto_seconds_observed: # verified epoch - start epoch
restore_request_evidence:
restored_row_counts_evidence:
restored_database_identity_evidence:
supervisor_hash_chain_check: # PASS/FAIL/not performed
supervisor_hash_chain_evidence:
deletion_protection_false_at_utc:
delete_request_evidence:
db_instance_deleted_at_utc:
teardown_complete: # true/false
result: # PASS/FAIL
exceptions:
```

Do not close DCI-027 without both measured values, successful restore
verification, and completed teardown:

- RPO = posture report `latest_restorable_time` lag.
- RTO = wall-clock restore start through successful restored-DB verification.

---

## 3. Production monitoring, paging, and datapoint verification

Register row: P9-10 / DCI-030 / FEO-011.

### Prerequisites

- the production ECS/logging environment exists (P9-4); staging execution does
  not close this production row;
- the exact shared production backend/worker CloudWatch log group;
- AWS permissions for Logs metric filters, CloudWatch alarms, and SNS reads;
- an SNS topic ARN in the target account/region with at least one
  `Confirmed` subscription owned by the named on-call rota;
- a recent production worker heartbeat and log ingestion window.

The script is convergent: reruns update the same named filters and alarms.
It now refuses `--apply` without `--alarm-action-arn`.

### Ordered commands

Set and validate the target. Replace values with the deployed production
identifiers; do not use the staging log group or topic:

```bash
cd arie-backend

export AWS_REGION="af-south-1"
export MONITORING_ENVIRONMENT="production"
export PRODUCTION_LOG_GROUP="REPLACE_WITH_PRODUCTION_LOG_GROUP"
export ALARM_ACTION_ARN="REPLACE_WITH_CONFIRMED_SNS_TOPIC_ARN"
export MONITORING_EVIDENCE="$EVIDENCE_DIR/production-monitoring"
mkdir -p "$MONITORING_EVIDENCE"

test "$MONITORING_ENVIRONMENT" = "production"
test "$PRODUCTION_LOG_GROUP" != "REPLACE_WITH_PRODUCTION_LOG_GROUP"
test "$PRODUCTION_LOG_GROUP" != "/ecs/regmind-staging"
test "$ALARM_ACTION_ARN" != "REPLACE_WITH_CONFIRMED_SNS_TOPIC_ARN"
case "$ALARM_ACTION_ARN" in
  arn:aws:sns:af-south-1:*) ;;
  *) echo "refused: SNS topic is not in af-south-1" >&2; exit 2 ;;
esac
```

Prove the log group exists and the SNS topic has a confirmed subscription:

```bash
aws logs describe-log-groups \
  --region "$AWS_REGION" \
  --log-group-name-prefix "$PRODUCTION_LOG_GROUP" \
  --query 'logGroups[].logGroupName' \
  --output json \
  | tee "$MONITORING_EVIDENCE/log-groups.json"

jq --arg group "$PRODUCTION_LOG_GROUP" -e \
  'index($group) != null' \
  "$MONITORING_EVIDENCE/log-groups.json"

aws sns list-subscriptions-by-topic \
  --region "$AWS_REGION" \
  --topic-arn "$ALARM_ACTION_ARN" \
  --query 'Subscriptions[].{Protocol:Protocol,Endpoint:Endpoint,SubscriptionArn:SubscriptionArn}' \
  --output json \
  | tee "$MONITORING_EVIDENCE/sns-subscriptions.json"

jq '[.[] | select(.SubscriptionArn |
      startswith("arn:aws:sns:af-south-1:"))] | length' \
  "$MONITORING_EVIDENCE/sns-subscriptions.json" \
  | tee "$MONITORING_EVIDENCE/confirmed-subscription-count.txt"
test "$(tr -d '\n' \
  < "$MONITORING_EVIDENCE/confirmed-subscription-count.txt")" -gt 0
```

Redact endpoints if the evidence destination is not approved for contact
details. A `PendingConfirmation` entry does not satisfy the prerequisite.

Dry-run, review the generated resources, then apply:

```bash
python scripts/provision_production_monitoring.py \
  --region "$AWS_REGION" \
  --environment "$MONITORING_ENVIRONMENT" \
  --log-group "$PRODUCTION_LOG_GROUP" \
  --alarm-action-arn "$ALARM_ACTION_ARN" \
  | tee "$MONITORING_EVIDENCE/dry-run.txt"

python scripts/provision_production_monitoring.py \
  --region "$AWS_REGION" \
  --environment "$MONITORING_ENVIRONMENT" \
  --log-group "$PRODUCTION_LOG_GROUP" \
  --alarm-action-arn "$ALARM_ACTION_ARN" \
  --apply \
  --confirm-production \
  | tee "$MONITORING_EVIDENCE/apply.txt"
```

Verify alarm configuration and paging actions. Alarm state is not health
evidence:

```bash
aws cloudwatch describe-alarms \
  --region "$AWS_REGION" \
  --alarm-names \
    production-screening-queue-backlog-high \
    production-screening-oldest-pending-age-high \
    production-screening-worker-failures \
    production-verification-worker-failures \
    production-verification-worker-heartbeat-missing \
    production-application-error-rate-high \
    production-schema-drift-detected \
  --query 'MetricAlarms[].{Name:AlarmName,Actions:AlarmActions,OKActions:OKActions,TreatMissing:TreatMissingData,Metric:MetricName}' \
  --output json \
  | tee "$MONITORING_EVIDENCE/alarms.json"

jq --arg arn "$ALARM_ACTION_ARN" -e \
  'length == 7 and all(.[];
    (.Actions | index($arn)) != null and
    (.OKActions | index($arn)) != null)' \
  "$MONITORING_EVIDENCE/alarms.json"
```

Wait for normal production log ingestion, then verify the metrics themselves
have datapoints. `OK` is not sufficient: a dead filter with
`TreatMissingData=notBreaching` can also display `OK`.

```bash
export METRIC_END_TIME="$(python - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
PY
)"
export METRIC_START_TIME="$(python - <<'PY'
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"))
PY
)"

aws cloudwatch get-metric-statistics \
  --region "$AWS_REGION" \
  --namespace RegMind/Pilot \
  --metric-name ScreeningQueueDepth \
  --dimensions \
    Name=Environment,Value=production \
    Name=Service,Value=verification-worker \
  --start-time "$METRIC_START_TIME" \
  --end-time "$METRIC_END_TIME" \
  --period 300 \
  --statistics SampleCount \
  --output json \
  | tee "$MONITORING_EVIDENCE/screening-queue-depth-datapoints.json"

# ApplicationErrorCount MUST be checked with Sum, not SampleCount.
# The metric filter is created with "defaultValue": 0
# (provision_production_monitoring.py), so CloudWatch publishes a 0 for EVERY
# non-matching log event. SampleCount is therefore > 0 from ordinary INFO
# traffic even when the `$.level = "ERROR"` pattern matches nothing at all —
# i.e. the datapoint check would "pass" against a completely dead filter, which
# is the exact failure mode this verification exists to catch.
# Sum > 0 requires a real ERROR record to have matched.
aws cloudwatch get-metric-statistics \
  --region "$AWS_REGION" \
  --namespace RegMind/Pilot \
  --metric-name ApplicationErrorCount-production \
  --start-time "$METRIC_START_TIME" \
  --end-time "$METRIC_END_TIME" \
  --period 300 \
  --statistics Sum SampleCount \
  --output json \
  | tee "$MONITORING_EVIDENCE/application-error-count-datapoints.json"

jq '.Datapoints | length' \
  "$MONITORING_EVIDENCE/screening-queue-depth-datapoints.json" \
  | tee "$MONITORING_EVIDENCE/screening-queue-datapoint-count.txt"
jq '[.Datapoints[]?.Sum] | add // 0' \
  "$MONITORING_EVIDENCE/application-error-count-datapoints.json" \
  | tee "$MONITORING_EVIDENCE/application-error-sum.txt"
test "$(tr -d '\n' \
  < "$MONITORING_EVIDENCE/screening-queue-datapoint-count.txt")" -gt 0
test "$(tr -d '\n' < "$MONITORING_EVIDENCE/application-error-sum.txt")" -gt 0
```

> **If `application-error-sum.txt` is 0**, do NOT record the error-rate alarm as
> verified. A zero Sum means no ERROR record matched in the window — which is
> either good news (no errors) or a dead filter, and the two are
> indistinguishable from this evidence alone. Induce one real ERROR log line in
> production (or widen the window to a period known to contain one), re-run, and
> require Sum ≥ 1. Recording `verification_basis: metric_datapoints` off a
> SampleCount that `defaultValue: 0` manufactures is exactly the false evidence
> this section is meant to prevent.

If either assertion fails, investigate the log group, emitted
`$.environment`, service dimension, and filter pattern. Do not close the row
based on alarm state.

### Evidence template

```yaml
item: p9-10-dci-030-production-monitoring
change_ticket:
operator:
reviewer:
repository_sha:
aws_account_id:
region:
environment: production
production_log_group:
sns_topic_arn:
confirmed_subscription_count:
on_call_rota:
on_call_owner:
dry_run_evidence:
apply_evidence:
alarm_configuration_evidence:
all_alarm_actions_match_topic: # true/false
screening_queue_metric_evidence:
screening_queue_datapoint_count:
application_error_metric_evidence:
application_error_datapoint_count:
metric_window_start_utc:
metric_window_end_utc:
verification_basis: metric_datapoints # never "alarm state"
result: # PASS/FAIL
exceptions:
```

Do not change P9-10 to `✅` until production apply, confirmed paging
subscription/rota, and non-empty metric datapoints are evidenced.
