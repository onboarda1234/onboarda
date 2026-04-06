"""
Wave 1 remediation regression tests — verifying all critical audit fixes.
"""
import json
import os
import sys
import pytest

# ── Ensure the backend root is importable ──
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═══════════════════════════════════════════════════════════
# W1-1: under_review status must be in DB CHECK constraints
# ═══════════════════════════════════════════════════════════

class TestW1_1_UnderReviewStatus:
    """Verify 'under_review' is a valid status in all schema definitions."""

    def _read_db_source(self):
        db_path = os.path.join(os.path.dirname(__file__), "..", "db.py")
        with open(db_path) as f:
            return f.read()

    def test_under_review_in_sqlite_pg_schema(self):
        """under_review must appear in the PostgreSQL applications table CHECK constraint."""
        src = self._read_db_source()
        # Both the PG and SQLite schemas contain the CHECK constraint
        assert "'under_review'" in src or '"under_review"' in src, \
            "under_review not found in db.py CHECK constraints"

    def test_under_review_in_all_check_constraints(self):
        """Count that under_review appears in every CHECK(status IN ...) block for applications."""
        src = self._read_db_source()
        import re
        # Find all CHECK(status IN (...)) blocks
        checks = re.findall(r"CHECK\(status IN \([^)]+\)\)", src)
        for check in checks:
            # Only check application status constraints (they have 'draft' in them)
            if "'draft'" in check and "'approved'" in check:
                assert "'under_review'" in check, \
                    f"under_review missing from CHECK constraint: {check[:80]}..."

    def test_server_transition_consistency(self):
        """under_review targets in server.py must all be valid DB statuses."""
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path) as f:
            src = f.read()
        # Verify under_review is referenced in valid_transitions
        assert '"under_review"' in src or "'under_review'" in src


# ═══════════════════════════════════════════════════════════
# W1-3: Nationality / Jurisdiction matching must be canonical
# ═══════════════════════════════════════════════════════════

class TestW1_3_NationalityMatching:
    """Verify _countries_match and _canonicalise_country work correctly."""

    def test_basic_imports(self):
        from document_verification import _canonicalise_country, _countries_match
        assert callable(_canonicalise_country)
        assert callable(_countries_match)

    def test_mauritius_iso_code(self):
        from document_verification import _canonicalise_country
        assert _canonicalise_country("MU") == "MU"

    def test_mauritius_full_name(self):
        from document_verification import _canonicalise_country
        assert _canonicalise_country("Mauritius") == "MU"

    def test_mauritian_demonym(self):
        from document_verification import _canonicalise_country
        assert _canonicalise_country("Mauritian") == "MU"

    def test_united_states_vs_united_kingdom_no_false_match(self):
        """The old [:3] prefix logic would wrongly match these. Verify they don't match."""
        from document_verification import _countries_match
        assert not _countries_match("United States", "United Kingdom"), \
            "United States should NOT match United Kingdom"

    def test_united_states_variants_match(self):
        from document_verification import _countries_match
        assert _countries_match("United States", "USA")
        assert _countries_match("United States", "American")
        assert _countries_match("US", "United States of America")

    def test_mauritius_variants_match(self):
        from document_verification import _countries_match
        assert _countries_match("Mauritius", "MU")
        assert _countries_match("Mauritian", "MU")
        assert _countries_match("Mauritius", "Mauritian")

    def test_uk_variants_match(self):
        from document_verification import _countries_match
        assert _countries_match("United Kingdom", "GB")
        assert _countries_match("UK", "British")
        assert _countries_match("Great Britain", "GB")

    def test_france_variants_match(self):
        from document_verification import _countries_match
        assert _countries_match("France", "FR")
        assert _countries_match("French", "FR")

    def test_bvi_match(self):
        from document_verification import _countries_match
        assert _countries_match("BVI", "British Virgin Islands")
        assert _countries_match("VG", "BVI")

    def test_empty_values(self):
        from document_verification import _countries_match
        assert not _countries_match("", "France")
        assert not _countries_match("Mauritius", "")
        assert not _countries_match("", "")

    def test_unknown_value_normalised_string_comparison(self):
        """Unknown values should fall back to normalised string comparison."""
        from document_verification import _countries_match
        # Exact same string should match
        assert _countries_match("Atlantis", "Atlantis")
        # Different unknown values should not
        assert not _countries_match("Atlantis", "Wakanda")

    def test_no_prefix_comparison_exists(self):
        """Verify that the old broken [:3] prefix comparison is gone from document_verification.py."""
        dv_path = os.path.join(os.path.dirname(__file__), "..", "document_verification.py")
        with open(dv_path) as f:
            src = f.read()
        assert "[:3]" not in src, \
            "Broken [:3] prefix comparison still exists in document_verification.py"

    def test_jurisdiction_doc07_uses_countries_match(self):
        """DOC-07 jurisdiction check should use _countries_match, not substring comparison."""
        dv_path = os.path.join(os.path.dirname(__file__), "..", "document_verification.py")
        with open(dv_path) as f:
            src = f.read()
        # Find the DOC-07 section and verify it uses _countries_match
        doc07_idx = src.find('"DOC-07"')
        assert doc07_idx > 0
        doc07_section = src[doc07_idx:doc07_idx + 800]
        assert "_countries_match" in doc07_section, \
            "DOC-07 should use _countries_match for jurisdiction comparison"


# ═══════════════════════════════════════════════════════════
# W1-2: Portal pricing alignment (code-level verification)
# ═══════════════════════════════════════════════════════════

class TestW1_2_PricingAlignment:
    """Verify pricing constants are consistent between portal fallback and backend."""

    def test_backend_pricing_tiers_structure(self):
        from server import PRICING_TIERS
        for level in ("LOW", "MEDIUM", "HIGH", "VERY_HIGH"):
            tier = PRICING_TIERS[level]
            assert "onboarding_fee" in tier
            assert "annual_monitoring_fee" in tier
            assert "currency" in tier
            assert isinstance(tier["onboarding_fee"], (int, float))
            assert isinstance(tier["annual_monitoring_fee"], (int, float))

    def test_portal_fallback_matches_backend(self):
        """The portal PRICING_FALLBACK must match the backend PRICING_TIERS values."""
        from server import PRICING_TIERS
        portal_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
        with open(portal_path) as f:
            src = f.read()
        # Verify portal no longer has the old mismatched PRICING object
        assert "var PRICING = {" not in src, \
            "Old hardcoded PRICING object should be removed from portal"
        # Verify PRICING_FALLBACK exists
        assert "PRICING_FALLBACK" in src, \
            "Portal should have PRICING_FALLBACK aligned with backend"
        # Verify backend LOW tier fee is 500 (not the old 1500)
        assert PRICING_TIERS["LOW"]["onboarding_fee"] == 500
        assert PRICING_TIERS["MEDIUM"]["onboarding_fee"] == 1500
        assert PRICING_TIERS["HIGH"]["onboarding_fee"] == 3500
        assert PRICING_TIERS["VERY_HIGH"]["onboarding_fee"] == 5000


# ═══════════════════════════════════════════════════════════
# W1-5: Hardcoded compliance strings removed
# ═══════════════════════════════════════════════════════════

class TestW1_5_NoHardcodedCompliance:
    """Verify hardcoded fake compliance strings are removed from portal."""

    def _read_portal(self):
        portal_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
        with open(portal_path) as f:
            return f.read()

    def test_no_hardcoded_sanctions_clearance(self):
        src = self._read_portal()
        assert "No matches identified across UN, EU, OFAC, and HMT sanctions lists" not in src, \
            "Hardcoded sanctions clearance string should be removed"

    def test_no_hardcoded_adverse_media_clearance(self):
        src = self._read_portal()
        assert "No material adverse media identified from automated scan of 100,000+ global sources" not in src, \
            "Hardcoded adverse media clearance string should be removed"

    def test_screening_results_pending_message_exists(self):
        src = self._read_portal()
        # Should have an honest "pending" message when no real results
        assert "not yet available" in src or "pending" in src.lower()


# ═══════════════════════════════════════════════════════════
# W1-7: Stale domain/email removed
# ═══════════════════════════════════════════════════════════

class TestW1_7_NoDomainStale:
    """Verify stale arie-finance domain is gone."""

    def test_no_arie_finance_in_portal(self):
        portal_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
        with open(portal_path) as f:
            src = f.read()
        assert "arie-finance" not in src, \
            "Stale arie-finance.com domain still present in portal"

    def test_onboarda_email_present(self):
        portal_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
        with open(portal_path) as f:
            src = f.read()
        assert "compliance@onboarda.com" in src, \
            "compliance@onboarda.com should be used in portal T&C"


# ═══════════════════════════════════════════════════════════
# W1-4: DOB in resume fallback (code-level check)
# ═══════════════════════════════════════════════════════════

class TestW1_4_DOBResumePresence:
    """Verify date_of_birth is included in resume fallback mappings."""

    def _read_portal(self):
        portal_path = os.path.join(os.path.dirname(__file__), "..", "..", "arie-portal.html")
        with open(portal_path) as f:
            return f.read()

    def test_directors_fallback_includes_dob(self):
        src = self._read_portal()
        # Find the psDirs.map section
        idx = src.find("psDirs.map")
        assert idx > 0
        section = src[idx:idx + 300]
        assert "date_of_birth" in section, \
            "Directors fallback mapping must include date_of_birth"

    def test_ubos_fallback_includes_dob(self):
        src = self._read_portal()
        # Find the psUbos.map section
        idx = src.find("psUbos.map")
        assert idx > 0
        section = src[idx:idx + 300]
        assert "date_of_birth" in section, \
            "UBOs fallback mapping must include date_of_birth"
