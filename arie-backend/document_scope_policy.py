"""Shared ordinary KYC document-slot scope policy.

This module is deliberately dependency-free so upload handlers and read-only
integrity validators enforce the same entity/person document contract.
"""

ENTITY_BASE_DOCUMENT_TYPES = frozenset(
    {
        "aml_policy",
        "bank_statements",
        "bankref",
        "board_res",
        "cert_inc",
        "contracts",
        "fin_stmt",
        "licence",
        "memarts",
        "poa",
        "reg_dir",
        "reg_sh",
        "source_funds",
        "source_wealth",
        "structure_chart",
    }
)

INDIVIDUAL_BASE_DOCUMENT_TYPES = frozenset(
    {
        "bankref",
        "national_id",
        "passport",
        "poa",
        "source_wealth",
    }
)

INTERMEDIARY_BASE_DOCUMENT_TYPES = frozenset(
    {
        "cert_gs",
        "cert_inc",
        "fin_stmt",
        "reg_dir",
        "reg_sh",
    }
)


def normalize_document_person_type(value):
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {
        "directors": "director",
        "beneficial_owner": "ubo",
        "beneficial_owners": "ubo",
        "ubos": "ubo",
        "intermediaries": "intermediary",
    }.get(normalized, normalized)


def base_document_scope_error_for_canonical_type(doc_type, person_type=None):
    """Return a fail-closed error for a canonical ordinary document type."""
    canonical_type = str(doc_type or "").strip().lower()
    normalized_person_type = normalize_document_person_type(person_type)
    if not normalized_person_type:
        if canonical_type not in ENTITY_BASE_DOCUMENT_TYPES:
            return (
                f"Document type '{canonical_type}' requires an explicit supported party "
                "association or a recognized request-specific upload route"
            )
        return None
    if normalized_person_type in ("director", "ubo"):
        allowed = INDIVIDUAL_BASE_DOCUMENT_TYPES
    elif normalized_person_type == "intermediary":
        allowed = INTERMEDIARY_BASE_DOCUMENT_TYPES
    else:
        return "Unsupported party type for document upload"
    if canonical_type not in allowed:
        return (
            f"Document type '{canonical_type}' is not supported for "
            f"{normalized_person_type} document slots"
        )
    return None
