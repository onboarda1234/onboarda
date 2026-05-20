"""Verification provider failure taxonomy.

Provider/request-path failures are operational failures, not ordinary document
review findings. This module keeps those failures explicit, PII-safe, and
CloudWatch-parseable without changing screening-provider behaviour.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from provider_errors import sanitize_provider_error


FAILURE_RETRYABLE_TRANSIENT = "retryable_transient"
FAILURE_TERMINAL_INVALID_REQUEST = "terminal_invalid_request"
FAILURE_REVIEW_REQUIRED_BUSINESS = "review_required_business"

PROVIDER_CLAUDE = "claude"
DEFAULT_OPERATION = "document_verification"

_TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
_INVALID_REQUEST_MARKERS = (
    "invalid_request_error",
    "pdf specified was not valid",
    "document specified was not valid",
    "image specified was not valid",
    "invalid file",
    "unsupported media type",
)
_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "connection error",
    "connection failed",
    "rate limit",
    "temporarily unavailable",
    "service unavailable",
)
_PII_NOISE_MARKERS = (
    "pii decrypt",
    "pii decryption",
    "invalid encryption token",
    "unreadable token",
)


class VerificationProviderError(RuntimeError):
    """Exception carrying a sanitized verification provider failure payload."""

    def __init__(self, failure: Mapping[str, Any]):
        self.failure = dict(failure)
        super().__init__(
            f"{self.failure.get('provider', 'provider')} "
            f"{self.failure.get('reason_code', 'verification_provider_error')}"
        )


def _compact_text(value: Any) -> str:
    try:
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, sort_keys=True, default=str)
        return str(value or "")
    except Exception:
        return "unprintable provider error"


def _error_body(error: Any) -> Any:
    body = getattr(error, "body", None)
    if body is not None:
        return body
    response = getattr(error, "response", None)
    return getattr(response, "text", None) or getattr(response, "body", None)


def _provider_status_code(error: Any, explicit_status_code: int | None = None) -> int | None:
    if explicit_status_code is not None:
        try:
            return int(explicit_status_code)
        except (TypeError, ValueError):
            return None
    for attr in ("status_code", "status"):
        value = getattr(error, attr, None)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _provider_error_type(error: Any, body: Any) -> str:
    for attr in ("type", "error_type", "code"):
        value = getattr(error, attr, None)
        if value:
            return _safe_token(value)
    if isinstance(body, dict):
        candidate = body.get("type") or body.get("error_type") or body.get("code")
        nested = body.get("error")
        if isinstance(nested, dict):
            nested_candidate = nested.get("type") or nested.get("code")
            if nested_candidate and (not candidate or str(candidate).lower() == "error"):
                candidate = nested_candidate
        if candidate:
            return _safe_token(candidate)
    return "unknown"


def _safe_token(value: Any, default: str = "unknown") -> str:
    token = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value or "").strip().lower()).strip("_")
    return token or default


def is_pii_decryption_noise(value: Any) -> bool:
    """Return True for the known staging PII decrypt warning pattern."""

    text = _compact_text(value).lower()
    return any(marker in text for marker in _PII_NOISE_MARKERS)


def classify_verification_provider_failure(
    error: Any,
    *,
    provider: str = PROVIDER_CLAUDE,
    operation: str = DEFAULT_OPERATION,
    status_code: int | None = None,
    model: str | None = None,
) -> dict:
    """Classify a provider/request failure into the PR8 taxonomy."""

    body = _error_body(error)
    text = " ".join(
        part for part in (
            _compact_text(error),
            _compact_text(body),
        ) if part
    )
    sanitized = sanitize_provider_error(text, max_len=300)
    lowered = text.lower()
    provider_status = _provider_status_code(error, status_code)
    provider_error_type = _provider_error_type(error, body)
    pii_context_signal = is_pii_decryption_noise(text)

    if provider_status == 400 or any(marker in lowered for marker in _INVALID_REQUEST_MARKERS):
        classification = FAILURE_TERMINAL_INVALID_REQUEST
        retryable = False
        reason_code = "claude_invalid_pdf" if "pdf specified was not valid" in lowered else "claude_invalid_request"
    elif provider_status in _TRANSIENT_STATUS_CODES or any(marker in lowered for marker in _TRANSIENT_MARKERS):
        classification = FAILURE_RETRYABLE_TRANSIENT
        retryable = True
        reason_code = "provider_transient_error"
    else:
        classification = FAILURE_RETRYABLE_TRANSIENT
        retryable = True
        reason_code = "provider_request_failed"

    return {
        "classification": classification,
        "reason_code": reason_code,
        "provider": _safe_token(provider, "provider"),
        "operation": _safe_token(operation, DEFAULT_OPERATION),
        "retryable": retryable,
        "provider_status_code": provider_status,
        "provider_error_type": provider_error_type,
        "provider_message": sanitized,
        "model": _safe_token(model, "") if model else None,
        "pii_context_signal": pii_context_signal,
    }


def provider_failure_from_result(result: Any) -> dict | None:
    if not isinstance(result, dict):
        return None
    failure = result.get("verification_failure")
    return failure if isinstance(failure, dict) else None


def build_provider_failure_result(
    error_or_failure: Any,
    *,
    provider: str = PROVIDER_CLAUDE,
    operation: str = DEFAULT_OPERATION,
    status_code: int | None = None,
    model: str | None = None,
) -> dict:
    """Build a verification result for a provider/request-path failure."""

    if isinstance(error_or_failure, VerificationProviderError):
        failure = dict(error_or_failure.failure)
    elif isinstance(error_or_failure, dict) and "classification" in error_or_failure:
        failure = dict(error_or_failure)
    else:
        failure = classify_verification_provider_failure(
            error_or_failure,
            provider=provider,
            operation=operation,
            status_code=status_code,
            model=model,
        )

    reason = failure.get("reason_code") or "provider_request_failed"
    if failure.get("retryable"):
        message = "Verification provider failed before document checks could be trusted. Retry verification."
    else:
        message = "Verification provider rejected the request before document checks could be trusted."

    check = {
        "id": "PROVIDER-ERR",
        "label": "Verification Provider",
        "classification": "system",
        "type": "provider_error",
        "result": "fail",
        "message": f"{message} reason={reason}",
        "source": "provider_error",
    }
    return {
        "checks": [check],
        "overall": "failed",
        "confidence": 0.0,
        "red_flags": [],
        "warnings": [check["message"]],
        "verification_failure": failure,
        "verification_failure_classification": failure.get("classification"),
        "provider_failure": True,
        "retryable": bool(failure.get("retryable")),
        "requires_review": True,
        "ai_source": f"{failure.get('provider', provider)}_error",
        "ai_error": failure.get("provider_message"),
    }


def file_size_band(file_size: Any) -> str:
    try:
        size = int(file_size or 0)
    except (TypeError, ValueError):
        size = 0
    mb = 1024 * 1024
    if size <= 0:
        return "zero_or_unknown"
    if size < mb:
        return "lt_1mb"
    if size < 5 * mb:
        return "1mb_5mb"
    if size < 10 * mb:
        return "5mb_10mb"
    if size <= 25 * mb:
        return "10mb_25mb"
    return "gt_25mb"


def format_verification_failure_log_line(
    failure: Mapping[str, Any],
    *,
    environment: str,
    document_id: str = "",
    application_id: str = "",
    doc_type: str = "",
    mime_type: str = "",
    file_size: Any = None,
    status: str = "failed",
) -> str:
    """Return a CloudWatch-parseable, PII-safe verification failure log line."""

    fields = {
        "event": "verification_provider_failure",
        "provider": failure.get("provider"),
        "classification": failure.get("classification"),
        "reason_code": failure.get("reason_code"),
        "retryable": str(bool(failure.get("retryable"))).lower(),
        "provider_status": failure.get("provider_status_code") or "none",
        "provider_error_type": failure.get("provider_error_type") or "unknown",
        "operation": failure.get("operation") or DEFAULT_OPERATION,
        "status": status,
        "doc_type": doc_type or "unknown",
        "mime_type": mime_type or "unknown",
        "file_size_band": file_size_band(file_size),
        "environment": environment,
    }
    if document_id:
        fields["document_id"] = document_id
    if application_id:
        fields["application_id"] = application_id
    if failure.get("model"):
        fields["model"] = failure.get("model")
    if failure.get("pii_context_signal"):
        fields["pii_context_signal"] = "true"

    parts = ["verification_provider_telemetry"]
    for key, value in fields.items():
        parts.append(f"{key}={_safe_token(value, 'unknown')}")
    return " ".join(parts)
