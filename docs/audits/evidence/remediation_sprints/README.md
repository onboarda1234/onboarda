# Remediation Sprint Evidence Packs

This directory stores evidence packs for RegMind remediation PRs.

## Folder Naming Convention

Use:

`docs/audits/evidence/remediation_sprints/<PR-ID>_<short-name>_<YYYYMMDDTHHMMSSZ>/`

Examples:

- `docs/audits/evidence/remediation_sprints/PR-0_remediation-control-framework_20260613T062347Z/`
- `docs/audits/evidence/remediation_sprints/PR-1_security-client-api-boundary-hardening_20260613T120000Z/`

Use UTC timestamps and the `Z` suffix.

## Standard Pack Contents

Each evidence pack should contain the applicable files below:

- `diagnosis.md`
- `root_cause.md`
- `test_results.md`
- `full_suite_results.md`
- `staging_deploy.md`
- `api_smoke.md`
- `browser_smoke.md`
- `screenshots/`
- `runtime_json/`
- `closure_report.md`

Not every PR needs every file. Documentation-only PRs may use a smaller pack, but product-defect closure requires every applicable evidence category.

## Evidence Rules

- Redact tokens, passwords, cookies, CSRF values, provider secrets, and session material.
- Include exact commands and results for tests.
- Include exact staging `/api/version` output for defect-closing PRs.
- Include browser screenshots when visible UI behavior is part of the issue.
- Include raw runtime JSON for API smoke tests where practical.
- Do not mark an issue closed from branch-only, local-only, stale, or old-staging evidence.
