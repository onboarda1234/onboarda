"""Unified company registry providers and normalization.

This module intentionally stays flat and is used by the existing screening and
portal handler paths. It does not introduce a separate registry subsystem.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

import requests
from requests.exceptions import RequestException, Timeout

from config import (
    COMPANIES_HOUSE_API_KEY as _CFG_COMPANIES_HOUSE_API_KEY,
    COMPANIES_HOUSE_API_URL as _CFG_COMPANIES_HOUSE_API_URL,
)
from environment import is_production
from provider_errors import sanitize_provider_error


logger = logging.getLogger("arie")

COMPANIES_HOUSE_API_KEY = _CFG_COMPANIES_HOUSE_API_KEY
COMPANIES_HOUSE_API_URL = _CFG_COMPANIES_HOUSE_API_URL
COMPANIES_HOUSE_TIMEOUT_SECONDS = 15

COMPANIES_HOUSE_PROVIDER = "companies_house"
OPENCORPORATES_PROVIDER = "opencorporates"

_PUBLIC_REGISTRY_UNAVAILABLE = (
    "Company registry is temporarily unavailable. Please try again or continue manually."
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _json_for_hash(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    except Exception:
        return str(value)


def _response_hash(raw: Any) -> str:
    return hashlib.sha256(_json_for_hash(raw).encode("utf-8")).hexdigest()


def _metadata(raw: Any, endpoint: str | None = None) -> dict[str, Any]:
    endpoint_value = endpoint
    simulated = False
    if isinstance(raw, dict):
        endpoint_value = endpoint_value or raw.get("_endpoint")
        simulated = bool(raw.get("_simulated"))
    return {
        "fetched_at": _utc_now_iso(),
        "endpoint": endpoint_value or "unknown",
        "response_hash": _response_hash(raw),
        "simulation": simulated,
    }


def provider_error(error_code: str, message: str | None = None) -> dict[str, Any]:
    return {
        "success": False,
        "provider": COMPANIES_HOUSE_PROVIDER,
        "error_code": error_code,
        "message": message or _PUBLIC_REGISTRY_UNAVAILABLE,
        "manual_fallback_allowed": True,
    }


def is_provider_error(result: Any) -> bool:
    return isinstance(result, dict) and result.get("success") is False and "error_code" in result


def provider_error_http_status(result: dict[str, Any]) -> int:
    return {
        "invalid_query": 400,
        "company_not_found": 404,
        "provider_rate_limited": 429,
        "provider_timeout": 503,
        "provider_malformed_response": 502,
        "provider_unavailable": 503,
        "provider_not_configured": 503,
    }.get(str(result.get("error_code") or ""), 503)


def _registered_address(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        text = _clean_text(value)
        return {"full_address": text} if text else None
    if not isinstance(value, dict):
        return None

    allowed = (
        "premises",
        "address_line_1",
        "address_line_2",
        "locality",
        "region",
        "postal_code",
        "country",
        "care_of",
        "po_box",
    )
    address = {key: _clean_text(value.get(key)) for key in allowed if _clean_text(value.get(key))}
    parts = [
        address.get("premises"),
        address.get("address_line_1"),
        address.get("address_line_2"),
        address.get("locality"),
        address.get("region"),
        address.get("postal_code"),
        address.get("country"),
    ]
    full_address = ", ".join(part for part in parts if part)
    if full_address:
        address["full_address"] = full_address
    return address or None


def _empty_company_model(provider: str, raw: Any, endpoint: str | None = None) -> dict[str, Any]:
    return {
        "provider": provider,
        "jurisdiction": None,
        "company_name": None,
        "company_number": None,
        "company_status": None,
        "entity_type": None,
        "incorporation_date": None,
        "registered_address": None,
        "sic_codes": [],
        "officers": [],
        "beneficial_owners": [],
        "source_metadata": _metadata(raw, endpoint),
    }


def _normalize_companies_house_company(raw: dict[str, Any], endpoint: str | None = None) -> dict[str, Any]:
    result = _empty_company_model(COMPANIES_HOUSE_PROVIDER, raw, endpoint)
    address = raw.get("registered_office_address") or raw.get("address")
    if not address and raw.get("address_snippet"):
        address = raw.get("address_snippet")
    result.update({
        "jurisdiction": "GB",
        "company_name": _clean_text(raw.get("company_name") or raw.get("title")),
        "company_number": _clean_text(raw.get("company_number")),
        "company_status": _clean_text(raw.get("company_status")),
        "entity_type": _clean_text(raw.get("type") or raw.get("company_type")),
        "incorporation_date": _clean_text(raw.get("date_of_creation")),
        "registered_address": _registered_address(address),
        "sic_codes": [str(code).strip() for code in (raw.get("sic_codes") or []) if str(code).strip()],
    })
    return result


def _normalize_opencorporates_company(raw: dict[str, Any], endpoint: str | None = None) -> dict[str, Any]:
    result = _empty_company_model(OPENCORPORATES_PROVIDER, raw, endpoint)
    result.update({
        "jurisdiction": _clean_text(raw.get("jurisdiction_code") or raw.get("jurisdiction")),
        "company_name": _clean_text(raw.get("name") or raw.get("company_name")),
        "company_number": _clean_text(raw.get("company_number")),
        "company_status": _clean_text(raw.get("current_status") or raw.get("status")),
        "entity_type": _clean_text(raw.get("company_type") or raw.get("type")),
        "incorporation_date": _clean_text(raw.get("incorporation_date")),
        "registered_address": _registered_address(
            raw.get("registered_address")
            or raw.get("registered_address_in_full")
        ),
        "sic_codes": [str(code).strip() for code in (raw.get("sic_codes") or []) if str(code).strip()],
    })
    return result


def _normalize_date_of_birth(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    dob = {}
    if raw.get("month") is not None:
        dob["month"] = raw.get("month")
    if raw.get("year") is not None:
        dob["year"] = raw.get("year")
    return dob or None


def _is_active_director_type(officer: dict[str, Any]) -> bool:
    role = str(officer.get("officer_role") or "").strip().lower()
    if not role:
        return False
    if officer.get("resigned_on"):
        return False
    if "secretary" in role:
        return False
    return "director" in role


def _officer_entity_type(officer_role: Any) -> str:
    role = str(officer_role or "").strip().lower()
    normalized_role = role.replace("_", "-")
    if "corporate-director" in normalized_role or "corporate director" in role:
        return "corporate"
    if "director" in role:
        return "individual"
    return "unknown"


def _normalize_companies_house_officer(raw: dict[str, Any], source_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    officer_entity_type = _officer_entity_type(raw.get("officer_role"))
    return {
        "provider": COMPANIES_HOUSE_PROVIDER,
        "jurisdiction": "GB",
        "name": _clean_text(raw.get("name")),
        "officer_role": _clean_text(raw.get("officer_role")),
        "officer_entity_type": officer_entity_type,
        "appointed_on": _clean_text(raw.get("appointed_on")),
        "resigned_on": None,
        "nationality": _clean_text(raw.get("nationality")),
        "occupation": _clean_text(raw.get("occupation")),
        "date_of_birth": _normalize_date_of_birth(raw.get("date_of_birth")),
        "candidate_type": "director",
        "is_candidate_director": True,
        "requires_individual_kyc": officer_entity_type == "individual",
        "requires_corporate_structure_review": officer_entity_type == "corporate",
        "status": "active",
        "source_metadata": source_metadata or {},
    }


def _normalize_officers(raw: dict[str, Any], endpoint: str | None = None) -> list[dict[str, Any]]:
    items = raw.get("items") or []
    source_metadata = _metadata(raw, endpoint)
    officers = []
    for item in items:
        if isinstance(item, dict) and _is_active_director_type(item):
            officers.append(_normalize_companies_house_officer(item, source_metadata))
    return officers


def _psc_text(item: dict[str, Any]) -> str:
    values = [
        item.get("kind"),
        item.get("description"),
        item.get("statement"),
        item.get("statement_description"),
        item.get("ceased_on"),
    ]
    return " ".join(str(value or "") for value in values).lower()


def _is_corporate_psc(item: dict[str, Any]) -> bool:
    text = _psc_text(item)
    return "corporate" in text or "legal-person" in text or "legal person" in text


def _psc_state(raw: dict[str, Any]) -> str:
    explicit = _clean_text(raw.get("psc_state"))
    if explicit in {"psc_found", "no_psc", "psc_exempt", "corporate_psc"}:
        return explicit

    items = [item for item in (raw.get("items") or []) if isinstance(item, dict)]
    combined = " ".join(_psc_text(item) for item in items)
    if "exempt" in combined:
        return "psc_exempt"
    if "no-individual-or-entity" in combined or "no registrable" in combined:
        return "no_psc"

    active_items = [item for item in items if not item.get("ceased_on")]
    if not active_items:
        return "no_psc"
    if any(_is_corporate_psc(item) for item in active_items):
        return "corporate_psc"
    return "psc_found"


def _psc_registry_statement_type(raw: dict[str, Any], state: str) -> str:
    items = [item for item in (raw.get("items") or []) if isinstance(item, dict)]
    if state == "no_psc":
        for item in items:
            text = _psc_text(item)
            if "no-individual-or-entity" in text or "no registrable" in text:
                return _clean_text(item.get("kind") or item.get("statement")) or "no_registrable_psc_statement"
        return "no_active_psc_entries"
    if state == "psc_exempt":
        for item in items:
            if "exempt" in _psc_text(item):
                return _clean_text(item.get("kind") or item.get("statement")) or "psc_exempt_statement"
        return "psc_exempt_statement"
    if state == "corporate_psc":
        for item in items:
            if not item.get("ceased_on") and _is_corporate_psc(item):
                return _clean_text(item.get("kind")) or "active_corporate_psc"
        return "active_corporate_psc"
    return "active_individual_psc"


def _psc_status_reason(raw: dict[str, Any], state: str, statement_type: str) -> str:
    if state == "no_psc":
        if statement_type == "no_active_psc_entries":
            return "No active PSC entries were returned by the registry."
        return "The registry statement indicates there is no registrable person or entity with significant control."
    if state == "psc_exempt":
        return "The registry returned a PSC exemption statement."
    if state == "corporate_psc":
        return "An active PSC entry is a corporate or legal-person PSC and requires corporate structure review."
    return "One or more active individual PSC entries were returned by the registry as beneficial owner candidates."


def _normalize_psc_candidate(raw: dict[str, Any], state: str) -> dict[str, Any]:
    is_corporate = _is_corporate_psc(raw)
    kind = "corporate" if is_corporate else "individual"
    if "legal" in _psc_text(raw) and not is_corporate:
        kind = "legal_person"
    return {
        "provider": COMPANIES_HOUSE_PROVIDER,
        "name": _clean_text(raw.get("name")),
        "kind": kind,
        "psc_state": state,
        "natures_of_control": [
            str(value).strip()
            for value in (raw.get("natures_of_control") or [])
            if str(value).strip()
        ],
        "notified_on": _clean_text(raw.get("notified_on")),
        "ceased_on": _clean_text(raw.get("ceased_on")),
        "country_of_residence": _clean_text(raw.get("country_of_residence")),
        "nationality": _clean_text(raw.get("nationality")),
        "date_of_birth": _normalize_date_of_birth(raw.get("date_of_birth")),
        "is_candidate_beneficial_owner": True,
        "candidate_type": "beneficial_owner_candidate",
    }


def _normalize_pscs(raw: dict[str, Any], endpoint: str | None = None) -> dict[str, Any]:
    state = _psc_state(raw)
    statement_type = _psc_registry_statement_type(raw, state)
    items = [item for item in (raw.get("items") or []) if isinstance(item, dict)]
    owners = []
    if state in {"psc_found", "corporate_psc"}:
        owners = [
            _normalize_psc_candidate(item, state)
            for item in items
            if not item.get("ceased_on")
        ]
    return {
        "provider": COMPANIES_HOUSE_PROVIDER,
        "jurisdiction": "GB",
        "company_number": _clean_text(raw.get("company_number")),
        "psc_state": state,
        "registry_statement_type": statement_type,
        "psc_status_reason": _psc_status_reason(raw, state, statement_type),
        "beneficial_owners": owners,
        "source_metadata": _metadata(raw, endpoint),
    }


def normalize_registry_result(provider: str, raw: Any, result_type: str | None = None) -> Any:
    """Normalize supported registry provider payloads into stable shapes."""
    if provider == COMPANIES_HOUSE_PROVIDER:
        endpoint = raw.get("_endpoint") if isinstance(raw, dict) else None
        if result_type == "search":
            items = raw.get("items") if isinstance(raw, dict) else []
            normalized = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_raw = dict(item)
                item_raw["_endpoint"] = endpoint
                if raw.get("_simulated"):
                    item_raw["_simulated"] = True
                normalized.append(_normalize_companies_house_company(item_raw, endpoint))
            return normalized
        if result_type == "officers":
            return _normalize_officers(raw if isinstance(raw, dict) else {}, endpoint)
        if result_type == "pscs":
            return _normalize_pscs(raw if isinstance(raw, dict) else {}, endpoint)
        return _normalize_companies_house_company(raw if isinstance(raw, dict) else {}, endpoint)

    if provider == OPENCORPORATES_PROVIDER:
        endpoint = raw.get("_endpoint") if isinstance(raw, dict) else None
        if result_type == "search":
            companies = []
            if isinstance(raw, dict):
                companies = raw.get("results", {}).get("companies", [])
            normalized = []
            for wrapped in companies:
                if not isinstance(wrapped, dict):
                    continue
                company = wrapped.get("company") if isinstance(wrapped.get("company"), dict) else wrapped
                company_raw = dict(company)
                company_raw["_endpoint"] = endpoint
                if raw.get("_simulated"):
                    company_raw["_simulated"] = True
                normalized.append(_normalize_opencorporates_company(company_raw, endpoint))
            return normalized
        return _normalize_opencorporates_company(raw if isinstance(raw, dict) else {}, endpoint)

    raise ValueError(f"Unsupported company registry provider: {provider}")


def _missing_key_result(endpoint: str, result_type: str, company_number: str | None = None, query: str | None = None) -> Any:
    if is_production():
        logger.error("Companies House lookup blocked: API key missing in production")
        return provider_error(
            "provider_not_configured",
            "Company registry is not configured. Please continue manually.",
        )
    logger.info("Companies House simulation used for endpoint=%s", endpoint)
    raw = _simulate_companies_house_raw(endpoint, result_type, company_number=company_number, query=query)
    return normalize_registry_result(COMPANIES_HOUSE_PROVIDER, raw, result_type)


def _simulate_companies_house_raw(
    endpoint: str,
    result_type: str,
    company_number: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    safe_name = _clean_text(query) or "Simulated Company"
    safe_number = _clean_text(company_number) or "00000000"
    if result_type == "search":
        return {
            "_endpoint": endpoint,
            "_simulated": True,
            "items": [{
                "title": f"{safe_name} Ltd",
                "company_number": "00000000",
                "company_status": "active",
                "company_type": "ltd",
                "date_of_creation": "2020-01-01",
                "address_snippet": "Cardiff, United Kingdom",
            }],
        }
    if result_type == "officers":
        return {
            "_endpoint": endpoint,
            "_simulated": True,
            "company_number": safe_number,
            "items": [{
                "name": "Simulated Director",
                "officer_role": "director",
                "appointed_on": "2020-01-01",
                "nationality": "British",
            }],
        }
    if result_type == "pscs":
        return {
            "_endpoint": endpoint,
            "_simulated": True,
            "company_number": safe_number,
            "active_count": 0,
            "items": [],
        }
    return {
        "_endpoint": endpoint,
        "_simulated": True,
        "company_name": "Simulated Company Ltd",
        "company_number": safe_number,
        "company_status": "active",
        "type": "ltd",
        "date_of_creation": "2020-01-01",
        "registered_office_address": {
            "locality": "Cardiff",
            "country": "United Kingdom",
        },
        "sic_codes": [],
    }


def _request_companies_house(endpoint: str, result_type: str, params: dict[str, Any] | None = None) -> Any:
    api_key = _clean_text(COMPANIES_HOUSE_API_KEY)
    if not api_key:
        return _missing_key_result(
            endpoint,
            result_type,
            company_number=endpoint.split("/")[2] if endpoint.startswith("/company/") else None,
            query=(params or {}).get("q"),
        )

    url = f"{COMPANIES_HOUSE_API_URL.rstrip('/')}{endpoint}"
    try:
        response = requests.get(
            url,
            params=params or None,
            auth=(api_key, ""),
            timeout=COMPANIES_HOUSE_TIMEOUT_SECONDS,
        )
    except Timeout:
        logger.warning("Companies House request timed out for endpoint=%s", endpoint)
        return provider_error("provider_timeout")
    except RequestException as exc:
        logger.warning(
            "Companies House request failed for endpoint=%s error=%s",
            endpoint,
            sanitize_provider_error(exc),
        )
        return provider_error("provider_unavailable")

    if response.status_code == 400:
        return provider_error("invalid_query", "Invalid company registry query. Please check the input or continue manually.")
    if response.status_code == 404:
        return provider_error("company_not_found", "Company was not found in the registry. Please continue manually.")
    if response.status_code == 429:
        return provider_error("provider_rate_limited")
    if response.status_code < 200 or response.status_code >= 300:
        logger.warning("Companies House returned HTTP %s for endpoint=%s", response.status_code, endpoint)
        return provider_error("provider_unavailable")

    try:
        raw = response.json()
    except ValueError:
        return provider_error("provider_malformed_response")

    if not isinstance(raw, dict):
        return provider_error("provider_malformed_response")

    raw = dict(raw)
    raw["_endpoint"] = endpoint
    malformed = _malformed_response(raw, result_type)
    if malformed:
        return provider_error("provider_malformed_response")
    return normalize_registry_result(COMPANIES_HOUSE_PROVIDER, raw, result_type)


def _malformed_response(raw: dict[str, Any], result_type: str) -> bool:
    if result_type == "search":
        return not isinstance(raw.get("items"), list)
    if result_type == "profile":
        return not (raw.get("company_name") or raw.get("company_number"))
    if result_type in {"officers", "pscs"}:
        return not isinstance(raw.get("items"), list)
    return False


def search_companies_house(query: str) -> Any:
    query = (query or "").strip()
    if not query:
        return provider_error("invalid_query", "A company search query is required.")
    return _request_companies_house("/search/companies", "search", params={"q": query})


def get_companies_house_profile(company_number: str) -> Any:
    company_number = (company_number or "").strip()
    if not company_number:
        return provider_error("invalid_query", "A company number is required.")
    return _request_companies_house(f"/company/{company_number}", "profile")


def get_companies_house_officers(company_number: str) -> Any:
    company_number = (company_number or "").strip()
    if not company_number:
        return provider_error("invalid_query", "A company number is required.")
    return _request_companies_house(f"/company/{company_number}/officers", "officers")


def get_companies_house_pscs(company_number: str) -> Any:
    company_number = (company_number or "").strip()
    if not company_number:
        return provider_error("invalid_query", "A company number is required.")
    return _request_companies_house(
        f"/company/{company_number}/persons-with-significant-control",
        "pscs",
    )
