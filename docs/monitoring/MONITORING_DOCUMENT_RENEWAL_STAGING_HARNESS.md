# Monitoring Document Renewal Staging Harness

Version: `monitoring_document_renewal_staging_harness_v1`

## Purpose and boundary

This harness validates the request and upload-binding contracts introduced by
PRs #920 and #921 against the real staging HTTP surfaces. It is staging-only,
synthetic-only and disabled during ordinary runtime. It does not add a
production fixture, a customer-visible feature, or a general-purpose scheduler.

The harness proves only this bounded flow:

`expired fixture document -> renewal request -> portal projection -> candidate upload -> exact binding -> Monitoring “Upload Received” projection`

It never invokes Agent 1, verifies a candidate, inserts or replaces a canonical
document, changes the fixture Monitoring Alert from `open`, performs screening,
or detects a material change. KYC & Documents remains the owner of the
canonical document and every verification decision.

## Permanent governed fixture

The reviewed data-only manifest is
`arie-backend/fixtures/document_renewal_staging_fixture_v1.json`. Its SHA-256
is evaluated from the checked-out release and is included in every seed and
activation gate.

| Object | Stable identity |
| --- | --- |
| Fixture | `document-renewal-expired-v1` / `FIX_DOC_RENEWAL_STAGING_V1` |
| Synthetic client | `f1xedrnwcli00001` |
| Synthetic application | `f1xedrnwapp00001` / `FX-DOC-RENEWAL-001` |
| Synthetic person | `f1xedrnwper00001` |
| Expired canonical document | `f1xedrnwdoc00001`, passport version 1, expired `2025-01-01` |
| Monitoring Alert | exact source `document:f1xedrnwdoc00001`, canonical status `open` |
| Renewal request | `fxrnwreq00000001` |

The synthetic client is permanently `inactive`, uses the reserved
`fixture.invalid` email domain and has no usable password. The application is
marked `is_fixture` and carries the fixture key, marker, version, manifest hash,
synthetic/non-production assertions and source in its fixture metadata. Normal
list surfaces therefore exclude it unless an authorised officer explicitly
opts into fixture visibility.

The fixture seeder is not registered with broad `seed_all`. Its dedicated CLI:

- refuses production and development;
- requires the exact staging allow variable, confirmation token and reviewed
  manifest hash for apply;
- treats an exact existing fixture as an idempotent no-op;
- refuses partial state, identifier collisions, extra same-application people,
  documents, alerts, notifications, Agent 1 jobs/executions and baseline drift;
- makes dry-run strictly read-only and never initializes or migrates a schema;
- creates no renewal request, upload, binding or workflow state.

The permanent roots are preserved after validation. Only their synthetic
operational descendants are temporary.

## Fixture-only scheduler

`run_fixture_only_renewal_eligibility` is a separate, non-parameterised entry
point. It loads the reviewed fixture manifest internally and proves the exact
client, application, person, current document, expiry, alert, linkage and
request identity before delegating one canonical request creation.

It scans exactly one alert, returns deterministic cardinality, does not read or
advance the global eligibility cursor and does not scan any non-fixture row.
While harness mode is configured, the ordinary eligibility and reminder
schedulers are prohibited and are not registered. Supplying no fixture scope
or a drifted/ambiguous fixture fails closed.

## Temporary activation

The governed flag `ENABLE_DOCUMENT_RENEWAL_AUTOMATION` remains `OFF` in the
ordinary backend and worker task definitions. The manual workflow
`.github/workflows/document-renewal-staging-harness.yml` temporarily deploys a
backend task definition cloned from the exact live staging revision. It changes
only the reviewed harness lease values and sets the other three governed
Monitoring flags explicitly `false`. The verification worker is never enabled
or replaced for this activation.

The lease is valid only when all of the following agree:

- environment is exactly `staging`;
- workflow ref is `refs/heads/main` and release/image/GIT SHA values are the
  same full immutable commit SHA;
- the run ID and UTC issue/expiry timestamps are valid and bounded;
- the manifest hash matches the checked-out governed fixture;
- renewal is the only raw governed flag set true;
- every database write resolves to the exact fixture application, alert or
  deterministic request.

The workflow issues both timestamps from one UTC epoch with a hard 28-minute
(1,680-second) lease, below the 30-minute runtime maximum. Before the dedicated
three-minute browser window opens, at least four minutes must remain or the
workflow fails closed and restores the original backend.

Malformed scope refuses startup. Lease expiry makes the evaluated feature OFF
even if ECS restoration is delayed. The workflow shares the staging deployment
concurrency lock, captures the exact original backend task definition and uses
an `always()` restoration path. Recovery is allowed to restore only a tagged,
expired harness revision to its recorded original revision; it cannot select a
new configuration.

No operator manually edits an ECS task definition. Registering, activating,
stabilising, restoring and verifying the temporary revision are all recorded
workflow actions.

## Real-route validation

The active window uses short-lived in-memory machine tokens derived inside the
approved release-evidence boundary. Token and secret values are never printed,
placed in arguments or uploaded. Redirects are prohibited so an Authorization
header cannot leave the exact staging host.

The officer token uses the existing active, no-login staging QA identity
`github-actions:day6-staging-smoke`. The permanent synthetic client remains
`inactive` with password marker `fixture-no-login`. During the exact active
lease only, a token carrying the reviewed harness purpose and exact run claim
may authenticate that inactive client on exactly two routes: the fixture
renewal-list `GET` and fixture renewal-upload `POST`. Every unrelated client
route, wrong method, wrong run/purpose, expired lease or identity drift retains
the ordinary inactive-client denial. Each successful fixture-client route
authorization appends a canonical hash-chained audit entry; failure to append
that evidence denies the request.

The validator performs bounded fixture requests only:

- authenticated `GET /api/version` and read-only Monitoring flag status;
- the exact synthetic client portal renewal list;
- one accepted synthetic PDF upload through the real portal route;
- one duplicate upload that must fail with the controlled 409 contract;
- the exact Monitoring list/detail projection.

Success requires one request, one binding, portal/Monitoring
`upload_received`, and an alert that remains `open`. The uploaded bytes contain
no identity or customer data.

## Fingerprints and allowed differences

Before activation, after upload and after cleanup, the harness captures
canonical SHA-256 descriptors for:

- fixture client, application, person, canonical document and Monitoring
  Alert;
- the complete fixture-application footprint: all directors, UBOs,
  intermediaries, documents, alerts, unrelated notifications and legacy
  requirements;
- renewal requests, events, uploads, bindings, cleanup ledger and portal
  notifications, plus the exact run/fixture/customer rate-limit row;
- the global renewal scheduler state;
- all non-fixture Monitoring alerts, documents, Agent 1 executions,
  verification jobs and renewal operational rows;
- fixture Agent 1 executions and queued verification jobs (both must remain
  zero), plus any stale/concurrent harness-shaped limiter key (must remain
  zero);
- fixture renewal audit action/count evidence and the canonical global audit
  chain integrity/coverage summary;
- evaluated governed feature states.

Raw rows, object keys, file names, hashes, notification text, credentials and
audit details are not emitted as release evidence. The after fingerprint may
contain only the expected one-request/one-upload synthetic graph. Final
reconciliation requires:

The three explicit before/after/final snapshots intentionally read the full
non-fixture relations to prove global isolation. Those scans never run while
the cleanup advisory/row locks are held, remain subject to the staging database
statement timeout, and fail the harness closed if exact evidence cannot be
captured. Streaming/aggregate descriptors are a future operator-performance
optimization; they are not used to weaken the full-isolation contract here.

- permanent fixture roots byte-equivalent by canonical digest;
- every manifest-defined root field remains exact and the fixture application
  still has exactly one current canonical document, one alert and one person,
  with zero Agent 1 executions or verification jobs;
- no renewal request, event, upload, binding, cleanup-ledger row or portal
  notification or exact harness limiter remaining for the fixture;
- every non-fixture digest unchanged;
- global scheduler state unchanged;
- all four governed flags OFF;
- the validation audit delta is exactly one each of `request_created`,
  `request_sent`, `artifact_reserved`, `artifact_stored`, `upload_received`,
  `upload_bound` and `duplicate_upload`, plus exactly four
  `fixture_client_auth` entries for the two portal reads and two upload
  attempts;
- cleanup adds exactly one `fixture_cleanup` action and no other renewal audit
  action;
- every new audit row is hash-chained, the canonical audit chain verifies,
  coverage remains complete, and there are zero coverage gaps or broken links.

Concurrent or unexpected staging drift therefore fails validation rather than
being explained away.

## Cleanup and rollback

Cleanup is an operator-only module and is not imported by the web server. It
requires the exact fixture identity, a deterministic approved plan fingerprint,
an explicit confirmation, all governed flags OFF and the approved staging
PostgreSQL identity fingerprint. It takes a transaction-scoped advisory lock
and exact row locks, then re-creates the plan before deleting anything.

The permitted graph is at most one exact deterministic request ID, its expected ordered events,
one exact notification, one exact composite rate-limit row, and a one-to-one
upload/binding/artifact ledger. Any orphan, duplicate, unexpected status,
linkage mismatch, request-ID collision, additional notification or non-exact
limiter refuses cleanup. The exact limiter may be removed as soon as the original non-harness
backend is healthy and all governed flags are OFF; cleanup never waits on or
deletes an unrelated limiter window.

The exact artifact-ledger row is locked before plan reconstruction or object
I/O. An active, malformed or ownerless artifact lease fails closed. A stranded
expired upload/reconciler lease may be recovered only after a five-minute
safety margin under that exact row lock, preventing overlap with the ordinary
artifact reconciler.

For S3, cleanup inventories every version and delete marker for the exact
reserved candidate key. It requires the one recorded current version and exact
request/upload/cleanup ownership metadata, deletes only that version, and then
proves that no version or delete marker remains. An unavailable inventory,
hidden version, ambiguous ownership or missing permission fails closed. Local
candidate deletion is test-only and constrained to the reserved directory and
exact upload filename.

Before any staging S3 call, the canonical client must resolve exactly to bucket
`regmind-documents-staging` in `af-south-1`; configuration drift fails before
inventory or deletion. A reservation interrupted before the object version was
recorded is recoverable only when exact-key inventory and object metadata prove
one owned current candidate (or prove the key has no versions or delete
markers).

The workflow temporarily adds a separate inline IAM policy permitting only
`s3:ListBucketVersions` for the exact synthetic client/request prefix and
`s3:DeleteObjectVersion` for objects beneath that prefix. It refuses a
pre-existing or drifted policy, verifies the exact statement set before
cleanup, removes the exact policy in an unconditional teardown step, and never
changes the ordinary backend task-role policy.

Database rows are deleted in foreign-key order within the sanctioned synthetic
fixture cleanup context. One canonical hash-chained cleanup audit entry is
appended atomically; existing audit evidence is never removed. If object
deletion succeeds but the database transaction fails, an unchanged plan can
resume safely. A second completed cleanup is an idempotent no-op and must not
create duplicate audit evidence.

The main harness preserves its registered/tagged temporary task definition as
the dead-man recovery handle unless backend restoration, exact cleanup,
before/after/final reconciliation, and temporary-IAM removal all succeed. Only
after those gates does it wait for the exact lease to expire, revalidate the
run ID, release SHA and task-definition identity, deregister that revision and
remove the five harness tags. A failure or cancellation before completion
therefore leaves the discoverable handle intact for the independent recovery
workflow. Before registration, global tag discovery and inspection of every
running backend-family task definition must prove that no earlier harness lease
or task remains. After retirement, the workflow polls the global tag index to
zero and re-runs the typed live-task residue verifier before it may record
success. Successful retirement therefore cannot collide with a later harness
run or conceal a still-running harness revision.

The ordinary staging deployment renderer also fails closed when its live
source task definition contains the harness marker or any
`DOCUMENT_RENEWAL_STAGING_HARNESS_*` lease field. A queued deployment may not
clone, retag or overwrite an active harness revision; recovery must restore the
ordinary OFF task definition first. The deploy path never strips or migrates a
lease silently.

## Operator prerequisites and recovery

The GitHub `staging` environment must contain the existing AWS deployment
credentials and a secret named
`DOCUMENT_RENEWAL_HARNESS_STAGING_DATABASE_FINGERPRINT`. The value is the
lowercase SHA-256 of the credential-free canonical identity
`postgresql://<lowercase-host>:<port-or-5432>/<database>`. An authorised
operator derives it from the actual staging `DATABASE_URL` with shell tracing
disabled and the secret JSON supplied to the canonicalisation script through
stdin; the URI, username and password must never be printed or written to a
file. The resulting 64-character digest is supplied to `gh secret set` through
stdin for the `staging` environment. Repository or workflow variables are not
an acceptable substitute.

The independent
`.github/workflows/document-renewal-staging-harness-recovery.yml` workflow is
the governed recovery path. It runs after a failed/cancelled harness and on a
bounded schedule under the same `deploy-staging` concurrency lock, with
`cancel-in-progress: false`. Its exact sequence is:

1. From the current recovery workflow code, perform read-only AWS discovery
   before any strict deployment-state assertion. Query globally for the exact
   `RegMindHarness=document-renewal-staging-harness-v1` tag, describe every
   returned revision with `--include TAGS`, and inventory running task
   definitions. Zero or one exact lease is permitted; ambiguity or tag drift
   fails closed. An event/manual run ID is an ownership assertion and never a
   discovery filter.
2. Derive the immutable release SHA from the exact tagged lease (or from the
   current OFF task definition when no lease exists). The bootstrap checkout
   contains full history and proves that SHA names a commit in current
   `origin/main` history. A failed-workflow event must identify the same tagged
   lease SHA; when no lease was registered, its head must be an ancestor of the
   selected current release. Only then does recovery perform a second
   `actions/checkout` pinned to that full SHA. All repository-owned recovery,
   snapshot and cleanup tools run from this immutable checkout. Movement of
   `main` therefore cannot strand an older lease, select an unreviewed commit,
   or cause older controls to restore a newer service backwards.
3. If the candidate says `wait`, wait only for its reviewed lease expiry and
   re-read ECS. No active revision is restored early. If the service still
   selects the exact expired lease, validate its recorded original and restore
   `regmind-backend` only. If the service is already on an OFF release, leave
   it there. Retry the typed ECS stable waiter for transient rollout
   convergence and require all tasks on the temporary revision to drain. The
   worker is verified but never updated.
4. Only after convergence, capture strict backend/worker/image/ALB evidence,
   prove both the live backend and cleanup task definition are OFF, and perform
   authenticated HTTP verification. The cleanup database fingerprint is
   validated only after restoration is complete and immediately before any
   snapshot or cleanup task. A missing or invalid fingerprint still blocks all
   database access, but cannot block ECS restoration or the unconditional
   removal of temporary IAM. Every invocation then runs an OFF
   `snapshot-off`: an unowned scheduled run uses its own `gh-<run>-<attempt>`
   probe identity, so absence of a tagged candidate can never become an
   unchecked no-op.
5. When exact run ownership exists, verify or install only the fixed temporary
   cleanup IAM policy, run exact cleanup, capture a final `snapshot-off`, and
   reconcile it. With or without cleanup, the final clean verifier requires
   the exact fixture roots/cardinalities, zero renewal operational rows, zero
   same-fixture and other-run harness rate limits, zero Agent 1 or verification
   jobs, all governed flags OFF, and an intact audit chain.
6. Preserve the registered and tagged temporary revision as the cancellation
   recovery handle until cleanup and final clean-fingerprint proof succeed.
   Then remove the exact temporary IAM policy, revalidate and deregister only
   that revision, and remove its five exact harness tags. The immutable
   recovery metadata remains in the uploaded evidence artifact rather than in
   a future-discovery tag. Final proof requires the live backend OFF, zero
   globally tagged harness revisions, and zero running task definitions whose
   tags or lease environment identify them as harness tasks.

If the backend was already restored before cancellation, global tag discovery
still finds the exact residual lease without requiring a supplied run ID. A
scheduled run with no candidate must prove the full clean baseline and exact
OFF runtime; dirty state without an attributable lease fails closed and
retains evidence for operator investigation. Recovery never sweeps a
task-definition family, never mutates the worker, and never reports success
until IAM, runtime, database fingerprint and ACTIVE/running residue gates all
pass.

A manual recovery dispatch may supply only the exact `gh-<run>-<attempt>`
identity to reconcile already-restored residue. It does not broaden task,
fixture, IAM or database scope. Any tag drift, original-task mismatch, worker
change, non-OFF flag, plan drift, artifact ambiguity or audit-chain failure
stops recovery and requires operator review.

## Required evidence and failure policy

The workflow is not successful unless it records sanitized evidence for the
reviewed/deployed SHA, original/temporary/restored task definitions, exact
fixture seed result, activation ON/OFF states, before/after/final fingerprints,
HTTP and binding assertions, cleanup plan/result, ECS and ALB health, and the
relevant CloudWatch window. Recovery records its CloudWatch window start at
workflow initialization, before discovery or restoration, so activation and
rollout failures cannot fall outside the review. A bounded three-minute read-only browser window is
opened only after the expected synthetic graph has been captured; restoration
then proceeds automatically even if no browser operator connects.
The wait/window itself is not browser evidence. Browser acceptance requires a
separate timestamped operator or Claude Chrome result (or screenshots) tied to
the exact run and deployed SHA; without it, closure retains a browser-QA
limitation.

Any failed gate still runs restoration and cleanup where the exact ownership
preconditions remain provable. If safe cleanup cannot be proven, the workflow
reports the exact synthetic residue and leaves it for governed recovery; it
never broadens scope to real data. A harness run is not a reason to enable any
Monitoring flag outside its lease and is not evidence that document
verification or replacement is approved.
