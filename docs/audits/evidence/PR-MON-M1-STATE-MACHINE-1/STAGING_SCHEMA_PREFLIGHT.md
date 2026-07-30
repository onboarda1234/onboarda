# PR-MON-M1-STATE-MACHINE-1 — staging schema preflight

Captured: `2026-07-30T07:10:18Z`
Deployed/base SHA: `9fa083c1ac185ea65bfa8515dff315eb254701a5`
Mode: authenticated, read-only SQL executed in one healthy staging backend task

## Dataset compatibility

The current staging inventory remains 19 Monitoring Alerts:

| Status | Count |
|---|---:|
| `open` | 13 |
| `dismissed` | 3 |
| `resolved` | 1 |
| `escalated` | 1 |
| `routed_to_edd` | 1 |

All values are members of `monitoring_alert_state_machine_v1`. The status
constraint precondition is therefore satisfied without rewriting alert data.

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

At `2026-07-30 07:41:46.004068+00:00`, a second authenticated read-only
preflight ran in backend task `ba97cef6735745a6a9d361a5a8cdd276`
(`regmind-staging:985`):

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
during either preflight.

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

## Verdict

`COMPATIBLE — NO STAGING DATA MUTATION PERFORMED`
