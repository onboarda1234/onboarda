<!--
AUTHORITATIVE audit-remediation master list — a STATUS REGISTER, not a journal.
Maintenance rules (single copy; CLAUDE.md points here):
1. One fact, one home. The phase tables below are the sole source of status.
   Never add narrative status paragraphs; when status changes, EDIT the row —
   superseded text is deleted, not layered. History lives in git log, PR
   descriptions, and compliance/REMEDIATION_CLOSURE_EVIDENCE.md.
2. Status cells hold a legend glyph + date + at most one short clause.
   Closure detail (SHAs, task defs, validation output, limitations) belongs in
   compliance/REMEDIATION_CLOSURE_EVIDENCE.md, linked from the E column.
3. On any request for PR/phase status: reconcile GitHub/Status columns against
   live GitHub PR state, update the affected rows, the gates block, and the
   Reconciled line, recompute the roll-up by counting rows, and commit.
4. Phase numbering and item IDs are canonical — NEVER renumbered. Sections were
   renumbered 2026-07-08 (founder instruction), so ID prefixes deliberately do
   NOT match section numbers: P10-x → Phase 9 · P11-x → Phase 10 ·
   P12-x → Phase 11 · P13-x → Phase 12 · P9-x → Phase 14.
5. 2026-07-11 re-run findings that reuse original BSA numbers carry the R2-
   prefix (R2-BSA-001 ≠ BSA-001). The re-run 1xx series (RDI-1xx, DCI-1xx,
   FEO-1xx) is collision-free and keeps its IDs.
The pre-2026-07-15 narrative format of this file (batch summaries, "Where
things stand") was retired in the 2026-07-15 restructure; see git history.
-->

# Onboarda / RegMind — Audit-Remediation Master List

**Reconciled:** 2026-07-22 against live GitHub · `main` = `61a1076` (merge of #837) · unions the #780 register stream with the first staged batch (#808–#815) and the second batch (#833–#837, all merged + staging-deployed); no duplicate register rows
**Pilot:** all 4 code blockers ✅ closed · remaining pilot work = **RSMP Tier 0C** (post-reset 0C-A rerun → 0C-B) + the open 🟠 gates below · Applications module: unconditional **PILOT-READY** (confirmation audit, 2026-07-16)
**Production:** blocked — Audit-3 verdict REMEDIATE BEFORE PROCEEDING; Phase 14 largely open. Nothing in this file is a production-readiness claim.
**Open PRs:** [#788](https://github.com/onboarda1234/onboarda/pull/788) (draft docs — staging-reset closure; content incorporated here) · [#779](https://github.com/onboarda1234/onboarda/pull/779) (draft — 0C-A evidence pack, pre-reset verdict) · [#737](https://github.com/onboarda1234/onboarda/pull/737) (draft — P12-1 Phase A discovery report) · #780 register reconcile is merged and unioned here

**Legend:** ✅ done/merged · ◐ split item (one half done, one open) · 🟢 PR open · 🔨 in progress · 📋 scoped · ⏸ blocked · ⬜ pending · 🔴 pilot code blocker · 🟠 pilot operational gate
**E column** = closure evidence in [`compliance/REMEDIATION_CLOSURE_EVIDENCE.md`](compliance/REMEDIATION_CLOSURE_EVIDENCE.md).

## Controlled-pilot gates (summary view — authoritative status lives in the phase rows)

🔴 Code blockers — **4 of 4 CLOSED:**

| ID | Blocker | Closed by |
|----|---------|-----------|
| P12-1 | Regulated-record deletion protection (DCI-001/003) | ✅ #738 (pilot scope) |
| P11-2 | Dependency CVEs + pip-audit CI gate (BSA-015) | ✅ #730 |
| P13-1 | Back-office stored-XSS (FEO-001/002) | ✅ #729 |
| item 26 | Shared fail-closed rate limiter (BSA-002) | ✅ #728 |

🟠 Operational gates and remaining pilot work:

| Gate | Tracked at | State |
|------|-----------|-------|
| RSMP Tier 0C — post-reset 0C-A rerun (read-only) → 0C-B controlled activation + recomputation | Re-audit → RSMP | 🔴 0C-A 2026-07-16 verdict NOT READY was against the pre-reset population (purged 2026-07-17); fresh 0C-A on the canonical baseline next; 0C-B unauthorized |
| item 33 — pilot-scope guards (server-side) | Phase 13 | ✅ #880 |
| P13-7 — SOP pack Section 16 execution (docs merged) | Phase 12 | 🟠 open |
| CA production workspace validation — complete or formally exclude from pilot scope | Phase 14 (P9-3) | ⏸ |
| Staging-SHA alignment gate — ops half | Phase 7 | 🟠 open |
| item 36 — persisted negative-path fixtures | Phase 13 | ✅ closed 2026-07-12 |

Two gates are decisions, not rows: **Applications-page readiness audit** — 2026-07-16 confirmation audit verdict, upgraded same day after APP-CONF-001/002 closures: unconditional **PILOT-READY** (P1 closed via #782, revalidated in 3 engines; synthetic-record sweep clean) · **PII-encryption deferral** recorded as a signed risk-acceptance (item 21 is a production item; deferred for pilot with compensating controls). P13-1 may alternatively be formally accepted with compensating controls.

---

## Phase 0 — Audit-integrity emergencies

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| 1 | Stop audit-trail purge (B1) | — | #661 | ✅ merged | — |
| 2 | Stop boot-time hash-chain rewrite (B2) | — | #661 | ✅ merged | — |
| 3 | Chain verify + anti-fork (H3, H12) | — | #661 | ✅ merged | — |
| 4 | Evidence-pack completeness (H4) | — | #661 | ✅ merged | — |

## Phase 1 — Client-facing misrepresentation & provenance

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| 5 | Remove client screening + lock endpoints (B4, M1) | — | #661 | ✅ merged | — |
| 6 | Effective-provider evidence provenance (B5) | — | #676 | ✅ merged | — |
| 7 | Remove fabricated portal preview rows (H1) | — | #661 | ✅ merged | — |

## Phase 2 — Operate as a compliance/AML platform

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| 8 | B6-B5 screening readiness + provenance | — | #676 | ✅ merged | — |
| 9a | H2A DSAR status honesty | — | #665 | ✅ merged | — |
| 9b | H2B GDPR erasure engine (wired-but-OFF) | — | #677 | ✅ merged | — |
| 10 | H1 memo-claim truthfulness | — | #670 | ✅ merged | — |

## Phase 3 — Deploy & runtime safety

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| 11 | Migrations + boot lock (B3/PC-3) | — | #675 | ✅ merged | — |
| 12 | Non-blocking I/O + graceful shutdown (B7) — coordinate with P11-4 | — | — | ⬜ dedicated session | — |
| 13 | Normalize ENVIRONMENT + prod keys (H8) | — | #673 | ✅ merged | — |
| 14 | Singleton-guard schedulers (H9) | — | #674 | ✅ merged | — |
| 15 | Container healthcheck (H10) | — | #672 | ✅ merged | — |
| 16 | Rollback runbook (H11) | — | #678 | ✅ merged | — |

## Phase 4 — Hardening (fast-follow)

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| 17 | Virus-scan uploads (H5) | P0 | — | 📋 scoped — decision needed | — |
| 18 | Redaction/response allow-list | — | #690 | ✅ merged | — |
| 19 | Resilience/fail-safe → delete dead `resilience/` | — | #693 | ✅ merged | — |
| 20 | Persist memo `blocked` verdict | P0 | #679 | ✅ merged | — |
| 21 | DOB/PII encryption at rest (= **DCI-018**, Audit-3 production blocker: full PII taxonomy still plaintext outside PIIEncryptor field lists) | blocker | — | ⬜ production item; pilot deferral needs signed risk-acceptance | — |
| 22 | CSP headers (report-only) | — | #688 | ✅ merged | — |
| 23 | Session revocation | — | #687 | ✅ merged | — |
| 24 | CA webhook retry idempotency (SW-4) | — | [#703](https://github.com/onboarda1234/onboarda/pull/703) | ✅ merged + validated | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#wave-a-prs-700-703) |
| 24b | CA webhook reconciler wiring (residual of item 24) | — | — | ⬜ pending | — |
| 25 | Unique seeded-account secrets (M14) | P0 | #681 | ✅ merged | — |
| 26 | Shared fail-closed rate limiter (= **BSA-002**) — blocker + R2-BSA-016 route gaps closed | HIGH | [#728](https://github.com/onboarda1234/onboarda/pull/728), [#808](https://github.com/onboarda1234/onboarda/pull/808) | ✅ merged + staging-validated 2026-07-21 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#eight-remediation-prs-808-815) |
| 27 | audit_log tamper-evidence (core; wiring deferred) | — | #691 | ✅ merged | — |
| 28 | Misc M7–M12 | — | — | ⬜ deprioritized (skip decision) | — |
| 40 | Close last silent fail-open (dead code) | — | #680 | ✅ merged | — |

## Phase 5 — Screening Review / Agent 3 (parallel audit)

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| — | Reconcile Agent 3 screening counts | — | #658 | ✅ merged | — |
| — | Registry badge normalization | — | #659 | ✅ merged | — |
| PR-A | No soft-green "clear" for incomplete screens | — | #682 | ✅ merged | — |
| PR-B | Slim Agent 3 panel + disposition | — | #683 | ✅ merged | — |
| PR-C | Watchlist as first-class category/count | — | #684 | ✅ merged | — |

### Screening-queue audit stream (2026-07) — added at the 2026-07-15 reconcile; previously untracked

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| — | Truthful entity mode badge, horizontal scroll, page indicator | — | #756 | ✅ merged | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#screening-queue-stream-prs-756-763) |
| — | Correct PEP/status filters; remove provider source filter | — | #757 | ✅ merged | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#screening-queue-stream-prs-756-763) |
| — | Slim table 8 → 5 columns; honest registry wording | — | #758 | ✅ merged | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#screening-queue-stream-prs-756-763) |
| — | Audit PR-A: provenance truth, distinct labels, error state | — | #759 | ✅ merged | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#screening-queue-stream-prs-756-763) |
| — | Audit Phase 2: stable subject-key joins for screening entries | — | #760 | ✅ merged | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#screening-queue-stream-prs-756-763) |
| — | Audit Phase 3: hydrate evidence for the returned page only | — | #761 | ✅ merged | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#screening-queue-stream-prs-756-763) |
| — | Audit Phase 4: fixture governance, QA disposition fixtures, 7-column layout | — | #763 | ✅ merged | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#screening-queue-stream-prs-756-763) |
| — | Phase 4b/4c/4d: sanctioned seeder deletes, PG booleans, FK-complete seeding, de-flake | — | #766 #769 #770 | ✅ merged · Phase 4 validated PASS 2026-07-15 | — |
| — | Phase 5 disposition/RBAC/leakage validation (four-eyes E2E, analyst 403, 539-row sweep) | — | — | ✅ PASS — Section M closed 2026-07-16 (below) | — |
| — | Section M latency: evidence cap + candidate hoist + `application_id` index (21.1s → 5.2s) | — | #773 | ✅ merged + staging-validated · correctness PASS | — |
| — | Section M latency: stage-timing attribution (`metrics.timings_ms`) → transfer-dominated | — | #778 | ✅ merged · attribution complete 2026-07-16 | — |
| — | Section M latency: gzip responses (4.7MB raw → ~15x compressed transfer) | — | #781 | ✅ merged + close-out PASS 2026-07-16 — evidence p50/p95 1.096s/1.202s (was 21.1s/33.6s) | — |
| — | Phase 6 closeout: module card, ops runbook, CLAUDE.md change-control entry, rts-1.0 methodology | — | #792 | ✅ merged + deployed 2026-07-18 — queue verdict VALIDATED/CHANGE-CONTROLLED; end-to-end workflow verdict stays gated on SRP-3 | — |
| — | Test hygiene: uuid-hex fixture-collision flake class retired, prefix-aware fixture-safe suffixes, async timeout raise | — | #800 #802 | ✅ merged 2026-07-19 | — |
| — | Ops tickets (Phase 6): CloudWatch p95 alarm on /api/screening/queue · PG-backed test lane for seed/ops tooling | — | [#875](https://github.com/onboarda1234/onboarda/pull/875), [#876](https://github.com/onboarda1234/onboarda/pull/876) | ◐ PG tooling lane ✅ merged + CI-enforced 2026-07-25 (#876) · p95 alarm: latency metric emission live + provisioning script + runbook merged (#875), AWS `--apply` = operator step ([runbook](OPS_HARDENING_RUNBOOK.md) §2) | — |

### Screening Review page & Agent 3 — simplification work plan (SRP, added 2026-07-16)

Source: Manus blueprint (ARF-2026-920016, 298 untriageable hits) — reviewed against code
2026-07-16. Manus's root cause ("normalizer discards triage data") verified INACCURATE:
the current normalizer retains matched name, stable profile id, risk types and media
evidence; observed blindness = pre-enrichment stored snapshots + match-score display
deliberately suppressed pending the CA scale answer. Execution gated per phase by founder
approval; fail-closed clearance, four-eyes, provenance separation and adjudication schema
are out of scope for every SRP item.

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| SRP-0 | Verify stale-snapshot vs live-normalizer split (ARF-2026-920016; fresh-screen contrast; distinct-profile count of the 298 hits) — read-only | — | — | ✅ 2026-07-16 — stale/partial snapshot confirmed (profile ids present; names/scores/match-types/media absent) · positive-hit fresh contrast inconclusive (fixture screen = 0 hits) | — |
| SRP-1 | ComplyAdvantage clarifications: match-score scale, stable profile id, hit-volume tuning, RPT-5 adverse-media persistence, data residency | — | — | ◐ partial: media/match-type API paths answered 2026-07-16; dashboard recon 2026-07-17 settled score display, triage UX, URLs, EU hosting · open: score scale, API-level entity key, fuzziness levers, region per Order Form | — |
| SRP-2 | Stale-report refresh pathway (governed re-screen; archive-first, adjudication guard, regulated archive table) | — | #786 | ✅ closed 2026-07-17 — harness merged + batch 1 validated all governance rails (10/10 archives, chained audits, adjudication guard, clean stop); fleet execution overtaken by events (legacy test apps deleted) | — |
| SRP-2a / RESCREEN-1 | Re-screen of an already-screened subject errors Mesh customer-creation (external identifier already assigned) — batch-1 finding; hits every future officer/periodic re-screen | P1 | #787 #801 | ◐ classification shipped + live-wording fix and existing-UUID harvest validated on staging 2026-07-19 (#801); Mesh rescreen endpoint confirmed 2026-07-18; Phase D merged 2026-07-19 ([#804](https://github.com/onboarda1234/onboarda/pull/804): delta-merge rescreen, `ENABLE_CA_RESCREEN` default-OFF, Re-screen button) · open: staging flag-on validation + monitor-on-demand entitlement (CA) | — |
| SRP-2b / RISK-FC-1 | Risk recompute lowered HIGH→LOW off a non-terminal/degraded screening report (TESCO 55→12.3) — fail-open | P1 | #787 | ✅ fixed 2026-07-17 — recompute_risk holds prior risk when a non-terminal report would lower it; raises still allowed; audited | — |
| SRP-3 | Review-page triage IA: summary strip, score-ranked hits with factor bands (profile-UUID dedup ruled out — Mesh recon 2026-07-17: profiles are minted per case, not stable entity keys), risk-type buckets, side-by-side disambiguation, progressive disclosure | — | #790 #793 #801 | ✅ closed 2026-07-19 — Phases A/B merged (#790/#793); populated-staging browser acceptance + enriched-sample inventory PASS (QAFIX-006/007 combined run); truth-flow defects found by that run fixed + validated (#801, rts-1.1) · Re-screen button tracked under SRP-2a; presentation consolidation tracked as SRP-3e (Phase E) | — |
| SRP-3e | Phase E — screening presentation consolidation: retire evidence popup chains (evidence inline on ranked hit cards), one-fact-one-place dedup, single renderer on queue + application surfaces, retire Agent 3 flat hit recital | — | — | 📋 scoped 2026-07-19 (founder direction: approved mockup is the target everywhere; no popups; no repeated fields) · design spec for sign-off after Phase D | — |
| SRP-4 | Agent 3 → triage narrative ("review these N first, here's why"), advisory-only, never mutates dispositions | — | #797 | ✅ closed 2026-07-19 — merged + deployed 2026-07-18; populated-data narrative acceptance PASS in combined run (ranked narrative, advisory labels, no probability vocabulary) | — |
| SRP-5 | Provider-side noise reduction for entity searches — validate against **Mesh** docs (Manus cited legacy API); sanctions/PEP-1 recall must not decrease | — | — | ⏸ blocked on SRP-1 answers · deliberately last | — |
| SRP-FIX-1 | Screening truth-flow hardening: live Mesh conflict wording classified; errored entity screens can never roll up as Clear; aml-type category fallback (rts-1.1, weights unchanged); conflict boolean + harvested UUIDs persist to stored report | P0 | #801 | ✅ merged + deployed + staging-validated 2026-07-19 (QAFIX-001 conflict classifies + non-clear; QAFIX-007 fresh screen scores 58/53 with category reasons; QAFIX-006 historical rts-1.0 untouched) | — |

## Phase 6 — Post-#661 staging follow-ups

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| 29 | `session_tokens.auto_purge=false` | — | #671 | ✅ merged | — |
| 30 | Drop provider names from portal comment | — | #668 | ✅ merged | — |
| 31 | Retention-policy seed fix + count probe | — | #671 | ✅ merged | — |
| 32 | De-flake periodic-review test | — | #669 | ✅ merged | — |

## Phase 7 — Applications page & pilot-readiness

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| PR-APP-STATUS-CANONICALIZATION-1 | Canonical status labels + senior queue + parity | P1 | #685 | ✅ merged | — |
| PR-APP-ACTION-OWNERSHIP-SCOPE-1 | Terminal decision & memo-approval ownership gate (= **FEO-013**) | P1/P2 | [#713](https://github.com/onboarda1234/onboarda/pull/713) | ✅ merged + validated 2026-07-09; sign-off memo awaiting founder signature | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#ownership-gate-pr-713) |
| 🟠 ops-enforce-staging-sha-alignment-gate | Staging-SHA gate + quarantine test logins | P0 | [#702](https://github.com/onboarda1234/onboarda/pull/702), [#882](https://github.com/onboarda1234/onboarda/pull/882) | ◐ code ✅ (SW-3) · test-login quarantine script + runbook merged 2026-07-25 (#882 — quarantine only; review proved a delete path would orphan memo/SAR/EDD attribution, so it was removed). **Codex validation 2026-07-26: FAIL** — the denylist-only identity guard accepted the live demo DSN and an arbitrary PG host; closed by [#886](https://github.com/onboarda1234/onboarda/pull/886) (positive `QUARANTINE_ALLOWED_DB_HOST` exact-host allowlist, 24-case bypass probe clean). **Repo + operator runsheet ready**: exact-host guard, mandatory `STAGING_QA_EMAIL` replacement smoke, ordered quarantine/empty-rerun/audit checks, and evidence template are pinned in the [operator runsheet](OPERATOR_RUNSHEET_REMAINING_OPS.md#1-staging-test-login-quarantine). No staging execution is claimed; row stays ◐ and 🟠 gate open until operator evidence lands | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#wave-a-prs-700-703) |
| perf-applications-default-list-projection | Slim paginated projection as default `/api/applications` payload | P2 | [#719](https://github.com/onboarda1234/onboarda/pull/719) | ✅ merged + staging-validated | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#applications-page-pair-prs-719-720-727) |
| audit-log-tamper-evidence-1 | *(cross-ref: = Phase 4 item 27, #691 — not counted)* | P2 | #691 | ✅ see item 27 | — |
| ux-applications-list-sort-status-tabs | Server-side sort + status tabs + fake-AI chat removal + toolbar declutter | P3 | [#720](https://github.com/onboarda1234/onboarda/pull/720) → [#727](https://github.com/onboarda1234/onboarda/pull/727) | ✅ merged + staging-validated (re-landed as #727 after wrong-base merge) | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#applications-page-pair-prs-719-720-727) |
| chore-applications-deadcode-cleanup | Delete dead approval branches (SW-2) | P3 | [#701](https://github.com/onboarda1234/onboarda/pull/701) | ✅ merged + validated | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#wave-a-prs-700-703) |
| CLIENT-PORTAL-RUNTIME-SMOKE-1 | Live client-credential smoke incl. cross-tenant denial (REGMIND-P1-006) | P1 | [#722](https://github.com/onboarda1234/onboarda/pull/722) | ✅ PASS 2026-07-09 (worker-trace limitation closed by #722) | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#portal-smoke-pr-722) |
| PERIODIC-BASELINE-METHOD-HYGIENE-1 | Clean 405 on POST-only baseline route (REGMIND-P2-001, SW-1) | P2 | [#700](https://github.com/onboarda1234/onboarda/pull/700) | ✅ merged | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#wave-a-prs-700-703) |
| PR-RISK-SECTOR-CALIBRATION-1 | Recalibrate sector risk + "unknown ≠ high" defaults (= **DCI-009**) — coordinate with RSMP | P2 | — | 📋 scoped | — |

### Applications-page readiness audit (Codex; final post-closure verdict: PILOT-READY / NOT PRODUCTION READY)

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| APP-727-001 | Cross-application audit-log leakage — immutable `application_id` scoping (Migration v2.50) | Critical | [#731](https://github.com/onboarda1234/onboarda/pull/731)→[#732](https://github.com/onboarda1234/onboarda/pull/732) | ✅ merged + validated; writer-side closed by #744; legacy-backfill + ref-uniqueness residuals open | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#app-727-prs-731-732) |
| APP-727-002 | Hostile filename → S3 `TagValue invalid` 500 — sanitise S3 tags | High | [#731](https://github.com/onboarda1234/onboarda/pull/731)→[#732](https://github.com/onboarda1234/onboarda/pull/732) | ✅ merged + validated | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#app-727-prs-731-732) |
| APP-AUD-002 | Role×route matrix harness (= P9-13) | Med | [#733](https://github.com/onboarda1234/onboarda/pull/733) | ✅ merged + validated; residuals tracked at P9-13 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#app-aud-prs-733-734-735) |
| APP-AUD-003 | Clean no-blocker approval path e2e | Med | [#734](https://github.com/onboarda1234/onboarda/pull/734) | ✅ merged + validated | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#app-aud-prs-733-734-735) |
| APP-AUD-001 | UI action-gate — analyst UI/authz alignment | Med | [#735](https://github.com/onboarda1234/onboarda/pull/735) | ✅ merged; staging re-validation pending | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#app-aud-prs-733-734-735) |
| APP-727-audit-writer-id-1 | Populate `application_id`/`request_id` in audit writers | Med | [#744](https://github.com/onboarda1234/onboarda/pull/744) | ✅ closed 2026-07-11; direct-insert writers still ref-only (write-forward) | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#audit-writer-id-pr-744) |
| APP-AUD-gov-dup-1 | Duplicate audit rows from two accepted governance requests (idempotency) | Low | [#815](https://github.com/onboarda1234/onboarda/pull/815) | ✅ merged + staging-validated 2026-07-21 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#eight-remediation-prs-808-815) |
| APP-AUD-005 | `/api/applications` ignores `search=` (UI uses `q=`) — document or alias | Low | [#814](https://github.com/onboarda1234/onboarda/pull/814) | ✅ merged + staging-validated 2026-07-20 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#eight-remediation-prs-808-815) |
| APP-A11Y-SORT-HEADERS-1 | Keyboard-accessible sortable headers (CodeRabbit on #727) | P3 | — | ⬜ pending | — |

### Applications-module confirmation audit 2026-07-16 (Codex, against `464972a`; final post-closure verdict: PILOT-READY / NOT PRODUCTION READY)

> Pre-audit remediation recorded here too (register was reconciled 2026-07-15
> before this stream landed). Application Review module is FROZEN per
> `CLAUDE.md` Module Status & Change Control — every code row below that is
> not ✅ requires explicit founder approval before implementation.

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| APP-REV-MEMO-HARDENING-1 | Memo workflow hardening — mutations via `boApiCall` (no false success), authoritative detail refresh, signoff disabled-state + static guards | P1 | `a237008` (direct to main) | ✅ merged + browser-validated | — |
| APP-PERF-DETAIL-INDEX-1 | Detail-open perf — `idx_agent_executions_document_id`, committed independently of later failing migrations + ERROR-level verify | P2 | [#771](https://github.com/onboarda1234/onboarda/pull/771)→[#774](https://github.com/onboarda1234/onboarda/pull/774) | ✅ merged + staging-validated 2026-07-16 (index active, planner verified) | — |
| — | Party correction modal locked to clicked person; residence/appointment fields correctable; directors-UBOs report de-flake | — | #794 | ✅ merged 2026-07-18 | — |
| APP-CONF-001 | Analyst RMI/Escalate UI/authz mismatch — matrix + UI aligned to decision-endpoint authority; contract tests pin all three surfaces | P1 | [#782](https://github.com/onboarda1234/onboarda/pull/782) | ✅ merged + revalidated 2026-07-16 (33/33 Chromium/Firefox/WebKit; analyst API 403) | — |
| APP-CONF-002 | Retained synthetic records visible in normal staging list | P2 | — | ✅ closed 2026-07-16 — all 3 records fixture-marked (approved); full normal-list sweep clean (247 rows, 0 synthetic visible) | — |
| APP-CONF-003 | Role-harness cross-client probe not actually cross-client — *(cross-ref: = P9-13 open half "cross-client seed fix" — not counted)* | P2 | [#881](https://github.com/onboarda1234/onboarda/pull/881) | ✅ closed 2026-07-25 — see P9-13 | — |
| APP-CONF-004 | Largest-case detail-open p95 2.105s > 2s prod target — round-2 detail optimisations (dedupe gate recompute, batch name resolution, single monitoring load) + p95 monitor; frozen-scope approval required | P2 | — | 📋 scoped | — |
| APP-CONF-005 | Firefox unreachable-code warning + report-only CSP console diagnostics not production-clean (CSP enforcement relates to item 22) | P2 | [#894](https://github.com/onboarda1234/onboarda/pull/894) | ◐ **half recorded as intended-by-design, half deferred** — the report-only CSP console output is the PR-22a *measuring* policy working as built (a stricter inert mirror whose would-be violations are meant to surface in devtools to scope item 22's strict-CSP rewrite); silencing it would destroy the measurement. The Firefox unreachable-code warning is a browser lint with no repro available here — deferred rather than blind-editing the 550KB frozen back-office file for a cosmetic P2 | — |
| APP-CONF-006 | Applications freeze policy absent from `CLAUDE.md` | P2 | [#776](https://github.com/onboarda1234/onboarda/pull/776) | ✅ closed 2026-07-16 (Module Status & Change Control section) | — |
| APP-PROD-LIVE-RUN-1 | Live-provider e2e run for Applications workflows — live Sumsub IDV + document verification without `CLAUDE_MOCK_MODE`, or formal scope sign-off (CA / OpenCorporates halves tracked at P9-3 / P9-14) | prod | — | ⬜ | — |

## Phase 8 — Monitoring alerts page (M-series)

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| M2.3 | QA sampling implementation | — | — | 📋 spec drafted | — |
| M1.2 | Status runtime audit/backfill | — | — | ⬜ pending | — |
| M1.3 | Status CHECK hardening | — | — | ⏸ depends on M1.2 | — |
| M2.4 | Status-sync on downstream close | — | — | ⬜ pending | — |
| M3.2 | Expiry-missing / coverage blind-spot report | — | — | ⬜ pending | — |
| M3.3 | Monitoring UI cleanup | — | — | ⬜ pending | — |
| M3.4 | Agent 1 verification for refreshed identity docs | — | — | 📋 decision approved | — |
| DOC-HEALTH-B/C/D | Document-health scheduler Phase B/C/D rollout | — | — | ⏸ pending go/no-go | — |
| M4.x | Screening-change monitoring phase | — | — | ⬜ not yet decomposed | — |

## Phase 9 — Regulatory Decision Integrity (RDI audit / Audit 1)

> Source: RegMind Production Audit 1, run against `c8b6dac`. 13 findings.
> Management response 2026-07-07 reclassified RDI-002 (CRITICAL → HIGH
> policy-exception) and RDI-005 (CRITICAL → HIGH Enterprise pre-enable blocker).
> The three current-stage blocking CRITICALs (RDI-001/004/006 = Wave 1) are
> closed and validated; Audit 2 subsequently ran against `e66405a`.

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| P10-1 | Server-side materiality classification (RDI-006) | CRITICAL | [#697](https://github.com/onboarda1234/onboarda/pull/697) | ✅ merged + validated; four-eyes scope closed by #704 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p10-wave-1-prs-695-698-and-704) |
| P10-2 | Fail-closed decision & memo persistence (RDI-001/007/011) | CRITICAL | [#698](https://github.com/onboarda1234/onboarda/pull/698) | ✅ merged + validated | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p10-wave-1-prs-695-698-and-704) |
| P10-3 | Risk-staleness gate on final decisions (RDI-004) | CRITICAL | [#696](https://github.com/onboarda1234/onboarda/pull/696) | ✅ merged + validated | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p10-wave-1-prs-695-698-and-704) |
| P10-4 | Per-decision-type prerequisite gates (RDI-003/008) | HIGH | — | 📋 scoped — policy decision needed | — |
| P10-5 | Decision-record coverage + provenance (RDI-009 non-SAR, 010) — includes RDI-002 residual assertions | HIGH | — | 📋 scoped (P10-2 dependency now met) | — |
| P10-6 | Sign-off IP attribution (RDI-012) — RDI-107 explicit allowlist delivered default-off | HIGH | [#708](https://github.com/onboarda1234/onboarda/pull/708), [#809](https://github.com/onboarda1234/onboarda/pull/809) | ✅ merged + staging-validated 2026-07-21 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#eight-remediation-prs-808-815) |
| P10-7 | Append-only audit at DB level (RDI-013 non-SAR) | MEDIUM | [#837](https://github.com/onboarda1234/onboarda/pull/837), [#875](https://github.com/onboarda1234/onboarda/pull/875), [#879](https://github.com/onboarda1234/onboarda/pull/879), [#886](https://github.com/onboarda1234/onboarda/pull/886) | ◐ code half ✅ (DB triggers, 2026-07-22) · grants pack merged 2026-07-25 (#875) — Codex r1 5(b) found the maint role could not execute the sanctioned purge, closed by #879 (full privilege set, proven on PG in CI); Codex r2 found the set overprivileged (UPDATE/TRUNCATE + evidence-read never used), minimised by #886 with convergence REVOKEs + behavioural negatives · RDS psql execution = operator step ([runbook](OPS_HARDENING_RUNBOOK.md) §3); documented residual: app role remains table owner | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#second-remediation-batch-prs-833-837) |
| RDI-002 | LOW/MEDIUM fast-path — by-design HIGH policy-exception; policy approved & signed (Aisha Sudally, 2026-07-07): [`LOW_MEDIUM_FASTPATH_APPROVAL_POLICY.md`](compliance/LOW_MEDIUM_FASTPATH_APPROVAL_POLICY.md) | HIGH | — | ✅ policy approved · residual code assertions → P10-5 | — |
| RDI-005 | SAR permanence (= **DCI-002**) — Enterprise pre-enable blocker; safe only while `ENABLE_SAR_WORKFLOW`/`ENABLE_SAR_STR` stay false; same guard covers SAR slices of RDI-009/013 | HIGH | — | ⏸ deferred until Enterprise SAR/STR enablement | — |

## Phase 10 — Backend Security & Authorization (BSA audit / Audit 2)

> Source: RegMind Production Audit 2, run against `e66405a`. 19 findings
> (BSA-001…019); BSA-002 = Phase 4 item 26 (closed via #728). Positively
> verified: 12-char password policy, CSRF double-submit, Sumsub
> HMAC-before-parse, mock-mode prod hard-block, no-wildcard CORS in prod,
> security headers. Note: the 2026-07-11 re-run issued a NEW BSA-001…021 set —
> those are tracked with the R2- prefix in the Re-audit section.

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| P11-1 | Fail-closed revocation + post-await session re-validation (BSA-001/014) | HIGH+MED | [#705](https://github.com/onboarda1234/onboarda/pull/705) | ✅ merged + validated | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#overnight-batch-prs-705-708) |
| P11-2 | Dependency CVE remediation + pip-audit CI gate (BSA-015) — 🔴 blocker, closed | HIGH | [#730](https://github.com/onboarda1234/onboarda/pull/730) | ✅ merged + validated 2026-07-09 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p11-2-pr-730) |
| P11-3 | Fail-closed inputs + AI budget (BSA-006/007/013) | MED+LOW | [#706](https://github.com/onboarda1234/onboarda/pull/706) | ✅ merged + validated | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#overnight-batch-prs-705-708) |
| P11-4 | Offload blocking I/O off the IOLoop (BSA-004/005) — coordinate with item 12 | MED | — | 📋 scoped | — |
| P11-5 | AI prompt sanitisation + output schema + circuit breaker (BSA-011/012) | MED | [#836](https://github.com/onboarda1234/onboarda/pull/836) | ◐ breaker + prompt-fencing merged + staging-validated 2026-07-22, flag-gated OFF · activation is a founder sign-off (PR body); `extract_document_fields` schema follow-up open | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#second-remediation-batch-prs-833-837) |
| P11-6 | AuthZ & audit hardening — admin reset re-auth, `log_authz_denial()` routing (BSA-003/009) | MED | [#834](https://github.com/onboarda1234/onboarda/pull/834) | ✅ merged + staging-validated 2026-07-22 (admin re-auth + 9 denial sites audited) · next BSA-009 slice (6 portal type-gates, 3 cm role gates) noted | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#second-remediation-batch-prs-833-837) |
| P11-7 | Document-download attachment + webhook signature hygiene (BSA-008/010, + DCI-017) | MED+LOW | [#833](https://github.com/onboarda1234/onboarda/pull/833) | ✅ merged + staging-validated 2026-07-22 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#second-remediation-batch-prs-833-837) |
| P11-8 | Supply-chain pinning (BSA-016/017/019 = DCI-022/024) — hash-pinned runtime lock enforced | MED+LOW | [#712](https://github.com/onboarda1234/onboarda/pull/712), [#811](https://github.com/onboarda1234/onboarda/pull/811) | ✅ merged + staging-validated 2026-07-21 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#eight-remediation-prs-808-815) |
| P11-9 | CI coverage-gate fail-closed (BSA-018 = DCI-026) | LOW | [#707](https://github.com/onboarda1234/onboarda/pull/707) | ✅ merged + deployed | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#overnight-batch-prs-705-708) |

## Phase 11 — Data Integrity, Compliance Logic & Infrastructure (DCI audit / Audit 3)

> Source: RegMind Production Audit 3, run against `956ed5b`. 30 findings
> (DCI-001…030); schema safety rated UNSAFE; verdict REMEDIATE BEFORE
> PROCEEDING — 6 blockers (DCI-001/003/012/018/019/027) + 1 Enterprise
> pre-enable blocker (DCI-002). 11 findings tracked elsewhere (cross-referenced,
> not duplicated): DCI-002 = RDI-005 · DCI-009 = PR-RISK-SECTOR-CALIBRATION-1 ·
> DCI-017 → P11-7 · DCI-018 = item 21 (blocker) · DCI-019 = P9-1 (blocker) ·
> DCI-022/024 = P11-8 ✅ · DCI-023 = P9-4 · DCI-026 = P11-9 ✅ ·
> DCI-027 = P9-8 (CRITICAL blocker) · DCI-030 = P9-10.

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| P12-1 | Regulated-record deletion protection (DCI-001/003) — 🔴 blocker, closed for pilot | CRITICAL+HIGH | [#738](https://github.com/onboarda1234/onboarda/pull/738) | ✅ merged + validated 2026-07-11 (pilot scope) · Phase A discovery report open as draft #737 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p12-1-pr-738) |
| P12-2 | Change-implementation fail-closed recompute + audit-in-transaction (DCI-012/013) | HIGH+MED | [#715](https://github.com/onboarda1234/onboarda/pull/715) | ✅ merged + validated 2026-07-09; M3 already-approved-apps residual open | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p12-2-pr-715) |
| P12-3 | Compliance-logic corrections — fail-closed risk-config load, `jur_rating` floor mutation, `MULTI_GAP_ESCALATION` branch order (DCI-008/010/011) | HIGH+HIGH+MED | [#710](https://github.com/onboarda1234/onboarda/pull/710) | ✅ merged 2026-07-08; deploy precondition (validate live staging risk_config row) awaits Codex sign-off | — |
| P12-4 | Migration hard-stops + schema-drift detection (DCI-005/004) | HIGH | [#711](https://github.com/onboarda1234/onboarda/pull/711), [#877](https://github.com/onboarda1234/onboarda/pull/877) | ✅ DCI-005 hard-stops #711 · DCI-004 warn-only drift detection merged + staging-deployed 2026-07-25 (#877: boot-time check vs `schema_expected.json`, CI freshness gate, PG name-parity test) | — |
| P12-5 | Status-column CHECK constraints (DCI-006, Migration v2.47) | MED | [#716](https://github.com/onboarda1234/onboarda/pull/716) + [#739](https://github.com/onboarda1234/onboarda/pull/739) | ✅ merged; staging constraints installed via #739, executed 2026-07-11 · 54-FK follow-up tracked at DCI-104 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p12-5-dci-006-prs-716-and-739) |
| P12-6 | PG pool connection validation — pre-ping on checkout (DCI-007) | MED | [#709](https://github.com/onboarda1234/onboarda/pull/709) | ✅ merged 2026-07-08 | — |
| P12-7 | Verification-matrix fidelity — HYBRID only on deterministic INCONCLUSIVE; resolve 5 TODO mappings (DCI-014/015) | MED+LOW | [#835](https://github.com/onboarda1234/onboarda/pull/835) | ◐ mechanism (rules-first gate + INCONCLUSIVE-aware aggregation) merged + staging-validated 2026-07-22, flag-gated OFF · DCI-015 mappings + evaluator activation = founder sign-off ([memo](compliance/P12_7_VERIFICATION_MATRIX_DECISION_MEMO.md)) | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#second-remediation-batch-prs-833-837) |
| P12-8 | Retention purge enforceability + purge-log evidence (DCI-020/021, Migration v2.48) | MED | [#717](https://github.com/onboarda1234/onboarda/pull/717) + hotfix [#723](https://github.com/onboarda1234/onboarda/pull/723) | ✅ merged + deployed | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p12-8-prs-717-and-723) |
| P12-9 | Observability hardening — JSON logs, request-correlation ids, readiness gates (DCI-028/029, Migration v2.49) | MED | [#718](https://github.com/onboarda1234/onboarda/pull/718) | ✅ merged + deployed | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p12-9-pr-718) |
| P12-10 | Infra guards — upload body-size pre-buffering, deploy fails on `services-stable` timeout (DCI-016/025; stability half partly mitigated by #702) | MED+LOW | [#812](https://github.com/onboarda1234/onboarda/pull/812), [#875](https://github.com/onboarda1234/onboarda/pull/875) | ◐ **upload pre-buffering REOPENED 2026-07-26** — the Audit-2 re-run (R3-BSA-022, independently CONFIRMED empirically) proved `max_body_size` was passed to the `Application()` constructor (`server.py:44262`), where Tornado ignores it; a 50KB body was accepted against a 1KB cap in test, while the same value on `HTTPServer`/`listen` rejected pre-buffer. The recorded "upload limits ✅ 2026-07-21" enforces only Tornado's ~100MB default pre-buffer, and per-route caps (10/20/25MB) run after full buffering · deploy-timeout half closed 2026-07-25 (#875: backend wait fails closed after 2×10-min attempts + diagnostics; proven live by the very deploy that shipped it) | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#eight-remediation-prs-808-815) |

## Phase 12 — Frontend & Operational Readiness (FEO audit / Audit 4)

> Source: RegMind Production Audit 4, run against `57890e3`. 15 findings
> (FEO-001…015). Consolidated 4-audit verdict: BLOCKED for uncontrolled
> production; conditional for controlled pilot. 8 findings tracked elsewhere:
> FEO-008 = P9-4/P9-5 · FEO-009 = DCI-027 = P9-8 · FEO-010 = P9-7 ·
> FEO-011 = P9-10 · FEO-012 = P9-2 · FEO-013 = PR-APP-ACTION-OWNERSHIP-SCOPE-1 ✅ ·
> FEO-015 = Optional Modernization §2. Frontend PRs touch
> `arie-backoffice.html` / `arie-portal.html` only.

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| P13-1 | Back-office stored-XSS elimination (FEO-001/002) — 🔴 blocker, closed; screening/notes/doc-metadata renderers are follow-up | HIGH | [#729](https://github.com/onboarda1234/onboarda/pull/729) | ✅ merged + validated 2026-07-09 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p13-1-pr-729) |
| P13-2 | Single API wrapper + consistent CSRF for all 23 raw `fetch()` sites (FEO-003) | MED | — | 📋 scoped | — |
| P13-3 | Defensive API response parsing — status/Content-Type before `res.json()` (FEO-004) | MED | [#883](https://github.com/onboarda1234/onboarda/pull/883) | ✅ merged (`0acbcc4`) 2026-07-25 — `boApiCall` (transport for ~161 sites incl. every frozen decision/memo/detail path) parsed BEFORE its status checks, so a non-JSON error threw SyntaxError and the branch building `apiErr.status`/`.payload` never ran; downstream `err.status === 403` checks were testing a status-less SyntaxError. Review verified success-path identity across 84 body-shape × content-type combinations (incl. `null`/`false`/`0`/`""`/arrays) — the only divergences are cases where the old code threw. Review REJECT r2 caught two real defects, both fixed: the new helper broke `test_p13_backoffice_xss_static.py` (red CI — it is defined outside the regions that test slices, so converted renderers hit a swallowed ReferenceError), and it silently returned `{}` for any response exposing neither `headers` nor `text()` — the repo's house mock shape — now falling back to `res.json()`. One of its own tests was proven vacuous and fixed. **Codex validation 2026-07-26: PARTIAL** — a valid-JSON `null` error body still lost its status (boApiCall dereferenced `data.error` → status-less TypeError); closed by [#886](https://github.com/onboarda1234/onboarda/pull/886) (error branches read a normalized object view; success path verbatim, 2xx `null` returns `null`, pinned + mutation-verified) | — |
| P13-4 | App-detail render race guard — request nonce in `openAppDetail` (FEO-005) | MED | — | 📋 scoped | — |
| P13-5 | Role-UI fail-closed until RBAC matrix loads (FEO-006) | LOW | [#884](https://github.com/onboarda1234/onboarda/pull/884) | ✅ **closed as race-elimination, founder-accepted (Aisha Sudally, 2026-07-26)** — merged (`f0f7a81`) 2026-07-25: the matrix now settles before the shell paints (bounded 3s so sign-in cannot be blocked), removing the optimistic-render window, and a failed load is surfaced via the degraded-load banner. The literal fail-CLOSED inversion was NOT done — `test_approval_ux_gates_static.py::test_ux_gates_fail_open_when_permission_matrix_unloaded` pins fail-open by name after a staging smoke blocker (a legitimate CO lost Approve); **the founder accepted the race-elimination version as satisfying the item's intent (2026-07-26)** — the literal inversion is declined: it would trade a cosmetic flicker (every decision action is re-checked server-side and awaits the matrix before acting) for re-creating a known lockout of a legitimate officer. Independent review upheld that call on stronger grounds: the decision actions already `await` the matrix before acting and the server enforces roles on every decision endpoint, so FEO-006's residual content is a cosmetic flicker, not an authz hole. It also cleared the cross-session question — `/config/roles-permissions` returns a module-level STATIC matrix, identical for every caller, so no per-user matrix can leak. Review REJECT r2 caught that the first revision was a silent NO-OP on the common auto-login path (`resetBackofficeSessionData()` nulled the preloaded matrix between kick-off and settle, and the settle still reported success, suppressing the banner); fixed by hoisting the reset, with runtime tests that drive the real sequence rather than its shape | — |
| P13-6 | Portal intake PII out of sessionStorage — server-side save/resume (FEO-007) | MED | — | 📋 scoped | — |
| 🟠 P13-7 | Compliance-officer SOP pack (FEO-014) | MED | [#745](https://github.com/onboarda1234/onboarda/pull/745) | ◐ docs ✅ merged 2026-07-13 (`02eeae5`) · 🟠 Section 16 execution open (officers named/trained, scope approved, signatures) | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p13-7-pr-745) |

## Phase 13 — Pilot Controls Pack

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| 🟠 33 | Pilot-scope guards (server-side) — pilot operational gate | — | [#880](https://github.com/onboarda1234/onboarda/pull/880) | ✅ merged 2026-07-25 — `PILOT_SCOPE` vetoes the enterprise modules ABOVE their feature flags (so the exclusion no longer depends on deployed flag values this repo cannot see), and closed a real gap: `/memo/supervisor/run` and `/memo/supervisor` had NO enterprise gate and admitted `analyst`, a broader role set than their gated siblings | — |
| 34 | Dashboard API performance (15.1s → sub-2s) | — | — | ⬜ pending | — |
| 35 | Screening full-evidence hydration performance | — | — | ⬜ pending | — |
| 36 | Persisted negative-path fixtures — controlled-pilot staging evidence | — | #748, #749 | ✅ closed 2026-07-12 (pilot scope; staging left clean) | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#item-36-prs-748-749) |
| 37 | Lower-privilege fixture authz regression tests | — | #692 | ✅ merged | — |
| 38 | Pilot operations runbook | — | #689 | ✅ merged | — |
| 🟠 — | *(cross-ref: CA production workspace validation = P9-3, Phase 14 — not counted)* | — | #498 | ⏸ see P9-3 | — |

### Pilot canonical dataset & staging baseline (2026-07)

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| — | Canonical dataset v1 — 41-scenario reviewed manifest + guarded idempotent seeder + triple-gated CLI | — | #784 | ✅ merged | — |
| — | Authorized staging reset — all 944 pre-reset synthetic applications purged at `a10d2c3`; risk-config aligned (Manufacturing 2; D3 40/35/25); RSMP OFF; audit chains intact | — | [#788](https://github.com/onboarda1234/onboarda/pull/788) | ◐ executed 2026-07-17 · closure docs open as draft #788 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#staging-reset-2026-07-17-pr-788) |
| — | Canonical seeder PostgreSQL dry-run fix (override flag / type mismatch) | — | #789 | ✅ merged | — |
| — | Canonical demo completion — structured memo fixture sections, deterministic periodic-review dates, notification suppression, fixture labels | — | #791 | ✅ merged 2026-07-18; dataset seeded on reset staging (41/41 `RM-PILOT-*`, validation pinned `9a77e11`) | — |
| — | Canonical document-types fix | — | #795 | ✅ merged | — |
| — | Canonical memo detail rendering compatibility | — | #796 | ✅ merged | — |

## Phase 14 — Production readiness

| ID | Title | Type | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| P9-1 | Enable live GDPR erasure, dual-control incl. S3/file deletion (= **DCI-019 blocker**; PC-4 control pack) | code | — | ⬜ | — |
| P9-2 | PC-1 evidence-pack continuity residual + hashes-only continuity ledger (+ **FEO-012**: supervisor export strips hash fields) | code | — | ⬜ | — |
| P9-3 | ComplyAdvantage prod workspace validation (PR-PROV1) | ops/vendor | [#498](https://github.com/onboarda1234/onboarda/pull/498) | ⏸ blocked — dashboard-mode evidence; PR closed unmerged 2026-07-09, record carried in evidence | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p9-3-pr-498) |
| P9-4 | Provision prod environment app.regmind.co (+ **DCI-023** IaC, **FEO-008**) | ops | — | ⬜ | — |
| P9-5 | Drill prod deploy + rollback with evidence (+ **FEO-008**) | ops | — | ⬜ | — |
| P9-6 | Load/performance test at prod scale | test/ops | — | ⬜ | — |
| P9-7 | Pen test + security review + rehearsed secret rotation (+ **FEO-010**) | security | — | ⬜ | — |
| P9-8 | DR/backup drill, restore/PITR, RTO/RPO (= **DCI-027 CRITICAL blocker** = **FEO-009**) | ops | [#882](https://github.com/onboarda1234/onboarda/pull/882) | ◐ posture check + drill runbook merged 2026-07-25 (retention/deletion-protection/encryption/**PITR freshness**, which proves point-in-time recovery works rather than is merely configured) · the timed restore drill that yields the measured RTO is the operator half ([runbook](OPS_HARDENING_RUNBOOK.md) §6) — the script reports `rto_seconds_observed: null` on purpose. Codex 2026-07-26 caught a teardown race (`--apply-immediately` does not block, so the delete could fire with deletion protection still active); fixed by [#886](https://github.com/onboarda1234/onboarda/pull/886): poll `DeletionProtection=False` before delete + `db-instance-deleted` waiter. **Repo + operator runsheet ready**: unique-target restore, restored-DB integrity checks, measured wall-clock RTO, PITR-lag RPO, mandatory teardown poll/waiter, and evidence template are ordered in the [operator runsheet](OPERATOR_RUNSHEET_REMAINING_OPS.md#2-dr-posture-and-timed-point-in-time-restore-drill). No AWS/RDS execution is claimed; DCI-027 remains CRITICAL and ◐ until operator evidence lands | — |
| P9-9 | Legal/compliance sign-off (residency, DPA, regulator) | legal | — | ⬜ | — |
| P9-10 | Prod monitoring/alerting/on-call (+ **DCI-030**, **FEO-011**) | ops | [#882](https://github.com/onboarda1234/onboarda/pull/882) | ◐ metric filters + alarms for the 7 emitted-but-unalarmed metrics, a worker HEARTBEAT alarm (catches running-but-wedged, which ECS LiveTaskCount cannot) and a log-based error rate, merged 2026-07-25. **Repo + operator runsheet ready**: `provision_production_monitoring.py --apply` now refuses actionless alarms (exit 2, no AWS call — verified end-to-end, guard mutation-tested), and the [operator runsheet](OPERATOR_RUNSHEET_REMAINING_OPS.md#3-production-monitoring-paging-and-datapoint-verification) requires a confirmed SNS subscription/on-call rota, explicit production target, and real **metric datapoints** (alarm state is not accepted as verification; the error-rate check asserts `Sum` because `defaultValue: 0` makes `SampleCount` non-zero even against a dead filter). **Scope of the refusal — two residuals:** it applies to THIS script only (`provision_screening_queue_p95_alarm.py` and `provision_pr6_observability.py` still create actionless alarms on `--apply`; the latter auto-creates a zero-subscriber SNS topic, which looks wired and pages nobody — see R3-OPS-001), and it checks ARN *presence*, not deliverability (a well-formed ARN for an unsubscribed topic passes). No AWS execution is claimed; row stays ◐ until production apply and evidence land | — |
| P9-11 | Close parked prod-posture decisions (PR-25 + PR-17) | decision | — | ⬜ | — |
| P9-12 | ECR immutable image tags (REGMIND-P2-004) | ops | [#875](https://github.com/onboarda1234/onboarda/pull/875) | ◐ deploy tagging audited immutability-compatible (one per-SHA tag, no `:latest`) + flip/rerun runbook merged 2026-07-25 · AWS `put-image-tag-mutability` = operator step ([runbook](OPS_HARDENING_RUNBOOK.md) §1) | — |
| P9-13 | Full authz/tenant-isolation route matrix (role-by-route) | security | [#733](https://github.com/onboarda1234/onboarda/pull/733), [#881](https://github.com/onboarda1234/onboarda/pull/881) | ◐ **code-complete in this PR; founder review/merge pending**. Cross-client seed fix ✅ 2026-07-25 (#881 — second tenant + reciprocal + positive control). Runtime suite now uses production-shaped rows to reach and mutation-verify every previously shallow cell: HIGH dual control after current risk provenance + memo + documents + enhanced-review + live-screening + webhook-backed IDV prerequisites; memo peer-supervisor ownership; sensitive screening second-review senior admission; admin/SCO-only unmatched IDV reconciliation visibility. Existing decision authority, IDV senior-outcome escalation, reassignment, and reciprocal tenant isolation remain covered. Self-adversarial result: disabling/loosening each of the four named server checks turns its targeted test red; restored production source has no diff. Frozen Application Review/Screening Queue behavior is unchanged (test-only fixture/assertion changes). **Residual (review-scoped):** coverage is DEPTH-complete for the named matrix cells, not BREADTH-complete — `server.py` registers ~200 authenticated routes and this harness deep-probes 7; a full custom-denial route inventory remains out of scope, which is why this row stays ◐ rather than ✅ | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#app-aud-prs-733-734-735) |
| P9-14 | Registry KYB (OpenCorporates) simulated → real/production | code/vendor | — | ⬜ | — |

---

## Re-audit 2026-07-11 (`d23cc45`) — consolidated re-run

> Source: full consolidated audit re-run against `main` = `d23cc45`; read-only
> Codex re-verification CONFIRMED every finding. Re-run IDs use the 1xx series
> (RDI-1xx, DCI-101…123, FEO-1xx) plus a fresh BSA set carried here with the
> **R2-** prefix (R2-BSA-001 ≠ original BSA-001). The re-run walked back the
> earlier "≈94–96% pilot-ready" estimate; since then the R2-BSA-001…004 cluster
> is closed and RSMP Tier 0A/0B are merged — Tier 0C remains the final RSMP
> pilot workstream.

### Net-new findings

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| 🔴 DCI-108 | Risk parser under-scores "very complex" ownership → 3 (`rule_engine.py:1219-1273`); with DCI-109 can flip MEDIUM→LOW | HIGH | [#753](https://github.com/onboarda1234/onboarda/pull/753), [#755](https://github.com/onboarda1234/onboarda/pull/755) | 🔨 engineering merged (0A/0B/0D/PR-1b/0C-A hotfix — see RSMP below) · Tier 0C-A rerun + 0C-B ⬜ | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#rsmp-prs-751-753-755-764) |
| 🔴 DCI-109 | "non-regulated" resolves to 1 via dict-ordering fall-through (same site); same MEDIUM→LOW flip risk | HIGH | [#753](https://github.com/onboarda1234/onboarda/pull/753), [#755](https://github.com/onboarda1234/onboarda/pull/755) | 🔨 engineering merged (0A/0B/0D/PR-1b/0C-A hotfix — see RSMP below) · Tier 0C-A rerun + 0C-B ⬜ | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#rsmp-prs-751-753-755-764) |
| DCI-110 | Middle-band turnover 500k–5m over-scores to 4 (severity corrected HIGH→MED 2026-07-11: over-, not under-scoring) | MED | — | 📋 scoped | — |
| R2-BSA-001 | Supervisor routes bypass BaseHandler middleware + wildcard CORS on authenticated APIs | HIGH | [#743](https://github.com/onboarda1234/onboarda/pull/743) | ✅ closed, staging-validated 2026-07-11 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#r2-bsa-cluster-prs-743-747) |
| R2-BSA-002 | Supervisor actor client-forgeable via request-body reviewer/escalation fields | HIGH | [#743](https://github.com/onboarda1234/onboarda/pull/743) | ✅ closed, staging-validated 2026-07-11 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#r2-bsa-cluster-prs-743-747) |
| R2-BSA-003 | Supervisor reviews/overrides/escalations persisted via raw `sqlite3` to ephemeral container disk (audit-record loss) | HIGH | [#747](https://github.com/onboarda1234/onboarda/pull/747) | ✅ closed, staging-validated 2026-07-12 (Migration v2.52) | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#r2-bsa-cluster-prs-743-747) |
| R2-BSA-004 | General CSRF bypass — `/webhook` URI substring match skips CSRF on ANY path | HIGH | [#743](https://github.com/onboarda1234/onboarda/pull/743) | ✅ closed, staging-validated 2026-07-11 (exact-path allowlist) | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#r2-bsa-cluster-prs-743-747) |

### Merged items re-flagged PARTIAL

| ID | Refines | Title | GitHub | Status | E |
|----|:--:|-------|:--:|----|:--:|
| R2-BSA-016 | item 26 / #728 | AI-route limiter gaps: `/api/documents/{id}/verify` + both supervisor pipeline triggers unlimited; enhanced-upload limiter process-local | [#808](https://github.com/onboarda1234/onboarda/pull/808) | ✅ merged + staging-validated 2026-07-21 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#eight-remediation-prs-808-815) |
| R2-BSA-019 | P11-8 / #712 | No hash-pinned lockfile / `pip install --require-hashes`; deps pinned by version only | [#811](https://github.com/onboarda1234/onboarda/pull/811) | ✅ merged + staging-validated 2026-07-21 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#eight-remediation-prs-808-815) |
| RDI-107 | P10-6 / #708 | Trusted-proxy check trusts ANY private/loopback peer; needs explicit proxy-CIDR allowlist | [#809](https://github.com/onboarda1234/onboarda/pull/809) | ✅ merged + staging-validated 2026-07-21 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#eight-remediation-prs-808-815) |
| DCI-104 | P12-5 / #716 | 3 v2.47 CHECK constraints absent on staging + 54 unindexed FKs | [#739](https://github.com/onboarda1234/onboarda/pull/739), [#810](https://github.com/onboarda1234/onboarda/pull/810) | ◐ constraints ✅ · FK-index batches #771/#774/#810 ✅; residual backlog open | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#eight-remediation-prs-808-815) |
| R2-PROC-1 | (new, LOW) | Staging QA/validation must not write raw SQL into regulated tables — route probe writes through the app or a marked fixture path | [#813](https://github.com/onboarda1234/onboarda/pull/813) | ✅ merged + staging-validated 2026-07-20 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#eight-remediation-prs-808-815) |

### Canonical staging dataset

| ID | Title | GitHub | Status | Evidence |
|----|-------|:--:|----|:--:|
| PILOT-DATA-001 | Canonical memo and lifecycle demo completion | Draft PR | 🔨 deterministic memo contract, fixture notification suppression, Monitoring/Periodic fixture visibility; AI Supervisor explicitly excluded; post-deploy UI revalidation still required | [Guide](pilot/PILOT_CANONICAL_DATASET.md) |

### RSMP — Risk Scoring Model Pack (DCI-108/109 response)

| ID | Title | GitHub | Status | E |
|----|-------|:--:|----|:--:|
| RSMP-DOCS | Audit/review pack (full audit, founder decision pack, scenario matrix, settings register) | [#751](https://github.com/onboarda1234/onboarda/pull/751) | ✅ merged | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#rsmp-prs-751-753-755-764) |
| RSMP-0A | Tier 0A — guarded parser + mapping fidelity (activation flag OFF) | [#753](https://github.com/onboarda1234/onboarda/pull/753) | ✅ merged; deployed within the 0B staging validation at `dd4784b` | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#rsmp-prs-751-753-755-764) |
| RSMP-0B | Tier 0B — fail-closed routing on unresolved mappings | [#755](https://github.com/onboarda1234/onboarda/pull/755) | ✅ merged + staging-validated at `dd4784b` | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#rsmp-prs-751-753-755-764) |
| RSMP-0D | Tier 0D — runtime and Back Office risk-model alignment | [#768](https://github.com/onboarda1234/onboarda/pull/768) | ✅ merged + staging-validated at `7e91114`; read-only UI/export evidence aligned; activation OFF | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#rsmp-tier-0d-pr-768) |
| RSMP-0D-UI | Risk presentation alignment — executive Risk Assessment dashboard + evidence PDF redesign; dashboard labels/EDD presentation aligned | [#798](https://github.com/onboarda1234/onboarda/pull/798), [#799](https://github.com/onboarda1234/onboarda/pull/799) | ✅ merged 2026-07-18/19 | — |
| RSMP-0C-HF | Tier 0C-A hotfix — maximum selected-service risk (D3.1 read the legacy singular `primary_service` alias; now MAX of all selected services) | [#775](https://github.com/onboarda1234/onboarda/pull/775) | ✅ merged + staging-validated 2026-07-16 at `8025040`; 28/28 maximum-risk outcomes correct; activation OFF | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#rsmp-tier-0c-a-hotfix-pr-775) |
| 🔴 RSMP-0C-A | Tier 0C-A — final frozen-baseline read-only replay + impact assessment | [#779](https://github.com/onboarda1234/onboarda/pull/779) | ◐ executed 2026-07-16 (read-only vs frozen `8025040`) — verdict **NOT READY** (705/802 pre-reset apps blocked; evidence draft #779) · that population was purged by the staging reset 2026-07-17 → fresh 0C-A on the canonical baseline is next | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#rsmp-tier-0c-a-final-assessment-pr-779) |
| RSMP-0C-REM | Tier 0C data/mapping remediation from the 0C-A findings | — | ◐ overtaken by the authorized staging reset 2026-07-17 (#788): blocking population purged, risk-config aligned (Manufacturing 2; D3 40/35/25) · residual scope = findings of the post-reset 0C-A rerun | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#staging-reset-2026-07-17-pr-788) |
| 🔴 RSMP-0C-B | Tier 0C-B — controlled activation + recomputation + officer review + final staging validation | — | ⬜ **pilot blocker** — unauthorized until the post-reset 0C-A concludes "Ready" | — |
| RSMP-PR1B | PR-1b — declared-PEP runtime alignment with approved Gate 0 v4 model | [#764](https://github.com/onboarda1234/onboarda/pull/764) | ✅ merged + staging-validated 2026-07-15 at `a823fb6`; activation OFF | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#rsmp-prs-751-753-755-764) |
| RSMP-FEV | Authoritative factor-level (D1–D5) computation-evidence ledger in `risk_dimensions`; fail-closed reconciliation identities; existing apps fail closed for detailed explainability until authorised recomputation | [#803](https://github.com/onboarda1234/onboarda/pull/803) | ✅ merged 2026-07-19 | — |
| RSMP-1A | Tier 1A — sector risk programme redesign (incl. 22 Lane B sector labels) | — | ⬜ post-pilot | — |
| RSMP-1B | Tier 1B — country risk programme (130 deferred countries + 19 regions) | — | ⬜ post-pilot | — |
| RSMP-2 | Tier 2 — model governance: versioning, maker-checker, effective dating, rollback, activation workflow | — | ⬜ production readiness | — |
---

## Audit 1 re-run 2026-07-21 (`3389d4eb`) — Regulatory Decision Integrity

> Source: `regmind_audit_1_regulatory_decision_integrity_20260721.md` (audited at
> `3389d4eb`). Re-audited on current `main` and CONFIRMED. Eight fail-open /
> control-bypass findings were remediated overnight **2026-07-24**, each through
> the full gate — re-audit → fix → independent adversarial review (APPROVE) →
> PR → CI → merge → staging deploy (all four deploys green). RDI-002/003 touch the
> **FROZEN** Application Review risk-staleness approval gate
> (`_application_risk_staleness_error`) and are **deferred pending Aisha Sudally
> sign-off** (Module Status & Change Control). The audit's remaining items
> (RDI-009/010/015 screening-config source-of-truth, RDI-017 SAR gating, etc.)
> are separate scope, not part of this batch.
>
> **Codex validation 2026-07-25 (round 1):** an independent Codex validation of
> the merged batch returned 5 PASS, 2 PARTIAL (RDI-006 exception mapping,
> RDI-014 memory bound) and 1 FAIL (RDI-008: a senior could direct-clear a
> CRITICAL tier-2/3 alert with notes but no documented evidence). All three
> closed by [#867](https://github.com/onboarda1234/onboarda/pull/867) (merged
> `d532956`, independently re-reviewed APPROVE).
>
> **Codex re-validation 2026-07-25 (round 2):** confirmed RDI-006 CLOSED; kept
> RDI-008 and RDI-014 PARTIAL with two reproduced defects — an approval-time
> evidence TOCTOU (a request created while an alert was non-critical could be
> approved after escalation to CRITICAL, clearing it with `has_evidence:false`)
> and a structural-violation undercount (300 dangling rows reported as
> `total_violations=202`). Both closed by
> [#870](https://github.com/onboarda1234/onboarda/pull/870) (merged `36c14d7`,
> independently re-reviewed APPROVE): approval-time
> `assert_evidence_current` recheck (409 before any mutation; request stays
> pending) + evidence now required even for the `approved_review` marker
> (closes the residual escalation race on the dismiss path); true per-class
> violation totals via uncapped COUNT(*) with `violation_totals` breakdown and
> additive coverage notes. Codex round-1's five PASS findings re-confirmed
> unregressed in round 2.

### Remediated (2026-07-24)

| ID | Title | Sev | GitHub | Status |
|----|-------|:--:|:--:|----|
| 🔴 RDI-006 | `change_management.create_change_alert` committed the materiality classification before an optional/swallowed audit → made atomic + fail-closed (refuses without actor + audit writer) | CRITICAL | [#862](https://github.com/onboarda1234/onboarda/pull/862), [#867](https://github.com/onboarda1234/onboarda/pull/867) | ✅ merged (`d377793`) + staging-deployed 2026-07-24 · Codex PARTIAL (400/500 mapping) closed by #867 |
| 🔴 RDI-001 | Unknown/typo `ENVIRONMENT` coerced to `development`, silently stripping every production guard (boot-path) → a non-empty invalid env is now fatal (`sys.exit(1)`); missing still → development | CRITICAL | [#863](https://github.com/onboarda1234/onboarda/pull/863) | ✅ merged (`436f24a`) + staging-deployed 2026-07-24 |
| 🔴 RDI-007 | `monitoring_routing` committed alert status/action + linkage before a swallowed routing audit → single atomic transaction that propagates audit failure and rolls back | CRITICAL | [#864](https://github.com/onboarda1234/onboarda/pull/864) | ✅ merged (`81d762c`) + staging-deployed 2026-07-24 |
| 🔴 RDI-004 | Supervisor `AuditLogger` advanced the in-memory hash-chain head before persistence, swallowing failures → persist-before-advance from the committed DB tail; raises `AuditPersistenceError` (fail closed) | CRITICAL | [#865](https://github.com/onboarda1234/onboarda/pull/865) | ✅ merged (`b3a3cbe`) + staging-deployed 2026-07-24 |
| 🔴 RDI-005 | Supervisor pipeline returned a successful decision-equivalent result without persistence → typed persist-or-raise; no cache/return before commit; 500 on failure; durable-store reads | CRITICAL | [#865](https://github.com/onboarda1234/onboarda/pull/865) | ✅ merged (`b3a3cbe`) + staging-deployed 2026-07-24 |
| RDI-008 | CRITICAL monitoring alert dismissable via the ordinary single-officer path (no severity gate) → fail-closed behind the M2.2 senior/four-eyes disposition (pairs RDI-007) | HIGH | [#864](https://github.com/onboarda1234/onboarda/pull/864), [#867](https://github.com/onboarda1234/onboarda/pull/867), [#870](https://github.com/onboarda1234/onboarda/pull/870) | ✅ merged (`81d762c`) + staging-deployed 2026-07-24 · Codex r1 FAIL (evidence-less senior clear) closed by #867 · Codex r2 PARTIAL (approval-time evidence TOCTOU) closed by #870: `assert_evidence_current` recheck + evidence required even for approved-review clearances |
| RDI-014 | Supervisor audit-verify API capped at 5000 rows → uncapped batched full-chain verify (`?full=true`, admin/SCO) returning root/head/count/timestamp (pairs RDI-004) | HIGH | [#865](https://github.com/onboarda1234/onboarda/pull/865), [#867](https://github.com/onboarda1234/onboarda/pull/867), [#870](https://github.com/onboarda1234/onboarda/pull/870) | ✅ merged (`b3a3cbe`) + staging-deployed 2026-07-24 · Codex r1 PARTIAL (O(n) memory) closed by #867 · Codex r2 PARTIAL (violation undercount) closed by #870: true per-class COUNT(*) totals + additive coverage notes |
| RDI-016 | `revalidate_actor_post_await` existed but was unwired → invoked after the pipeline await, before persist, aborting on mid-request revocation/demotion (pairs RDI-005) | HIGH | [#865](https://github.com/onboarda1234/onboarda/pull/865) | ✅ merged (`b3a3cbe`) + staging-deployed 2026-07-24 |

> Supervisor items (RDI-004/005/014/016) harden the Enterprise supervisor, which is
> `ENABLE_AI_SUPERVISOR`-OFF in staging/production (dev/demo only). The frozen
> memo-verdict hash-chain writer was proven behaviourally unchanged.

### Deferred — FROZEN scope (needs founder sign-off)

| ID | Title | Sev | Status |
|----|-------|:--:|----|
| 🔒 RDI-002 | Final approval allowed when the application has no `risk_config_version` (unknown risk provenance) — `_application_risk_staleness_error` | CRITICAL | ⏸ deferred — frozen Application Review approval gate; needs Aisha Sudally sign-off |
| 🔒 RDI-003 | Risk-staleness control disabled when the current risk-config version row is absent (same gate) | CRITICAL | ⏸ deferred — frozen Application Review approval gate; needs Aisha Sudally sign-off |

### Remaining findings from the same re-audit — now individually tracked (2026-07-25)

> The re-audit reported **26 findings** (7 CRITICAL, 13 HIGH, 6 MEDIUM). The ten
> above were remediated or deferred; the other sixteen were previously carried
> only as the prose phrase "separate scope". They are enumerated here so the
> register reflects the whole audit rather than the part that was actioned.
>
> **ID-space warning:** these `RDI-*` ids belong to the 2026-07-21 re-audit
> (`3389d4eb`, 26 findings). Phase 10 above uses the SAME prefix for the
> ORIGINAL Audit 1 (`c8b6dac`, 13 findings) with different meanings — e.g.
> re-audit RDI-005 is supervisor persistence, Phase-10 RDI-005 is SAR
> permanence. Ids are canonical per table and must not be merged or renumbered.

| ID | Title | Sev | Status |
|----|-------|:--:|----|
| RDI-009 | Production defaults to the legacy screening source while the same file states Sumsub is not authoritative for sanctions/PEP/watchlist/adverse-media | HIGH | 📋 scoped — screening-config source-of-truth stream (with RDI-010/024) |
| RDI-010 | Screening source selected globally from env vars; no per-application source-version pin or migration state | HIGH | 📋 scoped — same stream as RDI-009 |
| RDI-011 | Atomic `decision_records` persistence proven for the main decision endpoint; coverage for every decision-equivalent pathway is an acknowledged gap | HIGH | 📋 scoped — tracked by P10-5 (decision-record coverage + provenance) |
| RDI-012 | Per-decision-type prerequisite gates remain an open HIGH control in this register | HIGH | 📋 scoped — tracked by P10-4 (policy decision needed) |
| RDI-013 | Supervisor hash chain cannot detect deletion of the newest suffix — no external head-hash + entry-count anchor is wired | HIGH | ⬜ pending — pairs the RDI-004/014 chain work; needs an out-of-band anchor store |
| RDI-015 | With `TRUSTED_PROXY_CIDRS` unset, every private/loopback peer is trusted to supply officer sign-off IP headers | HIGH | ⬜ pending — P10-6 delivered the explicit allowlist default-OFF (#809); this is the deployed-value half |
| RDI-017 | SAR permanence not production-ready; safe only while the SAR feature flags stay disabled | HIGH | ◐ materially strengthened 2026-07-25 by item 33 ([#880](https://github.com/onboarda1234/onboarda/pull/880)): `PILOT_SCOPE` refuses every SAR route regardless of flag state, so the safety argument no longer rests on flag hygiene alone. Permanence itself still unbuilt — full closure remains an Enterprise pre-enable blocker |
| RDI-018 | Repository source cannot prove the app DB role lacks UPDATE/DELETE/TRUNCATE on legal-record tables (ENVIRONMENT-REQUIRED) | HIGH | ◐ grants pack + runbook merged ([#875](https://github.com/onboarda1234/onboarda/pull/875), [#879](https://github.com/onboarda1234/onboarda/pull/879)) — see P10-7; the RDS execution that would make this provable is an operator step ([runbook](OPS_HARDENING_RUNBOOK.md) §3) |
| RDI-019 | The free-form Claude path converts every provider/budget/programming failure to an empty string, making failure indistinguishable from a legitimate empty output | HIGH | ◐ **capability delivered, no caller opted in yet** ([#894](https://github.com/onboarda1234/onboarda/pull/894)) — `generate(raise_on_failure=True)` now raises a typed `ClaudeGenerationError` carrying `reason` (`breaker_open`/`not_initialised`/`provider_error`), and a legitimate empty completion still returns `""` without raising in either mode (incl. the degenerate empty content-list a reviewer found raising). Default stays `False`, so all 4 live monitoring callers are byte-identical — which is why this stays ◐: the ambiguity is now **fixable but not yet fixed in any live caller**. Closing it means opting those callers in (a behaviour change, needs its own review) |
| RDI-020 | Decorator-level and back-office denials are logged, but complete routing of every custom authorization denial is an acknowledged open control | HIGH | ✅ done ([#894](https://github.com/onboarda1234/onboarda/pull/894)) — denial routing moved to `on_finish()`, Tornado's **terminal hook**, so every 403 is audited however it was produced. The first cut only wrapped `self.error()` and **both reviewers independently proved that claim false** — CSRF `raise HTTPError(403)`, the enterprise-module gate's bare `set_status(403)`, the supervisor's `write_error_json(403)` and `_admin_json_error` all bypassed it. Behaviourally verified: 4 × 403 shapes → exactly one row each, no double-log when explicitly routed, no row on 200/400/401/404/500. Running post-response also makes it structurally unable to alter the response. **Scope:** covers 403 responses from `BaseHandler` subclasses; 401 authentication failures are a separate control Independently corroborated at handler level by the P9-13 deep runtime probes ([#893](https://github.com/onboarda1234/onboarda/pull/893)): the memo-ownership denial asserts a `Governance Attempt` audit row (`memo.approve.ownership_denied`), and dual control, screening second review, unmatched-IDV visibility, decision authority, senior IDV outcomes, reassignment and reciprocal tenant isolation are runtime/mutation verified. |
| RDI-021 | Officer-visible `rules_checked` metadata under-enumerates the actual pre-generation rules | MEDIUM | ⬜ pending |
| RDI-022 | Monitoring status treated as free text; DB CHECK hardening incomplete | MEDIUM | ⬜ pending — same family as P12-5/DCI-006 status lockstep |
| RDI-023 | Feature flags carry no owner, introduction version, expiry or sunset state, so permanent conditional paths are indistinguishable from temporary rollouts | MEDIUM | ✅ done ([#888](https://github.com/onboarda1234/onboarda/pull/888)) — `FLAG_LIFECYCLE` registry (owner/introduced/classification/sunset) covers all 41 governed flags incl. externally-resolved; pure metadata (resolution + `/api/config/environment` contract byte-unchanged); doc `docs/compliance/FEATURE_FLAG_LIFECYCLE.md`. Draft Claude-memo flag excluded by design — governed by the stronger H1/PC-4 guard |
| RDI-024 | The repository proves flag/provider DEFAULTS but not the live deployed values | MEDIUM | ◐ partially addressed by item 33: `PILOT_SCOPE` makes the enterprise exclusion independent of unprovable deployed flag values ([#880](https://github.com/onboarda1234/onboarda/pull/880)). The general problem (no evidence of live values) stands |
| RDI-025 | Pipeline persistence stores a summary of agent results, not the full agent evidence needed to reconstruct each output | MEDIUM | ⬜ pending |
| RDI-026 | The fallback memo claims "Full audit trail maintained" even though it is generated specifically when the AI pipeline failed | MEDIUM | ⬜ pending — wording/claim accuracy fix |

> Two of these were materially strengthened by the 2026-07-25 overnight batch
> (RDI-017 and RDI-024, both via the item-33 pilot-scope veto) and one moved
> from unprovable to operator-executable (RDI-018, via the P10-7 grants pack).
> None is claimed CLOSED: RDI-017 needs SAR permanence built, RDI-024 needs
> live-value evidence, RDI-018 needs the RDS run.

---

## Re-audit: Backend Security & Authorization (Audit 2 re-run, 2026-07-25)

> Source: `regmind_audit_2_backend_security_authorization_20260725.md` (in
> [`docs/audits/`](audits/regmind_audit_2_backend_security_authorization_20260725.md)),
> audited at pinned revision `62c629d` (after #879, before the rest of the
> overnight batch). **26 findings: 14 HIGH · 8 MEDIUM · 4 LOW.** Verdict:
> REMEDIATE BEFORE PROCEEDING (13 blocking).
>
> **⚠️ ID-SPACE WARNING — THREE distinct `BSA-*` id spaces now exist.** The
> report file uses bare `BSA-NN`; this register namespaces the new findings
> `R3-BSA-NN` to keep all three separate:
> - **bare `BSA-*`** = the ORIGINAL Audit 2 (Phase 10 rows P11-1…P11-9). e.g.
>   original BSA-015 = dependency CVEs (closed by P11-2).
> - **`R2-BSA-*`** = the 2026-07-11 consolidated re-run (`d23cc45`, the
>   "Net-new findings" + "Merged items re-flagged PARTIAL" tables above). e.g.
>   R2-BSA-001 = supervisor routes bypass BaseHandler (closed #743).
> - **`R3-BSA-*`** = THIS 2026-07-25 re-run (`62c629d`). e.g. R3-BSA-015 = log
>   PII/token leakage, R3-BSA-022 = ignored body-size cap.
>
> The three schemes collide numerically (each has a 001, a 015…) with entirely
> different meanings. Do not merge, renumber, or cite one audit's BSA-NN when
> you mean another's.
>
> **Two most consequential findings independently VERIFIED before fold-in:**
> - **R3-BSA-022 (CONFIRMED, empirically):** `max_body_size` in the
>   `Application()` constructor is silently ignored by `app.listen()` — proven
>   with a live Tornado 6.5 test (50KB body accepted against a 1KB cap; the
>   same value on `HTTPServer` rejected pre-buffer). This **reopens P12-10's
>   upload-limits ✅** (annotated above).
> - **R3-BSA-012 (CONFIRMED):** `SumsubDocumentHandler` (`server.py:30838`)
>   checks the resolved upload path with `str(requested).startswith(str(allowed_dir))`
>   — a string-prefix test a sibling dir (`.../uploads_evil/x.pdf`) passes.
>   One-line fix (`allowed_dir in requested.parents`).
>
> **Counting:** 4 findings are pure cross-references to already-open rows and
> are NOT counted (R3-BSA-002/003/004 → P11-4; R3-BSA-021 → P11-5). The other
> **22 are net-new rows** (total 215 → 237). None is remediated yet; a
> workflow-neutral remediation batch is the next step (blocking HIGHs first:
> 012, 022, 017/018, 005/006, 007, 015, 019 name-fields, 020, 011).

| ID | Sev | Finding | Status |
|----|:--:|---------|--------|
| R3-BSA-001 | MED | Production-capability modules imported under caught `ImportError` → GDPR purge / migrations / doc-verification / supervisor can silently degrade a deployed server instead of failing the deploy | ✅ done ([#894](https://github.com/onboarda1234/onboarda/pull/894)) — `enforce_capability_readiness()` emits a readiness manifest every boot and **exits 1 in staging/production** when any of the 6 soft-import capabilities is unavailable (dev/demo/testing warn only). Review confirmed the list matches every `except ImportError` guard in `server.py` exactly, and that the gate runs before `app.listen`. **Limitation (accepted):** checks import PRESENCE, not functional health — a module that imports but is degraded (e.g. no API key) still passes |
| R3-BSA-002 | HIGH | Awaited supervisor pipeline runs synchronous agents directly on the IOLoop | → **P11-4** (IOLoop offload, 📋 scoped) — re-confirmed, precise site added |
| R3-BSA-003 | HIGH | ComplyAdvantage async callback opens sync DB/network/sleep on the IOLoop | → **P11-4** — cross-ref |
| R3-BSA-004 | HIGH | Synchronous WeasyPrint rendering blocks request processing | → **P11-4** — cross-ref |
| R3-BSA-005 | HIGH | EDD routing-audit / actuation failure is swallowed; the memo transaction still commits and returns success (fail-open, same family as RDI-006/007, new site `server.py:32337-32374`) | ⬜ pending |
| R3-BSA-006 | MED | PDF audit/timestamp persistence failure is logged but the regulated PDF is still served | ⬜ pending |
| R3-BSA-007 | HIGH | Login/registration brute-force limiter is per-process, non-atomic, and fails OPEN to memory on DB errors (legacy `RateLimiter`) | ⬜ pending — move to the shared DB-backed fail-closed limiter |
| R3-BSA-008 | MED | The 21-entry permission matrix is descriptive only; no server-side `assertPermission()` exists, handlers duplicate literal role arrays → drift (structural cause of 009/010) | ⬜ pending — adjacent P9-13 |
| R3-BSA-009 | HIGH | `analyst` can transition a case into `edd_required` via the generic application PATCH despite matrix exclusion from `escalate_to_sco` | ⬜ pending — authz gap; P10-4 / P9-13 family |
| R3-BSA-010 | MED | Application-specific and supervisor audit routes allow `co`/`analyst` beyond the `view_audit_trail` admin/SCO matrix entry | ⬜ pending — cross-ref RDI-020 |
| R3-BSA-011 | HIGH | An owning **client** can invoke authoritative Agent 1 (`DocumentVerifyHandler`) and persist `verification_status="verified"` with no mandatory human acceptance; `agent_executions` logged only after commit, log failure swallowed | ⬜ pending — authority-boundary defect |
| R3-BSA-012 | HIGH | **CONFIRMED** — `SumsubDocumentHandler` path check is a string prefix (`startswith`), so a sibling dir (`uploads_evil`) passes; request-controlled `file_path` reaches Sumsub upload | ✅ done ([#894](https://github.com/onboarda1234/onboarda/pull/894)) — parent-directory test (`allowed_dir not in requested.parents`). Review exercised the shipped expression: `uploads_evil/`, `uploadsX/`, `../etc/passwd` and absolute paths all REJECTED, legit nested paths accepted; `.resolve()` normalises `..` and follows symlinks before the check, and the validated path is what reaches the upload. **No residual bypass found** |
| R3-BSA-013 | LOW | Root `/` uses Tornado's built-in `RedirectHandler`, which does not inherit BaseHandler security headers | ✅ done ([#894](https://github.com/onboarda1234/onboarda/pull/894)) — new `SecureRootRedirectHandler` carries the same 7-header posture as `SecureStaticFileHandler`. Verified in a **real Tornado 6.5 HTTP harness** (not by inspection): the 301 to `/portal` actually carries every header |
| R3-BSA-014 | MED | `DocumentDownloadHandler` uses stored `mime_type` and permits `?view=inline` (incl. PDF/images) — **qualifies P11-7's attachment ✅** | ⬜ pending — P11-7 closure incomplete |
| R3-BSA-015 | HIGH | Logs can carry PII, raw AI text, provider identifiers, and — on OpenCorporates connection exceptions — a token-bearing URL | ✅ done ([#894](https://github.com/onboarda1234/onboarda/pull/894)) — OpenCorporates / IP-geo / all four Sumsub request-loop sites now route through `sanitize_provider_error`, and the legacy Sumsub adapter is scrubbed for both its log AND its officer-visible `error` field (applicant-id PII). **Review found the first cut still leaked**: the IP-geo client builds a BARE `?key=`, and connection/timeout exceptions carry a RELATIVE url the `https://` collapse never touched — `_SECRET_PATTERNS` now covers bare `key=` (reproduced leaking, then fixed; `monkey=`-style false positives checked). Guard pins EVERY provider log site after a reviewer proved a single-site revert stayed green |
| R3-BSA-016 | LOW | Invalid Sumsub webhook signature returns 401 (audit wanted an indistinguishable 200) — **qualifies P11-7's webhook ✅** | ✅ closed as a **recorded design decision** ([#894](https://github.com/onboarda1234/onboarda/pull/894), no behaviour change) — 401 is retained deliberately: it is correct HMAC-failure semantics, leaks nothing actionable (the endpoint is already public in the Sumsub dashboard config), and preserves Sumsub's retry on non-2xx. A masking 200 would silently black-hole a legitimate misconfigured delivery. Rationale recorded at the call site |
| R3-BSA-017 | HIGH | Sumsub idempotency: EVERY insert exception is treated as a uniqueness collision, so DB outages/schema errors are falsely acknowledged as duplicates | ✅ done ([#894](https://github.com/onboarda1234/onboarda/pull/894)) — only a UNIQUE violation returns `already_processed`; any other insert error re-raises. Review mapped the full try/except nesting and confirmed **end-to-end**: the enclosing construct is a bare `try/finally` (no `except`), so the raise reaches Tornado → **HTTP 500 → Sumsub retries**, instead of a DB outage being falsely ACKed and the event lost forever. Classifier battery (3 duplicate / 8 infrastructure messages) showed zero misclassification |
| R3-BSA-018 | HIGH | Sumsub per-application update errors are swallowed, then the idempotency row commits — a retry can never repair the failed application | ⬜ pending |
| R3-BSA-019 | HIGH | Prompt fencing defaults OFF (known founder call), AND `entity_name`/`person_name` are interpolated into `context_hint` UNSANITIZED even when fencing is ON (`claude_client.py:1797-1802`) — the second half is a new code defect | ◐ **net-new half ✅ done** ([#894](https://github.com/onboarda1234/onboarda/pull/894)) — both names now pass through `_sanitize_for_prompt` when fencing is ON, exactly like `doc_type`/`file_name`; fencing-OFF output verified byte-identical. **Open half unchanged:** fencing still defaults OFF (P11-5 founder call). Review also flagged a design asymmetry worth a decision — `verify_document` sanitises names UNCONDITIONALLY, so in the default config `extract_document_fields` alone still interpolates raw names; making it unconditional would close that surface but changes the default prompt, so it is NOT in this no-workflow-change batch |
| R3-BSA-020 | HIGH | `extract_document_fields` has NO Pydantic schema registered in `_AGENT_SCHEMAS`; any parsed dict is fed into deterministic name/registration/date checks | ⬜ pending — sub-defect of P11-5's "output schema" claim |
| R3-BSA-021 | MED | AI circuit breaker defaults off and is process-local (no service-wide breaker unless the flag is set) | → **P11-5** (◐, activation is a founder call) — cross-ref |
| R3-BSA-022 | HIGH | **CONFIRMED empirically** — pre-buffer body cap set on `Application()` (ignored by Tornado); real cap is the ~100MB default; per-route caps (10/20/25MB) diverge and run post-buffer | ⬜ pending — pass `max_body_size` to `listen`/`HTTPServer`, one canonical policy; **reopens P12-10** |
| R3-BSA-023 | MED | Supervisor `_pipeline_cache` is process-local — review-package generation/submission lost on restart, inconsistent across workers | ◐ materially mitigated: the supervisor is `PILOT_SCOPE`-vetoed in staging/production since #880 (post-audit); residual is the cache design for any enterprise enablement |
| R3-BSA-024 | LOW | `aiosqlite` and `gunicorn` are direct prod pins but never imported (entrypoint is `python server.py`) | ✅ done ([#888](https://github.com/onboarda1234/onboarda/pull/888)) — dropped both pins + regenerated both hash-pinned locks (`packaging` orphan removed from the runtime lock); guard `test_unused_runtime_deps_stay_removed` blocks re-introduction into requirements.txt or the runtime lock |
| R3-BSA-025 | LOW | WeasyPrint 68.1 `CVE-2026-49452` — the #888 closure allowlisted it in CI on a **false "no fixed release" premise**; **Codex 2026-07-26** found WeasyPrint **69.0 (2026-06-02) is the upstream fix** | ✅ done ([#891](https://github.com/onboarda1234/onboarda/pull/891)) — **upgraded WeasyPrint 68.1→69.0** (the upstream fix), regenerated both hash-pinned locks, and **removed the CI `--ignore-vuln` allowlist** (pip-audit now clears the CVE, verified). Guards: pin `>=69.0` in requirements.txt **and** the lock (blocks a downgrade back into the vulnerable range), CI carries no allowlist for it, and `presentational_hints` stays unused (defense-in-depth). PDF suite + a live render smoke pass on 69.0. Supersedes the #888 allowlist-bounding (correct closure premise) |
| R3-BSA-026 | MED | Stale deps: `webencodings` (2017), `distro` (2023) exceed the 18-month threshold; `anthropic==0.49.0` far behind current SDK | ◐ risk-accepted (documented, [#888](https://github.com/onboarda1234/onboarda/pull/888)) — `webencodings`/`distro` are already the LATEST upstream (unmaintained, transitive-only) so a bump is a no-op; `anthropic` SDK bump deferred as higher-risk (document-verification path, not hygiene). Compensating control: CI `pip-audit` + hash-pinned locks. Open half = the deferred `anthropic` upgrade |

---

## Review-found items (independent adversarial review, 2026-07-26)

Raised by the two-reviewer gate rather than by a numbered audit. Both are real
defects found while verifying other work; neither is fixed here.

| ID | Sev | Finding | Status |
|----|:---:|---------|--------|
| R3-OPS-001 | MED | Sibling alarm-provisioning scripts still create alarms that page nobody: `provision_screening_queue_p95_alarm.py --apply` accepts an empty action, and `provision_pr6_observability.py --apply` **auto-creates a brand-new SNS topic with zero subscribers** — alarms then carry a non-empty `AlarmActions` and look wired while paging no one, which is precisely the failure mode P9-10's guard exists to kill. Also: the P9-10 guard checks ARN *presence*, not deliverability | ⬜ pending — extend the `apply_refusal()` pattern to both siblings; consider an SNS subscription pre-check under `--apply` |
| R3-APP-001 | LOW | Nulling `prescreening_data` on the approval path yields `400 "Approval blocked: Internal validation error: 'NoneType' object has no attribute 'get'"` — an unhandled `AttributeError` leaking an internal trace into an operator-facing message | ⬜ pending — **frozen Application Review decision path; needs founder approval before any change.** Cosmetic/robustness only; the approval is correctly blocked |

Operational note (2026-07-17): the controlled staging reset removed 944
founder-confirmed synthetic applications and aligned Manufacturing to 2 and D3
to 40/35/25. The canonical dataset remains unseeded because its PostgreSQL
dry-run exposed a type mismatch; RSMP remains OFF. See the
[staging reset closure record](pilot/STAGING_RESET_CLOSURE_2026-07-17.md).

---

## Release-found items (PR #928 verification-check retirement, 2026-08-04)

Found while verifying and validating the DOC-MA-01 / DOC-13 / DOC-68
retirement ([#928](https://github.com/onboarda1234/onboarda/pull/928), merged
`b1e1596`, deployed to staging). None was caused by that change and none
blocked the release — all three are pre-existing. Left untouched deliberately
to keep the release scoped; recorded here so the next audit does not
re-litigate them. Excluded from the 2026-07-26 roll-up (not recomputed).

| ID | Sev | Finding | Status |
|----|:---:|---------|--------|
| R928-001 | LOW | Stale `ai_checks` rows on staging: the config surface reports **89** active checks while the merged seed produces **85**. `sync_ai_checks_from_seed()` (`db.py`) upserts but never deletes, so rows for doc types dropped from `verification_matrix.py` in an earlier refactor persist indefinitely. Harmless today — live doc types are overwritten on every boot — but the count is misleading and the drift only grows | ⬜ pending — decide whether the sync should prune doc types absent from the seed, or whether the surface should count only seeded rows. Needs care: pruning must not orphan historical `documents.verification_results` |
| R928-002 | LOW | Dead `AGENT1_DOCUMENT_POLICIES` array in `arie-backoffice.html` (~lines 33588–33669): assigned with `agent1Policy(...)` entries, then **unconditionally reassigned in full** at 33740 by the `PR-DOC-POLICY-CANONICAL-1` canonical block, with no read of the variable in between. ~80 lines that cannot reach the DOM under any code path. Still carries retired check labels (`'account standing'` at 33606 and 33613, `'business objects / activities match'`), which surface in DOM scans and read as live config | ⬜ pending — delete the superseded array. Verify no read exists between the two assignments before removing; the canonical block at 33740 is the live fallback and must stay |
| R928-003 | LOW | Section F (Internal Controls) renders each control block **twice** per requirement. `renderUnifiedInternalControls()` calls `enhancedRequirementInternalControlHtml(req)` directly, and then `renderApplicationEnhancedRequirementActions(req)` calls it again as `typedContentHtml` inside its `<details>` panel — both the editable and read-only branches. Operators see the same "Internal control / summary / resolve button" card duplicated above and inside the Expand panel. Cosmetic only; status updates and the resolve button work correctly | ✅ fixed — `renderApplicationEnhancedRequirementActions` takes an opt-in `suppressTypedContent` option; only Section F passes it, so the evidence and portal-disclosure call sites are unchanged. Static guard added in `test_enhanced_requirement_settings.py` |

### Section F internal controls — retirement of the two PEP controls (2026-08-04)

Founder decision after confirming the automated controls are live
(`SCREENING_PROVIDER=complyadvantage`). Two of the three Section F controls are
superseded and retired; the third is deliberately **kept**.

| Requirement | Decision | Rationale |
|---|---|---|
| `pep_adverse_media_assessment` | ⛔ retired | Adverse media is screened per party by ComplyAdvantage (directors + UBOs), with per-party status surfaced in the Directors & UBOs report |
| `pep_enhanced_monitoring_flag` | ⛔ retired | Superseded by the risk-based periodic review cadence (`RISK_FREQUENCY_MONTHS`: LOW 36 / MEDIUM 24 / HIGH 12 / VERY_HIGH 6, with a 12-month floor for EDD lanes) |
| `jurisdiction_risk_assessment` | ✅ **kept** | Unlike the other two this is `blocking_approval: True` / `mandatory: True` — a hard approval gate on high-risk-jurisdiction cases. Country risk feeding the risk *score* is not a substitute for the officer sign-off *gate*. Retiring it would be a compliance-posture change and was explicitly declined |

Because `jurisdiction_risk_assessment` survives, **Section F remains** and
Verification History stays as Section G. Retirement uses the established
`REMOVED_ACTIVE_ENHANCED_REQUIREMENT_KEYS` mechanism, so persisted rules
deactivate on startup with an `enhanced_requirement_rules.taxonomy_reconciled`
audit entry, while historical generated requirements are retained for audit and
render the "Disabled source rule; historical requirement retained for audit"
marker.

#### ⚠️ Accepted risk — declared-but-unmatched PEPs lose enhanced monitoring (founder sign-off 2026-08-04)

`pep_enhanced_monitoring_flag` was retired on the basis that enhanced monitoring
is already driven by the risk-based periodic review cadence. That holds for
**provider-matched** PEPs but **not** for **client-declared, screening-unmatched**
PEPs. The two code paths key off different signals:

| Path | Reads |
|---|---|
| Retired rule — `_declared_pep_present()` (`enhanced_requirements.py`) | SQL against `directors.is_pep` / `ubos.is_pep` / `pep_declaration` |
| Replacement — `has_pep_signal()` (`periodic_review_policy.py`) | Text scan of six **application-level** fields; never reads the party tables |

Verified end-to-end, not inferred:

* The portal submits `directors` and `ubos` as **siblings of** `prescreening_data`, not inside it. Extracting the submitted `prescreening_data` object confirms `PEP-ish keys: NONE`, `contains 'directors': False`, `contains 'ubos': False`.
* Of the six fields `has_pep_signal()` scans, only `prescreening_data` and `decision_notes` exist as `applications` columns; `risk_escalations`, `elevation_reason_text`, `screening_summary` and `form_data` are not application columns, so those lookups are dead.
* Behaviour test: PEP flagged only on the director record → **24-month cadence, no enhanced monitoring**. PEP wording reaching the application fields (as happens when screening matches, because the screening report is stored inside `prescreening_data`) → 12-month floor, `enhanced_monitoring_floor:pep_exposure`.

**Exposed population:** MEDIUM-risk (or lower) relationships with a
client-declared PEP director or UBO whose screening returns no match. These now
sit on the standard cadence with no enhanced-monitoring flag and no Section F
control. No approval gate is affected — both retired rules were
`blocking_approval=0, mandatory=0`.

Accepted knowingly to keep this release scoped. The remediation is scoped as
**R929-001** below and should be closed before production onboarding of real
PEP relationships.

### R929-001 — make enhanced-monitoring PEP detection read the party tables (scoped, not implemented)

| Field | Value |
|---|---|
| Severity | MED — compliance coverage gap, no approval-gate impact |
| Trigger | Accepted risk recorded above |
| Goal | `has_pep_signal()` must detect a declared PEP with the same fidelity as `_declared_pep_present()`, so the 12-month enhanced-monitoring floor fires for client-declared PEPs regardless of screening outcome |

**Constraint that shapes the design:** `periodic_review_policy.py` is currently
pure — `has_pep_signal(app)`, `enhanced_monitoring_reasons(...)` and
`policy_snapshot_for_application(app, *, anchor_date)` all operate on a plain
dict with no database handle. Threading a `db` argument through would change
every caller and couple the policy module to persistence. The likely-correct
shape is instead to **enrich the app dict upstream** with a resolved
`declared_pep` boolean (computed once, where a `db` is already in hand) and have
`has_pep_signal()` honour it, keeping the policy module pure and unit-testable.

**Work items:**

1. Add a shared declared-PEP resolver so `enhanced_requirements._declared_pep_present()` and the monitoring path cannot drift again. Watch for a circular import between `enhanced_requirements` and `periodic_review_policy`.
2. Enrich the application dict with the resolved flag at every site that builds a policy snapshot — `periodic_review_engine`, `monitoring_automation.run_due_monitoring_reviews`, and the baseline write in `periodic_review_engine` (`periodic_review_baseline_*` columns).
3. Decide the **backfill** question explicitly: already-approved relationships carry a persisted `periodic_review_baseline_cadence_months` and a computed `periodic_review_next_review_due`. Fixing detection does not retro-correct them. Either recompute baselines for affected applications or record why not.
4. Prune or fix the four dead field lookups in `has_pep_signal()` — they read as coverage and provide none.
5. Fix the narrower bug found while scoping: `prescreening_data = {"pep_declaration": "Yes"}` does **not** trigger detection, because `pep_declaration` recurses into `_contains_pep_exposure` and a bare `"Yes"` string fails `_pep_text_positive`. A declaration key with a positive flag value should count.
6. Tests: a regression test asserting a PEP on the party tables alone yields a 12-month cadence; the two-path parity test (`_declared_pep_present` vs `has_pep_signal` agree on the same application); and the `pep_declaration: "Yes"` case from item 5.
7. Re-verify on staging that applications with `directors.is_pep='Yes'` carry `pep_exposure` in their periodic review `enhanced_monitoring_reasons`.

**Out of scope:** reinstating `pep_enhanced_monitoring_flag`. The fix targets the
automated control, not the retired manual task.

### R929-002 — `container-security.yml` required check cannot complete on a same-repo PR branch

| Field | Value |
|---|---|
| Severity | MED — a required security check that structurally cannot pass |
| Found | PR #937 release, 2026-08-06 |

`.github/workflows/container-security.yml` triggers on **both** `pull_request`
and `push`. A push to a same-repo PR branch fires both events, producing two
competing runs of `exact-sha-container-scan` for the same commit. The workflow's
`concurrency` group is
`container-security-${{ github.event.pull_request.number || github.ref }}` with
`cancel-in-progress: true`, so the two runs resolve to *different* group keys and
both compete for runners; under the runner contention this repo already
documents, each sits queued and is then cancelled.

Observed on PR #937: the check was cancelled on three consecutive attempts, each
after ~15 minutes queued, and never produced a verdict. The workflow's own
comment states the 30-minute timeout exists so *"a stalled scan cannot leave a
PR's required check pending"*, i.e. it is treated as required — so the failure
mode is a required security gate that can never go green on a same-repo branch.
PR #937 was merged with this check red; the image was still scanned, because
`deploy-staging.yml` independently runs `Gate exact image vulnerability findings`
and `Gate exact image OS and Python findings` against the exact pushed digest
(both passed). The scan also completed successfully on `main` post-merge in 1m 4s
(Container Security run #213), which corroborates that only the PR-branch path
is affected.

⬜ pending — drop the `push:` trigger (the `pull_request` event already covers
PR branches), or key the concurrency group so the two events cannot collide.
Verify afterwards that a same-repo PR branch produces exactly one run of this
check.

### R929-003 — CI failure signal is ambiguous, and stuck runs cannot be cleared by re-running

| Field | Value |
|---|---|
| Severity | LOW-MED — operational; cost several hours on the #937 release |
| Found | PR #937 release, 2026-08-06/07 |

Two related operability problems, both observed repeatedly on one release:

**(a) Infrastructure failure and genuine regression are indistinguishable at the
PR level.** A GitHub action-download outage (`Failed to resolve action download
info. Error: Service Unavailable`), runner starvation, and a real test failure
all surface identically as a red `protected-module-regression`. Telling them
apart requires opening the job log and checking whether the failure timestamp
precedes checkout. On a compliance platform where a red protected-module check
is meant to be a hard stop, that ambiguity erodes the signal — the risk is
habituation to red boards.

**(b) Re-running a stuck queued run does not recover it; a fresh dispatch does.**
When a job fails to acquire a runner (`runner_id: 0`, empty runner name, ~15 min
queued, then cancelled), `rerun_workflow_run` and `rerun_failed_jobs` re-enter
the same stuck slot and fail the same way. On #937 four re-runs achieved nothing
over several hours; a single `workflow_dispatch` on `deploy-staging.yml` started
immediately and completed successfully. The stuck run additionally could not be
cancelled — the API returns `409 Cannot cancel a workflow re-run that has not yet
queued` — so it cannot be cleaned up either.

Note the deploy pipeline behaved *correctly* throughout: `deploy` is gated
`needs: ci`, so when `docker-validate` was cancelled without acquiring a runner,
the deploy was skipped rather than shipping an unverified build. Fail-closed is
the right posture; the defect is the signal quality and the recovery path, not
the gating.

⬜ pending — (a) surface pre-checkout/infra failures distinctly from test
failures, e.g. a setup-phase guard step that annotates the run, so a red board
means "tests failed" and nothing else; (b) record in the release runbook that a
stuck queued run must be recovered with `workflow_dispatch`, not `rerun_*`.

---

Section F itself is **functioning as designed** and is retained: it tracks
`internal_control` requirement types that expect no document upload. Following
the 2026-08-04 retirement below its only remaining occupant is
`jurisdiction_risk_assessment`, so Section F now renders on high-risk-jurisdiction
cases rather than on every PEP case. Backed by `_internal_control_summary()` in
`enhanced_requirements.py`, deliberately excluded from portal-visible
requirement queries, with a working status/notes save path and a resolve button
that routes to the relevant tab.

---

## Optional / Post-Production Modernization (NOT required for pilot or first production cut; excluded from roll-up)

> Elective architecture/scale/enterprise upgrades for after production launch.
> Risk column = impact of the change itself on running workflows: 🟢 additive/safe ·
> 🟡 modifies live path (guardable) · 🔴 modifies live path (intrinsic).
> Cleared? column: ✅ done · 🟡 partial · 🟢 already on the remediation list · — not started.

### 1. Monolithic `server.py` decomposition

| # | Step | Impl. risk | Cleared? |
|---|------|:--:|:--:|
| 1.1 | Characterization/contract tests before any move | 🟢 | — |
| 1.2 | Extract handlers into `handlers/<domain>.py` (strangler) | 🟡 | 🟡 partial — `auth.py`, `base_handler.py` extracted |
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
| 2.5 | Component + Playwright E2E tests | 🟢 | — |
| 2.6 | Migrate client portal (later) | 🟡 | — |

### 4. SQLite / PostgreSQL dual support

| # | Step | Impl. risk | Cleared? |
|---|------|:--:|:--:|
| 4.1 | Run migrations against real PostgreSQL in CI | 🟢 | ✅ done — CI runs full suite on fresh PG (`ci.yml`) |
| 4.2 | Migration round-trip / idempotency tests | 🟢 | ✅ largely done — `tests/test_migration_*` |
| 4.3 | Make SQLite dev-only (decision + docs) | 🟡 | — |
| 4.4 | Forward-migration safety policy + docs | 🟢 | 🟡 partial — `scripts/check_schema_migration_policy.py` PR gate |
| 4.5 | Pre-deploy migration gate in deploy workflow | 🔴 | — |

### 5a. IaC & autoscaling

| # | Step | Impl. risk | Cleared? |
|---|------|:--:|:--:|
| 5a.1 | Codify ECS/RDS/Secrets/ALB in Terraform (import) | 🔴 | — (overlaps P9-4) |
| 5a.2 | ECS desired count ≥ 2 across AZs | 🟡 | ✅ appears satisfied — 2 healthy ALB targets (staging) |
| 5a.3 | ECS Service Auto Scaling policies | 🟡 | — |
| 5a.4 | Confirm uploads→S3 / no SQLite in prod | 🔴 | ✅ largely done — S3 path present; `DATABASE_URL` required in prod (#673) |

### 5b. HA / DR

| # | Step | Impl. risk | Cleared? |
|---|------|:--:|:--:|
| 5b.1 | RDS Multi-AZ + backups + PITR | 🟡 | ✅ done on staging; prod RDS not yet provisioned |
| 5b.2 | DR runbook + restore drill | 🟢 | 🟢 on list — P9-8 |
| 5b.3 | Deploy rollback automation + circuit breaker | 🔴 | 🟡 partial — runbook done (#678); automation pending |
| 5b.4 | Provision production env via IaC | 🔴 | 🟢 on list — P9-4 |

### 5c. Enterprise identity & compliance

| # | Step | Impl. risk | Cleared? |
|---|------|:--:|:--:|
| 5c.1 | SSO (SAML 2.0 / OIDC) for officers | 🔴 | — |
| 5c.2 | MFA / TOTP for officer logins | 🟡→🔴 | — |
| 5c.3 | RBAC formalization | 🔴 | 🟡 overlaps P9-13 |
| 5c.4 | SOC 2 / ISO 27001 readiness | 🟢 | — |

---

## Roll-up — computed by counting rows, 2026-07-26 (Audit-2 re-run fold-in)

Counting rule: every row in Phases 0–14 + the Re-audit/RSMP tables counts once.
The cross-reference rows (Phase 7 audit-log-tamper-evidence-1, Phase 7
APP-CONF-003, Phase 13 CA row, and the four R3-BSA IOLoop/breaker cross-refs
002/003/004/021 that point at P11-4/P11-5) and the Optional Modernization
tables are excluded. ◐ = items with one named half done and one open.
This count unions the #780 stream with the staged batches (#808–#815,
#833–#837, #862–#870, #875–#886).

**Audit-2 re-run fold-in 2026-07-26: +22 net-new rows (215 → 237).** The
2026-07-25 Backend Security & Authorization re-run (`62c629d`) reported 26
findings; 4 are pure cross-refs to already-open rows (R3-BSA-002/003/004 →
P11-4; R3-BSA-021 → P11-5) and the other 22 are enumerated as `R3-BSA-*` rows
under "Re-audit: Backend Security & Authorization". Two were independently
verified before fold-in: **R3-BSA-022 CONFIRMED empirically** (the `Application()`
`max_body_size` is ignored by Tornado — reopens P12-10's upload-limits ✅,
which moves ✅→◐) and **R3-BSA-012 CONFIRMED** (string-prefix path check).
None of the 22 is remediated yet.

**Prior net-new (2026-07-21 RDI re-audit): +16.** That re-audit reported 26
findings; 10 were already rows, the other 16 are enumerated under "Remaining
findings from the same re-audit". Total 199 → 215 → 237.

Overnight batch 2026-07-25 (PRs #875–#884, all through fix → adversarial
review → PR → green CI → merge → staging deploy):
* ✅ item 33 (#880), P12-4 and P12-10 (#875/#877), P13-3 (#883), APP-CONF-003 (#881), P13-5 (#884 — race-elimination, founder-accepted 2026-07-26)
* ◐ P9-13 (#881 — cross-client half closed; runtime coverage deliberately
  under-claimed after review mutation-tested it), P9-8 / P9-10 / staging-SHA
  test-login half (#882 — repo halves merged, AWS/DB execution outstanding),
  P10-7 (#879 — grants pack completed after
  Codex found the maintenance role could not execute the sanctioned purge)
* Three re-audit findings materially strengthened without being closed:
  RDI-017 and RDI-024 (item 33's veto) and RDI-018 (P10-7 grants).

Two review REJECTs in this batch were substantive, not cosmetic: a
`--delete-unused` path that would have orphaned memo/SAR/EDD attribution (flag
removed), and two P9-13 tests that passed with the gate they named deleted
(one fixed, one deleted as tautological).

**Codex validation 2026-07-26 (of the merged batch): overall FAIL → remediated
same day by [#886](https://github.com/onboarda1234/onboarda/pull/886).**
Verdicts: #880 PASS · #881 PASS · #882(c) PASS · #879 PARTIAL (grants
sufficient but overprivileged → minimised) · #882(a) FAIL (identity guard
bypassable → positive host allowlist) · #882(b) PARTIAL (DR teardown race →
waiters) · #883 PARTIAL (valid-JSON `null` error body lost status →
normalized) · #884 PARTIAL by design (race-elimination, not literal
fail-closed — Codex concurs the literal inversion is a founder
risk-acceptance decision; **accepted by the founder 2026-07-26, closing
P13-5**) · #885 register verdicts
upheld except the P13-3 row, corrected here. Codex's hold on the runbook §5/§6
operator steps lifted when #886 merged.

Hygiene batch 2026-07-26 ([#888](https://github.com/onboarda1234/onboarda/pull/888),
through fix → independent adversarial review → green CI → merge → staging deploy):
* ✅ **R3-BSA-024** (unused `gunicorn`/`aiosqlite` pins dropped + locks regenerated),
  **R3-BSA-025** (WeasyPrint CVE allowlist bounded by mitigation + dated-expiry guards — later corrected: see below),
  **RDI-023** (feature-flag lifecycle registry over all 41 governed flags)
* ◐ **R3-BSA-026** risk-accepted: the two dormant leaf deps are already latest upstream;
  the `anthropic` SDK bump is deferred (higher-risk, document-verification path)
* Reviews caught and fixed real gaps before merge: a non-recursive WeasyPrint scan
  and literal-only match (both broadened), a whole second population of
  externally-resolved flags omitted from the registry (added), and — via the full
  suite — an H1/PC-4 collision where registering the draft Claude-memo flag put its
  name on a config surface (excluded; that flag stays governed by the stronger H1 guard)

**R3-BSA-025 correction 2026-07-26 (Codex validation of #888): FAIL → remediated.**
Codex found the #888 closure rested on a false premise — the CI comment claimed
"no fixed WeasyPrint release" but **69.0 (2026-06-02) is the upstream fix for
CVE-2026-49452**. Bounding an allowlist was the wrong remedy. Corrected by
upgrading WeasyPrint 68.1→69.0, regenerating both locks, and removing the CI
`--ignore-vuln` exception (pip-audit clears the CVE; PDF suite + live render pass
on 69.0). Guards replaced: a `>=69.0` pin check (blocks downgrade) + a
no-allowlist assertion, keeping the `presentational_hints` mitigation as
defense-in-depth. The other three #888 items (R3-BSA-024, RDI-023, R3-BSA-026)
Codex validated as accurately recorded.

Low-risk batch 2026-07-26 (PR #894 — fix → **two independent adversarial
reviewers** → green CI → merge → staging deploy):
* ✅ **R3-BSA-012** (path-traversal parent-dir test), **R3-BSA-013** (root-redirect
  security headers), **R3-BSA-015** (provider-error scrubbing), **R3-BSA-017**
  (idempotency: only UNIQUE = duplicate), **R3-BSA-001** (deployed-env capability
  gate), **RDI-020** (403 denial routing via on_finish), **R3-BSA-016** (401
  retained as a recorded design decision)
* ◐ **R3-BSA-019** net-new sanitisation half done (fencing-default-off stays a
  P11-5 founder call) · **RDI-019** typed-failure capability delivered but no live
  caller opts in yet · **APP-CONF-005** CSP console output is the PR-22a measuring
  policy by design; the Firefox lint is deferred for want of a repro
* The two-reviewer gate earned its keep: they **converged independently** on the
  RDI-020 "every 403" claim being false (four real bypasses → re-architected onto
  Tornado's terminal hook), and one **reproduced a live secret leak** the first cut
  had missed — the IP-geo client's bare `?key=` survived sanitisation in
  relative-URL exceptions. Three guards were also shown to be test-theater
  (green against their own reverted fix) and were rewritten to execute the
  shipped code.

| Status | Count |
|--------|:--:|
| ✅ done/merged | 145 |
| ◐ split — one half open | 24 |
| 🟢 PR open | 0 |
| 🔨 in progress | 3 |
| 📋 scoped | 17 |
| ⏸ blocked | 7 |
| ⬜ pending | 43 |
| **Total tracked items** | **239** |
