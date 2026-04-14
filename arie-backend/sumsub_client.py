#!/usr/bin/env python3
"""
Sumsub API Integration Module
==============================
Provides a production-ready SumsubClient class for KYC/AML verification.
Handles authentication, applicant management, document uploads, and webhook verification.

Features:
- HMAC-SHA256 request signing
- Retry logic with exponential backoff for 5xx errors
- Built-in API usage tracking with monthly cost cap enforcement
- Webhook signature verification
- Type hints and comprehensive logging
- Singleton pattern for client reuse

Usage:
    from sumsub_client import get_sumsub_client

    client = get_sumsub_client()
    applicant = client.create_applicant(external_user_id="user_123", first_name="John")
    token = client.generate_access_token(external_user_id="user_123")
    status = client.get_applicant_status(applicant["applicant_id"])
"""

import os
import json
import time
import hmac
import hashlib
import logging
import base64
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple
from calendar import monthrange
import requests
from requests.exceptions import RequestException, Timeout

from environment import is_production, get_sumsub_individual_level_name


logger = logging.getLogger("sumsub_client")


class SumsubAPIError(Exception):
    """Base exception for Sumsub API errors."""
    pass


class SumsubAuthError(SumsubAPIError):
    """Raised when authentication fails."""
    pass


class SumsubRetryError(SumsubAPIError):
    """Raised after max retries exceeded."""
    pass


class APIUsageTracker:
    """
    Tracks Sumsub API usage and enforces monthly cost cap.
    Uses simple in-memory counter with month boundary reset.
    """

    def __init__(self, monthly_cap_usd: float = 500.0):
        """
        Initialize usage tracker.

        Args:
            monthly_cap_usd: Monthly spending limit in USD (default $500).
        """
        self.monthly_cap_usd = monthly_cap_usd
        self.current_month: Optional[Tuple[int, int]] = None  # (year, month)
        self.monthly_usage_usd = 0.0
        self.call_count = 0

        # Estimated costs per API call (in USD) based on Sumsub pricing
        self.costs = {
            "create_applicant": 0.0,      # Free
            "get_applicant": 0.0,          # Free
            "add_document": 0.5,           # ~$0.50 per document
            "get_verification_result": 0.0,  # Free
            "get_aml_screening": 1.5,      # ~$1.50 per AML check
            "generate_access_token": 0.0,  # Free
            "default": 0.1,                # Default ~$0.10 for other calls
        }

    def _reset_if_new_month(self) -> None:
        """Reset usage counter if we've entered a new month."""
        now = datetime.now(timezone.utc)
        month_tuple = (now.year, now.month)

        if self.current_month is None or self.current_month != month_tuple:
            self.current_month = month_tuple
            self.monthly_usage_usd = 0.0
            self.call_count = 0
            logger.info(f"Reset monthly usage tracker for {month_tuple[0]}-{month_tuple[1]:02d}")

    def add_call(self, operation: str) -> Tuple[float, float]:
        """
        Record an API call and return remaining budget.

        Args:
            operation: Name of the API operation (e.g., "add_document").

        Returns:
            Tuple of (cost_usd, remaining_budget_usd).

        Raises:
            SumsubAPIError: If monthly cap would be exceeded.
        """
        self._reset_if_new_month()

        cost = self.costs.get(operation, self.costs["default"])
        new_total = self.monthly_usage_usd + cost

        if new_total > self.monthly_cap_usd:
            remaining = self.monthly_cap_usd - self.monthly_usage_usd
            logger.error(
                f"Monthly API cap ({self.monthly_cap_usd} USD) exceeded. "
                f"Operation '{operation}' would cost ${cost:.2f}, "
                f"but only ${remaining:.2f} remaining."
            )
            raise SumsubAPIError(
                f"Monthly Sumsub API budget exceeded. "
                f"Limit: ${self.monthly_cap_usd:.2f}, Used: ${self.monthly_usage_usd:.2f}"
            )

        self.monthly_usage_usd = new_total
        self.call_count += 1

        return cost, self.monthly_cap_usd - new_total

    def get_usage(self) -> Dict[str, Any]:
        """Get current usage statistics."""
        self._reset_if_new_month()
        return {
            "current_month": f"{self.current_month[0]}-{self.current_month[1]:02d}" if self.current_month else None,
            "monthly_usage_usd": round(self.monthly_usage_usd, 2),
            "monthly_cap_usd": self.monthly_cap_usd,
            "remaining_budget_usd": round(self.monthly_cap_usd - self.monthly_usage_usd, 2),
            "call_count": self.call_count,
        }


class SumsubClient:
    """
    Production-ready Sumsub API client with authentication, retry logic, and usage tracking.

    Attributes:
        app_token: Sumsub app token from env var SUMSUB_APP_TOKEN.
        secret_key: Sumsub secret key from env var SUMSUB_SECRET_KEY.
        base_url: Sumsub API base URL (default: https://api.sumsub.com).
        level_name: Default KYC level (default: basic-kyc-level).
        webhook_secret: Secret for webhook signature verification.
    """

    def __init__(
        self,
        app_token: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: str = "https://api.sumsub.com",
        level_name: str = "basic-kyc-level",
        webhook_secret: Optional[str] = None,
        monthly_cap_usd: float = 500.0,
        max_retries: int = 3,
        timeout: int = 15,
    ):
        """
        Initialize Sumsub API client.

        Args:
            app_token: Sumsub app token. Defaults to SUMSUB_APP_TOKEN env var.
            secret_key: Sumsub secret key. Defaults to SUMSUB_SECRET_KEY env var.
            base_url: API base URL (default: https://api.sumsub.com).
            level_name: Default KYC level name (default: basic-kyc-level).
            webhook_secret: Secret for webhook verification. Defaults to SUMSUB_WEBHOOK_SECRET env var.
            monthly_cap_usd: Monthly API spending limit in USD (default: $500).
            max_retries: Maximum retry attempts for 5xx errors (default: 3).
            timeout: Request timeout in seconds (default: 15).
        """
        self.app_token = app_token or os.environ.get("SUMSUB_APP_TOKEN", "")
        self.secret_key = secret_key or os.environ.get("SUMSUB_SECRET_KEY", "")
        self.base_url = base_url or os.environ.get("SUMSUB_BASE_URL", "https://api.sumsub.com")
        self.level_name = level_name or get_sumsub_individual_level_name()
        self.webhook_secret = webhook_secret or os.environ.get("SUMSUB_WEBHOOK_SECRET", "")
        self.max_retries = max_retries
        self.timeout = timeout
        self.usage_tracker = APIUsageTracker(monthly_cap_usd=monthly_cap_usd)

        self.is_configured = bool(self.app_token and self.secret_key)

        # Block simulation mode in production — real KYC credentials are mandatory
        if not self.is_configured and is_production():
            raise RuntimeError(
                "CRITICAL: Sumsub credentials (SUMSUB_APP_TOKEN and SUMSUB_SECRET_KEY) "
                "are required in production. Simulated KYC is not permitted. "
                "Set SUMSUB_APP_TOKEN and SUMSUB_SECRET_KEY environment variables."
            )

        if self.is_configured:
            logger.info("Sumsub client initialized with live credentials")
        else:
            logger.warning(
                "Sumsub client initialized in SANDBOX/SIMULATED mode (no credentials). "
                "This is acceptable for development/testing only."
            )

    def _sign_request(self, method: str, url_path: str, body: bytes = b"") -> Dict[str, str]:
        """
        Create HMAC-SHA256 signature for Sumsub API request.

        Args:
            method: HTTP method (GET, POST, etc.).
            url_path: Request path (e.g., "/resources/applicants").
            body: Request body bytes (default: empty).

        Returns:
            Dictionary of required headers: X-App-Token, X-App-Access-Ts, X-App-Access-Sig.

        Raises:
            SumsubAuthError: If credentials are not configured.
        """
        if not self.is_configured:
            raise SumsubAuthError("Sumsub credentials not configured")

        ts = str(int(time.time()))

        # Build signature: timestamp + method + url_path + body
        sig_payload = (
            ts.encode("utf-8") +
            method.upper().encode("utf-8") +
            url_path.encode("utf-8") +
            (body if isinstance(body, bytes) else body.encode("utf-8"))
        )

        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            sig_payload,
            hashlib.sha256
        ).hexdigest()

        return {
            "X-App-Token": self.app_token,
            "X-App-Access-Ts": ts,
            "X-App-Access-Sig": signature,
        }

    def _request_with_retry(
        self,
        method: str,
        path: str,
        body: Optional[bytes] = None,
        files: Optional[Dict[str, Any]] = None,
        operation: str = "default",
    ) -> Tuple[int, Dict[str, Any], str]:
        """
        Execute HTTP request with retry logic for 5xx errors and exponential backoff.

        Args:
            method: HTTP method (GET, POST, etc.).
            path: Request path.
            body: Request body (for JSON requests).
            files: Files dict (for multipart requests).
            operation: Operation name for usage tracking.

        Returns:
            Tuple of (status_code, json_data, error_message).

        Raises:
            SumsubRetryError: If max retries exceeded.
            Timeout: If request times out.
        """
        headers = self._sign_request(method, path, body or b"")

        if body is not None:
            headers["Content-Type"] = "application/json"

        url = f"{self.base_url}{path}"

        for attempt in range(self.max_retries + 1):
            try:
                logger.debug(f"Sumsub {method} {path} (attempt {attempt + 1}/{self.max_retries + 1})")

                if method.upper() == "GET":
                    resp = requests.get(url, headers=headers, timeout=self.timeout)
                elif method.upper() == "POST":
                    if files:
                        resp = requests.post(url, headers=headers, files=files, timeout=self.timeout)
                    else:
                        resp = requests.post(url, headers=headers, data=body, timeout=self.timeout)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                # Success (2xx)
                if 200 <= resp.status_code < 300:
                    self.usage_tracker.add_call(operation)
                    try:
                        data = resp.json()
                    except json.JSONDecodeError:
                        data = {"raw": resp.text}
                    logger.debug(f"Sumsub {method} {path} returned {resp.status_code}")
                    return resp.status_code, data, ""

                # Client error (4xx) — don't retry
                if 400 <= resp.status_code < 500:
                    error_msg = resp.text[:300] if resp.text else f"HTTP {resp.status_code}"
                    logger.warning(f"Sumsub API client error {resp.status_code}: {error_msg}")
                    try:
                        data = resp.json()
                    except json.JSONDecodeError:
                        data = {"error": error_msg}
                    return resp.status_code, data, error_msg

                # Server error (5xx) — retry with backoff
                if resp.status_code >= 500:
                    if attempt < self.max_retries:
                        backoff = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                        logger.warning(
                            f"Sumsub API server error {resp.status_code}. "
                            f"Retrying in {backoff}s (attempt {attempt + 1}/{self.max_retries})"
                        )
                        time.sleep(backoff)
                        continue
                    else:
                        error_msg = resp.text[:300] if resp.text else f"HTTP {resp.status_code}"
                        logger.error(f"Sumsub API server error after {self.max_retries} retries: {error_msg}")
                        raise SumsubRetryError(
                            f"Sumsub API failed with {resp.status_code} after {self.max_retries} retries"
                        )

            except Timeout as e:
                logger.error(f"Sumsub request timeout: {e}")
                raise
            except RequestException as e:
                if attempt < self.max_retries:
                    backoff = 2 ** attempt
                    logger.warning(f"Sumsub request error: {e}. Retrying in {backoff}s")
                    time.sleep(backoff)
                    continue
                else:
                    logger.error(f"Sumsub request failed after {self.max_retries} retries: {e}")
                    raise SumsubRetryError(f"Sumsub API request failed: {str(e)}")

        raise SumsubRetryError("Max retries exceeded")

    def create_applicant(
        self,
        external_user_id: str,
        level_name: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        dob: Optional[str] = None,
        country: Optional[str] = None,
        info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new Sumsub applicant for KYC verification.

        Args:
            external_user_id: Unique identifier for the user in your system.
            level_name: KYC level name (default: client's default level).
            first_name: Applicant first name (optional).
            last_name: Applicant last name (optional).
            email: Applicant email (optional).
            phone: Applicant phone number (optional).
            dob: Applicant date of birth (optional).
            country: Applicant country code (optional).
            info: Additional applicant info dict (optional).

        Returns:
            Dict with keys: applicant_id, external_user_id, status, inspection_id, level_name,
                           created_at, source, api_status, and optionally error/note.
        """
        if not self.is_configured:
            logger.info(f"Sumsub not configured — simulating applicant creation for '{external_user_id}'")
            return self._simulate_applicant(external_user_id, first_name, last_name, info=info)

        try:
            level = level_name or self.level_name
            url_path = f"/resources/applicants?levelName={level}"

            body_data = {"externalUserId": external_user_id}

            # Add fixed info (name, DOB, country)
            fixed_info = {}
            if first_name:
                fixed_info["firstName"] = first_name
            if last_name:
                fixed_info["lastName"] = last_name
            if dob:
                fixed_info["dob"] = dob
            if country:
                fixed_info["country"] = country
            if fixed_info:
                body_data["fixedInfo"] = fixed_info

            if email:
                body_data["email"] = email
            if phone:
                body_data["phone"] = phone

            body_bytes = json.dumps(body_data).encode("utf-8")

            status, data, error_msg = self._request_with_retry(
                "POST", url_path, body=body_bytes, operation="create_applicant"
            )

            if status in (200, 201):
                applicant_id = data.get("id", "")
                logger.info(f"Sumsub: Created applicant {applicant_id} for user {external_user_id}")
                return {
                    "applicant_id": applicant_id,
                    "external_user_id": external_user_id,
                    "status": data.get("review", {}).get("reviewStatus", "init"),
                    "inspection_id": data.get("inspectionId", ""),
                    "level_name": level,
                    "created_at": data.get("createdAt", ""),
                    "source": "sumsub",
                    "api_status": "live",
                }
            elif status == 409:
                # Applicant already exists, retrieve it
                logger.info(f"Applicant already exists for {external_user_id}, retrieving existing")
                return self.get_applicant_by_external_id(external_user_id)
            else:
                logger.warning(f"Sumsub create applicant failed: {status} — {error_msg}")
                if self.is_configured:
                    return self._error_result("create_applicant", f"API returned {status}",
                                              external_user_id=external_user_id)
                return self._simulate_applicant(
                    external_user_id, first_name, last_name,
                    note=f"API returned {status}", info=info
                )

        except (SumsubRetryError, Timeout, RequestException) as e:
            logger.error(f"Sumsub create applicant error: {e}")
            if self.is_configured:
                return self._error_result("create_applicant", str(e)[:100],
                                          external_user_id=external_user_id)
            return self._simulate_applicant(
                external_user_id, first_name, last_name,
                note=f"Exception: {str(e)[:100]}", info=info
            )

    def get_applicant_by_external_id(self, external_user_id: str) -> Dict[str, Any]:
        """
        Retrieve an existing applicant by external user ID.

        Args:
            external_user_id: External user identifier.

        Returns:
            Dict with applicant details or simulated fallback.
        """
        if not self.is_configured:
            return self._simulate_applicant(external_user_id)

        try:
            url_path = f"/resources/applicants/-;externalUserId={external_user_id}/one"
            status, data, error_msg = self._request_with_retry(
                "GET", url_path, operation="get_applicant"
            )

            if status == 200:
                return {
                    "applicant_id": data.get("id", ""),
                    "external_user_id": external_user_id,
                    "status": data.get("review", {}).get("reviewStatus", "init"),
                    "review_answer": data.get("review", {}).get("reviewResult", {}).get("reviewAnswer", ""),
                    "inspection_id": data.get("inspectionId", ""),
                    "level_name": data.get("requiredIdDocs", {}).get("docSets", [{}])[0].get("idDocSetType", ""),
                    "source": "sumsub",
                    "api_status": "live",
                }
            else:
                logger.warning(f"Sumsub get applicant failed: {status}")
                if self.is_configured:
                    return self._error_result("get_applicant", f"Lookup returned {status}",
                                              external_user_id=external_user_id)
                return self._simulate_applicant(external_user_id, note=f"Lookup returned {status}")

        except (SumsubRetryError, Timeout, RequestException) as e:
            logger.error(f"Sumsub get applicant error: {e}")
            if self.is_configured:
                return self._error_result("get_applicant", str(e)[:100],
                                          external_user_id=external_user_id)
            return self._simulate_applicant(external_user_id, note=str(e)[:100])

    def get_applicant_status(self, applicant_id: str) -> Dict[str, Any]:
        """
        Get the verification status of an applicant.

        Args:
            applicant_id: Sumsub applicant ID.

        Returns:
            Dict with applicant_id, status, review_answer, rejection_labels, and verification_steps.
        """
        if not self.is_configured:
            return self._simulate_status(applicant_id)

        try:
            url_path = f"/resources/applicants/{applicant_id}/one"
            status, data, error_msg = self._request_with_retry(
                "GET", url_path, operation="get_applicant"
            )

            if status == 200:
                review = data.get("review", {})
                review_result = review.get("reviewResult", {})

                result = {
                    "applicant_id": applicant_id,
                    "external_user_id": data.get("externalUserId", ""),
                    "status": review.get("reviewStatus", "init"),
                    "review_answer": review_result.get("reviewAnswer", ""),
                    "rejection_labels": review_result.get("rejectLabels", []),
                    "moderation_comment": review_result.get("moderationComment", ""),
                    "created_at": data.get("createdAt", ""),
                    "source": "sumsub",
                    "api_status": "live",
                }

                # Fetch verification steps
                steps_url = f"/resources/applicants/{applicant_id}/requiredIdDocsStatus"
                steps_status, steps_data, _ = self._request_with_retry(
                    "GET", steps_url, operation="get_applicant"
                )
                if steps_status == 200:
                    result["verification_steps"] = steps_data

                return result
            else:
                logger.warning(f"Sumsub status check failed: {status}")
                if self.is_configured:
                    return self._error_result("get_applicant_status", f"API returned {status}",
                                              applicant_id=applicant_id)
                return self._simulate_status(applicant_id, note=f"API returned {status}")

        except (SumsubRetryError, Timeout, RequestException) as e:
            logger.error(f"Sumsub status error: {e}")
            if self.is_configured:
                return self._error_result("get_applicant_status", str(e)[:100],
                                          applicant_id=applicant_id)
            return self._simulate_status(applicant_id, note=str(e)[:100])

    def add_document(
        self,
        applicant_id: str,
        doc_type: str,
        file_data: bytes,
        filename: str = "document.pdf",
        country: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Upload an identity document for verification.

        Args:
            applicant_id: Sumsub applicant ID.
            doc_type: Document type (e.g., PASSPORT, ID_CARD, DRIVERS, SELFIE).
            file_data: Document file content (bytes).
            filename: Filename for the document.
            country: Country code for the document.

        Returns:
            Dict with status, doc_type, applicant_id, and source.
        """
        if not self.is_configured:
            return {
                "status": "simulated",
                "message": "Sumsub not configured",
                "source": "simulated",
            }

        try:
            url_path = f"/resources/applicants/{applicant_id}/info/idDoc"

            metadata = json.dumps({
                "idDocType": doc_type,
                "country": country,
            })

            # Multipart form data
            files = {
                "metadata": (None, metadata, "application/json"),
                "content": (filename, file_data, "application/octet-stream"),
            }

            status, data, error_msg = self._request_with_retry(
                "POST", url_path, files=files, operation="add_document"
            )

            if status in (200, 201):
                logger.info(f"Sumsub: Added {doc_type} document for applicant {applicant_id}")
                return {
                    "status": "uploaded",
                    "doc_type": doc_type,
                    "applicant_id": applicant_id,
                    "source": "sumsub",
                    "api_status": "live",
                }
            else:
                logger.warning(f"Sumsub doc upload failed: {status} — {error_msg}")
                return {
                    "status": "error",
                    "message": f"Upload failed: {status}",
                    "source": "sumsub",
                }

        except (SumsubRetryError, Timeout, RequestException) as e:
            logger.error(f"Sumsub doc upload error: {e}")
            return {
                "status": "error",
                "message": str(e)[:100],
                "source": "sumsub",
            }

    def get_verification_result(self, applicant_id: str) -> Dict[str, Any]:
        """
        Get detailed verification checks and results.

        Args:
            applicant_id: Sumsub applicant ID.

        Returns:
            Dict with verification results or simulated fallback.
        """
        if not self.is_configured:
            return self._simulate_verification_result(applicant_id)

        try:
            url_path = f"/resources/applicants/{applicant_id}/verification/result"
            status, data, error_msg = self._request_with_retry(
                "GET", url_path, operation="get_verification_result"
            )

            if status == 200:
                return {
                    "applicant_id": applicant_id,
                    "verification_result": data,
                    "source": "sumsub",
                    "api_status": "live",
                }
            else:
                logger.warning(f"Sumsub get verification result failed: {status}")
                if self.is_configured:
                    return self._error_result("get_verification_result", f"API returned {status}",
                                              applicant_id=applicant_id)
                return self._simulate_verification_result(applicant_id, note=f"API returned {status}")

        except (SumsubRetryError, Timeout, RequestException) as e:
            logger.error(f"Sumsub get verification result error: {e}")
            if self.is_configured:
                return self._error_result("get_verification_result", str(e)[:100],
                                          applicant_id=applicant_id)
            return self._simulate_verification_result(applicant_id, note=str(e)[:100])

    def get_aml_screening(self, applicant_id: str) -> Dict[str, Any]:
        """
        Get AML/PEP screening results for an applicant.

        Args:
            applicant_id: Sumsub applicant ID.

        Returns:
            Dict with AML screening results or simulated fallback.
        """
        if not self.is_configured:
            return self._simulate_aml_screening(applicant_id)

        try:
            url_path = f"/resources/applicants/{applicant_id}/checkSteps"
            status, data, error_msg = self._request_with_retry(
                "GET", url_path, operation="get_aml_screening"
            )

            if status == 200:
                # Extract AML check results
                aml_checks = [step for step in data if step.get("checkType") == "AML"]
                return {
                    "applicant_id": applicant_id,
                    "aml_checks": aml_checks,
                    "source": "sumsub",
                    "api_status": "live",
                }
            else:
                logger.warning(f"Sumsub get AML screening failed: {status}")
                if self.is_configured:
                    return self._error_result("get_aml_screening", f"API returned {status}",
                                              applicant_id=applicant_id)
                return self._simulate_aml_screening(applicant_id, note=f"API returned {status}")

        except (SumsubRetryError, Timeout, RequestException) as e:
            logger.error(f"Sumsub get AML screening error: {e}")
            if self.is_configured:
                return self._error_result("get_aml_screening", str(e)[:100],
                                          applicant_id=applicant_id)
            return self._simulate_aml_screening(applicant_id, note=str(e)[:100])

    def generate_access_token(
        self,
        external_user_id: str,
        level_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate an access token for the Sumsub WebSDK.

        Args:
            external_user_id: External user identifier.
            level_name: KYC level name (default: client's default level).

        Returns:
            Dict with token, user_id, level_name, source, and api_status.
        """
        if not self.is_configured:
            logger.info(f"Sumsub not configured — simulating access token for '{external_user_id}'")
            return self._simulate_token(external_user_id)

        try:
            level = level_name or self.level_name
            url_path = f"/resources/accessTokens?userId={external_user_id}&levelName={level}"

            status, data, error_msg = self._request_with_retry(
                "POST", url_path, operation="generate_access_token"
            )

            if status == 200:
                logger.info(f"Sumsub: Generated access token for user {external_user_id}")
                return {
                    "token": data.get("token", ""),
                    "user_id": external_user_id,
                    "level_name": level,
                    "source": "sumsub",
                    "api_status": "live",
                }
            else:
                logger.warning(f"Sumsub token gen failed: {status} — {error_msg}")
                if self.is_configured:
                    return self._error_result("generate_access_token", f"API returned {status}",
                                              external_user_id=external_user_id, token="")
                return self._simulate_token(external_user_id, note=f"API returned {status}")

        except (SumsubRetryError, Timeout, RequestException) as e:
            logger.error(f"Sumsub token error: {e}")
            if self.is_configured:
                return self._error_result("generate_access_token", str(e)[:100],
                                          external_user_id=external_user_id, token="")
            return self._simulate_token(external_user_id, note=str(e)[:100])

    # ── Webhook Signature Verification ──

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature_header: str,
    ) -> bool:
        """
        Verify a Sumsub webhook signature (HMAC-SHA256).

        Args:
            payload: Raw webhook body bytes.
            signature_header: X-App-Access-Sig header value from webhook.

        Returns:
            True if signature is valid, False otherwise.
        """
        if not self.webhook_secret:
            logger.error(
                "SECURITY: Sumsub webhook secret not configured — rejecting webhook. "
                "Set SUMSUB_WEBHOOK_SECRET environment variable."
            )
            return False

        expected = hmac.new(
            self.webhook_secret.encode("utf-8"),
            payload,
            hashlib.sha256
        ).hexdigest()

        is_valid = hmac.compare_digest(expected, signature_header or "")

        if not is_valid:
            logger.warning("Invalid Sumsub webhook signature")

        return is_valid

    # ── Safe Error Responses (returned when live API fails with credentials present) ──

    @staticmethod
    def _error_result(operation: str, reason: str, **extra) -> Dict[str, Any]:
        """
        Return a clearly-errored result instead of simulated data when live API fails.
        This prevents fabricated KYC data from being stored as real.
        """
        result = {
            "applicant_id": "",
            "status": "error",
            "review_answer": "",
            "source": "sumsub",
            "api_status": "error",
            "error": f"{operation} failed: {reason}",
            "note": reason,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        }
        result.update(extra)
        return result

    # ── Simulation Fallbacks (for when Sumsub is not configured) ──

    @staticmethod
    def _simulate_applicant(
        external_user_id: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        note: str = "No Sumsub credentials configured",
        info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Simulated applicant creation."""
        sim_id = f"sim_{hashlib.md5(external_user_id.encode()).hexdigest()[:16]}"
        return {
            "applicant_id": sim_id,
            "external_user_id": external_user_id,
            "status": "init",
            "inspection_id": f"insp_{sim_id[:12]}",
            "level_name": "basic-kyc-level",
            "source": "simulated",
            "api_status": "simulated",
            "note": note,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        }

    @staticmethod
    def _simulate_token(
        external_user_id: str,
        note: str = "No Sumsub credentials configured",
    ) -> Dict[str, Any]:
        """Simulated access token."""
        token = base64.b64encode(
            f"sim_token_{external_user_id}_{int(time.time())}".encode()
        ).decode()
        return {
            "token": token,
            "user_id": external_user_id,
            "level_name": "basic-kyc-level",
            "source": "simulated",
            "api_status": "simulated",
            "note": note,
        }

    @staticmethod
    def _simulate_status(
        applicant_id: str,
        note: str = "No Sumsub credentials configured",
    ) -> Dict[str, Any]:
        """Simulated verification status."""
        import random
        statuses = ["init", "pending", "completed"]
        answers = ["", "", "GREEN"]
        idx = random.randint(0, 2)
        return {
            "applicant_id": applicant_id,
            "external_user_id": "",
            "status": statuses[idx],
            "review_answer": answers[idx],
            "rejection_labels": [],
            "moderation_comment": "",
            "verification_steps": {
                "IDENTITY": {"reviewResult": {"reviewAnswer": answers[idx]} if idx == 2 else {}},
                "SELFIE": {"reviewResult": {"reviewAnswer": answers[idx]} if idx == 2 else {}},
            },
            "source": "simulated",
            "api_status": "simulated",
            "note": note,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        }

    @staticmethod
    def _simulate_verification_result(
        applicant_id: str,
        note: str = "No Sumsub credentials configured",
    ) -> Dict[str, Any]:
        """Simulated verification result."""
        return {
            "applicant_id": applicant_id,
            "verification_result": {
                "checks": [],
                "result": "INCONCLUSIVE",
            },
            "source": "simulated",
            "api_status": "simulated",
            "note": note,
        }

    @staticmethod
    def _simulate_aml_screening(
        applicant_id: str,
        note: str = "No Sumsub credentials configured",
    ) -> Dict[str, Any]:
        """Simulated AML screening result."""
        return {
            "applicant_id": applicant_id,
            "aml_checks": [],
            "source": "simulated",
            "api_status": "simulated",
            "note": note,
        }

    # ── Usage Tracking ──

    def get_usage_stats(self) -> Dict[str, Any]:
        """Get current API usage statistics."""
        return self.usage_tracker.get_usage()


# ── Singleton instance management ──

_sumsub_client_instance: Optional[SumsubClient] = None


def get_sumsub_client(
    app_token: Optional[str] = None,
    secret_key: Optional[str] = None,
    base_url: Optional[str] = None,
    level_name: Optional[str] = None,
    webhook_secret: Optional[str] = None,
    monthly_cap_usd: float = 500.0,
    max_retries: int = 3,
    timeout: int = 15,
) -> SumsubClient:
    """
    Get or create a cached Sumsub client instance (singleton pattern).

    Args:
        app_token: Sumsub app token (env: SUMSUB_APP_TOKEN).
        secret_key: Sumsub secret key (env: SUMSUB_SECRET_KEY).
        base_url: API base URL (env: SUMSUB_BASE_URL, default: https://api.sumsub.com).
        level_name: Default KYC level (env: SUMSUB_INDIVIDUAL_LEVEL_NAME / SUMSUB_LEVEL_NAME, default: id-and-liveness).
        webhook_secret: Webhook secret (env: SUMSUB_WEBHOOK_SECRET).
        monthly_cap_usd: Monthly API spending cap in USD (default: $500).
        max_retries: Max retry attempts for 5xx (default: 3).
        timeout: Request timeout in seconds (default: 15).

    Returns:
        Cached SumsubClient instance.
    """
    global _sumsub_client_instance

    if _sumsub_client_instance is None:
        _sumsub_client_instance = SumsubClient(
            app_token=app_token,
            secret_key=secret_key,
            base_url=base_url or os.environ.get("SUMSUB_BASE_URL", "https://api.sumsub.com"),
            level_name=level_name or get_sumsub_individual_level_name(),
            webhook_secret=webhook_secret,
            monthly_cap_usd=monthly_cap_usd,
            max_retries=max_retries,
            timeout=timeout,
        )

    return _sumsub_client_instance


def reset_sumsub_client() -> None:
    """Reset the cached client instance (useful for testing)."""
    global _sumsub_client_instance
    _sumsub_client_instance = None
