"""Focused cleanup/fingerprint tests for the renewal staging harness."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import uuid

import pytest

from environment import MONITORING_FEATURE_FLAGS
import monitoring_document_renewal_staging_cleanup as cleanup_module
from fixtures.document_renewal_staging_seeder import (
    seed_document_renewal_staging_fixture,
)
from monitoring_document_renewal_staging_cleanup import (
    CLEANUP_CONFIRMATION,
    CLEANUP_DELETE_ORDER,
    RenewalHarnessCleanupError,
    _cleanup_local_artifact,
    _cleanup_s3_artifact,
    _database_identity_fingerprint,
    build_renewal_harness_cleanup_plan,
    capture_renewal_harness_fingerprint,
    cleanup_renewal_harness_operational_state,
    compare_renewal_harness_baseline,
)
from regulated_deletion import DISPOSABLE_POSTGRES_TEST_DATABASE_PREFIX


FLAGS_OFF = {name: False for name in MONITORING_FEATURE_FLAGS}
STAMP = "2026-08-03T08:00:00+00:00"


def _postgres_dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get(
        "DATABASE_URL_TEST"
    )


@pytest.fixture
def disposable_postgres_cleanup_db(monkeypatch):
    base_dsn = _postgres_dsn()
    if not base_dsn:
        pytest.skip("No disposable PostgreSQL test DSN")
    import psycopg2
    from psycopg2 import sql

    database_name = (
        f"{DISPOSABLE_POSTGRES_TEST_DATABASE_PREFIX}renewal_harness_"
        f"{uuid.uuid4().hex[:12]}"
    )
    dsn_parts = urlsplit(base_dsn)
    if dsn_parts.scheme.lower() not in {"postgres", "postgresql"}:
        pytest.skip("Disposable PostgreSQL harness test requires a URI-form DSN")
    admin = psycopg2.connect(base_dsn)
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
        )
    fresh_dsn = urlunsplit(
        (
            dsn_parts.scheme,
            dsn_parts.netloc,
            "/" + database_name,
            dsn_parts.query,
            "",
        )
    )
    original = {
        key: os.environ.get(key)
        for key in ("DATABASE_URL", "TEST_POSTGRES_DSN", "ENVIRONMENT")
    }
    connection = None
    try:
        monkeypatch.setenv("DATABASE_URL", fresh_dsn)
        monkeypatch.setenv("TEST_POSTGRES_DSN", fresh_dsn)
        monkeypatch.setenv("ENVIRONMENT", "testing")
        for key in (
            "PRODUCTION",
            "IS_PRODUCTION",
            "STAGING",
            "IS_STAGING",
            "APP_ENV",
            "DEPLOYMENT_ENVIRONMENT",
        ):
            monkeypatch.delenv(key, raising=False)
        import config as config_module
        import db as db_module

        importlib.reload(config_module)
        importlib.reload(db_module)
        db_module.init_db()
        connection = db_module.get_db()
        yield connection, db_module
    finally:
        if connection is not None:
            connection.close()
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            import config as config_module
            import db as db_module

            importlib.reload(config_module)
            importlib.reload(db_module)
        finally:
            try:
                with admin.cursor() as cursor:
                    cursor.execute(
                        sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                            sql.Identifier(database_name)
                        )
                    )
            finally:
                admin.close()


def test_cleanup_database_fingerprint_rejects_target_query_overrides():
    with pytest.raises(RenewalHarnessCleanupError, match="complete PostgreSQL"):
        _database_identity_fingerprint(
            "postgresql://user:password@approved.internal/regmind_staging"
            "?host=production.internal&dbname=regmind_production"
        )


def test_cleanup_cli_redacts_unexpected_connection_failure(monkeypatch, capsys):
    import db as db_module

    sentinel = "postgresql://secret-user:secret-password@private-host/staging"

    def fail_get_db():
        raise RuntimeError(sentinel)

    monkeypatch.setattr(db_module, "get_db", fail_get_db)
    assert cleanup_module.main(["snapshot", "--run-id", "fixture-run-redaction"]) == 2
    output = capsys.readouterr().out
    assert sentinel not in output
    payload = json.loads(output)
    assert payload["error_code"] == "cleanup_internal_error"
    assert payload["error"] == "unexpected cleanup failure"
    assert payload["error_type"] == "RuntimeError"


def test_cleanup_cli_preserves_allowlisted_contract_error(monkeypatch, capsys):
    import db as db_module

    class Db:
        def rollback(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(db_module, "get_db", Db)
    monkeypatch.setattr(
        cleanup_module,
        "_load_cli_fixture",
        lambda _db: (_ for _ in ()).throw(
            RenewalHarnessCleanupError("fixture baseline is not exact")
        ),
    )
    assert cleanup_module.main(["plan", "--run-id", "fixture-run-contract"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "cleanup_contract_error"
    assert payload["error"] == "fixture baseline is not exact"


def test_cleanup_target_is_pinned_to_reviewed_fixture_manifest(cleanup_db):
    db, _tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    redirected = dict(fixture)
    redirected["application_id"] = "another-is-fixture-application"

    with pytest.raises(
        RenewalHarnessCleanupError,
        match="reviewed manifest at application_id",
    ):
        build_renewal_harness_cleanup_plan(
            db,
            redirected,
            run_id="fixture-run-manifest-pinning",
        )


@pytest.mark.parametrize(
    "override",
    (
        {"artifact_client": object()},
        {"audit_writer": lambda *_args, **_kwargs: "fake-evidence"},
        {"allow_test_database": True},
    ),
)
def test_staging_cleanup_rejects_dependency_and_test_overrides(
    cleanup_db,
    monkeypatch,
    override,
):
    db, _tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    plan = build_renewal_harness_cleanup_plan(
        db,
        fixture,
        run_id="fixture-run-staging-dependency-guard",
    )
    monkeypatch.setattr(
        cleanup_module,
        "_assert_cleanup_environment",
        lambda *_args, **_kwargs: "staging",
    )

    arguments = {
        "run_id": plan["run_id"],
        "expected_plan_fingerprint": plan["fingerprint"],
        "actor_id": "system:fixture-cleanup-test",
        "confirmation": CLEANUP_CONFIRMATION,
    }
    arguments.update(override)
    with pytest.raises(RenewalHarnessCleanupError, match="staging cleanup"):
        cleanup_renewal_harness_operational_state(db, fixture, **arguments)


@pytest.fixture
def cleanup_db(tmp_path, monkeypatch):
    path = str(tmp_path / "document-renewal-cleanup.db")
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("DB_PATH", path)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import config
    import db as db_module

    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "DATABASE_URL", "", raising=False)
    monkeypatch.setattr(config, "USE_POSTGRES", False, raising=False)
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(db_module, "DB_PATH", path)
    monkeypatch.setattr(db_module, "DATABASE_URL", "", raising=False)
    monkeypatch.setattr(db_module, "USE_POSTGRESQL", False)
    db_module.init_db()
    connection = db_module.get_db()
    try:
        yield connection, tmp_path
    finally:
        connection.close()


def _seed_fixture(db):
    return seed_document_renewal_staging_fixture(dry_run=False, db=db)


def _seed_operational_graph(db, fixture, tmp_path):
    request_id = fixture["request_id"]
    upload_id = "b" * 32
    cleanup_id = "cleanup-fixture-upload-v1"
    file_data = b"safe synthetic renewal candidate"
    digest = hashlib.sha256(file_data).hexdigest()
    candidate_dir = tmp_path / "monitoring-renewal-candidates"
    candidate_dir.mkdir(mode=0o700)
    candidate = candidate_dir / f"{upload_id}.pdf"
    candidate.write_bytes(file_data)
    storage_key = f"local://monitoring-renewal-candidates/{candidate.name}"

    notification = db.execute(
        """
        INSERT INTO client_notifications
            (application_id,client_id,notification_type,title,message,
             documents_list,read_status,created_at)
        VALUES (?,?,'document_renewal_request','Synthetic renewal',
                'Upload the synthetic passport','[\"passport\"]',FALSE,?)
        RETURNING id
        """,
        (fixture["application_id"], fixture["customer_id"], STAMP),
    ).fetchone()
    notification_id = notification["id"]
    db.execute(
        """
        INSERT INTO monitoring_document_renewal_requests
            (request_id,application_id,customer_id,person_id,person_type,
             document_id,document_type,document_slot_key,document_version,
             monitoring_alert_id,request_reason,request_status,created_at,
             due_date,created_by,sent_at,updated_at,updated_by,revision,
             contract_version,eligibility_fingerprint)
        VALUES (?,?,?,?,?,?,?,?,?,?,'expired','upload_received',?,?,?, ?,?,?,3,
                'monitoring_document_renewal_request_v1',?)
        """,
        (
            request_id,
            fixture["application_id"],
            fixture["customer_id"],
            fixture["person_id"],
            fixture["person_type"],
            fixture["document_id"],
            fixture["document_type"],
            fixture["document_slot_key"],
            fixture["document_version"],
            fixture["alert_id"],
            STAMP,
            "2026-08-24",
            "system:fixture-harness",
            STAMP,
            STAMP,
            "fixture-client",
            "a" * 64,
        ),
    )
    event_payloads = (
        ("request_created", {"request_reason": "expired"}),
        (
            "request_sent",
            {
                "portal_notification_id": str(notification_id),
                "delivery_channel": "client_portal",
            },
        ),
        (
            "upload_received",
            {
                "upload_id": upload_id,
                "file_sha256": digest,
                "staged_only": True,
            },
        ),
    )
    for sequence, (event_type, payload) in enumerate(event_payloads, 1):
        db.execute(
            """
            INSERT INTO monitoring_document_renewal_events
                (event_id,request_id,event_sequence,event_type,event_key,payload,
                 created_at,created_by,due_date_snapshot)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                f"fixture-event-{sequence}",
                request_id,
                sequence,
                event_type,
                f"fixture:{request_id}:{event_type}",
                json.dumps(payload, sort_keys=True),
                STAMP,
                "system:fixture-harness",
                "2026-08-24",
            ),
        )
    db.execute(
        """
        INSERT INTO monitoring_document_renewal_uploads
            (upload_id,request_id,application_id,customer_id,original_filename,
             storage_key,file_size,mime_type,file_sha256,uploaded_at,uploaded_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            upload_id,
            request_id,
            fixture["application_id"],
            fixture["customer_id"],
            "synthetic-passport.pdf",
            storage_key,
            len(file_data),
            "application/pdf",
            digest,
            STAMP,
            fixture["customer_id"],
        ),
    )
    db.execute(
        """
        INSERT INTO monitoring_document_renewal_upload_bindings
            (upload_id,renewal_request_id,application_id,customer_id,person_id,
             person_type,original_document_id,original_document_version,
             uploaded_document_id,document_type,upload_timestamp,uploaded_by,
             binding_status,contract_version,binding_fingerprint)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'bound',
                'monitoring_document_renewal_upload_binding_v1',?)
        """,
        (
            upload_id,
            request_id,
            fixture["application_id"],
            fixture["customer_id"],
            fixture["person_id"],
            fixture["person_type"],
            fixture["document_id"],
            fixture["document_version"],
            f"renewal-candidate:{upload_id}",
            fixture["document_type"],
            STAMP,
            fixture["customer_id"],
            "c" * 64,
        ),
    )
    db.execute(
        """
        INSERT INTO monitoring_document_renewal_upload_cleanup
            (cleanup_id,upload_id,request_id,application_id,customer_id,backend,
             storage_key,file_extension,local_path,artifact_state,created_at,
             stored_at,attached_at,updated_at)
        VALUES (?,?,?,?,?,'local',?,'.pdf',?,'attached',?,?,?,?)
        """,
        (
            cleanup_id,
            upload_id,
            request_id,
            fixture["application_id"],
            fixture["customer_id"],
            storage_key,
            str(candidate),
            STAMP,
            STAMP,
            STAMP,
            STAMP,
        ),
    )
    from db import append_audit_log

    append_audit_log(
        db,
        action="monitoring.document_renewal.upload_bound",
        user_id=fixture["customer_id"],
        user_name="Synthetic Fixture Client",
        user_role="client",
        target=f"monitoring_renewal_upload_binding:{upload_id}",
        detail=json.dumps({"run_id": "fixture-run-1"}, sort_keys=True),
        before_state={},
        after_state={"binding_status": "bound"},
        application_id=fixture["application_id"],
        request_id="fixture-run-1",
        commit=False,
    )
    db.commit()
    return candidate


def _cleanup(db, fixture, plan, **overrides):
    values = {
        "run_id": plan["run_id"],
        "expected_plan_fingerprint": plan["fingerprint"],
        "actor_id": "system:fixture-cleanup-test",
        "confirmation": CLEANUP_CONFIRMATION,
        "feature_state": FLAGS_OFF,
        "allow_test_database": True,
    }
    values.update(overrides)
    return cleanup_renewal_harness_operational_state(db, fixture, **values)


def test_plan_and_locked_cleanup_skip_global_isolation_scans(
    cleanup_db,
    monkeypatch,
):
    db, tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    _seed_operational_graph(db, fixture, tmp_path)
    original = cleanup_module._relation_rows
    observed = []

    def tracked_relation_rows(
        connection,
        spec,
        *,
        run_id,
        include_isolation=True,
    ):
        observed.append(include_isolation)
        return original(
            connection,
            spec,
            run_id=run_id,
            include_isolation=include_isolation,
        )

    monkeypatch.setattr(cleanup_module, "_relation_rows", tracked_relation_rows)
    plan = build_renewal_harness_cleanup_plan(
        db,
        fixture,
        run_id="fixture-run-isolation-scan-boundary",
    )
    assert observed == [False]

    observed.clear()
    _cleanup(db, fixture, plan)
    # Locked replan and post-delete proof are fixture-only. The one final
    # fingerprint deliberately retains the complete global isolation scan.
    assert observed == [False, False, True]


def test_plan_is_repeatable_sanitized_and_declares_exact_fk_order(cleanup_db):
    db, tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    candidate = _seed_operational_graph(db, fixture, tmp_path)

    first = build_renewal_harness_cleanup_plan(
        db, fixture, run_id="fixture-run-1"
    )
    second = build_renewal_harness_cleanup_plan(
        db, fixture, run_id="fixture-run-1"
    )
    assert first == second
    assert first["status"] == "ready"
    assert first["delete_order"] == list(CLEANUP_DELETE_ORDER)
    assert first["counts"] == {
        "renewal_requests": 1,
        "renewal_events": 3,
        "renewal_uploads": 1,
        "renewal_upload_bindings": 1,
        "renewal_upload_cleanup": 1,
        "renewal_notifications": 1,
        "harness_rate_limit": 0,
    }
    rendered = json.dumps(first)
    assert str(candidate) not in rendered
    assert "local://" not in rendered
    assert "synthetic-passport.pdf" not in rendered
    assert len(first["fingerprint"]) == 64


def test_cleanup_removes_only_expired_exact_harness_rate_limit(cleanup_db):
    from auth import build_shared_rate_limit_key

    db, tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    _seed_operational_graph(db, fixture, tmp_path)
    run_id = "fixture-run-rate-limit"
    exact_key = build_shared_rate_limit_key(
        "document_renewal_upload",
        fixture_request=fixture["request_id"],
        harness_run=run_id,
        user=fixture["customer_id"],
    )
    unrelated_key = build_shared_rate_limit_key(
        "document_renewal_upload",
        ip="203.0.113.10",
        user="real-customer",
    )
    db.execute(
        "INSERT INTO shared_rate_limits "
        "(key,window_start,attempt_count,expires_at) VALUES (?,?,?,?)",
        (exact_key, 1.0, 2, 2.0),
    )
    db.execute(
        "INSERT INTO shared_rate_limits "
        "(key,window_start,attempt_count,expires_at) VALUES (?,?,?,?)",
        (unrelated_key, 1.0, 1, 2.0),
    )
    db.commit()

    plan = build_renewal_harness_cleanup_plan(db, fixture, run_id=run_id)
    assert plan["counts"]["harness_rate_limit"] == 1
    _cleanup(db, fixture, plan)
    assert db.execute(
        "SELECT COUNT(*) AS count FROM shared_rate_limits WHERE key = ?",
        (exact_key,),
    ).fetchone()["count"] == 0
    assert db.execute(
        "SELECT COUNT(*) AS count FROM shared_rate_limits WHERE key = ?",
        (unrelated_key,),
    ).fetchone()["count"] == 1


def test_cleanup_removes_exact_harness_rate_limit_before_window_expiry(cleanup_db):
    from auth import build_shared_rate_limit_key

    db, tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    _seed_operational_graph(db, fixture, tmp_path)
    run_id = "fixture-run-unexpired-rate-limit"
    exact_key = build_shared_rate_limit_key(
        "document_renewal_upload",
        fixture_request=fixture["request_id"],
        harness_run=run_id,
        user=fixture["customer_id"],
    )
    db.execute(
        "INSERT INTO shared_rate_limits "
        "(key,window_start,attempt_count,expires_at) VALUES (?,?,?,?)",
        (exact_key, 1.0, 1, 9_999_999_999.0),
    )
    db.commit()

    plan = build_renewal_harness_cleanup_plan(db, fixture, run_id=run_id)
    result = _cleanup(db, fixture, plan)
    assert result["deleted_counts"]["harness_rate_limit"] == 1
    assert db.execute(
        "SELECT COUNT(*) AS count FROM shared_rate_limits WHERE key = ?",
        (exact_key,),
    ).fetchone()["count"] == 0


def test_cleanup_restores_operational_baseline_and_preserves_audit(cleanup_db):
    db, tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    before = capture_renewal_harness_fingerprint(
        db, fixture, run_id="fixture-run-1", feature_state=FLAGS_OFF
    )
    candidate = _seed_operational_graph(db, fixture, tmp_path)
    transient = capture_renewal_harness_fingerprint(
        db, fixture, run_id="fixture-run-1", feature_state=FLAGS_OFF
    )
    assert transient["operational_fingerprint"] != before["operational_fingerprint"]
    plan = build_renewal_harness_cleanup_plan(
        db, fixture, run_id="fixture-run-1"
    )

    result = _cleanup(db, fixture, plan)
    final = result["final_fingerprint"]
    comparison = compare_renewal_harness_baseline(before, final)
    assert comparison == {
        "core_restored": True,
        "operational_restored": True,
        "nonfixture_unchanged": True,
        "scheduler_unchanged": True,
        "all_flags_off": True,
        "audit_evidence_appended": True,
        "baseline_restored": True,
    }
    assert not candidate.exists()
    assert result["audit_preserved"] is True
    assert result["artifact_cleanup"][0]["outcome"] == "deleted"
    audits = db.execute(
        """
        SELECT action,request_id FROM audit_log
         WHERE application_id=? AND action LIKE 'monitoring.document_renewal.%'
         ORDER BY id
        """,
        (fixture["application_id"],),
    ).fetchall()
    assert [row["action"] for row in audits] == [
        "monitoring.document_renewal.upload_bound",
        "monitoring.document_renewal.fixture_cleanup",
    ]
    assert audits[-1]["request_id"] == "fixture-run-1"

    repeated = _cleanup(db, fixture, plan)
    assert repeated["idempotent"] is True
    assert repeated["prior_cleanup_audit_confirmed"] is True
    assert db.execute(
        """
        SELECT COUNT(*) AS count FROM audit_log
         WHERE action='monitoring.document_renewal.fixture_cleanup'
           AND application_id=?
        """,
        (fixture["application_id"],),
    ).fetchone()["count"] == 1


def test_audit_failure_rolls_back_db_and_cleanup_resumes_after_artifact_delete(
    cleanup_db,
):
    db, tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    candidate = _seed_operational_graph(db, fixture, tmp_path)
    plan = build_renewal_harness_cleanup_plan(
        db, fixture, run_id="fixture-run-audit-failure"
    )

    def failed_audit(*_args, **_kwargs):
        raise RuntimeError("synthetic audit outage")

    with pytest.raises(RuntimeError, match="synthetic audit outage"):
        _cleanup(db, fixture, plan, audit_writer=failed_audit)
    assert not candidate.exists()
    assert db.execute(
        "SELECT COUNT(*) AS count FROM monitoring_document_renewal_requests"
    ).fetchone()["count"] == 1
    assert db.execute(
        "SELECT COUNT(*) AS count FROM monitoring_document_renewal_upload_bindings"
    ).fetchone()["count"] == 1

    recovered = _cleanup(db, fixture, plan)
    assert recovered["artifact_cleanup"][0]["outcome"] == "already_absent"
    assert recovered["deleted_counts"]["renewal_requests"] == 1


def test_postgres_cleanup_lock_atomicity_and_idempotency(
    disposable_postgres_cleanup_db,
    tmp_path,
    monkeypatch,
):
    db, db_module = disposable_postgres_cleanup_db
    import config

    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path))
    fixture = _seed_fixture(db)
    candidate = _seed_operational_graph(db, fixture, tmp_path)
    plan = build_renewal_harness_cleanup_plan(
        db,
        fixture,
        run_id="fixture-run-postgres",
    )

    # The harness transaction locks the exact artifact-ledger row before any
    # external cleanup.  Prove a concurrent ordinary reconciler cannot claim
    # that row while the approved plan is being applied.
    import psycopg2
    from psycopg2.errors import LockNotAvailable

    cleanup_module._begin_locked_cleanup(
        db,
        cleanup_module._normalize_fixture(fixture),
    )
    contender = psycopg2.connect(db.database_identity)
    try:
        with contender.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '150ms'")
            with pytest.raises(LockNotAvailable):
                cursor.execute(
                    """
                    UPDATE monitoring_document_renewal_upload_cleanup
                       SET lease_owner='renewal-cleanup:contender'
                     WHERE request_id=%s
                    """,
                    (fixture["request_id"],),
                )
        contender.rollback()
    finally:
        contender.close()
        db.rollback()

    def failed_audit(*_args, **_kwargs):
        raise RuntimeError("synthetic PostgreSQL audit outage")

    with pytest.raises(RuntimeError, match="PostgreSQL audit outage"):
        _cleanup(db, fixture, plan, audit_writer=failed_audit)
    assert not candidate.exists()
    assert db.execute(
        "SELECT COUNT(*) AS count FROM monitoring_document_renewal_requests"
    ).fetchone()["count"] == 1

    applied = _cleanup(db, fixture, plan)
    assert applied["idempotent"] is False
    assert applied["artifact_cleanup"][0]["outcome"] == "already_absent"
    assert applied["deleted_counts"]["renewal_requests"] == 1
    audit_chain = db_module.verify_audit_log_chain(db)
    assert audit_chain["verified"] is True
    assert audit_chain["chained_rows"] == 7
    assert audit_chain["coverage_gaps"] == 0
    assert audit_chain["coverage_complete"] is True
    assert audit_chain["broken_links"] == []

    empty_plan = build_renewal_harness_cleanup_plan(
        db,
        fixture,
        run_id="fixture-run-postgres",
    )
    repeated = _cleanup(db, fixture, empty_plan)
    assert repeated["idempotent"] is True
    assert not any(repeated["deleted_counts"].values())
    assert db.execute(
        "SELECT COUNT(*) AS count FROM audit_log "
        "WHERE action='monitoring.document_renewal.fixture_cleanup'"
    ).fetchone()["count"] == 1


def test_cleanup_refuses_unowned_notification_and_enabled_flag(cleanup_db):
    db, tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    _seed_operational_graph(db, fixture, tmp_path)
    db.execute(
        """
        INSERT INTO client_notifications
            (application_id,client_id,notification_type,title,message)
        VALUES (?,?,'document_renewal_reminder','Unexpected','Not event-owned')
        """,
        (fixture["application_id"], fixture["customer_id"]),
    )
    db.commit()
    with pytest.raises(
        RenewalHarnessCleanupError,
        match="notifications are not exactly owned",
    ):
        build_renewal_harness_cleanup_plan(
            db, fixture, run_id="fixture-run-unowned-notification"
        )

    db.execute(
        "DELETE FROM client_notifications WHERE title='Unexpected'"
    )
    db.commit()
    plan = build_renewal_harness_cleanup_plan(
        db, fixture, run_id="fixture-run-enabled-flag"
    )
    enabled = dict(FLAGS_OFF)
    enabled["ENABLE_DOCUMENT_RENEWAL_AUTOMATION"] = True
    with pytest.raises(RenewalHarnessCleanupError, match="must be OFF"):
        _cleanup(db, fixture, plan, feature_state=enabled)


def test_cleanup_rejects_wrong_plan_and_preserve_audit_false(cleanup_db):
    db, tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    _seed_operational_graph(db, fixture, tmp_path)
    plan = build_renewal_harness_cleanup_plan(
        db, fixture, run_id="fixture-run-guards"
    )
    with pytest.raises(RenewalHarnessCleanupError, match="locked cleanup plan"):
        _cleanup(
            db,
            fixture,
            {**plan, "fingerprint": "0" * 64},
        )
    with pytest.raises(RenewalHarnessCleanupError, match="must be preserved"):
        _cleanup(db, fixture, plan, preserve_audit=False)


def test_fingerprint_detects_nonfixture_and_scheduler_drift(cleanup_db, monkeypatch):
    db, _tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    db.execute(
        """
        INSERT INTO client_notifications
            (application_id,client_id,notification_type,title,message)
        VALUES (NULL,NULL,'unrelated','Unrelated notification','before')
        """
    )
    db.execute(
        """
        INSERT INTO monitoring_document_renewal_scheduler_state
            (scheduler_key,cursor_primary,cursor_secondary,updated_at)
        VALUES ('eligibility','before','before',?)
        """,
        (STAMP,),
    )
    db.commit()
    before = capture_renewal_harness_fingerprint(
        db,
        fixture,
        run_id="fixture-run-fingerprint-drift",
        feature_state=FLAGS_OFF,
    )

    db.execute(
        "UPDATE client_notifications SET message='after' WHERE title='Unrelated notification'"
    )
    db.execute(
        """
        UPDATE monitoring_document_renewal_scheduler_state
           SET cursor_primary='after'
         WHERE scheduler_key='eligibility'
        """
    )
    db.commit()
    after = capture_renewal_harness_fingerprint(
        db,
        fixture,
        run_id="fixture-run-fingerprint-drift",
        feature_state=FLAGS_OFF,
    )

    comparison = compare_renewal_harness_baseline(before, after)
    assert comparison["nonfixture_unchanged"] is False
    assert comparison["scheduler_unchanged"] is False
    assert after["all_monitoring_flags_off"] is True
    incomplete_flags = capture_renewal_harness_fingerprint(
        db,
        fixture,
        run_id="fixture-run-fingerprint-drift",
        feature_state={"ENABLE_DOCUMENT_RENEWAL_AUTOMATION": False},
    )
    assert incomplete_flags["all_monitoring_flags_off"] is False
    monkeypatch.setenv("ENVIRONMENT", "staging")
    with pytest.raises(
        RenewalHarnessCleanupError,
        match="evaluate feature flags from the runtime",
    ):
        capture_renewal_harness_fingerprint(
            db,
            fixture,
            run_id="fixture-run-fingerprint-drift",
            feature_state=FLAGS_OFF,
        )


def test_fingerprint_detects_null_application_alert_drift(cleanup_db):
    db, _tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    db.execute(
        """
        INSERT INTO monitoring_alerts
            (application_id,client_name,alert_type,severity,detected_by,
             summary,source_reference,status,discovered_via,created_at)
        VALUES (NULL,'Orphan fixture probe','document_expired','low',
                'fixture_test','Orphan baseline probe','orphan:before',
                'open','manual',?)
        """,
        (STAMP,),
    )
    db.commit()
    before = capture_renewal_harness_fingerprint(
        db,
        fixture,
        run_id="fixture-run-null-alert-drift",
        feature_state=FLAGS_OFF,
    )
    db.execute(
        "UPDATE monitoring_alerts SET summary='Orphan changed' "
        "WHERE source_reference='orphan:before'"
    )
    db.commit()
    after = capture_renewal_harness_fingerprint(
        db,
        fixture,
        run_id="fixture-run-null-alert-drift",
        feature_state=FLAGS_OFF,
    )

    assert before["isolation_fingerprint"] != after["isolation_fingerprint"]
    assert compare_renewal_harness_baseline(before, after)[
        "nonfixture_unchanged"
    ] is False


def test_cleanup_rejects_claimed_artifact_lease(cleanup_db):
    db, tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    _seed_operational_graph(db, fixture, tmp_path)
    db.execute(
        """
        UPDATE monitoring_document_renewal_upload_cleanup
           SET lease_owner='renewal-cleanup:concurrent-worker',
               lease_expires_at='2099-01-01T00:00:00+00:00'
        """
    )
    db.commit()

    with pytest.raises(RenewalHarnessCleanupError, match="leased by"):
        build_renewal_harness_cleanup_plan(
            db,
            fixture,
            run_id="fixture-run-active-lease",
        )


def test_cleanup_accepts_reserved_artifact_after_expired_lease_safety_margin(cleanup_db):
    db, tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    candidate = _seed_operational_graph(db, fixture, tmp_path)
    db.execute(
        "DELETE FROM monitoring_document_renewal_upload_bindings"
    )
    db.execute("DELETE FROM monitoring_document_renewal_uploads")
    db.execute(
        "DELETE FROM monitoring_document_renewal_events WHERE event_sequence=3"
    )
    db.execute(
        """
        UPDATE monitoring_document_renewal_requests
           SET request_status='awaiting_upload', revision=2
        """
    )
    db.execute(
        """
        UPDATE monitoring_document_renewal_upload_cleanup
           SET artifact_state='reserved', stored_at=NULL, attached_at=NULL,
               lease_owner='upload:cleanup-fixture-upload-v1',
               lease_expires_at='2000-01-01T00:00:00+00:00'
        """
    )
    db.commit()

    plan = build_renewal_harness_cleanup_plan(
        db,
        fixture,
        run_id="fixture-run-expired-upload-lease",
    )
    result = _cleanup(db, fixture, plan)

    assert result["deleted_counts"]["renewal_upload_cleanup"] == 1
    assert result["deleted_counts"]["renewal_requests"] == 1
    assert not candidate.exists()


def test_cleanup_detects_foreign_request_id_collision(cleanup_db):
    db, _tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    db.execute(
        """
        INSERT INTO clients (id,email,password_hash,company_name,status,created_at)
        VALUES ('foreign-fixture-client','foreign@fixture.invalid','disabled',
                'Foreign fixture client','inactive',?)
        """,
        (STAMP,),
    )
    db.execute(
        """
        INSERT INTO applications (id,ref,client_id,company_name,status,is_fixture)
        VALUES ('foreign-fixture-app','FX-FOREIGN-001','foreign-fixture-client',
                'Foreign fixture app','approved',TRUE)
        """
    )
    db.execute(
        """
        INSERT INTO documents
            (id,application_id,doc_type,doc_name,file_path,slot_key,
             is_current,version,uploaded_at)
        VALUES ('foreign-fixture-doc','foreign-fixture-app','passport',
                'foreign.pdf','fixture://foreign','entity:passport',TRUE,1,?)
        """,
        (STAMP,),
    )
    alert = db.execute(
        """
        INSERT INTO monitoring_alerts
            (application_id,client_name,alert_type,severity,detected_by,
             summary,source_reference,status,discovered_via,created_at)
        VALUES ('foreign-fixture-app','Foreign fixture app','document_expired',
                'low','fixture_test','Foreign collision','document:foreign-fixture-doc',
                'open','manual',?) RETURNING id
        """,
        (STAMP,),
    ).fetchone()
    db.execute(
        """
        INSERT INTO monitoring_document_renewal_requests
            (request_id,application_id,customer_id,document_id,document_type,
             document_slot_key,document_version,monitoring_alert_id,
             request_reason,request_status,created_at,due_date,created_by,
             updated_at,revision,contract_version,eligibility_fingerprint)
        VALUES (?,'foreign-fixture-app','foreign-fixture-client',
                'foreign-fixture-doc','passport','entity:passport',1,?,
                'expired','created',?,?, 'system:foreign',?,1,
                'monitoring_document_renewal_request_v1',?)
        """,
        (fixture["request_id"], alert["id"], STAMP, "2026-08-24", STAMP, "f" * 64),
    )
    db.commit()

    with pytest.raises(RenewalHarnessCleanupError, match="request drifted"):
        build_renewal_harness_cleanup_plan(
            db,
            fixture,
            run_id="fixture-run-request-id-collision",
        )


def test_staging_s3_cleanup_rejects_foreign_bucket_before_access(cleanup_db):
    db, tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    _seed_operational_graph(db, fixture, tmp_path)
    db.execute(
        """
        UPDATE monitoring_document_renewal_upload_cleanup
           SET backend='s3', local_path=NULL,
               storage_key=?, s3_version_id='fixture-version-1'
        """,
        (
            "clients/f1xedrnwcli00001/monitoring_renewal_candidate/"
            f"{fixture['request_id']}/{'b' * 32}.pdf",
        ),
    )
    db.commit()
    relations = cleanup_module._relation_rows(
        db,
        cleanup_module._normalize_fixture(fixture),
        run_id="fixture-run-foreign-bucket",
    )

    class ForeignBucketClient:
        bucket_name = "production-documents"
        region = "af-south-1"

        def __getattr__(self, _name):
            raise AssertionError("foreign S3 client must not be accessed")

    with pytest.raises(RenewalHarnessCleanupError, match="reviewed bucket and region"):
        cleanup_module._cleanup_artifacts(
            relations,
            environment="staging",
            artifact_client=ForeignBucketClient(),
        )


def test_fingerprint_fails_closed_on_fixture_application_footprint_drift(cleanup_db):
    db, _tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    before = capture_renewal_harness_fingerprint(
        db,
        fixture,
        run_id="fixture-run-footprint",
        feature_state=FLAGS_OFF,
    )
    db.execute(
        """
        INSERT INTO documents
            (id,application_id,person_id,person_type,doc_type,doc_name,
             file_path,slot_key,is_current,version,uploaded_at)
        VALUES (?,?,?,?,?,?,?,?,FALSE,2,?)
        """,
        (
            "f1xedrnwdoc99999",
            fixture["application_id"],
            fixture["person_id"],
            fixture["person_type"],
            fixture["document_type"],
            "unexpected-fixture-document.pdf",
            "fixture://unexpected-document",
            fixture["document_slot_key"],
            STAMP,
        ),
    )
    db.execute(
        """
        INSERT INTO monitoring_alerts
            (application_id,client_name,alert_type,severity,detected_by,
             summary,source_reference,status,discovered_via,created_at)
        VALUES (?,?,?,?,?,?,?,'open','manual',?)
        """,
        (
            fixture["application_id"],
            "Synthetic fixture",
            "document_expired",
            "low",
            "fixture_test",
            "Unexpected fixture alert",
            "document:f1xedrnwdoc99999",
            STAMP,
        ),
    )
    db.execute(
        """
        INSERT INTO client_notifications
            (application_id,client_id,notification_type,title,message,
             documents_list,read_status,created_at)
        VALUES (?,?,'unrelated_fixture_notice','Unexpected','Unexpected',
                '[]',FALSE,?)
        """,
        (fixture["application_id"], fixture["customer_id"], STAMP),
    )
    db.execute(
        """
        INSERT INTO verification_jobs (id,document_id,application_id,status)
        VALUES ('fixture-unexpected-agent1-job',?,?,'pending')
        """,
        (fixture["document_id"], fixture["application_id"]),
    )
    db.commit()

    with pytest.raises(RenewalHarnessCleanupError, match="baseline drift"):
        capture_renewal_harness_fingerprint(
            db,
            fixture,
            run_id="fixture-run-footprint",
            feature_state=FLAGS_OFF,
        )


@pytest.mark.parametrize(
    ("statement", "label"),
    (
        ("UPDATE documents SET is_current=FALSE", "document.is_current"),
        ("UPDATE documents SET expiry_date='2030-01-01'", "document.expiry_date"),
        ("UPDATE applications SET risk_level='HIGH'", "application.risk_level"),
    ),
)
def test_fingerprint_fails_closed_on_permanent_manifest_drift(
    cleanup_db,
    statement,
    label,
):
    db, _tmp_path = cleanup_db
    fixture = _seed_fixture(db)
    db.execute(statement)
    db.commit()

    with pytest.raises(RenewalHarnessCleanupError, match=label):
        capture_renewal_harness_fingerprint(
            db,
            fixture,
            run_id="fixture-run-manifest-drift",
            feature_state=FLAGS_OFF,
        )


def test_s3_cleanup_deletes_only_recorded_version_and_proves_zero_residue():
    storage_key = (
        "clients/f1xedrnwcli00001/monitoring_renewal_candidate/"
        f"fixture-request/{'d' * 32}.pdf"
    )
    artifact = {
        "cleanup_id": "fixture-cleanup-s3-v1",
        "customer_id": "f1xedrnwcli00001",
        "request_id": "fixture-request",
        "upload_id": "d" * 32,
        "file_extension": ".pdf",
        "storage_key": storage_key,
        "s3_version_id": "exact-version-1",
    }
    upload = {"file_sha256": "e" * 64, "file_size": 123}

    class ExactVersionClient:
        def __init__(self):
            self.exists = True
            self.inventory_calls = 0
            self.versions = [
                {
                    "version_id": "exact-version-1",
                    "is_latest": True,
                    "size": 123,
                }
            ]
            self.deleted = []

        def inspect_monitoring_renewal_candidate_versions(self, key):
            self.inventory_calls += 1
            return True, {
                "key": key,
                "versions": list(self.versions),
                "delete_markers": [],
                "version_count": len(self.versions),
                "delete_marker_count": 0,
            }

        def inspect_monitoring_renewal_candidate(self, _key):
            if not self.exists:
                return True, {"exists": False, "version_id": None}
            return True, {
                "exists": True,
                "version_id": "exact-version-1",
                "request_id": artifact["request_id"],
                "upload_id": artifact["upload_id"],
                "cleanup_id": artifact["cleanup_id"],
                "file_sha256": upload["file_sha256"],
                "content_length": upload["file_size"],
            }

        def delete_monitoring_renewal_candidate(self, key, **kwargs):
            self.deleted.append((key, kwargs))
            self.exists = False
            self.versions = []
            return True, "candidate_deleted"

    client = ExactVersionClient()
    result = _cleanup_s3_artifact(artifact, upload, client)
    assert result == {
        "cleanup_id": artifact["cleanup_id"],
        "backend": "s3",
        "outcome": "candidate_deleted",
    }
    assert client.deleted == [
        (
            storage_key,
            {
                "version_id": "exact-version-1",
                "expected_request_id": artifact["request_id"],
                "expected_upload_id": artifact["upload_id"],
                "expected_cleanup_id": artifact["cleanup_id"],
            },
        )
    ]
    assert client.inventory_calls == 2


@pytest.mark.parametrize("object_present", (False, True))
def test_s3_cleanup_recovers_reserved_candidate_without_recorded_version(
    object_present,
):
    upload_id = "9" * 32
    storage_key = (
        "clients/f1xedrnwcli00001/monitoring_renewal_candidate/"
        f"fixture-request/{upload_id}.pdf"
    )
    artifact = {
        "cleanup_id": "fixture-cleanup-reserved-v1",
        "customer_id": "f1xedrnwcli00001",
        "request_id": "fixture-request",
        "upload_id": upload_id,
        "file_extension": ".pdf",
        "storage_key": storage_key,
        "s3_version_id": None,
        "artifact_state": "reserved",
    }

    class ReservedClient:
        def __init__(self):
            self.exists = object_present
            self.version_id = "reserved-version-1"
            self.deleted = []

        def inspect_monitoring_renewal_candidate_versions(self, key):
            versions = (
                [
                    {
                        "version_id": self.version_id,
                        "is_latest": True,
                        "size": 101,
                    }
                ]
                if self.exists
                else []
            )
            return True, {
                "key": key,
                "versions": versions,
                "delete_markers": [],
                "version_count": len(versions),
                "delete_marker_count": 0,
            }

        def inspect_monitoring_renewal_candidate(self, _key):
            if not self.exists:
                return True, {"exists": False, "version_id": None}
            return True, {
                "exists": True,
                "version_id": self.version_id,
                "request_id": artifact["request_id"],
                "upload_id": artifact["upload_id"],
                "cleanup_id": artifact["cleanup_id"],
                "content_length": 101,
            }

        def delete_monitoring_renewal_candidate(self, key, **kwargs):
            self.deleted.append((key, kwargs))
            self.exists = False
            return True, "candidate_deleted"

    client = ReservedClient()
    result = _cleanup_s3_artifact(artifact, None, client)
    assert result["outcome"] == (
        "candidate_deleted" if object_present else "already_absent"
    )
    if object_present:
        assert client.deleted[0][1]["version_id"] == "reserved-version-1"
    else:
        assert client.deleted == []


def test_s3_cleanup_rejects_a_different_customer_namespace_before_inventory():
    artifact = {
        "cleanup_id": "fixture-cleanup-s3-v1",
        "customer_id": "f1xedrnwcli00001",
        "request_id": "fixture-request",
        "upload_id": "d" * 32,
        "file_extension": ".pdf",
        "storage_key": (
            "clients/real-customer/monitoring_renewal_candidate/"
            f"fixture-request/{'d' * 32}.pdf"
        ),
        "s3_version_id": "exact-version-1",
    }

    class MustNotTouchS3:
        def __getattr__(self, _name):
            raise AssertionError("foreign namespace must fail before S3 access")

    with pytest.raises(RenewalHarnessCleanupError, match="exact owned identity"):
        _cleanup_s3_artifact(artifact, None, MustNotTouchS3())


def test_local_cleanup_refuses_same_named_directory_outside_upload_root(
    cleanup_db,
):
    _db, tmp_path = cleanup_db
    upload_id = "d" * 32
    outside = tmp_path / "outside" / "monitoring-renewal-candidates"
    outside.mkdir(parents=True)
    candidate = outside / f"{upload_id}.pdf"
    candidate.write_bytes(b"do not delete")
    artifact = {
        "cleanup_id": "fixture-cleanup-local-v1",
        "upload_id": upload_id,
        "file_extension": ".pdf",
        "storage_key": f"local://monitoring-renewal-candidates/{upload_id}.pdf",
        "local_path": str(candidate),
    }

    with pytest.raises(RenewalHarnessCleanupError, match="outside its allowlist"):
        _cleanup_local_artifact(artifact)
    assert candidate.read_bytes() == b"do not delete"


def test_s3_cleanup_rejects_residue_created_by_delete():
    storage_key = (
        "clients/f1xedrnwcli00001/monitoring_renewal_candidate/"
        f"fixture-request/{'d' * 32}.pdf"
    )
    artifact = {
        "cleanup_id": "fixture-cleanup-s3-v1",
        "customer_id": "f1xedrnwcli00001",
        "request_id": "fixture-request",
        "upload_id": "d" * 32,
        "file_extension": ".pdf",
        "storage_key": storage_key,
        "s3_version_id": "exact-version-1",
    }

    class ResidueAfterDeleteClient:
        def __init__(self):
            self.deleted = False

        def inspect_monitoring_renewal_candidate_versions(self, key):
            versions = [] if self.deleted else [
                {
                    "version_id": "exact-version-1",
                    "is_latest": True,
                    "size": 123,
                }
            ]
            markers = (
                [{"version_id": "delete-marker-1", "is_latest": True}]
                if self.deleted
                else []
            )
            return True, {
                "key": key,
                "versions": versions,
                "delete_markers": markers,
                "version_count": len(versions),
                "delete_marker_count": len(markers),
            }

        def inspect_monitoring_renewal_candidate(self, _key):
            if self.deleted:
                return True, {"exists": False, "version_id": None}
            return True, {
                "exists": True,
                "version_id": "exact-version-1",
                "request_id": artifact["request_id"],
                "upload_id": artifact["upload_id"],
                "cleanup_id": artifact["cleanup_id"],
            }

        def delete_monitoring_renewal_candidate(self, _key, **_kwargs):
            self.deleted = True
            return True, "candidate_deleted"

    with pytest.raises(
        RenewalHarnessCleanupError,
        match="version residue remains after cleanup",
    ):
        _cleanup_s3_artifact(artifact, None, ResidueAfterDeleteClient())


@pytest.mark.parametrize(
    ("exists", "versions", "delete_markers", "message"),
    [
        (
            True,
            [
                {"version_id": "exact-version-1", "is_latest": True, "size": 123},
                {"version_id": "hidden-version", "is_latest": False, "size": 123},
            ],
            [],
            "unexpected object versions",
        ),
        (
            False,
            [],
            [{"version_id": "hidden-delete-marker", "is_latest": True}],
            "residual object versions or delete markers",
        ),
    ],
)
def test_s3_cleanup_fails_closed_on_hidden_version_or_delete_marker(
    exists,
    versions,
    delete_markers,
    message,
):
    storage_key = (
        "clients/f1xedrnwcli00001/monitoring_renewal_candidate/"
        f"fixture-request/{'d' * 32}.pdf"
    )
    artifact = {
        "cleanup_id": "fixture-cleanup-s3-v1",
        "customer_id": "f1xedrnwcli00001",
        "request_id": "fixture-request",
        "upload_id": "d" * 32,
        "file_extension": ".pdf",
        "storage_key": storage_key,
        "s3_version_id": "exact-version-1",
    }

    class ResidualClient:
        @staticmethod
        def inspect_monitoring_renewal_candidate_versions(key):
            return True, {
                "key": key,
                "versions": versions,
                "delete_markers": delete_markers,
                "version_count": len(versions),
                "delete_marker_count": len(delete_markers),
            }

        @staticmethod
        def inspect_monitoring_renewal_candidate(_key):
            return True, {
                "exists": exists,
                "version_id": "exact-version-1" if exists else None,
            }

        @staticmethod
        def delete_monitoring_renewal_candidate(*_args, **_kwargs):
            raise AssertionError("residual versions must fail before deletion")

    with pytest.raises(RenewalHarnessCleanupError, match=message):
        _cleanup_s3_artifact(artifact, None, ResidualClient())


def test_s3_exact_key_version_inventory_is_paginated_and_payload_free():
    from s3_client import S3Client, build_monitoring_renewal_candidate_key

    key = build_monitoring_renewal_candidate_key(
        "f1xedrnwcli00001",
        "fixture-request",
        "d" * 32,
        "candidate.pdf",
    )

    class VersionInventoryClient:
        def __init__(self):
            self.calls = []

        def list_object_versions(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {
                    "IsTruncated": True,
                    "NextKeyMarker": key,
                    "NextVersionIdMarker": "version-2",
                    "Versions": [
                        {
                            "Key": key,
                            "VersionId": "version-2",
                            "IsLatest": True,
                            "Size": 20,
                            "Owner": {"DisplayName": "must-not-leak"},
                        },
                        {
                            "Key": key + ".unrelated",
                            "VersionId": "other-version",
                            "IsLatest": True,
                            "Size": 99,
                        },
                    ],
                }
            return {
                "IsTruncated": False,
                "Versions": [
                    {
                        "Key": key,
                        "VersionId": "version-1",
                        "IsLatest": False,
                        "Size": 10,
                    }
                ],
                "DeleteMarkers": [
                    {
                        "Key": key,
                        "VersionId": "marker-1",
                        "IsLatest": False,
                    }
                ],
            }

    client = object.__new__(S3Client)
    client.bucket_name = "renewal-test-bucket"
    inventory_client = VersionInventoryClient()
    client.monitoring_renewal_candidate_client = lambda: inventory_client

    success, result = client.inspect_monitoring_renewal_candidate_versions(key)
    assert success is True
    assert result == {
        "key": key,
        "versions": [
            {"version_id": "version-1", "is_latest": False, "size": 10},
            {"version_id": "version-2", "is_latest": True, "size": 20},
        ],
        "delete_markers": [
            {"version_id": "marker-1", "is_latest": False}
        ],
        "version_count": 2,
        "delete_marker_count": 1,
    }
    assert inventory_client.calls[1]["KeyMarker"] == key
    assert inventory_client.calls[1]["VersionIdMarker"] == "version-2"
    assert "Owner" not in json.dumps(result)
