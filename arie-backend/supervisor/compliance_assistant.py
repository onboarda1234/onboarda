"""
ARIE Finance — AI Compliance Assistant
========================================
Embedded AI assistant for compliance officers.
Analyzes agent outputs, summarizes risks, and provides
structured recommendations — but NEVER makes final decisions.

The assistant operates under strict constraints:
  - Never approves or rejects a client
  - Does not fabricate data
  - Bases conclusions only on available evidence
  - Clearly identifies uncertainty
  - Flags contradictions between agent outputs
  - Provides explanations suitable for regulatory review
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .schemas import (
    AgentOutputBase,
    AgentType,
    ComplianceAssistantOutput,
    Contradiction,
    RuleEvaluation,
    Severity,
)
from .supervisor import SupervisorPipelineResult

logger = logging.getLogger("arie.supervisor.compliance_assistant")


# ═══════════════════════════════════════════════════════════
# SYSTEM PROMPT — Used when calling the LLM
# ═══════════════════════════════════════════════════════════

COMPLIANCE_ASSISTANT_SYSTEM_PROMPT = """You are an AI Compliance Assistant embedded inside a regulated onboarding and monitoring platform used by financial institutions, corporate service providers, and payment companies.

Your role is to assist compliance officers in reviewing client onboarding applications, monitoring alerts, and periodic reviews.

You do NOT make final compliance decisions. You analyze information, summarize risks, and provide structured recommendations.

You must prioritize accuracy, explainability, and regulatory defensibility.

CONTEXT
The platform performs:
- Client onboarding (KYC / AML)
- Identity verification
- Corporate registry verification
- UBO mapping
- Sanctions / PEP screening
- Adverse media monitoring
- Ongoing monitoring
- Periodic review preparation

Multiple AI agents produce structured outputs for each case.

You have access to:
- Agent outputs
- Client documents
- Screening results
- Registry verification results
- Ownership structure
- Risk scores
- Previous compliance decisions
- Monitoring alerts
- Periodic review history

YOUR TASKS
When reviewing a client case, you must:
1. Summarize the client profile
2. Identify key risk indicators
3. Highlight inconsistencies or missing information
4. Explain screening results
5. Analyze ownership structure
6. Evaluate business model plausibility
7. Summarize monitoring alerts (if applicable)
8. Prepare compliance review summaries
9. Suggest a recommended review outcome

OUTPUT FORMAT
Always produce structured output with the following sections:

CLIENT SUMMARY
Brief description of: client type, jurisdiction, business activity, ownership structure.

KEY FINDINGS
List important facts discovered during analysis.

SCREENING SUMMARY
Summarize sanctions, PEP, and adverse media results.

RISK INDICATORS
Categorize risks as: Low, Medium, High.

DATA INCONSISTENCIES
List mismatches or missing information.

AI ANALYSIS
Explain why certain risk indicators matter.

RECOMMENDED ACTION
Provide one of the following recommendations:
- Proceed with Standard Due Diligence
- Request Additional Information
- Escalate to Enhanced Due Diligence
- Escalate to Senior Compliance Review

CONFIDENCE SCORE
Provide a confidence score between 0 and 1.

CONSTRAINTS
You must follow these rules:
1. Never approve or reject a client.
2. Do not fabricate data.
3. Base conclusions only on available evidence.
4. Clearly identify uncertainty.
5. If critical information is missing, recommend requesting more information.
6. Flag contradictions between agent outputs.
7. Provide explanations suitable for regulatory review.

TONE
Be clear, precise, and professional.
Avoid speculation.
Focus on compliance relevance.

GOAL
Your objective is to help compliance officers review cases faster while maintaining regulatory standards."""


class ComplianceAssistant:
    """
    AI Compliance Assistant that generates structured review summaries.

    Can work in two modes:
      1. Pure rule-based: Generates summaries from agent outputs without LLM
      2. LLM-enhanced: Uses the system prompt with an LLM for natural language analysis
    """

    def __init__(self):
        self.version = "1.0.0"

    def get_system_prompt(self) -> str:
        """Return the system prompt for LLM integration."""
        return COMPLIANCE_ASSISTANT_SYSTEM_PROMPT

    def generate_review_summary(
        self,
        pipeline_result: SupervisorPipelineResult,
        client_data: Optional[Dict[str, Any]] = None,
    ) -> ComplianceAssistantOutput:
        """
        Generate a structured compliance review summary from pipeline results.
        This is the rule-based mode — no LLM call needed.

        For LLM-enhanced mode, use build_llm_context() to prepare the prompt,
        then parse the LLM response into ComplianceAssistantOutput.
        """
        app_id = pipeline_result.application_id

        # ── Client Summary ──
        client_summary = self._build_client_summary(pipeline_result, client_data)

        # ── Key Findings ──
        key_findings = self._extract_key_findings(pipeline_result)

        # ── Screening Summary ──
        screening_summary = self._build_screening_summary(pipeline_result)

        # ── Risk Indicators ──
        risk_indicators = self._collect_risk_indicators(pipeline_result)

        # ── Data Inconsistencies ──
        inconsistencies = self._find_inconsistencies(pipeline_result)

        # ── AI Analysis ──
        ai_analysis = self._generate_analysis(pipeline_result)

        # ── Recommended Action ──
        recommended_action = self._determine_recommendation(pipeline_result)

        # ── Confidence Score ──
        confidence = (
            pipeline_result.case_aggregate.aggregate_confidence
            if pipeline_result.case_aggregate else 0.5
        )

        # ── Evidence ──
        evidence = self._collect_evidence(pipeline_result)

        # ── Caveats ──
        caveats = self._generate_caveats(pipeline_result)

        output = ComplianceAssistantOutput(
            assistant_version=self.version,
            application_id=app_id,
            review_type="onboarding",
            client_summary=client_summary,
            key_findings=key_findings,
            screening_summary=screening_summary,
            risk_indicators=risk_indicators,
            data_inconsistencies=inconsistencies,
            ai_analysis=ai_analysis,
            recommended_action=recommended_action,
            confidence_score=confidence,
            supporting_evidence=evidence,
            caveats=caveats,
        )

        return output

    def build_llm_context(
        self,
        pipeline_result: SupervisorPipelineResult,
        client_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Build the context string to send to an LLM along with the system prompt.
        The LLM will analyze this and produce a structured compliance review.
        """
        context_parts = []

        # Application info
        context_parts.append(f"APPLICATION ID: {pipeline_result.application_id}")
        context_parts.append(f"PIPELINE STATUS: {pipeline_result.status}")
        context_parts.append("")

        # Client data
        if client_data:
            context_parts.append("CLIENT DATA:")
            context_parts.append(json.dumps(client_data, indent=2, default=str))
            context_parts.append("")

        # Agent outputs
        context_parts.append("AGENT OUTPUTS:")
        for agent_type, output in pipeline_result.agent_outputs.items():
            context_parts.append(f"\n--- {agent_type.value} ---")
            context_parts.append(f"Status: {output.status.value}")
            context_parts.append(f"Confidence: {output.confidence_score:.3f}")
            context_parts.append(f"Findings ({len(output.findings)}):")
            for f in output.findings[:5]:
                context_parts.append(f"  - [{f.severity.value}] {f.title}: {f.description[:150]}")
            if output.detected_issues:
                context_parts.append(f"Issues ({len(output.detected_issues)}):")
                for i in output.detected_issues[:5]:
                    context_parts.append(
                        f"  - [{i.severity.value}] {i.title}: {i.description[:150]}"
                    )
            if output.risk_indicators:
                context_parts.append(f"Risk Indicators:")
                for r in output.risk_indicators[:5]:
                    context_parts.append(f"  - [{r.risk_level}] {r.description[:150]}")
            if output.recommendation:
                context_parts.append(f"Recommendation: {output.recommendation[:200]}")
            context_parts.append("")

        # Contradictions
        if pipeline_result.contradictions:
            context_parts.append("CONTRADICTIONS DETECTED:")
            for c in pipeline_result.contradictions:
                context_parts.append(
                    f"  - [{c.severity.value}] {c.contradiction_category.value}: "
                    f"{c.description[:200]}"
                )
            context_parts.append("")

        # Rules triggered
        triggered_rules = [r for r in pipeline_result.rule_evaluations if r.triggered]
        if triggered_rules:
            context_parts.append("COMPLIANCE RULES TRIGGERED:")
            for r in triggered_rules:
                context_parts.append(
                    f"  - [{r.severity.value if r.severity else 'N/A'}] {r.rule_name}: "
                    f"Action = {r.action_taken.value if r.action_taken else 'N/A'}"
                )
            context_parts.append("")

        # Case aggregate
        if pipeline_result.case_aggregate:
            agg = pipeline_result.case_aggregate
            context_parts.append("CASE AGGREGATE:")
            context_parts.append(f"  Aggregate Confidence: {agg.aggregate_confidence}")
            context_parts.append(
                f"  Confidence Routing: {agg.confidence_routing.value if agg.confidence_routing else 'N/A'}"
            )
            context_parts.append(f"  Escalation Required: {agg.escalation_required}")
            context_parts.append("")

        # Failed agents
        if pipeline_result.failed_agents:
            context_parts.append("FAILED AGENTS:")
            for fa in pipeline_result.failed_agents:
                context_parts.append(f"  - {fa['agent_type']}: {fa['reason']}")
            context_parts.append("")

        context_parts.append(
            "Please analyze this case and provide your structured compliance review "
            "following the output format specified in your instructions."
        )

        return "\n".join(context_parts)

    # ─── Internal helpers ──────────────────────────────────

    def _build_client_summary(
        self, result: SupervisorPipelineResult, client_data: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "application_id": result.application_id,
            "agents_completed": len(result.agent_outputs),
            "agents_failed": len(result.failed_agents),
        }
        if client_data:
            summary.update({
                "client_type": client_data.get("entity_type", "Unknown"),
                "jurisdiction": client_data.get("jurisdiction", "Unknown"),
                "business_activity": client_data.get("business_description", "Not provided"),
                "company_name": client_data.get("company_name", "Unknown"),
            })

        # Add from registry agent if available
        registry = result.agent_outputs.get(AgentType.EXTERNAL_DATABASE_VERIFICATION)
        if registry:
            summary["registry_status"] = getattr(registry, "company_status", None)
            summary["company_found_in_registry"] = getattr(registry, "company_found", None)

        return summary

    def _extract_key_findings(self, result: SupervisorPipelineResult) -> List[Dict[str, Any]]:
        findings = []
        for agent_type, output in result.agent_outputs.items():
            for f in output.findings:
                if f.severity in (Severity.CRITICAL, Severity.HIGH):
                    findings.append({
                        "source_agent": agent_type.value,
                        "title": f.title,
                        "severity": f.severity.value,
                        "description": f.description[:300],
                        "confidence": f.confidence,
                    })
        # Sort by severity
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        findings.sort(key=lambda x: severity_order.get(x["severity"], 99))
        return findings[:20]

    def _build_screening_summary(self, result: SupervisorPipelineResult) -> Dict[str, Any]:
        screening = result.agent_outputs.get(AgentType.FINCRIME_SCREENING)
        if not screening:
            return {"status": "not_run", "note": "FinCrime Screening Agent did not produce output"}

        return {
            "status": screening.status.value,
            "confidence": screening.confidence_score,
            "sanctions_match": getattr(screening, "sanctions_match_found", False),
            "pep_match": getattr(screening, "pep_match_found", False),
            "adverse_media": getattr(screening, "adverse_media_found", False),
            "highest_match_score": getattr(screening, "highest_match_score", None),
            "screened_entities_count": len(getattr(screening, "screened_entities", [])),
            "findings_count": len(screening.findings),
        }

    def _collect_risk_indicators(self, result: SupervisorPipelineResult) -> List[Dict[str, Any]]:
        indicators = []
        for agent_type, output in result.agent_outputs.items():
            for ri in output.risk_indicators:
                indicators.append({
                    "source_agent": agent_type.value,
                    "indicator_type": ri.indicator_type,
                    "risk_level": ri.risk_level,
                    "description": ri.description,
                })
        return indicators

    def _find_inconsistencies(self, result: SupervisorPipelineResult) -> List[Dict[str, Any]]:
        inconsistencies = []

        # Add contradictions as inconsistencies
        for c in result.contradictions:
            inconsistencies.append({
                "type": "agent_contradiction",
                "severity": c.severity.value,
                "agents": [c.agent_a_type.value, c.agent_b_type.value],
                "description": c.description[:300],
            })

        # Check for missing data across agents
        for agent_type, output in result.agent_outputs.items():
            for issue in output.detected_issues:
                if "missing" in issue.issue_type.lower() or "incomplete" in issue.issue_type.lower():
                    inconsistencies.append({
                        "type": "missing_data",
                        "severity": issue.severity.value,
                        "agent": agent_type.value,
                        "description": issue.description[:200],
                    })

        return inconsistencies

    def _generate_analysis(self, result: SupervisorPipelineResult) -> str:
        """Generate rule-based analysis text."""
        parts = []

        agg = result.case_aggregate
        if agg:
            parts.append(
                f"The aggregate confidence score for this case is "
                f"{agg.aggregate_confidence:.2f}, which places it in the "
                f"'{agg.confidence_routing.value if agg.confidence_routing else 'unknown'}' "
                f"routing category."
            )

        if result.contradictions:
            critical = sum(1 for c in result.contradictions if c.severity == Severity.CRITICAL)
            high = sum(1 for c in result.contradictions if c.severity == Severity.HIGH)
            parts.append(
                f"{len(result.contradictions)} contradiction(s) were detected between agents "
                f"({critical} critical, {high} high severity). "
                f"These require manual reconciliation before a decision can be made."
            )

        triggered = [r for r in result.rule_evaluations if r.triggered]
        if triggered:
            parts.append(
                f"{len(triggered)} compliance rule(s) were triggered. "
                + ". ".join(
                    f"'{r.rule_name}' ({r.severity.value if r.severity else 'N/A'})"
                    for r in triggered[:5]
                )
                + "."
            )

        if result.failed_agents:
            parts.append(
                f"{len(result.failed_agents)} agent(s) failed to produce output, "
                f"which may leave gaps in the risk assessment."
            )

        if not parts:
            parts.append("All agents completed successfully with no contradictions or rule triggers.")

        return " ".join(parts)

    def _determine_recommendation(self, result: SupervisorPipelineResult) -> str:
        """Determine recommended action based on pipeline results."""
        # Critical blocking issues → Senior review
        if result.blocking_issues:
            return "escalate_senior_review"

        # Critical contradictions → Enhanced DD
        critical_contradictions = [
            c for c in result.contradictions if c.severity == Severity.CRITICAL
        ]
        if critical_contradictions:
            return "escalate_enhanced_dd"

        # Missing data → Request info
        has_missing = False
        for output in result.agent_outputs.values():
            for issue in output.detected_issues:
                if "missing" in issue.issue_type.lower() and issue.blocking:
                    has_missing = True
        if has_missing:
            return "request_additional_information"

        # Low confidence → Enhanced DD
        if result.case_aggregate and result.case_aggregate.aggregate_confidence:
            if result.case_aggregate.aggregate_confidence < 0.65:
                return "escalate_enhanced_dd"
            elif result.case_aggregate.aggregate_confidence < 0.85:
                return "request_additional_information"

        return "proceed_standard_dd"

    def _collect_evidence(self, result: SupervisorPipelineResult) -> List[Dict[str, Any]]:
        evidence = []
        for agent_type, output in result.agent_outputs.items():
            for e in output.evidence[:3]:
                evidence.append({
                    "source_agent": agent_type.value,
                    "type": e.evidence_type,
                    "source": e.source,
                    "summary": e.content_summary[:200],
                    "verified": e.verified,
                })
        return evidence

    def _generate_caveats(self, result: SupervisorPipelineResult) -> List[str]:
        caveats = []

        if result.failed_agents:
            agents = ", ".join(fa["agent_type"] for fa in result.failed_agents)
            caveats.append(
                f"The following agents did not produce output: {agents}. "
                f"This review may be incomplete."
            )

        low_conf_agents = [
            at.value for at, output in result.agent_outputs.items()
            if output.confidence_score < 0.65
        ]
        if low_conf_agents:
            caveats.append(
                f"Low confidence outputs from: {', '.join(low_conf_agents)}. "
                f"Findings from these agents should be verified manually."
            )

        caveats.append(
            "This AI-generated summary is provided to assist compliance officer review. "
            "It does not constitute a compliance decision. All final decisions must be "
            "made by a qualified compliance officer."
        )

        return caveats
