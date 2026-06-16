"""
PR-CR1R country-risk rollback tests.

For pilot, manual Risk Scoring Model settings are the active source of truth.
The PR-CR1 imported country-risk snapshot remains dormant and must not drive
scoring, memo evidence, approval gates, or UI grouping.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memo_handler import build_compliance_memo
from rule_engine import (
    country_risk_details,
    classify_country,
    compute_risk_score,
    _get_risk_config_version,
    _is_elevated_jurisdiction,
)


def _manual_config(**overrides):
    scores = {
        "france": 1,
        "mauritius": 2,
        "kuwait": 2,
        "nigeria": 3,
        "iran": 4,
    }
    scores.update(overrides)
    return scores


def test_manual_country_risk_scores_are_authoritative_for_known_countries(temp_db):
    scores = _manual_config(mauritius=4, kuwait=3)

    assert classify_country("Mauritius", scores) == 4
    assert classify_country("Kuwait", scores) == 3
    assert classify_country("Unlisted Testland", {"unlisted testland": 3}) == 3


def test_unknown_country_defaults_to_medium_not_low(temp_db):
    details = country_risk_details("Atlantis", _manual_config())

    assert details["risk_score"] == 2
    assert details["risk_rating"] == "MEDIUM"
    assert details["is_unknown"] is True
    assert details["defaulted"] is True
    assert details["active_source"] == "manual_settings"


def test_manual_country_settings_override_stale_legacy_fatf_membership(temp_db):
    scores = _manual_config(pakistan=2)
    details = country_risk_details("Pakistan", scores)

    assert classify_country("Pakistan", scores) == 2
    assert details["fatf_status"] == "none"
    assert _is_elevated_jurisdiction("Pakistan", scores) is False


def test_risk_scoring_returns_manual_country_risk_provenance(temp_db):
    result = compute_risk_score(
        {
            "entity_type": "SME",
            "country": "Kuwait",
            "sector": "Technology",
            "directors": [{"full_name": "Jane Director", "nationality": "Kuwaiti", "is_pep": "No"}],
            "ubos": [{"full_name": "Jane UBO", "nationality": "Kuwaiti", "ownership_pct": "100", "is_pep": "No"}],
        },
        config_override={
            "dimensions": None,
            "thresholds": None,
            "country_risk_scores": _manual_config(kuwait=2),
            "sector_risk_scores": {"technology": 2},
            "entity_type_scores": {"sme": 2},
        },
    )

    provenance = result["country_risk_provenance"]
    assert provenance["country_key"] == "kuwait"
    assert provenance["risk_score"] == 2
    assert provenance["source"] == "risk_config.country_risk_scores"
    assert provenance["active_source"] == "manual_settings"
    assert "snapshot_version" not in provenance


def test_memo_uses_manual_country_risk_source_not_snapshot(temp_db):
    app = {
        "id": "app-cr1r-memo",
        "ref": "ARF-CR1R-MEMO",
        "reference_number": "ARF-CR1R-MEMO",
        "company_name": "CR1R Memo Test Ltd",
        "brn": "CR1R001",
        "entity_type": "SME",
        "country": "Mauritius",
        "sector": "Technology",
        "ownership_structure": "simple",
        "source_of_funds": "Operating revenue",
        "expected_volume": "USD 100,000 monthly",
        "risk_level": "MEDIUM",
        "risk_score": 50,
        "assigned_to": "admin001",
        "operating_countries": "Mauritius",
        "incorporation_date": "2024-01-01",
        "business_activity": "Technology services",
    }
    directors = [{"full_name": "Jane Director", "nationality": "Mauritian", "is_pep": "No"}]
    ubos = [{"full_name": "Jane UBO", "nationality": "Mauritian", "ownership_pct": 100, "is_pep": "No"}]

    memo, _, _, _ = build_compliance_memo(app, directors, ubos, [])
    jurisdiction = memo["metadata"]["risk_evidence"]["jurisdiction"]

    assert jurisdiction["source"] == "Manual Risk Scoring Model country_risk_scores"
    assert jurisdiction["source_mode"] == "manual_settings"
    assert jurisdiction["risk_score"] == 2
    assert jurisdiction["snapshot_version"] == ""
    assert "manual settings active for pilot" in jurisdiction["prose"]


def test_risk_config_version_no_longer_depends_on_country_risk_snapshot(temp_db):
    from db import get_db

    db = get_db()
    try:
        version = _get_risk_config_version(db)
        row = db.execute("SELECT updated_at FROM risk_config WHERE id=1").fetchone()
    finally:
        db.close()

    assert version == (f"risk_config:{row['updated_at']}" if row else None)
    assert not version or "country_risk:" not in version
