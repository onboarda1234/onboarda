# PR-1 API Smoke Evidence

## Local API Smoke

Covered by `tests/test_pr1_client_api_boundary.py` using real Tornado HTTP requests against a test database.

Verified locally:

- client token cannot access `/api/applications`
- client token cannot access `/api/screening/queue`
- client token cannot access `/api/screening/status`
- client token cannot access memo/supervisor/audit/evidence-pack/IDV internal surfaces
- client token cannot access another client's application by ID or ref
- client owned detail remains available and portal-safe
- client portal list remains available through `/api/portal/applications`
- admin/SCO/CO/analyst retain internal application list, screening queue, and provider status access

Raw redacted summary:

`runtime_json/local_api_boundary_summary.json`

## Staging API Smoke

Not completed. Required after merge and staging deployment before FSI-001 can be marked `CLOSED`.
