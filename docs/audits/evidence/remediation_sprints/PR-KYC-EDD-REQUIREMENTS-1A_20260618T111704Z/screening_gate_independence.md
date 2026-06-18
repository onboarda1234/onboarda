# PR-KYC-EDD-REQUIREMENTS-1A Screening Gate Independence

## Decision

The v5 alignment removes screening-related rows from active Enhanced Requirement generation and default seeding.

Removed from active generation:

- `screening_disposition`
- `material_screening_senior_review`
- `false_positive_rationale`
- `adverse_media_pep_sanctions_assessment`
- `client_clarification_screening`

This is safe because screening disposition, screening truth, material screening coverage, and second-review controls are independently enforced outside Enhanced Requirements.

## Independent Enforcement Evidence

### Approval truth gate

- `arie-backend/security_hardening.py`
  - `ApprovalGateValidator.validate_approval(...)` loads screening reviews independently from `screening_reviews`.
  - It calls `screening_second_review_pending_summary(...)` before approval.
  - It builds canonical screening truth through `build_screening_truth_summary(...)`.
  - If screening truth is approval-blocking, approval is denied independently of any Enhanced Requirement row.

### Second-review / four-eyes gate

- `arie-backend/security_hardening.py`
  - `screening_second_review_pending_summary(...)` returns fail-closed blockers when a screening review requires a second reviewer and the second review is missing, performed by the same user, or performed by a role other than SCO/admin.

- `arie-backend/server.py`
  - `_screening_second_review_summary_if_blocked(...)` wraps the independent gate for approval paths.
  - Application approval calls this gate before memo freshness and again after `ApprovalGateValidator.validate_approval(...)` when needed.
  - `_write_screening_second_review_block_response(...)` returns `screening_second_review_pending` without relying on Enhanced Requirement rows.

### Regression tests

Covered by `arie-backend/tests/test_approval_gate.py`:

- `test_validate_approval_blocks_stale_screening_truth`
- `test_final_approval_blocks_edd_route_when_new_trigger_not_covered`
- `test_screening_second_review_pending_blocks_approval`
- `test_same_user_first_and_second_screening_review_blocks_approval`
- `test_co_second_reviewer_does_not_satisfy_screening_four_eyes_gate`
- `test_sco_second_reviewer_satisfies_screening_four_eyes_gate`
- `test_completed_match_false_positive_clearance_allows_screening_gate`

## Result

Screening rows can be removed from active Enhanced Requirement generation for new applications while preserving independent approval blockers for screening truth, material screening trigger coverage, and second-review controls.
