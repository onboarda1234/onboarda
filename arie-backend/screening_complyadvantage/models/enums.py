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
    NOT_STARTED = "NOT-STARTED"
    IN_PROGRESS = "IN-PROGRESS"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    ERRORED = "ERRORED"

    @classmethod
    def _missing_(cls, value):
        """Forward-compat: a provider status must never crash screening.

        Any unrecognised CA workflow status (a new value CA adds, or a
        terminal-failure state we don't model) degrades to ERRORED — a terminal
        state the orchestrator handles gracefully (records a degraded/re-screen
        report) instead of raising a ValidationError.
        """
        return cls.ERRORED


class WebhookType(StrEnum):
    """Known webhook types. Unknown values fall through to CAUnknownWebhookEnvelope."""

    CASE_CREATED = "CASE_CREATED"
    CASE_ALERT_LIST_UPDATED = "CASE_ALERT_LIST_UPDATED"
