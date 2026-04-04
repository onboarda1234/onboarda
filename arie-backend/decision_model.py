"""
Decision Record Layer — Normalized decision records for compliance audit trail.

Provides a reusable DecisionRecord structure that captures all decision metadata
across the platform (ApplicationDecisionHandler, supervisor flow, pre-approval).
This does NOT replace existing decision logic — it normalizes existing data into
a structured, queryable format.
"""

import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Decision Record Data Structure
# ============================================================================

VALID_DECISION_TYPES = (
    "approve",
    "reject",
    "escalate_edd",
    "request_documents",
    "pre_approve",
    "request_info",
)

VALID_SOURCES = (
    "manual",
    "supervisor",
    "rule_engine",
)

VALID_RISK_LEVELS = (
    "LOW",
    "MEDIUM",
    "HIGH",
    "VERY_HIGH",
)


def build_decision_record(
    application_ref: str,
    decision_type: str,
    source: str,
    actor: Dict[str, str],
    risk_level: Optional[str] = None,
    confidence_score: Optional[float] = None,
    key_flags: Optional[List[str]] = None,
    override_flag: bool = False,
    override_reason: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build a normalized decision record dict from decision parameters.

    Args:
        application_ref: Application reference (e.g. "ARF-2026-0001")
        decision_type: One of VALID_DECISION_TYPES
        source: One of VALID_SOURCES ("manual", "supervisor", "rule_engine")
        actor: Dict with "user_id" and "role" keys
        risk_level: Risk level at time of decision (LOW/MEDIUM/HIGH/VERY_HIGH)
        confidence_score: AI confidence score if available (0.0 - 1.0), else None
        key_flags: List of flags/tags relevant to this decision
        override_flag: Whether the decision overrides AI recommendation
        override_reason: Reason for override (required if override_flag is True)
        extra: Additional metadata to store alongside the record

    Returns:
        Dict representing the normalized decision record.
    """
    if decision_type not in VALID_DECISION_TYPES:
        raise ValueError(
            f"Invalid decision_type '{decision_type}'. "
            f"Must be one of: {', '.join(VALID_DECISION_TYPES)}"
        )

    if source not in VALID_SOURCES:
        raise ValueError(
            f"Invalid source '{source}'. "
            f"Must be one of: {', '.join(VALID_SOURCES)}"
        )

    if risk_level is not None and risk_level not in VALID_RISK_LEVELS:
        raise ValueError(
            f"Invalid risk_level '{risk_level}'. "
            f"Must be one of: {', '.join(VALID_RISK_LEVELS)}"
        )

    if override_flag and not override_reason:
        raise ValueError("override_reason is required when override_flag is True")

    if confidence_score is not None:
        if not (0.0 <= confidence_score <= 1.0):
            raise ValueError("confidence_score must be between 0.0 and 1.0")

    record = {
        "decision_id": str(uuid.uuid4()),
        "application_ref": application_ref,
        "decision_type": decision_type,
        "risk_level": risk_level,
        "confidence_score": confidence_score,
        "source": source,
        "actor": {
            "user_id": actor.get("user_id", ""),
            "role": actor.get("role", ""),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "key_flags": key_flags or [],
        "override_flag": override_flag,
        "override_reason": override_reason if override_flag else None,
    }

    if extra:
        record["extra"] = extra

    return record


# ============================================================================
# Persistence Helpers
# ============================================================================

def save_decision_record(db, record: Dict[str, Any]) -> str:
    """
    Persist a decision record to the decision_records table.

    Args:
        db: Database connection (DBConnection from db.py)
        record: Decision record dict (from build_decision_record)

    Returns:
        The decision_id of the saved record.
    """
    decision_id = record["decision_id"]
    try:
        db.execute(
            """INSERT INTO decision_records
               (id, application_ref, decision_type, risk_level, confidence_score,
                source, actor_user_id, actor_role, timestamp, key_flags,
                override_flag, override_reason, extra_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision_id,
                record["application_ref"],
                record["decision_type"],
                record.get("risk_level"),
                record.get("confidence_score"),
                record["source"],
                record["actor"]["user_id"],
                record["actor"]["role"],
                record["timestamp"],
                json.dumps(record.get("key_flags", [])),
                1 if record.get("override_flag") else 0,
                record.get("override_reason"),
                json.dumps(record.get("extra", {})),
            ),
        )
        logger.info(
            "Decision record saved: %s type=%s app=%s",
            decision_id,
            record["decision_type"],
            record["application_ref"],
        )
    except Exception as e:
        # Non-fatal: decision records are an audit overlay, not a blocking requirement.
        # The original decision flow (status update, audit_log) has already committed.
        logger.error(
            "Failed to save decision record %s for app %s: %s",
            decision_id,
            record["application_ref"],
            e,
        )
    return decision_id


def get_decision_records(
    db,
    application_ref: str,
    decision_type: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Retrieve decision records for an application.

    Args:
        db: Database connection
        application_ref: Application reference to filter by
        decision_type: Optional filter by decision type
        limit: Maximum number of records to return (default 50)

    Returns:
        List of decision record dicts.
    """
    if decision_type:
        rows = db.execute(
            """SELECT * FROM decision_records
               WHERE application_ref = ? AND decision_type = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (application_ref, decision_type, limit),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT * FROM decision_records
               WHERE application_ref = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (application_ref, limit),
        ).fetchall()

    results = []
    for row in rows:
        record = {
            "decision_id": row["id"],
            "application_ref": row["application_ref"],
            "decision_type": row["decision_type"],
            "risk_level": row["risk_level"],
            "confidence_score": row["confidence_score"],
            "source": row["source"],
            "actor": {
                "user_id": row["actor_user_id"],
                "role": row["actor_role"],
            },
            "timestamp": row["timestamp"],
            "key_flags": _safe_json_loads(row["key_flags"]),
            "override_flag": bool(row["override_flag"]),
            "override_reason": row["override_reason"],
        }
        extra = _safe_json_loads(row["extra_json"])
        if extra:
            record["extra"] = extra
        results.append(record)

    return results


# ============================================================================
# Builder: Construct from ApplicationDecisionHandler context
# ============================================================================

def build_from_application_decision(
    app: Dict[str, Any],
    decision: str,
    decision_reason: str,
    user: Dict[str, Any],
    override_ai: bool = False,
    override_reason: str = "",
    supervisor_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build a decision record from the data available in ApplicationDecisionHandler.

    Derives confidence_score from the supervisor result if available,
    and populates key_flags from application context.

    Args:
        app: Application row (dict-like) from DB
        decision: Decision string (approve/reject/escalate_edd/request_documents)
        decision_reason: Human-provided reason
        user: Authenticated user dict (has "sub", "role", "name")
        override_ai: Whether AI recommendation was overridden
        override_reason: Reason for override
        supervisor_result: Optional supervisor result dict (from compliance memo)

    Returns:
        Decision record dict.
    """
    # Derive confidence from supervisor if available
    confidence = None
    if supervisor_result:
        confidence = supervisor_result.get("supervisor_confidence")
        if confidence is not None:
            # Normalize to 0-1 range if stored as percentage
            if confidence > 1.0:
                confidence = confidence / 100.0

    # Build key flags from application context
    key_flags = []
    risk_level = app.get("risk_level")
    if risk_level in ("HIGH", "VERY_HIGH"):
        key_flags.append(f"risk:{risk_level}")
    if override_ai:
        key_flags.append("ai_override")
    if supervisor_result:
        verdict = supervisor_result.get("verdict", "")
        if verdict == "INCONSISTENT":
            key_flags.append("supervisor:inconsistent")
        elif verdict == "CONSISTENT_WITH_WARNINGS":
            key_flags.append("supervisor:warnings")

    return build_decision_record(
        application_ref=app.get("ref", ""),
        decision_type=decision,
        source="manual",
        actor={
            "user_id": user.get("sub", user.get("id", "")),
            "role": user.get("role", ""),
        },
        risk_level=risk_level,
        confidence_score=confidence,
        key_flags=key_flags,
        override_flag=override_ai,
        override_reason=override_reason if override_ai else None,
        extra={
            "decision_reason": decision_reason,
        },
    )


def build_from_supervisor_verdict(
    app: Dict[str, Any],
    supervisor_result: Dict[str, Any],
    user: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Build a decision record from a supervisor pipeline verdict.

    The supervisor does not make final decisions, but its verdict
    (CONSISTENT/INCONSISTENT) is recorded as a decision-layer event.

    Args:
        app: Application row (dict-like) from DB
        supervisor_result: Supervisor result dict with verdict, confidence, etc.
        user: User who triggered the supervisor run

    Returns:
        Decision record dict.
    """
    verdict = supervisor_result.get("verdict", "UNKNOWN")
    confidence = supervisor_result.get("supervisor_confidence")
    if confidence is not None and confidence > 1.0:
        confidence = confidence / 100.0

    contradiction_count = supervisor_result.get("contradiction_count", 0)
    can_approve = supervisor_result.get("can_approve", False)

    key_flags = [f"verdict:{verdict}"]
    if contradiction_count > 0:
        key_flags.append(f"contradictions:{contradiction_count}")
    if not can_approve:
        key_flags.append("approval_blocked")

    # Map supervisor verdict to a decision_type equivalent
    if verdict == "INCONSISTENT":
        decision_type = "reject"
    elif can_approve:
        decision_type = "approve"
    else:
        decision_type = "escalate_edd"

    return build_decision_record(
        application_ref=app.get("ref", ""),
        decision_type=decision_type,
        source="supervisor",
        actor={
            "user_id": user.get("sub", user.get("id", "")),
            "role": user.get("role", ""),
        },
        risk_level=app.get("risk_level"),
        confidence_score=confidence,
        key_flags=key_flags,
        override_flag=False,
        extra={
            "verdict": verdict,
            "contradiction_count": contradiction_count,
            "can_approve": can_approve,
            "recommendation": supervisor_result.get("recommendation", ""),
        },
    )


# ============================================================================
# Internal helpers
# ============================================================================

def _safe_json_loads(value: Any) -> Any:
    """Safely parse JSON, returning empty structure on failure."""
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
