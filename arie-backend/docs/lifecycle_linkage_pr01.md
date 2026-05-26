# PR-01 — Lifecycle Linkage Foundation

This document records the design decisions for the lifecycle linkage
spine introduced in PR-01 and what is intentionally deferred to later
PRs. It is the reference the EDD / Monitoring / Periodic Review
operating-model work will build on.

## Scope

PR-01 is provider-agnostic infrastructure. It introduces the minimum
schema and helper surface so that EDD cases, periodic reviews, and
monitoring alerts can explicitly reference each other and record
lifecycle provenance without free-text-only traceability. It does
**not** activate ComplyAdvantage, does **not** enable the screening
abstraction, does **not** promote `screening_reports_normalized` to
authoritative, and does **not** hard-code any Sumsub assumption into
lifecycle architecture.

## What was added

### Schema (additive-only, nullable columns, no FKs)

`edd_cases` — `origin_context`, `linked_monitoring_alert_id`,
`linked_periodic_review_id`, `assigned_at`, `escalated_at`,
`closed_at`, `sla_due_at`, `priority`.

`periodic_reviews` — `trigger_source`, `linked_monitoring_alert_id`,
`linked_edd_case_id`, `review_reason`, `assigned_at`, `closed_at`,
`sla_due_at`, `priority`. Existing `trigger_type` and
`trigger_reason` columns are **not** renamed; `trigger_source` is a
new, disjoint field that captures lifecycle origin, not the nature of
the change.

`monitoring_alerts` — `linked_periodic_review_id`,
`linked_edd_case_id`, `triaged_at`, `assigned_at`, `resolved_at`.

Soft-reference lookup indexes are created for every new
`linked_*_id` and for `origin_context` / `trigger_source`.

Delivered via
`arie-backend/migrations/scripts/migration_008_lifecycle_linkage.sql`.
No modification of `db.py` or any other protected file.

### Enum enforcement scope (PR-01)

Enum vocabularies (`origin_context`, `trigger_source`, `priority`)
are enforced **at the Python layer only** in PR-01 by
`lifecycle_linkage.py` (`InvalidEnumValue`). Neither SQLite nor
PostgreSQL enforces these vocabularies at the DB level in PR-01 — the
migration deliberately does not add a `CHECK` constraint, because
SQLite rejects adding `CHECK` via `ALTER TABLE ADD COLUMN` without a
table rebuild, and because the vocabulary is still expected to evolve
between PR-01 and the first consumer PR. A later migration may add a
PostgreSQL `CHECK` constraint once the vocabulary stabilises. Until
then, any caller that bypasses `lifecycle_linkage.py` and writes
these columns directly can write arbitrary strings; this is an
accepted and documented limitation of PR-01, not a bug.

### Helper module

`arie-backend/lifecycle_linkage.py` is the single entry point for
cross-object lifecycle linkage and lifecycle-timestamp writes.
Functions: `set_edd_origin`, `mark_edd_assigned`,
`mark_edd_escalated`, `mark_edd_closed`,
`set_periodic_review_trigger`, `mark_review_assigned`,
`mark_review_closed`, `mark_alert_triaged`, `mark_alert_assigned`,
`mark_alert_resolved`, `link_alert_to_edd`,
`unlink_alert_from_edd`, `link_alert_to_review`,
`unlink_alert_from_review`.

All helpers validate enum membership, validate the existence of
referenced rows, reject obvious contradictions
(`origin_context="monitoring_alert"` without a linked alert id,
writes against terminal EDD stages, links to closed periodic
reviews), and emit a structured audit event via an injected
`audit_writer` callable whose signature mirrors
`BaseHandler.log_audit` exactly. `base_handler.py` is **not**
modified.

### Audit-writer requirement (PR-01)

Every **mutating** helper in `lifecycle_linkage.py` requires a
non-`None` `audit_writer`. Omitting it raises
`MissingAuditWriter` **before any DB mutation occurs**, so there is
no code path by which a lifecycle state change can be persisted
without a canonical audit path being available. This guarantee is
enforced by a single guard (`_require_audit_writer`) at the top of
each mutating helper and is covered by explicit tests
(`TestAuditWriterRequired`).

The canonical audit persistence path is the existing
`audit_log` table written by `BaseHandler.log_audit`; PR-01 does
not modify that contract. The canonical-persistence guarantee is
covered by tests that use a writer with the exact
`BaseHandler.log_audit` shape and then assert the presence of the
resulting row in the `audit_log` table
(`TestCanonicalAuditPersistence`).

### Re-link semantics and no-op unlinks

`link_alert_to_edd` and `link_alert_to_review` are safe to call when
the alert is already linked to a different target of the same kind.
In that case the old target's reverse pointer is cleared in the same
transactional unit as the new-link write, and a displacement audit
event (`lifecycle.link.alert_to_edd.removed` /
`lifecycle.link.alert_to_review.removed`, with
`displaced_by_relink_to` in the payload) is emitted before the new
`.created` event. This prevents dangling reverse pointers on the
previously linked EDD / review.

`unlink_alert_from_edd` and `unlink_alert_from_review` are no-ops
when the alert is not currently linked. In that case they return
without emitting any audit event, so that a `.removed` event only
appears in the audit trail when a link was actually broken.

### Audit event vocabulary

Actions (written through the existing `log_audit` contract):
`lifecycle.edd.origin_set`, `lifecycle.edd.assigned`,
`lifecycle.edd.escalated`, `lifecycle.edd.closed`,
`lifecycle.review.trigger_set`, `lifecycle.review.assigned`,
`lifecycle.review.closed`, `lifecycle.alert.triaged`,
`lifecycle.alert.assigned`, `lifecycle.alert.resolved`,
`lifecycle.link.alert_to_edd.created`,
`lifecycle.link.alert_to_edd.removed`,
`lifecycle.link.alert_to_review.created`,
`lifecycle.link.alert_to_review.removed`.

Targets: `edd_case:<id>`, `periodic_review:<id>`,
`monitoring_alert:<id>`.

Structured detail payloads are JSON strings carried through the
existing `detail` column; `before_state` / `after_state` are
populated for every mutation.

## Memo-pointer decision

**Deferred.** PR-01 does **not** add an `active_memo_id` pointer to
any lifecycle row. Rationale: inspection of the repo confirms that
`compliance_memos` is versioned per `(application_id, version)` with
a per-row `review_status` lifecycle
(`draft / reviewed / approved / rejected`). The repository has **no**
convention for "the active memo" versus historical memos — there is
no `is_active` flag, no `active_memo_version` pointer, and no single
code path that promotes a memo version to "current". Attaching an
EDD or periodic review to a specific `compliance_memos.id` in PR-01
would therefore either pin to a potentially-stale version or
manufacture a new semantics for "active memo" that every later
consumer would need to understand. The correct place to resolve
this is a later PR that explicitly decides the active-memo
convention (including what happens when a new memo version is
generated while an EDD is open). PR-01 leaves all memo surface area
untouched so that decision can be made cleanly.

## Non-goals (intentionally deferred)

The following are **not** in PR-01 and are explicitly deferred:

* UI / backoffice queue redesign.
* Routing that auto-creates a periodic review from a high-severity
  monitoring alert.
* Route-to-EDD workflow automation from monitoring alerts or reviews.
* Memo rewrite or new memo surface; `memo_handler.py` is unchanged.
* Information-request engine.
* Approval-gate changes.
* Change-management integration; `change_management.py` is unchanged.
* Hard foreign-key promotion of the soft references.
* Any broadening of `BaseHandler.log_audit` — PR-01 uses it as-is.
* ComplyAdvantage activation; `ENABLE_SCREENING_ABSTRACTION` stays
  false.
* Any change to `screening_reports_normalized`.
* Backoffice server.py routes / new handlers.
* DB-level enum enforcement for `origin_context` / `trigger_source`
  / `priority` (see "Enum enforcement scope" above).

## EX-control impact

None. No file in `PROTECTED_FILES` (basename match, per
`arie-backend/protected_controls.py`) is modified. The EX-01..EX-13
register continues to pass `verify_control_coverage()` and
`check_protected_files_in_diff` against the PR diff returns an empty
violation list. No existing column is altered, no existing row is
mutated, and no existing index is dropped, so
EX-02 / EX-07 / EX-09 / EX-11 / EX-12 regressions are impossible by
construction.

## Rollback

Because the migration is strictly additive with nullable columns and
no FK constraints:

* Rolling back the migration row in `schema_version` and leaving the
  columns in place is safe — the new columns are simply unused.
* If a hard column drop is ever required, a follow-up migration can
  drop the columns on PostgreSQL; on SQLite a column drop requires a
  table rebuild, which is avoided by design in PR-01.

## How later PRs should build on this

PR-02+ should:

1. Add routing that calls `set_edd_origin` /
   `set_periodic_review_trigger` from the appropriate handlers when
   lifecycle objects are created from other lifecycle objects
   (alert → review, review → EDD, etc.), always passing an
   `audit_writer` bound to the canonical `BaseHandler.log_audit`
   path.
2. Decide the active-memo convention and introduce the memo-pointer
   column on `edd_cases` / `periodic_reviews` if still appropriate.
3. Promote soft references to hard FKs once ordering is confirmed
   stable across both dialects.
4. Add a PostgreSQL `CHECK` constraint for `origin_context` /
   `trigger_source` / `priority` once the vocabulary is stable.
5. Wire the audit events into the backoffice trace view.

At every step, the invariants in this PR hold: `lifecycle_linkage.py`
remains the single write path, mutating helpers cannot run without an
`audit_writer`, and no protected file is modified.
