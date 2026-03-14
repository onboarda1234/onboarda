"""
ARIE Finance — AI Agent Supervisor: Confidence Control Framework
=================================================================
Implements confidence-based routing logic:
  - confidence > 0.85        → normal workflow
  - confidence 0.65 – 0.85   → human review required
  - confidence < 0.65        → mandatory escalation

Also calculates:
  - Case-level aggregate confidence (weighted by agent importance)
  - Confidence by agent type
  - Rolling average confidence over time

Aggregate confidence formula:
  weighted_sum(agent_confidence * agent_weight) / sum(weights)
  Adjusted downward by:
    - Number of contradictions (each critical: -0.05, high: -0.03, medium: -0.01)
    - Number of failed agents (each: -0.08)
    - Number of rules triggered (each blocking: -0.10, each non-blocking: -0.02)
  Final score clamped to [0.0, 1.0]
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from .schemas import (
    AgentOutputBase,
    AgentType,
    CaseAggregate,
    ConfidenceRouting,
    ConfidenceScore,
    Contradiction,
    EscalationLevel,
    RuleEvaluation,
    Severity,
)

logger = logging.getLogger("arie.supervisor.confidence")

# ═══════════════════════════════════════════════════════════
# THRESHOLDS (configurable)
# ═══════════════════════════════════════════════════════════

CONFIDENCE_THRESHOLD_NORMAL = 0.85
CONFIDENCE_THRESHOLD_REVIEW = 0.65

# Agent importance weights for aggregate calculation
# Higher weight = more influence on case-level confidence
AGENT_WEIGHTS: Dict[AgentType, float] = {
    AgentType.IDENTITY_DOCUMENT_INTEGRITY: 1.0,
    AgentType.EXTERNAL_DATABASE_VERIFICATION: 1.0,
    AgentType.CORPORATE_STRUCTURE_UBO: 1.2,        # UBO is critical for AML
    AgentType.BUSINESS_MODEL_PLAUSIBILITY: 0.9,
    AgentType.FINCRIME_SCREENING: 1.3,             # Screening is highest priority
    AgentType.COMPLIANCE_MEMO_RISK: 0.8,           # Derived from other agents
    AgentType.PERIODIC_REVIEW_PREPARATION: 0.7,
    AgentType.ADVERSE_MEDIA_PEP_MONITORING: 1.1,
    AgentType.BEHAVIOUR_RISK_DRIFT: 0.8,
    AgentType.ONGOING_COMPLIANCE_REVIEW: 0.7,
}

# Penalty factors for aggregate confidence
PENALTY_CRITICAL_CONTRADICTION = 0.05
PENALTY_HIGH_CONTRADICTION = 0.03
PENALTY_MEDIUM_CONTRADICTION = 0.01
PENALTY_FAILED_AGENT = 0.08
PENALTY_BLOCKING_RULE = 0.10
PENALTY_NON_BLOCKING_RULE = 0.02


class ConfidenceEvaluator:
    """
    Evaluates confidence scores and determines routing decisions.
    """

    def __init__(
        self,
        normal_threshold: float = CONFIDENCE_THRESHOLD_NORMAL,
        review_threshold: float = CONFIDENCE_THRESHOLD_REVIEW,
    ):
        self.normal_threshold = normal_threshold
        self.review_threshold = review_threshold
        self._history: Dict[AgentType, List[float]] = defaultdict(list)

    def route_confidence(self, score: float) -> ConfidenceRouting:
        """Determine routing based on confidence score."""
        if score >= self.normal_threshold:
            return ConfidenceRouting.NORMAL
        elif score >= self.review_threshold:
            return ConfidenceRouting.HUMAN_REVIEW
        else:
            return ConfidenceRouting.MANDATORY_ESCALATION

    def evaluate_agent_output(
        self,
        output: AgentOutputBase,
        pipeline_id: Optional[str] = None,
    ) -> ConfidenceScore:
        """
        Evaluate a single agent output's confidence.
        Records the score in rolling history.
        """
        score = output.confidence_score
        routing = self.route_confidence(score)

        # Track for rolling averages
        self._history[output.agent_type].append(score)

        result = ConfidenceScore(
            score_id=str(uuid4()),
            run_id=output.run_id,
            pipeline_id=pipeline_id,
            application_id=output.application_id,
            agent_type=output.agent_type,
            score_type="agent_output",
            confidence_score=score,
            routing_decision=routing,
            component_scores={},
            calculation_method="direct_agent_output",
        )

        logger.info(
            "Confidence: agent=%s app=%s score=%.3f routing=%s",
            output.agent_type.value, output.application_id,
            score, routing.value
        )

        return result

    def calculate_case_aggregate(
        self,
        agent_outputs: List[AgentOutputBase],
        contradictions: List[Contradiction],
        rule_evaluations: List[RuleEvaluation],
        failed_agent_count: int,
        pipeline_id: str,
        application_id: str,
    ) -> Tuple[CaseAggregate, ConfidenceScore]:
        """
        Calculate case-level aggregate confidence.

        Formula:
          base = weighted_avg(agent_confidence * weight)
          penalties = contradiction_penalties + failure_penalties + rule_penalties
          final = max(0.0, min(1.0, base - penalties))
        """
        if not agent_outputs:
            # No outputs — mandatory escalation
            aggregate = CaseAggregate(
                pipeline_id=pipeline_id,
                application_id=application_id,
                aggregate_confidence=0.0,
                confidence_routing=ConfidenceRouting.MANDATORY_ESCALATION,
                escalation_required=True,
                escalation_level=EscalationLevel.SENIOR_COMPLIANCE,
                pipeline_status="failed",
            )
            score = ConfidenceScore(
                pipeline_id=pipeline_id,
                application_id=application_id,
                score_type="case_aggregate",
                confidence_score=0.0,
                routing_decision=ConfidenceRouting.MANDATORY_ESCALATION,
                calculation_method="no_agent_outputs",
            )
            return aggregate, score

        # ── Weighted average ──
        total_weight = 0.0
        weighted_sum = 0.0
        component_scores: Dict[str, float] = {}
        confidence_values: List[float] = []

        for output in agent_outputs:
            weight = AGENT_WEIGHTS.get(output.agent_type, 1.0)
            weighted_sum += output.confidence_score * weight
            total_weight += weight
            component_scores[output.agent_type.value] = output.confidence_score
            confidence_values.append(output.confidence_score)

        base_score = weighted_sum / total_weight if total_weight > 0 else 0.0

        # ── Calculate penalties ──
        penalty = 0.0
        penalty_breakdown: Dict[str, float] = {}

        # Contradiction penalties
        contradiction_penalty = 0.0
        for c in contradictions:
            if c.severity == Severity.CRITICAL:
                contradiction_penalty += PENALTY_CRITICAL_CONTRADICTION
            elif c.severity == Severity.HIGH:
                contradiction_penalty += PENALTY_HIGH_CONTRADICTION
            elif c.severity == Severity.MEDIUM:
                contradiction_penalty += PENALTY_MEDIUM_CONTRADICTION
        penalty += contradiction_penalty
        penalty_breakdown["contradictions"] = contradiction_penalty

        # Failed agent penalties
        failure_penalty = failed_agent_count * PENALTY_FAILED_AGENT
        penalty += failure_penalty
        penalty_breakdown["failed_agents"] = failure_penalty

        # Rule trigger penalties
        rule_penalty = 0.0
        blocking_rules = 0
        for rule_eval in rule_evaluations:
            if rule_eval.triggered:
                if rule_eval.action_taken and rule_eval.action_taken.value in (
                    "block_approval", "reject", "escalate"
                ):
                    rule_penalty += PENALTY_BLOCKING_RULE
                    blocking_rules += 1
                else:
                    rule_penalty += PENALTY_NON_BLOCKING_RULE
        penalty += rule_penalty
        penalty_breakdown["rules"] = rule_penalty

        # ── Final score ──
        final_score = max(0.0, min(1.0, base_score - penalty))
        routing = self.route_confidence(final_score)

        # ── Determine escalation level ──
        escalation_required = routing != ConfidenceRouting.NORMAL
        escalation_level = None
        if routing == ConfidenceRouting.MANDATORY_ESCALATION:
            # Check if critical contradictions or blocking rules warrant MLRO
            critical_contradictions = sum(
                1 for c in contradictions if c.severity == Severity.CRITICAL
            )
            if critical_contradictions > 0 or blocking_rules > 0:
                escalation_level = EscalationLevel.MLRO
            else:
                escalation_level = EscalationLevel.SENIOR_COMPLIANCE
        elif routing == ConfidenceRouting.HUMAN_REVIEW:
            escalation_level = EscalationLevel.COMPLIANCE_OFFICER

        # ── Build results ──
        aggregate = CaseAggregate(
            pipeline_id=pipeline_id,
            application_id=application_id,
            total_agents_run=len(agent_outputs) + failed_agent_count,
            successful_agents=len(agent_outputs),
            failed_agents=failed_agent_count,
            aggregate_confidence=round(final_score, 4),
            min_agent_confidence=min(confidence_values) if confidence_values else None,
            max_agent_confidence=max(confidence_values) if confidence_values else None,
            confidence_routing=routing,
            total_contradictions=len(contradictions),
            critical_contradictions=sum(
                1 for c in contradictions if c.severity == Severity.CRITICAL
            ),
            total_rules_triggered=sum(1 for r in rule_evaluations if r.triggered),
            blocking_rules_triggered=blocking_rules,
            escalation_required=escalation_required,
            escalation_level=escalation_level,
            pipeline_status="awaiting_review" if escalation_required else "completed",
        )

        score = ConfidenceScore(
            pipeline_id=pipeline_id,
            application_id=application_id,
            score_type="case_aggregate",
            confidence_score=round(final_score, 4),
            routing_decision=routing,
            component_scores={
                **component_scores,
                "_base_score": round(base_score, 4),
                "_total_penalty": round(penalty, 4),
                **{f"_penalty_{k}": round(v, 4) for k, v in penalty_breakdown.items()}
            },
            calculation_method="weighted_average_with_penalties",
        )

        logger.info(
            "Case aggregate: app=%s base=%.3f penalty=%.3f final=%.3f routing=%s",
            application_id, base_score, penalty, final_score, routing.value
        )

        return aggregate, score

    def get_rolling_average(
        self,
        agent_type: AgentType,
        window_size: int = 50,
    ) -> Optional[ConfidenceScore]:
        """
        Calculate rolling average confidence for an agent type.
        Uses the last `window_size` runs.
        """
        history = self._history.get(agent_type, [])
        if not history:
            return None

        window = history[-window_size:]
        avg = sum(window) / len(window)
        routing = self.route_confidence(avg)

        return ConfidenceScore(
            application_id="rolling_average",
            agent_type=agent_type,
            score_type="agent_rolling_avg",
            confidence_score=round(avg, 4),
            routing_decision=routing,
            component_scores={
                "window_size": window_size,
                "actual_samples": len(window),
                "min": round(min(window), 4),
                "max": round(max(window), 4),
            },
            calculation_method=f"rolling_average_window_{window_size}",
        )
