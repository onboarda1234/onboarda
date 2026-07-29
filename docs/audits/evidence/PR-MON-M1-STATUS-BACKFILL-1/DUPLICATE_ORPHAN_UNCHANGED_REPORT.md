# Duplicate and orphan unchanged report

PR #900 found no exact duplicate alert group. This backfill does not merge,
dismiss, delete, or otherwise reconcile any similar or repeated-source row.

## Orphans

Alerts `1`, `2`, `54`, and `55` remain unchanged and are excluded as manual
review.

## Repeated source reference

Alerts `2` and `55` share a generic source-reference identity. This remains a
review signal only; it is not treated as proof of duplication.

## Similar unresolved groups

The PR #900 parallel groups `586/587`, `591/592`, `608/609`, and `610/611`
remain distinct. The first two groups are already canonical `open`; the latter
two remain manual review. No source identity or workflow ownership is merged.

## Count guard

The planner requires exactly 19 alerts before apply and the reconciler requires
exactly 19 afterward. No delete path exists in the operator module.
