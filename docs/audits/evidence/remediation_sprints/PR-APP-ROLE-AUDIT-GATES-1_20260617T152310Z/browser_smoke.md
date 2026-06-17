# Browser Smoke

Status: pending post-merge staging deploy.

Required authenticated browser checks after deployment:

- `/api/version.git_sha` and `image_tag` match the merge SHA.
- Application Review loads.
- Reassign modal opens.
- Empty reassignment reason is blocked.
- Valid reassignment reason persists.
- Case remains in Application Review context after reassignment.
- Audit trail shows reassignment reason and before/after assignee.
- Analyst/read-only sensitive config access is blocked or read-only as intended.
- No blocking console errors.
- No failed unexpected network requests.

Implementation-stage UI checks covered by tests:

- `tests/test_backoffice_review_audit.py` verifies the modal marks reassignment reason required, sends `reassignment_reason`, shows current/new assignee labels, and preserves Application Review tab context.
- `tests/test_ex12_client_security.py` verifies reassignment actions remain permission guarded before modal/API execution.
