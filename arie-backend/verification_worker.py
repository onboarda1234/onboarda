"""Async document-verification worker runtime.

This is the PR7A runtime counterpart to the PR6 Postgres-backed queue
foundation.  The worker is intentionally a separate process entrypoint for an
ECS Fargate worker service; it does not use in-process callbacks or
``resilience/task_queue.py``.
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
from verification_jobs import (
    format_async_job_health_log_line,
    claim_next_verification_job,
    mark_verification_job_failed,
    mark_verification_job_succeeded,
    recover_stuck_verification_jobs,
)
from verification_state import (
    STATE_FAILED,
    STATE_FLAGGED,
    STATE_VERIFIED,
    normalize_verification_state,
)

logger = logging.getLogger(__name__)

TERMINAL_DOCUMENT_STATES = (STATE_VERIFIED, STATE_FLAGGED, STATE_FAILED)
SYSTEM_USER = {
    "sub": "system:verification-worker",
    "name": "Verification Worker",
    "role": "system",
}


class RetryableVerificationWorkerError(Exception):
    """Raised when the job should be returned to the queue for retry."""


class TerminalVerificationWorkerError(Exception):
    """Raised when the job should fail terminally."""


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


def process_claimed_job(
    db,
    job: Dict[str, Any],
    *,
    worker_id: str,
    verification_executor: Optional[VerificationExecutor] = None,
) -> Dict[str, Any]:
    executor = verification_executor or default_verification_executor
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
        updated = mark_verification_job_succeeded(
            db,
            job["id"],
            worker_id=worker_id,
            verification_status=status,
            verification_results=verification_results,
            transition_document=not bool(result.get("document_already_updated")),
        )
        db.commit()
        logger.info(
            "verification_worker_job_completed job_id=%s document_id=%s status=%s worker_id=%s",
            job["id"],
            job.get("document_id"),
            status,
            worker_id,
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
        failed = mark_verification_job_failed(
            db,
            job["id"],
            worker_id=worker_id,
            error=str(exc),
            retryable=retryable,
        )
        db.commit()
        logger.warning(
            "verification_worker_job_failed job_id=%s document_id=%s retryable=%s error_type=%s",
            job["id"],
            job.get("document_id"),
            retryable,
            type(exc).__name__,
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
        job = claim_next_verification_job(db, worker_id)
        db.commit()
        if not job:
            return {
                "processed": False,
                "outcome": "idle",
                "worker_id": worker_id,
                "stuck_jobs_failed": int(health.get("failed_jobs") or 0),
            }
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


def run_forever(*, worker_id: Optional[str] = None, poll_interval: float = 5.0) -> None:
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
    parser.add_argument("--poll-interval", type=float, default=5.0)
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
