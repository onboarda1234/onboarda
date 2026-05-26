# PR-04 — EDD Active-Memo Integration

## Summary

PR-04 makes EDD findings feed the *correct* active decision artifact
(onboarding memo or periodic-review memo), without:

* mutating `compliance_memos` history,
* overwriting an onboarding memo with periodic-review-lifecycle material,
* creating a third, disconnected EDD memo universe,
* touching any file in `PROTECTED_FILES`,
* activating any provider integration (no ComplyAdvantage, no
  `ENABLE_SCREENING_ABSTRACTION` flip).

It is the smallest safe additive layer that satisfies the PR-04 brief:
the system can now answer *what is the active memo context for this
EDD case?*, *where do these findings live?*, *is this onboarding or
later review?*, and *what historical onboarding memo and current
periodic-review memo (if any) does this case touch?*.

## Repo-grounded current state (pre-PR-04)

* **`compliance_memos`** is the onboarding memo system of record:
  per-application, per-version, with `validation_status`,
  `supervisor_status`, `approved_by`, `approved_at`, `approval_reason`
  (added by `db.py` migration v2.25). PR-01 and PR-03 deliberately
  did NOT add a memo pointer to lifecycle rows because memo identity
  needed care.
* **`edd_cases`** has a free-text `edd_notes` JSON array and (since
  PR-01) `origin_context`, `linked_monitoring_alert_id`,
  `linked_periodic_review_id`. No structured findings payload existed.
* **`periodic_reviews`** (PR-03) has `outcome` / `outcome_reason` /
  `outcome_recorded_at` as the authoritative outcome columns; the
  legacy `decision` column is read-only legacy state. No memo pointer.
* **No EDD-to-memo-context linkage existed** — PR-01/PR-02/PR-03 left
  this for PR-04.

## Files changed

* `arie-backend/migrations/scripts/migration_010_edd_memo_integration.sql`
  — additive only: two new tables (`edd_findings`, `edd_memo_attachments`)
  with lookup indexes. **No** ALTER TABLE, **no** modification of
  `compliance_memos`, `edd_cases`, or `periodic_reviews`.
* `arie-backend/edd_memo_integration.py` — new module:
  active memo context resolver, structured findings upsert/read,
  attach/detach to memo context, read helpers for memo-side
  consumption. Audit-writer enforced (mirrors PR-01 contract).
* `arie-backend/tests/test_edd_memo_integration.py` — 40 unit tests.
* `arie-backend/docs/edd_memo_integration_pr04.md` — this design note.

**No protected file is touched.** `memo_handler.py`, `pdf_generator.py`,
`db.py`, `validation_engine.py`, `supervisor_engine.py`,
`security_hardening.py`, `auth.py`, `base_handler.py`, etc. are all
unchanged. `server.py` is unchanged in this PR — handler wiring is
intentionally deferred so PR-04 stays minimal and surgical (see
*Deferred items* below).

## Exact implementation

### A. Active memo context resolution

`edd_memo_integration.resolve_active_memo_context(db, edd_case_id)`
deterministically resolves the memo context an EDD belongs to, in
strict order (first match wins):

1. **Explicit `edd_cases.linked_periodic_review_id`** → periodic-review
   context (the strongest signal, set by PR-01 `set_edd_origin` and
   PR-03 `escalate_review_to_edd`).
2. **`origin_context='periodic_review'` without explicit link** →
   raises `MemoContextResolutionError`. *Never* silently guessed.
3. **`origin_context='onboarding'`** → onboarding memo context
   (latest `compliance_memos` row for the application, or `None` if
   no onboarding memo yet).
4. **`origin_context='monitoring_alert'`** → if the linked alert
   itself points at a periodic review, route to that review; else
   route to the onboarding context (documented contract: never create
   a disconnected EDD memo universe).
5. **`change_request` / `manual` / `NULL`** → default to onboarding.

Returns a dict with `kind`, `application_id`, `periodic_review_id`,
`memo_id`, `origin_context`, `resolution_reason`. Read-only.

### B. Structured EDD findings payload

New `edd_findings` table — one row per `edd_case_id` (UNIQUE),
upserted via `set_edd_findings(...)`. Fields:

* `findings_summary` (text)
* `key_concerns` (JSON array of strings)
* `mitigating_evidence` (JSON array of strings)
* `conditions` (JSON array of strings)
* `rationale` (text)
* `supporting_notes` (JSON array of dicts/strings)
* `recommended_outcome` (one of `approve`, `approve_with_conditions`,
  `reject`, `escalate`)

All input is validated; invalid types/enums raise
`FindingsValidationError` BEFORE any DB write. Updates merge in only
the supplied fields (others are preserved). Created/updated emit
distinct audit events (`edd.findings.created`, `edd.findings.updated`)
with full before/after state.

### C. Memo-context linkage

New `edd_memo_attachments` table — soft-references to the memo
context an EDD's findings feed:

| memo_context_kind | memo_id              | periodic_review_id |
|-------------------|----------------------|--------------------|
| `onboarding`      | `compliance_memos.id`| `NULL`             |
| `periodic_review` | `NULL` (today)       | `periodic_reviews.id` |

`attach_edd_findings_to_memo_context(...)` resolves the active context
and inserts an attachment row (idempotent on the same key — reuse, no
duplicate). Refuses to attach when no findings have been recorded
(`AttachmentValidationError`). When the EDD context changes (e.g.
re-linked from onboarding to a periodic review), a NEW attachment is
inserted; the old one is preserved for audit history (no overwrite).

`detach_edd_findings_from_memo_context(...)` is a soft-update: sets
`detached_at` / `detached_by` and emits `edd.memo_context.detached`.
Idempotent no-op when nothing matches (no misleading detached event).

### D. Onboarding vs periodic-review separation (non-negotiable)

* `compliance_memos` is **never** mutated by this module — proven by
  `test_attach_does_not_mutate_compliance_memos` and
  `test_compliance_memos_unmodified` (asserts schema is unchanged).
* Onboarding context attachments live on
  `(memo_context_kind='onboarding', memo_id=...)`.
* Periodic-review context attachments live on
  `(memo_context_kind='periodic_review', periodic_review_id=...)`.
* Read helpers filter by `kind` and the relevant id; no cross-context
  bleed (proven by `test_onboarding_and_review_contexts_are_disjoint`).

### E. Audit behavior

Every mutating helper requires a non-NULL `audit_writer` and raises
`MissingAuditWriter` BEFORE any DB write — same contract as PR-01
`lifecycle_linkage`. Audit events emitted:

| Event                          | When                              |
|--------------------------------|-----------------------------------|
| `edd.findings.created`         | First `set_edd_findings` call     |
| `edd.findings.updated`         | Subsequent `set_edd_findings`     |
| `edd.memo_context.attached`    | New attachment row inserted       |
| `edd.memo_context.detached`    | Active attachment soft-detached   |

All events carry structured `before_state` / `after_state` payloads
mirrored to the canonical `audit_writer` contract.

### F. Additive schema

Two new tables (no FK constraints, in line with PR-01..PR-03):

```sql
CREATE TABLE edd_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edd_case_id INTEGER NOT NULL UNIQUE,
    findings_summary TEXT,
    key_concerns TEXT DEFAULT '[]',
    mitigating_evidence TEXT DEFAULT '[]',
    conditions TEXT DEFAULT '[]',
    rationale TEXT,
    supporting_notes TEXT DEFAULT '[]',
    recommended_outcome TEXT,
    created_by TEXT, created_at TEXT,
    updated_by TEXT, updated_at TEXT
);

CREATE TABLE edd_memo_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edd_case_id INTEGER NOT NULL,
    application_id TEXT NOT NULL,
    memo_context_kind TEXT NOT NULL,
    memo_id INTEGER,
    periodic_review_id INTEGER,
    attached_by TEXT, attached_at TEXT,
    detached_by TEXT, detached_at TEXT
);
```

Plus lookup indexes by `edd_case_id`, `application_id`,
`memo_context_kind`, `memo_id`, `periodic_review_id`.

## Tests added

`arie-backend/tests/test_edd_memo_integration.py` — 40 tests across
six classes:

* **`TestMigration010Schema` (3)** — proves new tables exist and
  `compliance_memos` is unmodified.
* **`TestResolveActiveMemoContext` (10)** — every resolution rule:
  onboarding origin (with and without an onboarding memo present),
  explicit review-link wins over origin, periodic_review origin
  without link raises, broken review reference raises, monitoring
  alert with/without review link, NULL origin defaults, change_request
  defaults, missing EDD raises.
* **`TestEDDFindings` (10)** — minimal create, full create, update
  emits `.updated` event, before/after state in audit, invalid
  recommended outcome rejected, invalid list/text fields rejected,
  missing EDD raises, audit_writer required, get returns None when
  absent.
* **`TestAttachEDDFindingsToMemoContext` (8)** — attach to onboarding,
  attach to periodic review, idempotent on same context (one audit
  event), context-change creates NEW attachment (no overwrite), refuses
  without findings, audit_writer required, propagates resolution
  error, does NOT mutate `compliance_memos`.
* **`TestDetachEDDFindings` (3)** — marks row + emits audit, no-op
  when nothing attached (no audit), audit_writer required.
* **`TestReadHelpers` (4)** — get findings returns attached findings
  enriched with attachment metadata, onboarding/review contexts are
  disjoint, detached attachments excluded by default,
  invalid kind rejected.
* **`TestLifecycleIntegrationSmoke` (2)** — end-to-end: PR-01
  `set_edd_origin` → resolver picks it up; PR-03a outcome (not legacy
  `decision`) is the authoritative review outcome field — resolution
  works regardless of `decision` being NULL.

Full suite (3363 tests) passes after PR-04.

## PR-02 / PR-03 contract acknowledgements

* **PR-02 monitoring-originated reviews**: treated as first-class
  reviews. When an EDD comes from a monitoring alert that is itself
  linked to a periodic review, the EDD findings feed the **review**
  memo context (rule 4). Proven by
  `test_monitoring_alert_with_review_routes_to_review`.
* **PR-02 reverse-link displacement reality**: the resolver consults
  the explicit `edd_cases.linked_periodic_review_id` column as the
  authoritative *current* link. It does NOT enumerate every alert/review
  that ever pointed at the EDD. Last-write-wins semantics on the EDD
  side are preserved — PR-04 introduces no symmetric reverse-pointer
  cleanup.
* **PR-03 periodic review outcomes**: PR-04 reads
  `periodic_reviews.id` (the review row IS the review memo context).
  It does NOT read or write `periodic_reviews.decision`. Proven by
  `test_pr03_outcome_is_authoritative_not_decision`.
* **PR-03a decision vs outcome**: PR-04 never writes `decision`,
  never co-writes both; resolution succeeds even when the legacy
  `decision` column is NULL.
* **PR-01 audit-writer enforcement**: every mutating helper raises
  `MissingAuditWriter` BEFORE any DB write — same contract, reused
  exception class from `lifecycle_linkage`.
* **PR-01 linkage helper semantics**: PR-04 builds on top of, and
  consults, columns owned by `lifecycle_linkage` (`origin_context`,
  `linked_periodic_review_id`, `linked_monitoring_alert_id`).
  PR-04 does NOT write any of those columns — set them via the PR-01
  helpers as before.

## EX-01..EX-13 control posture

No file in `PROTECTED_FILES` is modified by this PR. No EX-control
critical file is touched. Migration 010 is additive (`CREATE TABLE
IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`); no existing column is
altered, no row is mutated. EX-01..EX-13 regressions are impossible
by construction.

## Deferred items (PR-05 and later)

* **HTTP route surface**: no new handlers in `server.py`. PR-05 is
  expected to add CRUD routes (`POST /api/edd/cases/:id/findings`,
  `POST /api/edd/cases/:id/attach-memo`, `GET …`).
* **Officer UI / queue clarity**: deliberately out of scope for PR-04.
* **Memo-handler consumption of attached findings**: `memo_handler.py`
  is protected (EX-03). Surface integration into memo generation is
  deferred — a future PR may add an additive call site that asks
  `get_memo_context_findings(...)` and renders findings into the
  rendered memo body. Until then, the linkage is auditable and
  queryable by back-office tooling without touching `memo_handler.py`.
* **PDF rendering of findings**: `pdf_generator.py` is protected
  (PR-04 does not touch it).
* **Promoting periodic-review memo to its own row**: the schema slot
  (`edd_memo_attachments.memo_id`) is already there for the future
  `periodic_review_memos` table; PR-04 deliberately does not introduce
  it because today the review row IS the review memo context.
* **PostgreSQL CHECK constraints** on `memo_context_kind` /
  `recommended_outcome` enums — application-layer enforcement only,
  consistent with PR-01..PR-03. A later migration may add named
  PG CHECK constraints once the runner supports dialect-specific blocks.

## Blocker report

None. No protected boundary required touching. No broader-risk issue
was encountered. The change is additive-only and stays inside the
documented PR-04 envelope.

---

## PR-04a addendum — Onboarding-attachment identity hardening

### What changed

PR-04 validation surfaced one meaningful artifact-model gap:
`attach_edd_findings_to_memo_context()` could create an `onboarding`
attachment row with `memo_id=NULL` *before* a `compliance_memos` row
existed. Once the onboarding memo was later generated, a *second*
active onboarding attachment could be created for the same EDD with
the real `memo_id` — leaving two active onboarding attachments under
two different identities.

PR-04a closes that gap with the smallest honest design (Option 1):

> **Onboarding attachment is FORBIDDEN until the onboarding memo
> exists.**

### The rule (authoritative)

* For `kind='onboarding'`, `attach_edd_findings_to_memo_context()`
  refuses to insert any attachment row when
  `resolve_active_memo_context()` returns `memo_id=None`. It raises
  `AttachmentValidationError` **before** any DB write and **before**
  any audit event is emitted.
* Once the onboarding memo (`compliance_memos` row) exists, attach
  succeeds normally and produces exactly one active row whose
  `memo_id` equals the real `compliance_memos.id`.
* `kind='periodic_review'` is unaffected — the `periodic_reviews.id`
  row IS the review memo context, so there is no pre-existence
  ambiguity to close there.

### Schema-level backstop

A partial unique index (migration 011) enforces the rule at the
schema level:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uix_edd_memo_attachments_active_identity
    ON edd_memo_attachments (
        edd_case_id,
        memo_context_kind,
        COALESCE(memo_id, 0),
        COALESCE(periodic_review_id, 0)
    )
    WHERE detached_at IS NULL;
```

* Scoped to ACTIVE rows (`detached_at IS NULL`) so detach-then-reattach
  still works and detached rows are preserved for audit history.
* `COALESCE(..., 0)` gives NULL-safe identity uniqueness portably across
  SQLite and PostgreSQL (both treat NULL as distinct in unique indexes
  by default; surrogate id columns start at 1, so 0 is a safe sentinel).
* This index is a defence-in-depth backstop. The application-layer
  guard in `attach_edd_findings_to_memo_context()` is the primary
  enforcement; the index protects against bypass and concurrent
  insertions.

### What PR-05 can safely assume

When PR-05 (or any other consumer) reads from `edd_memo_attachments`:

* Every active row with `memo_context_kind='onboarding'` has a
  non-NULL `memo_id` pointing at a real `compliance_memos.id`. There
  are no `memo_id=NULL` "ghost" onboarding attachments to defend
  against.
* For any `(edd_case_id, memo_context_kind, memo_id, periodic_review_id)`
  identity, there is **at most one** active row at any time.
* Detached rows (`detached_at IS NOT NULL`) may still exist and may
  legitimately have any historical `memo_id` value (including NULL on
  rows created by previous code paths, if any). Read helpers exclude
  these by default; pass `include_detached=True` only when audit
  history is being surfaced.

### Files changed in PR-04a

* `arie-backend/edd_memo_integration.py` — added the onboarding
  pre-memo guard in `attach_edd_findings_to_memo_context()`; updated
  the docstring to document the new rule.
* `arie-backend/migrations/scripts/migration_011_edd_memo_attachment_uniqueness.sql`
  — new additive partial unique index. No existing column changed.
* `arie-backend/tests/test_edd_memo_integration.py` — new
  `TestPR04aOnboardingAttachmentIdentity` class (8 tests):
  pre-memo attach fails cleanly, no row is created, attach succeeds
  after memo exists, periodic-review path unaffected, detach/reattach
  cycle ends with one active row, two EDDs aggregate to the same
  onboarding memo, schema-level uniqueness backstop blocks duplicate
  active inserts, detached rows do not collide with new active rows.
  Two pre-existing onboarding tests
  (`test_attach_is_idempotent_for_same_context`,
  `test_detach_marks_row_and_emits_audit`) were updated to insert a
  `compliance_memos` row first so they continue to exercise their
  original concern under the new rule.
* `arie-backend/docs/edd_memo_integration_pr04.md` — this addendum.

### EX-01..EX-13 control posture

No file in `PROTECTED_FILES` is modified. No EX-control critical file
is touched. Migration 011 is additive (`CREATE UNIQUE INDEX
IF NOT EXISTS`); no existing column or row is altered. EX-01..EX-13
regressions are impossible by construction.

### Deferred hardening (intentionally out of scope for PR-04a)

* **Concurrency hardening beyond the partial unique index.** The
  partial unique index is sufficient to make duplicate-active inserts
  impossible at the storage layer. A full read-modify-write retry
  wrapper around `attach_edd_findings_to_memo_context()` (so
  `IntegrityError` from a race becomes a clean reuse-existing-row
  return) is left for a future PR — today the application-layer
  `_find_active_attachment` lookup makes the race window vanishingly
  small in practice and the index makes the corruption impossible.
* **Reconciliation of historical NULL-memo_id onboarding attachments.**
  Production has not yet exercised `attach_edd_findings_to_memo_context`
  against real traffic, so no historical NULL-memo_id rows exist to
  reconcile. If/when a future deployment surfaces such rows, a one-shot
  reconciliation helper can be added then — it is not required by
  PR-04a to keep PR-05 unblocked.
* **PostgreSQL `NULLS NOT DISTINCT` simplification.** PG 15+ supports
  `UNIQUE NULLS NOT DISTINCT`, which would let us drop the `COALESCE`
  expression form. Deferred until the production minimum is known to
  be PG 15+.
* **Promoting periodic-review memo to its own row** — unchanged from
  PR-04: still deferred.

