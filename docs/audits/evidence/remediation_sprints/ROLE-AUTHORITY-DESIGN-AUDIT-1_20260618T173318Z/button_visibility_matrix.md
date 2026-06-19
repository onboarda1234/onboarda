# ROLE-AUTHORITY-DESIGN-AUDIT-1 — Button Visibility Matrix (code-grounded)

**Source:** `arie-backoffice.html`, `arie-portal.html` @ `origin/main` `69effaa`. Backend cross-ref `arie-backend/server.py`.

## Client-side permission machinery (back office)
- Policy fetched from backend: `ROLE_PERMISSIONS_PROMISE = boApiCall('GET','/config/roles-permissions',...)` (`arie-backoffice.html:5424`); global `ROLE_PERMISSIONS` (`5815`); loader `ensureRolePermissionsLoaded()` (`5420`).
- `currentUserRole()` (`5819`); `hasPermission(id)` (`5823-5830`, **fails closed** if policy unloaded); `assertPermission(id)` (`5832-5838`, click-time guard → "Insufficient permissions" toast).
- Header note (`5818`): *"Client-side permission helpers (defence-in-depth, NOT a replacement for backend auth)."*
- Visibility controller: `syncApplicationActionPermissions(app)` (`5851-5873`). **Key behavior:** it gates only the **pre-approval** buttons, **Reassign**, and **Export Pack** by permission/status. The **standard Approve / More Info / Reject / Override / Escalate** buttons are **always rendered** (no role/risk hide) and rely solely on click-time `assertPermission` + client-side approval blockers.

## Matrix

| Button | render line | visibility condition | client-side check | backend authority | mismatch + severity |
|---|---|---|---|---|---|
| Approve (`btn-approve`) | 1476 | always shown when not pre-approval (no role/risk hide) | `approveApplication()` `23227` → `assertPermission('approve_low_medium')` (admin/sco/co) | `/decision` admin/sco/co; **co 403 on HIGH/VERY_HIGH** `server.py:25353` | **P1** — UI shows Approve + opens modal for `co` on HIGH/VERY_HIGH; only a client blocker (`22050-22051`) disables confirm; backend is fail-closed but UX misleads |
| More Info (`btn-rmi`) | 1477 | always shown | `requestMoreInfo()` `24321` → `request_more_information` | admin/sco/co/analyst | OK (consistent) |
| Reject (`btn-reject`) | 1481 | always shown (More ▾) | `rejectApplication()` `23240` → `reject_applications` | admin/sco/co | OK |
| Override (`btn-override`) | 1482 | always shown (More ▾), no role hide | `confirmOverride()` `24276` → `override_ai_risk_score` (admin/sco) | `/decision` override SCO/admin `25316` | **P2** — UI-shows-backend-rejects for co/analyst (click-deny only) |
| Escalate (`escalateCase`) | 1483 | always shown (More ▾) | `escalateCase()` `24632` → `escalate_to_sco` (admin/sco/co/analyst) | `/decision` `escalate_edd` admin/sco/co | OK |
| Reassign (`btn-reassign`) | 1484 | **gated visible**: `setDetailActionVisibility('btn-reassign', hasPermission('assign_reassign_cases'))` `5866` | `reassignCase()` `24387` → `assign_reassign_cases` | PATCH inline admin/sco `6346` | OK (hidden + checked) |
| Export Pack (`btn-export-pack`) | 1485 | **gated visible**: `canExportEvidencePack()` `5867` (admin/sco) | `openExportPackModal()` | export endpoint admin/sco | OK |
| Pre-Approve / PA-Reject / PA-RMI (`btn-preapprove`, `…-reject`, `…-rmi`) | 1468-1470 | **gated visible**: `isPreApproval && hasPermission('approve_high_very_high')` `5863-5865`,`5870` | handlers `24678/24697/24716` → `approve_high_very_high` | `/pre-approval-decision` SCO/admin `8715` | OK |
| **Approve Memo** (`btn-approve-memo`) | 1645 | always rendered; enable/disable by **validation state, not role** (`29165-29214`) | `approveMemo()` `28946` → `assertPermission('approve_low_medium')` (admin/sco/**co**) | `MemoApproveHandler` **admin/sco only** `24476` | **P1** — UI enables memo-approve for `co`; backend returns 403. Client uses wrong permission id |
| Continue Review (`btn-rmi-continue`) | 10410 | status-driven (RMI banner) | `continueRMIReview()` `10437` — **no `assertPermission`** | PATCH status `rmi_sent`→… | **P2** — no client check; relies entirely on backend |
| Screening disposition (Clear/Confirm/Escalate/Request Info) | submit `12572` | shown if `canDispositionScreeningDisposition()` `14652` (admin/sco/co/**analyst**) | guard `12580` uses **hardcoded role array, not `hasPermission`** | `/screening/review`; second-review four-eyes SCO/admin | **P2** — hardcoded list bypasses `ROLE_PERMISSIONS` policy (drift risk) |
| Resolve IDV Exception | 9973 | `canResolveIdvException()` `10012` (co/sco/admin) AND `approval_ready!==true` | local role list | IDV resolve endpoint | **P2** — hardcoded list, not policy-driven |
| EDD Save Findings / Advance Stage | 15184 / 15203 | always rendered in EDD modal; gated by `disabled` on missing fields, **not role** | `saveEDDFindings`/`advanceEDDStage` — **no client `assertPermission`** | `EDDDetailHandler`; **closure SCO/admin only** `29816` | **P2** — UI shows advance/signoff controls to co/analyst; backend hard-blocks closure (non-closure stage edits allowed) |
| **Submit to Compliance / Send to Senior Review** | — | **ABSENT** | — | — | **P1-2** — no such control exists (exhaustive search) |

## Critical flags summary
- **(a) UI-shows-backend-rejects (P1):** `co` Approve button + open modal on HIGH/VERY_HIGH. Backend is fail-closed (`server.py:25353`); the issue is misleading UX, not data integrity.
- **(b) Memo-approve permission mismatch (P1):** client checks `approve_low_medium` (incl. `co`); backend requires `["admin","sco"]`. `co` is enabled in UI but denied at backend.
- **(c) Analyst (P2):** sees Approve/Reject/Override (always-rendered standard bar); all click-denied by `assertPermission` and backend. Click-time only.
- **(d) UI-hides-backend-allows:** **none found** — gated-hidden controls (Reassign/Export/Pre-approval) match backend role sets.
- **Latent drift (P2):** screening-disposition + IDV-resolve controls use hardcoded role arrays instead of `ROLE_PERMISSIONS`; if backend policy is edited at runtime, UI gates would diverge.

## Portal wording (arie-portal.html) — neutrality check: **PASS**
- Mapping `getClientPortalStatusLabel(status)` (`11199-11221`): `compliance_review`/`in_review`/`under_review`/`pre_approval_review`/`pricing_review`/`draft`/`pending` → **"Under Review"**; `edd_required` → **"Documents"** (never "EDD"); `approved`→"Approved"; `rejected`/`withdrawn`→"Declined"; `rmi_sent`→"Info Required".
- Compliance-hold view (`3274-3288`): "Application Under Compliance Review" / "No action is required at this time." No SCO/EDD/screening/second-review wording.
- Active scrubber `sanitizeClientPortalCopy` (`11224-11231`) rewrites leaked internal terms ("Pre-Screening"→"Initial Review", "bank officer"→"our team").
- **Verdict:** internal mechanics (submitted_to_compliance, SCO, EDD, screening, second review) are **not** exposed to applicants. Target decision #9 met today; future `submitted_to_compliance` status must map to "Under Review".
