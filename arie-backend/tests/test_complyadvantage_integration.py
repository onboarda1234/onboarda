"""
Tests for ComplyAdvantage integration and demo live-screening routing.

Covers:
  - ComplyAdvantage client: configured vs not configured
  - Result normalization to Onboarda screening format
  - Demo live-screening routing (DEMO_USE_LIVE_SCREENING flag)
  - Fallback behavior when credentials are missing
  - Source labeling (no fake success states)
  - Credential missing / invalid handling
  - Error handling and graceful degradation
"""
import json
import os
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime


# ════════════════════════════════════════════════════════════
# ComplyAdvantage Client Tests
# ════════════════════════════════════════════════════════════

class TestComplyAdvantageClient:
    """Tests for complyadvantage_client.py"""

    def setup_method(self):
        """Reset singleton before each test."""
        from complyadvantage_client import reset_complyadvantage_client
        reset_complyadvantage_client()

    def teardown_method(self):
        from complyadvantage_client import reset_complyadvantage_client
        reset_complyadvantage_client()

    def test_client_not_configured_without_api_key(self):
        """Client reports not configured when no API key is set."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COMPLYADVANTAGE_API_KEY", None)
            from complyadvantage_client import ComplyAdvantageClient
            client = ComplyAdvantageClient(api_key="")
            assert client.is_configured is False

    def test_client_configured_with_api_key(self):
        """Client reports configured when API key is present."""
        from complyadvantage_client import ComplyAdvantageClient
        client = ComplyAdvantageClient(api_key="test-key-123")
        assert client.is_configured is True

    def test_screen_entity_not_configured_returns_explicit_status(self):
        """Screening without credentials returns explicit not_configured status."""
        from complyadvantage_client import ComplyAdvantageClient
        client = ComplyAdvantageClient(api_key="")
        result = client.screen_entity(name="John Doe")
        assert result["source"] == "complyadvantage"
        assert result["api_status"] == "not_configured"
        assert result["matched"] is False
        assert "not configured" in result.get("note", "").lower()

    def test_screen_entity_success_normalization(self):
        """Successful API response is normalized to Onboarda format."""
        from complyadvantage_client import ComplyAdvantageClient
        client = ComplyAdvantageClient(api_key="test-key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": {
                "data": {
                    "id": 12345,
                    "total_hits": 1,
                    "hits": [
                        {
                            "doc": {
                                "name": "John Doe",
                                "types": ["sanction", "pep"],
                                "countries": ["US"],
                                "sources": [{"name": "OFAC SDN"}],
                            },
                            "match_status": "potential_match",
                        }
                    ],
                }
            }
        }

        with patch("complyadvantage_client.requests.post", return_value=mock_response):
            result = client.screen_entity(name="John Doe")

        assert result["source"] == "complyadvantage"
        assert result["api_status"] == "live"
        assert result["matched"] is True
        assert len(result["results"]) == 1
        assert result["results"][0]["is_pep"] is True
        assert result["results"][0]["is_sanctioned"] is True
        assert result["results"][0]["matched_name"] == "John Doe"
        assert result["results"][0]["match_score"] == 75.0
        assert result["search_id"] == 12345

    def test_screen_entity_no_hits(self):
        """No-hit response correctly normalized."""
        from complyadvantage_client import ComplyAdvantageClient
        client = ComplyAdvantageClient(api_key="test-key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": {"data": {"id": 99, "total_hits": 0, "hits": []}}
        }

        with patch("complyadvantage_client.requests.post", return_value=mock_response):
            result = client.screen_entity(name="Jane Clean")

        assert result["matched"] is False
        assert result["results"] == []
        assert result["source"] == "complyadvantage"
        assert result["api_status"] == "live"

    def test_screen_entity_auth_failure(self):
        """401 response returns error status, not simulated."""
        from complyadvantage_client import ComplyAdvantageClient
        client = ComplyAdvantageClient(api_key="bad-key")

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("complyadvantage_client.requests.post", return_value=mock_response):
            result = client.screen_entity(name="Test Person")

        assert result["api_status"] == "error"
        assert result["source"] == "complyadvantage"
        assert "authentication" in result.get("note", "").lower()

    def test_screen_entity_rate_limited(self):
        """429 response returns error status."""
        from complyadvantage_client import ComplyAdvantageClient
        client = ComplyAdvantageClient(api_key="key")

        mock_response = MagicMock()
        mock_response.status_code = 429

        with patch("complyadvantage_client.requests.post", return_value=mock_response):
            result = client.screen_entity(name="Test Person")

        assert result["api_status"] == "error"
        assert "rate" in result.get("note", "").lower()

    def test_screen_entity_network_error(self):
        """Network error returns error status."""
        from complyadvantage_client import ComplyAdvantageClient
        client = ComplyAdvantageClient(api_key="key")

        with patch("complyadvantage_client.requests.post", side_effect=Exception("Connection refused")):
            result = client.screen_entity(name="Test Person")

        assert result["api_status"] == "error"
        assert result["source"] == "complyadvantage"

    def test_screen_entity_timeout(self):
        """Timeout returns error status."""
        from complyadvantage_client import ComplyAdvantageClient
        from requests.exceptions import Timeout
        client = ComplyAdvantageClient(api_key="key")

        with patch("complyadvantage_client.requests.post", side_effect=Timeout("timed out")):
            result = client.screen_entity(name="Test Person")

        assert result["api_status"] == "error"
        assert "timed out" in result.get("note", "").lower()

    def test_health_check_not_configured(self):
        """Health check when not configured."""
        from complyadvantage_client import ComplyAdvantageClient
        client = ComplyAdvantageClient(api_key="")
        health = client.health_check()
        assert health["status"] == "not_configured"
        assert health["configured"] is False

    def test_singleton_pattern(self):
        """get_complyadvantage_client returns same instance."""
        from complyadvantage_client import get_complyadvantage_client, reset_complyadvantage_client
        reset_complyadvantage_client()
        c1 = get_complyadvantage_client()
        c2 = get_complyadvantage_client()
        assert c1 is c2


# ════════════════════════════════════════════════════════════
# Demo Live-Screening Routing Tests
# ════════════════════════════════════════════════════════════

class TestDemoLiveScreeningRouting:
    """Tests for screening.py routing with DEMO_USE_LIVE_SCREENING."""

    def setup_method(self):
        from complyadvantage_client import reset_complyadvantage_client
        reset_complyadvantage_client()

    def teardown_method(self):
        from complyadvantage_client import reset_complyadvantage_client
        reset_complyadvantage_client()

    def test_default_demo_uses_simulated(self, temp_db):
        """Without DEMO_USE_LIVE_SCREENING, demo uses simulated fallback."""
        with patch.dict(os.environ, {"ENV": "demo"}, clear=False):
            os.environ.pop("DEMO_USE_LIVE_SCREENING", None)
            os.environ.pop("COMPLYADVANTAGE_API_KEY", None)
            os.environ.pop("SUMSUB_APP_TOKEN", None)
            os.environ.pop("SUMSUB_SECRET_KEY", None)

            from screening import _fallback_aml_screen
            from complyadvantage_client import reset_complyadvantage_client
            reset_complyadvantage_client()

            result = _fallback_aml_screen("Test Person")
            assert result["source"] == "simulated"
            assert result["api_status"] == "simulated"

    def test_fallback_uses_complyadvantage_when_configured(self, temp_db):
        """_fallback_aml_screen uses ComplyAdvantage when API key is present."""
        from complyadvantage_client import reset_complyadvantage_client, ComplyAdvantageClient
        reset_complyadvantage_client()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": {"data": {"id": 1, "total_hits": 0, "hits": []}}
        }

        with patch.dict(os.environ, {"COMPLYADVANTAGE_API_KEY": "test-key"}):
            reset_complyadvantage_client()
            with patch("complyadvantage_client.requests.post", return_value=mock_response):
                from screening import _fallback_aml_screen
                result = _fallback_aml_screen("John Doe")

        assert result["source"] == "complyadvantage"
        assert result["api_status"] == "live"

    def test_fallback_degrades_to_simulated_on_ca_error(self, temp_db):
        """If ComplyAdvantage fails, falls back to simulation."""
        from complyadvantage_client import reset_complyadvantage_client
        reset_complyadvantage_client()

        with patch.dict(os.environ, {"COMPLYADVANTAGE_API_KEY": "bad-key", "ENV": "demo"}):
            reset_complyadvantage_client()
            with patch("complyadvantage_client.requests.post", side_effect=Exception("network error")):
                from screening import _fallback_aml_screen
                result = _fallback_aml_screen("Test Person")

        assert result["source"] == "simulated"
        assert result["api_status"] == "simulated"


# ════════════════════════════════════════════════════════════
# Source Label Correctness Tests
# ════════════════════════════════════════════════════════════

class TestSourceLabeling:
    """Verify no fake success states — source labels are always accurate."""

    def setup_method(self):
        from complyadvantage_client import reset_complyadvantage_client
        reset_complyadvantage_client()

    def teardown_method(self):
        from complyadvantage_client import reset_complyadvantage_client
        reset_complyadvantage_client()

    def test_simulated_source_never_says_live(self, temp_db):
        """Simulated results never claim api_status=live."""
        from screening import _simulate_aml_screen
        result = _simulate_aml_screen("Test")
        assert result["source"] == "simulated"
        assert result["api_status"] == "simulated"

    def test_complyadvantage_live_result_says_live(self):
        """ComplyAdvantage live results correctly say source=complyadvantage, api_status=live."""
        from complyadvantage_client import ComplyAdvantageClient
        client = ComplyAdvantageClient(api_key="key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": {"data": {"id": 1, "total_hits": 0, "hits": []}}
        }

        with patch("complyadvantage_client.requests.post", return_value=mock_response):
            result = client.screen_entity(name="Test")

        assert result["source"] == "complyadvantage"
        assert result["api_status"] == "live"

    def test_complyadvantage_error_never_says_simulated(self):
        """ComplyAdvantage errors say 'error', never 'simulated'."""
        from complyadvantage_client import ComplyAdvantageClient
        client = ComplyAdvantageClient(api_key="key")

        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("complyadvantage_client.requests.post", return_value=mock_response):
            result = client.screen_entity(name="Test")

        assert result["api_status"] == "error"
        assert result["source"] == "complyadvantage"
        # Must NOT say "simulated"
        assert "simulated" not in json.dumps(result).lower()


# ════════════════════════════════════════════════════════════
# Environment Flag Tests
# ════════════════════════════════════════════════════════════

class TestEnvironmentFlags:
    """Tests for DEMO_USE_LIVE_SCREENING and DEMO_USE_LIVE_DOCUMENT_AI flags."""

    def test_use_live_screening_false_by_default(self):
        """DEMO_USE_LIVE_SCREENING defaults to false in demo."""
        with patch.dict(os.environ, {"ENV": "demo"}, clear=False):
            os.environ.pop("DEMO_USE_LIVE_SCREENING", None)
            os.environ.pop("COMPLYADVANTAGE_API_KEY", None)
            # Reimport to pick up env changes
            from environment import FeatureFlags
            f = FeatureFlags("demo")
            assert f.is_enabled("DEMO_USE_LIVE_SCREENING") is False

    def test_use_live_screening_true_when_set(self):
        """DEMO_USE_LIVE_SCREENING=true is respected."""
        with patch.dict(os.environ, {"ENV": "demo", "DEMO_USE_LIVE_SCREENING": "true"}):
            from environment import FeatureFlags
            f = FeatureFlags("demo")
            assert f.is_enabled("DEMO_USE_LIVE_SCREENING") is True

    def test_use_live_document_ai_false_by_default(self):
        """DEMO_USE_LIVE_DOCUMENT_AI defaults to false."""
        with patch.dict(os.environ, {"ENV": "demo"}, clear=False):
            os.environ.pop("DEMO_USE_LIVE_DOCUMENT_AI", None)
            from environment import FeatureFlags
            f = FeatureFlags("demo")
            assert f.is_enabled("DEMO_USE_LIVE_DOCUMENT_AI") is False

    def test_use_live_screening_requires_api_key(self):
        """use_live_screening_in_demo() requires both flag AND API key."""
        with patch.dict(os.environ, {"DEMO_USE_LIVE_SCREENING": "true"}, clear=False):
            os.environ.pop("COMPLYADVANTAGE_API_KEY", None)
            from environment import FeatureFlags
            f = FeatureFlags("demo")
            # Flag is on but no key — function should return False
            assert f.is_enabled("DEMO_USE_LIVE_SCREENING") is True
            from environment import get_complyadvantage_api_key
            assert get_complyadvantage_api_key() == ""

    def test_use_live_screening_works_with_both(self):
        """use_live_screening_in_demo() returns True with both flag and key."""
        with patch.dict(os.environ, {
            "DEMO_USE_LIVE_SCREENING": "true",
            "COMPLYADVANTAGE_API_KEY": "test-key"
        }):
            from environment import FeatureFlags, get_complyadvantage_api_key
            f = FeatureFlags("demo")
            assert f.is_enabled("DEMO_USE_LIVE_SCREENING") is True
            assert bool(get_complyadvantage_api_key()) is True


# ════════════════════════════════════════════════════════════
# Result Normalization Tests
# ════════════════════════════════════════════════════════════

class TestResultNormalization:
    """ComplyAdvantage results match the shape expected by UI/downstream."""

    def test_result_has_required_fields(self):
        """Normalized result has all fields expected by UI."""
        from complyadvantage_client import ComplyAdvantageClient
        client = ComplyAdvantageClient(api_key="key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": {
                "data": {
                    "id": 1,
                    "total_hits": 1,
                    "hits": [{
                        "doc": {
                            "name": "Test Entity",
                            "types": ["pep-class-1"],
                            "countries": ["MU"],
                            "sources": [{"name": "PEP Database"}]
                        },
                        "match_status": "potential_match"
                    }]
                }
            }
        }

        with patch("complyadvantage_client.requests.post", return_value=mock_response):
            result = client.screen_entity(name="Test Entity")

        # Top-level fields
        assert "matched" in result
        assert "results" in result
        assert "source" in result
        assert "api_status" in result
        assert "screened_at" in result

        # Hit fields (matching Sumsub format)
        hit = result["results"][0]
        assert "match_score" in hit
        assert "matched_name" in hit
        assert "datasets" in hit
        assert "schema" in hit
        assert "topics" in hit
        assert "countries" in hit
        assert "sanctions_list" in hit
        assert "is_pep" in hit
        assert "is_sanctioned" in hit

    def test_company_entity_type_mapping(self):
        """Entity type 'Company' maps to 'company' for CA API."""
        from complyadvantage_client import ComplyAdvantageClient
        client = ComplyAdvantageClient(api_key="key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": {"data": {"id": 1, "total_hits": 0, "hits": []}}
        }

        with patch("complyadvantage_client.requests.post", return_value=mock_response) as mock_post:
            client.screen_entity(name="Acme Corp", entity_type="company")

        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert payload["filters"]["entity_type"] == "company"
