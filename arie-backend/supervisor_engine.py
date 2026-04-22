"""
ARIE Finance — Supervisor Engine: Contradiction Detection & Verdict
Extracted from server.py during Sprint 2 monolith decomposition.

Provides:
    - run_memo_supervisor() — 11-check contradiction detection + verdict computation
"""
import logging
from datetime import datetime

logger = logging.getLogger("arie")

def run_memo_supervisor(memo_data):
    """
    Supervisor layer for compliance memos.
    Detects contradictions between memo sections and produces a verdict.

    Returns:
        {
            "verdict": "CONSISTENT" | "CONSISTENT_WITH_WARNINGS" | "INCONSISTENT",
            "contradictions": [...],
            "warnings": [...],
            "recommendation": str,
            "supervisor_confidence": float
        }
    """
    sections = memo_data.get("sections") or {}
    metadata = memo_data.get("metadata") or {}

    # Defensive: ensure sub-dicts are dicts not None
    for key in list(sections.keys()):
        if sections[key] is None:
            sections[key] = {}

    contradictions = []
    warnings = []

    # Safe content getter to handle None values
    def _sc(section_key, field="content", default=""):
        s = sections.get(section_key) or {}
        v = s.get(field, default)
        return v if v is not None else default

    # ── 1. Risk rating vs decision consistency ──
    risk_rating = metadata.get("risk_rating") or ""
    decision = metadata.get("approval_recommendation") or ""
    RISK_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4}

    if RISK_RANK.get(risk_rating, 2) >= 3 and decision == "APPROVE":
        contradictions.append({
            "category": "risk_vs_decision",
            "severity": "critical",
            "description": "Risk rating is " + risk_rating + " but decision is unconditional APPROVE. High-risk entities require conditions or review.",
            "section_a": "risk_assessment",
            "section_b": "compliance_decision"
        })
    elif RISK_RANK.get(risk_rating, 2) <= 1 and decision in ("REJECT", "REVIEW"):
        contradictions.append({
            "category": "risk_vs_decision",
            "severity": "critical",
            "description": "Risk rating is LOW but decision is " + decision + ". Low-risk entities should not be rejected without extraordinary cause.",
            "section_a": "risk_assessment",
            "section_b": "compliance_decision"
        })

    # ── 2. Ownership section vs ownership risk rating ──
    own_section = sections.get("ownership_and_control") or {}
    own_content = _sc("ownership_and_control").lower()
    risk_sub = (sections.get("risk_assessment") or {}).get("sub_sections") or {}
    risk_sub = risk_sub.get("ownership_risk") or {}
    own_risk_rating = risk_sub.get("rating", "")

    if own_risk_rating == "LOW":
        if "critical" in own_content or "not provided" in own_content or "cannot be determined" in own_content:
            contradictions.append({
                "category": "ownership_inconsistency",
                "severity": "critical",
                "description": "Ownership section identifies critical gaps or missing data, but ownership risk is rated LOW.",
                "section_a": "ownership_and_control",
                "section_b": "risk_assessment.ownership_risk"
            })

    # ── 3. PEP findings vs screening results ──
    screening_content = _sc("screening_results").lower()
    exec_content = _sc("executive_summary").lower()

    # Detect *confirmed* PEP matches — exclude negations and properly-handled PEP flagging
    pep_negation_phrases = [
        "no matches", "0 confirmed", "no pep", "not a pep", "no confirmed pep",
        "pep screening clear", "clear pep", "clean pep", "no pep matches"
    ]
    pep_handled_phrases = [
        "identified and flagged", "flagged for enhanced", "flagged and subject to",
        "pep(s) identified and flagged", "enhanced due diligence applied",
        "enhanced measures applied", "appropriately flagged"
    ]
    has_pep_in_screening = (
        "pep" in screening_content
        and ("match" in screening_content or "identified" in screening_content or "confirmed" in screening_content)
        and not any(neg in screening_content for neg in pep_negation_phrases)
    )
    # PEP properly handled (identified + flagged + enhanced measures) is NOT a contradiction
    pep_properly_handled = any(phrase in screening_content for phrase in pep_handled_phrases)
    claims_no_pep_exec = "no pep exposure" in exec_content or "no pep" in exec_content

    if has_pep_in_screening and claims_no_pep_exec and not pep_properly_handled:
        contradictions.append({
            "category": "pep_inconsistency",
            "severity": "critical",
            "description": "Screening results identify PEP match(es) but executive summary claims no PEP exposure.",
            "section_a": "screening_results",
            "section_b": "executive_summary"
        })
    elif has_pep_in_screening and claims_no_pep_exec and pep_properly_handled:
        contradictions.append({
            "category": "pep_advisory",
            "severity": "medium",
            "description": "PEP identified and flagged with enhanced measures, but executive summary understates PEP exposure. Consider clarifying PEP status in executive summary.",
            "section_a": "screening_results",
            "section_b": "executive_summary"
        })

    # ── 3b. Priority A.2: Declared-PEP vs memo narrative contradiction ──
    # Source-of-truth signal lives in metadata.screening_state_summary
    # (populated by memo_handler from the application/director/UBO data).
    # If declared PEP exists but the memo body still denies PEP exposure,
    # this is a critical contradiction regardless of provider screening
    # state — declared PEP must never be flattened away.
    _scr_summary = metadata.get("screening_state_summary") or {}
    declared_pep_count = int(_scr_summary.get("declared_pep_count") or 0)
    if declared_pep_count > 0:
        # Concatenate the memo surfaces officers actually read.
        own_risk_sub = ((sections.get("risk_assessment") or {}).get("sub_sections") or {}).get("ownership_risk") or {}
        fc_risk_sub = ((sections.get("risk_assessment") or {}).get("sub_sections") or {}).get("financial_crime_risk") or {}
        narrative_chunks = [
            _sc("executive_summary"),
            _sc("screening_results"),
            _sc("ongoing_monitoring"),
            (own_risk_sub or {}).get("content", "") or "",
            (fc_risk_sub or {}).get("content", "") or "",
        ]
        # Also scan key_findings list since it is officer-visible.
        for _kf in (metadata.get("key_findings") or []):
            if isinstance(_kf, str):
                narrative_chunks.append(_kf)
        narrative_text = " \n ".join([c for c in narrative_chunks if c]).lower()
        denial_phrases = [
            "no pep exposure",
            "no declared or detected match",
            "no declared or detected pep",
            "no material pep concern",
            "no material pep or jurisdictional concern",
            "0 self-declared / detected match",
        ]
        matched_denials = [p for p in denial_phrases if p in narrative_text]
        if matched_denials:
            contradictions.append({
                "category": "declared_pep_contradiction",
                "severity": "critical",
                "description": (
                    "Declared PEP exists for " + str(declared_pep_count)
                    + " director/UBO subject(s) but memo narrative denies PEP exposure"
                    + " (matched phrasing: " + "; ".join(matched_denials[:3]) + ")."
                    + " Declared PEP must remain visible across executive summary,"
                    + " screening results, ownership risk, financial crime risk,"
                    + " and ongoing monitoring sections."
                ),
                "section_a": "metadata.screening_state_summary",
                "section_b": "memo_narrative"
            })

    # ── 4. Document verification vs decision conditions ──
    doc_content = _sc("document_verification").lower()
    decision_content = _sc("compliance_decision").lower()
    has_document_count = "document_count" in metadata
    document_count = int(metadata.get("document_count") or 0)

    has_outstanding_docs = "outstanding" in doc_content or "pending" in doc_content or "not verified" in doc_content
    no_documents_uploaded = (has_document_count and document_count == 0) or "no documents have been uploaded" in doc_content
    if has_outstanding_docs and decision == "APPROVE":
        contradictions.append({
            "category": "doc_vs_decision",
            "severity": "high",
            "description": "Document verification identifies outstanding/pending documents but decision is unconditional APPROVE without document remediation conditions.",
            "section_a": "document_verification",
            "section_b": "compliance_decision"
        })
    if no_documents_uploaded:
        if decision == "APPROVE":
            contradictions.append({
                "category": "missing_documents_vs_decision",
                "severity": "critical",
                "description": "No documents have been uploaded, but the memo recommends APPROVE. Approval cannot rely on undocumented entity verification.",
                "section_a": "document_verification",
                "section_b": "compliance_decision"
            })
        else:
            warnings.append({
                "category": "missing_documents",
                "severity": "warning",
                "description": "No supporting documents are uploaded for this application. Treat any approval recommendation as provisional until documents are received and reviewed."
            })

    # ── 5. Red flags vs mitigants balance ──
    rf_section = sections.get("red_flags_and_mitigants", {})
    red_flags = rf_section.get("red_flags", [])
    mitigants = rf_section.get("mitigants", [])

    if len(red_flags) > 0 and len(mitigants) == 0:
        rf_severity = "critical" if RISK_RANK.get(risk_rating, 2) >= 3 else "high"
        contradictions.append({
            "category": "rf_mitigant_imbalance",
            "severity": rf_severity,
            "description": str(len(red_flags)) + " red flag(s) identified but no mitigants provided. Every red flag should have a corresponding mitigant or escalation.",
            "section_a": "red_flags_and_mitigants",
            "section_b": "red_flags_and_mitigants"
        })
    elif len(red_flags) >= 3 and len(mitigants) <= 1:
        warnings.append({
            "category": "rf_mitigant_imbalance",
            "severity": "warning",
            "description": str(len(red_flags)) + " red flags but only " + str(len(mitigants)) + " mitigant(s). Consider whether all risks are adequately addressed."
        })

    # ── 6. AI explainability factors vs actual data ──
    ai_section = sections.get("ai_explainability", {})
    increasing_factors = ai_section.get("risk_increasing_factors", [])
    decreasing_factors = ai_section.get("risk_decreasing_factors", [])

    # Check factor classification correctness
    decrease_keywords = ["no pep", "low jurisdictional", "clean sanctions", "low sector", "full documentation"]
    for f in increasing_factors:
        f_lower = f.lower() if isinstance(f, str) else ""
        if any(kw in f_lower for kw in decrease_keywords):
            contradictions.append({
                "category": "factor_misclassification",
                "severity": "critical",
                "description": "Risk-decreasing item incorrectly listed as risk-increasing: " + (f[:100] if isinstance(f, str) else str(f)),
                "section_a": "ai_explainability",
                "section_b": "ai_explainability"
            })

    # ── 7. Confidence vs decision linkage (risk-aware threshold) ──
    confidence = metadata.get("confidence_level", 0)
    risk_rank_for_conf = RISK_RANK.get(risk_rating, 2)
    # Aligned with Rule Engine 4E: 70% floor for all, 75% for HIGH+ risk
    conf_threshold = 0.75 if risk_rank_for_conf >= 3 else 0.70
    if confidence and confidence < conf_threshold and decision in ("APPROVE", "APPROVE_WITH_CONDITIONS"):
        warnings.append({
            "category": "confidence_linkage",
            "severity": "warning",
            "description": "Model confidence is " + str(round(confidence * 100)) + "% (below " + str(round(conf_threshold * 100)) + "% threshold for " + risk_rating + " risk) with " + decision + " recommendation. Consider whether escalation to SCO review is warranted."
        })

    # ── 8. Jurisdiction risk vs monitoring tier ──
    jur_risk = ((sections.get("risk_assessment") or {}).get("sub_sections") or {}).get("jurisdiction_risk") or {}
    jur_risk = jur_risk.get("rating", "") if isinstance(jur_risk, dict) else ""
    monitoring = _sc("ongoing_monitoring").lower()

    if jur_risk == "HIGH" and "standard" in monitoring and "enhanced" not in monitoring:
        contradictions.append({
            "category": "jurisdiction_vs_monitoring",
            "severity": "high",
            "description": "Jurisdiction risk is HIGH but monitoring tier appears to be Standard. High-risk jurisdictions require Enhanced monitoring.",
            "section_a": "risk_assessment.jurisdiction_risk",
            "section_b": "ongoing_monitoring"
        })

    # ── 9. Aggregated risk vs original risk divergence ──
    original_risk = metadata.get("original_risk_level", "")
    aggregated_risk = metadata.get("aggregated_risk", "")
    if original_risk and aggregated_risk and original_risk != aggregated_risk:
        warnings.append({
            "category": "risk_aggregation_divergence",
            "severity": "info",
            "description": "Original application risk level (" + original_risk + ") differs from aggregated memo risk (" + aggregated_risk + "). The aggregated risk was computed from weighted sub-section analysis."
        })

    # ── 10. Rule Engine integration — ingest pre-generation violations ──
    rule_engine = metadata.get("rule_engine", {})
    rule_violations = rule_engine.get("violations", [])
    rule_enforcements = rule_engine.get("enforcements", [])
    engine_status = rule_engine.get("engine_status", "CLEAN")

    if rule_violations:
        for rv in rule_violations:
            severity = rv.get("severity", "warning")
            if severity in ("high", "critical"):
                contradictions.append({
                    "category": "rule_violation",
                    "severity": "critical",
                    "description": (
                        "Rule Engine violation [" + rv.get("rule", "UNKNOWN") + "]: "
                        + rv.get("detail", "No detail provided")
                    ),
                    "section_a": "rule_engine",
                    "section_b": rv.get("rule", "unknown")
                })
            else:
                warnings.append({
                    "category": "rule_violation",
                    "severity": "warning",
                    "description": "Rule Engine minor violation [" + rv.get("rule", "UNKNOWN") + "]: " + rv.get("detail", "")
                })

    # ── 11. Rule Engine enforcement verification ──
    # Verify that enforcements were actually applied (cross-check)
    for enforcement in rule_enforcements:
        rule_name = enforcement.get("rule", "")
        if rule_name == "CONFIDENCE_FLOOR" and decision == "APPROVE":
            contradictions.append({
                "category": "enforcement_bypass",
                "severity": "critical",
                "description": (
                    "CONFIDENCE_FLOOR rule was triggered (original: " + enforcement.get("original_decision", "?")
                    + ", enforced: " + enforcement.get("enforced_decision", "?")
                    + ") but final decision is still APPROVE. Rule enforcement was bypassed."
                ),
                "section_a": "rule_engine",
                "section_b": "compliance_decision"
            })
        if rule_name == "OWNERSHIP_FLOOR":
            # Verify ownership risk is not LOW in the final memo
            own_risk_final = ((sections.get("risk_assessment") or {}).get("sub_sections") or {}).get("ownership_risk") or {}
            own_risk_final = own_risk_final.get("rating", "") if isinstance(own_risk_final, dict) else ""
            if own_risk_final == "LOW":
                contradictions.append({
                    "category": "enforcement_bypass",
                    "severity": "critical",
                    "description": "OWNERSHIP_FLOOR rule enforced ownership to MEDIUM but final memo still shows ownership risk as LOW.",
                    "section_a": "rule_engine",
                    "section_b": "risk_assessment.ownership_risk"
                })

    # ── Compute verdict ──
    critical_contradictions = [c for c in contradictions if c.get("severity") == "critical"]
    high_contradictions = [c for c in contradictions if c.get("severity") == "high"]

    # True control layer: if rule violations exist AND contradictions exist, verdict CANNOT be CONSISTENT
    has_rule_violations = len(rule_violations) > 0
    has_unresolved_enforcements = any(
        c.get("category") == "enforcement_bypass" for c in contradictions
    )

    if len(critical_contradictions) >= 1 or has_unresolved_enforcements:
        verdict = "INCONSISTENT"
        recommendation = (
            "Memo contains " + str(len(critical_contradictions)) + " critical contradiction(s)"
            + (". Rule Engine detected " + str(len(rule_violations)) + " violation(s)" if has_rule_violations else "")
            + (". ENFORCEMENT BYPASS DETECTED — rule corrections were not applied to the final memo" if has_unresolved_enforcements else "")
            + ". Manual review required before this memo can be relied upon for compliance determination. Do not approve without resolving contradictions."
        )
    elif len(high_contradictions) >= 2 or len(contradictions) >= 3:
        verdict = "CONSISTENT_WITH_WARNINGS"
        recommendation = (
            "Memo has " + str(len(contradictions)) + " contradiction(s) and " + str(len(warnings)) + " warning(s)"
            + (". Rule Engine applied " + str(len(rule_enforcements)) + " correction(s)" if rule_enforcements else "")
            + ". Review flagged sections before finalising compliance decision."
        )
    elif contradictions or warnings or has_rule_violations:
        verdict = "CONSISTENT_WITH_WARNINGS"
        recommendation = (
            "Memo is broadly consistent. " + str(len(contradictions)) + " minor contradiction(s) and " + str(len(warnings)) + " warning(s) noted"
            + (". Rule Engine status: " + engine_status if engine_status != "CLEAN" else "")
            + ". Review before finalising."
        )
    else:
        verdict = "CONSISTENT"
        recommendation = "Memo sections are internally consistent. No inter-section contradictions detected. Rule Engine: CLEAN. Supervisor approves memo quality."

    # Supervisor confidence: penalised by contradictions and rule violations
    sup_confidence = 1.0
    sup_confidence -= len(critical_contradictions) * 0.15
    sup_confidence -= len(high_contradictions) * 0.08
    sup_confidence -= len(warnings) * 0.03
    sup_confidence -= len(rule_violations) * 0.05
    sup_confidence = max(0.1, round(sup_confidence, 2))

    # Control layer flags
    can_approve = verdict != "INCONSISTENT"
    requires_sco_review = verdict == "INCONSISTENT" or (has_rule_violations and len(rule_violations) >= 2)
    if no_documents_uploaded and decision in ("APPROVE", "APPROVE_WITH_CONDITIONS"):
        can_approve = False
        requires_sco_review = True

    # ── Priority B / Workstream B: mandatory_escalation flag ──────────
    # Derived from the Agent 5 input contract (memo_handler builds and
    # stores it under metadata.agent5_input_contract). When true, the
    # approval gate MUST refuse approval regardless of supervisor
    # verdict. This is the single authoritative escalation signal that
    # the UI and approval API consult.
    contract = metadata.get("agent5_input_contract") or {}
    risk_dims = contract.get("risk_dimensions") or {}
    screening_summary = contract.get("screening_terminality_summary") or {}
    mandatory_reasons = []
    if (contract.get("final_risk_level") or "").upper() in ("HIGH", "VERY_HIGH"):
        mandatory_reasons.append("final_risk_level=" + str(contract.get("final_risk_level")))
    if contract.get("declared_pep_present"):
        mandatory_reasons.append("declared_pep_present")
    if (risk_dims.get("jurisdiction") or "").upper() in ("HIGH", "VERY_HIGH"):
        mandatory_reasons.append("jurisdiction_risk_tier=" + str(risk_dims.get("jurisdiction")))
    if (risk_dims.get("business") or "").upper() in ("HIGH",):
        mandatory_reasons.append("sector_risk_tier=HIGH")
    if (contract.get("ownership_transparency_status") or "").lower() in ("opaque", "incomplete"):
        mandatory_reasons.append("ownership_transparency=" + str(contract.get("ownership_transparency_status")))
    if screening_summary.get("has_terminal_match"):
        mandatory_reasons.append("material_screening_concern")
    if verdict == "INCONSISTENT":
        mandatory_reasons.append("supervisor_verdict=INCONSISTENT")
    mandatory_escalation = len(mandatory_reasons) > 0

    if mandatory_escalation:
        # An escalated case must never be marked approvable by the
        # supervisor. The approval gate enforces this explicitly, but
        # we also flip can_approve here so any consumer that ignores
        # mandatory_escalation still fail-closes.
        can_approve = False
        requires_sco_review = True

    return {
        "verdict": verdict,
        "contradictions": contradictions,
        "warnings": warnings,
        "contradiction_count": len(contradictions),
        "warning_count": len(warnings),
        "recommendation": recommendation,
        "supervisor_confidence": sup_confidence,
        "rule_engine_status": engine_status,
        "rule_violations_ingested": len(rule_violations),
        "rule_enforcements_applied": len(rule_enforcements),
        "can_approve": can_approve,
        "requires_sco_review": requires_sco_review,
        "mandatory_escalation": mandatory_escalation,
        "mandatory_escalation_reasons": mandatory_reasons,
        "checked_at": datetime.now().isoformat()
    }
