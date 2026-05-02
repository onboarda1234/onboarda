"""
Round-trip tests for screening normalizer — SCR-007.

Core invariant:
    denormalize_to_legacy(normalize_screening_report(raw)) == raw

Tests use:
1. Golden fixtures from tests/fixtures/screening/golden_reports.json
2. Deterministic randomized fixture generator (100+ varied valid reports)
"""

import copy
import json
import os
import random
import pytest

from screening_normalizer import (
    normalize_screening_report,
    denormalize_to_legacy,
    AlreadyNormalizedError,
)
from screening_models import validate_normalized_report


# ── Golden Fixtures ──

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "screening")
GOLDEN_FILE = os.path.join(FIXTURES_DIR, "golden_reports.json")

with open(GOLDEN_FILE, encoding="utf-8") as f:
    GOLDEN_FIXTURES = json.load(f)


class TestGoldenFixtureRoundTrip:
    """Each golden fixture must survive normalize → denormalize unchanged."""

    @pytest.mark.parametrize(
        "fixture",
        GOLDEN_FIXTURES,
        ids=[f["name"] for f in GOLDEN_FIXTURES],
    )
    def test_roundtrip(self, fixture):
        raw = fixture["report"]
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        assert legacy == raw, f"Round-trip failed for fixture: {fixture['name']}"

    @pytest.mark.parametrize(
        "fixture",
        GOLDEN_FIXTURES,
        ids=[f["name"] for f in GOLDEN_FIXTURES],
    )
    def test_normalized_is_valid(self, fixture):
        raw = fixture["report"]
        normalized = normalize_screening_report(raw)
        errors = validate_normalized_report(normalized)
        assert errors == [], f"Validation failed for {fixture['name']}: {errors}"

    @pytest.mark.parametrize(
        "fixture",
        GOLDEN_FIXTURES,
        ids=[f["name"] for f in GOLDEN_FIXTURES],
    )
    def test_double_normalization_fails(self, fixture):
        raw = fixture["report"]
        normalized = normalize_screening_report(raw)
        with pytest.raises(AlreadyNormalizedError):
            normalize_screening_report(normalized)


class TestListOrderPreservation:
    def test_director_order(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {},
            "director_screenings": [
                {"person_name": "Zoe", "screening": {"matched": False, "results": []}},
                {"person_name": "Alice", "screening": {"matched": False, "results": []}},
                {"person_name": "Moe", "screening": {"matched": False, "results": []}},
            ],
            "ubo_screenings": [],
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        names = [d["person_name"] for d in legacy["director_screenings"]]
        assert names == ["Zoe", "Alice", "Moe"]

    def test_flags_order(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {},
            "director_screenings": [],
            "ubo_screenings": [],
            "overall_flags": ["Flag C", "Flag A", "Flag B"],
            "total_hits": 0,
            "degraded_sources": ["src_b", "src_a"],
        }
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        assert legacy["overall_flags"] == ["Flag C", "Flag A", "Flag B"]
        assert legacy["degraded_sources"] == ["src_b", "src_a"]


class TestTimestampStringPreservation:
    def test_various_timestamp_formats(self):
        for ts in [
            "2025-06-15T14:30:00",
            "2025-06-15T14:30:45.123456",
            "2025-01-01T00:00:00Z",
            "2025-12-31T23:59:59+00:00",
        ]:
            raw = {
                "screened_at": ts,
                "company_screening": {},
                "director_screenings": [],
                "ubo_screenings": [],
                "overall_flags": [],
                "total_hits": 0,
                "degraded_sources": [],
            }
            normalized = normalize_screening_report(raw)
            legacy = denormalize_to_legacy(normalized)
            assert legacy["screened_at"] == ts


class TestFloatPreservation:
    def test_ownership_pct_float(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {},
            "director_screenings": [],
            "ubo_screenings": [
                {
                    "person_name": "Float UBO",
                    "ownership_pct": 33.33,
                    "screening": {"matched": False, "results": []},
                },
            ],
            "overall_flags": [],
            "total_hits": 0,
            "degraded_sources": [],
        }
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        assert legacy["ubo_screenings"][0]["ownership_pct"] == 33.33

    def test_score_float(self):
        raw = {
            "screened_at": "2025-01-01T00:00:00",
            "company_screening": {},
            "director_screenings": [
                {
                    "person_name": "Score Person",
                    "screening": {
                        "matched": True,
                        "results": [{"is_pep": True, "is_sanctioned": False, "score": 0.9512}],
                    },
                },
            ],
            "ubo_screenings": [],
            "overall_flags": [],
            "total_hits": 1,
            "degraded_sources": [],
        }
        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)
        assert legacy["director_screenings"][0]["screening"]["results"][0]["score"] == 0.9512


# ── Deterministic Randomized Testing ──

_NAMES = [
    "John Smith", "Jane Doe", "Alice Johnson", "Bob Williams", "Carol Davis",
    "David Brown", "Eve Wilson", "Frank Miller", "Grace Lee", "Hank Taylor",
]
_NATIONALITIES = ["GB", "MU", "FR", "SG", "US", "DE", "IN", "ZA", "HK", ""]
_SOURCES = ["sumsub", "mocked", "simulated", "unavailable"]
_API_STATUSES = ["live", "simulated", "mocked", "error"]


def _generate_random_report(seed: int) -> dict:
    """Generate a deterministic random screening report."""
    rng = random.Random(seed)

    n_directors = rng.randint(0, 5)
    n_ubos = rng.randint(0, 4)

    directors = []
    for i in range(n_directors):
        matched = rng.random() < 0.3
        results = []
        if matched:
            n_results = rng.randint(1, 3)
            for _ in range(n_results):
                results.append({
                    "is_pep": rng.random() < 0.5,
                    "is_sanctioned": rng.random() < 0.3,
                    "name": rng.choice(_NAMES),
                    "score": round(rng.random(), 4),
                })
        d = {
            "person_name": rng.choice(_NAMES),
            "person_type": "director",
            "nationality": rng.choice(_NATIONALITIES),
            "declared_pep": rng.choice(["Yes", "No"]),
            "screening": {
                "matched": matched,
                "results": results,
                "source": rng.choice(_SOURCES),
                "api_status": rng.choice(_API_STATUSES),
            },
        }
        if matched and any(r.get("is_pep") for r in results) and d["declared_pep"] == "No":
            d["undeclared_pep"] = True
        directors.append(d)

    ubos = []
    for i in range(n_ubos):
        matched = rng.random() < 0.2
        results = []
        if matched:
            for _ in range(rng.randint(1, 2)):
                results.append({
                    "is_pep": rng.random() < 0.4,
                    "is_sanctioned": rng.random() < 0.2,
                    "name": rng.choice(_NAMES),
                })
        u = {
            "person_name": rng.choice(_NAMES),
            "person_type": "ubo",
            "nationality": rng.choice(_NATIONALITIES),
            "declared_pep": rng.choice(["Yes", "No"]),
            "ownership_pct": rng.randint(5, 100),
            "screening": {
                "matched": matched,
                "results": results,
                "source": rng.choice(_SOURCES),
            },
        }
        ubos.append(u)

    total_hits = sum(
        len(d["screening"]["results"]) for d in directors if d["screening"]["matched"]
    ) + sum(
        len(u["screening"]["results"]) for u in ubos if u["screening"]["matched"]
    )

    company_found = rng.random() < 0.7
    sanctions_matched = rng.random() < 0.1

    report = {
        "screened_at": f"2025-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}T{rng.randint(0,23):02d}:{rng.randint(0,59):02d}:{rng.randint(0,59):02d}",
        "company_screening": {
            "found": company_found,
            "source": rng.choice(_SOURCES),
            "sanctions": {
                "matched": sanctions_matched,
                "results": [{"name": "Bad Corp"}] if sanctions_matched else [],
                "source": "sumsub",
            },
        },
        "director_screenings": directors,
        "ubo_screenings": ubos,
        "overall_flags": [f"Flag {i}" for i in range(rng.randint(0, 5))],
        "total_hits": total_hits,
        "degraded_sources": rng.sample(["opencorporates", "sumsub", "ipapi"], k=rng.randint(0, 2)),
    }

    # Optionally add ip_geolocation
    if rng.random() < 0.7:
        report["ip_geolocation"] = {
            "source": rng.choice(_SOURCES),
            "risk_level": rng.choice(["LOW", "MEDIUM", "HIGH"]),
        }

    # Optionally add screening_mode
    if rng.random() < 0.5:
        report["screening_mode"] = rng.choice(["live", "simulated", "unknown"])

    return report


class TestRandomizedRoundTrip:
    """Deterministic randomized round-trip tests with 120 varied reports."""

    @pytest.mark.parametrize("seed", range(120))
    def test_roundtrip(self, seed):
        raw = _generate_random_report(seed)
        raw_copy = copy.deepcopy(raw)

        normalized = normalize_screening_report(raw)
        legacy = denormalize_to_legacy(normalized)

        assert legacy == raw_copy, f"Round-trip failed for seed={seed}"

    @pytest.mark.parametrize("seed", range(120))
    def test_input_not_mutated(self, seed):
        raw = _generate_random_report(seed)
        raw_copy = copy.deepcopy(raw)
        normalize_screening_report(raw)
        assert raw == raw_copy, f"Input mutated for seed={seed}"
