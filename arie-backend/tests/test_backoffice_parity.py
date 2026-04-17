"""
Backoffice Rendering Parity Test — SCR-009
============================================
Simulates the JavaScript data-access patterns used by
``arie-backoffice.html`` to extract screening data for display.

For each golden-file fixture:
1. Extract values from the raw legacy dict
2. Apply normalize → denormalize round-trip
3. Extract values from the denormalized output
4. Assert identical results

This proves that the abstraction layer will not silently break the
backoffice UI rendering.
"""

import copy
import pytest
from screening_normalizer import normalize_screening_report, denormalize_to_legacy

# Import golden files from the round-trip test suite
from tests.test_screening_roundtrip import ALL_GOLDEN_FILES


# ── JS-equivalent extraction functions ──

def extract_undeclared_pep_count(report: dict) -> dict:
    """
    Simulates:
        sr.director_screenings.filter(s => s.undeclared_pep).length
        sr.ubo_screenings.filter(s => s.undeclared_pep).length
    """
    director_peps = len([
        s for s in (report.get("director_screenings") or [])
        if isinstance(s, dict) and s.get("undeclared_pep")
    ])
    ubo_peps = len([
        s for s in (report.get("ubo_screenings") or [])
        if isinstance(s, dict) and s.get("undeclared_pep")
    ])
    return {"director_undeclared_pep": director_peps, "ubo_undeclared_pep": ubo_peps}


def classify_screening_hits(results: list) -> dict:
    """
    Simulates classifyScreeningHits() from arie-backoffice.html (lines 5374-5387):
        sanctions_hits = results.filter(r => r.is_sanctioned).length
        pep_hits = results.filter(r => r.is_pep).length
        other_hits = total - sanctions - pep
    """
    if not isinstance(results, list):
        return {"sanctions_hits": 0, "pep_hits": 0, "other_hits": 0, "total_hits": 0}
    sanctions = sum(1 for r in results if isinstance(r, dict) and r.get("is_sanctioned"))
    pep = sum(1 for r in results if isinstance(r, dict) and r.get("is_pep"))
    total = len(results)
    return {
        "sanctions_hits": sanctions,
        "pep_hits": pep,
        "other_hits": total - sanctions - pep,
        "total_hits": total,
    }


def extract_person_screening_data(person: dict) -> dict:
    """
    Simulates the backoffice extraction pattern for each person screening:
        screening = (screeningRecord || {}).screening || null;
        facts = screeningResultFacts(screening);
    """
    if not isinstance(person, dict):
        return {}
    screening = (person.get("screening") or {})
    return {
        "person_name": person.get("person_name", ""),
        "person_type": person.get("person_type", ""),
        "declared_pep": person.get("declared_pep", ""),
        "undeclared_pep": person.get("undeclared_pep", False),
        "api_status": screening.get("api_status", ""),
        "source": screening.get("source", ""),
        "screened_at": screening.get("screened_at", ""),
        "hit_classification": classify_screening_hits(screening.get("results", [])),
    }


def screening_badge_input(status: str) -> str:
    """
    Simulates screeningBadge() from arie-backoffice.html (lines 5492-5503):
    Maps status string to badge category.
    """
    s = (status or "").lower()
    if s in ("not_available", "unavailable"):
        return "Not Available"
    if s == "declared":
        return "Declared"
    if s == "not_declared":
        return "Not Declared"
    if s in ("clear", "no_match", "passed"):
        return "Clear"
    if s in ("match", "hit", "failed"):
        return "Match"
    if s in ("possible_match", "review"):
        return "Review"
    return s


def extract_all_rendering_data(report: dict) -> dict:
    """
    Extract all data points the backoffice UI uses from a screening report.
    """
    undeclared = extract_undeclared_pep_count(report)

    director_details = [
        extract_person_screening_data(d)
        for d in (report.get("director_screenings") or [])
    ]
    ubo_details = [
        extract_person_screening_data(u)
        for u in (report.get("ubo_screenings") or [])
    ]

    # All screening records for badge inputs
    all_screenings = (report.get("director_screenings") or []) + \
                     (report.get("ubo_screenings") or [])
    badge_inputs = []
    for s in all_screenings:
        if isinstance(s, dict):
            screening = (s.get("screening") or {})
            badge_inputs.append(screening_badge_input(screening.get("api_status", "")))

    return {
        "undeclared_pep_counts": undeclared,
        "director_details": director_details,
        "ubo_details": ubo_details,
        "badge_inputs": badge_inputs,
        "screened_at": report.get("screened_at", ""),
        "total_hits": report.get("total_hits", 0),
        "overall_flags": report.get("overall_flags", []),
    }


# ── Parametrised parity tests ──

@pytest.mark.parametrize(
    "name,golden", ALL_GOLDEN_FILES,
    ids=[g[0] for g in ALL_GOLDEN_FILES],
)
def test_backoffice_rendering_parity(name, golden):
    """
    Simulated backoffice extraction must produce identical results
    from raw legacy data vs. normalize→denormalize round-tripped data.
    """
    raw = copy.deepcopy(golden)
    roundtripped = denormalize_to_legacy(normalize_screening_report(golden))

    raw_rendering = extract_all_rendering_data(raw)
    rt_rendering = extract_all_rendering_data(roundtripped)

    assert raw_rendering == rt_rendering, (
        f"Backoffice rendering parity failed for '{name}'.\n"
        f"Raw:          {raw_rendering}\n"
        f"Round-tripped: {rt_rendering}"
    )


@pytest.mark.parametrize(
    "name,golden", ALL_GOLDEN_FILES,
    ids=[g[0] for g in ALL_GOLDEN_FILES],
)
def test_undeclared_pep_count_parity(name, golden):
    """Undeclared PEP counts must be identical after round-trip."""
    raw = copy.deepcopy(golden)
    roundtripped = denormalize_to_legacy(normalize_screening_report(golden))

    assert extract_undeclared_pep_count(raw) == extract_undeclared_pep_count(roundtripped)


@pytest.mark.parametrize(
    "name,golden", ALL_GOLDEN_FILES,
    ids=[g[0] for g in ALL_GOLDEN_FILES],
)
def test_screening_badge_parity(name, golden):
    """Screening badge inputs must be identical after round-trip."""
    raw = copy.deepcopy(golden)
    roundtripped = denormalize_to_legacy(normalize_screening_report(golden))

    raw_badges = []
    rt_badges = []
    for src, dst_list in [(raw, raw_badges), (roundtripped, rt_badges)]:
        for key in ("director_screenings", "ubo_screenings"):
            for person in (src.get(key) or []):
                if isinstance(person, dict):
                    screening = (person.get("screening") or {})
                    dst_list.append(screening_badge_input(screening.get("api_status", "")))

    assert raw_badges == rt_badges


@pytest.mark.parametrize(
    "name,golden", ALL_GOLDEN_FILES,
    ids=[g[0] for g in ALL_GOLDEN_FILES],
)
def test_hit_classification_parity(name, golden):
    """Hit classification must be identical after round-trip."""
    raw = copy.deepcopy(golden)
    roundtripped = denormalize_to_legacy(normalize_screening_report(golden))

    for key in ("director_screenings", "ubo_screenings"):
        raw_persons = raw.get(key) or []
        rt_persons = roundtripped.get(key) or []
        assert len(raw_persons) == len(rt_persons), f"Count mismatch in {key}"
        for i, (rp, rtp) in enumerate(zip(raw_persons, rt_persons)):
            raw_results = (rp.get("screening") or {}).get("results", [])
            rt_results = (rtp.get("screening") or {}).get("results", [])
            assert classify_screening_hits(raw_results) == classify_screening_hits(rt_results), \
                f"Hit classification mismatch for {key}[{i}]"
