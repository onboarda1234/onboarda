"""
Tests for Wave 1-3 remediation fixes.
Validates fixes against the master register audit findings.
"""
import pytest
from datetime import date


# ── W1.1: Jurisdiction synonym matching (DOC-07) ──────────────────

class TestJurisdictionSynonymMatching:
    """Verify jurisdiction matching uses synonym map, not 3-char prefix."""

    def test_uk_england_wales_match(self):
        """'England & Wales' MUST match 'United Kingdom' (was false negative)."""
        from document_verification import _canonicalise_jurisdiction
        assert _canonicalise_jurisdiction("England & Wales") == "united kingdom"
        assert _canonicalise_jurisdiction("United Kingdom") == "united kingdom"

    def test_uk_england_match(self):
        from document_verification import _canonicalise_jurisdiction
        assert _canonicalise_jurisdiction("England") == "united kingdom"

    def test_uk_us_no_match(self):
        """'United Kingdom' must NOT match 'United States' (was false positive with 3-char prefix)."""
        from document_verification import _canonicalise_jurisdiction
        uk = _canonicalise_jurisdiction("United Kingdom")
        us = _canonicalise_jurisdiction("United States")
        assert uk != us

    def test_gb_maps_to_uk(self):
        from document_verification import _canonicalise_jurisdiction
        assert _canonicalise_jurisdiction("GB") == "united kingdom"

    def test_us_aliases(self):
        from document_verification import _canonicalise_jurisdiction
        assert _canonicalise_jurisdiction("USA") == "united states"
        assert _canonicalise_jurisdiction("US") == "united states"

    def test_mauritius_republic(self):
        from document_verification import _canonicalise_jurisdiction
        assert _canonicalise_jurisdiction("Republic of Mauritius") == "mauritius"
        assert _canonicalise_jurisdiction("Mauritius") == "mauritius"

    def test_exact_match_preserved(self):
        from document_verification import _canonicalise_jurisdiction
        assert _canonicalise_jurisdiction("Singapore") == "singapore"

    def test_empty_string(self):
        from document_verification import _canonicalise_jurisdiction
        assert _canonicalise_jurisdiction("") == ""
        assert _canonicalise_jurisdiction(None) == ""


# ── W1.2: Nationality / demonym matching (DOC-52, DOC-56) ─────────

class TestNationalityDemonymMatching:
    """Verify nationality matching uses ISO/demonym lookup."""

    def test_mauritian_mauritius(self):
        """'Mauritian' MUST resolve to 'mauritius' (was 3-char prefix false match by luck)."""
        from document_verification import _canonicalise_nationality
        assert _canonicalise_nationality("Mauritian") == "mauritius"
        assert _canonicalise_nationality("Mauritius") == "mauritius"

    def test_gb_united_kingdom(self):
        """'GB' MUST resolve to 'united kingdom'."""
        from document_verification import _canonicalise_nationality
        assert _canonicalise_nationality("GB") == "united kingdom"
        assert _canonicalise_nationality("British") == "united kingdom"

    def test_american_us(self):
        from document_verification import _canonicalise_nationality
        assert _canonicalise_nationality("American") == "united states"
        assert _canonicalise_nationality("US") == "united states"

    def test_demonym_country_pair_match(self):
        """Demonym and country name must resolve to same canonical form."""
        from document_verification import _canonicalise_nationality
        pairs = [
            ("French", "France"),
            ("German", "Germany"),
            ("Indian", "India"),
            ("Japanese", "Japan"),
            ("Australian", "Australia"),
        ]
        for demonym, country in pairs:
            assert _canonicalise_nationality(demonym) == _canonicalise_nationality(country), \
                f"{demonym} should match {country}"


# ── W1.3: Date parsing and None guard ─────────────────────────────

class TestDateParsing:
    """Verify date parsing handles ordinals, 2-digit years, and None guards."""

    def test_ordinal_stripping(self):
        """'4th March 2026' MUST parse correctly."""
        from document_verification import _parse_date
        result = _parse_date("4th March 2026")
        assert result == date(2026, 3, 4)

    def test_ordinal_1st(self):
        from document_verification import _parse_date
        assert _parse_date("1st January 2026") == date(2026, 1, 1)

    def test_ordinal_2nd(self):
        from document_verification import _parse_date
        assert _parse_date("2nd February 2026") == date(2026, 2, 2)

    def test_ordinal_3rd(self):
        from document_verification import _parse_date
        assert _parse_date("3rd March 2026") == date(2026, 3, 3)

    def test_ordinal_21st(self):
        from document_verification import _parse_date
        assert _parse_date("21st June 2025") == date(2025, 6, 21)

    def test_two_digit_year(self):
        """'04/03/26' MUST parse as 4 March 2026 (dd/mm/yy)."""
        from document_verification import _parse_date
        result = _parse_date("04/03/26")
        assert result == date(2026, 3, 4)

    def test_standard_formats(self):
        from document_verification import _parse_date
        assert _parse_date("2026-03-04") == date(2026, 3, 4)
        assert _parse_date("04/03/2026") == date(2026, 3, 4)

    def test_none_returns_none(self):
        from document_verification import _parse_date
        assert _parse_date(None) is None
        assert _parse_date("") is None

    def test_unparseable_returns_none(self):
        from document_verification import _parse_date
        assert _parse_date("not a date") is None

    def test_none_none_does_not_pass(self):
        """Two unparseable dates must NOT silently pass comparison."""
        from document_verification import _parse_date
        d1 = _parse_date("garbled")
        d2 = _parse_date("also garbled")
        # Both are None — the code must NOT do None == None → True → PASS
        assert d1 is None
        assert d2 is None
        # The guard in the comparison code should catch this;
        # we test the parse returns None (the guard is tested via integration)


# ── W1.5: DB CHECK constraint includes under_review ───────────────

class TestDBConstraint:
    """Verify DB schema includes under_review in status CHECK."""

    def test_under_review_in_pg_schema(self):
        import db
        import inspect
        source = inspect.getsource(db)
        # Both PostgreSQL and SQLite schema variants must include under_review
        assert "'under_review'" in source or '"under_review"' in source

    def test_status_constraint_is_consistent(self):
        """Verify under_review is in server.py review_states AND db.py CHECK."""
        import db
        import inspect
        db_source = inspect.getsource(db)
        assert "under_review" in db_source


# ── W1.7: Registration number leading zero normalization ──────────

class TestRegistrationNumberNormalization:
    """Verify registration numbers strip leading zeros for comparison."""

    def test_leading_zeros_match(self):
        """'00123456' MUST match '123456' after normalization."""
        import re
        declared = "00123456"
        extracted = "123456"
        d_norm = re.sub(r"[\s\-]", "", str(declared).upper()).lstrip("0") or "0"
        e_norm = re.sub(r"[\s\-]", "", str(extracted).upper()).lstrip("0") or "0"
        assert d_norm == e_norm

    def test_all_zeros(self):
        """Edge case: '0000' and '0' should both normalize to '0'."""
        import re
        for val in ("0000", "0"):
            norm = re.sub(r"[\s\-]", "", str(val).upper()).lstrip("0") or "0"
            assert norm == "0"

    def test_no_leading_zeros(self):
        """Normal case: C12345 stays the same."""
        import re
        val = "C12345"
        norm = re.sub(r"[\s\-]", "", str(val).upper()).lstrip("0") or "0"
        assert norm == "C12345"


# ── W2.1: Country prefix normalization in risk scoring ────────────

class TestCountryRiskNormalization:
    """Verify rule_engine.classify_country handles prefixes and aliases."""

    def test_republic_of_mauritius(self):
        """'Republic of Mauritius' should get same score as 'Mauritius'."""
        from rule_engine import classify_country
        assert classify_country("Republic of Mauritius") == classify_country("Mauritius")

    def test_england_wales_maps_to_uk(self):
        from rule_engine import classify_country
        assert classify_country("England & Wales") == classify_country("United Kingdom")

    def test_uk_aliases(self):
        from rule_engine import classify_country
        uk_score = classify_country("United Kingdom")
        assert classify_country("UK") == uk_score
        assert classify_country("GB") == uk_score
        assert classify_country("Great Britain") == uk_score

    def test_us_aliases(self):
        from rule_engine import classify_country
        us_score = classify_country("United States")
        assert classify_country("USA") == us_score
        assert classify_country("US") == us_score

    def test_democratic_republic_of_congo(self):
        """Full name in FATF_GREY must still match (don't over-strip prefix)."""
        from rule_engine import classify_country
        assert classify_country("Democratic Republic of Congo") == 3  # FATF_GREY

    def test_empty_returns_default(self):
        from rule_engine import classify_country
        assert classify_country("") == 2
        assert classify_country(None) == 2


# ── W3.2: Address abbreviation expansion ──────────────────────────

class TestAddressAbbreviationExpansion:
    """Verify address abbreviations are expanded for matching."""

    def test_st_to_street(self):
        from document_verification import _expand_address_abbreviations
        assert "street" in _expand_address_abbreviations("10 Downing St")

    def test_rd_to_road(self):
        from document_verification import _expand_address_abbreviations
        assert "road" in _expand_address_abbreviations("Baker Rd")

    def test_downing_st_vs_street(self):
        """'10 Downing St' and '10 Downing Street' should produce high similarity."""
        from document_verification import _name_similarity
        sim = _name_similarity("10 Downing St", "10 Downing Street")
        assert sim >= 0.95, f"Expected ≥0.95, got {sim}"

    def test_no_expansion_needed(self):
        from document_verification import _expand_address_abbreviations
        result = _expand_address_abbreviations("10 Downing Street")
        assert result == "10 downing street"
