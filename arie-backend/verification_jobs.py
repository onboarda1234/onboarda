"""Async document-verification job primitives.

PR6 keeps the active verification path synchronous while laying down the
rollback-safe async foundation.  These helpers are deliberately small and
database-oriented so workers can claim jobs without changing provider
selection, Sumsub timing, or the authoritative document compatibility fields.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from branding import BRAND
from verification_state import (
    STATE_FAILED,
    STATE_IN_PROGRESS,
    STATE_PENDING,
    normalize_verification_state,
    verification_state_payload,
)

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
VERIFICATION_JOB_STATUSES = ACTIVE_JOB_STATUSES + TERMINAL_JOB_STATUSES

DEFAULT_JOB_PRIORITY = 100
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 120
MAX_PENDING_SECONDS = 15 * 60
MAX_IN_PROGRESS_SECONDS = 20 * 60
STUCK_JOB_THRESHOLD_SECONDS = MAX_IN_PROGRESS_SECONDS
ALERT_DESTINATION = "CloudWatch query verification_async_stuck_jobs -> compliance-ops on-call"
MANUAL_RECOVERY_PATH = (
    "Inspect provider/file error, resolve the root cause, then requeue a failed "
    "document verification job or rerun synchronous verification from Back Office."
)
SYSTEM_ACTOR_ID = "system:verification-worker"
SYSTEM_ACTOR_NAME = "Verification Worker"


class VerificationJobMissing(LookupError):
    """Raised when a verification job was already cleaned up."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        super().__init__(f"Verification job not found: {job_id}")


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
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _duration_ms(start: Any, end: Any) -> Optional[int]:
    start_ts = _parse_job_timestamp(start)
    end_ts = _parse_job_timestamp(end)
    if not start_ts or not end_ts:
        return None
    return max(int(round((end_ts - start_ts).total_seconds() * 1000)), 0)


def verification_job_timing_ms(job: Optional[Dict[str, Any]]) -> Dict[str, Optional[int]]:
    """Return derived PR7B timing metrics from persisted job timestamps."""
    job = job or {}
    return {
        "queue_wait_ms": _duration_ms(job.get("created_at"), job.get("locked_at")),
        "execution_ms": _duration_ms(job.get("locked_at"), job.get("completed_at")),
        "end_to_end_job_ms": _duration_ms(job.get("created_at"), job.get("completed_at")),
    }


def async_verify_sla_config() -> Dict[str, Any]:
    """Return the numeric PR6 SLA/stuck-job contract."""
    return {
        "max_pending_seconds": MAX_PENDING_SECONDS,
        "max_in_progress_seconds": MAX_IN_PROGRESS_SECONDS,
        "stuck_job_threshold_seconds": STUCK_JOB_THRESHOLD_SECONDS,
        "retry_backoff_seconds": RETRY_BACKOFF_SECONDS,
        "max_attempts": MAX_ATTEMPTS,
        "alert_destination": ALERT_DESTINATION,
        "manual_recovery_path": MANUAL_RECOVERY_PATH,
    }


def _safe_json_loads(value: Any, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def serialize_verification_job(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    job = dict(row)
    metadata = job.get("job_metadata")
    if isinstance(metadata, str):
        try:
            job["job_metadata"] = json.loads(metadata or "{}")
        except Exception:
            job["job_metadata"] = {}
    elif metadata is None:
        job["job_metadata"] = {}
    job["timing_ms"] = verification_job_timing_ms(job)
    return job


def get_verification_job_or_raise(db, job_id: str) -> Dict[str, Any]:
    job = serialize_verification_job(
        db.execute("SELECT * FROM verification_jobs WHERE id=?", (job_id,)).fetchone()
    )
    if not job:
        raise VerificationJobMissing(job_id)
    return job


def format_verification_job_timing_log_fields(job: Optional[Dict[str, Any]]) -> str:
    """PII-safe timing fields for worker and CloudWatch log queries."""
    timing = verification_job_timing_ms(job)
    return (
        f"queue_wait_ms={timing.get('queue_wait_ms')} "
        f"execution_ms={timing.get('execution_ms')} "
        f"end_to_end_job_ms={timing.get('end_to_end_job_ms')}"
    )


def _actor_from_user(user: Optional[Dict[str, Any]], *, worker_id: str = "") -> Dict[str, str]:
    user = user or {}
    if worker_id:
        return {
            "id": SYSTEM_ACTOR_ID,
            "name": SYSTEM_ACTOR_NAME,
            "role": "system",
            "actor_type": "system",
            "worker_id": worker_id,
        }
    return {
        "id": str(user.get("sub") or user.get("id") or ""),
        "name": str(user.get("name") or user.get("full_name") or ""),
        "role": str(user.get("role") or ""),
        "actor_type": "user",
        "worker_id": "",
    }


def _safe_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def _insert_audit(
    db,
    actor: Dict[str, str],
    action: str,
    target: str,
    detail: Dict[str, Any],
    *,
    before_state: Optional[Dict[str, Any]] = None,
    after_state: Optional[Dict[str, Any]] = None,
    ip_address: str = "system",
) -> None:
    db.execute(
        "INSERT INTO audit_log "
        "(user_id, user_name, user_role, action, target, detail, ip_address, before_state, after_state) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            actor.get("id", ""),
            actor.get("name", ""),
            actor.get("role", ""),
            action,
            target,
            _safe_json(detail),
            ip_address,
            _safe_json(before_state) if before_state is not None else None,
            _safe_json(after_state) if after_state is not None else None,
        ),
    )


def _document_state(doc: Dict[str, Any], status: Optional[str] = None, **extra) -> Dict[str, Any]:
    state = {
        "document_id": doc.get("id"),
        "verification_status": normalize_verification_state(
            status if status is not None else doc.get("verification_status")
        ),
        "doc_name": doc.get("doc_name"),
        "doc_type": doc.get("doc_type"),
    }
    state.update(extra)
    return state


def _latest_job_for_document(db, document_id: str) -> Optional[Dict[str, Any]]:
    row = db.execute(
        """
        SELECT * FROM verification_jobs
        WHERE document_id=?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (document_id,),
    ).fetchone()
    return serialize_verification_job(row)


def enqueue_verification_job(
    db,
    doc: Dict[str, Any],
    app: Dict[str, Any],
    actor_user: Optional[Dict[str, Any]],
    *,
    request_id: str = "",
    ip_address: str = "",
) -> Dict[str, Any]:
    """Create or return the active async verification job for a document."""
    existing = db.execute(
        """
        SELECT * FROM verification_jobs
        WHERE document_id=? AND status IN ('pending','retrying','in_progress')
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (doc["id"],),
    ).fetchone()
    if existing:
        return {"created": False, "job": serialize_verification_job(existing)}

    job_id = "vjob_" + uuid.uuid4().hex
    metadata = {
        "source": "api",
        "request_id": request_id or "",
        "flag": "FF_ASYNC_VERIFY",
    }
    db.execute(
        """
        INSERT INTO verification_jobs
        (id, document_id, application_id, status, priority, attempt_count,
         max_attempts, run_after, job_metadata, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?)
        """,
        (
            job_id,
            doc["id"],
            doc["application_id"],
            JOB_PENDING,
            DEFAULT_JOB_PRIORITY,
            0,
            MAX_ATTEMPTS,
            _safe_json(metadata),
            (actor_user or {}).get("sub") or (actor_user or {}).get("id") or "",
        ),
    )
    job = serialize_verification_job(
        db.execute("SELECT * FROM verification_jobs WHERE id=?", (job_id,)).fetchone()
    )
    actor = _actor_from_user(actor_user)
    before_doc_state = _document_state(doc)
    if before_doc_state["verification_status"] not in (STATE_PENDING, STATE_IN_PROGRESS):
        after_doc_state = _document_state(doc, STATE_PENDING, job_id=job_id)
        db.execute(
            "UPDATE documents SET verification_status=? WHERE id=?",
            (STATE_PENDING, doc["id"]),
        )
        _insert_audit(
            db,
            actor,
            "Document Verification State Changed",
            app.get("ref") or doc.get("application_id") or doc["id"],
            {
                "event": "document_verification_state_transition",
                "actor_type": "user",
                "trigger": "async_verify_enqueued",
                "application_id": app.get("id"),
                "application_ref": app.get("ref"),
                "document_id": doc["id"],
                "job_id": job_id,
                "from": before_doc_state["verification_status"],
                "to": after_doc_state["verification_status"],
            },
            before_state=before_doc_state,
            after_state=after_doc_state,
            ip_address=ip_address or "",
        )
    _insert_audit(
        db,
        actor,
        "Document Verification Job Enqueued",
        app.get("ref") or doc.get("application_id") or doc["id"],
        {
            "event": "document_verification_job_enqueued",
            "actor_type": actor["actor_type"],
            "application_id": app.get("id"),
            "application_ref": app.get("ref"),
            "document_id": doc["id"],
            "job_id": job_id,
            "status": JOB_PENDING,
        },
        after_state={"verification_job": job},
        ip_address=ip_address or "",
    )
    return {"created": True, "job": job}


def claim_next_verification_job(db, worker_id: str) -> Optional[Dict[str, Any]]:
    """Claim the next pending/retrying job.

    PostgreSQL uses ``FOR UPDATE SKIP LOCKED`` for safe concurrent workers.
    SQLite test/development mode uses a single-row conditional update.
    Caller owns the transaction and should commit after a successful claim.
    """
    if db.is_postgres:
        row = db.execute(
            """
            SELECT * FROM verification_jobs
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
            SELECT * FROM verification_jobs
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
        UPDATE verification_jobs
           SET status='in_progress',
               locked_by=?,
               locked_at=datetime('now'),
               attempt_count=attempt_count + 1,
               updated_at=datetime('now')
         WHERE id=? AND status IN ('pending','retrying')
        """,
        (worker_id, job_id),
    )
    job = serialize_verification_job(
        db.execute("SELECT * FROM verification_jobs WHERE id=?", (job_id,)).fetchone()
    )
    if not job or job.get("status") != JOB_IN_PROGRESS or job.get("locked_by") != worker_id:
        return None
    _transition_document_for_job(
        db,
        job,
        STATE_IN_PROGRESS,
        worker_id=worker_id,
        trigger="async_verify_worker_started",
    )
    return job


def _transition_document_for_job(
    db,
    job: Dict[str, Any],
    status: str,
    *,
    worker_id: str,
    trigger: str,
    verification_results: Optional[Dict[str, Any]] = None,
) -> None:
    doc = db.execute("SELECT * FROM documents WHERE id=?", (job["document_id"],)).fetchone()
    if not doc:
        return
    app = db.execute("SELECT * FROM applications WHERE id=?", (doc["application_id"],)).fetchone() or {}
    before_state = _document_state(doc)
    after_state = _document_state(doc, status, job_id=job["id"], worker_id=worker_id)
    if before_state["verification_status"] == after_state["verification_status"] and verification_results is None:
        return

    if verification_results is not None:
        db.execute(
            """
            UPDATE documents
               SET verification_status=?,
                   verification_results=?,
                   verified_at=datetime('now')
             WHERE id=?
            """,
            (status, _safe_json(verification_results), doc["id"]),
        )
    else:
        db.execute(
            "UPDATE documents SET verification_status=? WHERE id=?",
            (status, doc["id"]),
        )

    actor = _actor_from_user(None, worker_id=worker_id)
    _insert_audit(
        db,
        actor,
        "Document Verification State Changed",
        app.get("ref") or doc.get("application_id") or doc["id"],
        {
            "event": "document_verification_state_transition",
            "actor_type": "system",
            "trigger": trigger,
            "application_id": app.get("id"),
            "application_ref": app.get("ref"),
            "document_id": doc["id"],
            "job_id": job["id"],
            "worker_id": worker_id,
            "from": before_state["verification_status"],
            "to": after_state["verification_status"],
        },
        before_state=before_state,
        after_state=after_state,
        ip_address="system",
    )


def mark_verification_job_succeeded(
    db,
    job_id: str,
    *,
    worker_id: str,
    verification_status: str,
    verification_results: Dict[str, Any],
    transition_document: bool = True,
) -> Dict[str, Any]:
    status = normalize_verification_state(verification_status)
    if status not in ("verified", "flagged", "failed"):
        raise ValueError(f"Invalid terminal verification status: {verification_status}")
    db.execute(
        """
        UPDATE verification_jobs
           SET status='succeeded',
               locked_by=?,
               updated_at=datetime('now'),
               completed_at=datetime('now'),
               last_error=NULL
         WHERE id=?
        """,
        (worker_id, job_id),
    )
    job = get_verification_job_or_raise(db, job_id)
    if transition_document:
        _transition_document_for_job(
            db,
            job,
            status,
            worker_id=worker_id,
            trigger="async_verify_worker_completed",
            verification_results=verification_results,
        )
    return job


def mark_verification_job_failed(
    db,
    job_id: str,
    *,
    worker_id: str,
    error: str,
    retryable: bool,
) -> Dict[str, Any]:
    job = get_verification_job_or_raise(db, job_id)

    attempts = int(job.get("attempt_count") or 0)
    max_attempts = int(job.get("max_attempts") or MAX_ATTEMPTS)
    if retryable and attempts < max_attempts:
        run_after = db_timestamp(utc_now() + timedelta(seconds=RETRY_BACKOFF_SECONDS))
        db.execute(
            """
            UPDATE verification_jobs
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
        updated = serialize_verification_job(
            db.execute("SELECT * FROM verification_jobs WHERE id=?", (job_id,)).fetchone()
        )
        _transition_document_for_job(
            db,
            updated,
            STATE_PENDING,
            worker_id=worker_id,
            trigger="async_verify_worker_retry_scheduled",
        )
        return updated

    failure_results = {
        "overall": STATE_FAILED,
        "checks": [{
            "label": "Async verification job",
            "type": "system",
            "result": "fail",
            "message": "Async verification job failed before a trustworthy result could be produced.",
        }],
        "verification_failure_classification": "retry_exhausted" if retryable else "terminal_worker_failure",
        "provider_failure": False,
        "retryable": False,
        "system_warning": "async_verification_job_failed",
        "error": str(error or "")[:300],
        "verified_at": utc_now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    db.execute(
        """
        UPDATE verification_jobs
           SET status='failed',
               locked_by=?,
               last_error=?,
               updated_at=datetime('now'),
               completed_at=datetime('now')
         WHERE id=?
        """,
        (worker_id, str(error or "")[:1000], job_id),
    )
    updated = serialize_verification_job(
        db.execute("SELECT * FROM verification_jobs WHERE id=?", (job_id,)).fetchone()
    )
    _transition_document_for_job(
        db,
        updated,
        STATE_FAILED,
        worker_id=worker_id,
        trigger="async_verify_worker_failed",
        verification_results=failure_results,
    )
    return updated


def find_stuck_verification_jobs(db, *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or utc_now()
    pending_cutoff = db_timestamp(now - timedelta(seconds=MAX_PENDING_SECONDS))
    in_progress_cutoff = db_timestamp(now - timedelta(seconds=MAX_IN_PROGRESS_SECONDS))
    rows = db.execute(
        """
        SELECT * FROM verification_jobs
        WHERE (
            status IN ('pending','retrying') AND created_at < ?
        ) OR (
            status='in_progress' AND locked_at IS NOT NULL AND locked_at < ?
        )
        ORDER BY created_at ASC, id ASC
        """,
        (pending_cutoff, in_progress_cutoff),
    ).fetchall()
    return [serialize_verification_job(row) for row in rows]


def verification_queue_observability_snapshot(
    db,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return PII-safe queue gauges for alarm metric emission."""

    def row_get(row: Any, key: str, default: Any = None) -> Any:
        try:
            return row.get(key, default)
        except AttributeError:
            try:
                return row[key]
            except (KeyError, IndexError, TypeError):
                return default

    now = now or utc_now()
    active_rows = db.execute(
        """
        SELECT status, created_at, locked_at
          FROM verification_jobs
         WHERE status IN ('pending','retrying','in_progress')
        """
    ).fetchall()
    failed_cutoff = db_timestamp(now - timedelta(hours=1))
    failed_last_hour = db.execute(
        """
        SELECT COUNT(*) AS count
          FROM verification_jobs
         WHERE status='failed'
           AND COALESCE(completed_at, updated_at, created_at) >= ?
        """,
        (failed_cutoff,),
    ).fetchone()

    oldest_pending_age_seconds = 0
    oldest_active_ts = None
    for row in active_rows:
        timestamp_value = (
            row_get(row, "locked_at")
            if row_get(row, "status") == JOB_IN_PROGRESS
            else row_get(row, "created_at")
        )
        parsed = _parse_job_timestamp(timestamp_value)
        if parsed and (oldest_active_ts is None or parsed < oldest_active_ts):
            oldest_active_ts = parsed
    if oldest_active_ts:
        oldest_pending_age_seconds = max(int((now - oldest_active_ts).total_seconds()), 0)

    stuck = find_stuck_verification_jobs(db, now=now)
    return {
        "queue_depth": len(active_rows),
        "stuck_jobs": len(stuck),
        "oldest_pending_age_seconds": oldest_pending_age_seconds,
        "failed_last_hour": int(row_get(failed_last_hour, "count", 0) or 0),
        "max_pending_seconds": MAX_PENDING_SECONDS,
        "max_in_progress_seconds": MAX_IN_PROGRESS_SECONDS,
        "alert_destination": f"cloudwatch_alarm_{BRAND['system_id']}_pilot_alerts",
    }


def mark_stuck_verification_jobs_failed(
    db,
    *,
    worker_id: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    stuck = find_stuck_verification_jobs(db, now=now)
    failed = []
    for job in stuck:
        try:
            failed.append(
                mark_verification_job_failed(
                    db,
                    job["id"],
                    worker_id=worker_id,
                    error="async verification job exceeded stuck threshold",
                    retryable=False,
                )
            )
        except Exception:
            logger.exception("Failed to mark stuck verification job failed: %s", job.get("id"))
    return {
        "stuck_jobs": len(stuck),
        "failed_jobs": len(failed),
        "jobs": failed,
        "sla": async_verify_sla_config(),
    }


def recover_stuck_verification_jobs(
    db,
    *,
    worker_id: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Recover stale worker locks without silently losing jobs.

    A worker killed mid-verification leaves the row ``in_progress`` with its
    lock metadata.  The runtime worker uses this recovery path before claiming
    new work: stale in-progress jobs are returned to ``retrying`` while they
    still have attempts left, then terminally failed after attempts are
    exhausted.  Stale never-claimed pending/retrying jobs retain PR6's
    fail-safe terminal handling.
    """
    now = now or utc_now()
    pending_cutoff = db_timestamp(now - timedelta(seconds=MAX_PENDING_SECONDS))
    in_progress_cutoff = db_timestamp(now - timedelta(seconds=MAX_IN_PROGRESS_SECONDS))
    in_progress_rows = db.execute(
        """
        SELECT * FROM verification_jobs
        WHERE status='in_progress' AND locked_at IS NOT NULL AND locked_at < ?
        ORDER BY created_at ASC, id ASC
        """,
        (in_progress_cutoff,),
    ).fetchall()
    pending_rows = db.execute(
        """
        SELECT * FROM verification_jobs
        WHERE status IN ('pending','retrying') AND created_at < ?
        ORDER BY created_at ASC, id ASC
        """,
        (pending_cutoff,),
    ).fetchall()

    requeued = []
    failed = []
    for row in in_progress_rows:
        job = serialize_verification_job(row)
        attempts = int(job.get("attempt_count") or 0)
        max_attempts = int(job.get("max_attempts") or MAX_ATTEMPTS)
        if attempts < max_attempts:
            db.execute(
                """
                UPDATE verification_jobs
                   SET status='retrying',
                       run_after=datetime('now'),
                       locked_by=NULL,
                       locked_at=NULL,
                       last_error=?,
                       updated_at=datetime('now')
                 WHERE id=? AND status='in_progress'
                """,
                ("async verification worker lock exceeded stuck threshold", job["id"]),
            )
            updated = serialize_verification_job(
                db.execute("SELECT * FROM verification_jobs WHERE id=?", (job["id"],)).fetchone()
            )
            _transition_document_for_job(
                db,
                updated,
                STATE_PENDING,
                worker_id=worker_id,
                trigger="async_verify_worker_reclaimed",
            )
            requeued.append(updated)
        else:
            failed.append(
                mark_verification_job_failed(
                    db,
                    job["id"],
                    worker_id=worker_id,
                    error="async verification worker lock exceeded stuck threshold and attempts are exhausted",
                    retryable=False,
                )
            )

    for row in pending_rows:
        job = serialize_verification_job(row)
        failed.append(
            mark_verification_job_failed(
                db,
                job["id"],
                worker_id=worker_id,
                error="async verification job exceeded pending stuck threshold",
                retryable=False,
            )
        )

    return {
        "stuck_jobs": len(in_progress_rows) + len(pending_rows),
        "requeued_jobs": len(requeued),
        "failed_jobs": len(failed),
        "jobs": requeued + failed,
        "sla": async_verify_sla_config(),
    }


def verification_status_for_document(db, document_id: str) -> Optional[Dict[str, Any]]:
    doc = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
    if not doc:
        return None
    status = normalize_verification_state(doc.get("verification_status"))
    job = _latest_job_for_document(db, document_id)
    return {
        "doc_id": doc["id"],
        "id": doc["id"],
        "application_id": doc.get("application_id"),
        "doc_name": doc.get("doc_name"),
        "doc_type": doc.get("doc_type"),
        "person_id": doc.get("person_id"),
        "slot_key": doc.get("slot_key"),
        "is_current": doc.get("is_current"),
        "version": doc.get("version"),
        "verification_status": status,
        **verification_state_payload(status),
        "verification_results": _safe_json_loads(doc.get("verification_results"), {}),
        "verified_at": doc.get("verified_at"),
        "uploaded_by": doc.get("uploaded_by"),
        "uploaded_by_actor_type": doc.get("uploaded_by_actor_type"),
        "uploaded_by_actor_id": doc.get("uploaded_by_actor_id"),
        "uploaded_by_display": doc.get("uploaded_by_display"),
        "upload_source": doc.get("upload_source"),
        "verification_job": job,
        "async_sla": async_verify_sla_config(),
    }


def format_async_job_health_log_line(summary: Dict[str, Any]) -> str:
    """PII-safe line that saved CloudWatch queries can parse."""
    return (
        "verification_async_job_health "
        f"stuck_jobs={int(summary.get('stuck_jobs') or 0)} "
        f"requeued_jobs={int(summary.get('requeued_jobs') or 0)} "
        f"failed_jobs={int(summary.get('failed_jobs') or 0)} "
        f"max_pending_seconds={MAX_PENDING_SECONDS} "
        f"max_in_progress_seconds={MAX_IN_PROGRESS_SECONDS} "
        f"alert_destination=cloudwatch_compliance_ops"
    )
