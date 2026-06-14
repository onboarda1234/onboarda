# Root Cause

## Root Cause Summary

The memo builder generated a long regulator-style narrative first, then later governance gates corrected the metadata recommendation. That meant the final metadata could be `REVIEW` while the already-built formal `compliance_decision` section still carried an approval-style decision.

Contributing causes:

- Recommendation binding was applied to metadata but not consistently to the officer-facing decision section.
- The default memo used all evidence sections as the working memo rather than separating decision paper from appendix evidence.
- Screening status helper text was reused verbatim in executive summary, financial-crime risk, screening results, AI explainability, red flags/mitigants, and decision sections.
- Pending/non-terminal screening mitigation text was built before final approval-blocking semantics were applied.
- AI explainability included agent-pathway and future monitoring-agent language in the default officer memo.
- Officer disposition notes were collapsed directly into screening prose without a formal-note sanitization step.

## Fix Strategy

Implemented a final decision-paper cleanup pass inside `memo_handler.py`:

- Preserves the original verbose sections in `appendix_sections`.
- Rewrites default sections into concise decision-paper content while keeping the existing section keys required by validation.
- Aligns `sections.compliance_decision.decision` with the final authoritative metadata recommendation.
- Classifies pending screening as blocker/dependency, not risk-reducing evidence.
- Keeps legacy safety phrases required by existing screening truthfulness tests.
- Sanitizes rough/test-like officer notes before formal memo rendering.
- Re-runs memo supervisor after condensation so displayed supervisor state reflects the same officer-facing memo.

Implemented a lightweight PDF appendix index in `pdf_generator.py`:

- Keeps the PDF decision-first.
- Lists retained appendix evidence without dumping the old long-form memo into the default PDF.

## Corrective Browser Defect Root Cause

The post-merge browser defects had two additional causes:

1. The decision-paper cleanup used `aggregated_risk` / routing risk for visible
   formal narrative while the application risk chip used canonical application
   risk. For cases where routing/EDD diagnostics elevate handling but the
   stored risk score remains low, this produced text such as `HIGH risk with
   score 25/100`.

2. The back-office memo renderer calculated the visible decision snapshot
   blockers from `metadata.blocked` and `metadata.is_stale` only. It did not
   aggregate canonical PR-5B blockers from `metadata.primary_blockers`,
   `memo_output_profile.primary_blockers`, `executive_summary.decision_summary`,
   `screening_results.approval_blocked_reasons`, or
   `red_flags_and_mitigants.approval_blockers`.

3. The validation panel fell through to clean wording whenever the issue list
   was empty. It did not first check non-clean statuses such as
   `pass_with_fixes`, failed/blocked statuses, or approval blockers.

Corrective fix strategy:

- Bind formal memo risk wording to canonical `risk_display` / application risk.
- Keep routing/elevation risk as separate diagnostics, not the headline risk
  rating.
- Add a central back-office `memoCanonicalBlockers` helper and use it for the
  governance panel, memo approval blocker list, and decision snapshot.
- Make validation-panel empty-state text status-aware and approval-blocker-aware.
