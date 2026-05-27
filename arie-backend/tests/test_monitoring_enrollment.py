import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest


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
    sector="Technology",
    decision_notes=None,
    risk_escalations=None,
    elevation_reason_text=None,
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
             onboarding_lane, decision_notes, risk_escalations, elevation_reason_text,
             decided_at, created_at, updated_at,
             inputs_updated_at, is_fixture)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            app_ref,
            f"Monitoring Enrollment {suffix} Ltd",
            "Mauritius",
            sector,
            "SME",
            status,
            risk_level,
            final_risk_level or risk_level,
            25 if risk_level == "LOW" else 55 if risk_level == "MEDIUM" else 80,
            onboarding_lane,
            decision_notes,
            risk_escalations,
            elevation_reason_text,
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


def _insert_review(db, app_id, **overrides):
    allowed_columns = {
        "application_id",
        "client_name",
        "risk_level",
        "trigger_type",
        "trigger_source",
        "trigger_reason",
        "review_reason",
        "status",
        "due_date",
        "next_review_date",
        "priority",
        "review_cycle_number",
        "review_type",
        "policy_version",
        "frequency_months",
        "calculation_basis",
        "sla_due_at",
    }
    unknown = set(overrides) - allowed_columns
    if unknown:
        raise ValueError(f"Unsupported periodic_reviews override(s): {sorted(unknown)}")

    payload = {
        "application_id": app_id,
        "client_name": "Existing Review Ltd",
        "risk_level": "LOW",
        "trigger_type": "time_based",
        "trigger_source": "schedule",
        "trigger_reason": "Initial periodic review scheduled after application approval (LOW final risk, 36-month cadence).",
        "review_reason": "Initial periodic review scheduled after application approval (LOW final risk, 36-month cadence).",
        "status": "pending",
        "due_date": "2026-11-11",
        "next_review_date": "2029-05-15",
        "priority": "urgent",
        "review_cycle_number": 1,
        "review_type": "scheduled",
        "policy_version": "v1",
        "frequency_months": 36,
        "calculation_basis": "risk_level:LOW",
        "sla_due_at": "2026-11-11",
    }
    payload.update(overrides)
    columns = ", ".join(payload.keys())
    placeholders = ", ".join("?" for _ in payload)
    db.execute(
        f"INSERT INTO periodic_reviews ({columns}, state_changed_at, created_at) VALUES ({placeholders}, datetime('now'), datetime('now'))",
        tuple(payload.values()),
    )
    db.commit()


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
    assert rows[0]["due_date"] == "2029-05-15"
    assert rows[0]["next_review_date"] == "2029-05-15"
    assert _due_delta_days(rows[0]) == 1096
    assert rows[0]["priority"] == "low"
    assert rows[0]["trigger_source"] == "schedule"
    assert rows[0]["frequency_months"] == 36
    assert rows[0]["calculation_basis"] == "risk_level:LOW"

    audit = db.execute(
        "SELECT detail FROM audit_log WHERE action = 'Monitoring Enrollment' AND target = ?",
        (app["ref"],),
    ).fetchone()
    assert audit is not None
    detail = json.loads(audit["detail"])
    assert detail["event"] == "monitoring_enrollment"
    assert detail["interval_days"] == 1096
    assert detail["policy_version"] == "v2"
    assert detail["frequency_months"] == 36
    assert detail["calculation_basis"] == "risk_level:LOW"
    assert detail["enrollment_source"] == "approval_decision"


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
    assert result["interval_days"] == 731
    assert row["due_date"] == "2028-05-15"
    assert row["next_review_date"] == "2028-05-15"
    assert row["priority"] == "normal"
    assert row["frequency_months"] == 24
    assert row["calculation_basis"] == "risk_level:MEDIUM"

def test_high_plain_case_stays_on_annual_cadence(db):
    from monitoring_enrollment import enroll_approved_application

    _reset_audit_events()
    high = _insert_app(db, risk_level="HIGH")

    high_result = enroll_approved_application(
        db, high, user={"sub": "admin001", "name": "Admin", "role": "admin"},
        audit_writer=_audit_writer, approved_at=high["decided_at"],
        previous_status="compliance_review",
    )
    db.commit()

    high_row = _review_rows(db, high["id"])[0]
    assert high_result["interval_days"] == 365
    assert high_row["due_date"] == "2027-05-15"
    assert high_row["next_review_date"] == "2027-05-15"
    assert high_row["priority"] == "high"
    assert high_row["frequency_months"] == 12
    assert high_row["calculation_basis"] == "risk_level:HIGH"


@pytest.mark.parametrize(
    ("kwargs", "previous_status", "expected_basis"),
    [
        ({"risk_level": "HIGH", "final_risk_level": "HIGH", "onboarding_lane": "edd"}, "edd_approved", "enhanced_monitoring_floor:edd_route"),
        ({"risk_level": "HIGH", "final_risk_level": "HIGH", "onboarding_lane": "edd", "sector": "Cryptocurrency"}, "edd_approved", "enhanced_monitoring_floor:edd_route+crypto_vasp"),
        ({"risk_level": "LOW", "final_risk_level": "LOW", "onboarding_lane": "standard", "decision_notes": json.dumps({"pep_condition": True, "summary": "PEP exposure requires enhanced monitoring"})}, "approved", "enhanced_monitoring_floor:pep_exposure"),
    ],
)
def test_enhanced_classes_get_high_equivalent_twelve_month_cadence(db, kwargs, previous_status, expected_basis):
    from monitoring_enrollment import enroll_approved_application

    _reset_audit_events()
    app = _insert_app(db, **kwargs)
    result = enroll_approved_application(
        db,
        app,
        user={"sub": "admin001", "name": "Admin", "role": "admin"},
        audit_writer=_audit_writer,
        approved_at=app["decided_at"],
        previous_status=previous_status,
    )
    db.commit()

    row = _review_rows(db, app["id"])[0]
    assert result["interval_days"] == 365
    assert row["due_date"] == "2027-05-15"
    assert row["next_review_date"] == "2027-05-15"
    assert row["priority"] == "high"
    assert row["frequency_months"] == 12
    assert row["calculation_basis"] == expected_basis
    assert "12-month cadence" in row["trigger_reason"]


def test_very_high_case_keeps_six_month_cadence(db):
    from monitoring_enrollment import enroll_approved_application

    _reset_audit_events()
    app = _insert_app(db, risk_level="VERY_HIGH", final_risk_level="VERY_HIGH", onboarding_lane="edd")
    result = enroll_approved_application(
        db,
        app,
        user={"sub": "admin001", "name": "Admin", "role": "admin"},
        audit_writer=_audit_writer,
        approved_at=app["decided_at"],
        previous_status="edd_approved",
    )
    db.commit()

    row = _review_rows(db, app["id"])[0]
    assert result["interval_days"] == 184
    assert row["due_date"] == "2026-11-15"
    assert row["next_review_date"] == "2026-11-15"
    assert row["priority"] == "urgent"
    assert row["frequency_months"] == 6
    assert row["calculation_basis"] == "risk_level:VERY_HIGH"


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


def test_missing_audit_writer_fails_before_periodic_review_mutation(db):
    from monitoring_enrollment import enroll_approved_application

    app = _insert_app(db, risk_level="LOW")

    with pytest.raises(RuntimeError, match="audit writer"):
        enroll_approved_application(
            db,
            app,
            user={"sub": "admin001", "name": "Admin", "role": "admin"},
            audit_writer=None,
            approved_at=app["decided_at"],
            previous_status="compliance_review",
        )

    assert _review_rows(db, app["id"]) == []


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
    approved = _insert_app(db, risk_level="LOW", onboarding_lane="edd")
    rejected = _insert_app(db, status="rejected", risk_level="LOW")
    _insert_review(db, approved["id"])

    result = backfill_approved_applications(
        db,
        user={"sub": "admin001", "name": "Admin", "role": "admin"},
        audit_writer=_audit_writer,
    )
    db.commit()

    assert result["updated"] >= 1
    assert len(_review_rows(db, approved["id"])) == 1
    assert _review_rows(db, rejected["id"]) == []
    repaired = _review_rows(db, approved["id"])[0]
    assert repaired["due_date"] == "2027-05-15"
    assert repaired["next_review_date"] == "2027-05-15"
    assert repaired["frequency_months"] == 12
    assert repaired["calculation_basis"] == "enhanced_monitoring_floor:edd_route"
    assert _audit_writer.events[-1]["detail"]["enrollment_source"] == "backfill_repair"


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
