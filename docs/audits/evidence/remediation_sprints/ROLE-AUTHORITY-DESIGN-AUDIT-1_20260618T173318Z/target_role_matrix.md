# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Target Role Matrix (Design)

**Status:** design target to enforce. Locked decisions are not re-litigated here (see kickoff decisions 1-10, reflected in `submit_to_compliance_design.md` and `approval_gate_matrix.md`).

## Operating model
- **Onboarding Officer (`co`)** may **final-approve only LOW/MEDIUM clean** files. May **Submit to Compliance** any file (mandatory for high-risk lanes, optional/discretionary for clean files). May reject, request info, escalate, run screening + do first screening review. **Never** approves HIGH/VERY_HIGH/PEP/EDD/material-screening/second-review-pending/override cases, **never** completes EDD, **never** overrides/waives.
- **SCO** is the senior approval authority: final-approves any risk tier after all gates clear; completes EDD; performs screening second review; overrides/waives (subject to non-overridable gates).
- **Admin** may final-approve high-risk **only through the same gates as SCO** (no bypass); admin actions on high-risk are flagged privileged + extra-audited.
- **Analyst** never approves/rejects/overrides; read + first-review + request-info + escalate only.
- **Client** portal: own application only; never sees internal authority mechanics.

## Core business rule (authority × current risk at decision time)

| Case (evaluated against CURRENT risk) | Onboarding Officer | SCO / Admin |
|---|---|---|
| LOW / MEDIUM, clean | **Approve** | Approve |
| HIGH / VERY_HIGH | Cannot approve → **Submit to Compliance** | Approve after gates clear |
| PEP present | Cannot approve → Submit to Compliance | Approve after checks |
| EDD required | Cannot approve → Submit to Compliance | Approve after EDD complete |
| Material screening issue | Cannot approve → Submit to Compliance | Approve after screening gates clear |
| Screening second-review pending | Cannot approve → **Submit to Compliance allowed** | Complete second review (distinct actor), then approve |
| Override / waiver needed | Cannot approve | SCO/Admin only; some gates non-overridable |

> Authority is evaluated against the application's **current risk at decision time**, never a stored submission-time lane. A risk rise auto-escalates the required approver and re-opens gates (kickoff decision #8).

## Target capability matrix

Legend: ✅ allowed · ⚠️ conditional · ❌ blocked

| Capability | admin | sco | co | analyst | client | Target rule |
|---|:--:|:--:|:--:|:--:|:--:|---|
| Final Approve | ⚠️ same gates as SCO | ✅ after gates | ⚠️ LOW/MED clean only | ❌ | ❌ | single server-side `can_decide(user, app, decision)` gate, `decision=approve` (decision #10) |
| Final Reject | ✅ | ✅ | ✅ | ❌ | ❌ | **same `can_decide` gate, `decision=reject`** — reject is also a controlled terminal decision; analyst excluded; reason + audit required |
| **Submit to Compliance** | ✅ | ✅ | ✅ | ❌ | ❌ | status handoff; never blocked by screening/EDD/risk gates (decision #3) |
| Request more info | ✅ | ✅ | ✅ | ✅ | ❌ | unchanged |
| Escalate to EDD | ✅ | ✅ | ✅ | ⚠️ | ❌ | escalation allowed; EDD *completion* is senior-only |
| Complete / close EDD | ✅ | ✅ | ❌ | ❌ | ❌ | EDD owned by SCO/MLRO (decision #6) |
| Screening first review | ✅ | ✅ | ✅ | ✅ | ❌ | unchanged |
| Screening second review | ✅ | ✅ | ❌ | ❌ | ❌ | distinct actor, SCO/admin; approval reads but never writes it (decision #4) |
| Approve memo | ✅ | ✅ | ❌ | ❌ | ❌ | senior-only (unchanged) |
| Override AI / blocker | ✅ | ✅ | ❌ | ❌ | ❌ | SCO/admin; distinct `override_used` audit event + reason; non-overridable gates excluded (decision #5) |
| Waive enhanced/EDD requirement | ✅ | ✅ | ❌ | ❌ | ❌ | SCO/admin (unchanged) |
| Assign / reassign | ✅ | ✅ | ❌ | ❌ | ❌ | unchanged |
| Export evidence pack | ✅ | ✅ | ❌ | ❌ | ❌ | unchanged |
| Edit risk/system/AI config | ✅ | ❌ | ❌ | ❌ | ❌ | admin only (unchanged) |

## Explicit admin policy (target)
- Admin is a **supervisory/emergency** final authority. For high-risk approval, admin passes the **identical** `can_decide` gate stack as SCO (no admin-only shortcut).
- Admin high-risk approvals and any override/waiver must write a **privileged-action** audit event with the same structured reason fields SCO is held to, plus an `is_privileged_admin_action` flag.
- Admin must **not** silently bypass mandatory gates (screening second review, EDD completion, memo/document gates, live-sanctions non-overridable gate).

## Delta from current state (what must change)
1. Route **all** terminal `approved`/`rejected` writes through one server-side `can_decide(user, application, decision, db)` gate covering **both approve and reject** (role × current-risk × gate-states); remove `approved`/`rejected` from the PATCH status-mutation map or funnel it through the gate (closes P0-1). **This is PR1 — do it before the submit workflow.**
2. Add `submitted_to_compliance` status + `POST /api/applications/:id/submit-to-compliance`; SCO queue is a *projection* of that status (decisions #1, #3).
3. Add `can_decide` blocked-attempt + override/waiver first-class audit events; audit decorator-level role denials (closes audit P0/P1/P2).
4. UI: hide (not just click-deny) Approve on cases the role can never approve; show "Submit to Compliance" for `co`/`sco`; align memo-approve button to backend `["admin","sco"]`.
