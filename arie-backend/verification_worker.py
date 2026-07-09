"""Async document-verification worker runtime.

This is the PR7A runtime counterpart to the PR6 Postgres-backed queue
foundation.  The worker is intentionally a separate process entrypoint for an
ECS Fargate worker service; it does not use in-process callbacks.
"""

import argparse
import json
import logging
import os
import socket
import time
import uuid
from typing import Any, Callable, Dict, Optional

from base_handler import _safe_json
from db import get_db
from observability import emit_cloudwatch_metric_log
from screening_jobs import (
    claim_next_screening_job,
    format_screening_job_timing_log_fields,
    mark_screening_job_failed,
    mark_screening_job_succeeded,
    recover_stuck_screening_jobs,
    screening_job_timing_ms,
    screening_queue_observability_snapshot,
)
from verification_jobs import (
    VerificationJobMissing,
    format_async_job_health_log_line,
    format_verification_job_timing_log_fields,
    claim_next_verification_job,
    get_verification_job_or_raise,
    mark_verification_job_failed,
    mark_verification_job_succeeded,
    recover_stuck_verification_jobs,
    verification_job_timing_ms,
    verification_queue_observability_snapshot,
)
from verification_state import (
    STATE_FAILED,
    STATE_FLAGGED,
    STATE_SKIPPED,
    STATE_VERIFIED,
    normalize_verification_state,
)

logger = logging.getLogger(__name__)

TERMINAL_DOCUMENT_STATES = (STATE_VERIFIED, STATE_FLAGGED, STATE_FAILED, STATE_SKIPPED)
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_OBSERVABILITY_INTERVAL_SECONDS = 60.0
SYSTEM_USER = {
    "sub": "system:verification-worker",
    "name": "Verification Worker",
    "role": "system",
}
_LAST_OBSERVABILITY_EMIT_MONOTONIC = 0.0
_LAST_SCREENING_OBSERVABILITY_EMIT_MONOTONIC = 0.0


class RetryableVerificationWorkerError(Exception):
    """Raised when the job should be returned to the queue for retry."""


class TerminalVerificationWorkerError(Exception):
    """Raised when the job should fail terminally."""


class RetryableScreeningWorkerError(Exception):
    """Raised when the screening job should be returned to the queue."""


class TerminalScreeningWorkerError(Exception):
    """Raised when the screening job should fail terminally."""


class _WorkerRequest:
    def __init__(self):
        self.headers = {"X-Request-ID": "verification-worker"}


class _WorkerHandlerShim:
    """Small adapter for reusing DocumentVerifyHandler's sync path.

    The handler path already contains the current verification-provider and
    Sumsub timing behavior.  This shim supplies only the handler methods that
    the sync path uses, keeping worker behavior aligned without exposing an
    HTTP surface or an ALB.
    """

    def __init__(self):
        self.request = _WorkerRequest()
        self.status_code = 200
        self.payload: Optional[Dict[str, Any]] = None

    def get_client_ip(self):
        return "system"

    def set_status(self, status):
        self.status_code = int(status)

    def write(self, payload):
        if isinstance(payload, str):
            try:
                self.payload = json.loads(payload)
            except Exception:
                self.payload = {"raw": payload}
        elif isinstance(payload, dict):
            self.payload = payload
        else:
            self.payload = {"raw": payload}

    def success(self, data, status=200):
        self.set_status(status)
        self.payload = data

    def error(self, message, status=400):
        self.set_status(status)
        self.payload = {"error": message}

    def log_audit(
        self,
        user,
        action,
        target,
        detail,
        db=None,
        before_state=None,
        after_state=None,
        commit=True,
    ):
        own_db = db is None
        db = db or get_db()
        try:
            db.execute(
                "INSERT INTO audit_log "
                "(user_id, user_name, user_role, action, target, detail, ip_address, before_state, after_state) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    user.get("sub", ""),
                    user.get("name", ""),
                    user.get("role", ""),
                    action,
                    target,
                    detail,
                    "system",
                    _safe_json(before_state),
                    _safe_json(after_state),
                ),
            )
            if own_db or commit:
                db.commit()
        finally:
            if own_db:
                db.close()


def build_worker_id() -> str:
    return (
        os.getenv("VERIFICATION_WORKER_ID")
        or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    )


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, RetryableVerificationWorkerError):
        return True
    if isinstance(exc, TerminalVerificationWorkerError):
        return False
    return isinstance(exc, (TimeoutError, ConnectionError))


def _is_retryable_screening_exception(exc: Exception) -> bool:
    if isinstance(exc, RetryableScreeningWorkerError):
        return True
    if isinstance(exc, TerminalScreeningWorkerError):
        return False
    return isinstance(exc, (TimeoutError, ConnectionError))


def _observability_interval_seconds() -> float:
    try:
        return max(
            float(os.getenv("VERIFICATION_OBSERVABILITY_INTERVAL_SECONDS", DEFAULT_OBSERVABILITY_INTERVAL_SECONDS)),
            5.0,
        )
    except (TypeError, ValueError):
        return DEFAULT_OBSERVABILITY_INTERVAL_SECONDS


def emit_verification_observability_metrics(
    db,
    *,
    worker_id: str,
    force: bool = False,
) -> Dict[str, Any]:
    """Emit PII-safe verification queue gauges for CloudWatch alarms."""

    global _LAST_OBSERVABILITY_EMIT_MONOTONIC
    now_monotonic = time.monotonic()
    if (
        not force
        and now_monotonic - _LAST_OBSERVABILITY_EMIT_MONOTONIC
        < _observability_interval_seconds()
    ):
        return {"emitted": False}

    snapshot = verification_queue_observability_snapshot(db)
    dimensions = {
        "environment": os.getenv("APP_ENV") or os.getenv("ENVIRONMENT", "unknown"),
        "service": "verification-worker",
    }
    emit_cloudwatch_metric_log(
        "VerificationQueueDepth",
        snapshot.get("queue_depth") or 0,
        **dimensions,
    )
    emit_cloudwatch_metric_log(
        "VerificationStuckJobs",
        snapshot.get("stuck_jobs") or 0,
        **dimensions,
    )
    emit_cloudwatch_metric_log(
        "VerificationOldestPendingAgeSeconds",
        snapshot.get("oldest_pending_age_seconds") or 0,
        unit="Seconds",
        **dimensions,
    )
    emit_cloudwatch_metric_log(
        "VerificationFailedJobsLastHour",
        snapshot.get("failed_last_hour") or 0,
        **dimensions,
    )
    logger.info(
        "verification_queue_observability queue_depth=%s stuck_jobs=%s oldest_pending_age_seconds=%s failed_last_hour=%s worker_id=%s",
        snapshot.get("queue_depth"),
        snapshot.get("stuck_jobs"),
        snapshot.get("oldest_pending_age_seconds"),
        snapshot.get("failed_last_hour"),
        worker_id,
    )
    _LAST_OBSERVABILITY_EMIT_MONOTONIC = now_monotonic
    return {"emitted": True, "snapshot": snapshot}


def emit_screening_observability_metrics(
    db,
    *,
    worker_id: str,
    force: bool = False,
) -> Dict[str, Any]:
    """Emit PII-safe screening queue gauges for CloudWatch alarms."""

    global _LAST_SCREENING_OBSERVABILITY_EMIT_MONOTONIC
    now_monotonic = time.monotonic()
    if (
        not force
        and now_monotonic - _LAST_SCREENING_OBSERVABILITY_EMIT_MONOTONIC
        < _observability_interval_seconds()
    ):
        return {"emitted": False}

    snapshot = screening_queue_observability_snapshot(db)
    dimensions = {
        "environment": os.getenv("APP_ENV") or os.getenv("ENVIRONMENT", "unknown"),
        "service": "verification-worker",
    }
    emit_cloudwatch_metric_log(
        "ScreeningQueueDepth",
        snapshot.get("queue_depth") or 0,
        **dimensions,
    )
    emit_cloudwatch_metric_log(
        "ScreeningInProgressJobs",
        snapshot.get("in_progress") or 0,
        **dimensions,
    )
    emit_cloudwatch_metric_log(
        "ScreeningOldestPendingAgeSeconds",
        snapshot.get("oldest_pending_age_seconds") or 0,
        unit="Seconds",
        **dimensions,
    )
    emit_cloudwatch_metric_log(
        "ScreeningFailedJobsLastHour",
        snapshot.get("failed_last_hour") or 0,
        **dimensions,
    )
    logger.info(
        "screening_queue_observability queue_depth=%s in_progress=%s oldest_pending_age_seconds=%s failed_last_hour=%s worker_id=%s",
        snapshot.get("queue_depth"),
        snapshot.get("in_progress"),
        snapshot.get("oldest_pending_age_seconds"),
        snapshot.get("failed_last_hour"),
        worker_id,
    )
    _LAST_SCREENING_OBSERVABILITY_EMIT_MONOTONIC = now_monotonic
    return {"emitted": True, "snapshot": snapshot}


def _safe_emit_worker_metric(metric_name: str, value, *, unit: str = "Count") -> None:
    try:
        emit_cloudwatch_metric_log(
            metric_name,
            value,
            unit=unit,
            environment=os.getenv("APP_ENV") or os.getenv("ENVIRONMENT", "unknown"),
            service="verification-worker",
        )
    except Exception:
        logger.exception("verification_worker_metric_emit_failed metric_name=%s", metric_name)


def default_verification_executor(db, job: Dict[str, Any], worker_id: str) -> Dict[str, Any]:
    """Run the existing synchronous verification path for a claimed job."""
    from server import DocumentVerifyHandler

    handler = _WorkerHandlerShim()
    DocumentVerifyHandler._post_with_db(
        handler,
        job["document_id"],
        SYSTEM_USER,
        db,
        force_sync=True,
        audit_actor_type="system",
        started_trigger="async_verify_worker_started",
        completed_trigger="async_verify_worker_completed",
        audit_detail_extra={"job_id": job["id"], "worker_id": worker_id},
        close_db=False,
    )
    payload = handler.payload or {}
    if handler.status_code >= 500:
        raise RetryableVerificationWorkerError(
            payload.get("error") or f"verification handler returned {handler.status_code}"
        )
    if handler.status_code >= 400:
        raise TerminalVerificationWorkerError(
            payload.get("error") or f"verification handler returned {handler.status_code}"
        )

    status = normalize_verification_state(
        payload.get("verification_status") or payload.get("status")
    )
    if status not in TERMINAL_DOCUMENT_STATES:
        raise TerminalVerificationWorkerError(
            f"verification handler did not produce terminal document state: {status}"
        )
    verification_results = payload.get("verification_results") or {
        "overall": status,
        "checks": payload.get("checks") or [],
    }
    return {
        "verification_status": status,
        "verification_results": verification_results,
        "document_already_updated": True,
    }


VerificationExecutor = Callable[[Any, Dict[str, Any], str], Dict[str, Any]]


def _missing_verification_job_skip_result(
    job: Dict[str, Any],
    *,
    worker_id: str,
    stage: str,
) -> Dict[str, Any]:
    logger.info(
        "verification_job_missing_skip event=verification_job_missing_skip job_id=%s document_id=%s worker_id=%s reason=job_not_found action=skip stage=%s",
        job.get("id"),
        job.get("document_id"),
        worker_id,
        stage,
    )
    return {
        "processed": True,
        "outcome": "skipped",
        "reason": "job_not_found",
        "action": "skip",
        "job_id": job.get("id"),
        "document_id": job.get("document_id"),
        "worker_id": worker_id,
        "stage": stage,
    }


def default_screening_executor(db, job: Dict[str, Any], worker_id: str) -> Dict[str, Any]:
    """Run the submit-time provider screening path outside the HTTP request."""
    from server import process_async_screening_job

    return process_async_screening_job(db, job, worker_id)


ScreeningExecutor = Callable[[Any, Dict[str, Any], str], Dict[str, Any]]


def process_claimed_screening_job(
    db,
    job: Dict[str, Any],
    *,
    worker_id: str,
    screening_executor: Optional[ScreeningExecutor] = None,
) -> Dict[str, Any]:
    executor = screening_executor or default_screening_executor
    try:
        result = executor(db, job, worker_id)
        if result.get("retryable_error"):
            raise RetryableScreeningWorkerError(result.get("error") or "screening provider retry requested")
        if result.get("terminal_error"):
            raise TerminalScreeningWorkerError(result.get("error") or "screening provider terminal failure")
        updated = mark_screening_job_succeeded(db, job["id"], worker_id=worker_id)
        db.commit()
        try:
            timing = screening_job_timing_ms(updated)
            if timing.get("end_to_end_job_ms") is not None:
                _safe_emit_worker_metric(
                    "ScreeningEndToEndJobMs",
                    timing["end_to_end_job_ms"],
                    unit="Milliseconds",
                )
        except Exception:
            logger.exception("screening_worker_timing_metric_failed job_id=%s", job["id"])
        logger.info(
            "screening_worker_job_completed job_id=%s application_id=%s worker_id=%s %s",
            job["id"],
            job.get("application_id"),
            worker_id,
            format_screening_job_timing_log_fields(updated),
        )
        return {
            "processed": True,
            "job_type": "screening",
            "outcome": "succeeded",
            "job": updated,
            "result": result,
        }
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        retryable = _is_retryable_screening_exception(exc)
        try:
            from server import mark_async_screening_failure

            mark_async_screening_failure(
                db,
                job,
                worker_id=worker_id,
                error=str(exc),
                retryable=retryable,
            )
        except Exception:
            logger.exception("screening_worker_failed_state_persist_failed job_id=%s", job.get("id"))
        failed = mark_screening_job_failed(
            db,
            job["id"],
            worker_id=worker_id,
            error=str(exc),
            retryable=retryable,
        )
        db.commit()
        _safe_emit_worker_metric("ScreeningWorkerFailures", 1)
        logger.warning(
            "screening_worker_job_failed job_id=%s application_id=%s retryable=%s error_type=%s %s",
            job["id"],
            job.get("application_id"),
            retryable,
            type(exc).__name__,
            format_screening_job_timing_log_fields(failed),
        )
        return {
            "processed": True,
            "job_type": "screening",
            "outcome": failed["status"],
            "job": failed,
            "retryable": retryable,
            "error": str(exc),
        }


def process_claimed_job(
    db,
    job: Dict[str, Any],
    *,
    worker_id: str,
    verification_executor: Optional[VerificationExecutor] = None,
) -> Dict[str, Any]:
    executor = verification_executor or default_verification_executor
    try:
        get_verification_job_or_raise(db, job["id"])
    except VerificationJobMissing:
        return _missing_verification_job_skip_result(
            job,
            worker_id=worker_id,
            stage="pre_executor",
        )

    try:
        result = executor(db, job, worker_id)
        status = normalize_verification_state(
            result.get("verification_status") or result.get("status")
        )
        if status not in TERMINAL_DOCUMENT_STATES:
            raise TerminalVerificationWorkerError(
                f"worker executor returned non-terminal document state: {status}"
            )
        verification_results = result.get("verification_results") or {
            "overall": status,
            "checks": [],
        }
        try:
            updated = mark_verification_job_succeeded(
                db,
                job["id"],
                worker_id=worker_id,
                verification_status=status,
                verification_results=verification_results,
                transition_document=not bool(result.get("document_already_updated")),
            )
        except VerificationJobMissing:
            try:
                db.rollback()
            except Exception:
                pass
            return _missing_verification_job_skip_result(
                job,
                worker_id=worker_id,
                stage="completion",
            )
        db.commit()
        try:
            timing = verification_job_timing_ms(updated)
            if timing.get("end_to_end_job_ms") is not None:
                _safe_emit_worker_metric(
                    "VerificationEndToEndJobMs",
                    timing["end_to_end_job_ms"],
                    unit="Milliseconds",
                )
        except Exception:
            logger.exception("verification_worker_timing_metric_failed job_id=%s", job["id"])
        logger.info(
            "verification_worker_job_completed job_id=%s document_id=%s status=%s worker_id=%s %s",
            job["id"],
            job.get("document_id"),
            status,
            worker_id,
            format_verification_job_timing_log_fields(updated),
        )
        return {
            "processed": True,
            "outcome": "succeeded",
            "job": updated,
            "verification_status": status,
        }
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        retryable = _is_retryable_exception(exc)
        try:
            failed = mark_verification_job_failed(
                db,
                job["id"],
                worker_id=worker_id,
                error=str(exc),
                retryable=retryable,
            )
        except VerificationJobMissing:
            try:
                db.rollback()
            except Exception:
                pass
            logger.warning(
                "verification_worker_failure_state_missing_job job_id=%s document_id=%s worker_id=%s reason=job_not_found action=preserve_error original_error_type=%s",
                job.get("id"),
                job.get("document_id"),
                worker_id,
                type(exc).__name__,
            )
            raise exc from None
        db.commit()
        _safe_emit_worker_metric("VerificationWorkerFailures", 1)
        logger.warning(
            "verification_worker_job_failed job_id=%s document_id=%s retryable=%s error_type=%s %s",
            job["id"],
            job.get("document_id"),
            retryable,
            type(exc).__name__,
            format_verification_job_timing_log_fields(failed),
        )
        return {
            "processed": True,
            "outcome": failed["status"],
            "job": failed,
            "retryable": retryable,
            "error": str(exc),
        }


def run_once(
    *,
    db=None,
    worker_id: Optional[str] = None,
    verification_executor: Optional[VerificationExecutor] = None,
) -> Dict[str, Any]:
    worker_id = worker_id or build_worker_id()
    own_db = db is None
    db = db or get_db()
    try:
        health = recover_stuck_verification_jobs(db, worker_id=worker_id)
        if health.get("stuck_jobs"):
            logger.warning(format_async_job_health_log_line(health))
        screening_health = recover_stuck_screening_jobs(db, worker_id=worker_id)
        if screening_health.get("stuck_jobs"):
            logger.warning(
                "screening_async_job_health stuck_jobs=%s requeued_jobs=%s failed_jobs=%s worker_id=%s",
                screening_health.get("stuck_jobs"),
                screening_health.get("requeued_jobs"),
                screening_health.get("failed_jobs"),
                worker_id,
            )
        try:
            emit_verification_observability_metrics(db, worker_id=worker_id)
        except Exception:
            logger.exception("verification_observability_emit_failed worker_id=%s", worker_id)
        try:
            emit_screening_observability_metrics(db, worker_id=worker_id)
        except Exception:
            logger.exception("screening_observability_emit_failed worker_id=%s", worker_id)
        screening_job = claim_next_screening_job(db, worker_id)
        db.commit()
        if screening_job:
            logger.info(
                "screening_worker_job_claimed job_id=%s application_id=%s worker_id=%s attempt_count=%s %s",
                screening_job["id"],
                screening_job.get("application_id"),
                worker_id,
                screening_job.get("attempt_count"),
                format_screening_job_timing_log_fields(screening_job),
            )
            result = process_claimed_screening_job(
                db,
                screening_job,
                worker_id=worker_id,
            )
            result["worker_id"] = worker_id
            return result
        job = claim_next_verification_job(db, worker_id)
        db.commit()
        if not job:
            return {
                "processed": False,
                "outcome": "idle",
                "worker_id": worker_id,
                "stuck_jobs_failed": int(health.get("failed_jobs") or 0),
                "screening_stuck_jobs_failed": int(screening_health.get("failed_jobs") or 0),
            }
        logger.info(
            "verification_worker_job_claimed job_id=%s document_id=%s worker_id=%s attempt_count=%s %s",
            job["id"],
            job.get("document_id"),
            worker_id,
            job.get("attempt_count"),
            format_verification_job_timing_log_fields(job),
        )
        result = process_claimed_job(
            db,
            job,
            worker_id=worker_id,
            verification_executor=verification_executor,
        )
        result["worker_id"] = worker_id
        return result
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception("verification_worker_run_once_failed worker_id=%s", worker_id)
        raise
    finally:
        if own_db:
            try:
                db.close()
            except Exception:
                pass


def run_forever(*, worker_id: Optional[str] = None, poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS) -> None:
    worker_id = worker_id or build_worker_id()
    logger.info("verification_worker_started worker_id=%s", worker_id)
    while True:
        result = run_once(worker_id=worker_id)
        if not result.get("processed"):
            time.sleep(max(float(poll_interval), 0.5))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run async verification worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job")
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL_SECONDS)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.once:
        result = run_once(worker_id=args.worker_id)
        logger.info("verification_worker_once_result %s", json.dumps(result, default=str, sort_keys=True))
        return 0

    run_forever(worker_id=args.worker_id, poll_interval=args.poll_interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
