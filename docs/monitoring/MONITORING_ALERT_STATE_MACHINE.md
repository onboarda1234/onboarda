# Monitoring Alert State Machine

Version: `monitoring_alert_state_machine_v1`

Originating change: `PR-MON-M1-STATE-MACHINE-1`

## Objective

This contract governs the stored lifecycle status of a Monitoring Alert. It
defines the complete vocabulary, legal transitions, actors, evidence,
ownership boundaries, audit requirements, and failure behavior.

Anything not explicitly permitted here is prohibited.

This version does not activate document-renewal automation, client
notifications, Agent 1 refresh verification, screening-change monitoring,
automatic alert resolution, downstream outcome synchronization, or a
Monitoring UI redesign.

## Stored vocabulary

All values are internal Monitoring/back-office values and are not
client-facing. Client-visible document-request states remain in the separate
KYC & Documents lifecycle.

| API value | Display label | Class | Exact meaning | Next owner | Permitted alert types | Client-facing | Officer action required | Downstream owns resolution | Evidence required to enter |
|---|---|---|---|---|---|---|---|---|---|
| `open` | Open | active | A signal exists but has not completed initial triage. This is also the only fixed initial-insert state. | Monitoring officer | Any canonical alert type at creation. | No | Yes | Conditional: the authoritative linked workflow, if any, retains its business decision. | Initial detection provenance; there is no transition into `open` in v1. |
| `triaged` | Triaged | active | Initial relevance and ownership triage is complete; no downstream outcome is implied. | Monitoring officer | Any alert with an exact stored `source_reference` or `case_identifier`. | No | Yes | Conditional on the authoritative owner. | Exact `source_reference` or exact `case_identifier`; `reason_code=triage`. |
| `assigned` | Assigned | active | An active CO, SCO, or admin is assigned. Later reassignment is metadata-only and does not re-enter this state. | Assigned officer | Any alert in `open` or `triaged`; metadata-only reassignment is allowed while `assigned`, `in_review`, or `escalated`. | No | Yes | Conditional on the authoritative owner. | Active `officer_id`; `reason_code=assign`. |
| `in_review` | In Review | active | Substantive Monitoring investigation is under way. It is not a document-request or verification sub-state. | Reviewing officer; senior officer when returning from escalation | Any alert with exact identity evidence; an escalated alert additionally requires senior acknowledgement. | No | Yes | Conditional on the authoritative owner; Monitoring cannot duplicate a downstream decision. | Exact `source_reference` or `case_identifier` for ordinary entry, or `officer_rationale` for senior acknowledgement. |
| `escalated` | Escalated | active | Senior scrutiny, including an explicitly recorded overdue escalation, is required. It is not an EDD handoff or resolution. | SCO/admin | Any active alert. | No | Yes | Conditional on the authoritative owner. | `officer_rationale`; overdue entry also requires the exact alert-scoped `escalation_id`. |
| `routed_to_review` | Routed to Review | handoff | The signal is exactly linked and handed to Periodic Review. Monitoring actions are locked. | Periodic Review | Any active alert that can be linked to the exact same-application Periodic Review record. | No | No | Yes: Periodic Review owns its decision; automatic outcome synchronization is disabled in v1. | Exact `periodic_review_id`; `reason_code=route_to_periodic_review`. |
| `routed_to_edd` | Routed to EDD | handoff | The signal is exactly linked and handed to EDD. Monitoring actions are locked. | EDD | Any active alert that can be linked to the exact same-application EDD case. | No | No | Yes: EDD owns its decision; automatic outcome synchronization is disabled in v1. | Exact `edd_case_id`; `reason_code=route_to_edd`. |
| `dismissed` | Dismissed | terminal | A controlled officer decision found the signal false-positive, duplicate, not actionable, externally resolved, or otherwise dismissible. | None; a later signal requires a new alert | Manual-, document-, or screening-owned alerts only. EDD-, Periodic Review-, and Change Management-owned alerts cannot be dismissed by Monitoring. | No | No | No downstream resolution is invoked by dismissal. | Structured `dismissal_reason`, matching `dismiss_*` reason code, `officer_rationale`, owner-specific exact evidence, and approved control evidence where required. |
| `resolved` | Resolved | terminal | The Monitoring signal is complete because an allowed manual determination or an accepted document outcome is proven. A route alone never resolves an alert. | None; a later signal requires a new alert | Manual-owned alerts from `in_review`/`escalated`, or document-owned alerts from any active state. EDD, Periodic Review, and Screening terminal synchronization remains disabled. | No | No | Document-owned resolution depends on KYC & Documents; manual resolution does not. | Manual: `officer_rationale` and control evidence where required. Documents: exact `document_request_id` plus linked `document_id` with accepted outcome. |
| `waived` | Waived | terminal | An authorized document exception was approved with a reason. | None; a later signal requires a new alert | Document-owned alerts only. | No | No | KYC & Documents supplies the exact waived request; SCO/admin supplies the governed authorization. | Exact waived `document_request_id`, `waiver_reason`, and senior/control authorization. |

The following are not valid new stored values:

- `closed` duplicates `resolved` and has no reconciled staging row or current
  production writer.
- `cancelled` and `failed` have no audited Monitoring semantics. The historical
  spellings `cancelled` and `canceled` remain read-only projection aliases for
  `resolved`; neither is a valid stored value or transition target.
- `action_required` overlaps triage/assignment.
- `officer_review` overlaps the deployed `in_review` API contract.
- `client_requested`, `awaiting_client`, `received`, and `verifying` belong to
  the separate document-request/verification lifecycle.
- `document_requested`, `client_uploaded`, `under_review`,
  `awaiting_review`, and `notification_failed` remain read/display aliases
  only and may never be transition targets.

## Ownership boundaries

- Monitoring owns signal creation, triage, assignment visibility, escalation
  visibility, orchestration linkage, and auditability.
- KYC & Documents owns document requests, uploads, document review,
  acceptance, rejection, and waiver evidence.
- Screening Review owns screening disposition.
- EDD owns EDD case stages and outcomes.
- Periodic Review owns periodic-review decisions.
- Change Management owns material-profile-change decisions.

The existence of a downstream row is not resolution evidence. A linked owner
may produce a terminal Monitoring transition only through an explicit,
same-application, terminal downstream outcome contract.

Ownership resolution uses the same explicit contracts as the PR #900 runtime
audit: alert-side and reverse `linked_monitoring_alert_id` EDD/Periodic Review
links, `application_enhanced_requirements.monitoring_alert_id`,
`monitoring_alert_evidence.monitoring_alert_id`, and the exact
`change_alerts.source_reference = monitoring_alert:<id>` Change Management
bridge. Screening evidence must also agree with the alert's provider and case.
Explicit links take precedence over alert-type inference. A broken,
cross-application, conflicting-provider/case, or multi-owner link fails closed
and must be reconciled before any status transition.

The only temporary multi-owner shape accepted is an atomic handoff: exactly
one Documents, Screening Review, or Change Management source owner plus the
new exact EDD or Periodic Review target link supplied as transition evidence.
Two source owners, the wrong target identifier, or an EDD↔Periodic Review
transfer still fails closed. Once the alert reaches `routed_to_edd` or
`routed_to_review`, the exact linked target is the authoritative owner while
the source linkage remains immutable provenance.

## Legal transition matrix

“Officer” means an authenticated `co`, `sco`, or `admin`, with existing
assignment and four-eyes RBAC still enforced. “Senior” means `sco` or `admin`.
The executable selector first matches the exact source/target edge, enabled
actor type, authoritative database role, source workflow, and owner. It then
requires the supplied `reason_code` to belong to that exact rule and validates
that rule's evidence. A reason code allowed for the same target on a different
rule cannot select the requested edge. The target-level reason-code allowlist
is therefore only an outer guard; the per-rule binding below is authoritative.
For every row, `Allowed owner` is also its permitted-alert-type contract:
owner-specific values resolve through the authoritative linkage/type model
above, while `any` means every canonical alert type without a broken or
conflicting ownership link. Every enabled row shares the explicit idempotency
and reopening policy below: replay is a controlled no-audit conflict, and no
terminal state can be reopened.

V1 has 42 enabled rules:

| Rule ID | From | To | Exact `reason_code` values | Actor / source workflow | Mandatory evidence | Allowed owner | Control |
|---|---|---|---|---|---|---|---|
| `open_to_triaged` | `open` | `triaged` | `triage` | officer (`co`/`sco`/`admin`) / `monitoring` | one of exact `source_reference` or exact `case_identifier` | any | none |
| `open_to_assigned` | `open` | `assigned` | `assign` | officer (`co`/`sco`/`admin`) / `monitoring` | active `officer_id` | any | assignment RBAC |
| `triaged_to_assigned` | `triaged` | `assigned` | `assign` | officer (`co`/`sco`/`admin`) / `monitoring` | active `officer_id` | any | assignment RBAC |
| `open_to_in_review` | `open` | `in_review` | `review_started`<br>`risk_profile_update_required`<br>`further_information_requested` | officer (`co`/`sco`/`admin`) / `monitoring` | one of exact `source_reference` or exact `case_identifier` | any | none |
| `triaged_to_in_review` | `triaged` | `in_review` | `review_started`<br>`risk_profile_update_required`<br>`further_information_requested` | officer (`co`/`sco`/`admin`) / `monitoring` | one of exact `source_reference` or exact `case_identifier` | any | none |
| `assigned_to_in_review` | `assigned` | `in_review` | `review_started`<br>`risk_profile_update_required`<br>`further_information_requested` | officer (`co`/`sco`/`admin`) / `monitoring` | one of exact `source_reference` or exact `case_identifier` | any | none |
| `escalated_to_in_review` | `escalated` | `in_review` | `senior_acknowledged` | senior officer (`sco`/`admin`) / `monitoring` | `officer_rationale` | any | none |
| `open_to_escalated` | `open` | `escalated` | `escalated_to_sco` | officer (`co`/`sco`/`admin`) / `monitoring` | `officer_rationale` | any | none |
| `open_to_overdue_escalated` | `open` | `escalated` | `overdue_escalation` | officer (`co`/`sco`/`admin`) / `monitoring` | `officer_rationale` and exact alert-scoped `escalation_id` | any | none |
| `triaged_to_escalated` | `triaged` | `escalated` | `escalated_to_sco` | officer (`co`/`sco`/`admin`) / `monitoring` | `officer_rationale` | any | none |
| `triaged_to_overdue_escalated` | `triaged` | `escalated` | `overdue_escalation` | officer (`co`/`sco`/`admin`) / `monitoring` | `officer_rationale` and exact alert-scoped `escalation_id` | any | none |
| `assigned_to_escalated` | `assigned` | `escalated` | `escalated_to_sco` | officer (`co`/`sco`/`admin`) / `monitoring` | `officer_rationale` | any | none |
| `assigned_to_overdue_escalated` | `assigned` | `escalated` | `overdue_escalation` | officer (`co`/`sco`/`admin`) / `monitoring` | `officer_rationale` and exact alert-scoped `escalation_id` | any | none |
| `in_review_to_escalated` | `in_review` | `escalated` | `escalated_to_sco` | officer (`co`/`sco`/`admin`) / `monitoring` | `officer_rationale` | any | none |
| `in_review_to_overdue_escalated` | `in_review` | `escalated` | `overdue_escalation` | officer (`co`/`sco`/`admin`) / `monitoring` | `officer_rationale` and exact alert-scoped `escalation_id` | any | none |
| `open_to_periodic_review` | `open` | `routed_to_review` | `route_to_periodic_review` | officer (`co`/`sco`/`admin`) / `monitoring` | exact same-application `periodic_review_id` | any | none |
| `triaged_to_periodic_review` | `triaged` | `routed_to_review` | `route_to_periodic_review` | officer (`co`/`sco`/`admin`) / `monitoring` | exact same-application `periodic_review_id` | any | none |
| `assigned_to_periodic_review` | `assigned` | `routed_to_review` | `route_to_periodic_review` | officer (`co`/`sco`/`admin`) / `monitoring` | exact same-application `periodic_review_id` | any | none |
| `in_review_to_periodic_review` | `in_review` | `routed_to_review` | `route_to_periodic_review` | officer (`co`/`sco`/`admin`) / `monitoring` | exact same-application `periodic_review_id` | any | none |
| `escalated_to_periodic_review` | `escalated` | `routed_to_review` | `route_to_periodic_review` | officer (`co`/`sco`/`admin`) / `monitoring` | exact same-application `periodic_review_id` | any | none |
| `open_to_edd` | `open` | `routed_to_edd` | `route_to_edd` | officer (`co`/`sco`/`admin`) / `monitoring` | exact same-application `edd_case_id` | any | none |
| `triaged_to_edd` | `triaged` | `routed_to_edd` | `route_to_edd` | officer (`co`/`sco`/`admin`) / `monitoring` | exact same-application `edd_case_id` | any | none |
| `assigned_to_edd` | `assigned` | `routed_to_edd` | `route_to_edd` | officer (`co`/`sco`/`admin`) / `monitoring` | exact same-application `edd_case_id` | any | none |
| `in_review_to_edd` | `in_review` | `routed_to_edd` | `route_to_edd` | officer (`co`/`sco`/`admin`) / `monitoring` | exact same-application `edd_case_id` | any | none |
| `escalated_to_edd` | `escalated` | `routed_to_edd` | `route_to_edd` | officer (`co`/`sco`/`admin`) / `monitoring` | exact same-application `edd_case_id` | any | none |
| `open_to_dismissed` | `open` | `dismissed` | `dismiss_false_positive`<br>`dismiss_duplicate`<br>`dismiss_no_action_needed`<br>`dismiss_resolved_externally`<br>`dismiss_other` | officer (`co`/`sco`/`admin`) / `monitoring` | `dismissal_reason` and `officer_rationale`; owner-specific exact evidence | `manual`, `documents`, `screening_review` | risk-tiered |
| `triaged_to_dismissed` | `triaged` | `dismissed` | `dismiss_false_positive`<br>`dismiss_duplicate`<br>`dismiss_no_action_needed`<br>`dismiss_resolved_externally`<br>`dismiss_other` | officer (`co`/`sco`/`admin`) / `monitoring` | `dismissal_reason` and `officer_rationale`; owner-specific exact evidence | `manual`, `documents`, `screening_review` | risk-tiered |
| `assigned_to_dismissed` | `assigned` | `dismissed` | `dismiss_false_positive`<br>`dismiss_duplicate`<br>`dismiss_no_action_needed`<br>`dismiss_resolved_externally`<br>`dismiss_other` | officer (`co`/`sco`/`admin`) / `monitoring` | `dismissal_reason` and `officer_rationale`; owner-specific exact evidence | `manual`, `documents`, `screening_review` | risk-tiered |
| `in_review_to_dismissed` | `in_review` | `dismissed` | `dismiss_false_positive`<br>`dismiss_duplicate`<br>`dismiss_no_action_needed`<br>`dismiss_resolved_externally`<br>`dismiss_other` | officer (`co`/`sco`/`admin`) / `monitoring` | `dismissal_reason` and `officer_rationale`; owner-specific exact evidence | `manual`, `documents`, `screening_review` | risk-tiered |
| `escalated_to_dismissed` | `escalated` | `dismissed` | `dismiss_false_positive`<br>`dismiss_duplicate`<br>`dismiss_no_action_needed`<br>`dismiss_resolved_externally`<br>`dismiss_other` | officer (`co`/`sco`/`admin`) / `monitoring` | `dismissal_reason` and `officer_rationale`; owner-specific exact evidence | `manual`, `documents`, `screening_review` | risk-tiered |
| `in_review_to_resolved_manual` | `in_review` | `resolved` | `no_material_impact` | officer (`co`/`sco`/`admin`) / `monitoring` | `officer_rationale`; approved control evidence when required | manual | risk-tiered |
| `escalated_to_resolved_manual` | `escalated` | `resolved` | `no_material_impact` | officer (`co`/`sco`/`admin`) / `monitoring` | `officer_rationale`; approved control evidence when required | manual | risk-tiered |
| `open_to_resolved_documents` | `open` | `resolved` | `document_accepted`<br>`document_already_updated` | officer (`co`/`sco`/`admin`) / `kyc_documents` | exact `document_request_id` and its linked `document_id`; request outcome `accepted` | documents | document control |
| `triaged_to_resolved_documents` | `triaged` | `resolved` | `document_accepted`<br>`document_already_updated` | officer (`co`/`sco`/`admin`) / `kyc_documents` | exact `document_request_id` and its linked `document_id`; request outcome `accepted` | documents | document control |
| `assigned_to_resolved_documents` | `assigned` | `resolved` | `document_accepted`<br>`document_already_updated` | officer (`co`/`sco`/`admin`) / `kyc_documents` | exact `document_request_id` and its linked `document_id`; request outcome `accepted` | documents | document control |
| `in_review_to_resolved_documents` | `in_review` | `resolved` | `document_accepted`<br>`document_already_updated` | officer (`co`/`sco`/`admin`) / `kyc_documents` | exact `document_request_id` and its linked `document_id`; request outcome `accepted` | documents | document control |
| `escalated_to_resolved_documents` | `escalated` | `resolved` | `document_accepted`<br>`document_already_updated` | officer (`co`/`sco`/`admin`) / `kyc_documents` | exact `document_request_id` and its linked `document_id`; request outcome `accepted` | documents | document control |
| `open_to_waived_documents` | `open` | `waived` | `document_waived` | senior officer (`sco`/`admin`) / `kyc_documents` | exact waived `document_request_id` and `waiver_reason` | documents | senior/document control |
| `triaged_to_waived_documents` | `triaged` | `waived` | `document_waived` | senior officer (`sco`/`admin`) / `kyc_documents` | exact waived `document_request_id` and `waiver_reason` | documents | senior/document control |
| `assigned_to_waived_documents` | `assigned` | `waived` | `document_waived` | senior officer (`sco`/`admin`) / `kyc_documents` | exact waived `document_request_id` and `waiver_reason` | documents | senior/document control |
| `in_review_to_waived_documents` | `in_review` | `waived` | `document_waived` | senior officer (`sco`/`admin`) / `kyc_documents` | exact waived `document_request_id` and `waiver_reason` | documents | senior/document control |
| `escalated_to_waived_documents` | `escalated` | `waived` | `document_waived` | senior officer (`sco`/`admin`) / `kyc_documents` | exact waived `document_request_id` and `waiver_reason` | documents | senior/document control |

The following three rules are defined for contract evolution but are disabled
and cannot be selected in v1:

| Rule ID | From | To | Exact `reason_code` | Actor / source workflow | Mandatory evidence | Owner | Control |
|---|---|---|---|---|---|---|---|
| `routed_to_edd_to_resolved_future` | `routed_to_edd` | `resolved` | `downstream_outcome` | `downstream` actor (`system` role) / `edd` | exact `edd_case_id` and terminal `downstream_outcome` | `edd` | `downstream_control` |
| `routed_to_review_to_resolved_future` | `routed_to_review` | `resolved` | `downstream_outcome` | `downstream` actor (`system` role) / `periodic_review` | exact `periodic_review_id` and terminal `downstream_outcome` | `periodic_review` | `downstream_control` |
| `screening_in_review_to_resolved_future` | `in_review` | `resolved` | `downstream_outcome` | `downstream` actor (`system` role) / `screening_review` | exact `screening_case_id` and terminal `downstream_outcome` | `screening_review` | `downstream_control` |

For dismissal, the normalized `dismissal_reason` must exactly correspond to
the selected reason code (`dismiss_<dismissal_reason>`). Screening-owned
dismissal also requires the alert's exact stored provider `case_identifier`.
Document-owned dismissal requires an exact `document_id` or
`document_request_id`. Material clears additionally require the exact approved
`review_request_id`.

No terminal status has an outgoing transition in v1. A later signal creates a
new linked alert; no alert is silently reopened.

Route states are nonterminal but Monitoring-action-locked. They are never
treated as resolved simply because a downstream object exists.
The exact Periodic Review that owns a `routed_to_review` handoff is not blocked
from recording its own downstream decision when either side of the canonical
link identifies that same alert/review pair. This narrow completion exemption
does not change the alert status or synchronize a downstream outcome.
Unrelated `routed_to_review` alerts and every `routed_to_edd` alert remain
nonterminal blockers for Periodic Review completion.
An alert already owned through an EDD or Periodic Review linkage cannot be
dismissed or manually resolved by Monitoring, even if its stored status has not
yet reached the corresponding handoff state.

## Typed evidence contract

Recognized evidence keys are:

- `source_reference`
- `case_identifier`
- `officer_id`
- `officer_rationale`
- `dismissal_reason`
- `waiver_reason`
- `document_request_id`
- `document_id`
- `verification_execution_id`
- `screening_case_id`
- `edd_case_id`
- `change_request_id`
- `periodic_review_id`
- `review_request_id`
- `escalation_id`
- `downstream_outcome`

Identifiers must be non-empty, refer to an existing canonical object where a
table exists, and match the exact alert-level linkage. Same-application context
alone is not causal evidence. Free text is accepted only for the explicitly
textual rationale/reason fields; it cannot substitute for a required linked
object.

Exact-link validation is fail-closed:

- `source_reference` and `case_identifier` must equal the alert's stored value,
  byte-for-byte after outer whitespace removal.
- `officer_id` must identify an active user whose authoritative role is `co`,
  `sco`, or `admin`.
- `document_request_id` must identify the exact same-application enhanced
  requirement whose `monitoring_alert_id` is this alert. Resolution requires
  its `accepted` outcome; waiver requires its `waived` outcome and the supplied
  `waiver_reason` must exactly match the stored approved waiver reason.
- A supplied `document_id` must exist in the same application and equal the
  linked document on the supplied request. Without a request, it is causal only
  when the alert stores the exact `source_reference=document:<id>`.
- A screening-owned dismissal requires the alert's exact stored external
  provider `case_identifier`. The current `screening_reviews` schema has no
  alert-level foreign key, so `screening_case_id` is reserved for the disabled
  future consumer and is rejected for enabled transitions.
- `edd_case_id` and `periodic_review_id` require an exact alert-side or reverse
  `linked_monitoring_alert_id` relation and the same application. Their disabled
  future resolution rules additionally require an authoritative terminal
  downstream outcome.
- `change_request_id` is exact only when its Change Alert stores
  `source_reference=monitoring_alert:<alert_id>` in the same application. It
  does not by itself authorize any enabled v1 transition.
- `review_request_id` must belong to this alert, be `approved` or
  `senior_cleared`, name an authoritative active initiating officer and
  SCO/admin approver, and preserve maker/checker separation for an approved
  request. Only the recorded approver may execute it. It must also
  match the alert's exact current `source_alert_status`, approved terminal
  `requested_outcome`, structured `dismissal_reason`, `officer_rationale`, and
  complete allowlisted `transition_evidence` object. The service revalidates
  the human evidence note against the locked alert's current severity/type.
  Approval of one source state, outcome, rationale, or linked-evidence set
  cannot authorize another.
- `escalation_id` must identify the exact row in
  `monitoring_alert_escalations` for this alert. It is mandatory for every
  `overdue_escalation` rule and cannot be replaced by rationale text.

Pending maker-checker requests persist supported typed identifiers as a JSON
object in `monitoring_alert_review_requests.transition_evidence`. The existing
human-readable `evidence_ref` remains separate and cannot substitute for those
identifiers. Approval reloads and revalidates the typed object inside the
transition transaction.

Textual evidence and the human-readable transition reason are limited to 1,000
characters. The same limit is enforced before pending or senior-cleared
review-control rows are inserted, including officer rationale, waiver reason,
the evidence note, and typed control identifiers. Oversized text is rejected
before any alert, review-control, or audit row is mutated; it is never silently
truncated.
Document control also binds the ledger's requested outcome exactly:
`accept_updated_document` authorizes only `document_accepted`,
`mark_already_updated` authorizes only `document_already_updated`, and
`waive_with_reason` authorizes only `document_waived`.

## Role and four-eyes policy

- `co`, `sco`, and `admin` may perform ordinary officer transitions.
- Existing assignment RBAC remains: CO may self-assign; only SCO/admin may
  assign another active CO/SCO/admin. An `open` or `triaged` assignment uses
  the state machine and enters `assigned`. Reassignment while `assigned`,
  `in_review`, or `escalated` is a locked, audited metadata-only change that
  preserves the lifecycle status. Handoff and terminal alerts cannot be
  assigned.
- A system actor has no status transition in v1. Merely defining the state
  machine does not authorize automation.
- Downstream actors may perform only the future downstream-owned terminal
  transitions listed above.
- Material screening, critical alerts, and controlled document acceptance or
  waiver require a valid review-control record or a recorded senior disposition
  under the existing maker-checker policy. Direct service calls receive the
  same backstop as HTTP handlers.
- A pending maker/checker request that reaches `approved` may not be approved
  by its initiator. The explicit exception is the existing SCO/admin
  direct-clear path: it records `senior_cleared` with
  `second_review_bypassed=true`, rather than representing a self-approved
  maker/checker request.

## Idempotency, concurrency, and terminal policy

- Every mutation requires the caller’s exact expected current status.
- PostgreSQL locks the selected alert with `SELECT ... FOR UPDATE`.
- The update repeats the expected-status predicate.
- A stale source fails with HTTP 409 and changes nothing.
- Repeating a transition whose target is already current returns a controlled
  409 conflict and creates no duplicate transition audit.
- Two concurrent terminal decisions cannot both succeed.
- EDD routing additionally locks the application row, inspects every active
  EDD without `SKIP LOCKED`, and creates a case only when none exists. One
  already-consistent case linked to the same alert may be reused; every other
  active case fails closed without changing the alert or overwriting its
  workflow provenance.
- Terminal reopening is prohibited. V1 has no authorized reopening path.

`is_action_locked` is deliberately broader than terminality: both handoff
states and all terminal states reject Monitoring assignment, decision,
dismissal, document-request, document-review, replacement-upload, and
review-control creation paths. Those paths take the Monitoring Alert lock first
and revalidate the authoritative state before touching a linked row.

For KYC & Documents specifically:

- accepting a linked refresh request may resolve the alert, and waiving it may
  waive the alert, only through the exact document rules above;
- a controlled non-senior acceptance or waiver creates a pending review record
  before changing any requirement, document, or alert field; the approved
  executor returns through the same document service, so linked-row updates,
  the canonical transition, and all audits commit atomically;
- rejecting an active refresh request reopens only the document requirement to
  its request state and never reopens or rewrites the Monitoring Alert;
- an accepted/waived linked requirement cannot later be reversed through the
  enhanced-requirement API when its alert is terminal; the requirement and
  alert remain unchanged and the transaction returns 409;
- repeating an identical terminal document outcome, or changing only its note,
  is metadata-idempotent and creates no second status-transition audit; and
- a later concern after terminal completion requires a new linked alert.

## Review-control ledger atomicity

Risk-tiered clearing uses an evidence ledger; it is not a parallel lifecycle:

- alert creation/clearance paths lock the Monitoring Alert first;
- a partial unique index,
  `uq_monitoring_review_requests_one_pending`, permits at most one pending
  review request per alert under concurrency;
- each request stores allowlisted `transition_evidence`, the exact
  creation-time `source_alert_status`, requested terminal outcome, structured
  dismissal reason, and officer rationale;
- approval locks the alert first and the review request second, requires the
  request still be pending, revalidates source-status equality, authoritative
  approver role, maker/checker separation, exact outcome/reason/rationale,
  current evidence, and the complete typed-evidence object;
- a legacy request with missing/noncanonical `source_alert_status`, or a
  request whose alert moved, cannot approve; it must be rejected and recreated;
- marking the request approved, executing the canonical transition, appending
  both control and transition audits, and any associated officer metadata are
  one caller-owned transaction; and
- senior direct clearance records its `senior_cleared` ledger row and terminal
  transition in that same transaction. Any ledger insert, audit append,
  evidence validation, status update, or linked write failure rolls back the
  whole unit.

## Audit contract

Every successful status change appends one canonical hash-chained audit entry
in the same database transaction as the status update. It records:

- alert ID and application ID;
- previous and new status;
- actor type, actor ID, and actor role;
- source workflow;
- reason code and human-readable reason;
- typed evidence IDs;
- state-machine version;
- request/correlation ID;
- timestamp;
- originating PR/version.

The canonical action is `monitoring.alert.status_transition`. The public
service has no caller-supplied audit-writer override: it always uses the
repository’s append-only, hash-chained writer with `commit=False`. Before
commit, it reloads the returned hash from `audit_log` and verifies the exact
action, target, application, correlation ID, and before/after status. A no-op,
fabricated hash, missing row, or mismatched row is an audit failure. The caller
commits immediately after all local writes. An audit failure rolls back the
status and every caller-owned write. If no inbound correlation ID exists, the
service generates and records a unique transition correlation ID rather than
leaving the field blank.

Denied attempts are logged with safe structural metadata. Evidence values and
secrets are not written to application logs.

## Error behavior

| Error | HTTP behavior |
|---|---:|
| malformed/unknown status or evidence | 400 |
| insufficient role / four-eyes denial | 403 |
| missing alert or linked object | 404 |
| illegal transition, terminal state, stale state, linkage mismatch, or replay | 409 |
| audit/transaction infrastructure failure | sanitized 500 |

No invalid transition returns false success or an internal stack trace.

## Database protection and rollback

Fresh PostgreSQL and SQLite schemas enforce:

```text
NOT NULL
CHECK status IN (
  open, triaged, assigned, in_review, escalated,
  routed_to_review, routed_to_edd,
  dismissed, resolved, waived
)
```

Long-lived PostgreSQL installation:

1. scans for NULL, blank, and off-canon values;
2. fails startup without rewriting any row if one exists;
3. installs the named constraint as `NOT VALID`;
4. validates it;
5. sets `NOT NULL`;
6. verifies the constraint is valid.

The rollback drops only the named `CHECK` and `NOT NULL`; it rewrites no alert
data. No `status`/`resolved_at` cross-constraint is added because the audited
dataset contains historical terminal rows without `resolved_at`. That evidence
repair is outside this PR.

## Static enforcement

Runtime `UPDATE monitoring_alerts ... status = ...` statements are permitted
only inside the transition service. Conflict upserts must preserve the current
status.

The AST guard covers quoted/schema-qualified SQL, aliases, tuple assignments,
PostgreSQL `MERGE` and inheritance-table `*`/`ONLY` forms, SQLite conflict
updates and `REPLACE`, f-strings, concatenation, augmented assignment, and
fully unresolved SQL builder expressions. It masks SQL string literals and
comments and tracks nested parentheses when identifying the outer
`SET`/`WHERE` boundary. It inspects every statement in scripts/CTEs and
preserves every possible string/executor binding across conditions, loops,
`match`, `try`, and exception-suppressing `with` blocks. Dynamic or unresolved
execute statements fail closed unless their exact function and statement count
appear in a documented narrow allowlist.

The narrow lifecycle-write exceptions are:

- fixed initial `open` inserts;
- the versioned PR #902 migration/rollback tool;
- schema installation;
- explicitly guarded fixture/demo snapshot seeders.

## Feature-flag boundary

These flags remain OFF and gain no automatic consumer in this PR:

- `ENABLE_DOCUMENT_RENEWAL_AUTOMATION`
- `ENABLE_AGENT1_REFRESH_VERIFICATION`
- `ENABLE_MONITORING_SCREENING_CHANGE`
- `ENABLE_MONITORING_AUTO_RESOLUTION`

State-machine policy may describe evidence required by future consumers. Future
activation requires explicit migration analysis, an updated matrix/version,
focused tests, and founder/compliance approval.
