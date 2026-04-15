"""
Tests for memo-staleness logic with approval-state writes.

Verifies that:
1. Real application-input changes after memo generation still block approval
2. Screening reruns that change relevant state still block until memo regenerated
3. First-approval write alone does not falsely retrigger stale-memo block
4. Same-officer second approval still fails
5. Different-officer second approval succeeds when all prerequisites are met
6. No regression to EX-06 dual-approval flow
"""
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(user_id, name, role="sco"):
    return {"sub": user_id, "name": name, "role": role}


def _valid_screening():
    return {
        "screening_mode": "live",
        "sanctions": {"api_status": "live", "matched": False, "source": "sumsub"},
        "company_registry": {"api_status": "live"},
        "ip_geolocation": {"api_status": "live"},
        "kyc": {"api_status": "live"},
        "screened_at": datetime.now().isoformat(),
    }


def _insert_app(db, risk_level="HIGH", status="compliance_review",
                app_updated_at=None, inputs_updated_at=None,
                submitted_at=None):
    """Insert an application with configurable timestamps."""
    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-stale-{suffix}"
    app_ref = f"ARF-STALE-{suffix}"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updated = app_updated_at or now
    inputs_upd = inputs_updated_at or updated
    sub_at = submitted_at or (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    prescreening = json.dumps({"screening_report": _valid_screening()})
    db.execute(
        """INSERT INTO applications
           (id, ref, client_id, company_name, country, sector, entity_type,
            status, risk_level, risk_score, prescreening_data,
            submitted_at, updated_at, inputs_updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, app_ref, f"client-{suffix}", "Staleness Test Ltd",
         "Mauritius", "Banking", "NBFI", status,
         risk_level, 80, prescreening,
         sub_at, updated, inputs_upd)
    )
    db.commit()
    return app_id, app_ref


def _insert_memo(db, app_id, created_at=None):
    """Insert a valid compliance memo for an application."""
    now = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    memo_data = json.dumps({
        "ai_source": "deterministic",
        "metadata": {"ai_source": "deterministic"},
        "supervisor": {"verdict": "CONSISTENT", "can_approve": True},
    })
    db.execute(
        """INSERT INTO compliance_memos
           (application_id, version, memo_data, generated_by, ai_recommendation,
            review_status, quality_score, validation_status, supervisor_status,
            blocked, created_at)
           VALUES (?, 1, ?, 'system', 'APPROVE_WITH_CONDITIONS',
                   'approved', 8.5, 'pass', 'CONSISTENT', 0, ?)""",
        (app_id, memo_data, now)
    )
    db.commit()


def _get_app(db, app_id):
    row = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Tests: Staleness detection correctness
# ---------------------------------------------------------------------------

class TestMemoStalenessInputsUpdatedAt:
    """Staleness detection should use inputs_updated_at, not updated_at."""

    def test_real_input_change_blocks_approval(self, db):
        """When inputs_updated_at is after memo creation, approval is blocked."""
        from security_hardening import ApprovalGateValidator

        now = datetime.now()
        memo_time = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        input_time = now.strftime("%Y-%m-%d %H:%M:%S")

        app_id, _ = _insert_app(db, risk_level="MEDIUM",
                                inputs_updated_at=input_time)
        _insert_memo(db, app_id, created_at=memo_time)
        app = _get_app(db, app_id)

        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can, "Expected approval blocked for stale memo after real input change"
        assert "modified after" in err.lower() or "memo" in err.lower()

    def test_screening_rerun_blocks_approval(self, db):
        """Screening rerun updates inputs_updated_at and blocks approval."""
        from security_hardening import ApprovalGateValidator

        now = datetime.now()
        memo_time = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        # Simulate screening rerun updating inputs_updated_at after memo
        rerun_time = now.strftime("%Y-%m-%d %H:%M:%S")

        app_id, _ = _insert_app(db, risk_level="MEDIUM",
                                inputs_updated_at=rerun_time)
        _insert_memo(db, app_id, created_at=memo_time)
        app = _get_app(db, app_id)

        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can, "Expected approval blocked after screening rerun"
        assert "modified after" in err.lower() or "memo" in err.lower()

    def test_first_approval_write_does_not_retrigger_staleness(self, db):
        """First-approval write (updated_at only) should NOT retrigger stale-memo.

        The approval gate should compare against inputs_updated_at, which is
        not touched by the first-approval UPDATE.
        """
        from security_hardening import ApprovalGateValidator

        now = datetime.now()
        # Memo was created 1 hour ago
        memo_time = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        # inputs_updated_at is 2 hours ago (before memo), so memo is fresh
        input_time = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        # But updated_at is NOW (simulating first-approval write)
        row_updated_time = now.strftime("%Y-%m-%d %H:%M:%S")

        app_id, _ = _insert_app(db, risk_level="MEDIUM",
                                app_updated_at=row_updated_time,
                                inputs_updated_at=input_time)
        _insert_memo(db, app_id, created_at=memo_time)
        app = _get_app(db, app_id)

        # Verify the setup: updated_at > memo but inputs_updated_at < memo
        assert app["updated_at"] > memo_time, "Precondition: updated_at should be after memo"
        assert app["inputs_updated_at"] < memo_time, "Precondition: inputs_updated_at should be before memo"

        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert can, f"Expected approval to pass (no real input change) but got: {err}"

    def test_fresh_memo_after_input_change_passes(self, db):
        """When memo is regenerated after input change, approval passes."""
        from security_hardening import ApprovalGateValidator

        now = datetime.now()
        input_time = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        # Memo generated AFTER input change
        memo_time = now.strftime("%Y-%m-%d %H:%M:%S")

        app_id, _ = _insert_app(db, risk_level="MEDIUM",
                                inputs_updated_at=input_time)
        _insert_memo(db, app_id, created_at=memo_time)
        app = _get_app(db, app_id)

        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert can, f"Expected approval to pass with fresh memo but got: {err}"

    def test_fallback_to_updated_at_when_inputs_updated_at_null(self, db):
        """When inputs_updated_at is NULL, gate falls back to updated_at."""
        from security_hardening import ApprovalGateValidator

        now = datetime.now()
        memo_time = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        row_updated_time = now.strftime("%Y-%m-%d %H:%M:%S")

        app_id, _ = _insert_app(db, risk_level="MEDIUM",
                                app_updated_at=row_updated_time,
                                inputs_updated_at=row_updated_time)
        _insert_memo(db, app_id, created_at=memo_time)

        # Force inputs_updated_at to NULL to test fallback
        db.execute("UPDATE applications SET inputs_updated_at = NULL WHERE id = ?",
                   (app_id,))
        db.commit()
        app = _get_app(db, app_id)

        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can, "Expected fallback to updated_at to detect staleness"


# ---------------------------------------------------------------------------
# Tests: Full dual-approval flow with staleness
# ---------------------------------------------------------------------------

class TestDualApprovalNoFalseStaleness:
    """EX-06 dual-approval flow should not produce false stale-memo blocks."""

    def test_first_approval_then_second_approval_succeeds(self, db):
        """Full dual-approval: first-approval write → second officer can approve.

        The first-approval UPDATE only sets updated_at, not inputs_updated_at.
        Therefore, the approval gate should NOT detect memo staleness on the
        second approval attempt.
        """
        from security_hardening import ApprovalGateValidator

        now = datetime.now()
        input_time = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        memo_time = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

        app_id, app_ref = _insert_app(
            db, risk_level="HIGH",
            app_updated_at=input_time,
            inputs_updated_at=input_time,
        )
        _insert_memo(db, app_id, created_at=memo_time)

        user_a = _make_user("officer-a", "Officer A")
        user_b = _make_user("officer-b", "Officer B")

        # Step 1: First approval — sets first_approver_id and updated_at
        app = _get_app(db, app_id)
        can, msg = ApprovalGateValidator.validate_high_risk_dual_approval(app, user_a, db)
        assert not can  # first approval needed
        # Record first approval (mimics server.py logic)
        db.execute(
            "UPDATE applications SET first_approver_id = ?, first_approved_at = datetime('now'), "
            "updated_at = datetime('now') WHERE id = ?",
            (user_a["sub"], app_id)
        )
        db.commit()

        # Step 2: Re-read app — updated_at is now, but inputs_updated_at is still old
        app = _get_app(db, app_id)
        assert app["first_approver_id"] == "officer-a"
        assert app["updated_at"] > memo_time, "updated_at should be after memo"
        assert app["inputs_updated_at"] < memo_time, "inputs_updated_at should be before memo"

        # Step 3: Approval gate should PASS (no real input change)
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert can, f"Second-officer approval gate should pass but got: {err}"

        # Step 4: Dual-approval validation for officer B should succeed
        can, msg = ApprovalGateValidator.validate_high_risk_dual_approval(app, user_b, db)
        assert can, f"Second-officer dual-approval should pass but got: {msg}"

    def test_same_officer_second_approval_still_blocked(self, db):
        """Same officer cannot do both first and second approval."""
        from security_hardening import ApprovalGateValidator

        now = datetime.now()
        input_time = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        memo_time = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

        app_id, _ = _insert_app(
            db, risk_level="HIGH",
            app_updated_at=input_time,
            inputs_updated_at=input_time,
        )
        _insert_memo(db, app_id, created_at=memo_time)

        user_a = _make_user("officer-a", "Officer A")

        # Record first approval
        db.execute(
            "UPDATE applications SET first_approver_id = ?, first_approved_at = datetime('now'), "
            "updated_at = datetime('now') WHERE id = ?",
            (user_a["sub"], app_id)
        )
        db.commit()

        app = _get_app(db, app_id)
        can, msg = ApprovalGateValidator.validate_high_risk_dual_approval(app, user_a, db)
        assert not can
        assert msg == "DUAL_SAME_OFFICER"

    def test_real_change_after_first_approval_blocks_second(self, db):
        """If substantive data changes after first-approval, second approval is blocked."""
        from security_hardening import ApprovalGateValidator

        now = datetime.now()
        old_time = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        memo_time = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

        app_id, _ = _insert_app(
            db, risk_level="HIGH",
            app_updated_at=old_time,
            inputs_updated_at=old_time,
        )
        _insert_memo(db, app_id, created_at=memo_time)

        user_a = _make_user("officer-a", "Officer A")
        user_b = _make_user("officer-b", "Officer B")

        # First approval
        db.execute(
            "UPDATE applications SET first_approver_id = ?, first_approved_at = datetime('now'), "
            "updated_at = datetime('now') WHERE id = ?",
            (user_a["sub"], app_id)
        )
        db.commit()

        # Simulate a real data change (e.g., screening rerun)
        # This should update BOTH updated_at AND inputs_updated_at
        db.execute(
            "UPDATE applications SET updated_at = datetime('now'), "
            "inputs_updated_at = datetime('now') WHERE id = ?",
            (app_id,)
        )
        db.commit()

        app = _get_app(db, app_id)
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can, "Expected approval blocked after real data change"
        assert "modified after" in err.lower() or "memo" in err.lower()

    def test_low_risk_no_dual_approval_no_staleness_issue(self, db):
        """LOW risk apps skip dual approval; first-approval write is irrelevant."""
        from security_hardening import ApprovalGateValidator

        now = datetime.now()
        input_time = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        memo_time = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

        app_id, _ = _insert_app(
            db, risk_level="LOW",
            app_updated_at=input_time,
            inputs_updated_at=input_time,
        )
        _insert_memo(db, app_id, created_at=memo_time)

        user_a = _make_user("officer-a", "Officer A")
        app = _get_app(db, app_id)

        # Dual approval skipped for LOW risk
        can, msg = ApprovalGateValidator.validate_high_risk_dual_approval(app, user_a, db)
        assert can
        assert msg == ""

    def test_very_high_risk_dual_approval_flow(self, db):
        """VERY_HIGH risk follows same dual-approval flow without false staleness."""
        from security_hardening import ApprovalGateValidator

        now = datetime.now()
        input_time = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        memo_time = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

        app_id, _ = _insert_app(
            db, risk_level="VERY_HIGH",
            app_updated_at=input_time,
            inputs_updated_at=input_time,
        )
        _insert_memo(db, app_id, created_at=memo_time)

        user_a = _make_user("officer-a", "Officer A")
        user_b = _make_user("officer-b", "Officer B")

        # First approval
        app = _get_app(db, app_id)
        can, msg = ApprovalGateValidator.validate_high_risk_dual_approval(app, user_a, db)
        assert not can  # needs first approval
        db.execute(
            "UPDATE applications SET first_approver_id = ?, first_approved_at = datetime('now'), "
            "updated_at = datetime('now') WHERE id = ?",
            (user_a["sub"], app_id)
        )
        db.commit()

        # Second officer - gate should pass
        app = _get_app(db, app_id)
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert can, f"VERY_HIGH approval gate should pass but got: {err}"

        can, msg = ApprovalGateValidator.validate_high_risk_dual_approval(app, user_b, db)
        assert can, f"Second-officer dual approval should pass but got: {msg}"


# ---------------------------------------------------------------------------
# Tests: inputs_updated_at column schema and migration
# ---------------------------------------------------------------------------

class TestInputsUpdatedAtSchema:
    """Verify the inputs_updated_at column exists and has correct defaults."""

    def test_column_exists_in_applications(self, db):
        """applications table has inputs_updated_at column."""
        row = db.execute(
            "PRAGMA table_info(applications)"
        ).fetchall()
        columns = [r["name"] if isinstance(r, sqlite3.Row) else r[1] for r in row]
        assert "inputs_updated_at" in columns

    def test_default_value_set_on_insert(self, db):
        """New application gets inputs_updated_at defaulted."""
        suffix = uuid.uuid4().hex[:8]
        db.execute(
            """INSERT INTO applications
               (id, ref, company_name, status, risk_level, risk_score)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (f"schema-{suffix}", f"ARF-SCH-{suffix}", "Schema Test", "draft", "LOW", 10)
        )
        db.commit()
        app = db.execute(
            "SELECT inputs_updated_at FROM applications WHERE id = ?",
            (f"schema-{suffix}",)
        ).fetchone()
        assert app["inputs_updated_at"] is not None
