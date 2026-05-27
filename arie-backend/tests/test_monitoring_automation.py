import inspect
import json
import uuid
from datetime import datetime, timezone

import pytest


NOW = datetime(2026, 5, 27, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_monitoring_automation_rows(db):
    db.execute("DELETE FROM monitoring_alerts WHERE application_id LIKE 'mon-auto-%'")
    db.execute("DELETE FROM documents WHERE application_id LIKE 'mon-auto-%'")
    db.execute("DELETE FROM periodic_reviews WHERE application_id LIKE 'mon-auto-%'")
    db.execute("DELETE FROM applications WHERE id LIKE 'mon-auto-%'")
    db.execute(
        """
        DELETE FROM audit_log
         WHERE action LIKE 'monitoring.automation.%'
            OR action LIKE 'monitoring.document_health_alert.%'
            OR action = 'periodic_review.required_items.generated'
        """
    )
    db.execute("DELETE FROM monitoring_agent_status WHERE agent_type = 'periodic_review_automation'")
    db.commit()


def _insert_approved_application(db, *, risk_level="LOW", final_risk_level=None):
    suffix = uuid.uuid4().hex[:8]
    app_id = f"mon-auto-{suffix}"
    ref = f"ARF-MON-AUTO-{suffix}"
    db.execute(
        """
        INSERT INTO applications
            (id, ref, company_name, country, sector, entity_type,
             status, risk_level, final_risk_level, risk_score,
             onboarding_lane, decided_at, created_at, updated_at,
             inputs_updated_at, is_fixture)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            ref,
            f"Monitoring Automation {suffix} Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "approved",
            risk_level,
            final_risk_level or risk_level,
            25,
            "Fast Lane",
            "2026-05-15T12:00:00+00:00",
            "2026-05-15T12:00:00+00:00",
            "2026-05-15T12:00:00+00:00",
            "2026-05-15T12:00:00+00:00",
            0,
        ),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())


def _insert_due_review(db, app, **overrides):
    payload = {
        "application_id": app["id"],
        "client_name": app["company_name"],
        "risk_level": "LOW",
        "trigger_type": "time_based",
        "trigger_source": "schedule",
        "trigger_reason": "Scheduled review due under canonical cadence policy.",
        "review_reason": "Scheduled review due under canonical cadence policy.",
        "status": "pending",
        "due_date": "2026-05-27",
        "next_review_date": "2026-05-27",
        "priority": "low",
        "review_cycle_number": 1,
        "review_type": "scheduled",
        "policy_version": "v2",
        "frequency_months": 36,
        "calculation_basis": "risk_level:LOW",
        "sla_due_at": "2026-05-27",
    }
    payload.update(overrides)
    columns = ", ".join(payload.keys())
    placeholders = ", ".join("?" for _ in payload)
    db.execute(
        f"INSERT INTO periodic_reviews ({columns}, state_changed_at, created_at) "
        f"VALUES ({placeholders}, datetime('now'), datetime('now'))",
        tuple(payload.values()),
    )
    db.commit()
    return db.execute("SELECT * FROM periodic_reviews ORDER BY id DESC LIMIT 1").fetchone()


def _audit_rows(db, action):
    return [
        dict(row)
        for row in db.execute(
            "SELECT * FROM audit_log WHERE action = ? ORDER BY id ASC",
            (action,),
        ).fetchall()
    ]


def _audit_detail(row):
    return json.loads(row["detail"])


def test_monitoring_automation_processes_due_review_without_manual_click(db):
    import monitoring_automation as ma

    app = _insert_approved_application(db)
    review = _insert_due_review(db, app)

    result = ma.run_due_monitoring_reviews(db, now=NOW, max_reviews=10)

    assert result["processed"] == 1
    assert result["failed"] == 0
    persisted = db.execute(
        "SELECT * FROM periodic_reviews WHERE id = ?",
        (review["id"],),
    ).fetchone()
    assert persisted["status"] == "in_progress"
    assert persisted["required_items_generated_at"]
    assert persisted["required_items"]

    started = _audit_rows(db, "monitoring.automation.review_started")
    assert len(started) == 1
    started_detail = _audit_detail(started[0])
    assert started_detail["source_agents"] == [6, 7, 8]
    assert started_detail["policy_version"] == "v2"
    assert started_detail["frequency_months"] == 36
    assert started_detail["calculation_basis"] == "risk_level:LOW"

    run_started = _audit_rows(db, "monitoring.automation.run_started")
    assert _audit_detail(run_started[0])["policy_source"] == (
        "periodic_reviews_from_periodic_review_policy_v2"
    )
    assert _audit_rows(db, "periodic_review.required_items.generated")
    assert _audit_rows(db, "monitoring.automation.run_completed")

    agent = db.execute(
        "SELECT * FROM monitoring_agent_status WHERE agent_type = ?",
        (ma.AUTOMATION_AGENT_TYPE,),
    ).fetchone()
    assert agent is not None
    assert agent["last_run"]
    assert agent["next_run"]
    assert agent["clients_monitored"] == 1


def test_monitoring_automation_prevents_duplicate_interval_run_and_alert_storm(db):
    import monitoring_automation as ma

    app = _insert_approved_application(db)
    review = _insert_due_review(db, app)
    db.execute(
        """
        INSERT INTO documents
            (id, application_id, doc_type, doc_name, file_path,
             expiry_date, uploaded_at, is_current)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"doc-{uuid.uuid4().hex[:8]}",
            app["id"],
            "passport",
            "passport.pdf",
            "/tmp/passport.pdf",
            "2026-01-01",
            "2026-01-01T00:00:00+00:00",
            1,
        ),
    )
    db.commit()

    first = ma.run_due_monitoring_reviews(db, now=NOW, max_reviews=10)
    second = ma.run_due_monitoring_reviews(db, now=NOW, max_reviews=10)

    assert first["processed"] == 1
    assert second["processed"] == 0
    assert len(_audit_rows(db, "monitoring.automation.review_started")) == 1
    assert len(_audit_rows(db, "monitoring.document_health_alert.created")) == 1

    alert_count = db.execute(
        """
        SELECT COUNT(*) AS c
          FROM monitoring_alerts
         WHERE application_id = ?
           AND detected_by = 'document_health_monitor'
           AND source_reference LIKE 'document:%'
        """,
        (app["id"],),
    ).fetchone()["c"]
    assert alert_count == 1
    persisted = db.execute(
        "SELECT status FROM periodic_reviews WHERE id = ?",
        (review["id"],),
    ).fetchone()
    assert persisted["status"] == "in_progress"


def test_monitoring_automation_reuses_pr3_cadence_snapshot_from_enrollment(db):
    import monitoring_automation as ma
    from monitoring_enrollment import enroll_approved_application

    app = _insert_approved_application(db, risk_level="MEDIUM")
    enroll_approved_application(
        db,
        app,
        user={"sub": "admin001", "name": "Admin", "role": "admin"},
        audit_writer=ma.system_audit_writer,
        approved_at=app["decided_at"],
        previous_status="compliance_review",
    )
    review = db.execute(
        "SELECT * FROM periodic_reviews WHERE application_id = ?",
        (app["id"],),
    ).fetchone()
    assert review["policy_version"] == "v2"
    assert review["frequency_months"] == 24
    assert review["calculation_basis"] == "risk_level:MEDIUM"

    db.execute(
        """
        UPDATE periodic_reviews
           SET due_date = ?, next_review_date = ?, status = 'pending'
         WHERE id = ?
        """,
        ("2026-05-27", "2026-05-27", review["id"]),
    )
    db.commit()

    result = ma.run_due_monitoring_reviews(db, now=NOW, max_reviews=10)

    assert result["processed"] == 1
    started = _audit_rows(db, "monitoring.automation.review_started")[-1]
    detail = _audit_detail(started)
    assert detail["policy_version"] == "v2"
    assert detail["frequency_months"] == 24
    assert detail["calculation_basis"] == "risk_level:MEDIUM"


def test_monitoring_automation_status_exposes_due_count_and_policy_source(db):
    import monitoring_automation as ma

    app = _insert_approved_application(db)
    _insert_due_review(db, app)

    status = ma.automation_status(db, now=NOW)

    assert status["due_count"] == 1
    assert status["policy_source"] == "periodic_reviews_from_periodic_review_policy_v2"
    assert status["interval_seconds"] >= 60
    assert status["agent"]["agent_type"] == ma.AUTOMATION_AGENT_TYPE


def test_monitoring_automation_failure_restores_pending_for_retry(db, monkeypatch):
    import monitoring_automation as ma

    app = _insert_approved_application(db)
    review = _insert_due_review(db, app)

    def fail_generate(*args, **kwargs):
        raise RuntimeError("controlled failure")

    monkeypatch.setattr(ma.pre, "generate_required_items", fail_generate)

    result = ma.run_due_monitoring_reviews(db, now=NOW, max_reviews=10)

    assert result["processed"] == 0
    assert result["failed"] == 1
    persisted = db.execute(
        "SELECT status, required_items_generated_at FROM periodic_reviews WHERE id = ?",
        (review["id"],),
    ).fetchone()
    assert persisted["status"] == "pending"
    assert persisted["required_items_generated_at"] is None
    failed = _audit_rows(db, "monitoring.automation.review_failed")
    assert len(failed) == 1
    assert _audit_detail(failed[0])["retriable"] is True


def test_monitoring_automation_keeps_screening_provider_behavior_out_of_scope():
    import monitoring_automation as ma

    source = inspect.getsource(ma)
    forbidden = [
        "ComplyAdvantageScreeningAdapter",
        "SumsubScreeningAdapter",
        "screening_adapter_sumsub",
        "screening_complyadvantage",
        "screening_provider",
    ]
    for token in forbidden:
        assert token not in source
