# AWS Staging Reset and Risk-Config Alignment — 2026-07-17

Status: **reset complete; canonical seed blocked pending dry-run fix**

This record covers AWS staging only. It does not establish pilot or production
readiness, does not complete RSMP Tier 0C, and does not authorize canonical
seeding.

## Authorized baseline and rollback

- GitHub Actions run `29572158632` completed successfully for full backend,
  PostgreSQL, PDF, Docker and deployment jobs.
- `origin/main`, backend, worker, image tags, `GIT_SHA`, `IMAGE_TAG`, and the
  authenticated version endpoint were pinned to
  `a10d2c3e3894b433a0435534d27bc20f03c00863`.
- Backend task definition: `regmind-staging:869` (2/2 running).
- Worker task definition: `regmind-verification-worker:317` (6/6 running).
- Database: AWS account `782913119880`, region `af-south-1`, RDS instance
  `regmind-staging-db`, database `regmind`.
- Migration `048` was applied and `screening_report_archive` existed.
- Authoritative rollback snapshot:
  `regmind-staging-reset-prepurge-post786-20260717104514`; status `available`,
  encrypted with KMS, created `2026-07-17T10:45:20.187Z`.
- Founder/authorized reviewer `admin001` approved the full live-schema scope;
  the superseding hash-chained approval audit entry has hash
  `0406ca74cdf83fba06f3bac555f8ea799dac52ed999276fb7a4a11e4e2e2b4f6`.

## Purge execution

- Founder-confirmed population: 944 synthetic applications, zero real clients,
  zero pilot records, and zero `RM-PILOT-*` records.
- Approved live-scope SHA-256:
  `977b817703b2857a665b3824a8d869dc46aea64af9a514c2dd90a1522b8ec018`.
- Application-manifest SHA-256:
  `81c3f4da3a243aa2512d8b470ae581b85008d60b481a130a21c2d5ff4e2e308a`.
- Deleted: 944 applications, 15,502 child rows, and 239 eligible synthetic
  clients. Thirty-two unrelated clients were retained.
- `screening_report_archive`: 10 application-linked rows deleted.
- S3: 1,259 scoped keys checked; 1,178 live objects (195,675,918 bytes)
  deleted and 81 keys were already absent. Post-check found zero live scoped
  objects. Key-manifest SHA-256:
  `1fd671507568104b8a71f1daf83067b075ef38d5bcf399d0102774bb8b727c5e`.
- Local storage: 2,284 unique application-linked paths were inventoried; none
  existed on either running backend task, so zero local files required deletion.
- Purge evidence: `data_purge_log` batch `manual-6ebb460e84f2`, 17,863 total
  deleted database rows/objects, including migration-048 archive scope.
- Post-check: applications 0, `RM-PILOT-*` 0, application-linked residue 0,
  scoped S3 residue 0, retained clients 32.
- Protected users, settings, risk configuration, migrations, purge evidence,
  `audit_log`, and `supervisor_audit_log` were retained. Both chain verifiers
  pass. The known legacy `audit_log` chain-coverage limitation remains and was
  not changed by the reset.

Staging execution of SRP-2 was superseded by this reset. The feature and PR #786
functionality remain available for future governed use; no SRP-2 execution was
run in this reset window.

## Risk configuration alignment

The validated admin API was used; no direct SQL configuration update occurred.
The full before/after diff contained exactly:

| Setting | Before | After |
|---|---:|---:|
| Manufacturing | 1 | 2 |
| D3 Service Type | 40 | 40 |
| D3 Monthly Volume | 30 | 35 |
| D3 Transaction Complexity | 30 | 25 |

- Dimension weights remain `30/25/20/15/10`.
- Prior version: `risk_config:2026-07-13 07:15:16.941658`.
- New version: `risk_config:2026-07-17 11:16:03.481284`.
- Prior canonical SHA-256:
  `9ffcfe3e4dd5fcd3a2df7aa11506c39631b683eb934019384d59f7fba339d91e`.
- New canonical SHA-256:
  `97347127b940f0889c105c84323e7f465370fa1df1b38c9ad3a4cb3bd197b43c`.
- Audit event: `audit_log.id=168730`, actor `admin001`, action `Config`,
  target `Risk Model`.
- API recomputation result: attempted 0, recomputed 0, changed 0, failed 0.
- RSMP activation remains absent from ECS environment and evaluates false.

## Canonical dry-run blocker

The reviewed manifest remains
`fee7436a6bf6ead1cc9a8090ceaa3de7071a9b745e43f2c69a445cf74efdf9c9`
with 41 unique references, `RM-PILOT-001` through `RM-PILOT-041`.

The runtime-alignment preflight evaluated all 41 scenarios successfully under a
flag enabled only inside the isolated offline ECS Exec process. No ECS task
definition or service environment changed. The persistence dry-run then failed
at `RM-PILOT-037` because `decision_records.override_flag` is an integer in live
PostgreSQL while `fixtures/pilot_canonical_seeder.py` binds Boolean `false`.
PostgreSQL rejected the value with `DatatypeMismatch`; the transaction rolled
back. Post-checks confirm zero applications, zero `RM-PILOT-*` records and zero
application-scoped residue.

The seeder must be corrected and the complete 41-scenario dry-run rerun before
any canonical apply authorization. Do not work around the type mismatch
operationally and do not seed the current manifest yet.

## Safety evidence

From deployment completion through reset validation, CloudWatch contained zero
ERROR-level events, Exceptions, Tracebacks, unexpected 5xx responses,
startup/routing failures, provider calls, email/notification/webhook sends, or
SRP-2 events. The one recomputation event is the expected configuration update:
`apps=0, changed=0, failed(quarantined)=0`.

The final service state is backend 2/2, worker 6/6, both rollouts complete, two
healthy ALB targets, and HTTP 200 for liveness, health and authenticated
readiness (`ready=true`). The canonical dataset remains unseeded, Tier 0C was
not run, RSMP is OFF, and production was not accessed.

## Issues and limitations

- The first purge execution attempt failed closed before any deletion because
  the operational helper used the database wrapper rather than its cursor for
  the affected-row count. The database transaction rolled back, no S3 object
  had been touched, the helper was corrected, and the identical approved dry
  run was repeated before the successful execution.
- Ten historical SRP-2 archive/audit entries already existed before this reset
  window. No SRP-2 process was running or scheduled and no SRP-2 execution took
  place during the reset; the scoped historical rows were removed with their
  synthetic applications.
- The protected `audit_log` verifier passes for all chained entries, but the
  repository reports an existing incomplete chain-coverage population of
  legacy/unchained rows. The fully chained `supervisor_audit_log` verifies.
- The live PostgreSQL canonical dry-run type mismatch described above blocks
  canonical seeding. Static manifest validation and all 41 runtime score
  comparisons passed, but the expected per-scenario persistence result cannot
  be claimed until the seeder is fixed and the full dry run passes.
- Application-dependent UI export validation is intentionally deferred because
  the database is empty and canonical seeding is not authorized.
