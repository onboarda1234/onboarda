# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Recommended PR Sequence (design only)

**Revision note (post-review):** sequence reordered to **authority-gate-first**. The central gate is
`can_decide` (covers **approve and reject**), not merely `can_approve`. This supersedes the earlier
submit-first ordering: a routing workflow is worthless while a weaker endpoint can still finalize a
decision, and the high-risk dead-end the submit workflow addresses **already exists today** on `/decision`
(`server.py:25353`), so closing the PATCH bypass first introduces no new dead-end.

Five PRs, each independently shippable, none weakening an existing gate. **No code is implemented in this
audit.**

## Control invariant (the principle every PR must preserve)
> **No terminal application decision may be reachable through generic `PATCH /api/applications/:id` status
> mutation. All `approved` / `rejected` transitions must pass through one centralized server-side authority
> gate (`can_decide`) that evaluates actor role, current risk tier, EDD state, screening state, dual
> approval, override rules, same-user second-review block, and the existing `ApprovalGateValidator`
> precondition stack — returning structured blockers, failing closed, and auditing blocked attempts.**

> Existing test assets to extend (do not duplicate): `tests/test_decision_model.py`,
> `tests/test_dual_approval_race.py`, `tests/test_decision_path_integrity_priority_b.py`,
> `tests/test_prompt10_governance.py`, `tests/test_ex12_client_security.py`
> (`TestRegression_BackendAuthority`), `tests/test_approval_gate.py`, `tests/test_screening_review.py`,
> `tests/test_audit_before_after.py`, `tests/test_backoffice_review_audit.py`.

---

## PR1 — PR-APPROVAL-AUTHORITY-MATRIX-1  ⭐ foundation, do this first
**Objective:** centralize **all** terminal decision authority (approve **and** reject) in one server-side
`can_decide` gate and make it the ONLY path to `approved`/`rejected`. Close the PATCH terminal bypass.

**Issues fixed:** **P0-1** (PATCH approve **and** reject bypass), P1-1 (decentralized authority),
P1-4 (PATCH block-branch audit gaps for the decision path), P2-1 (PEP/EDD actor flag).

**Scope:**
- New `can_decide_application(user, application, decision, db)` → `(allowed: bool, blockers: list)` where
  `decision ∈ {approve, reject}`. Evaluates: actor **role**; **current risk** tier (live row, not stored
  lane); HIGH/VERY_HIGH **dual approval**; PEP / EDD / screening **escalation** handling; **same-user
  second-review block**; **override** rules (SCO/admin + reason; non-overridable gates excluded); and the
  existing `ApprovalGateValidator.validate_approval` precondition stack. Reject path enforces role
  (admin/sco/co; analyst excluded) + reason + case integrity.
- `/decision` approve **and** reject paths call `can_decide` (replaces inline `server.py:25353`/`25515`
  with the shared gate).
- `PATCH /api/applications/:id`: **remove `approved` and `rejected` from the status-transition map** (force
  callers to `/decision`) **or** route the status branch through `can_decide`. Preserve all current
  precondition behavior for non-terminal transitions.
- **Gate-level audit lives in PR1:** every blocked decision attempt emits a structured
  `application.decision_blocked` event (actor, role, decision, current risk, blocker list, source surface).
  A security gate that does not log its own denials is incomplete — this is **not** deferred to PR4.
- **Admin policy (explicit AC):** Admin may final-approve high-risk **only through the same `can_decide`
  gate as SCO** (no admin-only shortcut), with an `is_privileged_admin_action` flag + reason in the audit row.

**Acceptance criteria:**
- `co`/`analyst` **cannot** set `approved` on HIGH/VERY_HIGH via **any** endpoint (PATCH included).
- `analyst` **cannot** set `rejected` via **any** endpoint (PATCH included); reject stays admin/sco/co.
- HIGH/VH approval still requires two distinct officers (dual-approval preserved).
- Override stays SCO/admin; EDD completion stays SCO/admin; screening second-review behavior unchanged
  (gate **reads** it, never writes it).
- A PEP/EDD case at MEDIUM is blocked for `co` by an explicit actor flag, not only memo signals.
- Admin high-risk approval passes the identical gate and writes a privileged-action audit row.
- `/decision` and PATCH **cannot diverge** — a test asserts both routes yield the same authority outcome.
- Every blocked decision (approve or reject) writes a structured audit event.

**Tests:** extend `test_dual_approval_race.py`, `test_ex12_client_security.py`; **new**
`test_patch_decision_bypass.py` proving (a) co cannot approve HIGH via PATCH, (b) analyst cannot approve via
PATCH, (c) analyst cannot reject via PATCH, (d) HIGH/VH still requires dual approval, (e) SCO/admin approve
only after gates clear, (f) `/decision` and PATCH cannot diverge; `can_decide` unit matrix (role × risk ×
decision × gate). **API smoke:** PATCH `{status:approved}` as co on HIGH → 403 + audit row; PATCH
`{status:rejected}` as analyst → 403 + audit row. **Browser:** co Approve hidden/blocked on HIGH.
**Evidence:** before/after authority matrix, bypass test output, audit reconstruction.
**Pilot-blocking:** **Yes** (this is the P0 and the enterprise-trust issue).

---

## PR2 — PR-SUBMIT-TO-COMPLIANCE-WORKFLOW-1
**Objective:** add `submitted_to_compliance` status/action/queue/audit, per `submit_to_compliance_design.md`.
**It consumes the same `can_decide` evaluator** but with the package-readiness blocker subset (not the
final-decision superset), guaranteeing the dead-end cannot recur: whatever blocks a CO's approval always has
Submit-to-Compliance as a valid forward action.

**Issues fixed:** P1-2 (no submit-to-compliance / dead-end risk).

**Scope:**
- Add `submitted_to_compliance` to `STATUS_LABELS` (`branding.py`) and the transition map (`server.py:6101`).
- `POST /api/applications/:id/submit-to-compliance` (roles admin/sco/co; **package-readiness blockers only**
  — never blocked by HIGH-risk / PEP / material screening / second-review-pending / EDD-required, decision
  #3); writes submission metadata + audit; never writes decision/second-review fields.
- SCO queue filter/projection over `status='submitted_to_compliance'` (a projection, not a writable object).
- New audit events `application.submit_to_compliance` + `Submit to Compliance`.
- UI: "Submit to Compliance" button/modal for co/sco; portal maps the status → neutral "Under Review".

**Acceptance criteria:**
- Submit is non-terminal and distinct from approve/reject; uses the shared evaluator's package-readiness subset.
- Submission is **not** blocked by HIGH-risk / PEP / material screening / second-review-pending / EDD-required;
  IS blocked when no screening run / no memo / blocked-or-mock memo / no note.
- `submission_kind` tags discretionary vs mandatory; portal shows neutral "Under Review".

**Tests:** new `tests/test_submit_to_compliance.py` (from-status validity, blocker subset, idempotency, audit,
role gating, portal label). **Browser:** co submits a HIGH case → queue shows it pending.
**Pilot-blocking:** **Yes** (operating model needs the forward action; ship close behind PR1).

---

## PR3 — PR-APPROVAL-UX-GATES-1
**Objective:** make the back-office UI match backend authority (hide, not just click-deny).

**Issues fixed:** P1-5 (memo-approve permission mismatch), button-visibility P1/P2, P2-2/P2-4 (EDD decorator,
UI role-array drift).

**Scope:** hide Approve on cases the role can never approve (co + HIGH/VERY_HIGH) and show "Submit to
Compliance" instead; gate memo-approve on a senior permission id; drive screening-disposition / IDV controls
from `ROLE_PERMISSIONS` (remove hardcoded role arrays); tighten EDD decorator.

**Acceptance criteria:** co never sees an actionable Approve on HIGH/VERY_HIGH (sees Submit); analyst sees no
actionable Approve/Reject/Override; memo-approve button matches backend `["admin","sco"]`.
**Pilot-blocking:** No (UX/defensibility; strongly recommended).

---

## PR4 — PR-AUTHORITY-AUDIT-HARDENING-1
**Objective:** systemic audit coverage beyond the PR1 gate.

**Issues fixed:** P1-3 (132 decorator-level `require_auth` denials unaudited), P2-3 (no first-class
override/waiver events), remaining PATCH block-branch audit gaps.

**Scope:** route `require_auth(roles=[...])` denials through `log_authz_denial`; emit `override_used` /
`waiver_used` first-class events; audit residual PATCH block branches.
**Acceptance criteria:** wrong-role denials at the decorator boundary produce an audit row; override/waiver
have filterable first-class events.
**Pilot-blocking:** No (supervisory defensibility; strongly recommended). *Gate-level blocked-attempt audit
already shipped in PR1 — this PR is the broad sweep, not the gate itself.*

---

## PR5 — E2E-AUTHORITY-MATRIX-1
**Objective:** end-to-end browser + API proof of the full authority matrix.

**Scope:** automated E2E covering co LOW/MED approve; co HIGH blocked → submit; SCO approves after gates;
**PATCH bypass blocked for co/analyst on both approve and reject**; same-user screening second review blocked;
non-SCO second review blocked; EDD completion senior-only; override SCO-only; admin high-risk uses same gate
+ privileged audit; portal neutrality.
**Acceptance criteria:** all matrix cells pass; no decision bypass remains on any endpoint; audit
reconstruction proves actor/role/risk/second-actor/override for each path.
**Pilot-blocking:** **Yes** (sign-off gate — proves the matrix holds).

---

## Sequencing notes
- **PR1 first** (security): the central `can_decide` gate is the foundation; PR2-PR5 build on it.
- **Interim gap (deliberate):** between PR1 and PR2 a CO on a high-risk file still has no clean forward
  action — but this is the **status quo today**, not a regression, since `/decision` already blocks them.
  Ship PR1→PR2 in quick succession; document that `escalate_edd` + reject-with-reason are the only forward
  moves until Submit lands.
- PR3 depends on PR1+PR2 (UI mirrors new authority/status). PR5 runs last as the regression lock.
- None of these touch the Periodic Review track (PR-PRS-A merged / PR-PRS-B merged).
