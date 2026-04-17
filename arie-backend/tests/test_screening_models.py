"""
Tests for screening_models.py — Normalized screening schema, factories, and validator.
"""

import pytest

from screening_models import (
    VALID_COVERAGE_VALUES,
    NORMALIZED_PERSON_SCREENING_SCHEMA,
    NORMALIZED_COMPANY_SCREENING_SCHEMA,
    NORMALIZED_SCREENING_REPORT_SCHEMA,
    create_normalized_person_screening,
    create_normalized_company_screening,
    create_normalized_screening_report,
    validate_normalized_report,
)


class TestCoverageValues:
    def test_valid_coverage_values(self):
        assert "none" in VALID_COVERAGE_VALUES
        assert "partial" in VALID_COVERAGE_VALUES
        assert "full" in VALID_COVERAGE_VALUES
        assert len(VALID_COVERAGE_VALUES) == 3


class TestSchemaDefinitions:
    def test_person_schema_has_required_fields(self):
        expected = [
            "person_name", "person_type", "nationality", "declared_pep",
            "has_pep_hit", "has_sanctions_hit", "has_adverse_media_hit",
            "adverse_media_coverage", "screening",
        ]
        for f in expected:
            assert f in NORMALIZED_PERSON_SCREENING_SCHEMA

    def test_company_schema_has_required_fields(self):
        expected = [
            "company_screening_coverage", "has_company_screening_hit",
            "company_screening",
        ]
        for f in expected:
            assert f in NORMALIZED_COMPANY_SCREENING_SCHEMA

    def test_report_schema_has_required_fields(self):
        expected = [
            "provider", "normalized_version", "screened_at",
            "any_pep_hits", "any_sanctions_hits", "total_persons_screened",
            "director_screenings", "ubo_screenings", "overall_flags",
            "total_hits", "degraded_sources",
        ]
        for f in expected:
            assert f in NORMALIZED_SCREENING_REPORT_SCHEMA


class TestPersonFactory:
    def test_default_values(self):
        p = create_normalized_person_screening()
        assert p["person_name"] == ""
        assert p["person_type"] == "director"
        assert p["nationality"] == ""
        assert p["declared_pep"] == "No"
        assert p["has_pep_hit"] is None
        assert p["has_sanctions_hit"] is None
        assert p["has_adverse_media_hit"] is None
        assert p["adverse_media_coverage"] == "none"
        assert p["screening"] == {}

    def test_custom_values(self):
        p = create_normalized_person_screening(
            person_name="John Smith",
            person_type="ubo",
            has_pep_hit=True,
            has_sanctions_hit=False,
            adverse_media_coverage="full",
            has_adverse_media_hit=False,
        )
        assert p["person_name"] == "John Smith"
        assert p["person_type"] == "ubo"
        assert p["has_pep_hit"] is True
        assert p["has_sanctions_hit"] is False
        assert p["adverse_media_coverage"] == "full"
        assert p["has_adverse_media_hit"] is False

    def test_passthrough_extra_keys(self):
        p = create_normalized_person_screening(
            person_name="Jane",
            undeclared_pep=True,
            ownership_pct=25,
        )
        assert p["undeclared_pep"] is True
        assert p["ownership_pct"] == 25

    def test_null_hit_values(self):
        p = create_normalized_person_screening(
            has_pep_hit=None,
            has_sanctions_hit=None,
            has_adverse_media_hit=None,
        )
        assert p["has_pep_hit"] is None
        assert p["has_sanctions_hit"] is None
        assert p["has_adverse_media_hit"] is None

    def test_true_hit_values(self):
        p = create_normalized_person_screening(
            has_pep_hit=True,
            has_sanctions_hit=True,
            has_adverse_media_hit=True,
            adverse_media_coverage="full",
        )
        assert p["has_pep_hit"] is True
        assert p["has_sanctions_hit"] is True
        assert p["has_adverse_media_hit"] is True

    def test_false_hit_values(self):
        p = create_normalized_person_screening(
            has_pep_hit=False,
            has_sanctions_hit=False,
            has_adverse_media_hit=False,
            adverse_media_coverage="full",
        )
        assert p["has_pep_hit"] is False
        assert p["has_sanctions_hit"] is False
        assert p["has_adverse_media_hit"] is False


class TestCompanyFactory:
    def test_default_values(self):
        c = create_normalized_company_screening()
        assert c["company_screening_coverage"] == "none"
        assert c["has_company_screening_hit"] is None
        assert c["company_screening"] == {}

    def test_custom_values(self):
        c = create_normalized_company_screening(
            company_screening_coverage="partial",
            has_company_screening_hit=False,
            company_screening={"found": True},
        )
        assert c["company_screening_coverage"] == "partial"
        assert c["has_company_screening_hit"] is False
        assert c["company_screening"]["found"] is True


class TestReportFactory:
    def test_default_values(self):
        r = create_normalized_screening_report()
        assert r["provider"] == "sumsub"
        assert r["normalized_version"] == "1.0"
        assert r["any_pep_hits"] is False
        assert r["any_sanctions_hits"] is False
        assert r["total_persons_screened"] == 0
        assert r["adverse_media_coverage"] == "none"
        assert r["company_screening_coverage"] == "none"
        assert r["has_company_screening_hit"] is None
        assert r["director_screenings"] == []
        assert r["ubo_screenings"] == []
        assert r["overall_flags"] == []
        assert r["total_hits"] == 0
        assert r["degraded_sources"] == []

    def test_factory_produces_valid_report(self):
        r = create_normalized_screening_report(screened_at="2025-01-01T00:00:00")
        errors = validate_normalized_report(r)
        assert errors == [], f"Factory output not valid: {errors}"

    def test_passthrough_extra_keys(self):
        r = create_normalized_screening_report(
            ip_geolocation={"source": "ipapi"},
            kyc_applicants=[],
        )
        assert r["ip_geolocation"] == {"source": "ipapi"}
        assert r["kyc_applicants"] == []


class TestValidator:
    def test_valid_report_passes(self):
        r = create_normalized_screening_report(screened_at="2025-01-01T00:00:00")
        assert validate_normalized_report(r) == []

    def test_non_dict_fails(self):
        errors = validate_normalized_report("not a dict")
        assert "report must be a dict" in errors

    def test_missing_required_field(self):
        r = create_normalized_screening_report(screened_at="2025-01-01T00:00:00")
        del r["provider"]
        errors = validate_normalized_report(r)
        assert any("missing required field: provider" in e for e in errors)

    def test_wrong_type_field(self):
        r = create_normalized_screening_report(screened_at="2025-01-01T00:00:00")
        r["total_hits"] = "not_an_int"
        errors = validate_normalized_report(r)
        assert any("total_hits" in e and "int" in e for e in errors)

    def test_invalid_adverse_media_coverage_string(self):
        r = create_normalized_screening_report(screened_at="2025-01-01T00:00:00")
        r["adverse_media_coverage"] = "invalid"
        errors = validate_normalized_report(r)
        assert any("adverse_media_coverage" in e for e in errors)

    def test_invalid_company_screening_coverage_string(self):
        r = create_normalized_screening_report(screened_at="2025-01-01T00:00:00")
        r["company_screening_coverage"] = "invalid"
        errors = validate_normalized_report(r)
        assert any("company_screening_coverage" in e for e in errors)

    def test_none_coverage_with_non_null_hit_fails(self):
        r = create_normalized_screening_report(screened_at="2025-01-01T00:00:00")
        r["adverse_media_coverage"] = "none"
        r["has_adverse_media_hit"] = True
        errors = validate_normalized_report(r)
        assert any("has_adverse_media_hit" in e and "null" in e for e in errors)

    def test_none_company_coverage_with_non_null_hit_fails(self):
        r = create_normalized_screening_report(screened_at="2025-01-01T00:00:00")
        r["company_screening_coverage"] = "none"
        r["has_company_screening_hit"] = False
        errors = validate_normalized_report(r)
        assert any("has_company_screening_hit" in e and "null" in e for e in errors)

    def test_full_coverage_with_true_hit_passes(self):
        r = create_normalized_screening_report(screened_at="2025-01-01T00:00:00")
        r["adverse_media_coverage"] = "full"
        r["has_adverse_media_hit"] = True
        errors = validate_normalized_report(r)
        assert not any("has_adverse_media_hit" in e for e in errors)

    def test_full_coverage_with_false_hit_passes(self):
        r = create_normalized_screening_report(screened_at="2025-01-01T00:00:00")
        r["adverse_media_coverage"] = "full"
        r["has_adverse_media_hit"] = False
        errors = validate_normalized_report(r)
        assert not any("has_adverse_media_hit" in e for e in errors)

    def test_full_coverage_with_null_hit_passes(self):
        r = create_normalized_screening_report(screened_at="2025-01-01T00:00:00")
        r["adverse_media_coverage"] = "full"
        r["has_adverse_media_hit"] = None
        errors = validate_normalized_report(r)
        assert not any("has_adverse_media_hit" in e for e in errors)

    def test_hit_field_wrong_type(self):
        r = create_normalized_screening_report(screened_at="2025-01-01T00:00:00")
        r["has_adverse_media_hit"] = "yes"
        r["adverse_media_coverage"] = "full"
        errors = validate_normalized_report(r)
        assert any("has_adverse_media_hit" in e and "bool or None" in e for e in errors)

    def test_person_screening_invalid_coverage(self):
        r = create_normalized_screening_report(screened_at="2025-01-01T00:00:00")
        r["director_screenings"] = [
            create_normalized_person_screening(
                person_name="Test",
                adverse_media_coverage="invalid",
            )
        ]
        errors = validate_normalized_report(r)
        assert any("director_screenings[0].adverse_media_coverage" in e for e in errors)

    def test_person_screening_none_coverage_with_hit(self):
        r = create_normalized_screening_report(screened_at="2025-01-01T00:00:00")
        r["director_screenings"] = [
            create_normalized_person_screening(
                person_name="Test",
                adverse_media_coverage="none",
                has_adverse_media_hit=True,
            )
        ]
        errors = validate_normalized_report(r)
        assert any("director_screenings[0].has_adverse_media_hit" in e and "null" in e for e in errors)

    def test_person_hit_field_wrong_type(self):
        r = create_normalized_screening_report(screened_at="2025-01-01T00:00:00")
        r["director_screenings"] = [
            create_normalized_person_screening(person_name="Test")
        ]
        r["director_screenings"][0]["has_pep_hit"] = "yes"
        errors = validate_normalized_report(r)
        assert any("has_pep_hit" in e and "bool or None" in e for e in errors)

    def test_non_dict_person_in_list(self):
        r = create_normalized_screening_report(screened_at="2025-01-01T00:00:00")
        r["director_screenings"] = ["not_a_dict"]
        errors = validate_normalized_report(r)
        assert any("director_screenings[0] must be a dict" in e for e in errors)

    def test_empty_report_has_errors(self):
        errors = validate_normalized_report({})
        assert len(errors) > 0

    def test_all_coverage_values_accepted(self):
        for cov in ("none", "partial", "full"):
            r = create_normalized_screening_report(
                screened_at="2025-01-01T00:00:00",
                adverse_media_coverage=cov,
                has_adverse_media_hit=None if cov == "none" else False,
                company_screening_coverage=cov,
                has_company_screening_hit=None if cov == "none" else False,
            )
            errors = validate_normalized_report(r)
            cov_errors = [e for e in errors if "coverage" in e or "hit" in e]
            assert cov_errors == [], f"coverage='{cov}' errors: {cov_errors}"
