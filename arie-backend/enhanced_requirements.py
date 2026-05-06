"""Enhanced / EDD requirement settings.

This module defines the configurable rule vocabulary used by the
back-office settings layer.  It deliberately does not generate RMI requests,
portal prompts, approval blockers, or memo content; those workflow effects are
future phases.
"""

import json
import logging
import re
from datetime import datetime, timezone

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
)
ALLOWED_WAIVER_ROLES = ("admin", "sco")

EXPECTED_DEFAULT_TRIGGER_KEYS = (
    "high_or_very_high_risk",
    "pep",
    "crypto_vasp",
    "opaque_ownership",
    "high_risk_jurisdiction",
    "high_volume",
    "screening_concern",
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

EDD_TRIGGER_TO_REQUIREMENT_TRIGGER = {
    "high_or_very_high_risk": "high_or_very_high_risk",
    "declared_pep_present": "pep",
    "crypto_or_virtual_asset_sector": "crypto_vasp",
    "elevated_jurisdiction": "high_risk_jurisdiction",
    "opaque_or_incomplete_ownership": "opaque_ownership",
    "material_screening_concern": "screening_concern",
}


DEFAULT_ENHANCED_REQUIREMENT_RULES = [
    {
        "trigger_key": "high_or_very_high_risk",
        "trigger_label": "HIGH / VERY_HIGH risk",
        "trigger_category": "risk",
        "requirement_key": "company_bank_reference",
        "requirement_label": "Company bank reference where available",
        "requirement_description": "Request a current company bank reference where the applicant can reasonably provide one.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "company",
        "sort_order": 10,
    },
    {
        "trigger_key": "high_or_very_high_risk",
        "trigger_label": "HIGH / VERY_HIGH risk",
        "trigger_category": "risk",
        "requirement_key": "company_bank_statements_6m",
        "requirement_label": "6 months company bank statements where available",
        "requirement_description": "Collect recent company bank statements when available to support enhanced financial review.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "company",
        "sort_order": 20,
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
        "sort_order": 30,
    },
    {
        "trigger_key": "high_or_very_high_risk",
        "trigger_label": "HIGH / VERY_HIGH risk",
        "trigger_category": "risk",
        "requirement_key": "material_ubo_sow_evidence",
        "requirement_label": "UBO Source of Wealth evidence for material UBOs/controllers",
        "requirement_description": "Evidence supporting the source of wealth for material UBOs or controllers.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "ubo",
        "sort_order": 40,
    },
    {
        "trigger_key": "high_or_very_high_risk",
        "trigger_label": "HIGH / VERY_HIGH risk",
        "trigger_category": "risk",
        "requirement_key": "enhanced_business_activity_explanation",
        "requirement_label": "Enhanced business activity explanation",
        "requirement_description": "Additional explanation of business activity, counterparties, revenue model, and transaction purpose.",
        "audience": "client",
        "requirement_type": "explanation",
        "subject_scope": "application",
        "sort_order": 50,
    },
    {
        "trigger_key": "pep",
        "trigger_label": "PEP",
        "trigger_category": "screening",
        "requirement_key": "pep_declaration_details",
        "requirement_label": "PEP declaration details",
        "requirement_description": "Collect details of the PEP exposure, including relationship and public function context.",
        "audience": "client",
        "requirement_type": "declaration",
        "subject_scope": "screening_subject",
        "sort_order": 10,
    },
    {
        "trigger_key": "pep",
        "trigger_label": "PEP",
        "trigger_category": "screening",
        "requirement_key": "pep_role_position",
        "requirement_label": "PEP role/position",
        "requirement_description": "Record the PEP role, position, public office, or exposure basis.",
        "audience": "both",
        "requirement_type": "declaration",
        "subject_scope": "screening_subject",
        "sort_order": 20,
    },
    {
        "trigger_key": "pep",
        "trigger_label": "PEP",
        "trigger_category": "screening",
        "requirement_key": "pep_jurisdiction",
        "requirement_label": "PEP jurisdiction",
        "requirement_description": "Capture the jurisdiction associated with the PEP role or exposure.",
        "audience": "both",
        "requirement_type": "declaration",
        "subject_scope": "screening_subject",
        "sort_order": 30,
    },
    {
        "trigger_key": "pep",
        "trigger_label": "PEP",
        "trigger_category": "screening",
        "requirement_key": "pep_sow_evidence",
        "requirement_label": "Source of Wealth evidence",
        "requirement_description": "Evidence supporting the PEP's source of wealth.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "screening_subject",
        "sort_order": 40,
    },
    {
        "trigger_key": "pep",
        "trigger_label": "PEP",
        "trigger_category": "screening",
        "requirement_key": "pep_linked_sof_evidence",
        "requirement_label": "Source of Funds evidence where funds are linked to PEP",
        "requirement_description": "Source of funds evidence where the proposed relationship funds are linked to the PEP.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "application",
        "sort_order": 50,
    },
    {
        "trigger_key": "pep",
        "trigger_label": "PEP",
        "trigger_category": "screening",
        "requirement_key": "mandatory_senior_review",
        "requirement_label": "Mandatory senior review",
        "requirement_description": "Senior compliance review is required before closure.",
        "audience": "backoffice",
        "requirement_type": "review_task",
        "subject_scope": "application",
        "blocking_approval": True,
        "waivable": False,
        "waiver_roles": [],
        "sort_order": 60,
    },
    {
        "trigger_key": "pep",
        "trigger_label": "PEP",
        "trigger_category": "screening",
        "requirement_key": "ongoing_monitoring_flag",
        "requirement_label": "Ongoing monitoring flag",
        "requirement_description": "Flag the relationship for ongoing monitoring after onboarding.",
        "audience": "backoffice",
        "requirement_type": "internal_control",
        "subject_scope": "application",
        "blocking_approval": False,
        "waivable": False,
        "waiver_roles": [],
        "sort_order": 70,
    },
    {
        "trigger_key": "crypto_vasp",
        "trigger_label": "Crypto / VASP",
        "trigger_category": "sector",
        "requirement_key": "aml_cft_policy",
        "requirement_label": "AML/CFT policy",
        "requirement_description": "AML/CFT policy applicable to the crypto, VASP, or virtual asset activity.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "company",
        "sort_order": 10,
    },
    {
        "trigger_key": "crypto_vasp",
        "trigger_label": "Crypto / VASP",
        "trigger_category": "sector",
        "requirement_key": "licence_or_registration_evidence",
        "requirement_label": "Licence/registration evidence or confirmation of unlicensed status",
        "requirement_description": "Licence, registration, exemption, or explanation of unlicensed status for virtual asset activity.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "company",
        "sort_order": 20,
    },
    {
        "trigger_key": "crypto_vasp",
        "trigger_label": "Crypto / VASP",
        "trigger_category": "sector",
        "requirement_key": "transaction_flow_explanation",
        "requirement_label": "Transaction flow explanation",
        "requirement_description": "Explain expected transaction flows, rails, counterparties, and settlement model.",
        "audience": "client",
        "requirement_type": "explanation",
        "subject_scope": "application",
        "sort_order": 30,
    },
    {
        "trigger_key": "crypto_vasp",
        "trigger_label": "Crypto / VASP",
        "trigger_category": "sector",
        "requirement_key": "jurisdictions_served",
        "requirement_label": "Jurisdictions served",
        "requirement_description": "List jurisdictions served or targeted by the virtual asset activity.",
        "audience": "client",
        "requirement_type": "explanation",
        "subject_scope": "application",
        "sort_order": 40,
    },
    {
        "trigger_key": "crypto_vasp",
        "trigger_label": "Crypto / VASP",
        "trigger_category": "sector",
        "requirement_key": "wallet_exchange_counterparty_exposure",
        "requirement_label": "Wallet/exchange/counterparty exposure explanation where applicable",
        "requirement_description": "Explain wallet, exchange, counterparty, custody, or blockchain exposure where applicable.",
        "audience": "client",
        "requirement_type": "explanation",
        "subject_scope": "application",
        "sort_order": 50,
    },
    {
        "trigger_key": "crypto_vasp",
        "trigger_label": "Crypto / VASP",
        "trigger_category": "sector",
        "requirement_key": "crypto_source_of_funds_evidence",
        "requirement_label": "Source of Funds evidence",
        "requirement_description": "Evidence supporting the source of funds for virtual asset related activity.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "application",
        "sort_order": 60,
    },
    {
        "trigger_key": "opaque_ownership",
        "trigger_label": "Opaque ownership",
        "trigger_category": "ownership",
        "requirement_key": "ownership_structure_chart",
        "requirement_label": "Ownership structure chart",
        "requirement_description": "Current structure chart showing the full ownership and control chain.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "company",
        "sort_order": 10,
    },
    {
        "trigger_key": "opaque_ownership",
        "trigger_label": "Opaque ownership",
        "trigger_category": "ownership",
        "requirement_key": "ownership_chain_documents",
        "requirement_label": "Full ownership-chain documents",
        "requirement_description": "Documents evidencing each entity or arrangement in the ownership chain.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "company",
        "sort_order": 20,
    },
    {
        "trigger_key": "opaque_ownership",
        "trigger_label": "Opaque ownership",
        "trigger_category": "ownership",
        "requirement_key": "enhanced_ubo_evidence",
        "requirement_label": "Enhanced UBO evidence",
        "requirement_description": "Additional evidence supporting UBO identity, ownership, and control.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "ubo",
        "sort_order": 30,
    },
    {
        "trigger_key": "opaque_ownership",
        "trigger_label": "Opaque ownership",
        "trigger_category": "ownership",
        "requirement_key": "control_rationale",
        "requirement_label": "Control rationale",
        "requirement_description": "Explain how control is exercised where ownership is indirect, layered, or otherwise complex.",
        "audience": "client",
        "requirement_type": "explanation",
        "subject_scope": "controller",
        "sort_order": 40,
    },
    {
        "trigger_key": "opaque_ownership",
        "trigger_label": "Opaque ownership",
        "trigger_category": "ownership",
        "requirement_key": "trust_nominee_foundation_documents",
        "requirement_label": "Trust/nominee/foundation documents where applicable",
        "requirement_description": "Trust deeds, nominee agreements, foundation documents, or equivalent control documents where applicable.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "controller",
        "sort_order": 50,
    },
    {
        "trigger_key": "high_risk_jurisdiction",
        "trigger_label": "High-risk jurisdiction",
        "trigger_category": "jurisdiction",
        "requirement_key": "jurisdiction_exposure_rationale",
        "requirement_label": "Jurisdiction exposure rationale",
        "requirement_description": "Explain the business rationale for high-risk jurisdiction exposure.",
        "audience": "client",
        "requirement_type": "explanation",
        "subject_scope": "application",
        "sort_order": 10,
    },
    {
        "trigger_key": "high_risk_jurisdiction",
        "trigger_label": "High-risk jurisdiction",
        "trigger_category": "jurisdiction",
        "requirement_key": "operating_country_target_market_explanation",
        "requirement_label": "Operating-country / target-market explanation",
        "requirement_description": "Explain operating countries, target markets, and exposure controls.",
        "audience": "client",
        "requirement_type": "explanation",
        "subject_scope": "application",
        "sort_order": 20,
    },
    {
        "trigger_key": "high_risk_jurisdiction",
        "trigger_label": "High-risk jurisdiction",
        "trigger_category": "jurisdiction",
        "requirement_key": "jurisdiction_sof_evidence",
        "requirement_label": "Source of Funds evidence",
        "requirement_description": "Evidence supporting source of funds for high-risk jurisdiction exposure.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "application",
        "sort_order": 30,
    },
    {
        "trigger_key": "high_risk_jurisdiction",
        "trigger_label": "High-risk jurisdiction",
        "trigger_category": "jurisdiction",
        "requirement_key": "jurisdiction_licensing_regulatory_evidence",
        "requirement_label": "Licensing/regulatory evidence where relevant",
        "requirement_description": "Licence, registration, or regulatory evidence relevant to the jurisdiction exposure.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "company",
        "sort_order": 40,
    },
    {
        "trigger_key": "high_risk_jurisdiction",
        "trigger_label": "High-risk jurisdiction",
        "trigger_category": "jurisdiction",
        "requirement_key": "enhanced_screening_review",
        "requirement_label": "Enhanced screening review",
        "requirement_description": "Back-office review of enhanced screening evidence for the high-risk jurisdiction exposure.",
        "audience": "backoffice",
        "requirement_type": "review_task",
        "subject_scope": "application",
        "sort_order": 50,
    },
    {
        "trigger_key": "high_volume",
        "trigger_label": "High volume",
        "trigger_category": "transaction",
        "requirement_key": "contracts_invoices",
        "requirement_label": "Contracts/invoices",
        "requirement_description": "Commercial contracts, invoices, or equivalent evidence supporting expected high volume.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "application",
        "sort_order": 10,
    },
    {
        "trigger_key": "high_volume",
        "trigger_label": "High volume",
        "trigger_category": "transaction",
        "requirement_key": "expected_transaction_flow_evidence",
        "requirement_label": "Expected transaction flow evidence",
        "requirement_description": "Evidence supporting expected transaction flow, frequency, ticket size, and corridors.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "application",
        "sort_order": 20,
    },
    {
        "trigger_key": "high_volume",
        "trigger_label": "High volume",
        "trigger_category": "transaction",
        "requirement_key": "high_volume_bank_statements",
        "requirement_label": "Company bank statements where available",
        "requirement_description": "Company bank statements supporting expected high-volume activity where available.",
        "audience": "client",
        "requirement_type": "document",
        "subject_scope": "company",
        "sort_order": 30,
    },
    {
        "trigger_key": "high_volume",
        "trigger_label": "High volume",
        "trigger_category": "transaction",
        "requirement_key": "major_counterparties_explanation",
        "requirement_label": "Major counterparties explanation",
        "requirement_description": "Explain key counterparties, customer segments, suppliers, or payment participants.",
        "audience": "client",
        "requirement_type": "explanation",
        "subject_scope": "application",
        "sort_order": 40,
    },
    {
        "trigger_key": "high_volume",
        "trigger_label": "High volume",
        "trigger_category": "transaction",
        "requirement_key": "volume_rationale_vs_business_size",
        "requirement_label": "Volume rationale vs business size",
        "requirement_description": "Explain why expected volumes are proportionate to business size, age, sector, and operating model.",
        "audience": "client",
        "requirement_type": "explanation",
        "subject_scope": "application",
        "sort_order": 50,
    },
    {
        "trigger_key": "screening_concern",
        "trigger_label": "Screening concern",
        "trigger_category": "screening",
        "requirement_key": "screening_disposition",
        "requirement_label": "Back-office screening disposition",
        "requirement_description": "Record the back-office disposition for the screening concern.",
        "audience": "backoffice",
        "requirement_type": "review_task",
        "subject_scope": "screening_subject",
        "waivable": False,
        "waiver_roles": [],
        "sort_order": 10,
    },
    {
        "trigger_key": "screening_concern",
        "trigger_label": "Screening concern",
        "trigger_category": "screening",
        "requirement_key": "false_positive_rationale",
        "requirement_label": "False-positive rationale",
        "requirement_description": "Document the rationale where a possible match is assessed as a false positive.",
        "audience": "backoffice",
        "requirement_type": "review_task",
        "subject_scope": "screening_subject",
        "sort_order": 20,
    },
    {
        "trigger_key": "screening_concern",
        "trigger_label": "Screening concern",
        "trigger_category": "screening",
        "requirement_key": "adverse_media_pep_sanctions_assessment",
        "requirement_label": "Adverse-media / PEP / sanctions assessment",
        "requirement_description": "Assess adverse media, PEP, sanctions, or other screening risk presented by the concern.",
        "audience": "backoffice",
        "requirement_type": "review_task",
        "subject_scope": "screening_subject",
        "sort_order": 30,
    },
    {
        "trigger_key": "screening_concern",
        "trigger_label": "Screening concern",
        "trigger_category": "screening",
        "requirement_key": "material_screening_senior_review",
        "requirement_label": "Senior review if material",
        "requirement_description": "Escalate material screening concerns for senior review.",
        "audience": "backoffice",
        "requirement_type": "review_task",
        "subject_scope": "screening_subject",
        "waivable": False,
        "waiver_roles": [],
        "sort_order": 40,
    },
    {
        "trigger_key": "screening_concern",
        "trigger_label": "Screening concern",
        "trigger_category": "screening",
        "requirement_key": "client_clarification_screening",
        "requirement_label": "Client clarification only where needed",
        "requirement_description": "Request client clarification only where back-office review determines it is necessary and safe.",
        "audience": "both",
        "requirement_type": "explanation",
        "subject_scope": "screening_subject",
        "mandatory": False,
        "sort_order": 50,
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
    return item


def serialize_application_requirement(row):
    """Return an API-safe dict for a generated application requirement row."""
    if row is None:
        return None
    item = _row_dict(row)
    for key in ("blocking_approval", "waivable", "mandatory", "active"):
        item[key] = _bool(item.get(key))
    item["waiver_roles"] = _loads_json(item.get("waiver_roles"), [])
    item["trigger_context"] = _loads_json(item.get("trigger_context"), {})
    return item


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


def seed_default_enhanced_requirement_rules(db, actor="system"):
    """Insert missing default rules without overwriting customized rows.

    Returns the number of new rows inserted.
    """
    inserted = 0
    inserted_keys = []
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
                actor,
                actor,
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
    return inserted


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


def _insert_audit(db, action, target, detail, actor=None, before_state=None, after_state=None):
    user = _audit_user(actor)
    detail_text = json.dumps(detail or {}, default=str, sort_keys=True)
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


def _application_target(app):
    app = app or {}
    return "application:" + str(app.get("ref") or app.get("id") or "unknown")


def _is_yes(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in ("yes", "true", "1", "y")


def _declared_pep_present(db, application_id):
    try:
        rows = db.execute(
            """
            SELECT is_pep FROM directors WHERE application_id=?
            UNION ALL
            SELECT is_pep FROM ubos WHERE application_id=?
            """,
            (application_id, application_id),
        ).fetchall()
    except Exception:
        return False
    return any(_is_yes(_row_dict(row).get("is_pep")) for row in rows)


def _prescreening_dict(app):
    return _loads_json((app or {}).get("prescreening_data"), {}) or {}


def _screening_summary_from_app(app):
    prescreening = _prescreening_dict(app)
    report = prescreening.get("screening_report") if isinstance(prescreening, dict) else {}
    if not isinstance(report, dict):
        report = _loads_json(report, {})
    existing = prescreening.get("screening_terminality_summary") if isinstance(prescreening, dict) else {}
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
    except Exception:
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


def _declared_high_volume(app):
    prescreening = _prescreening_dict(app)
    values = []
    if isinstance(prescreening, dict):
        values.extend([
            prescreening.get("monthly_volume"),
            prescreening.get("expected_volume"),
        ])
        transaction = _loads_json(prescreening.get("transaction"), {})
        if isinstance(transaction, dict):
            expected = _loads_json(transaction.get("expected_monthly_volume"), {})
            if isinstance(expected, dict):
                values.append(expected.get("band_legacy"))
    text = " ".join(str(v or "") for v in values).lower()
    compact = re.sub(r"[^0-9a-z<>]+", "", text)
    if not text.strip():
        return False
    if "under" in text or "below" in text or "<50000" in compact:
        return False
    return (
        "over" in text
        or "above" in text
        or "500,000" in text
        or "5,000,000" in text
        or "500000" in compact
        or "5000000" in compact
        or ">500000" in compact
        or ">5m" in compact
    )


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


def _resolve_requirement_triggers(app, routing):
    mapped = {}
    warnings = []
    routing = routing or {}
    for source_trigger in list(routing.get("triggers") or []):
        target = EDD_TRIGGER_TO_REQUIREMENT_TRIGGER.get(source_trigger)
        if target:
            mapped.setdefault(target, []).append(source_trigger)
            continue
        if source_trigger == "supervisor_mandatory_escalation":
            screening = (routing.get("inputs") or {}).get("screening_terminality_summary") or {}
            if isinstance(screening, dict) and screening.get("has_terminal_match"):
                mapped.setdefault("screening_concern", []).append(source_trigger)
            else:
                warnings.append("Unmapped EDD routing trigger: supervisor_mandatory_escalation")
            continue
        if source_trigger == "high_risk_sector":
            if "crypto_vasp" not in mapped:
                warnings.append("Unmapped EDD routing trigger: high_risk_sector")
            continue
        if source_trigger.startswith("edd_flag:"):
            warnings.append("Unmapped EDD routing trigger: " + source_trigger)
            continue
        warnings.append("Unmapped EDD routing trigger: " + str(source_trigger))

    if _declared_high_volume(app):
        mapped.setdefault("high_volume", []).append("declared_high_volume")

    ordered = [key for key in EXPECTED_DEFAULT_TRIGGER_KEYS if key in mapped]
    for key in sorted(k for k in mapped if k not in ordered):
        ordered.append(key)
    return ordered, mapped, warnings


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
    target = _application_target(app)
    audit_base = {
        "application_id": app.get("id"),
        "application_ref": app.get("ref"),
        "actor": _audit_user(actor).get("sub"),
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

    routing_decision = routing or _routing_for_application(db, app)
    triggers, trigger_sources, trigger_warnings = _resolve_requirement_triggers(app, routing_decision)
    result["triggers"] = triggers
    result["warnings"].extend(trigger_warnings)
    result["ran"] = True

    if not triggers:
        _insert_audit(
            db,
            "application_enhanced_requirements.generation_completed",
            target,
            {
                **audit_base,
                "config_ok": True,
                "triggers": [],
                "generated_requirement_keys": [],
                "existing_requirement_keys": [],
                "warnings": result["warnings"],
            },
            actor=actor,
        )
        return result

    rules = _load_active_rules(db, triggers)
    if not rules:
        result["warnings"].append("No active enhanced requirement rules matched detected trigger(s)")

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

        trigger_context = {
            "routing": routing_decision,
            "mapped_from_triggers": trigger_sources.get(rule["trigger_key"], []),
            "generation_source": generation_source,
        }
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
            "generated",
            generation_source,
            "; ".join(trigger_sources.get(rule["trigger_key"], [])),
            json.dumps(trigger_context, default=str, sort_keys=True),
            1,
            _audit_user(actor).get("sub"),
            _audit_user(actor).get("sub"),
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
