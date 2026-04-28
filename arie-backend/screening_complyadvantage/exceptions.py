"""ComplyAdvantage-specific exception hierarchy."""

import re


_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)password\s*[:=]\s*[^,\s)]+"),
    re.compile(r"(?i)access_token\s*[:=]\s*[^,\s)]+"),
    re.compile(r"(?i)authorization\s*[:=]\s*bearer\s+[^,\s)]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)username\s*[:=]\s*[^,\s)]+"),
)


def _sanitize_message(message):
    text = str(message)
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


class CAError(Exception):
    """Base class for ComplyAdvantage integration errors."""

    default_message = "ComplyAdvantage error"

    def __init__(self, message=None, **context):
        safe_message = self.default_message if message is None else _sanitize_message(message)
        self.context = {
            key: _sanitize_message(value)
            for key, value in context.items()
            if key not in {"password", "access_token", "authorization", "body"}
        }
        super().__init__(safe_message)

    def __repr__(self):
        return f"{self.__class__.__name__}({str(self)!r})"


class CAConfigurationError(CAError):
    """Invalid or missing ComplyAdvantage configuration."""

    default_message = "ComplyAdvantage configuration error"


class CAAuthenticationFailed(CAError):
    """ComplyAdvantage authentication failed."""

    default_message = "ComplyAdvantage authentication failed"


class CARateLimited(CAError):
    """ComplyAdvantage rate limit response."""

    default_message = "ComplyAdvantage rate limited"


class CATimeout(CAError):
    """ComplyAdvantage request timed out."""

    default_message = "ComplyAdvantage request timed out"


class CABadRequest(CAError):
    """ComplyAdvantage rejected the request."""

    default_message = "ComplyAdvantage request rejected"


class CAServerError(CAError):
    """ComplyAdvantage returned repeated server errors."""

    default_message = "ComplyAdvantage server error"


class CAUnexpectedResponse(CAError):
    """ComplyAdvantage returned an unexpected HTTP or JSON response."""

    default_message = "ComplyAdvantage unexpected response"
