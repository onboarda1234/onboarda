# RegMind Compliance Process Configuration and Control Memo

**Classification:** Internal — Compliance Governance
**Prepared for:** Compliance function / MLRO / Head of Compliance / Board-level reviewer
**Prepared by:** Engineering (on behalf of the RegMind platform team)
**Environment of record:** AWS ECS Fargate — **staging** (`staging.regmind.co`, af-south-1)
**Source of truth:** `origin/main` at commit `4c172a3` (reconciled against `docs/REMEDIATION_MASTER_LIST.md`)
**Date:** 2026-07-08
**Status:** DRAFT for compliance review — not yet signed off

---

> **Purpose and standing of this document.** This is a compliance governance memo, not
> marketing material and not a product brochure. Its purpose is to describe — accurately
> and defensibly — the automated and semi-automated compliance processes implemented in
> RegMind so that Compliance can **review, challenge, approve, or require changes** before
> any controlled pilot use. Every material claim is anchored to a source file, function,
> handler, or test in the repository. Where a control is *proposed* or *scoped* but not yet
> in the codebase, or where behaviour is *inferred* rather than code-proven, it is labelled
> as such. Nothing in this memo should be read as a statement of production readiness; the
> platform's own audit roll-up rates production readiness at approximately 30–35% and
> controlled-pilot readiness at approximately 88–92%.

---

## 1. Executive summary

RegMind is the internal back-office compliance engine that sits behind the client-facing
Onboarda portal. It runs a **deterministic** four-layer decision pipeline (rule engine →
compliance memo → validation → supervisor). The memo you are reading covers the thirteen
process areas that carry regulatory weight.

**What is genuinely strong (implemented and test-locked):**

- **Deterministic risk scoring** with sanctioned/FATF country floors, PEP elevation, and a
  **decision-time stale-risk gate** that blocks approvals computed against an out-of-date
  risk configuration (`rule_engine.compute_risk_score`, `server._application_risk_staleness_error`).
- **A layered approval gate** that blocks approval on stale/simulated/incomplete screening,
  failed identity verification, unresolved documents, and stale memos
  (`security_hardening.ApprovalGateValidator.validate_approval`).
- **Fail-closed persistence** on the highest-stakes actions: a final application decision, a
  memo validation, and a memo approval **cannot report success if the database write fails**
  — the transaction rolls back and returns an error rather than a false positive
  (`tests/test_fail_closed_decision_persistence.py`).
- **Four-eyes controls** in the three places that most need them: HIGH/VERY_HIGH dual
  approval (distinct second officer), screening second-review, and EDD case closure
  (distinct senior reviewer/closer).
- **A tamper-evident hash chain on the supervisor verdict log**, with an evidence-pack
  export a regulator can independently recompute per row.

**What Compliance must consciously accept or reject (policy decisions, not defects):**

- **LOW/MEDIUM fast-path:** eligible LOW *and* MEDIUM cases can be approved by a single
  Onboarding Officer **without a compliance memo**. This is a deliberate, already-signed
  policy exception (`docs/compliance/LOW_MEDIUM_FASTPATH_APPROVAL_POLICY.md`), enforced in
  code with disqualifiers. Compliance should confirm continued acceptance.
- **Tier 2 change-management maker-checker relaxation:** only Tier 1 profile changes now
  require maker-checker; Tier 2 self-approval is permitted **after** screening/risk
  compensating controls clear.
- **Action-ownership is proposed, not built:** there is **no code-enforced "acting owner"
  gate** on sign-off actions today. Any officer with the correct role (subject to risk tier
  and four-eyes) can execute a terminal decision regardless of case assignment.

**What must not be relied upon (disabled / unremediated / ops-pending):**

- **SAR/STR is disabled by default in every environment** and carries unremediated
  permanence defects (cascade delete, mutable content). It must **not** be enabled for
  operational reliance until those are fixed.
- **Platform-wide audit tamper-evidence is not complete:** only the supervisor verdict chain
  is wired; the general `audit_log` (where officer sign-offs, decisions, screening reviews,
  and overrides are recorded) is **not yet hash-chained**.
- **Several Audit-3/BSA remediations exist only in open, unmerged PRs** (DB pre-ping,
  migration failure-mode restriction, sanctioned-floor memo correction) and are therefore
  **not present on `origin/main`** / staging.
- **Production is not provisioned or validated.** All evidence in this memo is staging-only.

The remainder of this memo takes each process area in turn using a fixed nine-point
structure, then presents a Compliance Decision Register, a Control Matrix, a Residual Risk
Register, a sign-off block, and the explicit questions Compliance must answer.

---

## 2. How to read each process area

Each of the thirteen sections below answers the same nine questions:

1. **Purpose** — what the process is for.
2. **Trigger / when it applies.**
3. **System rule or workflow** — what the code actually does (with citations).
4. **Roles permitted to act.**
5. **Approval / maker-checker / override logic.**
6. **Evidence and audit trail generated.**
7. **Human judgment retained.**
8. **Known limitations / residual risks.**
9. **Compliance decision required (if any).**

Status vocabulary: **Implemented** (in code on `main` + tested) · **Partially implemented**
· **Proposed / scoped** (designed, not in code) · **Disabled** · **Pending** ·
**Policy decision required**.

---

## Process Area 1 — Application ownership and sign-off control (PR-APP-ACTION-OWNERSHIP)

**Overall status: PROPOSED / NOT IMPLEMENTED.** This section describes a *design intent*
that Compliance is being asked to endorse in principle; it is **not** live in the code today.

**1. Purpose.** To ensure that terminal, sign-off-grade actions on an application (final
approve/reject, pre-approval approve/reject, and memo approval) are executed by the officer
who owns/is assigned the case — or by a supervisor exercising an audited override — while
leaving genuinely collaborative preparation work open to any authorised officer so files can
be progressed efficiently.

**2. Trigger / when it applies.** Would apply at the moment an officer attempts a terminal
sign-off action on an application. Collaborative actions (document requests, screening
review, EDD escalation, memo generation/validation/supervisor review) would remain open and
ungated by ownership.

**3. System rule or workflow (intended vs actual).**
- *Intended (proposed):* the first gated sign-off action on an **unassigned** case
  auto-assigns the acting officer and records an audit event; a **non-owner** CO/analyst
  cannot perform a gated sign-off action on an assigned case; **admin/SCO** may override with
  an audit-marked override reason.
- *Actual (code today):* **none of this ownership logic exists.** The terminal-decision
  authority gate `can_decide_application` (`security_hardening.py:2094`) evaluates
  **role × current-risk × decision × override only** — it reads `user["role"]` and never
  compares the actor to any `assigned_to`/owner. The decision endpoint
  `ApplicationDecisionHandler.post` (`server.py:31344`) and the memo-approval handler
  (`MemoApproveHandler`, `server.py:30186`) authorise purely on role and gate state. No
  `acting_officer`/`auto_assign`/ownership-`override_reason` concept exists anywhere in the
  codebase. The identifiers `PR-APP-ACTION-OWNERSHIP`/`FEO-013` do not appear in code.
- **Important disambiguation:** `applications.assigned_to` exists **only** as a workqueue
  field (queue filtering and reassignment, `server.py:5124`, `8245`), not an authority
  control. The `check_app_ownership` method (`base_handler.py:837`) and the
  `test_r8/r9/r10_*_ownership.py` tests are **client/tenant isolation** (a portal client may
  only see its own application) — they have nothing to do with officer sign-off ownership.

**4. Roles permitted to act (today).** Any officer holding the correct role for the risk tier
(see Area 2) can execute a terminal decision on any case, regardless of assignment.

**5. Approval / maker-checker / override logic.** The only person-binding control that exists
today is **four-eyes distinct-officer** on HIGH/VERY_HIGH approvals
(`validate_high_risk_dual_approval`, `security_hardening.py:2025`; same-officer retry returns
`DUAL_SAME_OFFICER`). This is identity-distinctness, **not** ownership.

**6. Evidence and audit trail generated.** If built, the expected audit events would be an
auto-assignment record and an override record with reason. Today, decisions are audited
(Area 12) but with **no ownership dimension**.

**7. Human judgment retained.** Full — every terminal decision is officer-initiated.

**8. Known limitations / residual risks.** The named-owner control described in the pilot
runbook is **manual, not code-enforced** (this is exactly audit finding FEO-013). Two people
with the same role can each act on the same case; there is no code binding a decision to a
designated owner. Even if built, the proposed scope is deliberately narrow — **terminal
decision + memo-approval ownership only** — and collaborative workflow actions (documents,
screening, EDD escalation, memo prep) would remain open by design.

**9. Compliance decision required.** (a) Endorse or reject the **proposed scope** ("terminal
decision and memo-approval ownership", *not* full owner-gated workflow). (b) Decide whether
the named-owner control may operate as a **manual/procedural** control during the pilot, or
whether code enforcement is a pilot precondition.

---

## Process Area 2 — Role and approval authority model

**Overall status: IMPLEMENTED.**

**1. Purpose.** To separate who may *view and operationally review* a file from who may give
*final regulatory sign-off*, and to escalate authority as risk rises.

**2. Trigger / when it applies.** On every authenticated back-office action; enforced hardest
at the decision endpoint.

**3. System rule or workflow.**
- Officer role set: `_OFFICER_ROLES = {"admin", "sco", "co", "analyst"}`
  (`base_handler.py:35`). Auth via `require_auth`/`require_backoffice_auth`
  (`base_handler.py:536`, `569`); role denials audited as `authz_denied_role`.
- Terminal-decision authority: `DECISION_AUTHORITY_ROLES = ("admin", "sco", "co")`
  (`security_hardening.py:2088`) — **analyst and client are excluded from decisions**.
- `co` is the **Onboarding Officer** (naming locked by
  `tests/test_role_naming_onboarding_officer_static.py`).
- Route classifier `classify_approval_route` (`security_hardening.py:997`) separates risk
  policy from operational readiness and yields one of `direct_low_medium`,
  `compliance_required`, `dual_control_required`.

**4. Roles permitted to act.**
- **View:** all four officer roles, including `analyst`.
- **Decide (terminal):** `admin`/`sco`/`co` only. **Analyst is read-only for decisions**
  (403 at `can_decide_application`, `security_hardening.py:2169`).

**5. Approval / maker-checker / override logic.**
- **LOW/MEDIUM, clean** → route `direct_low_medium`; **an Onboarding Officer (CO) may
  approve directly** (`DIRECT_APPROVAL_RISK_LEVELS = {"LOW","MEDIUM"}`,
  `security_hardening.py:744`).
- **LOW/MEDIUM with an escalation reason** (declared PEP, adverse media, EDD lane,
  material screening concern, officer-submitted-to-compliance) → route
  `compliance_required`; **CO is blocked**, must route to compliance/SCO
  (`security_hardening.py:2192`).
- **HIGH/VERY_HIGH** → route `dual_control_required`; **CO cannot approve**; only
  `admin`/`sco`, and **dual approval by two distinct officers** is required
  (`security_hardening.py:2180`, `2025`; first approval returns `202 first_approval_recorded`,
  distinct second → approved, same officer → `409`, enforced at `server.py:31721`).
- **AI override is senior-only** (`sco`/`admin`; `security_hardening.py:2209`).
- Admin high-risk approval is extra-audited (`is_privileged_admin_action`) but passes the
  **same** gate — no shortcut.
- Fast-path **disqualifiers are enforced in code** (`_approval_escalation_reasons`,
  `security_hardening.py:920`, and `validate_approval`, `1340`): sanctioned/watchlist
  completed-match without disposition (fail-closed), stale/expired screening, failed/unresolved
  IDV, screening second-review pending, unresolved documents/enhanced requirements, and stale
  memo all block approval. Tests: `tests/test_approval_gate.py`,
  `tests/test_e2e_authority_matrix.py`.

**6. Evidence and audit trail generated.** Role denials (`authz_denied_role`), an approval
gate snapshot recorded in the decision record `extra` (`server.py:31709`), and the
first-approval / dual-approval records for HIGH/VERY_HIGH.

**7. Human judgment retained.** Full. The system classifies and gates; a human officer of the
required seniority makes the decision.

**8. Known limitations / residual risks.** The LOW/MEDIUM fast-path is a deliberate policy
exception (see Area 6). The role model is role-based, not ownership-based (Area 1).

**9. Compliance decision required.** Confirm the authority matrix (especially CO-alone
approval of MEDIUM cases on the fast-path) is an approved compliance policy — this is
already documented and signed, but should be re-affirmed at MLRO/board level.

---

## Process Area 3 — Risk scoring and risk configuration

**Overall status: IMPLEMENTED (deterministic), with named residuals.**

**1. Purpose.** To produce a defensible, reproducible customer/application risk rating that
drives routing, EDD, and approval authority.

**2. Trigger / when it applies.** At submission/memo generation and on every recompute
(config change, material change, screening disposition, EDD routing).

**3. System rule or workflow.**
- Core scorer `rule_engine.compute_risk_score` (`rule_engine.py:923`) — a weighted five-
  dimension model (D1 customer/entity incl. PEP, source of funds/wealth; D2 geographic;
  D3 product/service; D4 industry/sector; D5 delivery channel), normalised 0–100.
- Levels via `classify_risk_level` (`rule_engine.py:900`): **LOW 0–39.9 · MEDIUM 40–54.9 ·
  HIGH 55–69.9 · VERY_HIGH 70–100** (canonical thresholds).
- **Deterministic, not AI:** the memo pre-generation layer re-derives ratings and records
  rule enforcements that "CANNOT be overridden by AI" (`memo_handler.py:1749`).
- **Country/sanctions/FATF floors:** sanctioned/FATF-black incorporation country forces
  VERY_HIGH (`compute_risk_score` FLOOR RULE 1, `rule_engine.py:1248`); sanctioned
  UBO/director nationality forces VERY_HIGH (FLOOR RULE 2, `1268`). Manual
  `risk_config.country_risk_scores` is the authoritative pilot source (PR-CR1R); unknown/
  missing country fails safe to **MEDIUM, never LOW** (`1258`).
- **PEP floor/elevation** and **EDD-policy floor** (declared PEP / high-risk sector /
  elevated jurisdiction / opaque ownership cannot remain LOW → HIGH floor, `1357`).
- **Multiple-gap escalation** in the memo (`MULTI_GAP_ESCALATION`, `memo_handler.py:1911`).
- **Recompute + versioning:** `recompute_risk` (`rule_engine.py:1693`) stamps
  `risk_config_version`; batch `recompute_risk_for_active_apps` (`1900`) **quarantines**
  failures with sentinel `stale:recompute_failed` in the same transaction as the config save.
- **Decision-time stale-risk gate:** `_application_risk_staleness_error` (`server.py:626`)
  blocks approval when the stored `risk_config_version` differs from current or is the
  quarantine sentinel, and **fails closed if the current version cannot be read**. Tests:
  `tests/test_risk_staleness_gate.py`.
- **Config governance:** `RiskConfigHandler` (`server.py:14256`) — **only `admin` may write
  the risk model** (`SENSITIVE_CONFIG_WRITE_ROLES = ["admin"]`). `_validate_risk_model_semantics`
  (`14280`) requires exactly the five dimensions, dimension weights summing to **exactly
  100**, subcriteria summing to 100, and ordered thresholds; saving recomputes all active
  apps and returns an honest failure summary.

**4. Roles permitted to act.** Read of the risk model is gated to sensitive-config readers;
**write is admin-only**. Scoring itself is automatic.

**5. Approval / maker-checker / override logic.** A risk-model change is an admin-only
sensitive-config write with semantic validation; there is **no second-approver (maker-checker)
on the risk-model change itself** in code — a residual for Compliance to weigh.

**6. Evidence and audit trail generated.** Recompute writes before/after audit state;
config save records a version stamp (microsecond precision to avoid collisions,
`server.py:14475`) and a recompute summary.

**7. Human judgment retained.** Officers cannot approve on a stale score; a human still makes
the approval decision. Risk-model calibration is a human admin action.

**8. Known limitations / residual risks (code-proven).**
- The **memo-side** `SANCTIONED_COUNTRY_FLOOR` block (`memo_handler.py:1757`) records a rule
  *enforcement entry* but does **not itself mutate `jur_rating` to VERY_HIGH** at that point;
  the authoritative VERY_HIGH mutation lives in the rule engine floor. The memo correction
  (Audit-3 DCI-010 / P12-3) exists only in **open PR #710, not merged** to `main`.
- **`MULTI_GAP_ESCALATION` branch order:** an `if/elif` means a LOW base with ≥4 critical
  gaps escalates only to **MEDIUM** (the ≥4→HIGH branch is reachable only from a MEDIUM
  base). Corrected in unmerged PR #710.
- **Score-map validator laxity:** the admin PUT path rejects booleans, but the lower-level
  `rule_engine.validate_score_map` accepts any `int/float` **including `bool`** — only the
  admin path is strict.
- **Missing `risk_config_version`** on legacy rows is not blocked until the next config
  sweep re-stamps them (documented residual).
- **Empty/missing config** falls back to hardcoded weights at scoring time (warn, not
  fail-closed at scoring); fail-closed happens at the decision gate, not at scoring.

**9. Compliance decision required.** (a) Approve the risk model, thresholds, and country
lists as the pilot baseline, and the **admin-only, single-approver** change process (decide
whether a second reviewer should be required for risk-model changes). (b) Note that the
sanctioned-floor memo correction and multi-gap fix are **not yet on main** — decide whether
merging them is a pilot precondition.

---

## Process Area 4 — Screening rules and screening approval process

**Overall status: IMPLEMENTED.**

**1. Purpose.** To screen parties against sanctions/PEP/adverse-media sources and to control
how a screening result is cleared before it can support an approval.

**2. Trigger / when it applies.** On submission and on demand; screening review occurs when
an officer dispositions a result.

**3. System rule or workflow.**
- **Providers:** ComplyAdvantage Mesh (AML/sanctions/PEP/adverse media, active when
  `SCREENING_PROVIDER=complyadvantage` + abstraction enabled + credentials present) and
  Sumsub (identity verification). Provider constants in `screening_provider.py`.
- **Review workflow:** `ScreeningReviewHandler` (`server.py:22998`); dispositions normalised
  to cleared/escalated/follow_up_required with a required rationale; every action logs a
  governance attempt and an audit row carrying provider + evidence references.
- **Two-reviewer / four-eyes:** triggered by sensitivity flags
  (`requires_four_eyes = bool(sensitivity_flags)`, `server.py:23177`). The second reviewer
  **must differ from the first and must be SCO/admin** (`23196`); a clearance is not complete
  while four-eyes is pending.
- **Second-review-pending blocker (fail-closed):** `screening_second_review_pending_summary`
  (`security_hardening.py:419`), wired at `server.py:29225`, **returns blocked = True if the
  lookup throws**.
- **Adverse/sanctions/PEP/true-match treatment:** blocking dispositions (`true_match`,
  `material_concern`, `escalated_to_edd`, `needs_more_information`) floor risk and set the
  EDD lane (`rule_engine.py:1522`); raw completed-match without a complete clearance also
  floors to HIGH; `false_positive_cleared` passes only with evidence + a distinct second
  reviewer. Proven in `tests/test_screening_clearance_validation_supervisor.py`.
- **Live vs simulated / production guard:** in production, `screening_mode != 'live'` **blocks
  approval** (`security_hardening.py:1397`). Missing/simulated/non-terminal/stale/unsafe
  screening blocks direct approval (`collect_approval_gate_blockers`, `security_hardening.py:2221`).
- **Freshness/expiry** via `screening_valid_until` and an "inputs modified after screening"
  check.

**4. Roles permitted to act.** Screening review is open to `admin`/`sco`/`co`/`analyst`;
the four-eyes second review is restricted to `sco`/`admin`. **It is deliberately not
owner-gated** — the handler authorises on role only and has its own independent two-reviewer
control (this is the intended design per your brief, and confirmed in code:
`ScreeningReviewHandler.post`, `server.py:23000`).

**5. Approval / maker-checker / override logic.** Four-eyes second review for sensitive
clearances; false-positive clearance restricted by role; a failed EDD escalation during
review **rolls back the whole review** (fail-closed, `server.py:23294`).

**6. Evidence and audit trail generated.** Governance attempts (accepted/rejected with status
code) and audit rows with ComplyAdvantage event type + evidence references
(`server.py:23324`); approval blocks by second review are separately audited.

**7. Human judgment retained.** Full — an officer dispositions every result; a second officer
confirms sensitive clearances.

**8. Known limitations / residual risks.**
- **No external adverse-media API.** Adverse-media signals are **parsed from the screening
  provider's payload and monitoring alerts** (`screening_adverse_truth.py`); there is no
  independent external adverse-media search integration and no `ADVERSE_MEDIA_API_KEY`. The
  back office correctly notes distinct adverse-media results are not separately persisted.
- **Provider trust:** sanctions/PEP/adverse determinations rely on the provider's returned
  status and flags; the system enforces "live, not simulated" and terminality but cannot
  validate the provider's underlying data quality. Officer disposition + four-eyes is the
  compensating control.
- **Simulated screening is tolerated outside production** (the live-mode guard is conditioned
  on `is_production()`).
- Four-eyes is **conditional** on sensitivity flags; under-flagging a subject could skip it.

**9. Compliance decision required.** (a) Confirm that screening clearance is correctly left
**outside** the (proposed) ownership gate because it has its own two-reviewer control.
(b) Accept the "no external adverse-media provider" limitation for the pilot, or require one
before go-live. (c) Confirm the production live-screening guard and simulated-outside-prod
posture.

---

## Process Area 5 — Compliance memo process

**Overall status: IMPLEMENTED (deterministic path); Claude memo path DISABLED.**

**1. Purpose.** To produce a structured compliance decision paper that ties the risk score,
screening truth, documents, and EDD routing to a recommendation, and to gate its approval.

**2. Trigger / when it applies.** On officer request (`POST /api/applications/:id/memo`),
and re-derived when material facts change (freshness).

**3. System rule or workflow.**
- Generation: `ComplianceMemoHandler.post` (`server.py:28484`) → deterministic builder
  `memo_handler.build_compliance_memo` (`memo_handler.py:1201`). `ai_source` is
  `"deterministic"` (or `"demo"` in demo mode) (`memo_handler.py:2514`).
- **AI content:** the Claude memo integration (`claude_memo_integration.py`) is **OFF by
  default** (`ENABLE_CLAUDE_MEMO` unset) and intentionally not wired into the handler;
  `maybe_generate_claude_memo` fails closed to the deterministic path. The risk-based model
  routing (`claude_client.select_memo_model`, `claude_client.py:985`: LOW/MEDIUM → Sonnet,
  HIGH/VERY_HIGH or score ≥ 55 → Opus) exists but is **not on the live path**.
- **Deterministic pre-generation rules** bind the recommendation to the truth: screening
  that blocks approval downgrades APPROVE→REVIEW; EDD route binds to ESCALATE_TO_EDD;
  supervisor veto binds to REVIEW (`memo_handler.py:2647`, `3186`, `3256`).
- **Validation engine:** `validation_engine.validate_compliance_memo` (`validation_engine.py:182`)
  → `pass` / `pass_with_fixes` / `fail`.
- **Supervisor contradiction engine:** `supervisor_engine.run_memo_supervisor`
  (`supervisor_engine.py:82`) → `CONSISTENT` / `CONSISTENT_WITH_WARNINGS` / `INCONSISTENT`,
  with an 11-check contradiction set (risk-vs-decision, ownership-vs-rating,
  PEP-vs-screening, declared-PEP-vs-narrative).
- **Blocked verdict persisted** (#679): `blocked`/`block_reason` columns written at
  generation (`server.py:_memo_block_columns`, `5646`); the approval gate honours the
  persisted verdict.
- **Freshness/invalidation:** `_ensure_memo_fresh_or_mark_stale` (`server.py:28355`)
  recomputes an input hash and marks stale (409) on divergence; freshness-unverifiable fails
  closed to stale.
- **Approval gate (owner-gated in the proposed model):** `MemoApproveHandler.post`
  (`server.py:30186`), **roles `admin`/`sco` only**, applies five gates (persisted-blocked;
  validation status; fallback/mandatory-escalation/EDD-route/supervisor-verdict; SCO-review).

**4. Roles permitted to act.** Generation/validation/supervisor review: `admin`/`sco`/`co`/
`analyst` (analytical preparation, left open). Memo **approval**: `admin`/`sco` only.

**5. Approval / maker-checker / override logic.** `pass_with_fixes` and
`CONSISTENT_WITH_WARNINGS` are approvable **only by admin/sco with a documented reason**
(EX-06). Memo approval is a **sign-off control** and is where the proposed action-ownership
gate would apply.

**6. Evidence and audit trail generated.** Officer sign-off audit row on approval
(`_persist_signoff_audit`), governance attempts, and the persisted memo status/verdict.

**7. Human judgment retained.** Full — the memo is advisory/structuring; a senior officer
approves.

**8. Known limitations / residual risks.**
- **Fail-closed persistence is implemented and test-locked** (RDI-007/011, #698): memo
  validation and memo approval **cannot fake success if persistence fails** — they roll back,
  re-log a REJECTED governance attempt, and return **500** ("has NOT been approved/recorded —
  retry"). Tests: `tests/test_fail_closed_decision_persistence.py`.
- **Residual (RDI-009 / P10-5):** the memo supervisor is distinct from the enterprise
  supervisor pipeline's `decision_records` overlay; the overlay is written **best-effort**
  and is scoped separately (see Area 12).

**9. Compliance decision required.** Confirm that (a) memo approval is correctly owner/
supervisor-gated in the proposed model, and (b) memo generation/validation/supervisor review
are acceptable to leave **open** as analytical preparation steps.

---

## Process Area 6 — Final application decision process

**Overall status: IMPLEMENTED for approve/reject (fail-closed, atomic); WEAKER for
escalate_edd / request_documents.**

**1. Purpose.** To record the terminal disposition of an application — approve, reject,
request documents, or escalate to EDD — with a complete, atomic evidence trail.

**2. Trigger / when it applies.** `POST /api/applications/:id/decision`
(`ApplicationDecisionHandler.post`, `server.py:31344`). Terminal status changes are forced
through this endpoint — a generic `PATCH` **cannot** set approved/rejected
(`server.py:8078`, `tests/test_patch_decision_bypass.py`).

**3. System rule or workflow (approve path gate order).**
1. Authority gate `can_decide_application` (role × risk × override × route).
2. Risk-staleness gate `_application_risk_staleness_error`.
3. Memo package required for escalated routes only (LOW/MEDIUM direct route intentionally
   skips the memo package).
4. Screening second-review block.
5. Memo freshness / mark-stale.
6. `ApprovalGateValidator.validate_approval` (full precondition stack).
7. Document reliance gate `evaluate_document_reliance_gate`.
8. HIGH/VERY_HIGH dual approval.

**Fail-closed atomic persistence (IMPLEMENTED — RDI-001/007/011, #698):** status update +
monitoring enrollment + audit_log "Decision" row + override row (if any) + officer sign-off
audit + normalized `decision_records` row + governance attempt commit in **one transaction**
(`server.py:31840`–`31996`); any exception rolls back and returns 500 — **a final decision
can never commit without its `decision_records` row**. `decision_model.save_decision_record`
**raises** on insert failure (`decision_model.py:142`), no log-and-continue. Tests:
`tests/test_fail_closed_decision_persistence.py`.

**4. Roles permitted to act.** Approve/reject/escalate: `admin`/`sco`/`co` (subject to risk
tier for approve). Analyst and client cannot decide.

**5. Approval / maker-checker / override logic.** HIGH/VERY_HIGH dual approval (distinct
officers); AI override senior-only; sign-off mandatory on every terminal decision.

**6. Evidence and audit trail generated.** Decision audit row, override row, sign-off audit,
normalized decision record, governance attempt, and an approval-gate snapshot embedded in the
decision record.

**7. Human judgment retained.** Full.

**8. Known limitations / residual risks (code-proven asymmetry).**
- **`escalate_edd` and `request_documents` have materially weaker prerequisites than
  approve/reject.** They do **not** call `can_decide_application` and do **not** run
  `ApprovalGateValidator`. For `escalate_edd`, missing/zero risk is only a **non-blocking
  warning**, and there is no authority-matrix, document, or senior-reviewer enforcement at the
  escalation step (those live on EDD case *closure*, Area 8). This is the RDI-003/008 / P10-4
  residual — **the per-decision-type gates are scoped, not implemented.**
- **Reject** deliberately does not run the full approval precondition stack (correct — an
  incomplete file can be rejected).
- The **LOW/MEDIUM direct route** intentionally skips the compliance-memo package (policy
  exception, Area 2).

**9. Compliance decision required.** (a) Accept, for the pilot, that `escalate_edd` /
`request_documents` are lower-friction by design, **or** require the P10-4 per-type
prerequisite gates first. (b) Confirm the LOW/MEDIUM memo-skip policy at decision level.

---

## Process Area 7 — Document collection and document review

**Overall status: IMPLEMENTED (with an honest taxonomy caveat).**

**1. Purpose.** To collect, verify, and rely upon KYC/KYB documents, and to block sign-off
until required evidence is present and clean.

**2. Trigger / when it applies.** On client/officer upload; on officer document requests
(RMI); and at every memo/decision gate that consumes document reliance.

**3. System rule or workflow.**
- Upload/versioning: `DocumentUploadHandler` (`server.py:10839`) with `is_current`/superseded
  columns.
- Officer document request / RMI: at the decision endpoint, `request_documents` sets status
  `rmi_sent`, requires ≥1 RMI item + a deadline, and creates a structured request
  (`server.py:31759`). RMI documents can alias back to the original required KYC slot
  (`document_reliance_gate.resolve_rmi_replacement_slot`).
- Review: `DocumentReviewHandler.post` (`server.py:12434`), statuses
  pending/accepted/rejected/info_requested; rejection requires a comment; **accepting a
  non-VERIFIED document requires admin/sco + a documented reason**, audited as "Document
  Accepted With Findings".
- Verification matrix: `verification_matrix.py` classifies checks as rule / hybrid / ai.
- **Document evidence gate:** `evaluate_document_reliance_gate` (`document_reliance_gate.py:753`)
  — a document may be relied upon only if `verified`/`manual_accepted` with clean verification
  evidence, a `verified_at`, not stale (365-day default), and Agent-1 execution proof. Enforced
  at memo generation, validation, and approval.

**4. Roles permitted to act.** Document request/review is open to authorised officers
(`admin`/`sco`/`co`); manual acceptance of a non-verified document is restricted to
`admin`/`sco`. **Document requests are intentionally left open** so officers can complete
files efficiently — a document request is **not** a final acceptance decision.

**5. Approval / maker-checker / override logic.** Senior (admin/sco) manual-acceptance gate
with a mandatory reason and before/after audit.

**6. Evidence and audit trail generated.** Every review action is audited with before/after
state; RMI request/item status is synced and audited.

**7. Human judgment retained.** Officers accept/reject documents and request more information;
seniors approve manual acceptances.

**8. Known limitations / residual risks.**
- **Taxonomy caveat (code-proven):** the marketing/CLAUDE.md framing lists checks as "Format,
  Authenticity, Expiry, Name Match, Tampering", but in code the **hard** checks are Format
  (rule), Name Match (hybrid), and Expiry/recency (rule); **Authenticity and Tampering are
  advisory-only escalation signals, never a hard fail** (`verification_matrix.py:29`,
  `document_verification.py:26`). There is **no deterministic anti-tamper hard-fail**.
- The reliance gate is **scoped to onboarding/KYC document slots** and does not model EDD,
  change-management, periodic-review, or monitoring evidence (those use separate paths).

**9. Compliance decision required.** (a) Accept that authenticity/tampering are advisory-only
for the pilot, or require a hard anti-tamper control. (b) Confirm the 365-day verification
staleness window and the senior manual-acceptance policy.

---

## Process Area 8 — Enhanced due diligence and EDD escalation

**Overall status: IMPLEMENTED (deterministic routing + dual-control closure); escalation
entry point is weaker than approval.**

**1. Purpose.** To route higher-risk cases into a documented enhanced-investigation lifecycle
with senior oversight.

**2. Trigger / when it applies.** Deterministically from risk/PEP/sector/jurisdiction/
ownership/screening facts, and by officer escalation at the decision endpoint.

**3. System rule or workflow.**
- Routing: `edd_routing_policy.evaluate_edd_routing` (versioned `edd_routing_policy_v1`),
  **fail-closed** — an incomplete fact contract routes to EDD. Called from the memo builder.
- Actuation/completion: `edd_actuation.py` upserts an EDD case and flips status to
  `edd_required`; `edd_completion.py` recognises completion (read-only, never commits) and is
  checked at memo generation and approval.
- **EDD case dual-control:** senior reviewer must be an active `sco`/`admin`
  (`_edd_senior_reviewer_error`, `server.py:37027`) and **must differ from the assigned
  officer** (`37309`); closure (`edd_approved`/`edd_rejected`) requires an `sco`/`admin`
  closer who **also differs from the assigned officer** plus a `decision_reason` (`37313`),
  audited as "EDD Closure (dual-control)". `sla_due_at` is captured; structured findings live
  on `edd_findings`.

**4. Roles permitted to act.** Escalate: `admin`/`sco`/`co`. Senior review/closure:
`sco`/`admin` (distinct from the assigned officer). **EDD escalation is intentionally left
open** because escalation *tightens* risk handling.

**5. Approval / maker-checker / override logic.** EDD closure is a genuine four-eyes control
(distinct senior reviewer and distinct closer).

**6. Evidence and audit trail generated.** EDD routing audit, actuation record, structured
findings, closure audit with assigned-officer/senior-reviewer/closer/reason, and SLA data.

**7. Human judgment retained.** Full — senior officers investigate, review, and close.

**8. Known limitations / residual risks.** The **escalation entry point** at the decision
endpoint has **weaker prerequisites than approval** (no authority matrix, no document gate,
missing/zero risk is warning-only). This is acceptable *because escalation increases scrutiny*,
but Compliance should note the asymmetry (RDI-003/008 / P10-4, scoped).

**9. Compliance decision required.** Confirm that leaving EDD escalation open (with weaker
prerequisites than approval) is acceptable because it tightens, never loosens, risk handling.

---

## Process Area 9 — Change management / customer profile changes

**Overall status: IMPLEMENTED (server-side materiality + Tier-1 maker-checker); one HIGH
recompute item unmerged.**

**1. Purpose.** To classify and control changes to an onboarded customer's profile and to
trigger the right downstream re-checks.

**2. Trigger / when it applies.** Client portal change requests and back-office change
requests.

**3. System rule or workflow.**
- Module `change_management.py`; back-office and portal handlers in `server.py` (`38007`+,
  `39440`).
- **Server-side materiality (RDI-006 / P10-1, #697):** `classify_materiality(change_type)`
  (`change_management.py:583`) maps change type → Tier 1/2/3; **client-supplied materiality is
  ignored** (the create function accepts no materiality parameter; overall materiality is the
  highest server-computed item tier).
- **Downstream actions** (`DOWNSTREAM_ACTION_MAP`, `change_management.py:294`): Tier 1 →
  screening + risk review + memo addendum + periodic-review acceleration; Tier 2 → screening +
  risk review; Tier 3 → none.
- **Maker-checker (#704, Tier-1-only):** `MAKER_CHECKER_TIERS = frozenset({"tier1"})`
  (`change_management.py:1327`) — non-waivable, no break-glass. **Tier 2 no longer requires
  maker-checker** and may be self-approved **after** screening/risk compensating controls
  clear; Tier 2 screening/risk blockers **remain** (`screening_required_uncleared` and
  `screening_unresolved_match` are non-waivable). Tier 3 has no maker-checker. Tests:
  `tests/test_cm_approval_preconditions.py`.
- Implementation is transactional with fail-closed staleness detection against recorded
  evidence.

**4. Roles permitted to act.** Approve Tier 1: `admin`/`sco`; Tier 2/3: `admin`/`sco`/`co`.
Implement: `admin`/`sco`.

**5. Approval / maker-checker / override logic.** Tier 1 maker-checker is non-waivable; Tier 2
relies on screening/risk blockers rather than maker-checker.

**6. Evidence and audit trail generated.** Change-request audit reconstruction
(`ChangeRequestAuditReconstructionHandler`) with before/after snapshots on approve/implement/
block.

**7. Human judgment retained.** Officers create, approve, and implement changes within the
tiered controls.

**8. Known limitations / residual risks (code-proven).**
- **`change_type` itself remains client-supplied.** Only *materiality* is server-derived from
  that value. `validate_change_types` is a **whitelist membership check**, not semantic
  validation — a mislabelled-but-valid type (e.g. a director change labelled
  `contact_detail_update`) would be classified Tier 3. Unknown types are rejected; valid-but-
  unmapped types default Tier 2. Semantic validation is **future hardening**.
- **Change-implementation fail-closed recompute (DCI-012 / P12-2) is NOT merged.** Today the
  risk recompute runs **after commit as best-effort** (a swallowed failure logs a warning);
  the "recompute-in-the-same-transaction" control exists only as a scoped PR.

**9. Compliance decision required.** (a) Confirm the Tier-2 maker-checker relaxation is
acceptable given the retained screening/risk blockers. (b) Accept `change_type` semantic
validation as future hardening, or require it before pilot. (c) Note DCI-012/P12-2 is not on
main.

---

## Process Area 10 — Periodic review and ongoing monitoring

**Overall status: IMPLEMENTED (state machine + env-gated scheduler); SLA is display-only.**

**1. Purpose.** To keep onboarded customers under review after approval — scheduled periodic
reviews, monitoring alerts, and lifecycle routing.

**2. Trigger / when it applies.** Periodic-review due dates/cadence; monitoring alerts; and
change-management-driven acceleration.

**3. System rule or workflow.**
- Periodic review engine (`periodic_review_engine.py`) with a state machine, attestation,
  document tasks, risk reassessment, and next-cycle scheduling on completion; trigger
  provenance preserved.
- **Automatic scheduler (env-gated):** a Tornado `PeriodicCallback` runs
  `monitoring_automation.run_due_monitoring_reviews` (`monitoring_automation.py:377`),
  guarded by a cross-task singleton (`_singleton_tick`). Enabled by default only when the
  environment is staging/production, or when `MONITORING_AUTOMATION_ENABLED=true`
  (`automation_enabled`, `monitoring_automation.py:579`). The sweep only consumes persisted
  due dates — it does **not** call screening providers or LLM agents.
- **Backfill endpoint** `POST /api/monitoring/reviews/schedule` (`server.py:35085`) is
  **API-only** (no back-office button).
- Monitoring alerts: routing to periodic review or EDD (`monitoring_routing.py`), dismissal
  control with valid-reason gating, and **SLA state that is derived display only, not an
  enforcement/legal guarantee** (`monitoring_sla.py`). All routing/assign/dismiss actions are
  audited.

**4. Roles permitted to act.** Officer roles via the handler `require_auth` gate.

**5. Approval / maker-checker / override logic.** Monitoring uses its **own officer-action +
status-lifecycle control model** (assign/dismiss/route), **not** a per-application owner gate
(inferred from `monitoring_routing.py` — no owner-equality check inside the module).

**6. Evidence and audit trail generated.** Routing/assignment/dismissal audit events; periodic
review state transitions and next-cycle scheduling.

**7. Human judgment retained.** Officers review, attest, dismiss, and route; the scheduler
only surfaces due work.

**8. Known limitations / residual risks.** SLA is a conservative pilot display approximation,
not an enforced clock. Automation is deliberately narrow (no provider/agent execution during
the sweep). Enterprise-adjacent modules (KPI, Reg-Intel) are badged "Coming Soon" in the
pilot.

**9. Compliance decision required.** Confirm the pilot monitoring scope (display-only SLA,
no automated provider re-screening during the sweep) is acceptable, and that monitoring's own
control model (not ownership-gated) is appropriate.

---

## Process Area 11 — SAR/STR workflow

**Overall status: DISABLED by default in every environment. Do NOT place operational reliance
on it.**

**1. Purpose.** (When enabled) to draft, review, approve, and file Suspicious Activity /
Transaction Reports.

**2. Trigger / when it applies.** Only if **both** `ENABLE_SAR_WORKFLOW` and `ENABLE_SAR_STR`
are set true. Defaults: `ENABLE_SAR_WORKFLOW` is false in staging/production;
`ENABLE_SAR_STR` is **false in every environment** (`environment.py`). Because
`_sar_str_enabled` (`server.py:3979`) requires both, **SAR/STR is off everywhere by default**,
including dev/demo.

**3. System rule or workflow.** SAR handlers exist in `server.py`
(`SARListHandler`/`SARDetailHandler`/`SARWorkflowHandler`/`SARAutoTriggerHandler`) but all
return **403 `enterprise_module_inactive`** while disabled; the UI button is disabled
("SAR/STR Coming Soon"). Test: `tests/test_pilot_scope_backend_lockdown.py` asserts POST
`/api/sar` returns 403 and `sar_reports` stays empty.

**4. Roles permitted to act.** N/A while disabled.

**5. Approval / maker-checker / override logic.** A `filing_status` workflow exists
(draft → pending_review → approved → filed) but is unreachable while disabled.

**6. Evidence and audit trail generated.** Standard handler audit only; **no dedicated
immutable per-SAR amendment chain**.

**7. Human judgment retained.** N/A while disabled.

**8. Known limitations / residual risks — SAR permanence is an unremediated pre-enable blocker
(RDI-005 / DCI-002, code-proven):**
- **`ON DELETE CASCADE`** on `sar_reports.application_id` (`db.py:1435`) — deleting the parent
  application would cascade-delete the SAR.
- **Cleanup delete path:** `scripts/cleanup_named_application.py` lists `sar_reports` among
  child tables it deletes.
- **Mutable SAR content:** `SARDetailHandler.put` updates narrative/indicators/etc. in place;
  the only guard blocks edits once `filing_status == 'filed'` — draft/pending/approved SARs
  remain freely mutable, and there is **no `sar_amendments` immutable-amendment table**.
- Retention intent (10-year, never auto-purge) and GDPR-erasure protection are documented, but
  the cascade and cleanup paths bypass that intent.

These defects are latent only because every SAR path is flag-gated off; they become live the
moment SAR/STR is enabled.

**9. Compliance decision required.** **Do not enable SAR/STR** until the permanence controls
(remove cascade, block cleanup deletion, immutable amendments/append-only history) are fixed
and re-verified. Enabling is a deliberate, compliance-sign-off-gated decision.

---

## Process Area 12 — Audit trail, sign-off, and evidence

**Overall status: PARTIALLY IMPLEMENTED. Supervisor chain is tamper-evident and wired; the
general audit log is not yet hash-chained.**

**1. Purpose.** To produce a durable, defensible evidence trail of every compliance-relevant
action and decision.

**2. Trigger / when it applies.** On every audited action across the platform.

**3. System rule or workflow.** There are **two** audit tables and **two** SHA-256 chains:
- **Supervisor verdict chain (`supervisor_audit_log`) — IMPLEMENTED, wired, fail-closed.**
  Appends through `supervisor/audit.py:append_verdict_chain_entry` on the caller's
  transaction (a verdict is never committed without its chain entry), serialised by an
  advisory lock and a unique index; verified by `verify_chain_integrity` (detects forks/
  orphans/cycles/duplicates). **Documented limit:** it cannot detect *suffix truncation*
  without an external anchor (PC-2).
- **General `audit_log` chain — CORE-ONLY, WIRING DEFERRED.** The primitives exist
  (`db.append_audit_log`, `verify_audit_log_chain`) but the ~200 existing audit writers are
  **not** routed through them (explicitly a "separate, decision-gated step"). So the actual
  officer/business rows — Decision, Officer Sign-Off, Screening Review, Override, Governance
  Attempt, change-management, EDD, monitoring — go to the **un-chained** table via
  `base_handler.log_audit` and are **not tamper-evident today**.
- **Decision records (`decision_model.py`):** `save_decision_record` **raises** on failure.
  Wired fail-closed for the application decision (`server.py:31959`); wired **best-effort** for
  the supervisor verdict overlay (`server.py:30887`) — the P10-5/RDI-009 residual.
- **IP attribution trusted-proxy fix (RDI-012, #708) — IMPLEMENTED.** `get_client_ip`
  (`base_handler.py:613`) honours `X-Forwarded-For`/`X-Real-IP` only when the direct peer is
  trusted. **Caveat:** "trusted" is a private/loopback heuristic, **not a configured ALB/CIDR
  allowlist**.
- **Evidence pack export (H4) — IMPLEMENTED.** `evidence_pack_export.py` exports supervisor
  chain rows **with** hash columns and a `canonical_hash_payload` a third party can recompute,
  plus a full-chain attestation and per-file SHA-256.

**4. Roles permitted to act.** Audit is written by the system; exports are officer-gated.

**5. Approval / maker-checker / override logic.** N/A (evidence layer).

**6. Evidence and audit trail generated.** As above.

**7. Human judgment retained.** N/A.

**8. Known limitations / residual risks.**
- **Not tamper-evident platform-wide** (only the supervisor chain is wired).
- **Decision-record coverage gap (RDI-009 / P10-5):** EDD closure, monitoring actions, change
  approvals, and risk changes emit plain audit rows but **no normalized decision record**.
- **PC-1 / FEO-012 continuity residual:** the JSON/CSV supervisor export API **strips**
  `previous_hash`/`entry_hash` (`server.py:18777`, locked in by `tests/test_audit_export.py`),
  so only the ZIP evidence pack — not the API export — is independently chain-verifiable; and
  the supervisor chain is a single global chain, so a per-application export cannot walk
  continuity from the pack alone (PC-1).
- **DB-level append-only (RDI-013 / P10-7) is PROPOSED only** — an aspirational SQL comment,
  no `REVOKE`/trigger enforcement in code; runtime `UPDATE/DELETE` revoke is ops work.

**9. Compliance decision required.** Decide whether, for the pilot, tamper-evidence on the
**supervisor verdict chain only** (plus per-row-verifiable evidence packs) is sufficient, or
whether platform-wide audit chaining and DB-level append-only enforcement are preconditions.

---

## Process Area 13 — Security and operational controls relevant to compliance

**Overall status: MIXED — several strong fail-closed controls implemented; a few remediations
remain in unmerged PRs.**

- **Session revocation fail-closed (BSA-001, #705) — IMPLEMENTED.** `decode_token`
  (`auth.py:97`) rejects a token when the revocation store is unavailable
  (`RevocationCheckUnavailable`); a revoke returns success only when durably persisted.
  *Residuals:* a `_NoopRevocationList` **import-time fallback fails open** if
  `security_hardening` cannot be imported; a token with an empty `jti` cannot be per-token
  revoked (user-level cutoff still applies).
- **Password change/reset & logout — IMPLEMENTED (503 + rollback, no false success).** Password
  change revokes all sessions in one transaction (503 on failure, `server.py:4955`); logout
  returns **503 and keeps the cookie** for retry if the durable revoke fails (`server.py:5027`).
  *Residual:* the **frontend** handling of that 503 is a UI-side follow-up (not in this
  backend repo — not code-proven here).
- **Malformed JSON fail-closed (BSA-006, #706) — IMPLEMENTED** via `get_json` (400). *Caveat:*
  a few handlers still parse the body directly, so it is enforced only where `get_json` is used.
- **Pagination bounding — IMPLEMENTED** (`_bounded_int`, and the supervisor API mirror).
- **AI budget fail-closed (BSA-013) — IMPLEMENTED**: blocks in staging/prod/demo when the
  budget store is unreadable.
- **Live vs sandbox / mock-mode prod hard-block — IMPLEMENTED**: `CLAUDE_MOCK_MODE` in
  production raises; SQLite is hard-blocked in production; staging/prod require the full secret
  set.
- **Staging SHA alignment gate (#702) — IMPLEMENTED (CI)**: the deploy workflow fails on
  task-def/SHA drift.
- **Migration failure-mode restriction (DCI-005, #711) — NOT ON MAIN.** Default is fail-closed
  (a migration failure halts startup), but `MIGRATION_FAILURE_MODE=continue` is **not yet
  rejected in staging/production** in the merged code; the gate exists only in an open PR.
- **Database connection pre-ping (DCI-007, #709) — NOT ON MAIN.** `get_db` returns a pooled
  connection with no `SELECT 1` liveness check; the pre-ping exists only in an open PR.

**Compliance decision required.** Note which of these (DCI-005, DCI-007, and the sanctioned-
floor/multi-gap memo fix from Area 3) are still in unmerged PRs, and decide whether merging
them is a pilot precondition.

---

## Compliance Decisions Required / Confirmed

| Area | Current system position | Compliance decision needed | Recommended decision | Risk if not approved |
|---|---|---|---|---|
| LOW/MEDIUM direct approval by CO / Onboarding Officer | All eligible LOW **and** MEDIUM approvable by one Onboarding Officer without a memo; disqualifiers enforced in code; policy already signed (2026-07-07) | Re-affirm the fast-path as an approved policy exception at MLRO/board level | **Confirm** (with the existing disqualifiers + 20% QA sampling) | Fast-path operates on engineering assumption rather than governance mandate |
| Whether every approval needs a memo/supervisor, or LOW/MEDIUM fast-path is accepted | Fast-path skips memo/validation/supervisor for clean LOW/MEDIUM | Decide: universal memo requirement vs accepted fast-path | **Accept fast-path** for clean LOW/MEDIUM with current disqualifiers | Either unnecessary friction, or unlogged risk if disqualifiers are weakened |
| Tier 2 maker-checker relaxation | Only Tier 1 requires maker-checker; Tier 2 self-approval allowed after screening/risk clear | Confirm Tier-2 relaxation is acceptable given retained screening/risk blockers | **Confirm** | Segregation-of-duties concern if screening/risk blockers are ever weakened |
| Screening clearance left outside ownership gate | Screening review is role-gated with its own four-eyes second review; not owner-gated | Confirm this is intended | **Confirm** | None if the second-review control holds; risk only if four-eyes is under-triggered |
| Document requests left open to officers | Any authorised officer may request/RMI; not a final acceptance | Confirm open document requests are acceptable | **Confirm** | Minimal — evidence gate still blocks sign-off |
| EDD escalation left open (weaker prerequisites) | `escalate_edd` bypasses authority matrix/document gate; risk-missing is warning-only | Accept because escalation tightens handling, or require P10-4 gates | **Accept for pilot; schedule P10-4** | Low (escalation only tightens); but audit optics of weaker prerequisites |
| Memo approval owner-gated | Proposed; today admin/sco role-gated, not owner-gated | Endorse proposed owner-gating scope for memo approval | **Endorse; treat as build item** | Same-role officers can approve any memo; ownership control is manual |
| Owner-gated sign-off vs full owner-gated workflow | Ownership is entirely proposed (FEO-013 not built) | Endorse **terminal-decision + memo-approval** ownership scope only | **Endorse narrow scope** | Named-owner control remains manual, not code-enforced |
| SAR/STR remains disabled until permanence fixed | Disabled by default everywhere; cascade/mutable-content defects unremediated | Keep disabled until RDI-005/DCI-002 fixed | **Keep disabled** | Regulatory records could be deleted/altered if enabled prematurely |
| `change_type` semantic validation | Client-supplied `change_type`; only whitelist check; materiality server-derived | Accept as future hardening, or require now | **Accept as future hardening** for pilot | Mislabelled change could be under-tiered (Tier 3) |
| Platform-wide audit tamper-evidence | Only supervisor chain wired; general `audit_log` not chained | Accept supervisor-chain-only for pilot, or require platform-wide | **Accept for pilot; schedule wiring** | General audit rows are not tamper-evident |
| Unmerged remediations on main (DCI-005, DCI-007, DCI-010/multi-gap) | Built in open PRs #710/#711/#709, not merged | Decide if merge is a pilot precondition | **Merge before pilot go-live** | Stale-config silent default, RDS-failover connection errors, sanctioned-floor memo gap |
| Production readiness | Not provisioned/validated; staging-only | Do not treat as production-ready | **Pilot only** | Overclaiming production readiness |

---

## Control Matrix

| Process | Automated rule | Human role | Maker-checker / override | Audit evidence | Status | Limitation |
|---|---|---|---|---|---|---|
| Application action ownership | — | Officer/supervisor | Proposed auto-assign + supervisor override | Would add assignment/override events | **Proposed** | Not built; named-owner control is manual (FEO-013) |
| Role & approval authority | `can_decide_application` role×risk×route | admin/sco/co decide; analyst read-only | HIGH/VH dual approval; AI override senior-only | Gate snapshot in decision record | **Implemented** | Role-based, not ownership-based |
| Risk scoring | `compute_risk_score` + floors + stale-risk gate | Admin configures model | No 2nd approver on model change | Recompute before/after; version stamp | **Implemented** | Boolean score-map laxity; missing-version unguarded; memo floor/multi-gap fixes unmerged |
| Screening review | `ScreeningReviewHandler` + blockers | admin/sco/co/analyst review | Four-eyes 2nd review (sco/admin) | Governance attempts + provider evidence | **Implemented** | No external adverse-media API; provider trust; simulated tolerated non-prod |
| Compliance memo | Deterministic builder + validation + supervisor | Prep open; approval admin/sco | Owner-gate proposed; warnings need reason | Sign-off audit; blocked verdict persisted | **Implemented** (Claude path **Disabled**) | Supervisor decision_records overlay best-effort |
| Final decision | Atomic fail-closed transaction | admin/sco/co | HIGH/VH dual; sign-off mandatory | Decision + override + sign-off + decision_record | **Implemented** (approve/reject) / **Partially implemented** (escalate/RMI) | escalate_edd/request_documents weaker prerequisites |
| Document review | Reliance gate + verification matrix | Officers review; senior manual-accept | Senior (admin/sco) manual acceptance | Before/after review audit | **Implemented** | Authenticity/tampering advisory-only; gate scoped to KYC slots |
| EDD | Deterministic fail-closed routing | Escalate open; senior review/closure | Distinct senior reviewer + distinct closer | Routing + structured findings + closure audit | **Implemented** | Escalation entry point weaker than approval |
| Change management | Server-side materiality + tiered downstream | Tier-based approval | Tier-1 maker-checker (non-waivable) | Change-request audit reconstruction | **Implemented** | `change_type` client-supplied; recompute-in-txn (DCI-012) unmerged |
| Periodic review / monitoring | State machine + env-gated scheduler | Officers review/attest/route | Own status-lifecycle model | Routing/dismissal + state transitions | **Implemented** | SLA display-only; own control model, not ownership-gated |
| SAR/STR | Flag-gated off | — | filing_status workflow (unreachable) | Standard audit only | **Disabled** | Cascade delete + mutable content unremediated |
| Audit / evidence | Supervisor hash chain + evidence pack | System-written | — | Recomputable per-row hashes | **Partially implemented** | General audit_log not chained; API export strips hashes; append-only = ops |
| Security controls | Fail-closed revocation/JSON/budget; SHA gate | Ops/admin | — | Auth + config validation | **Partially implemented** | DCI-005/DCI-007 unmerged; NoopRevocationList/jti-less residuals |

---

## Residual Risk Register

| Residual risk | Severity | Why it matters | Current compensating control | Recommended next action |
|---|---:|---|---|---|
| Action-ownership applies only to (proposed) terminal sign-off + memo approval; not built | High | Named-owner control is manual, not enforced; same-role officers can act on any case | Role + risk-tier gates; four-eyes on HIGH/VH | Build PR-APP-ACTION-OWNERSHIP-SCOPE-1; until then treat named-owner as a documented manual control |
| Collaborative actions remain open by design | Low | Documents/screening/EDD-prep are ungated by ownership | Each has its own control (evidence gate, four-eyes, tightening) | Accept; document as intended |
| `escalate_edd`/`request_documents` weaker prerequisites than approval | Medium | Asymmetric gating; audit optics | Escalation only tightens; sign-off still required | Implement P10-4 per-type gates |
| `change_type` semantic mislabelling | Medium | A misdeclared valid type can be under-tiered (Tier 3) | Whitelist validation; materiality server-derived; downstream re-checks for Tier 1/2 | Add semantic validation (future hardening) |
| SAR permanence (cascade delete, cleanup delete, mutable content) | High (if enabled) | Regulatory records could be lost/altered | SAR/STR disabled by default everywhere | Fix RDI-005/DCI-002 before any enable; keep flags off |
| Decision_records not written for all decision-equivalent workflows | Medium | EDD closure/monitoring/change/risk lack normalized records | Plain audit_log rows still written | Implement P10-5/RDI-009 |
| Platform-wide audit not tamper-evident | Medium | Officer/decision rows not hash-chained | Supervisor chain wired; evidence pack per-row verifiable | Wire `audit_log` chain (Phase 4 #27 follow-up) |
| Supervisor export API strips hash fields (FEO-012) + single global chain (PC-1) | Medium | API export not chain-verifiable; per-app continuity relies on attestation | ZIP evidence pack retains recomputable hashes | Ship hashes-only continuity ledger / anchoring |
| `_NoopRevocationList` import fallback fails open | Medium | Import failure would disable per-token revocation | Fails open only on import error, not runtime store outage; user-cutoff still applies | Fail closed on import failure |
| jti-less / empty-jti tokens not per-token revocable | Low | Such a token evades per-token revoke | User-level cutoff still revokes | Reject tokens without a jti |
| Frontend logout 503 handling | Low | UI must handle backend 503 + cookie retention | Backend fail-closed + retry-safe | Verify/har­den UI 503 path |
| DCI-005 (migration continue) + DCI-007 (pre-ping) not on main | Medium | Stale-schema continue not blocked in prod; stale pooled conns after RDS failover | Default migration halt is fail-closed | Merge #711/#709 before pilot go-live |
| IP trusted-proxy is private/loopback heuristic, not CIDR allowlist | Low | IP attribution could be spoofable from a private peer | XFF/X-Real-IP gated to trusted peer | Configure explicit ALB/CIDR allowlist |
| Production environment not provisioned/validated | High (for production) | No prod DR/backup/load/pen-test evidence | Staging validated; pilot scope only | Complete Phase 9 before production |

---

## Sign-off section

| Field | Entry |
|---|---|
| **Prepared by** | _______________________ (Engineering) — Date: __________ |
| **Reviewed by Compliance** | _______________________ (Compliance Officer / MLRO) — Date: __________ |
| **Approved by** | _______________________ (Head of Compliance / Board) — Date: __________ |
| **Date** | __________ |
| **Decision** | ☐ Approved for controlled pilot ☐ Approved with conditions ☐ Not approved — changes required |
| **Conditions / required changes before pilot** | ____________________________________________________________ |
| **Conditions / required changes before production** | ____________________________________________________________ |

---

## Compliance review questions

Compliance is asked to answer each of the following explicitly:

1. Do you approve the **LOW/MEDIUM fast-path** (single Onboarding Officer, no memo, with the
   coded disqualifiers and 20% QA sampling) as a standing policy exception?
2. Do you accept the **Tier-2 maker-checker relaxation**, relying on the retained
   (non-waivable) screening/risk blockers for Tier 2?
3. Do you endorse the **proposed action-ownership scope** (terminal decision + memo approval
   only), and do you accept the named-owner control operating **manually** during the pilot,
   or is code enforcement (PR-APP-ACTION-OWNERSHIP-SCOPE-1) a pilot precondition?
4. Do you accept that **screening clearance, document requests, and EDD escalation remain
   open** (not ownership-gated), each relying on its own control (four-eyes / evidence gate /
   tightening)?
5. Do you accept the **weaker prerequisites on `escalate_edd` / `request_documents`**, or do
   you require the P10-4 per-decision-type gates before pilot?
6. Do you accept **authenticity/tampering as advisory-only** document signals (no hard
   anti-tamper fail) for the pilot?
7. Do you accept **`change_type` semantic validation as future hardening**, given materiality
   is server-derived and Tier 1/2 trigger downstream re-checks?
8. Do you confirm **SAR/STR remains disabled** until the permanence controls (cascade,
   cleanup delete, immutable amendments) are fixed and re-verified?
9. Do you accept **supervisor-chain-only tamper-evidence** (plus per-row-verifiable evidence
   packs) for the pilot, or do you require platform-wide audit chaining and DB-level
   append-only enforcement first?
10. Do you require the **unmerged remediations** (DCI-005 migration restriction, DCI-007
    DB pre-ping, DCI-010/multi-gap memo correction) to be **merged to `main`** before pilot
    go-live?
11. Do you accept the **decision-record coverage gap** (EDD closure / monitoring / change /
    risk changes lack normalized decision records) for the pilot, pending P10-5?
12. Do you confirm that **no production reliance** is claimed and that Phase 9 (prod
    provisioning, DR/backup drill, pen test, legal sign-off) must complete before production?

---

*End of memo. All citations refer to `origin/main` at `4c172a3`; where a control is described
as "unmerged" or "open PR", it is present in a pull request but not on `main`/staging as of
the date above. This document should be re-reconciled against `docs/REMEDIATION_MASTER_LIST.md`
and live PR state before sign-off.*
