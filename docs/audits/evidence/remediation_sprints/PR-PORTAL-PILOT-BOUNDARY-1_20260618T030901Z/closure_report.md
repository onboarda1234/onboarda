# Closure Report

Task: PR-PORTAL-PILOT-BOUNDARY-1

Timestamp UTC: 20260618T030901Z

Status: not closed.

## Local Remediation Summary

- Client portal wording was neutralized across pending, processing, pre-approval, compliance hold, pricing, KYC, document review, RMI, and approved states.
- Approved state now shows account status, activation progress, application reference, no-further-action messaging, and team next steps only.
- Pricing acceptance routes by backend returned `status`, not frontend risk fields.
- Client document upload/link/persisted-document paths reject invalid person identifiers before DOM path construction or polling.
- Client public API projections hide risk fields and risk aggregations for client tokens.
- Back-office/internal risk visibility is preserved in `arie-backoffice.html` and officer/admin API responses.

## Local Validation

- `pytest arie-backend/tests/test_portal_pilot_boundary_static.py -q`: passed, 7 tests.
- `pytest arie-backend/tests/test_public_api.py -q`: passed, 20 tests.
- `pytest arie-backend/tests/test_pr1_client_api_boundary.py -q`: passed, 5 tests.
- Portal script parse check: passed.

## Closure Gates

- PR merged to main: pending.
- Staging deployed from merged main: pending.
- Authenticated `/api/version.git_sha` matches merge SHA: pending.
- Authenticated `/api/version.image_tag` matches merge SHA: pending.
- CI passes: pending remote CI.
- Staging API smoke passes: pending.
- Authenticated portal browser smoke passes: pending.
- Evidence folder complete: complete for local evidence; staging artifacts pending.

Do not mark this task closed until all closure gates above pass.
