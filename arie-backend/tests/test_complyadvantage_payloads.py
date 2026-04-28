from datetime import date

from screening_complyadvantage.payloads import (
    build_create_and_screen_payload,
    build_customer_company,
    build_customer_person,
    monitoring_enabled_from_payload,
    to_ca_address,
    to_ca_dob,
)


def test_to_ca_dob_accepts_date_and_iso_string():
    assert to_ca_dob(date(1980, 1, 31)) == {"day": 31, "month": 1, "year": 1980}
    assert to_ca_dob("1980-01-31T00:00:00Z") == {"day": 31, "month": 1, "year": 1980}
    assert to_ca_dob("1980") is None


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


def test_build_customer_person_strict_vs_relaxed():
    party = {
        "person_key": "p-1",
        "first_name": "Jane",
        "last_name": "Doe",
        "full_name": "Jane Doe",
        "date_of_birth": "1980-01-31",
        "nationality": "MU",
        "email": "jane@example.test",
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
    assert strict["nationality"] == "MU"
    assert strict["addresses"][0]["full_address"] == "1 Road"
    assert strict["contact_information"]["email"] == "jane@example.test"
    assert relaxed["full_name"] == "Jane Doe"
    assert "nationality" not in relaxed
    assert "addresses" not in relaxed


def test_build_customer_company_strict_vs_relaxed():
    app = {
        "company_name": "Acme Ltd",
        "brn": "C123",
        "country": "MU",
        "sector": "Payments",
        "registered_address": "1 Company Road",
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
    assert strict["registration_number"] == "C123"
    assert strict["addresses"][0]["location_type"] == "registered_address"
    assert relaxed == {"name": "Acme Ltd"}
    assert relaxed_customer["reference"] == "app-1"


def test_monitoring_block_defaults_true_and_can_be_disabled():
    default_payload = build_create_and_screen_payload({"company": {"name": "Acme"}})
    disabled_payload = build_create_and_screen_payload(
        {"company": {"name": "Acme"}},
        monitoring_enabled=False,
    )

    assert monitoring_enabled_from_payload(default_payload) is True
    assert monitoring_enabled_from_payload(disabled_payload) is False


def test_create_and_screen_external_identifier_override_stays_customer_level():
    payload = build_create_and_screen_payload(
        {"person": {"first_name": "Jane", "last_name": "Doe"}},
        external_identifier="app-1",
    )

    assert payload["customer"]["external_identifier"] == "app-1"
    assert payload["customer"]["reference"] == "app-1"
    assert "external_identifier" not in payload["customer"]["person"]
