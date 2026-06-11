# RegMind Full System Integrity Audit

**Date:** 2026-06-11
**Scope:** Full product integrity audit — all 31 product areas, 10 critical workflow chains, data integrity, security/roles, integrations
**Posture:** Hostile production-readiness review for a regulated paid pilot. Prior PASS reports treated as context only; every claim re-verified against the current codebase.
**Method:** Static code audit of `arie-backend/` (server.py 28,123 lines + 60 modules), `arie-backoffice.html`, `arie-portal.html`, `index.html`; full regression suite executed as baseline evidence. No live provider calls were triggered.

---

## 1. Executive Summary

**Verdict: CONDITIONALLY READY — core decisioning spine is production-grade; 2 CRITICAL and 6 HIGH findings must be remediated before a regulated paid pilot.**

The backend approval/decision spine is genuinely fail-closed: five independent server-side gates protect approval, terminal states are immutable, dual control is enforced, memo staleness invalidates downstream validation/supervisor results, and the supervisor audit log is hash-chained with verifiable integrity. The full regression suite passes: **5,222 passed, 17 skipped** (run 2026-06-11, `python -m pytest tests/ -q`).

However, the audit found cross-workflow defects that prior single-module audits missed — exactly the class of issue this audit was commissioned to find:

| # | Severity | Finding | Evidence |
|---|----------|---------|----------|
| F-01 | **CRITICAL** | Sumsub presented as an AML screening provider in the Screening Queue filter while the Sumsub entitlement is identity-verification only; ComplyAdvantage selectable while `ENABLE_SCREENING_ABSTRACTION` is hardcoded `False` and all screening routes to Sumsub | `arie-backoffice.html:1752`; `screening_config.py:21–26`; `screening_routing.py:100–107`; `screening.py:754` |
| F-02 | **CRITICAL** | "Save Schedule" for the Ongoing Review Schedule only mutates in-memory JS variables and shows a success toast; it never calls the backend. The backfill endpoint `/api/monitoring/reviews/schedule` is wired in routing but unreachable from any UI element | `arie-backoffice.html:24582–24588, 2599`; `server.py:23579, 27695` |
| F-03 | HIGH | Failed Sumsub identity verification (`reviewAnswer=RED`) is recorded as a flag only and does not gate application progression | `server.py:18554–18559, 6814` |
| F-04 | HIGH | "Latest memo" resolved inconsistently: detail view orders by `version DESC, id DESC` while memo approval and PDF download order by `created_at DESC` — wrong memo version can be approved/exported under same-timestamp collisions | `server.py:5127` vs `server.py:20549, 20901` (verified) |
| F-05 | HIGH | Memo generation failure returns HTTP 500 with no fallback path; `generate_fallback_memo()` exists in `validation_engine.py` but is never invoked — an Anthropic outage halts all decisioning | `server.py:19526–19533` (verified) |
| F-06 | HIGH | Three coexisting risk fields (`risk_level`, `final_risk_level`, `base_risk_level`) with inconsistent fallback logic across queries; risk elevation (`final_risk_level` write) is not audit-logged | `server.py:3332–3336, 5002, 4799, 6798` |
| F-07 | HIGH | Case Command Centre renders `guidance_blockers` while the backend enforces a different `gate_blockers` set from `ApprovalGateValidator` — officers can see "no blockers" then be rejected server-side | `arie-backoffice.html:20435–20445`; `server.py:21446–21450` |
| F-08 | HIGH | Sumsub webhooks overwrite the prior payload in `prescreening_data` with no history; GREEN→RED transitions are unreconstructable from the UI and webhooks are not audit-logged | `server.py:18551–18562` |

**Primary question** (*Can a real compliance officer process a regulated client end-to-end without developers, DB checks, or hidden APIs?*):

> **Qualified YES for the onboarding → decision path; NO for two adjacent paths.** Onboarding, screening disposition, EDD, memo, validation, supervisor, approval/rejection/RMI, monitoring, and export are operable end-to-end from the UI, with backend gates that protect against officer error. The two failures: (a) an officer cannot manually trigger periodic-review backfill or persist the review schedule from the UI (F-02 — mitigated by automatic enrollment on approval and the scheduled automation sweep in staging/production, see §5.6), and (b) an officer cannot correctly characterise the screening provider's entitlement from what the UI shows (F-01), which is a regulatory misstatement risk in a paid pilot.

---

## 2. Baseline Evidence

| Check | Result |
|---|---|
| Full pytest suite (`arie-backend/tests/`, 267 test files) | **5,222 passed, 17 skipped**, 0 failures, 307s |
| Server-side gate enforcement (approval) | 5 gates verified at endpoint level (§5.5) |
| Supervisor audit hash chain | v2 SHA-256 chaining + `verify_chain_integrity()` present and field-complete (`supervisor/audit.py:55–110, 486–545`) |
| Auth model | Tokens re-validated against DB row per request; role taken from DB, not token claims (`base_handler.py:440–487`) — no role-spoofing via stale JWT |
| Password storage | bcrypt for officers and clients (`server.py:2593, 2651, 2955, 2993, 3045`) |
| Audit IP provenance | `X-Forwarded-For` trusted only behind private/loopback proxy — spoof-resistant (`base_handler.py:505–530`) |

---

## 3. Chain 1 — New Application Onboarding

**Status: PASS with HIGH data-integrity caveats.**

Verified working:
- Application creation with duplicate-company prevention and `Create` + PEP-declaration audit rows (`server.py:3571–3660`).
- Status machine: `draft → submitted → pricing_review → pricing_accepted → [pre_approval_review] → kyc_documents → under_review → decision`.
- Post-submission immutability of `prescreening_data` and `screening_mode` (C-04/C-07 fix, `server.py:5215–5232`) — screening inputs cannot be rewritten after submit.
- KYC prerequisite gating (`_kyc_prerequisite_error`) and enhanced-requirement status transitions (`enhanced_requirements.py:45–92`).
- Back-office document visibility: active documents via `ACTIVE_DOCUMENT_SQL` plus full `document_history` with `include_history=true` (`server.py:5011–5030`).

Defects:
| ID | Sev | Finding |
|---|---|---|
| C1-1 | HIGH | (= F-06) Risk-field triplication. List filters use `COALESCE(final_risk_level, risk_level)` (`server.py:3332–3336`); other paths read `risk_level` directly. Legacy rows with NULL `final_risk_level` are classified inconsistently across pages. `final_risk_level` writes (`server.py:6798`) produce no audit row — risk elevation is invisible in the audit trail. |
| C1-2 | MEDIUM | Submission performs two sequential UPDATEs — status written `submitted` with risk data then immediately overwritten `pricing_review` (`server.py:6792–6814`); non-atomic window plus no explicit "Status Change" audit row for the intermediate transitions. |
| C1-3 | MEDIUM | Document `verification_status` transitions (pending → verified/failed) are not audit-logged (`server.py:8624`). |
| C1-4 | MEDIUM | Portal does not indicate a document was superseded (`superseded_by_document_id` never surfaced client-side); a client whose document was rejected and re-requested sees only the active doc with no rejection context. |
| C1-5 | LOW | List API returns raw enums without `status_label`/`risk_level_label` (detail API computes them, `server.py:5000–5002`); frontend maintains a parallel label map that can desynchronise. |

---

## 4. Chain 2 — Identity Verification (Sumsub)

**Status: FAIL on provider classification; webhook plumbing itself is sound.**

The Sumsub entitlement covers ID verification, liveness/face match, email/phone verification, Sumsub ID, and reusable KYC — i.e., **identity verification, not AML/PEP/sanctions screening** unless that entitlement is separately proven.

Verified working:
- Dual-format webhook signature verification (`X-App-Access-Sig` / `X-Payload-Digest`, `server.py:18316–18372`); applicant-ID format validation and log masking; unmatched webhooks routed to a DLQ table (`sumsub_unmatched_webhooks`).
- Only `applicantReviewed` is treated as mutating (`server.py:18414–18430`); idempotent mapping insert (`INSERT OR IGNORE` into `sumsub_applicant_mappings`, `server.py:18062`).
- Back office shows applicant ID, external user, received timestamp, event type, and RED rejection labels/moderation comment (`arie-backoffice.html:12421–12428`); GREEN/RED/PENDING colour mapping is correct (`12416–12417`).
- Admin integration page correctly labels Sumsub as "Sumsub IDV/KYC" (`arie-backoffice.html:26213`), and the AI-agent catalog explicitly notes "Does NOT do sanctions screening or registry lookups" (`23227`) — but neither note reaches the officer review surface.

Defects:
| ID | Sev | Finding |
|---|---|---|
| C2-1 | **CRITICAL** | (= F-01) `<option value="sumsub">Sumsub</option>` in the Screening Queue provider/source filter (`arie-backoffice.html:1752`) presents Sumsub as an AML screening provider. Combined with the AML calls in `screening.py:60` (`screen_sumsub_aml`) that depend on an unproven entitlement, an officer could record "sanctions screening completed via Sumsub" in a regulated file. |
| C2-2 | HIGH | (= F-03) `reviewAnswer=RED` only appends to `overall_flags` (`server.py:18554–18559`); no decision gate, RMI item, or pre-approval blocker consumes IDV failure. An application with failed identity verification proceeds through the workflow. |
| C2-3 | HIGH | (= F-08) Each webhook overwrites `prescreening_data['screening_report']['sumsub_webhook']` (`server.py:18551`); no append-only history and no audit-log entry per webhook — IDV state transitions are unreconstructable. |
| C2-4 | MEDIUM | The "Sumsub KYC Verification" card sits in the same visual block as AML "Screening Subjects" (`arie-backoffice.html:12413–12431`) with no separator distinguishing identity verification from AML screening. |
| C2-5 | MEDIUM | `sumsub_applicant_mappings` integrity is invisible — no UI to detect duplicate applicant IDs across applications or stale mappings. |

---

## 5. Chains 3–10 — Findings by Chain

### 5.1 Chain 3 — AML / Screening

**Status: PASS on state machine; FAIL on provider truth-in-labelling.**

Verified working:
- `defensible_clear` is mathematically fail-closed: requires `canonical_state == COMPLETED_CLEAR` AND `mode == LIVE_PROVIDER` AND terminal (`screening_state.py:661`). Simulated/fallback screening **cannot** produce a defensible clear. No "pending and clear" or "hits but defensible clear" state is constructible.
- Four-eyes logic itself is sound: `_review_second_signoff_satisfied()` validates `requires_four_eyes` + `second_reviewer_id` (`screening_state.py:210–217`).
- Provider degradation is explicit: per-source failures recorded as `degraded_sources` markers (`screening.py:754–776`); missing credentials produce clearly annotated simulated results (`screening.py:232, 321, 420`), and those results are excluded from defensible clears by the LIVE_PROVIDER requirement.

Defects:
| ID | Sev | Finding |
|---|---|---|
| C3-1 | **CRITICAL** | (= F-01) ComplyAdvantage selectable in UI while `ENABLE_SCREENING_ABSTRACTION` is `False` in all environments (`screening_config.py:21–26`); `_effective_provider_name()` always returns Sumsub (`screening_routing.py:100–107`); `run_full_screening()` hardcoded to the Sumsub path (`screening.py:754`). Provider selection in the UI is a no-op. |
| C3-2 | HIGH | Screening disposition save (`ScreeningReviewHandler.post`, `server.py:17420–17500`) accepts cleared/escalated dispositions with no memo prerequisite; the officer discovers the missing memo only at approval (`server.py:21413–21425`). |
| C3-3 | MEDIUM | Four-eyes is validated at memo approval, not at disposition submission — a single officer can save "cleared" on a `requires_four_eyes` match and learn it is non-actionable only downstream. |
| C3-4 | MEDIUM | Hit-count aggregation asymmetry: company hits use `max(company_facts, company_media_alerts)` (`server.py:17155–17158`) while person hits use a single source (`17331`) — queue totals and detail totals can differ. |

### 5.2 Chain 4 — High-Risk / EDD

**Status: PASS on enforcement; HIGH on officer guidance.**

Verified working:
- Risk-based role limits: CO cannot approve HIGH/VERY_HIGH (`server.py:21401–21410`); dual approval by two distinct officers for high risk (`21458–21494`).
- EDD routing/actuation with `origin_context` linkage; EDD completion is a memo prerequisite (`server.py:19520–19524`).

Defects:
| ID | Sev | Finding |
|---|---|---|
| C4-1 | HIGH | (= F-07) UI renders `guidance_blockers` ("No guidance blockers detected") while approval is enforced against `ApprovalGateValidator` `gate_blockers` (`server.py:21446–21450`; `arie-backoffice.html:20435–20445`). The disclaimer "Final approval remains subject to backend approval gates" is present but the two blocker sources are not reconciled — officers experience false readiness. |
| C4-2 | MEDIUM | `actuate_edd_routing()` (`edd_actuation.py:125–160`) can re-route an application to EDD without resetting a previously `edd_approved` case — case status and application blockers can contradict. |
| C4-3 | MEDIUM | Re-actuation under risk re-scoring can orphan a partially-completed EDD case (new case_id, old case unlinked from active workflow). |
| C4-4 | LOW | Blocker badge uses `projection.blocker_count` while the list renders `gate_blockers[]` (`arie-backoffice.html:4256, 13024`) — badge can read 0 above a non-empty list. |

### 5.3 Chain 5 — Compliance Memo

**Status: PASS on invalidation/regeneration; HIGH on ordering and availability.**

Verified working:
- Server-side prerequisites: risk-integrity check before generation (`server.py:19394–19401`); EDD completion gate (`19520–19524`).
- Staleness model: `_mark_latest_memo_stale` resets `validation_status`, `supervisor_status`, `validation_issues`, and `approved_by` (`server.py:19103–19200`) — a regenerated memo cannot inherit prior sign-offs.
- Validation results rendered with status badges (`arie-backoffice.html:25629–25648`); supervisor INCONSISTENT verdicts both rejected server-side (`server.py:20658–20676`) and alerted in UI (`25552–25558`).
- Risk-based model routing confirmed: LOW/MEDIUM → Sonnet, HIGH/VERY_HIGH → Opus (`claude_client.py:984–989`).
- PDF download gated on memo existence + freshness (`server.py:20908–20923`).

Defects:
| ID | Sev | Finding |
|---|---|---|
| C5-1 | HIGH | (= F-04, verified) `MemoApproveHandler` (`server.py:20549`) and `MemoPDFDownloadHandler` (`20901`) select the "latest" memo by `created_at DESC` while the detail view uses `version DESC, id DESC` (`5127`). Same-second memo creation can cause approval/export of a different memo than the one displayed. |
| C5-2 | HIGH | (= F-05, verified) `build_compliance_memo` exceptions return HTTP 500 (`server.py:19526–19533`); `generate_fallback_memo()` in `validation_engine.py` is dead code. Fail-closed is compliance-safe, but a provider outage blocks all decisioning with no officer-visible degraded path. |
| C5-3 | MEDIUM | Case Command Centre memo card does not refresh when a memo goes stale after page load; server-side gate still blocks, but the UI misleads until reload. |

### 5.4 Chain 6 — Periodic Review

**Status: PASS on backend engine and isolation; CRITICAL on UI wiring.**

Verified working:
- Context isolation: periodic-review memos use `MEMO_CONTEXT_KIND="periodic_review"` and never touch onboarding `compliance_memos` (`periodic_review_memo.py:14, 46–47`).
- EDD escalations from reviews carry `origin_context="periodic_review"` + `linked_periodic_review_id` (`periodic_review_engine.py:1386, 1405`); completed reviews are terminal and raise `ReviewClosedError` on re-escalation (`1352`) — no silent reroute into full onboarding.
- Next review date is policy-derived: LOW=36 / MEDIUM=24 / HIGH=12 / VERY_HIGH=6 months with a 12-month enhanced-monitoring floor (`periodic_review_policy.py:10–15, 262–314`).
- Periodic-review document requests have portal-visible status vocabulary and a back-office payload (`periodic_review_document_requests.py:21`; `server.py:24157–24162`).
- Approval automatically enrolls the client for monitoring/periodic review (`server.py:21588`, `_enroll_approved_application_for_monitoring`).

Defects:
| ID | Sev | Finding |
|---|---|---|
| C6-1 | **CRITICAL** | (= F-02, verified) `saveReviewSchedule()` (`arie-backoffice.html:24582–24588`) writes only local `REVIEW_SCHEDULE` JS variables and shows "✅ Review schedule saved — reminders will trigger accordingly". Nothing is persisted; backend cadence comes from `periodic_review_policy.py`. The backfill endpoint `PeriodicReviewScheduleHandler` (`server.py:23579`, route `27695`) is reachable from **no** UI element (`grep 'reviews/schedule' arie-backoffice.html` → 0 hits). The CLAUDE.md claim of a "Schedule Due Reviews" button is stale — no such button exists. |
| C6-2 | MEDIUM | Queue "active" classification for reviews checks status only (`lifecycle_queue.py:517–518`) whereas alerts also check resolution + quarantine (`467`) — reviews can present as actively blocked after their linked alerts are resolved. |
| C6-3 | LOW | `monitoring_automation.py:29` labels the sweep "Automatic due-review sweep"; it is automatic **only** where `automation_enabled()` is true (default: staging/production, `monitoring_automation.py:579–587`). In demo/development nothing schedules due reviews and the UI does not say so. |

### 5.5 Chain 9 — Approval Gate (and Chain 4 decisioning)

**Status: PASS — strongest area of the product.**

All gates verified enforced **server-side** in `ApplicationDecisionHandler` / `MemoApproveHandler`:

| Gate | Evidence |
|---|---|
| Role restriction (admin/sco/co) | `server.py:21242` |
| Terminal-state protection (409 on approved/rejected replay) | `server.py:21324–21335` |
| Memo existence + freshness | `server.py:21412–21425, 20579–20597` |
| Memo not rule-engine-blocked; validation pass/pass_with_fixes; supervisor not INCONSISTENT | `server.py:20608–20676` |
| Risk-based role limit (CO blocked on HIGH/VERY_HIGH) | `server.py:21401–21410` |
| Dual approval (distinct officers) for high risk | `server.py:21458–21494` |
| AI override: sco/admin only + mandatory reason + audited | `server.py:21359–21376, 20805–20806` |
| Rejection/escalation reasons mandatory; RMI requires items + deadline | `server.py:21352–21357, 21512–21528` |
| Decision audit rows with before/after state | `server.py:21637–21638` |
| "More info" round-trip (RMI → `rmi_sent` → client upload → reopen) | `server.py:21496–21569` |

### 5.6 Chain 7 — Monitoring Alerts

**Status: PASS.**

- Terminal/unresolved predicates are explicit and consistent (`monitoring_routing.py:148–161`); resolution stamps `resolved_at` and emits before/after audit (`lifecycle_linkage.py:318–334`).
- Queue materialisation preserves `application_id`, `linked_periodic_review_id`, `linked_edd_case_id` (`lifecycle_queue.py:454–490`); orphan/legacy rows are **quarantined visibly** (`is_legacy_unmapped: true`, reasons `VOCABULARY_GHOST`/`UNSCOPABLE`) rather than dropped (`lifecycle_quarantine.py`).
- Automation sweep registered as a Tornado `PeriodicCallback` with environment gating (`server.py:27987–28038`); startup failure raises in staging/production rather than silently degrading.

Defect: MEDIUM — `is_stale`/`stale_reason`/`stale_marked_at` alert columns exist in schema (`db.py:2188–2192`) but no code writes them; stale-alert detection is unimplemented.

### 5.7 Chain 8 — Change Management / Lifecycle

**Status: PASS with two MEDIUM caveats.**

- Before/after states captured on every transition (`change_management.py:588–589, 664–665, 936–937, 1046–1047, 1105–1106, 1231–1232`).
- Materiality tiers deterministic (`133–156, 368–378`); downstream approval requirements per tier.
- Profile versioning with stale-version conflict detection at request level (`321, 1151–1152`); entity field writes restricted to `SAFE_ENTITY_FIELDS` whitelist (`1400–1423`); no `INSERT OR REPLACE` / unconditional UPDATE found — no silent overwrite path.

Caveats: MEDIUM — person-level (director/UBO) writes lack the `base_profile_version_id` conflict re-check applied to entity fields; MEDIUM — risk recomputation triggered by changes has no dedicated audit event linking change-request → new score.

### 5.8 Chain 10 — Audit / Export

**Status: PASS.**

- Business audit rows include user, role, action, target, IP, before/after state (`base_handler.py:log_audit`); governance attempts (including **rejected** actions) are separately audited via `log_governance_attempt` (`base_handler.py:560–620`) — failed attempts are visible, which exceeds typical baselines.
- Supervisor audit log is hash-chained (v2 SHA-256 over all material fields, genesis `previous_hash=""`) with `verify_chain_integrity()` detecting tamper/deletion (`supervisor/audit.py:55–110, 486–545`). Chain hash fields are stripped from generic API payloads (`server.py:14219–14221`).
- Evidence pack: ZIP with `00_manifest.pdf`, screening summary, compliance memo, audit-trail CSV, per-file SHA-256 manifest and `zip_sha256`, redaction levels honoured (`evidence_pack_export.py:488–770`).

Gaps feeding this chain from upstream: webhook receipts (C2-3), document verification transitions (C1-3), and risk elevation (C1-1) do not write audit rows — the chain is intact but those events are absent from it.

---

## 6. Product Area Coverage (UI/UX answers)

| Area | Officer sees / can act | Verdict |
|---|---|---|
| Client Portal | Onboarding state, documents, RMI tasks, client-safe pricing; risk/memo/internal rationale stripped by `_client_safe_application_detail` (`server.py:4660`) | PASS (see §7 deny-list caveat) |
| Back Office Dashboard / Reports | KPI counts reconcile with canonical sources (covered by `test_phase4_reporting_evidence.py` — dashboard vs report buckets tested) | PASS |
| Applications / Application Review | List+detail agree on data; labels computed only on detail (C1-5) | PASS w/ LOW |
| Case Command Centre | Cards render; blocker source mismatch (C4-1, C4-4); memo card can go stale without refresh (C5-3) | **HIGH issues** |
| Case Management | Filters/terminal vocabularies explicit (`server.py:4692–4703`) | PASS |
| Screening Queue | States accurate; provider filter misleading (C2-1/C3-1); hit-count asymmetry (C3-4) | **CRITICAL issue** |
| Identity Verification visibility | Status, applicant ID, RED reasons shown; no history (C2-3); placement ambiguity (C2-4) | HIGH issues |
| KYC Documents | Sections render by doc type; verification states decorated; superseded-doc opacity in portal (C1-4) | PASS w/ MEDIUM |
| Enhanced Requirements / EDD | Transitions well-defined; stale case status on re-route (C4-2/C4-3) | PASS w/ MEDIUM |
| Memo / Validation / Supervisor | Full pipeline visible; gates enforced; ordering bug (C5-1) | PASS w/ HIGH |
| Approval / Rejection / More Info / Escalation | Strongest area — all gates server-side (§5.5) | **PASS** |
| Periodic Review Queue | Backend correct; schedule UI not wired (C6-1) | **CRITICAL issue** |
| Monitoring Alerts | Counts accurate, orphans quarantined visibly | PASS |
| Lifecycle / Change Management | Materiality, versioning, audit complete | PASS |
| User Management / Roles | bcrypt, rate limiting (`auth.py:140–205`), DB-revalidated sessions, password-change revokes tokens (`auth.py:80–99`) | PASS |
| Risk Scoring Model | Deterministic; field triplication (C1-1) | HIGH issue |
| AI Agents / AI Verification | Catalog truthfully scoped internally; truth not surfaced to officer review pages (C2-1) | MEDIUM |
| Audit Chain / Export Pack | Hash chain + SHA-256 manifests verified | PASS |
| Notifications | PRS-6 reminder sweep registered as PeriodicCallback (`server.py:28102`); writes notification metadata + audit only | PASS |
| Public pages / login | Officer + client login bcrypt-verified; rate limited; CSRF token issued on client login (`server.py:3051–3054`) | PASS |

---

## 7. Data Integrity & Security Findings

1. **Client redaction is a deny-list** (`CLIENT_APPLICATION_DETAIL_FORBIDDEN_KEYS`, `server.py:4600–4658`): coverage today is comprehensive (risk, memo, screening, periodic review, pre-approval, corrections all stripped), but the docstring's "fail closed" claim is inverted — any **new** officer-grade field added to the shared detail payload leaks to clients by default unless someone remembers to extend the set. Recommend inverting to an allow-list projection. **MEDIUM (architectural).**
2. **Auth model is sound**: per-request DB revalidation of token actors (`base_handler.py:440–487`) blocks deactivated users and role drift; `require_auth(roles=...)` used on sensitive handlers; client ownership asserted via `check_app_ownership` on detail/PUT and on document download (`server.py:9810`). 51 of 186 `require_auth()` calls pass no role list — most are intentionally dual-audience (portal+office) and guard with `user["type"]` checks (`server.py:3317, 5068–5107` etc.), but this pattern is convention-enforced, not structural. **LOW-MEDIUM.**
3. **No impossible screening states constructible** — fail-closed state machine verified (§5.1).
4. **Simulated provider fallbacks** are clearly annotated and excluded from defensible clears, but simulated AML/OpenCorporates/geolocation results still render in officer screens; ensure the "simulated" note is prominent in demo environments. **LOW.**
5. **Legacy/stale fields**: `risk_level` vs `final_risk_level` (F-06); unwritten `is_stale` alert columns (§5.6); `SCREENING_PROVIDER` env var read but ignored while abstraction is off (C3-1).

## 8. Integration Reality Check

| Integration | Actual state |
|---|---|
| Sumsub | Live for identity verification (applicant create, token, webhooks). AML calls (`screen_sumsub_aml`) depend on an **unproven entitlement**; simulated fallback without credentials. Must be labelled IDV-only in officer surfaces. |
| ComplyAdvantage | Scaffolded only (`screening_complyadvantage/`); `ENABLE_SCREENING_ABSTRACTION=False` everywhere; never invoked at runtime. Must not be presented as live. |
| OpenCorporates | Live when `OPENCORPORATES_API_KEY` set; annotated simulation otherwise (`screening.py:269–321`). |
| IP geolocation | ipapi.co with simulation fallback (`screening.py:370–420`). |
| Anthropic | Live; risk-routed Sonnet/Opus (`claude_client.py:984–989`); no fallback memo on failure (F-05). |
| S3 / PDF / Export | Present; export pack hash-manifested (§5.8). |
| Background processing | Tornado `PeriodicCallback`s: GDPR purge (daily), monitoring automation (staging/prod default), PRS-6 notifications (`server.py:27974–28102`). No external cron dependency. |

## 9. Prioritised Remediation

**P0 — before any regulated pilot demo**
1. F-01/C3-1: Remove ComplyAdvantage from the provider filter; relabel Sumsub entries on screening surfaces as "Identity Verification (Sumsub)"; add an explicit entitlement disclaimer on AML result cards.
2. F-02/C6-1: Wire `saveReviewSchedule()` to persist (or remove the Save button and label cadence read-only from policy); add a UI action invoking `POST /api/monitoring/reviews/schedule` showing created/updated/skipped counts.

**P1 — before pilot go-live**
3. F-04: Standardise memo selection to `ORDER BY version DESC, id DESC` at `server.py:20549, 20901`.
4. F-03: Make `reviewAnswer=RED` a pre-approval blocker or auto-RMI.
5. F-05: Invoke `generate_fallback_memo()` on AI failure with a mandatory manual-review flag, or surface a clear officer-facing outage banner.
6. F-06: Backfill `final_risk_level` from `risk_level` where NULL; audit-log risk elevation; single canonical accessor.
7. F-07: Have Case Command Centre fetch and render the same `gate_blockers` the validator enforces.
8. F-08: Append-only `sumsub_webhooks[]` history + audit row per webhook.

**P2 — hardening**
9. C3-2/C3-3: enforce memo prerequisite and four-eyes at disposition submission. 10. C4-2/C4-3: EDD state-machine reset on re-route; orphan-case sweep. 11. Stale-alert sweep writing `is_stale`. 12. Person-level version-conflict check in change management; risk-recomputation audit event. 13. Invert client redaction to allow-list. 14. C1-2/C1-3/C1-4, C2-4/C2-5, C3-4, C4-4, C5-3, C6-2/C6-3 as scheduled debt.

## 10. Documentation Corrections

- CLAUDE.md states periodic reviews are scheduled "via the manual 'Schedule Due Reviews' button in back office" and that no scheduler exists. **Both are stale**: no such button exists in `arie-backoffice.html`, and a monitoring-automation `PeriodicCallback` does run the due-review sweep by default in staging/production (`server.py:27987`; `monitoring_automation.py:579–587`).
- CLAUDE.md test count ("206 tests") is stale; the suite is 5,222 tests as of this audit.

---

*Audit performed by static analysis with line-level citations; all CRITICAL/HIGH findings were independently re-verified against source before inclusion. No live provider calls were made.*
