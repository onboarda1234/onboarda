# PR-CA4B API Smoke

Status: pending merged-main staging validation.

Required after deployment:

- For `ARF-2026-900164` or equivalent controlled fixture:
  - queue/detail current truth and memo metadata agree on adverse media,
  - if current truth has adverse media, memo does not say `coverage=none` / `has_hit=false`,
  - if current truth changed after memo generation, memo is stale or requires regeneration,
  - `current_risk_count` and `current_unresolved_risk_count` are reflected correctly in memo context,
  - unresolved adverse media blocks memo/approval reliance.
- PR-CA1 / PR-CA2 / PR-CA3 regressions remain passing.
- PR-CA4 UI/rollup regressions remain passing.

Branch-stage equivalent proof is covered by local API and backend tests in `test_results.md` and `full_suite_results.md`.
