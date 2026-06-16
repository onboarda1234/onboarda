# API Smoke

Local API smoke:
- Covered by automated HTTP tests in `test_pilot_scope_backend_lockdown.py`.
- Verified disabled responses for SAR/STR, AI Supervisor, Regulatory Intelligence, Supervisor Audit, Agent 8 enablement, and config environment flag exposure.

Staging API smoke:
- Pending PR merge and staging deploy.
- Required before closure:
  - `/api/version` authenticated proof
  - `/api/health`
  - `/api/liveness`
  - `/api/config/environment`
  - SAR disabled route check
  - AI Supervisor disabled route check
  - KPI config/route check
  - Regulatory Intelligence disabled/Coming Soon route check
  - Agent 8/9/10 disabled route check

Closure cannot be claimed until authenticated `/api/version` proves merged SHA.

