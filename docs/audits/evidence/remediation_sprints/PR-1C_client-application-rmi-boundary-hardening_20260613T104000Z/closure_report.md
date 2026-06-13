# Closure Report

PR name: PR-1C - Client Application RMI Boundary Hardening

Linked remediation ID:
- FSI-001 - Client tokens can access or receive internal compliance data

Original issue summary:
- PR-1B fixed client notification leakage, but post-merge staging smoke found client-owned application detail still exposed RMI creator metadata through `rmi_requests`.

Re-diagnosis result:
- Confirmed on staging after PR-1B deployment at `12be9e5c3d127400b6f74d7013bab1ae63d418b7`.
- `/api/notifications` passed.
- `/api/applications/{own_application_id}` failed portal-safe projection because `rmi_requests[].created_by` and `rmi_requests[].created_by_name` were present.

Root cause:
- `_client_safe_rmi_request(...)` was applied to `/api/notifications`, but `_client_safe_application_detail(...)` did not sanitize raw `rmi_requests` loaded by application detail.

Files changed:
- `arie-backend/server.py`
- `arie-backend/tests/test_pr1b_client_notification_boundary.py`

Behaviour before fix:
- Client-owned application detail included raw RMI request creator metadata.

Behaviour after fix:
- Client-owned application detail projects RMI requests through `_client_safe_rmi_request(...)`.

Tests added/updated:
- `test_client_application_detail_sanitizes_rmi_requests`

Targeted test results:
- PASS - 9 targeted PR-1B/PR-1 tests passed locally.

Full suite results:
- Local full suite BLOCKED by known WeasyPrint/Pango CFFI segmentation fault.
- GitHub CI required before merge.

Browser test results:
- PENDING post-merge staging validation.

Staging deploy evidence:
- PENDING post-merge.

/api/version evidence:
- PENDING post-merge.

API smoke test evidence:
- PENDING post-merge.

Browser smoke test evidence:
- PENDING post-merge.

Screenshots/evidence folder path:
- `docs/audits/evidence/remediation_sprints/PR-1C_client-application-rmi-boundary-hardening_20260613T104000Z/`

Remaining risks:
- FSI-001 remains PARTIALLY FIXED until PR-1C is merged, deployed, `/api/version` matches the merged SHA, and staging API/browser smoke pass.

Items not closed by this PR:
- No remediation item is closed by branch-level code or tests alone.

Final closure verdict:
- NOT CLOSED - pending PR merge, staging deploy, staging API smoke, and browser smoke.
