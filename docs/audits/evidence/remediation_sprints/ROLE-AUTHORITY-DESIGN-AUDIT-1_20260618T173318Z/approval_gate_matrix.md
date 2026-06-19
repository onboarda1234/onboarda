# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Approval Gate Matrix (code-grounded)

**Purpose:** enumerate every condition that blocks **final approval** today, and tag each as
**blocks-approval?** and **blocks-submission?** per locked decision #3 (*screening/EDD/risk gate final
approval, never submission*). This is the contract the future `can_decide(user, application)` gate
(decision #10) and the `submit-to-compliance` endpoint must honor.

**Primary source:** `ApprovalGateValidator.validate_approval` (`security_hardening.py:705-1200+`),
`validate_high_risk_dual_approval` (`security_hardening.py:1345`), and the per-handler inline gates in
`ApplicationDecisionHandler` (`server.py:25316-25550`) / `ApplicationDetailHandler.patch` (`server.py:6171-6287`).

## How the gate stack runs today
- **Actor/role gates** (co-cannot-approve-HIGH, override SCO/admin, dual-approval) live **inline in `/decision`** (`server.py:25316`, `25353`, `25515`) — **not** in `ApprovalGateValidator`, and are **absent from the PATCH path** (P0-1).
- **Precondition gates** live in `ApprovalGateValidator.validate_approval` and are called by **both** `/decision` (`25426`) and PATCH (`6250`). These are fail-closed (exceptions → block).

## Gate matrix

Legend — **B-A?** = blocks final approval · **B-S?** = should block Submit-to-Compliance (target).
`B-S? = No` means the gate must NOT prevent an officer from submitting (decision #3); the SCO queue surfaces it as pending.

| # | Gate (condition that blocks approval) | Source `file:line` | Kind | B-A? | B-S? (target) |
|---|---|---|:--:|:--:|:--:|
| G1 | **Actor: `co` approving HIGH/VERY_HIGH** | `server.py:25353` (`/decision`); **MISSING in PATCH** | actor/role | ✅ | ✅ blocks co approve → must offer Submit instead |
| G2 | **HIGH/VERY_HIGH dual-approval** (two distinct officers) | `server.py:25515`→`validate_high_risk_dual_approval` `security_hardening.py:1345`; **MISSING in PATCH** | actor/dual-control | ✅ | ❌ (governs approval, not submission) |
| G3 | **Override (`override_ai`) requires SCO/admin + reason** | `server.py:25316-25326`, reason `25309` | actor/role | ✅ | ❌ |
| G4 | App still in pre-review state (`draft`…`kyc_documents`) | `security_hardening.py:736-741` | workflow | ✅ | ❌ (submission moves it forward) |
| G5 | Risk-integrity error (stale/contradictory risk fields) | `security_hardening.py:743-745` (`_approval_risk_integrity_error`) | data integrity | ✅ | ⚠️ warn at submit, do not block |
| G6 | No screening report present | `security_hardening.py:757-759` | package readiness | ✅ | ✅ **submission requires screening run** (first review enough) |
| G7 | Screening not in `live` mode in production | `security_hardening.py:762-767` | screening | ✅ | ❌ |
| G8 | **Screening second review pending** | `security_hardening.py:785-794`; inline `server.py:25376`, `6200` | screening four-eyes | ✅ | ❌ **explicitly allowed to submit** (decision #3) |
| G9 | Screening truth not terminal / stale / expired | `security_hardening.py:808-859` | screening | ✅ | ❌ (warn) |
| G10 | IDV gate not approval-ready | `security_hardening.py:861-869` | identity | ✅ | ❌ |
| G11 | No compliance memo | `security_hardening.py:880-882` | package readiness | ✅ | ✅ **submission requires a memo** |
| G12 | Memo stale | `security_hardening.py:896-909`; inline `6230`, `25406` | memo | ✅ | ⚠️ warn; ideally fresh memo for packet |
| G13 | Memo blocked | `security_hardening.py:912-914` | memo | ✅ | ✅ (corrupt/blocked memo = incomplete packet) |
| G14 | Memo `review_status != approved` / missing approval_reason | `security_hardening.py:917-927` | memo (senior) | ✅ | ❌ (memo approval is part of senior review) |
| G15 | Memo `validation_status` not `pass`/senior-`pass_with_fixes` | `security_hardening.py:936-954` | memo validation | ✅ | ❌ |
| G16 | Supervisor verdict not `CONSISTENT`/senior-`CONSISTENT_WITH_WARNINGS` | `security_hardening.py:957-979` | supervisor | ✅ | ❌ |
| G17 | **Supervisor `mandatory_escalation` set** | `security_hardening.py:995-1003` | escalation | ✅ | ❌ **(this is exactly the case that must Submit, not be dead-ended)** |
| G18 | **EDD routing = `edd` and EDD not complete** | `security_hardening.py:1004-1050` | EDD | ✅ | ❌ **submit allowed; SCO completes EDD** (decision #6) |
| G19 | Enhanced-requirements unresolved (mandatory/blocking rows) | `security_hardening.py:1064-1078`; waiver SCO/admin `enhanced_requirements.py:36` | enhanced req | ✅ | ❌ |
| G20 | Required screening provider simulated/error/pending | `security_hardening.py:1092-1147` | screening provider | ✅ | ❌ (warn; submit allowed) |
| G21 | Memo `ai_source == mock` | `security_hardening.py:1159-1165` | provenance | ✅ | ✅ (mock memo = not a real packet) |
| G22 | Screening freshness (run after latest screening-relevant inputs) | `security_hardening.py:1167+` | screening | ✅ | ❌ (warn) |
| G23 | Documents flagged | `validate_approval` doc check (docstring item 4, `security_hardening.py:723`) | documents | ✅ | ❌ |
| G24 | Officer sign-off missing | `server.py:25332` (`/decision`) | attestation | ✅ | depends — submission needs a submission note, not full sign-off |

## Non-overridable gates (target, decision #5)
The following must remain non-overridable even by SCO/admin override:
- **Live sanctions hit** (positive, confirmed) — G8/G9/G20 family where the provider returns a confirmed match.
- **Simulated screening in production** (G7) and **mock AI memo** (G21) — provenance integrity.
All other gates may be subject to a documented SCO/admin override (`override_used` event + structured reason; decision #5).

## Submission-readiness blocker set (target, for `submit-to-compliance`)
**Disallowed (cannot submit) — incomplete review packet:** G6 (no screening run), G11 (no memo), G13 (memo blocked/corrupt), G21 (mock memo), ownership/authorization failure, missing submission note.
**Allowed-unresolved (CAN submit; surfaced as pending in SCO queue):** G1, G2, G7, G8, G9, G10, G12, G15-G20, G22, G23 — i.e. all risk/escalation/EDD/second-review/material concerns (decision #3, avoid dead-end).

## Findings (severity)
- **P0-1** — G1 and G2 are enforced only on `/decision`, **not** on `PATCH /api/applications/:id`. The precondition stack (G4-G23) runs on PATCH, but a `co`/`analyst` can satisfy those and finalize a HIGH/VERY_HIGH approval without the actor gate or dual-approval. See `bypass_risk_findings.md`.
- **P1-1** — Actor gates (G1-G3) are not centralized; they must move into the single `can_decide(user, application)` gate that both endpoints call (decision #10).
- **P2-1** — G1 keys on resolved risk level only; PEP/EDD-at-MEDIUM relies on memo-borne G17/G18, not a first-class PEP/EDD actor gate. Recommend an explicit PEP/EDD actor flag in `can_decide`.
