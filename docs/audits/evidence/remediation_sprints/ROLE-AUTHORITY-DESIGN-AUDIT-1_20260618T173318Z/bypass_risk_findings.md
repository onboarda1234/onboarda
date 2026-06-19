# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Bypass Risk Findings (code-grounded)

**Source:** `origin/main` @ `69effaa`. Each finding cites `file:line` and is reproducible by reading the
cited code. Severities: **P0** = authority bypass (wrong actor can finalize / single point of failure),
**P1** = inconsistency / route split / audit blindspot, **P2** = clarity / latent drift.

---

## P0-1 — Final-decision bypass (approve **and** reject) via `PATCH /api/applications/:id`
**Where:** `ApplicationDetailHandler.patch` — `server.py:6070` (entry), write `6296-6302`.
**What:** the handler authorizes with **bare `require_auth()`** (`server.py:6072`) — **no `roles=` list** —
so any authenticated officer (`admin`, `sco`, `co`, **`analyst`**) passes; only `client` is blocked from
status changes (`6090-6093`). The transition map permits **both** terminal transitions
`compliance_review|in_review|under_review|edd_required → approved` **and `→ rejected`** (`6111-6114`), and the
handler writes `status` with `decided_at`/`decision_by` for either (`6296-6302`).

**Both terminal outcomes diverge from `/decision`, not just approve:**
- **Approve:** `/decision` blocks `co` on HIGH/VERY_HIGH (`25353`) and requires dual approval (`25515`);
  PATCH enforces neither. → a CO/analyst can finalize a high-risk **approval** as a single non-senior officer.
- **Reject:** `/decision` restricts reject to `admin/sco/co` (`25192`, analyst excluded) with reason + audit;
  PATCH lets **`analyst`** drive `→ rejected` with no role gate and no reason requirement. → wrongful terminal
  **rejection** of a legitimate applicant is an equally real harm (conduct / fair-treatment exposure) and is
  just as much a control divergence as the approve case.

**Why it is a bypass:** the PATCH path runs the **precondition** stack (H-05 review-state `6171`, screening
run `6184`, memo exists `6196`, second-review block `6200`, stale memo `6230`, `ApprovalGateValidator` `6250`)
but **omits the two actor controls** that the canonical `/decision` endpoint enforces:
1. **co-cannot-approve-HIGH/VERY_HIGH** (present `server.py:25353`, **absent in PATCH**).
2. **HIGH/VERY_HIGH dual-approval** (present `server.py:25515` → `validate_high_risk_dual_approval`, **absent in PATCH**).

`ApprovalGateValidator.validate_approval` does **not** compensate — it contains no role/actor/dual-control
logic (`security_hardening.py:705`, confirmed through `1200`). The **same `patch` method already role-gates
assignment** (`if user.get("role") not in ("admin","sco")`, `6346`), proving the omission on the status
branch is an oversight, not policy.

**Impact:** an `analyst` or `co` who owns/accesses a HIGH/VERY_HIGH application that has cleared the
precondition gates can set `status="approved"` via `PATCH`, finalizing a high-risk approval as a single
non-senior officer — defeating both the role restriction and the two-person rule.

**Fix direction (decision #10; see PR-APPROVAL-AUTHORITY-MATRIX-1 = PR1):** introduce a single server-side
`can_decide(user, application, decision, db)` gate covering **both `approve` and `reject`** (role ×
current-risk × EDD/screening/dual-approval/override/same-user-second-review × precondition stack) and make it
the **only** path to `approved`/`rejected`. Remove `approved`/`rejected` from the PATCH transition map (force
`/decision`) or route the PATCH status branch through `can_decide`. Fail closed; return structured blockers;
audit blocked attempts at the gate.

---

## P1-1 — Decentralized authority logic (root cause of P0-1)
**Where:** `/decision` inline gates (`server.py:25316`, `25353`, `25515`) vs PATCH (`6070`) vs `ApprovalGateValidator` (`security_hardening.py:705`).
**What:** actor/role authority is duplicated inline per handler instead of centralized. Two endpoints reach
terminal `approved`/`rejected` with materially different controls. There is **no centralized `can_decide`
helper** (the only existing `can_approve` is a local variable holding `ApprovalGateValidator`'s precondition
result at `server.py:6250`, which carries no role/actor logic).
**Impact:** any new write path (or a future endpoint) can silently re-introduce a bypass. **Fix:** centralize
(decision #10).

---

## P1-2 — No `submitted_to_compliance` status / endpoint / button (dead-end risk)
**Where:** backend grep for `submitted_to_compliance` → **zero matches**; UI exhaustive search → **no
"Submit to Compliance" / "Send to Senior Review" button**.
**What:** submission and approval are conflated. A `co` facing a HIGH/PEP/EDD/second-review-pending case has
**no forward action** other than approve (blocked) or reject — the dead-end decision #3 warns against. The
only handoff is `escalate_edd` (`server.py:25590`), which is EDD-specific, not a general senior handoff.
**Impact:** officers either get stuck or are pushed toward inappropriate rejects; senior workload is invisible.
**Fix:** implement `submitted_to_compliance` as the single source-of-truth status + `POST /submit-to-compliance`
(decisions #1-#3); SCO queue is a projection. Submission must **not** be blocked by screening/EDD/risk gates.

---

## P1-3 — Decorator-level role denials are not audited
**Where:** `require_auth` silent 403 `base_handler.py:493-496`; **132 `require_auth(roles=[...])` sites in
`server.py`, zero `log_authz_denial`**.
**What:** a wrong-role attempt blocked at the decorator (e.g. `analyst` → `/decision` `25192`, `co` →
`/memo/approve` `24476`, lower role → EDD GET/PATCH) leaves **no audit trail**. Blocked-attempt monitoring is
blind to exactly the probing that precedes a bypass attempt.
**Impact:** cannot detect or reconstruct attempted privilege escalation at the decorator boundary.
**Fix:** route plain `require_auth` role denials through `log_authz_denial` (as `require_backoffice_auth`
already does, `base_handler.py:509-519`).

---

## P1-4 — PATCH status path drops governance audit on most block branches
**Where:** `server.py:6122, 6133, 6174, 6186, 6193, 6199, 6092` — block returns with **no
`log_governance_attempt`** (only the second-review block `6211/6263` and assignment `6351` are audited).
**Impact:** blocked approvals routed through PATCH are largely invisible; pairs with P0-1 to hide bypass
attempts. **Fix:** audit every block branch (folds into the `can_decide` consolidation in PR1).

---

## P1-5 — Memo-approve UI permission mismatch
**Where:** UI `approveMemo()` checks `assertPermission('approve_low_medium')` (incl. `co`) —
`arie-backoffice.html:28946`; backend `MemoApproveHandler` requires `["admin","sco"]` — `server.py:24476`.
**What:** the memo-approve button is **enabled for `co`** but the backend returns 403. Backend is fail-closed
(no data risk), but the UI misrepresents `co` authority. **Fix:** gate the button on a senior permission id.

---

## P2-1 — co-HIGH gate keys on risk level only (PEP/EDD-at-MEDIUM)
**Where:** `server.py:25353` checks `approval_risk_level in ("HIGH","VERY_HIGH")` only;
`_application_risk_snapshot` (`server.py:317`) returns no PEP/EDD flag.
**What:** a PEP or EDD-required case that scores MEDIUM is blocked for `co` only **indirectly**, via the
memo-borne `mandatory_escalation`/`edd_routing` gates in `ApprovalGateValidator` (`security_hardening.py:995`,
`1005`) — not by a first-class actor gate. If a memo lacks those signals, the indirect block fails open for
the actor dimension (preconditions still apply). **Fix:** add explicit PEP/EDD actor flags to `can_decide`.

---

## P2-2 — EDD `PATCH` decorator lists `co` (misleading)
**Where:** `EDDDetailHandler.patch` decorator `["admin","sco","co"]` `server.py:29689` vs inner closure gate
`29816-29822` (SCO/admin only + closer≠assigned). **Net behavior is correct** (co cannot close EDD), but the
decorator is misleading and invites future drift. **Fix:** tighten or comment.

---

## P2-3 — No first-class override / waiver audit events
**Where:** override embedded in `Decision` detail (`server.py:25606`); waiver state on
`application_enhanced_requirements` with **no dedicated audit emit**. **Fix:** emit `override_used` /
`waiver_used` events (see `audit_trail_requirements.md`).

---

## P2-4 — UI authority gates use hardcoded role arrays (drift risk)
**Where:** screening disposition guard `arie-backoffice.html:12580` (`canDispositionScreeningDisposition`,
`14652`) and IDV resolve `10012` use **hardcoded role lists**, not the `ROLE_PERMISSIONS` policy fetched from
`/config/roles-permissions`. If backend policy is edited at runtime, UI gates diverge. **Fix:** drive from
`hasPermission`.

---

## Controls verified SOUND (no bypass) — do not weaken
- **Screening second review**: SCO/admin-only (`server.py:20787-20796`), same-user block vs first reviewer
  (`20779-20785`), independently re-validated read-only at approval (`security_hardening.py:356-375`).
  **Approval reads but never writes second-review state** — no circular self-satisfaction.
- **EDD closure**: SCO/admin-only + closer≠assigned dual-control (`server.py:29816-29822`).
- **Override**: SCO/admin-only + reason (`server.py:25316`, `25309`).
- **Waiver**: SCO/admin-only (`enhanced_requirements.py:36`).
- **Dual-approval (HIGH/VH) on `/decision`**: two distinct officers (`server.py:25515`).
- **Authority evaluated against current risk** at decision time (`server.py:25351`, live locked row).
- **Public API** `/api/v1/.../decision` is **read-only** (`public_api.py:78`) — not a write surface.
- **Portal** never exposes internal mechanics (`arie-portal.html:11199-11231`).

## Pilot-blocking assessment
- **P0-1** is **pilot-blocking** and the top enterprise-trust issue: a non-senior officer can finalize a
  high-risk **approval** — or wrongfully drive a terminal **rejection** — via an undocumented route. Must be
  fixed **first** (PR-APPROVAL-AUTHORITY-MATRIX-1 = PR1, the `can_decide` gate) before pilot sign-off. The
  product cannot claim a controlled compliance workflow while a generic status PATCH bypasses the decision
  authority model.
- **P1-2** (no submit-to-compliance) is **pilot-blocking for the operating model** (officers have no correct
  forward action on high-risk).
- **P1-3 / P1-4** (audit blindspots) are **strongly recommended** before pilot for supervisory defensibility.
