# PR-PRS-A Browser Smoke

- URL: `http://127.0.0.1:10000/backoffice`
- Login: `raj.patel@onboarda.com` (Senior Compliance Officer)
- Browser: Playwright Chromium, authenticated through the real back-office login form

## Screenshots

- Default Queue = Actionable Only: `/Users/Aisha/CodexWork/onboarda-pr-prs-a/docs/audits/evidence/remediation_sprints/PR-PRS-A_20260618T115652Z/screenshots/01_default_queue_actionable_only.png`
- Completion Creates Anchored Next Cycle: `/Users/Aisha/CodexWork/onboarda-pr-prs-a/docs/audits/evidence/remediation_sprints/PR-PRS-A_20260618T115652Z/screenshots/02_next_cycle_anniversary_anchor.png`
- Completed = Frozen: `/Users/Aisha/CodexWork/onboarda-pr-prs-a/docs/audits/evidence/remediation_sprints/PR-PRS-A_20260618T115652Z/screenshots/03_completed_review_frozen.png`
- Legacy Decision Uses Canonical Gates: `/Users/Aisha/CodexWork/onboarda-pr-prs-a/docs/audits/evidence/remediation_sprints/PR-PRS-A_20260618T115652Z/screenshots/04_legacy_decision_canonical_gates.png`
- EDD Escalation Waits, Then Completes: `/Users/Aisha/CodexWork/onboarda-pr-prs-a/docs/audits/evidence/remediation_sprints/PR-PRS-A_20260618T115652Z/screenshots/05_edd_awaiting_feedback_completion.png`

## Confirmations

- Default queue excludes completed/cancelled; explicit completed filter includes completed.
- Completion creates exactly one next pending cycle anchored to the onboarding anniversary; replay is blocked.
- Completed review mutators return HTTP 409.
- Legacy decision endpoint returns 409 with blocking_items when blocked and completes clean reviews canonically without writing decision.
- EDD-required outcome keeps the review in awaiting_edd while EDD is open; EDD approval auto-completes the review and schedules the next cycle.
