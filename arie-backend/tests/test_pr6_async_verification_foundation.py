"""PR6 async verification foundation guards."""

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._migration_idempotency_helpers import fresh_migration_db, table_exists


def _seed_doc(db, *, app_id="app_pr6_async", doc_id="doc_pr6_async", status="pending"):
    db.execute(
        """
        INSERT INTO applications (id, ref, company_name, country, status, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            "ARF-2026-PR6",
            "PR6 Async Ltd",
            "Mauritius",
            "draft",
            json.dumps({"registered_entity_name": "PR6 Async Ltd"}),
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


def test_verification_jobs_schema_is_in_fresh_schema_and_inline_repair(tmp_path, monkeypatch):
    import db as db_module

    assert "CREATE TABLE IF NOT EXISTS verification_jobs" in db_module._get_sqlite_schema()
    assert "CREATE TABLE IF NOT EXISTS verification_jobs" in db_module._get_postgres_schema()
    assert "uq_verification_jobs_active_document" in db_module._get_sqlite_schema()
    assert "uq_verification_jobs_active_document" in db_module._get_postgres_schema()

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        assert table_exists(db, "verification_jobs")


def test_async_verify_flag_is_backend_only_and_default_off():
    from environment import (
        CLIENT_SAFE_UPLOAD_LATENCY_FLAGS,
        FeatureFlags,
        UPLOAD_LATENCY_FLAGS,
        _DEFAULT_FLAGS,
        get_environment_info,
    )

    assert "FF_ASYNC_VERIFY" in UPLOAD_LATENCY_FLAGS
    assert "FF_ASYNC_VERIFY" not in CLIENT_SAFE_UPLOAD_LATENCY_FLAGS
    for env, defaults in _DEFAULT_FLAGS.items():
        assert defaults["FF_ASYNC_VERIFY"] is False, env

    ff = FeatureFlags(env="staging")
    assert ff.is_enabled("FF_ASYNC_VERIFY") is False
    info = get_environment_info()
    assert "FF_ASYNC_VERIFY" not in info["features"]
    assert "FF_ASYNC_VERIFY" not in info["upload_latency_flags"]


def test_enqueue_verification_job_is_idempotent_and_audited(tmp_path, monkeypatch):
    from verification_jobs import enqueue_verification_job, verification_status_for_document

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        app, doc = _seed_doc(db)
        user = {"sub": "admin001", "name": "Test Admin", "role": "admin"}

        first = enqueue_verification_job(db, doc, app, user, request_id="req-1", ip_address="127.0.0.1")
        second = enqueue_verification_job(db, doc, app, user, request_id="req-1", ip_address="127.0.0.1")
        db.commit()

        assert first["created"] is True
        assert second["created"] is False
        assert first["job"]["id"] == second["job"]["id"]
        assert first["job"]["status"] == "pending"

        rows = db.execute("SELECT * FROM verification_jobs WHERE document_id=?", (doc["id"],)).fetchall()
        assert len(rows) == 1

        audit = db.execute(
            "SELECT action, detail, after_state FROM audit_log WHERE action=?",
            ("Document Verification Job Enqueued",),
        ).fetchall()
        assert len(audit) == 1
        detail = json.loads(audit[0]["detail"])
        assert detail["event"] == "document_verification_job_enqueued"
        assert detail["actor_type"] == "user"
        assert detail["job_id"] == first["job"]["id"]

        status_payload = verification_status_for_document(db, doc["id"])
        assert status_payload["verification_status"] == "pending"
        assert status_payload["verification_job"]["id"] == first["job"]["id"]
        assert status_payload["async_sla"]["max_pending_seconds"] == 900


def test_enqueue_resets_verified_document_to_pending_with_user_audit(tmp_path, monkeypatch):
    from verification_jobs import enqueue_verification_job

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        app, doc = _seed_doc(db, status="verified")
        user = {"sub": "admin001", "name": "Test Admin", "role": "admin"}

        result = enqueue_verification_job(db, doc, app, user, request_id="req-reset", ip_address="127.0.0.1")
        db.commit()

        assert result["created"] is True
        stored = db.execute("SELECT verification_status FROM documents WHERE id=?", (doc["id"],)).fetchone()
        assert stored["verification_status"] == "pending"

        transition = db.execute(
            """
            SELECT detail, before_state, after_state
            FROM audit_log
            WHERE action='Document Verification State Changed'
            """,
        ).fetchone()
        assert transition is not None
        detail = json.loads(transition["detail"])
        assert detail["actor_type"] == "user"
        assert detail["trigger"] == "async_verify_enqueued"
        assert detail["from"] == "verified"
        assert detail["to"] == "pending"
        assert json.loads(transition["after_state"])["verification_status"] == "pending"


def test_worker_claim_uses_system_audit_and_marks_document_in_progress(tmp_path, monkeypatch):
    from verification_jobs import claim_next_verification_job, enqueue_verification_job

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        app, doc = _seed_doc(db)
        enqueue_verification_job(db, doc, app, {"sub": "admin001", "name": "Test Admin", "role": "admin"})

        claimed = claim_next_verification_job(db, "worker-pr6-1")
        db.commit()

        assert claimed["status"] == "in_progress"
        assert claimed["locked_by"] == "worker-pr6-1"
        assert claimed["attempt_count"] == 1
        assert claim_next_verification_job(db, "worker-pr6-2") is None

        stored = db.execute("SELECT verification_status FROM documents WHERE id=?", (doc["id"],)).fetchone()
        assert stored["verification_status"] == "in_progress"

        transitions = db.execute(
            """
            SELECT user_role, detail, before_state, after_state
            FROM audit_log
            WHERE action='Document Verification State Changed'
            """,
        ).fetchall()
        assert len(transitions) == 1
        assert transitions[0]["user_role"] == "system"
        detail = json.loads(transitions[0]["detail"])
        assert detail["actor_type"] == "system"
        assert detail["trigger"] == "async_verify_worker_started"
        assert detail["worker_id"] == "worker-pr6-1"
        assert json.loads(transitions[0]["after_state"])["verification_status"] == "in_progress"


def test_worker_failure_preserves_document_compatibility_fields(tmp_path, monkeypatch):
    from verification_jobs import (
        claim_next_verification_job,
        enqueue_verification_job,
        mark_verification_job_failed,
    )

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        app, doc = _seed_doc(db)
        enqueue_verification_job(db, doc, app, {"sub": "admin001", "name": "Test Admin", "role": "admin"})
        claimed = claim_next_verification_job(db, "worker-pr6-fail")

        failed = mark_verification_job_failed(
            db,
            claimed["id"],
            worker_id="worker-pr6-fail",
            error="synthetic terminal worker failure",
            retryable=False,
        )
        db.commit()

        assert failed["status"] == "failed"
        stored = db.execute(
            "SELECT verification_status, verification_results, verified_at FROM documents WHERE id=?",
            (doc["id"],),
        ).fetchone()
        assert stored["verification_status"] == "failed"
        assert stored["verified_at"]
        results = json.loads(stored["verification_results"])
        assert results["overall"] == "failed"
        assert results["system_warning"] == "async_verification_job_failed"


def test_stuck_job_detection_has_numeric_thresholds_and_parseable_log(tmp_path, monkeypatch):
    from verification_jobs import (
        MAX_IN_PROGRESS_SECONDS,
        async_verify_sla_config,
        db_timestamp,
        find_stuck_verification_jobs,
        format_async_job_health_log_line,
        mark_stuck_verification_jobs_failed,
        utc_now,
    )

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        app, doc = _seed_doc(db)
        old_locked_at = db_timestamp(utc_now() - timedelta(seconds=MAX_IN_PROGRESS_SECONDS + 30))
        db.execute(
            """
            INSERT INTO verification_jobs
            (id, document_id, application_id, status, attempt_count, max_attempts, locked_by, locked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("vjob_stuck", doc["id"], app["id"], "in_progress", 1, 3, "worker-old", old_locked_at),
        )
        db.commit()

        stuck = find_stuck_verification_jobs(db)
        assert [job["id"] for job in stuck] == ["vjob_stuck"]

        summary = mark_stuck_verification_jobs_failed(db, worker_id="worker-sweeper")
        db.commit()
        assert summary["stuck_jobs"] == 1
        assert summary["failed_jobs"] == 1

        line = format_async_job_health_log_line(summary)
        assert "verification_async_job_health " in line
        assert "stuck_jobs=1" in line
        assert "failed_jobs=1" in line
        assert "max_pending_seconds=900" in line
        assert async_verify_sla_config()["stuck_job_threshold_seconds"] == 1200


def test_worker_claim_source_uses_postgres_skip_locked():
    source = Path(__file__).resolve().parents[1].joinpath("verification_jobs.py").read_text(encoding="utf-8")
    assert "FOR UPDATE SKIP LOCKED" in source


def test_document_verify_remains_synchronous_while_uploads_can_enqueue_status_jobs():
    source = Path(__file__).resolve().parents[1].joinpath("server.py").read_text(encoding="utf-8")
    assert "/api/documents/([^/]+)/verification-status" in source
    assert 'flags.is_enabled("FF_ASYNC_VERIFY")' not in source
    assert "enqueue_verification_job" in source
    verify_handler = source.split("class DocumentVerifyHandler", 1)[1].split(
        "class DocumentVerificationStatusHandler", 1
    )[0]
    assert "enqueue_verification_job" not in verify_handler
