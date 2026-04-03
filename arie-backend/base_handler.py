"""
ARIE Finance — BaseHandler
===========================
Sprint 3.5: Extracted from server.py to reduce concentration risk.
Central Tornado request handler base class used by all API endpoints.

Provides: CORS, security headers, CSRF protection, dual auth (Bearer + cookie),
rate limiting, JSON helpers, audit logging, and safe error responses.
"""

import hmac
import json
import logging
import os
import secrets

import tornado.web

from auth import decode_token, RateLimiter
from config import ALLOWED_ORIGIN, IS_DEVELOPMENT, IS_DEMO, ENVIRONMENT as _CFG_ENVIRONMENT
from db import get_db as db_get_db

logger = logging.getLogger("arie")

ENVIRONMENT = _CFG_ENVIRONMENT

# Module-level rate limiter instance — shared across all handlers
rate_limiter = RateLimiter()


def get_db():
    """Get a database connection — delegates to db module."""
    return db_get_db()


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
        # CORS — restricted in staging/production, permissive in dev/demo
        if IS_DEVELOPMENT or IS_DEMO:
            self.set_header("Access-Control-Allow-Origin", "*")
        elif ALLOWED_ORIGIN and ALLOWED_ORIGIN != "http://localhost:8080":
            self.set_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        else:
            # In staging/production with no explicit origin, same-origin only (most secure)
            logger.warning("ALLOWED_ORIGIN not configured for %s — defaulting to same-origin only", ENVIRONMENT)
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
        #
        # 'unsafe-eval' REMOVED (prior batch) — closes XSS amplification vector.
        #
        # 'unsafe-inline' REMAINS in script-src and style-src — INTENTIONALLY DEFERRED.
        # Reason: the frontend is single-file HTML with:
        #   - 219/173 inline event handlers (onclick, onchange, etc.) across portal/backoffice
        #   - 519/847 inline style= attributes
        #   - 1-2 large <script> blocks per file
        # Removing unsafe-inline from script-src would require converting ALL inline
        # event handlers to addEventListener — a full frontend rewrite (out of scope).
        # Removing it from style-src is not possible for inline style= attributes
        # (CSP nonce/hash only covers <style> blocks, not attributes).
        #
        # Mitigations applied:
        #   - object-src 'none' blocks plugin-based attacks (Flash/Java)
        #   - base-uri 'self' blocks <base> tag hijacking
        #   - frame-ancestors 'none' blocks clickjacking
        #   - unsafe-eval removed blocks eval()/Function() XSS escalation
        #   - connect-src 'self' blocks exfiltration to third-party endpoints
        #
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
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

    def issue_session_cookie(self, jwt_token):
        """
        Sprint 3.5: Set JWT as httpOnly cookie for browser sessions.
        Cookie is: httpOnly (no JS access), Secure (HTTPS-only in prod),
        SameSite=Lax (allows top-level navigations), 24h expiry.
        """
        self.set_cookie(
            "arie_session", jwt_token,
            httponly=True,
            secure=(ENVIRONMENT == "production"),
            samesite="Lax",
            path="/",
            expires_days=1,  # Match JWT 24h expiry
        )

    def clear_session_cookie(self):
        """Sprint 3.5: Clear session cookie on logout."""
        self.clear_cookie("arie_session", path="/")
        self.clear_cookie("csrf_token", path="/")

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
            "/api/auth/logout",
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
        """
        Sprint 3.5: Dual auth — check Bearer header first, then httpOnly cookie.
        Bearer tokens take precedence (API clients). Cookie auth is for browser sessions.
        """
        # 1. Bearer token (API clients, mobile apps)
        auth = self.request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return decode_token(auth[7:])
        # 2. httpOnly cookie (browser sessions — Sprint 3.5)
        session_token = self.get_cookie("arie_session", None)
        if session_token:
            return decode_token(session_token)
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
        """Cross-cutting: Safe error responses — no stack traces or DB internals in any environment."""
        import traceback
        error_msg = self._reason or "Internal server error"
        if "exc_info" in kwargs:
            exc_type, exc_value, _ = kwargs["exc_info"]
            # Log full detail server-side for debugging
            logger.error("Unhandled exception in %s: %s", self.__class__.__name__,
                         traceback.format_exception(*kwargs["exc_info"])[-1].strip())
            # Never expose raw DB errors, constraint names, or row data to the client
            if ENVIRONMENT not in ("production", "demo", "staging"):
                # Local dev only: include exception type (not message, which may contain row data)
                self.write({"error": error_msg, "status": status_code,
                            "debug_type": f"{exc_type.__module__}.{exc_type.__name__}" if exc_type else ""})
                return
        self.write({"error": error_msg, "status": status_code})
