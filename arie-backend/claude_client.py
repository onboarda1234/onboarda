#!/usr/bin/env python3
"""
Claude AI Integration Module for ARIE Finance Compliance Platform
==================================================================

Powers 5 onboarding AI functions in the compliance workflow:

1. Identity & Document Integrity Agent — document verification, OCR, validation, cross-document consistency
2. External Database Cross-Verification Agent — Registry lookups, OpenCorporates, Companies House, ADGM, DIFC verification
3. FinCrime Screening Interpretation Agent — Sanctions/PEP/adverse media analysis, false positive reduction, hit severity ranking
4. Corporate Structure & UBO Mapping Agent — Ownership chains, UBO identification, nominee detection, complex chain flagging
5. Compliance Memo & Risk Recommendation Agent — composite scoring, business plausibility assessment, risk routing, compliance memo generation

Usage:
    from claude_client import ClaudeClient
    client = ClaudeClient(api_key="sk-...", monthly_budget_usd=50.0)

    # Agent 1: Verify document
    result = client.verify_document(doc_type, file_name, person_name)

    # Agent 4: Analyze corporate structure
    result = client.analyze_corporate_structure(directors, ubos, jurisdiction)

    # Internal support for Agent 5: Assess business plausibility
    result = client.assess_business_plausibility(business_data, registry_data)

    # Agent 3: Interpret FinCrime screening
    result = client.interpret_fincrime_screening(screening_results, person_name, entity_type)

    # Agent 5: Score risk and generate compliance memo
    risk_result = client.score_risk(application_data)
    memo_result = client.generate_compliance_memo(application_data, agent_results)
"""

import os
import json
import logging
import re
import hashlib
import time
import random
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from config import (
    ANTHROPIC_API_KEY as _CFG_ANTHROPIC_API_KEY,
    CLAUDE_MOCK_MODE as _CFG_CLAUDE_MOCK_MODE,
    ENVIRONMENT as _CFG_ENVIRONMENT,
    IS_PRODUCTION as _CFG_IS_PRODUCTION,
    IS_DEMO as _CFG_IS_DEMO,
    IS_STAGING as _CFG_IS_STAGING,
    ARIE_MODEL_FAST as _CFG_ARIE_MODEL_FAST,
    ARIE_MODEL_THOROUGH as _CFG_ARIE_MODEL_THOROUGH,
    AI_CONFIDENCE_THRESHOLD as _CFG_AI_CONFIDENCE_THRESHOLD,
)

# C-03: Pydantic validation for AI outputs
try:
    from pydantic import BaseModel, Field, field_validator, ValidationError
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    BaseModel = None
    ValidationError = None

# ── Persistent budget tracking via production_controls.UsageCapManager ──
# This records Claude usage to the database so budget enforcement is durable
# across requests, processes, and restarts. Falls back gracefully if unavailable.

def _record_persistent_usage(model: str, input_tokens: int, output_tokens: int, method: str = ""):
    """Record Claude API usage to the persistent budget store (database-backed)."""
    try:
        from production_controls import usage_cap_manager
        # Pricing per 1M tokens (matching UsageTracker pricing)
        pricing = {
            "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
            "claude-opus-4-6": {"input": 15.0, "output": 45.0},
        }
        model_pricing = pricing.get(model, {"input": 3.0, "output": 15.0})
        cost = (input_tokens * model_pricing["input"] / 1_000_000
                + output_tokens * model_pricing["output"] / 1_000_000)
        desc = f"{model}:{method} in={input_tokens} out={output_tokens}"
        usage_cap_manager.record_usage("CLAUDE", cost, desc)
    except Exception as e:
        logging.getLogger("claude_client").debug(f"Persistent usage recording skipped: {e}")


def _check_persistent_budget(estimated_cost: float = 0.01) -> bool:
    """Check if Claude budget allows another request. Returns True if within budget."""
    try:
        from production_controls import usage_cap_manager
        return usage_cap_manager.check_budget("CLAUDE", estimated_cost)
    except Exception:
        return True  # Fail-open: if budget store unavailable, allow the request

# ── C-03: AI Output Validation Schemas ─────────────────────────
if PYDANTIC_AVAILABLE:
    class RiskDimensionSchema(BaseModel):
        """Schema for a single risk dimension in Agent 5 output."""
        score: int = Field(ge=0, le=100, description="Risk score 0-100")
        factors: List[str] = Field(default_factory=list)

    class RiskScoreSchema(BaseModel):
        """Agent 5 (Part 1): Risk scoring output validation."""
        overall_score: int = Field(ge=0, le=100)
        risk_level: str = Field(pattern=r'^(LOW|MEDIUM|HIGH|VERY_HIGH)$')
        dimensions: Dict[str, Any] = Field(default_factory=dict)
        flags: List[str] = Field(default_factory=list)
        recommendation: str = Field(pattern=r'^(APPROVE|REVIEW|EDD|REJECT)$')

    class DocumentVerificationSchema(BaseModel):
        """Agent 1: Document verification output validation."""
        checks: List[Dict[str, Any]] = Field(default_factory=list)
        overall: str = Field(default="flagged")
        confidence: float = Field(ge=0.0, le=1.0, default=0.0)
        red_flags: List[str] = Field(default_factory=list)

    class CorporateStructureSchema(BaseModel):
        """Agent 4: Corporate structure analysis output validation."""
        complexity_level: str = Field(default="")
        ubos_identified: List[Dict[str, Any]] = Field(default_factory=list)
        risk_indicators: List[str] = Field(default_factory=list)
        nominee_structures: bool = False

    class BusinessPlausibilitySchema(BaseModel):
        """Internal business plausibility output validation supporting Agent 5."""
        plausibility_score: float = Field(ge=0.0, le=1.0, default=0.5)
        concerns: List[str] = Field(default_factory=list)
        sector_alignment: str = Field(default="")
        recommendation: str = Field(default="")

    class FinCrimeScreeningSchema(BaseModel):
        """Agent 3: FinCrime screening interpretation output validation."""
        total_hits: int = Field(ge=0, default=0)
        confirmed_matches: int = Field(ge=0, default=0)
        false_positives: int = Field(ge=0, default=0)
        hit_details: List[Dict[str, Any]] = Field(default_factory=list)
        recommendation: str = Field(default="")

    class ComplianceMemoSchema(BaseModel):
        """Agent 5 (Part 2): Compliance memo output validation — regulator-grade 11-section structure."""
        sections: Dict[str, Any] = Field(description="11 mandatory memo sections")
        metadata: Dict[str, Any] = Field(description="Risk rating, score, confidence, recommendation, findings, conditions, checklist")

    # Map agent method names to their validation schemas
    _AGENT_SCHEMAS = {
        "score_risk": RiskScoreSchema,
        "verify_document": DocumentVerificationSchema,
        "analyze_corporate_structure": CorporateStructureSchema,
        "assess_business_plausibility": BusinessPlausibilitySchema,
        "interpret_fincrime_screening": FinCrimeScreeningSchema,
        "generate_compliance_memo": ComplianceMemoSchema,
    }
else:
    _AGENT_SCHEMAS = {}


# Try to import anthropic library
try:
    from anthropic import Anthropic, APIError, APIConnectionError, APITimeoutError
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    Anthropic = None

# ── Setup Logging ───────────────────────────────────────────────
logger = logging.getLogger("arie.claude")


# ── Enums and Constants ─────────────────────────────────────────


class RiskLevel(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class OverallMatch(Enum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    MISMATCH = "MISMATCH"


class ApprovalRecommendation(Enum):
    APPROVE = "APPROVE"
    APPROVE_WITH_CONDITIONS = "APPROVE_WITH_CONDITIONS"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


# ── Agent-to-Risk-Dimension Mapping (Improvement 5) ────────────
# D1=Customer/Entity, D2=Geographic, D3=Product/Service, D4=Channel, D5=Transaction
AGENT_RISK_DIMENSIONS = {
    1: ["D1"],              # Identity & Document Integrity → Customer/Entity Risk
    2: ["D1", "D2"],        # External DB Cross-Verification → Entity + Geographic
    3: ["D1"],              # FinCrime Screening → Customer Risk (screening)
    4: ["D1"],              # Corporate Structure & UBO → Customer Risk (UBO)
    5: ["D1", "D2", "D3", "D4", "D5"],  # Compliance Memo → All dimensions
}

# ── Escalation Trigger Rules (Improvement 6) ────────────────────
# Check IDs that always escalate when they FAIL
ALWAYS_ESCALATE_CHECK_IDS = {
    "SCR-01",   # Sanctions match
    "SCR-02",   # PEP confirmed match
    "DOC-14",   # Ownership consistency failure
    "DOC-17",   # Director consistency failure
    "UBO-01",   # UBO identification failure
}

# High-risk dimensions that escalate on WARN (score >= 3)
HIGH_RISK_ESCALATION_DIMENSIONS = {"D1", "D2"}


def compute_escalation(checks: list, agent_number: int = None, risk_dimensions: dict = None) -> bool:
    """
    Determine if agent output requires manual review (escalation).

    Rules:
    - Any FAIL → requires_review = True
    - Any check in ALWAYS_ESCALATE_CHECK_IDS with FAIL → requires_review = True
    - WARN on high-risk dimension (D1, D2 with score >= 3) → requires_review = True
    """
    if not checks:
        return False

    for check in checks:
        result = (check.get("result") or "").lower()
        check_id = check.get("id", "")

        # Any FAIL → escalate
        if result == "fail":
            return True

        # Specific check IDs always escalate on fail
        if check_id in ALWAYS_ESCALATE_CHECK_IDS and result == "fail":
            return True

    # Check dimension-based escalation for WARNs
    if risk_dimensions and agent_number:
        agent_dims = AGENT_RISK_DIMENSIONS.get(agent_number, [])
        has_warn = any((c.get("result") or "").lower() == "warn" for c in checks)
        if has_warn:
            for dim in agent_dims:
                if dim in HIGH_RISK_ESCALATION_DIMENSIONS:
                    dim_score = risk_dimensions.get(dim, {}).get("score", 0)
                    if dim_score >= 3:
                        return True

    return False


def compute_overall_status(checks: list) -> str:
    """
    Compute the overall status from a list of check results.

    FAIL if any check is FAIL, WARN if any check is WARN, else PASS.
    NOT_RUN if no checks present.
    """
    if not checks:
        return "NOT_RUN"

    has_fail = False
    has_warn = False
    for check in checks:
        result = (check.get("result") or "").lower()
        if result == "fail":
            has_fail = True
        elif result == "warn":
            has_warn = True

    if has_fail:
        return "FAIL"
    if has_warn:
        return "WARN"
    return "PASS"


def standardise_agent_output(
    checks: list,
    summary: str = "",
    agent_number: int = None,
    document_id: str = None,
    document_type: str = None,
    risk_dimensions: dict = None,
    error_message: str = None,
) -> dict:
    """
    Wrap any agent output into the standardised structure.

    Returns:
        {
            "status": "PASS" | "WARN" | "FAIL" | "ERROR" | "NOT_RUN",
            "checks": [...],
            "summary": "...",
            "flags": [],
            "requires_review": true/false,
            "validated": true/false,
            "rejected": false
        }
    """
    if error_message:
        return {
            "status": "ERROR",
            "checks": checks or [],
            "summary": error_message,
            "flags": [error_message],
            "requires_review": True,
            "validated": False,
            "rejected": False,
        }

    # Enrich checks with document_id/document_type if provided
    if document_id or document_type:
        for check in (checks or []):
            if document_id and "document_id" not in check:
                check["document_id"] = document_id
            if document_type and "document_type" not in check:
                check["document_type"] = document_type

    status = compute_overall_status(checks)
    requires_review = compute_escalation(checks, agent_number, risk_dimensions)

    # Extract flags from failed/warned checks
    flags = []
    for check in (checks or []):
        result = (check.get("result") or "").lower()
        if result in ("fail", "warn"):
            flags.append(check.get("message") or check.get("reason") or check.get("label", "Unknown check"))

    return {
        "status": status,
        "checks": checks or [],
        "summary": summary,
        "flags": flags,
        "requires_review": requires_review or status in ("FAIL", "ERROR"),
        "validated": status in ("PASS", "WARN"),
        "rejected": False,
    }


# ── Data Classes ────────────────────────────────────────────────


@dataclass
class TokenUsage:
    """Track API token usage and costs."""
    input_tokens: int
    output_tokens: int
    model: str
    timestamp: str
    cost_usd: float


class UsageTracker:
    """Track Claude API token usage and costs."""

    def __init__(self, monthly_budget_usd: float = 50.0):
        self.monthly_budget_usd = monthly_budget_usd
        self.usages: List[TokenUsage] = []
        self.total_cost_usd = 0.0

        # Model pricing (as of Feb 2025, per 1M tokens)
        self.pricing = {
            "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
            "claude-opus-4-6": {"input": 15.0, "output": 45.0},
        }

    def log_usage(
        self, model: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Log token usage and return cost in USD."""
        if model not in self.pricing:
            logger.warning(f"Unknown model {model} — no pricing available")
            cost = 0.0
        else:
            pricing = self.pricing[model]
            cost = (
                (input_tokens / 1_000_000) * pricing["input"]
                + (output_tokens / 1_000_000) * pricing["output"]
            )

        usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            timestamp=datetime.now().isoformat(),
            cost_usd=cost,
        )
        self.usages.append(usage)
        self.total_cost_usd += cost

        logger.info(
            f"Token usage - Model: {model}, Input: {input_tokens}, Output: {output_tokens}, Cost: ${cost:.4f}"
        )

        if self.total_cost_usd > self.monthly_budget_usd:
            logger.warning(
                f"Monthly budget exceeded! Total: ${self.total_cost_usd:.2f} / ${self.monthly_budget_usd:.2f}"
            )

        return cost

    def get_monthly_stats(self) -> Dict[str, Any]:
        """Get aggregated monthly usage stats."""
        return {
            "total_cost_usd": round(self.total_cost_usd, 2),
            "monthly_budget_usd": self.monthly_budget_usd,
            "remaining_budget_usd": round(
                self.monthly_budget_usd - self.total_cost_usd, 2
            ),
            "total_api_calls": len(self.usages),
            "usages": [
                {
                    "model": u.model,
                    "input_tokens": u.input_tokens,
                    "output_tokens": u.output_tokens,
                    "cost_usd": round(u.cost_usd, 4),
                    "timestamp": u.timestamp,
                }
                for u in self.usages
            ],
        }


# ── Mock Response Generators ────────────────────────────────────


def _mock_risk_score() -> Dict[str, Any]:
    """Generate realistic mock risk score response (Agent 5: Compliance Memo)."""
    return {
        "overall_score": 58,
        "risk_level": "MEDIUM",
        "dimensions": {
            "jurisdiction_risk": {
                "score": 60,
                "factors": [
                    "Operating in high-FATF jurisdiction",
                    "No known AML/CFT deficiencies",
                ],
            },
            "entity_structure": {
                "score": 55,
                "factors": [
                    "Standard corporate structure",
                    "Clear ownership chain identified",
                ],
            },
            "beneficial_ownership": {
                "score": 70,
                "factors": [
                    "UBO chain extends to 3 layers",
                    "Nominee directors present",
                ],
            },
            "financial_crime": {
                "score": 45,
                "factors": [
                    "No adverse media found",
                    "No PEP connections identified",
                ],
            },
            "document_integrity": {
                "score": 50,
                "factors": ["Standard documents provided", "Minor discrepancies noted"],
            },
        },
        "flags": [
            "Multi-layered ownership structure",
            "Some document discrepancies",
        ],
        "recommendation": "REVIEW",
    }


def _mock_assess_business_plausibility() -> Dict[str, Any]:
    """Generate realistic mock business plausibility assessment response."""
    return {
        "overall_match": "PARTIAL",
        "checks": [
            {
                "field": "company_name",
                "submitted": "Example Corp Ltd",
                "registry": "Example Corp Limited",
                "match": True,
            },
            {
                "field": "company_number",
                "submitted": "12345678",
                "registry": "12345678",
                "match": True,
            },
            {
                "field": "directors_count",
                "submitted": 3,
                "registry": 3,
                "match": True,
            },
            {
                "field": "director_names",
                "submitted": ["John Smith", "Jane Doe", "Bob Wilson"],
                "registry": ["John Smith", "Jane Doe", "Robert Wilson"],
                "match": False,
                "discrepancy": "Bob Wilson vs Robert Wilson (likely nickname)",
            },
        ],
        "risk_flags": [
            "Minor name variation in director listing",
            "All official records match submission",
        ],
        "confidence": 0.92,
    }


def _mock_analyze_corporate_structure() -> Dict[str, Any]:
    """Generate realistic mock corporate structure analysis (Agent 4: Corporate Structure & UBO Mapping)."""
    return {
        "structure_type": "Multi-layered holding",
        "complexity_score": 6,
        "ubo_identified": True,
        "nominee_indicators": ["Director D1 has nominee company", "UBO2 operates via trust"],
        "jurisdiction_flags": [
            "Layering through BVI (higher risk)",
            "Ultimate beneficial owner in Dubai",
        ],
        "shell_company_risk": "MEDIUM",
        "recommendations": [
            "Request certified copy of trust deed",
            "Verify BVI company registration status",
            "Obtain declaration of beneficial ownership",
            "Enhanced due diligence on Dubai connections",
        ],
    }


def _mock_interpret_fincrime_screening() -> Dict[str, Any]:
    """Generate realistic mock FinCrime screening interpretation (Agent 3: FinCrime Screening Interpretation)."""
    return {
        "consolidation_status": "All Clear",
        "total_hits": 3,
        "confirmed_hits": 0,
        "false_positive_hits": 3,
        "false_positive_explanations": [
            "Hit 1: Name similarity with common PEP surname, different DOB and nationality",
            "Hit 2: Adverse media reference to company with similar name, different jurisdiction",
            "Hit 3: Historical sanctions list match resolved in 2015, entity no longer listed",
        ],
        "severity_ranking": [],
        "recommendation": "All Clear",
        "reasoning": "All hits resolved as false positives through name disambiguation, jurisdiction verification, and historical record review.",
    }


def _mock_generate_compliance_memo() -> Dict[str, Any]:
    """Generate realistic mock compliance memo (Agent 5: Compliance Memo).
    Follows the mandatory 11-section regulator-grade memo structure."""
    return {
        "sections": {
            "executive_summary": {
                "title": "Executive Summary",
                "content": "This memo presents the compliance assessment of Example Corp Ltd (BRN: C-12345), a private company limited by shares incorporated in Mauritius, operating in the fund administration and corporate services sector. The composite risk score of 52/100 (MEDIUM) reflects a balanced risk profile: the principal risk drivers are the presence of a Politically Exposed Person among the board of directors and the offshore jurisdictional classification of Mauritius, which together contribute approximately 23 points to the risk score. These are materially offset by clean sanctions screening across all major consolidated lists, a fully traceable beneficial ownership chain terminating in natural persons resident in FATF-compliant jurisdictions, and a plausible business model consistent with the entity's regulatory licences. Two of nine required documents remain outstanding (Regulatory Certificate and Structure Chart), representing a documentation gap that must be remedied within 14 business days. Recommendation: APPROVE WITH CONDITIONS — subject to PEP declaration, bank reference letter for the identified PEP, receipt of outstanding documents, and enhanced monitoring for the first 12 months."
            },
            "client_overview": {
                "title": "Client Overview",
                "content": "Entity Name: Example Corp Ltd. Business Registration Number: C-12345. Jurisdiction of Incorporation: Mauritius. Entity Type: Private Company Limited by Shares. Sector: Financial Services (Fund Administration and Corporate Services). Date of Application: 2025-03-16. Application Reference: APP-2025-00123. Stated Business Activity: The entity provides fund administration and corporate secretarial services to investment funds domiciled in Mauritius and the broader Indian Ocean region. This activity is consistent with the entity's FSC licence and the commercial profile of Mauritius as an international financial centre. Source of Funds: Operating revenue derived from management and administration fees charged to fund clients. The fee structure is commensurate with market norms for the sector and jurisdiction. Expected Transaction Volume: USD 500,000–2,000,000 per annum, which falls within the expected range for a fund administrator of this size. No unusual volume patterns identified at this stage, although limited trading history reduces the ability to benchmark against historical norms."
            },
            "ownership_and_control": {
                "title": "Ownership & Control",
                "structure_complexity": "Simple",
                "control_statement": "John Smith exercises effective control as majority shareholder (75%) and is the sole UBO. The remaining 25% is held by Jane Doe, who also serves as a director. No shareholder agreements or special voting arrangements were disclosed that would alter effective control.",
                "content": "The entity operates a simple, single-tier corporate structure with no intermediate holding entities, nominee arrangements, or bearer shares in issue. UBO: John Smith — 75% direct shareholding, British national, resident in the United Kingdom, not a PEP. Director 1: Jane Doe — Mauritian national, resident in Mauritius, not a PEP, also holds 25% shareholding. Director 2: Robert Lee — Singapore national, resident in Singapore, identified as a Politically Exposed Person (Foreign Government Official — Senior Trade Advisor, Singapore Ministry of Trade and Industry). Robert Lee holds no shareholding and exercises no ownership control, but his board position confers governance influence that warrants enhanced due diligence. The beneficial ownership chain was verified against the Mauritius Companies Division register via OpenCorporates. The register of directors and register of shareholders are mutually consistent. No discrepancies identified between declared and registered ownership."
            },
            "risk_assessment": {
                "title": "Risk Assessment",
                "sub_sections": {
                    "jurisdiction_risk": {
                        "title": "Jurisdiction Risk",
                        "rating": "MEDIUM",
                        "content": "Mauritius presents moderate jurisdictional risk. The jurisdiction was placed on the FATF grey list in February 2020 due to strategic deficiencies in its AML/CFT framework, but was removed in October 2021 following completion of its action plan. While Mauritius is currently compliant with FATF standards and is a member of the Eastern and Southern Africa Anti-Money Laundering Group (ESAAMLG), it retains characteristics of an offshore international financial centre — including a Global Business Licence regime, extensive double taxation treaty network, and significant cross-border capital flows — that elevate baseline risk. The entity's stated business of fund administration is consistent with the jurisdiction's commercial profile, which partially mitigates this concern. Risk weighting factor: 0.20. Contribution to composite score: +10 points."
                    },
                    "business_risk": {
                        "title": "Business Risk",
                        "rating": "MEDIUM",
                        "content": "The financial services sector (fund administration) carries inherent regulatory and ML/TF risk due to the intermediary nature of the business and the volume of third-party funds under management. However, the entity holds a valid FSC licence, the stated business model — charging management and administration fees to investment fund clients — is plausible and consistent with the entity's incorporation documents, regulatory authorisations, and sector norms. Agent 4 (Corporate Structure & UBO Mapping) assessed the revenue model as consistent with market benchmarks. No indicators of shell company characteristics or front-company typologies were identified. Risk weighting factor: 0.15. Contribution to composite score: +8 points."
                    },
                    "transaction_risk": {
                        "title": "Transaction Risk",
                        "rating": "LOW",
                        "content": "The expected annual transaction volume of USD 500,000–2,000,000 falls within normal parameters for a fund administrator of this size and jurisdiction. Source of funds is identified as management fee income, which is verifiable against client contracts and audited financial statements. No unusual transaction patterns, high-value single transactions, or rapid movement of funds were flagged. However, as the entity is newly onboarded, there is limited historical transaction data against which to benchmark, which modestly reduces confidence in forward-looking transaction risk assessment. Risk weighting factor: 0.10. Contribution to composite score: +3 points."
                    },
                    "ownership_risk": {
                        "title": "Ownership Risk",
                        "rating": "MEDIUM",
                        "content": "Beneficial ownership has been identified to natural person level through a single-tier structure, which is a positive indicator. However, one director (Robert Lee) has been identified as a PEP (Foreign Government Official — Senior Trade Advisor, Singapore Ministry of Trade and Industry). Although Robert Lee holds no direct ownership and his PEP status relates to a government advisory role rather than a position with direct control over public funds, his board position confers governance influence and introduces the potential for corruption or undue influence risk that FATF Recommendation 12 requires enhanced scrutiny for. The PEP does not exercise effective control (which rests with John Smith at 75%), partially mitigating this concern. Risk weighting factor: 0.25. Contribution to composite score: +13 points."
                    },
                    "financial_crime_risk": {
                        "title": "Financial Crime Risk",
                        "rating": "LOW",
                        "content": "Sanctions screening was conducted across UN Security Council Consolidated List, EU Consolidated Financial Sanctions List, OFAC SDN List, and HMT Consolidated List via OpenSanctions API. No matches were returned for any director, UBO, or the entity itself. Adverse media screening returned no relevant hits across global media databases. No connections to high-risk individuals, designated entities, or known criminal networks were identified. The entity's business model does not exhibit typology indicators associated with money laundering (layering through fund structures), terrorist financing, or proliferation financing. Risk weighting factor: 0.10. Contribution to composite score: +2 points."
                    }
                }
            },
            "screening_results": {
                "title": "Screening Results",
                "content": "Sanctions Screening: Conducted via OpenSanctions API against UN, EU, OFAC, and HMT consolidated lists. No matches returned for John Smith, Jane Doe, Robert Lee, or Example Corp Ltd. Screening timestamp: 2025-03-16T10:15:00Z. PEP Screening: One match confirmed — Robert Lee identified as a Foreign Government Official (Senior Trade Advisor, Singapore Ministry of Trade and Industry). This is a confirmed true positive based on verified identity data (full name, date of birth, nationality). PEP classification: Foreign PEP, Tier 2. PEP declaration form and bank reference letter have been requested. Adverse Media Screening: Comprehensive adverse media search conducted across global news databases and regulatory enforcement databases. No relevant hits identified for any associated individual or the entity itself. No historical regulatory actions, enforcement proceedings, or negative press coverage identified. Company Registry Verification: Example Corp Ltd verified as active on the Mauritius Companies Division register via OpenCorporates API. Registration number, registered office, director names, and incorporation date are all consistent with application data. Last annual return filed: 2024-12-15 — entity is current with filing obligations."
            },
            "document_verification": {
                "title": "Document Verification",
                "content": "Seven of nine required documents have been submitted and verified. Certificate of Incorporation: Verified — document is authentic, dated 2019-04-12, consistent with registry data. No discrepancies. Memorandum & Articles: Verified — standard form for Mauritius private company, consistent with declared share structure and objects. Register of Directors: Verified — lists all three directors consistent with application data. Register of Shareholders: Verified — shareholding percentages (John Smith 75%, Jane Doe 25%) match UBO declaration and M&A. Financial Statements: Verified — audited by Baker Tilly Mauritius for FY2024, unqualified opinion, revenue profile consistent with stated business activity, no going concern issues. Proof of Address: Verified — utility bill (CEB Mauritius) dated 2025-01-22, within 3-month window, address matches registered office. Board Resolution: Verified — resolution dated 2025-03-10 authorises application for account opening, signed by two directors. MISSING: Regulatory Certificate (FSC licence copy) — requested 2025-03-16, expected within 7 business days. This is a material gap as it prevents independent verification of the entity's regulatory status. MISSING: Structure Chart — requested 2025-03-16. While the ownership structure appears simple, a formal structure chart is required per policy. Overall documentation adequacy: 78% of required documents received and verified. Confidence in verified documents: 94%. The two missing documents do not prevent onboarding but must be received within 14 business days as a condition of approval."
            },
            "ai_explainability": {
                "title": "AI Explainability Layer",
                "content": "Risk scoring model: Onboarda Composite Risk Engine v2.1. Scoring methodology: Weighted multi-factor analysis across 5 risk dimensions, calibrated against Basel Committee and Wolfsberg Group risk factor guidance. Overall risk score: 52/100 (MEDIUM). Model confidence: 87% — confidence is reduced from baseline 95% due to limited historical transaction data and two missing documents. Top 3 risk-increasing factors: (1) PEP presence among directors — weight: 0.25, contribution: +13 points. Robert Lee's PEP status triggers FATF Recommendation 12 enhanced scrutiny requirements. (2) Offshore jurisdiction classification — weight: 0.20, contribution: +10 points. Mauritius's IFC characteristics elevate cross-border risk baseline. (3) Financial services sector — weight: 0.15, contribution: +8 points. Fund administration carries inherent intermediary risk. Top 3 risk-decreasing factors: (1) Clean sanctions screening — weight: 0.20, contribution: -10 points. No matches across any consolidated list. (2) Verified beneficial ownership — weight: 0.15, contribution: -8 points. Single-tier structure with UBO identified to natural person. (3) Adequate documentation — weight: 0.10, contribution: -5 points. 7 of 9 documents verified with high confidence. Decision pathway: Data ingestion → Agent 1 (Identity & Document Integrity: 94% confidence) → Agent 2 (External Database Cross-Verification: registry confirmed) → Agent 3 (FinCrime Screening Interpretation: clear) → Agent 4 (Corporate Structure & UBO Mapping: simple structure, no concerns) → Agent 5 (Compliance Memo & Risk Recommendation). Supervisor module: No inter-agent contradictions flagged. All agent outputs are mutually consistent. Limitations: Limited historical transaction data reduces forward-looking risk confidence. Two missing documents prevent complete verification."
            },
            "red_flags_and_mitigants": {
                "title": "Red Flags & Mitigants",
                "red_flags": [
                    "Politically Exposed Person identified: Robert Lee (Director) holds a senior advisory position with the Singapore Ministry of Trade and Industry. While his role is advisory rather than executive, board membership confers governance influence and introduces corruption and undue influence risk per FATF Recommendation 12.",
                    "Documentation gap: 2 of 9 required documents remain outstanding (Regulatory Certificate, Structure Chart). The absence of the FSC licence copy is a material gap that prevents independent verification of the entity's regulatory authorisation. Until received, there is residual risk that the entity's stated regulatory status cannot be confirmed.",
                    "Limited trading history: As a new onboarding, there is no historical transaction data against which to benchmark stated expected volumes of USD 500K–2M per annum. This reduces confidence in forward-looking transaction risk assessment and creates a monitoring dependency."
                ],
                "mitigants": [
                    "Robert Lee holds no ownership stake (0%) and does not exercise effective control, which rests with John Smith (75%). His PEP role is advisory rather than executive, reducing the likelihood of direct corruption exposure. PEP declaration and bank reference letter have been requested as conditions of approval.",
                    "Outstanding documents have been formally requested with a 14-business-day deadline. Failure to provide will trigger automatic escalation. The 7 documents already verified are internally consistent and corroborated by external registry data, providing reasonable assurance of entity legitimacy.",
                    "Transaction monitoring will be applied on a quarterly basis for the first 12 months, with automated alerts for volumes exceeding 150% of stated expectations. This compensates for the absence of historical benchmarking data and will enable early detection of anomalous patterns."
                ]
            },
            "compliance_decision": {
                "title": "Compliance Decision",
                "decision": "APPROVE_WITH_CONDITIONS",
                "content": "On the basis of the composite risk assessment (MEDIUM — 52/100), clean sanctions and adverse media screening, verified beneficial ownership to natural person level, and a plausible business model consistent with regulatory authorisations, this application is recommended for APPROVAL WITH CONDITIONS. The conditions reflect the residual risks identified — principally the PEP exposure, outstanding documentation, and limited transaction history. Conditions of approval: (1) Regulatory Certificate (FSC licence copy) and Structure Chart must be received within 14 business days of this memo date. Failure to comply will trigger automatic escalation to the Senior Compliance Officer. (2) Robert Lee must complete and sign a PEP declaration form within 14 business days. (3) A bank reference letter for Robert Lee must be obtained from his primary banking institution. (4) Enhanced monitoring (quarterly transaction review, annual full re-assessment) will apply for the first 12 months. Residual risk acknowledgement: The MLRO acknowledges that residual risk remains in relation to the PEP exposure and documentation gap. These risks are assessed as manageable within the conditions framework and do not warrant rejection at this stage. The case will be re-assessed immediately upon receipt of outstanding documents."
            },
            "ongoing_monitoring": {
                "title": "Ongoing Monitoring & Review",
                "content": "Monitoring tier: Enhanced — assigned due to the combination of PEP presence, offshore jurisdiction, and financial services sector classification. This tier is warranted even at MEDIUM composite risk given the specific risk drivers identified. Review frequency: Every 12 months (standard for MEDIUM risk entities), with an interim 6-month review triggered by the PEP condition. Next scheduled review: 2026-03-16. Transaction monitoring: Quarterly review of transaction patterns against stated business activity (USD 500K–2M per annum). Automated alerts configured for: (a) single transactions exceeding USD 100,000, (b) monthly aggregate volume exceeding USD 250,000, (c) transactions involving jurisdictions on the FATF grey/black list. Trigger events requiring immediate review: (1) Any change in beneficial ownership structure or directorship composition. (2) Adverse media alerts from continuous screening. (3) Change in Robert Lee's PEP status (e.g., change of government role, cessation of PEP status). (4) Transaction volumes exceeding 150% of stated expectations in any quarter. (5) Regulatory action, investigation, or enforcement proceedings against the entity or any associated person. (6) Failure to provide outstanding documents within the 14-business-day deadline."
            },
            "audit_and_governance": {
                "title": "Audit & Governance",
                "content": "This compliance onboarding memo was generated by the Compliance Memo & Risk Recommendation workflow, version 2.1. In the live approval path, memo generation remains subject to downstream validation and memo supervisor checks before any human approval decision is made. Document classification: CONFIDENTIAL — this document contains personal data subject to GDPR (as applicable) and Mauritius Data Protection Act 2017 requirements. Retention period: 7 years from date of generation or termination of business relationship, whichever is later, in accordance with FIAMLA 2002 (Mauritius) record-keeping requirements. Applicable compliance frameworks: Financial Intelligence and Anti-Money Laundering Act 2002 (Mauritius), AML/CFT Codes and Guidance Notes (FSC Mauritius), EU Sixth Anti-Money Laundering Directive (6AMLD), FATF 40 Recommendations (2012, as updated). Generated: 2025-03-16T14:30:00Z. Memo version: 1.0. Reviewed by: Information not provided — memo pending Senior Compliance Officer review."
            }
        },
        "metadata": {
            "risk_rating": "MEDIUM",
            "risk_score": 52,
            "confidence_level": 0.87,
            "approval_recommendation": "APPROVE_WITH_CONDITIONS",
            "key_findings": [
                "Beneficial ownership traced to natural persons via single-tier structure — John Smith (75%, UK national) exercises effective control",
                "PEP identified: Robert Lee (Director, 0% ownership) — Foreign Government Official, Singapore. Advisory role, no direct control.",
                "Clean sanctions and adverse media screening across all consolidated lists via OpenSanctions API",
                "7 of 9 required documents verified at 94% confidence; 2 outstanding documents formally requested",
                "Business model assessed as plausible and consistent with FSC regulatory licence",
                "Mauritius jurisdiction presents moderate risk due to IFC classification despite current FATF compliance"
            ],
            "conditions": [
                "Outstanding documents (Regulatory Certificate, Structure Chart) to be received within 14 business days — escalation on non-compliance",
                "PEP declaration form to be completed and signed by Robert Lee within 14 business days",
                "Bank reference letter for Robert Lee to be obtained from primary banking institution",
                "Enhanced monitoring (quarterly transaction review, annual re-assessment) for first 12 months"
            ],
            "review_checklist": [
                "Company identity verified against Mauritius Companies Division register via OpenCorporates — confirmed active",
                "UBO chain mapped to natural person: John Smith (75%, UK national) — effective control confirmed",
                "PEP screening completed — 1 confirmed true positive: Robert Lee (Director, Foreign Government Official)",
                "Sanctions screening completed — no matches across UN, EU, OFAC, HMT lists",
                "Adverse media review conducted — no relevant hits identified",
                "Source of funds verified through audited financial statements (Baker Tilly, FY2024)",
                "Business model plausibility confirmed within Agent 5 — consistent with FSC licence and sector norms",
                "Document verification completed (7/9) — 2 documents outstanding with formal request issued",
                "Composite risk score reviewed: 52/100 (MEDIUM) at 87% confidence",
                "Compliance decision (APPROVE WITH CONDITIONS) aligned with risk assessment findings and conditions framework"
            ]
        }
    }


def _mock_verify_document() -> Dict[str, Any]:
    """Generate realistic mock document verification response (Agent 1: Identity & Document Integrity)."""
    return {
        "checks": [
            {
                "id": "DOC-01",
                "label": "Document Validity",
                "type": "validity",
                "result": "pass",
                "message": "Document format and structure verified"
            },
            {
                "id": "DOC-02",
                "label": "Expiry Risk",
                "type": "expiry",
                "result": "pass",
                "message": "Document validity extends beyond 6 months"
            },
            {
                "id": "DOC-03",
                "label": "Name Consistency",
                "type": "name",
                "result": "pass",
                "message": "Name on document matches application data"
            },
            {
                "id": "DOC-04",
                "label": "Quality Indicators",
                "type": "quality",
                "result": "pass",
                "message": "Document image quality is clear and legible"
            }
        ],
        "overall": "verified",
        "confidence": 0.94,
    }


# ── Claude Client ───────────────────────────────────────────────


class ClaudeClient:
    """
    Claude AI Integration for ARIE Finance Compliance Platform.

    Manages interactions with Claude API for:
    - Risk scoring across 5 compliance dimensions
    - Cross-verification of client data vs external registries
    - Corporate structure and UBO analysis
    - Compliance memo generation

    Features:
    - Automatic token usage tracking and budget enforcement
    - Mock mode for testing without API calls
    - Retry logic with exponential backoff
    - Structured JSON responses from all agents
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        monthly_budget_usd: float = 50.0,
        mock_mode: Optional[bool] = None,
    ):
        """
        Initialize Claude client.

        Args:
            api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var)
            monthly_budget_usd: Maximum monthly spend in USD (default: $50)
            mock_mode: Force mock mode (defaults to CLAUDE_MOCK_MODE env var)
        """
        self.api_key = api_key or _CFG_ANTHROPIC_API_KEY
        self.mock_mode = mock_mode if mock_mode is not None else _CFG_CLAUDE_MOCK_MODE

        # P0-04: Block mock mode in production
        if self.mock_mode and _CFG_IS_PRODUCTION:
            raise RuntimeError(
                "CRITICAL: CLAUDE_MOCK_MODE=true is not allowed in production. "
                "Set CLAUDE_MOCK_MODE=false or unset it, and provide a valid ANTHROPIC_API_KEY."
            )

        # Determine if we are in a non-demo environment where mock is unsafe
        _is_regulated_env = _CFG_IS_STAGING or _CFG_IS_PRODUCTION
        self._fail_closed = False  # Set True if AI service unavailable in regulated env

        if self.mock_mode:
            logger.info("Claude client initialized in MOCK MODE (no API calls)")
            self.client = None
        else:
            if not ANTHROPIC_AVAILABLE:
                if _is_regulated_env:
                    logger.error(
                        "FAIL-CLOSED: Anthropic library not available in %s. "
                        "AI verification will return ERROR, not mock PASS. "
                        "Install with: pip install anthropic", _CFG_ENVIRONMENT
                    )
                    self.mock_mode = False  # Do NOT enable mock — fail closed
                    self.client = None
                    self._fail_closed = True
                else:
                    logger.warning("Anthropic library not available — falling back to mock mode (demo/dev).")
                    self.mock_mode = True
                    self.client = None
            elif not self.api_key:
                if _is_regulated_env:
                    logger.error(
                        "FAIL-CLOSED: No ANTHROPIC_API_KEY in %s. "
                        "AI verification will return ERROR, not mock PASS.", _CFG_ENVIRONMENT
                    )
                    self.mock_mode = False
                    self.client = None
                    self._fail_closed = True
                else:
                    logger.warning("No API key — falling back to mock mode (demo/dev).")
                    self.mock_mode = True
                    self.client = None
            else:
                try:
                    self.client = Anthropic(api_key=self.api_key)
                    logger.info("Claude client initialized with Anthropic API")
                except Exception as e:
                    if _is_regulated_env:
                        logger.error(
                            "FAIL-CLOSED: Anthropic client init failed in %s: %s. "
                            "AI verification will return ERROR.", _CFG_ENVIRONMENT, e
                        )
                        self.mock_mode = False
                        self.client = None
                        self._fail_closed = True
                    else:
                        logger.warning(f"Anthropic client init failed: {e} — using mock mode (demo/dev)")
                        self.mock_mode = True
                        self.client = None

        self.usage_tracker = UsageTracker(monthly_budget_usd)
        self.max_retries = 3
        self.timeout_seconds = 30

    def _check_fail_closed(self, method_name: str) -> Optional[Dict[str, Any]]:
        """Return an error result if the client is in fail-closed state (regulated env, no AI service).
        Returns None if the client is operational (real API or allowed mock mode)."""
        if getattr(self, '_fail_closed', False):
            logger.error(f"FAIL-CLOSED: {method_name} called but AI service is unavailable in {_CFG_ENVIRONMENT}")
            return {
                "status": "error",
                "error": f"AI service unavailable in {_CFG_ENVIRONMENT}. Cannot produce mock results in regulated environment.",
                "checks": [],
                "requires_review": True,
                "_validated": False,
                "_rejected": True,
                "_fail_closed": True,
                "ai_source": "fail-closed",
            }
        return None

    def _sanitize_for_prompt(self, text: str, max_length: int = 500) -> str:
        """
        C-08: Whitelist-based prompt injection defense with recursive sanitization.

        Strategy:
        1. Whitelist: Only allow alphanumeric, standard punctuation, and business characters
        2. Structural markers: Remove anything that looks like prompt engineering
        3. Nested attack detection: Recursively sanitize until stable
        4. Length limiting per field type
        """
        if not isinstance(text, str):
            return str(text) if text is not None else ""

        result = text

        # Pass 1: Strip HTML/XML tags (prevents injection via markup)
        result = re.sub(r'<[^>]*>', '', result)

        # Pass 2: Remove Unicode control characters and zero-width chars
        result = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u2028-\u202f\ufeff]', '', result)

        # Pass 3: Whitelist — keep only safe characters for business data
        # Allows: letters, numbers, spaces, standard punctuation, accented chars
        result = re.sub(r'[^\w\s.,;:!?\'\"()\-/&#@+=%$£€¥°\[\]{}]', '', result, flags=re.UNICODE)

        # Pass 4: Structural prompt injection detection (recursive until stable)
        _injection_patterns = [
            r'(?i)\b(SYSTEM|IGNORE|OVERRIDE|PROMPT|INSTRUCTION|ASSISTANT|HUMAN|USER)\s*:',
            r'(?i)(ignore\s+(all\s+)?previous|disregard|forget\s+everything)',
            r'(?i)(as\s+an?\s+ai|you\s+are\s+now|pretend\s+to\s+be|act\s+as)',
            r'(?i)(do\s+not\s+follow|bypass|skip|override)\s+(rules|instructions|guidelines|safety|filters)',
            r'(?i)(jailbreak|DAN|ignore\s+safety|reveal\s+(system|prompt))',
            r'(?i)(```|<\|im_start\|>|<\|im_end\|>|\[INST\]|\[/INST\])',
            r'(?i)(new\s+instructions?|forget\s+above|ignore\s+above)',
            r'(?i)(role:\s*(system|assistant|user))',
        ]

        # Recursive sanitization: keep cleaning until no more injections found
        max_passes = 3
        for _ in range(max_passes):
            cleaned = result
            for pattern in _injection_patterns:
                cleaned = re.sub(pattern, '[BLOCKED]', cleaned)
            if cleaned == result:
                break  # Stable — no more injections
            result = cleaned

        # Pass 5: Collapse excessive whitespace
        result = re.sub(r'\s{3,}', '  ', result).strip()

        # Pass 6: Length limit
        return result[:max_length]

    def _deep_sanitize(self, data: Any, max_depth: int = 10, _depth: int = 0) -> Any:
        """
        Recursively sanitize nested data structures for safe embedding in prompts.

        Handles dicts, lists, tuples, and strings at any nesting depth.
        Non-string primitives (int, float, bool, None) pass through unchanged.
        Prevents infinite recursion via max_depth.

        Args:
            data: Any nested data structure (dict, list, tuple, str, int, etc.)
            max_depth: Maximum recursion depth (default 10, sufficient for compliance data)
            _depth: Internal depth counter (do not set externally)

        Returns:
            Sanitized copy of the data structure with all strings cleaned.
        """
        if _depth > max_depth:
            return "[DEPTH_LIMIT]"

        if isinstance(data, str):
            return self._sanitize_for_prompt(data)
        elif isinstance(data, dict):
            return {
                k: self._deep_sanitize(v, max_depth, _depth + 1)
                for k, v in data.items()
            }
        elif isinstance(data, (list, tuple)):
            sanitized = [self._deep_sanitize(item, max_depth, _depth + 1) for item in data]
            return type(data)(sanitized) if isinstance(data, tuple) else sanitized
        else:
            # int, float, bool, None — pass through unchanged
            return data

    # ── Sprint 3.5: Risk-Based Model Routing ─────────────────────

    # Routing tiers (environment-overridable)
    ROUTING_MODELS = {
        "fast": _CFG_ARIE_MODEL_FAST,
        "thorough": _CFG_ARIE_MODEL_THOROUGH,
    }

    def select_memo_model(self, risk_score: float, risk_level: str) -> tuple:
        """
        Sprint 3.5: Choose model for memo generation based on risk profile.

        Routing logic:
        - LOW risk (score < 40)            → Sonnet  (fast, cost-effective)
        - MEDIUM risk (40 ≤ score < 55)    → Sonnet  (with validation gate)
        - HIGH / VERY_HIGH (score ≥ 55)    → Opus    (thorough analysis)

        Returns: (model_name, routing_reason)
        """
        level = (risk_level or "MEDIUM").upper()
        score = risk_score or 50

        if level in ("HIGH", "VERY_HIGH") or score >= 55:
            model = self.ROUTING_MODELS["thorough"]
            reason = f"HIGH/VERY_HIGH risk (score={score}, level={level}) → Opus for thorough analysis"
        else:
            model = self.ROUTING_MODELS["fast"]
            reason = f"{level} risk (score={score}) → Sonnet for cost efficiency"

        logger.info(
            f"Model routing: {reason} | selected={model}",
            extra={"structured_data": {
                "event": "model_routing",
                "risk_score": score,
                "risk_level": level,
                "selected_model": model,
                "routing_reason": reason,
            }}
        )
        return model, reason

    # ── Risk Scoring (Sonnet - fast, cheap) ─────────────────────

    def score_risk(self, application_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Agent 5 (Part 1): Compliance Memo & Risk Recommendation Agent — Compute 5-dimension composite risk scoring.

        Scores application risk across 5 compliance dimensions:
        1. Jurisdiction Risk
        2. Entity Structure
        3. Beneficial Ownership
        4. Financial Crime
        5. Document Integrity

        Uses claude-sonnet-4-6 for speed and cost efficiency.

        Args:
            application_data: Dict with company info, directors, UBOs, jurisdiction, etc.

        Returns:
            {
                "overall_score": 65,
                "risk_level": "MEDIUM",
                "dimensions": {
                    "jurisdiction_risk": {"score": 70, "factors": [...]},
                    "entity_structure": {"score": 55, "factors": [...]},
                    "beneficial_ownership": {"score": 80, "factors": [...]},
                    "financial_crime": {"score": 45, "factors": [...]},
                    "document_integrity": {"score": 60, "factors": [...]}
                },
                "flags": ["Complex multi-layered ownership", ...],
                "recommendation": "REVIEW"
            }
        """
        fail_result = self._check_fail_closed("score_risk")
        if fail_result:
            return fail_result
        if self.mock_mode:
            logger.info("Returning mock risk score (mock mode)")
            result = _mock_risk_score()
            result["ai_source"] = "mock"
            return result

        system_prompt = """You are an expert financial compliance officer specializing in AML/CFT risk assessment.

Analyze the provided application data and score risk across 5 dimensions on a 1-4 scale:
1. Jurisdiction Risk (country/regulatory environment)
2. Entity Structure (corporate form, complexity, sophistication)
3. Beneficial Ownership (UBO identification, layering, nominee use)
4. Financial Crime (PEP connections, sanctions, adverse media)
5. Document Integrity (completeness, authenticity, discrepancies)

Return ONLY valid JSON (no markdown, no code blocks) with this exact structure:
{
    "overall_score": <1-100 integer>,
    "risk_level": "<LOW|MEDIUM|HIGH|VERY_HIGH>",
    "dimensions": {
        "jurisdiction_risk": {"score": <1-4>, "factors": ["factor1", "factor2"]},
        "entity_structure": {"score": <1-4>, "factors": ["factor1", "factor2"]},
        "beneficial_ownership": {"score": <1-4>, "factors": ["factor1", "factor2"]},
        "financial_crime": {"score": <1-4>, "factors": ["factor1", "factor2"]},
        "document_integrity": {"score": <1-4>, "factors": ["factor1", "factor2"]}
    },
    "flags": ["flag1", "flag2"],
    "recommendation": "<APPROVE|REVIEW|REJECT>"
}"""

        # Sanitize user-controlled fields recursively to prevent prompt injection
        sanitized_data = self._deep_sanitize(application_data)

        user_prompt = f"""Score the following application for AML/CFT risk:

{json.dumps(sanitized_data, indent=2)}"""

        try:
            response = self._call_claude(
                system_prompt,
                user_prompt,
                model="claude-sonnet-4-6",
                timeout=self.timeout_seconds,
            )
            result = self._parse_json_response(response, agent_method="score_risk")
            result["ai_source"] = "claude-sonnet-4-6"
            return result
        except Exception as e:
            fail_result = self._check_fail_closed("score_risk")
            if fail_result:
                fail_result["ai_error"] = str(e)[:200]
                return fail_result
            logger.error(f"Risk scoring failed: {e} — returning mock fallback")
            result = _mock_risk_score()
            result["ai_source"] = "mock"
            result["ai_error"] = str(e)[:200]
            return result

    # ── Business Plausibility Assessment (Sonnet - fast, cheap) ──

    def assess_business_plausibility(
        self, business_data: Dict[str, Any], registry_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Agent 4: Corporate Structure & UBO Mapping Agent — Evaluate business model alignment and transaction consistency.

        Assesses business story against sector benchmarks, transaction volume, geography, and source of funds.
        Uses claude-sonnet-4-6 for speed and cost efficiency.

        Args:
            business_data: Submitted business description, sector, geography, transaction volume, source of funds
            registry_data: Registry/reference data for benchmarking

        Returns:
            {
                "business_story_alignment": "<Consistent|Needs Clarification|Suspicious Profile>",
                "sector_alignment": "description",
                "transaction_benchmarking": "description",
                "geography_logic": "description",
                "source_of_funds_consistency": "description",
                "risk_flags": ["flag1", "flag2"],
                "recommendations": ["rec1", "rec2"],
                "confidence": 0.85
            }
        """
        fail_result = self._check_fail_closed("assess_business_plausibility")
        if fail_result:
            return fail_result
        if self.mock_mode:
            logger.info("Returning mock business plausibility assessment (mock mode)")
            result = _mock_assess_business_plausibility()
            result["ai_source"] = "mock"
            return result

        system_prompt = """You are an expert in business model analysis and financial crime risk assessment.

Evaluate the business description against sector benchmarks, transaction volume patterns,
geographic logic, and source of funds consistency. Identify red flags in business model plausibility.

Return ONLY valid JSON (no markdown, no code blocks) with this exact structure:
{
    "business_story_alignment": "<Consistent|Needs Clarification|Suspicious Profile>",
    "sector_alignment": "assessment of business vs sector norms",
    "transaction_benchmarking": "analysis of transaction volume vs company size",
    "geography_logic": "assessment of geographic footprint vs business model",
    "source_of_funds_consistency": "evaluation of funds origin vs business activity",
    "risk_flags": ["flag1", "flag2"],
    "recommendations": ["rec1", "rec2"],
    "confidence": <0.0-1.0>
}"""

        # Sanitize all input data recursively (Finding 8 fix)
        sanitized_business = self._deep_sanitize(business_data)
        sanitized_registry = self._deep_sanitize(registry_data)

        user_prompt = f"""Assess business model plausibility:

BUSINESS DATA:
{json.dumps(sanitized_business, indent=2)}

REGISTRY/REFERENCE DATA:
{json.dumps(sanitized_registry, indent=2)}

Evaluate business story consistency, sector alignment, transaction volume benchmarking,
geographic logic, and source of funds alignment."""

        try:
            response = self._call_claude(
                system_prompt,
                user_prompt,
                model="claude-sonnet-4-6",
                timeout=self.timeout_seconds,
            )
            result = self._parse_json_response(response, agent_method="assess_business_plausibility")
            result["ai_source"] = "claude-sonnet-4-6"
            return result
        except Exception as e:
            fail_result = self._check_fail_closed("assess_business_plausibility")
            if fail_result:
                fail_result["ai_error"] = str(e)[:200]
                return fail_result
            logger.error(f"Business plausibility assessment failed: {e} — returning mock response")
            result = _mock_assess_business_plausibility()
            result["ai_source"] = "mock"
            result["ai_error"] = str(e)[:200]
            return result

    # ── Corporate Structure & UBO Mapping (Sonnet - fast, cheap) ──

    def analyze_corporate_structure(
        self,
        directors: List[Dict[str, Any]],
        ubos: List[Dict[str, Any]],
        jurisdiction: str,
    ) -> Dict[str, Any]:
        """
        Agent 4: Corporate Structure & UBO Mapping Agent — Map ownership chains and identify beneficial owners.

        Analyzes corporate structure, identifies UBO chains, detects nominee arrangements, and flags structural risks.
        Uses claude-sonnet-4-6 for speed and cost efficiency.

        Args:
            directors: List of director dicts {name, nationality, is_pep, etc}
            ubos: List of UBO dicts {name, ownership_pct, is_pep, address, etc}
            jurisdiction: Country/jurisdiction of incorporation

        Returns:
            {
                "structure_type": "Multi-layered holding",
                "complexity_score": 7,
                "ubo_identified": true,
                "nominee_indicators": ["indicator1", "indicator2"],
                "jurisdiction_flags": ["flag1", "flag2"],
                "shell_company_risk": "<LOW|MEDIUM|HIGH>",
                "recommendations": ["recommendation1", "recommendation2"]
            }
        """
        fail_result = self._check_fail_closed("analyze_corporate_structure")
        if fail_result:
            return fail_result
        if self.mock_mode:
            logger.info("Returning mock structure analysis (mock mode)")
            result = _mock_analyze_corporate_structure()
            result["ai_source"] = "mock"
            return result

        system_prompt = """You are an expert in corporate structures and beneficial ownership.

Analyze the corporate structure, identify the UBO chain, spot nominee indicators,
and assess shell company risk. Be specific and cite the evidence.

Return ONLY valid JSON (no markdown, no code blocks) with this exact structure:
{
    "structure_type": "description of structure",
    "complexity_score": <1-10>,
    "ubo_identified": true/false,
    "nominee_indicators": ["indicator1", "indicator2"],
    "jurisdiction_flags": ["flag1", "flag2"],
    "shell_company_risk": "<LOW|MEDIUM|HIGH>",
    "recommendations": ["recommendation1", "recommendation2"]
}"""

        # Sanitize user inputs recursively (Finding 8 fix)
        sanitized_jurisdiction = self._sanitize_for_prompt(jurisdiction)
        sanitized_directors = self._deep_sanitize(directors)
        sanitized_ubos = self._deep_sanitize(ubos)

        user_prompt = f"""Analyze this corporate structure:

JURISDICTION: {sanitized_jurisdiction}

DIRECTORS:
{json.dumps(sanitized_directors, indent=2)}

BENEFICIAL OWNERS (UBOs):
{json.dumps(sanitized_ubos, indent=2)}

Map the ownership chain, identify the ultimate beneficial owner, and flag any risks."""

        try:
            response = self._call_claude(
                system_prompt,
                user_prompt,
                model="claude-sonnet-4-6",
                timeout=self.timeout_seconds,
            )
            result = self._parse_json_response(response, agent_method="analyze_corporate_structure")
            result["ai_source"] = "claude-sonnet-4-6"
            return result
        except Exception as e:
            fail_result = self._check_fail_closed("analyze_corporate_structure")
            if fail_result:
                fail_result["ai_error"] = str(e)[:200]
                return fail_result
            logger.error(f"Structure analysis failed: {e} — returning mock response")
            result = _mock_analyze_corporate_structure()
            result["ai_source"] = "mock"
            result["ai_error"] = str(e)[:200]
            return result

    # ── FinCrime Screening Interpretation (Sonnet - fast, cheap) ──

    def interpret_fincrime_screening(
        self,
        screening_results: Dict[str, Any],
        person_name: str,
        entity_type: str = "individual",
    ) -> Dict[str, Any]:
        """
        Agent 3: FinCrime Screening Interpretation Agent — Analyze sanctions/PEP/adverse media hits.

        Consolidates screening results, removes false positives, ranks severity, and provides recommendations.
        Uses claude-sonnet-4-6 for speed and cost efficiency.

        Args:
            screening_results: Raw screening output from Sumsub AML or similar service
            person_name: Name of person/entity being screened
            entity_type: Type of entity (individual, company, etc.)

        Returns:
            {
                "consolidation_status": "<All Clear|Review Required|Escalate>",
                "total_hits": 5,
                "confirmed_hits": 1,
                "false_positive_hits": 4,
                "false_positive_explanations": ["explanation1", "explanation2"],
                "severity_ranking": [{"hit": "Hit1", "severity": "HIGH", "action": "action"}],
                "recommendation": "<All Clear|Review Required|Escalate>",
                "reasoning": "detailed reasoning"
            }
        """
        fail_result = self._check_fail_closed("interpret_fincrime_screening")
        if fail_result:
            return fail_result
        if self.mock_mode:
            logger.info("Returning mock FinCrime screening interpretation (mock mode)")
            result = _mock_interpret_fincrime_screening()
            result["ai_source"] = "mock"
            return result

        sanitized_person_name = self._sanitize_for_prompt(person_name)
        sanitized_entity_type = self._sanitize_for_prompt(entity_type)

        system_prompt = """You are an expert in financial crime screening interpretation specializing in sanctions,
PEP (Politically Exposed Person), and adverse media analysis.

Analyze screening results to:
1. Identify false positives (name similarities, historical records, different entities)
2. Consolidate confirmed hits
3. Rank severity of confirmed hits
4. Provide actionable recommendations

Return ONLY valid JSON (no markdown, no code blocks) with this exact structure:
{
    "consolidation_status": "<All Clear|Review Required|Escalate>",
    "total_hits": <number>,
    "confirmed_hits": <number>,
    "false_positive_hits": <number>,
    "false_positive_explanations": ["explanation1", "explanation2"],
    "severity_ranking": [
        {
            "hit": "hit description",
            "severity": "<LOW|MEDIUM|HIGH|CRITICAL>",
            "action": "recommended action"
        }
    ],
    "recommendation": "<All Clear|Review Required|Escalate>",
    "reasoning": "detailed reasoning for recommendation"
}"""

        user_prompt = f"""Interpret these FinCrime screening results:

ENTITY: {sanitized_person_name}
ENTITY TYPE: {sanitized_entity_type}

SCREENING RESULTS:
{json.dumps(self._deep_sanitize(screening_results), indent=2)}

Identify false positives, consolidate confirmed hits, rank severity, and provide recommendation."""

        try:
            response = self._call_claude(
                system_prompt,
                user_prompt,
                model="claude-sonnet-4-6",
                timeout=self.timeout_seconds,
            )
            result = self._parse_json_response(response, agent_method="interpret_fincrime_screening")
            result["ai_source"] = "claude-sonnet-4-6"
            return result
        except Exception as e:
            fail_result = self._check_fail_closed("interpret_fincrime_screening")
            if fail_result:
                fail_result["ai_error"] = str(e)[:200]
                return fail_result
            logger.error(f"FinCrime screening interpretation failed: {e} — returning mock response")
            result = _mock_interpret_fincrime_screening()
            result["ai_source"] = "mock"
            result["ai_error"] = str(e)[:200]
            return result

    # ── Compliance Memo Generation (Opus - thorough, detailed) ───

    def generate_compliance_memo(
        self,
        application_data: Dict[str, Any],
        agent_results: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Agent 5 (Part 2): Compliance Memo & Risk Recommendation Agent — Generate comprehensive compliance narrative.

        Synthesizes risk scoring, beneficial ownership analysis, FinCrime interpretation, and business plausibility
        assessment into a professional compliance memo suitable for board and regulatory review.

        Uses claude-opus-4-6 for thoroughness and quality.

        Args:
            application_data: Full application data dict
            agent_results: Results from Agent 2 (corporate structure), Agent 3 (business plausibility), Agent 4 (FinCrime), and risk scoring

        Returns:
            {
                "sections": { ... 11 mandatory sections ... },
                "metadata": {
                    "risk_rating": "LOW|MEDIUM|HIGH|VERY_HIGH",
                    "risk_score": int,
                    "confidence_level": float,
                    "approval_recommendation": "APPROVE|APPROVE_WITH_CONDITIONS|REVIEW|REJECT",
                    "key_findings": [...],
                    "conditions": [...],
                    "review_checklist": [...]
                }
            }
        """
        fail_result = self._check_fail_closed("generate_compliance_memo")
        if fail_result:
            return fail_result
        if self.mock_mode:
            logger.info("Returning mock compliance memo (mock mode)")
            result = _mock_generate_compliance_memo()
            result["ai_source"] = "mock"
            return result

        system_prompt = """You are a Senior MLRO, Big 4 Compliance Reviewer, and RegTech Analyst producing a gold-standard, regulator-grade compliance onboarding memo.

QUALITY STANDARD: This memo must pass a Big 4 compliance review. It must read as if written by a senior compliance officer exercising professional judgement — not as structured data output.

MANDATORY WRITING RULES:
1. PROFESSIONAL JUDGEMENT: Every section must include (a) what was assessed, (b) what was found, (c) why it matters for risk. Do NOT merely list facts — ANALYSE them.
2. RISK REASONING: Every risk rating must be JUSTIFIED with contextual explanation. BAD: "Jurisdiction assessed against FATF." GOOD: "Mauritius presents moderate jurisdictional risk. Although currently removed from the FATF grey list following its 2021 action plan completion, the jurisdiction retains characteristics of an offshore financial centre, elevating baseline risk for cross-border fund flows."
3. MISSING DATA: If ANY data is missing, state "Information not provided" AND assess the impact: "This data gap prevents full verification and elevates residual risk."
4. SCREENING: Never use the word "simulated". Reference screening sources where available. Include false positive analysis for any name matches.
5. DOCUMENTS: For each document, state verification status, consistency with other data, and any anomalies — not just "uploaded" or "verified".
6. OWNERSHIP: Include % ownership for ALL UBOs. If missing, flag as data gap. Include a structure complexity rating (Simple / Layered / Complex) and a control statement identifying who exercises effective control.
7. RED FLAGS: Always include 2-3+ meaningful, specific red flags (even for low-risk cases — e.g., "Limited trading history reduces ability to benchmark expected transaction patterns"). Each must have a corresponding specific mitigant.
8. DECISION: Must include rationale, link to risk assessment findings, and conditions. "Approved subject to enhanced monitoring due to PEP exposure and offshore jurisdiction classification."
9. NO GENERIC AI PHRASING. No repetition. No vague wording. Formal, precise, defensible.
10. Use ONLY the data provided. Do NOT hallucinate or invent facts.

Return ONLY valid JSON (no markdown, no code blocks) with this EXACT structure:

{
    "sections": {
        "executive_summary": {
            "title": "Executive Summary",
            "content": "Synthesis paragraph: entity identity, jurisdiction, composite risk rating with score, principal risk drivers, key mitigating factors, and recommendation with conditions. This must read as a standalone briefing for a board member."
        },
        "client_overview": {
            "title": "Client Overview",
            "content": "Entity name, BRN, jurisdiction, entity type, sector, application date and reference, stated business activity with assessment of plausibility, source of funds with adequacy assessment, expected transaction volume with benchmarking commentary."
        },
        "ownership_and_control": {
            "title": "Ownership & Control",
            "structure_complexity": "Simple|Layered|Complex",
            "control_statement": "Name of person(s) exercising effective control and basis for determination.",
            "content": "Full UBO chain with % ownership for each. Director details with nationality and PEP status. Nominee/bearer share assessment. Structure verification against registry. Professional judgement on opacity or governance concerns."
        },
        "risk_assessment": {
            "title": "Risk Assessment",
            "sub_sections": {
                "jurisdiction_risk": {"title": "Jurisdiction Risk", "rating": "LOW|MEDIUM|HIGH|VERY_HIGH", "content": "FATF status with context (grey list history, action plan progress), offshore classification rationale, cross-border risk implications, risk weighting factor with justification."},
                "business_risk": {"title": "Business Risk", "rating": "...", "content": "Sector inherent risk with reasoning, business model plausibility assessment, regulatory licence status and adequacy, revenue model consistency, risk weighting factor."},
                "transaction_risk": {"title": "Transaction Risk", "rating": "...", "content": "Expected volume assessment against sector norms, source of funds adequacy, unusual pattern indicators, ability to benchmark, risk weighting factor."},
                "ownership_risk": {"title": "Ownership Risk", "rating": "...", "content": "UBO identification completeness, PEP exposure and its implications, structure complexity assessment, nominee/layered structure concerns, risk weighting factor."},
                "financial_crime_risk": {"title": "Financial Crime Risk", "rating": "...", "content": "Sanctions screening findings with source and false positive analysis, adverse media assessment, AML typology relevance, predicate offence exposure, risk weighting factor."}
            }
        },
        "screening_results": {
            "title": "Screening Results",
            "content": "Sanctions: provider, lists checked, results with false positive analysis for any hits. PEP: matches with classification and risk implications. Adverse media: methodology and findings. Company registry: source, verification status, any discrepancies. Each finding must include professional assessment, not just data."
        },
        "document_verification": {
            "title": "Document Verification",
            "content": "For EACH document: type, verification status (valid/expired/inconsistent/missing), consistency with other submitted data, any anomalies or discrepancies identified. Overall documentation adequacy assessment with confidence %."
        },
        "ai_explainability": {
            "title": "AI Explainability Layer",
            "content": "Model version, methodology description, overall score with confidence %. Top 3 risk-increasing factors with weights and point contributions. Top 3 risk-decreasing factors with weights. Decision pathway through agent pipeline. Supervisor contradiction flags if any. Limitations or caveats."
        },
        "red_flags_and_mitigants": {
            "title": "Red Flags & Mitigants",
            "red_flags": ["Specific, contextual risk indicator with explanation of WHY it is a concern — minimum 2 entries even for low-risk cases"],
            "mitigants": ["Specific mitigating factor corresponding to each red flag with explanation of WHY it reduces the risk"]
        },
        "compliance_decision": {
            "title": "Compliance Decision",
            "decision": "APPROVE|APPROVE_WITH_CONDITIONS|REVIEW|REJECT",
            "content": "Decision with full rationale linking to risk assessment, screening, and ownership findings. Specific conditions with deadlines. Escalation triggers. Residual risk acknowledgement."
        },
        "ongoing_monitoring": {
            "title": "Ongoing Monitoring & Review",
            "content": "Monitoring tier with justification, review frequency tied to risk level, next review date, transaction monitoring parameters, specific trigger events with rationale for each."
        },
        "audit_and_governance": {
            "title": "Audit & Governance",
            "content": "Generation method and validation pipeline, document classification, retention period with regulatory basis, applicable compliance frameworks (specific legislation), generation timestamp, reviewer identity or 'Information not provided', version control."
        }
    },
    "metadata": {
        "risk_rating": "LOW|MEDIUM|HIGH|VERY_HIGH",
        "risk_score": 0,
        "confidence_level": 0.0,
        "approval_recommendation": "APPROVE|APPROVE_WITH_CONDITIONS|REVIEW|REJECT",
        "key_findings": ["finding1", "finding2"],
        "conditions": ["condition1 or empty array"],
        "review_checklist": ["item1", "item2"]
    }
}"""

        # Sanitize application data and agent results recursively (Finding 8 fix)
        sanitized_app_data = self._deep_sanitize(application_data)
        sanitized_agent_results = self._deep_sanitize(agent_results)

        user_prompt = f"""Generate a Big 4-grade compliance onboarding memo for this application.

Use ONLY the data provided below. If any data point is missing, explicitly state "Information not provided" and assess the impact on risk.

APPLICATION DATA:
{json.dumps(sanitized_app_data, indent=2)}

AGENT ANALYSIS RESULTS:
{json.dumps(sanitized_agent_results, indent=2)}

CRITICAL REQUIREMENTS:
1. Follow ALL 11 mandatory sections in exact order.
2. Every section must demonstrate PROFESSIONAL JUDGEMENT — explain what was found AND why it matters.
3. Every risk rating must include CONTEXTUAL REASONING — not just a label.
4. Ownership section must include % ownership for every UBO, a structure complexity rating, and a control statement.
5. Screening section must NEVER use "simulated". Include false positive analysis for any matches.
6. Document verification must assess each document's consistency with other data, not just list status.
7. Red Flags must contain minimum 2 specific, contextual entries with corresponding mitigants.
8. Decision must include rationale, conditions with deadlines, and residual risk acknowledgement.
9. AI Explainability must include factor-level weights, confidence %, and top 3 risk drivers.
10. The memo must read as written by a senior compliance officer — not as AI-generated structured data."""

        try:
            # Sprint 3.5: Risk-based model routing
            risk_score = application_data.get("risk_score", 50)
            risk_level = application_data.get("risk_level", "MEDIUM")
            selected_model, routing_reason = self.select_memo_model(risk_score, risk_level)

            response = self._call_claude(
                system_prompt,
                user_prompt,
                model=selected_model,
                timeout=self.timeout_seconds,
            )
            result = self._parse_json_response(response, agent_method="generate_compliance_memo")
            result["ai_source"] = selected_model
            result["ai_routing_reason"] = routing_reason
            return result
        except Exception as e:
            fail_result = self._check_fail_closed("generate_compliance_memo")
            if fail_result:
                fail_result["ai_error"] = str(e)[:200]
                return fail_result
            logger.error(f"Memo generation failed: {e} — returning mock response")
            result = _mock_generate_compliance_memo()
            result["ai_source"] = "mock"
            result["ai_error"] = str(e)[:200]
            return result

    # ── Document Verification (Sonnet - fast, cheap) ──────────────

    # ── Deterministic check definitions per document type ──
    # Single source of truth — MUST align exactly with ENTITY_DOC_CHECKS and PERSON_DOC_CHECKS in back office.
    # BOOTSTRAP DEFAULTS ONLY — runtime loads from ai_checks DB table via check_overrides parameter.
    # These definitions are used ONLY when DB lookup fails or returns empty.
    # The canonical source of truth is the ai_checks database table, editable via back office.
    #
    # Claude evaluates each check — it does NOT decide what checks to run.
    # Keys: type (used for matching in UI), label (display name), rule (what Claude must verify)
    _DOC_CHECK_DEFINITIONS = {
        # ── Corporate Entity Documents (aligned with ENTITY_DOC_CHECKS in backoffice) ──
        "poa": [
            {"id": "DOC-01", "type": "age", "label": "Document Date", "rule": "Must be dated within the last 3 months. PASS if dated within 3 months. WARN if dated 3-6 months ago. FAIL if older than 6 months or undated."},
            {"id": "DOC-02", "type": "name", "label": "Entity Name Match", "rule": "Entity name on document must match application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing."},
            {"id": "DOC-04", "type": "content", "label": "Address Match", "rule": "Address must match registered office address on application. PASS if address matches. WARN if partial match. FAIL if mismatch or missing."},
            {"id": "DOC-03", "type": "quality", "label": "Document Clarity", "rule": "Document must be legible and unredacted. PASS if fully legible. WARN if partially legible. FAIL if illegible or blank."},
            {"id": "CERT-01", "type": "quality", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification."},
        ],
        "cert_inc": [
            {"id": "DOC-05", "type": "name", "label": "Entity Name Match", "rule": "Company name must match application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing."},
            {"id": "DOC-06", "type": "content", "label": "Registration Number Match", "rule": "Registration number must match the number declared in pre-screening. PASS if present and matches. WARN if partially legible. FAIL if missing or mismatch."},
            {"id": "DOC-11", "type": "age", "label": "Date of Incorporation Match", "rule": "Date of incorporation must match pre-screening declaration. PASS if date matches. WARN if date differs by less than 6 months. FAIL if mismatch or missing."},
            {"id": "DOC-12", "type": "content", "label": "Jurisdiction Match", "rule": "Jurisdiction/country of incorporation must match pre-screening. PASS if jurisdiction matches. WARN if abbreviation/variant used. FAIL if mismatch or missing."},
            {"id": "DOC-07", "type": "quality", "label": "Document Clarity", "rule": "Document must be legible, certified copy if applicable. PASS if legible. WARN if partially legible. FAIL if illegible or blank."},
            {"id": "CERT-01", "type": "quality", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification."},
        ],
        "memarts": [
            {"id": "DOC-08", "type": "name", "label": "Entity Name Match", "rule": "Company name must match application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing."},
            {"id": "DOC-16", "type": "content", "label": "Authorised Share Capital", "rule": "Authorised share capital must match the amount declared in pre-screening. PASS if capital matches. WARN if minor discrepancy. FAIL if mismatch or missing."},
            {"id": "DOC-09", "type": "quality", "label": "Completeness", "rule": "All pages must be present and legible. PASS if complete and legible. WARN if minor pages missing. FAIL if key pages missing or illegible."},
            {"id": "DOC-13", "type": "ai", "label": "Business Objects / Activities", "rule": "Declared business activities must fall within the objects clause of the MoA. PASS if activities within objects. WARN if partial overlap. FAIL if activities clearly outside objects. ESCALATE if uncertain."},
            {"id": "CERT-01", "type": "quality", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification."},
        ],
        # cert_reg retired — no checks. Doc type preserved for historical records only.
        "cert_reg": [],
        "reg_sh": [
            {"id": "DOC-14", "type": "name", "label": "Shareholder Name Match", "rule": "Shareholder names must match those declared in pre-screening. PASS if all names match (fuzzy > 90%). WARN if minor name variations. FAIL if names cannot be matched or are missing."},
            {"id": "DOC-15", "type": "content", "label": "Shareholding Percentages Match", "rule": "Shareholding percentages must match those declared in pre-screening. PASS if all percentages match. WARN if minor discrepancies (< 5%). FAIL if major discrepancies or missing."},
            {"id": "DOC-22", "type": "content", "label": "Total Shares Sum to 100%", "rule": "Total shareholdings must sum to 100%. PASS if totals 100%. WARN if totals 95-100% (rounding). FAIL if < 95% or > 100%."},
            {"id": "DOC-23", "type": "content", "label": "UBO Identification (\u226525%)", "rule": "Any shareholder holding \u2265 25% must be identified as a declared UBO. PASS if all \u226525% shareholders are declared UBOs. WARN if borderline (24-26%). FAIL if \u226525% shareholder not declared as UBO."},
            {"id": "CERT-01", "type": "quality", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification."},
        ],
        "reg_dir": [
            {"id": "DOC-17", "type": "name", "label": "Director Name Match", "rule": "Directors must match those declared in pre-screening. PASS if all directors match (fuzzy > 90%). WARN if minor name variations. FAIL if directors missing or undeclared directors present."},
            {"id": "DOC-18", "type": "content", "label": "Completeness", "rule": "All current directors must be listed. PASS if all listed. WARN if count uncertain. FAIL if directors clearly missing."},
            {"id": "DOC-19", "type": "quality", "label": "Document Clarity", "rule": "Must be legible. PASS if legible. WARN if partially legible. FAIL if illegible."},
            {"id": "CERT-01", "type": "quality", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification."},
        ],
        "fin_stmt": [
            {"id": "DOC-20", "type": "age", "label": "Financial Period", "rule": "Must be for most recent financial year (or forecast if < 1 year old). PASS if within last 18 months. WARN if 18-24 months old. FAIL if older than 24 months."},
            {"id": "DOC-21", "type": "name", "label": "Entity Name Match", "rule": "Company name on statements must match application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing."},
            {"id": "DOC-61", "type": "content", "label": "Revenue / Turnover Consistency", "rule": "Revenue or turnover figures must be broadly consistent with the annual turnover declared in pre-screening. PASS if within 20%. WARN if 20-50% variance. FAIL if > 50% variance or figures missing."},
            {"id": "CERT-01", "type": "quality", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification."},
        ],
        "board_res": [
            {"id": "DOC-24", "type": "name", "label": "Signatory Match", "rule": "Authorised signatory must be a declared director. PASS if signatory is a declared director. WARN if name variation. FAIL if signatory not a director."},
            {"id": "DOC-25", "type": "age", "label": "Resolution Date", "rule": "Must be dated and reasonably current. PASS if dated within 12 months. WARN if 12-24 months old. FAIL if undated or older than 24 months."},
            {"id": "DOC-26", "type": "ai", "label": "Scope of Authority", "rule": "Resolution must explicitly authorise the signatory to open a bank/payment account or engage the relevant service provider. PASS if explicit authorisation present. WARN if implicit only. FAIL if authorisation not found. ESCALATE if legal language is ambiguous."},
            {"id": "CERT-01", "type": "quality", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification."},
        ],
        "structure_chart": [
            {"id": "DOC-27", "type": "content", "label": "UBO Chain", "rule": "Must trace ownership to ultimate beneficial owners. PASS if UBO chain complete. WARN if chain incomplete but UBOs identifiable. FAIL if UBOs not identifiable."},
            {"id": "DOC-28", "type": "content", "label": "Ownership Match", "rule": "Shareholdings must match shareholder register. PASS if percentages match. WARN if minor discrepancies. FAIL if major discrepancies."},
            {"id": "DOC-29", "type": "quality", "label": "Legibility", "rule": "Diagram must be clear and readable. PASS if legible. WARN if partially legible. FAIL if illegible."},
            {"id": "CERT-01", "type": "quality", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification."},
        ],
        "bankref": [
            {"id": "DOC-30", "type": "quality", "label": "Bank Letterhead", "rule": "Must be on official bank letterhead. PASS if on letterhead. WARN if letterhead unclear. FAIL if no letterhead."},
            {"id": "DOC-31", "type": "age", "label": "Date", "rule": "Must be dated within the last 3 months. PASS if within 3 months. WARN if 3-6 months. FAIL if older than 6 months or undated."},
            {"id": "DOC-32", "type": "name", "label": "Entity Name Match", "rule": "Entity name must match application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing."},
            {"id": "CERT-01", "type": "quality", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification."},
        ],
        "licence": [
            {"id": "DOC-33", "type": "name", "label": "Entity Name Match", "rule": "Entity name must match. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing."},
            {"id": "DOC-34", "type": "expiry", "label": "Licence Validity", "rule": "Licence must be current and not expired. PASS if valid. WARN if expiring within 30 days. FAIL if expired."},
            {"id": "DOC-35", "type": "ai", "label": "Licence Scope", "rule": "Licence must cover the business activities declared in the application. PASS if scope covers activities. WARN if partial coverage. FAIL if activities fall outside licence scope. ESCALATE if scope is ambiguous."},
            {"id": "CERT-01", "type": "quality", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification."},
        ],
        # ── Additional entity docs (portal-only, no backoffice config yet) ──
        "contracts": [
            {"id": "DOC-36", "type": "name", "label": "Name Match", "rule": "Entity name must appear in the contract. PASS if name present and matches. WARN if partial match. FAIL if not present."},
            {"id": "DOC-37", "type": "content", "label": "Relevance", "rule": "Contract must be relevant to the declared business activity. PASS if relevant. WARN if tangentially related. FAIL if unrelated."},
            {"id": "DOC-38", "type": "quality", "label": "Clarity", "rule": "Document must be legible. PASS if legible. WARN if partially legible. FAIL if illegible."},
        ],
        "aml_policy": [
            {"id": "DOC-39", "type": "content", "label": "Completeness", "rule": "Must cover key AML areas (CDD, sanctions screening, reporting). PASS if all key areas covered. WARN if minor gaps. FAIL if major areas missing."},
            {"id": "DOC-40", "type": "age", "label": "Date", "rule": "Policy must be dated and reviewed within last 12 months. PASS if within 12 months. WARN if 12-24 months. FAIL if older or undated."},
            {"id": "DOC-41", "type": "content2", "label": "Relevance", "rule": "Must be relevant to the entity's business activities. PASS if relevant. WARN if generic. FAIL if irrelevant."},
        ],
        "source_wealth": [
            {"id": "DOC-42", "type": "content", "label": "Consistency", "rule": "Must be consistent with declared source of wealth in application. PASS if consistent. WARN if minor gaps. FAIL if contradicts declaration."},
            {"id": "DOC-43", "type": "quality", "label": "Clarity", "rule": "Document must be legible and credible. PASS if legible and credible. WARN if partially legible. FAIL if illegible or not credible."},
        ],
        "source_funds": [
            {"id": "DOC-44", "type": "content", "label": "Consistency", "rule": "Must be consistent with declared source of funds in application. PASS if consistent. WARN if minor gaps. FAIL if contradicts declaration."},
            {"id": "DOC-45", "type": "quality", "label": "Clarity", "rule": "Document must be legible and credible. PASS if legible and credible. WARN if partially legible. FAIL if illegible or not credible."},
        ],
        "bank_statements": [
            {"id": "DOC-71", "type": "age", "label": "Period", "rule": "Must cover a recent period (within last 6 months). PASS if within 6 months. WARN if 6-12 months. FAIL if older than 12 months."},
            {"id": "DOC-72", "type": "name", "label": "Name Match", "rule": "Account holder name must match the declared entity or person. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing."},
            {"id": "DOC-73", "type": "quality", "label": "Completeness", "rule": "All pages must be present. PASS if complete. WARN if minor pages missing. FAIL if key pages missing."},
        ],
        # ── KYC Person Documents (aligned with PERSON_DOC_CHECKS in backoffice) ──
        "passport": [
            {"id": "DOC-48", "type": "expiry", "label": "Document Expiry", "rule": "Passport must not be expired. PASS if > 6 months validity remaining. WARN if 1-6 months remaining. FAIL if expired."},
            {"id": "DOC-50", "type": "name", "label": "Name Match", "rule": "Name must match the person declared in the application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing."},
            {"id": "DOC-62", "type": "content", "label": "Date of Birth Match", "rule": "Date of birth must match the date declared in pre-screening. PASS if date matches. WARN if format variation only. FAIL if mismatch or missing."},
            {"id": "DOC-63", "type": "content", "label": "Nationality Match", "rule": "Nationality must match declared nationality. PASS if matches. WARN if not clearly visible. FAIL if mismatch."},
            {"id": "DOC-49", "type": "quality", "label": "Photo Quality", "rule": "Photo must be clear and identifiable. PASS if clear. WARN if partially obscured. FAIL if unidentifiable."},
            {"id": "CERT-01", "type": "quality", "label": "Certification", "rule": "Must be certified by a notary, lawyer, or accountant. PASS if certified. WARN if signed but uncertified. FAIL if no signature or certification."},
        ],
        "national_id": [
            {"id": "DOC-53", "type": "expiry", "label": "Document Expiry", "rule": "Must not be expired. PASS if valid. WARN if expiring within 30 days. FAIL if expired."},
            {"id": "DOC-54", "type": "quality", "label": "Photo Quality", "rule": "Photo must be clear and identifiable. PASS if clear. WARN if partially obscured. FAIL if unidentifiable."},
            {"id": "DOC-55", "type": "name", "label": "Name Match", "rule": "Name must match the person declared in the application. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing."},
            {"id": "DOC-56", "type": "content", "label": "Nationality Match", "rule": "Nationality must match declared nationality. PASS if matches. WARN if not clearly visible. FAIL if mismatch."},
        ],
        "cv": [
            {"id": "DOC-57", "type": "name", "label": "Name Match", "rule": "Name on CV/profile must match declared identity. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing."},
            {"id": "DOC-58", "type": "content", "label": "Employment History \u2014 Presence", "rule": "CV must include employment history section. PASS if history present. WARN if limited entries. FAIL if no employment history."},
            {"id": "DOC-65", "type": "ai", "label": "Employment History \u2014 Relevance", "rule": "Employment background must be relevant to the declared role and business activity. PASS if background is relevant. WARN if tangentially related. FAIL if no relevant experience. ESCALATE if background appears inconsistent with declared role."},
        ],
        "sow": [
            {"id": "DOC-59", "type": "name", "label": "Name Match", "rule": "Name must match the declared person. PASS if names match exactly or fuzzy match > 90%. WARN if fuzzy match 70-90%. FAIL if < 70% or missing."},
            {"id": "DOC-60", "type": "ai", "label": "Source of Wealth Evidence", "rule": "Document must contain credible evidence supporting the declared source of wealth. PASS if credible evidence present. WARN if evidence weak or indirect. FAIL if no evidence. ESCALATE if declared source appears implausible."},
            {"id": "DOC-66", "type": "ai", "label": "Consistency / Plausibility", "rule": "Declared wealth, function, and source of funds must be internally consistent and plausible. PASS if consistent and plausible. WARN if minor inconsistencies. FAIL if major inconsistencies. ESCALATE for compliance officer review."},
        ],
    }

    # Legacy text rules (kept for backward compatibility)
    _DOC_VERIFICATION_RULES = {k: " ".join(f"({i+1}) {c['rule']}" for i, c in enumerate(v)) for k, v in _DOC_CHECK_DEFINITIONS.items()}

    # Field extraction schemas: doc_type → fields Claude should extract
    _EXTRACTION_SCHEMAS = {
        "cert_inc":      ["entity_name", "registration_number", "incorporation_date", "country_of_incorporation"],
        "memarts":       ["entity_name", "registration_number", "objects_clause_present", "certification_present"],
        "reg_sh":        ["entity_name", "shareholders", "total_percentage", "register_date"],
        "reg_dir":       ["entity_name", "directors", "register_date"],
        "fin_stmt":      ["entity_name", "period_start", "period_end", "balance_sheet_present", "pnl_present"],
        "board_res":     ["entity_name", "resolution_date", "authorised_signatory", "purpose"],
        "structure_chart": ["entity_name", "ubo_names", "ownership_percentages", "chart_date"],
        "bankref":       ["entity_name", "bank_name", "letter_date", "account_holder"],
        "licence":       ["entity_name", "licence_number", "issuing_authority", "issue_date", "expiry_date", "licence_type"],
        "poa":           ["entity_name", "document_date", "address"],
        "passport":      ["full_name", "date_of_birth", "nationality", "expiry_date", "document_number"],
        "national_id":   ["full_name", "date_of_birth", "nationality", "expiry_date", "document_number"],
        "cv":            ["full_name", "employment_history_summary"],
        "sow":           ["full_name", "wealth_source_description"],
        "poa_person":    ["full_name", "document_date", "address"],
    }

    def extract_document_fields(
        self,
        doc_type: str,
        file_path: str,
        file_name: str = "",
        entity_name: str = "",
        person_name: str = "",
    ) -> dict:
        """
        Use Claude vision to extract structured fields from a document.
        Called by verify_document_layered() before rule evaluation.
        Returns a flat dict of field_name → extracted_value (strings/lists).
        On failure, returns {} — caller must handle gracefully.
        """
        fail_closed = self._check_fail_closed("extract_document_fields")
        if fail_closed is not None:
            return {}

        schema = self._EXTRACTION_SCHEMAS.get(doc_type, [])
        if not schema or not file_path:
            return {}

        if self.mock_mode:
            return {}  # No mock extraction — rule engine uses pre-screening data

        schema_list = "\n".join(f"- {f}" for f in schema)
        context_hint = ""
        if entity_name:
            context_hint += f" The expected entity name is '{entity_name}'."
        if person_name:
            context_hint += f" The expected person name is '{person_name}'."

        system_prompt = (
            "You are a document field extraction assistant. Extract the specified fields "
            "from the provided document image or PDF. Return ONLY a JSON object with the "
            "field names as keys and extracted values as strings (or arrays for list fields). "
            "Use null for fields that cannot be found. Do not add commentary."
        )
        user_prompt = (
            f"Document type: {doc_type}\nFile: {file_name}{context_hint}\n\n"
            f"Extract these fields:\n{schema_list}\n\n"
            "Return JSON only."
        )

        try:
            file_blocks = self._read_file_for_vision(file_path)
            content_blocks = file_blocks + [{"type": "text", "text": user_prompt}]
            raw = self._call_claude(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model="claude-sonnet-4-6",
                content_blocks=content_blocks if file_blocks else None,
            )
            parsed = self._parse_json_response(raw, "extract_document_fields")
            if isinstance(parsed, dict):
                return {k: v for k, v in parsed.items() if v is not None}
            return {}
        except Exception as e:
            logger.warning(f"extract_document_fields failed for {doc_type}: {e}")
            return {}

    def verify_document(
        self,
        doc_type: str,
        file_name: str,
        person_name: str = "",
        doc_category: str = "identity",
        file_path: str = None,
        check_overrides: list = None,
        entity_name: str = "",
        directors: list = None,
        ubos: list = None,
    ) -> Dict[str, Any]:
        """
        Agent 1: Identity & Document Integrity Agent — Verify document authenticity and compliance using Claude AI.

        When file_path is provided, sends the actual document to Claude's vision API for
        real content analysis. Without file_path, falls back to metadata-only verification.

        Args:
            doc_type: Type of document (passport, national_id, poa, cv, bankref, etc)
            file_name: Name of the uploaded file
            person_name: Name of the person the document belongs to (optional)
            doc_category: Category of document (identity, company, kyc, address, etc)
            file_path: Path to the actual file on disk (optional, enables vision analysis)
            check_overrides: Optional list of check dicts from ai_checks DB table.
                           If provided, used instead of _DOC_CHECK_DEFINITIONS.
            entity_name: Registered entity/company name from pre-screening (optional)
            directors: List of declared director full names (optional)
            ubos: List of declared UBO full names (optional)

        Returns:
            {
                "checks": [
                    {
                        "label": "Check Name",
                        "type": "check_type",
                        "result": "pass"|"warn"|"fail",
                        "message": "Details about the check"
                    },
                    ...
                ],
                "overall": "verified"|"flagged",
                "confidence": 0.85,
                "ai_source": "claude-sonnet-4-6"
            }
        """
        fail_result = self._check_fail_closed("verify_document")
        if fail_result:
            return fail_result
        if self.mock_mode:
            logger.info("Returning mock document verification (mock mode)")
            result = _mock_verify_document()
            result["ai_source"] = "mock"
            return result

        # Sanitize person name to prevent prompt injection
        sanitized_person_name = self._sanitize_for_prompt(person_name)

        # Read file for vision if available
        file_content_blocks = []
        if file_path:
            file_content_blocks = self._read_file_for_vision(file_path)
            if file_content_blocks:
                logger.info(f"Document vision enabled for {doc_type}: {file_name}")
            else:
                logger.warning(f"Could not read file for vision, falling back to metadata: {file_path}")

        # Get deterministic check definitions for this doc type
        # check_overrides (from ai_checks DB table) take priority over hardcoded _DOC_CHECK_DEFINITIONS
        if check_overrides:
            check_defs = check_overrides
        else:
            check_defs = self._DOC_CHECK_DEFINITIONS.get(doc_type, [
                {"type": "doc_type_match", "label": "Document Type Match", "rule": "Verify document matches claimed type"},
                {"type": "quality", "label": "Document Quality", "rule": "Document must be legible and complete"},
                {"type": "name", "label": "Name Match", "rule": "Name must match declared person or entity"},
            ])

        has_vision = len(file_content_blocks) > 0

        from datetime import date as _date
        today_str = _date.today().strftime("%d %B %Y")

        # Build deterministic check list for the prompt
        checks_prompt = "\n".join(
            f'{i+1}. [ID: {c.get("id", "DOC-XX")}] "{c["label"]}" (type: "{c["type"]}") — {c["rule"]}'
            for i, c in enumerate(check_defs)
        )

        system_prompt = f"""You are Agent 1: Identity & Document Integrity Agent for a regulated financial compliance platform.

TODAY'S DATE IS: {today_str}. Use this as the reference date for all expiry and date checks.

You MUST verify uploaded documents with strict compliance standards. {"An image/document of the actual file is attached — you MUST analyze its visual content." if has_vision else "No file image is attached — analyze based on metadata only and flag that visual verification was not possible."}

CRITICAL INSTRUCTION: You must evaluate EXACTLY the checks listed below — no more, no less.
Do NOT add extra checks. Do NOT skip any checks. Do NOT rename them.
Each check must appear in your response with the exact "type" value specified.

CHECKS TO EVALUATE FOR "{doc_type}" (evaluate ALL {len(check_defs)} checks):
{checks_prompt}

Return ONLY valid JSON (no markdown, no code blocks) with this exact structure:
{{
    "checks": [
        {{
            "id": "DOC-XX",
            "label": "Check Name",
            "type": "check_type",
            "result": "pass"|"warn"|"fail",
            "message": "Explanation of the check result"
        }}
    ],
    "overall": "verified"|"flagged",
    "confidence": <0.0-1.0>,
    "red_flags": ["flag1", "flag2"]
}}

RULES:
- You MUST return exactly {len(check_defs)} checks, one for each listed above
- Each check MUST include the exact "id" value specified (e.g., "DOC-01", "DOC-02", etc.)
- Use the exact "type" values specified (e.g., "doc_type_match", "entity_name", etc.)
- Each result MUST be exactly "pass", "warn", or "fail" — no other values
- If ANY check has result "fail", the overall MUST be "flagged"
- If ANY check has result "warn", the overall MUST be "flagged" unless overridden by pass evidence
- If you cannot evaluate a check (e.g., no file attached), use result "warn" with explanation
- Be strict — compliance errors have real regulatory consequences"""

        # Build application context for cross-referencing
        sanitized_entity = self._sanitize_for_prompt(entity_name) if entity_name else ""
        sanitized_directors = [self._sanitize_for_prompt(d) for d in (directors or []) if d]
        sanitized_ubos = [self._sanitize_for_prompt(u) for u in (ubos or []) if u]

        app_context_lines = []
        if sanitized_entity:
            app_context_lines.append(f"Entity Name: {sanitized_entity}")
        if sanitized_directors:
            app_context_lines.append(f"Declared Directors: {', '.join(sanitized_directors)}")
        if sanitized_ubos:
            app_context_lines.append(f"Declared UBOs: {', '.join(sanitized_ubos)}")
        if sanitized_person_name:
            app_context_lines.append(f"Associated Person for this document: {sanitized_person_name}")

        app_context_block = ""
        if app_context_lines:
            app_context_block = "\n\nApplication Context (use for cross-referencing names in the document):\n- " + "\n- ".join(app_context_lines)

        user_prompt = f"""Verify this document:

Claimed Document Type: {doc_type}
File Name: {file_name}
Document Category: {doc_category}
Associated Person/Entity: {sanitized_person_name if sanitized_person_name else "Not provided"}{app_context_block}

{"Analyze the attached document image/file carefully." if has_vision else "No file attached — verify based on metadata and flag that manual review is recommended."}
Evaluate ONLY the {len(check_defs)} checks specified in your instructions. Return results for each one."""

        try:
            # Build multimodal content blocks if file is available
            content_blocks = None
            if file_content_blocks:
                content_blocks = file_content_blocks + [{"type": "text", "text": user_prompt}]

            response = self._call_claude(
                system_prompt,
                user_prompt,
                model="claude-sonnet-4-6",
                timeout=45,  # Increased for vision processing
                content_blocks=content_blocks,
            )
            result = self._parse_json_response(response, agent_method="verify_document")
            result["ai_source"] = "claude-sonnet-4-6"
            result["vision_enabled"] = has_vision
            return result
        except Exception as e:
            logger.error(f"Document verification failed: {e} — returning flagged response")
            # In non-mock mode, return flagged (not fake passes) when AI fails
            return {
                "checks": [
                    {"label": "AI Verification", "type": "validity", "result": "warn", "message": f"AI verification unavailable: {str(e)[:100]}. Manual review required."},
                    {"label": "Document Type Match", "type": "doc_type_match", "result": "warn", "message": "Could not verify — manual check required"},
                ],
                "overall": "flagged",
                "confidence": 0.0,
                "red_flags": ["AI verification failed — manual review required"],
                "ai_source": "error",
                "ai_error": str(e)[:200],
                "vision_enabled": has_vision,
            }

    # ── Internal Helper Methods ─────────────────────────────────

    def _read_file_for_vision(self, file_path: str) -> list:
        """
        Read a document file and prepare it for Claude vision API.

        Supports JPG, PNG (as images) and PDF (as documents).
        Returns content blocks for the Messages API, or empty list on failure.
        """
        import base64 as b64mod
        import mimetypes

        if not file_path or not os.path.isfile(file_path):
            logger.warning(f"File not found for vision: {file_path}")
            return []

        file_size = os.path.getsize(file_path)
        if file_size > 10 * 1024 * 1024:  # 10MB limit
            logger.warning(f"File too large for vision ({file_size} bytes): {file_path}")
            return []

        ext = os.path.splitext(file_path)[1].lower()
        mime_map = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".pdf": "application/pdf",
        }
        mime_type = mime_map.get(ext)
        if not mime_type:
            logger.warning(f"Unsupported file type for vision: {ext}")
            return []

        try:
            with open(file_path, "rb") as f:
                file_data = b64mod.standard_b64encode(f.read()).decode("utf-8")

            if ext == ".pdf":
                return [{"type": "document", "source": {"type": "base64", "media_type": mime_type, "data": file_data}}]
            else:
                return [{"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": file_data}}]
        except Exception as e:
            logger.error(f"Failed to read file for vision: {e}")
            return []

    def generate(
        self,
        prompt: str,
        max_tokens: int = 300,
        model: str = None,
        timeout: int = 30,
    ) -> str:
        """
        Public text-generation method for monitoring agents and free-form prompts.

        Unlike the structured agent methods (score_risk, verify_document, etc.),
        this returns raw text — no JSON parsing, no schema validation.

        Args:
            prompt: The user prompt / instruction.
            max_tokens: Maximum tokens in the response (default 300).
            model: Model override. Defaults to the fast model (Sonnet).
            timeout: Timeout in seconds.

        Returns:
            Raw text string from Claude, or empty string on failure.
        """
        if self.mock_mode:
            logger.info("generate() called in mock mode — returning empty string")
            return ""

        fail_result = self._check_fail_closed("generate")
        if fail_result:
            raise RuntimeError(fail_result.get("error", "Fail-closed mode active"))

        chosen_model = model or self.ROUTING_MODELS["fast"]
        system_prompt = "You are a compliance monitoring assistant. Provide concise, factual responses."

        try:
            if not self.client:
                logger.warning("generate() called but Claude client not initialised")
                return ""

            response = self.client.messages.create(
                model=chosen_model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout,
            )

            text_content = response.content[0].text
            self.usage_tracker.log_usage(
                model=chosen_model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )
            _record_persistent_usage(
                chosen_model, response.usage.input_tokens,
                response.usage.output_tokens, method="generate"
            )
            return text_content

        except Exception as e:
            logger.error(f"generate() failed: {e}")
            return ""

    def _call_claude(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str = "claude-sonnet-4-6",
        timeout: int = 30,
        content_blocks: list = None,
    ) -> str:
        """
        Make a call to Claude API with retry logic and timeout handling.

        Args:
            system_prompt: System context for the AI agent
            user_prompt: User input/query
            model: Model to use (claude-sonnet-4-6 or claude-opus-4-6)
            timeout: Timeout in seconds
            content_blocks: Optional multimodal content blocks (images/documents + text).
                            When provided, replaces user_prompt in the message.

        Returns:
            Raw response text from Claude

        Raises:
            Exception: If all retries fail or API is unavailable
        """
        if not self.client:
            raise RuntimeError(
                "Claude client not available. Initialize with valid API key or enable mock mode."
            )

        # Persistent budget enforcement — block if monthly cap exceeded
        if not _check_persistent_budget():
            raise RuntimeError(
                "Claude API monthly budget exceeded. Check usage via /api/config/ai-agents or contact admin."
            )

        # Build message content — multimodal if content_blocks provided, else plain text
        message_content = content_blocks if content_blocks else user_prompt

        for attempt in range(self.max_retries):
            try:
                logger.debug(
                    f"Calling Claude {model} (attempt {attempt + 1}/{self.max_retries})"
                )

                response = self.client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=system_prompt,
                    messages=[{"role": "user", "content": message_content}],
                    timeout=timeout,
                )

                # Extract text content and track usage (in-memory + persistent)
                text_content = response.content[0].text
                self.usage_tracker.log_usage(
                    model=model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                )
                _record_persistent_usage(
                    model, response.usage.input_tokens,
                    response.usage.output_tokens, method="_call_claude"
                )

                logger.debug(f"Claude {model} request succeeded")
                return text_content

            except APITimeoutError as e:
                logger.warning(
                    f"Claude {model} request timeout (attempt {attempt + 1}/{self.max_retries}): {e}"
                )
                if attempt == self.max_retries - 1:
                    raise RuntimeError(f"Claude API timeout after {self.max_retries} retries") from e
                # Exponential backoff with jitter: 2^attempt * (1 + random 0-0.5s)
                backoff = (2 ** attempt) * (1 + random.uniform(0, 0.5))
                logger.info(f"Retrying in {backoff:.1f}s after timeout...")
                time.sleep(backoff)

            except APIConnectionError as e:
                logger.warning(
                    f"Claude connection error (attempt {attempt + 1}/{self.max_retries}): {e}"
                )
                if attempt == self.max_retries - 1:
                    raise RuntimeError(
                        f"Claude API connection failed after {self.max_retries} retries"
                    ) from e
                backoff = (2 ** attempt) * (1 + random.uniform(0, 0.5))
                logger.info(f"Retrying in {backoff:.1f}s after connection error...")
                time.sleep(backoff)

            except APIError as e:
                logger.error(f"Claude API error: {e}")
                if attempt == self.max_retries - 1:
                    raise RuntimeError(f"Claude API error: {e}") from e
                # For rate-limit (429) errors, use longer backoff
                backoff_base = 4 if getattr(e, 'status_code', 0) == 429 else 2
                backoff = (backoff_base ** attempt) * (1 + random.uniform(0, 0.5))
                logger.info(f"Retrying in {backoff:.1f}s after API error (status: {getattr(e, 'status_code', 'unknown')})...")
                time.sleep(backoff)

    def _parse_json_response(self, response_text: str, agent_method: str = None) -> Dict[str, Any]:
        """
        C-03: Parse and VALIDATE JSON from Claude response.

        Handles potential markdown code blocks, then validates against
        the Pydantic schema for the calling agent method.

        Args:
            response_text: Raw text from Claude
            agent_method: Name of the calling method (e.g. "score_risk") for schema lookup

        Returns:
            Validated and parsed JSON as dict

        Raises:
            ValueError: If JSON cannot be parsed or fails schema validation
        """
        text = response_text.strip()

        # Handle markdown code blocks
        if text.startswith("```"):
            # Remove opening code block marker
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            # Remove closing code block marker
            if text.endswith("```"):
                text = text[: -3].rstrip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response as JSON: {e}\nText: {text[:200]}")
            raise ValueError(f"Invalid JSON from Claude: {e}") from e

        # C-03: Pydantic schema validation
        if PYDANTIC_AVAILABLE and agent_method and agent_method in _AGENT_SCHEMAS:
            schema_cls = _AGENT_SCHEMAS[agent_method]
            try:
                validated = schema_cls.model_validate(parsed)
                logger.debug(f"AI output validated against {schema_cls.__name__}")
                result = validated.model_dump()

                # H-03 FIX: Enforce minimum confidence threshold
                CONFIDENCE_THRESHOLD = _CFG_AI_CONFIDENCE_THRESHOLD
                confidence = result.get("confidence") or result.get("overall_confidence") or result.get("risk_confidence")
                if confidence is not None and float(confidence) < CONFIDENCE_THRESHOLD:
                    logger.warning(
                        f"H-03 SECURITY: AI output below confidence threshold for {agent_method}: "
                        f"{confidence} < {CONFIDENCE_THRESHOLD}. Flagging for manual review."
                    )
                    result["_low_confidence"] = True
                    result["_confidence_value"] = float(confidence)
                    result["_confidence_threshold"] = CONFIDENCE_THRESHOLD
                    result["_requires_manual_review"] = True

                return result
            except ValidationError as e:
                logger.error(
                    f"H-02 SECURITY: AI output REJECTED for {agent_method} — schema validation failed: {e}"
                )
                # H-02 FIX: Hard reject invalid AI output — route to manual review
                # In a regulated compliance system, malformed AI output MUST NOT proceed
                return {
                    "_validated": False,
                    "_rejected": True,
                    "_validation_errors": str(e),
                    "_requires_manual_review": True,
                    "_agent_method": agent_method,
                    "_raw_text_hash": hashlib.sha256(str(parsed).encode()).hexdigest()[:16],
                    "error": f"AI output rejected: schema validation failed for {agent_method}. Manual review required."
                }
        else:
            if not PYDANTIC_AVAILABLE:
                logger.warning("Pydantic not available — AI output validation SKIPPED")

        return parsed

    def get_usage_stats(self) -> Dict[str, Any]:
        """Get current API usage and cost statistics."""
        return self.usage_tracker.get_monthly_stats()

    def check_budget(self) -> Tuple[bool, str]:
        """
        Check if under monthly budget.

        Returns:
            (is_within_budget, status_message)
        """
        stats = self.get_usage_stats()
        is_within = stats["remaining_budget_usd"] >= 0
        message = f"${stats['total_cost_usd']:.2f} / ${stats['monthly_budget_usd']:.2f}"
        return is_within, message

    def __repr__(self) -> str:
        mode = "MOCK" if self.mock_mode else "LIVE"
        stats = self.get_usage_stats()
        return (
            f"ClaudeClient(mode={mode}, "
            f"cost=${stats['total_cost_usd']:.2f}/{stats['monthly_budget_usd']:.2f}, "
            f"calls={stats['total_api_calls']})"
        )


# ── Module Initialization ───────────────────────────────────────

if __name__ == "__main__":
    # Quick test of the module
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("arie.claude")

    client = ClaudeClient(mock_mode=True)
    logger.info(f"Initialized: {client}")

    # Test risk scoring
    test_data = {
        "company_name": "Example Corp Ltd",
        "entity_type": "Private Company",
        "country": "Mauritius",
        "sector": "Technology",
        "directors": [{"full_name": "John Smith", "is_pep": "No"}],
        "ubos": [{"full_name": "Jane Doe", "ownership_pct": 100, "is_pep": "No"}],
    }

    logger.info("Testing risk_score()...")
    risk_result = client.score_risk(test_data)
    logger.info(f"Risk score: {risk_result['overall_score']}, Level: {risk_result['risk_level']}")

    logger.info("Testing assess_business_plausibility()...")
    plausibility_result = client.assess_business_plausibility(
        {"business_description": "Tech startup", "sector": "Software", "transaction_volume": "High"},
        {"sector_benchmarks": "typical values"}
    )
    logger.info(f"Business plausibility: {plausibility_result['business_story_alignment']}, Confidence: {plausibility_result['confidence']}")

    logger.info("Testing analyze_corporate_structure()...")
    structure_result = client.analyze_corporate_structure(
        [{"name": "John Smith"}],
        [{"name": "Jane Doe", "ownership_pct": 100}],
        "Mauritius"
    )
    logger.info(f"Structure type: {structure_result['structure_type']}, UBO identified: {structure_result['ubo_identified']}")

    logger.info("Testing interpret_fincrime_screening()...")
    fincrime_result = client.interpret_fincrime_screening(
        {"hits": []},
        "Jane Doe",
        "individual"
    )
    logger.info(f"FinCrime screening status: {fincrime_result['consolidation_status']}, Recommendation: {fincrime_result['recommendation']}")

    logger.info("Testing generate_compliance_memo()...")
    memo_result = client.generate_compliance_memo(
        test_data,
        {
            "risk_score": risk_result,
            "structure": structure_result,
            "business_plausibility": plausibility_result,
            "fincrime": fincrime_result
        }
    )
    logger.info(f"Memo generated, Recommendation: {memo_result['approval_recommendation']}")

    logger.info("\nUsage statistics:")
    logger.info(client.get_usage_stats())
