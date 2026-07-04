"""
Scheduled monitoring automation for due periodic reviews.

PR4 deliberately does not add a second cadence policy. Review cadence is
already persisted on ``periodic_reviews`` by ``monitoring_enrollment`` using
``periodic_review_policy``. This module only consumes due review rows and
starts the existing periodic-review operating model without an officer click.

Provider posture:
* no screening provider imports
* no ComplyAdvantage/Sumsub activation or provider selection changes
* document-health alert sync is reused through periodic_review_engine
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4

import periodic_review_engine as pre

logger = logging.getLogger("arie.monitoring_automation")

AUTOMATION_AGENT_NAME = "Monitoring Automation Scheduler"
AUTOMATION_AGENT_TYPE = "periodic_review_automation"
AUTOMATION_RUN_FREQUENCY = "Automatic due-review sweep"
SYSTEM_USER = {
    "sub": "system-monitoring-automation",
    "name": "Monitoring Automation",
    "role": "system",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _utc_now()).isoformat(timespec="seconds")


def _row_get(row: Any, key: str, default=None):
    if row is None:
        return default
    try:
        if key in row.keys():
            value = row[key]
            return default if value is None else value
    except Exception:
        pass
    if isinstance(row, dict):
        value = row.get(key, default)
        return default if value is None else value
    return default


def _row_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {key: row[key] for key in row.keys()}


def _fetchall(db, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    rows = db.execute(sql, params).fetchall()
    return [_row_dict(row) for row in rows]


def _fetchone(db, sql: str, params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    row = db.execute(sql, params).fetchone()
    return _row_dict(row) if row is not None else None


def _rowcount(result: Any) -> int:
    cursor = getattr(result, "_cursor", result)
    try:
        return int(cursor.rowcount)
    except Exception:
        return 0


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _detail(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, default=str, sort_keys=True)


def system_audit_writer(
    user,
    action,
    target,
    detail,
    db=None,
    before_state=None,
    after_state=None,
    commit=False,
):
    """Audit writer compatible with BaseHandler.log_audit.

    Used by the scheduler because there is no request handler context. The
    payload is deliberately workflow metadata only: IDs, refs, states, counts
    and policy fields, never raw document/PII content.
    """
    if db is None:
        raise RuntimeError("system_audit_writer requires db")
    actor = dict(user or SYSTEM_USER)
    db.execute(
        """
        INSERT INTO audit_log
            (user_id, user_name, user_role, action, target, detail,
             ip_address, before_state, after_state)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor.get("sub", SYSTEM_USER["sub"]),
            actor.get("name", SYSTEM_USER["name"]),
            actor.get("role", SYSTEM_USER["role"]),
            action,
            target,
            str(detail),
            "system",
            json.dumps(before_state, default=str, sort_keys=True) if before_state is not None else None,
            json.dumps(after_state, default=str, sort_keys=True) if after_state is not None else None,
        ),
    )
    if commit:
        db.commit()


def _emit_audit(db, action: str, target: str, payload: Dict[str, Any], *,
                before_state=None, after_state=None) -> None:
    system_audit_writer(
        SYSTEM_USER,
        action,
        target,
        _detail(payload),
        db=db,
        before_state=before_state,
        after_state=after_state,
    )


def automation_interval_seconds(default: int = 3600) -> int:
    try:
        configured = int(os.environ.get("MONITORING_AUTOMATION_INTERVAL_SECONDS", str(default)))
    except (TypeError, ValueError):
        configured = default
    return max(60, configured)


def _next_run_iso(now: datetime, interval_seconds: Optional[int] = None) -> str:
    seconds = interval_seconds or automation_interval_seconds()
    return _iso(now + timedelta(seconds=seconds))


def _fetch_agent_status_row(db) -> Optional[Dict[str, Any]]:
    return _fetchone(
        db,
        """
        SELECT * FROM monitoring_agent_status
         WHERE agent_type = ? OR agent_name = ?
         ORDER BY id ASC
         LIMIT 1
        """,
        (AUTOMATION_AGENT_TYPE, AUTOMATION_AGENT_NAME),
    )


def _virtual_agent_status_row() -> Dict[str, Any]:
    return {
        "id": AUTOMATION_AGENT_TYPE,
        "agent_name": AUTOMATION_AGENT_NAME,
        "agent_type": AUTOMATION_AGENT_TYPE,
        "last_run": None,
        "next_run": None,
        "run_frequency": AUTOMATION_RUN_FREQUENCY,
        "clients_monitored": 0,
        "alerts_generated": 0,
        "status": "active",
    }


def _ensure_agent_status_row(db) -> Dict[str, Any]:
    row = _fetch_agent_status_row(db)
    if row:
        db.execute(
            """
            UPDATE monitoring_agent_status
               SET agent_name = ?,
                   agent_type = ?,
                   run_frequency = COALESCE(run_frequency, ?),
                   status = CASE
                       WHEN status IS NULL OR status = '' OR status IN ('inactive','disabled')
                       THEN 'active'
                       ELSE status
                   END
             WHERE id = ?
            """,
            (AUTOMATION_AGENT_NAME, AUTOMATION_AGENT_TYPE, AUTOMATION_RUN_FREQUENCY, row["id"]),
        )
        return _fetchone(db, "SELECT * FROM monitoring_agent_status WHERE id = ?", (row["id"],)) or row
    db.execute(
        """
        INSERT INTO monitoring_agent_status
            (agent_name, agent_type, last_run, next_run, run_frequency,
             clients_monitored, alerts_generated, status)
        VALUES (?, ?, NULL, NULL, ?, 0, 0, 'active')
        """,
        (AUTOMATION_AGENT_NAME, AUTOMATION_AGENT_TYPE, AUTOMATION_RUN_FREQUENCY),
    )
    db.commit()
    return _fetchone(
        db,
        """
        SELECT * FROM monitoring_agent_status
         WHERE agent_type = ?
         ORDER BY id ASC
         LIMIT 1
        """,
        (AUTOMATION_AGENT_TYPE,),
    ) or {}


def _update_agent_status(
    db,
    *,
    last_run: datetime,
    next_run: datetime,
    due_count: int,
    processed: int,
    failed: int,
    alerts_generated_delta: int,
) -> None:
    row = _ensure_agent_status_row(db)
    status = "degraded" if failed else "active"
    db.execute(
        """
        UPDATE monitoring_agent_status
           SET last_run = ?,
               next_run = ?,
               run_frequency = ?,
               clients_monitored = ?,
               alerts_generated = COALESCE(alerts_generated, 0) + ?,
               status = ?
         WHERE id = ?
        """,
        (
            _iso(last_run),
            _iso(next_run),
            AUTOMATION_RUN_FREQUENCY,
            processed,
            alerts_generated_delta,
            status,
            row.get("id"),
        ),
    )
    db.commit()


def due_review_candidates(
    db,
    *,
    today: Optional[date] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return pending, due, approved-client review rows.

    Date filtering is intentionally done in Python so this remains portable
    across SQLite and PostgreSQL without adding dialect-specific casts.
    """
    today = today or _utc_now().date()
    rows = _fetchall(
        db,
        """
        SELECT pr.*,
               a.ref AS application_ref,
               a.status AS application_status,
               a.is_fixture AS application_is_fixture
          FROM periodic_reviews pr
          JOIN applications a ON a.id = pr.application_id
         WHERE COALESCE(pr.status, 'pending') = 'pending'
           AND a.status = 'approved'
         ORDER BY pr.due_date ASC, pr.id ASC
        """,
    )
    due = []
    for row in rows:
        if _truthy(row.get("application_is_fixture")):
            continue
        due_date = _parse_date(row.get("due_date") or row.get("next_review_date"))
        if due_date is None or due_date > today:
            continue
        due.append(row)
        if limit and len(due) >= limit:
            break
    return due


def _alert_count_for_application(db, application_id: Any) -> int:
    if not application_id:
        return 0
    row = _fetchone(
        db,
        """
        SELECT COUNT(*) AS c
          FROM monitoring_alerts
         WHERE application_id = ?
           AND COALESCE(status, 'open') NOT IN
               ('resolved','dismissed','closed','routed_to_review','routed_to_edd')
        """,
        (application_id,),
    )
    return int((row or {}).get("c") or 0)


def _claim_review(db, review_id: Any, ts: str) -> bool:
    result = db.execute(
        """
        UPDATE periodic_reviews
           SET status = 'in_progress',
               started_at = COALESCE(started_at, ?),
               state_changed_at = ?
         WHERE id = ?
           AND COALESCE(status, 'pending') = 'pending'
        """,
        (ts, ts, review_id),
    )
    db.commit()
    return _rowcount(result) == 1


def _restore_pending_after_failure(db, review_id: Any, ts: str) -> None:
    db.execute(
        """
        UPDATE periodic_reviews
           SET status = 'pending',
               state_changed_at = ?
         WHERE id = ?
           AND status = 'in_progress'
           AND required_items_generated_at IS NULL
        """,
        (ts, review_id),
    )
    db.commit()


def run_due_monitoring_reviews(
    db,
    *,
    now: Optional[datetime] = None,
    max_reviews: Optional[int] = None,
    audit_writer=system_audit_writer,
) -> Dict[str, Any]:
    """Run the scheduled due-review automation once.

    The runner is idempotent by claim: only reviews still in ``pending`` can be
    claimed. Once claimed, the review moves to ``in_progress`` and receives
    required items through the existing periodic-review engine. A later sweep
    will not process the same interval again unless an operator intentionally
    moves the review back to a due pending state.
    """
    if audit_writer is None:
        raise RuntimeError("monitoring automation requires audit_writer")

    now = now or _utc_now()
    max_reviews = max_reviews or int(os.environ.get("MONITORING_AUTOMATION_BATCH_SIZE", "25"))
    max_reviews = max(1, min(max_reviews, 100))
    run_id = str(uuid4())
    interval = automation_interval_seconds()
    _ensure_agent_status_row(db)
    candidates = due_review_candidates(db, today=now.date(), limit=max_reviews)

    summary = {
        "run_id": run_id,
        "run_source": "scheduled_automation",
        "due_count": len(candidates),
        "processed": 0,
        "skipped": 0,
        "failed": 0,
        "alerts_generated_delta": 0,
        "review_ids": [],
        "failures": [],
        "policy_source": "periodic_reviews_from_periodic_review_policy_v2",
    }
    _emit_audit(
        db,
        "monitoring.automation.run_started",
        "monitoring_automation",
        {
            "run_id": run_id,
            "due_count": len(candidates),
            "batch_size": max_reviews,
            "policy_source": summary["policy_source"],
        },
    )
    db.commit()

    for review in candidates:
        review_id = review.get("id")
        app_id = review.get("application_id")
        app_ref = review.get("application_ref") or app_id
        claim_ts = _iso(now)
        if not _claim_review(db, review_id, claim_ts):
            summary["skipped"] += 1
            continue

        before_state = {
            "status": "pending",
            "due_date": review.get("due_date"),
            "next_review_date": review.get("next_review_date"),
            "policy_version": review.get("policy_version"),
            "frequency_months": review.get("frequency_months"),
            "calculation_basis": review.get("calculation_basis"),
        }
        after_claim = _fetchone(db, "SELECT * FROM periodic_reviews WHERE id = ?", (review_id,))
        after_state = {
            "status": (after_claim or {}).get("status"),
            "started_at": (after_claim or {}).get("started_at"),
            "state_changed_at": (after_claim or {}).get("state_changed_at"),
        }
        _emit_audit(
            db,
            "monitoring.automation.review_started",
            f"periodic_review:{review_id}",
            {
                "run_id": run_id,
                "application_id": app_id,
                "application_ref": app_ref,
                "due_date": review.get("due_date"),
                "next_review_date": review.get("next_review_date"),
                "policy_version": review.get("policy_version"),
                "frequency_months": review.get("frequency_months"),
                "calculation_basis": review.get("calculation_basis"),
                "source_agents": [6, 7, 8],
                "source_agent_note": (
                    "Agent 6 due-review preparation is actuated; Agent 7/8 "
                    "stored monitoring signals remain provider-agnostic inputs."
                ),
            },
            before_state=before_state,
            after_state=after_state,
        )
        db.commit()

        before_alerts = _alert_count_for_application(db, app_id)
        try:
            items = pre.generate_required_items(
                db,
                review_id,
                user=SYSTEM_USER,
                audit_writer=audit_writer,
            )
            after_alerts = _alert_count_for_application(db, app_id)
            delta = max(0, after_alerts - before_alerts)
            summary["alerts_generated_delta"] += delta
            summary["processed"] += 1
            summary["review_ids"].append(review_id)
            _emit_audit(
                db,
                "monitoring.automation.review_processed",
                f"periodic_review:{review_id}",
                {
                    "run_id": run_id,
                    "application_id": app_id,
                    "application_ref": app_ref,
                    "required_items_count": len(items),
                    "required_item_codes": sorted({item.get("code") for item in items}),
                    "alerts_generated_delta": delta,
                    "policy_version": review.get("policy_version"),
                    "frequency_months": review.get("frequency_months"),
                    "calculation_basis": review.get("calculation_basis"),
                    "duplicate_interval_guard": "pending_to_in_progress_claim",
                },
            )
            db.commit()
        except Exception as exc:
            summary["failed"] += 1
            failure = {
                "review_id": review_id,
                "application_id": app_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            summary["failures"].append(failure)
            logger.exception("monitoring automation failed review_id=%s", review_id)
            try:
                db.rollback()
            except Exception:
                pass
            try:
                _restore_pending_after_failure(db, review_id, _iso())
                _emit_audit(
                    db,
                    "monitoring.automation.review_failed",
                    f"periodic_review:{review_id}",
                    {
                        **failure,
                        "run_id": run_id,
                        "retriable": True,
                        "state_restored": "pending",
                    },
                    after_state={"status": "pending"},
                )
                db.commit()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass

    next_run_dt = now + timedelta(seconds=interval)
    _update_agent_status(
        db,
        last_run=now,
        next_run=next_run_dt,
        due_count=len(candidates),
        processed=summary["processed"],
        failed=summary["failed"],
        alerts_generated_delta=summary["alerts_generated_delta"],
    )
    _emit_audit(
        db,
        "monitoring.automation.run_completed",
        "monitoring_automation",
        {
            **summary,
            "next_run": _iso(next_run_dt),
        },
    )
    db.commit()
    return summary


def automation_status(db, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _utc_now()
    row = _fetch_agent_status_row(db) or _virtual_agent_status_row()
    due_count = len(due_review_candidates(db, today=now.date()))
    return {
        "agent": row,
        "agent_persisted": bool(row.get("id") != AUTOMATION_AGENT_TYPE),
        "due_count": due_count,
        "policy_source": "periodic_reviews_from_periodic_review_policy_v2",
        "enabled_default": "staging_production",
        "interval_seconds": automation_interval_seconds(),
        "timestamp": _iso(now),
    }


def automation_enabled(environment: Optional[str] = None) -> bool:
    env_value = os.environ.get("MONITORING_AUTOMATION_ENABLED")
    if env_value is not None:
        return _truthy(env_value)
    # Canonicalized (audit H8 / PR-13): the raw set covered "prod" but not
    # "stage", so ENVIRONMENT=stage ran as staging everywhere else while the
    # due-review scheduler silently stayed off.
    from environment import canonicalize_environment
    return canonicalize_environment(
        environment or os.environ.get("ENVIRONMENT") or os.environ.get("ENV")
    ) in {"staging", "production"}


__all__ = [
    "AUTOMATION_AGENT_NAME",
    "AUTOMATION_AGENT_TYPE",
    "SYSTEM_USER",
    "automation_enabled",
    "automation_interval_seconds",
    "automation_status",
    "due_review_candidates",
    "run_due_monitoring_reviews",
    "system_audit_writer",
]
