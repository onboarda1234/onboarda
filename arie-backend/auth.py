"""
ARIE Finance — Authentication, Token Management, and Rate Limiting
Extracted from server.py during Sprint 2 monolith decomposition.

Provides:
    - create_token() / decode_token() — JWT creation and validation
    - sanitize_input() / sanitize_dict() — XSS/HTML injection prevention
    - RateLimiter — In-memory sliding window rate limiter
"""
import os
import uuid
import time
import html
import logging
from datetime import datetime, timedelta

import jwt

logger = logging.getLogger("arie")

# ── Configuration ──
SECRET_KEY = os.environ.get("SECRET_KEY", "CHANGE_ME_IN_PRODUCTION")
TOKEN_EXPIRY_HOURS = 24

# ── Token revocation (lazy import to avoid circular deps) ──
_revocation_list = None


def _get_revocation_list():
    """Lazily import token_revocation_list to avoid circular imports."""
    global _revocation_list
    if _revocation_list is None:
        try:
            from security_hardening import token_revocation_list
            _revocation_list = token_revocation_list
        except ImportError:
            # Fallback: no revocation checking
            class _NoopRevocationList:
                def is_revoked(self, jti):
                    return False
            _revocation_list = _NoopRevocationList()
    return _revocation_list


# ══════════════════════════════════════════════════════════
# TOKEN MANAGEMENT
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
        if _get_revocation_list().is_revoked(decoded.get("jti", "")):
            logger.debug("Token revoked")
            return None
        return decoded
    except jwt.ExpiredSignatureError:
        logger.debug("Token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug("Invalid token: %s", e)
        return None


# ══════════════════════════════════════════════════════════
# INPUT SANITIZATION
# ══════════════════════════════════════════════════════════

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
