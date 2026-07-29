# Linkage Report

| ID | Owner | Documents | Evidence | Screening cases | App review context | EDD | Periodic | Change | Finding |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Screening Review | 0 | 0 | 0 | 0 | 0 | 0 | 0 | missing_application_link, missing_screening_case_link |
| 2 | Manual | 0 | 0 | 0 | 0 | 0 | 0 | 0 | missing_application_link |
| 54 | Manual | 0 | 0 | 0 | 0 | 0 | 0 | 0 | missing_application_link |
| 55 | Manual | 0 | 0 | 0 | 0 | 0 | 0 | 0 | missing_application_link |
| 581 | Screening Review | 0 | 0 | 1 | 1 | 0 | 0 | 0 | none |
| 582 | Periodic Review | 0 | 0 | 1 | 1 | 0 | 1 | 0 | none |
| 583 | EDD | 0 | 0 | 1 | 1 | 1 | 0 | 0 | none |
| 584 | EDD | 0 | 0 | 1 | 1 | 1 | 0 | 0 | none |
| 585 | Periodic Review | 0 | 0 | 1 | 1 | 0 | 1 | 0 | none |
| 586 | Screening Review | 0 | 104 | 1 | 2 | 0 | 0 | 0 | none |
| 587 | Screening Review | 0 | 101 | 1 | 2 | 0 | 0 | 0 | none |
| 591 | Screening Review | 0 | 101 | 1 | 1 | 0 | 0 | 0 | none |
| 592 | Screening Review | 0 | 104 | 1 | 1 | 0 | 0 | 0 | none |
| 603 | Screening Review | 0 | 5 | 1 | 0 | 0 | 0 | 0 | none |
| 606 | Screening Review | 0 | 30 | 1 | 0 | 0 | 0 | 0 | none |
| 608 | Documents | 1 | 0 | 0 | 0 | 0 | 0 | 0 | none |
| 609 | Documents | 1 | 0 | 0 | 0 | 0 | 0 | 0 | none |
| 610 | Documents | 1 | 0 | 0 | 0 | 0 | 0 | 0 | none |
| 611 | Documents | 1 | 0 | 0 | 0 | 0 | 0 | 0 | none |

Cross-application links are classified as impossible. Referenced IDs that do not
resolve are classified as broken. Screening Review rows joined only by
application are labelled non-causal context and are never used as an alert link
or migration-routing signal. Change Management is linked only through the
schema-valid `change_alerts.source_reference = monitoring_alert:<id>` bridge;
`change_requests.source_alert_id` is never compared directly with a Monitoring
Alert ID. No linkage was added or changed.
