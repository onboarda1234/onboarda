"""PostgreSQL race coverage for Monitoring dismissal review control.

These tests use an isolated schema in an explicitly local, test-named
PostgreSQL database. They prove that all cooperating writers acquire the
Monitoring Alert row first, so concurrent request creation and senior-clear
attempts revalidate after the winning transaction commits.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import uuid

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitoring_dismissal_control as mdc
from db import DBConnection


OFFICER = {"sub": "officer-1", "role": "co"}
OTHER_OFFICER = {"sub": "officer-2", "role": "co"}
SENIOR = {"sub": "senior-1", "role": "sco"}


def _postgres_dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get(
        "DATABASE_URL_TEST"
    )


def _audit_writer(user, action, target, detail, *, db, **_kwargs):
    alert_id = int(str(target).rsplit(":", 1)[-1])
    db.execute(
        "INSERT INTO review_audit (alert_id, action) VALUES (?, ?)",
        (alert_id, action),
    )


@pytest.fixture
def postgres_review_control():
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

    schema = f"monitoring_review_race_{uuid.uuid4().hex[:12]}"
    raw = psycopg2.connect(dsn)
    holder = DBConnection(raw, is_postgres=True, database_identity=dsn)
    try:
        holder.execute(f'CREATE SCHEMA "{schema}"')
        holder.execute(f'SET search_path TO "{schema}"')
        holder.execute(
            """
            CREATE TABLE monitoring_alerts (
                id SERIAL PRIMARY KEY,
                application_id TEXT,
                provider TEXT,
                case_identifier TEXT,
                alert_type TEXT,
                severity TEXT,
                detected_by TEXT,
                summary TEXT,
                source_reference TEXT,
                status TEXT NOT NULL,
                linked_periodic_review_id INTEGER,
                linked_edd_case_id INTEGER,
                triaged_at TIMESTAMP,
                assigned_at TIMESTAMP,
                resolved_at TIMESTAMP
            )
            """
        )
        holder.execute(
            """
            CREATE TABLE monitoring_alert_review_requests (
                id SERIAL PRIMARY KEY,
                alert_id INTEGER NOT NULL REFERENCES monitoring_alerts(id),
                tier INTEGER NOT NULL,
                requested_outcome TEXT,
                dismissal_reason TEXT,
                rationale TEXT,
                evidence_ref TEXT,
                transition_evidence TEXT,
                source_alert_status TEXT,
                state TEXT NOT NULL DEFAULT 'pending',
                initiated_by TEXT,
                initiated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_by TEXT,
                approved_at TIMESTAMP,
                approval_note TEXT,
                rejection_reason TEXT,
                second_review_bypassed INTEGER NOT NULL DEFAULT 0,
                sampled_for_qa INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        holder.execute(
            "CREATE UNIQUE INDEX "
            f"{mdc.PENDING_REVIEW_UNIQUE_INDEX} "
            "ON monitoring_alert_review_requests(alert_id) "
            "WHERE state = 'pending'"
        )
        holder.execute(
            "CREATE TABLE review_audit "
            "(id SERIAL PRIMARY KEY, alert_id INTEGER NOT NULL, action TEXT NOT NULL)"
        )
        holder.execute(
            "CREATE TABLE users "
            "(id TEXT PRIMARY KEY, role TEXT NOT NULL, status TEXT NOT NULL)"
        )
        holder.execute(
            "INSERT INTO users (id, role, status) VALUES "
            "('officer-1', 'co', 'active'), "
            "('officer-2', 'co', 'active'), "
            "('senior-1', 'sco', 'active')"
        )
        alert_id = holder.execute(
            """
            INSERT INTO monitoring_alerts
                (application_id, alert_type, severity, summary,
                 source_reference, status)
            VALUES ('app-1', 'manual', 'medium', 'Manual review signal',
                    'manual:test:1', 'open')
            RETURNING id
            """
        ).fetchone()["id"]
        holder.commit()
        yield {
            "dsn": dsn,
            "schema": schema,
            "holder": holder,
            "holder_pid": raw.get_backend_pid(),
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


def _wait_until_postgres_reports_lock_wait(holder, *, worker_pid, done):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        row = holder.execute(
            "SELECT wait_event_type, wait_event "
            "FROM pg_stat_activity WHERE pid = ?",
            (worker_pid,),
        ).fetchone()
        if row and row.get("wait_event_type") == "Lock":
            return
        if done.is_set():
            pytest.fail("Competing review-control writer did not wait on the alert lock")
        time.sleep(0.025)
    pytest.fail("Competing review-control writer never reached the alert row lock")


def _run_competing_writer(env, writer):
    """Start ``writer`` after the holder owns the alert lock, then release it."""
    import psycopg2

    outcome = {}
    ready = threading.Event()
    done = threading.Event()

    def run():
        raw = psycopg2.connect(env["dsn"])
        db = DBConnection(raw, is_postgres=True, database_identity=env["dsn"])
        try:
            db.execute(f'SET search_path TO "{env["schema"]}"')
            db.execute("SET lock_timeout = '5s'")
            db.execute("SET statement_timeout = '10s'")
            db.commit()
            outcome["worker_pid"] = raw.get_backend_pid()
            ready.set()
            outcome["result"] = writer(db)
            db.commit()
        except Exception as exc:  # the expected fail-closed 409 lands here
            outcome["error"] = exc
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            done.set()
            db.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    released = False
    try:
        assert ready.wait(timeout=5), "Competing PostgreSQL writer did not start"
        _wait_until_postgres_reports_lock_wait(
            env["holder"],
            worker_pid=outcome["worker_pid"],
            done=done,
        )
        env["holder"].commit()
        released = True
    finally:
        if not released:
            env["holder"].rollback()
        thread.join(timeout=10)
    assert not thread.is_alive(), "Competing PostgreSQL writer did not finish"
    return outcome


def _pending_request(db, *, alert_id, user):
    return mdc.create_pending_request(
        db,
        alert={"id": alert_id},
        tier=3,
        requested_outcome="dismiss",
        dismissal_reason="other",
        rationale="Audited manual dismissal rationale.",
        evidence_ref="",
        transition_evidence={},
        user=user,
        audit_writer=_audit_writer,
        commit=False,
    )


def _senior_clear(db, *, alert_id):
    return mdc.record_senior_clear(
        db,
        alert={"id": alert_id},
        tier=3,
        requested_outcome="dismiss",
        dismissal_reason="other",
        rationale="Senior direct-clear rationale.",
        evidence_ref="",
        transition_evidence={},
        user=SENIOR,
        audit_writer=_audit_writer,
        commit=False,
    )


def _assert_single_pending_winner(env, outcome):
    error = outcome.get("error")
    assert isinstance(error, mdc.DismissalControlError)
    assert error.status_code == 409
    rows = env["holder"].execute(
        "SELECT state, COUNT(*) AS row_count "
        "FROM monitoring_alert_review_requests "
        "GROUP BY state ORDER BY state"
    ).fetchall()
    assert rows == [{"state": "pending", "row_count": 1}]
    audits = env["holder"].execute(
        "SELECT action, COUNT(*) AS row_count "
        "FROM review_audit GROUP BY action ORDER BY action"
    ).fetchall()
    assert audits == [
        {
            "action": "monitoring.alert.dismissal_requested",
            "row_count": 1,
        }
    ]


def test_postgres_concurrent_pending_requests_serialize_to_one_winner(
    postgres_review_control,
):
    env = postgres_review_control
    first = _pending_request(
        env["holder"],
        alert_id=env["alert_id"],
        user=OFFICER,
    )
    assert first["state"] == "pending"

    outcome = _run_competing_writer(
        env,
        lambda db: _pending_request(
            db,
            alert_id=env["alert_id"],
            user=OTHER_OFFICER,
        ),
    )

    _assert_single_pending_winner(env, outcome)


def test_postgres_pending_request_blocks_concurrent_senior_clear(
    postgres_review_control,
):
    env = postgres_review_control
    first = _pending_request(
        env["holder"],
        alert_id=env["alert_id"],
        user=OFFICER,
    )
    assert first["state"] == "pending"

    outcome = _run_competing_writer(
        env,
        lambda db: _senior_clear(db, alert_id=env["alert_id"]),
    )

    _assert_single_pending_winner(env, outcome)
