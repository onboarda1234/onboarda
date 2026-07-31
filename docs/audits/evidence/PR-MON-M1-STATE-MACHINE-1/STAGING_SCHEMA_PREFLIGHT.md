# PR-MON-M1-STATE-MACHINE-1 — staging schema preflight

Captured: `2026-07-31T03:12:13.624740Z`
Observed deployed SHA: `b5fb23276bacfc8aa543f5e31963e253c3ff8ab8`

This is point-in-time read-only evidence captured before the branch's final
rebase to `85c70431a2d2a2f4bd6dd3078257d5f22d92bad4`. The SHA is intentionally
preserved as observed; current deployment and dataset compatibility must be
reconfirmed through the fail-closed pre-merge/deployment gates.
Mode: authenticated, read-only SQL executed in one healthy staging backend task

## Fresh pre-merge revalidation

Revalidated: `2026-07-31T08:33:24Z`

- staging deployed immutable SHA
  `307b0d7bfc6ab855837f1c9b01f6d182748b8f2a`, digest
  `sha256:0e1f61e565d9a20db16b60fdc26e8bc303fcdf9013bc86398399d4647174f170`;
- backend task definition `regmind-staging:990`, 2/2 running and zero
  pending, rollout complete;
- verification-worker task definition
  `regmind-verification-worker:438`, 6/6 running and zero pending, rollout
  complete;
- all four governed Monitoring flag environment entries are absent and
  therefore evaluate through the documented fail-closed default OFF;
- SQL session set `TRANSACTION READ ONLY` before inspection and rolled back;
- 19 alerts: `open=13`, `dismissed=3`, `resolved=1`, `escalated=1`,
  `routed_to_edd=1`;
- zero off-vocabulary statuses;
- zero review-control rows, zero pending rows, and zero duplicate-pending
  alert groups;
- `schema_version` remains 053;
- the future `monitoring_alerts_status_check` constraint and
  `transition_evidence`/`source_alert_status` columns are not yet present.

Result: the current regulated alert dataset and schema still satisfy the
fail-closed assumptions for migrations 054–056. No row, schema object,
service setting, or feature flag was changed.

## Final pre-merge revalidation and inventory attribution

Revalidated: `2026-07-31T11:40:19Z`

- staging served immutable main SHA
  `85c70431a2d2a2f4bd6dd3078257d5f22d92bad4`, digest
  `sha256:fff009eed8150f0b84ff2caf0e8400bed600ce8241384b2a471aee1c8aaf9147`;
- backend task definition `regmind-staging:991` was 2/2 running, and worker
  task definition `regmind-verification-worker:439` was 6/6 running; both had
  zero pending tasks and completed rollouts;
- both ALB targets were healthy;
- all four governed Monitoring flag environment entries were absent and
  therefore evaluated through the fail-closed default OFF;
- the SQL session again enforced `TRANSACTION READ ONLY` and rolled back;
- 22 alerts: `open=16`, `dismissed=3`, `resolved=1`, `escalated=1`,
  `routed_to_edd=1`;
- zero off-vocabulary statuses, review-control rows, pending rows, or
  duplicate-pending alert groups; and
- schema version remained 053, without the future status constraint, review
  columns, or partial unique index.

The inventory evolved from 19 to 22 after the earlier observation. A
read-only attribution found three new non-fixture, canonical `open` alerts
created by existing ComplyAdvantage ingestion: two through the historical
subscription-seed backfill and one through the live webhook path. CloudWatch
operational evidence for the creation window recorded two completed backfill
runs with one inserted row each and one live `MonitoringAlertCreated` event;
it recorded no `ERROR` or `CRITICAL` event in that window. Those paths predate
this PR and do not reference any of the four governed Monitoring feature
flags. No customer identifier, application identifier, or source reference is
included in this evidence.

Result: the attributed runtime growth is compatible with migrations 054–056.
It did not introduce a noncanonical status, review-ledger conflict, schema
conflict, or feature activation, and no row was changed by either preflight.

Runtime evidence captured at `2026-07-31T03:12:13.624740Z`
(pre-rebase observation):

- backend task `b9e91f2a47d54bc5b2877f4b6e977d88`;
- backend task definition `regmind-staging:989`;
- exact image tag `b5fb23276bacfc8aa543f5e31963e253c3ff8ab8`;
- image digest
  `sha256:a98c9286479d1e32310f1ae4e402d3455abfc9904d7e2ee820374713443481d5`;
- `GIT_SHA` and `IMAGE_TAG` both equal the deployed SHA;
- backend service 2/2 and verification-worker service 6/6, with zero pending
  tasks and completed rollouts.

The database session was explicitly set to `READ ONLY`. It was rolled back and
closed after the queries.

## Dataset compatibility

The first two point-in-time observations contained 19 Monitoring Alerts:

| Status | Count |
|---|---:|
| `open` | 13 |
| `dismissed` | 3 |
| `resolved` | 1 |
| `escalated` | 1 |
| `routed_to_edd` | 1 |

All values are members of `monitoring_alert_state_machine_v1`. The status
constraint precondition is therefore satisfied without rewriting alert data.
The combined NULL, blank, and noncanonical-status offender count was `0`.
The final pre-merge observation contained 22 alerts after the attributed
existing ingestion described above; its combined offender count was also `0`.

## Pending-review uniqueness preflight

The fail-closed duplicate query returned zero groups:

```sql
SELECT alert_id, COUNT(*) AS count
FROM monitoring_alert_review_requests
WHERE state = 'pending'
GROUP BY alert_id
HAVING COUNT(*) > 1;
```

Result: `0` duplicate-pending alert groups.

This proves the existing staging dataset is compatible with the proposed
partial unique index. The migration does not merge, delete, reject, or
otherwise rewrite a review request.

```sql
SELECT
    COUNT(*) AS total_rows,
    COUNT(*) FILTER (WHERE state = 'pending') AS pending_rows,
    COUNT(DISTINCT alert_id) FILTER (WHERE state = 'pending') AS pending_alerts
FROM monitoring_alert_review_requests;
```

Result:

```text
total_rows=0, pending_rows=0, pending_alerts=0
```

The secondary state-count query returned an empty result set:

```sql
SELECT state, COUNT(*) AS row_count
FROM monitoring_alert_review_requests
GROUP BY state
ORDER BY state;
```

Consequently, migration 056 cannot strand a historical pending request with a
NULL `source_alert_status`: staging currently has no review-request rows. No
row was inserted, updated, deleted, approved, rejected, or otherwise changed
during the preflight.

## Existing review-request shape

Before this PR, staging has the following
`monitoring_alert_review_requests` columns:

`id`, `alert_id`, `tier`, `requested_outcome`, `dismissal_reason`,
`rationale`, `evidence_ref`, `state`, `initiated_by`, `initiated_at`,
`approved_by`, `approved_at`, `approval_note`, `rejection_reason`,
`second_review_bypassed`, `sampled_for_qa`.

Existing indexes:

- primary key on `id`;
- non-unique index on `alert_id`;
- non-unique index on `state`.

The proposed typed `transition_evidence` and exact `source_alert_status`
columns are additive. The partial unique pending-review index is also
additive and is installed only after the duplicate preflight passes.

The latest applied file migration is `053`. Staging has no status-vocabulary
constraint yet, no `transition_evidence` or `source_alert_status` column, and
no pending-review partial unique index. Migrations 054–056 therefore remain
available and compatible with the observed schema.

## Feature-governance boundary

All four governed Monitoring flags evaluated exactly OFF:

- `ENABLE_DOCUMENT_RENEWAL_AUTOMATION`;
- `ENABLE_AGENT1_REFRESH_VERIFICATION`;
- `ENABLE_MONITORING_SCREENING_CHANGE`;
- `ENABLE_MONITORING_AUTO_RESOLUTION`.

No new Monitoring workflow consumer was observed.

## PR #902 evidence freshness

Alert 610 remains in canonical status `open`, but its unrelated `reviewed_at`
metadata was populated after the PR #902 audit. That makes PR #902's historical
whole-row fingerprint stale and unsuitable as current rollback evidence. It
does not affect migration 054's status-only compatibility check, the v1
transition service's exact current-status predicate, or migrations 055–056
because the review ledger is empty.

The read-only preflight must be repeated immediately before rollout. Any
unattributed or incompatible status-count drift, noncanonical status,
review-ledger row, duplicate pending request, unexpected constraint/index, or
enabled Monitoring flag is a stop condition.

## Verdict

`COMPATIBLE — NO STAGING DATA MUTATION PERFORMED`
