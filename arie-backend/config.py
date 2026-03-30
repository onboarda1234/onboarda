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

ENVIRONMENT = os.getenv("ENVIRONMENT", os.getenv("ENV", "development"))
IS_DEMO = ENVIRONMENT == "demo"
IS_STAGING = ENVIRONMENT == "staging"
IS_PRODUCTION = ENVIRONMENT == "production"
IS_DEVELOPMENT = ENVIRONMENT == "development"


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
SUMSUB_WEBHOOK_SECRET = os.getenv("SUMSUB_WEBHOOK_SECRET", "")


# ══════════════════════════════════════════════════════════════
# External APIs
# ══════════════════════════════════════════════════════════════

OPENSANCTIONS_API_KEY = os.getenv("OPENSANCTIONS_API_KEY", "")
OPENSANCTIONS_API_URL = os.getenv("OPENSANCTIONS_API_URL", "https://api.opensanctions.org")
OPENCORPORATES_API_KEY = os.getenv("OPENCORPORATES_API_KEY", "")
OPENCORPORATES_API_URL = os.getenv("OPENCORPORATES_API_URL", "https://api.opencorporates.com/v0.4")
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

PORT = int(os.getenv("PORT", "8080"))
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "http://localhost:8080")
DEBUG = os.getenv("DEBUG", "0") == "1"
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "uploads", "documents"))
LOG_FORMAT = os.getenv("LOG_FORMAT", "text")

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
            warnings.append("DATABASE_URL not set — using SQLite (not recommended for production)")
        if not SUMSUB_APP_TOKEN or not SUMSUB_SECRET_KEY:
            warnings.append("SUMSUB credentials not set — KYC verification will be unavailable")

    if IS_PRODUCTION:
        if not PII_ENCRYPTION_KEY:
            errors.append("PII_ENCRYPTION_KEY is required in production")
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
