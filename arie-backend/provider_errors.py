"""Provider error sanitisation helpers.

External provider failures are useful operational evidence, but raw response
bodies can contain request identifiers, URLs, tokens, or PII. These helpers
keep provider-facing failures parseable while preventing raw payload leakage
into API responses, prescreening reports, and audit-facing JSON.
"""

from __future__ import annotations

import re
from typing import Any


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization[:=]\s*bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)((?:token|secret|api[_-]?key|access[_-]?sig|authorization|password)=)([^&\s]+)"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(x-app-access-sig[:=]\s*)[A-Za-z0-9._~+/=-]+"),
)
_EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")


def sanitize_provider_error(value: Any, *, max_len: int = 240) -> str:
    """Return a bounded provider-error string with obvious secrets redacted."""
    try:
        text = str(value or "")
    except Exception:
        text = "unprintable provider error"
    text = re.sub(r"\s+", " ", text).strip()
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: match.group(1) + "[redacted]", text)
    text = _EMAIL_PATTERN.sub("[redacted-email]", text)
    text = _PHONE_PATTERN.sub("[redacted-phone]", text)
    # Keep the provider host for triage, but remove path/query components
    # because provider URLs often embed applicant or request identifiers.
    text = re.sub(r"https?://([^\s/?#]+)[^\s]*", r"https://\1/[redacted]", text)
    return text[:max_len]


def public_provider_error(operation: str = "provider request") -> str:
    """Stable external message for provider failures."""
    return f"{operation} failed: provider temporarily unavailable"
