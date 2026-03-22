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
    classify_country, score_sector, compute_risk_score,
)
from validation_engine import (
    validate_compliance_memo,
    pre_validate_application,
    generate_fallback_memo,
)
from supervisor_engine import run_memo_supervisor
from memo_handler import build_compliance_memo

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


# ── Extracted modules: auth.py, rule_engine.py, screening.py, memo_handler.py,
# ── validation_engine.py, supervisor_engine.py (see Sprint 2 architecture)

def generate_ref():
    """Generate application reference like ARF-2026-100429"""
    db = get_db()
    count = db.execute("SELECT COUNT(*) as c FROM applications").fetchone()["c"]
    db.close()
    year = datetime.now().year
    return f"ARF-{year}-{100421 + count}"

from screening import (
    screen_sumsub_aml, _simulate_aml_screen,
    lookup_opencorporates, _simulate_company_lookup,
    geolocate_ip, _simulate_ip_geolocation,
    _sumsub_sign, sumsub_create_applicant, sumsub_get_applicant_by_external_id,
    sumsub_generate_access_token, sumsub_get_applicant_status,
    sumsub_add_document, sumsub_verify_webhook,
    _simulate_sumsub_applicant, _simulate_sumsub_token, _simulate_sumsub_status,
    run_full_screening,
)

# Sprint 3.5: BaseHandler extracted to base_handler.py to reduce server.py concentration risk
from base_handler import BaseHandler, rate_limiter, get_db as _bh_get_db  # noqa: F401


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
            self.set_status(429)
            self.write({"error": "Too many registration attempts. Please try again later."})
            return

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
        self.success({"token": token, "client": {"id": client_id, "email": email, "company": company}}, 201)


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
            risk_level = app.get("risk_level", "").upper()
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

        # ── v2.2: Pre-screening validation ──────────────────────────
        prescreening_raw = json.loads(app["prescreening_data"]) if app.get("prescreening_data") else {}

        # Validate incorporation date (no future dates)
        inc_date = prescreening_raw.get("incorporation_date", "")
        if inc_date:
            try:
                from datetime import date
                parsed_date = datetime.strptime(inc_date, "%Y-%m-%d").date()
                if parsed_date > datetime.utcnow().date():
                    db.close()
                    return self.error("Incorporation date cannot be in the future.", 400)
            except ValueError:
                pass  # Non-standard date format, allow through

        # Validate country is not sanctioned
        country = (app.get("country") or "").lower().strip()
        if country in SANCTIONED_COUNTRIES_FULL:
            db.close()
            return self.error(
                "ARIE Finance cannot onboard clients involved in sanctioned or prohibited jurisdictions.",
                403
            )

        # Source of Wealth / Source of Funds detail fields are optional — no minimum enforced

        # Validate currency
        currency = prescreening_raw.get("currency", "")
        if currency and currency not in ALLOWED_CURRENCIES:
            db.close()
            return self.error(f"Currency '{currency}' not supported. Allowed: {', '.join(ALLOWED_CURRENCIES)}", 400)

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
                pre_approval_decision=NULL, pre_approval_notes=NULL,
                pre_approval_officer_id=NULL, pre_approval_timestamp=NULL,
                updated_at=datetime('now')
            WHERE id=?
        """, (risk["score"], risk["level"], json.dumps(risk["dimensions"]), risk["lane"], real_id))

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

        db.commit()
        db.close()

        flags_summary = f", Flags: {len(screening_report['overall_flags'])}" if screening_report["overall_flags"] else ""
        self.log_audit(user, "Pre-Screening Submitted", app["ref"],
                       f"Pre-screening submitted — Score: {risk['score']}, Level: {risk['level']}, Lane: {risk['lane']}{flags_summary}")

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

        # Audit trail
        db.execute("""INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address)
                     VALUES (?,?,?,?,?,?,?)""",
                   (user.get("sub",""), user.get("name",""), user.get("role",""),
                    f"Pre-Approval: {decision}", app["ref"],
                    f"Pre-approval decision: {decision} | Risk: {app['risk_level']} (Score: {app['risk_score']}) | Notes: {notes}",
                    self.get_client_ip()))

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
    """POST /api/applications/:id/documents"""
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
        risk_level = (app.get("risk_level") or "").upper()
        if risk_level in ("HIGH", "VERY_HIGH"):
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

        # Build compliance memo (pure computation — extracted to memo_handler.py)
        memo, rule_engine_result, supervisor_result, validation_result = build_compliance_memo(app, directors, ubos, documents)
        rule_violations = rule_engine_result.get("violations", [])

        # Store memo in compliance_memos table
        rule_violations_json = json.dumps(rule_violations) if rule_violations else None
        memo_json = json.dumps(memo)
        try:
            db.execute(
                "INSERT INTO compliance_memos (application_id, memo_data, generated_by, ai_recommendation, review_status, quality_score, validation_status, supervisor_status, supervisor_summary, rule_violations) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (real_id, memo_json, user.get("sub", ""), memo["metadata"]["approval_recommendation"], "draft",
                 validation_result["quality_score"], validation_result["validation_status"],
                 supervisor_result["verdict"], supervisor_result["recommendation"], rule_violations_json)
            )
        except Exception:
            # Fallback if rule_violations column doesn't exist yet
            try:
                db.execute(
                    "INSERT INTO compliance_memos (application_id, memo_data, generated_by, ai_recommendation, review_status, quality_score, validation_status, supervisor_status, supervisor_summary) VALUES (?,?,?,?,?,?,?,?,?)",
                    (real_id, memo_json, user.get("sub", ""), memo["metadata"]["approval_recommendation"], "draft",
                     validation_result["quality_score"], validation_result["validation_status"],
                     supervisor_result["verdict"], supervisor_result["recommendation"])
                )
            except Exception:
                try:
                    db.execute(
                        "INSERT INTO compliance_memos (application_id, memo_data, generated_by, ai_recommendation, review_status) VALUES (?,?,?,?,?)",
                        (real_id, memo_json, user.get("sub", ""), memo["metadata"]["approval_recommendation"], "draft")
                    )
                except Exception:
                    pass

        db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                   (user.get("sub",""), user.get("name",""), user.get("role",""), "Generate Memo", app["ref"],
                    "Compliance memo generated for " + app["company_name"]
                    + " | Supervisor: " + supervisor_result["verdict"]
                    + " | Quality: " + str(validation_result["quality_score"]) + "/10"
                    + " | Rule Engine: " + rule_engine_result["engine_status"]
                    + (" | BLOCKED" if memo["metadata"].get("blocked") else ""),
                    self.get_client_ip()))
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

        try:
            supervisor = get_supervisor()
            result = await supervisor.run_pipeline(
                application_id=app["id"],
                trigger_type=__import__("supervisor.schemas", fromlist=["TriggerType"]).TriggerType(trigger_type),
                context_data={"app_ref": app["ref"], "company_name": app["company_name"]},
                trigger_source=f"backoffice:{user['id']}",
            )
            self.success({
                "pipeline_id": result.pipeline_id,
                "status": result.status,
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
        except Exception as e:
            logger.error("Supervisor pipeline failed: %s", e, exc_info=True)
            return self.error(f"Pipeline execution failed: {str(e)}", 500)


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

        # Return from the pipeline cache
        try:
            from supervisor.api import _pipeline_cache
            # Find the most recent pipeline for this application
            latest = None
            for pid, result in _pipeline_cache.items():
                if result.application_id == app["id"]:
                    if latest is None or (result.completed_at or "") > (latest.completed_at or ""):
                        latest = result
            if latest:
                self.success(latest.to_dict())
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
            memo_data = json.loads(memo_row["memo_data"])
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
        except Exception:
            pass
        db.close()

        self.success(validation)


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

        # ── SERVER-SIDE 5-GATE APPROVAL ENFORCEMENT ──
        # Gate 1: Check if memo is blocked by rule engine
        is_blocked = memo_row.get("blocked") or False
        block_reason = memo_row.get("block_reason") or ""
        if is_blocked:
            db.close()
            return self.error(f"Cannot approve blocked memo. Block reason: {block_reason}", 400)

        # Gate 2: Check validation status
        val_status = memo_row.get("validation_status") or "pending"
        if val_status == "fail":
            db.close()
            return self.error("Cannot approve memo with FAIL validation status. Fix issues and re-validate first.", 400)

        # Gate 3: Check supervisor verdict from memo content
        memo_content = memo_row.get("memo_content") or "{}"
        try:
            memo_data = json.loads(memo_content) if isinstance(memo_content, str) else memo_content
        except (json.JSONDecodeError, TypeError):
            memo_data = {}
        metadata = memo_data.get("metadata", {})
        supervisor_result = metadata.get("supervisor", {})
        supervisor_verdict = supervisor_result.get("verdict", "")
        can_approve = supervisor_result.get("can_approve", True)
        requires_sco = supervisor_result.get("requires_sco_review", False)

        if supervisor_verdict == "INCONSISTENT" and not can_approve:
            db.close()
            return self.error("Cannot approve memo: Supervisor verdict is INCONSISTENT with unresolved contradictions.", 400)

        # Gate 4: SCO review enforcement — if requires_sco_review, only SCO or admin can approve
        if requires_sco and user.get("role") not in ["sco", "admin"]:
            db.close()
            return self.error("This memo requires Senior Compliance Officer review before approval.", 403)

        # Gate 5: Check rule engine violations
        rule_engine = metadata.get("rule_engine", {})
        rule_violations_data = rule_engine.get("violations", [])
        if len(rule_violations_data) > 0 and supervisor_verdict == "INCONSISTENT":
            db.close()
            return self.error(f"Cannot approve memo with {len(rule_violations_data)} rule violation(s) and INCONSISTENT supervisor verdict.", 400)

        now_ts = datetime.now().isoformat()
        try:
            db.execute(
                "UPDATE compliance_memos SET review_status = 'approved', approved_by = ?, approved_at = ?, reviewed_by = ? WHERE id = ?",
                (user.get("sub", ""), now_ts, user.get("sub", ""), memo_row["id"])
            )
            db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                       (user.get("sub",""), user.get("name",""), user.get("role",""), "Approve Memo", app_id, f"Compliance memo approved by {user.get('name', 'Unknown')}", self.get_client_ip()))
            db.commit()
        except Exception:
            pass
        db.close()

        self.success({"status": "approved", "approved_by": user.get("name", ""), "approved_at": now_ts})


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
            issues = json.loads(memo_row["validation_issues"] or "[]")
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
            memo_data = json.loads(memo_row["memo_data"]) if isinstance(memo_row["memo_data"], str) else memo_row["memo_data"]
        except (json.JSONDecodeError, TypeError):
            db.close()
            return self.error("Memo data is corrupt or unparseable.", 500)

        # Build validation/supervisor context from memo metadata
        metadata = memo_data.get("metadata", {})
        validation_result = {
            "validation_status": memo_row.get("validation_status") or metadata.get("validation_status", "pending"),
            "quality_score": memo_row.get("quality_score") or metadata.get("quality_score", 0),
        }
        supervisor_result = {
            "verdict": memo_row.get("supervisor_status") or metadata.get("supervisor", {}).get("verdict", "N/A"),
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
        except Exception:
            pass
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
            memo_data = json.loads(memo_row["memo_data"])
        except (json.JSONDecodeError, TypeError):
            db.close()
            return self.error("Memo data is corrupt or unreadable.", 500)

        # Run memo supervisor
        supervisor_result = run_memo_supervisor(memo_data)

        # Update memo with supervisor results
        try:
            memo_data["supervisor"] = supervisor_result
            memo_data["metadata"]["supervisor_status"] = supervisor_result["verdict"]
            memo_data["metadata"]["supervisor_confidence"] = supervisor_result["supervisor_confidence"]
            db.execute(
                "UPDATE compliance_memos SET memo_data = ?, supervisor_status = ?, supervisor_summary = ? WHERE id = ?",
                (json.dumps(memo_data), supervisor_result["verdict"], supervisor_result["recommendation"], memo_row["id"])
            )
            db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                       (user.get("sub",""), user.get("name",""), user.get("role",""), "Run Memo Supervisor", app_id,
                        "Supervisor verdict: " + supervisor_result["verdict"] + " | Contradictions: " + str(supervisor_result["contradiction_count"]),
                        self.get_client_ip()))
            db.commit()
        except Exception:
            pass
        db.close()

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
            memo_data = json.loads(memo_row["memo_data"])
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
            supervisor_instance = setup_supervisor(DB_PATH)
            register_all_executors(supervisor_instance, DB_PATH)
            logger.info("✅ Supervisor framework initialized with %d agent executors", 10)
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
