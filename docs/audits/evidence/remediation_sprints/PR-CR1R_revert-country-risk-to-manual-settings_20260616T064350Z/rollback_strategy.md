# PR-CR1R Rollback Strategy

## Strategy Chosen

Option C: restore manual source of truth and leave snapshot model dormant.

## Reason

A full git revert of #502/#503 would remove already-applied migration/schema scaffolding and risk destabilising deployed staging databases. Operational disablement is safer: the manual Risk Scoring Model settings become active again while the PR-CR1 snapshot tables remain dormant reference schema for future governed-country-risk work.

## Operational Rule

For pilot:

- active country-risk scoring source: `risk_config.country_risk_scores`
- active memo country-risk evidence source: manual Risk Scoring Model settings
- active UI: manual editable country-risk groups
- PR-CR1 snapshot: reference only, not active for scoring/memo/gates/UI

## Deferred Follow-Up

Documented future PR: PR-CR3M - Manual Country Risk Change Impact, Rescore and Alerts.

Future PR-CR3M should trigger impact/rescore/alerts when manual country-risk settings change, including score changes, category moves, additions/removals, or other country-affecting risk-model setting changes.
