# PR-CA3 Runtime E2E Smoke

## Local Safe Fixture Pack

Command:

```bash
PYTHONPATH=arie-backend /opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_complyadvantage_runtime_e2e.py -q
```

Covered paths:

- CA no-hit result yields terminal clear and no approval blockers.
- CA unresolved sanctions/PEP-style hit yields review required and approval blocked.
- CA adverse-media hit yields material match and approval blocker.
- CA provider error yields provider failure state and approval blocker.
- Stale screening yields stale/expired blocker.
- Rescreen fixture refreshes freshness and returns to clean terminal no-hit.
- Approval gate blocks unresolved/failure/stale and allows clean terminal no-hit.
- Queue quarantines `adverse_media_status=clear` when adverse-media evidence exists.
- Duplicate webhook id does not duplicate normalized reports/alerts.
- Retry-pending webhook reconciliation recovers missed detail-fetch work without duplicate alert rows.

Result:

```text
7 passed
```

## Staging Runtime E2E

Pending until PR merge and staging deployment.
