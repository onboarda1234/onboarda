import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from periodic_review_notifications import (  # noqa: E402
    _table_columns,
    process_periodic_review_notification,
    run_periodic_review_notification_sweep,
)
from periodic_review_projection_service import get_review_projection  # noqa: E402


@pytest.fixture()
def prs6_db():
    db_path = os.path.join(
        tempfile.gettempdir(),
        f"onboarda_prs6_notifications_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
    )
    if os.path.exists(db_path):
        os.unlink(db_path)
    os.environ["DB_PATH"] = db_path

    import config as config_module
    import db as db_module

    orig_config_db_path = config_module.DB_PATH
    orig_db_db_path = db_module.DB_PATH
    config_module.DB_PATH = db_path
    db_module.DB_PATH = db_path
    db_module.init_db()
    conn = db_module.get_db()
    conn.execute(
        """
        INSERT INTO users (id, email, password_hash, full_name, role, status)
        VALUES ('admin001', 'admin@test.com', 'x', 'Admin User', 'admin', 'active')
        """
    )
    conn.execute(
        """
        INSERT INTO clients (id, email, password_hash, company_name, status)
        VALUES
        ('client001', 'client@test.com', 'x', 'Owned Co Ltd', 'active'),
        ('client002', 'client2@test.com', 'x', 'Unlinked Co Ltd', 'active')
        """
    )
    conn.execute(
        """
        INSERT INTO applications
            (id, ref, client_id, company_name, country, sector, status, risk_level,
             risk_score, is_fixture, created_at, updated_at)
        VALUES
            ('app-owned', 'ARF-PRS6-OWNED', 'client001', 'Owned Co Ltd', 'Mauritius', 'Fintech', 'approved', 'HIGH', 78, 0, datetime('now'), datetime('now')),
            ('app-no-client', 'ARF-PRS6-NOCLIENT', NULL, 'No Client Co Ltd', 'Mauritius', 'Fintech', 'approved', 'LOW', 10, 0, datetime('now'), datetime('now')),
            ('app-fixture', 'RM-PILOT-008', 'client001', 'Canonical Fixture Co Ltd', 'Mauritius', 'Fintech', 'approved', 'MEDIUM', 48, 1, datetime('now'), datetime('now')),
            ('app-marker-only', 'RM-PILOT-009', 'client001', 'Marker Only Co Ltd', 'Mauritius', 'Fintech', 'approved', 'MEDIUM', 48, 0, datetime('now'), datetime('now'))
        """
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()
        try:
            os.unlink(db_path)
        except Exception:
            pass
        config_module.DB_PATH = orig_config_db_path
        db_module.DB_PATH = orig_db_db_path


def _actor():
    return {"sub": "admin001", "name": "Admin User", "role": "admin"}


def _create_review(
    conn,
    *,
    app_id="app-owned",
    status="awaiting_information",
    due_date="2026-06-30",
    attestation_status="not_started",
    notification_status="not_sent",
    initial_sent_at=None,
    reminder_count=0,
    trigger_type=None,
    trigger_reason=None,
    trigger_source=None,
):
    conn.execute(
        """
        INSERT INTO periodic_reviews
            (application_id, client_name, risk_level, status, due_date, baseline_status,
             client_attestation_status, client_notification_status, initial_notification_sent_at,
             reminder_count, notification_channel, trigger_type, trigger_reason, trigger_source,
             created_at)
        VALUES (?, 'Owned Co Ltd', 'HIGH', ?, ?, 'not_applicable', ?, ?, ?, ?, 'portal', ?, ?, ?, datetime('now'))
        """,
        (
            app_id,
            status,
            due_date,
            attestation_status,
            notification_status,
            initial_sent_at,
            reminder_count,
            trigger_type,
            trigger_reason,
            trigger_source,
        ),
    )
    conn.commit()
    return conn.execute("SELECT * FROM periodic_reviews ORDER BY id DESC LIMIT 1").fetchone()


def _audit_actions(conn):
    rows = conn.execute("SELECT action, detail FROM audit_log ORDER BY id ASC").fetchall()
    return [(row["action"], json.loads(row["detail"])) for row in rows]


def _notification_count(conn, review_ref=None):
    if review_ref:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM client_notifications WHERE message LIKE ?",
            (f"%{review_ref}%",),
        ).fetchone()["c"]
    return conn.execute("SELECT COUNT(*) AS c FROM client_notifications").fetchone()["c"]


def _review_snapshot(conn, review_id):
    row = conn.execute(
        "SELECT * FROM periodic_reviews WHERE id=?", (review_id,)
    ).fetchone()
    return dict(row)


def test_postgres_column_discovery_does_not_probe_sqlite_pragma():
    class FakePostgresDb:
        is_postgres = True

        def __init__(self):
            self.queries = []

        def execute(self, sql, params=()):
            self.queries.append((sql, params))
            if "PRAGMA" in sql.upper():
                raise AssertionError("Postgres column discovery must not issue SQLite PRAGMA")
            return self

        def fetchall(self):
            return [{"column_name": "before_state"}, {"column_name": "after_state"}]

    db = FakePostgresDb()

    assert _table_columns(db, "audit_log") == {"before_state", "after_state"}
    assert len(db.queries) == 1
    assert "information_schema.columns" in db.queries[0][0]


def _add_missing_periodic_review_doc(conn, review_id):
    conn.execute(
        """
        INSERT INTO application_enhanced_requirements
            (application_id, trigger_key, trigger_label, requirement_type, requirement_key, requirement_label,
             audience, subject_scope, mandatory, active, status, linked_periodic_review_id)
        VALUES
            ('app-owned', 'periodic_review_attestation', 'Periodic Review Attestation', 'document',
             'updated_register', 'Updated Register', 'client', 'company', 1,
             1, 'requested', ?)
        """,
        (review_id,),
    )
    conn.commit()


def test_initial_client_notification_is_generated_and_audited(prs6_db):
    now = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    review = _create_review(prs6_db)
    result = process_periodic_review_notification(prs6_db, review, now=now, actor=_actor())
    prs6_db.commit()

    assert result["sent_events"] == ["periodic_review_required"]
    stored = prs6_db.execute("SELECT * FROM periodic_reviews WHERE id=?", (review["id"],)).fetchone()
    assert stored["client_notification_status"] == "sent"
    assert stored["initial_notification_sent_at"] == now.isoformat()
    assert stored["next_reminder_due_at"] == (now + timedelta(days=7)).isoformat()
    assert _notification_count(prs6_db, f"PR-{review['id']}") == 1
    actions = [action for action, _detail in _audit_actions(prs6_db)]
    assert "periodic_review_client_notification_sent" in actions
    assert "periodic_review_notification_status_updated" in actions


def test_fixture_notification_is_suppressed_without_dispatch_or_writes(
    prs6_db, caplog
):
    review = _create_review(
        prs6_db,
        app_id="app-fixture",
        trigger_type="pilot_canonical_fixture",
        trigger_reason="RM-PILOT-008:PERIODIC",
        trigger_source="pilot_canonical_dataset",
    )
    prs6_db.execute(
        "UPDATE periodic_reviews SET client_notification_status='failed', "
        "last_notification_error='No client linked', reminder_count=2, "
        "last_reminder_sent_at='2026-05-01T00:00:00+00:00', "
        "next_reminder_due_at='2026-05-08T00:00:00+00:00' WHERE id=?",
        (review["id"],),
    )
    prs6_db.commit()
    review = prs6_db.execute(
        "SELECT * FROM periodic_reviews WHERE id=?", (review["id"],)
    ).fetchone()
    before = _review_snapshot(prs6_db, review["id"])
    audit_before = _audit_actions(prs6_db)
    sender_calls = []

    def sender_spy(email, subject, message):
        sender_calls.append((email, subject, message))
        return True

    with caplog.at_level("INFO", logger="periodic_review_notifications"):
        result = process_periodic_review_notification(
            prs6_db,
            review,
            channel="both",
            email_sender=sender_spy,
            actor=_actor(),
        )
    prs6_db.commit()

    assert result["sent_events"] == []
    assert result["errors"] == []
    assert result["officer_alert_event"] is None
    assert result["client_action_required"] == "attestation_required"
    assert result["notification_suppressed"] is True
    assert result["notification_suppression_reason"] == "fixture_application"
    evidence = result["notification_suppression_evidence"]
    assert evidence == {
        "suppressed": True,
        "reason": "fixture_application",
        "policy": "fixture_applications_do_not_receive_periodic_review_notifications",
        "application_id": "app-fixture",
        "application_ref": "RM-PILOT-008",
        "periodic_review_id": review["id"],
        "canonical_review_marker_match": True,
        "trigger_type": "pilot_canonical_fixture",
        "trigger_source": "pilot_canonical_dataset",
    }
    assert result["notification"]["client_notification_status"] == "suppressed"
    assert result["notification"]["client_notification_status_label"] == "Suppressed — synthetic fixture"
    assert result["notification"]["last_notification_error"] is None
    assert result["notification"]["last_reminder_sent_at"] is None
    assert result["notification"]["next_reminder_due_at"] is None
    assert result["notification"]["reminder_count"] == 0
    assert result["next_reminder_due_at"] is None
    assert result["notification"]["notification_suppressed"] is True
    assert (
        result["notification"]["notification_suppression_reason"]
        == "fixture_application"
    )
    assert result["notification"]["notification_suppression_evidence"] == evidence
    assert sender_calls == []
    assert _notification_count(prs6_db) == 0
    assert _audit_actions(prs6_db) == audit_before
    assert _review_snapshot(prs6_db, review["id"]) == before

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "periodic_review_notifications"
        and record.getMessage().startswith(
            "periodic-review-notification-suppressed "
        )
    ]
    assert len(messages) == 1
    logged = json.loads(messages[0].split(" ", 1)[1])
    assert logged == evidence


def test_fixture_suppression_is_repeatable_and_reported_by_sweep(prs6_db):
    review = _create_review(prs6_db, app_id="app-fixture")
    before = _review_snapshot(prs6_db, review["id"])
    audit_before = _audit_actions(prs6_db)
    sender_calls = []

    def sender_spy(*args):
        sender_calls.append(args)
        return True

    first = process_periodic_review_notification(
        prs6_db,
        review,
        channel="both",
        email_sender=sender_spy,
        actor=_actor(),
    )
    refreshed = prs6_db.execute(
        "SELECT * FROM periodic_reviews WHERE id=?", (review["id"],)
    ).fetchone()
    second = process_periodic_review_notification(
        prs6_db,
        refreshed,
        channel="both",
        email_sender=sender_spy,
        actor=_actor(),
    )
    sweep = run_periodic_review_notification_sweep(
        prs6_db,
        review_ids=[review["id"]],
        channel="both",
        email_sender=sender_spy,
        actor=_actor(),
    )
    prs6_db.commit()

    assert first["notification_suppressed"] is True
    assert second["notification_suppressed"] is True
    assert first["notification_suppression_evidence"] == second[
        "notification_suppression_evidence"
    ]
    assert first["notification_suppression_evidence"][
        "canonical_review_marker_match"
    ] is False
    assert sweep["processed"] == 1
    assert sweep["suppressed_count"] == 1
    assert sweep["sent_count"] == 0
    assert sweep["failed_count"] == 0
    assert sweep["officer_alert_count"] == 0
    assert sweep["results"][0]["notification_suppressed"] is True
    assert sender_calls == []
    assert _notification_count(prs6_db) == 0
    assert _audit_actions(prs6_db) == audit_before
    assert _review_snapshot(prs6_db, review["id"]) == before


def test_canonical_marker_does_not_suppress_nonfixture_notification(prs6_db):
    review = _create_review(
        prs6_db,
        app_id="app-marker-only",
        trigger_type="pilot_canonical_fixture",
        trigger_reason="RM-PILOT-009:PERIODIC",
        trigger_source="pilot_canonical_dataset",
    )
    result = process_periodic_review_notification(
        prs6_db, review, channel="portal", actor=_actor()
    )
    prs6_db.commit()

    assert result["sent_events"] == ["periodic_review_required"]
    assert "notification_suppressed" not in result
    assert _notification_count(prs6_db, f"PR-{review['id']}") == 1


def test_initial_notification_is_not_duplicated(prs6_db):
    now = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    review = _create_review(prs6_db)
    process_periodic_review_notification(prs6_db, review, now=now, actor=_actor())
    prs6_db.commit()
    refreshed = prs6_db.execute("SELECT * FROM periodic_reviews WHERE id=?", (review["id"],)).fetchone()
    second = process_periodic_review_notification(prs6_db, refreshed, now=now, actor=_actor())
    prs6_db.commit()

    assert second["sent_events"] == []
    assert _notification_count(prs6_db, f"PR-{review['id']}") == 1


def test_reminder_is_sent_after_interval_when_action_incomplete(prs6_db):
    initial = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    now = initial + timedelta(days=8)
    review = _create_review(
        prs6_db,
        due_date="2026-06-30",
        notification_status="sent",
        initial_sent_at=initial.isoformat(),
    )
    result = process_periodic_review_notification(prs6_db, review, now=now, actor=_actor())
    prs6_db.commit()

    assert result["sent_events"] == ["periodic_review_reminder"]
    stored = prs6_db.execute("SELECT * FROM periodic_reviews WHERE id=?", (review["id"],)).fetchone()
    assert stored["reminder_count"] == 1
    assert stored["last_reminder_sent_at"] == now.isoformat()
    actions = [action for action, _detail in _audit_actions(prs6_db)]
    assert "periodic_review_client_reminder_sent" in actions


def test_no_reminder_after_client_action_is_complete(prs6_db):
    initial = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    now = initial + timedelta(days=8)
    review = _create_review(
        prs6_db,
        due_date="2026-06-30",
        attestation_status="submitted",
        notification_status="sent",
        initial_sent_at=initial.isoformat(),
    )
    result = process_periodic_review_notification(prs6_db, review, now=now, actor=_actor())
    prs6_db.commit()

    assert result["sent_events"] == []
    assert _notification_count(prs6_db) == 0
    stored = prs6_db.execute("SELECT * FROM periodic_reviews WHERE id=?", (review["id"],)).fetchone()
    assert stored["next_reminder_due_at"] is None


def test_overdue_notification_and_officer_alert_are_generated(prs6_db):
    initial = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    now = datetime(2026, 6, 10, 9, 0, tzinfo=timezone.utc)
    review = _create_review(
        prs6_db,
        due_date="2026-06-10",
        notification_status="sent",
        initial_sent_at=initial.isoformat(),
    )
    result = process_periodic_review_notification(prs6_db, review, now=now, actor=_actor())
    prs6_db.commit()

    assert "periodic_review_overdue" in result["sent_events"]
    stored = prs6_db.execute("SELECT * FROM periodic_reviews WHERE id=?", (review["id"],)).fetchone()
    assert stored["client_notification_status"] == "overdue_notified"
    assert stored["officer_alert_status"] == "active"
    actions = [action for action, _detail in _audit_actions(prs6_db)]
    assert "periodic_review_overdue_notification_sent" in actions
    assert "periodic_review_officer_alert_created" in actions


def test_queue_projection_exposes_notification_status_fields(prs6_db):
    initial = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    review = _create_review(
        prs6_db,
        notification_status="sent",
        initial_sent_at=initial.isoformat(),
        reminder_count=1,
    )
    projection = get_review_projection(prs6_db, review["id"])

    assert projection["client_notification_status"] == "sent"
    assert projection["client_notification_status_label"] == "Sent"
    assert projection["reminder_count"] == 1
    assert projection["notification_summary"]["client_action_required"] == "attestation_required"


def test_notification_failure_is_recorded_safely(prs6_db):
    review = _create_review(prs6_db, app_id="app-no-client")
    result = process_periodic_review_notification(prs6_db, review, channel="portal", actor=_actor())
    prs6_db.commit()

    assert result["errors"]
    stored = prs6_db.execute("SELECT * FROM periodic_reviews WHERE id=?", (review["id"],)).fetchone()
    assert stored["client_notification_status"] == "failed"
    assert "client" in stored["last_notification_error"].lower()
    actions = [action for action, _detail in _audit_actions(prs6_db)]
    assert "periodic_review_client_notification_failed" in actions


def test_client_notification_payload_does_not_expose_risk_vocabulary(prs6_db):
    review = _create_review(prs6_db)
    process_periodic_review_notification(prs6_db, review, actor=_actor())
    prs6_db.commit()
    row = prs6_db.execute("SELECT title, message FROM client_notifications ORDER BY id DESC LIMIT 1").fetchone()
    payload_text = f"{row['title']} {row['message']}".lower()

    assert "risk rating" not in payload_text
    assert "risk score" not in payload_text
    assert "high" not in payload_text
    assert "officer note" not in payload_text


def test_completed_reviews_do_not_continue_receiving_reminders(prs6_db):
    initial = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    now = initial + timedelta(days=30)
    review = _create_review(
        prs6_db,
        status="completed",
        due_date="2026-06-05",
        attestation_status="submitted",
        notification_status="sent",
        initial_sent_at=initial.isoformat(),
        reminder_count=1,
    )
    result = run_periodic_review_notification_sweep(prs6_db, now=now, actor=_actor())
    prs6_db.commit()

    assert result["processed"] == 0
    assert _notification_count(prs6_db) == 0


def test_document_upload_completion_suppresses_outstanding_document_reminders(prs6_db):
    initial = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    now = initial + timedelta(days=8)
    review = _create_review(
        prs6_db,
        due_date="2026-06-30",
        attestation_status="submitted",
        notification_status="sent",
        initial_sent_at=initial.isoformat(),
    )
    _add_missing_periodic_review_doc(prs6_db, review["id"])
    missing = prs6_db.execute("SELECT * FROM periodic_reviews WHERE id=?", (review["id"],)).fetchone()
    first = process_periodic_review_notification(prs6_db, missing, now=now, actor=_actor())
    prs6_db.commit()
    assert first["sent_events"] == ["periodic_review_documents_required"]

    doc_id = "prs6-uploaded-doc"
    prs6_db.execute(
        """
        INSERT INTO documents
            (id, application_id, doc_type, doc_name, file_path, uploaded_at, verification_status, review_status)
        VALUES (?, 'app-owned', 'supporting_evidence', 'support.pdf', '/tmp/support.pdf', datetime('now'), 'verified', 'accepted')
        """,
        (doc_id,),
    )
    prs6_db.execute(
        """
        UPDATE application_enhanced_requirements
           SET linked_document_id = ?, status = 'uploaded'
         WHERE linked_periodic_review_id = ?
        """,
        (doc_id, review["id"]),
    )
    prs6_db.commit()

    refreshed = prs6_db.execute("SELECT * FROM periodic_reviews WHERE id=?", (review["id"],)).fetchone()
    second = process_periodic_review_notification(prs6_db, refreshed, now=now + timedelta(days=7), actor=_actor())
    prs6_db.commit()
    stored = prs6_db.execute("SELECT * FROM periodic_reviews WHERE id=?", (review["id"],)).fetchone()

    assert second["sent_events"] == []
    assert stored["next_reminder_due_at"] is None
