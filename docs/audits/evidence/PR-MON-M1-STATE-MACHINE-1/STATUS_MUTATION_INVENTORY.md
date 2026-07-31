# PR-MON-M1-STATE-MACHINE-1 — Phase 1 status-mutation inventory

Status: complete before implementation
Original base: `9fa083c1ac185ea65bfa8515dff315eb254701a5`
Revalidated code base: `85c70431a2d2a2f4bd6dd3078257d5f22d92bad4`

The point-in-time staging data/schema preflight remains identified by its
observed deployed SHA, `b5fb23276bacfc8aa543f5e31963e253c3ff8ab8`, in
`STAGING_SCHEMA_PREFLIGHT.md`; it is not silently relabelled after main moved.
Inventory date: 2026-07-30; read-only revalidation: 2026-07-31

## Reconciled runtime baseline

A database-enforced read-only plan was run inside the staging backend task. Its
first observations scanned 19 Monitoring Alerts and changed nothing.

| Stored status | Count |
|---|---:|
| `open` | 13 |
| `dismissed` | 3 |
| `resolved` | 1 |
| `escalated` | 1 |
| `routed_to_edd` | 1 |

Alert `583` is already canonical at `routed_to_edd` with the PR #902
hash-chained audit entry. The four governed Monitoring flags evaluated OFF.

A final pre-merge read-only revalidation observed 22 alerts after existing
ComplyAdvantage ingestion created three non-fixture canonical `open` rows (two
historical subscription-seed backfill rows and one live-webhook row). The
current status counts are `open=16`, `dismissed=3`, `resolved=1`,
`escalated=1`, and `routed_to_edd=1`. CloudWatch creation-window counts and the
production call paths attribute the change to pre-existing screening behavior,
not a governed feature flag or this PR. The review ledger remains empty and
the migration compatibility result is unchanged.

The live PostgreSQL schema at the revalidated base has:

- `monitoring_alerts.status` nullable, defaulting to `open`;
- no status-value `CHECK`;
- no status/`resolved_at` consistency constraint.

The constraint design therefore targets the reconciled post-PR #902 data. It
does not rely on the merged pre-approval PR #902 report as current runtime
proof.

## Mutation paths

| Mutation path | Current source → target | Actor / evidence | Risk and required treatment |
|---|---|---|---|
| Manual alert creation (`server.py`) | creation → `open` | admin/SCO/CO; source fields | Initial-state exception only; creation is not a transition. Keep fixed `open` and audit creation. |
| Document-health detection (`document_health_monitor.py`) | creation → `open` | existing document issue | Initial-state exception only. |
| ComplyAdvantage webhook upsert (`webhook_storage.py`) | any existing status → `open` on conflict | webhook provider/case | Critical silent reopening. Conflict updates must preserve status. |
| ComplyAdvantage historical backfill (`historical_backfill.py`) | any existing status → `open` on conflict | operator/backfill | Critical silent reopening. Conflict updates must preserve status. |
| Start review (`server.py`) | broad state → `in_review` | officer; optional note | Route through the service with exact source, role, and detection evidence. |
| Triage (`monitoring_routing.py`) | `open` → `triaged` | officer | Route through the service. Remove duplicate transition logic. |
| Assign (`server.py`, `monitoring_routing.py`) | broad state → `assigned` | officer/assignee | Assignment may advance only `open`/`triaged`; later reassignment is metadata-only but still uses the same authoritative active-role validation. |
| Generic decision (`server.py`) | broad state → several statuses | officer; outcome-dependent note | Replace the open-ended map with explicit transition rules and typed evidence. |
| Dismiss (`monitoring_routing.py`) | broad state → `dismissed` | officer/senior; reason/control evidence | Route through the service and keep four-eyes evidence atomic. |
| Route to Periodic Review (`monitoring_routing.py`) | broad state → `routed_to_review` | officer; created/reused review | Lock first; require an exact same-application review link; route is nonterminal. |
| Route to EDD (`monitoring_routing.py`) | broad state → `routed_to_edd` | officer; created/reused EDD case | Lock the application and alert first. Reuse only one already-consistent active case linked to this alert; fail closed for another owner, ambiguous/malformed provenance, or multiple active cases. Route is nonterminal. |
| Overdue escalation (`server.py`) | active → `escalated` | officer; SLA and reason | Route through the service; escalation remains active. |
| Four-eyes clear (`monitoring_dismissal_control.py`, `server.py`) | active → terminal outcome | different senior approver or recorded senior override | Request/approval, transition, and audits commit once. The ledger is bound to exact source status, terminal intent, rationale, and typed evidence. |
| Document refresh acceptance/waiver (`monitoring_document_refresh.py`) | active → `resolved`/`waived` | KYC & Documents; request/document/reason | Route through the service with exact linked evidence. Controlled clears first create an approved or `senior_cleared` ledger entry bound to the exact `accept_updated_document`, `mark_already_updated`, or `waive_with_reason` intent. |
| Enhanced-requirement review sync (`monitoring_document_refresh.py`) | active → `resolved`/`waived` | KYC & Documents | Scope the requirement to the route application before control preflight, then run the same document-control contract before changing requirement state. Cross-application or stale linkage fails without a review row; a pending review leaves requirement, document, and alert unchanged. |
| Document-health issue disappearance (`document_health_monitor.py`) | active → `resolved` | scheduler inference | Automatic closure is not activated in this PR. Preserve status and report a resolution candidate. |
| PR #902 backfill/rollback (`monitoring_status_backfill.py`) | exact manifest row only | approved operator and fingerprint | Narrow, versioned, audited direct-write exception retained for its rollback window. |
| Fixture/demo seeders | fixture snapshot writes | guarded fixture operator | Narrow non-regulated exception; never a runtime mutation path. |

## Cross-cutting findings

1. Ordinary runtime writers do not lock the alert row or use an exact
   source-status predicate. Concurrent decisions can both report success.
2. Four-eyes request, approval, transition, and audit writes currently cross
   multiple commits.
3. Document-health audit failures are swallowed even when the alert mutation
   later commits.
4. `routed_to_edd` and `routed_to_review` are incorrectly classified as
   resolved/terminal in multiple modules.
5. `escalated` is incorrectly folded into `routed_to_edd`.
6. `resolved_at` is sometimes treated as an independent state machine. The
   stored canonical status must be authoritative; existing timestamp
   inconsistencies are evidence for a separate migration.
7. Assignment is both ownership metadata and a status. V1 preserves
   `assigned` only as the pre-review phase; later reassignments do not regress
   lifecycle state.
8. The document-request states (`requested`, `uploaded`, `under_review`, and
   related display aliases) belong to `application_enhanced_requirements`, not
   `monitoring_alerts`.
9. Screening Review, KYC & Documents, EDD, Periodic Review, and Change
   Management own their business decisions. Monitoring owns the signal,
   triage/assignment visibility, escalation visibility, and linkage.
10. PR #900 established explicit ownership through both alert-side and reverse
    EDD/Periodic Review links, linked enhanced requirements, and the exact
    Change Management source-reference bridge. Alert-row-only inference is
    insufficient; conflicting or broken explicit links must fail closed.

## Direct-write exceptions

The static guard may allow only:

1. insertion at the fixed initial state `open`;
2. the exact PR #902 versioned migration/rollback tool;
3. database schema/constraint installation;
4. strictly guarded fixture/demo snapshot seeders.

All runtime status changes must use
`monitoring_alert_state_machine.transition_alert_status`.

The final guard masks SQL literals and comments and tracks parentheses before
locating the outer `SET`/`WHERE` boundary. It preserves possible SQL and
executor bindings across branches, loops, `match`, `try`, and
exception-suppressing `with` blocks; inspects every statement in scripts/CTEs;
recognizes PostgreSQL inheritance-table forms and SQLite conflict/`REPLACE`
writes; and fails closed on unresolved executor SQL. Its allowlists remain
exact, function-scoped, and counted.
