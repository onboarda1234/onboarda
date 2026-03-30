# AI Agent Numbering Migration

## Purpose

This note records the remediation that aligned platform agent numbering to the
controlled AI register. The canonical source of truth in code is now
`arie-backend/ai_agent_catalog.py`.

## Canonical model

The platform now uses this canonical numbered model everywhere in scoped code:

1. Identity & Document Integrity Agent
2. External Database Cross-Verification Agent
3. FinCrime Screening Interpretation Agent
4. Corporate Structure & UBO Mapping Agent
5. Compliance Memo & Risk Recommendation Agent
6. Periodic Review Preparation Agent
7. Adverse Media & PEP Monitoring Agent
8. Behaviour & Risk Drift Agent
9. Regulatory Impact Agent
10. Ongoing Compliance Review Agent

## Pre-remediation drift

Before remediation, the supervisor implementation had drifted from the register:

- Agent 2 was used for Corporate Structure & UBO Mapping
- Agent 2a was used for External Database Cross-Verification
- Agent 3 was used for Business Model Plausibility
- Agent 4 was used for FinCrime Screening Interpretation
- Agent 9 had no active supervisor executor

## Mapping from drifted implementation to canonical model

| Drifted implementation label | Canonical outcome |
| --- | --- |
| Agent 1: Identity & Document Integrity | Agent 1: Identity & Document Integrity |
| Agent 2a: External Database Cross-Verification | Agent 2: External Database Cross-Verification |
| Agent 4: FinCrime Screening Interpretation | Agent 3: FinCrime Screening Interpretation |
| Agent 2: Corporate Structure & UBO Mapping | Agent 4: Corporate Structure & UBO Mapping |
| Agent 3: Business Model Plausibility | Folded into Agent 5 as an internal sub-analysis |
| Agent 5: Compliance Memo Agent | Agent 5: Compliance Memo & Risk Recommendation Agent |
| Agents 6, 7, 8, 10 | Retained with canonical numbering |
| Missing Agent 9 | Restored as Agent 9: Regulatory Impact (future-phase) |

## Audit note

Historical records, screenshots, or operator notes created before this
remediation may still use the drifted numbering above. New code, seeded agent
configuration, and in-scope UI surfaces should use only the canonical model.

