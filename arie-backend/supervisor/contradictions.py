"""
ARIE Finance — AI Agent Supervisor: Contradiction Detector
============================================================
Detects logical contradictions between agent outputs.

Contradiction taxonomy:
  - identity_vs_registry:        Doc integrity says verified, registry says not found
  - ubo_vs_risk:                 UBO missing/low confidence but risk says approve
  - screening_vs_plausibility:   Screening flags severe issues but business model says clean
  - registry_vs_memo:            Registry says mismatch but memo says all validated
  - document_vs_identity:        Document tampering but identity verified
  - monitoring_vs_onboarding:    Monitoring flags vs initial onboarding data
  - risk_level_mismatch:         Agents disagree on risk level
  - temporal_inconsistency:      Time-based contradictions
  - data_completeness_conflict:  One says complete, another says missing

Each contradiction gets a severity score [0.0, 1.0] based on:
  - Regulatory impact (higher for sanctions, UBO, PEP)
  - Confidence gap between contradicting agents
  - Whether the contradiction involves blocking findings
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .schemas import (
    AgentOutputBase,
    AgentType,
    Contradiction,
    ContradictionCategory,
    Severity,
)

logger = logging.getLogger("arie.supervisor.contradictions")


# ═══════════════════════════════════════════════════════════
# CONTRADICTION RULES
# ═══════════════════════════════════════════════════════════

class ContradictionRule:
    """Defines a single contradiction detection rule."""

    def __init__(
        self,
        name: str,
        category: ContradictionCategory,
        agent_a_type: AgentType,
        agent_b_type: AgentType,
        description_template: str,
        base_severity: Severity,
        base_severity_score: float,
        check_fn_name: str,
    ):
        self.name = name
        self.category = category
        self.agent_a_type = agent_a_type
        self.agent_b_type = agent_b_type
        self.description_template = description_template
        self.base_severity = base_severity
        self.base_severity_score = base_severity_score
        self.check_fn_name = check_fn_name


# ═══════════════════════════════════════════════════════════
# DETECTOR
# ═══════════════════════════════════════════════════════════

class ContradictionDetector:
    """
    Detects contradictions between agent outputs for a single case.
    """

    def __init__(self):
        self.detection_count = 0
        self.contradiction_count = 0

    def detect_all(
        self,
        outputs: Dict[AgentType, AgentOutputBase],
        pipeline_id: str,
        application_id: str,
    ) -> List[Contradiction]:
        """
        Run all contradiction checks across the provided agent outputs.

        Args:
            outputs: Dict mapping agent_type to validated output
            pipeline_id: Current pipeline ID
            application_id: Current application ID

        Returns:
            List of detected contradictions
        """
        self.detection_count += 1
        contradictions: List[Contradiction] = []

        # Only check pairs where both agents have outputs
        checks = [
            self._check_identity_vs_registry,
            self._check_ubo_vs_risk,
            self._check_screening_vs_plausibility,
            self._check_registry_vs_memo,
            self._check_document_vs_identity,
            self._check_risk_level_mismatch,
            self._check_data_completeness_conflict,
            self._check_screening_vs_memo,
            self._check_ubo_vs_memo,
        ]

        for check_fn in checks:
            try:
                result = check_fn(outputs, pipeline_id, application_id)
                if result:
                    contradictions.extend(result if isinstance(result, list) else [result])
            except Exception as e:
                logger.error("Contradiction check %s failed: %s", check_fn.__name__, e)

        self.contradiction_count += len(contradictions)
        logger.info(
            "Contradiction detection: app=%s found=%d",
            application_id, len(contradictions)
        )
        return contradictions

    # ─── Individual checks ──────────────────────────────────

    def _check_identity_vs_registry(
        self, outputs: Dict[AgentType, AgentOutputBase],
        pipeline_id: str, application_id: str
    ) -> Optional[Contradiction]:
        """
        Identity says verified, but registry says company not found.
        """
        identity = outputs.get(AgentType.IDENTITY_DOCUMENT_INTEGRITY)
        registry = outputs.get(AgentType.EXTERNAL_DATABASE_VERIFICATION)
        if not identity or not registry:
            return None

        identity_clean = identity.status.value == "clean"
        registry_data = getattr(registry, "company_found", None)

        if identity_clean and registry_data is False:
            severity_score = self._calc_severity_score(
                base=0.85, confidence_gap=abs(identity.confidence_score - registry.confidence_score)
            )
            return Contradiction(
                pipeline_id=pipeline_id,
                application_id=application_id,
                contradiction_type="identity_verified_but_company_not_found",
                contradiction_category=ContradictionCategory.IDENTITY_VS_REGISTRY,
                severity=Severity.HIGH,
                severity_score=severity_score,
                agent_a_run_id=identity.run_id,
                agent_a_type=identity.agent_type,
                agent_a_finding="Identity documents verified as authentic",
                agent_b_run_id=registry.run_id,
                agent_b_type=registry.agent_type,
                agent_b_finding="Company not found in official registry",
                description=(
                    "Identity & Document Integrity Agent verified the documents as authentic, "
                    "but the Corporate Structure & UBO Mapping Agent could not find the company "
                    "in the official registry. This could indicate fraudulent documents or "
                    "an unregistered entity."
                ),
            )
        return None

    def _check_ubo_vs_risk(
        self, outputs: Dict[AgentType, AgentOutputBase],
        pipeline_id: str, application_id: str
    ) -> Optional[Contradiction]:
        """
        UBO mapping has low confidence or missing UBO, but risk recommends approval.
        """
        ubo = outputs.get(AgentType.CORPORATE_STRUCTURE_UBO)
        risk = outputs.get(AgentType.COMPLIANCE_MEMO_RISK)
        if not ubo or not risk:
            return None

        ubo_incomplete = (
            ubo.confidence_score < 0.65
            or getattr(ubo, "ubo_completeness", 1.0) is not None
            and getattr(ubo, "ubo_completeness", 1.0) < 0.75
        )

        risk_recommends_approve = False
        if risk.recommendation and "approv" in risk.recommendation.lower():
            risk_recommends_approve = True
        recommended_action = getattr(risk, "recommended_action", "")
        if recommended_action and "approv" in str(recommended_action).lower():
            risk_recommends_approve = True

        if ubo_incomplete and risk_recommends_approve:
            severity_score = self._calc_severity_score(
                base=0.90, confidence_gap=abs(ubo.confidence_score - risk.confidence_score)
            )
            return Contradiction(
                pipeline_id=pipeline_id,
                application_id=application_id,
                contradiction_type="ubo_incomplete_but_approval_recommended",
                contradiction_category=ContradictionCategory.UBO_VS_RISK,
                severity=Severity.CRITICAL,
                severity_score=severity_score,
                agent_a_run_id=ubo.run_id,
                agent_a_type=ubo.agent_type,
                agent_a_finding=f"UBO mapping incomplete (confidence: {ubo.confidence_score:.2f})",
                agent_b_run_id=risk.run_id,
                agent_b_type=risk.agent_type,
                agent_b_finding="Risk recommendation suggests approval",
                description=(
                    "The UBO Mapping Agent has low confidence or incomplete beneficial owner "
                    "identification, but the Risk Recommendation Agent suggests approval. "
                    "Under AML regulations, UBO identification is mandatory before approval."
                ),
            )
        return None

    def _check_screening_vs_plausibility(
        self, outputs: Dict[AgentType, AgentOutputBase],
        pipeline_id: str, application_id: str
    ) -> Optional[Contradiction]:
        """
        Screening flags severe adverse media, but the final memo/risk agent
        still presents the case as low concern.
        """
        screening = outputs.get(AgentType.FINCRIME_SCREENING)
        memo = outputs.get(AgentType.COMPLIANCE_MEMO_RISK)
        if not screening or not memo:
            return None

        has_severe_findings = (
            getattr(screening, "sanctions_match_found", False)
            or getattr(screening, "adverse_media_found", False)
        )
        screening_high_concern = has_severe_findings and screening.confidence_score > 0.7

        memo_low_concern = (
            memo.status.value == "clean"
            or (
                memo.confidence_score > 0.8
                and getattr(memo, "recommended_risk_level", "") in ("LOW", "MEDIUM", None)
                and not memo.risk_indicators
            )
        )

        if screening_high_concern and memo_low_concern:
            severity_score = self._calc_severity_score(
                base=0.80, confidence_gap=abs(screening.confidence_score - memo.confidence_score)
            )
            return Contradiction(
                pipeline_id=pipeline_id,
                application_id=application_id,
                contradiction_type="screening_flags_vs_business_clean",
                contradiction_category=ContradictionCategory.SCREENING_VS_PLAUSIBILITY,
                severity=Severity.HIGH,
                severity_score=severity_score,
                agent_a_run_id=screening.run_id,
                agent_a_type=screening.agent_type,
                agent_a_finding="Severe screening flags detected (sanctions/adverse media)",
                agent_b_run_id=memo.run_id,
                agent_b_type=memo.agent_type,
                agent_b_finding="Memo/risk recommendation output shows low concern",
                description=(
                    "The FinCrime Screening Agent detected severe adverse media or sanctions "
                    "flags, but the final memo and risk recommendation still present the case "
                    "as low concern. This requires manual review to assess whether the final "
                    "risk synthesis adequately considered the screening results."
                ),
            )
        return None

    def _check_registry_vs_memo(
        self, outputs: Dict[AgentType, AgentOutputBase],
        pipeline_id: str, application_id: str
    ) -> Optional[Contradiction]:
        """
        Registry verification says directors mismatch, but compliance memo
        says all information validated.
        """
        registry = outputs.get(AgentType.EXTERNAL_DATABASE_VERIFICATION)
        memo = outputs.get(AgentType.COMPLIANCE_MEMO_RISK)
        if not registry or not memo:
            return None

        has_discrepancies = bool(getattr(registry, "discrepancies", []))
        directors_match = getattr(registry, "directors_match", None)
        directors_mismatch = False
        if isinstance(directors_match, dict):
            directors_mismatch = not directors_match.get("match", True)
        elif directors_match is False:
            directors_mismatch = True

        registry_issues = has_discrepancies or directors_mismatch

        memo_validated = memo.status.value == "clean" and memo.confidence_score > 0.8

        if registry_issues and memo_validated:
            severity_score = self._calc_severity_score(
                base=0.75, confidence_gap=abs(registry.confidence_score - memo.confidence_score)
            )
            return Contradiction(
                pipeline_id=pipeline_id,
                application_id=application_id,
                contradiction_type="registry_mismatch_but_memo_validated",
                contradiction_category=ContradictionCategory.REGISTRY_VS_MEMO,
                severity=Severity.HIGH,
                severity_score=severity_score,
                agent_a_run_id=registry.run_id,
                agent_a_type=registry.agent_type,
                agent_a_finding="Registry discrepancies or directors mismatch detected",
                agent_b_run_id=memo.run_id,
                agent_b_type=memo.agent_type,
                agent_b_finding="Compliance memo states all information validated",
                description=(
                    "The Corporate Structure & UBO Mapping Agent found registry discrepancies "
                    "or director mismatches, but the Compliance Memo & Risk Recommendation Agent states all "
                    "information has been validated. The memo may not have incorporated "
                    "the registry verification results."
                ),
            )
        return None

    def _check_document_vs_identity(
        self, outputs: Dict[AgentType, AgentOutputBase],
        pipeline_id: str, application_id: str
    ) -> Optional[Contradiction]:
        """
        Document tampering detected, but identity still shows as verified.
        """
        identity = outputs.get(AgentType.IDENTITY_DOCUMENT_INTEGRITY)
        if not identity:
            return None

        has_tampering = bool(getattr(identity, "tampering_indicators", []))
        status_clean = identity.status.value == "clean"

        if has_tampering and status_clean:
            return Contradiction(
                pipeline_id=pipeline_id,
                application_id=application_id,
                contradiction_type="tampering_detected_but_status_clean",
                contradiction_category=ContradictionCategory.DOCUMENT_VS_IDENTITY,
                severity=Severity.CRITICAL,
                severity_score=0.95,
                agent_a_run_id=identity.run_id,
                agent_a_type=identity.agent_type,
                agent_a_finding="Document tampering indicators detected",
                agent_b_run_id=identity.run_id,
                agent_b_type=identity.agent_type,
                agent_b_finding="Overall status reported as 'clean'",
                description=(
                    "The Identity & Document Integrity Agent detected tampering indicators "
                    "in the documents but still reported an overall status of 'clean'. "
                    "This internal inconsistency requires immediate review."
                ),
            )
        return None

    def _check_risk_level_mismatch(
        self, outputs: Dict[AgentType, AgentOutputBase],
        pipeline_id: str, application_id: str
    ) -> List[Contradiction]:
        """
        Check if agents disagree significantly on risk assessment.
        Compare agents that produce risk-related outputs.
        """
        contradictions = []
        risk_assessments: Dict[AgentType, str] = {}

        # Collect risk-related signals
        for agent_type, output in outputs.items():
            risk_level = None
            if hasattr(output, "recommended_risk_level") and output.recommended_risk_level:
                risk_level = output.recommended_risk_level
            elif hasattr(output, "industry_risk_level") and output.industry_risk_level:
                risk_level = output.industry_risk_level
            elif output.risk_indicators:
                # Derive from risk indicators
                levels = [ri.risk_level for ri in output.risk_indicators]
                if "critical" in levels or "high" in levels:
                    risk_level = "high"
                elif "medium" in levels:
                    risk_level = "medium"
                else:
                    risk_level = "low"

            if risk_level:
                risk_assessments[agent_type] = risk_level.lower()

        # Check for mismatches
        risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        agent_types = list(risk_assessments.keys())

        for i in range(len(agent_types)):
            for j in range(i + 1, len(agent_types)):
                a_type = agent_types[i]
                b_type = agent_types[j]
                a_level = risk_assessments[a_type]
                b_level = risk_assessments[b_type]

                a_order = risk_order.get(a_level, 0)
                b_order = risk_order.get(b_level, 0)
                gap = abs(a_order - b_order)

                if gap >= 2:  # At least 2 levels apart (e.g., low vs high)
                    severity = Severity.HIGH if gap >= 3 else Severity.MEDIUM
                    contradictions.append(Contradiction(
                        pipeline_id=pipeline_id,
                        application_id=application_id,
                        contradiction_type="risk_level_disagreement",
                        contradiction_category=ContradictionCategory.RISK_LEVEL_MISMATCH,
                        severity=severity,
                        severity_score=min(0.5 + gap * 0.15, 1.0),
                        agent_a_run_id=outputs[a_type].run_id,
                        agent_a_type=a_type,
                        agent_a_finding=f"Risk assessment: {a_level}",
                        agent_b_run_id=outputs[b_type].run_id,
                        agent_b_type=b_type,
                        agent_b_finding=f"Risk assessment: {b_level}",
                        description=(
                            f"{a_type.value} assessed risk as '{a_level}' while "
                            f"{b_type.value} assessed it as '{b_level}'. "
                            f"This {gap}-level disagreement requires reconciliation."
                        ),
                    ))

        return contradictions

    def _check_data_completeness_conflict(
        self, outputs: Dict[AgentType, AgentOutputBase],
        pipeline_id: str, application_id: str
    ) -> Optional[Contradiction]:
        """
        One agent reports data is complete, another reports missing data.
        """
        memo = outputs.get(AgentType.COMPLIANCE_MEMO_RISK)
        ubo = outputs.get(AgentType.CORPORATE_STRUCTURE_UBO)
        if not memo or not ubo:
            return None

        memo_data_quality = getattr(memo, "data_quality_assessment", None)
        memo_says_complete = False
        if isinstance(memo_data_quality, dict):
            memo_says_complete = memo_data_quality.get("complete", False)
        elif memo.status.value == "clean":
            memo_says_complete = True

        ubo_has_missing = any(
            issue.issue_type in ("missing_ubo", "incomplete_ownership", "ubo_not_identified")
            for issue in ubo.detected_issues
        )

        if memo_says_complete and ubo_has_missing:
            return Contradiction(
                pipeline_id=pipeline_id,
                application_id=application_id,
                contradiction_type="data_completeness_disagreement",
                contradiction_category=ContradictionCategory.DATA_COMPLETENESS_CONFLICT,
                severity=Severity.HIGH,
                severity_score=0.80,
                agent_a_run_id=memo.run_id,
                agent_a_type=memo.agent_type,
                agent_a_finding="Data assessed as complete",
                agent_b_run_id=ubo.run_id,
                agent_b_type=ubo.agent_type,
                agent_b_finding="Missing UBO or incomplete ownership data",
                description=(
                    "The Compliance Memo & Risk Recommendation Agent considers the data complete, but the "
                    "UBO Mapping Agent reports missing beneficial owner information or "
                    "incomplete ownership structure."
                ),
            )
        return None

    def _check_screening_vs_memo(
        self, outputs: Dict[AgentType, AgentOutputBase],
        pipeline_id: str, application_id: str
    ) -> Optional[Contradiction]:
        """
        Screening found sanctions/PEP but memo doesn't reflect this in risk level.
        """
        screening = outputs.get(AgentType.FINCRIME_SCREENING)
        memo = outputs.get(AgentType.COMPLIANCE_MEMO_RISK)
        if not screening or not memo:
            return None

        sanctions_found = getattr(screening, "sanctions_match_found", False)
        pep_found = getattr(screening, "pep_match_found", False)

        recommended_risk = getattr(memo, "recommended_risk_level", "")
        memo_low_risk = recommended_risk and recommended_risk.lower() in ("low", "standard")

        if (sanctions_found or pep_found) and memo_low_risk:
            return Contradiction(
                pipeline_id=pipeline_id,
                application_id=application_id,
                contradiction_type="screening_hits_but_memo_low_risk",
                contradiction_category=ContradictionCategory.SCREENING_VS_PLAUSIBILITY,
                severity=Severity.CRITICAL,
                severity_score=0.95,
                agent_a_run_id=screening.run_id,
                agent_a_type=screening.agent_type,
                agent_a_finding=f"Sanctions match: {sanctions_found}, PEP match: {pep_found}",
                agent_b_run_id=memo.run_id,
                agent_b_type=memo.agent_type,
                agent_b_finding=f"Recommended risk level: {recommended_risk}",
                description=(
                    "The FinCrime Screening Agent found sanctions or PEP matches, but the "
                    "Compliance Memo & Risk Recommendation Agent recommends a low/standard risk level. Sanctions and "
                    "PEP matches should always result in elevated risk assessment."
                ),
            )
        return None

    def _check_ubo_vs_memo(
        self, outputs: Dict[AgentType, AgentOutputBase],
        pipeline_id: str, application_id: str
    ) -> Optional[Contradiction]:
        """
        UBO has shell company indicators but memo doesn't flag high risk.
        """
        ubo = outputs.get(AgentType.CORPORATE_STRUCTURE_UBO)
        memo = outputs.get(AgentType.COMPLIANCE_MEMO_RISK)
        if not ubo or not memo:
            return None

        shell_indicators = getattr(ubo, "shell_company_indicators", [])
        circular = getattr(ubo, "circular_ownership_detected", False)

        has_structure_concerns = bool(shell_indicators) or circular

        recommended_risk = getattr(memo, "recommended_risk_level", "")
        memo_low_risk = recommended_risk and recommended_risk.lower() in ("low", "standard")

        if has_structure_concerns and memo_low_risk:
            return Contradiction(
                pipeline_id=pipeline_id,
                application_id=application_id,
                contradiction_type="shell_indicators_but_memo_low_risk",
                contradiction_category=ContradictionCategory.UBO_VS_RISK,
                severity=Severity.HIGH,
                severity_score=0.85,
                agent_a_run_id=ubo.run_id,
                agent_a_type=ubo.agent_type,
                agent_a_finding=f"Shell company indicators: {shell_indicators}, Circular ownership: {circular}",
                agent_b_run_id=memo.run_id,
                agent_b_type=memo.agent_type,
                agent_b_finding=f"Recommended risk level: {recommended_risk}",
                description=(
                    "The UBO Mapping Agent detected shell company indicators or circular "
                    "ownership structures, but the Compliance Memo suggests a low/standard "
                    "risk level. Complex structures require elevated risk assessment."
                ),
            )
        return None

    # ─── Helpers ──────────────────────────────────────────

    @staticmethod
    def _calc_severity_score(base: float, confidence_gap: float) -> float:
        """Calculate severity score adjusted by confidence gap between agents."""
        adjusted = base + (confidence_gap * 0.15)
        return round(min(1.0, max(0.0, adjusted)), 3)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_detections": self.detection_count,
            "contradictions_found": self.contradiction_count,
            "avg_per_case": (
                self.contradiction_count / max(self.detection_count, 1)
            ),
        }
