"""
Floor-rule memo validation false positive fix — test suite.

Validates that validation_engine.risk_consistency correctly distinguishes
legitimate risk elevation (floor rules, screening hits) from genuine
memo/risk contradictions.

12 tests covering:
- Baseline non-elevated scenarios (unchanged behaviour)
- Elevated scenarios (wider thresholds with explicit messaging)
- Extreme gaps still caught even when elevated
- Safe fallback when original_risk_level is missing
- Explicit validation messaging for elevated cases
- Migration v2.22 schema test
"""
import pytest
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from tests.conftest import make_base_memo


# ── Helpers ──

def _run_validation(memo):
    """Run validation and return the full result."""
    from validation_engine import validate_compliance_memo
    return validate_compliance_memo(memo)


def _risk_issues(result, severity=None):
    """Filter risk_consistency issues, optionally by severity."""
    return [
        i for i in result["issues"]
        if i["category"] == "risk_consistency"
        and (severity is None or i["severity"] == severity)
    ]


# ══════════════════════════════════════════════════════════
# BASELINE: Non-elevated applications (thresholds unchanged)
# ══════════════════════════════════════════════════════════

class TestNonElevatedBaseline:
    """Verify that non-elevated applications use original 1.5/0.8 thresholds."""

    def test_normal_low_consistent_passes(self, temp_db):
        """LOW overall with all-LOW subs → full pass, no risk_consistency issues."""
        memo = make_base_memo({
            "metadata": {"risk_rating": "LOW", "risk_score": 20, "original_risk_level": "LOW"},
            "sections": {"risk_assessment": {"sub_sections": {
                "jurisdiction_risk": {"rating": "LOW"},
                "business_risk": {"rating": "LOW"},
                "transaction_risk": {"rating": "LOW"},
                "ownership_risk": {"rating": "LOW"},
                "financial_crime_risk": {"rating": "LOW"},
            }}}
        })
        result = _run_validation(memo)
        critical = _risk_issues(result, "critical")
        assert len(critical) == 0, f"LOW/LOW should not fire critical: {critical}"

    def test_normal_high_consistent_passes(self, temp_db):
        """HIGH overall with mostly-HIGH subs → no critical issues."""
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH", "risk_score": 72,
                         "original_risk_level": "HIGH",
                         "approval_recommendation": "APPROVE_WITH_CONDITIONS"},
            "sections": {
                "risk_assessment": {"sub_sections": {
                    "jurisdiction_risk": {"rating": "HIGH"},
                    "business_risk": {"rating": "HIGH"},
                    "transaction_risk": {"rating": "HIGH"},
                    "ownership_risk": {"rating": "MEDIUM"},
                    "financial_crime_risk": {"rating": "HIGH"},
                }},
                "compliance_decision": {"decision": "APPROVE_WITH_CONDITIONS"},
            }
        })
        result = _run_validation(memo)
        critical = _risk_issues(result, "critical")
        assert len(critical) == 0, f"HIGH/HIGH should not fire critical: {critical}"

    def test_genuine_contradiction_critical(self, temp_db):
        """VERY_HIGH overall with all-LOW subs, no elevation → CRITICAL (genuine bug)."""
        memo = make_base_memo({
            "metadata": {"risk_rating": "VERY_HIGH", "risk_score": 85,
                         "original_risk_level": "VERY_HIGH",
                         "approval_recommendation": "REJECT"},
            "sections": {
                "risk_assessment": {"sub_sections": {
                    "jurisdiction_risk": {"rating": "LOW"},
                    "business_risk": {"rating": "LOW"},
                    "transaction_risk": {"rating": "LOW"},
                    "ownership_risk": {"rating": "LOW"},
                    "financial_crime_risk": {"rating": "LOW"},
                }},
                "compliance_decision": {"decision": "REJECT"},
            }
        })
        result = _run_validation(memo)
        critical = _risk_issues(result, "critical")
        assert len(critical) >= 1, "VERY_HIGH with all-LOW subs and no elevation must be CRITICAL"

    def test_non_elevated_thresholds_unchanged(self, temp_db):
        """Verify exact 1.5 threshold still triggers critical for non-elevated.

        HIGH(3) vs all-LOW(avg 1.0) = divergence 2.0 > 1.5 → critical.
        """
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH", "risk_score": 70,
                         "original_risk_level": "HIGH",
                         "approval_recommendation": "APPROVE_WITH_CONDITIONS"},
            "sections": {
                "risk_assessment": {"sub_sections": {
                    "jurisdiction_risk": {"rating": "LOW"},
                    "business_risk": {"rating": "LOW"},
                    "transaction_risk": {"rating": "LOW"},
                    "ownership_risk": {"rating": "LOW"},
                    "financial_crime_risk": {"rating": "LOW"},
                }},
                "compliance_decision": {"decision": "APPROVE_WITH_CONDITIONS"},
            }
        })
        result = _run_validation(memo)
        critical = _risk_issues(result, "critical")
        assert len(critical) >= 1, "Non-elevated HIGH vs all-LOW (divergence 2.0) must remain CRITICAL"


# ══════════════════════════════════════════════════════════
# ELEVATED: Floor-rule / screening elevation scenarios
# ══════════════════════════════════════════════════════════

class TestElevatedRiskConsistency:
    """Verify that legitimate elevation widens thresholds with explicit messaging."""

    def test_elevated_high_from_medium_no_critical(self, temp_db):
        """HIGH overall elevated from MEDIUM, avg subs ~1.6 → no critical (the fix).

        This is the core false positive scenario: floor rules push overall to HIGH(3)
        but sub-sections average around 1.6. Divergence = 1.4 < 2.2 → tolerated.
        """
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH", "risk_score": 70,
                         "original_risk_level": "MEDIUM",
                         "approval_recommendation": "APPROVE_WITH_CONDITIONS"},
            "sections": {
                "risk_assessment": {"sub_sections": {
                    "jurisdiction_risk": {"rating": "LOW"},
                    "business_risk": {"rating": "MEDIUM"},
                    "transaction_risk": {"rating": "HIGH"},
                    "ownership_risk": {"rating": "LOW"},
                    "financial_crime_risk": {"rating": "LOW"},
                }},
                "compliance_decision": {"decision": "APPROVE_WITH_CONDITIONS"},
            }
        })
        result = _run_validation(memo)
        critical = _risk_issues(result, "critical")
        assert len(critical) == 0, f"Elevated HIGH from MEDIUM should NOT fire critical: {critical}"

    def test_elevated_very_high_from_medium_tolerated(self, temp_db):
        """VERY_HIGH overall elevated from MEDIUM, avg subs = 2.0 → warning not critical.

        Divergence = 4 - 2.0 = 2.0, within elevated threshold of 2.2 → warning.
        """
        memo = make_base_memo({
            "metadata": {"risk_rating": "VERY_HIGH", "risk_score": 85,
                         "original_risk_level": "MEDIUM",
                         "approval_recommendation": "REJECT"},
            "sections": {
                "risk_assessment": {"sub_sections": {
                    "jurisdiction_risk": {"rating": "MEDIUM"},
                    "business_risk": {"rating": "MEDIUM"},
                    "transaction_risk": {"rating": "HIGH"},
                    "ownership_risk": {"rating": "MEDIUM"},
                    "financial_crime_risk": {"rating": "LOW"},
                }},
                "compliance_decision": {"decision": "REJECT"},
            }
        })
        result = _run_validation(memo)
        critical = _risk_issues(result, "critical")
        assert len(critical) == 0, f"Elevated VERY_HIGH from MEDIUM with avg 2.0 should NOT be critical: {critical}"

    def test_elevated_extreme_gap_still_critical(self, temp_db):
        """VERY_HIGH elevated from LOW with all-LOW subs → still CRITICAL.

        Divergence = 4 - 1.0 = 3.0 > 2.2 → critical even with elevation.
        """
        memo = make_base_memo({
            "metadata": {"risk_rating": "VERY_HIGH", "risk_score": 85,
                         "original_risk_level": "LOW",
                         "approval_recommendation": "REJECT"},
            "sections": {
                "risk_assessment": {"sub_sections": {
                    "jurisdiction_risk": {"rating": "LOW"},
                    "business_risk": {"rating": "LOW"},
                    "transaction_risk": {"rating": "LOW"},
                    "ownership_risk": {"rating": "LOW"},
                    "financial_crime_risk": {"rating": "LOW"},
                }},
                "compliance_decision": {"decision": "REJECT"},
            }
        })
        result = _run_validation(memo)
        critical = _risk_issues(result, "critical")
        assert len(critical) >= 1, "VERY_HIGH with all-LOW subs must remain CRITICAL even when elevated"
        # Verify the message mentions elevation context
        assert "elevation" in critical[0]["description"].lower()


# ══════════════════════════════════════════════════════════
# EDGE CASES & MESSAGING
# ══════════════════════════════════════════════════════════

class TestElevationEdgeCases:
    """Edge cases: missing original_risk_level, same level, explicit messaging."""

    def test_no_original_level_uses_default_threshold(self, temp_db):
        """Missing original_risk_level → non-elevated path (safe fallback)."""
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH", "risk_score": 70,
                         "approval_recommendation": "APPROVE_WITH_CONDITIONS"},
            "sections": {
                "risk_assessment": {"sub_sections": {
                    "jurisdiction_risk": {"rating": "LOW"},
                    "business_risk": {"rating": "LOW"},
                    "transaction_risk": {"rating": "LOW"},
                    "ownership_risk": {"rating": "LOW"},
                    "financial_crime_risk": {"rating": "LOW"},
                }},
                "compliance_decision": {"decision": "APPROVE_WITH_CONDITIONS"},
            }
        })
        # Remove original_risk_level entirely
        memo["metadata"].pop("original_risk_level", None)
        result = _run_validation(memo)
        critical = _risk_issues(result, "critical")
        # Should use default 1.5 threshold → divergence 2.0 > 1.5 → critical
        assert len(critical) >= 1, "Missing original_risk_level must use default threshold (1.5)"

    def test_same_level_no_relaxation(self, temp_db):
        """original_risk_level == risk_rating → non-elevated, no threshold relaxation."""
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH", "risk_score": 70,
                         "original_risk_level": "HIGH",
                         "approval_recommendation": "APPROVE_WITH_CONDITIONS"},
            "sections": {
                "risk_assessment": {"sub_sections": {
                    "jurisdiction_risk": {"rating": "LOW"},
                    "business_risk": {"rating": "LOW"},
                    "transaction_risk": {"rating": "LOW"},
                    "ownership_risk": {"rating": "LOW"},
                    "financial_crime_risk": {"rating": "LOW"},
                }},
                "compliance_decision": {"decision": "APPROVE_WITH_CONDITIONS"},
            }
        })
        result = _run_validation(memo)
        critical = _risk_issues(result, "critical")
        assert len(critical) >= 1, "Same-level (HIGH=HIGH) must NOT relax thresholds"

    def test_elevated_info_message_present(self, temp_db):
        """When elevation is tolerated, an info-level message must be emitted."""
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH", "risk_score": 70,
                         "original_risk_level": "MEDIUM",
                         "approval_recommendation": "APPROVE_WITH_CONDITIONS"},
            "sections": {
                "risk_assessment": {"sub_sections": {
                    "jurisdiction_risk": {"rating": "MEDIUM"},
                    "business_risk": {"rating": "MEDIUM"},
                    "transaction_risk": {"rating": "MEDIUM"},
                    "ownership_risk": {"rating": "MEDIUM"},
                    "financial_crime_risk": {"rating": "MEDIUM"},
                }},
                "compliance_decision": {"decision": "APPROVE_WITH_CONDITIONS"},
            }
        })
        result = _run_validation(memo)
        info_msgs = _risk_issues(result, "info")
        assert len(info_msgs) >= 1, "Tolerated elevation must emit an info-level message"
        assert "elevation" in info_msgs[0]["description"].lower()
        assert "MEDIUM" in info_msgs[0]["description"]  # mentions original level

    def test_elevated_warning_includes_context(self, temp_db):
        """When elevated divergence triggers warning, message must mention elevation."""
        memo = make_base_memo({
            "metadata": {"risk_rating": "VERY_HIGH", "risk_score": 90,
                         "original_risk_level": "HIGH",
                         "approval_recommendation": "REJECT"},
            "sections": {
                "risk_assessment": {"sub_sections": {
                    "jurisdiction_risk": {"rating": "MEDIUM"},
                    "business_risk": {"rating": "MEDIUM"},
                    "transaction_risk": {"rating": "HIGH"},
                    "ownership_risk": {"rating": "MEDIUM"},
                    "financial_crime_risk": {"rating": "MEDIUM"},
                }},
                "compliance_decision": {"decision": "REJECT"},
            }
        })
        result = _run_validation(memo)
        warnings = _risk_issues(result, "warning")
        # VERY_HIGH(4) vs avg 2.2 → divergence 1.8, elevated from HIGH
        # 1.8 > 1.2 warning threshold → warning
        elevation_warnings = [w for w in warnings if "elevation" in w["description"].lower()]
        assert len(elevation_warnings) >= 1, f"Elevated warning must mention elevation context: {warnings}"


# ══════════════════════════════════════════════════════════
# SCHEMA: Migration v2.22
# ══════════════════════════════════════════════════════════

class TestMigrationV222:
    """Verify migration v2.22 adds risk_escalations column."""

    def test_migration_v2_22_adds_column(self, temp_db):
        """risk_escalations column must exist after migration with correct default."""
        import sqlite3
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        cols = {r[1] for r in conn.execute("PRAGMA table_info(applications)").fetchall()}
        assert "risk_escalations" in cols, f"risk_escalations column missing. Columns: {cols}"
        # Verify default value
        col_info = [r for r in conn.execute("PRAGMA table_info(applications)").fetchall() if r[1] == "risk_escalations"]
        assert col_info[0][4] == "'[]'", f"Default should be '[]', got: {col_info[0][4]}"
        conn.close()
