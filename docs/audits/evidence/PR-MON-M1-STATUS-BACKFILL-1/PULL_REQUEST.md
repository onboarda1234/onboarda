# Draft PR body

## Objective

Add a controlled, operator-only status reconciliation for the exact PR #900
Monitoring Alert mapping. The default command is a database-enforced read-only
dry run. Merge and deployment do not apply the backfill.

## Exact mapping and scope

- Manifest: `1.0.0`
- Approved manifest SHA-256:
  `4764b491fff8afe03bf1afa26f3c6a874b34645b0067b0ab72bb2913e243ab16`
- Eligible: alert `583`, `in_review → routed_to_edd`
- Eligible rows: `1`
- Already canonical/no write: `4`
- Manual review/no write: `14`
- Exact duplicates merged: `0`
- Alerts deleted: `0`

The mapping is bound to the exact audited row identity and reverse EDD case 309
link. No fuzzy, substring, case-folded, or inferred mapping exists.

## Dry run

- Environment: staging
- PostgreSQL: `REPEATABLE READ READ ONLY`
- Alert count: `19`
- Eligible: `1`
- Changed preconditions: `0`
- Full normalized row MD5:
  `550cf672ab40b98d3ec177f12248ecb1`
- Plan fingerprint:
  `c3c25cf9e417a648ebf35079ef2b98849ef455cdf1d53820ebc2a539245304fd`
- All four Monitoring feature flags: `OFF`

## Atomicity and audit

Apply requires PostgreSQL and the approved manifest digest. It starts one
serializable transaction, locks `monitoring_alerts`, `audit_log`, and the exact
alert rows, rechecks the approved plan, updates only `monitoring_alerts.status`,
appends one canonical hash-chained audit entry per changed alert, reconciles
inside the transaction, and commits only if every pre-commit check succeeds.
Any update or audit failure rolls everything back. A fresh read-only snapshot
then verifies the committed result. If that post-commit check fails, the CLI
truthfully reports that the commit is durable and preserves changed-row/audit
evidence; it never claims a rollback succeeded. A second apply changes zero
rows and creates no audit entry.

## Rollback

Rollback uses the exact migration audit/before-state and refuses after any later
alert audit or row drift. It never resets a broad status group and preserves the
original migration audit.

## Tests

Focused tests cover exact mapping, ambiguous/unknown preservation, count and
timestamp drift, missing audit infrastructure, update/audit failure atomicity,
read-only repeatability, idempotency, narrow rollback, later-mutation refusal,
flags OFF, audit-value redaction, truthful post-commit failure reporting, and
absence of a runtime consumer. The same atomic apply, canonical audit chain,
rollback-on-audit-failure, read-only enforcement, and writer-lock contracts are
exercised on PostgreSQL.

Validation results:

- focused backfill + PR #900 audit: `60 passed` on disposable PostgreSQL;
- protected-module regression: `1572 passed`;
- repository suite, sharded to isolate PostgreSQL-only contracts:
  `8230 passed`, `34 declared skips`, `4 expected xfails`;
- PostgreSQL Monitoring routing contract: `34 passed`;
- PDF lane: `8 passed`;
- schema migration policy, compilation, fatal lint, JSON, whitespace, and
  sensitive-material checks: passed.

## Independent review

READY. The reviewer found no unresolved P0/P1/P2 or other actionable finding
after verifying exact PR #900 evidence linkage, manifest pinning, transaction
and writer locks, audit atomicity, idempotency, rollback safety, PostgreSQL
behavior, malformed-audit redaction, truthful durable-commit reporting,
protected scope, and absence of workflow activation.

## Confirmations

- No alert was deleted.
- No ambiguous row was changed.
- No Monitoring Alert data was modified by development or dry-run validation.
- All four Monitoring feature flags remain OFF.
- Protected modules and workflows are unchanged.
- No backfill runs at startup, merge, or deployment.
