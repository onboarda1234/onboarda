"""
Tests for screening_adapter_sumsub.py — SCR-008
=================================================
Validates the Sumsub adapter without making real API calls.
"""

import pytest
from unittest.mock import patch, MagicMock
from screening_adapter_sumsub import SumsubScreeningAdapter
from screening_provider import ScreeningProvider


class TestSumsubAdapterInterface:
    """Adapter must be a valid ScreeningProvider."""

    def test_is_screening_provider(self):
        adapter = SumsubScreeningAdapter()
        assert isinstance(adapter, ScreeningProvider)

    def test_provider_name(self):
        adapter = SumsubScreeningAdapter()
        assert adapter.provider_name == "sumsub"


class TestRunFullScreening:
    """run_full_screening delegates to screening.run_full_screening and normalizes."""

    @patch("screening_adapter_sumsub.normalize_screening_report")
    def test_delegates_and_normalizes(self, mock_normalize):
        raw_report = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {},
            "director_screenings": [],
            "ubo_screenings": [],
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }
        normalized = dict(raw_report)
        normalized["provider"] = "sumsub"
        normalized["normalized_version"] = "1.0"
        mock_normalize.return_value = normalized

        with patch("screening_adapter_sumsub._run_full_screening",
                   create=True) as mock_rfs:
            # We need to patch at the point of import inside the method
            with patch("screening.run_full_screening", return_value=raw_report):
                adapter = SumsubScreeningAdapter()
                result = adapter.run_full_screening({}, [], [])

        mock_normalize.assert_called_once_with(raw_report, provider="sumsub")
        assert result["provider"] == "sumsub"


class TestScreenPerson:
    """screen_person delegates to screen_sumsub_aml."""

    def test_delegates_to_screen_sumsub_aml(self):
        mock_result = {
            "matched": False,
            "results": [],
            "source": "sumsub",
            "api_status": "live",
            "screened_at": "2025-01-01T00:00:00",
        }
        with patch("screening.screen_sumsub_aml", return_value=mock_result):
            adapter = SumsubScreeningAdapter()
            result = adapter.screen_person("Jane Doe", birth_date="1990-01-01")

        assert result["provider"] == "sumsub"
        assert result["matched"] is False


class TestScreenCompany:
    """screen_company delegates to lookup_opencorporates."""

    def test_delegates_to_lookup_opencorporates(self):
        mock_result = {
            "found": True,
            "companies": [{"name": "Acme"}],
            "source": "opencorporates",
            "api_status": "live",
        }
        with patch("screening.lookup_opencorporates", return_value=mock_result):
            adapter = SumsubScreeningAdapter()
            result = adapter.screen_company("Acme Ltd", jurisdiction="mu")

        assert result["provider"] == "opencorporates"
        assert result["found"] is True


class TestIsConfigured:
    """is_configured delegates to sumsub_client."""

    def test_configured_client(self):
        mock_client = MagicMock()
        mock_client.is_configured = True
        with patch("sumsub_client.get_sumsub_client", return_value=mock_client):
            adapter = SumsubScreeningAdapter()
            assert adapter.is_configured() is True

    def test_unconfigured_client(self):
        mock_client = MagicMock()
        mock_client.is_configured = False
        with patch("sumsub_client.get_sumsub_client", return_value=mock_client):
            adapter = SumsubScreeningAdapter()
            assert adapter.is_configured() is False

    def test_client_import_error(self):
        with patch("sumsub_client.get_sumsub_client", side_effect=Exception("no client")):
            adapter = SumsubScreeningAdapter()
            assert adapter.is_configured() is False
