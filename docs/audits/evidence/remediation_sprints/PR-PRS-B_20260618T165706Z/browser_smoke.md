# PR-PRS-B Browser Smoke

- URL: `https://staging.regmind.co/backoffice`
- Login: staging QA account (`sco`)
- Browser: Playwright Chromium, authenticated through the real back-office login form

## Screenshots

- Agent 1 Runs for Periodic Review Evidence: `docs/audits/evidence/remediation_sprints/PR-PRS-B_20260618T165706Z/screenshots/staging_01_agent1_runs.png`
- Accepted Does Not Equal Verified: `docs/audits/evidence/remediation_sprints/PR-PRS-B_20260618T165706Z/screenshots/staging_02_accepted_not_verified_blocks.png`
- Verified Evidence Satisfies Completion: `docs/audits/evidence/remediation_sprints/PR-PRS-B_20260618T165706Z/screenshots/staging_03_verified_satisfies.png`
- Senior Manual Exception: `docs/audits/evidence/remediation_sprints/PR-PRS-B_20260618T165706Z/screenshots/staging_04_senior_manual_exception.png`
- Stale Evidence Re-Blocks Completion: `docs/audits/evidence/remediation_sprints/PR-PRS-B_20260618T165706Z/screenshots/staging_05_stale_reblocks.png`
- Onboarding/EDD Upload Regression: `docs/audits/evidence/remediation_sprints/PR-PRS-B_20260618T165706Z/screenshots/staging_06_onboarding_edd_regression.png`

## Confirmations

- Agent 1 ran for mapped periodic-review evidence and persisted checks/timestamp.
- Plain officer acceptance of skipped/unverified evidence did not satisfy completion.
- Verified evidence satisfied periodic-review completion.
- Senior manual exception required admin/SCO acceptance with a comment; CO acceptance was denied.
- Superseded/stale evidence re-blocked completion.
- Ordinary onboarding/EDD enhanced-requirement upload still verified normally.
