# Closure Report

## PR Name

PR-4B - Screening Memo Readiness Metadata Hardening

## Linked Remediation IDs

- `FSI-007` - Screening truth summary has unsafe approval-ready terminology

## Original Issue Summary

Screening readiness language must not imply approval readiness when screening blockers remain. PR-4 fixed current screening truth semantics, but staging smoke found a residual contradiction in stale memo metadata exposed through application detail.

## Re-diagnosis Result

PR-4 staging smoke failed on `GET /api/applications/13cabbdf214542ea`:
- `screening_truth_summary.approval_ready=false`
- `screening_truth_summary.approval_blocking=true`
- stale `latest_memo_data.metadata.screening_state_summary.approval_ready=true`
- stale `latest_memo_data.metadata.screening_state_summary.approval_blocking=true`
- stale `latest_memo_data.metadata.agent5_input_contract.screening_terminality_summary.approval_ready=true`
- stale `latest_memo_data.metadata.agent5_input_contract.screening_terminality_summary.approval_blocking=true`

## Root Cause

Stored memo rows can retain legacy screening readiness summaries from before PR-4. `ApplicationDetailHandler.get()` parsed and exposed those nested memo metadata blobs without read-time sanitization.

## Files Changed

- `arie-backend/screening_state.py`
- `arie-backend/server.py`
- `arie-backend/tests/test_pr4_screening_memo_readiness_metadata.py`
- `docs/audits/evidence/remediation_sprints/PR-4B_screening-memo-readiness-metadata-hardening_20260613T164500Z/*`

## Behaviour Before Fix

Back-office application detail could expose a safe current screening summary and contradictory stale memo metadata in the same response.

## Behaviour After Fix

Legacy memo screening readiness summaries are projected through `sanitize_screening_readiness_summary()` before being returned from application detail. If a summary is approval-blocking, `approval_ready`, `screening_gate_ready`, and `approval_gate_ready` are forced false and blocker reasons are preserved.

## Tests Added/Updated

- Added `test_pr4_screening_memo_readiness_metadata.py`.
- Added sanitizer unit coverage and application-detail runtime coverage for stale memo metadata.

## Targeted Test Results

See `test_results.md`.

## Full Suite Results

Local full suite remains blocked by the known native WeasyPrint/Pango CFFI issue on this machine. GitHub CI must pass before merge.

## Browser Test Results, If Applicable

Pending merged-main staging deployment.

## Staging Deploy Evidence

Pending merged-main staging deployment.

## /api/version Evidence

Pending merged-main staging deployment.

## API Smoke Test Evidence

Pre-fix failed smoke is captured in `runtime_json/staging_app_detail_contradiction_probe_redacted.json`.

Post-fix staging smoke pending.

## Browser Smoke Test Evidence, If Applicable

Pending.

## Screenshots/Evidence Folder Path

`docs/audits/evidence/remediation_sprints/PR-4B_screening-memo-readiness-metadata-hardening_20260613T164500Z/`

## Remaining Risks

Other legacy stored narrative text could still contain unsafe wording outside structured screening readiness metadata. Browser smoke must inspect affected memo/approval panels after deployment.

## Items Not Closed By This PR

No other remediation item is closed by PR-4B.

## Final Closure Verdict

`FSI-007` remains `PARTIALLY FIXED` until PR-4B is merged, deployed to staging, staging `/api/version` matches merged main, API smoke passes, browser smoke passes, and this closure report is completed with runtime proof.
