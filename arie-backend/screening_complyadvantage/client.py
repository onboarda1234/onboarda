"""Authenticated HTTP client for ComplyAdvantage screening APIs."""

import logging
import time
from urllib.parse import urlparse

import requests

from .auth import DEFAULT_TIMEOUT, ComplyAdvantageTokenClient, _username_fingerprint
from .exceptions import (
    CAAuthenticationFailed,
    CABadRequest,
    CARateLimited,
    CAServerError,
    CATimeout,
    CAUnexpectedResponse,
)
from .observability import emit_metric, endpoint_category, path_template, status_family


logger = logging.getLogger(__name__)


class ComplyAdvantageClient:
    """Small authenticated wrapper around ComplyAdvantage HTTP calls."""

    def __init__(self, config, token_client=None, timeout=DEFAULT_TIMEOUT, retry_backoff_seconds=0.25, sleep_fn=None):
        self.config = config
        self.session = requests.Session()
        self.token_client = token_client or ComplyAdvantageTokenClient(config)
        self.timeout = timeout
        self.retry_backoff_seconds = retry_backoff_seconds
        self.sleep_fn = sleep_fn or time.sleep

    def get(self, path, params=None, *, timeout=None):
        return self.request("GET", path, params=params, timeout=timeout)

    def post(self, path, json_body=None, *, timeout=None):
        return self.request("POST", path, json_body=json_body, timeout=timeout)

    def request(self, method, path, *, params=None, json_body=None, timeout=None):
        method = method.upper()
        token = self.token_client.get_token()
        response = self._send_with_retries(
            method,
            path,
            token,
            params=params,
            json_body=json_body,
            timeout=timeout,
            attempt=1,
        )
        if response.status_code != 401:
            return self._map_response(response, path)

        self.token_client.clear_cache()
        token = self.token_client.force_refresh()
        retry_response = self._send_with_retries(
            method,
            path,
            token,
            params=params,
            json_body=json_body,
            timeout=timeout,
            attempt=2,
        )
        if retry_response.status_code == 401:
            raise CAAuthenticationFailed("ComplyAdvantage authentication failed after refresh")
        return self._map_response(retry_response, path)

    def _send_with_retries(self, method, path, token, *, params, json_body, timeout, attempt):
        response = self._send(
            method,
            path,
            token,
            params=params,
            json_body=json_body,
            timeout=timeout,
            attempt=attempt,
        )
        # GET fetches are idempotent and safe to retry once on transient
        # provider/rate-limit errors. POST create-and-screen is not retried here
        # to avoid duplicate provider workflows.
        if method != "GET" or response.status_code not in (429, 500, 502, 503, 504):
            return response
        self.sleep_fn(self.retry_backoff_seconds)
        emit_metric(
            "ca_api_retry",
            metric_name="CaApiRetries",
            component="client",
            outcome="retry",
            method=method,
            path_template=path_template(self._log_path(path)),
            status_code=response.status_code,
            attempt=attempt + 1,
        )
        return self._send(
            method,
            path,
            token,
            params=params,
            json_body=json_body,
            timeout=timeout,
            attempt=attempt + 1,
        )

    def _send(self, method, path, token, *, params, json_body, timeout, attempt):
        url = self._url(path)
        log_path = self._log_path(path)
        started = time.monotonic()
        try:
            response = self.session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout or self.timeout,
            )
        except requests.exceptions.Timeout as exc:
            self._log_error(method, log_path, attempt, exc)
            raise CATimeout("ComplyAdvantage API request timed out") from exc
        except requests.exceptions.RequestException as exc:
            self._log_error(method, log_path, attempt, exc)
            raise CAUnexpectedResponse("ComplyAdvantage API network error") from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "ca_api_response method=%s path=%s status=%s duration_ms=%s attempt=%s realm=%s username_fp=%s",
            method,
            log_path,
            response.status_code,
            duration_ms,
            attempt,
            self.config.realm,
            _username_fingerprint(self.config.username),
        )
        category = endpoint_category(log_path)
        family = status_family(response.status_code)
        emit_metric(
            "ca_api_request",
            metric_name="CaApiRequests",
            component="client",
            outcome="success" if 200 <= response.status_code < 400 else "failure",
            method=method,
            path_template=path_template(log_path),
            status_code=response.status_code,
            status_family=family,
            endpoint_category=category,
            attempt=attempt,
        )
        emit_metric(
            "ca_api_latency",
            metric_name="CaApiLatencyMs",
            value=duration_ms,
            unit="Milliseconds",
            component="client",
            outcome="success" if 200 <= response.status_code < 400 else "failure",
            status_family=family,
            endpoint_category=category,
        )
        return response

    def _map_response(self, response, path):
        status = response.status_code
        if 200 <= status < 300:
            try:
                return response.json()
            except ValueError as exc:
                raise CAUnexpectedResponse("ComplyAdvantage API response was not JSON") from exc
        if status == 429:
            raise CARateLimited("ComplyAdvantage API rate limited")
        if 400 <= status < 500:
            context = _safe_provider_error_context(response, self._log_path(path))
            raise CABadRequest("ComplyAdvantage API request rejected", **context)
        if 500 <= status < 600:
            raise CAServerError("ComplyAdvantage API server error")
        raise CAUnexpectedResponse("ComplyAdvantage API status unexpected")

    def _url(self, path):
        return f"{self.config.api_base_url}{self._normalize_path(path)}"

    def _log_path(self, path):
        return self._normalize_path(path).split("?", 1)[0]

    def _normalize_path(self, path):
        if not path.startswith("/"):
            path = f"/{path}"
        return path

    def _log_error(self, method, path, attempt, exc):
        logger.warning(
            "ca_api_error method=%s path=%s attempt=%s realm=%s username_fp=%s exception=%s",
            method,
            path,
            attempt,
            self.config.realm,
            _username_fingerprint(self.config.username),
            exc.__class__.__name__,
        )
        emit_metric(
            "ca_api_request",
            metric_name="CaApiRequests",
            component="client",
            outcome="failure",
            method=method,
            path_template=path_template(path),
            status_family=status_family(error=exc),
            endpoint_category=endpoint_category(path),
            attempt=attempt,
            exception_class=exc.__class__.__name__,
        )


def _safe_provider_error_context(response, path):
    context = {
        "status_code": response.status_code,
        "path": path,
    }
    try:
        payload = response.json()
    except ValueError:
        return context
    if not isinstance(payload, dict):
        return context
    fields = {
        "provider_error_type": payload.get("type"),
        "provider_error_title": payload.get("title"),
        "provider_error_detail": payload.get("detail"),
        "provider_error_identifier": payload.get("identifier"),
    }
    properties = payload.get("properties")
    if isinstance(properties, dict):
        errors = properties.get("errors")
        if errors:
            fields["provider_error_count"] = len(errors) if isinstance(errors, list) else 1
    for key, value in fields.items():
        safe_value = _safe_status_value(value)
        if safe_value:
            context[key] = safe_value
    return context


def _safe_status_value(value, *, limit=240):
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (int, float, bool)):
        return value
    text = str(value).strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        text = parsed.path or parsed.netloc
    return text[:limit]
