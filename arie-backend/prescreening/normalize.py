"""
Backend-owned normalization for prescreening payloads.

Phase 1 goal:
- preserve current flat keys for backward compatibility
- add canonical nested structure
- project compatibility aliases needed by the current scorer
"""

from __future__ import annotations

import json

from prescreening.fields import (
    CURRENT_SCHEMA_VERSION,
    LEGACY_SESSION_PRESCREENING_FIELD_MAP,
    SESSION_PRESCREENING_FIELD_MAP,
    new_canonical_payload,
)


def safe_json_loads(val):
    if val is None:
        return {}
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def first_non_empty(*values):
    for value in values:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
        elif value not in (None, "", [], {}):
            return value
    return ""


def is_meaningful_value(value):
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _copy_list(value):
    if isinstance(value, list):
        return list(value)
    return []


def compose_source_of_funds_summary(prescreening):
    parts = []
    initial_type = first_non_empty(prescreening.get("source_of_funds_initial_type"))
    initial_detail = first_non_empty(prescreening.get("source_of_funds_initial_detail"))
    ongoing_type = first_non_empty(prescreening.get("source_of_funds_ongoing_type"))
    ongoing_detail = first_non_empty(prescreening.get("source_of_funds_ongoing_detail"))
    if initial_type:
        parts.append(f"Initial: {initial_type}")
    if initial_detail:
        parts.append(initial_detail)
    if ongoing_type:
        parts.append(f"Ongoing: {ongoing_type}")
    if ongoing_detail:
        parts.append(ongoing_detail)
    return "; ".join(parts)


def compose_source_of_wealth_summary(prescreening):
    source_type = first_non_empty(prescreening.get("source_of_wealth_type"))
    source_detail = first_non_empty(prescreening.get("source_of_wealth_detail"))
    if source_type and source_detail:
        return f"{source_type}; {source_detail}"
    return first_non_empty(source_type, source_detail, prescreening.get("source_of_wealth"))


def _derive_has_licence(legacy_text):
    text = (legacy_text or "").strip().lower()
    if not text:
        return None
    if text in ("none", "n/a", "na", "not applicable", "no"):
        return False
    return True


def _derive_cross_border_expected(transaction):
    if transaction.get("cross_border_expected") is True:
        return True

    complexity = (transaction.get("corridor_complexity") or "").lower()
    if any(term in complexity for term in ("cross-border", "international", "multi-currency", "multiple international", "high-risk corridor")):
        return True

    operating = transaction.get("operating_countries") or []
    target_markets = transaction.get("target_markets") or []
    primary_services = transaction.get("services") or {}
    services = primary_services.get("primary_services") or []
    if len(operating) > 1 or len(target_markets) > 0:
        return True
    for service in services:
        text = str(service).lower()
        if any(term in text for term in ("multi-currency", "cross-border", "international", "fx")):
            return True
    return False


def _derive_total_declared_pct(ubos, intermediaries):
    total = 0.0
    found = False
    for collection in (ubos, intermediaries):
        for item in collection:
            pct = item.get("ownership_pct")
            if isinstance(pct, (int, float)):
                total += float(pct)
                found = True
    if not found:
        return None
    return round(total, 4)


def _populate_canonical(merged):
    canonical = new_canonical_payload()

    canonical["entity"]["legal_name"] = first_non_empty(
        merged.get("registered_entity_name"),
        merged.get("company_name"),
        merged.get("entity_name"),
    )
    canonical["entity"]["trading_name"] = first_non_empty(merged.get("trading_name"))
    canonical["entity"]["type"] = first_non_empty(merged.get("entity_type"))
    canonical["entity"]["website"] = first_non_empty(merged.get("website"))
    canonical["entity"]["incorporation_country"] = first_non_empty(
        merged.get("country_of_incorporation"),
        merged.get("country"),
    )
    canonical["entity"]["registration_number"] = first_non_empty(
        merged.get("brn"),
        merged.get("registration_number"),
    )
    canonical["entity"]["incorporation_date"] = first_non_empty(merged.get("incorporation_date"))
    canonical["entity"]["registered_address"]["full_text"] = first_non_empty(merged.get("registered_address"))
    canonical["entity"]["headquarters_address"]["full_text"] = first_non_empty(merged.get("headquarters_address"))
    canonical["entity"]["contact"]["first_name"] = first_non_empty(merged.get("entity_contact_first"))
    canonical["entity"]["contact"]["last_name"] = first_non_empty(merged.get("entity_contact_last"))
    canonical["entity"]["contact"]["email"] = first_non_empty(merged.get("entity_contact_email"))
    canonical["entity"]["contact"]["phone_code"] = first_non_empty(merged.get("entity_contact_phone_code"))
    canonical["entity"]["contact"]["mobile"] = first_non_empty(merged.get("entity_contact_mobile"))

    canonical["business"]["sector"] = first_non_empty(merged.get("sector"))
    canonical["business"]["activity_description"] = first_non_empty(merged.get("business_overview"))
    canonical["business"]["management_overview"] = first_non_empty(merged.get("management_overview"))
    canonical["business"]["services"]["primary_services"] = _copy_list(merged.get("services_required"))
    canonical["business"]["account_purposes"] = _copy_list(merged.get("account_purposes"))

    canonical["ownership"]["structure_type"] = first_non_empty(merged.get("ownership_structure"))
    canonical["ownership"]["no_ubo_reason"] = first_non_empty(merged.get("no_ubo_reason"))
    canonical["ownership"]["total_declared_pct"] = _derive_total_declared_pct(
        merged.get("ubos") or [],
        merged.get("intermediary_shareholders") or merged.get("intermediaries") or [],
    )

    canonical["parties"]["directors"] = _copy_list(merged.get("directors"))
    canonical["parties"]["ubos"] = _copy_list(merged.get("ubos"))
    canonical["parties"]["intermediary_shareholders"] = _copy_list(
        merged.get("intermediary_shareholders") or merged.get("intermediaries")
    )

    canonical["transaction"]["operating_countries"] = _copy_list(
        merged.get("operating_countries") or merged.get("countries_of_operation")
    )
    canonical["transaction"]["target_markets"] = _copy_list(merged.get("target_markets"))
    canonical["transaction"]["currencies"] = _copy_list(merged.get("currencies"))
    canonical["transaction"]["corridor_complexity"] = first_non_empty(
        merged.get("transaction_complexity"),
        merged.get("payment_corridors"),
    )
    canonical["transaction"]["expected_monthly_volume"]["band_legacy"] = first_non_empty(
        merged.get("monthly_volume"),
        merged.get("expected_volume"),
    )
    estimated_activity = safe_json_loads(merged.get("estimated_monthly_activity"))
    if isinstance(estimated_activity, dict):
        canonical["transaction"]["estimated_activity"]["inflows"] = safe_json_loads(estimated_activity.get("inflows"))
        canonical["transaction"]["estimated_activity"]["outflows"] = safe_json_loads(estimated_activity.get("outflows"))
    canonical["transaction"]["expected_average_transaction"] = merged.get("expected_average_transaction")
    canonical["transaction"]["expected_highest_transaction"] = merged.get("expected_highest_transaction")

    canonical["wealth"]["source_of_wealth"]["type"] = first_non_empty(merged.get("source_of_wealth_type"))
    canonical["wealth"]["source_of_wealth"]["detail"] = first_non_empty(merged.get("source_of_wealth_detail"))
    canonical["wealth"]["source_of_wealth"]["summary"] = compose_source_of_wealth_summary(merged)

    canonical["funds"]["initial_source"] = {
        "type": first_non_empty(merged.get("source_of_funds_initial_type")),
        "detail": first_non_empty(merged.get("source_of_funds_initial_detail")),
    }
    canonical["funds"]["ongoing_source"] = {
        "type": first_non_empty(merged.get("source_of_funds_ongoing_type")),
        "detail": first_non_empty(merged.get("source_of_funds_ongoing_detail")),
    }
    canonical["funds"]["summary"] = first_non_empty(
        merged.get("source_of_funds"),
        compose_source_of_funds_summary(merged),
    )

    canonical["licensing"]["legacy_text"] = first_non_empty(merged.get("regulatory_licences"))
    canonical["licensing"]["has_licence"] = merged.get("has_licence")
    if canonical["licensing"]["has_licence"] is None:
        canonical["licensing"]["has_licence"] = _derive_has_licence(canonical["licensing"]["legacy_text"])
    canonical["licensing"]["regulated_activity_declared"] = merged.get("regulated_activity_declared")
    canonical["licensing"]["licences"] = _copy_list(merged.get("licences"))

    canonical["banking"]["existing_account"] = first_non_empty(merged.get("existing_bank_account"))
    canonical["banking"]["bank_name"] = first_non_empty(
        merged.get("existing_bank_name"),
        merged.get("bank_name"),
    )

    canonical["delivery_channel"]["introduction_method"] = first_non_empty(merged.get("introduction_method"))
    canonical["delivery_channel"]["referrer_name"] = first_non_empty(merged.get("referrer_name"))

    consents = {}
    for key in ("consent_data_processing", "consent_information_sharing",
                "consent_data_retention", "consent_ongoing_monitoring",
                "consent_marketing", "consent_declaration",
                "consent_pricing", "consent_terms"):
        val = merged.get(key)
        if val is not None:
            consents[key] = bool(val)
    canonical["submission"]["consents"] = consents

    canonical["submission"]["schema_version"] = first_non_empty(
        merged.get("schema_version"),
        CURRENT_SCHEMA_VERSION,
    )

    canonical["transaction"]["cross_border_expected"] = _derive_cross_border_expected(
        {
            "cross_border_expected": merged.get("cross_border_expected"),
            "corridor_complexity": canonical["transaction"]["corridor_complexity"],
            "operating_countries": canonical["transaction"]["operating_countries"],
            "target_markets": canonical["transaction"]["target_markets"],
            "services": canonical["business"]["services"],
        }
    )

    return canonical


def _project_compatibility_aliases(merged, canonical):
    merged["submission"] = canonical["submission"]
    merged["entity"] = canonical["entity"]
    merged["business"] = canonical["business"]
    merged["ownership"] = canonical["ownership"]
    merged["parties"] = canonical["parties"]
    merged["transaction"] = canonical["transaction"]
    merged["wealth"] = canonical["wealth"]
    merged["funds"] = canonical["funds"]
    merged["banking"] = canonical["banking"]
    merged["licensing"] = canonical["licensing"]
    merged["delivery_channel"] = canonical["delivery_channel"]

    merged["registered_entity_name"] = canonical["entity"]["legal_name"]
    if canonical["entity"]["legal_name"]:
        merged["company_name"] = canonical["entity"]["legal_name"]

    merged["country_of_incorporation"] = canonical["entity"]["incorporation_country"]
    merged["country"] = first_non_empty(merged.get("country"), canonical["entity"]["incorporation_country"])
    merged["brn"] = canonical["entity"]["registration_number"]
    merged["registration_number"] = canonical["entity"]["registration_number"]
    merged["incorporation_number"] = canonical["entity"]["registration_number"]
    merged["registered_address"] = canonical["entity"]["registered_address"]["full_text"]
    merged["registered_office_address"] = canonical["entity"]["registered_address"]["full_text"]
    merged["headquarters_address"] = canonical["entity"]["headquarters_address"]["full_text"]

    merged["services_required"] = canonical["business"]["services"]["primary_services"]
    primary_services = canonical["business"]["services"]["primary_services"]
    derived_primary_service = first_non_empty(
        merged.get("primary_service"),
        merged.get("service_required"),
        primary_services[0] if primary_services else "",
    )
    if derived_primary_service:
        merged["primary_service"] = derived_primary_service
        merged["service_required"] = derived_primary_service

    merged["countries_of_operation"] = canonical["transaction"]["operating_countries"]
    merged["operating_countries"] = canonical["transaction"]["operating_countries"]
    merged["target_markets"] = canonical["transaction"]["target_markets"]
    merged["currencies"] = canonical["transaction"]["currencies"]
    merged["transaction_complexity"] = canonical["transaction"]["corridor_complexity"]
    merged["payment_corridors"] = canonical["transaction"]["corridor_complexity"]
    merged["cross_border_expected"] = canonical["transaction"]["cross_border_expected"]
    merged["cross_border"] = canonical["transaction"]["cross_border_expected"]
    merged["monthly_volume"] = canonical["transaction"]["expected_monthly_volume"]["band_legacy"]
    if merged["monthly_volume"] and not merged.get("expected_volume"):
        merged["expected_volume"] = merged["monthly_volume"]
    merged["estimated_monthly_activity"] = canonical["transaction"]["estimated_activity"]

    merged["source_of_wealth_type"] = canonical["wealth"]["source_of_wealth"]["type"]
    merged["source_of_wealth_detail"] = canonical["wealth"]["source_of_wealth"]["detail"]
    merged["source_of_wealth"] = canonical["wealth"]["source_of_wealth"]["summary"]
    merged["source_of_funds"] = canonical["funds"]["summary"]

    merged["regulatory_licences"] = canonical["licensing"]["legacy_text"]
    merged["has_licence"] = canonical["licensing"]["has_licence"]
    merged["regulated_activity_declared"] = canonical["licensing"]["regulated_activity_declared"]
    merged["licences"] = canonical["licensing"]["licences"]

    merged["intermediaries"] = canonical["parties"]["intermediary_shareholders"]
    merged["intermediary_shareholders"] = canonical["parties"]["intermediary_shareholders"]
    merged["shareholders"] = canonical["parties"]["ubos"]
    merged["bank_name"] = first_non_empty(canonical["banking"]["bank_name"], merged.get("existing_bank_name"), merged.get("bank_name", ""))
    merged["existing_bank_name"] = canonical["banking"]["bank_name"]
    merged["existing_bank_account"] = canonical["banking"]["existing_account"]
    merged["schema_version"] = canonical["submission"]["schema_version"]

    # Project new canonical fields as flat aliases for backward compatibility
    merged["entity_type"] = canonical["entity"]["type"]
    merged["website"] = canonical["entity"]["website"]
    merged["entity_contact_first"] = canonical["entity"]["contact"]["first_name"]
    merged["entity_contact_last"] = canonical["entity"]["contact"]["last_name"]
    merged["entity_contact_email"] = canonical["entity"]["contact"]["email"]
    merged["entity_contact_phone_code"] = canonical["entity"]["contact"]["phone_code"]
    merged["entity_contact_mobile"] = canonical["entity"]["contact"]["mobile"]
    merged["management_overview"] = canonical["business"]["management_overview"]
    merged["referrer_name"] = canonical["delivery_channel"]["referrer_name"]

    return merged


def normalize_prescreening_data(data, existing=None):
    merged = {}
    current = safe_json_loads(existing)
    incoming = safe_json_loads((data or {}).get("prescreening_data", {}))

    if isinstance(current, dict):
        merged.update(current)
    if isinstance(incoming, dict):
        merged.update(incoming)

    payload = data or {}
    company_name = first_non_empty(
        payload.get("company_name"),
        payload.get("entity_name"),
        payload.get("registered_entity_name"),
        merged.get("registered_entity_name"),
        merged.get("company_name"),
    )
    if company_name:
        merged["registered_entity_name"] = company_name
        merged["company_name"] = company_name

    country = first_non_empty(payload.get("country"), merged.get("country_of_incorporation"))
    if country:
        merged["country_of_incorporation"] = country
        merged["country"] = country

    for key in ("entity_type", "ownership_structure", "sector", "brn"):
        if payload.get(key):
            merged[key] = payload.get(key)

    if payload.get("directors") is not None:
        merged["directors"] = _copy_list(payload.get("directors"))
    if payload.get("ubos") is not None:
        merged["ubos"] = _copy_list(payload.get("ubos"))
    if payload.get("intermediaries") is not None:
        merged["intermediaries"] = _copy_list(payload.get("intermediaries"))
    if payload.get("intermediary_shareholders") is not None:
        merged["intermediary_shareholders"] = _copy_list(payload.get("intermediary_shareholders"))

    canonical = _populate_canonical(merged)
    return _project_compatibility_aliases(merged, canonical)


def normalize_saved_session_prescreening(form_data):
    normalized = {}
    raw_form = safe_json_loads(form_data)
    if not isinstance(raw_form, dict):
        return normalized

    sources = []
    prescreening = raw_form.get("prescreening")
    if isinstance(prescreening, dict):
        sources.append(prescreening)
    sources.append(raw_form)

    for source in sources:
        if not isinstance(source, dict):
            continue
        for raw_key, normalized_key in SESSION_PRESCREENING_FIELD_MAP.items():
            raw_value = source.get(raw_key)
            if is_meaningful_value(raw_value):
                normalized[normalized_key] = raw_value
        for raw_key, normalized_key in LEGACY_SESSION_PRESCREENING_FIELD_MAP.items():
            raw_value = source.get(raw_key)
            if is_meaningful_value(raw_value):
                normalized[normalized_key] = raw_value

    if isinstance(raw_form.get("servicesRequired"), list):
        normalized["services_required"] = raw_form.get("servicesRequired")
    if isinstance(raw_form.get("countriesOfOperation"), list):
        normalized["countries_of_operation"] = raw_form.get("countriesOfOperation")
    if isinstance(raw_form.get("targetMarkets"), list):
        normalized["target_markets"] = raw_form.get("targetMarkets")
    if isinstance(raw_form.get("accountPurposes"), list):
        normalized["account_purposes"] = raw_form.get("accountPurposes")

    consent_map = {
        "f-consent-declaration": "consent_declaration",
        "f-consent-pricing": "consent_pricing",
        "f-consent-terms": "consent_terms",
    }
    for source in sources:
        if not isinstance(source, dict):
            continue
        for raw_key, normalized_key in consent_map.items():
            if raw_key in source:
                normalized[normalized_key] = bool(source.get(raw_key))

    legacy_consent_map = {
        "consentDeclaration": "consent_declaration",
        "consentPricing": "consent_pricing",
        "consentTerms": "consent_terms",
    }
    for source in sources:
        if not isinstance(source, dict):
            continue
        for raw_key, normalized_key in legacy_consent_map.items():
            if raw_key in source:
                normalized[normalized_key] = bool(source.get(raw_key))

    return normalize_prescreening_data({"prescreening_data": normalized})


def merge_prescreening_sources(primary, fallback):
    merged = {}
    fallback_data = safe_json_loads(fallback)
    primary_data = safe_json_loads(primary)
    if isinstance(fallback_data, dict):
        merged.update(fallback_data)
    if isinstance(primary_data, dict):
        for key, value in primary_data.items():
            if is_meaningful_value(value):
                merged[key] = value
    return normalize_prescreening_data({"prescreening_data": merged})


def resolve_application_company_name(data, prescreening_data, fallback=""):
    pdata = safe_json_loads(prescreening_data)
    entity = pdata.get("entity") if isinstance(pdata, dict) else {}
    return first_non_empty(
        (data or {}).get("company_name"),
        (data or {}).get("entity_name"),
        (data or {}).get("registered_entity_name"),
        entity.get("legal_name") if isinstance(entity, dict) else "",
        pdata.get("registered_entity_name") if isinstance(pdata, dict) else "",
        pdata.get("company_name") if isinstance(pdata, dict) else "",
        fallback,
    )
