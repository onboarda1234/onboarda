# Browser Smoke

## Local Browser Smoke

Base URL:

`http://127.0.0.1:18080`

Browser:

Playwright/Chromium using the real back-office login form. Credential values omitted from evidence.

Result:

PASS

Validated:

- Regulatory Intelligence direct URL renders Coming Soon placeholder.
- Sidebar shows Regulatory Intelligence as Coming Soon.
- Supervisor Dashboard direct URL renders Coming Soon placeholder.
- Supervisor Audit/Audit Chain direct URL renders Coming Soon placeholder.
- AI Agents page loads.
- Agent 1 remains active and is not marked Coming Soon.
- Agent 8, Agent 9, and Agent 10 show Coming Soon, Enterprise roadmap, and Not active in pilot.
- Agent 8/9/10 form controls are disabled and run/edit buttons are absent.
- Applications page loads.
- Application Review loads.
- KYC Documents / document review tab loads.
- Screening Queue loads.
- Risk Scoring page loads.
- Enhanced Requirements page loads.
- Portal loads.
- No page errors, failed requests, API 5xx responses, unexpected bad API responses, or blocking console errors were recorded.

Runtime JSON:

- `runtime_json/local_browser_smoke.json`

Screenshots:

- `screenshots/local-regulatory-intelligence-coming-soon.png`
- `screenshots/local-supervisor-dashboard-coming-soon.png`
- `screenshots/local-supervisor-audit-coming-soon.png`
- `screenshots/local-ai-agents-enterprise-roadmap.png`
- `screenshots/local-applications.png`
- `screenshots/local-application-review-documents.png`
- `screenshots/local-screening-queue.png`
- `screenshots/local-risk-scoring.png`
- `screenshots/local-enhanced-requirements.png`
- `screenshots/local-portal.png`

## Staging Browser Smoke

Pending post-merge deployment of merged `main` to staging.
