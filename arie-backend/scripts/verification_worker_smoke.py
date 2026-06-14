#!/usr/bin/env python3
"""Run a safe synthetic verification-worker smoke against the configured DB.

The smoke inserts a clearly named synthetic application/document, enqueues a
verification job, claims it with the production queue claim function before the
job is committed, and completes it through ``verification_worker`` with a
synthetic executor. It does not call Sumsub, ComplyAdvantage, Anthropic, S3, or
payment/email providers.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import get_db  # noqa: E402
from verification_jobs import (  # noqa: E402
    claim_next_verification_job,
    enqueue_verification_job,
)
from verification_worker import process_claimed_job  # noqa: E402


def _safe_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _seed_smoke_records(db, run_id: str) -> Dict[str, Any]:
    client_id = f"pr6_smoke_client_{run_id}"
    app_id = f"pr6_smoke_app_{run_id}"
    doc_id = f"pr6_smoke_doc_{run_id}"
    app_ref = f"ARF-PR6-SMOKE-{run_id.upper()}"
    db.execute(
        """
        INSERT INTO clients (id, email, password_hash, company_name, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            client_id,
            f"pr6-smoke-{run_id}@example.invalid",
            "synthetic-worker-smoke-no-login",
            "PR6 Worker Smoke Ltd",
            "active",
        ),
    )
    db.execute(
        """
        INSERT INTO applications (id, ref, client_id, company_name, country, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            app_ref,
            client_id,
            "PR6 Worker Smoke Ltd",
            "Mauritius",
            "draft",
            _safe_json({"registered_entity_name": "PR6 Worker Smoke Ltd", "fixture_type": "pr6_worker_smoke"}),
        ),
    )
    db.execute(
        """
        INSERT INTO documents (
            id, application_id, doc_type, doc_name, file_path, file_size,
            mime_type, verification_status, is_current, version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            app_id,
            "cert_inc",
            "pr6-worker-smoke.pdf",
            "/tmp/pr6-worker-smoke.pdf",
            64,
            "application/pdf",
            "pending",
            1,
            1,
        ),
    )
    app = db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    return {
        "app": app,
        "doc": doc,
        "client_id": client_id,
        "application_id": app_id,
        "application_ref": app_ref,
        "document_id": doc_id,
    }


def _synthetic_executor(db, job: Dict[str, Any], worker_id: str) -> Dict[str, Any]:
    return {
        "verification_status": "verified",
        "verification_results": {
            "overall": "verified",
            "ai_source": "synthetic_pr6_worker_smoke",
            "checks": [{
                "label": "Synthetic worker runtime smoke",
                "type": "runtime",
                "result": "pass",
                "message": "Verification worker processed a safe synthetic job without provider calls.",
            }],
            "worker_id": worker_id,
            "job_id": job.get("id"),
        },
    }


def _cleanup_smoke_records(db, *, client_id: str, application_id: str, document_id: str, job_id: str) -> None:
    db.execute("DELETE FROM audit_log WHERE detail LIKE ?", (f"%{job_id}%",))
    db.execute("DELETE FROM verification_jobs WHERE id=?", (job_id,))
    db.execute("DELETE FROM documents WHERE id=?", (document_id,))
    db.execute("DELETE FROM applications WHERE id=?", (application_id,))
    db.execute("DELETE FROM clients WHERE id=?", (client_id,))
    db.commit()


def run_smoke(
    *,
    db=None,
    run_id: Optional[str] = None,
    worker_id: Optional[str] = None,
    cleanup: bool = False,
) -> Dict[str, Any]:
    own_db = db is None
    db = db or get_db()
    run_id = (run_id or uuid.uuid4().hex[:10]).lower()
    worker_id = worker_id or f"pr6-smoke-{run_id}"
    try:
        seeded = _seed_smoke_records(db, run_id)
        job = enqueue_verification_job(
            db,
            seeded["doc"],
            seeded["app"],
            {"sub": "system:pr6-smoke", "name": "PR6 Worker Smoke", "role": "system"},
            request_id=f"pr6-smoke-{run_id}",
            ip_address="system",
        )["job"]
        claimed = claim_next_verification_job(db, worker_id)
        if not claimed or claimed.get("id") != job.get("id"):
            raise RuntimeError("Synthetic verification job was not claimed by smoke worker")
        db.commit()
        result = process_claimed_job(
            db,
            claimed,
            worker_id=worker_id,
            verification_executor=_synthetic_executor,
        )
        stored_doc = db.execute(
            "SELECT verification_status, verification_results FROM documents WHERE id=?",
            (seeded["document_id"],),
        ).fetchone()
        stored_job = db.execute(
            "SELECT status, attempt_count, locked_by FROM verification_jobs WHERE id=?",
            (job["id"],),
        ).fetchone()
        payload = {
            "status": "passed" if result.get("outcome") == "succeeded" else "failed",
            "run_id": run_id,
            "worker_id": worker_id,
            "application_id": seeded["application_id"],
            "application_ref": seeded["application_ref"],
            "document_id": seeded["document_id"],
            "job_id": job["id"],
            "job_status": stored_job["status"] if stored_job else None,
            "attempt_count": stored_job["attempt_count"] if stored_job else None,
            "locked_by": stored_job["locked_by"] if stored_job else None,
            "document_status": stored_doc["verification_status"] if stored_doc else None,
            "provider_calls": "none",
            "result": {
                "processed": result.get("processed"),
                "outcome": result.get("outcome"),
                "verification_status": result.get("verification_status"),
            },
        }
        if cleanup:
            _cleanup_smoke_records(
                db,
                client_id=seeded["client_id"],
                application_id=seeded["application_id"],
                document_id=seeded["document_id"],
                job_id=job["id"],
            )
            payload["cleanup"] = "completed"
        else:
            payload["cleanup"] = "not_requested"
        return payload
    finally:
        if own_db:
            db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--worker-id", default="")
    parser.add_argument("--cleanup", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    payload = run_smoke(
        run_id=args.run_id or None,
        worker_id=args.worker_id or None,
        cleanup=args.cleanup,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("status") == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
