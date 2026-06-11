# RegMind Back Office Administration Deep Audit - 2026-06-11

## 1. Executive Verdict

**Overall administration readiness: weak.** The Administration area is useful as an internal control surface, but it is not pilot-clean or enterprise-credible under buyer diligence standards. The largest issue is not cosmetic: the deployed staging Risk Scoring Model API accepted an invalid model and immediately recomputed risk for 368 applications. That is a P0 data-integrity failure.

**Pilot readiness: not ready until P0/P1 items are fixed and redeployed.** Staging browser navigation is mostly stable as an admin, unauthenticated API probes correctly returned 401, and the main pages loaded without console or network failures in the successful Playwright run. However, several control-changing admin pages lack complete validation, before/after auditability, or buyer-grade operating evidence.

**Enterprise readiness: not ready.** The pages expose important concepts - audit chain, RBAC, risk model, AI checks, AI agents, settings - but enforcement and audit evidence are inconsistent. Agent Health is a placeholder on staging, AI control changes are weakly audited, and lower-role browser/API validation could not be completed because only an administrator staging credential was available.

**Highest-risk findings:**

- P0: Risk model update endpoint accepted a malformed one-dimension model, persisted it, and recomputed 368 applications on staging.
- P1: Deployed Risk Scoring Model endpoint uses the same broad update path for full model and partial country/sector/entity edits, creating a risk of unintentionally blanking config fields.
- P1: AI Agents, AI Verification Checks, and System Settings mutations do not consistently capture before_state and after_state.
- P1: Several admin write APIs are admin-only, but the UI and audit records are not sufficient for regulator-grade defensibility.
- P2: Audit CSV export does not escape formula-like values.
- P2: Agent Health is not buyer-ready and should be hidden or explicitly marked unavailable.

**Final co-founder verdict: weak.**

## 2. Browser Validation Summary

| Item | Result |
| --- | --- |
| Environment URL | `https://staging.regmind.co/backoffice` |
| Date/time | 2026-06-11, Asia/Dubai |
| Browser/tool | Local Playwright Chromium automation |
| Desktop viewport | 1440 x 1000 |
| Narrow viewport | 390 x 844 sample |
| Deployed version | `e848551df7773f1a9676e0a64047445a2beb5258` |
| Build time | `2026-06-10T17:32:14Z` |
| Environment reported by `/api/version` | `staging` |
| Roles tested in browser | Administrator only |
| Role coverage | PARTIAL - SCO, CO, Analyst staging credentials were not available |
| Console errors | 0 in successful run |
| Failed network requests | 0 in successful run |
| HTTP errors after authenticated load | 0 in successful run |
| HAR/trace | Not generated |

Feature flags observed from the authenticated app state: demo mode off, demo banner off, phase 2 features on, regulatory intelligence full on, monitoring dashboard on, SAR workflow on, AI supervisor on, KPI demo data off, role switcher off, document AI analysis on.

Screenshots captured:

- `/Users/Aisha/Onboarda-pr410/tmp/regmind_admin_audit_20260611/desktop-audit.png`
- `/Users/Aisha/Onboarda-pr410/tmp/regmind_admin_audit_20260611/desktop-supervisor-audit.png`
- `/Users/Aisha/Onboarda-pr410/tmp/regmind_admin_audit_20260611/desktop-users.png`
- `/Users/Aisha/Onboarda-pr410/tmp/regmind_admin_audit_20260611/desktop-roles.png`
- `/Users/Aisha/Onboarda-pr410/tmp/regmind_admin_audit_20260611/desktop-risk-model.png`
- `/Users/Aisha/Onboarda-pr410/tmp/regmind_admin_audit_20260611/desktop-ai-checks.png`
- `/Users/Aisha/Onboarda-pr410/tmp/regmind_admin_audit_20260611/desktop-ai-agents.png`
- `/Users/Aisha/Onboarda-pr410/tmp/regmind_admin_audit_20260611/desktop-agent-health.png`
- `/Users/Aisha/Onboarda-pr410/tmp/regmind_admin_audit_20260611/desktop-enhanced-requirements.png`
- `/Users/Aisha/Onboarda-pr410/tmp/regmind_admin_audit_20260611/desktop-resources.png`
- `/Users/Aisha/Onboarda-pr410/tmp/regmind_admin_audit_20260611/desktop-settings.png`
- `/Users/Aisha/Onboarda-pr410/tmp/regmind_admin_audit_20260611/mobile-audit.png`
- `/Users/Aisha/Onboarda-pr410/tmp/regmind_admin_audit_20260611/mobile-risk-model.png`
- `/Users/Aisha/Onboarda-pr410/tmp/regmind_admin_audit_20260611/mobile-resources.png`
- `/Users/Aisha/Onboarda-pr410/tmp/regmind_admin_audit_20260611/mobile-settings.png`

## 3. Scope Discovery and Page Inventory

Primary frontend file: `arie-backoffice.html`.

Primary backend files inspected:

- `arie-backend/server.py`
- `arie-backend/base_handler.py`
- `arie-backend/db.py`
- `arie-backend/supervisor/api.py`
- `arie-backend/tests/test_api.py`
- `arie-backend/tests/test_audit_export.py`
- `arie-backend/tests/test_audit_before_after.py`
- `arie-backend/tests/test_ai_agent_catalog.py`
- `arie-backend/tests/test_enhanced_requirement_settings.py`
- `arie-backend/tests/test_backoffice_monitoring_navigation_static.py`

Source-of-truth tables identified:

- `users`
- `risk_config`
- `system_settings`
- `enhanced_requirement_rules`
- `application_enhanced_requirements`
- `ai_agents`
- `ai_checks`
- `agent_executions`
- `audit_log`
- `supervisor_audit_log`
- `compliance_resources`

| Page | Route/view identifier | API endpoints used | DB tables touched | Allowed roles from code | Write actions available | Expected audit events | Browser check | Backend/API check | Overall verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Audit Trail | `view-audit` | `GET /api/audit`, `GET /api/audit/export` | `audit_log`, joined reference tables | Admin, SCO | Export only | None for export; source events expected for admin actions | Loaded; filters/export present; CSV OK | Auth required; admin/SCO enforced | Needs hardening |
| Audit Chain | `view-supervisor-audit` | `GET /api/supervisor/audit`, `POST /api/supervisor/audit/verify`, `GET /api/audit/supervisor/export` | `supervisor_audit_log` | View admin/SCO/CO/Analyst; verify/export admin/SCO | Verify/export | Supervisor audit verification events expected | Loaded; hash/proof visible | Separate table/API inspected | Acceptable with controls |
| User Management | `view-users` | `GET /api/users`, `POST /api/users`, `PUT /api/users/:id` | `users`, `audit_log` | GET admin/SCO; write admin | Create/update/deactivate | User create/update/deactivate | Loaded; users listed | Auth/RBAC present; audit incomplete | Needs hardening before paid pilot |
| Roles & Permissions | `view-roles` | `GET /api/config/roles-permissions` | Backend policy constants, not DB | Any authenticated role for read | None | None | Loaded | Display source only | Needs hardening |
| Risk Scoring Model | `view-risk-model` | `GET/PUT /api/config/risk-model` | `risk_config`, applications/risk recompute, `audit_log` | GET any auth; PUT admin | Full model, thresholds, country/sector/entity scores | Risk config before/after | Loaded; total 100% displayed | P0 validation failure found on staging | Not pilot-ready |
| AI Verification Checks | `view-ai-checks` | `GET/PUT /api/config/verification-checks` | `ai_checks`, `audit_log` | GET any auth; PUT admin | Replace checks/config | Check config before/after expected | Loaded; 88 active checks | Audit/validation incomplete | Needs hardening before paid pilot |
| AI Agents | `view-ai-agents` | `GET/POST /api/config/ai-agents`, `PUT/DELETE /api/config/ai-agents/:id` | `ai_agents`, `audit_log` | GET any auth; write admin | Create/update/delete/toggle | Agent config before/after expected | Loaded; 10 active agents | Audit incomplete; hard delete | Needs hardening before paid pilot |
| Agent Health | `view-agent-health` | Frontend health panel; backend has agent execution surfaces | `agent_executions` where populated | Frontend admin-only | Refresh/export if present in UI | Operational monitoring/audit expected | Placeholder: "Not Yet Active" | Not meaningful as deployed | Not buyer-ready |
| Enhanced Requirements | `view-enhanced-requirements` | `GET/POST /api/settings/enhanced-requirements`, detail/state endpoints | `enhanced_requirement_rules`, `application_enhanced_requirements`, `audit_log` | GET admin/SCO/CO; writes admin/SCO | Create/update/retire | Rule create/update/state | Loaded; rules/filter visible | Stronger audit than other config pages | Acceptable with controls |
| Resources | `view-resources` | `GET/POST /api/resources`, `GET /api/resources/:id/download` | `compliance_resources`, `audit_log` | GET all officer roles; upload admin/SCO/CO | Upload/download | Upload/download | Loaded; API empty, static links shown | No delete/archive found | Needs hardening |
| Settings | `view-settings` | `GET/PUT /api/config/system-settings`, requirement settings APIs | `system_settings`, `audit_log` | GET any auth; PUT admin | Save system settings/review schedule | Settings before/after expected | Loaded | Audit incomplete; weak guardrails | Needs hardening before paid pilot |

## 4. Page-By-Page Readiness Matrix

| Page | Purpose clarity | Browser result | API/backend result | RBAC result | Audit logging result | Data integrity result | UX credibility | Verdict | Severity |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Audit Trail | Clear | PASS load | PARTIAL | Admin/SCO only | Depends on source actions; export not audited | Mostly source-of-truth | Good but IP/session concerns | Needs hardening | P2 |
| Audit Chain | Clear enough | PASS load | PASS separate chain API | Mixed view access needs rationale | Supervisor chain supports proof | Stronger than trail | Acceptable | Acceptable with controls | P2 |
| User Management | Clear | PASS load | PARTIAL | Backend write admin-only | No before/after on user changes | Persists to users | Acceptable | Needs hardening | P1 |
| Roles & Permissions | Clear as read-only | PASS load | Display-only | Backend source exists | None | No mutation | Buyer may expect enforcement proof | Needs hardening | P2 |
| Risk Scoring Model | Clear | PASS load | FAIL validation | PUT admin-only | before/after present but bad payload accepted | FAIL on staging | Good UI, unsafe API | Not pilot-ready | P0 |
| AI Verification Checks | Clear | PASS load | PARTIAL | PUT admin-only | Count-only audit | Weak schema validation | Acceptable UI | Needs hardening | P1 |
| AI Agents | Clear | PASS load | PARTIAL | Write admin-only | No before/after | Hard delete risk | Good catalog, weak governance evidence | Needs hardening | P1 |
| Agent Health | Clear concept | FAIL credibility | Not meaningful | Frontend admin-only | N/A | N/A | Placeholder | Hide until useful | P1/P2 |
| Enhanced Requirements | Clear | PASS load | PASS/PARTIAL | Writes admin/SCO | before/after for updates/state | Good | Acceptable | Acceptable with controls | P2 |
| Resources | Moderate | PASS load | PARTIAL | Upload CO+; read all roles | Upload/download audit | Empty API; no delete/archive | Weak for buyers | Needs hardening | P2 |
| Settings | Mixed | PASS load | PARTIAL | PUT admin-only | No before/after | Weak validation/guardrails | Risky settings surface | Needs hardening | P1 |

## 5. Finding Register

### ADMIN-AUDIT-001 - P0 - Risk Scoring Model

**Finding:** Deployed staging accepted a malformed risk model and recomputed application risk.

**Evidence:** A safe invalid-payload probe to `PUT /api/config/risk-model` with one invalid dimension returned success and reported `risk_recomputed_apps: 368`, `risk_changed_apps: 208`. A follow-up GET showed the live model had one dimension `BAD`, total weight `1`, and zero thresholds. The model was immediately restored using the canonical five-dimension config; restore verification showed dimensions `D1..D5`, total weight `100`, four thresholds, 64 country entries, 26 sector entries, and 12 entity entries. Restore still changed 58 application risk records.

**Business/compliance risk:** Critical data-integrity breach. An admin or compromised admin session can silently corrupt the regulatory risk model and alter live customer/application risk decisions.

**Recommended fix:** Reject incomplete or semantically invalid model payloads server-side before persistence. Validate required dimension IDs, positive integer weights totaling 100, required thresholds, valid score maps, and non-empty subcriteria. Run recomputation only after validation. Add transaction protection and sampled post-update verification.

**Suggested owner:** Backend/platform.

**Estimated effort:** 1-2 days for fix and tests; additional time for staging data verification.

**Blocker for paid pilot:** Yes.

### ADMIN-AUDIT-002 - P1 - Risk Scoring Model

**Finding:** The deployed endpoint uses the same `PUT /api/config/risk-model` path for full model saves and partial country/sector/entity updates. In the deployed code path, omitted fields can be replaced with empty defaults.

**Evidence:** Frontend country/sector/entity save controls call the same endpoint with partial objects. The backend deployed on staging accepted an incomplete shape and persisted it.

**Business/compliance risk:** A minor admin edit to country risk can unintentionally erase dimensions or thresholds and change risk decisions at scale.

**Recommended fix:** Merge partial payloads into the existing persisted config before validation, or split score-map updates into narrower endpoints with explicit schemas.

**Suggested owner:** Backend/platform.

**Estimated effort:** 1 day.

**Blocker for paid pilot:** Yes.

### ADMIN-AUDIT-003 - P1 - AI Agents

**Finding:** AI agent create/update/delete actions are not audited with before_state and after_state, and delete is a hard delete.

**Evidence:** Backend writes to `ai_agents` and logs string details only. Frontend also injects local in-memory audit rows for agent changes, including `ip: client`, which can mislead operators until reload.

**Business/compliance risk:** AI governance decisions cannot be reconstructed reliably. A buyer or auditor cannot tell what changed, who changed it, and what prior control state was replaced.

**Recommended fix:** Use soft delete/disable for agents, require confirmation for destructive changes, capture before_state/after_state for every create/update/delete/toggle, and display last updated by/at.

**Suggested owner:** Backend + product.

**Estimated effort:** 2-3 days.

**Blocker for paid pilot:** Yes.

### ADMIN-AUDIT-004 - P1 - AI Verification Checks

**Finding:** AI verification checks can be replaced through a broad config save path with weak schema/domain validation and count-only audit detail.

**Evidence:** `PUT /api/config/verification-checks` is admin-only but does not provide regulator-grade before/after audit detail. The UI reports 88 active checks, but the write API does not defensibly prove what changed.

**Business/compliance risk:** Control matrix changes can weaken onboarding gates without adequate accountability.

**Recommended fix:** Validate check IDs, authority type, document type, severity, gate/advisory semantics, active status, and immutable system check constraints. Log before_state and after_state.

**Suggested owner:** Backend + compliance product.

**Estimated effort:** 2-4 days.

**Blocker for paid pilot:** Yes.

### ADMIN-AUDIT-005 - P1 - Settings

**Finding:** System settings writes lack full before/after audit state, dangerous-setting confirmations, and strong value validation.

**Evidence:** `PUT /api/config/system-settings` is admin-only but uses simple validation and a basic audit entry. Browser UI presents broad settings without strong environment/freshness/last-updated evidence.

**Business/compliance risk:** Environment and control settings can be changed without adequate guardrails or reconstructable audit evidence.

**Recommended fix:** Introduce typed server-side schema validation, before/after audit, confirmation flows for dangerous settings, and visible last updated by/at.

**Suggested owner:** Backend + product.

**Estimated effort:** 2-3 days.

**Blocker for paid pilot:** Yes.

### ADMIN-AUDIT-006 - P1 - User Management

**Finding:** User create/update/deactivation is permissioned but not fully audit-defensible.

**Evidence:** `GET /api/users` is admin/SCO, `POST /api/users` and `PUT /api/users/:id` are admin-only. Audit entries exist but do not consistently include before_state and after_state. Browser showed several test-like users in staging.

**Business/compliance risk:** User lifecycle actions are core access-control events. Without complete state history, access reviews and incident reconstruction are weak.

**Recommended fix:** Capture before/after state for role/status/email changes, add explicit deactivation confirmation, add password reset event audit if present, and remove or quarantine stale test accounts.

**Suggested owner:** Backend + security.

**Estimated effort:** 1-2 days.

**Blocker for paid pilot:** Yes.

### ADMIN-AUDIT-007 - P2 - Audit Trail Export

**Finding:** CSV export does not escape formula-like values.

**Evidence:** Audit export uses CSV writer output without formula injection hardening for cells beginning with `=`, `+`, `-`, or `@`.

**Business/compliance risk:** If user-controlled values enter audit details or targets, exported CSVs can trigger spreadsheet formula execution.

**Recommended fix:** Prefix formula-like exported cells with a single quote or tab and add regression tests.

**Suggested owner:** Backend/security.

**Estimated effort:** 0.5 day.

**Blocker for paid pilot:** Should fix before pilot; not P0 unless untrusted fields are confirmed in exports.

### ADMIN-AUDIT-008 - P2 - Audit Trail UX

**Finding:** The frontend creates local fake audit rows for some admin actions, using generic `ip: client`.

**Evidence:** Frontend code pushes local audit rows after settings/resource/agent/risk UI actions. Browser export data showed meaningful IP from backend source rows, but local rows are not source-of-truth.

**Business/compliance risk:** Operators can be shown audit evidence that is not persisted or authoritative, undermining audit defensibility.

**Recommended fix:** Remove local fake audit rows. After a write, reload from `/api/audit` and display only persisted audit events. If immediate optimistic feedback is required, label it explicitly as pending.

**Suggested owner:** Frontend.

**Estimated effort:** 1 day.

**Blocker for paid pilot:** No, if fixed with P1 audit work.

### ADMIN-AUDIT-009 - P1/P2 - Agent Health

**Finding:** Agent Health is not active on staging and should not be shown as an enterprise governance page.

**Evidence:** Browser page displays "Agent Health Monitoring Not Yet Active" while surrounding copy implies real-time monitoring. No credible execution, failure, latency, or last-run evidence was loaded.

**Business/compliance risk:** This page weakens buyer confidence in AI governance because it looks like promised monitoring that is not actually operating.

**Recommended fix:** Hide the page for pilot or connect it to real `agent_executions` data with freshness, failure, latency, and safe error summaries.

**Suggested owner:** Product + backend.

**Estimated effort:** 2-5 days depending on data availability.

**Blocker for paid pilot:** Yes if shown to buyers.

### ADMIN-AUDIT-010 - P2 - Resources

**Finding:** Resources page is thin on staging: API returns no internal resources, static regulatory links carry most of the page, and no delete/archive endpoint was found.

**Evidence:** `GET /api/resources` returned an empty resource list. Backend supports upload/download and audit, but not a complete lifecycle.

**Business/compliance risk:** A compliance resources library with no internal content and incomplete lifecycle looks unfinished.

**Recommended fix:** Add seeded pilot-safe policy resources, categorization, archive/delete with audit, type/size messaging, and direct URL authorization tests.

**Suggested owner:** Product + backend.

**Estimated effort:** 2-3 days.

**Blocker for paid pilot:** No if hidden from buyer walkthrough; yes if positioned as operational policy control.

### ADMIN-AUDIT-011 - P2 - RBAC Coverage

**Finding:** Staging lower-role browser/API validation is incomplete.

**Evidence:** Only administrator staging credentials were available. Unauthenticated probes returned 401. Local code/tests show role checks, but direct SCO/CO/Analyst staging tests were blocked.

**Business/compliance risk:** Buyer diligence will require proof that frontend hiding is backed by server-side enforcement for every role.

**Recommended fix:** Provide controlled staging accounts for Administrator, SCO, CO, and Analyst/read-only; add a Playwright/API RBAC test suite that asserts direct URL and direct API denial.

**Suggested owner:** Security QA.

**Estimated effort:** 1-2 days.

**Blocker for paid pilot:** Yes for enterprise buyer evidence; not necessarily for internal pilot.

### ADMIN-AUDIT-012 - P2 - Roles & Permissions

**Finding:** Roles & Permissions is a display-only matrix and does not prove complete backend enforcement for every admin action.

**Evidence:** `/api/config/roles-permissions` returns backend policy metadata and is readable by any authenticated user. It is not an editable source of truth and does not include denial audit evidence.

**Business/compliance risk:** Buyers may assume this is a permission management surface when it is mostly a static map.

**Recommended fix:** Label as read-only policy reference, add endpoint/action coverage, include last generated version, and link to automated RBAC test results.

**Suggested owner:** Product + backend.

**Estimated effort:** 1-2 days.

**Blocker for paid pilot:** No, but must be clearly positioned.

### ADMIN-AUDIT-013 - P2 - Audit Chain

**Finding:** Audit Chain is meaningfully separate from Audit Trail, but role rationale is not obvious.

**Evidence:** Audit Chain uses `supervisor_audit_log`, shows hash/proof fields, and supports verification/export. General analysts can view supervisor audit entries, while verify/export are admin/SCO.

**Business/compliance risk:** Chain visibility could be appropriate, but enterprise buyers will ask why Analyst can view it and whether sensitive metadata is exposed.

**Recommended fix:** Document role rationale, validate no sensitive payload exposure, and add direct lower-role API tests for view vs verify/export.

**Suggested owner:** Security + product.

**Estimated effort:** 1 day.

**Blocker for paid pilot:** No if documented and tested.

### ADMIN-AUDIT-014 - P3 - Settings UX

**Finding:** Settings mixes control domains and lacks strong freshness markers.

**Evidence:** Browser page presents broad review/system controls in one area without prominent environment, last updated by, last updated at, or pending impact messaging.

**Business/compliance risk:** Lower buyer confidence and higher operator error risk.

**Recommended fix:** Group settings by control domain, show environment, show last updated metadata, and add explicit confirmation for settings that affect workflow or risk.

**Suggested owner:** Product/frontend.

**Estimated effort:** 1-2 days.

**Blocker for paid pilot:** No.

## 6. RBAC Matrix

| Area | Administrator | SCO | CO | Analyst/read-only | Frontend enforcement | Backend enforcement | Gaps |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Audit Trail | View/export | View/export | Hidden/blocked | Hidden/blocked | Admin/SCO visible | Admin/SCO | Lower-role staging tests blocked |
| Audit Chain | View/verify/export | View/verify/export | View | View | Sidebar item observed in admin; code allows supervisor views | View admin/SCO/CO/Analyst; verify admin/SCO | Need rationale and tests |
| User Management | View/create/update | View only | Hidden/blocked | Hidden/blocked | Admin-only page | GET admin/SCO; write admin | Need direct lower-role staging test |
| Roles & Permissions | View | View | View | View | Admin-only in sidebar from observed code | Any authenticated role | Frontend/backend mismatch may be intentional but confusing |
| Risk Scoring Model | View/write | Hidden/read possible by API | Hidden/read possible by API | Hidden/read possible by API | Admin-only page | GET any auth; PUT admin | Read exposure acceptable only if intentional |
| AI Verification Checks | View/write | Hidden/read possible by API | Hidden/read possible by API | Hidden/read possible by API | Admin-only page | GET any auth; PUT admin | Read exposure needs rationale |
| AI Agents | View/write | Hidden/read possible by API | Hidden/read possible by API | Hidden/read possible by API | Admin-only page | GET any auth; writes admin | Read exposure/model metadata needs review |
| Agent Health | View | Hidden | Hidden | Hidden | Admin-only page | Not fully validated | Placeholder should be hidden |
| Enhanced Requirements | View/write | View/write | View | Hidden/blocked | Admin/SCO/CO | GET admin/SCO/CO; writes admin/SCO | Better than most |
| Resources | View/upload/download | View/upload/download | View/upload/download | View/download | Visible broadly | GET all officer roles; upload admin/SCO/CO | Analyst exact behavior needs staging test |
| Settings | View/write | Hidden/read possible by API | Hidden/read possible by API | Hidden/read possible by API | Admin-only page | GET any auth; PUT admin | Read exposure and write audit gaps |

## 7. Auditability Matrix

| Action | Expected audit event | Actual audit event | before_state | after_state | IP/session | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| Risk model full save | `risk_config_update` with before/after | Present in backend | Yes | Yes | IP available from audit trail | FAIL because invalid config accepted |
| Risk country/sector/entity edit | Narrow risk map update with before/after | Same broad risk config endpoint | Partial/deployed unsafe | Partial/deployed unsafe | IP available | FAIL until patched/deployed |
| AI check update | Check config update with before/after | Count/detail audit only | No | No | Likely yes | P1 gap |
| AI agent create/update | Agent config event with before/after | Detail audit only | No | Weak/No | Likely yes | P1 gap |
| AI agent delete | Soft-delete/disable event | Hard delete route found | Weak/No | Weak/No | Likely yes | P1 gap |
| User create | User lifecycle event | Audit entry exists | N/A | Weak/No | Likely yes | P1 gap |
| User update/deactivate | User lifecycle event with before/after | Audit entry exists | No | No | Likely yes | P1 gap |
| System settings save | Settings update with before/after | Basic audit only | No | No | Likely yes | P1 gap |
| Enhanced requirement update/state | Rule update/state with before/after | Present in inspected code | Yes for update/state | Yes for update/state | Likely yes | Acceptable |
| Resource upload | Resource upload event | Present in inspected code | N/A | Metadata detail | Likely yes | Acceptable with lifecycle gaps |
| Resource download | Resource download event | Present in inspected code | N/A | N/A | Likely yes | Acceptable |
| Audit CSV export | Export event optional | Not confirmed | N/A | N/A | N/A | P2 gap |

## 8. API and Security Review

Unauthenticated probes returned 401 for:

- `GET /api/users`
- `GET /api/config/roles-permissions`
- `GET /api/config/risk-model`
- `GET /api/config/verification-checks`
- `GET /api/config/ai-agents`
- `GET /api/config/system-settings`
- `GET /api/settings/enhanced-requirements`

Authenticated administrator GET checks returned 200 for:

- `/api/version`
- `/api/users`
- `/api/config/roles-permissions`
- `/api/config/risk-model`
- `/api/config/verification-checks`
- `/api/config/ai-agents`
- `/api/config/system-settings`
- `/api/settings/enhanced-requirements`
- `/api/resources`
- `/api/audit?limit=10`
- `/api/audit/export?format=csv&limit=10`
- `/api/audit/supervisor/export?format=json`

CSRF posture from code: cookie-authenticated unsafe methods require `X-CSRF-Token`; bearer-token API calls are exempt. That is acceptable for bearer API automation but should be explicitly documented for admin endpoints and tested.

Security gaps found:

- Server-side semantic validation was inadequate for the most sensitive admin config: the risk model.
- CSV export lacks formula injection hardening.
- Lower-role staging direct API tests were blocked by missing credentials.
- Several mutation endpoints have inconsistent JSON error contracts and audit detail quality.
- No provider secrets were observed in browser/API samples, but AI agent model/provider metadata should be reviewed before buyer demos.

## 9. Data Integrity and Auditability Review

The risk model failure proves that the current deployed control surface can persist invalid source-of-truth configuration and trigger downstream recomputation. This is the dominant data-integrity risk. Staging was restored immediately, but the incident must still be treated as a data event because risk scores changed during invalid save and changed again during restore.

Required follow-up:

- Review audit logs for the invalid save and restore timestamps.
- Verify the restored risk model row matches canonical expected config.
- Sample applications whose risk changed during the invalid save and restore.
- Deploy the local semantic guardrail patch or equivalent.
- Add a regression test that rejects malformed model payloads without mutation.

## 10. Browser Evidence

Browser artifact file:

- `/Users/Aisha/Onboarda-pr410/tmp/regmind_admin_audit_20260611/browser-results.json`

Screenshots are listed in section 2.

Successful run telemetry:

- Console errors: 0
- Failed network requests: 0
- Authenticated HTTP errors: 0
- HAR/trace: not generated

Browser limitations:

- Only administrator browser coverage was completed.
- SCO, CO, and Analyst browser checks were blocked by unavailable staging credentials.
- No destructive actions were performed intentionally. The risk model invalid-payload probe unexpectedly mutated staging; it was immediately restored.

## 11. Testing

Existing and focused tests run locally:

- `pytest -q tests/test_api.py::TestRiskModelAdminConfigSafety tests/test_risk_config_integrity.py`
  - Result: 55 passed in 2.79s.
- `python3 -m py_compile server.py rule_engine.py base_handler.py`
  - Result: passed.
- `pytest -q tests/test_audit_export.py tests/test_audit_before_after.py tests/test_ex12_client_security.py tests/test_ai_agent_catalog.py tests/test_enhanced_requirement_settings.py tests/test_backoffice_monitoring_navigation_static.py`
  - Result: 156 passed in 6.65s.

Notes:

- Initial test commands using the wrong path/class selectors failed without running useful tests; corrected commands above passed.
- Local code now includes a small isolated P0 guardrail patch and focused tests for risk model config safety. This report remains audit-first; no broad refactor was performed.

## 12. Recommended Remediation Sprint

Sprint name: **ADMIN-PILOT-CONTROLS-HARDENING**

### ADMIN-P0 immediate fixes

- Deploy server-side semantic validation for `PUT /api/config/risk-model`.
- Split or safely merge partial risk map updates.
- Re-verify staging risk model source row and sampled application risk outputs.
- Add regression tests proving malformed risk model payloads are rejected without mutation or recomputation.

### ADMIN-P1 paid-pilot blockers

- Add before_state/after_state audit logging to AI Agents, AI Verification Checks, System Settings, and User Management mutations.
- Add schema/domain validation for AI Verification Checks and AI Agents.
- Convert AI agent delete to soft delete/disable with confirmation.
- Hide Agent Health or connect it to real execution data.
- Provide staging credentials for Administrator, SCO, CO, and Analyst and run direct URL/API RBAC tests.
- Remove or clearly quarantine stale test-like users before buyer demos.

### ADMIN-P2 production hardening

- Escape formula-like CSV export cells.
- Remove frontend fake audit rows and reload persisted audit entries after writes.
- Add denial audit or security telemetry for sensitive admin endpoint 403s.
- Improve Roles & Permissions as a read-only backend enforcement reference with test evidence.
- Add resource archive/delete lifecycle with audit.
- Document Audit Chain role visibility.

### ADMIN-P3 UI polish

- Add last updated by/at and environment labels to admin config pages.
- Tighten Settings grouping and confirmation copy.
- Add freshness indicators to AI governance pages.
- Clarify difference between Audit Trail and Audit Chain in page subtitles.

### Pages to hide/remove if not useful

- Hide Agent Health from paid-pilot buyer walkthroughs until it has real execution/latency/failure data.
- Hide Resources unless seeded with pilot-safe internal resources or reposition as external regulatory links only.

## 13. Final Paid-Pilot Decision

Do not take this Administration area into a controlled paid pilot as-is. The browser shell is stable, and some RBAC exists, but the risk model P0 and incomplete auditability on multiple admin control pages are direct blockers.

Proceed only after:

1. Risk model validation is deployed and verified.
2. P1 mutation audit gaps are closed.
3. Lower-role staging RBAC evidence is captured.
4. Agent Health is either made real or hidden.
5. CSV export formula hardening is added.

