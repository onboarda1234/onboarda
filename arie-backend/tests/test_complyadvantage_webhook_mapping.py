import json

from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME
from screening_complyadvantage.webhook_mapping import map_normalized_to_monitoring_alert


def _report(indicator_types):
    return {
        "provider_specific": {
            COMPLYADVANTAGE_PROVIDER_NAME: {
                "matches": [{"indicators": [{"type": t, "taxonomy_key": k} for t, k in indicator_types]}],
                "workflows": {"strict": {"alerts": [{"identifier": "alert-1"}]}},
            }
        }
    }


def _scoped_report(scope, indicator_types=None):
    report = _report(indicator_types or [("CAMediaIndicator", "r_adverse_media_general")])
    report["subject_scope"] = scope
    report["screening_subject_kind"] = "entity" if scope == "entity" else "subject"
    report["provider_specific"][COMPLYADVANTAGE_PROVIDER_NAME]["subject_scope"] = scope
    report["provider_specific"][COMPLYADVANTAGE_PROVIDER_NAME]["screening_subject"] = {
        "kind": report["screening_subject_kind"],
        "scope": scope,
        "person_key": "person-1" if scope == "person" else None,
    }
    return report


def test_mapping_uses_locked_priority_and_summary():
    row = map_normalized_to_monitoring_alert(
        _report([("CAMediaIndicator", "r_adverse_media_general"), ("CAPEPIndicator", "r_pep_class_2")]),
        case_identifier="case-1",
        customer_identifier="cust-1",
        normalized_record_id=42,
    )

    assert row["provider"] == COMPLYADVANTAGE_PROVIDER_NAME
    assert row["case_identifier"] == "case-1"
    assert row["alert_type"] == "pep"
    assert row["severity"] == "medium"
    assert row["summary"] == "CA case case-1 surfaced 1 match(es); top indicator: pep for customer cust-1"
    assert json.loads(row["source_reference"]) == {
        "provider": COMPLYADVANTAGE_PROVIDER_NAME,
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


def test_mapping_persists_entity_subject_scope_when_report_is_company_scoped():
    row = map_normalized_to_monitoring_alert(
        _scoped_report("entity"),
        case_identifier="case-company",
        customer_identifier="cust-company",
        normalized_record_id=7,
    )

    source_reference = json.loads(row["source_reference"])
    assert row["alert_type"] == "media"
    assert source_reference["subject_scope"] == "entity"
    assert source_reference["screening_subject"]["scope"] == "entity"
    assert source_reference["normalized_record_id"] == 7


def test_mapping_persists_person_subject_scope_when_report_is_person_scoped():
    row = map_normalized_to_monitoring_alert(
        _scoped_report("person"),
        case_identifier="case-person",
        customer_identifier="cust-person",
        normalized_record_id=8,
    )

    source_reference = json.loads(row["source_reference"])
    assert source_reference["subject_scope"] == "person"
    assert source_reference["screening_subject"]["person_key"] == "person-1"
