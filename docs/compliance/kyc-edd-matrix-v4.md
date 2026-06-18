# RegMind KYC/EDD Requirements Matrix — v4 (APPROVED TARGET STATE)

This is the canonical target configuration for back-office Enhanced Requirements
and Document Verification Policies. Source of truth for the alignment task.


## Standard KYC — Canonical Document Policies

| Section | Document | Applies To | Risk Scope | doc_type | Required? | Agent 1? | Notes/Display |
|---|---|---|---|---|---|---|---|
| A | Certificate of Incorporation | All companies | All risk levels | cert_inc | Required | Yes |  |
| A | Memorandum / Articles | All companies | All risk levels | memarts | Required | Yes |  |
| A | Shareholder Register | All companies | All risk levels | reg_sh | Required | Yes | RMI replacement must satisfy entity:reg_sh. |
| A | Register of Directors | All companies | All risk levels | reg_dir | Required | Yes |  |
| A | Financial Statements / Management Accounts | All companies | All risk levels | fin_stmt | Required | Yes |  |
| A | Proof of Registered Address | All companies | All risk levels | poa | Required | Yes |  |
| A | Board Resolution | All companies | All risk levels | board_res | Required | Yes |  |
| A | Company Structure Chart | All companies | All risk levels | structure_chart | Required | Yes | Also overlaps with opaque ownership EDD trigger; avoid duplicate display. |
| A | Company Bank Statements | Companies with existing bank account | All risk levels where applicable | bank_statements | Conditional | Yes | Also relevant to EDD/high-volume if activated. |
| A | Regulatory Licence(s) | Licensed or regulated entity | All risk levels where applicable | licence | Conditional | Yes |  |
| B | Passport / Government ID | Each director, UBO, individual intermediary | All risk levels | passport | Required per person | Yes |  |
| B | Personal Proof of Address | Each director, UBO, individual intermediary | All risk levels | poa | Required per person | Yes |  |
| B | CV / LinkedIn Profile | Each director, UBO, individual intermediary | All risk levels | cv | Required per person | Yes |  |
| B | Bank Reference Letter | Each director, UBO, individual intermediary | HIGH / VERY HIGH risk, or director/UBO who is a PEP | bankref | Conditional per person | Yes | Only required when the application is HIGH/VERY HIGH risk, or the specific director/UBO is a PEP. Not required for standard/low-risk persons. Carries the former EDD pep_bank_reference requirement (now consolidated here). |
| B | Source of Wealth evidence | Specific UBO/director (per person) | HIGH / VERY HIGH risk, or UBO/director who is a PEP | source_wealth | Conditional per person | Yes | Person-level Source of Wealth, attached to the specific UBO/director. Consolidates the former EDD requirements material_ubo_sow_evidence and pep_sow_evidence into one canonical person-level slot. |
| B | Corporate intermediary KYC pack | Corporate shareholder/intermediary | All risk levels where applicable | cert_inc/reg_dir/reg_sh/cert_gs/fin_stmt | Conditional | Mixed | Depends on document type. |


## Enhanced Requirements (EDD) — Target Rows

| Trigger | Requirement Key | Label | Type | Mandatory? | Blocking? | Active/Status | Canonical doc_type | Agent 1? | Show in Portal? | Portal Section |
|---|---|---|---|---|---|---|---|---|---|---|
| HIGH / VERY HIGH client (with existing bank account) | company_bank_reference | Company bank reference letter | Document | Yes (conditional) | No | Active mandatory — conditional on existing bank account | bankref | Yes | Yes, if requested/required | C — Enhanced Evidence Documents |
| HIGH / VERY HIGH client | company_sof_evidence | Company Source of Funds evidence | Document | Yes | No by default | Active mandatory | source_funds | Yes | Yes, if requested/required | C — Enhanced Evidence Documents |
| PEP / declared PEP | pep_declaration_details | Additional declaration details | Declaration | Yes | Yes | Active mandatory | None | No | Yes, if requested/required | E — Portal Disclosures |
| PEP / adverse media context | pep_adverse_media_assessment | Adverse media assessment | Internal task | No | No | Active back-office | Internal | No | No | Not portal-visible |
| PEP monitoring | pep_enhanced_monitoring_flag | Enhanced monitoring flag | Internal task | No | No | Active back-office | Internal | No | No | Not portal-visible |
| Crypto / VASP | aml_cft_policy | AML/CFT policy document | Document | No | No | Active advisory | aml_policy | Yes | Yes, if requested/required | C — Enhanced Evidence Documents |
| Trust / nominee / foundation | trust_nominee_foundation_documents | Trust / nominee / foundation documents | Document | Yes | Yes | Active mandatory | trust_deed | No - manual review | Yes, if requested/required | C — Enhanced Evidence Documents |
| High-risk jurisdiction | jurisdiction_sof_evidence | Source of funds evidence for activity in the higher-risk jurisdiction | Document | Yes | Yes if active | Inactive in some settings | source_funds | Yes if active | No unless activated/requested | C — Enhanced Evidence Documents |
| High-risk jurisdiction (country of incorporation) | jurisdiction_exposure_rationale | Jurisdiction Exposure Rationale | Explanation | Yes | Configurable | Active mandatory (conditional on country of incorporation) | None (portal disclosure) | No | Yes | E — Portal Disclosures |
| High-risk jurisdiction | jurisdiction_risk_assessment | Jurisdiction risk assessment | Internal task | Yes | Yes | Active back-office | Internal | No | No | Not portal-visible |
| High transaction volume | contracts_invoices | Contracts / invoices | Document | Yes | Yes | Active mandatory | contracts | Yes | Yes, if requested/required | C — Enhanced Evidence Documents |
| High transaction volume | expected_transaction_flow_evidence | Expected transaction flow evidence | Document | Yes | Yes if active | Inactive by default | supporting_document | No - manual review | No unless activated/requested | C — Enhanced Evidence Documents |
| High transaction volume | major_counterparties_explanation | Major counterparties explanation | Explanation | Yes | Yes | Active mandatory | None | No | Yes, if requested/required | E — Portal Disclosures |
| High transaction volume | volume_rationale_vs_business_size | Volume rationale vs business size | Explanation | Yes | Yes | Active mandatory | None | No | Yes, if requested/required | E — Portal Disclosures |
| Source of Funds / Source of Wealth concern | sof_sow_concern_pack | SOF/SOW concern evidence pack | Mixed | TBD | TBD | Not fully aligned as trigger family | source_funds/source_wealth | Yes if mapped | TBD | C — Enhanced Evidence Documents |


## Section Mapping (portal visibility)

| Section | Portal Name | Back-office Name | Client-visible? | Notes |
|---|---|---|---|---|
| A | A — Corporate Entity Documents | A — Corporate Entity Documents | Yes | Static standard KYC; can also house non-duplicative company-level EDD documents if policy chooses. |
| B | B — Directors & UBO Identity Documents | B — Directors & UBO Identity Documents | Yes | Use B for UBO/director-specific enhanced evidence where the document is tied to a person. |
| C | C — Enhanced Evidence Documents | C — Enhanced Evidence Documents | Yes, with safe wording | Primary location for SOF, SOW, bank references, licences, contracts, bank statements, AML policy. |
| D | D — Other Documents | D — Other Documents | Yes | Avoid using D for mandatory EDD if a canonical section exists. |
| E | E — Portal Disclosures | E — Portal Disclosures | Yes, with safe wording | Use for business activity explanation, counterparties explanation, volume rationale, declaration details. |
| F | Not portal-visible | F — Internal Controls | No | Use for screening disposition, senior review, monitoring flags, internal assessments. |
| G | Not portal-visible | G — Verification History | No | Back-office only. Verification history and Agent 1 detail must NOT be exposed on the client portal. Should not expose internal Agent 1 detail to clients. |


## HIGH/VERY-HIGH Baseline Pack (applied to all high-risk clients by default)

Justifies the EDD deletions: any 'removed because default' item is covered here.

| Baseline Requirement | Level | Source/doc_type | Condition | Covers (deleted EDD rows) |
|---|---|---|---|---|
| Company Source of Funds evidence | Company | source_funds | All high/very-high | crypto_source_of_funds_evidence; jurisdiction generic SoF |
| Company structure / ownership chart | Company | structure_chart | All clients (Section A) | crypto ownership_structure_chart; opaque ownership_structure_chart |
| Ownership chain / enhanced UBO evidence | Company | supporting_document (manual) | All high/very-high | opaque ownership_chain_documents; enhanced_ubo_evidence |
| Regulatory Licence(s) | Company | licence | If licence answer = Yes (Section A) | crypto licence_or_registration_evidence; jurisdiction_licensing_regulatory_evidence |
| Company Bank Statements | Company | bank_statements | If existing bank account = Yes (Section A) | company_bank_statements_6m; high_volume_bank_statements |
| Source of Wealth evidence (per UBO/director) | Person (Section B) | source_wealth | All high/very-high or PEP person | material_ubo_sow_evidence; pep_sow_evidence |
| Bank Reference Letter (per UBO/director) | Person (Section B) | bankref | High-risk or PEP person | pep_bank_reference; Section B bank ref |
| Screening: disposition + senior review | Back-office | Internal (screening engine) | All screened cases — NON-WAIVABLE | screening_disposition; material_screening_senior_review; false_positive_rationale; adverse_media_pep_sanctions_assessment |