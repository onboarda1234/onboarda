"""
ARIE Finance — AI Agent Supervisor: Pydantic Models & JSON Schemas
===================================================================
Strict schema definitions for all 10 agent outputs, validation results,
contradictions, rules, escalations, human reviews, and audit entries.

Every agent MUST return output conforming to these schemas.
The supervisor rejects/quarantines any non-conforming output.

Design principles:
  - All fields explicitly typed
  - Required fields enforced
  - Confidence scores bounded [0.0, 1.0]
  - Evidence required for non-trivial findings
  - Timestamps in ISO 8601
  - Enums for all categorical fields
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


# ═══════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════

class AgentType(str, Enum):
    IDENTITY_DOCUMENT_INTEGRITY = "identity_document_integrity"
    EXTERNAL_DATABASE_VERIFICATION = "external_database_verification"
    CORPORATE_STRUCTURE_UBO = "corporate_structure_ubo"
    FINCRIME_SCREENING = "fincrime_screening"
    COMPLIANCE_MEMO_RISK = "compliance_memo_risk"
    PERIODIC_REVIEW_PREPARATION = "periodic_review_preparation"
    ADVERSE_MEDIA_PEP_MONITORING = "adverse_media_pep_monitoring"
    BEHAVIOUR_RISK_DRIFT = "behaviour_risk_drift"
    REGULATORY_IMPACT = "regulatory_impact"
    ONGOING_COMPLIANCE_REVIEW = "ongoing_compliance_review"


class AgentStatus(str, Enum):
    CLEAN = "clean"
    ISSUES_FOUND = "issues_found"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"
    PARTIAL = "partial"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    QUARANTINED = "quarantined"
    SKIPPED = "skipped"


class TriggerType(str, Enum):
    ONBOARDING = "onboarding"
    PERIODIC_REVIEW = "periodic_review"
    MONITORING_ALERT = "monitoring_alert"
    MANUAL_TRIGGER = "manual_trigger"
    RERUN = "rerun"
    QA_TEST = "qa_test"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ConfidenceRouting(str, Enum):
    NORMAL = "normal"                       # > 0.85
    HUMAN_REVIEW = "human_review"           # 0.65 - 0.85
    MANDATORY_ESCALATION = "mandatory_escalation"  # < 0.65


class EscalationLevel(str, Enum):
    COMPLIANCE_OFFICER = "compliance_officer"
    SENIOR_COMPLIANCE = "senior_compliance"
    MLRO = "mlro"
    MANAGEMENT = "management"


class ReviewDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_INFORMATION = "request_information"
    ESCALATE = "escalate"
    ENHANCED_MONITORING = "enhanced_monitoring"
    EXIT_RELATIONSHIP = "exit_relationship"
    DEFER = "defer"


class RuleAction(str, Enum):
    ESCALATE = "escalate"
    BLOCK_APPROVAL = "block_approval"
    REQUIRE_REVIEW = "require_review"
    FLAG_WARNING = "flag_warning"
    REJECT = "reject"
    HOLD = "hold"
    NO_ACTION = "no_action"


class ContradictionCategory(str, Enum):
    IDENTITY_VS_REGISTRY = "identity_vs_registry"
    UBO_VS_RISK = "ubo_vs_risk"
    SCREENING_VS_PLAUSIBILITY = "screening_vs_plausibility"
    REGISTRY_VS_MEMO = "registry_vs_memo"
    DOCUMENT_VS_IDENTITY = "document_vs_identity"
    MONITORING_VS_ONBOARDING = "monitoring_vs_onboarding"
    RISK_LEVEL_MISMATCH = "risk_level_mismatch"
    TEMPORAL_INCONSISTENCY = "temporal_inconsistency"
    DATA_COMPLETENESS_CONFLICT = "data_completeness_conflict"
    OTHER = "other"


class OverrideType(str, Enum):
    RISK_LEVEL_CHANGE = "risk_level_change"
    APPROVAL_DESPITE_ESCALATION = "approval_despite_escalation"
    REJECTION_DESPITE_APPROVAL = "rejection_despite_approval"
    CONFIDENCE_OVERRIDE = "confidence_override"
    RULE_EXCEPTION = "rule_exception"
    CONTRADICTION_DISMISSAL = "contradiction_dismissal"


class AuditEventType(str, Enum):
    AGENT_RUN_STARTED = "agent_run_started"
    AGENT_RUN_COMPLETED = "agent_run_completed"
    AGENT_RUN_FAILED = "agent_run_failed"
    SCHEMA_VALIDATION_PASSED = "schema_validation_passed"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
    CONFIDENCE_CALCULATED = "confidence_calculated"
    CONFIDENCE_ROUTING = "confidence_routing"
    CONTRADICTION_DETECTED = "contradiction_detected"
    CONTRADICTION_RESOLVED = "contradiction_resolved"
    RULE_TRIGGERED = "rule_triggered"
    RULE_OVERRIDDEN = "rule_overridden"
    ESCALATION_CREATED = "escalation_created"
    ESCALATION_ASSIGNED = "escalation_assigned"
    ESCALATION_RESOLVED = "escalation_resolved"
    HUMAN_REVIEW_STARTED = "human_review_started"
    HUMAN_REVIEW_COMPLETED = "human_review_completed"
    AI_OVERRIDE = "ai_override"
    PIPELINE_STARTED = "pipeline_started"
    PIPELINE_COMPLETED = "pipeline_completed"
    PIPELINE_FAILED = "pipeline_failed"
    CONFIG_CHANGED = "config_changed"
    AGENT_VERSION_CHANGED = "agent_version_changed"
    PROMPT_VERSION_CHANGED = "prompt_version_changed"
    SYSTEM_ERROR = "system_error"


# ═══════════════════════════════════════════════════════════
# CORE OUTPUT MODELS (Required from every agent)
# ═══════════════════════════════════════════════════════════

class Finding(BaseModel):
    """Individual finding from an agent."""
    finding_id: str = Field(default_factory=lambda: str(uuid4())[:12])
    category: str = Field(..., description="Finding category/type code")
    title: str = Field(..., min_length=1, description="Short finding title")
    description: str = Field(..., min_length=1, description="Detailed finding description")
    severity: Severity = Field(default=Severity.INFO)
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in this finding")
    source: str = Field(..., description="Data source for this finding")
    evidence_refs: List[str] = Field(default_factory=list, description="References to evidence items")
    regulatory_relevance: Optional[str] = Field(None, description="Why this matters for compliance")


class Evidence(BaseModel):
    """Supporting evidence for findings."""
    evidence_id: str = Field(default_factory=lambda: str(uuid4())[:12])
    evidence_type: str = Field(..., description="Type: document, registry_record, screening_result, etc.")
    source: str = Field(..., description="Source system or document")
    content_summary: str = Field(..., description="Brief summary of evidence content")
    reference: str = Field(..., description="Document ID, URL, or record reference")
    verified: bool = Field(default=False, description="Whether evidence has been independently verified")
    timestamp: Optional[str] = Field(None, description="When evidence was obtained")


class DetectedIssue(BaseModel):
    """Issue detected by the agent."""
    issue_id: str = Field(default_factory=lambda: str(uuid4())[:12])
    issue_type: str = Field(..., description="Issue type code")
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    severity: Severity
    blocking: bool = Field(default=False, description="Whether this blocks case progression")
    remediation: Optional[str] = Field(None, description="Suggested remediation action")
    related_findings: List[str] = Field(default_factory=list)


class RiskIndicator(BaseModel):
    """Risk indicator flag."""
    indicator_type: str = Field(..., description="Risk indicator code")
    description: str
    risk_level: str = Field(..., description="low / medium / high / critical")
    source_agent: str
    contributing_factors: List[str] = Field(default_factory=list)


class AgentOutputBase(BaseModel):
    """
    BASE OUTPUT SCHEMA — Every agent MUST return this structure.
    This is the contract between agents and the supervisor.
    """
    # Identity & versioning
    agent_name: str = Field(..., min_length=1, description="Agent name")
    agent_type: AgentType
    agent_version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$", description="Semantic version")
    prompt_version: str = Field(..., min_length=1, description="Prompt version identifier")
    model_name: str = Field(..., min_length=1, description="LLM model used")
    run_id: str = Field(..., min_length=1, description="Unique run identifier")
    application_id: str = Field(..., min_length=1, description="Application being analyzed")

    # Status & confidence
    status: AgentStatus
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Overall confidence")

    # Results
    findings: List[Finding] = Field(..., description="Agent findings")
    evidence: List[Evidence] = Field(..., description="Supporting evidence")
    detected_issues: List[DetectedIssue] = Field(default_factory=list)
    risk_indicators: List[RiskIndicator] = Field(default_factory=list)

    # Recommendation
    recommendation: Optional[str] = Field(None, description="Agent recommendation text")

    # Escalation
    escalation_flag: bool = Field(default=False)
    escalation_reason: Optional[str] = Field(None)

    # Metadata
    processed_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z",
        description="ISO 8601 timestamp"
    )
    processing_time_ms: Optional[int] = Field(None, ge=0)
    token_count: Optional[int] = Field(None, ge=0)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_escalation_consistency(self):
        """Escalation flag requires a reason."""
        if self.escalation_flag and not self.escalation_reason:
            raise ValueError("escalation_reason is required when escalation_flag is True")
        return self

    @model_validator(mode="after")
    def validate_evidence_for_findings(self):
        """Non-clean status with findings should have evidence."""
        if self.status != AgentStatus.CLEAN and len(self.findings) > 0 and len(self.evidence) == 0:
            raise ValueError(
                "Evidence is required when findings are present "
                "and status is not 'clean'"
            )
        return self


# ═══════════════════════════════════════════════════════════
# AGENT-SPECIFIC OUTPUT MODELS
# ═══════════════════════════════════════════════════════════

class IdentityDocumentOutput(AgentOutputBase):
    """Agent 1: Identity & Document Integrity Agent

    Validates authenticity and internal consistency of identity documents.
    Performs ~36 checks: MRZ extraction/checksum, expiry validation, tampering detection,
    cross-document consistency, missing documentation, blur/cropping detection.
    Does NOT perform sanctions screening or registry lookups.
    """
    agent_type: AgentType = AgentType.IDENTITY_DOCUMENT_INTEGRITY

    # Passport verification
    passport_type: Optional[str] = Field(None, description="Detected passport type")
    mrz_extracted: Optional[Dict[str, Any]] = Field(None, description="MRZ data extracted from passport")
    mrz_checksum_valid: Optional[bool] = Field(None, description="Whether MRZ checksum verified")
    name_match_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="Name match vs application data")
    dob_consistency: Optional[bool] = Field(None, description="Date of birth matches across documents")

    # Expiry verification
    expiry_date: Optional[str] = Field(None, description="Document expiry date")
    expiry_status: Optional[str] = Field(None, description="VALID / EXPIRED / NEAR_EXPIRY")
    issue_date_valid: Optional[bool] = Field(None, description="Issue date vs expiry rules valid")

    # Authenticity checks
    tampering_risk: Optional[str] = Field(None, description="LOW / MEDIUM / HIGH")
    image_manipulation_detected: bool = Field(default=False)
    watermark_verified: Optional[bool] = None
    cropped_or_incomplete: bool = Field(default=False)
    blur_detected: bool = Field(default=False)
    document_authenticity_score: Optional[float] = Field(None, ge=0.0, le=1.0)

    # Cross-document consistency
    passport_name_vs_application: Optional[Dict[str, Any]] = Field(None, description="Name cross-check result")
    address_vs_proof_of_address: Optional[Dict[str, Any]] = Field(None, description="Address cross-check")
    director_name_vs_registry: Optional[Dict[str, Any]] = Field(None, description="Director name cross-check")

    # Completeness
    documents_verified: List[Dict[str, Any]] = Field(default_factory=list, description="Per-document verification results")
    missing_documents: List[str] = Field(default_factory=list, description="Missing required documents")
    document_completeness: Optional[str] = Field(None, description="PASS / FAIL / PARTIAL")
    tampering_indicators: List[str] = Field(default_factory=list)

    # Corporate document checks
    certificate_of_incorporation_valid: Optional[bool] = None
    shareholder_register_complete: Optional[bool] = None
    director_list_consistent: Optional[bool] = None


class ExternalDatabaseOutput(AgentOutputBase):
    """Agent 2 (sub-component): External Database Cross-Verification

    Part of Agent 2 (Corporate Structure & UBO Mapping Agent) per the official
    AI Agent Registry. This sub-component handles registry cross-referencing:
    checks passport/company document info against external databases
    (OpenCorporates, Companies House, ADGM, DIFC).
    Confirms persons exist in registry records as declared directors/shareholders.
    """
    agent_type: AgentType = AgentType.EXTERNAL_DATABASE_VERIFICATION

    company_found: Optional[bool] = None
    registry_source: Optional[str] = None
    registered_name: Optional[str] = None
    registration_number_match: Optional[bool] = None
    directors_match: Optional[Dict[str, Any]] = None
    registered_address_match: Optional[bool] = None
    company_status: Optional[str] = None
    incorporation_date: Optional[str] = None
    last_filing_date: Optional[str] = None
    discrepancies: List[Dict[str, Any]] = Field(default_factory=list)


class CorporateStructureUBOOutput(AgentOutputBase):
    """Agent 4: Corporate Structure & UBO Mapping Agent

    Maps ownership chains, identifies UBOs, detects nominee structures,
    flags complex chains, cross-references UBOs with screening results.
    Runs AFTER Identity & Document Integrity (Agent 1) in the pipeline.
    """
    agent_type: AgentType = AgentType.CORPORATE_STRUCTURE_UBO

    ownership_structure: Optional[Dict[str, Any]] = None
    ubos_identified: List[Dict[str, Any]] = Field(default_factory=list)
    ubo_completeness: Optional[float] = Field(None, ge=0.0, le=1.0)
    complex_structure_flag: bool = False
    shell_company_indicators: List[str] = Field(default_factory=list)
    circular_ownership_detected: bool = False
    nominee_arrangements_detected: bool = False
    indirect_ownership_paths: List[Dict[str, Any]] = Field(default_factory=list)
    total_ownership_mapped_pct: Optional[float] = Field(None, ge=0.0, le=100.0)


class FinCrimeScreeningOutput(AgentOutputBase):
    """Agent 3: FinCrime Screening Interpretation Agent

    Screens individuals/entities against sanctions lists, PEP databases,
    watchlists, and adverse media. Distinguishes false positives,
    assesses match confidence, classifies political exposure.
    Runs AFTER Identity Agent (Agent 1) and can inform Agent 4 ownership review.
    """
    agent_type: AgentType = AgentType.FINCRIME_SCREENING

    sanctions_results: List[Dict[str, Any]] = Field(default_factory=list)
    pep_results: List[Dict[str, Any]] = Field(default_factory=list)
    adverse_media_results: List[Dict[str, Any]] = Field(default_factory=list)
    sanctions_match_found: bool = False
    pep_match_found: bool = False
    adverse_media_found: bool = False
    highest_match_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    screened_entities: List[str] = Field(default_factory=list)
    screening_provider: Optional[str] = None
    screening_date: Optional[str] = None
    false_positive_assessment: Optional[Dict[str, Any]] = None


class ComplianceMemoOutput(AgentOutputBase):
    """Agent 5: Compliance Memo & Risk Recommendation Agent

    Final synthesis agent. Compiles results from Agents 1-4,
    computes 5-dimension composite risk scoring, recommends risk rating,
    produces onboarding memo and review checklist for compliance officers.
    """
    agent_type: AgentType = AgentType.COMPLIANCE_MEMO_RISK

    client_overview: Optional[Dict[str, Any]] = None
    business_activity_summary: Optional[str] = None
    ownership_summary: Optional[str] = None
    ubo_identification_summary: Optional[str] = None
    screening_summary: Optional[str] = None
    risk_indicators_summary: List[Dict[str, Any]] = Field(default_factory=list)
    recommended_risk_level: Optional[str] = None
    recommended_action: Optional[str] = None
    memo_sections: List[Dict[str, Any]] = Field(default_factory=list)
    overall_risk_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    data_quality_assessment: Optional[Dict[str, Any]] = None


class PeriodicReviewOutput(AgentOutputBase):
    """Agent 6: Periodic Review Preparation Agent"""
    agent_type: AgentType = AgentType.PERIODIC_REVIEW_PREPARATION

    review_trigger: Optional[str] = None
    previous_risk_level: Optional[str] = None
    current_risk_assessment: Optional[str] = None
    changes_since_last_review: List[Dict[str, Any]] = Field(default_factory=list)
    updated_screening_results: Optional[Dict[str, Any]] = None
    transaction_analysis: Optional[Dict[str, Any]] = None
    document_currency_status: Optional[Dict[str, Any]] = None
    recommended_risk_level: Optional[str] = None
    review_memo_sections: List[Dict[str, Any]] = Field(default_factory=list)
    risk_trend: Optional[str] = Field(None, description="increasing / stable / decreasing")


class AdverseMediaPEPOutput(AgentOutputBase):
    """Agent 7: Adverse Media & PEP Monitoring Agent"""
    agent_type: AgentType = AgentType.ADVERSE_MEDIA_PEP_MONITORING

    new_media_hits: List[Dict[str, Any]] = Field(default_factory=list)
    pep_status_changes: List[Dict[str, Any]] = Field(default_factory=list)
    media_sentiment_score: Optional[float] = Field(None, ge=-1.0, le=1.0)
    sources_checked: List[str] = Field(default_factory=list)
    monitoring_period: Optional[Dict[str, Any]] = None
    alert_generated: bool = False
    alert_severity: Optional[Severity] = None
    previous_screening_comparison: Optional[Dict[str, Any]] = None


class BehaviourRiskDriftOutput(AgentOutputBase):
    """Agent 8: Behaviour & Risk Drift Agent"""
    agent_type: AgentType = AgentType.BEHAVIOUR_RISK_DRIFT

    risk_drift_detected: bool = False
    drift_direction: Optional[str] = Field(None, description="increasing / stable / decreasing")
    drift_magnitude: Optional[float] = Field(None, ge=0.0, le=1.0)
    behavioural_indicators: List[Dict[str, Any]] = Field(default_factory=list)
    transaction_anomalies: List[Dict[str, Any]] = Field(default_factory=list)
    velocity_changes: Optional[Dict[str, Any]] = None
    geographic_pattern_changes: Optional[Dict[str, Any]] = None
    peer_comparison: Optional[Dict[str, Any]] = None
    recommended_action: Optional[str] = None


class RegulatoryImpactOutput(AgentOutputBase):
    """Agent 9: Regulatory Impact Agent

    Future-phase agent reserved in the canonical model. It is not part of the
    live approval chain in the current implementation.
    """
    agent_type: AgentType = AgentType.REGULATORY_IMPACT

    impact_summary: Optional[str] = None
    affected_jurisdictions: List[str] = Field(default_factory=list)
    affected_controls: List[str] = Field(default_factory=list)
    implementation_required: bool = False
    implementation_deadline: Optional[str] = None


class OngoingComplianceOutput(AgentOutputBase):
    """Agent 10: Ongoing Compliance Review Agent"""
    agent_type: AgentType = AgentType.ONGOING_COMPLIANCE_REVIEW

    compliance_status: Optional[str] = None
    regulatory_changes_impact: List[Dict[str, Any]] = Field(default_factory=list)
    document_expiry_alerts: List[Dict[str, Any]] = Field(default_factory=list)
    outstanding_actions: List[Dict[str, Any]] = Field(default_factory=list)
    next_review_due: Optional[str] = None
    recommended_review_frequency: Optional[str] = None
    relationship_health_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    exit_indicators: List[str] = Field(default_factory=list)


# Map agent types to their specific output models
AGENT_OUTPUT_MODELS: Dict[AgentType, type] = {
    AgentType.IDENTITY_DOCUMENT_INTEGRITY: IdentityDocumentOutput,
    AgentType.EXTERNAL_DATABASE_VERIFICATION: ExternalDatabaseOutput,
    AgentType.CORPORATE_STRUCTURE_UBO: CorporateStructureUBOOutput,
    AgentType.FINCRIME_SCREENING: FinCrimeScreeningOutput,
    AgentType.COMPLIANCE_MEMO_RISK: ComplianceMemoOutput,
    AgentType.PERIODIC_REVIEW_PREPARATION: PeriodicReviewOutput,
    AgentType.ADVERSE_MEDIA_PEP_MONITORING: AdverseMediaPEPOutput,
    AgentType.BEHAVIOUR_RISK_DRIFT: BehaviourRiskDriftOutput,
    AgentType.REGULATORY_IMPACT: RegulatoryImpactOutput,
    AgentType.ONGOING_COMPLIANCE_REVIEW: OngoingComplianceOutput,
}


# ═══════════════════════════════════════════════════════════
# SUPERVISOR MODELS
# ═══════════════════════════════════════════════════════════

class ValidationError(BaseModel):
    """Individual validation error."""
    field: str
    error_type: str  # missing, type_error, constraint, format
    message: str
    severity: Severity = Severity.HIGH


class ValidationResult(BaseModel):
    """Schema validation result for an agent output."""
    validation_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    agent_type: AgentType
    application_id: str
    is_valid: bool
    errors: List[ValidationError] = Field(default_factory=list)
    missing_fields: List[str] = Field(default_factory=list)
    type_errors: List[str] = Field(default_factory=list)
    constraint_violations: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    schema_version: str = "1.0.0"
    validator_version: str = "1.0.0"
    validated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class ConfidenceScore(BaseModel):
    """Confidence score with routing decision."""
    score_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: Optional[str] = None
    pipeline_id: Optional[str] = None
    application_id: str
    agent_type: Optional[AgentType] = None
    score_type: str  # agent_output, case_aggregate, agent_rolling_avg, pipeline_aggregate
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    routing_decision: ConfidenceRouting
    component_scores: Dict[str, float] = Field(default_factory=dict)
    calculation_method: Optional[str] = None
    calculated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class Contradiction(BaseModel):
    """Cross-agent contradiction record."""
    contradiction_id: str = Field(default_factory=lambda: str(uuid4()))
    pipeline_id: str
    application_id: str
    contradiction_type: str
    contradiction_category: ContradictionCategory
    severity: Severity
    severity_score: float = Field(..., ge=0.0, le=1.0)
    agent_a_run_id: str
    agent_a_type: AgentType
    agent_a_finding: str
    agent_b_run_id: str
    agent_b_type: AgentType
    agent_b_finding: str
    description: str
    resolution_required: bool = True
    detected_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class RuleEvaluation(BaseModel):
    """Rules engine evaluation result."""
    evaluation_id: str = Field(default_factory=lambda: str(uuid4()))
    pipeline_id: str
    application_id: str
    run_id: Optional[str] = None
    rule_id: str
    rule_name: str
    rule_category: str
    triggered: bool
    trigger_data: Optional[Dict[str, Any]] = None
    action_taken: Optional[RuleAction] = None
    overrides_ai: bool = False
    ai_recommendation: Optional[str] = None
    rule_recommendation: Optional[str] = None
    severity: Optional[Severity] = None
    evaluated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class Escalation(BaseModel):
    """Escalation routing record."""
    escalation_id: str = Field(default_factory=lambda: str(uuid4()))
    pipeline_id: str
    application_id: str
    escalation_source: str
    source_id: Optional[str] = None
    escalation_level: EscalationLevel
    priority: Severity
    reason: str
    context: Dict[str, Any] = Field(default_factory=dict)
    assigned_to: Optional[str] = None
    status: str = "pending"
    sla_deadline: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class HumanReview(BaseModel):
    """Human review decision record."""
    review_id: str = Field(default_factory=lambda: str(uuid4()))
    pipeline_id: str
    application_id: str
    escalation_id: Optional[str] = None
    review_type: str
    reviewer_id: str
    reviewer_name: str
    reviewer_role: str
    ai_recommendation: Optional[str] = None
    ai_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    ai_risk_level: Optional[str] = None
    rules_recommendation: Optional[str] = None
    rules_triggered: List[str] = Field(default_factory=list)
    contradictions: List[Dict[str, Any]] = Field(default_factory=list)
    decision: ReviewDecision
    decision_reason: str = Field(..., min_length=1)
    risk_level_assigned: Optional[str] = None
    conditions: Optional[str] = None
    follow_up_required: bool = False
    follow_up_details: Optional[str] = None
    is_ai_override: bool = False
    override_reason: Optional[str] = None
    decision_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    @model_validator(mode="after")
    def validate_override_reason(self):
        if self.is_ai_override and not self.override_reason:
            raise ValueError("override_reason is required when is_ai_override is True")
        return self


class Override(BaseModel):
    """AI override record."""
    override_id: str = Field(default_factory=lambda: str(uuid4()))
    review_id: str
    application_id: str
    agent_type: Optional[AgentType] = None
    override_type: OverrideType
    original_value: str
    override_value: str
    reason: str = Field(..., min_length=1)
    officer_id: str
    officer_name: str
    officer_role: str
    approver_id: Optional[str] = None
    approver_name: Optional[str] = None
    approved_at: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class AuditEntry(BaseModel):
    """Append-only audit log entry."""
    audit_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    event_type: AuditEventType
    severity: Severity = Severity.INFO
    pipeline_id: Optional[str] = None
    application_id: Optional[str] = None
    run_id: Optional[str] = None
    agent_type: Optional[str] = None
    actor_type: str = "system"
    actor_id: Optional[str] = None
    actor_name: Optional[str] = None
    actor_role: Optional[str] = None
    action: str
    detail: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    ip_address: Optional[str] = None
    session_id: Optional[str] = None
    previous_hash: Optional[str] = None
    entry_hash: Optional[str] = None

    def compute_hash(self, previous_hash: Optional[str] = None) -> str:
        """Compute SHA-256 hash for tamper detection."""
        content = json.dumps({
            "audit_id": self.audit_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type.value,
            "action": self.action,
            "data": self.data,
            "previous_hash": previous_hash or ""
        }, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()


class CaseAggregate(BaseModel):
    """Case-level aggregate scores and status."""
    aggregate_id: str = Field(default_factory=lambda: str(uuid4()))
    pipeline_id: str
    application_id: str
    total_agents_run: int = 0
    successful_agents: int = 0
    failed_agents: int = 0
    aggregate_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    min_agent_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    max_agent_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    confidence_routing: Optional[ConfidenceRouting] = None
    total_contradictions: int = 0
    critical_contradictions: int = 0
    total_rules_triggered: int = 0
    blocking_rules_triggered: int = 0
    escalation_required: bool = False
    escalation_level: Optional[EscalationLevel] = None
    ai_recommendation: Optional[str] = None
    ai_risk_level: Optional[str] = None
    pipeline_status: str = "running"


# ═══════════════════════════════════════════════════════════
# AGENT METRICS MODEL
# ═══════════════════════════════════════════════════════════

class AgentMetrics(BaseModel):
    """Rolling quality metrics for a single agent."""
    metrics_id: str = Field(default_factory=lambda: str(uuid4()))
    agent_type: AgentType
    agent_version: str
    period_start: str
    period_end: str
    period_type: str  # hourly, daily, weekly, monthly
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    timeout_runs: int = 0
    quarantined_runs: int = 0
    validation_pass_rate: Optional[float] = None
    avg_confidence: Optional[float] = None
    min_confidence: Optional[float] = None
    max_confidence: Optional[float] = None
    stddev_confidence: Optional[float] = None
    avg_runtime_ms: Optional[float] = None
    p95_runtime_ms: Optional[float] = None
    p99_runtime_ms: Optional[float] = None
    escalation_rate: Optional[float] = None
    override_rate: Optional[float] = None
    contradiction_rate: Optional[float] = None
    false_positive_rate: Optional[float] = None
    false_negative_rate: Optional[float] = None
    avg_token_count: Optional[float] = None
    total_tokens: int = 0


# ═══════════════════════════════════════════════════════════
# AI COMPLIANCE ASSISTANT OUTPUT
# ═══════════════════════════════════════════════════════════

class ComplianceAssistantOutput(BaseModel):
    """
    Structured output from the AI Compliance Assistant.
    Used when the assistant reviews a case end-to-end.
    """
    assistant_version: str = "1.0.0"
    application_id: str
    review_type: str  # onboarding / periodic_review / monitoring_alert
    generated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    # Sections
    client_summary: Dict[str, Any] = Field(
        ..., description="Client type, jurisdiction, business activity, ownership"
    )
    key_findings: List[Dict[str, Any]] = Field(
        ..., description="Important facts discovered during analysis"
    )
    screening_summary: Dict[str, Any] = Field(
        ..., description="Sanctions, PEP, adverse media results"
    )
    risk_indicators: List[Dict[str, Any]] = Field(
        ..., description="Categorized risks: low/medium/high"
    )
    data_inconsistencies: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Mismatches or missing information"
    )
    ai_analysis: str = Field(
        ..., description="Explanation of why risk indicators matter"
    )
    recommended_action: str = Field(
        ..., description="One of: standard_dd, request_info, enhanced_dd, senior_review"
    )
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    supporting_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    caveats: List[str] = Field(
        default_factory=list,
        description="Limitations and uncertainties in the analysis"
    )
