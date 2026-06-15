"""Canonical Agent 1 document policy registry.

This module keeps document-type verification policy separate from workflow usage.
Agent 1 checks remain document-type based; workflows decide when those documents
are required, what they block, and what follow-up they trigger.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Tuple

from verification_matrix import ALL_DOC_CHECKS, CheckClassification


REGISTRY_VERSION = "DOC-POLICY-CANONICAL-v1"

STATUS_ACTIVE = "Active"
STATUS_MANUAL = "Manual review only"
STATUS_FUTURE = "Future / enterprise"


TECHNICAL_CHECKS = [
    {
        "id": "GATE-01",
        "label": "File format",
        "method": "Rule",
        "default_visibility": "exception",
        "audit_required": True,
    },
    {
        "id": "GATE-02",
        "label": "File size",
        "method": "Rule",
        "default_visibility": "exception",
        "audit_required": True,
    },
    {
        "id": "GATE-03",
        "label": "Duplicate detection",
        "method": "Rule",
        "default_visibility": "exception",
        "audit_required": True,
    },
    {
        "id": "AUDIT-HASH",
        "label": "Hash generation",
        "method": "Rule",
        "default_visibility": "audit_only",
        "audit_required": True,
    },
    {
        "id": "TECH-OCR",
        "label": "OCR confidence",
        "method": "Hybrid",
        "default_visibility": "exception",
        "audit_required": True,
    },
    {
        "id": "TECH-READABILITY",
        "label": "Clarity/readability",
        "method": "Hybrid",
        "default_visibility": "exception",
        "audit_required": True,
    },
    {
        "id": "TECH-COMPLETENESS",
        "label": "Crop/page completeness",
        "method": "Hybrid",
        "default_visibility": "exception",
        "audit_required": True,
    },
    {
        "id": "TECH-TAMPER",
        "label": "Redaction/tamper indicators",
        "method": "Hybrid",
        "default_visibility": "exception",
        "audit_required": True,
    },
]


AUDIT_EXPORT_REQUIREMENTS = [
    "verification status",
    "material check results",
    "technical check results",
    "check method",
    "verification timestamp",
    "evidence hash",
    "agent execution link when available",
    "manual acceptance reason when applicable",
]


SUPPLEMENTARY_CHECKS: Dict[str, List[Dict[str, Any]]] = {
    "contracts": [
        {"id": "DOC-36", "label": "Name Match", "classification": "rule", "type": "name"},
        {"id": "DOC-37", "label": "Relevance", "classification": "hybrid", "type": "content"},
        {"id": "DOC-38", "label": "Clarity", "classification": "rule", "type": "quality"},
    ],
    "aml_policy": [
        {"id": "DOC-39", "label": "Completeness", "classification": "hybrid", "type": "content"},
        {"id": "DOC-40", "label": "Date", "classification": "rule", "type": "age"},
        {"id": "DOC-41", "label": "Relevance", "classification": "hybrid", "type": "content"},
    ],
    "source_wealth": [
        {"id": "DOC-42", "label": "Consistency", "classification": "hybrid", "type": "content"},
        {"id": "DOC-43", "label": "Clarity", "classification": "rule", "type": "quality"},
    ],
    "source_funds": [
        {"id": "DOC-44", "label": "Consistency", "classification": "hybrid", "type": "content"},
        {"id": "DOC-45", "label": "Clarity", "classification": "rule", "type": "quality"},
    ],
    "bank_statements": [
        {"id": "DOC-46", "label": "Period", "classification": "rule", "type": "age"},
        {"id": "DOC-47", "label": "Name Match", "classification": "rule", "type": "name"},
        {"id": "DOC-74", "label": "Completeness", "classification": "rule", "type": "quality"},
    ],
}


POLICY_DEFINITIONS = [
    {
        "key": "cert_inc",
        "label": "Certificate of Incorporation",
        "category": "entity",
        "policy_id": "DOC-ENTITY-COI-v1",
        "aliases": ["coi", "certificate_of_incorporation", "incorporation_certificate"],
        "sources": [("matrix", "cert_inc", "entity")],
        "manual_acceptance_allowed": True,
        "gate_behaviour": ["Blocks KYC submission, memo generation, and approval when required and failed/pending/stale."],
    },
    {
        "key": "cert_reg",
        "label": "Certificate of Registration / Business registration",
        "category": "entity",
        "policy_id": "DOC-ENTITY-REGISTRATION-v1",
        "aliases": ["business_registration", "business_registration_certificate", "certificate_of_registration"],
        "status": STATUS_MANUAL,
        "sources": [],
        "material_checks": ["issuer/authority review", "entity name match", "registration number/date review"],
        "gate_behaviour": ["Manual review only; not presented as runtime verified."],
    },
    {
        "key": "memarts",
        "label": "Memorandum of Association",
        "category": "entity",
        "policy_id": "DOC-ENTITY-MEMARTS-v1",
        "aliases": ["memorandum_and_articles", "memorandum_articles", "articles_of_association"],
        "sources": [("matrix", "memarts", "entity")],
        "gate_behaviour": ["Blocks KYC submission, memo generation, and approval when required and failed/pending/stale."],
    },
    {
        "key": "reg_dir",
        "label": "Register of Directors",
        "category": "entity",
        "policy_id": "DOC-ENTITY-REGDIR-v1",
        "aliases": ["director_register", "register_of_directors"],
        "sources": [("matrix", "reg_dir", "entity")],
        "gate_behaviour": ["Blocks onboarding approval, director change completion, and periodic review completion when required."],
        "re_screening_trigger": True,
    },
    {
        "key": "reg_sh",
        "label": "Register of Shareholders",
        "category": "entity",
        "policy_id": "DOC-ENTITY-REGSH-v1",
        "aliases": ["shareholder_register", "register_of_shareholders", "register_of_members"],
        "sources": [("matrix", "reg_sh", "entity")],
        "gate_behaviour": ["Blocks onboarding approval, UBO/ownership change completion, and periodic review completion when required."],
        "re_screening_trigger": True,
        "risk_score_trigger": True,
    },
    {
        "key": "ubo_declaration",
        "label": "UBO Declaration",
        "category": "entity",
        "policy_id": "DOC-ENTITY-UBO-DECL-v1",
        "aliases": ["beneficial_owner_declaration"],
        "status": STATUS_MANUAL,
        "sources": [],
        "material_checks": ["declared UBO match", "ownership percentage review", "natural-person trace"],
        "gate_behaviour": ["Manual review only unless mapped to verified shareholder register/ownership chart evidence."],
        "re_screening_trigger": True,
    },
    {
        "key": "structure_chart",
        "label": "Ownership chart / structure chart",
        "category": "entity",
        "policy_id": "DOC-ENTITY-OWNERSHIP-CHART-v1",
        "aliases": ["ownership_chart", "company_structure_chart"],
        "sources": [("matrix", "structure_chart", "entity")],
        "gate_behaviour": ["Blocks ownership reliance when required and failed/pending/stale."],
        "risk_score_trigger": True,
    },
    {
        "key": "board_res",
        "label": "Board resolution / authorised signatory resolution",
        "category": "entity",
        "policy_id": "DOC-ENTITY-BOARD-RES-v1",
        "aliases": ["board_resolution", "signatory_resolution", "authorised_signatory_resolution"],
        "sources": [("matrix", "board_res", "entity")],
        "gate_behaviour": ["Blocks signatory/authority reliance when required and failed/pending/stale."],
    },
    {
        "key": "poa",
        "label": "Proof of registered address",
        "category": "entity",
        "policy_id": "DOC-ENTITY-REGISTERED-ADDRESS-v1",
        "aliases": ["proof_of_registered_address", "registered_address_proof", "address_proof"],
        "sources": [("matrix", "poa", "entity")],
        "gate_behaviour": ["Blocks onboarding approval, address change completion, and periodic review completion when required."],
    },
    {
        "key": "fin_stmt",
        "label": "Financial statements / management accounts",
        "category": "entity",
        "policy_id": "DOC-ENTITY-FINANCIALS-v1",
        "aliases": ["financial_statements", "management_accounts"],
        "sources": [("matrix", "fin_stmt", "entity")],
        "gate_behaviour": ["Blocks reliance when required as business scale or periodic review evidence."],
        "risk_score_trigger": True,
    },
    {
        "key": "licence",
        "label": "Regulatory licence",
        "category": "entity",
        "policy_id": "DOC-ENTITY-LICENCE-v1",
        "aliases": ["business_licence", "regulatory_license", "license"],
        "sources": [("matrix", "licence", "entity")],
        "gate_behaviour": ["Blocks reliance where a regulated/licensed business activity is declared."],
    },
    {
        "key": "contracts",
        "label": "Contracts / invoices / business activity evidence",
        "category": "entity",
        "policy_id": "DOC-ENTITY-CONTRACTS-v1",
        "aliases": ["invoices", "business_activity_evidence"],
        "sources": [("supplementary", "contracts", "entity")],
        "gate_behaviour": ["Blocks automated reliance only when used in memo or approval reasoning."],
    },
    {
        "key": "aml_policy",
        "label": "AML/compliance policy",
        "category": "entity",
        "policy_id": "DOC-ENTITY-AML-POLICY-v1",
        "aliases": ["compliance_policy", "aml_cft_policy"],
        "sources": [("supplementary", "aml_policy", "entity")],
        "gate_behaviour": ["Blocks reliance when required for regulated/high-risk/intermediary review."],
    },
    {
        "key": "cert_gs",
        "label": "Certificate of Good Standing / Incumbency",
        "category": "entity",
        "policy_id": "DOC-ENTITY-GOOD-STANDING-v1",
        "aliases": ["certificate_good_standing", "incumbency_certificate"],
        "status": STATUS_MANUAL,
        "sources": [],
        "material_checks": ["issuer/authority review", "date/freshness", "entity continuity"],
        "gate_behaviour": ["Manual review only; accepted upload type but not runtime verified in pilot."],
    },
    {
        "key": "trust_deed",
        "label": "Trust deed / foundation charter / partnership agreement",
        "category": "entity",
        "policy_id": "DOC-ENTITY-TRUST-DEED-v1",
        "aliases": ["foundation_charter", "partnership_agreement"],
        "status": STATUS_MANUAL,
        "sources": [],
        "material_checks": ["legal form review", "party/beneficiary extraction", "control/person trace"],
        "gate_behaviour": ["Manual review only for pilot."],
    },
    {
        "key": "passport",
        "label": "Passport",
        "category": "person",
        "policy_id": "DOC-PERSON-PASSPORT-v1",
        "aliases": ["passport_copy"],
        "sources": [("matrix", "passport", "person")],
        "gate_behaviour": ["Blocks KYC, director/UBO changes, DOB/nationality correction, and passport replacement until verified or manually accepted."],
        "re_screening_trigger": True,
    },
    {
        "key": "national_id",
        "label": "National ID / government ID",
        "category": "person",
        "policy_id": "DOC-PERSON-NATIONAL-ID-v1",
        "aliases": ["id_card", "drivers_license", "director_id", "ubo_id", "national_identity_card"],
        "sources": [("matrix", "national_id", "person")],
        "gate_behaviour": ["Blocks identity reliance when required and failed/pending/stale."],
        "re_screening_trigger": True,
    },
    {
        "key": "poa_person",
        "label": "Proof of address",
        "category": "person",
        "policy_id": "DOC-PERSON-ADDRESS-v1",
        "aliases": ["personal_poa", "residential_address_proof"],
        "sources": [("matrix", "poa_person", "person")],
        "gate_behaviour": ["Blocks person address reliance when required and failed/pending/stale."],
    },
    {
        "key": "cv",
        "label": "CV / LinkedIn profile",
        "category": "person",
        "policy_id": "DOC-PERSON-CV-v1",
        "aliases": ["linkedin_profile", "curriculum_vitae"],
        "sources": [("matrix", "cv", "person")],
        "gate_behaviour": ["Blocks EDD/person background reliance when required and failed/pending/stale."],
    },
    {
        "key": "bankref",
        "label": "Bank reference",
        "category": "evidence",
        "policy_id": "DOC-EVIDENCE-BANK-REFERENCE-v1",
        "aliases": ["bank_reference", "pep_bank_reference", "edd_bank_reference"],
        "sources": [("matrix", "bankref", "entity"), ("matrix", "bankref_pep", "person")],
        "gate_behaviour": ["Blocks EDD/person/entity reliance when required and failed/pending/stale."],
    },
    {
        "key": "pep_declaration",
        "label": "PEP declaration support",
        "category": "person",
        "policy_id": "DOC-PERSON-PEP-SUPPORT-v1",
        "aliases": ["pep_support", "pep-declaration"],
        "sources": [("matrix", "pep_declaration", "person")],
        "gate_behaviour": ["Blocks EDD and approval reliance when required and failed/pending/stale."],
    },
    {
        "key": "source_wealth",
        "label": "Source of Wealth",
        "category": "edd",
        "policy_id": "DOC-EDD-SOW-v1",
        "aliases": ["sow", "source_of_wealth"],
        "sources": [("matrix", "sow", "person"), ("supplementary", "source_wealth", "entity")],
        "gate_behaviour": ["Blocks EDD closure only when active required SOW evidence is requested."],
    },
    {
        "key": "source_funds",
        "label": "Source of Funds",
        "category": "edd",
        "policy_id": "DOC-EDD-SOF-v1",
        "aliases": ["sof", "source_of_funds"],
        "sources": [("supplementary", "source_funds", "entity")],
        "gate_behaviour": ["Blocks EDD closure only when active required SOF evidence is requested."],
    },
    {
        "key": "bank_statements",
        "label": "Bank statements",
        "category": "edd",
        "policy_id": "DOC-EDD-BANK-STATEMENTS-v1",
        "aliases": ["bank_statement"],
        "sources": [("supplementary", "bank_statements", "entity")],
        "gate_behaviour": ["Blocks EDD closure only when active required bank statement evidence is requested."],
    },
    {
        "key": "tax_return",
        "label": "Tax return",
        "category": "edd",
        "policy_id": "DOC-EDD-TAX-RETURN-v1",
        "status": STATUS_MANUAL,
        "sources": [],
        "material_checks": ["party match", "tax year/date review", "income/source consistency"],
        "gate_behaviour": ["Manual review only in pilot."],
    },
    {
        "key": "payslip",
        "label": "Payslip / employment income proof",
        "category": "edd",
        "policy_id": "DOC-EDD-PAYSLIP-v1",
        "status": STATUS_MANUAL,
        "sources": [],
        "material_checks": ["party match", "employer match", "income period review"],
        "gate_behaviour": ["Manual review only in pilot."],
    },
    {
        "key": "investment_income",
        "label": "Dividend / investment income proof",
        "category": "edd",
        "policy_id": "DOC-EDD-INVESTMENT-INCOME-v1",
        "status": STATUS_MANUAL,
        "sources": [],
        "material_checks": ["issuer/investment match", "value/date consistency"],
        "gate_behaviour": ["Manual review only in pilot."],
    },
    {
        "key": "sale_agreement",
        "label": "Sale agreement",
        "category": "edd",
        "policy_id": "DOC-EDD-SALE-AGREEMENT-v1",
        "status": STATUS_MANUAL,
        "sources": [],
        "material_checks": ["buyer/seller match", "asset/value consistency", "date review"],
        "gate_behaviour": ["Manual review only in pilot."],
    },
    {
        "key": "inheritance_evidence",
        "label": "Inheritance evidence",
        "category": "edd",
        "policy_id": "DOC-EDD-INHERITANCE-v1",
        "status": STATUS_MANUAL,
        "sources": [],
        "material_checks": ["beneficiary match", "estate/source consistency", "date review"],
        "gate_behaviour": ["Manual review only in pilot."],
    },
    {
        "key": "loan_agreement",
        "label": "Loan agreement",
        "category": "edd",
        "policy_id": "DOC-EDD-LOAN-v1",
        "status": STATUS_MANUAL,
        "sources": [],
        "material_checks": ["lender/borrower match", "repayment/source plausibility"],
        "gate_behaviour": ["Manual review only in pilot."],
    },
    {
        "key": "adverse_media_response",
        "label": "Adverse media response",
        "category": "edd",
        "policy_id": "DOC-EDD-ADVERSE-MEDIA-RESPONSE-v1",
        "status": STATUS_MANUAL,
        "sources": [],
        "material_checks": ["allegation coverage", "response relevance", "supporting evidence consistency"],
        "gate_behaviour": ["Manual review only in pilot; adverse-media screening remains outside Agent 1."],
    },
    {
        "key": "senior_approval_evidence",
        "label": "Senior management approval evidence",
        "category": "edd",
        "policy_id": "DOC-EDD-SENIOR-APPROVAL-v1",
        "status": STATUS_MANUAL,
        "sources": [],
        "manual_acceptance_allowed": False,
        "material_checks": ["approver authority", "approval date", "scope of approval"],
        "gate_behaviour": ["Manual review only; cannot be auto-accepted by Agent 1."],
    },
    {
        "key": "periodic_review_attestation",
        "label": "Periodic review attestation / no-change confirmation",
        "category": "periodic",
        "policy_id": "DOC-PR-ATTESTATION-v1",
        "status": STATUS_MANUAL,
        "sources": [],
        "material_checks": ["attestation completeness", "signatory authority", "no-change scope"],
        "gate_behaviour": ["Manual review only unless current periodic-review runtime supports this evidence object."],
    },
    {
        "key": "certificate_name_change",
        "label": "Certificate of Name Change",
        "category": "change",
        "policy_id": "DOC-CHANGE-COMPANY-NAME-v1",
        "status": STATUS_MANUAL,
        "sources": [],
        "material_checks": ["old company name", "new company name", "registration number", "issuer/authority", "date", "entity continuity"],
        "gate_behaviour": ["Blocks company name change completion when required; manual review only in pilot."],
        "re_screening_trigger": True,
        "memo_staleness_trigger": True,
    },
    {
        "key": "monitoring_support_evidence",
        "label": "Monitoring alert support evidence",
        "category": "monitoring",
        "policy_id": "DOC-MON-SUPPORT-v1",
        "aliases": ["monitoring_alert_support", "transaction_support_evidence", "client_response_document"],
        "status": STATUS_MANUAL,
        "sources": [],
        "material_checks": ["alert/case match", "evidence relevance", "source/date capture", "investigation consistency"],
        "gate_behaviour": ["Manual review only in pilot; not presented as runtime verified."],
    },
    {
        "key": "sar_str_support",
        "label": "SAR/STR support document",
        "category": "monitoring",
        "policy_id": "DOC-MON-SAR-FUTURE-v1",
        "status": STATUS_FUTURE,
        "sources": [],
        "material_checks": ["future MLRO/SCO report evidence controls"],
        "gate_behaviour": ["Future / enterprise. SAR/STR implementation is not active in pilot scope."],
        "manual_acceptance_allowed": False,
    },
    {
        "key": "regulatory_intelligence",
        "label": "Regulatory intelligence source document",
        "category": "regulatory",
        "policy_id": "DOC-REG-INTELLIGENCE-v1",
        "aliases": ["regulatory_guidance", "laws_regulations", "compliance_resource_file", "source_document_used_in_memo"],
        "status": STATUS_MANUAL,
        "sources": [],
        "material_checks": ["source/date/version required", "jurisdiction relevance", "memo/reasoning citation match"],
        "gate_behaviour": ["Library-only by default. If relied on in a case, memo, policy, or decision, source/date/version review is required."],
    },
    {
        "key": "supporting_document",
        "label": "Unclassified / supporting document",
        "category": "evidence",
        "policy_id": "DOC-UNKNOWN-UNCLASSIFIED-v1",
        "aliases": ["general", "unknown", "unclassified"],
        "status": STATUS_MANUAL,
        "sources": [],
        "material_checks": ["officer classification required", "automated reliance blocked", "manual acceptance reason required"],
        "gate_behaviour": ["Unknown documents require review and are blocked from automated reliance until classified and verified or manually accepted with reason."],
    },
]


WORKFLOW_USAGE_MAPPINGS = [
    {
        "workflow": "onboarding",
        "label": "Onboarding / KYC",
        "trigger": "Client/officer KYC upload and KYC submission.",
        "required_documents": ["cert_inc", "cert_reg", "memarts", "reg_dir", "reg_sh", "ubo_declaration", "structure_chart", "poa", "passport", "national_id", "poa_person"],
        "optional_documents": ["board_res", "licence", "fin_stmt", "contracts", "aml_policy", "bankref"],
        "blockers": ["Blocks KYC submission", "Blocks memo generation", "Blocks approval"],
        "re_screening_triggers": ["new/changed director", "new/changed UBO", "name/DOB/nationality changes"],
        "risk_score_triggers": ["ownership complexity", "licence/regulatory activity", "financial/business activity evidence"],
        "memo_staleness_triggers": ["document replacement", "failed/stale/skipped verification"],
    },
    {
        "workflow": "director_change",
        "label": "Director change",
        "trigger": "Director appointment/removal/correction.",
        "required_documents": ["reg_dir", "passport", "national_id", "poa_person"],
        "optional_documents": [],
        "blockers": ["Blocks change completion"],
        "re_screening_triggers": ["new/changed director re-screening required"],
        "risk_score_triggers": [],
        "memo_staleness_triggers": ["director list changed"],
    },
    {
        "workflow": "ubo_change",
        "label": "UBO change",
        "trigger": "UBO addition/removal/correction.",
        "required_documents": ["reg_sh", "ubo_declaration", "passport", "national_id", "poa_person"],
        "optional_documents": ["structure_chart", "source_wealth"],
        "blockers": ["Blocks change completion"],
        "re_screening_triggers": ["new/changed UBO re-screening required"],
        "risk_score_triggers": ["ownership percentage validation", "natural-person trace"],
        "memo_staleness_triggers": ["UBO changed"],
    },
    {
        "workflow": "ownership_percentage_change",
        "label": "Ownership percentage change",
        "trigger": "Ownership percentage correction or material change.",
        "required_documents": ["reg_sh", "structure_chart"],
        "optional_documents": ["ubo_declaration"],
        "blockers": ["Blocks change completion"],
        "re_screening_triggers": ["material ownership change"],
        "risk_score_triggers": ["before/after comparison", "total ownership validation", "risk recalculation if material", "materiality threshold"],
        "memo_staleness_triggers": ["ownership changed"],
    },
    {
        "workflow": "address_change",
        "label": "Address change",
        "trigger": "Registered address correction/change.",
        "required_documents": ["poa", "cert_inc", "cert_reg"],
        "optional_documents": [],
        "blockers": ["Blocks change completion"],
        "re_screening_triggers": [],
        "risk_score_triggers": ["address/risk consistency refresh"],
        "memo_staleness_triggers": ["registered address changed"],
    },
    {
        "workflow": "company_name_change",
        "label": "Company name change",
        "trigger": "Entity legal name change.",
        "required_documents": ["certificate_name_change"],
        "optional_documents": [],
        "blockers": ["Blocks change completion"],
        "re_screening_triggers": ["entity re-screening required"],
        "risk_score_triggers": [],
        "memo_staleness_triggers": ["company name changed"],
    },
    {
        "workflow": "dob_correction",
        "label": "DOB correction",
        "trigger": "Date of birth correction.",
        "required_documents": ["passport"],
        "optional_documents": [],
        "blockers": ["Blocks change completion"],
        "re_screening_triggers": ["if identity continuity/risk changes"],
        "risk_score_triggers": [],
        "memo_staleness_triggers": ["person identity field changed"],
    },
    {
        "workflow": "nationality_correction",
        "label": "Nationality correction",
        "trigger": "Nationality correction.",
        "required_documents": ["passport"],
        "optional_documents": [],
        "blockers": ["Blocks change completion"],
        "re_screening_triggers": ["if nationality/risk changes"],
        "risk_score_triggers": ["country-risk refresh if nationality changes"],
        "memo_staleness_triggers": ["nationality changed"],
    },
    {
        "workflow": "passport_expiry",
        "label": "Passport expiry replacement",
        "trigger": "Expired or stale passport replacement.",
        "required_documents": ["passport"],
        "optional_documents": [],
        "blockers": ["Blocks reliance on superseded passport until replacement is verified or manually accepted"],
        "re_screening_triggers": [],
        "risk_score_triggers": [],
        "memo_staleness_triggers": ["passport replaced"],
    },
    {
        "workflow": "periodic_review",
        "label": "Periodic review",
        "trigger": "Periodic review case, refresh request, or stale/expired document replacement.",
        "required_documents": ["cert_inc", "reg_dir", "reg_sh", "poa", "passport", "national_id", "periodic_review_attestation"],
        "optional_documents": ["fin_stmt", "contracts", "source_wealth", "source_funds", "bank_statements"],
        "blockers": ["Blocks periodic review completion if required evidence is missing/pending/failed/stale/manual-review-required"],
        "re_screening_triggers": ["director/UBO/name/nationality changes"],
        "risk_score_triggers": ["financial/business activity refresh", "ownership/risk-relevant delta"],
        "memo_staleness_triggers": ["refreshed evidence changes compliance reasoning"],
    },
    {
        "workflow": "edd_basic",
        "label": "EDD basic pilot",
        "trigger": "PEP/high-risk/material UBO/enhanced due diligence requirement.",
        "required_documents": ["source_wealth", "source_funds", "bank_statements", "bankref"],
        "optional_documents": ["tax_return", "payslip", "investment_income", "sale_agreement", "inheritance_evidence", "loan_agreement", "adverse_media_response", "senior_approval_evidence"],
        "blockers": ["Blocks EDD closure only for active required documents"],
        "re_screening_triggers": [],
        "risk_score_triggers": ["source of wealth/source of funds plausibility affects EDD disposition"],
        "memo_staleness_triggers": ["EDD evidence replaced or failed"],
    },
    {
        "workflow": "monitoring",
        "label": "Monitoring evidence",
        "trigger": "Monitoring alert investigation support evidence.",
        "required_documents": [],
        "optional_documents": ["monitoring_support_evidence"],
        "blockers": ["Manual review only in pilot unless a runtime verified evidence type is used"],
        "re_screening_triggers": [],
        "risk_score_triggers": ["case-by-case officer risk refresh"],
        "memo_staleness_triggers": [],
    },
    {
        "workflow": "regulatory_source_evidence",
        "label": "Regulatory / source evidence",
        "trigger": "Resource or regulatory document is cited in a memo, policy, case, or decision.",
        "required_documents": [],
        "optional_documents": ["regulatory_intelligence"],
        "blockers": ["Library-only by default; source/date/version review required if relied upon"],
        "re_screening_triggers": [],
        "risk_score_triggers": [],
        "memo_staleness_triggers": ["cited source/version changes"],
    },
]


def _method_name(classification: str) -> str:
    mapping = {
        CheckClassification.RULE: "Rule",
        CheckClassification.HYBRID: "Hybrid",
        CheckClassification.AI: "AI",
        "manual": "Manual",
        "unknown": "Unknown",
    }
    return mapping.get(str(classification or "").lower(), "Unknown")


def _matrix_checks(matrix_key: str) -> List[Dict[str, Any]]:
    entry = ALL_DOC_CHECKS.get(matrix_key, {})
    checks = []
    for check in entry.get("checks", []):
        checks.append(
            {
                "id": check.get("id"),
                "label": check.get("label"),
                "method": _method_name(check.get("classification")),
                "classification": check.get("classification"),
                "source": "verification_matrix",
                "default_visibility": "material",
            }
        )
    return checks


def _supplementary_checks(doc_type: str) -> List[Dict[str, Any]]:
    checks = []
    for check in SUPPLEMENTARY_CHECKS.get(doc_type, []):
        checks.append(
            {
                "id": check.get("id"),
                "label": check.get("label"),
                "method": _method_name(check.get("classification")),
                "classification": check.get("classification"),
                "source": "ai_checks_seed",
                "default_visibility": "material",
            }
        )
    return checks


def _manual_checks(labels: Iterable[str]) -> List[Dict[str, Any]]:
    return [
        {
            "id": f"MANUAL-{index + 1:02d}",
            "label": label,
            "method": "Manual",
            "classification": "manual",
            "source": "policy_registry",
            "default_visibility": "material",
        }
        for index, label in enumerate(labels)
    ]


def _policy_checks(definition: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    checks: List[Dict[str, Any]] = []
    backend_sources: List[Dict[str, str]] = []
    for source_type, source_key, category in definition.get("sources", []):
        if source_type == "matrix":
            checks.extend(_matrix_checks(source_key))
            backend_sources.append(
                {
                    "source": "verification_matrix",
                    "matrix_key": source_key,
                    "runtime_doc_type": "poa" if source_key == "poa_person" else ("bankref" if source_key == "bankref_pep" else source_key),
                    "category": category,
                }
            )
        elif source_type == "supplementary":
            checks.extend(_supplementary_checks(source_key))
            backend_sources.append(
                {
                    "source": "ai_checks_seed",
                    "runtime_doc_type": source_key,
                    "category": category,
                }
            )
    if not checks:
        checks = _manual_checks(definition.get("material_checks", []))
    return checks, backend_sources


def _method_counts(checks: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"Rule": 0, "Hybrid": 0, "AI": 0, "Manual": 0, "Unknown": 0}
    for check in checks:
        method = check.get("method") or "Unknown"
        counts[method] = counts.get(method, 0) + 1
    return counts


def _category_label(category: str) -> str:
    return {
        "entity": "Entity Documents",
        "person": "Person / KYC Documents",
        "edd": "EDD Evidence",
        "change": "Change Management Evidence",
        "periodic": "Periodic Review Evidence",
        "monitoring": "Monitoring Evidence",
        "regulatory": "Regulatory / Resource Evidence",
        "evidence": "Supporting Evidence",
    }.get(category, category)


def _workflow_index() -> Dict[str, Dict[str, List[str]]]:
    index: Dict[str, Dict[str, List[str]]] = {}
    for mapping in WORKFLOW_USAGE_MAPPINGS:
        docs = set(mapping.get("required_documents", [])) | set(mapping.get("optional_documents", []))
        for doc_key in docs:
            item = index.setdefault(doc_key, {"used_in": [], "blocks": [], "triggers": []})
            item["used_in"].append(mapping["label"])
            item["blocks"].extend(mapping.get("blockers", []))
            item["triggers"].extend(mapping.get("re_screening_triggers", []))
            item["triggers"].extend(mapping.get("risk_score_triggers", []))
            item["triggers"].extend(mapping.get("memo_staleness_triggers", []))
    return index


def get_canonical_document_policies() -> List[Dict[str, Any]]:
    workflow_index = _workflow_index()
    policies = []
    for definition in POLICY_DEFINITIONS:
        status = definition.get("status", STATUS_ACTIVE)
        material_checks, backend_sources = _policy_checks(definition)
        backend_executable = status == STATUS_ACTIVE and bool(backend_sources) and bool(material_checks)
        workflow_data = workflow_index.get(definition["key"], {"used_in": [], "blocks": [], "triggers": []})
        gate_behaviour = definition.get("gate_behaviour") or workflow_data.get("blocks") or ["Advisory only"]
        policy = {
            "document_type": definition["key"],
            "canonical_key": definition["key"],
            "display_label": definition["label"],
            "family": definition["label"],
            "category": definition.get("category", "evidence"),
            "category_label": _category_label(definition.get("category", "evidence")),
            "stage": definition.get("category", "evidence"),
            "stageLabel": _category_label(definition.get("category", "evidence")),
            "aliases": sorted(set(definition.get("aliases", []))),
            "active_pilot_status": status,
            "status": status,
            "backend_executable": backend_executable,
            "runtime_verified": backend_executable,
            "policy_id": definition.get("policy_id"),
            "policyId": definition.get("policy_id"),
            "version": definition.get("version", "v1"),
            "material_checks": material_checks,
            "materialChecks": [check["label"] for check in material_checks],
            "technical_checks": deepcopy(TECHNICAL_CHECKS),
            "technicalChecks": [check["label"] for check in TECHNICAL_CHECKS],
            "check_method_counts": _method_counts(material_checks),
            "backend_sources": backend_sources,
            "default_visibility": "decision_first",
            "manual_acceptance_allowed": definition.get("manual_acceptance_allowed", True),
            "manualAcceptanceAllowed": definition.get("manual_acceptance_allowed", True),
            "manual_acceptance": (
                "Allowed with documented reason"
                if definition.get("manual_acceptance_allowed", True)
                else "Not allowed"
            ),
            "manualAcceptance": (
                "Allowed with documented reason"
                if definition.get("manual_acceptance_allowed", True)
                else "Not allowed"
            ),
            "audit_export_requirements": deepcopy(AUDIT_EXPORT_REQUIREMENTS),
            "used_in": sorted(set(workflow_data.get("used_in", []))),
            "usedIn": sorted(set(workflow_data.get("used_in", []))),
            "blocks": sorted(set(gate_behaviour)),
            "gate_behaviour": gate_behaviour,
            "gateBehavior": "; ".join(gate_behaviour),
            "gateType": "blocking" if any("Block" in item or "block" in item for item in gate_behaviour) else "review",
            "triggers": sorted(set(item for item in workflow_data.get("triggers", []) if item)),
            "re_screening_trigger": bool(definition.get("re_screening_trigger")) or any("screen" in item.lower() for item in workflow_data.get("triggers", [])),
            "risk_score_trigger": bool(definition.get("risk_score_trigger")) or any("risk" in item.lower() for item in workflow_data.get("triggers", [])),
            "memo_staleness_trigger": bool(definition.get("memo_staleness_trigger")) or any("memo" in item.lower() for item in workflow_data.get("triggers", [])),
            "rescreeningTrigger": "Yes" if bool(definition.get("re_screening_trigger")) or any("screen" in item.lower() for item in workflow_data.get("triggers", [])) else "No",
            "coverageStatus": status,
        }
        policies.append(policy)
    return policies


def get_workflow_usage_mappings() -> List[Dict[str, Any]]:
    return deepcopy(WORKFLOW_USAGE_MAPPINGS)


def get_policy_alias_map() -> Dict[str, str]:
    alias_map = {}
    for policy in get_canonical_document_policies():
        alias_map[policy["document_type"]] = policy["document_type"]
        for alias in policy.get("aliases", []):
            alias_map[alias] = policy["document_type"]
    return alias_map


def policy_for_document_type(doc_type: str) -> Dict[str, Any] | None:
    if not doc_type:
        return None
    normalized = str(doc_type).strip().lower()
    canonical_key = get_policy_alias_map().get(normalized)
    if not canonical_key:
        return None
    for policy in get_canonical_document_policies():
        if policy["document_type"] == canonical_key:
            return policy
    return None


def summarise_document_policies(policies: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    policies = policies or get_canonical_document_policies()
    method_counts = {"Rule": 0, "Hybrid": 0, "AI": 0, "Manual": 0, "Unknown": 0}
    for policy in policies:
        for method, count in policy.get("check_method_counts", {}).items():
            method_counts[method] = method_counts.get(method, 0) + count
    active = [p for p in policies if p["active_pilot_status"] == STATUS_ACTIVE]
    manual = [p for p in policies if p["active_pilot_status"] == STATUS_MANUAL]
    future = [p for p in policies if p["active_pilot_status"] == STATUS_FUTURE]
    return {
        "registry_version": REGISTRY_VERSION,
        "total_policies": len(policies),
        "active_policies": len(active),
        "manual_review_only_policies": len(manual),
        "future_enterprise_policies": len(future),
        "backend_executable_policies": sum(1 for p in policies if p.get("backend_executable")),
        "executable_check_instances": sum(sum(p.get("check_method_counts", {}).values()) for p in active if p.get("backend_executable")),
        "check_method_counts": method_counts,
        "lifecycle_stages_covered": len({usage["label"] for usage in WORKFLOW_USAGE_MAPPINGS}),
        "policies_that_block_decisions": sum(1 for p in policies if p.get("gateType") == "blocking"),
        "unknown_documents_require_review": True,
        "sar_str_active": any(
            p.get("document_type") == "sar_str_support" and p.get("active_pilot_status") == STATUS_ACTIVE
            for p in policies
        ),
    }


def build_document_policy_payload() -> Dict[str, Any]:
    policies = get_canonical_document_policies()
    return {
        "registry_version": REGISTRY_VERSION,
        "summary": summarise_document_policies(policies),
        "document_policies": policies,
        "workflow_usages": get_workflow_usage_mappings(),
        "unknown_unclassified_handling": {
            "status": STATUS_MANUAL,
            "automated_reliance_allowed": False,
            "required_action": "Officer classification and verification/manual acceptance reason required before reliance.",
        },
        "autonomy_boundary": {
            "agent1_can": ["verify", "flag", "block", "recommend", "trigger required follow-up markers"],
            "agent1_cannot": ["approve", "reject", "waive", "override compliance decisions", "perform sanctions/PEP/adverse-media screening"],
        },
    }
