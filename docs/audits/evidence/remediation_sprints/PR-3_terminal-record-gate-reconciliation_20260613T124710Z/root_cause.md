# Root Cause

## Root Cause

`ApplicationDetailHandler.get` recalculated and attached live approval-gate blockers for every non-client application detail response:

- `result["gate_blockers"] = collect_approval_gate_blockers(result, db)`
- `result["gate_blocker_count"] = len(result["gate_blockers"])`

This happened regardless of whether the record was active or terminal.

`collect_approval_gate_blockers(...)` correctly evaluates the current application state, but the detail API exposed those results as active approval blockers even for `approved`, `rejected`, `withdrawn`, or closed-like terminal records.

The Back Office Case Command Centre then treated the backend `gate_blockers` payload as authoritative current approval blockers and rendered the terminal record as `Blocked`.

## Missing Model Distinction

The product lacked a decision-aware presentation contract for terminal records:

- no explicit decision-time approval basis in the detail response
- no separate current diagnostics object
- no legacy/evidence-incomplete classification for approved records without decision-time evidence
- no future approval gate snapshot persisted in new decision records

## Fix Strategy

Chosen strategy: hybrid decision snapshot + legacy label.

Implemented:

- Terminal records now return `approval_gate_presentation.mode = terminal_decision_context`.
- Terminal records expose `decision_basis` from matching decision records where available.
- Approved records without a decision-time approval gate snapshot are labelled `legacy_evidence_incomplete`.
- Current gate failures on terminal records move to `current_gate_diagnostics` with `applies_to = current_state_only`.
- Terminal records return empty `gate_blockers` / `gate_blocker_count=0`, so current diagnostics are not consumed as active approval blockers.
- Active/non-terminal records continue to return blocking `gate_blockers` and remain fail-closed.
- New approvals persist an `approval_gate_snapshot` inside the normalized decision record after `ApprovalGateValidator.validate_approval(...)` passes.
- Back Office Case Command Centre renders terminal decision context separately from current diagnostics.
- Client-safe application projection excludes all new back-office-only decision/gate presentation fields.
