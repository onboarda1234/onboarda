# P0 KYC party/document staging repair runbook

This runbook covers the incident-scoped data repair for the July 2026
post-pricing KYC persistence defect. The utility is read-only by default and
never discovers a broad scope. It only inspects application references supplied
by the operator.

Do not run apply mode until the code fix that preserves party row IDs and stops
ref-only fixture marking has been deployed to staging. Otherwise a later party
save or service restart can recreate the corruption.

## What the utility changes

For each explicitly selected application, the utility:

1. Resolves every document `person_id` against party row `id` and `person_key`
   values within the same application.
2. Requires exactly one matching director, UBO, or intermediary.
3. Replaces a legacy document `person_key` with that party's stable row `id`.
4. Stores the explicit canonical `documents.person_type`.
5. Rebuilds the slot as
   `person:{person_type}:{party_id}:{document_type}` only when the stored
   `documents.doc_type` is already the exact canonical document type.
6. Clears `is_fixture` only for the exact approved S01, S02, S03, S07, and
   S09 July 2026 application identities when a stale marker is present. The
   read-only pricing-case audit confirmed stale markers on S01/S02/S09;
   S03/S07 remain contingency scope because their reused refs were also
   targeted by the historical ref-only migration and require separate
   diagnosis before any apply.
7. Writes one hash-chained, non-PII audit event per changed application in
   apply mode.

The utility does not change party rows, profile values, ownership percentages,
document categories, files, verification results, workflow status, or client
sessions.

## Refusal conditions

The whole requested apply is atomic and is refused if any selected application
has:

- a missing or duplicate application ref;
- a reserved or unclassified fixture identity;
- a duplicate party `id`/`person_key` alias;
- an unresolved or ambiguous document owner;
- an invalid or conflicting stored `documents.person_type`;
- a stored document category that is an alias or noncanonical spelling
  (`normalize(doc_type) != doc_type`); category normalization requires a
  separate reviewed migration;
- a document category/person-type combination that the ordinary upload policy
  rejects, including an unknown category;
- a document owner, person type, or document category that conflicts with its
  existing slot metadata;
- a malformed or non-person slot on a person document;
- a person slot with no `person_id`; or
- two current documents that would occupy the same canonical slot.

Historical versions may share a slot when no more than one is current.

## Read-only diagnostic

From `arie-backend`, with the staging database connection available:

```bash
ENVIRONMENT=staging \
REGMIND_STAGING_DATABASE_FINGERPRINT=<pre-approved-64-character-sha256> \
python scripts/repair_kyc_party_document_links.py \
  --application-ref ARF-2026-100421 \
  --application-ref ARF-2026-100430 \
  --application-ref ARF-2026-100423 \
  --application-ref ARF-2026-100425 \
  --application-ref ARF-2026-100427 \
  --application-ref ARF-2026-100426 \
  --dry-run
```

Omitting `--dry-run` is also read-only. The positive staging identity is
required before the CLI opens a database connection in either mode, preventing
an opaque production URI from being queried even read-only. Save the JSON
output in the controlled P0 evidence location and review every application,
party, document, proposed value, and refusal. The output contains internal row
identifiers but no party names or profile fields.

Expected pre-apply conditions:

- `outcome` is `ready` (or `no_changes`);
- `summary.refusal_count` is zero;
- every changed document has the intended `resolved_party.person_type`;
- every proposed `person_id` is the intended party row ID;
- every changed document's stored `doc_type` exactly equals the final slot's
  category segment;
- in this six-pricing-case command, only S01, S02, and S09 may show an
  `is_fixture: true -> false` change. S03/S07 are allowed only when those
  exact identities are explicitly selected in a separately reviewed run.

Any refusal requires investigation. Do not edit the report or bypass a refusal.

## Controlled staging apply

Apply mode requires the literal environment and confirmation phrase:

```bash
ENVIRONMENT=staging \
REGMIND_STAGING_DATABASE_FINGERPRINT=<pre-approved-64-character-sha256> \
python scripts/repair_kyc_party_document_links.py \
  --application-ref ARF-2026-100421 \
  --application-ref ARF-2026-100430 \
  --application-ref ARF-2026-100423 \
  --application-ref ARF-2026-100425 \
  --application-ref ARF-2026-100427 \
  --application-ref ARF-2026-100426 \
  --apply \
  --confirm APPLY_STAGING_KYC_PARTY_DOCUMENT_REPAIR
```

The fingerprint is the SHA-256 of the credential-free canonical staging
identity `postgresql://<host>:<port>/<database>`. It must come from the
independently reviewed staging inventory or change record. Do not derive it
from whichever target happens to be in `DATABASE_URL` immediately before an
apply, because that would defeat the positive identity check.

Explicit approval is required before running this command. Never run it against
production. The utility rejects non-staging environments, non-PostgreSQL
connections, production-like database identities, a DSN/database-name
mismatch, multi-host authorities, target-affecting libpq URI overrides (for
example `host`, `hostaddr`, `port`, `dbname`, or `service` query parameters),
ambient target-changing libpq variables (`PGHOST`, `PGHOSTADDR`, `PGPORT`,
`PGDATABASE`, `PGSERVICE`, and `PGSERVICEFILE`), any difference between the
effective live connection host/port/database and the fingerprinted URI, and a
missing or mismatched positive staging fingerprint. Benign TLS/transport
parameters such as `sslmode` do not change the fingerprint. Clear any listed
`PG*` variables rather than copying their values into the apply environment.
The fingerprint contains no password and the utility never prints the DSN.

## Verification and idempotence

Immediately rerun the read-only diagnostic with the same refs. It must report
`no_changes` and zero refusals. Then verify database, API, portal, and back-office
evidence independently:

- `documents.application_id` remains unchanged;
- each person document uses the intended party row ID and typed slot;
- document `doc_type` is unchanged and exactly equals the typed slot's category
  segment;
- file metadata and review/verification state are unchanged;
- S01/S02/S09, plus S03/S07 if separately diagnosed and approved, are no
  longer hidden by a stale fixture marker;
- party IDs remain stable through another save and clean login; and
- portal/back-office party and document associations agree.

Retain both JSON reports and the inserted audit events with the P0 evidence.

## Rollback

The pre-apply JSON report contains every old and proposed `person_id`, `slot_key`,
and fixture marker. If validation fails, stop the journey and use those exact
old values to prepare a separately reviewed, application-scoped rollback.
Do not run a broad inverse update: a document uploaded after the repair must not
be overwritten from stale evidence.
