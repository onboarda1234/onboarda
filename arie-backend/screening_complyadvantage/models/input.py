"""Input-side Pydantic v2 models for ComplyAdvantage requests."""

from typing import Optional

from pydantic import Field, model_validator

from .primitives import CADateOfBirth, CAWireModel


class CAResidentialInformation(CAWireModel):
    country_of_residence: Optional[str] = None
    residential_address: Optional[str] = None
    postcode: Optional[str] = None


class CAPersonalIdentification(CAWireModel):
    national_id: Optional[str] = None
    passport_number: Optional[str] = None
    document_number: Optional[str] = None
    issuing_country: Optional[str] = None


class CAContactInformation(CAWireModel):
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None


class CAAddress(CAWireModel):
    full_address: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    town_name: Optional[str] = None
    postal_code: Optional[str] = None
    country_subdivision: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None
    location_type: Optional[str] = None


class CACustomerPersonInput(CAWireModel):
    first_name: str
    last_name: str
    middle_name: Optional[str] = None
    full_name: Optional[str] = None
    date_of_birth: Optional[CADateOfBirth] = None
    gender: Optional[str] = None
    nationality: Optional[str] = None
    country_of_birth: Optional[str] = None
    place_of_birth: Optional[str] = None
    residential_information: Optional[CAResidentialInformation] = None
    personal_identification: Optional[CAPersonalIdentification] = None
    contact_information: Optional[CAContactInformation] = None
    addresses: list[CAAddress] = Field(default_factory=list)
    occupation: Optional[str] = None
    employer: Optional[str] = None
    salary: Optional[dict] = None  # TODO: tighten expected {amount, currency} after payload recon.
    net_worth: Optional[dict] = None  # TODO: tighten expected {amount, currency} after payload recon.
    source_of_wealth: Optional[str] = None
    source_of_funds: Optional[str] = None
    external_identifier: Optional[str] = None
    customer_reference: Optional[str] = None
    custom_fields: Optional[dict] = None  # TODO: tighten CA field map after payload recon.
    metadata: Optional[dict] = None


class CACustomerCompanyInput(CAWireModel):
    name: str
    registration_number: Optional[str] = None
    jurisdiction: Optional[str] = None
    incorporation_date: Optional[str] = None
    entity_type: Optional[str] = None
    industry: Optional[str] = None
    website: Optional[str] = None
    addresses: list[CAAddress] = Field(default_factory=list)
    external_identifier: Optional[str] = None
    customer_reference: Optional[str] = None
    custom_fields: Optional[dict] = None  # TODO: tighten CA field map after payload recon.
    metadata: Optional[dict] = None


class CACustomerInput(CAWireModel):
    person: Optional[CACustomerPersonInput] = None
    company: Optional[CACustomerCompanyInput] = None

    @model_validator(mode="after")
    def exactly_one_customer_kind(self) -> "CACustomerInput":
        present = [k for k in ("person", "company") if getattr(self, k) is not None]
        if len(present) != 1:
            raise ValueError(f"CACustomerInput requires exactly one of person/company; got {present}")
        return self


class CAMonitoringConfig(CAWireModel):
    enabled: bool = False
    frequency: Optional[str] = None
    notification_url: Optional[str] = None


class CAEntityScreeningConfig(CAWireModel):
    workflow_id: Optional[str] = None
    monitoring: Optional[CAMonitoringConfig] = None
    entity_type: Optional[str] = None


class CACreateAndScreenRequest(CAWireModel):
    customer: CACustomerInput
    screening: CAEntityScreeningConfig = Field(default_factory=CAEntityScreeningConfig)
    external_identifier: Optional[str] = None
