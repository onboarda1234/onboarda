# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Current Role Matrix (code-grounded)

**Source of truth:** `origin/main` @ `69effaa`. Audit/design only — no code/data/settings changed.
**Generated:** 2026-06-18 (UTC timestamp folder `20260618T173318Z`).
**Supersedes:** the thin root-level `current_role_matrix.md` (commit `1768037`), which under-stated the
existing H-1 actor gate and dual-approval control and over-stated the PATCH bypass.

## Files audited (with line anchors)
- `arie-backend/server.py` — all endpoints / handlers
- `arie-backend/base_handler.py` — `require_auth` (487-497), `require_backoffice_auth` (499-522), audit helpers (565-729)
- `arie-backend/security_hardening.py` — `ApprovalGateValidator.validate_approval` (705), `validate_high_risk_dual_approval` (1345), screening four-eyes re-validation (263-452)
- `arie-backend/branding.py` — `STATUS_LABELS` (95-113)
- `arie-backend/enhanced_requirements.py` — `ALLOWED_WAIVER_ROLES` (36)
- `arie-backoffice.html`, `arie-portal.html` — UI gating

## Roles in scope (canonical keys)
`VALID_ROLES = ("admin", "sco", "co", "analyst")` — `server.py:11905`; plus `client` (portal). Display labels at `server.py:13590-13595`:
- `admin` → Administrator
- `sco` → Senior Compliance Officer (SCO)
- `co` → **Onboarding Officer** (UI label renamed; internal role key remains `co`)
- `analyst` → Analyst
- `client` → portal applicant

## How authority is enforced today (two independent mechanisms)
1. **`require_auth(roles=[...])`** (`base_handler.py:487-497`) — token + role-membership gate at the handler entry. Plain `require_auth()` with **no** `roles=` admits **any authenticated user** (incl. `analyst`); only `require_backoffice_auth` (`499-522`) additionally enforces officer-type and logs denials.
2. **`ApprovalGateValidator`** (`security_hardening.py:705`, `1345`) — *precondition* gate (KYC/screening/memo/EDD/enhanced-requirements + dual-control). **It contains no role/risk-actor logic.** The "co cannot approve HIGH", override-role, and EDD-closure-role rules are enforced **inline, per handler** — present in some handlers, absent in others (see `bypass_risk_findings.md`).
3. **`ROLE_PERMISSION_MATRIX`** (`server.py:13567-13588`) is **advisory/UI reference only** (served via `RolesPermissionsHandler`, `13603`). It is **not consulted at decision time**.

## Capability matrix (observed behavior)

Legend: ✅ allowed · ⚠️ conditional/partial · ❌ blocked

| Capability | admin | sco | co (Onboarding Officer) | analyst | client | Current enforcement (file:line) |
|---|:--:|:--:|:--:|:--:|:--:|---|
| View dashboard / applications | ✅ | ✅ | ✅ | ✅ | ⚠️ own | `ROLE_PERMISSION_MATRIX` view perms 13568-13570; client own-only via `check_app_ownership` `base_handler.py:731` |
| Assign / reassign case | ✅ | ✅ | ❌ | ❌ | ❌ | PATCH branch `server.py:6346` `if role not in ("admin","sco")` → 403 (+ governance audit) |
| Upload / review documents | ✅ | ✅ | ✅ | ⚠️ | ⚠️ own | document handlers; manual acceptance of unverified evidence is senior-gated |
| Request more info (RMI) | ✅ | ✅ | ✅ | ✅ | ❌ | `ApplicationDecisionHandler` roles admin/sco/co `25192`; perm `request_more_information` incl. analyst `13574` |
| Run screening | ✅ | ✅ | ✅ | ❌ | ❌ | `ScreeningHandler` `require_auth(["admin","sco","co"])` `21144` |
| Screening **first** review | ✅ | ✅ | ✅ | ✅ | ❌ | `ScreeningReviewHandler` `require_auth(["admin","sco","co","analyst"])` `20584` |
| Screening **second** review (four-eyes) | ✅ | ✅ | ❌ | ❌ | ❌ | hard-blocked to SCO/admin `server.py:20787-20796`; same-user block vs first reviewer `20779-20785`; re-validated `security_hardening.py:356-375` |
| Generate / validate memo | ✅ | ✅ | ✅ | ⚠️ | ❌ | memo handlers |
| **Approve memo** | ✅ | ✅ | ❌ | ❌ | ❌ | `MemoApproveHandler` `require_auth(["admin","sco"])` `24476` |
| **Approve application (canonical)** | ⚠️ | ✅ | ⚠️ | ❌ | ❌ | `ApplicationDecisionHandler` `25192` admin/sco/co; **co blocked on HIGH/VERY_HIGH** `25353` (H-1 fix); HIGH/VH dual-approval `25515` |
| **Approve application (PATCH path)** | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ❌ | `ApplicationDetailHandler.patch` bare `require_auth()` `6072`; runs precondition gates but **omits co-HIGH actor gate & dual-approval** → see P0 in `bypass_risk_findings.md` |
| Reject application | ⚠️ | ✅ | ✅ | ⚠️ | ❌ | `/decision` admin/sco/co `25192`; also reachable via PATCH `6111-6114` |
| Escalate to EDD | ✅ | ✅ | ✅ | ⚠️ | ❌ | `/decision` `escalate_edd` → `edd_required` `25590`; perm `escalate_to_sco` incl. analyst `13576` |
| Complete / close EDD (`edd_approved`/`edd_rejected`) | ✅ | ✅ | ❌ | ❌ | ❌ | `EDDDetailHandler.patch` closure SCO/admin only + closer≠assigned dual-control `server.py:29816-29822` |
| Override AI / blocker (`override_ai`) | ✅ | ✅ | ❌ | ❌ | ❌ | `/decision` `25316-25326` SCO/admin only; `override_reason` required `25309` |
| Waive enhanced/EDD requirement | ✅ | ✅ | ❌ | ❌ | ❌ | `ALLOWED_WAIVER_ROLES = ("admin","sco")` `enhanced_requirements.py:36` |
| Export evidence pack | ✅ | ✅ | ❌ | ❌ | ❌ | admin/sco only (UI `canExportEvidencePack` + endpoint) |
| Edit risk/system/AI config | ✅ | ❌ | ❌ | ❌ | ❌ | `SENSITIVE_CONFIG_WRITE_ROLES = ["admin"]` `12052`; reads admin/sco/co/analyst `12051` |
| Manage users / roles | ✅ | ⚠️ | ❌ | ❌ | ❌ | `UsersHandler` GET admin/sco, POST/PUT admin |

## Net current-state assessment vs target
- **Already aligned with target:** screening second-review (SCO-only + same-user block), EDD closure (SCO/admin + dual-control), override (SCO/admin), waiver (SCO/admin), memo approval (SCO/admin), assignment (admin/sco), config writes (admin), analyst-cannot-approve on the canonical `/decision` endpoint, and the H-1 co-cannot-approve-HIGH gate on `/decision`.
- **Primary gap:** the generic `PATCH /api/applications/:id` reaches terminal `approved`/`rejected` using bare `require_auth()` and **omits** the co-HIGH actor gate and dual-approval enforced on `/decision`. See `bypass_risk_findings.md` (P0-1).
- **Missing capability:** there is **no `submitted_to_compliance` status and no submit-to-compliance endpoint/button** anywhere (backend grep: zero matches; UI search: absent). See `submit_to_compliance_design.md`.
