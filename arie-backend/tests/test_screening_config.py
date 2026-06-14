"""
Tests for screening_config.py — Feature flags and provider configuration.
"""

import os
import pytest

from screening_config import (
    is_abstraction_enabled,
    get_active_provider_name,
    get_provider_display_name,
    get_shadow_provider_name,
    is_complyadvantage_active,
    SOURCE_OF_TRUTH_RULES,
    get_source_of_truth,
)


class TestAbstractionFlag:
    """ENABLE_SCREENING_ABSTRACTION must default to False everywhere."""

    def test_default_is_false(self, monkeypatch):
        monkeypatch.delenv("ENABLE_SCREENING_ABSTRACTION", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "development")
        assert is_abstraction_enabled() is False

    def test_default_false_in_testing(self, monkeypatch):
        monkeypatch.delenv("ENABLE_SCREENING_ABSTRACTION", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "testing")
        assert is_abstraction_enabled() is False

    def test_default_false_in_staging(self, monkeypatch):
        monkeypatch.delenv("ENABLE_SCREENING_ABSTRACTION", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "staging")
        assert is_abstraction_enabled() is False

    def test_default_false_in_production(self, monkeypatch):
        monkeypatch.delenv("ENABLE_SCREENING_ABSTRACTION", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")
        assert is_abstraction_enabled() is False

    def test_default_false_in_demo(self, monkeypatch):
        monkeypatch.delenv("ENABLE_SCREENING_ABSTRACTION", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "demo")
        assert is_abstraction_enabled() is False

    def test_can_enable_via_env_var_true(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "true")
        assert is_abstraction_enabled() is True

    def test_can_enable_via_env_var_1(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "1")
        assert is_abstraction_enabled() is True

    def test_can_enable_via_env_var_yes(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "yes")
        assert is_abstraction_enabled() is True

    def test_disable_via_env_var_false(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "false")
        assert is_abstraction_enabled() is False

    def test_disable_via_env_var_0(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "0")
        assert is_abstraction_enabled() is False

    def test_unknown_env_defaults_false(self, monkeypatch):
        monkeypatch.delenv("ENABLE_SCREENING_ABSTRACTION", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "unknown_env")
        assert is_abstraction_enabled() is False


class TestProviderName:
    """SCREENING_PROVIDER must default to 'sumsub' everywhere."""

    def test_default_is_sumsub(self, monkeypatch):
        monkeypatch.delenv("SCREENING_PROVIDER", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "development")
        assert get_active_provider_name() == "sumsub"

    def test_default_sumsub_in_production(self, monkeypatch):
        monkeypatch.delenv("SCREENING_PROVIDER", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")
        assert get_active_provider_name() == "sumsub"

    def test_can_override_via_env_var(self, monkeypatch):
        monkeypatch.setenv("SCREENING_PROVIDER", "complyadvantage")
        assert get_active_provider_name() == "complyadvantage"

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("SCREENING_PROVIDER", "  sumsub  ")
        assert get_active_provider_name() == "sumsub"

    def test_lowercases(self, monkeypatch):
        monkeypatch.setenv("SCREENING_PROVIDER", "ComplyAdvantage")
        assert get_active_provider_name() == "complyadvantage"


class TestShadowProviderName:
    """SCREENING_SHADOW_PROVIDER is opt-in only for D2 comparison runs."""

    def test_default_is_none(self, monkeypatch):
        monkeypatch.delenv("SCREENING_SHADOW_PROVIDER", raising=False)
        assert get_shadow_provider_name() is None

    def test_empty_env_is_none(self, monkeypatch):
        monkeypatch.setenv("SCREENING_SHADOW_PROVIDER", "   ")
        assert get_shadow_provider_name() is None

    def test_normalizes_provider_name(self, monkeypatch):
        monkeypatch.setenv("SCREENING_SHADOW_PROVIDER", " ComplyAdvantage ")
        assert get_shadow_provider_name() == "complyadvantage"


class TestSourceOfTruth:
    """Source of truth must follow the active provider instead of stale docs."""

    def test_all_dimensions_are_legacy(self):
        for dim, source in SOURCE_OF_TRUTH_RULES.items():
            assert source == "legacy", f"{dim} source is not 'legacy'"

    def test_get_source_of_truth_screening_report_defaults_legacy(self, monkeypatch):
        monkeypatch.delenv("SCREENING_PROVIDER", raising=False)
        monkeypatch.delenv("ENABLE_SCREENING_ABSTRACTION", raising=False)
        assert get_source_of_truth("screening_report") == "legacy"

    def test_get_source_of_truth_risk_scoring_defaults_legacy(self, monkeypatch):
        monkeypatch.delenv("SCREENING_PROVIDER", raising=False)
        monkeypatch.delenv("ENABLE_SCREENING_ABSTRACTION", raising=False)
        assert get_source_of_truth("risk_scoring") == "legacy"

    def test_get_source_of_truth_memo_generation_defaults_legacy(self, monkeypatch):
        monkeypatch.delenv("SCREENING_PROVIDER", raising=False)
        monkeypatch.delenv("ENABLE_SCREENING_ABSTRACTION", raising=False)
        assert get_source_of_truth("memo_generation") == "legacy"

    def test_get_source_of_truth_approval_gates_defaults_legacy(self, monkeypatch):
        monkeypatch.delenv("SCREENING_PROVIDER", raising=False)
        monkeypatch.delenv("ENABLE_SCREENING_ABSTRACTION", raising=False)
        assert get_source_of_truth("approval_gates") == "legacy"

    def test_get_source_of_truth_backoffice_display_defaults_legacy(self, monkeypatch):
        monkeypatch.delenv("SCREENING_PROVIDER", raising=False)
        monkeypatch.delenv("ENABLE_SCREENING_ABSTRACTION", raising=False)
        assert get_source_of_truth("backoffice_display") == "legacy"

    def test_get_source_of_truth_is_ca_when_ca_cutover_active(self, monkeypatch):
        monkeypatch.setenv("SCREENING_PROVIDER", "complyadvantage")
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "true")

        assert is_complyadvantage_active() is True
        assert get_source_of_truth("screening_report") == "complyadvantage"
        assert get_source_of_truth("approval_gates") == "complyadvantage"
        assert get_source_of_truth("backoffice_display") == "complyadvantage"

    def test_ca_provider_display_name_is_mesh_and_unknown_stays_unknown(self):
        assert get_provider_display_name("complyadvantage") == "ComplyAdvantage Mesh"
        assert get_provider_display_name("ca") == "ComplyAdvantage Mesh"
        assert get_provider_display_name("") == "Unknown"
        assert get_provider_display_name(None) == "Unknown"
        assert get_provider_display_name("mystery_provider") == "mystery_provider"

    def test_unknown_dimension_raises(self):
        with pytest.raises(ValueError, match="Unknown source-of-truth dimension"):
            get_source_of_truth("nonexistent_dimension")

    def test_all_expected_dimensions_present(self):
        expected = [
            "screening_report", "risk_scoring", "memo_generation",
            "approval_gates", "backoffice_display", "pep_detection",
            "sanctions_detection", "webhook_updates",
        ]
        for dim in expected:
            assert dim in SOURCE_OF_TRUTH_RULES
