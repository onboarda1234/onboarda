# PR-1B API Smoke Evidence

## Branch-Level API Evidence

Local tests exercise the notification API and PR-1 boundary regressions:

- `arie-backend/tests/test_pr1b_client_notification_boundary.py`
- `arie-backend/tests/test_pr1_client_api_boundary.py`
- `arie-backend/tests/test_rmi_requests.py`

Result:

```text
13 passed in 6.66s
```

## Staging API Smoke

Pending until PR-1B is merged and deployed.

Required staging checks:

- Client `GET /api/notifications` returns safe JSON.
- No `Officer notes` text.
- No `officer_notes`, `internal_notes`, `review_notes`, memo, supervisor, gate, provider, audit, or internal risk data.
- Safe notification title/message/status/date remains available.
- Client cannot access another client's notification data.
- PR-1 regression remains intact:
  - client denied from `/api/screening/queue`
  - client denied from internal `/api/applications`
  - client denied from another client's application
  - client own portal-safe projection still works
  - back-office access still works
