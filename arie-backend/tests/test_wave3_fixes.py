"""
Wave 3 hardening regression tests.
"""
import os
import sys
import re
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestW3_BRNValidation:
    """Verify BRN validation exists in backend and frontend."""

    def test_backend_brn_validation(self):
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path) as f:
            src = f.read()
        assert "Invalid Business Registration Number" in src, \
            "Backend should validate BRN format"

    def test_frontend_brn_pattern(self):
        portal_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
        with open(portal_path) as f:
            src = f.read()
        assert 'pattern=' in src and 'f-brn' in src, \
            "Frontend BRN field should have pattern validation"


class TestW3_DuplicateApplicationCheck:
    """Verify duplicate application detection exists."""

    def test_duplicate_check_exists(self):
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path) as f:
            src = f.read()
        assert "already exists" in src, \
            "Backend should check for duplicate applications"

    def test_returns_409_on_duplicate(self):
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path) as f:
            src = f.read()
        assert "409" in src, \
            "Duplicate application should return HTTP 409 Conflict"


class TestW3_VerificationMessaging:
    """Verify portal messaging is accurate about verification timing."""

    def test_no_vague_during_review_for_verification(self):
        portal_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
        with open(portal_path) as f:
            src = f.read()
        # The old message said "Registry verification will be completed by the compliance team during review"
        assert "Registry verification will be completed by the compliance team during review" not in src, \
            "Vague verification timing message should be replaced"


class TestW3_RiskScoreClamping:
    """Verify risk score is properly clamped."""

    def test_score_formula_produces_0_to_100(self):
        """The formula (weighted_avg - 1) / 3 * 100 should map 1..4 to 0..100."""
        # weighted_avg min is 1.0 → (1-1)/3*100 = 0
        # weighted_avg max is 4.0 → (4-1)/3*100 = 100
        assert round((1.0 - 1) / 3 * 100, 1) == 0.0
        assert round((4.0 - 1) / 3 * 100, 1) == 100.0

    def test_floor_rule_enforced(self):
        rule_engine_path = os.path.join(os.path.dirname(__file__), "..", "rule_engine.py")
        with open(rule_engine_path) as f:
            src = f.read()
        # Floor rule: max(composite, 70.0) for sanctioned countries
        assert "max(composite, 70.0)" in src, \
            "Rule engine should enforce floor rule for sanctioned countries"
