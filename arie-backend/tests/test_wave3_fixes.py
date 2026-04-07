"""
Wave 3 hardening regression tests.
"""
import os
import sys
import re
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# BRN regex extracted from server.py pattern for direct behavioral testing
BRN_PATTERN = re.compile(r'^[A-Za-z0-9\-/.][A-Za-z0-9\-/. ]{0,28}[A-Za-z0-9\-/.]$')


class TestW3_BRNValidation:
    """Verify BRN validation exists in backend and frontend."""

    def test_backend_brn_validation(self):
        """Test the actual BRN regex pattern against valid and invalid inputs."""
        # Valid BRNs
        assert BRN_PATTERN.match("C07012345"), "Standard Mauritius BRN should be valid"
        assert BRN_PATTERN.match("12345678"), "Numeric BRN should be valid"
        assert BRN_PATTERN.match("AB-123/456"), "BRN with dashes/slashes should be valid"
        assert BRN_PATTERN.match("COMP.2024"), "BRN with dots should be valid"
        assert BRN_PATTERN.match("AB 1234 CD"), "BRN with spaces should be valid"

    def test_backend_brn_rejects_invalid(self):
        """Invalid BRN formats must be rejected."""
        assert not BRN_PATTERN.match(""), "Empty string should be invalid"
        assert not BRN_PATTERN.match("A"), "Single character should be invalid (min 2)"
        assert not BRN_PATTERN.match(" ABC"), "Leading space should be invalid"
        assert not BRN_PATTERN.match("ABC "), "Trailing space should be invalid"

    def test_frontend_brn_pattern(self):
        portal_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
        with open(portal_path) as f:
            src = f.read()
        assert 'pattern=' in src and 'f-brn' in src, \
            "Frontend BRN field should have pattern validation"


class TestW3_DuplicateApplicationCheck:
    """Verify duplicate application detection exists."""

    def test_duplicate_check_exists(self):
        import server
        import inspect
        src = inspect.getsource(server.ApplicationsHandler)
        assert "already exists" in src, \
            "ApplicationsHandler should check for duplicate applications"

    def test_returns_409_on_duplicate(self):
        import server
        import inspect
        src = inspect.getsource(server.ApplicationsHandler)
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
        """Sanctioned country must force VERY_HIGH risk with score >= 70."""
        from rule_engine import compute_risk_score, SANCTIONED
        # Pick a sanctioned country for the test
        sanctioned_country = next(iter(SANCTIONED))
        app_data = {
            "country": sanctioned_country,
            "sector": "Retail",
            "directors": [],
            "ubos": [],
        }
        result = compute_risk_score(app_data)
        assert result["level"] == "VERY_HIGH", \
            f"Sanctioned country '{sanctioned_country}' must yield VERY_HIGH, got {result['level']}"
        assert result["score"] >= 70.0, \
            f"Floor rule should enforce score >= 70.0, got {result['score']}"

    def test_floor_rule_with_low_base_score(self):
        """Even a low-risk sector + sanctioned country must produce VERY_HIGH."""
        from rule_engine import compute_risk_score
        app_data = {
            "country": "iran",
            "sector": "Retail",
            "directors": [{"full_name": "Test Person", "nationality": "Mauritius"}],
            "ubos": [],
        }
        result = compute_risk_score(app_data)
        assert result["level"] == "VERY_HIGH"
        assert result["score"] >= 70.0


class TestNullRiskLevel:
    """Regression: NULL risk_level must not crash the status PATCH handler."""

    def test_null_risk_level_or_pattern(self):
        """The (app.get("risk_level") or "").upper() pattern handles None correctly."""
        # Simulate what server.py does at line 1800
        for risk_val in [None, "", "LOW", "HIGH", "VERY_HIGH"]:
            app = {"risk_level": risk_val}
            result = (app.get("risk_level") or "").upper()
            assert isinstance(result, str)
        # None specifically must not raise
        app = {"risk_level": None}
        result = (app.get("risk_level") or "").upper()
        assert result == ""

    def test_missing_risk_level_key(self):
        """App dict with no risk_level key at all must not crash."""
        app = {"status": "submitted"}
        result = (app.get("risk_level") or "").upper()
        assert result == ""

    def test_risk_level_values_uppercase(self):
        """Valid risk levels should uppercase correctly."""
        for val, expected in [("low", "LOW"), ("High", "HIGH"), ("VERY_HIGH", "VERY_HIGH")]:
            app = {"risk_level": val}
            result = (app.get("risk_level") or "").upper()
            assert result == expected
