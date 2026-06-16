# Enterprise Surface Inventory

Verified / remediated enterprise surfaces:
- Regulatory Intelligence: sidebar/direct route already renders Coming Soon; backend `/api/regulatory-intelligence*` now returns controlled disabled response when inactive.
- AI Compliance Supervisor: Application Review tab now shows Coming Soon only; `/api/applications/:id/supervisor/run` and `/result` are disabled when inactive.
- Supervisor Dashboard: back-office route renders Coming Soon.
- Supervisor Audit / Audit Chain: back-office route renders Coming Soon; `/api/audit/supervisor/export` is gated by AI Supervisor + Supervisor Audit flags.
- Agent 8, Agent 9, Agent 10: AI Agents UI labels them Coming Soon / Enterprise roadmap / Not active in pilot; backend config API refuses enabling them.
- KPI Dashboard / Enterprise Analytics: remains Coming Soon; KPI demo data and KPI dashboard flags default off in staging/production.

No active-looking enterprise widgets should render in pilot for these modules.

