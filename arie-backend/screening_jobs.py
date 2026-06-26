"""Async application-screening job primitives.

Submit-time AML screening must not hold the client request open while the live
provider polls.  These helpers provide a small application-scoped queue that the
existing worker service can claim safely.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)

JOB_PENDING = "pending"
JOB_IN_PROGRESS = "in_progress"
JOB_RETRYING = "retrying"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"

ACTIVE_JOB_STATUSES = (JOB_PENDING, JOB_RETRYING, JOB_IN_PROGRESS)
CLAIMABLE_JOB_STATUSES = (JOB_PENDING, JOB_RETRYING)
TERMINAL_JOB_STATUSES = (JOB_SUCCEEDED, JOB_FAILED, JOB_CANCELLED)

DEFAULT_JOB_PRIORITY = 80
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 120
MAX_PENDING_SECONDS = 15 * 60
MAX_IN_PROGRESS_SECONDS = 20 * 60
SYSTEM_ACTOR_ID = "system:screening-worker"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def db_timestamp(value: Optional[datetime] = None) -> str:
    value = value or utc_now()
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_job_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    parsed = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _duration_ms(start: Any, end: Any) -> Optional[int]:
    start_ts = _parse_job_timestamp(start)
    end_ts = _parse_job_timestamp(end)
    if not start_ts or not end_ts:
        return None
    return max(int(round((end_ts - start_ts).total_seconds() * 1000)), 0)


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value if value is not None else {}, default=str, sort_keys=True)
    except Exception:
        return "{}"


def _safe_json_loads(value: Any, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        if hasattr(row, "get"):
            return row.get(key, default)
        if hasattr(row, "keys") and key not in row.keys():
            return default
        return row[key]
    except Exception:
        return default


def serialize_screening_job(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    job = dict(row)
    job["job_metadata"] = _safe_json_loads(job.get("job_metadata"), {})
    job["timing_ms"] = screening_job_timing_ms(job)
    return job


def screening_job_timing_ms(job: Optional[Dict[str, Any]]) -> Dict[str, Optional[int]]:
    job = job or {}
    return {
        "queue_wait_ms": _duration_ms(job.get("created_at"), job.get("locked_at")),
        "execution_ms": _duration_ms(job.get("locked_at"), job.get("completed_at")),
        "end_to_end_job_ms": _duration_ms(job.get("created_at"), job.get("completed_at")),
    }


def format_screening_job_timing_log_fields(job: Optional[Dict[str, Any]]) -> str:
    timing = screening_job_timing_ms(job)
    return (
        f"queue_wait_ms={timing.get('queue_wait_ms')} "
        f"execution_ms={timing.get('execution_ms')} "
        f"end_to_end_job_ms={timing.get('end_to_end_job_ms')}"
    )


def enqueue_screening_job(
    db,
    app: Dict[str, Any],
    actor_user: Optional[Dict[str, Any]],
    *,
    submit_attempt_id: str,
    provider: str = "complyadvantage",
    request_id: str = "",
    ip_address: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create or return the active async screening job for an application."""
    existing = db.execute(
        """
        SELECT * FROM screening_jobs
        WHERE application_id=? AND status IN ('pending','retrying','in_progress')
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (app["id"],),
    ).fetchone()
    if existing:
        return {"created": False, "job": serialize_screening_job(existing)}

    job_id = "sjob_" + uuid.uuid4().hex
    payload = {
        "source": "prescreening_submit",
        "request_id": request_id or "",
        "ip_address": ip_address or "",
        "submit_attempt_id": submit_attempt_id,
        **(metadata or {}),
    }
    db.execute(
        """
        INSERT INTO screening_jobs
        (id, application_id, submit_attempt_id, provider, status, priority,
         attempt_count, max_attempts, run_after, job_metadata, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?)
        """,
        (
            job_id,
            app["id"],
            submit_attempt_id,
            provider,
            JOB_PENDING,
            DEFAULT_JOB_PRIORITY,
            0,
            MAX_ATTEMPTS,
            _safe_json(payload),
            (actor_user or {}).get("sub") or (actor_user or {}).get("id") or "",
        ),
    )
    job = serialize_screening_job(
        db.execute("SELECT * FROM screening_jobs WHERE id=?", (job_id,)).fetchone()
    )
    return {"created": True, "job": job}


def claim_next_screening_job(db, worker_id: str) -> Optional[Dict[str, Any]]:
    """Claim the next pending/retrying screening job."""
    if db.is_postgres:
        row = db.execute(
            """
            SELECT * FROM screening_jobs
            WHERE status IN ('pending','retrying')
              AND COALESCE(run_after, datetime('now')) <= datetime('now')
            ORDER BY priority ASC, created_at ASC, id ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        ).fetchone()
    else:
        row = db.execute(
            """
            SELECT * FROM screening_jobs
            WHERE status IN ('pending','retrying')
              AND COALESCE(run_after, datetime('now')) <= datetime('now')
            ORDER BY priority ASC, created_at ASC, id ASC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return None

    job_id = row["id"]
    db.execute(
        """
        UPDATE screening_jobs
           SET status='in_progress',
               locked_by=?,
               locked_at=datetime('now'),
               attempt_count=attempt_count + 1,
               updated_at=datetime('now')
         WHERE id=? AND status IN ('pending','retrying')
        """,
        (worker_id, job_id),
    )
    job = serialize_screening_job(
        db.execute("SELECT * FROM screening_jobs WHERE id=?", (job_id,)).fetchone()
    )
    if not job or job.get("status") != JOB_IN_PROGRESS or job.get("locked_by") != worker_id:
        return None
    return job


def mark_screening_job_succeeded(db, job_id: str, *, worker_id: str) -> Dict[str, Any]:
    db.execute(
        """
        UPDATE screening_jobs
           SET status='succeeded',
               locked_by=?,
               updated_at=datetime('now'),
               completed_at=datetime('now'),
               last_error=NULL
         WHERE id=?
        """,
        (worker_id, job_id),
    )
    job = serialize_screening_job(
        db.execute("SELECT * FROM screening_jobs WHERE id=?", (job_id,)).fetchone()
    )
    if not job:
        raise ValueError(f"Screening job not found: {job_id}")
    return job


def mark_screening_job_failed(
    db,
    job_id: str,
    *,
    worker_id: str,
    error: str,
    retryable: bool,
) -> Dict[str, Any]:
    job = serialize_screening_job(
        db.execute("SELECT * FROM screening_jobs WHERE id=?", (job_id,)).fetchone()
    )
    if not job:
        raise ValueError(f"Screening job not found: {job_id}")

    attempts = int(job.get("attempt_count") or 0)
    max_attempts = int(job.get("max_attempts") or MAX_ATTEMPTS)
    if retryable and attempts < max_attempts:
        run_after = db_timestamp(utc_now() + timedelta(seconds=RETRY_BACKOFF_SECONDS))
        db.execute(
            """
            UPDATE screening_jobs
               SET status='retrying',
                   run_after=?,
                   locked_by=NULL,
                   locked_at=NULL,
                   last_error=?,
                   updated_at=datetime('now')
             WHERE id=?
            """,
            (run_after, str(error or "")[:1000], job_id),
        )
    else:
        db.execute(
            """
            UPDATE screening_jobs
               SET status='failed',
                   locked_by=?,
                   last_error=?,
                   updated_at=datetime('now'),
                   completed_at=datetime('now')
             WHERE id=?
            """,
            (worker_id, str(error or "")[:1000], job_id),
        )
    updated = serialize_screening_job(
        db.execute("SELECT * FROM screening_jobs WHERE id=?", (job_id,)).fetchone()
    )
    if not updated:
        raise ValueError(f"Screening job not found after failure update: {job_id}")
    return updated


def latest_screening_job_for_application(db, application_id: str) -> Optional[Dict[str, Any]]:
    row = db.execute(
        """
        SELECT * FROM screening_jobs
        WHERE application_id=?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (application_id,),
    ).fetchone()
    return serialize_screening_job(row)


def recover_stuck_screening_jobs(db, *, worker_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or utc_now()
    cutoff = db_timestamp(now - timedelta(seconds=MAX_IN_PROGRESS_SECONDS))
    rows = db.execute(
        """
        SELECT * FROM screening_jobs
        WHERE status='in_progress' AND locked_at < ?
        ORDER BY locked_at ASC, id ASC
        """,
        (cutoff,),
    ).fetchall()
    requeued = []
    failed = []
    for row in rows or []:
        job = serialize_screening_job(row)
        attempts = int(job.get("attempt_count") or 0)
        max_attempts = int(job.get("max_attempts") or MAX_ATTEMPTS)
        if attempts < max_attempts:
            db.execute(
                """
                UPDATE screening_jobs
                   SET status='retrying',
                       run_after=datetime('now'),
                       locked_by=NULL,
                       locked_at=NULL,
                       last_error=?,
                       updated_at=datetime('now')
                 WHERE id=?
                """,
                ("async screening worker lock exceeded stuck threshold", job["id"]),
            )
            requeued.append(serialize_screening_job(db.execute("SELECT * FROM screening_jobs WHERE id=?", (job["id"],)).fetchone()))
        else:
            failed.append(mark_screening_job_failed(
                db,
                job["id"],
                worker_id=worker_id,
                error="async screening worker lock exceeded stuck threshold and attempts are exhausted",
                retryable=False,
            ))
    return {
        "stuck_jobs": len(rows or []),
        "requeued_jobs": len([item for item in requeued if item]),
        "failed_jobs": len(failed),
        "jobs": [item for item in requeued if item] + failed,
    }


def screening_queue_observability_snapshot(db) -> Dict[str, Any]:
    pending = db.execute(
        "SELECT COUNT(*) AS c FROM screening_jobs WHERE status IN ('pending','retrying')"
    ).fetchone()
    in_progress = db.execute(
        "SELECT COUNT(*) AS c FROM screening_jobs WHERE status='in_progress'"
    ).fetchone()
    failed_recent = db.execute(
        """
        SELECT COUNT(*) AS c FROM screening_jobs
        WHERE status='failed' AND completed_at >= ?
        """,
        (db_timestamp(utc_now() - timedelta(hours=1)),),
    ).fetchone()
    oldest = db.execute(
        """
        SELECT created_at FROM screening_jobs
        WHERE status IN ('pending','retrying')
        ORDER BY created_at ASC LIMIT 1
        """
    ).fetchone()
    oldest_age = None
    if oldest:
        created = _parse_job_timestamp(oldest["created_at"])
        if created:
            oldest_age = max(int((utc_now() - created).total_seconds()), 0)
    return {
        "queue_depth": int(_row_get(pending, "c", 0) or 0),
        "in_progress": int(_row_get(in_progress, "c", 0) or 0),
        "failed_last_hour": int(_row_get(failed_recent, "c", 0) or 0),
        "oldest_pending_age_seconds": oldest_age,
    }
