"""
Phase 4 Remediation Tests — Environment / safety hardening.

Finding 5: environment.py must accept "development" and not silently remap to "demo".
Finding 13: Backoffice must reference sumsub_webhook in screening summary.
"""
import os
import pytest


class TestFinding5_DevelopmentEnvironment:
    """environment.py must recognise 'development' as a valid environment."""

    def test_development_in_valid_environments(self):
        from environment import VALID_ENVIRONMENTS
        assert "development" in VALID_ENVIRONMENTS, \
            "'development' not in VALID_ENVIRONMENTS — Finding 5 NOT fixed"

    def test_is_development_function_exists(self):
        from environment import is_development
        assert callable(is_development)

    def test_development_has_default_flags(self):
        from environment import _DEFAULT_FLAGS
        assert "development" in _DEFAULT_FLAGS, "No default flags for 'development'"

    def test_development_disables_mock_fallbacks(self):
        """Development should NOT enable mock fallbacks by default."""
        from environment import _DEFAULT_FLAGS
        dev_flags = _DEFAULT_FLAGS["development"]
        assert dev_flags.get("ENABLE_MOCK_FALLBACKS") is False, \
            "Development should have ENABLE_MOCK_FALLBACKS=False"

    def test_development_disables_demo_mode(self):
        """Development should NOT enable demo mode."""
        from environment import _DEFAULT_FLAGS
        dev_flags = _DEFAULT_FLAGS["development"]
        assert dev_flags.get("ENABLE_DEMO_MODE") is False


class TestFinding13_WebhookDisplayWired:
    """Backoffice must render sumsub_webhook data."""

    def test_backoffice_references_sumsub_webhook(self):
        """Backoffice HTML must contain sumsub_webhook rendering code."""
        bo_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-backoffice.html")
        if not os.path.exists(bo_path):
            bo_path = os.path.join(os.path.dirname(__file__), "..", "arie-backoffice.html")
        with open(bo_path, encoding="utf-8") as f:
            html = f.read()
        assert "sumsub_webhook" in html, "Backoffice does not reference sumsub_webhook — Finding 13 NOT fixed"
        assert "review_answer" in html, "Backoffice does not display review_answer"
        assert "rejection_labels" in html, "Backoffice does not display rejection_labels"
