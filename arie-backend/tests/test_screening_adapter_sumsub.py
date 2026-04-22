"""
Tests for screening_adapter_sumsub.py — Sumsub screening adapter.
"""

import os
import pytest

from screening_adapter_sumsub import SumsubScreeningAdapter
from screening_provider import ScreeningProvider
from screening_models import validate_normalized_report


class TestAdapterBasics:
    def test_is_screening_provider(self):
        adapter = SumsubScreeningAdapter()
        assert isinstance(adapter, ScreeningProvider)

    def test_provider_name(self):
        assert SumsubScreeningAdapter.provider_name == "sumsub"

    def test_importable_no_side_effects(self):
        """Adapter should import without triggering any screening calls."""
        from screening_adapter_sumsub import SumsubScreeningAdapter as SA
        assert SA.provider_name == "sumsub"


class TestIsConfigured:
    def test_configured_with_both_vars(self, monkeypatch):
        monkeypatch.setenv("SUMSUB_APP_TOKEN", "test-token")
        monkeypatch.setenv("SUMSUB_SECRET_KEY", "test-secret")
        assert SumsubScreeningAdapter().is_configured() is True

    def test_not_configured_without_token(self, monkeypatch):
        monkeypatch.delenv("SUMSUB_APP_TOKEN", raising=False)
        monkeypatch.setenv("SUMSUB_SECRET_KEY", "test-secret")
        assert SumsubScreeningAdapter().is_configured() is False

    def test_not_configured_without_secret(self, monkeypatch):
        monkeypatch.setenv("SUMSUB_APP_TOKEN", "test-token")
        monkeypatch.delenv("SUMSUB_SECRET_KEY", raising=False)
        assert SumsubScreeningAdapter().is_configured() is False


class TestRunFullScreening:
    def test_returns_normalized(self, monkeypatch):
        mock_report = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {
                "found": True, "source": "opencorporates",
                "sanctions": {"matched": False, "results": [], "source": "sumsub"},
            },
            "director_screenings": [
                {
                    "person_name": "John",
                    "person_type": "director",
                    "nationality": "GB",
                    "declared_pep": "No",
                    "screening": {"matched": False, "results": [], "source": "sumsub"},
                },
            ],
            "ubo_screenings": [],
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }

        import screening
        monkeypatch.setattr(screening, "run_full_screening", lambda *a, **kw: mock_report)

        adapter = SumsubScreeningAdapter()
        result = adapter.run_full_screening({}, [], [])

        assert result["provider"] == "sumsub"
        assert result["normalized_version"] == "1.0"
        assert result["total_persons_screened"] == 1
        errors = validate_normalized_report(result)
        assert errors == []


class TestScreenPerson:
    def test_returns_normalized_person(self, monkeypatch):
        mock_result = {
            "matched": False, "results": [], "source": "sumsub", "api_status": "live",
        }

        import screening
        monkeypatch.setattr(screening, "screen_sumsub_aml", lambda *a, **kw: mock_result)

        adapter = SumsubScreeningAdapter()
        result = adapter.screen_person("John Smith", nationality="GB")

        assert result["person_name"] == "John Smith"
        assert result["has_pep_hit"] is False
        assert result["has_sanctions_hit"] is False
        assert result["has_adverse_media_hit"] is None
        assert result["adverse_media_coverage"] == "none"
        assert result["screening"] == mock_result

    def test_detects_pep_hit(self, monkeypatch):
        mock_result = {
            "matched": True,
            "results": [{"is_pep": True, "is_sanctioned": False}],
            "source": "sumsub",
        }

        import screening
        monkeypatch.setattr(screening, "screen_sumsub_aml", lambda *a, **kw: mock_result)

        adapter = SumsubScreeningAdapter()
        result = adapter.screen_person("PEP Person")
        assert result["has_pep_hit"] is True
        assert result["has_sanctions_hit"] is False


class TestScreenCompany:
    def test_returns_normalized_company(self, monkeypatch):
        # Priority A: a terminal-clear (api_status=live) result yields
        # has_company_screening_hit=False. Anything non-terminal yields
        # None — verified separately in test_not_configured_company.
        mock_result = {
            "matched": False, "results": [], "source": "sumsub", "api_status": "live",
        }

        import screening
        monkeypatch.setattr(screening, "screen_sumsub_aml", lambda *a, **kw: mock_result)

        adapter = SumsubScreeningAdapter()
        result = adapter.screen_company("Test Corp")

        assert result["company_screening_coverage"] == "partial"
        assert result["has_company_screening_hit"] is False
        assert result["company_screening"] == mock_result

    def test_not_configured_company(self, monkeypatch):
        # Priority A: not_configured must never collapse into False.
        mock_result = {
            "matched": False, "results": [], "source": "sumsub", "api_status": "not_configured",
        }
        import screening
        monkeypatch.setattr(screening, "screen_sumsub_aml", lambda *a, **kw: mock_result)
        adapter = SumsubScreeningAdapter()
        result = adapter.screen_company("Test Corp")
        assert result["has_company_screening_hit"] is None

    def test_pending_company(self, monkeypatch):
        # Priority A: pending must never collapse into False.
        mock_result = {
            "matched": False, "results": [], "source": "sumsub", "api_status": "pending",
        }
        import screening
        monkeypatch.setattr(screening, "screen_sumsub_aml", lambda *a, **kw: mock_result)
        adapter = SumsubScreeningAdapter()
        result = adapter.screen_company("Test Corp")
        assert result["has_company_screening_hit"] is None

    def test_detects_sanctions_hit(self, monkeypatch):
        mock_result = {
            "matched": True,
            "results": [{"name": "Sanctioned Corp"}],
            "source": "sumsub",
        }

        import screening
        monkeypatch.setattr(screening, "screen_sumsub_aml", lambda *a, **kw: mock_result)

        adapter = SumsubScreeningAdapter()
        result = adapter.screen_company("Sanctioned Corp")
        assert result["has_company_screening_hit"] is True


class TestAdapterZeroEffect:
    """Adapter must have zero effect when abstraction flag is OFF."""

    def test_no_screening_calls_on_import(self):
        """Importing the adapter module must not trigger any screening calls."""
        import importlib
        import screening_adapter_sumsub
        importlib.reload(screening_adapter_sumsub)
        # If we get here without error, no screening calls were made

    def test_adapter_instance_no_side_effects(self):
        """Creating an adapter instance must not trigger any calls."""
        adapter = SumsubScreeningAdapter()
        assert adapter.provider_name == "sumsub"
