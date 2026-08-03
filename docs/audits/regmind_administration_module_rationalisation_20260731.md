# RegMind — Administration Module Product Rationalisation Audit

**Date:** 2026-07-31
**Author role:** Lead Product Architect / Enterprise UX Auditor
**Scope:** Every page reachable under the back-office **Administration** navigation section (`arie-backoffice.html`), plus their backing APIs in `arie-backend/`.
**Lens:** Commercial launch readiness of an enterprise SaaS admin module. **Not** a code-quality review.
**Verdict up front:** 10 nav items → **4**. One launch blocker. Three pages that actively mislead compliance officers.

---

## 0. Executive summary

The Administration section currently exposes **10 navigation items** (`arie-backoffice.html:1268–1278`). Assessed against what a paying regulated customer would actually use:

| Outcome | Count | Pages |
|---|---|---|
| Keep (rehomed / merged into) | 4 | Users & Access, Compliance Configuration, Audit Trail, Resources |
| Merge away | 3 | Roles & Permissions, Risk Scoring Model, Document Verification Policies |
| Delete | 2 | Agent Health, AI Agents (editor) |
| Move out of Administration | 2 | Audit Trail → Governance, Resources → Workspace-level |
| Feature-flag (Enterprise) | 1 | AI Agent Pipeline (read-only remnant) |

**Three findings block a commercial launch of this module:**

1. **ADM-BLOCK-1 — Admin can create users who can never log in.** `POST /api/users` generates a temporary password server-side (`server.py:15625–15627`) but never returns it, and the frontend never asks for one (`arie-backoffice.html:31835`). There is **no officer password-reset endpoint in production** — `/api/admin/officer-reset-password` hard-fails when `IS_PRODUCTION` (`server.py:4623–4625`) and is referenced **zero times** in the UI. Net effect: User Management is a write-only page that produces dead accounts.

2. **ADM-BLOCK-2 — Document Verification Policies silently discards every edit on deploy.** `sync_ai_checks_from_seed()` runs unconditionally at startup and overwrites `ai_checks` from `verification_matrix.py` (`db.py:12009–12040`). Its own docstring states: *"Back-office manual edits to individual checks are intentionally overwritten here."* The page shows "✅ AI verification checks saved" and the change is real until the next container restart. In a regulated product this is a false control claim.

3. **ADM-BLOCK-3 — Resources publishes factually wrong risk thresholds.** The "Risk Classification Thresholds" quick-reference card hardcodes Low 0–29.9 / Medium 30–49.9 / High 50–69.9 (`arie-backoffice.html:3253–3258`). The canonical runtime bands are Low 0–39.9 / Medium 40–54.9 / High 55–69.9 (`rule_engine.py:1383–1387`). An officer using this reference card will misclassify every case scoring 30–54.

Two further "saves" are theatre: **Ongoing Review Schedule** persists nothing at all, and **System Settings** persists five fields that no engine ever reads.

---

## 1. Complete page inventory

Navigation source: `arie-backoffice.html:1268–1278`. View markup: `arie-backoffice.html:2776–3279`. Routing: `showView()` at `arie-backoffice.html:7050`.

### 1.1 User Management — `view-users`

| | |
|---|---|
| **Purpose** | Create, edit, activate/deactivate compliance team accounts. |
| **Classification** | **Essential** |
| **UI reachable** | Yes — `role-admin-only` |
| **Backend** | `GET/POST /api/users`, `PUT /api/users/:id` (`server.py:15588`, `15667`) — real, audited, role-validated, self-modification-guarded |
| **Performs useful action** | **Partially — see ADM-BLOCK-1** |
| **Referenced elsewhere** | Yes — `USERS` hydrates assignment pickers and audit filters |
| **Placeholder content** | No |
| **Dead code** | No |
| **Duplicates** | No |
| **Maintenance cost** | **Low** (~76 JS lines + 12 markup lines + 1 modal) |
| **Customer value** | **High** |

**Gaps against enterprise baseline:** no password reset, no invite email, no MFA/SSO management, no session revocation from UI, no last-login column, no bulk actions. For a bank buyer, "user management" without a reset path is not a shippable control.

### 1.2 Roles & Permissions — `view-roles`

| | |
|---|---|
| **Purpose** | Read-only display of the backend RBAC matrix. |
| **Classification** | **Useful — but not a page** |
| **UI reachable** | Yes — `role-admin-only` |
| **Backend** | `GET /api/config/roles-permissions` (`server.py:17489`) — returns `ROLE_PERMISSION_MATRIX` verbatim, `"source": "backend_policy"` |
| **Performs useful action** | **No** — zero write path. Pure transparency surface. |
| **Referenced elsewhere** | `ROLE_PERMISSIONS` used only by `renderRolesPermissions()` |
| **Placeholder content** | No — but the page ships its own disclaimer that it is not authoritative |
| **Dead code** | No |
| **Duplicates** | Conceptually overlaps User Management (the role dropdown is where roles are *applied*) |
| **Maintenance cost** | **Low** (~47 JS lines) |
| **Customer value** | **Low–Medium** — consulted once during procurement/audit, never operationally |

A whole top-level nav item for a static 4-column table that cannot be changed is pure cognitive load.

### 1.3 Risk Scoring Model — `view-risk-model`

| | |
|---|---|
| **Purpose** | Read-only view of the active runtime risk model: thresholds, dimensions, scoring catalogue, floors/EDD/approval rules, Lane B pending calibration. |
| **Classification** | **Essential (content) / Misplaced (location)** |
| **UI reachable** | Yes — `role-admin-only` |
| **Backend** | `GET /api/config/risk-model`, `GET /api/config/country-risk` (`server.py:44789`, `44788`) — real, fail-closed (`test_risk_config_fail_closed.py`) |
| **Performs useful action** | Read-only. Page states editing "will be introduced in a future governed release" (`arie-backoffice.html:2822`). |
| **Referenced elsewhere** | Yes — `RUNTIME_RISK_MODEL` drives risk display across the app |
| **Placeholder content** | No — this page is honest and well built |
| **Dead code** | No |
| **Duplicates** | **Yes** — thresholds also hardcoded (wrongly) in Resources |
| **Maintenance cost** | **Medium** (~349 JS lines across 6 sub-renderers + country-risk governance) |
| **Customer value** | **High** — this is the model transparency a regulator asks for |

The best page in the module. It is also `role-admin-only`, which is wrong: the *compliance officers* who need it are SCO/CO, and the page's own subtitle says "Read-only runtime model reference for compliance officers."

### 1.4 Document Verification Policies — `view-ai-checks`

| | |
|---|---|
| **Purpose** | Edit the per-document-type verification checks Agent 1 runs (Entity / Person / Enhanced Evidence tabs). |
| **Classification** | **Placeholder masquerading as a control — see ADM-BLOCK-2** |
| **UI reachable** | Yes — `role-admin-only` |
| **Backend** | `GET/PUT /api/config/verification-checks` (`server.py:17694`) — persists to `ai_checks`, audits before/after |
| **Performs useful action** | **Writes are real but non-durable.** Overwritten every startup by `sync_ai_checks_from_seed()` (`db.py:12009`). |
| **Referenced elsewhere** | Yes — `document_verification.py:1596` consumes checks; `PUT` also rewrites Agent 1's label list (`server.py:17767`) |
| **Placeholder content** | ~272 lines of hardcoded `ENTITY_DOC_CHECKS` / `PERSON_DOC_CHECKS` / `EDD_DOC_CHECKS` (`arie-backoffice.html:32642–32914`) duplicating `verification_matrix.py`, used as a fallback and immediately replaced by the API payload (`arie-backoffice.html:5595–5605`) |
| **Dead code** | `addDocType()` uses a raw `prompt()` and creates a client-only doc type with no canonical `doc_type` mapping — unsaveable in any meaningful way |
| **Duplicates** | **Yes, threefold** — the same check catalogue lives in `verification_matrix.py`, in the `ai_checks` table, and hardcoded in the HTML |
| **Maintenance cost** | **High** (~234 JS lines + 272 data lines + a triple-source-of-truth sync problem) |
| **Customer value** | **Low as an editor / High as a read-only policy statement** |

The `/api/config/document-policies` endpoint (`server.py:17794`) has **zero UI callers** — orphaned; its payload is already folded into the verification-checks response.

### 1.5 AI Agents — `view-ai-agents`

| | |
|---|---|
| **Purpose** | "Configure the 10 AI agents" — rename them, change icon/stage/description, edit free-text "checks", enable/disable, add/delete agents. |
| **Classification** | **Developer-only / partially cosmetic** |
| **UI reachable** | Yes — `role-admin-only` |
| **Backend** | `GET/POST /api/config/ai-agents`, `PUT/DELETE /api/config/ai-agents/:id` (`server.py:17511`, `17575`) — real persistence |
| **Performs useful action** | **Only `enabled` matters, and only for 3 of 10 agents.** Runtime reads `ai_agents.enabled` for agent 1 (`server.py:13078`), agent 3 (`server.py:26739`, `28505`), agent 5 (`server.py:32326`). `name`, `icon`, `stage`, `description`, `checks` are **display metadata with no runtime effect**; agent 1's `checks` are machine-regenerated from `ai_checks` (`server.py:17767`, `db.py:12048`) so the free-text boxes are write-then-discard. |
| **Referenced elsewhere** | `AI_AGENTS` also seeds the fake Agent Health telemetry |
| **Placeholder content** | `+ Add Agent` creates a client-side "New Agent" object with an arbitrary next id that no executor exists for — a customer can create an agent that does nothing, forever |
| **Dead code** | Agents 8 and 10 are hard-flagged "Enterprise roadmap / not active in pilot" and all controls are disabled; agent 9 has generic non-classified checks |
| **Duplicates** | **Yes** — the Entity/Person/EDD check editor in Document Verification Policies is the *same data* surfaced as agent-1 free text |
| **Maintenance cost** | **High** (~210 JS lines + a 40-line hardcoded agent catalogue + `ai_agent_catalog.py` + `test_ai_agent_catalog.py`, `test_agent_config_integrity.py`, `test_agent_standardisation.py`) |
| **Customer value** | **Low as an editor / Medium as a read-only "what runs when" explainer** |

**No production customer will ever rename "Corporate Structure & UBO Mapping Agent" or add an 11th agent.** Giving a bank a button that lets them break the pipeline for zero benefit is a liability, not a feature.

#### 1.5.1 Workflow-connection verification (traced 2026-07-31)

**The supervisor pipeline never reads the `ai_agents` table.** Dispatch is a hardcoded `EXECUTOR_MAP` keyed on the `AgentType` enum (`supervisor/agent_executors.py:4606–4616`), looked up at `supervisor/supervisor.py:444`. There is no `enabled` check anywhere in `supervisor/supervisor.py` and no DB read of `ai_agents` in the entire `supervisor/` package.

The table is read at runtime in exactly **four places, all in `server.py`, all selecting only `enabled`**:

| Agent | Read site | Gated workflow |
|---|---|---|
| 1 | `server.py:13078` | `POST /api/documents/:id/verify` — document verification skipped, outcome persisted so reliance gates fail closed |
| 3 | `server.py:28505` | `POST /api/screening/run` — returns `{"status":"skipped"}` |
| 3 | `server.py:26738` (`_agent3_enabled`), called at `server.py:28346` | Agent 3 screening interpretation |
| 5 | `server.py:32326` | `POST /api/applications/:id/memo` — memo generation skipped |

Everything else that touches `ai_agents` is schema/seed (`db.py`) or table-name lists in `gdpr_erasure.py:193` / `regulated_deletion.py:33`.

**Field-by-field verdict:**

| Field | Connected to a workflow? |
|---|---|
| `enabled` (agents 1, 3, 5) | **Yes** — a live kill-switch on document verification, screening, and memo generation |
| `enabled` (agents 2, 4, 6, 7) | **No** — the toggle is enabled in the UI, persists to the DB, and is read by nothing |
| `enabled` (agents 8, 9, 10) | N/A — force-`False` by policy (`ENTERPRISE_ROADMAP_AGENT_IDS`, `server.py:4336`, `15477`, `17528`) |
| `name`, `icon`, `stage`, `description` | **No** — display metadata; zero runtime reads |
| `checks` | **No** — agent 1's list is machine-regenerated from `ai_checks` on every save (`server.py:17767`) and every startup (`db.py:12048`); agents 2–10's lists are never read |
| `supervisor_agent_type`, `risk_dimensions` (columns exist, `db.py:10999`) | **No** — never read by any module |

**`+ Add Agent`** creates `agent_number = 11`, for which no `AgentType` enum member and no executor exist. The agent can never run. **`Delete Agent`** is a soft-disable (`UPDATE ai_agents SET enabled=false`, `server.py:17669`) — which, for agents 2/4/6/7, changes nothing.

**Conclusion: the AI Agents page is not connected to any workflow except three kill-switches.** That is worse than being fully disconnected — of ~30 editable controls on the page, 27 are inert and 3 silently disable core compliance processing with no confirmation beyond a toast.

### 1.6 Agent Health — `view-agent-health`

| | |
|---|---|
| **Purpose** | "Real-time quality control monitoring for all AI agents — accuracy, confidence, overrides, drift." |
| **Classification** | **No longer used / Fabricated demo content** |
| **UI reachable** | **No.** Triple-hidden: `data-pilot-hidden="agent-health"` (CSS kill at `arie-backoffice.html:1117`) **plus** inline `style="display:none;"` (`:1274`) |
| **Backend** | **None.** Zero API calls. |
| **Performs useful action** | **No.** All metrics come from `generateAgentHealthData()` — `Math.sin()`-seeded pseudo-random numbers (`arie-backoffice.html:33277–33320`). `AGENT_HEALTH_ACTIVE = false` (`:33275`), and the render early-returns unless `APP_ENV === 'demo'` (`:33355`). |
| **Referenced elsewhere** | `showView('agent-health')` only from the hidden nav item |
| **Placeholder content** | **Entirely.** Accuracy %, drift %, golden-dataset regression score, false positives/negatives, latency, sparklines — all synthetic. |
| **Dead code** | The whole view: 32 markup lines + ~288 JS lines + `.ah-*` CSS + `exportAgentHealthReport()` which exports fabricated CSV |
| **Duplicates** | Overlaps AI Agents (agent roster) and Reports (analytics) |
| **Maintenance cost** | **Medium** — dead weight, but it is a live reputational hazard: any environment where `APP_ENV === 'demo'` renders invented "Golden Dataset 96.4%" numbers as if they were telemetry |
| **Customer value** | **Zero today. High if ever built on real `agent_executions` data.** |

### 1.7 Enhanced Requirements — `view-enhanced-requirements`

| | |
|---|---|
| **Purpose** | CRUD for enhanced-review requirement rules: which evidence/disclosure/control obligations a trigger raises, audience, blocking/waivable/mandatory flags, waiver roles, client-safe copy. |
| **Classification** | **Essential** |
| **UI reachable** | Yes — `role-enhanced-settings` (admin, sco, co) |
| **Backend** | `GET/POST/PUT/DELETE /api/settings/enhanced-requirements` + `/diagnostics` + `/:id/(disable\|enable)` (`server.py:44797–44800`) — real, governed, write-gated to admin/sco (`canManageEnhancedRequirements()`) |
| **Performs useful action** | **Yes** — genuinely drives onboarding requirements (`enhanced_requirements.py`, `test_enhanced_requirement_settings.py`, `test_application_enhanced_requirements.py`) |
| **Referenced elsewhere** | Yes — application detail, memo generation (`test_enhanced_requirement_memo.py`), portal |
| **Placeholder content** | No |
| **Dead code** | No |
| **Duplicates** | Overlaps the "Enhanced Evidence Documents" tab of Document Verification Policies — ER defines *which* enhanced document is required; DVP defines *how it is checked*. Related, not identical, but currently split across two unrelated nav items with no cross-link. |
| **Maintenance cost** | **Medium** (~202 JS lines + 70 markup lines + a 20-field form) |
| **Customer value** | **High** — this is the page a compliance officer will actually tune |

The form is raw: `Trigger Key`, `Requirement Key`, `Subject Scope`, `Sort Order`, `Canonical doc_type` are developer vocabulary exposed to an officer with no picker, no validation preview, and no "what will this change" impact statement.

#### 1.7.1 Workflow-connection verification (traced 2026-07-31)

**Fully connected — this is the most load-bearing page in Administration.** The admin CRUD writes `enhanced_requirement_rules`; `_load_active_rules()` reads that exact table with `WHERE active = 1 AND trigger_key IN (...)` (`enhanced_requirements.py:4744–4757`). Full chain:

```
Admin edits rule            → enhanced_requirement_rules
  → _load_active_rules()                                    (enhanced_requirements.py:4744)
  → generate_application_enhanced_requirements()            (enhanced_requirements.py:4759)
      callers: server.py:4056, server.py:9889, server.py:17414, routing_actuator.py:592
  → application_enhanced_requirements  (per-case snapshot)
  → validate_enhanced_requirements_for_approval()           (enhanced_requirements.py:2845)
      callers: security_hardening.py:1389 and :1849  ← THE APPROVAL GATE
               server.py:33091
```

Eleven modules import `enhanced_requirements`: `security_hardening.py` (approval gate), `server.py`, `routing_actuator.py`, `periodic_review_document_requests.py`, `periodic_review_memo.py`, `periodic_review_projection_service.py`, `periodic_review_risk_reassessment.py`, `periodic_review_notifications.py`, `periodic_review_blockers.py`, `monitoring_document_refresh.py`, `db.py`.

**Every editable field has runtime effect:** `active` (rule selection), `blocking_approval` + `mandatory` + `waivable` + `waiver_roles` (approval gate, `_valid_approval_waiver`), `audience` (portal vs back-office exposure via `list_portal_application_enhanced_requirements`), `requirement_type` (`classify_requirement_presentation_type`), `subject_scope` (per-UBO/director/screening-subject fan-out), `applies_when` (`_rule_applicable_to_application`), `client_safe_label`/`client_safe_description` (client-visible copy via `_client_safe_requirement_fields`), `sort_order`, labels/descriptions (memo via `build_enhanced_review_memo_summary`).

**Durability — the opposite of Document Verification Policies.** `seed_default_enhanced_requirement_rules()` is explicitly non-destructive: *"Insert missing default rules without overwriting customized rows"* (`enhanced_requirements.py:1942–1945`); it skips any `(trigger_key, requirement_key)` that already exists.

**One caveat (ADM-MED-9).** `_apply_approved_enhanced_requirement_taxonomy_updates()` (`enhanced_requirements.py:2012`) runs on every startup immediately after seeding and:
- **force-overwrites 9 named rules** — `high_or_very_high_risk/company_bank_reference`, `high_or_very_high_risk/company_sof_evidence`, `pep/pep_declaration_details`, `pep/pep_adverse_media_assessment`, `pep/pep_enhanced_monitoring_flag`, `opaque_ownership/trust_nominee_foundation_documents`, `high_risk_jurisdiction/jurisdiction_sof_evidence`, `high_risk_jurisdiction/jurisdiction_exposure_rationale`, `high_risk_jurisdiction/jurisdiction_risk_assessment` — pinning `active`, `blocking_approval`, `mandatory`, `audience`, `requirement_type`, `subject_scope`, labels, client-safe copy and `applies_when` back to product-approved values;
- **force-deactivates 35 legacy `requirement_key`s** (`REMOVED_ACTIVE_ENHANCED_REQUIREMENT_KEYS`, `enhanced_requirements.py:231`) — so re-enabling any of them via the UI reverts on the next deploy.

Nine of the 14 shipped default rules (`DEFAULT_ENHANCED_REQUIREMENT_RULES`, `enhanced_requirements.py:754–982`) are therefore effectively read-only despite presenting a full edit form. This is the same *class* of defect as ADM-BLOCK-2 but far narrower and intentional (product-approved config correction, audited as `enhanced_requirement_rules.taxonomy_reconciled`) rather than a blanket wipe. **Fix is UX, not architecture: mark pinned rules read-only in the UI with a "governed by product policy" badge, instead of accepting an edit and reverting it silently.**

**Conclusion: Enhanced Requirements is genuinely connected end-to-end — it gates approvals, drives the portal, and feeds the memo. Keep it. It is the one page in Administration that earns its place.**

### 1.8 Resources — `view-resources`

| | |
|---|---|
| **Purpose** | Internal policy/manual library + regulatory links + quick-reference cards + upload. |
| **Classification** | **Useful — but misplaced and partly wrong** |
| **UI reachable** | Yes — **and this is a defect: the nav item carries no role class at all** (`arie-backoffice.html:1276`) while the section header is `role-admin-only` (`:1268`). For an Analyst, every other Administration item disappears and "Resources" is left dangling under the "AI Supervisor" heading. Same for Enhanced Requirements under a CO login. |
| **Backend** | `GET/POST /api/resources`, `GET /api/resources/:id/download` (`server.py:14711`, `44776`) — real upload/download, 25MB cap, rate-limited |
| **Performs useful action** | Yes for the library. |
| **Placeholder content** | **Yes — and it is wrong.** Hardcoded risk thresholds contradict `rule_engine.CANONICAL_THRESHOLDS` (**ADM-BLOCK-3**). Three static external links (FATF, FSC, ComplyAdvantage) are hardcoded marketing-grade content. |
| **Dead code** | No |
| **Duplicates** | **Yes** — "Risk Classification Thresholds" duplicates Risk Scoring Model; "Approval Requirements" duplicates the Case Command Centre approval gates |
| **Maintenance cost** | **Low** (~71 JS lines) — but the hardcoded reference cards are an ongoing correctness liability |
| **Customer value** | **Medium** — every customer wants a policy shelf; nobody wants it under "Administration" |

### 1.9 Settings — `view-settings`

Three unrelated cards under one generic label.

**Card A — Ongoing Review Schedule**

| | |
|---|---|
| **Classification** | **Placeholder** |
| **Performs useful action** | **No.** `saveReviewSchedule()` (`arie-backoffice.html:34380`) writes four integers into an in-memory JS object and toasts *"✅ Review schedule saved — reminders will trigger accordingly."* **There is no network call.** `REVIEW_SCHEDULE` is read by nothing except its own label formatters. |
| **Duplicates** | The real cadence lives in `periodic_review_policy.py` / `periodic_review_engine.py` and is surfaced in Periodic Reviews |
| **Customer value** | **Negative** — it tells a regulated customer a FIAMLA/FIAMLR control was configured when nothing happened |

**Card B — API Integration Status**

| | |
|---|---|
| **Classification** | **Essential (content) / Misplaced (location)** |
| **Backend** | `GET /api/screening/status` (`arie-backoffice.html:36518`) — real provider truth: active AML provider, IDV provider, abstraction flag, simulation/fallback mode |
| **Duplicates** | **Yes** — the same `/api/screening/status` payload already renders in the Screening Queue provider status panel, which `CLAUDE.md` names as *"the operator source of truth."* Two renderers, one truth. |
| **Customer value** | **High** |

**Card C — System Settings**

| | |
|---|---|
| **Classification** | **Placeholder with a real database write** |
| **Backend** | `GET/PUT /api/config/system-settings` (`server.py:16123`) — validated, audited, persisted |
| **Performs useful action** | **No engine reads any of the five fields.** `default_retention_years`, `auto_approve_max_score`, `edd_threshold_score` appear only in their own validator/handler/schema (`server.py:15403–15428`, `16123–16195`, `db.py`); `company_name`/`licence_number` are never consumed for branding or PDF output. The UI already admits it: two labels say *"reference only, not auto-enforced."* |
| **Customer value** | **Low** — a settings form where nothing takes effect |

| | |
|---|---|
| **Maintenance cost (whole page)** | **Medium** (~128 JS lines + 72 markup lines, of which the majority is inert) |

### 1.10 Audit Trail — `view-audit`

| | |
|---|---|
| **Purpose** | Global, filterable, exportable audit log across users/actions/applications/dates. |
| **Classification** | **Essential** |
| **UI reachable** | Yes — `role-sco-only` (admin + sco) |
| **Backend** | `GET /api/audit`, `GET /api/audit/export?format=csv` (`server.py:44840–44842`) — real, append-only, hash-chained (`test_audit_log_chain.py`, `test_p10_7_audit_append_only.py`) |
| **Performs useful action** | Yes |
| **Referenced elsewhere** | Heavily — `refreshAdminAuditEvidence()` is called after nearly every admin write |
| **Placeholder content** | The action filter is a hardcoded `<option>` list (Approve/Reject/Assign/Login/View/Generate Memo/Download Memo PDF/Upload) that does not match the full backend action vocabulary; the user filter ships a single "All Users" option |
| **Dead code** | No |
| **Duplicates** | **Partial** — Application Review has a per-application "📋 Audit Trail" card (`arie-backoffice.html:1856`); this is the cross-entity view. Complementary, not duplicate. |
| **Maintenance cost** | **Low** (~115 JS lines) |
| **Customer value** | **Very High** — this is the page that survives a regulatory inspection |

**It does not belong under Administration.** It is a compliance/governance artefact, not a settings screen, and it is SCO-visible while the Administration header is admin-only — so an SCO sees a headerless orphan item.

---

## 2. Duplicate analysis

| # | Overlap | Reality | Action |
|---|---|---|---|
| D1 | **AI Agents vs Agent Health** | Agent Health is fabricated telemetry about the roster AI Agents lists. No shared backend. | Delete Agent Health; fold a real, backend-sourced status column into the agent roster only once `agent_executions` telemetry is wired. |
| D2 | **Document Verification Policies vs AI Agents** | Agent 1's `checks[]` is machine-regenerated from `ai_checks` (`server.py:17767`). The AI Agents editor lets you type over a field the server will overwrite. | Delete the AI Agents editor. One editor for checks. |
| D3 | **Document Verification Policies vs Enhanced Requirements** | ER decides *which* enhanced evidence is demanded; DVP's third tab decides *how it is verified*. Split across two nav items, no cross-link. | Co-locate under one **Compliance Configuration** page as sibling tabs. |
| D4 | **Verification checks: three sources of truth** | `verification_matrix.py` (seed, wins at startup) → `ai_checks` table (runtime) → 272 hardcoded lines in `arie-backoffice.html:32642+` (fallback). | Delete the HTML copy. Make the seed authoritative and the UI read-only, or make the DB authoritative and drop the unconditional re-sync. Pick one. |
| D5 | **Risk Scoring Model vs Resources quick-reference** | Resources hardcodes thresholds that are **wrong** vs `rule_engine.CANONICAL_THRESHOLDS`. | Delete the Resources card. Link to Risk Scoring Model. |
| D6 | **Settings → API Integration Status vs Screening Queue provider panel** | Same `/api/screening/status` payload, two renderers. `CLAUDE.md` designates the Screening Queue panel as the operator source of truth. | Delete the Settings copy; deep-link to the Screening Queue panel. |
| D7 | **Audit Trail vs Application Review audit card** | Global vs per-case. Genuinely complementary. | Keep both; add a "View full audit trail for this case" deep-link from the case card. |
| D8 | **Roles & Permissions vs User Management** | Roles are *assigned* in User Management and *displayed* in Roles & Permissions. | Merge — role matrix becomes a tab on Users & Access. |
| D9 | **Settings → Review Schedule vs Periodic Reviews** | Settings card is inert; Periodic Reviews holds the real state machine. | Delete the card. |
| D10 | **Resources vs Help** | There is **no Help page** in the back office (grep: zero matches). Resources is the de-facto help surface. | No merge needed — but Resources should be relabelled and rehomed as workspace-level, not Administration. |

---

## 3. Dead / unused / orphaned analysis

| Item | Evidence | Status |
|---|---|---|
| **`view-agent-health`** (32 markup + ~288 JS + `.ah-*` CSS) | `AGENT_HEALTH_ACTIVE = false`; hidden by CSS *and* inline style; zero API calls; `Math.sin()` data generator | **Fully dead — delete** |
| **`generateAgentHealthData()`, `exportAgentHealthReport()`** | Only callers are the dead view | **Dead — delete** |
| **`GET /api/config/document-policies`** (`server.py:17794`) | Zero UI callers; only `test_doc_policy_canonical_registry.py` | **Orphaned endpoint** — payload already inlined into `/config/verification-checks` |
| **`ENTITY_DOC_CHECKS` / `PERSON_DOC_CHECKS` / `EDD_DOC_CHECKS`** (~272 lines, `arie-backoffice.html:32642–32914`) | Overwritten by `applyVerificationChecksPayload()` on every authenticated load | **Dead-on-arrival fallback — delete** |
| **`REVIEW_SCHEDULE` + `saveReviewSchedule()`** | Never transmitted, never read | **Dead — delete with the card** |
| **`system_settings.auto_approve_max_score` / `edd_threshold_score` / `default_retention_years` / `company_name` / `licence_number`** | Stored and validated; read by no engine | **Inert persistence** |
| **`addNewAgent()` / `addDocType()`** | Create client-side entities with no executor / no canonical `doc_type` | **Trap functions — delete** |
| **AI agent fields `name`/`icon`/`stage`/`description`** | Never read at runtime; only `enabled` (agents 1/3/5) has effect | **Cosmetic writes** |
| **Nav role-class gap** | `Resources` has no role class; `Enhanced Requirements` is `role-enhanced-settings` — both survive when the `role-admin-only` section header is hidden | **Live UX defect for CO/Analyst logins** |
| **`/api/admin/officer-reset-password`** | 403s in production; zero UI references | **Unreachable in the only environment that matters** |

---

## 4. Pages no production customer would ever use

1. **Agent Health** — invisible, fabricated, exportable as fake CSV.
2. **AI Agents (as an editor)** — no bank will rename a pipeline agent or add an 11th. The only meaningful control (`enabled`) affects 3 of 10 agents and disabling any of them degrades the compliance pipeline the customer bought.
3. **Settings → System Settings** — five fields, zero runtime effect.
4. **Settings → Ongoing Review Schedule** — a save button that saves nothing.
5. **Roles & Permissions** — read once during procurement, never again.

## 5. Pages only developers would use

1. **Agent Health** — synthetic drift/golden-dataset metrics are an ML-ops debugging concept.
2. **AI Agents** — pipeline internals (stage names, check strings, icons).
3. **Document Verification Policies** — as an *editor*. As a read-only policy statement it has real customer value; as a live editor it exposes `doc_type` keys and check `type` enums, then discards the edits on deploy.
4. **Enhanced Requirements' raw key fields** — `Trigger Key`, `Requirement Key`, `Canonical doc_type`, `Sort Order`. The page stays; these fields need a picker, not a text box.

---

## 6. Per-page recommendation, cost and value

| Page | Maintenance | Customer value | Recommendation |
|---|---|---|---|
| User Management | Low | **High** | **Keep** — rename **Users & Access**; absorb Roles matrix as a tab; **fix ADM-BLOCK-1 before launch** |
| Roles & Permissions | Low | Low–Med | **Merge** → Users & Access ▸ *Roles* tab |
| Risk Scoring Model | Medium | **High** | **Merge** → Compliance Configuration ▸ *Risk Model* tab; widen visibility to SCO/CO (it is a read-only reference) |
| Document Verification Policies | **High** | Low (editor) / High (reference) | **Merge** → Compliance Configuration ▸ *Document Checks* tab, **read-only**, until the seed-overwrite conflict is resolved |
| AI Agents | **High** | Low | **Delete the editor.** Keep a read-only "AI Pipeline" panel inside Compliance Configuration behind an Enterprise flag. Retain `enabled` as an ops-only DB/API control, not a UI button. |
| Agent Health | Medium | **Zero** | **Delete completely** (view, JS, CSS, export). Re-introduce only when backed by `agent_executions`. |
| Enhanced Requirements | Medium | **High** | **Merge** → Compliance Configuration ▸ *Enhanced Requirements* tab (primary tab); keep full CRUD |
| Resources | Low | Medium | **Move out of Administration** to a workspace-level entry. **Delete the two hardcoded quick-reference cards (ADM-BLOCK-3).** Add the missing role class. |
| Settings | Medium | Low | **Dissolve.** Delete Review Schedule + System Settings. Move API Integration Status → Screening Queue provider panel (already the source of truth). |
| Audit Trail | Low | **Very High** | **Move** to a top-level **Governance** group (or under Compliance). Keep unchanged functionally; replace the hardcoded action filter with a backend-sourced vocabulary. |

---

## 7. Final proposed Administration menu

```
Administration            (role: admin)
├── Users & Access                     ← User Management + Roles & Permissions
│     ├─ Members         (CRUD, invite, reset password, deactivate, last login)
│     └─ Roles           (read-only backend RBAC matrix)
│
└── Compliance Configuration           ← Enhanced Requirements + Risk Model
      ├─ Enhanced Requirements   (full CRUD — the only editable tab)          ← primary
      ├─ Risk Model              (read-only runtime model + country risk)
      ├─ Document Checks         (read-only Entity / Person / Enhanced Evidence)
      └─ AI Pipeline             (read-only, Enterprise flag)
```

**Moved out of Administration:**

```
Governance                (role: admin + sco)
└── Audit Trail                        ← was Administration ▸ Audit Trail

Workspace  (sidebar footer, all roles)
└── Resources                          ← was Administration ▸ Resources
```

**Deleted outright:** Agent Health · AI Agents editor · Settings (all three cards dissolved or relocated).

Rationale, in one line each:
- **Two admin pages, not ten.** Everything an administrator does is either *"who can use this"* or *"how the compliance engine is configured."*
- **Audit Trail is evidence, not configuration** — and its SCO audience never sees the admin-only header today.
- **Resources is a shelf, not an admin control** — and Analysts already see it dangling under the wrong heading.
- **Read-only by default.** Every editor that cannot durably change runtime behaviour becomes a transparency panel. That is a *stronger* enterprise story: "here is exactly what the engine does," not "here is a knob that quietly resets on deploy."

---

## 8. Estimated reduction

### Navigation

| Metric | Before | After | Δ |
|---|---|---|---|
| Administration nav items | 10 | **2** | **−80%** |
| Total top-level admin destinations (incl. relocated) | 10 | 4 | −60% |
| Distinct pages an admin must learn | 10 | 2 (+6 tabs in context) | −80% first-run cognitive load |
| Editable surfaces | 6 | **2** (Users, Enhanced Requirements) | −67% |
| Pages presenting fabricated or non-persisting data | 3 | **0** | −100% |

### Code

| Component | Lines removed (approx.) |
|---|---|
| Agent Health view + JS + CSS + export | ~330 |
| AI Agents editor JS + hardcoded catalogue | ~250 |
| Hardcoded `*_DOC_CHECKS` arrays | ~272 |
| Settings: Review Schedule + System Settings cards + JS | ~120 |
| Resources hardcoded quick-reference cards | ~30 |
| Roles standalone view scaffolding | ~30 |
| **Total front-end removal** | **≈ 1,030 lines** |
| Backend: `/api/config/document-policies` (orphan), `/config/system-settings` write path (inert) | ~120 |
| **Total** | **≈ 1,150 lines** |

Against the ~1,860 lines that currently implement the Administration module (markup + JS + data blobs), that is a **~60% code reduction** with **zero loss of any control that affects runtime behaviour** — because every deleted editor either wrote cosmetic metadata, wrote nothing, or wrote data that the next deploy overwrote.

### Maintenance effort

| Bucket | Before | After |
|---|---|---|
| High-maintenance pages | 2 (DVP, AI Agents) | **0** |
| Medium | 4 (Risk Model, Agent Health, Enhanced Req, Settings) | 2 (Risk Model, Enhanced Req) |
| Low | 4 | 2 |
| Sources of truth for verification checks | **3** | **1** |
| Sources of truth for provider status | 2 | **1** |
| Sources of truth for risk thresholds | 2 (one wrong) | **1** |

Estimated ongoing maintenance reduction: **~55–60%**, concentrated in the elimination of the triple-source verification-check sync and the fabricated telemetry surface.

---

## 9. Launch gate — ordered

| ID | Item | Severity | Why it blocks |
|---|---|---|---|
| **ADM-BLOCK-1** | No officer password reset; `POST /api/users` never surfaces the generated temp password | **Blocker** | Admin creates permanently unusable accounts. Enterprise buyers test this in week one. |
| **ADM-BLOCK-2** | Verification-check edits wiped by `sync_ai_checks_from_seed()` on every startup | **Blocker** | False control claim in a regulated product. Make the page read-only *or* make the DB authoritative — do not ship both. |
| **ADM-BLOCK-3** | Resources publishes risk thresholds contradicting `rule_engine.CANONICAL_THRESHOLDS` | **Blocker** | Officers will misclassify every case scoring 30–54. |
| ADM-HIGH-4 | `saveReviewSchedule()` claims a FIAMLA/FIAMLR control was saved; nothing persists | High | Same class of defect as ADM-BLOCK-2, smaller blast radius. |
| ADM-HIGH-5 | Agent Health renders fabricated accuracy/drift/golden-dataset metrics whenever `APP_ENV === 'demo'` | High | Invented assurance metrics shown to prospects. |
| ADM-MED-6 | `Resources` / `Enhanced Requirements` nav items outlive the `role-admin-only` section header for CO/Analyst | Medium | Orphaned items under the wrong heading. |
| ADM-MED-7 | System Settings persists five fields no engine reads | Medium | Settings theatre. |
| ADM-MED-9 | AI Agents exposes live `enabled` toggles for agents 2/4/6/7 that are read by nothing, and 3 toggles (agents 1/3/5) that silently disable document verification, screening and memo generation behind a toast | Medium | 27 of ~30 controls inert; the 3 live ones are unguarded kill-switches on core compliance processing. |
| ADM-MED-10 | Enhanced Requirements accepts edits to 9 pinned rules and 35 removed keys, then reverts them on next startup via `_apply_approved_enhanced_requirement_taxonomy_updates()` | Medium | Silent revert on 9 of 14 default rules. UX fix: badge them read-only. |
| ADM-LOW-8 | `/api/config/document-policies` orphaned; audit action filter vocabulary hardcoded and incomplete | Low | Cleanup. |

---

## 10. Sequencing

**Phase 1 — Truth (pre-launch, blocking).** Fix ADM-BLOCK-1/2/3. Delete Review Schedule and Agent Health. No navigation changes: this phase only stops the module from making false statements.

**Phase 2 — Consolidate.** Build **Users & Access** (Members + Roles tabs) and **Compliance Configuration** (Enhanced Requirements + Risk Model + Document Checks read-only + AI Pipeline read-only). Relocate Audit Trail → Governance and Resources → Workspace. Delete Settings.

**Phase 3 — Earn back.** Real Agent Health from `agent_executions`; a governed edit path for the risk model and verification checks with maker-checker approval and versioning — which is the correct enterprise answer to "can we tune the model," and the one worth charging for.

---

### Change-control note

The Administration module is **not** in the frozen list in `CLAUDE.md` (only Application Review and Screening Queue are). Phase 1 and 2 touch shared code (`server.py`, `arie-backoffice.html`); the frozen-module guard tests must stay green and the Application Review / Screening Queue workflow output must be byte-identical. Enhanced Requirements changes must keep `test_enhanced_requirement_settings.py`, `test_application_enhanced_requirements.py` and `test_enhanced_requirement_memo*.py` green, since ER feeds the frozen Application Review approval gates.
