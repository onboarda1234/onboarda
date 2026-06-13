# PR-5 Root Cause

## FSI-005

The application had no canonical latest-memo selector. Consumers duplicated direct SQL against `compliance_memos` and used different ordering contracts:

- `created_at DESC, id DESC`
- `version DESC, id DESC`
- ad hoc app-id/ref resolution before querying memos

Because these paths were independent, a stale or non-authoritative memo could be displayed, validated, approved, linked to EDD, or exported depending on which consumer was used.

## FSI-006

The backend and UI had a governance contract mismatch:

- Memo approval required documented rationale in some elevated cases, but not as a uniform approval invariant.
- The UI did not render an approval reason input.
- The UI approval request did not include `approval_reason`.
- The validation panel hard-disabled `pass_with_fixes` because the UI lacked reason support.
- The validation issue renderer used "No issues found" whenever no issue list existed, even when `validation_status` was `fail`.

## Corrective Design

- Introduce a backend-only canonical selector in `memo_governance.py`.
- Use deterministic ordering: `COALESCE(version, 0) DESC, created_at DESC, id DESC`.
- Require non-empty `approval_reason` for memo approval.
- Persist and audit the approval reason.
- Include memo-selection metadata and `canonical_memo_id` in API responses.
- Add a consolidated memo-governance panel in the officer UI.
- Add an approval reason textarea and send the reason with memo approval.
- Collapse full memo and diagnostics behind `Full Memo / Diagnostics`.
