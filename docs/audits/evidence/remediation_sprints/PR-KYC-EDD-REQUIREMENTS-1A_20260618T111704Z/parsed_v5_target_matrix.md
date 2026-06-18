# Parsed KYC/EDD Requirements Matrix v5 Target

- Source: `docs/compliance/kyc-edd-matrix-v5.md`
- Parsed at: `20260618T111704Z`
- Required tables parsed: `4`

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

<details><summary>Normalized rows</summary>

```json
[
  {
    "Section": "A",
    "Document": "Certificate of Incorporation",
    "Applies To": "All companies",
    "Risk Scope": "All risk levels",
    "doc_type": "cert_inc",
    "Required?": "Required",
    "Agent 1?": "Yes",
    "Notes/Display": ""
  },
  {
    "Section": "A",
    "Document": "Memorandum / Articles",
    "Applies To": "All companies",
    "Risk Scope": "All risk levels",
    "doc_type": "memarts",
    "Required?": "Required",
    "Agent 1?": "Yes",
    "Notes/Display": ""
  },
  {
    "Section": "A",
    "Document": "Shareholder Register",
    "Applies To": "All companies",
    "Risk Scope": "All risk levels",
    "doc_type": "reg_sh",
    "Required?": "Required",
    "Agent 1?": "Yes",
    "Notes/Display": "RMI replacement must satisfy entity:reg_sh."
  },
  {
    "Section": "A",
    "Document": "Register of Directors",
    "Applies To": "All companies",
    "Risk Scope": "All risk levels",
    "doc_type": "reg_dir",
    "Required?": "Required",
    "Agent 1?": "Yes",
    "Notes/Display": ""
  },
  {
    "Section": "A",
    "Document": "Financial Statements / Management Accounts",
    "Applies To": "All companies",
    "Risk Scope": "All risk levels",
    "doc_type": "fin_stmt",
    "Required?": "Required",
    "Agent 1?": "Yes",
    "Notes/Display": ""
  },
  {
    "Section": "A",
    "Document": "Proof of Registered Address",
    "Applies To": "All companies",
    "Risk Scope": "All risk levels",
    "doc_type": "poa",
    "Required?": "Required",
    "Agent 1?": "Yes",
    "Notes/Display": ""
  },
  {
    "Section": "A",
    "Document": "Board Resolution",
    "Applies To": "All companies",
    "Risk Scope": "All risk levels",
    "doc_type": "board_res",
    "Required?": "Required",
    "Agent 1?": "Yes",
    "Notes/Display": ""
  },
  {
    "Section": "A",
    "Document": "Company Structure Chart",
    "Applies To": "All companies",
    "Risk Scope": "All risk levels",
    "doc_type": "structure_chart",
    "Required?": "Required",
    "Agent 1?": "Yes",
    "Notes/Display": "Also overlaps with opaque ownership EDD trigger; avoid duplicate display."
  },
  {
    "Section": "A",
    "Document": "Company Bank Statements",
    "Applies To": "Companies with existing bank account",
    "Risk Scope": "All risk levels where applicable",
    "doc_type": "bank_statements",
    "Required?": "Conditional",
    "Agent 1?": "Yes",
    "Notes/Display": "Also relevant to EDD/high-volume if activated."
  },
  {
    "Section": "A",
    "Document": "Regulatory Licence(s)",
    "Applies To": "Licensed or regulated entity",
    "Risk Scope": "All risk levels where applicable",
    "doc_type": "licence",
    "Required?": "Conditional",
    "Agent 1?": "Yes",
    "Notes/Display": ""
  },
  {
    "Section": "B",
    "Document": "Passport / Government ID",
    "Applies To": "Each director, UBO, individual intermediary",
    "Risk Scope": "All risk levels",
    "doc_type": "passport",
    "Required?": "Required per person",
    "Agent 1?": "Yes",
    "Notes/Display": ""
  },
  {
    "Section": "B",
    "Document": "Personal Proof of Address",
    "Applies To": "Each director, UBO, individual intermediary",
    "Risk Scope": "All risk levels",
    "doc_type": "poa",
    "Required?": "Required per person",
    "Agent 1?": "Yes",
    "Notes/Display": ""
  },
  {
    "Section": "B",
    "Document": "CV / LinkedIn Profile",
    "Applies To": "Each director, UBO, individual intermediary",
    "Risk Scope": "All risk levels",
    "doc_type": "cv",
    "Required?": "Required per person",
    "Agent 1?": "Yes",
    "Notes/Display": ""
  },
  {
    "Section": "B",
    "Document": "Bank Reference Letter",
    "Applies To": "Each director, UBO, individual intermediary",
    "Risk Scope": "HIGH / VERY HIGH risk, or director/UBO who is a PEP",
    "doc_type": "bankref",
    "Required?": "Conditional per person",
    "Agent 1?": "Yes",
    "Notes/Display": "Only required when the application is HIGH/VERY HIGH risk, or the specific director/UBO is a PEP. Not required for standard/low-risk persons. Carries the former EDD pep_bank_reference requirement (now consolidated here)."
  },
  {
    "Section": "B",
    "Document": "Source of Wealth evidence",
    "Applies To": "Specific UBO/director (per person)",
    "Risk Scope": "HIGH / VERY HIGH risk, or UBO/director who is a PEP",
    "doc_type": "source_wealth",
    "Required?": "Conditional per person",
    "Agent 1?": "Yes",
    "Notes/Display": "Person-level Source of Wealth, attached to the specific UBO/director. Consolidates the former EDD requirements material_ubo_sow_evidence and pep_sow_evidence into one canonical person-level slot."
  },
  {
    "Section": "B",
    "Document": "Corporate intermediary KYC pack",
    "Applies To": "Corporate shareholder/intermediary",
    "Risk Scope": "All risk levels where applicable",
    "doc_type": "cert_inc/reg_dir/reg_sh/cert_gs/fin_stmt",
    "Required?": "Conditional",
    "Agent 1?": "Mixed",
    "Notes/Display": "Depends on document type."
  }
]
```

</details>

## Enhanced Requirements — Target Rows

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
| High transaction volume | contracts_invoices | Contracts / invoices | Document | Yes | No | Active mandatory | contracts | Yes | Yes, if requested/required | C — Enhanced Evidence Documents |
| High transaction volume | expected_transaction_flow_evidence | Expected transaction flow evidence | Document | Yes | Yes if active | Inactive by default | supporting_document | No - manual review | No unless activated/requested | C — Enhanced Evidence Documents |
| High transaction volume | major_counterparties_explanation | Major counterparties explanation | Explanation | Yes | No | Active mandatory | None | No | Yes, if requested/required | E — Portal Disclosures |
| High transaction volume | volume_rationale_vs_business_size | Volume rationale vs business size | Explanation | Yes | Yes | Active mandatory | None | No | Yes, if requested/required | E — Portal Disclosures |

<details><summary>Normalized rows</summary>

```json
[
  {
    "Trigger": "HIGH / VERY HIGH client (with existing bank account)",
    "Requirement Key": "company_bank_reference",
    "Label": "Company bank reference letter",
    "Type": "Document",
    "Mandatory?": "Yes (conditional)",
    "Blocking?": "No",
    "Active/Status": "Active mandatory — conditional on existing bank account",
    "Canonical doc_type": "bankref",
    "Agent 1?": "Yes",
    "Show in Portal?": "Yes, if requested/required",
    "Portal Section": "C — Enhanced Evidence Documents"
  },
  {
    "Trigger": "HIGH / VERY HIGH client",
    "Requirement Key": "company_sof_evidence",
    "Label": "Company Source of Funds evidence",
    "Type": "Document",
    "Mandatory?": "Yes",
    "Blocking?": "No by default",
    "Active/Status": "Active mandatory",
    "Canonical doc_type": "source_funds",
    "Agent 1?": "Yes",
    "Show in Portal?": "Yes, if requested/required",
    "Portal Section": "C — Enhanced Evidence Documents"
  },
  {
    "Trigger": "PEP / declared PEP",
    "Requirement Key": "pep_declaration_details",
    "Label": "Additional declaration details",
    "Type": "Declaration",
    "Mandatory?": "Yes",
    "Blocking?": "Yes",
    "Active/Status": "Active mandatory",
    "Canonical doc_type": "None",
    "Agent 1?": "No",
    "Show in Portal?": "Yes, if requested/required",
    "Portal Section": "E — Portal Disclosures"
  },
  {
    "Trigger": "PEP / adverse media context",
    "Requirement Key": "pep_adverse_media_assessment",
    "Label": "Adverse media assessment",
    "Type": "Internal task",
    "Mandatory?": "No",
    "Blocking?": "No",
    "Active/Status": "Active back-office",
    "Canonical doc_type": "Internal",
    "Agent 1?": "No",
    "Show in Portal?": "No",
    "Portal Section": "Not portal-visible"
  },
  {
    "Trigger": "PEP monitoring",
    "Requirement Key": "pep_enhanced_monitoring_flag",
    "Label": "Enhanced monitoring flag",
    "Type": "Internal task",
    "Mandatory?": "No",
    "Blocking?": "No",
    "Active/Status": "Active back-office",
    "Canonical doc_type": "Internal",
    "Agent 1?": "No",
    "Show in Portal?": "No",
    "Portal Section": "Not portal-visible"
  },
  {
    "Trigger": "Crypto / VASP",
    "Requirement Key": "aml_cft_policy",
    "Label": "AML/CFT policy document",
    "Type": "Document",
    "Mandatory?": "No",
    "Blocking?": "No",
    "Active/Status": "Active advisory",
    "Canonical doc_type": "aml_policy",
    "Agent 1?": "Yes",
    "Show in Portal?": "Yes, if requested/required",
    "Portal Section": "C — Enhanced Evidence Documents"
  },
  {
    "Trigger": "Trust / nominee / foundation",
    "Requirement Key": "trust_nominee_foundation_documents",
    "Label": "Trust / nominee / foundation documents",
    "Type": "Document",
    "Mandatory?": "Yes",
    "Blocking?": "Yes",
    "Active/Status": "Active mandatory",
    "Canonical doc_type": "trust_deed",
    "Agent 1?": "No - manual review",
    "Show in Portal?": "Yes, if requested/required",
    "Portal Section": "C — Enhanced Evidence Documents"
  },
  {
    "Trigger": "High-risk jurisdiction",
    "Requirement Key": "jurisdiction_sof_evidence",
    "Label": "Source of funds evidence for activity in the higher-risk jurisdiction",
    "Type": "Document",
    "Mandatory?": "Yes",
    "Blocking?": "Yes if active",
    "Active/Status": "Inactive in some settings",
    "Canonical doc_type": "source_funds",
    "Agent 1?": "Yes if active",
    "Show in Portal?": "No unless activated/requested",
    "Portal Section": "C — Enhanced Evidence Documents"
  },
  {
    "Trigger": "High-risk jurisdiction (country of incorporation)",
    "Requirement Key": "jurisdiction_exposure_rationale",
    "Label": "Jurisdiction Exposure Rationale",
    "Type": "Explanation",
    "Mandatory?": "Yes",
    "Blocking?": "Configurable",
    "Active/Status": "Active mandatory (conditional on country of incorporation)",
    "Canonical doc_type": "None (portal disclosure)",
    "Agent 1?": "No",
    "Show in Portal?": "Yes",
    "Portal Section": "E — Portal Disclosures"
  },
  {
    "Trigger": "High-risk jurisdiction",
    "Requirement Key": "jurisdiction_risk_assessment",
    "Label": "Jurisdiction risk assessment",
    "Type": "Internal task",
    "Mandatory?": "Yes",
    "Blocking?": "Yes",
    "Active/Status": "Active back-office",
    "Canonical doc_type": "Internal",
    "Agent 1?": "No",
    "Show in Portal?": "No",
    "Portal Section": "Not portal-visible"
  },
  {
    "Trigger": "High transaction volume",
    "Requirement Key": "contracts_invoices",
    "Label": "Contracts / invoices",
    "Type": "Document",
    "Mandatory?": "Yes",
    "Blocking?": "No",
    "Active/Status": "Active mandatory",
    "Canonical doc_type": "contracts",
    "Agent 1?": "Yes",
    "Show in Portal?": "Yes, if requested/required",
    "Portal Section": "C — Enhanced Evidence Documents"
  },
  {
    "Trigger": "High transaction volume",
    "Requirement Key": "expected_transaction_flow_evidence",
    "Label": "Expected transaction flow evidence",
    "Type": "Document",
    "Mandatory?": "Yes",
    "Blocking?": "Yes if active",
    "Active/Status": "Inactive by default",
    "Canonical doc_type": "supporting_document",
    "Agent 1?": "No - manual review",
    "Show in Portal?": "No unless activated/requested",
    "Portal Section": "C — Enhanced Evidence Documents"
  },
  {
    "Trigger": "High transaction volume",
    "Requirement Key": "major_counterparties_explanation",
    "Label": "Major counterparties explanation",
    "Type": "Explanation",
    "Mandatory?": "Yes",
    "Blocking?": "No",
    "Active/Status": "Active mandatory",
    "Canonical doc_type": "None",
    "Agent 1?": "No",
    "Show in Portal?": "Yes, if requested/required",
    "Portal Section": "E — Portal Disclosures"
  },
  {
    "Trigger": "High transaction volume",
    "Requirement Key": "volume_rationale_vs_business_size",
    "Label": "Volume rationale vs business size",
    "Type": "Explanation",
    "Mandatory?": "Yes",
    "Blocking?": "Yes",
    "Active/Status": "Active mandatory",
    "Canonical doc_type": "None",
    "Agent 1?": "No",
    "Show in Portal?": "Yes, if requested/required",
    "Portal Section": "E — Portal Disclosures"
  }
]
```

</details>

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

<details><summary>Normalized rows</summary>

```json
[
  {
    "Section": "A",
    "Portal Name": "A — Corporate Entity Documents",
    "Back-office Name": "A — Corporate Entity Documents",
    "Client-visible?": "Yes",
    "Notes": "Static standard KYC; can also house non-duplicative company-level EDD documents if policy chooses."
  },
  {
    "Section": "B",
    "Portal Name": "B — Directors & UBO Identity Documents",
    "Back-office Name": "B — Directors & UBO Identity Documents",
    "Client-visible?": "Yes",
    "Notes": "Use B for UBO/director-specific enhanced evidence where the document is tied to a person."
  },
  {
    "Section": "C",
    "Portal Name": "C — Enhanced Evidence Documents",
    "Back-office Name": "C — Enhanced Evidence Documents",
    "Client-visible?": "Yes, with safe wording",
    "Notes": "Primary location for SOF, SOW, bank references, licences, contracts, bank statements, AML policy."
  },
  {
    "Section": "D",
    "Portal Name": "D — Other Documents",
    "Back-office Name": "D — Other Documents",
    "Client-visible?": "Yes",
    "Notes": "Avoid using D for mandatory EDD if a canonical section exists."
  },
  {
    "Section": "E",
    "Portal Name": "E — Portal Disclosures",
    "Back-office Name": "E — Portal Disclosures",
    "Client-visible?": "Yes, with safe wording",
    "Notes": "Use for business activity explanation, counterparties explanation, volume rationale, declaration details."
  },
  {
    "Section": "F",
    "Portal Name": "Not portal-visible",
    "Back-office Name": "F — Internal Controls",
    "Client-visible?": "No",
    "Notes": "Use for screening disposition, senior review, monitoring flags, internal assessments."
  },
  {
    "Section": "G",
    "Portal Name": "Not portal-visible",
    "Back-office Name": "G — Verification History",
    "Client-visible?": "No",
    "Notes": "Back-office only. Verification history and Agent 1 detail must NOT be exposed on the client portal. Should not expose internal Agent 1 detail to clients."
  }
]
```

</details>

## HIGH/VERY-HIGH Baseline Pack

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

<details><summary>Normalized rows</summary>

```json
[
  {
    "Baseline Requirement": "Company Source of Funds evidence",
    "Level": "Company",
    "Source/doc_type": "source_funds",
    "Condition": "All high/very-high",
    "Covers (deleted EDD rows)": "crypto_source_of_funds_evidence; jurisdiction generic SoF"
  },
  {
    "Baseline Requirement": "Company structure / ownership chart",
    "Level": "Company",
    "Source/doc_type": "structure_chart",
    "Condition": "All clients (Section A)",
    "Covers (deleted EDD rows)": "crypto ownership_structure_chart; opaque ownership_structure_chart"
  },
  {
    "Baseline Requirement": "Ownership chain / enhanced UBO evidence",
    "Level": "Company",
    "Source/doc_type": "supporting_document (manual)",
    "Condition": "All high/very-high",
    "Covers (deleted EDD rows)": "opaque ownership_chain_documents; enhanced_ubo_evidence"
  },
  {
    "Baseline Requirement": "Regulatory Licence(s)",
    "Level": "Company",
    "Source/doc_type": "licence",
    "Condition": "If licence answer = Yes (Section A)",
    "Covers (deleted EDD rows)": "crypto licence_or_registration_evidence; jurisdiction_licensing_regulatory_evidence"
  },
  {
    "Baseline Requirement": "Company Bank Statements",
    "Level": "Company",
    "Source/doc_type": "bank_statements",
    "Condition": "If existing bank account = Yes (Section A)",
    "Covers (deleted EDD rows)": "company_bank_statements_6m; high_volume_bank_statements"
  },
  {
    "Baseline Requirement": "Source of Wealth evidence (per UBO/director)",
    "Level": "Person (Section B)",
    "Source/doc_type": "source_wealth",
    "Condition": "All high/very-high or PEP person",
    "Covers (deleted EDD rows)": "material_ubo_sow_evidence; pep_sow_evidence"
  },
  {
    "Baseline Requirement": "Bank Reference Letter (per UBO/director)",
    "Level": "Person (Section B)",
    "Source/doc_type": "bankref",
    "Condition": "High-risk or PEP person",
    "Covers (deleted EDD rows)": "pep_bank_reference; Section B bank ref"
  },
  {
    "Baseline Requirement": "Screening: disposition + senior review",
    "Level": "Back-office",
    "Source/doc_type": "Internal (screening engine)",
    "Condition": "All screened cases — NON-WAIVABLE",
    "Covers (deleted EDD rows)": "screening_disposition; material_screening_senior_review; false_positive_rationale; adverse_media_pep_sanctions_assessment"
  }
]
```

</details>

