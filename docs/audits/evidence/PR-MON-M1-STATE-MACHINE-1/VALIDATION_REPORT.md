# PR-MON-M1-STATE-MACHINE-1 — validation report

Date: 2026-07-31
Base SHA: `b5fb23276bacfc8aa543f5e31963e253c3ff8ab8`
State-machine version: `monitoring_alert_state_machine_v1`

## Pre-implementation reconciliation

- Staging serves the exact current-main SHA above from immutable backend task
  definition `regmind-staging:989`.
- The reconciled inventory remains 19 Monitoring Alerts.
- Every stored status is in the v1 vocabulary.
- The review-control ledger contains zero rows and zero pending requests.
- The four governed Monitoring flags evaluate OFF.
- No staging row was mutated during schema/data compatibility preflight.
- The prior alert-610 whole-row fingerprint drift is limited to `reviewed_at`;
  its canonical `open` status and state-machine compatibility are unchanged.

See `STAGING_SCHEMA_PREFLIGHT.md` and `STATUS_MUTATION_INVENTORY.md` for the
query evidence and mutation-path inventory.

## Rebase and release-control reconciliation

- The branch was rebased from `9fa083c1ac185ea65bfa8515dff315eb254701a5`
  onto hardened main `b5fb23276bacfc8aa543f5e31963e253c3ff8ab8`
  without a conflict.
- `git range-diff` proved both implementation commits patch-identical after
  the rebase.
- The sole overlapping file was `arie-backend/server.py`; the resulting file
  preserves main's strict `IMAGE_TAG` lookup without a `GIT_SHA` fallback.
- Migrations 054–056 do not collide with main, whose latest migration is 053.
- No release workflow, deployment evidence, container-security, Docker, or
  protected-manifest file changed in this PR.

## Focused validation

| Lane | Result |
|---|---:|
| Rebased PR-delta files plus feature governance | 722 passed |
| PostgreSQL constraint, migration, locking, and routing core | 186 passed |
| Final EDD routing, ownership, and concurrency lane | 53 passed |
| PostgreSQL audit chain and grants | 23 passed |
| Protected inventory contracts | 17 passed |
| Static direct-write guard | 17 passed |
| Final review-remediation focus (SQLite portion) | 169 passed, 1 PostgreSQL-only skip |

Schema-migration policy, production Python compilation, the configured fatal
flake8 classes, and `git diff --check` passed.

## GitHub review remediation

After the draft PR was marked ready, CodeRabbit completed an actual review of
all 48 changed files. Its four actionable threads were reconciled as follows:

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

## Protected expectation reconciliation

The permanent protected manifest remains unchanged at 73 files. Five
manifest-listed test files have intentional expectation changes in this PR:

- `test_complyadvantage_webhook_storage.py`
- `test_lifecycle_linkage.py`
- `test_monitoring_refresh_status_decoupling.py`
- `test_monitoring_routing.py`
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

The same review then found two document/assignment P1s, three API/static-guard
P2s, and one evidence-handling P3:

- controlled document acceptance/waiver paths could bypass the maker-checker
  ledger through direct actions, aliases, enhanced-requirement sync, or a
  direct service call;
- metadata-only reassignment did not re-run canonical assignee-role
  validation;
- SQL literals/comments and stale Python string bindings could conceal a
  direct status write from the static guard;
- `escalated` → `in_review` had no reachable API reason/evidence contract;
- replaying `escalate_to_sco` could create metadata/audit churn; and
- oversized text evidence was silently truncated.

The remediation now performs document-control preflight before any linked-row
mutation, binds the ledger to the exact canonical outcome and typed evidence,
dispatches approved requests back through the document service, enforces the
same service-level backstop, validates every assignee authoritatively, masks
SQL literals/comments and invalidates stale bindings, exposes the documented
senior acknowledgement path, rejects same-state escalation replay, and rejects
oversized text evidence explicitly. Focused service/API/atomicity/guard
regressions pass as recorded above.

## Protected and full regression

- Protected-module regression: **final rerun pending**.
- Full repository suite with a disposable local PostgreSQL DSN:
  **final exact-tree rerun pending**.
- Dedicated PDF lane: **8 passed**, zero skips/xfails.
- PDF/evidence-pack coverage also ran inside the protected and full lanes.

## Independent review

The fresh independent review found both EDD-routing P1s described above and
blocked the merge after each discovery. Both have been remediated and the
resulting ownership, provenance, and concurrency contract passes 53/53
affected tests, including real PostgreSQL contention. The final review verdict
is pending only the completed broad-validation counts and evidence cross-check.

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

`LOCAL VALIDATION IN PROGRESS — FINAL INDEPENDENT REVIEW REQUIRED`

All four Monitoring feature flags remain OFF. No document-renewal, Agent 1
refresh-verification, screening-change, or automatic-resolution consumer was
activated.
