# PR-PILOT-SCOPE-1 Diagnosis

## Scope

PR-PILOT-SCOPE-1 marks non-pilot / enterprise-stage modules as Coming Soon so the controlled pilot does not present incomplete modules as operational.

Base source of truth:

- Repository: `onboarda1234/onboarda`
- Base branch: `origin/main`
- Initial recorded SHA at branch start: `e127b971e3678d3041fe2514186f58f3d4aa39b3`
- Current `origin/main` SHA after rebase: `3c093d6fec18dc8331ceb8be701360bbddb198d8`
- Working branch: `codex/pr-pilot-scope-1-coming-soon-enterprise-modules`

## Findings

The back-office UI previously exposed active-looking surfaces for enterprise-stage modules:

- Regulatory Intelligence had operational upload/list/detail UI and seeded sample intelligence data.
- AI Compliance Supervisor Dashboard exposed active-looking KPIs, rules, pipeline, and escalation panels.
- Supervisor Audit Chain exposed active-looking audit chain controls and filters.
- AI Agents 8, 9, and 10 appeared alongside pilot-active agents on the AI Agent Pipeline page.
- Direct `/backoffice/...` paths for enterprise module aliases were not explicitly routed to clean placeholders.

Backend API/routes were not removed. Normal audit trail and active pilot workflows remain in scope and must continue to load.

## Diagnosis Result

The issue is UI/product-scope exposure, not a backend data deletion problem. The least risky remediation is to keep the navigation discoverable, mark the enterprise modules clearly as Coming Soon/Enterprise/Not active in pilot, and route direct access to branded placeholders without deleting backend code or breaking active workflows.
