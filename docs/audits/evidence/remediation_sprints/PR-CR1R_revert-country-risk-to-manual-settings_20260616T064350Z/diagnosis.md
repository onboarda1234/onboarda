# PR-CR1R Diagnosis

Original observed deployed PR-CR1/#503 SHA: `0071f09a3bc1eb805f99f6c22c2674913868c78e`

Current `origin/main` SHA used for final PR-CR1R rebase/validation: `5d30ab0b4af83b8d6272fda1840e25e985c92037`

## Finding

PR #502 made the PR-CR1 imported country-risk/FATF snapshot operational by wiring it into:

- `arie-backend/rule_engine.py` country scoring and floor/elevation helpers
- `arie-backend/memo_handler.py` jurisdiction evidence
- `/api/config/country-risk`
- `arie-backoffice.html` Risk Scoring Model country-risk section
- risk recomputation config versioning

PR #503 fixed UI field-name mismatch only. It did not address data quality issues in the imported snapshot.

## Observed Risk

The imported snapshot was not reliable enough for pilot operation:

- incomplete active UI groupings, including manually configured score-2 countries such as Mauritius not appearing in the active country-risk UI
- duplicated countries across active UI sections, including Syria-style overlaps
- active UI showed imported snapshot/FATF categories as operational
- scoring/memo evidence could be driven by the imported snapshot rather than the existing manual Risk Scoring Model settings

## Root Cause

PR-CR1 switched the source of truth before the governed snapshot was sufficiently reconciled with manual pilot settings and before maker/checker/client-impact controls were implemented. The UI also converted snapshot rows into grouped operational lists, losing medium/standard countries and allowing duplicate display across groups.

## Scope Guard

This diagnosis covers only PR-CR1R country-risk rollback. PR-CR2/CR3, CA, DOC, SAR/STR, PR-7, Agent 1, portal, and unrelated remediation were not started.
