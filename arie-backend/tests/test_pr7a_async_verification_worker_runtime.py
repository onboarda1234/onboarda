"""PR7A async verification worker-runtime guards."""

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._migration_idempotency_helpers import fresh_migration_db


def _seed_doc(db, *, app_id="app_pr7a_worker", doc_id="doc_pr7a_worker", status="pending"):
    db.execute(
        """
        INSERT INTO applications (id, ref, company_name, country, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            f"ARF-2026-{app_id[-8:]}",
            "PR7A Worker Ltd",
            "Mauritius",
            "draft",
            json.dumps({"registered_entity_name": "PR7A Worker Ltd"}),
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
            "cert-inc.pdf",
            "/tmp/cert-inc.pdf",
            128,
            "application/pdf",
            status,
            1,
            1,
        ),
    )
    db.commit()
    doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    app = db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    return app, doc


def _enqueue(db, app, doc):
    from verification_jobs import enqueue_verification_job

    result = enqueue_verification_job(
        db,
        doc,
        app,
        {"sub": "admin001", "name": "Test Admin", "role": "admin"},
    )
    db.commit()
    return result["job"]


def _executor(status):
    def _run(db, job, worker_id):
        return {
            "verification_status": status,
            "verification_results": {
                "overall": status,
                "checks": [{
                    "label": "Synthetic worker check",
                    "type": "test",
                    "result": "pass" if status == "verified" else "warn",
                    "message": f"synthetic {status}",
                }],
            },
        }

    return _run


def _state_transition_details(db):
    rows = db.execute(
        """
        SELECT detail, before_state, after_state
        FROM audit_log
        WHERE action='Document Verification State Changed'
        ORDER BY id ASC
        """,
    ).fetchall()
    return [json.loads(row["detail"]) for row in rows]


def test_worker_claims_queued_job_and_completes_verified_with_system_audit(tmp_path, monkeypatch):
    from verification_worker import run_once

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        app, doc = _seed_doc(db)
        _enqueue(db, app, doc)

        result = run_once(
            db=db,
            worker_id="worker-pr7a-success",
            verification_executor=_executor("verified"),
        )

        assert result["outcome"] == "succeeded"
        job = db.execute("SELECT * FROM verification_jobs WHERE document_id=?", (doc["id"],)).fetchone()
        assert job["status"] == "succeeded"
        assert job["attempt_count"] == 1

        stored = db.execute(
            "SELECT verification_status, verification_results FROM documents WHERE id=?",
            (doc["id"],),
        ).fetchone()
        assert stored["verification_status"] == "verified"
        assert json.loads(stored["verification_results"])["overall"] == "verified"

        details = _state_transition_details(db)
        assert [detail["trigger"] for detail in details] == [
            "async_verify_worker_started",
            "async_verify_worker_completed",
        ]
        assert {detail["actor_type"] for detail in details} == {"system"}
        assert {detail["worker_id"] for detail in details} == {"worker-pr7a-success"}


def test_verification_job_timing_metrics_are_internally_consistent(tmp_path, monkeypatch):
    from verification_jobs import (
        enqueue_verification_job,
        format_verification_job_timing_log_fields,
        serialize_verification_job,
        verification_job_timing_ms,
        verification_status_for_document,
    )

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        app, doc = _seed_doc(db)
        job = enqueue_verification_job(
            db,
            doc,
            app,
            {"sub": "admin001", "name": "Test Admin", "role": "admin"},
        )["job"]
        db.execute(
            """
            UPDATE verification_jobs
               SET created_at=?, locked_at=?, completed_at=?
             WHERE id=?
            """,
            (
                "2026-01-01 00:00:00.000000",
                "2026-01-01 00:00:02.500000",
                "2026-01-01 00:00:08.000000",
                job["id"],
            ),
        )
        db.commit()

        stored = serialize_verification_job(
            db.execute("SELECT * FROM verification_jobs WHERE id=?", (job["id"],)).fetchone()
        )

        assert verification_job_timing_ms(stored) == {
            "queue_wait_ms": 2500,
            "execution_ms": 5500,
            "end_to_end_job_ms": 8000,
        }
        assert stored["timing_ms"] == {
            "queue_wait_ms": 2500,
            "execution_ms": 5500,
            "end_to_end_job_ms": 8000,
        }
        assert verification_status_for_document(db, doc["id"])["verification_job"]["timing_ms"] == {
            "queue_wait_ms": 2500,
            "execution_ms": 5500,
            "end_to_end_job_ms": 8000,
        }
        assert format_verification_job_timing_log_fields(stored) == (
            "queue_wait_ms=2500 execution_ms=5500 end_to_end_job_ms=8000"
        )


def test_two_workers_claim_distinct_jobs_safely(tmp_path, monkeypatch):
    from verification_jobs import claim_next_verification_job, enqueue_verification_job

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        app_a, doc_a = _seed_doc(
            db,
            app_id="app_pr7a_worker_a",
            doc_id="doc_pr7a_worker_a",
        )
        app_b, doc_b = _seed_doc(
            db,
            app_id="app_pr7a_worker_b",
            doc_id="doc_pr7a_worker_b",
        )
        enqueue_verification_job(
            db,
            doc_a,
            app_a,
            {"sub": "admin001", "name": "Test Admin", "role": "admin"},
        )
        enqueue_verification_job(
            db,
            doc_b,
            app_b,
            {"sub": "admin001", "name": "Test Admin", "role": "admin"},
        )

        first = claim_next_verification_job(db, "worker-pr7b-1")
        second = claim_next_verification_job(db, "worker-pr7b-2")
        db.commit()

        assert first is not None
        assert second is not None
        assert first["id"] != second["id"]
        assert {first["locked_by"], second["locked_by"]} == {
            "worker-pr7b-1",
            "worker-pr7b-2",
        }
        assert first["status"] == "in_progress"
        assert second["status"] == "in_progress"


def test_worker_completes_flagged_path(tmp_path, monkeypatch):
    from verification_worker import run_once

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        app, doc = _seed_doc(db)
        _enqueue(db, app, doc)

        result = run_once(
            db=db,
            worker_id="worker-pr7a-flagged",
            verification_executor=_executor("flagged"),
        )

        assert result["outcome"] == "succeeded"
        stored = db.execute(
            "SELECT verification_status, verification_results FROM documents WHERE id=?",
            (doc["id"],),
        ).fetchone()
        assert stored["verification_status"] == "flagged"
        assert json.loads(stored["verification_results"])["overall"] == "flagged"


def test_worker_retryable_failure_returns_job_to_queue_then_reclaims(tmp_path, monkeypatch):
    from verification_worker import RetryableVerificationWorkerError, run_once

    calls = {"count": 0}

    def flaky_executor(db, job, worker_id):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RetryableVerificationWorkerError("transient provider timeout")
        return {
            "verification_status": "verified",
            "verification_results": {"overall": "verified", "checks": []},
        }

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        app, doc = _seed_doc(db)
        _enqueue(db, app, doc)

        first = run_once(
            db=db,
            worker_id="worker-pr7a-retry",
            verification_executor=flaky_executor,
        )
        assert first["outcome"] == "retrying"
        retrying = db.execute("SELECT * FROM verification_jobs WHERE document_id=?", (doc["id"],)).fetchone()
        assert retrying["status"] == "retrying"
        assert retrying["attempt_count"] == 1
        assert retrying["locked_by"] is None
        assert db.execute(
            "SELECT verification_status FROM documents WHERE id=?",
            (doc["id"],),
        ).fetchone()["verification_status"] == "pending"

        db.execute(
            "UPDATE verification_jobs SET run_after=datetime('now', '-1 second') WHERE id=?",
            (retrying["id"],),
        )
        db.commit()

        second = run_once(
            db=db,
            worker_id="worker-pr7a-retry",
            verification_executor=flaky_executor,
        )
        assert second["outcome"] == "succeeded"
        succeeded = db.execute("SELECT * FROM verification_jobs WHERE id=?", (retrying["id"],)).fetchone()
        assert succeeded["status"] == "succeeded"
        assert succeeded["attempt_count"] == 2


def test_worker_recovers_stale_in_progress_job_after_worker_kill(tmp_path, monkeypatch):
    from verification_jobs import MAX_IN_PROGRESS_SECONDS, claim_next_verification_job, db_timestamp, utc_now
    from verification_worker import run_once

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        app, doc = _seed_doc(db)
        _enqueue(db, app, doc)
        killed_claim = claim_next_verification_job(db, "worker-pr7a-killed")
        stale_timestamp = db_timestamp(
            utc_now() - timedelta(seconds=MAX_IN_PROGRESS_SECONDS + 30)
        )
        db.execute(
            "UPDATE verification_jobs SET locked_at=? WHERE id=?",
            (stale_timestamp, killed_claim["id"]),
        )
        db.commit()

        result = run_once(
            db=db,
            worker_id="worker-pr7a-reclaimer",
            verification_executor=_executor("verified"),
        )

        assert result["outcome"] == "succeeded"
        recovered = db.execute("SELECT * FROM verification_jobs WHERE id=?", (killed_claim["id"],)).fetchone()
        assert recovered["status"] == "succeeded"
        assert recovered["attempt_count"] == 2
        assert recovered["locked_by"] == "worker-pr7a-reclaimer"
        details = _state_transition_details(db)
        assert "async_verify_worker_reclaimed" in [detail["trigger"] for detail in details]


def test_worker_terminal_failure_marks_document_failed(tmp_path, monkeypatch):
    from verification_worker import TerminalVerificationWorkerError, run_once

    def terminal_executor(db, job, worker_id):
        raise TerminalVerificationWorkerError("document no longer exists")

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        app, doc = _seed_doc(db)
        _enqueue(db, app, doc)

        result = run_once(
            db=db,
            worker_id="worker-pr7a-terminal",
            verification_executor=terminal_executor,
        )

        assert result["outcome"] == "failed"
        stored = db.execute(
            "SELECT verification_status, verification_results FROM documents WHERE id=?",
            (doc["id"],),
        ).fetchone()
        assert stored["verification_status"] == "failed"
        results = json.loads(stored["verification_results"])
        assert results["overall"] == "failed"
        assert results["system_warning"] == "async_verification_job_failed"


def test_default_executor_reuses_sync_handler_path_with_async_bypass(monkeypatch):
    import server
    from verification_worker import default_verification_executor

    calls = []

    def fake_post_with_db(self, doc_id, user, db, **kwargs):
        calls.append({"doc_id": doc_id, "user": user, "kwargs": kwargs})
        self.success({
            "verification_status": "verified",
            "verification_results": {"overall": "verified", "checks": []},
        })

    monkeypatch.setattr(server.DocumentVerifyHandler, "_post_with_db", fake_post_with_db)

    result = default_verification_executor(
        object(),
        {"id": "vjob_default", "document_id": "doc_default"},
        "worker-pr7a-default",
    )

    assert result["verification_status"] == "verified"
    assert result["document_already_updated"] is True
    assert calls[0]["kwargs"]["force_sync"] is True
    assert calls[0]["kwargs"]["audit_actor_type"] == "system"
    assert calls[0]["kwargs"]["completed_trigger"] == "async_verify_worker_completed"
    assert calls[0]["kwargs"]["audit_detail_extra"] == {
        "job_id": "vjob_default",
        "worker_id": "worker-pr7a-default",
    }
    assert calls[0]["kwargs"]["close_db"] is False


def test_default_executor_runs_real_sync_handler_contract_without_closing_worker_db(tmp_path, monkeypatch):
    import server
    from verification_worker import default_verification_executor

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        app, doc = _seed_doc(db)

        monkeypatch.setattr(server, "HAS_DOC_VERIFICATION", True)
        monkeypatch.setattr(
            server,
            "verify_document_layered",
            lambda **_: {
                "overall": "verified",
                "checks": [{
                    "label": "Synthetic worker check",
                    "type": "test",
                    "result": "pass",
                    "message": "worker contract smoke",
                }],
            },
        )

        result = default_verification_executor(
            db,
            {"id": "vjob_real_contract", "document_id": doc["id"]},
            "worker-pr6-real-contract",
        )

        assert result["verification_status"] == "verified"
        assert result["document_already_updated"] is True

        stored = db.execute(
            "SELECT verification_status, verification_results FROM documents WHERE id=?",
            (doc["id"],),
        ).fetchone()
        assert stored["verification_status"] == "verified"
        assert json.loads(stored["verification_results"])["overall"] == "verified"

        details = _state_transition_details(db)
        assert [detail["trigger"] for detail in details] == [
            "async_verify_worker_started",
            "async_verify_worker_completed",
        ]
        assert {detail["actor_type"] for detail in details} == {"system"}
        assert {detail["worker_id"] for detail in details} == {"worker-pr6-real-contract"}

        db.execute("SELECT COUNT(*) AS c FROM documents").fetchone()


def test_worker_runtime_does_not_use_in_process_queue_or_screening_abstraction():
    source = Path(__file__).resolve().parents[1].joinpath("verification_worker.py").read_text(
        encoding="utf-8"
    )
    assert "resilience.task_queue" not in source
    assert "screening_complyadvantage" not in source
    assert "ComplyAdvantage" not in source
