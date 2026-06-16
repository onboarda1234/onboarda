# Browser Smoke

Local static/browser-relevant verification:
- Static tests verify Coming Soon placeholders and absence of operational controls.
- Application Review supervisor tab is Coming Soon only.
- Monitoring alert SAR/STR action is disabled and labelled Coming Soon.
- KPI, Regulatory Intelligence, Supervisor Dashboard, Supervisor Audit routes render Coming Soon.
- Agent 8/9/10 render Coming Soon / Enterprise roadmap / Not active in pilot.

Staging browser smoke:
- Pending PR merge and staging deploy.
- Required before closure:
  - authenticate into back office
  - Applications list and one Application Review detail load
  - no active SAR/STR action
  - no active AI Supervisor workflow / Run Analysis
  - KPI routes Coming Soon only, no widgets/counters/charts/export controls
  - Regulatory Intelligence Coming Soon only
  - Supervisor Dashboard/Audit Coming Soon only
  - Agent 8/9/10 Coming Soon only; Agent 1 active
  - portal loads
  - no console/page/request errors

