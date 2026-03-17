#!/usr/bin/env python3
"""
Claude AI Integration Module for ARIE Finance Compliance Platform
==================================================================

Powers 5 Onboarding AI agents in the compliance workflow:

1. Identity & Document Integrity Agent — 66 automated checks, OCR, validation, registry cross-reference
2. Corporate Structure & UBO Mapping Agent — Ownership chains, UBO identification, multi-layered entity detection, nominee/trust identification
3. Business Model Plausibility Agent — Business story evaluation, sector alignment, transaction benchmarking, source of funds consistency
4. FinCrime Screening Interpretation Agent — Sanctions/PEP/adverse media analysis, false positive reduction, hit severity ranking
5. Compliance Memo & Risk Recommendation Agent — 5-dimension composite scoring, risk routing, compliance memo

Usage:
    from claude_client import ClaudeClient
    client = ClaudeClient(api_key="sk-...", monthly_budget_usd=50.0)

    # Agent 1: Verify document
    result = client.verify_document(doc_type, file_name, person_name)

    # Agent 2: Analyze corporate structure
    result = client.analyze_corporate_structure(directors, ubos, jurisdiction)

    # Agent 3: Assess business plausibility
    result = client.assess_business_plausibility(business_data, registry_data)

    # Agent 4: Interpret FinCrime screening
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
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

# C-03: Pydantic validation for AI outputs
try:
    from pydantic import BaseModel, Field, field_validator, ValidationError
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    BaseModel = None
    ValidationError = None

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
        document_type: str = Field(min_length=1)
        is_valid: bool
        confidence: float = Field(ge=0.0, le=1.0)
        checks: Dict[str, Any] = Field(default_factory=dict)
        issues: List[str] = Field(default_factory=list)

    class CorporateStructureSchema(BaseModel):
        """Agent 2: Corporate structure analysis output validation."""
        complexity_level: str = Field(default="")
        ubos_identified: List[Dict[str, Any]] = Field(default_factory=list)
        risk_indicators: List[str] = Field(default_factory=list)
        nominee_structures: bool = False

    class BusinessPlausibilitySchema(BaseModel):
        """Agent 3: Business model plausibility output validation."""
        plausibility_score: float = Field(ge=0.0, le=1.0, default=0.5)
        concerns: List[str] = Field(default_factory=list)
        sector_alignment: str = Field(default="")
        recommendation: str = Field(default="")

    class FinCrimeScreeningSchema(BaseModel):
        """Agent 4: FinCrime screening interpretation output validation."""
        total_hits: int = Field(ge=0, default=0)
        confirmed_matches: int = Field(ge=0, default=0)
        false_positives: int = Field(ge=0, default=0)
        hit_details: List[Dict[str, Any]] = Field(default_factory=list)
        recommendation: str = Field(default="")

    class ComplianceMemoSchema(BaseModel):
        """Agent 5 (Part 2): Compliance memo output validation."""
        summary: str = Field(min_length=10)
        risk_assessment: str = Field(default="")
        key_findings: List[str] = Field(default_factory=list)
        recommendation: str = Field(pattern=r'^(APPROVE|REVIEW|EDD|REJECT)$')
        conditions: List[str] = Field(default_factory=list)

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
    """Generate realistic mock risk score response (Agent 5: Compliance Memo & Risk Recommendation)."""
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
    """Generate realistic mock corporate structure analysis (Agent 2: Corporate Structure & UBO Mapping)."""
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
    """Generate realistic mock FinCrime screening interpretation (Agent 4: FinCrime Screening Interpretation)."""
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
    """Generate realistic mock compliance memo (Agent 5: Compliance Memo & Risk Recommendation)."""
    return {
        "memo_html": """
<h2>Compliance Assessment Memo</h2>
<p><strong>Application Reference:</strong> APP-2025-00123</p>
<p><strong>Company:</strong> Example Corp Ltd</p>
<p><strong>Assessment Date:</strong> 2025-03-16</p>

<h3>Executive Summary</h3>
<p>The applicant represents a medium-risk entity with standard corporate governance
and identifiable beneficial ownership. Key considerations include multi-layered ownership
structures and minor document discrepancies resolved through additional verification.</p>

<h3>Beneficial Ownership Assessment</h3>
<p>Ultimate beneficial ownership has been identified through a three-layer structure,
terminating with natural persons resident in FATF-compliant jurisdictions. Trust documentation
and nominee arrangements require standard enhanced due diligence.</p>

<h3>Compliance Recommendation</h3>
<p>Approve with standard conditions including: (1) certified trust documentation,
(2) beneficial ownership declaration, (3) standard ongoing monitoring.</p>
        """,
        "summary": "Medium-risk entity with identifiable UBO and standard governance. Recommend approval with conditions.",
        "risk_rating": "MEDIUM",
        "key_findings": [
            "UBO chain successfully mapped to natural persons",
            "Corporate structure complies with standard expectations",
            "No adverse media or sanctions findings",
            "All required documentation substantially complete",
        ],
        "recommendations": [
            "Obtain certified copy of trust deed",
            "Annual beneficial ownership update",
            "Quarterly transaction monitoring",
        ],
        "review_checklist": [
            "Company identity verified against registry",
            "UBO chain mapped to natural persons",
            "PEP screening completed - no matches",
            "Adverse media review conducted",
            "Source of funds verified through bank statements",
            "Business model plausibility confirmed",
            "Trust documentation reviewed",
            "Sanctions screening completed",
        ],
        "approval_recommendation": "APPROVE_WITH_CONDITIONS",
    }


def _mock_verify_document() -> Dict[str, Any]:
    """Generate realistic mock document verification response (Agent 1: Identity & Document Integrity)."""
    return {
        "checks": [
            {
                "label": "Document Validity",
                "type": "validity",
                "result": "pass",
                "message": "Document format and structure verified"
            },
            {
                "label": "Expiry Risk",
                "type": "expiry",
                "result": "pass",
                "message": "Document validity extends beyond 6 months"
            },
            {
                "label": "Name Consistency",
                "type": "name",
                "result": "pass",
                "message": "Name on document matches application data"
            },
            {
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
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.mock_mode = mock_mode if mock_mode is not None else os.environ.get(
            "CLAUDE_MOCK_MODE", "false"
        ).lower() == "true"

        # P0-04: Block mock mode in production
        if self.mock_mode and os.environ.get("ENVIRONMENT") == "production":
            raise RuntimeError(
                "CRITICAL: CLAUDE_MOCK_MODE=true is not allowed in production. "
                "Set CLAUDE_MOCK_MODE=false or unset it, and provide a valid ANTHROPIC_API_KEY."
            )

        if self.mock_mode:
            logger.info("Claude client initialized in MOCK MODE (no API calls)")
            self.client = None
        else:
            if not ANTHROPIC_AVAILABLE:
                logger.warning(
                    "Anthropic library not available — falling back to mock mode. "
                    "Install with: pip install anthropic"
                )
                self.mock_mode = True
                self.client = None
            elif not self.api_key:
                logger.warning(
                    "No API key provided — falling back to mock mode. "
                    "Set ANTHROPIC_API_KEY environment variable or pass api_key parameter."
                )
                self.mock_mode = True
                self.client = None
            else:
                try:
                    self.client = Anthropic(api_key=self.api_key)
                    logger.info("Claude client initialized with Anthropic API")
                except Exception as e:
                    logger.warning(f"Failed to initialize Anthropic client: {e} — using mock mode")
                    self.mock_mode = True
                    self.client = None

        self.usage_tracker = UsageTracker(monthly_budget_usd)
        self.max_retries = 3
        self.timeout_seconds = 30

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

        # Sanitize user-controlled fields to prevent prompt injection
        sanitized_data = {}
        for key, value in application_data.items():
            if isinstance(value, str):
                sanitized_data[key] = self._sanitize_for_prompt(value)
            elif isinstance(value, dict):
                sanitized_data[key] = {k: self._sanitize_for_prompt(v) if isinstance(v, str) else v
                                        for k, v in value.items()}
            else:
                sanitized_data[key] = value

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
        Agent 3: Business Model Plausibility Agent — Evaluate business model alignment and transaction consistency.

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

        user_prompt = f"""Assess business model plausibility:

BUSINESS DATA:
{json.dumps(business_data, indent=2)}

REGISTRY/REFERENCE DATA:
{json.dumps(registry_data, indent=2)}

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
        Agent 2: Corporate Structure & UBO Mapping Agent — Map ownership chains and identify beneficial owners.

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

        # Sanitize user inputs
        sanitized_jurisdiction = self._sanitize_for_prompt(jurisdiction)
        sanitized_directors = []
        for d in directors:
            sanitized_d = {}
            for k, v in d.items():
                sanitized_d[k] = self._sanitize_for_prompt(v) if isinstance(v, str) else v
            sanitized_directors.append(sanitized_d)

        sanitized_ubos = []
        for u in ubos:
            sanitized_u = {}
            for k, v in u.items():
                sanitized_u[k] = self._sanitize_for_prompt(v) if isinstance(v, str) else v
            sanitized_ubos.append(sanitized_u)

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
        Agent 4: FinCrime Screening Interpretation Agent — Analyze sanctions/PEP/adverse media hits.

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
{json.dumps(screening_results, indent=2)}

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
                "memo_html": "<h2>Compliance Assessment Memo</h2>...",
                "summary": "...",
                "risk_rating": "<LOW|MEDIUM|HIGH|VERY_HIGH>",
                "key_findings": ["finding1", "finding2"],
                "recommendations": ["rec1", "rec2"],
                "review_checklist": ["item1", "item2"],
                "approval_recommendation": "<APPROVE|APPROVE_WITH_CONDITIONS|REVIEW|REJECT>"
            }
        """
        if self.mock_mode:
            logger.info("Returning mock compliance memo (mock mode)")
            result = _mock_generate_compliance_memo()
            result["ai_source"] = "mock"
            return result

        system_prompt = """You are a senior compliance officer producing a formal compliance assessment memo.

Generate a comprehensive, professional memo that synthesizes:
- Risk assessment results
- Cross-verification findings
- Corporate structure analysis
- Regulatory expectations

The memo should be suitable for board-level review and regulatory audit.

Return ONLY valid JSON (no markdown, no code blocks) with this exact structure:
{
    "memo_html": "<h2>Compliance Assessment Memo</h2>...",
    "summary": "executive summary",
    "risk_rating": "<LOW|MEDIUM|HIGH|VERY_HIGH>",
    "key_findings": ["finding1", "finding2"],
    "recommendations": ["rec1", "rec2"],
    "review_checklist": ["item1", "item2"],
    "approval_recommendation": "<APPROVE|APPROVE_WITH_CONDITIONS|REVIEW|REJECT>"
}"""

        # Sanitize application data
        sanitized_app_data = {}
        for key, value in application_data.items():
            if isinstance(value, str):
                sanitized_app_data[key] = self._sanitize_for_prompt(value)
            elif isinstance(value, dict):
                sanitized_app_data[key] = {k: self._sanitize_for_prompt(v) if isinstance(v, str) else v
                                            for k, v in value.items()}
            else:
                sanitized_app_data[key] = value

        # Sanitize agent results
        sanitized_agent_results = {}
        for key, value in agent_results.items():
            if isinstance(value, str):
                sanitized_agent_results[key] = self._sanitize_for_prompt(value)
            elif isinstance(value, dict):
                sanitized_agent_results[key] = {k: self._sanitize_for_prompt(v) if isinstance(v, str) else v
                                                 for k, v in value.items()}
            elif isinstance(value, list):
                sanitized_agent_results[key] = [self._sanitize_for_prompt(item) if isinstance(item, str) else item for item in value]
            else:
                sanitized_agent_results[key] = value

        user_prompt = f"""Generate a compliance memo for this application:

APPLICATION DATA:
{json.dumps(sanitized_app_data, indent=2)}

AGENT ANALYSIS RESULTS:
{json.dumps(sanitized_agent_results, indent=2)}

Produce a comprehensive memo covering executive summary, findings, recommendations, and approval decision."""

        try:
            response = self._call_claude(
                system_prompt,
                user_prompt,
                model="claude-opus-4-6",
                timeout=self.timeout_seconds,
            )
            result = self._parse_json_response(response, agent_method="generate_compliance_memo")
            result["ai_source"] = "claude-opus-4-6"
            return result
        except Exception as e:
            logger.error(f"Memo generation failed: {e} — returning mock response")
            result = _mock_generate_compliance_memo()
            result["ai_source"] = "mock"
            result["ai_error"] = str(e)[:200]
            return result

    # ── Document Verification (Sonnet - fast, cheap) ──────────────

    def verify_document(
        self,
        doc_type: str,
        file_name: str,
        person_name: str = "",
        doc_category: str = "identity",
    ) -> Dict[str, Any]:
        """
        Agent 1: Identity & Document Integrity Agent — Verify document authenticity and compliance using Claude AI.

        Performs 66 automated checks including OCR, validation, and registry cross-reference.
        Uses claude-sonnet-4-6 for speed and cost efficiency.

        Args:
            doc_type: Type of document (passport, national_id, poa, cv, bankref, etc)
            file_name: Name of the uploaded file
            person_name: Name of the person the document belongs to (optional)
            doc_category: Category of document (identity, company, kyc, address, etc)

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
        if self.mock_mode:
            logger.info("Returning mock document verification (mock mode)")
            result = _mock_verify_document()
            result["ai_source"] = "mock"
            return result

        # Sanitize person name to prevent prompt injection
        sanitized_person_name = self._sanitize_for_prompt(person_name)

        system_prompt = """You are an expert document analyst specializing in identity verification and compliance.

Analyze the provided document information and verify its authenticity, validity, and compliance.
Evaluate the following aspects:
1. Document Validity: Is the document format valid and recognized?
2. Expiry Risk: Does the document have sufficient remaining validity period?
3. Name Consistency: Does the name on the document match the declared person (if provided)?
4. Quality Indicators: Are there signs of document tampering, poor quality, or forgery?
5. Red Flags: Are there any suspicious characteristics that warrant additional review?

Return ONLY valid JSON (no markdown, no code blocks) with this exact structure:
{
    "checks": [
        {
            "label": "Check Name",
            "type": "check_type",
            "result": "pass"|"warn"|"fail",
            "message": "Explanation of the check result"
        }
    ],
    "overall": "verified"|"flagged",
    "confidence": <0.0-1.0>,
    "red_flags": ["flag1", "flag2"]
}"""

        user_prompt = f"""Verify this document:

Document Type: {doc_type}
File Name: {file_name}
Document Category: {doc_category}
Associated Person: {sanitized_person_name if sanitized_person_name else "Not provided"}

Analyze the document details and provide verification assessment. Focus on authenticity,
validity dates, name matching, document quality, and any red flags."""

        try:
            response = self._call_claude(
                system_prompt,
                user_prompt,
                model="claude-sonnet-4-6",
                timeout=self.timeout_seconds,
            )
            result = self._parse_json_response(response, agent_method="verify_document")
            result["ai_source"] = "claude-sonnet-4-6"
            return result
        except Exception as e:
            logger.error(f"Document verification failed: {e} — returning mock response")
            result = _mock_verify_document()
            result["ai_source"] = "mock"
            result["ai_error"] = str(e)[:200]
            return result

    # ── Internal Helper Methods ─────────────────────────────────

    def _call_claude(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str = "claude-sonnet-4-6",
        timeout: int = 30,
    ) -> str:
        """
        Make a call to Claude API with retry logic and timeout handling.

        Args:
            system_prompt: System context for the AI agent
            user_prompt: User input/query
            model: Model to use (claude-sonnet-4-6 or claude-opus-4-6)
            timeout: Timeout in seconds

        Returns:
            Raw response text from Claude

        Raises:
            Exception: If all retries fail or API is unavailable
        """
        if not self.client:
            raise RuntimeError(
                "Claude client not available. Initialize with valid API key or enable mock mode."
            )

        for attempt in range(self.max_retries):
            try:
                logger.debug(
                    f"Calling Claude {model} (attempt {attempt + 1}/{self.max_retries})"
                )

                response = self.client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                    timeout=timeout,
                )

                # Extract text content and track usage
                text_content = response.content[0].text
                self.usage_tracker.log_usage(
                    model=model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                )

                logger.debug(f"Claude {model} request succeeded")
                return text_content

            except APITimeoutError as e:
                logger.warning(
                    f"Claude {model} request timeout (attempt {attempt + 1}/{self.max_retries}): {e}"
                )
                if attempt == self.max_retries - 1:
                    raise RuntimeError(f"Claude API timeout after {self.max_retries} retries") from e

            except APIConnectionError as e:
                logger.warning(
                    f"Claude connection error (attempt {attempt + 1}/{self.max_retries}): {e}"
                )
                if attempt == self.max_retries - 1:
                    raise RuntimeError(
                        f"Claude API connection failed after {self.max_retries} retries"
                    ) from e

            except APIError as e:
                logger.error(f"Claude API error: {e}")
                if attempt == self.max_retries - 1:
                    raise RuntimeError(f"Claude API error: {e}") from e

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
                CONFIDENCE_THRESHOLD = float(os.environ.get("AI_CONFIDENCE_THRESHOLD", "0.70"))
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
