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

**Last reconciled:** 2026-07-06 (base `main` ≈ `19a44f5`, contains merged #687).

> Maintenance: this is the single source of truth for remediation status. On any
> request for PR/phase status, refresh the Status/GitHub columns from GitHub and
> update this file. Item IDs (1–40, 33–38, P9-1…P9-11, PR-* slugs) are canonical.

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
| 18 | Redaction/response allow-list | #690 | 🟢 |
| 19 | Resilience/fail-safe → delete dead `resilience/` | #693 | 🟢 |
| 20 | Persist memo `blocked` verdict — P0 | #679 | ✅ |
| 21 | DOB/PII encryption at rest | — | ⬜ |
| 22 | CSP headers (report-only) | #688 | 🟢 |
| 23 | Session revocation | #687 | ✅ |
| 24 | CA webhook retry idempotency | — | 📋 scoped |
| 25 | Unique seeded-account secrets (M14) — P0 | #681 | ✅ |
| 26 | Shared rate limiter | — | ⬜ |
| 27 | audit_log tamper-evidence (core; wiring deferred) | #691 | 🟢 |
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
| PR-APP-ACTION-OWNERSHIP-SCOPE-1 | P1/P2 | Act-only-as-owner + supervisor override | — | 🔨 |
| ops-enforce-staging-sha-alignment-gate | P0 | Staging-SHA gate + delete test logins | — | ⬜ |
| perf-applications-default-list-projection | P2 | Slim default list payload | — | ⬜ |
| audit-log-tamper-evidence-1 | P2 | *(= Phase 4 #27)* | #691 | 🟢 |
| ux-applications-list-sort-status-tabs | P3 | Sortable headers + status tabs | — | ⬜ |
| chore-applications-deadcode-cleanup | P3 | Delete dead approval branches | — | ⬜ |

## Phase 8 — Pilot Controls Pack
| # | Title | GitHub | Status |
|---|-------|--------|:--:|
| 33 | Pilot-scope guards (server-side) | — | ⬜ |
| 34 | Dashboard API performance (15.1s → sub-2s) | — | ⬜ |
| 35 | Screening full-evidence hydration performance | — | ⬜ |
| 36 | Persisted negative-path fixtures | — | ⬜ |
| 37 | Lower-privilege fixture authz regression tests | #692 | 🟢 |
| 38 | Pilot operations runbook | #689 | 🟢 |
| — | ComplyAdvantage production workspace validation | #498 | ⏸ blocked (dashboard-mode evidence) |

## Phase 9 — Production readiness
| # | Item | Type | GitHub | Status |
|---|------|:--:|:--:|:--:|
| P9-1 | Enable live GDPR erasure (PC-4 control pack) | code | — | ⬜ |
| P9-2 | Close PC-1 evidence-pack continuity residual | code | — | ⬜ |
| P9-3 | ComplyAdvantage prod workspace validation | ops/vendor | #498 | ⏸ |
| P9-4 | Provision prod environment (app.regmind.co) | ops | — | ⬜ |
| P9-5 | Drill prod deploy + rollback | ops | — | ⬜ |
| P9-6 | Load/performance test at prod scale | test/ops | — | ⬜ |
| P9-7 | Pen test + security review + vuln scanning | security | — | ⬜ |
| P9-8 | DR/backup drill (restore/PITR) | ops | — | ⬜ |
| P9-9 | Legal/compliance sign-off (residency, DPA, regulator) | legal | — | ⬜ |
| P9-10 | Prod monitoring/alerting/on-call | ops | — | ⬜ |
| P9-11 | Close parked prod-posture decisions (PR-25 + PR-17) | decision | — | ⬜ |

## Backlog — after Phase 7
| PR | Priority | Title | Status |
|----|:--:|-------|:--:|
| PR-RISK-SECTOR-CALIBRATION-1 | P2 | Recalibrate sector risk + "unknown≠high" defaults | 📋 scoped (audit done) |

---

## Roll-up (65 line items)
| Status | Count |
|--------|:--:|
| ✅ merged | 30 |
| 🟢 PR open (built) | 6 |
| 🔨 in progress | 1 |
| 📋 scoped | 3 |
| ⏸ blocked | 1 |
| ⬜ pending | 24 |

**Open PRs (tonight):** #688 #689 #690 #691 #692 #693 · **Old blocked draft:** #498.

**Where things stand:** Phases 0–3 (except B7 #12) and 5–6 done. Phase 4 built out
(6 done/open; rest decision-gated). Phase 7 progressing (ownership gate #18 in
progress). Phases 8–9 are the remaining body — overwhelmingly ops/vendor/legal,
not code. Pilot-readiness ≈ 85–90%; production-readiness ≈ 30–35%.
