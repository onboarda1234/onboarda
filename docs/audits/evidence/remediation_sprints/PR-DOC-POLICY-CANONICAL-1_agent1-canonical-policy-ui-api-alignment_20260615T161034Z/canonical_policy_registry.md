# Canonical Policy Registry

Registry version: `DOC-POLICY-CANONICAL-v1`

Runtime payload evidence:

- `runtime_json/document_policy_payload.json`
- `runtime_json/document_policy_summary.json`

Registry summary from local import/API payload builder:

| Metric | Value |
| --- | ---: |
| Total canonical policies | 38 |
| Active policies | 20 |
| Manual-review-only policies | 17 |
| Future/enterprise policies | 1 |
| Backend executable policies | 20 |
| Canonical executable check instances | 88 |
| Rule check instances | 45 |
| Hybrid check instances | 35 |
| AI check instances | 8 |
| Manual check instances | 54 |
| Unknown check instances | 0 |
| Workflow stages covered | 13 |
| Policies that block decisions | 22 |
| Unknown documents require review | true |
| SAR/STR active | false |

Active runtime-backed policies:

`cert_inc`, `memarts`, `reg_dir`, `reg_sh`, `structure_chart`, `board_res`, `poa`, `fin_stmt`, `licence`, `contracts`, `aml_policy`, `passport`, `national_id`, `poa_person`, `cv`, `bankref`, `pep_declaration`, `source_wealth`, `source_funds`, `bank_statements`.

Manual-review-only policies:

`cert_reg`, `ubo_declaration`, `cert_gs`, `trust_deed`, `tax_return`, `payslip`, `investment_income`, `sale_agreement`, `inheritance_evidence`, `loan_agreement`, `adverse_media_response`, `senior_approval_evidence`, `periodic_review_attestation`, `certificate_name_change`, `monitoring_support_evidence`, `regulatory_intelligence`, `supporting_document`.

Future/enterprise:

`sar_str_support`.

Important distinction: active means backend runtime verified and method-classified. Manual-review-only means accepted/reviewable evidence but not presented as automatically verified. Future/enterprise means inactive for pilot scope.

