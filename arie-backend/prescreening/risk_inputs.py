"""
Canonical prescreening -> scorer input mapping.

Phase 1 goal:
- keep compute_risk_score untouched
- eliminate handler-specific field drift
- report corrected mappings via metadata for operational visibility
"""

from __future__ import annotations

from prescreening.normalize import (
    first_non_empty,
    normalize_prescreening_data,
    safe_json_loads,
)


def _copy_list(value):
    if isinstance(value, list):
        return list(value)
    return []


def _derive_primary_service(services):
    if not isinstance(services, list):
        return ""
    for service in services:
        text = str(service).strip()
        if text:
            return text
    return ""


def _derive_amount_currency(currencies):
    if not isinstance(currencies, list):
        return ""
    for currency in currencies:
        text = str(currency).strip()
        if text:
            return text
    return ""


def _current_vs_corrected_flags(raw_prescreening, normalized_prescreening):
    corrections = []
    if raw_prescreening.get("countries_of_operation") and normalized_prescreening.get("operating_countries"):
        corrections.append("operating_countries_from_countries_of_operation")
    if raw_prescreening.get("intermediaries") and normalized_prescreening.get("intermediary_shareholders"):
        corrections.append("intermediary_shareholders_from_intermediaries")
    if raw_prescreening.get("services_required") and normalized_prescreening.get("primary_service"):
        corrections.append("primary_service_from_services_required")
    if (
        raw_prescreening.get("source_of_wealth_type") or raw_prescreening.get("source_of_wealth_detail")
    ) and normalized_prescreening.get("source_of_wealth"):
        corrections.append("source_of_wealth_summary_from_type_detail")
    return corrections


def build_prescreening_risk_input(
    *,
    application=None,
    prescreening_data=None,
    directors=None,
    ubos=None,
    intermediaries=None,
):
    raw_prescreening = safe_json_loads(prescreening_data)
    payload = {
        "company_name": (application or {}).get("company_name"),
        "country": (application or {}).get("country"),
        "sector": (application or {}).get("sector"),
        "entity_type": (application or {}).get("entity_type"),
        "ownership_structure": (application or {}).get("ownership_structure"),
        "directors": _copy_list(directors),
        "ubos": _copy_list(ubos),
        "intermediaries": _copy_list(intermediaries),
        "prescreening_data": raw_prescreening,
    }
    normalized = normalize_prescreening_data(payload, existing=raw_prescreening)
    canonical = safe_json_loads(normalized.get("transaction"))
    business = safe_json_loads(normalized.get("business"))
    wealth = safe_json_loads(normalized.get("wealth"))
    entity = safe_json_loads(normalized.get("entity"))

    primary_services = business.get("services", {}).get("primary_services", []) if isinstance(business, dict) else []
    primary_service = first_non_empty(
        normalized.get("primary_service"),
        normalized.get("service_required"),
        _derive_primary_service(primary_services),
    )

    scorer_input = {
        **normalized,
        "company_name": first_non_empty((application or {}).get("company_name"), entity.get("legal_name")),
        "country": first_non_empty((application or {}).get("country"), entity.get("incorporation_country")),
        "sector": first_non_empty((application or {}).get("sector"), normalized.get("sector"), business.get("sector")),
        "entity_type": first_non_empty((application or {}).get("entity_type"), normalized.get("entity_type")),
        "ownership_structure": first_non_empty((application or {}).get("ownership_structure"), normalized.get("ownership_structure")),
        "directors": _copy_list(directors),
        "ubos": _copy_list(ubos),
        "intermediaries": _copy_list(intermediaries),
        "intermediary_shareholders": _copy_list(intermediaries),
        "operating_countries": _copy_list(normalized.get("operating_countries")),
        "countries_of_operation": _copy_list(normalized.get("countries_of_operation")),
        "target_markets": _copy_list(normalized.get("target_markets")),
        "currencies": _copy_list(normalized.get("currencies")),
        "primary_service": primary_service,
        "service_required": primary_service,
        "services_required": primary_services,
        "source_of_wealth": first_non_empty(
            normalized.get("source_of_wealth"),
            wealth.get("source_of_wealth", {}).get("summary") if isinstance(wealth, dict) else "",
        ),
        "source_of_funds": first_non_empty(normalized.get("source_of_funds")),
        "monthly_volume": first_non_empty(normalized.get("monthly_volume"), normalized.get("expected_volume")),
        "expected_volume": first_non_empty(normalized.get("expected_volume"), normalized.get("monthly_volume")),
        "payment_corridors": first_non_empty(normalized.get("payment_corridors"), normalized.get("transaction_complexity")),
        "cross_border": bool(normalized.get("cross_border")),
    }

    scorer_input["_prescreening_mapping_corrections"] = _current_vs_corrected_flags(raw_prescreening, scorer_input)
    scorer_input["_canonical_submission_schema_version"] = normalized.get("schema_version")
    scorer_input["_risk_input_snapshot"] = {
        "company_name": scorer_input.get("company_name"),
        "country": scorer_input.get("country"),
        "sector": scorer_input.get("sector"),
        "entity_type": scorer_input.get("entity_type"),
        "ownership_structure": scorer_input.get("ownership_structure"),
        "operating_countries": scorer_input.get("operating_countries"),
        "target_markets": scorer_input.get("target_markets"),
        "primary_service": scorer_input.get("primary_service"),
        "source_of_wealth": scorer_input.get("source_of_wealth"),
        "source_of_funds": scorer_input.get("source_of_funds"),
        "monthly_volume": scorer_input.get("monthly_volume"),
        "cross_border": scorer_input.get("cross_border"),
        "derived_volume_currency": _derive_amount_currency(scorer_input.get("currencies")),
    }
    return scorer_input
