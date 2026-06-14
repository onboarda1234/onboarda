# PR-5B Generated Memo Before/After Evidence

## Before

- Case shape: LOW risk, pending/non-terminal screening, one verified document, one pending document
- Metadata recommendation: `REVIEW`
- Original recommendation recorded: `APPROVE_WITH_CONDITIONS`
- Compliance decision section: `APPROVE_WITH_CONDITIONS`
- Section count: 12
- Word count: 1757 (~3.5 pages at 500 words/page before PDF styling)
- Pending screening in risk-decreasing factors: 1
- AI explainability word count: 185

Repeated phrase counts:

- `screening_status_phrase`: 7
- `provider_not_terminal_phrase`: 8
- `screening_resolution_required`: 1
- `approve_with_conditions`: 1
- `risk_decreasing_factors`: 4
- `ai_decision_pathway_mentions`: 3

Before runtime JSON:

- `runtime_json/memo_before_pr5b_summary.json`
- `runtime_json/memo_before_pr5b.json`

## After

- Case shape: LOW risk, pending/non-terminal screening, one verified document, one pending document
- Metadata recommendation: `REVIEW`
- Decision label: `SCREENING RESOLUTION REQUIRED`
- Compliance decision section: `REVIEW`
- Section count: 12
- Default section word count: 776 (~1.6 pages at 500 words/page before PDF styling)
- Original verbose section word count retained in appendix: 1756
- Pending screening in risk-decreasing factors: 0
- AI explainability word count: 48
- Appendix sections present: yes
- Raw/test-like officer note in default memo: no
- Sanitized onboarding enhanced review section present: yes
- Validation status: `pass_with_fixes`
- Supervisor status: `CONSISTENT_WITH_WARNINGS`

Repeated phrase counts:

- `screening_status_phrase`: 1
- `provider_not_terminal_phrase`: 1
- `screening_resolution_required`: 2
- `approve_with_conditions`: 0
- `ai_decision_pathway_mentions`: 0

After runtime JSON:

- `runtime_json/memo_after_pr5b_summary.json`
- `runtime_json/memo_after_pr5b.json`

## Corrective Browser-Defect After

The corrective branch updates the PR-5B output and browser renderer contract:

- Formal memo risk sentence uses canonical application risk:
  `LOW risk with score 22/100`.
- Routing/elevation risk, where different, is labelled as diagnostics rather
  than the headline risk rating.
- Memo metadata and section payload expose the same canonical blocker list.
- Back-office decision snapshot reads the canonical blocker list and no longer
  falls back to `Open blockers: None recorded in memo metadata`.
- Validation panel status text is status-aware:
  - `pass_with_fixes` does not render `No issues found`.
  - approval-blocked states do not render clean quality wording.

Corrective local browser evidence:

- `runtime_json/pr5b_corrective_local_browser_smoke.json`
- `screenshots/pr5b_corrective_local_memo_panel.png`
