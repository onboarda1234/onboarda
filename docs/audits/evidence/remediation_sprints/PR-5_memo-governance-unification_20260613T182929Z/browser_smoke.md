# PR-5 Browser Smoke

Branch-stage browser smoke: not run against staging.

Branch-stage static UI checks passed:

- Approval reason field is present.
- `approveMemo()` sends `approval_reason`.
- UI no longer contains the old "this UI does not capture or submit that reason yet" blocker text.
- Failed validation without issue detail no longer renders "No issues found".
- Full memo and diagnostics are collapsed behind `Full Memo / Diagnostics`.

Required after merge and staging deployment:

- Back-office login as permitted officer/admin.
- Open an application with blocked/stale memo.
- Confirm consolidated memo status panel appears.
- Confirm exact blockers and next required action appear.
- Confirm no contradictory validation/supervisor copy appears.
- Confirm Approve is not primary/actionable when memo is blocked.
- Open full memo and diagnostics sections.
- Open approval-ready memo fixture if available.
- Enter approval reason and submit approval.
- Confirm missing reason fails safely.
- Confirm no console/network errors.
- Client portal regression smoke for safe own application and no internal memo/gate/audit/supervisor leakage.
