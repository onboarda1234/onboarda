# Monitoring Document Renewal Request Contract

Version: `monitoring_document_renewal_request_v1`

## Purpose

This contract implements the request-and-upload-intake stage for documents
that require renewal. Monitoring remains the orchestration and visibility
layer. KYC & Documents remains the owner of the canonical document record and
of every verification or acceptance decision.

The workflow is controlled by
`ENABLE_DOCUMENT_RENEWAL_AUTOMATION`. The flag defaults to `OFF` in every
environment. When it is off, all new renewal business mutations fail closed:
request creation, resend, cancellation, due-date changes, candidate upload,
automatic eligibility and reminder generation. Authenticated read-only
projections remain available so already-created requests do not disappear
during a rollback. The independent artifact-cleanup worker remains active to
remove or reconcile already-reserved uploads safely, and protected legacy
workflows retain their existing flag-OFF behaviour.
When the flag is OFF, the renewal scheduler is not registered; eligibility and
reminder generation therefore write no requests, notifications, events, audit
records, or scheduler cursors.

## Scope

The service supports exact canonical document linkage for:

- `document_expired` alerts;
- `document_expiring_soon` alerts;
- an authorised manual renewal request against an exactly linked document.

Automatic eligibility uses the protected Document Health expiry contract.
Manual requests require an officer rationale. `document_expiry_missing`,
screening alerts, aliases, names, substring matches, case-folded guesses and
ambiguous links are not automatically eligible.

Every request snapshots stable identifiers:

- Monitoring Alert ID;
- application and customer IDs;
- person ID/type when the document belongs to a person;
- canonical current document ID, type and version;
- exact request reason;
- due date, creator and contract version.

The linkage is resolved through `monitoring_alert_linkage_v1` and revalidated
inside each write transaction. Ownership or document-version drift fails
closed.

The bounded eligibility scheduler consumes only the exact canonical alert
types above. It uses a persisted alert-ID cursor so one permanently blocked row
cannot starve later alerts. It never recreates a request for an alert that
already has request history, including a cancelled request. A manual request is
an explicit officer action and is never inferred from text, labels, or document
names.

## Request lifecycle

The persisted request vocabulary is deliberately narrow:

| API value | Meaning |
| --- | --- |
| `created` | Internal insertion state; creation has not completed until the request is exposed. |
| `awaiting_upload` | The request is visible to the client and awaits a candidate upload. |
| `upload_received` | A candidate file was received into the renewal intake ledger. It has not been verified or made canonical. |
| `cancelled` | The request was cancelled by an authorised officer. |

Successful creation records both `request_created` and `request_sent`, sets
`sent_at`, and commits as `awaiting_upload`. The Monitoring Alert status is not
changed. Monitoring may display the milestones “Renewal Requested”, “Request
Sent” and the current projection “Awaiting Upload”.

A database partial unique index allows at most one non-cancelled request for
the same application, person/entity, and logical document slot, across document
versions, and at most one non-cancelled request for an alert. Only cancellation
permits a new active request. Exact legacy conflicts use the same stable
document-slot identity. PostgreSQL request creation and the protected legacy
write guard share an application-and-slot advisory lock, then re-resolve the
current document under transaction locks, so a concurrent KYC replacement or
legacy request cannot split ownership across versions.

## Upload boundary

Portal uploads are validated and stored as renewal candidate artifacts in
`monitoring_document_renewal_uploads`. A pre-write durable artifact reservation
records the exact request/application/customer identity before bytes are sent
to object storage. The artifact lifecycle is `reserved`, `stored`, `attached`,
`cleanup_pending`, or `cleaned`; attachment to the upload row and request event
is atomic. A leased, idempotent cleanup worker remains active even while the
feature flag is OFF and retries only exact unattached artifacts with bounded
backoff.

Staging and production accept renewal candidates only through S3. Candidate
keys are deterministic, identity-scoped, and allowlisted; writes use
`If-None-Match: *`, and cleanup deletes only the recorded object version or an
unversioned candidate whose request, upload, and cleanup metadata proves exact
ownership. Conditional-write collisions are retried once where safe and are
then reconciled by exact request, upload, cleanup, digest, and size metadata.
An exact match recovers a lost success response; a proven mismatch is finalized
without deletion, while unavailable ownership evidence remains cleanup-pending.
S3 calls
use bounded timeouts, a two-worker executor, and cancellation-safe admission so
client disconnects cannot create an unbounded queue of upload bodies. No
redundant local copy is written in deployed environments. The testing/development
fallback opens its candidate directory and files through no-follow directory
descriptors with modes `0700` and `0600`.

Candidate intake does not create or update a row in `documents`, supersede a
document version, queue verification, invoke Agent 1, stale a memo, alter risk
evidence, or change a Monitoring Alert status.

`upload_received` means only that intake succeeded. A later, separately
approved release must transfer an accepted candidate into KYC & Documents and
perform verification.

The portal exposes only the requested document, reason, due date, current
request status and upload control. It exposes no approval, rejection,
verification, waiver or alert-closure control. Person-scoped cards use an
ownership-validated display label plus a short one-way reference so duplicate
or blank names remain distinguishable without exposing raw person or document
IDs.

## Officer actions

Authorised `admin`, `sco` and `co` users may:

- create an eligible request from an exact Monitoring Alert;
- resend the request intent;
- change the due date with an optimistic revision precondition;
- cancel the request with a reason.

Those actions mutate only the renewal request/event ledgers and their atomic
`client_notifications` and general `audit_log` evidence. They do not make
downstream document decisions. Read access remains subject to existing
application ownership and RBAC checks.

The authenticated contract exposes only these dedicated routes:

- `GET|POST /api/monitoring/alerts/{alert_id}/renewal-request`;
- `POST /api/monitoring/renewal-requests/{request_id}/resend`;
- `PATCH /api/monitoring/renewal-requests/{request_id}/due-date`;
- `POST /api/monitoring/renewal-requests/{request_id}/cancel`;
- `GET /api/portal/applications/{application_id}/renewal-requests`;
- `POST /api/portal/applications/{application_id}/renewal-requests/{request_id}/upload`.

The portal list is client-owned and cross-client requests are cloaked as not
found. Its response includes the evaluated feature state so an existing
request remains visible but exposes no upload control while the workflow is
OFF. Responses omit storage keys, file hashes, linkage fingerprints and audit
payloads. Listing is bounded before linkage projection: `limit` defaults to 50
and is capped at 100, `offset` is explicitly bounded, and cancelled history is
excluded unless `include_cancelled=true` is requested. The response includes
an exact filtered `total`, `has_more` and `next_offset`; the portal displays the
visible/total count and offers a GET-only **Load more** control until every
authoritative request has been rendered.

## Reminder generation

The scheduler uses bounded, configurable day offsets (default `14,7,3,1`), a
cross-task singleton lease, and a persisted due-date/request-ID cursor. It
publishes a client-portal notification and writes a deterministic
`reminder_generated` event for an eligible due-date milestone. It does not send
email. A unique event key makes repeated or overlapping scheduler ticks
idempotent. Cancelled and upload-received requests are excluded. Creation,
resend, and due-date changes already notify the portal, so an additional
reminder is suppressed on the same UTC day as the latest request update.
An audit or portal-notification infrastructure failure rolls back that row,
records a degraded failure, and advances the bounded fairness cursor so one
unrecoverable row cannot starve later reminders. The scheduler emits an ERROR
for the degraded tick and retries the row after the cursor wraps.

The officer projection derives the generated-reminder count and latest
generation timestamp from the event ledger. It does not label an intent as a
delivered email and does not predict a next reminder from stale browser state.

## Audit and atomicity

Every mutation writes an append-only general audit entry in the same database
transaction as the request/event change. Audit failure rolls back the entire
operation. Audit details include the stable request, alert, application and
document identifiers, before/after state, actor, reason and contract version.

The renewal event ledger records:

- `request_created`;
- `request_sent`;
- `request_resent`;
- `due_date_changed`;
- `reminder_generated`;
- `upload_received`;
- `request_cancelled`.

The artifact reservation is committed before external storage begins. After a
successful write, the exact reservation is marked stored; the upload row,
request status, event, general audit entry, and `attached` lifecycle state then
commit in one transaction. Any failed recording leaves durable cleanup evidence
instead of relying on process-local best effort. No network call is made while
the append-only audit lock or a pooled database connection is held.
Request-path cleanup uses a separate bounded executor from the periodic sweep,
and both cleanup marking and inline cleanup are best effort so they cannot mask
the authoritative upload response; the durable sweep remains the retry owner.

Artifact lifecycle transitions are durably evidenced in the restricted cleanup
ledger. Reservation, storage, cleanup, and attachment reconciliation also append
general audit entries; retry deferrals retain bounded error codes, attempt
counts, lease state, and retry timestamps. Public APIs expose none of the raw
storage keys.

## Legacy isolation

The pre-existing protected Monitoring document-refresh paths use
`application_enhanced_requirements` and can replace or review canonical
documents. They are not part of this contract. The new service does not import,
call, expose, extend or rely on those paths.

Existing active legacy requests are grandfathered and remain visible. The new
service never creates or calls a legacy request. To prevent parallel ownership,
when the new feature flag is `ON`, new legacy Monitoring document actions and
Monitoring-linked legacy replacement uploads fail closed with a controlled
conflict. A canonical active request continues to block a new legacy request if
the feature is later toggled OFF. The legacy endpoints themselves are not
extended into the new contract, and their protected flag-OFF historical
behaviour otherwise remains unchanged. Full historical migration and endpoint
removal require a separate evidence-backed decommissioning decision; this PR
does not rewrite historical rows.

## Explicit non-goals

This release does not:

- verify a document or invoke Agent 1;
- insert, update, supersede or delete a canonical document;
- accept, reject or waive document evidence;
- close, resolve, dismiss or otherwise transition a Monitoring Alert;
- change Applications, Screening, RSMP, EDD, Periodic Review or Change
  Management business state;
- implement screening-change automation;
- send email;
- activate any governed Monitoring flag by default.

## Release and QA boundary

Staging release verification must prove all four governed Monitoring flags are
`OFF`. Consequently, an enabled-path creation cannot be exercised against
regulated staging data during that release check. Enabled behavior is proven
with isolated SQLite and PostgreSQL tests and controlled fixtures. Browser QA
on the deployed `OFF` configuration must verify the feature is inactive, no
new request is created, existing protected pages remain unchanged and no
unexpected mutating request occurs. Any later staging activation requires a
separate explicit approval and controlled fixture plan.
