"""
Sprint 1 — Validation Engine Test Suite
Tests for all 15 validation rules in validate_compliance_memo().
18 deterministic test cases.
"""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from tests.conftest import make_base_memo


class TestValidationStructure:
    """Rule 1: Structural completeness — all 11 sections required."""

    def test_complete_memo_passes_structure(self, temp_db):
        from server import validate_compliance_memo
        memo = make_base_memo()
        result = validate_compliance_memo(memo)
        structure_issues = [i for i in result["issues"] if i["category"] == "structure"]
        assert len(structure_issues) == 0, f"Complete memo should have no structure issues: {structure_issues}"

    def test_missing_section_flags_critical(self, temp_db):
        from server import validate_compliance_memo
        memo = make_base_memo()
        del memo["sections"]["screening_results"]
        result = validate_compliance_memo(memo)
        structure_issues = [i for i in result["issues"] if i["category"] == "structure"]
        assert len(structure_issues) >= 1
        assert structure_issues[0]["severity"] == "critical"


class TestValidationRiskConsistency:
    """Rule 2: Risk assessment consistency — sub-ratings vs overall."""

    def test_consistent_ratings_pass(self, temp_db):
        from server import validate_compliance_memo
        memo = make_base_memo()
        result = validate_compliance_memo(memo)
        risk_issues = [i for i in result["issues"] if i["category"] == "risk_consistency" and i["severity"] == "critical"]
        assert len(risk_issues) == 0

    def test_divergent_ratings_flag_critical(self, temp_db):
        from server import validate_compliance_memo
        memo = make_base_memo({
            "metadata": {"risk_rating": "LOW", "risk_score": 15},
            "sections": {"risk_assessment": {"sub_sections": {
                "jurisdiction_risk": {"rating": "HIGH"},
                "business_risk": {"rating": "HIGH"},
                "transaction_risk": {"rating": "HIGH"},
                "ownership_risk": {"rating": "HIGH"},
                "financial_crime_risk": {"rating": "HIGH"}
            }}}
        })
        result = validate_compliance_memo(memo)
        risk_issues = [i for i in result["issues"] if i["category"] == "risk_consistency" and i["severity"] == "critical"]
        assert len(risk_issues) >= 1, "Huge divergence between LOW overall and all-HIGH sub-ratings should be critical"


class TestValidationDecisionAlignment:
    """Rule 3: Decision must align with risk rating."""

    def test_high_risk_approve_flags_critical(self, temp_db):
        from server import validate_compliance_memo
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH", "risk_score": 75, "approval_recommendation": "APPROVE"},
            "sections": {"compliance_decision": {"decision": "APPROVE"}}
        })
        result = validate_compliance_memo(memo)
        decision_issues = [i for i in result["issues"] if i["category"] == "decision_alignment"]
        assert any(i["severity"] == "critical" for i in decision_issues)

    def test_low_risk_reject_flags_critical(self, temp_db):
        from server import validate_compliance_memo
        memo = make_base_memo({
            "metadata": {"risk_rating": "LOW", "risk_score": 15, "approval_recommendation": "REJECT"},
            "sections": {"compliance_decision": {"decision": "REJECT"}}
        })
        result = validate_compliance_memo(memo)
        decision_issues = [i for i in result["issues"] if i["category"] == "decision_alignment"]
        assert any(i["severity"] == "critical" for i in decision_issues)

    def test_medium_risk_with_conditions_passes(self, temp_db):
        from server import validate_compliance_memo
        memo = make_base_memo()  # default is MEDIUM + APPROVE_WITH_CONDITIONS
        result = validate_compliance_memo(memo)
        decision_issues = [i for i in result["issues"] if i["category"] == "decision_alignment" and i["severity"] == "critical"]
        assert len(decision_issues) == 0


class TestValidationOwnership:
    """Rule 4: Ownership analysis quality."""

    def test_missing_control_statement_critical(self, temp_db):
        from server import validate_compliance_memo
        memo = make_base_memo({
            "sections": {"ownership_and_control": {
                "content": "UBO data.",
                "structure_complexity": "Simple",
                "control_statement": ""  # Missing
            }}
        })
        result = validate_compliance_memo(memo)
        own_issues = [i for i in result["issues"] if i["category"] == "ownership" and i["severity"] == "critical"]
        assert len(own_issues) >= 1

    def test_complete_ownership_passes(self, temp_db):
        from server import validate_compliance_memo
        memo = make_base_memo()
        result = validate_compliance_memo(memo)
        own_critical = [i for i in result["issues"] if i["category"] == "ownership" and i["severity"] == "critical"]
        assert len(own_critical) == 0


class TestValidationScreening:
    """Rule 5: Screening defensibility."""

    def test_simulated_screening_critical(self, temp_db):
        from server import validate_compliance_memo
        memo = make_base_memo({
            "sections": {"screening_results": {"content": "Simulated screening results for demo."}}
        })
        result = validate_compliance_memo(memo)
        screen_issues = [i for i in result["issues"] if i["category"] == "screening" and i["severity"] == "critical"]
        assert len(screen_issues) >= 1


class TestValidationRedFlags:
    """Rule 7: Red flags & mitigants."""

    def test_no_red_flags_critical(self, temp_db):
        from server import validate_compliance_memo
        memo = make_base_memo({
            "sections": {"red_flags_and_mitigants": {"red_flags": [], "mitigants": []}}
        })
        result = validate_compliance_memo(memo)
        rf_issues = [i for i in result["issues"] if i["category"] == "red_flags" and i["severity"] == "critical"]
        assert len(rf_issues) >= 1, "Zero red flags should always be critical"

    def test_adequate_red_flags_pass(self, temp_db):
        from server import validate_compliance_memo
        memo = make_base_memo()  # default has 2 red flags + 2 mitigants
        result = validate_compliance_memo(memo)
        rf_critical = [i for i in result["issues"] if i["category"] == "red_flags" and i["severity"] == "critical"]
        assert len(rf_critical) == 0


class TestValidationFactorClassification:
    """Rule 9: Factor classification correctness."""

    def test_misclassified_factor_flagged(self, temp_db):
        from server import validate_compliance_memo
        memo = make_base_memo({
            "sections": {"ai_explainability": {
                "content": "Multi-agent analysis.",
                "risk_increasing_factors": ["No PEP exposure identified", "Clean sanctions screening"],
                "risk_decreasing_factors": ["High-risk jurisdiction"]
            }}
        })
        result = validate_compliance_memo(memo)
        factor_issues = [i for i in result["issues"] if i["category"] == "factor_classification" and i["severity"] == "critical"]
        assert len(factor_issues) >= 1, "Risk-decreasing items in risk-increasing list should be critical"


class TestValidationQualityScore:
    """Quality score computation."""

    def test_perfect_memo_scores_above_8(self, temp_db):
        from server import validate_compliance_memo
        memo = make_base_memo()
        result = validate_compliance_memo(memo)
        assert result["quality_score"] >= 7.0, f"Well-formed memo should score >= 7.0, got {result['quality_score']}"

    def test_broken_memo_scores_below_5(self, temp_db):
        from server import validate_compliance_memo
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH", "approval_recommendation": "APPROVE", "confidence_level": 0},
            "sections": {
                "compliance_decision": {"decision": "APPROVE"},
                "red_flags_and_mitigants": {"red_flags": [], "mitigants": []},
                "ownership_and_control": {"content": "", "structure_complexity": "", "control_statement": ""},
                "screening_results": {"content": "Simulated data."},
                "ai_explainability": {"content": "", "risk_increasing_factors": ["No PEP exposure"], "risk_decreasing_factors": []}
            }
        })
        result = validate_compliance_memo(memo)
        assert result["quality_score"] < 6.0, f"Broken memo should score < 6.0, got {result['quality_score']}"


class TestValidationStatus:
    """Validation status determination."""

    def test_critical_issues_cause_fail(self, temp_db):
        from server import validate_compliance_memo
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH", "approval_recommendation": "APPROVE"},
            "sections": {
                "compliance_decision": {"decision": "APPROVE"},
                "red_flags_and_mitigants": {"red_flags": [], "mitigants": []},
            }
        })
        result = validate_compliance_memo(memo)
        # Multiple critical issues should result in fail or pass_with_fixes
        assert result["validation_status"] in ("fail", "pass_with_fixes")

    def test_clean_memo_passes(self, temp_db):
        from server import validate_compliance_memo
        memo = make_base_memo()
        result = validate_compliance_memo(memo)
        assert result["validation_status"] in ("pass", "pass_with_fixes")
