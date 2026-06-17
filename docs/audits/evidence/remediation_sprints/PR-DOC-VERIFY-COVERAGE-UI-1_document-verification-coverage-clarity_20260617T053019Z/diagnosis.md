# Diagnosis

- Existing Application Review document rows were still too tall and system-centric.
- Repetitive system-issue wording appeared across row summary, details metadata, issue blocks, and raw check payload rendering.
- Missing-document upload actions were not slot-aware from the back-office KYC review surface.
- The application action bar still exposed too many secondary actions directly.
- The unmatched Sumsub webhook notice was surfacing irrelevant operational noise inside ordinary case review.

# Constraints

- Preserve RegMind onboarding taxonomy:
  - `A — Corporate Entity Documents`
  - `B — Directors & UBO Identity Documents`
  - existing enhanced/other/history sections
- Do not redesign the portal.
- Do not change verification logic or weaken approval/document gates.
