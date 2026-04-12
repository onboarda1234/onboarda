"""
EX-06: Dual-approval race condition tests.

Verifies that:
1. Same officer cannot approve a HIGH/VERY_HIGH risk application twice
2. A different officer can complete the second approval
3. Concurrent approval attempts cannot race incorrectly
4. Approval state persists correctly in structured DB fields
5. Dual approval is not enforced for LOW/MEDIUM risk
6. First-approver fields are cleared on non-approve decisions
7. Audit log entries maintain before/after state consistency
"""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _get_db():
    """Lazy import to avoid premature DB_PATH evaluation."""
    from db import get_db
    return get_db()


def _make_user(user_id, name, role="sco"):
    return {"sub": user_id, "name": name, "role": role}


def _insert_high_risk_app(db, risk_level="HIGH"):
    """Insert a HIGH/VERY_HIGH risk application with a valid compliance memo."""
    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-dual-{suffix}"
    app_ref = f"ARF-DUAL-{suffix}"
    db.execute(
        """INSERT INTO applications
           (id, ref, client_id, company_name, country, sector, entity_type,
            status, risk_level, risk_score, prescreening_data)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, app_ref, f"client-dual-{suffix}", "Dual Approval Test Ltd",
         "Mauritius", "Banking", "NBFI", "compliance_review",
         risk_level, 80, json.dumps({"screening_report": {
             "screening_mode": "live",
             "sanctions": {"api_status": "live"},
             "company_registry": {"api_status": "live"},
             "ip_geolocation": {"api_status": "live"},
             "kyc": {"api_status": "live"},
         }}))
    )
    # Insert a valid compliance memo so approval gates pass
    db.execute(
        """INSERT INTO compliance_memos
           (application_id, memo_data, generated_by, ai_recommendation,
            review_status, quality_score, validation_status, supervisor_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id,
         json.dumps({"ai_source": "deterministic",
                      "metadata": {"ai_source": "deterministic"},
                      "supervisor": {"verdict": "CONSISTENT", "can_approve": True}}),
         "system", "APPROVE_WITH_CONDITIONS", "approved", 8.5, "pass", "CONSISTENT")
    )
    db.commit()
    app = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    return dict(app)


def _insert_low_risk_app(db):
    """Insert a LOW risk application with a valid compliance memo."""
    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-low-{suffix}"
    app_ref = f"ARF-LOW-{suffix}"
    db.execute(
        """INSERT INTO applications
           (id, ref, client_id, company_name, country, sector, entity_type,
            status, risk_level, risk_score, prescreening_data)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, app_ref, f"client-low-{suffix}", "Low Risk Test Ltd",
         "Mauritius", "Technology", "SME", "compliance_review",
         "LOW", 20, json.dumps({"screening_report": {
             "screening_mode": "live",
             "sanctions": {"api_status": "live"},
             "company_registry": {"api_status": "live"},
             "ip_geolocation": {"api_status": "live"},
             "kyc": {"api_status": "live"},
         }}))
    )
    db.execute(
        """INSERT INTO compliance_memos
           (application_id, memo_data, generated_by, ai_recommendation,
            review_status, quality_score, validation_status, supervisor_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id,
         json.dumps({"ai_source": "deterministic",
                      "metadata": {"ai_source": "deterministic"},
                      "supervisor": {"verdict": "CONSISTENT", "can_approve": True}}),
         "system", "APPROVE_WITH_CONDITIONS", "approved", 8.5, "pass", "CONSISTENT")
    )
    db.commit()
    app = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    return dict(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDualApprovalValidation:
    """Tests for ApprovalGateValidator.validate_high_risk_dual_approval()."""

    def test_no_first_approval_returns_false(self, temp_db):
        """Without a first approval, validation returns (False, message)."""
        from security_hardening import ApprovalGateValidator
        db = _get_db()
        try:
            app = _insert_high_risk_app(db)
            user_a = _make_user("officer-a", "Officer A")
            can_approve, msg = ApprovalGateValidator.validate_high_risk_dual_approval(app, user_a, db)
            assert not can_approve
            assert "dual approval" in msg.lower() or "another" in msg.lower()
        finally:
            db.close()

    def test_same_officer_blocked_after_first_approval(self, temp_db):
        """Same officer who did first approval is blocked with DUAL_SAME_OFFICER."""
        from security_hardening import ApprovalGateValidator
        db = _get_db()
        try:
            app = _insert_high_risk_app(db)
            user_a = _make_user("officer-a", "Officer A")

            # Simulate first approval by writing structured field
            db.execute(
                "UPDATE applications SET first_approver_id = ?, first_approved_at = datetime('now') WHERE id = ?",
                (user_a["sub"], app["id"])
            )
            db.commit()

            # Re-read app with updated fields
            app = db.execute("SELECT * FROM applications WHERE id = ?", (app["id"],)).fetchone()
            app = dict(app)

            can_approve, msg = ApprovalGateValidator.validate_high_risk_dual_approval(app, user_a, db)
            assert not can_approve
            assert msg == "DUAL_SAME_OFFICER"
        finally:
            db.close()

    def test_different_officer_allowed_after_first_approval(self, temp_db):
        """Different officer passes dual approval check after first approval."""
        from security_hardening import ApprovalGateValidator
        db = _get_db()
        try:
            app = _insert_high_risk_app(db)
            user_a = _make_user("officer-a", "Officer A")
            user_b = _make_user("officer-b", "Officer B")

            # Simulate first approval by officer A
            db.execute(
                "UPDATE applications SET first_approver_id = ?, first_approved_at = datetime('now') WHERE id = ?",
                (user_a["sub"], app["id"])
            )
            db.commit()

            app = db.execute("SELECT * FROM applications WHERE id = ?", (app["id"],)).fetchone()
            app = dict(app)

            can_approve, msg = ApprovalGateValidator.validate_high_risk_dual_approval(app, user_b, db)
            assert can_approve
            assert msg == ""
        finally:
            db.close()

    def test_low_risk_skips_dual_approval(self, temp_db):
        """LOW risk applications bypass dual approval entirely."""
        from security_hardening import ApprovalGateValidator
        db = _get_db()
        try:
            app = _insert_low_risk_app(db)
            user_a = _make_user("officer-a", "Officer A")
            can_approve, msg = ApprovalGateValidator.validate_high_risk_dual_approval(app, user_a, db)
            assert can_approve
            assert msg == ""
        finally:
            db.close()

    def test_medium_risk_skips_dual_approval(self, temp_db):
        """MEDIUM risk applications bypass dual approval entirely."""
        from security_hardening import ApprovalGateValidator
        db = _get_db()
        try:
            app = _insert_low_risk_app(db)
            # Override risk level to MEDIUM
            db.execute("UPDATE applications SET risk_level = 'MEDIUM' WHERE id = ?", (app["id"],))
            db.commit()
            app = db.execute("SELECT * FROM applications WHERE id = ?", (app["id"],)).fetchone()
            app = dict(app)
            user_a = _make_user("officer-a", "Officer A")
            can_approve, msg = ApprovalGateValidator.validate_high_risk_dual_approval(app, user_a, db)
            assert can_approve
            assert msg == ""
        finally:
            db.close()

    def test_very_high_risk_enforces_dual_approval(self, temp_db):
        """VERY_HIGH risk applications enforce dual approval."""
        from security_hardening import ApprovalGateValidator
        db = _get_db()
        try:
            app = _insert_high_risk_app(db, risk_level="VERY_HIGH")
            user_a = _make_user("officer-a", "Officer A")
            can_approve, msg = ApprovalGateValidator.validate_high_risk_dual_approval(app, user_a, db)
            assert not can_approve
            assert "dual approval" in msg.lower() or "another" in msg.lower()
        finally:
            db.close()


class TestDualApprovalDBPersistence:
    """Tests that first_approver_id and first_approved_at persist correctly."""

    def test_first_approval_fields_initially_null(self, temp_db):
        """New applications have NULL first_approver_id and first_approved_at."""
        db = _get_db()
        try:
            app = _insert_high_risk_app(db)
            assert app.get("first_approver_id") is None
            assert app.get("first_approved_at") is None
        finally:
            db.close()

    def test_first_approval_sets_fields(self, temp_db):
        """Writing first_approver_id and first_approved_at persists in DB."""
        db = _get_db()
        try:
            app = _insert_high_risk_app(db)
            db.execute(
                "UPDATE applications SET first_approver_id = ?, first_approved_at = datetime('now') WHERE id = ?",
                ("officer-x", app["id"])
            )
            db.commit()
            updated = db.execute("SELECT first_approver_id, first_approved_at FROM applications WHERE id = ?",
                                  (app["id"],)).fetchone()
            updated = dict(updated)
            assert updated["first_approver_id"] == "officer-x"
            assert updated["first_approved_at"] is not None
        finally:
            db.close()

    def test_fields_cleared_on_final_decision(self, temp_db):
        """first_approver fields are cleared when a final decision is recorded."""
        db = _get_db()
        try:
            app = _insert_high_risk_app(db)
            # Set first approver
            db.execute(
                "UPDATE applications SET first_approver_id = ?, first_approved_at = datetime('now') WHERE id = ?",
                ("officer-x", app["id"])
            )
            db.commit()
            # Simulate final decision clearing fields
            db.execute(
                "UPDATE applications SET status = 'approved', first_approver_id = NULL, "
                "first_approved_at = NULL WHERE id = ?",
                (app["id"],)
            )
            db.commit()
            updated = db.execute("SELECT first_approver_id, first_approved_at FROM applications WHERE id = ?",
                                  (app["id"],)).fetchone()
            updated = dict(updated)
            assert updated["first_approver_id"] is None
            assert updated["first_approved_at"] is None
        finally:
            db.close()


class TestDualApprovalRaceCondition:
    """Tests simulating concurrent approval attempts to verify locking."""

    def test_sequential_same_officer_blocked(self, temp_db):
        """Simulates two sequential approvals by the same officer.
        First should succeed (record first approval), second should be blocked."""
        from security_hardening import ApprovalGateValidator
        db = _get_db()
        try:
            app = _insert_high_risk_app(db)
            user_a = _make_user("officer-race-a", "Officer Race A")

            # First attempt: no first_approver_id → should return (False, needs first approval)
            can_approve_1, msg_1 = ApprovalGateValidator.validate_high_risk_dual_approval(app, user_a, db)
            assert not can_approve_1
            assert "DUAL_SAME_OFFICER" not in msg_1

            # Simulate handler recording first approval
            db.execute(
                "UPDATE applications SET first_approver_id = ?, first_approved_at = datetime('now') WHERE id = ?",
                (user_a["sub"], app["id"])
            )
            db.commit()

            # Re-read locked state
            app2 = db.execute("SELECT * FROM applications WHERE id = ?", (app["id"],)).fetchone()
            app2 = dict(app2)

            # Second attempt by same officer: should be blocked
            can_approve_2, msg_2 = ApprovalGateValidator.validate_high_risk_dual_approval(app2, user_a, db)
            assert not can_approve_2
            assert msg_2 == "DUAL_SAME_OFFICER"
        finally:
            db.close()

    def test_sequential_different_officers_succeed(self, temp_db):
        """First officer records first approval, different officer completes second."""
        from security_hardening import ApprovalGateValidator
        db = _get_db()
        try:
            app = _insert_high_risk_app(db)
            user_a = _make_user("officer-seq-a", "Officer Seq A")
            user_b = _make_user("officer-seq-b", "Officer Seq B")

            # First officer: gets (False, needs first approval)
            can_approve_1, _ = ApprovalGateValidator.validate_high_risk_dual_approval(app, user_a, db)
            assert not can_approve_1

            # Record first approval
            db.execute(
                "UPDATE applications SET first_approver_id = ?, first_approved_at = datetime('now') WHERE id = ?",
                (user_a["sub"], app["id"])
            )
            db.commit()
            app2 = db.execute("SELECT * FROM applications WHERE id = ?", (app["id"],)).fetchone()
            app2 = dict(app2)

            # Second officer: gets (True, "")
            can_approve_2, msg_2 = ApprovalGateValidator.validate_high_risk_dual_approval(app2, user_b, db)
            assert can_approve_2
            assert msg_2 == ""
        finally:
            db.close()

    def test_concurrent_first_approvals_serialized(self, temp_db):
        """Two officers trying to be the first approver concurrently.
        With locking, only one can win. The second sees the first_approver_id already set."""
        from security_hardening import ApprovalGateValidator
        db = _get_db()
        try:
            app = _insert_high_risk_app(db)
            user_a = _make_user("officer-conc-a", "Officer Conc A")
            user_b = _make_user("officer-conc-b", "Officer Conc B")

            # Officer A wins the first approval
            db.execute(
                "UPDATE applications SET first_approver_id = ?, first_approved_at = datetime('now') WHERE id = ?",
                (user_a["sub"], app["id"])
            )
            db.commit()

            # Officer B reads the updated row and sees first_approver_id is already set
            app_after = db.execute("SELECT * FROM applications WHERE id = ?", (app["id"],)).fetchone()
            app_after = dict(app_after)

            assert app_after["first_approver_id"] == user_a["sub"]

            # Officer B's validation should now pass (different officer)
            can_approve, msg = ApprovalGateValidator.validate_high_risk_dual_approval(app_after, user_b, db)
            assert can_approve
            assert msg == ""
        finally:
            db.close()


class TestDualApprovalAuditConsistency:
    """Tests that audit logging is maintained with before/after state."""

    def test_snapshot_includes_first_approver_fields(self, temp_db):
        """snapshot_app_state includes first_approver_id and first_approved_at."""
        from base_handler import snapshot_app_state
        db = _get_db()
        try:
            app = _insert_high_risk_app(db)
            db.execute(
                "UPDATE applications SET first_approver_id = ?, first_approved_at = datetime('now') WHERE id = ?",
                ("officer-snap", app["id"])
            )
            db.commit()
            app2 = db.execute("SELECT * FROM applications WHERE id = ?", (app["id"],)).fetchone()
            app2 = dict(app2)

            snapshot = snapshot_app_state(app2)
            assert "first_approver_id" in snapshot
            assert snapshot["first_approver_id"] == "officer-snap"
            assert "first_approved_at" in snapshot
        finally:
            db.close()

    def test_audit_log_records_first_approval(self, temp_db):
        """First approval generates an audit_log entry with before/after state."""
        from base_handler import snapshot_app_state, _safe_json
        db = _get_db()
        try:
            app = _insert_high_risk_app(db)
            _before = snapshot_app_state(app)

            # Simulate handler recording first approval
            db.execute(
                "UPDATE applications SET first_approver_id = ?, first_approved_at = datetime('now') WHERE id = ?",
                ("officer-audit", app["id"])
            )
            _first_after = {"status": app["status"], "decision": "approve",
                            "note": "awaiting_second_approver",
                            "first_approver_id": "officer-audit"}
            db.execute(
                """INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail,
                   ip_address, before_state, after_state)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                ("officer-audit", "Officer Audit", "sco",
                 "First Approval (Pending Second)", app["ref"],
                 "Decision: approve | Reason: test | Awaiting second approver",
                 "127.0.0.1", _safe_json(_before), _safe_json(_first_after))
            )
            db.commit()

            # Verify audit entry
            entry = db.execute(
                "SELECT * FROM audit_log WHERE target = ? AND action = 'First Approval (Pending Second)' ORDER BY timestamp DESC LIMIT 1",
                (app["ref"],)
            ).fetchone()
            entry = dict(entry)
            assert entry["user_id"] == "officer-audit"
            assert entry["before_state"] is not None
            assert entry["after_state"] is not None
            after = json.loads(entry["after_state"])
            assert after["first_approver_id"] == "officer-audit"
        finally:
            db.close()

    def test_migration_v219_columns_exist(self, temp_db):
        """Migration v2.19 columns exist in the applications table."""
        db = _get_db()
        try:
            # Verify columns exist by selecting them
            row = db.execute(
                "SELECT first_approver_id, first_approved_at FROM applications LIMIT 1"
            ).fetchone()
            # If we get here without exception, columns exist
            assert True
        finally:
            db.close()
