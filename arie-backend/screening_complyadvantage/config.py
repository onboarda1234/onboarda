"""Local ComplyAdvantage configuration boundary."""

import os
from dataclasses import dataclass

from .exceptions import CAConfigurationError


@dataclass(frozen=True)
class CAConfig:
    """Validated ComplyAdvantage OAuth and API configuration."""

    api_base_url: str
    auth_url: str
    realm: str
    username: str
    password: str
    strict_workflow_id: str
    relaxed_workflow_id: str

    @classmethod
    def from_env(cls):
        api_base_url = _required_env("COMPLYADVANTAGE_API_BASE_URL", "API base URL")
        auth_url = _required_env("COMPLYADVANTAGE_AUTH_URL", "auth URL")
        realm = os.environ.get("COMPLYADVANTAGE_REALM")
        if realm is None or not realm.strip():
            raise CAConfigurationError("realm not configured")
        if realm != "regmind":
            raise CAConfigurationError("realm must be 'regmind'")
        username = _required_env("COMPLYADVANTAGE_USERNAME", "account identifier")
        password = _required_env("COMPLYADVANTAGE_PASSWORD", "credential")
        strict_workflow_id = _required_env("COMPLYADVANTAGE_STRICT_WORKFLOW_ID", "strict workflow ID")
        relaxed_workflow_id = _required_env("COMPLYADVANTAGE_RELAXED_WORKFLOW_ID", "relaxed workflow ID")
        return cls(
            api_base_url=api_base_url.rstrip("/"),
            auth_url=auth_url.rstrip("/"),
            realm=realm,
            username=username,
            password=password,
            strict_workflow_id=strict_workflow_id,
            relaxed_workflow_id=relaxed_workflow_id,
        )


def _required_env(name, label):
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise CAConfigurationError(f"{label} not configured")
    return value.strip()
