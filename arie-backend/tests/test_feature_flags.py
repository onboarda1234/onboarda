"""
Tests for feature flag configuration and client-safe flag exposure.

Prevents regressions where a flag required by the frontend is omitted
from get_client_safe_flags(), making features invisible in production.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from environment import FeatureFlags, _DEFAULT_FLAGS

# Flags that the frontend reads via FEATURE_FLAGS.<name>.
# If a new flag is consumed by the frontend, add it here.
_REQUIRED_CLIENT_FLAGS = [
    "ENABLE_DEMO_MODE",
    "ENABLE_DEMO_BANNER",
    "ENABLE_PHASE2_FEATURES",
    "ENABLE_REGULATORY_INTELLIGENCE_FULL",
    "ENABLE_MONITORING_DASHBOARD",
    "ENABLE_SAR_WORKFLOW",
    "ENABLE_AI_SUPERVISOR",
    "ENABLE_KPI_DEMO_DATA",
    "ENABLE_ROLE_SWITCHER",
    "ENABLE_DOCUMENT_AI_ANALYSIS",
]

# Flags that must NEVER be exposed to the frontend (security-sensitive).
_FORBIDDEN_CLIENT_FLAGS = [
    "ENABLE_DEBUG_ENDPOINTS",
    "ENABLE_SHORTCUT_LOGIN",
    "REQUIRE_REAL_API_KEYS",
]


class TestClientSafeFlags:
    """get_client_safe_flags() exposes exactly the right set of flags."""

    def test_all_required_flags_are_exposed(self):
        """Every flag the frontend reads must be in the safe_keys list."""
        ff = FeatureFlags(env="demo")
        client_flags = ff.get_client_safe_flags()
        missing = [f for f in _REQUIRED_CLIENT_FLAGS if f not in client_flags]
        assert missing == [], (
            f"Frontend-required flags missing from get_client_safe_flags(): {missing}"
        )

    def test_no_forbidden_flags_exposed(self):
        """Security-sensitive flags must never reach the frontend."""
        ff = FeatureFlags(env="demo")
        client_flags = ff.get_client_safe_flags()
        leaked = [f for f in _FORBIDDEN_CLIENT_FLAGS if f in client_flags]
        assert leaked == [], (
            f"Security-sensitive flags leaked to frontend: {leaked}"
        )

    def test_client_flags_are_booleans(self):
        """All client-safe flag values must be booleans."""
        ff = FeatureFlags(env="demo")
        client_flags = ff.get_client_safe_flags()
        for key, val in client_flags.items():
            assert isinstance(val, bool), f"Flag {key} has non-boolean value: {val!r}"


class TestDefaultFlagConsistency:
    """Every environment defines the same set of flags."""

    def test_all_environments_have_same_flag_keys(self):
        """Flag key set must be identical across all environments."""
        envs = list(_DEFAULT_FLAGS.keys())
        assert len(envs) >= 2, "Expected at least 2 environments"
        reference_keys = set(_DEFAULT_FLAGS[envs[0]].keys())
        for env in envs[1:]:
            env_keys = set(_DEFAULT_FLAGS[env].keys())
            extra = env_keys - reference_keys
            missing = reference_keys - env_keys
            assert extra == set(), f"{env} has extra flags vs {envs[0]}: {extra}"
            assert missing == set(), f"{env} is missing flags vs {envs[0]}: {missing}"
