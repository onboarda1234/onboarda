"""
Public API v1 — Versioned external API layer
=============================================
Clean, client-safe endpoints for external integrations.
All endpoints are under /api/v1/ and return minimal, well-defined JSON.

Endpoints:
  GET /api/v1/health                          → service health check
  GET /api/v1/applications/{ref}/status        → application status
  GET /api/v1/applications/{ref}/decision      → latest decision record
  GET /api/v1/dashboard/status                → lightweight client status dashboard
"""

import logging

from base_handler import BaseHandler
from branding import get_status_label
from db import get_db

logger = logging.getLogger("arie")

# Roles permitted to access the public API
_PUBLIC_API_ROLES = ("admin", "sco", "co", "analyst", "client")


def _normalize_ts(value):
    """Return an ISO-8601 string for both datetime objects and raw strings."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


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
                "SELECT ref, status, updated_at, client_id FROM applications WHERE ref = ?",
                (app_ref,),
            ).fetchone()
            if not app:
                return self.error("Application not found", 404)

            # Clients may only view their own applications
            if user.get("type") == "client":
                if not self.check_app_ownership(user, app):
                    return
        finally:
            db.close()

        self.success({
            "application_ref": app["ref"],
            "status": app["status"],
            "status_label": get_status_label(app["status"]),
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
                "SELECT ref, client_id FROM applications WHERE ref = ?",
                (app_ref,),
            ).fetchone()
            if not app:
                return self.error("Application not found", 404)

            # Clients may only view their own applications
            if user.get("type") == "client":
                if not self.check_app_ownership(user, app):
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


class PublicDashboardStatusHandler(BaseHandler):
    """GET /api/v1/dashboard/status — Lightweight client status dashboard."""

    def get(self):
        user = self.require_auth(roles=list(_PUBLIC_API_ROLES))
        if not user:
            return
        if not self.check_rate_limit("v1_dashboard_status", max_attempts=30, window_seconds=60):
            return

        db = get_db()
        try:
            # Scope: clients see only their own data; officers see all
            is_client = user.get("type") == "client"
            client_filter = " WHERE client_id = ?" if is_client else ""
            params = (user["sub"],) if is_client else ()

            # Total applications
            total = db.execute(
                f"SELECT COUNT(*) AS c FROM applications{client_filter}",
                params,
            ).fetchone()["c"]

            # Applications grouped by status
            status_rows = db.execute(
                f"SELECT status, COUNT(*) AS c FROM applications{client_filter} GROUP BY status",
                params,
            ).fetchall()
            by_status = {r["status"]: r["c"] for r in status_rows}

            # Applications grouped by risk level from the latest decision_record per application
            if is_client:
                risk_sql = """
                    SELECT dr.risk_level, COUNT(DISTINCT dr.application_ref) AS c
                    FROM decision_records dr
                    JOIN (
                        SELECT application_ref, MAX(timestamp) AS max_timestamp
                        FROM decision_records
                        GROUP BY application_ref
                    ) latest
                        ON latest.application_ref = dr.application_ref
                       AND latest.max_timestamp = dr.timestamp
                    JOIN applications a ON a.ref = dr.application_ref
                    WHERE a.client_id = ?
                    GROUP BY dr.risk_level
                """
            else:
                risk_sql = """
                    SELECT dr.risk_level, COUNT(DISTINCT dr.application_ref) AS c
                    FROM decision_records dr
                    JOIN (
                        SELECT application_ref, MAX(timestamp) AS max_timestamp
                        FROM decision_records
                        GROUP BY application_ref
                    ) latest
                        ON latest.application_ref = dr.application_ref
                       AND latest.max_timestamp = dr.timestamp
                    GROUP BY dr.risk_level
                """
            risk_rows = db.execute(risk_sql, params if is_client else ()).fetchall()
            by_risk = {r["risk_level"]: r["c"] for r in risk_rows if r["risk_level"]}

            # Recent activity: last 5 updated applications
            recent_rows = db.execute(
                f"SELECT ref, status, updated_at FROM applications{client_filter} ORDER BY updated_at DESC LIMIT 5",
                params,
            ).fetchall()
            recent_activity = [
                {
                    "application_ref": r["ref"],
                    "status": r["status"],
                    "status_label": get_status_label(r["status"]),
                    "timestamp": _normalize_ts(r["updated_at"]),
                }
                for r in recent_rows
            ]

            # last_updated: most recent updated_at across visible applications
            last_row = db.execute(
                f"SELECT MAX(updated_at) AS last_updated FROM applications{client_filter}",
                params,
            ).fetchone()
            last_updated = _normalize_ts(last_row["last_updated"]) if last_row else None
        finally:
            db.close()

        self.success({
            "total_applications": total,
            "applications_by_status": by_status,
            "applications_by_risk_level": by_risk,
            "recent_activity": recent_activity,
            "last_updated": last_updated,
        })
