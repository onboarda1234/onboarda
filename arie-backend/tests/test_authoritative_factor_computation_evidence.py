"""Authoritative factor-level computation evidence remains arithmetic, not a scorer."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risk_model_view import build_authoritative_risk_report_evidence
from rule_engine import apply_risk_floor, compute_risk_score


DIMENSIONS = [
    {"id": "D1", "name": "Customer / Entity Risk", "weight": 30, "subcriteria": [
        {"name": "Entity Type", "weight": 20}, {"name": "Ownership Structure", "weight": 20},
        {"name": "PEP Status", "weight": 25}, {"name": "Adverse Media", "weight": 15},
        {"name": "Source of Wealth", "weight": 10}, {"name": "Source of Funds", "weight": 10},
    ]},
    {"id": "D2", "name": "Geographic Risk", "weight": 25, "subcriteria": [
        {"name": "Country of Incorporation", "weight": 25}, {"name": "UBO Nationalities", "weight": 20},
        {"name": "Intermediary Shareholder Jurisdictions", "weight": 20}, {"name": "Countries of Operation", "weight": 20},
        {"name": "Target Markets", "weight": 15},
    ]},
    {"id": "D3", "name": "Product / Service Risk", "weight": 20, "subcriteria": [
        {"name": "Service Type", "weight": 40}, {"name": "Monthly Volume", "weight": 35},
        {"name": "Transaction Complexity", "weight": 25},
    ]},
    {"id": "D4", "name": "Industry / Sector Risk", "weight": 15, "subcriteria": [
        {"name": "Industry Sector", "weight": 100},
    ]},
    {"id": "D5", "name": "Delivery Channel Risk", "weight": 10, "subcriteria": [
        {"name": "Introduction Method", "weight": 50}, {"name": "Delivery Channel", "weight": 50},
    ]},
]


def _config():
    return {
        "_config_version": "risk_config:test-factor-evidence",
        "dimensions": DIMENSIONS,
        "thresholds": [
            {"level": "LOW", "min": 0, "max": 39.9},
            {"level": "MEDIUM", "min": 40, "max": 54.9},
            {"level": "HIGH", "min": 55, "max": 69.9},
            {"level": "VERY_HIGH", "min": 70, "max": 100},
        ],
        "country_risk_scores": {"united kingdom": 1, "united arab emirates": 2, "turkey": 3},
        "sector_risk_scores": {"government": 1, "investment management": 3, "private banking": 4},
        "entity_type_scores": {"listed company": 1, "limited liability company": 2, "trust": 4},
    }


def _inputs(**overrides):
    data = {
        "entity_type": "Listed Company", "ownership_structure": "Simple", "country": "United Kingdom",
        "sector": "Government", "directors": [], "ubos": [], "intermediary_shareholders": [],
        "operating_countries": ["United Kingdom"], "target_markets": ["United Kingdom"],
        "primary_service": "domestic payments", "monthly_volume": "Under USD 50,000 per month",
        "transaction_complexity": "Simple", "source_of_wealth": "business revenue",
        "source_of_funds": "company bank account", "introduction_method": "Direct application",
        "customer_interaction": "face-to-face",
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize("case", ["low", "medium", "high"])
def test_persisted_factor_ledger_reproduces_every_dimension_and_final_composite(case):
    inputs = _inputs()
    if case == "medium":
        inputs.update(country="United Arab Emirates", sector="Investment Management", monthly_volume="USD 500,000 to USD 5m per month")
    elif case == "high":
        inputs.update(entity_type="Trust", ownership_structure="Opaque", country="Turkey", sector="Private Banking", monthly_volume="Over USD 5m")

    result = compute_risk_score(inputs, config_override=_config())
    ledger = result["dimensions"]["factor_computation_evidence"]

    assert ledger["schema_version"] == "risk-factor-evidence-v1"
    assert [row["dimension_id"] for row in ledger["dimensions"]] == ["D1", "D2", "D3", "D4", "D5"]
    for dimension in ledger["dimensions"]:
        factors = [row for row in ledger["factors"] if row["dimension_id"] == dimension["dimension_id"]]
        for factor in factors:
            assert factor["weighted_factor_contribution"] == pytest.approx(
                factor["rule_score"] * factor["factor_weight"] / 100
            )
        factor_total = sum(row["weighted_factor_contribution"] for row in factors)
        assert factor_total + dimension["rounding_adjustment"] == pytest.approx(
            dimension["dimension_score"]
        )
        assert dimension["dimension_score"] == result["dimensions"][dimension["dimension_id"].lower()]
        assert all(set(row) == {
            "dimension_id", "factor_key", "factor_label", "raw_value", "normalized_value",
            "rule_score", "factor_weight", "weighted_factor_contribution", "resolution_status",
            "rule_identifier", "evidence_source",
        } for row in factors)

    reproduced = sum(row["composite_contribution"] for row in ledger["dimensions"]) + ledger["policy_adjustment"]
    assert reproduced == pytest.approx(result["score"])
    assert ledger["final_composite_score"] == result["score"]


def test_report_builder_fails_closed_for_one_missing_expected_factor():
    config = _config()
    result = compute_risk_score(_inputs(), config_override=config)
    dimensions = result["dimensions"]
    dimensions["factor_computation_evidence"]["factors"] = [
        row for row in dimensions["factor_computation_evidence"]["factors"]
        if row["factor_key"] != "source_of_funds"
    ]
    evidence = build_authoritative_risk_report_evidence({
        "risk_score": result["score"], "risk_level": result["level"],
        "risk_dimensions": dimensions, "risk_escalations": result["escalations"],
        "risk_computed_at": "2026-07-19T00:00:00Z", "risk_config_version": config["_config_version"],
        "onboarding_lane": result["lane"],
    }, config, approval_route={"route": "direct_low_medium", "reasons": []})

    assert evidence["available"] is False
    assert "factor_evidence_missing:D1:source_of_funds" in evidence["reason_codes"]


def test_report_builder_returns_only_the_persisted_factor_ledger():
    config = _config()
    result = compute_risk_score(_inputs(), config_override=config)
    evidence = build_authoritative_risk_report_evidence({
        "risk_score": result["score"], "risk_level": result["level"],
        "risk_dimensions": result["dimensions"], "risk_escalations": result["escalations"],
        "risk_computed_at": "2026-07-19T00:00:00Z", "risk_config_version": config["_config_version"],
        "onboarding_lane": result["lane"],
    }, config, approval_route={"route": "direct_low_medium", "reasons": []})

    assert evidence["available"] is True
    assert evidence["factor_evidence"] == result["dimensions"]["factor_computation_evidence"]["factors"]
    assert evidence["computation_evidence"]["final_composite_score"] == result["score"]


@pytest.mark.parametrize(
    "minimum_level,expected_score",
    [("HIGH", 55.0), ("VERY_HIGH", 70.0)],
)
def test_policy_floor_keeps_the_ledger_reconcilable_and_reports_the_adjustment(
    minimum_level, expected_score
):
    """A floor raises the stored score; the ledger must record it as a policy
    adjustment or the whole panel/PDF/CSV fails closed.

    Before this was recorded, an application floored up to HIGH or VERY_HIGH
    kept a ledger describing the pre-floor computation, so both reconciliation
    identities in build_authoritative_risk_report_evidence broke and the
    officer saw "Authoritative risk evidence is missing, incomplete, or stale"
    — on exactly the cases needing the most explanation, and permanently, since
    recomputing re-applies the same floor.
    """
    config = _config()
    result = compute_risk_score(_inputs(), config_override=config)
    base_score = result["score"]
    ledger = result["dimensions"]["factor_computation_evidence"]
    base_composite = ledger["base_composite_score"]

    apply_risk_floor(result, minimum_level, "floor_rule_test", "policy floor under test")

    assert result["score"] == expected_score, "floor must raise the stored score"
    # The pre-policy figure is untouched, so the officer-facing
    # "Original Score -> Final Score" strip can state the floor explicitly.
    assert ledger["base_composite_score"] == base_composite
    assert ledger["final_composite_score"] == expected_score
    assert ledger["policy_adjustment"] == pytest.approx(expected_score - base_score, abs=1e-4)

    reproduced = (
        sum(row["composite_contribution"] for row in ledger["dimensions"])
        + ledger["policy_adjustment"]
    )
    assert reproduced == pytest.approx(result["score"], abs=1e-4)

    # End to end through the real gate: the panel renders instead of blocking.
    evidence = build_authoritative_risk_report_evidence({
        "risk_score": result["score"], "risk_level": result["level"],
        "risk_dimensions": result["dimensions"], "risk_escalations": result["escalations"],
        "risk_computed_at": "2026-07-31T00:00:00Z", "risk_config_version": config["_config_version"],
        "onboarding_lane": result["lane"],
    }, config, approval_route={"route": "dual_control_required", "reasons": []})

    assert evidence["available"] is True, evidence.get("reason_codes")
    assert "composite_computation_mismatch" not in evidence.get("reason_codes", [])
    assert "final_composite_evidence_mismatch" not in evidence.get("reason_codes", [])
    assert evidence["computation_evidence"]["final_composite_score"] == result["score"]


def test_medium_floor_preserves_the_raw_score_and_leaves_the_ledger_untouched():
    """MEDIUM floors deliberately keep the raw score, so nothing to adjust."""
    config = _config()
    result = compute_risk_score(_inputs(), config_override=config)
    ledger = result["dimensions"]["factor_computation_evidence"]
    before = dict(ledger)

    apply_risk_floor(result, "MEDIUM", "floor_rule_test", "medium floor")

    assert result["score"] == before["final_composite_score"]
    assert ledger["policy_adjustment"] == before["policy_adjustment"]
    assert ledger["final_composite_score"] == before["final_composite_score"]


def _evidence_for(result, config):
    return build_authoritative_risk_report_evidence({
        "risk_score": result["score"], "risk_level": result["level"],
        "risk_dimensions": result["dimensions"], "risk_escalations": result["escalations"],
        "risk_computed_at": "2026-07-31T00:00:00Z", "risk_config_version": config["_config_version"],
        "onboarding_lane": result["lane"],
    }, config, approval_route={"route": "dual_control_required", "reasons": []})


def test_stacked_floors_telescope_instead_of_double_counting():
    """Two floors can fire on one result (EDD routing, then screening).

    Each delta is measured against the CURRENT score, so the adjustments
    accumulate to the total movement. Measuring from the original base instead
    would double-count the first floor and leave the ledger unreconcilable.
    """
    config = _config()
    result = compute_risk_score(_inputs(), config_override=config)
    ledger = result["dimensions"]["factor_computation_evidence"]
    base_score = result["score"]
    start_adjustment = ledger["policy_adjustment"]

    apply_risk_floor(result, "HIGH", "floor_rule_edd_routing", "first floor")
    assert result["score"] == 55.0
    apply_risk_floor(result, "VERY_HIGH", "floor_rule_screening", "second floor")
    assert result["score"] == 70.0

    assert ledger["policy_adjustment"] == pytest.approx(
        start_adjustment + (70.0 - base_score), abs=1e-4
    )
    assert ledger["final_composite_score"] == 70.0
    reproduced = (
        sum(row["composite_contribution"] for row in ledger["dimensions"])
        + ledger["policy_adjustment"]
    )
    assert reproduced == pytest.approx(70.0, abs=1e-4)
    assert _evidence_for(result, config)["available"] is True


def test_floor_updates_a_ledger_rebuilt_from_a_stored_row():
    """A risk result rebuilt from a persisted row carries only the nested ledger."""
    config = _config()
    result = compute_risk_score(_inputs(), config_override=config)
    base_score = result["score"]

    rebuilt = json.loads(json.dumps(result))
    rebuilt.pop("factor_computation_evidence", None)
    apply_risk_floor(rebuilt, "HIGH", "floor_rule_test", "floor")

    ledger = rebuilt["dimensions"]["factor_computation_evidence"]
    assert ledger["final_composite_score"] == 55.0
    assert ledger["policy_adjustment"] == pytest.approx(55.0 - base_score, abs=1e-4)
    assert _evidence_for(rebuilt, config)["available"] is True


def test_floor_updates_both_ledger_copies_once_they_are_distinct_objects():
    """A JSON round-trip de-aliases the two copies; both must stay in step."""
    config = _config()
    result = compute_risk_score(_inputs(), config_override=config)
    base_score = result["score"]

    deduped = json.loads(json.dumps(result))
    assert (
        deduped["factor_computation_evidence"]
        is not deduped["dimensions"]["factor_computation_evidence"]
    )
    apply_risk_floor(deduped, "HIGH", "floor_rule_test", "floor")

    for ledger in (
        deduped["factor_computation_evidence"],
        deduped["dimensions"]["factor_computation_evidence"],
    ):
        assert ledger["final_composite_score"] == 55.0
        assert ledger["policy_adjustment"] == pytest.approx(55.0 - base_score, abs=1e-4)


def test_floor_on_a_result_that_already_carries_a_policy_adjustment():
    """An in-engine elevation leaves a non-zero adjustment the floor must keep."""
    config = _config()
    result = compute_risk_score(
        _inputs(entity_type="Trust", ownership_structure="Opaque", country="Turkey",
                sector="Private Banking", monthly_volume="Over USD 5m"),
        config_override=config,
    )
    ledger = result["dimensions"]["factor_computation_evidence"]
    start_adjustment = ledger["policy_adjustment"]
    base_score = result["score"]

    apply_risk_floor(result, "VERY_HIGH", "floor_rule_test", "floor")

    assert ledger["policy_adjustment"] == pytest.approx(
        start_adjustment + (70.0 - base_score), abs=1e-4
    )
    reproduced = (
        sum(row["composite_contribution"] for row in ledger["dimensions"])
        + ledger["policy_adjustment"]
    )
    assert reproduced == pytest.approx(result["score"], abs=1e-4)
    assert _evidence_for(result, config)["available"] is True


def test_unknown_prior_score_leaves_the_ledger_untouched():
    """A missing score makes the delta meaningless; do not write a wrong one."""
    config = _config()
    result = compute_risk_score(_inputs(), config_override=config)
    ledger = result["dimensions"]["factor_computation_evidence"]
    before = dict(ledger)

    result["score"] = None
    apply_risk_floor(result, "HIGH", "floor_rule_test", "floor")

    assert ledger["policy_adjustment"] == before["policy_adjustment"]
    assert ledger["final_composite_score"] == before["final_composite_score"]


def test_a_floor_cannot_fabricate_a_missing_policy_adjustment():
    """A floor must never turn a correctly fail-closed ledger into an available one."""
    config = _config()
    result = compute_risk_score(_inputs(), config_override=config)
    ledger = result["dimensions"]["factor_computation_evidence"]
    ledger.pop("policy_adjustment")
    assert _evidence_for(result, config)["available"] is False

    apply_risk_floor(result, "HIGH", "floor_rule_test", "floor")

    assert "policy_adjustment" not in ledger
    assert _evidence_for(result, config)["available"] is False
