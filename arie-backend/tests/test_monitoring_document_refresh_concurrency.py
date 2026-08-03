"""Concurrency and fail-closed guards for Monitoring document requests."""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time
import uuid

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitoring_alert_state_machine as state_machine
import monitoring_document_refresh as document_refresh
from db import DBConnection


OFFICER = {"sub": "officer-1", "role": "co", "name": "Test Officer"}


def _postgres_dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get(
        "DATABASE_URL_TEST"
    )


@pytest.fixture
def sqlite_alert_db():
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    db = DBConnection(raw, is_postgres=False, database_identity=":memory:")
    db.executescript(
        """
        CREATE TABLE clients (
            id TEXT PRIMARY KEY,
            email TEXT,
            company_name TEXT
        );
        CREATE TABLE applications (
            id TEXT PRIMARY KEY,
            ref TEXT,
            company_name TEXT,
            client_id TEXT
        );
        CREATE TABLE monitoring_alerts (
            id INTEGER PRIMARY KEY,
            application_id TEXT,
            alert_type TEXT,
            severity TEXT,
            detected_by TEXT,
            summary TEXT,
            source_reference TEXT,
            status TEXT
        );
        """
    )
    try:
        yield db
    finally:
        db.close()


def _must_not_run(*_args, **_kwargs):
    raise AssertionError("No request side effect should run")


def test_document_request_rejects_noncanonical_authoritative_status(
    sqlite_alert_db,
):
    db = sqlite_alert_db
    db.execute(
        "INSERT INTO applications (id, ref) VALUES ('app-1', 'APP-1')"
    )
    db.execute(
        "INSERT INTO monitoring_alerts "
        "(id, application_id, alert_type, status) "
        "VALUES (1, 'app-1', 'document_expired', 'OPEN')"
    )
    db.commit()

    with pytest.raises(
        document_refresh.MonitoringDocumentRefreshError,
        match="not canonical",
    ) as exc_info:
        document_refresh.request_updated_document(
            db,
            1,
            user=OFFICER,
            audit_writer=_must_not_run,
            email_sender=_must_not_run,
        )
    assert exc_info.value.status_code == 409
    db.rollback()


def test_document_request_rejects_deleted_linked_application(sqlite_alert_db):
    db = sqlite_alert_db
    db.execute(
        "INSERT INTO monitoring_alerts "
        "(id, application_id, alert_type, status) "
        "VALUES (1, 'missing-app', 'document_expired', 'open')"
    )
    db.commit()

    with pytest.raises(
        document_refresh.MonitoringDocumentRefreshError,
        match="linked application no longer exists",
    ) as exc_info:
        document_refresh.request_updated_document(
            db,
            1,
            user=OFFICER,
            audit_writer=_must_not_run,
            email_sender=_must_not_run,
        )
    assert exc_info.value.status_code == 409
    db.rollback()


def test_document_request_maps_missing_alert_to_safe_404(sqlite_alert_db):
    with pytest.raises(
        document_refresh.MonitoringDocumentRefreshError,
        match="Alert not found",
    ) as exc_info:
        document_refresh.request_updated_document(
            sqlite_alert_db,
            999,
            user=OFFICER,
            audit_writer=_must_not_run,
            email_sender=_must_not_run,
        )
    assert exc_info.value.status_code == 404
    sqlite_alert_db.rollback()


@pytest.fixture
def postgres_refresh_db():
    dsn = _postgres_dsn()
    if not dsn:
        pytest.skip("No disposable PostgreSQL test DSN")

    import psycopg2
    from psycopg2.extensions import parse_dsn

    parsed = parse_dsn(dsn)
    host = str(parsed.get("host") or "")
    database = str(parsed.get("dbname") or "")
    if host and host not in {"localhost", "127.0.0.1", "::1"} and not host.startswith(
        "/"
    ):
        pytest.skip("Concurrency test requires a local PostgreSQL server")
    if "test" not in database.lower():
        pytest.skip("Concurrency test requires a test-named PostgreSQL database")

    schema = f"monitoring_refresh_race_{uuid.uuid4().hex[:12]}"
    raw = psycopg2.connect(dsn)
    holder = DBConnection(raw, is_postgres=True, database_identity=dsn)
    try:
        holder.execute(f'CREATE SCHEMA "{schema}"')
        holder.execute(f'SET search_path TO "{schema}"')
        holder.execute(
            """
            CREATE TABLE clients (
                id TEXT PRIMARY KEY,
                email TEXT,
                company_name TEXT
            )
            """
        )
        holder.execute(
            """
            CREATE TABLE applications (
                id TEXT PRIMARY KEY,
                ref TEXT,
                company_name TEXT,
                client_id TEXT
            )
            """
        )
        holder.execute(
            """
            CREATE TABLE monitoring_alerts (
                id SERIAL PRIMARY KEY,
                application_id TEXT,
                client_name TEXT,
                alert_type TEXT,
                severity TEXT,
                detected_by TEXT,
                summary TEXT,
                source_reference TEXT,
                status TEXT NOT NULL,
                officer_action TEXT,
                officer_notes TEXT,
                reviewed_at TIMESTAMP,
                reviewed_by TEXT
            )
            """
        )
        holder.execute(
            """
            CREATE TABLE documents (
                id TEXT PRIMARY KEY,
                application_id TEXT,
                person_id TEXT,
                doc_type TEXT,
                doc_name TEXT,
                expiry_date TEXT,
                valid_until TEXT,
                verification_results TEXT,
                uploaded_at TIMESTAMP,
                review_status TEXT,
                verification_status TEXT,
                slot_key TEXT,
                is_current INTEGER,
                version INTEGER,
                superseded_at TIMESTAMP,
                superseded_by_document_id TEXT
            )
            """
        )
        holder.execute(
            """
            CREATE TABLE application_enhanced_requirements (
                id SERIAL PRIMARY KEY,
                application_id TEXT,
                trigger_key TEXT,
                trigger_label TEXT,
                trigger_category TEXT,
                requirement_key TEXT,
                requirement_label TEXT,
                requirement_description TEXT,
                audience TEXT,
                requirement_type TEXT,
                subject_scope TEXT,
                blocking_approval INTEGER,
                waivable INTEGER,
                waiver_roles TEXT,
                mandatory INTEGER,
                status TEXT,
                generation_source TEXT,
                trigger_reason TEXT,
                trigger_context TEXT,
                linked_document_id TEXT,
                monitoring_alert_id INTEGER,
                monitoring_document_id TEXT,
                due_date TEXT,
                requested_at TEXT,
                requested_by TEXT,
                created_at TEXT,
                created_by TEXT,
                updated_at TEXT,
                updated_by TEXT,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        holder.execute(
            """
            CREATE TABLE client_notifications (
                id SERIAL PRIMARY KEY,
                application_id TEXT,
                client_id TEXT,
                notification_type TEXT,
                title TEXT,
                message TEXT,
                documents_list TEXT,
                read_status BOOLEAN,
                created_at TIMESTAMP
            )
            """
        )
        holder.execute(
            """
            CREATE TABLE audit_log (
                id SERIAL PRIMARY KEY,
                target TEXT,
                action TEXT,
                detail TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        holder.execute(
            """
            CREATE TABLE refresh_audit (
                id SERIAL PRIMARY KEY,
                alert_id INTEGER,
                action TEXT
            )
            """
        )
        holder.execute(
            "INSERT INTO clients (id, email, company_name) "
            "VALUES ('client-1', 'client@example.test', 'Test Client')"
        )
        holder.execute(
            "INSERT INTO applications (id, ref, company_name, client_id) "
            "VALUES ('app-1', 'APP-1', 'Test Client', 'client-1')"
        )
        holder.execute(
            "INSERT INTO documents "
            "(id, application_id, doc_type, doc_name, expiry_date) "
            "VALUES ('doc-1', 'app-1', 'passport', 'Passport', '2026-01-01')"
        )
        alert_id = holder.execute(
            """
            INSERT INTO monitoring_alerts
                (application_id, client_name, alert_type, severity, detected_by,
                 summary, source_reference, status)
            VALUES ('app-1', 'Test Client', 'document_expired', 'low',
                    'Document Health Monitor', 'Passport expired',
                    'document:doc-1', 'open')
            RETURNING id
            """
        ).fetchone()["id"]
        holder.commit()
        yield {
            "dsn": dsn,
            "schema": schema,
            "holder": holder,
            "alert_id": alert_id,
        }
    finally:
        try:
            holder.rollback()
        except Exception:
            pass
        try:
            holder.execute("SET search_path TO public")
            holder.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            holder.commit()
        finally:
            holder.close()


def _wait_for_lock(holder, *, worker_pid, done):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        row = holder.execute(
            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = ?",
            (worker_pid,),
        ).fetchone()
        if row and row.get("wait_event_type") == "Lock":
            return
        if done.is_set():
            pytest.fail("Document-request writer did not wait on the alert lock")
        time.sleep(0.025)
    pytest.fail("Document-request writer never reached the alert row lock")


def test_postgres_terminal_winner_blocks_document_request_and_notification(
    postgres_refresh_db,
    monkeypatch,
):
    import psycopg2

    env = postgres_refresh_db
    holder = env["holder"]
    # Decoration is orthogonal to this race and otherwise requires the full
    # application questionnaire schema.
    monkeypatch.setattr(
        document_refresh,
        "decorate_application_requirements_for_backoffice",
        lambda _db, _app, requirements: requirements,
    )

    state_machine.lock_alert_for_transition(holder, env["alert_id"])
    holder.execute(
        "UPDATE monitoring_alerts SET status = 'dismissed' WHERE id = ?",
        (env["alert_id"],),
    )

    outcome = {"email_calls": 0}
    ready = threading.Event()
    done = threading.Event()

    def audit_writer(_user, action, target, _detail, *, db, **_kwargs):
        db.execute(
            "INSERT INTO refresh_audit (alert_id, action) VALUES (?, ?)",
            (int(str(target).rsplit(":", 1)[-1]), action),
        )

    def email_sender(*_args):
        outcome["email_calls"] += 1
        return True

    def run_request():
        raw = psycopg2.connect(env["dsn"])
        db = DBConnection(raw, is_postgres=True, database_identity=env["dsn"])
        try:
            db.execute(f'SET search_path TO "{env["schema"]}"')
            db.execute("SET lock_timeout = '5s'")
            db.execute("SET statement_timeout = '10s'")
            db.commit()
            outcome["worker_pid"] = raw.get_backend_pid()
            ready.set()
            outcome["result"] = document_refresh.request_updated_document(
                db,
                env["alert_id"],
                user=OFFICER,
                audit_writer=audit_writer,
                email_sender=email_sender,
            )
            db.commit()
        except Exception as exc:
            outcome["error"] = exc
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            done.set()
            db.close()

    thread = threading.Thread(target=run_request, daemon=True)
    thread.start()
    released = False
    try:
        assert ready.wait(timeout=5), "Document-request worker did not start"
        _wait_for_lock(
            holder,
            worker_pid=outcome["worker_pid"],
            done=done,
        )
        holder.commit()
        released = True
    finally:
        if not released:
            holder.rollback()
        thread.join(timeout=10)

    assert not thread.is_alive(), "Document-request worker did not finish"
    error = outcome.get("error")
    assert isinstance(error, document_refresh.MonitoringDocumentRefreshError)
    assert error.status_code == 409
    assert "terminal or downstream-owned" in str(error)
    assert outcome["email_calls"] == 0

    assert holder.execute(
        "SELECT status FROM monitoring_alerts WHERE id = ?",
        (env["alert_id"],),
    ).fetchone()["status"] == "dismissed"
    assert holder.execute(
        "SELECT COUNT(*) AS count FROM application_enhanced_requirements"
    ).fetchone()["count"] == 0
    assert holder.execute(
        "SELECT COUNT(*) AS count FROM client_notifications"
    ).fetchone()["count"] == 0
    assert holder.execute(
        "SELECT COUNT(*) AS count FROM refresh_audit"
    ).fetchone()["count"] == 0
