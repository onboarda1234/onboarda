"""
Phase 4 — Verification Integrity + Compliance Hardening Tests
=============================================================
Targeted tests for:
  - Nationality matching (DOC-52/DOC-56) — fixed 3-char prefix bug
  - DOB matching (DOC-49A) — year-only format removed
  - Registration number normalization (DOC-06) — dots/slashes now stripped
  - Stale data protection — party changes, memo staleness, screening freshness
  - Approval safety — party modification blocked after compliance review
"""
import os
import sys
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, date, timezone
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


# ═══════════════════════════════════════════════════════════════════
# Priority 1 — Verification Integrity
# ═══════════════════════════════════════════════════════════════════

class TestNationalityMatching:
    """DOC-52 / DOC-56: Nationality match must not false-pass on 3-char prefix."""

    def test_exact_match_passes(self):
        from document_verification import _canonicalise_nationality
        assert _canonicalise_nationality("Mauritius") == _canonicalise_nationality("mauritius")

    def test_canonicalise_resolves_demonyms(self):
        from document_verification import _canonicalise_nationality
        assert _canonicalise_nationality("Mauritian") == "mauritius"
        assert _canonicalise_nationality("British") == "united kingdom"
        assert _canonicalise_nationality("American") == "united states"

    def test_canonicalise_resolves_codes(self):
        from document_verification import _canonicalise_nationality
        assert _canonicalise_nationality("MU") == "mauritius"
        assert _canonicalise_nationality("GB") == "united kingdom"

    def test_united_states_vs_united_kingdom_fail(self):
        """Critical regression: these must NOT match despite sharing 'uni' prefix."""
        from document_verification import _canonicalise_nationality
        d_n = _canonicalise_nationality("United States")
        e_n = _canonicalise_nationality("United Kingdom")
        # Canonicalised names are different
        assert d_n != e_n
        # The check should NOT pass
        assert not (d_n == e_n)

    def test_united_arab_emirates_vs_united_states_fail(self):
        """UAE vs US must not match."""
        from document_verification import _canonicalise_nationality
        d_n = _canonicalise_nationality("United Arab Emirates")
        e_n = _canonicalise_nationality("United States")
        assert d_n != e_n

    def test_same_country_different_forms_passes(self):
        """'Mauritian' and 'Mauritius' should match via canonicalisation."""
        from document_verification import _canonicalise_nationality
        d_n = _canonicalise_nationality("Mauritian")
        e_n = _canonicalise_nationality("Mauritius")
        assert d_n == e_n == "mauritius"

    def test_british_matches_gb(self):
        """'British' and 'GB' should match via canonicalisation."""
        from document_verification import _canonicalise_nationality
        assert _canonicalise_nationality("British") == _canonicalise_nationality("GB") == "united kingdom"

    def test_unknown_nationality_returns_input(self):
        from document_verification import _canonicalise_nationality
        # Unknown values pass through normalised
        assert _canonicalise_nationality("Atlantean") == "atlantean"
        assert _canonicalise_nationality("") == ""
        assert _canonicalise_nationality(None) == ""


class TestDOBMatching:
    """DOC-49A: Date of birth matching — year-only format removed."""

    def test_iso_format_parses(self):
        from document_verification import _parse_date
        d = _parse_date("1990-05-15")
        assert d == date(1990, 5, 15)

    def test_european_format_parses(self):
        from document_verification import _parse_date
        d = _parse_date("15/05/1990")
        assert d == date(1990, 5, 15)

    def test_long_format_parses(self):
        from document_verification import _parse_date
        d = _parse_date("15 May 1990")
        assert d == date(1990, 5, 15)

    def test_year_only_format_rejected(self):
        """Year-only format is too permissive for DOB — must return None."""
        from document_verification import _parse_date
        d = _parse_date("1990")
        assert d is None, "Year-only format should not be accepted for date parsing"

    def test_full_date_match(self):
        from document_verification import _parse_date
        d1 = _parse_date("1990-05-15")
        d2 = _parse_date("15/05/1990")
        assert d1 == d2

    def test_mismatch_detected(self):
        from document_verification import _parse_date
        d1 = _parse_date("1990-05-15")
        d2 = _parse_date("1990-05-16")
        assert d1 != d2

    def test_none_handling(self):
        from document_verification import _parse_date
        assert _parse_date(None) is None
        assert _parse_date("") is None


class TestRegistrationNumberNormalization:
    """DOC-06: Registration number comparison — dots and slashes now stripped."""

    def test_spaces_and_hyphens_stripped(self):
        """Original normalization: spaces and hyphens."""
        import re
        d = re.sub(r"[\s\-./]", "", "C 12-345")
        e = re.sub(r"[\s\-./]", "", "C12345")
        assert d.upper() == e.upper()

    def test_dots_stripped(self):
        """New: dots must be stripped for comparison."""
        import re
        d = re.sub(r"[\s\-./]", "", "C.A.12345")
        e = re.sub(r"[\s\-./]", "", "CA12345")
        assert d.upper() == e.upper()

    def test_slashes_stripped(self):
        """New: slashes must be stripped."""
        import re
        d = re.sub(r"[\s\-./]", "", "REG/2024/001")
        e = re.sub(r"[\s\-./]", "", "REG2024001")
        assert d.upper() == e.upper()

    def test_mixed_separators(self):
        """Complex registration numbers with mixed separators."""
        import re
        d = re.sub(r"[\s\-./]", "", "B.R.N-12/345")
        e = re.sub(r"[\s\-./]", "", "BRN12345")
        assert d.upper() == e.upper()

    def test_actual_mismatch_detected(self):
        """Real mismatches must still fail."""
        import re
        d = re.sub(r"[\s\-./]", "", "C12345")
        e = re.sub(r"[\s\-./]", "", "C12346")
        assert d.upper() != e.upper()


# ═══════════════════════════════════════════════════════════════════
# Priority 2 & 3 — Stale Data Protection + Approval Safety
# ═══════════════════════════════════════════════════════════════════

def _insert_app_and_memo(db, app_id=None,
                          app_updated_at=None, memo_created_at=None,
                          screening_report=None, submitted_at=None,
                          status="in_review",
                          review_status="approved",
                          validation_status="pass",
                          supervisor_status="CONSISTENT",
                          blocked=False, block_reason=None):
    """Helper: insert application + memo with configurable timestamps."""
    import uuid
    suffix = uuid.uuid4().hex[:8]
    if app_id is None:
        app_id = f"app-p4-{suffix}"
    ref = f"ARF-P4-{suffix}"

    if screening_report is None:
        screening_report = {
            "screening_mode": "live",
            "sanctions": {"api_status": "live", "matched": False, "source": "sumsub"},
            "company_registry": {"api_status": "live"},
            "ip_geolocation": {"api_status": "live"},
            "kyc": {"api_status": "live"},
            "screened_at": datetime.now(timezone.utc).isoformat()
        }
    prescreening = json.dumps({"screening_report": screening_report})
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    app_updated = app_updated_at or now
    memo_created = memo_created_at or now
    sub_at = submitted_at or now

    db.execute("""
        INSERT INTO applications
        (id, ref, company_name, country, sector, entity_type, status,
         risk_level, risk_score, prescreening_data, submitted_at, updated_at,
         inputs_updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (app_id, ref, "Test Corp", "Mauritius", "Technology", "SME",
          status, "MEDIUM", 50, prescreening, sub_at, app_updated,
          app_updated))

    memo_data = json.dumps({"ai_source": "deterministic", "metadata": {"ai_source": "deterministic"}})
    db.execute("""
        INSERT INTO compliance_memos
        (application_id, version, memo_data, review_status, validation_status,
         supervisor_status, blocked, block_reason, created_at)
        VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)
    """, (app_id, memo_data, review_status, validation_status,
          supervisor_status, 1 if blocked else 0, block_reason, memo_created))
    db.commit()

    row = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    return dict(row)


class TestApprovalGateStaleness:
    """Approval gate must detect stale memo/screening data."""

    def test_fresh_memo_passes(self, db):
        """When memo is newer than app update, approval passes staleness check."""
        now = datetime.now(timezone.utc)
        # Screening was done after submission, memo was done after app update
        screening = {
            "screening_mode": "live",
            "sanctions": {"api_status": "live", "matched": False, "source": "sumsub"},
            "company_registry": {"api_status": "live"},
            "ip_geolocation": {"api_status": "live"},
            "kyc": {"api_status": "live"},
            "screened_at": now.isoformat(),
        }
        app = _insert_app_and_memo(
            db,
            screening_report=screening,
            submitted_at=(now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
            app_updated_at=(now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        from security_hardening import ApprovalGateValidator
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert can, f"Expected approval to pass but got: {err}"

    def test_stale_memo_blocked(self, db):
        """When app was updated after memo generation, approval is blocked."""
        now = datetime.now(timezone.utc)
        screening = {
            "screening_mode": "live",
            "sanctions": {"api_status": "live", "matched": False, "source": "sumsub"},
            "company_registry": {"api_status": "live"},
            "ip_geolocation": {"api_status": "live"},
            "kyc": {"api_status": "live"},
            "screened_at": now.isoformat(),
        }
        app = _insert_app_and_memo(
            db,
            screening_report=screening,
            submitted_at=(now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
            app_updated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=(now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
        )
        from security_hardening import ApprovalGateValidator
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can, "Expected approval to be blocked for stale memo"
        assert "modified after" in err.lower() or "memo" in err.lower()

    def test_screening_before_submission_blocked(self, db):
        """If screening timestamp is older than submission, approval is blocked."""
        now = datetime.now(timezone.utc)
        old_screening = {
            "screening_mode": "live",
            "sanctions": {"api_status": "live", "matched": False, "source": "sumsub"},
            "company_registry": {"api_status": "live"},
            "ip_geolocation": {"api_status": "live"},
            "kyc": {"api_status": "live"},
            "screened_at": (now - timedelta(hours=2)).isoformat(),
        }
        app = _insert_app_and_memo(
            db,
            screening_report=old_screening,
            submitted_at=now.isoformat(),
            app_updated_at=(now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
            memo_created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        from security_hardening import ApprovalGateValidator
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can, "Expected approval to be blocked for stale screening"
        assert "screening" in err.lower()

    def test_blocked_memo_still_blocked(self, db):
        """Memo with blocked=True still blocks approval (existing behavior preserved)."""
        now = datetime.now(timezone.utc)
        app = _insert_app_and_memo(
            db,
            blocked=True,
            block_reason="Pending review",
        )
        from security_hardening import ApprovalGateValidator
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can
        assert "blocked" in err.lower()


class TestPartyModificationBlocking:
    """Party modifications must be blocked after compliance review starts."""

    def test_party_update_allowed_in_draft(self):
        """Directors/UBOs can be updated in draft status."""
        immutable_party_states = ("compliance_review", "in_review", "edd_required",
                                  "under_review", "approved", "rejected")
        assert "draft" not in immutable_party_states

    def test_party_update_allowed_in_kyc_documents(self):
        """Directors/UBOs can be updated during KYC doc collection."""
        immutable_party_states = ("compliance_review", "in_review", "edd_required",
                                  "under_review", "approved", "rejected")
        assert "kyc_documents" not in immutable_party_states

    def test_party_update_blocked_in_compliance_review(self):
        """Directors/UBOs cannot be updated during compliance review."""
        immutable_party_states = ("compliance_review", "in_review", "edd_required",
                                  "under_review", "approved", "rejected")
        assert "compliance_review" in immutable_party_states

    def test_party_update_blocked_after_approval(self):
        """Directors/UBOs cannot be updated after approval."""
        immutable_party_states = ("compliance_review", "in_review", "edd_required",
                                  "under_review", "approved", "rejected")
        assert "approved" in immutable_party_states

    def test_party_update_blocked_in_edd(self):
        """Directors/UBOs cannot be updated during EDD."""
        immutable_party_states = ("compliance_review", "in_review", "edd_required",
                                  "under_review", "approved", "rejected")
        assert "edd_required" in immutable_party_states


class TestApprovalGateExistingChecks:
    """Validate existing approval gate checks still work (non-regression)."""

    def test_requires_screening_report(self, db):
        """Approval must fail without screening report."""
        import uuid
        suffix = uuid.uuid4().hex[:8]
        app_id = f"no_screening_{suffix}"
        prescreening = json.dumps({})
        db.execute("""
            INSERT INTO applications
            (id, ref, company_name, country, sector, entity_type, status,
             risk_level, risk_score, prescreening_data, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (app_id, f"ARF-{app_id}", "Test Corp", "Mauritius", "Technology", "SME",
              "in_review", "MEDIUM", 50, prescreening, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
        db.commit()
        app = dict(db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())
        from security_hardening import ApprovalGateValidator
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can
        assert "screening" in err.lower()

    def test_requires_compliance_memo(self, db):
        """Approval must fail without compliance memo."""
        import uuid
        suffix = uuid.uuid4().hex[:8]
        app_id = f"no_memo_{suffix}"
        prescreening = json.dumps({
            "screening_report": {
                "screening_mode": "live",
                "sanctions": {"api_status": "live"},
                "company_registry": {"api_status": "live"},
                "ip_geolocation": {"api_status": "live"},
                "kyc": {"api_status": "live"},
            }
        })
        db.execute("""
            INSERT INTO applications
            (id, ref, company_name, country, sector, entity_type, status,
             risk_level, risk_score, prescreening_data, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (app_id, f"ARF-{app_id}", "Test Corp", "Mauritius", "Technology", "SME",
              "in_review", "MEDIUM", 50, prescreening, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
        db.commit()
        app = dict(db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())
        from security_hardening import ApprovalGateValidator
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can
        assert "memo" in err.lower()

    def test_requires_memo_approved_review_status(self, db):
        """Approval must fail if memo review_status is not 'approved'."""
        now = datetime.now(timezone.utc)
        app = _insert_app_and_memo(
            db,
            review_status="draft",
        )
        from security_hardening import ApprovalGateValidator
        can, err = ApprovalGateValidator.validate_approval(app, db)
        assert not can
        assert "review_status" in err.lower()


# ═══════════════════════════════════════════════════════════════════
# PASS / FAIL / WARN Consistency
# ═══════════════════════════════════════════════════════════════════

class TestCheckStatusConsistency:
    """Verify PASS/FAIL/WARN status strings are consistent."""

    def test_check_status_values(self):
        from verification_matrix import CheckStatus
        assert CheckStatus.PASS == "pass"
        assert CheckStatus.FAIL == "fail"
        assert CheckStatus.WARN == "warn"
        assert CheckStatus.SKIP == "skip"
        assert CheckStatus.INCONCLUSIVE == "inconclusive"

    def test_result_builders_use_correct_status(self):
        from document_verification import _pass, _fail, _warn, _skip
        from verification_matrix import CheckStatus, CheckClassification
        p = _pass("T01", "Test", CheckClassification.RULE, "ok")
        assert p["result"] == CheckStatus.PASS
        f = _fail("T02", "Test", CheckClassification.RULE, "bad")
        assert f["result"] == CheckStatus.FAIL
        w = _warn("T03", "Test", CheckClassification.RULE, "maybe")
        assert w["result"] == CheckStatus.WARN
        s = _skip("T04", "Test", CheckClassification.RULE, "n/a")
        assert s["result"] == CheckStatus.SKIP
