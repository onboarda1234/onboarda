"""
ARIE Finance — AI Agent Supervisor: Rules Engine
==================================================
Configurable compliance rules that override AI recommendations.

Hard rules that always apply:
  - Sanctions hit → automatic escalation
  - Confirmed PEP → enhanced review
  - Missing UBO → cannot approve
  - Company not in registry → hold
  - Document tampering → reject/escalate
  - High-risk jurisdiction → mandatory review

Design:
  - Rules are loaded from database (supervisor_rules_config)
  - Each rule has a JSON condition + action
  - Rules are evaluated in priority order (lower number = higher priority)
  - Blocking rules prevent case progression
  - All evaluations are logged for audit
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .schemas import (
    AgentOutputBase,
    AgentType,
    RuleAction,
    RuleEvaluation,
    Severity,
)

logger = logging.getLogger("arie.supervisor.rules_engine")


class RuleCondition:
    """
    Parsed rule condition.
    Supports checking agent outputs for specific field values.

    Condition JSON format:
    {
        "field": "findings" | "issues" | "risk_indicators" | "status" | ...,
        "contains": "string_to_search_for",
        "confidence_min": 0.75,  // optional: only trigger if confidence >= this
        "agent_types": ["fincrime_screening"],  // optional: restrict to specific agents
        "operator": "contains" | "equals" | "greater_than" | "less_than",
        "value": ...  // for equals/greater_than/less_than
    }
    """

    def __init__(self, condition_json: Dict[str, Any]):
        self.field = condition_json.get("field", "")
        self.contains = condition_json.get("contains", "")
        self.confidence_min = condition_json.get("confidence_min")
        self.agent_types = condition_json.get("agent_types", [])
        self.operator = condition_json.get("operator", "contains")
        self.value = condition_json.get("value")

    def evaluate(self, output: AgentOutputBase) -> tuple[bool, Optional[str]]:
        """
        Evaluate this condition against an agent output.
        Returns (triggered, trigger_data_description).
        """
        # Check agent type restriction
        if self.agent_types:
            if output.agent_type.value not in self.agent_types:
                return False, None

        # Check confidence minimum
        if self.confidence_min is not None:
            if output.confidence_score < self.confidence_min:
                return False, None

        # Get the field value to check
        field_value = self._get_field_value(output)
        if field_value is None:
            return False, None

        # Apply operator
        if self.operator == "contains":
            return self._check_contains(field_value)
        elif self.operator == "equals":
            triggered = str(field_value).lower() == str(self.value).lower()
            return triggered, f"{self.field}={field_value}" if triggered else None
        elif self.operator == "greater_than":
            try:
                triggered = float(field_value) > float(self.value)
                return triggered, f"{self.field}={field_value}" if triggered else None
            except (ValueError, TypeError):
                return False, None
        elif self.operator == "less_than":
            try:
                triggered = float(field_value) < float(self.value)
                return triggered, f"{self.field}={field_value}" if triggered else None
            except (ValueError, TypeError):
                return False, None

        return False, None

    def _get_field_value(self, output: AgentOutputBase) -> Any:
        """Extract field value from agent output."""
        if self.field == "findings":
            return json.dumps([f.model_dump() for f in output.findings])
        elif self.field == "issues":
            return json.dumps([i.model_dump() for i in output.detected_issues])
        elif self.field == "risk_indicators":
            return json.dumps([r.model_dump() for r in output.risk_indicators])
        elif self.field == "evidence":
            return json.dumps([e.model_dump() for e in output.evidence])
        elif self.field == "status":
            return output.status.value
        elif self.field == "confidence_score":
            return output.confidence_score
        elif self.field == "recommendation":
            return output.recommendation or ""
        elif self.field == "escalation_flag":
            return output.escalation_flag
        else:
            # Try to get from agent-specific attributes
            return getattr(output, self.field, None)

    def _check_contains(self, field_value: Any) -> tuple[bool, Optional[str]]:
        """Check if field value contains the search string."""
        search = self.contains.lower()
        if isinstance(field_value, str):
            triggered = search in field_value.lower()
            if triggered:
                # Extract a snippet around the match for context
                idx = field_value.lower().find(search)
                start = max(0, idx - 30)
                end = min(len(field_value), idx + len(search) + 30)
                snippet = field_value[start:end]
                return True, f"Found '{self.contains}' in {self.field}: ...{snippet}..."
        elif isinstance(field_value, bool):
            triggered = str(field_value).lower() == search
            return triggered, f"{self.field}={field_value}" if triggered else None
        elif isinstance(field_value, (list, dict)):
            serialized = json.dumps(field_value).lower() if not isinstance(field_value, str) else field_value.lower()
            triggered = search in serialized
            return triggered, f"Found '{self.contains}' in {self.field}" if triggered else None

        return False, None


class Rule:
    """A single compliance rule."""

    def __init__(
        self,
        rule_id: str,
        rule_name: str,
        rule_category: str,
        description: str,
        condition: RuleCondition,
        action: RuleAction,
        severity: Severity,
        overrides_ai: bool,
        applies_to: List[str],
        priority: int,
        is_active: bool = True,
    ):
        self.rule_id = rule_id
        self.rule_name = rule_name
        self.rule_category = rule_category
        self.description = description
        self.condition = condition
        self.action = action
        self.severity = severity
        self.overrides_ai = overrides_ai
        self.applies_to = applies_to
        self.priority = priority
        self.is_active = is_active


class RulesEngine:
    """
    Evaluates compliance rules against agent outputs.
    Rules are sorted by priority (lower = higher priority).
    """

    def __init__(self):
        self.rules: List[Rule] = []
        self.evaluation_count = 0
        self.trigger_count = 0

    def load_rules_from_config(self, rules_config: List[Dict[str, Any]]):
        """
        Load rules from database config rows.
        Each row should have: id, rule_name, rule_category, description,
        condition_json, action, severity, overrides_ai, applies_to, priority, is_active
        """
        self.rules = []
        for cfg in rules_config:
            if not cfg.get("is_active", True):
                continue

            try:
                condition_dict = (
                    json.loads(cfg["condition_json"])
                    if isinstance(cfg["condition_json"], str)
                    else cfg["condition_json"]
                )
                applies_to = (
                    json.loads(cfg.get("applies_to", '["all"]'))
                    if isinstance(cfg.get("applies_to", '["all"]'), str)
                    else cfg.get("applies_to", ["all"])
                )

                rule = Rule(
                    rule_id=cfg["id"],
                    rule_name=cfg["rule_name"],
                    rule_category=cfg["rule_category"],
                    description=cfg.get("description", ""),
                    condition=RuleCondition(condition_dict),
                    action=RuleAction(cfg["action"]),
                    severity=Severity(cfg["severity"]),
                    overrides_ai=bool(cfg.get("overrides_ai", False)),
                    applies_to=applies_to,
                    priority=cfg.get("priority", 100),
                    is_active=True,
                )
                self.rules.append(rule)
            except Exception as e:
                logger.error("Failed to load rule '%s': %s", cfg.get("rule_name", "?"), e)

        # Sort by priority
        self.rules.sort(key=lambda r: r.priority)
        logger.info("Loaded %d active rules", len(self.rules))

    def load_default_rules(self):
        """Load hardcoded default rules (fallback if DB not available)."""
        defaults = [
            {
                "id": "rule_sanctions_hit",
                "rule_name": "sanctions_hit_auto_escalate",
                "rule_category": "sanctions",
                "description": "Any sanctions match triggers automatic escalation to MLRO",
                "condition_json": {"field": "findings", "contains": "sanctions_match", "confidence_min": 0.7},
                "action": "escalate",
                "severity": "critical",
                "overrides_ai": True,
                "applies_to": '["all"]',
                "priority": 10,
                "is_active": True,
            },
            {
                "id": "rule_confirmed_pep",
                "rule_name": "confirmed_pep_enhanced_review",
                "rule_category": "pep",
                "description": "Confirmed PEP status requires enhanced due diligence review",
                "condition_json": {"field": "findings", "contains": "pep_confirmed", "confidence_min": 0.8},
                "action": "require_review",
                "severity": "high",
                "overrides_ai": True,
                "applies_to": '["all"]',
                "priority": 20,
                "is_active": True,
            },
            {
                "id": "rule_missing_ubo",
                "rule_name": "missing_ubo_block_approval",
                "rule_category": "ubo",
                "description": "Missing UBO identification blocks approval",
                "condition_json": {"field": "issues", "contains": "ubo_not_identified"},
                "action": "block_approval",
                "severity": "critical",
                "overrides_ai": True,
                "applies_to": '["all"]',
                "priority": 15,
                "is_active": True,
            },
            {
                "id": "rule_company_not_found",
                "rule_name": "company_not_in_registry",
                "rule_category": "registry",
                "description": "Company not found in official registry requires hold",
                "condition_json": {"field": "findings", "contains": "company_not_found"},
                "action": "hold",
                "severity": "high",
                "overrides_ai": True,
                "applies_to": '["all"]',
                "priority": 25,
                "is_active": True,
            },
            {
                "id": "rule_doc_tampering",
                "rule_name": "document_tampering_detected",
                "rule_category": "document_integrity",
                "description": "Document tampering signals trigger rejection or escalation",
                "condition_json": {"field": "issues", "contains": "tampering_detected", "confidence_min": 0.75},
                "action": "reject",
                "severity": "critical",
                "overrides_ai": True,
                "applies_to": '["all"]',
                "priority": 5,
                "is_active": True,
            },
            {
                "id": "rule_high_risk_jurisdiction",
                "rule_name": "high_risk_jurisdiction_review",
                "rule_category": "jurisdiction",
                "description": "Exposure to high-risk jurisdictions requires mandatory review",
                "condition_json": {"field": "risk_indicators", "contains": "high_risk_jurisdiction"},
                "action": "require_review",
                "severity": "high",
                "overrides_ai": True,
                "applies_to": '["all"]',
                "priority": 30,
                "is_active": True,
            },
            {
                "id": "rule_adverse_media_severe",
                "rule_name": "severe_adverse_media",
                "rule_category": "sanctions",
                "description": "Severe adverse media findings require immediate escalation",
                "condition_json": {"field": "findings", "contains": "adverse_media_severe"},
                "action": "escalate",
                "severity": "critical",
                "overrides_ai": True,
                "applies_to": '["all"]',
                "priority": 8,
                "is_active": True,
            },
            {
                "id": "rule_shell_company",
                "rule_name": "shell_company_risk",
                "rule_category": "risk_level",
                "description": "Shell company characteristics detected",
                "condition_json": {"field": "risk_indicators", "contains": "shell_company_indicators"},
                "action": "escalate",
                "severity": "critical",
                "overrides_ai": True,
                "applies_to": '["all"]',
                "priority": 12,
                "is_active": True,
            },
        ]
        self.load_rules_from_config(defaults)

    def evaluate(
        self,
        outputs: List[AgentOutputBase],
        pipeline_id: str,
        application_id: str,
    ) -> List[RuleEvaluation]:
        """
        Evaluate all rules against all agent outputs.
        Returns list of rule evaluations (both triggered and not).
        """
        self.evaluation_count += 1
        evaluations: List[RuleEvaluation] = []

        for rule in self.rules:
            if not rule.is_active:
                continue

            triggered = False
            trigger_data = None
            matched_run_id = None

            for output in outputs:
                # Check if rule applies to this agent type
                if "all" not in rule.applies_to and output.agent_type.value not in rule.applies_to:
                    continue

                is_triggered, data = rule.condition.evaluate(output)
                if is_triggered:
                    triggered = True
                    trigger_data = data
                    matched_run_id = output.run_id
                    break

            if triggered:
                self.trigger_count += 1

            evaluation = RuleEvaluation(
                evaluation_id=str(uuid4()),
                pipeline_id=pipeline_id,
                application_id=application_id,
                run_id=matched_run_id,
                rule_id=rule.rule_id,
                rule_name=rule.rule_name,
                rule_category=rule.rule_category,
                triggered=triggered,
                trigger_data={"description": trigger_data} if trigger_data else None,
                action_taken=rule.action if triggered else RuleAction.NO_ACTION,
                overrides_ai=rule.overrides_ai if triggered else False,
                rule_recommendation=rule.description if triggered else None,
                severity=rule.severity if triggered else None,
            )
            evaluations.append(evaluation)

            if triggered:
                logger.warning(
                    "Rule TRIGGERED: %s [%s] app=%s action=%s severity=%s",
                    rule.rule_name, rule.rule_category,
                    application_id, rule.action.value, rule.severity.value
                )

        return evaluations

    def get_blocking_rules(self, evaluations: List[RuleEvaluation]) -> List[RuleEvaluation]:
        """Filter evaluations to only blocking rules that were triggered."""
        blocking_actions = {
            RuleAction.BLOCK_APPROVAL,
            RuleAction.REJECT,
            RuleAction.ESCALATE,
        }
        return [
            e for e in evaluations
            if e.triggered and e.action_taken in blocking_actions
        ]

    def get_highest_severity_action(
        self, evaluations: List[RuleEvaluation]
    ) -> Optional[RuleAction]:
        """Get the most severe action from triggered rules."""
        triggered = [e for e in evaluations if e.triggered]
        if not triggered:
            return None

        severity_order = {
            Severity.CRITICAL: 4,
            Severity.HIGH: 3,
            Severity.MEDIUM: 2,
            Severity.LOW: 1,
            Severity.INFO: 0,
        }

        triggered.sort(key=lambda e: severity_order.get(e.severity, 0), reverse=True)
        return triggered[0].action_taken

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_rules": len(self.rules),
            "total_evaluations": self.evaluation_count,
            "total_triggers": self.trigger_count,
            "trigger_rate": self.trigger_count / max(self.evaluation_count * len(self.rules), 1),
        }
