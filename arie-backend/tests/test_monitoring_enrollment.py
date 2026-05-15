import json
import uuid
from datetime import date, datetime, timedelta, timezone


def _audit_writer(user, action, target, detail, db=None,
                  before_state=None, after_state=None, commit=False):
    _audit_writer.events.append({
        "action": action,
        "target": target,
        "detail": json.loads(detail),
        "before_state": before_state,
        "after_state": after_state,
    })
    db.execute(
        """
        INSERT INTO audit_log
            (user_id, user_name, user_role, action, target, detail,
             ip_address, before_state, after_state)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (user or {}).get("sub", "system"),
            (user or {}).get("name", "System"),
            (user or {}).get("role", "system"),
            action,
            target,
            detail,
            "127.0.0.1",
            json.dumps(before_state, default=str) if before_state is not None else None,
            json.dumps(after_state, default=str) if after_state is not None else None,
        ),
    )


_audit_writer.events = []


def _reset_audit_events():
    _audit_writer.events.clear()


def _insert_app(
    db,
    *,
    status="approved",
    risk_level="LOW",
    final_risk_level=None,
    onboarding_lane="standard",
    decided_at=None,
    is_fixture=0,
):
    suffix = uuid.uuid4().hex[:8]
    app_id = f"mon-enroll-{suffix}"
    app_ref = f"ARF-MON-ENROLL-{suffix}"
    now = decided_at or datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc).isoformat()
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
            app_ref,
            f"Monitoring Enrollment {suffix} Ltd",
            "Mauritius",
            "Technology",
            "SME",
            status,
            risk_level,
            final_risk_level or risk_level,
            25 if risk_level == "LOW" else 55 if risk_level == "MEDIUM" else 80,
            onboarding_lane,
            now,
            now,
            now,
            now,
            is_fixture,
        ),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())


def _review_rows(db, app_id):
    return db.execute(
        "SELECT * FROM periodic_reviews WHERE application_id = ? ORDER BY id",
        (app_id,),
    ).fetchall()


def _due_delta_days(row, anchor="2026-05-15"):
    return (date.fromisoformat(row["due_date"]) - date.fromisoformat(anchor)).days


def test_approved_low_case_enrolls_monitoring_and_periodic_review(db):
    from monitoring_enrollment import enroll_approved_application

    _reset_audit_events()
    app = _insert_app(db, risk_level="LOW")

    result = enroll_approved_application(
        db,
        app,
        user={"sub": "admin001", "name": "Admin", "role": "admin"},
        audit_writer=_audit_writer,
        approved_at=app["decided_at"],
        previous_status="compliance_review",
    )
    db.commit()

    assert result["status"] == "created"
    rows = _review_rows(db, app["id"])
    assert len(rows) == 1
    assert rows[0]["risk_level"] == "LOW"
    assert _due_delta_days(rows[0]) == 1095
    assert rows[0]["priority"] == "low"
    assert rows[0]["trigger_source"] == "schedule"

    audit = db.execute(
        "SELECT detail FROM audit_log WHERE action = 'Monitoring Enrollment' AND target = ?",
        (app["ref"],),
    ).fetchone()
    assert audit is not None
    detail = json.loads(audit["detail"])
    assert detail["event"] == "monitoring_enrollment"
    assert detail["interval_days"] == 1095


def test_medium_case_gets_shorter_review_interval(db):
    from monitoring_enrollment import enroll_approved_application

    _reset_audit_events()
    app = _insert_app(db, risk_level="MEDIUM")
    result = enroll_approved_application(
        db,
        app,
        user={"sub": "admin001", "name": "Admin", "role": "admin"},
        audit_writer=_audit_writer,
        approved_at=app["decided_at"],
        previous_status="compliance_review",
    )
    db.commit()

    row = _review_rows(db, app["id"])[0]
    assert result["interval_days"] == 730
    assert _due_delta_days(row) == 730
    assert row["priority"] == "normal"


def test_high_and_edd_cases_get_enhanced_short_review_intervals(db):
    from monitoring_enrollment import enroll_approved_application

    _reset_audit_events()
    high = _insert_app(db, risk_level="HIGH")
    edd = _insert_app(
        db,
        risk_level="MEDIUM",
        final_risk_level="MEDIUM",
        onboarding_lane="edd",
    )

    high_result = enroll_approved_application(
        db, high, user={"sub": "admin001", "name": "Admin", "role": "admin"},
        audit_writer=_audit_writer, approved_at=high["decided_at"],
        previous_status="compliance_review",
    )
    edd_result = enroll_approved_application(
        db, edd, user={"sub": "admin001", "name": "Admin", "role": "admin"},
        audit_writer=_audit_writer, approved_at=edd["decided_at"],
        previous_status="edd_approved",
    )
    db.commit()

    high_row = _review_rows(db, high["id"])[0]
    edd_row = _review_rows(db, edd["id"])[0]
    assert high_result["interval_days"] == 365
    assert _due_delta_days(high_row) == 365
    assert high_row["priority"] == "high"
    assert edd_result["interval_days"] == 180
    assert _due_delta_days(edd_row) == 180
    assert edd_row["priority"] == "urgent"


def test_approval_retry_updates_existing_schedule_without_duplicate(db):
    from monitoring_enrollment import enroll_approved_application

    _reset_audit_events()
    app = _insert_app(db, risk_level="LOW")

    first = enroll_approved_application(
        db, app, user={"sub": "admin001", "name": "Admin", "role": "admin"},
        audit_writer=_audit_writer, approved_at=app["decided_at"],
        previous_status="compliance_review",
    )
    app["final_risk_level"] = "MEDIUM"
    second = enroll_approved_application(
        db, app, user={"sub": "admin001", "name": "Admin", "role": "admin"},
        audit_writer=_audit_writer, approved_at=app["decided_at"],
        previous_status="approved",
    )
    db.commit()

    rows = _review_rows(db, app["id"])
    assert len(rows) == 1
    assert second["status"] == "updated"
    assert second["periodic_review_id"] == first["periodic_review_id"]
    assert rows[0]["risk_level"] == "MEDIUM"


def test_rejected_withdrawn_and_fixtures_are_not_enrolled(db):
    from monitoring_enrollment import enroll_approved_application

    _reset_audit_events()
    rejected = _insert_app(db, status="rejected")
    withdrawn = _insert_app(db, status="withdrawn")
    fixture = _insert_app(db, status="approved", is_fixture=1)

    for app in (rejected, withdrawn, fixture):
        result = enroll_approved_application(
            db,
            app,
            user={"sub": "admin001", "name": "Admin", "role": "admin"},
            audit_writer=_audit_writer,
            approved_at=app["decided_at"],
        )
        assert result["status"] == "skipped"
        assert _review_rows(db, app["id"]) == []


def test_existing_approved_app_can_be_backfilled(db):
    from monitoring_enrollment import backfill_approved_applications

    _reset_audit_events()
    approved = _insert_app(db, risk_level="LOW")
    rejected = _insert_app(db, status="rejected", risk_level="LOW")

    result = backfill_approved_applications(
        db,
        user={"sub": "admin001", "name": "Admin", "role": "admin"},
        audit_writer=_audit_writer,
    )
    db.commit()

    assert result["created"] >= 1
    assert len(_review_rows(db, approved["id"])) == 1
    assert _review_rows(db, rejected["id"]) == []


def test_latest_review_summary_matches_db_state(db):
    from monitoring_enrollment import enroll_approved_application, latest_active_review_summary

    _reset_audit_events()
    app = _insert_app(db, risk_level="MEDIUM")
    result = enroll_approved_application(
        db,
        app,
        user={"sub": "admin001", "name": "Admin", "role": "admin"},
        audit_writer=_audit_writer,
        approved_at=app["decided_at"],
        previous_status="compliance_review",
    )
    db.commit()

    summary = latest_active_review_summary(db, app["id"])
    row = _review_rows(db, app["id"])[0]
    assert summary["id"] == result["periodic_review_id"] == row["id"]
    assert summary["due_date"] == row["due_date"]
    assert summary["status"] == row["status"]
