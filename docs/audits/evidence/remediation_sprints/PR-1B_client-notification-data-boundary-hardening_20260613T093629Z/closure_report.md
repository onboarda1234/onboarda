# PR Closure Report

## PR name

`PR-1B - Client Notification Data Boundary Hardening`

## Linked remediation IDs

- `FSI-001`

## Original issue summary

PR-1 fixed the internal application and screening API boundary, but client portal browser smoke found that `/api/notifications` still returned client-visible messages containing officer notes/internal wording.

## Re-diagnosis result

- Base `origin/main` SHA: `7806ea68addfab81f88c1d58a43a89acd52acb9d`
- Branch: `codex/pr1b-client-notification-data-boundary-hardening`
- Runtime evidence: `runtime_json/diagnosis_notifications_redacted.json`
- Result: leak reproduced on current main/staging.

## Root cause

`GET /api/notifications` returned stored client notification messages and RMI rows verbatim. Some notification creation paths stored officer notes or officer-supplied RMI reasons directly into client-facing payloads. Existing unsafe rows required read-time projection.

## Files changed

- `arie-backend/server.py`
- `arie-backend/tests/test_pr1b_client_notification_boundary.py`
- `docs/audits/evidence/remediation_sprints/PR-1B_client-notification-data-boundary-hardening_20260613T093629Z/*`

## Behaviour before fix

- Client token could call `GET /api/notifications` and receive `Officer notes` text in `message`.
- Client notification payload could include back-office wording such as `runtime audit`.
- Client notification response included full RMI rows, including officer-supplied `reason` and creator metadata.

## Behaviour after fix

- Known client notification types use canonical safe client-facing title/message copy.
- Unknown legacy notifications preserve title/message only if text passes unsafe-pattern screening.
- Client notification responses project only safe fields.
- Legacy `documents_list` values are sanitized at read time.
- Client-visible RMI rows use safe `reason`, safe item labels/descriptions, and omit creator metadata.
- New pre-approval/RMI client notification creation paths store safe client-facing copy.
- PR-1 internal application/screening boundary remains covered by regression tests.

## Tests added/updated

- Added `arie-backend/tests/test_pr1b_client_notification_boundary.py`

## Targeted test results

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest \
  arie-backend/tests/test_pr1b_client_notification_boundary.py \
  arie-backend/tests/test_pr1_client_api_boundary.py \
  arie-backend/tests/test_rmi_requests.py \
  -q
```

Result:

```text
13 passed in 6.66s
```

## Full suite results

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests -q
```

Result:

```text
5253 passed, 25 skipped in 273.13s (0:04:33)
```

## Browser test results, if applicable

Not run at branch stage. Browser smoke remains mandatory after merge and staging deployment.

## Staging deploy evidence

Pending. PR-1B is not merged or deployed yet.

## /api/version evidence

Pending. Required after merged-main staging deployment.

## API smoke test evidence

Branch-level API tests passed. Staging API smoke is pending until PR-1B is merged and deployed.

## Browser smoke test evidence, if applicable

Pending until PR-1B is merged and deployed.

## Screenshots/evidence folder path

`docs/audits/evidence/remediation_sprints/PR-1B_client-notification-data-boundary-hardening_20260613T093629Z/`

## Remaining risks

- FSI-001 cannot be closed until merged-main staging `/api/version`, staging API smoke, and staging browser smoke pass.
- Live staging role-matrix proof beyond the supplied back-office credential remains part of a separate readiness item.

## Items not closed by this PR

- `FSI-001` remains `PARTIALLY FIXED` until post-merge staging validation passes.
- No other remediation item is closed by this PR.

## Final closure verdict

`PARTIALLY FIXED`

Rationale:

Branch-level fix and tests are complete, but closure requires PR merge, staging deployment, `/api/version` SHA alignment, staging notification API smoke, client portal browser smoke, back-office smoke, and complete post-merge evidence.
