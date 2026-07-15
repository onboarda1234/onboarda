"""Focused acceptance tests for RSMP PR-1b PEP runtime alignment."""

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from edd_routing_policy import ROUTE_EDD, TRIGGER_DECLARED_PEP, evaluate_edd_routing
from rule_engine import (
    GATE0_DECLARED_PEP_SCORE,
    _declared_pep_score_evidence,
    compute_risk_score,
)
from security_hardening import (
    APPROVAL_ROUTE_DIRECT_LOW_MEDIUM,
    APPROVAL_ROUTE_DUAL_CONTROL_REQUIRED,
    classify_approval_route,
)


APPROVED_PEP_ROLES = (
    ("domestic_pep", "Domestic PEP"),
    ("foreign_pep", "Foreign PEP"),
    ("international_organisation_pep", "International Organisation PEP"),
    ("family_member", "Family Member"),
    ("close_associate", "Close Associate"),
)


def _config():
    return {
        "country_risk_scores": {"united kingdom": 1},
        "sector_risk_scores": {"government": 1},
        "entity_type_scores": {"listed company": 1},
    }


def _base_input(**overrides):
    data = {
        "entity_type": "Listed Company on Regulated Exchange",
        "ownership_structure": "Simple — direct identifiable UBOs",
        "country": "United Kingdom",
        "sector": "Government / Public Sector",
        "directors": [],
        "ubos": [],
        "intermediary_shareholders": [],
        "operating_countries": ["United Kingdom"],
        "target_markets": ["United Kingdom"],
        "primary_service": "domestic payments only (single currency)",
        "monthly_volume": "Under USD 50,000 per month",
        "transaction_complexity": "Simple — single currency, domestic corridors",
        "source_of_wealth": "business revenue",
        "source_of_funds": "company bank account",
        "introduction_method": "Direct application — client initiated",
        "customer_interaction": "face-to-face",
    }
    data.update(overrides)
    return data


def _declared_person(role_type):
    return {
        "full_name": "Gate Zero PEP",
        "nationality": "British",
        "is_pep": "Yes",
        "pep_declaration": {
            "declared_pep": True,
            "client_declared_pep": True,
            "pep_status": "declared_yes",
            "pep_role_type": role_type,
            "position_title": "Public function",
        },
    }


@pytest.mark.parametrize(
    "role_type",
    [pytest.param(role, id=label) for role, label in APPROVED_PEP_ROLES],
)
def test_every_approved_declared_pep_role_scores_exactly_four_and_preserves_role_evidence(
    role_type
):
    person = _declared_person(role_type)
    before = copy.deepcopy(person["pep_declaration"])

    evidence = _declared_pep_score_evidence(person)
    result = compute_risk_score(
        _base_input(directors=[person]),
        config_override=_config(),
    )

    assert evidence == {
        "pep_role_type": role_type,
        "score": GATE0_DECLARED_PEP_SCORE,
    }
    assert GATE0_DECLARED_PEP_SCORE == 4
    assert person["pep_declaration"] == before
    assert result["dimensions"]["d1"] == pytest.approx(1.75)
    assert result["declared_pep_present"] is True


def test_nested_pep_role_is_the_authoritative_runtime_path_with_legacy_fallback_only():
    nested = _declared_person("domestic_pep")
    nested["pep_type"] = "stale_top_level_foreign_pep"
    legacy = {
        "is_pep": "Yes",
        "pep_type": "foreign_pep",
    }

    assert _declared_pep_score_evidence(nested) == {
        "pep_role_type": "domestic_pep",
        "score": 4,
    }
    assert _declared_pep_score_evidence(legacy) == {
        "pep_role_type": "foreign_pep",
        "score": 4,
    }


def test_declared_pep_high_floor_edd_and_approval_routing_are_unchanged():
    result = compute_risk_score(
        _base_input(directors=[_declared_person("family_member")]),
        config_override=_config(),
    )

    assert result["base_risk_level"] == "LOW"
    assert result["final_risk_level"] == "HIGH"
    assert result["lane"] == "EDD"
    assert "floor_rule_declared_pep" in result["escalations"]
    assert "sub_factor_score_4" in result["escalations"]
    assert "monthly_volume_score_4" not in result["escalations"]

    edd = evaluate_edd_routing({
        "final_risk_level": result["final_risk_level"],
        "declared_pep_present": result["declared_pep_present"],
        "sector_risk_tier": result["sector_risk_tier"],
        "sector_label": result["sector_label"],
        "jurisdiction_risk_tier": result["jurisdiction_risk_tier"],
        "ownership_transparency_status": result["ownership_transparency_status"],
        "screening_terminality_summary": {
            "terminal": True,
            "has_terminal_match": False,
            "has_non_terminal": False,
        },
        "edd_trigger_flags": [],
        "supervisor_mandatory_escalation": False,
    })
    assert edd["route"] == ROUTE_EDD
    assert TRIGGER_DECLARED_PEP in edd["triggers"]

    approval = classify_approval_route({
        "id": "rsmp-pr1b-declared-pep",
        "status": "compliance_review",
        "risk_level": result["final_risk_level"],
        "final_risk_level": result["final_risk_level"],
        "risk_escalations": json.dumps(result["escalations"]),
    })
    assert approval["route"] == APPROVAL_ROUTE_DUAL_CONTROL_REQUIRED
    assert approval["requires_dual_control"] is True


def test_non_pep_and_provider_only_pep_evidence_remain_non_declared_and_direct():
    non_pep = {
        "is_pep": "No",
        "pep_declaration": {
            "declared_pep": False,
            "client_declared_pep": False,
            "pep_status": "declared_no",
            "pep_role_type": "foreign_pep",
        },
    }
    provider_only = {
        "is_pep": "Yes",
        "pep_declaration": {
            "declared_pep": False,
            "client_declared_pep": False,
            "pep_status": "declared_no",
            "provider_detected_pep": True,
            "pep_role_type": "foreign_pep",
        },
    }

    assert _declared_pep_score_evidence(non_pep) is None
    assert _declared_pep_score_evidence(provider_only) is None

    result = compute_risk_score(
        _base_input(directors=[non_pep]),
        config_override=_config(),
    )
    assert result["dimensions"]["d1"] == pytest.approx(1.0)
    assert result["declared_pep_present"] is False
    assert result["final_risk_level"] == "LOW"
    assert "floor_rule_declared_pep" not in result["escalations"]

    approval = classify_approval_route({
        "id": "rsmp-pr1b-non-pep",
        "status": "compliance_review",
        "risk_level": result["final_risk_level"],
        "final_risk_level": result["final_risk_level"],
        "risk_escalations": json.dumps(result["escalations"]),
    })
    assert approval["route"] == APPROVAL_ROUTE_DIRECT_LOW_MEDIUM
