"""
Screening Abstraction Configuration — Feature Flags & Provider Settings
=======================================================================
Controls for screening-provider routing and runtime status reporting.

SAFETY: Abstraction defaults OFF in every environment.
SAFETY: Provider defaults to "sumsub" (existing provider).
SAFETY: No imports from screening.py or sumsub_client.py.
"""

import os
import logging

logger = logging.getLogger("arie.screening_config")


COMPLYADVANTAGE_MESH_PROVIDER = "complyadvantage"
SUMSUB_PROVIDER = "sumsub"
OPENCORPORATES_PROVIDER = "opencorporates"

PROVIDER_DISPLAY_NAMES = {
    COMPLYADVANTAGE_MESH_PROVIDER: "ComplyAdvantage Mesh",
    "ca": "ComplyAdvantage Mesh",
    "mesh": "ComplyAdvantage Mesh",
    SUMSUB_PROVIDER: "Sumsub",
    OPENCORPORATES_PROVIDER: "OpenCorporates",
}


# ── Feature Flag Defaults ──
# Abstraction is OFF by default in ALL environments.
# Must be explicitly enabled via environment variable.

_ABSTRACTION_DEFAULTS = {
    "development": False,
    "testing": False,
    "demo": False,
    "staging": False,
    "production": False,
}

_PROVIDER_DEFAULTS = {
    "development": "sumsub",
    "testing": "sumsub",
    "demo": "sumsub",
    "staging": "sumsub",
    "production": "sumsub",
}


def is_abstraction_enabled() -> bool:
    """
    Check if the screening abstraction layer is enabled.

    Resolution order:
    1. ENABLE_SCREENING_ABSTRACTION environment variable
    2. Default for current environment (always False)

    Returns False in all environments unless explicitly overridden.
    """
    env_val = os.environ.get("ENABLE_SCREENING_ABSTRACTION")
    if env_val is not None:
        return env_val.lower() in ("true", "1", "yes", "on")
    env = os.environ.get("ENVIRONMENT", "development").lower().strip()
    return _ABSTRACTION_DEFAULTS.get(env, False)


def get_active_provider_name() -> str:
    """
    Get the name of the active screening provider.

    Resolution order:
    1. SCREENING_PROVIDER environment variable
    2. Default for current environment (always "sumsub")
    """
    env_val = os.environ.get("SCREENING_PROVIDER")
    if env_val is not None:
        return env_val.strip().lower()
    env = os.environ.get("ENVIRONMENT", "development").lower().strip()
    return _PROVIDER_DEFAULTS.get(env, "sumsub")


def get_shadow_provider_name() -> str | None:
    """
    Get the optional D2 shadow screening provider.

    Resolution order:
    1. SCREENING_SHADOW_PROVIDER environment variable
    2. None

    This is intentionally separate from SCREENING_PROVIDER.  The active
    provider remains the operational source of truth; the shadow provider is
    comparison-only and must never become authoritative by being set here.
    """
    env_val = os.environ.get("SCREENING_SHADOW_PROVIDER")
    if env_val is None:
        return None
    value = env_val.strip().lower()
    return value or None


# ── Source of Truth Rules ──
# These dimensions are runtime-routed. Under the safe default they remain on
# the legacy Sumsub path. When SCREENING_PROVIDER=complyadvantage and
# ENABLE_SCREENING_ABSTRACTION=true, ComplyAdvantage Mesh becomes the AML
# screening source of truth for these dimensions.

SOURCE_OF_TRUTH_RULES = {
    "screening_report": "legacy",
    "risk_scoring": "legacy",
    "memo_generation": "legacy",
    "approval_gates": "legacy",
    "backoffice_display": "legacy",
    "pep_detection": "legacy",
    "sanctions_detection": "legacy",
    "webhook_updates": "legacy",
}


def get_provider_display_name(provider_name: str | None, *, unknown_label: str = "Unknown") -> str:
    """Return the business-readable provider name without fabricating CA provenance."""
    raw = str(provider_name or "").strip()
    if not raw:
        return unknown_label
    key = raw.lower().replace("_", "").replace("-", "").replace(" ", "")
    return PROVIDER_DISPLAY_NAMES.get(key, raw)


def is_complyadvantage_active() -> bool:
    """Return True only when CA Mesh is both selected and allowed to route."""
    return get_active_provider_name() == COMPLYADVANTAGE_MESH_PROVIDER and is_abstraction_enabled()


def get_source_of_truth(dimension: str) -> str:
    """
    Get the authoritative data source for a given operational dimension.

    Returns "complyadvantage" only when the runtime provider cutover is active.
    Otherwise returns "legacy" for the Sumsub/legacy screening path.

    Args:
        dimension: Operational dimension (e.g., "screening_report", "risk_scoring")

    Returns:
        "legacy" — the existing prescreening_data.screening_report is authoritative.

    Raises:
        ValueError: If dimension is not recognized.
    """
    if dimension not in SOURCE_OF_TRUTH_RULES:
        raise ValueError(
            f"Unknown source-of-truth dimension: '{dimension}'. "
            f"Valid dimensions: {list(SOURCE_OF_TRUTH_RULES.keys())}"
        )
    if is_complyadvantage_active():
        return COMPLYADVANTAGE_MESH_PROVIDER
    return SOURCE_OF_TRUTH_RULES[dimension]
