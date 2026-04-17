"""
Tests for screening_config.py — SCR-002
========================================
Validates feature flags default to safe values (abstraction off, sumsub active).
"""

import os
import pytest
from screening_config import (
    is_abstraction_enabled,
    get_active_provider_name,
    get_source_of_truth,
    SOURCE_OF_TRUTH_RULES,
)


class TestAbstractionFlag:
    """ENABLE_SCREENING_ABSTRACTION must default to off."""

    def test_default_is_disabled(self, monkeypatch):
        monkeypatch.delenv("ENABLE_SCREENING_ABSTRACTION", raising=False)
        assert is_abstraction_enabled() is False

    def test_empty_string_is_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "")
        assert is_abstraction_enabled() is False

    def test_false_string_is_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "false")
        assert is_abstraction_enabled() is False

    def test_zero_is_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "0")
        assert is_abstraction_enabled() is False

    def test_true_enables(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "true")
        assert is_abstraction_enabled() is True

    def test_one_enables(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "1")
        assert is_abstraction_enabled() is True

    def test_yes_enables(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "yes")
        assert is_abstraction_enabled() is True

    def test_on_enables(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "on")
        assert is_abstraction_enabled() is True

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SCREENING_ABSTRACTION", "TRUE")
        assert is_abstraction_enabled() is True


class TestActiveProviderName:
    """SCREENING_PROVIDER must default to sumsub."""

    def test_default_is_sumsub(self, monkeypatch):
        monkeypatch.delenv("SCREENING_PROVIDER", raising=False)
        assert get_active_provider_name() == "sumsub"

    def test_explicit_sumsub(self, monkeypatch):
        monkeypatch.setenv("SCREENING_PROVIDER", "sumsub")
        assert get_active_provider_name() == "sumsub"

    def test_custom_provider(self, monkeypatch):
        monkeypatch.setenv("SCREENING_PROVIDER", "complyadvantage")
        assert get_active_provider_name() == "complyadvantage"

    def test_empty_string_defaults_to_sumsub(self, monkeypatch):
        monkeypatch.setenv("SCREENING_PROVIDER", "")
        assert get_active_provider_name() == "sumsub"

    def test_normalised_to_lowercase(self, monkeypatch):
        monkeypatch.setenv("SCREENING_PROVIDER", "Sumsub")
        assert get_active_provider_name() == "sumsub"


class TestSourceOfTruthRules:
    """SOURCE_OF_TRUTH_RULES is well-formed and queryable."""

    def test_all_dimensions_present(self):
        expected = {
            "person_aml", "person_pep", "person_sanctions",
            "company_sanctions", "company_registry",
            "ip_geolocation", "kyc_identity",
        }
        assert expected == set(SOURCE_OF_TRUTH_RULES.keys())

    def test_person_dimensions_default_sumsub(self):
        for dim in ("person_aml", "person_pep", "person_sanctions"):
            assert SOURCE_OF_TRUTH_RULES[dim] == "sumsub"

    def test_get_source_of_truth_known(self):
        assert get_source_of_truth("person_aml") == "sumsub"
        assert get_source_of_truth("company_registry") == "opencorporates"

    def test_get_source_of_truth_unknown_dimension(self, monkeypatch):
        monkeypatch.delenv("SCREENING_PROVIDER", raising=False)
        assert get_source_of_truth("unknown_dim") == "sumsub"
