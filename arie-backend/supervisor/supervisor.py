"""
ARIE Finance — AI Agent Supervisor Service
============================================
The central orchestrator that:
  1. Triggers the correct agents for a given workflow
  2. Validates each agent output
  3. Checks confidence and routes accordingly
  4. Detects contradictions between agents
  5. Applies hard compliance rules
  6. Routes to human review when needed
  7. Logs everything for audit
  8. Tracks agent quality over time

This is the single entry point for all AI agent pipelines.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from .audit import AuditLogger
from .confidence import ConfidenceEvaluator
from .contradictions import ContradictionDetector
from .rules_engine import RulesEngine
from .schemas import (
    AGENT_OUTPUT_MODELS,
    AgentOutputBase,
    AgentType,
    AuditEventType,
    CaseAggregate,
    ConfidenceRouting,
    Contradiction,
    Escalation,
    EscalationLevel,
    RuleAction,
    RuleEvaluation,
    RunStatus,
    Severity,
    TriggerType,
    ValidationResult,
)
from .validator import SchemaValidator

logger = logging.getLogger("arie.supervisor")


# ═══════════════════════════════════════════════════════════
# PIPELINE CONFIGURATION
# ═══════════════════════════════════════════════════════════

# Which agents run for each trigger type
PIPELINE_AGENTS: Dict[TriggerType, List[AgentType]] = {
    TriggerType.ONBOARDING: [
        AgentType.IDENTITY_DOCUMENT_INTEGRITY,       # Agent 1: Identity & Document Integrity
        AgentType.EXTERNAL_DATABASE_VERIFICATION,     # Agent 2 (sub): Registry cross-check
        AgentType.FINCRIME_SCREENING,                 # Agent 3: FinCrime Screening Interpretation
        AgentType.CORPORATE_STRUCTURE_UBO,            # Agent 4: Corporate Structure & UBO Mapping
        AgentType.COMPLIANCE_MEMO_RISK,               # Agent 5: Compliance Memo & Risk Recommendation (includes business plausibility sub-analysis)
    ],
    TriggerType.PERIODIC_REVIEW: [
        AgentType.PERIODIC_REVIEW_PREPARATION,        # Agent 6: Periodic Review Preparation
        AgentType.FINCRIME_SCREENING,                 # Agent 4 (re-run)
        AgentType.ADVERSE_MEDIA_PEP_MONITORING,       # Agent 7: Adverse Media & PEP Monitoring
        AgentType.BEHAVIOUR_RISK_DRIFT,               # Agent 8: Behaviour & Risk Drift
        AgentType.ONGOING_COMPLIANCE_REVIEW,          # Agent 10: Ongoing Compliance Review
    ],
    TriggerType.MONITORING_ALERT: [
        AgentType.ADVERSE_MEDIA_PEP_MONITORING,       # Agent 7: Adverse Media & PEP Monitoring
        AgentType.BEHAVIOUR_RISK_DRIFT,               # Agent 8: Behaviour & Risk Drift
    ],
    TriggerType.MANUAL_TRIGGER: [],  # Dynamically configured
}


class SupervisorPipelineResult:
    """Result of a complete supervisor pipeline run."""

    def __init__(self, pipeline_id: str, application_id: str):
        self.pipeline_id = pipeline_id
        self.application_id = application_id
        self.started_at = datetime.utcnow().isoformat() + "Z"
        self.completed_at: Optional[str] = None

        # Results from each stage
        self.agent_outputs: Dict[AgentType, AgentOutputBase] = {}
        self.validation_results: List[ValidationResult] = []
        self.failed_agents: List[Dict[str, Any]] = []
        self.contradictions: List[Contradiction] = []
        self.rule_evaluations: List[RuleEvaluation] = []
        self.escalations: List[Escalation] = []
        self.case_aggregate: Optional[CaseAggregate] = None

        # Status
        self.status: str = "running"
        self.requires_human_review: bool = False
        self.review_reasons: List[str] = []
        self.blocking_issues: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for API response / storage."""
        return {
            "pipeline_id": self.pipeline_id,
            "application_id": self.application_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "agent_count": len(self.agent_outputs),
            "failed_agent_count": len(self.failed_agents),
            "validation_pass_count": sum(1 for v in self.validation_results if v.is_valid),
            "validation_fail_count": sum(1 for v in self.validation_results if not v.is_valid),
            "contradiction_count": len(self.contradictions),
            "rules_triggered_count": sum(1 for r in self.rule_evaluations if r.triggered),
            "escalation_count": len(self.escalations),
            "requires_human_review": self.requires_human_review,
            "review_reasons": self.review_reasons,
            "blocking_issues": self.blocking_issues,
            "case_aggregate": self.case_aggregate.model_dump() if self.case_aggregate else None,
            "contradictions": [c.model_dump() for c in self.contradictions],
            "triggered_rules": [
                r.model_dump() for r in self.rule_evaluations if r.triggered
            ],
            "escalations": [e.model_dump() for e in self.escalations],
        }


class AgentSupervisor:
    """
    Central AI Agent Supervisor.

    Orchestrates the full pipeline:
      Agent Execution → Validation → Confidence → Contradictions
      → Rules → Escalation → Human Review Routing → Audit
    """

    def __init__(self, db_path: Optional[str] = None):
        self.validator = SchemaValidator()
        self.confidence = ConfidenceEvaluator()
        self.contradictions = ContradictionDetector()
        self.rules = RulesEngine()
        self.audit = AuditLogger(db_path=db_path)

        # Load default rules (in production, load from DB)
        self.rules.load_default_rules()

        # Agent execution functions (registered by the application)
        self._agent_executors: Dict[AgentType, Callable] = {}

        logger.info("AgentSupervisor initialized with %d rules", len(self.rules.rules))

    def register_agent_executor(
        self,
        agent_type: AgentType,
        executor: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    ):
        """
        Register an agent execution function.

        The executor should accept (application_id, context_data) and
        return a raw output dict matching the agent's schema.
        """
        self._agent_executors[agent_type] = executor
        logger.info("Registered executor for agent: %s", agent_type.value)

    async def run_pipeline(
        self,
        application_id: str,
        trigger_type: TriggerType,
        context_data: Optional[Dict[str, Any]] = None,
        agent_types: Optional[List[AgentType]] = None,
        trigger_source: Optional[str] = None,
    ) -> SupervisorPipelineResult:
        """
        Run a complete supervisor pipeline for an application.

        Args:
            application_id: The application to process
            trigger_type: What triggered this pipeline
            context_data: Additional context for agents
            agent_types: Override which agents to run (for manual triggers)
            trigger_source: Who/what initiated

        Returns:
            SupervisorPipelineResult with all outputs, validations,
            contradictions, rules, escalations, and aggregate scores.
        """
        pipeline_id = str(uuid4())
        result = SupervisorPipelineResult(pipeline_id, application_id)
        context = context_data or {}

        # ── 1. Log pipeline start ──
        self.audit.log(
            event_type=AuditEventType.PIPELINE_STARTED,
            action=f"Pipeline started: {trigger_type.value}",
            detail=f"Application {application_id}, trigger: {trigger_source or 'system'}",
            pipeline_id=pipeline_id,
            application_id=application_id,
            data={"trigger_type": trigger_type.value, "trigger_source": trigger_source},
        )

        # ── 2. Determine which agents to run ──
        agents_to_run = agent_types or PIPELINE_AGENTS.get(trigger_type, [])
        if not agents_to_run:
            result.status = "failed"
            result.blocking_issues.append("No agents configured for this trigger type")
            return result

        # ── 3. Execute each agent ──
        for agent_type in agents_to_run:
            run_id = str(uuid4())
            start_time = time.time()

            self.audit.log_agent_run_started(
                run_id=run_id,
                agent_type=agent_type.value,
                application_id=application_id,
                pipeline_id=pipeline_id,
            )

            try:
                raw_output = await self._execute_agent(
                    agent_type, application_id, run_id, context
                )
                elapsed_ms = int((time.time() - start_time) * 1000)

                # Inject run metadata if not present
                raw_output.setdefault("run_id", run_id)
                raw_output.setdefault("application_id", application_id)
                raw_output.setdefault("processing_time_ms", elapsed_ms)

                # ── 4. Validate output ──
                is_valid, validation, parsed = self.validator.validate(
                    raw_output, expected_agent_type=agent_type
                )
                result.validation_results.append(validation)

                self.audit.log_validation(
                    run_id=run_id,
                    agent_type=agent_type.value,
                    application_id=application_id,
                    is_valid=is_valid,
                    errors=[e.message for e in validation.errors],
                    pipeline_id=pipeline_id,
                )

                if not is_valid or parsed is None:
                    # Quarantine the output
                    result.failed_agents.append({
                        "agent_type": agent_type.value,
                        "run_id": run_id,
                        "reason": "validation_failed",
                        "errors": [e.message for e in validation.errors],
                    })
                    logger.warning(
                        "Agent %s output quarantined: validation failed",
                        agent_type.value
                    )
                    continue

                # ── 5. Evaluate confidence ──
                confidence_score = self.confidence.evaluate_agent_output(
                    parsed, pipeline_id=pipeline_id
                )

                self.audit.log(
                    event_type=AuditEventType.CONFIDENCE_CALCULATED,
                    action=f"Confidence calculated for {agent_type.value}",
                    detail=f"Score: {confidence_score.confidence_score:.3f}, "
                           f"Routing: {confidence_score.routing_decision.value}",
                    pipeline_id=pipeline_id,
                    application_id=application_id,
                    run_id=run_id,
                    agent_type=agent_type.value,
                    data={
                        "confidence": confidence_score.confidence_score,
                        "routing": confidence_score.routing_decision.value,
                    },
                )

                # Store successful output
                result.agent_outputs[agent_type] = parsed

                self.audit.log_agent_run_completed(
                    run_id=run_id,
                    agent_type=agent_type.value,
                    application_id=application_id,
                    pipeline_id=pipeline_id,
                    confidence=parsed.confidence_score,
                    status=parsed.status.value,
                )

            except Exception as e:
                elapsed_ms = int((time.time() - start_time) * 1000)
                error_msg = str(e)
                result.failed_agents.append({
                    "agent_type": agent_type.value,
                    "run_id": run_id,
                    "reason": "execution_error",
                    "error": error_msg,
                    "runtime_ms": elapsed_ms,
                })

                self.audit.log_agent_run_failed(
                    run_id=run_id,
                    agent_type=agent_type.value,
                    application_id=application_id,
                    pipeline_id=pipeline_id,
                    error=error_msg,
                )
                logger.error("Agent %s execution failed: %s", agent_type.value, e)

        # ── 6. Detect contradictions ──
        if len(result.agent_outputs) >= 2:
            result.contradictions = self.contradictions.detect_all(
                outputs=result.agent_outputs,
                pipeline_id=pipeline_id,
                application_id=application_id,
            )
            for contradiction in result.contradictions:
                self.audit.log_contradiction(
                    contradiction_id=contradiction.contradiction_id,
                    application_id=application_id,
                    pipeline_id=pipeline_id,
                    category=contradiction.contradiction_category.value,
                    severity=contradiction.severity.value,
                    agent_a=contradiction.agent_a_type.value,
                    agent_b=contradiction.agent_b_type.value,
                    description=contradiction.description,
                )

        # ── 7. Evaluate rules ──
        outputs_list = list(result.agent_outputs.values())
        if outputs_list:
            result.rule_evaluations = self.rules.evaluate(
                outputs=outputs_list,
                pipeline_id=pipeline_id,
                application_id=application_id,
            )
            for rule_eval in result.rule_evaluations:
                if rule_eval.triggered:
                    self.audit.log_rule_triggered(
                        rule_name=rule_eval.rule_name,
                        application_id=application_id,
                        pipeline_id=pipeline_id,
                        action=rule_eval.action_taken.value if rule_eval.action_taken else "none",
                        severity=rule_eval.severity.value if rule_eval.severity else "info",
                        trigger_data=json.dumps(rule_eval.trigger_data) if rule_eval.trigger_data else None,
                    )

        # ── 8. Calculate case aggregate ──
        aggregate, agg_confidence = self.confidence.calculate_case_aggregate(
            agent_outputs=outputs_list,
            contradictions=result.contradictions,
            rule_evaluations=result.rule_evaluations,
            failed_agent_count=len(result.failed_agents),
            pipeline_id=pipeline_id,
            application_id=application_id,
        )
        result.case_aggregate = aggregate

        # ── 9. Determine if human review is needed ──
        result.requires_human_review = self._check_human_review_required(result)
        result.review_reasons = self._get_review_reasons(result)
        result.blocking_issues = self._get_blocking_issues(result)

        # ── 10. Create escalations if needed ──
        result.escalations = self._create_escalations(result, pipeline_id)

        # ── 11. Set final status ──
        if result.blocking_issues:
            result.status = "awaiting_review"
        elif result.requires_human_review:
            result.status = "awaiting_review"
        elif result.failed_agents:
            result.status = "completed_with_errors"
        else:
            result.status = "completed"

        result.completed_at = datetime.utcnow().isoformat() + "Z"

        # ── 12. Log pipeline completion ──
        self.audit.log(
            event_type=AuditEventType.PIPELINE_COMPLETED,
            action=f"Pipeline completed: {result.status}",
            detail=(
                f"Agents: {len(result.agent_outputs)}/{len(agents_to_run)}, "
                f"Contradictions: {len(result.contradictions)}, "
                f"Rules triggered: {sum(1 for r in result.rule_evaluations if r.triggered)}, "
                f"Aggregate confidence: {aggregate.aggregate_confidence}"
            ),
            pipeline_id=pipeline_id,
            application_id=application_id,
            data=result.to_dict(),
        )

        logger.info(
            "Pipeline %s completed: status=%s agents=%d/%d contradictions=%d "
            "rules_triggered=%d review_required=%s",
            pipeline_id, result.status,
            len(result.agent_outputs), len(agents_to_run),
            len(result.contradictions),
            sum(1 for r in result.rule_evaluations if r.triggered),
            result.requires_human_review,
        )

        return result

    # ─── Internal methods ──────────────────────────────────

    async def _execute_agent(
        self,
        agent_type: AgentType,
        application_id: str,
        run_id: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute a single agent and return raw output."""
        executor = self._agent_executors.get(agent_type)
        if not executor:
            raise RuntimeError(f"No executor registered for agent: {agent_type.value}")

        # Call the executor
        import asyncio
        if asyncio.iscoroutinefunction(executor):
            raw_output = await executor(application_id, context)
        else:
            raw_output = executor(application_id, context)

        return raw_output

    def _check_human_review_required(self, result: SupervisorPipelineResult) -> bool:
        """Determine if human review is required."""
        # Always required if aggregate confidence is below normal threshold
        if result.case_aggregate:
            if result.case_aggregate.confidence_routing != ConfidenceRouting.NORMAL:
                return True

        # Required if any critical contradictions
        if any(c.severity == Severity.CRITICAL for c in result.contradictions):
            return True

        # Required if any blocking rules triggered
        blocking = self.rules.get_blocking_rules(result.rule_evaluations)
        if blocking:
            return True

        # Required if any agent flagged escalation
        for output in result.agent_outputs.values():
            if output.escalation_flag:
                return True

        # Required if agents failed
        if result.failed_agents:
            return True

        return False

    def _get_review_reasons(self, result: SupervisorPipelineResult) -> List[str]:
        """Collect all reasons why human review is needed."""
        reasons = []

        if result.case_aggregate:
            routing = result.case_aggregate.confidence_routing
            if routing == ConfidenceRouting.MANDATORY_ESCALATION:
                reasons.append(
                    f"Aggregate confidence below escalation threshold: "
                    f"{result.case_aggregate.aggregate_confidence:.3f}"
                )
            elif routing == ConfidenceRouting.HUMAN_REVIEW:
                reasons.append(
                    f"Aggregate confidence requires review: "
                    f"{result.case_aggregate.aggregate_confidence:.3f}"
                )

        for c in result.contradictions:
            if c.severity in (Severity.CRITICAL, Severity.HIGH):
                reasons.append(
                    f"Contradiction ({c.severity.value}): {c.contradiction_category.value}"
                )

        for r in result.rule_evaluations:
            if r.triggered and r.action_taken in (
                RuleAction.BLOCK_APPROVAL, RuleAction.REJECT, RuleAction.ESCALATE
            ):
                reasons.append(f"Rule triggered: {r.rule_name} → {r.action_taken.value}")

        for fa in result.failed_agents:
            reasons.append(f"Agent failed: {fa['agent_type']} ({fa['reason']})")

        for output in result.agent_outputs.values():
            if output.escalation_flag:
                reasons.append(
                    f"Agent escalation: {output.agent_type.value} — {output.escalation_reason}"
                )

        return reasons

    def _get_blocking_issues(self, result: SupervisorPipelineResult) -> List[str]:
        """Get issues that block case progression."""
        issues = []

        blocking_rules = self.rules.get_blocking_rules(result.rule_evaluations)
        for r in blocking_rules:
            issues.append(f"Rule: {r.rule_name} → {r.action_taken.value}")

        critical_contradictions = [
            c for c in result.contradictions if c.severity == Severity.CRITICAL
        ]
        for c in critical_contradictions:
            issues.append(f"Critical contradiction: {c.contradiction_type}")

        return issues

    def _create_escalations(
        self, result: SupervisorPipelineResult, pipeline_id: str
    ) -> List[Escalation]:
        """Create escalation records based on pipeline results."""
        escalations = []

        # Escalation from confidence routing
        if result.case_aggregate and result.case_aggregate.escalation_required:
            escalations.append(Escalation(
                pipeline_id=pipeline_id,
                application_id=result.application_id,
                escalation_source="low_confidence",
                escalation_level=result.case_aggregate.escalation_level or EscalationLevel.COMPLIANCE_OFFICER,
                priority=Severity.HIGH,
                reason=f"Aggregate confidence {result.case_aggregate.aggregate_confidence:.3f} "
                       f"below threshold",
                context={
                    "aggregate_confidence": result.case_aggregate.aggregate_confidence,
                    "routing": result.case_aggregate.confidence_routing.value
                    if result.case_aggregate.confidence_routing else None,
                },
            ))

        # Escalation from contradictions
        critical_contradictions = [
            c for c in result.contradictions if c.severity == Severity.CRITICAL
        ]
        if critical_contradictions:
            escalations.append(Escalation(
                pipeline_id=pipeline_id,
                application_id=result.application_id,
                escalation_source="contradiction",
                source_id=critical_contradictions[0].contradiction_id,
                escalation_level=EscalationLevel.SENIOR_COMPLIANCE,
                priority=Severity.CRITICAL,
                reason=f"{len(critical_contradictions)} critical contradiction(s) detected",
                context={
                    "contradictions": [c.model_dump() for c in critical_contradictions]
                },
            ))

        # Escalation from rules
        blocking = self.rules.get_blocking_rules(result.rule_evaluations)
        for rule in blocking:
            if rule.action_taken == RuleAction.ESCALATE:
                escalations.append(Escalation(
                    pipeline_id=pipeline_id,
                    application_id=result.application_id,
                    escalation_source="rule_trigger",
                    source_id=rule.evaluation_id,
                    escalation_level=EscalationLevel.MLRO if rule.severity == Severity.CRITICAL
                    else EscalationLevel.SENIOR_COMPLIANCE,
                    priority=rule.severity or Severity.HIGH,
                    reason=f"Rule {rule.rule_name}: {rule.rule_recommendation or rule.rule_category}",
                    context={"rule": rule.model_dump()},
                ))

        return escalations

    # ─── Stats ──────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {
            "validator": self.validator.get_stats(),
            "confidence": {"thresholds": {
                "normal": self.confidence.normal_threshold,
                "review": self.confidence.review_threshold,
            }},
            "contradictions": self.contradictions.get_stats(),
            "rules": self.rules.get_stats(),
            "audit": self.audit.get_stats(),
        }
