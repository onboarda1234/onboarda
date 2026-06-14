# PR-DOC1 Browser Smoke

## Local Browser Smoke

Browser: Playwright Chromium, local static server.

URL:

- `http://127.0.0.1:4173/arie-backoffice.html`
- `http://127.0.0.1:4173/arie-portal.html`

Result:

```text
PASS
```

Assertions:

- Back-office document readiness treats a verified-looking document without Agent 1 proof as incomplete.
- Back-office readiness exposes document-specific blocker descriptions.
- Back-office approval blockers include document evidence blockers when the application is otherwise approval-ready.
- Portal skipped verification renders as skipped/manual-review required.
- Portal status-only verified with `missing_agent_execution` is not treated as ready.
- Portal `manual_accepted` is treated as ready.
- Portal verification markup carries `data-reliance-state`.

Screenshots:

- `screenshots/backoffice_document_readiness.png`
- `screenshots/portal_document_readiness.png`

Runtime JSON:

- `runtime_json/local_browser_smoke.json`

## Staging Browser Smoke

Not run at branch stage. Required after merge and staging deployment before DOC-001 can be closed.

