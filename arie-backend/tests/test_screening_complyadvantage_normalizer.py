import copy
import json
import os

import pytest

from screening_models import validate_normalized_report
from screening_complyadvantage.models import (
    CAAlertResponse,
    CACustomerInput,
    CACustomerResponse,
    CAMediaIndicator,
    CAMediaArticleValue,
    CAPEPIndicator,
    CAPEPValue,
    CAPaginatedCollection,
    CAProfile,
    CARiskDetail,
    CARiskDetailInner,
    CARiskType,
    CASanctionIndicator,
    CASanctionValue,
    CAWatchlistIndicator,
    CAWatchlistValue,
    CAWorkflowResponse,
)
from screening_complyadvantage.normalizer import (
    MergedMatch,
    ResnapshotContext,
    ScreeningApplicationContext,
    compute_ca_screening_hash,
    compute_match_rollups,
    extract_pep_classes,
    merge_two_pass_results,
    normalize_single_pass,
    normalize_two_pass_screening,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "complyadvantage")


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def _risk(raw):
    values = []
    for item in raw.get("values", []):
        rt = CARiskType(**item["risk_type"])
        indicators = []
        for indicator in item.get("indicators", []):
            value = indicator["value"]
            key = rt.key
            if key.startswith("r_pep") or key == "r_rca":
                indicators.append(CAPEPIndicator(risk_type=rt, value=CAPEPValue.model_validate(value)))
            elif key.startswith("r_adverse_media"):
                indicators.append(CAMediaIndicator(risk_type=rt, value=CAMediaArticleValue.model_validate(value)))
            elif key.startswith("r_sanctions_exposure") or key in {"r_watchlist", "r_law_enforcement"}:
                indicators.append(CAWatchlistIndicator(risk_type=rt, value=CAWatchlistValue.model_validate(value)))
            else:
                indicators.append(CASanctionIndicator(risk_type=rt, value=CASanctionValue.model_validate(value)))
        values.append(CARiskDetailInner(risk_type=rt, indicators=indicators))
    return CARiskDetail(values=values)


def _objects(data, prefix=""):
    workflow = CAWorkflowResponse.model_validate(data.get(prefix + "workflow", data.get("workflow")))
    customer_input = CACustomerInput.model_validate(data["customer_input"])
    customer_response = CACustomerResponse.model_validate(data["customer_response"])
    context = ScreeningApplicationContext.model_validate(data["context"])
    alerts = []
    deep = {}
    alerts_risks = data.get(prefix + "alerts_risks", data.get("alerts_risks"))
    deep_risks = data.get(prefix + "deep_risks", data.get("deep_risks", {}))
    if alerts_risks is None:
        legacy_alerts = data.get(prefix + "alerts", data.get("alerts", []))
        alerts_risks = {
            raw["identifier"]: [{"identifier": raw["identifier"], "profile": raw["profile"]}]
            for raw in legacy_alerts
        }
        deep_risks = {raw["identifier"]: raw["risk_detail"] for raw in legacy_alerts}
    for risk_items in alerts_risks.values():
        for raw in risk_items:
            profile = CAProfile.model_validate(raw["profile"])
            risk = _risk(deep_risks[raw["identifier"]])
            alerts.append(CAAlertResponse(
                identifier=raw["identifier"],
                profile=profile,
                risk_details=CAPaginatedCollection[CARiskDetail](values=[risk]),
            ))
            deep[raw["identifier"]] = risk
    return workflow, alerts, deep, customer_input, customer_response, context


def _single(name):
    data = _fixture(name)
    workflow, alerts, deep, customer_input, customer_response, context = _objects(data)
    return normalize_single_pass(
        workflow, alerts, deep, customer_input, customer_response, context,
        ResnapshotContext(webhook_type="CASE_ALERT_LIST_UPDATED", source_case_identifier="case-test", received_at="2026-01-01T00:00:00Z"),
    )


def _merged_fixture(name):
    _, alerts, deep, *_ = _objects(_fixture(name))
    attached = {a.identifier: a.risk_details.values[0] for a in alerts}
    for alert in alerts:
        object.__setattr__(attached[alert.identifier], "_ca_profile", alert.profile)
    return [MergedMatch(risk=r, surfaced_by_pass="strict", profile=getattr(r, "_ca_profile"), profile_identifier=getattr(r, "_ca_profile").identifier, risk_id=k) for k, r in attached.items()]


def test_two_pass_merge_dedupes_by_profile_identifier():
    match = _merged_fixture("pep_canonical.json")[0]
    merged, _ = merge_two_pass_results({"a": match.risk}, {"b": match.risk})
    assert len(merged) == 1


def test_two_pass_merge_tags_strict_only_correctly():
    match = _merged_fixture("pep_canonical.json")[0]
    assert merge_two_pass_results({"a": match.risk}, {})[0][0].surfaced_by_pass == "strict"


def test_two_pass_merge_tags_relaxed_only_correctly():
    match = _merged_fixture("pep_canonical.json")[0]
    assert merge_two_pass_results({}, {"b": match.risk})[0][0].surfaced_by_pass == "relaxed"


def test_two_pass_merge_tags_both_correctly():
    match = _merged_fixture("pep_canonical.json")[0]
    assert merge_two_pass_results({"a": match.risk}, {"b": match.risk})[0][0].surfaced_by_pass == "both"


def test_two_pass_merge_provenance_counts_match():
    match = _merged_fixture("pep_canonical.json")[0]
    _, provenance = merge_two_pass_results({"a": match.risk}, {"b": match.risk})
    assert provenance["strict_match_count"] == 1
    assert provenance["relaxed_match_count"] == 1
    assert provenance["both_count"] == 1


def test_two_pass_merge_deterministic_output_order():
    a = _merged_fixture("sanctions_canonical.json")[0]
    b = _merged_fixture("pep_canonical.json")[0]
    merged, _ = merge_two_pass_results({"z": a.risk, "a": b.risk}, {})
    assert [m.profile_identifier for m in merged] == sorted(m.profile_identifier for m in merged)


def test_normalize_clean_baseline():
    report = _single("clean_baseline.json")
    assert report["total_hits"] == 0
    assert report["any_pep_hits"] is False


def test_normalize_pure_sanctions_via_fixture():
    report = _single("sanctions_canonical.json")
    assert report["any_sanctions_hits"] is True
    assert report["director_screenings"][0]["has_sanctions_hit"] is True


def test_normalize_pep_via_fixture():
    report = _single("pep_canonical.json")
    assert report["any_pep_hits"] is True
    assert report["director_screenings"][0]["pep_classes"] == ["PEP_CLASS_1", "PEP_CLASS_2"]


def test_normalize_rca_via_fixture():
    report = _single("rca_canonical.json")
    assert report["ubo_screenings"][0]["is_rca"] is True
    assert report["provider_specific"]["complyadvantage"]["matches"][0]["relationships"][0]["relationship_type"] == "relative"


def test_normalize_adverse_media_via_fixture():
    report = _single("adverse_media_multi_source.json")
    articles = report["provider_specific"]["complyadvantage"]["matches"][0]["indicators"]
    assert articles[0]["value"]["canonical_url"]["domain"] == "test-fixture.example.com"
    assert articles[0]["value"]["snippets"] == [{"text": "Test snippet 1"}]


def test_normalize_company_via_fixture():
    report = _single("company_canonical.json")
    assert report["company_screening_coverage"] == "full"
    assert report["total_persons_screened"] == 0


def test_two_pass_strict_misses_relaxed_catches_canonical():
    """Synthetic: relaxed returns canonical PEP that strict missed."""
    data = _fixture("two_pass_strict_misses_relaxed_catches.json")
    sw, sa, sd, customer_input, customer_response, context = _objects(data, "strict_")
    rw, ra, rd, *_ = _objects(data, "relaxed_")
    report = normalize_two_pass_screening(sw, sa, sd, rw, ra, rd, customer_input, customer_response, context)
    matches = report["provider_specific"]["complyadvantage"]["matches"]
    canonical = [m for m in matches if m["profile_identifier"] == "prof-canonical"][0]
    assert canonical["surfaced_by_pass"] == "relaxed"


def test_indicator_type_drives_pep_rollup_not_taxonomy():
    match = _merged_fixture("pep_canonical.json")[0]
    assert compute_match_rollups(match)["has_pep_hit"] is True


def test_watchlist_taxonomy_disambiguation_for_sanctions():
    match = _merged_fixture("sanctions_canonical.json")[0]
    assert compute_match_rollups(match)["has_sanctions_hit"] is True


def test_is_rca_three_state_logic():
    assert compute_match_rollups(_merged_fixture("company_canonical.json")[0])["is_rca"] is None
    assert compute_match_rollups(_merged_fixture("pep_canonical.json")[0])["is_rca"] is False
    assert compute_match_rollups(_merged_fixture("rca_canonical.json")[0])["is_rca"] is True


def test_pep_classes_preserves_multiplicity():
    assert extract_pep_classes(_merged_fixture("pep_canonical.json")[0]) == ["PEP_CLASS_1", "PEP_CLASS_2"]


def test_hash_stable_across_identical_state():
    matches = _merged_fixture("pep_canonical.json")
    assert compute_ca_screening_hash(matches) == compute_ca_screening_hash(copy.deepcopy(matches))


def test_hash_changes_on_decision_change():
    base = _merged_fixture("pep_canonical.json")
    other = _merged_fixture("company_canonical.json")
    assert compute_ca_screening_hash(base) != compute_ca_screening_hash(other)


def test_hash_changes_on_new_sanction_entry():
    assert compute_ca_screening_hash(_merged_fixture("pep_canonical.json")) != compute_ca_screening_hash(_merged_fixture("sanctions_canonical.json"))


def test_hash_changes_on_new_pep_class():
    assert compute_ca_screening_hash(_merged_fixture("pep_canonical.json")) != compute_ca_screening_hash(_merged_fixture("rca_canonical.json"))


def test_hash_changes_on_new_media_article():
    assert compute_ca_screening_hash(_merged_fixture("pep_canonical.json")) != compute_ca_screening_hash(_merged_fixture("adverse_media_multi_source.json"))


def test_hash_changes_on_new_relationship():
    assert compute_ca_screening_hash(_merged_fixture("pep_canonical.json")) != compute_ca_screening_hash(_merged_fixture("rca_canonical.json"))


def test_hash_excludes_surfaced_by_pass():
    matches = _merged_fixture("adverse_media_multi_source.json")
    changed = copy.deepcopy(matches)
    changed[0].surfaced_by_pass = "relaxed"
    assert compute_ca_screening_hash(matches) == compute_ca_screening_hash(changed)


def test_hash_format_32_lowercase_hex():
    h = compute_ca_screening_hash(_merged_fixture("pep_canonical.json"))
    assert len(h) == 32
    assert all(c in "0123456789abcdef" for c in h)


def test_normalize_single_pass_no_provenance():
    assert "provenance" not in _single("pep_canonical.json")


def test_normalize_single_pass_no_surfaced_by_pass_per_match():
    match = _single("pep_canonical.json")["provider_specific"]["complyadvantage"]["matches"][0]
    assert "surfaced_by_pass" not in match


def test_normalize_single_pass_resnapshot_metadata():
    resnapshot = _single("pep_canonical.json")["provider_specific"]["complyadvantage"]["resnapshot"]
    assert resnapshot["webhook_type"] == "CASE_ALERT_LIST_UPDATED"


def test_normalized_v2_passes_validate_normalized_report():
    assert validate_normalized_report(_single("pep_canonical.json")) == []


def test_normalized_record_provider_field_is_complyadvantage():
    assert _single("pep_canonical.json")["provider"] == "complyadvantage"


def test_normalized_record_normalized_version_is_2_0():
    assert _single("pep_canonical.json")["normalized_version"] == "2.0"
