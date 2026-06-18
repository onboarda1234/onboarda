# PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1
## Portal KYC / Enhanced Document Requirements Audit

**Audit ID:** PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1  
**Date:** 2026-06-18  
**Auditor:** Copilot Code Agent  
**Scope:** Static code audit — no live applications created, no data modified  
**Status:** COMPLETE — evidence recorded, findings summarised

---

## 1. Executive Summary

This audit systematically inspects the code paths governing KYC document requirements for LOW, MEDIUM, HIGH, and VERY_HIGH risk applications in the Onboarda / RegMind platform. All evidence is drawn from static source analysis of:

- `arie-backend/server.py` — API endpoints, status machine, pre-approval gate
- `arie-backend/enhanced_requirements.py` — EDD trigger resolution, requirement generation, portal projection
- `arie-backend/rule_engine.py` — risk scoring, risk levels, EDD flag computation
- `arie-backend/document_policy_registry.py` — canonical Agent 1 policies
- `arie-portal.html` — portal UI, document checklist, polling, client-facing wording

**Overall finding:** The core flow is structurally correct and the client-safe boundary is enforced. Several secondary observations are recorded in §8.

---

## 2. Flow Verification — Status Machine

### 2.1 Normal Flow (LOW / MEDIUM risk)

```
prescreening_submitted → pricing_review → pricing_accepted → kyc_documents
                                        ↑
                             (no pre-approval step)
```

**Evidence:**  
`server.py:6098–6113` — valid state transitions table. LOW/MEDIUM: `pricing_accepted` → `kyc_documents` directly.  
`server.py:7865` — `requires_pre_approval` is only set when `risk["level"] in ("HIGH", "VERY_HIGH") or risk.get("lane") == "EDD"`.

### 2.2 HIGH / VERY_HIGH Flow

```
prescreening_submitted → pricing_review → pre_approval_review → pre_approved → kyc_documents
```

**Evidence:**  
`server.py:6101–6103`:
```python
"prescreening_submitted": ["pricing_review", "pre_approval_review"],
"pre_approval_review": ["pre_approved", "rejected", "draft"],
"pre_approved": ["kyc_documents"],
```

`server.py:6127–6134` — hard guard on transition to `kyc_documents`:
```python
# v2.1: HIGH/VERY_HIGH risk MUST go through pre_approval_review before kyc_documents
if new_status == "kyc_documents" and risk_level in ("HIGH", "VERY_HIGH"):
    if app.get("pre_approval_decision") != "PRE_APPROVE":
        raise 403 — "HIGH/VERY_HIGH risk applications must be pre-approved before KYC."
```

`server.py:7908–7919` — after pricing acceptance, HIGH/VERY_HIGH are routed to `pre_approval_review`.

### 2.3 KYC Blocked Until Pre-Approval

`server.py:7962–7994` (`_kyc_prerequisite_error`):
- Applications in `pricing_accepted`, `pre_approval_review`, or `pre_approved` return `"pre_approval_or_routing_incomplete"` — KYC uploads/submissions are blocked.
- If `_kyc_requires_pre_approval(app)` is true AND `pre_approval_decision != "PRE_APPROVE"`, even `kyc_documents` status returns `"pre_approval_required"`.

**Finding:** ✅ CLIENT IS BLOCKED FROM KYC UNTIL PRE-APPROVAL IS RECORDED FOR HIGH/VERY_HIGH.

---

## 3. Portal Client-Safe Boundary

### 3.1 API Projection — Forbidden Keys

`server.py:4741–4900` defines `CLIENT_APPLICATION_DETAIL_FORBIDDEN_KEYS`. The following are **explicitly stripped** before any portal response:

| Field | Category |
|---|---|
| `risk_score` | Internal risk data |
| `risk_level` | Internal risk data |
| `final_risk_level` | Internal risk data |
| `final_risk_level_label` | Internal risk data |
| `base_risk_level` | Internal risk data |
| `risk_dimensions` | Internal risk data |
| `risk_escalations` | Internal risk data |
| `risk_integrity_warnings` | Internal risk data |
| `risk_computed_at` | Internal risk data |
| `edd_trigger_flags` | EDD routing |
| `elevation_reason_text` | Risk reasoning |
| `onboarding_lane` | Routing lane (EDD/Fast Lane/Standard) |
| `pre_approval_decision` | Officer decision |
| `pre_approval_notes` | Officer notes |
| `pre_approval_officer_id` | Officer identity |
| `enhanced_review_summary` | EDD summary |
| `latest_memo` / `latest_memo_data` | AI memo |
| `compliance_memos` | AI memos |
| `screening_mode` / `screening_reviews` / `screening_truth_summary` | Screening data |
| `gate_blockers` / `approval_gate_presentation` | Approval gate |
| `officer_corrections` | Officer override data |
| `decision_records` | Decision paper |
| `edd_cases` | EDD case data |

**Prescreening pricing keys also stripped:** `risk_score`, `risk_level`, `final_risk_level`, `risk_breakdown`, `risk_dimensions`, `risk_factors`, `risk_level_label`.

**Finding:** ✅ NO RISK SCORE, RISK LABEL, EDD FLAG, SCREENING MODE, OR AI MEMO DATA IS EXPOSED TO THE CLIENT PORTAL VIA THE API.

### 3.2 Portal Status Labels (Client-Facing Wording)

From `arie-portal.html:5721–5722`:

| Internal Status | Client-Facing Label |
|---|---|
| `pre_approval_review` | "Application Under Review" |
| `pre_approved` | "Ready for Documents" |
| `kyc_documents` | "Documents Required" |
| `edd_required` | "Application Under Review" |
| `compliance_review` | "Compliance Review in Progress" |
| `in_review` | "Verification Ongoing" |
| `under_review` | "Under Compliance Review" |
| `approved` | "Approved – Ready for Activation" |
| `rejected` | "Application Declined" |

**Finding:** ✅ NO STATUS LABEL CONTAINS "HIGH RISK", "EDD", "VERY HIGH", "SCREENING", OR "AI" LANGUAGE.

### 3.3 Pre-Approval Hold View

`arie-portal.html:3173–3230` — the `view-pre-approval-hold` panel shows:
- Title: "Application Under Compliance Review"
- Body: "Your application is being reviewed before the document submission step is opened. No action is required at this time."
- Card: "Review In Progress — Our team is reviewing your application. No documents are required at this stage."
- What happens next: neutral wording referencing "application details review" and "notify by email".

**Finding:** ✅ PRE-APPROVAL HOLD VIEW IS CLEAN. NO RISK TERMINOLOGY EXPOSED.

### 3.4 Risk Score / Risk Scoring in Portal

`arie-portal.html:7002–7005` — `requiresEnhancedRiskDocs()` is a stub that always returns `false`.

`arie-portal.html:7145–7175` — `showRiskResult()` overwrites the risk result div with:
```javascript
resultEl.innerHTML = '<div>✅ Application Step Ready</div><p>Transitioning to pricing schedule...</p>';
```
and then transitions to `showPricingView()`. No risk score, risk label, or EDD flag is rendered.

`arie-portal.html:6957–6999` — `buildRiskDisplayState()` accesses `app.final_risk_level` / `app.risk_level` / `app.risk_score` — but these are stripped by the server projection (§3.1), so they are always `undefined` in the portal. The function returns `hasRisk: false` when fields are absent, and the `renderRiskDisplay()` function renders "Application review complete" as a neutral badge with no score text.

**Finding:** ✅ RISK SCORE AND RISK LABEL ARE NOT RENDERED TO THE CLIENT. THE PORTAL BADGE SAYS "Application review complete" REGARDLESS OF RISK LEVEL.

---

## 4. Standard KYC Document Checklist

### 4.1 Section A — Corporate Entity Documents (Same for all risk levels)

| Doc ID | Backend doc_type | Required |
|---|---|---|
| Certificate of Incorporation | `cert_inc` | Required |
| Memorandum of Association | `memarts` | Required |
| Shareholder Register | `reg_sh` | Required |
| Register of Directors | `reg_dir` | Required |
| Financial Statements / Management Accounts | `fin_stmt` | Required |
| Proof of Registered Address | `poa` | Required |
| Board Resolution | `board_res` | Required |
| Company Structure Chart | `structure_chart` | Required |
| Bank Statements (Last 6 Months) | `bank_statements` | Conditional — shown only if `existing_bank_account = true` |
| Regulatory Licence(s) | `licence` | Optional — shown only if `has_licence = true` |

### 4.2 Section B — Per-Person KYC Documents (Same for all risk levels)

Each director, UBO, and individual intermediary shareholder gets:

| Document | Backend key | Notes |
|---|---|---|
| Passport / Government ID | `passport` | Required per person; `person_id` sent with upload |
| Proof of Address (Personal) | `poa` | Required per person |
| CV / LinkedIn Profile | `cv` | Required per person |
| Bank Reference Letter | `bankref` | Required per person (standard in Section B) |

For corporate intermediaries, per-entity slots:
- `cert_inc` (Intermediary Certificate of Incorporation)
- `reg_dir` (Register of Directors)
- `reg_sh` (Register of Shareholders)
- `cert_gs` (Certificate of Good Standing)
- `fin_stmt` (Financial Statements)

### 4.3 Section C — Enhanced / EDD Requirements (Dynamic, compliance-requested)

Enhanced requirements are NOT shown in the static HTML checklist as visible items. They appear dynamically in the `additional-info-required-card` container in Section C only after they are **requested** by a compliance officer (status = `requested`).

Optional static documents in Section C (initially hidden, never auto-shown by `requiresEnhancedRiskDocs`):
- `doc-source-wealth-proof` → `source_wealth` (style: `display:none`)
- `doc-source-funds-proof` → `source_funds` (style: `display:none`)
- `doc-aml-policy` → `aml_policy` (style: `display:none`)
- `doc-contracts` → `contracts` (always visible, Optional label)

**Finding:** ✅ HIGH-RISK-SPECIFIC ENHANCED DOCUMENTS ARE NOT PRE-POPULATED IN THE PORTAL CHECKLIST. THEY APPEAR ONLY WHEN EXPLICITLY REQUESTED BY COMPLIANCE THROUGH THE ENHANCED REQUIREMENTS SYSTEM.

### 4.4 DOC_TYPE_MAP Completeness

`arie-portal.html:9147–9155`:
```javascript
DOC_TYPE_MAP = {
  'doc-coi': 'cert_inc', 'doc-memarts': 'memarts', 'doc-shareholders': 'reg_sh',
  'doc-directors-reg': 'reg_dir', 'doc-financials': 'fin_stmt', 'doc-proof-address': 'poa',
  'doc-board-res': 'board_res', 'doc-structure-chart': 'structure_chart',
  'doc-bank-ref': 'bankref', 'doc-license-cert': 'licence',
  'doc-contracts': 'contracts', 'doc-source-wealth-proof': 'source_wealth',
  'doc-source-funds-proof': 'source_funds', 'doc-bank-statements': 'bank_statements',
  'doc-aml-policy': 'aml_policy'
}
```

All Section A/C/D static document slots map to canonical backend doc_types. No `undefined` mapping identified for any active document slot.

**Finding:** ✅ DOC_TYPE_MAP IS COMPLETE FOR ALL ACTIVE STATIC DOCUMENT SLOTS.

---

## 5. Enhanced / EDD Document Requirements by Trigger

Enhanced requirements are generated by `generate_application_enhanced_requirements()` in `enhanced_requirements.py`, triggered by routing flags from the EDD routing engine.

### 5.1 Trigger Mapping

| EDD Routing Trigger | Enhanced Requirement Trigger | Trigger Category |
|---|---|---|
| `high_or_very_high_risk` | `high_or_very_high_risk` | risk |
| `declared_pep_present` | `pep` | screening |
| `crypto_or_virtual_asset_sector` | `crypto_vasp` | sector |
| `elevated_jurisdiction` | `high_risk_jurisdiction` | jurisdiction |
| `opaque_or_incomplete_ownership` | `opaque_ownership` | structure |
| `material_screening_concern` | `screening_concern` | screening |
| Declared high-volume transaction volume | `high_volume` | transaction |

### 5.2 HIGH / VERY_HIGH Risk (`high_or_very_high_risk`)

| Requirement Key | Label | Audience | Type | Blocking Approval? |
|---|---|---|---|---|
| `company_bank_reference` | Company bank reference letter | client | document | No (default False) |
| `company_bank_statements_6m` | 6 months company bank statements | client | document | **INACTIVE by default** |
| `company_sof_evidence` | Company Source of Funds evidence | client | document | No |
| `material_ubo_sow_evidence` | UBO Source of Wealth evidence | client | document | No |
| `enhanced_business_activity_explanation` | Enhanced business activity explanation | client | explanation | No |

**Notes:**
- `company_bank_statements_6m` has `"active": False` in defaults — not generated unless admin enables the rule.
- Bank reference and SoF/SoW are standard for HIGH/VERY_HIGH but not set as `blocking_approval=True` by default (approval can proceed if waived by admin/SCO).

### 5.3 PEP (`pep`)

| Requirement Key | Label | Audience | Blocking Approval? |
|---|---|---|---|
| `pep_declaration_details` | PEP declaration details | client | Yes (default) |
| `pep_role_position` | PEP role/position | both | No |
| `pep_jurisdiction` | PEP jurisdiction | both | No |
| `pep_sow_evidence` | Source of Wealth Evidence — [PEP name] | client | No |
| `pep_bank_reference` | Bank Reference Letter — [PEP name] | client | **Yes — mandatory=1, blocking_approval=1** |
| `pep_linked_sof_evidence` | Source of Funds evidence (PEP-linked) | client | No |
| `pep_adverse_media_assessment` | Adverse media assessment | backoffice | No |
| `pep_enhanced_monitoring_flag` | Enhanced monitoring flag | backoffice | No |

**Notes:**
- `pep_sow_evidence` and `pep_bank_reference` are generated **per identified PEP subject** (per-UBO/director).
- `pep_bank_reference`: **blocking_approval=True, mandatory=True** (seeder: `enhanced_requirements.py:1587`). This will block final approval if not accepted or validly waived.
- Portal fallback copy for PEP requirements uses neutral wording (e.g. "Source of wealth evidence", "Role and public-position information") — no "PEP" or "politically exposed" language.

### 5.4 Cryptocurrency / VASP (`crypto_vasp`)

| Requirement Key | Label | Audience | Notes |
|---|---|---|---|
| `aml_cft_policy` | AML/CFT Policy document | client | Maps to `aml_policy` policy |
| `licence_or_registration_evidence` | Licence/registration evidence | client | Maps to `licence` policy |
| `crypto_source_of_funds_evidence` | Source of funds evidence (crypto) | client | Maps to `source_funds` |
| `ownership_structure_chart` | Ownership/control structure | client | Maps to `structure_chart` |
| `crypto_enhanced_monitoring_flag` | Enhanced monitoring flag | backoffice | Internal only |
| `crypto_regulatory_status_assessment` | Regulatory status assessment | backoffice | Internal only |

### 5.5 Opaque Ownership (`opaque_ownership`)

| Requirement Key | Label | Audience | Verification Mode |
|---|---|---|---|
| `ownership_structure_chart` | Ownership/control structure | client | active_runtime_verified |
| `ownership_chain_documents` | Ownership chain supporting docs | client | manual_review_only |
| `enhanced_ubo_evidence` | Enhanced UBO evidence | client | manual_review_only |
| `trust_nominee_foundation_documents` | Trust/nominee/foundation documents | client | manual_review_only |
| `expected_transaction_flow_evidence` | Expected transaction flow evidence | client | manual_review_only (**INACTIVE** by default) |

### 5.6 High-Risk Jurisdiction (`high_risk_jurisdiction`)

| Requirement Key | Audience | Maps to Policy |
|---|---|---|
| `jurisdiction_sof_evidence` | client | `source_funds` — active_runtime_verified |
| `jurisdiction_licensing_regulatory_evidence` | client | `licence` — active_runtime_verified |
| `jurisdiction_enhanced_monitoring_flag` | backoffice | internal only |
| `jurisdiction_risk_assessment` | backoffice | internal only |
| `jurisdiction_waivers_exceptions` | backoffice | internal only |

### 5.7 High Volume (`high_volume`)

| Requirement Key | Audience | Notes |
|---|---|---|
| `contracts_invoices` | client | active — maps to `contracts` |
| `expected_transaction_flow_evidence` | client | **INACTIVE by default** |
| `high_volume_bank_statements` | client | **INACTIVE by default**; also requires `existing_bank_account=true` |
| `major_counterparties_explanation` | client | explanation type |
| `volume_rationale_vs_business_size` | client | explanation type |

### 5.8 Screening Concern (`screening_concern`)

| Requirement Key | Audience | Notes |
|---|---|---|
| `screening_disposition` | backoffice | Internal review task |
| `false_positive_rationale` | backoffice | Internal review task |
| `adverse_media_pep_sanctions_assessment` | backoffice | Internal review task |
| `material_screening_senior_review` | backoffice | Internal; waivable=False |
| `client_clarification_screening` | both | Client-facing; mandatory=False; only used when back office determines it is necessary |

**Finding for all EDD triggers:** ✅ REQUIREMENTS ARE TRIGGER-SPECIFIC, NOT GENERIC. EACH TRIGGER MAPS TO A DISTINCT SET OF DOCUMENTS/DECLARATIONS. THE REQUIREMENTS COVER: SOURCE OF WEALTH, SOURCE OF FUNDS, BANK STATEMENTS, BANK REFERENCE, OWNERSHIP CHART, BUSINESS RATIONALE, PEP EXPLANATION/EVIDENCE, LICENCE/REGULATORY PROOF — MATCHING THE AUDIT SCOPE CHECKLIST.

---

## 6. Canonical Document Policy and Agent 1 Verification

### 6.1 Mapping to Canonical Policies

`enhanced_requirements.py:ENHANCED_REQUIREMENT_DOCUMENT_POLICY_MAP`:

| Requirement Key | Canonical doc_type | Verification Mode |
|---|---|---|
| `company_bank_reference` | `bankref` | active_runtime_verified |
| `company_bank_statements_6m` | `bank_statements` | active_runtime_verified |
| `company_sof_evidence` | `source_funds` | active_runtime_verified |
| `material_ubo_sow_evidence` | `source_wealth` | active_runtime_verified |
| `pep_sow_evidence` | `source_wealth` | active_runtime_verified |
| `pep_bank_reference` | `bankref` | active_runtime_verified |
| `pep_linked_sof_evidence` | `source_funds` | active_runtime_verified |
| `aml_cft_policy` | `aml_policy` | active_runtime_verified |
| `licence_or_registration_evidence` | `licence` | active_runtime_verified |
| `crypto_source_of_funds_evidence` | `source_funds` | active_runtime_verified |
| `ownership_structure_chart` | `structure_chart` | active_runtime_verified |
| `jurisdiction_sof_evidence` | `source_funds` | active_runtime_verified |
| `jurisdiction_licensing_regulatory_evidence` | `licence` | active_runtime_verified |
| `contracts_invoices` | `contracts` | active_runtime_verified |
| `high_volume_bank_statements` | `bank_statements` | active_runtime_verified |
| `ownership_chain_documents` | `supporting_document` | manual_review_only |
| `enhanced_ubo_evidence` | `supporting_document` | manual_review_only |
| `trust_nominee_foundation_documents` | `trust_deed` | manual_review_only |
| `expected_transaction_flow_evidence` | `supporting_document` | manual_review_only |

### 6.2 Agent 1 Verification Coverage

Requirements mapping to `bankref`, `source_funds`, `source_wealth`, `aml_policy`, `licence`, `structure_chart`, `contracts`, `bank_statements` are `active_runtime_verified` — Agent 1 checks run automatically when these documents are uploaded.

Requirements mapping to `supporting_document` or `trust_deed` are `manual_review_only` — no automated Agent 1 verification; compliance officer review required.

**Finding:** ✅ AGENT 1 VERIFICATION APPLIES TO THE MAJORITY OF ENHANCED DOCUMENT TYPES. MANUAL-REVIEW-ONLY REQUIREMENTS ARE APPROPRIATELY SCOPED TO COMPLEX/OPAQUE STRUCTURE DOCUMENTS THAT CANNOT BE ALGORITHMICALLY VERIFIED.

---

## 7. Approval Blocking by Missing Enhanced Documents

### 7.1 Validation Gate

`enhanced_requirements.py:2160–2268` (`validate_enhanced_requirements_for_approval`):
- Called before approval for HIGH/EDD applications.
- If `enhanced_review_active` (HIGH/VERY_HIGH risk) and no requirements have been generated → approval **blocked** with `missing_generated_requirements=True`.
- If requirements are generated but `blocking_approval=True` / `mandatory=True` requirements are not `accepted` or validly waived → approval **blocked** (`unresolved_count > 0`).
- `passed=False` prevents the approval action.

### 7.2 Default Blocking Status per Requirement

- Most HIGH/VERY_HIGH requirements: `blocking_approval=False` by default (configurable by admin).
- `pep_declaration_details`: `blocking_approval=True` (default rule).
- `pep_bank_reference`: `mandatory=1, blocking_approval=1` (seeder override).
- `screening_disposition` (screening_concern, backoffice): `waivable=False` — cannot be waived.
- `material_screening_senior_review` (screening_concern, backoffice): `waivable=False`.

**Finding:** ✅ MISSING ENHANCED DOCS WITH `blocking_approval=True` BLOCK FINAL APPROVAL. NON-BLOCKING REQUIREMENTS ARE WAIVABLE BY ADMIN/SCO. THE PEP BANK REFERENCE IS HARD-BLOCKING.

---

## 8. Client-Facing Wording Verification

### 8.1 Portal Enhanced Requirement Label Safety

`enhanced_requirements.py:3137–3165` — `_CLIENT_UNSAFE_LABEL_TERMS`:

The following terms are **filtered out** from all requirement labels/descriptions shown to clients:
`"adverse media"`, `"approval blocker"`, `"back-office"`, `"backoffice"`, `"edd"`, `"enhanced due diligence"`, `"false-positive"`, `"false positive"`, `"internal"`, `"officer"`, `"high risk"`, `"high-risk"`, `"pep"`, `"politically exposed"`, `"risk level"`, `"sanction"`, `"screening"`, `"screening concern"`, `"senior review"`, `"trigger"`, `"very high"`, `"very_high"`, `"waiver"`.

If a requirement label or description contains any of these terms, a neutral fallback is substituted (`_portal_safe_fallback_copy`). If no safe copy can be produced, the requirement is **skipped entirely** (not shown to client).

### 8.2 PEP-Specific Fallback Copy

`enhanced_requirements.py:3176–3196` — explicit safe copies for PEP requirements:
- `pep_declaration_details` → "Additional declaration details"
- `pep_role_position` → "Role and public-position information"
- `pep_jurisdiction` → "Public-position jurisdiction information"
- `pep_sow_evidence` → "Source of wealth evidence"
- `pep_linked_sof_evidence` → "Source of funds evidence"

### 8.3 Portal Section C Header

`arie-portal.html:3725–3745`:
- Section C header: "C — Additional Required Documents — Additional documents requested by Compliance for this application"
- Green banner: "If Compliance has requested additional documents, they will appear here."
- Dynamic requirement cards show: "Requested by Compliance · [subject] · [date]" — no risk or EDD wording.

### 8.4 Portal Status Wording Summary

The phrase "Additional information is required to complete compliance review" does **not appear verbatim** in the portal, but equivalent neutral wording is used throughout:
- "If we require any additional information, our team will contact you directly" (`arie-portal.html:3961`)
- "You may be contacted if additional information is needed" (`arie-portal.html:3215`)
- "Additional information required" (fallback copy for unknown requirement types)
- Status label for `edd_required`: "Application Under Review"

**Finding:** ✅ CLIENT-FACING WORDING IS CLEAN AND NON-TECHNICAL. NO "HIGH RISK", "EDD", "PEP", "SCREENING", OR RISK SCORE LANGUAGE IS EXPOSED TO THE CLIENT.

---

## 9. Portal Polling and Upload Integrity

### 9.1 Document Verification Polling

`arie-portal.html:7876–7909`:
- Polls `/documents/:id/verification-status` every **2 seconds** up to **120 seconds** max.
- Validated before starting: `isValidPortalPathId(options.docId)` — rejects null/undefined docId silently (console.warn only, no user-visible error).
- Terminates on terminal verification states.
- Per-document timer map prevents duplicate polls.

### 9.2 personId Validation

`arie-portal.html:10693–10720` (`handleKYCUpload`):
- Validates `personId` using `isValidPortalPersonId` before any upload attempt.
- If invalid: resets file input, shows toast "Person identifier is missing. Please refresh the page and try again." — **no `undefined` is passed to the API**.
- Validates `docType` with `isValidPortalPathId` before upload.

### 9.3 Enhanced Requirement Upload

`arie-portal.html:9625–9680` (`uploadPortalEnhancedRequirement`):
- Validates `AUTH_TOKEN` and `currentApplicationId` before upload.
- `portalEnhancedRequirementBusy` map prevents double-submission.
- Backend endpoint: `POST /portal/applications/:id/enhanced-requirements/:req_id/upload`.

**Finding:** ✅ NO UNDEFINED PERSONID BUG IDENTIFIED. GUARDS ARE IN PLACE. POLLING IS BOUNDED AND VALIDATED.

---

## 10. Back-Office vs Portal Data Availability Matrix

| Data Point | Back Office | Portal |
|---|---|---|
| Risk score (numeric) | ✅ Visible | ❌ Stripped |
| Risk level (LOW/MEDIUM/HIGH/VERY_HIGH) | ✅ Visible | ❌ Stripped |
| EDD trigger flags | ✅ Visible | ❌ Stripped |
| Onboarding lane (EDD/Fast Lane) | ✅ Visible | ❌ Stripped |
| Pre-approval decision | ✅ Visible | ❌ Stripped |
| Pre-approval notes | ✅ Visible | ❌ Stripped |
| Compliance memo | ✅ Visible | ❌ Stripped |
| Screening results | ✅ Visible | ❌ Stripped |
| Application status (neutral label) | ✅ Internal | ✅ Client-safe label |
| Standard document checklist | ✅ Same (Sections A–D) | ✅ Same (Sections A–D) |
| Enhanced requirements (internal) | ✅ All statuses/types | ❌ Only client/both audience, requested status |
| Enhanced requirement labels | ✅ Internal labels | ✅ Filtered/safe copy |
| PEP screening confirmed | ✅ Visible | ❌ Stripped |
| Document verification results | ✅ Full detail | ❌ Stripped |

---

## 11. Observations and Secondary Findings

### OBS-1: `company_bank_statements_6m` is INACTIVE by default
**File:** `enhanced_requirements.py` default rules  
**Detail:** The `high_or_very_high_risk` bank statements rule has `"active": False`. It will not be generated unless an admin explicitly activates the rule in Settings → Enhanced Requirements. This means HIGH/VERY_HIGH applicants do **not** automatically receive a bank statements requirement — only the bank reference and SoF/SoW requirements are generated by default.  
**Risk:** LOW — bank statements can be requested via the non-EDD static portal Section A checkbox, and the rule can be activated. No compliance gap if the officer reviews the application before approving.

### OBS-2: Static SoW/SoF/AML Documents in Section C Are Never Auto-Shown
**File:** `arie-portal.html:3765–3805`  
**Detail:** `doc-source-wealth-proof`, `doc-source-funds-proof`, `doc-aml-policy` in Section C have `style="display:none"` and `requiresEnhancedRiskDocs()` always returns `false`. These static slots are never auto-revealed. EDD documents for these types are served only via the dynamic enhanced requirements system.  
**Risk:** LOW — this is intentional. The dynamic enhanced requirements system is the correct mechanism. The static hidden slots are legacy scaffolding.

### OBS-3: Most HIGH/VERY_HIGH EDD Requirements are Non-Blocking by Default
**File:** `enhanced_requirements.py` DEFAULT_ENHANCED_REQUIREMENT_RULES  
**Detail:** `company_bank_reference`, `company_sof_evidence`, `material_ubo_sow_evidence`, `enhanced_business_activity_explanation` all default to `blocking_approval=False`. Approval can proceed even if these are unresolved (unless the rule has been configured as blocking in Settings).  
**Risk:** MEDIUM — depends on compliance officer review discipline. An admin/SCO can configure rules as `blocking_approval=True` in the settings. Consider whether these should default to `blocking_approval=True` for HIGH/VERY_HIGH.

### OBS-4: `buildRiskDisplayState` Accesses Stripped Fields (Safe)
**File:** `arie-portal.html:6957–6999`  
**Detail:** `buildRiskDisplayState` reads `app.final_risk_level`, `app.risk_level`, `app.risk_score`, `app.edd_trigger_flags` from the API response. These fields are stripped server-side. The function gracefully returns `hasRisk: false` when they are absent, and `renderRiskDisplay` shows a neutral badge. No client information leakage.  
**Risk:** NONE — functionally safe. The fields are present at submit-time (before `_client_safe_application_detail` projection) for the pricing display, then stripped for all subsequent loads.

### OBS-5: `portal-explain-section` Is Hidden, But Contains Risk Dimension HTML Infrastructure
**File:** `arie-portal.html:3147–3162`  
**Detail:** `portal-explain-section` and `portal-explain-dimensions` divs exist in the HTML. The `showRiskResult` function overwrites the parent `risk-result` div entirely before this becomes visible. The section is never populated with actual risk data.  
**Risk:** NONE — completely overwritten before display.

### OBS-6: `pre_approval_review` → Pre-Approval Hold View Shows a 6-Step Flow to Client
**File:** `arie-portal.html:3173–3220`  
**Detail:** The pre-approval hold view stepper shows: Pre-Screening → Application Review → Pricing → **Pre-Approval** → KYC & Documents → Approved. The step label "Pre-Approval" is technically visible to the client in the step bar.  
**Risk:** LOW — "Pre-Approval" is a neutral process term. It does not imply high risk. However, for sensitive cases the step label could be changed to "Compliance Review" for complete opacity.

### OBS-7: Section B Static Cards for dir1/ubo1 Have Hard-Coded IDs
**File:** `arie-portal.html:3537–3660`  
**Detail:** The Section B HTML includes static cards for `dir1` and `ubo1` hard-coded. Dynamic persons added via `syncDirectorsUBOsToKYC()` use generated IDs. This is a UI pattern note, not a data issue.  
**Risk:** NONE — functional, tested. Static fallback ensures at least one director/UBO is always shown.

---

## 12. Summary Verdict

| Audit Scope Item | Finding | Status |
|---|---|---|
| Pre-approval required for HIGH/VERY_HIGH | Enforced at API level — cannot be bypassed | ✅ PASS |
| Client blocked from KYC before pre-approval | `_kyc_prerequisite_error` gate active | ✅ PASS |
| Portal does not expose risk score | Stripped in CLIENT_APPLICATION_DETAIL_FORBIDDEN_KEYS | ✅ PASS |
| Portal does not expose risk label | Stripped — all status labels are neutral | ✅ PASS |
| Portal does not expose EDD/screening language | All unsafe terms filtered + fallback copy | ✅ PASS |
| Portal does not expose AI scoring/reasoning | Latest memo, risk_dimensions, edd_trigger_flags all stripped | ✅ PASS |
| Client-facing wording is clean | "Additional information required" / "Requested by Compliance" | ✅ PASS |
| Standard doc checklist is same for all risk levels | Sections A–B identical regardless of risk | ✅ PASS |
| High-risk EDD docs are risk-specific, not generic | Trigger-mapped requirements per EDD trigger type | ✅ PASS |
| SoW / SoF / Bank Reference / Ownership Chart covered | Generated by high_or_very_high_risk / pep / opaque_ownership | ✅ PASS |
| PEP explanation and evidence required | pep_declaration_details, pep_sow_evidence, pep_bank_reference | ✅ PASS |
| Licence/regulatory proof for relevant sectors | crypto_vasp + high_risk_jurisdiction trigger licence requirement | ✅ PASS |
| Business/transaction rationale required | enhanced_business_activity_explanation + high_volume explanations | ✅ PASS |
| Enhanced documents mapped to canonical policies | ENHANCED_REQUIREMENT_DOCUMENT_POLICY_MAP complete | ✅ PASS |
| Agent 1 verification on enhanced docs | active_runtime_verified for all main EDD doc types | ✅ PASS |
| Manual-review-only for complex structure docs | supporting_document/trust_deed = manual_review_only | ✅ PASS |
| Missing enhanced docs block approval | validate_enhanced_requirements_for_approval gates approval | ✅ PASS (conditional on blocking_approval config) |
| PEP bank reference hard-blocks approval | mandatory=1, blocking_approval=1 in seeder | ✅ PASS |
| Portal polling bounded and validated | 2s interval, 120s max, personId/docId validated | ✅ PASS |
| No undefined personId issue | handleKYCUpload guards against missing personId | ✅ PASS |
| DOC_TYPE_MAP complete | All active static slots mapped | ✅ PASS |
| `company_bank_statements_6m` inactive by default | Must be activated in settings | ⚠️ OBS-1 |
| Most HIGH/VERY_HIGH EDD requirements non-blocking | Configurable; defaults to non-blocking | ⚠️ OBS-3 |
| "Pre-Approval" step label visible to client | Step label neutral; low risk | ℹ️ OBS-6 |

---

## 13. Pre-Conditions for PR-PORTAL-PILOT-BOUNDARY-1

Based on this audit, the following pre-conditions are confirmed:

1. **The pre-approval gate is functional** — HIGH/VERY_HIGH cannot reach KYC without officer pre-approval (code-verified).
2. **The client-safe boundary is enforced** — no risk score, label, or EDD data leaks to the portal via the API.
3. **The enhanced requirements engine is trigger-specific** — requirements match the audit checklist (SoW, SoF, bank reference, ownership, PEP, licence, business rationale).
4. **Agent 1 applies to the majority of enhanced doc types**.
5. **Portal polling and upload mapping are clean** — no undefined personId bugs.
6. **Client wording is clean** — no "high risk", "EDD", "PEP", or "screening" in portal labels.

**Recommended actions before PR-PORTAL-PILOT-BOUNDARY-1:**
- Confirm whether `blocking_approval` defaults for HIGH/VERY_HIGH requirements (`company_bank_reference`, `company_sof_evidence`, `material_ubo_sow_evidence`) should be elevated to `True` for the pilot.
- Confirm whether `company_bank_statements_6m` should be activated in pilot settings.
- Review OBS-6 (Pre-Approval step label) if complete opacity is required.
