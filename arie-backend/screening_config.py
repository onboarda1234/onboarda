"""
Screening Abstraction Configuration — Feature Flags & Provider Settings
=======================================================================
Controls for screening-provider routing and runtime status reporting.

SAFETY: Abstraction defaults OFF in every environment.
SAFETY: Provider defaults to "sumsub" for legacy routing compatibility.
SAFETY: No imports from screening.py or sumsub_client.py.

Provider responsibility model:
- Sumsub is authoritative only for IDV, liveness, and identity document checks.
- ComplyAdvantage Mesh is authoritative for sanctions, PEP, watchlists,
  adverse media, and material screening concerns when screening is routed to CA.
- Legacy Sumsub-hosted screening paths remain compatibility paths, not the
  target screening/adverse-media source of truth.
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
    SUMSUB_PROVIDER: "Sumsub IDV/KYC",
    OPENCORPORATES_PROVIDER: "OpenCorporates",
}

PROVIDER_RESPONSIBILITY_MODEL = {
    SUMSUB_PROVIDER: {
        "provider_label": "Sumsub IDV/KYC",
        "authoritative_for": (
            "idv_identity_verification",
            "liveness_face_match",
            "identity_document_checks",
        ),
        "not_authoritative_for": (
            "sanctions_screening",
            "pep_screening",
            "watchlists",
            "adverse_media",
            "material_screening_concerns",
        ),
        "approval_gates": ("identity_verification",),
        "legacy_guidance": (
            "Legacy Sumsub-hosted AML/screening fields may exist for compatibility.",
            "New screening/adverse-media approval logic must not treat them as authoritative.",
        ),
    },
    COMPLYADVANTAGE_MESH_PROVIDER: {
        "provider_label": "ComplyAdvantage Mesh",
        "authoritative_for": (
            "sanctions_screening",
            "pep_screening",
            "watchlists",
            "adverse_media",
            "material_screening_concerns",
        ),
        "not_authoritative_for": (
            "idv_identity_verification",
            "liveness_face_match",
            "identity_document_checks",
        ),
        "approval_gates": ("screening_adverse_media",),
        "legacy_guidance": (
            "ComplyAdvantage Mesh provider truth should drive screening/adverse-media gates.",
            "Runtime cutover still depends on SCREENING_PROVIDER and ENABLE_SCREENING_ABSTRACTION.",
        ),
    },
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

# SRP-2a Phase D: existing-customer re-screen via the Mesh rescreen workflow.
# OFF by default in ALL environments; requires "monitor on demand" on the CA
# account. When off, re-screens keep today's create-and-screen + conflict
# classification behaviour byte-identical.
_CA_RESCREEN_DEFAULTS = {
    "development": False,
    "testing": False,
    "demo": False,
    "staging": False,
    "production": False,
}

# Phase G: on-demand ComplyAdvantage profile hydration (the Mesh
# GET /v2/alerts/{alert_identifier}/risks endpoint). OFF by default in ALL
# environments; requires the "View alerts" permission on the CA account. When
# off, no risks-endpoint reads occur and back-office behaviour is byte-identical
# to today. Hydration is DISPLAY/AUDIT enrichment only — it never touches risk,
# gates, triage, or dispositions.
_CA_PROFILE_HYDRATION_DEFAULTS = {
    "development": False,
    "testing": False,
    "demo": False,
    "staging": False,
    "production": False,
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
    # Canonicalized (audit H8 / PR-13) so alias values hit the right defaults.
    from environment import canonicalize_environment
    env = canonicalize_environment(os.environ.get("ENVIRONMENT") or os.environ.get("ENV"))
    return _ABSTRACTION_DEFAULTS.get(env, False)


def is_ca_rescreen_enabled() -> bool:
    """
    Check if the SRP-2a existing-customer re-screen pathway is enabled.

    Resolution order:
    1. ENABLE_CA_RESCREEN environment variable
    2. Default for current environment (always False)

    Returns False in all environments unless explicitly overridden.
    """
    env_val = os.environ.get("ENABLE_CA_RESCREEN")
    if env_val is not None:
        return env_val.lower() in ("true", "1", "yes", "on")
    # Canonicalized (audit H8 / PR-13) so alias values hit the right defaults.
    from environment import canonicalize_environment
    env = canonicalize_environment(os.environ.get("ENVIRONMENT") or os.environ.get("ENV"))
    return _CA_RESCREEN_DEFAULTS.get(env, False)


def is_ca_profile_hydration_enabled() -> bool:
    """
    Check if the Phase G on-demand CA profile hydration pathway is enabled.

    Resolution order:
    1. ENABLE_CA_PROFILE_HYDRATION environment variable
    2. Default for current environment (always False)

    Returns False in all environments unless explicitly overridden. When off,
    the back office never calls the Mesh risks endpoint and no hydrated
    attributes are added to stored reports.
    """
    env_val = os.environ.get("ENABLE_CA_PROFILE_HYDRATION")
    if env_val is not None:
        return env_val.lower() in ("true", "1", "yes", "on")
    # Canonicalized (audit H8 / PR-13) so alias values hit the right defaults.
    from environment import canonicalize_environment
    env = canonicalize_environment(os.environ.get("ENVIRONMENT") or os.environ.get("ENV"))
    return _CA_PROFILE_HYDRATION_DEFAULTS.get(env, False)


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
    # Canonicalized (audit H8 / PR-13) so alias values hit the right defaults.
    from environment import canonicalize_environment
    env = canonicalize_environment(os.environ.get("ENVIRONMENT") or os.environ.get("ENV"))
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
# These dimensions are runtime-routed. Under the current safe default they
# remain on the legacy compatibility path. Provider responsibility is stricter
# than runtime cutover: Sumsub remains IDV-only, while ComplyAdvantage Mesh is
# the target authority for sanctions, PEP, watchlists, adverse media, and
# material screening concerns.

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


def _copy_provider_responsibility(entry: dict) -> dict:
    copied = {}
    for key, value in entry.items():
        copied[key] = list(value) if isinstance(value, tuple) else value
    return copied


def get_provider_responsibility_model() -> dict:
    """Return the explicit provider responsibility matrix for UI/tests/docs."""
    return {
        provider: _copy_provider_responsibility(entry)
        for provider, entry in PROVIDER_RESPONSIBILITY_MODEL.items()
    }


def get_provider_responsibility(provider_name: str | None) -> dict:
    """Return one provider responsibility entry, or an empty dict for unknown providers."""
    key = str(provider_name or "").strip().lower()
    entry = PROVIDER_RESPONSIBILITY_MODEL.get(key)
    return _copy_provider_responsibility(entry) if entry else {}


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
