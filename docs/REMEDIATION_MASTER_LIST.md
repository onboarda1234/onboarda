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

**Last reconciled:** 2026-07-07 (base `main` ≈ `b577a5f`, contains merged #691).
The **entire tonight code batch is now merged, deployed to AWS staging, and
validated (PASS):** #692 (item 37, merge `8f65435`, TD 775), #690 (item 18, merge
`8b0a7a8`, TD 776), #693 (item 19, merge `db0702c`, TD 777), #691 (item 27, merge
`b577a5f`). Earlier: #687/#688/#689 (items 23/22/38, TDs 771/772/773). Only #695
(this docs PR) remains open in the batch. Incorporates REGMIND-SYSTEM-READINESS-AUDIT-1
(P9-12/13/14 + CLIENT-PORTAL-RUNTIME-SMOKE-1 + PERIODIC-BASELINE-METHOD-HYGIENE-1),
an Optional/Post-Production Modernization section, and Phase 10 (RDI audit).

> Maintenance: this is the single source of truth for remediation status. On any
> request for PR/phase status, refresh the Status/GitHub columns from GitHub and
> update this file. Item IDs (1–40, 33–38, P9-1…P9-14, P10-1…P10-7, PR-* slugs) are canonical.

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
| 21 | DOB/PII encryption at rest | — | ⬜ |
| 22 | CSP headers (report-only) | #688 | ✅ |
| 23 | Session revocation | #687 | ✅ |
| 24 | CA webhook retry idempotency | — | 📋 scoped |
| 25 | Unique seeded-account secrets (M14) — P0 | #681 | ✅ |
| 26 | Shared rate limiter | — | ⬜ |
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
| PR-APP-ACTION-OWNERSHIP-SCOPE-1 | P1/P2 | Act-only-as-owner + supervisor override | — | ⬜ |
| ops-enforce-staging-sha-alignment-gate | P0 | Staging-SHA gate + delete test logins | — | ⬜ |
| perf-applications-default-list-projection | P2 | Slim default list payload | — | ⬜ |
| audit-log-tamper-evidence-1 | P2 | *(= Phase 4 #27)* | #691 | ✅ |
| ux-applications-list-sort-status-tabs | P3 | Sortable headers + status tabs | — | ⬜ |
| chore-applications-deadcode-cleanup | P3 | Delete dead approval branches | — | ⬜ |
| CLIENT-PORTAL-RUNTIME-SMOKE-1 | P1 | Live client-credential smoke: status/upload/logout/**cross-tenant denial** *(audit REGMIND-P1-006)* | — | ⬜ |
| PERIODIC-BASELINE-METHOD-HYGIENE-1 | P2 | Clean 405 on POST-only periodic-review baseline route *(audit REGMIND-P2-001)* | — | ⬜ |

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
| P9-12 | ECR-IMMUTABLE-TAGS-1 — make ECR image tags immutable (rollback provenance) *(audit REGMIND-P2-004)* | ops | — | ⬜ |
| P9-13 | Full authz / tenant-isolation **route matrix** audit (role-by-route) *(audit §7)* | security | — | ⬜ |
| P9-14 | Registry KYB (OpenCorporates) **simulated → real/production** *(audit prod blocker)* | code/vendor | — | ⬜ |

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
| P10-1 | PR-RDI-1 — Server-side materiality (+ approved maker-checker scope change: four-eyes {tier1,tier2}→{tier1}) | RDI-006 | CRITICAL | Ignore client-supplied change materiality; always classify server-side from change type. Part B: relax four-eyes to Tier 1 only (approved, Aisha Sudally) — screening hard-block still covers Tier 2 | — | 🔨 in progress |
| P10-2 | PR-RDI-2 — Fail-closed decision & memo persistence | RDI-001, 007, 011 | CRITICAL + HIGH + MED | Decision status+audit+signoff+decision_record in one transaction; memo approve/validate roll back and 500 on save failure (no false "success") | — | 📋 scoped |
| P10-3 | PR-RDI-3 — Risk-staleness gate | RDI-004 | CRITICAL | Block final decisions when `risk_config_version` ≠ current or recompute failed; persist recompute failures | — | 📋 scoped |
| P10-4 | PR-RDI-4 — Per-decision-type gates | RDI-003, 008 | HIGH | Add required prerequisites for reject / escalate_edd / request_documents; block failed-validation memo from supervisor step **(needs policy decision on per-type prerequisites)** | — | 📋 scoped (decision-gated) |
| P10-5 | PR-RDI-5 — Decision-record coverage + provenance | RDI-009 (non-SAR), 010 | HIGH | Write decision_records for EDD closure / monitoring actions / change approvals / risk changes; add AI-vs-rule source + `agent_executions` link. Depends on **P10-2** | — | 📋 scoped |
| P10-6 | PR-RDI-6 — Sign-off IP attribution | RDI-012 | HIGH | Trust `X-Real-IP` only when the direct peer is a known proxy/ALB (stop browser spoofing) | — | 📋 scoped |
| P10-7 | PR-RDI-7 — Append-only audit at DB level | RDI-013 (non-SAR) | MEDIUM | Separate migration/admin DB role from runtime role; revoke runtime `UPDATE`/`DELETE` on `audit_log`/`decision_records`/`supervisor_audit_log`; stop cleanup code deleting those rows *(code half ships early; grants half is RDS/infra)* | — | 📋 scoped (part ops) |

**Deferred (per management response 2026-07-07):**
- **RDI-002** — by-design LOW/MEDIUM fast-path, HIGH policy-exception (not a code defect). **P10-DOC-1:** policy **✅ APPROVED & signed off** (Aisha Sudally, 2026-07-07) at [`docs/compliance/LOW_MEDIUM_FASTPATH_APPROVAL_POLICY.md`](compliance/LOW_MEDIUM_FASTPATH_APPROVAL_POLICY.md) (eligibility = all LOW/MEDIUM; disqualifiers = sanctioned/FATF, PEP, adverse hit, stale/incomplete screening, failed IDV; approver = Onboarding Officer alone; 20% QA sampling). **Residual code assertions** (decision-record eligibility-basis stamp + direct-route test that a disqualifying signal can never fast-track) folded into the Phase 10 approval-path PRs (P10-3 / P10-5) — ⬜.
- **RDI-005** — SAR permanence (`ON DELETE CASCADE`, cleanup delete, mutable SAR content), HIGH **Enterprise pre-enable blocker**. Must be fixed **before** enabling Enterprise SAR/STR; safe to defer **only while SAR/STR feature flags stay disabled** (`ENABLE_SAR_WORKFLOW`, `ENABLE_SAR_STR` = false). Same guard covers the SAR slices of RDI-009/RDI-013.

**Wave order:** W1 P10-1 → P10-2 → P10-3 (all CRITICAL; P10-2 unblocks P10-5) · W2 P10-4, P10-5, P10-6 (HIGH) · W3 P10-7 (MED/infra). P10-1 and P10-6 are small quick wins slot-able anytime.

## Backlog — after Phase 7
| PR | Priority | Title | Status |
|----|:--:|-------|:--:|
| PR-RISK-SECTOR-CALIBRATION-1 | P2 | Recalibrate sector risk + "unknown≠high" defaults | 📋 scoped (audit done) |

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

## Roll-up (78 remediation line items + optional modernization tracked separately)
| Status | Count |
|--------|:--:|
| ✅ merged | 36 |
| 🟢 PR open (built) | 1 |
| 🔨 in progress | 1 |
| 📋 scoped | 9 |
| ⏸ blocked | 1 |
| ⬜ pending | 30 |

**Open PRs:** #695 (this docs PR) · **Old blocked draft:** #498. All tonight code PRs
(#687–#693) merged, deployed to staging, validated PASS.

**Where things stand:** Phases 0–3 (except B7 #12) and 5–6 done. **Phase 4 fully
built/merged** (only decision-gated #17/#21/#24/#26/#28 remain). Phase 7: status-canon
done + audit-tamper (#691) merged; ownership gate not started (⬜). Phases 8–9 are the
remaining body — overwhelmingly ops/vendor/legal, not code. **Phase 10 (RDI audit):**
P10-1 in progress; P10-2/P10-3 next (the other two current-stage CRITICALs — RDI-001/004);
P10-DOC-1 policy approved. Management response (2026-07-07) narrowed Audit-2 blocking
CRITICALs to 3 (RDI-001/004/006). Pilot-readiness ≈ 88–92%; production-readiness ≈ 30–35%.
