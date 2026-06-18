# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Recommended Implementation PR Sequence

## PR-1: Decision authority hardening (backend)
**Scope**
- Remove/lock final decision status writes from generic `PATCH /api/applications/:id`.
- Route all final approve/reject through `POST /api/applications/:id/decision`.
- Enforce analyst cannot approve/reject at backend level only.

**Acceptance criteria**
- Analyst cannot set `approved`/`rejected` by any endpoint.
- co cannot approve `HIGH/VERY_HIGH` on any path.
- Existing screening second-review and EDD protections remain unchanged.

## PR-2: Introduce Submit to Compliance backend workflow
**Scope**
- Add `submitted_to_compliance` status.
- Add `POST /api/applications/:id/submit-to-compliance` with package-readiness blockers.
- Add audit event schema for submit-to-compliance actions.

**Acceptance criteria**
- Submit is non-terminal and distinct from approve.
- Submit requires reason and writes queue metadata.
- Disallowed blockers fail-closed with deterministic error payload.

## PR-3: SCO queue and decision routing updates
**Scope**
- Add queue filtering/visibility for `submitted_to_compliance`.
- Ensure SCO/admin decision actions consume submitted queue items and move to approved/rejected/edd_required.

**Acceptance criteria**
- SCO queue shows submitted cases with reason/tags.
- Decision transitions from submitted status are fully audited.

## PR-4: UI action model alignment
**Scope**
- Add Submit to Compliance button/modal and copy.
- Hide/disable buttons by authority where possible (not only submit-time failures).
- Align memo approval UI permission with backend authority.

**Acceptance criteria**
- Analyst does not see actionable Approve/Reject/Override controls.
- co sees Submit-to-Compliance where applicable.
- Memo approve button behavior matches backend roles.

## PR-5: Blocker-model separation implementation
**Scope**
- Implement explicit blocker sets for Approve, Submit, Reject, More Info, Override.
- Ensure Submit blockers are review-package based, not final-decision based.

**Acceptance criteria**
- Each action returns action-specific blocker payload.
- Submit allows unresolved senior-review concerns but blocks incomplete package states.

## PR-6: Audit/reporting and regression tests
**Scope**
- Add endpoint and UI tests for authority boundaries and new status transitions.
- Add audit log assertions for submit/decision/override paths.

**Acceptance criteria**
- Regression suite proves no approval bypass remains.
- Tests cover role matrix, second-review preservation, and submit-vs-approve semantics.
