# Compliance Control Sign-off Memo — Terminal Decision & Memo-Approval Ownership Gate

**Control:** Terminal Decision & Memo-Approval Ownership Gate
**Reference:** PR-APP-ACTION-OWNERSHIP-SCOPE-1 (Phase 7) · Audit-4 finding **FEO-013** · PR [#713](https://github.com/onboarda1234/onboarda/pull/713)
**Prepared for:** Aisha Sudally (Founder / Compliance Lead)
**Date:** 2026-07-08
**Status:** ⏳ AWAITING SIGN-OFF

---

## 1. Finding being remediated
The pilot runbook states "only the named owner works a case," but that control lived
only in the document — the software allowed any officer to perform any action on any
application. FEO-013 flags this as a control that is asserted but not code-enforced.

## 2. Scope of this control (deliberately narrow)
Ownership is enforced over **terminal sign-off authority only** — this is a *terminal
decision and memo-approval ownership gate*, NOT "all application actions owner-gated."

**Owner-gated:** final `approve`/`reject` (ApplicationDecisionHandler) ·
pre-approval `PRE_APPROVE`/`REJECT` (PreApprovalDecisionHandler) · memo approval
(MemoApproveHandler).

**Deliberately open to any authorised officer (collaboration):** document
review/request/upload, RMI, screening review **and clearance**, EDD escalation,
`REQUEST_INFO`, memo generation/validation, supervisor run. Rationale: these prepare
or *tighten* a file; none is a final risk acceptance.

## 3. Control behaviour
- **Assigned owner** performs a gated action → allowed (all existing gates still run).
- **Non-owner CO/analyst** → 403, audited (`ownership_denied`).
- **Admin/SCO override** → allowed only with an explicit `ownership_override_reason`
  (a distinct field from the AI-recommendation `override_reason`); audited as
  `ownership_override`. Without a reason: 403. An override never reassigns ownership.
- **Unassigned case at final decision** → the acting officer is auto-claimed as owner
  (first-touch ownership), audited as `ownership_claimed`. The claim is applied ONLY
  at the decision's success commit — a failed or unauthorized decision attempt can
  never seize ownership (adversarial-review fold B1).
- **Dual-approval second leg (amendment 2):** HIGH/VERY_HIGH dual approval requires a
  distinct second approver by design, so the completing `approve` leg is exempt from
  the ownership gate — but ONLY while the case's current risk is HIGH/VERY_HIGH (the
  same discriminator the dual-approval branch uses; adversarial-review fold B2
  prevents a stale first-approval marker from becoming a standing bypass). `reject`
  has no exemption.
- **Pre-approval and memo approval (amendment 1)** use the peer-supervisor variant:
  both routes are already admin/SCO-restricted, so a supervisor signing off a
  line-officer-owned case is the normal four-eyes flow. The gate fires only when
  ANOTHER supervisor owns the case — or when the owner's role cannot be determined
  (missing/dangling user record → fail closed, override reason required) — and never
  reassigns ownership.

## 4. Honest limitations (for the record)
1. The gate prevents acting on a case **owned by someone else**. It does NOT require
   pre-assignment: an unassigned case is auto-claimed by whoever signs off first
   (first-touch ownership, recorded in the audit trail). A CO can still unilaterally
   decide an unassigned case by claiming it. Accepted for PR1.
2. **Screening clearance stays open.** When a screening review requires four-eyes,
   the *approval* path is already blocked (`screening_second_review_pending`) until a
   second reviewer signs off; combined with this gate, a non-owner cannot finalize
   onboarding off a clearance they made. Note four-eyes on screening is conditional
   per review, so screening clearance itself is not universally dual-controlled — the
   accountable gate is the terminal approve/reject this control adds.
3. **Additive only.** Every existing gate (role/risk authority, memo
   freshness/validation, supervisor status, screening blocks, dual approval, evidence
   gates, decision-record persistence) runs unchanged; this control sits on top,
   never in place of them.

## 5. Residual risk
Ownership is an *accountability* control at the decision boundary, not
segregation-of-duties across the full case lifecycle. Full per-action ownership and
mandatory pre-assignment are explicitly out of scope for PR1. `assigned_to` values
are not yet validated as real user ids at assignment time (pre-existing; follow-up
candidate).

---

**Sign-off:** ☐ Approved ☐ Approved with changes ☐ Rejected

Signature: ______________________ (Aisha Sudally, Founder / Compliance Lead)  Date: __________
