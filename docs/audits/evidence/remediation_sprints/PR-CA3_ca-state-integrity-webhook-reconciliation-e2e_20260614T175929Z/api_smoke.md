# PR-CA3 API Smoke

Status: pending for staging.

Required staging API/runtime smoke must prove:

- Canonical screening truth has no impossible clear/unresolved combinations in tested paths.
- Queue/detail/gate agree on current CA state for safe test cases.
- Provider failure and stale states block approval reliance.
- Duplicate webhook/replay fixture does not duplicate hits/evidence.
- Reconciliation job or equivalent retry recovers missed webhook/detail-fetch work.
- Safe no-hit/hit/failure/stale/rescreen E2E paths pass.
- PR-CA1 provider source truth remains passing.
- PR-CA2 evidence/audit chain remains passing.
- No tokens, secrets, webhook signatures, or provider credentials appear in outputs.

No staging API smoke has been claimed before merge/deploy validation.
