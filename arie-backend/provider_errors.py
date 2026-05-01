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


def sanitize_provider_error(value: Any, *, max_len: int = 240) -> str:
    """Return a bounded provider-error string with obvious secrets redacted."""
    text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: match.group(1) + "[redacted]", text)
    # Remove query strings after redacting key/value secrets; endpoint paths
    # remain enough for operational triage.
    text = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", text)
    return text[:max_len]


def public_provider_error(operation: str = "provider request") -> str:
    """Stable external message for provider failures."""
    return f"{operation} failed: provider temporarily unavailable"
