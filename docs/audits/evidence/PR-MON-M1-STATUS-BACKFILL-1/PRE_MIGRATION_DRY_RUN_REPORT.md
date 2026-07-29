# Pre-migration dry-run report

Audit ID: `PR-MON-M1-STATUS-BACKFILL-1`

Manifest: `1.0.0`

Captured: `2026-07-29T14:04:12Z`

Environment: `staging`

Deployed SHA at planning time: `ff8f6e6721ccb7b4d6e9d1797740e996f07a2520`

## Database-enforced safety

The planner ran inside PostgreSQL `REPEATABLE READ READ ONLY`.
`SHOW transaction_read_only` returned `on`. The transaction was rolled back and
the planner has no commit path.

## Deterministic result

Plan fingerprint:

`c3c25cf9e417a648ebf35079ef2b98849ef455cdf1d53820ebc2a539245304fd`

| Check | Expected | Observed |
|---|---:|---:|
| Total alerts | 19 | 19 |
| Eligible mutations | 1 | 1 |
| Manual-review rows | 14 | 14 |
| Already-canonical rows | 4 | 4 |
| Exact duplicate alerts | 0 | 0 |
| Orphaned rows | 4 | 4 |
| Changed preconditions | 0 | 0 |

The normalized `monitoring_alerts` full-row MD5 remained
`550cf672ab40b98d3ec177f12248ecb1`, exactly matching PR #900.

## Exact proposed mutation

| Alert | Application | Current | Proposed | Evidence |
|---:|---|---|---|---|
| 583 | `pcdv100000000024` / `RM-PILOT-024` | `in_review` | `routed_to_edd` | Exact PR #900 reverse EDD link to case 309, application match, stage `analysis`, existing `create_edd` officer action |

No other row is eligible. Eighteen rows would remain unchanged.

## Status counts

| Status | Before | Planned after |
|---|---:|---:|
| dismissed | 3 | 3 |
| escalated | 1 | 1 |
| in_review | 1 | 0 |
| open | 13 | 13 |
| resolved | 1 | 1 |
| routed_to_edd | 0 | 1 |

## Feature governance

All four governed Monitoring flags evaluated `OFF`:

- `ENABLE_DOCUMENT_RENEWAL_AUTOMATION`
- `ENABLE_AGENT1_REFRESH_VERIFICATION`
- `ENABLE_MONITORING_SCREENING_CHANGE`
- `ENABLE_MONITORING_AUTO_RESOLUTION`

No mutation was performed by this dry run.
