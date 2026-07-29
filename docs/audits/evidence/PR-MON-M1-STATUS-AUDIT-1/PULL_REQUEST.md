## Objective

Add a reproducible, read-only runtime audit of every Monitoring Alert currently
stored in staging and publish its sanitized inventory, linkage analysis,
duplicate analysis, status mapping, orphan report, and migration-readiness
recommendations.

## Non-goals

This PR does not update, delete, backfill, route, resolve, or otherwise mutate an
alert. It adds no state machine, schema, migration, feature activation, runtime
workflow, scheduler, endpoint, UI, or protected-module behaviour.

## Methodology and safety

- Direct PostgreSQL collection without application startup/schema helpers.
- `READ ONLY`, `REPEATABLE READ` transaction.
- Runtime confirmation that `transaction_read_only=on`.
- Fixed `SELECT`/`WITH` statements plus `SHOW`; lexical write-statement guard.
- Source fingerprint before and after collection.
- Explicit rollback and close; no commit path.
- Sanitized evidence: no raw client/application/staff names, free-form source
  text, officer notes, credentials, audit details, or provider payloads.

## Runtime statistics

- Total alerts: **19**
- Exact `open`: **13**
- Exact `resolved`: **1**
- All terminal statuses: **4**
- Legacy status: **2**
- Invalid / unknown status: **0 / 0**
- Exact duplicate alerts: **0**
- Repeated source-reference groups: **1**
- Similar unresolved distinct-source groups: **4**
- Orphaned alerts: **4**
- Missing required linkage: **4**
- Broken/impossible linkage: **0**
- Migration ready: **4**
- Future migration candidate: **1**
- Manual review required: **14**

## Material findings

- Alerts `1`, `2`, `54`, and `55` are orphaned.
- Alerts `1` and `583` use legacy stored status.
- Four terminal rows lack `resolved_at`.
- Six non-fixture applications have test-like names and require provenance
  confirmation before migration.
- Alert `584` is `open` while explicitly linked to EDD; it is mapped as a future
  `routed_to_edd` candidate but blocked for manual review due to current
  owner/status drift.
- No alert has duplicate explicit workflow ownership.
- Monitoring is not assigned as the canonical workflow owner of any row.

## Reports

- Monitoring Audit Report: `PR-MON-M1-STATUS-AUDIT-1.md`
- Migration Readiness Report: `MIGRATION_READINESS_REPORT.md`
- Status Mapping Report: `STATUS_MAPPING_REPORT.md`
- Duplicate Report: `DUPLICATE_REPORT.md`
- Orphan Report: `ORPHAN_REPORT.md`
- Linkage Report: `LINKAGE_REPORT.md`
- Sanitized machine-readable inventory: `runtime_inventory.json`

## Validation

- Focused audit contracts: **26 passed**
- Protected-module regression: **1,572 passed**
- Full repository suite: **8,252 passed, 2 expected skips, 4 expected xfails**
- PDF lane: **8 passed**
- Python compilation and CI fatal flake8: **PASS**
- Independent review: **READY — no actionable P0/P1/P2**
- All four Monitoring feature flags remained **OFF**

## Behaviour confirmation

No observable or runtime behaviour change is intended. A quick read-only staging
browser check remains required after deployment.

**No Monitoring Alert data was modified.**
