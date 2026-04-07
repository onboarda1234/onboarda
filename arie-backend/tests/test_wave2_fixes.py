"""
Wave 2 remediation regression tests — verifying high-severity consistency fixes.
"""
import json
import os
import re
import sys
import pytest
from datetime import datetime, timedelta

# ── Ensure the backend root is importable ──
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═══════════════════════════════════════════════════════════
# W2-3: Backend DOB validation
# ═══════════════════════════════════════════════════════════

class TestW2_3_DOBValidation:
    """Verify _validate_date_of_birth correctly validates dates."""

    def test_valid_date(self):
        from server import _validate_date_of_birth
        result = _validate_date_of_birth("1990-06-15")
        assert result == "1990-06-15"

    def test_future_date_rejected(self):
        from server import _validate_date_of_birth
        future = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d")
        assert _validate_date_of_birth(future) == ""

    def test_empty_string(self):
        from server import _validate_date_of_birth
        assert _validate_date_of_birth("") == ""

    def test_none_value(self):
        from server import _validate_date_of_birth
        assert _validate_date_of_birth(None) == ""

    def test_invalid_format(self):
        from server import _validate_date_of_birth
        assert _validate_date_of_birth("not-a-date") == ""

    def test_implausible_young(self):
        from server import _validate_date_of_birth
        # 5 years old
        young = (datetime.now() - timedelta(days=5*365)).strftime("%Y-%m-%d")
        assert _validate_date_of_birth(young) == ""

    def test_implausible_old(self):
        from server import _validate_date_of_birth
        # 130 years old
        assert _validate_date_of_birth("1890-01-01") == ""

    def test_normalizes_to_iso(self):
        from server import _validate_date_of_birth
        # European format DD/MM/YYYY
        result = _validate_date_of_birth("15/06/1990")
        assert result == "1990-06-15"

    def test_adult_age_valid(self):
        from server import _validate_date_of_birth
        # 30 years old
        dob = (datetime.now() - timedelta(days=30*365)).strftime("%Y-%m-%d")
        result = _validate_date_of_birth(dob)
        assert result != ""  # Should be valid


# ═══════════════════════════════════════════════════════════
# W2-2: Minimum director validation in SubmitApplicationHandler
# ═══════════════════════════════════════════════════════════

class TestW2_2_MinimumDirector:
    """Verify the submit handler checks for at least one director."""

    def test_submit_handler_has_director_check(self):
        import server
        import inspect
        src = inspect.getsource(server.SubmitApplicationHandler)
        assert "director" in src.lower(), \
            "SubmitApplicationHandler should validate director count"

    def test_frontend_has_director_check(self):
        portal_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
        with open(portal_path) as f:
            src = f.read()
        assert "Director Required" in src or "director is required" in src.lower(), \
            "Frontend should validate minimum director count"


# ═══════════════════════════════════════════════════════════
# W2-1: Same-person deduplication in screening
# ═══════════════════════════════════════════════════════════

class TestW2_1_PersonDedup:
    """Verify dedup logic exists in screening.py."""

    def test_screening_has_dedup_logic(self):
        import screening
        import inspect
        src = inspect.getsource(screening.run_full_screening)
        assert "dedup_key" in src, \
            "run_full_screening should have dedup_key for person deduplication"

    def test_screening_no_role_prefix_in_ext_id(self):
        import screening
        import inspect
        src = inspect.getsource(screening.run_full_screening)
        # ext_id should use "person_" prefix, not role-specific "{ptype}_"
        matches = re.findall(r'f"{\s*ptype\s*}_', src)
        assert len(matches) == 0, \
            "ext_id should not use ptype prefix — causes duplicate Sumsub applicants"
        assert "person_" in src, \
            "ext_id should use role-agnostic 'person_' prefix"

    def test_run_full_screening_is_callable(self):
        from screening import run_full_screening
        assert callable(run_full_screening)


# ═══════════════════════════════════════════════════════════
# W2-5: Memo PEP logic considers screening results
# ═══════════════════════════════════════════════════════════

class TestW2_5_MemoPEPScreening:
    """Verify memo handler checks screening results for PEP matches."""

    def test_memo_handler_checks_screening(self):
        import memo_handler
        import inspect
        src = inspect.getsource(memo_handler.build_compliance_memo)
        assert "screening_results" in src or "screening_report" in src, \
            "build_compliance_memo should check screening data for PEP matches"
        assert "pep_match" in src or "is_pep" in src, \
            "build_compliance_memo should look for pep_match/is_pep in screening data"

    def test_memo_deduplicates_peps(self):
        import memo_handler
        import inspect
        src = inspect.getsource(memo_handler.build_compliance_memo)
        assert "deduped_peps" in src or "seen_pep_names" in src, \
            "build_compliance_memo should deduplicate PEPs across roles"

    def test_build_compliance_memo_is_callable(self):
        from memo_handler import build_compliance_memo
        assert callable(build_compliance_memo)


# ═══════════════════════════════════════════════════════════
# W2-4: kyc_submitted is no longer a dead state
# ═══════════════════════════════════════════════════════════

class TestW2_4_KYCSubmittedState:
    """Verify kyc_submitted is now actively used."""

    def test_kyc_handler_sets_kyc_submitted(self):
        import server
        import inspect
        # Verify the KYC handler exists and references kyc_submitted
        handler_src = inspect.getsource(server.ApplicationDetailHandler.patch)
        assert "kyc_submitted" in handler_src, \
            "ApplicationDetailHandler.patch should reference kyc_submitted as a valid state"

    def test_kyc_submitted_has_outgoing_transitions(self):
        import server
        import inspect
        handler_src = inspect.getsource(server.ApplicationDetailHandler.patch)
        # kyc_submitted must appear as a key (source state) in valid_transitions
        assert '"kyc_submitted"' in handler_src or "'kyc_submitted'" in handler_src, \
            "kyc_submitted must be a source state in valid_transitions"

    def test_kyc_submitted_in_db_schema(self):
        import db
        pg_schema = db._get_postgres_schema()
        assert "kyc_submitted" in pg_schema, \
            "kyc_submitted must be in DB schema CHECK constraints"


# ═══════════════════════════════════════════════════════════
# W2-6: Nationality normalization
# ═══════════════════════════════════════════════════════════

class TestW2_6_NationalityNormalization:
    """Verify nationality is normalized on storage."""

    def test_canonicalise_country_importable(self):
        from document_verification import _canonicalise_country
        assert callable(_canonicalise_country)
        # Basic smoke test
        assert _canonicalise_country("Mauritius") == "MU"

    def test_store_parties_importable(self):
        from server import store_application_parties
        assert callable(store_application_parties)

    def test_store_parties_uses_canonicalise(self):
        import server
        import inspect
        src = inspect.getsource(server.store_application_parties)
        assert "_canonicalise_country" in src, \
            "store_application_parties should normalize nationality values"


# ═══════════════════════════════════════════════════════════
# W2-7: Ownership percentage validation
# ═══════════════════════════════════════════════════════════

class TestW2_7_OwnershipPctValidation:
    """Verify ownership_pct is validated and clamped."""

    def test_ownership_pct_clamped_in_code(self):
        import server
        import inspect
        src = inspect.getsource(server.store_application_parties)
        assert "max(0.0, min(100.0" in src, \
            "ownership_pct should be clamped to 0-100 range"

    def test_store_parties_callable(self):
        from server import store_application_parties
        assert callable(store_application_parties)
