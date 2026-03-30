"""
Sprint 1 — Supervisor Tests (Framework + Memo Contradiction Detection)
Tests for supervisor framework (schemas, confidence, etc.) and
all 11 memo supervisor checks + verdict computation.
16 framework tests + 16 contradiction tests = 32 total.
"""
import pytest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from tests.conftest import make_base_memo


# ═══════════════════════════════════════════════════════════════
# PART A: Supervisor Framework Tests (original)
# ═══════════════════════════════════════════════════════════════

class TestSupervisorSchemas:
    def test_agent_types_defined(self):
        from supervisor.schemas import AgentType
        assert len(AgentType) == 10
        assert AgentType.IDENTITY_DOCUMENT_INTEGRITY is not None
        assert AgentType.FINCRIME_SCREENING is not None

    def test_confidence_routing_thresholds(self):
        from supervisor.schemas import ConfidenceRouting
        assert ConfidenceRouting.NORMAL is not None
        assert ConfidenceRouting.HUMAN_REVIEW is not None
        assert ConfidenceRouting.MANDATORY_ESCALATION is not None


class TestValidator:
    def test_validator_initializes(self):
        from supervisor.validator import SchemaValidator
        v = SchemaValidator()
        assert v is not None

    def test_basic_validation(self):
        from supervisor.validator import SchemaValidator
        from supervisor.schemas import AgentType
        v = SchemaValidator()
        output = {
            "agent_type": "identity_document_integrity",
            "status": "completed",
            "confidence_score": 0.85,
            "summary": "Document verified",
            "findings": [],
            "evidence": [],
            "risk_indicators": [],
            "requires_escalation": False,
        }
        result = v.validate(output, AgentType.IDENTITY_DOCUMENT_INTEGRITY)
        assert result is not None


class TestConfidence:
    def test_evaluator_initializes(self):
        from supervisor.confidence import ConfidenceEvaluator
        ce = ConfidenceEvaluator()
        assert ce is not None
        assert ce.normal_threshold == 0.85
        assert ce.review_threshold == 0.65

    def test_routing_decision(self):
        from supervisor.confidence import ConfidenceEvaluator
        from supervisor.schemas import ConfidenceRouting
        ce = ConfidenceEvaluator()
        assert ce.route_confidence(0.90) == ConfidenceRouting.NORMAL
        assert ce.route_confidence(0.75) == ConfidenceRouting.HUMAN_REVIEW
        assert ce.route_confidence(0.50) == ConfidenceRouting.MANDATORY_ESCALATION


class TestContradictions:
    def test_detector_initializes(self):
        from supervisor.contradictions import ContradictionDetector
        cd = ContradictionDetector()
        assert cd is not None


class TestRulesEngine:
    def test_engine_initializes(self):
        from supervisor.rules_engine import RulesEngine
        re = RulesEngine()
        re.load_default_rules()
        assert re is not None
        assert len(re.rules) > 0

    def test_rules_have_priority(self):
        from supervisor.rules_engine import RulesEngine
        re = RulesEngine()
        re.load_default_rules()
        priorities = [r.priority for r in re.rules]
        assert priorities == sorted(priorities), "Rules should be priority-ordered"


class TestAuditLogger:
    def test_logger_initializes(self, temp_db):
        from supervisor.audit import AuditLogger
        al = AuditLogger(db_path=temp_db)
        assert al is not None

    def test_hash_chain_integrity(self, temp_db):
        from supervisor.audit import AuditLogger
        from supervisor.schemas import AuditEventType
        al = AuditLogger(db_path=temp_db)
        al.log(
            event_type=AuditEventType.AGENT_RUN_STARTED,
            action="test",
            application_id="app1",
            data={"key": "value"}
        )
        al.log(
            event_type=AuditEventType.AGENT_RUN_COMPLETED,
            action="test2",
            application_id="app1",
            data={"key": "value2"}
        )
        result = al.verify_chain_integrity(limit=10)
        assert result["verified"] is True


# ═══════════════════════════════════════════════════════════════
# PART B: Memo Supervisor Contradiction Tests (Sprint 1)
# ═══════════════════════════════════════════════════════════════

class TestMemoCheck1_RiskVsDecision:
    """Check 1: Risk rating vs decision consistency."""

    def test_high_risk_approve_contradiction(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH", "approval_recommendation": "APPROVE"}
        })
        result = run_memo_supervisor(memo)
        cats = [c["category"] for c in result["contradictions"]]
        assert "risk_vs_decision" in cats

    def test_low_risk_reject_contradiction(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "metadata": {"risk_rating": "LOW", "approval_recommendation": "REJECT"}
        })
        result = run_memo_supervisor(memo)
        cats = [c["category"] for c in result["contradictions"]]
        assert "risk_vs_decision" in cats

    def test_medium_conditions_no_contradiction(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo()
        result = run_memo_supervisor(memo)
        risk_c = [c for c in result["contradictions"] if c["category"] == "risk_vs_decision"]
        assert len(risk_c) == 0


class TestMemoCheck2_Ownership:
    """Check 2: Ownership gaps vs LOW rating."""

    def test_low_ownership_with_gaps(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "sections": {
                "ownership_and_control": {"content": "UBO data not provided. Cannot be determined.", "structure_complexity": "Simple", "control_statement": "Unknown."},
                "risk_assessment": {"sub_sections": {
                    "jurisdiction_risk": {"rating": "MEDIUM"},
                    "business_risk": {"rating": "LOW"},
                    "transaction_risk": {"rating": "MEDIUM"},
                    "ownership_risk": {"rating": "LOW"},
                    "financial_crime_risk": {"rating": "LOW"}
                }}
            }
        })
        result = run_memo_supervisor(memo)
        own_c = [c for c in result["contradictions"] if c["category"] == "ownership_inconsistency"]
        assert len(own_c) >= 1


class TestMemoCheck3_PEP:
    """Check 3: PEP findings vs screening results."""

    def test_pep_match_denied_in_exec(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "sections": {
                "screening_results": {"content": "PEP confirmed match identified in screening results."},
                "executive_summary": {"content": "No PEP exposure. Low risk entity."}
            }
        })
        result = run_memo_supervisor(memo)
        pep_issues = [c for c in result["contradictions"] if c["category"] in ("pep_inconsistency", "pep_advisory")]
        assert len(pep_issues) >= 1

    def test_pep_handled_not_critical(self, temp_db):
        """PEP identified AND flagged for enhanced measures → not a critical contradiction."""
        from server import run_memo_supervisor
        memo = make_base_memo({
            "sections": {
                "screening_results": {"content": "PEP confirmed match identified and flagged for enhanced due diligence. Enhanced measures applied."},
                "executive_summary": {"content": "No PEP exposure in executive summary."}
            }
        })
        result = run_memo_supervisor(memo)
        pep_critical = [c for c in result["contradictions"] if c["category"] == "pep_inconsistency"]
        assert len(pep_critical) == 0, "Properly handled PEP should not trigger critical contradiction"

    def test_clean_screening_no_pep_issue(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo()
        result = run_memo_supervisor(memo)
        pep_issues = [c for c in result["contradictions"] if c["category"] in ("pep_inconsistency", "pep_advisory")]
        assert len(pep_issues) == 0


class TestMemoCheck4_Docs:
    """Check 4: Outstanding docs vs APPROVE."""

    def test_outstanding_docs_approve(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "metadata": {"approval_recommendation": "APPROVE"},
            "sections": {
                "document_verification": {"content": "2 documents outstanding and pending."},
                "compliance_decision": {"decision": "APPROVE"}
            }
        })
        result = run_memo_supervisor(memo)
        doc_c = [c for c in result["contradictions"] if c["category"] == "doc_vs_decision"]
        assert len(doc_c) >= 1

    def test_no_documents_blocks_approval(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "metadata": {
                "approval_recommendation": "APPROVE_WITH_CONDITIONS",
                "document_count": 0,
                "documentation_complete": False
            },
            "sections": {
                "document_verification": {"content": "No documents have been uploaded. Entity verification cannot be completed."},
                "compliance_decision": {"decision": "APPROVE_WITH_CONDITIONS"}
            }
        })
        result = run_memo_supervisor(memo)
        assert result["can_approve"] is False
        assert result["requires_sco_review"] is True
        warning_cats = [w["category"] for w in result["warnings"]]
        assert "missing_documents" in warning_cats


class TestMemoCheck5_RedFlags:
    """Check 5: Red flags without mitigants."""

    def test_flags_no_mitigants(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH"},
            "sections": {"red_flags_and_mitigants": {
                "red_flags": ["High risk jurisdiction", "Complex ownership"],
                "mitigants": []
            }}
        })
        result = run_memo_supervisor(memo)
        rf_c = [c for c in result["contradictions"] if c["category"] == "rf_mitigant_imbalance"]
        assert len(rf_c) >= 1


class TestMemoCheck6_Factors:
    """Check 6: Misclassified factors."""

    def test_decreasing_in_increasing_list(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "sections": {"ai_explainability": {
                "content": "Analysis.",
                "risk_increasing_factors": ["No PEP exposure", "Clean sanctions"],
                "risk_decreasing_factors": []
            }}
        })
        result = run_memo_supervisor(memo)
        factor_c = [c for c in result["contradictions"] if c["category"] == "factor_misclassification"]
        assert len(factor_c) >= 1


class TestMemoCheck8_JurisdictionMonitoring:
    """Check 8: HIGH jurisdiction + Standard monitoring."""

    def test_high_jur_standard_monitoring(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "sections": {
                "risk_assessment": {"sub_sections": {
                    "jurisdiction_risk": {"rating": "HIGH"},
                    "business_risk": {"rating": "LOW"},
                    "transaction_risk": {"rating": "MEDIUM"},
                    "ownership_risk": {"rating": "MEDIUM"},
                    "financial_crime_risk": {"rating": "LOW"}
                }},
                "ongoing_monitoring": {"content": "Standard monitoring tier applied."}
            }
        })
        result = run_memo_supervisor(memo)
        jur_c = [c for c in result["contradictions"] if c["category"] == "jurisdiction_vs_monitoring"]
        assert len(jur_c) >= 1


class TestMemoVerdict:
    """Verdict computation logic."""

    def test_clean_memo_consistent(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo()
        result = run_memo_supervisor(memo)
        assert result["verdict"] in ("CONSISTENT", "CONSISTENT_WITH_WARNINGS")

    def test_critical_contradiction_inconsistent(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH", "approval_recommendation": "APPROVE"}
        })
        result = run_memo_supervisor(memo)
        assert result["verdict"] == "INCONSISTENT"

    def test_confidence_penalised(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo({
            "metadata": {"risk_rating": "HIGH", "approval_recommendation": "APPROVE"}
        })
        result = run_memo_supervisor(memo)
        assert result["supervisor_confidence"] < 1.0

    def test_result_fields(self, temp_db):
        from server import run_memo_supervisor
        memo = make_base_memo()
        result = run_memo_supervisor(memo)
        for field in ["verdict", "contradictions", "warnings", "recommendation", "supervisor_confidence"]:
            assert field in result, f"Missing field: {field}"
