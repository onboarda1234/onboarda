# PR-MON-PROTECTED-BASELINE-1 — protected regression baseline

Status: **BACKUP-ONLY BRANCH PRESERVED — AUTHENTICATED BROWSER GATE BLOCKED**
Captured: 2026-07-27
Scope: regression infrastructure and evidence only; no product behaviour, compliance logic, score, disposition, workflow, database schema/data, staging configuration, or feature activation is changed by this branch.

## Baseline identity

| Item | Recorded value |
|---|---|
| Repository | `onboarda1234/onboarda` |
| `origin/main` / branch base | `726d01d96cbbccddc68f1f665d51848f0f1adc1a` |
| Branch | `codex/pr-mon-protected-baseline-1` |
| Staging `/api/version.git_sha` | `726d01d96cbbccddc68f1f665d51848f0f1adc1a` |
| Staging `/api/version.image_tag` | `726d01d96cbbccddc68f1f665d51848f0f1adc1a` |
| Staging matches `origin/main` | **Yes** |
| Backend task definition | `regmind-staging:980` — desired/running `2/2` |
| Worker task definition | `regmind-verification-worker:428` — desired/running `6/6` |
| Screening provider | ComplyAdvantage Mesh; live sandbox workspace; abstraction enabled; fallback disabled |
| RSMP mapping-fidelity flag | `ENABLE_RSMP_TIER0A_MAPPING_FIDELITY=true` |
| Monitoring dashboard | `ENABLE_MONITORING_DASHBOARD=true` |
| Monitoring automation | Evaluated `true` from the staging/production default; no ECS task override |
| CA re-screen | `ENABLE_CA_RESCREEN=false` (no task override; false default) |
| CA profile hydration | `ENABLE_CA_PROFILE_HYDRATION=false` (no task override; false default) |

The mandatory SHA-alignment gate passed before baseline work began.

## Backup-only preservation status

- Implementation and all non-browser verification are complete.
- The protected implementation commit is
  `685f4731750d84e7e1dde09896080d32357a7034` and remains in branch history.
- `codex/pr-mon-protected-baseline-1` was pushed to `origin` solely as a
  durable backup. The push did not trigger a workflow or staging mutation.
- No pull request was opened. No merge or deployment was performed.
- The authenticated browser gate remains blocked because approved browser
  control is unavailable and the staging administrator credential has not been
  supplied through an approved secure credential mechanism.
- Work must not proceed to PR, merge, deployment, or a later Monitoring Alerts
  workstream until both browser control and secure administrator access are
  available and the protected browser validation passes.

## Authoritative-document audit

Documents reviewed:

- `CLAUDE.md`
- `docs/REMEDIATION_MASTER_LIST.md`
- `docs/compliance/screening_queue_module_card.md`
- `docs/compliance/REMEDIATION_CLOSURE_EVIDENCE.md`
- `docs/risk-programme/RSMP_GATE0_V4_FOUNDER_APPROVAL.md`
- `docs/risk-programme/RSMP_LIVE_CONFIG_DISPOSITION.md`
- `docs/risk-programme/RSMP_TIER0D_RUNTIME_UI_RECONCILIATION.md`
- `docs/architecture/sumsub-complyadvantage-provider-model.md`
- `docs/adr/0009-lifecycle-periodic-review-architecture.md`
- `arie-backend/docs/lifecycle_linkage_pr01.md`
- `docs/compliance/FEATURE_FLAG_LIFECYCLE.md`
- `docs/pilot/PILOT_CANONICAL_DATASET.md`
- `docs/qa/CM-E2E-PILOT-READINESS-1-report.md`

### Reconciled module status

| Module | Baseline status | Preservation interpretation |
|---|---|---|
| Application Review | **PILOT-READY — FROZEN** since 2026-07-16 | Detail load, seven tabs, memo loop, gates, decisions, permissions, and exports must remain output-equivalent. |
| KYC & Documents | Protected as part of frozen Application Review | Canonical/current documents, party linkage, verification display, download linkage, isolation, and outcomes must not regress. No document-model redesign is authorized. |
| Screening Queue | **VALIDATED — CHANGE-CONTROLLED** since 2026-07-17 | Queue state axes, truth labels, filters, pagination, fixture exclusion, evidence hydration, and caps are frozen unless separately approved. |
| Screening Review | Established SRP-3/4 implementation is protected; overall workflow still has open work | Current disposition, four-eyes, analyst RBAC, evidence, and re-screen behaviour are regression-protected. This baseline does not claim the entire workflow is pilot-ready. |
| RSMP | Tier 0A/0B/0D and explainability work merged; wider RSMP remains an **active workstream** | Current D1–D5 evidence, score/tier, floors, routes, configuration version, fail-closed mapping, exports, and backend ownership are authoritative and unchanged. |
| Change Management | Existing QA evidence is PASS; not declared frozen | Workflow ownership, materiality, officer decisions, application linkage, audit reconstruction, and implementation propagation are protected. |
| EDD | Active protected workflow; not declared frozen | EDD stages/outcomes and application linkage remain EDD-owned. Monitoring may link or route a signal but may not own the decision. |
| Portal upload / client ownership | Existing security controls are protected | No cross-tenant access, document leakage, or portal bypass may be introduced. |
| Monitoring Alerts | **Active Phase 8 workstream** | M2.3 is scoped; M1.2/M2.4/M3.2/M3.3/M4.x remain pending; M1.3 depends on M1.2; M3.4 is approved but not implemented here. |

### Explicit contradictions and drift

These are reported, not silently resolved:

1. `CLAUDE.md` says Screening Review SRP-3/4 is “actively developed” and gates the end-to-end verdict. The remediation register records SRP-3 and SRP-4 closed on 2026-07-19, while SRP-3e and SRP-2a follow-up work remain open. Reconciliation: the shipped SRP-3/4 behaviour is protected, but the workflow is not promoted to an unconditional pilot-ready claim.
2. RSMP governance and the remediation register describe Tier 0A/0B/0D validation with mapping-fidelity activation **OFF**, and say 0C-B activation is unauthorized. Staging currently evaluates `ENABLE_RSMP_TIER0A_MAPPING_FIDELITY=true`. This branch does not change the flag. The mismatch requires a separate governance disposition and must not be “fixed” inside a Monitoring PR.
3. The canonical dataset document/manifest expects `RM-PILOT-028` at `55 / HIGH`; current staging returns `70 / VERY_HIGH` with the current runtime configuration. This branch snapshots the runtime output and does not alter either score or expectation to force agreement.
4. The governed seeder recreates `ARF-QAFIX-002` in `pending_second_review`; current staging shows the same explicitly marked fixture after completion of its second review. Fresh local/CI seeding protects the pending four-eyes contract; staging is not reset or mutated. `ARF-QAFIX-006` currently supplies a read-only pending-second-review browser/API specimen.
5. The historical lifecycle-linkage note says screening abstraction remains false; current runtime status is true. The provider architecture document and `/api/screening/status` are treated as the current runtime authority.
6. Application Review is frozen while the remediation register retains deferred risk-provenance findings RDI-002/RDI-003. Those findings are not changed or reclassified here.
7. Change Management has historical PASS evidence, but `R3-BSA-005` (swallowed EDD routing-audit/actuation failure) remains pending in the remediation register. This baseline protects current behaviour; it does not close that finding.

## Permanent regression group

CI now contains a job/check named exactly `protected-module-regression`. It is part of the existing reusable `ci.yml`, so it runs for pull requests and when the staging deploy workflow invokes CI after merge.

The machine-readable inventory is
`arie-backend/tests/protected_module_regression_manifest.json`; the runner is
`arie-backend/scripts/qa/run_protected_module_regression.py`.

| Group | Files | Coverage emphasis |
|---|---:|---|
| `applications_kyc` | 27 | Application list/detail, all tabs, KYC/documents, memo, supervisor/AI advisory boundary, decisions, dual approval, role/action permissions, evidence export, portal ownership, browser contracts |
| `screening` | 21 | Execution/adjudication/provenance truth, no false clear, fixture exclusion, linkage/hydration/history, four-eyes, RBAC, re-screen, replay idempotency, pagination/filters, per-hit dispositions, Agent 3 advisory boundary |
| `rsmp` | 12 | Tier 0A/0B/0C/0D, D1–D5, floors/routes, configuration, mapping fail-closed, authoritative PDF/CSV evidence, canonical SQLite/PostgreSQL validation |
| `change_management_edd` | 13 | Materiality, maker-checker, audit reconstruction, implementation propagation, portal ownership, EDD stages/outcomes, lifecycle linkage, no Monitoring-owned duplicate state |

The manifest is an exact allow-list, not a minimum subset. The runner always
prepends both contract files and installs a pytest policy plugin that fails on
any requested file with zero collected tests and on every skip, xfail, or
xpass. CI validates the immutable inventory separately before the full group.
The CI PostgreSQL service is explicitly restarted with TLS enabled and proves
`sslmode=require` before creating the suite database.

Behavioral architecture guards route Monitoring signals into the canonical EDD
and periodic-review records and prove that the action creates no KYC document,
Screening Review, Change Management, compliance-memo, AI conclusion, or officer
judgment state. Monitoring stores linkage/status only; the canonical owner
retains its decision and outcome. Both disposable SQLite and isolated
production-engine PostgreSQL schemas must match the same exact Monitoring
table-and-column allow-list.

The designated Application Review guards from `CLAUDE.md` are all present:

- `test_application_review_audit_fixes_static.py`
- `test_application_detail_perf_index.py`
- `test_approval_ux_gates_static.py`
- `test_pr5_memo_governance.py`
- `test_memo_staleness_hard_gate.py`
- `test_dual_approval_race.py`

The complete Screening Queue module-card guard set is present:

- `test_screening_queue.py`
- `test_screening_queue_state_integrity.py`
- `test_seed_screening_qa_fixtures.py`
- `test_fixture_exclusion.py`
- `test_inline_screening_runtime.py`
- `test_backoffice_ca_truthflow_static.py`
- `test_provider_label_policy.py`
- `test_declared_pep_truthfulness_priority_a2.py`
- `test_srp2_refresh_stale_screening_reports.py`
- `test_srp2_batch1_regressions.py`

No existing test was deleted, weakened, skipped, or rewritten.

## Canonical smoke fixture catalogue

All entries use explicit `is_fixture=true` plus a deterministic application ID or reserved fixture namespace. Names are descriptive only and are never the authoritative fixture marker. No staging write is authorized by either smoke harness.

| Case | Reference / ID | Source |
|---|---|---|
| Low risk, complete KYC | `RM-PILOT-001` / `pcdv100000000001` | Approved pilot canonical dataset |
| High risk, enhanced controls | `RM-PILOT-024` / `pcdv100000000024` | Approved pilot canonical dataset |
| Verified KYC documents | `RM-PILOT-028` / `pcdv100000000028` | Approved pilot canonical dataset |
| Screening hits | `RM-PILOT-024` / `pcdv100000000024` | Approved pilot canonical dataset |
| Cleared screening | `RM-PILOT-039` / `pcdv100000000039` | Approved pilot canonical dataset |
| Four-eyes review | `ARF-QAFIX-002` / `f1xedqa000000002` | Governed screening seeder; local/CI recreation |
| EDD case | `RM-PILOT-024` / `pcdv100000000024` | Approved pilot canonical dataset |
| Change Management request | `RM-PROTECTED-CM-001` / `f1xedmonbasecm01` / `CR-PROTECTED-BASELINE-001` | Disposable local/CI database only |
| Authoritative RSMP evidence | `RM-PILOT-039` / `pcdv100000000039` | Approved pilot canonical dataset |
| Monitoring alert, unchanged | `RM-PILOT-041` / `pcdv100000000041` | Approved pilot canonical dataset |

The focused Change Management contract uses the real service and canonical fixture filter in a disposable database, while monkeypatching only generated identifiers. It proves that the request is visible under explicit fixture access and absent under the default officer filter.

Staging also contains one older historical Change Management fixture whose
request ID was not generated deterministically. It is evidence only, not the
canonical fixture introduced here. The staging smoke targets its stable
fixture application identity, `qafix-confirmed-match-817`, and proves that
specific record is absent by default and present only with explicit fixture
opt-in; it does not require legitimate non-fixture queues to be empty.

## Staging semantic API baseline

The read-only authenticated smoke is
`arie-backend/scripts/qa/protected_module_staging_smoke.py`. Authentication is
the only POST; all protected-resource calls are GET. It emits no credentials or
token and supports comparison with the committed compact baseline:

`docs/audits/evidence/PR-MON-PROTECTED-BASELINE-1/protected_module_semantic_baseline.json`

Result: **33/33 checks passed** (16 safety/runtime checks plus 17 semantic
baseline comparisons).

Visibility evidence:

- default `RM-PILOT-` application count: `0`; explicit fixture opt-in: `41`
- default `QAFIX` Screening Queue count: `0`; explicit fixture opt-in: `12` rows
- targeted Change Management fixture application
  `qafix-confirmed-match-817`: absent by default, present by explicit fixture
  opt-in (the current total counts were `0` default and `1` opt-in)

Application hashes cover more than list status: canonical/current document
identity and verification, decision eligibility, current and decision-time
gate presentation, KYC/IDV/Screening gates, memo status and stable content,
supervisor output, Agent 3 advisory output, and authoritative RSMP evidence.
Screening hashes preserve three explicit axes: provider execution,
officer adjudication, and provenance. Provenance includes stable provider
identifiers, evidence quality/current-historical-stale counts, per-hit evidence
and disposition provenance, and triage output, while actor/timestamp metadata
is excluded.

The comparison also pins:

- the complete `/config/environment` feature output, including
  `ENABLE_MONITORING_DASHBOARD=true`;
- `rescreen_enabled=false` and `hydration_enabled=false` from authenticated
  provider status;
- ComplyAdvantage Mesh provider truth, abstraction enabled, and simulation
  fallback disabled;
- RSMP activation/model source through the read-only risk-model hash;
- EDD stage, outcome, score/tier, trigger source, and canonical application
  linkage; and
- the targeted Change Management fixture's application linkage, approved
  status, `tier3` materiality, decision note, changed-field count, downstream
  obligations, and precondition semantics.

Stable Application Review / RSMP outputs:

| Reference | Status | Score / tier | Documents | Monitoring alerts | Semantic SHA-256 |
|---|---|---|---:|---:|---|
| `RM-PILOT-001` | approved | `12 / LOW` | 3 | 0 | `3866a4092c3abfb5c65b3158cc16b98e28660b20925f2f8fe8536da1f474fe99` |
| `RM-PILOT-024` | approved | `70 / VERY_HIGH` | 3 | 1 | `8b3a2c41b74e770aee0eeec142b3275b8ce415b0ab7d51a88a7099e7a26f237b` |
| `RM-PILOT-028` | submitted_to_compliance | `70 / VERY_HIGH` | 8 | 0 | `a7dcf2a4f77bb20fbf409741a8fbeed9d9babd85713b33ef8d3ff08a91fe08fd` |
| `RM-PILOT-039` | approved | `43.3 / MEDIUM` | 3 | 0 | `67238f01c2207b47ad99f9adc4d1dbb8fa4b874a654f54953961b1025ab11734` |
| `RM-PILOT-041` | approved | `12 / LOW` | 3 | 1 | `176bea69f3309d4a218b923ae889da462b4f45aa937f8354b80eb0b7de5f6413` |

Every case has `risk_report_evidence.available=true`,
`authoritative=true`, and `read_only=true`. The runtime model contains D1–D5,
is read-only, and has semantic SHA-256
`41832ee3963683c47051f53220ff78412eefc00c101f5f12154636f0ce8d3b76`.

Stable Screening outcomes:

| Reference | Truth / status | Four eyes | Defensible clear | Semantic SHA-256 |
|---|---|---|---:|---|
| `ARF-QAFIX-001` | pending_provider / screening_in_progress | not required | false | `b753e7bf6be8984b6e6c44d33eb6f5d275911614d516b1442d8d87cf63c663b8` |
| `ARF-QAFIX-002` | completed_match / cleared_by_officer | complete | true | `5407f4baf5eb59bee005c059fd77893af327f26c175193bea7d8282ece0e9e44` |
| `ARF-QAFIX-004` | failed / failed | not required | false | `653de083dbd1d62af812dfe397cd6b2585f454a10c472ea7145f625957dca706` |
| `ARF-QAFIX-005` | stale / stale | not required | false | `a3c7736da8496fc2c223ee663ea50faf00dd5a89c09ba14507178caca5562922` |
| `ARF-QAFIX-006` | completed_match / review_required | pending_second_review | false | `5428c68bda00304dcf925571f2b7955c03f290bc3bd69384c74154366332c7e3` |

Pending, failed, and stale fixtures all remain non-clear. EDD cases for
`RM-PILOT-024` and `RM-PILOT-028` remain linked to their canonical application
IDs and stay EDD-owned. Their semantic hashes are respectively
`94fde689e8770e23de1b9f52d19d014f8a22a14484c371b228e6868b207683de`
and
`63c31fda68389c225422de01ecfe6fe5b5b97f6a156144b40680fb461b089d00`.
The targeted historical Change Management specimen remains
`approved / tier3`; its semantic hash is
`5e91bf5836e1835371a26724ba110b17cc50786c382d31d1ecf534bc2a295133`.

## Browser harness

The existing Chromium harness now checks:

- Application list and the fixed `RM-PILOT-039` canonical detail specimen;
- all seven Application Review tabs;
- rendered KYC identities, document types, review/verification outcomes, and
  explicit rejection of loading/error placeholders;
- rendered Screening truth, provider references, hydrated per-hit evidence,
  and explicit rejection of loading/error/outage placeholders;
- exact backend-derived memo and application-decision enabled/disabled state
  for the canonical approved specimen, including disabled reasons, after the
  asynchronous memo validation response has established the approved state,
  without clicking;
- authoritative risk evidence and export-control presence without downloading;
- default fixture exclusion;
- Screening Queue filters and pagination;
- a failed provider fixture never presented as clear and without disposition actions;
- populated evidence and the exact pending-second-review action set (one
  enabled `Clear as False Positive` action for the approved SCO role, with
  true-match/escalate/RMI actions absent);
- backend RSMP projection, D1–D5, and read-only state;
- the admin-only Risk Scoring Model page, backend projection, and absence of edit controls when run with an administrator.

No protected decision, disposition, upload, export, or mutation is performed.
The protected profile is fixed to the canonical specimen. The existing
Application role-matrix harness uses a separate explicit profile that requires
its per-role target and expected role, records the application actually opened,
and fails before summarizing evidence if either value does not match.

Current result: **not executed through browser control**. The browser skill
requires the in-app Node REPL control tool, which is unavailable in this
session; its instructions prohibit substituting another computer-control tool.
In addition, the approved staging QA credential is `sco`, while the Risk
Scoring Model page is admin-only. The harness records that role limitation and
still validates the authenticated backend projection and application evidence.

Planned report/screenshots location when an approved browser session is
available: `/tmp/regmind-staging-browser-smoke/report.json` and sibling PNG
files. No screenshot artifact is claimed in this baseline.

## Validation evidence

| Gate | Result |
|---|---|
| Revised contract/ownership/browser tests | `56 passed`, including isolated production PostgreSQL ownership schema |
| Role-matrix/browser profile regression | `25 passed` after independent-review remediation |
| Protected-module regression | `1567 passed` in `220.56s` with Python 3.11 and real SSL PostgreSQL; zero skips/xfails/xpasses |
| Full repository suite using CI exclusion | `8190 passed, 2 skipped, 4 xfailed` in `1121.08s`; zero failures/errors |
| CI-excluded PDF suite | `8 passed` |
| Collection floor | `8196` collected, required minimum `3800` |
| Python syntax | PASS |
| flake8 `E9,F63,F7,F82` | PASS, `0` findings |
| Schema migration policy | PASS; no migration added |
| JavaScript syntax | PASS |
| Staging semantic API smoke | PASS, 33/33 including EDD, CM, provider/CA, environment-feature, RSMP, Application, KYC, and Screening comparisons |
| Local/authenticated browser smoke | **Not run — limitation described above** |

The first protected-suite attempt produced 26 PostgreSQL connection errors
because the local Homebrew server did not support the repository-required
`sslmode=require`. After enabling SSL exactly as CI does, the affected 32 tests
passed and the complete 1,452-test protected suite passed. This was test
infrastructure correction, not a product or test change.

During independent-review remediation, one runner invocation accidentally used
the macOS system Python 3.9 and stopped at six existing `X | None` annotations.
The fail-closed plugin then named every requested file that collected zero
tests. The required Python 3.11 rerun collected all tests and passed.

## Independent-review remediation

The mandatory independent reviews were blocking until every actionable finding
was addressed:

1. The new CI job now enables PostgreSQL TLS and proves
   `sslmode=require` before database creation.
2. The manifest is an exact inventory, contract tests always run, and a pytest
   plugin fails on zero collection, skip, xfail, or xpass.
3. Behavioral ownership tests prove Monitoring routes to canonical owners
   without duplicating protected-domain or officer/AI decision state.
4. Semantic snapshots now include memo/decision/gate state and explicit
   Screening execution/adjudication/provenance evidence.
5. Change Management fixture visibility is identity-based rather than a
   whole-queue emptiness assumption.
6. Browser contracts now require semantic KYC/Screening evidence, exact
   canonical control states, and the exact four-eyes action set.
7. Monitoring schema ownership is frozen in both SQLite and isolated
   production PostgreSQL, not just through a column-name blacklist.
8. EDD, Change Management, the full environment feature map, provider truth,
   and CA re-screen/hydration flags are semantic baseline comparisons.
9. Browser memo state waits for asynchronous validation, detail four-eyes
   requires its positive second-clear action, and the specimen is fixed to
   `RM-PILOT-039` rather than advertising an incompatible override.
10. The existing Application role-matrix browser wrapper now selects an
    explicit role-aware profile, proves the requested application was actually
    opened, and records only observed application/role values in its summary.

The final independent re-review is **READY** with no remaining actionable
P0/P1/P2. No PR may be opened while the authenticated in-app browser execution
remains outstanding.

## Conditions every later Monitoring PR must preserve

1. Start from current `origin/main` and prove staging alignment before capturing comparisons.
2. Run this named CI group, the affected module tests, and the full suite without weakening or skipping a guard.
3. Keep Monitoring a signal/orchestration surface; do not duplicate KYC storage, Screening Review, EDD state, Change Management state, or officer judgment.
4. Keep Application Review output and all seven tabs behaviour-equivalent.
5. Preserve document currentness, party linkage, verification outcome, download linkage, and tenant isolation.
6. Preserve separate Screening execution, adjudication, and provenance axes; pending/degraded/error/conflict is never clear.
7. Preserve four-eyes and role/state disposition controls, replay idempotency, evidence hydration, pagination, filters, and default fixture exclusion.
8. Preserve authoritative D1–D5 values, composite score/tier, floors, reasons, routes, configuration version, fail-closed unresolved mapping, and backend-owned exports; never recompute in the browser.
9. Do not change feature activation or staging configuration in a Monitoring remediation PR.
10. Compare stable semantic hashes; do not byte-snapshot volatile timestamps, generated actor IDs, or transient provider metadata.
11. Obtain an independent-agent review, resolve every actionable P0/P1/P2, and re-run affected tests before opening/updating a PR.
12. After merge, verify exact merge SHA deployment, task definitions, service health, old-task drainage, CloudWatch, authenticated API/browser smoke, unchanged flags, and zero fixture leakage.

Any protected test failure, unexpected semantic change, false-clear outcome,
four-eyes regression, Application Review output drift, RSMP drift, fixture
leakage, or unresolved P0/P1/P2 is a blocker.
