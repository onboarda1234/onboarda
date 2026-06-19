# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Backend Endpoint Authority Matrix (code-grounded)

**Source:** `origin/main` @ `69effaa`. Every row carries `file:line` and the verbatim role gate.
Route table: `server.py:31839-32076`. Auth primitives: `base_handler.py:487-522`.

## Legend
- **Decorator gate** = role set passed to `require_auth(roles=[...])` / `require_backoffice_auth(roles=[...])`.
- **Inline gate** = additional role/risk check inside the handler body.
- ⚠️ = authority gap (cross-ref `bypass_risk_findings.md`).

## A. Decision / approval / rejection / escalation paths

| Method | Path | Action | Decorator roles | Inline authority gates | Status write | file:line |
|---|---|---|---|---|---|---|
| POST | `/api/applications/:id/decision` | approve / reject / escalate_edd / request_documents | `["admin","sco","co"]` | **co blocked HIGH/VERY_HIGH** (`25353`); `override_ai` SCO/admin (`25316`); HIGH/VH **dual-approval** (`25515`); memo+screening+stale+`ApprovalGateValidator` chain | `approved`/`rejected`/`edd_required`/`rmi_sent` (`25587`, write `25627`) | handler `25185`; roles `25192` |
| PATCH | `/api/applications/:id` | direct `status` mutation incl. `approved`/`rejected` | **bare `require_auth()` — NO role list** (`6072`) | client blocked from status (`6090`); transition map (`6101-6118`); H-05 review-state for HIGH/VH (`6171`); screening/memo/second-review/stale/`ApprovalGateValidator` (`6182-6287`). **NO co-HIGH actor gate, NO dual-approval** | `approved`/`rejected` (write `6296-6302`) | ⚠️ **P0-1** — handler `5645`, `patch` `6070` |
| POST | `/api/applications/:id/pre-approval-decision` | PRE_APPROVE / REJECT / REQUEST_INFO | `require_auth()` then inline `role in ("admin","sco")` (`8715`) | state guard (`8731`); idempotency (`8769`); notes required (`8752`) | PRE_APPROVE→`kyc_documents` (`8787`); REJECT→`rejected` (`8815`); REQUEST_INFO→`draft` (`8847`) | handler `8680`, post `8691` |
| POST | `/api/applications/:id/memo/approve` | approve compliance **memo** (not app terminal) | `["admin","sco"]` (`24476`) | validation_status pass/pass_with_fixes (`24578`); supervisor verdict (`24690`); `requires_sco`→SCO/admin (`24713`); EDD-routing gate (`24639`); doc-reliance (`24553`); reason+signoff | `compliance_memos.review_status='approved'` (`24720`) | handler `24473`, post `24475` |
| GET | `/api/v1/applications/:id/decision` | read latest decision record | `["admin","sco","co","analyst","client"]` (`public_api.py:82`) | client own-only (`98`); risk hidden from client (`125`) | **read-only — no write** | `public_api.py:78` |

## B. EDD authority

| Method | Path | Action | Decorator roles | Inline authority gates | file:line |
|---|---|---|---|---|---|
| GET/POST | `/api/edd/cases` | list / create | `["admin","sco","co"]` (`29550`, `29602`) | — | `EDDListHandler` `29547` |
| GET/PATCH | `/api/edd/cases/:id` | view / advance / **close** | `["admin","sco","co"]` (`29674`, `29689`) | **closure `edd_approved`/`edd_rejected` → SCO/admin only** + closer≠assigned (dual-control) (`29816-29822`); senior reviewer ≠ assigned (`29808`); decision_reason required (`29827`) | `EDDDetailHandler` `29671` |
| GET/PATCH | `/api/edd/cases/:id/findings` | edit findings (no closure) | `["admin","sco","co"]` (`29993`, `30012`) | — | `EDDFindingsHandler` `29990` |

> **co can open/edit EDD but cannot close it** — decorator lists `co`, terminal closure is SCO/admin-only (target-compliant; decorator is misleading, see P2 in `bypass_risk_findings.md`).

## C. Screening review authority

| Method | Path | Action | Decorator roles | Inline authority gates | file:line |
|---|---|---|---|---|---|
| POST | `/api/screening/run` | run screening | `["admin","sco","co"]` (`21144`) | — | `ScreeningHandler` `21141` |
| POST | `/api/screening/review` | first / second disposition | `["admin","sco","co","analyst"]` (`20584`) | **second review → SCO/admin only** (`20787-20796`); **same-user block vs first reviewer** (`20779-20785`); re-validated read-only at approval (`security_hardening.py:356-375`) | `ScreeningReviewHandler` `20581` |
| GET | `/api/screening/queue` | queue | `require_backoffice_auth(resource="screening:queue")` (`20549`) | — | `ScreeningQueueHandler` `20546` |

## D. Override / waiver

| Method | Path | Action | Roles | Notes | file:line |
|---|---|---|---|---|---|
| POST | `/api/applications/:id/decision` | `override_ai=true` | SCO/admin (`25316`) | `override_reason` required (`25309`); persisted in `detail_info` (`25606-25609`); **no distinct `override_used` audit action** (audit P2) | `25316-25326` |
| (enhanced req) | enhanced-requirement waiver | waive a requirement | `ALLOWED_WAIVER_ROLES=("admin","sco")` | `enhanced_requirements.py:36`; surfaced `server.py:12597` | — |

## E. Assignment

| Method | Path | Action | Roles | Notes | file:line |
|---|---|---|---|---|---|
| PATCH | `/api/applications/:id` (`assigned_to`) | assign/reassign | inline `role in ("admin","sco")` (`6346`) | `reassignment_reason` required; structured audit (see PR-APP-ROLE-AUDIT-GATES-1) | `6344-6359` |
| POST | `/api/monitoring/reviews/:id/assignment` | periodic-review assign | (periodic-review scope — separate track) | out of this audit's scope | `31993` |

## F. Config / settings / users

| Method | Path | Action | Roles | file:line |
|---|---|---|---|---|
| GET/POST | `/api/users` | list / create | GET admin/sco; POST admin | `UsersHandler` |
| PUT | `/api/users/:id` | update role/status | admin | `UserDetailHandler` |
| PUT | `/api/config/risk-model` · `/system-settings` · `/verification-checks` | edit | `SENSITIVE_CONFIG_WRITE_ROLES=["admin"]` (`12052`) | reads `SENSITIVE_CONFIG_READ_ROLES` admin/sco/co/analyst (`12051`) |
| POST/PUT/DELETE | `/api/config/ai-agents[/:id]` | manage AI agents | admin | denials logged `authz_denied_internal_api` (`base_handler.py:511`) |
| GET | `/api/config/roles-permissions` | RBAC reference matrix (advisory) | any authenticated | `RolesPermissionsHandler` `13603` |
| POST/PATCH/enable-disable | `/api/settings/enhanced-requirements*` | manage rules | `ENHANCED_REQUIREMENT_WRITE_ROLES=["admin","sco"]` (`12569`) | — |

## Endpoint-level findings (severity; full detail in `bypass_risk_findings.md`)
- **P0-1** — `PATCH /api/applications/:id` reaches terminal `approved`/`rejected` with bare `require_auth()` and **without** the co-HIGH actor gate / dual-approval present on `/decision`. Authority is split across two endpoints with materially different controls.
- **P1-1** — final-decision authority logic is **duplicated inline** rather than centralized in a single `can_decide` gate (root cause of P0-1); `ApprovalGateValidator` is preconditions-only (no role/actor logic).
- **P1-2** — **no `submitted_to_compliance` status and no submit-to-compliance endpoint** exists (backend grep: zero matches). Submission and approval are conflated.
- **P2-1** — `/decision` co-HIGH gate keys on resolved risk level only (`25353`); PEP/EDD that scores MEDIUM is blocked for `co` only indirectly via memo-borne `mandatory_escalation`/`edd_routing` in `ApprovalGateValidator` (`security_hardening.py:995`, `1005`), not by a first-class actor gate.
- **P2-2** — `analyst` is permitted to perform screening **first** review (`20584`) while excluded from run/decision; confirm this is intended.
