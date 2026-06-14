# Diagnosis

## Source of Truth

- Branch: `codex/pr5b-memo-concision-decision-paper-cleanup`
- Base `origin/main` SHA: `1d2bb4fb6df31a2474d8e605f368442132e36257`
- PR-6 status at PR-5B start: PR #478 and PR #481 were merged; this branch starts from main containing PR-6D (`1d2bb4fb6df31a2474d8e605f368442132e36257`).

## Representative Case Diagnosed

Local deterministic memo generation was run against a staging-safe synthetic case:

- LOW risk entity
- Pending / non-terminal screening
- One verified document
- One outstanding document
- Messy officer note text in screening review evidence

No provider calls, staging mutations, or live workflows were triggered.

## Current Defects Confirmed On Main

1. The default memo was too long for a simple blocked LOW-risk case.
   - Before default section word count: 1757 words, roughly 3.5 pages before PDF styling.
   - A comparable PDF had previously rendered as a 6-page officer memo.

2. The memo had contradictory recommendation language.
   - Metadata recommendation: `REVIEW`
   - Metadata original recommendation: `APPROVE_WITH_CONDITIONS`
   - Formal compliance decision section: `APPROVE_WITH_CONDITIONS`
   - Screening was approval-blocking.

3. Pending screening was incorrectly presented as risk-reducing evidence.
   - Pending screening appeared in `ai_explainability.risk_decreasing_factors`.

4. Screening pending / terminal-provider wording was repeated across multiple sections.
   - `screening_status_phrase`: 7
   - `provider_not_terminal_phrase`: 8

5. AI explainability dominated the default memo.
   - AI explainability word count: 185
   - Decision-pathway / future-agent wording appeared in default memo content.

6. Rough officer-note text could enter formal memo prose.
   - Test-like officer note text was included in the representative input.

Raw before evidence:

- `runtime_json/memo_before_pr5b_summary.json`
- `runtime_json/memo_before_pr5b.json`

## Corrective Browser Re-Diagnosis

After PR #482 was merged, deployed, and validated by API/PDF smoke, focused
staging browser smoke on the back-office memo view found three real
memo-section contradictions:

1. Risk contradiction.
   - The application risk chip showed `LOW`.
   - The memo text said the entity was assessed as `HIGH risk with score 25/100`.

2. Blocker contradiction.
   - The memo decision snapshot showed `Open blockers: None recorded in memo metadata`.
   - Screening and document blockers were present in memo metadata and approval
     gate context.

3. Validation contradiction.
   - The validation panel could render clean wording (`No issues found`) when
     the memo status was not a clean pass or approval remained blocked.

Corrective evidence:

- Screenshot: `screenshots/pr5b_backoffice_memo_summary.png`
- Browser report: `runtime_json/staging_pr5b_browser_smoke_report_redacted.json`
- Corrective branch local browser proof: `runtime_json/pr5b_corrective_local_browser_smoke.json`
- Corrective local screenshot: `screenshots/pr5b_corrective_local_memo_panel.png`
