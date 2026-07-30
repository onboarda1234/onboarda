# PR-MON-M1-STATE-MACHINE-1 — validation report

Date: 2026-07-30
Base SHA: `9fa083c1ac185ea65bfa8515dff315eb254701a5`
State-machine version: `monitoring_alert_state_machine_v1`

## Pre-implementation reconciliation

- Staging serves the base SHA above.
- The reconciled inventory remains 19 Monitoring Alerts.
- Every stored status is in the v1 vocabulary.
- The review-control ledger contains zero rows and zero pending requests.
- The four governed Monitoring flags evaluate OFF.
- No staging row was mutated during schema/data compatibility preflight.

See `STAGING_SCHEMA_PREFLIGHT.md` and `STATUS_MUTATION_INVENTORY.md` for the
query evidence and mutation-path inventory.

## Focused validation

| Lane | Result |
|---|---:|
| Canonical lifecycle and PostgreSQL row-lock tests | 94 passed |
| Routing and Monitoring list API | 49 passed |
| Four-eyes, concurrency, and review-ledger schema | 51 passed |
| Review-ledger migration rerun | 26 passed |
| Static direct-write guard | 14 passed |
| Independent PostgreSQL core matrix | 190 passed |
| All changed/added test files, independent run | 678 passed |
| Audit-chain, audit metadata, and feature flags | 53 passed |

The independent review also passed schema-migration policy, production Python
compilation, the configured fatal flake8 classes, and `git diff --check`.

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
The focused remediation matrix passed **196 tests** with a disposable local
PostgreSQL DSN.

## Protected and full regression

- Protected-module regression: **1,589 passed**, zero skips/xfails.
- Full repository suite with a disposable local PostgreSQL DSN:
  **8,546 passed, 2 expected skips, 4 expected xfails**.
- PDF/evidence-pack coverage ran inside both the protected and full lanes.

## Independent review

Final verdict: **no actionable P0/P1/P2 findings**.

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
  f-string, tuple-assignment, and `MERGE` SQL;
- protected-module behavior; and
- feature-flag boundaries and absence of new workflow consumers.

## Result

`READY FOR DRAFT PR`

All four Monitoring feature flags remain OFF. No document-renewal, Agent 1
refresh-verification, screening-change, or automatic-resolution consumer was
activated.
