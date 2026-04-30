"""Authenticated HTTP client for ComplyAdvantage screening APIs."""

import logging
import time

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

    def __init__(self, config, token_client=None, timeout=DEFAULT_TIMEOUT):
        self.config = config
        self.session = requests.Session()
        self.token_client = token_client or ComplyAdvantageTokenClient(config)
        self.timeout = timeout

    def get(self, path, params=None, *, timeout=None):
        return self.request("GET", path, params=params, timeout=timeout)

    def post(self, path, json_body=None, *, timeout=None):
        return self.request("POST", path, json_body=json_body, timeout=timeout)

    def request(self, method, path, *, params=None, json_body=None, timeout=None):
        method = method.upper()
        token = self.token_client.get_token()
        response = self._send(
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
        retry_response = self._send(
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
            raise CABadRequest("ComplyAdvantage API request rejected")
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
