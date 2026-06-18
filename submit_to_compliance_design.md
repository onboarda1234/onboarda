# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Submit to Compliance Design (proposed)

## 1) Purpose and semantic boundary
- **Submit to Compliance** = handoff to SCO queue for senior review.
- **Approve** = final decision (`approved`).
- Submit is never terminal; approve/reject remain terminal decisions.

## 2) New workflow object
- New status: `submitted_to_compliance`.
- Transition-in: from `compliance_review` and `in_review` only.
- Transition-out: SCO/admin decision endpoints to `approved`, `rejected`, or `edd_required`.

## 3) Button visibility
- Show **Submit to Compliance** to `co` and `sco` on non-terminal applications when status in `{compliance_review, in_review}`.
- Hide for `analyst` and `client`.
- If `sco` opens case already in `submitted_to_compliance`, show as queued (disabled state, with queue timestamp + submitter).

## 4) Required reason/note
- Mandatory `submission_reason` (min length e.g. 12 chars).
- Optional structured tags: `submission_basis[]` (e.g. `high_risk`, `pep`, `material_screening`, `edd_required`, `override_used`, `manual_acceptance_present`).

## 5) Blocker model for Submit to Compliance

### Allowed unresolved blockers (can still submit)
- High/very-high risk requiring senior sign-off
- PEP present
- Material screening hit requiring senior determination
- EDD escalation need
- Officer override requiring senior disposition

### Disallowed unresolved blockers (cannot submit)
- No memo exists
- Memo corrupt/unreadable
- No screening run recorded
- Case ownership/authorization failure
- Missing mandatory package artifacts defined for senior review packet (e.g. no rationale/no decision notes)

## 6) Approve vs Submit blocker separation (required)

| Action | Blocker set intent | Example strictness |
|---|---|---|
| Approve | Final-decision readiness | all decision gates clear; second-review complete; no unresolved mandatory blockers |
| Submit to Compliance | Review-package readiness | allows unresolved risk/escalation concerns, but requires complete review packet |
| Reject | Decision integrity + reason | reason mandatory, basic case integrity required |
| More Info | Request quality + auditability | request items + deadline + signoff required |
| Override | Senior override governance | senior role + override reason + signoff + full audit |

## 7) SCO queue visibility
- Add queue filter/status for `submitted_to_compliance`.
- Include columns: submitted_by, submitted_at, submission_reason summary, blocker tags, risk tier.
- Queue should prioritize `VERY_HIGH`, `PEP/material`, then age.

## 8) Audit events
- `application.submit_to_compliance.requested`
- `application.submit_to_compliance.accepted`
- `application.submit_to_compliance.rejected`
- Payload: actor id/role, app ref/id, from_status, to_status, reason, blocker snapshot, source_surface, timestamp.

## 9) Backend endpoint needed
- `POST /api/applications/:id/submit-to-compliance`
  - Roles: `co`, `sco`, `admin` (admin optional by policy)
  - Valid from-status: `{compliance_review, in_review}`
  - Writes status `submitted_to_compliance`, submission metadata, audit entries
  - Does **not** write `decided_at`, `decision_by`, or decision terminal fields

## 10) UI changes needed
- Add `Submit to Compliance` button and modal (reason + tags + signoff).
- Add status badge and queue views for `submitted_to_compliance`.
- Distinguish CTA copy:
  - Submit = “Send to Senior Review”
  - Approve = “Final Approval”
- Ensure client-side guard + backend fail-closed behavior.
