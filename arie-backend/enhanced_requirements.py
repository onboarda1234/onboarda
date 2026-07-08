"""Enhanced / EDD requirement settings.

This module defines the configurable rule vocabulary used by the
back-office settings layer.  It deliberately does not generate RMI requests or
client notifications.  Approval and memo integrations are deterministic
read-only consumers of the application-specific requirement records.
"""

import json
import logging
import re
from datetime import datetime, timezone

from document_policy_registry import STATUS_ACTIVE, STATUS_FUTURE, STATUS_MANUAL, policy_for_document_type

logger = logging.getLogger(__name__)


ALLOWED_AUDIENCES = ("client", "backoffice", "both")
ALLOWED_REQUIREMENT_TYPES = (
    "document",
    "declaration",
    "review_task",
    "explanation",
    "internal_control",
)
ALLOWED_SUBJECT_SCOPES = (
    "company",
    "ubo",
    "director",
    "controller",
    "application",
    "screening_subject",
    "intermediary",
)
ALLOWED_WAIVER_ROLES = ("admin", "sco")

EXPECTED_DEFAULT_TRIGGER_KEYS = (
    "high_or_very_high_risk",
    "pep",
    "crypto_vasp",
    "opaque_ownership",
    "high_risk_jurisdiction",
    "high_volume",
)

APPLICATION_REQUIREMENT_STATUSES = (
    "generated",
    "requested",
    "uploaded",
    "under_review",
    "accepted",
    "rejected",
    "waived",
    "cancelled",
)

APPLICATION_REQUIREMENT_REVIEW_ROLES = ("admin", "sco", "co")
APPLICATION_REQUIREMENT_WAIVER_ROLES = ("admin", "sco")
APPLICATION_REQUIREMENT_REQUEST_ROLES = ("admin", "sco", "co")
APPLICATION_REQUIREMENT_FK_AUDIT_ROLES = ("admin", "sco", "co", "analyst")
APPLICATION_REQUIREMENT_NOTES_MAX_LENGTH = 4000
APPLICATION_REQUIREMENT_REQUESTABLE_AUDIENCES = ("client", "both")
APPLICATION_REQUIREMENT_REQUESTABLE_STATUSES = ("generated", "under_review", "rejected")
APPLICATION_REQUIREMENT_PORTAL_VISIBLE_STATUSES = (
    "requested",
    "uploaded",
    "under_review",
    "rejected",
)
APPLICATION_REQUIREMENT_CLIENT_FULFILLMENT_AUDIENCES = ("client", "both")
APPLICATION_REQUIREMENT_CLIENT_FULFILLMENT_STATUSES = ("requested", "rejected")
APPLICATION_REQUIREMENT_CLIENT_DOCUMENT_TYPES = ("document",)
APPLICATION_REQUIREMENT_CLIENT_TEXT_TYPES = ("explanation", "declaration")
APPLICATION_REQUIREMENT_CLIENT_RESPONSE_MAX_LENGTH = 10000
APPLICATION_REQUIREMENT_MEMO_UNRESOLVED_STATUSES = (
    "generated",
    "requested",
    "uploaded",
    "under_review",
    "rejected",
)
APPLICATION_REQUIREMENT_APPROVAL_UNRESOLVED_STATUSES = APPLICATION_REQUIREMENT_MEMO_UNRESOLVED_STATUSES
APPLICATION_REQUIREMENT_APPROVAL_RESOLVED_STATUSES = ("accepted", "waived")
APPLICATION_REQUIREMENT_STATUS_TRANSITIONS = {
    "generated": ("under_review", "accepted", "rejected", "waived"),
    "requested": ("under_review", "accepted", "rejected", "waived"),
    "uploaded": ("under_review", "accepted", "rejected", "waived"),
    "under_review": ("accepted", "rejected", "waived"),
    "rejected": ("under_review", "accepted"),
    "waived": ("under_review",),
    "accepted": ("under_review",),
    "cancelled": (),
}

PRESENTATION_REQUIREMENT_TYPES = (
    "evidence",
    "portal_disclosure",
    "internal_control",
)

_EVIDENCE_TYPE_TERMS = (
    "adverse_media_explanation",
    "bank_statement",
    "document",
    "evidence",
    "financial_statement",
    "funds_evidence",
    "proof",
    "source_of_funds",
    "source_of_wealth",
    "supporting_document",
    "wealth_declaration",
)

_PORTAL_DISCLOSURE_TERMS = (
    "declaration",
    "declared_pep",
    "pep_declaration",
    "pep_jurisdiction",
    "pep_position",
    "pep_role",
    "portal_form",
    "questionnaire",
    "self_declared_pep",
)

_INTERNAL_CONTROL_TERMS = (
    "approval_control",
    "enhanced_monitoring",
    "mandatory_senior_review",
    "monitoring_flag",
    "ongoing_monitoring_flag",
    "second_line",
    "senior_review",
    "supervisor_review",
)

EDD_TRIGGER_TO_REQUIREMENT_TRIGGER = {
    "high_or_very_high_risk": "high_or_very_high_risk",
    "declared_pep_present": "pep",
    "crypto_or_virtual_asset_sector": "crypto_vasp",
    "elevated_jurisdiction": "high_risk_jurisdiction",
    "opaque_or_incomplete_ownership": "opaque_ownership",
}

SCREENING_INDEPENDENT_ROUTING_TRIGGERS = {
    "material_screening_concern",
    "supervisor_mandatory_escalation",
}

BANK_ACCOUNT_DEPENDENT_REQUIREMENT_KEYS = {
    "company_bank_reference",
}


ENHANCED_REQUIREMENT_DOCUMENT_POLICY_MAP = {
    # Active v5 enhanced-requirement document-backed rows only.
    "company_bank_reference": "bankref",
    "company_sof_evidence": "source_funds",
    "aml_cft_policy": "aml_policy",
    "trust_nominee_foundation_documents": "trust_deed",
    "jurisdiction_sof_evidence": "source_funds",
    "contracts_invoices": "contracts",
    "expected_transaction_flow_evidence": "supporting_document",
    # PR-PRS-B: periodic-review document requests bound to canonical Agent 1
    # policies so uploads run verification instead of silently falling
    # through to supporting_document. Keys are the periodic_review_document_requests
    # QUESTION_REQUIREMENT_MAP requirement_keys. Mappings flagged with TODO need
    # product sign-off; narrative/disclosure-type keys are intentionally NOT mapped
    # here (they remain manual) pending the v7 Section E disclosure split.
    "updated_register_of_directors": "reg_dir",
    "new_director_id_document": "passport",
    "new_director_proof_of_address": "poa",
    "updated_register_of_shareholders": "reg_sh",
    "updated_cap_table": "reg_sh",  # TODO confirm: reg_sh vs structure_chart
    "updated_ownership_chart": "structure_chart",
    "ubo_identification_document": "passport",
    "ubo_proof_of_address": "poa",
    "proof_of_ownership_or_control": "structure_chart",  # TODO confirm
    "website_product_operating_evidence": "contracts",
    "contracts_invoices_or_commercial_evidence": "contracts",
    "regulated_activity_licence_or_approval": "licence",
    "financials_bank_statements_or_projections": "bank_statements",
    "licence_or_registration_certificate": "licence",
    "updated_company_extract": "cert_inc",  # TODO confirm: cert_inc vs business registration
    "updated_registered_office_proof": "poa",  # TODO confirm: entity registered-address policy
    "updated_authorised_contact_confirmation": "board_res",  # TODO confirm
}

LEGACY_ENHANCED_REQUIREMENT_DOCUMENT_POLICY_ALIASES = {
    # Historical/read-only compatibility for generated records created before
    # KYC/EDD matrix v5. These keys are not active defaults for new generation.
    "company_bank_statements_6m": "bank_statements",
    "material_ubo_sow_evidence": "source_wealth",
    "pep_sow_evidence": "source_wealth",
    "pep_bank_reference": "bankref",
    "pep_linked_sof_evidence": "source_funds",
    "licence_or_registration_evidence": "licence",
    "crypto_source_of_funds_evidence": "source_funds",
    "ownership_structure_chart": "structure_chart",
    "jurisdiction_licensing_regulatory_evidence": "licence",
    "high_volume_bank_statements": "bank_statements",
    "ownership_chain_documents": "supporting_document",
    "enhanced_ubo_evidence": "supporting_document",
}

SECTION_B_PERSON_DOCUMENT_POLICY_PREFIXES = {
    "bankref": "bankref",
    "source_wealth": "source_wealth",
}

TARGET_ENHANCED_REQUIREMENT_SECTIONS = {
    "company_bank_reference": "C",
    "company_sof_evidence": "C",
    "pep_declaration_details": "E",
    "pep_adverse_media_assessment": "F",
    "pep_enhanced_monitoring_flag": "F",
    "aml_cft_policy": "C",
    "trust_nominee_foundation_documents": "C",
    "jurisdiction_sof_evidence": "C",
    "jurisdiction_exposure_rationale": "E",
    "jurisdiction_risk_assessment": "F",
    "contracts_invoices": "C",
    "expected_transaction_flow_evidence": "C",
    "major_counterparties_explanation": "E",
    "volume_rationale_vs_business_size": "E",
}

REMOVED_ACTIVE_ENHANCED_REQUIREMENT_KEYS = {
    "enhanced_business_activity_explanation",
    "company_bank_statements_6m",
    "material_ubo_sow_evidence",
    "pep_role_position",
    "pep_jurisdiction",
    "pep_sow_evidence",
    "pep_bank_reference",
    "pep_linked_sof_evidence",
    "mandatory_senior_review",
    "ongoing_monitoring_flag",
    "licence_or_registration_evidence",
    "transaction_flow_explanation",
    "jurisdictions_served",
    "wallet_exchange_counterparty_exposure",
    "crypto_source_of_funds_evidence",
    "crypto_enhanced_monitoring_flag",
    "crypto_regulatory_status_assessment",
    "ownership_structure_chart",
    "ownership_chain_documents",
    "enhanced_ubo_evidence",
    "control_rationale",
    "operating_country_target_market_explanation",
    "jurisdiction_licensing_regulatory_evidence",
    "enhanced_screening_review",
    "high_volume_bank_statements",
    "screening_disposition",
    "false_positive_rationale",
    "adverse_media_pep_sanctions_assessment",
    "material_screening_senior_review",
    "client_clarification_screening",
    "manual_edd_pack",
    "money_services_pack",
    "regulated_financial_services_pack",
    "cross_border_pack",
    "high_risk_product_pack",
}


def _document_type_for_enhanced_requirement(requirement_key):
    key = _clean_text(requirement_key).lower()
    if key in ENHANCED_REQUIREMENT_DOCUMENT_POLICY_MAP:
        return ENHANCED_REQUIREMENT_DOCUMENT_POLICY_MAP[key]
    for prefix, doc_type in SECTION_B_PERSON_DOCUMENT_POLICY_PREFIXES.items():
        if key == prefix or key.startswith(prefix + "_"):
            return doc_type
    if key in LEGACY_ENHANCED_REQUIREMENT_DOCUMENT_POLICY_ALIASES:
        return LEGACY_ENHANCED_REQUIREMENT_DOCUMENT_POLICY_ALIASES[key]
    for prefix, doc_type in (
        ("material_ubo_sow_evidence", "source_wealth"),
        ("pep_sow_evidence", "source_wealth"),
        ("pep_bank_reference", "bankref"),
        ("pep_linked_sof_evidence", "source_funds"),
    ):
        if key.startswith(prefix + "_"):
            return doc_type
    return "supporting_document"


def enhanced_requirement_document_policy(requirement_key):
    """Return the canonical Agent 1 policy classification for an EDD request."""
    key = _clean_text(requirement_key).lower()
    doc_type = _document_type_for_enhanced_requirement(key)
    policy = policy_for_document_type(doc_type) or {}
    status = policy.get("active_pilot_status") or STATUS_MANUAL
    backend_executable = bool(policy.get("backend_executable"))
    runtime_executable = status == STATUS_ACTIVE and backend_executable
    if status == STATUS_FUTURE:
        verification_mode = "future_enterprise"
    elif runtime_executable:
        verification_mode = "active_runtime_verified"
    else:
        verification_mode = "manual_review_only"
    return {
        "requirement_key": key,
        "document_type": policy.get("document_type") or doc_type,
        "display_label": policy.get("display_label") or doc_type.replace("_", " ").title(),
        "policy_id": policy.get("policy_id") or "",
        "active_pilot_status": status,
        "backend_executable": backend_executable,
        "runtime_executable": runtime_executable,
        "manual_review_only": verification_mode == "manual_review_only",
        "future_enterprise": verification_mode == "future_enterprise",
        "verification_mode": verification_mode,
    }


DEFAULT_ENHANCED_REQUIREMENT_RULES = [
    {
        "trigger_key": "high_or_very_high_risk",
        "trigger_label": "HIGH / VERY_HIGH risk",
        "trigger_category": "risk",
        "requirement_key": "company_bank_reference",
        "requirement_label": "Company bank reference letter",
        "requirement_description": "Company bank reference letter for the proposed relationship.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "company",
        "blocking_approval": False,
        "mandatory": True,
        "sort_order": 10,
        "applies_when": {"existing_bank_account": True},
        "client_safe_label": "Company bank reference letter",
        "client_safe_description": "Please upload the requested company bank reference letter.",
    },
    {
        "trigger_key": "high_or_very_high_risk",
        "trigger_label": "HIGH / VERY_HIGH risk",
        "trigger_category": "risk",
        "requirement_key": "company_sof_evidence",
        "requirement_label": "Company Source of Funds evidence",
        "requirement_description": "Evidence explaining the origin of company funds used for the proposed relationship.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "company",
        "blocking_approval": False,
        "mandatory": True,
        "sort_order": 20,
        "client_safe_label": "Company Source of Funds evidence",
        "client_safe_description": "Please upload evidence explaining the source of company funds for the proposed relationship.",
    },
    {
        "trigger_key": "pep",
        "trigger_label": "PEP / declared PEP",
        "trigger_category": "screening",
        "requirement_key": "pep_declaration_details",
        "requirement_label": "Additional declaration details",
        "requirement_description": "Additional declaration details captured for officer review.",
        "audience": "client",
        "requirement_type": "declaration",
        "subject_scope": "screening_subject",
        "blocking_approval": True,
        "mandatory": True,
        "sort_order": 10,
        "client_safe_label": "Additional declaration details",
        "client_safe_description": "Please provide the requested declaration details so our team can complete the review.",
    },
    {
        "trigger_key": "pep",
        "trigger_label": "PEP / adverse media context",
        "trigger_category": "screening",
        "requirement_key": "pep_adverse_media_assessment",
        "requirement_label": "Adverse media assessment",
        "requirement_description": "Back-office adverse media assessment for the relevant person.",
        "audience": "backoffice",
        "requirement_type": "review_task",
        "subject_scope": "screening_subject",
        "blocking_approval": False,
        "mandatory": False,
        "waivable": False,
        "waiver_roles": [],
        "sort_order": 20,
    },
    {
        "trigger_key": "pep",
        "trigger_label": "PEP monitoring",
        "trigger_category": "screening",
        "requirement_key": "pep_enhanced_monitoring_flag",
        "requirement_label": "Enhanced monitoring flag",
        "requirement_description": "Back-office monitoring flag for the relevant person or relationship.",
        "audience": "backoffice",
        "requirement_type": "internal_control",
        "subject_scope": "application",
        "blocking_approval": False,
        "mandatory": False,
        "waivable": False,
        "waiver_roles": [],
        "sort_order": 30,
    },
    {
        "trigger_key": "crypto_vasp",
        "trigger_label": "Crypto / VASP",
        "trigger_category": "sector",
        "requirement_key": "aml_cft_policy",
        "requirement_label": "AML/CFT policy document",
        "requirement_description": "AML/CFT policy document applicable to the activity.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "company",
        "blocking_approval": False,
        "mandatory": False,
        "sort_order": 10,
        "client_safe_label": "AML/CFT policy document",
        "client_safe_description": "Please upload the requested AML/CFT policy document.",
    },
    {
        "trigger_key": "opaque_ownership",
        "trigger_label": "Trust / nominee / foundation",
        "trigger_category": "ownership",
        "requirement_key": "trust_nominee_foundation_documents",
        "requirement_label": "Trust / nominee / foundation documents",
        "requirement_description": "Trust deeds, nominee agreements, foundation documents, or equivalent control documents where applicable.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "controller",
        "blocking_approval": True,
        "mandatory": True,
        "sort_order": 10,
        "client_safe_label": "Trust / nominee / foundation documents",
        "client_safe_description": "Please upload the requested trust, nominee, foundation, or equivalent control documents.",
    },
    {
        "trigger_key": "high_risk_jurisdiction",
        "trigger_label": "High-risk jurisdiction",
        "trigger_category": "jurisdiction",
        "requirement_key": "jurisdiction_sof_evidence",
        "requirement_label": "Source of funds evidence for activity in the higher-risk jurisdiction",
        "requirement_description": "Source of funds evidence for activity in the relevant jurisdiction.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "application",
        "blocking_approval": True,
        "mandatory": True,
        "active": False,
        "sort_order": 10,
        "client_safe_label": "Source of funds evidence",
        "client_safe_description": "Please upload the requested source of funds evidence.",
    },
    {
        "trigger_key": "high_risk_jurisdiction",
        "trigger_label": "High-risk jurisdiction",
        "trigger_category": "jurisdiction",
        "requirement_key": "jurisdiction_exposure_rationale",
        "requirement_label": "Jurisdiction Exposure Rationale",
        "requirement_description": "Required for certain countries of incorporation.",
        "audience": "client",
        "requirement_type": "explanation",
        "subject_scope": "application",
        "blocking_approval": True,
        "mandatory": True,
        "sort_order": 20,
        "client_safe_label": "Country of incorporation information",
        "client_safe_description": "Required for certain countries of incorporation.",
    },
    {
        "trigger_key": "high_risk_jurisdiction",
        "trigger_label": "High-risk jurisdiction",
        "trigger_category": "jurisdiction",
        "requirement_key": "jurisdiction_risk_assessment",
        "requirement_label": "Jurisdiction risk assessment",
        "requirement_description": "Back-office jurisdiction risk assessment.",
        "audience": "backoffice",
        "requirement_type": "review_task",
        "subject_scope": "application",
        "blocking_approval": True,
        "mandatory": True,
        "waivable": False,
        "waiver_roles": [],
        "sort_order": 30,
    },
    {
        "trigger_key": "high_volume",
        "trigger_label": "High transaction volume",
        "trigger_category": "transaction",
        "requirement_key": "contracts_invoices",
        "requirement_label": "Contracts / invoices",
        "requirement_description": "Commercial contracts, invoices, or equivalent evidence supporting expected transaction volume.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "application",
        "blocking_approval": False,
        "mandatory": True,
        "sort_order": 10,
        "client_safe_label": "Contracts / invoices",
        "client_safe_description": "Please upload contracts, invoices, or equivalent evidence supporting expected transaction activity.",
    },
    {
        "trigger_key": "high_volume",
        "trigger_label": "High transaction volume",
        "trigger_category": "transaction",
        "requirement_key": "expected_transaction_flow_evidence",
        "requirement_label": "Expected transaction flow evidence",
        "requirement_description": "Evidence supporting expected transaction flow, frequency, ticket size, and corridors.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "application",
        "blocking_approval": True,
        "mandatory": True,
        "active": False,
        "sort_order": 20,
        "client_safe_label": "Expected transaction flow evidence",
        "client_safe_description": "Please upload evidence supporting expected transaction flow.",
    },
    {
        "trigger_key": "high_volume",
        "trigger_label": "High transaction volume",
        "trigger_category": "transaction",
        "requirement_key": "major_counterparties_explanation",
        "requirement_label": "Major counterparties explanation",
        "requirement_description": "Explain key counterparties, customer segments, suppliers, or payment participants.",
        "audience": "client",
        "requirement_type": "explanation",
        "subject_scope": "application",
        "blocking_approval": False,
        "mandatory": True,
        "sort_order": 30,
        "client_safe_label": "Major counterparties explanation",
        "client_safe_description": "Please provide information about major counterparties, customer segments, suppliers, or payment participants.",
    },
    {
        "trigger_key": "high_volume",
        "trigger_label": "High transaction volume",
        "trigger_category": "transaction",
        "requirement_key": "volume_rationale_vs_business_size",
        "requirement_label": "Volume rationale vs business size",
        "requirement_description": "Explain why expected volumes are proportionate to business size, age, sector, and operating model.",
        "audience": "client",
        "requirement_type": "explanation",
        "subject_scope": "application",
        "blocking_approval": True,
        "mandatory": True,
        "sort_order": 40,
        "client_safe_label": "Volume rationale vs business size",
        "client_safe_description": "Please explain why expected volumes are proportionate to the business size, age, sector, and operating model.",
    },
]


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _loads_json(value, fallback):
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _bool(value, default=False):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _clean_text(value, default=""):
    if value is None:
        return default
    return str(value).strip()


def _application_prescreening(app):
    app = _row_dict(app) or {}
    return _loads_json(app.get("prescreening_data"), {}) or {}


def _application_existing_bank_account(app):
    app = _row_dict(app) or {}
    prescreening = _application_prescreening(app)
    candidates = (
        prescreening.get("existing_bank_account"),
        prescreening.get("has_existing_bank_account"),
        prescreening.get("has_bank"),
        prescreening.get("hasBank"),
        app.get("existing_bank_account"),
        app.get("has_existing_bank_account"),
    )
    for value in candidates:
        if value in (None, ""):
            continue
        text = str(value).strip().lower()
        if text in ("yes", "y", "true", "1", "on"):
            return True
        if text in ("no", "n", "false", "0", "off"):
            return False
    return False


def _rule_applicable_to_application(rule, app):
    key = _clean_text((rule or {}).get("requirement_key")).lower()
    if key in BANK_ACCOUNT_DEPENDENT_REQUIREMENT_KEYS and not _application_existing_bank_account(app):
        return False, "existing_bank_account_not_declared_yes"
    applies_when = (rule or {}).get("applies_when") or {}
    if isinstance(applies_when, str):
        applies_when = _loads_json(applies_when, {})
    if isinstance(applies_when, dict) and _bool(applies_when.get("existing_bank_account"), False):
        if not _application_existing_bank_account(app):
            return False, "existing_bank_account_not_declared_yes"
    return True, ""


def _prefill_fields_for_generated_requirement(rule, app):
    key = _clean_text((rule or {}).get("requirement_key")).lower()
    if key != "jurisdiction_exposure_rationale":
        return {}
    app = _row_dict(app) or {}
    prescreening = _application_prescreening(app)
    rationale = _clean_text(prescreening.get("jurisdiction_exposure_rationale"))
    if not rationale:
        return {}
    return {
        "status": "uploaded",
        "client_response_text": rationale,
        "client_response_at": app.get("created_at") or _now_iso(),
        "client_response_by": app.get("client_id"),
        "uploaded_at": _now_iso(),
    }


def _require_key(value, field_name):
    text = _clean_text(value)
    if not text:
        return None, f"{field_name} is required"
    if not re.match(r"^[a-z0-9][a-z0-9_:-]{1,119}$", text):
        return None, f"{field_name} must be a stable lowercase key"
    return text, None


def serialize_rule(row):
    """Return an API-safe dict for a DB row."""
    if row is None:
        return None
    item = dict(row)
    for key in ("blocking_approval", "waivable", "mandatory", "active"):
        item[key] = _bool(item.get(key))
    item["waiver_roles"] = _loads_json(item.get("waiver_roles"), [])
    item["applies_when"] = _loads_json(item.get("applies_when"), {})
    req_key = _clean_text(item.get("requirement_key")).lower()
    if _clean_text(item.get("requirement_type")).lower() == "document":
        item["canonical_doc_type"] = _document_type_for_enhanced_requirement(req_key)
        item["document_policy"] = enhanced_requirement_document_policy(req_key)
    else:
        item["canonical_doc_type"] = ""
    section = TARGET_ENHANCED_REQUIREMENT_SECTIONS.get(req_key)
    if not section:
        if req_key.startswith("bankref_") or req_key.startswith("source_wealth_"):
            section = "B"
        elif item.get("audience") == "backoffice":
            section = "F"
    item["section"] = section or ""
    item["portal_section"] = section if section in ("A", "B", "C", "D", "E") else ""
    item["backoffice_section"] = section or ""
    return item


def serialize_application_requirement(row):
    """Return an API-safe dict for a generated application requirement row."""
    if row is None:
        return None
    item = _row_dict(row)
    for key in ("blocking_approval", "waivable", "mandatory", "active"):
        item[key] = _bool(item.get(key))
    if "source_rule_active" in item:
        item["source_rule_active"] = _bool(item.get("source_rule_active"))
    item["waiver_roles"] = _loads_json(item.get("waiver_roles"), [])
    item["trigger_context"] = _loads_json(item.get("trigger_context"), {})
    if isinstance(item.get("trigger_context"), dict):
        item["trigger_question_key"] = _clean_text(item["trigger_context"].get("trigger_question_key"))
        item["trigger_question_label"] = _clean_text(item["trigger_context"].get("trigger_question_label"))
    subject = item["trigger_context"].get("subject") if isinstance(item.get("trigger_context"), dict) else None
    if isinstance(subject, dict):
        item["subject"] = {
            "type": _clean_text(subject.get("type") or subject.get("subject_type")),
            "id": _clean_text(subject.get("id") or subject.get("subject_id")),
            "person_key": _clean_text(subject.get("person_key")),
            "name": _clean_text(subject.get("name") or subject.get("subject_name")),
        }
        item["subject_name"] = item["subject"].get("name")
        item["subject_id"] = item["subject"].get("id")
        item["subject_person_key"] = item["subject"].get("person_key")
    req_key = _clean_text(item.get("requirement_key")).lower()
    section = TARGET_ENHANCED_REQUIREMENT_SECTIONS.get(req_key)
    if not section:
        if req_key.startswith("bankref_") or req_key.startswith("source_wealth_"):
            section = "B"
        elif item.get("audience") == "backoffice":
            section = "F"
    item["section"] = section or ""
    item["portal_section"] = section if section in ("A", "B", "C", "D", "E") else ""
    item["backoffice_section"] = section or ""
    if _clean_text(item.get("requirement_type")).lower() == "document":
        item["document_policy"] = enhanced_requirement_document_policy(item.get("requirement_key"))
        item["canonical_doc_type"] = item["document_policy"].get("document_type")
    else:
        item["canonical_doc_type"] = ""
    return item


def classify_requirement_presentation_type(requirement):
    """Classify one enhanced requirement for officer-facing workflow display.

    This deliberately does not change the persisted requirement_type.  Existing
    records keep their lifecycle semantics; the presentation type only prevents
    portal disclosures and internal controls from being rendered as missing
    documents.
    """
    requirement = requirement or {}
    req_type = _clean_text(requirement.get("requirement_type")).lower()
    key = _clean_text(requirement.get("requirement_key")).lower()
    label = _clean_text(requirement.get("requirement_label")).lower()
    trigger = _clean_text(requirement.get("trigger_key")).lower()
    haystack = " ".join((key, label, trigger)).replace("-", "_")

    if req_type == "document":
        return "evidence"
    if req_type in ("review_task", "internal_control"):
        return "internal_control"
    if req_type in ("declaration", "explanation"):
        if any(term in haystack for term in _INTERNAL_CONTROL_TERMS):
            return "internal_control"
        if any(term in haystack for term in _EVIDENCE_TYPE_TERMS):
            return "evidence"
        return "portal_disclosure"

    if any(term in haystack for term in _INTERNAL_CONTROL_TERMS):
        return "internal_control"
    if any(term in haystack for term in _PORTAL_DISCLOSURE_TERMS):
        return "portal_disclosure"
    if any(term in haystack for term in _EVIDENCE_TYPE_TERMS):
        return "evidence"
    return "evidence"


def _pep_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "declared_yes", "confirmed_pep"}


def _first_value(data, keys):
    data = data or {}
    for key in keys:
        value = data.get(key)
        if value not in (None, "", []):
            return value
    return ""


def _load_portal_pep_disclosures(db, app):
    """Return PEP declaration data captured from the portal/application form."""
    app = _row_dict(app) or {}
    app_id = app.get("id")
    if not app_id:
        return []
    disclosures = []
    for table, party_type in (("directors", "director"), ("ubos", "ubo")):
        if not _table_exists(db, table):
            continue
        try:
            rows = db.execute(
                f"""
                SELECT id, person_key, full_name, nationality, is_pep,
                       pep_declaration, date_of_birth, created_at
                FROM {table}
                WHERE application_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (app_id,),
            ).fetchall()
        except Exception:
            continue
        for row in rows:
            party = _row_dict(row) or {}
            declaration = _loads_json(party.get("pep_declaration"), {})
            if not isinstance(declaration, dict):
                declaration = {}
            declared = (
                _pep_bool(party.get("is_pep"))
                or _pep_bool(declaration.get("declared_pep"))
                or _pep_bool(declaration.get("client_declared_pep"))
            )
            has_capture = declared or any(
                _first_value(declaration, keys)
                for keys in (
                    ("pep_role_type", "role_type", "pep_type"),
                    ("position_title", "public_function", "public_position"),
                    ("pep_country_jurisdiction", "country_jurisdiction", "jurisdiction", "country"),
                    ("relationship_type",),
                    ("source_of_wealth_detail", "source_of_wealth_note"),
                    ("source_of_funds_detail", "source_of_funds_note"),
                    ("notes",),
                )
            )
            if not has_capture:
                continue
            disclosures.append({
                "subject_type": party_type,
                "subject_id": party.get("id"),
                "person_key": party.get("person_key"),
                "subject_name": party.get("full_name") or "Unnamed party",
                "declared_pep": declared,
                "pep_status": declaration.get("pep_status") or ("declared_yes" if declared else "declared_no"),
                "pep_role_type": _first_value(declaration, ("pep_role_type", "role_type", "pep_type")),
                "position_title": _first_value(declaration, ("position_title", "public_function", "public_position")),
                "jurisdiction": _first_value(declaration, ("pep_country_jurisdiction", "country_jurisdiction", "jurisdiction", "country")),
                "relationship_type": _first_value(declaration, ("relationship_type",)) or ("self" if declared else ""),
                "related_pep_name": _first_value(declaration, ("related_pep_name",)),
                "source_of_wealth_detail": _first_value(declaration, ("source_of_wealth_detail", "source_of_wealth_note")),
                "source_of_funds_detail": _first_value(declaration, ("source_of_funds_detail", "source_of_funds_note")),
                "notes": _first_value(declaration, ("notes",)),
                "submitted_at": declaration.get("submitted_at") or declaration.get("captured_at") or party.get("created_at"),
                "submitted_by": app.get("client_id"),
                "source": "client_portal_application_form",
            })
    return disclosures


def _pep_subjects_for_person_specific_requirements(db, app):
    """Return portal-declared PEP subjects for person-specific evidence rows."""
    disclosures = _load_portal_pep_disclosures(db, app)
    subjects = []
    seen = set()
    for disclosure in disclosures:
        if not disclosure.get("declared_pep"):
            continue
        subject_type = _clean_text(disclosure.get("subject_type")) or "screening_subject"
        subject_id = _clean_text(disclosure.get("subject_id"))
        person_key = _clean_text(disclosure.get("person_key"))
        subject_name = _clean_text(disclosure.get("subject_name")) or "PEP subject"
        identity = (subject_type, subject_id or person_key or subject_name.lower())
        if identity in seen:
            continue
        seen.add(identity)
        subjects.append({
            "type": subject_type,
            "id": subject_id,
            "person_key": person_key,
            "name": subject_name,
        })
    return subjects


def _application_effective_risk_level(app):
    app = _row_dict(app) or {}
    return _clean_text(
        app.get("final_risk_level")
        or app.get("risk_level")
        or app.get("base_risk_level")
    ).upper()


def _application_high_or_very_high(app):
    return _application_effective_risk_level(app) in {"HIGH", "VERY_HIGH"}


def _party_subject(row, party_type):
    party = _row_dict(row) or {}
    declaration = _loads_json(party.get("pep_declaration"), {})
    if not isinstance(declaration, dict):
        declaration = {}
    name = (
        party.get("full_name")
        or party.get("entity_name")
        or party.get("person_key")
        or party.get("id")
        or "Unnamed party"
    )
    return {
        "type": party_type,
        "id": _clean_text(party.get("id")),
        "person_key": _clean_text(party.get("person_key")),
        "name": _clean_text(name),
        "is_pep": (
            _pep_bool(party.get("is_pep"))
            or _pep_bool(declaration.get("declared_pep"))
            or _pep_bool(declaration.get("client_declared_pep"))
        ),
    }


def _section_b_subjects_for_person_requirements(db, app):
    app = _row_dict(app) or {}
    app_id = app.get("id")
    if not app_id:
        return []
    subjects = []
    for table, party_type, columns in (
        ("directors", "director", "id, person_key, full_name, is_pep, pep_declaration, created_at"),
        ("ubos", "ubo", "id, person_key, full_name, is_pep, pep_declaration, created_at"),
        ("intermediaries", "intermediary", "id, person_key, entity_name, created_at"),
    ):
        if not _table_exists(db, table):
            continue
        try:
            rows = db.execute(
                f"""
                SELECT {columns}
                FROM {table}
                WHERE application_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (app_id,),
            ).fetchall()
        except Exception:
            continue
        subjects.extend(_party_subject(row, party_type) for row in rows)
    return subjects


def _section_b_person_document_rule(kind, subject, idx, *, reason):
    suffix = _subject_requirement_suffix(subject, idx)
    subject_type = subject.get("type") if subject.get("type") in ALLOWED_SUBJECT_SCOPES else "screening_subject"
    subject_name = subject.get("name") or "Named person"
    if kind == "bankref":
        key = f"bankref_{subject_type}_{suffix}"
        label = f"Bank Reference Letter - {subject_name}"
        description = "Bank reference letter for the named person."
        safe_description = "Please upload the requested bank reference letter for the named person."
        sort_offset = 10
    else:
        key = f"source_wealth_{subject_type}_{suffix}"
        label = f"Source of Wealth evidence - {subject_name}"
        description = "Source of Wealth evidence for the named person."
        safe_description = "Please upload source of wealth evidence for the named person."
        sort_offset = 20
    return {
        "id": None,
        "trigger_key": "standard_kyc_section_b",
        "trigger_label": "Section B person-level documents",
        "trigger_category": "standard_kyc",
        "requirement_key": key,
        "requirement_label": label,
        "requirement_description": description,
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": subject_type,
        "blocking_approval": True,
        "waivable": True,
        "waiver_roles": ["admin", "sco"],
        "mandatory": True,
        "active": True,
        "sort_order": 1000 + (idx * 10) + sort_offset,
        "applies_when": {"section": "B", "reason": reason},
        "client_safe_label": label,
        "client_safe_description": safe_description,
        "internal_notes": "Generated from KYC/EDD matrix v5 Section B.",
        "_subject": {
            "type": subject_type,
            "id": subject.get("id"),
            "person_key": subject.get("person_key"),
            "name": subject_name,
        },
    }


def _section_b_person_document_rules(db, app):
    """Return v5 Section B person-level requirements for new generation only."""
    high_or_very_high = _application_high_or_very_high(app)
    rules = []
    for idx, subject in enumerate(_section_b_subjects_for_person_requirements(db, app)):
        subject_type = subject.get("type")
        is_director_or_ubo = subject_type in {"director", "ubo"}
        is_pep = bool(subject.get("is_pep"))
        bankref_required = is_director_or_ubo and (high_or_very_high or is_pep)
        source_wealth_required = is_director_or_ubo and (high_or_very_high or is_pep)
        reason = "high_or_very_high_risk" if high_or_very_high else "pep_person"
        if bankref_required:
            rules.append(_section_b_person_document_rule("bankref", subject, idx, reason=reason))
        if source_wealth_required:
            rules.append(_section_b_person_document_rule("source_wealth", subject, idx, reason=reason))
    return rules


def _subject_requirement_suffix(subject, index=0):
    base = _clean_text(
        (subject or {}).get("person_key")
        or (subject or {}).get("id")
        or (subject or {}).get("name")
    )
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", base).strip("_").lower()
    return base or f"subject_{index + 1}"


def _portal_disclosure_summary(requirement, disclosures):
    key = _clean_text((requirement or {}).get("requirement_key")).lower()
    label = "Portal response"
    if "jurisdiction" in key:
        label = "PEP jurisdiction"
    elif "role" in key or "position" in key:
        label = "PEP role / position"
    elif "pep_declaration" in key or "declared_pep" in key:
        label = "PEP declaration"

    if not disclosures:
        return {
            "status": "not_submitted",
            "status_label": "Not submitted in portal",
            "summary": "Not submitted in portal",
            "fields": [],
            "responses": [],
        }

    fields = []
    for disclosure in disclosures:
        subject = disclosure.get("subject_name") or "Unnamed party"

        def add(field_label, value):
            value = _clean_text(value)
            if value:
                fields.append({
                    "label": field_label,
                    "value": value,
                    "subject": subject,
                    "subject_type": disclosure.get("subject_type"),
                })

        if "jurisdiction" in key:
            add("Jurisdiction", disclosure.get("jurisdiction"))
        elif "role" in key or "position" in key:
            add("Role/type", disclosure.get("pep_role_type"))
            add("Position/title", disclosure.get("position_title"))
            add("Relationship", disclosure.get("relationship_type"))
        elif "source_of_wealth" in key:
            add("Source of wealth", disclosure.get("source_of_wealth_detail"))
        elif "source_of_funds" in key:
            add("Source of funds", disclosure.get("source_of_funds_detail"))
        else:
            add("Declared PEP", "Yes" if disclosure.get("declared_pep") else "No")
            add("Role/type", disclosure.get("pep_role_type"))
            add("Position/title", disclosure.get("position_title"))
            add("Jurisdiction", disclosure.get("jurisdiction"))
            add("Relationship", disclosure.get("relationship_type"))

    visible_values = [f"{field['subject']}: {field['label']} {field['value']}" for field in fields[:4]]
    summary = "; ".join(visible_values) if visible_values else f"{label} captured in portal"
    return {
        "status": "captured",
        "status_label": "Captured from portal",
        "requirement_status_label": "Pending officer review",
        "summary": summary,
        "fields": fields,
        "responses": disclosures,
        "submitted_at": max([d.get("submitted_at") for d in disclosures if d.get("submitted_at")] or [None]),
        "submitted_by": next((d.get("submitted_by") for d in disclosures if d.get("submitted_by")), None),
    }


def _application_text_disclosure_config(requirement_key):
    key = _clean_text(requirement_key).lower()
    configs = {
        "volume_rationale_vs_business_size": {
            "field_key": "volume_rationale_vs_business_size",
            "label": "Volume rationale vs business size",
            "summary": "Volume rationale captured from portal",
            "extra_fields": (
                ("Expected monthly volume", ("monthly_volume", "expected_volume", "expected_volumes")),
            ),
        },
        "major_counterparties_explanation": {
            "field_key": "major_counterparties_explanation",
            "label": "Major counterparties explanation",
            "summary": "Major counterparties explanation captured from portal",
        },
        "operating_country_target_market_explanation": {
            "field_key": "operating_country_target_market_explanation",
            "label": "Operating-country / target-market explanation",
            "summary": "Operating-country / target-market explanation captured from portal",
        },
        "control_rationale": {
            "field_key": "control_rationale",
            "label": "Control rationale",
            "summary": "Control rationale captured from portal",
        },
    }
    return configs.get(key)


def _application_text_disclosure_summary(requirement, app):
    requirement = requirement or {}
    app = _row_dict(app) or {}
    config = _application_text_disclosure_config(requirement.get("requirement_key"))
    if not config:
        return None

    prescreening = _loads_json(app.get("prescreening_data"), {}) or {}
    subject = app.get("company") or app.get("company_name") or "Application"
    response_text = _clean_text(requirement.get("client_response_text"))
    response_source = "client_portal_enhanced_requirement_response" if response_text else ""
    if not response_text:
        response_text = _clean_text(prescreening.get(config["field_key"]))
        response_source = "client_portal_application_form" if response_text else ""

    if not response_text:
        return {
            "status": "not_submitted",
            "status_label": "Not submitted in portal",
            "summary": "Not submitted in portal",
            "fields": [],
            "responses": [],
        }

    fields = []
    for label, keys in config.get("extra_fields", ()):
        extra_value = _first_value(prescreening, keys)
        if extra_value:
            fields.append({
                "label": label,
                "value": extra_value,
                "subject": subject,
            })
    fields.append({
        "label": config["label"],
        "value": response_text,
        "subject": subject,
    })
    submitted_at = (
        requirement.get("client_response_at")
        or app.get("submitted_at")
        or app.get("created_at")
    )
    submitted_by = requirement.get("client_response_by") or app.get("client_id")
    return {
        "status": "captured",
        "status_label": "Captured from portal",
        "requirement_status_label": "Pending officer review",
        "summary": config["summary"],
        "fields": fields,
        "responses": [{
            "subject_type": "application",
            "subject_name": subject,
            "requirement_key": requirement.get("requirement_key"),
            "response_text": response_text,
            "source": response_source,
        }],
        "submitted_at": submitted_at,
        "submitted_by": submitted_by,
    }


def _jurisdiction_exposure_disclosure_summary(app):
    app = _row_dict(app) or {}
    prescreening = _loads_json(app.get("prescreening_data"), {}) or {}
    rationale = _clean_text(prescreening.get("jurisdiction_exposure_rationale"))
    country = _clean_text(prescreening.get("country_of_incorporation") or app.get("country"))
    if not rationale:
        return {
            "status": "not_submitted",
            "status_label": "Not submitted in portal",
            "summary": "Not submitted in portal",
            "fields": [],
            "responses": [],
        }
    fields = []
    subject = app.get("company") or app.get("company_name") or "Application"
    if country:
        fields.append({
            "label": "Country of incorporation",
            "value": country,
            "subject": subject,
        })
    fields.append({
        "label": "Jurisdiction exposure rationale",
        "value": rationale,
        "subject": subject,
    })
    return {
        "status": "captured",
        "status_label": "Captured from portal",
        "requirement_status_label": "Pending officer review",
        "summary": f"Jurisdiction exposure rationale captured for {country or 'selected jurisdiction'}",
        "fields": fields,
        "responses": [{
            "subject_type": "application",
            "subject_name": subject,
            "country_of_incorporation": country,
            "jurisdiction_exposure_rationale": rationale,
            "source": "client_portal_application_form",
        }],
        "submitted_at": app.get("created_at"),
        "submitted_by": app.get("client_id"),
    }


def _internal_control_summary(requirement, app):
    requirement = requirement or {}
    app = _row_dict(app) or {}
    status = _clean_text(requirement.get("status") or "generated").lower()
    key = _clean_text(requirement.get("requirement_key")).lower()
    completed = status in ("accepted", "waived", "cancelled")
    status_label = "Completed" if completed else "Pending"
    resolve_label = "Open relevant control"
    target_tab = "overview"
    summary = _clean_text(requirement.get("requirement_description")) or "Internal control must be completed before closure where applicable."

    if "senior" in key or "supervisor" in key:
        resolve_label = "Open AI Compliance Supervisor"
        target_tab = "supervisor"
        if completed:
            summary = "Senior/supervisor review has been recorded as completed for this requirement."
        else:
            summary = "Senior/supervisor review is pending. Use the supervisor workflow before closing this requirement."
    elif "monitoring" in key:
        resolve_label = "View monitoring status"
        target_tab = "lifecycle"
        if completed:
            summary = "Monitoring control has been recorded as completed for this requirement."
        else:
            app_status = _clean_text(app.get("status")).lower()
            if app_status == "approved":
                summary = "Monitoring setup/status should be reviewed in Lifecycle / Monitoring."
            else:
                summary = "Monitoring will activate after approval where the onboarding decision requires it."
        status_label = "Enabled" if completed and "monitoring" in key else status_label

    return {
        "status": "completed" if completed else "pending",
        "status_label": status_label,
        "summary": summary,
        "resolve_label": resolve_label,
        "target_tab": target_tab,
    }


def _verification_status_label(status):
    normalized = _clean_text(status or "pending").lower()
    return {
        "verified": "Verified",
        "pending": "Verification pending",
        "in_progress": "Verification running",
        "running": "Verification running",
        "flagged": "Review required",
        "failed": "Verification failed",
        "rejected": "Verification failed",
        "skipped": "Manual review required",
        "not_run": "Verification not available",
    }.get(normalized, normalized.replace("_", " ").title() or "Verification pending")


def _verification_status_tone(status):
    normalized = _clean_text(status or "pending").lower()
    if normalized == "verified":
        return "success"
    if normalized in ("flagged", "failed", "rejected"):
        return "error"
    if normalized in ("in_progress", "running"):
        return "info"
    if normalized == "skipped":
        return "warning"
    return "pending"


def _load_linked_documents_for_requirements(db, app_id, requirements):
    doc_ids = [
        _clean_text(item.get("linked_document_id"))
        for item in requirements or []
        if _clean_text(item.get("linked_document_id"))
    ]
    if not doc_ids or not _table_exists(db, "documents"):
        return {}
    placeholders = ",".join("?" for _ in doc_ids)
    try:
        rows = db.execute(
            f"""
            SELECT id, application_id, doc_type, doc_name, file_size, mime_type,
                   slot_key, is_current, version, verification_status,
                   verification_results, verified_at, review_status, reviewed_by,
                   reviewed_at, uploaded_at
            FROM documents
            WHERE application_id = ? AND id IN ({placeholders})
            """,
            [app_id] + doc_ids,
        ).fetchall()
    except Exception:
        return {}

    documents = {}
    for row in rows:
        doc = _row_dict(row) or {}
        status = _clean_text(doc.get("verification_status") or "pending").lower() or "pending"
        doc["verification_status"] = status
        doc["verification_status_label"] = _verification_status_label(status)
        doc["verification_status_tone"] = _verification_status_tone(status)
        documents[str(doc.get("id"))] = doc
    return documents


def decorate_application_requirements_for_backoffice(db, app, requirements):
    """Add read-only workflow taxonomy/enrichment for back-office rendering."""
    app = _row_dict(app) or {}
    disclosures = _load_portal_pep_disclosures(db, app)
    linked_documents = _load_linked_documents_for_requirements(db, app.get("id"), requirements)
    decorated = []
    for requirement in requirements or []:
        item = serialize_application_requirement(requirement) if not isinstance(requirement, dict) else dict(requirement)
        if not item:
            continue
        display_type = classify_requirement_presentation_type(item)
        item["requirement_display_type"] = display_type
        item["requirement_display_type_label"] = {
            "evidence": "Evidence requirement",
            "portal_disclosure": "Portal disclosure",
            "internal_control": "Internal control",
        }.get(display_type, "Evidence requirement")
        item["accepts_document_upload"] = (
            display_type == "evidence"
            and _clean_text(item.get("requirement_type")).lower() == "document"
        )
        if display_type == "portal_disclosure":
            key = _clean_text(item.get("requirement_key")).lower()
            app_text_disclosure = _application_text_disclosure_summary(item, app)
            if key == "jurisdiction_exposure_rationale":
                item["portal_disclosure"] = _jurisdiction_exposure_disclosure_summary(app)
            elif app_text_disclosure is not None:
                item["portal_disclosure"] = app_text_disclosure
            else:
                item["portal_disclosure"] = _portal_disclosure_summary(item, disclosures)
            disclosure = item["portal_disclosure"]
            status = _clean_text(item.get("status") or "generated").lower()
            if disclosure.get("status") == "captured" and status in ("generated", "requested", "uploaded", "under_review"):
                item["status_display_label"] = disclosure.get("requirement_status_label") or "Pending officer review"
                item["status_display_tone"] = "amber"
            elif disclosure.get("status") == "not_submitted":
                item["status_display_label"] = "Not submitted in portal"
                item["status_display_tone"] = "purple"
        elif display_type == "internal_control":
            item["internal_control"] = _internal_control_summary(item, app)
        if item.get("linked_document_id"):
            linked_doc = linked_documents.get(str(item.get("linked_document_id")))
            if linked_doc:
                item["linked_document"] = linked_doc
        decorated.append(item)
    return decorated


def validate_rule_payload(data, existing=None):
    """Validate and normalize a create/update payload.

    Args:
        data: incoming API payload.
        existing: optional serialized existing rule.  When provided, omitted
            fields inherit the existing value.

    Returns:
        (normalized_dict, error_message)
    """
    data = data or {}
    base = dict(existing or {})
    merged = dict(base)
    merged.update(data)

    trigger_key, err = _require_key(merged.get("trigger_key"), "trigger_key")
    if err:
        return None, err
    requirement_key, err = _require_key(merged.get("requirement_key"), "requirement_key")
    if err:
        return None, err
    requirement_label = _clean_text(merged.get("requirement_label"))
    if not requirement_label:
        return None, "requirement_label is required"

    audience = _clean_text(merged.get("audience") or "client").lower()
    if audience not in ALLOWED_AUDIENCES:
        return None, "audience must be one of: " + ", ".join(ALLOWED_AUDIENCES)

    requirement_type = _clean_text(merged.get("requirement_type") or "document").lower()
    if requirement_type not in ALLOWED_REQUIREMENT_TYPES:
        return None, "requirement_type must be one of: " + ", ".join(ALLOWED_REQUIREMENT_TYPES)

    subject_scope = _clean_text(merged.get("subject_scope") or "application").lower()
    if subject_scope not in ALLOWED_SUBJECT_SCOPES:
        return None, "subject_scope must be one of: " + ", ".join(ALLOWED_SUBJECT_SCOPES)

    waivable = _bool(merged.get("waivable"), True)
    raw_roles = merged.get("waiver_roles")
    if raw_roles in (None, ""):
        waiver_roles = ["admin", "sco"] if waivable else []
    elif isinstance(raw_roles, str):
        parsed_roles = _loads_json(raw_roles, None)
        waiver_roles = parsed_roles if isinstance(parsed_roles, list) else [r.strip() for r in raw_roles.split(",") if r.strip()]
    elif isinstance(raw_roles, list):
        waiver_roles = raw_roles
    else:
        return None, "waiver_roles must be a list"
    waiver_roles = sorted(set(_clean_text(r).lower() for r in waiver_roles if _clean_text(r)))
    invalid_roles = [r for r in waiver_roles if r not in ALLOWED_WAIVER_ROLES]
    if invalid_roles:
        return None, "waiver_roles contains invalid role(s): " + ", ".join(invalid_roles)
    if not waivable:
        waiver_roles = []

    applies_when = merged.get("applies_when")
    if applies_when in (None, ""):
        applies_when = {}
    elif isinstance(applies_when, str):
        applies_when = _loads_json(applies_when, None)
        if not isinstance(applies_when, dict):
            return None, "applies_when must be a JSON object"
    elif not isinstance(applies_when, dict):
        return None, "applies_when must be a JSON object"

    try:
        sort_order = int(merged.get("sort_order", 100))
    except (TypeError, ValueError):
        return None, "sort_order must be an integer"

    normalized = {
        "trigger_key": trigger_key,
        "trigger_label": _clean_text(merged.get("trigger_label") or trigger_key.replace("_", " ").title()),
        "trigger_category": _clean_text(merged.get("trigger_category") or "manual").lower(),
        "requirement_key": requirement_key,
        "requirement_label": requirement_label,
        "requirement_description": _clean_text(merged.get("requirement_description")),
        "audience": audience,
        "requirement_type": requirement_type,
        "subject_scope": subject_scope,
        "blocking_approval": _bool(merged.get("blocking_approval"), True),
        "waivable": waivable,
        "waiver_roles": waiver_roles,
        "mandatory": _bool(merged.get("mandatory"), True),
        "active": _bool(merged.get("active"), True),
        "sort_order": sort_order,
        "applies_when": applies_when,
        "client_safe_label": _clean_text(merged.get("client_safe_label") or requirement_label),
        "client_safe_description": _clean_text(merged.get("client_safe_description")),
        "internal_notes": _clean_text(merged.get("internal_notes")),
    }
    return normalized, None


def default_rule_rows():
    """Return validated default rules with explicit defaults applied."""
    rows = []
    for rule in DEFAULT_ENHANCED_REQUIREMENT_RULES:
        normalized, error = validate_rule_payload(rule)
        if error:
            raise ValueError(f"Invalid default enhanced requirement rule {rule}: {error}")
        rows.append(normalized)
    return rows


def _seed_actor_fk_value(db, actor):
    """Return a FK-safe users.id for seed metadata, or None for system seed.

    Production PostgreSQL enforces ``created_by`` / ``updated_by`` foreign
    keys on ``enhanced_requirement_rules``. Startup default seeding is a
    system action, and ``system`` is not an officer row. The columns are
    nullable by design, so system-seeded defaults keep audit attribution in
    ``audit_log`` while storing NULL in FK-constrained metadata columns.
    """
    actor_id = _clean_text(actor)
    if not actor_id:
        return None
    try:
        row = db.execute("SELECT id FROM users WHERE id=? LIMIT 1", (actor_id,)).fetchone()
        if row:
            return actor_id
    except Exception:
        return None
    return None


def seed_default_enhanced_requirement_rules(db, actor="system"):
    """Insert missing default rules without overwriting customized rows.

    Returns the number of new rows inserted.
    """
    inserted = 0
    inserted_keys = []
    actor_fk = _seed_actor_fk_value(db, actor)
    for rule in default_rule_rows():
        existing = db.execute(
            "SELECT id FROM enhanced_requirement_rules WHERE trigger_key=? AND requirement_key=?",
            (rule["trigger_key"], rule["requirement_key"]),
        ).fetchone()
        if existing:
            continue
        db.execute(
            """
            INSERT INTO enhanced_requirement_rules
            (trigger_key, trigger_label, trigger_category, requirement_key,
             requirement_label, requirement_description, audience,
             requirement_type, subject_scope, blocking_approval, waivable,
             waiver_roles, mandatory, active, sort_order, applies_when,
             client_safe_label, client_safe_description, internal_notes,
             created_by, updated_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                rule["trigger_key"],
                rule["trigger_label"],
                rule["trigger_category"],
                rule["requirement_key"],
                rule["requirement_label"],
                rule["requirement_description"],
                rule["audience"],
                rule["requirement_type"],
                rule["subject_scope"],
                1 if rule["blocking_approval"] else 0,
                1 if rule["waivable"] else 0,
                json.dumps(rule["waiver_roles"]),
                1 if rule["mandatory"] else 0,
                1 if rule["active"] else 0,
                rule["sort_order"],
                json.dumps(rule["applies_when"]),
                rule["client_safe_label"],
                rule["client_safe_description"],
                rule["internal_notes"],
                actor_fk,
                actor_fk,
            ),
        )
        inserted += 1
        inserted_keys.append(f"{rule['trigger_key']}:{rule['requirement_key']}")

    if inserted:
        detail = json.dumps({
            "event": "enhanced_requirement_rules.seeded",
            "inserted_count": inserted,
            "rule_keys": inserted_keys,
            "actor": actor,
            "timestamp": _now_iso(),
        }, sort_keys=True)
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) "
            "VALUES (?,?,?,?,?,?,?)",
            (actor, actor, "system", "enhanced_requirement_rules.seeded", "Enhanced Requirement Rules", detail, ""),
        )
    _apply_approved_enhanced_requirement_taxonomy_updates(db, actor=actor, actor_fk=actor_fk)
    return inserted


def _apply_approved_enhanced_requirement_taxonomy_updates(db, actor="system", actor_fk=None):
    """Reconcile named enhanced-rule settings approved in KYC/EDD matrix v5.

    Normal default seeding is intentionally non-destructive. These specific
    rows are product-approved configuration corrections that must apply to
    persisted staging/production settings without deleting historical
    generated application requirements.
    """
    updates = [
        (
            "high_or_very_high_risk",
            "company_bank_reference",
            {
                "requirement_label": "Company bank reference letter",
                "requirement_description": "Company bank reference letter for the proposed relationship.",
                "blocking_approval": 0,
                "mandatory": 1,
                "active": 1,
                "client_safe_label": "Company bank reference letter",
                "client_safe_description": "Please upload the requested company bank reference letter.",
                "subject_scope": "company",
                "audience": "client",
                "requirement_type": "document",
                "applies_when": json.dumps({"existing_bank_account": True}, sort_keys=True),
            },
        ),
        (
            "high_or_very_high_risk",
            "company_sof_evidence",
            {
                "active": 1,
                "requirement_label": "Company Source of Funds evidence",
                "requirement_description": "Evidence explaining the origin of company funds used for the proposed relationship.",
                "blocking_approval": 0,
                "mandatory": 1,
                "subject_scope": "company",
                "audience": "client",
                "requirement_type": "document",
                "client_safe_label": "Company Source of Funds evidence",
                "client_safe_description": "Please upload evidence explaining the source of company funds for the proposed relationship.",
            },
        ),
        (
            "pep",
            "pep_declaration_details",
            {
                "active": 1,
                "requirement_label": "Additional declaration details",
                "requirement_description": "Additional declaration details captured for officer review.",
                "blocking_approval": 1,
                "mandatory": 1,
                "subject_scope": "screening_subject",
                "audience": "client",
                "requirement_type": "declaration",
                "client_safe_label": "Additional declaration details",
                "client_safe_description": "Please provide the requested declaration details so our team can complete the review.",
            },
        ),
        (
            "pep",
            "pep_adverse_media_assessment",
            {
                "active": 1,
                "requirement_label": "Adverse media assessment",
                "requirement_description": "Back-office adverse media assessment for the relevant person.",
                "blocking_approval": 0,
                "mandatory": 0,
                "waivable": 0,
                "waiver_roles": "[]",
                "subject_scope": "screening_subject",
                "audience": "backoffice",
                "requirement_type": "review_task",
            },
        ),
        (
            "pep",
            "pep_enhanced_monitoring_flag",
            {
                "active": 1,
                "requirement_label": "Enhanced monitoring flag",
                "requirement_description": "Back-office monitoring flag for the relevant person or relationship.",
                "blocking_approval": 0,
                "mandatory": 0,
                "waivable": 0,
                "waiver_roles": "[]",
                "subject_scope": "application",
                "audience": "backoffice",
                "requirement_type": "internal_control",
            },
        ),
        ("crypto_vasp", "aml_cft_policy", {
            "active": 1,
            "requirement_label": "AML/CFT policy document",
            "requirement_description": "AML/CFT policy document applicable to the activity.",
            "blocking_approval": 0,
            "mandatory": 0,
            "subject_scope": "company",
            "audience": "client",
            "requirement_type": "document",
            "client_safe_label": "AML/CFT policy document",
            "client_safe_description": "Please upload the requested AML/CFT policy document.",
        }),
        (
            "opaque_ownership",
            "trust_nominee_foundation_documents",
            {
                "active": 1,
                "requirement_label": "Trust / nominee / foundation documents",
                "blocking_approval": 1,
                "mandatory": 1,
                "subject_scope": "controller",
                "audience": "client",
                "requirement_type": "document",
                "client_safe_label": "Trust / nominee / foundation documents",
                "client_safe_description": "Please upload the requested trust, nominee, foundation, or equivalent control documents.",
            },
        ),
        (
            "high_risk_jurisdiction",
            "jurisdiction_sof_evidence",
            {
                "active": 0,
                "requirement_label": "Source of funds evidence for activity in the higher-risk jurisdiction",
                "requirement_description": "Source of funds evidence for activity in the relevant jurisdiction.",
                "blocking_approval": 1,
                "mandatory": 1,
                "subject_scope": "application",
                "audience": "client",
                "requirement_type": "document",
                "client_safe_label": "Source of funds evidence",
                "client_safe_description": "Please upload the requested source of funds evidence.",
            },
        ),
        (
            "high_risk_jurisdiction",
            "jurisdiction_exposure_rationale",
            {
                "active": 1,
                "requirement_label": "Jurisdiction Exposure Rationale",
                "requirement_description": "Required for certain countries of incorporation.",
                "blocking_approval": 1,
                "mandatory": 1,
                "subject_scope": "application",
                "audience": "client",
                "requirement_type": "explanation",
                "client_safe_label": "Country of incorporation information",
                "client_safe_description": "Required for certain countries of incorporation.",
            },
        ),
        (
            "high_risk_jurisdiction",
            "jurisdiction_risk_assessment",
            {
                "active": 1,
                "requirement_label": "Jurisdiction risk assessment",
                "requirement_description": "Back-office jurisdiction risk assessment.",
                "blocking_approval": 1,
                "mandatory": 1,
                "waivable": 0,
                "waiver_roles": "[]",
                "subject_scope": "application",
                "audience": "backoffice",
                "requirement_type": "review_task",
            },
        ),
        ("high_volume", "contracts_invoices", {
            "active": 1,
            "requirement_label": "Contracts / invoices",
            "requirement_description": "Commercial contracts, invoices, or equivalent evidence supporting expected transaction volume.",
            "blocking_approval": 0,
            "mandatory": 1,
            "subject_scope": "application",
            "audience": "client",
            "requirement_type": "document",
            "client_safe_label": "Contracts / invoices",
            "client_safe_description": "Please upload contracts, invoices, or equivalent evidence supporting expected transaction activity.",
        }),
        ("high_volume", "expected_transaction_flow_evidence", {
            "active": 0,
            "blocking_approval": 1,
            "mandatory": 1,
            "subject_scope": "application",
            "audience": "client",
            "requirement_type": "document",
            "client_safe_label": "Expected transaction flow evidence",
            "client_safe_description": "Please upload evidence supporting expected transaction flow.",
        }),
        ("high_volume", "major_counterparties_explanation", {
            "active": 1,
            "blocking_approval": 0,
            "mandatory": 1,
            "subject_scope": "application",
            "audience": "client",
            "requirement_type": "explanation",
            "client_safe_label": "Major counterparties explanation",
            "client_safe_description": "Please provide information about major counterparties, customer segments, suppliers, or payment participants.",
        }),
        ("high_volume", "volume_rationale_vs_business_size", {
            "active": 1,
            "blocking_approval": 1,
            "mandatory": 1,
            "subject_scope": "application",
            "audience": "client",
            "requirement_type": "explanation",
            "client_safe_label": "Volume rationale vs business size",
            "client_safe_description": "Please explain why expected volumes are proportionate to the business size, age, sector, and operating model.",
        }),
    ]
    changed = []
    for trigger_key, requirement_key, fields in updates:
        row = db.execute(
            "SELECT * FROM enhanced_requirement_rules WHERE trigger_key=? AND requirement_key=?",
            (trigger_key, requirement_key),
        ).fetchone()
        if not row:
            continue
        row_dict = dict(row)
        needs_update = False
        for field, value in fields.items():
            current = row_dict.get(field)
            if field in ("blocking_approval", "mandatory", "active"):
                current = 1 if _bool(current, False) else 0
            if current != value:
                needs_update = True
                break
        if not needs_update:
            continue
        assignments = ", ".join([f"{field}=?" for field in fields] + ["updated_by=?", "updated_at=CURRENT_TIMESTAMP"])
        db.execute(
            f"UPDATE enhanced_requirement_rules SET {assignments} WHERE id=?",
            tuple(fields.values()) + (actor_fk, row_dict["id"]),
        )
        changed.append(f"{trigger_key}:{requirement_key}")

    placeholders = ",".join("?" for _ in REMOVED_ACTIVE_ENHANCED_REQUIREMENT_KEYS)
    legacy_rows = db.execute(
        f"""
        SELECT id, trigger_key, requirement_key
        FROM enhanced_requirement_rules
        WHERE active = 1 AND requirement_key IN ({placeholders})
        """,
        tuple(sorted(REMOVED_ACTIVE_ENHANCED_REQUIREMENT_KEYS)),
    ).fetchall()
    for row in legacy_rows:
        row_dict = dict(row)
        db.execute(
            "UPDATE enhanced_requirement_rules SET active=0, updated_by=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (actor_fk, row_dict["id"]),
        )
        changed.append(f"{row_dict['trigger_key']}:{row_dict['requirement_key']}")

    if changed:
        detail = json.dumps({
            "event": "enhanced_requirement_rules.taxonomy_reconciled",
            "rule_keys": changed,
            "actor": actor,
            "timestamp": _now_iso(),
        }, sort_keys=True)
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) "
            "VALUES (?,?,?,?,?,?,?)",
            (actor, actor, "system", "enhanced_requirement_rules.taxonomy_reconciled", "Enhanced Requirement Rules", detail, ""),
        )


def _row_dict(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        pass
    keys = row.keys() if hasattr(row, "keys") else []
    return {key: row[key] for key in keys}


def _db_is_postgres(db):
    return bool(getattr(db, "is_postgres", False))


def _table_exists(db, table_name):
    try:
        if _db_is_postgres(db):
            row = db.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=?",
                (table_name,),
            ).fetchone()
            return row is not None
        row = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _column_exists(db, table_name, column_name):
    try:
        if _db_is_postgres(db):
            row = db.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name=? AND column_name=?",
                (table_name, column_name),
            ).fetchone()
            return row is not None
        rows = db.execute(f"PRAGMA table_info({table_name})").fetchall()
        for row in rows:
            data = _row_dict(row)
            if data and data.get("name") == column_name:
                return True
            try:
                if row[1] == column_name:
                    return True
            except Exception:
                continue
    except Exception:
        return False
    return False


def _audit_user(actor):
    if isinstance(actor, dict):
        return {
            "sub": actor.get("sub") or actor.get("id") or "system",
            "name": actor.get("name") or actor.get("full_name") or actor.get("sub") or "system",
            "role": actor.get("role") or "system",
        }
    text = str(actor or "system")
    return {"sub": text, "name": text, "role": "system"}


def _actor_user_fk_value(db, actor):
    """Return a FK-safe users.id for application requirement metadata.

    Portal clients and system jobs can generate enhanced requirements, but
    ``application_enhanced_requirements.created_by`` / ``updated_by`` point at
    back-office ``users``.  Keep the actor in audit metadata and write NULL to
    the FK columns unless the actor is an existing back-office user.
    """
    if db is None:
        return None
    actor_id = _clean_text(_audit_user(actor).get("sub"))
    if not actor_id:
        return None
    try:
        row = db.execute(
            "SELECT id, role FROM users WHERE id=? LIMIT 1",
            (actor_id,),
        ).fetchone()
    except Exception as exc:
        try:
            from observability import log_error

            log_error(
                "enhanced_requirement_actor_fk_validation_failed",
                handler="_actor_user_fk_value",
                actor_id=actor_id,
                error=str(exc),
                db_present=True,
            )
        except Exception as obs_exc:
            logger.debug("Observability logging failed for actor FK validation: %s", obs_exc)
        logger.warning("Could not validate enhanced requirement actor %s: %s", actor_id, exc)
        return None
    if not row:
        return None
    row = _row_dict(row)
    role = str(row.get("role") or "").strip().lower()
    if role not in APPLICATION_REQUIREMENT_FK_AUDIT_ROLES:
        return None
    return row.get("id") or actor_id


def _redact_audit_state(state):
    """Keep client free-text out of audit before/after snapshots."""
    if state is None:
        return None
    if isinstance(state, dict):
        redacted = dict(state)
        if "client_response_text" in redacted:
            redacted["client_response_text_present"] = bool(redacted.get("client_response_text"))
            redacted.pop("client_response_text", None)
        return redacted
    return state


def _insert_audit(db, action, target, detail, actor=None, before_state=None, after_state=None):
    user = _audit_user(actor)
    detail_text = json.dumps(detail or {}, default=str, sort_keys=True)
    before_state = _redact_audit_state(before_state)
    after_state = _redact_audit_state(after_state)
    has_state_columns = (
        _column_exists(db, "audit_log", "before_state")
        and _column_exists(db, "audit_log", "after_state")
    )
    if has_state_columns:
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address, before_state, after_state) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                user["sub"],
                user["name"],
                user["role"],
                action,
                target,
                detail_text,
                "",
                json.dumps(before_state, default=str, sort_keys=True) if before_state is not None else None,
                json.dumps(after_state, default=str, sort_keys=True) if after_state is not None else None,
            ),
        )
    else:
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) "
            "VALUES (?,?,?,?,?,?,?)",
            (user["sub"], user["name"], user["role"], action, target, detail_text, ""),
        )


def diagnose_enhanced_requirement_config(db):
    """Validate that Step 2 generation can safely consume settings config."""
    diagnostics = {
        "ok": False,
        "config_ok": False,
        "table_exists": False,
        "expected_trigger_groups": {},
        "missing_trigger_groups": [],
        "inactive_trigger_groups": [],
        "warnings": [],
        "errors": [],
    }

    if not _table_exists(db, "enhanced_requirement_rules"):
        diagnostics["errors"].append("enhanced_requirement_rules table is missing")
        diagnostics["missing_trigger_groups"] = list(EXPECTED_DEFAULT_TRIGGER_KEYS)
        return diagnostics

    diagnostics["table_exists"] = True
    for trigger_key in EXPECTED_DEFAULT_TRIGGER_KEYS:
        row = db.execute(
            """
            SELECT
                COUNT(*) AS total_count,
                SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END) AS active_count
            FROM enhanced_requirement_rules
            WHERE trigger_key = ?
            """,
            (trigger_key,),
        ).fetchone()
        row_dict = _row_dict(row) or {}
        total_count = int(row_dict.get("total_count") or 0)
        active_count = int(row_dict.get("active_count") or 0)
        diagnostics["expected_trigger_groups"][trigger_key] = {
            "total_count": total_count,
            "active_count": active_count,
            "ok": active_count > 0,
        }
        if total_count == 0:
            diagnostics["missing_trigger_groups"].append(trigger_key)
        elif active_count == 0:
            diagnostics["inactive_trigger_groups"].append(trigger_key)

    if diagnostics["missing_trigger_groups"]:
        diagnostics["errors"].append(
            "Missing enhanced requirement trigger group(s): "
            + ", ".join(diagnostics["missing_trigger_groups"])
        )
    if diagnostics["inactive_trigger_groups"]:
        diagnostics["errors"].append(
            "No active enhanced requirement rules for trigger group(s): "
            + ", ".join(diagnostics["inactive_trigger_groups"])
        )

    diagnostics["ok"] = not diagnostics["errors"]
    diagnostics["config_ok"] = diagnostics["ok"]
    return diagnostics


def _load_application(db, application_id):
    row = db.execute(
        "SELECT * FROM applications WHERE id = ? OR ref = ?",
        (application_id, application_id),
    ).fetchone()
    return _row_dict(row)


def _load_application_requirement(db, requirement_id):
    row = db.execute(
        "SELECT * FROM application_enhanced_requirements WHERE id = ?",
        (requirement_id,),
    ).fetchone()
    return serialize_application_requirement(row)


def _load_application_requirement_for_app(db, application_id, requirement_id):
    row = db.execute(
        """
        SELECT * FROM application_enhanced_requirements
        WHERE id = ? AND application_id = ?
        """,
        (requirement_id, application_id),
    ).fetchone()
    return serialize_application_requirement(row)


def _application_target(app):
    app = app or {}
    return "application:" + str(app.get("ref") or app.get("id") or "unknown")


_MEMO_STATUS_LABELS = {
    "generated": "Generated but not requested",
    "requested": "Requested from client",
    "uploaded": "Submitted by client",
    "under_review": "Under officer review",
    "accepted": "Accepted",
    "rejected": "Rejected / further information required",
    "waived": "Waived with reason",
    "cancelled": "Cancelled",
}


def _memo_safe_text(value, max_chars=240):
    """Return bounded memo text without raw JSON/debug payloads."""
    text = _clean_text(value)
    if not text:
        return ""
    if text[0] in "[{":
        try:
            json.loads(text)
            return ""
        except Exception:
            pass
    lowered = text.lower()
    blocked_fragments = (
        "screening_report",
        "raw_payload",
        "provider_payload",
        "trigger_context",
        "client_response_text",
        "verification_results",
    )
    if any(fragment in lowered for fragment in blocked_fragments):
        return ""
    return text[:max_chars]


def _memo_requirement_item(row):
    """Sanitize one application enhanced requirement for officer memo use."""
    item = serialize_application_requirement(row)
    if not item:
        return None
    status = str(item.get("status") or "generated").strip().lower()
    requirement_type = str(item.get("requirement_type") or "").strip().lower()
    label = _memo_safe_text(item.get("requirement_label"), 180) or item.get("requirement_key")
    trigger_label = _memo_safe_text(item.get("trigger_label"), 160) or item.get("trigger_key")
    return {
        "id": item.get("id"),
        "trigger_key": item.get("trigger_key"),
        "trigger_label": trigger_label,
        "trigger_category": item.get("trigger_category"),
        "requirement_key": item.get("requirement_key"),
        "requirement_label": label,
        "audience": item.get("audience"),
        "requirement_type": requirement_type,
        "subject_scope": item.get("subject_scope"),
        "mandatory": bool(item.get("mandatory")),
        "blocking_approval": bool(item.get("blocking_approval")),
        "waivable": bool(item.get("waivable")),
        "status": status,
        "memo_status": _MEMO_STATUS_LABELS.get(status, status.replace("_", " ").title()),
        "generation_source": item.get("generation_source"),
        "trigger_reason_summary": _memo_safe_text(item.get("trigger_reason"), 240),
        "requested_at": item.get("requested_at"),
        "uploaded_at": item.get("uploaded_at"),
        "reviewed_at": item.get("reviewed_at"),
        "reviewed_by": item.get("reviewed_by"),
        "linked_document_present": bool(item.get("linked_document_id")),
        "linked_document_id": item.get("linked_document_id"),
        "client_response_submitted": bool(
            item.get("client_response_at") or item.get("client_response_text")
        ),
        "client_response_at": item.get("client_response_at"),
        "client_response_by": item.get("client_response_by"),
        "review_notes_present": bool(item.get("review_notes")),
        "waived_at": item.get("waived_at"),
        "waived_by": item.get("waived_by"),
        "waiver_reason": _memo_safe_text(item.get("waiver_reason"), 500),
    }


def _memo_overall_status(active_items, unresolved_mandatory, unresolved_blocking):
    if not active_items:
        return "not_triggered"
    if unresolved_mandatory or unresolved_blocking:
        return "incomplete"
    statuses = {str(item.get("status") or "").lower() for item in active_items}
    if "waived" in statuses and statuses <= {"accepted", "waived"}:
        return "waived_partial"
    if statuses and statuses <= {"accepted", "waived"}:
        return "complete"
    if statuses & {"uploaded", "under_review"}:
        return "in_progress"
    if "requested" in statuses:
        return "requested"
    if "generated" in statuses:
        return "generated"
    return "incomplete"


def build_enhanced_review_memo_summary(db, application_id):
    """Build a sanitized onboarding Enhanced Review summary for memo generation.

    The summary is officer/auditor-facing but deliberately excludes raw
    trigger_context JSON, raw screening payloads, full client free text, and
    officer internal notes.  It is read-only and has no workflow side effects.
    """
    empty = {
        "triggered": False,
        "total_requirements": 0,
        "by_trigger": [],
        "requested": [],
        "submitted": [],
        "accepted": [],
        "rejected": [],
        "waived": [],
        "outstanding": [],
        "mandatory_outstanding_count": 0,
        "blocking_outstanding_count": 0,
        "client_facing_count": 0,
        "backoffice_only_count": 0,
        "document_submissions_count": 0,
        "text_responses_count": 0,
        "waiver_count": 0,
        "senior_review_items": [],
        "overall_status": "not_triggered",
        "warnings": [],
    }
    if not application_id:
        return dict(empty)
    if not _table_exists(db, "application_enhanced_requirements"):
        summary = dict(empty)
        summary["warnings"] = ["application_enhanced_requirements table is missing"]
        return summary

    rows = db.execute(
        """
        SELECT *
        FROM application_enhanced_requirements
        WHERE application_id = ?
          AND active = 1
        ORDER BY trigger_category, trigger_label, requirement_label, id
        """,
        (application_id,),
    ).fetchall()
    items = []
    for row in rows:
        item = _memo_requirement_item(row)
        if item:
            items.append(item)

    if not items:
        return dict(empty)

    unresolved_statuses = set(APPLICATION_REQUIREMENT_MEMO_UNRESOLVED_STATUSES)
    outstanding = [item for item in items if item["status"] in unresolved_statuses]
    mandatory_outstanding = [
        item for item in outstanding
        if item.get("mandatory") and item["status"] not in ("accepted", "waived")
    ]
    blocking_outstanding = [
        item for item in outstanding
        if item.get("blocking_approval") and item["status"] not in ("accepted", "waived")
    ]

    grouped = {}
    for item in items:
        key = item.get("trigger_key") or "unknown"
        group = grouped.setdefault(key, {
            "trigger_key": key,
            "trigger_label": item.get("trigger_label") or key,
            "trigger_category": item.get("trigger_category"),
            "total": 0,
            "statuses": {},
            "requirements": [],
            "trigger_reasons": [],
        })
        group["total"] += 1
        group["statuses"][item["status"]] = group["statuses"].get(item["status"], 0) + 1
        reason = item.get("trigger_reason_summary")
        if reason and reason not in group["trigger_reasons"]:
            group["trigger_reasons"].append(reason)
        group["requirements"].append(item)

    senior_review_items = [
        item for item in items
        if item.get("requirement_type") == "review_task"
        and (
            "senior" in str(item.get("requirement_label") or "").lower()
            or "senior" in str(item.get("requirement_key") or "").lower()
        )
    ]

    summary = {
        "triggered": True,
        "total_requirements": len(items),
        "by_trigger": list(grouped.values()),
        "requested": [item for item in items if item["status"] == "requested"],
        "submitted": [item for item in items if item["status"] in ("uploaded", "under_review")],
        "accepted": [item for item in items if item["status"] == "accepted"],
        "rejected": [item for item in items if item["status"] == "rejected"],
        "waived": [item for item in items if item["status"] == "waived"],
        "outstanding": outstanding,
        "mandatory_outstanding_count": len(mandatory_outstanding),
        "blocking_outstanding_count": len(blocking_outstanding),
        "client_facing_count": len([i for i in items if i.get("audience") in ("client", "both")]),
        "backoffice_only_count": len([i for i in items if i.get("audience") == "backoffice"]),
        "document_submissions_count": len([i for i in items if i.get("linked_document_present")]),
        "text_responses_count": len([i for i in items if i.get("client_response_submitted")]),
        "waiver_count": len([i for i in items if i["status"] == "waived"]),
        "senior_review_items": senior_review_items,
        "overall_status": _memo_overall_status(
            items,
            mandatory_outstanding,
            blocking_outstanding,
        ),
        "warnings": [],
    }
    if mandatory_outstanding:
        summary["warnings"].append(
            f"{len(mandatory_outstanding)} mandatory enhanced review requirement(s) remain unresolved"
        )
    if blocking_outstanding:
        summary["warnings"].append(
            f"{len(blocking_outstanding)} blocking enhanced review requirement(s) remain unresolved"
        )
    return summary


def _application_requires_enhanced_requirements(app):
    app = app or {}
    risk_level = str(app.get("risk_level") or "").strip().upper()
    status = str(app.get("status") or "").strip().lower()
    lane = str(app.get("onboarding_lane") or app.get("review_route") or "").strip().lower()
    return (
        risk_level in ("HIGH", "VERY_HIGH")
        or status in ("edd_required", "edd_approved")
        or lane == "edd"
    )


def _waiver_role_for_user(db, user_id):
    user_id = _clean_text(user_id)
    if not user_id or not _table_exists(db, "users"):
        return None
    try:
        row = db.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
    except Exception:
        return None
    data = _row_dict(row)
    return str(data.get("role") or "").strip().lower() or None


def _approval_requirement_item(item, action_needed="Resolve requirement"):
    return {
        "id": item.get("id"),
        "requirement_key": item.get("requirement_key"),
        "requirement_label": item.get("requirement_label"),
        "trigger_key": item.get("trigger_key"),
        "trigger_label": item.get("trigger_label"),
        "audience": item.get("audience"),
        "requirement_type": item.get("requirement_type"),
        "status": item.get("status"),
        "mandatory": bool(item.get("mandatory")),
        "blocking_approval": bool(item.get("blocking_approval")),
        "waived_at": item.get("waived_at"),
        "waived_by": item.get("waived_by"),
        "waiver_reason_present": bool(_clean_text(item.get("waiver_reason"))),
        "action_needed": action_needed,
    }


def _valid_approval_waiver(db, item):
    reason = _clean_text(item.get("waiver_reason"))
    waived_by = _clean_text(item.get("waived_by"))
    waived_at = _clean_text(item.get("waived_at"))
    if not reason or not waived_by or not waived_at:
        return False, "waiver requires waiver_reason, waived_by, and waived_at"
    role = _waiver_role_for_user(db, waived_by)
    if role and role not in APPLICATION_REQUIREMENT_WAIVER_ROLES:
        return False, "waived_by is not an admin or SCO"
    return True, ""


def validate_enhanced_requirements_for_approval(db, application_id, app_row=None):
    """Validate application-specific enhanced requirements for approval.

    This is a read-only approval-control helper. It does not generate missing
    requirements, change lifecycle status, create RMI/client notifications, or
    alter memo output.  Approval passes only when active mandatory/blocking
    enhanced requirements are accepted or validly waived.
    """
    result = {
        "passed": True,
        "has_requirements": False,
        "unresolved_count": 0,
        "blocking_unresolved_count": 0,
        "mandatory_unresolved_count": 0,
        "invalid_waiver_count": 0,
        "unresolved_requirements": [],
        "invalid_waivers": [],
        "warnings": [],
        "errors": [],
        "missing_generated_requirements": False,
    }
    app = _row_dict(app_row) if app_row is not None else _load_application(db, application_id)
    if not app:
        result["passed"] = False
        result["errors"].append("application_not_found")
        return result

    application_id = app.get("id") or application_id
    requires_enhanced = _application_requires_enhanced_requirements(app)

    if not _table_exists(db, "application_enhanced_requirements"):
        if requires_enhanced:
            result["passed"] = False
            result["missing_generated_requirements"] = True
            result["errors"].append(
                "enhanced review requirements table is missing; re-run migrations before approval"
            )
        else:
            result["warnings"].append("application_enhanced_requirements table is missing")
        return result

    rows = db.execute(
        """
        SELECT *
        FROM application_enhanced_requirements
        WHERE application_id = ?
          AND active = 1
        ORDER BY trigger_category, trigger_label, requirement_label, id
        """,
        (application_id,),
    ).fetchall()
    items = [serialize_application_requirement(row) for row in rows]
    items = [item for item in items if item]
    result["has_requirements"] = bool(items)

    if not items:
        if requires_enhanced:
            result["passed"] = False
            result["missing_generated_requirements"] = True
            result["errors"].append(
                "enhanced review requirements are missing or not generated for this HIGH/EDD application"
            )
        return result

    for item in items:
        status = str(item.get("status") or "generated").strip().lower()
        is_blocking = bool(item.get("mandatory")) or bool(item.get("blocking_approval"))
        if status == "cancelled":
            continue
        if status == "accepted":
            continue
        if status == "waived":
            waiver_ok, waiver_error = _valid_approval_waiver(db, item)
            if waiver_ok:
                continue
            invalid = _approval_requirement_item(item, waiver_error)
            result["invalid_waivers"].append(invalid)
            result["invalid_waiver_count"] += 1
            if is_blocking:
                result["unresolved_requirements"].append(invalid)
            continue
        if is_blocking:
            result["unresolved_requirements"].append(
                _approval_requirement_item(
                    item,
                    "Accept the requirement or record a valid senior waiver before approval",
                )
            )

    # De-duplicate invalid waivers that were also included as unresolved rows.
    seen = set()
    unresolved = []
    for item in result["unresolved_requirements"]:
        key = item.get("id") or (item.get("trigger_key"), item.get("requirement_key"))
        if key in seen:
            continue
        seen.add(key)
        unresolved.append(item)
    result["unresolved_requirements"] = unresolved
    result["unresolved_count"] = len(unresolved)
    result["mandatory_unresolved_count"] = len([item for item in unresolved if item.get("mandatory")])
    result["blocking_unresolved_count"] = len([item for item in unresolved if item.get("blocking_approval")])
    result["passed"] = not result["errors"] and result["unresolved_count"] == 0
    return result


def _blank_enhanced_operational_summary():
    return {
        "enhanced_review_active": False,
        "total": 0,
        "unresolved_count": 0,
        "mandatory_unresolved_count": 0,
        "blocking_unresolved_count": 0,
        "pending_client_count": 0,
        "submitted_awaiting_review_count": 0,
        "rejected_count": 0,
        "accepted_count": 0,
        "waived_count": 0,
        "approval_blocked": False,
        "next_action": "No enhanced review required",
        "next_action_code": "none",
        "status_label": "Clear",
        "trigger_labels": [],
        "type_counts": {
            "evidence": 0,
            "portal_disclosure": 0,
            "internal_control": 0,
        },
        "last_updated_at": None,
        "missing_generated_requirements": False,
        "invalid_waiver_count": 0,
        "unresolved_requirements": [],
        "invalid_waivers": [],
        "warnings": [],
        "errors": [],
    }


def _item_timestamp(item):
    candidates = (
        item.get("updated_at"),
        item.get("reviewed_at"),
        item.get("uploaded_at"),
        item.get("requested_at"),
        item.get("waived_at"),
        item.get("client_response_at"),
        item.get("created_at"),
    )
    return max([str(value) for value in candidates if value] or [""], default="") or None


def _approval_validation_from_items(db, app, items, *, table_missing=False):
    result = {
        "passed": True,
        "has_requirements": False,
        "unresolved_count": 0,
        "blocking_unresolved_count": 0,
        "mandatory_unresolved_count": 0,
        "invalid_waiver_count": 0,
        "unresolved_requirements": [],
        "invalid_waivers": [],
        "warnings": [],
        "errors": [],
        "missing_generated_requirements": False,
    }
    app = _row_dict(app) or {}
    if not app:
        result["passed"] = False
        result["errors"].append("application_not_found")
        return result

    requires_enhanced = _application_requires_enhanced_requirements(app)
    if table_missing:
        if requires_enhanced:
            result["passed"] = False
            result["missing_generated_requirements"] = True
            result["errors"].append(
                "enhanced review requirements table is missing; re-run migrations before approval"
            )
        else:
            result["warnings"].append("application_enhanced_requirements table is missing")
        return result

    items = [item for item in (items or []) if item]
    result["has_requirements"] = bool(items)
    if not items:
        if requires_enhanced:
            result["passed"] = False
            result["missing_generated_requirements"] = True
            result["errors"].append(
                "enhanced review requirements are missing or not generated for this HIGH/EDD application"
            )
        return result

    for item in items:
        status = str(item.get("status") or "generated").strip().lower()
        is_blocking = bool(item.get("mandatory")) or bool(item.get("blocking_approval"))
        if status == "cancelled":
            continue
        if status == "accepted":
            continue
        if status == "waived":
            waiver_ok, waiver_error = _valid_approval_waiver(db, item)
            if waiver_ok:
                continue
            invalid = _approval_requirement_item(item, waiver_error)
            result["invalid_waivers"].append(invalid)
            result["invalid_waiver_count"] += 1
            if is_blocking:
                result["unresolved_requirements"].append(invalid)
            continue
        if is_blocking:
            result["unresolved_requirements"].append(
                _approval_requirement_item(
                    item,
                    "Accept the requirement or record a valid senior waiver before approval",
                )
            )

    seen = set()
    unresolved = []
    for item in result["unresolved_requirements"]:
        key = item.get("id") or (item.get("trigger_key"), item.get("requirement_key"))
        if key in seen:
            continue
        seen.add(key)
        unresolved.append(item)
    result["unresolved_requirements"] = unresolved
    result["unresolved_count"] = len(unresolved)
    result["mandatory_unresolved_count"] = len([item for item in unresolved if item.get("mandatory")])
    result["blocking_unresolved_count"] = len([item for item in unresolved if item.get("blocking_approval")])
    result["passed"] = not result["errors"] and result["unresolved_count"] == 0
    return result


def _build_enhanced_operational_summary_from_items(db, app, items, validation=None):
    app = _row_dict(app) or {}
    active_items = []
    for item in items or []:
        serialized = serialize_application_requirement(item) if not isinstance(item, dict) else dict(item)
        if serialized and serialized.get("active") is not False:
            active_items.append(serialized)

    validation = validation or _approval_validation_from_items(db, app, active_items)
    summary = _blank_enhanced_operational_summary()
    summary["total"] = len(active_items)
    summary["enhanced_review_active"] = bool(active_items) or bool(validation.get("missing_generated_requirements"))
    summary["approval_blocked"] = not bool(validation.get("passed", True))
    summary["missing_generated_requirements"] = bool(validation.get("missing_generated_requirements"))
    summary["mandatory_unresolved_count"] = int(validation.get("mandatory_unresolved_count") or 0)
    summary["blocking_unresolved_count"] = int(validation.get("blocking_unresolved_count") or 0)
    summary["invalid_waiver_count"] = int(validation.get("invalid_waiver_count") or 0)
    summary["unresolved_requirements"] = validation.get("unresolved_requirements") or []
    summary["invalid_waivers"] = validation.get("invalid_waivers") or []
    summary["warnings"] = validation.get("warnings") or []
    summary["errors"] = validation.get("errors") or []

    unresolved_statuses = set(APPLICATION_REQUIREMENT_APPROVAL_UNRESOLVED_STATUSES)
    trigger_labels = []
    timestamps = []
    rejected_client_facing = 0
    under_review_count = 0
    optional_unresolved_count = 0

    for item in active_items:
        status = str(item.get("status") or "generated").strip().lower()
        audience = str(item.get("audience") or "").strip().lower()
        client_facing = audience in APPLICATION_REQUIREMENT_REQUESTABLE_AUDIENCES
        is_blocking = bool(item.get("mandatory")) or bool(item.get("blocking_approval"))
        if status in unresolved_statuses:
            summary["unresolved_count"] += 1
            if not is_blocking:
                optional_unresolved_count += 1
        if status == "requested" and client_facing:
            summary["pending_client_count"] += 1
        if status == "uploaded":
            summary["submitted_awaiting_review_count"] += 1
        if status == "rejected":
            summary["rejected_count"] += 1
            if client_facing:
                rejected_client_facing += 1
        if status == "accepted":
            summary["accepted_count"] += 1
        if status == "waived":
            summary["waived_count"] += 1
        if status == "under_review":
            under_review_count += 1
        display_type = classify_requirement_presentation_type(item)
        if display_type in summary["type_counts"]:
            summary["type_counts"][display_type] += 1
        label = _clean_text(item.get("trigger_label") or item.get("trigger_key"))
        if label and label not in trigger_labels:
            trigger_labels.append(label)
        ts = _item_timestamp(item)
        if ts:
            timestamps.append(ts)

    summary["trigger_labels"] = trigger_labels
    summary["last_updated_at"] = max(timestamps) if timestamps else None

    if summary["missing_generated_requirements"]:
        summary["next_action_code"] = "generate_requirements"
        summary["next_action"] = "Generate enhanced review requirements"
        summary["status_label"] = "Approval blocked"
    elif rejected_client_facing:
        summary["next_action_code"] = "request_updated_info"
        summary["next_action"] = "Request updated information from client"
        summary["status_label"] = "Approval blocked" if summary["approval_blocked"] else "Pending client"
    elif summary["pending_client_count"]:
        summary["next_action_code"] = "awaiting_client"
        summary["next_action"] = "Awaiting client submission"
        summary["status_label"] = "Pending client"
    elif summary["submitted_awaiting_review_count"]:
        summary["next_action_code"] = "review_submitted"
        summary["next_action"] = "Review submitted enhanced requirement evidence"
        summary["status_label"] = "Awaiting review"
    elif under_review_count:
        summary["next_action_code"] = "complete_review"
        summary["next_action"] = "Complete officer review"
        summary["status_label"] = "Under review"
    elif summary["invalid_waiver_count"]:
        summary["next_action_code"] = "fix_invalid_waiver"
        summary["next_action"] = "Fix invalid waiver"
        summary["status_label"] = "Approval blocked"
    elif summary["approval_blocked"]:
        summary["next_action_code"] = "resolve_blockers"
        summary["next_action"] = "Resolve outstanding enhanced review requirements"
        summary["status_label"] = "Approval blocked"
    elif summary["enhanced_review_active"] and not summary["approval_blocked"]:
        summary["next_action_code"] = "resolved"
        summary["next_action"] = "Enhanced review resolved"
        summary["status_label"] = "Resolved" if optional_unresolved_count == 0 else "Enhanced review resolved"
    else:
        summary["next_action_code"] = "none"
        summary["next_action"] = "No enhanced review required"
        summary["status_label"] = "Clear"

    if summary["approval_blocked"] and summary["status_label"] not in ("Pending client", "Awaiting review", "Under review"):
        summary["status_label"] = "Approval blocked"
    return summary


def build_enhanced_requirement_operational_summary(db, application_id, app_row=None):
    """Build read-only back-office operational visibility for enhanced requirements."""
    app = _row_dict(app_row) if app_row is not None else _load_application(db, application_id)
    if not app:
        summary = _blank_enhanced_operational_summary()
        summary["approval_blocked"] = True
        summary["errors"] = ["application_not_found"]
        summary["next_action_code"] = "error"
        summary["next_action"] = "Application not found"
        summary["status_label"] = "Unavailable"
        return summary

    app_id = app.get("id") or application_id
    if not _table_exists(db, "application_enhanced_requirements"):
        validation = _approval_validation_from_items(db, app, [], table_missing=True)
        return _build_enhanced_operational_summary_from_items(db, app, [], validation)

    rows = db.execute(
        """
        SELECT *
        FROM application_enhanced_requirements
        WHERE application_id = ?
          AND active = 1
        ORDER BY trigger_category, trigger_label, requirement_label, id
        """,
        (app_id,),
    ).fetchall()
    items = [serialize_application_requirement(row) for row in rows]
    validation = validate_enhanced_requirements_for_approval(db, app_id, app_row=app)
    return _build_enhanced_operational_summary_from_items(db, app, items, validation)


def build_enhanced_requirement_operational_summaries(db, app_rows):
    """Build operational summaries for a page of applications without N+1 row loads."""
    apps = [_row_dict(app) for app in (app_rows or []) if _row_dict(app)]
    if not apps:
        return {}
    summaries = {}
    if not _table_exists(db, "application_enhanced_requirements"):
        for app in apps:
            validation = _approval_validation_from_items(db, app, [], table_missing=True)
            summaries[app.get("id")] = _build_enhanced_operational_summary_from_items(db, app, [], validation)
        return summaries

    app_ids = [app.get("id") for app in apps if app.get("id")]
    grouped = {app_id: [] for app_id in app_ids}
    if app_ids:
        placeholders = ",".join("?" for _ in app_ids)
        rows = db.execute(
            f"""
            SELECT *
            FROM application_enhanced_requirements
            WHERE active = 1
              AND application_id IN ({placeholders})
            ORDER BY trigger_category, trigger_label, requirement_label, id
            """,
            app_ids,
        ).fetchall()
        for row in rows:
            item = serialize_application_requirement(row)
            if item:
                grouped.setdefault(item.get("application_id"), []).append(item)

    for app in apps:
        items = grouped.get(app.get("id"), [])
        validation = _approval_validation_from_items(db, app, items)
        summaries[app.get("id")] = _build_enhanced_operational_summary_from_items(db, app, items, validation)
    return summaries


def format_enhanced_requirements_approval_error(validation):
    validation = validation or {}
    if validation.get("missing_generated_requirements"):
        return (
            "Enhanced review requirements are missing or not generated. "
            "Re-run enhanced requirement generation before approval."
        )
    unresolved = validation.get("unresolved_requirements") or []
    invalid_waivers = validation.get("invalid_waivers") or []
    if not unresolved and invalid_waivers:
        unresolved = invalid_waivers
    sample_parts = []
    for item in unresolved[:5]:
        label = item.get("requirement_label") or item.get("requirement_key") or "Enhanced requirement"
        status = item.get("status") or "unknown"
        flags = []
        if item.get("mandatory"):
            flags.append("mandatory")
        if item.get("blocking_approval"):
            flags.append("blocking")
        flag_text = f"; {', '.join(flags)}" if flags else ""
        sample_parts.append(f"{label} ({status}{flag_text})")
    detail = "; ".join(sample_parts)
    if len(unresolved) > 5:
        detail += f"; +{len(unresolved) - 5} more"
    counts = (
        f"mandatory unresolved={validation.get('mandatory_unresolved_count', 0)}, "
        f"blocking unresolved={validation.get('blocking_unresolved_count', 0)}, "
        f"invalid waivers={validation.get('invalid_waiver_count', 0)}"
    )
    if detail:
        return (
            "Onboarding Enhanced Review requirements remain unresolved. "
            f"{counts}. Items: {detail}. "
            "Accept mandatory/blocking requirements or record a valid senior waiver before approval."
        )
    return (
        "Onboarding Enhanced Review requirements remain unresolved. "
        f"{counts}. Accept mandatory/blocking requirements or record a valid senior waiver before approval."
    )


def audit_enhanced_requirements_approval_block(db, app, validation, actor=None):
    """Write a focused audit row for an approval attempt blocked by enhanced requirements."""
    app = _row_dict(app) or {}
    validation = validation or {}
    detail = {
        "event": "approval.blocked.enhanced_requirements",
        "application_id": app.get("id"),
        "application_ref": app.get("ref"),
        "unresolved_count": validation.get("unresolved_count", 0),
        "mandatory_unresolved_count": validation.get("mandatory_unresolved_count", 0),
        "blocking_unresolved_count": validation.get("blocking_unresolved_count", 0),
        "invalid_waiver_count": validation.get("invalid_waiver_count", 0),
        "missing_generated_requirements": bool(validation.get("missing_generated_requirements")),
        "unresolved_requirements": validation.get("unresolved_requirements", [])[:20],
        "invalid_waivers": validation.get("invalid_waivers", [])[:20],
        "warnings": validation.get("warnings", []),
        "errors": validation.get("errors", []),
        "actor": _audit_user(actor),
        "timestamp": _now_iso(),
    }
    _insert_audit(
        db,
        "approval.blocked.enhanced_requirements",
        _application_target(app),
        detail,
        actor=actor,
    )
    return detail


def _is_yes(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in ("yes", "true", "1", "y")


def _party_row_declared_or_confirmed_pep(row):
    row = row or {}
    declaration = _loads_json(row.get("pep_declaration"), {}) or {}
    if not isinstance(declaration, dict):
        declaration = {}
    status = str(declaration.get("pep_status") or "").strip().lower()
    values = [declaration.get(key) for key in ("client_declared_pep", "declared_pep", "officer_verified_pep", "verified_pep")]
    if any(_is_yes(value) for value in values):
        return True
    if status in ("declared_yes", "confirmed_pep"):
        return True
    if any(str(value).strip().lower() in ("no", "false", "0", "n") for value in values):
        return False
    if status in ("declared_no", "false_positive", "not_pep", "pending_review", "not_verified"):
        return False
    return not declaration and _is_yes(row.get("is_pep"))


def _declared_pep_present(db, application_id):
    try:
        rows = db.execute(
            """
            SELECT is_pep, pep_declaration FROM directors WHERE application_id=?
            UNION ALL
            SELECT is_pep, pep_declaration FROM ubos WHERE application_id=?
            """,
            (application_id, application_id),
        ).fetchall()
    except Exception:
        return False
    return any(_party_row_declared_or_confirmed_pep(_row_dict(row)) for row in rows)


def _prescreening_dict(app):
    return _loads_json((app or {}).get("prescreening_data"), {}) or {}


def _screening_summary_from_app(app):
    prescreening = _prescreening_dict(app)
    report = prescreening.get("screening_report") if isinstance(prescreening, dict) else {}
    if not isinstance(report, dict):
        report = _loads_json(report, {})
    existing = prescreening.get("screening_terminality_summary") if isinstance(prescreening, dict) else {}
    if report:
        try:
            from screening_state import build_screening_terminality_summary

            return build_screening_terminality_summary(report, prescreening)
        except Exception:
            logger.exception("Failed to build canonical screening terminality summary")
    if isinstance(existing, dict) and existing:
        return existing
    try:
        total_hits = int(report.get("total_hits") or 0)
    except Exception:
        total_hits = 0
    return {
        "terminal": bool(report),
        "has_terminal_match": total_hits > 0,
        "has_non_terminal": False,
    }


def _ownership_transparency_status(app):
    raw = (
        (app or {}).get("ownership_transparency_status")
        or (app or {}).get("ownership_structure")
        or ""
    )
    text = str(raw).strip().lower()
    if text in ("opaque", "incomplete", "unknown", "high"):
        return text
    opaque_tokens = (
        "complex",
        "shell",
        "opaque",
        "nominee",
        "bearer",
        "multi-layered",
        "layered",
        "trust",
        "3+",
        "undisclosed",
    )
    if any(token in text for token in opaque_tokens):
        return "opaque"
    if text in ("simple", "transparent", "clear", "1-2"):
        return "clear"
    return text


def _jurisdiction_risk_tier(app):
    existing = (app or {}).get("jurisdiction_risk_tier")
    if existing:
        return str(existing).strip().lower()
    try:
        from rule_engine import classify_country

        score = classify_country((app or {}).get("country"))
    except Exception as exc:
        from rule_engine import RiskConfigUnavailable
        if isinstance(exc, RiskConfigUnavailable):
            # DCI-008: don't silently build the EDD requirement set with an
            # empty jurisdiction tier — fail the generation attempt instead.
            raise
        return ""
    if score >= 4:
        return "very_high"
    if score >= 3:
        return "high"
    if score <= 1:
        return "low"
    return "standard"


def _sector_risk_tier(app):
    existing = (app or {}).get("sector_risk_tier")
    if existing:
        return str(existing).strip().lower()
    try:
        from rule_engine import score_sector

        score = score_sector((app or {}).get("sector"))
    except Exception:
        return ""
    if score >= 4:
        return "high"
    if score == 3:
        return "elevated"
    if score <= 1:
        return "low"
    return "standard"


HIGH_VOLUME_THRESHOLD = 500000


def _expected_volume_values(app):
    app = app or {}
    prescreening = _prescreening_dict(app)
    values = [
        app.get("monthly_volume"),
        app.get("expected_volume"),
    ]
    if isinstance(prescreening, dict):
        values.extend([
            prescreening.get("monthly_volume"),
            prescreening.get("expected_volume"),
            prescreening.get("expected_volumes"),
        ])
        transaction = _loads_json(prescreening.get("transaction"), {})
        if isinstance(transaction, dict):
            expected = _loads_json(transaction.get("expected_monthly_volume"), {})
            if isinstance(expected, dict):
                values.extend([
                    expected.get("band_legacy"),
                    expected.get("label"),
                    expected.get("value"),
                ])
            elif expected:
                values.append(expected)
    return [str(value).strip() for value in values if str(value or "").strip()]


def _parse_volume_amount(token):
    token = str(token or "").strip().lower().replace(",", "")
    suffix = ""
    if token.endswith(("k", "m")):
        suffix = token[-1]
        token = token[:-1]
    try:
        amount = float(token)
    except (TypeError, ValueError):
        return None
    if suffix == "k":
        amount *= 1000
    elif suffix == "m":
        amount *= 1000000
    return int(amount)


def _expected_volume_assessment(raw_value, threshold=HIGH_VOLUME_THRESHOLD):
    raw = str(raw_value or "").strip()
    if not raw:
        return {
            "raw": raw,
            "parsed": False,
            "is_high_volume": False,
            "reason": "expected_volume_missing",
            "threshold": threshold,
        }

    text = raw.lower()
    text = (
        text.replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
        .replace("per month", "")
        .replace("monthly", "")
    )
    compact = re.sub(r"\s+", "", text)
    amount_matches = re.findall(
        r"(?<![a-z])(\d+(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)(\s*[km])?",
        text,
    )
    amounts = [
        _parse_volume_amount((number or "") + (suffix or "").strip())
        for number, suffix in amount_matches
    ]
    amounts = [amount for amount in amounts if amount is not None]
    if not amounts:
        return {
            "raw": raw,
            "parsed": False,
            "is_high_volume": False,
            "reason": "expected_volume_unparsed",
            "threshold": threshold,
        }

    has_range = bool(re.search(r"\d\s*-\s*\d", compact) or re.search(r"\bto\b", text))
    is_upper_bound = bool(
        re.search(r"\b(under|below|less\s+than|up\s+to|upto|not\s+more\s+than)\b", text)
        or "<" in text
    )
    is_lower_bound = bool(
        re.search(r"\b(over|above|more\s+than|greater\s+than|at\s+least|minimum)\b", text)
        or ">" in text
        or re.search(r"\d\s*[km]?\s*\+", text)
    )
    normalized = max(amounts) if has_range else amounts[0]

    if is_upper_bound:
        return {
            "raw": raw,
            "parsed": True,
            "is_high_volume": False,
            "normalized_amount": normalized,
            "threshold": threshold,
            "reason": (
                "expected_volume_upper_bound_below_threshold"
                if normalized < threshold
                else "expected_volume_upper_bound_not_definite_high"
            ),
        }

    if has_range:
        is_high = normalized >= threshold
        return {
            "raw": raw,
            "parsed": True,
            "is_high_volume": is_high,
            "normalized_amount": normalized,
            "threshold": threshold,
            "reason": (
                "expected_volume_range_max_gte_threshold"
                if is_high
                else "expected_volume_range_max_below_threshold"
            ),
        }

    is_high = normalized >= threshold
    if is_lower_bound:
        reason = (
            "expected_volume_lower_bound_gte_threshold"
            if is_high
            else "expected_volume_lower_bound_below_threshold"
        )
    else:
        reason = (
            "expected_volume_value_gte_threshold"
            if is_high
            else "expected_volume_value_below_threshold"
        )
    return {
        "raw": raw,
        "parsed": True,
        "is_high_volume": is_high,
        "normalized_amount": normalized,
        "threshold": threshold,
        "reason": reason,
    }


def _declared_high_volume_context(app):
    assessments = [_expected_volume_assessment(value) for value in _expected_volume_values(app)]
    high = next((item for item in assessments if item.get("is_high_volume")), None)
    if high:
        return {
            "is_high_volume": True,
            "reason": high.get("reason"),
            "normalized_amount": high.get("normalized_amount"),
            "threshold": high.get("threshold"),
            "raw": high.get("raw"),
            "assessments": assessments,
        }
    parsed = next((item for item in assessments if item.get("parsed")), None)
    return {
        "is_high_volume": False,
        "reason": (parsed or {}).get("reason") or "expected_volume_missing",
        "normalized_amount": (parsed or {}).get("normalized_amount"),
        "threshold": HIGH_VOLUME_THRESHOLD,
        "raw": (parsed or {}).get("raw"),
        "assessments": assessments,
    }


def _declared_high_volume(app):
    return bool(_declared_high_volume_context(app).get("is_high_volume"))


def _declared_high_volume_reason(app):
    context = _declared_high_volume_context(app)
    if not context.get("is_high_volume"):
        return ""
    parts = [
        "declared_high_volume",
        "reason=" + str(context.get("reason") or "expected_volume_high"),
    ]
    if context.get("normalized_amount") is not None:
        parts.append("normalized_amount=" + str(context.get("normalized_amount")))
    parts.append("threshold=" + str(context.get("threshold") or HIGH_VOLUME_THRESHOLD))
    if context.get("raw"):
        parts.append("raw=" + str(context.get("raw")))
    return ";".join(parts)


def _routing_for_application(db, app):
    app = dict(app or {})
    risk_dict = {
        "score": app.get("risk_score"),
        "level": app.get("final_risk_level") or app.get("risk_level") or "",
        "final_risk_level": app.get("final_risk_level") or app.get("risk_level") or "",
        "base_risk_level": app.get("base_risk_level") or app.get("risk_level") or "",
        "sector_label": app.get("sector") or "",
        "sector_risk_tier": _sector_risk_tier(app),
        "jurisdiction_risk_tier": _jurisdiction_risk_tier(app),
        "ownership_transparency_status": _ownership_transparency_status(app),
        "declared_pep_present": _declared_pep_present(db, app.get("id")),
    }
    screening_summary = _screening_summary_from_app(app)
    try:
        from routing_actuator import build_routing_facts
        from edd_routing_policy import evaluate_edd_routing

        facts = build_routing_facts(
            db=db,
            app_row=app,
            risk_dict=risk_dict,
            screening_summary=screening_summary,
        )
        routing = evaluate_edd_routing(facts)
    except Exception as exc:
        logger.warning("Enhanced requirement routing detection failed: %s", exc)
        routing = {
            "policy_version": "edd_routing_policy_v1",
            "route": "standard",
            "triggers": [],
            "inputs": risk_dict,
            "evaluated_at": _now_iso(),
            "errors": [str(exc)],
        }
    return routing


def _routing_for_generation(db, app, routing=None):
    """Return routing context for requirement generation.

    Automatic callers often have a freshly evaluated routing decision with
    in-memory risk facts that have not been persisted yet.  The application
    row can still contain durable facts such as declared PEPs or high-volume
    declarations.  Merge both views so generation remains conservative but
    does not lose either source.
    """
    if not routing:
        return _routing_for_application(db, app)

    merged = dict(routing or {})
    merged_triggers = []
    for trigger in list(merged.get("triggers") or []):
        if trigger not in merged_triggers:
            merged_triggers.append(trigger)

    try:
        app_routing = _routing_for_application(db, app)
    except Exception as exc:
        app_routing = {
            "route": "standard",
            "triggers": [],
            "errors": [str(exc)],
        }

    app_triggers = []
    for trigger in list((app_routing or {}).get("triggers") or []):
        app_triggers.append(trigger)
        if trigger not in merged_triggers:
            merged_triggers.append(trigger)

    merged["triggers"] = merged_triggers
    if (app_routing or {}).get("route") == "edd":
        merged["route"] = "edd"
    if app_triggers:
        merged["application_detected_triggers"] = app_triggers
    return merged


def _resolve_requirement_triggers(app, routing):
    mapped = {}
    warnings = []
    routing = routing or {}
    for source_trigger in list(routing.get("triggers") or []):
        target = EDD_TRIGGER_TO_REQUIREMENT_TRIGGER.get(source_trigger)
        if target:
            mapped.setdefault(target, []).append(source_trigger)
            continue
        if source_trigger in SCREENING_INDEPENDENT_ROUTING_TRIGGERS:
            continue
        if source_trigger == "high_risk_sector":
            if "crypto_vasp" not in mapped:
                warnings.append("Unmapped EDD routing trigger: high_risk_sector")
            continue
        if source_trigger.startswith("edd_flag:"):
            warnings.append("Unmapped EDD routing trigger: " + source_trigger)
            continue
        warnings.append("Unmapped EDD routing trigger: " + str(source_trigger))

    high_volume_reason = _declared_high_volume_reason(app)
    if high_volume_reason:
        mapped.setdefault("high_volume", []).append(high_volume_reason)

    ordered = [key for key in EXPECTED_DEFAULT_TRIGGER_KEYS if key in mapped]
    for key in sorted(k for k in mapped if k not in ordered):
        ordered.append(key)
    return ordered, mapped, warnings


def detect_application_enhanced_requirement_triggers(
    db,
    application_id=None,
    app_row=None,
    routing=None,
):
    """Resolve enhanced requirement trigger keys without writing records."""
    app = _row_dict(app_row) if app_row is not None else _load_application(db, application_id)
    result = {
        "application_id": application_id,
        "triggers": [],
        "trigger_sources": {},
        "routing": None,
        "warnings": [],
        "errors": [],
    }
    if not app:
        result["errors"].append("application_not_found")
        return result

    result["application_id"] = app.get("id") or application_id
    routing_decision = _routing_for_generation(db, app, routing)
    triggers, trigger_sources, warnings = _resolve_requirement_triggers(app, routing_decision)
    result["triggers"] = triggers
    result["trigger_sources"] = trigger_sources
    result["routing"] = routing_decision
    result["warnings"] = warnings
    return result


def _validate_requirement_transition(current_status, new_status, actor_role):
    current = str(current_status or "generated").strip().lower()
    target = str(new_status or "").strip().lower()
    if target not in APPLICATION_REQUIREMENT_STATUSES:
        return f"Invalid enhanced requirement status: {target}"
    if target == current:
        return None
    allowed = APPLICATION_REQUIREMENT_STATUS_TRANSITIONS.get(current, ())
    if target not in allowed:
        return f"Invalid enhanced requirement status transition: {current} -> {target}"
    if target == "waived" and actor_role not in APPLICATION_REQUIREMENT_WAIVER_ROLES:
        return "Only admin or SCO can waive enhanced requirements"
    if current == "waived" and target == "under_review" and actor_role not in APPLICATION_REQUIREMENT_WAIVER_ROLES:
        return "Only admin or SCO can reopen waived enhanced requirements"
    if current == "accepted" and target == "under_review" and actor_role not in APPLICATION_REQUIREMENT_WAIVER_ROLES:
        return "Only admin or SCO can reopen accepted enhanced requirements"
    return None


def _audit_requirement_update_payload(app, before, after, actor, changes):
    before = before or {}
    after = after or {}
    user = _audit_user(actor)
    return {
        "application_id": app.get("id") if app else before.get("application_id"),
        "application_ref": app.get("ref") if app else None,
        "requirement_id": after.get("id") or before.get("id"),
        "requirement_key": after.get("requirement_key") or before.get("requirement_key"),
        "trigger_key": after.get("trigger_key") or before.get("trigger_key"),
        "old_status": before.get("status"),
        "new_status": after.get("status"),
        "linked_document_id": after.get("linked_document_id"),
        "actor": user.get("sub"),
        "timestamp": _now_iso(),
        "changes": changes,
    }


_CLIENT_UNSAFE_LABEL_TERMS = (
    "adverse media",
    "approval blocker",
    "back-office",
    "backoffice",
    "edd",
    "enhanced due diligence",
    "fatf",
    "false-positive",
    "false positive",
    "internal",
    "officer",
    "high risk",
    "high-risk",
    "pep",
    "politically exposed",
    "risk level",
    "sanction",
    "sanctions",
    "screening",
    "screening concern",
    "senior review",
    "trigger",
    "very high",
    "very_high",
    "waiver",
)

_CLIENT_UNSAFE_DESCRIPTION_TERMS = _CLIENT_UNSAFE_LABEL_TERMS + (
    "approval",
    "block approval",
    "sanctions",
    "screening risk",
)


def _text_contains_any(text, terms):
    normalized = str(text or "").strip().lower()
    return any(term in normalized for term in terms)


_PORTAL_SAFE_COPY_BY_REQUIREMENT_KEY = {
    "pep_declaration_details": (
        "Additional declaration details",
        "Please provide the requested declaration details so our team can complete the review.",
    ),
    "pep_role_position": (
        "Role and public-position information",
        "Please provide the role, position, and related context requested for the relevant person.",
    ),
    "pep_jurisdiction": (
        "Public-position jurisdiction information",
        "Please provide the jurisdiction details requested for the relevant person.",
    ),
    "pep_sow_evidence": (
        "Source of wealth evidence",
        "Please upload evidence supporting the source of wealth for the relevant person.",
    ),
    "pep_linked_sof_evidence": (
        "Source of funds evidence",
        "Please upload evidence supporting the source of funds for the proposed relationship.",
    ),
    "jurisdiction_exposure_rationale": (
        "Country of incorporation information",
        "Required for certain countries of incorporation.",
    ),
}


def _portal_safe_fallback_copy(requirement):
    requirement = requirement or {}
    key = str(requirement.get("requirement_key") or "").strip().lower()
    if key in _PORTAL_SAFE_COPY_BY_REQUIREMENT_KEY:
        label, description = _PORTAL_SAFE_COPY_BY_REQUIREMENT_KEY[key]
        return {"label": label, "description": description}

    requirement_type = str(requirement.get("requirement_type") or "").strip().lower()
    if requirement_type == "document":
        return {
            "label": "Supporting document",
            "description": "Please upload the requested supporting document.",
        }
    if requirement_type in ("declaration", "explanation"):
        return {
            "label": "Additional information",
            "description": "Please provide the requested information so our team can complete the review.",
        }
    return {
        "label": "Additional information required",
        "description": "Please provide the requested information so our team can complete the review.",
    }


def _load_requirement_source_rule(db, requirement):
    rule_id = (requirement or {}).get("source_rule_id")
    if rule_id in (None, ""):
        return {}
    try:
        row = db.execute(
            "SELECT client_safe_label, client_safe_description FROM enhanced_requirement_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
    except Exception:
        return {}
    return _row_dict(row) or {}


def _client_safe_requirement_fields(db, requirement):
    """Return client-safe request copy without exposing routing/internal context."""
    requirement = requirement or {}
    rule = _load_requirement_source_rule(db, requirement)
    label = _clean_text(rule.get("client_safe_label") or requirement.get("requirement_label"))
    used_fallback = False
    if not label:
        fallback = _portal_safe_fallback_copy(requirement)
        label = fallback["label"]
        used_fallback = True
    if _text_contains_any(label, _CLIENT_UNSAFE_LABEL_TERMS):
        fallback = _portal_safe_fallback_copy(requirement)
        label = fallback["label"]
        used_fallback = True

    description = _clean_text(rule.get("client_safe_description"))
    if not description:
        fallback_description = _clean_text(requirement.get("requirement_description"))
        if not _text_contains_any(fallback_description, _CLIENT_UNSAFE_DESCRIPTION_TERMS):
            description = fallback_description
    if not description or _text_contains_any(description, _CLIENT_UNSAFE_DESCRIPTION_TERMS):
        fallback = _portal_safe_fallback_copy(requirement)
        description = fallback["description"]
        used_fallback = True
    if _text_contains_any(label, _CLIENT_UNSAFE_LABEL_TERMS) or _text_contains_any(description, _CLIENT_UNSAFE_DESCRIPTION_TERMS):
        return None, "No safe client wording available for enhanced requirement"
    if used_fallback:
        logger.warning(
            "portal_enhanced_requirement_safe_fallback=true requirement_id=%s requirement_key=%s",
            requirement.get("id"),
            requirement.get("requirement_key"),
        )

    return {
        "label": label,
        "description": description,
        "audience": requirement.get("audience"),
        "requirement_type": requirement.get("requirement_type"),
        "subject_scope": requirement.get("subject_scope"),
    }, None


_PORTAL_STATUS_LABELS = {
    "requested": ("required", "Required"),
    "uploaded": ("submitted", "Submitted"),
    "under_review": ("under_review", "Under review"),
    "accepted": ("accepted", "Accepted / verified"),
    "rejected": ("additional_information_needed", "Additional information needed"),
}

_PORTAL_REQUIREMENT_TYPES = {
    "document": "document",
    "declaration": "declaration",
    "explanation": "explanation",
    "review_task": "information",
    "internal_control": "information",
}

_PORTAL_SUBJECT_SCOPES = {
    "company": "company",
    "ubo": "beneficial_owner",
    "director": "director",
    "controller": "controller",
    "application": "application",
    "screening_subject": "person",
    "intermediary": "intermediary",
}


def serialize_portal_application_requirement(db, row):
    """Return a client-safe portal representation of one requested requirement."""
    requirement = serialize_application_requirement(row)
    if not requirement:
        return None

    client_request, safe_error = _client_safe_requirement_fields(db, requirement)
    if safe_error:
        logger.warning(
            "portal_enhanced_requirement_unsafe_skip=true requirement_id=%s reason=%s",
            requirement.get("id"),
            safe_error,
        )
        return None

    backend_status = str(requirement.get("status") or "").strip().lower()
    status_key, status_label = _PORTAL_STATUS_LABELS.get(
        backend_status,
        ("required", "Required"),
    )
    requirement_type = _PORTAL_REQUIREMENT_TYPES.get(
        str(requirement.get("requirement_type") or "").strip().lower(),
        "information",
    )
    subject_scope = _PORTAL_SUBJECT_SCOPES.get(
        str(requirement.get("subject_scope") or "").strip().lower()
    )

    result = {
        "id": requirement.get("id"),
        "label": client_request.get("label"),
        "description": client_request.get("description") or "",
        "requirement_type": requirement_type,
        "status": status_key,
        "status_label": status_label,
        "due_date": requirement.get("due_date") or (
            requirement.get("trigger_context", {}) if isinstance(requirement.get("trigger_context"), dict) else {}
        ).get("due_date"),
        "request_reason": (
            requirement.get("trigger_context", {}) if isinstance(requirement.get("trigger_context"), dict) else {}
        ).get("request_reason"),
        "requested_at": requirement.get("requested_at"),
        "uploaded_at": requirement.get("uploaded_at"),
        "reviewed_at": requirement.get("reviewed_at"),
    }
    if subject_scope:
        result["subject_scope"] = subject_scope
    if requirement.get("subject"):
        subject = requirement.get("subject") or {}
        if subject.get("name"):
            result["subject_name"] = subject.get("name")
            result["owner_label"] = subject.get("name")
        if subject.get("type"):
            result["subject_type"] = subject.get("type")
    if requirement.get("linked_document_id"):
        result["linked_document_id"] = requirement.get("linked_document_id")
        try:
            doc_row = db.execute(
                """
                SELECT id, doc_type, doc_name, uploaded_at, verification_status, review_status
                FROM documents
                WHERE id = ? AND application_id = ?
                """,
                (requirement.get("linked_document_id"), requirement.get("application_id")),
            ).fetchone()
            doc = _row_dict(doc_row) or {}
            if doc:
                result["linked_document"] = {
                    "id": doc.get("id"),
                    "doc_type": doc.get("doc_type"),
                    "doc_name": doc.get("doc_name"),
                    "uploaded_at": doc.get("uploaded_at"),
                    "verification_status": doc.get("verification_status"),
                    "verification_status_label": _verification_status_label(doc.get("verification_status")),
                    "verification_status_tone": _verification_status_tone(doc.get("verification_status")),
                    "review_status": doc.get("review_status"),
                }
        except Exception:
            pass
    return result


def list_portal_application_enhanced_requirements(db, application_id, *, exclude_linked_periodic_review=False):
    """List only client-visible requested enhanced requirements for the portal."""
    placeholders = ",".join(["?"] * len(APPLICATION_REQUIREMENT_PORTAL_VISIBLE_STATUSES))
    periodic_review_filter = "AND aer.linked_periodic_review_id IS NULL" if exclude_linked_periodic_review else ""
    rows = db.execute(
        f"""
        SELECT aer.*, err.active AS source_rule_active
        FROM application_enhanced_requirements aer
        LEFT JOIN enhanced_requirement_rules err ON err.id = aer.source_rule_id
        WHERE aer.application_id = ?
          AND aer.active = 1
          AND aer.audience IN ('client', 'both')
          AND aer.requirement_type NOT IN ('review_task', 'internal_control')
          AND aer.status IN ({placeholders})
          {periodic_review_filter}
        ORDER BY aer.requested_at DESC, aer.updated_at DESC, aer.requirement_label, aer.id
        """,
        (application_id, *APPLICATION_REQUIREMENT_PORTAL_VISIBLE_STATUSES),
    ).fetchall()

    requirements = []
    for row in rows:
        item = serialize_application_requirement(row)
        if (
            item
            and item.get("source_rule_id")
            and item.get("source_rule_active") is False
            and str(item.get("status") or "").lower() in ("requested", "rejected")
        ):
            continue
        safe = serialize_portal_application_requirement(db, row)
        if safe:
            requirements.append(safe)
    return requirements


def _validate_client_fulfillment_target(db, application_id, requirement_id, *, allowed_types):
    app = _load_application(db, application_id)
    if not app:
        return None, None, None, "Application not found", 404

    before = _load_application_requirement_for_app(db, app["id"], requirement_id)
    if not before:
        return app, None, None, "Enhanced requirement not found for application", 404

    if not before.get("active"):
        return app, before, None, "Inactive enhanced requirements cannot be fulfilled", 400

    audience = str(before.get("audience") or "").strip().lower()
    if audience not in APPLICATION_REQUIREMENT_CLIENT_FULFILLMENT_AUDIENCES:
        return app, before, None, "Back-office-only enhanced requirements cannot be fulfilled from the portal", 400

    current_status = str(before.get("status") or "generated").strip().lower()
    if current_status not in APPLICATION_REQUIREMENT_CLIENT_FULFILLMENT_STATUSES:
        return app, before, None, f"Enhanced requirement status cannot be fulfilled from the portal: {current_status}", 400

    requirement_type = str(before.get("requirement_type") or "").strip().lower()
    if requirement_type not in tuple(allowed_types or ()):
        return app, before, None, "Enhanced requirement type does not match this fulfilment endpoint", 400

    client_request, safe_error = _client_safe_requirement_fields(db, before)
    if safe_error:
        return app, before, None, safe_error, 400

    return app, before, client_request, None, 200


def _client_fulfillment_actor(actor):
    user = _audit_user(actor)
    if user.get("role") != "client":
        return None, "Only clients can fulfil requested information", 403
    return user, None, 200


def fulfill_application_enhanced_requirement_document(
    db,
    application_id,
    requirement_id,
    document_id,
    actor=None,
):
    """Link an existing uploaded document to a requested portal requirement.

    The caller owns file validation/storage and document row creation. This
    helper only updates the application-specific enhanced requirement and
    writes audit rows. It does not create RMI rows, notifications, emails,
    approval blockers, memo content, EDD case changes, or screening changes.
    """
    client_user, actor_error, actor_status = _client_fulfillment_actor(actor)
    if actor_error:
        return None, actor_error, actor_status

    app, before, _client_request, error, status_code = _validate_client_fulfillment_target(
        db,
        application_id,
        requirement_id,
        allowed_types=APPLICATION_REQUIREMENT_CLIENT_DOCUMENT_TYPES,
    )
    if error:
        return None, error, status_code

    is_monitoring_refresh = (
        str(before.get("generation_source") or "").strip() == "monitoring_document_expiry_refresh"
        or bool(before.get("monitoring_alert_id") or before.get("monitoring_document_id"))
    )
    policy_info = enhanced_requirement_document_policy(before.get("requirement_key"))
    expected_doc_type = policy_info.get("document_type") or "enhanced_requirement"
    doc_type_clause = "" if is_monitoring_refresh else "AND doc_type = ?"
    params = [document_id, app["id"]]
    if not is_monitoring_refresh:
        params.append(expected_doc_type)
    doc = db.execute(
        f"""
        SELECT id
        FROM documents
        WHERE id = ?
          AND application_id = ?
          {doc_type_clause}
        """,
        params,
    ).fetchone()
    if not doc:
        if is_monitoring_refresh:
            return None, "Uploaded document must belong to the same application", 400
        return None, "Uploaded document must match the requested enhanced requirement document type", 400

    now = _now_iso()
    client_id = client_user.get("sub")
    db.execute(
        """
        UPDATE application_enhanced_requirements
        SET status='uploaded',
            linked_document_id=?,
            uploaded_at=?,
            reviewed_at=NULL,
            reviewed_by=NULL,
            updated_at=?
        WHERE id=? AND application_id=?
        """,
        (document_id, now, now, before["id"], app["id"]),
    )
    after = _load_application_requirement_for_app(db, app["id"], before["id"])
    target = _application_target(app)
    changes = {
        "status": {"before": before.get("status"), "after": "uploaded"},
        "linked_document_id": {"before": before.get("linked_document_id"), "after": document_id},
        "uploaded_at": {"before": before.get("uploaded_at"), "after": now},
    }
    payload = _audit_requirement_update_payload(app, before, after, actor, changes)
    payload.update({
        "document_id": document_id,
        "response_present": False,
        "client_id": client_id,
    })
    for action in (
        "application_enhanced_requirement.updated",
        "application_enhanced_requirement.status_changed",
        "application_enhanced_requirement.document_linked",
        "application_enhanced_requirement.client_uploaded",
    ):
        _insert_audit(
            db,
            action,
            target,
            payload,
            actor=actor,
            before_state=before,
            after_state=after,
        )

    return {
        "application_id": app["id"],
        "application_ref": app.get("ref"),
        "requirement": after,
        "document_id": document_id,
        "fulfilled": True,
    }, None, 200


def submit_application_enhanced_requirement_response(
    db,
    application_id,
    requirement_id,
    response_text,
    actor=None,
):
    """Store a client text response for a requested declaration/explanation."""
    client_user, actor_error, actor_status = _client_fulfillment_actor(actor)
    if actor_error:
        return None, actor_error, actor_status

    response_text = _clean_text(response_text)
    if not response_text:
        return None, "response_text is required", 400
    if len(response_text) > APPLICATION_REQUIREMENT_CLIENT_RESPONSE_MAX_LENGTH:
        return None, f"response_text must be {APPLICATION_REQUIREMENT_CLIENT_RESPONSE_MAX_LENGTH} characters or fewer", 400

    app, before, _client_request, error, status_code = _validate_client_fulfillment_target(
        db,
        application_id,
        requirement_id,
        allowed_types=APPLICATION_REQUIREMENT_CLIENT_TEXT_TYPES,
    )
    if error:
        return None, error, status_code

    now = _now_iso()
    client_id = client_user.get("sub")
    db.execute(
        """
        UPDATE application_enhanced_requirements
        SET status='uploaded',
            client_response_text=?,
            client_response_at=?,
            client_response_by=?,
            uploaded_at=?,
            reviewed_at=NULL,
            reviewed_by=NULL,
            updated_at=?
        WHERE id=? AND application_id=?
        """,
        (response_text, now, client_id, now, now, before["id"], app["id"]),
    )
    after = _load_application_requirement_for_app(db, app["id"], before["id"])
    target = _application_target(app)
    changes = {
        "status": {"before": before.get("status"), "after": "uploaded"},
        "client_response_text": {
            "before_present": bool(before.get("client_response_text")),
            "after_present": True,
        },
        "client_response_at": {"before": before.get("client_response_at"), "after": now},
        "uploaded_at": {"before": before.get("uploaded_at"), "after": now},
    }
    payload = _audit_requirement_update_payload(app, before, after, actor, changes)
    payload.update({
        "document_id": after.get("linked_document_id"),
        "response_present": True,
        "client_id": client_id,
    })
    for action in (
        "application_enhanced_requirement.updated",
        "application_enhanced_requirement.status_changed",
        "application_enhanced_requirement.client_response_submitted",
    ):
        _insert_audit(
            db,
            action,
            target,
            payload,
            actor=actor,
            before_state=before,
            after_state=after,
        )

    return {
        "application_id": app["id"],
        "application_ref": app.get("ref"),
        "requirement": after,
        "fulfilled": True,
    }, None, 200


def request_application_enhanced_requirement_from_client(
    db,
    application_id,
    requirement_id,
    actor=None,
):
    """Mark one eligible enhanced requirement as explicitly requested.

    This is request orchestration only.  It does not create RMI rows, client
    notifications, emails, portal prompts, document slots, memo content, or
    approval blockers.
    """
    actor_role = (_audit_user(actor).get("role") or "").lower()
    if actor_role not in APPLICATION_REQUIREMENT_REQUEST_ROLES:
        return None, "Insufficient permissions", 403

    app = _load_application(db, application_id)
    if not app:
        return None, "Application not found", 404

    before = _load_application_requirement_for_app(db, app["id"], requirement_id)
    if not before:
        return None, "Enhanced requirement not found for application", 404

    if not before.get("active"):
        return None, "Inactive enhanced requirements cannot be requested from clients", 400

    audience = str(before.get("audience") or "").strip().lower()
    if audience not in APPLICATION_REQUIREMENT_REQUESTABLE_AUDIENCES:
        return None, "Back-office-only enhanced requirements cannot be requested from clients", 400

    current_status = str(before.get("status") or "generated").strip().lower()
    if current_status == "requested":
        return None, "Enhanced requirement has already been requested from the client", 409
    if current_status not in APPLICATION_REQUIREMENT_REQUESTABLE_STATUSES:
        return None, f"Enhanced requirement status cannot be requested from client: {current_status}", 400

    client_request, safe_error = _client_safe_requirement_fields(db, before)
    if safe_error:
        return None, safe_error, 400

    now = _now_iso()
    requested_by = _audit_user(actor).get("sub")
    db.execute(
        """
        UPDATE application_enhanced_requirements
        SET status='requested',
            requested_at=?,
            requested_by=?,
            updated_at=?,
            updated_by=?
        WHERE id=? AND application_id=?
        """,
        (now, requested_by, now, requested_by, before["id"], app["id"]),
    )
    after = _load_application_requirement_for_app(db, app["id"], before["id"])
    target = _application_target(app)
    changes = {
        "status": {"before": before.get("status"), "after": "requested"},
        "requested_at": {"before": before.get("requested_at"), "after": now},
        "requested_by": {"before": before.get("requested_by"), "after": requested_by},
        "client_request_label": client_request.get("label"),
        "client_request_description_present": bool(client_request.get("description")),
        "rmi_integration": "deferred",
    }
    payload = _audit_requirement_update_payload(app, before, after, actor, changes)
    payload.update({
        "requested_by": requested_by,
        "requested_at": now,
        "linked_rmi_item_id": after.get("linked_rmi_item_id"),
        "client_request_label": client_request.get("label"),
        "rmi_integration": "deferred",
    })
    _insert_audit(
        db,
        "application_enhanced_requirement.updated",
        target,
        payload,
        actor=actor,
        before_state=before,
        after_state=after,
    )
    _insert_audit(
        db,
        "application_enhanced_requirement.status_changed",
        target,
        payload,
        actor=actor,
        before_state=before,
        after_state=after,
    )
    _insert_audit(
        db,
        "application_enhanced_requirement.requested_from_client",
        target,
        payload,
        actor=actor,
        before_state=before,
        after_state=after,
    )

    return {
        "application_id": app["id"],
        "application_ref": app.get("ref"),
        "requirement": after,
        "requested": True,
        "client_request": client_request,
        "rmi_integration": "deferred",
    }, None, 200


def update_application_enhanced_requirement(
    db,
    application_id,
    requirement_id,
    data,
    actor=None,
):
    """Apply controlled back-office lifecycle updates to one requirement.

    This helper intentionally updates only the application-specific enhanced
    requirement row.  It does not create RMI requests, portal notifications,
    document slots, memo content, or approval blockers.
    """
    data = data or {}
    actor_role = (_audit_user(actor).get("role") or "").lower()
    if actor_role not in APPLICATION_REQUIREMENT_REVIEW_ROLES:
        return None, "Insufficient permissions", 403

    app = _load_application(db, application_id)
    if not app:
        return None, "Application not found", 404

    before = _load_application_requirement_for_app(db, app["id"], requirement_id)
    if not before:
        return None, "Enhanced requirement not found for application", 404

    updates = {}
    changes = {}
    status_change = False
    notes_changed = False
    document_linked = False
    waived = False

    if "status" in data and data.get("status") not in (None, ""):
        new_status = str(data.get("status") or "").strip().lower()
        error = _validate_requirement_transition(before.get("status"), new_status, actor_role)
        if error:
            status_code = 403 if "Only admin or SCO" in error else 400
            return None, error, status_code
        if new_status != before.get("status"):
            updates["status"] = new_status
            changes["status"] = {"before": before.get("status"), "after": new_status}
            status_change = True
            if new_status in ("under_review", "accepted", "rejected"):
                updates["reviewed_by"] = _audit_user(actor).get("sub")
                updates["reviewed_at"] = _now_iso()
            if before.get("status") == "accepted" and new_status == "under_review":
                reopen_reason = _clean_text(data.get("reopen_reason") or data.get("review_notes"))
                if not reopen_reason:
                    return None, "review_notes or reopen_reason is required when reopening an accepted enhanced requirement", 400
            if before.get("status") == "waived" and new_status == "under_review":
                updates["waived_at"] = None
                updates["waived_by"] = None
                updates["waiver_reason"] = None
            if new_status == "waived":
                if not before.get("waivable"):
                    return None, "Enhanced requirement is not waivable", 400
                reason = _clean_text(data.get("waiver_reason"))
                if not reason:
                    return None, "waiver_reason is required when waiving an enhanced requirement", 400
                updates["waived_at"] = _now_iso()
                updates["waived_by"] = _audit_user(actor).get("sub")
                updates["waiver_reason"] = reason
                waived = True
                changes["waiver_reason"] = {"before": before.get("waiver_reason"), "after": reason}
        elif new_status == "waived":
            reason = _clean_text(data.get("waiver_reason"))
            if not reason:
                return None, "waiver_reason is required when waiving an enhanced requirement", 400

    if "review_notes" in data:
        notes = _clean_text(data.get("review_notes"))
        if len(notes) > APPLICATION_REQUIREMENT_NOTES_MAX_LENGTH:
            return None, f"review_notes must be {APPLICATION_REQUIREMENT_NOTES_MAX_LENGTH} characters or fewer", 400
        if notes != (before.get("review_notes") or ""):
            updates["review_notes"] = notes
            changes["review_notes"] = {"before": before.get("review_notes") or "", "after": notes}
            notes_changed = True

    if "linked_document_id" in data and data.get("linked_document_id") not in (None, ""):
        linked_document_id = _clean_text(data.get("linked_document_id"))
        doc = db.execute(
            "SELECT id FROM documents WHERE id = ? AND application_id = ?",
            (linked_document_id, app["id"]),
        ).fetchone()
        if not doc:
            return None, "linked_document_id must belong to the same application", 400
        if linked_document_id != before.get("linked_document_id"):
            updates["linked_document_id"] = linked_document_id
            changes["linked_document_id"] = {
                "before": before.get("linked_document_id"),
                "after": linked_document_id,
            }
            document_linked = True

    if not updates:
        return {
            "application_id": app["id"],
            "application_ref": app.get("ref"),
            "requirement": before,
            "updated": False,
            "changes": {},
        }, None, 200

    updates["updated_by"] = _audit_user(actor).get("sub")
    updates["updated_at"] = _now_iso()
    set_clause = ", ".join(f"{column}=?" for column in updates)
    params = list(updates.values()) + [before["id"], app["id"]]
    db.execute(
        f"""
        UPDATE application_enhanced_requirements
        SET {set_clause}
        WHERE id = ? AND application_id = ?
        """,
        tuple(params),
    )
    after = _load_application_requirement_for_app(db, app["id"], before["id"])
    target = _application_target(app)

    payload = _audit_requirement_update_payload(app, before, after, actor, changes)
    _insert_audit(
        db,
        "application_enhanced_requirement.updated",
        target,
        payload,
        actor=actor,
        before_state=before,
        after_state=after,
    )
    if status_change:
        _insert_audit(
            db,
            "application_enhanced_requirement.status_changed",
            target,
            payload,
            actor=actor,
            before_state=before,
            after_state=after,
        )
    if document_linked:
        _insert_audit(
            db,
            "application_enhanced_requirement.document_linked",
            target,
            payload,
            actor=actor,
            before_state=before,
            after_state=after,
        )
    if waived:
        _insert_audit(
            db,
            "application_enhanced_requirement.waived",
            target,
            payload,
            actor=actor,
            before_state=before,
            after_state=after,
        )
    if notes_changed:
        _insert_audit(
            db,
            "application_enhanced_requirement.notes_updated",
            target,
            payload,
            actor=actor,
            before_state=before,
            after_state=after,
        )

    return {
        "application_id": app["id"],
        "application_ref": app.get("ref"),
        "requirement": after,
        "updated": True,
        "changes": changes,
    }, None, 200


def _load_active_rules(db, trigger_keys):
    if not trigger_keys:
        return []
    placeholders = ",".join("?" for _ in trigger_keys)
    rows = db.execute(
        f"""
        SELECT * FROM enhanced_requirement_rules
        WHERE active = 1 AND trigger_key IN ({placeholders})
        ORDER BY trigger_category, trigger_label, sort_order, id
        """,
        tuple(trigger_keys),
    ).fetchall()
    return [serialize_rule(row) for row in rows]


def generate_application_enhanced_requirements(
    db,
    application_id,
    app_row=None,
    routing=None,
    actor=None,
    generation_source="manual_api",
):
    """Generate missing application-specific enhanced requirements.

    The engine is intentionally create-only in Step 2: it snapshots active
    settings rules into application rows and preserves all existing generated
    records, including reviewed, waived, uploaded, accepted, or rejected work.
    The caller owns the transaction.
    """
    app = _row_dict(app_row) if app_row is not None else _load_application(db, application_id)
    result = {
        "application_id": application_id,
        "ran": False,
        "config_ok": False,
        "triggers": [],
        "trigger_sources": {},
        "generated_count": 0,
        "existing_count": 0,
        "skipped_count": 0,
        "requirements": [],
        "warnings": [],
        "errors": [],
    }
    if not app:
        result["errors"].append("application_not_found")
        return result

    result["application_id"] = app.get("id") or application_id
    actor_audit = _audit_user(actor)
    actor_fk = _actor_user_fk_value(db, actor)
    target = _application_target(app)
    audit_base = {
        "application_id": app.get("id"),
        "application_ref": app.get("ref"),
        "actor": actor_audit.get("sub"),
        "actor_role": actor_audit.get("role"),
        "actor_user_fk": actor_fk,
        "generation_source": generation_source,
        "timestamp": _now_iso(),
    }

    _insert_audit(
        db,
        "application_enhanced_requirements.generation_attempted",
        target,
        dict(audit_base),
        actor=actor,
    )

    diagnostics = diagnose_enhanced_requirement_config(db)
    result["config_ok"] = bool(diagnostics.get("config_ok"))
    if not result["config_ok"]:
        result["errors"].extend(diagnostics.get("errors") or [])
        result["warnings"].extend(diagnostics.get("warnings") or [])
        _insert_audit(
            db,
            "application_enhanced_requirements.config_invalid",
            target,
            {
                **audit_base,
                "config_ok": False,
                "errors": result["errors"],
                "warnings": result["warnings"],
            },
            actor=actor,
        )
        _insert_audit(
            db,
            "application_enhanced_requirements.generation_completed",
            target,
            {
                **audit_base,
                "config_ok": False,
                "generated_requirement_keys": [],
                "existing_requirement_keys": [],
                "errors": result["errors"],
                "warnings": result["warnings"],
            },
            actor=actor,
        )
        return result

    routing_decision = _routing_for_generation(db, app, routing)
    triggers, trigger_sources, trigger_warnings = _resolve_requirement_triggers(app, routing_decision)
    result["triggers"] = triggers
    result["trigger_sources"] = trigger_sources
    result["warnings"].extend(trigger_warnings)
    result["ran"] = True
    section_b_rules = _section_b_person_document_rules(db, app)

    if not triggers and not section_b_rules:
        _insert_audit(
            db,
            "application_enhanced_requirements.generation_completed",
            target,
            {
                **audit_base,
                "config_ok": True,
                "triggers": [],
                "trigger_sources": trigger_sources,
                "generated_requirement_keys": [],
                "existing_requirement_keys": [],
                "warnings": result["warnings"],
            },
            actor=actor,
        )
        return result

    rules = _load_active_rules(db, triggers)
    if not rules and not section_b_rules:
        result["warnings"].append("No active enhanced requirement rules matched detected trigger(s)")
    rules = list(rules) + section_b_rules

    generated_keys = []
    existing_keys = []
    for rule in rules:
        existing = db.execute(
            """
            SELECT * FROM application_enhanced_requirements
            WHERE application_id=? AND trigger_key=? AND requirement_key=?
            """,
            (app["id"], rule["trigger_key"], rule["requirement_key"]),
        ).fetchone()
        if existing:
            result["existing_count"] += 1
            existing_item = serialize_application_requirement(existing)
            result["requirements"].append(existing_item)
            existing_keys.append(rule["requirement_key"])
            continue

        applicable, skip_reason = _rule_applicable_to_application(rule, app)
        if not applicable:
            result["skipped_count"] += 1
            result["warnings"].append(
                f"Skipped {rule['requirement_key']}: {skip_reason}"
            )
            continue

        trigger_context = {
            "routing": routing_decision,
            "mapped_from_triggers": trigger_sources.get(rule["trigger_key"], []),
            "generation_source": generation_source,
        }
        if rule.get("_subject"):
            trigger_context["subject"] = rule["_subject"]
        prefill = _prefill_fields_for_generated_requirement(rule, app)
        generated_status = prefill.get("status") or "generated"
        params = (
            app["id"],
            rule.get("id"),
            rule["trigger_key"],
            rule["trigger_label"],
            rule["trigger_category"],
            rule["requirement_key"],
            rule["requirement_label"],
            rule.get("requirement_description", ""),
            rule["audience"],
            rule["requirement_type"],
            rule["subject_scope"],
            1 if rule.get("blocking_approval") else 0,
            1 if rule.get("waivable") else 0,
            json.dumps(rule.get("waiver_roles") or []),
            1 if rule.get("mandatory") else 0,
            generated_status,
            generation_source,
            "; ".join(trigger_sources.get(rule["trigger_key"], [])),
            json.dumps(trigger_context, default=str, sort_keys=True),
            1,
            actor_fk,
            actor_fk,
        )
        if _db_is_postgres(db):
            inserted = db.execute(
                """
                INSERT INTO application_enhanced_requirements
                (application_id, source_rule_id, trigger_key, trigger_label,
                 trigger_category, requirement_key, requirement_label,
                 requirement_description, audience, requirement_type,
                 subject_scope, blocking_approval, waivable, waiver_roles,
                 mandatory, status, generation_source, trigger_reason,
                 trigger_context, active, created_by, updated_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                RETURNING id
                """,
                params,
            ).fetchone()
            req_id = inserted["id"]
        else:
            db.execute(
                """
                INSERT INTO application_enhanced_requirements
                (application_id, source_rule_id, trigger_key, trigger_label,
                 trigger_category, requirement_key, requirement_label,
                 requirement_description, audience, requirement_type,
                 subject_scope, blocking_approval, waivable, waiver_roles,
                 mandatory, status, generation_source, trigger_reason,
                 trigger_context, active, created_by, updated_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                params,
            )
            req_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        if prefill:
            db.execute(
                """
                UPDATE application_enhanced_requirements
                SET client_response_text=?,
                    client_response_at=?,
                    client_response_by=?,
                    uploaded_at=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    prefill.get("client_response_text"),
                    prefill.get("client_response_at"),
                    prefill.get("client_response_by"),
                    prefill.get("uploaded_at"),
                    _now_iso(),
                    req_id,
                ),
            )

        created = _load_application_requirement(db, req_id)
        result["generated_count"] += 1
        result["requirements"].append(created)
        generated_keys.append(rule["requirement_key"])
        _insert_audit(
            db,
            "application_enhanced_requirement.generated",
            target,
            {
                **audit_base,
                "requirement_id": created.get("id") if created else req_id,
                "trigger_key": rule["trigger_key"],
                "requirement_key": rule["requirement_key"],
                "trigger_sources": trigger_sources.get(rule["trigger_key"], []),
            },
            actor=actor,
            after_state=created,
        )

    _insert_audit(
        db,
        "application_enhanced_requirements.generation_completed",
        target,
        {
            **audit_base,
            "config_ok": True,
            "triggers": triggers,
            "trigger_sources": trigger_sources,
            "generated_requirement_keys": generated_keys,
            "existing_requirement_keys": existing_keys,
            "generated_count": result["generated_count"],
            "existing_count": result["existing_count"],
            "warnings": result["warnings"],
            "errors": result["errors"],
        },
        actor=actor,
    )
    return result
