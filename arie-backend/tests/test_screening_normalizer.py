"""
Tests for screening_normalizer.py — SCR-006
=============================================
Validates normalize → denormalize round-trip and edge cases.
"""

import copy
import pytest
from screening_normalizer import (
    normalize_screening_report,
    denormalize_to_legacy,
)


# ── Fixtures ──

def _clear_screening():
    return {
        "matched": False,
        "results": [],
        "source": "sumsub",
        "api_status": "live",
        "screened_at": "2025-01-01T00:00:00",
    }


def _pep_screening():
    return {
        "matched": True,
        "results": [{
            "match_score": 85.0, "matched_name": "Jane Doe",
            "datasets": ["AML"], "schema": "Person",
            "topics": ["pep"], "countries": ["US"],
            "sanctions_list": "", "is_pep": True, "is_sanctioned": False,
        }],
        "source": "sumsub", "api_status": "live",
        "screened_at": "2025-01-01T00:00:00",
    }


def _sanctions_screening():
    return {
        "matched": True,
        "results": [{
            "match_score": 92.0, "matched_name": "OFAC Entity",
            "datasets": ["sanctions"], "schema": "Person",
            "topics": ["sanction"], "countries": [],
            "sanctions_list": "OFAC SDN", "is_pep": False, "is_sanctioned": True,
        }],
        "source": "sumsub", "api_status": "live",
        "screened_at": "2025-01-01T00:00:00",
    }


def _error_screening():
    return {
        "matched": False, "results": [],
        "source": "sumsub", "api_status": "error",
        "error": "Sumsub AML screening failed: timeout",
        "screened_at": "2025-01-01T00:00:00",
    }


def _simulated_screening():
    return {
        "matched": False, "results": [],
        "source": "simulated", "api_status": "simulated",
        "note": "Simulated result",
        "screened_at": "2025-01-01T00:00:00",
    }


# ── Round-trip test helper ──

def assert_roundtrip(raw):
    """Verify that denormalize(normalize(raw)) == raw."""
    original = copy.deepcopy(raw)
    normalized = normalize_screening_report(raw)
    legacy = denormalize_to_legacy(normalized)
    assert legacy == original, (
        f"Round-trip mismatch.\n"
        f"Original keys: {sorted(original.keys())}\n"
        f"Legacy keys:   {sorted(legacy.keys())}"
    )


class TestNormalizeBasic:
    """normalize_screening_report adds correct metadata."""

    def test_adds_provider_and_version(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {},
            "director_screenings": [],
            "ubo_screenings": [],
            "ip_geolocation": {},
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }
        n = normalize_screening_report(raw)
        assert n["provider"] == "sumsub"
        assert n["normalized_version"] == "1.0"
        assert n["any_pep_hits"] is False
        assert n["any_sanctions_hits"] is False
        assert n["total_persons_screened"] == 0

    def test_pep_hit_detected(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {},
            "director_screenings": [{
                "person_name": "PEP Person", "person_type": "director",
                "nationality": "US", "declared_pep": "No",
                "screening": _pep_screening(),
            }],
            "ubo_screenings": [],
            "overall_flags": [], "total_hits": 1,
            "degraded_sources": [],
        }
        n = normalize_screening_report(raw)
        assert n["any_pep_hits"] is True
        assert n["director_screenings"][0]["has_pep_hit"] is True

    def test_sanctions_hit_from_company(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {
                "found": True,
                "sanctions": {"matched": True, "results": [{"match": True}]},
            },
            "director_screenings": [],
            "ubo_screenings": [],
            "overall_flags": [], "total_hits": 1,
            "degraded_sources": [],
        }
        n = normalize_screening_report(raw)
        assert n["any_sanctions_hits"] is True

    def test_does_not_mutate_original(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {},
            "director_screenings": [{
                "person_name": "D1", "person_type": "director",
                "nationality": "", "declared_pep": "No",
                "screening": _clear_screening(),
            }],
            "ubo_screenings": [],
            "overall_flags": [], "total_hits": 0,
            "degraded_sources": [],
        }
        original = copy.deepcopy(raw)
        normalize_screening_report(raw)
        assert raw == original, "normalize_screening_report mutated the input"


class TestDenormalizeBasic:
    """denormalize_to_legacy strips normalizer metadata."""

    def test_strips_report_keys(self):
        normalized = {
            "screened_at": "2025-01-01T00:00:00",
            "provider": "sumsub",
            "normalized_version": "1.0",
            "any_pep_hits": False,
            "any_sanctions_hits": False,
            "total_persons_screened": 0,
            "director_screenings": [],
            "ubo_screenings": [],
        }
        legacy = denormalize_to_legacy(normalized)
        assert "provider" not in legacy
        assert "normalized_version" not in legacy
        assert "any_pep_hits" not in legacy

    def test_strips_person_keys(self):
        normalized = {
            "screened_at": "2025-01-01T00:00:00",
            "provider": "sumsub",
            "normalized_version": "1.0",
            "any_pep_hits": False,
            "any_sanctions_hits": False,
            "total_persons_screened": 1,
            "director_screenings": [{
                "person_name": "D1", "person_type": "director",
                "nationality": "", "declared_pep": "No",
                "screening": _clear_screening(),
                "has_pep_hit": False,
                "has_sanctions_hit": False,
                "has_adverse_media_hit": False,
            }],
            "ubo_screenings": [],
        }
        legacy = denormalize_to_legacy(normalized)
        d = legacy["director_screenings"][0]
        assert "has_pep_hit" not in d
        assert "has_sanctions_hit" not in d
        assert "has_adverse_media_hit" not in d


class TestRoundTrip:
    """Critical invariant: denormalize(normalize(raw)) == raw."""

    def test_empty_report(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {},
            "director_screenings": [],
            "ubo_screenings": [],
            "ip_geolocation": {},
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }
        assert_roundtrip(raw)

    def test_single_director_clear(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {"found": True, "source": "opencorporates"},
            "director_screenings": [{
                "person_name": "John Smith", "person_type": "director",
                "nationality": "GB", "declared_pep": "No",
                "screening": _clear_screening(),
            }],
            "ubo_screenings": [],
            "ip_geolocation": {"country": "GB", "source": "ipapi"},
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }
        assert_roundtrip(raw)

    def test_pep_hit_with_undeclared(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {},
            "director_screenings": [{
                "person_name": "PEP Person", "person_type": "director",
                "nationality": "US", "declared_pep": "No",
                "undeclared_pep": True,
                "screening": _pep_screening(),
            }],
            "ubo_screenings": [],
            "overall_flags": ["Director 'PEP Person' has sanctions/PEP matches"],
            "total_hits": 1,
            "degraded_sources": [],
        }
        assert_roundtrip(raw)

    def test_multiple_directors_and_ubos(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {
                "found": True,
                "sanctions": {"matched": False, "results": [], "source": "sumsub", "api_status": "live"},
            },
            "director_screenings": [
                {"person_name": "D1", "person_type": "director",
                 "nationality": "MU", "declared_pep": "No",
                 "screening": _clear_screening()},
                {"person_name": "D2", "person_type": "director",
                 "nationality": "FR", "declared_pep": "Yes",
                 "screening": _pep_screening()},
            ],
            "ubo_screenings": [
                {"person_name": "U1", "person_type": "ubo",
                 "nationality": "SG", "declared_pep": "No",
                 "ownership_pct": 51,
                 "screening": _clear_screening()},
            ],
            "ip_geolocation": {"country": "MU", "source": "ipapi", "api_status": "live"},
            "overall_flags": ["Director 'D2' has PEP matches"],
            "total_hits": 1,
            "degraded_sources": [],
            "kyc_applicants": [
                {"person_name": "D1", "person_type": "director",
                 "applicant_id": "abc123", "api_status": "live"},
            ],
            "screening_mode": "live",
        }
        assert_roundtrip(raw)

    def test_error_screening(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {},
            "director_screenings": [{
                "person_name": "Error Person", "person_type": "director",
                "nationality": "", "declared_pep": "No",
                "screening": _error_screening(),
            }],
            "ubo_screenings": [],
            "overall_flags": ["Director 'Error Person' screening unavailable"],
            "total_hits": 0,
            "degraded_sources": ["director_screening:Error Person"],
        }
        assert_roundtrip(raw)

    def test_simulated_screening(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {},
            "director_screenings": [{
                "person_name": "Sim Person", "person_type": "director",
                "nationality": "", "declared_pep": "No",
                "screening": _simulated_screening(),
            }],
            "ubo_screenings": [],
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
            "screening_mode": "simulated",
        }
        assert_roundtrip(raw)

    def test_degraded_sources(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {"found": False, "source": "unavailable", "degraded": True},
            "director_screenings": [],
            "ubo_screenings": [],
            "ip_geolocation": {"source": "unavailable", "degraded": True},
            "overall_flags": ["Company registry lookup unavailable"],
            "total_hits": 0,
            "degraded_sources": ["opencorporates", "ip_geolocation"],
        }
        assert_roundtrip(raw)

    def test_empty_directors_empty_ubos(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {},
            "director_screenings": [],
            "ubo_screenings": [],
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }
        assert_roundtrip(raw)

    def test_sanctions_hit_on_person(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {},
            "director_screenings": [],
            "ubo_screenings": [{
                "person_name": "Sanctioned UBO", "person_type": "ubo",
                "nationality": "", "declared_pep": "No",
                "ownership_pct": 30,
                "screening": _sanctions_screening(),
            }],
            "overall_flags": ["UBO 'Sanctioned UBO' has sanctions matches"],
            "total_hits": 1,
            "degraded_sources": [],
        }
        assert_roundtrip(raw)

    def test_company_with_sanctions_match(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {
                "found": True,
                "companies": [{"name": "Bad Corp"}],
                "sanctions": {
                    "matched": True,
                    "results": [{"match_score": 95, "matched_name": "Bad Corp"}],
                    "source": "sumsub", "api_status": "live",
                },
                "source": "opencorporates", "api_status": "live",
            },
            "director_screenings": [],
            "ubo_screenings": [],
            "overall_flags": ["Company has sanctions matches"],
            "total_hits": 1,
            "degraded_sources": [],
        }
        assert_roundtrip(raw)

    def test_kyc_applicants_preserved(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {},
            "director_screenings": [],
            "ubo_screenings": [],
            "ip_geolocation": {},
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
            "kyc_applicants": [
                {"person_name": "D1", "person_type": "director",
                 "applicant_id": "abc", "api_status": "live"},
                {"person_name": "U1", "person_type": "ubo",
                 "applicant_id": "def", "api_status": "live"},
            ],
        }
        assert_roundtrip(raw)


class TestEdgeCases:
    """Edge cases that must not break normalization."""

    def test_non_dict_raises(self):
        with pytest.raises(ValueError):
            normalize_screening_report("not a dict")

    def test_denormalize_non_dict_raises(self):
        with pytest.raises(ValueError):
            denormalize_to_legacy("not a dict")

    def test_missing_director_screenings(self):
        raw = {"screened_at": "2025-01-01T00:00:00"}
        n = normalize_screening_report(raw)
        assert n["total_persons_screened"] == 0
        assert_roundtrip(raw)

    def test_none_screening_in_person(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "director_screenings": [{
                "person_name": "NoScreen", "person_type": "director",
                "nationality": "", "declared_pep": "No",
                "screening": None,
            }],
            "ubo_screenings": [],
        }
        n = normalize_screening_report(raw)
        assert n["director_screenings"][0]["has_pep_hit"] is False
        assert_roundtrip(raw)

    def test_empty_results_array(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "director_screenings": [{
                "person_name": "D1", "person_type": "director",
                "nationality": "", "declared_pep": "No",
                "screening": {"matched": False, "results": [],
                              "source": "sumsub", "api_status": "live"},
            }],
            "ubo_screenings": [],
        }
        assert_roundtrip(raw)

    def test_extra_keys_preserved(self):
        """Keys unknown to the normalizer must survive round-trip."""
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "director_screenings": [],
            "ubo_screenings": [],
            "custom_key": "custom_value",
        }
        assert_roundtrip(raw)
