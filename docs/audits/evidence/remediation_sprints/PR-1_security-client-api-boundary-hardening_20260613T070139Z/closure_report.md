# PR Closure Report

## PR name

`PR-1 - Security Client API Boundary Hardening`

## Linked remediation IDs

- `FSI-001`

## Original issue summary

Client-authenticated tokens could access internal application and screening APIs, including officer-only application list/detail surfaces, screening queue/status, and internal fields that should never be exposed to portal users.

## Re-diagnosis result

- Current `origin/main` SHA: `902ba4e59b8108173fe7b2991692ddff9a57c643`
- Branch name: `codex/pr1-security-client-api-boundary-hardening`
- Branch commit SHA: final branch HEAD is recorded in the PR description and final output after commit finalization. Pre-finalization commit: `581754afa21d5ee4bce0b1947078989dc8a81b32`.
- Does the issue still exist on current `origin/main`? Yes.
- Evidence: `diagnosis.md` and `runtime_json/local_api_boundary_summary.json`

## Root cause

Selected internal handlers used authentication-only gates where role-aware back-office authorization was required. The application detail endpoint also relied on top-level projection but did not explicitly strip nested document review metadata and prescreening provider/screening internals.

## Files changed

- `arie-backend/base_handler.py`
- `arie-backend/server.py`
- `arie-backend/tests/test_pr1_client_api_boundary.py`
- `arie-backend/tests/test_auth.py`
- `arie-backend/tests/test_api.py`
- `arie-backend/tests/test_application_enhanced_requirements.py`
- `docs/audits/evidence/remediation_sprints/PR-1_security-client-api-boundary-hardening_20260613T070139Z/*`

## Behaviour before fix

- Active client tokens could call internal `/api/applications`.
- Active client tokens could call `/api/screening/queue`.
- Active client tokens could call `/api/screening/status`.
- Owned application detail was projected for clients, but nested document and prescreening internals were not explicitly fail-closed.

## Behaviour after fix

- `/api/applications` requires back-office officer roles.
- `/api/screening/queue` requires back-office officer roles.
- `/api/screening/status` requires back-office officer roles.
- Denied internal API attempts are safely audit logged without tokens, cookies, CSRF values, provider secrets, or sensitive PII.
- Client list access remains available through `/api/portal/applications`.
- Client owned detail remains available but strips internal risk, memo, gate, provider, audit/review, document review metadata, and prescreening screening/provider internals.
- Client cross-tenant access by ID or ref remains denied.
- Admin/SCO/CO/analyst access remains available for the internal application list, screening queue, and provider status.

## Tests added/updated

- Added `tests/test_pr1_client_api_boundary.py`
- Updated `tests/test_auth.py` to use `/api/portal/applications` for client active/inactive token checks.
- Updated `tests/test_api.py` to assert clients are denied from `/api/applications` and allowed on `/api/portal/applications`.
- Updated `tests/test_application_enhanced_requirements.py` to assert the new client/internal list boundary.

## Targeted test results

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest \
  tests/test_pr1_client_api_boundary.py \
  tests/test_auth.py \
  tests/test_r9_portal_ownership.py \
  tests/test_r10_portal_ownership.py \
  tests/test_screening_queue.py \
  tests/test_audit_export.py \
  tests/test_ex13_batch_refresh.py \
  tests/test_api.py::TestAuthenticatedAccess::test_applications_endpoint_excludes_fixtures_by_default_and_supports_alias_opt_in \
  tests/test_application_enhanced_requirements.py::test_applications_list_includes_enhanced_operational_summary_and_filters \
  -q
```

Result:

```text
118 passed in 9.19s
```

## Full suite results

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest tests -q
```

Result:

```text
BLOCKED / NEEDS EVIDENCE
Repeated exit 139 segmentation fault during WeasyPrint/Pango CFFI import through evidence_pack_export.py.
Earlier full run completed with 2 stale client /api/applications expectation failures; those tests were updated and passed targeted reruns.
```

## Browser test results, if applicable

Not run at branch stage. Browser smoke remains mandatory after merge and staging deploy because client portal and back-office workflows are visible surfaces affected by this API boundary.

## Staging deploy evidence

- Merged main SHA: Not available. PR not merged.
- Deployment mechanism: Not run.
- ECS/task/image evidence, if applicable: Not available.
- Deployed at: Not deployed.

## /api/version evidence

Endpoint:

```text
Not run. PR not merged/deployed.
```

Result:

```json
{}
```

Verdict:

- [ ] `git_sha` equals merged main SHA
- [ ] `image_tag` equals merged main SHA

## API smoke test evidence

- Endpoint(s): see `api_smoke.md`
- Role/token type: local test client and back-office JWTs
- Expected: client internal APIs forbidden, portal-safe own application allowed, back-office access preserved
- Actual: passed in `tests/test_pr1_client_api_boundary.py`
- Raw evidence path: `runtime_json/local_api_boundary_summary.json`

## Browser smoke test evidence, if applicable

Not run. Required after staging deploy.

## Screenshots/evidence folder path

`docs/audits/evidence/remediation_sprints/PR-1_security-client-api-boundary-hardening_20260613T070139Z/`

## Remaining risks

- Full backend suite is blocked locally by WeasyPrint/Pango native dependency crash.
- No merged-main staging deployment has been performed.
- No staging `/api/version` proof exists.
- No staging API smoke evidence exists.
- No staging browser smoke evidence exists.

## Items not closed by this PR

- `FSI-001` is not closed until merged-main staging validation is complete.
- No other remediation item is closed by this PR.

## Final closure verdict

`PARTIALLY FIXED`

Rationale:

Branch-level code and targeted regression tests harden the client/API boundary, but closure requires PR merge, staging deployment, `/api/version` SHA alignment, staging API smoke, staging browser smoke, and full evidence pack completion.
