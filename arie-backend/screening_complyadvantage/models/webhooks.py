"""Webhook envelope models for /webhooks/complyadvantage."""

from typing import Literal, Optional, Union

from pydantic import ConfigDict

from .primitives import CAWireModel


class CAWebhookCustomer(CAWireModel):
    identifier: str
    external_identifier: str
    version: int


class CAWebhookSubject(CAWireModel):
    identifier: str
    external_identifier: str
    type: str


class CAWebhookCaseStage(CAWireModel):
    identifier: str
    display_name: Optional[str] = None
    display_order: Optional[int] = None
    stage_type: Optional[str] = None


class CACaseCreatedWebhook(CAWireModel):
    """Per s2 recon — case-creation event."""

    webhook_type: Literal["CASE_CREATED"]
    api_version: str
    account_identifier: str
    case_identifier: str
    case_type: str
    case_state: Optional[str] = None
    case_stage: Optional[CAWebhookCaseStage] = None
    customer: CAWebhookCustomer
    subjects: list[CAWebhookSubject]


class CACaseAlertListUpdatedWebhook(CAWireModel):
    """Per s2 recon — case-alert-list-updated event."""

    webhook_type: Literal["CASE_ALERT_LIST_UPDATED"]
    api_version: str
    account_identifier: str
    case_identifier: str
    alert_identifiers: list[str]
    customer: CAWebhookCustomer
    subjects: list[CAWebhookSubject]


class CAUnknownWebhookEnvelope(CAWireModel):
    """Fallback for webhook types not yet characterized."""

    webhook_type: str
    api_version: str
    account_identifier: str
    case_identifier: Optional[str] = None
    customer: Optional[CAWebhookCustomer] = None

    model_config = ConfigDict(extra="allow")


CAWebhookEnvelope = Union[
    CACaseCreatedWebhook,
    CACaseAlertListUpdatedWebhook,
    CAUnknownWebhookEnvelope,
]
