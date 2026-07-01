# A1F-3 Remaining High-Risk Bare Auth Fixes

Audit date: 2026-07-01

Audited `origin/main` SHA: `8cf84e2a1cb8364ed4266f4f771e273bfba69dc0`

## Scope

A1F-3 reviewed the remaining A1F-0/A1F-2 bare `require_auth()` inventory after A1F-1 / PR #631.

This is intentionally narrow. Bare `require_auth()` is not automatically a vulnerability; A1F-3 fixes only remaining high-risk object-by-id access where a client can supply or derive another client's object identifier without a deterministic ownership check.

## Already Fixed By A1F-1

- `POST /api/documents/{id}/verify`
- `POST /api/documents/ai-verify`
- `POST /api/kyc/applicant`
- `POST /api/kyc/token`
- `POST /api/kyc/document`

A1F-3 does not rework these guards.

## Fixed In A1F-3

| Route | Handler | Issue | Fix |
| --- | --- | --- | --- |
| `GET /api/kyc/status/{applicant_id}` | `SumsubStatusHandler` | Client ownership check used `applications.prescreening_data LIKE applicant_id`, which is not a deterministic object-authorization check. | Reuse A1F Sumsub mapping ownership guard. Cross-object clients receive 403 and `authz_denied_not_owner` before the provider status call. |

## Deferred

The following are not object-level IDOR fixes and are deferred to later access-policy/role-gate cleanup:

- `GET /api/case-management/worklist`: existing manual officer-role guard.
- `POST /api/applications/{id}/pre-approval-decision`: existing admin/SCO governance guard; protected approval workflow.
- `POST /api/applications/{id}/export-pack`: existing admin/SCO export guard; protected Evidence Pack workflow.
- `GET /api/config/roles-permissions`: role/config metadata; no caller-supplied object id.
- `POST /api/screening/sanctions`: ad-hoc provider action; no RegMind object id.
- `POST /api/screening/company`: ad-hoc provider action; no RegMind object id.
- `POST /api/applications`: client path binds to token subject; officer-created client application path is role-intent cleanup, not object IDOR.
- `GET /api/dashboard`: canonical dashboard stats are user/fixture scoped; no caller-supplied object id gap identified.

## Residual Follow-Up

Future A1F-3+ or access-policy PRs may convert manual role checks to `require_backoffice_auth()` and review ad-hoc provider action endpoints for explicit officer-only policy. Those changes are deliberately outside this object-authorization PR.
