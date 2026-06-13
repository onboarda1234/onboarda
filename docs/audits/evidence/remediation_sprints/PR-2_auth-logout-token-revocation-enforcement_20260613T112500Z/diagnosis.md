# Diagnosis

PR: PR-2 - Auth Logout Token Revocation Enforcement

Linked remediation ID: FSI-002

Base `origin/main` SHA before diagnosis: `b4d1f7387ab03eca849a451bedc98b5208e0ddba`

Branch: `codex/pr2-auth-logout-token-revocation-enforcement`

PR #471 inclusion: confirmed. `origin/main` is PR #471 merge commit
`b4d1f7387ab03eca849a451bedc98b5208e0ddba`, closing FSI-001 through
PR #469, PR #470, and PR #471 before this work started.

## Re-diagnosis Result

FSI-002 still exists on latest deployed main.

Staging diagnosis used fresh client and back-office logins, then replayed the
same bearer token or cookie after logout. The raw redacted response evidence is:

`docs/audits/evidence/remediation_sprints/PR-2_auth-logout-token-revocation-enforcement_20260613T112500Z/runtime_json/diagnosis_logout_revocation_staging_redacted.json`

## Evidence Summary

- Client bearer token worked before logout: `/api/auth/me` 200 and
  `/api/portal/applications` 200.
- Client logout returned 200.
- Same client bearer after logout: `/api/portal/applications` returned 401,
  but `/api/auth/me` still returned 200.
- Client cookie session worked before logout.
- Client logout returned 200.
- Same client cookie after logout: `/api/portal/applications` returned 401,
  but `/api/auth/me` still returned 200.
- Back-office bearer token worked before logout: `/api/auth/me` 200 and
  `/api/applications?view=list&limit=1` 200.
- Back-office logout returned 200.
- Same back-office bearer after logout: `/api/applications` returned 401,
  but `/api/auth/me` still returned 200.
- Back-office cookie session worked before logout.
- Back-office logout returned 200.
- Same back-office cookie after logout: `/api/auth/me` returned 401, but
  `/api/applications?view=list&limit=1` still returned 200.
- Fresh client and back-office logins still worked after logout.

The diagnosis script attempted unauthenticated `/api/version`; staging returned
401, so this diagnosis did not use that response as deployment proof. Post-merge
closure still requires authenticated `/api/version` evidence showing both
`git_sha` and `image_tag` equal the merged main SHA.

## Current Status

FSI-002 remains OPEN before this branch fix. It must remain no better than
PARTIALLY FIXED until the branch is merged, deployed to staging, `/api/version`
matches merged main, API smoke passes, browser smoke passes, and this evidence
pack is complete.
