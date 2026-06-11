# RegMind Admin Role Evidence And Agent Telemetry Sprint

Date: 2026-06-11  
Branch: `codex/admin-role-evidence-agent-telemetry`  
Base validated main: `ca0477afeec0ac9dbc92e9a13a40a5c429a97244`  
Staging URL: `https://staging.regmind.co/backoffice`  
Final verdict: **PASS WITH MINOR ISSUES**

## Executive Summary

The ADMIN-ROLE-EVIDENCE-AND-AGENT-TELEMETRY sprint completed the remaining role evidence checks without touching `main`.

Staging `/api/version` confirmed `ca0477afeec0ac9dbc92e9a13a40a5c429a97244`. Four synthetic officer users were created for Administrator, SCO, CO, and Analyst, exercised through API and browser RBAC checks, then deactivated. Inactive-login checks returned `401` for all four users after cleanup.

Backend enforcement is correct for the targeted admin mutation endpoints: unauthenticated requests return `401`, and SCO/CO/Analyst direct mutation attempts return `403` server-side. Agent Health remains hidden for all tested roles and is still unavailable unless real telemetry is implemented.

One frontend RBAC mismatch was found during browser evidence collection: CO and Analyst could see the Audit Chain nav item even though the backend supervisor-audit endpoint is admin/SCO-only. This branch contains a narrow fix that makes Audit Chain `role-sco-only` and adds `supervisor-audit` to the direct-view guard. Static regression coverage was added.

## Evidence Artifacts

| Artifact | Path |
|---|---|
| Staging API/browser RBAC probe | `/Users/Aisha/Onboarda-pr410/tmp/admin_role_evidence_agent_telemetry_20260611/role_rbac_probe_summary.json` |
| Admin screenshot | `/Users/Aisha/Onboarda-pr410/tmp/admin_role_evidence_agent_telemetry_20260611/browser-rbac-admin-desktop.png` |
| SCO screenshot | `/Users/Aisha/Onboarda-pr410/tmp/admin_role_evidence_agent_telemetry_20260611/browser-rbac-sco-desktop.png` |
| CO screenshot | `/Users/Aisha/Onboarda-pr410/tmp/admin_role_evidence_agent_telemetry_20260611/browser-rbac-co-desktop.png` |
| Analyst screenshot | `/Users/Aisha/Onboarda-pr410/tmp/admin_role_evidence_agent_telemetry_20260611/browser-rbac-analyst-desktop.png` |

No credentials, tokens, cookies, or synthetic passwords are stored in the artifact. User identifiers and emails are represented only as short hashes.

## Synthetic Users

| Role | Created | Login Before Tests | Deactivated | Login After Deactivation |
|---|---:|---:|---:|---:|
| Administrator | `201` | `200` | `200` | `401` |
| SCO | `201` | `200` | `200` | `401` |
| CO | `201` | `200` | `200` | `401` |
| Analyst | `201` | `200` | `200` | `401` |

All synthetic users were prefixed `ADMIN-ROLE-EVIDENCE` and deactivated at the end of the probe.

## API RBAC Results

Unauthenticated admin mutation probes:

| Endpoint | Result |
|---|---:|
| `PUT /api/config/risk-model` | `401` |
| `POST /api/config/ai-agents` | `401` |
| `PUT /api/config/verification-checks` | `401` |
| `PUT /api/config/system-settings` | `401` |
| `POST /api/users` | `401` |

Authenticated read policy:

| Endpoint | Admin | SCO | CO | Analyst |
|---|---:|---:|---:|---:|
| `GET /api/users` | `200` | `200` | `403` | `403` |
| `GET /api/config/risk-model` | `200` | `200` | `200` | `200` |
| `GET /api/config/ai-agents` | `200` | `200` | `200` | `200` |
| `GET /api/config/verification-checks` | `200` | `200` | `200` | `200` |
| `GET /api/config/system-settings` | `200` | `200` | `200` | `200` |
| `GET /api/settings/enhanced-requirements` | `200` | `200` | `200` | `403` |

Authenticated mutation policy:

| Endpoint | SCO | CO | Analyst |
|---|---:|---:|---:|
| `PUT /api/config/risk-model` | `403` | `403` | `403` |
| `POST /api/config/ai-agents` | `403` | `403` | `403` |
| `PUT /api/config/verification-checks` | `403` | `403` | `403` |
| `PUT /api/config/system-settings` | `403` | `403` | `403` |
| `POST /api/users` | `403` | `403` | `403` |

Result: backend enforcement is server-side and not dependent on frontend hiding.

## Denial Telemetry

Generic `require_auth(roles=...)` denials returned correct `403` responses but did not create audit rows for the lower-role admin mutation probes. This matches current implementation: `BaseHandler.require_auth` writes the response and does not call `log_authz_denial`. Some workflow-specific authorization denials do emit audit/telemetry, but generic admin endpoint role denials do not.

This is not a paid-pilot blocker because the server blocks the action, but it remains a production hardening item for security operations evidence.

## Browser RBAC Results

Desktop viewport: `1440x1000`

| Page/Nav Item | Admin | SCO | CO | Analyst |
|---|---:|---:|---:|---:|
| User Management | visible | hidden | hidden | hidden |
| Roles & Permissions | visible | hidden | hidden | hidden |
| Risk Scoring Model | visible | hidden | hidden | hidden |
| AI Verification Checks | visible | hidden | hidden | hidden |
| AI Agents | visible | hidden | hidden | hidden |
| Settings | visible | hidden | hidden | hidden |
| Audit Trail | visible | visible | hidden | hidden |
| Enhanced Requirements | visible | visible | visible | hidden |
| Resources | visible | visible | visible | visible |
| Agent Health | hidden | hidden | hidden | hidden |

Direct `showView(...)` attempts for restricted admin pages did not change the active view for SCO, CO, or Analyst. Audit Trail direct-view attempts were blocked for CO and Analyst. Enhanced Requirements direct-view attempts were blocked for Analyst.

Staging evidence before the branch fix showed Audit Chain visible for CO and Analyst, causing a 403 backend response when data loaded. The branch now fixes this by making Audit Chain admin/SCO-only in navigation and direct-view logic.

## Agent Health

Agent Health remains intentionally hidden:

- `AGENT_HEALTH_ACTIVE = false`
- Navigation item has `data-pilot-hidden="agent-health"`
- Visible state was `false` for Admin, SCO, CO, and Analyst
- Direct page state says monitoring is unavailable until real telemetry is connected

No live provider calls were triggered. Real Agent Health telemetry was not implemented in this sprint. If implemented later, it must use real `agent_executions` data only: last run, status, latency, failure count, safe error summary, and freshness, with no stack traces or secrets.

## Code Changes In This Branch

Narrow frontend RBAC alignment:

- Audit Chain sidebar item now has `role-sco-only`.
- `showView` now treats `supervisor-audit` as admin/SCO-only.
- Static regression added to `tests/test_backoffice_monitoring_navigation_static.py`.

No changes were made to `main` for this sprint branch.

## Tests Run

Local:

```text
python3 -m py_compile server.py rule_engine.py base_handler.py
pytest -q tests/test_backoffice_monitoring_navigation_static.py tests/test_api.py::TestAdminPilotMutationAuditabilityAndRBAC
```

Results:

```text
15 passed
```

Staging:

```text
role_rbac_probe_summary.json
summary.all_unauth_401 = true
summary.all_read_expectations_met = true
summary.lower_role_mutations_403 = true
summary.agent_health_hidden_all_roles = true
summary.cleanup_deactivated_users = true
summary.all_required_checks_ok = true
```

## Remaining Gaps

1. Generic admin endpoint `403` denials are not audit logged by `require_auth(roles=...)`. Recommended future hardening: emit structured security telemetry or audit rows for admin mutation denials.
2. Agent Health is hidden rather than implemented from real `agent_executions` telemetry. This remains acceptable for pilot but should be implemented before enterprise AI governance demos.
3. The Audit Chain frontend RBAC fix is branch-local until this sprint branch is reviewed and merged.

## Final Verdict

**PASS WITH MINOR ISSUES**

The core RBAC evidence gap is closed: synthetic role users prove backend enforcement for admin endpoints, browser visibility mostly matches policy, and Agent Health remains hidden. The branch includes a targeted fix for the one browser mismatch found during testing. Remaining issues are non-blocking hardening items, not paid-pilot blockers.
