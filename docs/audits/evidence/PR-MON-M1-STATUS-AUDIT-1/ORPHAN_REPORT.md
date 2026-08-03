# Orphan Report

An alert is classified Orphaned when a required application/workflow object is
missing, broken, or impossible. Optional relationships are not treated as
required.

| ID | Type | Status | Expected owner | Finding |
|---|---|---|---|---|
| 1 | Sanctions Match | escalated | Screening Review | missing_application_link, missing_screening_case_link |
| 2 | Risk Drift | dismissed | Manual | missing_application_link |
| 54 | manual_pr290_schema_validation | open | Manual | missing_application_link |
| 55 | other | open | Manual | missing_application_link |

No link was repaired. Every orphan remains **Manual review required**.
