# Closure Report

## PR name

`PR-2 - Auth Logout Token Revocation Enforcement`

## Linked remediation IDs

- `FSI-002`

## Original issue summary

Logout does not reliably invalidate the tested client bearer token. A user can
log out, then replay the same bearer token or cookie against authenticated API
routes.

## Re-diagnosis result

- Current `origin/main` SHA: `b4d1f7387ab03eca849a451bedc98b5208e0ddba`
- Branch name: `codex/pr2-auth-logout-token-revocation-enforcement`
- Branch commit SHA: recorded in the PR description and final status output
  after commit creation. The branch SHA is not treated as closure evidence;
  closure requires merged-main staging proof.
- Does the issue still exist on current `origin/main`? Yes.
- Evidence:
  `docs/audits/evidence/remediation_sprints/PR-2_auth-logout-token-revocation-enforcement_20260613T112500Z/runtime_json/diagnosis_logout_revocation_staging_redacted.json`

## Root cause

`TokenRevocationList` trusted a per-process in-memory revocation cache for cache
misses after a one-time database load. If another worker handled logout and
persisted the revoked JTI, an already-running worker with `_db_loaded=True` and
a stale cache accepted the same token because it never checked the database for
that missed JTI.

## Files changed

- `arie-backend/security_hardening.py`
- `arie-backend/tests/test_sprint35.py`

## Behaviour before fix

Logout wrote revocation entries, and same-process local tests passed. In the
staging runtime, the same token/cookie was inconsistently rejected on some
routes and accepted on others after logout, consistent with stale revocation
caches across workers/tasks.

## Behaviour after fix

On every authenticated request, a local revocation-cache miss checks the
database for the exact active JTI before accepting the token. If the JTI is
persisted and unexpired, the worker caches it locally and rejects the request.
The same logic is applied to user-level revocation expiry lookup.

## Tests added/updated

- Added stale-worker logout regressions to `arie-backend/tests/test_sprint35.py`:
  - bearer token replay after logout
  - cookie token replay after logout
  - client bearer replay against `/api/auth/me` and portal application state

## Targeted test results

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest \
  arie-backend/tests/test_sprint35.py::TestLogout::test_logout_revocation_survives_stale_worker_cache_for_bearer \
  arie-backend/tests/test_sprint35.py::TestLogout::test_logout_revocation_survives_stale_worker_cache_for_cookie \
  arie-backend/tests/test_sprint35.py::TestLogout::test_client_logout_revocation_survives_stale_worker_cache \
  -q
```

Result:

```text
3 passed in 1.77s
```

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_sprint35.py::TestLogout -q
```

Result:

```text
13 passed in 2.32s
```

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest \
  arie-backend/tests/test_pr1_client_api_boundary.py \
  arie-backend/tests/test_pr1b_client_notification_boundary.py \
  -q
```

Result:

```text
9 passed in 7.83s
```

## Full suite results

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest \
  arie-backend/tests/test_auth.py \
  arie-backend/tests/test_auth_extended.py \
  arie-backend/tests/test_auth_stability.py \
  arie-backend/tests/test_sprint35.py \
  arie-backend/tests/test_pr1_client_api_boundary.py \
  arie-backend/tests/test_pr1b_client_notification_boundary.py \
  -q
```

Result:

```text
114 passed in 10.02s
```

Whole-repo GitHub CI evidence is pending until PR checks complete.

## Browser test results, if applicable

Pending post-merge staging validation.

## Staging deploy evidence

- Merged main SHA: pending.
- Deployment mechanism: pending.
- ECS/task/image evidence, if applicable: pending.
- Deployed at: pending.

## /api/version evidence

Pending post-merge staging validation.

Verdict:

- [ ] `git_sha` equals merged main SHA
- [ ] `image_tag` equals merged main SHA

## API smoke test evidence

Pending post-merge staging validation.

## Browser smoke test evidence, if applicable

Pending post-merge staging validation.

## Screenshots/evidence folder path

`docs/audits/evidence/remediation_sprints/PR-2_auth-logout-token-revocation-enforcement_20260613T112500Z/`

## Remaining risks

- Whole-repo GitHub CI has not run yet for this branch.
- Merged-main staging deployment proof is not yet available.
- Post-merge API and browser smoke tests are still required.

## Items not closed by this PR

- No other remediation item is closed by this PR.

## Final closure verdict

`PARTIALLY FIXED`

Rationale:

The branch fix and local relevant regression tests are complete, but FSI-002
cannot be marked CLOSED until PR-2 is merged, deployed to staging, `/api/version`
matches merged main, API smoke proves the same bearer/cookie fails after logout,
browser smoke passes, and CI/full-suite evidence is acceptable.
