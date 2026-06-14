# PR-DOC1 API Smoke

## Branch-Stage API Evidence

Local targeted API tests prove:

- Pending required documents block KYC submit.
- Failed and skipped required documents block reliance.
- Missing `verification_results`, missing `verified_at`, and missing Agent 1 execution proof block.
- Stale verification blocks.
- Verified required documents with Agent 1 proof allow progression.
- Governed admin/SCO manual acceptance allows progression.
- Manual acceptance without reason or role is rejected.
- Memo generation and memo approval fail closed on document evidence blockers.
- Final application approval returns document-specific blockers.

See `test_results.md` and `full_suite_results.md`.

## Staging API Smoke

Not run at branch stage. Required after merge and staging deployment:

- Pending document blocks KYC/memo/approval.
- Failed document blocks KYC/memo/approval.
- Skipped/manual-review document blocks unless properly manual accepted.
- Missing Agent 1 execution proof blocks.
- Verified document unblocks.
- Governed manual acceptance unblocks if used.
- Memo includes document evidence snapshot and does not overstate pending evidence.
- Approval gate returns document-specific blocker reasons.
- Existing FSI/CA/memo regressions remain passing.

Verdict: DOC-001 remains below `CLOSED` until staging API smoke passes.

