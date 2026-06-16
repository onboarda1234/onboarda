# E2E-PILOT-READINESS-1 Test Plan

Run ID: 20260616T093840Z
Evidence folder: /private/tmp/onboarda-e2e-pilot/docs/audits/evidence/remediation_sprints/E2E-PILOT-READINESS-1_ten-scenario-portal-backoffice-validation_20260616T093840Z
Origin/main SHA: 69f751cf12f7a7694ecfcd67ad5f6134c706f393
Staging /api/version SHA: 69f751cf12f7a7694ecfcd67ad5f6134c706f393

Scope: create 10 synthetic applications through authenticated staging portal endpoints used by the portal UI, then inspect the resulting back-office/API state. No production data, no backend database inserts, no SAR/STR activation, no remediation changes.

Portal creation method: authenticated client portal API calls matching the portal UI payload and upload endpoints. Screenshots were captured from the actual portal and back-office UI after creation.

Provider mode observed:
```json
{
  "environment": "staging",
  "is_demo": false,
  "is_production": false,
  "integrations": {
    "opencorporates": "simulated",
    "ip_geolocation": "live",
    "sumsub_identity_verification": "configured",
    "complyadvantage": "live"
  },
  "document_policy_registry": "DOC-POLICY-CANONICAL-v1",
  "sar_str_active": false,
  "features": {
    "ENABLE_DEMO_MODE": false,
    "ENABLE_DEMO_BANNER": false,
    "ENABLE_PHASE2_FEATURES": true,
    "ENABLE_REGULATORY_INTELLIGENCE_FULL": true,
    "ENABLE_MONITORING_DASHBOARD": true,
    "ENABLE_SAR_WORKFLOW": true,
    "ENABLE_AI_SUPERVISOR": true,
    "ENABLE_KPI_DEMO_DATA": false,
    "ENABLE_ROLE_SWITCHER": false,
    "ENABLE_DOCUMENT_AI_ANALYSIS": true,
    "FF_SIZE_CAP_CLIENT_REJECT": true,
    "FF_UX_SPLIT_UPLOAD_VERIFY": false
  }
}
```

Credentials used: portal asudally@gmail.com; back office asudally@ariefinance.mu. Secrets omitted.
