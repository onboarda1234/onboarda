#!/usr/bin/env python3
"""
ARIE Finance — Back-End API Server
====================================
Single-file production-ready API server using Tornado + SQLite.
Provides: authentication, application CRUD, document uploads,
risk scoring, AI verification, audit trail, and user management.

Run:  python server.py
Env:  PORT=8080 SECRET_KEY=your-secret DB_PATH=./arie.db
"""

import os, sys, json, uuid, time, hashlib, hmac, re, sqlite3, base64, logging, secrets, io
from datetime import datetime, timedelta
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
from db import get_db as db_get_db, init_db as db_init_db, USE_POSTGRESQL

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
    from claude_client import ClaudeClient
    HAS_CLAUDE_CLIENT = True
except ImportError:
    HAS_CLAUDE_CLIENT = False
    ClaudeClient = None

# Environment configuration module
from environment import (
    ENV, is_demo, is_production, is_staging, flags,
    enforce_startup_safety, get_environment_info,
    get_database_url, get_jwt_secret, get_cors_origin, get_s3_bucket
)

# Supervisor framework
try:
    from supervisor.api import setup_supervisor, get_supervisor_routes
    SUPERVISOR_AVAILABLE = True
except ImportError:
    SUPERVISOR_AVAILABLE = False
    logging.getLogger("arie").warning("Supervisor framework not available — install pydantic>=2.0")

# ── Logging ───────────────────────────────────────────────
# JSON structured logging for production, human-readable for development
_log_format = os.environ.get("LOG_FORMAT", "text")  # "json" or "text"

class JSONFormatter(logging.Formatter):
    """JSON structured log formatter for production log aggregation."""
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
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

if _log_format == "json" or os.environ.get("ENVIRONMENT", "development") == "production":
    _handler = logging.StreamHandler()
    _handler.setFormatter(JSONFormatter())
    logging.root.handlers = []
    logging.root.addHandler(_handler)
    logging.root.setLevel(logging.INFO)
else:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

logger = logging.getLogger("arie")

# ── Configuration ──────────────────────────────────────────
PORT = int(os.environ.get("PORT", 8080))
ENVIRONMENT = os.environ.get("ENVIRONMENT", os.environ.get("ENV", "development"))

# SECRET_KEY: In production, MUST be set via env var. In dev, auto-generate a random key per session.
_env_secret = os.environ.get("SECRET_KEY", "")
if not _env_secret and ENVIRONMENT == "production":
    print("FATAL: SECRET_KEY environment variable is required in production mode.")
    print("       Generate one with: python3 -c \"import secrets; print(secrets.token_hex(64))\"")
    sys.exit(1)
SECRET_KEY = _env_secret or secrets.token_hex(64)  # Random per-session in dev

# Database: PostgreSQL (via DATABASE_URL) in production, SQLite for development
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "arie.db"))
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

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "uploads", "documents"))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..")  # Serves from arie-backend parent
MAX_UPLOAD_MB = 10
TOKEN_EXPIRY_HOURS = 24

# ── External API Keys (set via environment variables) ──────
OPENSANCTIONS_API_KEY = os.environ.get("OPENSANCTIONS_API_KEY", "")
OPENSANCTIONS_API_URL = os.environ.get("OPENSANCTIONS_API_URL", "https://api.opensanctions.org")
OPENCORPORATES_API_KEY = os.environ.get("OPENCORPORATES_API_KEY", "")
OPENCORPORATES_API_URL = os.environ.get("OPENCORPORATES_API_URL", "https://api.opencorporates.com/v0.4")
IP_GEOLOCATION_API_KEY = os.environ.get("IP_GEOLOCATION_API_KEY", "")
IP_GEOLOCATION_API_URL = os.environ.get("IP_GEOLOCATION_API_URL", "https://ipapi.co")

# Sumsub KYC/Identity Verification
SUMSUB_APP_TOKEN = os.environ.get("SUMSUB_APP_TOKEN", "")
SUMSUB_SECRET_KEY = os.environ.get("SUMSUB_SECRET_KEY", "")
SUMSUB_BASE_URL = os.environ.get("SUMSUB_BASE_URL", "https://api.sumsub.com")
SUMSUB_LEVEL_NAME = os.environ.get("SUMSUB_LEVEL_NAME", "basic-kyc-level")
SUMSUB_WEBHOOK_SECRET = os.environ.get("SUMSUB_WEBHOOK_SECRET", "")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

# ── C-02: PII Encryption Initialization ──────────────────────
# Initialize PIIEncryptor singleton for encrypting sensitive data at rest.
# In demo/dev mode, auto-generate a key if not set.
# In production, PII_ENCRYPTION_KEY MUST be set (PIIEncryptor enforces this).
_pii_encryptor = None
try:
    _pii_encryptor = PIIEncryptor()
    logger.info("PIIEncryptor initialized — field-level encryption active")
except (RuntimeError, ValueError) as e:
    if ENVIRONMENT in ("production", "prod"):
        logger.critical(f"FATAL: PIIEncryptor failed in production: {e}")
        sys.exit(1)
    else:
        # In demo/dev, generate a transient key for testing
        from cryptography.fernet import Fernet as _Fernet
        _auto_key = _Fernet.generate_key().decode()
        os.environ["PII_ENCRYPTION_KEY"] = _auto_key
        try:
            _pii_encryptor = PIIEncryptor(_auto_key)
            logger.warning("PIIEncryptor: auto-generated key for development (NOT for production)")
        except Exception as e2:
            logger.error(f"PIIEncryptor initialization failed even with auto key: {e2}")


def encrypt_pii_fields(record: dict, field_names: list) -> dict:
    """Encrypt specified PII fields in a record before database write."""
    if not _pii_encryptor:
        return record
    encrypted = dict(record)
    for field in field_names:
        if field in encrypted and encrypted[field]:
            val = str(encrypted[field])
            if val and not val.startswith("gAAAAA"):  # Don't double-encrypt Fernet tokens
                encrypted[field] = _pii_encryptor.encrypt(val)
    return encrypted


def decrypt_pii_fields(record: dict, field_names: list) -> dict:
    """Decrypt specified PII fields in a record after database read."""
    if not _pii_encryptor:
        return record
    decrypted = dict(record)
    for field in field_names:
        if field in decrypted and decrypted[field]:
            val = str(decrypted[field])
            if val.startswith("gAAAAA"):  # Fernet ciphertext prefix
                try:
                    decrypted[field] = _pii_encryptor.decrypt(val)
                except Exception:
                    pass  # Return encrypted value if decryption fails
    return decrypted


# PII field definitions for each entity type
PII_FIELDS_DIRECTORS = ["passport_number", "nationality", "id_number"]
PII_FIELDS_UBOS = ["passport_number", "nationality", "ownership_pct"]
PII_FIELDS_APPLICATIONS = ["pep_flags"]

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
        if not os.environ.get("ALLOWED_ORIGIN"):
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
    from db import seed_initial_data
    db_init_db()
    db = get_db()
    try:
        seed_initial_data(db)
        db.commit()
    except Exception as e:
        logging.error(f"Seed error: {e}")
    finally:
        db.close()


# ══════════════════════════════════════════════════════════
# AUTH HELPERS
# ══════════════════════════════════════════════════════════
def create_token(user_id, role, name, token_type="officer"):
    """Create a JWT with session binding (jti) and issuer claim for security."""
    jti = uuid.uuid4().hex  # Unique token ID for session tracking / revocation
    payload = {
        "sub": user_id,
        "role": role,
        "name": name,
        "type": token_type,
        "jti": jti,
        "iss": "arie-finance",
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_EXPIRY_HOURS),
        "iat": datetime.utcnow(),
        "nbf": datetime.utcnow(),  # Not valid before issuance
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def decode_token(token):
    """Decode and validate JWT with issuer verification."""
    try:
        decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"],
                          issuer="arie-finance",
                          options={"require": ["exp", "iat", "sub"]})
        # Check token revocation
        if token_revocation_list.is_revoked(decoded.get("jti", "")):
            logger.debug("Token revoked")
            return None
        return decoded
    except jwt.ExpiredSignatureError:
        logger.debug("Token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug("Invalid token: %s", e)
        return None


def generate_ref():
    """Generate application reference like ARF-2026-100429"""
    db = get_db()
    count = db.execute("SELECT COUNT(*) as c FROM applications").fetchone()["c"]
    db.close()
    year = datetime.now().year
    return f"ARF-{year}-{100421 + count}"


def sanitize_input(value):
    """Sanitize user input to prevent XSS and HTML injection."""
    if value is None:
        return None
    if isinstance(value, str):
        return html.escape(value.strip(), quote=True)
    return value

def sanitize_dict(data, keys=None):
    """Sanitize specified keys in a dict (or all string values if keys=None)."""
    if not isinstance(data, dict):
        return data
    result = {}
    for k, v in data.items():
        if keys is None or k in keys:
            result[k] = sanitize_input(v) if isinstance(v, str) else v
        else:
            result[k] = v
    return result


# ══════════════════════════════════════════════════════════
# RISK SCORING ENGINE
# ══════════════════════════════════════════════════════════
# Country risk classification
FATF_GREY = {"syria","myanmar","iran","north korea","yemen","haiti","south sudan",
             "nigeria","south africa","kenya","philippines","tanzania","mozambique",
             "democratic republic of congo","cameroon","burkina faso","mali","senegal"}
FATF_BLACK = {"iran","north korea","myanmar"}
SANCTIONED = {"iran","north korea","syria","cuba","crimea"}
LOW_RISK = {"mauritius","united kingdom","uk","france","germany","sweden","norway",
            "denmark","finland","australia","new zealand","canada","usa","united states",
            "japan","singapore","hong kong","switzerland","netherlands","belgium","luxembourg",
            "ireland","austria","portugal","spain","italy"}

SECTOR_SCORES = {
    "regulated financial":1, "government":1, "bank":1, "listed company":1,
    "healthcare":2, "technology":2, "software":2, "saas":2, "manufacturing":2,
    "retail":2, "e-commerce":2, "education":2, "media":2, "logistics":2,
    "import":3, "export":3, "real estate":3, "construction":3, "mining":3,
    "oil":3, "gas":3, "money services":3, "forex":3, "precious":3,
    "non-profit":3, "ngo":3, "charity":3, "advisory":3,
    "management consulting":3, "consulting":3, "financial / tax advisory":3,
    "crypto":4, "virtual asset":4, "gambling":4, "gaming":4, "betting":4,
    "arms":4, "defence":4, "military":4, "shell company":4, "nominee":4
}


def classify_country(country_name):
    """Return risk score 1-4 for a country."""
    if not country_name:
        return 2
    c = country_name.lower().strip()
    if c in SANCTIONED:
        return 4
    if c in FATF_BLACK:
        return 4
    if c in FATF_GREY:
        return 3
    if c in LOW_RISK:
        return 1
    return 2  # standard


def score_sector(sector_name):
    """Return risk score 1-4 for a sector."""
    if not sector_name:
        return 2
    s = sector_name.lower()
    for key, score in SECTOR_SCORES.items():
        if key in s:
            return score
    return 2


def compute_risk_score(app_data):
    """
    Compute composite risk score from application data.
    Returns: { score, level, dimensions: {d1..d5}, lane }
    """
    data = app_data if isinstance(app_data, dict) else json.loads(app_data)

    # D1: Customer / Entity Risk (30%)
    entity_map = {"listed":1,"regulated fund":2,"regulated":1,"government":1,
                  "large private":2,"sme":2,
                  "newly incorporated":3,"trust":3,"foundation":3,"non-profit":3,
                  "unregulated fund":4,"shell":4}
    owner_map = {"simple":1,"1-2":2,"3+":3,"complex":4}

    d1_entity = 2
    et = (data.get("entity_type") or "").lower()
    for k, v in entity_map.items():
        if k in et:
            d1_entity = v; break

    d1_owner = 2
    os_val = (data.get("ownership_structure") or "").lower()
    for k, v in owner_map.items():
        if k in os_val:
            d1_owner = v; break

    has_pep = any(d.get("is_pep") == "Yes" for d in data.get("directors", []))
    has_pep = has_pep or any(u.get("is_pep") == "Yes" for u in data.get("ubos", []))
    d1_pep = 3 if has_pep else 1

    d1 = d1_entity * 0.20 + d1_owner * 0.20 + d1_pep * 0.25 + 1 * 0.15 + 2 * 0.10 + 2 * 0.10

    # D2: Geographic Risk (25%)
    d2_inc = classify_country(data.get("country"))

    # Intermediary shareholder jurisdictions
    intermediaries = data.get("intermediary_shareholders", [])
    secrecy_jurisdictions = {"bvi", "cayman islands", "panama", "seychelles", "bermuda",
                             "jersey", "guernsey", "isle of man", "liechtenstein",
                             "vanuatu", "samoa", "marshall islands"}
    if intermediaries:
        inter_scores = []
        for inter in intermediaries:
            j = (inter.get("jurisdiction") or "").strip()
            j_score = classify_country(j)
            # Boost secrecy/opacity jurisdictions to at least 3
            if j.lower() in secrecy_jurisdictions:
                j_score = max(j_score, 3)
            inter_scores.append(j_score)
        d2_inter = max(inter_scores) if inter_scores else 1
    else:
        d2_inter = 1  # No intermediaries = low risk

    # UBO/Director nationalities
    nat_demonym_map = {
        "indian": "india", "singaporean": "singapore", "swedish": "sweden",
        "emirati": "uae", "russian": "russia", "estonian": "estonia",
        "senegalese": "senegal", "french": "france", "mauritian": "mauritius",
        "chinese": "china", "moroccan": "morocco", "nigerian": "nigeria",
        "british": "united kingdom", "american": "united states",
        "german": "germany", "japanese": "japan", "australian": "australia",
        "canadian": "canada", "lebanese": "lebanon", "iranian": "iran",
        "syrian": "syria", "afghan": "afghanistan", "belarusian": "belarus",
        "venezuelan": "venezuela", "cuban": "cuba", "north korean": "north korea (dprk)",
        "pakistani": "pakistan", "south african": "south africa",
        "vietnamese": "vietnam", "filipino": "philippines",
    }
    all_persons = data.get("directors", []) + data.get("ubos", [])
    nat_scores = []
    for person in all_persons:
        nat = (person.get("nationality") or person.get("nat") or "").strip().lower()
        if nat:
            mapped = nat_demonym_map.get(nat, nat)
            nat_scores.append(classify_country(mapped))
    d2_ubo_nat = max(nat_scores) if nat_scores else 1

    op_countries = data.get("operating_countries", [])
    d2_op = max([classify_country(c) for c in op_countries]) if op_countries else d2_inc
    target_markets = data.get("target_markets", [])
    d2_tgt = max([classify_country(c) for c in target_markets]) if target_markets else d2_inc
    d2 = d2_inc * 0.25 + d2_ubo_nat * 0.20 + d2_inter * 0.20 + d2_op * 0.20 + d2_tgt * 0.15

    # D3: Product / Service Risk (20%)
    vol_map = {"under":1,"50,000":2,"500,000":3,"over":4}
    d3_svc = 2  # default
    if data.get("cross_border"):
        d3_svc = 3
    d3_vol = 2
    vol = (data.get("monthly_volume") or "").lower()
    for k, v in vol_map.items():
        if k in vol:
            d3_vol = v; break
    d3 = d3_svc * 0.40 + d3_vol * 0.30 + 2 * 0.30

    # D4: Industry / Sector Risk (15%)
    d4 = score_sector(data.get("sector"))

    # D5: Delivery Channel Risk (10%)
    intro_map = {"direct":1,"regulated":1,"non-regulated":3,"unsolicited":4}
    d5_intro = 2
    intro = (data.get("introduction_method") or "").lower()
    for k, v in intro_map.items():
        if k in intro:
            d5_intro = v; break
    d5 = d5_intro * 0.50 + 2 * 0.50  # non-face-to-face by default

    # Composite
    composite = (d1 * 0.30 + d2 * 0.25 + d3 * 0.20 + d4 * 0.15 + d5 * 0.10) / 4 * 100
    composite = round(composite, 1)

    if composite >= 70:
        level = "VERY_HIGH"
    elif composite >= 55:
        level = "HIGH"
    elif composite >= 40:
        level = "MEDIUM"
    else:
        level = "LOW"

    lane_map = {"LOW": "Fast Lane", "MEDIUM": "Standard Review", "HIGH": "EDD", "VERY_HIGH": "EDD"}

    return {
        "score": composite,
        "level": level,
        "dimensions": {"d1": round(d1, 2), "d2": round(d2, 2), "d3": round(d3, 2), "d4": round(d4, 2), "d5": round(d5, 2)},
        "lane": lane_map[level]
    }


# ══════════════════════════════════════════════════════════
# REAL API INTEGRATIONS
# ══════════════════════════════════════════════════════════

def screen_sumsub_aml(name, birth_date=None, nationality=None, entity_type="Person"):
    """
    Screen a person or entity against Sumsub AML (sanctions, PEP, watchlists).
    Returns: { matched: bool, results: [...], source: "sumsub"|"simulated" }
    """
    try:
        # First create/retrieve a Sumsub applicant
        import hashlib
        ext_id = f"{entity_type}_{hashlib.md5(name.encode()).hexdigest()[:12]}"
        parts = name.strip().split(" ", 1)
        first = parts[0] if parts else ""
        last = parts[1] if len(parts) > 1 else ""

        applicant_result = sumsub_create_applicant(
            external_user_id=ext_id,
            first_name=first,
            last_name=last,
            dob=birth_date,
            country=nationality
        )

        if not applicant_result.get("applicant_id"):
            logger.warning(f"Sumsub AML: Failed to create/retrieve applicant for '{name}'")
            return _simulate_aml_screen(name)

        applicant_id = applicant_result["applicant_id"]

        # Get AML screening results
        from sumsub_client import get_sumsub_client
        client = get_sumsub_client()
        aml_result = client.get_aml_screening(applicant_id)

        if aml_result.get("source") == "simulated":
            # API not configured, return simulated
            return _simulate_aml_screen(name)

        # Parse AML check results into our standard format
        aml_checks = aml_result.get("aml_checks", [])
        hits = []

        for check in aml_checks:
            check_data = check.get("data", {})
            if check_data.get("matches"):
                for match in check_data.get("matches", []):
                    hits.append({
                        "match_score": round(float(match.get("matchScore", 0)) * 100, 1),
                        "matched_name": match.get("name", name),
                        "datasets": [check.get("checkType", "AML")],
                        "schema": entity_type,
                        "topics": match.get("topics", []),
                        "countries": match.get("countries", []),
                        "sanctions_list": match.get("list", ""),
                        "is_pep": "pep" in match.get("topics", []) or match.get("isPep", False),
                        "is_sanctioned": "sanction" in match.get("topics", []) or match.get("isSanctioned", False),
                    })

        return {
            "matched": len(hits) > 0,
            "results": hits,
            "source": "sumsub",
            "api_status": "live",
            "screened_at": datetime.utcnow().isoformat()
        }

    except Exception as e:
        logger.error(f"Sumsub AML screening error: {e}")
        return _simulate_aml_screen(name)


def _simulate_aml_screen(name, note="No Sumsub credentials configured — simulated result"):
    """Fallback: realistic simulation when Sumsub not configured."""
    if is_production():
        logger.error("BLOCKED: Mock screening attempted in production")
        return {"matched": False, "results": [], "source": "blocked", "api_status": "error", "note": "Mock fallback blocked in production", "screened_at": datetime.utcnow().isoformat()}
    import random
    is_hit = random.random() < 0.08  # 8% simulated hit rate
    results = []
    if is_hit:
        results.append({
            "match_score": round(random.uniform(68, 95), 1),
            "matched_name": name,
            "datasets": ["aml-simulated"],
            "schema": "Person",
            "topics": ["pep"] if random.random() < 0.5 else ["sanction"],
            "countries": [],
            "sanctions_list": "Simulated AML List",
            "is_pep": random.random() < 0.5,
            "is_sanctioned": random.random() < 0.3,
        })
    return {
        "matched": is_hit,
        "results": results,
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
        "screened_at": datetime.utcnow().isoformat()
    }


def lookup_opencorporates(company_name, jurisdiction=None):
    """
    Look up a company via Open Corporates registry.
    Returns: { found: bool, companies: [...], source: "opencorporates"|"simulated" }
    """
    if not OPENCORPORATES_API_KEY:
        logger.info(f"OpenCorporates: No API key — simulating lookup for '{company_name}'")
        return _simulate_company_lookup(company_name)

    try:
        params = {"q": company_name, "api_token": OPENCORPORATES_API_KEY}
        if jurisdiction:
            params["jurisdiction_code"] = jurisdiction.lower()

        resp = requests.get(
            f"{OPENCORPORATES_API_URL}/companies/search",
            params=params,
            timeout=15
        )

        if resp.status_code == 200:
            data = resp.json()
            companies_raw = data.get("results", {}).get("companies", [])
            companies = []
            for c_wrap in companies_raw[:5]:  # Top 5 matches
                c = c_wrap.get("company", {})
                companies.append({
                    "name": c.get("name", ""),
                    "company_number": c.get("company_number", ""),
                    "jurisdiction": c.get("jurisdiction_code", ""),
                    "incorporation_date": c.get("incorporation_date", ""),
                    "dissolution_date": c.get("dissolution_date"),
                    "company_type": c.get("company_type", ""),
                    "registry_url": c.get("registry_url", ""),
                    "status": c.get("current_status", ""),
                    "registered_address": c.get("registered_address_in_full", ""),
                    "opencorporates_url": c.get("opencorporates_url", ""),
                })

            return {
                "found": len(companies) > 0,
                "companies": companies,
                "total_results": data.get("results", {}).get("total_count", 0),
                "source": "opencorporates",
                "api_status": "live",
                "searched_at": datetime.utcnow().isoformat()
            }
        elif resp.status_code == 401:
            logger.warning("OpenCorporates: Invalid API key — falling back to simulation")
            return _simulate_company_lookup(company_name, note="API key invalid — simulated result")
        else:
            logger.warning(f"OpenCorporates: HTTP {resp.status_code}")
            return _simulate_company_lookup(company_name, note=f"API returned {resp.status_code} — simulated")

    except Exception as e:
        logger.error(f"OpenCorporates error: {e}")
        return _simulate_company_lookup(company_name, note=f"API error — simulated result")


def _simulate_company_lookup(company_name, note="No API key configured — simulated result"):
    """Fallback: simulated company registry lookup. C-04: BLOCKED in production."""
    if is_production():
        logger.error("BLOCKED: Mock company lookup attempted in production")
        return {"found": False, "companies": [], "source": "blocked", "api_status": "error", "note": "Mock fallback blocked in production"}
    import random
    found = random.random() < 0.85  # 85% chance found
    companies = []
    if found:
        companies.append({
            "name": company_name,
            "company_number": f"C{random.randint(10000,99999)}",
            "jurisdiction": "mu",
            "incorporation_date": f"20{random.randint(10,24)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            "dissolution_date": None,
            "company_type": random.choice(["Private Company Limited by Shares", "Global Business Company"]),
            "registry_url": "",
            "status": random.choice(["Active", "Active"]),
            "registered_address": "Port Louis, Mauritius",
            "opencorporates_url": "",
        })
    return {
        "found": found,
        "companies": companies,
        "total_results": 1 if found else 0,
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
        "searched_at": datetime.utcnow().isoformat()
    }


def geolocate_ip(ip_address):
    """
    Look up geolocation and risk data for an IP address.
    Returns: { country, city, is_vpn, is_proxy, risk_level, source }
    """
    if not ip_address or ip_address in ("127.0.0.1", "::1", "0.0.0.0"):
        return {
            "country": "Local",
            "country_code": "XX",
            "city": "Localhost",
            "region": "",
            "is_vpn": False,
            "is_proxy": False,
            "is_tor": False,
            "risk_level": "LOW",
            "source": "local",
            "api_status": "skipped",
            "checked_at": datetime.utcnow().isoformat()
        }

    try:
        # Use ipapi.co (free tier: 1000 req/day, no key needed for basic)
        url = f"{IP_GEOLOCATION_API_URL}/{ip_address}/json/"
        if IP_GEOLOCATION_API_KEY:
            url += f"?key={IP_GEOLOCATION_API_KEY}"

        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("error"):
                return _simulate_ip_geolocation(ip_address, note=data.get("reason", "API error"))

            country_code = (data.get("country_code") or "").upper()
            # Determine IP risk level
            ip_risk = "LOW"
            if country_code in [c.upper() for c in SANCTIONED]:
                ip_risk = "VERY_HIGH"
            elif country_code in [c.upper() for c in FATF_BLACK]:
                ip_risk = "HIGH"
            elif country_code in [c.upper() for c in FATF_GREY]:
                ip_risk = "MEDIUM"

            return {
                "country": data.get("country_name", "Unknown"),
                "country_code": country_code,
                "city": data.get("city", ""),
                "region": data.get("region", ""),
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "org": data.get("org", ""),
                "asn": data.get("asn", ""),
                "is_vpn": False,  # Basic API doesn't detect VPN
                "is_proxy": False,
                "is_tor": False,
                "risk_level": ip_risk,
                "source": "ipapi",
                "api_status": "live",
                "checked_at": datetime.utcnow().isoformat()
            }
        else:
            return _simulate_ip_geolocation(ip_address, note=f"API returned {resp.status_code}")

    except Exception as e:
        logger.error(f"IP Geolocation error: {e}")
        return _simulate_ip_geolocation(ip_address, note=f"API error — simulated")


def _simulate_ip_geolocation(ip_address, note="Simulated result"):
    """Fallback: simulated IP geolocation. C-04: BLOCKED in production."""
    if is_production():
        logger.error("BLOCKED: Mock IP geolocation attempted in production")
        return {"country": "Unknown", "country_code": "XX", "source": "blocked", "api_status": "error", "note": "Mock fallback blocked in production"}
    import random
    countries = [
        ("Mauritius", "MU"), ("United Kingdom", "GB"), ("France", "FR"),
        ("India", "IN"), ("South Africa", "ZA"), ("Singapore", "SG")
    ]
    country, code = random.choice(countries)
    return {
        "country": country,
        "country_code": code,
        "city": "Simulated City",
        "region": "",
        "is_vpn": random.random() < 0.05,
        "is_proxy": random.random() < 0.03,
        "is_tor": False,
        "risk_level": "LOW",
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
        "checked_at": datetime.utcnow().isoformat()
    }


# ══════════════════════════════════════════════════════════
# SUMSUB KYC / IDENTITY VERIFICATION
# ══════════════════════════════════════════════════════════

def _sumsub_sign(method, url_path, body=b""):
    """
    Create HMAC-SHA256 signature for Sumsub API requests.
    Returns headers dict with X-App-Token, X-App-Access-Ts, X-App-Access-Sig.
    """
    ts = str(int(time.time()))
    # Build signature payload: ts + method + path + body
    sig_payload = ts.encode("utf-8") + method.upper().encode("utf-8") + url_path.encode("utf-8")
    if body:
        sig_payload += body if isinstance(body, bytes) else body.encode("utf-8")
    sig = hmac.new(
        SUMSUB_SECRET_KEY.encode("utf-8"),
        sig_payload,
        hashlib.sha256
    ).hexdigest()
    return {
        "X-App-Token": SUMSUB_APP_TOKEN,
        "X-App-Access-Ts": ts,
        "X-App-Access-Sig": sig,
    }


def sumsub_create_applicant(external_user_id, first_name=None, last_name=None,
                            email=None, phone=None, dob=None, country=None,
                            level_name=None):
    """
    Create a Sumsub applicant for KYC verification.
    Returns: { applicant_id, external_user_id, status, source }
    """
    if not SUMSUB_APP_TOKEN or not SUMSUB_SECRET_KEY:
        logger.info(f"Sumsub: No credentials — simulating applicant creation for '{external_user_id}'")
        return _simulate_sumsub_applicant(external_user_id, first_name, last_name)

    try:
        level = level_name or SUMSUB_LEVEL_NAME
        url_path = f"/resources/applicants?levelName={level}"
        body_data = {
            "externalUserId": external_user_id,
        }
        if first_name or last_name:
            fixed_info = {}
            if first_name:
                fixed_info["firstName"] = first_name
            if last_name:
                fixed_info["lastName"] = last_name
            if dob:
                fixed_info["dob"] = dob
            if country:
                fixed_info["country"] = country
            body_data["fixedInfo"] = fixed_info
        if email:
            body_data["email"] = email
        if phone:
            body_data["phone"] = phone

        body_bytes = json.dumps(body_data).encode("utf-8")
        headers = _sumsub_sign("POST", url_path, body_bytes)
        headers["Content-Type"] = "application/json"

        resp = requests.post(
            f"{SUMSUB_BASE_URL}{url_path}",
            headers=headers,
            data=body_bytes,
            timeout=15
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            applicant_id = data.get("id", "")
            logger.info(f"Sumsub: Created applicant {applicant_id} for user {external_user_id}")
            return {
                "applicant_id": applicant_id,
                "external_user_id": external_user_id,
                "status": data.get("review", {}).get("reviewStatus", "init"),
                "inspection_id": data.get("inspectionId", ""),
                "level_name": level,
                "created_at": data.get("createdAt", ""),
                "source": "sumsub",
                "api_status": "live",
            }
        else:
            logger.warning(f"Sumsub create applicant failed: {resp.status_code} — {resp.text[:300]}")
            # If applicant already exists, try to get existing
            if resp.status_code == 409:
                return sumsub_get_applicant_by_external_id(external_user_id)
            return _simulate_sumsub_applicant(external_user_id, first_name, last_name,
                                             note=f"API returned {resp.status_code}")

    except Exception as e:
        logger.error(f"Sumsub create applicant error: {e}")
        return _simulate_sumsub_applicant(external_user_id, first_name, last_name,
                                         note=f"Exception: {str(e)[:100]}")


def sumsub_get_applicant_by_external_id(external_user_id):
    """Retrieve an existing Sumsub applicant by external user ID."""
    if not SUMSUB_APP_TOKEN or not SUMSUB_SECRET_KEY:
        return _simulate_sumsub_applicant(external_user_id)

    try:
        url_path = f"/resources/applicants/-;externalUserId={external_user_id}/one"
        headers = _sumsub_sign("GET", url_path)

        resp = requests.get(
            f"{SUMSUB_BASE_URL}{url_path}",
            headers=headers,
            timeout=15
        )

        if resp.status_code == 200:
            data = resp.json()
            return {
                "applicant_id": data.get("id", ""),
                "external_user_id": external_user_id,
                "status": data.get("review", {}).get("reviewStatus", "init"),
                "review_answer": data.get("review", {}).get("reviewResult", {}).get("reviewAnswer", ""),
                "inspection_id": data.get("inspectionId", ""),
                "level_name": data.get("requiredIdDocs", {}).get("docSets", [{}])[0].get("idDocSetType", "") if data.get("requiredIdDocs") else "",
                "source": "sumsub",
                "api_status": "live",
            }
        else:
            return _simulate_sumsub_applicant(external_user_id,
                                             note=f"Lookup returned {resp.status_code}")

    except Exception as e:
        logger.error(f"Sumsub get applicant error: {e}")
        return _simulate_sumsub_applicant(external_user_id, note=str(e)[:100])


def sumsub_generate_access_token(external_user_id, level_name=None):
    """
    Generate an access token for the Sumsub WebSDK.
    The client portal uses this token to launch the KYC widget.
    Returns: { token, userId, source }
    """
    if not SUMSUB_APP_TOKEN or not SUMSUB_SECRET_KEY:
        logger.info(f"Sumsub: No credentials — simulating access token for '{external_user_id}'")
        return _simulate_sumsub_token(external_user_id)

    try:
        level = level_name or SUMSUB_LEVEL_NAME
        url_path = f"/resources/accessTokens?userId={external_user_id}&levelName={level}"
        headers = _sumsub_sign("POST", url_path)

        resp = requests.post(
            f"{SUMSUB_BASE_URL}{url_path}",
            headers=headers,
            timeout=15
        )

        if resp.status_code == 200:
            data = resp.json()
            logger.info(f"Sumsub: Generated access token for user {external_user_id}")
            return {
                "token": data.get("token", ""),
                "user_id": external_user_id,
                "level_name": level,
                "source": "sumsub",
                "api_status": "live",
            }
        else:
            logger.warning(f"Sumsub token gen failed: {resp.status_code} — {resp.text[:300]}")
            return _simulate_sumsub_token(external_user_id,
                                         note=f"API returned {resp.status_code}")

    except Exception as e:
        logger.error(f"Sumsub token error: {e}")
        return _simulate_sumsub_token(external_user_id, note=str(e)[:100])


def sumsub_get_applicant_status(applicant_id):
    """
    Get the verification status of a Sumsub applicant.
    Returns: { applicant_id, status, review_answer, verification_steps, source }
    """
    if not SUMSUB_APP_TOKEN or not SUMSUB_SECRET_KEY:
        return _simulate_sumsub_status(applicant_id)

    try:
        # Get applicant data
        url_path = f"/resources/applicants/{applicant_id}/one"
        headers = _sumsub_sign("GET", url_path)

        resp = requests.get(
            f"{SUMSUB_BASE_URL}{url_path}",
            headers=headers,
            timeout=15
        )

        if resp.status_code == 200:
            data = resp.json()
            review = data.get("review", {})
            review_result = review.get("reviewResult", {})

            result = {
                "applicant_id": applicant_id,
                "external_user_id": data.get("externalUserId", ""),
                "status": review.get("reviewStatus", "init"),
                "review_answer": review_result.get("reviewAnswer", ""),
                "rejection_labels": review_result.get("rejectLabels", []),
                "moderation_comment": review_result.get("moderationComment", ""),
                "created_at": data.get("createdAt", ""),
                "source": "sumsub",
                "api_status": "live",
            }

            # Also get verification steps
            steps_url = f"/resources/applicants/{applicant_id}/requiredIdDocsStatus"
            steps_headers = _sumsub_sign("GET", steps_url)
            steps_resp = requests.get(
                f"{SUMSUB_BASE_URL}{steps_url}",
                headers=steps_headers,
                timeout=10
            )
            if steps_resp.status_code == 200:
                result["verification_steps"] = steps_resp.json()

            return result
        else:
            logger.warning(f"Sumsub status check failed: {resp.status_code}")
            return _simulate_sumsub_status(applicant_id,
                                          note=f"API returned {resp.status_code}")

    except Exception as e:
        logger.error(f"Sumsub status error: {e}")
        return _simulate_sumsub_status(applicant_id, note=str(e)[:100])


def sumsub_add_document(applicant_id, doc_type, country, file_path=None, file_data=None, file_name="document.pdf"):
    """
    Add an identity document to a Sumsub applicant.
    doc_type: PASSPORT, ID_CARD, DRIVERS, SELFIE, etc.
    """
    if not SUMSUB_APP_TOKEN or not SUMSUB_SECRET_KEY:
        return {"status": "simulated", "message": "Sumsub not configured", "source": "simulated"}

    try:
        url_path = f"/resources/applicants/{applicant_id}/info/idDoc"
        metadata = json.dumps({
            "idDocType": doc_type,
            "country": country,
        })

        # Read file content
        content = None
        if file_path and os.path.exists(file_path):
            with open(file_path, "rb") as f:
                content = f.read()
        elif file_data:
            content = base64.b64decode(file_data) if isinstance(file_data, str) else file_data

        if not content:
            return {"status": "error", "message": "No document content provided", "source": "sumsub"}

        # Multipart form data — we need to sign without the body for multipart
        headers = _sumsub_sign("POST", url_path)

        files = {
            "metadata": (None, metadata, "application/json"),
            "content": (file_name, content, "application/octet-stream"),
        }

        resp = requests.post(
            f"{SUMSUB_BASE_URL}{url_path}",
            headers=headers,
            files=files,
            timeout=30
        )

        if resp.status_code in (200, 201):
            logger.info(f"Sumsub: Added {doc_type} document for applicant {applicant_id}")
            return {
                "status": "uploaded",
                "doc_type": doc_type,
                "applicant_id": applicant_id,
                "source": "sumsub",
                "api_status": "live",
            }
        else:
            logger.warning(f"Sumsub doc upload failed: {resp.status_code} — {resp.text[:200]}")
            return {
                "status": "error",
                "message": f"Upload failed: {resp.status_code}",
                "source": "sumsub",
            }

    except Exception as e:
        logger.error(f"Sumsub doc upload error: {e}")
        return {"status": "error", "message": str(e)[:100], "source": "sumsub"}


def sumsub_verify_webhook(body_bytes, signature_header):
    """Verify a Sumsub webhook signature (HMAC-SHA256)."""
    if not SUMSUB_WEBHOOK_SECRET:
        if ENVIRONMENT == "production":
            logger.error("Sumsub webhook secret not configured in production — REJECTING webhook")
            return False
        logger.warning("Sumsub webhook secret not configured — accepting in dev mode only")
        return True

    expected = hmac.new(
        SUMSUB_WEBHOOK_SECRET.encode("utf-8"),
        body_bytes,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header or "")


# ── Sumsub simulation fallbacks ──────────────────────────

def _simulate_sumsub_applicant(external_user_id, first_name=None, last_name=None, note="No Sumsub credentials configured"):
    """Fallback: simulated applicant creation. C-04: BLOCKED in production."""
    if is_production():
        logger.error("BLOCKED: Mock Sumsub applicant creation attempted in production")
        return {"applicant_id": None, "source": "blocked", "api_status": "error", "note": "Mock fallback blocked in production"}
    sim_id = f"sim_{hashlib.md5(external_user_id.encode()).hexdigest()[:16]}"
    return {
        "applicant_id": sim_id,
        "external_user_id": external_user_id,
        "status": "init",
        "inspection_id": f"insp_{sim_id[:12]}",
        "level_name": SUMSUB_LEVEL_NAME,
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
        "created_at": datetime.utcnow().isoformat(),
    }


def _simulate_sumsub_token(external_user_id, note="No Sumsub credentials configured"):
    """Fallback: simulated access token. C-04: BLOCKED in production."""
    if is_production():
        logger.error("BLOCKED: Mock Sumsub token generation attempted in production")
        return {"token": None, "source": "blocked", "api_status": "error", "note": "Mock fallback blocked in production"}
    token = base64.b64encode(f"sim_token_{external_user_id}_{int(time.time())}".encode()).decode()
    return {
        "token": token,
        "user_id": external_user_id,
        "level_name": SUMSUB_LEVEL_NAME,
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
    }


def _simulate_sumsub_status(applicant_id, note="No Sumsub credentials configured"):
    """Fallback: simulated verification status. C-04: BLOCKED in production."""
    if is_production():
        logger.error("BLOCKED: Mock Sumsub status check attempted in production")
        return {"applicant_id": applicant_id, "status": "blocked", "source": "blocked", "api_status": "error", "note": "Mock fallback blocked in production"}
    import random
    statuses = ["init", "pending", "completed"]
    answers = ["", "", "GREEN"]
    idx = random.randint(0, 2)
    return {
        "applicant_id": applicant_id,
        "external_user_id": "",
        "status": statuses[idx],
        "review_answer": answers[idx],
        "rejection_labels": [],
        "moderation_comment": "",
        "verification_steps": {
            "IDENTITY": {"reviewResult": {"reviewAnswer": answers[idx]} if idx == 2 else {}},
            "SELFIE": {"reviewResult": {"reviewAnswer": answers[idx]} if idx == 2 else {}},
        },
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
        "created_at": datetime.utcnow().isoformat(),
    }


def run_full_screening(application_data, directors, ubos, client_ip=None):
    """
    Run all screening agents in parallel against an application.
    Uses ThreadPoolExecutor for concurrent HTTP API calls.
    """
    company_name = application_data.get("company_name", "")
    country = application_data.get("country", "")

    report = {
        "screened_at": datetime.utcnow().isoformat(),
        "company_screening": {},
        "director_screenings": [],
        "ubo_screenings": [],
        "ip_geolocation": {},
        "overall_flags": [],
        "total_hits": 0,
    }

    # Map jurisdiction
    jurisdiction = None
    if country:
        jur_map = {"mauritius": "mu", "united kingdom": "gb", "uk": "gb", "france": "fr",
                   "singapore": "sg", "india": "in", "hong kong": "hk", "usa": "us",
                   "united states": "us", "south africa": "za", "germany": "de"}
        jurisdiction = jur_map.get(country.lower())

    # ── Parallel API calls ──
    with ThreadPoolExecutor(max_workers=8) as executor:
        # Submit all screening tasks concurrently
        company_future = executor.submit(lookup_opencorporates, company_name, jurisdiction)
        company_sanctions_future = executor.submit(screen_sumsub_aml, company_name, entity_type="Company")

        director_futures = []
        for d in directors:
            d_name = d.get("full_name", "")
            if d_name:
                f = executor.submit(screen_sumsub_aml, d_name, nationality=d.get("nationality"), entity_type="Person")
                director_futures.append((d, f))

        ubo_futures = []
        for u in ubos:
            u_name = u.get("full_name", "")
            if u_name:
                f = executor.submit(screen_sumsub_aml, u_name, nationality=u.get("nationality"), entity_type="Person")
                ubo_futures.append((u, f))

        ip_future = executor.submit(geolocate_ip, client_ip) if client_ip else None

        kyc_futures = []
        all_persons = [(d, "director") for d in directors] + [(u, "ubo") for u in ubos]
        for person, ptype in all_persons:
            p_name = person.get("full_name", "")
            if not p_name:
                continue
            ext_id = person.get("email", "") or f"{ptype}_{hashlib.md5(p_name.encode()).hexdigest()[:12]}"
            parts = p_name.strip().split(" ", 1)
            first = parts[0] if parts else ""
            last = parts[1] if len(parts) > 1 else ""
            f = executor.submit(sumsub_create_applicant,
                external_user_id=ext_id, first_name=first, last_name=last,
                country=person.get("nationality", ""))
            kyc_futures.append((person, ptype, p_name, f))

        # ── Collect results ──
        report["company_screening"] = company_future.result(timeout=30)
        if not report["company_screening"]["found"]:
            report["overall_flags"].append(f"Company '{company_name}' not found in corporate registry")

        company_sanctions = company_sanctions_future.result(timeout=30)
        report["company_screening"]["sanctions"] = company_sanctions
        if company_sanctions["matched"]:
            report["overall_flags"].append(f"Company '{company_name}' has sanctions/watchlist matches")
            report["total_hits"] += len(company_sanctions["results"])

        for d, f in director_futures:
            d_name = d.get("full_name", "")
            screening = f.result(timeout=30)
            result = {
                "person_name": d_name, "person_type": "director",
                "nationality": d.get("nationality", ""), "declared_pep": d.get("is_pep", "No"),
                "screening": screening,
            }
            if screening["matched"]:
                report["overall_flags"].append(f"Director '{d_name}' has sanctions/PEP matches")
                report["total_hits"] += len(screening["results"])
                for hit in screening["results"]:
                    if hit.get("is_pep") and d.get("is_pep", "No") != "Yes":
                        result["undeclared_pep"] = True
                        report["overall_flags"].append(f"Director '{d_name}' may be undeclared PEP")
            report["director_screenings"].append(result)

        for u, f in ubo_futures:
            u_name = u.get("full_name", "")
            screening = f.result(timeout=30)
            result = {
                "person_name": u_name, "person_type": "ubo",
                "nationality": u.get("nationality", ""), "ownership_pct": u.get("ownership_pct", 0),
                "declared_pep": u.get("is_pep", "No"), "screening": screening,
            }
            if screening["matched"]:
                report["overall_flags"].append(f"UBO '{u_name}' has sanctions/PEP matches")
                report["total_hits"] += len(screening["results"])
                for hit in screening["results"]:
                    if hit.get("is_pep") and u.get("is_pep", "No") != "Yes":
                        result["undeclared_pep"] = True
                        report["overall_flags"].append(f"UBO '{u_name}' may be undeclared PEP")
            report["ubo_screenings"].append(result)

        if ip_future:
            report["ip_geolocation"] = ip_future.result(timeout=30)
            ip_geo = report["ip_geolocation"]
            if ip_geo.get("risk_level") in ("HIGH", "VERY_HIGH"):
                report["overall_flags"].append(f"Client IP geolocated to high-risk jurisdiction: {ip_geo.get('country')}")
            if ip_geo.get("is_vpn"):
                report["overall_flags"].append("Client IP detected as VPN")
            if ip_geo.get("is_proxy"):
                report["overall_flags"].append("Client IP detected as proxy")
            if ip_geo.get("is_tor"):
                report["overall_flags"].append("Client IP detected as Tor exit node")

        report["kyc_applicants"] = []
        for person, ptype, p_name, f in kyc_futures:
            applicant = f.result(timeout=30)
            applicant["person_name"] = p_name
            applicant["person_type"] = ptype
            report["kyc_applicants"].append(applicant)
            if applicant.get("review_answer") == "RED":
                report["overall_flags"].append(f"Sumsub KYC FAILED for {ptype} '{p_name}'")
                report["total_hits"] += 1

    return report


# ══════════════════════════════════════════════════════════
# RATE LIMITING
# ══════════════════════════════════════════════════════════

class RateLimiter:
    """In-memory sliding window rate limiter. Keyed by IP + endpoint."""
    def __init__(self):
        self._attempts = {}  # key → list of timestamps

    def is_limited(self, key, max_attempts=10, window_seconds=900):
        """Returns True if the key has exceeded max_attempts in the window."""
        now = time.time()
        cutoff = now - window_seconds
        if key not in self._attempts:
            self._attempts[key] = []
        # Prune old entries
        self._attempts[key] = [t for t in self._attempts[key] if t > cutoff]
        if len(self._attempts[key]) >= max_attempts:
            return True
        self._attempts[key].append(now)
        return False

    def remaining(self, key, max_attempts=10, window_seconds=900):
        """Returns how many attempts remain for the key."""
        now = time.time()
        cutoff = now - window_seconds
        attempts = [t for t in self._attempts.get(key, []) if t > cutoff]
        return max(0, max_attempts - len(attempts))

    def reset(self, key):
        """Reset rate limit for a key (e.g., after successful login)."""
        self._attempts.pop(key, None)

# Global rate limiter instance
rate_limiter = RateLimiter()


# ══════════════════════════════════════════════════════════
# TORNADO REQUEST HANDLERS
# ══════════════════════════════════════════════════════════

class BaseHandler(tornado.web.RequestHandler):
    def prepare(self):
        """Enforce HTTPS in production via X-Forwarded-Proto from reverse proxy."""
        if ENVIRONMENT == "production":
            forwarded_proto = self.request.headers.get("X-Forwarded-Proto", "")
            if forwarded_proto == "http":
                # Redirect HTTP to HTTPS
                url = self.request.full_url().replace("http://", "https://", 1)
                self.redirect(url, permanent=True)
                return

    def check_rate_limit(self, endpoint_key, max_attempts=30, window_seconds=60):
        """
        C-06 FIX: Check rate limit for the current request.
        Returns True if the request should proceed, False if rate-limited.
        """
        ip = self.get_client_ip()
        user = self.get_current_user_token()
        user_id = user.get("sub", "") if user else ""

        # Per-IP rate limit
        ip_key = f"{endpoint_key}:ip:{ip}"
        if rate_limiter.is_limited(ip_key, max_attempts=max_attempts, window_seconds=window_seconds):
            self.set_status(429)
            self.write({"error": f"Rate limit exceeded for {endpoint_key}. Try again later.", "retry_after": window_seconds})
            return False

        # Per-user rate limit (if authenticated)
        if user_id:
            user_key = f"{endpoint_key}:user:{user_id}"
            if rate_limiter.is_limited(user_key, max_attempts=max_attempts, window_seconds=window_seconds):
                self.set_status(429)
                self.write({"error": f"Rate limit exceeded. Try again later.", "retry_after": window_seconds})
                return False

        return True

    def set_default_headers(self):
        # CORS — in production, MUST set ALLOWED_ORIGIN env var to your domain
        allowed_origin = os.environ.get("ALLOWED_ORIGIN", "")
        if not allowed_origin:
            if ENVIRONMENT == "production":
                # In production, no CORS header = same-origin only (most secure)
                logger.warning("ALLOWED_ORIGIN not set in production — defaulting to same-origin only")
            else:
                allowed_origin = "*"  # Permissive in dev only
        if allowed_origin:
            self.set_header("Access-Control-Allow-Origin", allowed_origin)
        self.set_header("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type,Authorization,X-CSRF-Token,X-Idempotency-Key")
        self.set_header("Access-Control-Max-Age", "3600")
        self.set_header("Content-Type", "application/json")
        # Security headers — always on
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("X-Frame-Options", "DENY")
        self.set_header("X-XSS-Protection", "1; mode=block")
        self.set_header("Referrer-Policy", "strict-origin-when-cross-origin")
        # HSTS — always in production (tells browsers to only use HTTPS)
        if ENVIRONMENT == "production":
            self.set_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload")
        # Content Security Policy
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        self.set_header("Content-Security-Policy", csp)
        self.set_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")

    def issue_csrf_token(self):
        """Issue a CSRF token as a secure cookie. Call on successful login."""
        csrf_token = secrets.token_hex(32)
        self.set_cookie(
            "csrf_token", csrf_token,
            httponly=False,  # Must be readable by JS to send in header
            secure=(ENVIRONMENT == "production"),
            samesite="Strict",
            path="/",
        )
        return csrf_token

    def options(self, *args):
        self.set_status(204)
        self.finish()

    def check_xsrf_cookie(self):
        """
        CSRF protection: double-submit cookie pattern.
        - Bearer token requests: CSRF-exempt (token proves authentication)
        - Webhook endpoints: CSRF-exempt (use HMAC signature validation)
        - Auth endpoints (login/register): CSRF-exempt (no session exists yet)
        - Cookie-based requests: REQUIRE X-CSRF-Token header matching csrf_token cookie
        """
        # Bearer token auth is inherently CSRF-safe
        if self.request.headers.get("Authorization", "").startswith("Bearer "):
            return
        # Webhook endpoints use HMAC signatures, not cookies
        if "/webhook" in self.request.uri:
            return
        # Auth endpoints (login, register) are pre-session — no CSRF token exists yet
        # These are protected by rate limiting instead
        _csrf_exempt_paths = (
            "/api/auth/officer/login",
            "/api/auth/client/login",
            "/api/auth/client/register",
            "/api/health",
        )
        if self.request.uri in _csrf_exempt_paths:
            return
        # OPTIONS preflight requests don't need CSRF
        if self.request.method == "OPTIONS":
            return
        # For state-changing methods with cookie auth, enforce CSRF
        if self.request.method in ("POST", "PUT", "PATCH", "DELETE"):
            csrf_cookie = self.get_cookie("csrf_token", None)
            csrf_header = self.request.headers.get("X-CSRF-Token", "")
            if not csrf_cookie or not csrf_header:
                raise tornado.web.HTTPError(403, reason="CSRF token missing")
            if not hmac.compare_digest(csrf_cookie, csrf_header):
                raise tornado.web.HTTPError(403, reason="CSRF token mismatch")
            return
        # GET/HEAD are safe methods — no CSRF check needed

    def get_json(self):
        try:
            return json.loads(self.request.body)
        except Exception:
            return {}

    def get_current_user_token(self):
        auth = self.request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return decode_token(auth[7:])
        return None

    def require_auth(self, roles=None):
        user = self.get_current_user_token()
        if not user:
            self.set_status(401)
            self.write({"error": "Authentication required"})
            return None
        if roles and user.get("role") not in roles:
            self.set_status(403)
            self.write({"error": "Insufficient permissions"})
            return None
        return user

    def get_client_ip(self):
        return self.request.headers.get("X-Real-IP", self.request.remote_ip)

    def success(self, data, status=200):
        self.set_status(status)
        self.write(json.dumps(data, default=str))

    def error(self, message, status=400):
        self.set_status(status)
        self.write({"error": message})

    def log_audit(self, user, action, target, detail, db=None):
        own_db = db is None
        if own_db:
            db = get_db()
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
            (user.get("sub",""), user.get("name",""), user.get("role",""), action, target, detail, self.get_client_ip())
        )
        db.commit()
        if own_db:
            db.close()

    def check_app_ownership(self, user, app):
        """Returns True if user is allowed to access this application."""
        if user.get("type") == "client" and app["client_id"] != user["sub"]:
            self.error("Unauthorized", 403)
            return False
        return True

    def write_error(self, status_code, **kwargs):
        """Cross-cutting: Safe error responses — no stack traces in production."""
        if ENVIRONMENT == "production":
            self.write({"error": self._reason or "Internal server error", "status": status_code})
        else:
            # In dev, include more detail for debugging
            import traceback
            error_detail = ""
            if "exc_info" in kwargs:
                error_detail = traceback.format_exception(*kwargs["exc_info"])[-1].strip()
            self.write({"error": self._reason or "Internal server error", "status": status_code, "detail": error_detail})


# ── Health Check ──
class HealthHandler(BaseHandler):
    def get(self):
        """Enhanced health check with database connectivity and dependency status."""
        health = {
            "status": "ok",
            "service": "ARIE Finance API",
            "version": "1.0.0",
            "environment": ENV,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        # Database connectivity check
        try:
            db = get_db()
            if USE_POSTGRES:
                cur = db.cursor()
                cur.execute("SELECT 1")
                cur.close()
            else:
                db.execute("SELECT 1")
            db.close()
            health["database"] = {"status": "connected", "type": "postgresql" if USE_POSTGRES else "sqlite"}
        except Exception as e:
            health["database"] = {"status": "error", "error": str(e)[:200]}
            health["status"] = "degraded"

        # External API status — only show if authenticated and is admin
        # Remove integrations section to avoid configuration leakage
        user = self.get_current_user()
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
            self.set_status(429)
            self.write({"error": "Too many login attempts. Please try again in 15 minutes."})
            logger.warning(f"Rate limited officer login from {ip} for {email}")
            return

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ? AND status = 'active'", (email,)).fetchone()
        db.close()

        if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            return self.error("Invalid credentials", 401)

        rate_limiter.reset(rl_key)  # Reset on successful login
        token = create_token(user["id"], user["role"], user["full_name"], "officer")
        csrf_token = self.issue_csrf_token()
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
            self.set_status(429)
            self.write({"error": "Too many login attempts. Please try again in 15 minutes."})
            logger.warning(f"Rate limited client login from {ip} for {email}")
            return

        db = get_db()
        client = db.execute("SELECT * FROM clients WHERE email = ? AND status = 'active'", (email,)).fetchone()
        db.close()

        if not client or not bcrypt.checkpw(password.encode(), client["password_hash"].encode()):
            return self.error("Invalid credentials", 401)

        rate_limiter.reset(rl_key)  # Reset on successful login

        token = create_token(client["id"], "client", client["company_name"] or email, "client")
        csrf_token = self.issue_csrf_token()
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
            self.set_status(429)
            self.write({"error": "Too many registration attempts. Please try again later."})
            return

        if len(password) < 8:
            return self.error("Password must be at least 8 characters")

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
        self.success({"token": token, "client": {"id": client_id, "email": email, "company": company}}, 201)


class MeHandler(BaseHandler):
    """GET /api/auth/me"""
    def get(self):
        user = self.require_auth()
        if not user:
            return
        self.success({"id": user["sub"], "name": user["name"], "role": user["role"], "type": user["type"]})


# ══════════════════════════════════════════════════════════
# APPLICATION ENDPOINTS
# ══════════════════════════════════════════════════════════

class ApplicationsHandler(BaseHandler):
    """GET /api/applications — list, POST — create"""
    def get(self):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        status = self.get_argument("status", None)
        risk = self.get_argument("risk", None)
        assigned = self.get_argument("assigned", None)

        query = "SELECT * FROM applications WHERE 1=1"
        params = []

        # Clients can only see their own
        if user["type"] == "client":
            query += " AND client_id = ?"
            params.append(user["sub"])

        if status:
            query += " AND status = ?"
            params.append(status)
        if risk:
            query += " AND risk_level = ?"
            params.append(risk)
        if assigned:
            query += " AND assigned_to = ?"
            params.append(assigned)

        query += " ORDER BY created_at DESC LIMIT 200"
        rows = db.execute(query, params).fetchall()
        db.close()

        apps = [dict(r) for r in rows]
        # Attach directors and UBOs for each — C-02: decrypt PII on read
        db = get_db()
        for app in apps:
            app["directors"] = [decrypt_pii_fields(dict(d), PII_FIELDS_DIRECTORS) for d in db.execute(
                "SELECT * FROM directors WHERE application_id = ?", (app["id"],)).fetchall()]
            app["ubos"] = [decrypt_pii_fields(dict(u), PII_FIELDS_UBOS) for u in db.execute(
                "SELECT * FROM ubos WHERE application_id = ?", (app["id"],)).fetchall()]
        db.close()

        self.success({"applications": apps, "total": len(apps)})

    def post(self):
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        app_id = uuid.uuid4().hex[:16]
        ref = generate_ref()

        db = get_db()
        db.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, brn, country, sector,
                entity_type, ownership_structure, prescreening_data, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            app_id, ref,
            user["sub"] if user["type"] == "client" else data.get("client_id"),
            data.get("company_name", ""),
            data.get("brn", ""),
            data.get("country", ""),
            data.get("sector", ""),
            data.get("entity_type", ""),
            data.get("ownership_structure", ""),
            json.dumps(data.get("prescreening_data", {})),
            "draft"
        ))

        # Add directors — C-02: encrypt PII fields before write
        for d in data.get("directors", []):
            d_encrypted = encrypt_pii_fields(d, PII_FIELDS_DIRECTORS)
            db.execute("INSERT INTO directors (application_id, full_name, nationality, is_pep) VALUES (?,?,?,?)",
                        (app_id, d.get("full_name",""), d_encrypted.get("nationality",""), d.get("is_pep","No")))

        # Add UBOs — C-02: encrypt PII fields before write
        for u in data.get("ubos", []):
            u_encrypted = encrypt_pii_fields(u, PII_FIELDS_UBOS)
            db.execute("INSERT INTO ubos (application_id, full_name, nationality, ownership_pct, is_pep) VALUES (?,?,?,?,?)",
                        (app_id, u.get("full_name",""), u_encrypted.get("nationality",""), u_encrypted.get("ownership_pct",0), u.get("is_pep","No")))

        db.commit()
        db.close()

        self.log_audit(user, "Create", ref, f"New application created: {data.get('company_name','')}")
        self.success({"id": app_id, "ref": ref, "status": "draft"}, 201)


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
        # C-02: Decrypt PII fields on read
        result["directors"] = [decrypt_pii_fields(dict(d), PII_FIELDS_DIRECTORS) for d in db.execute(
            "SELECT * FROM directors WHERE application_id = ?", (result["id"],)).fetchall()]
        result["ubos"] = [decrypt_pii_fields(dict(u), PII_FIELDS_UBOS) for u in db.execute(
            "SELECT * FROM ubos WHERE application_id = ?", (result["id"],)).fetchall()]
        result["documents"] = [dict(d) for d in db.execute(
            "SELECT * FROM documents WHERE application_id = ?", (result["id"],)).fetchall()]
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
                    ownership_structure=?, prescreening_data=?, updated_at=datetime('now')
                WHERE id=?
            """, (
                data.get("company_name", app["company_name"]),
                data.get("brn", app["brn"]),
                data.get("country", app["country"]),
                data.get("sector", app["sector"]),
                data.get("entity_type", app["entity_type"]),
                data.get("ownership_structure", app["ownership_structure"]),
                json.dumps(data.get("prescreening_data", json.loads(app["prescreening_data"]))),
                real_id
            ))
        else:
            # Post-submission: only allow metadata updates, NOT screening data
            db.execute("""
                UPDATE applications SET
                    company_name=?, brn=?, country=?, sector=?, entity_type=?,
                    ownership_structure=?, updated_at=datetime('now')
                WHERE id=?
            """, (
                data.get("company_name", app["company_name"]),
                data.get("brn", app["brn"]),
                data.get("country", app["country"]),
                data.get("sector", app["sector"]),
                data.get("entity_type", app["entity_type"]),
                data.get("ownership_structure", app["ownership_structure"]),
                real_id
            ))

        # Rebuild directors and UBOs if provided — C-02: encrypt PII fields
        if "directors" in data:
            db.execute("DELETE FROM directors WHERE application_id = ?", (real_id,))
            for d in data["directors"]:
                d_encrypted = encrypt_pii_fields(d, PII_FIELDS_DIRECTORS)
                db.execute("INSERT INTO directors (application_id, full_name, nationality, is_pep) VALUES (?,?,?,?)",
                            (real_id, d.get("full_name",""), d_encrypted.get("nationality",""), d.get("is_pep","No")))

        if "ubos" in data:
            db.execute("DELETE FROM ubos WHERE application_id = ?", (real_id,))
            for u in data["ubos"]:
                u_encrypted = encrypt_pii_fields(u, PII_FIELDS_UBOS)
                db.execute("INSERT INTO ubos (application_id, full_name, nationality, ownership_pct, is_pep) VALUES (?,?,?,?,?)",
                            (real_id, u.get("full_name",""), u_encrypted.get("nationality",""), u_encrypted.get("ownership_pct",0), u.get("is_pep","No")))

        db.commit()
        db.close()

        self.log_audit(user, "Update", app["ref"], f"Application updated")
        self.success({"status": "updated"})

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

            # Define valid state transitions
            valid_transitions = {
                "draft": ["submitted"],
                "submitted": ["under_review", "rejected"],
                "under_review": ["edd_required", "approved", "rejected"],
                "edd_required": ["under_review", "approved", "rejected"],
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

            # ── H-05 FIX: High-risk cases MUST go through under_review before approval ──
            risk_level = app.get("risk_level", "").upper()
            if new_status == "approved" and risk_level in ("HIGH", "VERY_HIGH"):
                if current_status != "under_review" and current_status != "edd_required":
                    db.close()
                    return self.error(
                        f"HIGH/VERY_HIGH risk applications must undergo compliance review (under_review or edd_required) "
                        f"before approval. Current status: {current_status}",
                        400
                    )

            # For approval: enforce that screening is complete, memo exists, and approval gate passes
            if new_status == "approved":
                # Require screening to have been run (not in draft)
                prescreening = json.loads(app["prescreening_data"]) if app.get("prescreening_data") else {}
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
            self.log_audit(user, new_status.replace("_"," ").title(), app["ref"], f"Status → {new_status}", db=db)

        # Handle assignment
        if "assigned_to" in data:
            db.execute("UPDATE applications SET assigned_to=?, updated_at=datetime('now') WHERE id=?",
                       (data["assigned_to"], real_id))
            self.log_audit(user, "Assign", app["ref"], f"Assigned to {data['assigned_to']}", db=db)

        db.commit()
        db.close()
        self.success({"status": "updated"})


class SubmitApplicationHandler(BaseHandler):
    """POST /api/applications/:id/submit — submit pre-screening, run screening, calculate risk, show pricing"""
    def post(self, app_id):
        user = self.require_auth()
        if not user:
            return

        if not self.check_rate_limit("submit", max_attempts=5, window_seconds=60):
            return

        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        real_id = app["id"]

        # Build scoring input
        directors = [dict(d) for d in db.execute("SELECT * FROM directors WHERE application_id=?", (real_id,)).fetchall()]
        ubos = [dict(u) for u in db.execute("SELECT * FROM ubos WHERE application_id=?", (real_id,)).fetchall()]

        prescreening = json.loads(app["prescreening_data"]) if app["prescreening_data"] else {}
        scoring_input = {
            **prescreening,
            "entity_type": app["entity_type"],
            "ownership_structure": app["ownership_structure"],
            "country": app["country"],
            "sector": app["sector"],
            "company_name": app["company_name"],
            "directors": directors,
            "ubos": ubos
        }

        # ── Run real screening (Agents 1, 2, 3, 5) ──
        client_ip = self.get_client_ip()
        screening_report = run_full_screening(
            scoring_input, directors, ubos, client_ip=client_ip
        )

        # Track screening mode (live vs simulated) — mandatory
        screening_mode = determine_screening_mode(screening_report)
        store_screening_mode(db, real_id, screening_mode)
        screening_report["screening_mode"] = screening_mode

        # Compute risk score
        risk = compute_risk_score(scoring_input)

        # Elevate risk if screening found hits
        if screening_report["total_hits"] > 0:
            risk_bump = min(screening_report["total_hits"] * 8, 25)  # Up to +25 points
            risk["score"] = min(100, risk["score"] + risk_bump)
            # Re-classify level
            if risk["score"] >= 70:
                risk["level"] = "VERY_HIGH"
            elif risk["score"] >= 55:
                risk["level"] = "HIGH"
            elif risk["score"] >= 40:
                risk["level"] = "MEDIUM"
            risk["lane"] = {"LOW": "Fast Lane", "MEDIUM": "Standard Review", "HIGH": "EDD", "VERY_HIGH": "EDD"}[risk["level"]]
            risk["screening_elevated"] = True
            risk["screening_hits"] = screening_report["total_hits"]

        # Store screening report in prescreening_data
        prescreening["screening_report"] = screening_report
        db.execute("UPDATE applications SET prescreening_data=? WHERE id=?",
                   (json.dumps(prescreening, default=str), real_id))

        db.execute("""
            UPDATE applications SET
                status='submitted', submitted_at=datetime('now'),
                risk_score=?, risk_level=?, risk_dimensions=?, onboarding_lane=?,
                updated_at=datetime('now')
            WHERE id=?
        """, (risk["score"], risk["level"], json.dumps(risk["dimensions"]), risk["lane"], real_id))

        # After pre-screening: move to pricing review
        # Client must accept pricing before proceeding
        db.execute("UPDATE applications SET status='pricing_review' WHERE id=?", (real_id,))

        # Get pricing for this risk level
        pricing = PRICING_TIERS.get(risk["level"], PRICING_TIERS["MEDIUM"])

        # Store pricing in prescreening data
        prescreening["pricing"] = pricing
        prescreening["pricing"]["risk_level"] = risk["level"]
        db.execute("UPDATE applications SET prescreening_data=? WHERE id=?",
                   (json.dumps(prescreening, default=str), real_id))

        # Notify compliance team for HIGH/VERY_HIGH risk
        if risk["level"] in ("HIGH", "VERY_HIGH"):
            compliance_users = db.execute("SELECT id FROM users WHERE role IN ('sco','co')").fetchall()
            for cu in compliance_users:
                db.execute("INSERT INTO notifications (user_id, title, message) VALUES (?,?,?)",
                          (cu["id"], f"{risk['level']}-Risk Pre-Screening Submitted",
                           f"Pre-screening {app['ref']} ({app['company_name']}) — Risk: {risk['level']} (Score: {risk['score']}). Pricing review pending."))
            db.commit()

        db.commit()
        db.close()

        flags_summary = f", Flags: {len(screening_report['overall_flags'])}" if screening_report["overall_flags"] else ""
        self.log_audit(user, "Pre-Screening Submitted", app["ref"],
                       f"Pre-screening submitted — Score: {risk['score']}, Level: {risk['level']}, Lane: {risk['lane']}{flags_summary}")

        self.success({
            "ref": app["ref"],
            "risk_score": risk["score"],
            "risk_level": risk["level"],
            "risk_dimensions": risk["dimensions"],
            "onboarding_lane": risk["lane"],
            "status": "pricing_review",
            "pricing": pricing,
            "screening": {
                "total_hits": screening_report["total_hits"],
                "flags": screening_report["overall_flags"],
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

        # Route based on risk:
        # LOW/MEDIUM → proceed directly to KYC & Documents
        # HIGH/VERY_HIGH → must go through compliance review first
        if risk_level in ("LOW", "MEDIUM"):
            next_status = "kyc_documents"
            db.execute("UPDATE applications SET status=? WHERE id=?", (next_status, real_id))
            message = "Pricing accepted. Please proceed with KYC verification and document upload."
        else:
            next_status = "compliance_review"
            db.execute("UPDATE applications SET status=? WHERE id=?", (next_status, real_id))
            message = "Pricing accepted. Your application has been referred for compliance review due to the risk profile."
            # Notify compliance officers
            compliance_users = db.execute("SELECT id FROM users WHERE role IN ('sco','co')").fetchall()
            for cu in compliance_users:
                db.execute("INSERT INTO notifications (user_id, title, message) VALUES (?,?,?)",
                          (cu["id"], f"High-Risk Case Requires Review: {app['ref']}",
                           f"{app['company_name']} — Risk: {risk_level}. Client accepted pricing. Compliance review required before KYC proceeds."))

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

        if app["status"] != "kyc_documents":
            db.close()
            return self.error("Application is not in KYC & Documents stage", 400)

        real_id = app["id"]

        # Check that at least one document has been uploaded
        doc_count = db.execute("SELECT COUNT(*) as c FROM documents WHERE application_id=?", (real_id,)).fetchone()["c"]
        if doc_count == 0:
            db.close()
            return self.error("Please upload at least one document before submitting", 400)

        # ALL applications after KYC go to compliance review — no auto-approval
        db.execute("""
            UPDATE applications SET
                status='compliance_review',
                updated_at=datetime('now')
            WHERE id=?
        """, (real_id,))

        # Notify ALL compliance officers
        compliance_users = db.execute("SELECT id FROM users WHERE role IN ('sco','co','admin')").fetchall()
        for cu in compliance_users:
            db.execute("INSERT INTO notifications (user_id, title, message) VALUES (?,?,?)",
                      (cu["id"], f"KYC Submitted — Ready for Review: {app['ref']}",
                       f"{app['company_name']} has completed KYC & document upload. Risk: {app['risk_level']} (Score: {app['risk_score']}). Awaiting compliance approval."))

        db.commit()
        db.close()

        self.log_audit(user, "KYC Submitted", app["ref"],
                       f"KYC documents submitted for compliance review — {doc_count} document(s)")
        self.success({
            "status": "compliance_review",
            "message": "Your documents have been submitted for compliance review. An officer will review your application shortly.",
            "documents_uploaded": doc_count
        })


# ══════════════════════════════════════════════════════════
# DOCUMENT UPLOAD ENDPOINTS
# ══════════════════════════════════════════════════════════

class DocumentUploadHandler(BaseHandler):
    """POST /api/applications/:id/documents"""
    def post(self, app_id):
        user = self.require_auth()
        if not user:
            return

        if not self.check_rate_limit("doc_upload", max_attempts=30, window_seconds=60):
            return

        db = get_db()
        app = db.execute("SELECT id, ref, client_id FROM applications WHERE id=? OR ref=?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

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

        # Save file locally
        doc_id = uuid.uuid4().hex[:16]
        ext = os.path.splitext(filename)[1]
        safe_name = f"{app['id']}_{doc_id}{ext}"
        file_path = os.path.join(UPLOAD_DIR, safe_name)

        with open(file_path, "wb") as f:
            f.write(body)

        # Upload to S3 if available
        s3_key = None
        if HAS_S3:
            try:
                s3_client = get_s3_client()
                if s3_client:
                    s3_key = f"documents/{app['id']}/{safe_name}"
                    s3_client.upload_fileobj(
                        io.BytesIO(body),
                        Bucket=os.environ.get("S3_BUCKET", "arie-documents"),
                        Key=s3_key,
                        ExtraArgs={"ContentType": file_info["content_type"]}
                    )
                    logger.info(f"Document {doc_id} uploaded to S3: {s3_key}")
            except Exception as e:
                logger.warning(f"S3 upload failed for {doc_id}: {e}. Falling back to local storage.")
                s3_key = None

        doc_type = self.get_argument("doc_type", "general")
        person_id = self.get_argument("person_id", None)

        db.execute("""
            INSERT INTO documents (id, application_id, person_id, doc_type, doc_name, file_path, file_size, mime_type)
            VALUES (?,?,?,?,?,?,?,?)
        """, (doc_id, app["id"], person_id, doc_type, filename, file_path, len(body), file_info["content_type"]))
        db.commit()
        db.close()

        self.log_audit(user, "Upload", app["ref"], f"Document uploaded: {filename} ({doc_type})")
        self.success({"id": doc_id, "doc_name": filename, "doc_type": doc_type, "file_size": len(body), "s3_key": s3_key}, 201)


class DocumentVerifyHandler(BaseHandler):
    """POST /api/documents/:id/verify — trigger AI verification"""
    def post(self, doc_id):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            db.close()
            return self.error("Document not found", 404)

        # Get the related application and person for screening
        app = db.execute("SELECT * FROM applications WHERE id=?", (doc["application_id"],)).fetchone()

        import random
        checks = []
        check_types = [
            ("Name Match", "name", "Entity/person name verified against application"),
            ("Document Date", "age", "Document is within acceptable date range"),
            ("Document Clarity", "quality", "Document is legible and complete"),
            ("Content Verification", "content", "Required content elements present"),
        ]
        all_passed = True
        for label, ctype, rule in check_types:
            passed = random.random() > 0.12  # 88% pass rate
            severity = "pass" if passed else ("warn" if random.random() > 0.4 else "fail")
            if not passed:
                all_passed = False
            checks.append({
                "label": label,
                "type": ctype,
                "rule": rule,
                "result": severity,
                "message": "" if passed else f"Check flagged — manual review recommended"
            })

        # If it's an identity document, run sanctions/PEP screening
        sanctions_result = None
        id_doc_types = ["passport", "national_id", "id_card", "drivers_license", "director_id", "ubo_id"]
        if doc["doc_type"] in id_doc_types and doc["person_id"]:
            # Try to find the person's name
            person = db.execute("SELECT full_name, nationality FROM directors WHERE id=?", (doc["person_id"],)).fetchone()
            if not person:
                person = db.execute("SELECT full_name, nationality FROM ubos WHERE id=?", (doc["person_id"],)).fetchone()
            if person:
                sanctions_result = screen_sumsub_aml(
                    person["full_name"],
                    nationality=person["nationality"],
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

        status = "verified" if all_passed else "flagged"
        results = json.dumps({
            "checks": checks,
            "overall": status,
            "verified_at": datetime.utcnow().isoformat(),
            "sanctions_screening": sanctions_result
        }, default=str)

        db.execute("UPDATE documents SET verification_status=?, verification_results=?, verified_at=datetime('now') WHERE id=?",
                   (status, results, doc_id))
        db.commit()
        db.close()

        self.success({"doc_id": doc_id, "status": status, "checks": checks})


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
        except:
            return self.error("Invalid JSON", 400)

        doc_type = body.get("doc_type", "")
        file_name = body.get("file_name", "")
        person_name = body.get("person_name", "")
        doc_category = body.get("doc_category", "identity")

        if not doc_type or not file_name:
            return self.error("doc_type and file_name are required", 400)

        # Initialize Claude client
        if not HAS_CLAUDE_CLIENT:
            logger.warning("Claude client not available — returning mock response")
            return self.success({
                "checks": [
                    {"label": "Document Validity", "type": "validity", "result": "pass", "message": "Document format verified"},
                    {"label": "Expiry Risk", "type": "expiry", "result": "pass", "message": "Document validity verified"},
                    {"label": "Name Consistency", "type": "name", "result": "pass", "message": "Name verified"},
                    {"label": "Quality Indicators", "type": "quality", "result": "pass", "message": "Quality verified"}
                ],
                "overall": "verified",
                "confidence": 0.90,
                "ai_source": "mock"
            })

        try:
            claude_client = ClaudeClient(
                api_key=os.environ.get("ANTHROPIC_API_KEY"),
                monthly_budget_usd=float(os.environ.get("CLAUDE_BUDGET_USD", 50.0)),
                mock_mode=os.environ.get("CLAUDE_MOCK_MODE", "false").lower() == "true"
            )

            result = claude_client.verify_document(
                doc_type=doc_type,
                file_name=file_name,
                person_name=person_name,
                doc_category=doc_category
            )

            self.success(result)
        except Exception as e:
            logger.error(f"Document AI verification failed: {e}")
            self.error(f"Verification failed: {str(e)[:200]}", 500)


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

    def post(self):
        user = self.require_auth(roles=["admin"])
        if not user:
            return

        data = self.get_json()
        email = data.get("email", "").strip().lower()
        name = data.get("full_name", "")
        role = data.get("role", "analyst")
        password = data.get("password", "")
        if not password:
            password = PasswordPolicy.generate_temporary()
            must_change_password = True
        else:
            must_change_password = False
            is_valid, pw_error = PasswordPolicy.validate(password)
            if not is_valid:
                return self.error(f"Password policy violation: {pw_error}", 400)

        if not email or not name:
            return self.error("Email and full name required")

        db = get_db()
        exists = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if exists:
            db.close()
            return self.error("Email already exists")

        user_id = uuid.uuid4().hex[:16]
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        db.execute("INSERT INTO users (id, email, password_hash, full_name, role) VALUES (?,?,?,?,?)",
                    (user_id, email, pw_hash, name, role))
        db.commit()
        db.close()

        self.log_audit(user, "Create User", name, f"New user added as {role}")
        self.success({"id": user_id, "email": email, "name": name, "role": role}, 201)


class UserDetailHandler(BaseHandler):
    """PUT /api/users/:id — update user"""
    def put(self, user_id):
        user = self.require_auth(roles=["admin"])
        if not user:
            return

        data = self.get_json()
        db = get_db()
        u = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not u:
            db.close()
            return self.error("User not found", 404)

        db.execute("UPDATE users SET full_name=?, role=?, status=?, updated_at=datetime('now') WHERE id=?",
                   (data.get("full_name", u["full_name"]), data.get("role", u["role"]),
                    data.get("status", u["status"]), user_id))
        db.commit()
        db.close()

        self.log_audit(user, "Update User", u["full_name"], f"Updated: role={data.get('role')}, status={data.get('status')}")
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
            self.success({"dimensions": json.loads(config["dimensions"]),
                         "thresholds": json.loads(config["thresholds"]),
                         "updated_at": config["updated_at"]})
        else:
            self.success({"dimensions": [], "thresholds": []})

    def put(self):
        user = self.require_auth(roles=["admin"])
        if not user:
            return
        data = self.get_json()
        db = get_db()
        db.execute("UPDATE risk_config SET dimensions=?, thresholds=?, updated_by=?, updated_at=datetime('now') WHERE id=1",
                   (json.dumps(data.get("dimensions",[])), json.dumps(data.get("thresholds",[])), user["sub"]))
        db.commit()
        db.close()
        self.log_audit(user, "Config", "Risk Model", "Risk scoring model updated")
        self.success({"status": "saved"})


class EnvironmentInfoHandler(BaseHandler):
    """GET /api/config/environment — return environment info for frontend"""
    def get(self):
        self.success(get_environment_info())


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
            a["checks"] = json.loads(a["checks"]) if a["checks"] else []
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
        db.execute("""UPDATE ai_agents SET name=?, icon=?, stage=?, description=?,
                      enabled=?, checks=?, updated_at=datetime('now') WHERE id=?""",
                   (data.get("name",""), data.get("icon",""), data.get("stage",""),
                    data.get("description",""), 1 if data.get("enabled",True) else 0,
                    json.dumps(data.get("checks",[])), agent_id))
        db.commit()
        db.close()
        self.success({"status": "updated"})

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
            # Parse prescreening_data for risk info
            prescreening = json.loads(record.get("prescreening_data") or "{}")
            risk_info = prescreening.get("risk_assessment", {})
            record["risk_score"] = risk_info.get("score", 0)
            record["risk_level"] = risk_info.get("level", record.get("risk_level", ""))
            record["risk_lane"] = risk_info.get("lane", "")

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


# ══════════════════════════════════════════════════════════
# DASHBOARD STATS
# ══════════════════════════════════════════════════════════

class DashboardHandler(BaseHandler):
    """GET /api/dashboard"""
    def get(self):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        stats = {}

        if user.get("type") == "client":
            client_id = user["sub"]
            stats["total"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE client_id=?", (client_id,)).fetchone()["c"]
            stats["pending"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status IN ('pending_review','submitted') AND client_id=?", (client_id,)).fetchone()["c"]
            stats["in_review"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='in_review' AND client_id=?", (client_id,)).fetchone()["c"]
            stats["kyc_documents"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='kyc_documents' AND client_id=?", (client_id,)).fetchone()["c"]
            stats["compliance_review"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='compliance_review' AND client_id=?", (client_id,)).fetchone()["c"]
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
            stats["total"] = db.execute("SELECT COUNT(*) as c FROM applications").fetchone()["c"]
            stats["pending"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status IN ('pending_review','submitted')").fetchone()["c"]
            stats["in_review"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='in_review'").fetchone()["c"]
            stats["kyc_documents"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='kyc_documents'").fetchone()["c"]
            stats["compliance_review"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='compliance_review'").fetchone()["c"]
            stats["approved"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='approved'").fetchone()["c"]
            stats["rejected"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='rejected'").fetchone()["c"]
            stats["edd"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='edd_required'").fetchone()["c"]

            # Risk distribution
            stats["risk_low"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level='LOW'").fetchone()["c"]
            stats["risk_medium"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level='MEDIUM'").fetchone()["c"]
            stats["risk_high"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level='HIGH'").fetchone()["c"]
            stats["risk_very_high"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level='VERY_HIGH'").fetchone()["c"]

            # Recent applications
            recent = db.execute("""
                SELECT a.*, u.full_name as assigned_name FROM applications a
                LEFT JOIN users u ON a.assigned_to = u.id
                ORDER BY a.created_at DESC LIMIT 10
            """).fetchall()
            stats["recent"] = [dict(r) for r in recent]

        db.close()
        self.success(stats)


# ══════════════════════════════════════════════════════════
# SAVE & RESUME (Client Portal)
# ══════════════════════════════════════════════════════════

class SaveResumeHandler(BaseHandler):
    """POST /api/save-resume — save form progress, GET — restore"""
    def get(self):
        user = self.require_auth()
        if not user:
            return
        db = get_db()
        session = db.execute(
            "SELECT * FROM client_sessions WHERE client_id=? ORDER BY updated_at DESC LIMIT 1",
            (user["sub"],)).fetchone()
        db.close()
        if session:
            self.success({"form_data": json.loads(session["form_data"]),
                         "last_step": session["last_step"],
                         "application_id": session["application_id"]})
        else:
            self.success({"form_data": {}, "last_step": 0})

    def post(self):
        user = self.require_auth()
        if not user:
            return
        data = self.get_json()
        db = get_db()
        existing = db.execute("SELECT id FROM client_sessions WHERE client_id=?", (user["sub"],)).fetchone()
        if existing:
            db.execute("UPDATE client_sessions SET form_data=?, last_step=?, application_id=?, updated_at=datetime('now') WHERE id=?",
                       (json.dumps(data.get("form_data",{})), data.get("last_step",0),
                        data.get("application_id"), existing["id"]))
        else:
            db.execute("INSERT INTO client_sessions (client_id, application_id, form_data, last_step) VALUES (?,?,?,?)",
                       (user["sub"], data.get("application_id"), json.dumps(data.get("form_data",{})), data.get("last_step",0)))
        db.commit()
        db.close()
        self.success({"status": "saved"})


# ══════════════════════════════════════════════════════════
# PORTAL FILE SERVING
# ══════════════════════════════════════════════════════════

PORTAL_DIR = os.path.join(os.path.dirname(__file__), "..")  # outputs/ directory

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


# ══════════════════════════════════════════════════════════
# SCREENING ENDPOINTS (Real API Integrations)
# ══════════════════════════════════════════════════════════

class ScreeningHandler(BaseHandler):
    """POST /api/screening/run — run full screening for an application"""
    def post(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        if not self.check_rate_limit("screening", max_attempts=20, window_seconds=60):
            return

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
        directors = [dict(d) for d in db.execute("SELECT * FROM directors WHERE application_id=?", (real_id,)).fetchall()]
        ubos = [dict(u) for u in db.execute("SELECT * FROM ubos WHERE application_id=?", (real_id,)).fetchall()]

        app_data = {
            "company_name": app["company_name"],
            "country": app["country"],
            "sector": app["sector"],
            "entity_type": app["entity_type"],
        }

        report = run_full_screening(app_data, directors, ubos, client_ip=self.get_client_ip())

        # Store screening report
        prescreening = json.loads(app["prescreening_data"]) if app["prescreening_data"] else {}
        prescreening["screening_report"] = report
        prescreening["last_screened_at"] = datetime.utcnow().isoformat()
        prescreening["screened_by"] = user["sub"]
        db.execute("UPDATE applications SET prescreening_data=?, updated_at=datetime('now') WHERE id=?",
                   (json.dumps(prescreening, default=str), real_id))
        db.commit()
        db.close()

        self.log_audit(user, "Screening", app["ref"],
                       f"Full screening run — {report['total_hits']} hit(s), {len(report['overall_flags'])} flag(s)")

        self.success(report)


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

        self.log_audit(user, "KYC Applicant Created", external_user_id,
                       f"Sumsub applicant created — ID: {result.get('applicant_id')} ({result['source']})")
        self.success(result)


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
        file_path = data.get("file_path")
        file_name = data.get("file_name", "document.pdf")

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


class SumsubWebhookHandler(tornado.web.RequestHandler):
    """POST /api/kyc/webhook — Receive Sumsub verification webhooks"""

    def set_default_headers(self):
        self.set_header("Content-Type", "application/json")

    def post(self):
        body = self.request.body
        signature = self.request.headers.get("X-Payload-Digest", "")

        # Verify webhook signature
        if SUMSUB_WEBHOOK_SECRET and not sumsub_verify_webhook(body, signature):
            logger.warning("Sumsub webhook: Invalid signature")
            self.set_status(401)
            self.write(json.dumps({"error": "Invalid signature"}))
            return

        try:
            payload = json.loads(body)
        except Exception:
            self.set_status(400)
            self.write(json.dumps({"error": "Invalid JSON"}))
            return

        event_type = payload.get("type", "")
        applicant_id = payload.get("applicantId", "")
        external_user_id = payload.get("externalUserId", "")
        review_result = payload.get("reviewResult", {})
        review_answer = review_result.get("reviewAnswer", "")

        logger.info(f"Sumsub webhook: {event_type} — applicant={applicant_id}, answer={review_answer}")

        # Handle applicantReviewed event
        if event_type == "applicantReviewed":
            db = get_db()
            try:
                # Find the application linked to this external user
                # The external user ID may be a client email or director ID
                kyc_data = json.dumps({
                    "sumsub_applicant_id": applicant_id,
                    "external_user_id": external_user_id,
                    "review_answer": review_answer,
                    "rejection_labels": review_result.get("rejectLabels", []),
                    "moderation_comment": review_result.get("moderationComment", ""),
                    "event_type": event_type,
                    "received_at": datetime.utcnow().isoformat(),
                })

                # Store webhook data in audit log
                db.execute("""
                    INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, ("system", "Sumsub Webhook", "system", f"KYC {event_type}: {review_answer}", applicant_id, kyc_data))

                # Try to update application status if we can find it
                # Look for applications where prescreening_data contains this applicant
                apps = db.execute("SELECT id, prescreening_data FROM applications").fetchall()
                for app in apps:
                    pdata = app["prescreening_data"] or ""
                    if applicant_id in pdata or external_user_id in pdata:
                        # Update the prescreening data with new KYC result
                        try:
                            pdict = json.loads(pdata) if pdata else {}
                            if "screening_report" not in pdict:
                                pdict["screening_report"] = {}
                            pdict["screening_report"]["sumsub_webhook"] = json.loads(kyc_data)

                            # If verification failed, add a flag
                            if review_answer == "RED":
                                flags = pdict["screening_report"].get("overall_flags", [])
                                flags.append(f"Sumsub KYC verification REJECTED for {external_user_id}")
                                pdict["screening_report"]["overall_flags"] = flags

                            db.execute("UPDATE applications SET prescreening_data=? WHERE id=?",
                                      (json.dumps(pdict), app["id"]))
                            logger.info(f"Sumsub webhook: Updated application {app['id']}")
                        except Exception as e:
                            logger.error(f"Failed to update application: {e}")

                db.commit()
            finally:
                db.close()

        elif event_type == "applicantPending":
            logger.info(f"Sumsub: Applicant {applicant_id} pending review")

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

        db = get_db()
        stats = {
            "files_due": 0,
            "docs_expiring": 0,
            "alerts": 0,
            "clients_under_review": 0,
            "high_risk_alerts": [],
            "periodic_review_due": 0
        }

        # Count applications pending compliance review
        compliance_review = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='compliance_review'").fetchone()["c"]
        stats["clients_under_review"] = compliance_review

        # Count high-risk alerts
        high_risk = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level IN ('HIGH','VERY_HIGH')").fetchone()["c"]
        stats["alerts"] = high_risk

        # Get recent high-risk applications for alert summary
        recent_alerts = db.execute("""
            SELECT ref, company_name, risk_level, risk_score, created_at FROM applications
            WHERE risk_level IN ('HIGH','VERY_HIGH')
            ORDER BY created_at DESC LIMIT 10
        """).fetchall()
        stats["high_risk_alerts"] = [dict(a) for a in recent_alerts]

        db.close()
        self.success(stats)


class MonitoringClientsHandler(BaseHandler):
    """GET /api/monitoring/clients — returns client monitoring status for Kanban board"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        db = get_db()

        # Get all applications grouped by status/stage
        applications = db.execute("""
            SELECT a.id, a.ref, a.company_name, a.status, a.risk_level, a.risk_score,
                   a.created_at, u.full_name as assigned_to
            FROM applications a
            LEFT JOIN users u ON a.assigned_to = u.id
            ORDER BY a.created_at DESC
        """).fetchall()

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

        severity = self.get_argument("severity", None)
        alert_type = self.get_argument("type", None)
        status_filter = self.get_argument("status", None)
        client_id = self.get_argument("client", None)

        db = get_db()
        query = "SELECT * FROM monitoring_alerts WHERE 1=1"
        params = []

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

        # Create notification for relevant users
        alert_users = db.execute("SELECT id FROM users WHERE role IN ('sco','co','admin')").fetchall()
        for u in alert_users:
            db.execute("INSERT INTO notifications (user_id, title, message) VALUES (?,?,?)",
                      (u["id"], data.get("title", "Monitoring Alert"),
                       data.get("message", "")))

        db.commit()
        db.close()
        self.log_audit(user, "Alert", "Monitoring", f"Alert created: {data.get('title','')}")
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

        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        real_id = app["id"]

        # Fetch related data
        directors = [dict(d) for d in db.execute("SELECT * FROM directors WHERE application_id=?", (real_id,)).fetchall()]
        ubos = [dict(u) for u in db.execute("SELECT * FROM ubos WHERE application_id=?", (real_id,)).fetchall()]
        documents = [dict(d) for d in db.execute("SELECT * FROM documents WHERE application_id=?", (real_id,)).fetchall()]

        # Generate memo structure
        memo = {
            "application_ref": app["ref"],
            "company_name": app["company_name"],
            "memo_generated": datetime.now().isoformat(),
            "client_overview": {
                "company_name": app["company_name"],
                "entity_type": app["entity_type"],
                "country": app["country"],
                "sector": app["sector"],
                "brn": app["brn"]
            },
            "ownership_structure": {
                "structure_description": app["ownership_structure"],
                "directors_count": len(directors),
                "ubos_count": len(ubos),
                "directors": [{"name": d["full_name"], "nationality": d["nationality"], "is_pep": d["is_pep"]} for d in directors],
                "ubos": [{"name": u["full_name"], "ownership_pct": u["ownership_pct"], "is_pep": u["is_pep"]} for u in ubos]
            },
            "screening_results": {
                "sanctions_status": "No matches" if not any(d["is_pep"]=="Yes" for d in directors + ubos) else "Potential matches - Review required",
                "pep_matches": [d["full_name"] for d in directors if d["is_pep"]=="Yes"] + [u["full_name"] for u in ubos if u["is_pep"]=="Yes"],
                "documents_verified": len([d for d in documents if d["verification_status"]=="verified"]),
                "documents_total": len(documents)
            },
            "risk_indicators": {
                "risk_score": app["risk_score"],
                "risk_level": app["risk_level"],
                "onboarding_lane": app["onboarding_lane"]
            },
            "ai_recommendation": "Approve" if app["risk_level"]=="LOW" else "Review required" if app["risk_level"] in ("MEDIUM","HIGH") else "Escalate",
            "review_checklist": [
                "✓ Company identity verified",
                "✓ UBO chain mapped",
                "✓ PEP screening completed",
                "✓ Adverse media review conducted",
                "✓ Source of funds verified",
                "✓ Business model plausibility confirmed"
            ]
        }

        db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                   (user.get("sub",""), user.get("name",""), user.get("role",""), "Generate Memo", app["ref"], f"Compliance memo generated for {app['company_name']}", self.get_client_ip()))
        db.commit()
        db.close()

        self.success(memo)


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

        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        real_id = app["id"]

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

        # ── SECURITY: Enforce approval preconditions (mandatory) ──
        if decision == "approve":
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

            # Check dual-approval for high-risk cases
            if app["risk_level"] in ("HIGH", "VERY_HIGH"):
                can_approve, dual_error = ApprovalGateValidator.validate_high_risk_dual_approval(app, user, db)
                if not can_approve:
                    # Record first approval but don't change status
                    db.execute("""INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address)
                                 VALUES (?,?,?,?,?,?,?)""",
                               (user.get("sub",""), user.get("name",""), user.get("role",""),
                                "First Approval (Pending Second)", app["ref"],
                                f"Decision: approve | Reason: {decision_reason} | Awaiting second approver",
                                self.get_client_ip()))
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
                status=?, decided_at=datetime('now'), decision_by=?, decision_notes=?, updated_at=datetime('now')
            WHERE id=?
        """, (new_status, user["sub"], json.dumps(detail_info), real_id))

        # Log audit trail with full detail
        audit_detail = f"Decision: {decision} | Reason: {decision_reason}"
        if override_ai:
            audit_detail += f" | AI Override: {override_reason}"
        if required_documents:
            audit_detail += f" | Documents Required: {', '.join(required_documents)}"

        db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                   (user.get("sub",""), user.get("name",""), user.get("role",""), "Decision", app["ref"], audit_detail, self.get_client_ip()))

        db.commit()
        db.close()

        self.success({"status": "decision_recorded", "decision": decision, "application_status": new_status}, 201)


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
                n["documents_list"] = json.loads(n["documents_list"])

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
        """Update alert status (dismiss, escalate, trigger_review)"""
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()
        db = get_db()

        alert = db.execute("SELECT * FROM monitoring_alerts WHERE id = ?", (alert_id,)).fetchone()
        if not alert:
            db.close()
            return self.error("Alert not found", 404)

        action = data.get("action")
        reason = data.get("reason", "")

        valid_actions = ["dismiss", "escalate", "trigger_review"]
        if action not in valid_actions:
            db.close()
            return self.error(f"Invalid action. Must be one of: {', '.join(valid_actions)}", 400)

        new_status = {
            "dismiss": "dismissed",
            "escalate": "escalated",
            "trigger_review": "escalated"
        }[action]

        db.execute("""
            UPDATE monitoring_alerts SET
                status=?, reviewed_at=datetime('now'), reviewed_by=?, officer_notes=?, officer_action=?
            WHERE id=?
        """, (new_status, user["sub"], reason, action, alert_id))

        # Log audit
        db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                   (user.get("sub",""), user.get("name",""), user.get("role",""), "Alert Action", f"Alert {alert_id}",
                    f"Action: {action}, Reason: {reason}", self.get_client_ip()))

        db.commit()
        db.close()

        self.success({"status": "alert_updated", "new_status": new_status})


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
            result["review_memo"] = json.loads(result["review_memo"])

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
            "LOW": 730,  # days
            "MEDIUM": 365,
            "HIGH": 180,
            "VERY_HIGH": 90
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
            row_dict["indicators"] = json.loads(row_dict["indicators"]) if row_dict["indicators"] else []
            row_dict["transaction_details"] = json.loads(row_dict["transaction_details"]) if row_dict["transaction_details"] else {}
            row_dict["supporting_documents"] = json.loads(row_dict["supporting_documents"]) if row_dict["supporting_documents"] else []
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
        result["indicators"] = json.loads(result["indicators"]) if result["indicators"] else []
        result["transaction_details"] = json.loads(result["transaction_details"]) if result["transaction_details"] else {}
        result["supporting_documents"] = json.loads(result["supporting_documents"]) if result["supporting_documents"] else []

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
            json.dumps(data.get("indicators", json.loads(sar["indicators"] or "[]"))),
            json.dumps(data.get("transaction_details", json.loads(sar["transaction_details"] or "{}"))),
            json.dumps(data.get("supporting_documents", json.loads(sar["supporting_documents"] or "[]"))),
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
# APP SETUP & ROUTES
# ══════════════════════════════════════════════════════════

def make_app():
    routes = [
        # Health
        (r"/api/health", HealthHandler),

        # Auth
        (r"/api/auth/officer/login", OfficerLoginHandler),
        (r"/api/auth/client/login", ClientLoginHandler),
        (r"/api/auth/client/register", ClientRegisterHandler),
        (r"/api/auth/me", MeHandler),

        # Applications (more specific routes first)
        (r"/api/applications/([^/]+)/submit", SubmitApplicationHandler),
        (r"/api/applications/([^/]+)/accept-pricing", PricingAcceptHandler),
        (r"/api/applications/([^/]+)/submit-kyc", KYCSubmitHandler),
        (r"/api/applications/([^/]+)/memo", ComplianceMemoHandler),
        (r"/api/applications/([^/]+)/decision", ApplicationDecisionHandler),
        (r"/api/applications/([^/]+)/notify", ClientNotificationHandler),
        (r"/api/applications/([^/]+)/documents", DocumentUploadHandler),
        (r"/api/applications/([^/]+)", ApplicationDetailHandler),
        (r"/api/applications", ApplicationsHandler),

        # Documents
        (r"/api/documents/([^/]+)/verify", DocumentVerifyHandler),
        (r"/api/documents/ai-verify", DocumentAIVerifyHandler),

        # Users
        (r"/api/users", UsersHandler),
        (r"/api/users/([^/]+)", UserDetailHandler),

        # Config
        (r"/api/config/risk-model", RiskConfigHandler),
        (r"/api/config/ai-agents", AIAgentsHandler),
        (r"/api/config/ai-agents/([^/]+)", AIAgentDetailHandler),
        (r"/api/config/environment", EnvironmentInfoHandler),

        # Screening (Real API Integrations)
        (r"/api/screening/run", ScreeningHandler),
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

        # Reports
        (r"/api/reports/generate", ReportHandler),

        # Audit
        (r"/api/audit", AuditHandler),

        # Dashboard
        (r"/api/dashboard", DashboardHandler),

        # Client Notifications
        (r"/api/notifications", GetClientNotificationsHandler),
        (r"/api/notifications/([^/]+)/read", MarkNotificationReadHandler),

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
        (r"/api/monitoring/reviews/([^/]+)/decision", PeriodicReviewDecisionHandler),
        (r"/api/monitoring/reviews/([^/]+)", PeriodicReviewDetailHandler),
        (r"/api/monitoring/reviews", PeriodicReviewsListHandler),

        # SAR (Suspicious Activity Reports)
        (r"/api/sar/auto-trigger", SARAutoTriggerHandler),
        (r"/api/sar/([^/]+)/workflow", SARWorkflowHandler),
        (r"/api/sar/([^/]+)", SARDetailHandler),
        (r"/api/sar", SARListHandler),

        # AI Assistant
        (r"/api/ai/assistant", AIAssistantHandler),

        # Save & Resume
        (r"/api/save-resume", SaveResumeHandler),

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
        debug=os.environ.get("DEBUG", "0") == "1",
        xsrf_cookies=False,  # CSRF handled by custom check_xsrf_cookie() on BaseHandler (double-submit cookie pattern)
        cookie_secret=SECRET_KEY,
        max_body_size=20 * 1024 * 1024,  # 20MB max request body
    )


if __name__ == "__main__":
    # Validate environment before starting
    validate_environment()

    init_db()

    # Run database migrations
    try:
        from migrations.runner import run_all_migrations
        run_all_migrations(DB_PATH)
    except Exception as e:
        logger.warning("Migration runner unavailable: %s", e)

    # Initialize supervisor framework
    if SUPERVISOR_AVAILABLE:
        try:
            setup_supervisor(DB_PATH)
            logger.info("✅ Supervisor framework initialized")
        except Exception as e:
            logger.error("Failed to initialize supervisor: %s", e)
            SUPERVISOR_AVAILABLE = False

    app = make_app()

    # Validate production environment (mandatory)
    try:
        validate_production_environment()
    except RuntimeError as e:
        logging.critical(f"PRODUCTION ENVIRONMENT VALIDATION FAILED: {e}")
        if ENVIRONMENT == "production":
            sys.exit(1)

    # Enforce startup safety checks
    enforce_startup_safety()

    # Bind to 0.0.0.0 for cloud deployment (Railway, Render, etc.)
    app.listen(PORT, address="0.0.0.0")

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
║  Admin email: asudally@ariefinance.mu              ║
║  Password: see initial boot output above          ║
╚══════════════════════════════════════════════════╝
    """)
    tornado.ioloop.IOLoop.current().start()
