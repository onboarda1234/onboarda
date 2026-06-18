# PR-KYC-EDD-REQUIREMENTS-1A Removed-Key Reference Audit

## Command

```bash
rg -n "enhanced_business_activity_explanation|company_bank_statements_6m|material_ubo_sow_evidence|pep_role_position|pep_jurisdiction|pep_sow_evidence|pep_bank_reference|pep_linked_sof_evidence|crypto_source_of_funds_evidence|licence_or_registration_evidence|crypto_enhanced_monitoring_flag|crypto_regulatory_status_assessment|ownership_chain_documents|enhanced_ubo_evidence|jurisdiction_licensing_regulatory_evidence|high_volume_bank_statements|screening_disposition|false_positive_rationale|adverse_media_pep_sanctions_assessment|material_screening_senior_review|client_clarification_screening|manual_edd_pack|money_services_pack|regulated_financial_services_pack|cross_border_pack|high_risk_product_pack|ownership_structure_chart" arie-backend/enhanced_requirements.py arie-backend/document_policy_registry.py arie-backend/server.py arie-portal.html arie-backoffice.html
```

## Findings

No removed key remains in active default enhanced-requirement rows, active enhanced-requirement generation rules, active portal slots, or portal `DOC_TYPE_MAP` entries.

Remaining references are intentionally limited to:

- `LEGACY_ENHANCED_REQUIREMENT_DOCUMENT_POLICY_ALIASES` in `arie-backend/enhanced_requirements.py`, preserving historical/read-only document policy classification for already-generated application rows.
- `REMOVED_ACTIVE_ENHANCED_REQUIREMENT_KEYS` in `arie-backend/enhanced_requirements.py`, used only to deactivate legacy persisted settings rows without deleting historical records.
- Safe-copy fallbacks for legacy PEP rows in `arie-backend/enhanced_requirements.py`, used only if historical requested rows are rendered through the client-safe portal boundary.
- Screening disposition workflow functions in `arie-backend/server.py`, which remain active because screening workflow and approval gates are independent of Enhanced Requirement rows.
- `ownership_structure_chart` upload alias in `arie-backend/server.py`, which preserves the standard Section A company structure chart and is not an EDD duplicate row.
- Historical/back-office Agent 1 alias lists in `arie-backoffice.html` and `document_policy_registry.py`, preserving compatibility with existing uploaded document aliases.

## Ownership Structure Chart

No active v5 EDD `ownership_structure_chart` default row remains. The only active ownership chart surface is the standard Section A company structure chart.
