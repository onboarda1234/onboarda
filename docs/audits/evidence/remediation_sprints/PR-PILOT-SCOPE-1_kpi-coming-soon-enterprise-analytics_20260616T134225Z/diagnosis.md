# PR-PILOT-SCOPE-1 KPI Scope Addendum Diagnosis

## Scope

The KPI Dashboard was added to the non-pilot / enterprise-stage scope after PR-PILOT-SCOPE-1 was merged.

## Finding

The back-office sidebar exposed `KPI Dashboard` as a normal active module. The `view-kpis` page contained live-looking filters, export controls, KPI sections, and the `showView('kpis')` path called `renderKPIDashboard()`.

## Risk

Pilot users could interpret KPI cards, counters, controls, or partial analytics as validated operational reporting. Direct URL access could also route users into an active-looking dashboard.

## Required Outcome

KPI Dashboard must behave like an enterprise / Coming Soon module in pilot:

- visible only with clear Coming Soon labeling,
- direct route access must render a branded Coming Soon placeholder,
- no KPI cards, counters, charts, filters, export controls, or demo/sample numbers may appear in pilot,
- staging and production pilot defaults must keep KPI Dashboard inactive unless explicitly enabled.
