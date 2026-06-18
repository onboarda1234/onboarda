# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Status Transition Matrix (current)

Source status labels: `/home/runner/work/onboarda/onboarda/arie-backend/branding.py`

## Status existence check (requested set)

| Status | Exists now? | Notes |
|---|---|---|
| `pricing_review` | ✅ | pre-KYC flow |
| `pre_approval_review` | ✅ | high-risk pre-approval stage |
| `pre_approved` | ✅ (legacy in map) | pre-approval decision currently writes `kyc_documents` |
| `kyc_documents` | ✅ | KYC required |
| `kyc_submitted` | ✅ | KYC submitted |
| `compliance_review` | ✅ | compliance stage |
| `submitted_to_compliance` | ❌ | not implemented in backend status model |
| `edd_required` | ✅ | EDD route/escalation |
| `rmi_sent` | ✅ | request-more-info sent |
| `approved` | ✅ | terminal |
| `rejected` | ✅ | terminal (reopen path to draft in PATCH transitions) |

## Current transitions relevant to authority

### Via `PATCH /api/applications/:id`
- `compliance_review -> in_review | edd_required | approved | rejected`
- `in_review -> edd_required | approved | rejected`
- `under_review -> edd_required | approved | rejected`
- `edd_required -> under_review | in_review | approved | rejected`
- `rmi_sent -> kyc_documents | kyc_submitted | compliance_review`

### Via `POST /api/applications/:id/decision`
- `approve -> approved`
- `reject -> rejected`
- `escalate_edd -> edd_required`
- `request_documents -> rmi_sent`

### Via `POST /api/applications/:id/pre-approval-decision`
- `PRE_APPROVE -> kyc_documents`
- `REJECT -> rejected`
- `REQUEST_INFO -> draft`

## Design implication
- There is no dedicated handoff status between package completion and final decision (missing `submitted_to_compliance`).
- Final authority currently conflates status mutation and final decision submission.
