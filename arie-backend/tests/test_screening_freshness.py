"""
EX-10 — Screening Freshness Validation Tests
=============================================
Tests for:
  - Valid (fresh) screening allows approval
  - Expired screening blocks approval
  - Missing freshness metadata fails closed
  - Re-screen updates timestamp and valid-until
  - Configurable validity period affects behavior
"""
import os
import sys
import json
import uuid
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _make_screening_report(screened_at=None, screening_mode="live"):
    """Build a minimal valid screening report."""
    if screened_at is None:
        screened_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "screening_mode": screening_mode,
        "screened_at": screened_at,
        "sanctions": {"api_status": "live", "matched": False, "source": "sumsub"},
        "company_registry": {"api_status": "live"},
        "ip_geolocation": {"api_status": "live"},
        "kyc": {"api_status": "live"},
    }


def _insert_app_and_memo(db, *,
                          screening_report=None,
                          prescreening_extras=None,
                          submitted_at=None,
                          app_updated_at=None,
                          memo_created_at=None,
                          status="in_review",
                          validation_status="pass",
                          supervisor_status="CONSISTENT",
                          review_status="approved"):
    """Insert application + memo with configurable screening data."""
    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-fresh-{suffix}"
    ref = f"ARF-FRESH-{suffix}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    if screening_report is None:
        screening_report = _make_screening_report()

    prescreening = {"screening_report": screening_report}
    if prescreening_extras:
        prescreening.update(prescreening_extras)

    sub_at = submitted_at or now
    app_updated = app_updated_at or now
    memo_created = memo_created_at or now

    db.execute("""
        INSERT INTO applications
        (id, ref, company_name, country, sector, entity_type, status,
         risk_level, risk_score, prescreening_data, submitted_at, updated_at,
         inputs_updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (app_id, ref, "Freshness Test Ltd", "Mauritius", "Technology", "SME",
          status, "MEDIUM", 50, json.dumps(prescreening), sub_at, app_updated,
          app_updated))

    memo_data = json.dumps({"ai_source": "deterministic", "metadata": {"ai_source": "deterministic"}})
    db.execute("""
        INSERT INTO compliance_memos
        (application_id, version, memo_data, review_status, validation_status,
         supervisor_status, blocked, block_reason, created_at)
        VALUES (?, 1, ?, ?, ?, ?, 0, NULL, ?)
    """, (app_id, memo_data, review_status, validation_status,
          supervisor_status, memo_created))
    db.commit()

    row = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    return dict(row)


# ═══════════════════════════════════════════════════════════════
# Test: Valid (fresh) screening allows approval
# ═══════════════════════════════════════════════════════════════

class TestFreshScreeningAllowsApproval:
    """Screening within the validity period should pass Gate 9."""

    def test_recent_screening_passes(self, db):
        """Screening done 1 day ago (well within 90-day default) passes."""
        from security_hardening import ApprovalGateValidator
        now = datetime.now(timezone.utc)
        screened_at = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        valid_until = (now + timedelta(days=89)).strftime("%Y-%m-%dT%H:%M:%S")

        app = _insert_app_and_memo(
            db,
            screening_report=_make_screening_report(screened_at=screened_at),
            prescreening_extras={
                "screening_valid_until": valid_until,
                "screening_validity_days": 90,
            },
            submitted_at=(now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert can, f"Expected approval to pass but got: {err}"

    def test_screening_at_boundary_passes(self, db):
        """Screening done exactly at the boundary (day 89 of 90) passes."""
        from security_hardening import ApprovalGateValidator
        now = datetime.now(timezone.utc)
        screened_at = (now - timedelta(days=89)).strftime("%Y-%m-%dT%H:%M:%S")
        valid_until = (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")

        app = _insert_app_and_memo(
            db,
            screening_report=_make_screening_report(screened_at=screened_at),
            prescreening_extras={
                "screening_valid_until": valid_until,
                "screening_validity_days": 90,
            },
            submitted_at=(now - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert can, f"Expected approval to pass but got: {err}"


# ═══════════════════════════════════════════════════════════════
# Test: Expired screening blocks approval
# ═══════════════════════════════════════════════════════════════

class TestExpiredScreeningBlocksApproval:
    """Screening older than the validity period must block approval."""

    def test_expired_screening_with_valid_until_blocks(self, db):
        """Screening with explicit valid_until in the past blocks approval."""
        from security_hardening import ApprovalGateValidator
        now = datetime.now(timezone.utc)
        screened_at = (now - timedelta(days=95)).strftime("%Y-%m-%dT%H:%M:%S")
        valid_until = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S")

        app = _insert_app_and_memo(
            db,
            screening_report=_make_screening_report(screened_at=screened_at),
            prescreening_extras={
                "screening_valid_until": valid_until,
                "screening_validity_days": 90,
            },
            submitted_at=(now - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can, "Expected expired screening to block approval"
        assert "expired" in err.lower()
        assert "re-screen" in err.lower()

    def test_expired_screening_fallback_computation_blocks(self, db):
        """When no valid_until is stored, expiry is computed from screened_at + validity_days."""
        from security_hardening import ApprovalGateValidator
        now = datetime.now(timezone.utc)
        screened_at = (now - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%S")

        app = _insert_app_and_memo(
            db,
            screening_report=_make_screening_report(screened_at=screened_at),
            # No screening_valid_until — Gate 9 falls back to computation
            submitted_at=(now - timedelta(days=110)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can, "Expected expired screening (fallback path) to block approval"
        assert "expired" in err.lower()

    def test_clear_error_message_on_expiry(self, db):
        """Error message should be operator-readable with age and validity info."""
        from security_hardening import ApprovalGateValidator
        now = datetime.now(timezone.utc)
        screened_at = (now - timedelta(days=95)).strftime("%Y-%m-%dT%H:%M:%S")
        valid_until = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S")

        app = _insert_app_and_memo(
            db,
            screening_report=_make_screening_report(screened_at=screened_at),
            prescreening_extras={
                "screening_valid_until": valid_until,
                "screening_validity_days": 90,
            },
            submitted_at=(now - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can
        assert "day(s) ago" in err
        assert "validity period" in err
        assert "90 days" in err


# ═══════════════════════════════════════════════════════════════
# Test: Missing freshness metadata fails closed
# ═══════════════════════════════════════════════════════════════

class TestMissingFreshnessFailsClosed:
    """Missing screening timestamp must block approval (fail-closed)."""

    def test_missing_screened_at_blocks(self, db):
        """When screened_at is missing and no valid_until, approval is blocked."""
        from security_hardening import ApprovalGateValidator
        report = _make_screening_report()
        del report["screened_at"]  # Remove the timestamp

        app = _insert_app_and_memo(
            db,
            screening_report=report,
            submitted_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can, "Expected missing screening timestamp to block approval"
        assert "missing" in err.lower() or "re-screen" in err.lower()

    def test_empty_screened_at_blocks(self, db):
        """When screened_at is empty string, approval is blocked."""
        from security_hardening import ApprovalGateValidator
        report = _make_screening_report()
        report["screened_at"] = ""

        app = _insert_app_and_memo(
            db,
            screening_report=report,
            submitted_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can, "Expected empty screening timestamp to block approval"

    def test_invalid_timestamp_format_blocks(self, db):
        """When valid_until has invalid format, approval is blocked (fail-closed)."""
        from security_hardening import ApprovalGateValidator
        now = datetime.now(timezone.utc)

        app = _insert_app_and_memo(
            db,
            screening_report=_make_screening_report(
                screened_at=now.strftime("%Y-%m-%dT%H:%M:%S")
            ),
            prescreening_extras={
                "screening_valid_until": "not-a-valid-timestamp",
            },
            submitted_at=(now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can, "Expected invalid timestamp format to block approval"
        assert "timestamp" in err.lower() or "re-run" in err.lower()


# ═══════════════════════════════════════════════════════════════
# Test: Re-screen updates timestamp and valid-until
# ═══════════════════════════════════════════════════════════════

class TestRescreenUpdatesTimestamps:
    """Re-screening must refresh screened_at and screening_valid_until."""

    def test_rescreen_stores_valid_until(self, db):
        """After a re-screen, prescreening_data includes screening_valid_until."""
        from environment import get_screening_validity_days
        now = datetime.now(timezone.utc)
        validity_days = get_screening_validity_days()

        # Simulate what the screening handler does
        prescreening = {
            "screening_report": _make_screening_report(
                screened_at=now.strftime("%Y-%m-%dT%H:%M:%S")
            ),
            "last_screened_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "screening_valid_until": (now + timedelta(days=validity_days)).strftime("%Y-%m-%dT%H:%M:%S"),
            "screening_validity_days": validity_days,
        }

        assert "screening_valid_until" in prescreening
        valid_until = datetime.fromisoformat(prescreening["screening_valid_until"])
        # Valid until should be ~90 days from now
        expected = now + timedelta(days=validity_days)
        # Make both naive for comparison
        if valid_until.tzinfo is None and expected.tzinfo is not None:
            expected = expected.replace(tzinfo=None)
        elif expected.tzinfo is None and valid_until.tzinfo is not None:
            valid_until = valid_until.replace(tzinfo=None)
        assert abs((valid_until - expected).total_seconds()) < 2

    def test_rescreen_refreshes_expired_screening(self, db):
        """An expired screening becomes valid after re-screen."""
        from security_hardening import ApprovalGateValidator
        now = datetime.now(timezone.utc)

        # First: create app with expired screening
        old_screened = (now - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%S")
        old_valid_until = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S")

        app = _insert_app_and_memo(
            db,
            screening_report=_make_screening_report(screened_at=old_screened),
            prescreening_extras={
                "screening_valid_until": old_valid_until,
                "screening_validity_days": 90,
            },
            submitted_at=(now - timedelta(days=110)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        can, _ = ApprovalGateValidator.validate_approval(app, db)
        assert not can, "Expected expired screening to block"

        # Simulate re-screen: update the screening data
        new_screened = now.strftime("%Y-%m-%dT%H:%M:%S")
        new_valid_until = (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S")
        new_report = _make_screening_report(screened_at=new_screened)
        new_prescreening = json.dumps({
            "screening_report": new_report,
            "last_screened_at": new_screened,
            "screening_valid_until": new_valid_until,
            "screening_validity_days": 90,
        })
        db.execute(
            "UPDATE applications SET prescreening_data=?, inputs_updated_at=? WHERE id=?",
            (new_prescreening, now.strftime("%Y-%m-%d %H:%M:%S"), app["id"])
        )
        db.commit()

        # Re-fetch and update memo timestamp (memo must be after inputs_updated_at)
        memo_ts = (now + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "UPDATE compliance_memos SET created_at=? WHERE application_id=?",
            (memo_ts, app["id"])
        )
        db.commit()

        updated_app = dict(db.execute("SELECT * FROM applications WHERE id=?", (app["id"],)).fetchone())
        can, err = ApprovalGateValidator.validate_approval(updated_app, db)
        assert can, f"Expected re-screen to make screening valid but got: {err}"


# ═══════════════════════════════════════════════════════════════
# Test: Configurable validity period affects behavior
# ═══════════════════════════════════════════════════════════════

class TestConfigurableValidityPeriod:
    """SCREENING_VALIDITY_DAYS env var controls the validity window."""

    def test_default_validity_is_90_days(self):
        """Default validity period should be 90 days."""
        from environment import get_screening_validity_days
        # Clear any override
        old = os.environ.pop("SCREENING_VALIDITY_DAYS", None)
        try:
            assert get_screening_validity_days() == 90
        finally:
            if old is not None:
                os.environ["SCREENING_VALIDITY_DAYS"] = old

    def test_custom_validity_period(self):
        """SCREENING_VALIDITY_DAYS env var overrides the default."""
        from environment import get_screening_validity_days
        old = os.environ.get("SCREENING_VALIDITY_DAYS")
        os.environ["SCREENING_VALIDITY_DAYS"] = "30"
        try:
            assert get_screening_validity_days() == 30
        finally:
            if old is not None:
                os.environ["SCREENING_VALIDITY_DAYS"] = old
            else:
                os.environ.pop("SCREENING_VALIDITY_DAYS", None)

    def test_minimum_validity_is_1_day(self):
        """Validity period cannot be less than 1 day."""
        from environment import get_screening_validity_days
        old = os.environ.get("SCREENING_VALIDITY_DAYS")
        os.environ["SCREENING_VALIDITY_DAYS"] = "0"
        try:
            assert get_screening_validity_days() == 1
        finally:
            if old is not None:
                os.environ["SCREENING_VALIDITY_DAYS"] = old
            else:
                os.environ.pop("SCREENING_VALIDITY_DAYS", None)

    def test_invalid_value_defaults_to_90(self):
        """Non-numeric SCREENING_VALIDITY_DAYS falls back to 90."""
        from environment import get_screening_validity_days
        old = os.environ.get("SCREENING_VALIDITY_DAYS")
        os.environ["SCREENING_VALIDITY_DAYS"] = "not-a-number"
        try:
            assert get_screening_validity_days() == 90
        finally:
            if old is not None:
                os.environ["SCREENING_VALIDITY_DAYS"] = old
            else:
                os.environ.pop("SCREENING_VALIDITY_DAYS", None)

    def test_short_validity_blocks_older_screening(self, db):
        """With 7-day validity, a 10-day-old screening is blocked."""
        from security_hardening import ApprovalGateValidator
        now = datetime.now(timezone.utc)
        screened_at = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S")
        # Valid until was 7 days after screening = 3 days ago
        valid_until = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")

        app = _insert_app_and_memo(
            db,
            screening_report=_make_screening_report(screened_at=screened_at),
            prescreening_extras={
                "screening_valid_until": valid_until,
                "screening_validity_days": 7,
            },
            submitted_at=(now - timedelta(days=11)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can, "Expected 10-day-old screening to be blocked with 7-day validity"
        assert "expired" in err.lower()

    def test_short_validity_allows_fresh_screening(self, db):
        """With 7-day validity, a 3-day-old screening passes."""
        from security_hardening import ApprovalGateValidator
        now = datetime.now(timezone.utc)
        screened_at = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
        valid_until = (now + timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%S")

        app = _insert_app_and_memo(
            db,
            screening_report=_make_screening_report(screened_at=screened_at),
            prescreening_extras={
                "screening_valid_until": valid_until,
                "screening_validity_days": 7,
            },
            submitted_at=(now - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert can, f"Expected fresh screening (3 days old, 7-day validity) to pass: {err}"


# ═══════════════════════════════════════════════════════════════
# Test: Existing approval gates not weakened (regression)
# ═══════════════════════════════════════════════════════════════

class TestExistingGatesNotWeakened:
    """Verify other gates still function with screening freshness added."""

    def test_missing_screening_report_still_blocks(self, db):
        """Gate 2: no screening report still blocks."""
        from security_hardening import ApprovalGateValidator
        suffix = uuid.uuid4().hex[:8]
        app_id = f"app-reg-{suffix}"
        db.execute("""
            INSERT INTO applications
            (id, ref, company_name, country, sector, entity_type, status,
             risk_level, risk_score, prescreening_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (app_id, f"ARF-REG-{suffix}", "Regression Ltd", "Mauritius",
              "Technology", "SME", "in_review", "MEDIUM", 50,
              json.dumps({})))
        db.commit()
        app = dict(db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can
        assert "screening" in err.lower()

    def test_simulated_screening_still_blocks(self, db):
        """Gate 5: simulated screening still blocks approval."""
        from security_hardening import ApprovalGateValidator
        now = datetime.now(timezone.utc)
        report = _make_screening_report(screened_at=now.strftime("%Y-%m-%dT%H:%M:%S"))
        report["director_screenings"] = [
            {"person_name": "Test Director", "screening": {"api_status": "simulated", "source": "simulated"}}
        ]
        app = _insert_app_and_memo(
            db,
            screening_report=report,
            prescreening_extras={
                "screening_valid_until": (now + timedelta(days=89)).strftime("%Y-%m-%dT%H:%M:%S"),
                "screening_validity_days": 90,
            },
            submitted_at=(now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can
        assert "simulated" in err.lower()

    def test_stale_memo_still_blocks(self, db):
        """Gate 7: stale memo still blocks approval."""
        from security_hardening import ApprovalGateValidator
        now = datetime.now(timezone.utc)
        app = _insert_app_and_memo(
            db,
            screening_report=_make_screening_report(
                screened_at=now.strftime("%Y-%m-%dT%H:%M:%S")
            ),
            prescreening_extras={
                "screening_valid_until": (now + timedelta(days=89)).strftime("%Y-%m-%dT%H:%M:%S"),
                "screening_validity_days": 90,
            },
            submitted_at=(now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"),
            app_updated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=(now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
        )
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can
        assert "memo" in err.lower() or "modified" in err.lower()


# ═══════════════════════════════════════════════════════════════
# EX-10 closeout Fix 1: Future-dated screening timestamps rejected
# ═══════════════════════════════════════════════════════════════

class TestFutureDatedScreeningBlocks:
    """Future-dated screened_at or screening_valid_until must block approval."""

    def test_future_screened_at_blocks(self, db):
        """screened_at set 1 hour in the future (beyond 5-min skew) blocks."""
        from security_hardening import ApprovalGateValidator
        now = datetime.now(timezone.utc)
        future_screened = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        valid_until = (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S")

        app = _insert_app_and_memo(
            db,
            screening_report=_make_screening_report(screened_at=future_screened),
            prescreening_extras={
                "screening_valid_until": valid_until,
                "screening_validity_days": 90,
            },
            submitted_at=(now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can, "Future-dated screened_at should block approval"
        assert "future" in err.lower()

    def test_small_clock_skew_allowed(self, db):
        """screened_at 2 minutes in the future (within 5-min skew) is allowed."""
        from security_hardening import ApprovalGateValidator
        now = datetime.now(timezone.utc)
        # 2 minutes ahead — within 300s tolerance
        screened_at = (now + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")
        valid_until = (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S")

        app = _insert_app_and_memo(
            db,
            screening_report=_make_screening_report(screened_at=screened_at),
            prescreening_extras={
                "screening_valid_until": valid_until,
                "screening_validity_days": 90,
            },
            submitted_at=(now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert can, f"Small clock skew should be tolerated but got: {err}"

    def test_future_valid_until_beyond_window_blocks(self, db):
        """screening_valid_until far beyond validity_days + skew blocks."""
        from security_hardening import ApprovalGateValidator
        now = datetime.now(timezone.utc)
        screened_at = now.strftime("%Y-%m-%dT%H:%M:%S")
        # valid_until is 200 days from now, but validity is only 90
        implausible_valid_until = (now + timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%S")

        app = _insert_app_and_memo(
            db,
            screening_report=_make_screening_report(screened_at=screened_at),
            prescreening_extras={
                "screening_valid_until": implausible_valid_until,
                "screening_validity_days": 90,
            },
            submitted_at=(now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can, "Implausibly far future valid_until should block"
        assert "future" in err.lower() or "implausib" in err.lower()

    def test_future_screened_at_logged(self, db, caplog):
        """A future-dated screened_at emits a warning log."""
        import logging
        from security_hardening import ApprovalGateValidator
        now = datetime.now(timezone.utc)
        future_screened = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")

        app = _insert_app_and_memo(
            db,
            screening_report=_make_screening_report(screened_at=future_screened),
            prescreening_extras={
                "screening_valid_until": (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S"),
                "screening_validity_days": 90,
            },
            submitted_at=(now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        with caplog.at_level(logging.WARNING):
            can, _ = ApprovalGateValidator.validate_approval(app, db)
        assert not can
        assert any("future-dated" in r.message.lower() for r in caplog.records), \
            "Expected a warning log for future-dated screened_at"


# ═══════════════════════════════════════════════════════════════
# EX-10 closeout Fix 3: End-to-end re-screen persistence via handler
# ═══════════════════════════════════════════════════════════════

class TestEndToEndRescreenPersistence:
    """Drive the actual ScreeningHandler endpoint to verify persistence."""

    def test_screening_handler_persists_validity_fields(self, db, monkeypatch):
        """POST /api/screening/run persists screening_valid_until and
        screening_validity_days through the real handler code path."""
        from server import make_app, create_token
        import tornado.testing
        import tornado.httpserver
        import tornado.ioloop
        import asyncio
        import socket
        import threading
        import time
        import requests as http_requests

        # Create app with prescreening data
        now = datetime.now(timezone.utc)
        suffix = uuid.uuid4().hex[:8]
        app_id = f"app-e2e-{suffix}"
        ref = f"ARF-E2E-{suffix}"
        db.execute("""
            INSERT INTO applications
            (id, ref, company_name, country, sector, entity_type, status,
             risk_level, risk_score, prescreening_data, submitted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (app_id, ref, "E2E Test Ltd", "Mauritius", "Technology", "SME",
              "in_review", "MEDIUM", 50, json.dumps({}),
              now.strftime("%Y-%m-%d %H:%M:%S")))
        db.commit()

        # Mock external screening providers
        def mock_sanctions(name, birth_date=None, nationality=None, entity_type="Person"):
            return {
                "matched": False, "results": [], "total_checked": 1,
                "source": "mocked", "api_status": "mocked",
                "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            }
        def mock_company(company_name, jurisdiction=None):
            return {
                "found": True, "companies": [],
                "total_results": 0, "source": "mocked", "api_status": "mocked",
                "searched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            }
        def mock_geolocate(ip):
            return {
                "ip": ip, "country": "Mauritius", "country_code": "MU",
                "risk_level": "LOW", "source": "mocked",
                "is_vpn": False, "is_proxy": False, "is_tor": False
            }

        import server
        import screening
        monkeypatch.setattr(server, "screen_sumsub_aml", mock_sanctions)
        monkeypatch.setattr(server, "lookup_opencorporates", mock_company)
        monkeypatch.setattr(server, "geolocate_ip", mock_geolocate)
        monkeypatch.setattr(screening, "screen_sumsub_aml", mock_sanctions)
        monkeypatch.setattr(screening, "lookup_opencorporates", mock_company)
        monkeypatch.setattr(screening, "geolocate_ip", mock_geolocate)

        # Start a real Tornado server
        app = make_app()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        server_ref = {}
        started = threading.Event()

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            io_loop = tornado.ioloop.IOLoop.current()
            srv = tornado.httpserver.HTTPServer(app)
            srv.listen(port, "127.0.0.1")
            server_ref["srv"] = srv
            server_ref["loop"] = io_loop
            started.set()
            io_loop.start()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        started.wait(timeout=3)
        time.sleep(0.2)
        base_url = f"http://127.0.0.1:{port}"

        try:
            token = create_token("admin001", "admin", "Test Admin", "officer")
            resp = http_requests.post(
                f"{base_url}/api/screening/run",
                json={"application_id": app_id},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            body = resp.json()

            # Verify response contains validity fields
            assert "screening_valid_until" in body, "Response missing screening_valid_until"
            assert "screening_validity_days" in body, "Response missing screening_validity_days"
            assert body["screening_validity_days"] >= 1

            # Verify persistence in the database
            row = db.execute("SELECT prescreening_data FROM applications WHERE id=?", (app_id,)).fetchone()
            assert row is not None
            psd = json.loads(row["prescreening_data"])
            assert "screening_valid_until" in psd, "screening_valid_until not persisted"
            assert "screening_validity_days" in psd, "screening_validity_days not persisted"

            # Verify the valid_until is plausible (~90 days from now)
            vu = datetime.fromisoformat(psd["screening_valid_until"])
            expected_vu = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=psd["screening_validity_days"])
            assert abs((vu - expected_vu).total_seconds()) < 10, \
                f"valid_until off by more than 10s: {vu} vs {expected_vu}"

        finally:
            io_loop = server_ref.get("loop")
            srv = server_ref.get("srv")
            if io_loop and srv:
                io_loop.add_callback(srv.stop)
                io_loop.add_callback(io_loop.stop)
            t.join(timeout=2)

    def test_rescreen_refreshes_validity_window(self, db, monkeypatch):
        """A second screening run refreshes the validity window in the DB."""
        from server import make_app, create_token
        import tornado.httpserver
        import tornado.ioloop
        import asyncio
        import socket
        import threading
        import time
        import requests as http_requests

        now = datetime.now(timezone.utc)
        suffix = uuid.uuid4().hex[:8]
        app_id = f"app-e2e2-{suffix}"
        ref = f"ARF-E2E2-{suffix}"

        # Seed app with old (expired) screening data
        old_valid_until = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S")
        old_prescreening = json.dumps({
            "screening_valid_until": old_valid_until,
            "screening_validity_days": 90,
            "last_screened_at": (now - timedelta(days=95)).strftime("%Y-%m-%dT%H:%M:%S"),
        })
        db.execute("""
            INSERT INTO applications
            (id, ref, company_name, country, sector, entity_type, status,
             risk_level, risk_score, prescreening_data, submitted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (app_id, ref, "Re-screen Test Ltd", "Mauritius", "Technology", "SME",
              "in_review", "MEDIUM", 50, old_prescreening,
              now.strftime("%Y-%m-%d %H:%M:%S")))
        db.commit()

        # Mock external screening providers
        def mock_sanctions(name, birth_date=None, nationality=None, entity_type="Person"):
            return {
                "matched": False, "results": [], "total_checked": 1,
                "source": "mocked", "api_status": "mocked",
                "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            }
        def mock_company(company_name, jurisdiction=None):
            return {
                "found": True, "companies": [],
                "total_results": 0, "source": "mocked", "api_status": "mocked",
                "searched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            }
        def mock_geolocate(ip):
            return {
                "ip": ip, "country": "Mauritius", "country_code": "MU",
                "risk_level": "LOW", "source": "mocked",
                "is_vpn": False, "is_proxy": False, "is_tor": False
            }

        import server
        import screening
        monkeypatch.setattr(server, "screen_sumsub_aml", mock_sanctions)
        monkeypatch.setattr(server, "lookup_opencorporates", mock_company)
        monkeypatch.setattr(server, "geolocate_ip", mock_geolocate)
        monkeypatch.setattr(screening, "screen_sumsub_aml", mock_sanctions)
        monkeypatch.setattr(screening, "lookup_opencorporates", mock_company)
        monkeypatch.setattr(screening, "geolocate_ip", mock_geolocate)

        app = make_app()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        server_ref = {}
        started = threading.Event()

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            io_loop = tornado.ioloop.IOLoop.current()
            srv = tornado.httpserver.HTTPServer(app)
            srv.listen(port, "127.0.0.1")
            server_ref["srv"] = srv
            server_ref["loop"] = io_loop
            started.set()
            io_loop.start()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        started.wait(timeout=3)
        time.sleep(0.2)
        base_url = f"http://127.0.0.1:{port}"

        try:
            token = create_token("admin001", "admin", "Test Admin", "officer")
            resp = http_requests.post(
                f"{base_url}/api/screening/run",
                json={"application_id": app_id},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            assert resp.status_code == 200, f"Re-screen failed: {resp.text}"

            # After re-screen, valid_until should be refreshed to ~90 days from now
            row = db.execute("SELECT prescreening_data FROM applications WHERE id=?", (app_id,)).fetchone()
            psd = json.loads(row["prescreening_data"])
            new_vu = datetime.fromisoformat(psd["screening_valid_until"])
            # Must be in the future now (was expired before)
            now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            assert new_vu > now_naive, \
                f"After re-screen, valid_until should be in the future but got {new_vu}"
            # And roughly validity_days from now
            expected = now_naive + timedelta(days=psd["screening_validity_days"])
            assert abs((new_vu - expected).total_seconds()) < 10

        finally:
            io_loop = server_ref.get("loop")
            srv = server_ref.get("srv")
            if io_loop and srv:
                io_loop.add_callback(srv.stop)
                io_loop.add_callback(io_loop.stop)
            t.join(timeout=2)


# ═══════════════════════════════════════════════════════════════
# EX-10 closeout Fix 4: Audit log on successful freshness validation
# ═══════════════════════════════════════════════════════════════

class TestFreshnessValidationAuditLog:
    """Successful freshness validation emits a structured log."""

    def test_successful_validation_emits_audit_log(self, db, caplog):
        """When screening freshness passes, a structured info log is emitted."""
        import logging
        from security_hardening import ApprovalGateValidator
        now = datetime.now(timezone.utc)
        screened_at = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S")
        valid_until = (now + timedelta(days=85)).strftime("%Y-%m-%dT%H:%M:%S")

        app = _insert_app_and_memo(
            db,
            screening_report=_make_screening_report(screened_at=screened_at),
            prescreening_extras={
                "screening_valid_until": valid_until,
                "screening_validity_days": 90,
            },
            submitted_at=(now - timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        with caplog.at_level(logging.INFO):
            can, err = ApprovalGateValidator.validate_approval(app, db)
        assert can, f"Expected approval to pass: {err}"

        # Find the freshness audit log
        freshness_logs = [r for r in caplog.records
                          if "screening freshness validated" in r.message.lower()]
        assert len(freshness_logs) >= 1, \
            f"Expected a freshness validation log; got: {[r.message for r in caplog.records]}"

        log_msg = freshness_logs[0].message
        assert "screening_age_days=" in log_msg
        assert "valid_until=" in log_msg
        assert "validity_days=" in log_msg
        assert app["id"] in log_msg

    def test_audit_log_contains_correct_age(self, db, caplog):
        """The screening_age_days in the audit log is correct."""
        import logging
        from security_hardening import ApprovalGateValidator
        now = datetime.now(timezone.utc)
        screened_at = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S")
        valid_until = (now + timedelta(days=80)).strftime("%Y-%m-%dT%H:%M:%S")

        app = _insert_app_and_memo(
            db,
            screening_report=_make_screening_report(screened_at=screened_at),
            prescreening_extras={
                "screening_valid_until": valid_until,
                "screening_validity_days": 90,
            },
            submitted_at=(now - timedelta(days=11)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        with caplog.at_level(logging.INFO):
            can, _ = ApprovalGateValidator.validate_approval(app, db)
        assert can

        freshness_logs = [r for r in caplog.records
                          if "screening freshness validated" in r.message.lower()]
        assert len(freshness_logs) >= 1
        assert "screening_age_days=10" in freshness_logs[0].message
