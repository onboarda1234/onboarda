# API Smoke

## Local API Smoke

Base URL:

`http://127.0.0.1:18080`

Authentication:

Officer login through `/api/auth/officer/login`; token omitted from evidence.

Result:

PASS

Checked:

- `/api/version` -> 200
- `/api/health` -> 200
- `/api/liveness` -> 200
- `/api/config/environment` -> 200
- `/backoffice/regulatory-intelligence` -> 200
- `/backoffice/reg-intel` -> 200
- `/backoffice/ai-compliance-supervisor` -> 200
- `/backoffice/supervisor-dashboard` -> 200
- `/backoffice/supervisor` -> 200
- `/backoffice/audit-chain` -> 200
- `/backoffice/supervisor-audit` -> 200
- `/backoffice/supervisor-audit-chain` -> 200
- `/backoffice/ai-agents` -> 200

Runtime JSON:

- `runtime_json/local_api_smoke.json`

## Staging API Smoke

Pending post-merge deployment of merged `main` to staging.
