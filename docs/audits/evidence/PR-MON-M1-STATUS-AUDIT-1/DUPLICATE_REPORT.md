# Duplicate Report

Exact unresolved duplicates require the same application, owner workflow,
normalised alert type, and source identity. Distinct provider case/alert
identifiers and distinct document IDs are not treated as duplicates merely
because they share an application and type.

Detected duplicate groups: **0**.

No exact duplicate group was found. Similar parallel provider alerts retain distinct case identities.

Future reconciliation should remain a separate, reviewed migration. This audit
does not merge, dismiss, or delete any alert.

## Duplicate source references

Detected repeated source-reference identities: **1**.

| Alert IDs | Source identity HMAC-SHA-256 |
|---|---|
| 2, 55 | 7bb2abd4168c6782bb6bb5a8ff3fea5e7ea741ce84dc5c66f2a798d2a192435d |

A repeated generic manual label is a review signal, not proof that the underlying
events are the same.

## Duplicate workflow ownership

Alerts linked to more than one explicit owner workflow: **0**.

No alert has duplicate explicit workflow ownership.

## Similar unresolved alerts

| Alert IDs | Application | Owner | Type | Assessment |
|---|---|---|---|---|
| 608, 609 | 4a247758771c4220 | Documents | document_expiry_missing | parallel unresolved alerts with distinct source identities |
| 610, 611 | 85db5081e3454dd8 | Documents | document_expiry_missing | parallel unresolved alerts with distinct source identities |
| 586, 587 | f1xedqa000000006 | Screening Review | media | parallel unresolved alerts with distinct source identities |
| 591, 592 | f1xedqa000000007 | Screening Review | media | parallel unresolved alerts with distinct source identities |

These groups were not classified as duplicates when their provider case,
provider alert, or document identities differ. Future reconciliation should
confirm that each distinct source represents a distinct monitored subject or
document before any merge is proposed.
