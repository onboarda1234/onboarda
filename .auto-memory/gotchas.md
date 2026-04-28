# Gotchas

## Upload Latency Remediation

- `docs/IMPLEMENTATION_PLAYBOOK.md` existed locally before PR0 but was not present on GitHub `main`; GitHub `main` remains the source of truth.
- Backend upload limit is already 10 MB on GitHub `main`.
- Back office client-side size check still references 25 MB before PR5.
- Portal and back office currently chain upload and verification.
- CloudWatch Logs Insights rejects `| sort bin(5m) desc` after a `stats ... by bin(5m)` aggregation. Alias the bin first: `by bin(5m) as window, ... | sort window desc`.
- `FF_POLLING_SLOW` is backend-only under the current flag-exposure contract. PR4 must either keep the polling slowdown server-driven or explicitly lock a new client-exposure decision and test before frontend code reads that flag.
- PR3 telemetry logs only upload/verify request-boundary duration. It does not yet split validation, S3, DB, or verifier phase timings.
