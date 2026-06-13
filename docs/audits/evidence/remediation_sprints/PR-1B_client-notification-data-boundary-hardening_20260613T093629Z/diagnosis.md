# PR-1B Diagnosis

## Scope

- PR: `PR-1B - Client Notification Data Boundary Hardening`
- Remediation ID: `FSI-001`
- Corrective follow-up to: `PR #469`
- Base `origin/main` SHA: `7806ea68addfab81f88c1d58a43a89acd52acb9d`
- Branch: `codex/pr1b-client-notification-data-boundary-hardening`

## Runtime Reproduction

Using an authenticated staging client token against the deployed PR-1 main SHA:

- Endpoint: `GET /api/notifications`
- Role: client
- Status: `200`
- Raw redacted evidence: `runtime_json/diagnosis_notifications_redacted.json`

Finding:

- The response contained client-visible notification text with `Officer notes: testing of PEP`.
- The same response contained a document-request notification with `runtime audit` wording.
- The response did not expose separate `officer_notes` fields, but unsafe internal wording was embedded in the `message` field.

## Browser Reproduction

The PR-1 post-merge browser smoke failed on the client portal notifications view. The portal rendered the unsafe `Officer notes` text because `arie-portal.html` displays `notification.message` from `/api/notifications` directly.

## Code Diagnosis

Affected code paths:

- `GetClientNotificationsHandler.get` selected stored `client_notifications.message` verbatim.
- `PreApprovalDecisionHandler` stored `Officer notes: {notes}` inside a client notification for `REQUEST_INFO`.
- `_create_structured_rmi_request` stored officer-supplied RMI `reason` in both `client_notifications.message` and the `rmi_requests.reason` row.
- `_load_client_rmi_requests` returned full RMI rows to client notifications, including officer-facing `reason`, `created_by`, and `created_by_name`.

## Diagnosis Result

`FSI-001` still reproduced on current `origin/main` via client-visible notification payloads.
