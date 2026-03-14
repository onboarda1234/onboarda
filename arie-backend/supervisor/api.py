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
from typing import Any, Dict, Optional

import tornado.web

from .compliance_assistant import ComplianceAssistant
from .human_review import HumanReviewService
from .schemas import ReviewDecision, TriggerType
from .supervisor import AgentSupervisor, SupervisorPipelineResult

logger = logging.getLogger("arie.supervisor.api")


# ═══════════════════════════════════════════════════════════
# GLOBAL INSTANCES (initialized by setup_supervisor)
# ═══════════════════════════════════════════════════════════
_supervisor: Optional[AgentSupervisor] = None
_review_service: Optional[HumanReviewService] = None
_assistant: Optional[ComplianceAssistant] = None
_pipeline_cache: Dict[str, SupervisorPipelineResult] = {}


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
    """Base handler with common utilities."""

    def set_default_headers(self):
        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def options(self, *args, **kwargs):
        self.set_status(204)
        self.finish()

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

        supervisor = get_supervisor()
        result = await supervisor.run_pipeline(
            application_id=application_id,
            trigger_type=tt,
            context_data=body.get("context"),
            trigger_source=trigger_source,
        )

        # Cache for retrieval
        _pipeline_cache[result.pipeline_id] = result

        self.write_json(result.to_dict())


class PipelineDetailHandler(SupervisorBaseHandler):
    """GET /api/supervisor/pipeline/:id — Get pipeline results."""

    def get(self, pipeline_id: str):
        result = _pipeline_cache.get(pipeline_id)
        if not result:
            return self.write_error_json(404, "Pipeline not found")
        self.write_json(result.to_dict())


class PipelineReviewPackageHandler(SupervisorBaseHandler):
    """GET /api/supervisor/pipeline/:id/review — Get review package for officer."""

    def get(self, pipeline_id: str):
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
        supervisor = get_supervisor()
        self.write_json(supervisor.get_stats())


class DashboardHandler(SupervisorBaseHandler):
    """GET /api/supervisor/dashboard — Governance dashboard data."""

    def get(self):
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
