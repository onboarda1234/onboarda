import json
import uuid
from datetime import datetime, timedelta, timezone


def _insert_application_and_memo(
    db,
    *,
    validation_status="pass",
    supervisor_status="CONSISTENT",
    review_status="approved",
    risk_level="MEDIUM",
    status="compliance_review",
    prescreening_data=None,
):
    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-approval-gate-{suffix}"
    app_ref = f"ARF-APPROVAL-GATE-{suffix}"
    now = datetime.now(timezone.utc)
    screened_at = now.strftime("%Y-%m-%dT%H:%M:%S")
    valid_until = (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S")
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            app_ref,
            f"client-approval-gate-{suffix}",
            "Approval Gate Test Ltd",
            "Mauritius",
            "Technology",
            "SME",
            status,
            risk_level,
            80 if risk_level in ("HIGH", "VERY_HIGH") else 45,
            json.dumps(prescreening_data if prescreening_data is not None else {
                "screening_report": {
                    "screening_mode": "live",
                    "screened_at": screened_at,
                    "sanctions": {"api_status": "live"},
                    "company_registry": {"api_status": "live"},
                    "ip_geolocation": {"api_status": "live"},
                    "kyc": {"api_status": "live"},
                },
                "screening_valid_until": valid_until,
                "screening_validity_days": 90,
            }),
        ),
    )
    db.execute(
        """
        INSERT INTO compliance_memos
        (application_id, memo_data, generated_by, ai_recommendation, review_status, quality_score, validation_status, supervisor_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            json.dumps({"ai_source": "deterministic", "metadata": {"ai_source": "deterministic"}}),
            "system",
            "APPROVE_WITH_CONDITIONS",
            review_status,
            8.5,
            validation_status,
            supervisor_status,
        ),
    )
    db.commit()
    app = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    return dict(app)


def _insert_enhanced_requirement(
    db,
    app_id,
    *,
    status="generated",
    mandatory=True,
    blocking=True,
    waivable=True,
    waived_by=None,
    waived_at=None,
    waiver_reason=None,
    requirement_key=None,
):
    suffix = uuid.uuid4().hex[:8]
    key = requirement_key or f"approval_gate_requirement_{suffix}"
    db.execute(
        """
        INSERT INTO application_enhanced_requirements (
            application_id, trigger_key, trigger_label, trigger_category,
            requirement_key, requirement_label, requirement_description,
            audience, requirement_type, subject_scope, blocking_approval,
            waivable, waiver_roles, mandatory, status, generation_source,
            trigger_reason, trigger_context, active, waived_by, waived_at,
            waiver_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            "high_or_very_high_risk",
            "HIGH / VERY_HIGH risk",
            "risk",
            key,
            "Enhanced approval gate evidence",
            "Evidence required for enhanced review approval gate tests.",
            "client",
            "document",
            "application",
            1 if blocking else 0,
            1 if waivable else 0,
            json.dumps(["admin", "sco"]),
            1 if mandatory else 0,
            status,
            "test",
            "Approval gate test trigger",
            "{}",
            1,
            waived_by,
            waived_at,
            waiver_reason,
        ),
    )
    db.commit()


def test_validate_approval_requires_explicit_validation_pass(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, validation_status="pass_with_fixes")
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "pass_with_fixes" in message


def test_validate_approval_requires_explicit_supervisor_consistent(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, supervisor_status="CONSISTENT_WITH_WARNINGS")
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "supervisor_status" in message
    assert "CONSISTENT_WITH_WARNINGS" in message


def test_validate_approval_allows_explicit_positive_states(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, validation_status="pass", supervisor_status="CONSISTENT", review_status="approved")
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is True
    assert message == ""


def test_mandatory_enhanced_requirements_block_until_accepted(db):
    from security_hardening import ApprovalGateValidator

    for status in ("generated", "requested", "uploaded", "under_review", "rejected"):
        app = _insert_application_and_memo(db)
        _insert_enhanced_requirement(db, app["id"], status=status, mandatory=True, blocking=False)

        can_approve, message = ApprovalGateValidator.validate_approval(app, db)

        assert can_approve is False
        assert "Enhanced Review requirements remain unresolved" in message
        assert status in message


def test_blocking_enhanced_requirements_block_until_accepted(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db)
    _insert_enhanced_requirement(db, app["id"], status="uploaded", mandatory=False, blocking=True)

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "blocking unresolved=1" in message


def test_accepted_or_validly_waived_enhanced_requirements_allow_approval(db):
    from security_hardening import ApprovalGateValidator

    accepted_app = _insert_application_and_memo(db)
    _insert_enhanced_requirement(db, accepted_app["id"], status="accepted")

    can_approve, message = ApprovalGateValidator.validate_approval(accepted_app, db)
    assert can_approve is True, message

    waived_app = _insert_application_and_memo(db)
    _insert_enhanced_requirement(
        db,
        waived_app["id"],
        status="waived",
        waived_by="sco001",
        waived_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        waiver_reason="Senior waiver documented for approval gate test.",
    )

    can_approve, message = ApprovalGateValidator.validate_approval(waived_app, db)
    assert can_approve is True, message


def test_optional_nonblocking_enhanced_requirement_does_not_block(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db)
    _insert_enhanced_requirement(db, app["id"], status="generated", mandatory=False, blocking=False)

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is True, message


def test_cancelled_enhanced_requirement_does_not_block(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, risk_level="HIGH", status="edd_required")
    _insert_enhanced_requirement(db, app["id"], status="cancelled", mandatory=True, blocking=True)

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is True, message


def test_invalid_enhanced_requirement_waiver_blocks(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db)
    db.execute(
        """
        INSERT OR IGNORE INTO users (id, email, password_hash, full_name, role, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("co001", "co001@example.com", "x", "Compliance Officer", "co", "active"),
    )
    db.execute("UPDATE users SET role='co' WHERE id='co001'")
    db.commit()
    _insert_enhanced_requirement(
        db,
        app["id"],
        status="waived",
        waived_by="co001",
        waived_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        waiver_reason="CO waiver should not satisfy approval control.",
    )

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "invalid waivers=1" in message


def test_high_risk_missing_enhanced_requirements_blocks(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, risk_level="HIGH", status="edd_required")

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "Enhanced review requirements are missing or not generated" in message


def _completed_match_prescreening():
    now = datetime.now(timezone.utc)
    return {
        "company_name": "Approval Gate Test Ltd",
        "screening_report": {
            "screening_mode": "live",
            "screened_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "company_screening": {
                "found": True,
                "sanctions": {
                    "api_status": "live",
                    "source": "sumsub",
                    "matched": True,
                    "results": [{"name": "Potential Watchlist Match", "is_sanctioned": True}],
                },
            },
            "director_screenings": [],
            "ubo_screenings": [],
        },
        "screening_valid_until": (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S"),
        "screening_validity_days": 90,
    }


def _insert_screening_review(
    db,
    app_id,
    *,
    app_ref,
    disposition,
    disposition_code,
    rationale="Officer reviewed provider profile and evidence before disposition.",
    notes="Provider case CA-GATE-001 and registry evidence retained.",
):
    db.execute(
        """
        INSERT INTO screening_reviews (
            application_id, subject_type, subject_name, disposition, notes,
            disposition_code, rationale, requires_four_eyes, reviewer_name
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            "entity",
            "Approval Gate Test Ltd",
            disposition,
            notes,
            disposition_code,
            rationale,
            0,
            "Compliance Officer",
        ),
    )
    db.execute(
        """
        INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "co001",
            "Compliance Officer",
            "co",
            "Screening Review",
            app_ref,
            json.dumps({
                "subject_type": "entity",
                "subject_name": "Approval Gate Test Ltd",
                "disposition": disposition,
                "disposition_code": disposition_code,
            }, sort_keys=True),
            "127.0.0.1",
        ),
    )
    db.commit()


def test_completed_match_without_disposition_blocks_approval(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, prescreening_data=_completed_match_prescreening())

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "Screening truth gate failed" in message
    assert "completed_match" in message


def test_completed_match_false_positive_clearance_allows_screening_gate(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, prescreening_data=_completed_match_prescreening())
    _insert_screening_review(
        db,
        app["id"],
        app_ref=app["ref"],
        disposition="cleared",
        disposition_code="false_positive_cleared",
        rationale="Officer confirmed the provider hit belongs to another legal entity after registry comparison.",
    )

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is True, message


def test_completed_match_true_match_disposition_remains_blocking(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, prescreening_data=_completed_match_prescreening())
    _insert_screening_review(
        db,
        app["id"],
        app_ref=app["ref"],
        disposition="escalated",
        disposition_code="true_match",
        rationale="Officer confirmed the provider hit appears to match the entity and must remain blocked.",
    )

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "Screening truth gate failed" in message
