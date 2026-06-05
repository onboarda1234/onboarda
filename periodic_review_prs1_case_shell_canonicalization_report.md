# PRS-1 — Periodic Review Case Shell Canonicalization Report

Date: 2026-06-05
Source-of-truth branch base: `origin/main`
Source-of-truth commit: `7b735c1dafe1f129b832cdbe961e64ecc9338e6d`
Implementation branch: `codex/prs1-case-shell-canonicalization`
Worktree: `/tmp/onboarda-prs1-case-shell`

## 1. Scope Decision

PRS-1 was implemented as a hardening pass on the existing `periodic_reviews` model and its existing handlers/UI surfaces.

No duplicate Periodic Review table or workflow shell was introduced.

No schema migration was required.

Reason:

- `periodic_reviews` already contains the required case-shell primitives: review id, application linkage, client name, risk level, trigger source/reason, due date, next review date, assignment, status, priority, completion fields, and audit-adjacent timestamps.
- The main gap was not missing structure. The gap was inconsistent queue semantics and misleading product framing.

## 2. What Changed

### Backend

`arie-backend/periodic_review_projection_service.py`

- Extended the shared projection into the canonical PRS-1 queue contract.
- Added stable queue fields without changing storage shape:
  - `review_reference`
  - `application_ref`
  - `assigned_officer_name`
  - `owner_display_name`
  - `owner_state`
  - `queue_status`
  - `queue_status_label`
  - `due_state`
  - `due_status_label`
  - `is_overdue`
  - `is_due_date_missing`
  - `days_until_due`
  - `is_blocked`
  - `created_at`
  - derived `updated_at`
  - `last_activity_at`
  - `audit_reference`
  - `primary_action_label`
  - `can_take_action`
  - `is_terminal`
- Added deterministic due-date classification:
  - `missing_due_date`
  - `scheduled`
  - `due`
  - `overdue`
- Added canonical queue-status mapping on top of raw backend status.
- Batched application and officer lookups in `list_review_projections()` to avoid additional list-path N+1 expansion.

`arie-backend/server.py`

- Hardened `GET /api/monitoring/reviews` into the canonical queue endpoint.
- Added queue query support:
  - `queue=due|overdue|open|awaiting_client|in_review|completed|cancelled`
  - `assigned_to_me=true`
- Reused batch projections in the list path instead of recalculating per row.
- Surfaced canonical queue fields directly on review payloads, not only inside nested `projection`.

### Frontend

`arie-backoffice.html`

- Reframed the existing `periodic-review-signals` surface into a real Periodic Review queue without changing the route key.
- Renamed user-facing navigation/title text from `Periodic Review Signals` to `Periodic Review Queue`.
- Changed page copy so Monitoring Alerts remains the alert surface and Periodic Review is presented as a case queue.
- Replaced client-side “signal” inference with projection-backed queue semantics.
- Updated the queue table to show case-oriented fields:
  - client + application reference
  - risk
  - due date
  - case status
  - owner
  - last activity
  - primary action
- Kept row actions deep-linked into Application Lifecycle.

## 3. Canonical Status Semantics

Raw backend status is preserved. PRS-1 adds a canonical queue status for filtering and officer-facing queue behavior.

| Raw backend status | Queue status | Display label | Meaning | Terminal |
| --- | --- | --- | --- | --- |
| `pending` + due date in future | `open` | Open | Case exists but is not yet due | No |
| `pending` + due today | `due` | Due | Officer action is due now | No |
| `pending` + due date in past | `overdue` | Overdue | Officer action is overdue | No |
| `pending` + missing due date | `open` | Open | Case exists but due date is missing and explicitly flagged | No |
| `awaiting_information` | `awaiting_client` | Awaiting Client | Waiting on external/client information | No |
| `in_progress` | `in_review` | In Review | Officer actively reviewing | No |
| `pending_senior_review` | `in_review` | In Review | Review is active, but senior review is the operating phase | No |
| `completed` | `completed` | Completed | Review closed | Yes |
| `cancelled` | `cancelled` | Cancelled | Review cancelled/terminal | Yes |

Operational blockers are still surfaced separately via:

- `status_label`
- `blocker_count`
- `blocker_summary`
- `is_blocked`

That keeps PRS-1 from overbuilding a new state machine while still preserving “Blocked” visibility.

## 4. Due-Date / Owner / Audit Semantics

### Due-date hardening

- Queue due date is always exposed from `due_date` with safe fallback to `next_review_date`.
- Overdue is derived deterministically from UTC date comparison.
- Missing due dates do not silently look normal:
  - `is_due_date_missing=true`
  - `due_state=missing_due_date`
  - `due_status_label=Missing Due Date`

### Owner hardening

- `assigned_officer` remains the stored owner id.
- PRS-1 adds:
  - `assigned_officer_name`
  - `owner_display_name`
  - `owner_state`
- `assigned_to_me=true` now works on the canonical queue endpoint.

### Audit integrity

Existing audit patterns were preserved and reused:

- creation via approval scheduling: `Monitoring Enrollment`
- creation via alert routing: `monitoring.alert.routed_to_review`
- assignment changes: `periodic_review.assignment_updated`
- status changes: `periodic_review.state_changed`
- due-date/risk cadence changes on rerate: `periodic_review.risk_rerated`

PRS-1 adds `audit_reference=periodic_review:<id>` to the canonical review projection so queue payloads retain audit linkage without creating a separate audit system.

## 5. Explicit Non-Goals Preserved

PRS-1 intentionally did not build:

- portal attestation
- conditional document request workflow
- memo addendum
- reminder emails
- AI risk conclusions
- automatic risk update or offboarding
- duplicate Investigation Case creation model
- duplicate Change Management model

## 6. Verification

Passed:

- `pytest -q arie-backend/tests/test_periodic_review_handlers.py`
- `pytest -q arie-backend/tests/test_periodic_review_phase1_handlers.py`
- `pytest -q arie-backend/tests/test_periodic_review_phase1_canonical.py`
- `pytest -q arie-backend/tests/test_monitoring_routing.py`
- `pytest -q arie-backend/tests/test_monitoring_enrollment.py`
- `pytest -q arie-backend/tests/test_backoffice_monitoring_navigation_static.py arie-backend/tests/test_application_lifecycle_tab_shell_static.py`

All passed on the clean `origin/main`-based PRS-1 worktree.

## 7. Runtime Verification Limitation

No new privileged staging verification was performed in this environment.

The limitation remains the same as PRS-0:

- staging `/api/version` is auth-gated
- no `STAGING_QA_EMAIL`
- no `STAGING_QA_PASSWORD`
- no staging back-office token were available in this environment

Because of that, PRS-1 runtime confirmation is test-backed and source-backed, not authenticated staging-session-backed.

## 8. PRS-2 Readiness

PRS-1 is complete enough to proceed.

Recommended PRS-2 entry point:

1. Use this canonical queue as the only officer-facing Periodic Review list surface.
2. Add client attestation capture against the existing review shell.
3. Add conditional evidence request logic against existing `required_items` and document-link flows.
4. Preserve Lifecycle as the execution workspace and avoid introducing a second review editor.

Conclusion:

Proceed to PRS-2. `periodic_reviews` is now a canonical case shell and queue surface, not a loose signal list.
