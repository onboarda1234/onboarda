# PR-KYC-EDD-REQUIREMENTS-1A Current vs Target Diff

- Generated at: `20260618T111704Z`
- Target source: `docs/compliance/kyc-edd-matrix-v5.md`
- Current sources: `arie-backend/enhanced_requirements.py`, `arie-backend/document_policy_registry.py`, `arie-portal.html`, `arie-backoffice.html`, `arie-backend/db.py`
- Code changes made before this diff: none after the required v5 compliance-file commit.

## Parse Gate

| Required table | Rows parsed | Status |
|---|---:|---|
| Standard KYC — Canonical Document Policies | 16 | OK |
| Enhanced Requirements — Target Rows | 14 | OK |
| Section Mapping (portal visibility) | 7 | OK |
| HIGH/VERY-HIGH Baseline Pack | 8 | OK |

## Standard KYC Target Deltas

| Target item | Current observation | Required implementation |
|---|---|---|
| Section B `bankref` | Portal has PEP-only bank reference rows and `bankref` in `DOC_TYPE_MAP`; current EDD also generates `pep_bank_reference`. | Keep canonical `bankref`; make Section B person-level conditional for HIGH/VERY_HIGH or PEP; remove duplicate EDD PEP bank-reference generation for new apps. |
| Section B `source_wealth` | `source_wealth` exists in document policy registry and portal `DOC_TYPE_MAP`; current EDD generates `material_ubo_sow_evidence` and `pep_sow_evidence`. | Add/ensure Section B person-level SOW requirement for HIGH/VERY_HIGH or PEP director/UBO; preserve `person_id`/`person_type`; remove duplicate EDD SOW rows for new apps. |

## Enhanced Requirements Target Rows

| Requirement key | Target status | Current default rule | Current policy map | Diff / action |
|---|---|---|---|---|
| `company_bank_reference` | Active mandatory — conditional on existing bank account | active=True mandatory=False blocking=False audience=client type=document scope=company | `bankref` | mandatory current=False target=Yes (conditional); missing existing-bank-account applicability condition |
| `company_sof_evidence` | Active mandatory | active=True mandatory=True blocking=True audience=client type=document scope=company | `source_funds` | blocking current=True target=No by default |
| `pep_declaration_details` | Active mandatory | active=True mandatory=True blocking=True audience=client type=declaration scope=screening_subject | `None` | label mismatch |
| `pep_adverse_media_assessment` | Active back-office | <missing> | `None` | missing default rule |
| `pep_enhanced_monitoring_flag` | Active back-office | <missing> | `None` | missing default rule |
| `aml_cft_policy` | Active advisory | active=True mandatory=False blocking=False audience=client type=document scope=company | `aml_policy` | label mismatch |
| `trust_nominee_foundation_documents` | Active mandatory | active=True mandatory=True blocking=True audience=client type=document scope=controller | `trust_deed` | label mismatch |
| `jurisdiction_sof_evidence` | Inactive in some settings | active=False mandatory=True blocking=True audience=client type=document scope=application | `source_funds` | label mismatch; keep inactive unless activated/requested |
| `jurisdiction_exposure_rationale` | Active mandatory (conditional on country of incorporation) | active=True mandatory=True blocking=True audience=client type=explanation scope=application | `None` | label mismatch; client-visible description contains unsafe trigger wording |
| `jurisdiction_risk_assessment` | Active back-office | <missing> | `None` | missing default rule |
| `contracts_invoices` | Active mandatory | active=True mandatory=True blocking=True audience=client type=document scope=application | `contracts` | blocking current=True target=No; label mismatch |
| `expected_transaction_flow_evidence` | Inactive by default | active=False mandatory=True blocking=True audience=client type=document scope=application | `supporting_document` | keep inactive unless activated/requested |
| `major_counterparties_explanation` | Active mandatory | active=True mandatory=True blocking=True audience=client type=explanation scope=application | `None` | blocking current=True target=No |
| `volume_rationale_vs_business_size` | Active mandatory | active=True mandatory=True blocking=True audience=client type=explanation scope=application | `None` | aligned or no code change expected |

## Current Active Rules Not In v5 Target

| Current key | Active? | In required removal list? | Notes |
|---|---:|---:|---|
| `adverse_media_pep_sanctions_assessment` | True | True | Screening safety exception candidate: remove only after independent screening gates are proven. |
| `client_clarification_screening` | True | True | Remove from active generation/default seeding for new apps. |
| `company_bank_statements_6m` | False | True | Inactive default; remove from active/default target map or keep only as documented legacy compatibility if needed. |
| `control_rationale` | True | False | Remove from active generation/default seeding for new apps. |
| `crypto_source_of_funds_evidence` | True | True | Remove from active generation/default seeding for new apps. |
| `enhanced_business_activity_explanation` | True | True | Remove from active generation/default seeding for new apps. |
| `enhanced_screening_review` | True | False | Remove from active generation/default seeding for new apps. |
| `enhanced_ubo_evidence` | True | True | Remove from active generation/default seeding for new apps. |
| `false_positive_rationale` | True | True | Screening safety exception candidate: remove only after independent screening gates are proven. |
| `high_volume_bank_statements` | False | True | Inactive default; remove from active/default target map or keep only as documented legacy compatibility if needed. |
| `jurisdiction_licensing_regulatory_evidence` | True | True | Remove from active generation/default seeding for new apps. |
| `jurisdictions_served` | True | False | Remove from active generation/default seeding for new apps. |
| `licence_or_registration_evidence` | True | True | Remove from active generation/default seeding for new apps. |
| `mandatory_senior_review` | True | False | Remove from active generation/default seeding for new apps. |
| `material_screening_senior_review` | True | True | Screening safety exception candidate: remove only after independent screening gates are proven. |
| `material_ubo_sow_evidence` | True | False | Remove from active generation/default seeding for new apps. |
| `ongoing_monitoring_flag` | True | False | Remove from active generation/default seeding for new apps. |
| `operating_country_target_market_explanation` | True | False | Remove from active generation/default seeding for new apps. |
| `ownership_chain_documents` | True | True | Remove from active generation/default seeding for new apps. |
| `ownership_structure_chart` | False | False | Inactive duplicate EDD ownership chart exists; v5 says avoid duplicate Section A display. |
| `pep_bank_reference` | True | True | Remove from active generation/default seeding for new apps. |
| `pep_jurisdiction` | True | True | Remove from active generation/default seeding for new apps. |
| `pep_linked_sof_evidence` | True | True | Remove from active generation/default seeding for new apps. |
| `pep_role_position` | True | True | Remove from active generation/default seeding for new apps. |
| `pep_sow_evidence` | True | True | Remove from active generation/default seeding for new apps. |
| `screening_disposition` | True | True | Screening safety exception candidate: remove only after independent screening gates are proven. |
| `transaction_flow_explanation` | True | False | Remove from active generation/default seeding for new apps. |
| `wallet_exchange_counterparty_exposure` | True | False | Remove from active generation/default seeding for new apps. |

## Minimum Removal Key Inventory

| Key from task | Present in current defaults | Active in current defaults | Present in policy map |
|---|---:|---:|---:|
| `enhanced_business_activity_explanation` | True | True | False |
| `company_bank_statements_6m` | True | False | True |
| `pep_role_position` | True | True | False |
| `pep_jurisdiction` | True | True | False |
| `pep_sow_evidence` | True | True | True |
| `pep_bank_reference` | True | True | True |
| `pep_linked_sof_evidence` | True | True | True |
| `crypto_source_of_funds_evidence` | True | True | True |
| `licence_or_registration_evidence` | True | True | True |
| `crypto_enhanced_monitoring_flag` | False | False | False |
| `crypto_regulatory_status_assessment` | False | False | False |
| `ownership_chain_documents` | True | True | True |
| `enhanced_ubo_evidence` | True | True | True |
| `jurisdiction_licensing_regulatory_evidence` | True | True | True |
| `high_volume_bank_statements` | True | False | True |
| `screening_disposition` | True | True | False |
| `false_positive_rationale` | True | True | False |
| `adverse_media_pep_sanctions_assessment` | True | True | False |
| `material_screening_senior_review` | True | True | False |
| `client_clarification_screening` | True | True | False |
| `manual_edd_pack` | False | False | False |
| `money_services_pack` | False | False | False |
| `regulated_financial_services_pack` | False | False | False |
| `cross_border_pack` | False | False | False |
| `high_risk_product_pack` | False | False | False |

## Policy Map and Document Policy Registry

- Current `ENHANCED_REQUIREMENT_DOCUMENT_POLICY_MAP` keys: `aml_cft_policy, company_bank_reference, company_bank_statements_6m, company_sof_evidence, contracts_invoices, crypto_source_of_funds_evidence, enhanced_ubo_evidence, expected_transaction_flow_evidence, high_volume_bank_statements, jurisdiction_licensing_regulatory_evidence, jurisdiction_sof_evidence, licence_or_registration_evidence, material_ubo_sow_evidence, ownership_chain_documents, ownership_structure_chart, pep_bank_reference, pep_linked_sof_evidence, pep_sow_evidence, trust_nominee_foundation_documents`
- Current bank-account-dependent enhanced keys: `high_volume_bank_statements`

| doc_type | Registry status | Backend executable | Policy id | Diff / action |
|---|---|---:|---|---|
| `aml_policy` | Active | True | `DOC-ENTITY-AML-POLICY-v1` | OK; runtime verified |
| `bank_statements` | Active | True | `DOC-EDD-BANK-STATEMENTS-v1` | OK; runtime verified |
| `bankref` | Active | True | `DOC-EVIDENCE-BANK-REFERENCE-v1` | OK; runtime verified |
| `board_res` | Active | True | `DOC-ENTITY-BOARD-RES-v1` | OK; runtime verified |
| `cert_inc` | Active | True | `DOC-ENTITY-COI-v1` | OK; runtime verified |
| `contracts` | Active | True | `DOC-ENTITY-CONTRACTS-v1` | OK; runtime verified |
| `cv` | Active | True | `DOC-PERSON-CV-v1` | OK; runtime verified |
| `fin_stmt` | Active | True | `DOC-ENTITY-FINANCIALS-v1` | OK; runtime verified |
| `licence` | Active | True | `DOC-ENTITY-LICENCE-v1` | OK; runtime verified |
| `memarts` | Active | True | `DOC-ENTITY-MEMARTS-v1` | OK; runtime verified |
| `passport` | Active | True | `DOC-PERSON-PASSPORT-v1` | OK; runtime verified |
| `poa` | Active | True | `DOC-ENTITY-REGISTERED-ADDRESS-v1` | OK; runtime verified |
| `reg_dir` | Active | True | `DOC-ENTITY-REGDIR-v1` | OK; runtime verified |
| `reg_sh` | Active | True | `DOC-ENTITY-REGSH-v1` | OK; runtime verified |
| `source_funds` | Active | True | `DOC-EDD-SOF-v1` | OK; runtime verified |
| `source_wealth` | Active | True | `DOC-EDD-SOW-v1` | OK; runtime verified |
| `structure_chart` | Active | True | `DOC-ENTITY-OWNERSHIP-CHART-v1` | OK; runtime verified |
| `supporting_document` | Manual review only | False | `DOC-UNKNOWN-UNCLASSIFIED-v1` | Manual review only; acceptable only where v5 says manual/no Agent 1. |
| `trust_deed` | Manual review only | False | `DOC-ENTITY-TRUST-DEED-v1` | Manual review only; acceptable only where v5 says manual/no Agent 1. |

## Portal Current Mapping

- `DOC_TYPE_MAP` contains: `{"doc-aml-policy": "aml_policy", "doc-bank-ref": "bankref", "doc-bank-statements": "bank_statements", "doc-board-res": "board_res", "doc-coi": "cert_inc", "doc-contracts": "contracts", "doc-directors-reg": "reg_dir", "doc-financials": "fin_stmt", "doc-license-cert": "licence", "doc-memarts": "memarts", "doc-proof-address": "poa", "doc-shareholders": "reg_sh", "doc-source-funds-proof": "source_funds", "doc-source-wealth-proof": "source_wealth", "doc-structure-chart": "structure_chart"}`
- Current review UI has sections A-D only; enhanced requirement portal rendering is dynamic through requested `application_enhanced_requirements` and does not expose F/G when audience is `backoffice`.
- Section E target is not a static upload section today; current enhanced explanation/declaration fulfilment is dynamic and must be kept client-safe.
- `jurisdiction_exposure_rationale` current client-safe description falls back to rule description and includes “high-risk jurisdiction”; must be neutralized to “Required for certain countries of incorporation.” or equivalent.
- Section G verification history is not represented as a portal section; must stay excluded from portal.

## Back-Office Settings Current Fields

Enhanced Requirements rule form fields found:

```json
[
  "er-active",
  "er-active-filter",
  "er-add-btn",
  "er-audience",
  "er-audience-filter",
  "er-blocking-approval",
  "er-client-safe-description",
  "er-client-safe-label",
  "er-doc-' + escapeHtml(controlId) + '",
  "er-form-card",
  "er-form-title",
  "er-internal-notes",
  "er-mandatory",
  "er-notes-' + escapeHtml(controlId) + '",
  "er-requirement-description",
  "er-requirement-key",
  "er-requirement-label",
  "er-requirement-type",
  "er-rule-id",
  "er-search",
  "er-sort-order",
  "er-status-' + escapeHtml(controlId) + '",
  "er-subject-scope",
  "er-trigger-category",
  "er-trigger-filter",
  "er-trigger-key",
  "er-trigger-label",
  "er-upload-' + escapeHtml(controlId) + '",
  "er-waivable",
  "er-waiver-' + escapeHtml(controlId) + '",
  "er-waiver-roles"
]
```

- Existing enhanced-rule persistence supports: trigger key/label/category, requirement key/label/description, audience, requirement_type, subject_scope, blocking_approval, waivable/roles, mandatory, active, sort_order, client_safe_label, client_safe_description, internal_notes.
- Existing enhanced-rule persistence does **not** have first-class `section` or canonical `doc_type` columns. Adding them would require schema/API changes; implementation should prefer derived section/doc_type metadata unless a safe existing JSON field can carry non-breaking metadata.
- Document Verification Policies are currently static JS/back-end registry definitions rather than a persisted settings API; editable controls exist for check label/rule/type, but active/runtime/manual/doc_type/section persistence was not found in this pass.

## Safety Gate Before Implementation

- No target row requires changing non-admin API response shapes if implemented through existing enhanced-rule fields plus derived metadata.
- No target row requires mutating historical `application_enhanced_requirements`; generation is create-only and snapshots active rules for new applications.
- No target row requires weakening approval/document/screening gates; several current defaults are stricter than v5 for EDD blocking and must be changed only where v5 explicitly says non-blocking.
- Screening rows must not be removed until independent screening disposition and second-review gates are proven. If proof is insufficient, retain those rows under `screening_gate_safety_exception.md`.
- Adding editable first-class `section`/`doc_type` fields to persisted enhanced rules would require DB schema/API expansion; avoid unless stopped and approved.
