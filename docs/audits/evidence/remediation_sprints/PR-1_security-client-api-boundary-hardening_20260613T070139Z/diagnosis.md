# PR-1 Diagnosis - FSI-001 Client API Boundary

## Source Of Truth

- Repository: `onboarda1234/onboarda`
- PR-0 prerequisite: PR #468 merged to `main`
- Base `origin/main` SHA before diagnosis: `902ba4e59b8108173fe7b2991692ddff9a57c643`
- Branch: `codex/pr1-security-client-api-boundary-hardening`
- Evidence timestamp: `20260613T070139Z`

## Re-Diagnosis Result

FSI-001 still existed on current `origin/main`.

## Pre-Fix Code Evidence

The current `origin/main` implementation allowed active client JWTs through internal handlers:

- `ApplicationsHandler.get` used `self.require_auth()` with no back-office role restriction. Client tokens were filtered by `client_id`, but still used the internal `/api/applications` response path.
- `ScreeningQueueHandler.get` used `self.require_auth()` with no role restriction, exposing the authoritative screening queue to any active authenticated client.
- `APIStatusHandler.get` used `self.require_auth()` with no role restriction, exposing provider runtime/diagnostic status to clients.
- `ApplicationDetailHandler.get` had client ownership checks and a top-level projection, but nested document review metadata and prescreening provider/screening objects were not explicitly stripped.

## Endpoints Re-Diagnosed

| Endpoint | Method | Role/token | Expected | Actual on base main |
| --- | --- | --- | --- | --- |
| `/api/applications` | GET | client | 403 or portal-safe projection only | Internal application list path reachable |
| `/api/applications?view=list&limit=1` | GET | client | 403 | Internal list path reachable |
| `/api/applications/{owned_id}` | GET | client | Owned portal-safe projection only | Top-level projection existed, nested internals not fail-closed |
| `/api/applications/{other_id}` | GET | client | 403 safe denial | Ownership guard existed |
| `/api/screening/queue` | GET | client | 403 | Reachable by any authenticated client |
| `/api/screening/status` | GET | client | 403 | Reachable by any authenticated client |
| `/api/applications/{id}/memo/validation` | GET | client | 403 | Existing role guard present |
| `/api/applications/{id}/memo/supervisor` | GET | client | 403 | Existing role guard present |
| `/api/applications/{id}/audit-log` | GET | client | 403 | Existing role guard present |
| `/api/applications/{id}/kyc/identity-verifications` | GET | client | 403 | Existing role guard present |

## Raw Evidence

Local redacted evidence summary is saved at:

`runtime_json/local_api_boundary_summary.json`

No staging mutation or live provider call was performed.
