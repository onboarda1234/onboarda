# PR-PILOT-SCOPE-1 KPI Scope Addendum Browser Smoke

## Local Browser Smoke

Tooling:

- Playwright Chromium, headless
- Local HTTP harness serving `arie-backoffice.html`
- Stubbed `/api/config/environment` as staging with `ENABLE_KPI_DASHBOARD=false`

Route tested:

- `/backoffice/kpi-dashboard`

Assertions:

- `applyBackofficeHashRoute()` returned true.
- `view-kpis` became active.
- `view-dashboard` was not active.
- KPI page showed:
  - `Coming Soon`
  - `Enterprise Analytics`
  - `Not active in pilot`
- Sidebar KPI item showed `Coming Soon`.
- No `#kpi-period` control existed.
- No KPI sections, KPI cards, charts, or canvases existed in the active KPI view.
- No page errors.
- No console errors.

Result:

```json
{
  "pass": true,
  "route": "/backoffice/kpi-dashboard",
  "routeApplied": true,
  "active": true,
  "hasPeriodControl": false,
  "hasKpiSections": 0,
  "dashboardActive": false,
  "pageErrors": [],
  "consoleErrors": []
}
```
