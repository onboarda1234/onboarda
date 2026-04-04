"""
Public API v1 — Versioned external API layer
=============================================
Clean, client-safe endpoints for external integrations.
All endpoints are under /api/v1/ and return minimal, well-defined JSON.

Endpoints:
  GET /api/v1/health                          → service health check
  GET /api/v1/applications/{ref}/status        → application status
  GET /api/v1/applications/{ref}/decision      → latest decision record
"""

import logging

from base_handler import BaseHandler
from db import get_db

logger = logging.getLogger("arie")

# Roles permitted to access the public API
_PUBLIC_API_ROLES = ("admin", "sco", "co", "analyst", "client")


class PublicHealthHandler(BaseHandler):
    """GET /api/v1/health — Simple health check."""

    def get(self):
        if not self.check_rate_limit("v1_health", max_attempts=60, window_seconds=60):
            return
        self.success({"status": "ok"})


class PublicApplicationStatusHandler(BaseHandler):
    """GET /api/v1/applications/{ref}/status — Application status (client-safe)."""

    def get(self, app_ref):
        user = self.require_auth(roles=list(_PUBLIC_API_ROLES))
        if not user:
            return
        if not self.check_rate_limit("v1_app_status", max_attempts=30, window_seconds=60):
            return

        db = get_db()
        try:
            app = db.execute(
                "SELECT ref, status, updated_at FROM applications WHERE ref = ?",
                (app_ref,),
            ).fetchone()
            if not app:
                return self.error("Application not found", 404)

            # Clients may only view their own applications
            if user.get("role") == "client":
                full_app = db.execute(
                    "SELECT * FROM applications WHERE ref = ?", (app_ref,)
                ).fetchone()
                if full_app and not self.check_app_ownership(user, full_app):
                    return
        finally:
            db.close()

        self.success({
            "application_ref": app["ref"],
            "status": app["status"],
            "last_updated": app["updated_at"],
        })


class PublicApplicationDecisionHandler(BaseHandler):
    """GET /api/v1/applications/{ref}/decision — Latest decision record (client-safe)."""

    def get(self, app_ref):
        user = self.require_auth(roles=list(_PUBLIC_API_ROLES))
        if not user:
            return
        if not self.check_rate_limit("v1_app_decision", max_attempts=30, window_seconds=60):
            return

        db = get_db()
        try:
            app = db.execute(
                "SELECT id, ref, client_id FROM applications WHERE ref = ?",
                (app_ref,),
            ).fetchone()
            if not app:
                return self.error("Application not found", 404)

            # Clients may only view their own applications
            if user.get("role") == "client":
                full_app = db.execute(
                    "SELECT * FROM applications WHERE ref = ?", (app_ref,)
                ).fetchone()
                if full_app and not self.check_app_ownership(user, full_app):
                    return

            row = db.execute(
                """SELECT decision_type, risk_level, confidence_score, timestamp
                   FROM decision_records
                   WHERE application_ref = ?
                   ORDER BY timestamp DESC
                   LIMIT 1""",
                (app["ref"],),
            ).fetchone()
        finally:
            db.close()

        if not row:
            return self.error("No decision record found for this application", 404)

        # Normalize timestamp
        ts = row["timestamp"]
        if hasattr(ts, "isoformat"):
            ts = ts.isoformat()

        self.success({
            "decision_type": row["decision_type"],
            "risk_level": row["risk_level"],
            "confidence_score": row["confidence_score"],
            "timestamp": ts,
        })
