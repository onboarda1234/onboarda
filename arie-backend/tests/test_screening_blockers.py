"""
Targeted tests for S-01, S-02, S-03 screening blocker fixes.

S-01: SumsubWebhookHandler must return clean 401/400 on invalid signature/JSON
S-02: screen_sumsub_aml() must not silently fall back to simulation when configured
S-03: SumsubClient.get_aml_screening() must return _error_result() when configured and API fails
"""
import os
import sys
import json
import hmac
import hashlib
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ═══════════════════════════════════════════════════════════════
# S-01: WEBHOOK HANDLER BASE CLASS FIX
# ═══════════════════════════════════════════════════════════════

class TestS01WebhookHandlerBaseClass:
    """S-01: SumsubWebhookHandler must extend BaseHandler, not tornado.web.RequestHandler."""

    def test_webhook_handler_inherits_basehandler(self):
        """SumsubWebhookHandler must inherit from BaseHandler."""
        from server import SumsubWebhookHandler
        from base_handler import BaseHandler
        assert issubclass(SumsubWebhookHandler, BaseHandler), \
            "SumsubWebhookHandler must extend BaseHandler, not tornado.web.RequestHandler directly"

    def test_webhook_handler_has_error_method(self):
        """SumsubWebhookHandler must have access to BaseHandler.error()."""
        from server import SumsubWebhookHandler
        assert hasattr(SumsubWebhookHandler, 'error'), \
            "SumsubWebhookHandler must have error() method via BaseHandler"

    def test_webhook_handler_error_method_is_basehandler(self):
        """The error() method must be from BaseHandler, not a stub."""
        from server import SumsubWebhookHandler
        from base_handler import BaseHandler
        assert SumsubWebhookHandler.error is BaseHandler.error, \
            "error() must be the BaseHandler.error() method"

    def test_webhook_handler_has_no_standalone_set_default_headers(self):
        """SumsubWebhookHandler should rely on BaseHandler.set_default_headers, not its own."""
        from server import SumsubWebhookHandler
        from base_handler import BaseHandler
        # The method should come from BaseHandler, not be overridden in SumsubWebhookHandler
        assert SumsubWebhookHandler.set_default_headers is BaseHandler.set_default_headers, \
            "SumsubWebhookHandler should not override set_default_headers"


# ═══════════════════════════════════════════════════════════════
# S-03: SUMSUB CLIENT get_aml_screening() HARDENING
# ═══════════════════════════════════════════════════════════════

class TestS03AmlScreeningHardened:
    """S-03: get_aml_screening() must use _error_result() when configured and API fails."""

    def _make_configured_client(self):
        """Create a configured SumsubClient (is_configured=True) without hitting real API."""
        from sumsub_client import SumsubClient
        client = SumsubClient.__new__(SumsubClient)
        client.app_token = "test_token"
        client.secret_key = "test_secret"
        client.base_url = "https://api.sumsub.com"
        client.level_name = "basic-kyc-level"
        client.webhook_secret = ""
        client.timeout = 15
        client.max_retries = 1
        client.is_configured = True
        client.usage_tracker = MagicMock()
        return client

    def _make_unconfigured_client(self):
        """Create an unconfigured SumsubClient (is_configured=False)."""
        from sumsub_client import SumsubClient
        client = SumsubClient.__new__(SumsubClient)
        client.app_token = ""
        client.secret_key = ""
        client.base_url = "https://api.sumsub.com"
        client.level_name = "basic-kyc-level"
        client.webhook_secret = ""
        client.timeout = 15
        client.max_retries = 1
        client.is_configured = False
        client.usage_tracker = MagicMock()
        return client

    def test_configured_client_api_error_returns_error_result(self):
        """When configured and API returns non-200, must return _error_result(), not simulation."""
        client = self._make_configured_client()

        with patch.object(client, '_request_with_retry', return_value=(500, {}, "Server Error")):
            result = client.get_aml_screening("test-applicant-id")

        assert result["api_status"] == "error", \
            "Configured client API failure must return api_status='error'"
        assert result["source"] == "sumsub", \
            "Source must be 'sumsub', not 'simulated'"
        assert "error" in result, \
            "Result must contain an 'error' field"
        assert "get_aml_screening" in result["error"], \
            "Error must identify the operation"

    def test_configured_client_exception_returns_error_result(self):
        """When configured and request throws exception, must return _error_result()."""
        from requests.exceptions import Timeout
        client = self._make_configured_client()

        with patch.object(client, '_request_with_retry', side_effect=Timeout("Connection timed out")):
            result = client.get_aml_screening("test-applicant-id")

        assert result["api_status"] == "error"
        assert result["source"] == "sumsub"
        assert "error" in result

    def test_unconfigured_client_still_simulates(self):
        """When NOT configured, simulation fallback should still work."""
        client = self._make_unconfigured_client()

        result = client.get_aml_screening("test-applicant-id")

        assert result["source"] == "simulated"
        assert result["api_status"] == "simulated"

    def test_configured_client_success_returns_live(self):
        """When configured and API returns 200, must return live result."""
        client = self._make_configured_client()

        mock_data = [
            {"checkType": "AML", "data": {"matches": []}},
            {"checkType": "OTHER", "data": {}},
        ]
        with patch.object(client, '_request_with_retry', return_value=(200, mock_data, "")):
            result = client.get_aml_screening("test-applicant-id")

        assert result["api_status"] == "live"
        assert result["source"] == "sumsub"
        assert len(result["aml_checks"]) == 1  # Only AML checks

    def test_aml_error_result_consistent_with_other_methods(self):
        """get_aml_screening error must have same shape as create_applicant error."""
        client = self._make_configured_client()

        with patch.object(client, '_request_with_retry', return_value=(500, {}, "Server Error")):
            aml_result = client.get_aml_screening("test-id")

        # Check it has the same required keys as _error_result
        required_keys = {"applicant_id", "status", "source", "api_status", "error", "note"}
        missing = required_keys - set(aml_result.keys())
        assert not missing, f"Missing keys from _error_result(): {missing}"


# ═══════════════════════════════════════════════════════════════
# S-02: SCREENING.PY AML SIMULATION FALLBACK FIX
# ═══════════════════════════════════════════════════════════════

class TestS02ScreenSumsubAmlHardened:
    """S-02: screen_sumsub_aml() must not silently simulate when Sumsub is configured and fails."""

    def test_error_applicant_creation_propagates_error(self):
        """When applicant creation returns error (configured), must propagate, not simulate."""
        error_result = {
            "applicant_id": "",
            "status": "error",
            "source": "sumsub",
            "api_status": "error",
            "error": "create_applicant failed: API returned 500",
            "note": "API returned 500",
        }

        with patch("screening.sumsub_create_applicant", return_value=error_result):
            from screening import screen_sumsub_aml
            result = screen_sumsub_aml("Test Person")

        assert result["api_status"] == "error", \
            "When applicant creation fails with error status, screen_sumsub_aml must propagate error"
        assert result["source"] == "sumsub", \
            "Source must remain 'sumsub' (not 'simulated') when configured API fails"
        assert "error" in result

    def test_error_aml_screening_propagates_error(self):
        """When get_applicant_review_status returns error (configured), must propagate, not simulate."""
        good_applicant = {
            "applicant_id": "test-abc-123",
            "status": "init",
            "source": "sumsub",
            "api_status": "live",
        }

        mock_client = MagicMock()
        mock_client.request_check.return_value = {
            "ok": True, "source": "sumsub", "api_status": "live",
        }
        mock_client.get_applicant_review_status.return_value = {
            "applicant_id": "test-abc-123",
            "status": "error",
            "source": "sumsub",
            "api_status": "error",
            "error": "get_applicant_review_status failed: API returned 500",
        }
        mock_client.is_configured = True

        with patch("screening.sumsub_create_applicant", return_value=good_applicant), \
             patch("screening.get_sumsub_client", return_value=mock_client), \
             patch("sumsub_client.get_sumsub_client", return_value=mock_client), \
             patch("sumsub_client._sumsub_client_instance", mock_client):
            from screening import screen_sumsub_aml
            result = screen_sumsub_aml("Test Person")

        assert result["api_status"] == "error", \
            "When AML screening returns error, must propagate error"
        assert result["source"] == "sumsub"
        assert result.get("matched") is False

    def test_exception_with_configured_client_returns_error(self):
        """When exception occurs and Sumsub is configured, must return error, not simulation."""
        mock_client = MagicMock()
        mock_client.is_configured = True

        with patch("screening.sumsub_create_applicant", side_effect=RuntimeError("Connection failed")), \
             patch("sumsub_client.get_sumsub_client", return_value=mock_client), \
             patch("sumsub_client._sumsub_client_instance", mock_client):
            from screening import screen_sumsub_aml
            result = screen_sumsub_aml("Test Person")

        assert result["api_status"] == "error", \
            "When exception occurs with configured client, must return error"
        assert result["source"] == "sumsub"
        assert "error" in result

    def test_exception_without_configured_client_falls_back_to_simulation(self):
        """When exception occurs and Sumsub is NOT configured, simulation is still allowed."""
        mock_client = MagicMock()
        mock_client.is_configured = False

        with patch("screening.sumsub_create_applicant", side_effect=RuntimeError("Connection failed")), \
             patch("sumsub_client.get_sumsub_client", return_value=mock_client), \
             patch("sumsub_client._sumsub_client_instance", mock_client):
            from screening import screen_sumsub_aml
            result = screen_sumsub_aml("Test Person")

        assert result["source"] == "simulated", \
            "When Sumsub is not configured, simulation fallback is still allowed"

    def test_successful_aml_screening_still_works(self):
        """Happy path: successful AML screening (RED) returns matched=True."""
        good_applicant = {
            "applicant_id": "test-abc-123",
            "status": "init",
            "source": "sumsub",
            "api_status": "live",
        }

        mock_client = MagicMock()
        mock_client.request_check.return_value = {
            "ok": True, "source": "sumsub", "api_status": "live",
        }
        mock_client.get_applicant_review_status.return_value = {
            "applicant_id": "test-abc-123",
            "review_status": "completed",
            "review_answer": "RED",
            "source": "sumsub",
            "api_status": "live",
        }
        mock_client.is_configured = True

        with patch("screening.sumsub_create_applicant", return_value=good_applicant), \
             patch("screening.get_sumsub_client", return_value=mock_client), \
             patch("sumsub_client.get_sumsub_client", return_value=mock_client), \
             patch("sumsub_client._sumsub_client_instance", mock_client):
            from screening import screen_sumsub_aml
            result = screen_sumsub_aml("Test Person")

        assert result["matched"] is True
        assert result["source"] == "sumsub"
        assert result["api_status"] == "live"
        assert len(result["results"]) >= 1

    def test_simulated_aml_result_triggers_simulation(self):
        """When review result is simulated (not configured), return simulation."""
        good_applicant = {
            "applicant_id": "test-abc-123",
            "status": "init",
            "source": "sumsub",
            "api_status": "live",
        }

        mock_client = MagicMock()
        mock_client.request_check.return_value = {
            "ok": True, "source": "simulated", "api_status": "simulated",
        }
        mock_client.get_applicant_review_status.return_value = {
            "applicant_id": "test-abc-123",
            "review_status": "completed",
            "review_answer": "GREEN",
            "source": "simulated",
            "api_status": "simulated",
        }
        mock_client.is_configured = False

        with patch("screening.sumsub_create_applicant", return_value=good_applicant), \
             patch("screening.get_sumsub_client", return_value=mock_client), \
             patch("sumsub_client.get_sumsub_client", return_value=mock_client), \
             patch("sumsub_client._sumsub_client_instance", mock_client):
            from screening import screen_sumsub_aml
            result = screen_sumsub_aml("Test Person")

        assert result["source"] == "simulated"

    def test_error_result_has_required_fields(self):
        """Error results must have all required fields for downstream consumers."""
        error_result = {
            "applicant_id": "",
            "status": "error",
            "source": "sumsub",
            "api_status": "error",
            "error": "create_applicant failed: API returned 500",
            "note": "API returned 500",
        }

        with patch("screening.sumsub_create_applicant", return_value=error_result):
            from screening import screen_sumsub_aml
            result = screen_sumsub_aml("Test Person")

        # All results from screen_sumsub_aml must have these fields
        assert "matched" in result
        assert "results" in result
        assert "source" in result
        assert "api_status" in result
        assert "screened_at" in result
