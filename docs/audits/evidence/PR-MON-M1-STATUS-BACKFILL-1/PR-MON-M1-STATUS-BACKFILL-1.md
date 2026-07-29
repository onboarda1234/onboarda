# PR-MON-M1-STATUS-BACKFILL-1 — Controlled status reconciliation

## Objective

Provide a deterministic, dry-run-first operator path for the one exact
Monitoring Alert status mapping proven by PR #900. No automatic execution is
wired into application startup, deployment, an API route, a scheduler, or a
worker.

## Approved mapping

Manifest `1.0.0` enumerates one automatic row:

`alert 583: in_review → routed_to_edd`

The row is eligible only with its exact application, application reference,
alert type, severity, source reference, provider/case identity, officer action,
effective update timestamp, and reverse EDD linkage to case 309.

Four PR #900 migration-ready rows already store canonical `open` and receive no
write. Fourteen ambiguous rows remain manual review.

## Transaction and audit design

The apply path:

1. verifies the approved canonical manifest SHA-256
   `4764b491fff8afe03bf1afa26f3c6a874b34645b0067b0ab72bb2913e243ab16`;
2. requires PostgreSQL and the PR #900 full-row MD5;
3. clears only the dedicated operator connection's checkout/pre-ping
   transaction;
4. starts a PostgreSQL `SERIALIZABLE READ WRITE` transaction;
5. takes short `SHARE ROW EXCLUSIVE` locks on `monitoring_alerts` and
   `audit_log`, plus explicit alert-row locks;
6. rebuilds and fingerprint-checks the exact plan;
7. updates only the status column with an exact `id + source status` predicate;
8. appends one canonical hash-chained `audit_log` entry on the same connection;
9. reconciles the normalized full-row fingerprint, counts, flags, and audit
   count before commit;
10. commits, then verifies the committed result from a fresh database-enforced
    read-only snapshot;
11. rolls back everything if any pre-commit mutation or audit step fails.

The audit records before/after status, manifest version, reason, actor,
timestamp, PR identifier, and approved dry-run fingerprint.

If the commit succeeds but the fresh-snapshot verification cannot complete,
the operator result explicitly reports `committed: true` and
`failed_closed: false`, preserving the exact changed-row and audit-entry
evidence. A lost connection during cleanup cannot mask that durable result.
Stored audit fingerprints and entry hashes must be exact lowercase SHA-256
values; malformed values are redacted and block reconciliation.

## Non-goals

- no alert deletion or duplicate merge;
- no notes, evidence, source, linkage, assignment, severity, type, or timestamp
  rewrite;
- no status constraint or state machine;
- no Applications, Screening, RSMP, EDD, Periodic Review, Change Management,
  KYC & Documents, or Portal behavior change;
- no feature activation;
- no automatic staging apply.

## Current runtime evidence

The database-enforced staging dry run is safe with fingerprint
`c3c25cf9e417a648ebf35079ef2b98849ef455cdf1d53820ebc2a539245304fd`.
It scanned 19 rows, found one exact eligible row, left 18 unchanged, found zero
precondition drift, and observed every governed feature flag OFF.

No Monitoring Alert data has been modified.
