# Enhanced Requirements Inventory

Source inspected: `arie-backend/enhanced_requirements.py`

Document-type reconciliation now uses `ENHANCED_REQUIREMENT_DOCUMENT_POLICY_MAP` and `enhanced_requirement_document_policy()`.

| Requirement key | UI label | Canonical document type | Canonical policy | Pilot classification | Active by default |
|---|---|---|---|---|---|
| `company_bank_reference` | Company bank reference letter | `bankref` | `DOC-EVIDENCE-BANK-REFERENCE-v1` | Active runtime verified | Yes |
| `company_bank_statements_6m` | 6 months company bank statements where available | `bank_statements` | `DOC-EDD-BANK-STATEMENTS-v1` | Active runtime verified | No |
| `company_sof_evidence` | Company Source of Funds evidence | `source_funds` | `DOC-EDD-SOF-v1` | Active runtime verified | Yes |
| `material_ubo_sow_evidence` | UBO Source of Wealth evidence for material UBOs/controllers | `source_wealth` | `DOC-EDD-SOW-v1` | Active runtime verified | Yes |
| `pep_sow_evidence` | Source of Wealth evidence | `source_wealth` | `DOC-EDD-SOW-v1` | Active runtime verified | Yes |
| `pep_bank_reference` | Bank reference letter | `bankref` | `DOC-EVIDENCE-BANK-REFERENCE-v1` | Active runtime verified | Yes |
| `pep_linked_sof_evidence` | Source of Funds evidence where funds are linked to PEP | `source_funds` | `DOC-EDD-SOF-v1` | Active runtime verified | Yes |
| `aml_cft_policy` | AML/CFT policy | `aml_policy` | `DOC-ENTITY-AML-POLICY-v1` | Active runtime verified | Yes |
| `licence_or_registration_evidence` | Licence/registration evidence or confirmation of unlicensed status | `licence` | `DOC-ENTITY-LICENCE-v1` | Active runtime verified | Yes |
| `crypto_source_of_funds_evidence` | Source of Funds evidence | `source_funds` | `DOC-EDD-SOF-v1` | Active runtime verified | Yes |
| `ownership_structure_chart` | Ownership structure chart | `structure_chart` | `DOC-ENTITY-OWNERSHIP-CHART-v1` | Active runtime verified | No |
| `ownership_chain_documents` | Full ownership-chain documents | `supporting_document` | `DOC-UNKNOWN-UNCLASSIFIED-v1` | Manual review only | Yes |
| `enhanced_ubo_evidence` | Enhanced UBO evidence | `supporting_document` | `DOC-UNKNOWN-UNCLASSIFIED-v1` | Manual review only | Yes |
| `trust_nominee_foundation_documents` | Trust/nominee/foundation documents where applicable | `trust_deed` | `DOC-ENTITY-TRUST-DEED-v1` | Manual review only | Yes |
| `jurisdiction_sof_evidence` | Source of Funds evidence | `source_funds` | `DOC-EDD-SOF-v1` | Active runtime verified | No |
| `jurisdiction_licensing_regulatory_evidence` | Licensing/regulatory evidence where relevant | `licence` | `DOC-ENTITY-LICENCE-v1` | Active runtime verified | Yes |
| `contracts_invoices` | Contracts/invoices | `contracts` | `DOC-ENTITY-CONTRACTS-v1` | Active runtime verified | Yes |
| `expected_transaction_flow_evidence` | Expected transaction flow evidence | `supporting_document` | `DOC-UNKNOWN-UNCLASSIFIED-v1` | Manual review only | No |
| `high_volume_bank_statements` | Company bank statements where available | `bank_statements` | `DOC-EDD-BANK-STATEMENTS-v1` | Active runtime verified | No |

## Not Currently Requestable As Standalone Default Enhanced Requirements

The following EDD families are present in canonical policy/settings context as manual-review-only or future scope where applicable, but are not currently standalone default Enhanced Requirement document request keys:

- Tax return
- Payslip / employment income proof
- Dividend / investment income proof
- Sale agreement
- Inheritance evidence
- Loan agreement
- Adverse media response
- Senior management approval evidence
- EDD memo support

These should remain manual-review-only or future unless a later PR adds explicit request rules, runtime checks, and tests.
