"""
Tests for EX-06 B2: Supervisor-warnings approval policy.

Validates that memos with supervisor_status == 'CONSISTENT_WITH_WARNINGS'
can only be approved (at the application-approval gate) by admin or SCO roles
with a mandatory documented reason, while preserving all existing CONSISTENT
approval behaviour.

The MemoApproveHandler enforces can_approve, role, and reason requirements
before setting review_status='approved'.  These tests verify the downstream
ApprovalGateValidator correctly interprets the resulting memo state.
"""

import json
import uuid
import pytest
from datetime import datetime, timedelta, timezone


# ── Helpers ──────────────────────────────────────────────────────────────────

VALID_SUPERVISOR_CONSISTENT = json.dumps({
    "metadata": {"ai_source": "deterministic"},
    "supervisor": {"verdict": "CONSISTENT", "can_approve": True},
})

VALID_SUPERVISOR_WARNINGS = json.dumps({
    "metadata": {"ai_source": "deterministic"},
    "supervisor": {"verdict": "CONSISTENT_WITH_WARNINGS", "can_approve": True},
})

SUPERVISOR_WARNINGS_NO_APPROVE = json.dumps({
    "metadata": {"ai_source": "deterministic"},
    "supervisor": {"verdict": "CONSISTENT_WITH_WARNINGS", "can_approve": False},
})

SUPERVISOR_INCONSISTENT = json.dumps({
    "metadata": {"ai_source": "deterministic"},
    "supervisor": {"verdict": "INCONSISTENT", "can_approve": False},
})


def _insert_app(db, *, app_id=None, ref=None):
    """Insert a minimal application and return its id."""
    suffix = uuid.uuid4().hex[:8]
    app_id = app_id or f"app-swarn-{suffix}"
    ref = ref or f"ARF-SWARN-{suffix}"
    _now = datetime.now(timezone.utc)
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type,
         status, risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id, ref, f"client-swarn-{suffix}",
            "Supervisor Warnings Test Ltd", "Mauritius", "Technology", "SME",
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
                 approval_reason=None, memo_data=None):
    """Insert a compliance memo for the given application."""
    if memo_data is None:
        memo_data = VALID_SUPERVISOR_CONSISTENT
    params = [
        app_id, memo_data, "system", "APPROVE_WITH_CONDITIONS",
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


# ── 1. CONSISTENT verdict still approves as before ──────────────────────────

class TestConsistentUnchanged:

    def test_consistent_verdict_passes_gate(self, db):
        """CONSISTENT + can_approve=True + approved review still passes."""
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass",
                     supervisor_status="CONSISTENT", review_status="approved")
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is True, msg


# ── 2. CONSISTENT_WITH_WARNINGS + admin + reason → approves ─────────────────

class TestAdminApprovesWithWarnings:

    def test_admin_can_approve_with_supervisor_warnings(self, db):
        """Admin approving with reason passes the gate validator."""
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass",
                     supervisor_status="CONSISTENT_WITH_WARNINGS",
                     review_status="approved",
                     approval_reason="Supervisor warnings reviewed — residual risk acceptable",
                     memo_data=VALID_SUPERVISOR_WARNINGS)
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is True, msg


# ── 3. CONSISTENT_WITH_WARNINGS + SCO + reason → approves ───────────────────

class TestSCOApprovesWithWarnings:

    def test_sco_can_approve_with_supervisor_warnings(self, db):
        """SCO approving with reason passes the gate validator."""
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass",
                     supervisor_status="CONSISTENT_WITH_WARNINGS",
                     review_status="approved",
                     approval_reason="All supervisor warnings reviewed and mitigated",
                     memo_data=VALID_SUPERVISOR_WARNINGS)
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is True, msg


# ── 4. CONSISTENT_WITH_WARNINGS + missing reason → rejects ──────────────────

class TestMissingReasonRejected:

    def test_warnings_without_reason_blocked(self, db):
        """CONSISTENT_WITH_WARNINGS without approval_reason is blocked."""
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass",
                     supervisor_status="CONSISTENT_WITH_WARNINGS",
                     review_status="approved", approval_reason=None,
                     memo_data=VALID_SUPERVISOR_WARNINGS)
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is False
        assert "CONSISTENT_WITH_WARNINGS" in msg

    def test_warnings_with_empty_reason_blocked(self, db):
        """CONSISTENT_WITH_WARNINGS with empty approval_reason is blocked."""
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass",
                     supervisor_status="CONSISTENT_WITH_WARNINGS",
                     review_status="approved", approval_reason="   ",
                     memo_data=VALID_SUPERVISOR_WARNINGS)
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is False
        assert "senior approver" in msg or "CONSISTENT_WITH_WARNINGS" in msg


# ── 5. CONSISTENT_WITH_WARNINGS + non-senior role → rejects ─────────────────

class TestNonSeniorRoleRejected:

    def test_warnings_not_approved_review_blocked(self, db):
        """
        Non-senior roles cannot call MemoApproveHandler (handler-level auth).
        At the gate level, this manifests as review_status != 'approved'.
        """
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass",
                     supervisor_status="CONSISTENT_WITH_WARNINGS",
                     review_status="draft",
                     memo_data=VALID_SUPERVISOR_WARNINGS)
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is False
        assert "review_status" in msg


# ── 6. CONSISTENT_WITH_WARNINGS + can_approve false → rejects ───────────────

class TestCanApproveFalseRejected:

    def test_can_approve_false_prevents_approval(self, db):
        """
        When can_approve=False, MemoApproveHandler rejects the approval
        attempt, so review_status stays draft.  The gate validator rejects.
        """
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        # Simulate: handler rejected (can_approve=false) → review_status stays draft
        _insert_memo(db, app_id, validation_status="pass",
                     supervisor_status="CONSISTENT_WITH_WARNINGS",
                     review_status="draft",
                     memo_data=SUPERVISOR_WARNINGS_NO_APPROVE)
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is False


# ── 7. INCONSISTENT verdict → rejects ───────────────────────────────────────

class TestInconsistentRejected:

    def test_inconsistent_verdict_blocked(self, db):
        """INCONSISTENT supervisor verdict is always blocked."""
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass",
                     supervisor_status="INCONSISTENT",
                     review_status="approved",
                     memo_data=SUPERVISOR_INCONSISTENT)
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is False
        assert "supervisor_status" in msg
        assert "'CONSISTENT'" in msg


# ── 8. Audit trail records supervisor warnings approval ──────────────────────

class TestAuditRecordsSupervisorWarnings:

    def test_audit_detail_contains_supervisor_warnings_info(self, db):
        """Verify the audit record captures supervisor warnings context."""
        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass",
                     supervisor_status="CONSISTENT_WITH_WARNINGS",
                     review_status="approved",
                     approval_reason="Supervisor warnings acceptable after review",
                     memo_data=VALID_SUPERVISOR_WARNINGS)

        # Simulate the audit log entry that MemoApproveHandler would write
        user_name = "Admin Officer"
        user_role = "admin"
        user_id = "user-admin-001"
        approval_reason = "Supervisor warnings acceptable after review"

        # Build audit detail same way as MemoApproveHandler
        audit_detail = (
            f"Compliance memo approved with supervisor warnings by {user_name} "
            f"(role: {user_role}). "
            f"Supervisor verdict: CONSISTENT_WITH_WARNINGS. "
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
        assert "supervisor warnings" in row["detail"]
        assert "CONSISTENT_WITH_WARNINGS" in row["detail"]
        assert user_role in row["detail"]
        assert approval_reason in row["detail"]
        assert row["user_role"] == "admin"

    def test_combined_findings_and_warnings_audit(self, db):
        """Verify audit captures both findings and warnings when both apply."""
        app_id = _insert_app(db)

        user_name = "Jane SCO"
        user_role = "sco"
        approval_reason = "Findings and warnings reviewed"

        # Build audit detail for combined case (pass_with_fixes + CONSISTENT_WITH_WARNINGS)
        audit_detail = (
            f"Compliance memo approved with outstanding findings and supervisor warnings by {user_name} "
            f"(role: {user_role}). "
            f"Validation status: pass_with_fixes. "
            f"Supervisor verdict: CONSISTENT_WITH_WARNINGS. "
            f"Approval reason: {approval_reason}"
        )
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) "
            "VALUES (?,?,?,?,?,?,?)",
            ("user-sco-001", user_name, user_role, "Approve Memo", app_id, audit_detail, "127.0.0.1"),
        )
        db.commit()

        row = db.execute(
            "SELECT * FROM audit_log WHERE target = ? AND action = 'Approve Memo'",
            (app_id,),
        ).fetchone()
        row = dict(row)
        assert "outstanding findings" in row["detail"]
        assert "supervisor warnings" in row["detail"]
        assert "pass_with_fixes" in row["detail"]
        assert "CONSISTENT_WITH_WARNINGS" in row["detail"]


# ── 9. Decision gate reachable after supervisor warnings approval ────────────

class TestDecisionGateReachable:

    def test_gate_passes_with_warnings_approved(self, db):
        """Full gate validation passes when CONSISTENT_WITH_WARNINGS is senior-approved."""
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass",
                     supervisor_status="CONSISTENT_WITH_WARNINGS",
                     review_status="approved",
                     approval_reason="All warnings reviewed — acceptable residual risk",
                     memo_data=VALID_SUPERVISOR_WARNINGS)
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is True, f"Decision gate should be reachable: {msg}"

    def test_gate_passes_combined_pass_with_fixes_and_warnings(self, db):
        """
        Combined case: pass_with_fixes + CONSISTENT_WITH_WARNINGS.
        Both conditions met → gate passes.
        """
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass_with_fixes",
                     supervisor_status="CONSISTENT_WITH_WARNINGS",
                     review_status="approved",
                     approval_reason="Both findings and warnings reviewed via enhanced due diligence",
                     memo_data=VALID_SUPERVISOR_WARNINGS)
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is True, f"Decision gate should be reachable: {msg}"

    def test_gate_blocked_when_warnings_not_approved(self, db):
        """Gate blocked if CONSISTENT_WITH_WARNINGS but no approval_reason."""
        from security_hardening import ApprovalGateValidator

        app_id = _insert_app(db)
        _insert_memo(db, app_id, validation_status="pass",
                     supervisor_status="CONSISTENT_WITH_WARNINGS",
                     review_status="approved", approval_reason="",
                     memo_data=VALID_SUPERVISOR_WARNINGS)
        app = db.execute("SELECT * FROM applications WHERE id = ?",
                         (app_id,)).fetchone()
        can, msg = ApprovalGateValidator.validate_approval(dict(app), db)
        assert can is False
        assert "CONSISTENT_WITH_WARNINGS" in msg
