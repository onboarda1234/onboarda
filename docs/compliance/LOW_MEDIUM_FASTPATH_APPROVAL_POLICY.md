# Low/Medium-Risk Fast-Path Approval Policy

**Status:** APPROVED · **Owner:** Head of Compliance · **Applies to:** RegMind application approvals
**Approved & signed off by:** Aisha Sudally · **Approval date:** 2026-07-07
**Audit reference:** RDI-002 (RegMind Production Audit 1) · **Last updated:** 2026-07-07

---

## 1. Purpose

RegMind allows a **fast-path approval** for genuinely low-risk applications: they can be
approved **without a full compliance memo** (no AI memo, memo validation, or supervisor
verdict). This document defines **when** that shortcut is allowed, **when it is not**, and
**what controls stay in place** so the decision remains defensible to a regulator.

This is an approved, deliberate business control — not a gap. Any approval outside the
rules below must go through the **full compliance-memo route**.

## 2. When the fast-path is allowed (eligibility)

An application may be fast-tracked **only if all** of these are true:

- Risk level is **LOW or MEDIUM** — **all MEDIUM cases qualify** (no additional score
  cutoff), provided the score is current and freshly computed, not stale.
- **No disqualifying signal** in Section 3 is present.
- A **completed AML screening** and **identity verification** are on file and still valid
  (within the standard freshness window).

If any condition fails, the fast-path is not available and the full memo route applies.

## 3. When it is NOT allowed (disqualifying signals)

The fast-path is **blocked** — full compliance-memo route required — if **any** of these apply,
regardless of the stored risk level:

- **Sanctioned / FATF-listed jurisdiction** anywhere in the structure.
- **PEP** (politically exposed person) match on any party.
- **Adverse screening result** or watchlist/sanctions hit.
- **Screening incomplete, expired, errored, or pending second review.**
- Identity verification **not passed**.
- Risk score is **missing, zero, or stale** (not recomputed against the current risk config).

## 4. Who may approve

- An **Onboarding Officer acting alone** may approve **all** eligible fast-path cases
  (LOW and MEDIUM). No second approver or SCO escalation is required for the fast-path.
- A **Senior Compliance Officer (SCO)** may also approve.
- HIGH / VERY_HIGH cases are never eligible and follow existing dual-approval rules.

## 5. Controls that stay in place (compensating controls)

Even without a memo, the fast-path still enforces:

- Officer authentication and **sign-off** on every approval.
- **Screening + identity-verification** presence and freshness checks.
- A permanent **audit-log** entry and a normalized **decision record** for every approval.
- **Post-approval QA sampling:** a second officer reviews **20% of fast-path approvals** on
  a periodic basis to confirm eligibility was correctly applied.

## 6. Evidence & auditability

Every fast-path approval must record **why it was eligible**, so the decision proves itself:

- Decision record stamped with **route = fast-path (direct low/medium)** and the
  **eligibility basis** (risk level + confirmation that no disqualifying signal was present).
- Automated tests assert that a case carrying a disqualifying signal (e.g. a sanctioned
  country) **can never** take the fast-path.

## 7. Review

This policy is reviewed at least **annually**, or sooner if the risk model, product scope,
or regulatory guidance changes.

---

| Role | Name | Signature | Date |
|------|------|-----------|------|
| Head of Compliance | Aisha Sudally | Signed (approved) | 2026-07-07 |
| MLRO / SCO | | | |
