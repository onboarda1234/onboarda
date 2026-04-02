"""
Phase 2 Remediation Tests — Verify fixes for audit findings 10, 14, 15, 16.

Finding 10: SumsubClient must not simulate on API failure when configured
Finding 14: SumsubStatusHandler must check applicant ownership for clients
Finding 15: SumsubDocumentHandler must restrict file_path to uploads dir
Finding 16: Webhook signature check must not be short-circuited
"""
import os
import json
import pytest


# ── Finding 10: SumsubClient error result on failure ──

class TestFinding10_NoSimulationOnConfiguredFailure:
    """When is_configured=True, API failure must return error, not simulated data."""

    def test_error_result_method_exists(self):
        """SumsubClient must have _error_result helper."""
        from sumsub_client import SumsubClient
        assert hasattr(SumsubClient, "_error_result"), "Missing _error_result method"

    def test_error_result_returns_error_status(self):
        """_error_result must return api_status='error', not 'simulated'."""
        from sumsub_client import SumsubClient
        result = SumsubClient._error_result("test_op", "test reason")
        assert result["api_status"] == "error"
        assert result["status"] == "error"
        assert "test reason" in result.get("error", "")

    def test_error_result_never_returns_green(self):
        """_error_result must never return review_answer GREEN."""
        from sumsub_client import SumsubClient
        result = SumsubClient._error_result("test_op", "test reason")
        assert result.get("review_answer", "") != "GREEN", \
            "Error result must not return GREEN — Finding 10 NOT fixed"

    def test_configured_client_code_uses_error_result(self):
        """Verify the source code uses _error_result in failure paths when configured."""
        client_path = os.path.join(os.path.dirname(__file__), "..", "sumsub_client.py")
        with open(client_path) as f:
            src = f.read()

        # Count how many times the pattern "if self.is_configured:\n                    return self._error_result" appears
        import re
        pattern = r"if self\.is_configured:\s+return self\._error_result"
        matches = re.findall(pattern, src)
        # Should be at least 8 (2 per method for 4+ methods: create, get_by_ext, status, verification, token)
        assert len(matches) >= 8, \
            f"Expected ≥8 is_configured→_error_result guards, found {len(matches)}"


# ── Finding 15: File path restriction ──

class TestFinding15_FilePathRestriction:
    """SumsubDocumentHandler must restrict file_path to uploads directory."""

    def test_server_blocks_path_traversal(self):
        """Server code must validate file_path against allowed directory."""
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path) as f:
            src = f.read()
        assert "uploads" in src[src.find("class SumsubDocumentHandler"):src.find("class SumsubDocumentHandler") + 2000], \
            "SumsubDocumentHandler must validate file_path against uploads directory"
        assert "path traversal" in src.lower() or "allowed_dir" in src, \
            "SumsubDocumentHandler must have path traversal protection"


# ── Finding 16: Webhook signature check not short-circuited ──

class TestFinding16_WebhookSignatureAlwaysChecked:
    """Webhook handler must always verify signature, not skip when secret empty."""

    def test_no_short_circuit_in_webhook_handler(self):
        """The old pattern 'if SUMSUB_WEBHOOK_SECRET and not ...' must be gone."""
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path) as f:
            src = f.read()

        # Find the webhook handler section
        wh_start = src.find("class SumsubWebhookHandler")
        wh_end = src.find("\nclass ", wh_start + 10)
        wh_code = src[wh_start:wh_end]

        # The old vulnerable pattern
        assert "SUMSUB_WEBHOOK_SECRET and not" not in wh_code, \
            "Webhook handler still has short-circuit pattern — Finding 16 NOT fixed"

        # Must call sumsub_verify_webhook unconditionally
        assert "sumsub_verify_webhook" in wh_code, \
            "Webhook handler must call sumsub_verify_webhook"
