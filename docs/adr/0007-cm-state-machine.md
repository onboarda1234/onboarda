# ADR-0007: Change Management State Machine — Transitions from `submitted`

## Status

Accepted

## Date

2026-04-15

## Context

During QA Rounds 8–11, an admin attempted to reject a Change Request
directly from `submitted` via `PATCH /api/change-management/requests/{id}`
with `{"status":"rejected"}` and received a 400 error.

Investigation confirmed this is **working as designed**. The state machine
(`change_management.py`, `CHANGE_REQUEST_TRANSITIONS`) defines only two
legal transitions from `submitted`:

| From        | To                    |
|-------------|----------------------|
| `submitted` | `triage_in_progress` |
| `submitted` | `cancelled`          |

The `rejected` status is a **terminal state** reachable only from
`approval_pending` via the dedicated `POST .../reject` endpoint.

## Decision

1. **No "force-reject from submitted" path.** If an admin needs to
   discard a CR that is still in `submitted`, the correct action is
   `PATCH {status:'cancelled'}` with appropriate notes.

2. The full lifecycle to reach `rejected` is:
   ```
   draft → submitted → triage_in_progress → ready_for_review
   → approval_pending → rejected (via POST .../reject)
   ```

3. All 14 states and their valid transitions are defined in
   `CHANGE_REQUEST_TRANSITIONS` in `change_management.py`. Terminal
   states (`rejected`, `implemented`, `cancelled`, `superseded`) have
   no outbound transitions.

4. The `POST .../reject` endpoint is restricted to roles
   `admin`, `sco`, `co` — analysts cannot reject.

5. 404 responses (application not found) are **not** AuthZ denials and
   do not produce denial audit rows. They indicate a lookup failure, not
   a cross-tenant probe.

## Consequences

- Admins who wish to discard submitted CRs must use `cancelled`, not
  `rejected`. This preserves semantic distinction: `cancelled` = withdrawn
  before review; `rejected` = reviewed and denied.
- A `test_submitted_to_rejected_blocked` regression test ensures this
  invariant is maintained.
- The PATCH handler correctly returns 400 (not 403) for invalid transitions.

## Alternatives Considered

- **Allow `submitted → rejected` directly**: Rejected as it conflates
  pre-review withdrawal with post-review denial, losing audit clarity.
- **Add a `force_reject` admin override**: Rejected — `cancelled` is
  sufficient and avoids adding exception paths to the state machine.
