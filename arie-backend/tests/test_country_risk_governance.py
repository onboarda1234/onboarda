"""
PR-CR1 country-risk source governance tests.

Country risk must be source-backed, versioned, freshness-aware, and shared by
risk scoring and memo evidence.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from country_risk import (
    ACTIVE_SNAPSHOT_ID,
    ACTIVE_SNAPSHOT_VERSION,
    FATF_CALL_FOR_ACTION_URL,
    FATF_INCREASED_MONITORING_URL,
    list_country_risk_entries,
    lookup_country_risk,
)
from memo_handler import build_compliance_memo
from rule_engine import classify_country, compute_risk_score, _is_elevated_jurisdiction


def test_seeded_country_risk_snapshot_has_source_metadata(temp_db):
    from db import get_db

    db = get_db()
    try:
        payload = list_country_risk_entries(db)
        snapshot = payload["snapshot"]
        entries = payload["entries"]
    finally:
        db.close()

    assert snapshot["id"] == ACTIVE_SNAPSHOT_ID
    assert snapshot["version"] == ACTIVE_SNAPSHOT_VERSION
    assert snapshot["source_url"] == FATF_INCREASED_MONITORING_URL
    assert snapshot["effective_date"] == "2026-02-13"
    assert snapshot["checksum"]
    assert len(entries) > 50


def test_fatf_statuses_are_source_backed(temp_db):
    from db import get_db

    db = get_db()
    try:
        iran = lookup_country_risk("Iran", db=db)
        kuwait = lookup_country_risk("Kuwait", db=db)
    finally:
        db.close()

    assert iran["risk_score"] == 4
    assert iran["fatf_status"] == "call_for_action"
    assert iran["source_url"] == FATF_CALL_FOR_ACTION_URL
    assert iran["snapshot_version"] == ACTIVE_SNAPSHOT_VERSION
    assert kuwait["risk_score"] == 3
    assert kuwait["fatf_status"] == "increased_monitoring"
    assert kuwait["source_url"] == FATF_INCREASED_MONITORING_URL


def test_unknown_country_fails_safe_to_medium_with_warning(temp_db):
    country_risk = lookup_country_risk("Atlantis")

    assert country_risk["risk_score"] == 2
    assert country_risk["risk_rating"] == "MEDIUM"
    assert country_risk["is_unknown"] is True
    assert country_risk["defaulted"] is True
    assert "never LOW" in country_risk["notes"]
    assert country_risk["stale_warning"]


def test_canonical_snapshot_overrides_legacy_country_config_for_known_countries(temp_db):
    config = {"mauritius": 4, "unlisted testland": 4}

    assert classify_country("Mauritius", config) == 2
    assert classify_country("Unlisted Testland", config) == 4


def test_pakistan_uses_canonical_current_status_not_stale_legacy_fatf_entry(temp_db):
    pakistan = lookup_country_risk("Pakistan")

    assert pakistan["found"] is True
    assert pakistan["risk_score"] == 2
    assert pakistan["fatf_status"] == "none"
    assert _is_elevated_jurisdiction("Pakistan") is False


def test_risk_scoring_returns_country_risk_provenance(temp_db):
    result = compute_risk_score({
        "entity_type": "SME",
        "country": "Kuwait",
        "sector": "Technology",
        "directors": [{"full_name": "Jane Director", "nationality": "Kuwaiti", "is_pep": "No"}],
        "ubos": [{"full_name": "Jane UBO", "nationality": "Kuwaiti", "ownership_pct": "100", "is_pep": "No"}],
    })

    provenance = result["country_risk_provenance"]
    assert provenance["country_key"] == "kuwait"
    assert provenance["risk_score"] == 3
    assert provenance["fatf_status"] == "increased_monitoring"
    assert provenance["snapshot_version"] == ACTIVE_SNAPSHOT_VERSION


def test_memo_uses_same_country_risk_snapshot_as_scoring(temp_db):
    app = {
        "id": "app-cr1-memo",
        "ref": "ARF-CR1-MEMO",
        "reference_number": "ARF-CR1-MEMO",
        "company_name": "CR1 Memo Test Ltd",
        "brn": "CR1001",
        "entity_type": "SME",
        "country": "Kuwait",
        "sector": "Technology",
        "ownership_structure": "simple",
        "source_of_funds": "Operating revenue",
        "expected_volume": "USD 100,000 monthly",
        "risk_level": "HIGH",
        "risk_score": 64,
        "assigned_to": "admin001",
        "operating_countries": "Kuwait",
        "incorporation_date": "2024-01-01",
        "business_activity": "Technology services",
    }
    directors = [{"full_name": "Jane Director", "nationality": "Kuwaiti", "is_pep": "No"}]
    ubos = [{"full_name": "Jane UBO", "nationality": "Kuwaiti", "ownership_pct": 100, "is_pep": "No"}]

    memo, _, _, _ = build_compliance_memo(app, directors, ubos, [])
    jurisdiction = memo["metadata"]["risk_evidence"]["jurisdiction"]

    assert jurisdiction["rating"] == "HIGH"
    assert jurisdiction["risk_score"] == 3
    assert jurisdiction["fatf_status"] == "increased_monitoring"
    assert jurisdiction["snapshot_version"] == ACTIVE_SNAPSHOT_VERSION
    assert jurisdiction["source_url"] == FATF_INCREASED_MONITORING_URL


def test_stale_snapshot_is_reported_on_lookup(temp_db):
    from db import get_db

    db = get_db()
    try:
        db.execute(
            """
            UPDATE country_risk_snapshots
               SET last_checked_at='2025-01-01T00:00:00+00:00',
                   freshness_days=1
             WHERE id=?
            """,
            (ACTIVE_SNAPSHOT_ID,),
        )
        db.commit()
        kuwait = lookup_country_risk("Kuwait", db=db)
    finally:
        db.close()

    assert kuwait["is_stale"] is True
    assert "freshness has expired" in kuwait["stale_warning"]
