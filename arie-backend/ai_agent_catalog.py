"""
Canonical AI agent catalog aligned to the controlled AI register.

This module is the code-local source of truth for agent numbering, names,
stages, and implementation truthfulness labels.
"""

from __future__ import annotations

AI_AGENT_CATALOG = [
    {
        "id": 1,
        "name": "Identity & Document Integrity Agent",
        "stage": "Onboarding",
        "implementation_mode": "live",
        "authority": "authoritative",
        "supervisor_type": "identity_document_integrity",
    },
    {
        "id": 2,
        "name": "External Database Cross-Verification Agent",
        "stage": "Onboarding",
        "implementation_mode": "deterministic",
        "authority": "decision_support",
        "supervisor_type": "external_database_verification",
        "notes": "Rule-based registry verification with provider abstraction. Runs in degraded mode (internal data checks) when no external API credentials are configured.",
    },
    {
        "id": 3,
        "name": "FinCrime Screening Interpretation Agent",
        "stage": "Onboarding",
        "implementation_mode": "hybrid",
        "authority": "decision_support",
        "supervisor_type": "fincrime_screening",
        "notes": "Policy-bounded screening interpreter. 4 rule (retrieval, disambiguation), 4 hybrid (FP reduction, severity, disposition), 3 AI (media assessment, narrative). Reads stored prescreening_data; degraded mode when no screening report.",
    },
    {
        "id": 4,
        "name": "Corporate Structure & UBO Mapping Agent",
        "stage": "Onboarding",
        "implementation_mode": "deterministic",
        "authority": "decision_support",
        "supervisor_type": "corporate_structure_ubo",
        "notes": "Rule-based ownership mapping with indirect path tracking, circular ownership detection, nominee/trust/holding detection, and complexity scoring. No AI calls.",
    },
    {
        "id": 5,
        "name": "Compliance Memo & Risk Recommendation Agent",
        "stage": "Onboarding",
        "implementation_mode": "deterministic",
        "authority": "authoritative",
        "supervisor_type": "compliance_memo_risk",
        "notes": "Unified executor bridges to authoritative memo path (memo_handler.py). Enforces Rules 4A-4E, computes 7 risk dimensions, generates 11-section memo. Classification-tagged output (rule/hybrid/ai). Risk-model divergence cross-check between D1-D5 and memo aggregated risk.",
    },
    {
        "id": 6,
        "name": "Periodic Review Preparation Agent",
        "stage": "Monitoring",
        "implementation_mode": "synthetic",
        "authority": "decision_support",
        "supervisor_type": "periodic_review_preparation",
    },
    {
        "id": 7,
        "name": "Adverse Media & PEP Monitoring Agent",
        "stage": "Monitoring",
        "implementation_mode": "synthetic",
        "authority": "decision_support",
        "supervisor_type": "adverse_media_pep_monitoring",
    },
    {
        "id": 8,
        "name": "Behaviour & Risk Drift Agent",
        "stage": "Monitoring",
        "implementation_mode": "synthetic",
        "authority": "decision_support",
        "supervisor_type": "behaviour_risk_drift",
    },
    {
        "id": 9,
        "name": "Regulatory Impact Agent",
        "stage": "Monitoring",
        "implementation_mode": "future_phase",
        "authority": "decision_support",
        "supervisor_type": "regulatory_impact",
    },
    {
        "id": 10,
        "name": "Ongoing Compliance Review Agent",
        "stage": "Monitoring",
        "implementation_mode": "synthetic",
        "authority": "decision_support",
        "supervisor_type": "ongoing_compliance_review",
    },
]

AI_AGENT_BY_ID = {agent["id"]: agent for agent in AI_AGENT_CATALOG}
