# PR-PILOT-SCOPE-1 KPI Scope Addendum Routes And Navigation Inventory

## Navigation

- `KPI Dashboard` remains visible in the back-office sidebar.
- It is tagged with:
  - `Coming Soon`
  - `data-enterprise-coming-soon="true"`

## Direct Routes

These direct routes serve the back-office shell and normalize to the KPI Coming Soon view:

- `/backoffice/kpis`
- `/backoffice/kpi-dashboard`
- `/backoffice/enterprise-analytics`

## Pilot Behavior

- `ENTERPRISE_COMING_SOON_VIEWS['kpis'] = true`
- `showView('kpis')` activates the placeholder and returns before `renderKPIDashboard()`.
- The pilot-visible `view-kpis` region contains no KPI filter, export button, KPI sections, KPI cards, counters, charts, or demo/sample values.
