# PR-CA4 Closure Report

## PR name

`PR-CA4 - Mesh Parity and Screening UI/UX Hardening`

## Linked remediation IDs

- `CA-PAR-001`
- `CA-PAR-002`
- `CA-PAR-003`
- `CA-PAR-004`
- `CA-PAR-005`
- `CA-PAR-007`
- `CA-PAR-009`
- `CA-UX-001`
- `CA-UX-002`
- `CA-UX-003`
- `CA-UX-004`
- `CA-UX-005`
- `CA-UX-006`
- `CA-UX-007`
- `CA-UX-008`
- `CA-UX-010`
- `CA-UX-011`
- `CA-UX-012`

## Original issue summary

RegMind needed Mesh-parity-grade screening/adverse-media display: consistent current-risk counts across queue/detail/memo, canonical adverse-media rollup, visible provider references through progressive disclosure, clearer officer labels, subject-specific approval blockers, and defensible adverse-media source context.

## Re-diagnosis result

- Current `origin/main` SHA: `b83e3052485d432a1e47adbe1f4d9bb1bbea4a58`
- Branch name: `codex/pr-ca4-mesh-parity-screening-ui-ux`
- Branch commit SHA: `649e57ba7825ae1956ea42b083a28a5d7e82fdc8`; follow-up CodeRabbit commit pending at the time this evidence entry was updated
- Does the issue still exist on current `origin/main`? Yes. The gaps were reproduced by inspecting queue/detail/memo/UI code and by updating regression tests around the missing behavior.
- Evidence: see `diagnosis.md` and `root_cause.md`.

## Root cause

Officer-facing views still consumed older display projections and ambiguous labels even after PR-CA1/CA2/CA3 established provider source truth, evidence references, and canonical state integrity. Memo adverse-media context also did not fully trust canonical provider evidence when legacy rollup flags were absent.

## Files changed

- `arie-backend/server.py`
- `arie-backend/memo_handler.py`
- `arie-backoffice.html`
- `arie-backend/tests/test_phase3_memo_integrity.py`
- `arie-backend/tests/test_screening_queue.py`
- `arie-backend/tests/test_backoffice_ca_truthflow_static.py`
- `arie-backend/tests/test_backoffice_review_audit.py`
- `arie-backend/tests/test_case_command_centre_runtime.py`
- `arie-backend/tests/test_inline_screening_runtime.py`

## Behaviour before fix

- Adverse-media provider evidence could be under-represented in memo context.
- Queue/detail payloads did not expose a compact canonical current-risk rollup.
- Article/source context was not consistently propagated into display evidence.
- Primary officer actions included ambiguous `No Match` and `Match` labels.
- Generic categories such as `Other` or `Provider screening hit` appeared in officer surfaces.
- Approval blocker messages could be generic or technical rather than subject-specific.

## Behaviour after fix

- Queue/detail evidence includes canonical rollups for current, unresolved, stale, historical, duplicate, category, and adverse-media risk counts.
- Memo context consumes canonical adverse-media provider evidence even when old rollup flags are missing.
- Provider evidence preserves and renders title, publisher/source, publication date, match rationale, relevance, confidence, and source-unavailable next action where available.
- Officer actions use business-readable labels: `Clear as False Positive`, `Confirm True Match`, `Escalate`, and `Request More Information`.
- Generic fallback category is now `Unclassified Provider Risk`.
- Approval blockers use plain-English, subject-specific language in default officer views.
- Provider references remain progressively disclosed rather than cluttering default rows.

## Tests added/updated

- Added memo adverse-media canonical evidence regression.
- Added queue evidence current/stale/historical/duplicate rollup regression.
- Updated static UI tests for source context, evidence summary, disposition labels, provider refs, and category fallback.
- Updated runtime rendering tests for inline screening actions and activity/audit labels.
- Updated Case Command Centre blocker tests for plain-English blocker text.
- Updated post-CodeRabbit assertions for source URL fallback, internal status key display labels, legacy true-match audit labeling, and cleared provider decisions in risk rollups.

## Targeted test results

See `test_results.md`.

## Full suite results

See `full_suite_results.md`.

## Browser test results, if applicable

- Browser: pending for staging
- URL: pending
- Role: pending
- Steps: see `browser_smoke.md`
- Result: not claimed before merge/deploy
- Screenshot path: pending

## Staging deploy evidence

- Merged main SHA: pending
- Deployment mechanism: pending
- ECS/task/image evidence, if applicable: pending
- Deployed at: pending

## /api/version evidence

Pending after merge/deploy.

## API smoke test evidence

Pending after merge/deploy. See `api_smoke.md`.

## Browser smoke test evidence, if applicable

Pending after merge/deploy. See `browser_smoke.md`.

## Screenshots/evidence folder path

`docs/audits/evidence/remediation_sprints/PR-CA4_mesh-parity-screening-ui-ux_20260615T025229Z/`

## Remaining risks

- Staging parity and browser evidence are still required before any CA-PAR or CA-UX issue can be closed.
- Live Mesh dashboard visual parity is not claimed by local fixture tests alone.

## Items not closed by this PR

- No PR-7, DOC, CR, post-approval locking, officer correction controls, or unrelated remediation items were touched or marked closed.
- All PR-CA4 target issues remain `PARTIALLY FIXED` until merged-main staging validation and browser smoke pass.

## Final closure verdict

`PARTIALLY FIXED`

Rationale: code and local regression coverage are complete; initial full backend suite passed, and post-CodeRabbit focused/regression suites passed. Closure still requires GitHub CI on the follow-up commit, PR merge, staging deploy, `/api/version`, staging API smoke, and authenticated browser smoke.
