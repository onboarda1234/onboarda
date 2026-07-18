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

**Reconciled:** 2026-07-17 against live GitHub · `main` = `97ae6f8` · scope of this reconcile: row union of the Applications-module confirmation-audit stream with the Phase 5 screening rows merged through #783/#786; full-register reconcile remains in [#780](https://github.com/onboarda1234/onboarda/pull/780)
**Pilot:** all 4 code blockers ✅ closed · remaining pilot work = **RSMP Tier 0C** + the open 🟠 gates below · Applications module: unconditional **PILOT-READY** (confirmation audit, 2026-07-16)
**Production:** blocked — Audit-3 verdict REMEDIATE BEFORE PROCEEDING; Phase 14 largely open. Nothing in this file is a production-readiness claim.
**Open PRs:** [#785](https://github.com/onboarda1234/onboarda/pull/785) (Applications tab-preserving refresh + register row union) · [#780](https://github.com/onboarda1234/onboarda/pull/780) (docs — full register reconcile; supersedes drafts #777/#767/#752) · [#779](https://github.com/onboarda1234/onboarda/pull/779) (draft — RSMP Tier 0C-A evidence pack, verdict NOT READY) · [#737](https://github.com/onboarda1234/onboarda/pull/737) (draft — P12-1 Phase A discovery report)

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
| RSMP Tier 0C — activation + recomputation | Re-audit → RSMP | ⬜ last remaining pilot code workstream |
| item 33 — pilot-scope guards (server-side) | Phase 13 | ⬜ |
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
| 26 | Shared fail-closed rate limiter (= **BSA-002**) — 🔴 blocker, closed; re-run partial R2-BSA-016 open | HIGH | [#728](https://github.com/onboarda1234/onboarda/pull/728) | ✅ merged + validated 2026-07-09 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#item-26-pr-728) |
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
| — | Phase 6 closeout: module card, ops runbook, CLAUDE.md change-control entry, rts-1.0 methodology | — | — | 🟢 PR open 2026-07-18 — queue verdict VALIDATED/CHANGE-CONTROLLED; end-to-end workflow verdict stays gated on SRP-3 | — |
| — | Ops tickets (Phase 6): CloudWatch p95 alarm on /api/screening/queue · PG-backed test lane for seed/ops tooling | — | — | ⬜ recorded — not yet created | — |

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
| SRP-2a / RESCREEN-1 | Re-screen of an already-screened subject errors Mesh customer-creation (external identifier already assigned) — batch-1 finding; hits every future officer/periodic re-screen | P1 | — | ◐ distinct fail-closed classification shipped (customer_identifier_conflict degraded source); existing-customer re-screen wiring ⏸ pending Mesh endpoint confirmation (docs blocked from this env; Codex/CA to confirm) | — |
| SRP-2b / RISK-FC-1 | Risk recompute lowered HIGH→LOW off a non-terminal/degraded screening report (TESCO 55→12.3) — fail-open | P1 | — | ✅ fixed 2026-07-17 — recompute_risk holds prior risk when a non-terminal report would lower it; raises still allowed; audited | — |
| SRP-3 | Review-page triage IA: summary strip, score-ranked hits with factor bands (profile-UUID dedup ruled out — Mesh recon 2026-07-17: profiles are minted per case, not stable entity keys), risk-type buckets, side-by-side disambiguation, progressive disclosure | — | — | ⬜ enriched dataset now via ONE fresh staging app with a well-known matching name (first-time screen avoids RESCREEN-1); API-vs-dashboard score cross-check rides along | — |
| SRP-4 | Agent 3 → triage narrative ("review these N first, here's why"), advisory-only, never mutates dispositions | — | — | ⬜ after SRP-3 | — |
| SRP-5 | Provider-side noise reduction for entity searches — validate against **Mesh** docs (Manus cited legacy API); sanctions/PEP-1 recall must not decrease | — | — | ⏸ blocked on SRP-1 answers · deliberately last | — |

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
| 🟠 ops-enforce-staging-sha-alignment-gate | Staging-SHA gate + delete test logins | P0 | [#702](https://github.com/onboarda1234/onboarda/pull/702) | ◐ code ✅ (SW-3) · delete-test-logins ⬜ ops · 🟠 gate open | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#wave-a-prs-700-703) |
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
| APP-AUD-gov-dup-1 | Duplicate audit rows from two accepted governance requests (idempotency) | Low | — | ⬜ pending | — |
| APP-AUD-005 | `/api/applications` ignores `search=` (UI uses `q=`) — document or alias | Low | — | ⬜ pending | — |
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
| APP-CONF-001 | Analyst RMI/Escalate UI/authz mismatch — matrix + UI aligned to decision-endpoint authority; contract tests pin all three surfaces | P1 | [#782](https://github.com/onboarda1234/onboarda/pull/782) | ✅ merged + revalidated 2026-07-16 (33/33 Chromium/Firefox/WebKit; analyst API 403) | — |
| APP-CONF-002 | Retained synthetic records visible in normal staging list | P2 | — | ✅ closed 2026-07-16 — all 3 records fixture-marked (approved); full normal-list sweep clean (247 rows, 0 synthetic visible) | — |
| APP-CONF-003 | Role-harness cross-client probe not actually cross-client — *(cross-ref: = P9-13 open half "cross-client seed fix" — not counted)* | P2 | — | ⬜ see P9-13 | — |
| APP-CONF-004 | Largest-case detail-open p95 2.105s > 2s prod target — round-2 detail optimisations (dedupe gate recompute, batch name resolution, single monitoring load) + p95 monitor; frozen-scope approval required | P2 | — | 📋 scoped | — |
| APP-CONF-005 | Firefox unreachable-code warning + report-only CSP console diagnostics not production-clean (CSP enforcement relates to item 22) | P2 | — | ⬜ pending | — |
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
| P10-6 | Sign-off IP attribution (RDI-012) — re-run partial RDI-107 open | HIGH | [#708](https://github.com/onboarda1234/onboarda/pull/708) | ✅ merged + validated | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#overnight-batch-prs-705-708) |
| P10-7 | Append-only audit at DB level (RDI-013 non-SAR) | MEDIUM | — | 📋 scoped — grants half is RDS/infra ops | — |
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
| P11-5 | AI prompt sanitisation + output schema + circuit breaker (BSA-011/012) | MED | — | 📋 scoped | — |
| P11-6 | AuthZ & audit hardening — admin reset re-auth, `log_authz_denial()` routing (BSA-003/009) | MED | — | 📋 scoped | — |
| P11-7 | Document-download attachment + webhook signature hygiene (BSA-008/010, + DCI-017) | MED+LOW | — | 📋 scoped | — |
| P11-8 | Supply-chain pinning (BSA-016/017/019 = DCI-022/024) — re-run partial R2-BSA-019 open | MED+LOW | [#712](https://github.com/onboarda1234/onboarda/pull/712) | ✅ merged + validated 2026-07-08 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p11-8-pr-712) |
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
| P12-4 | Migration hard-stops + schema-drift detection (DCI-005/004) | HIGH | [#711](https://github.com/onboarda1234/onboarda/pull/711) | ◐ DCI-005 half ✅ #711 · DCI-004 drift check 📋 | — |
| P12-5 | Status-column CHECK constraints (DCI-006, Migration v2.47) | MED | [#716](https://github.com/onboarda1234/onboarda/pull/716) + [#739](https://github.com/onboarda1234/onboarda/pull/739) | ✅ merged; staging constraints installed via #739, executed 2026-07-11 · 54-FK follow-up tracked at DCI-104 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p12-5-dci-006-prs-716-and-739) |
| P12-6 | PG pool connection validation — pre-ping on checkout (DCI-007) | MED | [#709](https://github.com/onboarda1234/onboarda/pull/709) | ✅ merged 2026-07-08 | — |
| P12-7 | Verification-matrix fidelity — HYBRID only on deterministic INCONCLUSIVE; resolve 5 TODO mappings (DCI-014/015) | MED+LOW | — | 📋 scoped | — |
| P12-8 | Retention purge enforceability + purge-log evidence (DCI-020/021, Migration v2.48) | MED | [#717](https://github.com/onboarda1234/onboarda/pull/717) + hotfix [#723](https://github.com/onboarda1234/onboarda/pull/723) | ✅ merged + deployed | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p12-8-prs-717-and-723) |
| P12-9 | Observability hardening — JSON logs, request-correlation ids, readiness gates (DCI-028/029, Migration v2.49) | MED | [#718](https://github.com/onboarda1234/onboarda/pull/718) | ✅ merged + deployed | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p12-9-pr-718) |
| P12-10 | Infra guards — upload body-size pre-buffering, deploy fails on `services-stable` timeout (DCI-016/025; stability half partly mitigated by #702) | MED+LOW | — | 📋 scoped | — |

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
| P13-3 | Defensive API response parsing — status/Content-Type before `res.json()` (FEO-004) | MED | — | 📋 scoped | — |
| P13-4 | App-detail render race guard — request nonce in `openAppDetail` (FEO-005) | MED | — | 📋 scoped | — |
| P13-5 | Role-UI fail-closed until RBAC matrix loads (FEO-006) | LOW | — | 📋 scoped | — |
| P13-6 | Portal intake PII out of sessionStorage — server-side save/resume (FEO-007) | MED | — | 📋 scoped | — |
| 🟠 P13-7 | Compliance-officer SOP pack (FEO-014) | MED | [#745](https://github.com/onboarda1234/onboarda/pull/745) | ◐ docs ✅ merged 2026-07-13 (`02eeae5`) · 🟠 Section 16 execution open (officers named/trained, scope approved, signatures) | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p13-7-pr-745) |

## Phase 13 — Pilot Controls Pack

| ID | Title | Sev | GitHub | Status | E |
|----|-------|:--:|:--:|----|:--:|
| 🟠 33 | Pilot-scope guards (server-side) — pilot operational gate | — | — | ⬜ pending | — |
| 34 | Dashboard API performance (15.1s → sub-2s) | — | — | ⬜ pending | — |
| 35 | Screening full-evidence hydration performance | — | — | ⬜ pending | — |
| 36 | Persisted negative-path fixtures — controlled-pilot staging evidence | — | #748, #749 | ✅ closed 2026-07-12 (pilot scope; staging left clean) | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#item-36-prs-748-749) |
| 37 | Lower-privilege fixture authz regression tests | — | #692 | ✅ merged | — |
| 38 | Pilot operations runbook | — | #689 | ✅ merged | — |
| 🟠 — | *(cross-ref: CA production workspace validation = P9-3, Phase 14 — not counted)* | — | #498 | ⏸ see P9-3 | — |

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
| P9-8 | DR/backup drill, restore/PITR, RTO/RPO (= **DCI-027 CRITICAL blocker** = **FEO-009**) | ops | — | ⬜ | — |
| P9-9 | Legal/compliance sign-off (residency, DPA, regulator) | legal | — | ⬜ | — |
| P9-10 | Prod monitoring/alerting/on-call (+ **DCI-030**, **FEO-011**) | ops | — | ⬜ | — |
| P9-11 | Close parked prod-posture decisions (PR-25 + PR-17) | decision | — | ⬜ | — |
| P9-12 | ECR immutable image tags (REGMIND-P2-004) | ops | — | ⬜ | — |
| P9-13 | Full authz/tenant-isolation route matrix (role-by-route) | security | [#733](https://github.com/onboarda1234/onboarda/pull/733) | ◐ harness ✅ #733 (53/53 checks) · ⬜ runtime coverage of approval/dual-control/memo-approve/screening-2nd-review/IDV + cross-client seed fix | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#app-aud-prs-733-734-735) |
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
| 🔴 DCI-108 | Risk parser under-scores "very complex" ownership → 3 (`rule_engine.py:1219-1273`); with DCI-109 can flip MEDIUM→LOW | HIGH | [#753](https://github.com/onboarda1234/onboarda/pull/753), [#755](https://github.com/onboarda1234/onboarda/pull/755) | 🔨 Tier 0A+0B merged (see RSMP below) · Tier 0C ⬜ | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#rsmp-prs-751-753-755-764) |
| 🔴 DCI-109 | "non-regulated" resolves to 1 via dict-ordering fall-through (same site); same MEDIUM→LOW flip risk | HIGH | [#753](https://github.com/onboarda1234/onboarda/pull/753), [#755](https://github.com/onboarda1234/onboarda/pull/755) | 🔨 Tier 0A+0B merged (see RSMP below) · Tier 0C ⬜ | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#rsmp-prs-751-753-755-764) |
| DCI-110 | Middle-band turnover 500k–5m over-scores to 4 (severity corrected HIGH→MED 2026-07-11: over-, not under-scoring) | MED | — | 📋 scoped | — |
| R2-BSA-001 | Supervisor routes bypass BaseHandler middleware + wildcard CORS on authenticated APIs | HIGH | [#743](https://github.com/onboarda1234/onboarda/pull/743) | ✅ closed, staging-validated 2026-07-11 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#r2-bsa-cluster-prs-743-747) |
| R2-BSA-002 | Supervisor actor client-forgeable via request-body reviewer/escalation fields | HIGH | [#743](https://github.com/onboarda1234/onboarda/pull/743) | ✅ closed, staging-validated 2026-07-11 | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#r2-bsa-cluster-prs-743-747) |
| R2-BSA-003 | Supervisor reviews/overrides/escalations persisted via raw `sqlite3` to ephemeral container disk (audit-record loss) | HIGH | [#747](https://github.com/onboarda1234/onboarda/pull/747) | ✅ closed, staging-validated 2026-07-12 (Migration v2.52) | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#r2-bsa-cluster-prs-743-747) |
| R2-BSA-004 | General CSRF bypass — `/webhook` URI substring match skips CSRF on ANY path | HIGH | [#743](https://github.com/onboarda1234/onboarda/pull/743) | ✅ closed, staging-validated 2026-07-11 (exact-path allowlist) | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#r2-bsa-cluster-prs-743-747) |

### Merged items re-flagged PARTIAL

| ID | Refines | Title | GitHub | Status | E |
|----|:--:|-------|:--:|----|:--:|
| R2-BSA-016 | item 26 / #728 | AI-route limiter gaps: `/api/documents/{id}/verify` + both supervisor pipeline triggers unlimited; enhanced-upload limiter process-local | — | ⬜ partial open | — |
| R2-BSA-019 | P11-8 / #712 | No hash-pinned lockfile / `pip install --require-hashes`; deps pinned by version only | — | ⬜ partial open | — |
| RDI-107 | P10-6 / #708 | Trusted-proxy check trusts ANY private/loopback peer; needs explicit proxy-CIDR allowlist | — | ⬜ partial open | — |
| DCI-104 | P12-5 / #716 | 3 v2.47 CHECK constraints absent on staging + 54 unindexed FKs | [#739](https://github.com/onboarda1234/onboarda/pull/739) | ◐ constraints ✅ executed on staging 2026-07-11 · 54 FK indexes ⬜ | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p12-5-dci-006-prs-716-and-739) |
| R2-PROC-1 | (new, LOW) | Staging QA/validation must not write raw SQL into regulated tables — route probe writes through the app or a marked fixture path | — | ⬜ pending | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#p12-5-dci-006-prs-716-and-739) |

### Canonical staging dataset

| ID | Title | GitHub | Status | Evidence |
|----|-------|:--:|----|:--:|
| PILOT-DATA-001 | Canonical memo and lifecycle demo completion | Draft PR | 🔨 deterministic memo contract, fixture notification suppression, Monitoring/Periodic fixture visibility; AI Supervisor explicitly excluded; post-deploy UI revalidation still required | [Guide](pilot/PILOT_CANONICAL_DATASET.md) |

### RSMP — Risk Scoring Model Pack (DCI-108/109 response)

| ID | Title | GitHub | Status | E |
|----|-------|:--:|----|:--:|
| RSMP-DOCS | Audit/review pack (full audit, founder decision pack, scenario matrix, settings register) | [#751](https://github.com/onboarda1234/onboarda/pull/751) | ✅ merged | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#rsmp-prs-751-753-755-764) |
| RSMP-0A | Tier 0A — guarded parser + mapping fidelity (activation flag OFF) | [#753](https://github.com/onboarda1234/onboarda/pull/753) | ✅ merged | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#rsmp-prs-751-753-755-764) |
| RSMP-0B | Tier 0B — fail-closed routing on unresolved mappings | [#755](https://github.com/onboarda1234/onboarda/pull/755) | ✅ merged + staging-validated at `dd4784b` | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#rsmp-prs-751-753-755-764) |
| RSMP-0D | Tier 0D — runtime and Back Office risk-model alignment | [#768](https://github.com/onboarda1234/onboarda/pull/768) | ✅ merged + staging-validated at `7e91114`; read-only UI/export evidence aligned; activation OFF | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#rsmp-tier-0d-pr-768) |
| RSMP-0C | Tier 0C — activation + recomputation | — | ⬜ **final remaining RSMP pilot-readiness workstream**; no recomputation executed | — |
| RSMP-PR1B | PR-1b — declared-PEP runtime alignment with approved Gate 0 v4 model | [#764](https://github.com/onboarda1234/onboarda/pull/764) | ✅ merged 2026-07-15 at `a823fb6` | [E](compliance/REMEDIATION_CLOSURE_EVIDENCE.md#rsmp-prs-751-753-755-764) |

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

## Roll-up — computed by counting rows, 2026-07-16

Counting rule: every row in Phases 0–14 + the Re-audit/RSMP tables counts once.
The 3 cross-reference rows (Phase 7 audit-log-tamper-evidence-1, Phase 7
APP-CONF-003, Phase 13 CA row) and the Optional Modernization tables are
excluded. ◐ = items with one named half done and one open (SRP-1, SRP-2,
staging-SHA gate, P12-4, P13-7, P9-13, DCI-104). Note: parallel register PRs (`#780` and
merged `#783`) recount their own streams; on merge, recount the union.

| Status | Count |
|--------|:--:|
| ✅ done/merged | 98 |
| ◐ split — one half open | 7 |
| 🟢 PR open | 1 |
| 🔨 in progress | 2 |
| 📋 scoped | 20 |
| ⏸ blocked | 5 |
| ⬜ pending | 37 |
| **Total tracked items** | **170** |
