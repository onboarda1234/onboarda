"""OAuth token acquisition and in-memory cache for ComplyAdvantage."""

import hashlib
import logging
import random
import threading
import time
from dataclasses import dataclass

import requests

from .exceptions import (
    CAAuthenticationFailed,
    CABadRequest,
    CAConfigurationError,
    CAError,
    CARateLimited,
    CAServerError,
    CATimeout,
    CAUnexpectedResponse,
)


logger = logging.getLogger(__name__)
DEFAULT_TIMEOUT = (3.0, 15.0)
_REFRESH_BUFFER_SECONDS = 60.0
_MAX_ATTEMPTS = 4


def _username_fingerprint(username: str) -> str:
    """Return a deterministic, non-reversible username correlation key."""

    return hashlib.sha256(username.encode()).hexdigest()[:8]


@dataclass
class _TokenCache:
    access_token: str
    expires_at_monotonic: float
    token_type: str = ""
    scope: str = ""


class ComplyAdvantageTokenClient:
    """Fetch and cache ComplyAdvantage OAuth bearer tokens."""

    def __init__(self, config, session=None, clock=time.monotonic, timeout=DEFAULT_TIMEOUT):
        self.config = config
        self.session = session or requests.Session()
        self.clock = clock
        self.timeout = timeout
        self._cache = None
        self._lock = threading.RLock()

    def get_token(self):
        with self._lock:
            if self._cache_is_fresh_locked():
                return self._cache.access_token
            return self._refresh_locked()

    def force_refresh(self):
        with self._lock:
            return self._refresh_locked(force=True)

    def clear_cache(self):
        with self._lock:
            self._cache = None

    def _cache_is_fresh_locked(self):
        if self._cache is None:
            return False
        remaining = self._cache.expires_at_monotonic - self.clock()
        return remaining > _REFRESH_BUFFER_SECONDS

    def _refresh_locked(self, force=False):
        if not force and self._cache_is_fresh_locked():
            return self._cache.access_token

        last_error = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            started = self.clock()
            try:
                response = self.session.post(
                    self.config.auth_url,
                    json={
                        "realm": self.config.realm,
                        "username": self.config.username,
                        "password": self.config.password,
                    },
                    headers={"Content-Type": "application/json"},
                    timeout=self.timeout,
                )
                duration_ms = int((self.clock() - started) * 1000)
                self._log(
                    "ca_auth_response",
                    "POST",
                    _path_for_log(self.config.auth_url),
                    attempt=attempt,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                )
                token = self._handle_auth_response(response)
                return token
            except (CAConfigurationError, CAAuthenticationFailed, CABadRequest, CARateLimited):
                raise
            except requests.exceptions.Timeout as exc:
                last_error = CATimeout("ComplyAdvantage auth request timed out")
                self._log_error("POST", _path_for_log(self.config.auth_url), attempt, exc)
            except requests.exceptions.RequestException as exc:
                last_error = CAUnexpectedResponse("ComplyAdvantage auth network error")
                self._log_error("POST", _path_for_log(self.config.auth_url), attempt, exc)
            except CAServerError as exc:
                last_error = exc
            except CAError:
                raise

            if attempt == _MAX_ATTEMPTS:
                raise last_error
            self._sleep_before_retry(attempt)

        raise CAUnexpectedResponse()

    def _handle_auth_response(self, response):
        status = response.status_code
        if 200 <= status < 300:
            try:
                payload = response.json()
            except ValueError as exc:
                raise CAUnexpectedResponse("ComplyAdvantage auth response was not JSON") from exc
            access_token = payload.get("access_token")
            expires_in = payload.get("expires_in")
            if not access_token or expires_in is None:
                raise CAUnexpectedResponse("ComplyAdvantage auth token shape invalid")
            try:
                expires_in_seconds = float(expires_in)
            except (TypeError, ValueError) as exc:
                raise CAUnexpectedResponse("ComplyAdvantage auth expiry invalid") from exc
            if expires_in_seconds <= 0:
                raise CAUnexpectedResponse("ComplyAdvantage auth expiry invalid")
            self._cache = _TokenCache(
                access_token=access_token,
                token_type=str(payload.get("token_type", "")),
                expires_at_monotonic=self.clock() + expires_in_seconds,
                scope=str(payload.get("scope", "")),
            )
            return access_token
        if status == 401:
            raise CAAuthenticationFailed("ComplyAdvantage authentication failed")
        if status == 429:
            raise CARateLimited("ComplyAdvantage auth rate limited")
        if 400 <= status < 500:
            raise CABadRequest("ComplyAdvantage auth request rejected")
        if 500 <= status < 600:
            raise CAServerError("ComplyAdvantage auth server error")
        raise CAUnexpectedResponse("ComplyAdvantage auth status unexpected")

    def _sleep_before_retry(self, attempt):
        delay = min(0.5 * (2 ** (attempt - 1)), 4.0)
        time.sleep(delay + random.uniform(0, delay * 0.2))

    def _log(self, event, method, path, *, attempt, status_code=None, duration_ms=None):
        logger.info(
            "%s method=%s path=%s status=%s duration_ms=%s attempt=%s realm=%s username_fp=%s",
            event,
            method,
            path,
            status_code,
            duration_ms,
            attempt,
            self.config.realm,
            _username_fingerprint(self.config.username),
        )

    def _log_error(self, method, path, attempt, exc):
        logger.warning(
            "ca_auth_error method=%s path=%s attempt=%s realm=%s username_fp=%s exception=%s",
            method,
            path,
            attempt,
            self.config.realm,
            _username_fingerprint(self.config.username),
            exc.__class__.__name__,
        )


def _path_for_log(url):
    parsed = requests.utils.urlparse(url)
    return parsed.path or "/"
