# Migration Readiness Report

Migration-ready means: canonical current status, no duplicate/orphan/linkage
finding, no unresolved fixture-provenance concern, complete terminal resolution
timestamp where applicable, and an evidence-supported future status.

| ID | Current | Future | Readiness | Finding |
|---|---|---|---|---|
| 1 | escalated | — | Manual review required | missing_application_link, missing_screening_case_link |
| 2 | dismissed | dismissed | Manual review required | missing_application_link, terminal_status_without_resolved_at |
| 54 | open | open | Manual review required | missing_application_link |
| 55 | open | open | Manual review required | missing_application_link |
| 581 | dismissed | dismissed | Manual review required | terminal_status_without_resolved_at |
| 582 | resolved | resolved | Manual review required | terminal_status_without_resolved_at |
| 583 | in_review | routed_to_edd | Candidate for future migration | none |
| 584 | open | routed_to_edd | Manual review required | active_status_conflicts_with_edd_owner_link |
| 585 | dismissed | dismissed | Manual review required | terminal_status_without_resolved_at |
| 586 | open | open | Ready | none |
| 587 | open | open | Ready | none |
| 591 | open | open | Ready | none |
| 592 | open | open | Ready | none |
| 603 | open | open | Manual review required | test_like_non_fixture_requires_confirmation |
| 606 | open | open | Manual review required | test_like_non_fixture_requires_confirmation |
| 608 | open | open | Manual review required | test_like_non_fixture_requires_confirmation |
| 609 | open | open | Manual review required | test_like_non_fixture_requires_confirmation |
| 610 | open | open | Manual review required | test_like_non_fixture_requires_confirmation |
| 611 | open | open | Manual review required | test_like_non_fixture_requires_confirmation |

This report is advisory. It performs no backfill and must not be used as an
instruction to mutate the database without founder approval for a later PR.
