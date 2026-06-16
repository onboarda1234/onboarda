# Feature Flag Inventory

Pilot staging/production defaults verified:
- `ENABLE_REGULATORY_INTELLIGENCE_FULL=false`
- `ENABLE_SAR_WORKFLOW=false`
- `ENABLE_SAR_STR=false`
- `ENABLE_AI_SUPERVISOR=false`
- `ENABLE_SUPERVISOR_DASHBOARD=false`
- `ENABLE_SUPERVISOR_AUDIT=false`
- `ENABLE_KPI_DASHBOARD=false`
- `ENABLE_KPI_DEMO_DATA=false`

Client-safe exposure verified for pilot UI:
- `ENABLE_SAR_STR`
- `ENABLE_SUPERVISOR_DASHBOARD`
- `ENABLE_SUPERVISOR_AUDIT`

Existing security-sensitive flags remain excluded from client-safe config.

