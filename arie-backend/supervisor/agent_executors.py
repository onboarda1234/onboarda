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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .schemas import AgentType, AgentStatus, Severity

# Database abstraction — lazy import to avoid polluting test DB state.
# The actual import happens inside _get_app_data() at call time.
_get_db_connection = None  # Populated lazily on first _get_app_data() call
_get_db_loaded = False

logger = logging.getLogger("arie.supervisor.executors")

# Current versions
AGENT_VERSION = "1.0.0"
PROMPT_VERSION = "v1.0-2026Q1"
# Agents 1-5 (onboarding) are deterministic/heuristic — no live AI calls.
# Agents 7-10 (monitoring) make optional Claude calls with template fallback.
# MODEL_NAME is used ONLY to tag agents that actually invoke the model.
HEURISTIC_MODEL_NAME = "heuristic-v1.0"
AI_MODEL_NAME = "claude-sonnet-4-6"
MEMO_MODEL = "claude-opus-4-6"
# Default for _base_output — overridden per agent when live AI is used
MODEL_NAME = HEURISTIC_MODEL_NAME


def _get_app_data(db_path: str, application_id: str) -> Dict[str, Any]:
    """Fetch full application data bundle from DB.

    Database-agnostic: uses the production DBConnection layer (get_db()) which
    handles both SQLite and PostgreSQL transparently. Falls back to raw
    sqlite3.connect(db_path) only when:
      - get_db() is not importable (edge-case test environments), AND
      - a valid db_path string pointing to a SQLite file is provided

    This ensures supervisor pipeline execution works on PostgreSQL staging
    while preserving backward compatibility for local SQLite dev/test.
    """
    db = None
    try:
        # Determine connection strategy:
        # 1. If db_path points to an existing SQLite file AND we're NOT on
        #    PostgreSQL (DATABASE_URL set), use it directly. This covers:
        #    - Test fixtures (temp DB files)
        #    - Local SQLite dev
        # 2. If DATABASE_URL is set (staging/production), always use get_db()
        #    which returns a PostgreSQL connection from the pool.
        # 3. Fall back to get_db() even without DATABASE_URL (standard local dev).
        import os

        has_postgres = bool(os.environ.get("DATABASE_URL"))
        use_explicit_path = (
            not has_postgres
            and db_path
            and os.path.isfile(db_path)
        )

        if use_explicit_path:
            # Test/local path: use the provided SQLite file directly
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            db = _SqliteFallback(conn)
        else:
            # Production/staging path: use get_db() for PostgreSQL (or SQLite fallback)
            global _get_db_connection, _get_db_loaded
            if not _get_db_loaded:
                import sys as _sys
                db_mod = _sys.modules.get("db")
                if db_mod and hasattr(db_mod, "get_db"):
                    _get_db_connection = db_mod.get_db
                else:
                    try:
                        from db import get_db as _gdb
                        _get_db_connection = _gdb
                    except ImportError:
                        _get_db_connection = None
                _get_db_loaded = True

            if _get_db_connection is not None:
                db = _get_db_connection()
            elif db_path:
                # Last resort: raw SQLite if get_db unavailable
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                db = _SqliteFallback(conn)
            else:
                raise RuntimeError(
                    "No database connection available: get_db() not importable and no db_path provided"
                )

        app = db.execute(
            "SELECT * FROM applications WHERE id = ? OR ref = ?",
            (application_id, application_id)
        ).fetchone()
        if not app:
            raise RuntimeError(f"Application not found: {application_id}")

        app_dict = dict(app)
        real_id = app_dict["id"]

        # C-02: decrypt PII fields on read (from neutral party_utils module)
        try:
            from party_utils import decrypt_pii_fields, PII_FIELDS_DIRECTORS, PII_FIELDS_UBOS
            directors = [decrypt_pii_fields(dict(d), PII_FIELDS_DIRECTORS) for d in db.execute(
                "SELECT * FROM directors WHERE application_id=?", (real_id,)
            ).fetchall()]
            ubos = [decrypt_pii_fields(dict(u), PII_FIELDS_UBOS) for u in db.execute(
                "SELECT * FROM ubos WHERE application_id=?", (real_id,)
            ).fetchall()]
        except (ImportError, ValueError, RuntimeError):
            directors = [dict(d) for d in db.execute(
                "SELECT * FROM directors WHERE application_id=?", (real_id,)
            ).fetchall()]
            ubos = [dict(u) for u in db.execute(
                "SELECT * FROM ubos WHERE application_id=?", (real_id,)
            ).fetchall()]

        documents = [dict(d) for d in db.execute(
            "SELECT * FROM documents WHERE application_id=?", (real_id,)
        ).fetchall()]

        # Fetch intermediary shareholders for ownership chain analysis (Agent 4)
        intermediaries = []
        try:
            intermediaries = [dict(i) for i in db.execute(
                "SELECT * FROM intermediaries WHERE application_id=?", (real_id,)
            ).fetchall()]
        except Exception:
            pass  # Table may not exist in older schemas

        return {
            "application": app_dict,
            "directors": directors,
            "ubos": ubos,
            "documents": documents,
            "intermediaries": intermediaries,
        }
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


class _SqliteFallback:
    """Minimal duck-type wrapper for raw sqlite3 connections.

    Matches the DBConnection API surface used by _get_app_data():
    execute(), fetchone(), fetchall(), close().
    Used only when the db module's get_db() is not importable (test edge cases).
    """

    def __init__(self, conn):
        self._conn = conn
        self._cursor = None

    def execute(self, sql, params=()):
        self._cursor = self._conn.execute(sql, params)
        return self

    def fetchone(self):
        row = self._cursor.fetchone() if self._cursor else None
        return dict(row) if row is not None else None

    def fetchall(self):
        rows = self._cursor.fetchall() if self._cursor else []
        return [dict(r) for r in rows]

    def close(self):
        self._conn.close()


def _base_output(agent_type: AgentType, agent_name: str, application_id: str, run_id: str, model_name: str = None) -> Dict[str, Any]:
    """Build base output fields required by AgentOutputBase.

    Args:
        model_name: Override model name. Use AI_MODEL_NAME when the agent
                    actually invoked Claude. Defaults to HEURISTIC_MODEL_NAME
                    for deterministic agents.
    """
    return {
        "agent_name": agent_name,
        "agent_type": agent_type.value,
        "agent_version": AGENT_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model_name": model_name or MODEL_NAME,
        "run_id": run_id,
        "application_id": application_id,
        "processed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
            "timestamp": d.get("uploaded_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
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

# --- Agent 2 constants (rule-based) ---
# Registry source selection based on jurisdiction (rule)
REGISTRY_SOURCES = {
    "mauritius": {"name": "Companies & Business Registration Department (CBRD)", "url": "https://companies.govmu.org"},
    "united kingdom": {"name": "Companies House", "url": "https://find-and-update.company-information.service.gov.uk"},
    "uk": {"name": "Companies House", "url": "https://find-and-update.company-information.service.gov.uk"},
    "uae": {"name": "ADGM / DIFC Registry", "url": "https://www.adgm.com"},
    "abu dhabi": {"name": "ADGM Registry", "url": "https://www.adgm.com"},
    "dubai": {"name": "DIFC Registry", "url": "https://www.difc.ae"},
    "singapore": {"name": "ACRA (Accounting and Corporate Regulatory Authority)", "url": "https://www.acra.gov.sg"},
    "hong kong": {"name": "Companies Registry", "url": "https://www.cr.gov.hk"},
    "india": {"name": "Ministry of Corporate Affairs (MCA)", "url": "https://www.mca.gov.in"},
    "south africa": {"name": "CIPC (Companies and Intellectual Property Commission)", "url": "https://www.cipc.co.za"},
    "canada": {"name": "Corporations Canada", "url": "https://www.ic.gc.ca"},
    "australia": {"name": "ASIC (Australian Securities & Investments Commission)", "url": "https://www.asic.gov.au"},
    "france": {"name": "Registre du Commerce et des Sociétés (RCS)", "url": "https://www.infogreffe.fr"},
    "germany": {"name": "Handelsregister", "url": "https://www.handelsregister.de"},
}

# Company legal form patterns for entity type matching (rule)
ENTITY_TYPE_PATTERNS = {
    "ltd": "Private Limited Company",
    "limited": "Private Limited Company",
    "plc": "Public Limited Company",
    "inc": "Corporation",
    "corp": "Corporation",
    "llc": "Limited Liability Company",
    "llp": "Limited Liability Partnership",
    "gmbh": "Gesellschaft mit beschränkter Haftung",
    "sa": "Société Anonyme",
    "sarl": "Société à responsabilité limitée",
    "bv": "Besloten Vennootschap",
    "pte": "Private Limited (Singapore)",
    "sdn bhd": "Sendirian Berhad",
}


def _select_registry_source(country: str) -> Dict[str, Any]:
    """Rule-based registry source selection based on jurisdiction."""
    country_lower = country.lower().strip()
    source = REGISTRY_SOURCES.get(country_lower)
    if source:
        return {"source": source["name"], "url": source["url"], "matched": True, "classification": "rule"}
    # Fallback: OpenCorporates covers 140+ jurisdictions
    return {
        "source": f"OpenCorporates ({country})",
        "url": "https://opencorporates.com",
        "matched": False,
        "classification": "rule",
    }


def _infer_entity_type(company_name: str) -> Optional[str]:
    """Rule-based entity type inference from company name suffix."""
    name_lower = company_name.lower().strip()
    # Check longer patterns first to avoid false matches
    for pattern in sorted(ENTITY_TYPE_PATTERNS.keys(), key=len, reverse=True):
        if name_lower.endswith(pattern) or f" {pattern} " in name_lower or f" {pattern}." in name_lower:
            return ENTITY_TYPE_PATTERNS[pattern]
    return None


def _check_registration_number_format(reg_number: str, country: str) -> Dict[str, Any]:
    """Rule-based registration number format validation."""
    if not reg_number:
        return {"valid": False, "reason": "No registration number provided", "classification": "rule"}

    reg_clean = reg_number.strip()
    country_lower = country.lower()

    # Basic format checks per jurisdiction
    if country_lower in ("united kingdom", "uk"):
        # UK companies: 8 digits or 2 letters + 6 digits
        import re
        if re.match(r'^[A-Z]{0,2}\d{6,8}$', reg_clean, re.IGNORECASE):
            return {"valid": True, "format": "UK Companies House format", "classification": "rule"}
        return {"valid": False, "reason": f"'{reg_clean}' does not match UK format (e.g. 12345678 or SC123456)", "classification": "rule"}
    elif country_lower == "mauritius":
        # Mauritius BRN format
        if len(reg_clean) >= 4:
            return {"valid": True, "format": "Mauritius BRN format", "classification": "rule"}
        return {"valid": False, "reason": f"'{reg_clean}' too short for Mauritius BRN", "classification": "rule"}
    else:
        # Generic: must be non-empty alphanumeric
        if len(reg_clean) >= 3:
            return {"valid": True, "format": "Generic registration number", "classification": "rule"}
        return {"valid": False, "reason": f"Registration number '{reg_clean}' too short", "classification": "rule"}


def _match_directors_to_registry(
    declared_directors: List[Dict],
    app: Dict[str, Any],
) -> Dict[str, Any]:
    """Rule-based director reconciliation between declared and application data.

    In degraded mode (no external API), we verify internal consistency:
    directors declared must have complete data (name, at least).
    When API is available, this will compare against registry records.
    """
    total = len(declared_directors)
    if total == 0:
        return {
            "match": False,
            "checked": 0,
            "matched": 0,
            "unmatched": 0,
            "issues": ["No directors declared"],
            "classification": "rule",
            "mode": "internal_consistency",
        }

    issues = []
    valid_count = 0
    for d in declared_directors:
        name = d.get("full_name", "").strip()
        if not name or name == "Unknown":
            issues.append(f"Director missing name (id={d.get('id', '?')})")
        else:
            valid_count += 1

    return {
        "match": valid_count == total and not issues,
        "checked": total,
        "matched": valid_count,
        "unmatched": total - valid_count,
        "issues": issues,
        "classification": "rule",
        "mode": "internal_consistency",
    }


def _check_jurisdiction_match(app_country: str, app: Dict[str, Any]) -> Dict[str, Any]:
    """Rule-based jurisdiction consistency check."""
    country = (app_country or "").strip()
    if not country:
        return {
            "match": False,
            "declared": "",
            "issue": "No jurisdiction declared",
            "classification": "rule",
        }
    return {
        "match": True,
        "declared": country,
        "issue": None,
        "classification": "rule",
    }


def execute_external_database(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 2: Rule-based external database cross-verification.

    Design philosophy:
      - Registry source selection → rule
      - Company registration lookup → rule (degraded: internal data check)
      - Registration number format → rule
      - Company status → rule
      - Entity type / legal form → rule
      - Jurisdiction match → rule
      - Director reconciliation → rule (internal consistency in degraded mode)
      - Address match → deferred to hybrid (future enhancement)

    All checks are classified. No AI calls are made.
    In degraded mode (no external API credentials), all checks run against
    internal application data and are clearly labelled as 'degraded'.
    """
    db_path = context.get("db_path", "")
    data = _get_app_data(db_path, application_id)
    app = data["application"]
    directors = data["directors"]
    ubos = data["ubos"]
    run_id = str(uuid4())

    company_name = (app.get("company_name") or "").strip()
    country = (app.get("country") or "").strip()
    reg_number = (app.get("registration_number") or "").strip()
    entity_type = (app.get("entity_type") or "").strip()

    # Check for external API availability
    import os
    has_opencorporates = bool(os.environ.get("OPENCORPORATES_API_KEY"))
    provider_mode = "live" if has_opencorporates else "degraded"

    # --- Run all rule-based checks ---
    checks = {}

    # 1. Registry source selection (rule)
    registry = _select_registry_source(country)
    checks["registry_source"] = registry

    # 2. Company existence check (rule / live when API available)
    has_required_fields = bool(company_name and country)
    oc_result = None
    if provider_mode == "live" and has_required_fields:
        # Wire OpenCorporates API via screening.lookup_opencorporates
        try:
            from screening import lookup_opencorporates
            oc_result = lookup_opencorporates(company_name, country)
            if oc_result and oc_result.get("source") not in ("simulated", "blocked"):
                company_found = oc_result.get("found", False)
                lookup_mode = "live"
            else:
                # API returned simulated/blocked — treat as degraded
                company_found = has_required_fields
                lookup_mode = "degraded"
        except Exception:
            company_found = has_required_fields
            lookup_mode = "degraded"
    else:
        company_found = has_required_fields
        lookup_mode = "degraded"

    company_lookup_data = {
        "found": company_found,
        "mode": lookup_mode,
        "company_name": company_name,
        "country": country,
        "classification": "rule",
    }
    if oc_result and lookup_mode == "live":
        top_match = (oc_result.get("companies") or [{}])[0] if oc_result.get("companies") else {}
        company_lookup_data["registry_name"] = top_match.get("name")
        company_lookup_data["registry_number"] = top_match.get("company_number")
        company_lookup_data["registry_status"] = top_match.get("status")
        company_lookup_data["registry_jurisdiction"] = top_match.get("jurisdiction")
        company_lookup_data["incorporation_date"] = top_match.get("incorporation_date")
        company_lookup_data["total_results"] = oc_result.get("total_results", 0)
        company_lookup_data["api_source"] = oc_result.get("source", "opencorporates")
    checks["company_lookup"] = company_lookup_data

    # Fire degraded-mode admin alert when running without external registry
    if lookup_mode == "degraded":
        try:
            from production_controls import alert_degraded_mode
            alert_degraded_mode(
                agent_name="External Database Verification",
                agent_number=2,
                reason="No external registry API available — internal consistency checks only",
                application_id=application_id,
            )
        except Exception:
            pass

    # 3. Registration number format (rule)
    reg_check = _check_registration_number_format(reg_number, country)
    checks["registration_number"] = reg_check

    # 4. Entity type inference and match (rule)
    inferred_type = _infer_entity_type(company_name)
    type_match = None
    if entity_type and inferred_type:
        type_match = entity_type.lower() in inferred_type.lower() or inferred_type.lower() in entity_type.lower()
    checks["entity_type"] = {
        "declared": entity_type or None,
        "inferred": inferred_type,
        "match": type_match,
        "classification": "rule",
    }

    # 5. Jurisdiction check (rule)
    jurisdiction_check = _check_jurisdiction_match(country, app)
    checks["jurisdiction"] = jurisdiction_check

    # 6. Director reconciliation (rule)
    directors_match = _match_directors_to_registry(directors, app)
    checks["directors"] = directors_match

    # --- Aggregate results ---
    findings = []
    evidence = []
    detected_issues = []
    discrepancies = []

    # Finding: company lookup result
    if company_found:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "registry_verification",
            "title": "Company data present for verification",
            "description": (
                f"Entity '{company_name}' has required identification data in {country}. "
                f"Registry source: {registry['source']}. Mode: {lookup_mode}."
            ),
            "severity": Severity.INFO.value,
            "confidence": 0.70 if lookup_mode == "degraded" else 0.90,
            "source": registry["source"],
            "evidence_refs": [],
            "classification": "rule",
            "verification_mode": lookup_mode,
        })
        evidence.append({
            "evidence_id": str(uuid4())[:12],
            "evidence_type": "registry_record",
            "source": registry["source"],
            "content_summary": f"Company '{company_name}' in {country} — {lookup_mode} verification",
            "reference": reg_number or f"REG-{country[:3].upper()}-pending",
            "verified": lookup_mode == "live",
            "classification": "rule",
        })
    else:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "company_not_found",
            "title": "Insufficient data for registry verification",
            "description": (
                f"Company name {'missing' if not company_name else repr(company_name)} "
                f"and/or jurisdiction {'missing' if not country else repr(country)} — "
                f"cannot perform registry lookup."
            ),
            "severity": Severity.HIGH.value,
            "confidence": 0.90,
            "source": "input_validation",
            "evidence_refs": [],
            "regulatory_relevance": "Entity must be identifiable in declared jurisdiction per FATF R24",
            "classification": "rule",
        })
        detected_issues.append({
            "issue_id": str(uuid4())[:12],
            "issue_type": "company_not_verifiable",
            "title": "Company cannot be verified",
            "description": "Missing company name or jurisdiction prevents registry verification.",
            "severity": Severity.HIGH.value,
            "blocking": True,
            "remediation": "Obtain complete company name and jurisdiction from applicant",
            "related_findings": [],
            "classification": "rule",
        })

    # Finding: registration number
    if not reg_check["valid"]:
        discrepancies.append({
            "field": "registration_number",
            "issue": reg_check["reason"],
            "severity": "medium" if reg_number else "high",
            "classification": "rule",
        })
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "registration_number",
            "title": "Registration number issue",
            "description": reg_check["reason"],
            "severity": Severity.MEDIUM.value if reg_number else Severity.HIGH.value,
            "confidence": 0.90,
            "source": "format_validation",
            "evidence_refs": [],
            "classification": "rule",
        })

    # Finding: director reconciliation
    if not directors_match["match"]:
        for issue in directors_match["issues"]:
            discrepancies.append({
                "field": "directors",
                "issue": issue,
                "severity": "medium",
                "classification": "rule",
            })
        if directors_match["issues"]:
            findings.append({
                "finding_id": str(uuid4())[:12],
                "category": "director_verification",
                "title": "Director data issues",
                "description": f"{len(directors_match['issues'])} issue(s): {'; '.join(directors_match['issues'][:3])}",
                "severity": Severity.MEDIUM.value,
                "confidence": 0.85,
                "source": "director_reconciliation",
                "evidence_refs": [],
                "classification": "rule",
            })

    # Confidence: degraded mode caps confidence
    if lookup_mode == "degraded":
        base_confidence = 0.65 if company_found else 0.40
    else:
        base_confidence = 0.90 if company_found else 0.60

    # Penalise for discrepancies
    confidence = max(0.30, base_confidence - len(discrepancies) * 0.05)

    # Status
    has_blocking = any(i.get("blocking") for i in detected_issues)
    status = AgentStatus.CLEAN if company_found and not discrepancies and not has_blocking else AgentStatus.ISSUES_FOUND

    # Escalation
    escalation_flag = not company_found or has_blocking or len(discrepancies) > 2
    escalation_reasons = []
    if not company_found:
        escalation_reasons.append("Company cannot be verified against registry")
    if has_blocking:
        escalation_reasons.append("Blocking issues detected")
    if len(discrepancies) > 2:
        escalation_reasons.append(f"{len(discrepancies)} discrepancies found")

    output = _base_output(AgentType.EXTERNAL_DATABASE_VERIFICATION, "Agent 2: External Database Cross-Verification", application_id, run_id)
    output.update({
        "status": status.value,
        "confidence_score": round(confidence, 3),
        "findings": findings,
        "evidence": evidence,
        "detected_issues": detected_issues,
        "risk_indicators": [],
        "recommendation": (
            f"Registry verification complete ({lookup_mode} mode)" if status == AgentStatus.CLEAN
            else f"Registry verification incomplete — {len(discrepancies)} discrepanc{'y' if len(discrepancies) == 1 else 'ies'} found"
        ),
        "escalation_flag": escalation_flag,
        "escalation_reason": "; ".join(escalation_reasons) if escalation_reasons else None,
        # Structured output fields
        "provider_mode": provider_mode,
        "lookup_mode": lookup_mode,
        "company_found": company_found,
        "registry_source": registry["source"],
        "registry_url": registry["url"],
        "registered_name": company_name if company_found else None,
        "registration_number_match": reg_check["valid"],
        "registration_number_format": reg_check,
        "entity_type_check": checks["entity_type"],
        "jurisdiction_check": checks["jurisdiction"],
        "directors_match": directors_match,
        "discrepancies": discrepancies,
        "company_status": "data_present" if company_found else "not_verifiable",
        "checks_performed": checks,
    })
    return output


# ═══════════════════════════════════════════════════════════
# AGENT 4: Corporate Structure & UBO Mapping
# ═══════════════════════════════════════════════════════════

# --- Agent 4 constants (rule-based thresholds) ---
UBO_THRESHOLD_PCT = 25.0          # FATF R24/R25: beneficial owner = ≥25% ownership
COMPLETENESS_GOOD = 0.75          # ownership ≥75% mapped → acceptable
COMPLETENESS_PARTIAL = 0.50       # ownership ≥50% mapped → partial
COMPLEXITY_UBO_COUNT = 3           # >3 UBOs → complex structure
OWNERSHIP_OVERCOUNT_PCT = 105.0    # declared >105% → arithmetic error / suspect
NOMINEE_KEYWORDS = frozenset([
    "nominee", "custodian", "trustee", "fiduciary", "designated",
    "registered holder", "on behalf of",
])
TRUST_KEYWORDS = frozenset([
    "trust", "foundation", "stiftung", "waqf", "fideicomiso",
    "settlement", "blind trust",
])
HOLDING_KEYWORDS = frozenset([
    "holdings", "holding company", "spv", "special purpose",
    "investment vehicle", "bvi", "offshore",
])


def _detect_keyword_match(text: str, keywords: frozenset) -> Optional[str]:
    """Return the first matching keyword found in text, or None."""
    text_lower = text.lower()
    for kw in keywords:
        if kw in text_lower:
            return kw
    return None


def _build_ownership_graph(ubos: List[Dict], intermediaries: List[Dict]) -> Dict[str, Any]:
    """Build a directed ownership graph and compute indirect paths.

    Returns:
        {
            "direct_owners": [{name, pct, is_ubo, ...}],
            "indirect_paths": [{path: [entity_chain], effective_pct, ...}],
            "circular_detected": bool,
            "total_direct_pct": float,
            "total_effective_pct": float,
            "layers": int,
        }
    """
    direct_owners = []
    total_direct = 0.0
    for u in ubos:
        pct = float(u.get("ownership_pct", 0) or 0)
        total_direct += pct
        direct_owners.append({
            "name": u.get("full_name", "Unknown"),
            "ownership_pct": pct,
            "nationality": u.get("nationality", "Unknown"),
            "is_pep": str(u.get("is_pep", "")).lower() in ("yes", "true", "1"),
            "person_key": u.get("person_key", ""),
            "type": "direct",
        })

    # Build indirect ownership paths through intermediaries
    indirect_paths = []
    if intermediaries:
        # intermediaries each represent an entity in the ownership chain.
        # An intermediary owns X% of the target company, and the UBOs own
        # shares of the intermediary (not tracked in detail in current schema).
        # We record the intermediary layer and flag it for review.
        for inter in intermediaries:
            inter_name = inter.get("entity_name", "Unknown")
            inter_pct = float(inter.get("ownership_pct", 0) or 0)
            inter_jurisdiction = inter.get("jurisdiction", "Unknown")
            indirect_paths.append({
                "intermediary": inter_name,
                "intermediary_jurisdiction": inter_jurisdiction,
                "intermediary_ownership_pct": inter_pct,
                "effective_pct": inter_pct,  # without UBO-through-intermediary data, effective = declared
                "path": [inter_name, "[Target Company]"],
                "classification": "rule",
            })

    # Circular ownership detection (rule-based):
    # Check if any UBO name appears as an intermediary entity name
    ubo_names_lower = {o["name"].lower().strip() for o in direct_owners if o["name"] != "Unknown"}
    inter_names_lower = {p["intermediary"].lower().strip() for p in indirect_paths}
    circular = bool(ubo_names_lower & inter_names_lower)

    layers = 1  # base: direct ownership
    if intermediaries:
        layers += 1  # at least one intermediary layer

    total_effective = total_direct + sum(p["effective_pct"] for p in indirect_paths)

    return {
        "direct_owners": direct_owners,
        "indirect_paths": indirect_paths,
        "circular_detected": circular,
        "total_direct_pct": round(total_direct, 2),
        "total_effective_pct": round(total_effective, 2),
        "layers": layers,
    }


def _detect_structure_indicators(
    ubos: List[Dict],
    intermediaries: List[Dict],
    directors: List[Dict],
    app: Dict[str, Any],
    ownership_graph: Dict[str, Any],
) -> Dict[str, Any]:
    """Detect nominee, trust, holding, and shell company indicators.

    Returns dict with:
        nominee_detected: bool
        nominee_evidence: list of matched strings
        trust_detected: bool
        trust_evidence: list
        holding_detected: bool
        holding_evidence: list
        shell_indicators: list of indicator codes
    """
    # Collect all text sources for keyword scanning
    all_names = [u.get("full_name", "") for u in ubos]
    all_names += [d.get("full_name", "") for d in directors]
    all_names += [i.get("entity_name", "") for i in intermediaries]
    ownership_text = str(app.get("ownership_structure", "") or "")

    scannable_texts = all_names + [ownership_text]

    # Nominee detection
    nominee_evidence = []
    for txt in scannable_texts:
        match = _detect_keyword_match(txt, NOMINEE_KEYWORDS)
        if match:
            nominee_evidence.append(f"{match} in '{txt[:60]}'")

    # Trust detection
    trust_evidence = []
    for txt in scannable_texts:
        match = _detect_keyword_match(txt, TRUST_KEYWORDS)
        if match:
            trust_evidence.append(f"{match} in '{txt[:60]}'")

    # Holding/SPV detection
    holding_evidence = []
    for txt in scannable_texts:
        match = _detect_keyword_match(txt, HOLDING_KEYWORDS)
        if match:
            holding_evidence.append(f"{match} in '{txt[:60]}'")

    # Shell company indicators (aggregated rule-based assessment)
    shell_indicators = []
    if nominee_evidence:
        shell_indicators.append("nominee_arrangement_detected")
    if not directors and not ubos:
        shell_indicators.append("no_officers_or_ubos")
    if not directors and ubos:
        shell_indicators.append("no_directors_declared")
    if ownership_graph["total_direct_pct"] > OWNERSHIP_OVERCOUNT_PCT:
        shell_indicators.append("ownership_exceeds_100_pct")
    if holding_evidence:
        shell_indicators.append("holding_or_spv_in_chain")

    # Opaque jurisdiction check for intermediaries
    OPAQUE_JURISDICTIONS = frozenset([
        "bvi", "british virgin islands", "cayman islands", "panama",
        "seychelles", "marshall islands", "vanuatu", "samoa",
    ])
    for inter in intermediaries:
        j = str(inter.get("jurisdiction", "")).lower()
        if any(oj in j for oj in OPAQUE_JURISDICTIONS):
            shell_indicators.append("opaque_jurisdiction_intermediary")
            break

    return {
        "nominee_detected": bool(nominee_evidence),
        "nominee_evidence": nominee_evidence,
        "trust_detected": bool(trust_evidence),
        "trust_evidence": trust_evidence,
        "holding_detected": bool(holding_evidence),
        "holding_evidence": holding_evidence,
        "shell_indicators": shell_indicators,
    }


def _compute_complexity_score(
    ubos: List[Dict],
    intermediaries: List[Dict],
    indicators: Dict[str, Any],
    ownership_graph: Dict[str, Any],
) -> Dict[str, Any]:
    """Rule-based complexity scoring (0–100 scale).

    Factors:
      - UBO count (>3 adds complexity)
      - Intermediary layers
      - Nominee/trust/holding presence
      - Circular ownership
      - Ownership completeness gaps
    """
    score = 0
    reasons = []

    # UBO count
    ubo_count = len(ubos)
    if ubo_count > COMPLEXITY_UBO_COUNT:
        score += 20
        reasons.append(f"{ubo_count} UBOs declared (>{COMPLEXITY_UBO_COUNT})")
    elif ubo_count == 0:
        score += 15
        reasons.append("No UBOs declared")

    # Intermediary layers
    inter_count = len(intermediaries)
    if inter_count > 0:
        score += min(inter_count * 10, 30)
        reasons.append(f"{inter_count} intermediary entit{'y' if inter_count == 1 else 'ies'} in chain")

    # Structure indicators
    if indicators["nominee_detected"]:
        score += 15
        reasons.append("Nominee arrangement detected")
    if indicators["trust_detected"]:
        score += 10
        reasons.append("Trust/foundation structure detected")
    if indicators["holding_detected"]:
        score += 10
        reasons.append("Holding company/SPV in chain")
    if "opaque_jurisdiction_intermediary" in indicators["shell_indicators"]:
        score += 15
        reasons.append("Opaque jurisdiction in intermediary chain")

    # Circular ownership
    if ownership_graph["circular_detected"]:
        score += 25
        reasons.append("Circular ownership detected")

    # Ownership gap
    total = ownership_graph["total_direct_pct"]
    if total < 75:
        score += 10
        reasons.append(f"Only {total:.0f}% ownership mapped")

    score = min(score, 100)

    # Tier classification
    if score >= 60:
        tier = "HIGH"
    elif score >= 30:
        tier = "MEDIUM"
    else:
        tier = "LOW"

    return {
        "score": score,
        "tier": tier,
        "reasons": reasons,
        "classification": "rule",
    }


def execute_corporate_structure_ubo(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 4: Rule-based corporate structure and UBO mapping.

    Design philosophy:
      - Direct ownership calculation → rule
      - Indirect ownership calculation → rule
      - UBO threshold determination (≥25%) → rule
      - Total ownership completeness → rule
      - Circular ownership detection → rule
      - Nominee/trust/holding detection → rule (keyword-based)
      - Opaque structure assessment → rule (jurisdiction list)
      - Complexity score → rule (weighted factors)

    All checks are classified as 'rule'. No AI calls are made.
    """
    db_path = context.get("db_path", "")
    data = _get_app_data(db_path, application_id)
    app = data["application"]
    directors = data["directors"]
    ubos = data["ubos"]
    intermediaries = data.get("intermediaries", [])
    run_id = str(uuid4())

    # 1. Build ownership graph (rule)
    ownership_graph = _build_ownership_graph(ubos, intermediaries)

    # 2. Detect structure indicators (rule)
    indicators = _detect_structure_indicators(ubos, intermediaries, directors, app, ownership_graph)

    # 3. Compute complexity score (rule)
    complexity = _compute_complexity_score(ubos, intermediaries, indicators, ownership_graph)

    # 4. UBO threshold analysis (rule): which declared owners qualify as UBOs
    ubo_list = []
    for owner in ownership_graph["direct_owners"]:
        qualifies = owner["ownership_pct"] >= UBO_THRESHOLD_PCT
        ubo_list.append({
            "name": owner["name"],
            "ownership_pct": owner["ownership_pct"],
            "nationality": owner["nationality"],
            "is_pep": owner["is_pep"],
            "qualifies_as_ubo": qualifies,
            "type": owner["type"],
            "classification": "rule",
        })

    # 5. Completeness assessment (rule)
    total_direct = ownership_graph["total_direct_pct"]
    ubo_completeness = min(total_direct / 100.0, 1.0) if ubos else 0.0
    qualified_ubo_count = sum(1 for u in ubo_list if u["qualifies_as_ubo"])

    # 6. Confidence calculation (rule-based tiers)
    is_complex = complexity["tier"] in ("HIGH", "MEDIUM")
    has_shell = bool(indicators["shell_indicators"])
    if ubo_completeness > COMPLETENESS_GOOD and not has_shell and not is_complex:
        confidence = 0.90
    elif ubo_completeness > COMPLETENESS_GOOD and not has_shell:
        confidence = 0.80
    elif ubo_completeness > COMPLETENESS_PARTIAL:
        confidence = 0.65
    else:
        confidence = 0.45

    # 7. Status determination (rule)
    clean = (
        ubo_completeness > COMPLETENESS_GOOD
        and not has_shell
        and not ownership_graph["circular_detected"]
        and qualified_ubo_count > 0
    )
    status = AgentStatus.CLEAN if clean else AgentStatus.ISSUES_FOUND

    # 8. Build findings
    findings = []
    findings.append({
        "finding_id": str(uuid4())[:12],
        "category": "ubo_mapping",
        "title": "UBO Mapping Complete" if ubo_completeness > COMPLETENESS_GOOD else "UBO Mapping Incomplete",
        "description": (
            f"Mapped {len(ubos)} declared beneficial owner(s) covering {total_direct:.1f}% direct ownership. "
            f"{qualified_ubo_count} meet the ≥{UBO_THRESHOLD_PCT:.0f}% UBO threshold. "
            f"Complexity: {complexity['tier']} (score {complexity['score']}/100)."
        ),
        "severity": Severity.INFO.value if clean else Severity.HIGH.value,
        "confidence": confidence,
        "source": "deterministic_ubo_mapping",
        "evidence_refs": [],
        "regulatory_relevance": f"FATF R24/R25 require identification of all beneficial owners ≥{UBO_THRESHOLD_PCT:.0f}%",
        "classification": "rule",
    })

    if ownership_graph["circular_detected"]:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "circular_ownership",
            "title": "Circular ownership detected",
            "description": "One or more UBOs appear in the intermediary chain, indicating potential circular ownership.",
            "severity": Severity.CRITICAL.value,
            "confidence": 0.95,
            "source": "deterministic_ubo_mapping",
            "evidence_refs": [],
            "regulatory_relevance": "Circular ownership structures require enhanced due diligence",
            "classification": "rule",
        })

    if indicators["nominee_detected"]:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "nominee_arrangement",
            "title": "Nominee arrangement detected",
            "description": f"Nominee indicators found: {'; '.join(indicators['nominee_evidence'][:3])}.",
            "severity": Severity.HIGH.value,
            "confidence": 0.85,
            "source": "deterministic_ubo_mapping",
            "evidence_refs": [],
            "regulatory_relevance": "Nominee arrangements require identification of the underlying beneficial owner",
            "classification": "rule",
        })

    if indicators["trust_detected"]:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "trust_structure",
            "title": "Trust or foundation structure detected",
            "description": f"Trust/foundation indicators found: {'; '.join(indicators['trust_evidence'][:3])}.",
            "severity": Severity.MEDIUM.value,
            "confidence": 0.80,
            "source": "deterministic_ubo_mapping",
            "evidence_refs": [],
            "regulatory_relevance": "FATF R25 requires CDD on legal arrangements including trusts",
            "classification": "rule",
        })

    if ownership_graph["total_direct_pct"] > OWNERSHIP_OVERCOUNT_PCT:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "ownership_arithmetic",
            "title": "Declared ownership exceeds 100%",
            "description": f"Total declared direct ownership is {ownership_graph['total_direct_pct']:.1f}%. This indicates data entry error or overlapping claims.",
            "severity": Severity.HIGH.value,
            "confidence": 0.95,
            "source": "deterministic_ubo_mapping",
            "evidence_refs": [],
            "regulatory_relevance": "Ownership data must be arithmetically consistent",
            "classification": "rule",
        })

    # 9. Evidence
    evidence = [{
        "evidence_id": str(uuid4())[:12],
        "evidence_type": "corporate_record",
        "source": "application_data",
        "content_summary": (
            f"Corporate structure: {len(directors)} directors, {len(ubos)} UBOs, "
            f"{len(intermediaries)} intermediaries. "
            f"Complexity: {complexity['tier']} ({complexity['score']}/100)."
        ),
        "reference": app.get("ref", application_id),
        "verified": True,
        "classification": "rule",
    }]

    # 10. Detected issues (blocking)
    detected_issues = []
    if ubo_completeness < COMPLETENESS_GOOD:
        detected_issues.append({
            "issue_id": str(uuid4())[:12],
            "issue_type": "incomplete_ownership",
            "title": "Ownership not fully mapped",
            "description": f"Only {total_direct:.1f}% of direct ownership has been identified (threshold: {COMPLETENESS_GOOD * 100:.0f}%).",
            "severity": Severity.HIGH.value,
            "blocking": True,
            "remediation": "Request additional ownership documentation or shareholder register",
            "related_findings": [],
            "classification": "rule",
        })
    if not ubos:
        detected_issues.append({
            "issue_id": str(uuid4())[:12],
            "issue_type": "ubo_not_identified",
            "title": "No UBOs identified",
            "description": "No beneficial owners have been declared for this entity.",
            "severity": Severity.CRITICAL.value,
            "blocking": True,
            "remediation": "UBO identification is mandatory under FATF R24/R25",
            "related_findings": [],
            "classification": "rule",
        })
    if ownership_graph["circular_detected"]:
        detected_issues.append({
            "issue_id": str(uuid4())[:12],
            "issue_type": "circular_ownership",
            "title": "Circular ownership requires investigation",
            "description": "A UBO appears in the intermediary chain — possible circular or self-referencing structure.",
            "severity": Severity.CRITICAL.value,
            "blocking": True,
            "remediation": "Investigate ownership chain and obtain clarification from applicant",
            "related_findings": [],
            "classification": "rule",
        })

    # 11. Risk indicators
    risk_indicators = []
    for ind in indicators["shell_indicators"]:
        risk_indicators.append({
            "indicator_type": ind,
            "description": ind.replace("_", " ").title(),
            "risk_level": "high",
            "source_agent": "corporate_structure_ubo",
            "contributing_factors": [],
            "classification": "rule",
        })

    # 12. Escalation logic (rule)
    escalation_flag = (
        not ubos
        or has_shell
        or ownership_graph["circular_detected"]
        or complexity["tier"] == "HIGH"
    )
    escalation_reasons = []
    if not ubos:
        escalation_reasons.append("No UBOs identified")
    if has_shell:
        escalation_reasons.append(f"Shell indicators: {', '.join(indicators['shell_indicators'])}")
    if ownership_graph["circular_detected"]:
        escalation_reasons.append("Circular ownership detected")
    if complexity["tier"] == "HIGH":
        escalation_reasons.append(f"High complexity score ({complexity['score']}/100)")

    output = _base_output(AgentType.CORPORATE_STRUCTURE_UBO, "Agent 4: Corporate Structure & UBO Mapping", application_id, run_id)
    output.update({
        "status": status.value,
        "confidence_score": round(confidence, 3),
        "findings": findings,
        "evidence": evidence,
        "detected_issues": detected_issues,
        "risk_indicators": risk_indicators,
        "recommendation": "Ownership structure verified" if clean else "UBO identification requires further investigation",
        "escalation_flag": escalation_flag,
        "escalation_reason": "; ".join(escalation_reasons) if escalation_reasons else None,
        # Structured output fields
        "ownership_structure": {
            "layers": ownership_graph["layers"],
            "complexity": complexity["tier"].lower(),
            "complexity_score": complexity["score"],
            "complexity_reasons": complexity["reasons"],
        },
        "ubos_identified": ubo_list,
        "ubo_completeness": round(ubo_completeness, 3),
        "complex_structure_flag": complexity["tier"] in ("HIGH", "MEDIUM"),
        "shell_company_indicators": indicators["shell_indicators"],
        "circular_ownership_detected": ownership_graph["circular_detected"],
        "nominee_arrangements_detected": indicators["nominee_detected"],
        "trust_structures_detected": indicators["trust_detected"],
        "holding_structures_detected": indicators["holding_detected"],
        "indirect_ownership_paths": ownership_graph["indirect_paths"],
        "total_ownership_mapped_pct": round(total_direct, 1),
        "qualified_ubo_count": qualified_ubo_count,
        "ubo_threshold_pct": UBO_THRESHOLD_PCT,
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
#
# Workbook alignment: Onboarda_Agent_2_10_Operating_Model.xlsx
# 11 checks: 4 rule-based, 4 hybrid, 3 AI
#
# This executor reads stored screening results from prescreening_data
# (populated by run_full_screening() during prescreening). It does NOT
# re-run live screening — it interprets already-captured results.
#
# If no screening report exists in prescreening_data, the executor runs
# in degraded mode using only stored PEP flags from director/UBO records.


# ── Severity policy matrix (deterministic) ──────────────────
# Used for check #7: severity ranking of confirmed hits.
_SEVERITY_MATRIX = {
    "sanctions":      {"base": "CRITICAL", "rank": 4},
    "pep":            {"base": "HIGH",     "rank": 3},
    "adverse_media":  {"base": "MEDIUM",   "rank": 2},
    "watchlist":      {"base": "MEDIUM",   "rank": 2},
}

# Confidence threshold for auto-clear vs gray-zone (check #4 & #5)
_EXACT_MATCH_THRESHOLD = 85.0    # above → confirmed match
_NEAR_MATCH_LOWER = 50.0         # below → auto-clear as likely FP
_FP_CONFIDENCE_THRESHOLD = 60.0  # below this → classified as false positive


def _extract_screening_report(app: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract the screening_report from prescreening_data JSON."""
    raw = app.get("prescreening_data", "{}")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    elif isinstance(raw, dict):
        parsed = raw
    else:
        return None
    return parsed.get("screening_report")


def _retrieve_sanctions_hits(screening_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Check #1 (rule): Extract sanctions hits from screening report."""
    hits = []
    for section_key in ("director_screenings", "ubo_screenings"):
        for person in screening_report.get(section_key, []):
            scr = person.get("screening", {})
            for result in scr.get("results", []):
                if result.get("is_sanctioned"):
                    hits.append({
                        "person_name": person.get("person_name", ""),
                        "person_type": person.get("person_type", "unknown"),
                        "matched_name": result.get("matched_name", ""),
                        "match_score": result.get("match_score", 0),
                        "sanctions_list": result.get("sanctions_list", ""),
                        "countries": result.get("countries", []),
                        "source": "screening_report",
                        "classification": "rule",
                    })
    company_scr = screening_report.get("company_screening", {}).get("sanctions", {})
    for result in company_scr.get("results", []):
        if result.get("is_sanctioned"):
            hits.append({
                "person_name": screening_report.get("company_screening", {}).get("name", "Company"),
                "person_type": "company",
                "matched_name": result.get("matched_name", ""),
                "match_score": result.get("match_score", 0),
                "sanctions_list": result.get("sanctions_list", ""),
                "countries": result.get("countries", []),
                "source": "screening_report",
                "classification": "rule",
            })
    return hits


def _retrieve_pep_hits(
    screening_report: Optional[Dict[str, Any]],
    directors: List[Dict[str, Any]],
    ubos: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Check #2 (rule): Consolidate PEP hits from screening report + stored flags."""
    hits = []
    seen_names = set()

    # From screening report
    if screening_report:
        for section_key in ("director_screenings", "ubo_screenings"):
            for person in screening_report.get(section_key, []):
                scr = person.get("screening", {})
                for result in scr.get("results", []):
                    if result.get("is_pep"):
                        name = person.get("person_name", "")
                        seen_names.add(name.lower())
                        hits.append({
                            "person_name": name,
                            "person_type": person.get("person_type", "unknown"),
                            "matched_name": result.get("matched_name", ""),
                            "match_score": result.get("match_score", 0),
                            "topics": result.get("topics", []),
                            "countries": result.get("countries", []),
                            "source": "screening_report",
                            "undeclared": person.get("undeclared_pep", False),
                            "classification": "rule",
                        })

    # From stored PEP flags (fallback / supplement)
    for person_list, ptype in [(directors, "director"), (ubos, "ubo")]:
        for p in person_list:
            if p.get("is_pep") == "Yes":
                name = p.get("full_name", "")
                if name.lower() not in seen_names:
                    seen_names.add(name.lower())
                    hits.append({
                        "person_name": name,
                        "person_type": ptype,
                        "matched_name": name,
                        "match_score": 100.0,
                        "topics": ["pep"],
                        "countries": [],
                        "source": "stored_pep_flag",
                        "undeclared": False,
                        "classification": "rule",
                    })
    return hits


def _retrieve_adverse_media_hits(screening_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Check #3 (rule): Extract adverse media hits from screening report."""
    hits = []
    for section_key in ("director_screenings", "ubo_screenings"):
        for person in screening_report.get(section_key, []):
            scr = person.get("screening", {})
            for result in scr.get("results", []):
                topics = [t.lower() for t in result.get("topics", [])]
                if not result.get("is_sanctioned") and not result.get("is_pep") and topics:
                    hits.append({
                        "person_name": person.get("person_name", ""),
                        "person_type": person.get("person_type", "unknown"),
                        "matched_name": result.get("matched_name", ""),
                        "match_score": result.get("match_score", 0),
                        "topics": result.get("topics", []),
                        "countries": result.get("countries", []),
                        "source": "screening_report",
                        "classification": "rule",
                    })
    return hits


def _exact_identity_disambiguation(all_hits: List[Dict[str, Any]], directors: List[Dict[str, Any]], ubos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Check #4 (rule): Auto-clear or confirm hits based on strong identifiers."""
    known_names = set()
    for p in directors + ubos:
        name = (p.get("full_name") or "").strip().lower()
        if name:
            known_names.add(name)

    disambiguated = []
    for hit in all_hits:
        score = hit.get("match_score", 0)
        matched = (hit.get("matched_name") or "").strip().lower()
        person = (hit.get("person_name") or "").strip().lower()

        if score >= _EXACT_MATCH_THRESHOLD and matched and person and matched == person:
            hit["disambiguation"] = "confirmed_exact"
            hit["disambiguation_method"] = "rule"
        elif score < _NEAR_MATCH_LOWER:
            hit["disambiguation"] = "auto_cleared"
            hit["disambiguation_method"] = "rule"
        else:
            hit["disambiguation"] = "requires_review"
            hit["disambiguation_method"] = "pending"
        disambiguated.append(hit)
    return disambiguated


def _near_match_identity_disambiguation(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Check #5 (hybrid): Score gray-zone matches using bounded interpretation."""
    for hit in hits:
        if hit.get("disambiguation") != "requires_review":
            continue
        score = hit.get("match_score", 0)
        # Deterministic scoring first
        name_match = (hit.get("matched_name") or "").lower() == (hit.get("person_name") or "").lower()
        if name_match and score >= 70:
            hit["disambiguation"] = "probable_match"
            hit["near_match_confidence"] = round(score / 100, 2)
        elif score >= 60:
            hit["disambiguation"] = "gray_zone"
            hit["near_match_confidence"] = round(score / 100, 2)
        else:
            hit["disambiguation"] = "probable_fp"
            hit["near_match_confidence"] = round(score / 100, 2)
        hit["disambiguation_method"] = "hybrid"
    return hits


def _false_positive_reduction(hits: List[Dict[str, Any]]) -> tuple:
    """Check #6 (hybrid): Classify hits as confirmed, likely FP, or gray-zone."""
    confirmed = []
    false_positives = []
    gray_zone = []
    for hit in hits:
        d = hit.get("disambiguation", "")
        score = hit.get("match_score", 0)
        if d in ("confirmed_exact", "probable_match"):
            confirmed.append(hit)
        elif d == "auto_cleared" or score < _FP_CONFIDENCE_THRESHOLD:
            hit["fp_reason"] = "below_confidence_threshold" if score < _FP_CONFIDENCE_THRESHOLD else "auto_cleared_low_score"
            false_positives.append(hit)
        else:
            gray_zone.append(hit)
    return confirmed, false_positives, gray_zone


def _severity_ranking(confirmed_hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Check #7 (hybrid): Rank confirmed hits by policy severity matrix."""
    ranked = []
    for hit in confirmed_hits:
        if hit.get("is_sanctioned") or "sanctions" in str(hit.get("topics", [])).lower():
            severity_info = _SEVERITY_MATRIX["sanctions"]
        elif hit.get("is_pep") or "pep" in str(hit.get("topics", [])).lower():
            severity_info = _SEVERITY_MATRIX["pep"]
        else:
            severity_info = _SEVERITY_MATRIX.get("adverse_media", {"base": "LOW", "rank": 1})
        hit["severity"] = severity_info["base"]
        hit["severity_rank"] = severity_info["rank"]
        hit["classification"] = "hybrid"
        ranked.append(hit)
    ranked.sort(key=lambda h: h.get("severity_rank", 0), reverse=True)
    return ranked


def _adverse_media_ai_assessment(media_hits: List[Dict[str, Any]], app: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Checks #8 & #9 (AI): Assess relevance and materiality of adverse media.

    Attempts to call interpret_fincrime_screening() for AI assessment.
    Falls back to template output if Claude is unavailable or no hits.
    """
    if not media_hits:
        return []

    assessed = []
    ai_available = False
    try:
        import os as _os
        from claude_client import ClaudeClient
        api_key = _os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            client = ClaudeClient(api_key=api_key)
            ai_available = True
    except (ImportError, ValueError, RuntimeError):
        pass

    for hit in media_hits:
        if ai_available:
            try:
                screening_input = {
                    "results": [hit],
                    "total_hits": 1,
                    "source": hit.get("source", "screening_report"),
                }
                ai_result = client.interpret_fincrime_screening(
                    screening_results=screening_input,
                    person_name=hit.get("person_name", ""),
                    entity_type=hit.get("person_type", "individual"),
                )
                hit["relevance_assessment"] = ai_result.get("recommendation", "requires_manual_review")
                hit["materiality_note"] = ai_result.get("reasoning", "")
                hit["ai_confidence"] = ai_result.get("confirmed_hits", 0) > 0
                hit["assessment_source"] = "ai"
            except Exception:
                hit["relevance_assessment"] = "requires_manual_review"
                hit["materiality_note"] = "AI assessment unavailable — manual review required"
                hit["assessment_source"] = "fallback"
        else:
            hit["relevance_assessment"] = "requires_manual_review"
            hit["materiality_note"] = "AI assessment unavailable — manual review required"
            hit["assessment_source"] = "fallback"
        hit["classification"] = "ai"
        assessed.append(hit)
    return assessed


def _consolidated_screening_narrative(
    sanctions_hits: List[Dict[str, Any]],
    pep_hits: List[Dict[str, Any]],
    media_hits: List[Dict[str, Any]],
    confirmed: List[Dict[str, Any]],
    false_positives: List[Dict[str, Any]],
    gray_zone: List[Dict[str, Any]],
    screened_entities: List[str],
    degraded: bool,
) -> str:
    """Check #10 (AI): Generate consolidated screening narrative."""
    parts = []
    parts.append(f"Screening interpretation completed for {len(screened_entities)} entities.")

    if degraded:
        parts.append("NOTE: Running in degraded mode — no screening report available. "
                      "Results based on stored PEP declarations only.")

    total_raw = len(sanctions_hits) + len(pep_hits) + len(media_hits)
    parts.append(f"Total raw hits: {total_raw}. "
                 f"Confirmed: {len(confirmed)}. "
                 f"False positives: {len(false_positives)}. "
                 f"Gray zone (requires review): {len(gray_zone)}.")

    if sanctions_hits:
        names = ", ".join(set(h.get("person_name", "") for h in sanctions_hits))
        parts.append(f"SANCTIONS: {len(sanctions_hits)} hit(s) involving {names}. "
                     "Immediate escalation required per AML/CFT policy.")
    if pep_hits:
        names = ", ".join(set(h.get("person_name", "") for h in pep_hits))
        undeclared = [h for h in pep_hits if h.get("undeclared")]
        parts.append(f"PEP: {len(pep_hits)} hit(s) involving {names}. "
                     "Enhanced due diligence required per FATF R12.")
        if undeclared:
            parts.append(f"WARNING: {len(undeclared)} undeclared PEP(s) detected.")
    if media_hits:
        parts.append(f"ADVERSE MEDIA: {len(media_hits)} hit(s). Review for relevance and materiality.")

    if not total_raw:
        parts.append("No sanctions, PEP, or adverse media matches found. Screening clear.")

    return " ".join(parts)


def _screening_disposition(
    sanctions_hits: List[Dict[str, Any]],
    confirmed: List[Dict[str, Any]],
    gray_zone: List[Dict[str, Any]],
    pep_hits: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Check #11 (hybrid): Policy-led disposition routing."""
    if sanctions_hits:
        return {
            "disposition": "REJECT",
            "reason": "Sanctions match identified — automatic rejection per AML policy",
            "requires_human_review": True,
            "classification": "hybrid",
        }
    if any(h.get("severity") == "CRITICAL" for h in confirmed):
        return {
            "disposition": "ESCALATE",
            "reason": "Critical severity hit confirmed — escalation to MLRO required",
            "requires_human_review": True,
            "classification": "hybrid",
        }
    if gray_zone:
        return {
            "disposition": "ESCALATE",
            "reason": f"{len(gray_zone)} gray-zone hit(s) require manual review",
            "requires_human_review": True,
            "classification": "hybrid",
        }
    if pep_hits:
        return {
            "disposition": "EDD_REQUIRED",
            "reason": f"{len(pep_hits)} PEP hit(s) — enhanced due diligence required",
            "requires_human_review": True,
            "classification": "hybrid",
        }
    return {
        "disposition": "CLEAR",
        "reason": "No confirmed adverse findings — proceed with standard onboarding",
        "requires_human_review": False,
        "classification": "hybrid",
    }


def execute_fincrime_screening(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 3: FinCrime Screening Interpretation.

    Workbook: 11 checks — 4 rule, 4 hybrid, 3 AI.
    Reads stored screening results from prescreening_data. Does NOT re-run
    live screening. Falls back to degraded mode if no screening report exists.
    """
    db_path = context.get("db_path", "")
    data = _get_app_data(db_path, application_id)
    app = data["application"]
    directors = data["directors"]
    ubos = data["ubos"]
    run_id = str(uuid4())

    all_persons = directors + ubos
    screened_entities = [app.get("company_name", "")] + [p.get("full_name", "") for p in all_persons]
    screened_entities = [e for e in screened_entities if e]

    # ── Extract screening report from prescreening_data ──
    screening_report = _extract_screening_report(app)
    degraded = screening_report is None

    # ── Check #1 (rule): Sanctions hit retrieval ──
    sanctions_hits = _retrieve_sanctions_hits(screening_report) if screening_report else []

    # ── Check #2 (rule): PEP hit retrieval ──
    pep_hits = _retrieve_pep_hits(screening_report, directors, ubos)

    # ── Check #3 (rule): Adverse media hit retrieval ──
    media_hits = _retrieve_adverse_media_hits(screening_report) if screening_report else []

    # ── Check #4 (rule): Exact identity disambiguation ──
    all_hits = sanctions_hits + pep_hits + media_hits
    all_hits = _exact_identity_disambiguation(all_hits, directors, ubos)

    # ── Check #5 (hybrid): Near-match identity disambiguation ──
    all_hits = _near_match_identity_disambiguation(all_hits)

    # ── Check #6 (hybrid): False-positive reduction ──
    confirmed, false_positives, gray_zone = _false_positive_reduction(all_hits)

    # ── Check #7 (hybrid): Severity ranking ──
    ranked_confirmed = _severity_ranking(confirmed)

    # ── Checks #8 & #9 (AI): Adverse media relevance & materiality ──
    media_confirmed = [h for h in confirmed if not h.get("is_sanctioned") and not h.get("is_pep")
                       and "pep" not in str(h.get("topics", [])).lower()]
    assessed_media = _adverse_media_ai_assessment(media_confirmed, app)

    # ── Check #10 (AI): Consolidated screening narrative ──
    narrative = _consolidated_screening_narrative(
        sanctions_hits, pep_hits, media_hits,
        confirmed, false_positives, gray_zone,
        screened_entities, degraded,
    )

    # ── Check #11 (hybrid): Recommended screening disposition ──
    disposition = _screening_disposition(sanctions_hits, confirmed, gray_zone, pep_hits)

    # ── Build output ──
    sanctions_found = len(sanctions_hits) > 0
    pep_found = len(pep_hits) > 0
    adverse_media_found = len(media_hits) > 0
    highest_score = max((h.get("match_score", 0) for h in all_hits), default=0.0)

    if sanctions_found:
        confidence = 0.70
        status = AgentStatus.ISSUES_FOUND
    elif pep_found or gray_zone:
        confidence = 0.80
        status = AgentStatus.ISSUES_FOUND
    else:
        confidence = 0.95
        status = AgentStatus.CLEAN

    findings = []
    evidence = []

    for hit in ranked_confirmed:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": f"{hit.get('severity', 'MEDIUM').lower()}_match_confirmed",
            "title": f"Confirmed: {hit.get('person_name', 'Unknown')} ({hit.get('severity', 'MEDIUM')})",
            "description": f"Matched {hit.get('matched_name', '')} on {hit.get('sanctions_list', 'screening list')}. "
                           f"Score: {hit.get('match_score', 0)}%. Disposition: {disposition.get('disposition', 'REVIEW')}.",
            "severity": hit.get("severity", Severity.MEDIUM.value),
            "confidence": round(hit.get("match_score", 0) / 100, 2),
            "source": hit.get("source", "screening_report"),
            "evidence_refs": [],
            "regulatory_relevance": "FATF R12" if hit.get("is_pep") or "pep" in str(hit.get("topics", [])).lower() else "AML/CFT Act s.17",
            "classification": hit.get("classification", "rule"),
        })
        evidence.append({
            "evidence_id": str(uuid4())[:12],
            "evidence_type": "screening_result",
            "source": hit.get("source", "screening_report"),
            "content_summary": f"Match: {hit.get('person_name', '')} → {hit.get('matched_name', '')} ({hit.get('match_score', 0)}%)",
            "reference": f"SCR-{str(uuid4())[:8]}",
            "verified": True,
        })

    if not findings:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "screening_clear",
            "title": "Screening — no adverse findings",
            "description": f"Screened {len(screened_entities)} entities. {narrative}",
            "severity": Severity.INFO.value,
            "confidence": 0.95,
            "source": "screening_interpretation",
            "evidence_refs": [],
            "classification": "rule",
        })

    checks_performed = [
        {"check": "Sanctions hit retrieval", "classification": "rule", "result": f"{len(sanctions_hits)} hit(s)"},
        {"check": "PEP hit retrieval", "classification": "rule", "result": f"{len(pep_hits)} hit(s)"},
        {"check": "Adverse media hit retrieval", "classification": "rule", "result": f"{len(media_hits)} hit(s)"},
        {"check": "Exact identity disambiguation", "classification": "rule", "result": f"{len([h for h in all_hits if h.get('disambiguation_method') == 'rule'])} resolved"},
        {"check": "Near-match identity disambiguation", "classification": "hybrid", "result": f"{len([h for h in all_hits if h.get('disambiguation_method') == 'hybrid'])} scored"},
        {"check": "False-positive reduction", "classification": "hybrid", "result": f"{len(false_positives)} FP(s), {len(gray_zone)} gray-zone"},
        {"check": "Severity ranking of confirmed hits", "classification": "hybrid", "result": f"{len(ranked_confirmed)} ranked"},
        {"check": "Adverse media relevance assessment", "classification": "ai", "result": f"{len(assessed_media)} assessed"},
        {"check": "Adverse media materiality / seriousness", "classification": "ai", "result": f"{len(assessed_media)} assessed"},
        {"check": "Consolidated screening narrative", "classification": "ai", "result": "generated"},
        {"check": "Recommended screening disposition", "classification": "hybrid", "result": disposition.get("disposition", "UNKNOWN")},
    ]

    pep_results = [{"name": h.get("person_name", ""), "pep_type": "politically_exposed_person",
                     "confidence": round(h.get("match_score", 0) / 100, 2),
                     "undeclared": h.get("undeclared", False)} for h in pep_hits]

    output = _base_output(AgentType.FINCRIME_SCREENING, "Agent 3: FinCrime Screening Interpretation", application_id, run_id)
    output.update({
        "status": status.value,
        "confidence_score": round(confidence, 3),
        "findings": findings,
        "evidence": evidence,
        "detected_issues": [],
        "risk_indicators": [{"indicator_type": "pep_exposure", "description": f"{len(pep_hits)} PEP(s) identified",
                             "risk_level": "high", "source_agent": "fincrime_screening",
                             "contributing_factors": [h.get("person_name", "") for h in pep_hits]}] if pep_found else [],
        "recommendation": disposition.get("reason", ""),
        "escalation_flag": sanctions_found or disposition.get("disposition") in ("REJECT", "ESCALATE"),
        "escalation_reason": disposition.get("reason") if disposition.get("disposition") in ("REJECT", "ESCALATE") else None,
        "sanctions_results": sanctions_hits,
        "pep_results": pep_results,
        "adverse_media_results": assessed_media or media_hits,
        "sanctions_match_found": sanctions_found,
        "pep_match_found": pep_found,
        "adverse_media_found": adverse_media_found,
        "highest_match_score": round(highest_score, 2),
        "screened_entities": screened_entities,
        "screening_provider": "prescreening_report" if not degraded else "stored_application_data",
        "screening_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "screening_mode": "full" if not degraded else "degraded",
        "checks_performed": checks_performed,
        "confirmed_hits": ranked_confirmed,
        "false_positives_cleared": len(false_positives),
        "gray_zone_count": len(gray_zone),
        "disposition": disposition,
        "narrative": narrative,
        "false_positive_assessment": {
            "total_raw_hits": len(all_hits),
            "confirmed": len(confirmed),
            "cleared_as_fp": len(false_positives),
            "gray_zone": len(gray_zone),
            "classification": "hybrid",
        },
    })
    return output


# ═══════════════════════════════════════════════════════════
# AGENT 5: Compliance Memo & Risk Recommendation
# ═══════════════════════════════════════════════════════════

# Risk tier mapping for divergence cross-check
_RISK_TIER_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4}


def _classify_memo_sections(memo: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Tag each memo section with its classification (rule/hybrid/ai).

    Based on the Onboarda Agent 2-10 Operating Model:
    - Rule-based: completeness, scoring, thresholds, escalation triggers
    - Hybrid: business-vs-sector alignment, transaction-vs-scale
    - AI: plausibility, memo narrative drafting (currently template-based)
    """
    SECTION_CLASSIFICATIONS = {
        "executive_summary": "hybrid",
        "client_overview": "rule",
        "ownership_and_control": "rule",
        "risk_assessment": "rule",
        "screening_results": "rule",
        "document_verification": "rule",
        "ai_explainability": "rule",
        "red_flags_and_mitigants": "hybrid",
        "compliance_decision": "rule",
        "ongoing_monitoring": "rule",
        "audit_and_governance": "rule",
    }
    sections = memo.get("sections", {})
    tagged = []
    for key, content in sections.items():
        tagged.append({
            "section_key": key,
            "classification": SECTION_CLASSIFICATIONS.get(key, "rule"),
            "content": content,
        })
    return tagged


def _compute_risk_divergence(
    stored_risk_level: str,
    stored_risk_score: float,
    memo_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """Cross-check stored D1-D5 risk vs memo-handler aggregated risk.

    Flags divergence if the two models disagree by more than 1 tier.
    """
    memo_risk = memo_metadata.get("aggregated_risk") or memo_metadata.get("risk_rating", "")
    stored_tier = _RISK_TIER_RANK.get(str(stored_risk_level).upper(), 0)
    memo_tier = _RISK_TIER_RANK.get(str(memo_risk).upper(), 0)

    if stored_tier == 0 or memo_tier == 0:
        return {
            "divergence_detected": False,
            "stored_risk_level": stored_risk_level,
            "memo_aggregated_risk": memo_risk,
            "tier_gap": 0,
            "note": "Unable to compare — one or both risk levels missing",
            "classification": "rule",
        }

    gap = abs(stored_tier - memo_tier)
    return {
        "divergence_detected": gap > 1,
        "stored_risk_level": stored_risk_level,
        "stored_risk_score": stored_risk_score,
        "memo_aggregated_risk": memo_risk,
        "memo_weighted_score": memo_metadata.get("weighted_risk_score"),
        "tier_gap": gap,
        "note": (
            f"Risk models diverge by {gap} tier(s): stored={stored_risk_level}, memo={memo_risk}"
            if gap > 1 else "Risk models aligned (within 1 tier)"
        ),
        "classification": "rule",
    }


def execute_compliance_memo(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 5: Unified compliance memo and risk recommendation.

    Bridges the supervisor executor to the authoritative memo path
    (build_compliance_memo in memo_handler.py), which enforces Rules 4A-4E,
    computes 7 risk dimensions, and generates the full 11-section memo.

    Design philosophy (per operating model):
      Rule-based (10 tasks):
        - document completeness, jurisdiction/sector/product/channel scoring,
          ownership complexity ingestion, screening severity ingestion,
          weighted total risk score, risk tier bucket, mandatory escalation triggers
      Hybrid (3 tasks):
        - business description vs sector alignment,
          transaction profile vs scale, recommendation narrative (policy-led)
      AI (3 tasks — currently template-based, tagged for future upgrade):
        - revenue model plausibility, business model plausibility, memo drafting

    All outputs include classification tags. No direct Claude API calls.
    """
    db_path = context.get("db_path", "")
    data = _get_app_data(db_path, application_id)
    app = data["application"]
    directors = data["directors"]
    ubos = data["ubos"]
    docs = data["documents"]
    run_id = str(uuid4())

    # Stored risk from D1-D5 model (computed at prescreening/KYC time)
    stored_risk_level = app.get("risk_level", "MEDIUM") or "MEDIUM"
    stored_risk_score = float(app.get("risk_score", 50) or 50)

    # --- Bridge to authoritative memo path ---
    memo = None
    rule_engine_result = None
    supervisor_result = None
    validation_result = None
    memo_source = "unified"

    try:
        from memo_handler import build_compliance_memo
        memo, rule_engine_result, supervisor_result, validation_result = build_compliance_memo(
            app, directors, ubos, docs
        )
    except Exception as e:
        logger.warning("Agent 5: build_compliance_memo failed (%s), falling back to summary mode", e)
        memo = None
        memo_source = "fallback"

    # --- Extract from memo if available, else fall back to stored fields ---
    if memo and memo.get("metadata"):
        meta = memo["metadata"]
        risk_level = meta.get("aggregated_risk") or meta.get("risk_rating") or stored_risk_level
        risk_score_raw = meta.get("risk_score", stored_risk_score)
        risk_score = float(risk_score_raw) if risk_score_raw else stored_risk_score
        decision = meta.get("approval_recommendation", "REVIEW")
        confidence_pct = meta.get("confidence_level", 70)
        confidence = max(0.30, min(1.0, confidence_pct / 100.0))
    else:
        risk_level = stored_risk_level
        risk_score = stored_risk_score
        decision = "APPROVE" if risk_level == "LOW" else "APPROVE_WITH_CONDITIONS" if risk_level == "MEDIUM" else "REVIEW"
        confidence = 0.88 if risk_level == "LOW" else 0.75 if risk_level == "MEDIUM" else 0.60

    status = AgentStatus.CLEAN if risk_level in ("LOW", "MEDIUM") else AgentStatus.ISSUES_FOUND

    # --- Build findings ---
    pep_directors = [d for d in directors if d.get("is_pep") == "Yes"]
    pep_ubos = [u for u in ubos if u.get("is_pep") == "Yes"]
    all_peps = pep_directors + pep_ubos
    business_model = _build_business_model_summary(app)

    findings = [{
        "finding_id": str(uuid4())[:12],
        "category": "risk_assessment",
        "title": f"Overall risk assessment: {risk_level}",
        "description": (
            f"Composite risk score: {risk_score}/100. Recommendation: {decision}. "
            f"PEP exposure: {len(all_peps)}. "
            f"Sector: {app.get('sector', 'N/A')}. "
            f"Jurisdiction: {app.get('country', 'N/A')}. "
            f"Business plausibility: {business_model['revenue_model_plausibility']}."
        ),
        "severity": Severity.INFO.value if risk_level == "LOW" else Severity.MEDIUM.value if risk_level == "MEDIUM" else Severity.HIGH.value,
        "confidence": confidence,
        "source": "memo_handler" if memo else "stored_risk_fields",
        "evidence_refs": [],
        "regulatory_relevance": "Risk-based approach per FATF R1",
        "classification": "rule",
    }]

    # Add rule enforcement findings from memo
    if rule_engine_result:
        enforcements = rule_engine_result.get("enforcements", [])
        for enf in enforcements[:5]:
            findings.append({
                "finding_id": str(uuid4())[:12],
                "category": "rule_enforcement",
                "title": f"Rule enforced: {enf.get('rule', 'unknown')}",
                "description": enf.get("detail", str(enf)),
                "severity": Severity.HIGH.value,
                "confidence": 0.95,
                "source": "rule_engine",
                "evidence_refs": [],
                "classification": "rule",
            })

    # Add divergence finding if models disagree
    divergence = _compute_risk_divergence(
        stored_risk_level, stored_risk_score,
        memo.get("metadata", {}) if memo else {},
    )
    if divergence["divergence_detected"]:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "risk_model_divergence",
            "title": "Risk model divergence detected",
            "description": divergence["note"],
            "severity": Severity.HIGH.value,
            "confidence": 0.95,
            "source": "cross_check",
            "evidence_refs": [],
            "classification": "rule",
        })

    evidence = [{
        "evidence_id": str(uuid4())[:12],
        "evidence_type": "risk_model_output",
        "source": "memo_handler" if memo else "stored_application_data",
        "content_summary": f"Risk model: score={risk_score}, level={risk_level}, peps={len(all_peps)}, source={memo_source}",
        "reference": app.get("ref", application_id),
        "verified": True,
        "classification": "rule",
    }]

    # Detected issues from memo
    detected_issues = []
    if memo and memo.get("metadata"):
        meta = memo["metadata"]
        if meta.get("low_confidence_flag"):
            detected_issues.append({
                "issue_id": str(uuid4())[:12],
                "issue_type": "low_confidence",
                "title": "Low model confidence",
                "description": f"Model confidence is {meta.get('confidence_level', 0)}% — below 70% threshold (Rule 4E)",
                "severity": Severity.HIGH.value,
                "blocking": False,
                "remediation": "Review data quality and completeness",
                "related_findings": [],
                "classification": "rule",
            })
        if not meta.get("documentation_complete"):
            detected_issues.append({
                "issue_id": str(uuid4())[:12],
                "issue_type": "incomplete_documentation",
                "title": "Documentation incomplete",
                "description": f"{meta.get('pending_document_count', 0)} document(s) pending verification",
                "severity": Severity.MEDIUM.value,
                "blocking": False,
                "remediation": "Complete document verification before approval",
                "related_findings": [],
                "classification": "rule",
            })

    # --- Classify memo sections ---
    memo_sections = _classify_memo_sections(memo) if memo else []

    # --- Risk dimensions from memo ---
    risk_dimensions = {}
    if memo and memo.get("metadata", {}).get("risk_dimensions"):
        risk_dimensions = memo["metadata"]["risk_dimensions"]

    # --- Escalation ---
    escalation_flag = risk_level in ("HIGH", "VERY_HIGH") or divergence["divergence_detected"]
    escalation_reasons = []
    if risk_level in ("HIGH", "VERY_HIGH"):
        escalation_reasons.append(f"Risk level {risk_level} (score: {risk_score})")
    if divergence["divergence_detected"]:
        escalation_reasons.append(divergence["note"])

    # --- Data quality ---
    if memo and memo.get("metadata"):
        meta = memo["metadata"]
        dq_score = (meta.get("verified_document_count", 0) / max(meta.get("document_count", 1), 1))
        data_quality = {
            "complete": meta.get("documentation_complete", False),
            "score": round(dq_score, 2),
            "document_count": meta.get("document_count", 0),
            "verified_count": meta.get("verified_document_count", 0),
            "pending_count": meta.get("pending_document_count", 0),
            "classification": "rule",
        }
    else:
        data_quality = {
            "complete": len(docs) >= 3 and len(ubos) > 0,
            "score": 0.8 if len(docs) >= 3 else 0.5,
            "classification": "rule",
        }

    output = _base_output(AgentType.COMPLIANCE_MEMO_RISK, "Agent 5: Compliance Memo & Risk Recommendation", application_id, run_id)
    output["model_name"] = MEMO_MODEL
    output.update({
        "status": status.value,
        "confidence_score": round(confidence, 3),
        "findings": findings,
        "evidence": evidence,
        "detected_issues": detected_issues,
        "risk_indicators": [],
        "recommendation": decision,
        "escalation_flag": escalation_flag,
        "escalation_reason": "; ".join(escalation_reasons) if escalation_reasons else None,
        # Backward-compatible fields (consumed by contradictions detector, etc.)
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
        "overall_risk_score": min(risk_score / 100.0, 1.0),
        "memo_sections": memo_sections,
        "data_quality_assessment": data_quality,
        "risk_indicators_summary": [{
            "category": "business_plausibility",
            "summary": business_model["summary"],
            "plausibility_score": business_model["plausibility"],
            "red_flags": business_model["red_flags"],
            "classification": "ai",  # tagged for future AI upgrade
        }],
        # New unified fields
        "memo_source": memo_source,
        "risk_model_divergence": divergence,
        "risk_dimensions": risk_dimensions,
        "rule_enforcements": rule_engine_result if rule_engine_result else {},
        "validation_result": {
            "quality_score": validation_result.get("quality_score") if validation_result else None,
            "validation_status": validation_result.get("validation_status") if validation_result else None,
        } if validation_result else None,
        "supervisor_verdict": {
            "verdict": supervisor_result.get("verdict") if supervisor_result else None,
            "supervisor_confidence": supervisor_result.get("supervisor_confidence") if supervisor_result else None,
        } if supervisor_result else None,
    })
    return output


# ═══════════════════════════════════════════════════════════
# MONITORING AGENTS (6, 7, 8, 9, 10)
# ═══════════════════════════════════════════════════════════

# ── Monitoring data helper ──────────────────────────────────

def _get_monitoring_data(db_path: str, application_id: str) -> Dict[str, Any]:
    """Fetch monitoring-specific data (alerts, reviews) for an application.

    Returns empty lists when tables don't exist (test/minimal DB).
    """
    db = None
    try:
        import os
        has_postgres = bool(os.environ.get("DATABASE_URL"))
        use_explicit_path = not has_postgres and db_path and os.path.isfile(db_path)

        if use_explicit_path:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            db = _SqliteFallback(conn)
        else:
            global _get_db_connection, _get_db_loaded
            if not _get_db_loaded:
                import sys as _sys
                db_mod = _sys.modules.get("db")
                if db_mod and hasattr(db_mod, "get_db"):
                    _get_db_connection = db_mod.get_db
                else:
                    try:
                        from db import get_db as _gdb
                        _get_db_connection = _gdb
                    except ImportError:
                        _get_db_connection = None
                _get_db_loaded = True

            if _get_db_connection is not None:
                db = _get_db_connection()
            elif db_path:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                db = _SqliteFallback(conn)
            else:
                return {"alerts": [], "reviews": [], "agent_runs": []}

        alerts = []
        try:
            alerts = [dict(r) for r in db.execute(
                "SELECT * FROM monitoring_alerts WHERE application_id=? ORDER BY created_at DESC",
                (application_id,)
            ).fetchall()]
        except Exception:
            pass

        reviews = []
        try:
            reviews = [dict(r) for r in db.execute(
                "SELECT * FROM periodic_reviews WHERE application_id=? ORDER BY created_at DESC",
                (application_id,)
            ).fetchall()]
        except Exception:
            pass

        agent_runs = []
        try:
            agent_runs = [dict(r) for r in db.execute(
                "SELECT * FROM monitoring_agent_status ORDER BY last_run DESC"
            ).fetchall()]
        except Exception:
            pass

        return {"alerts": alerts, "reviews": reviews, "agent_runs": agent_runs}
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def _get_transaction_data(db_path: str, application_id: str) -> List[Dict]:
    """Fetch transaction records for an application from the transactions table.

    Returns an empty list when the table does not exist or contains no rows.
    This function supports Agent 8 (Behaviour & Risk Drift Detection).
    """
    db = None
    try:
        import os
        has_postgres = bool(os.environ.get("DATABASE_URL"))
        use_explicit_path = not has_postgres and db_path and os.path.isfile(db_path)

        if use_explicit_path:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            db = _SqliteFallback(conn)
        else:
            global _get_db_connection, _get_db_loaded
            if not _get_db_loaded:
                import sys as _sys
                db_mod = _sys.modules.get("db")
                if db_mod and hasattr(db_mod, "get_db"):
                    _get_db_connection = db_mod.get_db
                else:
                    try:
                        from db import get_db as _gdb
                        _get_db_connection = _gdb
                    except ImportError:
                        _get_db_connection = None
                _get_db_loaded = True

            if _get_db_connection is not None:
                db = _get_db_connection()
            elif db_path:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                db = _SqliteFallback(conn)
            else:
                return []

        txns = []
        try:
            txns = [dict(r) for r in db.execute(
                "SELECT * FROM transactions WHERE application_id=? ORDER BY transaction_date DESC",
                (application_id,)
            ).fetchall()]
        except Exception:
            pass

        return txns
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


# ── Review schedule thresholds ──────────────────────────────

_REVIEW_FREQUENCY_MAP = {
    "LOW": 365,        # annual
    "MEDIUM": 180,     # semi-annual
    "HIGH": 90,        # quarterly
    "VERY_HIGH": 30,   # monthly
}

_SCREENING_STALENESS_DAYS = 180  # screening older than 6 months = stale

_DOCUMENT_EXPIRY_WARN_DAYS = 90   # warn when doc expires within 90 days

# ── Priority scoring weights ────────────────────────────────

_PRIORITY_WEIGHTS = {
    "overdue_review": 25,
    "risk_level_change": 20,
    "expired_documents": 15,
    "ownership_change": 15,
    "stale_screening": 10,
    "outstanding_alerts": 10,
    "regulatory_gaps": 5,
}


# ═══════════════════════════════════════════════════════════
# AGENT 6: Periodic Review Preparation (10 checks: 8R + 2H)
# ═══════════════════════════════════════════════════════════

def _check_review_schedule(app: Dict, reviews: List[Dict]) -> Dict[str, Any]:
    """Check #1 (rule): Review schedule compliance check."""
    risk_level = app.get("risk_level", "MEDIUM")
    freq_days = _REVIEW_FREQUENCY_MAP.get(risk_level, 365)

    if not reviews:
        return {
            "check": "Review schedule compliance check",
            "classification": "rule",
            "status": "no_prior_review",
            "review_frequency_days": freq_days,
            "days_since_last_review": None,
            "schedule_status": "no_history",
        }

    last_review = reviews[0]
    last_date_str = last_review.get("completed_at") or last_review.get("created_at")
    if not last_date_str:
        return {
            "check": "Review schedule compliance check",
            "classification": "rule",
            "status": "unknown",
            "review_frequency_days": freq_days,
            "days_since_last_review": None,
            "schedule_status": "no_date_available",
        }

    try:
        last_date = datetime.fromisoformat(str(last_date_str).replace("Z", "+00:00").replace("+00:00", ""))
        days_since = (datetime.now(timezone.utc).replace(tzinfo=None) - last_date).days
    except (ValueError, TypeError):
        days_since = None

    if days_since is None:
        schedule_status = "unknown"
    elif days_since > freq_days:
        schedule_status = "overdue"
    elif days_since > freq_days - 30:
        schedule_status = "upcoming"
    else:
        schedule_status = "on_schedule"

    return {
        "check": "Review schedule compliance check",
        "classification": "rule",
        "status": "completed",
        "review_frequency_days": freq_days,
        "days_since_last_review": days_since,
        "schedule_status": schedule_status,
    }


def _check_risk_level_change(app: Dict, reviews: List[Dict]) -> Dict[str, Any]:
    """Check #2 (rule): Risk level change detection."""
    current_risk = app.get("risk_level")
    if not reviews:
        return {
            "check": "Risk level change detection",
            "classification": "rule",
            "status": "no_prior_review",
            "current_risk_level": current_risk,
            "previous_risk_level": None,
            "changed": False,
        }
    previous_risk = reviews[0].get("risk_level") or reviews[0].get("previous_risk_level")
    changed = bool(current_risk and previous_risk and current_risk != previous_risk)
    return {
        "check": "Risk level change detection",
        "classification": "rule",
        "status": "completed",
        "current_risk_level": current_risk,
        "previous_risk_level": previous_risk,
        "changed": changed,
    }


def _check_document_expiry(documents: List[Dict]) -> Dict[str, Any]:
    """Check #3 (rule): Document expiry scan."""
    expired = []
    expiring_soon = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for doc in documents:
        expiry_str = doc.get("expiry_date") or doc.get("valid_until")
        if not expiry_str:
            continue
        try:
            expiry = datetime.fromisoformat(str(expiry_str).replace("Z", "+00:00").replace("+00:00", ""))
            days_to_expiry = (expiry - now).days
            entry = {
                "document_id": doc.get("id"),
                "document_type": doc.get("document_type"),
                "filename": doc.get("filename"),
                "expiry_date": str(expiry_str),
                "days_to_expiry": days_to_expiry,
            }
            if days_to_expiry < 0:
                expired.append(entry)
            elif days_to_expiry <= _DOCUMENT_EXPIRY_WARN_DAYS:
                expiring_soon.append(entry)
        except (ValueError, TypeError):
            continue

    return {
        "check": "Document expiry scan",
        "classification": "rule",
        "status": "completed",
        "expired_count": len(expired),
        "expiring_soon_count": len(expiring_soon),
        "expired_documents": expired,
        "expiring_soon_documents": expiring_soon,
    }


def _check_ownership_changes(app: Dict, ubos: List[Dict], intermediaries: List[Dict]) -> Dict[str, Any]:
    """Check #4 (rule): Ownership structure change detection.

    Compares stored ownership_structure JSON against current UBOs/intermediaries.
    Without historical snapshots, flags structural indicators.
    """
    changes = []
    ownership_str = app.get("ownership_structure") or "{}"
    try:
        ownership = json.loads(ownership_str) if isinstance(ownership_str, str) else (ownership_str or {})
    except (json.JSONDecodeError, TypeError):
        ownership = {}

    # Flag if intermediaries exist (possible layered structure)
    if intermediaries:
        changes.append({
            "type": "intermediary_presence",
            "detail": f"{len(intermediaries)} intermediary shareholder(s) in structure",
        })

    # Flag if any UBO has borderline ownership (near 25% threshold)
    for ubo in ubos:
        pct = ubo.get("ownership_pct") or 0
        try:
            pct = float(pct)
        except (ValueError, TypeError):
            pct = 0
        if 20 <= pct <= 30:
            changes.append({
                "type": "borderline_ubo_threshold",
                "detail": f"UBO {ubo.get('full_name', 'Unknown')} at {pct}% (near 25% threshold)",
            })

    return {
        "check": "Ownership structure change detection",
        "classification": "rule",
        "status": "completed",
        "changes_detected": len(changes),
        "changes": changes,
    }


def _check_screening_staleness(app: Dict) -> Dict[str, Any]:
    """Check #5 (rule): Screening data staleness check."""
    prescreening_raw = app.get("prescreening_data") or "{}"
    try:
        prescreening = json.loads(prescreening_raw) if isinstance(prescreening_raw, str) else (prescreening_raw or {})
    except (json.JSONDecodeError, TypeError):
        prescreening = {}

    screening_report = prescreening.get("screening_report", {})
    if not screening_report:
        return {
            "check": "Screening data staleness check",
            "classification": "rule",
            "status": "no_screening_data",
            "staleness_days": None,
            "is_stale": True,
        }

    screened_at = screening_report.get("screened_at") or screening_report.get("timestamp")
    if not screened_at:
        return {
            "check": "Screening data staleness check",
            "classification": "rule",
            "status": "no_timestamp",
            "staleness_days": None,
            "is_stale": True,
        }

    try:
        screening_date = datetime.fromisoformat(str(screened_at).replace("Z", "+00:00").replace("+00:00", ""))
        days = (datetime.now(timezone.utc).replace(tzinfo=None) - screening_date).days
    except (ValueError, TypeError):
        days = None

    is_stale = days is None or days > _SCREENING_STALENESS_DAYS

    return {
        "check": "Screening data staleness check",
        "classification": "rule",
        "status": "completed",
        "staleness_days": days,
        "is_stale": is_stale,
    }


def _check_activity_volume(app: Dict) -> Dict[str, Any]:
    """Check #6 (rule): Activity volume comparison.

    Compares declared expected_volume against any stored actual metrics.
    Degraded mode: reports declared volume only (no actuals available).
    """
    expected = app.get("expected_volume") or app.get("monthly_volume")
    return {
        "check": "Activity volume comparison",
        "classification": "rule",
        "status": "degraded" if not expected else "completed",
        "expected_volume": expected,
        "actual_volume": None,  # No transaction table yet — degraded mode
        "deviation_pct": None,
        "data_available": False,
    }


def _check_outstanding_alerts(alerts: List[Dict]) -> Dict[str, Any]:
    """Check #7 (rule): Outstanding alert aggregation."""
    open_alerts = [a for a in alerts if a.get("status") in ("open", "pending", None)]
    by_severity = {}
    for a in open_alerts:
        sev = a.get("severity", "unknown")
        by_severity[sev] = by_severity.get(sev, 0) + 1

    return {
        "check": "Outstanding alert aggregation",
        "classification": "rule",
        "status": "completed",
        "total_open_alerts": len(open_alerts),
        "by_severity": by_severity,
        "alerts": [{
            "alert_id": a.get("id"),
            "alert_type": a.get("alert_type"),
            "severity": a.get("severity"),
            "summary": a.get("summary"),
        } for a in open_alerts[:10]],  # cap at 10 for output size
    }


def _check_regulatory_completeness(app: Dict, documents: List[Dict]) -> Dict[str, Any]:
    """Check #8 (rule): Regulatory requirement completeness.

    Checks that minimum document set is present for the entity's jurisdiction/type.
    """
    required_types = {"passport", "poa", "cert_inc"}  # minimum universal set
    entity_type = (app.get("entity_type") or "").lower()
    if "company" in entity_type or "ltd" in entity_type or "corp" in entity_type:
        required_types.update({"reg_sh", "reg_dir", "board_res"})

    present_types = {d.get("document_type") for d in documents if d.get("document_type")}
    missing = required_types - present_types
    completeness_pct = ((len(required_types) - len(missing)) / max(len(required_types), 1)) * 100

    return {
        "check": "Regulatory requirement completeness",
        "classification": "rule",
        "status": "completed",
        "required_document_types": sorted(required_types),
        "present_document_types": sorted(present_types),
        "missing_document_types": sorted(missing),
        "completeness_pct": round(completeness_pct, 1),
    }


def _compute_review_priority(check_results: List[Dict]) -> Dict[str, Any]:
    """Check #9 (hybrid): Review priority scoring.

    Weighted scoring across all rule-check outputs to produce a 0-100 priority.
    """
    score = 0.0

    for cr in check_results:
        check_name = cr.get("check", "")
        if "schedule" in check_name.lower():
            if cr.get("schedule_status") == "overdue":
                score += _PRIORITY_WEIGHTS["overdue_review"]
            elif cr.get("schedule_status") == "upcoming":
                score += _PRIORITY_WEIGHTS["overdue_review"] * 0.5
        elif "risk level" in check_name.lower():
            if cr.get("changed"):
                score += _PRIORITY_WEIGHTS["risk_level_change"]
        elif "expiry" in check_name.lower():
            expired_ct = cr.get("expired_count", 0)
            if expired_ct > 0:
                score += min(expired_ct * 5, _PRIORITY_WEIGHTS["expired_documents"])
        elif "ownership" in check_name.lower():
            if cr.get("changes_detected", 0) > 0:
                score += _PRIORITY_WEIGHTS["ownership_change"]
        elif "staleness" in check_name.lower():
            if cr.get("is_stale"):
                score += _PRIORITY_WEIGHTS["stale_screening"]
        elif "alert" in check_name.lower():
            open_ct = cr.get("total_open_alerts", 0)
            if open_ct > 0:
                score += min(open_ct * 3, _PRIORITY_WEIGHTS["outstanding_alerts"])
        elif "regulatory" in check_name.lower():
            completeness = cr.get("completeness_pct", 100)
            if completeness < 100:
                score += _PRIORITY_WEIGHTS["regulatory_gaps"]

    score = min(score, 100.0)
    if score >= 70:
        priority_label = "high"
    elif score >= 40:
        priority_label = "medium"
    else:
        priority_label = "low"

    return {
        "check": "Review priority scoring",
        "classification": "hybrid",
        "status": "completed",
        "priority_score": round(score, 1),
        "priority_label": priority_label,
    }


def _assemble_review_package(app: Dict, check_results: List[Dict], priority_result: Dict) -> Dict[str, Any]:
    """Check #10 (hybrid): Review package assembly."""
    issues = []
    for cr in check_results:
        if cr.get("schedule_status") == "overdue":
            issues.append("Review is overdue")
        if cr.get("changed"):
            issues.append(f"Risk level changed: {cr.get('previous_risk_level')} → {cr.get('current_risk_level')}")
        if cr.get("expired_count", 0) > 0:
            issues.append(f"{cr['expired_count']} expired document(s)")
        if cr.get("changes_detected", 0) > 0:
            issues.append(f"{cr['changes_detected']} ownership structure change(s)")
        if cr.get("is_stale"):
            issues.append("Screening data is stale")
        if cr.get("total_open_alerts", 0) > 0:
            issues.append(f"{cr['total_open_alerts']} outstanding alert(s)")
        missing = cr.get("missing_document_types", [])
        if missing:
            issues.append(f"Missing documents: {', '.join(missing)}")

    return {
        "check": "Review package assembly",
        "classification": "hybrid",
        "status": "completed",
        "company_name": app.get("company_name"),
        "risk_level": app.get("risk_level"),
        "priority_score": priority_result.get("priority_score"),
        "priority_label": priority_result.get("priority_label"),
        "issues_requiring_attention": issues,
        "total_issues": len(issues),
    }


def execute_periodic_review(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 6: Periodic Review Preparation — 10 checks (8 rule + 2 hybrid).

    Scans document expiry, ownership changes, screening staleness, outstanding
    alerts; assembles review package with priority score.
    """
    db_path = context.get("db_path", "")
    data = _get_app_data(db_path, application_id)
    app = data["application"]
    documents = data["documents"]
    ubos = data["ubos"]
    intermediaries = data.get("intermediaries", [])
    monitoring = _get_monitoring_data(db_path, application_id)
    alerts = monitoring["alerts"]
    reviews = monitoring["reviews"]
    run_id = str(uuid4())

    # Run 8 rule checks
    c1 = _check_review_schedule(app, reviews)
    c2 = _check_risk_level_change(app, reviews)
    c3 = _check_document_expiry(documents)
    c4 = _check_ownership_changes(app, ubos, intermediaries)
    c5 = _check_screening_staleness(app)
    c6 = _check_activity_volume(app)
    c7 = _check_outstanding_alerts(alerts)
    c8 = _check_regulatory_completeness(app, documents)
    rule_checks = [c1, c2, c3, c4, c5, c6, c7, c8]

    # 2 hybrid checks
    c9 = _compute_review_priority(rule_checks)
    c10 = _assemble_review_package(app, rule_checks, c9)
    all_checks = rule_checks + [c9, c10]

    # Determine overall status and risk trend
    priority_score = c9.get("priority_score", 0)
    risk_changed = c2.get("changed", False)
    previous_risk = c2.get("previous_risk_level")
    current_risk = app.get("risk_level")

    if risk_changed and previous_risk and current_risk:
        risk_order = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4}
        prev_val = risk_order.get(previous_risk, 2)
        curr_val = risk_order.get(current_risk, 2)
        risk_trend = "increasing" if curr_val > prev_val else ("decreasing" if curr_val < prev_val else "stable")
    else:
        risk_trend = "stable"

    if priority_score >= 70:
        overall_status = AgentStatus.ISSUES_FOUND.value
    elif priority_score >= 40:
        overall_status = AgentStatus.INCONCLUSIVE.value
    else:
        overall_status = AgentStatus.CLEAN.value

    escalation_flag = priority_score >= 70
    findings = []
    detected_issues = []
    for issue in c10.get("issues_requiring_attention", []):
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "periodic_review",
            "title": issue,
            "description": issue,
            "severity": Severity.HIGH.value if priority_score >= 70 else Severity.MEDIUM.value,
            "confidence": 0.85,
            "source": "rule_check",
            "evidence_refs": [],
        })
        detected_issues.append(issue)

    if not findings:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "periodic_review",
            "title": "Review preparation complete — no issues detected",
            "description": f"Review preparation for {app.get('company_name', 'entity')}. No urgent issues found.",
            "severity": Severity.INFO.value,
            "confidence": 0.90,
            "source": "rule_check",
            "evidence_refs": [],
        })

    output = _base_output(AgentType.PERIODIC_REVIEW_PREPARATION, "Agent 6: Periodic Review Preparation", application_id, run_id)
    output.update({
        "status": overall_status,
        "confidence_score": 0.85,
        "findings": findings,
        "evidence": [{
            "evidence_id": str(uuid4())[:12],
            "evidence_type": "review_data",
            "source": "stored_application_data",
            "content_summary": f"Review data for {app.get('company_name', '')} — {len(all_checks)} checks performed",
            "reference": app.get("ref", application_id),
            "verified": True,
        }],
        "detected_issues": detected_issues,
        "risk_indicators": [],
        "recommendation": c9.get("priority_label", "low") + " priority review",
        "escalation_flag": escalation_flag,
        "escalation_reason": "High priority review — multiple issues detected" if escalation_flag else None,
        "review_trigger": "scheduled",
        "previous_risk_level": previous_risk,
        "current_risk_assessment": current_risk,
        "recommended_risk_level": current_risk,
        "risk_trend": risk_trend,
        "checks_performed": all_checks,
        "review_schedule_status": c1.get("schedule_status"),
        "risk_level_changed": risk_changed,
        "expired_documents": c3.get("expired_documents", []),
        "ownership_changes_detected": c4.get("changes", []),
        "screening_staleness_days": c5.get("staleness_days"),
        "activity_volume_comparison": c6,
        "outstanding_alerts": c7.get("alerts", []),
        "regulatory_completeness": c8,
        "priority_score": priority_score,
        "review_package": c10,
    })
    return output


# ═══════════════════════════════════════════════════════════
# AGENT 8: Behaviour & Risk Drift (11 checks: 6R + 5H)
# ═══════════════════════════════════════════════════════════

# Thresholds
_VOLUME_DEVIATION_WARN = 0.30   # 30% deviation from baseline
_VOLUME_DEVIATION_ALERT = 0.50  # 50% deviation
_DORMANCY_DAYS = 180            # 6 months without activity = dormant
_CONCENTRATION_THRESHOLD = 0.60 # 60% to single counterparty = concentrated


def _check_volume_baseline(app: Dict, txns: List[Dict] = None) -> Dict[str, Any]:
    """Check #1 (rule): Transaction volume baseline comparison.

    When transaction data is available, compares actual monthly volume against
    the declared expected_volume/monthly_volume.  Falls back to degraded mode
    when no transaction records exist.
    """
    expected = app.get("expected_volume") or app.get("monthly_volume")
    txns = txns or []

    if not txns:
        return {
            "check": "Transaction volume baseline comparison",
            "classification": "rule",
            "status": "degraded",
            "mode": "no_transaction_data",
            "declared_volume": expected,
            "actual_volume": None,
            "deviation_pct": None,
            "breach": False,
        }

    # Compute actual volume from transaction amounts
    actual_volume = sum(float(t.get("amount", 0)) for t in txns)
    deviation_pct = None
    breach = False
    if expected:
        try:
            exp_val = float(expected)
            if exp_val > 0:
                deviation_pct = round(((actual_volume - exp_val) / exp_val) * 100, 1)
                breach = abs(deviation_pct) > 50  # >50% deviation = breach
        except (ValueError, TypeError):
            pass

    return {
        "check": "Transaction volume baseline comparison",
        "classification": "rule",
        "status": "completed",
        "mode": "live",
        "declared_volume": expected,
        "actual_volume": round(actual_volume, 2),
        "transaction_count": len(txns),
        "deviation_pct": deviation_pct,
        "breach": breach,
    }


def _check_geographic_deviation(app: Dict, txns: List[Dict] = None) -> Dict[str, Any]:
    """Check #2 (rule): Geographic activity deviation.

    Compares declared country/jurisdiction against transaction counterparty
    countries.  Falls back to degraded mode when no transaction data exists.
    """
    country = app.get("country")
    txns = txns or []

    if not txns:
        return {
            "check": "Geographic activity deviation",
            "classification": "rule",
            "status": "degraded",
            "mode": "no_transaction_data",
            "declared_country": country,
            "detected_countries": [],
            "unexpected_countries": [],
            "deviation_detected": False,
        }

    detected_countries = list({
        t.get("counterparty_country")
        for t in txns
        if t.get("counterparty_country")
    })
    declared_upper = (country or "").upper()
    unexpected = [c for c in detected_countries if c.upper() != declared_upper]

    return {
        "check": "Geographic activity deviation",
        "classification": "rule",
        "status": "completed",
        "mode": "live",
        "declared_country": country,
        "detected_countries": detected_countries,
        "unexpected_countries": unexpected,
        "deviation_detected": len(unexpected) > 0,
    }


def _check_counterparty_concentration(app: Dict, txns: List[Dict] = None) -> Dict[str, Any]:
    """Check #3 (rule): Counterparty concentration check.

    When transaction data is available, computes the share of total volume
    going to the top counterparty.  Falls back to degraded mode otherwise.
    """
    txns = txns or []

    if not txns:
        return {
            "check": "Counterparty concentration check",
            "classification": "rule",
            "status": "degraded",
            "mode": "no_transaction_data",
            "top_counterparties": [],
            "concentration_ratio": None,
            "concentrated": False,
        }

    from collections import Counter
    ctr = Counter()
    for t in txns:
        cp = t.get("counterparty_name") or "unknown"
        ctr[cp] += float(t.get("amount", 0))

    total = sum(ctr.values()) or 1
    top = ctr.most_common(5)
    top_counterparties = [{"name": n, "volume": round(v, 2), "share": round(v / total, 3)} for n, v in top]
    top_ratio = (top[0][1] / total) if top else 0

    return {
        "check": "Counterparty concentration check",
        "classification": "rule",
        "status": "completed",
        "mode": "live",
        "top_counterparties": top_counterparties,
        "concentration_ratio": round(top_ratio, 3),
        "concentrated": top_ratio >= _CONCENTRATION_THRESHOLD,
    }


def _check_product_usage_deviation(app: Dict, txns: List[Dict] = None) -> Dict[str, Any]:
    """Check #4 (rule): Product usage deviation.

    When transaction data is available, examines the product_type distribution
    for deviations from the declared sector.  Falls back to degraded mode
    when no transaction records exist.
    """
    sector = app.get("sector")
    txns = txns or []

    if not txns:
        return {
            "check": "Product usage deviation",
            "classification": "rule",
            "status": "degraded",
            "mode": "no_transaction_data",
            "declared_sector": sector,
            "detected_product_mix": [],
            "deviation_detected": False,
        }

    product_counts: Dict[str, int] = {}
    for t in txns:
        pt = t.get("product_type") or "unclassified"
        product_counts[pt] = product_counts.get(pt, 0) + 1

    detected_mix = [{"product": p, "count": c} for p, c in sorted(product_counts.items(), key=lambda x: -x[1])]

    return {
        "check": "Product usage deviation",
        "classification": "rule",
        "status": "completed",
        "mode": "live",
        "declared_sector": sector,
        "detected_product_mix": detected_mix,
        "deviation_detected": False,  # Requires sector->product mapping for real detection
    }


def _check_dormancy(app: Dict) -> Dict[str, Any]:
    """Check #5 (rule): Dormancy/reactivation detection.

    Without transaction data, checks application status and last activity timestamp.
    """
    status = app.get("status", "")
    created_at = app.get("created_at")
    updated_at = app.get("updated_at")

    last_activity = updated_at or created_at
    days_inactive = None
    if last_activity:
        try:
            last_dt = datetime.fromisoformat(str(last_activity).replace("Z", "+00:00").replace("+00:00", ""))
            days_inactive = (datetime.now(timezone.utc).replace(tzinfo=None) - last_dt).days
        except (ValueError, TypeError):
            pass

    is_dormant = days_inactive is not None and days_inactive > _DORMANCY_DAYS

    return {
        "check": "Dormancy/reactivation detection",
        "classification": "rule",
        "status": "completed",
        "application_status": status,
        "days_since_last_activity": days_inactive,
        "dormancy_threshold_days": _DORMANCY_DAYS,
        "is_dormant": is_dormant,
    }


def _check_threshold_breach(app: Dict, alerts: List[Dict]) -> Dict[str, Any]:
    """Check #6 (rule): Threshold breach detection.

    Scans existing alerts for threshold-related types.
    """
    threshold_alerts = [
        a for a in alerts
        if (a.get("alert_type") or "").lower() in ("threshold_breach", "volume_alert", "limit_exceeded")
    ]
    return {
        "check": "Threshold breach detection",
        "classification": "rule",
        "status": "completed",
        "threshold_breaches_found": len(threshold_alerts),
        "breaches": [{
            "alert_id": a.get("id"),
            "type": a.get("alert_type"),
            "severity": a.get("severity"),
            "summary": a.get("summary"),
        } for a in threshold_alerts[:5]],
    }


def _score_velocity_anomaly(rule_checks: List[Dict]) -> Dict[str, Any]:
    """Check #7 (hybrid): Velocity anomaly scoring.

    Synthesises rule-check outputs into a velocity anomaly score.
    In degraded mode, relies on dormancy and threshold breach signals.
    """
    score = 0.0
    for cr in rule_checks:
        if cr.get("is_dormant"):
            score += 0.3
        if cr.get("threshold_breaches_found", 0) > 0:
            score += min(cr["threshold_breaches_found"] * 0.15, 0.4)
        if cr.get("deviation_pct") is not None:
            dev = abs(cr["deviation_pct"])
            if dev > _VOLUME_DEVIATION_ALERT:
                score += 0.3
            elif dev > _VOLUME_DEVIATION_WARN:
                score += 0.15

    score = min(score, 1.0)
    return {
        "check": "Velocity anomaly scoring",
        "classification": "hybrid",
        "status": "completed",
        "velocity_score": round(score, 3),
        "anomaly_detected": score >= 0.4,
    }


def _score_peer_deviation(app: Dict) -> Dict[str, Any]:
    """Check #8 (hybrid): Peer group deviation analysis.

    Without a peer benchmark table, reports the entity's sector/risk as its peer group.
    Degraded mode.
    """
    return {
        "check": "Peer group deviation analysis",
        "classification": "hybrid",
        "status": "degraded",
        "mode": "no_peer_data",
        "sector": app.get("sector"),
        "risk_level": app.get("risk_level"),
        "peer_deviation_score": None,
        "deviation_detected": False,
    }


def _detect_temporal_drift(app: Dict, rule_checks: List[Dict]) -> Dict[str, Any]:
    """Check #9 (hybrid): Temporal pattern drift detection.

    Checks for time-based patterns: dormancy, reactivation, seasonal anomalies.
    """
    dormancy_check = next((c for c in rule_checks if "ormancy" in c.get("check", "")), {})
    is_dormant = dormancy_check.get("is_dormant", False)
    days_inactive = dormancy_check.get("days_since_last_activity")

    drift_signals = []
    if is_dormant:
        drift_signals.append("dormancy_detected")
    if days_inactive and days_inactive > 365:
        drift_signals.append("prolonged_inactivity")

    return {
        "check": "Temporal pattern drift detection",
        "classification": "hybrid",
        "status": "completed",
        "drift_signals": drift_signals,
        "temporal_drift_detected": len(drift_signals) > 0,
    }


def _compute_multi_dimensional_drift(rule_checks: List[Dict], hybrid_checks: List[Dict]) -> Dict[str, Any]:
    """Check #10 (hybrid): Multi-dimensional risk drift scoring.

    Weighted aggregation of all drift signals into a single 0-1 score.
    """
    score = 0.0
    weights = {
        "velocity": 0.25,
        "dormancy": 0.20,
        "threshold": 0.20,
        "geographic": 0.15,
        "temporal": 0.10,
        "peer": 0.10,
    }

    for cr in rule_checks + hybrid_checks:
        check = cr.get("check", "").lower()
        if "velocity" in check:
            score += weights["velocity"] * cr.get("velocity_score", 0)
        if "dormancy" in check and cr.get("is_dormant"):
            score += weights["dormancy"]
        if "threshold" in check and cr.get("threshold_breaches_found", 0) > 0:
            score += weights["threshold"] * min(cr["threshold_breaches_found"] / 3, 1.0)
        if "geographic" in check and cr.get("deviation_detected"):
            score += weights["geographic"]
        if "temporal" in check and cr.get("temporal_drift_detected"):
            score += weights["temporal"]
        if "peer" in check and cr.get("deviation_detected"):
            score += weights["peer"]

    score = min(score, 1.0)
    if score >= 0.5:
        direction = "increasing"
    elif score >= 0.2:
        direction = "stable"
    else:
        direction = "stable"

    return {
        "check": "Multi-dimensional risk drift scoring",
        "classification": "hybrid",
        "status": "completed",
        "drift_score": round(score, 3),
        "drift_direction": direction,
        "drift_detected": score >= 0.3,
    }


def _generate_drift_narrative(app: Dict, drift_score: Dict, all_checks: List[Dict]) -> Dict[str, Any]:
    """Check #11 (hybrid): Drift narrative and recommendation.

    Template-based narrative summarising drift findings.
    """
    company = app.get("company_name", "entity")
    score = drift_score.get("drift_score", 0)
    direction = drift_score.get("drift_direction", "stable")

    issues = []
    for cr in all_checks:
        if cr.get("is_dormant"):
            issues.append("account dormancy detected")
        if cr.get("threshold_breaches_found", 0) > 0:
            issues.append(f"{cr['threshold_breaches_found']} threshold breach(es)")
        if cr.get("temporal_drift_detected"):
            issues.append("temporal pattern drift")
        if cr.get("anomaly_detected"):
            issues.append("velocity anomaly")

    if not issues:
        narrative = f"No significant risk drift detected for {company}. Risk profile remains stable."
        recommendation = "continue_monitoring"
    elif score >= 0.5:
        narrative = (
            f"Elevated risk drift detected for {company} (score: {score:.2f}, direction: {direction}). "
            f"Issues: {'; '.join(issues)}. Recommend enhanced due diligence."
        )
        recommendation = "enhanced_due_diligence"
    else:
        narrative = (
            f"Minor risk drift signals for {company} (score: {score:.2f}). "
            f"Issues: {'; '.join(issues)}. Recommend continued monitoring with attention."
        )
        recommendation = "continue_monitoring_with_attention"

    return {
        "check": "Drift narrative and recommendation",
        "classification": "hybrid",
        "status": "completed",
        "narrative": narrative,
        "recommendation": recommendation,
        "issues_summarised": issues,
    }


def execute_behaviour_risk_drift(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 8: Behaviour & Risk Drift — 11 checks (6 rule + 5 hybrid).

    Compares transaction volume, geographic activity, counterparty concentration,
    product usage against onboarding baseline. Degraded mode where no transaction
    data is available.
    """
    db_path = context.get("db_path", "")
    data = _get_app_data(db_path, application_id)
    app = data["application"]
    monitoring = _get_monitoring_data(db_path, application_id)
    alerts = monitoring["alerts"]
    txns = _get_transaction_data(db_path, application_id)
    run_id = str(uuid4())

    # 6 rule checks (transaction-aware: live when txns exist, degraded otherwise)
    c1 = _check_volume_baseline(app, txns)
    c2 = _check_geographic_deviation(app, txns)
    c3 = _check_counterparty_concentration(app, txns)
    c4 = _check_product_usage_deviation(app, txns)
    c5 = _check_dormancy(app)
    c6 = _check_threshold_breach(app, alerts)
    rule_checks = [c1, c2, c3, c4, c5, c6]

    # Fire degraded-mode admin alert if any checks are degraded
    degraded_checks = [c for c in rule_checks if c.get("status") == "degraded"]
    if degraded_checks:
        try:
            from production_controls import alert_degraded_mode
            alert_degraded_mode(
                agent_name="Behaviour & Risk Drift Detection",
                agent_number=8,
                reason=f"{len(degraded_checks)} of 6 rule checks in degraded mode (no transaction data)",
                application_id=application_id,
            )
        except Exception:
            pass

    # 5 hybrid checks
    c7 = _score_velocity_anomaly(rule_checks)
    c8 = _score_peer_deviation(app)
    c9 = _detect_temporal_drift(app, rule_checks)
    c10 = _compute_multi_dimensional_drift(rule_checks, [c7, c8, c9])
    c11 = _generate_drift_narrative(app, c10, rule_checks + [c7, c8, c9])
    hybrid_checks = [c7, c8, c9, c10, c11]
    all_checks = rule_checks + hybrid_checks

    drift_score = c10.get("drift_score", 0)
    drift_detected = c10.get("drift_detected", False)
    drift_direction = c10.get("drift_direction", "stable")

    if drift_score >= 0.5:
        overall_status = AgentStatus.ISSUES_FOUND.value
    elif drift_score >= 0.3:
        overall_status = AgentStatus.INCONCLUSIVE.value
    else:
        overall_status = AgentStatus.CLEAN.value

    escalation_flag = drift_score >= 0.5
    findings = []
    if drift_detected:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "risk_drift",
            "title": f"Risk drift detected (score: {drift_score:.2f})",
            "description": c11.get("narrative", ""),
            "severity": Severity.HIGH.value if drift_score >= 0.5 else Severity.MEDIUM.value,
            "confidence": 0.80,
            "source": "hybrid_drift_analysis",
            "evidence_refs": [],
        })
    else:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "risk_drift",
            "title": "No significant risk drift detected",
            "description": c11.get("narrative", f"Risk profile for {app.get('company_name', 'entity')} remains stable."),
            "severity": Severity.INFO.value,
            "confidence": 0.85,
            "source": "hybrid_drift_analysis",
            "evidence_refs": [],
        })

    output = _base_output(AgentType.BEHAVIOUR_RISK_DRIFT, "Agent 8: Behaviour & Risk Drift", application_id, run_id)
    output.update({
        "status": overall_status,
        "confidence_score": 0.80 if any(c.get("status") == "degraded" for c in all_checks) else 0.85,
        "findings": findings,
        "evidence": [{
            "evidence_id": str(uuid4())[:12],
            "evidence_type": "behaviour_analysis",
            "source": "drift_detection_pipeline",
            "content_summary": f"Drift analysis for {app.get('company_name', '')} — {len(all_checks)} checks",
            "reference": f"BRD-{run_id[:8]}",
            "verified": True,
        }],
        "detected_issues": c11.get("issues_summarised", []),
        "risk_indicators": [],
        "recommendation": c11.get("recommendation", "continue_monitoring"),
        "escalation_flag": escalation_flag,
        "escalation_reason": "Elevated risk drift score" if escalation_flag else None,
        "checks_performed": all_checks,
        "risk_drift_detected": drift_detected,
        "drift_direction": drift_direction,
        "drift_magnitude": drift_score,
        "volume_baseline_comparison": c1,
        "geographic_deviation": c2,
        "counterparty_concentration": c3,
        "product_usage_deviation": c4,
        "dormancy_status": c5,
        "threshold_breaches": c6.get("breaches", []),
        "velocity_anomaly_score": c7.get("velocity_score"),
        "peer_group_deviation": c8,
        "temporal_pattern_drift": c9,
        "multi_dimensional_drift_score": drift_score,
        "drift_narrative": c11.get("narrative"),
        "recommended_action": c11.get("recommendation"),
    })
    return output


# ═══════════════════════════════════════════════════════════
# AGENT 7: Adverse Media & PEP Monitoring (12 checks: 6R + 4H + 2AI)
# ═══════════════════════════════════════════════════════════

_MEDIA_SOURCE_CREDIBILITY = {
    "government": 1.0,
    "regulatory": 0.95,
    "court_records": 0.90,
    "major_news": 0.85,
    "financial_press": 0.80,
    "industry_publication": 0.70,
    "blog": 0.40,
    "social_media": 0.30,
    "unknown": 0.50,
}

_MEDIA_SEVERITY_MATRIX = {
    "sanctions": 4,
    "money_laundering": 4,
    "terrorism_financing": 4,
    "fraud": 3,
    "corruption": 3,
    "tax_evasion": 3,
    "regulatory_action": 2,
    "litigation": 2,
    "negative_press": 1,
    "unknown": 1,
}
_MEDIA_ALERT_TYPES = {"adverse_media", "media_alert", "news_alert", "media"}


def _retrieve_new_media(app: Dict, alerts: List[Dict]) -> Dict[str, Any]:
    """Check #1 (rule): New adverse media retrieval.

    Scans monitoring_alerts for media-type alerts. Without a live media feed,
    this reads stored alert data.
    """
    media_alerts = [
        a for a in alerts
        if (a.get("alert_type") or "").lower() in _MEDIA_ALERT_TYPES
    ]
    return {
        "check": "New adverse media retrieval",
        "classification": "rule",
        "status": "completed",
        "media_alerts_found": len(media_alerts),
        "hits": [{
            "alert_id": a.get("id"),
            "summary": a.get("summary"),
            "severity": a.get("severity"),
            "source": a.get("source_reference"),
            "status": a.get("status"),
            "discovered_via": a.get("discovered_via", "webhook_live"),
            "discovered_at": a.get("discovered_at"),
            "backfill_run_id": a.get("backfill_run_id"),
        } for a in media_alerts[:10]],
    }


def _detect_pep_changes(app: Dict, directors: List[Dict], ubos: List[Dict]) -> Dict[str, Any]:
    """Check #2 (rule): PEP status change detection.

    Compares current PEP flags on directors/UBOs against stored declarations.
    """
    pep_persons = []
    for person_list, role in [(directors, "director"), (ubos, "ubo")]:
        for p in person_list:
            is_pep = str(p.get("is_pep", "No")).lower() in ("yes", "true", "1")
            if is_pep:
                pep_persons.append({
                    "name": p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
                    "role": role,
                    "pep_status": "active",
                })

    return {
        "check": "PEP status change detection",
        "classification": "rule",
        "status": "completed",
        "current_pep_count": len(pep_persons),
        "pep_persons": pep_persons,
        "changes_detected": [],  # No historical PEP snapshots yet — report current state
    }


def _check_sanctions_updates(app: Dict, alerts: List[Dict]) -> Dict[str, Any]:
    """Check #3 (rule): Sanctions list update check.

    Scans alerts for sanctions-related entries.
    """
    sanctions_alerts = [
        a for a in alerts
        if (a.get("alert_type") or "").lower() in ("sanctions", "sanctions_hit", "sanctions_update")
    ]
    return {
        "check": "Sanctions list update check",
        "classification": "rule",
        "status": "completed",
        "sanctions_alerts_found": len(sanctions_alerts),
        "hits": [{
            "alert_id": a.get("id"),
            "summary": a.get("summary"),
            "severity": a.get("severity"),
        } for a in sanctions_alerts[:5]],
    }


def _score_media_credibility(media_check: Dict) -> Dict[str, Any]:
    """Check #4 (rule): Media source credibility scoring."""
    scored_hits = []
    for hit in media_check.get("hits", []):
        source = (hit.get("source") or "unknown").lower()
        credibility = _MEDIA_SOURCE_CREDIBILITY.get(source, _MEDIA_SOURCE_CREDIBILITY["unknown"])
        scored_hits.append({**hit, "credibility_score": credibility, "source_category": source})

    return {
        "check": "Media source credibility scoring",
        "classification": "rule",
        "status": "completed",
        "scored_hits": scored_hits,
        "high_credibility_count": sum(1 for h in scored_hits if h["credibility_score"] >= 0.7),
    }


def _deduplicate_alerts(media_check: Dict, sanctions_check: Dict, pep_check: Dict) -> Dict[str, Any]:
    """Check #5 (rule): Alert deduplication.

    Removes duplicate alert IDs across media, sanctions, and PEP checks.
    """
    all_ids = set()
    total_raw = 0
    for check in [media_check, sanctions_check]:
        for hit in check.get("hits", []):
            aid = hit.get("alert_id")
            if aid:
                all_ids.add(str(aid))
            total_raw += 1

    deduplicated_count = len(all_ids)
    duplicates_removed = max(0, total_raw - deduplicated_count)

    return {
        "check": "Alert deduplication",
        "classification": "rule",
        "status": "completed",
        "total_raw_alerts": total_raw,
        "deduplicated_count": deduplicated_count,
        "duplicates_removed": duplicates_removed,
    }


def _compare_historical_media(app: Dict, media_check: Dict) -> Dict[str, Any]:
    """Check #6 (rule): Historical media comparison.

    Compares current media hits against prescreening baseline.
    """
    prescreening_raw = app.get("prescreening_data") or "{}"
    try:
        prescreening = json.loads(prescreening_raw) if isinstance(prescreening_raw, str) else (prescreening_raw or {})
    except (json.JSONDecodeError, TypeError):
        prescreening = {}

    baseline_report = prescreening.get("screening_report", {})
    baseline_media = baseline_report.get("adverse_media", {})
    baseline_count = len(baseline_media.get("hits", [])) if isinstance(baseline_media, dict) else 0
    hits = media_check.get("hits", [])
    current_count = media_check.get("media_alerts_found", 0)
    if hits:
        historical_backfill_hits = sum(
            1 for hit in hits
            if hit.get("discovered_via") in ("webhook_backfill", "manual_backfill")
        )
        live_hits = sum(
            1 for hit in hits
            if hit.get("discovered_via", "webhook_live") == "webhook_live"
        )
    else:
        historical_backfill_hits = 0
        live_hits = current_count
    live_new_hits_since_baseline = max(0, live_hits - baseline_count)

    return {
        "check": "Historical media comparison",
        "classification": "rule",
        "status": "completed" if baseline_report else "no_baseline",
        "baseline_media_count": baseline_count,
        "current_media_count": current_count,
        "historical_backfill_hits": historical_backfill_hits,
        "live_monitoring_hits": live_hits,
        "live_new_hits_since_baseline": live_new_hits_since_baseline,
        "new_since_baseline": live_new_hits_since_baseline,
        "has_baseline": bool(baseline_report),
    }


def _assess_media_severity(media_check: Dict, credibility_check: Dict) -> Dict[str, Any]:
    """Check #7 (hybrid): Media severity assessment.

    Combines hit type with source credibility for severity scoring.
    """
    scored = []
    for hit in credibility_check.get("scored_hits", []):
        summary = (hit.get("summary") or "").lower()
        # Infer category from summary keywords
        category = "unknown"
        for key, _ in sorted(_MEDIA_SEVERITY_MATRIX.items(), key=lambda x: -x[1]):
            if key.replace("_", " ") in summary:
                category = key
                break

        base_severity = _MEDIA_SEVERITY_MATRIX.get(category, 1)
        credibility = hit.get("credibility_score", 0.5)
        adjusted_severity = round(base_severity * credibility, 2)

        scored.append({
            **hit,
            "inferred_category": category,
            "base_severity": base_severity,
            "adjusted_severity": adjusted_severity,
        })

    max_sev = max((s["adjusted_severity"] for s in scored), default=0)
    return {
        "check": "Media severity assessment",
        "classification": "hybrid",
        "status": "completed",
        "severity_scored_hits": scored,
        "max_adjusted_severity": max_sev,
    }


def _score_pep_proximity(pep_check: Dict, ubos: List[Dict], directors: List[Dict]) -> Dict[str, Any]:
    """Check #8 (hybrid): PEP proximity scoring.

    Scores PEP exposure based on ownership percentage and role.
    """
    scores = []
    for pep in pep_check.get("pep_persons", []):
        name = pep.get("name", "")
        role = pep.get("role", "")
        ownership_pct = 0

        if role == "ubo":
            for u in ubos:
                full_name = u.get("full_name", "")
                if full_name and full_name.lower() == name.lower():
                    try:
                        ownership_pct = float(u.get("ownership_pct", 0) or 0)
                    except (ValueError, TypeError):
                        pass

        # Proximity score: higher for UBOs with large ownership
        base = 0.6 if role == "director" else 0.5
        ownership_factor = min(ownership_pct / 100.0, 1.0) * 0.4 if role == "ubo" else 0
        proximity = min(base + ownership_factor, 1.0)

        scores.append({
            "name": name,
            "role": role,
            "ownership_pct": ownership_pct,
            "proximity_score": round(proximity, 3),
        })

    return {
        "check": "PEP proximity scoring",
        "classification": "hybrid",
        "status": "completed",
        "pep_proximity_scores": scores,
        "max_proximity": max((s["proximity_score"] for s in scores), default=0),
    }


def _resolve_entities(media_check: Dict, pep_check: Dict, app: Dict) -> Dict[str, Any]:
    """Check #9 (hybrid): Entity resolution for media hits.

    Attempts to match media/alert subjects to known persons in the application.
    """
    known_names = set()
    company_name = (app.get("company_name") or "").lower()
    if company_name:
        known_names.add(company_name)

    resolved = []
    unresolved = []
    for hit in media_check.get("hits", []):
        summary = (hit.get("summary") or "").lower()
        matched = company_name and company_name in summary
        entry = {"alert_id": hit.get("alert_id"), "summary": hit.get("summary"), "matched": matched}
        if matched:
            resolved.append(entry)
        else:
            unresolved.append(entry)

    return {
        "check": "Entity resolution for media hits",
        "classification": "hybrid",
        "status": "completed",
        "resolved_count": len(resolved),
        "unresolved_count": len(unresolved),
        "resolved": resolved[:5],
        "unresolved": unresolved[:5],
    }


def _aggregate_risk_signals(all_checks: List[Dict]) -> Dict[str, Any]:
    """Check #10 (hybrid): Combined risk signal aggregation.

    Produces a combined risk signal from all monitoring checks.
    """
    signals = {
        "media_risk": 0,
        "pep_risk": 0,
        "sanctions_risk": 0,
    }

    for cr in all_checks:
        check = cr.get("check", "").lower()
        if "severity" in check:
            signals["media_risk"] = max(signals["media_risk"], cr.get("max_adjusted_severity", 0))
        if "historical media comparison" in check and cr.get("historical_backfill_hits", 0) > 0:
            signals["media_risk"] = max(signals["media_risk"], 1)
        if "historical media comparison" in check and cr.get("live_new_hits_since_baseline", 0) > 0:
            signals["media_risk"] = max(signals["media_risk"], 1)
        if "pep proximity" in check:
            signals["pep_risk"] = cr.get("max_proximity", 0) * 4  # scale to 0-4
        if "sanctions" in check:
            signals["sanctions_risk"] = 4.0 if cr.get("sanctions_alerts_found", 0) > 0 else 0

    combined = max(signals.values())
    if combined >= 3:
        risk_level = "critical"
    elif combined >= 2:
        risk_level = "high"
    elif combined >= 1:
        risk_level = "medium"
    else:
        risk_level = "low"

    return {
        "check": "Combined risk signal aggregation",
        "classification": "hybrid",
        "status": "completed",
        "signals": signals,
        "combined_score": round(combined, 2),
        "risk_level": risk_level,
    }


def _generate_media_narrative(app: Dict, all_checks: List[Dict]) -> Dict[str, Any]:
    """Check #11 (ai): Media narrative summarisation.

    Uses ClaudeClient when available; falls back to template-based narrative.
    """
    company = app.get("company_name", "entity")

    # Collect key facts for narrative
    media_count = 0
    historical_count = 0
    pep_count = 0
    sanctions_count = 0
    risk_level = "low"
    for cr in all_checks:
        check = cr.get("check", "").lower()
        if "media retrieval" in check:
            media_count = cr.get("media_alerts_found", 0)
        if "pep status" in check:
            pep_count = cr.get("current_pep_count", 0)
        if "sanctions" in check:
            sanctions_count = cr.get("sanctions_alerts_found", 0)
        if "signal aggregation" in check:
            risk_level = cr.get("risk_level", "low")
        if "historical media comparison" in check:
            historical_count = cr.get("historical_backfill_hits", 0)

    # Try AI narrative
    narrative = None
    ai_used = False
    if media_count > 0 or pep_count > 0 or sanctions_count > 0:
        try:
            from claude_client import ClaudeClient
            client = ClaudeClient()
            prompt = (
                f"Write a concise monitoring narrative for {company}. "
                f"Media alerts: {media_count}. PEP-exposed persons: {pep_count}. "
                f"Sanctions alerts: {sanctions_count}. Overall risk: {risk_level}. "
                f"Summarise the monitoring findings in 2-3 sentences for a compliance officer."
            )
            response = client.generate(prompt, max_tokens=300)
            if response and isinstance(response, str) and len(response) > 10:
                narrative = response.strip()
                ai_used = True
        except Exception:
            pass

    if not narrative:
        if media_count == 0 and pep_count == 0 and sanctions_count == 0:
            narrative = f"No new adverse media, PEP changes, or sanctions alerts detected for {company}. Monitoring scan is clean."
        else:
            parts = []
            if media_count > 0:
                if historical_count:
                    parts.append(
                        f"{media_count} adverse media alert(s), including {historical_count} historically discovered backfill hit(s)"
                    )
                else:
                    parts.append(f"{media_count} adverse media alert(s)")
            if pep_count > 0:
                parts.append(f"{pep_count} PEP-exposed person(s)")
            if sanctions_count > 0:
                parts.append(f"{sanctions_count} sanctions alert(s)")
            narrative = f"Monitoring scan for {company} detected: {'; '.join(parts)}. Risk level: {risk_level}. Review recommended."

    return {
        "check": "Media narrative summarisation",
        "classification": "ai",
        "status": "completed",
        "narrative": narrative,
        "ai_used": ai_used,
    }


def _determine_monitoring_disposition(app: Dict, risk_signal: Dict, all_checks: List[Dict]) -> Dict[str, Any]:
    """Check #12 (ai): Monitoring alert disposition.

    Uses ClaudeClient when available; falls back to rule-based disposition.
    """
    combined_score = risk_signal.get("combined_score", 0)
    risk_level = risk_signal.get("risk_level", "low")

    # Rule-based disposition (fallback and default)
    if combined_score >= 3:
        disposition = "ESCALATE"
        reason = "Critical risk signals detected — immediate compliance review required"
    elif combined_score >= 2:
        disposition = "REVIEW"
        reason = "Elevated risk signals — compliance officer review recommended"
    elif combined_score >= 1:
        disposition = "MONITOR"
        reason = "Minor risk signals — continue enhanced monitoring"
    else:
        disposition = "CLEAR"
        reason = "No significant risk signals — standard monitoring continues"

    ai_used = False
    # Try AI for nuanced disposition on elevated cases
    if combined_score >= 2:
        try:
            from claude_client import ClaudeClient
            client = ClaudeClient()
            company = app.get("company_name", "entity")
            prompt = (
                f"As a compliance monitoring system, determine the disposition for {company}. "
                f"Combined risk score: {combined_score:.1f}/4.0. Risk level: {risk_level}. "
                f"Signals: {json.dumps(risk_signal.get('signals', {}))}. "
                f"Respond with one of: ESCALATE, REVIEW, MONITOR, CLEAR. "
                f"Then provide a one-sentence reason."
            )
            response = client.generate(prompt, max_tokens=150)
            if response and isinstance(response, str):
                resp_upper = response.strip().upper()
                for d in ("ESCALATE", "REVIEW", "MONITOR", "CLEAR"):
                    if resp_upper.startswith(d):
                        disposition = d
                        reason = response.strip()
                        ai_used = True
                        break
        except Exception:
            pass

    return {
        "check": "Monitoring alert disposition",
        "classification": "ai",
        "status": "completed",
        "disposition": disposition,
        "reason": reason,
        "combined_risk_score": combined_score,
        "ai_used": ai_used,
    }


def execute_adverse_media_pep(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 7: Adverse Media & PEP Monitoring — 12 checks (6R + 4H + 2AI).

    Retrieves new media/PEP/sanctions signals, deduplicates, scores severity,
    resolves entities; AI generates narrative summary and disposition.
    """
    db_path = context.get("db_path", "")
    data = _get_app_data(db_path, application_id)
    app = data["application"]
    directors = data["directors"]
    ubos = data["ubos"]
    monitoring = _get_monitoring_data(db_path, application_id)
    alerts = monitoring["alerts"]
    run_id = str(uuid4())

    # 6 rule checks
    c1 = _retrieve_new_media(app, alerts)
    c2 = _detect_pep_changes(app, directors, ubos)
    c3 = _check_sanctions_updates(app, alerts)
    c4 = _score_media_credibility(c1)
    c5 = _deduplicate_alerts(c1, c3, c2)
    c6 = _compare_historical_media(app, c1)
    rule_checks = [c1, c2, c3, c4, c5, c6]

    # 4 hybrid checks
    c7 = _assess_media_severity(c1, c4)
    c8 = _score_pep_proximity(c2, ubos, directors)
    c9 = _resolve_entities(c1, c2, app)
    c10 = _aggregate_risk_signals(rule_checks + [c7, c8, c9])
    hybrid_checks = [c7, c8, c9, c10]

    # 2 AI checks
    c11 = _generate_media_narrative(app, rule_checks + hybrid_checks)
    c12 = _determine_monitoring_disposition(app, c10, rule_checks + hybrid_checks)
    ai_checks = [c11, c12]

    all_checks = rule_checks + hybrid_checks + ai_checks

    combined_score = c10.get("combined_score", 0)
    disposition = c12.get("disposition", "CLEAR")

    if disposition == "ESCALATE":
        overall_status = AgentStatus.ISSUES_FOUND.value
        alert_generated = True
        alert_severity = Severity.CRITICAL
    elif disposition == "REVIEW":
        overall_status = AgentStatus.INCONCLUSIVE.value
        alert_generated = True
        alert_severity = Severity.HIGH
    elif disposition == "MONITOR":
        overall_status = AgentStatus.INCONCLUSIVE.value
        alert_generated = combined_score > 0
        alert_severity = Severity.MEDIUM if combined_score > 0 else None
    else:
        overall_status = AgentStatus.CLEAN.value
        alert_generated = False
        alert_severity = None

    findings = []
    if c1.get("media_alerts_found", 0) > 0:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "adverse_media",
            "title": f"{c1['media_alerts_found']} adverse media alert(s) found",
            "description": c11.get("narrative", ""),
            "severity": Severity.HIGH.value if combined_score >= 2 else Severity.MEDIUM.value,
            "confidence": 0.85,
            "source": "monitoring_pipeline",
            "evidence_refs": [],
        })
    if c2.get("current_pep_count", 0) > 0:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "pep_monitoring",
            "title": f"{c2['current_pep_count']} PEP-exposed person(s)",
            "description": f"Max proximity score: {c8.get('max_proximity', 0):.2f}",
            "severity": Severity.HIGH.value if c8.get("max_proximity", 0) >= 0.7 else Severity.MEDIUM.value,
            "confidence": 0.85,
            "source": "monitoring_pipeline",
            "evidence_refs": [],
        })
    if c3.get("sanctions_alerts_found", 0) > 0:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "sanctions",
            "title": f"{c3['sanctions_alerts_found']} sanctions alert(s)",
            "description": "Sanctions list match detected — immediate review required",
            "severity": Severity.CRITICAL.value,
            "confidence": 0.90,
            "source": "monitoring_pipeline",
            "evidence_refs": [],
        })
    if not findings:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "media_monitoring",
            "title": "No new adverse signals detected",
            "description": c11.get("narrative", "Clean monitoring scan."),
            "severity": Severity.INFO.value,
            "confidence": 0.90,
            "source": "monitoring_pipeline",
            "evidence_refs": [],
        })

    output = _base_output(AgentType.ADVERSE_MEDIA_PEP_MONITORING, "Agent 7: Adverse Media & PEP Monitoring", application_id, run_id)
    output.update({
        "status": overall_status,
        "confidence_score": 0.85,
        "findings": findings,
        "evidence": [{
            "evidence_id": str(uuid4())[:12],
            "evidence_type": "monitoring_scan",
            "source": "monitoring_pipeline",
            "content_summary": f"Monitoring scan for {app.get('company_name', '')} — {len(all_checks)} checks",
            "reference": f"MON-{run_id[:8]}",
            "verified": True,
        }],
        "detected_issues": [f.get("title") for f in findings if f.get("severity") != Severity.INFO.value],
        "risk_indicators": [],
        "recommendation": c12.get("reason", "No action required"),
        "escalation_flag": disposition == "ESCALATE",
        "escalation_reason": c12.get("reason") if disposition == "ESCALATE" else None,
        "checks_performed": all_checks,
        "new_media_hits": c1.get("hits", []),
        "pep_status_changes": c2.get("changes_detected", []),
        "sanctions_updates": c3.get("hits", []),
        "source_credibility_scores": c4.get("scored_hits", []),
        "deduplicated_alert_count": c5.get("deduplicated_count", 0),
        "historical_comparison": c6,
        "media_severity_scores": c7.get("severity_scored_hits", []),
        "pep_proximity_scores": c8.get("pep_proximity_scores", []),
        "entity_resolution_results": c9.get("resolved", []) + c9.get("unresolved", []),
        "combined_risk_signal": c10,
        "narrative": c11.get("narrative"),
        "disposition": c12,
        "alert_generated": alert_generated,
        "alert_severity": alert_severity.value if alert_severity else None,
    })
    return output


def execute_regulatory_impact(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 9: Regulatory Impact — DEFERRED (future phase).

    This agent is registered in the master register but is explicitly deferred.
    It returns a PARTIAL status with zero confidence and a clear invocation guard
    message indicating it is not yet implemented for production use.
    """
    run_id = str(uuid4())
    output = _base_output(AgentType.REGULATORY_IMPACT, "Agent 9: Regulatory Impact", application_id, run_id)
    logger.warning(
        "Agent 9 (Regulatory Impact) invoked for application %s — "
        "this agent is DEFERRED (future phase) and returns placeholder output only. "
        "Do not use for approval decisions.",
        application_id,
    )
    output.update({
        "status": AgentStatus.PARTIAL.value,
        "confidence_score": 0.0,
        "findings": [],
        "evidence": [],
        "detected_issues": [{
            "issue": "Agent 9 is a registered future-phase agent — not yet implemented",
            "severity": "info",
            "action_required": "Manual regulatory review required until Agent 9 is fully implemented",
        }],
        "risk_indicators": [],
        "recommendation": "DEFERRED — manual regulatory review required. Agent 9 is registered but not yet implemented.",
        "escalation_flag": False,
        "escalation_reason": None,
        "impact_summary": (
            "Regulatory Impact is a registered future-phase agent and is NOT active in the "
            "live approval chain. This output is a placeholder only — do not rely on it for "
            "compliance decisions."
        ),
        "affected_jurisdictions": [],
        "affected_controls": [],
        "implementation_required": False,
        "implementation_deadline": None,
        "_deferred": True,
        "_deferred_reason": "Agent 9 is registered in master register but implementation is deferred to future phase",
    })
    return output


# ═══════════════════════════════════════════════════════════
# AGENT 10: Ongoing Compliance Review (11 checks: 7R + 2H + 2AI)
# ═══════════════════════════════════════════════════════════

_SCREENING_RECENCY_WARN_DAYS = 180
_FILING_DEADLINES_MONTHS = {"annual_return": 12, "financial_statements": 18, "licence_renewal": 12}


def _check_document_currency(documents: List[Dict]) -> Dict[str, Any]:
    """Check #1 (rule): Document currency verification."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    results = []
    for doc in documents:
        doc_type = doc.get("document_type", "unknown")
        status = doc.get("verification_status", "pending")
        expiry_str = doc.get("expiry_date") or doc.get("valid_until")
        days_to_expiry = None
        is_current = True
        if expiry_str:
            try:
                expiry = datetime.fromisoformat(str(expiry_str).replace("Z", "+00:00").replace("+00:00", ""))
                days_to_expiry = (expiry - now).days
                is_current = days_to_expiry > 0
            except (ValueError, TypeError):
                pass

        results.append({
            "document_id": doc.get("id"),
            "document_type": doc_type,
            "verification_status": status,
            "days_to_expiry": days_to_expiry,
            "is_current": is_current,
        })

    expired_count = sum(1 for r in results if not r["is_current"] and r["days_to_expiry"] is not None)
    return {
        "check": "Document currency verification",
        "classification": "rule",
        "status": "completed",
        "total_documents": len(results),
        "expired_count": expired_count,
        "documents": results[:20],
    }


def _check_screening_recency(app: Dict) -> Dict[str, Any]:
    """Check #2 (rule): Screening recency check."""
    prescreening_raw = app.get("prescreening_data") or "{}"
    try:
        prescreening = json.loads(prescreening_raw) if isinstance(prescreening_raw, str) else (prescreening_raw or {})
    except (json.JSONDecodeError, TypeError):
        prescreening = {}

    screening_report = prescreening.get("screening_report", {})
    screened_at = screening_report.get("screened_at") or screening_report.get("timestamp")
    days = None
    if screened_at:
        try:
            dt = datetime.fromisoformat(str(screened_at).replace("Z", "+00:00").replace("+00:00", ""))
            days = (datetime.now(timezone.utc).replace(tzinfo=None) - dt).days
        except (ValueError, TypeError):
            pass

    return {
        "check": "Screening recency check",
        "classification": "rule",
        "status": "completed" if days is not None else "no_screening_data",
        "days_since_screening": days,
        "is_recent": days is not None and days <= _SCREENING_RECENCY_WARN_DAYS,
        "threshold_days": _SCREENING_RECENCY_WARN_DAYS,
    }


def _check_policy_applicability(app: Dict) -> Dict[str, Any]:
    """Check #3 (rule): Policy change applicability check.

    Without a policy change feed, checks entity attributes against known
    high-risk policy triggers.
    """
    triggers = []
    country = (app.get("country") or "").lower()
    sector = (app.get("sector") or "").lower()
    risk_level = app.get("risk_level", "")

    high_risk_jurisdictions = {"iran", "north korea", "myanmar", "syria", "cuba", "russia", "belarus"}
    high_risk_sectors = {"cryptocurrency", "gambling", "cannabis", "weapons", "precious metals"}

    if country in high_risk_jurisdictions:
        triggers.append({"type": "jurisdiction", "detail": f"High-risk jurisdiction: {country}"})
    if sector in high_risk_sectors:
        triggers.append({"type": "sector", "detail": f"High-risk sector: {sector}"})
    if risk_level in ("HIGH", "VERY_HIGH"):
        triggers.append({"type": "risk_level", "detail": f"Elevated risk level: {risk_level}"})

    return {
        "check": "Policy change applicability check",
        "classification": "rule",
        "status": "completed",
        "policy_triggers": triggers,
        "trigger_count": len(triggers),
    }


def _check_condition_compliance(app: Dict, alerts: List[Dict]) -> Dict[str, Any]:
    """Check #4 (rule): Condition compliance tracking.

    Checks if any conditions were imposed during onboarding and whether they're met.
    """
    status = app.get("status", "")
    conditions = []

    # Check for conditional approval
    if status == "conditionally_approved":
        conditions.append({
            "condition": "Conditional approval — outstanding conditions exist",
            "met": False,
        })

    # Check for unresolved alerts that may represent conditions
    condition_alerts = [
        a for a in alerts
        if (a.get("alert_type") or "").lower() in ("condition", "requirement", "action_required")
        and a.get("status") in ("open", "pending", None)
    ]
    for a in condition_alerts:
        conditions.append({
            "condition": a.get("summary", "Unresolved condition"),
            "met": False,
            "alert_id": a.get("id"),
        })

    return {
        "check": "Condition compliance tracking",
        "classification": "rule",
        "status": "completed",
        "conditions_tracked": len(conditions),
        "conditions_met": sum(1 for c in conditions if c.get("met")),
        "conditions_unmet": sum(1 for c in conditions if not c.get("met")),
        "conditions": conditions,
    }


def _check_filing_deadlines(app: Dict) -> Dict[str, Any]:
    """Check #5 (rule): Filing deadline monitoring.

    Estimates filing deadlines based on incorporation date.
    """
    deadlines = []
    created_str = app.get("created_at") or app.get("submitted_at")
    if created_str:
        try:
            created = datetime.fromisoformat(str(created_str).replace("Z", "+00:00").replace("+00:00", ""))
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            for filing_type, months in _FILING_DEADLINES_MONTHS.items():
                # Estimate next deadline from creation
                next_due = created.replace(year=created.year + (months // 12))
                while next_due < now:
                    next_due = next_due.replace(year=next_due.year + 1)
                days_until = (next_due - now).days
                deadlines.append({
                    "filing_type": filing_type,
                    "estimated_due": next_due.isoformat(),
                    "days_until_due": days_until,
                    "status": "overdue" if days_until < 0 else ("upcoming" if days_until < 30 else "ok"),
                })
        except (ValueError, TypeError):
            pass

    return {
        "check": "Filing deadline monitoring",
        "classification": "rule",
        "status": "completed" if deadlines else "no_date_available",
        "deadlines": deadlines,
        "overdue_count": sum(1 for d in deadlines if d["status"] == "overdue"),
    }


def _consolidate_inter_agent_findings(alerts: List[Dict], reviews: List[Dict]) -> Dict[str, Any]:
    """Check #6 (rule): Inter-agent finding consolidation.

    Aggregates findings from monitoring_alerts (populated by agents 6, 7, 8).
    """
    by_type = {}
    for a in alerts:
        atype = a.get("alert_type", "unknown")
        by_type[atype] = by_type.get(atype, 0) + 1

    open_count = sum(1 for a in alerts if a.get("status") in ("open", "pending", None))
    reviewed_count = sum(1 for a in alerts if a.get("status") in ("reviewed", "resolved", "closed"))

    return {
        "check": "Inter-agent finding consolidation",
        "classification": "rule",
        "status": "completed",
        "total_alerts": len(alerts),
        "open_alerts": open_count,
        "reviewed_alerts": reviewed_count,
        "by_alert_type": by_type,
        "total_reviews": len(reviews),
        "pending_reviews": sum(1 for r in reviews if r.get("status") == "pending"),
    }


def _track_remediation(alerts: List[Dict]) -> Dict[str, Any]:
    """Check #7 (rule): Remediation tracker status."""
    remediation_items = []
    for a in alerts:
        if a.get("status") in ("open", "pending", None) and a.get("ai_recommendation"):
            remediation_items.append({
                "alert_id": a.get("id"),
                "type": a.get("alert_type"),
                "severity": a.get("severity"),
                "recommendation": a.get("ai_recommendation"),
                "status": "open",
            })

    return {
        "check": "Remediation tracker status",
        "classification": "rule",
        "status": "completed",
        "open_remediation_items": len(remediation_items),
        "items": remediation_items[:10],
    }


def _rescore_compliance_risk(all_rule_checks: List[Dict], app: Dict) -> Dict[str, Any]:
    """Check #8 (hybrid): Compliance risk re-scoring.

    Weighted aggregation of all rule-check outputs into a compliance risk score.
    """
    score = 0.0
    base_risk = {"LOW": 10, "MEDIUM": 30, "HIGH": 50, "VERY_HIGH": 70}
    score += base_risk.get(app.get("risk_level", "MEDIUM"), 30)

    for cr in all_rule_checks:
        check = cr.get("check", "").lower()
        if "document currency" in check:
            expired = cr.get("expired_count", 0)
            score += min(expired * 3, 10)
        if "screening recency" in check:
            if not cr.get("is_recent", True):
                score += 10
        if "policy" in check:
            score += cr.get("trigger_count", 0) * 5
        if "condition" in check:
            score += cr.get("conditions_unmet", 0) * 5
        if "filing" in check:
            score += cr.get("overdue_count", 0) * 5
        if "inter-agent" in check:
            score += min(cr.get("open_alerts", 0) * 2, 10)
        if "remediation" in check:
            score += min(cr.get("open_remediation_items", 0) * 3, 10)

    score = min(score, 100.0)
    return {
        "check": "Compliance risk re-scoring",
        "classification": "hybrid",
        "status": "completed",
        "compliance_risk_score": round(score, 1),
    }


def _recommend_review_frequency(app: Dict, risk_score: Dict) -> Dict[str, Any]:
    """Check #9 (hybrid): Review frequency recommendation."""
    score = risk_score.get("compliance_risk_score", 30)

    if score >= 70:
        frequency = "monthly"
        next_days = 30
    elif score >= 50:
        frequency = "quarterly"
        next_days = 90
    elif score >= 30:
        frequency = "semi-annual"
        next_days = 180
    else:
        frequency = "annual"
        next_days = 365

    next_review = (datetime.now(timezone.utc).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0))
    next_review = next_review.replace(day=1)  # Start of next period
    from datetime import timedelta
    next_review = next_review + timedelta(days=next_days)

    return {
        "check": "Review frequency recommendation",
        "classification": "hybrid",
        "status": "completed",
        "recommended_frequency": frequency,
        "next_review_due": next_review.isoformat() + "Z",
        "compliance_risk_score": score,
    }


def _generate_compliance_narrative(app: Dict, all_checks: List[Dict]) -> Dict[str, Any]:
    """Check #10 (ai): Compliance narrative generation.

    Uses ClaudeClient when available; falls back to template narrative.
    """
    company = app.get("company_name", "entity")

    # Gather key metrics
    expired_docs = 0
    open_alerts = 0
    unmet_conditions = 0
    risk_score = 30
    for cr in all_checks:
        check = cr.get("check", "").lower()
        if "document currency" in check:
            expired_docs = cr.get("expired_count", 0)
        if "inter-agent" in check:
            open_alerts = cr.get("open_alerts", 0)
        if "condition" in check:
            unmet_conditions = cr.get("conditions_unmet", 0)
        if "compliance risk" in check:
            risk_score = cr.get("compliance_risk_score", 30)

    narrative = None
    ai_used = False
    if risk_score >= 50 or expired_docs > 0 or open_alerts > 0:
        try:
            from claude_client import ClaudeClient
            client = ClaudeClient()
            prompt = (
                f"Write a compliance review narrative for {company}. "
                f"Risk score: {risk_score}/100. Expired documents: {expired_docs}. "
                f"Open alerts: {open_alerts}. Unmet conditions: {unmet_conditions}. "
                f"Provide 2-3 sentences summarising compliance status for a compliance officer."
            )
            response = client.generate(prompt, max_tokens=300)
            if response and isinstance(response, str) and len(response) > 10:
                narrative = response.strip()
                ai_used = True
        except Exception:
            pass

    if not narrative:
        if risk_score < 30 and expired_docs == 0 and open_alerts == 0:
            narrative = f"{company} is in good compliance standing. No expired documents, no open alerts, and no outstanding conditions."
        else:
            issues = []
            if expired_docs > 0:
                issues.append(f"{expired_docs} expired document(s)")
            if open_alerts > 0:
                issues.append(f"{open_alerts} open alert(s)")
            if unmet_conditions > 0:
                issues.append(f"{unmet_conditions} unmet condition(s)")
            narrative = (
                f"Compliance review for {company} (risk score: {risk_score:.0f}/100). "
                f"Issues: {'; '.join(issues) if issues else 'none'}. "
                f"{'Review and remediation recommended.' if risk_score >= 50 else 'Continued monitoring recommended.'}"
            )

    return {
        "check": "Compliance narrative generation",
        "classification": "ai",
        "status": "completed",
        "narrative": narrative,
        "ai_used": ai_used,
    }


def _recommend_escalation_closure(app: Dict, risk_score: Dict, all_checks: List[Dict]) -> Dict[str, Any]:
    """Check #11 (ai): Escalation/closure recommendation.

    Uses ClaudeClient for elevated cases; falls back to rule-based logic.
    """
    score = risk_score.get("compliance_risk_score", 30)

    if score >= 70:
        recommendation = "ESCALATE"
        reason = "Compliance risk score exceeds threshold — senior review required"
    elif score >= 50:
        recommendation = "ENHANCED_MONITORING"
        reason = "Elevated compliance risk — increase monitoring frequency"
    elif score >= 30:
        recommendation = "CONTINUE"
        reason = "Moderate compliance status — standard monitoring continues"
    else:
        recommendation = "CLOSE_REVIEW"
        reason = "Good compliance standing — review period can be closed"

    ai_used = False
    if score >= 50:
        try:
            from claude_client import ClaudeClient
            client = ClaudeClient()
            company = app.get("company_name", "entity")
            prompt = (
                f"As a compliance system, recommend an action for {company}. "
                f"Compliance risk score: {score:.0f}/100. "
                f"Choose: ESCALATE, ENHANCED_MONITORING, CONTINUE, or CLOSE_REVIEW. "
                f"One sentence reason."
            )
            response = client.generate(prompt, max_tokens=150)
            if response and isinstance(response, str):
                resp_upper = response.strip().upper()
                for r in ("ESCALATE", "ENHANCED_MONITORING", "CONTINUE", "CLOSE_REVIEW"):
                    if resp_upper.startswith(r):
                        recommendation = r
                        reason = response.strip()
                        ai_used = True
                        break
        except Exception:
            pass

    return {
        "check": "Escalation/closure recommendation",
        "classification": "ai",
        "status": "completed",
        "recommendation": recommendation,
        "reason": reason,
        "compliance_risk_score": score,
        "ai_used": ai_used,
    }


def execute_ongoing_compliance(application_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 10: Ongoing Compliance Review — 11 checks (7R + 2H + 2AI).

    Verifies document currency, screening recency, policy applicability,
    condition compliance, filing deadlines; consolidates inter-agent findings;
    AI generates compliance narrative and escalation/closure recommendation.
    """
    db_path = context.get("db_path", "")
    data = _get_app_data(db_path, application_id)
    app = data["application"]
    documents = data["documents"]
    monitoring = _get_monitoring_data(db_path, application_id)
    alerts = monitoring["alerts"]
    reviews = monitoring["reviews"]
    run_id = str(uuid4())

    # 7 rule checks
    c1 = _check_document_currency(documents)
    c2 = _check_screening_recency(app)
    c3 = _check_policy_applicability(app)
    c4 = _check_condition_compliance(app, alerts)
    c5 = _check_filing_deadlines(app)
    c6 = _consolidate_inter_agent_findings(alerts, reviews)
    c7 = _track_remediation(alerts)
    rule_checks = [c1, c2, c3, c4, c5, c6, c7]

    # 2 hybrid checks
    c8 = _rescore_compliance_risk(rule_checks, app)
    c9 = _recommend_review_frequency(app, c8)
    hybrid_checks = [c8, c9]

    # 2 AI checks
    c10 = _generate_compliance_narrative(app, rule_checks + hybrid_checks)
    c11 = _recommend_escalation_closure(app, c8, rule_checks + hybrid_checks)
    ai_checks = [c10, c11]

    all_checks = rule_checks + hybrid_checks + ai_checks

    compliance_score = c8.get("compliance_risk_score", 30)
    recommendation = c11.get("recommendation", "CONTINUE")

    if recommendation == "ESCALATE":
        overall_status = AgentStatus.ISSUES_FOUND.value
        compliance_status = "non_compliant"
    elif recommendation == "ENHANCED_MONITORING":
        overall_status = AgentStatus.INCONCLUSIVE.value
        compliance_status = "at_risk"
    elif recommendation == "CONTINUE":
        overall_status = AgentStatus.INCONCLUSIVE.value
        compliance_status = "needs_attention"
    else:
        overall_status = AgentStatus.CLEAN.value
        compliance_status = "compliant"

    escalation_flag = recommendation == "ESCALATE"
    findings = []
    detected_issues = []

    if c1.get("expired_count", 0) > 0:
        issue = f"{c1['expired_count']} expired document(s)"
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "document_currency",
            "title": issue,
            "description": issue,
            "severity": Severity.HIGH.value,
            "confidence": 0.90,
            "source": "compliance_review",
            "evidence_refs": [],
        })
        detected_issues.append(issue)

    if not c2.get("is_recent", True):
        issue = f"Screening data is {c2.get('days_since_screening', '?')} days old (threshold: {_SCREENING_RECENCY_WARN_DAYS})"
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "screening_recency",
            "title": "Screening data is stale",
            "description": issue,
            "severity": Severity.MEDIUM.value,
            "confidence": 0.85,
            "source": "compliance_review",
            "evidence_refs": [],
        })
        detected_issues.append(issue)

    if c4.get("conditions_unmet", 0) > 0:
        issue = f"{c4['conditions_unmet']} unmet condition(s)"
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "condition_compliance",
            "title": issue,
            "description": issue,
            "severity": Severity.HIGH.value,
            "confidence": 0.85,
            "source": "compliance_review",
            "evidence_refs": [],
        })
        detected_issues.append(issue)

    if c6.get("open_alerts", 0) > 0:
        issue = f"{c6['open_alerts']} open alert(s) from monitoring agents"
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "inter_agent",
            "title": issue,
            "description": issue,
            "severity": Severity.MEDIUM.value,
            "confidence": 0.85,
            "source": "compliance_review",
            "evidence_refs": [],
        })
        detected_issues.append(issue)

    if not findings:
        findings.append({
            "finding_id": str(uuid4())[:12],
            "category": "compliance_review",
            "title": "Good compliance standing",
            "description": c10.get("narrative", f"Compliance review for {app.get('company_name', 'entity')}: compliant."),
            "severity": Severity.INFO.value,
            "confidence": 0.90,
            "source": "compliance_review",
            "evidence_refs": [],
        })

    review_freq = c9.get("recommended_frequency", "annual")
    next_review = c9.get("next_review_due")

    output = _base_output(AgentType.ONGOING_COMPLIANCE_REVIEW, "Agent 10: Ongoing Compliance Review", application_id, run_id)
    output.update({
        "status": overall_status,
        "confidence_score": 0.85,
        "findings": findings,
        "evidence": [{
            "evidence_id": str(uuid4())[:12],
            "evidence_type": "compliance_check",
            "source": "compliance_review_pipeline",
            "content_summary": f"Compliance review for {app.get('company_name', '')} — {len(all_checks)} checks",
            "reference": f"OCR-{run_id[:8]}",
            "verified": True,
        }],
        "detected_issues": detected_issues,
        "risk_indicators": [],
        "recommendation": c11.get("reason", "Maintain current monitoring"),
        "escalation_flag": escalation_flag,
        "escalation_reason": c11.get("reason") if escalation_flag else None,
        "checks_performed": all_checks,
        "compliance_status": compliance_status,
        "document_currency_results": c1.get("documents", []),
        "screening_recency_days": c2.get("days_since_screening"),
        "policy_applicability": c3.get("policy_triggers", []),
        "condition_compliance": c4.get("conditions", []),
        "filing_deadline_status": c5.get("deadlines", []),
        "inter_agent_findings": [{
            "alert_type": k,
            "count": v,
        } for k, v in c6.get("by_alert_type", {}).items()],
        "remediation_items": c7.get("items", []),
        "compliance_risk_score": compliance_score,
        "next_review_due": next_review,
        "recommended_review_frequency": review_freq,
        "compliance_narrative": c10.get("narrative"),
        "escalation_recommendation": c11,
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
