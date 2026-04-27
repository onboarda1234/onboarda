"""Model validation tests for the ComplyAdvantage Pydantic v2 scaffold."""

import os
import sys

import pytest
from pydantic import TypeAdapter, ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screening_models import TwoPassProvenance
from screening_complyadvantage.models import (
    CAAlertResponse,
    CACreateAndScreenRequest,
    CACustomerCompanyInput,
    CACustomerInput,
    CACustomerPersonInput,
    CADateOfBirth,
    CAMatchDetails,
    CAName,
    CAPEPValue,
    CAPaginatedCollection,
    CAPagination,
    CAPaginationMeta,
    CAProfile,
    CAProfileCompany,
    CAProfileCompanyName,
    CAProfilePerson,
    CARiskType,
    CAUnknownWebhookEnvelope,
    CAWebhookEnvelope,
)


def test_paginated_collection_generic_typing():
    collection = CAPaginatedCollection[CAName](values=[{"name": "Jane Doe"}])
    assert isinstance(collection.values[0], CAName)


def test_pagination_meta_optional_on_followup_responses():
    page = CAPagination(next="", meta=None)
    assert page.meta is None
    first_page = CAPagination(meta=CAPaginationMeta(page_number=1, page_size=10, total_count=20))
    assert first_page.meta.total_count == 20


def test_profile_discriminator_enforces_exactly_one_present():
    profile = CAProfile(
        identifier="p1",
        person=CAProfilePerson(),
        match_details=CAMatchDetails(),
        risk_types=[],
        risk_indicators=[],
    )
    assert profile.subject_kind == "person"


def test_profile_discriminator_raises_on_zero_kinds():
    with pytest.raises(ValidationError):
        CAProfile(identifier="p1", match_details=CAMatchDetails(), risk_types=[], risk_indicators=[])


def test_profile_discriminator_raises_on_multiple_kinds():
    with pytest.raises(ValidationError):
        CAProfile(
            identifier="p1",
            person=CAProfilePerson(),
            company=CAProfileCompany(),
            match_details=CAMatchDetails(),
            risk_types=[],
            risk_indicators=[],
        )


def test_profile_subject_kind_returns_correct_discriminator():
    profile = CAProfile(
        identifier="c1",
        company=CAProfileCompany(),
        match_details=CAMatchDetails(),
        risk_types=[],
        risk_indicators=[],
    )
    assert profile.subject_kind == "company"


def test_unknown_aml_taxonomy_key_passes_through():
    risk_type = CARiskType(key="brand_new_ca_taxonomy", label="Future key")
    assert risk_type.key == "brand_new_ca_taxonomy"


def test_unknown_webhook_type_falls_through_to_fallback_envelope():
    parsed = TypeAdapter(CAWebhookEnvelope).validate_python({
        "webhook_type": "NEW_WEBHOOK",
        "api_version": "2",
        "account_identifier": "acct",
        "unexpected": {"kept": True},
    })
    assert isinstance(parsed, CAUnknownWebhookEnvelope)
    assert parsed.unexpected == {"kept": True}


def test_known_webhook_type_parses_as_typed_envelope():
    parsed = TypeAdapter(CAWebhookEnvelope).validate_python({
        "webhook_type": "CASE_CREATED",
        "api_version": "2",
        "account_identifier": "acct",
        "case_identifier": "case-1",
        "case_type": "customer",
        "customer": {"identifier": "cust", "external_identifier": "app", "version": 1},
        "subjects": [{"identifier": "sub", "external_identifier": "person", "type": "person"}],
    })
    assert parsed.webhook_type == "CASE_CREATED"
    assert parsed.case_identifier == "case-1"


def test_input_models_minimal_request_validates():
    request = CACreateAndScreenRequest(
        customer=CACustomerInput(person=CACustomerPersonInput(first_name="Jane", last_name="Doe"))
    )
    assert request.customer.person.first_name == "Jane"


def test_input_models_full_23_field_person_validates():
    person = CACustomerPersonInput(
        first_name="Jane",
        last_name="Doe",
        middle_name="A",
        full_name="Jane A Doe",
        date_of_birth={"year": 1980},
        gender="F",
        nationality="MU",
        country_of_birth="MU",
        place_of_birth="Port Louis",
        residential_information={"country_of_residence": "MU"},
        personal_identification={"passport_number": "P123"},
        contact_information={"email": "jane@example.com"},
        addresses=[{"city": "Port Louis", "country": "MU"}],
        occupation="Director",
        employer="Acme",
        salary={"amount": 1},
        net_worth={"amount": 2},
        source_of_wealth="business",
        source_of_funds="salary",
        external_identifier="ext",
        customer_reference="ref",
        custom_fields={"k": "v"},
        metadata={"source": "test"},
    )
    assert person.date_of_birth.year == 1980
    assert person.custom_fields == {"k": "v"}


def test_company_input_vs_profile_company_diverge_in_shape():
    input_company = CACustomerCompanyInput(name="Acme Ltd", registration_number="BRN")
    profile_company = CAProfileCompany(
        names=CAPaginatedCollection[CAProfileCompanyName](values=[{"name": "Acme Ltd"}])
    )
    assert input_company.name == "Acme Ltd"
    assert profile_company.names.values[0].name == "Acme Ltd"


def test_dob_year_only_partial_dates_supported():
    dob = CADateOfBirth(year=1975)
    assert dob.year == 1975
    assert dob.month is None


def test_pep_class_field_alias_works():
    pep = CAPEPValue.model_validate({"class": "PEP_CLASS_1", "position": "minister"})
    assert pep.class_ == "PEP_CLASS_1"
    assert pep.model_dump(by_alias=True)["class"] == "PEP_CLASS_1"


def test_two_pass_provenance_defaults_to_zero_counts():
    provenance = TwoPassProvenance()
    assert provenance.strict_match_count == 0
    assert provenance.both_count == 0


def test_collection_bearing_response_uses_paginated_collection():
    alert = CAAlertResponse(identifier="alert-1")
    assert isinstance(alert.risk_details, CAPaginatedCollection)
