"""
Screening Abstraction Configuration — Feature Flags & Provider Settings
=======================================================================
Controls for the ComplyAdvantage migration scaffolding.

SAFETY: Abstraction defaults OFF in every environment.
SAFETY: Provider defaults to "sumsub" (existing provider).
SAFETY: No imports from screening.py or sumsub_client.py.
"""

import os
import logging

logger = logging.getLogger("arie.screening_config")


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


# ── Source of Truth Rules ──
# In Sprint 1–2, all operational dimensions use the legacy source.
# Normalized storage is non-authoritative scaffolding only.

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


def get_source_of_truth(dimension: str) -> str:
    """
    Get the authoritative data source for a given operational dimension.

    In Sprint 1–2, always returns "legacy".
    This will be updated in Sprint 3 when normalized storage becomes authoritative
    for specific dimensions.

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
    return SOURCE_OF_TRUTH_RULES[dimension]
