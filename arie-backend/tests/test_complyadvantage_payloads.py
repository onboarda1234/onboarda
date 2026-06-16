from datetime import date

from screening_complyadvantage.payloads import (
    build_create_and_screen_payload,
    build_customer_company,
    build_customer_person,
    monitoring_enabled_from_payload,
    to_ca_address,
    to_ca_country_code,
    to_ca_dob,
    to_ca_nationalities,
)
from screening_complyadvantage.models import CACustomerInput


def test_to_ca_dob_accepts_date_and_iso_string():
    assert to_ca_dob(date(1980, 1, 31)) == {"day": 31, "month": 1, "year": 1980}
    assert to_ca_dob("1980-01-31T00:00:00Z") == {"day": 31, "month": 1, "year": 1980}
    assert to_ca_dob("1980") is None


def test_to_ca_country_code_maps_names_and_demonyms_to_alpha2():
    assert to_ca_country_code("Mauritius") == "MU"
    assert to_ca_country_code("Mauritian") == "MU"
    assert to_ca_country_code("United Kingdom") == "GB"
    assert to_ca_country_code("GB") == "GB"
    assert to_ca_country_code("Unknownland") is None


def test_to_ca_nationalities_returns_alpha2_array():
    assert to_ca_nationalities("Mauritius") == ["MU"]
    assert to_ca_nationalities(["Mauritius", "MU", "British"]) == ["MU", "GB"]
    assert to_ca_nationalities("Unknownland") is None


def test_to_ca_address_omits_empty_fields_and_uses_rich_postal_keys():
    result = to_ca_address(
        {
            "full_address": "1 Test Road, Test City",
            "line1": "1 Test Road",
            "city": "Test City",
            "postcode": "ABC123",
            "state": "",
            "country": "Mauritius",
            "country_code": "MU",
        },
        location_type="registered_address",
    )

    assert result == {
        "full_address": "1 Test Road, Test City",
        "address_line1": "1 Test Road",
        "town_name": "Test City",
        "postal_code": "ABC123",
        "country": "Mauritius",
        "country_code": "MU",
        "location_type": "registered_address",
    }


def test_to_ca_address_omits_location_type_only_address():
    assert to_ca_address({"country_of_birth": "Mauritius"}, location_type="residential_address") == {}


def test_build_customer_person_strict_vs_relaxed():
    party = {
        "person_key": "p-1",
        "first_name": "Jane",
        "last_name": "Doe",
        "full_name": "Jane Doe",
        "date_of_birth": "1980-01-31",
        "nationality": "Mauritius",
        "country_of_birth": "Mauritius",
        "email": "jane@example.test",
        "ownership_pct": 100,
        "declared_pep": True,
        "address": {"full_address": "1 Road", "country_code": "MU"},
    }

    strict_customer = build_customer_person(party, strict=True)
    relaxed_customer = build_customer_person(party, strict=False)
    strict = strict_customer["person"]
    relaxed = relaxed_customer["person"]

    assert strict_customer["external_identifier"] == "p-1"
    assert strict_customer["reference"] == "p-1"
    assert "external_identifier" not in strict
    assert "customer_reference" not in strict
    assert strict["nationality"] == ["MU"]
    assert strict["country_of_birth"] == "MU"
    assert "addresses" not in strict
    assert "contact_information" not in strict
    assert "occupation" not in strict
    assert "source_of_funds" not in strict
    assert "metadata" not in strict
    assert relaxed["full_name"] == "Jane Doe"
    assert "first_name" not in strict
    assert "last_name" not in strict
    assert "nationality" not in relaxed
    assert "addresses" not in relaxed


def test_build_customer_person_uses_last_name_only_when_full_name_missing():
    customer = build_customer_person(
        {
            "person_key": "p-2",
            "first_name": "Jane",
            "last_name": "Doe",
            "date_of_birth": "1980-01-31",
        },
        strict=False,
    )

    assert customer["person"] == {
        "last_name": "Doe",
        "date_of_birth": {"day": 31, "month": 1, "year": 1980},
    }


def test_build_customer_company_strict_vs_relaxed():
    app = {
        "company_name": "Acme Ltd",
        "brn": "C123",
        "country": "MU",
        "sector": "Payments",
        "registered_address": {
            "full_address": "1 Company Road, Port Louis",
            "country": "Mauritius",
            "postcode": "12345",
        },
        "entity_type": "Private Company",
        "incorporation_date": "2020-05-04T00:00:00Z",
        "application_ref": "ARF-1",
    }

    app["application_id"] = "app-1"
    strict_customer = build_customer_company(app, strict=True)
    relaxed_customer = build_customer_company(app, strict=False)
    strict = strict_customer["company"]
    relaxed = relaxed_customer["company"]

    assert strict_customer["external_identifier"] == "app-1"
    assert strict_customer["reference"] == "app-1"
    assert "external_identifier" not in strict
    assert "customer_reference" not in strict
    assert strict["legal_name"] == "Acme Ltd"
    assert strict["industry"] == "Payments"
    assert "registration_number" not in strict
    assert "jurisdiction" not in strict
    assert "entity_type" not in strict
    assert "incorporation_date" not in strict
    assert "addresses" not in strict
    assert "custom_fields" not in strict
    assert relaxed == {"legal_name": "Acme Ltd"}
    assert relaxed_customer["reference"] == "app-1"


def test_monitoring_block_defaults_true_and_can_be_disabled():
    default_payload = build_create_and_screen_payload({"company": {"legal_name": "Acme"}})
    disabled_payload = build_create_and_screen_payload(
        {"company": {"legal_name": "Acme"}},
        monitoring_enabled=False,
    )

    assert monitoring_enabled_from_payload(default_payload) is True
    assert monitoring_enabled_from_payload(disabled_payload) is False


def test_create_and_screen_external_identifier_override_stays_customer_level():
    payload = build_create_and_screen_payload(
        {"person": {"last_name": "Doe"}, "external_identifier": "stale", "reference": "stale"},
        external_identifier="app-1",
    )

    assert payload["customer"]["external_identifier"] == "app-1"
    assert payload["customer"]["reference"] == "app-1"
    assert "external_identifier" not in payload["customer"]["person"]


def test_create_and_screen_includes_screening_configuration_identifier_when_supplied():
    payload = build_create_and_screen_payload(
        {"company": {"legal_name": "Acme"}},
        screening_configuration_identifier="cfg-123",
    )

    assert payload["configuration"]["screening_configuration_identifier"] == "cfg-123"
    assert "screening" not in payload


def test_build_customer_company_uses_legal_name_key_not_name():
    company = build_customer_company({"legal_name": "Acme Legal", "application_id": "app-1"}, strict=False)

    assert company["company"]["legal_name"] == "Acme Legal"
    assert "name" not in company["company"]


def test_build_customer_company_validates_with_legal_name_only():
    customer = build_customer_company({"legal_name": "Acme Legal", "application_id": "app-1"}, strict=False)

    validated = CACustomerInput.model_validate(customer)

    assert validated.company.legal_name == "Acme Legal"
    assert validated.company.name is None


def test_build_customer_company_safely_omits_unavailable_fields_and_fake_address():
    customer = build_customer_company(
        {
            "legal_name": "Sparse Co",
            "application_id": "app-2",
            "country": "Mauritius",
        },
        strict=True,
    )

    company = customer["company"]
    validated = CACustomerInput.model_validate(customer)

    assert validated.company.legal_name == "Sparse Co"
    assert company["legal_name"] == "Sparse Co"
    assert "jurisdiction" not in company
    assert "registration_number" not in company
    assert "incorporation_date" not in company
    assert "entity_type" not in company
    assert "industry" not in company
    assert "addresses" not in company


def test_strict_company_payload_uses_ca_sandbox_compatible_fields_only():
    customer = build_customer_company(
        {
            "legal_name": "Sandbox Safe Ltd",
            "application_id": "app-3",
            "registration_number": "R123",
            "country": "Mauritius",
            "incorporation_date": "2020-01-01",
            "entity_type": "Private Company",
            "industry": "Payments",
            "website": "https://example.test",
            "registered_address": {"full_address": "1 Road", "country_code": "MU"},
            "application_ref": "ARF-3",
        },
        strict=True,
    )

    assert customer["company"] == {"legal_name": "Sandbox Safe Ltd", "industry": "Payments"}


def test_strict_person_payload_omits_ca_sandbox_rejected_address_and_contact_fields():
    customer = build_customer_person(
        {
            "person_key": "p-3",
            "full_name": "Jane Doe",
            "date_of_birth": "1980-01-31",
            "nationality": "Mauritius",
            "country_of_birth": "Mauritius",
            "email": "jane@example.test",
            "phone": "+230000000",
            "address": {"full_address": "1 Road", "country_code": "MU"},
        },
        strict=True,
    )

    assert customer["person"] == {
        "date_of_birth": {"day": 31, "month": 1, "year": 1980},
        "full_name": "Jane Doe",
        "nationality": ["MU"],
        "country_of_birth": "MU",
    }
