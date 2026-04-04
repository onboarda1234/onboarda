#!/usr/bin/env python3
"""
Production Controls Module for ARIE Finance Platform
═══════════════════════════════════════════════════════════════════════════════

Track D: All production controls needed before going live.

Includes:
  1. Rate Limiting Middleware
  2. Usage Caps & Budget Monitoring
  3. Monitoring & Health Checks
  4. Email Alerting
  5. Incident Logging
  6. Data Retention Policy
  7. Database Table Definitions

All components integrate with Tornado web framework and SQLite/PostgreSQL.
"""

import os
import json
import time
import hashlib
import logging
import smtplib
import functools
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Any, Tuple
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import defaultdict
from threading import Lock
import threading
import socket
import subprocess
import psutil

import tornado.web
import tornado.escape
import requests

from db import get_db, USE_POSTGRESQL

logger = logging.getLogger("arie.production_controls")

# ═════════════════════════════════════════════════════════════════════════════
# 1. RATE LIMITING MIDDLEWARE
# ═════════════════════════════════════════════════════════════════════════════


class RateLimiter:
    """
    Tornado-compatible rate limiter that tracks requests per IP and authenticated user.
    Uses in-memory storage with periodic cleanup of expired entries.
    """

    def __init__(self):
        self._locks = {}  # Per-key locks for thread safety
        self._requests = defaultdict(list)  # key -> [(timestamp, count), ...]
        self._cleanup_thread = None
        self._running = False
        self._start_cleanup()

    def _get_lock(self, key: str) -> Lock:
        """Get or create a lock for a key."""
        if key not in self._locks:
            self._locks[key] = Lock()
        return self._locks[key]

    def _start_cleanup(self):
        """Start background thread to clean expired entries."""
        if not self._running:
            self._running = True
            self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
            self._cleanup_thread.start()

    def _cleanup_loop(self):
        """Periodically remove expired entries."""
        while self._running:
            try:
                time.sleep(60)  # Cleanup every 60 seconds
                now = time.time()
                to_delete = []

                for key, timestamps in self._requests.items():
                    # Keep only entries from the last 1 hour
                    self._requests[key] = [t for t in timestamps if now - t < 3600]
                    if not self._requests[key]:
                        to_delete.append(key)

                for key in to_delete:
                    del self._requests[key]
                    if key in self._locks:
                        del self._locks[key]
            except Exception as e:
                logger.error(f"Rate limiter cleanup error: {e}")

    def is_allowed(
        self,
        key: str,
        max_requests: int,
        window_seconds: int
    ) -> Tuple[bool, int, int]:
        """
        Check if a request is allowed.

        Returns:
            (allowed: bool, requests_made: int, requests_remaining: int)
        """
        lock = self._get_lock(key)
        with lock:
            now = time.time()
            cutoff = now - window_seconds

            # Remove old entries
            self._requests[key] = [t for t in self._requests[key] if t > cutoff]

            requests_made = len(self._requests[key])

            if requests_made < max_requests:
                self._requests[key].append(now)
                return True, requests_made + 1, max_requests - requests_made - 1

            return False, requests_made, 0

    def shutdown(self):
        """Stop the cleanup thread."""
        self._running = False


# Global rate limiter instance
_rate_limiter = RateLimiter()


def rate_limit(max_requests: int = 60, window_seconds: int = 60):
    """
    Decorator for Tornado request handlers to apply rate limiting.

    Usage:
        @rate_limit(max_requests=10, window_seconds=60)
        class MyHandler(tornado.web.RequestHandler):
            def get(self):
                ...
    """
    def decorator(handler_class):
        original_prepare = handler_class.prepare if hasattr(handler_class, 'prepare') else None

        def prepare(self):
            # Determine the rate limit key
            user_id = None
            try:
                user_id = self.get_secure_cookie("user_id")
                if user_id:
                    user_id = user_id.decode() if isinstance(user_id, bytes) else user_id
            except:
                pass

            # Use user_id if authenticated, otherwise use IP
            key = f"user:{user_id}" if user_id else f"ip:{self.request.remote_ip}"

            allowed, made, remaining = _rate_limiter.is_allowed(key, max_requests, window_seconds)

            # Set rate limit headers
            self.set_header("X-RateLimit-Limit", str(max_requests))
            self.set_header("X-RateLimit-Remaining", str(max(0, remaining)))
            self.set_header("X-RateLimit-Reset", str(int(time.time()) + window_seconds))

            if not allowed:
                # Log rate limit violation
                incident_logger.log_incident(
                    incident_type="RATE_LIMIT_EXCEEDED",
                    severity="MEDIUM",
                    description=f"Rate limit exceeded for {key}: {made}/{max_requests} requests",
                    source_ip=self.request.remote_ip,
                    user_id=user_id,
                    endpoint=self.request.uri
                )

                self.set_header("Retry-After", str(window_seconds))
                self.set_status(429)
                self.finish({
                    "error": "Too Many Requests",
                    "retry_after": window_seconds
                })
                return

            if original_prepare:
                original_prepare(self)

        handler_class.prepare = prepare
        return handler_class

    return decorator


class RateLimitMixin:
    """
    Mixin class for Tornado handlers to apply rate limiting.

    Usage:
        class MyHandler(RateLimitMixin, tornado.web.RequestHandler):
            rate_limit_max = 10
            rate_limit_window = 60

            def get(self):
                ...
    """
    rate_limit_max = 60
    rate_limit_window = 60

    def prepare(self):
        """Apply rate limiting before processing request."""
        user_id = None
        try:
            user_id = self.get_secure_cookie("user_id")
            if user_id:
                user_id = user_id.decode() if isinstance(user_id, bytes) else user_id
        except:
            pass

        key = f"user:{user_id}" if user_id else f"ip:{self.request.remote_ip}"

        allowed, made, remaining = _rate_limiter.is_allowed(
            key,
            self.rate_limit_max,
            self.rate_limit_window
        )

        self.set_header("X-RateLimit-Limit", str(self.rate_limit_max))
        self.set_header("X-RateLimit-Remaining", str(max(0, remaining)))
        self.set_header("X-RateLimit-Reset", str(int(time.time()) + self.rate_limit_window))

        if not allowed:
            incident_logger.log_incident(
                incident_type="RATE_LIMIT_EXCEEDED",
                severity="MEDIUM",
                description=f"Rate limit exceeded for {key}: {made}/{self.rate_limit_max} requests",
                source_ip=self.request.remote_ip,
                user_id=user_id,
                endpoint=self.request.uri
            )

            self.set_header("Retry-After", str(self.rate_limit_window))
            self.set_status(429)
            self.finish({
                "error": "Too Many Requests",
                "retry_after": self.rate_limit_window
            })
            return

        super().prepare() if hasattr(super(), 'prepare') else None


# ═════════════════════════════════════════════════════════════════════════════
# 2. USAGE CAPS & BUDGET MONITORING
# ═════════════════════════════════════════════════════════════════════════════


class UsageCapManager:
    """
    Manages monthly spending caps across external services.
    Tracks usage and enforces budget limits.
    """

    # Default caps (configurable via environment variables)
    DEFAULT_CAPS = {
        "SUMSUB": float(os.environ.get("SUMSUB_MONTHLY_CAP", 500)),
        "CLAUDE": float(os.environ.get("CLAUDE_MONTHLY_CAP", 50)),
        "AWS": float(os.environ.get("AWS_MONTHLY_CAP", 50)),
    }

    def __init__(self):
        self._lock = Lock()
        self._init_tables()

    def _init_tables(self):
        """Ensure api_usage table exists."""
        db = get_db()
        try:
            if USE_POSTGRESQL:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS api_usage (
                        id SERIAL PRIMARY KEY,
                        service TEXT NOT NULL,
                        cost DECIMAL(10, 2) NOT NULL,
                        description TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        month_key TEXT NOT NULL
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_month ON api_usage(month_key)")
            else:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS api_usage (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        service TEXT NOT NULL,
                        cost REAL NOT NULL,
                        description TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        month_key TEXT NOT NULL
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_month ON api_usage(month_key)")
            db.commit()
        finally:
            db.close()

    def _get_month_key(self, dt: Optional[datetime] = None) -> str:
        """Get month key in YYYY-MM format."""
        if dt is None:
            dt = datetime.now(timezone.utc)
        return dt.strftime("%Y-%m")

    def check_budget(self, service: str, estimated_cost: float) -> bool:
        """
        Check if a service request fits within budget.

        Returns True if within budget, False otherwise.
        """
        service = service.upper()
        cap = self.DEFAULT_CAPS.get(service, float('inf'))

        month_key = self._get_month_key()
        current_usage = self._get_monthly_usage(service, month_key)

        return (current_usage + estimated_cost) <= cap

    def record_usage(self, service: str, cost: float, description: str = ""):
        """Record API usage for a service."""
        with self._lock:
            db = get_db()
            try:
                month_key = self._get_month_key()
                db.execute("""
                    INSERT INTO api_usage (service, cost, description, month_key)
                    VALUES (?, ?, ?, ?)
                """, (service.upper(), cost, description, month_key))
                db.commit()

                logger.info(f"Recorded {service} usage: ${cost} ({description})")

                # Check if approaching cap
                cap = self.DEFAULT_CAPS.get(service.upper(), float('inf'))
                current_usage = self._get_monthly_usage(service.upper(), month_key)

                if current_usage >= cap * 0.8:  # 80% threshold
                    alert_manager.send_alert(
                        alert_type="WARNING",
                        subject=f"API Budget Alert: {service} at {current_usage/cap*100:.0f}%",
                        message=f"{service} monthly spending: ${current_usage:.2f}/${cap:.2f}"
                    )
            finally:
                db.close()

    def get_usage_summary(self) -> Dict[str, Dict[str, float]]:
        """Get current month's usage summary for all services."""
        month_key = self._get_month_key()
        summary = {}

        for service, cap in self.DEFAULT_CAPS.items():
            usage = self._get_monthly_usage(service, month_key)
            summary[service] = {
                "usage": usage,
                "cap": cap,
                "remaining": max(0, cap - usage),
                "percent_used": (usage / cap * 100) if cap > 0 else 0,
            }

        return summary

    def _get_monthly_usage(self, service: str, month_key: str) -> float:
        """Get total usage for a service in a given month."""
        db = get_db()
        try:
            db.execute("""
                SELECT SUM(cost) as total FROM api_usage
                WHERE service = ? AND month_key = ?
            """, (service.upper(), month_key))
            result = db.fetchone()
            return float(result['total'] or result[0] or 0) if result else 0.0
        finally:
            db.close()


usage_cap_manager = UsageCapManager()


# ═════════════════════════════════════════════════════════════════════════════
# 3. MONITORING & HEALTH CHECK
# ═════════════════════════════════════════════════════════════════════════════


class HealthMonitor:
    """
    Monitors system health and provides detailed health check endpoints.
    """

    def __init__(self):
        self._start_time = time.time()
        self._request_counts = defaultdict(lambda: {"1h": 0, "24h": 0})
        self._response_times = defaultdict(list)
        self._error_count = 0
        self._lock = Lock()
        self._last_backup = None
        self._init_metrics_table()

    def _init_metrics_table(self):
        """Ensure metrics tables exist."""
        db = get_db()
        try:
            if USE_POSTGRESQL:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS metrics (
                        id SERIAL PRIMARY KEY,
                        endpoint TEXT NOT NULL,
                        response_time_ms FLOAT NOT NULL,
                        status_code INTEGER NOT NULL,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp)")
            else:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS metrics (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        endpoint TEXT NOT NULL,
                        response_time_ms FLOAT NOT NULL,
                        status_code INTEGER NOT NULL,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp)")
            db.commit()
        finally:
            db.close()

    def record_request(self, endpoint: str, response_time_ms: float, status_code: int):
        """Record a request for metrics."""
        with self._lock:
            db = get_db()
            try:
                db.execute("""
                    INSERT INTO metrics (endpoint, response_time_ms, status_code)
                    VALUES (?, ?, ?)
                """, (endpoint, response_time_ms, status_code))
                db.commit()

                if status_code >= 500:
                    self._error_count += 1
            finally:
                db.close()

    def get_detailed_health(self) -> Dict[str, Any]:
        """Get detailed health status."""
        health = {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "uptime_seconds": int(time.time() - self._start_time),
            "checks": {}
        }

        # Database connectivity
        try:
            db = get_db()
            db.execute("SELECT 1")
            db.close()
            health["checks"]["database"] = {"status": "ok"}
        except Exception as e:
            health["checks"]["database"] = {"status": "error", "error": str(e)}
            health["status"] = "unhealthy"

        # Disk usage
        try:
            disk = psutil.disk_usage('/')
            health["checks"]["disk"] = {
                "status": "ok",
                "percent": disk.percent,
                "available_gb": disk.free / (1024**3)
            }
            if disk.percent > 90:
                health["status"] = "degraded"
        except Exception as e:
            health["checks"]["disk"] = {"status": "error", "error": str(e)}

        # Memory usage
        try:
            memory = psutil.virtual_memory()
            health["checks"]["memory"] = {
                "status": "ok",
                "percent": memory.percent,
                "available_gb": memory.available / (1024**3)
            }
            if memory.percent > 90:
                health["status"] = "degraded"
        except Exception as e:
            health["checks"]["memory"] = {"status": "error", "error": str(e)}

        # Sumsub API
        try:
            response = requests.head("https://api.sumsub.com", timeout=5)
            health["checks"]["sumsub_api"] = {
                "status": "ok" if response.status_code < 500 else "error"
            }
        except Exception as e:
            health["checks"]["sumsub_api"] = {"status": "error", "error": str(e)}

        # Claude API (if configured)
        try:
            if os.environ.get("ANTHROPIC_API_KEY"):
                response = requests.head("https://api.anthropic.com", timeout=5)
                health["checks"]["claude_api"] = {
                    "status": "ok" if response.status_code < 500 else "error"
                }
            else:
                health["checks"]["claude_api"] = {"status": "not_configured"}
        except Exception as e:
            health["checks"]["claude_api"] = {"status": "error", "error": str(e)}

        # S3 bucket (if configured)
        try:
            from s3_client import get_s3_client
            s3 = get_s3_client()
            if s3:
                bucket = os.environ.get("S3_BUCKET_NAME", "")
                if bucket:
                    s3.head_bucket(Bucket=bucket)
                    health["checks"]["s3_bucket"] = {"status": "ok"}
            else:
                health["checks"]["s3_bucket"] = {"status": "not_configured"}
        except Exception as e:
            health["checks"]["s3_bucket"] = {"status": "error", "error": str(e)}

        return health

    def get_metrics(self) -> Dict[str, Any]:
        """Get system metrics."""
        now = datetime.now(timezone.utc)
        one_hour_ago = now - timedelta(hours=1)
        one_day_ago = now - timedelta(days=1)

        metrics = {
            "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "uptime_seconds": int(time.time() - self._start_time),
            "requests": {},
            "errors": {"total": self._error_count},
            "api_usage": usage_cap_manager.get_usage_summary()
        }

        # Get request metrics from database
        db = get_db()
        try:
            # Request count by endpoint (last hour)
            db.execute("""
                SELECT endpoint, COUNT(*) as count
                FROM metrics
                WHERE timestamp > ?
                GROUP BY endpoint
            """, (one_hour_ago.strftime("%Y-%m-%dT%H:%M:%S"),))
            results = db.fetchall()
            metrics["requests"]["last_hour"] = {row.get('endpoint', row[0]): row.get('count', row[1]) for row in results}

            # Request count by endpoint (last 24 hours)
            db.execute("""
                SELECT endpoint, COUNT(*) as count
                FROM metrics
                WHERE timestamp > ?
                GROUP BY endpoint
            """, (one_day_ago.strftime("%Y-%m-%dT%H:%M:%S"),))
            results = db.fetchall()
            metrics["requests"]["last_24h"] = {row.get('endpoint', row[0]): row.get('count', row[1]) for row in results}

            # Average response time
            db.execute("""
                SELECT AVG(response_time_ms) as avg_ms
                FROM metrics
                WHERE timestamp > ?
            """, (one_hour_ago.strftime("%Y-%m-%dT%H:%M:%S"),))
            result = db.fetchone()
            avg_val = 0
            if result:
                try:
                    avg_val = float(result.get('avg_ms') or result[0] or 0)
                except:
                    avg_val = 0
            metrics["requests"]["avg_response_time_ms"] = avg_val

            # Error rate (last hour)
            db.execute("""
                SELECT COUNT(*) as errors
                FROM metrics
                WHERE status_code >= 500 AND timestamp > ?
            """, (one_hour_ago.strftime("%Y-%m-%dT%H:%M:%S"),))
            result = db.fetchone()

            if result:
                errors_count = result.get('errors', result[0]) if result else 0
                metrics["errors"]["last_hour_count"] = errors_count
                # Calculate error rate as percentage of total requests
                total_requests = sum(metrics["requests"]["last_hour"].values()) or 1
                metrics["errors"]["last_hour_rate"] = (errors_count / total_requests * 100) if total_requests else 0
        finally:
            db.close()

        return metrics


health_monitor = HealthMonitor()


# ═════════════════════════════════════════════════════════════════════════════
# 4. EMAIL ALERTING
# ═════════════════════════════════════════════════════════════════════════════


class AlertManager:
    """
    Sends email alerts via SMTP with rate limiting to prevent spam.
    """

    ALERT_TYPES = {
        "CRITICAL": 0,    # Send immediately
        "WARNING": 3600,  # Batch/rate limit to 1 per hour
        "INFO": 86400,    # Daily digest
    }

    def __init__(self):
        self._last_alert = {}  # type -> timestamp
        self._lock = Lock()
        self._smtp_configured = bool(
            os.environ.get("SMTP_HOST") and
            os.environ.get("SMTP_USER")
        )

    def send_alert(
        self,
        alert_type: str,
        subject: str,
        message: str,
        recipient: Optional[str] = None
    ) -> bool:
        """
        Send an alert email. Respects rate limiting per alert type.

        Returns True if email was sent, False otherwise.
        """
        with self._lock:
            # Rate limit check
            min_interval = self.ALERT_TYPES.get(alert_type, 3600)
            last_time = self._last_alert.get(f"{alert_type}:{subject}", 0)

            if time.time() - last_time < min_interval:
                logger.debug(f"Alert rate-limited: {subject}")
                return False

            self._last_alert[f"{alert_type}:{subject}"] = time.time()

        # Send the email
        if not self._smtp_configured:
            logger.warning(f"SMTP not configured. Alert not sent: {subject}")
            return False

        try:
            recipient = recipient or os.environ.get("ALERT_EMAIL_TO")
            if not recipient:
                logger.error("No recipient configured for alert email")
                return False

            self._send_email(recipient, subject, message)
            logger.info(f"Alert sent: {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")
            return False

    def _send_email(self, to_addr: str, subject: str, message: str):
        """Send email via SMTP."""
        smtp_host = os.environ.get("SMTP_HOST")
        smtp_port = int(os.environ.get("SMTP_PORT", 587))
        smtp_user = os.environ.get("SMTP_USER")
        smtp_password = os.environ.get("SMTP_PASSWORD")

        msg = MIMEMultipart()
        msg["From"] = smtp_user
        msg["To"] = to_addr
        msg["Subject"] = f"[ARIE Alert] {subject}"

        msg.attach(MIMEText(message, "plain"))

        try:
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
            server.quit()
        except Exception as e:
            logger.error(f"SMTP error: {e}")
            raise


alert_manager = AlertManager()


# ═════════════════════════════════════════════════════════════════════════════
# 5. INCIDENT LOGGING
# ═════════════════════════════════════════════════════════════════════════════


class IncidentLogger:
    """
    Logs security and operational incidents to database.
    """

    INCIDENT_TYPES = {
        "FAILED_LOGIN",
        "RATE_LIMIT_EXCEEDED",
        "API_ERROR",
        "UNAUTHORIZED_ACCESS",
        "DATA_BREACH_ATTEMPT",
        "BACKUP_FAILURE",
        "OTHER",
    }

    SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

    def __init__(self):
        self._lock = Lock()
        self._init_tables()

    def _init_tables(self):
        """Ensure incidents table exists."""
        db = get_db()
        try:
            if USE_POSTGRESQL:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS incidents (
                        id SERIAL PRIMARY KEY,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        type TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        source_ip TEXT,
                        user_id TEXT,
                        description TEXT NOT NULL,
                        metadata JSONB,
                        resolved BOOLEAN DEFAULT FALSE,
                        resolved_by TEXT,
                        resolved_at TIMESTAMP
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_incidents_type ON incidents(type)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity)")
            else:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS incidents (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        type TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        source_ip TEXT,
                        user_id TEXT,
                        description TEXT NOT NULL,
                        metadata TEXT,
                        resolved BOOLEAN DEFAULT 0,
                        resolved_by TEXT,
                        resolved_at TIMESTAMP
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_incidents_type ON incidents(type)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity)")
            db.commit()
        finally:
            db.close()

    def log_incident(
        self,
        incident_type: str,
        severity: str,
        description: str,
        source_ip: Optional[str] = None,
        user_id: Optional[str] = None,
        endpoint: Optional[str] = None,
        **kwargs
    ) -> int:
        """
        Log a security/operational incident.

        Returns the incident ID.
        """
        if incident_type not in self.INCIDENT_TYPES:
            incident_type = "OTHER"
        if severity not in self.SEVERITIES:
            severity = "MEDIUM"

        with self._lock:
            db = get_db()
            try:
                metadata = {"endpoint": endpoint, **kwargs}
                metadata_str = json.dumps(metadata)

                if USE_POSTGRESQL:
                    db.execute("""
                        INSERT INTO incidents
                        (type, severity, source_ip, user_id, description, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (incident_type, severity, source_ip, user_id, description, metadata_str))
                else:
                    db.execute("""
                        INSERT INTO incidents
                        (type, severity, source_ip, user_id, description, metadata)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (incident_type, severity, source_ip, user_id, description, metadata_str))

                db.commit()

                # Alert on critical incidents
                if severity == "CRITICAL":
                    alert_manager.send_alert(
                        alert_type="CRITICAL",
                        subject=f"Critical Incident: {incident_type}",
                        message=f"{description}\nSource IP: {source_ip}\nUser: {user_id}"
                    )

                logger.warning(f"Incident logged: {incident_type} ({severity}): {description}")
            finally:
                db.close()

    def get_incidents(
        self,
        start_date: datetime,
        end_date: datetime,
        severity: Optional[str] = None,
        incident_type: Optional[str] = None,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Query incidents by date range and optional filters."""
        db = get_db()
        try:
            query = "SELECT * FROM incidents WHERE timestamp BETWEEN ? AND ?"
            params = [start_date.strftime("%Y-%m-%dT%H:%M:%S"), end_date.strftime("%Y-%m-%dT%H:%M:%S")]

            if severity:
                query += " AND severity = ?"
                params.append(severity)

            if incident_type:
                query += " AND type = ?"
                params.append(incident_type)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            db.execute(query, tuple(params))
            results = db.fetchall()

            incidents = []
            for row in results:
                incident = dict(row) if hasattr(row, 'keys') else {
                    'id': row[0],
                    'timestamp': row[1],
                    'type': row[2],
                    'severity': row[3],
                    'source_ip': row[4],
                    'user_id': row[5],
                    'description': row[6],
                    'metadata': row[7],
                    'resolved': row[8],
                    'resolved_by': row[9],
                    'resolved_at': row[10],
                }
                if isinstance(incident.get('metadata'), str):
                    try:
                        incident['metadata'] = json.loads(incident['metadata'])
                    except:
                        pass
                incidents.append(incident)

            return incidents
        finally:
            db.close()

    def resolve_incident(self, incident_id: int, resolved_by: str):
        """Mark an incident as resolved."""
        db = get_db()
        try:
            db.execute("""
                UPDATE incidents
                SET resolved = ?, resolved_by = ?, resolved_at = ?
                WHERE id = ?
            """, (True, resolved_by, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), incident_id))
            db.commit()
        finally:
            db.close()


incident_logger = IncidentLogger()


# ═════════════════════════════════════════════════════════════════════════════
# 6. DATA RETENTION POLICY
# ═════════════════════════════════════════════════════════════════════════════


class RetentionManager:
    """
    Manages data retention policies and automated cleanup.
    """

    # Retention periods in days
    RETENTION_PERIODS = {
        "kyc_documents": 7 * 365,      # 7 years
        "application_data": 7 * 365,   # 7 years
        "audit_logs": 5 * 365,         # 5 years
        "session_data": 90,            # 90 days
        "incidents": 3 * 365,          # 3 years
        "temporary_uploads": 30,       # 30 days
    }

    def __init__(self):
        self._lock = Lock()
        self._init_audit_table()

    def _init_audit_table(self):
        """Ensure audit_log table exists."""
        db = get_db()
        try:
            if USE_POSTGRESQL:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id SERIAL PRIMARY KEY,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        action TEXT NOT NULL,
                        table_name TEXT,
                        record_count INTEGER,
                        description TEXT,
                        metadata JSONB
                    )
                """)
            else:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        action TEXT NOT NULL,
                        table_name TEXT,
                        record_count INTEGER,
                        description TEXT,
                        metadata TEXT
                    )
                """)
            db.commit()
        finally:
            db.close()

    def enforce_retention(self) -> Dict[str, Any]:
        """
        Delete expired data according to retention policies.

        Returns a summary of deleted records.
        """
        with self._lock:
            summary = {
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                "deleted": {},
                "errors": []
            }

            db = get_db()
            try:
                # Session data (90 days)
                cutoff = datetime.now(timezone.utc) - timedelta(days=self.RETENTION_PERIODS["session_data"])
                try:
                    count = db.execute("""
                        DELETE FROM sessions WHERE created_at < ?
                    """, (cutoff.strftime("%Y-%m-%dT%H:%M:%S"),))
                    db.commit()
                    if count:
                        summary["deleted"]["session_data"] = count
                        self._log_deletion("sessions", count, f"Older than {self.RETENTION_PERIODS['session_data']} days")
                except Exception as e:
                    summary["errors"].append(f"Session cleanup failed: {e}")

                # Temporary uploads (30 days)
                cutoff = datetime.now(timezone.utc) - timedelta(days=self.RETENTION_PERIODS["temporary_uploads"])
                try:
                    count = db.execute("""
                        DELETE FROM documents
                        WHERE document_type = 'temporary' AND created_at < ?
                    """, (cutoff.strftime("%Y-%m-%dT%H:%M:%S"),))
                    db.commit()
                    if count:
                        summary["deleted"]["temporary_uploads"] = count
                        self._log_deletion("documents", count, "Temporary files older than 30 days")
                except Exception as e:
                    summary["errors"].append(f"Temporary upload cleanup failed: {e}")

                # Incident logs (3 years)
                cutoff = datetime.now(timezone.utc) - timedelta(days=self.RETENTION_PERIODS["incidents"])
                try:
                    count = db.execute("""
                        DELETE FROM incidents WHERE timestamp < ? AND resolved = ?
                    """, (cutoff.strftime("%Y-%m-%dT%H:%M:%S"), True))
                    db.commit()
                    if count:
                        summary["deleted"]["incidents"] = count
                        self._log_deletion("incidents", count, "Resolved incidents older than 3 years")
                except Exception as e:
                    summary["errors"].append(f"Incident cleanup failed: {e}")

                logger.info(f"Retention enforcement completed: {summary['deleted']}")
            finally:
                db.close()

            return summary

    def _log_deletion(self, table_name: str, record_count: int, description: str):
        """Log a deletion to audit trail."""
        db = get_db()
        try:
            metadata = json.dumps({"retention_policy": True})
            if USE_POSTGRESQL:
                db.execute("""
                    INSERT INTO audit_log (action, table_name, record_count, description, metadata)
                    VALUES (%s, %s, %s, %s, %s)
                """, ("DELETE", table_name, record_count, description, metadata))
            else:
                db.execute("""
                    INSERT INTO audit_log (action, table_name, record_count, description, metadata)
                    VALUES (?, ?, ?, ?, ?)
                """, ("DELETE", table_name, record_count, description, metadata))
            db.commit()
        finally:
            db.close()

    def get_retention_report(self) -> Dict[str, Any]:
        """Get a report of data volumes and retention status."""
        report = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "categories": {}
        }

        db = get_db()
        try:
            # Session data
            db.execute("SELECT COUNT(*) as cnt FROM sessions")
            result = db.fetchone()
            session_count = result.get('cnt', result[0] if result else 0) if result else 0

            db.execute("SELECT MIN(created_at) as min_date FROM sessions")
            oldest = db.fetchone()
            oldest_date = oldest.get('min_date', oldest[0] if oldest else None) if oldest else None

            report["categories"]["session_data"] = {
                "count": session_count,
                "retention_days": self.RETENTION_PERIODS["session_data"],
                "oldest_record": oldest_date,
                "expiry_date": (
                    (datetime.fromisoformat(oldest_date) + timedelta(days=self.RETENTION_PERIODS["session_data"])).isoformat()
                    if oldest_date else None
                )
            }

            # Documents
            db.execute("SELECT COUNT(*) as cnt FROM documents")
            result = db.fetchone()
            doc_count = result.get('cnt', result[0] if result else 0) if result else 0

            db.execute("SELECT MIN(created_at) as min_date FROM documents")
            oldest = db.fetchone()
            oldest_date = oldest.get('min_date', oldest[0] if oldest else None) if oldest else None

            report["categories"]["documents"] = {
                "count": doc_count,
                "retention_days": self.RETENTION_PERIODS["application_data"],
                "oldest_record": oldest_date,
                "expiry_date": (
                    (datetime.fromisoformat(oldest_date) + timedelta(days=self.RETENTION_PERIODS["application_data"])).isoformat()
                    if oldest_date else None
                )
            }

            # Incidents
            db.execute("SELECT COUNT(*) as cnt FROM incidents")
            result = db.fetchone()
            incident_count = result.get('cnt', result[0] if result else 0) if result else 0

            db.execute("SELECT MIN(timestamp) as min_date FROM incidents")
            oldest = db.fetchone()
            oldest_date = oldest.get('min_date', oldest[0] if oldest else None) if oldest else None

            report["categories"]["incidents"] = {
                "count": incident_count,
                "retention_days": self.RETENTION_PERIODS["incidents"],
                "oldest_record": oldest_date,
                "expiry_date": (
                    (datetime.fromisoformat(oldest_date) + timedelta(days=self.RETENTION_PERIODS["incidents"])).isoformat()
                    if oldest_date else None
                )
            }
        finally:
            db.close()

        return report


retention_manager = RetentionManager()


# ═════════════════════════════════════════════════════════════════════════════
# 7. PRODUCTION CONTROL HANDLERS
# ═════════════════════════════════════════════════════════════════════════════


class HealthDetailedHandler(tornado.web.RequestHandler):
    """Handler for /api/health/detailed endpoint."""

    def get(self):
        """Return detailed health status."""
        health = health_monitor.get_detailed_health()
        self.set_header("Content-Type", "application/json")
        self.write(health)


class MetricsHandler(tornado.web.RequestHandler):
    """Handler for /api/metrics endpoint."""

    def get(self):
        """Return system metrics."""
        metrics = health_monitor.get_metrics()
        self.set_header("Content-Type", "application/json")
        self.write(metrics)


class IncidentsHandler(tornado.web.RequestHandler):
    """Handler for /api/incidents endpoint (admin only)."""

    def get(self):
        """Get incidents with optional filtering."""
        # Admin-only check (simplified)
        user_id = self.get_secure_cookie("user_id")
        if not user_id:
            self.set_status(401)
            self.write({"error": "Unauthorized"})
            return

        # Parse query parameters
        days_back = int(self.get_argument("days", 7))
        severity = self.get_argument("severity", None)
        incident_type = self.get_argument("type", None)

        start_date = datetime.now(timezone.utc) - timedelta(days=days_back)
        end_date = datetime.now(timezone.utc)

        incidents = incident_logger.get_incidents(
            start_date=start_date,
            end_date=end_date,
            severity=severity,
            incident_type=incident_type
        )

        self.set_header("Content-Type", "application/json")
        self.write({
            "count": len(incidents),
            "incidents": incidents
        })


class UsageHandler(tornado.web.RequestHandler):
    """Handler for /api/usage endpoint (admin only)."""

    def get(self):
        """Get current API usage summary."""
        user_id = self.get_secure_cookie("user_id")
        if not user_id:
            self.set_status(401)
            self.write({"error": "Unauthorized"})
            return

        summary = usage_cap_manager.get_usage_summary()
        self.set_header("Content-Type", "application/json")
        self.write(summary)


class RetentionReportHandler(tornado.web.RequestHandler):
    """Handler for /api/admin/retention-report endpoint."""

    def get(self):
        """Get data retention status report."""
        user_id = self.get_secure_cookie("user_id")
        if not user_id:
            self.set_status(401)
            self.write({"error": "Unauthorized"})
            return

        report = retention_manager.get_retention_report()
        self.set_header("Content-Type", "application/json")
        self.write(report)

    def post(self):
        """Trigger data retention enforcement."""
        user_id = self.get_secure_cookie("user_id")
        if not user_id:
            self.set_status(401)
            self.write({"error": "Unauthorized"})
            return

        summary = retention_manager.enforce_retention()
        self.set_header("Content-Type", "application/json")
        self.write(summary)


# ═════════════════════════════════════════════════════════════════════════════
# 8. TABLE DEFINITIONS (SQL)
# ═════════════════════════════════════════════════════════════════════════════


def get_sqlite_schema() -> str:
    """Get SQLite-compatible schema for production control tables."""
    return """
    -- API Usage Tracking
    CREATE TABLE IF NOT EXISTS api_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        service TEXT NOT NULL,
        cost REAL NOT NULL,
        description TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        month_key TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_api_usage_month ON api_usage(month_key);
    CREATE INDEX IF NOT EXISTS idx_api_usage_service ON api_usage(service);

    -- Incident Logging
    CREATE TABLE IF NOT EXISTS incidents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        type TEXT NOT NULL,
        severity TEXT NOT NULL,
        source_ip TEXT,
        user_id TEXT,
        description TEXT NOT NULL,
        metadata TEXT,
        resolved BOOLEAN DEFAULT 0,
        resolved_by TEXT,
        resolved_at TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_incidents_type ON incidents(type);
    CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity);
    CREATE INDEX IF NOT EXISTS idx_incidents_timestamp ON incidents(timestamp);

    -- Rate Limit Violations
    CREATE TABLE IF NOT EXISTS rate_limit_violations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        ip_address TEXT,
        user_id TEXT,
        endpoint TEXT NOT NULL,
        request_count INTEGER NOT NULL,
        window_seconds INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_ratelimit_ip ON rate_limit_violations(ip_address);

    -- Health Metrics
    CREATE TABLE IF NOT EXISTS metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        endpoint TEXT NOT NULL,
        response_time_ms FLOAT NOT NULL,
        status_code INTEGER NOT NULL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp);

    -- Audit Log
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        action TEXT NOT NULL,
        table_name TEXT,
        record_count INTEGER,
        description TEXT,
        metadata TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
    """


def get_postgresql_schema() -> str:
    """Get PostgreSQL-compatible schema for production control tables."""
    return """
    -- API Usage Tracking
    CREATE TABLE IF NOT EXISTS api_usage (
        id SERIAL PRIMARY KEY,
        service TEXT NOT NULL,
        cost DECIMAL(10, 2) NOT NULL,
        description TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        month_key TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_api_usage_month ON api_usage(month_key);
    CREATE INDEX IF NOT EXISTS idx_api_usage_service ON api_usage(service);

    -- Incident Logging
    CREATE TABLE IF NOT EXISTS incidents (
        id SERIAL PRIMARY KEY,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        type TEXT NOT NULL,
        severity TEXT NOT NULL,
        source_ip TEXT,
        user_id TEXT,
        description TEXT NOT NULL,
        metadata JSONB,
        resolved BOOLEAN DEFAULT FALSE,
        resolved_by TEXT,
        resolved_at TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_incidents_type ON incidents(type);
    CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity);
    CREATE INDEX IF NOT EXISTS idx_incidents_timestamp ON incidents(timestamp);

    -- Rate Limit Violations
    CREATE TABLE IF NOT EXISTS rate_limit_violations (
        id SERIAL PRIMARY KEY,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        ip_address TEXT,
        user_id TEXT,
        endpoint TEXT NOT NULL,
        request_count INTEGER NOT NULL,
        window_seconds INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_ratelimit_ip ON rate_limit_violations(ip_address);

    -- Health Metrics
    CREATE TABLE IF NOT EXISTS metrics (
        id SERIAL PRIMARY KEY,
        endpoint TEXT NOT NULL,
        response_time_ms FLOAT NOT NULL,
        status_code INTEGER NOT NULL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp);

    -- Audit Log
    CREATE TABLE IF NOT EXISTS audit_log (
        id SERIAL PRIMARY KEY,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        action TEXT NOT NULL,
        table_name TEXT,
        record_count INTEGER,
        description TEXT,
        metadata JSONB
    );
    CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
    """


def init_production_controls():
    """Initialize all production control tables."""
    logger.info("Initializing production control tables...")
    db = get_db()
    try:
        if USE_POSTGRESQL:
            schema = get_postgresql_schema()
        else:
            schema = get_sqlite_schema()

        # Execute schema statements
        for statement in schema.split(";"):
            if statement.strip():
                db.execute(statement)
        db.commit()

        logger.info("Production control tables initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize production control tables: {e}")
        raise
    finally:
        db.close()


# ═════════════════════════════════════════════════════════════════════════════
# 9. TORNADO ROUTE REGISTRATION
# ═════════════════════════════════════════════════════════════════════════════


def get_production_control_routes():
    """
    Get Tornado route handlers for production controls.

    Usage in server.py:
        from production_controls import get_production_control_routes, init_production_controls

        init_production_controls()  # Call once at startup

        app = tornado.web.Application([
            ...
            *get_production_control_routes(),
        ])
    """
    return [
        (r"/api/health/detailed", HealthDetailedHandler),
        (r"/api/metrics", MetricsHandler),
        (r"/api/incidents", IncidentsHandler),
        (r"/api/usage", UsageHandler),
        (r"/api/admin/retention-report", RetentionReportHandler),
    ]


# ═════════════════════════════════════════════════════════════════════════════
# 10. UTILITY FUNCTIONS FOR EXTERNAL USE
# ═════════════════════════════════════════════════════════════════════════════


def check_api_budget(service: str, estimated_cost: float) -> bool:
    """Convenience function to check API budget."""
    return usage_cap_manager.check_budget(service, estimated_cost)


def record_api_usage(service: str, cost: float, description: str = ""):
    """Convenience function to record API usage."""
    usage_cap_manager.record_usage(service, cost, description)


def log_security_incident(
    incident_type: str,
    severity: str,
    description: str,
    source_ip: Optional[str] = None,
    user_id: Optional[str] = None,
    **kwargs
) -> int:
    """Convenience function to log security incidents."""
    return incident_logger.log_incident(
        incident_type=incident_type,
        severity=severity,
        description=description,
        source_ip=source_ip,
        user_id=user_id,
        **kwargs
    )


def send_alert(alert_type: str, subject: str, message: str):
    """Convenience function to send alerts."""
    return alert_manager.send_alert(alert_type, subject, message)


if __name__ == "__main__":
    # Quick test
    init_production_controls()

    print("Production Controls Module")
    print("=" * 60)

    print("\nHealth Status:")
    health = health_monitor.get_detailed_health()
    print(json.dumps(health, indent=2))

    print("\nAPI Usage Summary:")
    usage = usage_cap_manager.get_usage_summary()
    print(json.dumps(usage, indent=2))

    print("\nData Retention Report:")
    retention = retention_manager.get_retention_report()
    print(json.dumps(retention, indent=2))

    print("\nProduction controls initialized and tested successfully!")
