# Closure Report

## PR Name

PR-CR1 - Country Risk Source Governance and FATF Snapshot

## Base

- Base branch: `origin/main`
- Base SHA: `07c992d7716183226d53f70bf0d01bf7e87da874`
- Branch: `codex/pr-cr1-country-risk-source-governance`

## Diagnosis Summary

Country risk was spread across hardcoded backend lists, JSON config, `risk_config` JSON, memo constants, and front-end static arrays. FATF status was not source-backed per country and stale FATF-style entries could affect scoring/elevation behavior.

## Root Cause

Country risk was modeled as mutable/static scoring configuration rather than a governed compliance policy with versioned source evidence.

## Implementation Summary

- Added canonical country-risk snapshot tables.
- Added source-backed seed snapshot for FATF February 2026 public statements plus internal pilot policy classifications.
- Added country-level checksums, snapshot version/checksum, source URL, publication date, effective date, imported/last-checked metadata, and freshness checks.
- Wired risk scoring, elevation/floor rules, risk recomputation versioning, memo jurisdiction evidence, API, and Risk Model UI to canonical lookup.
- Preserved legacy maps as fallback only.
- Unknown countries fail safe to MEDIUM with warning.

## Tests

See `test_results.md`.

Current local validation:
- Targeted PR-CR1 tests: `11 passed`.
- Relevant risk suite: `245 passed`.
- Full backend suite: `5420 passed, 17 skipped`.

## Staging Deploy Evidence

Pending merged-main staging deployment.

## `/api/version`

Pending merged-main staging deployment.

## API Smoke

Pending merged-main staging deployment. Local API coverage passed; see `api_smoke.md`.

## Browser Smoke

Pending merged-main staging deployment. Static UI coverage passed; see `browser_smoke.md`.

## Items Not Closed By This PR

- PR-CR2 country-risk maker/checker approval and full audit workflow.
- PR-CR3 approved-client impact analysis, rescore queue, and alerts.
- No CA, PR-PROV1, DOC, SAR/STR, PR-7, or unrelated remediation item is marked closed by this PR.

## Final Closure Verdict

PR-CR1 remains open until merged to main, deployed to staging, staging `/api/version` matches the merged main SHA/image tag, API smoke passes, browser smoke passes, and final runtime evidence is recorded.
