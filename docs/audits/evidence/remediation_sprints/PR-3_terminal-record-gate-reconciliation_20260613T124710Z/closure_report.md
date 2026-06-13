# Closure Report

## PR Name

PR-3 — Terminal Record Gate Reconciliation

## Linked Remediation ID

FSI-003 — Approved and terminal records fail current approval gates.

## Original Issue Summary

Approved and terminal records on staging displayed current approval-gate blockers as if the historical decision was invalid or still awaiting approval action.

## Re-Diagnosis Result

Reproduced on staging aligned to source-of-truth main SHA `3f00f491c75a5605440d56899bebb9e513cc1cb3`.

Multiple approved/rejected records returned non-empty `gate_blockers` with current IDV, memo, screening, and supervisor blockers.

## Root Cause

The application detail API recalculated current approval gates for every non-client application detail response and exposed them as `gate_blockers` even when the record was terminal. The Case Command Centre treated those blockers as active approval blockers. Decision records did not consistently provide decision-time gate context, and legacy records lacked explicit evidence-incomplete labelling.

## Chosen Fix Strategy

Hybrid:

- decision-context presentation for terminal records
- legacy/evidence-incomplete labelling when decision-time approval evidence is missing
- current gate diagnostics separated from action-required approval blockers
- forward-looking approval gate snapshot on new approval decision records

## Files Changed

- `arie-backend/server.py`
- `arie-backoffice.html`
- `arie-backend/tests/test_pr3_terminal_record_gate_reconciliation.py`
- `arie-backend/tests/test_case_command_centre_runtime.py`
- PR-3 evidence pack files

## Behaviour Before Fix

- Terminal approved/rejected records returned current `gate_blockers`.
- Back Office rendered terminal records as `Blocked`.
- Officers could not distinguish historical decision basis from current diagnostics.
- Legacy approved records without decision-time evidence were not labelled clearly.

## Behaviour After Fix

- Active non-terminal records still return blocking `gate_blockers`.
- Terminal records return no action-required `gate_blockers`.
- Terminal records return `approval_gate_presentation.mode = terminal_decision_context`.
- Terminal records return `current_gate_diagnostics` separately and explicitly labelled as current-state only.
- Legacy approved records without decision-time gate snapshots are labelled `legacy_evidence_incomplete`.
- New approvals save an `approval_gate_snapshot` in the decision record.
- Back Office renders terminal decision context separately from active approval blockers.
- Client application projection excludes new internal gate/decision presentation fields.

## Tests Added/Updated

- Added `arie-backend/tests/test_pr3_terminal_record_gate_reconciliation.py`.
- Updated `arie-backend/tests/test_case_command_centre_runtime.py` to include terminal Case Command Centre runtime coverage.

## Targeted Test Results

PASS:

- `git diff --check`
- `python3.11 -m py_compile arie-backend/server.py arie-backend/tests/test_pr3_terminal_record_gate_reconciliation.py`
- `python3.11 -m pytest arie-backend/tests/test_pr3_terminal_record_gate_reconciliation.py -q` — 7 passed
- `python3.11 -m pytest arie-backend/tests/test_case_command_centre_runtime.py -q` — 28 passed
- `python3.11 -m pytest arie-backend/tests/test_approval_gate.py arie-backend/tests/test_decision_model.py -q` — 59 passed

## Regression Results

FSI-001:

- `python3.11 -m pytest arie-backend/tests/test_pr1_client_api_boundary.py arie-backend/tests/test_pr1b_client_notification_boundary.py -q` — 9 passed

FSI-002:

- `python3.11 -m pytest arie-backend/tests/test_sprint35.py -q` — 40 passed

## Full Suite Results

- `python3.11 -m pytest arie-backend/tests -q --ignore=arie-backend/tests/test_pdf_generator.py`
- PASS — 5,265 passed, 17 skipped

## Browser Test Results

Local branch Playwright smoke:

- PASS — terminal record context rendered without misleading active blockers.
- PASS — active non-terminal blocker state still rendered as blocked.
- Evidence:
  - `runtime_json/branch_browser_smoke_result.json`
  - `screenshots/branch_browser_terminal_record_context.png`
  - `screenshots/branch_browser_active_gate_blocked.png`

## Staging Deploy Evidence

Pending. PR-3 is not yet merged/deployed.

## /api/version Evidence

Pending post-merge deployment.

Pre-fix diagnosis confirmed staging was aligned to main SHA `3f00f491c75a5605440d56899bebb9e513cc1cb3`.

## API Smoke Test Evidence

Pending post-merge deployment.

## Browser Smoke Test Evidence

Local branch browser smoke complete. Merged-main staging browser smoke is pending.

## Screenshots/Evidence Folder Path

`docs/audits/evidence/remediation_sprints/PR-3_terminal-record-gate-reconciliation_20260613T124710Z/`

## Remaining Risks

- Existing legacy approved records without decision-time gate snapshots are not retroactively remediated; they are clearly labelled `legacy_evidence_incomplete`.
- FSI-003 cannot be closed until staging proves merged-main behavior.

## Items Not Closed By This PR

- No other remediation item is closed by PR-3.
- FSI-003 is not closed at branch stage.

## Final Closure Verdict

Branch verdict: PARTIALLY FIXED.

FSI-003 may be marked CLOSED only after PR-3 is merged, deployed to staging, staging `/api/version` matches merged main SHA, staging API smoke passes, and staging browser smoke passes.
