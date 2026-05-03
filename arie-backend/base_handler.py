"""
Onboarda — BaseHandler
===========================
Sprint 3.5: Extracted from server.py to reduce concentration risk.
Central Tornado request handler base class used by all API endpoints.

Provides: CORS, security headers, CSRF protection, dual auth (Bearer + cookie),
rate limiting, JSON helpers, audit logging, and safe error responses.
"""

import datetime
import hmac
import ipaddress
import json
import logging
import os
import secrets
import sys
import time

import tornado.web

from auth import decode_token, RateLimiter
from config import ALLOWED_ORIGIN, IS_DEVELOPMENT, IS_DEMO, ENVIRONMENT as _CFG_ENVIRONMENT
from db import get_db as db_get_db

logger = logging.getLogger("arie")

ENVIRONMENT = _CFG_ENVIRONMENT

# Module-level rate limiter instance — shared across all handlers
rate_limiter = RateLimiter()


def _upload_latency_route_context(method, path):
    """Return telemetry context for upload-latency endpoints only."""
    if method != "POST":
        return None

    parts = (path or "").strip("/").split("/")
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "applications" and parts[3] == "documents":
        return {
            "operation": "document_upload",
            "path_template": "/api/applications/{application_id}/documents",
            "application_id": parts[2],
        }
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "documents" and parts[3] == "verify":
        return {
            "operation": "document_verify",
            "path_template": "/api/documents/{document_id}/verify",
            "document_id": parts[2],
        }
    return None


def _parse_content_length(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed >= 0 else 0


def _format_upload_latency_log_line(context, status, duration_ms, request_bytes, environment):
    parts = [
        "upload_latency_telemetry",
        "event=upload_latency_request",
        f"operation={context['operation']}",
        f"path_template={context['path_template']}",
        f"status={status}",
        f"duration_ms={duration_ms:.2f}",
        f"request_bytes={request_bytes}",
        f"environment={environment}",
    ]
    if context.get("application_id"):
        parts.append(f"application_id={context['application_id']}")
    if context.get("document_id"):
        parts.append(f"document_id={context['document_id']}")
    return " ".join(parts)


def get_db():
    """Get a database connection — delegates to db module."""
    return db_get_db()


def _safe_json(obj):
    """Serialize obj to JSON string safely. Returns None if obj is None or not serializable."""
    if obj is None:
        return None
    try:
        return json.dumps(obj, default=str, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        return None


def snapshot_app_state(app):
    """Extract a non-PII workflow-focused snapshot from an application row.

    Returns a dict with only status/risk/decision fields — no personal data.
    Safe for audit_log before_state / after_state columns.
    Works with both dict and sqlite3.Row objects.
    """
    if app is None:
        return None
    fields = ("status", "risk_level", "risk_score", "pre_approval_decision",
              "decided_at", "decision_by", "onboarding_lane",
              "first_approver_id", "first_approved_at",
              "risk_computed_at", "risk_config_version")
    result = {}
    for f in fields:
        try:
            val = app.get(f) if hasattr(app, "get") else app[f]
        except (IndexError, KeyError):
            continue
        if val is not None:
            result[f] = val
    return result


class BaseHandler(tornado.web.RequestHandler):
    def prepare(self):
        """Enforce HTTPS in production via X-Forwarded-Proto from reverse proxy."""
        self._upload_latency_started_at = time.monotonic()
        if ENVIRONMENT == "production":
            forwarded_proto = self.request.headers.get("X-Forwarded-Proto", "")
            if forwarded_proto == "http":
                # Redirect HTTP to HTTPS
                url = self.request.full_url().replace("http://", "https://", 1)
                self.redirect(url, permanent=True)
                return
        self.check_xsrf_cookie()

    def on_finish(self):
        """Emit parse-ready timing logs for upload-latency endpoints only."""
        context = _upload_latency_route_context(self.request.method, self.request.path)
        if not context:
            return

        started_at = getattr(self, "_upload_latency_started_at", None)
        if started_at is None:
            return

        duration_ms = (time.monotonic() - started_at) * 1000
        request_bytes = _parse_content_length(self.request.headers.get("Content-Length"))
        logger.info(
            _format_upload_latency_log_line(
                context=context,
                status=self.get_status(),
                duration_ms=duration_ms,
                request_bytes=request_bytes,
                environment=ENVIRONMENT,
            )
        )

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
        # CORS — explicit origin takes precedence in all environments.
        if ALLOWED_ORIGIN and ALLOWED_ORIGIN != "http://localhost:10000":
            self.set_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        elif IS_DEVELOPMENT or IS_DEMO:
            self.set_header("Access-Control-Allow-Origin", "*")
        else:
            # In staging/production with no explicit origin, same-origin only (most secure)
            logger.warning("ALLOWED_ORIGIN not configured for %s — defaulting to same-origin only", ENVIRONMENT)
        self.set_header("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type,Authorization,X-CSRF-Token,X-Idempotency-Key")
        self.set_header("Access-Control-Max-Age", "3600")
        self.set_header("Content-Type", "application/json")
        self.set_header("Server", "RegMind")
        # Security headers — always on
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("X-Frame-Options", "DENY")
        self.set_header("X-XSS-Protection", "1; mode=block")
        self.set_header("Referrer-Policy", "strict-origin-when-cross-origin")
        # HSTS — enforce HTTPS-only browser behaviour on deployed surfaces.
        self.set_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
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
            samesite="Lax",
            path="/",
            expires_days=1,  # Match JWT 24h expiry
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
        # CSRF only applies to browser session-cookie auth. Requests with no
        # session cookie should reach normal authentication and return 401.
        if not self.get_cookie("arie_session", None):
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
        remote_ip = self.request.remote_ip or ""
        x_real_ip = (self.request.headers.get("X-Real-IP") or "").strip()
        x_forwarded_for = (self.request.headers.get("X-Forwarded-For") or "").strip()

        def _is_valid_ip(value):
            try:
                ipaddress.ip_address(value)
                return True
            except ValueError:
                return False

        def _is_trusted_proxy(value):
            try:
                ip = ipaddress.ip_address(value)
            except ValueError:
                return False
            # ALB/ECS hops are private. In tests, loopback is the local proxy.
            return ip.is_private or ip.is_loopback

        # AWS ALB writes the original client as the leftmost X-Forwarded-For
        # value. Trust it only when the direct peer is a private/loopback proxy
        # so direct public callers cannot spoof audit provenance.
        if x_forwarded_for and _is_trusted_proxy(remote_ip):
            for part in x_forwarded_for.split(","):
                candidate = part.strip()
                if _is_valid_ip(candidate):
                    return candidate

        if x_real_ip and _is_valid_ip(x_real_ip):
            return x_real_ip
        return remote_ip

    def success(self, data, status=200):
        self.set_status(status)
        self.write(json.dumps(data, default=str))

    def error(self, message, status=400):
        self.set_status(status)
        self.write({"error": message})

    def log_audit(self, user, action, target, detail, db=None,
                  before_state=None, after_state=None, commit=True):
        """Write a standard audit row.

        When a caller supplies db and commit=False, the caller must commit
        before closing the connection. Callers that need autonomous persistence
        should use the default commit=True.
        """
        own_db = db is None
        if own_db:
            db = get_db()
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address, before_state, after_state) VALUES (?,?,?,?,?,?,?,?,?)",
            (user.get("sub",""), user.get("name",""), user.get("role",""), action, target, detail, self.get_client_ip(),
             _safe_json(before_state), _safe_json(after_state))
        )
        if own_db or commit:
            db.commit()
        if own_db:
            db.close()

    def _governance_audit_target(self, target):
        text = str(target or "unknown")
        text = "".join(ch if ch.isprintable() and ch not in "\r\n\t" else " " for ch in text)
        text = " ".join(text.split())
        return (text or "unknown")[:160]

    def log_governance_attempt(self, user, action, target, outcome, status_code,
                               reason="", payload_summary=None, db=None, commit=True):
        """Persist an audit row for success or rejection of governed actions.

        This is intentionally separate from the business-event audit rows
        ("Decision", "Screening Review", etc.) so failed attempts are visible
        even when the guarded action is rejected before any state change.

        The write is best-effort: failures are logged with a structured marker
        and never replace the original user-visible handler response. Rejected
        attempts should generally use the default commit=True; accepted attempts
        that share a caller transaction can pass commit=False. Do not use
        commit=False unless the caller will commit the supplied db before close.
        """
        target = self._governance_audit_target(target)
        summary = payload_summary if isinstance(payload_summary, dict) else {}
        reason_text = str(reason or "")
        reason_truncated = len(reason_text) > 512
        if reason_truncated:
            reason_text = reason_text[:512]
        path = self.request.path if hasattr(self, "request") else ""
        method = self.request.method if hasattr(self, "request") else ""

        detail_obj = {
            "event": "governance_attempt",
            "action": action,
            "outcome": outcome,
            "response_code": status_code,
            "rejection_reason": reason_text,
            "rejection_reason_truncated": reason_truncated,
            "payload_summary": summary,
            "path": str(path)[:512],
            "method": str(method)[:32],
            "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
        detail = json.dumps(detail_obj, default=str)
        if len(detail) > 4096:
            detail_obj["payload_summary"] = {
                "truncated": True,
                "keys": sorted(str(k) for k in summary.keys())[:20],
                "original_detail_bytes": len(detail),
            }
            detail = json.dumps(detail_obj, default=str)

        own_db = db is None
        try:
            if own_db:
                db = get_db()
            db.execute(
                "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    user.get("sub", "") if user else "",
                    user.get("name", "") if user else "",
                    user.get("role", "") if user else "",
                    "Governance Attempt",
                    target,
                    detail,
                    self.get_client_ip() if hasattr(self, "request") else "",
                ),
            )
            if own_db or commit:
                db.commit()
        except Exception:
            logger.exception(
                "governance_audit_write_failed=true action=%s target=%s outcome=%s status_code=%s",
                action, target, outcome, status_code,
            )
        finally:
            if own_db and db is not None:
                db.close()

    def log_authz_denial(self, user, event, resource_id, context_dict, db=None):
        """Write a uniform audit row for any AuthZ denial.

        Uses an autonomous DB connection so the row survives even if the
        caller's transaction rolls back.  Falls back to structured stderr
        (tagged ``AUDIT_FALLBACK``) if the primary write fails.

        Args:
            user: Authenticated user dict.
            event: Action name, e.g. ``authz_denied_not_owner``.
            resource_id: The resource the caller tried to access.
            context_dict: Extra context merged into the audit detail.
        """
        payload = {
            "event": event,
            "client_id": user.get("sub", ""),
            "attempted_resource_id": resource_id,
            "actual_owner": context_dict.get("actual_owner", ""),
            "path": self.request.path if hasattr(self, "request") else "",
            "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
        # Merge caller-supplied extras (e.g. source_channel) without
        # overriding the canonical keys above.
        for k, v in context_dict.items():
            if k not in payload:
                payload[k] = v
        detail = json.dumps(payload, default=str)

        # --- Primary: autonomous connection ---
        try:
            audit_db = get_db()
            try:
                audit_db.execute(
                    "INSERT INTO audit_log (user_id, user_name, user_role, "
                    "action, target, detail, ip_address) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        user.get("sub", ""),
                        user.get("name", ""),
                        user.get("role", ""),
                        event,
                        resource_id,
                        detail,
                        self.get_client_ip() if hasattr(self, "request") else "",
                    ),
                )
                audit_db.commit()
            finally:
                audit_db.close()
            return  # success
        except Exception:
            logger.exception("Primary audit write failed for event=%s", event)

        # --- Fallback: structured stderr ---
        try:
            fallback = json.dumps({
                "AUDIT_FALLBACK": True,
                **payload,
                "user_name": user.get("name", ""),
                "user_role": user.get("role", ""),
            }, default=str)
            print(fallback, file=sys.stderr, flush=True)
        except Exception:
            logger.exception("Audit fallback stderr write also failed")

    def check_app_ownership(self, user, app):
        """Returns True if user is allowed to access this application."""
        if user.get("type") == "client" and app["client_id"] != user["sub"]:
            self.log_authz_denial(
                user,
                "authz_denied_not_owner",
                app.get("id", app.get("ref", "")),
                {"actual_owner": app["client_id"]},
            )
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
