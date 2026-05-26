"""
Screening Provider Interface and Registry
==========================================
Abstract base class for screening providers and a registry
for provider lookup.

SAFETY: No imports from screening.py or sumsub_client.py.
SAFETY: No runtime side effects on import.
SAFETY: No provider switching wired yet.

Phase A5 additions
------------------
* ``ProviderNotRegistered`` — named exception raised when a requested
  provider name is absent from the factory registry.
* ``_factory_registry`` — module-level dict mapping provider names to
  factory callables.  Initialises EMPTY; no provider registers itself
  automatically.
* ``register_provider(name, factory)`` — register a factory by name.
* ``get_provider(name)`` — resolve a factory; raises ``ProviderNotRegistered``
  (never returns None silently).
* ``list_providers()`` — return the current registry contents (list of names).
* ``screening_abstraction_enabled()`` — thin helper that re-reads the
  ``ENABLE_SCREENING_ABSTRACTION`` environment flag dynamically so tests can
  monkeypatch it.  Also captures the flag value once at module load for the
  startup log line.
"""

import logging

logger = logging.getLogger("arie.screening_provider")

#: Canonical provider name string for Sumsub.
#: Use this constant when registering or resolving the Sumsub provider.
#: Inline string literals elsewhere in the codebase will be migrated in a
#: separate sweep.
SUMSUB_PROVIDER_NAME = "sumsub"
COMPLYADVANTAGE_PROVIDER_NAME = "complyadvantage"


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


# ---------------------------------------------------------------------------
# Phase A5 — factory-based provider registry
# ---------------------------------------------------------------------------

class ProviderNotRegistered(Exception):
    """Raised when a requested provider name is absent from the factory registry.

    Callers MUST handle this explicitly.  ``get_provider()`` never returns
    ``None`` silently; a missing provider is always an error.
    """


# Mapping of provider name → factory callable.
# Initialises EMPTY at module load.  No provider registers itself here.
# The first registration happens in Track C, not in this phase.
_factory_registry: dict[str, object] = {}

# Read the feature flag once at module import for the startup log line.
# The exported helper ``screening_abstraction_enabled()`` performs a fresh
# env-var read so test monkeypatching continues to work correctly.
def _read_abstraction_flag() -> bool:
    from screening_config import is_abstraction_enabled
    return is_abstraction_enabled()

_abstraction_enabled_at_import: bool = _read_abstraction_flag()
logger.debug(
    "screening_provider: ENABLE_SCREENING_ABSTRACTION=%s at import time",
    _abstraction_enabled_at_import,
)


def screening_abstraction_enabled() -> bool:
    """Return True if the ENABLE_SCREENING_ABSTRACTION feature flag is set.

    Reads the environment variable dynamically on every call so that
    test monkeypatching works correctly.
    """
    from screening_config import is_abstraction_enabled
    return is_abstraction_enabled()


def register_provider(name: str, factory) -> None:
    """Register a provider factory by name.

    Args:
        name:    Provider identifier string (e.g. ``"sumsub"``).
        factory: Any callable that produces a screening provider.  The
                 registry stores it as-is; no instantiation occurs here.

    Raises:
        ValueError: If *name* is empty.
    """
    if not name:
        raise ValueError("Provider name must not be empty")
    _factory_registry[name] = factory
    logger.info("Registered provider factory: %s", name)


def get_provider(name: str):
    """Return the factory registered under *name*.

    Args:
        name: Provider identifier string.

    Returns:
        The factory callable that was passed to ``register_provider()``.

    Raises:
        ProviderNotRegistered: If no provider has been registered under
            *name*.  Never returns ``None`` silently.
    """
    if name not in _factory_registry:
        raise ProviderNotRegistered(
            f"No provider registered under name '{name}'. "
            f"Registered providers: {list(_factory_registry.keys())}"
        )
    return _factory_registry[name]


def list_providers() -> list[str]:
    """Return a list of registered provider names (may be empty).

    The list is a snapshot; mutations to the registry after this call
    are not reflected in the returned list.
    """
    return list(_factory_registry.keys())
