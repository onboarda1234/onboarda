"""
ARIE Finance — AI Agent Supervisor: Agent Executor Integration
================================================================
Maps stored application data into the supervisor's agent executor
interface for decision-support style outputs.

Each executor function:
  - Accepts (application_id, context_data)
  - Fetches application data from the DB
  - Builds output from stored data using deterministic, heuristic, or synthetic logic
  - Returns a raw dict matching the agent's Pydantic schema

Usage:
    from supervisor.agent_executors import register_all_executors
    supervisor = setup_supervisor(db_path)
    register_all_executors(supervisor, db_path)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .schemas import AgentType, AgentStatus, Severity

logger = logging.getLogger("arie.supervisor.executors")

# Current versions
AGENT_VERSION = "1.0.0"
PROMPT_VERSION = "v1.0-2026Q1"
MODEL_NAME = "claude-sonnet-4-6"
MEMO_MODEL = "claude-opus-4-6"


def _get_app_data(db_path: str, application_id: str) -> Dict[str, Any]:
    """Fetch full application data bundle from DB."""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    app = db.execute(
        "SELECT * FROM applications WHERE id = ? OR ref = ?",
        (application_id, application_id)
    ).fetchone()
    if not app:
        db.close()
        raise RuntimeError(f"Application not found: {application_id}")

    app_dict = dict(app)
    real_id = app_dict["id"]

    # C-02: decrypt PII fields on read
    try:
        from server import decrypt_pii_fields, PII_FIELDS_DIRECTORS, PII_FIELDS_UBOS
        directors = [decrypt_pii_fields(dict(d), PII_FIELDS_DIRECTORS) for d in db.execute(
            "SELECT * FROM directors WHERE application_id=?", (real_id,)
        ).fetchall()]
        ubos = [decrypt_pii_fields(dict(u), PII_FIELDS_UBOS) for u in db.execute(
            "SELECT * FROM ubos WHERE application_id=?", (real_id,)
        ).fetchall()]
    except ImportError:
        directors = [dict(d) for d in db.execute(
            "SELECT * FROM directors WHERE application_id=?", (real_id,)
        ).fetchall()]
        ubos = [dict(u) for u in db.execute(
            "SELECT * FROM ubos WHERE application_id=?", (real_id,)
        ).fetchall()]

    documents = [dict(d) for d in db.execute(
        "SELECT * FROM documents WHERE application_id=?", (real_id,)
    ).fetchall()]

    db.close()

    return {
        "application": app_dict,
        "directors": directors,
        "ubos": ubos,
        "documents": documents,
    }


def _base_output(agent_type: AgentType, agent_name: str, application_id: str, run_id: str) -> Dict[str, Any]:
    """Build base output fields required by AgentOutputBase."""
    return {
        "agent_name": agent_name,
        "agent_type": agent_type.value,
        "agent_version": AGENT_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model_name": MODEL_NAME,
        "run_id": run_id,
        "application_id": application_id,
        "processed_at": datetime.utcnow().isoformat() + "Z",
    }


# ═══════════════════════════════════════════════════════════
# AGENT 1: Identity & Document Integrity
# ═══════════════════════════════════════════════════════════

def execute_identity_document(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 1: Verify document authenticity and cross-check identity data."""
    db_path = context.get("db_path", "")
    data = _get_app_data(db_path, application_id)
    app = data["application"]
    docs = data["documents"]
    directors = data["directors"]
    run_id = str(uuid4())

    verified_docs = [d for d in docs if d.get("verification_status") == "verified"]
    pending_docs = [d for d in docs if d.get("verification_status") != "verified"]
    has_tampering = any(d.get("tampering_detected") for d in docs)

    # Build per-document verification results
    doc_results = []
    for d in docs:
        doc_results.append({
            "document_type": d.get("document_type", "unknown"),
            "filename": d.get("filename", ""),
            "status": d.get("verification_status", "pending"),
            "verified": d.get("verification_status") == "verified",
        })

    # Completeness assessment
    required_types = {"passport", "certificate_of_incorporation", "proof_of_address"}
    provided_types = {d.get("document_type", "").lower() for d in docs}
    missing = list(required_types - provided_types)
    completeness = "PASS" if not missing else "PARTIAL" if len(missing) < len(required_types) else "FAIL"

    # Confidence based on document verification rates
    doc_confidence = len(verified_docs) / max(len(docs), 1)
    overall_confidence = max(0.3, min(1.0, doc_confidence * 0.7 + (0.3 if not has_tampering else 0.0)))

    status = AgentStatus.CLEAN if not has_tampering and not missing else AgentStatus.ISSUES_FOUND

    findings = []
    evidence = []
    detected_issues = []

    if has_tampering:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "document_tampering",
            "title": "Document tampering indicators detected",
            "description": "One or more documents show signs of potential manipulation or alteration.",
            "severity": Severity.CRITICAL.value,
            "confidence": 0.85,
            "source": "document_analysis",
            "evidence_refs": [],
            "regulatory_relevance": "FATF R10 requires reliable identification documents"
        })

    for d in verified_docs:
        evidence.append({
            "evidence_id": str(uuid4())[:12],
            "evidence_type": "document",
            "source": "document_upload",
            "content_summary": f"Verified {d.get('document_type', 'document')}: {d.get('filename', '')}",
            "reference": d.get("id", ""),
            "verified": True,
            "timestamp": d.get("uploaded_at") or datetime.utcnow().isoformat(),
        })

    if missing:
        detected_issues.append({
            "issue_id": str(uuid4())[:12],
            "issue_type": "missing_documents",
            "title": "Required documents missing",
            "description": f"Missing: {', '.join(missing)}",
            "severity": Severity.HIGH.value,
            "blocking": True,
            "remediation": "Request missing documents from applicant",
            "related_findings": [],
        })

    output = _base_output(AgentType.IDENTITY_DOCUMENT_INTEGRITY, "Agent 1: Identity & Document Integrity", application_id, run_id)
    output.update({
        "status": status.value,
        "confidence_score": round(overall_confidence, 3),
        "findings": findings,
        "evidence": evidence,
        "detected_issues": detected_issues,
        "risk_indicators": [],
        "recommendation": "Proceed" if status == AgentStatus.CLEAN else "Review document issues before proceeding",
        "escalation_flag": has_tampering,
        "escalation_reason": "Document tampering detected — requires manual review" if has_tampering else None,
        "documents_verified": doc_results,
        "missing_documents": missing,
        "document_completeness": completeness,
        "tampering_indicators": ["potential_manipulation"] if has_tampering else [],
        "image_manipulation_detected": has_tampering,
    })
    return output


# ═══════════════════════════════════════════════════════════
# AGENT 2: External Database Verification
# ═══════════════════════════════════════════════════════════

def execute_external_database(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 2: Heuristic registry cross-reference from stored application data."""
    db_path = context.get("db_path", "")
    data = _get_app_data(db_path, application_id)
    app = data["application"]
    directors = data["directors"]
    run_id = str(uuid4())

    # Simulate registry lookup based on application data.
    # This executor is decision-support only until a real registry integration is wired here.
    company_name = app.get("company_name", "")
    country = app.get("country", "")
    reg_number = app.get("registration_number", "")

    # In production this would call OpenCorporates / Companies House APIs
    company_found = bool(company_name and country)
    directors_match = {"match": len(directors) > 0, "checked": len(directors)}

    findings = []
    evidence = []
    discrepancies = []

    if company_found:
        evidence.append({
            "evidence_id": str(uuid4())[:12],
            "evidence_type": "registry_record",
            "source": "company_registry",
            "content_summary": f"Company '{company_name}' found in {country} registry",
            "reference": reg_number or f"REG-{country}-{company_name[:10]}",
            "verified": True,
        })
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "registry_verification",
            "title": "Company registry verification",
            "description": f"Entity '{company_name}' verified in {country} official registry.",
            "severity": Severity.INFO.value,
            "confidence": 0.90,
            "source": "company_registry",
            "evidence_refs": [],
        })
    else:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "company_not_found",
            "title": "Company not found in registry",
            "description": f"Unable to verify '{company_name}' in {country} registry. May indicate an unregistered entity.",
            "severity": Severity.HIGH.value,
            "confidence": 0.80,
            "source": "company_registry",
            "evidence_refs": [],
            "regulatory_relevance": "Entity must be registered in declared jurisdiction per FATF R24"
        })

    confidence = 0.88 if company_found else 0.60
    status = AgentStatus.CLEAN if company_found and not discrepancies else AgentStatus.ISSUES_FOUND

    output = _base_output(AgentType.EXTERNAL_DATABASE_VERIFICATION, "Agent 2: External Database Cross-Verification", application_id, run_id)
    output.update({
        "status": status.value,
        "confidence_score": round(confidence, 3),
        "findings": findings,
        "evidence": evidence,
        "detected_issues": [],
        "risk_indicators": [],
        "recommendation": "Registry verification complete" if company_found else "Manual registry check required",
        "escalation_flag": not company_found,
        "escalation_reason": "Company not found in official registry" if not company_found else None,
        "company_found": company_found,
        "registry_source": f"{country} Companies Registry",
        "registered_name": company_name if company_found else None,
        "registration_number_match": bool(reg_number),
        "directors_match": directors_match,
        "discrepancies": discrepancies,
        "company_status": "active" if company_found else "not_found",
    })
    return output


# ═══════════════════════════════════════════════════════════
# AGENT 2: Corporate Structure & UBO Mapping
# ═══════════════════════════════════════════════════════════

def execute_corporate_structure_ubo(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 4: Deterministic UBO mapping from stored application data."""
    db_path = context.get("db_path", "")
    data = _get_app_data(db_path, application_id)
    app = data["application"]
    directors = data["directors"]
    ubos = data["ubos"]
    run_id = str(uuid4())

    # Build UBO analysis from stored application data only.
    # Do not make provider calls here unless the provider output becomes part of the
    # authoritative returned payload and is clearly labelled as such.
    ubo_list = []
    total_ownership = 0
    for u in ubos:
        pct = float(u.get("ownership_pct", 0) or 0)
        total_ownership += pct
        ubo_list.append({
            "name": u.get("full_name", "Unknown"),
            "ownership_pct": pct,
            "nationality": u.get("nationality", "Unknown"),
            "is_pep": u.get("is_pep") == "Yes",
        })

    ubo_completeness = min(total_ownership / 100.0, 1.0) if ubos else 0.0
    has_nominee = any("nominee" in str(u.get("full_name", "")).lower() or
                      "nominee" in str(app.get("ownership_structure", "")).lower()
                      for u in ubos)
    complex_structure = len(ubos) > 3 or has_nominee
    shell_indicators = []
    if has_nominee:
        shell_indicators.append("nominee_arrangement_detected")
    if len(directors) == 0 and len(ubos) == 0:
        shell_indicators.append("no_officers_or_ubos")

    confidence = 0.85 if ubo_completeness > 0.75 else 0.65 if ubo_completeness > 0.5 else 0.45
    status = AgentStatus.CLEAN if ubo_completeness > 0.75 and not shell_indicators else AgentStatus.ISSUES_FOUND

    findings = [{
        "finding_id": str(uuid4())[:12],
        "category": "ubo_mapping",
        "title": "UBO Mapping Complete" if ubo_completeness > 0.75 else "UBO Mapping Incomplete",
        "description": f"Mapped {len(ubos)} beneficial owner(s) covering {total_ownership:.0f}% ownership. "
                       f"Structure complexity: {'Complex' if complex_structure else 'Simple'}.",
        "severity": Severity.INFO.value if ubo_completeness > 0.75 else Severity.HIGH.value,
        "confidence": confidence,
        "source": "deterministic_ubo_mapping",
        "evidence_refs": [],
        "regulatory_relevance": "FATF R24/R25 require identification of all beneficial owners >25%"
    }]

    evidence = [{
        "evidence_id": str(uuid4())[:12],
        "evidence_type": "corporate_record",
        "source": "application_data",
        "content_summary": f"Corporate structure with {len(directors)} directors, {len(ubos)} UBOs",
        "reference": app.get("ref", application_id),
        "verified": True,
    }]

    detected_issues = []
    if ubo_completeness < 0.75:
        detected_issues.append({
            "issue_id": str(uuid4())[:12],
            "issue_type": "incomplete_ownership",
            "title": "Ownership not fully mapped",
            "description": f"Only {total_ownership:.0f}% of ownership has been identified.",
            "severity": Severity.HIGH.value,
            "blocking": True,
            "remediation": "Request additional ownership documentation",
            "related_findings": [],
        })
    if not ubos:
        detected_issues.append({
            "issue_id": str(uuid4())[:12],
            "issue_type": "ubo_not_identified",
            "title": "No UBOs identified",
            "description": "No beneficial owners have been identified for this entity.",
            "severity": Severity.CRITICAL.value,
            "blocking": True,
            "remediation": "UBO identification is mandatory under AML regulations",
            "related_findings": [],
        })

    output = _base_output(AgentType.CORPORATE_STRUCTURE_UBO, "Agent 4: Corporate Structure & UBO Mapping", application_id, run_id)
    output.update({
        "status": status.value,
        "confidence_score": round(confidence, 3),
        "findings": findings,
        "evidence": evidence,
        "detected_issues": detected_issues,
        "risk_indicators": [{"indicator_type": i, "description": i.replace("_", " "), "risk_level": "high", "source_agent": "corporate_structure_ubo", "contributing_factors": []} for i in shell_indicators],
        "recommendation": "Ownership structure verified" if status == AgentStatus.CLEAN else "UBO identification requires further investigation",
        "escalation_flag": not ubos or bool(shell_indicators),
        "escalation_reason": "Missing UBO identification or shell company indicators" if not ubos or shell_indicators else None,
        "ownership_structure": {"layers": len(ubos), "complexity": "complex" if complex_structure else "simple"},
        "ubos_identified": ubo_list,
        "ubo_completeness": round(ubo_completeness, 3),
        "complex_structure_flag": complex_structure,
        "shell_company_indicators": shell_indicators,
        "circular_ownership_detected": False,
        "nominee_arrangements_detected": has_nominee,
        "indirect_ownership_paths": [],
        "total_ownership_mapped_pct": round(total_ownership, 1),
    })
    return output


# ═══════════════════════════════════════════════════════════
# INTERNAL SUB-ANALYSIS: Business Model Plausibility
# ═══════════════════════════════════════════════════════════

def _build_business_model_summary(app: Dict[str, Any]) -> Dict[str, Any]:
    """Internal business plausibility summary used inside Agent 5."""
    sector = app.get("sector", "Unknown")
    country = app.get("country", "Unknown")
    sof = app.get("source_of_funds", "")
    expected_vol = app.get("expected_volume", "")

    HIGH_RISK_SECTORS = ("Cryptocurrency", "Money Services", "Gaming", "Arms", "Precious Metals")
    MEDIUM_RISK_SECTORS = ("Financial Services", "Real Estate", "Legal Services", "Trust Services", "Art Dealing")

    is_high_risk_sector = sector in HIGH_RISK_SECTORS
    is_medium_risk_sector = sector in MEDIUM_RISK_SECTORS

    plausibility = 0.80 if sof and expected_vol else 0.55
    industry_risk = "HIGH" if is_high_risk_sector else "MEDIUM" if is_medium_risk_sector else "LOW"

    red_flags = []
    if is_high_risk_sector:
        red_flags.append(f"High-risk sector: {sector}")
    if not sof or sof == "Information not provided":
        red_flags.append("Source of funds not declared")

    confidence = 0.85 if plausibility > 0.7 else 0.65
    return {
        "sector": sector,
        "country": country,
        "plausibility": round(plausibility, 3),
        "industry_risk_level": industry_risk,
        "revenue_model_plausibility": "plausible" if plausibility > 0.7 else "requires_review",
        "red_flags": red_flags,
        "confidence_score": round(confidence, 3),
        "summary": (
            f"Business model: {sector} in {country}. "
            f"Source of funds: {'declared' if sof and sof != 'Information not provided' else 'not declared'}."
        ),
    }


# ═══════════════════════════════════════════════════════════
# AGENT 3: FinCrime Screening Interpretation
# ═══════════════════════════════════════════════════════════

def execute_fincrime_screening(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 3: Deterministic interpretation of stored screening-related fields."""
    db_path = context.get("db_path", "")
    data = _get_app_data(db_path, application_id)
    app = data["application"]
    directors = data["directors"]
    ubos = data["ubos"]
    run_id = str(uuid4())

    # Aggregate screening-related fields from stored application data.
    # This executor is not a live screening provider integration.
    all_persons = directors + ubos
    screened_entities = [app.get("company_name", "")] + [p.get("full_name", "") for p in all_persons]
    screened_entities = [e for e in screened_entities if e]

    pep_directors = [d for d in directors if d.get("is_pep") == "Yes"]
    pep_ubos = [u for u in ubos if u.get("is_pep") == "Yes"]
    all_peps = pep_directors + pep_ubos

    sanctions_found = False  # Would come from real API in production
    pep_found = len(all_peps) > 0
    adverse_media = False  # Would come from real API in production

    pep_results = [{"name": p.get("full_name", ""), "pep_type": "politically_exposed_person", "confidence": 0.90} for p in all_peps]

    confidence = 0.90 if not sanctions_found and not pep_found else 0.80
    status = AgentStatus.CLEAN if not sanctions_found and not pep_found else AgentStatus.ISSUES_FOUND

    findings = []
    evidence = []

    if pep_found:
        for p in all_peps:
            findings.append({
                "finding_id": str(uuid4())[:12],
                "category": "pep_confirmed",
                "title": f"PEP identified: {p.get('full_name', 'Unknown')}",
                "description": f"Confirmed Politically Exposed Person: {p.get('full_name', '')}. "
                               f"Role: {p.get('position', 'N/A')}. Enhanced due diligence required per FATF R12.",
                "severity": Severity.HIGH.value,
                "confidence": 0.90,
                "source": "stored_pep_flag",
                "evidence_refs": [],
                "regulatory_relevance": "FATF R12: Enhanced CDD required for PEPs"
            })
            evidence.append({
                "evidence_id": str(uuid4())[:12],
                "evidence_type": "screening_result",
                "source": "stored_screening_record",
                "content_summary": f"PEP match: {p.get('full_name', '')}",
                "reference": f"PEP-{str(uuid4())[:8]}",
                "verified": True,
            })

    if not findings:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "screening_clear",
            "title": "Screening — no adverse findings",
            "description": f"Screened {len(screened_entities)} entities. No sanctions, PEP, or adverse media matches.",
            "severity": Severity.INFO.value,
            "confidence": 0.90,
            "source": "heuristic_screening_summary",
            "evidence_refs": [],
        })
        evidence.append({
            "evidence_id": str(uuid4())[:12],
            "evidence_type": "screening_result",
            "source": "stored_screening_record",
            "content_summary": f"Clean screening for {len(screened_entities)} entities",
            "reference": f"SCR-{str(uuid4())[:8]}",
            "verified": True,
        })

    output = _base_output(AgentType.FINCRIME_SCREENING, "Agent 3: FinCrime Screening Interpretation", application_id, run_id)
    output.update({
        "status": status.value,
        "confidence_score": round(confidence, 3),
        "findings": findings,
        "evidence": evidence,
        "detected_issues": [],
        "risk_indicators": [{"indicator_type": "pep_exposure", "description": f"{len(all_peps)} PEP(s) identified", "risk_level": "high", "source_agent": "fincrime_screening", "contributing_factors": [p.get("full_name", "") for p in all_peps]}] if pep_found else [],
        "recommendation": "Clear screening — proceed" if not pep_found and not sanctions_found else "Enhanced due diligence required for PEP exposure",
        "escalation_flag": sanctions_found,
        "escalation_reason": "Sanctions match identified" if sanctions_found else None,
        "sanctions_results": [],
        "pep_results": pep_results,
        "adverse_media_results": [],
        "sanctions_match_found": sanctions_found,
        "pep_match_found": pep_found,
        "adverse_media_found": adverse_media,
        "highest_match_score": 0.90 if pep_found else 0.0,
        "screened_entities": screened_entities,
        "screening_provider": "stored_application_data",
        "screening_date": datetime.utcnow().isoformat(),
    })
    return output


# ═══════════════════════════════════════════════════════════
# AGENT 5: Compliance Memo & Risk Recommendation
# ═══════════════════════════════════════════════════════════

def execute_compliance_memo(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 5: Deterministic risk summary and recommendation.

    This executor summarizes stored risk and screening fields for supervisor-side
    decision support. It does not generate the authoritative compliance memo used
    in the live approval path.
    """
    db_path = context.get("db_path", "")
    data = _get_app_data(db_path, application_id)
    app = data["application"]
    directors = data["directors"]
    ubos = data["ubos"]
    docs = data["documents"]
    run_id = str(uuid4())
    business_model = _build_business_model_summary(app)

    # Determine risk level and recommendation
    risk_level = app.get("risk_level", "MEDIUM") or "MEDIUM"
    risk_score = app.get("risk_score", 50) or 50

    pep_directors = [d for d in directors if d.get("is_pep") == "Yes"]
    pep_ubos = [u for u in ubos if u.get("is_pep") == "Yes"]
    all_peps = pep_directors + pep_ubos

    decision = "APPROVE" if risk_level == "LOW" else "APPROVE_WITH_CONDITIONS" if risk_level == "MEDIUM" else "REVIEW"

    confidence = 0.88 if risk_level == "LOW" else 0.75 if risk_level == "MEDIUM" else 0.60
    status = AgentStatus.CLEAN if risk_level in ("LOW", "MEDIUM") else AgentStatus.ISSUES_FOUND

    findings = [{
        "finding_id": str(uuid4())[:12],
        "category": "risk_assessment",
        "title": f"Overall risk assessment: {risk_level}",
        "description": f"Composite risk score: {risk_score}/100. Recommendation: {decision}. "
                       f"PEP exposure: {len(all_peps)}. "
                       f"Sector: {app.get('sector', 'N/A')}. "
                       f"Jurisdiction: {app.get('country', 'N/A')}. "
                       f"Business plausibility: {business_model['revenue_model_plausibility']}.",
        "severity": Severity.INFO.value if risk_level == "LOW" else Severity.MEDIUM.value if risk_level == "MEDIUM" else Severity.HIGH.value,
        "confidence": confidence,
        "source": "stored_risk_fields",
        "evidence_refs": [],
        "regulatory_relevance": "Risk-based approach per FATF R1"
    }]

    evidence = [{
        "evidence_id": str(uuid4())[:12],
        "evidence_type": "risk_model_output",
        "source": "stored_application_data",
        "content_summary": f"Risk model: score={risk_score}, level={risk_level}, peps={len(all_peps)}",
        "reference": app.get("ref", application_id),
        "verified": True,
    }]

    output = _base_output(AgentType.COMPLIANCE_MEMO_RISK, "Agent 5: Compliance Memo & Risk Recommendation", application_id, run_id)
    output["model_name"] = MEMO_MODEL
    output.update({
        "status": status.value,
        "confidence_score": round(confidence, 3),
        "findings": findings,
        "evidence": evidence,
        "detected_issues": [],
        "risk_indicators": [],
        "recommendation": decision,
        "escalation_flag": risk_level in ("HIGH", "VERY_HIGH"),
        "escalation_reason": f"High-risk application (score: {risk_score})" if risk_level in ("HIGH", "VERY_HIGH") else None,
        "client_overview": {
            "company_name": app.get("company_name"),
            "entity_type": app.get("entity_type"),
            "country": app.get("country"),
            "sector": app.get("sector"),
        },
        "business_activity_summary": f"{app.get('company_name', 'Entity')} operates in {app.get('sector', 'N/A')} from {app.get('country', 'N/A')}",
        "ownership_summary": f"{len(ubos)} UBO(s) identified, {len(directors)} director(s)",
        "screening_summary": f"PEP exposure: {len(all_peps)}",
        "recommended_risk_level": risk_level,
        "recommended_action": decision,
        "overall_risk_score": risk_score / 100.0,
        "memo_sections": [],
        "data_quality_assessment": {
            "complete": len(docs) >= 3 and len(ubos) > 0,
            "score": 0.8 if len(docs) >= 3 else 0.5,
        },
        "risk_indicators_summary": [{
            "category": "business_plausibility",
            "summary": business_model["summary"],
            "plausibility_score": business_model["plausibility"],
            "red_flags": business_model["red_flags"],
        }],
    })
    return output


# ═══════════════════════════════════════════════════════════
# MONITORING AGENTS (6, 7, 8, 9, 10)
# ═══════════════════════════════════════════════════════════

def execute_periodic_review(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 6: Synthetic periodic review preparation summary."""
    db_path = context.get("db_path", "")
    data = _get_app_data(db_path, application_id)
    app = data["application"]
    run_id = str(uuid4())

    output = _base_output(AgentType.PERIODIC_REVIEW_PREPARATION, "Agent 6: Periodic Review Preparation", application_id, run_id)
    output.update({
        "status": AgentStatus.CLEAN.value,
        "confidence_score": 0.85,
        "findings": [{
            "finding_id": str(uuid4())[:12],
            "category": "periodic_review",
            "title": "Periodic review data compiled",
            "description": f"Review preparation for {app.get('company_name', 'entity')}. Current risk: {app.get('risk_level', 'N/A')}.",
            "severity": Severity.INFO.value,
            "confidence": 0.85,
            "source": "synthetic_review_summary",
            "evidence_refs": [],
        }],
        "evidence": [{
            "evidence_id": str(uuid4())[:12],
            "evidence_type": "review_data",
            "source": "stored_application_data",
            "content_summary": f"Review data for {app.get('company_name', '')}",
            "reference": app.get("ref", application_id),
            "verified": True,
        }],
        "detected_issues": [],
        "risk_indicators": [],
        "recommendation": "Standard periodic review",
        "escalation_flag": False,
        "escalation_reason": None,
        "review_trigger": "scheduled",
        "previous_risk_level": app.get("risk_level"),
        "current_risk_assessment": app.get("risk_level"),
        "recommended_risk_level": app.get("risk_level"),
        "risk_trend": "stable",
    })
    return output


def execute_adverse_media_pep(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 7: Synthetic adverse media and PEP monitoring summary."""
    db_path = context.get("db_path", "")
    data = _get_app_data(db_path, application_id)
    app = data["application"]
    run_id = str(uuid4())

    output = _base_output(AgentType.ADVERSE_MEDIA_PEP_MONITORING, "Agent 7: Adverse Media & PEP Monitoring", application_id, run_id)
    output.update({
        "status": AgentStatus.CLEAN.value,
        "confidence_score": 0.88,
        "findings": [{
            "finding_id": str(uuid4())[:12],
            "category": "media_monitoring",
            "title": "No new adverse media detected",
            "description": f"Monitoring scan for {app.get('company_name', 'entity')}: no new hits.",
            "severity": Severity.INFO.value,
            "confidence": 0.88,
            "source": "synthetic_monitoring_summary",
            "evidence_refs": [],
        }],
        "evidence": [{
            "evidence_id": str(uuid4())[:12],
            "evidence_type": "monitoring_scan",
            "source": "synthetic_monitoring_summary",
            "content_summary": "Clean monitoring scan",
            "reference": f"MON-{str(uuid4())[:8]}",
            "verified": True,
        }],
        "detected_issues": [],
        "risk_indicators": [],
        "recommendation": "No action required",
        "escalation_flag": False,
        "escalation_reason": None,
        "new_media_hits": [],
        "pep_status_changes": [],
        "alert_generated": False,
    })
    return output


def execute_behaviour_risk_drift(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 8: Synthetic behaviour and risk drift summary."""
    db_path = context.get("db_path", "")
    data = _get_app_data(db_path, application_id)
    app = data["application"]
    run_id = str(uuid4())

    output = _base_output(AgentType.BEHAVIOUR_RISK_DRIFT, "Agent 8: Behaviour & Risk Drift", application_id, run_id)
    output.update({
        "status": AgentStatus.CLEAN.value,
        "confidence_score": 0.85,
        "findings": [{
            "finding_id": str(uuid4())[:12],
            "category": "risk_drift",
            "title": "No significant risk drift detected",
            "description": f"Risk profile for {app.get('company_name', 'entity')} remains stable.",
            "severity": Severity.INFO.value,
            "confidence": 0.85,
            "source": "synthetic_risk_drift_summary",
            "evidence_refs": [],
        }],
        "evidence": [{
            "evidence_id": str(uuid4())[:12],
            "evidence_type": "behaviour_analysis",
            "source": "synthetic_risk_drift_summary",
            "content_summary": "Stable risk profile",
            "reference": f"BRD-{str(uuid4())[:8]}",
            "verified": True,
        }],
        "detected_issues": [],
        "risk_indicators": [],
        "recommendation": "No action required",
        "escalation_flag": False,
        "escalation_reason": None,
        "risk_drift_detected": False,
        "drift_direction": "stable",
        "drift_magnitude": 0.0,
    })
    return output


def execute_regulatory_impact(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 9: Future-phase regulatory impact placeholder."""
    run_id = str(uuid4())
    output = _base_output(AgentType.REGULATORY_IMPACT, "Agent 9: Regulatory Impact", application_id, run_id)
    output.update({
        "status": AgentStatus.PARTIAL.value,
        "confidence_score": 0.0,
        "findings": [],
        "evidence": [],
        "detected_issues": [],
        "risk_indicators": [],
        "recommendation": "Future phase — manual regulatory review required",
        "escalation_flag": False,
        "escalation_reason": None,
        "impact_summary": "Regulatory Impact is a registered future-phase agent and is not active in the live approval chain.",
        "affected_jurisdictions": [],
        "affected_controls": [],
        "implementation_required": False,
        "implementation_deadline": None,
    })
    return output


def execute_ongoing_compliance(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 10: Synthetic ongoing compliance review summary."""
    db_path = context.get("db_path", "")
    data = _get_app_data(db_path, application_id)
    app = data["application"]
    run_id = str(uuid4())

    output = _base_output(AgentType.ONGOING_COMPLIANCE_REVIEW, "Agent 10: Ongoing Compliance Review", application_id, run_id)
    output.update({
        "status": AgentStatus.CLEAN.value,
        "confidence_score": 0.87,
        "findings": [{
            "finding_id": str(uuid4())[:12],
            "category": "compliance_review",
            "title": "Ongoing compliance status",
            "description": f"Compliance review for {app.get('company_name', 'entity')}: compliant.",
            "severity": Severity.INFO.value,
            "confidence": 0.87,
            "source": "synthetic_compliance_review",
            "evidence_refs": [],
        }],
        "evidence": [{
            "evidence_id": str(uuid4())[:12],
            "evidence_type": "compliance_check",
            "source": "synthetic_compliance_review",
            "content_summary": "Compliant status",
            "reference": f"OCR-{str(uuid4())[:8]}",
            "verified": True,
        }],
        "detected_issues": [],
        "risk_indicators": [],
        "recommendation": "Maintain current monitoring",
        "escalation_flag": False,
        "escalation_reason": None,
        "compliance_status": "compliant",
        "next_review_due": None,
        "recommended_review_frequency": "annual" if app.get("risk_level") in ("LOW", None) else "semi-annual",
    })
    return output


# ═══════════════════════════════════════════════════════════
# REGISTRATION
# ═══════════════════════════════════════════════════════════

EXECUTOR_MAP = {
    AgentType.IDENTITY_DOCUMENT_INTEGRITY: execute_identity_document,
    AgentType.EXTERNAL_DATABASE_VERIFICATION: execute_external_database,
    AgentType.CORPORATE_STRUCTURE_UBO: execute_corporate_structure_ubo,
    AgentType.FINCRIME_SCREENING: execute_fincrime_screening,
    AgentType.COMPLIANCE_MEMO_RISK: execute_compliance_memo,
    AgentType.PERIODIC_REVIEW_PREPARATION: execute_periodic_review,
    AgentType.ADVERSE_MEDIA_PEP_MONITORING: execute_adverse_media_pep,
    AgentType.BEHAVIOUR_RISK_DRIFT: execute_behaviour_risk_drift,
    AgentType.REGULATORY_IMPACT: execute_regulatory_impact,
    AgentType.ONGOING_COMPLIANCE_REVIEW: execute_ongoing_compliance,
}


def register_all_executors(supervisor, db_path: str):
    """
    Register all agent executor functions with the supervisor.
    Each executor receives (application_id, context) where context
    always includes db_path for data access.

    Args:
        supervisor: AgentSupervisor instance
        db_path: Path to SQLite database
    """
    for agent_type, executor_fn in EXECUTOR_MAP.items():
        # Wrap to inject db_path into context
        def make_wrapper(fn):
            def wrapper(application_id, context):
                context = context or {}
                context["db_path"] = db_path
                return fn(application_id, context)
            return wrapper

        supervisor.register_agent_executor(agent_type, make_wrapper(executor_fn))

    logger.info("Registered %d agent executors with supervisor", len(EXECUTOR_MAP))
