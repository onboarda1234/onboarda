"""BE-1 — surface proven percentage scores into the legacy screening result.

Historical normalized fixtures use ``profile.match_details.match_score`` as a
percentage-capable value. Live CA Mesh ``detail.profile.match_score`` is kept as
raw provider data instead because sandbox values 0.7 and 1.7 were both returned
for exact matches.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screening_complyadvantage.normalizer import (
    _legacy_screening_result_from_match,
    _match_score_percentage,
    MergedMatch,
)
from screening_complyadvantage.models.output import (
    CAProfile,
    CAMatchDetails,
    CAProfileCompany,
    CARiskDetail,
)


def _match(score, surfaced="strict", with_profile=True):
    profile = None
    if with_profile:
        profile = CAProfile(
            identifier="p",
            company=CAProfileCompany(),
            match_details=CAMatchDetails(match_score=score),
            risk_types=[],
            risk_indicators=[],
        )
    return MergedMatch(
        risk=CARiskDetail(),
        surfaced_by_pass=surfaced,
        profile=profile,
        profile_identifier="p",
        risk_id="r",
        alert_id="a",
    )


def test_fractional_score_scaled_to_percentage():
    assert _match_score_percentage(_match(0.87)) == 87.0
    assert _match_score_percentage(_match(1.0)) == 100.0
    assert _match_score_percentage(_match(0.5)) == 50.0


def test_already_percentage_passed_through():
    # Defensive: a value already on a 0–100 scale is not scaled again.
    assert _match_score_percentage(_match(96)) == 96.0


def test_missing_score_is_none():
    assert _match_score_percentage(_match(None)) is None


def test_missing_profile_is_none():
    assert _match_score_percentage(_match(0.9, with_profile=False)) is None


def test_legacy_result_carries_score_and_confidence():
    row = _legacy_screening_result_from_match(_match(0.87, "strict"), {"has_sanctions_hit": True})
    assert row["match_score"] == 87.0
    assert row["surfaced_by_pass"] == "strict"


def test_legacy_result_null_score_keeps_confidence_fallback():
    row = _legacy_screening_result_from_match(_match(None, "relaxed"), {})
    assert row["match_score"] is None
    assert row["surfaced_by_pass"] == "relaxed"


def test_live_provider_raw_score_is_captured_but_not_rendered_as_percentage():
    profile = CAProfile(
        identifier="p",
        company=CAProfileCompany(),
        provider_match_score_raw=1.7,
        provider_match_types=["exact_match"],
    )
    match = MergedMatch(
        risk=CARiskDetail(),
        surfaced_by_pass="strict",
        profile=profile,
        profile_identifier="p",
        risk_id="r",
        alert_id="a",
    )

    row = _legacy_screening_result_from_match(match, {})

    assert row["match_score"] is None
    assert row["provider_match_score_raw"] == 1.7
    assert row["provider_match_types"] == ["exact_match"]


def test_pdf_flattener_prefers_percentage_then_confidence():
    import pdf_generator
    report = {
        "company_screening": {
            "company_name": "Score Co",
            "results": [
                {"name": "Scored Hit", "category": "sanctions", "match_score": 87.0, "surfaced_by_pass": "strict"},
                {"name": "Unscored Hit", "category": "watchlist", "match_score": None, "surfaced_by_pass": "relaxed"},
                {"name": "Bare Hit", "category": "watchlist"},
            ],
        },
    }
    hits = {h["matched_name"]: h for h in pdf_generator._screening_report_hits(report)}
    assert hits["Scored Hit"]["confidence"] == "87.0%"
    assert "relaxed" in hits["Unscored Hit"]["confidence"].lower()
    assert hits["Bare Hit"]["confidence"] == "Not scored by provider"
