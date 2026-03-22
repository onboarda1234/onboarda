"""
Sprint 1 — Integration Tests + Demo Safety Verification
Tests the full memo → validate → supervisor pipeline.
6 test cases.
"""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from tests.conftest import make_base_memo


class TestFullPipeline:
    """End-to-end: memo → validate → supervisor."""

    def test_valid_memo_passes_full_pipeline(self, temp_db):
        """A well-formed memo should pass validation and get CONSISTENT supervisor verdict."""
        from server import validate_compliance_memo, run_memo_supervisor
        memo = make_base_memo()
        val_result = validate_compliance_memo(memo)
        assert val_result["validation_status"] in ("pass", "pass_with_fixes"), \
            f"Valid memo failed validation: {val_result['issues']}"

        sup_result = run_memo_supervisor(memo)
        assert sup_result["verdict"] in ("CONSISTENT", "CONSISTENT_WITH_WARNINGS"), \
            f"Valid memo got INCONSISTENT: {sup_result['contradictions']}"

    def test_bad_memo_caught_by_pipeline(self, temp_db):
        """A contradictory memo should be caught by at least one layer."""
        from server import validate_compliance_memo, run_memo_supervisor
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH", "approval_recommendation": "APPROVE", "confidence_level": 0.3},
            "sections": {
                "compliance_decision": {"decision": "APPROVE"},
                "red_flags_and_mitigants": {"red_flags": [], "mitigants": []},
            }
        })
        val_result = validate_compliance_memo(memo)
        sup_result = run_memo_supervisor(memo)

        # At least one layer must catch the problem
        has_critical_validation = any(i["severity"] == "critical" for i in val_result["issues"])
        has_contradiction = len(sup_result["contradictions"]) > 0

        assert has_critical_validation or has_contradiction, \
            "Neither validation nor supervisor caught HIGH risk + APPROVE contradiction"

    def test_pipeline_never_crashes_on_empty_memo(self, temp_db):
        """Empty memo should produce results, not crash."""
        from server import validate_compliance_memo, run_memo_supervisor
        empty_memo = {"sections": {}, "metadata": {}}
        val_result = validate_compliance_memo(empty_memo)
        assert "validation_status" in val_result
        assert "quality_score" in val_result

        sup_result = run_memo_supervisor(empty_memo)
        assert "verdict" in sup_result

    def test_pipeline_never_crashes_on_none_values(self, temp_db):
        """Memo with None values should not raise exceptions."""
        from server import validate_compliance_memo, run_memo_supervisor
        broken_memo = {
            "sections": {
                "executive_summary": {"content": None},
                "risk_assessment": {"sub_sections": None},
                "screening_results": None,
            },
            "metadata": {"risk_rating": None, "approval_recommendation": None}
        }
        # Should not throw — graceful degradation
        try:
            val_result = validate_compliance_memo(broken_memo)
            assert "validation_status" in val_result
        except (TypeError, AttributeError):
            pytest.fail("validate_compliance_memo crashed on None values — needs defensive coding")

        try:
            sup_result = run_memo_supervisor(broken_memo)
            assert "verdict" in sup_result
        except (TypeError, AttributeError):
            pytest.fail("run_memo_supervisor crashed on None values — needs defensive coding")


class TestDemoSafety:
    """Verify the system cannot hard-crash during a demo."""

    def test_compute_risk_score_with_minimal_data(self, temp_db):
        """compute_risk_score should handle minimal/empty input."""
        from server import compute_risk_score
        minimal = {"entity_type": "", "ownership_structure": "", "country": "", "sector": "", "directors": [], "ubos": []}
        result = compute_risk_score(minimal)
        assert "score" in result
        assert "level" in result
        assert 0 <= result["score"] <= 100

    def test_compute_risk_score_with_pep(self, temp_db):
        """Risk score should increase with PEP exposure."""
        from server import compute_risk_score
        no_pep = {"entity_type": "SME", "ownership_structure": "Simple", "country": "Mauritius",
                   "sector": "Technology", "directors": [{"is_pep": "No"}], "ubos": []}
        with_pep = {"entity_type": "SME", "ownership_structure": "Simple", "country": "Mauritius",
                     "sector": "Technology", "directors": [{"is_pep": "Yes"}], "ubos": []}
        r1 = compute_risk_score(no_pep)
        r2 = compute_risk_score(with_pep)
        assert r2["score"] >= r1["score"], "PEP exposure should increase risk score"
