"""Model validation tests for the ComplyAdvantage Pydantic v2 scaffold."""

import os
import sys

import pytest
from pydantic import TypeAdapter, ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screening_complyadvantage.models import input as ca_input_models
from screening_complyadvantage.models import output as ca_output_models
from screening_complyadvantage.models import primitives as ca_primitives
from screening_complyadvantage.models import webhooks as ca_webhook_models
from screening_models import TwoPassProvenance
from screening_complyadvantage.models import (
    CAAddress,
    CAAlertResponse,
    CACaseCreatedWebhook,
    CACreateAndScreenRequest,
    CACustomerCompanyInput,
    CACustomerInput,
    CACustomerPersonInput,
    CADateOfBirth,
    CAMediaArticleValue,
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
    CASanctionValue,
    CAUnknownWebhookEnvelope,
    CAWebhookEnvelope,
    CAWatchlistValue,
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
    risk_type = CARiskType(
        key="brand_new_ca_taxonomy",
        label="Future key",
        name="Future key name",
        taxonomy="future.synthetic",
    )
    assert risk_type.key == "brand_new_ca_taxonomy"
    assert risk_type.label == "Future key"
    assert risk_type.name == "Future key name"
    assert risk_type.taxonomy == "future.synthetic"


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


def test_known_webhook_type_preserves_extras():
    parsed = TypeAdapter(CAWebhookEnvelope).validate_python({
        "webhook_type": "CASE_CREATED",
        "api_version": "2",
        "account_identifier": "acct",
        "case_identifier": "case-1",
        "case_type": "customer",
        "customer": {"identifier": "cust", "external_identifier": "app", "version": 1, "tier": "fixture"},
        "subjects": [{"identifier": "sub", "external_identifier": "person", "type": "person"}],
        "delivery_attempt": 2,
    })
    assert isinstance(parsed, CACaseCreatedWebhook)
    assert parsed.delivery_attempt == 2
    assert parsed.customer.__pydantic_extra__ == {"tier": "fixture"}


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
        addresses=[{
            "full_address": "1 Test Road",
            "address_line1": "1 Test Road",
            "address_line2": "Unit Test",
            "town_name": "Port Louis",
            "postal_code": "TST",
            "country_subdivision": "Test District",
            "country": "MU",
            "country_code": "MU",
            "location_type": "residential_address",
        }],
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
    assert person.addresses[0].town_name == "Port Louis"


def test_address_fields_align_with_payload_emit_names():
    address = CAAddress(
        full_address="1 Test Road",
        address_line1="1 Test Road",
        address_line2="Unit Test",
        town_name="Test Town",
        postal_code="TST",
        country_subdivision="Test District",
        country="XX",
        country_code="XX",
        location_type="registered_address",
    )
    assert address.model_dump(exclude_none=True) == {
        "full_address": "1 Test Road",
        "address_line1": "1 Test Road",
        "address_line2": "Unit Test",
        "town_name": "Test Town",
        "postal_code": "TST",
        "country_subdivision": "Test District",
        "country": "XX",
        "country_code": "XX",
        "location_type": "registered_address",
    }
    assert "address_line_1" not in CAAddress.model_fields
    assert "city" not in CAAddress.model_fields
    assert "state" not in CAAddress.model_fields


def test_legacy_address_field_names_are_extras_not_explicit_fields():
    address = CAAddress(address_line_1="Legacy Line", city="Legacy City", state="Legacy State")
    assert address.__pydantic_extra__ == {
        "address_line_1": "Legacy Line",
        "city": "Legacy City",
        "state": "Legacy State",
    }
    reparsed = CAAddress.model_validate(address.model_dump())
    assert reparsed.__pydantic_extra__ == address.__pydantic_extra__


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


def test_pep_value_expanded_s3_fields_validate():
    pep = CAPEPValue.model_validate({
        "class": "PEP_CLASS_1",
        "position": "Synthetic Minister",
        "country": "XX",
        "level": "national",
        "scope_of_influence": "synthetic jurisdiction",
        "political_position_type": "synthetic_office",
        "institution_type": "synthetic_institution",
        "political_positions": [{"title": "Synthetic Minister"}],
        "political_parties": [{"name": "Test Fixture Party"}],
        "active_start_date": "2000-01-01",
        "active_end_date": "2005-01-01",
        "issuing_jurisdictions": [{"country": "XX"}],
        "source_metadata": {"source_identifier": "TEST-PEP"},
    })
    assert pep.level == "national"
    assert pep.political_positions == [{"title": "Synthetic Minister"}]
    assert pep.political_parties == [{"name": "Test Fixture Party"}]
    assert pep.issuing_jurisdictions == [{"country": "XX"}]
    assert pep.source_metadata == {"source_identifier": "TEST-PEP"}


def test_sanction_watchlist_and_media_expanded_fields_validate():
    sanction = CASanctionValue(
        program="TEST-SAN",
        authority="TEST-AUTH",
        listed_at="2000-01-01",
        source_metadata={"source_identifier": "TEST-SAN-META"},
        issuing_jurisdictions=[{"country": "XX"}],
        start_date="2000-01-01",
        status="active",
        reason="Synthetic reason",
    )
    watchlist = CAWatchlistValue(
        list_name="TEST-WATCH",
        authority="TEST-WATCH-AUTH",
        source_metadata={"source_identifier": "TEST-WATCH-META"},
        issuing_jurisdictions=["XX"],
        start_date="2001-01-01",
        status="active",
        reason="Synthetic watchlist reason",
    )
    media = CAMediaArticleValue(
        title="Test Article",
        source_name="Test Fixture Source",
        source_type="synthetic_news",
        publisher="Test Fixture Publisher",
        language="en",
        categories=["synthetic_adverse_media"],
        source_metadata={"source_identifier": "TEST-MEDIA"},
    )
    assert sanction.source_metadata["source_identifier"] == "TEST-SAN-META"
    assert watchlist.issuing_jurisdictions == ["XX"]
    assert media.categories == ["synthetic_adverse_media"]


def test_profile_expanded_entity_and_nested_fields_validate():
    profile = CAProfile(
        identifier="p1",
        entity_type="person",
        person=CAProfilePerson(
            additional_fields={"values": [{"name": "fixture_profile_source", "value": "synthetic"}]},
            risk_indicators=[{
                "risk_type": {"key": "r_pep_class_1", "label": "PEP class 1"},
                "value": {"class": "PEP_CLASS_1", "position": "Synthetic Minister", "country": "XX"},
            }],
        ),
        match_details=CAMatchDetails(),
        risk_types=[],
        risk_indicators=[],
    )
    company = CAProfileCompany(
        additional_fields={"values": [{"name": "fixture_company_source", "value": "synthetic"}]},
        risk_indicators=[{
            "risk_type": {"key": "r_direct_sanctions_exposure", "label": "Direct sanctions"},
            "value": {"program": "TEST-SAN", "authority": "TEST-AUTH"},
        }],
    )
    assert profile.entity_type == "person"
    assert profile.person.additional_fields.values[0].name == "fixture_profile_source"
    assert profile.person.risk_indicators[0].value.class_ == "PEP_CLASS_1"
    assert company.additional_fields.values[0].value == "synthetic"
    assert company.risk_indicators[0].value.program == "TEST-SAN"


def test_ca_wire_models_allow_unknown_extras():
    primitive = CADateOfBirth(year=1900, date_precision="year")
    risk_type = CARiskType(key="r_future", future_taxonomy=True)
    pep = CAPEPValue.model_validate({"class": "PEP_CLASS_1", "future_pep_field": "kept"})
    address = CAAddress(full_address="1 Test Road", future_address_field="kept")
    webhook = CACaseCreatedWebhook(
        webhook_type="CASE_CREATED",
        api_version="2",
        account_identifier="acct",
        case_identifier="case-1",
        case_type="customer",
        customer={"identifier": "cust", "external_identifier": "app", "version": 1},
        subjects=[],
        future_webhook_field="kept",
    )
    assert primitive.__pydantic_extra__ == {"date_precision": "year"}
    assert risk_type.__pydantic_extra__ == {"future_taxonomy": True}
    assert pep.__pydantic_extra__ == {"future_pep_field": "kept"}
    assert address.__pydantic_extra__ == {"future_address_field": "kept"}
    assert webhook.__pydantic_extra__ == {"future_webhook_field": "kept"}


def test_unknown_extras_survive_model_dump_round_trip():
    pep = CAPEPValue.model_validate({"class": "PEP_CLASS_1", "future_pep_field": {"kept": True}})
    reparsed_pep = CAPEPValue.model_validate(pep.model_dump(mode="json", by_alias=True))
    address = CAAddress(full_address="1 Test Road", future_address_field="kept")
    reparsed_address = CAAddress.model_validate(address.model_dump(mode="json"))
    assert reparsed_pep.__pydantic_extra__ == {"future_pep_field": {"kept": True}}
    assert reparsed_address.__pydantic_extra__ == {"future_address_field": "kept"}


def test_all_ca_pydantic_models_inherit_allow_extra_config():
    modules = (ca_primitives, ca_input_models, ca_output_models, ca_webhook_models)
    for module in modules:
        for name, obj in vars(module).items():
            if isinstance(obj, type) and hasattr(obj, "model_config") and obj.__module__ == module.__name__:
                assert obj.model_config.get("extra") == "allow", name


def test_two_pass_provenance_defaults_to_zero_counts():
    provenance = TwoPassProvenance()
    assert provenance.strict_match_count == 0
    assert provenance.both_count == 0


def test_collection_bearing_response_uses_paginated_collection():
    alert = CAAlertResponse(identifier="alert-1")
    assert isinstance(alert.risk_details, CAPaginatedCollection)
