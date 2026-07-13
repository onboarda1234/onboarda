<!--
AUTHORITATIVE audit-remediation master list.
When the user asks for PR/phase status ("the master list", "full table", "what's
done/pending"), read THIS file, reconcile the Status/GitHub columns against live
GitHub PR states (mcp__github tools), present it, and commit any updates back
here. Phase numbering and item IDs below are canonical — do not renumber.
Legend: ✅ merged · 🟢 PR open (built, awaiting merge) · 🔨 in progress ·
📋 scoped (plan ready, not built) · ⏸ blocked (ops/vendor/legal) · ⬜ pending
-->

# Onboarda / RegMind — Audit-Remediation Master List

**Last reconciled:** 2026-07-11 (base `main` = `6ba253d`; P12-1 #738 merged + AWS-staging-validated for controlled pilot; DCI-006 staging remediation executed (#739, Codex PASS); full-consolidated audit RE-RUN folded — see [**Re-audit 2026-07-11**](#re-audit-2026-07-11-d23cc45--reconciliation) section below; prior reconcile 2026-07-10 on `84de284` after #735, staging validated `8a0fdef`/`0e1a4ee`).
**2026-07-09/11 batch — 4 of the 4 controlled-pilot CODE blockers CLOSED (merged + AWS-staging-validated):**
**#729 (P13-1, back-office stored-XSS)** PASS-with-limitation · **#728 (item 26 / BSA-002, shared fail-closed rate limiter)** PASS-with-limitation (migration v2.51 `shared_rate_limits`) · **#730 (P11-2, dep-CVE + pip-audit CI gate)** PASS-with-limitation · **#738 (P12-1, regulated-record deletion protection)** PASS for controlled-pilot closure (staging SHA/image `6ba253d49d786cd686b5d53cba80b649ff7d35cf`; backend TD `regmind-staging:827`; worker TD `regmind-verification-worker:275`; authenticated `/api/version` + `/api/readiness` 200; synthetic P12-1 app/document delete guards, fixture cleanup guard, retention purge, and v2.13 report-only boot checks passed). Also landed: **#731→#732** (Applications audit-log isolation by immutable `application_id`, migration v2.50; APP-727-001/002 CLOSED), **#733** (P9-13 role×route matrix harness), **#734** (clean-approval e2e, APP-AUD-003), **#735** (APP-AUD-001 role-UI/authz alignment). Migration sequence v2.47…v2.51 clean, no collision, no up-front-index (#717-class) bug. Staging == latest `origin/main`; the known v2.47 DCI-006 off-canon logs are **RESOLVED** — remediation SQL merged (#739, `9d597ea`) and **executed on staging 2026-07-11 (Codex PASS)**: all 3 v2.47 CHECK constraints (`clients_status_check`, `agent_executions_status_check`, `agent_executions_source_check`) now installed, off-canon counts 0, DCI-006 CloudWatch ERRORs cleared (54 unindexed FKs remain a separate open follow-up; staging remediation only — no production-readiness claim). **2026-07-11 audit re-run** additionally surfaced net-new HIGH findings (risk-parser under-scoring DCI-108/109; supervisor-route/CSRF cluster BSA-001–004 re-run) + 4 confirmed partials — see the [Re-audit 2026-07-11](#re-audit-2026-07-11-d23cc45--reconciliation) section.
**Wave A fully closed:** all four small-wins merged + deployed to AWS staging + validated
(PASS) — **#700 (SW-1)**, **#701 (SW-2, `dd28a79`, TD 788)**, **#702 (SW-3, staging-SHA
gate)**, **#703 (SW-4, `daab2bb`, TD 789)**; staging == `origin/main` == `daab2bb`.
**Overnight batch — merged + DEPLOYED to AWS staging + validated (Codex closure report):**
**#705 (P11-1)** PASS, **#706 (P11-3)** PASS-with-limitation (budget-store outage source/test-validated,
not live fault-injected), **#708 (P10-6)** PASS-with-limitation (no live sign-off audit record;
spoof rejection source/test-validated), **#707 (P11-9)** PASS. Final staging SHA `fadf8a6` ==
latest `origin/main`; backend `regmind-staging:796`, worker `regmind-verification-worker:244`;
`/api/version`+liveness+health+readiness 200; CloudWatch ERROR/Exception/Traceback/5xx = 0.
Staging-only evidence — no production-readiness claim. **Wave B built
(do-not-merge):** **#709 (P12-6 / DCI-007)**, **#710 (P12-3 / DCI-008+010+011)** — each
implemented → SQLite + live-PostgreSQL → fresh-context adversarial review → folded → pushed.
**Audit 4 (FEO) folded as Phase 12.** Consolidated 4-audit verdict: BLOCKED for
uncontrolled production, conditional for controlled pilot; P12-1 / DCI-001 is
closed for controlled pilot by #738, while DCI-027 (P9-8) remains tracked.
**Phase 9 (RDI) Wave-1 complete:** the three current-stage blocking CRITICALs are merged,
deployed (`regmind-staging:782` / worker `:230`, image `e66405a`), validated (PASS) —
**P10-1 #697 (RDI-006), P10-3 #696 (RDI-004), P10-2 #698 (RDI-001/007/011)**; merge order
#695 → #697 → #696 → #698. **#704 merged** (Codex): maker-checker narrowed to Tier 1 only
— closes the approved four-eyes scope change #697 had left outstanding. Prior batches all
merged/validated: #692/#690/#693/#691 (TDs 775/776/777), #687/#688/#689 (TDs 771/772/773),
docs #695. Incorporates REGMIND-SYSTEM-READINESS-AUDIT-1 (P9-12/13/14 +
CLIENT-PORTAL-RUNTIME-SMOKE-1 + PERIODIC-BASELINE-METHOD-HYGIENE-1), an Optional/
Post-Production Modernization section, Phase 9 (RDI audit), **Phase 10 (BSA / Audit 2 —
19 findings)**, **Phase 11 (DCI / Audit 3 — 30 findings, 6 blockers, schema UNSAFE)**, and
**Phase 12 (FEO / Audit 4 — 15 findings)**. Section order places **Phase 14 (Production
readiness) last**, after Phases 8–13 (sections renumbered per founder instruction
2026-07-08; Phase 8 is now the Monitoring alerts stream, Phase 13 the Pilot Controls Pack). **PR #699** (Codex draft, P10-1
closure-evidence docs) was **closed unmerged** — its closure record is carried here.

> Maintenance: this is the single source of truth for remediation status. On any
> request for PR/phase status, refresh the Status/GitHub columns from GitHub and
> update this file. Item IDs (1–40, 33–38, M-series, P9-1…P9-14, P10-1…P10-7, P11-1…P11-9, P12-1…P12-10, P13-1…P13-7, PR-* slugs) are canonical and NEVER renumbered — their numeric prefixes reflect the section numbering in force when each audit landed and are retained for continuity with merged PRs, audit reports, and closure evidence. Section headings were renumbered on 2026-07-08 (founder instruction), so item-ID prefixes intentionally do NOT match today's section numbers (e.g. P10-x items live in Phase 9 — RDI; P9-x items live in Phase 14 — Production readiness).

**Legend:** ✅ merged · 🟢 PR open (built) · 🔨 in progress · 📋 scoped · ⏸ blocked · ⬜ pending · 🔴 **controlled-pilot blocker (code)** · 🟠 **controlled-pilot operational gate**

> 🔴 **Controlled-pilot blockers (code).** **4 of 4 CLOSED (merged + staging-validated 2026-07-09/11):**
> - ✅ **P12-1** — Regulated-record deletion protection (DCI-001/003, CRITICAL) — Phase 11 — **CLOSED (#738; controlled-pilot scope)**
> - ✅ **P11-2** — Dependency CVE remediation + pip-audit CI gate (BSA-015, HIGH) — Phase 10 — **CLOSED (#730)**
> - ✅ **P13-1** — Back-office stored-XSS elimination (FEO-001/002, HIGH) — Phase 12 — **CLOSED (#729)**
> - ✅ **item 26** — Shared, fail-closed rate limiter (BSA-002) — Phase 4 — **CLOSED (#728)**
>
> These are the *code* blockers. A controlled pilot also has 🟠 **operational gates**, flagged 🟠 inline where they exist as rows:
> - 🟠 **item 33** — Pilot-scope guards (server-side) — Phase 13
> - ✅ **item 36** — Persisted negative-path fixtures — Phase 13 — **CLOSED for controlled pilot** (#748, #749; staging PASS 2026-07-12; staging left clean)
> - 🟠 **P13-7** — Compliance-officer SOP pack (+ refresh the pilot runbook, item 38 ✅) — Phase 13
> - 🟠 **ComplyAdvantage production workspace validation** (#498) — complete OR explicitly exclude from pilot scope — Phase 13
> - 🟠 **ops-enforce-staging-sha-alignment-gate** — staging SHA aligned + smoke-tested — Phase 7
>
> Two operational gates are **not discrete rows** (activities/decisions, not tracked items): the **Applications-page readiness audit** clear of P0/P1 (run after #719/#720), and the **PII-encryption deferral recorded as a signed risk-acceptance** (PII field encryption is a *production* item — deferred for pilot with compensating controls, not a pilot blocker). P13-1 may alternatively be *formally accepted with compensating controls* rather than fully closed.

---

## Phase 0 — Audit-integrity emergencies
| # | Title | GitHub | Status |
|---|-------|--------|:--:|
| 1 | Stop audit-trail purge (B1) | #661 | ✅ |
| 2 | Stop boot-time hash-chain rewrite (B2) | #661 | ✅ |
| 3 | Chain verify + anti-fork (H3, H12) | #661 | ✅ |
| 4 | Evidence-pack completeness (H4) | #661 | ✅ |

## Phase 1 — Client-facing misrepresentation & provenance
| # | Title | GitHub | Status |
|---|-------|--------|:--:|
| 5 | Remove client screening + lock endpoints (B4, M1) | #661 | ✅ |
| 6 | Effective-provider evidence provenance (B5) | #676 | ✅ |
| 7 | Remove fabricated portal preview rows (H1) | #661 | ✅ |

## Phase 2 — Operate as a compliance/AML platform
| # | Title | GitHub | Status |
|---|-------|--------|:--:|
| 8 | B6-B5 screening readiness + provenance | #676 | ✅ |
| 9a | H2A DSAR status honesty | #665 | ✅ |
| 9b | H2B GDPR erasure engine (wired-but-OFF) | #677 | ✅ |
| 10 | H1 memo-claim truthfulness | #670 | ✅ |

## Phase 3 — Deploy & runtime safety
| # | Title | GitHub | Status |
|---|-------|--------|:--:|
| 11 | Migrations + boot lock (B3/PC-3) | #675 | ✅ |
| 12 | Non-blocking I/O + graceful shutdown (B7) | — | ⬜ dedicated session |
| 13 | Normalize ENVIRONMENT + prod keys (H8) | #673 | ✅ |
| 14 | Singleton-guard schedulers (H9) | #674 | ✅ |
| 15 | Container healthcheck (H10) | #672 | ✅ |
| 16 | Rollback runbook (H11) | #678 | ✅ |

## Phase 4 — Hardening (fast-follow)
| # | Title | GitHub | Status |
|---|-------|--------|:--:|
| 17 | Virus-scan uploads (H5) — P0 | — | 📋 scoped (decision needed) |
| 18 | Redaction/response allow-list | #690 | ✅ |
| 19 | Resilience/fail-safe → delete dead `resilience/` | #693 | ✅ |
| 20 | Persist memo `blocked` verdict — P0 | #679 | ✅ |
| 21 | DOB/PII encryption at rest *(= Audit-3 **DCI-018 BLOCKER**: PII taxonomy across all tables — names/DOB/emails/addresses still plaintext outside the PIIEncryptor field lists)* | — | ⬜ |
| 22 | CSP headers (report-only) | #688 | ✅ |
| 23 | Session revocation | #687 | ✅ |
| 24 | CA webhook retry idempotency | [#703](https://github.com/onboarda1234/onboarda/pull/703) | ✅ merged (SW-4; merge `daab2bb`, TD 789, validated PASS; reconciler wiring = item 24b residual) |
| 25 | Unique seeded-account secrets (M14) — P0 | #681 | ✅ |
| ✅ 26 | Shared rate limiter *(= Audit-2 **BSA-002**: persist forgot-pw/doc-upload/AI keys across ECS tasks, fail-closed)* — DB-backed `shared_rate_limits` (Migration **v2.51**, `idx_shared_rate_limits_expires_at`); forgot-pw/reset/upload/AI-verify over-limit → 429; limiter keys expose no raw email/IP/token/payload | [#728](https://github.com/onboarda1234/onboarda/pull/728) | ✅ merged + Codex-validated PASS WITH LIMITATION (staging; live DB-outage fault-injection source/test-validated only) |
| 27 | audit_log tamper-evidence (core; wiring deferred) | #691 | ✅ |
| 28 | Misc M7–M12 | — | ⬜ (skip) |
| 40 | Close last silent fail-open (dead code) | #680 | ✅ |

## Phase 5 — Screening Review / Agent 3 (parallel audit)
| # | Title | GitHub | Status |
|---|-------|--------|:--:|
| — | Reconcile Agent 3 screening counts | #658 | ✅ |
| — | Registry badge normalization | #659 | ✅ |
| PR-A | No soft-green "clear" for incomplete screens | #682 | ✅ |
| PR-B | Slim Agent 3 panel + disposition | #683 | ✅ |
| PR-C | Watchlist as first-class category/count | #684 | ✅ |

## Phase 6 — Post-#661 staging follow-ups
| # | Title | GitHub | Status |
|---|-------|--------|:--:|
| 29 | `session_tokens.auto_purge=false` | #671 | ✅ |
| 30 | Drop provider names from portal comment | #668 | ✅ |
| 31 | Retention-policy seed fix + count probe | #671 | ✅ |
| 32 | De-flake periodic-review test | #669 | ✅ |

## Phase 7 — Applications page & pilot-readiness
| PR | Priority | Title | GitHub | Status |
|----|:--:|-------|:--:|:--:|
| PR-APP-STATUS-CANONICALIZATION-1 | P1 blocker | Canonical status labels + senior queue + parity | #685 | ✅ |
| PR-APP-ACTION-OWNERSHIP-SCOPE-1 | P1/P2 | Terminal decision & memo-approval ownership gate *(= Audit-4 **FEO-013**)*: final approve/reject + pre-approval + memo approval owner-gated; admin/SCO override needs `ownership_override_reason`; unassigned → auto-claim at SUCCESS commit only (failed attempts can never seize ownership); dual second leg exempt only at current HIGH/VERY_HIGH; collaboration verbs stay open. Adversarial review: 2 blockers found + redesigned away; 26 tests incl. HTTP endpoint matrix; live-PG probe PASS | [#713](https://github.com/onboarda1234/onboarda/pull/713) | ✅ merged + Codex-validated PASS WITH LIMITATION (staging `074607d`; browser smoke clean; live ownership-denial not exercised — no safe assigned fixture, RDS private; TOCTOU + assigned_to-validation residuals stand; sign-off memo awaiting founder signature) |
| 🟠 ops-enforce-staging-sha-alignment-gate | P0 | Staging-SHA gate + delete test logins | [#702](https://github.com/onboarda1234/onboarda/pull/702) | ✅ code half merged (SW-3; gate exercises on next deploy) · delete-test-logins half ⬜ ops-side · 🟠 **pilot operational gate** (staging SHA aligned + smoke-tested) |
| perf-applications-default-list-projection | P2 | Slim paginated projection is the DEFAULT `/api/applications` payload (was: full `a.*` + child hydration for 5000 rows to any caller omitting `?view=`); `?view=full` unchanged opt-in. Review fold: periodic_review projection stays a full/detail-surface field (attaching it to the auto-refreshing list would regress the hot path). Full suite 6748-green on the stack | [#719](https://github.com/onboarda1234/onboarda/pull/719) | ✅ merged + staging-validated |
| audit-log-tamper-evidence-1 | P2 | *(= Phase 4 #27)* | #691 | ✅ |
| ux-applications-list-sort-status-tabs | P3 | Whitelisted server-side sort (8 columns; COALESCE NULL-score parity SQLite↔PG, severity-rank risk_level, unique `a.id` pagination tiebreaker) + comma-status filter backing 6 grouped status tabs (proper tab ARIA); dropdown-wins conflict resolution; off-canon "(non-standard)" status safety net wired to the real load path; **fake-AI "Quick Reference" chat removed wholesale** (canned "All checks passed" responses were a misleading-claims liability). Adversarial review: 4 MAJOR folds; 13 API tests + 24-check headless-Chromium run. **Toolbar declutter** folded in later (remove Score / Enhanced Status / Next Action columns + Enhanced Review dropdown + duplicate in-page search; enhanced-review filter *logic* preserved via variable + deep-links) | [#720](https://github.com/onboarda1234/onboarda/pull/720) → **re-landed as [#727](https://github.com/onboarda1234/onboarda/pull/727)** | ✅ merged + staging-validated (PASS-with-limitation). Note: #720 was merged into the already-merged #719 branch (wrong base) so its changes never reached `main`; re-landed cleanly as #727 (`2315c62`) and deployed |
| chore-applications-deadcode-cleanup | P3 | Delete dead approval branches | [#701](https://github.com/onboarda1234/onboarda/pull/701) | ✅ merged (SW-2; merge `dd28a79`, TD 788, validated PASS) |
| CLIENT-PORTAL-RUNTIME-SMOKE-1 | P1 | Live client-credential smoke: status/upload/logout/**cross-tenant denial** *(audit REGMIND-P1-006)* — Codex-executed 2026-07-08 against staging `d4fdb3f`: full cross-tenant matrix denied (A↔B apps/docs/uploads 403; no list leakage), logout token replay 401, rate-limit + upload rejections clean, no 5xx; synthetic fixtures fully cleaned incl. S3 | [#722](https://github.com/onboarda1234/onboarda/pull/722) (worker-trace fix) | ✅ PASS — the benign limitation (cleanup racing the async verification worker → `Verification job not found` traces) is now CLOSED by #722 (merged `dd7627f`, Codex-validated PASS 2026-07-09: worker treats a cleaned-up job as `verification_job_missing_skip`, real DB/provider failures still propagate; staging window ERROR/Exception/5xx/`job not found`=0) |
| PERIODIC-BASELINE-METHOD-HYGIENE-1 | P2 | Clean 405 on POST-only periodic-review baseline route *(audit REGMIND-P2-001)* | [#700](https://github.com/onboarda1234/onboarda/pull/700) | ✅ merged (SW-1) |
| PR-RISK-SECTOR-CALIBRATION-1 | P2 | Recalibrate sector risk + "unknown≠high" defaults *(audit done; was "Backlog — after Phase 7"; also Audit-3 **DCI-009**: missing/unknown country defaults MEDIUM — treat as manual-review/HIGH)* | — | 📋 scoped |

> **Applications-page readiness audit (Codex, run against PR727 staging then re-run against `8a0fdef`).** Initial run STOPPED on a Critical (audit-log leakage); after remediation the re-run verdict is **READY FOR PILOT WITH CONTROLS / NOT PRODUCTION READY**. Findings + status:

| PR / finding | Priority | Title | GitHub | Status |
|----|:--:|-------|:--:|:--:|
| APP-727-001 | Critical | Cross-application audit-log leakage — Activity Log queried by ref-derived `target` text with no immutable scoping; reused/colliding refs returned another app's rows. Fix: add `audit_log.application_id` (Migration **v2.50** + `idx_audit_log_application_id`) and scope Activity Log / evidence-pack reads by immutable id | [#731](https://github.com/onboarda1234/onboarda/pull/731)→[#732](https://github.com/onboarda1234/onboarda/pull/732) | ✅ merged + Codex-validated (staging `8a0fdef`; isolation PASS). **Residuals:** legacy ref-only rows hidden (not backfilled); app-ref uniqueness not enforced; **decision/sign-off audit writers still leave `application_id`/`request_id` NULL → writer-side population is a follow-up (below)** |
| APP-727-002 | High | Hostile filename → S3 `TagValue invalid` → 500 on upload. Fix: sanitise S3 tag values/keys derived from filename; hostile/quote/unicode/traversal/long names now 201 | [#731](https://github.com/onboarda1234/onboarda/pull/731)→[#732](https://github.com/onboarda1234/onboarda/pull/732) | ✅ merged + Codex-validated (staging `8a0fdef`; CloudWatch `TagValue`=0) |
| APP-AUD-002 *(= P9-13)* | Med | SCO/CO/analyst role×route matrix not proven — role-test harness (5 generated-password actors + 11 fixture apps, `0600` creds, staging-only fixture exception, bulk disable); 53/53 API role checks, client denial, blocked-approval denial, ownership matrix | [#733](https://github.com/onboarda1234/onboarda/pull/733) | ✅ merged + Codex-validated PASS WITH LIMITATION (staging; analyst-UI + several runtime action paths still to prove — see P9-13) |
| APP-AUD-003 | Med | Clean *no-blocker* approval path never exercised — real portal→submit→zero-blocker→**real approve**→decision record→replay-409→blocked negative control e2e (`test_portal_to_approval_e2e.py`) | [#734](https://github.com/onboarda1234/onboarda/pull/734) | ✅ merged + Codex-validated CLOSED WITH LIMITATION (staging `0e1a4ee`; provider/doc/IDV/screening clearances fixture-assisted non-prod) |
| APP-AUD-001 | Med | UI action-gate — Approve looked active on a blocked case (backend already blocks 400/403); analyst UI/authz alignment + denied-endpoint handling | [#735](https://github.com/onboarda1234/onboarda/pull/735) | ✅ merged (role-UI/authz alignment + static authz test) — staging re-validation pending |
| APP-727-audit-writer-id-1 | Med | Populate `application_id` (and `request_id`) in audit writers, decision/sign-off first, to complete APP-727-001 immutable-id isolation | [#744](https://github.com/onboarda1234/onboarda/pull/744) | ✅ **CLOSED — #744** (staging-validated 2026-07-11, `ff47717`): decision/sign-off/memo audit rows now carry `application_id`+`request_id`; `append_audit_log` gained the two params (reuses `_resolve_audit_application_id` + `get_request_id()` contextvar); hash-chain `verified=true` (payload/`hash_version` untouched); cross-app isolation confirmed (App A row not shown on App B). Residual: lower-priority direct-insert writers still ref-only (write-forward) |
| APP-AUD-gov-dup-1 | Low | **NEW** — two accepted governance requests produced duplicate audit rows (investigate audit idempotency) | — | ⬜ pending |
| APP-AUD-005 | Low | `/api/applications` ignores `search=` (UI uses `q=`) — document or alias | — | ⬜ pending |
| APP-A11Y-SORT-HEADERS-1 | P3 | Keyboard-accessible sortable headers (`tabindex`+Enter/Space+`aria-sort`, keep `<th>` `columnheader` role) *(CodeRabbit on #727)* | — | ⬜ pending |

## Phase 8 — Monitoring alerts Page
> Monitoring-alerts remediation stream (M-series). Statuses per founder update
> 2026-07-08; items below are the remaining fixes still pending.

| Item | Status |
|------|--------|
| M2.3 QA sampling implementation | 📋 Spec drafted, not implemented |
| M1.2 status runtime audit/backfill | ⬜ Pending |
| M1.3 status CHECK hardening | ⏸ Depends on M1.2 |
| M2.4 status-sync on downstream close | ⬜ Pending |
| M3.2 expiry-missing / coverage blind-spot report | ⬜ Pending |
| M3.3 Monitoring UI cleanup | ⬜ Pending |
| M3.4 Agent 1 verification for refreshed identity docs | 📋 Decision approved, not implemented |
| Document-health scheduler Phase B/C/D rollout | ⏸ Pending explicit go/no-go |
| M4.x screening-change monitoring phase | ⬜ Not yet fully decomposed |

## Phase 9 — Regulatory Decision Integrity (RDI audit)
> Source: **RegMind Production Audit 1 — Regulatory Decision Integrity**, run against
> `c8b6dac` (current `main`, all merged remediation included). 13 findings.
> **Management response 2026-07-07** formally reclassified two: **RDI-002** (LOW/MEDIUM
> fast-path) CRITICAL → **HIGH policy-exception** (by-design, not a code defect) and
> **RDI-005** (SAR permanence) CRITICAL → **HIGH Enterprise pre-enable blocker**. Both
> are deferred (see below); current-stage **blocking CRITICALs are now 3 — RDI-001,
> RDI-004, RDI-006 = exactly Wave 1 (P10-2, P10-3, P10-1)**. Audit 2 stays paused until
> those three are remediated + re-verified. The 11 remaining findings are grouped into 7
> PRs across 3 waves. Discipline per PR: implement → full SQLite + live-PostgreSQL tests →
> fresh-context adversarial review → fold → push. Item IDs `P10-1…P10-7` are canonical.

| # | PR | Findings | Severity | What it fixes (plain) | GitHub | Status |
|---|----|----------|:--:|-----------------------|:--:|:--:|
| P10-1 | PR-RDI-1 — Server-side materiality | RDI-006 | CRITICAL | Ignore client-supplied change materiality; always classify server-side from change type via `classify_materiality(change_type)` | [#697](https://github.com/onboarda1234/onboarda/pull/697) | ✅ merged |
| P10-2 | PR-RDI-2 — Fail-closed decision & memo persistence | RDI-001, 007, 011 | CRITICAL + HIGH + MED | Decision status+audit+signoff+decision_record in one transaction; memo approve/validate roll back and 500 on save failure (no false "success") | [#698](https://github.com/onboarda1234/onboarda/pull/698) | ✅ merged |
| P10-3 | PR-RDI-3 — Risk-staleness gate | RDI-004 | CRITICAL | Block final decisions when `risk_config_version` ≠ current or recompute failed; persist recompute failures | [#696](https://github.com/onboarda1234/onboarda/pull/696) | ✅ merged |
| P10-4 | PR-RDI-4 — Per-decision-type gates | RDI-003, 008 | HIGH | Add required prerequisites for reject / escalate_edd / request_documents; block failed-validation memo from supervisor step **(needs policy decision on per-type prerequisites)** | — | 📋 scoped (decision-gated) |
| P10-5 | PR-RDI-5 — Decision-record coverage + provenance | RDI-009 (non-SAR), 010 | HIGH | Write decision_records for EDD closure / monitoring actions / change approvals / risk changes; add AI-vs-rule source + `agent_executions` link. Depends on **P10-2** | — | 📋 scoped |
| P10-6 | PR-RDI-6 — Sign-off IP attribution | RDI-012 | HIGH | Trust `X-Real-IP` only when the direct peer is a known proxy/ALB (stop browser spoofing) — XFF was already gated; the unconditional X-Real-IP fallback closed | [#708](https://github.com/onboarda1234/onboarda/pull/708) | ✅ merged |
| P10-7 | PR-RDI-7 — Append-only audit at DB level | RDI-013 (non-SAR) | MEDIUM | Separate migration/admin DB role from runtime role; revoke runtime `UPDATE`/`DELETE` on `audit_log`/`decision_records`/`supervisor_audit_log`; stop cleanup code deleting those rows *(code half ships early; grants half is RDS/infra)* | — | 📋 scoped (part ops) |

**Deferred (per management response 2026-07-07):**
- **RDI-002** — by-design LOW/MEDIUM fast-path, HIGH policy-exception (not a code defect). **P10-DOC-1:** policy **✅ APPROVED & signed off** (Aisha Sudally, 2026-07-07) at [`docs/compliance/LOW_MEDIUM_FASTPATH_APPROVAL_POLICY.md`](compliance/LOW_MEDIUM_FASTPATH_APPROVAL_POLICY.md) (eligibility = all LOW/MEDIUM; disqualifiers = sanctioned/FATF, PEP, adverse hit, stale/incomplete screening, failed IDV; approver = Onboarding Officer alone; 20% QA sampling). **Residual code assertions** (decision-record eligibility-basis stamp + direct-route test that a disqualifying signal can never fast-track) folded into the RDI (Phase 9) approval-path PRs (P10-3 / P10-5) — ⬜.
- **RDI-005** — SAR permanence (`ON DELETE CASCADE`, cleanup delete, mutable SAR content), HIGH **Enterprise pre-enable blocker**. Must be fixed **before** enabling Enterprise SAR/STR; safe to defer **only while SAR/STR feature flags stay disabled** (`ENABLE_SAR_WORKFLOW`, `ENABLE_SAR_STR` = false). Same guard covers the SAR slices of RDI-009/RDI-013. *(Re-confirmed by Audit-3 **DCI-002** — same cascade + pre-file overwrite findings; note the general SAR cleanup-delete surface is also covered by P12-1.)*

**Wave order:** W1 P10-1 → P10-2 → P10-3 (all CRITICAL; P10-2 unblocks P10-5) · W2 P10-4, P10-5, P10-6 (HIGH) · W3 P10-7 (MED/infra). P10-1 and P10-6 are small quick wins slot-able anytime.

**Closure evidence (2026-07-07):**
- **P10-1 (#697)** — **merged** (base `b577a5f`, merge `b6192fb`; ancestor of deployed HEAD `e66405a`, so live on `regmind-staging:782`). `create_change_request()` now ignores client-supplied `items[].materiality` and server-computes tier from `change_type` via `classify_materiality`; fresh-context review fold prevents server-known alert types (e.g. `control_change`) downgrading to `other`/Tier 2. Full SQLite suite 6549 passed; CM regression 217 passed; static guard asserts no `item.get("materiality")` read. **RDI-006 CLOSED/REMEDIATED** (Codex-verified; control C-11 VERIFIED for client-supplied override). **Two residuals:** (a) `change_type` itself is still client-supplied — semantic mislabeling is a future hardening item (unknown types default Tier 2); (b) the previously-approved four-eyes scope change (tier1,tier2→tier1) was not part of #697 — **since CLOSED by #704** (Codex, merge `956ed5b`): maker-checker narrowed to Tier 1 only, Tier 2 still covered by the screening hard-block.
- **P10-3 (#696)** — **merged**, deployed (`regmind-staging:781` / `regmind-verification-worker:229`, image `fbedc7c`), validated. Targeted `test_risk_staleness_gate.py` 15 passed; runtime synthetic probe confirmed current-version app proceeds, older-version app + `stale:recompute_failed` quarantine both 409-block, non-approval decisions (reject/escalate/request-docs = 201) not newly blocked. **RDI-004 CLOSED/PASS.** Residual (per design): legacy `NULL`-provenance apps blocked only after first config update/sweep.
- **P10-2 (#698)** — rebased onto #696-merged `main`, retargeted, CI green, **merged**, deployed (`regmind-staging:782` / `regmind-verification-worker:230`, image `e66405a`), validated. Targeted decision/memo/approval suite 263 passed / 2 skipped; full SQLite suite 6568 passed. Runtime probe: decision 201 persisted `decision_records_count=1` + audit + accepted governance; memo approve 200 with signoff audit; memo validate 200 persisted status+timestamp. **RDI-001 / RDI-007 / RDI-011 CLOSED/PASS.** Residual: live-DB fault injection not run (forced-failure covered by merged tests); memo-supervisor `decision_records` overlay stays scoped to P10-5/RDI-009.
- Final staging aligned to #698 merge SHA `e66405a`; `/api/version` git_sha+image_tag match; liveness/health/readiness 200 (`ready=true`); both ALB targets healthy; 30-min CloudWatch window ERROR/Exception/Traceback/HTTP-5 = 0.

**Audit-2 unpause status:** ✅ **all three current-stage blocking CRITICALs closed & validated** — RDI-006 (#697), RDI-004 (#696), RDI-001 (#698). Merge order on `main`: #695 → #697 → #696 → #698 (HEAD `e66405a`, deployed `regmind-staging:782`). The audit artifact's "remaining blockers RDI-001/RDI-004" note reflects the point-in-time when #697 was verified — both have since merged. **Audit 2 has since run** (see Phase 10 — BSA). Remaining RDI work is W2/W3 (HIGH/MED: P10-4 decision-gated, P10-5 dep-on-P10-2, P10-6, P10-7) plus the deferred RDI-002/005 items; the four-eyes scope decision is closed (#704, Tier-1-only maker-checker).

## Phase 10 — Backend Security & Authorization (BSA audit)
> Source: **RegMind Production Audit 2 — Backend Security & Authorization**, run against
> `e66405a` (PR #698 merge — post-Audit-1-closure). 19 findings (BSA-001…019).
> **Verdict: REMEDIATE BEFORE PROCEEDING** — 2 HIGH blockers (BSA-001, BSA-015); rest MED/LOW.
> **BSA-002 is not new** — it is Phase 4 item 26 (Shared rate limiter), cross-referenced there.
> Audit positively verified several controls (12-char password policy, CSRF double-submit,
> Sumsub HMAC-before-parse constant-time, mock-mode prod hard-block, no-wildcard CORS in prod,
> security headers). Many sections UNVERIFIED (not exhaustively checked; deepest gap = the
> P9-13 route×role matrix, already listed). 18 net-new findings grouped into 9 PRs, 3 waves.
> Item IDs `P11-1…P11-9` canonical. Same discipline per PR: implement → full SQLite + live-PG
> tests → fresh-context adversarial review → fold → push.

| # | PR | Findings | Severity | What it fixes (plain) | GitHub | Status |
|---|----|----------|:--:|-----------------------|:--:|:--:|
| P11-1 | Fail-closed revocation + post-await session re-validation | BSA-001, 014 | HIGH + MED | Make token-revocation persistence **mandatory** for logout / password-reset / password-change (503 + rollback, no false success); `is_revoked()`/`decode_token` fail-closed on store outage; logout-retry convergence (review fold B1); supervisor run re-validates actor post-await before persisting | [#705](https://github.com/onboarda1234/onboarda/pull/705) | ✅ merged |
| ✅ P11-2 | Dependency CVE remediation + pip-audit CI gate | BSA-015 | HIGH | pip-audit-driven minimal bumps + a `pip-audit` CI gate (fails on HIGH/CRITICAL) with a documented, dated WeasyPrint allowlist (`CVE-2026-49452`, review 2026-08-09, unused vulnerable mode). Full suite 6858 passed; Docker/PDF/Fernet/JWT compat verified | [#730](https://github.com/onboarda1234/onboarda/pull/730) | ✅ merged + Codex-validated PASS WITH LIMITATION (staging; `docker-validate` via CI, local Docker NA) |
| P11-3 | Fail-closed inputs + AI budget | BSA-006, 007, 013 | MED + LOW | `get_json()` returns structured **400** on malformed body (both BaseHandler and supervisor API); bounded-int pagination everywhere (server + supervisor routes); Claude budget **fails closed** in staging/prod/demo incl. the raw `generate()` path | [#706](https://github.com/onboarda1234/onboarda/pull/706) | ✅ merged |
| P11-4 | Offload blocking I/O off the IOLoop | BSA-004, 005 | MED | Move WeasyPrint PDF render and in-request Claude document-verify to a worker/executor; replace `time.sleep` backoff; enforce per-user/app AI quotas *(coordinate with item 12 / B7)* | — | 📋 scoped |
| P11-5 | AI prompt sanitisation + output schema + circuit breaker | BSA-011, 012 | MED | Apply the deep/3-pass sanitiser to **all** `generate()` inputs; replace raw-token enum parsing with Pydantic schemas (AI free-text advisory only); add source-controlled, DB-persisted circuit breaker around Anthropic/Sumsub/S3 | — | 📋 scoped |
| P11-6 | AuthZ & audit hardening | BSA-003, 009 | MED | Require recent re-auth / second factor on admin password-reset (+ mandatory revocation); route all change-management 403 denials through `log_authz_denial()` | — | 📋 scoped |
| P11-7 | Document-download attachment + webhook signature hygiene | BSA-008, 010 (+ DCI-017) | MED + LOW | Force `Content-Disposition: attachment` on all uploaded-doc downloads (separate sanitised preview endpoint if previews needed); document/opaque webhook invalid-sig response; remove ComplyAdvantage legacy signature fallback; *(DCI-017)* no silent local-disk fallback when S3 fails in staging/prod + MIME from server allowlist not stored value | — | 📋 scoped |
| P11-8 | Supply-chain pinning | BSA-016, 017, 019 (= DCI-022/024) | MED + LOW | SHA-pin GitHub Actions (all 4 workflows, exact-release comments, annotated tags peeled); split test deps into `requirements-dev.txt` (flake8 now pinned too); pin Docker base image by manifest-list digest + `.dockerignore` excludes uploads/data/logs; 8 guard tests prevent regression. Residual: CI service container + dev compose still on mutable postgres tags (out of scope) | [#712](https://github.com/onboarda1234/onboarda/pull/712) | ✅ merged + Codex-validated PASS (2026-07-08; BSA-016/017/019 CLOSED; SHA-refresh process + CI service-container pinning remain ops decisions) |
| P11-9 | CI coverage-gate fail-closed | BSA-018 (= DCI-026) | LOW | Unparseable coverage now FAILS the build (empty-COV branch exits 1) | [#707](https://github.com/onboarda1234/onboarda/pull/707) | ✅ merged + deployed (`fadf8a6`, PASS) |

**Cross-ref:** **BSA-002** (share/persist rate limits across ECS tasks — forgot-pw, doc-upload, AI keys, fail-closed) = existing **Phase 4 item 26 "Shared rate limiter"** (⬜). Fold BSA-002's specifics there rather than duplicate here.

**Wave order:** W1 P11-1, P11-2 (both blockers — clear before pilot/prod) · W2 P11-3…P11-7 (MED) · W3 P11-8, P11-9 (LOW/supply-chain/CI).

## Phase 11 — Data Integrity, Compliance Logic & Infrastructure (DCI audit)
> Source: **RegMind Production Audit 3 — Data Integrity, Compliance Logic and Infrastructure**,
> run against `956ed5b` (#704 merge). 30 findings (DCI-001…030). Schema safety rated
> **UNSAFE** (regulated-record deletion paths + admitted schema drift). Verdict:
> **REMEDIATE BEFORE PROCEEDING** — 6 blockers (DCI-001, 003, 012, 018, 019, 027) plus 1
> Enterprise pre-enable blocker (DCI-002). Positives verified: risk-config save validates
> 5 dimensions/weight=100; sanctioned/FATF floor rules present in rule engine (12
> elevation/floor rules enumerated); supervisor contradiction logic VERIFIED; Agent 9
> properly deferred/guarded; presigned-URL expiry bounded.
> **11 of 30 findings are already tracked elsewhere** — cross-referenced, NOT duplicated:
> DCI-002 = RDI-005 (deferred Enterprise SAR blocker, Phase 9 — RDI) · DCI-009 =
> PR-RISK-SECTOR-CALIBRATION-1 (Phase 7) · DCI-017 → folded into P11-7 · DCI-018 =
> Phase 4 item 21 (now an **Audit-3 BLOCKER**) · DCI-019 = P9-1 (now an **Audit-3
> BLOCKER**) · DCI-022/024 = P11-8 · DCI-023 = P9-4 (IaC) · DCI-026 = P11-9 ·
> DCI-027 = P9-8 (**CRITICAL blocker**, environment-required) · DCI-030 = P9-10.
> The 19 net-new findings group into 10 PRs. Item IDs `P12-1…P12-10` canonical. Same
> discipline per PR: implement → full SQLite + live-PG tests → fresh-context adversarial
> review → fold → push.

| # | PR | Findings | Severity | What it fixes (plain) | GitHub | Status |
|---|----|----------|:--:|-----------------------|:--:|:--:|
| ✅ P12-1 | Regulated-record deletion protection | DCI-001, 003 | CRITICAL + HIGH | Blocks unsafe regulated-record hard deletes at runtime choke points while preserving sanctioned retention/fixture contexts; v2.13 startup cleanup is report-only/non-destructive; fixture cleanup requires marker/confirmation; retention purge remains evidence-backed | [#738](https://github.com/onboarda1234/onboarda/pull/738) | ✅ merged + AWS-staging-validated PASS (2026-07-11, merge `6ba253d`, backend TD `regmind-staging:827`, worker TD `regmind-verification-worker:275`; `/api/version.git_sha` + `image_tag` matched merge SHA; `/api/liveness`, `/api/health`, authenticated `/api/readiness` passed; synthetic app/document delete denial, v2.13 report-only boot check, retention purge, fixture cleanup guard, and CloudWatch checks passed) · **CLOSED for controlled pilot; no production-readiness claim** |
| P12-2 | Change-implementation fail-closed recompute + audit-in-transaction | DCI-012, 013 | HIGH + MED | Per-request quarantine sentinel `stale:cm_recompute_pending:<id>` stamped in the SAME txn as implement whenever the change requires risk (staleness gate blocks approvals until recompute verifies against stored provenance); CAS-guarded recompute persistence; approve/reject/implement audit rows written pre-commit. Review folds: predicate parity (M1), PATCH-path recompute (M2), gate hoist. 18 tests; live-PG probe 17-green. Residual follow-up: no enforcement path for already-approved applications (M3) | [#715](https://github.com/onboarda1234/onboarda/pull/715) | ✅ merged + Codex-validated PASS WITH LIMITATION (2026-07-09, staging `02f5538`; DCI-012/013 **CLOSED**; live runtime: risk-relevant CR implemented with `risk_recompute_quarantined:false` + sentinel-stamped app blocked from approval with 409; fault-injection paths source/test-validated only; M3 already-approved-apps residual stands) |
| P12-3 | Compliance-logic corrections | DCI-008, 010, 011 | HIGH + HIGH + MED | Risk-config load failure fails CLOSED in staging/prod (no silent hardcoded-default model); memo `jur_rating` actually mutates to VERY_HIGH when `SANCTIONED_COUNTRY_FLOOR` is claimed; fix `MULTI_GAP_ESCALATION` branch order (≥4 checked before ≥3). Review folds: PG/JSONB parse hole closed (`safe_json_loads` coerced malformed scalars to `{}` before validation); recompute/boot-repair/correction/EDD-tier laundering paths all re-raise; boot-time CRITICAL probe. **Deploy precondition: validate live staging risk_config row first (see PR)** | [#710](https://github.com/onboarda1234/onboarda/pull/710) | ✅ merged (2026-07-08; deploy precondition — validate live staging risk_config row — remains for Codex sign-off) |
| P12-4 | Migration hard-stops + schema-drift detection | DCI-005, 004 | HIGH | Reject `MIGRATION_FAILURE_MODE=continue` when ENVIRONMENT is staging/production — **DCI-005 half shipped in [#711](https://github.com/onboarda1234/onboarda/pull/711)** (override ignored + ERROR on every boot; dev/test/demo keep it; clean adversarial review). Still scoped: DCI-004 startup drift check comparing declared constraints/FKs/columns vs live schema, fail-closed in staging/prod | [#711](https://github.com/onboarda1234/onboarda/pull/711) | ✅ DCI-005 half merged (2026-07-08); DCI-004 drift check still 📋 |
| P12-5 | Status-column CHECK constraints | DCI-006 | MED | Canon-constant CHECK constraints for all 8 status/enum columns (Migration v2.47, steady-state no-op boots via constraint-def comparison; fail-closed `clients.status` backfill; SQLite→PG migrator per-row SAVEPOINTs). Bonus repair: `Severity.WARNING` added to supervisor enum — 6 call sites crashed with AttributeError before their audit INSERT. 20 tests; live-PG probe 19-green | [#716](https://github.com/onboarda1234/onboarda/pull/716) | ✅ merged + deployed. **Residual RESOLVED 2026-07-11 (Codex PASS):** the 3 constraints (clients.status, agent_executions.status/source) had been SKIPPING on staging because of 4 legacy off-canon rows; remediation SQL `scripts/dci006_staging_remediation.sql` merged (#739, `9d597ea`) and **executed on staging** (task def `826`) — 680 `fixture`→`ai`, 1 `disabled`→`inactive`, and execution `id=1` (synthetic QA app `pr4-auto-7f861903` = "PR4 Monitoring Automation Smoke", created 2026-05-27, no linked client) canonicalized `direct_probe`/`staging_direct_probe`→`error`/`ai` as a **provenance-guard-flagged, human-reviewed synthetic exception** (row preserved, not deleted). v2.47 INFO-logged all 3 constraints installed; off-canon counts 0; DCI-006 CloudWatch ERRORs cleared. Re-audit **DCI-104** also flagged **54 unindexed FKs** (separate follow-up, open) |
| P12-6 | PG pool connection validation | DCI-007 | MED | Pre-ping (`SELECT 1`) on pool checkout; discard/retry stale connections after RDS failover | [#709](https://github.com/onboarda1234/onboarda/pull/709) | ✅ merged (2026-07-08) |
| P12-7 | Verification-matrix fidelity | DCI-014, 015 | MED + LOW | HYBRID checks go to Claude ONLY on deterministic INCONCLUSIVE (never override a deterministic FAIL), per the matrix policy; resolve the 5 TODO enhanced-requirement document mappings with compliance sign-off | — | 📋 scoped |
| P12-8 | Retention purge enforceability + purge-log evidence | DCI-020, 021 | MED | 7 manual categories documented (`docs/compliance/MANUAL_PURGE_PROCEDURE.md` + CLI recorder); `data_purge_log` gains subject/application/tables/per-table-counts/batch-id/evidence columns (Migration v2.48) written in ONE txn with the DELETE. Review MAJOR fold: legacy `purged_by`→users FK dropped — the scheduler identity would have failed EVERY PG purge forever. 21 tests; live-PG probe 8-green | [#717](https://github.com/onboarda1234/onboarda/pull/717) | ✅ merged + deployed. Boot-crash hotfix [#723](https://github.com/onboarda1234/onboarda/pull/723): the up-front `idx_purge_log_batch` index was moved *into* the v2.48 migration (after `ADD COLUMN`) — it had crashed existing-DB boot (`column purge_batch_id does not exist`) and failed deploy #975; upgrade-path regression test added |
| P12-9 | Observability hardening | DCI-028, 029 | MED | Forced JSON logs in staging/prod across BOTH pipelines (kills staging double-emission); contextvar request-correlation ids (sanitised `c-` prefixed `X-Request-ID`, echoed header, auto-injected into structured + root-logger lines, persisted on `audit_log` rows — Migration v2.49, worker `job-*` ids); readiness gains disk-capacity gate + tight-timeout S3 probe (403 = reachable_permission_limited, non-gating). 30 tests; live-PG probe 5-green. Residual: legacy direct `INSERT INTO audit_log` sites keep request_id NULL | [#718](https://github.com/onboarda1234/onboarda/pull/718) | ✅ merged + deployed (staging `5d6ba3e`) |
| P12-10 | Infra guards | DCI-016, 025 | MED + LOW | Enforce upload body-size before full buffering (server/proxy level; handler check stays as second line); deploy workflow FAILS when ECS `services-stable` times out *(partially mitigated by #702's SHA-alignment gate — stability half still open)* | — | 📋 scoped |

**Wave order:** W1 P12-1, P12-2 (code blockers) — the other Audit-3 blockers live elsewhere: item 21 (DCI-018), P9-1 (DCI-019), P9-8 (DCI-027) · W2 P12-3…P12-9 · W3 P12-10.

## Phase 12 — Frontend & Operational Readiness (FEO audit)
> Source: **RegMind Production Audit 4 — Frontend & Operational Readiness**, run against
> `57890e3` (#702 merge). 15 findings (FEO-001…015). Consolidated 4-audit verdict:
> **BLOCKED** for uncontrolled production; **conditional for controlled pilot** with
> documented manual controls. The only remaining CRITICALs across all 4 audits are
> **DCI-001** (= P12-1) and **DCI-027** (= P9-8) — both already tracked.
> Positives verified: token in httpOnly cookie / in-memory only (no localStorage);
> portal password fields + 12-char policy mirror; prescreening inputs are allowlisted
> selects; client cannot set status; portal renderers use `escapeHtml()` far more
> consistently than backoffice.
> **8 of 15 findings are already tracked elsewhere** — cross-referenced, NOT duplicated:
> FEO-008 = P9-4/P9-5 (prod provisioning + deploy/rollback drill) · FEO-009 = DCI-027 =
> P9-8 · FEO-010 = P9-7 (secrets-rotation half) · FEO-011 = P9-10 (+ DCI-030) ·
> FEO-012 = P9-2 (PC-1 evidence-pack continuity residual + supervisor-export hash
> stripping) · FEO-013 = PR-APP-ACTION-OWNERSHIP-SCOPE-1 (Phase 7) · FEO-015 = Optional
> Modernization §2 (frontend rework/profiling).
> The 7 net-new findings group into 6 PRs + 1 ops/docs pack. Item IDs `P13-1…P13-7`
> canonical. Frontend PRs touch `arie-backoffice.html` / `arie-portal.html` only.

| # | PR | Findings | Severity | What it fixes (plain) | GitHub | Status |
|---|----|----------|:--:|-----------------------|:--:|:--:|
| ✅ P13-1 | Backoffice stored-XSS elimination | FEO-001, 002 | HIGH | Escaped/`textContent` the API-interpolated fields in the memo (`renderMemoSections`) + supervisor/audit renderers; enum→class badge maps; XSS regression fixtures. Scope held to the named high-risk renderers (screening/notes/document-metadata documented as follow-up) | [#729](https://github.com/onboarda1234/onboarda/pull/729) | ✅ merged + Codex-validated PASS WITH LIMITATION (staging; runtime malicious-fixture injection source/test-validated only) |
| P13-2 | Single API wrapper + consistent CSRF | FEO-003 | MED | Route all 23 backoffice + portal raw `fetch()` sites through `boApiCall`/`apiCall`; state-changing calls fail closed client-side without a CSRF token; consistent `credentials: 'include'` (incl. logout + uploads + supervisor-run) | — | 📋 scoped |
| P13-3 | Defensive API response parsing | FEO-004 | MED | Check status + `Content-Type` BEFORE `res.json()` in both wrappers; handle 401 before JSON-dependent logic; text/error-envelope fallback for ALB/proxy HTML errors | — | 📋 scoped |
| P13-4 | App-detail render race guard | FEO-005 | MED | Monotonic request nonce / expected-ref check in `openAppDetail`→`renderAuthoritativeAppDetail`; ignore stale responses so Application A can never render over Application B's context | — | 📋 scoped |
| P13-5 | Role-UI fail-closed until matrix loads | FEO-006 | LOW | Privileged controls hidden/disabled with a loading/retry state until the RBAC matrix is fetched (today UI deliberately fails open; backend remains the gate) | — | 📋 scoped |
| P13-6 | Portal intake PII out of sessionStorage | FEO-007 | MED | Persist company-intake state via the authenticated server-side save/resume path; keep only an opaque resume handle client-side; clear legacy `arie_company_intake_state` on load | — | 📋 scoped |
| 🟠 P13-7 | Compliance-officer SOP pack | FEO-014 | MED (ops/docs) | Officer onboarding/training SOP, pre-approval review checklist, `INCONSISTENT` supervisor-verdict handling, senior escalation, override + evidence-export procedures | #745 | ✅ docs merged (#745, 02eeae5062d1f1d8f77e7ca69c4629bac72c57b0) · 🟠 **GATE STILL OPEN — closure requires the executed Section 16 sign-off (named/trained officers, approved scope, provider mode, monitoring operational-status checklist, signatures)** |

**Wave order:** W1 P13-1 (the two HIGH stored-XSS findings — officer-session code execution) · W2 P13-2…P13-6 · P13-7 alongside (docs, non-code).

## Phase 13 — Pilot Controls Pack
| # | Title | GitHub | Status |
|---|-------|--------|:--:|
| 🟠 33 | Pilot-scope guards (server-side) **— pilot operational gate** | — | ⬜ |
| 34 | Dashboard API performance (15.1s → sub-2s) | — | ⬜ |
| 35 | Screening full-evidence hydration performance | — | ⬜ |
| ✅ 36 | Persisted negative-path fixtures **— controlled-pilot staging evidence** | #748, #749 | ✅ |
| 37 | Lower-privilege fixture authz regression tests | #692 | ✅ |
| 38 | Pilot operations runbook | #689 | ✅ |
| 🟠 — | ComplyAdvantage production workspace validation **— pilot operational gate** (complete OR explicitly exclude from pilot scope) | #498 | ⏸ blocked (dashboard-mode evidence) |

**Item 36 closure evidence (2026-07-12).** #748 introduced the registered
negative-path fixture substrate; #749 moved it to the reserved
`FX-ITEM36-*` namespace after the original ARF refs collided with long-lived
staging rows. Hotfix merge `6197734bc7a64ee83fba6e261625c8b6ec45a856`
was deployed through staging run `29204426827`. The complete synthetic
walkthrough passed: 12 logical fixtures / 13 application refs seeded, all refs
read back through the authenticated API, a second seed retained every root and
child ID/count, blocked approval returned 400, terminal replay returned 409,
cross-client access returned 403, and the representative P12-1 direct delete
was denied without mutation. Sanctioned cleanup left zero Item 36 residue, and
all 12 previously occupied ARF refs (including non-fixture application
`acf4ade81e694d31`) remained unchanged. Retention decision: **A — staging left
clean**. CloudWatch was clean of runtime/deploy/fixture errors; one corrected
read-only operator preflight query produced a wrapper `IndexError` and no
mutation, classified separately as validation-harness noise. This closes Item
36 for controlled-pilot scope only and is not a production-readiness claim.

## Phase 14 — Production readiness
| # | Item | Type | GitHub | Status |
|---|------|:--:|:--:|:--:|
| P9-1 | Enable live GDPR erasure (PC-4 control pack) *(= Audit-3 **DCI-019 BLOCKER**: dual-control live erasure incl. S3/file deletion)* | code | — | ⬜ |
| P9-2 | Close PC-1 evidence-pack continuity residual *(+ Audit-4 **FEO-012**: supervisor audit export strips hash fields; ship a hashes-only global continuity ledger / anchored checkpoints so a regulator can verify chain continuity from an export)* | code | — | ⬜ |
| P9-3 | ComplyAdvantage prod workspace validation | ops/vendor | #498 | ⏸ |
| P9-4 | Provision prod environment (app.regmind.co) *(+ Audit-3 **DCI-023**: ECS task defs/IAM/subnets/SGs into source-controlled IaC; + Audit-4 **FEO-008**)* | ops | — | ⬜ |
| P9-5 | Drill prod deploy + rollback *(+ Audit-4 **FEO-008**: prod-specific runbooks are staging-only today — validate with a drill + evidence)* | ops | — | ⬜ |
| P9-6 | Load/performance test at prod scale | test/ops | — | ⬜ |
| P9-7 | Pen test + security review + vuln scanning *(+ Audit-4 **FEO-010**: documented + REHEARSED secret-rotation procedures — Fernet multi-key re-encrypt, JWT invalidation comms, provider keys, DB password)* | security | — | ⬜ |
| P9-8 | DR/backup drill (restore/PITR) *(= Audit-3 **DCI-027 CRITICAL BLOCKER** = Audit-4 **FEO-009**: RDS backups/PITR/deletion-protection + documented restore test + prod RTO/RPO, environment-required)* | ops | — | ⬜ |
| P9-9 | Legal/compliance sign-off (residency, DPA, regulator) | legal | — | ⬜ |
| P9-10 | Prod monitoring/alerting/on-call *(+ Audit-3 **DCI-030** + Audit-4 **FEO-011**: on-call rotation, 15-min human escalation, confirmed SNS subscription, tested first page)* | ops | — | ⬜ |
| P9-11 | Close parked prod-posture decisions (PR-25 + PR-17) | decision | — | ⬜ |
| P9-12 | ECR-IMMUTABLE-TAGS-1 — make ECR image tags immutable (rollback provenance) *(audit REGMIND-P2-004)* | ops | — | ⬜ |
| P9-13 | Full authz / tenant-isolation **route matrix** audit (role-by-route) *(audit §7)* | security | [#733](https://github.com/onboarda1234/onboarda/pull/733) | 🟢 harness built + merged (APP-AUD-002): 5-role matrix, 53/53 API checks, client denial, ownership matrix PASS. **Residual for full close:** analyst-UI alignment (#735), runtime coverage of successful approval/dual-control/memo-approve/screening-2nd-review/IDV, and harness cross-client seed fix |
| P9-14 | Registry KYB (OpenCorporates) **simulated → real/production** *(audit prod blocker)* | code/vendor | — | ⬜ |

---

## Re-audit 2026-07-11 (`d23cc45`) — reconciliation

> Source: **RegMind Production Audit — full consolidated re-run**, executed against
> `main` = `d23cc45` (after the 2026-07-09/10 batch). Read-only Codex re-verification
> CONFIRMED every finding below.
> **⚠️ ID-collision caveat:** this re-run renumbered findings into a **1xx-series**
> (RDI-101…, DCI-101…123, FEO-101…113) plus a fresh **BSA-001…021** set — these re-run
> IDs are **DISTINCT from the identically-numbered original findings** (e.g. re-run
> **BSA-001 ≠ original BSA-001**/revocation-fail-open, which is CLOSED via #705; re-run
> BSA-016/019 ≠ the original supply-chain BSA-016/019 in P11-8/#712). Findings are
> described by content + code evidence so there is no ambiguity, and each is
> cross-referenced to the canonical tracked item it refines. Nothing here is renumbered
> into the canonical P-series — these are net-new or partial-reopen entries only.

**A. Net-new findings (read-only re-verify CONFIRMED):**

| Re-run ID | Sev | Finding (code evidence) | Status |
|-----------|:--:|-------------------------|:--:|
| 🔴 DCI-108 | HIGH | Risk parser **under-scores** "very complex" ownership structure → 3 at `rule_engine.py:1219-1273`; combined with DCI-109 can flip an application **MEDIUM→LOW** (risk understatement). Fix = exact-enum rewrite + recompute. **Pilot-relevant** | ⬜ scoped (fix PR offered) |
| 🔴 DCI-109 | HIGH | "non-regulated" resolves to 1 via **dict-ordering fall-through** at `rule_engine.py:1219-1273` (should score higher); same MEDIUM→LOW flip risk. **Pilot-relevant** | ⬜ scoped (same fix PR) |
| DCI-110 | MED | Middle-band turnover 500k–5m **OVER-scores** to 4 at `rule_engine.py:1219-1273`. *(Correction: the audit/my earlier note called this under-scoring — it is over-scoring; severity corrected HIGH→MED.)* | ⬜ scoped |
| ✅ BSA-001 (re-run) | HIGH | Supervisor routes subclass `tornado.web.RequestHandler` via `SupervisorBaseHandler` (`supervisor/api.py:200`; 14 handlers; registered in `get_supervisor_routes()` `~:646`). They **already do bespoke JWT/role auth** (`require_auth`), so the gap is missing CSRF/security-header/request-id/rate-limit middleware **+ a wildcard `Access-Control-Allow-Origin: *`** on authenticated APIs — consolidate onto `BaseHandler` (its `prepare()` `~:243` wires CSRF; app sets `xsrf_cookies=False`). | ✅ **CLOSED — #743** (staging-validated 2026-07-11, `5c255e8`; backend TD `regmind-staging:829`, worker `:277`): supervisor routes on `BaseHandler` (cookie-CSRF enforced via `prepare()`, Bearer path intact), wildcard CORS removed |
| ✅ BSA-002 (re-run) | HIGH | Supervisor actor is **client-forgeable**: `ReviewSubmitHandler` (`supervisor/api.py:372`) and `EscalationHandler` (`:438`) fetch `user = require_auth(...)` but then persist actor from **request-body** fields (`reviewer_id`/`reviewer_name`/`reviewer_role`; `escalated_by`/`escalated_by_role`). Server must derive the actor from the session, never the body. | ✅ **CLOSED — #743** (staging-validated `5c255e8`): `get_server_actor()` — forged body actor ignored, session actor+role persisted (probe stored role `sco`); conflicts logged |
| ✅ BSA-003 (re-run) | HIGH | Supervisor reviews/overrides/escalations persist via **raw `sqlite3`** in **`arie-backend/supervisor/human_review.py`** (`HumanReviewService`; `import sqlite3` `~:22`, `submit_review` `~:251`, `escalate_case` `~:378`); every write is guarded by `if self.db_path:` (silently skipped when unset) and `setup_supervisor(db_path)` always passes a SQLite path → on staging (PostgreSQL) records land on ephemeral container disk. Fail-closed PostgreSQL persistence required. **Pilot-relevant** (audit-record loss). | ✅ **CLOSED — #747** (staging-validated 2026-07-12, `f3754cd`; backend TD `regmind-staging:832`, worker `:280`; **migration v2.52**): the 3 tables (`supervisor_human_reviews`/`_overrides`/`_escalations`) now durable in main PostgreSQL (11 indexes, legacy ids→text, `/app/arie.db` absent, evidence in PG); mirrors the `supervisor/audit.py` `get_db()` pattern; actor server-derived; `request_id` via contextvar; fail-closed source/CI/rollback-validated (live DB-failure injection not run on staging); **all 3 tables P12-1-classified as regulated**. |
| ✅ BSA-004 (re-run) | HIGH | **General CSRF bypass**: `check_xsrf_cookie()` (`base_handler.py` `~:527`) does `if "/webhook" in self.request.uri: return` — a substring match on the full URI (query string included), so ANY path containing `/webhook` skips CSRF. Replace with an exact **`self.request.path`** allowlist of the only two real webhooks (`/api/kyc/webhook`, `/api/webhooks/complyadvantage`); also fix the sibling `_csrf_exempt_paths` `.uri` match. **Pilot-relevant**. | ✅ **CLOSED — #743** (staging-validated `5c255e8`): exact `.path` allowlist — substring `/webhook` and `?=/webhook` query both 403; both real webhooks still reach signature verification (401 on missing sig) |

> **Consolidation intent** (BSA-001–004): one PR = supervisor routes onto BaseHandler +
> server-derived actor + fail-closed PostgreSQL persistence + exact-path CSRF allowlist.
> 🔴 **pilot-relevant** for BSA-003/004.

**B. Merged items re-flagged PARTIAL (read-only re-verify CONFIRMED):**

| Re-run ID | Refines | Finding still open | Status |
|-----------|:--:|--------------------|:--:|
| BSA-016 (re-run) | item 26 / #728 shared limiter | AI-route limiter gaps: `/api/documents/{id}/verify` + **both** supervisor pipeline triggers are unlimited; enhanced-upload limiter still process-local | ⬜ partial |
| BSA-019 (re-run) | P11-8 / #712 supply-chain | No hash-pinned lockfile / `pip install --require-hashes`; deps pinned by version only | ⬜ partial |
| RDI-107 (re-run) | P10-6 / #708 IP attribution | Trusted-proxy check trusts ANY private/loopback peer — `base_handler.py:811-847` returns `ip.is_private or ip.is_loopback`; needs an explicit proxy-CIDR allowlist | ⬜ partial |
| DCI-104 (re-run) | P12-5 / #716 + DCI-006 | 3 v2.47 CHECK constraints were **ABSENT** on staging **+ 54 unindexed FKs**. *(Correction: the DCI-006 remediation SQL was NOT "on main" — it landed via **[#739](https://github.com/onboarda1234/onboarda/pull/739)**, MERGED `9d597ea`.)* | ✅ #739 merged + **STAGING EXECUTED 2026-07-11 (Codex PASS)** — all 3 constraints installed, off-canon counts 0, DCI-006 CloudWatch noise cleared; execution `id=1` resolved as a reviewed synthetic exception · ⬜ 54-FK-index follow-up (open) |

**Process-hygiene follow-up (from the DCI-006 remediation):** the DCI-006 off-canon
values (`direct_probe`/`staging_direct_probe`) were injected into a **regulated table**
(`agent_executions`) by a **direct staging-DB probe** during an earlier automated validation
sprint — bypassing the app and the P12-1 `DBConnection` interceptor (which is exactly why
they were off-canon and un-catchable in-process). ⬜ **New LOW follow-up:** staging QA/validation
must not write raw SQL into regulated tables; route probe writes through the app or an
explicitly-marked fixture path so the interceptor and provenance flags stay authoritative.

**Net effect on readiness:** the earlier "≈94–96% pilot-ready" estimate was **walked back** on
the re-run. Since then the **entire BSA-001–004 supervisor/security cluster is CLOSED** (#743 +
#747, both staging-validated) — including the two pilot-relevant items (BSA-003 audit-record loss,
BSA-004 general CSRF bypass). The remaining pilot-relevant re-run item is **DCI-108/109** (risk
understatement, can flip MEDIUM→LOW) — retriage pending founder decision; the risk-parser fix
(exact-enum rewrite + recompute) is offered as the next code PR. DCI-110 (MED, over-scoring),
BSA-016/019 and RDI-107 (partials), and DCI-104's 54 unindexed FKs remain open follow-ups.

---

## Optional / Post-Production Modernization (NOT required for pilot or first production cut)

> These are **elective** architecture/scale/enterprise upgrades to consider
> *after* production launch. They are tracked separately from the remediation
> roll-up. Risk column = impact of the change itself on running workflows:
> 🟢 additive/safe · 🟡 modifies live path (guardable by flag/parallel-run/test) ·
> 🔴 modifies live path (intrinsic — cannot be made fully additive).
> **Cleared?** column: ✅ already done · 🟡 partially done · 🟢 already on the
> remediation list above · — not started.

### 1. Monolithic `server.py` decomposition
| # | Step | Impl. risk | Cleared? |
|---|------|:--:|:--:|
| 1.1 | Characterization/contract tests before any move | 🟢 | — |
| 1.2 | Extract handlers into `handlers/<domain>.py` (strangler) | 🟡 | 🟡 partial — `auth.py`, `base_handler.py` already extracted; bulk of handlers still in `server.py` |
| 1.3 | Split route table into per-domain lists | 🟡 | — |
| 1.4 | Extract shared concerns (DB wrapper, auth decorators) | 🔴 | 🟡 partial — auth/base_handler extracted |
| 1.5 | Add CODEOWNERS per module | 🟢 | — |

### 2 & 3. Frontend modernization (Vite + React + TS)
| # | Step | Impl. risk | Cleared? |
|---|------|:--:|:--:|
| 2.1 | Stand up Vite + TS in new `frontend/` workspace | 🟢 | — |
| 2.2 | Choose React + TypeScript (decision) | 🟢 | — |
| 2.3 | Typed API client / OpenAPI contract | 🟢/🟡 | — |
| 2.4 | Migrate back-office screens page-by-page (flag/parallel) | 🟡 | — |
| 2.5 | Component + Playwright E2E tests | 🟢 | — *(Playwright pre-installed in env; no FE tests yet)* |
| 2.6 | Migrate client portal (later) | 🟡 | — |

### 4. SQLite / PostgreSQL dual support
| # | Step | Impl. risk | Cleared? |
|---|------|:--:|:--:|
| 4.1 | Run migrations against real PostgreSQL in CI | 🟢 | ✅ **done** — CI creates a fresh PG DB and runs the full suite (`ci.yml`) |
| 4.2 | Migration round-trip / idempotency tests | 🟢 | ✅ **largely done** — `tests/test_migration_*` (004–026 idempotency, chain, backfill-replay) |
| 4.3 | Make SQLite dev-only (decision + docs) | 🟡 | — |
| 4.4 | Forward-migration safety policy + docs | 🟢 | 🟡 partial — `scripts/check_schema_migration_policy.py` gate runs on PRs |
| 4.5 | Pre-deploy migration gate in deploy workflow | 🔴 | — |

### 5a. IaC & autoscaling
| # | Step | Impl. risk | Cleared? |
|---|------|:--:|:--:|
| 5a.1 | Codify ECS/RDS/Secrets/ALB in Terraform (import) | 🔴 | — *(overlaps P9-4)* |
| 5a.2 | ECS desired count ≥ 2 across AZs | 🟡 | ✅ appears satisfied — audit shows 2 healthy ALB targets (staging) |
| 5a.3 | ECS Service Auto Scaling policies | 🟡 | — |
| 5a.4 | Confirm uploads→S3 / no SQLite in prod | 🔴 | ✅ **largely done** — S3 upload path present; `DATABASE_URL` required in prod (PR-13 #673) |

### 5b. HA / DR
| # | Step | Impl. risk | Cleared? |
|---|------|:--:|:--:|
| 5b.1 | RDS Multi-AZ + backups + PITR | 🟡 | ✅ done on **staging** (audit: Multi-AZ, deletion protection, 7-day retention); prod RDS not yet provisioned |
| 5b.2 | DR runbook + restore drill | 🟢 | 🟢 on list — **P9-8** |
| 5b.3 | Deploy rollback automation + circuit breaker | 🔴 | 🟡 partial — rollback *runbook* done (PR-16 #678); automation/circuit-breaker pending |
| 5b.4 | Provision production env via IaC | 🔴 | 🟢 on list — **P9-4** |

### 5c. Enterprise identity & compliance
| # | Step | Impl. risk | Cleared? |
|---|------|:--:|:--:|
| 5c.1 | SSO (SAML 2.0 / OIDC) for officers | 🔴 | — |
| 5c.2 | MFA / TOTP for officer logins | 🟡→🔴 | — |
| 5c.3 | RBAC formalization | 🔴 | 🟡 overlaps P9-13 route-matrix audit |
| 5c.4 | SOC 2 / ISO 27001 readiness | 🟢 | — |

---

## Roll-up (113 remediation line items + optional modernization tracked separately)
| Status | Count |
|--------|:--:|
| ✅ merged | ~67 |
| 🟢 PR open (built) | 0 |
| 🔨 in progress | 0 |
| 📋 scoped | ~20 |
| ⏸ blocked | 3 |
| ⬜ pending | ~32 |

*(Counts are approximate as of the 2026-07-10 reconcile — the 2026-07-09/10 batch merged all previously-open built PRs (#716/#717/#718/#719/#720→#727) plus the security blockers (#728/#729/#730) and the Applications-audit remediations (#731/#732/#733/#734/#735); 4 new Applications-audit residual items added as ⬜.)*

**Open PRs (built, do-not-merge):** none — all previously-open built PRs (#716/#717/#718/#719/#720) merged in the 2026-07-09/10 batch (#720 re-landed as **#727** after a wrong-base merge; #717 boot-crash hotfixed by **#723**). **Old blocked draft:** #498. **De-flake backlog:** `test_fresh_install_pg_chain`
shared-DSN schema_version order-coupling · `test_evidence_pack_supervisor_chain` ad-hoc
batch flake · `test_applications_list_includes_enhanced_operational_summary_and_filters`
(view=list&limit=50 over the shared module DB + same-second created_at ties with no unique
ORDER BY tiebreaker → seeded app can fall off page 1; server-side tiebreaker ships in #720,
test-side q-scoping still wanted; hit #715 CI 2026-07-09) · CI infra: postgres
service-container "PostgreSQL SSL restart timed out" (killed #717's first two runs in ~60s
and masked a real ADR-0008 schema-policy gate failure — fixed by the `migration_043`
marker commit `c3e0610`; #717 green as of 2026-07-09 04:31Z). All of #715-#719 reached
green CI; #720 gets CI when #719 merges (workflow triggers on main-based PRs only).
**Merged + deployed + validated:** **#722** (verification-worker missing-job hygiene — Codex PASS, staging `dd7627f`; closes the P1-006 worker-trace limitation) · **#715** (P12-2 — Codex PASS WITH LIMITATION, staging `02f5538`, DCI-012/013 CLOSED) · **#713** (ownership gate — Codex PASS WITH LIMITATION, staging `074607d`) · **#712** (P11-8 — Codex PASS, BSA-016/017/019 CLOSED) · **#709/#710/#711** (P12-6/P12-3/P12-4-half, merged 2026-07-08; #710's risk-config deploy precondition still needs explicit Codex sign-off) · **#705/#706/#707/#708** (staging `fadf8a6` == main;
backend TD `:796`, worker `:244`; #706/#708 PASS-with-limitation) · Wave A **#700/#701/#702/#703** (TDs
784–789) · #704 (Tier-1-only maker-checker) · RDI (Phase 9) Wave 1 #696/#697/#698 · docs #695 ·
#699 closed unmerged (redundant). Earlier code PRs (#687–#693) merged/validated.

**Where things stand:** Phases 0–3 (except B7 #12) and 5–6 done. **Phase 4 fully
built/merged** (only decision-gated #17/#21/#24/#26/#28 remain). Phase 7: status-canon
done + audit-tamper (#691) merged; **ownership gate merged (#713)**; Applications-page
perf + sort/tabs/chat-removal pair open as #719/#720 (stacked). Phases 8–9 are the
remaining body — overwhelmingly ops/vendor/legal, not code. **Phase 9 (RDI audit):**
**all three current-stage CRITICALs closed & validated — P10-1 (#697, RDI-006) · P10-3
(#696, RDI-004) · P10-2 (#698, RDI-001/007/011)**; P10-DOC-1 policy approved; W2/W3
(P10-4…P10-7, HIGH/MED) and the deferred RDI-002/005 items remain. **Phase 10 (BSA audit,
Audit 2 — run against `e66405a`):** 19 findings folded as P11-1…P11-9; 2 HIGH blockers
(BSA-001 revocation fail-open, BSA-015 dependency CVEs) lead Wave 1; BSA-002 = existing
item 26. **Phase 11 (DCI audit, Audit 3 — run against `956ed5b`):** 30 findings; 11 map to
existing items (incl. 3 blockers elevating item 21 / P9-1 / P9-8), 19 net-new folded as
P12-1…P12-10; code blockers: P12-1 (regulated-record deletion) remains (supervised session); P12-2
(change-implementation recompute) built as #715. Overnight queue 2026-07-08/09 delivered
P12-2/P12-5/P12-8/P12-9 + the Applications-page pair, each with fresh-context adversarial
review folds and 6,7xx-green full suites. **Section order:** phase sections now run …8 → 10 → 11 → 12 →
**9 (Production readiness, last)**. **2026-07-09/10 batch closed 3 of the 4 code pilot blockers** (#728 item 26, #729 P13-1, #730 P11-2) — only **P12-1** (supervised) remains — and remediated the Applications-page readiness-audit Critical/High (#731→#732) plus role-matrix (#733), clean-approval (#734), and UI action-gate (#735); the audit re-run verdict is **READY FOR PILOT WITH CONTROLS**. **The 2026-07-11 full-consolidated audit re-run (`d23cc45`) walks back the prior "≈94–96% pilot-ready" figure:** it surfaced net-new HIGH findings not in that count — DCI-108/109 (risk-parser under-scoring, can flip MEDIUM→LOW) and BSA-003/004 re-run (supervisor audit-record loss to local SQLite + general `/webhook`-substring CSRF bypass), plus 4 confirmed partials (BSA-016/019, RDI-107, DCI-104 incl. 54 unindexed FKs). Pilot-readiness is under retriage pending founder decision on those items; the DCI-108/109 risk-parser fix is offered as the next code PR. Production-readiness ≈ 35–40%
(Audit 3 verdict: REMEDIATE BEFORE PROCEEDING; production still gated by ops/vendor/legal + P12-1 and the Applications-audit prod residuals — analyst-UI/runtime action coverage, audit-writer id population, CSP enforcement). See the [Re-audit 2026-07-11](#re-audit-2026-07-11-d23cc45--reconciliation) section for the full finding list.
