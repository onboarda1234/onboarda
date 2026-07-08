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

**Last reconciled:** 2026-07-08 (base `main` = `5c7a3af`, HEAD after #708 merged).
**Wave A fully closed:** all four small-wins merged + deployed to AWS staging + validated
(PASS) — **#700 (SW-1)**, **#701 (SW-2, `dd28a79`, TD 788)**, **#702 (SW-3, staging-SHA
gate)**, **#703 (SW-4, `daab2bb`, TD 789)**; staging == `origin/main` == `daab2bb`.
**Overnight batch:** **#705 (P11-1)**, **#706 (P11-3)**, **#708 (P10-6)** MERGED
(await Codex deploy/validate report); **#707 (P11-9)** still open. **Wave B built
(do-not-merge):** **#709 (P12-6 / DCI-007)**, **#710 (P12-3 / DCI-008+010+011)** — each
implemented → SQLite + live-PostgreSQL → fresh-context adversarial review → folded → pushed.
**Audit 4 (FEO) folded as Phase 13.** Consolidated 4-audit verdict: BLOCKED for
uncontrolled production, conditional for controlled pilot; only remaining CRITICALs =
DCI-001 (P12-1) and DCI-027 (P9-8).
**Phase 10 Wave-1 complete:** the three current-stage blocking CRITICALs are merged,
deployed (`regmind-staging:782` / worker `:230`, image `e66405a`), validated (PASS) —
**P10-1 #697 (RDI-006), P10-3 #696 (RDI-004), P10-2 #698 (RDI-001/007/011)**; merge order
#695 → #697 → #696 → #698. **#704 merged** (Codex): maker-checker narrowed to Tier 1 only
— closes the approved four-eyes scope change #697 had left outstanding. Prior batches all
merged/validated: #692/#690/#693/#691 (TDs 775/776/777), #687/#688/#689 (TDs 771/772/773),
docs #695. Incorporates REGMIND-SYSTEM-READINESS-AUDIT-1 (P9-12/13/14 +
CLIENT-PORTAL-RUNTIME-SMOKE-1 + PERIODIC-BASELINE-METHOD-HYGIENE-1), an Optional/
Post-Production Modernization section, Phase 10 (RDI audit), **Phase 11 (BSA / Audit 2 —
19 findings)**, **Phase 12 (DCI / Audit 3 — 30 findings, 6 blockers, schema UNSAFE)**, and
**Phase 13 (FEO / Audit 4 — 15 findings)**. Section order places **Phase 9 (Production
readiness) last**, after Phases 10/11/12/13. **PR #699** (Codex draft, P10-1
closure-evidence docs) was **closed unmerged** — its closure record is carried here.

> Maintenance: this is the single source of truth for remediation status. On any
> request for PR/phase status, refresh the Status/GitHub columns from GitHub and
> update this file. Item IDs (1–40, 33–38, P9-1…P9-14, P10-1…P10-7, P11-1…P11-9, P12-1…P12-10, P13-1…P13-7, PR-* slugs) are canonical.

**Legend:** ✅ merged · 🟢 PR open (built) · 🔨 in progress · 📋 scoped · ⏸ blocked · ⬜ pending

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
| 26 | Shared rate limiter *(= Audit-2 **BSA-002**: persist forgot-pw/doc-upload/AI keys across ECS tasks, fail-closed)* | — | ⬜ |
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
| PR-APP-ACTION-OWNERSHIP-SCOPE-1 | P1/P2 | Act-only-as-owner + supervisor override *(= Audit-4 **FEO-013**: pilot runbook's named-owner control is manual, not code-enforced)* | — | ⬜ |
| ops-enforce-staging-sha-alignment-gate | P0 | Staging-SHA gate + delete test logins | [#702](https://github.com/onboarda1234/onboarda/pull/702) | ✅ code half merged (SW-3; gate exercises on next deploy) · delete-test-logins half ⬜ ops-side |
| perf-applications-default-list-projection | P2 | Slim default list payload | — | ⬜ |
| audit-log-tamper-evidence-1 | P2 | *(= Phase 4 #27)* | #691 | ✅ |
| ux-applications-list-sort-status-tabs | P3 | Sortable headers + status tabs | — | ⬜ |
| chore-applications-deadcode-cleanup | P3 | Delete dead approval branches | [#701](https://github.com/onboarda1234/onboarda/pull/701) | ✅ merged (SW-2; merge `dd28a79`, TD 788, validated PASS) |
| CLIENT-PORTAL-RUNTIME-SMOKE-1 | P1 | Live client-credential smoke: status/upload/logout/**cross-tenant denial** *(audit REGMIND-P1-006)* | — | ⬜ |
| PERIODIC-BASELINE-METHOD-HYGIENE-1 | P2 | Clean 405 on POST-only periodic-review baseline route *(audit REGMIND-P2-001)* | [#700](https://github.com/onboarda1234/onboarda/pull/700) | ✅ merged (SW-1) |
| PR-RISK-SECTOR-CALIBRATION-1 | P2 | Recalibrate sector risk + "unknown≠high" defaults *(audit done; was "Backlog — after Phase 7"; also Audit-3 **DCI-009**: missing/unknown country defaults MEDIUM — treat as manual-review/HIGH)* | — | 📋 scoped |

## Phase 8 — Pilot Controls Pack
| # | Title | GitHub | Status |
|---|-------|--------|:--:|
| 33 | Pilot-scope guards (server-side) | — | ⬜ |
| 34 | Dashboard API performance (15.1s → sub-2s) | — | ⬜ |
| 35 | Screening full-evidence hydration performance | — | ⬜ |
| 36 | Persisted negative-path fixtures | — | ⬜ |
| 37 | Lower-privilege fixture authz regression tests | #692 | ✅ |
| 38 | Pilot operations runbook | #689 | ✅ |
| — | ComplyAdvantage production workspace validation | #498 | ⏸ blocked (dashboard-mode evidence) |

## Phase 10 — Regulatory Decision Integrity (RDI audit)
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
- **RDI-002** — by-design LOW/MEDIUM fast-path, HIGH policy-exception (not a code defect). **P10-DOC-1:** policy **✅ APPROVED & signed off** (Aisha Sudally, 2026-07-07) at [`docs/compliance/LOW_MEDIUM_FASTPATH_APPROVAL_POLICY.md`](compliance/LOW_MEDIUM_FASTPATH_APPROVAL_POLICY.md) (eligibility = all LOW/MEDIUM; disqualifiers = sanctioned/FATF, PEP, adverse hit, stale/incomplete screening, failed IDV; approver = Onboarding Officer alone; 20% QA sampling). **Residual code assertions** (decision-record eligibility-basis stamp + direct-route test that a disqualifying signal can never fast-track) folded into the Phase 10 approval-path PRs (P10-3 / P10-5) — ⬜.
- **RDI-005** — SAR permanence (`ON DELETE CASCADE`, cleanup delete, mutable SAR content), HIGH **Enterprise pre-enable blocker**. Must be fixed **before** enabling Enterprise SAR/STR; safe to defer **only while SAR/STR feature flags stay disabled** (`ENABLE_SAR_WORKFLOW`, `ENABLE_SAR_STR` = false). Same guard covers the SAR slices of RDI-009/RDI-013. *(Re-confirmed by Audit-3 **DCI-002** — same cascade + pre-file overwrite findings; note the general SAR cleanup-delete surface is also covered by P12-1.)*

**Wave order:** W1 P10-1 → P10-2 → P10-3 (all CRITICAL; P10-2 unblocks P10-5) · W2 P10-4, P10-5, P10-6 (HIGH) · W3 P10-7 (MED/infra). P10-1 and P10-6 are small quick wins slot-able anytime.

**Closure evidence (2026-07-07):**
- **P10-1 (#697)** — **merged** (base `b577a5f`, merge `b6192fb`; ancestor of deployed HEAD `e66405a`, so live on `regmind-staging:782`). `create_change_request()` now ignores client-supplied `items[].materiality` and server-computes tier from `change_type` via `classify_materiality`; fresh-context review fold prevents server-known alert types (e.g. `control_change`) downgrading to `other`/Tier 2. Full SQLite suite 6549 passed; CM regression 217 passed; static guard asserts no `item.get("materiality")` read. **RDI-006 CLOSED/REMEDIATED** (Codex-verified; control C-11 VERIFIED for client-supplied override). **Two residuals:** (a) `change_type` itself is still client-supplied — semantic mislabeling is a future hardening item (unknown types default Tier 2); (b) the previously-approved four-eyes scope change (tier1,tier2→tier1) was not part of #697 — **since CLOSED by #704** (Codex, merge `956ed5b`): maker-checker narrowed to Tier 1 only, Tier 2 still covered by the screening hard-block.
- **P10-3 (#696)** — **merged**, deployed (`regmind-staging:781` / `regmind-verification-worker:229`, image `fbedc7c`), validated. Targeted `test_risk_staleness_gate.py` 15 passed; runtime synthetic probe confirmed current-version app proceeds, older-version app + `stale:recompute_failed` quarantine both 409-block, non-approval decisions (reject/escalate/request-docs = 201) not newly blocked. **RDI-004 CLOSED/PASS.** Residual (per design): legacy `NULL`-provenance apps blocked only after first config update/sweep.
- **P10-2 (#698)** — rebased onto #696-merged `main`, retargeted, CI green, **merged**, deployed (`regmind-staging:782` / `regmind-verification-worker:230`, image `e66405a`), validated. Targeted decision/memo/approval suite 263 passed / 2 skipped; full SQLite suite 6568 passed. Runtime probe: decision 201 persisted `decision_records_count=1` + audit + accepted governance; memo approve 200 with signoff audit; memo validate 200 persisted status+timestamp. **RDI-001 / RDI-007 / RDI-011 CLOSED/PASS.** Residual: live-DB fault injection not run (forced-failure covered by merged tests); memo-supervisor `decision_records` overlay stays scoped to P10-5/RDI-009.
- Final staging aligned to #698 merge SHA `e66405a`; `/api/version` git_sha+image_tag match; liveness/health/readiness 200 (`ready=true`); both ALB targets healthy; 30-min CloudWatch window ERROR/Exception/Traceback/HTTP-5 = 0.

**Audit-2 unpause status:** ✅ **all three current-stage blocking CRITICALs closed & validated** — RDI-006 (#697), RDI-004 (#696), RDI-001 (#698). Merge order on `main`: #695 → #697 → #696 → #698 (HEAD `e66405a`, deployed `regmind-staging:782`). The audit artifact's "remaining blockers RDI-001/RDI-004" note reflects the point-in-time when #697 was verified — both have since merged. **Audit 2 has since run** (see Phase 11). Remaining Phase 10 work is W2/W3 (HIGH/MED: P10-4 decision-gated, P10-5 dep-on-P10-2, P10-6, P10-7) plus the deferred RDI-002/005 items; the four-eyes scope decision is closed (#704, Tier-1-only maker-checker).

## Phase 11 — Backend Security & Authorization (BSA audit)
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
| P11-2 | Dependency CVE remediation + OSV/pip-audit CI gate | BSA-015 | HIGH | Upgrade Tornado ≥6.5.7, PyJWT ≥2.13.0, cryptography ≥48.0.1, WeasyPrint (upgrade/mitigate); add pip-audit / OSV scan that fails CI on HIGH/CRITICAL advisories | — | 📋 scoped (W1 blocker) |
| P11-3 | Fail-closed inputs + AI budget | BSA-006, 007, 013 | MED + LOW | `get_json()` returns structured **400** on malformed body (both BaseHandler and supervisor API); bounded-int pagination everywhere (server + supervisor routes); Claude budget **fails closed** in staging/prod/demo incl. the raw `generate()` path | [#706](https://github.com/onboarda1234/onboarda/pull/706) | ✅ merged |
| P11-4 | Offload blocking I/O off the IOLoop | BSA-004, 005 | MED | Move WeasyPrint PDF render and in-request Claude document-verify to a worker/executor; replace `time.sleep` backoff; enforce per-user/app AI quotas *(coordinate with item 12 / B7)* | — | 📋 scoped |
| P11-5 | AI prompt sanitisation + output schema + circuit breaker | BSA-011, 012 | MED | Apply the deep/3-pass sanitiser to **all** `generate()` inputs; replace raw-token enum parsing with Pydantic schemas (AI free-text advisory only); add source-controlled, DB-persisted circuit breaker around Anthropic/Sumsub/S3 | — | 📋 scoped |
| P11-6 | AuthZ & audit hardening | BSA-003, 009 | MED | Require recent re-auth / second factor on admin password-reset (+ mandatory revocation); route all change-management 403 denials through `log_authz_denial()` | — | 📋 scoped |
| P11-7 | Document-download attachment + webhook signature hygiene | BSA-008, 010 (+ DCI-017) | MED + LOW | Force `Content-Disposition: attachment` on all uploaded-doc downloads (separate sanitised preview endpoint if previews needed); document/opaque webhook invalid-sig response; remove ComplyAdvantage legacy signature fallback; *(DCI-017)* no silent local-disk fallback when S3 fails in staging/prod + MIME from server allowlist not stored value | — | 📋 scoped |
| P11-8 | Supply-chain pinning | BSA-016, 017, 019 (= DCI-022/024) | MED + LOW | SHA-pin GitHub Actions (all 4 workflows, exact-release comments, annotated tags peeled); split test deps into `requirements-dev.txt` (flake8 now pinned too); pin Docker base image by manifest-list digest + `.dockerignore` excludes uploads/data/logs; 8 guard tests prevent regression. Residual: CI service container + dev compose still on mutable postgres tags (out of scope) | [#712](https://github.com/onboarda1234/onboarda/pull/712) | 🟢 PR open |
| P11-9 | CI coverage-gate fail-closed | BSA-018 (= DCI-026) | LOW | Unparseable coverage now FAILS the build (empty-COV branch exits 1) | [#707](https://github.com/onboarda1234/onboarda/pull/707) | 🟢 PR open (CI pending) |

**Cross-ref:** **BSA-002** (share/persist rate limits across ECS tasks — forgot-pw, doc-upload, AI keys, fail-closed) = existing **Phase 4 item 26 "Shared rate limiter"** (⬜). Fold BSA-002's specifics there rather than duplicate here.

**Wave order:** W1 P11-1, P11-2 (both blockers — clear before pilot/prod) · W2 P11-3…P11-7 (MED) · W3 P11-8, P11-9 (LOW/supply-chain/CI).

## Phase 12 — Data Integrity, Compliance Logic & Infrastructure (DCI audit)
> Source: **RegMind Production Audit 3 — Data Integrity, Compliance Logic and Infrastructure**,
> run against `956ed5b` (#704 merge). 30 findings (DCI-001…030). Schema safety rated
> **UNSAFE** (regulated-record deletion paths + admitted schema drift). Verdict:
> **REMEDIATE BEFORE PROCEEDING** — 6 blockers (DCI-001, 003, 012, 018, 019, 027) plus 1
> Enterprise pre-enable blocker (DCI-002). Positives verified: risk-config save validates
> 5 dimensions/weight=100; sanctioned/FATF floor rules present in rule engine (12
> elevation/floor rules enumerated); supervisor contradiction logic VERIFIED; Agent 9
> properly deferred/guarded; presigned-URL expiry bounded.
> **11 of 30 findings are already tracked elsewhere** — cross-referenced, NOT duplicated:
> DCI-002 = RDI-005 (deferred Enterprise SAR blocker, Phase 10) · DCI-009 =
> PR-RISK-SECTOR-CALIBRATION-1 (Phase 7) · DCI-017 → folded into P11-7 · DCI-018 =
> Phase 4 item 21 (now an **Audit-3 BLOCKER**) · DCI-019 = P9-1 (now an **Audit-3
> BLOCKER**) · DCI-022/024 = P11-8 · DCI-023 = P9-4 (IaC) · DCI-026 = P11-9 ·
> DCI-027 = P9-8 (**CRITICAL blocker**, environment-required) · DCI-030 = P9-10.
> The 19 net-new findings group into 10 PRs. Item IDs `P12-1…P12-10` canonical. Same
> discipline per PR: implement → full SQLite + live-PG tests → fresh-context adversarial
> review → fold → push.

| # | PR | Findings | Severity | What it fixes (plain) | GitHub | Status |
|---|----|----------|:--:|-----------------------|:--:|:--:|
| P12-1 | Regulated-record deletion protection | DCI-001, 003 | CRITICAL + HIGH | App-delete cleanup + startup cleanup migration must NEVER delete regulated evidence (`sar_reports`, `compliance_memos`, `edd_cases`, `agent_executions`, `supervisor_audit_log`, `decision_records`) — soft-delete/tombstone with deletion marker instead; move fixture cleanup out of generic startup code | — | 📋 scoped (W1 blocker) |
| P12-2 | Change-implementation fail-closed recompute + audit-in-transaction | DCI-012, 013 | HIGH + MED | Recompute risk (or write a `requires_recomputation` quarantine marker, P10-3-style) in the SAME transaction as implement — a swallowed recompute failure must not leave a live material change on a stale score; write CM approve/implement audit rows before commit, not after | — | 📋 scoped (W1 blocker) |
| P12-3 | Compliance-logic corrections | DCI-008, 010, 011 | HIGH + HIGH + MED | Risk-config load failure fails CLOSED in staging/prod (no silent hardcoded-default model); memo `jur_rating` actually mutates to VERY_HIGH when `SANCTIONED_COUNTRY_FLOOR` is claimed; fix `MULTI_GAP_ESCALATION` branch order (≥4 checked before ≥3). Review folds: PG/JSONB parse hole closed (`safe_json_loads` coerced malformed scalars to `{}` before validation); recompute/boot-repair/correction/EDD-tier laundering paths all re-raise; boot-time CRITICAL probe. **Deploy precondition: validate live staging risk_config row first (see PR)** | [#710](https://github.com/onboarda1234/onboarda/pull/710) | 🟢 PR open |
| P12-4 | Migration hard-stops + schema-drift detection | DCI-005, 004 | HIGH | Reject `MIGRATION_FAILURE_MODE=continue` when ENVIRONMENT is staging/production — **DCI-005 half shipped in [#711](https://github.com/onboarda1234/onboarda/pull/711)** (override ignored + ERROR on every boot; dev/test/demo keep it; clean adversarial review). Still scoped: DCI-004 startup drift check comparing declared constraints/FKs/columns vs live schema, fail-closed in staging/prod | [#711](https://github.com/onboarda1234/onboarda/pull/711) | 🟢 PR open (DCI-005 half; DCI-004 still 📋) |
| P12-5 | Status-column CHECK constraints | DCI-006 | MED | CHECK constraints/enums for `clients.status`, `agent_executions.status/source`, `supervisor_pipeline_results.status`, `supervisor_audit_log.event_type/severity`, `compliance_memos.supervisor_status/rule_engine_status` (backfill invalid data first) | — | 📋 scoped |
| P12-6 | PG pool connection validation | DCI-007 | MED | Pre-ping (`SELECT 1`) on pool checkout; discard/retry stale connections after RDS failover | [#709](https://github.com/onboarda1234/onboarda/pull/709) | 🟢 PR open |
| P12-7 | Verification-matrix fidelity | DCI-014, 015 | MED + LOW | HYBRID checks go to Claude ONLY on deterministic INCONCLUSIVE (never override a deterministic FAIL), per the matrix policy; resolve the 5 TODO enhanced-requirement document mappings with compliance sign-off | — | 📋 scoped |
| P12-8 | Retention purge enforceability + purge-log evidence | DCI-020, 021 | MED | Map (or explicitly mark manual-with-procedure) all retention categories beyond audit_logs/monitoring_alerts; add subject_id/application_id/tables_affected/per-table counts/batch id to `data_purge_log`, written atomically with the purge | — | 📋 scoped |
| P12-9 | Observability hardening | DCI-028, 029 | MED | Force JSON logs + request-correlation IDs in staging/prod; readiness probes for S3 reachability and disk capacity | — | 📋 scoped |
| P12-10 | Infra guards | DCI-016, 025 | MED + LOW | Enforce upload body-size before full buffering (server/proxy level; handler check stays as second line); deploy workflow FAILS when ECS `services-stable` times out *(partially mitigated by #702's SHA-alignment gate — stability half still open)* | — | 📋 scoped |

**Wave order:** W1 P12-1, P12-2 (code blockers) — the other Audit-3 blockers live elsewhere: item 21 (DCI-018), P9-1 (DCI-019), P9-8 (DCI-027) · W2 P12-3…P12-9 · W3 P12-10.

## Phase 13 — Frontend & Operational Readiness (FEO audit)
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
| P13-1 | Backoffice stored-XSS elimination | FEO-001, 002 | HIGH | Escape (or DOM-construct with `textContent`) every API-interpolated field in the memo renderer (`renderMemoSections`) and supervisor/audit renderers (contradictions, rules, audit entries, chain errors); fixed enum→class maps for status/risk badges; XSS regression fixtures (payload in company name, memo section, red flag, supervisor recommendation, audit detail, error string) | — | 📋 scoped (W1) |
| P13-2 | Single API wrapper + consistent CSRF | FEO-003 | MED | Route all 23 backoffice + portal raw `fetch()` sites through `boApiCall`/`apiCall`; state-changing calls fail closed client-side without a CSRF token; consistent `credentials: 'include'` (incl. logout + uploads + supervisor-run) | — | 📋 scoped |
| P13-3 | Defensive API response parsing | FEO-004 | MED | Check status + `Content-Type` BEFORE `res.json()` in both wrappers; handle 401 before JSON-dependent logic; text/error-envelope fallback for ALB/proxy HTML errors | — | 📋 scoped |
| P13-4 | App-detail render race guard | FEO-005 | MED | Monotonic request nonce / expected-ref check in `openAppDetail`→`renderAuthoritativeAppDetail`; ignore stale responses so Application A can never render over Application B's context | — | 📋 scoped |
| P13-5 | Role-UI fail-closed until matrix loads | FEO-006 | LOW | Privileged controls hidden/disabled with a loading/retry state until the RBAC matrix is fetched (today UI deliberately fails open; backend remains the gate) | — | 📋 scoped |
| P13-6 | Portal intake PII out of sessionStorage | FEO-007 | MED | Persist company-intake state via the authenticated server-side save/resume path; keep only an opaque resume handle client-side; clear legacy `arie_company_intake_state` on load | — | 📋 scoped |
| P13-7 | Compliance-officer SOP pack | FEO-014 | MED (ops/docs) | Officer onboarding/training SOP, pre-approval review checklist, `INCONSISTENT` supervisor-verdict handling, senior escalation, override + evidence-export procedures | — | 📋 scoped (ops/docs) |

**Wave order:** W1 P13-1 (the two HIGH stored-XSS findings — officer-session code execution) · W2 P13-2…P13-6 · P13-7 alongside (docs, non-code).

## Phase 9 — Production readiness
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
| P9-13 | Full authz / tenant-isolation **route matrix** audit (role-by-route) *(audit §7)* | security | — | ⬜ |
| P9-14 | Registry KYB (OpenCorporates) **simulated → real/production** *(audit prod blocker)* | code/vendor | — | ⬜ |

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

## Roll-up (104 remediation line items + optional modernization tracked separately)
| Status | Count |
|--------|:--:|
| ✅ merged | 46 |
| 🟢 PR open (built) | 5 |
| 🔨 in progress | 0 |
| 📋 scoped | 24 |
| ⏸ blocked | 1 |
| ⬜ pending | 28 |

**Open PRs (built, do-not-merge, awaiting review + Codex handover):** **#707 (P11-9)** ·
**#709 (P12-6)** · **#710 (P12-3)** · **#711 (P12-4, DCI-005 half)** · **#712 (P11-8)** ·
**Old blocked draft:** #498.
**Recently merged:** **#705 (P11-1)** · **#706 (P11-3)** · **#708 (P10-6)** — awaiting
Codex deploy/validate report. **Merged + validated:** Wave A **#700/#701/#702/#703** (TDs
784–789) · #704 (Tier-1-only maker-checker) · Phase 10 Wave 1 #696/#697/#698 · docs #695 ·
#699 closed unmerged (redundant). Earlier code PRs (#687–#693) merged/validated.

**Where things stand:** Phases 0–3 (except B7 #12) and 5–6 done. **Phase 4 fully
built/merged** (only decision-gated #17/#21/#24/#26/#28 remain). Phase 7: status-canon
done + audit-tamper (#691) merged; ownership gate not started (⬜). Phases 8–9 are the
remaining body — overwhelmingly ops/vendor/legal, not code. **Phase 10 (RDI audit):**
**all three current-stage CRITICALs closed & validated — P10-1 (#697, RDI-006) · P10-3
(#696, RDI-004) · P10-2 (#698, RDI-001/007/011)**; P10-DOC-1 policy approved; W2/W3
(P10-4…P10-7, HIGH/MED) and the deferred RDI-002/005 items remain. **Phase 11 (BSA audit,
Audit 2 — run against `e66405a`):** 19 findings folded as P11-1…P11-9; 2 HIGH blockers
(BSA-001 revocation fail-open, BSA-015 dependency CVEs) lead Wave 1; BSA-002 = existing
item 26. **Phase 12 (DCI audit, Audit 3 — run against `956ed5b`):** 30 findings; 11 map to
existing items (incl. 3 blockers elevating item 21 / P9-1 / P9-8), 19 net-new folded as
P12-1…P12-10; code blockers P12-1 (regulated-record deletion) + P12-2 (change-implementation
recompute) lead Wave 1. **Section order:** phase sections now run …8 → 10 → 11 → 12 →
**9 (Production readiness, last)**. Pilot-readiness ≈ 88–92%; production-readiness ≈ 30–35%
(Audit 3 verdict: REMEDIATE BEFORE PROCEEDING).
