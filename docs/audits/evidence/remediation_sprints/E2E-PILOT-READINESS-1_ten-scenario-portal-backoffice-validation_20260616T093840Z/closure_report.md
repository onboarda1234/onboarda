# E2E-PILOT-READINESS-1 Closure Report

Run ID: 20260616T093840Z
Evidence folder: /private/tmp/onboarda-e2e-pilot/docs/audits/evidence/remediation_sprints/E2E-PILOT-READINESS-1_ten-scenario-portal-backoffice-validation_20260616T093840Z
Origin/main SHA: 69f751cf12f7a7694ecfcd67ad5f6134c706f393
Staging /api/version: 69f751cf12f7a7694ecfcd67ad5f6134c706f393
Provider mode: {"environment":"staging","is_demo":false,"is_production":false,"integrations":{"opencorporates":"simulated","ip_geolocation":"live","sumsub_identity_verification":"configured","complyadvantage":"live"},"document_policy_registry":"DOC-POLICY-CANONICAL-v1","sar_str_active":false,"features":{"ENABLE_DEMO_MODE":false,"ENABLE_DEMO_BANNER":false,"ENABLE_PHASE2_FEATURES":true,"ENABLE_REGULATORY_INTELLIGENCE_FULL":true,"ENABLE_MONITORING_DASHBOARD":true,"ENABLE_SAR_WORKFLOW":true,"ENABLE_AI_SUPERVISOR":true,"ENABLE_KPI_DEMO_DATA":false,"ENABLE_ROLE_SWITCHER":false,"ENABLE_DOCUMENT_AI_ANALYSIS":true,"FF_SIZE_CAP_CLIENT_REJECT":true,"FF_UX_SPLIT_UPLOAD_VERIFY":false}}
Credentials used: portal asudally@gmail.com; back office asudally@ariefinance.mu; secrets omitted.

## Summary

| Scenario | Portal created? | Back office visible? | Risk score OK? | Docs OK? | Agent 1 OK? | EDD OK? | Screening OK? | Memo/pre-approval OK? | Approval gate OK? | Defects | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S01 Clean low-risk standard company | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P0:prescreening_screening_provider_unavailable | BLOCKED |
| S02 Missing required corporate document | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P0:prescreening_screening_provider_unavailable | BLOCKED |
| S03 Expired or stale document | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P0:prescreening_screening_provider_unavailable | BLOCKED |
| S04 Director/UBO person KYC issue | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P0:prescreening_screening_provider_unavailable | BLOCKED |
| S05 PEP declared | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P0:prescreening_screening_provider_unavailable | BLOCKED |
| S06 High-risk jurisdiction / country-risk scenario | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P2:submit_rate_limit_blocked_test_continuation | BLOCKED |
| S07 High-risk business activity / regulated activity | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P2:submit_rate_limit_blocked_test_continuation | BLOCKED |
| S08 Complex ownership / multiple UBOs | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P2:submit_rate_limit_blocked_test_continuation | BLOCKED |
| S09 Intermediary / introducer involved | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P2:submit_rate_limit_blocked_test_continuation | BLOCKED |
| S10 Manual acceptance / override path | Yes | Yes | Blocked | Not reached | Not reached | Not reached | Blocked | Not reached | Not reached | P2:submit_rate_limit_blocked_test_continuation | BLOCKED |

## Defects

- P0 S01 prescreening_screening_provider_unavailable: Portal prescreening submit returned 503 before pricing/KYC: {"error":"Screening provider temporarily unavailable. Please retry in a moment."}
- P0 S02 prescreening_screening_provider_unavailable: Portal prescreening submit returned 503 before pricing/KYC: {"error":"Screening provider temporarily unavailable. Please retry in a moment."}
- P0 S03 prescreening_screening_provider_unavailable: Portal prescreening submit returned 503 before pricing/KYC: {"error":"Screening provider temporarily unavailable. Please retry in a moment."}
- P0 S04 prescreening_screening_provider_unavailable: Portal prescreening submit returned 503 before pricing/KYC: {"error":"Screening provider temporarily unavailable. Please retry in a moment."}
- P0 S05 prescreening_screening_provider_unavailable: Portal prescreening submit returned 503 before pricing/KYC: {"error":"Screening provider temporarily unavailable. Please retry in a moment."}
- P2 S06 submit_rate_limit_blocked_test_continuation: Portal prescreening submit was rate-limited after earlier failed attempts: {"error":"Rate limit exceeded for submit. Try again later.","retry_after":60}
- P2 S07 submit_rate_limit_blocked_test_continuation: Portal prescreening submit was rate-limited after earlier failed attempts: {"error":"Rate limit exceeded for submit. Try again later.","retry_after":60}
- P2 S08 submit_rate_limit_blocked_test_continuation: Portal prescreening submit was rate-limited after earlier failed attempts: {"error":"Rate limit exceeded for submit. Try again later.","retry_after":60}
- P2 S09 submit_rate_limit_blocked_test_continuation: Portal prescreening submit was rate-limited after earlier failed attempts: {"error":"Rate limit exceeded for submit. Try again later.","retry_after":60}
- P2 S10 submit_rate_limit_blocked_test_continuation: Portal prescreening submit was rate-limited after earlier failed attempts: {"error":"Rate limit exceeded for submit. Try again later.","retry_after":60}

## Screenshots / Runtime JSON

Screenshots: /private/tmp/onboarda-e2e-pilot/docs/audits/evidence/remediation_sprints/E2E-PILOT-READINESS-1_ten-scenario-portal-backoffice-validation_20260616T093840Z/screenshots
Runtime JSON: /private/tmp/onboarda-e2e-pilot/docs/audits/evidence/remediation_sprints/E2E-PILOT-READINESS-1_ten-scenario-portal-backoffice-validation_20260616T093840Z/runtime_json

## Final Verdict

not ready
