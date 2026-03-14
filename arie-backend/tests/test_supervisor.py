"""
Tests for the AI Agent Supervisor framework.
"""
import pytest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSupervisorSchemas:
    def test_agent_types_defined(self):
        from supervisor.schemas import AgentType
        assert len(AgentType) == 10
        assert AgentType.IDENTITY_DOCUMENT is not None
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
        # Minimal valid output
        output = {
            "agent_type": "IDENTITY_DOCUMENT",
            "status": "completed",
            "confidence_score": 0.85,
            "summary": "Document verified",
            "findings": [],
            "evidence": [],
            "risk_indicators": [],
            "requires_escalation": False,
        }
        result = v.validate(output, AgentType.IDENTITY_DOCUMENT)
        assert result is not None


class TestConfidence:
    def test_evaluator_initializes(self):
        from supervisor.confidence import ConfidenceEvaluator
        ce = ConfidenceEvaluator()
        assert ce is not None
        assert ce.THRESHOLD_NORMAL == 0.85
        assert ce.THRESHOLD_REVIEW == 0.65

    def test_routing_decision(self):
        from supervisor.confidence import ConfidenceEvaluator
        from supervisor.schemas import ConfidenceRouting
        ce = ConfidenceEvaluator()
        assert ce.get_routing(0.90) == ConfidenceRouting.NORMAL
        assert ce.get_routing(0.75) == ConfidenceRouting.HUMAN_REVIEW
        assert ce.get_routing(0.50) == ConfidenceRouting.MANDATORY_ESCALATION


class TestContradictions:
    def test_detector_initializes(self):
        from supervisor.contradictions import ContradictionDetector
        cd = ContradictionDetector()
        assert cd is not None


class TestRulesEngine:
    def test_engine_initializes(self):
        from supervisor.rules_engine import RulesEngine
        re = RulesEngine()
        assert re is not None
        assert len(re.rules) > 0

    def test_rules_have_priority(self):
        from supervisor.rules_engine import RulesEngine
        re = RulesEngine()
        priorities = [r.priority for r in re.rules]
        assert priorities == sorted(priorities), "Rules should be priority-ordered"


class TestAuditLogger:
    def test_logger_initializes(self, temp_db):
        from supervisor.audit import AuditLogger
        al = AuditLogger(db_path=temp_db)
        assert al is not None

    def test_hash_chain_integrity(self, temp_db):
        from supervisor.audit import AuditLogger
        al = AuditLogger(db_path=temp_db)
        al.log("test_event", application_id="app1", action="test", details={"key": "value"})
        al.log("test_event_2", application_id="app1", action="test2", details={"key": "value2"})
        result = al.verify_chain_integrity(limit=10)
        assert result["is_valid"] is True
