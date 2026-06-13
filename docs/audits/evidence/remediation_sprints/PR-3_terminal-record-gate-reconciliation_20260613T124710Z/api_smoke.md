# Api Smoke

## Branch Stage

Runtime diagnosis was performed against staging at current main before PR-3:

- `/api/version` matched `3f00f491c75a5605440d56899bebb9e513cc1cb3`.
- Approved/rejected terminal records reproduced misleading current `gate_blockers`.

Raw redacted evidence:

- `runtime_json/diagnosis_terminal_gate_staging_redacted.json`

## Required Post-Merge Staging API Smoke

Not yet complete.

After PR-3 is merged and deployed, staging API smoke must prove:

- Approved record no longer returns current approval-gate failures as `gate_blockers`.
- Approved record returns `approval_gate_presentation.mode = terminal_decision_context`.
- Approved record returns decision basis where available.
- Legacy approved record without decision-time evidence is labelled `legacy_evidence_incomplete`.
- Current gate diagnostics, if present, are under `current_gate_diagnostics` with `applies_to=current_state_only`.
- Rejected/withdrawn terminal records do not return irrelevant approval-action blockers.
- Non-terminal pending/in-review records still return fail-closed `gate_blockers`.
- FSI-001 client boundary regression still passes.
- FSI-002 logout revocation regression still passes.

FSI-003 must remain below CLOSED until this merged-main staging API smoke passes.
