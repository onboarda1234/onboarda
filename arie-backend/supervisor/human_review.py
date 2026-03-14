"""
ARIE Finance — AI Agent Supervisor: Human Review Service
==========================================================
Manages the officer review workflow:
  - Present agent outputs, confidence, contradictions, rules
  - Accept officer decisions
  - Track overrides with mandatory reason/role/timestamp
  - Route escalations to appropriate level
  - Maintain complete audit trail

Every override requires:
  - override_reason
  - officer_name
  - officer_role
  - timestamp
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .audit import AuditLogger
from .schemas import (
    AuditEventType,
    EscalationLevel,
    HumanReview,
    Override,
    OverrideType,
    ReviewDecision,
    Severity,
)
from .supervisor import SupervisorPipelineResult

logger = logging.getLogger("arie.supervisor.human_review")


class HumanReviewService:
    """
    Manages the human-in-the-loop review workflow.
    """

    def __init__(self, db_path: Optional[str] = None, audit_logger: Optional[AuditLogger] = None):
        self.db_path = db_path
        self.audit = audit_logger or AuditLogger(db_path=db_path)

        if db_path:
            self._init_db()

    def _init_db(self):
        """Create review-related tables if needed."""
        db = sqlite3.connect(self.db_path)
        db.executescript("""
            CREATE TABLE IF NOT EXISTS supervisor_human_reviews (
                id TEXT PRIMARY KEY,
                pipeline_id TEXT NOT NULL,
                application_id TEXT NOT NULL,
                escalation_id TEXT,
                review_type TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                reviewer_name TEXT NOT NULL,
                reviewer_role TEXT NOT NULL,
                ai_recommendation TEXT,
                ai_confidence REAL,
                ai_risk_level TEXT,
                rules_recommendation TEXT,
                rules_triggered TEXT DEFAULT '[]',
                contradictions_json TEXT DEFAULT '[]',
                decision TEXT NOT NULL,
                decision_reason TEXT NOT NULL,
                risk_level_assigned TEXT,
                conditions TEXT,
                follow_up_required INTEGER DEFAULT 0,
                follow_up_details TEXT,
                is_ai_override INTEGER DEFAULT 0,
                override_reason TEXT,
                review_started_at TEXT,
                decision_at TEXT DEFAULT (datetime('now')),
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS supervisor_overrides (
                id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL,
                application_id TEXT NOT NULL,
                agent_type TEXT,
                override_type TEXT NOT NULL,
                original_value TEXT NOT NULL,
                override_value TEXT NOT NULL,
                reason TEXT NOT NULL,
                officer_id TEXT NOT NULL,
                officer_name TEXT NOT NULL,
                officer_role TEXT NOT NULL,
                approver_id TEXT,
                approver_name TEXT,
                approved_at TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS supervisor_escalations (
                id TEXT PRIMARY KEY,
                pipeline_id TEXT NOT NULL,
                application_id TEXT NOT NULL,
                escalation_source TEXT NOT NULL,
                source_id TEXT,
                escalation_level TEXT NOT NULL,
                priority TEXT NOT NULL,
                reason TEXT NOT NULL,
                context_json TEXT DEFAULT '{}',
                assigned_to TEXT,
                status TEXT DEFAULT 'pending',
                sla_deadline TEXT,
                resolved_at TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        db.commit()
        db.close()

    def prepare_review_package(
        self, pipeline_result: SupervisorPipelineResult
    ) -> Dict[str, Any]:
        """
        Prepare a structured review package for the compliance officer.
        Contains everything needed to make an informed decision.
        """
        package = {
            "pipeline_id": pipeline_result.pipeline_id,
            "application_id": pipeline_result.application_id,
            "pipeline_status": pipeline_result.status,
            "requires_review": pipeline_result.requires_human_review,
            "review_reasons": pipeline_result.review_reasons,
            "blocking_issues": pipeline_result.blocking_issues,

            # Case-level summary
            "case_summary": {
                "aggregate_confidence": (
                    pipeline_result.case_aggregate.aggregate_confidence
                    if pipeline_result.case_aggregate else None
                ),
                "confidence_routing": (
                    pipeline_result.case_aggregate.confidence_routing.value
                    if pipeline_result.case_aggregate and pipeline_result.case_aggregate.confidence_routing
                    else None
                ),
                "total_agents": len(pipeline_result.agent_outputs),
                "failed_agents": len(pipeline_result.failed_agents),
                "contradictions": len(pipeline_result.contradictions),
                "rules_triggered": sum(
                    1 for r in pipeline_result.rule_evaluations if r.triggered
                ),
                "escalation_level": (
                    pipeline_result.case_aggregate.escalation_level.value
                    if pipeline_result.case_aggregate and pipeline_result.case_aggregate.escalation_level
                    else None
                ),
            },

            # Agent outputs (summarized for officer)
            "agent_results": [
                {
                    "agent_type": agent_type.value,
                    "agent_name": output.agent_name,
                    "status": output.status.value,
                    "confidence": output.confidence_score,
                    "findings_count": len(output.findings),
                    "issues_count": len(output.detected_issues),
                    "risk_indicators_count": len(output.risk_indicators),
                    "escalation_flag": output.escalation_flag,
                    "escalation_reason": output.escalation_reason,
                    "recommendation": output.recommendation,
                    "key_findings": [
                        {
                            "title": f.title,
                            "severity": f.severity.value,
                            "description": f.description[:200],
                        }
                        for f in output.findings[:10]
                    ],
                    "key_issues": [
                        {
                            "title": i.title,
                            "severity": i.severity.value,
                            "blocking": i.blocking,
                            "description": i.description[:200],
                        }
                        for i in output.detected_issues[:10]
                    ],
                }
                for agent_type, output in pipeline_result.agent_outputs.items()
            ],

            # Contradictions
            "contradictions": [
                {
                    "id": c.contradiction_id,
                    "category": c.contradiction_category.value,
                    "severity": c.severity.value,
                    "severity_score": c.severity_score,
                    "agent_a": c.agent_a_type.value,
                    "agent_a_finding": c.agent_a_finding,
                    "agent_b": c.agent_b_type.value,
                    "agent_b_finding": c.agent_b_finding,
                    "description": c.description,
                    "resolution_required": c.resolution_required,
                }
                for c in pipeline_result.contradictions
            ],

            # Rules triggered
            "rules_triggered": [
                {
                    "rule_name": r.rule_name,
                    "rule_category": r.rule_category,
                    "action": r.action_taken.value if r.action_taken else None,
                    "severity": r.severity.value if r.severity else None,
                    "overrides_ai": r.overrides_ai,
                    "trigger_data": r.trigger_data,
                }
                for r in pipeline_result.rule_evaluations if r.triggered
            ],

            # Escalations
            "escalations": [
                {
                    "id": e.escalation_id,
                    "source": e.escalation_source,
                    "level": e.escalation_level.value,
                    "priority": e.priority.value,
                    "reason": e.reason,
                }
                for e in pipeline_result.escalations
            ],

            # Failed agents
            "failed_agents": pipeline_result.failed_agents,

            # Available actions
            "available_actions": [
                "approve", "reject", "request_information",
                "escalate", "enhanced_monitoring", "defer"
            ],
        }

        return package

    def submit_review(
        self,
        pipeline_result: SupervisorPipelineResult,
        reviewer_id: str,
        reviewer_name: str,
        reviewer_role: str,
        decision: str,
        decision_reason: str,
        risk_level_assigned: Optional[str] = None,
        conditions: Optional[str] = None,
        follow_up_required: bool = False,
        follow_up_details: Optional[str] = None,
        override_ai: bool = False,
        override_reason: Optional[str] = None,
    ) -> HumanReview:
        """
        Submit an officer review decision.

        All overrides require a reason.
        """
        review_decision = ReviewDecision(decision)

        # Determine if this is an AI override
        ai_recommendation = None
        if pipeline_result.case_aggregate:
            ai_recommendation = pipeline_result.case_aggregate.ai_recommendation

        is_override = override_ai
        if not is_override and ai_recommendation:
            # Auto-detect override: if officer decision contradicts AI recommendation
            if (review_decision == ReviewDecision.APPROVE and
                    "reject" in (ai_recommendation or "").lower()):
                is_override = True
            elif (review_decision == ReviewDecision.REJECT and
                    "approv" in (ai_recommendation or "").lower()):
                is_override = True

        if is_override and not override_reason:
            raise ValueError("override_reason is required when overriding AI recommendation")

        review = HumanReview(
            review_id=str(uuid4()),
            pipeline_id=pipeline_result.pipeline_id,
            application_id=pipeline_result.application_id,
            review_type="onboarding_decision",
            reviewer_id=reviewer_id,
            reviewer_name=reviewer_name,
            reviewer_role=reviewer_role,
            ai_recommendation=ai_recommendation,
            ai_confidence=(
                pipeline_result.case_aggregate.aggregate_confidence
                if pipeline_result.case_aggregate else None
            ),
            ai_risk_level=(
                pipeline_result.case_aggregate.ai_risk_level
                if pipeline_result.case_aggregate else None
            ),
            rules_triggered=[
                r.rule_name for r in pipeline_result.rule_evaluations if r.triggered
            ],
            contradictions=[
                {"id": c.contradiction_id, "category": c.contradiction_category.value}
                for c in pipeline_result.contradictions
            ],
            decision=review_decision,
            decision_reason=decision_reason,
            risk_level_assigned=risk_level_assigned,
            conditions=conditions,
            follow_up_required=follow_up_required,
            follow_up_details=follow_up_details,
            is_ai_override=is_override,
            override_reason=override_reason,
        )

        # Persist
        if self.db_path:
            self._persist_review(review)

        # Audit log
        self.audit.log_human_review(
            review_id=review.review_id,
            application_id=review.application_id,
            pipeline_id=review.pipeline_id,
            reviewer_name=reviewer_name,
            reviewer_role=reviewer_role,
            decision=decision,
            is_override=is_override,
        )

        # Create override record if applicable
        if is_override:
            override = Override(
                review_id=review.review_id,
                application_id=review.application_id,
                override_type=(
                    OverrideType.APPROVAL_DESPITE_ESCALATION
                    if review_decision == ReviewDecision.APPROVE
                    else OverrideType.REJECTION_DESPITE_APPROVAL
                ),
                original_value=ai_recommendation or "N/A",
                override_value=decision,
                reason=override_reason or decision_reason,
                officer_id=reviewer_id,
                officer_name=reviewer_name,
                officer_role=reviewer_role,
            )
            if self.db_path:
                self._persist_override(override)

            self.audit.log_override(
                override_id=override.override_id,
                application_id=review.application_id,
                officer_name=reviewer_name,
                officer_role=reviewer_role,
                override_type=override.override_type.value,
                original_value=override.original_value,
                override_value=override.override_value,
                reason=override.reason,
            )

        logger.info(
            "Review submitted: app=%s decision=%s reviewer=%s override=%s",
            review.application_id, decision, reviewer_name, is_override
        )

        return review

    def escalate_case(
        self,
        application_id: str,
        pipeline_id: str,
        escalation_level: str,
        reason: str,
        escalated_by: str,
        escalated_by_role: str,
        assigned_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Manually escalate a case to a higher review level."""
        escalation_id = str(uuid4())
        level = EscalationLevel(escalation_level)

        if self.db_path:
            db = sqlite3.connect(self.db_path)
            db.execute("""
                INSERT INTO supervisor_escalations
                (id, pipeline_id, application_id, escalation_source,
                 escalation_level, priority, reason, assigned_to, status)
                VALUES (?, ?, ?, 'manual', ?, 'high', ?, ?, 'pending')
            """, (escalation_id, pipeline_id, application_id,
                  level.value, reason, assigned_to))
            db.commit()
            db.close()

        self.audit.log(
            event_type=AuditEventType.ESCALATION_CREATED,
            action=f"Manual escalation to {escalation_level}",
            detail=f"Reason: {reason}",
            severity=Severity.WARNING,
            pipeline_id=pipeline_id,
            application_id=application_id,
            actor_type="officer",
            actor_name=escalated_by,
            actor_role=escalated_by_role,
            data={
                "escalation_id": escalation_id,
                "level": escalation_level,
                "reason": reason,
                "assigned_to": assigned_to,
            },
        )

        return {
            "escalation_id": escalation_id,
            "level": escalation_level,
            "status": "pending",
        }

    # ─── Persistence ──────────────────────────────────────

    def _persist_review(self, review: HumanReview):
        try:
            db = sqlite3.connect(self.db_path)
            db.execute("""
                INSERT INTO supervisor_human_reviews
                (id, pipeline_id, application_id, escalation_id, review_type,
                 reviewer_id, reviewer_name, reviewer_role,
                 ai_recommendation, ai_confidence, ai_risk_level,
                 rules_recommendation, rules_triggered, contradictions_json,
                 decision, decision_reason, risk_level_assigned, conditions,
                 follow_up_required, follow_up_details,
                 is_ai_override, override_reason, decision_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                review.review_id, review.pipeline_id, review.application_id,
                review.escalation_id, review.review_type,
                review.reviewer_id, review.reviewer_name, review.reviewer_role,
                review.ai_recommendation, review.ai_confidence, review.ai_risk_level,
                review.rules_recommendation,
                json.dumps(review.rules_triggered),
                json.dumps(review.contradictions),
                review.decision.value, review.decision_reason,
                review.risk_level_assigned, review.conditions,
                int(review.follow_up_required), review.follow_up_details,
                int(review.is_ai_override), review.override_reason,
                review.decision_at,
            ))
            db.commit()
            db.close()
        except Exception as e:
            logger.error("Failed to persist review %s: %s", review.review_id, e)

    def _persist_override(self, override: Override):
        try:
            db = sqlite3.connect(self.db_path)
            db.execute("""
                INSERT INTO supervisor_overrides
                (id, review_id, application_id, agent_type, override_type,
                 original_value, override_value, reason,
                 officer_id, officer_name, officer_role)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                override.override_id, override.review_id, override.application_id,
                override.agent_type.value if override.agent_type else None,
                override.override_type.value,
                override.original_value, override.override_value, override.reason,
                override.officer_id, override.officer_name, override.officer_role,
            ))
            db.commit()
            db.close()
        except Exception as e:
            logger.error("Failed to persist override %s: %s", override.override_id, e)

    # ─── Query ──────────────────────────────────────────

    def get_reviews(
        self,
        application_id: Optional[str] = None,
        reviewer_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Query review history."""
        if not self.db_path:
            return []

        try:
            db = sqlite3.connect(self.db_path)
            db.row_factory = sqlite3.Row

            query = "SELECT * FROM supervisor_human_reviews WHERE 1=1"
            params = []
            if application_id:
                query += " AND application_id = ?"
                params.append(application_id)
            if reviewer_id:
                query += " AND reviewer_id = ?"
                params.append(reviewer_id)
            query += " ORDER BY decision_at DESC LIMIT ?"
            params.append(limit)

            rows = db.execute(query, params).fetchall()
            db.close()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error("Failed to query reviews: %s", e)
            return []

    def get_overrides(
        self,
        application_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Query override history."""
        if not self.db_path:
            return []

        try:
            db = sqlite3.connect(self.db_path)
            db.row_factory = sqlite3.Row

            query = "SELECT * FROM supervisor_overrides WHERE 1=1"
            params = []
            if application_id:
                query += " AND application_id = ?"
                params.append(application_id)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            rows = db.execute(query, params).fetchall()
            db.close()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error("Failed to query overrides: %s", e)
            return []

    def get_pending_escalations(
        self,
        escalation_level: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Query pending escalations."""
        if not self.db_path:
            return []

        try:
            db = sqlite3.connect(self.db_path)
            db.row_factory = sqlite3.Row

            query = "SELECT * FROM supervisor_escalations WHERE status = 'pending'"
            params = []
            if escalation_level:
                query += " AND escalation_level = ?"
                params.append(escalation_level)
            query += " ORDER BY created_at ASC LIMIT ?"
            params.append(limit)

            rows = db.execute(query, params).fetchall()
            db.close()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error("Failed to query escalations: %s", e)
            return []
