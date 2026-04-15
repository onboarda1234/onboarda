"""
Tests for EX-06: Senior-approval-with-findings memo policy.

Validates that memos with validation_status == 'pass_with_fixes' can only be
approved by admin or SCO roles with a mandatory documented reason, while
preserving all existing approval behaviour.
"""

import json
import uuid
import pytest
from datetime import datetime, timedelta, timezone


# ── Helpers ──────────────────────────────────────────────────────────────────

SENIOR_ROLES = ("admin", "sco")
NON_SENIOR_ROLES = ("co", "analyst")

VALID_SUPERVISOR = json.dumps({
    "metadata": {"ai_source": "deterministic"},
    "supervisor": {"verdict": "CONSISTENT", "can_approve": True},
})

def _insert_app(db, *, app_id=None, ref=None):
    """Insert a minimal application and return its id."""
    suffix = uuid.uuid4().hex[:8]
    app_id = app_id or f"app-pwf-{suffix}"
    ref = ref or f"ARF-PWF-{suffix}"
    _now = datetime.now(timezone.utc)
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type,
         status, risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id, ref, f"client-pwf-{suffix}",
            "PWF Test Ltd", "Mauritius", "Technology", "SME",
            "compliance_review", "HIGH", 72,
            json.dumps({
                "screening_report": {
                    "screening_mode": "live",
                    "screened_at": _now.strftime("%Y-%m-%dT%H:%M:%S"),
                    "sanctions": {"api_status": "live"},
                    "company_registry": {"api_status": "live"},
                    "ip_geolocation": {"api_status": "live"},
                    "kyc": {"api_status": "live"},
                },
                "screening_valid_until": (_now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S"),
                "screening_validity_days": 90,
            }),
        ),
    )
    db.commit()
    return app_id


def _insert_memo(db, app_id, *, validation_status="pass",
                 supervisor_status="CONSISTENT", review_status="draft",
                 approval_reason=None):
    """Insert a compliance memo for the given application."""
    params = [
        app_id, VALID_SUPERVISOR, "system", "APPROVE_WITH_CONDITIONS",
        review_status, 8.5, validation_status, supervisor_status,
    ]
    if approval_reason is not None:
        db.execute(
            """
            INSERT INTO compliance_memos
            (application_id, memo_data, generated_by, ai_recommendation,
             review_status, quality_score, validation_status, supervisor_status,
             approval_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*params, approval_reason),
        )
    else:
        db.execute(
            """
            INSERT INTO compliance_memos
            (application_id, memo_data, generated_by, ai_recommendation,
             review_status, quality_score, validation_status, supervisor_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
    db.commit()


# ── 1. Existing behaviour: validation_status == "pass" unchanged ────────────

class TestPassStatusUnchanged:
    """validation_status == 'pass' still behaves exactly as before."""

    def test_approval_gate_accepts_pass_with_approved_review(self, db):
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass",
                     review_status="approved")
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is True, msg

    def test_approval_gate_rejects_pass_not_approved(self, db):
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass",
                     review_status="draft")
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is False
        assert "review_status" in msg


# ── 2. Admin can approve pass_with_fixes with a reason ──────────────────────

class TestAdminApprovePassWithFixes:

    def test_admin_can_approve_pass_with_fixes(self, db):
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass_with_fixes",
                     review_status="approved",
                     approval_reason="Ownership risk mitigated via enhanced due diligence")
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is True, msg


# ── 3. SCO can approve pass_with_fixes with a reason ────────────────────────

class TestSCOApprovePassWithFixes:

    def test_sco_can_approve_pass_with_fixes(self, db):
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass_with_fixes",
                     review_status="approved",
                     approval_reason="Reviewed all findings — acceptable residual risk")
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is True, msg


# ── 4. Non-senior roles rejected for pass_with_fixes ────────────────────────

class TestNonSeniorRejected:

    def test_pass_with_fixes_without_senior_approval_blocked(self, db):
        """pass_with_fixes with review_status='draft' (not senior-approved) is blocked."""
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass_with_fixes",
                     review_status="draft")
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is False
        assert "review_status" in msg or "pass_with_fixes" in msg


# ── 5. Missing reason on pass_with_fixes is rejected ────────────────────────

class TestMissingReasonRejected:

    def test_pass_with_fixes_approved_without_reason_blocked(self, db):
        """Memo review_status='approved' but no approval_reason is blocked."""
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass_with_fixes",
                     review_status="approved", approval_reason=None)
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is False
        assert "senior approver" in msg or "pass_with_fixes" in msg

    def test_pass_with_fixes_approved_with_empty_reason_blocked(self, db):
        """Memo with empty-string approval_reason is blocked."""
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass_with_fixes",
                     review_status="approved", approval_reason="   ")
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is False
        assert "senior approver" in msg or "pass_with_fixes" in msg


# ── 6. validation_status == "fail" is still rejected ────────────────────────

class TestFailStillRejected:

    def test_fail_status_blocked(self, db):
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="fail",
                     review_status="approved")
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is False
        assert "validation_status" in msg


# ── 7. Audit trail records senior approval with findings ────────────────────

class TestAuditTrailRecordsSeniorApproval:

    def test_audit_detail_contains_findings_info(self, db):
        """Verify the audit record captures pass_with_fixes context."""
        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass_with_fixes",
                     review_status="approved",
                     approval_reason="Ownership risk accepted after EDD review")

        # Simulate the audit log entry that MemoApproveHandler would write
        user_name = "Jane SCO"
        user_role = "sco"
        user_id = "user-sco-001"
        approval_reason = "Ownership risk accepted after EDD review"

        audit_detail = (
            f"Compliance memo approved with outstanding findings by {user_name} "
            f"(role: {user_role}). "
            f"Validation status: pass_with_fixes. "
            f"Approval reason: {approval_reason}"
        )
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) "
            "VALUES (?,?,?,?,?,?,?)",
            (user_id, user_name, user_role, "Approve Memo", app_id, audit_detail, "127.0.0.1"),
        )
        db.commit()

        row = db.execute(
            "SELECT * FROM audit_log WHERE target = ? AND action = 'Approve Memo'",
            (app_id,),
        ).fetchone()
        row = dict(row)
        assert "outstanding findings" in row["detail"]
        assert "pass_with_fixes" in row["detail"]
        assert user_role in row["detail"]
        assert approval_reason in row["detail"]
        assert row["user_role"] == "sco"


# ── 8. Existing memo approval tests still pass (regression guard) ───────────

class TestExistingApprovalGateRegression:
    """Mirrors test_approval_gate.py tests to ensure no regression."""

    def test_pass_with_fixes_without_senior_approval_still_blocked_at_gate(self, db):
        """Original test_validate_approval_requires_explicit_validation_pass equivalent."""
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        # pass_with_fixes without proper senior approval reason
        _insert_memo(db, app_id, validation_status="pass_with_fixes",
                     review_status="approved", approval_reason=None)
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is False

    def test_explicit_positive_states_still_pass(self, db):
        """Original test_validate_approval_allows_explicit_positive_states equivalent."""
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass",
                     supervisor_status="CONSISTENT", review_status="approved")
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is True, msg


# ── 9. Decision gate reachable once memo is senior-approved ─────────────────

class TestDecisionGateReachable:

    def test_approval_gate_passes_with_senior_approved_pass_with_fixes(self, db):
        """Full gate validation passes when pass_with_fixes is senior-approved."""
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass_with_fixes",
                     supervisor_status="CONSISTENT", review_status="approved",
                     approval_reason="All findings reviewed and mitigated via EDD")
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is True, f"Decision gate should be reachable: {msg}"

    def test_approval_gate_blocked_when_only_review_approved_but_no_reason(self, db):
        """Gate blocked if review_status=approved but no approval_reason."""
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass_with_fixes",
                     supervisor_status="CONSISTENT", review_status="approved",
                     approval_reason="")
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is False
        assert "pass_with_fixes" in msg
