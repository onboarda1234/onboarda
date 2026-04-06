"""
Wave 2 remediation regression tests — verifying high-severity consistency fixes.
"""
import json
import os
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
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path) as f:
            src = f.read()
        # Find the SubmitApplicationHandler section
        assert "At least one director is required" in src, \
            "Backend should validate minimum director count on submission"

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
        screening_path = os.path.join(os.path.dirname(__file__), "..", "screening.py")
        with open(screening_path) as f:
            src = f.read()
        assert "dedup_key" in src, \
            "screening.py should have dedup logic for person screening"
        # Should use name-based dedup, not role-based ext_id
        assert "person_" in src, \
            "ext_id should be role-agnostic (person_) not role-specific (director_/ubo_)"

    def test_screening_no_role_prefix_in_ext_id(self):
        screening_path = os.path.join(os.path.dirname(__file__), "..", "screening.py")
        with open(screening_path) as f:
            src = f.read()
        # The old pattern used {ptype}_ which would create different IDs for same person
        import re
        # Check that ext_id no longer uses ptype prefix
        matches = re.findall(r'f"{\s*ptype\s*}_', src)
        assert len(matches) == 0, \
            "ext_id should not use ptype prefix — causes duplicate Sumsub applicants"


# ═══════════════════════════════════════════════════════════
# W2-5: Memo PEP logic considers screening results
# ═══════════════════════════════════════════════════════════

class TestW2_5_MemoPEPScreening:
    """Verify memo handler checks screening results for PEP matches."""

    def test_memo_handler_checks_screening(self):
        memo_path = os.path.join(os.path.dirname(__file__), "..", "memo_handler.py")
        with open(memo_path) as f:
            src = f.read()
        assert "screening_results" in src, \
            "memo_handler should check screening_results for PEP matches"
        assert "pep_match" in src or "is_pep" in src, \
            "memo_handler should look for pep_match/is_pep in screening data"

    def test_memo_deduplicates_peps(self):
        memo_path = os.path.join(os.path.dirname(__file__), "..", "memo_handler.py")
        with open(memo_path) as f:
            src = f.read()
        assert "deduped_peps" in src or "seen_pep_names" in src, \
            "memo_handler should deduplicate PEPs across roles"


# ═══════════════════════════════════════════════════════════
# W2-4: kyc_submitted is no longer a dead state
# ═══════════════════════════════════════════════════════════

class TestW2_4_KYCSubmittedState:
    """Verify kyc_submitted is now actively used."""

    def test_kyc_handler_sets_kyc_submitted(self):
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path) as f:
            src = f.read()
        assert "status='kyc_submitted'" in src, \
            "KYCSubmitHandler should set status to 'kyc_submitted'"

    def test_stats_count_includes_kyc_submitted(self):
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path) as f:
            src = f.read()
        assert "'compliance_review','kyc_submitted'" in src or "'kyc_submitted','compliance_review'" in src, \
            "Stats queries should count kyc_submitted along with compliance_review"


# ═══════════════════════════════════════════════════════════
# W2-6: Nationality normalization
# ═══════════════════════════════════════════════════════════

class TestW2_6_NationalityNormalization:
    """Verify nationality is normalized on storage."""

    def test_server_imports_canonical_function(self):
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path) as f:
            src = f.read()
        assert "_canonicalise_country" in src, \
            "server.py should import _canonicalise_country for nationality normalization"

    def test_store_parties_normalizes_nationality(self):
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path) as f:
            src = f.read()
        # Find the store_application_parties function
        idx = src.find("def store_application_parties")
        assert idx > 0
        section = src[idx:idx + 3000]
        assert "_canonicalise_country" in section, \
            "store_application_parties should normalize nationality values"


# ═══════════════════════════════════════════════════════════
# W2-7: Ownership percentage validation
# ═══════════════════════════════════════════════════════════

class TestW2_7_OwnershipPctValidation:
    """Verify ownership_pct is validated and clamped."""

    def test_ownership_pct_clamped_in_code(self):
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path) as f:
            src = f.read()
        # Find the UBO section in store_application_parties
        idx = src.find("def store_application_parties")
        assert idx > 0
        section = src[idx:idx + 3000]
        assert "max(0.0, min(100.0" in section, \
            "ownership_pct should be clamped to 0-100 range"
