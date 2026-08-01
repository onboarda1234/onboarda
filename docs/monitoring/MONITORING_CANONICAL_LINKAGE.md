# Monitoring Canonical Linkage

Version: `monitoring_alert_linkage_v1`

Originating change: `PR-MON-CANONICAL-LINKAGE-1`

## Objective

This contract gives Monitoring a read-only, fail-closed view of the
authoritative workflow record for the two alert families currently approved:

- document expiry;
- screening hit.

Monitoring remains an orchestration and visibility surface. KYC & Documents
owns document records and decisions. Screening Review owns screening evidence,
disposition, and four-eyes decisions.

This version does not add a write path, persist linkage projections, backfill
an alert, or support EDD, Periodic Review, Change Management, transaction
monitoring, document-renewal automation, refreshed-document verification,
screening-change automation, or automatic resolution.

## Exact type scope

Stored alert types are compared exactly and case-sensitively. Aliases, case
folding, substring matching, and semantic matching are prohibited.

| Linkage contract | Exact stored alert types | Owner module |
|---|---|---|
| `document_expiry` | `document_expired`, `document_expiring_soon`, `document_expiry_missing` | `kyc_documents` |
| `screening_hit` | `sanctions`, `watchlist`, `pep`, `media`, `adverse_media` | `screening_review` |

Every other value returns `unsupported_alert_type` and remains out of scope.

## Common contract

Every successful response contains:

- contract version and Monitoring Alert ID;
- exact application, customer, and entity IDs;
- `read_only: true`;
- `mutation_controls: false`;
- owner module and truthful owner-state projection;
- allowlisted navigation metadata;
- provenance describing the exact linkage strategy.

The application must exist, its stored ID must equal the alert's application
ID, and its customer must resolve to an existing canonical client row. A
missing, broken, cross-application, or ambiguous identity fails closed. Public
errors contain controlled codes and reasons, not raw provider payloads, names,
credentials, SQL, or stack traces.

## Document-expiry contract

The alert must store `source_reference=document:<document-id>`, where the
identifier matches the restricted canonical-ID grammar. JSON references,
names, document types, aliases, and approximate matches are rejected.

The resolver proves:

1. the trigger document exists and belongs to the alert application;
2. a person-scoped document points to one exact canonical party row in that
   application, or the document is unambiguously entity-scoped;
3. the trigger document has a stable slot key;
4. exactly one current document exists in that application and slot;
5. every version in the supersession chain preserves application, person,
   person type, document type, and slot;
6. any active document request is uniquely linked to the same alert,
   application, trigger document, and current document.

The response distinguishes the trigger (expired) version from the current
replacement version and includes document type and expiry dates. A broken
chain, multiple current documents, multiple active requests, or owner drift is
never guessed.

### Document owner-state projection

| Projection | Authoritative basis |
|---|---|
| `expired` | Current trigger document for an exact `document_expired` alert |
| `request_not_started` | No active exact document request |
| `awaiting_client` | Exact request is `requested` or `rejected` |
| `replacement_received` | Exact request is `uploaded`, or the current replacement is pending verification |
| `verifying` | Exact request is `under_review`, or current replacement verification is in progress |
| `officer_review_required` | Canonical document verification/review requires officer action |
| `verified` | Current replacement is verified and accepted |
| `unavailable` | Owner state cannot be proven |

These values are read-time projections only. Monitoring cannot request,
upload, verify, accept, reject, or waive a document through this contract.

### Protected legacy document-write limitation

The repository contains pre-existing, protected Monitoring document-refresh
APIs that can request, upload, replace, accept, reject, or waive documents.
They predate `monitoring_alert_linkage_v1` and remain unchanged in this PR so
the protected baseline is not silently redefined.

Those legacy APIs are not part of this linkage contract: the resolver and
linkage endpoint do not call, expose, extend, or depend on them, and the new
Monitoring owner card renders no control that invokes them. Their migration
to owner-module entry points and subsequent removal from Monitoring is
scheduled for the Document Renewal Automation release, with its own migration,
protected-baseline, and approval gates. This PR performs no API decommissioning.

## Screening-hit contract

The alert must provide exact, mutually agreeing provider and case identifiers
in both its canonical columns and structured source reference. The structured
reference must also contain one stable subject contract:

- entity: `kind=entity`, `scope=entity`, no person key; or
- person: an approved person kind, `scope=person`, and exact `person_key`.

For a person, the stable key must resolve to exactly one same-application
canonical party row. For an entity, the application ID is the stable subject
ID. The resolved subject name is used only to dereference the legacy
subject-level Screening Review roll-up after stable identity is proven; it is
never accepted as the alert-to-subject or alert-to-case linkage. A duplicate
name in the applicable owner table makes the roll-up ambiguous and fails
closed.

The provider case is proven by exact same-application Monitoring evidence or
an exact normalized snapshot. Evidence rows must agree on application,
provider, and case. A referenced normalized snapshot must be successful,
belong to the same customer/application/provider and stable subject, and carry
the exact provider case and alert identifiers. Historical and current
snapshots are returned separately.

Screening Review has no stable alert foreign key in the current schema.
Therefore a review row can influence the owner-state projection only when the
append-only Screening Review audit also corroborates the exact provider,
provider case, provider alert reference where present, subject, disposition,
and review timestamp. Without that corroboration the projection is
`unavailable`; name-only state projection is prohibited.

### Screening owner-state projection

| Projection | Authoritative basis |
|---|---|
| `review_required` | Exact current case evidence exists and no review decision is projected |
| `pending_second_review` | Exact audited review requires four eyes and the second decision is incomplete |
| `escalated` | Exact audited Screening Review disposition is escalated |
| `resolved_in_screening_review` | Exact audited false-positive clearance is complete, including four-eyes controls where required |
| `stale` | Exact evidence is newer than the corroborated review |
| `unavailable` | Evidence failed, is incomplete, or the review/case/state cannot be proven |

Pending, failed, stale, unaudited, or incomplete four-eyes evidence never
projects a clear or resolved state. Monitoring cannot clear, disposition,
escalate, or provide a second review through this contract.

## Navigation contract

Navigation uses existing Application Review routing and an allowlisted tuple;
no persisted or free-form browser URL is accepted.

Document targets include application, customer/entity, canonical document,
document version, and person ID/type where applicable. The owner view must
contain exactly one matching document ID and version before focus occurs.

Screening targets include application, customer/entity, stable subject ID,
subject person key/type, and provider case. The back office re-fetches the
authoritative application, proves the same stable subject again, and then
opens Screening Review. The display name is derived only after this proof.

No Monitoring decision control is rendered for an alert in either exact type
family, including when canonical linkage is unavailable. Assignment of the
Monitoring signal remains separate from downstream decision ownership.

## API contract

`GET /api/monitoring/alerts/{numeric-alert-id}/linkage`

- requires an authenticated `admin`, `sco`, or `co` role;
- is GET-only; unsupported methods receive the framework's controlled 405;
- returns 404 for a missing alert, 422 for an unsupported type, and controlled
  409-class linkage errors for missing, broken, cross-boundary, or ambiguous
  linkage;
- returns an authoritative success envelope only after all exact checks pass.

The existing authenticated alert-detail GET embeds the same linkage envelope.
Unexpected resolution errors degrade to `manual_review_required` in the detail
view and to a generic controlled 500 on the dedicated endpoint.

## Runtime audit and reconciliation policy

The versioned dry-run planner is read-only and deterministic. It reports
scope, linkage classification, review disposition, duplicate membership,
proposed read-time fields, and whether a data change would be required.
Duplicate detection is a separate dimension and uses exact canonical keys.
For screening alerts that identity includes the exact provider case, stable
subject identity, and provider alert/reference. Distinct provider alerts
within the same subject and case are therefore not classified as duplicate
linkage. When no provider alert/reference is supplied, the planner does not
infer duplicate identity from the case and subject alone.

The staging collector:

- opens one PostgreSQL `REPEATABLE READ`, `READ ONLY` transaction;
- permits only `SELECT`, `WITH`, and `SHOW` statements;
- fingerprints all source relations used by the resolvers before and after
  planning, including document-verification `agent_executions` evidence and
  the active reviewer-role records used for four-eyes corroboration;
- builds the plan twice and requires identical deterministic output;
- explicitly rolls back and has no commit or apply path;
- sanitizes generated evidence.

If future reconciliation requires stored alert changes, it must be a separate
approved migration with its own founder approval gate. This PR does not offer
or trigger that operation.

## Feature-governance boundary

The following flags must remain OFF:

- `ENABLE_DOCUMENT_RENEWAL_AUTOMATION`
- `ENABLE_AGENT1_REFRESH_VERIFICATION`
- `ENABLE_MONITORING_SCREENING_CHANGE`
- `ENABLE_MONITORING_AUTO_RESOLUTION`

The linkage contract has no consumer that activates those workflows. Future
contract changes require explicit scope review, stable-ID migration analysis,
updated audit evidence, tests, and approval before a new version is introduced.
