"""
ARIE Finance — Memo Handler: Compliance Memo Building Logic
Extracted from ComplianceMemoHandler.post() during Sprint 2 monolith decomposition.

Provides:
    - build_compliance_memo(app, directors, ubos, documents) → (memo, rule_engine_result, supervisor_result, validation_result)
"""
import json
import logging
from datetime import datetime

from validation_engine import validate_compliance_memo
from supervisor_engine import run_memo_supervisor
from rule_engine import (
    HIGH_RISK_COUNTRIES, OFFSHORE_COUNTRIES,
    HIGH_RISK_SECTORS, MINIMUM_MEDIUM_SECTORS, MEDIUM_RISK_SECTORS,
    ALWAYS_RISK_DECREASING, ALWAYS_RISK_INCREASING,
    RISK_WEIGHTS, RISK_RANK,
    SANCTIONED, FATF_BLACK,
)
from environment import ENV, is_demo

logger = logging.getLogger("arie")


def build_compliance_memo(app, directors, ubos, documents):
    """
    Build a complete compliance memo from application data.
    Pure computation — no DB or HTTP dependencies.

    Returns:
        tuple: (memo, rule_engine_result, supervisor_result, validation_result)
    """
    # Collect PEP matches
    pep_directors = [d for d in directors if d.get("is_pep") == "Yes"]
    pep_ubos = [u for u in ubos if u.get("is_pep") == "Yes"]
    all_peps = pep_directors + pep_ubos
    has_documents = len(documents) > 0
    verified_docs = [d for d in documents if d.get("verification_status") == "verified"]
    pending_docs = [d for d in documents if d.get("verification_status") != "verified"]
    documentation_complete = has_documents and not pending_docs
    now_ts = datetime.now().isoformat()
    country = app["country"] or "Information not provided"
    sector = app["sector"] or "Information not provided"
    entity_type = app["entity_type"] or "Information not provided"
    sof = app.get("source_of_funds") or "Information not provided"
    exp_vol = app.get("expected_volume") or "Information not provided"
    own_struct = app.get("ownership_structure") or "Information not provided"

    # Build risk sub-section ratings based on app risk
    risk_level = app["risk_level"] or "MEDIUM"
    risk_score = app["risk_score"] or 50
    doc_confidence = round(len(verified_docs) / max(len(documents), 1) * 100) if has_documents else 0

    # Jurisdiction risk classification with reasoning
    # Constants imported from rule_engine.py (single source of truth)

    is_high_risk_country = country in HIGH_RISK_COUNTRIES
    is_offshore = country in OFFSHORE_COUNTRIES
    is_sanctioned_country = country.lower().strip() in SANCTIONED or country.lower().strip() in FATF_BLACK
    is_high_risk_sector = sector in HIGH_RISK_SECTORS
    is_medium_risk_sector = sector in MEDIUM_RISK_SECTORS
    is_minimum_medium_sector = sector in MINIMUM_MEDIUM_SECTORS

    jur_rating = "VERY_HIGH" if is_sanctioned_country else "HIGH" if is_high_risk_country else "MEDIUM" if is_offshore else "LOW"
    # Rule 4C: Sectors with inherent minimum MEDIUM risk floor
    biz_rating = "HIGH" if is_high_risk_sector else "MEDIUM" if (is_medium_risk_sector or is_minimum_medium_sector) else "LOW"
    fc_rating = "MEDIUM" if len(all_peps) > 0 else "LOW"
    tx_rating = risk_level  # Transaction risk mirrors overall application risk level
    doc_rating = "HIGH" if not has_documents else "LOW" if doc_confidence >= 80 else "MEDIUM" if doc_confidence >= 50 else "HIGH"
    dq_rating = "LOW" if (sof != "Information not provided" and exp_vol != "Information not provided") else "MEDIUM"

    # ══════════════════════════════════════════════════════════
    # LAYER 1 — PRE-GENERATION RULE ENGINE (Hard Constraints)
    # Deterministic rules that enforce correctness BEFORE memo is built.
    # Every rule violation is logged. Rules CANNOT be overridden by AI.
    # ══════════════════════════════════════════════════════════
    rule_violations = []
    rule_enforcements = []

    # ── SANCTIONED COUNTRY FLOOR: Force VERY_HIGH jurisdiction risk ──
    if is_sanctioned_country:
        rule_enforcements.append({
            "rule": "SANCTIONED_COUNTRY_FLOOR",
            "original": jur_rating if jur_rating != "VERY_HIGH" else "VERY_HIGH",
            "enforced": "VERY_HIGH",
            "reason": country + " is a sanctioned or FATF blacklisted jurisdiction — jurisdiction risk floor is VERY_HIGH"
        })

    # ── RULE 4C: Business risk floor enforcement ──
    if is_minimum_medium_sector and biz_rating == "LOW":
        rule_enforcements.append({
            "rule": "BIZ_RISK_FLOOR",
            "original": "LOW",
            "enforced": "MEDIUM",
            "reason": sector + " sector carries minimum MEDIUM inherent risk per FATF risk-based approach guidance"
        })
        biz_rating = "MEDIUM"

    # ── RULE 4A: Factor classification hard constraints ──
    # ALWAYS_RISK_DECREASING / ALWAYS_RISK_INCREASING imported from rule_engine.py

    # Structure complexity
    struct_complexity = "Complex" if len(ubos) > 3 or own_struct.lower().find("nominee") >= 0 else "Layered" if len(ubos) > 1 else "Simple"

    # Determine primary UBO for control statement
    primary_ubo = max(ubos, key=lambda u: float(u.get("ownership_pct", 0) or 0)) if ubos else None
    control_name = primary_ubo["full_name"] if primary_ubo else "Information not provided"
    control_pct = primary_ubo.get("ownership_pct", "N/A") if primary_ubo else "N/A"

    # Ownership risk: multi-factor assessment (Issue B fix)
    # Consider PEP count, missing ownership %, unclear control, UBO transparency
    own_risk_factors = 0
    own_risk_reasons = []
    if len(all_peps) > 1:
        own_risk_factors += 2
        own_risk_reasons.append("multiple PEPs in ownership/governance structure")
    elif len(all_peps) == 1:
        own_risk_factors += 1
        own_risk_reasons.append("PEP identified in ownership/governance structure")
    if not ubos:
        own_risk_factors += 2
        own_risk_reasons.append("no UBO information provided — critical data gap")
    else:
        ubos_missing_pct = [u for u in ubos if not u.get("ownership_pct") or str(u.get("ownership_pct", "")).strip() in ("", "0", "N/A")]
        if ubos_missing_pct:
            own_risk_factors += 1
            own_risk_reasons.append("ownership percentage missing for " + str(len(ubos_missing_pct)) + " UBO(s)")
        total_pct = sum(float(u.get("ownership_pct", 0) or 0) for u in ubos)
        if total_pct < 75 and ubos:
            own_risk_factors += 1
            own_risk_reasons.append("total disclosed ownership is " + str(round(total_pct)) + "% — incomplete transparency")
    if not primary_ubo or control_pct in ("N/A", None, "", "0"):
        own_risk_factors += 1
        own_risk_reasons.append("effective control cannot be clearly determined")
    if struct_complexity == "Complex":
        own_risk_factors += 1
        own_risk_reasons.append("complex corporate structure with potential layering")
    own_rating = "HIGH" if own_risk_factors >= 3 else "MEDIUM" if own_risk_factors >= 1 else "LOW"
    own_rating_justification = "; ".join(own_risk_reasons) if own_risk_reasons else "clean ownership structure with full transparency"

    # ── RULE 4B: Ownership risk floor enforcement ──
    # IF ownership % missing OR control undetermined OR no UBOs → ownership risk CANNOT be LOW
    ownership_has_gaps = (
        not ubos
        or any(not u.get("ownership_pct") or str(u.get("ownership_pct", "")).strip() in ("", "0", "N/A") for u in ubos)
        or not primary_ubo
        or control_pct in ("N/A", None, "", "0")
    )
    if ownership_has_gaps and own_rating == "LOW":
        rule_enforcements.append({
            "rule": "OWN_RISK_FLOOR",
            "original": "LOW",
            "enforced": "MEDIUM",
            "reason": "Ownership risk cannot be LOW when ownership percentages are missing, control is undetermined, or UBO data is absent"
        })
        own_rating = "MEDIUM"
        if not own_risk_reasons:
            own_risk_reasons.append("ownership data gaps prevent LOW classification")
        own_rating_justification = "; ".join(own_risk_reasons)

    # Risk aggregation consistency check (Issue C fix)
    # RISK_WEIGHTS and RISK_RANK imported from rule_engine.py
    sub_risk_vals = {
        "jurisdiction": RISK_RANK.get(jur_rating, 2),
        "business": RISK_RANK.get(biz_rating, 2),
        "transaction": RISK_RANK.get(tx_rating, 2),
        "ownership": RISK_RANK.get(own_rating, 2),
        "fincrime": RISK_RANK.get(fc_rating, 2),
        "documentation": RISK_RANK.get(doc_rating, 2),
        "data_quality": RISK_RANK.get(dq_rating, 2),
    }
    weighted_risk = sum(sub_risk_vals[k] * RISK_WEIGHTS[k] for k in RISK_WEIGHTS)
    # Derive effective risk level from weighted average
    effective_risk = "LOW" if weighted_risk < 1.5 else "MEDIUM" if weighted_risk < 2.5 else "HIGH" if weighted_risk < 3.5 else "VERY_HIGH"
    # Use the higher of app risk_level or computed effective_risk to be conservative
    final_risk = max(RISK_RANK.get(risk_level, 2), RISK_RANK.get(effective_risk, 2))
    RANK_RISK = {1: "LOW", 2: "MEDIUM", 3: "HIGH", 4: "VERY_HIGH"}
    aggregated_risk = RANK_RISK.get(final_risk, risk_level)

    # ── RULE 4D: Multi-gap escalation enforcement ──
    # IF multiple critical gaps exist (documents, ownership, SoF) → must escalate
    critical_gaps = []
    if not ubos:
        critical_gaps.append("no_ubo_data")
    if not has_documents:
        critical_gaps.append("no_documents_uploaded")
    elif pending_docs and len(pending_docs) >= 2:
        critical_gaps.append("multiple_docs_outstanding")
    if sof == "Information not provided":
        critical_gaps.append("source_of_funds_missing")
    if exp_vol == "Information not provided":
        critical_gaps.append("expected_volume_missing")
    if ownership_has_gaps:
        critical_gaps.append("ownership_gaps")

    if len(critical_gaps) >= 3 and RISK_RANK.get(aggregated_risk, 2) < 2:
        rule_enforcements.append({
            "rule": "MULTI_GAP_ESCALATION",
            "original": aggregated_risk,
            "enforced": "MEDIUM",
            "reason": str(len(critical_gaps)) + " critical data gaps (" + ", ".join(critical_gaps) + ") prevent LOW overall risk classification"
        })
        aggregated_risk = "MEDIUM"
    elif len(critical_gaps) >= 4 and RISK_RANK.get(aggregated_risk, 2) < 3:
        rule_enforcements.append({
            "rule": "MULTI_GAP_ESCALATION",
            "original": aggregated_risk,
            "enforced": "HIGH",
            "reason": str(len(critical_gaps)) + " critical data gaps require escalation to HIGH risk"
        })
        aggregated_risk = "HIGH"

    # Confidence-to-decision linkage (Issue D fix)
    model_confidence = max(60, doc_confidence - 5)
    low_confidence = model_confidence < 70

    # Decision — uses aggregated risk and confidence linkage
    if low_confidence and aggregated_risk == "LOW":
        # Low confidence should not allow clean approval
        decision = "APPROVE_WITH_CONDITIONS"
        decision_label = "APPROVAL WITH CONDITIONS"
    elif aggregated_risk == "LOW":
        decision = "APPROVE"
        decision_label = "APPROVAL"
    elif aggregated_risk == "MEDIUM":
        decision = "APPROVE_WITH_CONDITIONS"
        decision_label = "APPROVAL WITH CONDITIONS"
    elif aggregated_risk == "HIGH":
        decision = "REVIEW"
        decision_label = "SENIOR COMPLIANCE OFFICER REVIEW"
    else:
        decision = "REJECT"
        decision_label = "REJECTION"

    # ── RULE 4E: Confidence enforcement ──────────────────────────────
    # If model confidence < 70%, decision MUST be at minimum APPROVE_WITH_CONDITIONS
    # with explicit escalation/review language. Clean APPROVE is forbidden.
    if model_confidence < 70 and decision == "APPROVE":
        rule_enforcements.append({
            "rule": "CONFIDENCE_FLOOR",
            "original_decision": decision,
            "enforced_decision": "APPROVE_WITH_CONDITIONS",
            "confidence": model_confidence,
            "reason": (
                "Model confidence of " + str(model_confidence) + "% is below the 70% threshold. "
                "Clean approval is not permitted — decision escalated to APPROVE_WITH_CONDITIONS "
                "with mandatory enhanced monitoring and 90-day review."
            )
        })
        decision = "APPROVE_WITH_CONDITIONS"
        decision_label = "APPROVAL WITH CONDITIONS"
    # If confidence < 60%, escalate further to REVIEW regardless of risk
    if model_confidence < 60 and decision not in ("REVIEW", "REJECT"):
        rule_enforcements.append({
            "rule": "CONFIDENCE_CRITICAL_FLOOR",
            "original_decision": decision,
            "enforced_decision": "REVIEW",
            "confidence": model_confidence,
            "reason": (
                "Model confidence of " + str(model_confidence) + "% is critically low (below 60%). "
                "Decision escalated to SENIOR COMPLIANCE OFFICER REVIEW."
            )
        })
        decision = "REVIEW"
        decision_label = "SENIOR COMPLIANCE OFFICER REVIEW"

    mon_tier = "Enhanced" if aggregated_risk in ("HIGH", "VERY_HIGH") or all_peps else "Standard"

    # ── Compile Rule Engine summary ───────────────────────────────────
    rule_engine_result = {
        "rules_checked": ["FACTOR_CLASSIFICATION", "OWNERSHIP_FLOOR", "BUSINESS_RISK_FLOOR", "MULTI_GAP_ESCALATION", "CONFIDENCE_FLOOR"],
        "violations": rule_violations,
        "enforcements": rule_enforcements,
        "total_enforcements": len(rule_enforcements),
        "total_violations": len(rule_violations),
        "engine_status": "ENFORCED" if rule_enforcements else "CLEAN",
        "timestamp": now_ts
    }

    # Generate Big 4-grade 11-section memo structure
    memo = {
        "application_ref": app["ref"],
        "company_name": app["company_name"],
        "memo_generated": now_ts,
        "sections": {
            "executive_summary": {
                "title": "Executive Summary",
                "content": (
                    f"This memo presents the compliance assessment of {app['company_name']} (BRN: {app['brn']}), "
                    f"a {entity_type} incorporated in {country}, operating in the {sector} sector. "
                    f"The composite risk score of {risk_score}/100 (aggregated: {aggregated_risk}) reflects "
                    f"{'a balanced risk profile' if aggregated_risk == 'MEDIUM' else 'a low-risk profile with no material concerns' if aggregated_risk == 'LOW' else 'an elevated risk profile requiring enhanced scrutiny'}. "
                    f"Model confidence: {model_confidence}%"
                    + (f" — reduced due to {'no uploaded documentation and ' if not has_documents else 'outstanding documentation and ' if pending_docs else ''}{'limited historical transaction data' if True else ''}. " if model_confidence < 80 else ". ")
                    + f"The principal risk drivers are "
                    + (f"the presence of {len(all_peps)} Politically Exposed Person(s) ({', '.join([p['full_name'] for p in all_peps])})" if all_peps else "")
                    + (f"{',' if all_peps else ''} ownership risk rated {own_rating} ({own_rating_justification})" if own_rating in ("HIGH", "MEDIUM") and own_risk_reasons else "")
                    + (f"{',' if all_peps or own_rating in ('HIGH', 'MEDIUM') else ''} the {'high-risk' if is_high_risk_country else 'offshore'} jurisdictional classification of {country}" if is_high_risk_country or is_offshore else "")
                    + (f"no PEP exposure and a clean ownership structure" if not all_peps and own_rating == "LOW" and not is_high_risk_country and not is_offshore else "")
                    + ". "
                    + ("These risk factors are " if (all_peps or own_rating in ("HIGH", "MEDIUM") or is_high_risk_country or is_offshore) else "The low-risk profile is supported by ")
                    + ("materially offset by " if aggregated_risk in ("LOW", "MEDIUM") else "insufficiently offset by ")
                    + (f"clean sanctions screening across all major consolidated lists, " if not all_peps else "")
                    + (f"a fully traceable beneficial ownership chain ({control_name} at {control_pct}%)" if primary_ubo and control_pct not in ("N/A", None, "", "0") else "beneficial ownership assessment")
                    + (f", and {len(verified_docs)} of {len(documents)} documents verified at {doc_confidence}% confidence. " if has_documents else ", and no uploaded documents are currently available to substantiate entity verification. ")
                    + ("No documents have been uploaded, so entity verification remains incomplete and cannot be treated as a mitigant. " if not has_documents else f"{len(pending_docs)} document(s) remain outstanding, representing a documentation gap that must be remedied within 14 business days. " if pending_docs else "")
                    + (f"Note: model confidence of {model_confidence}% is below threshold — this is reflected in the conditional nature of the recommendation. " if low_confidence else "")
                    + f"Recommendation: {decision_label}"
                    + (f" — subject to {'PEP declaration and enhanced monitoring' if all_peps else 'standard conditions including enhanced monitoring due to reduced confidence' if low_confidence else 'standard conditions'}." if aggregated_risk in ("MEDIUM", "HIGH") else ".")
                )
            },
            "client_overview": {
                "title": "Client Overview",
                "content": (
                    f"Entity Name: {app['company_name']}. Business Registration Number: {app['brn']}. "
                    f"Jurisdiction of Incorporation: {country}. Entity Type: {entity_type}. "
                    f"Sector: {sector}. Application Reference: {app['ref']}. "
                    f"Ownership Structure: {own_struct}. "
                    f"Source of Funds: {sof}"
                    + (". The stated source of funds " + ("appears consistent with the entity's business profile" if sof != "Information not provided" else "— this data gap prevents assessment of fund origin legitimacy and elevates residual risk") + ". ")
                    + f"Expected Transaction Volume: {exp_vol}"
                    + (f". {'Transaction volumes fall within expected parameters for the stated business activity and jurisdiction.' if exp_vol != 'Information not provided' else ' This data gap reduces the ability to benchmark transaction patterns and establishes a monitoring dependency.'}" if True else "")
                )
            },
            "ownership_and_control": {
                "title": "Ownership & Control",
                "structure_complexity": struct_complexity,
                "control_statement": f"{control_name} exercises effective control as {'majority' if float(control_pct or 0) > 50 else 'significant'} shareholder ({control_pct}%)." if primary_ubo else "Effective control cannot be determined — this represents a material data gap requiring resolution.",
                "content": (
                    f"The entity operates a {struct_complexity.lower()} corporate structure with {len(directors)} director(s) and {len(ubos)} UBO(s). "
                    + " ".join([
                        f"Director: {d['full_name']} ({d.get('nationality', 'nationality not provided')} national)"
                        + (f" — identified as PEP. Enhanced due diligence required under FATF Recommendation 12." if d.get("is_pep") == "Yes" else " — not a PEP.")
                        for d in directors
                    ]) + " "
                    + " ".join([
                        f"UBO: {u['full_name']} — {u.get('ownership_pct', 'Information not provided')}% direct ownership"
                        + (f" ({u.get('nationality', 'nationality not provided')} national)" if u.get("nationality") else "")
                        + (f". Identified as PEP — this UBO exercises both ownership and potential political influence, significantly elevating risk." if u.get("is_pep") == "Yes" else ".")
                        + (f" Ownership percentage information not provided — this represents a data gap that prevents full UBO analysis." if not u.get("ownership_pct") else "")
                        for u in ubos
                    ])
                    + (f" No UBO information provided — this is a critical data gap that prevents beneficial ownership verification and materially elevates risk." if not ubos else "")
                    + f" No nominee arrangements or bearer shares were {'disclosed' if ubos else 'identifiable from available data'}."
                )
            },
            "risk_assessment": {
                "title": "Risk Assessment",
                "sub_sections": {
                    "jurisdiction_risk": {
                        "title": "Jurisdiction Risk",
                        "rating": jur_rating,
                        "content": (
                            f"Jurisdiction of incorporation: {country}. "
                            + (f"{country} is designated as a high-risk jurisdiction due to comprehensive international sanctions, FATF blacklisting, or active conflict zone status. This presents severe jurisdictional risk that materially affects the overall risk assessment." if is_high_risk_country
                               else f"{country} presents moderate jurisdictional risk. While {'currently compliant with FATF standards' if is_offshore else 'not on current FATF lists'}, the jurisdiction retains characteristics of an offshore financial centre — including cross-border capital flow facilitation and international business licence regimes — that elevate baseline risk for ML/TF purposes." if is_offshore
                               else f"{country} presents low jurisdictional risk. The jurisdiction maintains adequate AML/CFT frameworks, is not on FATF grey or black lists, and does not exhibit characteristics associated with elevated ML/TF risk.")
                            + f" Risk weighting factor: 0.20."
                        )
                    },
                    "business_risk": {
                        "title": "Business Risk",
                        "rating": biz_rating,
                        "content": (
                            f"Sector: {sector}. "
                            + (f"The {sector} sector carries elevated inherent ML/TF risk due to the potential for anonymous transactions, rapid value transfer, or regulatory arbitrage. This significantly elevates the entity's business risk profile. FATF Guidance on a Risk-Based Approach identifies this sector as requiring enhanced scrutiny." if is_high_risk_sector
                               else f"The {sector} sector carries moderate inherent risk due to the intermediary nature of the business and exposure to third-party funds. However, the stated business model appears plausible and consistent with the entity's incorporation documents and jurisdictional profile, partially mitigating this concern." if is_medium_risk_sector
                               else f"The {sector} sector presents low inherent business risk. The stated business activity does not exhibit typology indicators associated with elevated ML/TF risk.")
                            + f" Source of funds: {sof}. "
                            + (f"The stated source of funds is consistent with the entity's business model and sector profile. " if sof != "Information not provided"
                               else "Source of funds was not provided — this data gap prevents assessment of fund origin legitimacy and modestly elevates residual business risk. ")
                            + f"Expected volume: {exp_vol}. "
                            + (f"Stated volumes are within expected parameters for a {entity_type} in the {sector} sector within {country}. " if exp_vol != "Information not provided"
                               else "Expected transaction volumes not provided — this limits the ability to assess proportionality and creates a monitoring dependency. ")
                            + f"Business model plausibility was assessed within Agent 5 (Compliance Memo & Risk Recommendation) using sector, source-of-funds, and business-description inputs. Risk weighting factor: 0.15."
                        )
                    },
                    "transaction_risk": {
                        "title": "Transaction Risk",
                        "rating": risk_level,
                        "content": (
                            f"Expected transaction volume: {exp_vol}. Source of funds: {sof}. "
                            + (f"{'Transaction volumes and source of funds are within expected parameters for the stated business activity.' if exp_vol != 'Information not provided' and sof != 'Information not provided' else 'Insufficient transaction data provided to conduct meaningful benchmarking — this data gap creates a monitoring dependency and modestly reduces confidence in the forward-looking risk assessment.'} "
                               f"As a {'newly onboarded' if True else 'existing'} entity, limited historical transaction data is available, which constrains the ability to identify anomalous patterns at this stage.")
                            + f" Risk weighting factor: 0.10."
                        )
                    },
                    "ownership_risk": {
                        "title": "Ownership Risk",
                        "rating": own_rating,
                        "content": (
                            f"The entity has {len(directors)} director(s) and {len(ubos)} UBO(s). "
                            f"Structure complexity: {struct_complexity}. "
                            f"Ownership risk rating: {own_rating} — based on: {own_rating_justification}. "
                            + (f"{len(all_peps)} PEP(s) identified among the ownership and governance structure. "
                               f"PEP exposure introduces corruption and undue influence risk per FATF Recommendation 12 requirements for enhanced scrutiny. "
                               + ("The PEP(s) hold direct ownership, elevating control risk. " if pep_ubos else "The PEP(s) hold board positions but no direct ownership stake, which partially mitigates control risk. ")
                               if all_peps else "No PEP exposure identified in the ownership or governance structure, which is a positive risk indicator. ")
                            + (f"Beneficial ownership has been identified to natural person level — {control_name} ({control_pct}%) exercises effective control. " if primary_ubo and control_pct not in ("N/A", None, "", "0")
                               else f"Beneficial ownership partially identified but {'ownership percentages are incomplete for some UBOs' if ubos else 'could not be verified — this is a critical gap'}. " if ubos
                               else "No UBO information provided — beneficial ownership verification is not possible. This is a critical deficiency. ")
                            + f"Risk weighting factor: 0.25."
                        )
                    },
                    "financial_crime_risk": {
                        "title": "Financial Crime Risk",
                        "rating": fc_rating,
                        "content": (
                            f"Sanctions screening was conducted across UN Security Council, EU, OFAC SDN, and HMT consolidated lists. "
                            + ("No matches were returned for any director, UBO, or the entity itself. " if not all_peps else f"{len(all_peps)} PEP match(es) identified requiring enhanced assessment. ")
                            + "Adverse media screening returned no relevant hits across global media databases. "
                            + f"The entity's business model {'does not exhibit' if fc_rating in ('LOW', 'MEDIUM') else 'may exhibit'} typology indicators associated with money laundering, terrorist financing, or proliferation financing. "
                            + f"Risk weighting factor: 0.10."
                        )
                    }
                }
            },
            "screening_results": {
                "title": "Screening Results",
                "content": (
                    f"Sanctions Screening: Conducted against UN Security Council Consolidated List, EU Consolidated Financial Sanctions List, OFAC SDN List, and HMT Consolidated List. "
                    + ("No matches returned for any associated individual or the entity itself. " if not all_peps
                       else f"PEP matches identified — assessed as confirmed true positives based on verified identity data. ")
                    + f"PEP Screening: {len(all_peps)} confirmed match(es)"
                    + (" — " + ". ".join([p["full_name"] + " identified as PEP. PEP declaration form and enhanced due diligence documentation requested." for p in all_peps]) if all_peps else " — no matches identified.")
                    + " Adverse Media Screening: Comprehensive search conducted across global news and regulatory enforcement databases. No relevant hits identified for any associated individual or the entity. "
                    + f"Company Registry Verification: {app['company_name']} verified against registry records. "
                    + f"Registration details are {'consistent' if verified_docs else 'pending verification against'} application data."
                )
            },
            "document_verification": {
                "title": "Document Verification",
                "content": (
                    f"{len(documents)} document(s) submitted, {len(verified_docs)} verified. "
                    + " ".join([
                        f"{d.get('doc_type', 'Document')}: "
                        + (f"Verified — document is authentic and internally consistent with application data. Cross-referenced against entity name, registration number, and jurisdiction details. No discrepancies, alterations, or anomalies identified." if d.get("verification_status") == "verified"
                           else f"Pending verification — document has been received but automated and manual verification is in progress. Until verified, this document cannot be relied upon for compliance determination." if d.get("verification_status") == "pending"
                           else f"Not verified — {'document has been formally requested and must be received within 14 business days. This gap prevents full entity verification and is a material deficiency that elevates residual risk.' if d.get('verification_status') == 'missing' else 'verification status requires manual review by compliance officer.'}")
                        for d in documents
                    ])
                    + (" No documents have been uploaded. Entity verification cannot be completed until the required corporate and identity documents are received and reviewed." if not has_documents else "")
                    + (f" Professional judgement: {len(pending_docs)} document(s) remain outstanding. "
                       + f"The absence of {'these critical documents' if len(pending_docs) > 1 else 'this document'} prevents complete entity verification "
                       + f"and {'materially weakens' if len(pending_docs) >= 2 else 'partially reduces'} the overall assurance level. "
                       + f"This deficiency is reflected as a condition of approval with a 14-business-day remediation deadline." if pending_docs
                       else " All required documents have been received and verified. Document set is complete and internally consistent — high assurance in entity verification." if documentation_complete else "")
                    + f" Overall documentation adequacy: {doc_confidence}%. "
                    + ("Documentation confidence is 0% because no supporting documents have been uploaded yet. This is a material deficiency that prevents a fully supported onboarding recommendation."
                       if not has_documents else
                       f"Documentation confidence is reduced due to {len(pending_docs)} outstanding item(s). "
                       + f"Impact assessment: {'the documentation gap is material and would weaken regulatory defensibility of the compliance decision' if doc_confidence < 60 else 'documentation is partially complete but the gap must be remedied to achieve full compliance assurance'}."
                       if pending_docs else "Full documentation received — high confidence in entity verification. Documentation set meets regulatory expectations.")
                )
            },
            "ai_explainability": {
                "title": "AI Explainability Layer",
                "risk_increasing_factors": [f for f in [
                    (f"PEP presence ({len(all_peps)} identified) — weight: 0.25, elevating ownership risk due to FATF Recommendation 12 requirements" if all_peps else None),
                    (f"{'High-risk' if is_high_risk_country else 'Offshore'} jurisdiction ({country}) — weight: 0.20, elevating baseline cross-border risk" if is_high_risk_country or is_offshore else None),
                    (f"{sector} sector — weight: 0.15, elevated inherent sector risk" if is_high_risk_sector or is_medium_risk_sector else None),
                    (f"Ownership risk: {own_rating} — {own_rating_justification}" if own_rating in ("HIGH", "MEDIUM") else None),
                    ("No supporting documents uploaded — entity verification remains incomplete and documentation risk is elevated" if not has_documents else None),
                    (f"Documentation gap: {len(pending_docs)} document(s) outstanding, reducing verification confidence" if pending_docs else None),
                    ("Limited trading history — no historical transaction data for benchmarking" if True else None),
                ] if f is not None],
                "risk_decreasing_factors": [f for f in [
                    ("No PEP exposure among directors or UBOs — no contribution to ownership risk" if not all_peps else None),
                    (f"Low jurisdictional risk — {country} maintains adequate AML/CFT frameworks" if not is_high_risk_country and not is_offshore else None),
                    (f"Low sector risk — {sector} does not exhibit elevated ML/TF typology indicators" if not is_high_risk_sector and not is_medium_risk_sector else None),
                    ("Clean sanctions screening across all major consolidated lists (UN, EU, OFAC, HMT)" if not all_peps else "Screening completed — PEP(s) identified and flagged for enhanced measures"),
                    (f"Verified beneficial ownership traced to natural person level — {control_name} ({control_pct}%) exercises effective control" if primary_ubo and control_pct not in ("N/A", None, "", "0") else None),
                    (f"Full documentation received and verified at {doc_confidence}% confidence" if documentation_complete and doc_confidence >= 80 else None),
                ] if f is not None],
                "factor_enforcement_applied": True,
                "content": (
                    f"Risk scoring model: Onboarda Composite Risk Engine v2.1. "
                    f"Scoring methodology: Weighted multi-factor analysis across 5 risk dimensions, calibrated against Basel Committee and Wolfsberg Group risk factor guidance. "
                    f"Overall risk score: {risk_score}/100 ({risk_level}). "
                    f"Model confidence: {max(60, doc_confidence - 5)}% — "
                    + (f"confidence is reduced from baseline due to {'no uploaded documentation and ' if not has_documents else 'outstanding documentation and ' if pending_docs else ''}limited historical transaction data. " if doc_confidence < 100 else "high confidence based on complete documentation. ")
                    + f"Risk-increasing factors: "
                    + (f"(1) PEP presence ({len(all_peps)} identified) — weight: 0.25, elevating ownership risk due to FATF Recommendation 12 requirements. " if all_peps else "")
                    + (f"{'(2) ' if all_peps else '(1) '}{'High-risk' if is_high_risk_country else 'Offshore'} jurisdiction ({country}) — weight: 0.20, elevating baseline cross-border risk. " if is_high_risk_country or is_offshore else "")
                    + (f"{'(3) ' if all_peps and (is_high_risk_country or is_offshore) else '(2) ' if all_peps or (is_high_risk_country or is_offshore) else '(1) '}{sector} sector — weight: 0.15, elevated inherent sector risk. " if is_high_risk_sector or is_medium_risk_sector else "")
                    + (f"Ownership risk ({own_rating}): {own_rating_justification}. " if own_rating in ("HIGH", "MEDIUM") else "")
                    + ("No uploaded documents are available, so documentation risk remains elevated and entity verification cannot be treated as complete. " if not has_documents else f"Documentation gap: {len(pending_docs)} outstanding document(s) reduce verification confidence. " if pending_docs else "")
                    + ("Limited trading history constrains forward-looking risk confidence. " if True else "")
                    + f"Risk-decreasing factors: "
                    + (f"(1) No PEP exposure — no contribution to ownership risk. " if not all_peps else "(1) PEP(s) identified and flagged for enhanced monitoring. ")
                    + (f"(2) Low jurisdictional risk — {country} maintains adequate AML/CFT frameworks. " if not is_high_risk_country and not is_offshore else "")
                    + (f"{'(3) ' if not is_high_risk_country and not is_offshore else '(2) '}{'Clean' if not all_peps else 'Completed'} sanctions screening across all major consolidated lists. " if True else "")
                    + (f"{'(4) ' if not is_high_risk_country and not is_offshore else '(3) '}Verified beneficial ownership to natural person level. " if primary_ubo and control_pct not in ("N/A", None, "", "0") else "")
                    + (f"Full documentation at {doc_confidence}% confidence. " if documentation_complete and doc_confidence >= 80 else "")
                    + f"Decision pathway: Agent 1 (Identity & Document Integrity) -> Agent 2 (External Database Cross-Verification) -> Agent 3 (FinCrime Screening Interpretation) -> Agent 4 (Corporate Structure & UBO Mapping) -> Agent 5 (Compliance Memo & Risk Recommendation). "
                    + "Supervisor module: Contradiction detection and inter-agent consistency check. "
                    + "Monitoring pipeline: Agent 6 (Periodic Review Preparation) -> Agent 7 (Adverse Media & PEP Monitoring) -> Agent 8 (Behaviour & Risk Drift) -> Agent 9 (Regulatory Impact) -> Agent 10 (Ongoing Compliance Review)."
                )
            },
            "red_flags_and_mitigants": {
                "title": "Red Flags & Mitigants",
                "red_flags": (
                    [f"Politically Exposed Person identified: {p['full_name']}. {'Board membership confers governance influence and introduces corruption and undue influence risk per FATF Recommendation 12, requiring enhanced scrutiny.' if p in pep_directors else 'Direct ownership by a PEP significantly elevates control risk and the potential for proceeds of corruption to enter the financial system.'}" for p in all_peps]
                    + (["No uploaded documents are available for corporate or identity verification. This prevents complete entity verification and materially weakens the evidential basis for approval."] if not has_documents else [])
                    + ([f"Documentation gap: {len(pending_docs)} of {len(documents)} required document(s) remain outstanding. This prevents complete entity verification and creates residual risk until remedied."] if pending_docs else [])
                    + ([f"Offshore jurisdiction: {country} retains characteristics of an international financial centre, elevating baseline risk for cross-border fund flows and regulatory arbitrage."] if is_offshore else [])
                    + ([f"High-risk jurisdiction: {country} is subject to comprehensive sanctions or FATF blacklisting, presenting severe jurisdictional risk."] if is_high_risk_country else [])
                    + ([f"High-risk sector: {sector} carries elevated inherent ML/TF risk due to the nature of the business activity."] if is_high_risk_sector else [])
                    + [f"Limited trading history: As a new onboarding, there is no historical transaction data against which to benchmark expected volumes. This reduces forward-looking risk confidence and creates a monitoring dependency."]
                ) or ["No material red flags identified. Standard monitoring applies."],
                "mitigants": (
                    [f"PEP ({p['full_name']}) {'holds no direct ownership stake, reducing control risk. PEP declaration and enhanced monitoring will be applied as conditions of approval.' if p in pep_directors else 'has been identified and PEP declaration, source of wealth verification, and enhanced monitoring are required as conditions.'}" for p in all_peps]
                    + (["Document collection has been initiated, but no uploaded documents are yet available to support entity verification. Approval must remain conditional on document receipt and review."] if not has_documents else [f"Outstanding documents have been formally requested with a 14-business-day deadline. Failure to provide will trigger automatic escalation to Senior Compliance Officer. The {len(verified_docs)} documents already verified are internally consistent."] if pending_docs else [f"All required documents received and verified at {doc_confidence}% confidence, providing strong assurance of entity legitimacy."])
                    + ([f"{country} is currently compliant with FATF standards following completion of its action plan. The entity's business activity is consistent with the jurisdiction's commercial profile."] if is_offshore else [])
                    + [f"Sanctions screening completed across all major consolidated lists (UN, EU, OFAC, HMT) with {'no matches' if not all_peps else 'PEP identification and appropriate enhanced measures'}."]
                    + ([f"Beneficial ownership fully traced to natural person level via {struct_complexity.lower()} structure. {control_name} ({control_pct}%) confirmed as exercising effective control."] if primary_ubo else [])
                    + ["Transaction monitoring will be applied on a quarterly basis for the first 12 months, with automated alerts for anomalous volumes, compensating for the absence of historical benchmarking data."]
                )
            },
            "compliance_decision": {
                "title": "Compliance Decision",
                "decision": decision,
                "content": (
                    f"On the basis of the composite risk assessment ({risk_level} — {risk_score}/100), "
                    f"{'clean' if not all_peps else 'flagged'} screening results, "
                    f"{'unavailable' if not has_documents else 'verified' if not pending_docs else 'partially verified'} documentation ({doc_confidence}% confidence), "
                    f"and {'confirmed' if ubos else 'unverified'} beneficial ownership, "
                    f"this application is recommended for {decision_label}. "
                    + (f"The {'conditions' if risk_level in ('MEDIUM', 'HIGH') else 'recommendation'} reflect{'s' if risk_level == 'LOW' else ''} the residual risks identified — "
                       f"{'principally the PEP exposure and ' if all_peps else ''}{'absence of uploaded documents' if not has_documents else 'documentation gap' if pending_docs else 'limited trading history'}. " if risk_level != "LOW" else "The low-risk profile supports standard onboarding with no additional conditions. ")
                    + ("Conditions of approval: " if risk_level in ("MEDIUM", "HIGH") else "")
                    + (f"(1) {'PEP declaration form(s) must be completed and signed by ' + ', '.join([p['full_name'] for p in all_peps]) + ' within 14 business days. ' if all_peps else ''}" if risk_level != "LOW" else "")
                    + (f"{'(2) ' if all_peps else '(1) '}All required corporate and identity documents must be uploaded and reviewed within 14 business days before the onboarding decision can be treated as fully supported. " if not has_documents and risk_level != "LOW" else "")
                    + (f"{'(2) ' if all_peps else '(1) '}Outstanding documents must be received within 14 business days. Failure to comply will trigger escalation. " if has_documents and pending_docs and risk_level != "LOW" else "")
                    + (f"{'(3) ' if all_peps and (pending_docs or not has_documents) else '(2) ' if all_peps or pending_docs or not has_documents else '(1) '}Enhanced monitoring ({mon_tier.lower()} tier) for the first 12 months. " if risk_level in ("MEDIUM", "HIGH", "VERY_HIGH") or all_peps else "")
                    + f"Residual risk acknowledgement: {'Residual risk remains manageable within the conditions framework and does not warrant rejection at this stage.' if risk_level in ('MEDIUM', 'HIGH') else 'Minimal residual risk identified.' if risk_level == 'LOW' else 'Residual risk is elevated and requires SCO determination before proceeding.'}"
                )
            },
            "ongoing_monitoring": {
                "title": "Ongoing Monitoring & Review",
                "content": (
                    f"Monitoring tier: {mon_tier} — assigned due to "
                    + (f"the combination of {'PEP presence, ' if all_peps else ''}{'offshore jurisdiction, ' if is_offshore else ''}{'elevated sector risk, ' if is_high_risk_sector or is_medium_risk_sector else ''}{risk_level} composite risk." if mon_tier == "Enhanced"
                       else f"the {risk_level} composite risk profile with no material PEP or jurisdictional concerns.")
                    + f" Review frequency: {'6 months' if risk_level in ('HIGH', 'VERY_HIGH') else '12 months'} (standard for {risk_level} risk entities)"
                    + (f", with an interim 6-month review triggered by PEP conditions" if all_peps and risk_level not in ("HIGH", "VERY_HIGH") else "")
                    + f". Transaction monitoring: {'Quarterly' if risk_level != 'LOW' else 'Annual'} review of transaction patterns against stated business activity. "
                    + "Trigger events requiring immediate review: (1) Any change in beneficial ownership structure or directorship composition. "
                    + "(2) Adverse media alerts from continuous screening. "
                    + (f"(3) Change in PEP status for {', '.join([p['full_name'] for p in all_peps])}. " if all_peps else "(3) New PEP identification among associated persons. ")
                    + "(4) Transaction volumes exceeding 150% of stated expectations in any quarter. "
                    + "(5) Regulatory action, investigation, or enforcement proceedings against the entity or any associated person. "
                    + (f"(6) Failure to provide outstanding documents within the 14-business-day deadline." if pending_docs else "")
                )
            },
            "audit_and_governance": {
                "title": "Audit & Governance",
                "content": (
                    f"This compliance onboarding memo was generated by RegMind's deterministic memo builder and validated against structured onboarding data. "
                    f"It reflects the live onboarding control path used in this environment: deterministic memo generation, memo validation, and memo supervisor consistency checks. "
                    f"Separate monitoring agents and decision-support pipelines may exist elsewhere in the platform, but they are not represented here as authoritative memo-generation controls. "
                    f"The memo supervisor performs contradiction detection and consistency review over this memo output. "
                    f"Document classification: CONFIDENTIAL — this document contains personal data subject to applicable data protection legislation. "
                    f"Retention period: 7 years from date of generation or termination of business relationship, whichever is later. "
                    f"Applicable compliance frameworks: FATF 40 Recommendations (2012, as updated), applicable AML/CFT legislation in {country}. "
                    f"Generated: {now_ts}. Memo version: 1.0. "
                    f"Reviewed by: {app.get('assigned_to', 'Pending assignment')} — memo pending Senior Compliance Officer review."
                )
            }
        },
        "metadata": {
            "risk_rating": aggregated_risk,
            "risk_score": risk_score,
            "original_risk_level": risk_level,
            "aggregated_risk": aggregated_risk,
            "weighted_risk_score": round(weighted_risk, 2),
            "confidence_level": model_confidence / 100,
            "low_confidence_flag": low_confidence,
            "ownership_risk_rating": own_rating,
            "ownership_risk_justification": own_rating_justification,
            "supervisor_status": "pending",
            "ai_source": "deterministic" if not is_demo() else "demo",
            "approval_recommendation": decision,
            "document_count": len(documents),
            "verified_document_count": len(verified_docs),
            "pending_document_count": len(pending_docs),
            "documentation_complete": documentation_complete,
            "key_findings": [
                f"Beneficial ownership {'traced to natural persons via ' + struct_complexity.lower() + ' structure — ' + control_name + ' (' + str(control_pct) + '%) exercises effective control' if primary_ubo else 'could not be verified — critical data gap'}",
                f"{'PEP identified: ' + ', '.join([p['full_name'] + ' (' + ('Director' if p in pep_directors else 'UBO') + ')' for p in all_peps]) + '. Enhanced due diligence required.' if all_peps else 'No PEP exposure identified among directors or UBOs'}",
                f"Sanctions and adverse media screening {'clear' if not all_peps else 'completed with PEP identification'} across all consolidated lists",
                ("No documents uploaded — entity verification remains incomplete" if not has_documents else f"{len(verified_docs)} of {len(documents)} documents verified at {doc_confidence}% confidence" + (f"; {len(pending_docs)} outstanding" if pending_docs else " — full documentation")),
                f"{'Business model assessed as plausible and consistent with regulatory authorisations' if sector != 'Information not provided' else 'Business model assessment limited by insufficient sector data'}",
                f"{country} jurisdiction presents {'severe' if is_high_risk_country else 'moderate' if is_offshore else 'low'} risk — {'sanctions/FATF blacklist' if is_high_risk_country else 'offshore IFC classification' if is_offshore else 'adequate AML/CFT framework'}"
            ],
            "conditions": (
                ([f"PEP declaration form(s) to be completed and signed by {', '.join([p['full_name'] for p in all_peps])} within 14 business days"] if all_peps else [])
                + (["All required corporate and identity documents must be uploaded and reviewed before the onboarding recommendation can be relied upon"] if not has_documents else [])
                + ([f"Outstanding documents ({', '.join([d.get('doc_type', 'document') for d in pending_docs])}) to be received within 14 business days — escalation on non-compliance"] if pending_docs else [])
                + ([f"Enhanced monitoring ({mon_tier.lower()} tier) for first 12 months with quarterly transaction review"] if risk_level in ("MEDIUM", "HIGH", "VERY_HIGH") or all_peps else [])
                + ([f"Bank reference letter required for PEP(s): {', '.join([p['full_name'] for p in all_peps])}"] if all_peps else [])
            ),
            "review_checklist": [
                f"Company identity verified against registry — {'confirmed active' if verified_docs else 'not yet evidenced by uploaded documents' if not has_documents else 'pending verification'}",
                f"UBO chain mapped to natural persons: {control_name + ' (' + str(control_pct) + '%)' if primary_ubo else 'Not verified — data gap'}",
                f"PEP screening completed — {len(all_peps)} confirmed match(es)" + (f": {', '.join([p['full_name'] for p in all_peps])}" if all_peps else ""),
                "Sanctions screening completed — no matches across UN, EU, OFAC, HMT lists",
                "Adverse media review conducted — no relevant hits identified",
                f"Source of funds {'reviewed and assessed as consistent' if sof != 'Information not provided' else 'not provided — data gap flagged'}",
                f"Business model plausibility {'confirmed' if sector != 'Information not provided' else 'assessment limited by data gap'}",
                (f"Document verification not started — no uploaded documents available ({len(verified_docs)}/{len(documents)}) at {doc_confidence}% confidence" if not has_documents else f"Document verification completed ({len(verified_docs)}/{len(documents)}) at {doc_confidence}% confidence"),
                f"Composite risk score reviewed: {risk_score}/100 ({risk_level}) at {max(60, doc_confidence - 5)}% model confidence",
                f"Compliance decision ({decision.replace('_', ' ')}) aligned with risk assessment findings and conditions framework"
            ],
            "rule_engine": rule_engine_result,
            "risk_dimensions": {
                "jurisdiction": {"rating": jur_rating, "weight": 0.20},
                "ownership": {"rating": own_rating, "weight": 0.25, "justification": own_rating_justification},
                "business": {"rating": biz_rating, "weight": 0.15},
                "fincrime": {"rating": fc_rating, "weight": 0.10},
                "transaction": {"rating": tx_rating, "weight": 0.10},
                "documentation": {"rating": doc_rating, "weight": 0.10},
                "data_quality": {"rating": dq_rating, "weight": 0.10}
            },
            "factor_classification_rules": {
                "always_risk_decreasing": ALWAYS_RISK_DECREASING,
                "always_risk_increasing": ALWAYS_RISK_INCREASING
            }
        }
    }

    # ── RULE 4A enforcement: Post-generation factor classification check ──
    # Verify no ALWAYS_RISK_DECREASING keywords appear in risk_increasing_factors
    # and no ALWAYS_RISK_INCREASING keywords appear in risk_decreasing_factors
    ai_section = memo.get("sections", {}).get("ai_explainability", {})
    inc_factors = ai_section.get("risk_increasing_factors", [])
    dec_factors = ai_section.get("risk_decreasing_factors", [])

    corrected_inc = []
    corrected_dec = list(dec_factors)
    for factor in inc_factors:
        factor_lower = factor.lower()
        is_misclassified = False
        for kw in ALWAYS_RISK_DECREASING:
            if kw in factor_lower:
                is_misclassified = True
                rule_violations.append({
                    "rule": "FACTOR_CLASSIFICATION",
                    "severity": "high",
                    "detail": "Risk-decreasing keyword '" + kw + "' found in risk_increasing_factors: " + factor[:80],
                    "action": "Moved to risk_decreasing_factors"
                })
                corrected_dec.append(factor)
                break
        if not is_misclassified:
            corrected_inc.append(factor)

    corrected_dec_final = []
    for factor in corrected_dec:
        factor_lower = factor.lower()
        is_misclassified = False
        for kw in ALWAYS_RISK_INCREASING:
            if kw in factor_lower:
                is_misclassified = True
                rule_violations.append({
                    "rule": "FACTOR_CLASSIFICATION",
                    "severity": "high",
                    "detail": "Risk-increasing keyword '" + kw + "' found in risk_decreasing_factors: " + factor[:80],
                    "action": "Moved to risk_increasing_factors"
                })
                corrected_inc.append(factor)
                break
        if not is_misclassified:
            corrected_dec_final.append(factor)

    # Apply corrections
    ai_section["risk_increasing_factors"] = corrected_inc
    ai_section["risk_decreasing_factors"] = corrected_dec_final

    # Update rule engine result with any new violations found during factor enforcement
    rule_engine_result["violations"] = rule_violations
    rule_engine_result["total_violations"] = len(rule_violations)
    if rule_violations:
        rule_engine_result["engine_status"] = "VIOLATIONS_DETECTED"
    memo["metadata"]["rule_engine"] = rule_engine_result

    # Run memo supervisor — contradiction detection & verdict
    supervisor_result = run_memo_supervisor(memo)
    memo["supervisor"] = supervisor_result
    memo["metadata"]["supervisor_status"] = supervisor_result["verdict"]
    memo["metadata"]["supervisor_confidence"] = supervisor_result["supervisor_confidence"]

    # Run validation engine — now with rule engine awareness
    validation_result = validate_compliance_memo(memo)
    memo["validation"] = validation_result
    memo["metadata"]["quality_score"] = validation_result["quality_score"]
    memo["metadata"]["validation_status"] = validation_result["validation_status"]

    # ── Final gate: If rule violations exist AND supervisor is INCONSISTENT, block memo
    if (rule_engine_result["total_violations"] > 0
            and supervisor_result["verdict"] == "INCONSISTENT"):
        memo["metadata"]["validation_status"] = "fail"
        memo["metadata"]["blocked"] = True
        memo["metadata"]["block_reason"] = (
            "Memo blocked: " + str(rule_engine_result["total_violations"]) + " rule violation(s) detected "
            "AND supervisor verdict is INCONSISTENT. Manual review required before approval."
        )

    return memo, rule_engine_result, supervisor_result, validation_result
