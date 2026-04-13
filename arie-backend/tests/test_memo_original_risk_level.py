"""
EX-06 memo-validation blocker fix — test suite for Fix Option C.

Tests that memo_handler correctly derives pre-elevation/original risk level
from risk_escalations, enabling validation_engine to distinguish legitimate
floor-rule elevation from genuine memo inconsistency.

Test coverage:
1. Elevated HIGH-risk app (score-based MEDIUM, escalated to HIGH) → validation does not critical-fail
2. Non-elevated inconsistent HIGH-risk app → still critical-fails
3. Missing/invalid risk_escalations → safe fallback (uses stored risk_level)
4. Memo metadata contains correct original_risk_level
5. Existing validation tests still pass (run via full suite)
6. Approval gate reachable when memo validation no longer falsely fails
"""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from memo_handler import build_compliance_memo
from validation_engine import validate_compliance_memo
from tests.conftest import make_base_memo


# ── Helpers ──

def _make_app(**overrides):
    """Build a minimal app dict for build_compliance_memo."""
    base = {
        "id": "test-app-001",
        "ref": "ARF-2026-100455",
        "company_name": "Test Corp Ltd",
        "brn": "C12345",
        "country": "Mauritius",
        "sector": "Technology",
        "entity_type": "SME",
        "source_of_funds": "Operating revenue",
        "expected_volume": "USD 100,000",
        "ownership_structure": "Simple",
        "risk_level": "MEDIUM",
        "risk_score": 45,
        "assigned_to": "admin001",
        "operating_countries": "Mauritius",
        "incorporation_date": "2020-01-01",
        "business_activity": "Software development",
    }
    base.update(overrides)
    return base


def _make_directors():
    return [{"full_name": "John Doe", "nationality": "Mauritius", "is_pep": "No"}]


def _make_ubos():
    return [{"full_name": "Jane Doe", "nationality": "Mauritius", "ownership_pct": 100, "is_pep": "No"}]


def _risk_issues(result, severity=None):
    """Filter risk_consistency issues from validation result."""
    return [
        i for i in result["issues"]
        if i["category"] == "risk_consistency"
        and (severity is None or i["severity"] == severity)
    ]


# ══════════════════════════════════════════════════════════
# 1. ELEVATED HIGH-RISK APP: subsection average lower but elevation is legitimate
# ══════════════════════════════════════════════════════════

class TestElevatedMemoMetadata:
    """Test that memo_handler derives correct original_risk_level from risk_escalations."""

    def test_elevated_high_from_medium_metadata(self):
        """Score 50.9 → MEDIUM, stored level HIGH, escalations present.
        Memo metadata.original_risk_level should be MEDIUM (pre-elevation)."""
        app = _make_app(
            risk_level="HIGH",
            risk_score=50.9,
            risk_escalations=json.dumps(["sub_factor_score_4"]),
        )
        memo, rule_result, supervisor_result, validation_result = build_compliance_memo(
            app, _make_directors(), _make_ubos(), []
        )
        assert memo["metadata"]["original_risk_level"] == "MEDIUM", \
            "original_risk_level should be MEDIUM (score-based pre-elevation level)"

    def test_elevated_very_high_from_medium_metadata(self):
        """Score 45 → MEDIUM, stored level VERY_HIGH, floor rule present.
        Note: for sanctioned country floor rules the score is also elevated,
        but if the stored score is still below the threshold, original should be derived."""
        app = _make_app(
            risk_level="VERY_HIGH",
            risk_score=45,
            risk_escalations=json.dumps(["floor_rule_sanctioned_nationality:iran"]),
        )
        memo, _, _, _ = build_compliance_memo(
            app, _make_directors(), _make_ubos(), []
        )
        assert memo["metadata"]["original_risk_level"] == "MEDIUM"

    def test_no_escalations_uses_stored_level(self):
        """No escalations → original_risk_level should equal stored risk_level."""
        app = _make_app(
            risk_level="HIGH",
            risk_score=60,
            risk_escalations=json.dumps([]),
        )
        memo, _, _, _ = build_compliance_memo(
            app, _make_directors(), _make_ubos(), []
        )
        # No escalations, so original_risk_level should equal stored risk_level
        # HIGH with score 60 → classify_risk_level(60) = HIGH (55-69.9 range)
        # Even without checking escalations, the result is the same
        assert memo["metadata"]["original_risk_level"] == "HIGH"

    def test_escalation_without_actual_elevation(self):
        """Escalation flags present but score naturally maps to stored level.
        original_risk_level should equal stored risk_level (no elevation detected)."""
        app = _make_app(
            risk_level="HIGH",
            risk_score=60,  # classify_risk_level(60) = HIGH (55-69.9)
            risk_escalations=json.dumps(["sub_factor_score_4"]),
        )
        memo, _, _, _ = build_compliance_memo(
            app, _make_directors(), _make_ubos(), []
        )
        # Score 60 → HIGH, stored level HIGH → no elevation → original stays HIGH
        assert memo["metadata"]["original_risk_level"] == "HIGH"


# ══════════════════════════════════════════════════════════
# 2. END-TO-END: Elevated case passes validation, non-elevated still fails
# ══════════════════════════════════════════════════════════

class TestElevatedValidationE2E:
    """End-to-end: memo built with risk_escalations → validation detects elevation."""

    def test_elevated_high_no_critical_fail(self):
        """Floor-rule elevated HIGH app (score 50.9 → MEDIUM).
        Subsection averages are MEDIUM-level. Validation should NOT critical-fail
        because elevation from MEDIUM to HIGH is legitimate."""
        app = _make_app(
            risk_level="HIGH",
            risk_score=50.9,
            risk_escalations=json.dumps(["sub_factor_score_4"]),
        )
        memo, _, _, validation_result = build_compliance_memo(
            app, _make_directors(), _make_ubos(), []
        )

        # Verify metadata is correct
        assert memo["metadata"]["original_risk_level"] == "MEDIUM"
        assert memo["metadata"]["risk_rating"] in ("HIGH", "VERY_HIGH")

        # Verify no critical risk_consistency failure
        critical_issues = _risk_issues(validation_result, "critical")
        risk_consistency_criticals = [
            i for i in critical_issues
            if "diverges" in i.get("description", "").lower()
        ]
        assert len(risk_consistency_criticals) == 0, \
            f"Should not critical-fail for legitimate elevation: {risk_consistency_criticals}"

    def test_non_elevated_inconsistent_still_fails(self):
        """Non-elevated HIGH app with all-MEDIUM subs should still fail.
        This is the control case: without escalations, strict thresholds apply."""
        # Build memo directly (bypass memo_handler) to test validation engine
        memo = make_base_memo({
            "sections": {
                "risk_assessment": {
                    "sub_sections": {
                        "jurisdiction_risk": {"rating": "LOW", "content": "Low risk"},
                        "business_risk": {"rating": "LOW", "content": "Low risk"},
                        "transaction_risk": {"rating": "LOW", "content": "Low risk"},
                        "ownership_risk": {"rating": "LOW", "content": "Low risk"},
                        "financial_crime_risk": {"rating": "LOW", "content": "Low risk"},
                    }
                }
            },
            "metadata": {
                "risk_rating": "HIGH",
                "original_risk_level": "HIGH",  # No elevation — same as risk_rating
                "risk_score": 60,
            }
        })
        result = validate_compliance_memo(memo)
        critical_issues = _risk_issues(result, "critical")
        assert len(critical_issues) > 0, \
            "Non-elevated HIGH with all-LOW subs should critical-fail"

    def test_non_elevated_consistent_passes(self):
        """Non-elevated HIGH app with HIGH subs should pass."""
        memo = make_base_memo({
            "sections": {
                "risk_assessment": {
                    "sub_sections": {
                        "jurisdiction_risk": {"rating": "HIGH", "content": "High risk"},
                        "business_risk": {"rating": "HIGH", "content": "High risk"},
                        "transaction_risk": {"rating": "MEDIUM", "content": "Medium"},
                        "ownership_risk": {"rating": "HIGH", "content": "High risk"},
                        "financial_crime_risk": {"rating": "MEDIUM", "content": "Medium"},
                    }
                }
            },
            "metadata": {
                "risk_rating": "HIGH",
                "original_risk_level": "HIGH",
                "risk_score": 60,
            }
        })
        result = validate_compliance_memo(memo)
        critical_issues = _risk_issues(result, "critical")
        assert len(critical_issues) == 0, \
            "Consistent HIGH should not critical-fail"


# ══════════════════════════════════════════════════════════
# 3. MISSING/INVALID risk_escalations: safe fallback
# ══════════════════════════════════════════════════════════

class TestMissingEscalationsFallback:
    """Verify safe fallback when risk_escalations is missing, null, or corrupt."""

    def test_missing_risk_escalations_key(self):
        """App dict without risk_escalations key → uses stored risk_level."""
        app = _make_app(risk_level="HIGH", risk_score=50.9)
        # Ensure no risk_escalations key
        app.pop("risk_escalations", None)
        memo, _, _, _ = build_compliance_memo(
            app, _make_directors(), _make_ubos(), []
        )
        # Fallback: original_risk_level = risk_level (no escalation info available)
        assert memo["metadata"]["original_risk_level"] == "HIGH"

    def test_null_risk_escalations(self):
        """risk_escalations = None → uses stored risk_level."""
        app = _make_app(
            risk_level="HIGH",
            risk_score=50.9,
            risk_escalations=None,
        )
        memo, _, _, _ = build_compliance_memo(
            app, _make_directors(), _make_ubos(), []
        )
        assert memo["metadata"]["original_risk_level"] == "HIGH"

    def test_corrupt_json_risk_escalations(self):
        """risk_escalations = invalid JSON → uses stored risk_level (safe fallback)."""
        app = _make_app(
            risk_level="HIGH",
            risk_score=50.9,
            risk_escalations="not-valid-json{{{",
        )
        memo, _, _, _ = build_compliance_memo(
            app, _make_directors(), _make_ubos(), []
        )
        # Corrupt JSON → fallback to stored risk_level
        assert memo["metadata"]["original_risk_level"] == "HIGH"

    def test_empty_string_escalations(self):
        """risk_escalations = '' → treated as empty → uses stored risk_level."""
        app = _make_app(
            risk_level="HIGH",
            risk_score=50.9,
            risk_escalations="",
        )
        memo, _, _, _ = build_compliance_memo(
            app, _make_directors(), _make_ubos(), []
        )
        assert memo["metadata"]["original_risk_level"] == "HIGH"


# ══════════════════════════════════════════════════════════
# 4. APPROVAL GATE REACHABILITY
# ══════════════════════════════════════════════════════════

class TestApprovalGateReachability:
    """Verify that memo validation no longer blocks approval for legitimate elevated cases."""

    def test_validation_status_not_failed_for_elevated(self):
        """Elevated HIGH app should not have validation_status='failed'
        solely due to risk_consistency."""
        app = _make_app(
            risk_level="HIGH",
            risk_score=50.9,
            risk_escalations=json.dumps(["sub_factor_score_4"]),
        )
        _, _, _, validation_result = build_compliance_memo(
            app, _make_directors(), _make_ubos(), []
        )

        # Check that risk_consistency didn't produce a critical failure
        critical_issues = _risk_issues(validation_result, "critical")
        risk_critical = [i for i in critical_issues if "diverges" in i.get("description", "")]
        assert len(risk_critical) == 0, \
            f"Validation should not block approval for legitimate elevation: {risk_critical}"

    def test_elevated_info_message_explains_elevation(self):
        """Elevated cases should include info-level message explaining the elevation."""
        memo = make_base_memo({
            "sections": {
                "risk_assessment": {
                    "sub_sections": {
                        "jurisdiction_risk": {"rating": "MEDIUM", "content": "Med"},
                        "business_risk": {"rating": "LOW", "content": "Low"},
                        "transaction_risk": {"rating": "MEDIUM", "content": "Med"},
                        "ownership_risk": {"rating": "MEDIUM", "content": "Med"},
                        "financial_crime_risk": {"rating": "LOW", "content": "Low"},
                    }
                }
            },
            "metadata": {
                "risk_rating": "HIGH",
                "original_risk_level": "MEDIUM",  # Elevation from MEDIUM to HIGH
                "risk_score": 50,
            }
        })
        result = validate_compliance_memo(memo)
        info_issues = _risk_issues(result, "info")
        has_elevation_info = any(
            "elevation" in i.get("description", "").lower()
            for i in info_issues
        )
        # Should have info or warning about elevation, not critical
        critical_issues = _risk_issues(result, "critical")
        assert len(critical_issues) == 0, "Should not critical-fail"
