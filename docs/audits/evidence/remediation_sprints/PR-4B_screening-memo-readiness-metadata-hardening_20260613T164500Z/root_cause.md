# Root Cause

PR-4 fixed the canonical screening truth summary and future memo generation, but existing `compliance_memos.memo_data` rows can contain serialized legacy screening readiness summaries from before PR-4.

`ApplicationDetailHandler.get()` parses `latest_memo_data` from the stored memo row and returns `latest_memo_data.metadata` to back-office users. That path did not sanitize stale nested memo metadata at read time.

As a result, current application detail could simultaneously show:
- safe current `screening_truth_summary`
- stale unsafe memo metadata with `approval_ready=true` and `approval_blocking=true`

This is an officer-facing API contradiction. It is not safe to solve by rewriting historical memo rows during read. The smallest safe fix is a centralized read-time projection sanitizer for legacy screening readiness blobs.
