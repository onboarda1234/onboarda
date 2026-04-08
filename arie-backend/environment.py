"""
Onboarda Platform — Environment Configuration & Feature Flags
=========================================================
Single codebase, strict separation between Demo, Staging, and Production.
"""

import os
import sys
import logging

logger = logging.getLogger("arie.environment")

# ══════════════════════════════════════════════════════════════
# 1. ENVIRONMENT DETECTION
# ══════════════════════════════════════════════════════════════

VALID_ENVIRONMENTS = ("development", "testing", "demo", "staging", "production")

def get_environment() -> str:
    """Get current environment from ENV variable. Defaults to 'demo'."""
    env = (os.environ.get("ENVIRONMENT") or os.environ.get("ENV") or "demo").lower().strip()
    if env not in VALID_ENVIRONMENTS:
        logger.warning(f"Invalid ENV='{env}' — defaulting to 'demo'")
        env = "demo"
    return env

ENV = get_environment()

def is_development() -> bool:
    return ENV == "development"


def is_testing() -> bool:
    """Returns True when running automated tests with ENVIRONMENT=testing."""
    return ENV == "testing"

def is_demo() -> bool:
    return ENV == "demo"

def is_staging() -> bool:
    return ENV == "staging"

def is_production() -> bool:
    return ENV == "production"


# ══════════════════════════════════════════════════════════════
# 2. FEATURE FLAGS
# ══════════════════════════════════════════════════════════════

# Default flags per environment
_DEFAULT_FLAGS = {
    "development": {
        "ENABLE_DEMO_MODE": False,
        "ENABLE_DEMO_BANNER": False,
        "ENABLE_DEMO_DATA_SEEDING": False,
        "ENABLE_MOCK_FALLBACKS": False,
        "ENABLE_ROLE_SWITCHER": True,
        "ENABLE_PHASE2_FEATURES": True,
        "ENABLE_REGULATORY_INTELLIGENCE_FULL": True,
        "ENABLE_MONITORING_DASHBOARD": True,
        "ENABLE_SAR_WORKFLOW": True,
        "ENABLE_AI_SUPERVISOR": True,
        "ENABLE_KPI_DEMO_DATA": False,
        "ENABLE_DOCUMENT_AI_ANALYSIS": True,
        "ENABLE_SUMSUB_LIVE": False,
        "ENABLE_SUMSUB_SANDBOX": True,
        "ENABLE_REAL_SCREENING": False,
        "ENABLE_SIMULATED_SCREENING": True,
        "REQUIRE_REAL_API_KEYS": False,
        "ENABLE_DEBUG_ENDPOINTS": True,
        "ENABLE_SHORTCUT_LOGIN": True,
    },
    "demo": {
        "ENABLE_DEMO_MODE": True,
        "ENABLE_DEMO_BANNER": True,
        "ENABLE_DEMO_DATA_SEEDING": True,
        "ENABLE_MOCK_FALLBACKS": True,
        "ENABLE_ROLE_SWITCHER": True,
        "ENABLE_PHASE2_FEATURES": True,
        "ENABLE_REGULATORY_INTELLIGENCE_FULL": True,
        "ENABLE_MONITORING_DASHBOARD": True,
        "ENABLE_SAR_WORKFLOW": True,
        "ENABLE_AI_SUPERVISOR": True,
        "ENABLE_KPI_DEMO_DATA": True,
        "ENABLE_DOCUMENT_AI_ANALYSIS": True,
        "ENABLE_SUMSUB_LIVE": False,  # Use sandbox in demo
        "ENABLE_SUMSUB_SANDBOX": True,
        "ENABLE_REAL_SCREENING": False,
        "ENABLE_SIMULATED_SCREENING": True,
        "REQUIRE_REAL_API_KEYS": False,
        "ENABLE_DEBUG_ENDPOINTS": True,
        "ENABLE_SHORTCUT_LOGIN": True,
    },
    "staging": {
        "ENABLE_DEMO_MODE": False,
        "ENABLE_DEMO_BANNER": False,
        "ENABLE_DEMO_DATA_SEEDING": False,
        "ENABLE_MOCK_FALLBACKS": False,
        "ENABLE_ROLE_SWITCHER": False,
        "ENABLE_PHASE2_FEATURES": True,
        "ENABLE_REGULATORY_INTELLIGENCE_FULL": True,
        "ENABLE_MONITORING_DASHBOARD": True,
        "ENABLE_SAR_WORKFLOW": True,
        "ENABLE_AI_SUPERVISOR": True,
        "ENABLE_KPI_DEMO_DATA": False,
        "ENABLE_DOCUMENT_AI_ANALYSIS": True,
        "ENABLE_SUMSUB_LIVE": True,
        "ENABLE_SUMSUB_SANDBOX": False,
        "ENABLE_REAL_SCREENING": True,
        "ENABLE_SIMULATED_SCREENING": False,
        "REQUIRE_REAL_API_KEYS": True,
        "ENABLE_DEBUG_ENDPOINTS": False,
        "ENABLE_SHORTCUT_LOGIN": False,
    },
    "production": {
        "ENABLE_DEMO_MODE": False,
        "ENABLE_DEMO_BANNER": False,
        "ENABLE_DEMO_DATA_SEEDING": False,
        "ENABLE_MOCK_FALLBACKS": False,
        "ENABLE_ROLE_SWITCHER": False,
        "ENABLE_PHASE2_FEATURES": True,
        "ENABLE_REGULATORY_INTELLIGENCE_FULL": True,
        "ENABLE_MONITORING_DASHBOARD": True,
        "ENABLE_SAR_WORKFLOW": True,
        "ENABLE_AI_SUPERVISOR": True,
        "ENABLE_KPI_DEMO_DATA": False,
        "ENABLE_DOCUMENT_AI_ANALYSIS": True,
        "ENABLE_SUMSUB_LIVE": True,
        "ENABLE_SUMSUB_SANDBOX": False,
        "ENABLE_REAL_SCREENING": True,
        "ENABLE_SIMULATED_SCREENING": False,
        "REQUIRE_REAL_API_KEYS": True,
        "ENABLE_DEBUG_ENDPOINTS": False,
        "ENABLE_SHORTCUT_LOGIN": False,
    },
}

_DEFAULT_FLAGS["testing"] = dict(_DEFAULT_FLAGS["development"])


class FeatureFlags:
    """
    Feature flag manager with environment defaults and env var overrides.

    Resolution order:
    1. Environment variable (e.g. ENABLE_DEMO_MODE=true)
    2. Default for current environment
    """

    def __init__(self, env: str = None):
        self._env = env or ENV
        self._defaults = _DEFAULT_FLAGS.get(self._env, _DEFAULT_FLAGS["demo"])
        self._cache = {}
        self._load_all()

    def _load_all(self):
        """Load all flags with env var overrides."""
        for flag, default in self._defaults.items():
            env_val = os.environ.get(flag)
            if env_val is not None:
                self._cache[flag] = env_val.lower() in ("true", "1", "yes", "on")
            else:
                self._cache[flag] = default

    def is_enabled(self, flag: str) -> bool:
        """Check if a feature flag is enabled."""
        if flag in self._cache:
            return self._cache[flag]
        # Check env var for unknown flags
        env_val = os.environ.get(flag)
        if env_val is not None:
            return env_val.lower() in ("true", "1", "yes", "on")
        return False

    def get_all(self) -> dict:
        """Return all flags as a dictionary (safe for API response)."""
        return dict(self._cache)

    def get_client_safe_flags(self) -> dict:
        """Return flags safe to expose to frontend."""
        safe_keys = [
            "ENABLE_DEMO_MODE", "ENABLE_DEMO_BANNER", "ENABLE_PHASE2_FEATURES",
            "ENABLE_REGULATORY_INTELLIGENCE_FULL", "ENABLE_MONITORING_DASHBOARD",
            "ENABLE_SAR_WORKFLOW", "ENABLE_AI_SUPERVISOR", "ENABLE_KPI_DEMO_DATA",
            "ENABLE_ROLE_SWITCHER", "ENABLE_DOCUMENT_AI_ANALYSIS",
        ]
        return {k: self._cache.get(k, False) for k in safe_keys}


# Singleton
flags = FeatureFlags()


# ══════════════════════════════════════════════════════════════
# 3. SAFETY GUARDS — CRITICAL
# ══════════════════════════════════════════════════════════════

# These flags must NEVER be enabled in production
_PRODUCTION_FORBIDDEN_FLAGS = [
    "ENABLE_DEMO_MODE",
    "ENABLE_DEMO_BANNER",
    "ENABLE_DEMO_DATA_SEEDING",
    "ENABLE_MOCK_FALLBACKS",
    "ENABLE_ROLE_SWITCHER",
    "ENABLE_SIMULATED_SCREENING",
    "ENABLE_DEBUG_ENDPOINTS",
    "ENABLE_SHORTCUT_LOGIN",
    "ENABLE_KPI_DEMO_DATA",
    "ENABLE_SUMSUB_SANDBOX",
]

# These env vars must be set in production
_PRODUCTION_REQUIRED_VARS = [
    "ANTHROPIC_API_KEY",
    "SUMSUB_APP_TOKEN",
    "SUMSUB_SECRET_KEY",
    "JWT_SECRET",
    "DATABASE_URL",
]


def validate_environment() -> list:
    """
    Validate environment configuration at startup.
    Returns list of errors. Empty list = safe to start.

    CRITICAL: In production, this will BLOCK startup if unsafe.
    """
    errors = []
    warnings = []

    if is_production():
        # Check forbidden flags
        for flag in _PRODUCTION_FORBIDDEN_FLAGS:
            if flags.is_enabled(flag):
                errors.append(
                    f"CRITICAL: Flag '{flag}' is enabled in production. "
                    f"This is FORBIDDEN. Set {flag}=false or remove it."
                )

        # Check required variables
        for var in _PRODUCTION_REQUIRED_VARS:
            if not os.environ.get(var):
                errors.append(
                    f"CRITICAL: Required variable '{var}' is not set in production."
                )

        # Ensure mock fallbacks are impossible
        if os.environ.get("CLAUDE_MOCK_MODE", "").lower() in ("true", "1"):
            errors.append(
                "CRITICAL: CLAUDE_MOCK_MODE=true in production. "
                "AI must use real API calls."
            )

    elif is_staging():
        # Staging should not have demo data
        if flags.is_enabled("ENABLE_DEMO_DATA_SEEDING"):
            warnings.append("WARNING: ENABLE_DEMO_DATA_SEEDING is on in staging.")
        if flags.is_enabled("ENABLE_MOCK_FALLBACKS"):
            warnings.append("WARNING: ENABLE_MOCK_FALLBACKS is on in staging.")

        # Staging should have real API keys
        for var in _PRODUCTION_REQUIRED_VARS:
            if not os.environ.get(var):
                warnings.append(f"WARNING: '{var}' not set in staging.")

    # Log results
    for w in warnings:
        logger.warning(w)
    for e in errors:
        logger.error(e)

    return errors


def enforce_startup_safety():
    """
    Call at application startup. Blocks production if unsafe.
    Logs warnings for staging. Passes silently for demo.
    """
    logger.info(f"═══ Onboarda Platform Environment: {ENV.upper()} ═══")
    logger.info(f"Feature flags loaded: {len(flags.get_all())} flags")

    errors = validate_environment()

    if errors and is_production():
        logger.critical("═══ STARTUP BLOCKED — UNSAFE PRODUCTION CONFIGURATION ═══")
        for e in errors:
            logger.critical(e)
        logger.critical("Fix the configuration above before starting in production.")
        sys.exit(1)
    elif errors and is_staging():
        logger.warning(f"Staging has {len(errors)} configuration issues (non-blocking)")

    # Log active flags summary
    enabled = [k for k, v in flags.get_all().items() if v]
    disabled = [k for k, v in flags.get_all().items() if not v]
    logger.info(f"Enabled flags ({len(enabled)}): {', '.join(enabled)}")
    logger.info(f"Disabled flags ({len(disabled)}): {', '.join(disabled)}")


# ══════════════════════════════════════════════════════════════
# 4. ENVIRONMENT-SPECIFIC CONFIGURATION
# ══════════════════════════════════════════════════════════════

def get_database_url() -> str:
    """Get database URL for current environment."""
    if is_production():
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL required in production")
        return url
    elif is_staging():
        return os.environ.get("STAGING_DATABASE_URL", os.environ.get("DATABASE_URL", "sqlite:///arie_staging.db"))
    elif is_testing():
        return os.environ.get("TEST_DATABASE_URL", os.environ.get("DATABASE_URL", "sqlite:///arie_test.db"))
    else:
        return os.environ.get("DEMO_DATABASE_URL", os.environ.get("DATABASE_URL", "sqlite:///arie_demo.db"))


def get_s3_bucket() -> str:
    """Get S3 bucket name for current environment."""
    if is_production():
        return os.environ.get("S3_BUCKET", "arie-production-documents")
    elif is_staging():
        return os.environ.get("S3_BUCKET_STAGING", "arie-staging-documents")
    elif is_testing():
        return os.environ.get("S3_BUCKET_TESTING", "arie-testing-documents")
    else:
        return os.environ.get("S3_BUCKET_DEMO", "arie-demo-documents")


def get_sumsub_base_url() -> str:
    """Get Sumsub API URL — sandbox for demo, live for production."""
    if flags.is_enabled("ENABLE_SUMSUB_SANDBOX"):
        return "https://api.sumsub.com"  # Sumsub sandbox uses same URL with test credentials
    return os.environ.get("SUMSUB_BASE_URL", "https://api.sumsub.com")


def get_jwt_secret() -> str:
    """Get JWT secret — must be unique per environment."""
    import logging as _logging
    _jwt_logger = _logging.getLogger("arie.security")

    if is_production():
        secret = os.environ.get("JWT_SECRET")
        if not secret or len(secret) < 32:
            raise RuntimeError("JWT_SECRET must be set and >= 32 chars in production")
        return secret
    elif is_staging():
        secret = os.environ.get("JWT_SECRET_STAGING", os.environ.get("JWT_SECRET", ""))
        if not secret:
            _jwt_logger.warning("JWT_SECRET not set for staging — using generated fallback. Set JWT_SECRET env var.")
            secret = "staging-fallback-" + os.urandom(16).hex()
        return secret
    elif is_testing():
        secret = os.environ.get("JWT_SECRET_TESTING", os.environ.get("JWT_SECRET", ""))
        if not secret:
            _jwt_logger.warning("JWT_SECRET not set for testing — using generated fallback. Set JWT_SECRET env var.")
            secret = "testing-fallback-" + os.urandom(16).hex()
        return secret
    else:
        secret = os.environ.get("JWT_SECRET_DEMO", os.environ.get("JWT_SECRET", ""))
        if not secret:
            _jwt_logger.warning("JWT_SECRET not set for demo — using generated fallback. Set JWT_SECRET env var.")
            secret = "demo-fallback-" + os.urandom(16).hex()
        return secret


# ── API Credentials (Sprint 2.5: single access point) ──

def get_sumsub_app_token() -> str:
    return os.environ.get("SUMSUB_APP_TOKEN", "")

def get_sumsub_secret_key() -> str:
    return os.environ.get("SUMSUB_SECRET_KEY", "")

def get_sumsub_level_name() -> str:
    return os.environ.get("SUMSUB_LEVEL_NAME", "basic-kyc-level")

def get_opencorporates_api_key() -> str:
    return os.environ.get("OPENCORPORATES_API_KEY", "")

def get_ip_geolocation_api_key() -> str:
    return os.environ.get("IP_GEOLOCATION_API_KEY", "")

def get_opensanctions_api_key() -> str:
    return os.environ.get("OPENSANCTIONS_API_KEY", "")


def get_cors_origin() -> str:
    """Get allowed CORS origin for current environment."""
    if is_production():
        return os.environ.get("CORS_ORIGIN", "https://app.ariefinance.mu")
    elif is_staging():
        return os.environ.get("CORS_ORIGIN_STAGING", "https://staging.ariefinance.mu")
    elif is_testing():
        return os.environ.get("CORS_ORIGIN_TESTING", "http://localhost:3000")
    else:
        return os.environ.get("CORS_ORIGIN_DEMO", "https://demo.ariefinance.mu")


# ══════════════════════════════════════════════════════════════
# 5. ENVIRONMENT INFO API (for frontend)
# ══════════════════════════════════════════════════════════════

def get_environment_info() -> dict:
    """
    Return environment info safe to send to frontend.
    Used by /api/config/environment endpoint.
    """
    info = {
        "environment": ENV,
        "is_demo": is_demo(),
        "is_production": is_production(),
        "features": flags.get_client_safe_flags(),
        "version": os.environ.get("APP_VERSION", "1.0.0-pilot"),
    }
    # Demo credentials — only exposed in demo environments, read from env vars
    if is_demo():
        info["demo_credentials"] = {
            "portal_password": os.environ.get("DEMO_PORTAL_PASSWORD", ""),
            "backoffice_password": os.environ.get("DEMO_BACKOFFICE_PASSWORD", ""),
        }
    return info
