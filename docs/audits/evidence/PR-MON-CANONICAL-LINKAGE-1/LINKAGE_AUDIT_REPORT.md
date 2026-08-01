# PR-MON-CANONICAL-LINKAGE-1 — Linkage Audit Report

- Contract: `monitoring_alert_linkage_v1`
- Environment: `staging`
- Deployed SHA: `33f4ce8bbfe880430bd998770ee024672a4cb3be`
- Plan fingerprint: `1208b4424f209f28075ebead9cebbb099fb9d81cd30f0207afb0e4aef859a720`
- Database transaction: `READ ONLY`, `REPEATABLE READ`, completed by `ROLLBACK`
- Collector-side governed-flag evaluation: all 4 `OFF` (not deployed-runtime evidence)
- Apply support: `false`
- Data changes planned: `0`

## Statistics

| Metric | Count |
|---|---:|
| Ambiguous Linkage | 0 |
| Broken Linkage | 1 |
| Data Changes Planned | 0 |
| Duplicate Linkage Alerts | 0 |
| Duplicate Linkage Groups | 0 |
| Linked Correctly | 10 |
| Manual Review Required | 8 |
| Missing Linkage | 7 |
| Not Applicable | 0 |
| Orphaned Alert | 4 |
| Safely Backfillable | 0 |
| Supported | 18 |
| Total Alerts | 22 |
| Unsupported | 4 |

## Alert inventory

| Alert | Stored type | Scope | Classification | Review | Duplicate | Reasons |
|---:|---|---|---|---|---|---|
| 1 | `Sanctions Match` | unsupported | orphaned_alert | out_of_scope | no | missing_application_link |
| 2 | `Risk Drift` | unsupported | orphaned_alert | out_of_scope | no | missing_application_link |
| 54 | `manual_pr290_schema_validation` | unsupported | orphaned_alert | out_of_scope | no | missing_application_link |
| 55 | `other` | unsupported | orphaned_alert | out_of_scope | no | missing_application_link |
| 581 | `adverse_media` | supported | missing_linkage | manual_review_required | no | missing_customer_link |
| 582 | `sanctions` | supported | missing_linkage | manual_review_required | no | missing_customer_link |
| 583 | `sanctions` | supported | missing_linkage | manual_review_required | no | missing_customer_link |
| 584 | `adverse_media` | supported | missing_linkage | manual_review_required | no | missing_customer_link |
| 585 | `adverse_media` | supported | missing_linkage | manual_review_required | no | missing_customer_link |
| 586 | `media` | supported | linked_correctly | no_action | no | — |
| 587 | `media` | supported | linked_correctly | no_action | no | — |
| 591 | `media` | supported | linked_correctly | no_action | no | — |
| 592 | `media` | supported | linked_correctly | no_action | no | — |
| 603 | `media` | supported | missing_linkage | manual_review_required | no | missing_customer_link |
| 606 | `pep` | supported | missing_linkage | manual_review_required | no | missing_customer_link |
| 608 | `document_expiry_missing` | supported | linked_correctly | no_action | no | — |
| 609 | `document_expiry_missing` | supported | linked_correctly | no_action | no | — |
| 610 | `document_expiry_missing` | supported | broken_linkage | manual_review_required | no | document_request_identity_mismatch |
| 611 | `document_expiry_missing` | supported | linked_correctly | no_action | no | — |
| 613 | `pep` | supported | linked_correctly | no_action | no | — |
| 614 | `media` | supported | linked_correctly | no_action | no | — |
| 615 | `media` | supported | linked_correctly | no_action | no | — |

## Safety conclusion

No Monitoring Alert or downstream record was modified. The dry-run contract has no apply path. Rows that cannot be proven by exact stable identifiers remain unchanged and require manual review.
