# A1F-0/A1F-2 Object Authorization Preparation Summary

Audit date: 2026-06-30

Audited `origin/main` SHA: `76700d5b5df878be34a510865ac33ed4988fdadb`

## What Changed

- Added characterization tests for confirmed object-authorization risk endpoints.
- Added strict expected-deny xfail tests for cross-object client access where A1F-1 is pending.
- Added a complete static inventory of bare `require_auth()` handlers.
- No production authorization behavior is changed in this preparation PR.

## Characterization Coverage

- `POST /api/documents/ai-verify`: officer success path plus cross-object client expected 403 xfail.
- `POST /api/kyc/applicant`: officer success path plus cross-object client expected 403 xfail.
- `POST /api/documents/{id}/verify`: officer success path plus cross-object client expected 403 xfail.
- `POST /api/kyc/token`: owning-client success path plus cross-object client expected 403 xfail.
- `POST /api/kyc/document`: officer success path plus cross-object client expected 403 xfail.
- `GET /api/documents/{id}/download`: owner success and cross-object client deny passing tests for the known safe pattern.

## Inventory Totals

- Total Bare Auth Handlers: `53`
- High Risk Object By Id Handlers: `5`
- Client Owned Object Access Handlers With Safe Or Partial Checks: `30`
- Officer Only Candidates: `6`
- Safe Authn Only Handlers: `10`
- Manual Review Handlers: `1`

## Recommended A1F-1 Patch Scope

- `POST /api/documents/([^/]+)/verify` (`DocumentVerifyHandler`): Confirmed gap: resolves document/application and can mutate verification state without client ownership check.
- `POST /api/documents/ai-verify` (`DocumentAIVerifyHandler`): Confirmed gap: reads document/application/directors/UBOs/prescreening context from caller-supplied ids.
- `POST /api/kyc/applicant` (`SumsubApplicantHandler`): Confirmed gap: writes applicant mapping and application prescreening for caller-supplied application_id.
- `POST /api/kyc/token` (`SumsubAccessTokenHandler`): Confirmed gap: generates token for caller-supplied external_user_id without mapping ownership check.
- `POST /api/kyc/document` (`SumsubDocumentHandler`): Confirmed gap: uploads document for caller-supplied applicant_id without mapping ownership check.

## Recommended A1F-3+ Scope

- Convert manual officer-only bare-auth handlers to explicit role checks or `require_backoffice_auth()`.
- Replace `SumsubStatusHandler` prescreening `LIKE` ownership check with deterministic applicant mapping ownership.
- Review `ApplicationsHandler.post` non-client application creation path for explicit officer role intent.
- Keep existing client-owned safe checks as regression coverage.

## Inventory Files

- `docs/security/object-auth/A1F-2-bare-require-auth-inventory.md`
- `docs/security/object-auth/A1F-2-bare-require-auth-inventory.csv`
