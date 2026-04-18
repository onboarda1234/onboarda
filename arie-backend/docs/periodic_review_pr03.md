# PR-03 — Periodic Review Operating Model

## Summary

PR-03 turns `periodic_reviews` from a thin pending/completed placeholder
into a real lifecycle review object with explicit operational states,
trigger provenance preserved from PR-01/PR-02, structured required-item
generation, deterministic and duplicate-safe escalation to EDD, and
explicit outcome recording — all while preserving the onboarding memo
identity model and without weakening EX-01..EX-13 controls.

## Repo-grounded current state (pre-PR-03)

* **State model**: `periodic_reviews.status` defaulted to `pending`;
  the only other value the codebase wrote was `completed`. There were
  no intermediate operational states.
* **Trigger provenance**: PR-01 added `trigger_source`,
  `linked_monitoring_alert_id`, `linked_edd_case_id`, `review_reason`,
  `assigned_at`, `closed_at`, `sla_due_at`, `priority`. PR-02 already
  populates `trigger_source='monitoring_alert'` for alert-routed
  reviews via `lifecycle_linkage.set_periodic_review_trigger`.
* **Structured required items**: did not exist.
* **Escalation to EDD**: there was no review-side escalation path.
  `EDDCreateHandler.post` enforced active-EDD-per-application
  duplicate-prevention; `monitoring_routing.route_alert_to_edd` reused
  the same predicate for alert-driven escalation.
* **Outcome recording**: `decision` was a free-text-but-validated field
  (`continue|enhanced_monitoring|request_info|exit_relationship`).
  Progress and outcome were conflated in `status='completed'` plus
  `decision`.
* **Memo / artifact linkage**: `compliance_memos` is the onboarding
  memo identity (per-application per-version). No periodic-review
  pointer existed; PR-01 design left memo history independent of
  lifecycle review context.

## Files changed

* `arie-backend/migrations/scripts/migration_009_periodic_review_operating_model.sql`
  — additive nullable columns and lookup indexes.
* `arie-backend/periodic_review_engine.py` — new module: state
  vocabulary, transition validation, required-item generation, outcome
  recording, escalate-to-EDD reusing PR-01 helpers + active-EDD dedup.
  Audit-writer enforced.
* `arie-backend/server.py` — additive: 5 new handlers and 5 new route
  registrations under `/api/monitoring/reviews/<id>/...`. The legacy
  `PeriodicReviewDecisionHandler` is unchanged.
* `arie-backend/tests/test_periodic_review_engine.py` — 30 unit tests.
* `arie-backend/tests/test_periodic_review_handlers.py` — 18 HTTP tests.
* `arie-backend/docs/periodic_review_pr03.md` — this design note.

## Implementation summary

### State model

The existing `status` column on `periodic_reviews` carries the
operational state. PR-03 extends the in-code vocabulary from
`{pending, completed}` to:

* `pending` → `in_progress` → `awaiting_information` →
  `pending_senior_review` → `completed`

There is no DB-level CHECK on `status`, so the expansion is additive.
Backwards transitions are rejected. Completion is only reachable via
`record_review_outcome` (so completion always carries an explicit
outcome), not via `transition_review_state`.

### Provenance handling

PR-03 reads PR-01 / PR-02 fields directly:

* `trigger_source`, `linked_monitoring_alert_id`, `review_reason` are
  surfaced into required-item rationales (e.g. the
  `monitoring_alert_followup` item references the alert id).
* No new provenance fields are introduced.

### Required-item generation

Implemented in `_generate_items_for_context` and the public
`generate_required_items` helper:

* Baseline items (`kyc_refresh`, `ubo_confirmation`) are always
  emitted.
* Risk-tier items: `source_of_funds_refresh` and
  `source_of_wealth_refresh` for `HIGH`/`VERY_HIGH`;
  `licensing_refresh` for `VERY_HIGH`.
* Application-context items: `jurisdiction_review`,
  `business_activity_review`, `ownership_change_review` when the
  matching `applications` columns are populated.
* `document_expiry_refresh` when any document on the application is
  older than 365 days (`uploaded_at`-based; conservative default).
* `monitoring_alert_followup` when `trigger_source='monitoring_alert'`.
* `prior_outcome_followup` when the previous completed review carried
  `outcome` in `{enhanced_monitoring, edd_required}` (or legacy
  `decision` equivalents).

The list is persisted as JSON on `required_items` and stamped with
`required_items_generated_at`. Per-item status tracking is **not**
implemented in PR-03 — that belongs to the future information-request
engine.

### Review progression logic

`transition_review_state(review_id, new_state, reason, user, audit_writer)`

* Validates `new_state ∈ VALID_REVIEW_STATES`.
* Validates the transition against `STATE_TRANSITIONS`.
* Refuses to transition into `completed` (must use
  `record_review_outcome`).
* Refuses to mutate a completed review.
* Persists `status` and `state_changed_at`.
* Emits `periodic_review.state_changed` audit with before/after.

### Escalation-to-EDD logic

`escalate_review_to_edd(review_id, trigger_notes, priority, user, audit_writer)`

* If the review is already linked to a non-terminal EDD, reuse it.
* Else if any other active EDD exists for the same application
  (matches `EDDCreateHandler.post` and
  `monitoring_routing.route_alert_to_edd` predicates), link to it and
  call `lifecycle_linkage.set_edd_origin(origin_context='periodic_review',
  linked_periodic_review_id=review_id)`.
* Else create a new `edd_cases` row mirroring the INSERT shape used by
  `EDDCreateHandler.post`/`monitoring_routing._create_edd_case_row`,
  set origin via `set_edd_origin`, optionally call `mark_edd_assigned`
  with `priority`.
* Emits `periodic_review.escalated_to_edd` with `created`/`reused`/
  `edd_case_id`.

### Duplicate-prevention logic

Reuses the existing predicate (`stage NOT IN TERMINAL_EDD_STAGES`).
PR-03 does not introduce a new EDD creation path; it composes the
existing one. Repeated escalation from the same review is a no-op
(returns the same `edd_case_id`).

### Outcome recording

`record_review_outcome(review_id, outcome, outcome_reason, user, audit_writer)`

* Validates `outcome ∈ VALID_REVIEW_OUTCOMES`
  (`no_change|enhanced_monitoring|edd_required|exit_recommended`).
* Requires non-empty `outcome_reason`.
* Decision-replay protection: refuses to complete a review that is
  already completed (mirrors the C-03 fix in
  `PeriodicReviewDecisionHandler`).
* Writes `outcome`, `outcome_reason`, `outcome_recorded_at`,
  `status='completed'`, `completed_at`, `state_changed_at`.
* Calls `lifecycle_linkage.mark_review_closed` so PR-01 closure audit
  is preserved (`lifecycle.review.closed`).
* Emits `periodic_review.outcome_recorded`.
* Does **not** touch `compliance_memos` (proven by test).

### Audit behavior

All mutating helpers require a non-None `audit_writer` and raise
`MissingAuditWriter` (from `lifecycle_linkage`) before any DB write
when one is missing — the PR-01 enforcement contract is preserved.
Each helper emits a structured audit event with before/after state.

## Tests added

### `tests/test_periodic_review_engine.py` (30 tests)

* `TestStateTransitions` (6) — pending→in_progress, awaiting_info,
  cannot-skip-state, cannot-complete-via-state, invalid-state-rejected,
  audit-writer-required.
* `TestRequiredItemsGeneration` (8) — baseline, HIGH-risk SoF/SoW,
  VERY_HIGH licensing, jurisdiction/sector/ownership, monitoring-alert
  follow-up, prior-outcome follow-up, completed-refusal,
  audit-writer-required.
* `TestEscalateToEDD` (8) — creates new EDD, reuses review-linked EDD,
  reuses other-origin active EDD (same predicate as
  `EDDCreateHandler` / `monitoring_routing` — proves dedup contract),
  monitoring-originated review escalates as first-class,
  PR-02 reverse-link displacement contract pinned, completed-refusal,
  audit-writer-required.
* `TestRecordOutcome` (6) — happy path, decision-replay blocked,
  invalid outcome, empty reason, **does-not-touch-compliance_memos**
  (memo history preservation proven), audit-writer-required.
* `TestReadHelpers` (3) — state coercion of legacy values,
  empty-required-items default, ReviewNotFound.

### `tests/test_periodic_review_handlers.py` (18 tests)

HTTP/API-level coverage of all 5 new endpoints:

* `TestStateHandler` (5) — auth required (401), client role forbidden
  (403), happy-path 200 + persistence, invalid-transition 409,
  terminal-completion-via-state blocked 409, missing-state 400.
* `TestRequiredItemsHandler` (3) — empty-default GET, generate +
  read-back, generate-on-completed 409.
* `TestEscalateHandler` (4) — first-time creates + PR-01 origin
  recorded, repeat is dedup-safe (one EDD row), completed 409,
  unknown-id 404.
* `TestCompleteHandler` (5) — happy path + PR-01 closed_at,
  decision-replay 409, invalid outcome 400, missing outcome 400,
  missing reason 400.

Full suite: **3313 passed** after PR-03 (3265 → 3313).

## PR-02 contract acknowledgements

1. **Monitoring-originated review creation/reuse**: PR-02
   `route_alert_to_periodic_review` already creates real
   `periodic_reviews` rows with `trigger_source='monitoring_alert'`;
   PR-03 treats them as first-class reviews with the same operating
   model as scheduled reviews. Proven by
   `test_monitoring_originated_review_escalates_as_first_class`.
2. **EDD reverse-link displacement contract**: when a periodic-review
   escalation reuses an EDD that was previously the target of a
   monitoring alert, PR-03 sets the EDD's `linked_periodic_review_id`
   and origin to `periodic_review` via PR-01 `set_edd_origin`. The
   alert-side **forward** link (`alert.linked_edd_case_id`) is
   preserved so traceability from the alert to the EDD is never
   broken. The EDD's own reverse pointers are last-write-wins by
   design and may not enumerate every prior originator. This asymmetry
   is pinned by
   `test_pr02_reverse_link_displacement_contract_respected`.
3. **Handler/API seam risk near PR-02**: PR-03 adds focused HTTP-level
   tests for the new periodic-review handlers
   (`test_periodic_review_handlers.py`). The PR-02-noted gap in
   handler-level tests for `MonitoringAlertDetailHandler.patch` is
   intentionally **deferred** — PR-03 does not modify that handler
   and the engine-level `test_monitoring_routing.py` already covers
   the routing primitives.

## Deferred items (PR-04+)

* Per-required-item status tracking and the actual information-request
  engine (response capture, client-portal surfacing).
* Real cadence policy for `document_expiry_refresh` (currently a
  conservative 365-day staleness heuristic; production cadence belongs
  to the rule engine).
* Memo / artifact pointer for periodic-review-derived artifacts (PR-03
  intentionally leaves `compliance_memos` untouched).
* Backoffice UI surfaces for the new state model and outcome.
* Provider integration paths (ComplyAdvantage activation, screening
  abstraction promotion) — out of scope by problem-statement
  non-goals.
* Tests for `MonitoringAlertDetailHandler.patch` HTTP seam (PR-02
  follow-up).

## Protected-control posture

* No file in `protected_controls.PROTECTED_FILES` is modified.
* `server.py` is touched only additively (5 new handler classes + 5
  new route registrations). None of the touched paths intersect EX-02
  (CSRF), EX-07 (approval gate), EX-09 (rate-limit), EX-11 (officer
  sign-off), EX-12 (client-side security), or EX-13 (batch-fetch /
  ETag) enforcement points.
* `memo_handler.py`, `rule_engine.py`, `validation_engine.py`,
  `supervisor_engine.py`, `auth.py`, `base_handler.py`,
  `change_management.py`, `gdpr.py`, `screening.py`,
  `sumsub_client.py`, `pdf_generator.py`, `claude_client.py`,
  `db.py`, `arie-backoffice.html`, `arie-portal.html` — all unchanged.
* `db.py` schema is extended **only** through the migration script
  (`migration_009_*.sql`); no inline `_run_migrations` change is made.
