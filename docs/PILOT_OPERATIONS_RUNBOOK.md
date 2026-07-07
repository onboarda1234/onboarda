# Pilot Operations Runbook — RegMind / Onboarda Controlled Pilot

**Audience:** compliance officers, SCOs, and the operator running the controlled paid pilot.
**Scope:** how to run the pilot on AWS **staging** (`staging.regmind.co`, ECS Fargate `af-south-1`, RDS **PostgreSQL** 15) within the controls that keep it *controlled*.

> The platform is assessed as **"complete for a controlled pilot"** (see
> `docs/audits/pr661_remediation_production_conditions.md`). This runbook is the
> operating procedure for the five controls that make that assessment hold. Four
> are enforced in code; one (Section 5) is a **temporary manual control** until
> its PR lands.

> **Substrate note (read first).** The pilot runs on **PostgreSQL** — the
> production-equivalent database. SQLite is the dev/test substrate only. Where a
> control has different mechanics on the two engines, the **PostgreSQL** behaviour
> is the operative one for the pilot. Do not reason about pilot behaviour from
> SQLite-only mechanics.

### The five controls at a glance

| # | Control | Status |
|---|---------|--------|
| 1 | Controlled submission process (forward-only lifecycle, fixture isolation, row-locked decisions) | ✅ Code-enforced |
| 2 | Provider-call prohibition — ComplyAdvantage is **sandbox**, mode is attested not assumed | ✅ Code-enforced |
| 3 | Approval controls — single authority gate, role×risk limits, dual-control, fail-closed precondition stack | ✅ Code-enforced |
| 4 | Frozen / isolated CloudWatch validation windows (SHA-aligned, readiness-green, attributable) | ✅ Process (uses code signals) |
| 5 | Named-owner action control (officer of record per case) | ⚠️ **Temporary manual** — code control pending |

---

## 0. Pre-pilot readiness checklist

Run this before opening the pilot to real applicants (details in each section):

- [ ] **SHA aligned** — deployed runtime `GET /api/version` `git_sha`/`image_tag` == audited `origin/main` == the ECS task's image tag (§4).
- [ ] **Readiness green** — `GET /api/liveness` 200; `GET /api/readiness` 200 with `aml_screening` reporting **sandbox/inactive** (never `ok`/live) (§2, §4).
- [ ] **CA = sandbox, attested** — `GET /api/screening/status` shows `active_aml_screening_mode = sandbox`, `workspace_label = ca-sandbox`, simulation fallback disabled (§2).
- [ ] **Alarms subscribed** — the paid-pilot CloudWatch alarm baseline is deployed and its SNS actions have a **confirmed** subscription (§4).
- [ ] **Named owners assigned** — every pilot application has a single named officer of record (§5).
- [ ] **No fixtures in pilot data** — real applicants are `is_fixture = 0` and never in the `f1xed…` id namespace; `demo_pilot_data.py` seeds are **not** loaded into the pilot DB (§1).

---

## 1. Controlled submission process

**Rule.** Applications move through a **forward-only** lifecycle; real applicant data is isolated from test/fixture data; and decision/handoff writes are row-locked so two officers can't double-act.

**Lifecycle (forward-only):**
`draft → pricing_review → pricing_accepted → kyc_documents → kyc_submitted → (background screening) → submitted_to_compliance → approved / rejected`

**How it's enforced (code anchors):**
- **Submission** — `POST /api/applications/:id/submit` (`SubmitApplicationHandler`, `server.py`) runs prescreening + risk and **defers** provider screening to the background `screening_jobs` queue. No synchronous provider call blocks the request.
- **KYC gate** — `POST /api/applications/:id/submit-kyc` enforces the document gate.
- **Handoff** — `POST /api/applications/:id/submit-to-compliance` (`SubmitToComplianceHandler`, `server.py`) is the **non-terminal** handoff to the SCO queue. It never approves or waives.
- **Fixture isolation** — back-office lists exclude test rows via `fixture_filter.py` (`id LIKE 'f1xed%' OR is_fixture`); the `show_fixtures`/`include_fixtures` opt-in is honoured **only for admin/sco**. The dual-DB predicate holds identically on PostgreSQL and SQLite.
- **Concurrency safety** — the decision and submit-to-compliance paths take a **row lock** (PostgreSQL `SELECT … FOR UPDATE`; SQLite `BEGIN IMMEDIATE` is the dev-parity equivalent). On the pilot's PostgreSQL the `FOR UPDATE` lock is the operative one.

**Operator procedure:**
- Real pilot applicants submit through the **client portal**; officer/CM-originated intake is also supported.
- **Never** create pilot rows in the `f1xed…` namespace; keep `is_fixture = 0` for real applicants.
- Do **not** pass `show_fixtures`/`include_fixtures` during pilot operation.
- `demo_pilot_data.py` seeds are **development-only** and must not be seeded into the pilot database.

**Evidence to capture:** for a sampled case, the status transitions in order; the submit response showing screening deferred to the queue (no inline provider call).

---

## 2. Provider-call prohibition — ComplyAdvantage runs **sandbox**

**Rule.** During the pilot, ComplyAdvantage runs in its **sandbox** workspace. No live/production provider screening is performed, and **no result may be presented as live production screening**.

**How the platform attests this (mode honesty, not assumption):**
- `_complyadvantage_runtime_status(...)` (`server.py`) reports the **truthful** workspace mode. Mode is **never inferred from `ENVIRONMENT`** — an unknown workspace stays `unknown`; `mode_source = attested_env` only when an operator explicitly sets `COMPLYADVANTAGE_WORKSPACE_MODE`.
- The readiness gate `_readiness_status_payload(...)` (`server.py`) makes sandbox/unverified resolve to `sandbox` / `mode_unverified` with `ready = False` in production, and a transient outage resolve to `unreachable` (degrade, stay up). **No environment override can turn AML green** (regression-locked by `tests/test_b6b5_screening_readiness.py`).
- **Operator source of truth:** `GET /api/screening/status` surfaces `active_aml_screening_mode` + `workspace_label` and separates ComplyAdvantage AML, Sumsub IDV/KYC, and OpenCorporates registry status.
- **Agent 3 makes no provider call.** It is a deterministic, policy-bounded interpreter that reads the **stored** `prescreening_data.screening_report` only — surfaced verbatim as *"No provider call was made."* Officers may re-interpret/triage a case any number of times without re-hitting the provider; a genuine re-screen is a separate, explicit background-queue action.
- **Webhooks are signature fail-closed** in deployed environments (inbound CA webhook verification rejects unsigned/invalid). The legacy `webhook.site` endpoints are inactive and **must never be copied to production**.

**Operator attestation checklist (capture each validation run):**
- `GET /api/screening/status` → `active_aml_screening_mode = sandbox`, `workspace_label = ca-sandbox`, simulation fallback disabled.
- `GET /api/readiness` → `aml_screening.status` in `{sandbox, inactive}` on staging (**never** `ok`/live).

---

## 3. Approval controls for the pilot

**Rule.** Every approved/rejected transition passes the **single centralized authority gate**; a bare status `PATCH` can never finalize a decision.

**How it's enforced (code anchors, `security_hardening.py` unless noted):**
- **Single authority path** — terminal decisions must pass `can_decide_application(...)`; `PATCH` status mutation cannot finalize (closed under the approval-authority-matrix work). `can_decide_application` is invoked on both the `/decision` and `PATCH` paths in `server.py`.
- **Who can approve (role × risk)** — terminal decisions require **admin/sco/co** (`DECISION_AUTHORITY_ROLES`); analyst/client cannot approve or reject. An **Onboarding Officer (co) cannot approve HIGH/VERY_HIGH**, nor LOW/MEDIUM cases routed to compliance/dual-control — those go through submit-to-compliance to an **SCO**. AI override (`override_ai`) is **SCO/admin-only and requires a reason**.
- **The gates (blockers)** — HIGH/VERY_HIGH requires **two distinct officers** (same-officer blocked, `validate_high_risk_dual_approval`); screening four-eyes requires a **different** SCO/admin reviewer; the full precondition stack (screening terminal + clear, IDV ready, memo present/fresh/approved/validated, supervisor consistent, EDD complete, enhanced requirements resolved, provenance not mock) runs **fail-closed** via `ApprovalGateValidator.validate_approval(...)` on both `/decision` and `PATCH`. See `docs/audits/evidence/remediation_sprints/ROLE-AUTHORITY-DESIGN-AUDIT-1_20260618T173318Z/approval_gate_matrix.md` (G1–G24) for the authoritative table.
- **Non-overridable even by SCO/admin:** a confirmed live sanctions hit; simulated screening in production; a mock AI memo.
- **Audit** — every decision and every blocked attempt writes a governance/audit row with before/after snapshots; officer sign-off is enforced.

**Pilot procedure:**
- Name the **SCO(s)** who are the *only* approvers of HIGH / EDD / escalated cases.
- **co** handles clean LOW/MEDIUM only.
- Every HIGH/VERY_HIGH approval must have a **documented two-officer** sign-off.

**Evidence:** `tests/test_patch_decision_bypass.py` (PATCH cannot finalize), `tests/test_approval_gate.py` + `tests/test_dual_approval_race.py` (precondition stack + two-distinct-officer dual approval), `tests/test_screening_clearance_validation_supervisor.py` (four-eyes/terminal-clear).

---

## 4. Frozen / isolated CloudWatch validation windows

**Rule.** A defensible pilot validation run needs a **clean, attributable** log window. This codifies the QA practice already used in the CM-E2E and E2E-PORTAL readiness runs.

**Pre-conditions to "freeze" a window:**
1. **SHA alignment** — the deployed runtime must equal the audited `origin/main`. Check `GET /api/version` (`VersionHandler`, `server.py`) `git_sha`/`image_tag` == `origin/main` == the ECS task's image tag. The staging deploy tags the image with `github.sha` (`.github/workflows/deploy-staging.yml`).
2. **Health/readiness green** — `GET /api/liveness` 200; `GET /api/readiness` 200 with `aml_screening` confirming **sandbox** (ties to §2).
3. **Boot hygiene** — the rolling deploy is serialized by the PostgreSQL advisory **boot lock** (`boot_lock.py`) so migrations can't race and pollute the window.

**Running an isolated window:**
- Pick a bounded UTC start/end; tag the run with a unique run-prefix (e.g. `CME2E-YYYYMMDD-…`) so every event is attributable.
- Scan the `/ecs/regmind-staging` CloudWatch log group (`af-south-1`) for the zero-tolerance pattern set: HTTP 5xx, unhandled exceptions/tracebacks, worker crashes, mock-fallback, duplicate screening jobs, DB errors, submit 503/504, CA polling timeout.
- Separate **expected controlled negatives** (deliberate 400/403/409 from negative tests) from unexpected errors.
- **Isolation discipline:** run **one** validation harness at a time against staging; do not run concurrent smoke/E2E harnesses that interleave events and break attribution; keep fixture traffic out of pilot windows (§1).

**Known log-hygiene note:** a deliberate unsafe-field negative test emits an ERROR + traceback while returning a controlled 400. Expect and **annotate** it; do not treat it as a failure. (A follow-up may downgrade it to a warning.)

**Alarms during the pilot:** the paid-pilot CloudWatch alarm baseline (ALB 5xx, task counts, verification queue depth/stuck/latency, RDS pressure) plus the CA operational/audit log groups provide standing monitoring. Alarm actions/paging must have a **confirmed** SNS subscription.

---

## 5. Named-owner action control — **TEMPORARY MANUAL CONTROL**

> ⚠️ **Status honesty:** this control is **NOT yet enforced in code** on `main`
> (its PR — `PR-APP-ACTION-OWNERSHIP-SCOPE-1` — is pending; P1 prod / P2 pilot).
> Do **not** claim code enforcement for it.

**The gap.** Today any authorized back-office role can act on any application. Action authority is scoped by **role + risk** (§3) but **not** by per-case ownership.

**Substrate to lean on.** Applications already carry an assignee, and reassignment is **audited** with structured before/after metadata and a required reason (`PR-APP-ROLE-AUDIT-GATES-1` / PR #525).

**Temporary manual control for the pilot:**
- Assign every pilot application a **single named human owner** (officer of record) accountable for its actions.
- Any **non-owner** action on a case is a documented exception (senior override) and must be captured in the audit trail.
- **Reconcile the audit log periodically** to confirm actions were taken by the case owner or an approving SCO.

**Exit criterion.** When `PR-APP-ACTION-OWNERSHIP-SCOPE-1` merges, replace this manual control with the code-enforced owner-scoped action gate and update this section.

---

## 6. Appendices

### A. Endpoint quick reference

| Purpose | Endpoint |
|---------|----------|
| Build identity (SHA alignment) | `GET /api/version` |
| Liveness | `GET /api/liveness` |
| Deep readiness (auth) | `GET /api/readiness` |
| Screening provider source of truth | `GET /api/screening/status` |
| Submit application (defers screening) | `POST /api/applications/:id/submit` |
| Submit KYC (document gate) | `POST /api/applications/:id/submit-kyc` |
| Handoff to SCO queue (non-terminal) | `POST /api/applications/:id/submit-to-compliance` |

### B. Cross-references
- `docs/DEPLOYMENT_RUNBOOK.md` — deploy procedure.
- `docs/DAY6_CLOSING_RUNBOOK.md` — closing/readiness practice.
- `docs/ROLLBACK_RUNBOOK.md` — rollback (tags `v4.1-stable` etc.).
- `docs/audits/pr661_remediation_production_conditions.md` — the "complete for a controlled pilot" assessment and production conditions.
- `docs/audits/evidence/remediation_sprints/ROLE-AUTHORITY-DESIGN-AUDIT-1_20260618T173318Z/approval_gate_matrix.md` — the G1–G24 approval gate matrix.

### C. Do-not-do list
- ❌ No production ComplyAdvantage credentials in the pilot — CA stays **sandbox**.
- ❌ No fixtures in pilot views; no `f1xed…` ids for real applicants; no `demo_pilot_data.py` seeds in the pilot DB.
- ❌ No concurrent validation harnesses in a single frozen window.
- ❌ No **co** approval of HIGH/VERY_HIGH — those route to an SCO with two-officer sign-off.
- ❌ Never present sandbox screening as live production screening.

---

*This runbook is operator-facing. Each control section states the Rule, how it is
enforced (code anchor), the operator procedure, and the evidence to capture.
Section 5 is explicitly a temporary manual control. The pilot runs on
PostgreSQL — do not restate SQLite-only mechanics as production behaviour.*
