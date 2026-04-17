"""
Tests for screening_provider.py — Provider interface and registry.
"""

import pytest

from screening_provider import (
    ScreeningProvider,
    ScreeningProviderRegistry,
    get_registry,
)


class DummyProvider(ScreeningProvider):
    """Test provider for registry tests."""
    provider_name = "dummy"

    def screen_person(self, name, birth_date=None, nationality=None, entity_type="Person"):
        return {"person_name": name, "matched": False}

    def screen_company(self, company_name, jurisdiction=None):
        return {"company_name": company_name, "found": False}

    def run_full_screening(self, application_data, directors, ubos, client_ip=None):
        return {"total_hits": 0}

    def is_configured(self) -> bool:
        return True


class TestScreeningProviderBase:
    """Base class must raise NotImplementedError for unimplemented methods."""

    def test_screen_person_raises(self):
        p = ScreeningProvider()
        with pytest.raises(NotImplementedError):
            p.screen_person("test")

    def test_screen_company_raises(self):
        p = ScreeningProvider()
        with pytest.raises(NotImplementedError):
            p.screen_company("test")

    def test_run_full_screening_raises(self):
        p = ScreeningProvider()
        with pytest.raises(NotImplementedError):
            p.run_full_screening({}, [], [])

    def test_is_configured_raises(self):
        p = ScreeningProvider()
        with pytest.raises(NotImplementedError):
            p.is_configured()

    def test_provider_name_default(self):
        p = ScreeningProvider()
        assert p.provider_name == ""


class TestDummyProvider:
    """Dummy provider must be a valid ScreeningProvider."""

    def test_is_instance(self):
        assert isinstance(DummyProvider(), ScreeningProvider)

    def test_screen_person(self):
        result = DummyProvider().screen_person("John")
        assert result["person_name"] == "John"

    def test_screen_company(self):
        result = DummyProvider().screen_company("Acme Corp")
        assert result["company_name"] == "Acme Corp"

    def test_run_full_screening(self):
        result = DummyProvider().run_full_screening({}, [], [])
        assert result["total_hits"] == 0

    def test_is_configured(self):
        assert DummyProvider().is_configured() is True


class TestRegistry:
    """ScreeningProviderRegistry must support register/get/get_active."""

    def test_register_and_get(self):
        reg = ScreeningProviderRegistry()
        dp = DummyProvider()
        reg.register("dummy", dp)
        assert reg.get("dummy") is dp

    def test_get_unknown_raises(self):
        reg = ScreeningProviderRegistry()
        with pytest.raises(KeyError, match="not registered"):
            reg.get("unknown")

    def test_register_empty_name_raises(self):
        reg = ScreeningProviderRegistry()
        with pytest.raises(ValueError, match="must not be empty"):
            reg.register("", DummyProvider())

    def test_register_non_provider_raises(self):
        reg = ScreeningProviderRegistry()
        with pytest.raises(TypeError, match="ScreeningProvider"):
            reg.register("bad", {"not": "a provider"})

    def test_registered_names(self):
        reg = ScreeningProviderRegistry()
        reg.register("dummy1", DummyProvider())
        reg.register("dummy2", DummyProvider())
        assert sorted(reg.registered_names) == ["dummy1", "dummy2"]

    def test_get_active_uses_config(self, monkeypatch):
        monkeypatch.setenv("SCREENING_PROVIDER", "dummy")
        reg = ScreeningProviderRegistry()
        dp = DummyProvider()
        reg.register("dummy", dp)
        assert reg.get_active() is dp

    def test_get_active_raises_if_not_registered(self, monkeypatch):
        monkeypatch.setenv("SCREENING_PROVIDER", "nonexistent")
        reg = ScreeningProviderRegistry()
        with pytest.raises(KeyError):
            reg.get_active()


class TestModuleRegistry:
    """Module-level registry must be available."""

    def test_get_registry_returns_instance(self):
        reg = get_registry()
        assert isinstance(reg, ScreeningProviderRegistry)

    def test_get_registry_same_instance(self):
        assert get_registry() is get_registry()
