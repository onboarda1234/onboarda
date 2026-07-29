# Validation report

Status: **PASS — CODE AND DRY-RUN VALIDATION**

No staging mutation was performed.

Final base: `origin/main`
`a21d82f3dde90ee61ccb0b08e98cf5ff626d2d60`.

After the base advanced through PR #901, its three-file Screening-only diff
was inspected. It does not touch Monitoring Alert data, status vocabulary,
schema, linkage, audit writing, feature flags, or this operator. The new
Screening tests plus backfill and feature-governance checks passed
`99 passed, 4 PostgreSQL-only skips`; the protected regression was then rerun
on the updated base and passed `1572/1572`.

## Focused reconciliation

- Disposable TLS PostgreSQL, backfill and PR #900 audit suites:
  `60 passed`.
- Final operator/feature-governance compatibility delta:
  all `27` backfill tests and all `31` feature-governance tests passed.
- Final automated-review fixes: `59 passed`, `4` declared PostgreSQL-only
  skips; independent delta review READY.
- Exact staging dry run: 19 scanned, 1 eligible, 14 manual review,
  4 already canonical, 0 blockers.
- Plan fingerprint:
  `c3c25cf9e417a648ebf35079ef2b98849ef455cdf1d53820ebc2a539245304fd`.

PostgreSQL coverage includes:

- serializable transaction and row locking;
- concurrent alert-writer exclusion;
- legacy raw audit-insert exclusion;
- canonical audit append in the same transaction;
- update/audit failure rollback;
- database-enforced read-only planning;
- apply and rollback idempotency;
- refusal after later audit activity;
- truthful evidence when post-commit verification fails;
- malformed audit-hash redaction and fail-closed refusal.

## Repository regression

The non-PDF repository suite was run in stable shards so local HTTP fixtures
were not starved by concurrent PostgreSQL-heavy modules:

- first shard: `3549 passed`, `32 declared PostgreSQL-only skips`;
- PostgreSQL Monitoring routing contract: `34 passed`;
- second PostgreSQL-enabled shard: `4647 passed`, `2 declared skips`,
  `4 expected xfails`.

Combined: `8230 passed`, `34 declared skips`, `4 expected xfails`.
PostgreSQL-specific behavior relevant to this PR was executed separately and
passed; it was not accepted solely as a skip.

Additional results:

- protected-module regression: `1572 passed`;
- PDF lane: `8 passed`;
- schema migration policy: passed;
- Python compilation: passed;
- fatal flake8 (`E9,F63,F7,F82`): passed;
- JSON validation: passed;
- Markdown/patch whitespace: passed;
- sensitive-material scan: passed;
- runtime-consumer scan: passed.

## Scope result

- no protected runtime module changed;
- no schema or migration changed;
- no API, startup, scheduler, worker, or deployment hook invokes the backfill;
- all four Monitoring feature flags remain OFF;
- no Monitoring Alert data was modified.
