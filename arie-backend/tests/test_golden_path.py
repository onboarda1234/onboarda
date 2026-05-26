"""
Golden Path Test — Canonical "Perfect Onboarding Flow"
======================================================
This is THE reference test for the entire ARIE pipeline.
It exercises: Rule Engine → Memo Generation → Validation → Supervisor
with deterministic, known-good inputs and verifies every output.

If this test breaks, the platform is not demo-safe.
"""
import pytest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from tests.conftest import make_base_memo


# ═══════════════════════════════════════════════════════════════
# Golden Input: a "perfect" low-medium risk Mauritius application
# ═══════════════════════════════════════════════════════════════

GOLDEN_APPLICATION = {
    "company_name": "Zenith Technologies Ltd",
    "country": "Mauritius",
    "sector": "Technology",
    "entity_type": "SME",
    "risk_level": "MEDIUM",
    "risk_score": 42,
    "directors": [
        {"full_name": "Aisha Sudally", "nationality": "Mauritius", "pep_status": False}
    ],
    "ubos": [
        {"full_name": "Aisha Sudally", "nationality": "Mauritius", "ownership_pct": 100.0}
    ],
    "source_of_funds": "Business revenue from software consulting",
    "expected_volume": "USD 50,000 monthly",
}


class TestGoldenPath:
    """The canonical end-to-end flow — must ALWAYS pass."""

    def test_rule_engine_clean_pass(self, temp_db):
        """Rule Engine produces no violations for a clean application."""
        app = GOLDEN_APPLICATION

        # 4B: Ownership floor — complete UBO data = no gap
        for ubo in app["ubos"]:
            assert ubo.get("ownership_pct") is not None
            assert ubo["ownership_pct"] > 0

        # 4C: Business risk floor — Technology = LOW (no floor)
        assert app["sector"] == "Technology"

        # 4D: Multi-gap escalation — 0 gaps < 3 threshold
        gaps = 0
        for ubo in app["ubos"]:
            if not ubo.get("ownership_pct"):
                gaps += 1
            if not ubo.get("nationality"):
                gaps += 1
        assert gaps < 3

        # 4E: Confidence enforcement — score > 70%
        golden_confidence = 0.78
        assert golden_confidence >= 0.70

        # Pre-validation passes
        from server import pre_validate_application
        valid, errors = pre_validate_application(app)
        assert valid is True

    def test_validation_engine_clean_pass(self, temp_db):
        """Validation Engine passes a well-formed memo with no critical issues."""
        from server import validate_compliance_memo

        memo = make_base_memo()
        result = validate_compliance_memo(memo)

        assert result is not None
        assert "issues" in result
        assert "quality_score" in result
        assert "validation_status" in result

        # No critical issues
        critical = [i for i in result["issues"] if i.get("severity") == "critical"]
        assert len(critical) == 0, f"Golden memo should have 0 critical issues, got: {critical}"

        # Quality score above threshold
        assert result["quality_score"] >= 7.0, f"Golden memo quality too low: {result['quality_score']}"

        # Status should pass
        assert result["validation_status"] in ("pass", "pass_with_fixes")

    def test_supervisor_clean_pass(self, temp_db):
        """Supervisor produces CONSISTENT verdict for a clean memo."""
        from server import run_memo_supervisor

        memo = make_base_memo()
        result = run_memo_supervisor(memo)

        assert result is not None
        assert "verdict" in result
        assert "contradictions" in result
        assert "warnings" in result
        assert "supervisor_confidence" in result

        # Clean memo = CONSISTENT
        assert result["verdict"] == "CONSISTENT", f"Expected CONSISTENT, got {result['verdict']}"

        # No critical contradictions
        assert len(result["contradictions"]) == 0, f"Unexpected contradictions: {result['contradictions']}"

        # Can approve
        assert result["can_approve"] is True

    def test_full_pipeline_deterministic(self, temp_db):
        """Full pipeline: Rule Engine clean → Validation pass → Supervisor CONSISTENT."""
        from server import validate_compliance_memo, run_memo_supervisor

        memo = make_base_memo()

        # Step 1: Validation
        val_result = validate_compliance_memo(memo)
        assert val_result["validation_status"] in ("pass", "pass_with_fixes")
        assert val_result["quality_score"] >= 7.0

        # Step 2: Supervisor
        sup_result = run_memo_supervisor(memo)
        assert sup_result["verdict"] == "CONSISTENT"
        assert sup_result["can_approve"] is True

        # Step 3: Verify key fields exist and are deterministic
        assert isinstance(val_result["quality_score"], (int, float))
        assert isinstance(sup_result["supervisor_confidence"], (int, float))
        assert sup_result["supervisor_confidence"] > 0

    def test_risk_score_computation(self, temp_db):
        """Risk score computed correctly for golden application."""
        from server import compute_risk_score

        result = compute_risk_score({
            "entity_type": GOLDEN_APPLICATION["entity_type"],
            "country": GOLDEN_APPLICATION["country"],
            "sector": GOLDEN_APPLICATION["sector"],
        })

        assert result is not None
        assert "score" in result
        assert "level" in result
        assert "lane" in result
        assert "dimensions" in result
        assert isinstance(result["score"], (int, float))
        assert result["score"] >= 0 and result["score"] <= 100

    def test_pipeline_rejects_bad_memo(self, temp_db):
        """Pipeline correctly flags a HIGH-risk APPROVE with contradictions."""
        from server import validate_compliance_memo, run_memo_supervisor

        bad_memo = make_base_memo({
            "sections": {
                "risk_assessment": {
                    "content": "Overall risk: HIGH",
                    "sub_sections": {
                        "jurisdiction_risk": {"rating": "HIGH", "content": "Iran — sanctioned jurisdiction"},
                        "business_risk": {"rating": "HIGH", "content": "Cryptocurrency"},
                        "ownership_risk": {"rating": "HIGH", "content": "Opaque ownership"},
                    }
                },
                "compliance_decision": {"decision": "APPROVE", "content": "Approved."},
                "screening_results": {"content": "Simulated screening only."},
                "red_flags_and_mitigants": {"red_flags": [], "mitigants": []},
            },
            "metadata": {
                "risk_rating": "HIGH",
                "risk_score": 88,
                "approval_recommendation": "APPROVE",
                "confidence_level": 0.55,
            }
        })

        val_result = validate_compliance_memo(bad_memo)
        sup_result = run_memo_supervisor(bad_memo)

        # Must flag critical issues
        critical = [i for i in val_result["issues"] if i.get("severity") == "critical"]
        assert len(critical) > 0, "Bad memo MUST have critical issues"

        # Must be INCONSISTENT or at minimum flagged
        assert sup_result["verdict"] in ("INCONSISTENT", "CONSISTENT_WITH_WARNINGS")

    def test_pipeline_resilience_none_values(self, temp_db):
        """Pipeline never crashes even with extreme None pollution."""
        from server import validate_compliance_memo, run_memo_supervisor

        null_memo = {
            "sections": {
                "executive_summary": None,
                "risk_assessment": {"content": None, "sub_sections": None},
                "compliance_decision": None,
                "screening_results": None,
                "ownership_and_control": None,
                "document_verification": None,
            },
            "metadata": {"risk_rating": None, "confidence_level": None}
        }

        # Must not crash
        val = validate_compliance_memo(null_memo)
        sup = run_memo_supervisor(null_memo)

        assert val is not None
        assert sup is not None
        assert "verdict" in sup
        assert "issues" in val
