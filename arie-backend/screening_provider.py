"""
Screening Provider Interface and Registry
==========================================
Defines the abstract base class for screening providers and a simple
registry to look them up by name.

This module does NOT import ``screening.py`` or ``sumsub_client.py``.
Concrete adapters (e.g. ``screening_adapter_sumsub.py``) do the wiring.
"""

import logging
from screening_config import get_active_provider_name

logger = logging.getLogger("arie")


class ScreeningProvider:
    """
    Abstract base class for all screening providers.

    Subclasses must implement every method listed below.
    """

    provider_name: str = ""

    def screen_person(self, name: str, birth_date: str = None,
                      nationality: str = None, entity_type: str = "Person") -> dict:
        """Screen a single person against AML/PEP/sanctions databases."""
        raise NotImplementedError

    def screen_company(self, company_name: str, jurisdiction: str = None) -> dict:
        """Screen a company for corporate registry and sanctions."""
        raise NotImplementedError

    def run_full_screening(self, application_data: dict, directors: list,
                           ubos: list, client_ip: str = None) -> dict:
        """Run the full screening pipeline for an application."""
        raise NotImplementedError

    def is_configured(self) -> bool:
        """Return True if the provider has valid credentials / config."""
        raise NotImplementedError


class ProviderRegistry:
    """
    Simple in-process registry of screening providers keyed by name.
    """

    def __init__(self):
        self._providers: dict = {}

    def register(self, name: str, provider: ScreeningProvider) -> None:
        """Register a provider instance under *name*."""
        if not isinstance(provider, ScreeningProvider):
            raise TypeError(f"Expected ScreeningProvider, got {type(provider).__name__}")
        self._providers[name] = provider
        logger.info("Screening provider registered: %s", name)

    def get(self, name: str) -> ScreeningProvider:
        """Return the provider registered under *name*, or raise KeyError."""
        if name not in self._providers:
            raise KeyError(f"No screening provider registered with name '{name}'")
        return self._providers[name]

    def get_active(self) -> ScreeningProvider:
        """
        Return the provider whose name matches the current
        ``SCREENING_PROVIDER`` configuration.
        """
        active_name = get_active_provider_name()
        return self.get(active_name)

    def list_providers(self) -> list:
        """Return sorted list of registered provider names."""
        return sorted(self._providers.keys())


# Module-level singleton
provider_registry = ProviderRegistry()
