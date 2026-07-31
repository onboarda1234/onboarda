# PR-MON-M1-STATE-MACHINE-1 — validation report

Date: 2026-07-31
Base SHA: `2287a0ce2992f8941dbe0be49e86b23fe3272962`
State-machine version: `monitoring_alert_state_machine_v1`

## Pre-implementation reconciliation

- The point-in-time read-only staging preflight served
  `b5fb23276bacfc8aa543f5e31963e253c3ff8ab8` from immutable backend task
  definition `regmind-staging:989`. `STAGING_SCHEMA_PREFLIGHT.md` records that
  observation rather than relabelling it as current after main advanced.
- A fresh pre-merge read-only revalidation against deployed immutable SHA
  `307b0d7bfc6ab855837f1c9b01f6d182748b8f2a` reconfirmed the same 19-alert
  canonical inventory, empty review ledger, migration-053 schema, absent future
  constraint/columns, and all governed flags defaulting OFF.
- The latest captured read-only revalidation against then-current main SHA
  `85c70431a2d2a2f4bd6dd3078257d5f22d92bad4` observed 22 alerts after
  attributable runtime ingestion. Two new canonical `open` rows came from the
  existing ComplyAdvantage historical subscription-seed backfill and one from
  the existing live webhook path. CloudWatch recorded nine successful
  subscription-seed backfills collectively reporting two inserts and one
  update, plus one live create, with no failure, `ERROR`, or `CRITICAL` event
  in the creation window. These paths predate this PR and have no governed-flag
  consumer.
- The latest reconciled inventory is 22 Monitoring Alerts; all are canonical,
  the review ledger remains empty, and schema version remains 053.
- Every stored status is in the v1 vocabulary.
- The review-control ledger contains zero rows and zero pending requests.
- The four governed Monitoring flags evaluate OFF.
- No staging row was mutated during schema/data compatibility preflight.
- The prior alert-610 whole-row fingerprint drift is limited to `reviewed_at`;
  its canonical `open` status and state-machine compatibility are unchanged.

See `STAGING_SCHEMA_PREFLIGHT.md` and `STATUS_MUTATION_INVENTORY.md` for the
query evidence and mutation-path inventory.

## Rebase and release-control reconciliation

- The branch was first rebased from
  `9fa083c1ac185ea65bfa8515dff315eb254701a5` onto hardened main
  `b5fb23276bacfc8aa543f5e31963e253c3ff8ab8`, then cleanly rebased again
  onto `7251bd7f24e6d4be87cc3d15566a219d3c1a12a4`, then onto
  `85c70431a2d2a2f4bd6dd3078257d5f22d92bad4`, and finally onto current main
  `2287a0ce2992f8941dbe0be49e86b23fe3272962`.
- `git range-diff` proved all nine PR commits patch-identical across the final
  rebase. The first intervening main set changed the Periodic Reviews
  back-office label/test and added architecture documents. The final three
  commits changed risk-presentation HTML and three focused tests. Neither set
  overlapped the state-machine patch; all main-side expectations are preserved
  and included in the post-rebase validation lane.
- The final intervening PR #912 added only isolated
  `supervisor_foundation/` modules/tests and updated an architecture document.
  It has zero file overlap with this PR; all nine PR commits remained
  patch-identical through the conflict-free rebase.
- The combined test tree exposed two cross-PR guard assumptions. PR #912's
  historical file-identity proof compared every future branch with
  `origin/main`; it is now anchored to the immutable full PR #912 base and
  final head, preserving all 18 paths in the original proof without rejecting
  later work. Its `_fetchall` helper is now constrained at runtime to an exact
  nine-query read-only manifest and remains one exact, counted exception in the
  Monitoring SQL guard. Multi-statement, unknown, CTE, and non-manifest SQL is
  rejected before reaching the driver, while the foundation's independent
  no-write suite remains authoritative.
- The sole overlapping file was `arie-backend/server.py`; the resulting file
  preserves main's strict `IMAGE_TAG` lookup without a `GIT_SHA` fallback.
- Migrations 054–056 do not collide with main, whose latest migration is 053.
- No release workflow, deployment evidence, container-security, Docker, or
  protected-manifest file changed in this PR.

## Focused validation

| Lane | Result |
|---|---:|
| Post-rebase PR-delta, feature governance, lifecycle label, and risk-presentation integration | 826 passed |
| Protected-module regression (73-file manifest) | 1,601 passed; zero skips/xfails |
| PostgreSQL append-only grants | 5 passed |
| Protected inventory contracts | 17 passed |
| Static direct-write guard | 26 passed |
| PR #912 foundation compatibility | 178 passed |
| Release-control compatibility | 86 passed |
| Independent final runtime focus | 210 passed; 3 PostgreSQL cases deselected and covered by the PostgreSQL-enabled root lanes |
| Full repository suite | 8,661 passed; 3 expected skips; 4 expected xfails |
| Dedicated PDF lane | 8 passed |

Schema-migration policy, production Python compilation, the configured fatal
flake8 classes, and `git diff --check` passed. The three full-suite skips are
the optional Playwright import and two legacy tests gated on the separate
`ARIE_PG_TEST_DSN` fixture contract; PostgreSQL state-machine, constraint,
locking, routing, canonical-dataset, fresh-install, audit-chain, and grants
tests ran through `TEST_POSTGRES_DSN`. Local Docker validation was unavailable
because the desktop Docker daemon is not running; the hardened required
GitHub Docker and exact-image vulnerability lanes remain mandatory before
merge.

## GitHub review remediation

After the draft PR was marked ready, CodeRabbit completed an actual review of
the then-current 48-file diff. Its four actionable threads were reconciled as
follows; the later 49-file exact tree received a separate independent review:

- PostgreSQL status-constraint verification now parses a narrow, fully
  consumed semantic grammar instead of requiring one exact
  `pg_get_constraintdef` rendering. Equivalent lossless TEXT/VARCHAR cast
  placement and `NOT VALID` recovery are accepted; different operators,
  values, columns, functions, collations, or trailing clauses still fail
  closed.
- Monitoring ownership deliberately continues to inspect every linked
  document requirement. Selecting only the latest row would hide corrupt or
  cross-application history; duplicate alert-level links remain a controlled
  reconciliation error and now have an explicit regression test.
- Public state-machine exports are deterministically sorted.
- Triage, assignment, Periodic Review reuse, and EDD reuse responses now
  return the same `state_machine_version` field as their changed paths.

Additional safety-oriented review findings hardened bare-name, keyword, and
aliased SQL executor detection (including chained aliases), narrowed
PostgreSQL exception assertions, and corrected the four-eyes import boundary.
The rebased validation above re-exercised those remediations with disposable
local PostgreSQL databases.

### Post-publication review pass

The rebased head `795cea0ec426f29aae606c4e4efd653393596f9c`
completed every then-required GitHub check: full lint/test, protected-module
regression, Docker validation, PDF, two exact-SHA container-security runs, and
CodeRabbit. The fresh CodeRabbit review opened ten threads. Seven were valid
and were fixed before merge:

- document outcomes now bind exactly to their governed requested action and
  any non-accept/non-waive helper invocation fails closed;
- enhanced-requirement synchronization proves both alert identity and locked
  application identity before any document, alert metadata, or audit write;
- unknown, blank, and NULL stored statuses action-lock instead of permitting a
  late failure;
- review-request early returns explicitly roll back after the alert lock;
- indirect fixture-helper inspection resolves positional, keyword, qualified,
  and aliased calls and fails closed on unresolved call shapes;
- function decorators, defaults, annotations, returns, and type-parameter
  expressions are inspected in their enclosing scope by the SQL guard; and
- the two staging observations and historical `cancelled`/`canceled` read
  aliases are documented unambiguously.

Three suggestions were independently verified as non-actionable and were not
applied. List action ownership must use the raw stored status, not the derived
display key, or a blank invalid value would be reopened as display `open`.
`review_document_refresh` has no `commit` parameter and contains no commit, so
the caller already owns the single transaction. Finally, closing the database
would already release the review lock; the explicit rollback was retained as
harmless defense-in-depth.

Post-fix validation on Python 3.11:

- focused production/API set: **73 passed**;
- static direct-write guard: **26 passed**;
- expanded affected lane with PostgreSQL: **591 passed**;
- permanent protected-module regression: **1,601 passed**, zero skips/xfails;
- production compilation and `git diff --check`: passed; and
- independent post-fix review: **P0=0, P1=0, P2=0, P3=0**, no actionable
  finding.

The superseded green CI result is not merge evidence for the review-fix commit.
Every required GitHub check, including the exact-image vulnerability gate,
must rerun and pass on the new head before merge.

## Protected expectation reconciliation

The permanent protected manifest remains unchanged at 73 files. Six
manifest-listed test files have intentional expectation changes in this PR:

- `test_complyadvantage_webhook_storage.py`
- `test_lifecycle_linkage.py`
- `test_monitoring_refresh_status_decoupling.py`
- `test_monitoring_routing.py`
- `test_monitoring_alerts_sprint2_api.py`
- `test_monitoring_status_model.py`

Those changes replace legacy Monitoring status/write assumptions with the
approved v1 lifecycle contract and add fail-closed linkage assertions. They do
not relax Applications, KYC & Documents, Screening Queue, Screening Review,
RSMP, EDD, Periodic Review, or Change Management behavior. The manifest,
protected runner, skip/xfail prohibition, and every unrelated protected
expectation remain unchanged.

A fresh independent review identified two related P1s in inherited EDD-routing
behavior. A second Monitoring Alert could ignore an active EDD owned by another
alert and create a duplicate case for the same application; the protected
expectation had incorrectly asserted that two active EDD cases were acceptable.
The first remediation then exposed that claiming a formally sourced but
unlinked EDD would overwrite its original workflow provenance.
The implementation now serializes routing on the application row, locks and
inspects every active EDD case, reuses only an already-consistent case linked
to the same alert, and fails closed for an unlinked case, another owner,
multiple cases, malformed stage/provenance, or incomplete reverse linkage.
Parameterized provenance-preservation, sequential ownership-conflict,
multiple-case, malformed-stage, and real PostgreSQL concurrency regressions
prove that one active EDD case is preserved without stealing ownership or
rewriting another workflow's evidence.

The same review then found two document/assignment P1s, additional
API/static-guard P2s, and evidence/operability P3s:

- controlled document acceptance/waiver paths could bypass the maker-checker
  ledger through direct actions, aliases, enhanced-requirement sync, or a
  direct service call;
- metadata-only reassignment did not re-run canonical assignee-role
  validation;
- SQL literals/comments and stale Python string bindings could conceal a
  direct status write from the static guard;
- `escalated` → `in_review` had no reachable API reason/evidence contract;
- replaying `escalate_to_sco` could create metadata/audit churn; and
- oversized text evidence and the human transition reason could be silently
  truncated;
- a cross-application enhanced-requirement route could create a pending control
  row before the route application was proven;
- a stale linked document during approved execution surfaced a generic 500
  instead of a controlled refusal with blocked-attempt evidence;
- control-flow, nested-subquery, multi-statement/CTE, SQLite conflict/replace,
  and PostgreSQL inheritance-table SQL forms could evade static inspection; and
- an oversized pending review rationale or typed identifier could persist a
  ledger row that could never be approved.

The remediation now performs document-control preflight before any linked-row
mutation, binds the ledger to the exact canonical outcome and typed evidence,
dispatches approved requests back through the document service, enforces the
same service-level backstop, validates every assignee authoritatively, masks
SQL literals/comments, tracks nested boundaries and every control-flow binding,
inspects every executable statement and supported PostgreSQL/SQLite write form,
exposes the documented senior acknowledgement path, rejects same-state
escalation replay, scopes enhanced requirements before preflight, maps stale
approved document evidence to a safe refusal, and rejects oversized evidence,
reasons, or control-ledger text before any write. Focused
service/API/atomicity/guard regressions pass as recorded above.

## Protected and full regression

- Protected-module regression: **1,601 passed**, zero skips/xfails.
- Full repository suite with a disposable local PostgreSQL DSN:
  **8,661 passed, 3 expected skips, 4 expected xfails**.
- Dedicated PDF lane: **8 passed**, zero skips/xfails.
- PDF/evidence-pack coverage also ran inside the protected and full lanes.

## Independent review

The fresh independent review found both EDD-routing P1s described above and
blocked the merge after each discovery. Both have been remediated, including
real PostgreSQL contention coverage. Follow-up independent passes found and
blocked every document, RBAC, static-guard, route-scoping, error-mapping, and
text-bound issue described above. The exact post-rebase code/test tree has no
remaining P0/P1/P2/P3 finding; the committed evidence cross-check is the final
review handoff before publishing.

The review specifically rechecked:

- canonical vocabulary and the 42 enabled transition rules;
- downstream ownership boundaries and evidence-bound handoffs;
- terminal-state and replay behavior;
- authoritative maker/checker and executor identity;
- current evidence, source-state, outcome, rationale, and typed-evidence
  binding;
- audit atomicity and persisted-row verification;
- PostgreSQL locking, stale-state handling, and race behavior;
- database `CHECK`/`NOT NULL` protection;
- exact partial-index table/key/predicate plus PostgreSQL
  `indisunique`/`indisvalid`/`indisready`;
- direct-write guard coverage for unresolved, concatenated, augmented,
  f-string, tuple-assignment, `MERGE`, literal/comment masking, and stale
  binding SQL;
- protected-module behavior; and
- feature-flag boundaries and absence of new workflow consumers.

## Result

`LOCAL VALIDATION PASS — CI, DEPLOYMENT, AND STAGING QA PENDING`

All four Monitoring feature flags remain OFF. No document-renewal, Agent 1
refresh-verification, screening-change, or automatic-resolution consumer was
activated.
