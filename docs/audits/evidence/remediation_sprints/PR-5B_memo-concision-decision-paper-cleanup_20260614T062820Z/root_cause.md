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
