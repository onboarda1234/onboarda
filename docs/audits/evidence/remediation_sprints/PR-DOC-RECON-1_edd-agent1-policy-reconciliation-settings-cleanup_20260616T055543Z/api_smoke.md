# API Smoke

Local disposable demo server:

```bash
ENVIRONMENT=demo PORT=10100 DB_PATH=/tmp/pr-doc-recon-1-smoke.sqlite UPLOAD_DIR=/tmp/pr-doc-recon-1-uploads JWT_SECRET=local-doc-recon-secret ADMIN_INITIAL_PASSWORD='LocalPass123!' CLAUDE_MOCK_MODE=true /opt/homebrew/bin/python3.11 server.py
```

## Results

- `GET /backoffice`: `200 text/html`
- `GET /portal`: `200 text/html`
- `POST /api/auth/officer/login`: success for seeded local admin
- Authenticated `GET /api/version`: success
- Authenticated `GET /api/config/ai-agents`: Agent 1 description aligned
- Authenticated `GET /api/config/verification-checks`: returned canonical summary and EDD-backed check rows

Local `/api/version` payload:

```json
{
  "git_sha": "unknown",
  "git_sha_short": "unknown",
  "build_time": "unknown",
  "image_tag": "unknown",
  "environment": "demo",
  "service": "regmind-backend"
}
```

Local Agent 1 API payload summary:

```json
{
  "agent_number": 1,
  "name": "Identity & Document Integrity Agent",
  "description": "Agent 1 verifies uploaded onboarding and requested evidence documents using the checks configured in Document Verification Policies. It can verify, flag, block reliance, recommend officer action, and trigger required follow-up. It cannot approve, reject, waive, or perform sanctions/PEP/adverse-media screening.",
  "checks_count": 88
}
```

Local verification checks summary:

```json
{
  "entity_rows": 16,
  "person_rows": 7,
  "edd_rows_in_entity_payload": [
    "aml_policy",
    "bank_statements",
    "bankref",
    "contracts",
    "fin_stmt",
    "source_funds",
    "source_wealth"
  ],
  "sar_str_active": false
}
```

Staging API smoke remains pending until merge and deployment.
