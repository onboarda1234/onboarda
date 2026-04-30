#!/usr/bin/env python3
"""
Onboarda — Back-End API Server
====================================
Single-file production-ready API server using Tornado + SQLite.
Provides: authentication, application CRUD, document uploads,
risk scoring, AI verification, audit trail, and user management.

Run:  python server.py
Env:  PORT=8080 SECRET_KEY=your-secret DB_PATH=./arie.db
"""

import os, sys, json, uuid, time, hashlib, re, base64, logging, secrets, smtplib
from dotenv import load_dotenv
load_dotenv()  # Load .env before any config reads
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Unified configuration — single source of truth for all env vars
from config import (
    ENVIRONMENT as _CFG_ENVIRONMENT,
    IS_PRODUCTION as _CFG_IS_PRODUCTION,
    JWT_SECRET as _CFG_JWT_SECRET,
    SECRET_KEY as _CFG_SECRET_KEY,
    DATABASE_URL as _CFG_DATABASE_URL,
    DB_PATH as _CFG_DB_PATH,
    PORT as _CFG_PORT,
    ANTHROPIC_API_KEY as _CFG_ANTHROPIC_API_KEY,
    CLAUDE_BUDGET_USD as _CFG_CLAUDE_BUDGET_USD,
    CLAUDE_MOCK_MODE as _CFG_CLAUDE_MOCK_MODE,
    SUMSUB_APP_TOKEN as _CFG_SUMSUB_APP_TOKEN,
    SUMSUB_SECRET_KEY as _CFG_SUMSUB_SECRET_KEY,
    SUMSUB_BASE_URL as _CFG_SUMSUB_BASE_URL,
    SUMSUB_LEVEL_NAME as _CFG_SUMSUB_LEVEL_NAME,
    SUMSUB_WEBHOOK_SECRET as _CFG_SUMSUB_WEBHOOK_SECRET,
    OPENSANCTIONS_API_KEY as _CFG_OPENSANCTIONS_API_KEY,
    OPENSANCTIONS_API_URL as _CFG_OPENSANCTIONS_API_URL,
    OPENCORPORATES_API_KEY as _CFG_OPENCORPORATES_API_KEY,
    OPENCORPORATES_API_URL as _CFG_OPENCORPORATES_API_URL,
    IP_GEOLOCATION_API_KEY as _CFG_IP_GEOLOCATION_API_KEY,
    IP_GEOLOCATION_API_URL as _CFG_IP_GEOLOCATION_API_URL,
    S3_BUCKET as _CFG_S3_BUCKET,
    UPLOAD_DIR as _CFG_UPLOAD_DIR,
    DEBUG as _CFG_DEBUG,
    LOG_FORMAT as _CFG_LOG_FORMAT,
    ALLOWED_ORIGIN as _CFG_ALLOWED_ORIGIN,
    validate_config,
)
from functools import wraps
from pathlib import Path
from urllib.parse import quote_plus

import bcrypt
import jwt
import tornado.ioloop
import tornado.web
import tornado.escape
import requests

import html
import asyncio
from concurrent.futures import ThreadPoolExecutor

# Import database module
from db import get_db as db_get_db, init_db as db_init_db, USE_POSTGRESQL, log_agent_execution

# S3 support (optional)
try:
    from s3_client import get_s3_client
    HAS_S3 = True
except ImportError:
    HAS_S3 = False

# Security hardening module — MANDATORY dependency
# If this import fails, the server MUST NOT start. These modules enforce:
# approval gates, password policy, file upload validation, token revocation,
# PII encryption, and production environment guards.
from security_hardening import (
    ApprovalGateValidator, validate_production_environment,
    PasswordPolicy, ApplicationSchema, FileUploadValidator,
    TokenRevocationList, token_revocation_list,
    get_safe_health_response, determine_screening_mode,
    store_screening_mode, PIIEncryptor
)
HAS_SECURITY_HARDENING = True  # Always True — module is now mandatory

# Claude AI integration (optional)
try:
    from claude_client import ClaudeClient, standardise_agent_output, compute_overall_status, AGENT_RISK_DIMENSIONS
    HAS_CLAUDE_CLIENT = True
except ImportError:
    HAS_CLAUDE_CLIENT = False
    ClaudeClient = None
    standardise_agent_output = None
    compute_overall_status = None
    AGENT_RISK_DIMENSIONS = {}

# Environment configuration module
from environment import (
    ENV, is_demo, is_production, is_staging, flags,
    enforce_startup_safety, get_environment_info,
    get_database_url, get_jwt_secret, get_cors_origin, get_s3_bucket
)

# ── Sprint 2: Extracted modules ──────────────────────────
from auth import (
    create_token, decode_token,
    sanitize_input, sanitize_dict,
    RateLimiter,
)
from rule_engine import (
    FATF_GREY, FATF_BLACK, SANCTIONED, SANCTIONED_COUNTRIES_FULL,
    ALLOWED_CURRENCIES, LOW_RISK, SECTOR_SCORES,
    HIGH_RISK_SECTORS, MINIMUM_MEDIUM_SECTORS, MEDIUM_RISK_SECTORS,
    HIGH_RISK_COUNTRIES, ALWAYS_RISK_DECREASING, ALWAYS_RISK_INCREASING,
    RISK_WEIGHTS, RISK_RANK,
    classify_country, score_sector, compute_risk_score, classify_risk_level,
    validate_risk_config,
    recompute_risk, recompute_risk_for_active_apps,
)
from validation_engine import (
    validate_compliance_memo,
    pre_validate_application,
    generate_fallback_memo,
)
from supervisor_engine import run_memo_supervisor
from memo_handler import build_compliance_memo
from decision_model import (
    build_from_application_decision,
    build_from_supervisor_verdict,
    save_decision_record,
    get_decision_records,
)
from edd_routing_policy import (
    evaluate_edd_routing as _evaluate_edd_routing,
    emit_routing_audit as _emit_edd_routing_audit,
)


# ── Priority B.2 / Workstream A: EDD route actuation ─────────────────
# When the deterministic EDD routing policy returns route="edd", this
# helper turns the policy decision into actual workflow reality:
#   1. upserts an ``edd_cases`` row at stage='triggered' for the
#      application (idempotent — at most one active EDD case per
#      application);
#   2. flips ``applications.status`` to ``edd_required`` so the case
#      leaves the Standard Review lane;
#   3. preserves routing context (policy_version, triggers,
#      evaluated_at, supervisor mandatory-escalation reasons,
#      origin=``policy_routing``) inside the EDD case's
#      ``trigger_notes`` and ``edd_notes`` so the audit trail is
#      reconstructable without replaying the pipeline.
# Returns ``{"case_id": int, "created": bool, "status_changed": bool}``.
# The caller owns the transaction (``db.commit()``).
_EDD_ACTUATION_TERMINAL_STAGES = ("edd_approved", "edd_rejected")


def _actuate_edd_routing(db, app_row, edd_routing, supervisor_result, user, client_ip=""):
    """Create or upsert an EDD case + flip application status when route=edd.

    Args:
        db: live DBConnection (caller commits).
        app_row: dict-like application record (must expose id, ref,
                 company_name, risk_level, risk_score, status).
        edd_routing: routing decision dict from evaluate_edd_routing.
        supervisor_result: supervisor result dict (may be empty).
        user: authenticated user dict for audit attribution.
        client_ip: optional client IP for audit row.

    Returns:
        dict with ``case_id`` (int or None), ``created`` (bool),
        ``status_changed`` (bool), and ``skipped`` (bool when route
        is not edd or app_row missing).
    """
    result = {"case_id": None, "created": False, "status_changed": False, "skipped": False}
    try:
        if not isinstance(edd_routing, dict) or edd_routing.get("route") != "edd":
            result["skipped"] = True
            return result
        if not app_row:
            result["skipped"] = True
            return result
        try:
            app_dict = dict(app_row)
        except Exception:
            app_dict = app_row
        application_id = app_dict.get("id")
        if not application_id:
            result["skipped"] = True
            return result

        # Idempotency: at most one active EDD case per application.
        placeholders = ",".join(["?"] * len(_EDD_ACTUATION_TERMINAL_STAGES))
        existing = db.execute(
            "SELECT id, stage FROM edd_cases WHERE application_id = ? "
            "AND stage NOT IN (" + placeholders + ") "
            "ORDER BY id ASC LIMIT 1",
            (application_id, *_EDD_ACTUATION_TERMINAL_STAGES),
        ).fetchone()

        triggers = list(edd_routing.get("triggers") or [])
        policy_version = edd_routing.get("policy_version", "")
        evaluated_at = edd_routing.get("evaluated_at", "")
        mandatory_reasons = list((supervisor_result or {}).get("mandatory_escalation_reasons") or [])
        trigger_notes = (
            "Auto-routed to EDD by policy " + str(policy_version)
            + " | triggers: " + ", ".join(triggers[:8])
            + (" | mandatory_escalation: " + ", ".join(mandatory_reasons[:6])
               if mandatory_reasons else "")
        )

        if existing:
            case_id = existing["id"]
            # Append a new audited note so reviewers see the routing
            # re-confirmed by this memo regeneration. We do not
            # re-create the case (idempotent).
            try:
                _row = db.execute(
                    "SELECT edd_notes FROM edd_cases WHERE id = ?",
                    (case_id,),
                ).fetchone()
                existing_notes = []
                if _row and _row.get("edd_notes"):
                    raw = _row["edd_notes"]
                    if isinstance(raw, str):
                        try:
                            existing_notes = json.loads(raw) or []
                        except Exception:
                            existing_notes = []
                    elif isinstance(raw, list):
                        existing_notes = list(raw)
                existing_notes.append({
                    "ts": datetime.now().isoformat(),
                    "author": (user or {}).get("name") or "system",
                    "source": "policy_routing",
                    "policy_version": policy_version,
                    "triggers": triggers,
                    "mandatory_escalation_reasons": mandatory_reasons,
                    "evaluated_at": evaluated_at,
                    "note": "Routing re-confirmed by memo regeneration",
                })
                db.execute(
                    "UPDATE edd_cases SET edd_notes = ? WHERE id = ?",
                    (json.dumps(existing_notes), case_id),
                )
            except Exception as _ne:
                logger.warning(
                    "Failed to append routing note to EDD case %s: %s",
                    case_id, _ne,
                )
            result["case_id"] = case_id
            result["created"] = False
        else:
            initial_note = json.dumps([{
                "ts": datetime.now().isoformat(),
                "author": (user or {}).get("name") or "system",
                "source": "policy_routing",
                "policy_version": policy_version,
                "triggers": triggers,
                "mandatory_escalation_reasons": mandatory_reasons,
                "evaluated_at": evaluated_at,
                "note": "EDD case auto-created by routing policy actuation",
            }])
            insert_params = (
                application_id,
                app_dict.get("company_name") or "",
                (app_dict.get("risk_level") or "HIGH"),
                app_dict.get("risk_score") or 0,
                "triggered",
                (user or {}).get("sub") or None,
                "policy_routing",
                trigger_notes,
                initial_note,
            )
            if USE_POSTGRES:
                row = db.execute(
                    "INSERT INTO edd_cases (application_id, client_name, "
                    "risk_level, risk_score, stage, assigned_officer, "
                    "trigger_source, trigger_notes, edd_notes) "
                    "VALUES (?,?,?,?,?,?,?,?,?) RETURNING id",
                    insert_params,
                ).fetchone()
                case_id = row["id"]
            else:
                db.execute(
                    "INSERT INTO edd_cases (application_id, client_name, "
                    "risk_level, risk_score, stage, assigned_officer, "
                    "trigger_source, trigger_notes, edd_notes) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    insert_params,
                )
                case_id = db.execute(
                    "SELECT last_insert_rowid() as id"
                ).fetchone()["id"]
            result["case_id"] = case_id
            result["created"] = True

        # Flip application status to edd_required if not already on
        # the EDD path. Preserve terminal/edd-approved states.
        current_status = (app_dict.get("status") or "")
        if current_status not in ("edd_required", "edd_approved", "approved", "rejected"):
            db.execute(
                "UPDATE applications SET status = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                ("edd_required", application_id),
            )
            result["status_changed"] = True

        # Write a single audit row so the actuation is independently
        # reconstructable (separate from the routing.evaluated row).
        try:
            db.execute(
                "INSERT INTO audit_log (user_id, user_name, user_role, "
                "action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                (
                    (user or {}).get("sub") or "system",
                    (user or {}).get("name") or "system",
                    (user or {}).get("role") or "system",
                    "edd_routing.actuated",
                    "application:" + str(app_dict.get("ref") or application_id),
                    json.dumps({
                        "policy_version": policy_version,
                        "route": edd_routing.get("route"),
                        "triggers": triggers,
                        "mandatory_escalation_reasons": mandatory_reasons,
                        "evaluated_at": evaluated_at,
                        "edd_case_id": result["case_id"],
                        "edd_case_created": result["created"],
                        "status_changed": result["status_changed"],
                        "origin": "policy_routing",
                    }, default=str, sort_keys=True),
                    client_ip or "",
                ),
            )
        except Exception as _ae:
            logger.warning("Failed to write edd_routing.actuated audit row: %s", _ae)
    except Exception as _err:
        # Fail-closed semantics: if actuation cannot complete, log
        # loudly. The approval gate already refuses approval when
        # route=edd and status is not edd_required, so the case
        # cannot silently leak through.
        logger.error("EDD route actuation failed: %s", _err, exc_info=True)
    return result
from branding import BRAND, get_status_label
from party_utils import (
    _pii_encryptor, _pii_encryption_ok,
    extract_fernet_token, encrypt_pii_fields, decrypt_pii_fields,
    PII_FIELDS_DIRECTORS, PII_FIELDS_UBOS, PII_FIELDS_APPLICATIONS,
    parse_json_field, hydrate_party_record, get_application_parties,
    get_application_parties_batch,
)
from prescreening.normalize import (
    compose_source_of_funds_summary as _compose_source_of_funds_summary,
    first_non_empty as _first_non_empty,
    is_meaningful_value as _is_meaningful_value,
    merge_prescreening_sources as _merge_prescreening_sources,
    normalize_prescreening_data as _normalize_prescreening_data,
    normalize_saved_session_prescreening as _normalize_saved_session_prescreening,
    resolve_application_company_name as _resolve_application_company_name,
    safe_json_loads as _safe_json_loads,
)
from prescreening.risk_inputs import build_prescreening_risk_input

# Layered document verification engine (Agent 1)
try:
    from document_verification import verify_document_layered, to_legacy_result, _canonicalise_country
    HAS_DOC_VERIFICATION = True
except ImportError:
    HAS_DOC_VERIFICATION = False
    verify_document_layered = None
    to_legacy_result = None
    _canonicalise_country = None

# Sprint 3: Server-side PDF generation
try:
    from pdf_generator import generate_memo_pdf
    HAS_PDF_GENERATOR = True
except ImportError:
    HAS_PDF_GENERATOR = False
    logging.getLogger("arie").warning("PDF generator not available — install weasyprint")

# Supervisor framework
try:
    from supervisor.api import setup_supervisor, get_supervisor_routes, get_supervisor
    from supervisor.agent_executors import register_all_executors
    SUPERVISOR_AVAILABLE = True
except ImportError:
    SUPERVISOR_AVAILABLE = False
    logging.getLogger("arie").warning("Supervisor framework not available — install pydantic>=2.0")

# ── Logging ───────────────────────────────────────────────
# JSON structured logging for production, human-readable for development
_log_format = _CFG_LOG_FORMAT  # "json" or "text"

class JSONFormatter(logging.Formatter):
    """JSON structured log formatter for production log aggregation."""
    def format(self, record):
        log_entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)

if _log_format == "json" or _CFG_IS_PRODUCTION:
    _handler = logging.StreamHandler()
    _handler.setFormatter(JSONFormatter())
    logging.root.handlers = []
    logging.root.addHandler(_handler)
    logging.root.setLevel(logging.INFO)
else:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

logger = logging.getLogger("arie")

# ── Configuration (from unified config module) ────────────
PORT = _CFG_PORT
ENVIRONMENT = _CFG_ENVIRONMENT
SECRET_KEY = _CFG_SECRET_KEY

DATABASE_URL = _CFG_DATABASE_URL
DB_PATH = _CFG_DB_PATH
USE_POSTGRES = bool(DATABASE_URL)

# PostgreSQL adapter (optional import)
if USE_POSTGRES:
    try:
        import psycopg2
        import psycopg2.extras
        logger.info("PostgreSQL mode enabled via DATABASE_URL")
    except ImportError:
        logger.error("DATABASE_URL set but psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)

UPLOAD_DIR = _CFG_UPLOAD_DIR
RESOURCE_UPLOAD_DIR = os.path.join(UPLOAD_DIR, "resources")
REGULATORY_UPLOAD_DIR = os.path.join(UPLOAD_DIR, "regulatory_intelligence")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..")  # Serves from arie-backend parent
MAX_UPLOAD_MB = 10
TOKEN_EXPIRY_HOURS = 24

# ── External API Keys (from unified config module) ────────
OPENSANCTIONS_API_KEY = _CFG_OPENSANCTIONS_API_KEY
OPENSANCTIONS_API_URL = _CFG_OPENSANCTIONS_API_URL
OPENCORPORATES_API_KEY = _CFG_OPENCORPORATES_API_KEY
OPENCORPORATES_API_URL = _CFG_OPENCORPORATES_API_URL
IP_GEOLOCATION_API_KEY = _CFG_IP_GEOLOCATION_API_KEY
IP_GEOLOCATION_API_URL = _CFG_IP_GEOLOCATION_API_URL

# Sumsub KYC/Identity Verification
SUMSUB_APP_TOKEN = _CFG_SUMSUB_APP_TOKEN
SUMSUB_SECRET_KEY = _CFG_SUMSUB_SECRET_KEY
SUMSUB_BASE_URL = _CFG_SUMSUB_BASE_URL
SUMSUB_LEVEL_NAME = _CFG_SUMSUB_LEVEL_NAME
SUMSUB_WEBHOOK_SECRET = _CFG_SUMSUB_WEBHOOK_SECRET


def mask_email(email: str) -> str:
    """Mask email addresses for safe logging (PII redaction)."""
    if not email or '@' not in str(email):
        return '***'
    local, domain = str(email).rsplit('@', 1)
    return f"{local[0]}***@{domain}"


def hash_reset_token(token: str) -> str:
    """Hash password reset tokens before storing them."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _revoke_all_client_sessions(db, user_id):
    """
    Best-effort revocation helper: invalidate any JWT that was issued for *user_id*.

    Since JWTs are stateless the only mechanism we have is the token revocation
    list, which tracks individual JTI values.  We don't store issued tokens
    server-side, so there is nothing to iterate over.  Instead this function
    records a "user-level" revocation entry keyed on the user_id itself.
    ``decode_token`` already validates the per-JTI revocation list, and by
    also checking the per-user key we can block all tokens for a given user
    after a password reset / change.

    The entry expires after TOKEN_EXPIRY_HOURS so it is automatically cleaned
    up — any JWT issued before the password change will have expired by then.
    """
    import time as _time
    from auth import TOKEN_EXPIRY_HOURS
    expires_at = _time.time() + TOKEN_EXPIRY_HOURS * 3600
    # Use a synthetic JTI that encode_token never generates but that the
    # revocation list can match on via the helper below.
    user_jti = f"user:{user_id}"
    token_revocation_list.revoke(user_jti, expires_at)


def build_regulatory_analysis(doc: dict) -> dict:
    """Deterministic backend analysis for regulatory intelligence documents."""
    text = str(doc.get("source_text") or "").lower()
    title = str(doc.get("title") or "regulatory document")
    regulator = str(doc.get("regulator") or "Unknown regulator")
    jurisdiction = str(doc.get("jurisdiction") or "Unknown jurisdiction")
    doc_type = str(doc.get("doc_type") or "Document")
    effective_date = str(doc.get("effective_date") or "") or "TBD"

    keyword_groups = {
        "onboarding": ["onboard", "application", "client", "customer"],
        "kyc": ["kyc", "document", "verification", "identity"],
        "sanctions": ["sanction", "pep", "list", "designation", "watchlist"],
        "riskScoring": ["risk", "score", "rating", "classification"],
        "edd": ["edd", "enhanced", "due diligence", "high risk"],
        "monitoring": ["monitor", "periodic", "ongoing", "review cycle", "review"],
        "reporting": ["report", "fiu", "disclosure", "suspicious", "filing"],
    }
    affected_areas = {area: any(term in text for term in terms) for area, terms in keyword_groups.items()}
    if not any(affected_areas.values()):
        affected_areas["onboarding"] = True

    suggestions = []

    def add_suggestion(suggestion_type: str, short_text: str, detail: str):
        suggestion_id = f"S{len(suggestions) + 1:03d}"
        suggestions.append({
            "id": suggestion_id,
            "type": suggestion_type,
            "text": short_text,
            "detail": detail,
            "status": "pending",
            "reviewedBy": None,
            "reviewedAt": None,
            "notes": "",
        })

    if affected_areas["riskScoring"]:
        add_suggestion(
            "modify",
            "Review and update risk scoring parameters based on new guidance",
            "The update references risk assessment methodology. Review whether current RegMind scoring dimensions and thresholds capture the new risk factors, and document any approved model changes before deployment.",
        )
    if affected_areas["sanctions"]:
        add_suggestion(
            "flag",
            "Update screening lists and sanctions interpretation controls",
            "The update references sanctions, PEP, or designation changes. Confirm source lists and screening interpretation controls are aligned before relying on automated screening outcomes.",
        )
    if affected_areas["kyc"]:
        add_suggestion(
            "add",
            "Review onboarding document requirements and verification checks",
            "The update introduces or affects documentary obligations. Review onboarding checklists and automated document verification controls so required evidence is collected and validated consistently.",
        )
    if affected_areas["reporting"]:
        add_suggestion(
            "add",
            "Assess reporting thresholds and filing workflows",
            "The update appears to introduce or change reporting obligations. Validate whether SAR/FIU or equivalent filing workflows, thresholds, and turnaround expectations require configuration changes.",
        )
    if "jurisdict" in text or "country" in text or "grey" in text or "black" in text:
        add_suggestion(
            "flag",
            "Review country risk references and affected-client exposure",
            "The update affects jurisdictional treatment. Review country risk references and identify existing clients or onboarding cases with exposure to the affected jurisdictions.",
        )
    if affected_areas["edd"]:
        add_suggestion(
            "escalate",
            "Review EDD trigger criteria and escalation routing",
            "The update affects enhanced due diligence expectations. Confirm EDD triggers, officer routing, and approval gating remain aligned with the new obligations.",
        )
    if affected_areas["monitoring"]:
        add_suggestion(
            "modify",
            "Review monitoring cadence and ongoing review rules",
            "The update affects monitoring or review obligations. Confirm periodic review frequency, alerting, and monitoring procedures are aligned before relying on current settings.",
        )
    if not suggestions:
        add_suggestion(
            "modify",
            "Perform documented gap assessment against current compliance procedures",
            "A manual gap assessment is recommended to confirm whether this update changes onboarding, screening, memo, or monitoring controls before the effective date.",
        )
        add_suggestion(
            "add",
            "Brief compliance staff and record implementation decisions",
            "Document the implementation decision, obtain human approval for any policy changes, and brief the compliance team before the update takes effect.",
        )

    client_types = [f"All regulated entities under {regulator}"]
    if affected_areas["sanctions"]:
        client_types.append("Clients with exposure to designated jurisdictions or persons")
    if affected_areas["edd"]:
        client_types.append("High-risk and very high-risk clients")
    if affected_areas["riskScoring"]:
        client_types.append("Clients whose onboarding risk classification may change")
    if affected_areas["monitoring"]:
        client_types.append("Clients subject to enhanced or periodic review obligations")
    if "payment" in text:
        client_types.append("Payment institutions and money service businesses")
    if "fund" in text or "invest" in text:
        client_types.append("Investment funds and asset managers")

    obligations = [
        f"Review and assess the regulatory requirements outlined in {title}",
        "Determine applicability to RegMind's current client base, controls, and operating procedures",
        f"Implement any approved changes before the effective date ({effective_date})",
        "Brief the compliance team and retain an implementation audit trail",
        "Record the final implementation decision with human approval",
    ]

    impacted_labels = [label for label, hit in affected_areas.items() if hit]
    if impacted_labels:
        if len(impacted_labels) == 1:
            impacted_text = impacted_labels[0]
        else:
            impacted_text = ", ".join(impacted_labels[:-1]) + " and " + impacted_labels[-1]
    else:
        impacted_text = "onboarding"

    # Heuristic confidence: base 35, +4 per suggestion, +3 per area hit, capped at 82.
    # Intentionally conservative — this is keyword matching, not semantic analysis.
    confidence = min(82, 35 + (len(suggestions) * 4) + sum(3 for hit in affected_areas.values() if hit))

    return {
        "summary": (
            f"This {doc_type.lower()} from {regulator} ({jurisdiction}) introduces changes affecting {impacted_text}. "
            f"{len(suggestions)} implementation suggestion(s) require human review before any control changes are made."
        ),
        "keyObligations": obligations,
        "affectedAreas": affected_areas,
        "suggestions": suggestions,
        "affectedClientTypes": client_types,
        "confidence": confidence,
        "analysedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "analysisSource": "backend_rule_assisted",
        "humanReviewRequired": True,
    }


def build_regulatory_workflow_state(doc: dict) -> str:
    """Return a truthful UI workflow state for regulatory intelligence records."""
    status = str(doc.get("status") or "")
    analysis_source = str(doc.get("analysis_source") or "")
    source_text = bool(doc.get("source_text"))
    file_name = bool(doc.get("file_name"))

    if analysis_source == "manual_review_required" or status == "review_required":
        return "manual_text_required"
    if analysis_source == "backend_rule_assisted" or status == "analysed":
        return "heuristic_review"
    if file_name or source_text or status == "uploaded":
        return "stored_only"
    # Conservative default: unknown records should not appear as "analysis available".
    return "stored_only"


def send_portal_email(to_addr: str, subject: str, text_body: str) -> bool:
    """Send a transactional portal email via SMTP if configured."""
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    smtp_from = BRAND.get("email_from_address") or smtp_user
    smtp_from_name = BRAND.get("email_from_name") or "Onboarda"

    if not smtp_host or not smtp_user:
        logger.warning("SMTP not configured. Transactional email not sent: %s", subject)
        return False

    msg = MIMEMultipart()
    msg["From"] = f"{smtp_from_name} <{smtp_from}>"
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body, "plain"))

    try:
        server = smtplib.SMTP(smtp_host, smtp_port)
        server.starttls()
        if smtp_password:
            server.login(smtp_user, smtp_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as exc:
        logger.error("Transactional email failed for %s: %s", mask_email(to_addr), exc)
        return False


def _safe_verification_status(checks: list, raw_status: str = None) -> str:
    """
    Improvement 9: No Result = No Pass.
    Returns NOT_RUN if no checks exist. Never returns 'verified'/'pass' without evidence.
    """
    if not checks:
        return "not_run"
    if raw_status in ("verified", "pass"):
        # Verify that checks actually support a pass
        has_fail = any((c.get("result") or "").lower() == "fail" for c in checks)
        has_warn = any((c.get("result") or "").lower() == "warn" for c in checks)
        if has_fail:
            return "flagged"
        if has_warn:
            return "flagged"
        return raw_status
    return raw_status or "not_run"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(RESOURCE_UPLOAD_DIR, exist_ok=True)
os.makedirs(REGULATORY_UPLOAD_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

# ── C-02: PII Encryption ─────────────────────────────────────
# PII encryptor, field constants, encrypt/decrypt helpers, and
# party-query utilities now live in party_utils.py (imported above)
# to avoid circular imports (rule_engine → server → Prometheus clash).


def safe_json_loads(val):
    """Safely parse JSON — handles PostgreSQL JSONB (already dict) and SQLite TEXT (string)."""
    return _safe_json_loads(val)


# ── Priority C: Draft form_data at-rest protection ───────────
# Drafts persisted in client_sessions.form_data may contain PII
# (contact email, names, DOB, nationality, ownership %, etc.).
# We encrypt the whole JSON blob using the existing PIIEncryptor
# (same Fernet key used for director/UBO PII fields). When the
# encryptor is not configured (dev/test without PII_ENCRYPTION_KEY)
# we fall back to plaintext JSON so existing behaviour is preserved.
# Reads transparently handle both encrypted and legacy plaintext
# rows so deployment requires no data migration.

DRAFT_FORM_DATA_ENCRYPTED_PREFIX = "enc:v1:"
DRAFT_FORM_DATA_ENCRYPTED_JSON_KEY = "__draft_form_data_enc_v1"


def encrypt_draft_form_data(payload) -> str:
    """Serialize a draft form_data payload, encrypting at rest if a PII key is available."""
    raw = json.dumps(payload or {})
    if _pii_encryptor is None:
        return raw
    try:
        token = _pii_encryptor.encrypt(raw)
    except Exception as exc:
        logger.warning("Draft form_data encryption failed, falling back to plaintext: %s", exc)
        return raw
    # Store encrypted payloads as JSON so PostgreSQL JSONB columns accept them.
    return json.dumps({DRAFT_FORM_DATA_ENCRYPTED_JSON_KEY: token})


def decrypt_draft_form_data(stored) -> dict:
    """Decrypt + parse a stored client_sessions.form_data value.

    Accepts:
      * dict (PostgreSQL JSONB legacy rows) → returned as-is
      * plaintext JSON strings (legacy rows / encryptor disabled)
      * "enc:v1:<fernet_token>" strings written by encrypt_draft_form_data
    Always returns a dict; never raises.
    """
    no_token = object()

    def _decrypt_token(token):
        if not isinstance(token, str) or not token.strip():
            return no_token
        if _pii_encryptor is None:
            logger.warning("Encrypted draft form_data encountered but PII encryptor is not configured")
            return {}
        try:
            plaintext = _pii_encryptor.decrypt(token)
        except Exception as exc:
            logger.warning("Draft form_data decryption failed: %s", exc)
            return {}
        return safe_json_loads(plaintext) or {}

    if stored is None or stored == "":
        return {}
    if isinstance(stored, dict):
        decrypted = _decrypt_token(stored.get(DRAFT_FORM_DATA_ENCRYPTED_JSON_KEY))
        if decrypted is not no_token:
            return decrypted
        return stored
    if isinstance(stored, (bytes, bytearray)):
        try:
            stored = stored.decode("utf-8")
        except Exception:
            return {}
    if not isinstance(stored, str):
        return {}
    if stored.startswith(DRAFT_FORM_DATA_ENCRYPTED_PREFIX):
        return _decrypt_token(stored[len(DRAFT_FORM_DATA_ENCRYPTED_PREFIX):]) or {}
    decoded = safe_json_loads(stored)
    if isinstance(decoded, dict):
        decrypted = _decrypt_token(decoded.get(DRAFT_FORM_DATA_ENCRYPTED_JSON_KEY))
        if decrypted is not no_token:
            return decrypted
        return decoded
    # Legacy plaintext JSON (or raw dict-like string)
    if isinstance(decoded, str) and decoded.startswith(DRAFT_FORM_DATA_ENCRYPTED_PREFIX):
        return _decrypt_token(decoded[len(DRAFT_FORM_DATA_ENCRYPTED_PREFIX):]) or {}
    return {}


def _draft_value_is_meaningful(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, dict):
        return any(_draft_value_is_meaningful(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_draft_value_is_meaningful(v) for v in value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return True


def _draft_payload_is_meaningful(payload) -> bool:
    """Reject completely empty drafts (no form fields, no party rows)."""
    if not isinstance(payload, dict) or not payload:
        return False
    # Metadata-only payloads must not count as real draft content.
    interesting_keys = (
        "prescreening",
        "prescreening_data",
        "directors",
        "ubos",
        "intermediaries",
        "intermediary_shareholders",
        "servicesRequired",
        "accountPurposes",
        "currencies",
        "countriesOfOperation",
        "targetMarkets",
        "kycPersons",
        "uploadedDocs",
        "company_name",
        "entity_name",
        "registered_entity_name",
        "country",
        "sector",
        "entity_type",
        "ownership_structure",
        "brn",
    )
    return any(_draft_value_is_meaningful(payload.get(key)) for key in interesting_keys)


def _extract_save_resume_form_data(payload) -> dict:
    """Accept both modern {form_data: {...}} and legacy/root payload shapes."""
    if not isinstance(payload, dict):
        return {}
    form_data = payload.get("form_data")
    if isinstance(form_data, dict):
        return form_data

    extracted = {}
    for key in (
        "prescreening",
        "prescreening_data",
        "directors",
        "ubos",
        "intermediaries",
        "intermediary_shareholders",
        "servicesRequired",
        "accountPurposes",
        "currencies",
        "countriesOfOperation",
        "targetMarkets",
        "kycPersons",
        "uploadedDocs",
        "company_name",
        "entity_name",
        "registered_entity_name",
        "country",
        "sector",
        "entity_type",
        "ownership_structure",
        "brn",
    ):
        if key in payload:
            extracted[key] = payload.get(key)
    return extracted


def first_non_empty(*values):
    """Return the first non-empty string-like value, preserving non-string scalars."""
    return _first_non_empty(*values)


def compose_source_of_funds_summary(prescreening: dict) -> str:
    return _compose_source_of_funds_summary(prescreening)


def normalize_prescreening_data(data: dict, existing=None) -> dict:
    """Merge incoming prescreening data with existing state and normalize core aliases."""
    return _normalize_prescreening_data(data, existing=existing)


def is_meaningful_value(value) -> bool:
    return _is_meaningful_value(value)


def normalize_saved_session_prescreening(form_data) -> dict:
    """Backfill authoritative prescreening aliases from save/resume session payloads."""
    return _normalize_saved_session_prescreening(form_data)


def merge_prescreening_sources(primary, fallback) -> dict:
    """Merge prescreening sources while preserving authoritative stored values over backfill."""
    return _merge_prescreening_sources(primary, fallback)


def load_saved_session_prescreening(db, app_record) -> dict:
    """Load the latest saved portal form snapshot for an application, if any."""
    app_id = app_record.get("id") if isinstance(app_record, dict) else None
    session = None
    if app_id:
        session = db.execute(
            "SELECT form_data FROM client_sessions WHERE application_id=? ORDER BY updated_at DESC LIMIT 1",
            (app_id,)
        ).fetchone()
    if not session:
        return {}
    if isinstance(session, dict):
        form_data = session.get("form_data")
    else:
        form_data = session["form_data"]
    decoded = decrypt_draft_form_data(form_data)
    return normalize_saved_session_prescreening(decoded)


def resolve_application_company_name(data: dict, prescreening_data: dict, fallback="") -> str:
    """Resolve the authoritative legal entity name for application persistence."""
    return _resolve_application_company_name(data, prescreening_data, fallback=fallback)


def build_full_name(record: dict) -> str:
    first_name = first_non_empty(record.get("first_name"))
    last_name = first_non_empty(record.get("last_name"))
    if first_name or last_name:
        return f"{first_name} {last_name}".strip()
    return first_non_empty(record.get("full_name"))


def normalize_is_pep(value, default="No") -> str:
    normalized = first_non_empty(value, default)
    return "Yes" if str(normalized).strip().lower() in ("yes", "true", "1") else "No"


def _validate_date_of_birth(dob_str):
    """Validate a date of birth string. Returns the cleaned date string or empty string if invalid.
    Rejects future dates and implausible ages (< 16 or > 120 years old).
    """
    if not dob_str or not isinstance(dob_str, str):
        return ""
    dob_str = dob_str.strip()
    if not dob_str:
        return ""
    try:
        parsed = datetime.strptime(dob_str, "%Y-%m-%d").date()
    except ValueError:
        # Try other common formats
        for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"):
            try:
                parsed = datetime.strptime(dob_str, fmt).date()
                break
            except ValueError:
                continue
        else:
            return ""  # Unparseable → store empty rather than garbage
    today = datetime.now(timezone.utc).date()
    if parsed > today:
        return ""  # Future date → invalid
    from datetime import date as _date
    age_years = (today - parsed).days / 365.25
    if age_years < 16 or age_years > 120:
        return ""  # Implausible age → store empty
    return parsed.isoformat()  # Normalize to YYYY-MM-DD


def _check_dob_not_future(dob_str):
    """Raise ValueError if dob_str parses as a valid date that is in the future."""
    if not dob_str or not isinstance(dob_str, str):
        return
    dob_str = dob_str.strip()
    if not dob_str:
        return
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(dob_str, fmt).date()
            today = datetime.now(timezone.utc).date()
            if parsed > today:
                raise ValueError(f"Date of birth cannot be in the future: {dob_str!r}")
            return
        except ValueError as exc:
            if "future" in str(exc):
                raise
            continue


def store_application_parties(db, application_id, directors=None, ubos=None, intermediaries=None):
    """Store party records with validation of DOB and ownership_pct."""
    if directors is not None:
        db.execute("DELETE FROM directors WHERE application_id = ?", (application_id,))
        for director in directors:
            full_name = build_full_name(director)
            if not full_name:
                continue
            # Validate DOB if provided
            dob = director.get("date_of_birth", "")
            if dob:
                _check_dob_not_future(dob)
                dob = _validate_date_of_birth(dob)
            # W2-6: Normalize nationality if canonical lookup is available
            raw_nat = director.get("nationality", "")
            if raw_nat and _canonicalise_country:
                canon = _canonicalise_country(raw_nat)
                if canon:
                    director = dict(director)
                    director["nationality"] = canon
            encrypted = encrypt_pii_fields(director, PII_FIELDS_DIRECTORS)
            db.execute("""
                INSERT INTO directors (
                    application_id, person_key, first_name, last_name, full_name,
                    nationality, is_pep, pep_declaration, date_of_birth
                ) VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                application_id,
                director.get("person_key"),
                director.get("first_name", ""),
                director.get("last_name", ""),
                full_name,
                encrypted.get("nationality", ""),
                normalize_is_pep(director.get("is_pep", "No")),
                json.dumps(parse_json_field(director.get("pep_declaration"), {})),
                dob,
            ))
    if ubos is not None:
        db.execute("DELETE FROM ubos WHERE application_id = ?", (application_id,))
        for ubo in ubos:
            full_name = build_full_name(ubo)
            if not full_name:
                continue
            # Validate DOB if provided
            dob = ubo.get("date_of_birth", "")
            if dob:
                _check_dob_not_future(dob)
                dob = _validate_date_of_birth(dob)
            # Validate ownership_pct range (0–100)
            raw_pct = ubo.get("ownership_pct", 0)
            try:
                pct_val = float(raw_pct) if raw_pct else 0.0
            except (ValueError, TypeError):
                pct_val = 0.0
            pct_val = max(0.0, min(100.0, pct_val))
            # W2-6: Normalize nationality if canonical lookup is available
            raw_nat = ubo.get("nationality", "")
            if raw_nat and _canonicalise_country:
                canon = _canonicalise_country(raw_nat)
                if canon:
                    ubo = dict(ubo)
                    ubo["nationality"] = canon
            encrypted = encrypt_pii_fields(ubo, PII_FIELDS_UBOS)
            db.execute("""
                INSERT INTO ubos (
                    application_id, person_key, first_name, last_name, full_name,
                    nationality, ownership_pct, is_pep, pep_declaration, date_of_birth
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                application_id,
                ubo.get("person_key"),
                ubo.get("first_name", ""),
                ubo.get("last_name", ""),
                full_name,
                encrypted.get("nationality", ""),
                pct_val,
                normalize_is_pep(ubo.get("is_pep", "No")),
                json.dumps(parse_json_field(ubo.get("pep_declaration"), {})),
                dob,
            ))
    if intermediaries is not None:
        db.execute("DELETE FROM intermediaries WHERE application_id = ?", (application_id,))
        for intermediary in intermediaries:
            entity_name = first_non_empty(intermediary.get("entity_name"), intermediary.get("full_name"))
            if not entity_name:
                continue
            intermediary_id = first_non_empty(intermediary.get("id"), secrets.token_hex(8))
            db.execute("""
                INSERT INTO intermediaries (id, application_id, person_key, entity_name, jurisdiction, ownership_pct)
                VALUES (?,?,?,?,?,?)
            """, (
                intermediary_id,
                application_id,
                intermediary.get("person_key"),
                entity_name,
                intermediary.get("jurisdiction", ""),
                intermediary.get("ownership_pct", 0),
            ))


def resolve_application_person(db, application_id, person_ref):
    if not application_id or not person_ref:
        return None

    director = db.execute("""
        SELECT id, person_key, first_name, last_name, full_name, nationality, is_pep, pep_declaration, date_of_birth
        FROM directors WHERE application_id = ? AND (id = ? OR person_key = ?)
        LIMIT 1
    """, (application_id, person_ref, person_ref)).fetchone()
    if director:
        result = hydrate_party_record(director, PII_FIELDS_DIRECTORS)
        result["person_type"] = "director"
        result["entity_type"] = "Person"
        return result

    ubo = db.execute("""
        SELECT id, person_key, first_name, last_name, full_name, nationality, ownership_pct, is_pep, pep_declaration, date_of_birth
        FROM ubos WHERE application_id = ? AND (id = ? OR person_key = ?)
        LIMIT 1
    """, (application_id, person_ref, person_ref)).fetchone()
    if ubo:
        result = hydrate_party_record(ubo, PII_FIELDS_UBOS)
        result["person_type"] = "ubo"
        result["entity_type"] = "Person"
        return result

    intermediary = db.execute("""
        SELECT id, person_key, entity_name, jurisdiction, ownership_pct
        FROM intermediaries WHERE application_id = ? AND (id = ? OR person_key = ?)
        LIMIT 1
    """, (application_id, person_ref, person_ref)).fetchone()
    if intermediary:
        result = dict(intermediary)
        result["full_name"] = result.get("entity_name", "")
        result["person_type"] = "intermediary"
        result["entity_type"] = "Company"
        return result

    return None


def resolve_document_subject_context(db, app, doc):
    person_record = None
    if app and doc.get("person_id"):
        person_record = resolve_application_person(db, app["id"], doc["person_id"])
    if not person_record and app:
        dir_match = re.search(r'_dir(\d+)$', doc.get("doc_type", ""))
        ubo_match = re.search(r'_ubo(\d+)$', doc.get("doc_type", ""))
        int_match = re.search(r'_int(\d+)$', doc.get("doc_type", ""))
        if dir_match:
            idx = int(dir_match.group(1)) - 1
            directors = get_application_parties(db, app["id"])[0]
            if 0 <= idx < len(directors):
                person_record = directors[idx]
                person_record["person_type"] = "director"
                person_record["entity_type"] = "Person"
        elif ubo_match:
            idx = int(ubo_match.group(1)) - 1
            ubos = get_application_parties(db, app["id"])[1]
            if 0 <= idx < len(ubos):
                person_record = ubos[idx]
                person_record["person_type"] = "ubo"
                person_record["entity_type"] = "Person"
        elif int_match:
            idx = int(int_match.group(1)) - 1
            intermediaries = get_application_parties(db, app["id"])[2]
            if 0 <= idx < len(intermediaries):
                person_record = intermediaries[idx]
                person_record["person_type"] = "intermediary"
                person_record["entity_type"] = "Company"

    raw_doc_type = doc.get("doc_type", "general")
    base_doc_type = raw_doc_type
    if base_doc_type.startswith("intermediary_"):
        base_doc_type = base_doc_type[len("intermediary_"):]
    base_doc_type = re.sub(r'_(dir|ubo|inter)\d+$', '', base_doc_type)

    company_doc_types = {
        "cert_inc", "memarts", "reg_sh", "reg_dir", "fin_stmt", "board_res",
        "structure_chart", "poa", "bankref", "licence", "contracts",
        "source_wealth", "source_funds", "bank_statements", "aml_policy",
    }
    if person_record and person_record.get("person_type") == "intermediary":
        doc_category = "company"
        subject_type = "intermediary_company"
    elif person_record and person_record.get("person_type") in ("director", "ubo"):
        doc_category = "kyc"
        subject_type = person_record.get("person_type")
    else:
        doc_category = "company" if raw_doc_type in company_doc_types else "kyc"
        subject_type = "application_company" if doc_category == "company" else "person"

    return {
        "person_record": person_record,
        "raw_doc_type": raw_doc_type,
        "base_doc_type": base_doc_type,
        "doc_category": doc_category,
        "subject_type": subject_type,
    }


def build_document_verification_context(db, app, doc):
    context = resolve_document_subject_context(db, app, doc)
    person_record = context["person_record"]
    doc_category = context["doc_category"]

    stored_prescreening = parse_json_field(app.get("prescreening_data") if app else None, {})
    saved_session_prescreening = load_saved_session_prescreening(db, app) if app else {}
    prescreening_data = merge_prescreening_sources(stored_prescreening, saved_session_prescreening)
    if not isinstance(prescreening_data, dict):
        prescreening_data = {}

    entity_name = app.get("company_name", "") if app else ""
    person_name = ""
    directors_list = []
    ubos_list = []

    if app and context["subject_type"] == "application_company":
        dir_rows = db.execute("SELECT full_name FROM directors WHERE application_id=? ORDER BY id", (app["id"],)).fetchall()
        directors_list = [r["full_name"] for r in dir_rows if r.get("full_name")]
        ubo_rows = db.execute("SELECT full_name FROM ubos WHERE application_id=? ORDER BY id", (app["id"],)).fetchall()
        ubos_list = [r["full_name"] for r in ubo_rows if r.get("full_name")]

    if person_record and doc_category == "kyc":
        prescreening_data = dict(prescreening_data)
        pep_decl = parse_json_field(person_record.get("pep_declaration"), {}) or {}
        person_name = person_record.get("full_name", "")
        person_fields = {
            "full_name": person_name,
            "date_of_birth": person_record.get("date_of_birth", ""),
            "nationality": person_record.get("nationality", ""),
            "residential_address": person_record.get("residential_address", ""),
            "role": person_record.get("person_type", ""),
            "source_of_wealth_detail": pep_decl.get("source_of_wealth_detail", ""),
            "pep_function": pep_decl.get("public_function", ""),
            "pep_net_worth": pep_decl.get("estimated_assets_usd", ""),
            "pep_source_of_funds": pep_decl.get("source_of_wealth_detail", ""),
        }
        for key, value in person_fields.items():
            if value not in (None, "", {}, []):
                prescreening_data[key] = value
    elif person_record and person_record.get("person_type") == "intermediary":
        prescreening_data = dict(prescreening_data)
        entity_name = first_non_empty(person_record.get("entity_name"), person_record.get("full_name"), entity_name)
        intermediary_fields = {
            "registered_entity_name": entity_name,
            "company_name": entity_name,
            "country_of_incorporation": person_record.get("jurisdiction", ""),
            "jurisdiction": person_record.get("jurisdiction", ""),
            "ownership_pct": person_record.get("ownership_pct", ""),
        }
        for key, value in intermediary_fields.items():
            if value not in (None, "", {}, []):
                prescreening_data[key] = value
        for key in ("shareholders", "directors", "ubos"):
            prescreening_data.pop(key, None)

    context.update({
        "prescreening_data": prescreening_data,
        "entity_name": entity_name,
        "person_name": person_name,
        "directors_list": directors_list,
        "ubos_list": ubos_list,
    })
    return context


def resolve_user_display_name(db, user_id):
    if not user_id:
        return ""
    row = db.execute("SELECT full_name, email FROM users WHERE id = ? LIMIT 1", (user_id,)).fetchone()
    if not row:
        return str(user_id)
    return row.get("full_name") or row.get("email") or str(user_id)


# ── Prometheus Metrics (optional) ──────────────────────────
try:
    from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
    METRICS_ENABLED = True
    REQUEST_COUNT = Counter("arie_http_requests_total", "Total HTTP requests", ["method", "endpoint", "status"])
    REQUEST_LATENCY = Histogram("arie_http_request_duration_seconds", "HTTP request latency", ["method", "endpoint"])
    ACTIVE_CONNECTIONS = Gauge("arie_active_connections", "Active HTTP connections")
    APPLICATION_COUNT = Gauge("arie_applications_total", "Total applications by status", ["status"])
    SCREENING_COUNT = Counter("arie_screenings_total", "Total screenings run", ["source", "result"])
    SAR_COUNT = Counter("arie_sar_reports_total", "Total SAR reports", ["status"])
    logger.info("Prometheus metrics enabled")
except ImportError:
    METRICS_ENABLED = False
    logger.info("prometheus-client not installed — metrics disabled")

# ── Environment Validation ──────────────────────────────────
def validate_environment():
    """Validate required environment configuration on startup."""
    warnings = []
    errors = []

    if ENVIRONMENT == "production":
        if not SECRET_KEY or SECRET_KEY == "arie-dev-secret-change-in-production":
            errors.append("SECRET_KEY must be set to a secure random value in production")
        if not _CFG_ALLOWED_ORIGIN or _CFG_ALLOWED_ORIGIN == "http://localhost:8080":
            warnings.append("ALLOWED_ORIGIN not set — CORS defaults to same-origin only")
        if not DATABASE_URL:
            warnings.append("DATABASE_URL not set — using SQLite (not recommended for production)")
        if not OPENSANCTIONS_API_KEY:
            warnings.append("OPENSANCTIONS_API_KEY not set — sanctions screening will be simulated")
        if not SUMSUB_APP_TOKEN:
            warnings.append("SUMSUB_APP_TOKEN not set — KYC verification will be simulated")
    else:
        if not SECRET_KEY:
            warnings.append("SECRET_KEY not set — using auto-generated random key")

    for w in warnings:
        logger.warning("ENV CHECK: %s", w)
    for e in errors:
        logger.error("ENV CHECK: %s", e)

    if errors:
        logger.error("Environment validation failed — aborting startup")
        sys.exit(1)

    logger.info("Environment validation passed (%d warning(s))", len(warnings))
    return {"warnings": warnings, "errors": errors}

# ── Pricing Configuration (based on risk profile) ──
PRICING_TIERS = {
    "LOW": {
        "onboarding_fee": 500,
        "annual_monitoring_fee": 250,
        "currency": "USD",
        "description": "Standard onboarding — Low risk profile",
        "includes": ["Basic KYC verification", "Sanctions screening", "Annual review"]
    },
    "MEDIUM": {
        "onboarding_fee": 1500,
        "annual_monitoring_fee": 750,
        "currency": "USD",
        "description": "Enhanced onboarding — Medium risk profile",
        "includes": ["Enhanced KYC verification", "Sanctions & PEP screening", "Semi-annual review", "Adverse media monitoring"]
    },
    "HIGH": {
        "onboarding_fee": 3500,
        "annual_monitoring_fee": 2000,
        "currency": "USD",
        "description": "Enhanced Due Diligence onboarding — High risk profile",
        "includes": ["Full EDD verification", "Continuous sanctions & PEP monitoring", "Quarterly review", "Adverse media monitoring", "Behaviour & risk drift monitoring"]
    },
    "VERY_HIGH": {
        "onboarding_fee": 5000,
        "annual_monitoring_fee": 3500,
        "currency": "USD",
        "description": "Maximum Due Diligence onboarding — Very High risk profile",
        "includes": ["Maximum EDD verification", "Real-time sanctions & PEP monitoring", "Monthly review", "Full monitoring suite", "Dedicated compliance officer"]
    }
}

# ══════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════

class PostgresRowWrapper:
    """Wraps psycopg2 DictRow to behave like sqlite3.Row for compatibility."""
    def __init__(self, row):
        self._row = row
    def __getitem__(self, key):
        return self._row[key]
    def keys(self):
        return self._row.keys() if hasattr(self._row, 'keys') else []

def get_db():
    """Get a database connection — delegates to db module.
    PostgreSQL if DATABASE_URL is set, otherwise SQLite."""
    return db_get_db()


def init_db():
    """Initialize database schema and seed initial data."""
    from db import seed_initial_data, sync_ai_checks_from_seed, normalize_legacy_doc_types
    logger.info("startup: entering db_init_db (schema + pool)")
    db_init_db()
    logger.info("startup: db_init_db completed")
    db = get_db()
    try:
        logger.info("startup: entering seed_initial_data")
        seed_initial_data(db)
        db.commit()
        logger.info("startup: completed seed_initial_data")
        # Normalize any legacy portal-style doc_type values in documents table
        logger.info("startup: entering normalize_legacy_doc_types")
        normalize_legacy_doc_types(db)
        logger.info("startup: completed normalize_legacy_doc_types")
        # Upsert canonical ai_checks on every startup so stale rows on
        # existing databases (staging/prod) are always brought up to date.
        logger.info("startup: entering sync_ai_checks_from_seed")
        sync_ai_checks_from_seed(db)
        logger.info("startup: completed sync_ai_checks_from_seed")
    except Exception as e:
        logging.error(f"Seed error: {e}", exc_info=True)
        raise RuntimeError(
            "Verification-critical startup initialization failed. "
            "Check database connectivity and Agent 1 seed/ai_checks data integrity."
        ) from e
    finally:
        db.close()


# ── Extracted modules: auth.py, rule_engine.py, screening.py, memo_handler.py,
# ── validation_engine.py, supervisor_engine.py (see Sprint 2 architecture)

_REF_BASE_NUMBER = 100421  # First issued ref suffix — all refs start from ARF-YYYY-100421


def generate_ref():
    """Generate a unique application reference like ARF-2026-100429.

    Uses the maximum existing numeric suffix for the current year rather than
    COUNT(*) so that refs remain monotonically increasing even after
    application deletions. COUNT-based generation regenerates previously-issued
    refs when applications are deleted, causing UNIQUE constraint violations
    (HTTP 500) on staging databases where draft discard and demo resets delete
    rows.  MAX-based generation is safe regardless of deletion history.
    """
    year = datetime.now().year
    prefix = f"ARF-{year}-"
    db = get_db()
    try:
        rows = db.execute(
            "SELECT ref FROM applications WHERE ref LIKE ?",
            (f"{prefix}%",),
        ).fetchall()
    finally:
        db.close()
    max_num = _REF_BASE_NUMBER - 1  # one below the first valid ref
    for row in rows:
        ref_val = row["ref"] if isinstance(row, dict) else row[0]
        try:
            num = int(str(ref_val)[len(prefix):])
            if num > max_num:
                max_num = num
        except (ValueError, IndexError, TypeError):
            pass
    return f"{prefix}{max_num + 1}"

from screening import (
    screen_sumsub_aml, _simulate_aml_screen,
    lookup_opencorporates, _simulate_company_lookup,
    geolocate_ip, _simulate_ip_geolocation,
    _sumsub_sign, sumsub_create_applicant, sumsub_get_applicant_by_external_id,
    sumsub_generate_access_token, sumsub_get_applicant_status,
    sumsub_add_document, sumsub_verify_webhook,
    _simulate_sumsub_applicant, _simulate_sumsub_token, _simulate_sumsub_status,
    run_full_screening, ScreeningProviderError,
)

# Priority A — Canonical screening state model (truthful, fail-closed).
# See screening_state.py for state semantics. Used by the screening queue
# serializer to ensure pending / not_configured / failed provider states are
# never rendered as "Clear" or "No Provider Match".
from screening_state import (
    derive_screening_state,
    derive_subject_state,
    state_label as screening_state_label,
    legacy_status_value as _screening_legacy_status,
    combine_states as _combine_screening_states,
    COMPLETED_CLEAR as _SCR_COMPLETED_CLEAR,
    COMPLETED_MATCH as _SCR_COMPLETED_MATCH,
    NOT_CONFIGURED as _SCR_NOT_CONFIGURED,
    FAILED as _SCR_FAILED,
    PENDING_PROVIDER as _SCR_PENDING,
    PARTIAL_RESULT as _SCR_PARTIAL,
    NOT_STARTED as _SCR_NOT_STARTED,
    TERMINAL_STATES as _SCR_TERMINAL_STATES,
)

# Sprint 3.5: BaseHandler extracted to base_handler.py to reduce server.py concentration risk
from base_handler import BaseHandler, rate_limiter, get_db as _bh_get_db, snapshot_app_state, _safe_json  # noqa: F401
from screening_complyadvantage.webhook_handler import ComplyAdvantageWebhookHandler

# Public API v1 — versioned external endpoints
from public_api import (
    PublicHealthHandler,
    PublicApplicationStatusHandler,
    PublicApplicationDecisionHandler,
    PublicDashboardStatusHandler,
)


# ── Database Reset (temporary — remove after staging wipe) ──
class AdminResetDBHandler(BaseHandler):
    def post(self):
        """One-time staging database reset. Drops all data and re-seeds."""
        user = self.require_auth(roles=["admin"])
        if not user:
            return
        from config import IS_PRODUCTION
        if IS_PRODUCTION:
            self.error("Cannot reset production database", 403)
            return
        secret = self.get_json().get("confirm")
        if secret != "WIPE_STAGING_2026":
            self.error("Invalid confirmation", 403)
            return
        try:
            db = get_db()
            if db.is_postgres:
                # Disable FK constraints, truncate all tables, re-enable
                db.execute("SET session_replication_role = 'replica'")
                tables = db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()
                for t in tables:
                    tname = t.get("tablename") if hasattr(t, 'get') else t[0]
                    if tname and tname not in ("schema_version", "schema_migrations"):
                        db.execute(f'TRUNCATE TABLE "{tname}" CASCADE')
                db.execute("SET session_replication_role = 'origin'")
            else:
                tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                for t in tables:
                    tname = t.get("name") if hasattr(t, 'get') else t[0]
                    if tname and tname not in ("schema_version", "schema_migrations"):
                        db.execute(f"DELETE FROM {tname}")
            # Add missing columns if needed
            if db.is_postgres:
                try:
                    db.execute("ALTER TABLE applications ADD COLUMN IF NOT EXISTS screening_mode TEXT DEFAULT 'live'")
                except Exception:
                    pass
            db.commit()
            # Re-seed directly (don't use init_db which may skip if schema exists)
            from db import seed_initial_data
            try:
                seed_initial_data(db)
                logger.info("Database re-seeded successfully after reset")
            except Exception as seed_err:
                logger.error(f"Re-seed failed: {seed_err}", exc_info=True)
                db.close()
                self.error(f"Wipe succeeded but re-seed failed: {str(seed_err)}", 500)
                return
            db.close()
            self.success({"status": "reset_complete", "message": "Database wiped and re-seeded"})
        except Exception as e:
            logger.error(f"DB reset failed: {e}", exc_info=True)
            self.error(f"Reset failed: {str(e)}", 500)


class AdminResetPasswordHandler(BaseHandler):
    """POST /api/admin/reset-password — reset a client's password (staging only)."""
    def post(self):
        user = self.require_auth(roles=["admin"])
        if not user:
            return
        from config import IS_PRODUCTION
        if IS_PRODUCTION:
            self.error("Not available in production", 403)
            return
        data = self.get_json()
        email = data.get("email", "").strip().lower()
        new_password = data.get("new_password", "")
        if not email or not new_password:
            self.error("email and new_password required", 400)
            return
        import bcrypt
        pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        db = get_db()
        db.execute("UPDATE clients SET password_hash=? WHERE LOWER(email)=?", (pw_hash, email))
        db.commit()
        check = db.execute("SELECT id FROM clients WHERE LOWER(email)=?", (email,)).fetchone()
        if not check:
            db.close()
            self.error("Email not found", 404)
            return
        db.close()
        self.success({"status": "password_reset", "email": email})


class AdminOfficerPasswordResetHandler(BaseHandler):
    """POST /api/admin/officer-reset-password — reset an officer's password (staging only).
    Targets the users table (officers/admins), NOT the clients table.
    Requires admin auth + confirmation token. NOT available in production."""
    def post(self):
        user = self.require_auth(roles=["admin"])
        if not user:
            return
        from config import IS_PRODUCTION
        if IS_PRODUCTION:
            return self.error("Not available in production", 403)

        data = self.get_json()
        confirm = data.get("confirm", "")
        email = data.get("email", "").strip().lower()
        new_password = data.get("new_password", "")

        if confirm != "RESET_STAGING_ADMIN":
            return self.error("Invalid confirmation token", 403)
        if not email or not new_password:
            return self.error("email and new_password required", 400)
        if len(new_password) < 8:
            return self.error("Password must be at least 8 characters", 400)

        db = get_db()
        user = db.execute("SELECT id, role, full_name FROM users WHERE LOWER(email) = ?", (email,)).fetchone()
        if not user:
            db.close()
            return self.error("Officer not found", 404)

        import bcrypt
        pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        db.execute("UPDATE users SET password_hash = ? WHERE LOWER(email) = ?", (pw_hash, email))
        db.commit()
        db.close()

        logger.warning(f"OFFICER PASSWORD RESET: {email} (role={user['role']}) password was reset via staging endpoint")
        self.success({"status": "password_reset", "email": email, "role": user["role"]})


# ── Health Check ──
class HealthHandler(BaseHandler):
    def get(self):
        """Enhanced health check with database connectivity and dependency status."""
        from branding import BRAND
        health = {
            "status": "ok",
            "service": f"{BRAND['backoffice_name']} API",
            "platform": BRAND["portal_name"],
            "version": "1.0.0",
            "environment": ENV,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        # Database connectivity check
        db = None
        try:
            db = get_db()
            db.execute("SELECT 1")
            health["database"] = {"status": "connected", "type": "postgresql" if USE_POSTGRES else "sqlite"}
        except Exception as e:
            logger.error("Health check database error: %s", e)
            health["database"] = {"status": "error"}
            health["status"] = "degraded"
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

        # External API status — only show if authenticated and is admin
        # Remove integrations section to avoid configuration leakage
        user = self.get_current_user_token()
        if user and user.get("role") == "admin":
            health["integrations"] = {
                "opensanctions": "configured" if OPENSANCTIONS_API_KEY else "simulated",
                "opencorporates": "configured" if OPENCORPORATES_API_KEY else "simulated",
                "ip_geolocation": "live",
                "sumsub_kyc": "configured" if (SUMSUB_APP_TOKEN and SUMSUB_SECRET_KEY) else "simulated",
            }

        # Metrics status
        health["metrics_enabled"] = METRICS_ENABLED

        status_code = 200 if health["status"] == "ok" else 503
        self.set_status(status_code)
        self.write(json.dumps(health, default=str))


class ReadinessHandler(tornado.web.RequestHandler):
    """GET /api/readiness — Deep readiness probe for load balancers and orchestrators.

    Returns 200 only when ALL critical subsystems are operational:
    - PII encryption initialised and self-test passed
    - Database reachable
    - Required secrets/config present for the current environment
    """

    def get(self):
        checks = {}
        ready = True

        # 1. PII encryption init status
        if _pii_encryption_ok and _pii_encryptor is not None:
            checks["encryption"] = {"status": "ok"}
        else:
            checks["encryption"] = {"status": "failed", "detail": "PIIEncryptor not initialised or self-test failed"}
            ready = False

        # 2. Database connectivity
        db = None
        try:
            db = get_db()
            db.execute("SELECT 1")
            checks["database"] = {"status": "ok"}
        except Exception as e:
            checks["database"] = {"status": "failed", "detail": str(e)}
            ready = False
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

        # 3. Required config present (environment-specific)
        missing_config = []
        if ENVIRONMENT in ("staging", "production", "prod"):
            if not os.environ.get("PII_ENCRYPTION_KEY"):
                missing_config.append("PII_ENCRYPTION_KEY")
            if not SECRET_KEY:
                missing_config.append("SECRET_KEY/JWT_SECRET")
        if missing_config:
            checks["config"] = {"status": "failed", "missing": missing_config}
            ready = False
        else:
            checks["config"] = {"status": "ok"}

        status_code = 200 if ready else 503
        self.set_status(status_code)
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({
            "ready": ready,
            "environment": ENVIRONMENT,
            "checks": checks,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }, default=str))


class MetricsHandler(tornado.web.RequestHandler):
    """GET /metrics — Prometheus metrics endpoint"""
    def get(self):
        if not METRICS_ENABLED:
            self.set_status(404)
            self.write("Metrics not enabled")
            return
        self.set_header("Content-Type", CONTENT_TYPE_LATEST)
        self.write(generate_latest())


# ══════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ══════════════════════════════════════════════════════════

class OfficerLoginHandler(BaseHandler):
    """POST /api/auth/officer/login  {email, password}"""
    def post(self):
        data = self.get_json()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        if not email or not password:
            return self.error("Email and password required")

        # Rate limit: 10 attempts per 15 minutes per IP
        ip = self.get_client_ip()
        rl_key = f"officer_login:{ip}"
        if rate_limiter.is_limited(rl_key, max_attempts=10, window_seconds=900):
            logger.warning(f"Rate limited officer login from {ip} for {mask_email(email)}")
            return self.error("Too many login attempts. Please try again in 15 minutes.", 429)

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ? AND status = 'active'", (email,)).fetchone()
        db.close()

        if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            return self.error("Invalid credentials", 401)

        rate_limiter.reset(rl_key)  # Reset on successful login
        token = create_token(user["id"], user["role"], user["full_name"], "officer")
        csrf_token = self.issue_csrf_token()
        self.issue_session_cookie(token)  # Sprint 3.5: httpOnly cookie auth
        self.log_audit({"sub": user["id"], "name": user["full_name"], "role": user["role"]},
                       "Login", "System", f"Officer login from {ip}")
        self.success({
            "token": token,
            "csrf_token": csrf_token,
            "user": {"id": user["id"], "email": user["email"], "name": user["full_name"], "role": user["role"]}
        })


class ClientLoginHandler(BaseHandler):
    """POST /api/auth/client/login  {email, password}"""
    def post(self):
        data = self.get_json()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        if not email or not password:
            return self.error("Email and password required")

        # Rate limit: 10 attempts per 15 minutes per IP
        ip = self.get_client_ip()
        rl_key = f"client_login:{ip}"
        if rate_limiter.is_limited(rl_key, max_attempts=10, window_seconds=900):
            logger.warning(f"Rate limited client login from {ip} for {mask_email(email)}")
            return self.error("Too many login attempts. Please try again in 15 minutes.", 429)

        db = get_db()
        client = db.execute("SELECT * FROM clients WHERE email = ? AND status = 'active'", (email,)).fetchone()
        db.close()

        if not client or not bcrypt.checkpw(password.encode(), client["password_hash"].encode()):
            return self.error("Invalid credentials", 401)

        rate_limiter.reset(rl_key)  # Reset on successful login

        token = create_token(client["id"], "client", client["company_name"] or email, "client")
        csrf_token = self.issue_csrf_token()
        self.issue_session_cookie(token)  # Sprint 3.5: httpOnly cookie auth
        self.success({
            "token": token,
            "csrf_token": csrf_token,
            "client": {"id": client["id"], "email": client["email"], "company": client["company_name"]}
        })


class ClientRegisterHandler(BaseHandler):
    """POST /api/auth/client/register  {email, password, company_name}"""
    def post(self):
        data = self.get_json()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        company = data.get("company_name", "")
        if not email or not password:
            return self.error("Email and password required")

        # Rate limit: 5 registrations per 30 minutes per IP
        ip = self.get_client_ip()
        rl_key = f"register:{ip}"
        if rate_limiter.is_limited(rl_key, max_attempts=5, window_seconds=1800):
            return self.error("Too many registration attempts. Please try again later.", 429)

        if len(password) < 12:
            return self.error("Password must be at least 12 characters")

        # Check for common passwords
        common_passwords = {"password", "12345678", "qwerty", "letmein", "welcome", "monkey",
                           "dragon", "master", "abc123", "password1", "onboarda", "123456789012"}
        if password.lower() in common_passwords or any(cp in password.lower() for cp in common_passwords):
            return self.error("Password is too common or easily guessable", 400)

        # Validate password policy (mandatory)
        is_valid, pw_error = PasswordPolicy.validate(password)
        if not is_valid:
            return self.error(f"Password policy violation: {pw_error}", 400)

        db = get_db()
        exists = db.execute("SELECT id FROM clients WHERE email = ?", (email,)).fetchone()
        if exists:
            db.close()
            return self.error("Email already registered")

        client_id = uuid.uuid4().hex[:16]
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        db.execute("INSERT INTO clients (id, email, password_hash, company_name) VALUES (?,?,?,?)",
                    (client_id, email, pw_hash, company))
        db.commit()
        db.close()

        token = create_token(client_id, "client", company or email, "client")
        csrf_token = self.issue_csrf_token()
        self.issue_session_cookie(token)
        self.success({"token": token, "csrf_token": csrf_token, "client": {"id": client_id, "email": email, "company": company}}, 201)


class ForgotPasswordHandler(BaseHandler):
    """POST /api/auth/client/forgot-password — generate a password reset token."""
    def post(self):
        data = self.get_json()
        email = data.get("email", "").strip().lower()
        if not email:
            return self.error("Email is required", 400)

        # Rate limit: 5 attempts per 30 minutes per IP
        ip = self.get_client_ip()
        rl_key = f"forgot_pw:{ip}"
        if rate_limiter.is_limited(rl_key, max_attempts=5, window_seconds=1800):
            return self.error("Too many reset attempts. Please try again later.", 429)

        # Per-email rate limit: prevent enumeration via repeated requests for the same address
        email_rl_key = f"forgot_pw:email:{hashlib.sha256(email.encode()).hexdigest()}"
        if rate_limiter.is_limited(email_rl_key, max_attempts=3, window_seconds=1800):
            # Return identical success message to prevent email enumeration
            return self.success({"message": "If that email is registered, a reset link has been sent."})

        db = get_db()
        client = db.execute("SELECT id, email FROM clients WHERE email = ? AND status = 'active'", (email,)).fetchone()
        if not client:
            db.close()
            # Don't reveal whether email exists
            return self.success({"message": "If that email is registered, a reset link has been sent."})

        # Generate reset token and expiry (1 hour)
        reset_token = secrets.token_urlsafe(32)
        reset_token_hash = hash_reset_token(reset_token)
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        db.execute("UPDATE clients SET password_reset_token=?, password_reset_expires=? WHERE id=?",
                   (reset_token_hash, expires, client["id"]))
        db.commit()
        db.close()

        from config import IS_PRODUCTION
        result = {"message": "If that email is registered, a reset link has been sent."}
        portal_base = os.environ.get("PORTAL_BASE_URL") or BRAND.get("website") or ""
        reset_link = f"{portal_base.rstrip('/')}/?reset_token={reset_token}" if portal_base else ""
        email_body = (
            "A password reset was requested for your Onboarda portal account.\n\n"
            f"Use this reset token: {reset_token}\n\n"
            + (f"Reset link: {reset_link}\n\n" if reset_link else "")
            + "This token will expire in 1 hour. If you did not request this change, you can ignore this email."
        )
        email_sent = send_portal_email(email, "Onboarda password reset", email_body)

        if not IS_PRODUCTION:
            result["reset_token"] = reset_token  # Only expose token in non-production
            if reset_link:
                result["reset_link"] = reset_link
        result["email_sent"] = bool(email_sent)
        self.success(result)


class ResetPasswordHandler(BaseHandler):
    """POST /api/auth/client/reset-password — reset password using token."""
    def post(self):
        data = self.get_json()
        token = data.get("token", "").strip()
        new_password = data.get("new_password", "")
        if not token or not new_password:
            return self.error("Token and new_password are required", 400)

        if len(new_password) < 12:
            return self.error("Password must be at least 12 characters", 400)

        # Validate password policy
        is_valid, pw_error = PasswordPolicy.validate(new_password)
        if not is_valid:
            return self.error(f"Password policy violation: {pw_error}", 400)

        token_hash = hash_reset_token(token)
        db = get_db()
        client = db.execute(
            "SELECT id, email, password_reset_token, password_reset_expires FROM clients WHERE password_reset_token = ?",
            (token_hash,)
        ).fetchone()

        if not client:
            db.close()
            return self.error("Invalid or expired reset token", 400)

        # Check expiry — compare as naive UTC consistently
        try:
            expires = datetime.fromisoformat(client["password_reset_expires"])
            # Normalise both sides to naive UTC for safe comparison
            if expires.tzinfo is not None:
                expires = expires.replace(tzinfo=None)
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            if now_utc > expires:
                db.close()
                return self.error("Reset token has expired", 400)
        except (ValueError, TypeError):
            db.close()
            return self.error("Invalid or expired reset token", 400)

        # Reset password and clear token
        pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        db.execute("UPDATE clients SET password_hash=?, password_reset_token=NULL, password_reset_expires=NULL WHERE id=?",
                   (pw_hash, client["id"]))
        db.commit()

        # Revoke all active sessions for this client so old tokens can't be reused
        _revoke_all_client_sessions(db, client["id"])

        db.close()

        logger.info(f"Password reset completed for {mask_email(client['email'])}")
        self.success({"message": "Password has been reset successfully."})


class ClientChangePasswordHandler(BaseHandler):
    """POST /api/auth/client/change-password — change password for authenticated client."""
    def post(self):
        user = self.require_auth()
        if not user:
            return
        data = self.get_json()
        current = data.get("current_password", "")
        new_pw = data.get("new_password", "")
        if not current or not new_pw:
            return self.error("current_password and new_password required", 400)
        if len(new_pw) < 12:
            return self.error("Password must be at least 12 characters", 400)

        db = get_db()
        client = db.execute("SELECT password_hash FROM clients WHERE id=?", (user.get("sub"),)).fetchone()
        if not client or not bcrypt.checkpw(current.encode(), client["password_hash"].encode()):
            db.close()
            return self.error("Current password is incorrect", 401)
        is_valid, pw_error = PasswordPolicy.validate(new_pw)
        if not is_valid:
            db.close()
            return self.error(f"Password policy violation: {pw_error}", 400)
        new_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
        db.execute("UPDATE clients SET password_hash=? WHERE id=?", (new_hash, user.get("sub")))
        db.commit()

        # Revoke the current session — client must re-login with new password
        jti = user.get("jti")
        exp = user.get("exp")
        if jti and exp:
            token_revocation_list.revoke(jti, exp)

        db.close()
        self.clear_session_cookie()
        logger.info(f"Password changed for client {user.get('sub')}")
        self.success({"status": "password_changed"})


class MeHandler(BaseHandler):
    """GET /api/auth/me"""
    def get(self):
        user = self.require_auth()
        if not user:
            return
        self.success({"id": user["sub"], "name": user["name"], "role": user["role"], "type": user["type"]})


class LogoutHandler(BaseHandler):
    """POST /api/auth/logout — Revoke token and clear session cookie."""
    def post(self):
        user = self.get_current_user_token()
        if user:
            # Revoke the JWT so it can't be reused even before expiry
            jti = user.get("jti")
            exp = user.get("exp")
            if jti and exp:
                token_revocation_list.revoke(jti, exp)
            self.log_audit(user, "Logout", "System", f"User {user.get('name', '')} logged out")
        self.clear_session_cookie()
        self.success({"status": "logged_out"})


# ══════════════════════════════════════════════════════════
# APPLICATION ENDPOINTS
# ══════════════════════════════════════════════════════════

class ApplicationsHandler(BaseHandler):
    """GET /api/applications — list, POST — create"""
    def get(self):
        user = self.require_auth()
        if not user:
            return

        from fixture_filter import (
            fixture_app_exclude_clause,
            should_show_fixtures,
        )

        db = get_db()
        status = self.get_argument("status", None)
        risk = self.get_argument("risk", None)
        assigned = self.get_argument("assigned", None)

        query = """
            SELECT a.*, u.full_name AS assigned_name
            FROM applications a
            LEFT JOIN users u ON a.assigned_to = u.id
            WHERE 1=1
        """
        params = []

        # Clients can only see their own
        if user["type"] == "client":
            query += " AND a.client_id = ?"
            params.append(user["sub"])
        else:
            # Officer / admin: exclude fixtures by default
            show_fx = should_show_fixtures(user, self.get_argument("show_fixtures", None))
            if not show_fx:
                fx_excl, fx_params = fixture_app_exclude_clause()
                query += f" AND {fx_excl}"
                params.extend(fx_params)

        if status:
            query += " AND a.status = ?"
            params.append(status)
        if risk:
            query += " AND a.risk_level = ?"
            params.append(risk)
        if assigned:
            query += " AND a.assigned_to = ?"
            params.append(assigned)

        query += " ORDER BY a.created_at DESC LIMIT 200"
        rows = db.execute(query, params).fetchall()
        db.close()

        apps = [dict(r) for r in rows]

        # EX-13: Batch-fetch related records to eliminate N+1 query pattern.
        # Instead of 4N+1 queries, we use 5 total queries regardless of app count.
        db = get_db()
        app_ids = [app["id"] for app in apps]

        # Batch fetch parties (3 queries: directors, ubos, intermediaries)
        parties_by_app = get_application_parties_batch(db, app_ids) if app_ids else {}

        # Batch fetch documents (1 query)
        docs_by_app = {}
        if app_ids:
            doc_placeholders = ",".join("?" for _ in app_ids)
            doc_rows = db.execute(
                "SELECT id, doc_type, doc_name, file_size, verification_status, "
                "verification_results, verified_at, person_id, review_status, "
                "review_comment, reviewed_by, reviewed_at, application_id "
                f"FROM documents WHERE application_id IN ({doc_placeholders})",
                app_ids,
            ).fetchall()
            for d in doc_rows:
                docs_by_app.setdefault(d["application_id"], []).append(dict(d))

        # Stitch results back into each application
        for app in apps:
            app["status_label"] = get_status_label(app.get("status"))
            parties = parties_by_app.get(app["id"], ([], [], []))
            app["directors"] = parties[0]
            app["ubos"] = parties[1]
            app["intermediaries"] = parties[2]
            app_docs = docs_by_app.get(app["id"], [])
            # Remove application_id from document dicts (not part of original response)
            for doc in app_docs:
                doc.pop("application_id", None)
            app["documents"] = app_docs
            # Bug #4: Parse risk_dimensions from JSON string for API consumers
            if app.get("risk_dimensions") and isinstance(app["risk_dimensions"], str):
                app["risk_dimensions"] = safe_json_loads(app["risk_dimensions"])
        db.close()

        # EX-13: ETag support — compute hash of response for conditional requests
        payload = {"applications": apps, "total": len(apps)}
        payload_json = json.dumps(payload, sort_keys=True, default=str)
        etag = '"' + hashlib.md5(payload_json.encode("utf-8")).hexdigest() + '"'

        # Check If-None-Match header for conditional request
        client_etag = self.request.headers.get("If-None-Match", "")
        if client_etag == etag:
            self.set_status(304)
            self.set_header("ETag", etag)
            self.finish()
            return

        self.set_header("ETag", etag)
        self.success(payload)

    def post(self):
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        app_id = uuid.uuid4().hex[:16]
        ref = generate_ref()
        prescreening_data = normalize_prescreening_data(data)
        company_name = resolve_application_company_name(data, prescreening_data)
        if not company_name:
            return self.error("Registered entity name is required.", 400)

        # W3: BRN basic validation — if provided, allow letters, numbers, internal spaces, dashes, dots, and slashes
        brn = first_non_empty(data.get("brn"), prescreening_data.get("brn")) or ""
        brn = re.sub(r'\s+', ' ', brn.strip())  # collapse multiple spaces
        if brn and not re.match(r'^[A-Za-z0-9\-/.][A-Za-z0-9\-/. ]{0,28}[A-Za-z0-9\-/.]$', brn):
            return self.error("Invalid Business Registration Number format. Please use 2-30 characters consisting of letters, numbers, spaces, dashes, dots, or slashes.", 400)

        db = get_db()

        # W3/GATE-03: Prevent duplicate active applications for same client + company
        # Normalize company_name for comparison to prevent bypass via case/whitespace variants
        client_id = user["sub"] if user["type"] == "client" else data.get("client_id")
        # Defence-in-depth: if caller supplies an existing application_id, exclude
        # it from the duplicate check so a self-update is never blocked.
        exclude_app_id = data.get("application_id") or None
        if client_id and company_name:
            normalized_name = re.sub(r'\s+', ' ', company_name.strip()).lower()
            existing = db.execute(
                "SELECT id, ref, company_name FROM applications WHERE client_id=? AND status NOT IN ('rejected','withdrawn')",
                (client_id,)
            ).fetchall()
            dup = next((e for e in existing
                        if re.sub(r'\s+', ' ', (e['company_name'] or '').strip()).lower() == normalized_name
                        and e['id'] != exclude_app_id), None)
            existing = dup
            if existing:
                db.close()
                return self.error(
                    f"An active application for '{company_name}' already exists (ref: {existing['ref']}). "
                    "Please resume the existing application instead of creating a new one.",
                    409
                )
        db.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, brn, country, sector,
                entity_type, ownership_structure, prescreening_data, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            app_id, ref,
            client_id,
            company_name,
            brn,
            first_non_empty(data.get("country"), prescreening_data.get("country_of_incorporation")),
            first_non_empty(data.get("sector"), prescreening_data.get("sector")),
            first_non_empty(data.get("entity_type"), prescreening_data.get("entity_type")),
            first_non_empty(data.get("ownership_structure"), prescreening_data.get("ownership_structure")),
            json.dumps(prescreening_data),
            "draft"
        ))

        try:
            store_application_parties(
                db,
                app_id,
                directors=data.get("directors"),
                ubos=data.get("ubos"),
                intermediaries=data.get("intermediaries")
            )
        except ValueError as exc:
            db.close()
            return self.error(str(exc), 400)

        db.commit()
        db.close()

        self.log_audit(user, "Create", ref, f"New application created: {company_name}")
        self.success({"id": app_id, "ref": ref, "status": "draft", "status_label": get_status_label("draft")}, 201)


def cleanup_application_delete_artifacts(db, application_id, application_ref):
    """Delete uploaded document artifacts and non-cascading child rows before app hard delete."""
    documents = db.execute(
        "SELECT id, file_path, s3_key FROM documents WHERE application_id=?",
        (application_id,)
    ).fetchall()

    for doc in documents:
        file_path = doc["file_path"]
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError as exc:
                logger.warning("Failed to remove draft document file %s: %s", file_path, exc)

        if doc.get("s3_key") and HAS_S3:
            try:
                s3 = get_s3_client()
                deleted, message = s3.delete_document(doc["s3_key"])
                if not deleted:
                    logger.warning("S3 deletion failed for draft application doc %s: %s", doc["s3_key"], message)
            except Exception as exc:
                logger.warning("S3 deletion failed for draft application doc %s: %s", doc["s3_key"], exc)

    for table in (
        "client_sessions",
        "documents",
        "client_notifications",
        "monitoring_alerts",
        "periodic_reviews",
        "sar_reports",
        "compliance_memos",
        "edd_cases",
        "directors",
        "ubos",
        "intermediaries",
        "transactions",
        "agent_executions",
        "sumsub_applicant_mappings",
        "supervisor_pipeline_results",
        "supervisor_audit_log",
    ):
        db.execute(f"DELETE FROM {table} WHERE application_id=?", (application_id,))
    db.execute("DELETE FROM decision_records WHERE application_ref=?", (application_ref,))

    # Sprint 3 Obj 2a (PR #116 fixup H2/H3): Delete normalized screening
    # records via the shared helper, which narrowly handles the
    # missing-table case (migration 007 not applied) without swallowing
    # other DB errors.  This is the single production cleanup path.
    from screening_storage import delete_normalized_reports_for_application
    delete_normalized_reports_for_application(db, application_id)


class ApplicationDetailHandler(BaseHandler):
    """GET/PUT/PATCH /api/applications/:id"""
    def get(self, app_id):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        result = dict(app)
        result["status_label"] = get_status_label(result.get("status"))
        result["assigned_name"] = resolve_user_display_name(db, result.get("assigned_to"))
        result["directors"], result["ubos"], result["intermediaries"] = get_application_parties(db, result["id"])
        result["documents"] = [dict(d) for d in db.execute(
            "SELECT * FROM documents WHERE application_id = ?", (result["id"],)).fetchall()]
        for doc in result["documents"]:
            doc["verification_results"] = parse_json_field(doc.get("verification_results"), {})
            doc["reviewed_by_name"] = resolve_user_display_name(db, doc.get("reviewed_by"))
        stored_prescreening = parse_json_field(result.get("prescreening_data"), {})
        saved_session_prescreening = load_saved_session_prescreening(db, result)
        result["prescreening_data"] = merge_prescreening_sources(stored_prescreening, saved_session_prescreening)
        # Bug #4: Parse risk_dimensions from JSON string for API consumers
        if result.get("risk_dimensions") and isinstance(result["risk_dimensions"], str):
            result["risk_dimensions"] = safe_json_loads(result["risk_dimensions"])
        latest_memo = db.execute("""
            SELECT id, version, memo_data, review_status, validation_status, blocked, block_reason,
                   quality_score, memo_version, approved_by, approved_at, created_at
            FROM compliance_memos
            WHERE application_id = ?
            ORDER BY version DESC, id DESC
            LIMIT 1
        """, (result["id"],)).fetchone()
        if latest_memo:
            latest_memo_dict = dict(latest_memo)
            latest_memo_data = parse_json_field(latest_memo_dict.get("memo_data"), {})
            latest_memo_data.setdefault("metadata", {})
            latest_memo_data["review_status"] = latest_memo_dict.get("review_status")
            latest_memo_data["validation_status"] = latest_memo_dict.get("validation_status")
            latest_memo_data["approved_by"] = latest_memo_dict.get("approved_by")
            latest_memo_data["approved_at"] = latest_memo_dict.get("approved_at")
            latest_memo_data["memo_version"] = latest_memo_dict.get("memo_version") or latest_memo_dict.get("version")
            latest_memo_data["memo_generated"] = latest_memo_dict.get("created_at")
            latest_memo_data["application_ref"] = result.get("ref")
            latest_memo_data["metadata"]["blocked"] = bool(latest_memo_dict.get("blocked"))
            latest_memo_data["metadata"]["block_reason"] = latest_memo_dict.get("block_reason")
            latest_memo_data["metadata"]["quality_score"] = latest_memo_dict.get("quality_score")

            latest_memo_dict.pop("memo_data", None)
            result["latest_memo"] = latest_memo_dict
            result["latest_memo_data"] = latest_memo_data
            # Memo staleness detection: compare memo creation vs application input update
            # Use inputs_updated_at (substantive changes only) to avoid false
            # staleness from operational writes (e.g. first-approval recording).
            memo_created = latest_memo_dict.get("created_at", "")
            app_input_updated = result.get("inputs_updated_at") or result.get("updated_at", "")
            if memo_created and app_input_updated:
                result["memo_is_stale"] = str(app_input_updated) > str(memo_created)
            else:
                result["memo_is_stale"] = False
        else:
            result["latest_memo"] = None
            result["latest_memo_data"] = None
            result["memo_is_stale"] = False
        db.close()

        self.success(result)

    def put(self, app_id):
        """Full update of application data + resubmit."""
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        db = get_db()

        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        real_id = app["id"]
        existing_prescreening = safe_json_loads(app["prescreening_data"])
        normalized_prescreening = normalize_prescreening_data(data, existing_prescreening)
        resolved_company_name = resolve_application_company_name(data, normalized_prescreening, app["company_name"])

        # ── C-04/C-07 FIX: Block modification of screening data and immutable fields after submission ──
        non_draft_statuses = ("submitted", "pricing_review", "under_review", "edd_required", "approved", "rejected", "kyc_documents")
        if app["status"] in non_draft_statuses:
            # Block prescreening_data modification entirely after submission
            if "prescreening_data" in data:
                db.close()
                return self.error(
                    "Screening data is immutable after submission. Cannot modify prescreening_data.",
                    403
                )
            # Block screening_mode modification
            if "screening_mode" in data:
                db.close()
                return self.error(
                    "Screening mode is immutable after submission.",
                    403
                )

        # ── C-04/C-07: Only allow prescreening_data update in draft status ──
        if app["status"] == "draft":
            db.execute("""
                UPDATE applications SET
                    company_name=?, brn=?, country=?, sector=?, entity_type=?,
                    ownership_structure=?, prescreening_data=?,
                    updated_at=datetime('now'), inputs_updated_at=datetime('now')
                WHERE id=?
            """, (
                resolved_company_name,
                first_non_empty(data.get("brn"), normalized_prescreening.get("brn"), app["brn"]),
                first_non_empty(data.get("country"), normalized_prescreening.get("country_of_incorporation"), app["country"]),
                first_non_empty(data.get("sector"), normalized_prescreening.get("sector"), app["sector"]),
                first_non_empty(data.get("entity_type"), normalized_prescreening.get("entity_type"), app["entity_type"]),
                first_non_empty(data.get("ownership_structure"), normalized_prescreening.get("ownership_structure"), app["ownership_structure"]),
                json.dumps(normalized_prescreening),
                real_id
            ))
        else:
            # Post-submission: only allow metadata updates, NOT screening data
            db.execute("""
                UPDATE applications SET
                    company_name=?, brn=?, country=?, sector=?, entity_type=?,
                    ownership_structure=?,
                    updated_at=datetime('now'), inputs_updated_at=datetime('now')
                WHERE id=?
            """, (
                resolved_company_name,
                first_non_empty(data.get("brn"), normalized_prescreening.get("brn"), app["brn"]),
                first_non_empty(data.get("country"), normalized_prescreening.get("country_of_incorporation"), app["country"]),
                first_non_empty(data.get("sector"), normalized_prescreening.get("sector"), app["sector"]),
                first_non_empty(data.get("entity_type"), normalized_prescreening.get("entity_type"), app["entity_type"]),
                first_non_empty(data.get("ownership_structure"), normalized_prescreening.get("ownership_structure"), app["ownership_structure"]),
                real_id
            ))

        if any(key in data for key in ("directors", "ubos", "intermediaries")):
            # ── Phase 4: Block party modifications after compliance review / approval states ──
            immutable_party_states = ("compliance_review", "in_review", "edd_required",
                                      "under_review", "approved", "rejected")
            if app["status"] in immutable_party_states:
                db.close()
                return self.error(
                    f"Cannot modify directors/UBOs/intermediaries after compliance review has started. "
                    f"Current status: {app['status']}. Create a new application or contact an officer "
                    f"to reopen the case if changes are required and reopening is allowed.",
                    403
                )
            try:
                store_application_parties(
                    db,
                    real_id,
                    directors=data["directors"] if "directors" in data else None,
                    ubos=data["ubos"] if "ubos" in data else None,
                    intermediaries=data["intermediaries"] if "intermediaries" in data else None
                )
            except ValueError as exc:
                db.close()
                return self.error(str(exc), 400)

        # ── Risk recomputation on material field edits ──
        # If any risk-relevant field changed, recompute the canonical risk score.
        # This prevents stale risk values after back-office edits.
        RISK_RELEVANT_FIELDS = {
            "entity_type", "ownership_structure", "sector", "country",
            "directors", "ubos", "intermediaries",
        }
        # Also check fields inside prescreening_data that affect scoring
        RISK_RELEVANT_PRESCREENING = {
            "operating_countries", "countries_of_operation", "target_markets",
            "primary_service", "service_required", "monthly_volume",
            "expected_volume", "transaction_complexity", "payment_corridors",
            "source_of_wealth", "source_of_funds", "introduction_method",
            "customer_interaction", "interaction_type", "cross_border",
        }
        risk_changed = any(k in data for k in RISK_RELEVANT_FIELDS)
        if not risk_changed and "prescreening_data" in data:
            ps = data.get("prescreening_data", {})
            if isinstance(ps, dict):
                risk_changed = any(k in ps for k in RISK_RELEVANT_PRESCREENING)

        risk_recomputed = False
        if risk_changed and app.get("risk_score") is not None:
            rr = recompute_risk(db, real_id, "application_edit", user=user,
                                log_audit_fn=self.log_audit)
            risk_recomputed = rr.get("recomputed", False)

        db.commit()
        db.close()

        audit_detail = "Application updated"
        if risk_recomputed:
            audit_detail += " (risk recomputed)"
        self.log_audit(user, "Update", app["ref"], audit_detail)
        self.success({"status": "updated", "risk_recomputed": risk_recomputed})

    def patch(self, app_id):
        """Partial update — status changes, assignments, etc."""
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        real_id = app["id"]

        # Only officers can change status and assignment
        if user.get("type") == "client":
            if "status" in data or "assigned_to" in data or "decision_by" in data:
                db.close()
                return self.error("Only officers can change application status", 403)

        # C-05: Workflow integrity enforcement
        new_status = data.get("status")
        if new_status:
            current_status = app["status"]

            # Define valid state transitions (v2.1: includes pre-approval flow)
            valid_transitions = {
                "draft": ["submitted", "prescreening_submitted"],
                "prescreening_submitted": ["pricing_review", "pre_approval_review"],
                "pre_approval_review": ["pre_approved", "rejected", "draft"],  # officer pre-approval decisions
                "pre_approved": ["kyc_documents"],
                "pricing_review": ["pricing_accepted"],
                "pricing_accepted": ["kyc_documents", "pre_approval_review"],
                "kyc_documents": ["kyc_submitted", "compliance_review"],
                "kyc_submitted": ["compliance_review"],
                "submitted": ["under_review", "rejected"],
                "compliance_review": ["in_review", "edd_required", "approved", "rejected"],
                "in_review": ["edd_required", "approved", "rejected"],
                "under_review": ["edd_required", "approved", "rejected"],
                "edd_required": ["under_review", "in_review", "approved", "rejected"],
                "approved": [],  # Terminal state
                "rejected": ["draft"],  # Can reopen to draft
            }

            allowed = valid_transitions.get(current_status, [])
            if new_status not in allowed:
                db.close()
                return self.error(
                    f"Invalid workflow transition: '{current_status}' → '{new_status}'. "
                    f"Allowed transitions: {allowed or 'none (terminal state)'}",
                    400
                )

            # v2.1: HIGH/VERY_HIGH risk MUST go through pre_approval_review before kyc_documents
            risk_level = (app.get("risk_level") or "").upper()
            if new_status == "kyc_documents" and risk_level in ("HIGH", "VERY_HIGH"):
                if app.get("pre_approval_decision") != "PRE_APPROVE":
                    db.close()
                    return self.error(
                        "HIGH/VERY_HIGH risk applications must be pre-approved before KYC. "
                        f"Pre-approval decision: {app.get('pre_approval_decision') or 'none'}",
                        400
                    )

            # ── H-05 FIX: High-risk cases MUST go through compliance review before approval ──
            if new_status == "approved" and risk_level in ("HIGH", "VERY_HIGH"):
                review_states = ("under_review", "edd_required", "compliance_review", "in_review")
                if current_status not in review_states:
                    db.close()
                    return self.error(
                        f"HIGH/VERY_HIGH risk applications must undergo compliance review "
                        f"before approval. Current status: {current_status}",
                        400
                    )

            # For approval: enforce that screening is complete, memo exists, and approval gate passes
            if new_status == "approved":
                # Require screening to have been run (not in draft)
                prescreening = safe_json_loads(app["prescreening_data"])
                if not prescreening.get("screening_report"):
                    db.close()
                    return self.error("Cannot approve: screening has not been run. Submit the application first.", 400)

                # Freeze screening post-submission: prevent re-screening after approval
                screening_report = prescreening.get("screening_report", {})
                if screening_report.get("screening_mode") == "simulated" and is_production():
                    db.close()
                    return self.error("Cannot approve: screening used simulated data in production.", 400)

                # Require compliance memo before approval decision
                memo = db.execute("SELECT id FROM compliance_memos WHERE application_id = ?", (real_id,)).fetchone()
                if not memo:
                    db.close()
                    return self.error("Cannot approve: compliance memo must be generated before decision.", 400)

                # Run full approval gate validation
                app_dict = dict(app)
                app_dict["prescreening_data"] = prescreening
                can_approve, gate_error = ApprovalGateValidator.validate_approval(app_dict, db)
                if not can_approve:
                    db.close()
                    return self.error(f"Approval gate failed: {gate_error}", 400)

            db.execute("UPDATE applications SET status=?, updated_at=datetime('now') WHERE id=?", (new_status, real_id))
            if new_status in ("approved", "rejected"):
                db.execute("UPDATE applications SET decided_at=datetime('now'), decision_by=?, decision_notes=? WHERE id=?",
                           (user["sub"], data.get("notes",""), real_id))
            self.log_audit(user, "Status Change", app["ref"],
                           f"Status: {current_status} → {new_status}", db=db)

        # Handle assignment — only admin, sco, co can reassign
        if "assigned_to" in data:
            if user.get("role") not in ("admin", "sco", "co"):
                db.close()
                return self.error("Only Admin, Senior Compliance Officer, or Compliance Officer can reassign applications", 403)
            old_assigned = app.get("assigned_to") or "Unassigned"
            new_assigned = data["assigned_to"] or "Unassigned"
            db.execute("UPDATE applications SET assigned_to=?, updated_at=datetime('now') WHERE id=?",
                       (data["assigned_to"], real_id))
            self.log_audit(user, "Reassign", app["ref"],
                           f"Reassigned from {old_assigned} to {new_assigned}", db=db)

        db.commit()
        db.close()
        self.success({"status": "updated"})

    def delete(self, app_id):
        """Delete a draft application for the owning client."""
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        if user.get("type") != "client":
            db.close()
            return self.error("Only portal clients can delete applications.", 403)

        if app["status"] != "draft":
            db.close()
            return self.error("Only draft applications can be deleted.", 403)

        cleanup_application_delete_artifacts(db, app["id"], app["ref"])
        db.execute("DELETE FROM applications WHERE id=?", (app["id"],))
        self.log_audit(user, "Delete", app["ref"], f"Draft application deleted by client: {app['company_name']}", db=db)
        db.commit()
        db.close()
        self.success({"status": "deleted", "id": app["id"], "ref": app["ref"]})


class SubmitApplicationHandler(BaseHandler):
    """POST /api/applications/:id/submit — submit pre-screening, run screening, calculate risk, show pricing"""
    def post(self, app_id):
        user = self.require_auth()
        if not user:
            return

        if not self.check_rate_limit("submit", max_attempts=5, window_seconds=60):
            return

        db = get_db()
        stage = "init"
        try:
            return self._do_submit(db, user, app_id)
        except Exception as exc:
            # ── Outer defence-in-depth handler ──
            # Guarantee no unhandled exception leaks as Tornado's default 500.
            try:
                db.rollback()
            except Exception:
                pass
            # Attempt to identify the failing stage from the traceback
            import traceback
            tb_text = traceback.format_exc()
            if "compute_risk_score" in tb_text or "_score_entity_type" in tb_text:
                stage = "compute_risk_score"
            elif "run_full_screening" in tb_text:
                stage = "run_full_screening"
            elif "build_prescreening_risk_input" in tb_text:
                stage = "build_prescreening_risk_input"
            elif "classify_risk_level" in tb_text:
                stage = "classify_risk_level"
            else:
                stage = "unknown"
            logger.error(
                "SubmitApplicationHandler unhandled error: app_id=%s user=%s ip=%s stage=%s error=%s",
                app_id,
                user.get("sub", "unknown") if user else "unknown",
                self.get_client_ip(),
                stage,
                str(exc)[:500],
                exc_info=True,
            )
            return self.error("An unexpected error occurred while processing your submission. Please try again.", 500)
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _do_submit(self, db, user, app_id):
        """Core submit logic, separated for clean error handling."""
        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            return

        real_id = app["id"]

        # EX-05: Capture before-state for audit trail
        _before = snapshot_app_state(app)

        # ── v2.2: Pre-screening validation ──────────────────────────
        prescreening_raw = safe_json_loads(app["prescreening_data"])

        # Validate incorporation date (no future dates)
        inc_date = prescreening_raw.get("incorporation_date", "")
        if inc_date:
            try:
                from datetime import date
                parsed_date = datetime.strptime(inc_date, "%Y-%m-%d").date()
                if parsed_date > datetime.now(timezone.utc).date():
                    return self.error("Incorporation date cannot be in the future.", 400)
            except ValueError:
                pass  # Non-standard date format, allow through

        # Validate country is not sanctioned
        country = (app.get("country") or "").lower().strip()
        if country in SANCTIONED_COUNTRIES_FULL:
            return self.error(
                f"{BRAND['portal_name']} cannot onboard clients involved in sanctioned or prohibited jurisdictions.",
                403
            )

        # Source of Wealth / Source of Funds detail fields are optional — no minimum enforced

        # Validate currency
        currency = prescreening_raw.get("currency", "")
        if currency and currency not in ALLOWED_CURRENCIES:
            return self.error(f"Currency '{currency}' not supported. Allowed: {', '.join(ALLOWED_CURRENCIES)}", 400)

        # ── v2.3: Validate contact fields (email, phone, website) ──
        contact_email = prescreening_raw.get("entity_contact_email", "")
        if contact_email:
            if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', contact_email):
                return self.error("Invalid email format. Please provide a valid email address.", 400)

        contact_phone = prescreening_raw.get("entity_contact_mobile", "")
        if contact_phone:
            stripped_phone = re.sub(r'[\s\-()]', '', contact_phone)
            if not re.match(r'^[0-9]{4,15}$', stripped_phone):
                return self.error("Invalid phone number. Please enter 4-15 digits.", 400)

        website = prescreening_raw.get("website", "")
        if website:
            if not re.match(r'^(https?://)?([a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}(/.*)?$', website):
                return self.error("Invalid website URL. Please enter a valid domain (e.g. www.company.com).", 400)

        directors, ubos, intermediaries = get_application_parties(db, real_id)

        # W2-2: Require at least one director before submission
        if not directors:
            return self.error("At least one director is required before submitting the application.", 400)

        prescreening = safe_json_loads(app["prescreening_data"])
        scoring_input = build_prescreening_risk_input(
            application=app,
            prescreening_data=prescreening,
            directors=directors,
            ubos=ubos,
            intermediaries=intermediaries,
        )

        # ── Run real screening (Agents 1, 2, 3, 5) ──
        client_ip = self.get_client_ip()
        try:
            screening_report = run_full_screening(
                scoring_input, directors, ubos, client_ip=client_ip
            )
        except ScreeningProviderError as spe:
            logger.error(
                "Screening provider critical failure: app_id=%s ref=%s user=%s ip=%s stage=run_full_screening error=%s",
                real_id, app.get("ref", ""), user.get("sub", ""), client_ip, str(spe)[:300],
                exc_info=True,
            )
            return self.error(
                "Screening provider temporarily unavailable. Please retry in a moment.", 503
            )
        except Exception as screening_exc:
            logger.error(
                "Screening failed: app_id=%s ref=%s user=%s ip=%s stage=run_full_screening error=%s",
                real_id, app.get("ref", ""), user.get("sub", ""), client_ip, str(screening_exc)[:300],
                exc_info=True,
            )
            return self.error(
                "Screening provider temporarily unavailable. Please retry in a moment.", 503
            )

        # Track screening mode (live vs simulated)
        try:
            screening_mode = determine_screening_mode(screening_report)
            if not store_screening_mode(db, real_id, screening_mode):
                logger.warning(
                    "store_screening_mode returned False: app_id=%s mode=%s", real_id, screening_mode
                )
            screening_report["screening_mode"] = screening_mode
        except Exception as mode_exc:
            logger.error(
                "Screening mode storage failed: app_id=%s ref=%s stage=store_screening_mode error=%s",
                real_id, app.get("ref", ""), str(mode_exc)[:300],
                exc_info=True,
            )
            screening_report["screening_mode"] = "unknown"

        # Compute risk score
        risk = compute_risk_score(scoring_input)

        # EX-09: Capture risk config version at computation time
        try:
            from rule_engine import _get_risk_config_version
            risk["_config_version"] = _get_risk_config_version(db) or ""
        except Exception:
            risk["_config_version"] = ""

        # Elevate risk if screening found hits
        if screening_report["total_hits"] > 0:
            risk_bump = min(screening_report["total_hits"] * 8, 25)  # Up to +25 points
            risk["score"] = min(100, risk["score"] + risk_bump)
            # Re-classify level using CANONICAL thresholds (single source of truth)
            risk["level"] = classify_risk_level(risk["score"])
            risk["final_risk_level"] = risk["level"]
            risk["lane"] = {"LOW": "Fast Lane", "MEDIUM": "Standard Review", "HIGH": "EDD", "VERY_HIGH": "EDD"}[risk["level"]]
        # Priority E: policy-driven EDD routing on prescreening submit.
        # Even when level=MEDIUM, sector/jurisdiction/PEP/ownership can
        # mandate EDD. Run the deterministic v1 policy now so the case
        # is on the EDD lane and an edd_cases row is created
        # before the application reaches the officer queue.
        try:
            from routing_actuator import (
                apply_routing_decision,
                SOURCE_PRESCREENING_SUBMIT,
            )
            _routing_outcome = apply_routing_decision(
                db=db,
                app_row=app,
                risk_dict=risk,
                screening_summary=None,
                user=user,
                client_ip=self.get_client_ip() if hasattr(self, 'get_client_ip') else '',
                source=SOURCE_PRESCREENING_SUBMIT,
            )
            if _routing_outcome.get('route') == 'edd':
                risk['lane'] = 'EDD'
        except Exception as _routing_err:
            logger.warning(
                'apply_routing_decision (prescreening_submit) failed: %s',
                _routing_err,
            )
            risk["screening_elevated"] = True
            risk["screening_hits"] = screening_report["total_hits"]

        # ── DB write path: store screening results and update application ──
        try:
            # Store screening report in prescreening_data
            prescreening["screening_report"] = screening_report
            db.execute("UPDATE applications SET prescreening_data=? WHERE id=?",
                       (json.dumps(prescreening, default=str), real_id))

            # SCR-010: Dual-write normalized screening report (non-authoritative)
            try:
                from screening_config import is_abstraction_enabled
                if is_abstraction_enabled():
                    from screening_normalizer import normalize_screening_report
                    from screening_storage import (
                        ensure_normalized_table, persist_normalized_report,
                        persist_normalization_failure, compute_report_hash,
                    )
                    ensure_normalized_table(db)
                    _src_hash = compute_report_hash(screening_report)
                    _norm = normalize_screening_report(screening_report)
                    persist_normalized_report(
                        db, app.get("client_id", ""), real_id,
                        _norm, _src_hash,
                    )
            except Exception as _norm_exc:
                logger.warning(
                    "Normalized screening write failed: app_id=%s client_id=%s error_type=%s",
                    real_id, app.get("client_id", ""), type(_norm_exc).__name__,
                )
                try:
                    from screening_storage import (
                        ensure_normalized_table, persist_normalization_failure,
                        compute_report_hash,
                    )
                    ensure_normalized_table(db)
                    persist_normalization_failure(
                        db, app.get("client_id", ""), real_id,
                        compute_report_hash(screening_report),
                        type(_norm_exc).__name__,
                    )
                except Exception:
                    pass  # Do not block onboarding flow

            # Sync undeclared PEP detections back to director/UBO records
            for ds in screening_report.get("director_screenings", []):
                if ds.get("undeclared_pep"):
                    db.execute(
                        "UPDATE directors SET is_pep='Yes' WHERE application_id=? AND full_name=?",
                        (real_id, ds.get("person_name", ""))
                    )
            for us in screening_report.get("ubo_screenings", []):
                if us.get("undeclared_pep"):
                    db.execute(
                        "UPDATE ubos SET is_pep='Yes' WHERE application_id=? AND full_name=?",
                        (real_id, us.get("person_name", ""))
                    )

            db.execute("""
                UPDATE applications SET
                    status='submitted', submitted_at=datetime('now'),
                    risk_score=?, risk_level=?, risk_dimensions=?, onboarding_lane=?,
                    risk_computed_at=?, risk_config_version=?,
                    risk_escalations=?,
                    base_risk_level=?, final_risk_level=?, elevation_reason_text=?,
                    pre_approval_decision=NULL, pre_approval_notes=NULL,
                    pre_approval_officer_id=NULL, pre_approval_timestamp=NULL,
                    updated_at=datetime('now')
                WHERE id=?
            """, (risk["score"], risk["level"], json.dumps(risk["dimensions"]), risk["lane"],
                  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  str(risk.get("_config_version", "")),
                  json.dumps(risk.get("escalations", [])),
                  risk.get("base_risk_level", risk["level"]),
                  risk.get("final_risk_level", risk["level"]),
                  risk.get("elevation_reason_text", ""),
                  real_id))

            # After pre-screening: ALL risk levels see pricing first
            # Routing to pre-approval (HIGH/VERY_HIGH) happens after pricing acceptance
            db.execute("UPDATE applications SET status='pricing_review' WHERE id=?", (real_id,))

            # Get pricing for this risk level
            pricing = PRICING_TIERS.get(risk["level"], PRICING_TIERS["MEDIUM"])

            # Store pricing in prescreening data
            prescreening["pricing"] = pricing
            prescreening["pricing"]["risk_level"] = risk["level"]
            db.execute("UPDATE applications SET prescreening_data=? WHERE id=?",
                       (json.dumps(prescreening, default=str), real_id))

            # Notify compliance team for HIGH/VERY_HIGH risk — requires pre-approval
            if risk["level"] in ("HIGH", "VERY_HIGH"):
                compliance_users = db.execute("SELECT id FROM users WHERE role IN ('sco','co')").fetchall()
                for cu in compliance_users:
                    db.execute("INSERT INTO notifications (user_id, title, message) VALUES (?,?,?)",
                              (cu["id"], f"PRE-APPROVAL REQUIRED: {risk['level']}-Risk Application {app['ref']}",
                               f"Pre-screening {app['ref']} ({app['company_name']}) — Risk: {risk['level']} (Score: {risk['score']}). "
                               f"This application requires pre-approval before the client can proceed to KYC. "
                               f"Review pre-screening data and screening results in the Pre-Approval Queue."))

            db.commit()
        except Exception as db_exc:
            logger.error(
                "DB write failed after screening: app_id=%s ref=%s user=%s stage=post_screening_db_write error=%s",
                real_id, app.get("ref", ""), user.get("sub", ""), str(db_exc)[:300],
                exc_info=True,
            )
            try:
                db.rollback()
            except Exception:
                pass
            return self.error(
                "Failed to save screening results. Please retry your submission.", 500
            )

        flags_summary = f", Flags: {len(screening_report['overall_flags'])}" if screening_report["overall_flags"] else ""
        _after = {"status": "pricing_review", "risk_score": risk["score"],
                  "risk_level": risk["level"], "onboarding_lane": risk["lane"]}
        self.log_audit(user, "Pre-Screening Submitted", app["ref"],
                       f"Pre-screening submitted — Score: {risk['score']}, Level: {risk['level']}, Lane: {risk['lane']}{flags_summary}",
                       before_state=_before, after_state=_after)

        result_status = "pricing_review"
        self.success({
            "ref": app["ref"],
            "risk_score": risk["score"],
            "risk_level": risk["level"],
            "risk_dimensions": risk["dimensions"],
            "onboarding_lane": risk["lane"],
            "status": result_status,
            "requires_pre_approval": risk["level"] in ("HIGH", "VERY_HIGH"),
            "pricing": pricing,
            "screening": {
                "total_hits": screening_report["total_hits"],
                "flags": screening_report["overall_flags"],
                "degraded_sources": screening_report.get("degraded_sources", []),
                "api_sources": {
                    "sanctions": screening_report.get("director_screenings", [{}])[0].get("screening", {}).get("source", "none") if screening_report.get("director_screenings") else "none",
                    "corporate_registry": screening_report["company_screening"].get("source", "none"),
                    "ip_geolocation": screening_report["ip_geolocation"].get("source", "none") if screening_report.get("ip_geolocation") else "none",
                }
            }
        })


class PricingAcceptHandler(BaseHandler):
    """POST /api/applications/:id/accept-pricing — Client accepts pricing, proceeds to next step"""
    def post(self, app_id):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        if app["status"] != "pricing_review":
            db.close()
            return self.error("Application is not in pricing review stage", 400)

        real_id = app["id"]
        risk_level = app["risk_level"] or "MEDIUM"

        # Update status: accepted pricing
        db.execute("UPDATE applications SET status='pricing_accepted', updated_at=datetime('now') WHERE id=?", (real_id,))

        # Route based on risk level after pricing acceptance:
        # LOW/MEDIUM → proceed directly to KYC & Documents (straight-through)
        # HIGH/VERY_HIGH → pre-approval review (KYC blocked until officer pre-approves)
        if risk_level in ("HIGH", "VERY_HIGH"):
            next_status = "pre_approval_review"
            db.execute("UPDATE applications SET status=? WHERE id=?", (next_status, real_id))
            message = "Pricing accepted. Your application is now undergoing an initial compliance review before document submission."
            # Notify compliance officers
            compliance_users = db.execute("SELECT id FROM users WHERE role IN ('sco','co')").fetchall()
            for cu in compliance_users:
                db.execute("INSERT INTO notifications (user_id, title, message) VALUES (?,?,?)",
                          (cu["id"], f"PRE-APPROVAL REQUIRED: {app['ref']}",
                           f"{app['company_name']} — Risk: {risk_level}. Client accepted pricing. "
                           f"Pre-approval required before KYC proceeds."))
        else:
            next_status = "kyc_documents"
            db.execute("UPDATE applications SET status=? WHERE id=?", (next_status, real_id))
            message = "Pricing accepted. Please proceed with KYC verification and document upload."

        db.commit()
        db.close()

        self.log_audit(user, "Pricing Accepted", app["ref"], f"Pricing accepted — Risk: {risk_level}, Next: {next_status}")
        self.success({"status": next_status, "message": message, "risk_level": risk_level})


class KYCSubmitHandler(BaseHandler):
    """POST /api/applications/:id/submit-kyc — Submit KYC documents for compliance review"""
    def post(self, app_id):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        # Allow KYC submission from multiple valid states (handles edge cases where pricing was accepted client-side)
        valid_kyc_submit_statuses = ("kyc_documents", "pricing_accepted", "pricing_review", "submitted", "draft", "pre_approved")
        if app["status"] not in valid_kyc_submit_statuses:
            db.close()
            return self.error(f"Application cannot be submitted from status '{app['status']}'", 400)

        real_id = app["id"]

        # Check that at least one document has been uploaded
        doc_count = db.execute("SELECT COUNT(*) as c FROM documents WHERE application_id=?", (real_id,)).fetchone()["c"]
        if doc_count == 0:
            db.close()
            return self.error("Please upload at least one document before submitting", 400)

        # Always recompute risk at KYC submission — data may have changed since pre-screening
        risk_score = app["risk_score"] or 0
        risk_level = app["risk_level"] or "MEDIUM"
        rr = recompute_risk(db, real_id, "kyc_submission", user=user,
                            log_audit_fn=self.log_audit)
        if rr.get("recomputed"):
            risk_score = rr["new_score"]
            risk_level = rr["new_level"]

        # W2-4: Set status to kyc_submitted (previously dead state, now active)
        db.execute("""
            UPDATE applications SET
                status='kyc_submitted',
                updated_at=datetime('now')
            WHERE id=?
        """, (real_id,))

        # Priority C: KYC submission is the final client-side hand-off into
        # compliance review. The portal draft (client_sessions row) is no
        # longer needed at this point — discard it so a stale "Resume"
        # banner does not reappear on the dashboard after submission.
        try:
            db.execute(
                "DELETE FROM client_sessions WHERE application_id=?",
                (real_id,),
            )
        except Exception as exc:
            # Non-fatal: KYC submission is the source of truth, not the draft.
            logger.warning(
                "Failed to clear draft session for app %s after KYC submit: %s",
                real_id, exc,
            )

        # Notify ALL compliance officers
        compliance_users = db.execute("SELECT id FROM users WHERE role IN ('sco','co','admin')").fetchall()
        for cu in compliance_users:
            db.execute("INSERT INTO notifications (user_id, title, message) VALUES (?,?,?)",
                      (cu["id"], f"KYC Submitted — Ready for Review: {app['ref']}",
                       f"{app['company_name']} has completed KYC & document upload. Risk: {risk_level} (Score: {risk_score}). Awaiting compliance approval."))

        db.commit()
        db.close()

        self.log_audit(user, "KYC Submitted", app["ref"],
                       f"KYC documents submitted for compliance review — {doc_count} document(s)")
        self.success({
            "status": "kyc_submitted",
            "message": "Your documents have been submitted for compliance review. An officer will review your application shortly.",
            "documents_uploaded": doc_count
        })


# ══════════════════════════════════════════════════════════
# PRE-APPROVAL DECISION ENDPOINT (v2.1: Risk-Gated Flow)
# ══════════════════════════════════════════════════════════

class PreApprovalDecisionHandler(BaseHandler):
    """POST /api/applications/:id/pre-approval-decision

    Allows compliance officers to pre-approve, reject, or request info
    on HIGH/VERY_HIGH risk applications BEFORE KYC stage.

    Decisions:
      PRE_APPROVE   → status = pre_approved → pricing shown → KYC unlocked
      REJECT        → status = rejected (terminal)
      REQUEST_INFO  → status = draft (client re-edits pre-screening)
    """
    def post(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        if not self.check_rate_limit("pre_approval", max_attempts=10, window_seconds=60):
            return

        data = self.get_json()
        db = get_db()

        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        real_id = app["id"]

        # EX-05: Capture before-state for audit trail
        _before = snapshot_app_state(app)

        # Enforce: only allowed when status = pre_approval_review
        if app["status"] != "pre_approval_review":
            db.close()
            return self.error(
                f"Pre-approval decision not allowed: application is in '{app['status']}' status. "
                "Only applications in 'pre_approval_review' can receive pre-approval decisions.",
                400
            )

        # Validate decision
        decision = (data.get("decision") or "").upper()
        valid_decisions = ["PRE_APPROVE", "REJECT", "REQUEST_INFO"]
        if decision not in valid_decisions:
            db.close()
            return self.error(f"Invalid pre-approval decision. Must be one of: {', '.join(valid_decisions)}", 400)

        notes = sanitize_input(data.get("notes", ""))
        if not notes:
            db.close()
            return self.error("Pre-approval decision notes are required", 400)

        # Idempotency: check if a decision was already recorded
        if app.get("pre_approval_decision"):
            db.close()
            return self.error(
                f"Pre-approval decision already recorded: {app['pre_approval_decision']}. "
                "Duplicate decisions are blocked for audit integrity.",
                409
            )

        # Apply decision
        if decision == "PRE_APPROVE":
            new_status = "kyc_documents"
            message = "Application pre-approved. Client can now proceed to KYC document submission."
            # Auto-transition to kyc_documents (pricing was already accepted before pre-approval)
            db.execute("""
                UPDATE applications SET
                    status='kyc_documents',
                    pre_approval_decision='PRE_APPROVE',
                    pre_approval_notes=?,
                    pre_approval_officer_id=?,
                    pre_approval_timestamp=datetime('now'),
                    updated_at=datetime('now')
                WHERE id=?
            """, (notes, user["sub"], real_id))

            # Notify the client
            if app.get("client_id"):
                db.execute("INSERT INTO client_notifications (client_id, application_id, title, message, notification_type) VALUES (?,?,?,?,?)",
                          (app["client_id"], real_id,
                           "Application Pre-Approved — Proceed to KYC Documents",
                           f"Your application {app['ref']} has passed initial compliance review. "
                           f"You can now proceed with KYC verification and document submission.",
                           "pre_approval"))

        elif decision == "REJECT":
            new_status = "rejected"
            message = "Application rejected at pre-approval stage."
            db.execute("""
                UPDATE applications SET
                    status='rejected',
                    pre_approval_decision='REJECT',
                    pre_approval_notes=?,
                    pre_approval_officer_id=?,
                    pre_approval_timestamp=datetime('now'),
                    decided_at=datetime('now'),
                    decision_by=?,
                    decision_notes=?,
                    updated_at=datetime('now')
                WHERE id=?
            """, (notes, user["sub"], user["sub"], f"Rejected at pre-approval: {notes}", real_id))

            # Notify the client
            if app.get("client_id"):
                db.execute("INSERT INTO client_notifications (client_id, application_id, title, message, notification_type) VALUES (?,?,?,?,?)",
                          (app["client_id"], real_id,
                           "Application Update",
                           f"Your application {app['ref']} has been reviewed. "
                           f"Unfortunately, we are unable to proceed with your application at this time. "
                           f"Please contact our compliance team for further information.",
                           "pre_approval_reject"))

        elif decision == "REQUEST_INFO":
            new_status = "draft"
            message = "Additional information requested. Client can re-edit pre-screening data."
            db.execute("""
                UPDATE applications SET
                    status='draft',
                    pre_approval_decision='REQUEST_INFO',
                    pre_approval_notes=?,
                    pre_approval_officer_id=?,
                    pre_approval_timestamp=datetime('now'),
                    updated_at=datetime('now')
                WHERE id=?
            """, (notes, user["sub"], real_id))

            # Notify the client
            if app.get("client_id"):
                db.execute("INSERT INTO client_notifications (client_id, application_id, title, message, notification_type) VALUES (?,?,?,?,?)",
                          (app["client_id"], real_id,
                           "Additional Information Required",
                           f"Our compliance team requires additional information for application {app['ref']}. "
                           f"Please update your pre-screening data and resubmit. Officer notes: {notes}",
                           "pre_approval_rmi"))

        # Audit trail — authoritative in-transaction record carries before/after state
        _after = {"status": new_status, "pre_approval_decision": decision}
        db.execute("""INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address, before_state, after_state)
                     VALUES (?,?,?,?,?,?,?,?,?)""",
                   (user.get("sub",""), user.get("name",""), user.get("role",""),
                    f"Pre-Approval: {decision}", app["ref"],
                    f"Pre-approval decision: {decision} | Risk: {app['risk_level']} (Score: {app['risk_score']}) | Notes: {notes}",
                    self.get_client_ip(), _safe_json(_before), _safe_json(_after)))

        db.commit()
        db.close()

        self.log_audit(user, f"Pre-Approval {decision}", app["ref"],
                       f"Pre-approval decision: {decision} — {notes}")

        self.success({
            "status": "decision_recorded",
            "decision": decision,
            "application_status": new_status,
            "message": message
        }, 201)


# ══════════════════════════════════════════════════════════
# DOCUMENT UPLOAD ENDPOINTS
# ══════════════════════════════════════════════════════════

class DocumentUploadHandler(BaseHandler):
    """GET/POST /api/applications/:id/documents"""

    def get(self, app_id):
        """Return all documents for an application."""
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        app = db.execute("SELECT id, ref, client_id FROM applications WHERE id=? OR ref=?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        docs = [dict(d) for d in db.execute(
            "SELECT id, application_id, person_id, doc_type, doc_name, file_size, mime_type, verification_status, verification_results, verified_at, review_status, review_comment, reviewed_by, reviewed_at FROM documents WHERE application_id = ?",
            (app["id"],)).fetchall()]

        # Parse verification_results JSON strings
        for doc in docs:
            if doc.get("verification_results"):
                try:
                    doc["verification_results"] = safe_json_loads(doc["verification_results"])
                except (json.JSONDecodeError, TypeError):
                    pass
            doc["reviewed_by_name"] = resolve_user_display_name(db, doc.get("reviewed_by"))

        db.close()

        self.success(docs)

    def post(self, app_id):
        user = self.require_auth()
        if not user:
            return

        if not self.check_rate_limit("doc_upload", max_attempts=30, window_seconds=60):
            return

        db = get_db()
        app = db.execute("SELECT id, ref, client_id, risk_level, status, pre_approval_decision FROM applications WHERE id=? OR ref=?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        # v2.1: KYC access control — HIGH/VERY_HIGH risk requires pre-approval before document upload
        # In production, this gate is enforced; in staging, allow uploads for testing
        risk_level = (app.get("risk_level") or "").upper()
        if risk_level in ("HIGH", "VERY_HIGH") and ENVIRONMENT == "production":
            if app.get("pre_approval_decision") != "PRE_APPROVE":
                db.close()
                return self.error(
                    "Pre-approval required: HIGH/VERY_HIGH risk applications must be pre-approved "
                    "by a compliance officer before KYC documents can be uploaded.",
                    403
                )

        if "file" not in self.request.files:
            db.close()
            return self.error("No file provided")

        file_info = self.request.files["file"][0]
        filename = file_info["filename"]
        # Sanitize filename
        filename = os.path.basename(filename)
        body = file_info["body"]
        content_type = file_info.get("content_type", "application/octet-stream")

        if len(body) > MAX_UPLOAD_MB * 1024 * 1024:
            db.close()
            return self.error(f"File exceeds {MAX_UPLOAD_MB}MB limit")

        # Validate file upload (mandatory)
        is_valid, upload_error = FileUploadValidator.validate(filename, content_type, body)
        if not is_valid:
            db.close()
            return self.error(f"File rejected: {upload_error}", 400)

        # Save file locally (as cache; S3 is the durable store in production)
        doc_id = uuid.uuid4().hex[:16]
        ext = os.path.splitext(filename)[1]
        safe_name = f"{app['id']}_{doc_id}{ext}"
        file_path = os.path.join(UPLOAD_DIR, safe_name)

        with open(file_path, "wb") as f:
            f.write(body)

        # Upload to S3 — required in production/staging, best-effort in demo
        s3_key = None
        if HAS_S3:
            try:
                s3 = get_s3_client()
                success, key_or_error = s3.upload_document(
                    file_data=body,
                    client_id=app["id"],
                    doc_type=self.get_argument("doc_type", "general"),
                    filename=safe_name,
                    content_type=content_type,
                    metadata={"original_name": filename}
                )
                if success:
                    s3_key = key_or_error
                    logger.info(f"Document {doc_id} uploaded to S3: {s3_key}")
                else:
                    logger.error(f"S3 upload failed for {doc_id}: {key_or_error}")
                    if is_production() or is_staging():
                        db.close()
                        return self.error("Document upload failed: unable to store document durably. Please retry.", 500)
                    logger.warning(f"S3 upload failed in demo — using local storage only for {doc_id}")
            except Exception as e:
                logger.error(f"S3 upload exception for {doc_id}: {e}")
                if is_production() or is_staging():
                    db.close()
                    return self.error("Document upload failed: unable to store document durably. Please retry.", 500)
                logger.warning(f"S3 upload exception in demo — using local storage only for {doc_id}")
                s3_key = None
        elif is_production() or is_staging():
            db.close()
            return self.error("Document upload failed: S3 storage is not available. Contact administrator.", 500)

        if ENVIRONMENT == "production" and not s3_key:
            db.close()
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except OSError as cleanup_err:
                logger.warning(f"Failed to clean up local file after storage error for {doc_id}: {cleanup_err}")
            return self.error(
                "Document storage is temporarily unavailable. Please retry once durable storage is restored.",
                503
            )

        doc_type = self.get_argument("doc_type", "general")
        person_id = self.get_argument("person_id", None)

        # Defense-in-depth: normalize portal HTML IDs to canonical doc_type values
        _DOC_TYPE_NORMALIZE = {
            "doc-coi": "cert_inc", "doc-memarts": "memarts", "doc-shareholders": "reg_sh",
            "doc-directors-reg": "reg_dir", "doc-financials": "fin_stmt", "doc-proof-address": "poa",
            "doc-board-res": "board_res", "doc-structure-chart": "structure_chart",
            "doc-bank-ref": "bankref", "doc-license-cert": "licence",
            "doc-contracts": "contracts", "doc-source-wealth-proof": "source_wealth",
            "doc-source-funds-proof": "source_funds", "doc-bank-statements": "bank_statements",
            "doc-aml-policy": "aml_policy",
        }
        doc_type = _DOC_TYPE_NORMALIZE.get(doc_type, doc_type)

        # Validate person_id refers to an existing person if provided
        person_resolved = None
        if person_id:
            person_resolved = resolve_application_person(db, app["id"], person_id)
            if not person_resolved:
                logger.warning(
                    f"[doc-upload] person_id={person_id!r} not found in application={app['id']} "
                    f"doc_type={doc_type} file={filename} — storing document with unresolved person_id"
                )

        logger.info(
            f"[doc-upload] app={app['id']} doc_id={doc_id} doc_type={doc_type} "
            f"person_id={person_id!r} person_resolved={'yes' if person_resolved else 'no'} "
            f"file={filename} size={len(body)} s3={'yes' if s3_key else 'no'}"
        )

        db.execute("""
            INSERT INTO documents (id, application_id, person_id, doc_type, doc_name, file_path, s3_key, file_size, mime_type)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (doc_id, app["id"], person_id, doc_type, filename, file_path, s3_key, len(body), content_type))
        db.commit()
        db.close()

        audit_detail = f"Document uploaded: {filename} ({doc_type})"
        if person_id:
            audit_detail += f" person_id={person_id}"
        self.log_audit(user, "Upload", app["ref"], audit_detail)
        self.success({"id": doc_id, "doc_name": filename, "doc_type": doc_type, "file_size": len(body), "s3_key": s3_key}, 201)


class DocumentDeleteHandler(BaseHandler):
    """DELETE /api/applications/:app_id/documents/:doc_id — delete an uploaded document"""
    def delete(self, app_id, doc_id):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        app = db.execute(
            "SELECT id, ref, client_id, status FROM applications WHERE id=? OR ref=?",
            (app_id, app_id)
        ).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        # Only allow deletion in pre-submission statuses (post-submission documents
        # are part of the compliance audit trail and must not be removed)
        allowed_statuses = ("draft", "kyc_documents", "pricing_accepted", "pricing_review", "pre_approved")
        if app["status"] not in allowed_statuses:
            db.close()
            return self.error("Documents cannot be deleted after submission.", 403)

        doc = db.execute(
            "SELECT id, doc_name, file_path, s3_key FROM documents WHERE id=? AND application_id=?",
            (doc_id, app["id"])
        ).fetchone()
        if not doc:
            db.close()
            return self.error("Document not found", 404)

        # Delete local file if exists
        if doc["file_path"] and os.path.exists(doc["file_path"]):
            try:
                os.remove(doc["file_path"])
            except OSError:
                pass

        # Delete from S3 if applicable (best-effort; DB record removal proceeds regardless)
        if doc.get("s3_key") and HAS_S3:
            try:
                s3 = get_s3_client()
                deleted, message = s3.delete_document(doc["s3_key"])
                if not deleted:
                    logging.warning("S3 deletion failed for key %s: %s", doc["s3_key"], message)
            except Exception as e:
                logging.warning("S3 deletion failed for key %s: %s", doc["s3_key"], e)

        db.execute("DELETE FROM documents WHERE id=? AND application_id=?", (doc_id, app["id"]))
        db.commit()
        db.close()

        self.log_audit(user, "Delete", app["ref"], f"Document deleted: {doc['doc_name']}")
        self.success({"deleted": True, "id": doc_id})


class DocumentVerifyHandler(BaseHandler):
    """POST /api/documents/:id/verify — trigger AI verification"""
    def post(self, doc_id):
        user = self.require_auth()
        if not user:
            return

        db = get_db()

        # P0-3: Check if Agent 1 (document verification) is enabled before executing
        agent1 = db.execute("SELECT enabled FROM ai_agents WHERE agent_number=1").fetchone()
        if agent1 and not agent1["enabled"]:
            db.close()
            self.log_audit(user, "Agent Skipped", "Agent 1", "Document verification skipped — agent disabled")
            self.success({
                "status": "skipped",
                "message": "Document verification agent is currently disabled",
                "checks": [],
                "requires_review": True
            })
            return

        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            db.close()
            return self.error("Document not found", 404)

        # EX-05: Capture before-state for audit trail (new audit event for document verification)
        _doc_before = {"verification_status": doc.get("verification_status"),
                       "doc_name": doc.get("doc_name"), "doc_type": doc.get("doc_type")}

        # Get the related application and person for screening
        app = db.execute("SELECT * FROM applications WHERE id=?", (doc["application_id"],)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found for document", 404)

        # ── AI Document Verification (real vision analysis) ──
        checks = []
        all_passed = True

        # Resolve file path — prefer local, fallback to S3 download
        file_path = doc.get("file_path", "")
        if file_path and not os.path.isabs(file_path):
            file_path = os.path.join(UPLOAD_DIR, os.path.basename(file_path))

        file_source = "none"
        if file_path and os.path.isfile(file_path):
            file_source = "local"
        elif doc.get("s3_key") and HAS_S3:
            # S3 fallback: local file missing (e.g. after container redeploy) but S3 key exists
            try:
                s3 = get_s3_client()
                s3_ok, s3_data = s3.download_document(doc["s3_key"])
                if s3_ok and isinstance(s3_data, bytes):
                    ext = os.path.splitext(doc.get("doc_name", "") or "")[1] or ".bin"
                    cache_name = f"s3_cache_{doc_id}{ext}"
                    cache_path = os.path.join(UPLOAD_DIR, cache_name)
                    with open(cache_path, "wb") as _cf:
                        _cf.write(s3_data)
                    file_path = cache_path
                    file_source = "s3"
                    logger.info(f"[verify] doc={doc_id} retrieved from S3 ({len(s3_data)} bytes)")
                else:
                    logger.warning(f"[verify] doc={doc_id} S3 download failed: {s3_data}")
            except Exception as s3_err:
                logger.error(f"[verify] doc={doc_id} S3 fallback error: {s3_err}")

        verification_context = build_document_verification_context(db, app, doc)
        person_record = verification_context["person_record"]
        raw_doc_type = verification_context["raw_doc_type"]
        base_doc_type = verification_context["base_doc_type"]
        doc_category = verification_context["doc_category"]
        entity_name = verification_context["entity_name"]
        person_name = verification_context["person_name"]
        directors_list = verification_context["directors_list"]
        ubos_list = verification_context["ubos_list"]
        verify_name = entity_name if doc_category == "company" else person_name

        # Diagnostic logging for verification context
        logger.info(
            f"[verify-context] doc={doc_id} app={doc.get('application_id','')} "
            f"raw_doc_type={raw_doc_type} base_doc_type={base_doc_type} doc_category={doc_category} "
            f"person_id={doc.get('person_id','')} verify_name={verify_name!r} entity_name={entity_name!r} "
            f"file_source={file_source} local_exists={os.path.isfile(file_path) if file_path else False} "
            f"s3_key={'yes' if doc.get('s3_key') else 'no'}"
        )

        # Load check overrides from ai_checks table (hybrid/AI checks only)
        check_overrides = None
        try:
            check_category = "entity" if doc_category == "company" else "person"
            ai_check_row = db.execute(
                "SELECT checks FROM ai_checks WHERE doc_type=? AND category=?",
                (base_doc_type, check_category)
            ).fetchone()
            if ai_check_row and ai_check_row["checks"]:
                loaded_checks = safe_json_loads(ai_check_row["checks"])
                if loaded_checks:
                    check_overrides = loaded_checks
            if not check_overrides:
                logger.warning(f"No DB checks found for doc_type={base_doc_type}, category={check_category}. Using matrix fallback.")
        except Exception as e:
            logger.warning(f"Could not load ai_checks for {base_doc_type}: {e}. Using matrix fallback.")

        # Build effective declared-data context from stored application data + save/resume overlay
        prescreening_data = verification_context["prescreening_data"]
        risk_level = (app.get("risk_level") or "MEDIUM") if app else "MEDIUM"

        # Compute SHA-256 hashes of other documents already uploaded for this application
        # Used by GATE-03 duplicate detection
        existing_hashes = []
        if app:
            try:
                other_docs = db.execute(
                    "SELECT file_path FROM documents WHERE application_id=? AND id!=?",
                    (app["id"], doc_id)
                ).fetchall()
                for od in other_docs:
                    fp = od.get("file_path", "")
                    if fp and not os.path.isabs(fp):
                        fp = os.path.join(UPLOAD_DIR, os.path.basename(fp))
                    if fp and os.path.isfile(fp):
                        try:
                            h = hashlib.sha256(open(fp, "rb").read()).hexdigest()
                            existing_hashes.append(h)
                        except OSError:
                            pass
            except Exception as e:
                logger.debug(f"Could not compute existing hashes: {e}")

        ai_result = None
        try:
            if HAS_DOC_VERIFICATION:
                _claude = ClaudeClient(
                    api_key=_CFG_ANTHROPIC_API_KEY,
                    monthly_budget_usd=_CFG_CLAUDE_BUDGET_USD,
                    mock_mode=_CFG_CLAUDE_MOCK_MODE,
                ) if HAS_CLAUDE_CLIENT else None

                ai_result = verify_document_layered(
                    doc_type=base_doc_type,
                    category="entity" if doc_category == "company" else "person",
                    file_path=file_path,
                    file_size=doc.get("file_size") or 0,
                    mime_type=doc.get("mime_type") or "",
                    prescreening_data=prescreening_data,
                    risk_level=risk_level,
                    existing_hashes=existing_hashes,
                    claude_client=_claude,
                    entity_name=entity_name,
                    person_name=verify_name,
                    directors=directors_list,
                    ubos=ubos_list,
                    check_overrides=check_overrides,
                    file_name=doc.get("doc_name", ""),
                )

                # P0-2: Guard against rejected/invalid AI responses
                if ai_result.get("_rejected") or ai_result.get("_validated") is False:
                    logger.warning(f"Layered verification rejected for doc {doc_id}: {ai_result.get('error', 'validation failed')}")
                    checks = [{"label": "AI Verification", "type": "validity", "result": "fail",
                               "message": "Verification output failed validation — manual review required"}]
                    all_passed = False
                else:
                    checks = ai_result.get("checks", [])
                    # P0-5: No pass without evidence
                    if not checks:
                        all_passed = False
                    else:
                        all_passed = ai_result.get("overall") == "verified"

            elif HAS_CLAUDE_CLIENT:
                # Fallback: legacy single-Claude-call path if layered engine unavailable
                claude_client = ClaudeClient(
                    api_key=_CFG_ANTHROPIC_API_KEY,
                    monthly_budget_usd=_CFG_CLAUDE_BUDGET_USD,
                    mock_mode=_CFG_CLAUDE_MOCK_MODE,
                )
                ai_result = claude_client.verify_document(
                    doc_type=base_doc_type,
                    file_name=doc.get("doc_name", ""),
                    person_name=verify_name,
                    doc_category=doc_category,
                    file_path=file_path,
                    check_overrides=check_overrides,
                    entity_name=entity_name,
                    directors=directors_list,
                    ubos=ubos_list,
                )
                if ai_result.get("_rejected") or ai_result.get("_validated") is False:
                    logger.warning(f"AI verification rejected for doc {doc_id}: {ai_result.get('error', 'schema validation failed')}")
                    checks = [{"label": "AI Verification", "type": "validity", "result": "fail",
                               "message": "AI output failed validation — manual review required"}]
                    all_passed = False
                else:
                    checks = ai_result.get("checks", [])
                    if not checks:
                        all_passed = False
                    else:
                        all_passed = ai_result.get("overall") == "verified"
            else:
                logger.warning("Document verification engine not available — flagging for manual review")
                checks = [{"label": "AI Verification", "type": "validity", "result": "warn",
                           "message": "Verification engine unavailable — manual review required"}]
                all_passed = False
        except Exception as e:
            logger.error(f"Document verification failed: {e}")
            checks = [{"label": "AI Verification", "type": "validity", "result": "warn",
                       "message": f"Verification error: {str(e)[:100]}. Manual review required."}]
            all_passed = False

        # If it's an identity document, run sanctions/PEP screening
        sanctions_result = None
        id_doc_types = ["passport", "national_id", "id_card", "drivers_license", "director_id", "ubo_id"]
        if doc["doc_type"] in id_doc_types and doc["person_id"]:
            try:
                person = resolve_application_person(db, doc["application_id"], doc["person_id"])
                if person:
                    person_name = person.get("full_name") or ""
                    if not person_name:
                        logger.warning(f"[verify] doc={doc_id} person_id={doc['person_id']} resolved but full_name is empty — skipping sanctions screening")
                    else:
                        sanctions_result = screen_sumsub_aml(
                            person_name,
                            nationality=person.get("nationality"),
                            entity_type="Person"
                        )
                        if sanctions_result["matched"]:
                            all_passed = False
                            checks.append({
                                "label": "Sanctions/PEP Screening",
                                "type": "sanctions",
                                "rule": "Screened against Sumsub AML watchlists and PEP databases",
                                "result": "fail",
                                "message": f"MATCH FOUND — {len(sanctions_result['results'])} hit(s) on sanctions/PEP lists",
                                "details": sanctions_result["results"],
                                "source": sanctions_result["source"]
                            })
                        else:
                            checks.append({
                                "label": "Sanctions/PEP Screening",
                                "type": "sanctions",
                                "rule": "Screened against Sumsub AML watchlists and PEP databases",
                                "result": "pass",
                                "message": "No matches found on sanctions or PEP lists",
                                "source": sanctions_result["source"]
                            })
                else:
                    logger.warning(
                        f"[verify] doc={doc_id} person_id={doc['person_id']} not found in application={doc['application_id']} — skipping sanctions screening"
                    )
            except Exception as screening_err:
                logger.error(f"[verify] Sanctions screening failed for doc={doc_id} person_id={doc.get('person_id')}: {screening_err}")
                checks.append({
                    "label": "Sanctions/PEP Screening",
                    "type": "sanctions",
                    "result": "warn",
                    "message": "Screening temporarily unavailable. Manual review required."
                })
                all_passed = False

        status = "verified" if all_passed else "flagged"

        # Finding 9: Propagate ai_source so mock/degraded results are explicit
        ai_source = "live"
        if ai_result:
            ai_source = ai_result.get("ai_source", "live")
        if not HAS_CLAUDE_CLIENT:
            ai_source = "unavailable"
        if _CFG_CLAUDE_MOCK_MODE:
            ai_source = "mock"

        # Build system_warning if file was inaccessible
        system_warning = None
        if file_source == "none":
            system_warning = "file_not_accessible"
        elif not verify_name and doc_category == "company":
            system_warning = "entity_context_missing"

        layered_results = to_legacy_result(ai_result or {"checks": checks, "overall": status}) if to_legacy_result else {
            "checks": checks,
            "overall": status,
        }
        layered_results.update({
            "overall": status,
            "checks": checks,
            "ai_source": ai_source,
            "file_source": file_source,
            "system_warning": system_warning,
            "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "sanctions_screening": sanctions_result,
            "doc_category": doc_category,
            "subject_id": doc.get("person_id") or (app.get("id") if app else ""),
            "subject_type": verification_context["subject_type"],
        })
        results = json.dumps(layered_results, default=str)

        db.execute("UPDATE documents SET verification_status=?, verification_results=?, verified_at=datetime('now') WHERE id=?",
                   (status, results, doc_id))
        db.commit()
        db.close()

        # EX-05: New audit event — log document verification with before/after state
        _doc_after = {"verification_status": status, "checks_count": len(checks),
                      "doc_name": doc.get("doc_name"), "doc_type": doc.get("doc_type")}
        self.log_audit(user, "Document Verified", app["ref"],
                       f"Document '{doc.get('doc_name', doc_id)}' verification: {status} ({len(checks)} checks)",
                       before_state=_doc_before, after_state=_doc_after)

        # Improvement 8: Log agent execution for traceability
        try:
            app_id = doc.get("application_id", "")
            log_agent_execution(
                application_id=app_id,
                agent_name="verify_document",
                agent_number=1,
                status=status,
                checks=checks,
                flags=[c.get("message", "") for c in checks if (c.get("result") or "").lower() in ("fail", "warn")],
                requires_review=not all_passed,
                document_id=doc_id,
            )
        except Exception as e:
            logger.debug(f"Agent execution logging failed: {e}")

        self.success({
            "doc_id": doc_id,
            "status": status,
            "verification_status": status,
            "checks": checks,
            "verification_results": layered_results,
            "verified_at": layered_results.get("verified_at"),
        })


class DocumentReviewHandler(BaseHandler):
    """POST /api/documents/:id/review — persist officer document review outcome"""
    def post(self, doc_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return

        data = self.get_json() or {}
        review_status = str(data.get("status", "pending")).strip().lower()
        review_comment = str(data.get("comment", "") or "").strip()
        allowed_statuses = {"pending", "accepted", "rejected", "info_requested"}
        if review_status not in allowed_statuses:
            return self.error("Invalid document review status", 400)

        db = get_db()
        doc = db.execute(
            "SELECT id, application_id, doc_name, doc_type, verification_status, review_status, review_comment, reviewed_by "
            "FROM documents WHERE id=?", (doc_id,)
        ).fetchone()
        if not doc:
            db.close()
            return self.error("Document not found", 404)

        app = db.execute("SELECT id, ref, client_id FROM applications WHERE id=?", (doc["application_id"],)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        user_role = (user.get("role") or "").lower()

        # Senior-officer override gate for flagged documents (EX-06):
        # Only admin/sco may accept a flagged document, and a non-empty reason
        # (review_comment) is mandatory.  Non-senior roles are rejected outright.
        is_flagged = (doc.get("verification_status") or "").lower() == "flagged"
        if is_flagged and review_status == "accepted":
            if user_role not in ("admin", "sco"):
                db.close()
                return self.error(
                    "Only senior compliance officers (admin/sco) may accept flagged documents", 403
                )
            if not review_comment:
                db.close()
                return self.error(
                    "A documented reason is required when accepting a flagged document", 400
                )

        # Capture before-state for audit trail
        _before = {
            "verification_status": doc.get("verification_status"),
            "review_status": doc.get("review_status"),
            "review_comment": doc.get("review_comment"),
            "reviewed_by": doc.get("reviewed_by"),
        }

        db.execute("""
            UPDATE documents
            SET review_status = ?, review_comment = ?, reviewed_by = ?, reviewer_role = ?, reviewed_at = datetime('now')
            WHERE id = ?
        """, (review_status, review_comment, user.get("sub", ""), user_role, doc_id))
        db.commit()

        reviewed_doc = db.execute("""
            SELECT id, review_status, review_comment, reviewed_by, reviewed_at, reviewer_role
            FROM documents WHERE id = ?
        """, (doc_id,)).fetchone()
        result = dict(reviewed_doc)
        result["reviewed_by_name"] = resolve_user_display_name(db, result.get("reviewed_by"))
        db.close()

        # Audit trail — enhanced for flagged-document override
        _after = {
            "verification_status": doc.get("verification_status"),
            "review_status": review_status,
            "review_comment": review_comment,
            "reviewed_by": user.get("sub", ""),
            "reviewer_role": user_role,
        }
        if is_flagged and review_status == "accepted":
            audit_action = "Document Accepted With Findings"
            audit_detail = (
                f"Senior override: flagged document '{doc['doc_name']}' (type={doc.get('doc_type')}) "
                f"accepted by {user.get('name', '')} (role={user_role}). "
                f"Reason: {review_comment}"
            )
        else:
            audit_action = "Document Review"
            audit_detail = (
                f"Document {doc['doc_name']} marked {review_status}"
                + (f" — {review_comment}" if review_comment else "")
            )

        self.log_audit(
            user,
            audit_action,
            app["ref"],
            audit_detail,
            before_state=_before,
            after_state=_after,
        )
        self.success(result)


class DocumentAIVerifyHandler(BaseHandler):
    """POST /api/documents/ai-verify — AI document verification using Claude"""
    def post(self):
        user = self.require_auth()
        if not user:
            return

        if not self.check_rate_limit("ai_verify", max_attempts=10, window_seconds=60):
            return

        try:
            body = json.loads(self.request.body)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.warning(f"Invalid JSON in AI verify request: {e}")
            return self.error("Invalid JSON", 400)

        doc_type = body.get("doc_type", "")
        file_name = body.get("file_name", "")
        person_name = body.get("person_name", "")
        doc_category = body.get("doc_category", "identity")
        doc_id = body.get("doc_id", "")
        entity_name = body.get("entity_name", "")
        directors = body.get("directors", [])
        ubos = body.get("ubos", [])
        app_id = body.get("application_id", "")

        # Defense-in-depth: normalize portal HTML IDs to canonical doc_type values
        _DOC_TYPE_NORMALIZE = {
            "doc-coi": "cert_inc", "doc-memarts": "memarts", "doc-shareholders": "reg_sh",
            "doc-directors-reg": "reg_dir", "doc-financials": "fin_stmt", "doc-proof-address": "poa",
            "doc-board-res": "board_res", "doc-structure-chart": "structure_chart",
            "doc-bank-ref": "bankref", "doc-license-cert": "licence",
            "doc-contracts": "contracts", "doc-source-wealth-proof": "source_wealth",
            "doc-source-funds-proof": "source_funds", "doc-bank-statements": "bank_statements",
            "doc-aml-policy": "aml_policy",
        }
        doc_type = _DOC_TYPE_NORMALIZE.get(doc_type, doc_type)

        if not doc_type or not file_name:
            return self.error("doc_type and file_name are required", 400)

        # If pre-screening context not provided in request, look it up from the application
        if (not entity_name or not directors) and app_id:
            try:
                db = get_db()
                app_row = db.execute("SELECT company_name, prescreening_data FROM applications WHERE id=?", (app_id,)).fetchone()
                if app_row:
                    if not entity_name:
                        entity_name = app_row.get("company_name", "") or ""
                    if not directors:
                        dir_rows = db.execute("SELECT full_name FROM directors WHERE application_id=? ORDER BY id", (app_id,)).fetchall()
                        directors = [r["full_name"] for r in dir_rows if r.get("full_name")]
                    if not ubos:
                        ubo_rows = db.execute("SELECT full_name FROM ubos WHERE application_id=? ORDER BY id", (app_id,)).fetchall()
                        ubos = [r["full_name"] for r in ubo_rows if r.get("full_name")]
                db.close()
            except Exception as e:
                logger.warning(f"Could not look up pre-screening data for app {app_id}: {e}")

        # Resolve file path if doc_id provided, with S3 fallback
        file_path = None
        if doc_id:
            db = get_db()
            doc_record = db.execute("SELECT file_path, s3_key, doc_name, application_id, person_id FROM documents WHERE id=?", (doc_id,)).fetchone()
            db.close()
            if doc_record:
                fp = doc_record.get("file_path", "")
                if fp and not os.path.isabs(fp):
                    file_path = os.path.join(UPLOAD_DIR, os.path.basename(fp))
                elif fp:
                    file_path = fp

                # S3 fallback: local file missing (e.g. after container redeploy)
                if (not file_path or not os.path.isfile(file_path)) and doc_record.get("s3_key") and HAS_S3:
                    try:
                        s3 = get_s3_client()
                        s3_ok, s3_data = s3.download_document(doc_record["s3_key"])
                        if s3_ok and isinstance(s3_data, bytes):
                            ext = os.path.splitext(doc_record.get("doc_name", "") or "")[1] or ".bin"
                            cache_name = f"s3_cache_{doc_id}{ext}"
                            cache_path = os.path.join(UPLOAD_DIR, cache_name)
                            with open(cache_path, "wb") as _cf:
                                _cf.write(s3_data)
                            file_path = cache_path
                            logger.info(f"[ai-verify] doc={doc_id} retrieved from S3 ({len(s3_data)} bytes)")
                    except Exception as s3_err:
                        logger.error(f"[ai-verify] doc={doc_id} S3 fallback error: {s3_err}")

                # Auto-resolve application_id from document record if not provided
                if not app_id and doc_record.get("application_id"):
                    app_id = doc_record["application_id"]

                # Auto-resolve person_name from document record if not provided
                if not person_name and doc_record.get("person_id"):
                    try:
                        db2 = get_db()
                        person_row = db2.execute(
                            "SELECT full_name FROM directors WHERE id=? UNION SELECT full_name FROM ubos WHERE id=?",
                            (doc_record["person_id"], doc_record["person_id"])
                        ).fetchone()
                        db2.close()
                        if person_row:
                            person_name = person_row.get("full_name", "")
                    except Exception:
                        pass

        # Load check_overrides and prescreening_context from DB so that the helper path
        # uses the same canonical check IDs as the authoritative DocumentVerifyHandler.
        check_overrides = None
        prescreening_context = {}
        if app_id:
            try:
                db3 = get_db()
                check_category = "entity" if doc_category in ("company", "entity") else "person"
                ai_check_row = db3.execute(
                    "SELECT checks FROM ai_checks WHERE doc_type=? AND category=?",
                    (doc_type, check_category)
                ).fetchone()
                if ai_check_row and ai_check_row.get("checks"):
                    loaded = safe_json_loads(ai_check_row["checks"])
                    if loaded:
                        check_overrides = loaded

                # Build minimal prescreening context from declared values
                ps_row = db3.execute(
                    "SELECT prescreening_data, company_name FROM applications WHERE id=?",
                    (app_id,)
                ).fetchone()
                if ps_row:
                    stored_ps = parse_json_field(ps_row.get("prescreening_data"), {})
                    if isinstance(stored_ps, dict):
                        prescreening_context = {
                            k: v for k, v in stored_ps.items()
                            if v not in (None, "", [], {})
                        }
                db3.close()
            except Exception as _e:
                logger.warning(f"[ai-verify] Could not load check_overrides/prescreening for app {app_id}: {_e}")

        # Initialize Claude client
        if not HAS_CLAUDE_CLIENT:
            logger.warning("Claude client not available — returning flagged response for manual review")
            return self.success({
                "checks": [
                    {"label": "Document Type Match", "type": "doc_type_match", "result": "warn", "message": "AI unavailable — manual review required"},
                    {"label": "Document Validity", "type": "validity", "result": "warn", "message": "AI unavailable — manual review required"},
                ],
                "overall": "flagged",
                "confidence": 0.0,
                "ai_source": "unavailable",
                # /api/documents/ai-verify is helper-only; persisted /api/documents/:id/verify stays authoritative.
                "authoritative": False,
            })

        try:
            claude_client = ClaudeClient(
                api_key=_CFG_ANTHROPIC_API_KEY,
                monthly_budget_usd=_CFG_CLAUDE_BUDGET_USD,
                mock_mode=_CFG_CLAUDE_MOCK_MODE
            )

            result = claude_client.verify_document(
                doc_type=doc_type,
                file_name=file_name,
                person_name=person_name,
                doc_category=doc_category,
                file_path=file_path,
                entity_name=entity_name,
                directors=directors,
                ubos=ubos,
                check_overrides=check_overrides or None,
                prescreening_context=prescreening_context or None,
            )

            # P0-2: Guard against rejected/invalid AI responses
            if result.get("_rejected") or result.get("_validated") is False:
                logger.warning(f"AI verify rejected for {doc_type}/{file_name}: {result.get('error', 'schema validation failed')}")
                result["checks"] = [{"label": "AI Verification", "type": "validity", "result": "fail",
                                     "message": "AI output failed validation — manual review required"}]
                result["overall"] = "flagged"

            # P0-5: No pass without evidence — empty checks cannot be "verified"
            if not result.get("checks"):
                result["checks"] = [{"label": "AI Verification", "type": "validity", "result": "warn",
                                     "message": "No verification checks returned — manual review required"}]
                result["overall"] = "flagged"

            # /api/documents/ai-verify is helper-only; persisted /api/documents/:id/verify stays authoritative.
            result["authoritative"] = False
            self.success(result)
        except Exception as e:
            logger.error(f"Document AI verification failed: {e}")
            self.error("AI verification temporarily unavailable — please retry or proceed with manual review", 500)


# ══════════════════════════════════════════════════════════
# DOCUMENT DOWNLOAD ENDPOINT
# ══════════════════════════════════════════════════════════

# MIME types that support inline preview in browsers
INLINE_PREVIEWABLE_TYPES = {
    "application/pdf", "image/jpeg", "image/png", "image/gif", "image/webp",
}

class DocumentDownloadHandler(BaseHandler):
    """GET /api/documents/:id/download — get presigned S3 URL or serve local file.
    Query params:
      ?view=inline — for previewable types (PDF, images), serve with Content-Disposition: inline
                     so the browser opens the file in a new tab instead of downloading.
    """
    def get(self, doc_id):
        user = self.require_auth()
        if not user:
            return

        inline_view = self.get_argument("view", "") == "inline"

        db = get_db()
        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            db.close()
            return self.error("Document not found", 404)

        # Check access — officer can view any, client can only view their own
        app = db.execute("SELECT id, client_id, ref FROM applications WHERE id=?", (doc["application_id"],)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)
        if user.get("type") == "client" and app["client_id"] != user["sub"]:
            db.close()
            return self.error("Access denied", 403)
        db.close()

        mime_type = doc.get("mime_type") or "application/octet-stream"
        is_previewable = mime_type in INLINE_PREVIEWABLE_TYPES
        disposition = "inline" if (inline_view and is_previewable) else "attachment"

        s3_key = doc.get("s3_key") if doc else None

        # Prefer S3 presigned URL if document is stored in S3
        if s3_key and HAS_S3:
            try:
                s3 = get_s3_client()
                success, url_or_error = s3.get_presigned_url_with_ownership(
                    key=s3_key,
                    requesting_user_id=user.get("sub", ""),
                    requesting_user_role=user.get("role") or user.get("type", ""),
                    db_connection=db,
                    expiry=900,
                    response_filename=doc["doc_name"]
                )
                if success:
                    db.close()
                    action = "View" if inline_view else "Download"
                    self.log_audit(user, action, app["ref"], f"Document {action.lower()}ed via S3: {doc['doc_name']}")
                    return self.success({
                        "download_url": url_or_error,
                        "source": "s3",
                        "expires_in": 900,
                        "disposition": disposition,
                        "previewable": is_previewable,
                    })
                else:
                    logger.warning(f"S3 presigned URL failed for {doc_id}: {url_or_error}. Falling back to local.")
            except Exception as e:
                logger.warning(f"S3 download failed for {doc_id}: {e}. Falling back to local.")

        # Fall back to local file
        db.close()
        file_path = doc["file_path"]
        if file_path and not os.path.isabs(file_path):
            file_path = os.path.join(UPLOAD_DIR, os.path.basename(file_path))

        if not file_path or not os.path.exists(file_path):
            return self.error("Document file not found on server", 404)

        self.set_header("Content-Type", mime_type)
        self.set_header("Content-Disposition", f'{disposition}; filename="{doc["doc_name"]}"')
        with open(file_path, "rb") as f:
            self.write(f.read())
        action = "View" if inline_view else "Download"
        self.log_audit(user, action, app["ref"], f"Document {action.lower()}ed locally: {doc['doc_name']}")
        self.finish()


# ══════════════════════════════════════════════════════════
# COMPLIANCE RESOURCES ENDPOINTS
# ══════════════════════════════════════════════════════════

class ComplianceResourcesHandler(BaseHandler):
    """GET/POST /api/resources — list and upload compliance reference resources"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return

        db = get_db()
        rows = db.execute("""
            SELECT r.id, r.slug, r.title, r.description, r.category, r.resource_type, r.file_name,
                   r.mime_type, r.file_size, r.created_at, r.updated_at, r.uploaded_by,
                   u.full_name AS uploaded_by_name
            FROM compliance_resources r
            LEFT JOIN users u ON r.uploaded_by = u.id
            ORDER BY
                CASE WHEN r.resource_type = 'system' THEN 0 ELSE 1 END,
                r.created_at DESC,
                r.title ASC
        """).fetchall()
        db.close()
        self.success({"resources": [dict(r) for r in rows]})

    def post(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        if not self.check_rate_limit("resource_upload", max_attempts=20, window_seconds=60):
            return

        files = self.request.files.get("files") or self.request.files.get("file") or []
        if not files:
            return self.error("No file provided", 400)

        uploaded = []
        db = get_db()
        try:
            for file_info in files:
                filename = os.path.basename(file_info["filename"] or "")
                body = file_info["body"]
                content_type = file_info.get("content_type", "application/octet-stream")

                if not filename:
                    continue
                if len(body) > 25 * 1024 * 1024:
                    return self.error(f"File exceeds 25MB limit: {filename}", 400)

                is_valid, upload_error = FileUploadValidator.validate(filename, content_type, body)
                if not is_valid:
                    return self.error(f"File rejected: {upload_error}", 400)

                resource_id = uuid.uuid4().hex[:16]
                ext = os.path.splitext(filename)[1]
                safe_name = f"{resource_id}{ext}"
                file_path = os.path.join(RESOURCE_UPLOAD_DIR, safe_name)

                with open(file_path, "wb") as f:
                    f.write(body)

                db.execute("""
                    INSERT INTO compliance_resources
                    (id, title, description, category, resource_type, file_name, file_path, mime_type, file_size, uploaded_by, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))
                """, (
                    resource_id,
                    filename,
                    "Uploaded via back-office resources library.",
                    "internal",
                    "uploaded",
                    filename,
                    file_path,
                    content_type,
                    len(body),
                    user.get("sub", ""),
                ))

                uploaded.append({
                    "id": resource_id,
                    "title": filename,
                    "file_name": filename,
                    "file_size": len(body),
                    "mime_type": content_type,
                })

            db.commit()
        finally:
            db.close()

        for resource in uploaded:
            self.log_audit(user, "Upload", "Resources", f"Compliance resource uploaded: {resource['file_name']}")

        self.success({"uploaded": uploaded, "count": len(uploaded)}, 201)


class ComplianceResourceDownloadHandler(BaseHandler):
    """GET /api/resources/:id/download — download a compliance resource"""
    def get(self, resource_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return

        db = get_db()
        resource = db.execute("SELECT * FROM compliance_resources WHERE id = ? OR slug = ?", (resource_id, resource_id)).fetchone()
        db.close()
        if not resource:
            return self.error("Resource not found", 404)

        file_path = resource["file_path"]
        if not file_path:
            return self.error("Resource file is not configured", 404)
        if not os.path.isabs(file_path):
            file_path = os.path.join(STATIC_DIR, file_path)
        if not os.path.exists(file_path):
            return self.error("Resource file not found on server", 404)

        self.set_header("Content-Type", resource.get("mime_type") or "application/octet-stream")
        self.set_header("Content-Disposition", f'attachment; filename="{resource["file_name"]}"')
        with open(file_path, "rb") as f:
            self.write(f.read())
        self.log_audit(user, "Download", "Resources", f"Compliance resource downloaded: {resource['file_name']}")
        self.finish()


# ══════════════════════════════════════════════════════════
# REGULATORY INTELLIGENCE ENDPOINTS
# ══════════════════════════════════════════════════════════

class RegulatoryIntelligenceHandler(BaseHandler):
    """GET/POST /api/regulatory-intelligence — persisted regulatory document workflow"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return

        db = get_db()
        rows = db.execute("""
            SELECT d.*, u.full_name AS uploaded_by_name
            FROM regulatory_documents d
            LEFT JOIN users u ON d.uploaded_by = u.id
            ORDER BY d.created_at DESC, d.title ASC
        """).fetchall()
        db.close()

        documents = []
        for row in rows:
            doc = dict(row)
            for key, default in (("analysis_summary", {}), ("audit_trail", [])):
                try:
                    raw_value = doc.get(key)
                    if raw_value is None:
                        doc[key] = default
                    elif isinstance(raw_value, (dict, list)):
                        doc[key] = raw_value
                    else:
                        doc[key] = safe_json_loads(raw_value)
                except (json.JSONDecodeError, TypeError):
                    doc[key] = default
            doc["workflow_state"] = build_regulatory_workflow_state(doc)
            documents.append(doc)

        self.success({"documents": documents})

    def post(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        if not self.check_rate_limit("regulatory_upload", max_attempts=10, window_seconds=60):
            return

        title = (self.get_body_argument("title", "") or "").strip()
        regulator = (self.get_body_argument("regulator", "") or "").strip()
        jurisdiction = (self.get_body_argument("jurisdiction", "") or "").strip()
        doc_type = (self.get_body_argument("doc_type", "") or "").strip()
        publication_date = (self.get_body_argument("publication_date", "") or "").strip()
        effective_date = (self.get_body_argument("effective_date", "") or "").strip()
        source_text = (self.get_body_argument("source_text", "") or "").strip()

        if not title or not regulator or not jurisdiction or not doc_type:
            return self.error("title, regulator, jurisdiction, and doc_type are required", 400)

        file_info = None
        files = self.request.files.get("file") or self.request.files.get("files") or []
        if files:
            file_info = files[0]

        if not file_info and not source_text:
            return self.error("Provide a file upload or pasted source_text for analysis", 400)

        file_name = None
        file_path = None
        file_size = None
        mime_type = None
        s3_key = None

        if file_info:
            file_name = os.path.basename(file_info.get("filename") or "")
            file_body = file_info.get("body") or b""
            mime_type = file_info.get("content_type", "application/octet-stream")
            if not file_name:
                return self.error("Uploaded file must include a filename", 400)
            if len(file_body) > 25 * 1024 * 1024:
                return self.error("File exceeds 25MB limit", 400)

            # Regulatory Intelligence accepts only PDF and DOCX (matches frontend).
            reg_allowed_ext = {".pdf", ".docx"}
            reg_allowed_mime = {
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
            ext_check = os.path.splitext(file_name)[1].lower()
            if ext_check not in reg_allowed_ext:
                return self.error(f"Regulatory Intelligence accepts PDF and DOCX files only (got {ext_check})", 400)
            if mime_type not in reg_allowed_mime:
                return self.error(f"Regulatory Intelligence accepts PDF and DOCX files only", 400)

            is_valid, upload_error = FileUploadValidator.validate(file_name, mime_type, file_body)
            if not is_valid:
                return self.error(f"File rejected: {upload_error}", 400)

            doc_id = uuid.uuid4().hex[:16]
            ext = os.path.splitext(file_name)[1]
            safe_name = f"{doc_id}{ext}"
            file_path = os.path.join(REGULATORY_UPLOAD_DIR, safe_name)
            with open(file_path, "wb") as f:
                f.write(file_body)
            file_size = len(file_body)

            if not source_text and mime_type.startswith("text/"):
                try:
                    source_text = file_body.decode("utf-8", errors="ignore").strip()
                except Exception:
                    source_text = ""

            if HAS_S3:
                try:
                    s3 = get_s3_client()
                    success, key_or_error = s3.upload_document(
                        file_data=file_body,
                        client_id="regulatory-intelligence",
                        doc_type="regulatory_intelligence",
                        filename=safe_name,
                        metadata={"content_type": mime_type, "original_name": file_name}
                    )
                    if success:
                        s3_key = key_or_error
                    else:
                        logger.warning("Regulatory intelligence S3 upload failed: %s", key_or_error)
                except Exception as e:
                    logger.warning("Regulatory intelligence S3 upload failed: %s", e)

            if ENVIRONMENT == "production" and not s3_key and file_info:
                try:
                    if file_path and os.path.exists(file_path):
                        os.remove(file_path)
                except OSError as cleanup_err:
                    logger.warning("Failed to clean up regulatory upload after storage error: %s", cleanup_err)
                return self.error("Regulatory document storage is temporarily unavailable. Please retry once durable storage is restored.", 503)
        else:
            doc_id = uuid.uuid4().hex[:16]

        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        audit_trail = [{"time": created_at, "action": f"Document uploaded by {user.get('name', 'Compliance Officer')}"}]

        if source_text:
            analysis_summary = build_regulatory_analysis({
                "title": title,
                "regulator": regulator,
                "jurisdiction": jurisdiction,
                "doc_type": doc_type,
                "effective_date": effective_date,
                "source_text": source_text,
            })
            status = "analysed"
            audit_trail.append({
                "time": analysis_summary["analysedAt"],
                "action": f"Backend analysis completed — {len(analysis_summary.get('suggestions', []))} suggestions generated (confidence: {analysis_summary.get('confidence', 0)}%)"
            })
            analysis_source = analysis_summary.get("analysisSource", "backend_rule_assisted")
        else:
            analysis_summary = {
                "summary": "Source document stored. Manual text extraction is required before structured regulatory analysis can be generated in this environment.",
                "keyObligations": [],
                "affectedAreas": {
                    "onboarding": False, "kyc": False, "sanctions": False, "riskScoring": False,
                    "edd": False, "monitoring": False, "reporting": False
                },
                "suggestions": [],
                "affectedClientTypes": [],
                "confidence": 0,
                "analysedAt": None,
                "analysisSource": "manual_review_required",
                "humanReviewRequired": True,
            }
            status = "review_required"
            analysis_source = "manual_review_required"
            audit_trail.append({
                "time": created_at,
                "action": "Stored without text analysis — manual source text entry required before structured review."
            })

        db = get_db()
        try:
            db.execute("""
                INSERT INTO regulatory_documents
                (id, title, regulator, jurisdiction, doc_type, publication_date, effective_date,
                 file_name, file_path, s3_key, mime_type, file_size, source_text, status,
                 analysis_source, analysis_summary, audit_trail, uploaded_by, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
            """, (
                doc_id,
                title,
                regulator,
                jurisdiction,
                doc_type,
                publication_date or None,
                effective_date or None,
                file_name,
                file_path,
                s3_key,
                mime_type,
                file_size,
                source_text or None,
                status,
                analysis_source,
                json.dumps(analysis_summary),
                json.dumps(audit_trail),
                user.get("sub", ""),
            ))
            db.commit()
        finally:
            db.close()

        self.log_audit(user, "Upload", "Regulatory Intelligence", f"Regulatory document uploaded: {title} ({status})")
        self.success({
            "id": doc_id,
            "title": title,
            "status": status,
            "workflow_state": build_regulatory_workflow_state({
                "status": status,
                "analysis_source": analysis_source,
                "source_text": source_text,
                "file_name": file_name,
            }),
            "analysis_source": analysis_source,
            "analysis_summary": analysis_summary,
            "audit_trail": audit_trail,
        }, 201)


class RegulatoryIntelligenceReviewHandler(BaseHandler):
    """POST /api/regulatory-intelligence/:id/review — persist suggestion review decisions"""
    def post(self, document_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()
        suggestion_id = (data.get("suggestion_id") or "").strip()
        decision = (data.get("decision") or "").strip().lower()
        note = (data.get("note") or "").strip()

        if not suggestion_id or decision not in ("approved", "rejected", "deferred"):
            return self.error("suggestion_id and valid decision are required", 400)

        db = get_db()
        row = db.execute("SELECT * FROM regulatory_documents WHERE id = ?", (document_id,)).fetchone()
        if not row:
            db.close()
            return self.error("Regulatory document not found", 404)

        try:
            raw_analysis = row.get("analysis_summary")
            if raw_analysis is None:
                analysis_summary = {}
            elif isinstance(raw_analysis, dict):
                analysis_summary = raw_analysis
            else:
                analysis_summary = safe_json_loads(raw_analysis)
        except (json.JSONDecodeError, TypeError):
            analysis_summary = {}
        try:
            raw_audit = row.get("audit_trail")
            if raw_audit is None:
                audit_trail = []
            elif isinstance(raw_audit, list):
                audit_trail = raw_audit
            else:
                audit_trail = safe_json_loads(raw_audit)
        except (json.JSONDecodeError, TypeError):
            audit_trail = []

        suggestions = analysis_summary.get("suggestions") or []
        suggestion = next((s for s in suggestions if s.get("id") == suggestion_id), None)
        if not suggestion:
            db.close()
            return self.error("Suggestion not found on this regulatory document", 404)

        reviewed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        suggestion["status"] = decision
        suggestion["reviewedBy"] = user.get("name", "Compliance Officer")
        suggestion["reviewedAt"] = reviewed_at
        suggestion["notes"] = note or suggestion.get("notes") or ""

        audit_trail.append({
            "time": reviewed_at,
            "action": f"Suggestion {suggestion_id} {decision} by {user.get('name', 'Compliance Officer')}" + (f' — "{note}"' if note else "")
        })

        db.execute(
            "UPDATE regulatory_documents SET analysis_summary = ?, audit_trail = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(analysis_summary), json.dumps(audit_trail), document_id)
        )
        db.commit()
        db.close()

        self.log_audit(user, "Review", "Regulatory Intelligence", f"Suggestion {suggestion_id} {decision} for document {document_id}")
        self.success({"status": "recorded", "document_id": document_id, "suggestion": suggestion})


class RegulatoryIntelligenceSourceTextHandler(BaseHandler):
    """POST /api/regulatory-intelligence/:id/source-text — attach manual source text and run structured review"""
    def post(self, document_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()
        source_text = (data.get("source_text") or "").strip()
        note = (data.get("note") or "").strip()
        if not source_text:
            return self.error("source_text is required", 400)

        db = get_db()
        row = db.execute("SELECT * FROM regulatory_documents WHERE id = ?", (document_id,)).fetchone()
        if not row:
            db.close()
            return self.error("Regulatory document not found", 404)

        row_dict = dict(row)
        try:
            raw_audit = row_dict.get("audit_trail")
            if raw_audit is None:
                audit_trail = []
            elif isinstance(raw_audit, list):
                audit_trail = raw_audit
            else:
                audit_trail = safe_json_loads(raw_audit)
        except (json.JSONDecodeError, TypeError):
            audit_trail = []

        reviewed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        analysis_summary = build_regulatory_analysis({
            "title": row_dict.get("title"),
            "regulator": row_dict.get("regulator"),
            "jurisdiction": row_dict.get("jurisdiction"),
            "doc_type": row_dict.get("doc_type"),
            "effective_date": row_dict.get("effective_date"),
            "source_text": source_text,
        })
        analysis_source = analysis_summary.get("analysisSource", "backend_rule_assisted")
        prior_source_text = bool(row_dict.get("source_text"))

        audit_trail.append({
            "time": reviewed_at,
            "action": (
                f"Manual source text {'updated' if prior_source_text else 'added'} by {user.get('name', 'Compliance Officer')}" +
                (f' — "{note}"' if note else "")
            )
        })
        audit_trail.append({
            "time": analysis_summary["analysedAt"],
            "action": f"Structured review re-run using manual source text — {len(analysis_summary.get('suggestions', []))} suggestions generated (confidence: {analysis_summary.get('confidence', 0)}%)"
        })

        db.execute("""
            UPDATE regulatory_documents
            SET source_text = ?, status = ?, analysis_source = ?, analysis_summary = ?, audit_trail = ?, updated_at = datetime('now')
            WHERE id = ?
        """, (
            source_text,
            "analysed",
            analysis_source,
            json.dumps(analysis_summary),
            json.dumps(audit_trail),
            document_id,
        ))
        db.commit()
        db.close()

        self.log_audit(
            user,
            "Update",
            "Regulatory Intelligence",
            f"Manual source text {'updated' if prior_source_text else 'added'} and structured review re-run for document {document_id}"
        )
        self.success({
            "id": document_id,
            "status": "analysed",
            "workflow_state": build_regulatory_workflow_state({
                "status": "analysed",
                "analysis_source": analysis_source,
                "source_text": source_text,
                "file_name": row_dict.get("file_name"),
            }),
            "analysis_source": analysis_source,
            "analysis_summary": analysis_summary,
            "audit_trail": audit_trail,
            "source_text": source_text,
        })


class RegulatoryIntelligenceDownloadHandler(BaseHandler):
    """GET /api/regulatory-intelligence/:id/download — download source document when a file exists"""
    def get(self, document_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return

        db = get_db()
        row = db.execute("SELECT * FROM regulatory_documents WHERE id = ?", (document_id,)).fetchone()
        db.close()
        if not row:
            return self.error("Regulatory document not found", 404)

        s3_key = row.get("s3_key")
        if s3_key and HAS_S3:
            try:
                s3 = get_s3_client()
                success, url_or_error = s3.get_presigned_url(
                    key=s3_key,
                    expiry=900,
                    response_filename=row.get("file_name") or f"{document_id}.bin"
                )
                if success:
                    self.log_audit(user, "Download", "Regulatory Intelligence", f"Downloaded regulatory document via S3: {row.get('title')}")
                    return self.success({"download_url": url_or_error, "source": "s3", "expires_in": 900})
            except Exception as e:
                logger.warning("Regulatory intelligence download via S3 failed: %s", e)

        file_path = row.get("file_path")
        if not file_path:
            return self.error("No source file is stored for this regulatory document", 404)
        if not os.path.isabs(file_path):
            file_path = os.path.join(REGULATORY_UPLOAD_DIR, os.path.basename(file_path))
        if not os.path.exists(file_path):
            return self.error("Regulatory document file not found on server", 404)

        self.set_header("Content-Type", row.get("mime_type") or "application/octet-stream")
        self.set_header("Content-Disposition", f'attachment; filename="{row.get("file_name") or (document_id + ".bin")}"')
        with open(file_path, "rb") as f:
            self.write(f.read())
        self.log_audit(user, "Download", "Regulatory Intelligence", f"Downloaded regulatory document locally: {row.get('title')}")
        self.finish()


# ══════════════════════════════════════════════════════════
# USER MANAGEMENT ENDPOINTS
# ══════════════════════════════════════════════════════════

class UsersHandler(BaseHandler):
    """GET /api/users — list, POST — create"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco"])
        if not user:
            return

        db = get_db()
        rows = db.execute("SELECT id, email, full_name, role, status, created_at FROM users ORDER BY created_at").fetchall()
        db.close()
        self.success({"users": [dict(r) for r in rows]})

    VALID_ROLES = ("admin", "sco", "co", "analyst")

    def post(self):
        user = self.require_auth(roles=["admin"])
        if not user:
            return

        data = self.get_json()
        email = data.get("email", "").strip().lower()
        name = data.get("full_name", "").strip()
        role = data.get("role", "analyst")
        password = data.get("password", "")

        if not email or not name:
            return self.error("Email and full name required")

        # Validate email format
        if "@" not in email or "." not in email.split("@")[-1]:
            return self.error("Invalid email format", 400)

        # Validate role
        if role not in self.VALID_ROLES:
            return self.error(f"Invalid role. Must be one of: {', '.join(self.VALID_ROLES)}", 400)

        if not password:
            password = PasswordPolicy.generate_temporary()
            must_change_password = True
        else:
            must_change_password = False
            is_valid, pw_error = PasswordPolicy.validate(password)
            if not is_valid:
                return self.error(f"Password policy violation: {pw_error}", 400)

        db = get_db()
        exists = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if exists:
            db.close()
            return self.error("Email already exists", 400)

        user_id = uuid.uuid4().hex[:16]
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        try:
            db.execute("INSERT INTO users (id, email, password_hash, full_name, role) VALUES (?,?,?,?,?)",
                        (user_id, email, pw_hash, name, role))
            db.commit()
        except Exception:
            db.close()
            return self.error("Failed to create user", 500)
        db.close()

        self.log_audit(user, "Create User", name, f"New user added as {role}")
        self.success({"id": user_id, "email": email, "name": name, "role": role}, 201)


class UserDetailHandler(BaseHandler):
    """PUT /api/users/:id — update user"""

    VALID_ROLES = ("admin", "sco", "co", "analyst")
    VALID_STATUSES = ("active", "inactive")

    def put(self, user_id):
        user = self.require_auth(roles=["admin"])
        if not user:
            return

        # Prevent self-modification (avoid admin lockout)
        if user_id == user.get("sub"):
            return self.error("Cannot modify your own account", 403)

        data = self.get_json()

        # Reject unsupported password field — password changes are not
        # implemented via this endpoint.  Return 400 rather than silently
        # ignoring the field.
        if "password" in data:
            return self.error(
                "Password changes are not supported via this endpoint. "
                "Use the dedicated password-change flow.", 400)

        # Validate role
        new_role = data.get("role")
        if new_role and new_role not in self.VALID_ROLES:
            return self.error(f"Invalid role. Must be one of: {', '.join(self.VALID_ROLES)}", 400)

        # Validate status
        new_status = data.get("status")
        if new_status and new_status not in self.VALID_STATUSES:
            return self.error(f"Invalid status. Must be one of: {', '.join(self.VALID_STATUSES)}", 400)

        db = get_db()
        u = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not u:
            db.close()
            return self.error("User not found", 404)

        updated_name = data.get("full_name", u["full_name"])
        updated_role = new_role or u["role"]
        updated_status = new_status or u["status"]

        try:
            db.execute("UPDATE users SET full_name=?, role=?, status=?, updated_at=datetime('now') WHERE id=?",
                       (updated_name, updated_role, updated_status, user_id))
            db.commit()
        except Exception:
            db.close()
            return self.error("Failed to update user", 500)
        db.close()

        self.log_audit(user, "Update User", u["full_name"],
                       f"Updated: role={u['role']}→{updated_role}, status={u['status']}→{updated_status}, name={u['full_name']}→{updated_name}")
        self.success({"status": "updated"})


# ══════════════════════════════════════════════════════════
# RISK CONFIG ENDPOINTS
# ══════════════════════════════════════════════════════════

class RiskConfigHandler(BaseHandler):
    """GET/PUT /api/config/risk-model"""
    def get(self):
        user = self.require_auth()
        if not user:
            return
        db = get_db()
        config = db.execute("SELECT * FROM risk_config WHERE id=1").fetchone()
        db.close()
        if config:
            result = {
                "dimensions": safe_json_loads(config["dimensions"]),
                "thresholds": safe_json_loads(config["thresholds"]),
                "updated_at": config["updated_at"],
            }
            # Include scoring config columns (may not exist in older schemas)
            for col in ("country_risk_scores", "sector_risk_scores", "entity_type_scores"):
                try:
                    val = config[col]
                    result[col] = safe_json_loads(val) if val else {}
                except (KeyError, IndexError):
                    result[col] = {}
            self.success(result)
        else:
            self.success({"dimensions": [], "thresholds": [],
                         "country_risk_scores": {}, "sector_risk_scores": {}, "entity_type_scores": {}})

    def put(self):
        user = self.require_auth(roles=["admin"])
        if not user:
            return
        data = self.get_json()

        # Schema-validate before saving — reject malformed config
        config_to_validate = {
            "dimensions": data.get("dimensions", []),
            "thresholds": data.get("thresholds", []),
            "country_risk_scores": data.get("country_risk_scores", {}),
            "sector_risk_scores": data.get("sector_risk_scores", {}),
            "entity_type_scores": data.get("entity_type_scores", {}),
        }
        validated, errors = validate_risk_config(config_to_validate)
        if errors:
            # Sanitize error messages — they may contain user-supplied type names
            safe_errors = [str(e)[:200] for e in errors]
            self.set_status(400)
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps({
                "status": "error",
                "message": "Risk config validation failed",
                "errors": safe_errors,
            }))
            return

        db = get_db()

        # EX-05: Capture full before-state of risk config for audit trail
        _risk_before = None
        try:
            old_cfg = db.execute("SELECT dimensions, thresholds, country_risk_scores, sector_risk_scores, entity_type_scores FROM risk_config WHERE id=1").fetchone()
            if old_cfg:
                _risk_before = {
                    "dimensions": safe_json_loads(old_cfg["dimensions"]),
                    "thresholds": safe_json_loads(old_cfg["thresholds"]),
                    "country_risk_scores": safe_json_loads(old_cfg["country_risk_scores"]) if old_cfg["country_risk_scores"] else {},
                    "sector_risk_scores": safe_json_loads(old_cfg["sector_risk_scores"]) if old_cfg["sector_risk_scores"] else {},
                    "entity_type_scores": safe_json_loads(old_cfg["entity_type_scores"]) if old_cfg["entity_type_scores"] else {},
                }
        except Exception:
            pass  # Non-critical: proceed without before-state

        db.execute(
            "UPDATE risk_config SET dimensions=?, thresholds=?, country_risk_scores=?, sector_risk_scores=?, entity_type_scores=?, updated_by=?, updated_at=datetime('now') WHERE id=1",
            (json.dumps(validated.get("dimensions", [])),
             json.dumps(validated.get("thresholds", [])),
             json.dumps(validated.get("country_risk_scores", {})),
             json.dumps(validated.get("sector_risk_scores", {})),
             json.dumps(validated.get("entity_type_scores", {})),
             user["sub"]))
        db.commit()

        # EX-09: Recompute risk for all active applications after config change
        recomp_results = recompute_risk_for_active_apps(
            db, "risk_config_updated", user=user, log_audit_fn=self.log_audit)
        if recomp_results:
            db.commit()

        db.close()

        _risk_after = {
            "dimensions": validated.get("dimensions", []),
            "thresholds": validated.get("thresholds", []),
            "country_risk_scores": validated.get("country_risk_scores", {}),
            "sector_risk_scores": validated.get("sector_risk_scores", {}),
            "entity_type_scores": validated.get("entity_type_scores", {}),
        }
        self.log_audit(user, "Config", "Risk Model", "Risk scoring model updated",
                       before_state=_risk_before, after_state=_risk_after)

        changed_count = sum(1 for r in recomp_results if r.get("changed"))
        self.success({
            "status": "saved",
            "risk_recomputed_apps": len(recomp_results),
            "risk_changed_apps": changed_count,
        })


class EnvironmentInfoHandler(BaseHandler):
    """GET /api/config/environment — return environment info for frontend"""
    def get(self):
        self.success(get_environment_info())


class VersionHandler(BaseHandler):
    """GET /api/version — build identification for authenticated sessions.

    Auth-gated: returns 401 for unauthenticated requests.
    No DB calls, no secrets, no PII.  Values from env vars only.
    """

    def get(self):
        user = self.require_auth()
        if not user:
            return
        self.success({
            "git_sha": os.environ.get("GIT_SHA", "unknown"),
            "git_sha_short": os.environ.get("GIT_SHA", "unknown")[:7]
            if os.environ.get("GIT_SHA") else "unknown",
            "build_time": os.environ.get("BUILD_TIME", "unknown"),
            "environment": ENVIRONMENT,
            "service": "regmind-backend",
        })


class SystemSettingsHandler(BaseHandler):
    """GET/PUT /api/config/system-settings"""
    def get(self):
        user = self.require_auth()
        if not user:
            return
        db = get_db()
        row = db.execute("""
            SELECT s.*, u.full_name as updated_by_name
            FROM system_settings s
            LEFT JOIN users u ON s.updated_by = u.id
            WHERE s.id = 1
        """).fetchone()
        db.close()
        if not row:
            return self.success({
                "company_name": "Onboarda Ltd",
                "licence_number": "FSC-PIS-2024-001",
                "default_retention_years": 7,
                "auto_approve_max_score": 40,
                "edd_threshold_score": 55,
            })
        self.success(dict(row))

    def put(self):
        user = self.require_auth(roles=["admin"])
        if not user:
            return
        data = self.get_json()
        db = get_db()
        current = db.execute("SELECT * FROM system_settings WHERE id=1").fetchone()
        if current:
            db.execute("""
                UPDATE system_settings SET
                    company_name=?,
                    licence_number=?,
                    default_retention_years=?,
                    auto_approve_max_score=?,
                    edd_threshold_score=?,
                    updated_by=?,
                    updated_at=datetime('now')
                WHERE id=1
            """, (
                data.get("company_name", current["company_name"]),
                data.get("licence_number", current["licence_number"]),
                int(data.get("default_retention_years", current["default_retention_years"])),
                int(data.get("auto_approve_max_score", current["auto_approve_max_score"])),
                int(data.get("edd_threshold_score", current["edd_threshold_score"])),
                user["sub"],
            ))
        else:
            db.execute("""
                INSERT INTO system_settings
                (id, company_name, licence_number, default_retention_years, auto_approve_max_score, edd_threshold_score, updated_by, updated_at)
                VALUES (1,?,?,?,?,?,?,datetime('now'))
            """, (
                data.get("company_name", "Onboarda Ltd"),
                data.get("licence_number", "FSC-PIS-2024-001"),
                int(data.get("default_retention_years", 7)),
                int(data.get("auto_approve_max_score", 40)),
                int(data.get("edd_threshold_score", 55)),
                user["sub"],
            ))
        db.commit()
        db.close()
        self.log_audit(user, "Config", "System Settings", "System settings updated")
        self.success({"status": "saved"})


ROLE_PERMISSION_MATRIX = [
    {"id": "view_dashboard", "label": "View dashboard", "roles": ["admin", "sco", "co", "analyst"]},
    {"id": "view_all_applications", "label": "View all applications", "roles": ["admin", "sco", "co", "analyst"]},
    {"id": "view_application_details", "label": "View application details", "roles": ["admin", "sco", "co", "analyst"]},
    {"id": "approve_low_medium", "label": "Approve applications (Low/Medium)", "roles": ["admin", "sco", "co"]},
    {"id": "approve_high_very_high", "label": "Approve applications (High/Very High)", "roles": ["admin", "sco"]},
    {"id": "reject_applications", "label": "Reject applications", "roles": ["admin", "sco", "co"]},
    {"id": "request_more_information", "label": "Request more information", "roles": ["admin", "sco", "co", "analyst"]},
    {"id": "assign_reassign_cases", "label": "Assign / reassign cases", "roles": ["admin", "sco"]},
    {"id": "escalate_to_sco", "label": "Escalate to Senior CO", "roles": ["admin", "sco", "co", "analyst"]},
    {"id": "view_compliance_memo", "label": "View compliance memo", "roles": ["admin", "sco", "co", "analyst"]},
    {"id": "override_ai_risk_score", "label": "Override AI risk score", "roles": ["admin", "sco"]},
    {"id": "edd_review_signoff", "label": "EDD review & sign-off", "roles": ["admin", "sco"]},
    {"id": "view_screening_results", "label": "View screening results", "roles": ["admin", "sco", "co", "analyst"]},
    {"id": "view_reports_analytics", "label": "View reports & analytics", "roles": ["admin", "sco", "co"]},
    {"id": "manage_users", "label": "Manage users", "roles": ["admin"]},
    {"id": "manage_roles_permissions", "label": "Manage roles & permissions", "roles": ["admin"]},
    {"id": "view_audit_trail", "label": "View audit trail", "roles": ["admin", "sco"]},
    {"id": "system_settings", "label": "System settings", "roles": ["admin"]},
]


class RolesPermissionsHandler(BaseHandler):
    """GET /api/config/roles-permissions — backend-owned RBAC matrix for UI/reference"""
    def get(self):
        user = self.require_auth()
        if not user:
            return
        self.success({
            "roles": [
                {"id": "admin", "label": "Administrator"},
                {"id": "sco", "label": "Senior CO"},
                {"id": "co", "label": "Compliance Officer"},
                {"id": "analyst", "label": "Analyst"},
            ],
            "permissions": ROLE_PERMISSION_MATRIX,
            "source": "backend_policy",
        })


# ══════════════════════════════════════════════════════════
# AI AGENTS CONFIG ENDPOINTS
# ══════════════════════════════════════════════════════════

class AIAgentsHandler(BaseHandler):
    """GET/POST /api/config/ai-agents"""
    def get(self):
        user = self.require_auth()
        if not user:
            return
        db = get_db()
        rows = db.execute("SELECT * FROM ai_agents ORDER BY agent_number").fetchall()
        db.close()
        agents = []
        for r in rows:
            a = dict(r)
            a["checks"] = safe_json_loads(a["checks"]) if a["checks"] else []
            a["enabled"] = bool(a["enabled"])
            agents.append(a)
        self.success({"agents": agents})

    def post(self):
        user = self.require_auth(roles=["admin"])
        if not user:
            return
        data = self.get_json()
        db = get_db()
        db.execute("""INSERT INTO ai_agents (agent_number, name, icon, stage, description, enabled, checks)
                      VALUES (?,?,?,?,?,?,?)""",
                   (data.get("agent_number",0), data.get("name",""), data.get("icon","🤖"),
                    data.get("stage",""), data.get("description",""),
                    1 if data.get("enabled", True) else 0, json.dumps(data.get("checks",[]))))
        db.commit()
        db.close()
        self.log_audit(user, "Config", "AI Agents", f"Agent added: {data.get('name','')}")
        self.success({"status": "created"}, 201)


class AIAgentDetailHandler(BaseHandler):
    """PUT/DELETE /api/config/ai-agents/:id"""
    def put(self, agent_id):
        user = self.require_auth(roles=["admin"])
        if not user:
            return
        data = self.get_json()
        db = get_db()

        # P2-1: Read old state for audit diff
        old_agent = db.execute("SELECT * FROM ai_agents WHERE id=?", (agent_id,)).fetchone()
        if not old_agent:
            db.close()
            return self.error("Agent not found", 404)

        # P2-3: Conflict detection — reject stale updates
        if data.get("expected_updated_at"):
            if old_agent["updated_at"] and old_agent["updated_at"] != data["expected_updated_at"]:
                db.close()
                return self.error("Configuration was modified by another user. Please refresh and try again.", 409)

        db.execute("""UPDATE ai_agents SET name=?, icon=?, stage=?, description=?,
                      enabled=?, checks=?, updated_at=datetime('now') WHERE id=?""",
                   (data.get("name",""), data.get("icon",""), data.get("stage",""),
                    data.get("description",""), 1 if data.get("enabled",True) else 0,
                    json.dumps(data.get("checks",[])), agent_id))
        db.commit()

        # P2-1: Build audit detail with old/new values
        changes = []
        if "enabled" in data and (1 if data["enabled"] else 0) != old_agent["enabled"]:
            changes.append(f"enabled: {bool(old_agent['enabled'])} -> {data['enabled']}")
        if "name" in data and data["name"] != old_agent["name"]:
            changes.append(f"name: '{old_agent['name']}' -> '{data['name']}'")
        if "stage" in data and data["stage"] != old_agent["stage"]:
            changes.append(f"stage: '{old_agent['stage']}' -> '{data['stage']}'")
        detail = f"Agent {agent_id} updated: {data.get('name', old_agent['name'])}. Changes: "
        detail += ", ".join(changes) if changes else "no field changes"

        # Return updated_at for conflict detection
        updated_row = db.execute("SELECT updated_at FROM ai_agents WHERE id=?", (agent_id,)).fetchone()
        db.close()
        self.log_audit(user, "Config Update", "AI Agents", detail)
        self.success({"status": "updated", "updated_at": updated_row["updated_at"] if updated_row else None})

    def delete(self, agent_id):
        user = self.require_auth(roles=["admin"])
        if not user:
            return
        db = get_db()
        db.execute("DELETE FROM ai_agents WHERE id=?", (agent_id,))
        db.commit()
        db.close()
        self.log_audit(user, "Config", "AI Agents", f"Agent {agent_id} deleted")
        self.success({"status": "deleted"})


# ══════════════════════════════════════════════════════════
# AI VERIFICATION CHECKS CONFIG ENDPOINTS
# ══════════════════════════════════════════════════════════

class VerificationChecksHandler(BaseHandler):
    """GET/PUT /api/config/verification-checks"""
    def get(self):
        user = self.require_auth()
        if not user:
            return
        db = get_db()
        rows = db.execute("SELECT * FROM ai_checks ORDER BY category, id").fetchall()
        db.close()
        entity = []
        person = []
        for r in rows:
            item = {
                "id": r["id"],
                "doc_type": r["doc_type"],
                "doc_name": r["doc_name"],
                "checks": safe_json_loads(r["checks"]) if r["checks"] else [],
                "updated_at": r["updated_at"],
            }
            if r["category"] == "entity":
                entity.append(item)
            else:
                person.append(item)
        self.success({"entity": entity, "person": person})

    def put(self):
        user = self.require_auth(roles=["admin"])
        if not user:
            return
        data = self.get_json()
        doc_type = data.get("doc_type")
        category = data.get("category")
        checks = data.get("checks", [])
        if not doc_type or not category:
            return self.error("doc_type and category are required", 400)

        db = get_db()

        # P2-1: Read old state for audit diff
        old_row = db.execute("SELECT checks FROM ai_checks WHERE doc_type=? AND category=?", (doc_type, category)).fetchone()
        old_checks_count = len(safe_json_loads(old_row["checks"])) if old_row and old_row["checks"] else 0

        # Update existing row or insert if new
        existing = db.execute("SELECT id FROM ai_checks WHERE doc_type=? AND category=?", (doc_type, category)).fetchone()
        if existing:
            db.execute(
                "UPDATE ai_checks SET checks=?, updated_at=datetime('now') WHERE doc_type=? AND category=?",
                (json.dumps(checks), doc_type, category)
            )
        else:
            doc_name = data.get("doc_name", doc_type)
            db.execute(
                "INSERT INTO ai_checks (category, doc_type, doc_name, checks) VALUES (?,?,?,?)",
                (category, doc_type, doc_name, json.dumps(checks))
            )

        # Auto-update Agent 1's checks list from all ai_checks
        all_rows = db.execute("SELECT doc_name, checks FROM ai_checks ORDER BY category, id").fetchall()
        all_labels = []
        for row in all_rows:
            row_checks = safe_json_loads(row["checks"]) if row["checks"] else []
            for ch in row_checks:
                all_labels.append(f"{row['doc_name']}: {ch.get('label', '')}")
        db.execute(
            "UPDATE ai_agents SET checks=?, updated_at=datetime('now') WHERE agent_number=1",
            (json.dumps(all_labels),)
        )

        db.commit()
        db.close()
        # P2-1: Audit log with old/new check counts
        new_checks_count = len(checks)
        detail = f"Checks updated for {category}/{doc_type}: {old_checks_count} -> {new_checks_count} checks"
        self.log_audit(user, "Config Update", "AI Checks", detail)
        self.success({"status": "saved"})


# ══════════════════════════════════════════════════════════
# REPORT GENERATION ENDPOINTS
# ══════════════════════════════════════════════════════════

class ReportHandler(BaseHandler):
    """GET /api/reports/generate — generate filtered report data"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        db = get_db()

        # Get filter parameters
        status = self.get_argument("status", None)
        risk_level = self.get_argument("risk_level", None)
        date_from = self.get_argument("date_from", None)
        date_to = self.get_argument("date_to", None)
        jurisdiction = self.get_argument("jurisdiction", None)
        entity_type = self.get_argument("entity_type", None)
        assigned_to = self.get_argument("assigned_to", None)
        fields = self.get_argument("fields", "ref,company_name,status,risk_level,created_at,assigned_to")

        # Build query
        conditions = []
        params = []
        if status:
            conditions.append("a.status=?")
            params.append(status)
        if risk_level:
            conditions.append("a.risk_level=?")
            params.append(risk_level)
        if date_from:
            conditions.append("a.created_at >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("a.created_at <= ?")
            params.append(date_to)
        if jurisdiction:
            conditions.append("a.country = ?")
            params.append(jurisdiction)
        if entity_type:
            conditions.append("a.entity_type = ?")
            params.append(entity_type)
        if assigned_to:
            conditions.append("a.assigned_to = ?")
            params.append(assigned_to)

        where = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
            SELECT a.*,
                   (SELECT COUNT(*) FROM directors WHERE application_id=a.id) as director_count,
                   (SELECT COUNT(*) FROM ubos WHERE application_id=a.id) as ubo_count,
                   (SELECT COUNT(*) FROM documents WHERE application_id=a.id) as document_count
            FROM applications a
            WHERE {where}
            ORDER BY a.created_at DESC
        """

        rows = db.execute(query, params).fetchall()
        db.close()

        # Parse field selection
        field_list = [f.strip() for f in fields.split(",")]

        results = []
        for row in rows:
            record = dict(row)
            # Bug #4: Use actual DB columns for risk data (not prescreening_data which may lack these)
            record["risk_score"] = record.get("risk_score") or 0
            record["risk_level"] = record.get("risk_level") or ""
            record["risk_lane"] = record.get("onboarding_lane") or ""
            # Parse risk_dimensions from JSON string
            if record.get("risk_dimensions") and isinstance(record["risk_dimensions"], str):
                record["risk_dimensions"] = safe_json_loads(record["risk_dimensions"])

            # Filter to requested fields
            filtered = {}
            for f in field_list:
                if f in record:
                    filtered[f] = record[f]
                elif f == "director_count":
                    filtered[f] = record.get("director_count", 0)
                elif f == "ubo_count":
                    filtered[f] = record.get("ubo_count", 0)
                elif f == "document_count":
                    filtered[f] = record.get("document_count", 0)
            results.append(filtered)

        self.log_audit(user, "Report", "Generate", f"Report generated: {len(results)} records, fields: {fields}")
        self.success({
            "total": len(results),
            "fields": field_list,
            "data": results
        })

class ReportAnalyticsHandler(BaseHandler):
    """GET /api/reports/analytics — analytics data for the Reports page"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        from fixture_filter import (
            fixture_app_exclude_clause,
            should_show_fixtures,
        )

        db = get_db()

        try:
            # Collect filter parameters
            date_from = self.get_argument("date_from", None)
            date_to = self.get_argument("date_to", None)
            status = self.get_argument("status", None)
            risk_level = self.get_argument("risk_level", None)
            jurisdiction = self.get_argument("jurisdiction", None)
            entity_type = self.get_argument("entity_type", None)
            assigned_to = self.get_argument("assigned_to", None)

            # Build dynamic WHERE clause for applications
            conditions = []
            params = []

            # Fixture exclusion (default: excluded; admin/sco may opt-in)
            show_fx = should_show_fixtures(user, self.get_argument("show_fixtures", None))
            if not show_fx:
                fx_excl, fx_params = fixture_app_exclude_clause()
                conditions.append(fx_excl)
                params.extend(fx_params)

            if date_from:
                conditions.append("a.created_at >= ?")
                params.append(date_from)
            if date_to:
                conditions.append("a.created_at <= ?")
                params.append(date_to)
            if status:
                conditions.append("a.status = ?")
                params.append(status)
            if risk_level:
                conditions.append("a.risk_level = ?")
                params.append(risk_level)
            if jurisdiction:
                conditions.append("a.country = ?")
                params.append(jurisdiction)
            if entity_type:
                conditions.append("a.entity_type = ?")
                params.append(entity_type)
            if assigned_to:
                conditions.append("a.assigned_to = ?")
                params.append(assigned_to)


            where = " AND ".join(conditions) if conditions else "1=1"

            # --- summary ---
            summary = {
                "total": 0, "approved": 0, "rejected": 0, "pending": 0,
                "edd_required": 0, "withdrawn": 0,
                "avg_risk_score": 0.0, "approval_rate": 0.0, "rejection_rate": 0.0
            }
            try:
                row = db.execute(f"""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN a.status='approved' THEN 1 ELSE 0 END) as approved,
                        SUM(CASE WHEN a.status='rejected' THEN 1 ELSE 0 END) as rejected,
                        SUM(CASE WHEN a.status='pending' OR a.status='submitted' OR a.status='under_review' THEN 1 ELSE 0 END) as pending,
                        SUM(CASE WHEN a.status='edd_required' THEN 1 ELSE 0 END) as edd_required,
                        SUM(CASE WHEN a.status='withdrawn' THEN 1 ELSE 0 END) as withdrawn,
                        AVG(CASE WHEN a.risk_score IS NOT NULL THEN a.risk_score ELSE NULL END) as avg_risk_score
                    FROM applications a WHERE {where}
                """, params).fetchone()
                if row:
                    r = dict(row)
                    total = r.get("total") or 0
                    approved = r.get("approved") or 0
                    rejected = r.get("rejected") or 0
                    summary = {
                        "total": total,
                        "approved": approved,
                        "rejected": rejected,
                        "pending": r.get("pending") or 0,
                        "edd_required": r.get("edd_required") or 0,
                        "withdrawn": r.get("withdrawn") or 0,
                        "avg_risk_score": round(r.get("avg_risk_score") or 0.0, 2),
                        "approval_rate": round((approved / total * 100) if total > 0 else 0.0, 2),
                        "rejection_rate": round((rejected / total * 100) if total > 0 else 0.0, 2),
                    }
            except Exception:
                pass

            # --- risk_distribution ---
            risk_distribution = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "VERY_HIGH": 0}
            try:
                rows = db.execute(f"""
                    SELECT a.risk_level, COUNT(*) as cnt
                    FROM applications a WHERE {where}
                    GROUP BY a.risk_level
                """, params).fetchall()
                for row in rows:
                    r = dict(row)
                    level = r.get("risk_level") or ""
                    if level in risk_distribution:
                        risk_distribution[level] = r.get("cnt") or 0
            except Exception:
                pass

            # --- status_distribution ---
            status_distribution = {}
            try:
                rows = db.execute(f"""
                    SELECT a.status, COUNT(*) as cnt
                    FROM applications a WHERE {where}
                    GROUP BY a.status
                """, params).fetchall()
                for row in rows:
                    r = dict(row)
                    s = r.get("status") or "unknown"
                    status_distribution[s] = r.get("cnt") or 0
            except Exception:
                pass

            # --- monthly_trends ---
            monthly_trends = []
            try:
                rows = db.execute(f"""
                    SELECT strftime('%Y-%m', a.created_at) as month,
                           COUNT(*) as submitted,
                           SUM(CASE WHEN a.status='approved' THEN 1 ELSE 0 END) as approved,
                           SUM(CASE WHEN a.status='rejected' THEN 1 ELSE 0 END) as rejected
                    FROM applications a WHERE {where}
                    GROUP BY strftime('%Y-%m', a.created_at)
                    ORDER BY month ASC
                """, params).fetchall()
                for row in rows:
                    r = dict(row)
                    monthly_trends.append({
                        "month": r.get("month") or "",
                        "submitted": r.get("submitted") or 0,
                        "approved": r.get("approved") or 0,
                        "rejected": r.get("rejected") or 0,
                    })
            except Exception:
                pass

            # --- jurisdiction_breakdown ---
            jurisdiction_breakdown = []
            try:
                rows = db.execute(f"""
                    SELECT a.country, COUNT(*) as cnt
                    FROM applications a WHERE {where}
                    GROUP BY a.country
                    ORDER BY cnt DESC
                    LIMIT 20
                """, params).fetchall()
                for row in rows:
                    r = dict(row)
                    jurisdiction_breakdown.append({
                        "country": r.get("country") or "Unknown",
                        "count": r.get("cnt") or 0,
                    })
            except Exception:
                pass

            # --- entity_type_breakdown ---
            entity_type_breakdown = []
            try:
                rows = db.execute(f"""
                    SELECT a.entity_type, COUNT(*) as cnt
                    FROM applications a WHERE {where}
                    GROUP BY a.entity_type
                    ORDER BY cnt DESC
                    LIMIT 20
                """, params).fetchall()
                for row in rows:
                    r = dict(row)
                    entity_type_breakdown.append({
                        "entity_type": r.get("entity_type") or "Unknown",
                        "count": r.get("cnt") or 0,
                    })
            except Exception:
                pass

            # --- pipeline_stages ---
            pipeline_stages = {}
            try:
                rows = db.execute(f"""
                    SELECT a.status, COUNT(*) as cnt
                    FROM applications a WHERE {where}
                    GROUP BY a.status
                """, params).fetchall()
                for row in rows:
                    r = dict(row)
                    pipeline_stages[r.get("status") or "unknown"] = r.get("cnt") or 0
            except Exception:
                pass

            # --- edd_stats ---
            edd_stats = {"total": 0, "active": 0, "completed": 0, "by_stage": {}}
            try:
                # EDD stats respect all active filters via application join
                edd_conditions = []
                edd_params = []
                # Fixture exclusion (mirrors the main conditions block)
                if not show_fx:
                    fx_excl2, fx_params2 = fixture_app_exclude_clause()
                    edd_conditions.append(fx_excl2)
                    edd_params.extend(fx_params2)
                if date_from:
                    edd_conditions.append("a.created_at >= ?")
                    edd_params.append(date_from)
                if date_to:
                    edd_conditions.append("a.created_at <= ?")
                    edd_params.append(date_to)
                if status:
                    edd_conditions.append("a.status = ?")
                    edd_params.append(status)
                if risk_level:
                    edd_conditions.append("a.risk_level = ?")
                    edd_params.append(risk_level)
                if jurisdiction:
                    edd_conditions.append("a.country = ?")
                    edd_params.append(jurisdiction)
                if entity_type:
                    edd_conditions.append("a.entity_type = ?")
                    edd_params.append(entity_type)
                if assigned_to:
                    edd_conditions.append("a.assigned_to = ?")
                    edd_params.append(assigned_to)
                edd_where = " AND ".join(edd_conditions) if edd_conditions else "1=1"

                edd_query = f"""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN e.stage NOT IN ('edd_approved','edd_rejected') THEN 1 ELSE 0 END) as active,
                        SUM(CASE WHEN e.stage IN ('edd_approved','edd_rejected') THEN 1 ELSE 0 END) as completed
                    FROM edd_cases e
                    JOIN applications a ON a.id = e.application_id
                    WHERE {edd_where}
                """
                row = db.execute(edd_query, edd_params).fetchone()
                if row:
                    r = dict(row)
                    edd_stats["total"] = r.get("total") or 0
                    edd_stats["active"] = r.get("active") or 0
                    edd_stats["completed"] = r.get("completed") or 0

                # by_stage
                stage_rows = db.execute(f"""
                    SELECT e.stage, COUNT(*) as cnt
                    FROM edd_cases e
                    JOIN applications a ON a.id = e.application_id
                    WHERE {edd_where}
                    GROUP BY e.stage
                """, edd_params).fetchall()
                by_stage = {}
                for row in stage_rows:
                    r = dict(row)
                    by_stage[r.get("stage") or "unknown"] = r.get("cnt") or 0
                edd_stats["by_stage"] = by_stage
            except Exception:
                pass

            # --- screening_stats ---
            screening_stats = {"total_reviews": 0, "cleared": 0, "escalated": 0, "follow_up": 0}
            try:
                row = db.execute(f"""
                    SELECT
                        COUNT(*) as total_reviews,
                        SUM(CASE WHEN sr.disposition='cleared' THEN 1 ELSE 0 END) as cleared,
                        SUM(CASE WHEN sr.disposition='escalated' THEN 1 ELSE 0 END) as escalated,
                        SUM(CASE WHEN sr.disposition='follow_up_required' THEN 1 ELSE 0 END) as follow_up
                    FROM screening_reviews sr
                    JOIN applications a ON a.id = sr.application_id
                    WHERE {where}
                """, params).fetchone()
                if row:
                    r = dict(row)
                    screening_stats = {
                        "total_reviews": r.get("total_reviews") or 0,
                        "cleared": r.get("cleared") or 0,
                        "escalated": r.get("escalated") or 0,
                        "follow_up": r.get("follow_up") or 0,
                    }
            except Exception:
                pass

            # --- periodic_review_stats ---
            periodic_review_stats = {"total": 0, "pending": 0, "completed": 0, "overdue": 0}
            try:
                row = db.execute(f"""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN pr.status='pending' THEN 1 ELSE 0 END) as pending,
                        SUM(CASE WHEN pr.status='completed' THEN 1 ELSE 0 END) as completed,
                        SUM(CASE WHEN pr.status='pending' AND pr.due_date < date('now') THEN 1 ELSE 0 END) as overdue
                    FROM periodic_reviews pr
                    JOIN applications a ON a.id = pr.application_id
                    WHERE {where}
                """, params).fetchone()
                if row:
                    r = dict(row)
                    periodic_review_stats = {
                        "total": r.get("total") or 0,
                        "pending": r.get("pending") or 0,
                        "completed": r.get("completed") or 0,
                        "overdue": r.get("overdue") or 0,
                    }
            except Exception:
                pass

            # --- reviewer_workload ---
            reviewer_workload = []
            try:
                rows = db.execute(f"""
                    SELECT a.assigned_to,
                           COUNT(*) as cnt,
                           SUM(CASE WHEN a.status='approved' THEN 1 ELSE 0 END) as approved,
                           SUM(CASE WHEN a.status='rejected' THEN 1 ELSE 0 END) as rejected
                    FROM applications a
                    WHERE {where} AND a.assigned_to IS NOT NULL AND a.assigned_to != ''
                    GROUP BY a.assigned_to
                """, params).fetchall()
                for row in rows:
                    r = dict(row)
                    reviewer_workload.append({
                        "assigned_to": r.get("assigned_to") or "",
                        "count": r.get("cnt") or 0,
                        "approved": r.get("approved") or 0,
                        "rejected": r.get("rejected") or 0,
                    })
            except Exception:
                pass

            # --- recent_decisions ---
            recent_decisions = []
            try:
                rows = db.execute(f"""
                    SELECT d.application_ref, d.decision_type,
                           d.risk_level, d.timestamp, d.source,
                           a.company_name
                    FROM decision_records d
                    LEFT JOIN applications a ON a.ref = d.application_ref
                    WHERE {where}
                    ORDER BY d.timestamp DESC
                    LIMIT 20
                """, params).fetchall()
                for row in rows:
                    r = dict(row)
                    recent_decisions.append({
                        "ref": r.get("application_ref") or "",
                        "company_name": r.get("company_name") or "",
                        "decision_type": r.get("decision_type") or "",
                        "risk_level": r.get("risk_level") or "",
                        "timestamp": r.get("timestamp") or "",
                        "source": r.get("source") or "",
                    })
            except Exception:
                pass

            filter_desc = ", ".join(f"{k}={v}" for k, v in [
                ("date_from", date_from), ("date_to", date_to),
                ("status", status), ("risk_level", risk_level),
                ("jurisdiction", jurisdiction), ("entity_type", entity_type),
                ("assigned_to", assigned_to),
            ] if v)
            self.log_audit(user, "Report", "Analytics",
                           f"Analytics report generated with filters: {filter_desc or 'none'}")

            self.success({
                "summary": summary,
                "risk_distribution": risk_distribution,
                "status_distribution": status_distribution,
                "monthly_trends": monthly_trends,
                "jurisdiction_breakdown": jurisdiction_breakdown,
                "entity_type_breakdown": entity_type_breakdown,
                "pipeline_stages": pipeline_stages,
                "edd_stats": edd_stats,
                "screening_stats": screening_stats,
                "periodic_review_stats": periodic_review_stats,
                "reviewer_workload": reviewer_workload,
                "recent_decisions": recent_decisions,
            })
        finally:
            db.close()


# ══════════════════════════════════════════════════════════
# AUDIT TRAIL ENDPOINTS
# ══════════════════════════════════════════════════════════

class AuditHandler(BaseHandler):
    """GET /api/audit"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco"])
        if not user:
            return
        db = get_db()
        limit = int(self.get_argument("limit", 100))
        offset = int(self.get_argument("offset", 0))
        rows = db.execute("SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        total = db.execute("SELECT COUNT(*) as c FROM audit_log").fetchone()["c"]
        db.close()
        self.success({"entries": [dict(r) for r in rows], "total": total})


class ApplicationAuditLogHandler(BaseHandler):
    """GET /api/applications/:id/audit-log — audit trail for a single application."""
    def get(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        db = get_db()
        app = db.execute("SELECT id, ref FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)
        limit = min(int(self.get_argument("limit", "200")), 500)
        offset = max(0, int(self.get_argument("offset", "0")))
        rows = db.execute(
            "SELECT * FROM audit_log WHERE target = ? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (app["ref"], limit, offset)
        ).fetchall()
        total = db.execute("SELECT COUNT(*) as c FROM audit_log WHERE target = ?", (app["ref"],)).fetchone()["c"]
        db.close()
        self.success({"entries": [dict(r) for r in rows], "total": total})


class ApplicationNotesHandler(BaseHandler):
    """GET/POST /api/applications/:id/notes — internal officer notes."""
    def get(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        db = get_db()
        app = db.execute("SELECT id, ref FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)
        rows = db.execute(
            "SELECT * FROM application_notes WHERE application_id = ? ORDER BY created_at DESC",
            (app["id"],)
        ).fetchall()
        db.close()
        self.success({"notes": [dict(r) for r in rows]})

    def post(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        data = self.get_json()
        content = (data.get("content") or "").strip()
        if not content:
            return self.error("Note content is required", 400)
        if len(content) > 5000:
            return self.error("Note content exceeds maximum length (5000 characters)", 400)

        db = get_db()
        app = db.execute("SELECT id, ref FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        db.execute(
            "INSERT INTO application_notes (application_id, user_id, user_name, user_role, content) VALUES (?, ?, ?, ?, ?)",
            (app["id"], user.get("sub", ""), user.get("name", ""), user.get("role", ""), content)
        )
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
            (user.get("sub", ""), user.get("name", ""), user.get("role", ""), "Add Note", app["ref"],
             "Internal note added (" + str(len(content)) + " chars)", self.get_client_ip())
        )
        db.commit()
        db.close()
        self.success({"status": "ok"}, 201)


class AuditExportHandler(BaseHandler):
    """GET /api/audit/export — export audit_log entries as JSON or CSV."""

    _BASE_HEADERS = [
        "id", "timestamp", "user_id", "user_name", "user_role",
        "action", "target", "detail", "ip_address",
    ]
    _DECISION_HEADERS = [
        "decision_id", "decision_type", "decision_risk_level",
        "decision_confidence", "decision_source",
    ]

    MAX_EXPORT_ROWS = 10000

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_date(value):
        """Parse an ISO-8601 date/datetime string. Returns str or None."""
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                    "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%fZ",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(value, fmt)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        return None

    def get(self):
        user = self.require_auth(roles=["admin", "sco"])
        if not user:
            return

        fmt = self.get_argument("format", "json").lower()
        if fmt not in ("json", "csv"):
            return self.error("format must be json or csv", 400)

        start_date = self.get_argument("start_date", None)
        end_date = self.get_argument("end_date", None)
        actor_user_id = self.get_argument("actor_user_id", None)
        action_filter = self.get_argument("action", None)
        include_decisions = self.get_argument("include_decisions", "false").lower() in ("true", "1", "yes")

        if start_date:
            start_date = self._parse_date(start_date)
            if start_date is None:
                return self.error("start_date must be a valid ISO-8601 date", 400)
        if end_date:
            end_date = self._parse_date(end_date)
            if end_date is None:
                return self.error("end_date must be a valid ISO-8601 date", 400)

        db = get_db()
        try:
            query = "SELECT * FROM audit_log WHERE 1=1"
            params = []

            if start_date:
                query += " AND timestamp >= ?"
                params.append(start_date)
            if end_date:
                query += " AND timestamp <= ?"
                params.append(end_date)
            if actor_user_id:
                query += " AND user_id = ?"
                params.append(actor_user_id)
            if action_filter:
                query += " AND action = ?"
                params.append(action_filter)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(self.MAX_EXPORT_ROWS)

            rows = db.execute(query, tuple(params)).fetchall()
            entries = [dict(r) for r in rows]

            # Normalize timestamps to ISO format strings
            for entry in entries:
                if entry.get("timestamp"):
                    entry["timestamp"] = str(entry["timestamp"])

            # Optionally merge decision_records keyed by application ref
            if include_decisions:
                d_query = "SELECT * FROM decision_records WHERE 1=1"
                d_params = []
                if start_date:
                    d_query += " AND timestamp >= ?"
                    d_params.append(start_date)
                if end_date:
                    d_query += " AND timestamp <= ?"
                    d_params.append(end_date)
                if actor_user_id:
                    d_query += " AND actor_user_id = ?"
                    d_params.append(actor_user_id)
                d_query += " ORDER BY timestamp DESC"
                d_rows = db.execute(d_query, tuple(d_params)).fetchall()
                decision_map = {}
                for dr in d_rows:
                    d = dict(dr)
                    key = d.get("application_ref", "")
                    decision_map.setdefault(key, []).append(d)

                for entry in entries:
                    target = entry.get("target", "")
                    decisions = decision_map.get(target, [])
                    if decisions:
                        best = decisions[0]
                        entry["decision_id"] = best.get("id", "")
                        entry["decision_type"] = best.get("decision_type", "")
                        entry["decision_risk_level"] = best.get("risk_level", "")
                        entry["decision_confidence"] = best.get("confidence_score", "")
                        entry["decision_source"] = best.get("source", "")
                    else:
                        entry["decision_id"] = ""
                        entry["decision_type"] = ""
                        entry["decision_risk_level"] = ""
                        entry["decision_confidence"] = ""
                        entry["decision_source"] = ""

            if fmt == "csv":
                return self._write_csv(entries, include_decisions)

            self.success({"entries": entries, "total": len(entries)})
        finally:
            db.close()

    def _write_csv(self, entries, include_decisions):
        import csv, io as _io
        headers = self._BASE_HEADERS + self._DECISION_HEADERS if include_decisions else list(self._BASE_HEADERS)
        buf = _io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for entry in entries:
            writer.writerow({h: entry.get(h, "") for h in headers})
        self.set_header("Content-Type", "text/csv; charset=utf-8")
        self.set_header("Content-Disposition", "attachment; filename=audit_export.csv")
        self.write(buf.getvalue())


class SupervisorAuditExportHandler(BaseHandler):
    """GET /api/audit/supervisor/export — export supervisor_audit_log entries as JSON or CSV."""

    _BASE_HEADERS = [
        "id", "timestamp", "event_type", "severity", "pipeline_id",
        "application_id", "run_id", "agent_type", "actor_type",
        "actor_id", "actor_name", "actor_role", "action", "detail",
        "ip_address", "session_id",
    ]
    _DECISION_HEADERS = [
        "decision_id", "decision_type", "decision_risk_level",
        "decision_confidence", "decision_source",
    ]

    MAX_EXPORT_ROWS = 10000

    def get(self):
        user = self.require_auth(roles=["admin", "sco"])
        if not user:
            return

        fmt = self.get_argument("format", "json").lower()
        if fmt not in ("json", "csv"):
            return self.error("format must be json or csv", 400)

        start_date = self.get_argument("start_date", None)
        end_date = self.get_argument("end_date", None)
        actor_user_id = self.get_argument("actor_user_id", None)
        action_filter = self.get_argument("action", None)
        include_decisions = self.get_argument("include_decisions", "false").lower() in ("true", "1", "yes")

        if start_date:
            start_date = AuditExportHandler._parse_date(start_date)
            if start_date is None:
                return self.error("start_date must be a valid ISO-8601 date", 400)
        if end_date:
            end_date = AuditExportHandler._parse_date(end_date)
            if end_date is None:
                return self.error("end_date must be a valid ISO-8601 date", 400)

        db = get_db()
        try:
            query = "SELECT * FROM supervisor_audit_log WHERE 1=1"
            params = []

            if start_date:
                query += " AND timestamp >= ?"
                params.append(start_date)
            if end_date:
                query += " AND timestamp <= ?"
                params.append(end_date)
            if actor_user_id:
                query += " AND actor_id = ?"
                params.append(actor_user_id)
            if action_filter:
                query += " AND action = ?"
                params.append(action_filter)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(self.MAX_EXPORT_ROWS)

            rows = db.execute(query, tuple(params)).fetchall()
            entries = [dict(r) for r in rows]

            # Normalize timestamps and strip internal hash fields
            for entry in entries:
                if entry.get("timestamp"):
                    entry["timestamp"] = str(entry["timestamp"])
                entry.pop("previous_hash", None)
                entry.pop("entry_hash", None)
                entry.pop("data_json", None)

            # Optionally merge decision_records by application_id → application_ref
            if include_decisions:
                # Build a mapping from application id → ref for correct join
                app_ids = list({e.get("application_id", "") for e in entries if e.get("application_id")})
                id_to_ref = {}
                if app_ids:
                    placeholders = ",".join("?" for _ in app_ids)
                    app_rows = db.execute(
                        f"SELECT id, ref FROM applications WHERE id IN ({placeholders}) OR ref IN ({placeholders})",
                        tuple(app_ids + app_ids),
                    ).fetchall()
                    for ar in app_rows:
                        row = dict(ar)
                        id_to_ref[row["id"]] = row["ref"]
                        id_to_ref[row["ref"]] = row["ref"]

                d_query = "SELECT * FROM decision_records WHERE 1=1"
                d_params = []
                if start_date:
                    d_query += " AND timestamp >= ?"
                    d_params.append(start_date)
                if end_date:
                    d_query += " AND timestamp <= ?"
                    d_params.append(end_date)
                if actor_user_id:
                    d_query += " AND actor_user_id = ?"
                    d_params.append(actor_user_id)
                d_query += " ORDER BY timestamp DESC"
                d_rows = db.execute(d_query, tuple(d_params)).fetchall()
                decision_map = {}
                for dr in d_rows:
                    d = dict(dr)
                    key = d.get("application_ref", "")
                    decision_map.setdefault(key, []).append(d)

                for entry in entries:
                    app_id = entry.get("application_id", "")
                    app_ref = id_to_ref.get(app_id, app_id)
                    decisions = decision_map.get(app_ref, [])
                    if decisions:
                        best = decisions[0]
                        entry["decision_id"] = best.get("id", "")
                        entry["decision_type"] = best.get("decision_type", "")
                        entry["decision_risk_level"] = best.get("risk_level", "")
                        entry["decision_confidence"] = best.get("confidence_score", "")
                        entry["decision_source"] = best.get("source", "")
                    else:
                        entry["decision_id"] = ""
                        entry["decision_type"] = ""
                        entry["decision_risk_level"] = ""
                        entry["decision_confidence"] = ""
                        entry["decision_source"] = ""

            if fmt == "csv":
                return self._write_csv(entries, include_decisions)

            self.success({"entries": entries, "total": len(entries)})
        finally:
            db.close()

    def _write_csv(self, entries, include_decisions):
        import csv, io as _io
        headers = self._BASE_HEADERS + self._DECISION_HEADERS if include_decisions else list(self._BASE_HEADERS)
        buf = _io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for entry in entries:
            writer.writerow({h: entry.get(h, "") for h in headers})
        self.set_header("Content-Type", "text/csv; charset=utf-8")
        self.set_header("Content-Disposition", "attachment; filename=supervisor_audit_export.csv")
        self.write(buf.getvalue())


# ══════════════════════════════════════════════════════════
# DASHBOARD STATS
# ══════════════════════════════════════════════════════════

class DashboardHandler(BaseHandler):
    """GET /api/dashboard"""
    def get(self):
        user = self.require_auth()
        if not user:
            return

        from fixture_filter import (
            fixture_app_exclude_clause,
            should_show_fixtures,
        )

        db = get_db()
        stats = {}

        if user.get("type") == "client":
            client_id = user["sub"]
            # Client branch is scoped by client_id. Fixtures are never assigned
            # a real client_id, so the client_id WHERE clause acts as an implicit
            # fixture filter — no explicit fixture exclusion needed here.
            stats["total"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE client_id=?", (client_id,)).fetchone()["c"]
            stats["early_stage_applications"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status IN ('submitted','prescreening_submitted') AND client_id=?", (client_id,)).fetchone()["c"]
            stats["in_review"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='in_review' AND client_id=?", (client_id,)).fetchone()["c"]
            stats["kyc_documents"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='kyc_documents' AND client_id=?", (client_id,)).fetchone()["c"]
            stats["compliance_review"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status IN ('compliance_review','kyc_submitted') AND client_id=?", (client_id,)).fetchone()["c"]
            stats["approved"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='approved' AND client_id=?", (client_id,)).fetchone()["c"]
            stats["rejected"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='rejected' AND client_id=?", (client_id,)).fetchone()["c"]
            stats["edd"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='edd_required' AND client_id=?", (client_id,)).fetchone()["c"]

            # Risk distribution
            stats["risk_low"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level='LOW' AND client_id=?", (client_id,)).fetchone()["c"]
            stats["risk_medium"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level='MEDIUM' AND client_id=?", (client_id,)).fetchone()["c"]
            stats["risk_high"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level='HIGH' AND client_id=?", (client_id,)).fetchone()["c"]
            stats["risk_very_high"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level='VERY_HIGH' AND client_id=?", (client_id,)).fetchone()["c"]

            # Recent applications
            recent = db.execute("""
                SELECT a.*, u.full_name as assigned_name FROM applications a
                LEFT JOIN users u ON a.assigned_to = u.id
                WHERE a.client_id=?
                ORDER BY a.created_at DESC LIMIT 10
            """, (client_id,)).fetchall()
            stats["recent"] = [dict(r) for r in recent]
        else:
            # Officer / admin branch: exclude fixtures by default.
            # Pass show_fixtures=true (admin/sco only) to include them.
            show_fx = should_show_fixtures(user, self.get_argument("show_fixtures", None))
            fx_excl, fx_params = fixture_app_exclude_clause(table_alias="")
            fx_clause = "" if show_fx else f" AND {fx_excl}"
            fp = [] if show_fx else fx_params

            stats["total"] = db.execute(f"SELECT COUNT(*) as c FROM applications WHERE 1=1{fx_clause}", fp).fetchone()["c"]
            stats["early_stage_applications"] = db.execute(f"SELECT COUNT(*) as c FROM applications WHERE status IN ('submitted','prescreening_submitted'){fx_clause}", fp).fetchone()["c"]
            stats["in_review"] = db.execute(f"SELECT COUNT(*) as c FROM applications WHERE status='in_review'{fx_clause}", fp).fetchone()["c"]
            stats["kyc_documents"] = db.execute(f"SELECT COUNT(*) as c FROM applications WHERE status='kyc_documents'{fx_clause}", fp).fetchone()["c"]
            stats["compliance_review"] = db.execute(f"SELECT COUNT(*) as c FROM applications WHERE status IN ('compliance_review','kyc_submitted'){fx_clause}", fp).fetchone()["c"]
            stats["approved"] = db.execute(f"SELECT COUNT(*) as c FROM applications WHERE status='approved'{fx_clause}", fp).fetchone()["c"]
            stats["rejected"] = db.execute(f"SELECT COUNT(*) as c FROM applications WHERE status='rejected'{fx_clause}", fp).fetchone()["c"]
            stats["edd"] = db.execute(f"SELECT COUNT(*) as c FROM applications WHERE status='edd_required'{fx_clause}", fp).fetchone()["c"]

            # Risk distribution
            stats["risk_low"] = db.execute(f"SELECT COUNT(*) as c FROM applications WHERE risk_level='LOW'{fx_clause}", fp).fetchone()["c"]
            stats["risk_medium"] = db.execute(f"SELECT COUNT(*) as c FROM applications WHERE risk_level='MEDIUM'{fx_clause}", fp).fetchone()["c"]
            stats["risk_high"] = db.execute(f"SELECT COUNT(*) as c FROM applications WHERE risk_level='HIGH'{fx_clause}", fp).fetchone()["c"]
            stats["risk_very_high"] = db.execute(f"SELECT COUNT(*) as c FROM applications WHERE risk_level='VERY_HIGH'{fx_clause}", fp).fetchone()["c"]

            # Recent applications
            recent_fx_excl, recent_fx_params = fixture_app_exclude_clause(table_alias="a")
            fx_where_clause = "" if show_fx else f" AND {recent_fx_excl}"
            recent_fp = [] if show_fx else recent_fx_params
            recent = db.execute(f"""
                SELECT a.*, u.full_name as assigned_name FROM applications a
                LEFT JOIN users u ON a.assigned_to = u.id
                WHERE 1=1{fx_where_clause}
                ORDER BY a.created_at DESC LIMIT 10
            """, recent_fp).fetchall()
            stats["recent"] = [dict(r) for r in recent]
            stats["show_fixtures"] = show_fx

        db.close()
        self.success(stats)


# ══════════════════════════════════════════════════════════
# SAVE & RESUME (Client Portal)
# ══════════════════════════════════════════════════════════

class SaveResumeHandler(BaseHandler):
    """Portal draft persistence — save / resume / discard a single application's form state.

    Endpoints:
      POST   /api/save-resume                — create or update the draft for an app
      GET    /api/save-resume?application_id=… — retrieve the draft for an app
      DELETE /api/save-resume?application_id=… — discard the draft for an app

    Only portal client users may use this endpoint. Drafts are scoped strictly
    to the authenticated client_id AND the application_id (which must belong
    to that client). form_data is encrypted at rest where a PII encryption
    key is configured; legacy plaintext rows continue to read transparently.
    """

    # ── Internal helpers ────────────────────────────────────────
    def _require_portal_client(self):
        """Restrict draft endpoints to portal client users only."""
        user = self.require_auth()
        if not user:
            return None
        if user.get("type") != "client":
            self.error("Draft persistence is only available to portal clients.", 403)
            return None
        return user

    def _assert_app_belongs_to_client(self, db, app_id, client_id):
        """Defence-in-depth: verify that application_id belongs to the calling client.

        Returns the application row on success, sends an error response and
        returns None on failure. Prevents one client from writing/reading a
        draft keyed to another client's application_id.
        """
        if not app_id:
            self.error("application_id required", 400)
            return None
        row = db.execute(
            "SELECT id, ref, client_id, status, company_name FROM applications WHERE id=? OR ref=?",
            (app_id, app_id),
        ).fetchone()
        if not row:
            self.error("Application not found", 404)
            return None
        if row["client_id"] != client_id:
            # Same shape as not-found to avoid enumeration leakage
            self.error("Application not found", 404)
            return None
        return row

    @staticmethod
    def _normalize_company_key(name):
        return re.sub(r"\s+", " ", (name or "").strip()).lower()

    @staticmethod
    def _is_missing_column_error(exc, column_name):
        msg = str(exc or "").lower()
        col = str(column_name or "").lower()
        if not msg or not col:
            return False
        return (
            ("no such column" in msg and col in msg)
            or ("does not exist" in msg and col in msg)
        )

    @staticmethod
    def _is_unique_constraint_error(exc):
        """Detect UNIQUE constraint violations in both SQLite and PostgreSQL."""
        msg = str(exc or "").lower()
        return (
            "unique constraint failed" in msg       # SQLite
            or "unique violation" in msg            # PostgreSQL (psycopg2)
            or "duplicate key value" in msg         # PostgreSQL (detail message)
            or "already exists" in msg              # PostgreSQL (some messages)
        )

    def _ensure_pre_submit_draft_application(self, db, client_id, form_data):
        """Create or reuse a draft application shell before first submit."""
        normalized_from_session = normalize_saved_session_prescreening(form_data) or {}
        normalized_prescreening = normalize_prescreening_data(form_data or {})
        if normalized_from_session:
            normalized_prescreening = normalize_prescreening_data(
                {"prescreening_data": normalized_from_session},
                existing=normalized_prescreening,
            )
        if not isinstance(normalized_prescreening, dict):
            normalized_prescreening = {}

        company_name = resolve_application_company_name(form_data if isinstance(form_data, dict) else {}, normalized_prescreening)
        if not company_name:
            self.error(
                "Registered entity name is required before saving your first draft.",
                400
            )
            return None

        normalized_name = self._normalize_company_key(company_name)
        try:
            existing_drafts = db.execute(
                "SELECT id, ref, company_name FROM applications WHERE client_id=? AND status='draft' ORDER BY updated_at DESC",
                (client_id,)
            ).fetchall()
        except Exception as exc:
            if not self._is_missing_column_error(exc, "updated_at"):
                raise
            # Legacy-schema fallback: older staging databases can miss applications.updated_at.
            existing_drafts = db.execute(
                "SELECT id, ref, company_name FROM applications WHERE client_id=? AND status='draft' ORDER BY created_at DESC, id DESC",
                (client_id,)
            ).fetchall()
        for row in existing_drafts:
            candidate = self._normalize_company_key(row.get("company_name"))
            if candidate == normalized_name:
                return row

        # Insert the new draft application shell.  Retry up to 3 times on the
        # rare UNIQUE ref collision that can still occur under high concurrency
        # (the MAX-based generate_ref() already eliminates the deletion-caused
        # systematic collision; this loop covers the concurrent-create window).
        app_id = uuid.uuid4().hex[:16]
        for _ref_attempt in range(3):
            ref = generate_ref()
            try:
                db.execute(
                    """
                    INSERT INTO applications (
                        id, ref, client_id, company_name, brn, country, sector,
                        entity_type, ownership_structure, prescreening_data, status
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        app_id,
                        ref,
                        client_id,
                        company_name,
                        first_non_empty(normalized_prescreening.get("brn"), ""),
                        first_non_empty(normalized_prescreening.get("country_of_incorporation"), ""),
                        first_non_empty(normalized_prescreening.get("sector"), ""),
                        first_non_empty(normalized_prescreening.get("entity_type"), ""),
                        first_non_empty(normalized_prescreening.get("ownership_structure"), ""),
                        json.dumps(normalized_prescreening),
                        "draft",
                    )
                )
                break  # INSERT succeeded
            except Exception as exc:
                if self._is_unique_constraint_error(exc) and _ref_attempt < 2:
                    logger.warning(
                        "generate_ref collision on attempt %d (ref=%s) — retrying",
                        _ref_attempt + 1, ref,
                    )
                    continue
                raise
        return {"id": app_id, "ref": ref, "company_name": company_name, "status": "draft"}

    # ── HTTP methods ────────────────────────────────────────────
    def get(self):
        user = self._require_portal_client()
        if not user:
            return
        app_id = self.get_argument("application_id", None)
        db = get_db()
        try:
            app_row = self._assert_app_belongs_to_client(db, app_id, user["sub"])
            if not app_row:
                return
            real_id = app_row["id"]
            session = db.execute(
                "SELECT * FROM client_sessions WHERE client_id=? AND application_id=? "
                "ORDER BY updated_at DESC LIMIT 1",
                (user["sub"], real_id),
            ).fetchone()
            if session:
                self.success({
                    "form_data": decrypt_draft_form_data(session["form_data"]),
                    "last_step": session["last_step"],
                    "application_id": session["application_id"],
                    "last_saved_at": session["updated_at"],
                })
            else:
                self.success({"form_data": {}, "last_step": 0, "last_saved_at": None})
        finally:
            db.close()

    def post(self):
        user = self._require_portal_client()
        if not user:
            return
        data = self.get_json() or {}
        app_id = data.get("application_id")
        form_data = _extract_save_resume_form_data(data)
        last_step = data.get("last_step", 0)

        # Reject drafts with no meaningful content — prevents noisy empty
        # autosaves on a freshly opened (untouched) form.
        if not _draft_payload_is_meaningful(form_data):
            return self.error("Draft is empty — nothing to save.", 400)

        db = get_db()
        try:
            app_row = None
            if app_id:
                app_row = self._assert_app_belongs_to_client(db, app_id, user["sub"])
            else:
                app_row = self._ensure_pre_submit_draft_application(db, user["sub"], form_data)
            if not app_row:
                return
            real_id = app_row["id"]
            stored_form_data = encrypt_draft_form_data(form_data)

            existing = db.execute(
                "SELECT id FROM client_sessions WHERE client_id=? AND application_id=?",
                (user["sub"], real_id),
            ).fetchone()
            if existing:
                # Single active draft per (client, application) — updates in place,
                # never inserts a duplicate row.
                try:
                    db.execute(
                        "UPDATE client_sessions SET form_data=?, last_step=?, "
                        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (stored_form_data, last_step, existing["id"]),
                    )
                except Exception as exc:
                    if not self._is_missing_column_error(exc, "updated_at"):
                        raise
                    db.execute(
                        "UPDATE client_sessions SET form_data=?, last_step=? WHERE id=?",
                        (stored_form_data, last_step, existing["id"]),
                    )
            else:
                db.execute(
                    "INSERT INTO client_sessions (client_id, application_id, form_data, last_step) "
                    "VALUES (?,?,?,?)",
                    (user["sub"], real_id, stored_form_data, last_step),
                )
            db.commit()
            try:
                saved_at = db.execute(
                    "SELECT updated_at FROM client_sessions WHERE client_id=? AND application_id=?",
                    (user["sub"], real_id),
                ).fetchone()
            except Exception as exc:
                if not self._is_missing_column_error(exc, "updated_at"):
                    raise
                saved_at = None
        finally:
            db.close()
        self.success({
            "status": "saved",
            "application_id": real_id,
            "application_ref": app_row.get("ref"),
            "last_saved_at": saved_at["updated_at"] if saved_at else None,
        })

    def delete(self):
        """DELETE /api/save-resume — discard the draft for an application."""
        user = self._require_portal_client()
        if not user:
            return
        app_id = self.get_argument("application_id", None)
        db = get_db()
        try:
            app_row = self._assert_app_belongs_to_client(db, app_id, user["sub"])
            if not app_row:
                return
            real_id = app_row["id"]
            db.execute(
                "DELETE FROM client_sessions WHERE client_id=? AND application_id=?",
                (user["sub"], real_id),
            )
            db.commit()
        finally:
            db.close()
        self.success({"status": "deleted"})


class ActiveDraftsHandler(BaseHandler):
    """GET /api/save-resume/active — list the calling client's active in-progress drafts.

    Drives the portal Resume/Discard banner. Returns at most the most recent
    open (non-terminal) drafts joined with their parent application's metadata
    so the UI can render a per-draft Resume + Discard control.
    """

    TERMINAL_STATUSES = ("approved", "rejected", "withdrawn")

    def get(self):
        user = self.require_auth()
        if not user:
            return
        if user.get("type") != "client":
            return self.error("Draft persistence is only available to portal clients.", 403)
        client_id = user["sub"]
        db = get_db()
        try:
            placeholders = ",".join("?" * len(self.TERMINAL_STATUSES))
            rows = db.execute(
                f"""
                SELECT cs.application_id, cs.last_step, cs.updated_at AS last_saved_at,
                       a.ref, a.company_name, a.status
                FROM client_sessions cs
                JOIN applications a ON a.id = cs.application_id
                WHERE cs.client_id = ?
                  AND a.client_id  = ?
                  AND a.status NOT IN ({placeholders})
                ORDER BY cs.updated_at DESC
                """,
                (client_id, client_id, *self.TERMINAL_STATUSES),
            ).fetchall()
        finally:
            db.close()
        drafts = [
            {
                "application_id": r["application_id"],
                "ref": r["ref"],
                "company_name": r["company_name"],
                "status": r["status"],
                "status_label": get_status_label(r["status"]),
                "last_step": r["last_step"],
                "last_saved_at": r["last_saved_at"],
            }
            for r in rows
        ]
        self.success({"drafts": drafts})


# ══════════════════════════════════════════════════════════
# PORTAL FILE SERVING
# ══════════════════════════════════════════════════════════

# Look for HTML files in parent dir (local dev) or same dir (Docker)
_parent_dir = os.path.join(os.path.dirname(__file__), "..")
_same_dir = os.path.dirname(__file__)
if os.path.exists(os.path.join(_parent_dir, "arie-portal.html")):
    PORTAL_DIR = _parent_dir
elif os.path.exists(os.path.join(_same_dir, "arie-portal.html")):
    PORTAL_DIR = _same_dir
else:
    PORTAL_DIR = _parent_dir  # fallback

class PortalHandler(tornado.web.RequestHandler):
    """Serve the client portal HTML"""
    def get(self):
        self.set_header("Content-Type", "text/html")
        with open(os.path.join(PORTAL_DIR, "arie-portal.html"), "r") as f:
            self.write(f.read())

class BackOfficeHandler(tornado.web.RequestHandler):
    """Serve the back-office portal HTML"""
    def get(self):
        self.set_header("Content-Type", "text/html")
        with open(os.path.join(PORTAL_DIR, "arie-backoffice.html"), "r") as f:
            self.write(f.read())


def _screening_hit_facts(screening_record):
    results = (screening_record or {}).get("results", []) or []
    sanctions_hits = sum(1 for hit in results if hit.get("is_sanctioned"))
    pep_hits = sum(1 for hit in results if hit.get("is_pep"))
    return {
        "total_hits": len(results),
        "sanctions_hits": sanctions_hits,
        "pep_hits": pep_hits,
        "other_hits": max(0, len(results) - sanctions_hits - pep_hits),
    }


def upsert_screening_review(db, application_id, subject_type, subject_name, disposition, notes, reviewer_id, reviewer_name):
    db.execute(
        """
        INSERT INTO screening_reviews
        (application_id, subject_type, subject_name, disposition, notes, reviewer_id, reviewer_name)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(application_id, subject_type, subject_name)
        DO UPDATE SET
            disposition=excluded.disposition,
            notes=excluded.notes,
            reviewer_id=excluded.reviewer_id,
            reviewer_name=excluded.reviewer_name,
            updated_at=CURRENT_TIMESTAMP
        """,
        (application_id, subject_type, subject_name, disposition, notes, reviewer_id, reviewer_name),
    )


def _build_screening_queue_payload(db, user):
    query = "SELECT * FROM applications WHERE 1=1"
    params = []
    if user["type"] == "client":
        query += " AND client_id = ?"
        params.append(user["sub"])
    query += " ORDER BY created_at DESC LIMIT 200"

    apps = [dict(r) for r in db.execute(query, params).fetchall()]
    rows = []
    metrics = {
        "applications_awaiting_screening": 0,
        "applications_screened": 0,
        "applications_requiring_review": 0,
        "subject_rows": 0,
    }

    for app in apps:
        directors = [decrypt_pii_fields(dict(d), PII_FIELDS_DIRECTORS) for d in db.execute(
            "SELECT * FROM directors WHERE application_id = ?", (app["id"],)).fetchall()]
        ubos = [decrypt_pii_fields(dict(u), PII_FIELDS_UBOS) for u in db.execute(
            "SELECT * FROM ubos WHERE application_id = ?", (app["id"],)).fetchall()]
        review_map = {}
        for review in db.execute(
            "SELECT * FROM screening_reviews WHERE application_id = ?",
            (app["id"],),
        ).fetchall():
            review = dict(review)
            review_map[(review.get("subject_type"), review.get("subject_name"))] = review

        prescreening = safe_json_loads(app.get("prescreening_data"))
        report = prescreening.get("screening_report") or None
        overall_flags = report.get("overall_flags", []) if report else []
        screening_mode = report.get("screening_mode") if report else None
        screened_at = (report or {}).get("screened_at") or prescreening.get("last_screened_at")
        screened_by = prescreening.get("screened_by")

        if report:
            metrics["applications_screened"] += 1
        else:
            metrics["applications_awaiting_screening"] += 1

        person_screenings = {}
        if report:
            for item in (report.get("director_screenings") or []) + (report.get("ubo_screenings") or []):
                person_screenings[item.get("person_name")] = item

        company_screening = (report or {}).get("company_screening") or {}
        company_sanctions = company_screening.get("sanctions") or {}
        company_ip = (report or {}).get("ip_geolocation") or {}
        company_kyc = (report or {}).get("kyc_applicants") or []
        company_registry_found = company_screening.get("found")

        # Priority A: derive canonical company sanctions state from provider
        # api_status. Never coerce pending/not_configured/error/unavailable
        # into "clear".
        company_sanctions_state = derive_screening_state(company_sanctions)
        company_sanctions_subject = derive_subject_state(company_sanctions)
        company_watchlist_status = _screening_legacy_status(
            company_sanctions_state,
            company_sanctions_subject["has_provider_sanctions_hit"],
        )

        company_context = []
        if report:
            company_context.append(
                "Registry found" if company_registry_found else "Registry not found"
            )
            if company_ip.get("risk_level"):
                company_context.append("IP risk: " + company_ip.get("risk_level"))
            if company_ip.get("is_vpn"):
                company_context.append("VPN detected")
            if company_ip.get("is_proxy"):
                company_context.append("Proxy detected")
            if company_ip.get("is_tor"):
                company_context.append("Tor detected")
            rejected_kyc = [a.get("person_name") for a in company_kyc if a.get("review_answer") == "RED"]
            if rejected_kyc:
                company_context.append("KYC RED: " + ", ".join(rejected_kyc))
            # Surface explicit non-terminal sanctions state so officers cannot
            # mistake "we did not get a real answer" for "clear".
            if company_sanctions_state == _SCR_NOT_CONFIGURED:
                company_context.append("Company sanctions screening not configured")
            elif company_sanctions_state == _SCR_FAILED:
                company_context.append("Company sanctions screening unavailable")
            elif company_sanctions_state in (_SCR_PENDING, _SCR_PARTIAL, _SCR_NOT_STARTED):
                company_context.append("Company sanctions screening pending")

        # Fail-closed: any non-terminal company sanctions state requires
        # officer review. Previously only ``matched`` triggered review,
        # which silently passed not_configured / pending / unavailable.
        # Equivalent to "anything that is not completed_clear", expressed
        # explicitly so the intent is auditable.
        company_sanctions_requires_review = company_sanctions_state != _SCR_COMPLETED_CLEAR

        company_requires_review = False
        if report:
            company_requires_review = (
                company_sanctions_requires_review or
                company_registry_found is False or
                company_ip.get("risk_level") in ("HIGH", "VERY_HIGH") or
                company_ip.get("is_vpn") or
                company_ip.get("is_proxy") or
                company_ip.get("is_tor") or
                any(a.get("review_answer") == "RED" for a in company_kyc)
            )

        application_requires_review = company_requires_review

        company_review = review_map.get(("entity", app["company_name"]))
        if directors or ubos or report:
            # Priority A: explicit, truthful entity status_label / status_key.
            # Provider-derived states drive the label so non-terminal cases
            # cannot masquerade as "No Provider Match".
            if not report:
                entity_status_key = "awaiting_screening"
                entity_status_label = "Awaiting Screening"
            elif company_sanctions_state == _SCR_COMPLETED_MATCH:
                entity_status_key = "review_required"
                entity_status_label = "Review Required"
            elif company_sanctions_state == _SCR_NOT_CONFIGURED:
                entity_status_key = "screening_not_configured"
                entity_status_label = "Screening Not Configured"
            elif company_sanctions_state == _SCR_FAILED:
                entity_status_key = "screening_unavailable"
                entity_status_label = "Screening Unavailable"
            elif company_sanctions_state in (_SCR_PENDING, _SCR_PARTIAL, _SCR_NOT_STARTED):
                entity_status_key = "screening_pending"
                entity_status_label = "Screening Pending Provider"
            elif company_requires_review:
                # Terminal-clear sanctions but other entity-level signal
                # (registry not found, IP risk, KYC RED) requires review.
                entity_status_key = "review_required"
                entity_status_label = "Review Required"
            else:
                entity_status_key = "screened_no_match"
                entity_status_label = "No Provider Match"

            rows.append({
                "application_id": app["id"],
                "application_ref": app["ref"],
                "company_name": app["company_name"],
                "subject_name": app["company_name"],
                "subject_type": "entity",
                "watchlist_status": company_watchlist_status,
                "pep_declared_status": "not_applicable",
                "pep_screening_status": "not_applicable",
                "screening_state": company_sanctions_state,
                "entity_context": company_context,
                "status_key": entity_status_key,
                "status_label": entity_status_label,
                "screening_mode": screening_mode,
                "screened_at": screened_at,
                "screened_by": screened_by,
                "flag_count": len(overall_flags),
                "total_hits": (report or {}).get("total_hits", 0),
                "review_required": company_requires_review,
                "review_disposition": (company_review or {}).get("disposition"),
                "review_notes": (company_review or {}).get("notes"),
                "reviewed_by": (company_review or {}).get("reviewer_name"),
                "reviewed_at": (company_review or {}).get("updated_at") or (company_review or {}).get("created_at"),
            })

        for person, subject_type in [(d, "director") for d in directors] + [(u, "ubo") for u in ubos]:
            person_name = person.get("full_name", "")
            item = person_screenings.get(person_name)
            screening = (item or {}).get("screening") or {}
            facts = _screening_hit_facts(screening)
            # Priority A.2: declared PEP must survive any non-canonical
            # truthy form ("Yes"/"yes"/True/"true"/"1"). Falling back to the
            # raw == "Yes" check silently flattened declared PEP into
            # "Not Declared" on the queue chip whenever stored data did
            # not exactly match "Yes".
            declared_pep = normalize_is_pep(person.get("is_pep", "No")) == "Yes"
            provider_other = facts["other_hits"] > 0

            # Priority A: derive canonical state from api_status. Never let
            # pending/init/created/error/unavailable/not_configured collapse
            # into "clear". Declared PEP is preserved as a separate signal.
            person_state = derive_screening_state(screening)
            subject_envelope = derive_subject_state(screening, declared_pep=declared_pep)
            has_pep_hit = subject_envelope["has_provider_pep_hit"]
            has_sanctions_hit = subject_envelope["has_provider_sanctions_hit"]
            # ``undeclared_pep`` is computed by run_full_screening and only
            # set when results contain a PEP hit — i.e. terminal. It is
            # therefore safe to treat it as a provider PEP hit.
            if (item or {}).get("undeclared_pep"):
                has_pep_hit = True

            if not report:
                person_state = _SCR_NOT_STARTED
                watchlist_status = "pending"
                pep_screening_status = "pending"
            elif not item:
                # We have a report but no screening sub-record for this
                # person — fail-closed: treat as incomplete, never clear.
                person_state = _SCR_NOT_STARTED
                watchlist_status = "pending"
                pep_screening_status = "pending"
            else:
                watchlist_status = _screening_legacy_status(person_state, has_sanctions_hit)
                pep_screening_status = _screening_legacy_status(person_state, has_pep_hit)

            # Status key/label — declared PEP must remain visible even when
            # the provider is non-terminal.
            if not report:
                status_key = "awaiting_screening"
                status_label = "Awaiting Screening"
            elif not item:
                status_key = "incomplete_record"
                status_label = "Incomplete Screening Record"
            elif person_state == _SCR_COMPLETED_MATCH or has_sanctions_hit or has_pep_hit or provider_other:
                status_key = "review_required"
                status_label = "Review Required"
            elif person_state == _SCR_NOT_CONFIGURED:
                status_key = "screening_not_configured"
                status_label = "Declared PEP — Screening Not Configured" if declared_pep else "Screening Not Configured"
            elif person_state == _SCR_FAILED:
                status_key = "screening_unavailable"
                status_label = "Declared PEP — Screening Unavailable" if declared_pep else "Screening Unavailable"
            elif person_state in (_SCR_PENDING, _SCR_PARTIAL, _SCR_NOT_STARTED):
                status_key = "screening_pending"
                status_label = "Declared PEP — Screening Pending Provider" if declared_pep else "Screening Pending Provider"
            elif declared_pep:
                # Terminal-clear provider, but declared PEP must still be
                # surfaced for officer review.
                status_key = "declared_pep_review"
                status_label = "Declared PEP Review"
            else:
                status_key = "screened_no_match"
                status_label = "No Provider Match"

            # Fail-closed review requirement: any non-terminal state, any
            # match, any declared PEP, any not_configured/failed.
            requires_review = (
                status_key in (
                    "review_required", "declared_pep_review", "incomplete_record",
                    "screening_pending", "screening_not_configured",
                    "screening_unavailable", "awaiting_screening",
                )
            )
            person_review = review_map.get((subject_type, person_name))
            review_disposition = (person_review or {}).get("disposition")
            review_resolved = review_disposition == "cleared"
            if requires_review and not review_resolved:
                application_requires_review = True

            entity_context = []
            if screening.get("source"):
                entity_context.append("Source: " + screening.get("source"))
            if screening.get("api_status"):
                entity_context.append("API: " + screening.get("api_status"))
            if (item or {}).get("undeclared_pep"):
                entity_context.append("Undeclared PEP")
            if declared_pep:
                # Always surface declared PEP separately from provider state
                # so it cannot be lost behind a "Pending" or "Clear" label.
                entity_context.append("Declared PEP")
            if person_state == _SCR_NOT_CONFIGURED:
                entity_context.append("Provider not configured")
            elif person_state == _SCR_FAILED:
                entity_context.append("Provider unavailable")
            elif person_state in (_SCR_PENDING, _SCR_PARTIAL, _SCR_NOT_STARTED) and report and item:
                entity_context.append("Provider result pending")

            rows.append({
                "application_id": app["id"],
                "application_ref": app["ref"],
                "company_name": app["company_name"],
                "subject_name": person_name,
                "subject_type": subject_type,
                "watchlist_status": watchlist_status,
                "pep_declared_status": "declared" if declared_pep else "not_declared",
                "pep_screening_status": pep_screening_status,
                "screening_state": person_state,
                "entity_context": entity_context,
                "status_key": status_key,
                "status_label": status_label,
                "screening_mode": screening_mode,
                "screened_at": screening.get("screened_at") or screened_at,
                "screened_by": screened_by,
                "flag_count": len(overall_flags),
                "total_hits": facts["total_hits"],
                "review_required": requires_review,
                "review_disposition": review_disposition,
                "review_notes": (person_review or {}).get("notes"),
                "reviewed_by": (person_review or {}).get("reviewer_name"),
                "reviewed_at": (person_review or {}).get("updated_at") or (person_review or {}).get("created_at"),
            })

        if company_requires_review and (company_review or {}).get("disposition") != "cleared":
            application_requires_review = True
        if application_requires_review:
            metrics["applications_requiring_review"] += 1

    metrics["subject_rows"] = len(rows)
    return {
        "metrics": metrics,
        "rows": rows,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ══════════════════════════════════════════════════════════
# SCREENING ENDPOINTS (Real API Integrations)
# ══════════════════════════════════════════════════════════

class ScreeningQueueHandler(BaseHandler):
    """GET /api/screening/queue — authoritative screening queue payload"""
    def get(self):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        payload = _build_screening_queue_payload(db, user)
        db.close()
        self.success(payload)


class ScreeningReviewHandler(BaseHandler):
    """POST /api/screening/review — persist reviewer disposition for a screening queue row"""
    def post(self):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return

        data = self.get_json()
        app_id = data.get("application_id")
        subject_type = (data.get("subject_type") or "").strip().lower()
        subject_name = (data.get("subject_name") or "").strip()
        disposition = (data.get("disposition") or "").strip().lower()
        notes = (data.get("notes") or "").strip()

        if not app_id or not subject_type or not subject_name or not disposition:
            return self.error("application_id, subject_type, subject_name, and disposition are required")

        if disposition not in ("cleared", "escalated", "follow_up_required"):
            return self.error("Unsupported screening review disposition", 400)

        db = get_db()
        app = db.execute("SELECT id, ref FROM applications WHERE id=? OR ref=?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        upsert_screening_review(
            db,
            app["id"],
            subject_type,
            subject_name,
            disposition,
            notes,
            user["sub"],
            user.get("name") or user.get("full_name") or user["sub"],
        )

        # EX-09: Recompute risk when screening review indicates escalation
        risk_recomputed = False
        if disposition == "escalated":
            rr = recompute_risk(db, app["id"], "screening_review_escalated",
                                user=user, log_audit_fn=self.log_audit)
            risk_recomputed = rr.get("recomputed", False)

        db.commit()

        disposition_label = disposition.replace("_", " ")
        self.log_audit(user, "Screening Review", app["ref"], f"{subject_type}:{subject_name} -> {disposition_label}", db=db)
        review = dict(db.execute(
            """
            SELECT application_id, subject_type, subject_name, disposition, notes, reviewer_name, created_at, updated_at
            FROM screening_reviews WHERE application_id=? AND subject_type=? AND subject_name=?
            """,
            (app["id"], subject_type, subject_name),
        ).fetchone())
        db.close()
        response = {"review": review}
        if risk_recomputed:
            response["risk_recomputed"] = True
        self.success(response)


class ScreeningHandler(BaseHandler):
    """POST /api/screening/run — run full screening for an application"""
    def post(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        if not self.check_rate_limit("screening", max_attempts=20, window_seconds=60):
            return

        # P0-3: Check if Agent 3 (screening) is enabled before executing
        db = get_db()
        agent3 = db.execute("SELECT enabled FROM ai_agents WHERE agent_number=3").fetchone()
        if agent3 and not agent3["enabled"]:
            db.close()
            self.log_audit(user, "Agent Skipped", "Agent 3", "Screening skipped — agent disabled")
            self.success({
                "status": "skipped",
                "message": "Screening agent is currently disabled",
                "total_hits": 0,
                "overall_flags": [],
                "requires_review": True
            })
            return
        db.close()

        data = self.get_json()
        app_id = data.get("application_id")
        if not app_id:
            return self.error("application_id required")

        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id=? OR ref=?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        real_id = app["id"]
        directors = [decrypt_pii_fields(dict(d), PII_FIELDS_DIRECTORS) for d in db.execute("SELECT * FROM directors WHERE application_id=?", (real_id,)).fetchall()]
        ubos = [decrypt_pii_fields(dict(u), PII_FIELDS_UBOS) for u in db.execute("SELECT * FROM ubos WHERE application_id=?", (real_id,)).fetchall()]

        app_data = {
            "company_name": app["company_name"],
            "country": app["country"],
            "sector": app["sector"],
            "entity_type": app["entity_type"],
        }

        report = run_full_screening(app_data, directors, ubos, client_ip=self.get_client_ip())
        screening_mode = determine_screening_mode(report)
        report["screening_mode"] = screening_mode
        store_screening_mode(db, real_id, screening_mode)

        # Store screening report
        prescreening = safe_json_loads(app["prescreening_data"])
        prescreening["screening_report"] = report
        now_utc = datetime.now(timezone.utc)
        prescreening["last_screened_at"] = now_utc.strftime("%Y-%m-%dT%H:%M:%S")
        prescreening["screened_by"] = user["sub"]

        # EX-10: Compute and store screening validity deadline
        from environment import get_screening_validity_days
        validity_days = get_screening_validity_days()
        valid_until = now_utc + timedelta(days=validity_days)
        prescreening["screening_valid_until"] = valid_until.strftime("%Y-%m-%dT%H:%M:%S")
        prescreening["screening_validity_days"] = validity_days

        db.execute("UPDATE applications SET prescreening_data=?, updated_at=datetime('now'), inputs_updated_at=datetime('now') WHERE id=?",
                   (json.dumps(prescreening, default=str), real_id))

        # SCR-010: Dual-write normalized screening report (non-authoritative)
        try:
            from screening_config import is_abstraction_enabled
            if is_abstraction_enabled():
                from screening_normalizer import normalize_screening_report
                from screening_storage import (
                    ensure_normalized_table, persist_normalized_report,
                    persist_normalization_failure, compute_report_hash,
                )
                ensure_normalized_table(db)
                _src_hash = compute_report_hash(report)
                _norm = normalize_screening_report(report)
                persist_normalized_report(
                    db, app.get("client_id", ""), real_id,
                    _norm, _src_hash,
                )
        except Exception as _norm_exc:
            logger.warning(
                "Normalized screening write failed: app_id=%s client_id=%s error_type=%s",
                real_id, app.get("client_id", ""), type(_norm_exc).__name__,
            )
            try:
                from screening_storage import (
                    ensure_normalized_table, persist_normalization_failure,
                    compute_report_hash,
                )
                ensure_normalized_table(db)
                persist_normalization_failure(
                    db, app.get("client_id", ""), real_id,
                    compute_report_hash(report),
                    type(_norm_exc).__name__,
                )
            except Exception:
                pass  # Do not block onboarding flow

        # EX-09: Recompute risk after screening re-run — screening hits affect risk score
        rr = recompute_risk(db, real_id, "screening_rerun", user=user,
                            log_audit_fn=self.log_audit)
        risk_recomputed = rr.get("recomputed", False)

        db.commit()
        db.close()

        self.log_audit(user, "Screening", app["ref"],
                       f"Full screening run — {report['total_hits']} hit(s), {len(report['overall_flags'])} flag(s)")

        response = dict(report)
        if risk_recomputed:
            response["risk_recomputed"] = True
        response["screening_valid_until"] = prescreening["screening_valid_until"]
        response["screening_validity_days"] = validity_days
        self.success(response)


class SanctionsCheckHandler(BaseHandler):
    """POST /api/screening/sanctions — ad-hoc sanctions/PEP check"""
    def post(self):
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        name = data.get("name", "").strip()
        if not name:
            return self.error("name is required")

        entity_type = data.get("entity_type", "Person")
        nationality = data.get("nationality")
        birth_date = data.get("birth_date")

        result = screen_sumsub_aml(name, birth_date=birth_date, nationality=nationality, entity_type=entity_type)
        self.log_audit(user, "Sanctions Check", name,
                       f"Ad-hoc sanctions check — {'MATCH' if result['matched'] else 'CLEAR'} ({result['source']})")
        self.success(result)


class CompanyLookupHandler(BaseHandler):
    """POST /api/screening/company — ad-hoc company registry lookup"""
    def post(self):
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        company_name = data.get("company_name", "").strip()
        if not company_name:
            return self.error("company_name is required")

        jurisdiction = data.get("jurisdiction")
        result = lookup_opencorporates(company_name, jurisdiction)
        self.log_audit(user, "Company Lookup", company_name,
                       f"Company registry lookup — {'FOUND' if result['found'] else 'NOT FOUND'} ({result['source']})")
        self.success(result)


class IPCheckHandler(BaseHandler):
    """GET /api/screening/ip — check IP geolocation"""
    def get(self):
        user = self.require_auth()
        if not user:
            return

        ip = self.get_argument("ip", self.get_client_ip())
        result = geolocate_ip(ip)
        self.success(result)


class APIStatusHandler(BaseHandler):
    """GET /api/screening/status — check which APIs are live vs simulated"""
    def get(self):
        user = self.require_auth()
        if not user:
            return

        self.success({
            "opensanctions": {
                "configured": bool(OPENSANCTIONS_API_KEY),
                "status": "live" if OPENSANCTIONS_API_KEY else "simulated",
                "description": "Sanctions, PEP, and watchlist screening"
            },
            "opencorporates": {
                "configured": bool(OPENCORPORATES_API_KEY),
                "status": "live" if OPENCORPORATES_API_KEY else "simulated",
                "description": "Company registry verification"
            },
            "ip_geolocation": {
                "configured": True,  # ipapi.co works without key (free tier)
                "status": "live",
                "description": "IP address geolocation and risk assessment"
            },
            "sumsub": {
                "configured": bool(SUMSUB_APP_TOKEN and SUMSUB_SECRET_KEY),
                "status": "live" if (SUMSUB_APP_TOKEN and SUMSUB_SECRET_KEY) else "simulated",
                "description": "KYC identity verification (document + selfie + liveness)"
            },
            "anthropic": {
                "configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
                "status": "configured" if os.environ.get("ANTHROPIC_API_KEY") else "simulated",
                "description": "Claude-backed document verification and optional analysis paths; the live compliance memo approval path remains deterministic"
            },
            "environment": ENVIRONMENT,
        })


# ══════════════════════════════════════════════════════════
# SUMSUB KYC ENDPOINTS
# ══════════════════════════════════════════════════════════

class SumsubApplicantHandler(BaseHandler):
    """POST /api/kyc/applicant — Create a Sumsub applicant for KYC"""
    def post(self):
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        external_user_id = data.get("external_user_id", "").strip()
        if not external_user_id:
            return self.error("external_user_id is required")

        result = sumsub_create_applicant(
            external_user_id=external_user_id,
            first_name=data.get("first_name"),
            last_name=data.get("last_name"),
            email=data.get("email"),
            phone=data.get("phone"),
            dob=data.get("dob"),
            country=data.get("country"),
            level_name=data.get("level_name"),
        )

        # Finding 12: Store applicant→application mapping for deterministic webhook linking
        applicant_id = result.get("applicant_id", "")
        application_id = data.get("application_id", "")
        if applicant_id and application_id:
            db = get_db()
            try:
                db.execute("""
                    INSERT OR IGNORE INTO sumsub_applicant_mappings
                    (application_id, applicant_id, external_user_id, person_name, person_type)
                    VALUES (?, ?, ?, ?, ?)
                """, (application_id, applicant_id, external_user_id,
                      (data.get("first_name", "") + " " + data.get("last_name", "")).strip(),
                      data.get("person_type", "")))

                # Also store applicant_id in prescreening_data so the legacy fallback
                # substring scan in the webhook handler can find it even if the
                # mapping table lookup fails (e.g. pre-migration or missing row).
                # application_id may be a primary-key id (sent by the portal) or a
                # ref string (sent by the back office), so we resolve both.
                app_row = db.execute(
                    "SELECT id, prescreening_data FROM applications WHERE id=? OR ref=?",
                    (application_id, application_id)
                ).fetchone()
                if app_row:
                    pdict = safe_json_loads(app_row["prescreening_data"] or "{}")
                    if "sumsub_applicant_ids" not in pdict:
                        pdict["sumsub_applicant_ids"] = {}
                    pdict["sumsub_applicant_ids"][external_user_id] = applicant_id
                    db.execute(
                        "UPDATE applications SET prescreening_data=? WHERE id=?",
                        (json.dumps(pdict), app_row["id"])
                    )

                db.commit()
            except Exception as e:
                logger.debug(f"Applicant mapping insert: {e}")
            finally:
                db.close()

        # Only write "KYC Applicant Created" audit entry when applicantId is real
        # and non-empty.  Log failure explicitly otherwise.
        if applicant_id:
            self.log_audit(user, "KYC Applicant Created", external_user_id,
                           f"Sumsub applicant created — ID: {applicant_id} ({result.get('source', 'unknown')})")
        else:
            self.log_audit(user, "KYC Applicant Creation Failed", external_user_id,
                           f"Sumsub applicant creation failed — "
                           f"api_status={result.get('api_status', 'unknown')} "
                           f"error={result.get('error', 'no applicant_id returned')}")
        self.success(result)


class SumsubDiagnosticsHandler(BaseHandler):
    """GET /api/admin/sumsub-diagnostics?application_id=... — Per-person Sumsub state"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco"])
        if not user:
            return

        app_id = self.get_query_argument("application_id", "")
        if not app_id:
            return self.error("application_id query parameter required")

        db = get_db()
        try:
            app = db.execute(
                "SELECT id, ref, prescreening_data FROM applications WHERE id=? OR ref=?",
                (app_id, app_id)
            ).fetchone()
            if not app:
                db.close()
                return self.error("Application not found", 404)

            real_id = app["id"]
            prescreening = safe_json_loads(app["prescreening_data"] or "{}")
            screening_report = prescreening.get("screening_report", {})
            sumsub_applicant_ids = prescreening.get("sumsub_applicant_ids", {})

            # Collect applicant mapping rows for this application
            mappings = db.execute(
                "SELECT applicant_id, external_user_id, person_name, person_type, created_at "
                "FROM sumsub_applicant_mappings WHERE application_id=?",
                (real_id,)
            ).fetchall()
            mapping_list = [dict(m) for m in mappings]

            # Collect audit entries for applicant creation for this application
            # Use exact match on target with all known identifiers (app_id + ext_ids)
            ext_ids = [m["external_user_id"] for m in mapping_list]
            all_targets = [real_id] + ext_ids
            placeholders = ",".join("?" for _ in all_targets)
            audit_entries = db.execute(
                "SELECT action, target, detail, created_at FROM audit_log "
                "WHERE action IN ('KYC Applicant Created', 'KYC Applicant Creation Failed') "
                f"AND target IN ({placeholders}) "
                "ORDER BY created_at DESC LIMIT 50",
                tuple(all_targets)
            ).fetchall()

            unique_audits = [dict(a) for a in audit_entries]

            # Build per-person diagnostics
            persons = []
            # From KYC applicants in screening report
            for kyc in screening_report.get("kyc_applicants", []):
                persons.append({
                    "person_name": kyc.get("person_name", ""),
                    "person_type": kyc.get("person_type", ""),
                    "sumsub_applicant_id": kyc.get("applicant_id", ""),
                    "applicant_creation_status": kyc.get("api_status", "unknown"),
                    "source": kyc.get("source", "unknown"),
                })
            # From director screenings
            for ds in screening_report.get("director_screenings", []):
                scr = ds.get("screening", {})
                persons.append({
                    "person_name": ds.get("person_name", ""),
                    "person_type": "director",
                    "screening_api_status": scr.get("api_status", "unknown"),
                    "screening_source": scr.get("source", "unknown"),
                })
            # From UBO screenings
            for us in screening_report.get("ubo_screenings", []):
                scr = us.get("screening", {})
                persons.append({
                    "person_name": us.get("person_name", ""),
                    "person_type": "ubo",
                    "screening_api_status": scr.get("api_status", "unknown"),
                    "screening_source": scr.get("source", "unknown"),
                })

            self.success({
                "application_id": real_id,
                "application_ref": app["ref"],
                "sumsub_applicant_ids": sumsub_applicant_ids,
                "applicant_mappings": mapping_list,
                "audit_entries": unique_audits,
                "persons": persons,
                "screening_mode": screening_report.get("screening_mode", "unknown"),
                "last_screened_at": prescreening.get("last_screened_at", ""),
            })
        finally:
            db.close()


class SumsubAccessTokenHandler(BaseHandler):
    """POST /api/kyc/token — Generate a Sumsub WebSDK access token"""
    def post(self):
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        external_user_id = data.get("external_user_id", "").strip()
        if not external_user_id:
            return self.error("external_user_id is required")

        result = sumsub_generate_access_token(
            external_user_id=external_user_id,
            level_name=data.get("level_name"),
        )
        self.success(result)


class SumsubStatusHandler(BaseHandler):
    """GET /api/kyc/status/:applicant_id — Get verification status"""
    def get(self, applicant_id):
        user = self.require_auth()
        if not user:
            return

        # Ownership check: officers can query any applicant; clients can only query their own.
        user_role = user.get("role", "client")
        if user_role == "client":
            db = get_db()
            try:
                user_id = user.get("sub", user.get("id", ""))
                app = db.execute(
                    "SELECT id FROM applications WHERE client_id = ? AND prescreening_data LIKE ?",
                    (user_id, f"%{applicant_id}%")
                ).fetchone()
                if not app:
                    return self.error("Not authorised to view this applicant", 403)
            finally:
                db.close()

        result = sumsub_get_applicant_status(applicant_id)
        self.success(result)


class SumsubDocumentHandler(BaseHandler):
    """POST /api/kyc/document — Upload a document to Sumsub"""
    def post(self):
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        applicant_id = data.get("applicant_id", "").strip()
        doc_type = data.get("doc_type", "PASSPORT").strip()
        country = data.get("country", "").strip()

        if not applicant_id:
            return self.error("applicant_id is required")

        # Support base64 file data or a reference to an uploaded file
        file_data = data.get("file_data")
        file_name = data.get("file_name", "document.pdf")

        # Security: restrict file_path to uploads directory only (Finding S-15)
        file_path = data.get("file_path")
        if file_path:
            import pathlib
            allowed_dir = pathlib.Path(os.path.join(os.path.dirname(__file__), "uploads")).resolve()
            requested = pathlib.Path(file_path).resolve()
            if not str(requested).startswith(str(allowed_dir)):
                logger.warning(f"SumsubDocumentHandler: blocked path traversal attempt: {file_path}")
                return self.error("file_path must be within the uploads directory", 400)
            file_path = str(requested)

        result = sumsub_add_document(
            applicant_id=applicant_id,
            doc_type=doc_type,
            country=country,
            file_path=file_path,
            file_data=file_data,
            file_name=file_name,
        )

        self.log_audit(user, "KYC Document Upload", applicant_id,
                       f"Sumsub document upload — Type: {doc_type}, Status: {result.get('status')}")
        self.success(result)


class SumsubWebhookHandler(BaseHandler):
    """POST /api/kyc/webhook — Receive Sumsub verification webhooks.

    PR 14 hardening (Rev 3):
      * F-2  Digest algorithm allowlist — when X-App-Access-Sig is absent and
             X-Payload-Digest with X-Payload-Digest-Alg is used, the algorithm
             is passed through to ``sumsub_verify_webhook`` where it is gated
             fail-closed against a known set. Unknown algorithms are rejected.
      * F-7  Legacy substring scan removed. Unmatched deliveries (no row in
             ``sumsub_applicant_mappings``) are routed to the
             ``sumsub_unmatched_webhooks`` dead-letter queue for manual
             triage. The scan could cross-link records whose prescreening_data
             happened to contain the applicant_id as a substring — a silent
             multi-tenancy break.
      * F-8  Applicant ID format validation. Sumsub IDs are hex strings; we
             reject anything outside ``[0-9a-fA-F]{16,64}`` with 400 before
             any DB open, and mask the value in all log lines.
      * Event-type gate. ``applicantReviewed`` is the only event we treat as
             mutating. Other known events are acknowledged with INFO and a
             200. Unknown event types are acknowledged with WARN and a 200.
             Both non-mutating branches short-circuit BEFORE the DB is opened
             — no audit_log row is written for them (see
             utils/sumsub_validation.py for the rationale; tested in T14/T14b).
      * DLQ insert failure returns 503. If we cannot persist an unmatched
             delivery to the DLQ, Sumsub must retry — we never silently drop.
    """

    def post(self):
        # Lazy import to avoid circular dependency at module-import time.
        from utils.sumsub_validation import (
            validate_applicant_id,
            mask_applicant_id,
            SUMSUB_MUTATING_EVENT_TYPES,
            SUMSUB_ACKNOWLEDGED_EVENT_TYPES,
        )

        body = self.request.body

        # Support both signature header formats safely:
        # Primary: X-App-Access-Sig (original Sumsub format)
        # Fallback: X-Payload-Digest (observed on staging — digest-style format)
        _primary_sig = self.request.headers.get("X-App-Access-Sig", "")
        _digest_sig = self.request.headers.get("X-Payload-Digest", "")
        _digest_alg = self.request.headers.get("X-Payload-Digest-Alg", "")

        if _primary_sig:
            signature = _primary_sig
            _sig_source = "X-App-Access-Sig"
        elif _digest_sig:
            signature = _digest_sig
            _sig_source = "X-Payload-Digest"
        else:
            signature = ""
            _sig_source = "none"

        # Staging-safe diagnostic logging — partial values only, never full secrets
        _header_names = list(self.request.headers.keys())
        _sig_present = bool(signature)
        logger.info(
            "Sumsub webhook diagnostic: env=%s body_len=%d "
            "sig_header_present=%s sig_source=%s sig_empty=%s "
            "digest_alg=%s header_names=%s",
            ENVIRONMENT,
            len(body),
            _sig_present,
            _sig_source,
            signature == "",
            _digest_alg or "n/a",
            _header_names,
        )
        if signature:
            logger.info("Sumsub webhook: received sig prefix=%s source=%s", signature[:8], _sig_source)

        # Verify webhook signature — always verify, never skip (Finding S-16).
        # F-2: pass the advertised algorithm through to the verifier, which
        # hard-gates against ALLOWED_DIGEST_ALGS fail-closed.
        if not sumsub_verify_webhook(body, signature, digest_alg=_digest_alg or None):
            logger.warning("Sumsub webhook: Invalid or missing signature")
            return self.error("Invalid signature", 401)

        try:
            payload = json.loads(body)
        except Exception:
            return self.error("Invalid JSON", 400)

        event_type = payload.get("type", "")
        applicant_id = payload.get("applicantId", "")
        external_user_id = payload.get("externalUserId", "")
        review_result = payload.get("reviewResult", {})
        review_answer = review_result.get("reviewAnswer", "")

        # F-8: validate applicant_id format BEFORE any DB open or log line that
        # would include it. Rejects hex-format violations and guards against
        # log-poisoning / SQL-context injection via the identifier.
        if not validate_applicant_id(applicant_id):
            logger.warning(
                "Sumsub webhook: malformed applicant_id rejected (event_type=%s, sig_source=%s)",
                event_type,
                _sig_source,
            )
            return self.error("Invalid applicantId", 400)

        _masked_id = mask_applicant_id(applicant_id)
        logger.info(
            "Sumsub webhook: event=%s applicant=%s answer=%s",
            event_type,
            _masked_id,
            review_answer,
        )

        # ── Event-type gate ──────────────────────────────────────────────
        # Unknown events and known-but-non-mutating events short-circuit here,
        # BEFORE we open the database. No audit_log row is written. The
        # audit trail for these deliveries is this log line. See
        # utils/sumsub_validation.py for the Rev 3 rationale.
        if event_type not in SUMSUB_MUTATING_EVENT_TYPES:
            if event_type in SUMSUB_ACKNOWLEDGED_EVENT_TYPES:
                logger.info(
                    "Sumsub webhook: acknowledged non-mutating event=%s applicant=%s — no DB write",
                    event_type,
                    _masked_id,
                )
            else:
                logger.warning(
                    "Sumsub webhook: unknown event_type=%r applicant=%s — acknowledged, no DB write",
                    event_type,
                    _masked_id,
                )
            self.set_status(200)
            self.write(json.dumps({"status": "ok"}))
            return

        # ── Mutating path (applicantReviewed) ────────────────────────────
        matched_app_ids = set()  # SCR-013: hoisted; post-commit renorm block reads this
        db = get_db()
        try:
            # ── EX-04: Idempotency guard ────────────────────────────────
            # Derive a canonical dedup key from immutable payload fields.
            # Sumsub does not provide a stable unique event ID; the most
            # reliable combination is (applicantId, type, reviewAnswer,
            # createdAtMs). createdAtMs is the millisecond epoch timestamp
            # assigned by Sumsub when the event was created — it is stable
            # across retries of the same event.
            _created_at_ms = str(payload.get("createdAtMs", ""))
            _dedup_input = f"{applicant_id}:{event_type}:{review_answer}:{_created_at_ms}"
            _event_digest = hashlib.sha256(_dedup_input.encode("utf-8")).hexdigest()

            _now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            try:
                db.execute("""
                    INSERT INTO webhook_processed_events
                        (event_digest, event_type, applicant_id, external_user_id, review_answer, received_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (_event_digest, event_type, applicant_id, external_user_id, review_answer, _now_utc))
            except Exception:
                # UNIQUE constraint violation — this event was already processed.
                try:
                    db.rollback()
                except Exception:
                    pass
                logger.info(
                    "Sumsub webhook: duplicate delivery skipped (already processed) "
                    "applicant=%s event=%s digest=%s",
                    _masked_id, event_type, _event_digest[:16],
                )
                self.set_status(200)
                self.write(json.dumps({"status": "already_processed"}))
                return

            kyc_data = json.dumps({
                "sumsub_applicant_id": applicant_id,
                "external_user_id": external_user_id,
                "review_answer": review_answer,
                "rejection_labels": review_result.get("rejectLabels", []),
                "moderation_comment": review_result.get("moderationComment", ""),
                "event_type": event_type,
                "received_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            })

            # Audit log — mutating branch only (Rev 3: audit_log is a
            # state-change record, not a webhook-arrival record).
            db.execute("""
                INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("system", "Sumsub Webhook", "system", f"KYC {event_type}: {review_answer}", applicant_id, kyc_data))

            # Finding 12: Deterministic applicant→application lookup via mapping table.
            # F-7: the legacy substring scan has been removed — unmatched
            # deliveries go to the DLQ for manual triage.
            mapping_lookup_failed = False

            try:
                mappings = db.execute(
                    "SELECT application_id FROM sumsub_applicant_mappings WHERE applicant_id = ? OR external_user_id = ?",
                    (applicant_id, external_user_id)
                ).fetchall()
                for m in mappings:
                    matched_app_ids.add(m["application_id"])
            except Exception as e:
                # Don't silently swallow — route this delivery to the DLQ with
                # a diagnostic resolution_note so an operator can investigate.
                logger.error(
                    "Sumsub webhook: mapping table lookup failed for applicant=%s — routing to DLQ: %s",
                    _masked_id, e,
                )
                mapping_lookup_failed = True

            if not matched_app_ids:
                # ── Dead-letter queue path ──────────────────────────────
                resolution_note = (
                    "auto:mapping_lookup_failed" if mapping_lookup_failed
                    else "auto:no_mapping_found"
                )
                try:
                    db.execute("""
                        INSERT INTO sumsub_unmatched_webhooks
                            (applicant_id, external_user_id, event_type, review_answer,
                             payload, status, resolution_note, received_at)
                        VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                    """, (
                        applicant_id,
                        external_user_id,
                        event_type,
                        review_answer,
                        kyc_data,
                        resolution_note,
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                    ))
                    db.commit()
                    logger.warning(
                        "Sumsub webhook: unmatched delivery queued to DLQ applicant=%s note=%s",
                        _masked_id, resolution_note,
                    )
                    self.set_status(200)
                    self.write(json.dumps({"status": "queued"}))
                    return
                except Exception as dlq_err:
                    # DLQ insert failure must NOT be silently swallowed.
                    # Return 503 so Sumsub retries the delivery.
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    logger.error(
                        "Sumsub webhook: DLQ insert FAILED for applicant=%s — returning 503: %s",
                        _masked_id, dlq_err,
                    )
                    return self.error("Webhook persistence failure", 503)

            # Update matched applications
            for app_id in matched_app_ids:
                try:
                    # app_id from the mapping table may be a ref or an id
                    row = db.execute(
                        "SELECT id, prescreening_data FROM applications WHERE id=? OR ref=?",
                        (app_id, app_id)
                    ).fetchone()
                    if not row:
                        continue
                    pdict = safe_json_loads(row["prescreening_data"] or "{}")
                    if "screening_report" not in pdict:
                        pdict["screening_report"] = {}
                    pdict["screening_report"]["sumsub_webhook"] = safe_json_loads(kyc_data)

                    # If verification failed, add a flag
                    if review_answer == "RED":
                        flags = pdict["screening_report"].get("overall_flags", [])
                        flag_msg = f"Sumsub KYC verification REJECTED for {external_user_id}"
                        if flag_msg not in flags:
                            flags.append(flag_msg)
                        pdict["screening_report"]["overall_flags"] = flags

                    db.execute("UPDATE applications SET prescreening_data=? WHERE id=?",
                              (json.dumps(pdict), row["id"]))
                    logger.info("Sumsub webhook: updated application id=%s applicant=%s",
                                row["id"], _masked_id)
                except Exception as e:
                    logger.error(
                        "Sumsub webhook: failed to update application %s applicant=%s: %s",
                        app_id, _masked_id, e,
                    )

            db.commit()
        finally:
            db.close()

        # SCR-013: Post-commit re-normalization. Helper enforces narrow-except;
        # operational errors are swallowed, programmer errors propagate.
        import sqlite3 as _sqlite3
        try:
            import psycopg2 as _psycopg2
            _renorm_operational_errors = (_sqlite3.Error, _psycopg2.Error)
        except ImportError:
            _renorm_operational_errors = (_sqlite3.Error,)
        try:
            from screening_config import is_abstraction_enabled
            if matched_app_ids and is_abstraction_enabled():
                from screening_storage import webhook_renormalize_from_committed_legacy
                for app_id in matched_app_ids:
                    webhook_renormalize_from_committed_legacy(db, app_id)
        except _renorm_operational_errors as outer_op_err:
            logger.warning(
                "Webhook renorm: outer operational error error_type=%s",
                type(outer_op_err).__name__,
            )

        self.set_status(200)
        self.write(json.dumps({"status": "ok"}))


# ══════════════════════════════════════════════════════════
# MONITORING ENDPOINTS
# ══════════════════════════════════════════════════════════

class MonitoringDashboardHandler(BaseHandler):
    """GET /api/monitoring/dashboard — returns monitoring stats"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        from fixture_filter import fixture_app_exclude_clause

        db = get_db()
        stats = {
            "files_due": 0,
            "docs_expiring": 0,
            "alerts": 0,
            "clients_under_review": 0,
            "high_risk_alerts": [],
            "periodic_review_due": 0
        }

        fx_excl, fx_params = fixture_app_exclude_clause(table_alias="")

        # Count applications pending compliance review (excluding fixtures)
        compliance_review = db.execute(
            f"SELECT COUNT(*) as c FROM applications WHERE status IN ('compliance_review','kyc_submitted') AND {fx_excl}",
            fx_params,
        ).fetchone()["c"]
        stats["clients_under_review"] = compliance_review

        # Count high-risk alerts (excluding fixtures)
        high_risk = db.execute(
            f"SELECT COUNT(*) as c FROM applications WHERE risk_level IN ('HIGH','VERY_HIGH') AND {fx_excl}",
            fx_params,
        ).fetchone()["c"]
        stats["alerts"] = high_risk

        # Get recent high-risk applications for alert summary (excluding fixtures)
        recent_alerts = db.execute(f"""
            SELECT ref, company_name, risk_level, risk_score, created_at FROM applications
            WHERE risk_level IN ('HIGH','VERY_HIGH') AND {fx_excl}
            ORDER BY created_at DESC LIMIT 10
        """, fx_params).fetchall()
        stats["high_risk_alerts"] = [dict(a) for a in recent_alerts]

        db.close()
        self.success(stats)


class MonitoringClientsHandler(BaseHandler):
    """GET /api/monitoring/clients — returns client monitoring status for Kanban board"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        from fixture_filter import fixture_app_exclude_clause

        db = get_db()

        # Get all applications grouped by status/stage (excluding fixtures)
        fx_excl, fx_params = fixture_app_exclude_clause()
        applications = db.execute(f"""
            SELECT a.id, a.ref, a.company_name, a.status, a.risk_level, a.risk_score,
                   a.created_at, u.full_name as assigned_to
            FROM applications a
            LEFT JOIN users u ON a.assigned_to = u.id
            WHERE {fx_excl}
            ORDER BY a.created_at DESC
        """, fx_params).fetchall()

        clients = {}
        for app in applications:
            status = app["status"]
            if status not in clients:
                clients[status] = []
            clients[status].append(dict(app))

        db.close()
        self.success({"clients_by_status": clients})


class MonitoringAlertCreateHandler(BaseHandler):
    """GET/POST /api/monitoring/alerts — List and create monitoring alerts"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        from fixture_filter import fixture_app_id_exclude_clause

        severity = self.get_argument("severity", None)
        alert_type = self.get_argument("type", None)
        status_filter = self.get_argument("status", None)
        client_id = self.get_argument("client", None)

        db = get_db()
        # Exclude fixture-linked alerts by default (application_id NOT LIKE 'f1xed%')
        fx_excl, fx_params = fixture_app_id_exclude_clause("application_id")
        query = f"SELECT * FROM monitoring_alerts WHERE {fx_excl}"
        params = list(fx_params)

        if severity:
            query += " AND severity = ?"
            params.append(severity)
        if alert_type:
            query += " AND alert_type = ?"
            params.append(alert_type)
        if status_filter:
            query += " AND status = ?"
            params.append(status_filter)
        if client_id:
            query += " AND application_id = ?"
            params.append(client_id)

        query += " ORDER BY created_at DESC"
        alerts = db.execute(query, params).fetchall()

        result = [dict(a) for a in alerts]
        db.close()
        self.success({"alerts": result, "total": len(result)})

    def post(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()
        db = get_db()

        # Insert the alert into monitoring_alerts
        alert_type = data.get("alert_type", data.get("type", "Manual"))
        severity = data.get("severity", "Medium")
        client_name = data.get("client_name", "")
        application_id = data.get("application_id")
        summary = data.get("summary", data.get("message", ""))
        detected_by = data.get("detected_by", user.get("name", "Officer"))
        source_reference = data.get("source_reference", "Manual entry")
        ai_recommendation = data.get("ai_recommendation", "")

        db.execute("""
            INSERT INTO monitoring_alerts
                (application_id, client_name, alert_type, severity, detected_by, summary, source_reference, ai_recommendation, status)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (application_id, client_name, alert_type, severity, detected_by, summary, source_reference, ai_recommendation, "open"))

        # Create notification for relevant users
        title = data.get("title", f"Monitoring Alert: {alert_type}")
        alert_users = db.execute("SELECT id FROM users WHERE role IN ('sco','co','admin')").fetchall()
        for u in alert_users:
            db.execute("INSERT INTO notifications (user_id, title, message) VALUES (?,?,?)",
                      (u["id"], title, summary))

        db.commit()
        db.close()
        self.log_audit(user, "Alert", "Monitoring", f"Alert created: {alert_type} — {severity}")
        self.success({"status": "created"}, 201)


# ══════════════════════════════════════════════════════════
# COMPLIANCE MEMO ENDPOINT (Step 5)
# ══════════════════════════════════════════════════════════

class ComplianceMemoHandler(BaseHandler):
    """POST /api/applications/:id/memo — Generate compliance memo from application data"""
    def post(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return

        if not self.check_rate_limit("memo", max_attempts=10, window_seconds=60):
            return

        # P0-3: Check if Agent 5 (compliance memo) is enabled before executing
        db = get_db()
        agent5 = db.execute("SELECT enabled FROM ai_agents WHERE agent_number=5").fetchone()
        if agent5 and not agent5["enabled"]:
            db.close()
            self.log_audit(user, "Agent Skipped", "Agent 5", "Compliance memo generation skipped — agent disabled")
            self.success({
                "status": "skipped",
                "message": "Compliance memo agent is currently disabled",
                "requires_review": True
            })
            return

        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        real_id = app["id"]

        # Fetch related data — C-02: decrypt PII fields on read
        directors = [decrypt_pii_fields(dict(d), PII_FIELDS_DIRECTORS) for d in db.execute("SELECT * FROM directors WHERE application_id=?", (real_id,)).fetchall()]
        ubos = [decrypt_pii_fields(dict(u), PII_FIELDS_UBOS) for u in db.execute("SELECT * FROM ubos WHERE application_id=?", (real_id,)).fetchall()]
        documents = [dict(d) for d in db.execute("SELECT * FROM documents WHERE application_id=?", (real_id,)).fetchall()]

        # Enrich app with prescreening fields for memo_handler
        # prescreening_data is a JSON column; memo_handler expects source_of_funds and expected_volume as top-level keys
        app = dict(app)
        ps_raw = app.get("prescreening_data") or "{}"
        ps = ps_raw if isinstance(ps_raw, dict) else json.loads(ps_raw)
        ps = merge_prescreening_sources(ps, load_saved_session_prescreening(db, app))
        app["prescreening_data"] = ps
        sof = ps.get("source_of_funds", "")
        if not sof:
            sof_parts = []
            if ps.get("source_of_funds_initial_type"):
                sof_parts.append("Initial: " + ps["source_of_funds_initial_type"])
            if ps.get("source_of_funds_initial_detail"):
                sof_parts.append(ps["source_of_funds_initial_detail"])
            if ps.get("source_of_funds_ongoing_type"):
                sof_parts.append("Ongoing: " + ps["source_of_funds_ongoing_type"])
            if ps.get("source_of_funds_ongoing_detail"):
                sof_parts.append(ps["source_of_funds_ongoing_detail"])
            sof = "; ".join(sof_parts)
        app["source_of_funds"] = sof
        app["expected_volume"] = ps.get("expected_volume") or ps.get("monthly_volume", "")
        # Enrich with operating countries and incorporation date for memo narrative
        app["operating_countries"] = ps.get("operating_countries") or ps.get("countries_of_operation") or ps.get("target_markets") or ""
        app["incorporation_date"] = ps.get("incorporation_date") or ""
        app["business_activity"] = ps.get("business_activity") or ps.get("business_description") or ""

        # Build compliance memo (pure computation — extracted to memo_handler.py)
        memo, rule_engine_result, supervisor_result, validation_result = build_compliance_memo(app, directors, ubos, documents)
        rule_violations = rule_engine_result.get("violations", [])

        # Store memo in compliance_memos table
        rule_violations_json = json.dumps(rule_violations) if rule_violations else None
        memo_json = json.dumps(memo)
        try:
            db.execute(
                "INSERT INTO compliance_memos (application_id, memo_data, generated_by, ai_recommendation, review_status, quality_score, validation_status, supervisor_status, supervisor_summary, rule_violations, memo_version) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (real_id, memo_json, user.get("sub", ""), memo["metadata"]["approval_recommendation"], "draft",
                 validation_result["quality_score"], validation_result["validation_status"],
                 supervisor_result["verdict"], supervisor_result["recommendation"], rule_violations_json,
                 memo.get("metadata", {}).get("model_version", "v1.0"))
            )
        except Exception as e:
            # Fallback if rule_violations column doesn't exist yet
            logger.warning(f"Memo insert with rule_violations failed (column may not exist): {e}")
            try:
                db.execute(
                    "INSERT INTO compliance_memos (application_id, memo_data, generated_by, ai_recommendation, review_status, quality_score, validation_status, supervisor_status, supervisor_summary) VALUES (?,?,?,?,?,?,?,?,?)",
                    (real_id, memo_json, user.get("sub", ""), memo["metadata"]["approval_recommendation"], "draft",
                     validation_result["quality_score"], validation_result["validation_status"],
                     supervisor_result["verdict"], supervisor_result["recommendation"])
                )
            except Exception as e2:
                logger.warning(f"Memo insert with supervisor columns failed: {e2}")
                try:
                    db.execute(
                        "INSERT INTO compliance_memos (application_id, memo_data, generated_by, ai_recommendation, review_status) VALUES (?,?,?,?,?)",
                        (real_id, memo_json, user.get("sub", ""), memo["metadata"]["approval_recommendation"], "draft")
                    )
                except Exception as e3:
                    logger.error(f"All memo insert attempts failed for application {real_id}: {e3}", exc_info=True)

        db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                   (user.get("sub",""), user.get("name",""), user.get("role",""), "Generate Memo", app["ref"],
                    "Compliance memo generated for " + app["company_name"]
                    + " | Supervisor: " + supervisor_result["verdict"]
                    + " | Quality: " + str(validation_result["quality_score"]) + "/10"
                    + " | Rule Engine: " + rule_engine_result["engine_status"]
                    + (" | BLOCKED" if memo["metadata"].get("blocked") else ""),
                    self.get_client_ip()))

        # ── Priority B / Workstream C: audit-log the EDD routing decision ──
        try:
            _routing = memo.get("metadata", {}).get("edd_routing")
            if _routing:
                _emit_edd_routing_audit(db, user, app["ref"], _routing, self.get_client_ip())
        except Exception as _re:
            logger.error("Failed to emit EDD routing audit row for %s: %s", app["ref"], _re)

        # ── Priority B.2 / Workstream A: Actuate EDD routing ──
        # When policy says route=edd, this is where the policy decision
        # becomes workflow reality: an EDD case is upserted and the
        # application status flips to edd_required. Idempotent on
        # re-generation.
        try:
            _routing_actuate = memo.get("metadata", {}).get("edd_routing")
            if _routing_actuate and _routing_actuate.get("route") == "edd":
                _actuation = _actuate_edd_routing(
                    db, app, _routing_actuate, supervisor_result, user,
                    client_ip=self.get_client_ip(),
                )
                memo.setdefault("metadata", {})["edd_routing_actuation"] = _actuation
        except Exception as _ae:
            logger.error(
                "Failed to actuate EDD routing for %s: %s",
                app.get("ref"), _ae, exc_info=True,
            )

        db.commit()
        db.close()

        self.success(memo)


class SupervisorRunHandler(BaseHandler):
    """POST /api/applications/:id/supervisor/run — Trigger full supervisor pipeline for an application"""
    async def post(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        if not SUPERVISOR_AVAILABLE:
            return self.error("Supervisor framework not available", 503)

        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)
        db.close()

        data = self.get_json()
        trigger_type = data.get("trigger_type", "onboarding") if data else "onboarding"

        trigger_source = f"backoffice:{user.get('sub', user.get('id', 'unknown'))}"
        try:
            supervisor = get_supervisor()
            result = await asyncio.wait_for(
                supervisor.run_pipeline(
                    application_id=app["id"],
                    trigger_type=__import__("supervisor.schemas", fromlist=["TriggerType"]).TriggerType(trigger_type),
                    context_data={"app_ref": app["ref"], "company_name": app["company_name"]},
                    trigger_source=trigger_source,
                ),
                timeout=120.0,
            )
        except asyncio.TimeoutError:
            logger.error("Supervisor pipeline timed out after 120s for app %s", app_id)
            return self.error("Pipeline execution timed out after 120 seconds", 504)
        except Exception as e:
            import traceback
            logger.error("Supervisor pipeline execution failed for app %s: %s (%s)\n%s",
                         app_id, e, type(e).__name__, traceback.format_exc())
            return self.error(f"Pipeline execution failed: {type(e).__name__}: {str(e)}", 500)

        # Persist to database (survives restarts)
        try:
            from supervisor.api import persist_pipeline_result
            persist_pipeline_result(result, trigger_type=trigger_type, trigger_source=trigger_source)
        except Exception as persist_err:
            logger.error("Failed to persist pipeline result: %s", persist_err)

        # Serialize and return results — separate try/except for clearer diagnostics
        try:
            self.success({
                "pipeline_id": result.pipeline_id,
                "status": result.status,
                "started_at": result.started_at,
                "completed_at": result.completed_at,
                "agent_count": len(result.agent_outputs),
                "failed_agents": len(result.failed_agents),
                "contradictions": len(result.contradictions),
                "rules_triggered": sum(1 for r in result.rule_evaluations if r.triggered),
                "requires_human_review": result.requires_human_review,
                "review_reasons": result.review_reasons,
                "blocking_issues": result.blocking_issues,
                "case_aggregate": result.case_aggregate.model_dump() if result.case_aggregate else None,
                "contradictions_detail": [c.model_dump() for c in result.contradictions],
                "triggered_rules": [r.model_dump() for r in result.rule_evaluations if r.triggered],
                "escalations": [e.model_dump() for e in result.escalations],
                "agent_results": [
                    {
                        "agent_type": at.value,
                        "agent_name": out.agent_name,
                        "status": out.status.value,
                        "confidence": out.confidence_score,
                        "findings_count": len(out.findings),
                        "issues_count": len(out.detected_issues),
                        "escalation_flag": out.escalation_flag,
                        "recommendation": out.recommendation,
                    }
                    for at, out in result.agent_outputs.items()
                ],
                "failed_agent_details": result.failed_agents,
            })
        except Exception as ser_err:
            import traceback
            logger.error("Supervisor result serialization failed for app %s: %s (%s)\n%s",
                         app_id, ser_err, type(ser_err).__name__, traceback.format_exc())
            # Return minimal result without complex serialization
            self.success({
                "pipeline_id": result.pipeline_id,
                "status": result.status,
                "started_at": result.started_at,
                "completed_at": result.completed_at,
                "agent_count": len(result.agent_outputs),
                "failed_agents": len(result.failed_agents),
                "requires_human_review": result.requires_human_review,
                "review_reasons": result.review_reasons,
                "blocking_issues": result.blocking_issues,
                "_serialization_error": f"{type(ser_err).__name__}: {str(ser_err)}",
            })


class SupervisorResultHandler(BaseHandler):
    """GET /api/applications/:id/supervisor/result — Get latest pipeline result"""
    def get(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return

        if not SUPERVISOR_AVAILABLE:
            return self.error("Supervisor framework not available", 503)

        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)
        db.close()

        # Return from memory cache first, then fall back to database
        try:
            from supervisor.api import _pipeline_cache, load_latest_pipeline_result
            # 1. Check in-memory cache (fast path)
            latest = None
            for pid, result in _pipeline_cache.items():
                if result.application_id == app["id"]:
                    if latest is None or (result.completed_at or "") > (latest.completed_at or ""):
                        latest = result
            if latest:
                self.success(latest.to_dict())
                return

            # 2. Fall back to database (survives restarts)
            db_result = load_latest_pipeline_result(app["id"])
            if db_result:
                self.success(db_result)
            else:
                self.success({"status": "no_pipeline_run", "message": "No supervisor pipeline has been run for this application."})
        except Exception as e:
            return self.error(f"Failed to fetch result: {str(e)}", 500)


class MemoValidateHandler(BaseHandler):
    """POST /api/applications/:id/memo/validate — Run validation engine on stored memo"""
    def post(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return

        db = get_db()
        # Fetch latest memo for this application
        memo_row = db.execute(
            "SELECT * FROM compliance_memos WHERE application_id = ? OR application_id = (SELECT id FROM applications WHERE ref = ?) ORDER BY created_at DESC LIMIT 1",
            (app_id, app_id)
        ).fetchone()

        if not memo_row:
            db.close()
            return self.error("No compliance memo found for this application. Generate a memo first.", 404)

        try:
            memo_data = safe_json_loads(memo_row["memo_data"])
        except (json.JSONDecodeError, TypeError):
            db.close()
            return self.error("Memo data is corrupt or unreadable.", 500)

        # Run validation engine
        validation = validate_compliance_memo(memo_data)

        # Store results
        try:
            db.execute(
                "UPDATE compliance_memos SET quality_score = ?, validation_status = ?, validation_issues = ?, validation_run_at = ? WHERE id = ?",
                (validation["quality_score"], validation["validation_status"], json.dumps(validation["issues"]), validation["validated_at"], memo_row["id"])
            )
            db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                       (user.get("sub",""), user.get("name",""), user.get("role",""), "Validate Memo", app_id, f"Memo validation: {validation['validation_status']} (score: {validation['quality_score']}/10)", self.get_client_ip()))
            db.commit()
        except Exception as e:
            logger.error(f"Failed to store memo validation results for {app_id}: {e}", exc_info=True)
        db.close()

        self.success(validation)


# ── EX-11: Officer sign-off validation & audit helpers ──

_VALID_SIGNOFF_SCOPES = {"decision", "override", "memo"}


def _validate_officer_signoff(signoff, expected_scope):
    """Validate the officer_signoff object in a request payload.

    Returns an error message string if invalid, or None if valid.
    Fail-closed: missing or malformed sign-off is rejected.
    """
    if signoff is None:
        return (
            "officer_signoff is required. Officers must acknowledge AI-generated advisory "
            "outputs before this action can proceed."
        )
    if not isinstance(signoff, dict):
        return "officer_signoff must be an object with acknowledged, scope, and source_context fields."

    acknowledged = signoff.get("acknowledged")
    if acknowledged is not True:
        return (
            "officer_signoff.acknowledged must be true. Officers must confirm they have "
            "reviewed the AI-generated advisory content."
        )

    scope = signoff.get("scope", "")
    if scope not in _VALID_SIGNOFF_SCOPES:
        return (
            f"officer_signoff.scope must be one of: {', '.join(sorted(_VALID_SIGNOFF_SCOPES))}. "
            f"Received: '{scope}'."
        )
    if scope != expected_scope:
        return (
            f"officer_signoff.scope mismatch: expected '{expected_scope}', received '{scope}'."
        )

    source_context = signoff.get("source_context", "")
    if source_context != "ai_advisory":
        return "officer_signoff.source_context must be 'ai_advisory'."

    return None


def _persist_signoff_audit(db, user, target_ref, scope, signoff_obj, ip_address, user_agent):
    """Persist officer sign-off event to the audit_log table.

    Records server-side context (IP, user agent, timestamp) independently
    of any client-side audit log. This is the authoritative compliance record.
    """
    detail = json.dumps({
        "signoff_acknowledged": signoff_obj.get("acknowledged", False),
        "signoff_scope": scope,
        "source_context": "ai_advisory",
        "user_agent": user_agent,
    }, default=str)

    db.execute(
        "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) "
        "VALUES (?,?,?,?,?,?,?)",
        (user.get("sub", ""), user.get("name", ""), user.get("role", ""),
         f"Officer Sign-Off ({scope})", target_ref, detail, ip_address)
    )


class MemoApproveHandler(BaseHandler):
    """POST /api/applications/:id/memo/approve — Approve memo (requires validation pass)"""
    def post(self, app_id):
        user = self.require_auth(roles=["admin", "sco"])
        if not user:
            return

        db = get_db()
        memo_row = db.execute(
            "SELECT * FROM compliance_memos WHERE application_id = ? OR application_id = (SELECT id FROM applications WHERE ref = ?) ORDER BY created_at DESC LIMIT 1",
            (app_id, app_id)
        ).fetchone()

        if not memo_row:
            db.close()
            return self.error("No compliance memo found.", 404)

        # ── EX-11: Backend enforcement of officer sign-off for memo approval ──
        body = {}
        try:
            body = json.loads(self.request.body or b"{}")
        except (json.JSONDecodeError, TypeError):
            pass
        signoff_error = _validate_officer_signoff(body.get("officer_signoff"), "memo")
        if signoff_error:
            db.close()
            return self.error(signoff_error, 400)

        # ── SERVER-SIDE 5-GATE APPROVAL ENFORCEMENT ──
        # Gate 1: Check if memo is blocked by rule engine
        is_blocked = memo_row.get("blocked") or False
        block_reason = memo_row.get("block_reason") or ""
        if is_blocked:
            db.close()
            return self.error(f"Cannot approve blocked memo. Block reason: {block_reason}", 400)

        # Gate 2: Check validation status
        val_status = memo_row.get("validation_status") or "pending"
        if val_status == "pass":
            pass  # Standard approval — no additional requirements
        elif val_status == "pass_with_fixes":
            # Senior-approval-with-findings policy (EX-06):
            # Only admin or SCO may approve a memo that passed with outstanding findings.
            # A documented reason is mandatory.
            approver_role = user.get("role", "")
            if approver_role not in ("admin", "sco"):
                db.close()
                return self.error(
                    "Cannot approve memo with validation status 'pass_with_fixes'. "
                    "Only admin or Senior Compliance Officer (SCO) may approve memos with outstanding findings.",
                    403
                )
            # body parsed at EX-11 sign-off validation gate (before Gate 1)
            approval_reason = (body.get("approval_reason") or "").strip()
            if not approval_reason:
                db.close()
                return self.error(
                    "approval_reason is required when approving a memo with validation status 'pass_with_fixes'. "
                    "Provide a documented reason for approving despite outstanding findings.",
                    400
                )
        else:
            db.close()
            return self.error(
                f"Cannot approve memo with validation status '{val_status}'. "
                "Validation must be PASS before memo approval.",
                400
            )

        # Gate 3: Check supervisor verdict from memo content
        # BUGFIX: column is memo_data, not memo_content
        memo_data_raw = memo_row.get("memo_data") or "{}"
        try:
            memo_data = safe_json_loads(memo_data_raw)
        except (json.JSONDecodeError, TypeError):
            memo_data = {}
        metadata = memo_data.get("metadata", {})
        # Gate 3a: Reject fallback memos — AI pipeline must have succeeded
        if metadata.get("is_fallback") is True:
            db.close()
            return self.error(
                "Cannot approve a fallback memo. AI pipeline was unavailable when this memo was generated. "
                "Re-generate the memo with a working AI pipeline before approval.", 400)

        supervisor_result = memo_data.get("supervisor") or metadata.get("supervisor", {})
        supervisor_verdict = supervisor_result.get("verdict", "")
        can_approve = supervisor_result.get("can_approve", False)  # Default to False (fail-closed)
        requires_sco = supervisor_result.get("requires_sco_review", False)

        # ── Priority B / Workstream B: mandatory_escalation gate ──
        # The supervisor is the single authoritative verdict; when it
        # has raised mandatory_escalation, the approval API MUST refuse
        # regardless of CONSISTENT verdict. This protects against UI
        # paths that surface only the verdict string and against
        # legacy embedded memo-only signals.
        if supervisor_result.get("mandatory_escalation"):
            db.close()
            reasons = supervisor_result.get("mandatory_escalation_reasons") or []
            return self.error(
                "Cannot approve memo: supervisor mandatory_escalation is set "
                "(reasons: " + ", ".join(reasons[:6]) + "). "
                "Case must be routed to EDD / senior review before approval.",
                400
            )

        # ── Priority B / Workstream C: server-side EDD routing gate ──
        # If the deterministic routing policy says EDD and the
        # application status is not already on the EDD path, the
        # standard memo-approval API must refuse. The application can
        # only be approved through the EDD-completion flow.
        edd_routing = metadata.get("edd_routing") or {}
        if edd_routing.get("route") == "edd":
            app_row = db.execute(
                "SELECT status FROM applications WHERE id = ? OR ref = ?",
                (app_id, app_id)
            ).fetchone()
            current_status = (app_row["status"] if app_row else "") or ""
            if current_status not in ("edd_required", "edd_approved"):
                db.close()
                return self.error(
                    "Cannot approve memo: deterministic EDD routing policy ("
                    + str(edd_routing.get("policy_version", "")) + ") routes this "
                    "case to EDD (triggers: "
                    + ", ".join(edd_routing.get("triggers", [])[:6])
                    + "). Application status is '" + current_status
                    + "'. Route the case via /api/edd/cases or set status to "
                    "'edd_required' before memo approval.",
                    400
                )

        supervisor_warnings_approval = False
        if supervisor_verdict == "CONSISTENT" and can_approve:
            pass  # Standard approval — no additional requirements
        elif supervisor_verdict == "CONSISTENT_WITH_WARNINGS" and can_approve:
            # Supervisor-warnings approval policy (EX-06 B2):
            # Only admin or SCO may approve a memo whose supervisor flagged warnings.
            # A documented reason is mandatory.
            approver_role = user.get("role", "")
            if approver_role not in ("admin", "sco"):
                db.close()
                return self.error(
                    "Cannot approve memo with supervisor verdict 'CONSISTENT_WITH_WARNINGS'. "
                    "Only admin or Senior Compliance Officer (SCO) may approve memos with supervisor warnings.",
                    403
                )
            # Parse approval_reason if not already parsed by Gate 2.
            # When val_status == "pass_with_fixes", Gate 2 has already validated
            # and set approval_reason to a non-empty string (empty is rejected).
            # body parsed at EX-11 sign-off validation gate (before Gate 1)
            if val_status != "pass_with_fixes":
                approval_reason = (body.get("approval_reason") or "").strip()
            if not approval_reason:
                db.close()
                return self.error(
                    "approval_reason is required when approving a memo with supervisor verdict 'CONSISTENT_WITH_WARNINGS'. "
                    "Provide a documented reason for approving despite supervisor warnings.",
                    400
                )
            supervisor_warnings_approval = True
        else:
            db.close()
            return self.error(
                f"Cannot approve memo with supervisor verdict '{supervisor_verdict or 'pending'}'. "
                "Supervisor verdict must be CONSISTENT before memo approval.",
                400
            )

        # Gate 4: SCO review enforcement — if requires_sco_review, only SCO or admin can approve
        if requires_sco and user.get("role") not in ["sco", "admin"]:
            db.close()
            return self.error("This memo requires Senior Compliance Officer review before approval.", 403)

        now_ts = datetime.now().isoformat()
        needs_reason = val_status == "pass_with_fixes" or supervisor_warnings_approval
        try:
            if needs_reason:
                # Store approval with reason — validation_status and supervisor verdict stay unchanged
                db.execute(
                    "UPDATE compliance_memos SET review_status = 'approved', approved_by = ?, approved_at = ?, reviewed_by = ?, approval_reason = ? WHERE id = ?",
                    (user.get("sub", ""), now_ts, user.get("sub", ""), approval_reason, memo_row["id"])
                )
                # Build context-aware audit detail
                context_parts = []
                if val_status == "pass_with_fixes":
                    context_parts.append("outstanding findings")
                if supervisor_warnings_approval:
                    context_parts.append("supervisor warnings")
                context_desc = " and ".join(context_parts)
                audit_detail = (
                    f"Compliance memo approved with {context_desc} by {user.get('name', 'Unknown')} "
                    f"(role: {user.get('role', 'unknown')}). "
                )
                if val_status == "pass_with_fixes":
                    audit_detail += f"Validation status: pass_with_fixes. "
                if supervisor_warnings_approval:
                    audit_detail += f"Supervisor verdict: CONSISTENT_WITH_WARNINGS. "
                audit_detail += f"Approval reason: {approval_reason}"
            else:
                db.execute(
                    "UPDATE compliance_memos SET review_status = 'approved', approved_by = ?, approved_at = ?, reviewed_by = ? WHERE id = ?",
                    (user.get("sub", ""), now_ts, user.get("sub", ""), memo_row["id"])
                )
                audit_detail = f"Compliance memo approved by {user.get('name', 'Unknown')}"

            db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                       (user.get("sub",""), user.get("name",""), user.get("role",""), "Approve Memo", app_id, audit_detail, self.get_client_ip()))

            # ── EX-11: Persist officer sign-off audit record ──
            _persist_signoff_audit(db, user, app_id, "memo",
                                   body.get("officer_signoff", {}),
                                   self.get_client_ip(),
                                   self.request.headers.get("User-Agent", ""))

            db.commit()
        except Exception as e:
            logger.error(f"Failed to store memo approval for {app_id}: {e}", exc_info=True)
        db.close()

        response = {"status": "approved", "approved_by": user.get("name", ""), "approved_at": now_ts}
        if val_status == "pass_with_fixes":
            response["validation_status"] = "pass_with_fixes"
            response["approval_reason"] = approval_reason
            response["senior_approval"] = True
        if supervisor_warnings_approval:
            response["supervisor_verdict"] = "CONSISTENT_WITH_WARNINGS"
            response["approval_reason"] = approval_reason
            response["supervisor_warnings_approval"] = True
        self.success(response)


class MemoValidationResultsHandler(BaseHandler):
    """GET /api/applications/:id/memo/validation — Fetch latest validation results"""
    def get(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return

        db = get_db()
        memo_row = db.execute(
            "SELECT quality_score, validation_status, validation_issues, validation_run_at, review_status, approved_by, approved_at, memo_version FROM compliance_memos WHERE application_id = ? OR application_id = (SELECT id FROM applications WHERE ref = ?) ORDER BY created_at DESC LIMIT 1",
            (app_id, app_id)
        ).fetchone()
        db.close()

        if not memo_row:
            return self.error("No memo found.", 404)

        try:
            issues = safe_json_loads(memo_row["validation_issues"])
        except (json.JSONDecodeError, TypeError):
            issues = []

        self.success({
            "quality_score": memo_row["quality_score"],
            "validation_status": memo_row["validation_status"] or "pending",
            "issues": issues,
            "validated_at": memo_row["validation_run_at"],
            "review_status": memo_row["review_status"],
            "approved_by": memo_row["approved_by"],
            "approved_at": memo_row["approved_at"],
            "memo_version": memo_row["memo_version"]
        })


class MemoPDFDownloadHandler(BaseHandler):
    """GET /api/applications/:id/memo/pdf — Generate and download compliance memo as PDF"""
    def get(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return

        if not HAS_PDF_GENERATOR:
            return self.error("PDF generation not available. Install weasyprint.", 503)

        db = get_db()
        # Fetch application
        app = db.execute(
            "SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)
        ).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        real_id = app["id"]

        # Fetch latest memo
        memo_row = db.execute(
            "SELECT * FROM compliance_memos WHERE application_id = ? ORDER BY created_at DESC LIMIT 1",
            (real_id,)
        ).fetchone()
        if not memo_row:
            db.close()
            return self.error("No compliance memo found. Generate a memo first.", 404)

        # Parse memo data
        try:
            memo_data = safe_json_loads(memo_row["memo_data"])
        except (json.JSONDecodeError, TypeError):
            db.close()
            return self.error("Memo data is corrupt or unparseable.", 500)

        # Build validation/supervisor context from memo metadata
        metadata = memo_data.get("metadata", {})
        validation_result = {
            "validation_status": memo_row.get("validation_status") or metadata.get("validation_status", "pending"),
            "quality_score": memo_row.get("quality_score") or metadata.get("quality_score", 0),
        }
        stored_supervisor = memo_data.get("supervisor") or metadata.get("supervisor", {})
        supervisor_result = {
            "verdict": memo_row.get("supervisor_status") or stored_supervisor.get("verdict", "N/A"),
        }

        approved_by = memo_row.get("approved_by")
        approved_at = memo_row.get("approved_at")

        # If approved_by is user ID, try to resolve to name
        if approved_by:
            approver = db.execute("SELECT email FROM users WHERE id = ?", (approved_by,)).fetchone()
            if approver:
                approved_by = approver["email"]

        # Generate PDF
        try:
            pdf_bytes = generate_memo_pdf(
                memo_data=memo_data,
                application=dict(app),
                validation_result=validation_result,
                supervisor_result=supervisor_result,
                approved_by=approved_by,
                approved_at=approved_at,
            )
        except Exception as e:
            logger.error("PDF generation failed for %s: %s", app_id, str(e))
            db.close()
            return self.error(f"PDF generation failed: {str(e)}", 500)

        # Update pdf_generated_at timestamp
        try:
            db.execute(
                "UPDATE compliance_memos SET pdf_generated_at = ? WHERE id = ?",
                (datetime.now().isoformat(), memo_row["id"])
            )
            db.execute(
                "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                (user.get("sub",""), user.get("name",""), user.get("role",""), "Download Memo PDF", app["ref"],
                 f"PDF generated for {app['company_name']} memo", self.get_client_ip())
            )
            db.commit()
        except Exception as e:
            logger.error(f"Failed to store PDF generation audit for {app_id}: {e}", exc_info=True)
        db.close()

        # Return PDF as binary download
        safe_ref = re.sub(r'[^a-zA-Z0-9_-]', '_', app.get("ref", "memo"))
        filename = f"compliance_memo_{safe_ref}.pdf"
        self.set_header("Content-Type", "application/pdf")
        self.set_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.set_header("Content-Length", str(len(pdf_bytes)))
        self.set_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.write(pdf_bytes)


class MemoSupervisorHandler(BaseHandler):
    """POST /api/applications/:id/memo/supervisor — Run supervisor on stored memo"""
    def post(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return

        db = get_db()
        memo_row = db.execute(
            "SELECT * FROM compliance_memos WHERE application_id = ? OR application_id = (SELECT id FROM applications WHERE ref = ?) ORDER BY created_at DESC LIMIT 1",
            (app_id, app_id)
        ).fetchone()

        if not memo_row:
            db.close()
            return self.error("No compliance memo found for this application.", 404)

        try:
            memo_data = safe_json_loads(memo_row["memo_data"])
        except (json.JSONDecodeError, TypeError):
            db.close()
            return self.error("Memo data is corrupt or unreadable.", 500)

        # Run memo supervisor
        supervisor_result = run_memo_supervisor(memo_data)

        # Update memo with supervisor results
        _persist_ok = False
        try:
            memo_data["supervisor"] = supervisor_result
            memo_data["metadata"]["supervisor_status"] = supervisor_result["verdict"]
            memo_data["metadata"]["supervisor_confidence"] = supervisor_result["supervisor_confidence"]
            # ── Priority B / Workstream C: re-evaluate EDD routing ──
            # The supervisor verdict (and mandatory_escalation) may
            # have changed; the routing decision must reflect that.
            try:
                _contract = (memo_data.get("metadata") or {}).get("agent5_input_contract") or {}
                _facts = dict(_contract)
                _facts["supervisor_mandatory_escalation"] = bool(
                    supervisor_result.get("mandatory_escalation", False)
                )
                _routing = _evaluate_edd_routing(_facts)
                memo_data["metadata"]["edd_routing"] = _routing
                _emit_edd_routing_audit(db, user, app_id, _routing, self.get_client_ip())
                # ── Priority B.2 / Workstream A: Actuate EDD routing ──
                # Re-running the supervisor may surface mandatory_escalation
                # that the original memo generation missed; if the
                # re-evaluated route is edd, actuate it here too.
                if _routing.get("route") == "edd":
                    try:
                        _app_for_actuate = db.execute(
                            "SELECT * FROM applications WHERE id = ? OR ref = ?",
                            (app_id, app_id),
                        ).fetchone()
                        _actuation = _actuate_edd_routing(
                            db, _app_for_actuate, _routing,
                            supervisor_result, user,
                            client_ip=self.get_client_ip(),
                        )
                        memo_data["metadata"]["edd_routing_actuation"] = _actuation
                    except Exception as _ae2:
                        logger.error(
                            "Failed to actuate EDD routing in supervisor run for %s: %s",
                            app_id, _ae2, exc_info=True,
                        )
            except Exception as _re:
                logger.error("Failed to re-evaluate EDD routing for %s: %s", app_id, _re)
            db.execute(
                "UPDATE compliance_memos SET memo_data = ?, supervisor_status = ?, supervisor_summary = ? WHERE id = ?",
                (json.dumps(memo_data), supervisor_result["verdict"], supervisor_result["recommendation"], memo_row["id"])
            )
            db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                       (user.get("sub",""), user.get("name",""), user.get("role",""), "Run Memo Supervisor", app_id,
                        "Supervisor verdict: " + supervisor_result["verdict"] + " | Contradictions: " + str(supervisor_result["contradiction_count"]),
                        self.get_client_ip()))

            # ── Record normalized supervisor decision record ──
            try:
                app_row = db.execute(
                    "SELECT * FROM applications WHERE id = ? OR ref = ?",
                    (app_id, app_id)
                ).fetchone()
                if app_row:
                    sup_record = build_from_supervisor_verdict(
                        app=dict(app_row),
                        supervisor_result=supervisor_result,
                        user=user,
                    )
                    save_decision_record(db, sup_record)
            except Exception as rec_err:
                logger.error("Failed to record supervisor decision record for %s: %s", app_id, rec_err)

            # ── Append hash-chain audit entry (fail-closed) ──
            # This call uses the same open DB connection so the INSERT
            # participates in the current transaction.  If it raises,
            # the exception propagates out of the try-block and db.commit()
            # is never reached, preventing a verdict-without-chain-entry.
            from supervisor.audit import append_verdict_chain_entry
            append_verdict_chain_entry(
                db=db,
                application_id=app_id,
                verdict=supervisor_result["verdict"],
                contradiction_count=supervisor_result.get("contradiction_count", 0),
                supervisor_confidence=supervisor_result.get("supervisor_confidence", 0.0),
                memo_id=str(memo_row["id"]),
                actor_id=user.get("sub", ""),
                actor_name=user.get("name", ""),
                actor_role=user.get("role", ""),
                ip_address=self.get_client_ip(),
            )

            db.commit()
            _persist_ok = True
        except Exception as e:
            logger.error(
                "AUDIT CHAIN WRITE FAILURE for %s: verdict and chain entry were NOT persisted. "
                "Error: %s",
                app_id, e, exc_info=True,
            )
            _persist_ok = False
        db.close()

        if not _persist_ok:
            return self.error(
                "Supervisor verdict could not be persisted: the memo update and audit-chain "
                "entry were rolled back. Please retry.",
                500,
            )
        self.success(supervisor_result)


class MemoSupervisorResultHandler(BaseHandler):
    """GET /api/applications/:id/memo/supervisor — Get supervisor results for stored memo"""
    def get(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return

        db = get_db()
        memo_row = db.execute(
            "SELECT memo_data FROM compliance_memos WHERE application_id = ? OR application_id = (SELECT id FROM applications WHERE ref = ?) ORDER BY created_at DESC LIMIT 1",
            (app_id, app_id)
        ).fetchone()
        db.close()

        if not memo_row:
            return self.error("No memo found.", 404)

        try:
            memo_data = safe_json_loads(memo_row["memo_data"])
        except (json.JSONDecodeError, TypeError):
            return self.error("Memo data is corrupt.", 500)

        supervisor = memo_data.get("supervisor")
        if not supervisor:
            return self.error("Supervisor has not been run on this memo yet.", 404)

        self.success(supervisor)


# ══════════════════════════════════════════════════════════
# DECISION WORKFLOW ENDPOINTS (Step 7)
# ══════════════════════════════════════════════════════════

class ApplicationDecisionHandler(BaseHandler):
    """POST /api/applications/:id/decision — Submit application decision with override support"""
    def post(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        if not self.check_rate_limit("decision", max_attempts=10, window_seconds=60):
            return

        data = self.get_json()
        db = get_db()

        # ── EX-06: Row-level locking for approval path ──
        # PostgreSQL: SELECT ... FOR UPDATE serializes concurrent approval attempts.
        # SQLite: BEGIN IMMEDIATE acquires a write lock before the read.
        if db.is_postgres:
            app = db.execute(
                "SELECT * FROM applications WHERE id = ? OR ref = ? FOR UPDATE",
                (app_id, app_id)
            ).fetchone()
        else:
            # SQLite: acquire write lock early to prevent TOCTOU race
            try:
                db.execute("BEGIN IMMEDIATE")
            except Exception:
                pass  # Already in a transaction
            app = db.execute(
                "SELECT * FROM applications WHERE id = ? OR ref = ?",
                (app_id, app_id)
            ).fetchone()

        if not app:
            db.close()
            return self.error("Application not found", 404)

        real_id = app["id"]

        # EX-05: Capture before-state for audit trail
        _before = snapshot_app_state(app)

        # ── C-03 FIX: Prevent decision replay on terminal-state applications ──
        terminal_states = ("approved", "rejected")
        if app["status"] in terminal_states:
            db.close()
            return self.error(
                f"Decision replay blocked: application {app['ref']} is already in terminal state '{app['status']}'. "
                "Decisions cannot be replayed once an application has reached a final state.",
                409
            )

        # Validate required fields
        decision = data.get("decision")
        decision_reason = data.get("decision_reason")
        override_ai = data.get("override_ai", False)
        override_reason = data.get("override_reason", "")

        valid_decisions = ["approve", "reject", "escalate_edd", "request_documents"]
        if decision not in valid_decisions:
            db.close()
            return self.error(f"Invalid decision. Must be one of: {', '.join(valid_decisions)}", 400)

        if not decision_reason:
            db.close()
            return self.error("decision_reason is required", 400)

        if override_ai and not override_reason:
            db.close()
            return self.error("override_reason is required when override_ai is true", 400)

        # ── EX-11: Backend enforcement of officer sign-off for AI advisory ──
        signoff_scope = "override" if override_ai else "decision"
        signoff_error = _validate_officer_signoff(data.get("officer_signoff"), signoff_scope)
        if signoff_error:
            db.close()
            return self.error(signoff_error, 400)

        # ── SECURITY: Enforce approval preconditions (mandatory) ──
        if decision == "approve":
            # ── H-1 FIX: Enforce ROLE_PERMISSION_MATRIX — CO cannot approve HIGH/VERY_HIGH ──
            if user.get("role") == "co" and app["risk_level"] in ("HIGH", "VERY_HIGH"):
                db.close()
                return self.error(
                    "Approval blocked: Compliance Officers cannot approve HIGH or VERY_HIGH risk applications. "
                    "Only Admin or Senior Compliance Officer roles may approve at this risk level.",
                    403
                )

            # ── C-05 FIX: Enforce compliance memo existence via DB lookup on ALL approval paths ──
            memo_exists = db.execute(
                "SELECT id FROM compliance_memos WHERE application_id = ?", (real_id,)
            ).fetchone()
            if not memo_exists:
                db.close()
                return self.error(
                    "Approval blocked: compliance memo must be generated before approval. "
                    "Generate a memo via POST /api/applications/{id}/memo first.",
                    400
                )

            can_approve, gate_error = ApprovalGateValidator.validate_approval(app, db)
            if not can_approve:
                db.close()
                return self.error(f"Approval blocked: {gate_error}", 400)

            # ── EX-06: Dual-approval for high-risk cases using structured fields ──
            if app["risk_level"] in ("HIGH", "VERY_HIGH"):
                can_approve, dual_error = ApprovalGateValidator.validate_high_risk_dual_approval(app, user, db)
                if not can_approve:
                    # Distinguish: same-officer retry vs genuine first approval
                    if dual_error == "DUAL_SAME_OFFICER":
                        db.close()
                        return self.error(
                            "Dual-approval conflict: you already recorded the first approval. "
                            "A different authorized officer must complete the second approval.",
                            409
                        )
                    # Record first approval in structured application fields
                    db.execute(
                        "UPDATE applications SET first_approver_id = ?, first_approved_at = datetime('now'), "
                        "updated_at = datetime('now') WHERE id = ?",
                        (user.get("sub", ""), real_id)
                    )
                    _first_after = {"status": app["status"], "decision": "approve",
                                    "note": "awaiting_second_approver",
                                    "first_approver_id": user.get("sub", "")}
                    db.execute("""INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address, before_state, after_state)
                                 VALUES (?,?,?,?,?,?,?,?,?)""",
                               (user.get("sub",""), user.get("name",""), user.get("role",""),
                                "First Approval (Pending Second)", app["ref"],
                                f"Decision: approve | Reason: {decision_reason} | Awaiting second approver",
                                self.get_client_ip(), _safe_json(_before), _safe_json(_first_after)))
                    db.commit()
                    db.close()
                    return self.success({"status": "first_approval_recorded", "message": dual_error}, 202)

        # Handle request_documents
        required_documents = []
        if decision == "request_documents":
            required_documents = data.get("documents_list", [])
            if not required_documents:
                db.close()
                return self.error("documents_list is required for request_documents decision", 400)

        # Update application status
        new_status = {
            "approve": "approved",
            "reject": "rejected",
            "escalate_edd": "edd_required",
            "request_documents": "kyc_documents"
        }[decision]

        detail_info = {
            "decision": decision,
            "decision_reason": decision_reason,
            "override_ai": override_ai,
            "override_reason": override_reason if override_ai else None,
            "required_documents": required_documents if decision == "request_documents" else None
        }

        db.execute("""
            UPDATE applications SET
                status=?, decided_at=datetime('now'), decision_by=?, decision_notes=?,
                first_approver_id=NULL, first_approved_at=NULL,
                updated_at=datetime('now')
            WHERE id=?
        """, (new_status, user["sub"], json.dumps(detail_info), real_id))

        # Log audit trail with full detail
        audit_detail = f"Decision: {decision} | Reason: {decision_reason}"
        if override_ai:
            audit_detail += f" | AI Override: {override_reason}"
        if required_documents:
            audit_detail += f" | Documents Required: {', '.join(required_documents)}"

        _after = {"status": new_status, "decision": decision, "decision_reason": decision_reason,
                  "override_ai": override_ai}
        db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address, before_state, after_state) VALUES (?,?,?,?,?,?,?,?,?)",
                   (user.get("sub",""), user.get("name",""), user.get("role",""), "Decision", app["ref"], audit_detail, self.get_client_ip(),
                    _safe_json(_before), _safe_json(_after)))

        # ── EX-11: Persist officer sign-off audit record ──
        _persist_signoff_audit(db, user, app["ref"],
                               "override" if override_ai else "decision",
                               data.get("officer_signoff", {}),
                               self.get_client_ip(),
                               self.request.headers.get("User-Agent", ""))

        # ── Record normalized decision record ──
        try:
            # Try to derive confidence from supervisor result in the compliance memo
            supervisor_result = None
            try:
                memo_row = db.execute(
                    "SELECT memo_data FROM compliance_memos WHERE application_id = ? ORDER BY created_at DESC LIMIT 1",
                    (real_id,)
                ).fetchone()
                if memo_row:
                    memo_data = json.loads(memo_row["memo_data"]) if memo_row["memo_data"] else {}
                    supervisor_result = memo_data.get("supervisor")
            except Exception:
                pass  # Non-critical: proceed without supervisor data

            decision_record = build_from_application_decision(
                app=dict(app),
                decision=decision,
                decision_reason=decision_reason,
                user=user,
                override_ai=override_ai,
                override_reason=override_reason,
                supervisor_result=supervisor_result,
            )
            save_decision_record(db, decision_record)
        except Exception as e:
            logger.error("Failed to record decision record for app %s: %s", app["ref"], e)

        db.commit()
        db.close()

        self.success({"status": "decision_recorded", "decision": decision, "application_status": new_status}, 201)


class DecisionRecordsHandler(BaseHandler):
    """GET /api/applications/:id/decision-records — Retrieve normalized decision records"""
    def get(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return

        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        decision_type = self.get_argument("decision_type", None)
        raw_limit = self.get_argument("limit", "50")
        try:
            limit = int(raw_limit)
        except ValueError:
            db.close()
            return self.error("Invalid 'limit' parameter: must be a positive integer", 400)

        if limit <= 0:
            db.close()
            return self.error("Invalid 'limit' parameter: must be a positive integer", 400)

        limit = min(limit, 200)

        try:
            records = get_decision_records(db, app["ref"], decision_type=decision_type, limit=limit)
        except Exception as e:
            db.close()
            logger.error("Failed to retrieve decision records for %s: %s", app_id, e)
            return self.error("Failed to retrieve decision records", 500)

        db.close()
        self.success({"records": records, "count": len(records)})


# ══════════════════════════════════════════════════════════
# CLIENT NOTIFICATION ENDPOINTS (Step 9)
# ══════════════════════════════════════════════════════════

class ClientNotificationHandler(BaseHandler):
    """POST /api/applications/:id/notify — Send notification to client"""
    def post(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()
        db = get_db()

        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        notification_type = data.get("notification_type")
        message = data.get("message")
        documents_list = data.get("documents_list", [])

        valid_types = ["approved", "documents_required", "rejected"]
        if notification_type not in valid_types:
            db.close()
            return self.error(f"Invalid notification_type. Must be one of: {', '.join(valid_types)}", 400)

        if not message:
            db.close()
            return self.error("message is required", 400)

        if not app.get("client_id"):
            db.close()
            return self.error("Cannot send notification: no client associated with this application", 400)

        # Create notification
        title_map = {
            "approved": "Application Approved",
            "documents_required": "Documents Required",
            "rejected": "Application Rejected"
        }

        db.execute("""
            INSERT INTO client_notifications (application_id, client_id, notification_type, title, message, documents_list, read_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, datetime('now'))
        """, (app["id"], app.get("client_id"), notification_type, title_map[notification_type], message,
              json.dumps(documents_list) if documents_list else None))

        # Log audit trail
        db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                   (user.get("sub",""), user.get("name",""), user.get("role",""), "Notify Client", app["ref"],
                    f"Sent {notification_type} notification to client", self.get_client_ip()))

        db.commit()
        db.close()

        self.success({"status": "notification_sent", "notification_type": notification_type}, 201)


class GetClientNotificationsHandler(BaseHandler):
    """GET /api/notifications — Get notifications for logged-in client"""
    def get(self):
        user = self.require_auth()
        if not user:
            return

        # Only clients can retrieve their notifications
        if user.get("type") != "client":
            return self.error("Only clients can retrieve notifications", 403)

        db = get_db()
        notifications = db.execute("""
            SELECT id, application_id, notification_type, title, message, documents_list, read_status, created_at
            FROM client_notifications
            WHERE client_id = ?
            ORDER BY created_at DESC
        """, (user["sub"],)).fetchall()

        result = [dict(n) for n in notifications]
        for n in result:
            if n["documents_list"]:
                n["documents_list"] = safe_json_loads(n["documents_list"])

        db.close()
        self.success({"notifications": result})


class MarkNotificationReadHandler(BaseHandler):
    """PATCH /api/notifications/:id/read — Mark notification as read"""
    def patch(self, notif_id):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        notif = db.execute("SELECT * FROM client_notifications WHERE id = ?", (notif_id,)).fetchone()
        if not notif:
            db.close()
            return self.error("Notification not found", 404)

        if notif["client_id"] != user["sub"]:
            db.close()
            return self.error("Unauthorized", 403)

        db.execute("UPDATE client_notifications SET read_status=1, read_at=datetime('now') WHERE id=?", (notif_id,))
        db.commit()
        db.close()

        self.success({"status": "marked_read"})


STATUS_LOOKUP_PUBLIC_FIELDS = ("ref", "status", "updated_at")


def lookup_application_status_record(db, ref="", email="", current_user=None):
    """
    Resolve a status-lookup request with a public-safe contract.

    Anonymous lookup requires both reference number and email so that the endpoint
    does not become an enumeration surface. Authenticated client lookups are
    restricted to the caller's own applications.
    """
    ref = (ref or "").strip()
    email = (email or "").strip().lower()

    if current_user and current_user.get("type") == "client":
        query = """
            SELECT a.ref, a.status, a.updated_at
            FROM applications a
            LEFT JOIN clients c ON c.id = a.client_id
            WHERE a.client_id = ?
        """
        params = [current_user["sub"]]

        if ref:
            query += " AND a.ref = ?"
            params.append(ref)
        elif email:
            query += " AND LOWER(c.email) = ?"
            params.append(email)
        else:
            raise ValueError("Reference number or email is required.")
    else:
        if not ref or not email:
            raise ValueError("Reference number and email are required for public status lookup.")

        query = """
            SELECT a.ref, a.status, a.updated_at
            FROM applications a
            LEFT JOIN clients c ON c.id = a.client_id
            WHERE a.ref = ? AND LOWER(c.email) = ?
        """
        params = [ref, email]

    query += " ORDER BY a.updated_at DESC, a.created_at DESC LIMIT 1"
    return db.execute(query, params).fetchone()


def build_status_lookup_payload(app_row):
    """Return the minimal public-safe status payload."""
    if not app_row:
        return None
    payload = {
        field: app_row[field]
        for field in STATUS_LOOKUP_PUBLIC_FIELDS
        if field in app_row.keys()
    }
    payload["status_label"] = get_status_label(payload.get("status"))
    return payload


class ClientStatusLookupHandler(BaseHandler):
    """GET /api/status-lookup — Look up latest application status by reference and/or email"""
    def get(self):
        ref = self.get_argument("ref", "").strip()
        email = self.get_argument("email", "").strip().lower()

        if not ref and not email:
            return self.error("ref or email is required", 400)

        current_user = self.get_current_user_token()
        db = get_db()
        try:
            app = lookup_application_status_record(db, ref=ref, email=email, current_user=current_user)
        except ValueError as exc:
            db.close()
            return self.error(str(exc), 400)
        db.close()

        if not app:
            return self.error("Application not found", 404)

        self.success({"application": build_status_lookup_payload(app)})


# ══════════════════════════════════════════════════════════
# MONITORING ENDPOINTS (Ongoing Monitoring)
# ══════════════════════════════════════════════════════════

class MonitoringAlertDetailHandler(BaseHandler):
    """GET/PATCH /api/monitoring/alerts/:id — Get alert detail and update status"""
    def get(self, alert_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        db = get_db()
        alert = db.execute("SELECT * FROM monitoring_alerts WHERE id = ?", (alert_id,)).fetchone()
        if not alert:
            db.close()
            return self.error("Alert not found", 404)

        result = dict(alert)
        db.close()
        self.success(result)

    def patch(self, alert_id):
        """Update alert state and route it to a downstream object.

        PR-02 supported actions:
          - triage
          - assign
          - dismiss              (requires `dismissal_reason`)
          - route_to_periodic_review
          - route_to_edd

        Legacy aliases (kept for back-compat with existing UI/tests):
          - escalate         -> route_to_edd
          - trigger_review   -> route_to_periodic_review
        """
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()

        action = data.get("action")
        reason = data.get("reason") or ""

        # Back-compat aliases. Kept narrow so legacy callers still work
        # while new explicit verbs are the documented contract.
        legacy_alias = {
            "escalate": "route_to_edd",
            "trigger_review": "route_to_periodic_review",
        }
        canonical_action = legacy_alias.get(action, action)

        valid_actions = [
            "triage", "assign", "dismiss",
            "route_to_periodic_review", "route_to_edd",
        ]
        valid_for_error = valid_actions + list(legacy_alias.keys())
        if canonical_action not in valid_actions:
            return self.error(
                f"Invalid action. Must be one of: {', '.join(valid_for_error)}",
                400,
            )

        import monitoring_routing as mr

        db = get_db()
        try:
            try:
                if canonical_action == "triage":
                    result = mr.triage_alert(
                        db, alert_id, user=user, audit_writer=self.log_audit,
                    )
                elif canonical_action == "assign":
                    result = mr.assign_alert(
                        db, alert_id, user=user, audit_writer=self.log_audit,
                    )
                elif canonical_action == "dismiss":
                    dismissal_reason = data.get("dismissal_reason")
                    if not dismissal_reason:
                        return self.error(
                            "dismissal_reason is required for action=dismiss "
                            f"(one of: {', '.join(mr.VALID_DISMISSAL_REASONS)})",
                            400,
                        )
                    result = mr.dismiss_alert(
                        db, alert_id,
                        dismissal_reason=dismissal_reason,
                        dismissal_notes=reason or data.get("dismissal_notes"),
                        user=user, audit_writer=self.log_audit,
                    )
                elif canonical_action == "route_to_periodic_review":
                    result = mr.route_alert_to_periodic_review(
                        db, alert_id,
                        review_reason=reason or data.get("review_reason"),
                        priority=data.get("priority"),
                        user=user, audit_writer=self.log_audit,
                    )
                elif canonical_action == "route_to_edd":
                    result = mr.route_alert_to_edd(
                        db, alert_id,
                        trigger_notes=reason or data.get("trigger_notes"),
                        priority=data.get("priority"),
                        user=user, audit_writer=self.log_audit,
                    )
            except mr.AlertNotFound:
                return self.error("Alert not found", 404)
            except mr.InvalidDismissalReason as e:
                return self.error(str(e), 400)
            except mr.AlertAlreadyTerminal as e:
                return self.error(str(e), 409)
            except mr.MonitoringRoutingError as e:
                return self.error(str(e), 400)

            self.success({
                "status": "alert_updated",
                "action": canonical_action,
                "result": result,
                # Back-compat field for existing UI consumers.
                "new_status": result.get("status"),
            })
        finally:
            db.close()


class MonitoringAgentsHandler(BaseHandler):
    """GET /api/monitoring/agents — Get status of monitoring agents"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        db = get_db()
        agents = db.execute("""
            SELECT id, agent_name, agent_type, last_run, next_run, run_frequency, clients_monitored, alerts_generated, status
            FROM monitoring_agent_status
            ORDER BY agent_name
        """).fetchall()

        result = [dict(a) for a in agents]
        db.close()
        self.success({"agents": result})


class MonitoringAgentRunHandler(BaseHandler):
    """POST /api/monitoring/agents/:id/run — Manually trigger agent run"""
    def post(self, agent_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        db = get_db()
        agent = db.execute("SELECT * FROM monitoring_agent_status WHERE id = ?", (agent_id,)).fetchone()
        if not agent:
            db.close()
            return self.error("Agent not found", 404)

        # Simulate agent run - in production, this would trigger actual monitoring logic
        now = datetime.now().isoformat()
        db.execute("""
            UPDATE monitoring_agent_status SET last_run=?, alerts_generated=alerts_generated+1 WHERE id=?
        """, (now, agent_id))

        db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                   (user.get("sub",""), user.get("name",""), user.get("role",""), "Agent Run", agent["agent_name"],
                    f"Manual run triggered for {agent['agent_name']}", self.get_client_ip()))

        db.commit()
        db.close()

        self.success({"status": "agent_run_initiated", "agent": agent["agent_name"], "run_time": now})


class PeriodicReviewsListHandler(BaseHandler):
    """GET /api/monitoring/reviews — List periodic reviews"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        status_filter = self.get_argument("status", None)
        db = get_db()

        query = "SELECT * FROM periodic_reviews WHERE 1=1"
        params = []

        if status_filter:
            query += " AND status = ?"
            params.append(status_filter)

        query += " ORDER BY due_date ASC"
        reviews = db.execute(query, params).fetchall()

        result = [dict(r) for r in reviews]
        db.close()
        self.success({"reviews": result})


class PeriodicReviewDetailHandler(BaseHandler):
    """GET /api/monitoring/reviews/:id — Get review detail"""
    def get(self, review_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        db = get_db()
        review = db.execute("SELECT * FROM periodic_reviews WHERE id = ?", (review_id,)).fetchone()
        if not review:
            db.close()
            return self.error("Review not found", 404)

        result = dict(review)
        if result["review_memo"]:
            result["review_memo"] = safe_json_loads(result["review_memo"])

        db.close()
        self.success(result)


class PeriodicReviewDecisionHandler(BaseHandler):
    """POST /api/monitoring/reviews/:id/decision — Submit review decision"""
    def post(self, review_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()
        db = get_db()

        review = db.execute("SELECT * FROM periodic_reviews WHERE id = ?", (review_id,)).fetchone()
        if not review:
            db.close()
            return self.error("Review not found", 404)

        # ── C-03 FIX: Prevent decision replay on completed reviews ──
        if review["status"] == "completed":
            db.close()
            return self.error(
                f"Decision replay blocked: review {review_id} is already completed.",
                409
            )

        decision = data.get("decision")
        decision_reason = data.get("decision_reason")

        valid_decisions = ["continue", "enhanced_monitoring", "request_info", "exit_relationship"]
        if decision not in valid_decisions:
            db.close()
            return self.error(f"Invalid decision. Must be one of: {', '.join(valid_decisions)}", 400)

        if not decision_reason:
            db.close()
            return self.error("decision_reason is required", 400)

        db.execute("""
            UPDATE periodic_reviews SET
                status='completed', decision=?, decision_reason=?, decided_by=?, completed_at=datetime('now')
            WHERE id=?
        """, (decision, decision_reason, user["sub"], review_id))

        # Log audit
        db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                   (user.get("sub",""), user.get("name",""), user.get("role",""), "Review Decision", f"Review {review_id}",
                    f"Decision: {decision}, Reason: {decision_reason}", self.get_client_ip()))

        db.commit()
        db.close()

        self.success({"status": "decision_recorded", "decision": decision})


class PeriodicReviewScheduleHandler(BaseHandler):
    """POST /api/monitoring/reviews/schedule — Check and create due reviews"""
    def post(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        db = get_db()

        # Find applications due for periodic review based on risk level
        # LOW: every 2 years, MEDIUM: annual, HIGH: semi-annual, VERY_HIGH: quarterly
        now = datetime.now()
        today = now.date().isoformat()

        risk_intervals = {
            "LOW": 1095,       # 36 months (3 years)
            "MEDIUM": 730,     # 24 months (2 years)
            "HIGH": 365,       # 12 months (1 year)
            "VERY_HIGH": 180   # 6 months
        }

        created_count = 0
        for risk_level, days in risk_intervals.items():
            # Find applications with this risk level that haven't been reviewed recently
            cutoff_date = (now - timedelta(days=days)).isoformat()
            apps = db.execute("""
                SELECT a.id, a.ref, a.company_name, a.risk_level
                FROM applications a
                WHERE a.risk_level = ? AND a.status IN ('approved', 'rmi_sent')
                AND NOT EXISTS (
                    SELECT 1 FROM periodic_reviews pr
                    WHERE pr.application_id = a.id AND pr.created_at > ?
                )
            """, (risk_level, cutoff_date)).fetchall()

            for app in apps:
                due_date = (now + timedelta(days=7)).date().isoformat()
                db.execute("""
                    INSERT INTO periodic_reviews (application_id, client_name, risk_level, trigger_type, trigger_reason, status, due_date, created_at)
                    VALUES (?, ?, ?, 'time_based', ?, 'pending', ?, datetime('now'))
                """, (app["id"], app["company_name"], app["risk_level"], f"Periodic review due for {risk_level} risk client", due_date))
                created_count += 1

        db.commit()
        db.close()

        self.success({"status": "schedule_check_complete", "reviews_created": created_count})


# ──────────────────────────────────────────────────────────
# PR-03: Periodic Review Operating Model -- additive handlers
# ──────────────────────────────────────────────────────────
# These handlers are additive and do NOT replace the legacy
# PeriodicReviewDecisionHandler above (kept for back-compat). They
# delegate all state, generation, escalation and outcome logic to the
# provider-agnostic ``periodic_review_engine`` module so that server.py
# stays thin and the operating model is unit-testable in isolation.
#
# PR-03a hardening: every PR-03 handler validates ``review_id`` at the
# HTTP boundary via ``_parse_review_id`` BEFORE calling the engine /
# touching the database. This prevents a non-numeric path segment from
# bubbling down into a Postgres type error and surfacing as a 500 to
# clients. The path regex is permissive (``[^/]+``) so the validation
# must live in code, not in the URL spec.
def _parse_review_id(handler, raw_review_id):
    """Validate / coerce a path-segment review_id to a positive int.

    Returns the int on success; on failure writes a 400 response and
    returns ``None`` so the caller can early-return without ever
    invoking the engine or the DB.
    """
    try:
        rid = int(raw_review_id)
    except (TypeError, ValueError):
        handler.error("review_id must be a positive integer", 400)
        return None
    if rid < 1:
        handler.error("review_id must be a positive integer", 400)
        return None
    return rid


class PeriodicReviewStateHandler(BaseHandler):
    """POST /api/monitoring/reviews/:id/state -- transition review state.

    Body: {"state": "<one of in_progress|awaiting_information|"
                       "pending_senior_review>",
           "reason": "<optional rationale>"}

    Use POST /complete to move a review to 'completed' (completion
    must carry an explicit outcome).
    """
    def post(self, review_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return
        review_id = _parse_review_id(self, review_id)
        if review_id is None:
            return
        data = self.get_json()
        new_state = data.get("state")
        reason = data.get("reason")
        if not new_state:
            return self.error("state is required", 400)
        import periodic_review_engine as pre
        db = get_db()
        try:
            try:
                result = pre.transition_review_state(
                    db, review_id,
                    new_state=new_state, reason=reason,
                    user=user, audit_writer=self.log_audit,
                )
            except pre.ReviewNotFound:
                return self.error("Review not found", 404)
            except pre.InvalidReviewState as e:
                return self.error(str(e), 400)
            except pre.InvalidReviewTransition as e:
                return self.error(str(e), 409)
            except pre.ReviewClosedError as e:
                return self.error(str(e), 409)
            self.success({"status": "state_changed", "result": result})
        finally:
            db.close()


class PeriodicReviewRequiredItemsHandler(BaseHandler):
    """GET /api/monitoring/reviews/:id/required-items -- list items.

    POST /api/monitoring/reviews/:id/required-items/generate -- (re)generate.
    """
    def get(self, review_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return
        review_id = _parse_review_id(self, review_id)
        if review_id is None:
            return
        import periodic_review_engine as pre
        db = get_db()
        try:
            try:
                items = pre.get_required_items(db, review_id)
            except pre.ReviewNotFound:
                return self.error("Review not found", 404)
            self.success({"review_id": review_id, "items": items})
        finally:
            db.close()


class PeriodicReviewRequiredItemsGenerateHandler(BaseHandler):
    """POST /api/monitoring/reviews/:id/required-items/generate."""
    def post(self, review_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return
        review_id = _parse_review_id(self, review_id)
        if review_id is None:
            return
        import periodic_review_engine as pre
        db = get_db()
        try:
            try:
                items = pre.generate_required_items(
                    db, review_id,
                    user=user, audit_writer=self.log_audit,
                )
            except pre.ReviewNotFound:
                return self.error("Review not found", 404)
            except pre.ReviewClosedError as e:
                return self.error(str(e), 409)
            self.success({
                "status": "required_items_generated",
                "review_id": review_id,
                "count": len(items),
                "items": items,
            })
        finally:
            db.close()


class PeriodicReviewEscalateHandler(BaseHandler):
    """POST /api/monitoring/reviews/:id/escalate -- escalate to EDD.

    Body: {"trigger_notes": "<optional>", "priority": "<optional>"}.

    Reuses any active EDD case linked to the review or to the
    application; otherwise creates a new EDD case via the same
    duplicate-prevention predicate as ``EDDCreateHandler.post``.
    """
    def post(self, review_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return
        review_id = _parse_review_id(self, review_id)
        if review_id is None:
            return
        data = self.get_json() or {}
        import periodic_review_engine as pre
        db = get_db()
        try:
            try:
                result = pre.escalate_review_to_edd(
                    db, review_id,
                    trigger_notes=data.get("trigger_notes"),
                    priority=data.get("priority"),
                    user=user, audit_writer=self.log_audit,
                )
            except pre.ReviewNotFound:
                return self.error("Review not found", 404)
            except pre.ReviewClosedError as e:
                return self.error(str(e), 409)
            except pre.PeriodicReviewEngineError as e:
                return self.error(str(e), 400)
            self.success({"status": "escalated_to_edd", "result": result})
        finally:
            db.close()


class PeriodicReviewCompleteHandler(BaseHandler):
    """POST /api/monitoring/reviews/:id/complete -- record outcome and close.

    Body: {"outcome": "<one of no_change|enhanced_monitoring|"
                       "edd_required|exit_recommended>",
           "outcome_reason": "<rationale>"}
    """
    def post(self, review_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return
        review_id = _parse_review_id(self, review_id)
        if review_id is None:
            return
        data = self.get_json() or {}
        outcome = data.get("outcome")
        outcome_reason = data.get("outcome_reason")
        if not outcome:
            return self.error("outcome is required", 400)
        if not outcome_reason:
            return self.error("outcome_reason is required", 400)
        import periodic_review_engine as pre
        db = get_db()
        try:
            try:
                result = pre.record_review_outcome(
                    db, review_id,
                    outcome=outcome, outcome_reason=outcome_reason,
                    user=user, audit_writer=self.log_audit,
                )
            except pre.ReviewNotFound:
                return self.error("Review not found", 404)
            except pre.InvalidReviewOutcome as e:
                return self.error(str(e), 400)
            except pre.ReviewClosedError as e:
                return self.error(str(e), 409)
            except pre.PeriodicReviewEngineError as e:
                return self.error(str(e), 400)

            # PR-D: generate the lightweight periodic review memo AFTER
            # the outcome commit. Deterministic, template-driven, no AI.
            # Failure here MUST NOT roll back the outcome -- the review
            # remains completed; a status='generation_failed' row is
            # persisted by the generator so the read endpoint and UI can
            # differentiate "not yet completed" (no row) from "completed
            # but generation failed" (row with failure status). See
            # periodic_review_memo.py docstring for the failure contract.
            memo_result = None
            try:
                import periodic_review_memo as prm
                memo_result = prm.generate_periodic_review_memo(db, review_id)
            except Exception:
                # Generator already logged with full traceback. Swallow
                # here so the outcome commit is the authoritative result
                # surfaced to the caller.
                memo_result = {"status": "generation_failed"}

            self.success({
                "status": "outcome_recorded",
                "result": result,
                "memo": memo_result,
            })
        finally:
            db.close()


class PeriodicReviewMemoHandler(BaseHandler):
    """GET /api/periodic-reviews/:id/memo — Lightweight periodic review memo.

    PR-D: returns the latest memo row for the given periodic review.

    * 200 with ``status='generated'`` and full ``memo_data`` when the
      memo was generated successfully.
    * 200 with ``status='generation_failed'`` when a failure-indicator
      row exists (review completed, but memo generation raised).
    * 404 when no memo row exists at all (review not yet completed).

    Auth gating mirrors ``PeriodicReviewDetailHandler`` (admin|sco|co).
    Explicitly NOT ``compliance_memos``: this endpoint never reads or
    writes the onboarding memo table.
    """
    def get(self, review_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return
        review_id = _parse_review_id(self, review_id)
        if review_id is None:
            return
        db = get_db()
        try:
            import periodic_review_memo as prm
            memo = prm.fetch_latest_memo(db, review_id)
            if memo is None:
                return self.error("Memo not found", 404)
            self.success(memo)
        finally:
            db.close()


# ══════════════════════════════════════════════════════════
# SAR (SUSPICIOUS ACTIVITY REPORT) ENDPOINTS
# ══════════════════════════════════════════════════════════

class SARListHandler(BaseHandler):
    """GET /api/sar — List SAR reports, POST — create new SAR"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        status_filter = self.get_argument("status", None)
        db = get_db()

        query = "SELECT * FROM sar_reports WHERE 1=1"
        params = []
        if status_filter:
            query += " AND filing_status = ?"
            params.append(status_filter)
        query += " ORDER BY created_at DESC"

        rows = db.execute(query, params).fetchall()
        result = []
        for r in rows:
            row_dict = dict(r)
            row_dict["indicators"] = safe_json_loads(row_dict["indicators"]) if row_dict["indicators"] else []
            row_dict["transaction_details"] = safe_json_loads(row_dict["transaction_details"]) if row_dict["transaction_details"] else {}
            row_dict["supporting_documents"] = safe_json_loads(row_dict["supporting_documents"]) if row_dict["supporting_documents"] else []
            result.append(row_dict)

        db.close()
        self.success({"sar_reports": result, "total": len(result)})

    def post(self):
        """Create a new SAR report — can be auto-triggered by alert or manually created."""
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()
        subject_name = data.get("subject_name", "").strip()
        narrative = data.get("narrative", "").strip()

        if not subject_name or not narrative:
            return self.error("subject_name and narrative are required")

        db = get_db()
        sar_id = uuid.uuid4().hex[:16]

        # Generate SAR reference number
        count = db.execute("SELECT COUNT(*) as c FROM sar_reports").fetchone()["c"]
        year = datetime.now().year
        sar_ref = f"SAR-{year}-{10001 + count}"

        db.execute("""
            INSERT INTO sar_reports (id, application_id, alert_id, sar_reference, report_type,
                subject_name, subject_type, risk_level, narrative, indicators,
                transaction_details, supporting_documents, filing_status, prepared_by, regulatory_body)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            sar_id,
            data.get("application_id"),
            data.get("alert_id"),
            sar_ref,
            data.get("report_type", "SAR"),
            subject_name,
            data.get("subject_type", "individual"),
            data.get("risk_level", "HIGH"),
            narrative,
            json.dumps(data.get("indicators", [])),
            json.dumps(data.get("transaction_details", {})),
            json.dumps(data.get("supporting_documents", [])),
            "draft",
            user["sub"],
            data.get("regulatory_body", "FIU Mauritius"),
        ))

        db.commit()
        db.close()

        self.log_audit(user, "SAR Created", sar_ref, f"SAR report created for {subject_name}")
        if METRICS_ENABLED:
            SAR_COUNT.labels(status="draft").inc()

        self.success({"id": sar_id, "sar_reference": sar_ref, "status": "draft"}, 201)


class SARDetailHandler(BaseHandler):
    """GET/PUT /api/sar/:id — Get or update a SAR report"""
    def get(self, sar_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        db = get_db()
        sar = db.execute("SELECT * FROM sar_reports WHERE id = ? OR sar_reference = ?", (sar_id, sar_id)).fetchone()
        if not sar:
            db.close()
            return self.error("SAR report not found", 404)

        result = dict(sar)
        result["indicators"] = safe_json_loads(result["indicators"]) if result["indicators"] else []
        result["transaction_details"] = safe_json_loads(result["transaction_details"]) if result["transaction_details"] else {}
        result["supporting_documents"] = safe_json_loads(result["supporting_documents"]) if result["supporting_documents"] else []

        db.close()
        self.success(result)

    def put(self, sar_id):
        """Update SAR report (edit narrative, indicators, etc.)"""
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()
        db = get_db()

        sar = db.execute("SELECT * FROM sar_reports WHERE id = ? OR sar_reference = ?", (sar_id, sar_id)).fetchone()
        if not sar:
            db.close()
            return self.error("SAR report not found", 404)

        if sar["filing_status"] == "filed":
            db.close()
            return self.error("Cannot modify a filed SAR report", 400)

        real_id = sar["id"]
        db.execute("""
            UPDATE sar_reports SET
                narrative=?, indicators=?, transaction_details=?,
                supporting_documents=?, risk_level=?, updated_at=datetime('now')
            WHERE id=?
        """, (
            data.get("narrative", sar["narrative"]),
            json.dumps(data.get("indicators", safe_json_loads(sar["indicators"]))),
            json.dumps(data.get("transaction_details", safe_json_loads(sar["transaction_details"]))),
            json.dumps(data.get("supporting_documents", safe_json_loads(sar["supporting_documents"]))),
            data.get("risk_level", sar["risk_level"]),
            real_id,
        ))

        db.commit()
        db.close()

        self.log_audit(user, "SAR Updated", sar["sar_reference"], "SAR report updated")
        self.success({"status": "updated"})


class SARWorkflowHandler(BaseHandler):
    """POST /api/sar/:id/workflow — Advance SAR through workflow (review → approve → file)"""
    def post(self, sar_id):
        user = self.require_auth(roles=["admin", "sco"])
        if not user:
            return

        data = self.get_json()
        action = data.get("action")
        notes = data.get("notes", "")

        valid_actions = ["submit_review", "approve", "reject", "file", "archive"]
        if action not in valid_actions:
            return self.error(f"Invalid action. Must be one of: {', '.join(valid_actions)}")

        db = get_db()
        sar = db.execute("SELECT * FROM sar_reports WHERE id = ? OR sar_reference = ?", (sar_id, sar_id)).fetchone()
        if not sar:
            db.close()
            return self.error("SAR report not found", 404)

        real_id = sar["id"]
        current_status = sar["filing_status"]

        # Validate state transitions
        valid_transitions = {
            "draft": ["submit_review"],
            "pending_review": ["approve", "reject"],
            "approved": ["file"],
            "filed": ["archive"],
            "rejected": ["submit_review"],
        }

        if action not in valid_transitions.get(current_status, []):
            db.close()
            return self.error(f"Cannot {action} a SAR in '{current_status}' status", 400)

        new_status = {
            "submit_review": "pending_review",
            "approve": "approved",
            "reject": "rejected",
            "file": "filed",
            "archive": "archived",
        }[action]

        # C-06: Parameterized SQL — NO f-string interpolation for SQL
        # Each action maps to a fixed SQL statement with explicit parameter positions
        if action == "approve":
            db.execute(
                "UPDATE sar_reports SET filing_status=?, updated_at=datetime('now'), approved_by=? WHERE id=?",
                (new_status, user["sub"], real_id)
            )
        elif action == "submit_review":
            db.execute(
                "UPDATE sar_reports SET filing_status=?, updated_at=datetime('now'), reviewed_by=? WHERE id=?",
                (new_status, user["sub"], real_id)
            )
        elif action == "file":
            external_ref = str(data.get("external_reference", ""))[:100]  # Sanitize + limit length
            db.execute(
                "UPDATE sar_reports SET filing_status=?, updated_at=datetime('now'), filed_at=datetime('now'), external_reference=? WHERE id=?",
                (new_status, external_ref, real_id)
            )
        else:
            # reject, archive — status-only update
            db.execute(
                "UPDATE sar_reports SET filing_status=?, updated_at=datetime('now') WHERE id=?",
                (new_status, real_id)
            )

        self.log_audit(user, f"SAR {action.replace('_',' ').title()}", sar["sar_reference"],
                       f"SAR workflow: {current_status} → {new_status}. {notes}", db=db)

        db.commit()
        db.close()

        if METRICS_ENABLED:
            SAR_COUNT.labels(status=new_status).inc()

        self.success({"status": new_status, "sar_reference": sar["sar_reference"]})


class SARAutoTriggerHandler(BaseHandler):
    """POST /api/sar/auto-trigger — Auto-create SAR from high-risk monitoring alert"""
    def post(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()
        alert_id = data.get("alert_id")

        if not alert_id:
            return self.error("alert_id is required")

        db = get_db()
        alert = db.execute("SELECT * FROM monitoring_alerts WHERE id = ?", (alert_id,)).fetchone()
        if not alert:
            db.close()
            return self.error("Alert not found", 404)

        # Check if SAR already exists for this alert
        existing = db.execute("SELECT id, sar_reference FROM sar_reports WHERE alert_id = ?", (alert_id,)).fetchone()
        if existing:
            db.close()
            return self.success({"existing": True, "sar_reference": existing["sar_reference"], "id": existing["id"]})

        # Auto-populate SAR from alert data
        sar_id = uuid.uuid4().hex[:16]
        count = db.execute("SELECT COUNT(*) as c FROM sar_reports").fetchone()["c"]
        sar_ref = f"SAR-{datetime.now().year}-{10001 + count}"

        narrative = (
            f"Auto-generated SAR from monitoring alert.\n\n"
            f"Alert Type: {alert['alert_type']}\n"
            f"Severity: {alert['severity']}\n"
            f"Detected By: {alert['detected_by']}\n"
            f"Summary: {alert['summary']}\n"
            f"Source: {alert['source_reference']}\n"
            f"AI Recommendation: {alert['ai_recommendation']}"
        )

        db.execute("""
            INSERT INTO sar_reports (id, application_id, alert_id, sar_reference, report_type,
                subject_name, subject_type, risk_level, narrative, filing_status, prepared_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            sar_id, alert["application_id"], alert_id, sar_ref, "SAR",
            alert["client_name"], "entity", alert["severity"].upper(),
            narrative, "draft", user["sub"],
        ))

        # Update alert to reflect SAR creation
        db.execute("UPDATE monitoring_alerts SET officer_action='sar_filed', officer_notes=? WHERE id=?",
                   (f"SAR {sar_ref} auto-created", alert_id))

        db.commit()
        db.close()

        self.log_audit(user, "SAR Auto-Trigger", sar_ref, f"SAR auto-created from alert #{alert_id}")
        self.success({"id": sar_id, "sar_reference": sar_ref, "status": "draft"}, 201)


# ══════════════════════════════════════════════════════════
# EDD (ENHANCED DUE DILIGENCE) PIPELINE ENDPOINTS
# ══════════════════════════════════════════════════════════

class EDDListHandler(BaseHandler):
    """GET /api/edd/cases — List EDD cases; POST — Create a new EDD case"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        from fixture_filter import (
            fixture_app_id_exclude_clause,
            should_show_fixtures,
        )

        stage = self.get_argument("stage", None)
        assigned = self.get_argument("assigned_officer", None)
        show_fx = should_show_fixtures(user, self.get_argument("show_fixtures", None))

        db = get_db()
        if show_fx:
            query = "SELECT * FROM edd_cases WHERE 1=1"
            params = []
        else:
            fx_excl, fx_params = fixture_app_id_exclude_clause("application_id")
            # edd_cases.application_id is NOT NULL, but use shared helper for
            # consistency (the NULL-safe guard is a no-op here).
            query = f"SELECT * FROM edd_cases WHERE {fx_excl}"
            params = list(fx_params)

        if stage:
            query += " AND stage = ?"
            params.append(stage)
        if assigned:
            query += " AND assigned_officer = ?"
            params.append(assigned)

        query += " ORDER BY triggered_at DESC"
        cases = db.execute(query, params).fetchall()
        result = []
        for c in cases:
            row = dict(c)
            row["edd_notes"] = safe_json_loads(row.get("edd_notes", "[]"))
            result.append(row)
        db.close()
        self.success({"cases": result, "total": len(result)})

    def post(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()
        application_id = data.get("application_id")
        if not application_id:
            return self.error("application_id is required", 400)

        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        # Check if EDD case already exists for this application
        existing = db.execute("SELECT id FROM edd_cases WHERE application_id = ? AND stage NOT IN ('edd_approved','edd_rejected')", (application_id,)).fetchone()
        if existing:
            db.close()
            return self.success({"existing": True, "id": existing["id"]})

        trigger_notes = data.get("trigger_notes", "EDD triggered by officer decision")
        initial_note = json.dumps([{
            "ts": datetime.now().isoformat(),
            "author": user.get("name", "System"),
            "note": trigger_notes
        }])

        insert_params = (application_id, app["company_name"], app.get("risk_level", "HIGH"), app.get("risk_score", 0),
              "triggered", user["sub"], data.get("trigger_source", "officer_decision"), trigger_notes, initial_note)

        if USE_POSTGRES:
            row = db.execute("""
                INSERT INTO edd_cases (application_id, client_name, risk_level, risk_score, stage, assigned_officer, trigger_source, trigger_notes, edd_notes)
                VALUES (?,?,?,?,?,?,?,?,?) RETURNING id
            """, insert_params).fetchone()
            case_id = row["id"]
        else:
            db.execute("""
                INSERT INTO edd_cases (application_id, client_name, risk_level, risk_score, stage, assigned_officer, trigger_source, trigger_notes, edd_notes)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, insert_params)
            case_id = db.execute("SELECT last_insert_rowid() as id").fetchone()["id"]

        db.commit()
        db.close()

        self.log_audit(user, "EDD Created", app["ref"], f"EDD case created for {app['company_name']}")
        self.success({"id": case_id, "status": "created"}, 201)


class EDDDetailHandler(BaseHandler):
    """GET/PATCH /api/edd/cases/:id — Get or update EDD case"""
    def get(self, case_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        db = get_db()
        case = db.execute("SELECT * FROM edd_cases WHERE id = ?", (case_id,)).fetchone()
        if not case:
            db.close()
            return self.error("EDD case not found", 404)

        result = dict(case)
        result["edd_notes"] = safe_json_loads(result.get("edd_notes", "[]"))
        db.close()
        self.success(result)

    def patch(self, case_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()
        db = get_db()

        case = db.execute("SELECT * FROM edd_cases WHERE id = ?", (case_id,)).fetchone()
        if not case:
            db.close()
            return self.error("EDD case not found", 404)

        # Prevent updates on closed cases
        if case["stage"] in ("edd_approved", "edd_rejected"):
            db.close()
            return self.error(f"EDD case is already {case['stage']}. Cannot modify.", 409)

        new_stage = data.get("stage")
        valid_stages = ["triggered", "information_gathering", "analysis", "pending_senior_review", "edd_approved", "edd_rejected"]

        if new_stage and new_stage not in valid_stages:
            db.close()
            return self.error(f"Invalid stage. Must be one of: {', '.join(valid_stages)}", 400)

        # Stage transition validation
        valid_transitions = {
            "triggered": ["information_gathering", "analysis", "edd_rejected"],
            "information_gathering": ["analysis", "edd_rejected"],
            "analysis": ["pending_senior_review", "edd_rejected"],
            "pending_senior_review": ["edd_approved", "edd_rejected", "analysis"],
        }

        if new_stage and new_stage != case["stage"]:
            allowed = valid_transitions.get(case["stage"], [])
            if new_stage not in allowed:
                db.close()
                return self.error(f"Invalid transition: {case['stage']} → {new_stage}. Allowed: {', '.join(allowed)}", 400)

        # Build update fields
        updates = ["updated_at=datetime('now')"]
        params = []

        if new_stage:
            updates.append("stage=?")
            params.append(new_stage)

        if data.get("assigned_officer"):
            updates.append("assigned_officer=?")
            params.append(data["assigned_officer"])

        if data.get("senior_reviewer"):
            updates.append("senior_reviewer=?")
            params.append(data["senior_reviewer"])

        # Handle decision for terminal stages
        if new_stage in ("edd_approved", "edd_rejected"):
            decision_reason = data.get("decision_reason", "")
            if not decision_reason:
                db.close()
                return self.error("decision_reason is required for approval/rejection", 400)
            updates.append("decision=?")
            params.append(new_stage)
            updates.append("decision_reason=?")
            params.append(decision_reason)
            updates.append("decided_by=?")
            params.append(user["sub"])
            updates.append("decided_at=datetime('now')")

        params.append(case_id)
        db.execute(f"UPDATE edd_cases SET {', '.join(updates)} WHERE id=?", params)

        # Append note if provided
        note_text = data.get("note")
        if note_text:
            existing_notes = safe_json_loads(case.get("edd_notes", "[]"))
            existing_notes.append({
                "ts": datetime.now().isoformat(),
                "author": user.get("name", "System"),
                "note": note_text
            })
            db.execute("UPDATE edd_cases SET edd_notes=? WHERE id=?", (json.dumps(existing_notes), case_id))

        # Audit trail
        detail = f"Stage: {case['stage']} → {new_stage}" if new_stage else "EDD case updated"
        if note_text:
            detail += f" | Note: {note_text[:100]}"
        self.log_audit(user, "EDD Update", f"EDD-{case_id}", detail, db=db)

        db.commit()
        db.close()

        self.success({"status": "updated", "stage": new_stage or case["stage"]})


class EDDStatsHandler(BaseHandler):
    """GET /api/edd/stats — Get EDD pipeline statistics"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        from fixture_filter import fixture_app_id_exclude_clause

        db = get_db()
        fx_excl, fx_params = fixture_app_id_exclude_clause("application_id")
        active = db.execute(
            f"SELECT COUNT(*) as c FROM edd_cases WHERE stage NOT IN ('edd_approved','edd_rejected') AND {fx_excl}",
            fx_params,
        ).fetchone()["c"]
        pending_senior = db.execute(
            f"SELECT COUNT(*) as c FROM edd_cases WHERE stage = 'pending_senior_review' AND {fx_excl}",
            fx_params,
        ).fetchone()["c"]
        if USE_POSTGRES:
            completed_month = db.execute(f"""
                SELECT COUNT(*) as c FROM edd_cases
                WHERE stage IN ('edd_approved','edd_rejected') AND decided_at >= date_trunc('month', CURRENT_DATE)
                AND {fx_excl}
            """, fx_params).fetchone()["c"]
        else:
            completed_month = db.execute(f"""
                SELECT COUNT(*) as c FROM edd_cases
                WHERE stage IN ('edd_approved','edd_rejected') AND decided_at >= date('now','start of month')
                AND {fx_excl}
            """, fx_params).fetchone()["c"]
        db.close()

        self.success({
            "active": active,
            "pending_senior_review": pending_senior,
            "completed_this_month": completed_month
        })


# ══════════════════════════════════════════════════════════
# PR-05: LIFECYCLE QUEUE CLARITY ENDPOINTS (additive, read-only)
# ══════════════════════════════════════════════════════════
# These endpoints expose a single coherent operational view over the
# three lifecycle object types (monitoring alerts, periodic reviews,
# EDD cases) so the back-office can answer "what needs attention now,
# who owns it, how old is it, what is it linked to?" without
# scattering five separate API calls. Read-only; no DB mutation; no
# semantic change to existing endpoints. Delegates to lifecycle_queue.

class LifecycleQueueHandler(BaseHandler):
    """GET /api/lifecycle/queue — unified lifecycle work queue.

    Query params (all optional):
      include       : 'active' (default), 'historical', or 'all'
      type          : 'alerts', 'reviews', 'edd', or 'all' (default)
                      also accepted: comma-separated list of item types
                      (alert / review / edd) for finer control
      application_id: scope to a single application
      show_fixtures : 'true' to include fixture rows (admin/sco only;
                      silently ignored for other roles)

    Response: ``{"items": [...], "counts": {...}, "filter": {...}}``.
    Items carry: type, state, owner, age, linkage, next-action hint,
    plus type-specific fields (severity, outcome, memo_context, ...).
    """
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        from fixture_filter import should_show_fixtures

        include = (self.get_argument("include", "active") or "active").lower()
        if include not in ("active", "historical", "all", "legacy_unmapped"):
            return self.error(
                "include must be one of: active, historical, all, legacy_unmapped", 400,
            )

        # Accept either a single 'type' or 'types' (alerts/reviews/edd/all)
        # plus comma-separated value for friendliness.
        type_arg = (self.get_argument("type", None)
                    or self.get_argument("types", None))
        types = None
        if type_arg:
            raw = [t.strip().lower() for t in type_arg.split(",") if t.strip()]
            if "all" in raw:
                types = None
            else:
                # Accept both 'alerts'/'reviews'/'edd' (plural friendly) and
                # canonical 'alert'/'review'/'edd' singular.
                singular = {"alerts": "alert", "reviews": "review", "edd": "edd"}
                normalised = []
                for t in raw:
                    canonical = singular.get(t, t)
                    if canonical not in ("alert", "review", "edd"):
                        return self.error(
                            "type must be one of: alerts, reviews, edd, all",
                            400,
                        )
                    normalised.append(canonical)
                types = tuple(normalised)

        application_id = self.get_argument("application_id", None) or None
        show_fx = should_show_fixtures(user, self.get_argument("show_fixtures", None))

        import lifecycle_queue as lq
        db = get_db()
        try:
            try:
                result = lq.build_lifecycle_queue(
                    db,
                    include=include,
                    types=types,
                    application_id=application_id,
                    exclude_fixtures=not show_fx,
                )
            except ValueError as exc:
                return self.error(str(exc), 400)
            self.success(result)
        finally:
            db.close()


class LifecycleApplicationSummaryHandler(BaseHandler):
    """GET /api/lifecycle/applications/:application_id/summary

    Per-application lifecycle linkage view. Returns active and
    historical lifecycle objects plus the explicit linkage edge set
    (alert<->review, alert<->edd, review<->edd) so the application
    detail surface can show "what lifecycle objects exist for this
    client and how are they connected" in one read.
    """
    def get(self, application_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return
        if not application_id:
            return self.error("application_id is required", 400)

        import lifecycle_queue as lq
        db = get_db()
        try:
            # Sanity-check that the application exists -- 404 is a more
            # useful signal than an empty payload.
            row = db.execute(
                "SELECT id FROM applications WHERE id = ?", (application_id,)
            ).fetchone()
            if not row:
                return self.error("Application not found", 404)
            try:
                result = lq.build_application_lifecycle_summary(
                    db, application_id,
                )
            except ValueError as exc:
                return self.error(str(exc), 400)
            self.success(result)
        finally:
            db.close()


# ══════════════════════════════════════════════════════════
# AI ASSISTANT ENDPOINT
# ══════════════════════════════════════════════════════════

class AIAssistantHandler(BaseHandler):
    """POST /api/ai/assistant — AI assistant for compliance topics"""
    def post(self):
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        message = data.get("message", "").strip()

        if not message:
            self.error("Message cannot be empty", 400)
            return

        # Simulated AI response about compliance topics
        # In production, this would integrate with an LLM API
        compliance_keywords = ["kyc", "aml", "sanctions", "pep", "risk", "screening", "compliance", "due diligence"]
        is_compliance_topic = any(kw in message.lower() for kw in compliance_keywords)

        if is_compliance_topic:
            response = self._get_compliance_response(message)
        else:
            response = "I can help with compliance and KYC-related questions. Please ask about AML, sanctions screening, PEP verification, risk assessment, or due diligence procedures."

        self.success({"response": response, "topic": "compliance" if is_compliance_topic else "general"})

    def _get_compliance_response(self, message):
        """Return contextual compliance guidance based on the message"""
        msg_lower = message.lower()

        if any(w in msg_lower for w in ["kyc", "know your customer"]):
            return "KYC (Know Your Customer) involves verifying customer identity, conducting beneficial ownership analysis, and assessing risk profile. Core elements: identity verification, address verification, source of funds, business purpose, and beneficial ownership structure."

        elif any(w in msg_lower for w in ["sanctions", "screening"]):
            return "Sanctions and PEP screening is mandatory. Check against OFAC, UN, EU sanctions lists and PEP databases. Document all screening results. Update screening annually or when customer information changes. Maintain audit trail of all checks."

        elif any(w in msg_lower for w in ["pep", "politically exposed"]):
            return "PEP (Politically Exposed Persons) are individuals holding prominent public positions. Enhanced due diligence required including: source of wealth verification, beneficial ownership analysis, and ongoing monitoring. Document political exposure and relationships."

        elif any(w in msg_lower for w in ["risk", "assessment"]):
            return "Risk assessment evaluates customer risk across dimensions: entity risk, geographic risk, product risk, sector risk. Consider: entity type, ownership structure, jurisdiction, business model, transaction patterns. Document risk rating and mitigation measures."

        elif any(w in msg_lower for w in ["ubo", "beneficial owner"]):
            return "Ultimate Beneficial Owner (UBO) identification requires mapping full ownership chain to identify natural persons. Detect nominee structures, complex layering, and high-risk jurisdictions. Verify UBO identity and screen against sanctions/PEP lists."

        elif any(w in msg_lower for w in ["aml", "anti-money laundering"]):
            return "AML compliance requires: customer due diligence, sanctions screening, ongoing monitoring, transaction monitoring, suspicious activity reporting, and record retention. Maintain policies, procedures, and staff training programs."

        else:
            return "I can assist with compliance questions including KYC procedures, sanctions screening, PEP verification, risk assessment, UBO identification, and AML compliance. What specific area would you like to know more about?"


# ══════════════════════════════════════════════════════════
# CHANGE MANAGEMENT ENDPOINTS
# ══════════════════════════════════════════════════════════

# Import change management module (safe — additive only)
try:
    import change_management as cm
    HAS_CHANGE_MANAGEMENT = True
except ImportError:
    HAS_CHANGE_MANAGEMENT = False
    cm = None


class ChangeAlertsListHandler(BaseHandler):
    """GET/POST /api/change-management/alerts — List and create change alerts"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        application_id = self.get_argument("application_id", None)
        status = self.get_argument("status", None)
        limit = int(self.get_argument("limit", "50"))
        offset = int(self.get_argument("offset", "0"))

        db = get_db()
        try:
            alerts = cm.list_change_alerts(db, application_id=application_id,
                                           status=status, limit=limit, offset=offset)
            self.success({"alerts": alerts, "total": len(alerts)})
        finally:
            db.close()

    def post(self):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        allowed, err = cm.check_role_permission(user.get("role", ""), "create_alert")
        if not allowed:
            self.error(err, 403)
            return

        data = self.get_json()
        application_id = data.get("application_id")
        if not application_id:
            self.error("application_id is required", 400)
            return

        db = get_db()
        try:
            # Verify application exists
            app = db.execute("SELECT id FROM applications WHERE id = ?", (application_id,)).fetchone()
            if not app:
                self.error("Application not found", 404)
                return

            alert = cm.create_change_alert(
                db=db,
                application_id=application_id,
                alert_type=data.get("alert_type", "other"),
                source_channel=data.get("source_channel", "backoffice"),
                summary=data.get("summary", ""),
                detected_changes=data.get("detected_changes", {}),
                confidence=data.get("confidence"),
                source_reference=data.get("source_reference"),
                source_payload=data.get("source_payload"),
                detected_by=data.get("detected_by", user.get("name")),
                user=user,
                log_audit_fn=self.log_audit,
            )
            self.success(alert, 201)
        finally:
            db.close()


class ChangeAlertDetailHandler(BaseHandler):
    """GET/PATCH /api/change-management/alerts/:id — Alert detail and status update"""
    def get(self, alert_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        db = get_db()
        try:
            alert = cm.get_change_alert_detail(db, alert_id)
            if not alert:
                self.error("Alert not found", 404)
                return
            self.success(alert)
        finally:
            db.close()

    def patch(self, alert_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        data = self.get_json()
        new_status = data.get("status")
        if not new_status:
            self.error("status is required", 400)
            return

        db = get_db()
        try:
            success, err = cm.update_change_alert_status(
                db, alert_id, new_status, user,
                notes=data.get("notes"),
                log_audit_fn=self.log_audit,
            )
            if not success:
                self.error(err, 400)
                return
            self.success({"status": "updated", "new_status": new_status})
        finally:
            db.close()


class ChangeAlertConvertHandler(BaseHandler):
    """POST /api/change-management/alerts/:id/convert — Convert alert to request"""
    def post(self, alert_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        allowed, err = cm.check_role_permission(user.get("role", ""), "convert_alert")
        if not allowed:
            self.error(err, 403)
            return

        data = self.get_json() if self.request.body else {}

        db = get_db()
        try:
            request, err = cm.convert_alert_to_request(
                db, alert_id, user,
                additional_notes=data.get("notes"),
                items=data.get("items"),
                log_audit_fn=self.log_audit,
            )
            if not request:
                self.error(err, 400)
                return
            self.success(request, 201)
        except ValueError as ve:
            self.error(str(ve), 400)
            return
        finally:
            db.close()


class ChangeRequestsListHandler(BaseHandler):
    """GET/POST /api/change-management/requests — List and create change requests"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        application_id = self.get_argument("application_id", None)
        status = self.get_argument("status", None)
        materiality = self.get_argument("materiality", None)
        source = self.get_argument("source", None)
        limit = int(self.get_argument("limit", "50"))
        offset = int(self.get_argument("offset", "0"))

        db = get_db()
        try:
            requests_list = cm.list_change_requests(
                db, application_id=application_id, status=status,
                materiality=materiality, source=source,
                limit=limit, offset=offset,
            )
            self.success({"requests": requests_list, "total": len(requests_list)})
        finally:
            db.close()

    def post(self):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        allowed, err = cm.check_role_permission(user.get("role", ""), "create_request")
        if not allowed:
            self.error(err, 403)
            return

        data = self.get_json()
        application_id = data.get("application_id")
        if not application_id:
            self.error("application_id is required", 400)
            return

        db = get_db()
        try:
            app = db.execute("SELECT id FROM applications WHERE id = ?", (application_id,)).fetchone()
            if not app:
                self.error("Application not found", 404)
                return

            items = data.get("items", [])
            if not items:
                # Reject legacy top-level field/new_value payloads and empty creates
                if "field" in data or "new_value" in data or "change_type" in data:
                    self.error(
                        "Legacy top-level field/new_value payload is not supported. "
                        "Provide an 'items' array instead.",
                        400,
                    )
                else:
                    self.error("At least one change item is required in 'items' array", 400)
                return
            try:
                request = cm.create_change_request(
                    db=db,
                    application_id=application_id,
                    source=data.get("source", "backoffice_manual"),
                    source_channel=data.get("source_channel", "backoffice"),
                    reason=data.get("reason", ""),
                    items=items,
                    user=user,
                    log_audit_fn=self.log_audit,
                )
            except PermissionError as pe:
                self.error(str(pe), 403)
                return
            except ValueError as ve:
                self.error(str(ve), 400)
                return
            self.success(request, 201)
        finally:
            db.close()


class ChangeRequestDetailHandler(BaseHandler):
    """GET/PATCH /api/change-management/requests/:id — Request detail and status"""
    def get(self, request_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        db = get_db()
        try:
            detail = cm.get_change_request_detail(db, request_id)
            if not detail:
                self.error("Request not found", 404)
                return
            self.success(detail)
        finally:
            db.close()

    def patch(self, request_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        data = self.get_json()
        new_status = data.get("status")
        if not new_status:
            self.error("status is required", 400)
            return

        db = get_db()
        try:
            success, err = cm.update_change_request_status(
                db, request_id, new_status, user,
                notes=data.get("notes"),
                log_audit_fn=self.log_audit,
            )
            if not success:
                status_code = 403 if "not permitted" in err.lower() else 400
                self.error(err, status_code)
                return
            self.success({"status": "updated", "new_status": new_status})
        finally:
            db.close()


class ChangeRequestSubmitHandler(BaseHandler):
    """POST /api/change-management/requests/:id/submit — Submit a draft request"""
    def post(self, request_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        db = get_db()
        try:
            success, err = cm.submit_change_request(
                db, request_id, user, log_audit_fn=self.log_audit,
            )
            if not success:
                status_code = 403 if "not permitted" in err.lower() else 400
                self.error(err, status_code)
                return
            self.success({"status": "submitted"})
        finally:
            db.close()


class ChangeRequestApproveHandler(BaseHandler):
    """POST /api/change-management/requests/:id/approve — Approve a request"""
    def post(self, request_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        data = self.get_json() if self.request.body else {}

        db = get_db()
        try:
            success, err = cm.approve_change_request(
                db, request_id, user,
                decision_notes=data.get("decision_notes"),
                log_audit_fn=self.log_audit,
            )
            if not success:
                self.error(err, 400 if "not found" not in err.lower() else 404)
                return
            self.success({"status": "approved"})
        finally:
            db.close()


class ChangeRequestRejectHandler(BaseHandler):
    """POST /api/change-management/requests/:id/reject — Reject a request"""
    def post(self, request_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        data = self.get_json() if self.request.body else {}

        db = get_db()
        try:
            success, err = cm.reject_change_request(
                db, request_id, user,
                decision_notes=data.get("decision_notes"),
                log_audit_fn=self.log_audit,
            )
            if not success:
                if "not permitted" in err.lower():
                    status_code = 403
                elif "not found" in err.lower():
                    status_code = 404
                else:
                    status_code = 400
                self.error(err, status_code)
                return
            self.success({"status": "rejected"})
        finally:
            db.close()


class ChangeRequestImplementHandler(BaseHandler):
    """POST /api/change-management/requests/:id/implement — Implement approved changes"""
    def post(self, request_id):
        user = self.require_auth(roles=["admin", "sco"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        # Import risk recomputation function
        recompute_risk_fn = None
        try:
            from rule_engine import recompute_risk
            recompute_risk_fn = recompute_risk
        except ImportError:
            pass

        db = get_db()
        try:
            success, err, version_id = cm.implement_change_request(
                db, request_id, user,
                log_audit_fn=self.log_audit,
                recompute_risk_fn=recompute_risk_fn,
            )
            if not success:
                if "not permitted" in err.lower() or "not authorized" in err.lower():
                    status_code = 403
                elif "stale" in err.lower() or "conflict" in err.lower() or "version" in err.lower():
                    status_code = 409
                else:
                    status_code = 400
                self.error(err, status_code)
                return
            self.success({"status": "implemented", "profile_version_id": version_id})
        finally:
            db.close()


class ChangeRequestDocumentHandler(BaseHandler):
    """POST /api/change-management/requests/:id/documents — Upload supporting doc"""
    def post(self, request_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        db = get_db()
        try:
            # Verify request exists
            row = db.execute("SELECT id FROM change_requests WHERE id = ?", (request_id,)).fetchone()
            if not row:
                self.error("Request not found", 404)
                return

            files = self.request.files.get("file", [])
            if not files:
                self.error("No file uploaded", 400)
                return

            uploaded = files[0]
            doc_type = self.get_argument("doc_type", "supporting_document")
            item_id = self.get_argument("item_id", None)

            # Validate file
            if HAS_SECURITY_HARDENING:
                valid, validation_err = FileUploadValidator.validate_upload(
                    uploaded["filename"], uploaded["body"]
                )
                if not valid:
                    self.error(validation_err, 400)
                    return

            # Save file — sanitize path components to prevent path traversal
            import re as _re
            safe_request_id = _re.sub(r'[^a-zA-Z0-9\-_]', '', request_id)
            safe_filename = Path(uploaded["filename"]).name  # strip directory components
            safe_filename = _re.sub(r'[^a-zA-Z0-9\-_.]', '_', safe_filename)
            if not safe_filename or safe_filename.startswith('.'):
                self.error("Invalid filename", 400)
                return
            upload_dir = Path(_CFG_UPLOAD_DIR) / "change_requests" / safe_request_id
            upload_dir.mkdir(parents=True, exist_ok=True)
            file_path = upload_dir / safe_filename
            # Verify resolved path is under upload_dir
            if not str(file_path.resolve()).startswith(str(upload_dir.resolve())):
                self.error("Invalid file path", 400)
                return
            with open(file_path, "wb") as f:
                f.write(uploaded["body"])

            doc = cm.attach_document_to_request(
                db, request_id, uploaded["filename"], doc_type,
                str(file_path), item_id=item_id,
                uploaded_by=user.get("sub"),
            )

            self.log_audit(user, "Change Request Document Uploaded", request_id,
                          f"Document: {uploaded['filename']}, type: {doc_type}")
            self.success(doc, 201)
        finally:
            db.close()


class ChangeManagementStatsHandler(BaseHandler):
    """GET /api/change-management/stats — Dashboard statistics"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        db = get_db()
        try:
            stats = cm.get_change_management_stats(db)
            self.success(stats)
        finally:
            db.close()


class EntityProfileVersionsHandler(BaseHandler):
    """GET /api/applications/:id/profile-versions — List profile versions"""
    def get(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        db = get_db()
        try:
            versions = cm.get_profile_versions(db, app_id)
            self.success({"versions": versions, "total": len(versions)})
        finally:
            db.close()


class EntityProfileVersionDetailHandler(BaseHandler):
    """GET /api/profile-versions/:id — Get profile version detail with snapshot"""
    def get(self, version_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        db = get_db()
        try:
            version = cm.get_profile_version_detail(db, version_id)
            if not version:
                self.error("Version not found", 404)
                return
            self.success(version)
        finally:
            db.close()


class ApplicationProfileVersionDetailHandler(BaseHandler):
    """GET /api/applications/:app_id/profile-versions/:version_id — Scoped version detail"""
    def get(self, app_id, version_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        db = get_db()
        try:
            version = cm.get_profile_version_detail(db, version_id)
            if not version or version.get("application_id") != app_id:
                self.error("Version not found", 404)
                return
            self.success(version)
        finally:
            db.close()


class PortalApplicationsHandler(BaseHandler):
    """GET /api/portal/applications — List only client-owned applications"""
    def get(self):
        user = self.require_auth()
        if not user:
            return

        client_id = user.get("sub")
        db = get_db()
        try:
            rows = db.execute(
                "SELECT id, ref, company_name, status FROM applications WHERE client_id = ? ORDER BY created_at DESC",
                (client_id,),
            ).fetchall()
            apps = [dict(r) for r in rows]
            self.success({"applications": apps, "total": len(apps)})
        finally:
            db.close()


class PortalChangeRequestHandler(BaseHandler):
    """POST /api/portal/change-requests — Client creates a change request from portal"""
    def get(self):
        """List client's own change requests."""
        user = self.require_auth()
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        db = get_db()
        try:
            # Get client's applications
            client_id = user.get("sub")
            apps = db.execute(
                "SELECT id FROM applications WHERE client_id = ?", (client_id,)
            ).fetchall()
            app_ids = [a["id"] for a in apps]

            all_requests = []
            for app_id in app_ids:
                reqs = cm.list_change_requests(db, application_id=app_id)
                all_requests.extend(reqs)

            self.success({"requests": all_requests, "total": len(all_requests)})
        finally:
            db.close()

    def post(self):
        """Client creates a new change request."""
        user = self.require_auth()
        if not user:
            return
        if not HAS_CHANGE_MANAGEMENT:
            self.error("Change management module not available", 503)
            return

        data = self.get_json()
        application_id = data.get("application_id")
        if not application_id:
            self.error("application_id is required", 400)
            return

        db = get_db()
        try:
            client_id = user.get("sub")

            # Step 1: Check application exists
            app = db.execute(
                "SELECT id, status, client_id FROM applications WHERE id = ?",
                (application_id,),
            ).fetchone()
            if not app:
                logger.warning(
                    "Portal CR denied: app not found | client=%s app=%s",
                    client_id, application_id,
                )
                try:
                    self.log_audit(
                        user, "portal_change_request_denied",
                        application_id,
                        json.dumps({"reason": "not_found", "client_id": client_id,
                                    "attempted_application_id": application_id}),
                        db=db,
                    )
                except Exception:
                    pass  # audit best-effort
                self.error("Application not found", 404)
                return

            # Step 2: Verify client owns this application
            # Same ownership predicate as GET /api/portal/applications:
            #   WHERE client_id = <authenticated_client_id>
            if app["client_id"] != client_id:
                logger.warning(
                    "Portal CR denied: not owner | client=%s app=%s owner=%s",
                    client_id, application_id, app["client_id"],
                )
                try:
                    self.log_audit(
                        user, "portal_cr_denied_not_owner",
                        application_id,
                        json.dumps({"reason": "not_owner", "client_id": client_id,
                                    "attempted_application_id": application_id,
                                    "actual_owner": app["client_id"]}),
                        db=db,
                    )
                except Exception:
                    pass  # audit best-effort
                self.error("You do not have permission to create a change request for this application", 403)
                return

            items = data.get("items", [])
            if not items:
                self.error("At least one change item is required", 400)
                return

            try:
                request = cm.create_change_request(
                    db=db,
                    application_id=application_id,
                    source="portal_client",
                    source_channel="portal",
                    reason=data.get("reason", ""),
                    items=items,
                    user=user,
                    log_audit_fn=self.log_audit,
                )
            except PermissionError as pe:
                self.error(str(pe), 403)
                return
            except ValueError as ve:
                self.error(str(ve), 400)
                return

            # Auto-submit portal requests
            ok, submit_err = cm.submit_change_request(db, request["id"], user, log_audit_fn=self.log_audit)
            if ok:
                request["status"] = "submitted"
            else:
                logger.warning("Portal CR %s auto-submit failed: %s", request["id"], submit_err)

            self.success(request, 201)
        except Exception:
            logger.exception("Unhandled error in portal change request creation")
            self.error("Internal server error while creating change request", 500)
        finally:
            db.close()


# ══════════════════════════════════════════════════════════
# APP SETUP & ROUTES
# ══════════════════════════════════════════════════════════

def make_app():
    routes = [
        # Health & Readiness
        (r"/api/health", HealthHandler),
        (r"/api/readiness", ReadinessHandler),
        (r"/api/admin/reset-db", AdminResetDBHandler),
        (r"/api/admin/reset-password", AdminResetPasswordHandler),
        (r"/api/admin/officer-reset-password", AdminOfficerPasswordResetHandler),

        # Auth
        (r"/api/auth/officer/login", OfficerLoginHandler),
        (r"/api/auth/client/login", ClientLoginHandler),
        (r"/api/auth/client/register", ClientRegisterHandler),
        (r"/api/auth/client/forgot-password", ForgotPasswordHandler),
        (r"/api/auth/client/reset-password", ResetPasswordHandler),
        (r"/api/auth/client/change-password", ClientChangePasswordHandler),
        (r"/api/auth/logout", LogoutHandler),
        (r"/api/auth/me", MeHandler),

        # Applications (more specific routes first)
        (r"/api/applications/([^/]+)/submit", SubmitApplicationHandler),
        (r"/api/applications/([^/]+)/accept-pricing", PricingAcceptHandler),
        (r"/api/applications/([^/]+)/pre-approval-decision", PreApprovalDecisionHandler),
        (r"/api/applications/([^/]+)/submit-kyc", KYCSubmitHandler),
        (r"/api/applications/([^/]+)/supervisor/run", SupervisorRunHandler),
        (r"/api/applications/([^/]+)/supervisor/result", SupervisorResultHandler),
        (r"/api/applications/([^/]+)/memo/validate", MemoValidateHandler),
        (r"/api/applications/([^/]+)/memo/approve", MemoApproveHandler),
        (r"/api/applications/([^/]+)/memo/validation", MemoValidationResultsHandler),
        (r"/api/applications/([^/]+)/memo/pdf", MemoPDFDownloadHandler),
        (r"/api/applications/([^/]+)/memo/supervisor/run", MemoSupervisorHandler),
        (r"/api/applications/([^/]+)/memo/supervisor", MemoSupervisorResultHandler),
        (r"/api/applications/([^/]+)/memo", ComplianceMemoHandler),
        (r"/api/applications/([^/]+)/decision", ApplicationDecisionHandler),
        (r"/api/applications/([^/]+)/decision-records", DecisionRecordsHandler),
        (r"/api/applications/([^/]+)/audit-log", ApplicationAuditLogHandler),
        (r"/api/applications/([^/]+)/notes", ApplicationNotesHandler),
        (r"/api/applications/([^/]+)/notify", ClientNotificationHandler),
        (r"/api/applications/([^/]+)/documents/([^/]+)", DocumentDeleteHandler),
        (r"/api/applications/([^/]+)/documents", DocumentUploadHandler),
        (r"/api/applications/([^/]+)", ApplicationDetailHandler),
        (r"/api/applications", ApplicationsHandler),

        # Documents
        (r"/api/documents/([^/]+)/download", DocumentDownloadHandler),
        (r"/api/documents/([^/]+)/verify", DocumentVerifyHandler),
        (r"/api/documents/([^/]+)/review", DocumentReviewHandler),
        (r"/api/documents/ai-verify", DocumentAIVerifyHandler),
        (r"/api/resources/([^/]+)/download", ComplianceResourceDownloadHandler),
        (r"/api/resources", ComplianceResourcesHandler),
        (r"/api/regulatory-intelligence/([^/]+)/download", RegulatoryIntelligenceDownloadHandler),
        (r"/api/regulatory-intelligence/([^/]+)/source-text", RegulatoryIntelligenceSourceTextHandler),
        (r"/api/regulatory-intelligence/([^/]+)/review", RegulatoryIntelligenceReviewHandler),
        (r"/api/regulatory-intelligence", RegulatoryIntelligenceHandler),

        # Users
        (r"/api/users", UsersHandler),
        (r"/api/users/([^/]+)", UserDetailHandler),

        # Config
        (r"/api/config/risk-model", RiskConfigHandler),
        (r"/api/config/system-settings", SystemSettingsHandler),
        (r"/api/config/roles-permissions", RolesPermissionsHandler),
        (r"/api/config/ai-agents", AIAgentsHandler),
        (r"/api/config/ai-agents/([^/]+)", AIAgentDetailHandler),
        (r"/api/config/verification-checks", VerificationChecksHandler),
        (r"/api/config/environment", EnvironmentInfoHandler),
        (r"/api/version", VersionHandler),

        # Screening (Real API Integrations)
        (r"/api/screening/run", ScreeningHandler),
        (r"/api/screening/queue", ScreeningQueueHandler),
        (r"/api/screening/review", ScreeningReviewHandler),
        (r"/api/screening/sanctions", SanctionsCheckHandler),
        (r"/api/screening/company", CompanyLookupHandler),
        (r"/api/screening/ip", IPCheckHandler),
        (r"/api/screening/status", APIStatusHandler),

        # Sumsub KYC
        (r"/api/kyc/applicant", SumsubApplicantHandler),
        (r"/api/kyc/token", SumsubAccessTokenHandler),
        (r"/api/kyc/status/([^/]+)", SumsubStatusHandler),
        (r"/api/kyc/document", SumsubDocumentHandler),
        (r"/api/kyc/webhook", SumsubWebhookHandler),
        (r"/api/webhooks/complyadvantage", ComplyAdvantageWebhookHandler),

        # Sumsub Diagnostics (admin only)
        (r"/api/admin/sumsub-diagnostics", SumsubDiagnosticsHandler),

        # Reports
        (r"/api/reports/generate", ReportHandler),
        (r"/api/reports/analytics", ReportAnalyticsHandler),

        # Audit
        (r"/api/audit/export", AuditExportHandler),
        (r"/api/audit/supervisor/export", SupervisorAuditExportHandler),
        (r"/api/audit", AuditHandler),

        # Dashboard
        (r"/api/dashboard", DashboardHandler),

        # Client Notifications
        (r"/api/notifications", GetClientNotificationsHandler),
        (r"/api/notifications/([^/]+)/read", MarkNotificationReadHandler),
        (r"/api/status-lookup", ClientStatusLookupHandler),

        # Monitoring
        (r"/api/monitoring/dashboard", MonitoringDashboardHandler),
        (r"/api/monitoring/clients", MonitoringClientsHandler),
        # Alerts (more specific routes first)
        (r"/api/monitoring/alerts/([^/]+)", MonitoringAlertDetailHandler),
        (r"/api/monitoring/alerts", MonitoringAlertCreateHandler),
        # Agents
        (r"/api/monitoring/agents/([^/]+)/run", MonitoringAgentRunHandler),
        (r"/api/monitoring/agents", MonitoringAgentsHandler),
        # Periodic Reviews (more specific routes first)
        (r"/api/monitoring/reviews/schedule", PeriodicReviewScheduleHandler),
        (r"/api/monitoring/reviews/([^/]+)/required-items/generate",
         PeriodicReviewRequiredItemsGenerateHandler),
        (r"/api/monitoring/reviews/([^/]+)/required-items",
         PeriodicReviewRequiredItemsHandler),
        (r"/api/monitoring/reviews/([^/]+)/state", PeriodicReviewStateHandler),
        (r"/api/monitoring/reviews/([^/]+)/escalate", PeriodicReviewEscalateHandler),
        (r"/api/monitoring/reviews/([^/]+)/complete", PeriodicReviewCompleteHandler),
        (r"/api/monitoring/reviews/([^/]+)/decision", PeriodicReviewDecisionHandler),
        (r"/api/monitoring/reviews/([^/]+)", PeriodicReviewDetailHandler),
        (r"/api/monitoring/reviews", PeriodicReviewsListHandler),

        # PR-D: Lightweight periodic review memo artifact (read-only)
        (r"/api/periodic-reviews/([^/]+)/memo", PeriodicReviewMemoHandler),

        # SAR (Suspicious Activity Reports)
        (r"/api/sar/auto-trigger", SARAutoTriggerHandler),
        (r"/api/sar/([^/]+)/workflow", SARWorkflowHandler),
        (r"/api/sar/([^/]+)", SARDetailHandler),
        (r"/api/sar", SARListHandler),

        # EDD Pipeline
        (r"/api/edd/stats", EDDStatsHandler),
        (r"/api/edd/cases/([^/]+)", EDDDetailHandler),
        (r"/api/edd/cases", EDDListHandler),

        # PR-05: Lifecycle queue clarity (additive, read-only)
        (r"/api/lifecycle/queue", LifecycleQueueHandler),
        (r"/api/lifecycle/applications/([^/]+)/summary",
         LifecycleApplicationSummaryHandler),

        # AI Assistant
        (r"/api/ai/assistant", AIAssistantHandler),

        # Save & Resume
        (r"/api/save-resume/active", ActiveDraftsHandler),
        (r"/api/save-resume", SaveResumeHandler),

        # Change Management
        (r"/api/change-management/alerts/([^/]+)/convert", ChangeAlertConvertHandler),
        (r"/api/change-management/alerts/([^/]+)", ChangeAlertDetailHandler),
        (r"/api/change-management/alerts", ChangeAlertsListHandler),
        (r"/api/change-management/requests/([^/]+)/submit", ChangeRequestSubmitHandler),
        (r"/api/change-management/requests/([^/]+)/approve", ChangeRequestApproveHandler),
        (r"/api/change-management/requests/([^/]+)/reject", ChangeRequestRejectHandler),
        (r"/api/change-management/requests/([^/]+)/implement", ChangeRequestImplementHandler),
        (r"/api/change-management/requests/([^/]+)/documents", ChangeRequestDocumentHandler),
        (r"/api/change-management/requests/([^/]+)", ChangeRequestDetailHandler),
        (r"/api/change-management/requests", ChangeRequestsListHandler),
        (r"/api/change-management/stats", ChangeManagementStatsHandler),
        (r"/api/applications/([^/]+)/profile-versions/([^/]+)", ApplicationProfileVersionDetailHandler),
        (r"/api/applications/([^/]+)/profile-versions", EntityProfileVersionsHandler),
        (r"/api/profile-versions/([^/]+)", EntityProfileVersionDetailHandler),
        (r"/api/portal/applications", PortalApplicationsHandler),
        (r"/api/portal/change-requests", PortalChangeRequestHandler),

        # ── Public API v1 ─────────────────────────────────────
        (r"/api/v1/health", PublicHealthHandler),
        (r"/api/v1/applications/([^/]+)/status", PublicApplicationStatusHandler),
        (r"/api/v1/applications/([^/]+)/decision", PublicApplicationDecisionHandler),
        (r"/api/v1/dashboard/status", PublicDashboardStatusHandler),

        # Prometheus Metrics
        (r"/metrics", MetricsHandler),

        # Root redirect
        (r"/", tornado.web.RedirectHandler, {"url": "/portal"}),

        # Serve portal HTML files and static assets
        (r"/portal", PortalHandler),
        (r"/backoffice", BackOfficeHandler),
        (r"/static/(.*)", tornado.web.StaticFileHandler, {"path": STATIC_DIR}),
    ]

    # Integrate supervisor routes
    if SUPERVISOR_AVAILABLE:
        routes.extend(get_supervisor_routes())
        logger.info("Supervisor API endpoints registered (%d routes)", len(get_supervisor_routes()))

    return tornado.web.Application(routes,
        debug=_CFG_DEBUG,
        xsrf_cookies=False,  # CSRF handled by custom check_xsrf_cookie() on BaseHandler (double-submit cookie pattern)
        cookie_secret=SECRET_KEY,
        max_body_size=20 * 1024 * 1024,  # 20MB max request body
    )


if __name__ == "__main__":
    import time as _time

    _t0 = _time.monotonic()

    def _elapsed():
        return f"{_time.monotonic() - _t0:.2f}s"

    logger.info("startup: begin (+%s)", _elapsed())

    # Validate unified configuration before starting
    logger.info("startup: entering validate_config (+%s)", _elapsed())
    validate_config()
    logger.info("startup: completed validate_config (+%s)", _elapsed())

    # Validate environment before starting
    logger.info("startup: entering validate_environment (+%s)", _elapsed())
    validate_environment()
    logger.info("startup: completed validate_environment (+%s)", _elapsed())

    logger.info("startup: entering init_db (+%s)", _elapsed())
    init_db()
    logger.info("startup: completed init_db (+%s)", _elapsed())

    # Run database migrations.
    #
    # Failure policy (closes #127): the runner is fail-closed by default.
    # If any migration raises, ``run_all_migrations`` will itself raise
    # ``MigrationFailure`` after emitting a structured summary; we then
    # halt startup so the platform is never booted with un-applied schema
    # changes — a regulated AML system must not silently drift.
    #
    # The ``ImportError`` branch below is the *only* tolerated swallow
    # (the migrations package is genuinely optional for some unit-test
    # entrypoints).  Any other exception is re-raised after logging.
    #
    # Override for non-production debugging: set
    # ``MIGRATION_FAILURE_MODE=continue`` (handled inside the runner).
    logger.info("startup: entering run_all_migrations (+%s)", _elapsed())
    try:
        from migrations.runner import run_all_migrations, MigrationFailure
    except ImportError as e:
        logger.warning("Migration runner unavailable (import failed): %s", e)
    else:
        try:
            run_all_migrations()
        except MigrationFailure as e:
            logger.error(
                "startup: migration runner failed-closed (%d/%d applied, "
                "failed=%s) — halting startup. Set MIGRATION_FAILURE_MODE=continue "
                "to override in non-production.",
                e.applied_count, e.total_count, e.failed_versions,
            )
            raise
    logger.info("startup: completed run_all_migrations (+%s)", _elapsed())

    # Initialize supervisor framework
    if SUPERVISOR_AVAILABLE:
        logger.info("startup: entering setup_supervisor (+%s)", _elapsed())
        try:
            supervisor_instance = setup_supervisor(DB_PATH)
            register_all_executors(supervisor_instance, DB_PATH)
            logger.info("startup: completed setup_supervisor — %d agent executors (+%s)", 10, _elapsed())
        except Exception as e:
            logger.error("Failed to initialize supervisor: %s", e)
            SUPERVISOR_AVAILABLE = False

    # Register Sumsub factory in the factory-based provider registry (A6).
    # Runs after config is loaded and before make_app() so the registry is
    # fully populated before any incoming request can reach the screening path.
    from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME, SUMSUB_PROVIDER_NAME, register_provider
    from screening_adapter_sumsub import SumsubScreeningAdapter
    from screening_complyadvantage.adapter import ComplyAdvantageScreeningAdapter
    register_provider(SUMSUB_PROVIDER_NAME, SumsubScreeningAdapter)
    register_provider(COMPLYADVANTAGE_PROVIDER_NAME, ComplyAdvantageScreeningAdapter)
    logger.info("startup: registered screening provider: %s (+%s)", SUMSUB_PROVIDER_NAME, _elapsed())

    logger.info("startup: entering make_app (+%s)", _elapsed())
    app = make_app()
    logger.info("startup: completed make_app (+%s)", _elapsed())

    # Validate production environment (mandatory)
    logger.info("startup: entering validate_production_environment (+%s)", _elapsed())
    try:
        validate_production_environment()
    except RuntimeError as e:
        logging.critical(f"PRODUCTION ENVIRONMENT VALIDATION FAILED: {e}")
        if ENVIRONMENT == "production":
            sys.exit(1)
    logger.info("startup: completed validate_production_environment (+%s)", _elapsed())

    # Enforce startup safety checks
    logger.info("startup: entering enforce_startup_safety (+%s)", _elapsed())
    enforce_startup_safety()
    logger.info("startup: completed enforce_startup_safety (+%s)", _elapsed())

    # Bind to 0.0.0.0 for cloud deployment (Railway, Render, etc.)
    logger.info("startup: binding to 0.0.0.0:%s (+%s)", PORT, _elapsed())
    app.listen(PORT, address="0.0.0.0")
    logger.info("startup: listener bound — server READY (+%s)", _elapsed())

    # API integration status
    sanctions_status = "LIVE" if (SUMSUB_APP_TOKEN and SUMSUB_SECRET_KEY) else "SIMULATED"
    corporates_status = "LIVE" if OPENCORPORATES_API_KEY else "SIMULATED"
    ip_status = "LIVE (ipapi.co free tier)"
    sumsub_status = "LIVE" if (SUMSUB_APP_TOKEN and SUMSUB_SECRET_KEY) else "SIMULATED"

    print(f"""
╔══════════════════════════════════════════════════╗
║  ARIE Finance API Server                         ║
║  Running on http://0.0.0.0:{PORT}                ║
║  Environment: {ENVIRONMENT:<33s}║
║                                                  ║
║  Core Endpoints:                                 ║
║    POST /api/auth/officer/login                  ║
║    POST /api/auth/client/register                ║
║    POST /api/auth/client/login                   ║
║    GET  /api/dashboard                           ║
║    GET  /api/applications                        ║
║    POST /api/applications/:id/submit             ║
║                                                  ║
║  Screening APIs:                                 ║
║    POST /api/screening/run                       ║
║    POST /api/screening/sanctions                 ║
║    POST /api/screening/company                   ║
║    GET  /api/screening/ip                        ║
║    GET  /api/screening/status                    ║
║                                                  ║
║  API Integrations:                               ║
║    Sumsub AML:       {sanctions_status:<27s}║
║    OpenCorporates:   {corporates_status:<27s}║
║    IP Geolocation:   {ip_status:<27s}║
║    Sumsub KYC:       {sumsub_status:<27s}║
║                                                  ║
║  Admin email: asudally@onboarda.com                ║
║  Password: see initial boot output above          ║
╚══════════════════════════════════════════════════╝
    """)
    tornado.ioloop.IOLoop.current().start()
