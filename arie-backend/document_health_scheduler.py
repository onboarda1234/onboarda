"""
Document Health Scheduler — staged rollout orchestrator (M3.1).

Makes the existing document_health_monitor detection truly ongoing, safely:

- ``run_document_health_sweep`` supports DRY-RUN (read-only report), a strict
  per-run creation CAP (pre-checked per application via the pure plan, so the
  cap is never exceeded and nothing is silently truncated — the remainder is
  counted and audited), an application SEGMENT allowlist, and a
  fixtures-only scope for hidden Phase-B validation.
- The background scheduler is **off by default everywhere** (including
  staging): it runs only when ``DOCUMENT_HEALTH_SCHEDULER_ENABLED`` is
  explicitly truthy. This deliberately deviates from monitoring_automation's
  staging/production auto-enable because M3.1's whole purpose is staged
  first-sweep backlog control.

Safety posture (test-enforced):
- no provider calls, no emails, no client notifications, no Agent 1 runs,
  no document mutation — the sweep reads applications/documents and writes
  monitoring_alerts (via document_health_monitor), the agent status row, and
  audit_log only.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import document_health_monitor as dhm
from monitoring_automation import system_audit_writer

logger = logging.getLogger("arie.document_health_scheduler")

SCHEDULER_AGENT_NAME = "Document Health Scheduler"
SCHEDULER_AGENT_TYPE = "document_health_scheduler"
SCHEDULER_RUN_FREQUENCY = "Scheduled document health sweep"
SYSTEM_USER = dict(dhm.SYSTEM_USER)

DEFAULT_INTERVAL_SECONDS = 21600  # 6h
MIN_INTERVAL_SECONDS = 300
DEFAULT_MAX_ALERTS_PER_RUN = 50
MAX_MAX_ALERTS_PER_RUN = 500


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _utc_now()).isoformat(timespec="seconds")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _row_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {key: row[key] for key in row.keys()}


def scheduler_enabled() -> bool:
    """Explicit opt-in only. Default OFF in every environment (incl. staging)
    until the staged rollout reaches Phase D — see M3.1 spec."""
    return _truthy(os.environ.get("DOCUMENT_HEALTH_SCHEDULER_ENABLED"))


def scheduler_interval_seconds(default: int = DEFAULT_INTERVAL_SECONDS) -> int:
    try:
        configured = int(os.environ.get("DOCUMENT_HEALTH_INTERVAL_SECONDS", str(default)))
    except (TypeError, ValueError):
        configured = default
    return max(MIN_INTERVAL_SECONDS, configured)


def max_alerts_per_run(default: int = DEFAULT_MAX_ALERTS_PER_RUN) -> int:
    try:
        configured = int(os.environ.get("DOCUMENT_HEALTH_MAX_ALERTS_PER_RUN", str(default)))
    except (TypeError, ValueError):
        configured = default
    return max(1, min(configured, MAX_MAX_ALERTS_PER_RUN))


def configured_segment() -> Optional[List[str]]:
    raw = str(os.environ.get("DOCUMENT_HEALTH_SEGMENT") or "").strip()
    if not raw:
        return None
    ids = [token.strip() for token in raw.split(",") if token.strip()]
    return ids or None


# ── Scope ────────────────────────────────────────────────────────────────────
def sweep_candidate_application_ids(
    db,
    *,
    segment: Optional[List[str]] = None,
    fixtures_only: bool = False,
) -> List[str]:
    """Applications in sweep scope.

    Normal scope: approved, non-fixture (mirrors the officer alert queue).
    fixtures_only: fixture applications regardless of status — used for the
    hidden Phase-B validation sweep, invisible to officer queues.
    A segment allowlist intersects with the scope; unknown ids are ignored.
    """
    if fixtures_only:
        rows = db.execute(
            "SELECT id FROM applications WHERE COALESCE(is_fixture, 0) IN (1, TRUE) ORDER BY id"
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT id FROM applications
             WHERE status = 'approved'
               AND COALESCE(is_fixture, 0) NOT IN (1, TRUE)
             ORDER BY id
            """
        ).fetchall()
    ids = [_row_dict(r).get("id") for r in rows]
    ids = [i for i in ids if i]
    if segment:
        allow = {str(s) for s in segment}
        ids = [i for i in ids if str(i) in allow]
    return ids


# ── Agent status row (mirrors monitoring_automation) ─────────────────────────
def _fetch_status_row(db) -> Optional[Dict[str, Any]]:
    row = db.execute(
        """
        SELECT * FROM monitoring_agent_status
         WHERE agent_type = ? OR agent_name = ?
         ORDER BY id ASC
         LIMIT 1
        """,
        (SCHEDULER_AGENT_TYPE, SCHEDULER_AGENT_NAME),
    ).fetchone()
    return _row_dict(row) if row is not None else None


def _ensure_status_row(db) -> Dict[str, Any]:
    row = _fetch_status_row(db)
    if row:
        return row
    db.execute(
        """
        INSERT INTO monitoring_agent_status
            (agent_name, agent_type, last_run, next_run, run_frequency,
             clients_monitored, alerts_generated, status)
        VALUES (?, ?, NULL, NULL, ?, 0, 0, ?)
        """,
        (
            SCHEDULER_AGENT_NAME,
            SCHEDULER_AGENT_TYPE,
            SCHEDULER_RUN_FREQUENCY,
            "active" if scheduler_enabled() else "disabled",
        ),
    )
    db.commit()
    return _fetch_status_row(db) or {}


def _update_status_row(db, *, last_run: datetime, applications: int,
                       created: int, failed: int, capped: bool) -> None:
    row = _ensure_status_row(db)
    if failed:
        status = "degraded"
    elif scheduler_enabled():
        status = "active"
    else:
        status = "disabled"
    next_run = (
        _iso(last_run + timedelta(seconds=scheduler_interval_seconds()))
        if scheduler_enabled()
        else None
    )
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
            next_run,
            SCHEDULER_RUN_FREQUENCY + (" (capped run)" if capped else ""),
            applications,
            created,
            status,
            row.get("id"),
        ),
    )
    db.commit()


def scheduler_status(db) -> Dict[str, Any]:
    row = _fetch_status_row(db)
    return {
        "agent": row or {
            "agent_name": SCHEDULER_AGENT_NAME,
            "agent_type": SCHEDULER_AGENT_TYPE,
            "status": "disabled",
        },
        "enabled": scheduler_enabled(),
        "enabled_default": "explicit_opt_in_only",
        "interval_seconds": scheduler_interval_seconds(),
        "max_alerts_per_run": max_alerts_per_run(),
        "segment": configured_segment(),
        "timestamp": _iso(),
    }


# ── Sweep ────────────────────────────────────────────────────────────────────
_RUN_IN_PROGRESS = False


def run_document_health_sweep(
    db,
    *,
    dry_run: bool = True,
    segment: Optional[List[str]] = None,
    fixtures_only: bool = False,
    max_alerts: Optional[int] = None,
    now: Optional[datetime] = None,
    trigger: str = "manual",
    audit_writer=system_audit_writer,
) -> Dict[str, Any]:
    """Run one document-health sweep (dry-run by default).

    Cap semantics (strict, no silent truncation): before each application is
    processed, its would-create count is computed via the pure plan; if
    processing it would exceed ``max_alerts``, the application is skipped and
    counted in the remainder. Created alerts therefore never exceed the cap.
    Updates/resolves are cleanup and are never capped.
    """
    global _RUN_IN_PROGRESS
    if _RUN_IN_PROGRESS:
        return {"status": "skipped", "reason": "run_in_progress"}
    _RUN_IN_PROGRESS = True
    try:
        now = now or _utc_now()
        cap = max_alerts if max_alerts is not None else max_alerts_per_run()
        cap = max(1, min(int(cap), MAX_MAX_ALERTS_PER_RUN))
        run_id = str(uuid4())
        app_ids = sweep_candidate_application_ids(
            db, segment=segment, fixtures_only=fixtures_only,
        )

        summary: Dict[str, Any] = {
            "run_id": run_id,
            "trigger": trigger,
            "dry_run": bool(dry_run),
            "fixtures_only": bool(fixtures_only),
            "segment_size": len(segment) if segment else None,
            "applications_in_scope": len(app_ids),
            "applications_processed": 0,
            "applications_skipped_by_cap": 0,
            "created": 0,
            "updated": 0,
            "resolved": 0,
            "failed": 0,
            "capped": False,
            "remainder_would_create": 0,
            "max_alerts": cap,
            "by_type": {},
            "by_severity": {},
            "per_application": [],
            "failures": [],
        }

        if not dry_run:
            _ensure_status_row(db)
            system_audit_writer(
                SYSTEM_USER,
                "monitoring.document_health.run_started",
                "document_health_scheduler",
                str({"run_id": run_id, "trigger": trigger,
                     "applications_in_scope": len(app_ids),
                     "max_alerts": cap, "fixtures_only": bool(fixtures_only)}),
                db=db,
                commit=True,
            )

        for app_id in app_ids:
            try:
                plan = dhm.compute_document_health_plan(db, app_id, today=now.date())
            except Exception as exc:
                summary["failed"] += 1
                summary["failures"].append({
                    "application_id": app_id,
                    "stage": "plan",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                })
                logger.exception("document health plan failed app=%s", app_id)
                continue

            would_create = plan["would_create"]
            for issue in would_create:
                summary["by_type"][issue["alert_type"]] = (
                    summary["by_type"].get(issue["alert_type"], 0) + 1
                )
                summary["by_severity"][issue["severity"]] = (
                    summary["by_severity"].get(issue["severity"], 0) + 1
                )

            if dry_run:
                summary["applications_processed"] += 1
                summary["created"] += len(would_create)
                summary["updated"] += plan["would_update"]
                summary["resolved"] += plan["would_resolve"]
                if would_create or plan["would_update"] or plan["would_resolve"]:
                    summary["per_application"].append({
                        "application_id": app_id,
                        "would_create": len(would_create),
                        "would_update": plan["would_update"],
                        "would_resolve": plan["would_resolve"],
                    })
                continue

            # Strict cap pre-check: never start an application whose creations
            # would push the run past the cap.
            if summary["created"] + len(would_create) > cap:
                summary["capped"] = True
                summary["applications_skipped_by_cap"] += 1
                summary["remainder_would_create"] += len(would_create)
                continue

            try:
                result = dhm.sync_document_health_alerts_for_application(
                    db,
                    app_id,
                    user=SYSTEM_USER,
                    audit_writer=audit_writer,
                    today=now.date(),
                )
                summary["applications_processed"] += 1
                summary["created"] += result["created"]
                summary["updated"] += result["updated"]
                summary["resolved"] += result["resolved"]
                if result["created"] or result["updated"] or result["resolved"]:
                    summary["per_application"].append({
                        "application_id": app_id,
                        "created": result["created"],
                        "updated": result["updated"],
                        "resolved": result["resolved"],
                    })
            except Exception as exc:
                summary["failed"] += 1
                summary["failures"].append({
                    "application_id": app_id,
                    "stage": "sync",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                })
                logger.exception("document health sweep failed app=%s", app_id)
                try:
                    db.rollback()
                except Exception:
                    pass

        if not dry_run:
            _update_status_row(
                db,
                last_run=now,
                applications=summary["applications_processed"],
                created=summary["created"],
                failed=summary["failed"],
                capped=summary["capped"],
            )
            system_audit_writer(
                SYSTEM_USER,
                "monitoring.document_health.run_completed",
                "document_health_scheduler",
                str({
                    "run_id": run_id,
                    "trigger": trigger,
                    "applications_processed": summary["applications_processed"],
                    "created": summary["created"],
                    "updated": summary["updated"],
                    "resolved": summary["resolved"],
                    "failed": summary["failed"],
                    "capped": summary["capped"],
                    "applications_skipped_by_cap": summary["applications_skipped_by_cap"],
                    "remainder_would_create": summary["remainder_would_create"],
                }),
                db=db,
                commit=True,
            )
        return summary
    finally:
        _RUN_IN_PROGRESS = False


__all__ = [
    "SCHEDULER_AGENT_NAME",
    "SCHEDULER_AGENT_TYPE",
    "SYSTEM_USER",
    "configured_segment",
    "max_alerts_per_run",
    "run_document_health_sweep",
    "scheduler_enabled",
    "scheduler_interval_seconds",
    "scheduler_status",
    "sweep_candidate_application_ids",
]
