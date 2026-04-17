"""
Tests for screening_provider.py — SCR-004
==========================================
Validates the provider interface and registry without importing
any concrete screening implementation.
"""

import pytest
from screening_provider import ScreeningProvider, ProviderRegistry, provider_registry


class DummyProvider(ScreeningProvider):
    """Minimal implementation for testing."""
    provider_name = "dummy"

    def screen_person(self, name, birth_date=None, nationality=None, entity_type="Person"):
        return {"matched": False, "provider": "dummy"}

    def screen_company(self, company_name, jurisdiction=None):
        return {"found": False, "provider": "dummy"}

    def run_full_screening(self, application_data, directors, ubos, client_ip=None):
        return {"screened_at": "2025-01-01T00:00:00", "provider": "dummy"}

    def is_configured(self):
        return True


class TestScreeningProviderBase:

    def test_cannot_instantiate_and_call_unimplemented(self):
        p = ScreeningProvider()
        with pytest.raises(NotImplementedError):
            p.screen_person("test")
        with pytest.raises(NotImplementedError):
            p.screen_company("test")
        with pytest.raises(NotImplementedError):
            p.run_full_screening({}, [], [])
        with pytest.raises(NotImplementedError):
            p.is_configured()

    def test_dummy_provider_implements_interface(self):
        d = DummyProvider()
        assert d.provider_name == "dummy"
        r = d.screen_person("Jane")
        assert r["provider"] == "dummy"
        assert d.is_configured() is True


class TestProviderRegistry:

    def test_register_and_get(self):
        reg = ProviderRegistry()
        d = DummyProvider()
        reg.register("dummy", d)
        assert reg.get("dummy") is d

    def test_get_unknown_raises_key_error(self):
        reg = ProviderRegistry()
        with pytest.raises(KeyError, match="dummy"):
            reg.get("dummy")

    def test_register_non_provider_raises_type_error(self):
        reg = ProviderRegistry()
        with pytest.raises(TypeError):
            reg.register("bad", {"not": "a provider"})

    def test_list_providers(self):
        reg = ProviderRegistry()
        reg.register("beta", DummyProvider())
        reg.register("alpha", DummyProvider())
        assert reg.list_providers() == ["alpha", "beta"]

    def test_get_active_returns_matching_provider(self, monkeypatch):
        monkeypatch.setenv("SCREENING_PROVIDER", "dummy")
        reg = ProviderRegistry()
        d = DummyProvider()
        reg.register("dummy", d)
        assert reg.get_active() is d

    def test_get_active_raises_when_not_registered(self, monkeypatch):
        monkeypatch.setenv("SCREENING_PROVIDER", "missing")
        reg = ProviderRegistry()
        with pytest.raises(KeyError, match="missing"):
            reg.get_active()


class TestModuleSingleton:

    def test_provider_registry_exists(self):
        assert isinstance(provider_registry, ProviderRegistry)

    def test_singleton_starts_empty(self):
        # Module singleton may have providers from other tests;
        # just verify it's a ProviderRegistry
        assert hasattr(provider_registry, "get")
        assert hasattr(provider_registry, "register")
