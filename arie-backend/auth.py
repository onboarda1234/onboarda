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

from environment import get_jwt_secret

logger = logging.getLogger("arie")

# ── Configuration ──
# Unified secret: reads JWT_SECRET via centralized environment.py (not raw os.environ)
SECRET_KEY = get_jwt_secret()
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
    """
    Sliding window rate limiter with DB persistence for auth-critical keys.

    General API rate limits use fast in-memory tracking. Auth-critical keys
    (login endpoints) are additionally persisted to the database so that
    brute-force counters survive server restarts.
    """

    # Keys containing these substrings get DB persistence
    _PERSIST_PATTERNS = ("login", "register", "auth")

    def __init__(self):
        self._attempts = {}  # key → list of timestamps (in-memory hot path)

    def _should_persist(self, key):
        """Returns True if this key should be persisted to DB."""
        key_lower = key.lower()
        return any(p in key_lower for p in self._PERSIST_PATTERNS)

    def _db_load(self, key, cutoff):
        """Load persisted attempts from DB for a key."""
        try:
            from db import get_db as _db_get
            db = _db_get()
            rows = db.execute(
                "SELECT attempted_at FROM rate_limits WHERE key = ? AND attempted_at > ?",
                (key, cutoff)
            ).fetchall()
            db.close()
            return [r[0] if isinstance(r, (tuple, list)) else r["attempted_at"] for r in rows]
        except Exception:
            # Table may not exist yet or DB unavailable — fall back to in-memory
            return []

    def _db_record(self, key, ts):
        """Persist an attempt timestamp to DB."""
        try:
            from db import get_db as _db_get
            db = _db_get()
            db.execute(
                "INSERT INTO rate_limits (key, attempted_at) VALUES (?, ?)",
                (key, ts)
            )
            db.commit()
            db.close()
        except Exception:
            pass  # Best-effort — in-memory still protects us

    def _db_cleanup(self, key, cutoff):
        """Remove expired entries from DB."""
        try:
            from db import get_db as _db_get
            db = _db_get()
            db.execute("DELETE FROM rate_limits WHERE key = ? AND attempted_at <= ?", (key, cutoff))
            db.commit()
            db.close()
        except Exception:
            pass

    def _db_delete(self, key):
        """Remove all entries for a key from DB."""
        try:
            from db import get_db as _db_get
            db = _db_get()
            db.execute("DELETE FROM rate_limits WHERE key = ?", (key,))
            db.commit()
            db.close()
        except Exception:
            pass

    def is_limited(self, key, max_attempts=10, window_seconds=900):
        """Returns True if the key has exceeded max_attempts in the window."""
        now = time.time()
        cutoff = now - window_seconds
        persist = self._should_persist(key)

        if key not in self._attempts:
            self._attempts[key] = []

        # Prune old in-memory entries
        self._attempts[key] = [t for t in self._attempts[key] if t > cutoff]

        # For auth-critical keys, merge DB-persisted attempts
        if persist:
            db_times = self._db_load(key, cutoff)
            # Merge: use union of in-memory and DB timestamps (dedup)
            mem_set = set(self._attempts[key])
            for t in db_times:
                if t not in mem_set:
                    self._attempts[key].append(t)
                    mem_set.add(t)
            self._db_cleanup(key, cutoff)

        if len(self._attempts[key]) >= max_attempts:
            return True

        self._attempts[key].append(now)
        if persist:
            self._db_record(key, now)
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
        if self._should_persist(key):
            self._db_delete(key)
