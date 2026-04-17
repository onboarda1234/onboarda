"""
Golden-File Round-Trip Tests — SCR-007
=======================================
Five+ realistic fixture shapes based on the actual screening output
of ``run_full_screening()``.  Each tests the critical invariant:

    ``denormalize_to_legacy(normalize_screening_report(raw)) == raw``
"""

import copy
import pytest
from screening_normalizer import normalize_screening_report, denormalize_to_legacy


# ── Golden-file fixtures ──

GOLDEN_1_CLEAN_APPLICATION = {
    "screened_at": "2025-03-15T10:30:00",
    "company_screening": {
        "found": True,
        "companies": [{
            "name": "Acme Ltd",
            "company_number": "C12345",
            "jurisdiction": "mu",
            "incorporation_date": "2020-01-15",
            "company_type": "Private Limited",
            "registry_url": "https://opencorporates.com/companies/mu/C12345",
            "status": "Active",
        }],
        "total_results": 1,
        "source": "opencorporates",
        "api_status": "live",
        "searched_at": "2025-03-15T10:30:01",
        "sanctions": {
            "matched": False,
            "results": [],
            "source": "sumsub",
            "api_status": "live",
            "screened_at": "2025-03-15T10:30:02",
        },
    },
    "director_screenings": [
        {
            "person_name": "Alice Johnson",
            "person_type": "director",
            "nationality": "MU",
            "declared_pep": "No",
            "screening": {
                "matched": False,
                "results": [],
                "source": "sumsub",
                "api_status": "live",
                "screened_at": "2025-03-15T10:30:03",
            },
        },
        {
            "person_name": "Bob Williams",
            "person_type": "director",
            "nationality": "GB",
            "declared_pep": "No",
            "screening": {
                "matched": False,
                "results": [],
                "source": "sumsub",
                "api_status": "live",
                "screened_at": "2025-03-15T10:30:04",
            },
        },
    ],
    "ubo_screenings": [
        {
            "person_name": "Charlie Brown",
            "person_type": "ubo",
            "nationality": "FR",
            "ownership_pct": 60,
            "declared_pep": "No",
            "screening": {
                "matched": False,
                "results": [],
                "source": "sumsub",
                "api_status": "live",
                "screened_at": "2025-03-15T10:30:05",
            },
        },
    ],
    "ip_geolocation": {
        "ip": "196.192.1.1",
        "country": "MU",
        "country_name": "Mauritius",
        "source": "ipapi",
        "api_status": "live",
        "risk_level": "LOW",
        "is_vpn": False,
        "is_proxy": False,
        "is_tor": False,
    },
    "overall_flags": [],
    "total_hits": 0,
    "degraded_sources": [],
    "kyc_applicants": [
        {
            "person_name": "Alice Johnson",
            "person_type": "director",
            "applicant_id": "sumsub_app_001",
            "api_status": "live",
        },
        {
            "person_name": "Bob Williams",
            "person_type": "director",
            "applicant_id": "sumsub_app_002",
            "api_status": "live",
        },
        {
            "person_name": "Charlie Brown",
            "person_type": "ubo",
            "applicant_id": "sumsub_app_003",
            "api_status": "live",
        },
    ],
    "screening_mode": "live",
}


GOLDEN_2_PEP_HIT_WITH_UNDECLARED = {
    "screened_at": "2025-03-16T14:00:00",
    "company_screening": {
        "found": True,
        "companies": [{"name": "PEP Holdings"}],
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
            "person_name": "Politically Exposed Director",
            "person_type": "director",
            "nationality": "NG",
            "declared_pep": "No",
            "undeclared_pep": True,
            "screening": {
                "matched": True,
                "results": [{
                    "match_score": 87.5,
                    "matched_name": "Politically Exposed Director",
                    "datasets": ["AML"],
                    "schema": "Person",
                    "topics": ["pep"],
                    "countries": ["NG"],
                    "sanctions_list": "",
                    "is_pep": True,
                    "is_sanctioned": False,
                }],
                "source": "sumsub",
                "api_status": "live",
                "screened_at": "2025-03-16T14:00:01",
            },
        },
    ],
    "ubo_screenings": [],
    "overall_flags": [
        "Director 'Politically Exposed Director' has sanctions/PEP matches",
        "Director 'Politically Exposed Director' may be undeclared PEP",
    ],
    "total_hits": 1,
    "degraded_sources": [],
    "screening_mode": "live",
}


GOLDEN_3_DEGRADED_SOURCES = {
    "screened_at": "2025-03-17T09:15:00",
    "company_screening": {
        "found": False,
        "source": "unavailable",
        "degraded": True,
    },
    "director_screenings": [
        {
            "person_name": "Dave Error",
            "person_type": "director",
            "nationality": "",
            "declared_pep": "No",
            "screening": {
                "matched": False,
                "results": [],
                "source": "sumsub",
                "api_status": "error",
                "error": "Sumsub AML screening failed: Connection timeout",
                "screened_at": "2025-03-17T09:15:01",
            },
        },
    ],
    "ubo_screenings": [],
    "ip_geolocation": {
        "source": "unavailable",
        "degraded": True,
    },
    "overall_flags": [
        "Company registry lookup unavailable: Connection timeout",
        "Director 'Dave Error' screening unavailable: error",
    ],
    "total_hits": 0,
    "degraded_sources": [
        "opencorporates",
        "director_screening:Dave Error",
        "ip_geolocation",
    ],
    "screening_mode": "unknown",
}


GOLDEN_4_SIMULATED_MODE = {
    "screened_at": "2025-03-18T11:00:00",
    "company_screening": {
        "found": True,
        "companies": [{"name": "SimCo"}],
        "source": "simulated",
        "api_status": "simulated",
        "searched_at": "2025-03-18T11:00:01",
        "sanctions": {
            "matched": False,
            "results": [],
            "source": "simulated",
            "api_status": "simulated",
        },
    },
    "director_screenings": [
        {
            "person_name": "Sim Director",
            "person_type": "director",
            "nationality": "MU",
            "declared_pep": "No",
            "screening": {
                "matched": False,
                "results": [],
                "source": "simulated",
                "api_status": "simulated",
                "note": "No Sumsub credentials configured — simulated result",
                "screened_at": "2025-03-18T11:00:02",
            },
        },
    ],
    "ubo_screenings": [
        {
            "person_name": "Sim UBO",
            "person_type": "ubo",
            "nationality": "SG",
            "ownership_pct": 100,
            "declared_pep": "No",
            "screening": {
                "matched": True,
                "results": [{
                    "match_score": 72.3,
                    "matched_name": "Sim UBO",
                    "datasets": ["aml-simulated"],
                    "schema": "Person",
                    "topics": ["pep"],
                    "countries": [],
                    "sanctions_list": "Simulated AML List",
                    "is_pep": True,
                    "is_sanctioned": False,
                }],
                "source": "simulated",
                "api_status": "simulated",
                "note": "No Sumsub credentials configured — simulated result",
                "screened_at": "2025-03-18T11:00:03",
            },
        },
    ],
    "ip_geolocation": {
        "ip": "127.0.0.1",
        "source": "simulated",
        "api_status": "simulated",
    },
    "overall_flags": ["UBO 'Sim UBO' has sanctions/PEP matches"],
    "total_hits": 1,
    "degraded_sources": [],
    "screening_mode": "simulated",
}


GOLDEN_5_FULL_PRODUCTION_MULTI_HIT = {
    "screened_at": "2025-03-19T16:45:00",
    "company_screening": {
        "found": True,
        "companies": [{
            "name": "HighRisk Corp",
            "company_number": "HR999",
            "jurisdiction": "ky",
            "status": "Active",
        }],
        "total_results": 1,
        "source": "opencorporates",
        "api_status": "live",
        "sanctions": {
            "matched": True,
            "results": [{
                "match_score": 95.0,
                "matched_name": "HighRisk Corp",
                "datasets": ["OFAC"],
                "is_sanctioned": True,
            }],
            "source": "sumsub",
            "api_status": "live",
            "screened_at": "2025-03-19T16:45:01",
        },
    },
    "director_screenings": [
        {
            "person_name": "Director Alpha",
            "person_type": "director",
            "nationality": "US",
            "declared_pep": "Yes",
            "screening": {
                "matched": True,
                "results": [{
                    "match_score": 91.0,
                    "matched_name": "Director Alpha",
                    "datasets": ["AML"],
                    "schema": "Person",
                    "topics": ["pep"],
                    "countries": ["US"],
                    "sanctions_list": "",
                    "is_pep": True,
                    "is_sanctioned": False,
                }],
                "source": "sumsub",
                "api_status": "live",
                "screened_at": "2025-03-19T16:45:02",
            },
        },
        {
            "person_name": "Director Beta",
            "person_type": "director",
            "nationality": "DE",
            "declared_pep": "No",
            "screening": {
                "matched": False,
                "results": [],
                "source": "sumsub",
                "api_status": "live",
                "screened_at": "2025-03-19T16:45:03",
            },
        },
    ],
    "ubo_screenings": [
        {
            "person_name": "UBO Gamma",
            "person_type": "ubo",
            "nationality": "RU",
            "ownership_pct": 80,
            "declared_pep": "No",
            "undeclared_pep": True,
            "screening": {
                "matched": True,
                "results": [
                    {
                        "match_score": 88.0,
                        "matched_name": "UBO Gamma",
                        "datasets": ["sanctions"],
                        "schema": "Person",
                        "topics": ["sanction"],
                        "countries": ["RU"],
                        "sanctions_list": "EU Consolidated",
                        "is_pep": False,
                        "is_sanctioned": True,
                    },
                    {
                        "match_score": 76.0,
                        "matched_name": "UBO Gamma",
                        "datasets": ["AML"],
                        "schema": "Person",
                        "topics": ["pep"],
                        "countries": ["RU"],
                        "sanctions_list": "",
                        "is_pep": True,
                        "is_sanctioned": False,
                    },
                ],
                "source": "sumsub",
                "api_status": "live",
                "screened_at": "2025-03-19T16:45:04",
            },
        },
    ],
    "ip_geolocation": {
        "ip": "203.0.113.1",
        "country": "RU",
        "country_name": "Russia",
        "source": "ipapi",
        "api_status": "live",
        "risk_level": "VERY_HIGH",
        "is_vpn": True,
        "is_proxy": False,
        "is_tor": False,
    },
    "overall_flags": [
        "Company 'HighRisk Corp' has sanctions/watchlist matches",
        "Director 'Director Alpha' has sanctions/PEP matches",
        "UBO 'UBO Gamma' has sanctions/PEP matches",
        "UBO 'UBO Gamma' may be undeclared PEP",
        "Client IP geolocated to high-risk jurisdiction: RU",
        "Client IP detected as VPN",
    ],
    "total_hits": 4,
    "degraded_sources": [],
    "kyc_applicants": [
        {"person_name": "Director Alpha", "person_type": "director",
         "applicant_id": "sa_001", "api_status": "live"},
        {"person_name": "Director Beta", "person_type": "director",
         "applicant_id": "sa_002", "api_status": "live"},
        {"person_name": "UBO Gamma", "person_type": "ubo",
         "applicant_id": "sa_003", "api_status": "live"},
    ],
    "screening_mode": "live",
}


GOLDEN_6_COMPANY_NOT_FOUND = {
    "screened_at": "2025-03-20T08:00:00",
    "company_screening": {
        "found": False,
        "companies": [],
        "total_results": 0,
        "source": "opencorporates",
        "api_status": "live",
        "searched_at": "2025-03-20T08:00:01",
        "sanctions": {
            "matched": False,
            "results": [],
            "source": "sumsub",
            "api_status": "not_configured",
            "reason": "Sumsub company KYB level not configured",
        },
    },
    "director_screenings": [],
    "ubo_screenings": [],
    "ip_geolocation": {},
    "overall_flags": ["Company 'Unknown Corp' not found in corporate registry"],
    "total_hits": 0,
    "degraded_sources": [],
}


# ── Parametrised golden-file round-trip ──

ALL_GOLDEN_FILES = [
    ("clean_application", GOLDEN_1_CLEAN_APPLICATION),
    ("pep_hit_undeclared", GOLDEN_2_PEP_HIT_WITH_UNDECLARED),
    ("degraded_sources", GOLDEN_3_DEGRADED_SOURCES),
    ("simulated_mode", GOLDEN_4_SIMULATED_MODE),
    ("full_production_multi_hit", GOLDEN_5_FULL_PRODUCTION_MULTI_HIT),
    ("company_not_found", GOLDEN_6_COMPANY_NOT_FOUND),
]


@pytest.mark.parametrize("name,golden", ALL_GOLDEN_FILES, ids=[g[0] for g in ALL_GOLDEN_FILES])
def test_golden_roundtrip(name, golden):
    """denormalize(normalize(golden)) must equal the original."""
    original = copy.deepcopy(golden)
    normalized = normalize_screening_report(golden)
    legacy = denormalize_to_legacy(normalized)
    assert legacy == original, f"Golden file '{name}' round-trip failed"


@pytest.mark.parametrize("name,golden", ALL_GOLDEN_FILES, ids=[g[0] for g in ALL_GOLDEN_FILES])
def test_golden_normalized_has_metadata(name, golden):
    """Normalized output must contain provider metadata."""
    normalized = normalize_screening_report(golden)
    assert normalized["provider"] == "sumsub"
    assert normalized["normalized_version"] == "1.0"
    assert isinstance(normalized["any_pep_hits"], bool)
    assert isinstance(normalized["any_sanctions_hits"], bool)
    assert isinstance(normalized["total_persons_screened"], int)


@pytest.mark.parametrize("name,golden", ALL_GOLDEN_FILES, ids=[g[0] for g in ALL_GOLDEN_FILES])
def test_golden_normalize_does_not_mutate_input(name, golden):
    """normalize_screening_report must not mutate the original dict."""
    original = copy.deepcopy(golden)
    normalize_screening_report(golden)
    assert golden == original, f"Golden file '{name}' was mutated by normalization"


class TestGoldenFileSummaries:
    """Validate the normalizer computes correct summary flags."""

    def test_clean_has_no_hits(self):
        n = normalize_screening_report(GOLDEN_1_CLEAN_APPLICATION)
        assert n["any_pep_hits"] is False
        assert n["any_sanctions_hits"] is False
        assert n["total_persons_screened"] == 3

    def test_pep_undeclared_has_pep_hit(self):
        n = normalize_screening_report(GOLDEN_2_PEP_HIT_WITH_UNDECLARED)
        assert n["any_pep_hits"] is True
        assert n["any_sanctions_hits"] is False
        assert n["total_persons_screened"] == 1

    def test_degraded_no_hits(self):
        n = normalize_screening_report(GOLDEN_3_DEGRADED_SOURCES)
        assert n["any_pep_hits"] is False
        assert n["total_persons_screened"] == 1

    def test_simulated_with_pep(self):
        n = normalize_screening_report(GOLDEN_4_SIMULATED_MODE)
        assert n["any_pep_hits"] is True
        assert n["total_persons_screened"] == 2

    def test_multi_hit_production(self):
        n = normalize_screening_report(GOLDEN_5_FULL_PRODUCTION_MULTI_HIT)
        assert n["any_pep_hits"] is True
        assert n["any_sanctions_hits"] is True
        assert n["total_persons_screened"] == 3

    def test_company_not_found_no_hits(self):
        n = normalize_screening_report(GOLDEN_6_COMPANY_NOT_FOUND)
        assert n["any_pep_hits"] is False
        assert n["any_sanctions_hits"] is False
        assert n["total_persons_screened"] == 0
