"""Officer-safe ComplyAdvantage evidence archival helpers.

RegMind keeps provider reference IDs and decision evidence needed for audit, but
must never persist or expose provider credentials, bearer tokens, cookies, or
webhook signatures in application evidence/audit payloads.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_PROTECTED_KEY_TOKENS = {
    "authorization",
    "cookie",
    "signature",
    "webhooksignature",
    "xcomplyadvantagesignature",
    "secret",
    "password",
    "token",
    "accesstoken",
    "refreshtoken",
    "bearertoken",
    "apikey",
    "clientsecret",
}


def redact_provider_payload(value: Any) -> Any:
    """Return a JSON-safe copy with provider secrets removed.

    Reference identifiers such as case, alert, risk, profile, workflow, and
    customer IDs are intentionally preserved because they are required for
    audit traceability back to ComplyAdvantage Mesh.
    """

    if isinstance(value, Mapping):
        redacted = {}
        for key, item in value.items():
            text_key = str(key)
            if _is_protected_key(text_key):
                redacted[text_key] = "[redacted]"
            else:
                redacted[text_key] = redact_provider_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_provider_payload(item) for item in value]
    return value


def _is_protected_key(key: str) -> bool:
    normalized = "".join(ch for ch in str(key).lower() if ch.isalnum())
    return any(token in normalized for token in _PROTECTED_KEY_TOKENS)
