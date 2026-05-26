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
        "implementation_mode": "deterministic",
        "authority": "decision_support",
        "supervisor_type": "periodic_review_preparation",
        "notes": "Rule-based review preparation with hybrid priority scoring. 10 checks (8 rule + 2 hybrid). Scans document expiry, ownership changes, screening staleness, outstanding alerts; assembles review package with priority score. Degraded mode when no prior review history exists.",
    },
    {
        "id": 7,
        "name": "Adverse Media & PEP Monitoring Agent",
        "stage": "Monitoring",
        "implementation_mode": "hybrid",
        "authority": "decision_support",
        "supervisor_type": "adverse_media_pep_monitoring",
        "notes": "Monitoring interpreter with AI narrative. 12 checks (6 rule + 4 hybrid + 2 AI). Retrieves new media/PEP/sanctions signals, deduplicates, scores severity, resolves entities; AI generates narrative summary and disposition. Degraded mode when no screening baseline exists.",
    },
    {
        "id": 8,
        "name": "Behaviour & Risk Drift Agent",
        "stage": "Monitoring",
        "implementation_mode": "deterministic",
        "authority": "decision_support",
        "supervisor_type": "behaviour_risk_drift",
        "notes": "Rule-based drift detection with hybrid scoring. 11 checks (6 rule + 5 hybrid). Compares transaction volume, geographic activity, counterparty concentration, product usage against onboarding baseline; scores velocity anomalies and peer deviation. Degraded mode when no transaction data available.",
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
        "implementation_mode": "hybrid",
        "authority": "decision_support",
        "supervisor_type": "ongoing_compliance_review",
        "notes": "Consolidation agent with AI narrative. 11 checks (7 rule + 2 hybrid + 2 AI). Verifies document currency, screening recency, policy applicability, condition compliance, filing deadlines; consolidates inter-agent findings; AI generates compliance narrative and escalation/closure recommendation. Degraded mode when upstream agents have not run.",
    },
]

AI_AGENT_BY_ID = {agent["id"]: agent for agent in AI_AGENT_CATALOG}
