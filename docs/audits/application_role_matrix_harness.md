# Applications Role Matrix Harness

This harness closes the role-coverage gap from the Applications Page Readiness
Audit on staging SHA `8a0fdefd30930b5d88e7f7d57f47661c8bb0c244`. It does not claim
production readiness and does not alter production role policy.

## Role model discovered

The database and JWT/session model support four back-office roles and one
portal identity:

- `admin` — supervisory and administrative authority.
- `sco` — Senior Compliance Officer.
- `co` — Onboarding Officer.
- `analyst` — review/preparation role without terminal-decision authority.
- `client` — portal actor, not a back-office role.

`BaseHandler.require_backoffice_auth()` protects the Applications list.
Role-specific state changes use `require_auth(roles=...)`,
`can_decide_application()`, and `authorize_signoff_ownership()`. Client-owned
application detail and document reads deliberately share the back-end route but
are projected through explicit client-safe allow lists. Activity Log and
evidence-pack routes are officer-only.

## Current permission matrix

| Capability | Admin | SCO | CO | Analyst | Client |
|---|---|---|---|---|---|
| Applications list | Allow | Allow | Allow | Allow | Deny |
| Application detail | Allow | Allow | Allow | Allow | Own, client-safe projection only |
| Documents read | Allow | Allow | Allow | Allow | Own, client-safe projection only |
| Activity Log | Allow | Allow | Allow | Allow | Deny |
| Evidence pack | Allow | Allow | Allow | Allow | Deny |
| Memo generate/validate | Allow | Allow | Allow | Allow | Deny |
| Memo approve | Allow | Allow | Deny | Deny | Deny |
| Screening first review | Allow | Allow | Allow | Allow | Deny |
| Screening second review | Allow | Allow | Deny | Deny | Deny |
| IDV resolution | Allow | Allow | Conditional | Deny | Deny |
| Submit/move to compliance | Allow | Allow | Allow | Deny | Deny |
| Final approve LOW/MEDIUM | Conditional | Conditional | Conditional | Deny | Deny |
| Final approve HIGH/VERY_HIGH | Dual-control conditional | Dual-control conditional | Deny | Deny | Deny |
| Final reject | Conditional | Conditional | Conditional | Deny | Deny |
| Pre-approval decision | Allow | Allow | Deny | Deny | Deny |
| Assignment/reassignment | Allow | Allow | Deny | Deny | Deny |

All conditional decisions remain subject to stage, risk, evidence, screening,
IDV, memo, blocker, ownership, sign-off, and dual-control gates.

## Staging seed safety

The seed command refuses to run unless all of these are true:

- `ENVIRONMENT=staging`.
- `ALLOW_APPLICATION_ROLE_SEED=1`.
- `ROLE_AUDIT_ALLOWED_DB_HOST` exactly matches the PostgreSQL `DATABASE_URL` host.
- The database identity contains no production marker.
- The explicit confirmation token is supplied.

Passwords are generated per actor and written only to a local `0600` JSON
artifact under `/tmp` (or an operator-selected output directory). The evidence
manifest records IDs and emails but not passwords. Every application has
`is_fixture=true` and a `ROLEAUDIT-<timestamp>` label. The harness provides a
single command to disable every synthetic account after validation; fixture
rows and audit evidence are intentionally retained unless an approved cleanup
procedure removes them.

## Local validation

```bash
python3.11 -m pytest arie-backend/tests/test_application_role_matrix.py -q
python3.11 -m pytest arie-backend/tests/test_auth_extended.py -q
python3.11 -m pytest arie-backend/tests/test_api.py -k "applications or audit or documents or approval or role" -q
```

## Staging validation after merge and deploy

1. Confirm `/api/version` SHA alignment and staging readiness.
2. Export the staging `DATABASE_URL` and its exact host separately.
3. Seed the harness with the guarded command.
4. Run the API validator using the generated manifest and credential artifact.
5. Run the harness `browser` command. It invokes `staging_browser_smoke.js`
   separately for SCO, CO, and analyst, using each generated credential and
   assigned application ID without putting passwords in command arguments.
6. Capture screenshots, browser console output, API report, and CloudWatch
   counts for the validation window.
7. Disable all synthetic accounts with the guarded `disable` command.

Example (values are operator-supplied and never committed):

```bash
ENVIRONMENT=staging \
ALLOW_APPLICATION_ROLE_SEED=1 \
ROLE_AUDIT_ALLOWED_DB_HOST="$STAGING_DB_HOST" \
python3.11 arie-backend/scripts/qa/application_role_matrix_harness.py seed \
  --confirm I-UNDERSTAND-STAGING-ROLE-AUDIT-WRITES
```

Using the two artifact paths printed by `seed`:

```bash
python3.11 arie-backend/scripts/qa/application_role_matrix_harness.py validate \
  --manifest "$ROLE_AUDIT_MANIFEST" \
  --credentials "$ROLE_AUDIT_CREDENTIALS" \
  --out /tmp/application-role-matrix-api.json

python3.11 arie-backend/scripts/qa/application_role_matrix_harness.py browser \
  --manifest "$ROLE_AUDIT_MANIFEST" \
  --credentials "$ROLE_AUDIT_CREDENTIALS" \
  --out-dir /tmp/application-role-matrix-browser

ENVIRONMENT=staging \
ALLOW_APPLICATION_ROLE_SEED=1 \
ROLE_AUDIT_ALLOWED_DB_HOST="$STAGING_DB_HOST" \
python3.11 arie-backend/scripts/qa/application_role_matrix_harness.py disable \
  --manifest "$ROLE_AUDIT_MANIFEST" \
  --confirm I-UNDERSTAND-STAGING-ROLE-AUDIT-WRITES
```

Known ambiguity: the backend Roles & Permissions reference lists analysts for
`request_more_information`, while the canonical `/decision` endpoint permits
only admin/SCO/CO. The harness records the endpoint behavior as authoritative;
policy alignment is a follow-up and is not changed here.

Known audit limitation: some existing rejected-governance writers do not yet
populate `request_id`. The harness requires immutable `application_id`, actor,
and role on those denials; successful document-review actions additionally prove
`request_id` propagation. This PR does not broaden into a general audit-writer
backfill.
