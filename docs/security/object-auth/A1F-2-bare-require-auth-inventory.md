# A1F-2 Bare require_auth() Inventory

Audit date: 2026-06-30

Audited `origin/main` SHA: `76700d5b5df878be34a510865ac33ed4988fdadb`

Scope: static inventory of handler methods that call bare `require_auth()` with no explicit `roles=` in `arie-backend/server.py`, `arie-backend/supervisor/api.py`, and `arie-backend/public_api.py`. Supervisor and public API modules were inspected; bare rows were found only in `server.py`.

Important: bare `require_auth()` is not automatically a vulnerability. High-risk classification is reserved for handlers that read or write caller-supplied object identifiers without a role-aware ownership/object check.

## Summary

- Total Bare Auth Handlers: `53`
- High Risk Object By Id Handlers: `5`
- Client Owned Object Access Handlers With Safe Or Partial Checks: `30`
- Officer Only Candidates: `6`
- Safe Authn Only Handlers: `10`
- Manual Review Handlers: `1`

Classification counts:

- `client-owned object access`: 1
- `high-risk object-by-id`: 5
- `needs manual review`: 1
- `officer-only but role should be explicit`: 6
- `public/authn-safe`: 10
- `safe existing check`: 30

Follow-up counts:

- `A1F-1 confirmed fix`: 5
- `A1F-3 batch fix`: 4
- `convert to role-restricted`: 6
- `no fix`: 38

## Inventory

| File | Line | Handler | Route | Method | Explicit roles | Object by id | Object type | ID source | Object auth check | Classification | Follow-up | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| arie-backend/server.py | 4619 | ClientChangePasswordHandler | /api/auth/client/change-password | POST | no | no | none | none | no | public/authn-safe | no fix | Self-service password change uses authenticated subject only. |
| arie-backend/server.py | 4658 | MeHandler | /api/auth/me | GET | no | no | none | none | no | public/authn-safe | no fix | Returns current token subject only. |
| arie-backend/server.py | 5093 | ApplicationsHandler | /api/applications | POST | no | yes | application | body/token | partial | client-owned object access | A1F-3 batch fix | Creates a new application. Client path binds to token subject; non-client path accepts client_id and should use explicit officer roles. |
| arie-backend/server.py | 6668 | CaseManagementWorklistHandler | /api/case-management/worklist | GET | no | yes | application; periodic review | query string | manual role check | officer-only but role should be explicit | convert to role-restricted | Manual officer role guard exists after bare auth; convert to require_auth roles/require_backoffice_auth. |
| arie-backend/server.py | 7061 | ApplicationDetailHandler | /api/applications/([^/]+) | GET | no | yes | application | URL path | yes | safe existing check | no fix | Uses check_app_ownership after resolving application. |
| arie-backend/server.py | 7352 | ApplicationDetailHandler | /api/applications/([^/]+) | PUT | no | yes | application; director; UBO | URL path/body | yes | safe existing check | no fix | Uses check_app_ownership before updates. |
| arie-backend/server.py | 7536 | ApplicationDetailHandler | /api/applications/([^/]+) | PATCH | no | yes | application | URL path/body | yes | safe existing check | A1F-3 batch fix | Uses check_app_ownership; status/assignment role checks are manual and could be made explicit. |
| arie-backend/server.py | 7922 | ApplicationDetailHandler | /api/applications/([^/]+) | DELETE | no | yes | application | URL path | yes | safe existing check | no fix | Uses check_app_ownership and client-only draft delete guard. |
| arie-backend/server.py | 9005 | SubmitApplicationHandler | /api/applications/([^/]+)/submit | POST | no | yes | application; director; UBO | URL path | yes | safe existing check | no fix | Uses check_app_ownership before submit. |
| arie-backend/server.py | 9483 | PricingAcceptHandler | /api/applications/([^/]+)/accept-pricing | POST | no | yes | application | URL path | yes | safe existing check | no fix | Uses check_app_ownership before pricing acceptance. |
| arie-backend/server.py | 10037 | KYCSubmitHandler | /api/applications/([^/]+)/submit-kyc | POST | no | yes | application; document | URL path | yes | safe existing check | no fix | Uses check_app_ownership and document gates. |
| arie-backend/server.py | 10230 | PreApprovalDecisionHandler | /api/applications/([^/]+)/pre-approval-decision | POST | no | yes | application | URL path | manual role check | officer-only but role should be explicit | convert to role-restricted | Manual admin/SCO role guard exists; no client ownership guard because endpoint is officer-only by intent. |
| arie-backend/server.py | 10484 | DocumentUploadHandler | /api/applications/([^/]+)/documents | GET | no | yes | application; document | URL path | yes | safe existing check | no fix | Uses check_app_ownership before listing documents. |
| arie-backend/server.py | 10530 | DocumentUploadHandler | /api/applications/([^/]+)/documents | POST | no | yes | application; document; director; UBO | URL path/body/form | yes | safe existing check | no fix | Uses check_app_ownership before upload; validates RMI/person slot in application. |
| arie-backend/server.py | 10979 | DocumentDeleteHandler | /api/applications/([^/]+)/documents/([^/]+) | DELETE | no | yes | application; document | URL path | yes | safe existing check | no fix | Uses check_app_ownership and document must belong to resolved application. |
| arie-backend/server.py | 11237 | DocumentVerifyHandler | /api/documents/([^/]+)/verify | POST | no | yes | document; application | URL path | no | high-risk object-by-id | A1F-1 confirmed fix | Confirmed gap: resolves document/application and can mutate verification state without client ownership check. |
| arie-backend/server.py | 11783 | DocumentVerificationStatusHandler | /api/documents/([^/]+)/verification-status | GET | no | yes | document; application | URL path | yes | safe existing check | no fix | Resolves document to application then calls check_app_ownership. |
| arie-backend/server.py | 12289 | DocumentAIVerifyHandler | /api/documents/ai-verify | POST | no | yes | document; application; director; UBO | request body | no | high-risk object-by-id | A1F-1 confirmed fix | Confirmed gap: reads document/application/directors/UBOs/prescreening context from caller-supplied ids. |
| arie-backend/server.py | 12500 | DocumentDownloadHandler | /api/documents/([^/]+)/download | GET | no | yes | document; application | URL path | yes | safe existing check | no fix | Known safe pattern: denies clients when app.client_id differs from token sub. |
| arie-backend/server.py | 14034 | VersionHandler | /api/version | GET | no | no | none | none | no | public/authn-safe | no fix | Auth-gated build metadata only; no object ids or DB reads. |
| arie-backend/server.py | 15197 | RolesPermissionsHandler | /api/config/roles-permissions | GET | no | no | user/admin config | none | no | officer-only but role should be explicit | convert to role-restricted | RBAC reference matrix is not object-by-id but should be back-office role restricted. |
| arie-backend/server.py | 17736 | ApplicationExportPackHandler | /api/applications/([^/]+)/export-pack | POST | no | yes | application; document; audit | URL path | manual role check | officer-only but role should be explicit | convert to role-restricted | Manual admin/SCO guard exists after bare auth; no client access. |
| arie-backend/server.py | 18207 | DashboardHandler | /api/dashboard | GET | no | yes | application; periodic review; dashboard stats | query string | partial | public/authn-safe | A1F-3 batch fix | Dashboard stats are scoped by current user/fixture filters; consider explicit back-office route if not portal-facing. |
| arie-backend/server.py | 18542 | ActiveDraftsHandler | /api/save-resume/active | GET | no | yes | application; draft session | token subject | yes | safe existing check | no fix | Client-only and joins on client_sessions.client_id plus applications.client_id. |
| arie-backend/server.py | 23037 | SanctionsCheckHandler | /api/screening/sanctions | POST | no | yes | screening item | request body | no | officer-only but role should be explicit | convert to role-restricted | Ad-hoc provider screening has no object id but should not be generally client callable. |
| arie-backend/server.py | 23059 | CompanyLookupHandler | /api/screening/company | POST | no | yes | registry/company lookup | request body | no | officer-only but role should be explicit | convert to role-restricted | Ad-hoc registry lookup has no object id but should be role-scoped if back-office only. |
| arie-backend/server.py | 24015 | CompanyIntakeSearchHandler | /api/company-intake/search | GET | no | yes | registry/company lookup | query string | not applicable | public/authn-safe | no fix | Client intake lookup; no existing RegMind object id. |
| arie-backend/server.py | 24032 | CompanyIntakeProfileHandler | /api/company-intake/company/([^/]+) | GET | no | yes | registry/company lookup | URL path | not applicable | public/authn-safe | no fix | Client intake lookup; no existing RegMind object id. |
| arie-backend/server.py | 24048 | CompanyIntakeOfficersHandler | /api/company-intake/company/([^/]+)/officers | GET | no | yes | registry/company lookup | URL path | not applicable | public/authn-safe | no fix | Client intake lookup; no existing RegMind object id. |
| arie-backend/server.py | 24064 | CompanyIntakePSCsHandler | /api/company-intake/company/([^/]+)/pscs | GET | no | yes | registry/company lookup | URL path | not applicable | public/authn-safe | no fix | Client intake lookup; no existing RegMind object id. |
| arie-backend/server.py | 24078 | CompanyIntakeStartHandler | /api/company-intake/start | POST | no | yes | application; company intake session | request body | yes | safe existing check | no fix | Requires client and creates/reuses draft for token subject. |
| arie-backend/server.py | 24328 | CompanyIntakeConfirmProfileHandler | /api/company-intake/confirm-profile | POST | no | yes | company intake session; application | request body | yes | safe existing check | no fix | Uses _company_intake_get_owned_session for token subject. |
| arie-backend/server.py | 24395 | CompanyIntakeConfirmOfficersHandler | /api/company-intake/confirm-officers | POST | no | yes | company intake session; application; director | request body | yes | safe existing check | no fix | Uses _company_intake_get_owned_session for token subject. |
| arie-backend/server.py | 24479 | CompanyIntakeConfirmPSCsHandler | /api/company-intake/confirm-pscs | POST | no | yes | company intake session; application; UBO | request body | yes | safe existing check | no fix | Uses _company_intake_get_owned_session for token subject. |
| arie-backend/server.py | 24589 | CompanyIntakeSessionHandler | /api/company-intake/session/([^/]+) | GET | no | yes | company intake session; application | URL path | yes | safe existing check | no fix | Uses _company_intake_get_owned_session for token subject. |
| arie-backend/server.py | 24608 | IPCheckHandler | /api/screening/ip | GET | no | no | none | query string | no | public/authn-safe | no fix | No RegMind object id; geolocation helper only. |
| arie-backend/server.py | 24690 | SumsubApplicantHandler | /api/kyc/applicant | POST | no | yes | application; Sumsub applicant | request body | no | high-risk object-by-id | A1F-1 confirmed fix | Confirmed gap: writes applicant mapping and application prescreening for caller-supplied application_id. |
| arie-backend/server.py | 24858 | SumsubAccessTokenHandler | /api/kyc/token | POST | no | yes | Sumsub applicant | request body | no | high-risk object-by-id | A1F-1 confirmed fix | Confirmed gap: generates token for caller-supplied external_user_id without mapping ownership check. |
| arie-backend/server.py | 24877 | SumsubStatusHandler | /api/kyc/status/([^/]+) | GET | no | yes | Sumsub applicant; application | URL path | partial | needs manual review | A1F-3 batch fix | Client path checks applications.client_id and prescreening_data LIKE applicant_id; replace with mapping-table ownership check. |
| arie-backend/server.py | 24903 | SumsubDocumentHandler | /api/kyc/document | POST | no | yes | Sumsub applicant; document | request body | no | high-risk object-by-id | A1F-1 confirmed fix | Confirmed gap: uploads document for caller-supplied applicant_id without mapping ownership check. |
| arie-backend/server.py | 29590 | GetClientNotificationsHandler | /api/notifications | GET | no | yes | notification; RMI request | token subject | yes | safe existing check | no fix | Client-only and filters notifications/RMI by token subject. |
| arie-backend/server.py | 29621 | MarkNotificationReadHandler | /api/notifications/([^/]+)/read | PATCH | no | yes | notification | URL path | yes | safe existing check | no fix | Checks notification.client_id against token sub. |
| arie-backend/server.py | 29645 | ApplicationRMIRequestsHandler | /api/applications/([^/]+)/rmi | GET | no | yes | application; RMI request | URL path | yes | safe existing check | no fix | Uses check_app_ownership before returning RMI requests. |
| arie-backend/server.py | 34485 | AIAssistantHandler | /api/ai/assistant | POST | no | no | none | request body | no | public/authn-safe | no fix | No RegMind object id; simulated compliance assistant response. |
| arie-backend/server.py | 35298 | PortalApplicationsHandler | /api/portal/applications | GET | no | yes | application; periodic review | token subject | yes | safe existing check | no fix | Lists only applications where client_id equals token sub. |
| arie-backend/server.py | 35398 | PortalApplicationPeriodicReviewAttestationHandler | /api/portal/applications/([^/]+)/periodic-review | GET | no | yes | application; periodic review | URL path | yes | safe existing check | no fix | Client-only; helper checks application ownership. |
| arie-backend/server.py | 35418 | PortalApplicationPeriodicReviewAttestationDraftHandler | /api/portal/applications/([^/]+)/periodic-review/save-draft | POST | no | yes | application; periodic review | URL path/body | yes | safe existing check | no fix | Client-only; helper checks application ownership and review state. |
| arie-backend/server.py | 35513 | PortalApplicationPeriodicReviewAttestationSubmitHandler | /api/portal/applications/([^/]+)/periodic-review/submit | POST | no | yes | application; periodic review | URL path/body | yes | safe existing check | no fix | Client-only; helper checks application ownership and review state. |
| arie-backend/server.py | 35630 | PortalApplicationEnhancedRequirementsHandler | /api/portal/applications/([^/]+)/enhanced-requirements | GET | no | yes | application; enhanced requirement | URL path | yes | safe existing check | no fix | Client-only and uses check_app_ownership. |
| arie-backend/server.py | 35692 | PortalApplicationEnhancedRequirementUploadHandler | /api/portal/applications/([^/]+)/enhanced-requirements/([^/]+)/upload | POST | no | yes | application; enhanced requirement; document | URL path/body | yes | safe existing check | no fix | Client-only; uses check_app_ownership and requirement lookup scoped to app. |
| arie-backend/server.py | 36037 | PortalApplicationEnhancedRequirementResponseHandler | /api/portal/applications/([^/]+)/enhanced-requirements/([^/]+)/response | POST | no | yes | application; enhanced requirement | URL path/body | yes | safe existing check | no fix | Client-only; uses check_app_ownership and requirement lookup scoped to app. |
| arie-backend/server.py | 36141 | PortalChangeRequestHandler | /api/portal/change-requests | GET | no | yes | application; change request | token subject | yes | safe existing check | no fix | Lists change requests only across applications owned by token subject. |
| arie-backend/server.py | 36171 | PortalChangeRequestHandler | /api/portal/change-requests | POST | no | yes | application; change request | request body | yes | safe existing check | no fix | Explicitly checks application.client_id equals token subject before creating request. |
