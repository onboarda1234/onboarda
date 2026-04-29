import json

from screening_complyadvantage.webhook_mapping import map_normalized_to_monitoring_alert


def _report(indicator_types):
    return {
        "provider_specific": {
            "complyadvantage": {
                "matches": [{"indicators": [{"type": t, "taxonomy_key": k} for t, k in indicator_types]}],
                "workflows": {"strict": {"alerts": [{"identifier": "alert-1"}]}},
            }
        }
    }


def test_mapping_uses_locked_priority_and_summary():
    row = map_normalized_to_monitoring_alert(
        _report([("CAMediaIndicator", "r_adverse_media_general"), ("CAPEPIndicator", "r_pep_class_2")]),
        case_identifier="case-1",
        customer_identifier="cust-1",
        normalized_record_id=42,
    )

    assert row["provider"] == "complyadvantage"
    assert row["case_identifier"] == "case-1"
    assert row["alert_type"] == "pep"
    assert row["severity"] == "medium"
    assert row["summary"] == "CA case case-1 surfaced 1 match(es); top indicator: pep for customer cust-1"
    assert json.loads(row["source_reference"]) == {
        "provider": "complyadvantage",
        "case_identifier": "case-1",
        "alert_identifier": "alert-1",
        "normalized_record_id": 42,
    }


def test_sanctions_beats_watchlist_pep_and_media():
    row = map_normalized_to_monitoring_alert(
        _report([
            ("CAMediaIndicator", "r_adverse_media_general"),
            ("CAPEPIndicator", "r_pep_class_2"),
            ("CAWatchlistIndicator", "r_watchlist"),
            ("CASanctionIndicator", "r_direct_sanctions_exposure"),
        ]),
        case_identifier="case-2",
        customer_identifier="cust-2",
    )
    assert row["alert_type"] == "sanctions"
    assert row["severity"] == "critical"
