# Monitoring Document Renewal Upload Binding Contract

Version: `monitoring_document_renewal_upload_binding_v1`

## Purpose

This contract binds one staged replacement upload to exactly one authoritative
document-renewal request. It extends the renewal-request intake boundary; it
does not make the candidate a canonical KYC document or make any decision about
its contents.

Monitoring remains an orchestration and visibility layer. KYC & Documents
continues to own the original document, any later canonical replacement, and
all verification or acceptance decisions.

The contract is governed by `ENABLE_DOCUMENT_RENEWAL_AUTOMATION`, which remains
`OFF` by default. Turning the flag off disables new uploads while retaining
read-only visibility and exact cleanup of already-reserved candidate artifacts.

## Authoritative identity

Every successful binding records:

- `upload_id`;
- `renewal_request_id`;
- `application_id`;
- `customer_id`;
- nullable `person_id` and `person_type` for a person-owned document, with the
  application entity remaining authoritative for entity-owned documents;
- `original_document_id` and its snapshotted version;
- a candidate-only `uploaded_document_id` that is not a `documents` row;
- the exact canonical `document_type` from the renewal request;
- `upload_timestamp` and `uploaded_by`;
- `binding_status=bound`;
- the binding-contract version and deterministic integrity fingerprint.

These values are derived by the server from the authenticated application and
the locked renewal request. The portal supplies only the request route and file
body. It never supplies raw customer, person or document identifiers as
binding authority.

The database independently rejects cross-wired identities. One dialect-aware
INSERT/UPDATE guard joins the candidate upload, renewal request,
application/customer and original document/version/type and compares the
nullable person/entity pair, upload timestamp and uploader with null-safe
semantics. It rejects unrelated but individually valid IDs. The guard is
installed both by consolidated startup and the migration-059 runner hook before
the migration is recorded as applied.

The design deliberately does not add redundant composite indexes to the
existing Applications or Documents tables (or to the renewal tables): every
proposed tuple began with an already-unique primary key, while a normal index
build could block regulated writes. Existing simple foreign keys retain parent
existence protection. Current owner linkage is revalidated on every read; later
parent drift suppresses the public binding and requires manual review instead
of displaying a stale **Bound** state. The service remains the only approved
application writer.

## Fail-closed validation

The upload must resolve to one existing renewal request whose exact state is
`awaiting_upload`. The service validates the following before durable storage
and repeats the authoritative checks under transaction locks before binding:

- feature flag and authenticated client identity;
- exact application and customer ownership;
- exact person/entity ownership;
- exact original canonical document, version, slot and document type;
- current canonical linkage and eligibility fingerprint;
- active Monitoring Alert without changing its status;
- non-cancelled, non-expired request state;
- optimistic request revision;
- absence of an existing upload binding;
- exact stored-artifact reservation identity.

Missing, cancelled, expired, duplicate, stale, cross-application,
cross-customer, wrong-person, wrong-document and wrong-document-type attempts
fail closed. Cross-client application or request probes remain cloaked as
`404 Not Found`; they do not reveal or append domain history to another
customer's request.

## Duplicate and idempotency policy

Version 1 is reject-only: one renewal request may have only one immutable
active binding. It does not replace a pending upload. A different subsequent
upload is rejected and cannot create an ambiguous second binding.

An exact retry of the same authoritative upload identity is idempotent. It
returns the existing binding without creating a second upload or duplicate
successful audit evidence. Cancelling the renewal request retains the original
candidate and audit history; a later approved request receives its own distinct
binding.

## Storage and transaction boundary

Candidate bytes use the dedicated, private renewal-candidate namespace. A
durable reservation is committed before external storage. Production and
staging writes are create-only and the object identity is reconciled using the
exact request, upload, cleanup, digest and size metadata.

The final upload row, binding identity, request projection to
`upload_received`, domain event, canonical audit entry and reservation
attachment commit atomically. Any failure rolls back the binding and request
change; an unbound stored candidate is retained only as bounded cleanup
evidence until the exact cleanup worker removes it. This is not a partial
binding. After that rollback, a proven-owned controlled or transaction failure
receives one separate `binding_failed` audit entry. If failure evidence cannot
be appended, the request fails closed as audit infrastructure unavailable.

The transaction never inserts, updates, supersedes or deletes a row in
`documents`; invokes Agent 1 or another verifier; or transitions a Monitoring
Alert.

## Audit

Successful intake records the distinct facts that the candidate was received
and bound. Audit evidence includes stable binding identifiers, actor, timestamp,
contract version and correlation identity without exposing file bytes, storage
credentials or raw object-storage locations.

Owned rejected attempts use bounded reason codes such as cancelled request,
expired request, duplicate upload, wrong document type and binding failure.
Same-customer cross-application attempts receive rejection evidence only after
the mismatch is re-proven under lock. Stale or broken linkage receives
`binding_failed` evidence only after the exact failure is re-proven.

Cross-client and nonexistent-request probes remain in the authorization/
security audit boundary rather than writing into a foreign request's domain
history. That evidence is scoped to the caller-owned route application and a
templated route; it does not persist the supplied or foreign renewal-request
identifier.

## API and RBAC

No new mutation route is introduced. The authenticated client upload remains:

`POST /api/portal/applications/{application_id}/renewal-requests/{request_id}/upload`

The existing client-owned renewal list remains read-only. The upload response
contains a safe binding projection and omits its fingerprint, storage key,
file hash, raw customer/person/original-document identifiers, audit payloads
and any provider or infrastructure credential. Officer projections may show
the read-only binding status and candidate reference; they expose no binding,
verification, approval or alert-closure control.

## User-interface semantics

After a successful binding:

- the Client Portal displays **Uploaded** and **Awaiting Verification**;
- Monitoring displays **Upload Received**;
- the Monitoring Alert remains open;
- neither surface claims that verification has started or succeeded;
- neither surface exposes a canonical-document replacement or decision control.

## Content-type limitation

Binding proves which exact request, customer, person/entity, original document
and requested document type own the uploaded bytes. Standard upload safety
validation checks the file extension, MIME type, magic bytes and size.

This PR deliberately performs no OCR, semantic document classification or
document verification. A client declaration or filename cannot prove that the
file contents are the requested document type. That determination belongs to a
later separately approved verification workflow. The UI and API must therefore
never present `bound` or `Awaiting Verification` as `verified`, `accepted` or
`canonical`.

## Explicit non-goals

This contract does not:

- invoke Agent 1, OCR or any verifier;
- create or replace a canonical KYC document;
- edit document metadata;
- accept, reject, waive or approve document evidence;
- detect material changes;
- resolve, dismiss or close a Monitoring Alert;
- add Screening behavior;
- enable any governed Monitoring feature flag by default.
