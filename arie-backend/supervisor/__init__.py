"""
ARIE Finance — AI Agent Supervisor Framework
=============================================
Quality Control layer that sits above all 10 AI agents.
Ensures outputs are reliable, contradictions are detected,
low-confidence cases are escalated, and the entire process
is auditable for regulators (FSC Mauritius, FATF standards).

Modules:
    schemas      — Pydantic models for all agent outputs
    validator    — Schema validation + completeness checks
    supervisor   — Agent orchestrator + pipeline controller
    confidence   — Confidence scoring + routing logic
    contradictions — Cross-agent contradiction detection
    rules_engine — Hard compliance rules that override AI
    audit        — Append-only audit logging
    human_review — Officer review workflow + override tracking
    performance  — Agent quality metrics + drift detection
    api          — FastAPI/Tornado endpoints

Architecture:
    Client App → Agent Orchestrator → [Agent 1..10]
                      ↓
              Schema Validator → Confidence Evaluator
                      ↓
           Contradiction Detector → Rules Engine
                      ↓
              Human Review Router → Audit Logger
                      ↓
              Performance Tracker → Governance Dashboard
"""

__version__ = "1.0.0"
__author__ = "Onboarda Engineering"
