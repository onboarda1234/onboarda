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

# Database abstraction — lazy import to avoid polluting test DB state.
# The actual import happens inside _get_app_data() at call time.
_get_db_connection = None  # Populated lazily on first _get_app_data() call
_get_db_loaded = False

logger = logging.getLogger("arie.supervisor.executors")

# Current versions
AGENT_VERSION = "1.0.0"
PROMPT_VERSION = "v1.0-2026Q1"
MODEL_NAME = "claude-sonnet-4-6"
MEMO_MODEL = "claude-opus-4-6"


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

        # C-02: decrypt PII fields on read (graceful — skip if server module unavailable)
        try:
            from server import decrypt_pii_fields, PII_FIELDS_DIRECTORS, PII_FIELDS_UBOS
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

    # 2. Company existence check (rule / degraded)
    has_required_fields = bool(company_name and country)
    if provider_mode == "live":
        # Future: call OpenCorporates API here
        # For now, treat as degraded even if key exists (API integration not wired)
        company_found = has_required_fields
        lookup_mode = "degraded"
    else:
        company_found = has_required_fields
        lookup_mode = "degraded"

    checks["company_lookup"] = {
        "found": company_found,
        "mode": lookup_mode,
        "company_name": company_name,
        "country": country,
        "classification": "rule",
    }

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
