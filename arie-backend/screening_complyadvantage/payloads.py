"""Plain-dict payload builders for ComplyAdvantage create-and-screen flows."""

from copy import deepcopy
from datetime import date, datetime


def to_ca_dob(value):
    """Convert a RegMind DOB value to CA's structured day/month/year shape."""
    if not value:
        return None
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return {"day": value.day, "month": value.month, "year": value.year}
    if isinstance(value, str):
        try:
            parsed = datetime.strptime(value[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None
        return {"day": parsed.day, "month": parsed.month, "year": parsed.year}
    return None


def to_ca_address(data, *, location_type):
    """Map a RegMind address dict/string to CA's rich postal address shape."""
    if not data:
        return {}
    if isinstance(data, str):
        source = {"full_address": data}
    else:
        source = data

    mapped = {
        "full_address": _first(source, "full_address", "registered_address", "registered_office_address", "residential_address", "address"),
        "address_line1": _first(source, "address_line1", "address_line_1", "line1", "street"),
        "address_line2": _first(source, "address_line2", "address_line_2", "line2"),
        "town_name": _first(source, "town_name", "city", "town"),
        "postal_code": _first(source, "postal_code", "postcode", "zip"),
        "country_subdivision": _first(source, "country_subdivision", "state", "province", "region"),
        "country": _first(source, "country", "country_name"),
        "country_code": _first(source, "country_code", "country_iso", "jurisdiction"),
        "location_type": location_type,
    }
    return _drop_empty(mapped)


def build_customer_person(person, *, strict=True):
    """Build a CA customer.person dict from a RegMind party dict."""
    full_name = _first(person, "full_name", "name") or ""
    first_name = _first(person, "first_name") or _split_name(full_name)[0]
    last_name = _first(person, "last_name") or _split_name(full_name)[1]
    customer = _drop_empty({
        "first_name": first_name,
        "last_name": last_name or full_name or "Unknown",
        "middle_name": _first(person, "middle_name"),
        "full_name": full_name or " ".join(p for p in [first_name, last_name] if p),
        "date_of_birth": to_ca_dob(_first(person, "date_of_birth", "dob", "birth_date")),
        "external_identifier": _first(person, "person_key", "id"),
        "customer_reference": _first(person, "person_key", "id"),
    })
    if strict:
        customer.update(_drop_empty({
            "gender": _first(person, "gender"),
            "nationality": _first(person, "nationality"),
            "country_of_birth": _first(person, "country_of_birth"),
            "place_of_birth": _first(person, "place_of_birth"),
            "occupation": _first(person, "occupation"),
            "employer": _first(person, "employer"),
            "source_of_wealth": _first(person, "source_of_wealth"),
            "source_of_funds": _first(person, "source_of_funds"),
        }))
        address = to_ca_address(
            _first(person, "address", "residential_address", "registered_address") or person,
            location_type="residential_address",
        )
        if address:
            customer["addresses"] = [address]
        contact = _drop_empty({
            "email": _first(person, "email"),
            "phone": _first(person, "phone"),
            "mobile": _first(person, "mobile"),
        })
        if contact:
            customer["contact_information"] = contact
        metadata = _drop_empty({
            "ownership_pct": _first(person, "ownership_pct"),
            "declared_pep": _first(person, "is_pep", "declared_pep"),
        })
        if metadata:
            customer["metadata"] = metadata
    return {"person": customer}


def build_customer_company(application_data, *, strict=True):
    """Build a CA customer.company dict from RegMind application data."""
    name = _first(application_data, "company_name", "name", "legal_name") or "Unknown Company"
    company = _drop_empty({
        "name": name,
        "external_identifier": _first(application_data, "application_id", "id", "ref"),
        "customer_reference": _first(application_data, "application_id", "id", "ref"),
    })
    if strict:
        company.update(_drop_empty({
            "registration_number": _first(application_data, "registration_number", "brn"),
            "jurisdiction": _first(application_data, "jurisdiction", "country_code", "country"),
            "incorporation_date": _first(application_data, "incorporation_date"),
            "entity_type": _first(application_data, "entity_type"),
            "industry": _first(application_data, "industry", "sector"),
            "website": _first(application_data, "website"),
        }))
        address = to_ca_address(
            _first(application_data, "registered_address", "registered_office_address", "address") or application_data,
            location_type="registered_address",
        )
        if address:
            company["addresses"] = [address]
    return {"company": company}


def build_create_and_screen_payload(customer, *, monitoring_enabled=True, workflow_id=None, external_identifier=None):
    """Wrap a customer dict in CA create-and-screen workflow payload shape."""
    payload = {
        "customer": deepcopy(customer),
        "monitoring": {"entity_screening": {"enabled": bool(monitoring_enabled)}},
        "configuration": {},
    }
    if workflow_id:
        payload["screening"] = {"workflow_id": workflow_id}
    if external_identifier:
        payload["external_identifier"] = external_identifier
    return payload


def monitoring_enabled_from_payload(payload):
    return bool(
        payload.get("monitoring", {})
        .get("entity_screening", {})
        .get("enabled", False)
    )


def _first(data, *keys):
    if not isinstance(data, dict):
        return None
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    entity = data.get("entity")
    if isinstance(entity, dict):
        registered = entity.get("registered_address")
        if isinstance(registered, dict):
            for key in keys:
                value = registered.get(key)
                if value not in (None, ""):
                    return value
    return None


def _drop_empty(value):
    return {k: v for k, v in value.items() if v not in (None, "", [], {})}


def _split_name(full_name):
    parts = str(full_name or "").split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])
