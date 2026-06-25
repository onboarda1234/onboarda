import json
import uuid
from datetime import datetime, timedelta, timezone


def _screening_prescreening():
    from tests.conftest import clean_ca_prescreening_json
    return clean_ca_prescreening_json(company_name="IDV Gate Ltd")


def _insert_approval_ready_app(db, *, risk_level="MEDIUM"):
    from tests.conftest import insert_verified_required_documents

    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-idv-gate-{suffix}"
    app_ref = f"ARF-IDV-GATE-{suffix}"
    director_id = f"dir-idv-{suffix}"
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            app_ref,
            f"client-idv-gate-{suffix}",
            "IDV Gate Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "compliance_review",
            risk_level,
            80 if risk_level in {"HIGH", "VERY_HIGH"} else 45,
            _screening_prescreening(),
        ),
    )
    db.execute(
        "INSERT INTO directors (id, application_id, full_name, nationality, is_pep) VALUES (?, ?, ?, ?, ?)",
        (director_id, app_id, "Jane Director", "Mauritius", "No"),
    )
    insert_verified_required_documents(db, app_id)
    db.execute(
        """
        INSERT INTO compliance_memos
        (application_id, memo_data, generated_by, ai_recommendation, review_status, quality_score, validation_status, supervisor_status, approval_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            json.dumps({"ai_source": "deterministic", "metadata": {"ai_source": "deterministic"}}),
            "system",
            "APPROVE_WITH_CONDITIONS",
            "approved",
            8.5,
            "pass",
            "CONSISTENT",
            "Fixture approval reason",
        ),
    )
    db.commit()
    app = dict(db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())
    app["_test_director_id"] = director_id
    return app


def _insert_sumsub_event(db, app, *, answer):
    director_id = app["_test_director_id"]
    applicant_id = "sumsub-app-idv-" + director_id
    db.execute(
        """
        INSERT INTO sumsub_applicant_mappings
        (application_id, applicant_id, external_user_id, person_name, person_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (app["id"], applicant_id, director_id, "Jane Director", "director", "2026-06-11T10:00:00Z"),
    )
    db.execute(
        """
        INSERT INTO audit_log
        (user_id, user_name, user_role, action, target, detail, ip_address)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "system",
            "Sumsub",
            "system",
            f"KYC applicantReviewed: {answer}",
            applicant_id,
            json.dumps({"review_answer": answer, "applicant_id": applicant_id, "external_user_id": director_id}),
            "",
        ),
    )
    db.commit()


def _insert_idv_resolution(db, app, *, status):
    director_id = app["_test_director_id"]
    db.execute(
        """
        INSERT INTO idv_resolutions
        (id, application_id, application_ref, person_id, person_type, person_name,
         prior_provider_status, prior_review_answer, resolution_status, resolution_outcome,
         reason_code, evidence_reviewed, rationale, confirmation_text, senior_approver_id,
         resolved_by, resolved_by_name, resolved_by_role, ip_address, user_agent, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "res-idv-gate-" + status + "-" + uuid.uuid4().hex[:8],
            app["id"],
            app["ref"],
            director_id,
            "director",
            "Jane Director",
            "failed",
            "RED",
            status,
            "senior_exception_approved" if status == "exception_approved" else "manual_verification_completed",
            "provider_coverage_limitation",
            json.dumps(["passport", "certified_copy"]),
            "Officer reviewed evidence and recorded a compliant IDV resolution.",
            "confirmed",
            "sco-1" if status == "exception_approved" else "",
            "sco-1" if status == "exception_approved" else "co-1",
            "Senior Officer" if status == "exception_approved" else "Case Officer",
            "sco" if status == "exception_approved" else "co",
            "127.0.0.1",
            "pytest",
            "2026-06-11T10:10:00Z",
        ),
    )
    db.commit()


def _insert_accepted_enhanced_requirement(db, app_id):
    db.execute(
        """
        INSERT INTO application_enhanced_requirements (
            application_id, trigger_key, trigger_label, trigger_category,
            requirement_key, requirement_label, requirement_description,
            audience, requirement_type, subject_scope, blocking_approval,
            waivable, waiver_roles, mandatory, status, generation_source,
            trigger_reason, trigger_context, active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            "high_or_very_high_risk",
            "HIGH / VERY_HIGH risk",
            "risk",
            "idv_exception_high_risk_evidence",
            "Enhanced IDV exception evidence",
            "Accepted enhanced evidence for high-risk IDV exception gate test.",
            "backoffice",
            "internal_control",
            "application",
            1,
            1,
            json.dumps(["admin", "sco"]),
            1,
            "accepted",
            "test",
            "High-risk IDV exception approval test",
            "{}",
            1,
        ),
    )
    db.commit()


def test_pending_idv_blocks_final_approval(db, temp_db):
    from security_hardening import ApprovalGateValidator

    app = _insert_approval_ready_app(db)

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "Identity verification gate failed" in message
    assert "not automatic" not in message.lower()


def test_sumsub_red_blocks_as_unresolved_failed_not_auto_rejection(db, temp_db):
    from security_hardening import ApprovalGateValidator

    app = _insert_approval_ready_app(db)
    _insert_sumsub_event(db, app, answer="RED")

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "Identity verification failed and unresolved" in message
    assert "automatic rejection" not in message.lower()


def test_manual_verified_allows_idv_gate(db, temp_db):
    from security_hardening import ApprovalGateValidator

    app = _insert_approval_ready_app(db)
    _insert_sumsub_event(db, app, answer="RED")
    _insert_idv_resolution(db, app, status="manual_verified")

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is True, message


def test_exception_approved_allows_idv_gate_for_high_risk(db, temp_db):
    from security_hardening import ApprovalGateValidator

    app = _insert_approval_ready_app(db, risk_level="HIGH")
    _insert_accepted_enhanced_requirement(db, app["id"])
    _insert_sumsub_event(db, app, answer="RED")
    _insert_idv_resolution(db, app, status="exception_approved")

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is True, message


def test_backend_gate_blockers_include_unresolved_idv_and_clear_after_manual_resolution(db, temp_db):
    from security_hardening import collect_approval_gate_blockers

    app = _insert_approval_ready_app(db)
    blockers = collect_approval_gate_blockers(app, db)

    assert any(blocker["category"] == "Identity Verification" for blocker in blockers)

    _insert_sumsub_event(db, app, answer="RED")
    _insert_idv_resolution(db, app, status="manual_verified")
    blockers = collect_approval_gate_blockers(app, db)

    assert not any(blocker["category"] == "Identity Verification" for blocker in blockers)


def test_idv_resolution_role_rules_are_enforced():
    from server import _idv_resolution_role_error

    assert _idv_resolution_role_error(
        {"risk_level": "MEDIUM"},
        {"role": "co"},
        "provider_unable_to_verify",
        "provider_coverage_limitation",
    ) == ""
    assert "SCO or Admin" in _idv_resolution_role_error(
        {"risk_level": "HIGH"},
        {"role": "co"},
        "provider_unable_to_verify",
        "provider_coverage_limitation",
    )
    assert "SCO or Admin" in _idv_resolution_role_error(
        {"risk_level": "MEDIUM"},
        {"role": "co"},
        "senior_exception_approved",
        "other",
    )
    assert "Only Onboarding Officer" in _idv_resolution_role_error(
        {"risk_level": "LOW"},
        {"role": "analyst"},
        "manual_verification_completed",
        "other",
    )


def test_idv_resolution_handler_requires_mandatory_fields():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("server.py").read_text()
    handler = source[
        source.index("class ApplicationIdentityVerificationResolutionHandler"):
        source.index("class ApplicationDetailHandler")
    ]

    assert "Valid resolution outcome is required" in handler
    assert "Valid IDV reason is required" in handler
    assert "At least one valid evidence reviewed value is required" in handler
    assert "Officer rationale is required" in handler
    assert "Officer confirmation is required" in handler


def test_idv_resolution_handler_writes_before_after_audit_state():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("server.py").read_text()
    handler = source[
        source.index("class ApplicationIdentityVerificationResolutionHandler"):
        source.index("class ApplicationDetailHandler")
    ]

    assert "\"IDV Resolution\"" in handler
    assert "before_state=before_state" in handler
    assert "after_state=after_state" in handler
    assert "officer_rationale" in handler
    assert "evidence_reviewed" in handler
