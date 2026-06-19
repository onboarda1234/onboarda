# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Submit to Compliance Design (code-grounded)

**Status:** design. Locked decisions #1-#9 are honored, not re-litigated.
**Current state:** `submitted_to_compliance` does not exist (backend grep: zero matches); no UI control exists.
The only handoff today is `escalate_edd` (`server.py:25590`), which is EDD-specific.

## 1. Semantic boundary
- **Submit to Compliance** = move the application to the authoritative status `submitted_to_compliance`
  (handoff to senior review). **Non-terminal.**
- **Approve / Reject** = terminal decisions (`approved` / `rejected`) via the single `can_decide` gate.
- The **SCO queue item is a projection of the `submitted_to_compliance` status, not a separately writable
  object** (decision #1). There is one source of truth: application status + submission metadata.

## 2. Status model (extends `server.py:6101-6118`)
Add `submitted_to_compliance` to `STATUS_LABELS` (`branding.py`) and the transition map:
- **in:** `compliance_review`, `in_review`, `under_review`, `kyc_submitted` → `submitted_to_compliance`
  (also discretionary from `compliance_review` for clean LOW/MED — decision #2).
- **out:** `submitted_to_compliance` → `approved` / `rejected` / `edd_required` / `rmi_sent` (SCO/admin
  decision) and → `compliance_review` / `in_review` (return to officer).
- **Field lock (decision #7):** on entry, lock decision/authority + risk fields; **keep document collection /
  prep open** (no hard freeze). Implement as a write-guard on risk/decision columns while status =
  `submitted_to_compliance`, not a blanket row lock.

## 3. New endpoint
`POST /api/applications/:id/submit-to-compliance`
- **Roles:** `["admin","sco","co"]` (mirror `/decision` decorator `server.py:25192`); analyst/client blocked.
- **Valid from-status:** `{compliance_review, in_review, under_review, kyc_submitted}`.
- **Writes:** `status='submitted_to_compliance'`, `submitted_to_compliance_at`, `submitted_to_compliance_by`,
  `submission_note`, `submission_basis` (tags), `submission_kind` (`mandatory`|`discretionary`),
  `blocker_snapshot` (JSON from the gate evaluator).
- **Does NOT write:** `decided_at`, `decision_by`, decision terminal fields, or any screening second-review
  field (decision #4 — no circular writes).
- **Idempotent:** re-submit while already `submitted_to_compliance` returns the queued projection (200), not a
  duplicate.

## 4. Submission blocker model (decision #3 — gates approval, NOT submission)
Reuse the gate evaluator from `approval_gate_matrix.md`. Submit is governed by **package readiness**, not
final-decision readiness.

**Disallowed (cannot submit — incomplete packet):**
- No screening run (G6) · no compliance memo (G11) · memo blocked/corrupt (G13) · mock-AI memo (G21) ·
  ownership/authorization failure · missing `submission_note`.

**Allowed-unresolved (CAN submit; surfaced as pending in the queue):**
- HIGH/VERY_HIGH (G1) · PEP · material screening hit (G9/G20) · **screening second-review pending (G8)** ·
  EDD required / mandatory_escalation (G17/G18) · enhanced-requirements pending (G19) · override needed.

> An officer can submit with **only the first screening review done**; the SCO queue shows what's pending;
> final approval remains fail-closed via `can_decide`.

## 5. Submission metadata
- `submission_note`: mandatory, min length (e.g. ≥12 chars), sanitized.
- `submission_basis[]`: structured tags — `high_risk`, `very_high_risk`, `pep`, `material_screening`,
  `screening_second_review_pending`, `edd_required`, `mandatory_escalation`, `enhanced_requirements_pending`,
  `override_needed`, `discretionary`.
- `submission_kind`: `mandatory` (any blocking basis present) vs `discretionary` (clean LOW/MED submitted on
  officer judgment, decision #2) — tag for metrics.
- `blocker_snapshot`: the structured blocker list from the gate evaluator at submission time (for the queue
  and for audit reconstruction).

## 6. SCO queue (projection)
- Derive from `status='submitted_to_compliance'` (a query/filter, not a new writable table).
- Columns: `ref`, `submitted_by`, `submitted_at`, `submission_kind`, `submission_basis` tags, `risk_tier`,
  pending-gate summary, age.
- Ordering: `VERY_HIGH` → `PEP`/material → `screening_second_review_pending` → age.

## 7. Audit events (new — see `audit_trail_requirements.md`)
- Governance: `application.submit_to_compliance` (accepted/rejected) with actor/role, from→to status, basis,
  kind, blocker snapshot, source surface, ts.
- Business event: `Submit to Compliance` (`log_audit`) with before/after state.

## 8. UI changes
- **Back office:** add a "Submit to Compliance" button (CTA "Send to Senior Review") for `co`/`sco` when
  status ∈ from-set; modal collects `submission_note` + basis tags + sign-off. Distinguish from "Final
  Approval". For a `co` on a high-risk case, this is the **primary** CTA (replaces the misleading Approve
  button — see `button_visibility_matrix.md` P1). When SCO opens a `submitted_to_compliance` case, show queued
  state (submitter + timestamp) and the decision controls.
- **Portal (decision #9):** map `submitted_to_compliance` → neutral **"Under Review"** in
  `getClientPortalStatusLabel` (`arie-portal.html:11199-11221`); never expose submitted-to-compliance / SCO /
  EDD / screening mechanics. The existing scrubber (`sanitizeClientPortalCopy` `11224`) provides a backstop.

## 9. Relationship to `can_decide` (decision #10)
Submit-to-Compliance and `can_decide` share the **same gate evaluator** but consume different blocker sets:
Submit uses the *package-readiness* subset; `can_decide` uses the *final-decision* superset including the
actor gates (G1-G3). This guarantees the dead-end cannot recur: whatever blocks approval for `co` always has
Submit-to-Compliance as a valid forward action.
