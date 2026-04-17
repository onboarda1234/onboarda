"""
Screening Abstraction Configuration
====================================
Provides feature flags and source-of-truth rules for the screening
provider abstraction layer.

All flags default to OFF / "sumsub" — the abstraction is inert until
explicitly enabled.
"""

import os
import logging

logger = logging.getLogger("arie")

# ---------------------------------------------------------------------------
# Source-of-truth rules: which provider is authoritative for each dimension.
# During Sprint 1-2 this is always "sumsub".  Future sprints may split
# dimensions across providers.
# ---------------------------------------------------------------------------
SOURCE_OF_TRUTH_RULES = {
    "person_aml":       "sumsub",
    "person_pep":       "sumsub",
    "person_sanctions": "sumsub",
    "company_sanctions":"sumsub",
    "company_registry": "opencorporates",
    "ip_geolocation":   "ipapi",
    "kyc_identity":     "sumsub",
}


def is_abstraction_enabled() -> bool:
    """
    Return True only if the screening abstraction layer is explicitly
    activated.  Defaults to False in every environment.
    """
    val = os.environ.get("ENABLE_SCREENING_ABSTRACTION", "").strip().lower()
    return val in ("true", "1", "yes", "on")


def get_active_provider_name() -> str:
    """
    Return the name of the active screening provider.
    Defaults to ``"sumsub"`` everywhere.
    """
    return os.environ.get("SCREENING_PROVIDER", "sumsub").strip().lower() or "sumsub"


def get_source_of_truth(dimension: str) -> str:
    """
    Return the authoritative provider name for a given screening dimension.

    Falls back to ``get_active_provider_name()`` if the dimension is not
    explicitly mapped.
    """
    return SOURCE_OF_TRUTH_RULES.get(dimension, get_active_provider_name())
