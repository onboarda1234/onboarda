# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Current Role Matrix (origin/main)

Source files audited:
- `/home/runner/work/onboarda/onboarda/arie-backend/server.py`
- `/home/runner/work/onboarda/onboarda/arie-backoffice.html`
- `/home/runner/work/onboarda/onboarda/arie-backend/branding.py`

## Roles in scope
- `admin`
- `sco`
- `co` (user-facing: Onboarding Officer)
- `analyst`
- `client`

## Capability matrix (current behavior)

Legend: ✅ allowed, ⚠️ conditional/partial, ❌ blocked

| Capability | admin | sco | co (Onboarding Officer) | analyst | client | Current enforcement summary |
|---|---:|---:|---:|---:|---:|---|
| View application | ✅ | ✅ | ✅ | ✅ | ⚠️ own apps only | `ApplicationDetailHandler.get` uses auth + ownership checks |
| Assign / reassign | ✅ | ✅ | ❌ | ❌ | ❌ | Backend assignment in `PATCH /api/applications/:id` only allows admin/sco |
| Upload documents | ✅ | ✅ | ✅ | ✅ | ✅ | Upload endpoint auth + ownership + stage gates; clients can upload for own case |
| Review documents | ✅ | ✅ | ✅ | ✅ | ❌ | `POST /api/documents/:id/review` |
| Accept/reject documents | ✅ | ✅ | ⚠️ | ⚠️ | ❌ | Any officer can reject; manual acceptance of unverified docs requires admin/sco |
| Request more info (RMI) | ✅ | ✅ | ✅ | ❌ | ❌ | `POST /api/applications/:id/decision` with `request_documents` |
| Run screening | ✅ | ✅ | ✅ | ❌ | ❌ | `POST /api/screening/run` |
| Review screening (first review) | ✅ | ✅ | ✅ | ✅ | ❌ | `POST /api/screening/review` |
| Screening second review | ✅ | ✅ | ❌ | ❌ | ❌ | Backend hard-enforced in `ScreeningReviewHandler` |
| Generate memo | ✅ | ✅ | ✅ | ✅ | ❌ | `POST /api/applications/:id/memo` |
| Validate memo | ✅ | ✅ | ✅ | ✅ | ❌ | `POST /api/applications/:id/memo/validate` |
| Approve memo | ✅ | ✅ | ❌ | ❌ | ❌ | `POST /api/applications/:id/memo/approve` |
| Approve application | ✅ | ✅ | ⚠️ | ⚠️ | ❌ | Canonical path is `/decision` (admin/sco/co). **But `PATCH /api/applications/:id` can set status=approved without role-restricting analyst/co** |
| Reject application | ✅ | ✅ | ⚠️ | ⚠️ | ❌ | Canonical path is `/decision`. **But `PATCH /api/applications/:id` can set status=rejected without role-restricting analyst/co** |
| Override blocker / AI recommendation | ✅ | ✅ | ❌ | ❌ | ❌ | `override_ai=true` in `/decision` requires admin/sco; plus officer sign-off |
| Escalate (to EDD) | ✅ | ✅ | ✅ | ❌ | ❌ | `/decision` with `escalate_edd` |
| Export evidence pack | ✅ | ✅ | ❌ | ❌ | ❌ | `POST /api/applications/:id/export-pack` |
| Edit risk/config/AI settings | ✅ | ⚠️ | ⚠️ | ⚠️ | ❌ | Risk/system/AI write endpoints are admin-only; enhanced-requirement rule writes include sco |

## Key authority mismatch in current state

1. `PATCH /api/applications/:id` permits status changes (including `approved` / `rejected`) for any non-client officer role after ownership checks; this includes analyst and can bypass intended decision authority model.
2. UI permissions and button visibility are not fully aligned; several buttons remain visible but fail only on submit.
