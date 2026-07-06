# RegMind — Investor-Grade Product Audit Report

**Classification:** CONFIDENTIAL — Due Diligence Material  
**Report Date:** July 2026 (Rev. — refreshed against current `main`)  
**Methodology:** Main-branch code audit of `onboarda1234/onboarda`, measured against `main` at 2026-07-06, plus GitHub Actions CI evidence and AWS staging deployment evidence where available  
**Auditor Role:** Senior Product Auditor / Enterprise SaaS Analyst / Technical Due Diligence Expert

---

## 0. Change Log — What Changed Since the Prior Audit

This revision refreshes the report against current `main`. The codebase has grown substantially and several remediation/hardening tracks have closed since the previous (May 2026) audit.

**Metrics refreshed (measured, not asserted):**

| Metric | Prior audit | Current `main` (2026-07-06) |
|---|---|---|
| Total Python | 142,441 lines / 303 files | **≈294,408 lines / 531 files** |
| Main service `server.py` | 13,765 lines | **≈39,964 lines** |
| Back-office HTML | 829 KB / 13,450 lines | **≈2.0 MB / 34,596 lines** |
| Client portal HTML | 547 KB / 9,953 lines | **≈745 KB / 14,608 lines** |
| Automated tests | 4,087 | **≈6,000 test functions** (CI-enforced) |
| `memo_handler.py` | 778 lines | **3,467 lines** |
| `rule_engine.py` | 1,221 lines | **1,911 lines** |
| `change_management.py` | 1,802 lines | **4,025 lines** |
| `security_hardening.py` | 1,814 lines | **3,949 lines** |

Approximately **326 commits merged to `main`** between June and this report date.

**Tracks closed / hardening landed since the prior audit:**
- Authority governance track (role-authority model, approval-authority matrix, Submit-to-Compliance workflow, override/denial audit) — closed.
- Periodic Review remediation A→F — closed; **the automatic monitoring scheduler is now active** in staging/production (`monitoring_automation.py` via Tornado `PeriodicCallback`), superseding the prior "no scheduler" finding.
- **Persisted memo hard-block** — the memo hard-block verdict is now persisted so the approval gate cannot be bypassed.
- **AML screening readiness truthfulness / provider-provenance honesty** — the system now reports screening-provider readiness and provenance accurately rather than defaulting.
- **GDPR subject-erasure engine** (`gdpr_erasure.py`) — wired, category-keyed, fail-closed, default-OFF, with a complete erasure ledger.
- **Multi-task safety** — boot-phase advisory lock (`boot_lock.py`) and a cross-task scheduler singleton guard; config canonicalization (DATABASE_URL required in staging/prod); health-check probes liveness; rollback runbook added.

**Net effect:** genuine production-posture hardening (governance, screening honesty, GDPR, multi-task correctness) landed, while the monolith and front-end single-file bloat grew — enlarging the refactoring liability. The overall classification is unchanged: **production-pilot-ready for controlled deployment, not yet enterprise-grade.**

---

## 1. Executive Summary

### What RegMind Is

RegMind is a compliance operating system for regulated financial institutions — specifically banks, Electronic Money Institutions (EMIs), and payment service providers operating in or targeting the Mauritius regulatory jurisdiction. It automates KYC/AML due diligence through a deterministic 4-layer AI pipeline, replacing manual compliance workflows with a structured, auditable, machine-enforced decision framework.

The platform operates as two branded surfaces:
- **Onboarda** — Client-facing portal for applicant onboarding (document submission, prescreening, risk scoring, KYC)
- **RegMind** — Internal compliance back-office for officers (case management, screening review, memo generation, decision-making, ongoing monitoring)

### Classification

**Production-pilot-ready for controlled deployment, but not yet enterprise-grade.**

Several core subsystems — risk scoring, memo generation, validation engine, supervisor pipeline, change management, and audit trail — remain production-grade in implementation depth. The platform is best classified as **production-pilot-ready for controlled deployment**: prior remediation sprints are closed, `main` CI is green as of 2026-07-06, and AWS staging on ECS Fargate is the authoritative near-production runtime surface. The platform is still not enterprise-grade because staging remains single-task / non-autoscaled, `server.py` has grown to a large monolith (≈39,964 lines), and the frontends remain large single-file HTML applications (≈2.0 MB back-office, ≈745 KB portal).

### Overall Assessment

RegMind is a genuine compliance operating system, not a collection of tools. It implements end-to-end workflows from client intake through ongoing monitoring, with deterministic rule enforcement, AI-assisted analysis, multi-layer validation, and immutable audit trails. The depth of the compliance logic — 10 AI agents, multi-point memo validation, supervisor contradiction detection, materiality-tiered change management — remains stronger than what is typically found in early-stage compliance platforms. Recent evidence continues to improve the posture: the authority-governance and periodic-review tracks are closed, the monitoring scheduler is now active, and screening/GDPR/approval-gate governance has been hardened. That said, the current infrastructure and enterprise controls still support **controlled pilot deployment**, not broad enterprise production rollout.

### Evidence Grading Used in This Report

| Label | Meaning |
|---|---|
| **Code-confirmed** | Directly evidenced in `main` branch source, tests, or CI configuration |
| **Runtime-confirmed** | Evidenced by validated AWS staging deployment/workflow/runbook or successful staging deployment on `main` |
| **Partially implemented** | Present in code but limited by feature flags, missing wiring, or incomplete runtime proof |
| **Demo-ready** | Suitable for demonstration or internal review, but not enough for controlled regulated deployment by itself |
| **Production-ready** | Control or subsystem is technically mature and operationally credible within the current pilot posture |
| **Blocker** | Material gap that prevents full production / enterprise-readiness classification |

### Key Strengths
1. **Deterministic AI pipeline** — 4-layer architecture (rules → memo → validation → supervisor) prevents AI hallucination from reaching compliance decisions
2. **35+ core database tables** with comprehensive relational integrity — not a thin prototype
3. **10 specialized AI agents** with defined authority levels (authoritative vs decision_support)
4. **~6,000 automated test functions** on `main`, with CI enforcing a minimum collected-test threshold
5. **Closed remediation & governance tracks** — EX-01→EX-13, authority governance, and periodic-review A→F are closed; approval, audit, screening, GDPR, and multi-task controls are materially stronger
6. **Validated AWS staging path** — ECS Fargate staging deploys from `main`, with SHA-tagged images, health checks, and post-deploy verification; `main` CI green as of 2026-07-06

### Key Weaknesses
1. **Monolithic server.py** (≈39,964 lines — nearly 3× the prior audit) — technical debt that will constrain team scaling and raises deployment blast radius
2. **Single-file HTML frontends** (≈2.0 MB / 34,596-line back-office) — no component framework, no build pipeline, limits frontend iteration velocity
3. **SQLite/PostgreSQL dual support** — acceptable for now but will need migration tooling hardening
4. **External provider dependency** — screening relies on Sumsub (KYC/IDV) and ComplyAdvantage (AML, when configured); the provider-abstraction layer exists but is not yet the sole live path, and ComplyAdvantage production-workspace validation is in progress
5. **Infrastructure is still single-instance pilot posture** — single ECS desired task, no autoscaling, no confirmed HA/DR, and no enterprise identity/compliance certification layer

---

## 2. System-Level Product Definition

### Classification: Compliance Operating System

RegMind is an **operating system**, not a tool or a platform. The distinction:

| Classification | Definition | RegMind? |
|---|---|---|
| Tool | Single-function utility | No |
| Platform | Multiple tools with shared data | No |
| Operating System | Unified system controlling full workflow lifecycle with enforcement, audit, and governance | **Yes** |

**Evidence:** RegMind controls the complete compliance lifecycle:
- Intake → Risk Scoring → Screening → Document Verification → Memo Generation → Validation → Supervisor Review → Decision → Ongoing Monitoring → Change Management → Periodic Review

Each stage feeds the next with structured data. No stage can be bypassed. The supervisor layer enforces consistency across all prior stages. This is OS-level control, not tool-level functionality.

### Compliance Stack Ownership

RegMind owns **Layer 2 (Operational Compliance)** and **Layer 3 (Decision & Governance)** of the compliance stack:

| Layer | Function | RegMind Coverage |
|---|---|---|
| Layer 1 — Data Ingestion | Client data capture, document upload | ✅ Full (Portal) |
| Layer 2 — Operational Compliance | Screening, risk scoring, verification, case management | ✅ Full |
| Layer 3 — Decision & Governance | Memo generation, supervisor review, audit trail | ✅ Full |
| Layer 4 — Regulatory Reporting | SAR filing, regulatory submissions | ⚠️ Partial (SAR structure exists, no regulatory API integration) |
| Layer 5 — Enterprise Integration | Core banking, CRM, data warehouse | ❌ Not implemented |

### Workflows Replaced or Centralised

1. Manual KYC document review → Automated 5-layer document verification (gate → rule → hybrid → AI → aggregation)
2. Spreadsheet-based risk scoring → Deterministic 5-dimension weighted scoring with floor/elevation rules
3. Word document compliance memos → 11-section structured memo with template-driven generation + validation
4. Email-based screening review → Structured screening queue with disposition tracking
5. Ad-hoc monitoring → Agent-driven periodic reviews with priority scoring and an active automatic scheduler
6. Unversioned client profile changes → Materiality-tiered change management with atomic profile versioning

---

## 3. Full Workflow Architecture

### A. Onboarding → Application → Case Flow

**Step-by-step flow:**

1. **Client Registration** (`POST /api/auth/client/register`) — Email/password with bcrypt hashing, strong password policy (12+ chars, 4 character types)
2. **Company Lookup** (`POST /api/screening/company`) — Company lookup path exists, but external registry verification should currently be treated as **partially implemented / degraded**, not as a fully proven production control
3. **Prescreening Submission** (`POST /api/applications`) — Company details, sector, entity type, ownership structure, expected transaction volumes
4. **Real-time Sanctions Check** (`POST /api/screening/sanctions`) — Country-level sanctioned jurisdiction detection during form completion
5. **Risk Scoring** (rule_engine.py `compute_risk_score()`) — 5-dimension composite score (D1-D5) with floor rules and elevation logic
6. **Pricing Review** (`POST /api/applications/{id}/accept-pricing`) — Status transition: pricing_review → pricing_accepted
7. **Pre-Approval Decision** (`POST /api/applications/{id}/pre-approval-decision`) — For HIGH/VERY_HIGH risk: officer pre-approval gate before KYC investment
8. **KYC Document Upload** (`POST /api/applications/{id}/documents`) — Section A (corporate), Section B (personal), Section C (business), Section D (other)
9. **KYC Submission** (`POST /api/applications/{id}/submit-kyc`) — Triggers document verification pipeline
10. **Compliance Review** — Back-office case management: screening review, memo generation, supervisor validation, decision

**System components:** Portal HTML → server.py handlers → db.py → rule_engine.py → screening.py → sumsub_client.py  
**Data flow:** Client form data → applications table → prescreening_data JSONB → directors/ubos/intermediaries tables → documents table  
**Automation vs human:** Steps 1-6 are fully automated. Steps 7 and 10 require human officer decisions. Step 8 is client-driven.  
**Commercial value:** Eliminates 60-80% of manual intake effort. Prescreening risk scoring prevents wasted KYC costs on clearly high-risk applicants.

### B. Verification & AI Checks

**Step-by-step flow:**

1. **Gate Checks (Layer 0)** — File format validation (MIME + magic bytes), size check (25MB max), duplicate detection (SHA-256 hash)
2. **Rule-Based Checks (Layer 1)** — Deterministic: name matching (threshold 0.90 pass, 0.70 warn), registration number format, date parsing, jurisdiction matching, ownership percentage validation (25% UBO threshold)
3. **Hybrid Checks (Layer 2)** — Rules first; if INCONCLUSIVE, falls back to Claude AI interpretation. Example: certification keyword detection + stamp analysis
4. **AI Checks (Layer 3)** — Claude Vision API for genuine interpretation: business plan assessment (DOC-MA-01), document authenticity signals
5. **Aggregation (Layer 4)** — Weighted check results → per-document verification_status (pending/verified/flagged/failed) → verification_results JSONB

**System components:** document_verification.py (1,660 lines) → verification_matrix.py (1,255 lines) → claude_client.py → agent_executors.py (Agent 1)  
**Data flow:** Uploaded file → gate checks → rule checks → hybrid/AI checks → documents.verification_results JSONB → agent_executions table  
**Automation vs human:** Fully automated; flagged documents surface for human review in back-office  
**Commercial value:** Core IP. The 5-layer verification architecture with explicit check IDs (GATE-01, DOC-05, DOC-06, etc.) is auditor-friendly and regulator-defensible.

### C. Screening & Risk Layer

**Step-by-step flow:**

1. **AML/PEP Screening** — ComplyAdvantage-based sanctions, PEP/RCA, watchlist, adverse-media, customer, company, and ongoing-monitoring screening (active when `SCREENING_PROVIDER=complyadvantage` and the abstraction is enabled); production-workspace validation is in progress
2. **Company Registry Verification** (`lookup_opencorporates()`) — OpenCorporates enrichment path exists, but it is not yet a fully runtime-proven authoritative dependency for controlled deployment
3. **IP Geolocation** (`geolocate_ip()`) — Client IP risk classification
4. **Screening Queue** (`GET /api/screening/queue`) — Officers review hits with false positive analysis (Agent 3: FinCrime Screening Interpretation)
5. **Screening Review** (`POST /api/screening/review`) — Per-subject disposition: cleared / escalated / follow_up_required
6. **Risk Scoring** (`compute_risk_score()`) — 5-dimension composite:
   - D1: Customer/Entity Risk (30%) — entity type, ownership, PEP, adverse media, source of wealth/funds
   - D2: Geographic Risk (25%) — incorporation country, UBO nationalities, intermediary jurisdictions
   - D3: Product/Service Risk (20%) — service type, transaction volume, complexity
   - D4: Industry/Sector Risk (15%) — direct sector lookup against scored dictionary
   - D5: Delivery Channel Risk (10%) — introduction method, customer interaction type
7. **Floor Rules** — Sanctioned/FATF_BLACK country → forced VERY_HIGH (non-overridable)
8. **Elevation Rules** — Contextual escalation (e.g., MEDIUM + FATF grey + high-risk sector + opaque structure → HIGH)
9. **Escalation Rules** — Any sub-factor ≥ 4 OR composite ≥ 85 → requires_compliance_approval

**System components:** screening.py → sumsub_client.py → rule_engine.py → screening_normalizer.py / screening_state.py  
**Data flow:** Person/company data → screening provider API → prescreening_data.screening_report JSONB → screening_reviews table → risk_score + risk_level on applications  
**Automation vs human:** Screening is automated; disposition is human-reviewed; risk scoring is fully automated with floor rules  
**Commercial value:** Risk-based model routing (Sonnet for LOW/MEDIUM, Opus for HIGH/VERY_HIGH) optimizes AI costs. Floor rules provide regulatory defensibility.

### D. Compliance Operations

**Case Management:**
- Applications progress through 17 defined statuses: draft → submitted → prescreening_submitted → pricing_review → pricing_accepted → pre_approval_review → pre_approved → kyc_documents → kyc_submitted → compliance_review → in_review → under_review → edd_required → approved → rejected → rmi_sent → withdrawn
- Status transitions are server-enforced, not client-controlled
- Assigned officer tracking via assigned_to FK

**EDD Pipeline:**
- 6-stage lifecycle: triggered → information_gathering → analysis → pending_senior_review → edd_approved → edd_rejected
- Assigned officer + senior reviewer (dual-control)
- Trigger source tracking (officer_decision)
- EDD notes as JSONB (structured)
- Statistics endpoint for pipeline monitoring

**Ongoing Monitoring:**
- Agent 6 (Periodic Review): document expiry, ownership changes, screening staleness, activity volume
- Agent 7 (Adverse Media & PEP): media signals, PEP changes, sanctions updates
- Agent 8 (Behaviour & Risk Drift): transaction volume, geographic deviation, counterparty concentration, velocity anomalies
- Monitoring alerts with severity, AI recommendation, officer action tracking
- Periodic reviews with risk-level-driven scheduling and priority scoring
- **Automatic scheduler active** (`monitoring_automation.py`, Tornado `PeriodicCallback`) with a cross-task singleton guard; the `/api/monitoring/reviews/schedule` backfill endpoint is API-only (no manual UI button)

**Change Management:**
- Full state machine: 7 alert statuses, 14 request statuses
- Materiality classification: Tier 1 (structural) → Tier 2 (operational) → Tier 3 (administrative)
- Downstream action routing: Tier 1 triggers screening + risk review + memo addendum; Tier 3 triggers nothing
- Profile versioning with before/after snapshots
- Atomic implementation (all-or-nothing with rollback)
- Portal-originated change requests with defence-in-depth ownership validation

### E. Decision & Output Layer

**Step-by-step flow:**

1. **Memo Generation** (`POST /api/applications/{id}/memo`) — build_compliance_memo() produces 11-section structured memo with pre-generation rule enforcement (6 deterministic rules)
2. **Memo Validation** (`POST /api/applications/{id}/memo/validate`) — 15-point quality audit with weighted rules, producing quality score (0-10) and pass/pass_with_fixes/fail verdict
3. **Supervisor Review** (`POST /api/applications/{id}/memo/supervisor/run`) — 11-check contradiction detection with verdict (CONSISTENT/CONSISTENT_WITH_WARNINGS/INCONSISTENT), a `can_approve` boolean, and a **persisted memo hard-block** that the approval gate enforces (bypass closed)
4. **Memo Approval** (`POST /api/applications/{id}/memo/approve`) — Officer approval gate with officer sign-off enforcement
5. **Application Decision** (`POST /api/applications/{id}/decision`) — Approval with 9-point approval gate validation (security_hardening.py ApprovalGateValidator):
   - KYC completion check
   - Screening mode validation (live vs simulated)
   - Memo approval check
   - Document flagging check
   - Screening provider validation
   - AI source tracking
   - Staleness detection
   - Screening freshness (90-day validity)
   - Screening age validation
6. **PDF Generation** (`GET /api/applications/{id}/memo/pdf`) — WeasyPrint-generated A4 PDF with SHA-256 content hash for immutability verification
7. **Decision Record** — Normalized to decision_records table with decision_type, risk_level, confidence_score, actor, key_flags

**System components:** memo_handler.py → validation_engine.py → supervisor_engine.py → security_hardening.py → pdf_generator.py → decision_model.py  
**Automation vs human:** Memo generation and validation are automated. Supervisor check is automated. Approval decision is human with machine-enforced prerequisites.  
**Commercial value:** The approval gate validator is the critical control — it prevents premature approvals with 9 sequential checks, now backed by a persisted supervisor hard-block. This is the compliance control that regulators look for.

### F. Audit & Oversight

**Audit Chain:**
- `supervisor_audit_log` table with cryptographic chaining: each entry contains `previous_hash` and `entry_hash`
- Fields: event_type, severity, pipeline_id, agent_type, actor details, IP address, session ID
- Indexes on timestamp, event_type, application_id for efficient querying
- Supervisor audit export endpoint for regulator submissions

**Audit Trail:**
- `audit_log` table captures all system actions: user_id, action, target, detail, ip_address, timestamp
- Per-application audit log retrieval (`GET /api/applications/{id}/audit-log`)
- Decision records as normalized audit overlay
- GDPR-compliant purge logging (data_purge_log — immutable), plus a wired-but-OFF subject-erasure ledger (`gdpr_erasure.py`)

**Traceability:**
- Every AI agent execution logged to `agent_executions` table with checks_json, flags_json, source, timestamps
- Decision records link actor → application → decision type → risk level → confidence → key flags
- Officer sign-off enforcement with server-side IP and User-Agent capture
- AuthZ denial audit logging (base_handler.py `log_authz_denial()`); wrong-role denials, override and waiver use, and terminal decision attempts are audited

**Commercial value:** The cryptographic audit chain (hash-linked entries) is a differentiator. Most compliance platforms log actions but don't chain them cryptographically. This makes post-facto tampering detectable.

---

## 4. Module-Level Breakdown

### Applications
- **Purpose:** Core entity management — lifecycle from draft to final decision
- **Functionality:** 17-status state machine, CRUD, risk scoring integration, document association, party management
- **Completeness:** ✅ Production-ready (full CRUD, batch-fetch optimization with N+1 elimination, ETag support)
- **Dependencies:** db.py, rule_engine.py, party_utils.py
- **Commercial relevance:** Foundation of the entire system

### Case Management
- **Purpose:** Officer workflow for reviewing applications
- **Functionality:** Assigned officer, decision recording, notes, notification, pre-approval gates
- **Completeness:** ✅ Production-ready
- **Dependencies:** Applications module, decision_model.py, security_hardening.py
- **Commercial relevance:** Core daily workflow for compliance officers

### Screening Queue
- **Purpose:** Centralized review of AML/PEP/sanctions screening results
- **Functionality:** Queue listing, per-subject review, disposition tracking (cleared/escalated/follow_up_required), false positive analysis via Agent 3
- **Completeness:** ⚠️ **Pilot-ready** — screening-provider evidence paths are code-confirmed and staging-aligned, and provider readiness/provenance is now reported honestly; ComplyAdvantage production-workspace validation and external registry enrichment remain in progress
- **Dependencies:** screening.py, sumsub_client.py, screening_normalizer.py, screening_state.py
- **Commercial relevance:** High — eliminates manual screening review spreadsheets

### Ongoing Monitoring
- **Purpose:** Post-onboarding continuous compliance surveillance
- **Functionality:** 3 monitoring agents (6, 7, 8), alert management with severity/disposition, periodic review scheduling with risk-level-driven frequency, agent execution tracking
- **Completeness:** ✅ **Pilot-ready** — agents and review state management are implemented and tested; the **automatic scheduler is now active** in staging/production (`monitoring_automation.py`) with a cross-task singleton guard. The `/api/monitoring/reviews/schedule` backfill endpoint is API-only. AML ongoing-monitoring remains provider-dependent (ComplyAdvantage production validation in progress).
- **Dependencies:** supervisor/ module, agent_executors.py, periodic_review_engine.py, monitoring tables
- **Commercial relevance:** Critical for ongoing regulatory compliance — transforms RegMind from onboarding tool to lifecycle system

### EDD Pipeline
- **Purpose:** Enhanced Due Diligence for high-risk applications
- **Functionality:** 6-stage lifecycle, dual-control (assigned officer + senior reviewer), structured notes, statistics dashboard
- **Completeness:** ✅ Production-ready
- **Dependencies:** Applications, case management
- **Commercial relevance:** Required capability for any regulated institution handling HIGH/VERY_HIGH risk clients

### Change Management
- **Purpose:** Formal lifecycle for client profile changes post-onboarding
- **Functionality:** Alert detection → request creation → materiality classification → approval workflow → atomic implementation with profile versioning
- **Completeness:** ✅ Production-ready (4,025 lines, state machine guards, role-based approval, atomic implementation)
- **Dependencies:** Applications, profile versioning, rule_engine.py (risk recomputation)
- **Commercial relevance:** Enterprise differentiator — most compliance platforms lack formal change management

### Reports
- **Purpose:** Operational and compliance analytics
- **Functionality:** Overview, operations, compliance, and data table views; CSV export; report generation endpoint
- **Completeness:** ⚠️ **Pilot-ready for internal reporting** — report structure and aggregation exist, but customization and enterprise reporting breadth remain limited
- **Dependencies:** Applications, screening, decisions
- **Commercial relevance:** Required for board/management reporting and regulatory submissions

### Regulatory Intelligence
- **Purpose:** Regulatory document management and AI-assisted analysis
- **Functionality:** Document upload, AI analysis (status: uploaded → analysed → review_required), source text management, review workflow
- **Completeness:** ⚠️ **Partially implemented / demo-ready** — structure exists, but AI analysis remains scaffolded rather than production-proven
- **Dependencies:** claude_client.py, document storage
- **Commercial relevance:** Forward-looking differentiator — positions RegMind as proactive compliance rather than reactive

### Compliance Memo Generation
- **Purpose:** Automated generation of regulator-grade compliance memos
- **Functionality:** 11-section structured memo, pre-generation rule enforcement (6 rules), metadata aggregation, PDF generation with SHA-256 immutability hash, persisted hard-block on the approval gate
- **Completeness:** ✅ Production-ready (3,467 lines memo_handler.py, deterministic generation, fully tested)
- **Dependencies:** rule_engine.py, validation_engine.py, supervisor_engine.py, pdf_generator.py
- **Commercial relevance:** Core IP — highest commercial value module. See Section 8 for detailed analysis.

### Risk Scoring Model
- **Purpose:** Configurable multi-dimensional risk assessment
- **Functionality:** 5-dimension scoring (D1-D5) with configurable weights, sub-factor scoring, country/sector risk maps, floor rules, elevation rules, escalation checks, DB-backed configuration with live reload
- **Completeness:** ✅ Production-ready (1,911 lines, comprehensive country/sector mappings, FATF alignment)
- **Dependencies:** config from risk_config table, applications data
- **Commercial relevance:** Differentiator — configurable risk model that institutions can adapt to their risk appetite
- **Watch item:** A canonical regulated-financial-activity sector taxonomy is not yet formalised across intake, scoring, EDD, memo, and display; a sector synonym false-negative risk is identified and paused pending compliance policy (see Section 12)

### AI Verification Checks
- **Purpose:** Configurable document verification check matrix
- **Functionality:** Per-document-type check definitions, 5-layer verification (gate → rule → hybrid → AI → aggregation), check status enum (PASS/WARN/FAIL/SKIP/INCONCLUSIVE), configurable via back-office UI
- **Completeness:** ✅ Production-ready (verification_matrix.py: 1,255 lines, document_verification.py: 1,660 lines)
- **Dependencies:** claude_client.py, verification_matrix.py
- **Commercial relevance:** Core IP — the check matrix is the encoding of compliance expertise into software

### AI Agents
- **Purpose:** 10-agent compliance automation pipeline
- **Functionality:** Agent 1 (Identity/Document), Agent 2 (External Database), Agent 3 (FinCrime Screening), Agent 4 (Corporate Structure), Agent 5 (Memo/Risk), Agent 6 (Periodic Review), Agent 7 (Adverse Media/PEP), Agent 8 (Behaviour/Risk Drift), Agent 9 (Regulatory Impact — future), Agent 10 (Ongoing Compliance Review)
- **Completeness:** ⚠️ Agents 1-8, 10 implemented; Agent 9 future phase. 4,000+ lines in agent_executors.py
- **Dependencies:** claude_client.py, rule_engine.py, screening.py, supervisor/
- **Commercial relevance:** The agent architecture is the technical moat — 10 specialized agents with defined authority levels is hard to replicate

### Agent Health
- **Purpose:** Monitoring AI agent execution quality and reliability
- **Functionality:** Agent execution tracking (agent_executions table), golden test capability, health data generation, export
- **Completeness:** ⚠️ **Demo-ready / internal-governance-ready** — useful for internal oversight, but not yet an enterprise operations layer
- **Dependencies:** AI agents, supervisor/
- **Commercial relevance:** Enterprise requirement — AI governance demands operational monitoring

### Audit Chain
- **Purpose:** Cryptographically linked audit trail for supervisor actions
- **Functionality:** Hash-chained entries (previous_hash → entry_hash), event classification, severity levels, full actor attribution
- **Completeness:** ✅ Production-ready (implemented in supervisor_audit_log table with hash chaining)
- **Dependencies:** Supervisor pipeline
- **Commercial relevance:** Regulatory differentiator — provable tamper detection

### Audit Trail
- **Purpose:** Comprehensive system action logging
- **Functionality:** All actions logged with user, target, detail, IP, timestamp; per-application audit log; decision records; GDPR purge audit and subject-erasure ledger
- **Completeness:** ✅ Production-ready
- **Dependencies:** All modules log to audit_log
- **Commercial relevance:** Regulatory baseline requirement — essential for examination readiness

### Roles & Permissions
- **Purpose:** Role-based access control and authority governance
- **Functionality:** 4 roles (admin, sco, co, analyst) with permission matrix; approval-authority matrix and `can_decide` gate; Submit-to-Compliance workflow; client-side hasPermission()/assertPermission() helpers; server-side enforcement via BaseHandler; override/denial/waiver audit
- **Completeness:** ✅ Production-ready (ROLE_PERMISSION_MATRIX defined in server.py, authority governance track closed)
- **Dependencies:** auth.py, base_handler.py
- **Commercial relevance:** Enterprise requirement — segregation of duties

### User Management
- **Purpose:** Officer/admin lifecycle management
- **Functionality:** CRUD, role assignment, password management, admin password reset
- **Completeness:** ✅ Production-ready
- **Dependencies:** auth.py, security_hardening.py (PasswordPolicy)
- **Commercial relevance:** Operational necessity

### Supervisor Dashboard
- **Purpose:** Executive oversight of AI pipeline quality
- **Functionality:** Pipeline execution monitoring, contradiction visualization, audit chain verification, re-screening capability
- **Completeness:** ⚠️ **Pilot-ready for controlled use** — UI and data exist, but broader production hardening is still required
- **Dependencies:** supervisor/, agent_executions, supervisor_pipeline_results
- **Commercial relevance:** Governance requirement — provides compliance leadership with pipeline visibility

---

## 5. Architecture Assessment

### Backend Structure

| Dimension | Assessment |
|---|---|
| **Language** | Python 3.11 |
| **Framework** | Tornado 6.5 (async web framework) |
| **Total Python files** | 531 |
| **Total lines of code** | ≈294,408 |
| **Main server** | server.py — ≈39,964 lines |
| **Database** | PostgreSQL (production) / SQLite (development) |
| **AI Integration** | Anthropic Claude API (Sonnet + Opus) |
| **KYC / IDV Provider** | Sumsub |
| **AML Screening Provider** | ComplyAdvantage (when configured) |
| **PDF Generation** | WeasyPrint |

### API Design

- **Style:** RESTful with resource-based URLs
- **Authentication:** Bearer token (API) + httpOnly cookie (browser) dual authentication
- **Authorization:** Role-based with 4-role permission matrix (admin > sco > co > analyst)
- **Error handling:** Structured JSON errors with HTTP status codes
- **Rate limiting:** In-memory + DB persistence for auth endpoints
- **CORS:** Strict in production, permissive in development
- **CSRF:** Double-submit cookie pattern
- **Total routes:** 200+
- **Public API:** Versioned (v1) with a small set of endpoints for external integration

### Database Schema (Key Entities)

**35+ core tables** organized into domains:

| Domain | Tables | Key Entity |
|---|---|---|
| Identity | users, clients | Actor management |
| Applications | applications, directors, ubos, intermediaries | Core workflow |
| Documents | documents, compliance_resources, regulatory_documents | Evidence management |
| Configuration | risk_config, system_settings, ai_agents, ai_checks | System configuration |
| Screening | screening_reviews | Disposition tracking |
| Audit | audit_log, supervisor_audit_log, decision_records, agent_executions | Traceability |
| Monitoring | monitoring_alerts, periodic_reviews, monitoring_agent_status, transactions | Ongoing surveillance |
| Compliance | compliance_memos, supervisor_pipeline_results, sar_reports, edd_cases | Decision outputs |
| Change Management | (managed via change_management.py with dedicated tables) | Profile versioning |
| GDPR | data_retention_policies, data_subject_requests, data_purge_log | Data lifecycle |
| Security | rate_limits, revoked_tokens, client_sessions | Access control |
| Notifications | notifications, client_notifications | Communication |

### Frontend Structure

| Component | Size | Technology |
|---|---|---|
| Back-office (arie-backoffice.html) | ≈2.0MB, 34,596 lines | Vanilla JS SPA |
| Client portal (arie-portal.html) | ≈745KB, 14,608 lines | Vanilla JS SPA |
| Landing page (index.html) | 55KB, 637 lines | Static HTML |

No build step, no framework, no component library. All JavaScript is inline. This is simultaneously a strength (zero build complexity, instant deployment) and a growing weakness (no code splitting, no TypeScript safety, no component reuse; the back-office single file is now very large).

### Modularity

The backend is partially modularized:

**Well-separated modules (current line counts):**
- rule_engine.py (1,911 lines) — isolated risk scoring
- memo_handler.py (3,467 lines) — isolated memo generation
- validation_engine.py (817 lines) — isolated memo validation
- supervisor_engine.py (585 lines) — isolated contradiction detection
- change_management.py (4,025 lines) — isolated change lifecycle
- security_hardening.py (3,949 lines) — isolated security controls
- screening.py (1,005 lines) — isolated screening integration
- sumsub_client.py (1,357 lines) — isolated KYC provider
- supervisor/ package (12 modules) — agent-orchestration framework
- resilience/ package (10 modules) — circuit breaker, retry, queue patterns

**Monolithic concern:**
- server.py (≈39,964 lines) — route registration and significant business logic remain concentrated in one file. This has grown materially since the prior audit and is now the single largest structural liability; a service-layer decomposition is the priority refactor.

### Coupling vs Separation

The coupling is **acceptable for current scale** but increasingly needs refactoring:
- Core compliance modules (rule_engine, memo_handler, validation_engine, supervisor_engine) are cleanly separated with clear interfaces
- Database access is centralized through db.py
- server.py handlers are tightly coupled to db.py (direct SQL in handlers)
- No service layer between handlers and database — handlers contain business logic

### Scalability Readiness

| Dimension | Current State | Assessment |
|---|---|---|
| Horizontal scaling | Single-process Tornado on a single ECS desired task in staging; multi-task boot/scheduler correctness guards added | ⚠️ Correctness-safe, not yet horizontally scaled |
| Database | PostgreSQL with connection pooling | ✅ Ready |
| File storage | S3 support (boto3) | ✅ Ready |
| Rate limiting | In-memory (per-container / per-process) | ⚠️ Partial |
| Session management | DB-backed tokens | ✅ Ready |
| Background tasks | Resilience queue (SQLite-backed) + active periodic scheduler | ⚠️ Partial |
| Caching | None | ❌ Not implemented |

---

## 6. Data Model & Source of Truth

### Canonical Data Model

The `applications` table is the central entity. All other data gravitates around it:

```
applications (central)
  ├── directors (CASCADE)
  ├── ubos (CASCADE)
  ├── intermediaries (CASCADE)
  ├── documents (CASCADE)
  ├── compliance_memos
  ├── screening_reviews (CASCADE)
  ├── monitoring_alerts (CASCADE)
  ├── periodic_reviews (CASCADE)
  ├── edd_cases
  ├── sar_reports (CASCADE)
  ├── client_notifications (CASCADE)
  ├── client_sessions (CASCADE)
  ├── supervisor_pipeline_results
  ├── decision_records
  └── transactions (CASCADE)
```

### Cross-Module Data Flow

1. **Portal → Backend:** Client submits form data → applications + directors + ubos + intermediaries + documents tables
2. **Backend → Screening:** Application party data → screening provider API → prescreening_data.screening_report JSONB
3. **Screening → Risk:** Screening results + application data → rule_engine.py → risk_score + risk_level + risk_dimensions
4. **Risk → Memo:** Application + risk data + screening data + documents → memo_handler.py → compliance_memos table
5. **Memo → Validation:** memo_data → validation_engine.py → validation_status + quality_score + issues
6. **Validation → Supervisor:** memo + validation results → supervisor_engine.py → verdict + contradictions + persisted hard-block
7. **Supervisor → Decision:** supervisor results → approval gate → decision_records
8. **Decision → Back-office:** All data aggregated for officer review in back-office HTML

### Consistency

**Strong consistency:**
- Application status transitions are server-enforced
- Risk scores are recomputed on data changes (change management triggers recomputation)
- Floor rules are deterministic and non-overridable
- Memo validation cross-checks memo claims against actual data

**Potential gaps:**
- prescreening_data is stored as JSONB (schemaless) — no schema migration for existing data when structure changes
- Screening normalization layer (screening_normalizer.py) is behind feature flag (ENABLE_SCREENING_ABSTRACTION) — dual-write during migration
- Profile versioning captures snapshots but doesn't enforce version-aware queries across all modules

---

## 7. AI Layer Evaluation

### Where AI Is Used

| Component | AI Usage | Model | Purpose |
|---|---|---|---|
| Document Verification (Layer 3) | Claude Vision | Sonnet | Document content interpretation |
| FinCrime Screening (Agent 3) | Claude | Sonnet/Opus | False positive analysis, severity ranking |
| Corporate Structure (Agent 4) | Claude | Sonnet/Opus | Ownership complexity assessment |
| Business Plausibility | Claude | Sonnet/Opus | Business model consistency |
| Adverse Media (Agent 7) | Claude | Sonnet/Opus | Media narrative, disposition |
| Risk Drift (Agent 8) | Claude | Sonnet/Opus | Multi-dimensional drift narrative |
| Ongoing Review (Agent 10) | Claude | Sonnet/Opus | Compliance narrative, consolidation |
| AI Assistant | Claude | Sonnet | Chat-based compliance Q&A |

### Deterministic vs Probabilistic

| Component | Type | Evidence |
|---|---|---|
| Risk scoring (D1-D5) | **Deterministic** | rule_engine.py — weighted formula with hardcoded thresholds |
| Floor rules | **Deterministic** | Sanctioned country → VERY_HIGH (no override) |
| Elevation rules | **Deterministic** | Rule conditions → risk level escalation |
| Memo structure (11 sections) | **Deterministic** | memo_handler.py — template-driven section generation |
| Pre-generation rules (6) | **Deterministic** | SANCTIONED_COUNTRY_FLOOR, BIZ_RISK_FLOOR, etc. |
| Validation (15 rules) | **Deterministic** | validation_engine.py — weighted rule checks |
| Supervisor (11 checks) | **Deterministic** | supervisor_engine.py — contradiction detection |
| Document verification (Layers 0-1) | **Deterministic** | Gate checks + rule-based name/date matching |
| Document verification (Layer 3) | **Probabilistic** | Claude Vision interpretation |
| False positive analysis | **Probabilistic** | Agent 3 AI assessment |
| Narrative generation | **Probabilistic** | Agents 7, 8, 10 AI narratives |

**Key insight:** The architecture ensures that **no probabilistic AI output can reach a compliance decision without passing through deterministic validation and supervisor layers.** This is the critical design decision that makes the system regulatable.

### AI-Rules Interaction

The 4-layer pipeline enforces a strict hierarchy:

```
Layer 1: Rule Engine (deterministic) — Sets boundaries
    ↓ (rules feed into)
Layer 2: Memo Generation (deterministic templates + rule enforcement)
    ↓ (memo feeds into)
Layer 3: Validation Engine (deterministic, 15 rules) — Audits memo quality
    ↓ (validation feeds into)
Layer 4: Supervisor (deterministic, 11 checks) — Detects contradictions
    ↓ (supervisor verdict + persisted hard-block gates)
Human Decision (with 9-point approval gate)
```

AI operates within Layers 2 and 3 as **advisory input**, never as **authoritative output**. The supervisor layer explicitly checks whether AI outputs contradict rule outputs and flags inconsistencies.

### Validation & Supervisor Layers

**Validation Engine (817 lines):**
- 15 weighted rules producing quality score (0-10)
- Detects: structural incompleteness, risk-decision misalignment, unsubstantiated claims, screening defensibility gaps, contradictory keywords
- Output: pass / pass_with_fixes / fail

**Supervisor Engine (585 lines):**
- 11 contradiction checks: risk-vs-decision, ownership inconsistency, PEP findings, document gaps, red flags balance, AI factor classification, confidence linkage, jurisdiction-monitoring alignment, risk divergence, rule engine integration, enforcement verification
- Verdict: CONSISTENT / CONSISTENT_WITH_WARNINGS / INCONSISTENT
- Control: `can_approve` boolean, `requires_sco_review` boolean, supervisor_confidence (penalized per contradiction), and a **persisted hard-block** the approval gate enforces

### Risks of AI-Driven Decisions

1. **Mitigated:** AI cannot override floor rules or deterministic risk levels
2. **Mitigated:** Prompt injection defense with 3-pass recursive sanitization (claude_client.py)
3. **Mitigated:** Production blocks mock mode entirely (fail-closed)
4. **Mitigated:** Pydantic validation on all AI agent outputs
5. **Residual risk:** AI narrative generation (Agents 7, 8, 10) could produce misleading summaries — mitigated by supervisor contradiction detection but not fully eliminated
6. **Residual risk:** Document verification Layer 3 (AI interpretation) could miss authenticity issues — mitigated by policy: "suspicion/escalation signal only, never AI hard-fail"

---

## 8. Compliance Memo Analysis

### How Memos Are Generated

Compliance memos are generated by `memo_handler.py` (`build_compliance_memo()` — 3,467 lines). The process is **deterministic and template-driven**, not AI-generated:

1. **Input assembly:** Application data (company_name, country, sector, directors, ubos, documents, prescreening_data, risk_score, risk_level)
2. **Pre-generation rule enforcement (6 rules):**
   - SANCTIONED_COUNTRY_FLOOR: Force VERY_HIGH for sanctioned jurisdictions
   - BIZ_RISK_FLOOR: Enforce minimum MEDIUM for medium-risk sectors
   - OWN_RISK_FLOOR: Enforce minimum MEDIUM for ownership gaps
   - MULTI_GAP_ESCALATION: Escalate if ≥3 critical gaps
   - CONFIDENCE_FLOOR: Block APPROVE if confidence < 70%; force REVIEW if < 60%
   - CONFIDENCE_CRITICAL_FLOOR: Force escalation at extreme low confidence
3. **Section generation (11 sections):** Each section built from application data using structured templates
4. **Metadata aggregation:** Risk dimensions, approval recommendation, conditions, key findings, review checklist
5. **Post-generation check:** Factor classification correctness (RULE 4A)

### What Data Memos Rely On

- Application table (company details, sector, entity type)
- Directors/UBOs/intermediaries tables (ownership structure, PEP declarations)
- Documents table (verification results, statuses)
- prescreening_data JSONB (screening results, registry data)
- risk_config table (scoring weights, thresholds)
- Risk computation output (5-dimension scores, floor/elevation results)

### Generation Classification

**Hybrid: Template-driven + Rule-assisted**

The memo generation is primarily **template-driven** (sections are constructed from structured data using hardcoded logic, not AI prompts) with **rule-assisted** pre-generation enforcement (6 deterministic rules that override or constrain memo content before generation). Claude AI is optionally used for narrative enrichment within sections but the memo structure, risk ratings, and decision recommendations are deterministic.

This is a deliberate design decision: deterministic memo generation ensures **reproducibility** — the same input always produces the same memo output.

### Consistency and Reproducibility

**Reproducibility: ✅ High**

Given identical input data:
- The same 11 sections are generated
- The same risk dimension ratings are computed
- The same pre-generation rules are enforced
- The same approval recommendation is derived
- The same conditions and review checklist are produced

The only non-deterministic element is if Claude AI is used for narrative enrichment within specific sections — this is isolated and does not affect risk ratings or decisions.

### Operational Leverage

Memo generation **significantly strengthens operational leverage:**

1. **Time reduction:** Manual compliance memo drafting takes 2-4 hours per application. Automated generation takes seconds.
2. **Consistency:** Every memo follows the same 11-section structure with the same risk assessment methodology
3. **Error reduction:** Pre-generation rules prevent common compliance errors (e.g., approving sanctioned jurisdiction applicants)
4. **Scalability:** Memo generation cost is near-zero per application vs. linear compliance officer cost

### Auditability and Reviewer Efficiency

Memo generation **materially increases auditability:**

1. **Structured output:** 11 mandatory sections with consistent naming — regulators can find information in predictable locations
2. **Risk transparency:** 5-dimension risk breakdown with per-dimension ratings and justifications
3. **Factor explainability:** Risk-increasing and risk-decreasing factors with weights
4. **Validation overlay:** 15-point quality audit with quality score — reviewers see pass/fail before reading
5. **Supervisor overlay:** 11-check contradiction detection with a persisted hard-block — reviewers see consistency verdict before approving
6. **PDF immutability:** SHA-256 content hash on generated PDFs — proves document hasn't been modified post-generation
7. **Version tracking:** compliance_memos table tracks version, validation_status, supervisor_status, blocked status

### Commercial Differentiation

Memo generation is **the primary commercial differentiator:**

1. **Most compliance platforms don't generate memos** — they collect data and leave memo drafting to humans
2. **The 4-layer pipeline (rules → generation → validation → supervisor) is unusual** — competitors typically offer either AI-only (unreliable) or template-only (rigid) memo generation
3. **Reproducibility reduces regulatory risk** — regulators can verify that the same inputs produce the same outputs
4. **The validation engine provides automated quality assurance** — this replaces the senior reviewer's initial quality check
5. **PDF generation with immutability hash** — provides evidence-grade output for regulatory examinations

---

## 9. Auditability & Compliance Strength

### Decision Traceability

| Layer | Mechanism | Table |
|---|---|---|
| User actions | Comprehensive audit logging | audit_log |
| AI agent executions | Per-execution tracking with checks/flags | agent_executions |
| Supervisor pipeline | Full pipeline results with agent outputs | supervisor_pipeline_results |
| Supervisor audit | Hash-chained immutable entries | supervisor_audit_log |
| Compliance decisions | Normalized decision records | decision_records |
| Memo lifecycle | Version tracking with validation/supervisor status | compliance_memos |
| Officer sign-off | Server-side IP + User-Agent + acknowledged flag | Persisted via _persist_signoff_audit() |
| AuthZ denials | Uniform denial audit | audit_log (via log_authz_denial) |
| Change management | Before/after profile snapshots | Profile versioning |
| Data purge / erasure | Immutable deletion records + erasure ledger | data_purge_log / gdpr_erasure |

### Reproducibility

- Risk scoring: Deterministic given same inputs (same weights, same country/sector scores)
- Memo generation: Deterministic template-driven generation
- Validation: Deterministic 15-rule audit
- Supervisor: Deterministic 11-check contradiction detection
- Floor rules: Non-overridable hard constraints

### Logging Completeness

- Structured JSON logging (observability.py) for production log aggregation
- Request lifecycle tracking (start/end with duration_ms)
- AI model usage logging (model, tokens, cost)
- Pipeline step tracking
- Validation result logging (quality_score, critical_count)
- Supervisor verdict logging (contradictions, warnings, can_approve)
- Cost comparison logging for model routing decisions

### Explainability

- AI Explainability section (Section 7 of memo): risk-increasing/decreasing factors with weights, decision pathway, supervisor status
- Supervisor confidence score with per-contradiction penalty breakdown
- Factor classification correctness check (risk-decreasing items cannot appear in risk-increasing list)
- Rule engine violation/enforcement logging with rule IDs

### Regulator-Readiness

**Assessment: ⚠️ Strong foundation, needs production hardening**

Strengths:
- Complete audit trail from intake to decision
- Cryptographic hash-chaining on supervisor audit entries
- Immutable PDF memos with SHA-256 content hash
- GDPR compliance (data retention policies, DSAR handling, purge audit, wired-but-OFF subject-erasure ledger)
- Decision record normalization for examination queries

Gaps:
- No regulatory reporting API integration (SAR filing structure exists but no submission)
- No regulatory examination export tool (audit export exists but format is CSV, not regulatory-specific)
- No data retention automation in production (auto-purge default is false; erasure engine default-OFF)
- Sector taxonomy not yet canonical across the pipeline (paused pending compliance policy)

---

## 10. Production Readiness

### Current Readiness Verdict

**RegMind is best described as _production-pilot-ready for controlled deployment_.**

This is a stronger position than the earlier "demo-ready with production-grade subsystems" framing, but it remains materially short of full enterprise-grade readiness. Current `main` evidence shows:

- **Code-confirmed:** EX-01→EX-13, authority-governance, and periodic-review A→F remediation tracks closed; protected controls and dedicated tests exist on `main`
- **Code-confirmed:** GitHub Actions CI on `main` is **green as of 2026-07-06** (latest merged commits passing)
- **Runtime-confirmed (prior cycle):** AWS staging deployment on `main` via ECS Fargate, with SHA-pinned ECR images, readiness checks, and portal/backoffice verification. Operators should re-confirm the current staging revision during diligence.
- **Not yet confirmed:** HA, autoscaling, multi-region DR, SSO/SAML, compliance certifications, or enterprise multi-tenancy

### Readiness by Environment

| Environment | Current status | Basis |
|---|---|---|
| **Internal demo** | ✅ **Ready** | Code-confirmed and suitable for investor, stakeholder, and internal workflow demonstrations |
| **AWS staging / UAT** | ✅ **Ready** | Validated staging runbook, deployment workflow, and green `main` CI |
| **Controlled production pilot** | ⚠️ **Ready with conditions** | Appropriate for controlled deployment with a limited design-partner scope and explicit infrastructure caveats |
| **Broad production rollout** | ❌ **Not ready** | Single-task posture, no autoscaling/HA, unresolved enterprise controls, and structural technical debt remain |
| **Enterprise-grade deployment** | ❌ **Not ready** | No confirmed SSO/SAML, SOC 2 / ISO 27001, multi-tenancy, or cross-region DR posture |

### Remediation & Hardening — Closure Matrix

**EX-01 to EX-13 sprint — closed:**

| Control | Close-out summary | Status |
|---|---|---|
| **EX-01** | Admin reset DB authentication hardening closed | **Code-confirmed** |
| **EX-02** | Demo credential fallback removed | **Code-confirmed** |
| **EX-03** | Mock company data removed | **Code-confirmed** |
| **EX-04** | Webhook idempotency guard closed | **Code-confirmed** |
| **EX-05** | `before_state` / `after_state` audit logging verified | **Runtime-confirmed** |
| **EX-06** | High-risk dual-approval workflow verified | **Runtime-confirmed** |
| **EX-07** | Legacy webhook fallback removed | **Code-confirmed** |
| **EX-08** | Sumsub applicant ID validation verified | **Runtime-confirmed** |
| **EX-09** | Risk score recomputation trigger verified | **Runtime-confirmed** |
| **EX-10** | Screening freshness validation verified | **Runtime-confirmed** |
| **EX-11** | AI outputs advisory labeling + officer sign-off governance verified | **Runtime-confirmed** |
| **EX-12** | Client-side defense-in-depth guards verified | **Runtime-confirmed** |
| **EX-13** | Applications-list N+1 optimization + refresh behavior verified | **Runtime-confirmed** |

**Post-EX tracks closed / hardening since the prior audit:**

| Track | Summary | Status |
|---|---|---|
| Authority governance | Role-authority model, approval-authority matrix, Submit-to-Compliance workflow, UX gates, override/denial/waiver audit, E2E authority matrix | **Closed** |
| Periodic Review A→F | Lifecycle/queue cleanup, document evidence gates, risk-elevation floor, no-silent-downgrade, memo-gated completion, queue filters/actions | **Closed** |
| Monitoring scheduler | Tornado `PeriodicCallback` due-review sweep now active in staging/production with cross-task singleton guard | **Active** |
| Memo hard-block | Supervisor hard-block verdict persisted so the approval gate cannot be bypassed | **Closed** |
| AML screening honesty | Screening readiness truthfulness + provider-provenance accuracy (no default-to-provider) | **Closed** |
| GDPR subject erasure | Category-keyed, fail-closed, default-OFF erasure engine with complete ledger | **Wired (OFF)** |
| Multi-task safety | Boot-phase advisory lock + scheduler singleton; DATABASE_URL required in staging/prod; liveness health-check; rollback runbook | **Closed** |
| CA production validation | ComplyAdvantage production-workspace validation (PR-PROV1 / PR-PROV1A) | **Pending — separate workstream** |
| Sector taxonomy / risk policy | Canonical regulated-financial-activity taxonomy + synonym calibration | **Paused — pending compliance policy** |

### Components That Are Production-Ready Within the Current Pilot Posture

| Component | Evidence |
|---|---|
| Risk scoring engine | Code-confirmed; recomputation and threshold governance hardened |
| Memo generation | Code-confirmed; deterministic 11-section memo pipeline + persisted hard-block |
| Validation engine | Code-confirmed; 15-point validation layer |
| Supervisor engine | Code-confirmed; contradiction detection and approval gating |
| Security hardening | Code-confirmed; approval gates, webhook verification, auth controls, screening freshness |
| Change management | Code-confirmed; auditability and recomputation controls |
| Authentication / RBAC / authority | Code-confirmed; demo fallbacks removed, authority governance closed |
| Audit trail | Code-confirmed; before/after state capture and sign-off governance |
| Document verification | Code-confirmed; 5-layer verification matrix remains one of the strongest subsystems |
| Ongoing monitoring scheduler | Code-confirmed; active automatic due-review sweep with cross-task guard |

### Components That Remain Pilot-Ready, Partial, or Demo-Ready

| Component | Current posture | Limitation |
|---|---|---|
| AML screening (provider) | **Pilot-ready** | ComplyAdvantage production-workspace validation in progress; provider abstraction not yet sole live path |
| Regulatory intelligence | **Partially implemented / demo-ready** | AI analysis remains scaffolded rather than fully production-proven |
| Agent health monitoring | **Demo-ready / internal-governance-ready** | Good internal oversight surface, but not yet an enterprise ops control plane |
| Reports | **Pilot-ready for internal use** | Limited customization and enterprise reporting depth |
| Public API v1 | **Partial** | Limited external integration breadth and documentation |
| Sector taxonomy | **Paused** | Canonical taxonomy not yet formalised; synonym false-negative risk identified |

### AWS Staging Architecture (Known Posture)

| Component | Current known state |
|---|---|
| **Region** | `af-south-1` (Cape Town) |
| **Runtime** | AWS ECS Fargate |
| **Cluster / service** | `regmind-staging` / `regmind-backend` |
| **Task size** | 1 vCPU / 3 GiB RAM |
| **Desired tasks** | 1 |
| **Autoscaling** | Not configured |
| **ALB** | `regmind-staging-alb` |
| **Target group** | `regmind-staging-tg` |
| **Document storage** | S3 bucket `regmind-documents-staging` |
| **Secrets** | AWS Secrets Manager (`regmind/staging`) |
| **Image tags** | ECR `regmind-backend:$GIT_SHA` plus `:latest` |

### Remaining Production / Enterprise Blockers

1. **Single ECS desired task** — staging proves operability, but not HA (multi-task correctness guards now exist)
2. **No autoscaling configured** — capacity remains manually bounded
3. **No full HA posture** — no multi-task or multi-AZ application redundancy confirmed at the service layer
4. **Staging public-IP exposure remains a consideration** — not yet a locked-down enterprise network design
5. **No confirmed SOC 2 / ISO 27001** — limits enterprise procurement readiness
6. **No confirmed SSO / SAML** — limits enterprise identity integration
7. **No full multi-tenancy model** — current posture is not enterprise tenant-isolation grade
8. **No full DR / cross-region failover** — recovery posture is not yet enterprise-class
9. **Limited production customer evidence** — a first paid pilot is signed (see Section 14); broad regulatory production proof is not yet established
10. **Structural technical debt** — the ≈39,964-line `server.py` monolith and large single-file frontends remain material scaling risks (and have grown since the prior audit)

### Reliability Risks

1. **Resilience layer exists but is not integrated into all API calls** — circuit_breaker.py, retry_policy.py, task_queue.py are available but manual integration required
2. **WeasyPrint dependency** — heavy C library dependency for PDF generation; font/rendering issues possible across environments
3. **Screening provider dependency** — if the configured provider is unavailable, the screening pipeline degrades; the abstraction layer is not yet the sole live path

### Security Considerations

**Strengths:**
- Fail-closed approval gates (9 sequential checks) with persisted supervisor hard-block
- PII encryption with Fernet (field-level)
- Prompt injection defense (3-pass recursive sanitization)
- Magic byte file validation (prevents MIME spoofing)
- Token revocation with DB persistence
- Production environment guards (block mock mode, require credentials, DATABASE_URL required)
- Password policy (12+ chars, 4 character types)
- HMAC-SHA256 webhook signature verification (timing-attack safe)

**Considerations:**
- No WAF (Web Application Firewall) — relies on framework-level protections
- No penetration test evidence in repository
- CORS configuration is environment-driven — misconfiguration risk in deployment
- No Content Security Policy nonce for inline scripts (single-file HTML)

### Deployment Maturity

- **Docker:** Dockerfile with non-root user, health checks (liveness-probed), persistent volumes ✅
- **Docker Compose:** PostgreSQL 16 + backend with health checks ✅
- **GitHub Actions CI:** `main` CI green as of 2026-07-06 ✅
- **AWS staging deploy:** ECS Fargate staging deploy from `main` (re-confirm current revision in diligence) ✅
- **Demo surface:** Render remains relevant for demo, not as the authoritative near-production path ✅
- **No Kubernetes manifests** — ECS is workflow-driven rather than IaC-defined
- **No infrastructure-as-code** (no Terraform, no CloudFormation)

---

## 11. Strengths

### Technical Strengths
1. **≈294,408 lines of Python** across 531 files — substantial codebase, not a prototype
2. **~6,000 automated test functions** on `main` — CI-enforced minimum collected-test threshold
3. **10 AI agents** with defined authority levels (authoritative vs decision_support) — sophisticated agent architecture
4. **5-layer document verification** (gate → rule → hybrid → AI → aggregation) — defense-in-depth
5. **Pydantic validation on AI outputs** — prevents malformed AI responses from propagating
6. **Resilience module** (circuit breaker, retry policy, task queue) + multi-task boot/scheduler correctness guards

### Workflow Strengths
1. **17-status application lifecycle** — comprehensive state machine covering all compliance workflow states
2. **4-layer AI pipeline** (rules → memo → validation → supervisor) — prevents AI hallucination from reaching decisions
3. **9-point approval gate + persisted supervisor hard-block** — fail-closed, sequential prerequisite checks before any approval
4. **Materiality-tiered change management** — Tier 1 (structural) triggers different downstream actions than Tier 3 (administrative)
5. **Dual-control for high-risk decisions** — HIGH/VERY_HIGH risk requires SCO/admin + pre-approval gate; authority governance closed
6. **Active ongoing-monitoring scheduler** — automatic periodic-review sweep in staging/production

### Architectural Strengths
1. **Clean module separation** for core compliance logic (rule_engine, memo_handler, validation_engine, supervisor_engine)
2. **Supervisor package** (12 modules) — full agent orchestration framework with contradiction detection
3. **Configuration-driven risk model** — institutions can adjust weights, thresholds, country/sector scores
4. **Extensive feature-flag system** (environment.py) — controlled rollout of new capabilities
5. **Screening abstraction layer** — provider migration infrastructure (behind feature flag)

### Commercial Strengths
1. **Automated compliance memo generation** — eliminates 2-4 hours of manual drafting per application
2. **Risk-based model routing** — optimizes AI costs (Sonnet for LOW/MEDIUM, Opus for HIGH/VERY_HIGH)
3. **Complete onboarding-to-monitoring lifecycle** — not just onboarding, ongoing compliance too
4. **GDPR tooling** — data retention policies, DSAR handling, immutable purge audit, wired erasure ledger
5. **Two-surface architecture** — separate portal (clients) and back-office (officers) reduces per-surface complexity

---

## 12. Weaknesses / Risks

### Incomplete Workflows
1. **SAR filing** — SAR data structure exists (sar_reports table) but no regulatory submission API integration
2. **Agent 9 (Regulatory Impact)** — marked as future_phase; not implemented
3. **AML provider production validation** — ComplyAdvantage production-workspace validation (PR-PROV1 / PR-PROV1A) is a pending separate workstream
4. **Regulatory intelligence analysis** — structure present but AI analysis pipeline not production-tested
5. **Sector taxonomy** — canonical regulated-financial-activity taxonomy not yet formalised; a synonym false-negative risk in the risk/screening pipeline is identified and **paused pending compliance policy** (must not be represented as fixed)

### Inconsistencies
1. **Legacy naming** — files use `arie-` prefix while brand is "Onboarda" / "RegMind"; causes confusion
2. **Dual database support** — SQLite (dev) / PostgreSQL (prod) divergence risks (e.g., JSONB vs TEXT behavior)
3. **Screening dual-write** — normalized and legacy screening data coexist behind feature flag; migration incomplete

### Fragile Areas
1. **server.py at ≈39,964 lines** — any merge conflict, syntax error, or import failure crashes the entire backend; the file has grown materially and is the priority decomposition target
2. **Single-file HTML frontends** — the ≈2.0 MB / 34,596-line back-office is well past comfortable single-file limits
3. **prescreening_data JSONB** — schemaless storage means no schema migration for historical data

### Architectural Issues
1. **No service layer** — HTTP handlers contain business logic and direct database queries
2. **No message queue** — agent execution, notifications, and background tasks are synchronous or queue-adjacent (SQLite-backed resilience queue)
3. **No caching layer** — every request hits the database; no Redis/Memcached for frequently accessed data
4. **No API versioning beyond v1** — internal APIs are unversioned; breaking changes will affect integrations
5. **No WebSocket/SSE** — no real-time updates; clients must poll for status changes

### Scalability Concerns
1. **Single-process Tornado** — CPU-bound operations (PDF generation, AI agent execution) block the event loop
2. **In-memory rate limiting** — per-process only; doesn't work in multi-instance deployment
3. **File uploads to local disk** — S3 support exists but local disk is default; persistent volume limits container orchestration
4. **No database read replicas** — all queries hit primary; analytics/reports will compete with transactional workload

---

## 13. Defensibility

### What Is Hard to Replicate

1. **The 4-layer deterministic AI pipeline** — The combination of rule engine → template memo → validation audit → supervisor contradiction detection is architecturally distinctive. Competitors typically use AI-only (unreliable) or template-only (rigid) approaches. The hybrid approach requires deep compliance domain knowledge to design correctly.

2. **The verification matrix** (1,255 lines) — A complete encoding of document-level compliance checks (GATE-01 through DOC-XX) with check classification (RULE/HYBRID/AI), trigger timing, and escalation outcomes. This represents months of compliance expertise encoded in software.

3. **10 specialized AI agents** with defined authority levels — The agent catalog (authoritative vs decision_support) with per-agent check decomposition is a sophisticated architecture that requires both compliance and AI engineering expertise.

4. **Mauritius regulatory specificity** — FATF grey/black list alignment, secrecy jurisdiction scoring, Mauritius DPA 2017 GDPR compliance, FIU Mauritius SAR reporting — this is jurisdiction-specific compliance knowledge embedded in code.

5. **Test coverage** — ~6,000 automated tests create a meaningful regression safety net that competitors starting from scratch will struggle to match.

### Where Moat Exists or Can Be Built

- **Compliance memo quality** — As more memos are generated and validated, the template and rule engine can be refined. This creates a data flywheel.
- **Verification check matrix** — Each new document type and jurisdiction adds to the matrix, creating cumulative compliance IP.
- **Risk model configuration** — Institutions that configure their risk models create switching costs.
- **Audit trail** — Historical audit data becomes valuable for regulatory examinations and cannot be migrated to competitors.

### Reliance on External Providers

| Provider | Dependency | Risk | Mitigation |
|---|---|---|---|
| Sumsub | KYC / IDV (individual identity verification) | High — single provider | Provider-abstraction infrastructure exists but is not yet the sole live path |
| ComplyAdvantage (Mesh) | AML/PEP/sanctions/watchlist/adverse-media/ongoing-monitoring screening | Medium — active when configured | Sandbox/staging path validated; **production-workspace validation in progress (PR-PROV1 / PR-PROV1A)** |
| Anthropic Claude | AI agent execution | High — sole AI provider | Fail-closed mock blocking in production |
| OpenCorporates | Company registry enrichment | Medium — enrichment only | Graceful degradation / partial implementation |
| AWS ECS Fargate | Staging / planned production hosting | Medium — current service is single-task and non-autoscaled | Portable Docker/ECR deployment path already exists |
| Render.com | Demo hosting | Low | Isolated from the near-production AWS staging path |
| WeasyPrint | PDF generation | Low — OSS library | Replaceable |

### Uniqueness of Workflow Integration

The integration of screening → risk scoring → memo generation → validation → supervisor → decision into a single pipeline with mandatory sequential execution is distinctive. Most compliance platforms treat these as independent tools. RegMind's architecture enforces that **you cannot approve an application without passing through all pipeline stages**, and each stage's output feeds into the next stage's validation.

---

## 14. Commercial Readiness

### How Sellable Is the Product Today

**Sellable as a controlled production pilot.** The platform can credibly support a limited pilot deployment with explicit infrastructure caveats, while also serving as a strong investor / regulator / prospect demonstration environment. It should not yet be marketed as fully enterprise-grade software.

### Commercial Status (company-confirmed)

- **First pilot signed** — an FSC-regulated institution in Mauritius, at USD 60,000 ARR + a USD 40,000 one-time implementation fee (Year-1 total contract value ≈ USD 100,000); billing commences H2 2026. Revenue is contracted but not yet recognised, and the deployment has not yet been validated through a regulatory examination cycle.
- **Second pilot in pipeline** — a comparable regulated institution expected within ~6 months at similar pricing; not yet contracted.

### Best ICP (Ideal Customer Profile)

1. **Primary:** Small-to-medium EMIs and payment institutions in Mauritius applying for or holding FSC licences
2. **Secondary:** Compliance consultancies serving multiple regulated entities who need a shared compliance platform
3. **Tertiary:** Mid-tier banks in emerging markets (Africa, MENA) seeking to digitize compliance workflows

### Strongest Use Cases

1. **New licence applications** — End-to-end onboarding workflow with regulator-ready memo output
2. **Ongoing compliance management** — Periodic reviews, monitoring alerts, change management for existing clients
3. **Compliance team augmentation** — AI agents handle routine checks, freeing officers for judgment-intensive decisions
4. **Regulatory examination preparation** — Complete audit trail + hash-chained supervisor logs + PDF memos

### What Is Missing for Closing Enterprise Deals

1. **SSO/SAML integration** — Enterprise customers require federated identity
2. **Multi-tenancy** — Current architecture is single-tenant; enterprises need isolated data per business unit
3. **SLA guarantees** — No uptime monitoring, alerting, or SLA enforcement infrastructure
4. **Compliance certifications** — No SOC 2, ISO 27001, or equivalent certification
5. **Data residency / regional posture** — staging is on AWS af-south-1, but there is no demonstrated multi-region residency or failover model
6. **API documentation** — Public API v1 is limited; enterprise integration requires comprehensive API docs
7. **Disaster recovery** — No documented backup/restore procedures, no cross-region failover

---

## 15. Valuation Perspective

> This section describes maturity-related valuation factors only. It does **not** state a valuation. A standalone valuation and a product fact base are maintained as separate documents.

### Product Maturity Classification

**Controlled pilot stage / Early commercial deployment**

The codebase demonstrates depth that exceeds typical demo-stage platforms (≈294,408 lines of Python, ~6,000 tests, 35+ core tables) and has a credible validated staging posture on AWS. It still lacks the operational infrastructure and enterprise controls (HA, autoscaling, DR, SSO, certifications, multi-tenancy) required for broad enterprise production classification.

### Strengths Supporting Valuation

1. **Deep compliance domain encoding** — The verification matrix, risk model, and memo template represent months of regulatory expertise codified in software. This is not easily replicated.
2. **Test coverage** — ~6,000 automated tests provide materially stronger deployment confidence and reduce future development risk.
3. **Architecture sophistication** — 4-layer AI pipeline with deterministic controls is a distinctive approach to compliance automation.
4. **Complete workflow coverage** — From client registration through ongoing monitoring and change management — this is not a point solution.
5. **Security & governance posture** — 9-point approval gate + persisted hard-block, PII encryption, cryptographic audit chain, authority governance, prompt injection defense.

### Weaknesses Limiting Valuation

1. **Monolithic architecture** — server.py (≈39,964 lines, grown since the prior audit) creates deployment risk and limits team scaling.
2. **Provider dependency** — Sumsub (KYC/IDV) and ComplyAdvantage (AML, production validation pending); abstraction layer not yet the sole live path.
3. **No production-validated customers** — one pilot is signed but not yet live/recognised or regulator-examined.
4. **No compliance certifications** — SOC 2, ISO 27001 absence limits enterprise sales conversations.
5. **Single-file frontends** — technical debt that will require significant refactoring for feature velocity.

### What Would Increase Valuation Significantly

1. **First production customer live with regulatory sign-off** — proves the platform satisfies actual regulatory requirements
2. **Second pilot signed** — demonstrates repeatability across institution types
3. **SOC 2 Type I certification** — table stakes for enterprise compliance software sales
4. **server.py decomposition** into a service layer — the priority refactor; the monolith has grown, not shrunk
5. **ComplyAdvantage production validation + multi-provider screening** — completes the AML core and removes single-provider risk
6. **Frontend modernization** — component framework with type safety for the back-office
7. **Second-jurisdiction expansion** — beyond Mauritius (UK FCA, EU AMLD6, or DFSA) would materially increase TAM (requires the canonical sector taxonomy to be locked first)

---

## Final Verdict

### "Is RegMind a scalable, enterprise-grade compliance operating system with real market value?"

**RegMind is a genuine compliance operating system with real market value, and it is now credible as a controlled production-pilot platform — but it is not yet enterprise-grade.**

**It IS:**
- A complete compliance operating system (not a tool or platform)
- Architecturally sophisticated (4-layer deterministic AI pipeline)
- Deeply encoded with compliance domain knowledge (verification matrix, risk model, memo templates)
- Well-tested (~6,000 automated test functions)
- Security- and governance-conscious (PII encryption, approval gates + persisted hard-block, cryptographic audit chain, authority governance)
- Workflow-complete (onboarding through ongoing monitoring, change management, and an active periodic-review scheduler)

**It is NOT yet:**
- Horizontally scalable (single-process Tornado / single ECS desired task; multi-task correctness guards exist but no autoscaling/HA)
- Enterprise-ready (no SSO, no multi-tenancy, no compliance certifications)
- Broad-production-proven (one signed pilot, not yet live/recognised or regulator-examined)
- Architecturally modular (≈39,964-line server.py, single-file frontends)
- Fully provider-validated (ComplyAdvantage production validation pending)
- Sector-taxonomy-consistent (canonical taxonomy paused pending compliance policy)

**Assessment:** RegMind has moved well beyond a pure demo narrative and should be positioned as **production-pilot-ready for controlled deployment**. The compliance domain depth, AI pipeline architecture, closed remediation/governance tracks, active monitoring scheduler, and staging evidence create a strong foundation. The remaining risks are structural and operational — monolith growth, scaling posture, enterprise identity, certification, tenancy, DR, and provider/taxonomy closure — rather than conceptual.

**For acquisition purposes:** The value is in the compliance IP (verification matrix, risk model, memo pipeline, supervisor framework, authority governance) and the architectural approach (deterministic AI controls with human approval gates), not in the current deployment infrastructure. An acquirer would likely retain the compliance logic and re-platform the infrastructure.

**For investment purposes:** The platform demonstrates strong depth for its stage. The ≈294,408 lines of Python, ~6,000 automated tests, 10 AI agents, closed governance/remediation tracks, and current AWS staging posture represent substantial engineering investment. With first-customer pilot validation (live and recognised), ComplyAdvantage production clearance, and architectural hardening, RegMind could command a meaningful valuation in the compliance technology space.

---

*Report refreshed from codebase audit of `onboarda1234/onboarda` — measured against `main` at 2026-07-06. Product and technology facts are traceable to specific source files and measured metrics; commercial facts are company-confirmed. This document is a product/technical audit and does not itself constitute a valuation.*
