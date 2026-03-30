import json
import uuid


def _insert_application_and_memo(db, *, validation_status="pass", supervisor_status="CONSISTENT", review_status="approved"):
    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-approval-gate-{suffix}"
    app_ref = f"ARF-APPROVAL-GATE-{suffix}"
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
            "compliance_review",
            "MEDIUM",
            45,
            json.dumps(
                {
                    "screening_report": {
                        "screening_mode": "live",
                        "sanctions": {"api_status": "live"},
                        "company_registry": {"api_status": "live"},
                        "ip_geolocation": {"api_status": "live"},
                        "kyc": {"api_status": "live"},
                    }
                }
            ),
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


def test_validate_approval_requires_explicit_validation_pass(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, validation_status="pass_with_fixes")
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "validation_status" in message
    assert "'pass'" in message


def test_validate_approval_requires_explicit_supervisor_consistent(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, supervisor_status="CONSISTENT_WITH_WARNINGS")
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "supervisor_status" in message
    assert "'CONSISTENT'" in message


def test_validate_approval_allows_explicit_positive_states(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, validation_status="pass", supervisor_status="CONSISTENT", review_status="approved")
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is True
    assert message == ""
