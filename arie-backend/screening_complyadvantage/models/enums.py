"""Enum types for CA's API surface.

Note: AMLTypeKey is intentionally NOT defined here as a strict StrEnum.
Wire-model fields use ``str`` to allow forward-compatibility with new CA
taxonomy keys. Known-key references for normalizer logic live in C1.b's
constants module.
"""

from enum import StrEnum


class NameType(StrEnum):
    PRIMARY = "PRIMARY"
    ALIAS = "ALIAS"
    LEGAL_NAME = "LEGAL_NAME"
    UNSPECIFIED = "UNSPECIFIED"


class ScreeningStatus(StrEnum):
    IN_PROGRESS = "IN-PROGRESS"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"


class WebhookType(StrEnum):
    """Known webhook types. Unknown values fall through to CAUnknownWebhookEnvelope."""

    CASE_CREATED = "CASE_CREATED"
    CASE_ALERT_LIST_UPDATED = "CASE_ALERT_LIST_UPDATED"
