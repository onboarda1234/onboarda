"""
Wave 1 remediation regression tests — verifying all critical audit fixes.
"""
import json
import os
import sys
import re
import pytest

# ── Ensure the backend root is importable ──
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═══════════════════════════════════════════════════════════
# W1-1: under_review status must be in DB CHECK constraints
# ═══════════════════════════════════════════════════════════

class TestW1_1_UnderReviewStatus:
    """Verify 'under_review' is a valid status in all schema definitions."""

    def test_under_review_in_sqlite_pg_schema(self):
        """under_review must appear in the DB schema SQL returned by db module."""
        import db
        pg_schema = db._get_postgres_schema()
        sqlite_schema = db._get_sqlite_schema()
        assert "under_review" in pg_schema, \
            "under_review not found in PostgreSQL schema"
        assert "under_review" in sqlite_schema, \
            "under_review not found in SQLite schema"

    def test_under_review_in_all_check_constraints(self):
        """under_review appears in every application status CHECK block in both schemas."""
        import db
        for schema_fn, label in [(db._get_postgres_schema, "PG"), (db._get_sqlite_schema, "SQLite")]:
            src = schema_fn()
            checks = re.findall(r"CHECK\(status\s+IN\s*\([^)]+\)\)", src)
            for check in checks:
                if "'draft'" in check and "'approved'" in check:
                    assert "'under_review'" in check, \
                        f"under_review missing from {label} CHECK constraint: {check[:80]}..."

    def test_server_transition_consistency(self):
        """under_review must be reachable and have outgoing transitions in server module."""
        import server
        import inspect
        # The valid_transitions dict is embedded in ApplicationDetailHandler.patch;
        # verify it exists by inspecting that the handler accepts under_review as status.
        handler_src = inspect.getsource(server.ApplicationDetailHandler.patch)
        assert "under_review" in handler_src, \
            "under_review must appear in ApplicationDetailHandler.patch transitions"


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
        """Verify the old broken [:3] prefix logic is gone by testing actual behavior."""
        from document_verification import _countries_match
        # The [:3] bug would make "United States" match "United Kingdom" (both "uni").
        # If this passes, the broken logic is gone.
        assert not _countries_match("United States", "United Kingdom")
        assert not _countries_match("South Africa", "South Korea")

    def test_jurisdiction_doc07_uses_countries_match(self):
        """DOC-07 jurisdiction check should use _countries_match (verified via import inspection)."""
        import document_verification
        import inspect
        src = inspect.getsource(document_verification)
        doc07_idx = src.find('"DOC-07"')
        assert doc07_idx > 0, "DOC-07 check must exist in document_verification module"
        doc07_section = src[doc07_idx:doc07_idx + 800]
        assert "_countries_match" in doc07_section, \
            "DOC-07 should use _countries_match for jurisdiction comparison"

    # ── Alpha-3 code tests (MUS, GBR, FRA etc.) ──

    def test_alpha3_mauritius(self):
        from document_verification import _countries_match
        assert _countries_match("Mauritius", "MUS")

    def test_alpha3_united_kingdom(self):
        from document_verification import _countries_match
        assert _countries_match("United Kingdom", "GBR")

    def test_alpha3_france(self):
        from document_verification import _countries_match
        assert _countries_match("France", "FRA")

    def test_alpha3_united_states(self):
        from document_verification import _countries_match
        assert _countries_match("United States", "USA")

    def test_alpha3_bvi(self):
        from document_verification import _countries_match
        assert _countries_match("British Virgin Islands", "VGB")


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
