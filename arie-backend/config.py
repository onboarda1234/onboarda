"""
ONBOARDA / REGMIND — Unified Configuration Module
===================================================
Single source of truth for all environment configuration.
All modules import from here. No direct os.environ reads elsewhere.

This module consolidates env-var reads that were previously scattered across
server.py, db.py, and claude_client.py into one canonical location.  It
complements environment.py (which owns environment detection and feature
flags) by owning concrete configuration *values*.

Usage:
    from config import (
        ENVIRONMENT, JWT_SECRET, ANTHROPIC_API_KEY,
        DATABASE_URL, PORT, validate_config,
    )
"""

import os
import sys
import logging
import secrets

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""
    pass


# ══════════════════════════════════════════════════════════════
# Environment
# ══════════════════════════════════════════════════════════════

# Canonicalized via environment.canonicalize_environment (audit H8 / PR-13):
# the raw value previously produced split-brain behaviour — ENVIRONMENT=prod
# left every IS_* flag False here (skipping validate_config entirely) while
# other modules string-matched "prod" as production. environment.py imports
# nothing from this module, so this import cannot cycle.
from environment import canonicalize_environment as _canonicalize_environment

# The `or` chain (not getenv defaults) matches environment.py exactly: a
# SET-BUT-EMPTY ENVIRONMENT must fall through to ENV, not shadow it —
# render.yaml services set ENV, and an IaC layer adding an empty ENVIRONMENT
# previously re-split the brain (config→development, environment→ENV value).
ENVIRONMENT = _canonicalize_environment(
    os.environ.get("ENVIRONMENT") or os.environ.get("ENV") or "development"
)
IS_TESTING = ENVIRONMENT == "testing"
IS_DEMO = ENVIRONMENT == "demo"
IS_STAGING = ENVIRONMENT == "staging"
IS_PRODUCTION = ENVIRONMENT == "production"
IS_DEVELOPMENT = ENVIRONMENT in ("development", "testing")


# ══════════════════════════════════════════════════════════════
# Security
# ══════════════════════════════════════════════════════════════

# JWT_SECRET: prefer JWT_SECRET, fall back to SECRET_KEY for backward compat.
# In production, must be explicitly set. In dev, auto-generate per session.
_env_secret = os.getenv("JWT_SECRET") or os.getenv("SECRET_KEY", "")
if not _env_secret and IS_PRODUCTION:
    print("FATAL: JWT_SECRET or SECRET_KEY environment variable is required in production mode.")
    print("       Generate one with: python3 -c \"import secrets; print(secrets.token_hex(64))\"")
    sys.exit(1)
JWT_SECRET = _env_secret or secrets.token_hex(64)

# Backward-compat alias — existing code references SECRET_KEY
SECRET_KEY = JWT_SECRET

PII_ENCRYPTION_KEY = os.getenv("PII_ENCRYPTION_KEY")


# ══════════════════════════════════════════════════════════════
# Database
# ══════════════════════════════════════════════════════════════

DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "arie.db"))
USE_POSTGRES = bool(DATABASE_URL)


# ══════════════════════════════════════════════════════════════
# AI / Anthropic
# ══════════════════════════════════════════════════════════════

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_BUDGET_USD = float(os.getenv("CLAUDE_BUDGET_USD", "50.0"))
CLAUDE_MOCK_MODE = os.getenv("CLAUDE_MOCK_MODE", "false").lower() == "true"
AI_CONFIDENCE_THRESHOLD = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "0.70"))
ARIE_MODEL_FAST = os.getenv("ARIE_MODEL_FAST", "claude-sonnet-4-6")
ARIE_MODEL_THOROUGH = os.getenv("ARIE_MODEL_THOROUGH", "claude-opus-4-6")


# ══════════════════════════════════════════════════════════════
# KYC / Sumsub
# ══════════════════════════════════════════════════════════════

SUMSUB_APP_TOKEN = os.getenv("SUMSUB_APP_TOKEN", "")
SUMSUB_SECRET_KEY = os.getenv("SUMSUB_SECRET_KEY", "")
SUMSUB_BASE_URL = os.getenv("SUMSUB_BASE_URL", "https://api.sumsub.com")
SUMSUB_LEVEL_NAME = os.getenv("SUMSUB_LEVEL_NAME", "basic-kyc-level")
SUMSUB_INDIVIDUAL_LEVEL_NAME = (
    os.getenv("SUMSUB_INDIVIDUAL_LEVEL_NAME")
    or os.getenv("SUMSUB_LEVEL_NAME")
    or "id-and-liveness"
)
SUMSUB_COMPANY_LEVEL_NAME = os.getenv("SUMSUB_COMPANY_LEVEL_NAME", "")
SUMSUB_WEBHOOK_SECRET = os.getenv("SUMSUB_WEBHOOK_SECRET", "")


# ══════════════════════════════════════════════════════════════
# External APIs
# ══════════════════════════════════════════════════════════════

OPENCORPORATES_API_KEY = os.getenv("OPENCORPORATES_API_KEY", "")
OPENCORPORATES_API_URL = os.getenv("OPENCORPORATES_API_URL", "https://api.opencorporates.com/v0.4")
COMPANIES_HOUSE_API_KEY = os.getenv("COMPANIES_HOUSE_API_KEY", "")
COMPANIES_HOUSE_API_URL = os.getenv("COMPANIES_HOUSE_API_URL", "https://api.company-information.service.gov.uk")
IP_GEOLOCATION_API_KEY = os.getenv("IP_GEOLOCATION_API_KEY", "")
IP_GEOLOCATION_API_URL = os.getenv("IP_GEOLOCATION_API_URL", "https://ipapi.co")


# ══════════════════════════════════════════════════════════════
# AWS S3
# ══════════════════════════════════════════════════════════════

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "af-south-1")
S3_BUCKET = os.getenv("S3_BUCKET", "arie-documents")


# ══════════════════════════════════════════════════════════════
# Redis
# ══════════════════════════════════════════════════════════════

REDIS_URL = os.getenv("REDIS_URL")


# ══════════════════════════════════════════════════════════════
# Server
# ══════════════════════════════════════════════════════════════

PORT = int(os.getenv("PORT", "10000"))
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "http://localhost:10000")
DEBUG = os.getenv("DEBUG", "0") == "1"
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "uploads", "documents"))
LOG_FORMAT = os.getenv("LOG_FORMAT", "text")


# RDI-107: explicit trusted reverse-proxy CIDR allowlist. When set (comma-
# separated CIDRs, e.g. "10.0.0.0/16,172.31.0.0/16"), only a direct peer whose
# address falls inside one of these networks is trusted to set the client IP
# via X-Forwarded-For / X-Real-IP. When UNSET, the legacy permissive behaviour
# (trust any RFC1918-private or loopback peer) applies, so existing ALB/ECS
# deployments keep working until an operator configures the exact proxy CIDRs.
def _parse_trusted_proxy_cidrs(raw):
    import ipaddress

    nets = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid TRUSTED_PROXY_CIDRS entry: %r", part)
    return nets


TRUSTED_PROXY_CIDRS = _parse_trusted_proxy_cidrs(os.getenv("TRUSTED_PROXY_CIDRS", ""))

ADMIN_INITIAL_PASSWORD = os.getenv("ADMIN_INITIAL_PASSWORD", "")


# ══════════════════════════════════════════════════════════════
# Optional / Monitoring
# ══════════════════════════════════════════════════════════════

SENTRY_DSN = os.getenv("SENTRY_DSN")


# ══════════════════════════════════════════════════════════════
# Startup Validation
# ══════════════════════════════════════════════════════════════

def validate_config():
    """
    Validate required configuration at startup.
    Fails hard in staging/production. Logs warnings otherwise.
    Returns True if no errors.
    """
    errors = []
    warnings = []

    if IS_STAGING or IS_PRODUCTION:
        if not JWT_SECRET:
            errors.append("JWT_SECRET or SECRET_KEY is required")
        if not ANTHROPIC_API_KEY:
            errors.append("ANTHROPIC_API_KEY is required")
        if not DATABASE_URL:
            # Audit H8 / PR-13: hard error, not a warning. Without DATABASE_URL
            # the app boots on SQLite inside the container — ephemeral storage
            # for a regulated AML platform. db.py's C-07 guard only fires at
            # request time and only for production; the deploy must fail here.
            errors.append(
                "DATABASE_URL is required in staging and production — "
                "SQLite fallback would store regulated data on ephemeral container disk"
            )
        if not SUMSUB_APP_TOKEN or not SUMSUB_SECRET_KEY:
            warnings.append("SUMSUB credentials not set — KYC verification will be unavailable")
        if not PII_ENCRYPTION_KEY:
            errors.append("PII_ENCRYPTION_KEY is required in staging and production")

    if IS_PRODUCTION:
        if not S3_BUCKET or S3_BUCKET == "arie-documents":
            errors.append("S3_BUCKET must be explicitly configured in production")
        if not os.environ.get("SMTP_HOST") or not os.environ.get("SMTP_USER"):
            warnings.append("SMTP_HOST/SMTP_USER not set — forgot-password emails will not be delivered")
        if not REDIS_URL:
            warnings.append("REDIS_URL not set — using in-memory session store (not scalable)")
        if CLAUDE_MOCK_MODE:
            errors.append("CLAUDE_MOCK_MODE=true is not allowed in production")

    for w in warnings:
        logger.warning(f"CONFIG WARNING: {w}")

    if errors:
        for e in errors:
            logger.error(f"CONFIG ERROR: {e}")
        if IS_STAGING or IS_PRODUCTION:
            raise ConfigError(f"Missing required configuration: {'; '.join(errors)}")

    logger.info(f"Configuration validated for environment: {ENVIRONMENT}")
    return len(errors) == 0
