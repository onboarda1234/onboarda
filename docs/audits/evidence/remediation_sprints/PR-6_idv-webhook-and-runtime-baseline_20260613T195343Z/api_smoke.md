# PR-6 API / Runtime Smoke

Branch-stage staging API smoke: not run against PR-6 because PR-6 is not merged or deployed.

Required after merge and staging deployment:

- Authenticated `/api/version` shows merged main SHA for `git_sha` and `image_tag`.
- Sumsub webhook post-commit renormalization path no longer passes a DB handle.
- Post-commit committed-read invariant remains proven by tests and, where safe, staging smoke.
- Webhook idempotency remains intact.
- Unmatched webhook DLQ path remains intact.
- Errors/logs remain safe and redacted.
- Runtime baseline helper reports backend and worker aligned with merged main SHA.
- FSI-001, FSI-002, FSI-003, FSI-007, FSI-005, and FSI-006 regressions remain passing.

Pre-fix runtime baseline smoke:

```bash
/opt/homebrew/bin/python3.11 arie-backend/scripts/staging_runtime_baseline.py \
  --expected-sha b061c52f147b6fa42398629bb2b5dd2502682f3d \
  --strict
```

Result:

```text
exit 2
backend_image_matches_expected=true
worker_image_matches_expected=false
```

Evidence:

- `runtime_json/staging_runtime_baseline_helper_prefix_redacted.json`
