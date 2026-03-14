"""
ARIE Finance — AI Agent Supervisor: Confidence Control Framework
}================================================================
Implements confidence-based routing logic:
  - confidence > 0.85        → normal workflow
  - confidence 0.65 – 0.85   → human review required
  - confidence < 0.65         → mandatory escalation

Also calculates:
  - Case-level aggregate confidence (weighted by agent importance)
  - Confidence by agent type
  - Rolling average confidence over time

Aggregate confidence formula:
  weighted_sum(agent_confidence * agent_weight) / sum(weights)
  Adjusted downward by:
    - Number of contradictions (each critical: -0.05, high: -0.03, medium: -0.01)
    - Number of failed agents (each: -0.08)
    - Number of rules triggered (each blocking: -0.10, each non-blocking: -0.02)
  Final score clamped to [0.0, 1.0]
"""
