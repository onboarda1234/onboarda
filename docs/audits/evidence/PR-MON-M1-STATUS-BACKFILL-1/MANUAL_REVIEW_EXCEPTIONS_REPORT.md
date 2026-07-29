# Manual-review exceptions report

These 14 rows are explicitly excluded from the automatic mutation set. The
planner verifies their audited identity and fails closed if they drift, but it
never updates them.

| ID | Current | Audit recommendation | Reason |
|---:|---|---|---|
| 1 | `escalated` | — | Orphaned: missing application and screening-case linkage |
| 2 | `dismissed` | `dismissed` | Orphaned and terminal without `resolved_at` |
| 54 | `open` | `open` | Orphaned: missing application linkage |
| 55 | `open` | `open` | Orphaned; repeated generic source reference is not proof of duplication |
| 581 | `dismissed` | `dismissed` | Terminal without `resolved_at` |
| 582 | `resolved` | `resolved` | Terminal without `resolved_at` |
| 584 | `open` | `routed_to_edd` | Active status conflicts with EDD owner linkage |
| 585 | `dismissed` | `dismissed` | Terminal without `resolved_at` |
| 603 | `open` | `open` | Test-like non-fixture provenance requires confirmation |
| 606 | `open` | `open` | Test-like non-fixture provenance requires confirmation |
| 608 | `open` | `open` | Test-like non-fixture provenance requires confirmation |
| 609 | `open` | `open` | Test-like non-fixture provenance requires confirmation |
| 610 | `open` | `open` | Test-like non-fixture provenance requires confirmation |
| 611 | `open` | `open` | Test-like non-fixture provenance requires confirmation |

No inference is made from downstream-object existence, officer notes, or
resolution evidence. These rows require separate human decisions.
