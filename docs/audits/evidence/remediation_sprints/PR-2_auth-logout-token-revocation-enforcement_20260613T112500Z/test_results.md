# Test Results

## Static / Compile Checks

Command:

```bash
/opt/homebrew/bin/python3.11 -m py_compile arie-backend/security_hardening.py arie-backend/tests/test_sprint35.py
```

Result: passed.

Command:

```bash
git diff --check
```

Result: passed.

## Targeted New Regression Tests

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

## Existing Logout Suite

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_sprint35.py::TestLogout -q
```

Result:

```text
13 passed in 2.32s
```

## User-Level Revocation / Logout Integrity

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest \
  arie-backend/tests/test_auth_stability.py::TestUserLevelTokenRevocation \
  arie-backend/tests/test_auth_stability.py::TestLogoutRevocationIntegrity \
  -q
```

Result:

```text
8 passed in 1.95s
```

## FSI-001 Regression Subset

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

Covered:

- Client denied from `/api/screening/queue`.
- Client denied from internal `/api/applications` surfaces.
- Client denied from memo/supervisor/audit/provider/IDV internal endpoints.
- Client denied from another client's application by ID/ref.
- Client notifications remain sanitized.
- Client RMI notification/application payloads exclude `created_by` and
  `created_by_name`.
- Back-office application list and screening queue still work.
