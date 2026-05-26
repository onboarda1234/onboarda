"""Output-side Pydantic v2 models for ComplyAdvantage responses."""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import ConfigDict, Field, model_validator

from .enums import NameType, ScreeningStatus
from .primitives import CADateOfBirth, CAPaginatedCollection, CAWireModel


class CAAdditionalField(CAWireModel):
    name: str
    value: Optional[str] = None


class CAName(CAWireModel):
    name: str
    type: Optional[NameType] = None


class CARelationship(CAWireModel):
    name: Optional[str] = None
    relationship_type: Optional[str] = None


class CAPosition(CAWireModel):
    title: Optional[str] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None


class CAProfileCompanyName(CAWireModel):
    name: str
    type: Optional[NameType] = None


class CAProfileCompanyLocation(CAWireModel):
    country: Optional[str] = None
    address: Optional[str] = None


class CAProfileCompanyRegistrationNumber(CAWireModel):
    registration_number: str
    jurisdiction: Optional[str] = None


class CAProfilePerson(CAWireModel):
    names: CAPaginatedCollection[CAName] = Field(default_factory=CAPaginatedCollection[CAName])
    date_of_birth: Optional[CADateOfBirth] = None
    nationality: Optional[str] = None
    countries: list[str] = Field(default_factory=list)
    relationships: CAPaginatedCollection[CARelationship] = Field(default_factory=CAPaginatedCollection[CARelationship])
    positions: CAPaginatedCollection[CAPosition] = Field(default_factory=CAPaginatedCollection[CAPosition])
    additional_fields: CAPaginatedCollection[CAAdditionalField] = Field(default_factory=CAPaginatedCollection[CAAdditionalField])
    risk_indicators: list[CARiskIndicator] = Field(default_factory=list)


class CAProfileCompany(CAWireModel):
    names: CAPaginatedCollection[CAProfileCompanyName] = Field(default_factory=CAPaginatedCollection[CAProfileCompanyName])
    locations: CAPaginatedCollection[CAProfileCompanyLocation] = Field(default_factory=CAPaginatedCollection[CAProfileCompanyLocation])
    registration_numbers: CAPaginatedCollection[CAProfileCompanyRegistrationNumber] = Field(default_factory=CAPaginatedCollection[CAProfileCompanyRegistrationNumber])
    entity_type: Optional[str] = None
    additional_fields: CAPaginatedCollection[CAAdditionalField] = Field(default_factory=CAPaginatedCollection[CAAdditionalField])
    risk_indicators: list[CARiskIndicator] = Field(default_factory=list)


class CAMatchDetails(CAWireModel):
    match_score: Optional[float] = None
    matched_name: Optional[str] = None
    matched_terms: list[str] = Field(default_factory=list)


class CARiskType(CAWireModel):
    key: str
    label: Optional[str] = None
    name: Optional[str] = None
    taxonomy: Optional[str] = None


class CASanctionValue(CAWireModel):
    program: Optional[str] = None
    authority: Optional[str] = None
    listed_at: Optional[str] = None
    source_metadata: Optional[dict[str, Any]] = None
    issuing_jurisdictions: list[dict[str, Any] | str] = Field(default_factory=list)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: Optional[str] = None
    reason: Optional[str] = None


class CAWatchlistValue(CAWireModel):
    list_name: Optional[str] = None
    authority: Optional[str] = None
    source_metadata: Optional[dict[str, Any]] = None
    issuing_jurisdictions: list[dict[str, Any] | str] = Field(default_factory=list)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: Optional[str] = None
    reason: Optional[str] = None


class CAPEPValue(CAWireModel):
    class_: str = Field(alias="class")
    position: Optional[str] = None
    country: Optional[str] = None
    level: Optional[str] = None
    scope_of_influence: Optional[str] = None
    political_position_type: Optional[str] = None
    institution_type: Optional[str] = None
    political_positions: list[dict[str, Any]] = Field(default_factory=list)
    political_parties: list[dict[str, Any]] = Field(default_factory=list)
    active_start_date: Optional[str] = None
    active_end_date: Optional[str] = None
    issuing_jurisdictions: list[dict[str, Any] | str] = Field(default_factory=list)
    source_metadata: Optional[dict[str, Any]] = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class CAMediaArticleSnippet(CAWireModel):
    text: str
    offset: Optional[int] = None


class CAMediaArticleValue(CAWireModel):
    title: Optional[str] = None
    url: Optional[str] = None
    publication_date: Optional[str] = None
    snippets: list[CAMediaArticleSnippet] = Field(default_factory=list)
    source_name: Optional[str] = None
    source_type: Optional[str] = None
    publisher: Optional[str] = None
    language: Optional[str] = None
    categories: list[str] = Field(default_factory=list)
    source_metadata: Optional[dict[str, Any]] = None


class CASanctionIndicator(CAWireModel):
    risk_type: CARiskType
    value: CASanctionValue


class CAWatchlistIndicator(CAWireModel):
    risk_type: CARiskType
    value: CAWatchlistValue


class CAPEPIndicator(CAWireModel):
    risk_type: CARiskType
    value: CAPEPValue


class CAMediaIndicator(CAWireModel):
    risk_type: CARiskType
    value: CAMediaArticleValue


CARiskIndicator = Union[
    CASanctionIndicator,
    CAWatchlistIndicator,
    CAPEPIndicator,
    CAMediaIndicator,
]


class CARiskDetailInner(CAWireModel):
    risk_type: CARiskType
    indicators: list[CARiskIndicator] = Field(default_factory=list)


class CARiskDetail(CAWireModel):
    values: list[CARiskDetailInner] = Field(default_factory=list)


class CAProfile(CAWireModel):
    """Profile envelope with KEY-PRESENCE discrimination."""

    identifier: str
    person: Optional[CAProfilePerson] = None
    company: Optional[CAProfileCompany] = None
    vessel: Optional[dict] = None
    entity_type: Optional[str] = None
    match_details: CAMatchDetails
    risk_types: list[str]
    risk_indicators: list[CARiskIndicator]

    @model_validator(mode="after")
    def exactly_one_subject_kind(self) -> "CAProfile":
        present = [k for k in ("person", "company", "vessel") if getattr(self, k) is not None]
        if len(present) != 1:
            raise ValueError(f"CAProfile requires exactly one of person/company/vessel; got {present}")
        return self

    @property
    def subject_kind(self) -> Literal["person", "company", "vessel"]:
        if self.person is not None:
            return "person"
        if self.company is not None:
            return "company"
        return "vessel"


class CAStepDetail(CAWireModel):
    step_identifier: Optional[str] = None
    status: Optional[ScreeningStatus] = None
    profiles: CAPaginatedCollection[CAProfile] = Field(default_factory=CAPaginatedCollection[CAProfile])


class CAWorkflowResponse(CAWireModel):
    workflow_instance_identifier: str
    workflow_type: str
    steps: list[str] = Field(default_factory=list)
    status: ScreeningStatus
    step_details: dict[str, CAStepDetail] = Field(default_factory=dict)


class CAAlertResponse(CAWireModel):
    identifier: str
    profile: Optional[CAProfile] = None
    risk_details: CAPaginatedCollection[CARiskDetail] = Field(default_factory=CAPaginatedCollection[CARiskDetail])


class CACaseResponse(CAWireModel):
    identifier: str
    case_type: Optional[str] = None
    alerts: CAPaginatedCollection[CAAlertResponse] = Field(default_factory=CAPaginatedCollection[CAAlertResponse])


class CACustomerResponse(CAWireModel):
    identifier: str
    external_identifier: Optional[str] = None
    version: Optional[int] = None
    cases: CAPaginatedCollection[CACaseResponse] = Field(default_factory=CAPaginatedCollection[CACaseResponse])


class CAMonitoringState(CAWireModel):
    enabled: bool = False
    status: Optional[str] = None


class CAEntityScreeningState(CAWireModel):
    status: Optional[ScreeningStatus] = None
    workflow_identifier: Optional[str] = None
    monitoring: Optional[CAMonitoringState] = None
