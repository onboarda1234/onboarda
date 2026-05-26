"""
Tests for screening_normalizer.py — Normalizer and denormalizer.
"""

import copy
import pytest

from screening_normalizer import (
    normalize_screening_report,
    denormalize_to_legacy,
    AlreadyNormalizedError,
)


def _make_raw_report(**overrides):
    """Create a realistic raw screening report."""
    report = {
        "screened_at": "2025-06-15T14:30:00",
        "company_screening": {
            "found": True,
            "source": "opencorporates",
            "api_status": "live",
            "sanctions": {"matched": False, "results": [], "source": "sumsub", "api_status": "live"},
        },
        "director_screenings": [
            {
                "person_name": "John Smith",
                "person_type": "director",
                "nationality": "GB",
                "declared_pep": "No",
                "screening": {
                    "matched": False,
                    "results": [],
                    "source": "sumsub",
                    "api_status": "live",
                },
            },
        ],
        "ubo_screenings": [
            {
                "person_name": "Jane Doe",
                "person_type": "ubo",
                "nationality": "MU",
                "declared_pep": "No",
                "ownership_pct": 30,
                "screening": {
                    "matched": False,
                    "results": [],
                    "source": "sumsub",
                    "api_status": "live",
                },
            },
        ],
        "ip_geolocation": {"source": "ipapi", "api_status": "live", "risk_level": "LOW"},
        "kyc_applicants": [
            {"person_name": "John Smith", "source": "sumsub", "api_status": "live", "review_answer": "GREEN"},
        ],
        "overall_flags": [],
        "total_hits": 0,
        "degraded_sources": [],
    }
    report.update(overrides)
    return report


class TestNormalize:
    def test_adds_provider(self):
        raw = _make_raw_report()
        normalized = normalize_screening_report(raw)
        assert normalized["provider"] == "sumsub"

    def test_adds_normalized_version(self):
        raw = _make_raw_report()
        normalized = normalize_screening_report(raw)
        assert normalized["normalized_version"] == "1.0"

    def test_adds_person_hit_summaries(self):
        raw = _make_raw_report()
        normalized = normalize_screening_report(raw)
        d = normalized["director_screenings"][0]
        assert d["has_pep_hit"] is False
        assert d["has_sanctions_hit"] is False
        assert d["has_adverse_media_hit"] is None
        assert d["adverse_media_coverage"] == "none"

    def test_adds_report_level_summaries(self):
        raw = _make_raw_report()
        normalized = normalize_screening_report(raw)
        assert normalized["any_pep_hits"] is False
        assert normalized["any_sanctions_hits"] is False
        assert normalized["total_persons_screened"] == 2  # 1 director + 1 ubo

    def test_adverse_media_coverage_none(self):
        raw = _make_raw_report()
        normalized = normalize_screening_report(raw)
        assert normalized["adverse_media_coverage"] == "none"

    def test_company_screening_coverage_partial(self):
        raw = _make_raw_report()
        normalized = normalize_screening_report(raw)
        assert normalized["company_screening_coverage"] == "partial"
        assert normalized["has_company_screening_hit"] is False

    def test_preserves_all_original_fields(self):
        raw = _make_raw_report()
        normalized = normalize_screening_report(raw)
        assert normalized["screened_at"] == raw["screened_at"]
        assert normalized["total_hits"] == raw["total_hits"]
        assert normalized["ip_geolocation"] == raw["ip_geolocation"]
        assert normalized["kyc_applicants"] == raw["kyc_applicants"]

    def test_does_not_mutate_input(self):
        raw = _make_raw_report()
        raw_copy = copy.deepcopy(raw)
        normalize_screening_report(raw)
        assert raw == raw_copy

    def test_pep_hit_detected(self):
        raw = _make_raw_report()
        raw["director_screenings"][0]["screening"] = {
            "matched": True,
            "results": [{"is_pep": True, "is_sanctioned": False, "name": "John Smith PEP"}],
            "source": "sumsub",
        }
        normalized = normalize_screening_report(raw)
        assert normalized["director_screenings"][0]["has_pep_hit"] is True
        assert normalized["any_pep_hits"] is True

    def test_sanctions_hit_detected(self):
        raw = _make_raw_report()
        raw["ubo_screenings"][0]["screening"] = {
            "matched": True,
            "results": [{"is_pep": False, "is_sanctioned": True, "name": "Jane Doe"}],
            "source": "sumsub",
        }
        normalized = normalize_screening_report(raw)
        assert normalized["ubo_screenings"][0]["has_sanctions_hit"] is True
        assert normalized["any_sanctions_hits"] is True

    def test_company_sanctions_hit(self):
        raw = _make_raw_report()
        raw["company_screening"]["sanctions"] = {
            "matched": True,
            "results": [{"name": "Bad Corp"}],
            "source": "sumsub",
        }
        normalized = normalize_screening_report(raw)
        assert normalized["has_company_screening_hit"] is True


class TestDenormalize:
    def test_strips_metadata(self):
        raw = _make_raw_report()
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        assert "provider" not in legacy
        assert "normalized_version" not in legacy
        assert "any_pep_hits" not in legacy
        assert "any_sanctions_hits" not in legacy
        assert "total_persons_screened" not in legacy

    def test_strips_person_metadata(self):
        raw = _make_raw_report()
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        for d in legacy["director_screenings"]:
            assert "has_pep_hit" not in d
            assert "has_sanctions_hit" not in d
            assert "has_adverse_media_hit" not in d
            assert "adverse_media_coverage" not in d

    def test_non_dict_raises(self):
        with pytest.raises(TypeError):
            denormalize_to_legacy("not a dict")


class TestRoundTrip:
    """INVARIANT: denormalize_to_legacy(normalize_screening_report(raw)) == raw"""

    def test_basic_roundtrip(self):
        raw = _make_raw_report()
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        assert legacy == raw

    def test_roundtrip_with_hits(self):
        raw = _make_raw_report()
        raw["director_screenings"][0]["screening"] = {
            "matched": True,
            "results": [{"is_pep": True, "is_sanctioned": False}],
            "source": "sumsub",
        }
        raw["total_hits"] = 1
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        assert legacy == raw

    def test_roundtrip_empty_directors(self):
        raw = _make_raw_report(director_screenings=[])
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        assert legacy == raw

    def test_roundtrip_empty_ubos(self):
        raw = _make_raw_report(ubo_screenings=[])
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        assert legacy == raw

    def test_roundtrip_with_undeclared_pep(self):
        raw = _make_raw_report()
        raw["director_screenings"][0]["undeclared_pep"] = True
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        assert legacy == raw

    def test_roundtrip_with_degraded_sources(self):
        raw = _make_raw_report(degraded_sources=["opencorporates", "sumsub_company_sanctions"])
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        assert legacy == raw

    def test_roundtrip_preserves_screening_mode(self):
        raw = _make_raw_report()
        raw["screening_mode"] = "live"
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        assert legacy == raw


class TestDoubleNormalization:
    def test_raises_already_normalized(self):
        raw = _make_raw_report()
        normalized = normalize_screening_report(raw)
        with pytest.raises(AlreadyNormalizedError):
            normalize_screening_report(normalized)

    def test_non_dict_raises(self):
        with pytest.raises(TypeError):
            normalize_screening_report("not a dict")


class TestEdgeCases:
    def test_empty_screening_results(self):
        raw = _make_raw_report()
        raw["director_screenings"][0]["screening"] = {
            "matched": False, "results": [], "source": "sumsub", "api_status": "live",
        }
        normalized = normalize_screening_report(raw)
        # Priority A: terminal-clear (api_status=live) yields explicit False.
        assert normalized["director_screenings"][0]["has_pep_hit"] is False
        assert normalized["director_screenings"][0]["screening_state"] == "completed_clear"

    def test_pending_provider_state_is_not_clear(self):
        # Priority A: api_status=pending must NOT yield has_pep_hit=False.
        # Otherwise officers would see "Clear" before a real screening
        # answer is in.
        raw = _make_raw_report()
        raw["director_screenings"][0]["screening"] = {
            "matched": False, "results": [], "source": "sumsub", "api_status": "pending",
        }
        normalized = normalize_screening_report(raw)
        d = normalized["director_screenings"][0]
        assert d["has_pep_hit"] is None
        assert d["has_sanctions_hit"] is None
        assert d["screening_state"] == "pending_provider"
        assert normalized["any_non_terminal_subject"] is True

    def test_not_configured_state_is_explicit(self):
        raw = _make_raw_report()
        raw["company_screening"]["sanctions"] = {
            "matched": False, "results": [], "source": "sumsub", "api_status": "not_configured",
        }
        normalized = normalize_screening_report(raw)
        # Priority A: not_configured is preserved, never flattened to False.
        assert normalized["has_company_screening_hit"] is None
        assert normalized["company_screening_state"] == "not_configured"

    def test_failed_provider_state_is_explicit(self):
        raw = _make_raw_report()
        raw["ubo_screenings"][0]["screening"] = {
            "matched": False, "results": [], "source": "sumsub", "api_status": "error",
        }
        normalized = normalize_screening_report(raw)
        u = normalized["ubo_screenings"][0]
        assert u["has_pep_hit"] is None
        assert u["has_sanctions_hit"] is None
        assert u["screening_state"] == "failed"

    def test_declared_pep_preserved_through_pending(self):
        # Priority A: declared PEP is a self-declared signal that must
        # remain visible even when the provider screening has not produced
        # a terminal answer.
        raw = _make_raw_report()
        raw["director_screenings"][0]["declared_pep"] = "Yes"
        raw["director_screenings"][0]["screening"] = {
            "matched": False, "results": [], "source": "sumsub", "api_status": "pending",
        }
        normalized = normalize_screening_report(raw)
        d = normalized["director_screenings"][0]
        assert d["declared_pep"] == "Yes"
        assert d["screening_state"] == "pending_provider"
        assert d["requires_review"] is True

    def test_no_company_screening(self):
        raw = _make_raw_report(company_screening={})
        normalized = normalize_screening_report(raw)
        assert normalized["company_screening_coverage"] == "none"
        assert normalized["has_company_screening_hit"] is None

    def test_float_preservation(self):
        raw = _make_raw_report()
        raw["ubo_screenings"][0]["ownership_pct"] = 33.33
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        assert legacy["ubo_screenings"][0]["ownership_pct"] == 33.33

    def test_timestamp_preservation(self):
        raw = _make_raw_report(screened_at="2025-06-15T14:30:45.123456")
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        assert legacy["screened_at"] == "2025-06-15T14:30:45.123456"

    def test_list_order_preservation(self):
        raw = _make_raw_report()
        raw["director_screenings"] = [
            {"person_name": "Zoe", "screening": {"matched": False, "results": []}},
            {"person_name": "Alice", "screening": {"matched": False, "results": []}},
            {"person_name": "Bob", "screening": {"matched": False, "results": []}},
        ]
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        assert [d["person_name"] for d in legacy["director_screenings"]] == ["Zoe", "Alice", "Bob"]
