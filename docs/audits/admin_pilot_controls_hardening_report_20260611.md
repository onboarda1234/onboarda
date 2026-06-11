# RegMind Admin Pilot Controls Hardening Report - 2026-06-11

## Executive Verdict

**Final verdict:** PASS WITH MINOR ISSUES

The ADMIN-PILOT-CONTROLS-HARDENING sprint moves the Back Office Administration area from **WEAK / NOT PILOT READY** to **ACCEPTABLE WITH CONTROLS** for a controlled paid pilot.

The paid-pilot blockers from the deep audit have been addressed:

- malformed risk model payloads are rejected before persistence;
- invalid risk updates do not mutate `risk_config`;
- invalid risk updates do not trigger recomputation;
- admin mutation endpoints now persist server-side before/after audit evidence;
- AI agent disable/delete is soft-disable, not hard delete;
- admin CSV export cells are formula-safe;
- frontend fake admin audit rows with `ip: client` were removed;
- Agent Health is hidden from paid-pilot navigation until real telemetry exists.

Remaining controls for pilot: lower-role staging credentials were not available, so staging browser RBAC remains blocked. API-level seeded/local RBAC tests were added and pass.

## PR And Deployment

- **Branch:** `codex/admin-pilot-controls-hardening`
- **PR:** https://github.com/onboarda1234/onboarda/pull/451
- **Latest commit:** `6bbf86e3fb0bc0292fa84c45b9c541075dd71712`
- **Staging deployed SHA:** `6bbf86e3fb0bc0292fa84c45b9c541075dd71712`
- **Final deploy run:** https://github.com/onboarda1234/onboarda/actions/runs/27329885599
- **Environment:** `https://staging.regmind.co/backoffice`

## Fixed Findings Map

| Finding | Status | Evidence |
|---|---:|---|
| ADMIN-AUDIT-001 risk model accepted malformed payload | Fixed | `PUT /api/config/risk-model` returns `400 risk_config_invalid` for malformed dimensions, empty thresholds, unknown `BAD` dimension, empty score maps |
| ADMIN-AUDIT-002 invalid risk update recomputed application risk | Fixed | Invalid response has no `risk_recomputed_apps`; config hash before/after unchanged |
| ADMIN-AUDIT-003 AI agent mutations lacked regulator-grade audit | Fixed | AI agent create/update/toggle/soft-disable log before/after state |
| ADMIN-AUDIT-004 AI verification checks lacked before/after audit | Fixed | Update endpoint validates schema and logs before/after state |
| ADMIN-AUDIT-005 settings mutations lacked before/after audit | Fixed | Settings update validates typed payloads and logs before/after state |
| ADMIN-AUDIT-006 user management mutations lacked before/after audit | Fixed | User create/update/deactivate paths log sanitized before/after state |
| ADMIN-AUDIT-007 CSV formula injection risk | Fixed | Admin CSV exports escape `=`, `+`, `-`, `@` leading cells |
| ADMIN-AUDIT-008 frontend fake audit rows | Fixed | Admin frontend no longer inserts local-only audit evidence rows with generic `ip: client`; it refreshes persisted audit evidence |
| ADMIN-AUDIT-009 Agent Health fake/live-looking page | Fixed for pilot | Agent Health hidden from paid-pilot navigation; unavailable state remains if direct view is invoked |

## Risk Model Evidence

Staging probe artifact: `/Users/Aisha/Onboarda-pr410/tmp/admin_pilot_controls_api_probe_20260611.json`

Observed on staging:

- `/api/version` returned deployed SHA `6bbf86e3fb0bc0292fa84c45b9c541075dd71712`.
- Invalid risk payload returned `400` with `code: risk_config_invalid`.
- Error codes included `risk_dimension_missing`, `risk_dimension_unknown`, `risk_subcriteria_required`, `risk_thresholds_required`, and `risk_score_map_required`.
- `risk_config` hash before invalid write: `cd0a7640d162ec22`.
- `risk_config` hash after invalid write: `cd0a7640d162ec22`.
- Invalid response did not include `risk_recomputed_apps`, proving recompute did not run through the success path.

## Auditability Evidence

Staging synthetic mutations used only reversible/no-op admin config operations:

- AI agent enabled flag toggled and reverted on an existing agent.
- System settings no-op save submitted with current values and required confirmation flag.
- Recent admin audit export found **9** matching admin entries with both `before_state` and `after_state`.
- Audit evidence was read from `/api/audit/export?format=json`, not frontend local rows.

Audit entries are sanitized through server-side state filtering to avoid secret field leakage.

## Browser Evidence

Browser tool: Node Playwright Chromium, headless.

Viewport coverage:

- Desktop: `1440x1000`
- Narrow: `390x844`

Artifact directory: `/Users/Aisha/Onboarda-pr410/tmp/admin_pilot_controls_browser_20260611`

Pages smoke-tested from sidebar route wiring:

- Audit Chain
- User Management
- Roles & Permissions
- Risk Scoring Model
- AI Verification Checks
- AI Agents
- Enhanced Requirements
- Resources
- Settings
- Audit Trail

Result:

- Desktop pages visible: `10/10`
- Narrow pages visible: `10/10`
- Console errors: `0`
- Failed requests: `0`
- HTTP failures: `0`
- Agent Health desktop: `display: none`, `visible: false`
- Agent Health narrow: `display: none`, `visible: false`

Screenshots are saved as `desktop-1440x1000-*.png` and `narrow-390x844-*.png` in the artifact directory.

## RBAC Coverage

| Role | Local/API test coverage | Staging browser coverage | Notes |
|---|---:|---:|---|
| Administrator | Complete | Complete for supplied admin credential | Admin can access and mutate allowed admin APIs |
| SCO | Complete in seeded/local API tests | Blocked | No staging SCO credential provided |
| Compliance Officer | Complete in seeded/local API tests | Blocked | No staging CO credential provided |
| Analyst/read-only | Complete in seeded/local API tests | Blocked | No staging analyst credential provided |
| Unauthenticated | Complete in API tests | Partial via API only | Admin APIs return `401` |

Server-side RBAC tests prove lower roles cannot mutate risk model, AI agents, AI checks, system settings, or users. Frontend hiding was not treated as sufficient.

## Tests Run

Local:

- `python3 -m py_compile server.py rule_engine.py base_handler.py` - passed
- `pytest -q tests/test_api.py::TestRiskModelAdminConfigSafety tests/test_risk_config_integrity.py` - `62 passed`
- `pytest -q tests/test_audit_export.py tests/test_audit_before_after.py tests/test_ai_agent_catalog.py tests/test_enhanced_requirement_settings.py tests/test_ex12_client_security.py tests/test_backoffice_monitoring_navigation_static.py tests/test_api.py::TestAdminPilotMutationAuditabilityAndRBAC` - `169 passed`
- `pytest -q tests/test_backoffice_monitoring_navigation_static.py` - `5 passed`

GitHub Actions final run:

- `ci / lint-and-test` - success, `10m46s`
- `ci / docker-validate` - success, `47s`
- `ci / pdf-tests` - success, `39s`
- `deploy` - success, `5m19s`

Staging:

- API version check - passed
- Invalid risk model probe - passed
- Config unchanged after invalid risk write - passed
- Synthetic AI agent toggle/revert - passed
- Synthetic system settings no-op save - passed
- Audit before/after evidence query - passed
- Admin CSV export formula-safety sample - passed
- Browser smoke desktop/narrow - passed
- Recent CloudWatch `ERROR` sweep - no matching post-deploy error output returned

## Remaining Gaps

- Staging lower-role browser validation remains blocked without SCO/CO/Analyst credentials.
- Agent Health is hidden rather than connected to real execution telemetry. This is acceptable for paid pilot, not enterprise-ready AI governance.
- The staging CI workflow re-runs the full test suite during coverage threshold parsing. This is not a product blocker, but it slows deployment feedback.

## Paid-Pilot Blockers

No ADMIN-PILOT-CONTROLS paid-pilot blockers remain open after this sprint.

Pilot control conditions:

- keep Agent Health hidden until real `agent_executions` telemetry is implemented;
- do not expose lower-role staging as complete until role credentials are available;
- use synthetic-only admin mutation validation on staging.

## Recommended Next Sprint

**Sprint name:** ADMIN-ROLE-EVIDENCE-AND-AGENT-TELEMETRY

Scope:

- provision non-production SCO, CO, and Analyst staging credentials;
- run browser and direct API RBAC probes for each role;
- implement real Agent Health backed by `agent_executions`, or keep it hidden;
- add denial/security telemetry checks for 403 mutation attempts;
- simplify CI coverage threshold calculation to avoid duplicate full-suite execution.

