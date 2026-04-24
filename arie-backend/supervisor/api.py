"""
ARIE Finance — AI Agent Supervisor: API Endpoints
====================================================
Tornado-compatible request handlers for the supervisor framework.
Integrates with the existing server.py Tornado application.

Endpoints:
  POST   /api/supervisor/pipeline/run         — Run a supervisor pipeline
  GET    /api/supervisor/pipeline/:id         — Get pipeline results
  GET    /api/supervisor/pipeline/:id/review  — Get review package for officer
  POST   /api/supervisor/review               — Submit officer review decision
  POST   /api/supervisor/escalate             — Manually escalate a case
  GET    /api/supervisor/escalations           — List pending escalations
  GET    /api/supervisor/reviews               — List review history
  GET    /api/supervisor/overrides             — List override history
  GET    /api/supervisor/audit                 — Query audit log
  GET    /api/supervisor/audit/verify          — Verify audit chain integrity
  GET    /api/supervisor/metrics               — Agent performance metrics
  GET    /api/supervisor/stats                 — System stats
  GET    /api/supervisor/dashboard             — Governance dashboard data
  GET    /api/supervisor/rules                 — List compliance rules
  PUT    /api/supervisor/rules/:id            — Update a compliance rule
  POST   /api/supervisor/assistant/review      — AI assistant review summary
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import tornado.web

from .compliance_assistant import ComplianceAssistant
from .human_review import HumanReviewService
from .schemas import ReviewDecision, TriggerType
from .supervisor import AgentSupervisor, SupervisorPipelineResult


def _decode_token(token: str):
    """Decode a JWT token — delegates to auth module."""
    try:
        from auth import decode_token
        return decode_token(token)
    except ImportError:
        return None

logger = logging.getLogger("arie.supervisor.api")


# ═══════════════════════════════════════════════════════════
# GLOBAL INSTANCES (initialized by setup_supervisor)
# ═══════════════════════════════════════════════════════════
_supervisor: Optional[AgentSupervisor] = None
_review_service: Optional[HumanReviewService] = None
_assistant: Optional[ComplianceAssistant] = None
_pipeline_cache: Dict[str, SupervisorPipelineResult] = {}


# ═══════════════════════════════════════════════════════════
# PIPELINE PERSISTENCE (database-backed)
# ═══════════════════════════════════════════════════════════

def _get_db():
    """Import get_db lazily to avoid circular imports."""
    import sys
    db_mod = sys.modules.get("db")
    if db_mod and hasattr(db_mod, "get_db"):
        return db_mod.get_db()
    try:
        from db import get_db
        return get_db()
    except ImportError:
        return None


def persist_pipeline_result(
    result: SupervisorPipelineResult,
    trigger_type: str = None,
    trigger_source: str = None,
    _db=None,
):
    """Persist a pipeline result to the database.

    Args:
        result: The completed pipeline result.
        trigger_type: e.g. "onboarding".
        trigger_source: e.g. "backoffice:<user_id>".
        _db: Optional caller-owned DB connection.  When provided the function
             uses it (no open / close / commit — caller is responsible for all
             three).  When None the function opens its own connection, commits,
             and closes it.  Pass an external connection when you need the
             pipeline-result INSERT and an audit-chain entry to share one
             transaction (fail-closed transactionality).
    """
    _own_db = _db is None
    if _own_db:
        db = _get_db()
        if db is None:
            logger.warning("Cannot persist pipeline result: DB not available")
            return
    else:
        db = _db

    try:
        result_dict = result.to_dict()
        # Include agent_results in the persisted JSON for the detail tab
        result_dict["agent_results"] = [
            {
                "agent_type": at.value,
                "agent_name": out.agent_name,
                "status": out.status.value,
                "confidence": out.confidence_score,
                "findings_count": len(out.findings),
                "issues_count": len(out.detected_issues),
                "escalation_flag": out.escalation_flag,
                "recommendation": out.recommendation,
            }
            for at, out in result.agent_outputs.items()
        ]
        result_dict["failed_agent_details"] = result.failed_agents
        result_json = json.dumps(result_dict, default=str)

        # Use INSERT with ON CONFLICT for PostgreSQL compatibility (INSERT OR REPLACE is SQLite-only)
        import os
        if os.environ.get("DATABASE_URL"):
            db.execute(
                """INSERT INTO supervisor_pipeline_results
                   (id, pipeline_id, application_id, status, trigger_type, trigger_source,
                    started_at, completed_at, result_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    result_json = EXCLUDED.result_json,
                    completed_at = EXCLUDED.completed_at""",
                (
                    result.pipeline_id,
                    result.pipeline_id,
                    result.application_id,
                    result.status,
                    trigger_type,
                    trigger_source,
                    result.started_at,
                    result.completed_at,
                    result_json,
                )
            )
        else:
            db.execute(
                """INSERT OR REPLACE INTO supervisor_pipeline_results
                   (id, pipeline_id, application_id, status, trigger_type, trigger_source,
                    started_at, completed_at, result_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result.pipeline_id,
                    result.pipeline_id,
                    result.application_id,
                    result.status,
                    trigger_type,
                    trigger_source,
                    result.started_at,
                    result.completed_at,
                    result_json,
                )
            )
        if _own_db:
            db.commit()
        logger.info("Pipeline result %s persisted to DB for app %s", result.pipeline_id, result.application_id)
    except Exception as e:
        logger.error("Failed to persist pipeline result %s: %s", result.pipeline_id, e)
        if not _own_db:
            # Propagate so the caller's transaction is not committed
            raise
    finally:
        if _own_db:
            try:
                db.close()
            except Exception:
                pass


def load_latest_pipeline_result(application_id: str) -> Optional[Dict[str, Any]]:
    """Load the most recent pipeline result for an application from the database."""
    db = _get_db()
    if db is None:
        return None
    try:
        row = db.execute(
            """SELECT result_json, completed_at FROM supervisor_pipeline_results
               WHERE application_id = ?
               ORDER BY completed_at DESC LIMIT 1""",
            (application_id,)
        ).fetchone()
        if row:
            return json.loads(row["result_json"])
        return None
    except Exception as e:
        logger.error("Failed to load pipeline result for app %s: %s", application_id, e)
        return None
    finally:
        try:
            db.close()
        except Exception:
            pass


def setup_supervisor(db_path: str) -> AgentSupervisor:
    """Initialize supervisor framework. Call once at app startup."""
    global _supervisor, _review_service, _assistant
    _supervisor = AgentSupervisor(db_path=db_path)
    _review_service = HumanReviewService(db_path=db_path, audit_logger=_supervisor.audit)
    _assistant = ComplianceAssistant()
    logger.info("Supervisor framework initialized with db=%s", db_path)
    return _supervisor


def get_supervisor() -> AgentSupervisor:
    if not _supervisor:
        raise RuntimeError("Supervisor not initialized. Call setup_supervisor() first.")
    return _supervisor


# ═══════════════════════════════════════════════════════════
# BASE HANDLER
# ═══════════════════════════════════════════════════════════

class SupervisorBaseHandler(tornado.web.RequestHandler):
    """Base handler with common utilities and JWT authentication."""

    def set_default_headers(self):
        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def options(self, *args, **kwargs):
        self.set_status(204)
        self.finish()

    def get_current_user_token(self):
        """Decode JWT from Bearer header or session cookie."""
        auth = self.request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return _decode_token(auth[7:])
        session_token = self.get_cookie("arie_session", None)
        if session_token:
            return _decode_token(session_token)
        return None

    def require_auth(self, roles: Optional[List[str]] = None):
        """Require authentication; optionally restrict to specific roles.
        Returns the decoded user dict, or None (after writing 401/403)."""
        user = self.get_current_user_token()
        if not user:
            self.set_status(401)
            self.write(json.dumps({"error": "Authentication required"}))
            return None
        if roles and user.get("role") not in roles:
            self.set_status(403)
            self.write(json.dumps({"error": "Insufficient permissions"}))
            return None
        return user

    def write_json(self, data: Any, status: int = 200):
        self.set_status(status)
        self.write(json.dumps(data, default=str))

    def write_error_json(self, status: int, message: str, details: Any = None):
        self.set_status(status)
        body = {"error": message, "status": status}
        if details:
            body["details"] = details
        self.write(json.dumps(body, default=str))

    def get_json_body(self) -> Dict[str, Any]:
        try:
            return json.loads(self.request.body)
        except (json.JSONDecodeError, TypeError):
            return {}


# ═══════════════════════════════════════════════════════════
# PIPELINE ENDPOINTS
# ═══════════════════════════════════════════════════════════

class PipelineRunHandler(SupervisorBaseHandler):
    """POST /api/supervisor/pipeline/run — Run a supervisor pipeline."""

    async def post(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return
        body = self.get_json_body()
        application_id = body.get("application_id")
        trigger_type = body.get("trigger_type", "onboarding")
        trigger_source = body.get("trigger_source", "api")

        if not application_id:
            return self.write_error_json(400, "application_id is required")

        try:
            tt = TriggerType(trigger_type)
        except ValueError:
            return self.write_error_json(400, f"Invalid trigger_type: {trigger_type}")

        import asyncio
        supervisor = get_supervisor()
        try:
            result = await asyncio.wait_for(
                supervisor.run_pipeline(
                    application_id=application_id,
                    trigger_type=tt,
                    context_data=body.get("context"),
                    trigger_source=trigger_source,
                ),
                timeout=120.0,
            )
        except asyncio.TimeoutError:
            logger.error("Pipeline run timed out after 120s for %s", application_id)
            return self.write_error_json(504, "Pipeline execution timed out after 120 seconds")
        except Exception as e:
            logger.exception("Pipeline run failed for %s: %s", application_id, e)
            return self.write_error_json(500, f"Pipeline execution error: {type(e).__name__}: {e}")

        # Cache in memory + persist to database
        _pipeline_cache[result.pipeline_id] = result
        persist_pipeline_result(result, trigger_type=trigger_type, trigger_source=trigger_source)

        self.write_json(result.to_dict())


class PipelineDetailHandler(SupervisorBaseHandler):
    """GET /api/supervisor/pipeline/:id — Get pipeline results."""

    def get(self, pipeline_id: str):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        result = _pipeline_cache.get(pipeline_id)
        if not result:
            return self.write_error_json(404, "Pipeline not found")
        self.write_json(result.to_dict())


class PipelineReviewPackageHandler(SupervisorBaseHandler):
    """GET /api/supervisor/pipeline/:id/review — Get review package for officer."""

    def get(self, pipeline_id: str):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        result = _pipeline_cache.get(pipeline_id)
        if not result:
            return self.write_error_json(404, "Pipeline not found")

        package = _review_service.prepare_review_package(result)
        self.write_json(package)


# ═══════════════════════════════════════════════════════════
# REVIEW ENDPOINTS
# ═══════════════════════════════════════════════════════════

class ReviewSubmitHandler(SupervisorBaseHandler):
    """POST /api/supervisor/review — Submit officer review decision."""

    def post(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return
        body = self.get_json_body()

        required = ["pipeline_id", "reviewer_id", "reviewer_name",
                     "reviewer_role", "decision", "decision_reason"]
        missing = [f for f in required if not body.get(f)]
        if missing:
            return self.write_error_json(400, f"Missing required fields: {missing}")

        pipeline_id = body["pipeline_id"]
        result = _pipeline_cache.get(pipeline_id)
        if not result:
            return self.write_error_json(404, "Pipeline not found")

        try:
            review = _review_service.submit_review(
                pipeline_result=result,
                reviewer_id=body["reviewer_id"],
                reviewer_name=body["reviewer_name"],
                reviewer_role=body["reviewer_role"],
                decision=body["decision"],
                decision_reason=body["decision_reason"],
                risk_level_assigned=body.get("risk_level_assigned"),
                conditions=body.get("conditions"),
                follow_up_required=body.get("follow_up_required", False),
                follow_up_details=body.get("follow_up_details"),
                override_ai=body.get("override_ai", False),
                override_reason=body.get("override_reason"),
            )
            self.write_json(review.model_dump())
        except ValueError as e:
            self.write_error_json(400, str(e))
        except Exception as e:
            logger.error("Review submission failed: %s", e)
            self.write_error_json(500, "Internal error during review submission")


class ReviewListHandler(SupervisorBaseHandler):
    """GET /api/supervisor/reviews — List review history."""

    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        app_id = self.get_argument("application_id", None)
        reviewer_id = self.get_argument("reviewer_id", None)
        limit = int(self.get_argument("limit", "50"))

        reviews = _review_service.get_reviews(
            application_id=app_id,
            reviewer_id=reviewer_id,
            limit=limit,
        )
        self.write_json({"reviews": reviews, "count": len(reviews)})


# ═══════════════════════════════════════════════════════════
# ESCALATION ENDPOINTS
# ═══════════════════════════════════════════════════════════

class EscalationHandler(SupervisorBaseHandler):
    """POST /api/supervisor/escalate — Manually escalate a case."""

    def post(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return
        body = self.get_json_body()
        required = ["application_id", "pipeline_id", "escalation_level",
                     "reason", "escalated_by", "escalated_by_role"]
        missing = [f for f in required if not body.get(f)]
        if missing:
            return self.write_error_json(400, f"Missing required fields: {missing}")

        result = _review_service.escalate_case(
            application_id=body["application_id"],
            pipeline_id=body["pipeline_id"],
            escalation_level=body["escalation_level"],
            reason=body["reason"],
            escalated_by=body["escalated_by"],
            escalated_by_role=body["escalated_by_role"],
            assigned_to=body.get("assigned_to"),
        )
        self.write_json(result)


class EscalationListHandler(SupervisorBaseHandler):
    """GET /api/supervisor/escalations — List pending escalations."""

    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        level = self.get_argument("level", None)
        limit = int(self.get_argument("limit", "50"))
        escalations = _review_service.get_pending_escalations(
            escalation_level=level, limit=limit
        )
        self.write_json({"escalations": escalations, "count": len(escalations)})


# ═══════════════════════════════════════════════════════════
# OVERRIDE ENDPOINTS
# ═══════════════════════════════════════════════════════════

class OverrideListHandler(SupervisorBaseHandler):
    """GET /api/supervisor/overrides — List override history."""

    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        app_id = self.get_argument("application_id", None)
        limit = int(self.get_argument("limit", "50"))
        overrides = _review_service.get_overrides(
            application_id=app_id, limit=limit
        )
        self.write_json({"overrides": overrides, "count": len(overrides)})


# ═══════════════════════════════════════════════════════════
# AUDIT ENDPOINTS
# ═══════════════════════════════════════════════════════════

class AuditLogHandler(SupervisorBaseHandler):
    """GET /api/supervisor/audit — Query audit log."""

    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        app_id = self.get_argument("application_id", None)
        event_type = self.get_argument("event_type", None)
        limit = int(self.get_argument("limit", "100"))

        supervisor = get_supervisor()
        entries = supervisor.audit.get_entries(
            application_id=app_id,
            event_type=event_type,
            limit=limit,
        )
        self.write_json({"entries": entries, "count": len(entries)})


class AuditVerifyHandler(SupervisorBaseHandler):
    """GET /api/supervisor/audit/verify — Verify audit chain integrity."""

    def get(self):
        user = self.require_auth(roles=["admin", "sco"])
        if not user:
            return
        limit = int(self.get_argument("limit", "1000"))
        supervisor = get_supervisor()
        result = supervisor.audit.verify_chain_integrity(limit=limit)
        self.write_json(result)


# ═══════════════════════════════════════════════════════════
# METRICS / DASHBOARD
# ═══════════════════════════════════════════════════════════

class StatsHandler(SupervisorBaseHandler):
    """GET /api/supervisor/stats — System stats."""

    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        supervisor = get_supervisor()
        self.write_json(supervisor.get_stats())


class DashboardHandler(SupervisorBaseHandler):
    """GET /api/supervisor/dashboard — Governance dashboard data."""

    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        supervisor = get_supervisor()
        stats = supervisor.get_stats()

        # Build dashboard data
        dashboard = {
            "system_stats": stats,
            "recent_pipelines": [
                r.to_dict() for r in list(_pipeline_cache.values())[-20:]
            ],
            "pending_escalations": (
                _review_service.get_pending_escalations(limit=20)
                if _review_service else []
            ),
        }
        self.write_json(dashboard)


# ═══════════════════════════════════════════════════════════
# RULES ENDPOINTS
# ═══════════════════════════════════════════════════════════

class RulesListHandler(SupervisorBaseHandler):
    """GET /api/supervisor/rules — List compliance rules."""

    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return
        supervisor = get_supervisor()
        rules = [
            {
                "id": r.rule_id,
                "name": r.rule_name,
                "category": r.rule_category,
                "description": r.description,
                "action": r.action.value,
                "severity": r.severity.value,
                "overrides_ai": r.overrides_ai,
                "priority": r.priority,
                "is_active": r.is_active,
            }
            for r in supervisor.rules.rules
        ]
        self.write_json({"rules": rules, "count": len(rules)})


# ═══════════════════════════════════════════════════════════
# AI ASSISTANT ENDPOINTS
# ═══════════════════════════════════════════════════════════

class AssistantReviewHandler(SupervisorBaseHandler):
    """POST /api/supervisor/assistant/review — AI assistant review summary."""

    def post(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return
        body = self.get_json_body()
        pipeline_id = body.get("pipeline_id")

        if not pipeline_id:
            return self.write_error_json(400, "pipeline_id is required")

        result = _pipeline_cache.get(pipeline_id)
        if not result:
            return self.write_error_json(404, "Pipeline not found")

        assistant = _assistant or ComplianceAssistant()
        summary = assistant.generate_review_summary(
            pipeline_result=result,
            client_data=body.get("client_data"),
        )

        self.write_json({
            "summary": summary.model_dump(),
            "llm_context": assistant.build_llm_context(
                pipeline_result=result,
                client_data=body.get("client_data"),
            ) if body.get("include_llm_context") else None,
            "system_prompt": assistant.get_system_prompt() if body.get("include_system_prompt") else None,
        })


# ═══════════════════════════════════════════════════════════
# URL PATTERNS — Register with Tornado app
# ═══════════════════════════════════════════════════════════

def get_supervisor_routes():
    """Return list of (pattern, handler) tuples for Tornado app."""
    return [
        (r"/api/supervisor/pipeline/run", PipelineRunHandler),
        (r"/api/supervisor/pipeline/([^/]+)", PipelineDetailHandler),
        (r"/api/supervisor/pipeline/([^/]+)/review", PipelineReviewPackageHandler),
        (r"/api/supervisor/review", ReviewSubmitHandler),
        (r"/api/supervisor/reviews", ReviewListHandler),
        (r"/api/supervisor/escalate", EscalationHandler),
        (r"/api/supervisor/escalations", EscalationListHandler),
        (r"/api/supervisor/overrides", OverrideListHandler),
        (r"/api/supervisor/audit", AuditLogHandler),
        (r"/api/supervisor/audit/verify", AuditVerifyHandler),
        (r"/api/supervisor/stats", StatsHandler),
        (r"/api/supervisor/dashboard", DashboardHandler),
        (r"/api/supervisor/rules", RulesListHandler),
        (r"/api/supervisor/assistant/review", AssistantReviewHandler),
    ]
