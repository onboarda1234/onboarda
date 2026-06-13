# Api Smoke

TBD.
# API Smoke

## Branch Diagnosis Smoke

Raw redacted staging diagnosis:

`docs/audits/evidence/remediation_sprints/PR-2_auth-logout-token-revocation-enforcement_20260613T112500Z/runtime_json/diagnosis_logout_revocation_staging_redacted.json`

Result before fix: failed. Reusing the same bearer token or cookie after logout
was inconsistently accepted by authenticated endpoints on staging.

## Post-Merge Staging Smoke

Pending. Required after merge and staging deploy.

Must prove:

- Client bearer token works before logout.
- Client logout succeeds.
- The same client bearer token fails after logout on `/api/auth/me`.
- The same client bearer token fails after logout on the portal-safe
  application endpoint.
- Client cookie session works before logout.
- The same client cookie session fails after logout.
- Back-office bearer/session works before logout.
- The same back-office bearer/session fails after logout.
- Normal login still works after logout.
- FSI-001 regression still passes.
