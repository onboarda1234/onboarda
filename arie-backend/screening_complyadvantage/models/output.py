"""Output-side Pydantic v2 models for ComplyAdvantage responses."""

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import NameType, ScreeningStatus
from .primitives import CADateOfBirth, CAPaginatedCollection


class CAAdditionalField(BaseModel):
    name: str
    value: Optional[str] = None


class CAName(BaseModel):
    name: str
    type: Optional[NameType] = None


class CARelationship(BaseModel):
    name: Optional[str] = None
    relationship_type: Optional[str] = None


class CAPosition(BaseModel):
    title: Optional[str] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None


class CAProfileCompanyName(BaseModel):
    name: str
    type: Optional[NameType] = None


class CAProfileCompanyLocation(BaseModel):
    country: Optional[str] = None
    address: Optional[str] = None


class CAProfileCompanyRegistrationNumber(BaseModel):
    registration_number: str
    jurisdiction: Optional[str] = None


class CAProfilePerson(BaseModel):
    names: CAPaginatedCollection[CAName] = Field(default_factory=CAPaginatedCollection[CAName])
    date_of_birth: Optional[CADateOfBirth] = None
    nationality: Optional[str] = None
    countries: list[str] = Field(default_factory=list)
    relationships: CAPaginatedCollection[CARelationship] = Field(default_factory=CAPaginatedCollection[CARelationship])
    positions: CAPaginatedCollection[CAPosition] = Field(default_factory=CAPaginatedCollection[CAPosition])


class CAProfileCompany(BaseModel):
    names: CAPaginatedCollection[CAProfileCompanyName] = Field(default_factory=CAPaginatedCollection[CAProfileCompanyName])
    locations: CAPaginatedCollection[CAProfileCompanyLocation] = Field(default_factory=CAPaginatedCollection[CAProfileCompanyLocation])
    registration_numbers: CAPaginatedCollection[CAProfileCompanyRegistrationNumber] = Field(default_factory=CAPaginatedCollection[CAProfileCompanyRegistrationNumber])
    entity_type: Optional[str] = None


class CAMatchDetails(BaseModel):
    match_score: Optional[float] = None
    matched_name: Optional[str] = None
    matched_terms: list[str] = Field(default_factory=list)


class CARiskType(BaseModel):
    key: str
    label: Optional[str] = None


class CASanctionValue(BaseModel):
    program: Optional[str] = None
    authority: Optional[str] = None
    listed_at: Optional[str] = None


class CAWatchlistValue(BaseModel):
    list_name: Optional[str] = None
    authority: Optional[str] = None


class CAPEPValue(BaseModel):
    class_: str = Field(alias="class")
    position: Optional[str] = None
    country: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class CAMediaArticleSnippet(BaseModel):
    text: str
    offset: Optional[int] = None


class CAMediaArticleValue(BaseModel):
    title: Optional[str] = None
    url: Optional[str] = None
    publication_date: Optional[str] = None
    snippets: list[CAMediaArticleSnippet] = Field(default_factory=list)


class CASanctionIndicator(BaseModel):
    risk_type: CARiskType
    value: CASanctionValue


class CAWatchlistIndicator(BaseModel):
    risk_type: CARiskType
    value: CAWatchlistValue


class CAPEPIndicator(BaseModel):
    risk_type: CARiskType
    value: CAPEPValue


class CAMediaIndicator(BaseModel):
    risk_type: CARiskType
    value: CAMediaArticleValue


CARiskIndicator = Union[
    CASanctionIndicator,
    CAWatchlistIndicator,
    CAPEPIndicator,
    CAMediaIndicator,
]


class CARiskDetailInner(BaseModel):
    risk_type: CARiskType
    indicators: list[CARiskIndicator] = Field(default_factory=list)


class CARiskDetail(BaseModel):
    values: list[CARiskDetailInner] = Field(default_factory=list)


class CAProfile(BaseModel):
    """Profile envelope with KEY-PRESENCE discrimination."""

    identifier: str
    person: Optional[CAProfilePerson] = None
    company: Optional[CAProfileCompany] = None
    vessel: Optional[dict] = None
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


class CAStepDetail(BaseModel):
    step_identifier: Optional[str] = None
    status: Optional[ScreeningStatus] = None
    profiles: CAPaginatedCollection[CAProfile] = Field(default_factory=CAPaginatedCollection[CAProfile])


class CAWorkflowResponse(BaseModel):
    identifier: str
    status: Optional[ScreeningStatus] = None
    step_details: CAPaginatedCollection[CAStepDetail] = Field(default_factory=CAPaginatedCollection[CAStepDetail])


class CAAlertResponse(BaseModel):
    identifier: str
    profile: Optional[CAProfile] = None
    risk_details: CAPaginatedCollection[CARiskDetail] = Field(default_factory=CAPaginatedCollection[CARiskDetail])


class CACaseResponse(BaseModel):
    identifier: str
    case_type: Optional[str] = None
    alerts: CAPaginatedCollection[CAAlertResponse] = Field(default_factory=CAPaginatedCollection[CAAlertResponse])


class CACustomerResponse(BaseModel):
    identifier: str
    external_identifier: Optional[str] = None
    version: Optional[int] = None
    cases: CAPaginatedCollection[CACaseResponse] = Field(default_factory=CAPaginatedCollection[CACaseResponse])


class CAMonitoringState(BaseModel):
    enabled: bool = False
    status: Optional[str] = None


class CAEntityScreeningState(BaseModel):
    status: Optional[ScreeningStatus] = None
    workflow_identifier: Optional[str] = None
    monitoring: Optional[CAMonitoringState] = None
