"""
Screening Provider Interface and Registry
==========================================
Abstract base class for screening providers and a registry
for provider lookup.

SAFETY: No imports from screening.py or sumsub_client.py.
SAFETY: No runtime side effects on import.
SAFETY: No provider switching wired yet.
"""

import logging

logger = logging.getLogger("arie.screening_provider")


class ScreeningProvider:
    """
    Abstract base class for screening providers.

    All screening providers must implement this interface.
    Subclasses are thin wrappers around existing provider modules.
    """

    provider_name = ""

    def screen_person(self, name, birth_date=None, nationality=None, entity_type="Person"):
        """
        Screen an individual person.

        Args:
            name: Full name of the person.
            birth_date: Date of birth (optional).
            nationality: Nationality (optional).
            entity_type: "Person" or "Company".

        Returns:
            dict: Normalized person screening result.
        """
        raise NotImplementedError(f"{self.__class__.__name__} must implement screen_person()")

    def screen_company(self, company_name, jurisdiction=None):
        """
        Screen a company.

        Args:
            company_name: Name of the company.
            jurisdiction: Jurisdiction code (optional).

        Returns:
            dict: Normalized company screening result.
        """
        raise NotImplementedError(f"{self.__class__.__name__} must implement screen_company()")

    def run_full_screening(self, application_data, directors, ubos, client_ip=None):
        """
        Run full screening pipeline (all agents in parallel).

        Args:
            application_data: Application data dict.
            directors: List of director dicts.
            ubos: List of UBO dicts.
            client_ip: Client IP address (optional).

        Returns:
            dict: Normalized screening report.
        """
        raise NotImplementedError(f"{self.__class__.__name__} must implement run_full_screening()")

    def is_configured(self) -> bool:
        """
        Check if this provider is properly configured.

        Returns:
            True if all required configuration (API keys, etc.) is available.
        """
        raise NotImplementedError(f"{self.__class__.__name__} must implement is_configured()")


class ScreeningProviderRegistry:
    """
    Registry for screening provider instances.

    Providers are registered by name and can be looked up.
    """

    def __init__(self):
        self._providers = {}

    def register(self, name: str, provider_instance) -> None:
        """
        Register a screening provider.

        Args:
            name: Provider name (e.g., "sumsub", "complyadvantage").
            provider_instance: Instance of ScreeningProvider subclass.

        Raises:
            TypeError: If provider_instance is not a ScreeningProvider.
            ValueError: If name is empty.
        """
        if not name:
            raise ValueError("Provider name must not be empty")
        if not isinstance(provider_instance, ScreeningProvider):
            raise TypeError(
                f"Provider must be a ScreeningProvider instance, "
                f"got {type(provider_instance).__name__}"
            )
        self._providers[name] = provider_instance
        logger.info("Registered screening provider: %s", name)

    def get(self, name: str):
        """
        Get a registered screening provider by name.

        Args:
            name: Provider name.

        Returns:
            ScreeningProvider instance.

        Raises:
            KeyError: If provider is not registered.
        """
        if name not in self._providers:
            raise KeyError(
                f"Screening provider '{name}' is not registered. "
                f"Available providers: {list(self._providers.keys())}"
            )
        return self._providers[name]

    def get_active(self):
        """
        Get the currently active screening provider based on configuration.

        Returns:
            ScreeningProvider instance for the active provider.

        Raises:
            KeyError: If active provider is not registered.
        """
        from screening_config import get_active_provider_name
        name = get_active_provider_name()
        return self.get(name)

    @property
    def registered_names(self) -> list:
        """Return list of registered provider names."""
        return list(self._providers.keys())


# Module-level registry instance (lazy — no side effects on import)
_registry = ScreeningProviderRegistry()


def get_registry() -> ScreeningProviderRegistry:
    """Get the module-level provider registry."""
    return _registry
