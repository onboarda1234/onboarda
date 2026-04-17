"""
Tests for screening_models.py — SCR-003
========================================
Validates normalized screening model factories and validation.
"""

import pytest
from screening_models import (
    create_normalized_person_screening,
    create_normalized_company_screening,
    create_normalized_screening_report,
    validate_normalized_report,
    NORMALIZER_ADDED_REPORT_KEYS,
    NORMALIZER_ADDED_PERSON_KEYS,
)


# ── Fixtures ──

def _pep_screening():
    return {
        "matched": True,
        "results": [
            {"is_pep": True, "is_sanctioned": False, "match_score": 85.0,
             "matched_name": "Jane Doe", "datasets": ["AML"], "schema": "Person"},
        ],
        "source": "sumsub",
        "api_status": "live",
        "screened_at": "2025-01-01T00:00:00",
    }


def _sanctions_screening():
    return {
        "matched": True,
        "results": [
            {"is_pep": False, "is_sanctioned": True, "match_score": 92.0,
             "matched_name": "OFAC Entity", "datasets": ["sanctions"], "schema": "Person"},
        ],
        "source": "sumsub",
        "api_status": "live",
        "screened_at": "2025-01-01T00:00:00",
    }


def _clear_screening():
    return {
        "matched": False,
        "results": [],
        "source": "sumsub",
        "api_status": "live",
        "screened_at": "2025-01-01T00:00:00",
    }


# ── Person Screening Tests ──

class TestCreateNormalizedPersonScreening:

    def test_basic_creation(self):
        p = create_normalized_person_screening(
            person_name="Jane Doe", person_type="director",
            screening=_clear_screening(),
        )
        assert p["person_name"] == "Jane Doe"
        assert p["person_type"] == "director"
        assert p["has_pep_hit"] is False
        assert p["has_sanctions_hit"] is False
        assert p["has_adverse_media_hit"] is False

    def test_pep_hit_detected(self):
        p = create_normalized_person_screening(
            person_name="PEP Person", person_type="director",
            screening=_pep_screening(),
        )
        assert p["has_pep_hit"] is True
        assert p["has_sanctions_hit"] is False

    def test_sanctions_hit_detected(self):
        p = create_normalized_person_screening(
            person_name="Sanctions Hit", person_type="ubo",
            screening=_sanctions_screening(),
        )
        assert p["has_sanctions_hit"] is True
        assert p["has_pep_hit"] is False

    def test_undeclared_pep_passed_through(self):
        p = create_normalized_person_screening(
            person_name="Test", person_type="director",
            screening=_clear_screening(), undeclared_pep=True,
        )
        assert p["undeclared_pep"] is True

    def test_ownership_pct_passed_through(self):
        p = create_normalized_person_screening(
            person_name="UBO", person_type="ubo",
            screening=_clear_screening(), ownership_pct=25,
        )
        assert p["ownership_pct"] == 25

    def test_missing_screening_defaults(self):
        p = create_normalized_person_screening(
            person_name="Test", person_type="director",
        )
        assert p["has_pep_hit"] is False
        assert p["has_sanctions_hit"] is False
        assert p["screening"] == {}

    def test_empty_results_no_hits(self):
        p = create_normalized_person_screening(
            person_name="Test", person_type="director",
            screening={"matched": False, "results": []},
        )
        assert p["has_pep_hit"] is False

    def test_none_screening_handled(self):
        p = create_normalized_person_screening(
            person_name="Test", person_type="director",
            screening=None,
        )
        assert p["has_pep_hit"] is False


# ── Company Screening Tests ──

class TestCreateNormalizedCompanyScreening:

    def test_basic_creation(self):
        cs = {"found": True, "companies": [], "sanctions": {"matched": False, "results": []}}
        c = create_normalized_company_screening(company_screening=cs)
        assert c["has_sanctions_hit"] is False
        assert c["company_screening"] == cs

    def test_sanctions_match(self):
        cs = {"found": True, "sanctions": {"matched": True, "results": [{"match": True}]}}
        c = create_normalized_company_screening(company_screening=cs)
        assert c["has_sanctions_hit"] is True

    def test_empty_company(self):
        c = create_normalized_company_screening()
        assert c["has_sanctions_hit"] is False


# ── Report Tests ──

class TestCreateNormalizedScreeningReport:

    def test_basic_report(self):
        r = create_normalized_screening_report(
            screened_at="2025-01-01T00:00:00",
            director_screenings=[
                create_normalized_person_screening(
                    person_name="D1", person_type="director",
                    screening=_clear_screening()),
            ],
            ubo_screenings=[],
        )
        assert r["provider"] == "sumsub"
        assert r["normalized_version"] == "1.0"
        assert r["any_pep_hits"] is False
        assert r["any_sanctions_hits"] is False
        assert r["total_persons_screened"] == 1

    def test_pep_propagates_to_report(self):
        r = create_normalized_screening_report(
            screened_at="2025-01-01T00:00:00",
            director_screenings=[
                create_normalized_person_screening(
                    person_name="PEP", person_type="director",
                    screening=_pep_screening()),
            ],
            ubo_screenings=[],
        )
        assert r["any_pep_hits"] is True

    def test_total_persons_counts_both(self):
        r = create_normalized_screening_report(
            screened_at="2025-01-01T00:00:00",
            director_screenings=[
                create_normalized_person_screening(
                    person_name="D1", person_type="director",
                    screening=_clear_screening()),
            ],
            ubo_screenings=[
                create_normalized_person_screening(
                    person_name="U1", person_type="ubo",
                    screening=_clear_screening()),
                create_normalized_person_screening(
                    person_name="U2", person_type="ubo",
                    screening=_clear_screening()),
            ],
        )
        assert r["total_persons_screened"] == 3


# ── Validation Tests ──

class TestValidateNormalizedReport:

    def test_valid_report(self):
        r = create_normalized_screening_report(
            screened_at="2025-01-01T00:00:00",
            director_screenings=[], ubo_screenings=[],
        )
        assert validate_normalized_report(r) == []

    def test_missing_screened_at(self):
        r = create_normalized_screening_report(
            screened_at="2025-01-01T00:00:00",
            director_screenings=[], ubo_screenings=[],
        )
        del r["screened_at"]
        errors = validate_normalized_report(r)
        assert any("screened_at" in e for e in errors)

    def test_missing_provider(self):
        r = create_normalized_screening_report(
            screened_at="2025-01-01T00:00:00",
            director_screenings=[], ubo_screenings=[],
        )
        del r["provider"]
        errors = validate_normalized_report(r)
        assert any("provider" in e for e in errors)

    def test_non_dict_report(self):
        errors = validate_normalized_report("not a dict")
        assert errors == ["report must be a dict"]

    def test_director_screenings_not_list(self):
        r = create_normalized_screening_report(
            screened_at="2025-01-01T00:00:00",
            director_screenings="bad", ubo_screenings=[],
        )
        r["director_screenings"] = "bad"
        errors = validate_normalized_report(r)
        assert any("director_screenings must be a list" in e for e in errors)

    def test_director_missing_person_name(self):
        r = create_normalized_screening_report(
            screened_at="2025-01-01T00:00:00",
            director_screenings=[{"person_type": "director"}],
            ubo_screenings=[],
        )
        errors = validate_normalized_report(r)
        assert any("person_name" in e for e in errors)


# ── Metadata Key Sets ──

class TestNormalizerKeysets:

    def test_report_keys_defined(self):
        assert "provider" in NORMALIZER_ADDED_REPORT_KEYS
        assert "normalized_version" in NORMALIZER_ADDED_REPORT_KEYS
        assert "any_pep_hits" in NORMALIZER_ADDED_REPORT_KEYS

    def test_person_keys_defined(self):
        assert "has_pep_hit" in NORMALIZER_ADDED_PERSON_KEYS
        assert "has_sanctions_hit" in NORMALIZER_ADDED_PERSON_KEYS
        assert "has_adverse_media_hit" in NORMALIZER_ADDED_PERSON_KEYS
