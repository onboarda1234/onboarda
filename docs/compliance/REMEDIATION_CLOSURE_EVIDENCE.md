# Remediation Closure Evidence

Companion archive to [`docs/REMEDIATION_MASTER_LIST.md`](../REMEDIATION_MASTER_LIST.md).
The master list holds **status only**; this file holds the closure evidence that
backs each ✅ — merge SHAs, ECS task definitions, validation output, limitations,
and residuals. Entries are append-only per item; supersede by adding a dated
correction, never by deleting. Evidence text is carried over verbatim-in-substance
from the pre-2026-07-15 master list (see that file's git history for the original
placement).

All staging evidence below is staging-only. **No entry in this file constitutes a
production-readiness claim.**

---

## wave-a PRs 700-703

All four small-wins merged + deployed to AWS staging + validated (PASS):
**#700 (SW-1)** clean 405 on POST-only periodic-review baseline route ·
**#701 (SW-2)** dead approval-branch cleanup, merge `dd28a79`, TD 788 ·
**#702 (SW-3)** staging-SHA gate (code half; gate exercises on each deploy) ·
**#703 (SW-4)** CA webhook retry idempotency, merge `daab2bb`, TD 789.
At validation, staging == `origin/main` == `daab2bb`. Item 24's reconciler
wiring remains open as item 24b.

## overnight-batch PRs 705-708

Merged + deployed to AWS staging + validated (Codex closure report):
**#705 (P11-1)** PASS · **#706 (P11-3)** PASS-with-limitation (budget-store
outage source/test-validated, not live fault-injected) · **#708 (P10-6)**
PASS-with-limitation (no live sign-off audit record; spoof rejection
source/test-validated) · **#707 (P11-9)** PASS. Final staging SHA `fadf8a6`
== latest `origin/main`; backend `regmind-staging:796`, worker
`regmind-verification-worker:244`; `/api/version` + liveness + health +
readiness 200; CloudWatch ERROR/Exception/Traceback/5xx = 0.

## p10-wave-1 PRs 695-698 and 704

Phase 9 (RDI) Wave-1 closure — the three current-stage blocking CRITICALs
merged, deployed (`regmind-staging:782` / worker `:230`, image `e66405a`),
validated PASS. Merge order on `main`: #695 → #697 → #696 → #698.

- **P10-1 (#697, RDI-006)** — merged (base `b577a5f`, merge `b6192fb`).
  `create_change_request()` ignores client-supplied `items[].materiality` and
  server-computes tier from `change_type` via `classify_materiality`;
  fresh-context review fold prevents server-known alert types (e.g.
  `control_change`) downgrading to `other`/Tier 2. Full SQLite suite 6549
  passed; CM regression 217 passed; static guard asserts no
  `item.get("materiality")` read. RDI-006 CLOSED/REMEDIATED (Codex-verified;
  control C-11 VERIFIED for client-supplied override). Residuals:
  (a) `change_type` itself is still client-supplied — semantic mislabeling is
  a future hardening item (unknown types default Tier 2); (b) the
  previously-approved four-eyes scope change was not part of #697 — since
  CLOSED by **#704** (Codex, merge `956ed5b`): maker-checker narrowed to
  Tier 1 only, Tier 2 still covered by the screening hard-block.
- **P10-3 (#696, RDI-004)** — merged, deployed (`regmind-staging:781` /
  worker `:229`, image `fbedc7c`), validated. Targeted
  `test_risk_staleness_gate.py` 15 passed; runtime synthetic probe confirmed
  current-version app proceeds, older-version app + `stale:recompute_failed`
  quarantine both 409-block, non-approval decisions (reject / escalate /
  request-docs = 201) not newly blocked. RDI-004 CLOSED/PASS. Residual (per
  design): legacy `NULL`-provenance apps blocked only after first config
  update/sweep.
- **P10-2 (#698, RDI-001/007/011)** — rebased onto #696-merged `main`,
  retargeted, CI green, merged, deployed (`regmind-staging:782` / worker
  `:230`, image `e66405a`), validated. Targeted decision/memo/approval suite
  263 passed / 2 skipped; full SQLite suite 6568 passed. Runtime probe:
  decision 201 persisted `decision_records_count=1` + audit + accepted
  governance; memo approve 200 with signoff audit; memo validate 200
  persisted status+timestamp. RDI-001/007/011 CLOSED/PASS. Residual: live-DB
  fault injection not run (forced-failure covered by merged tests);
  memo-supervisor `decision_records` overlay stays scoped to P10-5/RDI-009.
- Final staging aligned to #698 merge SHA `e66405a`; `/api/version`
  git_sha+image_tag match; liveness/health/readiness 200 (`ready=true`); both
  ALB targets healthy; 30-min CloudWatch window ERROR/Exception/Traceback/
  HTTP-5xx = 0.
- **#695** (docs) merged ahead of the wave. **#699** (Codex draft, P10-1
  closure-evidence docs) closed unmerged as redundant — its closure record is
  carried here.
- Audit-2 unpause: with RDI-006/004/001 closed and validated, Audit 2 (BSA)
  ran against `e66405a`. The audit artifact's "remaining blockers
  RDI-001/RDI-004" note reflects the point in time when #697 was verified —
  both had since merged.

## p11-8 PR-712

Supply-chain pinning (BSA-016/017/019 = DCI-022/024) — merged +
Codex-validated PASS (2026-07-08). SHA-pinned GitHub Actions (all 4
workflows, exact-release comments, annotated tags peeled); test deps split
into `requirements-dev.txt` (flake8 pinned); Docker base image pinned by
manifest-list digest + `.dockerignore` excludes uploads/data/logs; 8 guard
tests prevent regression. Residuals: CI service container + dev compose still
on mutable postgres tags (accepted out of scope); SHA-refresh process + CI
service-container pinning remain ops decisions. Hash-pinned lockfile gap
re-flagged by re-run finding R2-BSA-019 (open).

## ownership-gate PR-713

PR-APP-ACTION-OWNERSHIP-SCOPE-1 (FEO-013) — merged + Codex-validated PASS
WITH LIMITATION (staging `074607d`). Final approve/reject + pre-approval +
memo approval owner-gated; admin/SCO override requires
`ownership_override_reason`; unassigned → auto-claim at SUCCESS commit only
(failed attempts can never seize ownership); dual second leg exempt only at
current HIGH/VERY_HIGH; collaboration verbs stay open. Adversarial review
found 2 blockers, both redesigned away; 26 tests incl. HTTP endpoint matrix;
live-PG probe PASS. Browser smoke clean. Limitations: live ownership-denial
not exercised (no safe assigned fixture, RDS private); TOCTOU +
assigned_to-validation residuals stand; sign-off memo
(`OWNERSHIP_GATE_SIGNOFF_MEMO.md`) awaiting founder signature.

## p12-2 PR-715

Change-implementation fail-closed recompute (DCI-012/013) — merged +
Codex-validated PASS WITH LIMITATION (2026-07-09, staging `02f5538`).
Per-request quarantine sentinel `stale:cm_recompute_pending:<id>` stamped in
the SAME txn as implement whenever the change requires risk; CAS-guarded
recompute persistence; approve/reject/implement audit rows written
pre-commit. Review folds: predicate parity (M1), PATCH-path recompute (M2),
gate hoist. 18 tests; live-PG probe 17-green. Live runtime: risk-relevant CR
implemented with `risk_recompute_quarantined:false` + sentinel-stamped app
blocked from approval with 409. Limitations: fault-injection paths
source/test-validated only. Residual: no enforcement path for
already-approved applications (M3).

## p12-5-dci-006 PRs 716 and 739

**#716 (P12-5)** — merged + deployed. Canon-constant CHECK constraints for
all 8 status/enum columns (Migration v2.47, steady-state no-op boots via
constraint-def comparison; fail-closed `clients.status` backfill; SQLite→PG
migrator per-row SAVEPOINTs). Bonus repair: `Severity.WARNING` added to the
supervisor enum — 6 call sites crashed with AttributeError before their audit
INSERT. 20 tests; live-PG probe 19-green.

**Staging residual, RESOLVED 2026-07-11 (Codex PASS):** the 3 v2.47
constraints (`clients_status_check`, `agent_executions_status_check`,
`agent_executions_source_check`) had been SKIPPING on staging because of 4
legacy off-canon rows. Remediation SQL
`scripts/dci006_staging_remediation.sql` merged via **#739** (`9d597ea`) and
executed on staging (task def `826`): 680 `fixture`→`ai`, 1
`disabled`→`inactive`, and execution `id=1` (synthetic QA app
`pr4-auto-7f861903` = "PR4 Monitoring Automation Smoke", created 2026-05-27,
no linked client) canonicalized `direct_probe`/`staging_direct_probe` →
`error`/`ai` as a provenance-guard-flagged, human-reviewed synthetic
exception (row preserved, not deleted). v2.47 INFO-logged all 3 constraints
installed; off-canon counts 0; DCI-006 CloudWatch ERRORs cleared.

**Process-hygiene origin note (tracked as R2-PROC-1):** the off-canon values
were injected into a regulated table (`agent_executions`) by a direct
staging-DB probe during an earlier automated validation sprint — bypassing
the app and the P12-1 `DBConnection` interceptor (which is exactly why they
were off-canon and un-catchable in-process). Staging QA/validation must not
write raw SQL into regulated tables; probe writes must go through the app or
an explicitly-marked fixture path.

Re-run finding DCI-104 additionally flagged **54 unindexed FKs** — separate
open follow-up.

## p12-8 PRs 717 and 723

Retention purge enforceability + purge-log evidence (DCI-020/021) — merged +
deployed. 7 manual categories documented
(`docs/compliance/MANUAL_PURGE_PROCEDURE.md` + CLI recorder);
`data_purge_log` gains subject/application/tables/per-table-counts/batch-id/
evidence columns (Migration v2.48) written in ONE txn with the DELETE.
Review MAJOR fold: legacy `purged_by`→users FK dropped — the scheduler
identity would have failed EVERY PG purge forever. 21 tests; live-PG probe
8-green. Boot-crash hotfix **#723**: the up-front `idx_purge_log_batch`
index was moved into the v2.48 migration (after `ADD COLUMN`) — it had
crashed existing-DB boot (`column purge_batch_id does not exist`) and failed
deploy #975; upgrade-path regression test added.

## p12-9 PR-718

Observability hardening (DCI-028/029) — merged + deployed (staging
`5d6ba3e`). Forced JSON logs in staging/prod across BOTH pipelines (kills
staging double-emission); contextvar request-correlation ids (sanitised
`c-`-prefixed `X-Request-ID`, echoed header, auto-injected into structured +
root-logger lines, persisted on `audit_log` rows — Migration v2.49, worker
`job-*` ids); readiness gains disk-capacity gate + tight-timeout S3 probe
(403 = reachable_permission_limited, non-gating). 30 tests; live-PG probe
5-green. Residual: legacy direct `INSERT INTO audit_log` sites keep
request_id NULL (write-forward; see also PR-744 entry).

## applications-page-pair PRs 719 720 727

**#719 (perf-applications-default-list-projection)** — merged +
staging-validated. Slim paginated projection is the DEFAULT
`/api/applications` payload (was: full `a.*` + child hydration for 5000 rows
to any caller omitting `?view=`); `?view=full` unchanged opt-in. Review
fold: periodic_review projection stays a full/detail-surface field. Full
suite 6748-green on the stack.

**#720 → re-landed as #727 (ux-applications-list-sort-status-tabs)** —
merged + staging-validated PASS-with-limitation. Whitelisted server-side
sort (8 columns; COALESCE NULL-score parity SQLite↔PG, severity-rank
risk_level, unique `a.id` pagination tiebreaker) + comma-status filter
backing 6 grouped status tabs (proper tab ARIA); dropdown-wins conflict
resolution; off-canon "(non-standard)" status safety net wired to the real
load path; fake-AI "Quick Reference" chat removed wholesale (canned "All
checks passed" responses were a misleading-claims liability). Adversarial
review: 4 MAJOR folds; 13 API tests + 24-check headless-Chromium run.
Toolbar declutter folded in later. **Process note:** #720 was merged into
the already-merged #719 branch (wrong base) so its changes never reached
`main`; re-landed cleanly as **#727** (`2315c62`) and deployed.

## portal-smoke PR-722

CLIENT-PORTAL-RUNTIME-SMOKE-1 (REGMIND-P1-006) — Codex-executed 2026-07-08
against staging `d4fdb3f`: full cross-tenant matrix denied (A↔B
apps/docs/uploads 403; no list leakage), logout token replay 401, rate-limit
+ upload rejections clean, no 5xx; synthetic fixtures fully cleaned incl.
S3. The one benign limitation (cleanup racing the async verification worker
→ `Verification job not found` traces) CLOSED by **#722** (merged `dd7627f`,
Codex-validated PASS 2026-07-09): worker treats a cleaned-up job as
`verification_job_missing_skip`, real DB/provider failures still propagate;
staging window ERROR/Exception/5xx/`job not found` = 0.

## item-26 PR-728

Shared fail-closed rate limiter (BSA-002) — merged + Codex-validated PASS
WITH LIMITATION (staging). DB-backed `shared_rate_limits` (Migration v2.51,
`idx_shared_rate_limits_expires_at`); forgot-pw/reset/upload/AI-verify
over-limit → 429; limiter keys expose no raw email/IP/token/payload.
Limitation: live DB-outage fault-injection source/test-validated only.
Re-run finding R2-BSA-016 (open) flags remaining AI-route gaps:
`/api/documents/{id}/verify` + both supervisor pipeline triggers unlimited;
enhanced-upload limiter still process-local.

## p13-1 PR-729

Back-office stored-XSS elimination (FEO-001/002) — merged + Codex-validated
PASS WITH LIMITATION (staging). Escaped/`textContent` the API-interpolated
fields in the memo (`renderMemoSections`) + supervisor/audit renderers;
enum→class badge maps; XSS regression fixtures. Scope held to the named
high-risk renderers — screening/notes/document-metadata renderers documented
as follow-up. Limitation: runtime malicious-fixture injection
source/test-validated only.

## p11-2 PR-730

Dependency CVE remediation + pip-audit CI gate (BSA-015) — merged +
Codex-validated PASS WITH LIMITATION (staging; `docker-validate` via CI,
local Docker NA). pip-audit-driven minimal bumps + `pip-audit` CI gate
(fails on HIGH/CRITICAL) with a documented, dated WeasyPrint allowlist
(`CVE-2026-49452`, review 2026-08-09, unused vulnerable mode). Full suite
6858 passed; Docker/PDF/Fernet/JWT compat verified.

## app-727 PRs 731 732

Applications-page readiness audit (Codex, run against PR-727 staging, then
re-run against `8a0fdef`). Initial run STOPPED on a Critical (audit-log
leakage); after remediation the re-run verdict is **READY FOR PILOT WITH
CONTROLS / NOT PRODUCTION READY**.

- **APP-727-001 (Critical)** — cross-application audit-log leakage: Activity
  Log queried by ref-derived `target` text with no immutable scoping;
  reused/colliding refs returned another app's rows. Fixed by
  `audit_log.application_id` (Migration v2.50 + `idx_audit_log_application_id`)
  and scoping Activity Log / evidence-pack reads by immutable id. #731→#732
  merged + Codex-validated (staging `8a0fdef`; isolation PASS). Residuals:
  legacy ref-only rows hidden (not backfilled); app-ref uniqueness not
  enforced; writer-side population completed by #744 (below).
- **APP-727-002 (High)** — hostile filename → S3 `TagValue invalid` → 500 on
  upload. Fixed by sanitising S3 tag values/keys derived from filename;
  hostile/quote/unicode/traversal/long names now 201. Merged +
  Codex-validated (staging `8a0fdef`; CloudWatch `TagValue` = 0).

## app-aud PRs 733 734 735

- **APP-AUD-002 / P9-13 (#733)** — role×route matrix harness: 5
  generated-password actors + 11 fixture apps, `0600` creds, staging-only
  fixture exception, bulk disable; 53/53 API role checks, client denial,
  blocked-approval denial, ownership matrix. Merged + Codex-validated PASS
  WITH LIMITATION — analyst-UI + several runtime action paths still to prove
  (tracked at P9-13).
- **APP-AUD-003 (#734)** — clean no-blocker approval path e2e: real
  portal→submit→zero-blocker→real approve→decision record→replay-409→blocked
  negative control (`test_portal_to_approval_e2e.py`). Merged +
  Codex-validated CLOSED WITH LIMITATION (staging `0e1a4ee`;
  provider/doc/IDV/screening clearances fixture-assisted non-prod).
- **APP-AUD-001 (#735)** — UI action-gate: Approve looked active on a blocked
  case (backend already blocks 400/403); analyst UI/authz alignment +
  denied-endpoint handling + static authz test. Merged; staging
  re-validation pending.

## audit-writer-id PR-744

APP-727-audit-writer-id-1 — CLOSED (staging-validated 2026-07-11,
`ff47717`): decision/sign-off/memo audit rows now carry `application_id` +
`request_id`; `append_audit_log` gained the two params (reuses
`_resolve_audit_application_id` + `get_request_id()` contextvar);
hash-chain `verified=true` (payload/`hash_version` untouched); cross-app
isolation confirmed (App A row not shown on App B). Residual:
lower-priority direct-insert writers still ref-only (write-forward).

## p12-1 PR-738

Regulated-record deletion protection (DCI-001/003) — merged +
AWS-staging-validated PASS (2026-07-11, merge `6ba253d`, backend TD
`regmind-staging:827`, worker TD `regmind-verification-worker:275`).
`/api/version.git_sha` + `image_tag` matched merge SHA; `/api/liveness`,
`/api/health`, authenticated `/api/readiness` passed; synthetic
app/document delete denial, v2.13 report-only boot check, retention purge,
fixture cleanup guard, and CloudWatch checks passed. Blocks unsafe
regulated-record hard deletes at runtime choke points while preserving
sanctioned retention/fixture contexts; v2.13 startup cleanup is
report-only/non-destructive; fixture cleanup requires marker/confirmation;
retention purge remains evidence-backed. **CLOSED for controlled pilot; no
production-readiness claim.** Related: **#737** (draft, open) is the
supervised Phase A discovery report (regulated-record classification,
hard-delete inventory, cascade FK audit, Phase B plan) awaiting founder
decisions.

## r2-bsa-cluster PRs 743 747

2026-07-11 re-run supervisor/security cluster, all four findings CLOSED:

- **R2-BSA-001 (#743)** — staging-validated 2026-07-11, `5c255e8`; backend TD
  `regmind-staging:829`, worker `:277`. Supervisor routes moved onto
  `BaseHandler` (cookie-CSRF enforced via `prepare()`, Bearer path intact);
  wildcard `Access-Control-Allow-Origin: *` removed.
- **R2-BSA-002 (#743)** — `get_server_actor()`: forged body actor ignored,
  session actor+role persisted (probe stored role `sco`); conflicts logged.
- **R2-BSA-004 (#743)** — exact `request.path` CSRF allowlist: substring
  `/webhook` and `?=/webhook` query both 403; both real webhooks
  (`/api/kyc/webhook`, `/api/webhooks/complyadvantage`) still reach signature
  verification (401 on missing sig).
- **R2-BSA-003 (#747)** — staging-validated 2026-07-12, `f3754cd`; backend TD
  `regmind-staging:832`, worker `:280`; **Migration v2.52**: the 3 tables
  (`supervisor_human_reviews`/`_overrides`/`_escalations`) now durable in
  main PostgreSQL (11 indexes, legacy ids→text, `/app/arie.db` absent,
  evidence in PG); mirrors the `supervisor/audit.py` `get_db()` pattern;
  actor server-derived; `request_id` via contextvar; fail-closed
  source/CI/rollback-validated (live DB-failure injection not run on
  staging); all 3 tables P12-1-classified as regulated.

## item-36 PRs 748 749

Persisted negative-path fixtures — CLOSED for controlled-pilot scope
(2026-07-12). #748 introduced the registered negative-path fixture
substrate; #749 moved it to the reserved `FX-ITEM36-*` namespace after the
original ARF refs collided with long-lived staging rows. Hotfix merge
`6197734bc7a64ee83fba6e261625c8b6ec45a856` deployed through staging run
`29204426827`. Complete synthetic walkthrough passed: 12 logical fixtures /
13 application refs seeded, all refs read back through the authenticated
API, a second seed retained every root and child ID/count, blocked approval
returned 400, terminal replay returned 409, cross-client access returned
403, and the representative P12-1 direct delete was denied without
mutation. Sanctioned cleanup left zero Item 36 residue; all 12 previously
occupied ARF refs (including non-fixture application `acf4ade81e694d31`)
remained unchanged. Retention decision: **A — staging left clean**.
CloudWatch clean of runtime/deploy/fixture errors; one corrected read-only
operator preflight query produced a wrapper `IndexError` and no mutation,
classified as validation-harness noise. Not a production-readiness claim.

## p13-7 PR-745

Compliance-officer SOP pack (FEO-014) — **docs merged 2026-07-13**, merge
SHA `02eeae5`. Pack: `docs/pilot/COMPLIANCE_OFFICER_SOP.md`,
`PILOT_REVIEW_CHECKLIST.md`, `OVERRIDE_AND_ESCALATION_PROCEDURE.md`,
`EVIDENCE_EXPORT_PROCEDURE.md`. Covers LOW/MEDIUM fast-path
cross-reference + disqualifiers, maker-checker/dual-control clarification,
document-precedence rule, monitoring operational-status checklist,
controlled-document metadata. **Merge does not close the gate:** the 🟠
pilot operational gate remains open until Section 16 execution — named
officers assigned and trained, pilot scope approved, provider-mode decision,
monitoring decision recorded, founder/management + compliance approval, and
signatures retained. (#752, open at reconcile time, was a docs-only PR
recording this same status.)

## rsmp PRs 751 753 755 764

Risk Scoring Model Pack — response to re-run findings DCI-108/109 (risk
parser under-scoring). Reference docs:
`docs/audits/RISK_SCORING_MODEL_FULL_AUDIT.md`,
`RISK_SCORING_FOUNDER_DECISION_PACK.md`, `RISK_SCORING_SCENARIO_MATRIX.md`,
`RISK_SCORING_SETTINGS_REGISTER.md`,
`RSMP_TIER0A_ACTIVATION_AND_REVIEW_PLAN.md`,
`RSMP_TIER0B_REVIEW_AND_REBASE_PLAN.md`.

- **#751** — review/audit pack docs, merged (`c31d0b2`).
- **Tier 0A (#753)** — guarded parser + mapping fidelity, merged
  (`228a6c2`). Activation flag remains OFF.
- **Tier 0B (#755)** — fail-closed routing on unresolved RSMP mappings,
  merged; **staging validated at
  `dd4784bdf270cb532c9c290e6f16e826ea2776ba`** on backend
  `regmind-staging:847` and worker `regmind-verification-worker:295`.
- **Tier 0C** — activation + recomputation: NOT executed. Final remaining
  RSMP pilot-readiness workstream.
- **PR-1b (#764)** — declared-PEP runtime alignment with the approved
  Gate 0 v4 model (all declared/officer-confirmed PEP roles score 4;
  `pep_declaration.pep_role_type` authoritative). Merged 2026-07-15 at
  `a823fb6491ea35a1647800853a97dc7a0f328b6f`; Gate 0 v4 canonical Markdown SHA-256
  `33cdcaac5f01ba431776a4b8a300aee4cb6e48f0d585d9c1665c726d655f66f0`.
- **PR-1b staging validation (added 2026-07-15, from the controlled #764
  deployment; recorded in draft docs PR #767, incorporated here):**
  AWS-staging-validated at merge SHA `a823fb6`; backend task definition
  `regmind-staging:850`, worker task definition
  `regmind-verification-worker:298`. Every declared PEP role scores 4 while
  the existing High floor, EDD route, and approval routing remain unchanged.
  RSMP activation remains OFF; Tier 0C stays the next incomplete
  risk-scoring pilot gate.

## rsmp tier 0d pr 768

- **Merge and deployment:** #768 merged 2026-07-15 at
  `7e9111476d73b1fb937e214ca712261bc88ea91a`. Canonical staging workflow
  [run 29423727704](https://github.com/onboarda1234/onboarda/actions/runs/29423727704)
  passed the 7,114-test PostgreSQL-backed suite, Docker validation, PDF tests,
  exact-SHA image build, ECS rollout, and health gates.
- **Artifact alignment:** backend `regmind-staging:856` (2/2) and worker
  `regmind-verification-worker:304` (6/6) both run the merge-SHA image;
  image tags, `GIT_SHA`, `IMAGE_TAG`, and authenticated `/api/version` values
  match the merge SHA. Both ALB targets were healthy; liveness, health, and
  authenticated readiness returned HTTP 200 (`ready=true`).
- **Runtime/UI alignment:** the Back Office Risk Scoring Model page loads the
  read-only `/api/config/risk-model` projection from the scorer's validated
  runtime loader. The page has no risk-model editing controls or client-side
  score/mutation handlers. Lane B remains separate and pending calibration;
  runtime floors and the explicit monthly-volume/unsolicited-referral no-floor
  rules are displayed from the projection.
- **Authoritative exports:** a safe staging fixture returned ready, read-only
  risk evidence whose score, tier, five dimensions, D3 weights, EDD route, and
  approval route matched stored backend outcomes. A declared-PEP fixture
  exposed factor score 4. Missing and stale synthetic in-memory evidence both
  failed closed; no synthetic application was created.
- **API safety:** admin, SCO, CO, and analyst reads returned HTTP 200;
  unauthenticated and client reads were denied; POST and PATCH were HTTP 405.
  The exact-SHA CI suite verified malformed projection input returns a
  controlled non-sensitive HTTP 503 without traceback leakage.
- **Activation/data safety:** `ENABLE_RSMP_TIER0A_MAPPING_FIDELITY` is absent
  and evaluates false. The staging risk-config version
  `risk_config:2026-07-13 07:15:16.941658` and SHA-256
  `9ffcfe3e4dd5fcd3a2df7aa11506c39631b683eb934019384d59f7fba339d91e`
  were unchanged. Application count (933), recomputation-stamped count (681),
  and latest recomputation timestamp (`2026-07-13 07:16:03`) were unchanged;
  no Tier 0C activation or recomputation occurred.
- **Operational evidence:** deployment-through-validation CloudWatch review
  found zero errors, exceptions, tracebacks, unexpected 5xx, startup,
  projection, export, routing, recomputation, risk-config mutation, provider,
  or email-send events. Both sampled audit hash chains verified intact.
- **Next gate:** Tier 0C remains outstanding. This validation is not a
  production-readiness claim.

## rsmp tier 0c-a hotfix PR 775

Multi-service maximum-risk hotfix — merged 2026-07-16 (`8025040`),
staging-validated at the merge SHA (ledger draft #777, incorporated here).
Found by the Tier 0C-A read-only frozen-baseline replay.

- **Root cause:** `services_required` correctly persisted all selections, but
  normalization also populated the legacy singular
  `primary_service`/`service_required` alias from the first element, and
  D3.1 consumed only that alias — selection order and stale primary values
  determined 15 of 28 multi-service outcomes. Submission, replay, and
  recompute shared the defect through `build_prescreening_risk_input()` and
  `compute_risk_score()`.
- **Fix:** preserve the full plural payload in the canonical scorer input;
  parse every supported form (arrays, nested objects, JSON/Python-list
  strings, delimited legacy); score every selection independently; set D3.1
  to MAX of the individual selected-service scores. Single-service and
  activation-OFF behavior preserved exactly; genuinely unmatched
  multi-select values reuse the existing hashed `stale:unmapped_*` approval
  block (no fuzzy matching, no silent lower default). The runtime-owned
  maximum rule surfaces through the read-only Tier 0D model projection.
- **Replay evidence (650 active scored applications, READ ONLY txn):**
  28/28 multi-service applications correct after fix (13 before); 15
  service-factor changes, 9 composite-score changes, **0 tier / EDD-route /
  approval-route / newly-unresolved / unexplained changes**. Risk-config
  version `risk_config:2026-07-13 07:15:16.941658` and hash unchanged;
  Gate 0 canonical SHA-256 unchanged. Full pseudonymised 28-case evidence:
  `docs/risk-programme/RSMP_TIER0C_MULTISERVICE_MAX_HOTFIX.md`.
- **Boundaries:** no activation, no recomputation, no staging config
  mutation; the Tier 0C-A unresolved-mapping and data-remediation backlogs
  were not changed.

**Tier 0C definition (founder status 2026-07-16):** 0C-A = final
frozen-baseline read-only replay + impact assessment (rerun next,
post-hotfix); 0C-B = controlled activation + recomputation + officer review
+ final staging validation, authorized only if 0C-A concludes "Ready". Both
are pilot blockers. Post-pilot workstreams: Tier 1A (sector risk programme,
incl. 22 Lane B sector labels), Tier 1B (country risk programme — 130
deferred countries + 19 regions); production-readiness workstream: Tier 2
model governance (versioning, maker-checker, effective dating, rollback,
activation workflow).

## rsmp tier 0c-a final assessment PR 779

Tier 0C-A final frozen-baseline activation assessment — executed 2026-07-16
against frozen/deployed `main` `8025040089000fdca2e4c013f70397d59f436e55`.
Evidence lives in draft PR **#779** (docs/evidence only: executive founder
report, pseudonymised classification of all 944 applications,
per-application active replay deltas, terminal would-have-changed evidence,
machine-readable replay/policy evidence). Draft awaiting founder review —
facts below verified against the PR record 2026-07-16.

**Verdict: NOT READY.** The runtime model is technically reconciled with
zero unexplained deltas, but the current staging data population blocks
Tier 0C-B activation:

- 705/802 active applications classify `BLOCKED`, including 405 nonfixture;
- 363 applications would become newly approval-blocked, including 132
  nonfixture and 76 pilot-relevant nonfixture;
- 22 additional unresolved controlled labels affect 44 active applications;
- 15 unresolved service labels affect 32 active applications;
- 47 nonfixture applications are cleanup-only before recomputation;
- all 104 current compliance-review applications are blocked.

**Technical validation:** activation environment absent (runtime evaluation
false); Gate 0 hash and risk-config version/hash unchanged; application
snapshot and recomputation timestamp unchanged; PostgreSQL transaction
confirmed READ ONLY; 802 active applications replayed + 142 terminal
read-only classifications; 28/28 active scored multi-service cases use
maximum selected-service risk; 0 tier / EDD-route / High-floor changes;
0 unexplained deltas; focused Tier 0D/0C/PEP/0B tests 86 passed; CloudWatch
clean (no errors, exceptions, tracebacks, unexpected 5xx, replay/routing
failures, recomputation, config mutation, provider calls, or email sends).

**Consequence:** Tier 0C-B remains unauthorized pending explicit founder
review and the data/mapping remediation tracked as RSMP-0C-REM. No
activation, recomputation, mutation, or deployment occurred; no pilot- or
production-readiness claim.

## dci-104 first fk indexes PRs 771 774

First landings against the 54-unindexed-FK follow-up (DCI-104), merged
2026-07-16: **#771** adds `idx_agent_executions_document_id` (fixes slow
application-detail open — the document-reliance gate full-scanned
`agent_executions` per document; planner-usage regression test included).
**#774** commits the index migration independently with loud verification,
adds the migration-047 ledger file, and bounds
`monitoring_alert_evidence` growth with an `application_id` index. The
broader FK-index backlog remains open.

## screening-queue-stream PRs 756-763

Screening-queue audit remediation stream, merged 2026-07-13/15 —
**previously untracked in the master list; folded in at the 2026-07-15
reconcile** (statuses from merge commits on `main`):

- **#756** — truthful entity mode badge, horizontal scroll, page indicator.
- **#757** — correct PEP/status filters; remove provider source filter.
- **#758** — slim table 8 → 5 columns; honest registry wording.
- **#759** — audit PR-A: provenance truth, distinct labels, error state.
- **#760** — audit Phase 2: stable subject-key joins for screening entries.
- **#761** — audit Phase 3: hydrate evidence for the returned page only.
- **#763** — audit Phase 4: fixture governance, QA disposition fixtures,
  7-column layout (merge `c17135e`).
- **#769 / #770 (added 2026-07-15)** — audit Phase 4d follow-ups: seed the
  fixture client to satisfy the FK (`e8c896e`) and de-flake declared-PEP
  queue tests against fixture text patterns (`73bd05e`); merged 2026-07-15
  (merges `9d14405`, `7badf4b`).

## p9-3 PR-498

ComplyAdvantage production workspace validation (PR-PROV1) — PR **closed
unmerged 2026-07-09**; the validation itself remains **BLOCKED / NEEDS
EVIDENCE**. Operator approval, approved subjects (entity + director + UBO +
intermediary), case cap 10, billing cap USD 50, and webhook subscription
were recorded; controlled runtime screening was NOT started because the CA
dashboard/account mode could not be independently confirmed as Production
(a prior CA Mesh dashboard screenshot reportedly showed Sandbox; API
credential inference is `production_domain`). Screening requests sent after
approval = 0. Evidence pack:
`docs/audits/evidence/remediation_sprints/PR-PROV1_ca-production-provider-validation-staging_20260615T091951Z/`.
To resume: redacted dashboard evidence showing Production mode (or written
operator confirmation), then run the controlled matrix under the approved
caps. Pilot alternative: formally exclude CA production validation from
pilot scope.

## staging reset 2026-07-17 PR 788

Authorized AWS staging reset, executed 2026-07-17 at deployed main
`a10d2c3e3894b433a0435534d27bc20f03c00863` (closure docs open as draft #788;
facts verified against the PR record 2026-07-19):

- live-schema purge of all 944 founder-confirmed synthetic applications and
  scoped data; encrypted post-migration snapshot retained; zero
  application/database/S3 residue after purge and failed dry-run rollback;
- protected audit/system data retained; audit verifiers pass;
- admin-API risk-config alignment to Manufacturing 2 and D3 40/35/25 with
  zero recomputation; RSMP remained OFF; Tier 0C was not run;
- deployment run `29572158632` green (backend/PostgreSQL, Docker, PDF,
  deploy); backend 2/2, worker 6/6, ALB healthy; CloudWatch window clean;
- at reset time the 41-scenario canonical dataset was NOT yet seeded (the
  PostgreSQL canonical dry-run type mismatch blocked it — fixed by #789);
  seeding completed subsequently (see canonical stream below; validation doc
  pinned staging `9a77e11`, 41/41 `RM-PILOT-*`, zero noncanonical).

**RSMP consequence:** the population that produced the Tier 0C-A NOT READY
verdict (705/802 blocked, unresolved labels, cleanup-only apps) no longer
exists. RSMP-0C-REM's pre-reset backlog is overtaken; the next step is a
fresh 0C-A read-only assessment on the canonical baseline, then (only on a
"Ready" verdict) Tier 0C-B. No pilot- or production-readiness claim.

## canonical dataset stream PRs 784-796

Pilot Canonical Dataset v1: **#784** (41-scenario reviewed manifest, guarded
idempotent seeder, triple-gated CLI — `ENVIRONMENT=staging`,
`ALLOW_PILOT_CANONICAL_SEED=1`, confirm token, reviewed manifest hash) ·
**#789** (PostgreSQL dry-run override-flag/type fix) · **#791** (demo
completion: deterministic structured memo fixture payloads, deterministic
periodic-review dates/priority, authoritative `is_fixture` notification
suppression, monitoring fixture labels; seeder converges existing canonical
rows in place — memo/periodic-review UPDATE by id) · **#795** (document-types
fix) · **#796** (memo detail rendering compatibility). Manifest SHA-256
`fee7436a6bf6ead1cc9a8090ceaa3de7071a9b745e43f2c69a445cf74efdf9c9`
(unchanged across the stream). Re-seed procedure: dry-run then apply via
`fixtures.pilot_canonical_cli` as a one-off Fargate task on the deployed
image (post-#791 image required for the converged fixture format).

## Appendix — de-flake backlog and CI-infra notes

Test de-flake backlog (not remediation items):

- `test_fresh_install_pg_chain` — shared-DSN schema_version order-coupling.
- `test_evidence_pack_supervisor_chain` — ad-hoc batch flake.
- `test_applications_list_includes_enhanced_operational_summary_and_filters`
  — `view=list&limit=50` over the shared module DB + same-second
  `created_at` ties with no unique ORDER BY tiebreaker → seeded app can fall
  off page 1; server-side tiebreaker shipped in #720/#727, test-side
  q-scoping still wanted (hit #715 CI 2026-07-09).

CI infra: postgres service-container "PostgreSQL SSL restart timed out"
killed #717's first two runs in ~60s and masked a real ADR-0008
schema-policy gate failure — fixed by the `migration_043` marker commit
`c3e0610`; #717 green as of 2026-07-09 04:31Z. Historical note: the CI
workflow triggers on main-based PRs only, so stacked PRs (e.g. #720 on
#719's branch) get CI only after retarget.
