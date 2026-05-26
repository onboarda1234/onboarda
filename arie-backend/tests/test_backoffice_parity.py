"""
Backoffice Rendering Parity Test — SCR-009
==========================================
Simulates backoffice JS data-access patterns against both:
1. Raw legacy screening_report
2. denormalize_to_legacy(normalize_screening_report(raw))

Verifies field-level parity for all screening badge and display inputs.
"""

import copy
import json
import pytest

from screening_normalizer import normalize_screening_report, denormalize_to_legacy


def _make_full_report():
    """Realistic full screening report matching backoffice expectations."""
    return {
        "screened_at": "2025-06-15T14:30:00",
        "screening_mode": "live",
        "company_screening": {
            "found": True,
            "source": "opencorporates",
            "api_status": "live",
            "sanctions": {
                "matched": False,
                "results": [],
                "source": "sumsub",
                "api_status": "live",
            },
        },
        "director_screenings": [
            {
                "person_name": "John Smith",
                "person_type": "director",
                "nationality": "GB",
                "declared_pep": "No",
                "screening": {
                    "matched": True,
                    "results": [
                        {"is_pep": True, "is_sanctioned": False, "name": "JOHN SMITH", "score": 0.95},
                    ],
                    "source": "sumsub",
                    "api_status": "live",
                },
                "undeclared_pep": True,
            },
            {
                "person_name": "Jane Director",
                "person_type": "director",
                "nationality": "MU",
                "declared_pep": "Yes",
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
                "person_name": "Bob UBO",
                "person_type": "ubo",
                "nationality": "SG",
                "declared_pep": "No",
                "ownership_pct": 51,
                "screening": {
                    "matched": True,
                    "results": [
                        {"is_pep": False, "is_sanctioned": True, "name": "BOB UBO", "score": 0.88},
                    ],
                    "source": "sumsub",
                    "api_status": "live",
                },
                "undeclared_pep": False,
            },
        ],
        "ip_geolocation": {
            "source": "ipapi",
            "api_status": "live",
            "risk_level": "LOW",
            "country": "Mauritius",
        },
        "kyc_applicants": [
            {"person_name": "John Smith", "source": "sumsub", "api_status": "live", "review_answer": "RED"},
        ],
        "overall_flags": [
            "Director 'John Smith' has sanctions/PEP matches",
            "Director 'John Smith' may be undeclared PEP",
        ],
        "total_hits": 2,
        "degraded_sources": [],
    }


def _extract_backoffice_fields(report: dict) -> dict:
    """
    Simulate backoffice JS extraction patterns.
    Mirrors the actual JS code in arie-backoffice.html.
    """
    sr = report

    # director_screenings[].undeclared_pep
    director_undeclared_peps = [
        s.get("undeclared_pep") for s in sr.get("director_screenings", [])
    ]

    # ubo_screenings[].undeclared_pep
    ubo_undeclared_peps = [
        s.get("undeclared_pep") for s in sr.get("ubo_screenings", [])
    ]

    # Screening PEP count (backoffice lines 3086-3087, 4988-4989)
    screening_peps_count = (
        len([s for s in sr.get("director_screenings", []) if s.get("undeclared_pep")])
        + len([s for s in sr.get("ubo_screenings", []) if s.get("undeclared_pep")])
    )

    # All screening results: is_pep, is_sanctioned (lines 5383-5384)
    all_screenings = sr.get("director_screenings", []) + sr.get("ubo_screenings", [])
    all_results_is_pep = []
    all_results_is_sanctioned = []
    for s in all_screenings:
        for hit in s.get("screening", {}).get("results", []):
            all_results_is_pep.append(hit.get("is_pep"))
            all_results_is_sanctioned.append(hit.get("is_sanctioned"))

    # api_status fields
    company_api_status = sr.get("company_screening", {}).get("api_status")
    company_sanctions_api_status = sr.get("company_screening", {}).get("sanctions", {}).get("api_status")
    ip_api_status = sr.get("ip_geolocation", {}).get("api_status")

    director_api_statuses = [
        s.get("screening", {}).get("api_status")
        for s in sr.get("director_screenings", [])
    ]
    ubo_api_statuses = [
        s.get("screening", {}).get("api_status")
        for s in sr.get("ubo_screenings", [])
    ]

    # person_name (lines 5464)
    person_names = [s.get("person_name") for s in all_screenings]

    # screened_at (line 5414)
    screened_at = sr.get("screened_at")

    # total_hits (line 5443)
    total_hits = sr.get("total_hits")

    # overall_flags (line 5442)
    overall_flags = sr.get("overall_flags", [])

    # screening_mode (line 4827)
    screening_mode = sr.get("screening_mode")

    # Per-person screening badge inputs (lines 5471-5486)
    badge_inputs = []
    for s in all_screenings:
        screening = s.get("screening", {})
        results = screening.get("results", [])
        badge = {
            "person_name": s.get("person_name"),
            "undeclared_pep": s.get("undeclared_pep"),
            "screening_api_status": screening.get("api_status"),
            "screening_source": screening.get("source"),
            "matched": screening.get("matched"),
            "result_count": len(results),
            "results_is_pep": [r.get("is_pep") for r in results],
            "results_is_sanctioned": [r.get("is_sanctioned") for r in results],
        }
        badge_inputs.append(badge)

    return {
        "director_undeclared_peps": director_undeclared_peps,
        "ubo_undeclared_peps": ubo_undeclared_peps,
        "screening_peps_count": screening_peps_count,
        "all_results_is_pep": all_results_is_pep,
        "all_results_is_sanctioned": all_results_is_sanctioned,
        "company_api_status": company_api_status,
        "company_sanctions_api_status": company_sanctions_api_status,
        "ip_api_status": ip_api_status,
        "director_api_statuses": director_api_statuses,
        "ubo_api_statuses": ubo_api_statuses,
        "person_names": person_names,
        "screened_at": screened_at,
        "total_hits": total_hits,
        "overall_flags": overall_flags,
        "screening_mode": screening_mode,
        "badge_inputs": badge_inputs,
    }


class TestBackofficeParity:
    """Extraction from raw vs round-tripped reports must produce identical results."""

    def _assert_parity(self, raw):
        """Compare extraction from raw vs round-tripped report."""
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)

        raw_fields = _extract_backoffice_fields(raw)
        legacy_fields = _extract_backoffice_fields(legacy)

        for key in raw_fields:
            assert raw_fields[key] == legacy_fields[key], (
                f"Parity mismatch for '{key}':\n"
                f"  raw:     {raw_fields[key]}\n"
                f"  legacy:  {legacy_fields[key]}"
            )

    def test_full_report_parity(self):
        self._assert_parity(_make_full_report())

    def test_empty_screenings_parity(self):
        report = _make_full_report()
        report["director_screenings"] = []
        report["ubo_screenings"] = []
        self._assert_parity(report)

    def test_degraded_sources_parity(self):
        report = _make_full_report()
        report["company_screening"] = {"found": False, "source": "unavailable", "degraded": True}
        report["director_screenings"][0]["screening"] = {
            "matched": False, "results": [], "source": "unavailable", "degraded": True,
        }
        report["degraded_sources"] = ["opencorporates", "director_screening:John Smith"]
        self._assert_parity(report)

    def test_sanctions_hit_parity(self):
        report = _make_full_report()
        report["company_screening"]["sanctions"]["matched"] = True
        report["company_screening"]["sanctions"]["results"] = [{"name": "Bad Corp"}]
        self._assert_parity(report)

    def test_no_undeclared_pep_parity(self):
        report = _make_full_report()
        del report["director_screenings"][0]["undeclared_pep"]
        del report["ubo_screenings"][0]["undeclared_pep"]
        self._assert_parity(report)

    def test_simulated_mode_parity(self):
        report = _make_full_report()
        report["screening_mode"] = "simulated"
        report["director_screenings"][0]["screening"]["api_status"] = "simulated"
        report["director_screenings"][0]["screening"]["source"] = "simulated"
        self._assert_parity(report)

    def test_multiple_hits_parity(self):
        report = _make_full_report()
        report["director_screenings"][0]["screening"]["results"] = [
            {"is_pep": True, "is_sanctioned": False, "name": "Hit 1"},
            {"is_pep": False, "is_sanctioned": True, "name": "Hit 2"},
            {"is_pep": True, "is_sanctioned": True, "name": "Hit 3"},
        ]
        report["total_hits"] = 4
        self._assert_parity(report)

    def test_no_ip_geolocation_parity(self):
        report = _make_full_report()
        del report["ip_geolocation"]
        self._assert_parity(report)

    def test_float_ownership_parity(self):
        report = _make_full_report()
        report["ubo_screenings"][0]["ownership_pct"] = 33.33
        self._assert_parity(report)


class TestFieldLevelParity:
    """Test specific field extractions match exactly."""

    def test_undeclared_pep_extraction(self):
        report = _make_full_report()
        raw_fields = _extract_backoffice_fields(report)
        assert raw_fields["director_undeclared_peps"] == [True, None]
        assert raw_fields["ubo_undeclared_peps"] == [False]

    def test_pep_count(self):
        report = _make_full_report()
        raw_fields = _extract_backoffice_fields(report)
        assert raw_fields["screening_peps_count"] == 1  # Only John Smith has undeclared_pep=True

    def test_screened_at(self):
        report = _make_full_report()
        raw_fields = _extract_backoffice_fields(report)
        assert raw_fields["screened_at"] == "2025-06-15T14:30:00"

    def test_total_hits(self):
        report = _make_full_report()
        raw_fields = _extract_backoffice_fields(report)
        assert raw_fields["total_hits"] == 2

    def test_person_names_ordered(self):
        report = _make_full_report()
        raw_fields = _extract_backoffice_fields(report)
        assert raw_fields["person_names"] == ["John Smith", "Jane Director", "Bob UBO"]

    def test_badge_inputs_count(self):
        report = _make_full_report()
        raw_fields = _extract_backoffice_fields(report)
        assert len(raw_fields["badge_inputs"]) == 3  # 2 directors + 1 UBO
