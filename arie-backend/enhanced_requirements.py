"""Enhanced / EDD requirement settings.

This module defines the configurable rule vocabulary used by the
back-office settings layer.  It deliberately does not generate RMI requests,
portal prompts, approval blockers, or memo content; those workflow effects are
future phases.
"""

import json
import re
from datetime import datetime, timezone


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
