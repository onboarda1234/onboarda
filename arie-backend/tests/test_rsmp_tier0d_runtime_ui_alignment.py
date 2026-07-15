"""Tier 0D: the Back Office model is a read-only projection of runtime truth."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

import environment
import risk_controlled_values
from edd_routing_policy import ALL_TRIGGERS, POLICY_VERSION as EDD_POLICY_VERSION
from periodic_review_policy import ENHANCED_REVIEW_FLOOR_MONTHS, RISK_FREQUENCY_MONTHS
from risk_controlled_values import FAMILY_RECORDS, UNRESOLVED_SECTOR_LABELS
from risk_model_view import READ_ONLY_MESSAGE, build_runtime_risk_model_view
from rule_engine import _score_entity_type, classify_country, compute_risk_score, load_risk_config, score_sector
from rule_engine import (
    ADVERSE_MEDIA_CLEAR_VALUES,
    ADVERSE_MEDIA_SCORE_2_KEYWORDS,
    ADVERSE_MEDIA_SCORE_4_KEYWORDS,
    DELIVERY_REMOTE_KEYWORDS,
    DELIVERY_SCORE_1_KEYWORDS,
    DELIVERY_SCORE_2_KEYWORDS,
    DELIVERY_SCORE_4_KEYWORDS,
    SERVICE_SCORE_2_KEYWORDS,
    SERVICE_SCORE_3_KEYWORDS,
    SOURCE_OF_FUNDS_SCORE_MAP,
    SOURCE_OF_FUNDS_UNKNOWN_VALUES,
    SOURCE_OF_WEALTH_SCORE_MAP,
    SOURCE_OF_WEALTH_UNKNOWN_VALUES,
)
from security_hardening import (
    APPROVAL_ROUTE_BLOCKED,
    APPROVAL_ROUTE_COMPLIANCE_REQUIRED,
    APPROVAL_ROUTE_DIRECT_LOW_MEDIUM,
    APPROVAL_ROUTE_DUAL_CONTROL_REQUIRED,
    DIRECT_APPROVAL_RISK_LEVELS,
)


ACTIVATION_FLAG = risk_controlled_values.ACTIVATION_FLAG
_MISSING = object()
_FACTOR_LOCATION = {
    "ownership": ("D1", 1, "d1"),
    "pep": ("D1", 2, "d1"),
    "adverse_media": ("D1", 3, "d1"),
    "source_of_wealth": ("D1", 4, "d1"),
    "source_of_funds": ("D1", 5, "d1"),
    "service_type": ("D3", 0, "d3"),
    "monthly_volume": ("D3", 1, "d3"),
    "complexity": ("D3", 2, "d3"),
    "introduction": ("D5", 0, "d5"),
    "delivery_channel": ("D5", 1, "d5"),
}


def _flag_targets():
    targets = []
    for target in (environment.flags, risk_controlled_values.flags):
        if all(target is not existing for existing in targets):
            targets.append(target)
    return targets


def _set_activation(enabled: bool):
    os.environ[ACTIVATION_FLAG] = "true" if enabled else "false"
    for target in _flag_targets():
        target._cache[ACTIVATION_FLAG] = enabled


@pytest.fixture(autouse=True)
def _restore_activation_state():
    env_before = os.environ.get(ACTIVATION_FLAG, _MISSING)
    cache_before = [
        (target, target._cache.get(ACTIVATION_FLAG, _MISSING))
        for target in _flag_targets()
    ]
    try:
        yield
    finally:
        if env_before is _MISSING:
            os.environ.pop(ACTIVATION_FLAG, None)
        else:
            os.environ[ACTIVATION_FLAG] = env_before
        for target, previous in cache_before:
            if previous is _MISSING:
                target._cache.pop(ACTIVATION_FLAG, None)
            else:
                target._cache[ACTIVATION_FLAG] = previous


def _base_input():
    return {
        "entity_type": "Listed Company on Regulated Exchange",
        "ownership_structure": "Simple — direct identifiable UBOs",
        "country": "united kingdom",
        "sector": "Government / Public Sector",
        "directors": [],
        "ubos": [],
        "primary_service": "Multi-currency",
        "monthly_volume": "USD 50,000 to USD 500,000 per month",
        "transaction_complexity": "Standard — multi-currency, established corridors",
        "introduction_method": "Direct application — client initiated",
        "customer_interaction": "Video",
        "source_of_wealth": "Business revenue",
        "source_of_funds": "Company bank transfer",
        "adverse_media": "clear",
    }


def _isolated_score(config, family, runtime_input):
    dimension_id, sub_index, result_key = _FACTOR_LOCATION[family]
    isolated = deepcopy(config)
    for dimension in isolated["dimensions"]:
        if dimension["id"] != dimension_id:
            continue
        for index, subcriterion in enumerate(dimension["subcriteria"]):
            subcriterion["weight"] = 100 if index == sub_index else 0
    payload = _base_input()
    payload.update(runtime_input)
    result = compute_risk_score(payload, config_override=isolated)
    score = float(result["dimensions"][result_key])
    return int(score) if score.is_integer() else score


def _runtime_model(temp_db, *, activation=False):
    _set_activation(activation)
    config = load_risk_config()
    assert config
    return config, build_runtime_risk_model_view(config)


def test_activation_flag_remains_off_by_default():
    os.environ.pop(ACTIVATION_FLAG, None)
    for target in _flag_targets():
        target._cache.pop(ACTIVATION_FLAG, None)
    assert risk_controlled_values.mapping_fidelity_enabled() is False


@pytest.mark.parametrize("activation", [False, True])
def test_every_displayed_score_is_recomputed_by_the_runtime(temp_db, activation):
    config, model = _runtime_model(temp_db, activation=activation)
    for family, items in model["catalogs"].items():
        for item in items:
            assert item["ui_score"] == item["runtime_score"]
            assert item["match"] is True
            if family == "sector":
                expected = score_sector(item["runtime_input"]["sector"], config["sector_risk_scores"])
            elif family == "entity_type":
                expected = _score_entity_type(
                    item["runtime_input"]["entity_type"], config["entity_type_scores"]
                )
            elif family == "country":
                expected = classify_country(
                    item["runtime_input"]["country"], config["country_risk_scores"]
                )
            else:
                expected = _isolated_score(config, family, item["runtime_input"])
            assert item["runtime_score"] == expected, (family, item["label"])


def test_every_displayed_label_is_runtime_owned_and_unique(temp_db):
    config, model = _runtime_model(temp_db)
    for family, items in model["catalogs"].items():
        labels = [item["label"] for item in items]
        assert len(labels) == len(set(labels)), family
        for item in items:
            if family in FAMILY_RECORDS:
                if item["classification"] == "Runtime only":
                    config_field = "sector_risk_scores" if family == "sector" else "entity_type_scores"
                    assert item["label"] in config[config_field]
                else:
                    assert item["label"] in FAMILY_RECORDS[family]
            elif family == "country":
                assert item["label"] in config["country_risk_scores"]
            elif family == "pep":
                assert item["structured_evidence_path"] == "pep_declaration.pep_role_type"
            else:
                assert item["runtime_catalog_key"]
                assert item["runtime_source"].startswith("rule_engine.compute_risk_score")


def test_inline_parser_catalogs_are_complete_and_runtime_owned(temp_db):
    _, model = _runtime_model(temp_db)
    expected = {
        "adverse_media": set(ADVERSE_MEDIA_SCORE_4_KEYWORDS)
        | set(ADVERSE_MEDIA_SCORE_2_KEYWORDS)
        | set(ADVERSE_MEDIA_CLEAR_VALUES)
        | {"Default branch (unrecognised or missing)"},
        "source_of_wealth": set(SOURCE_OF_WEALTH_SCORE_MAP)
        | set(SOURCE_OF_WEALTH_UNKNOWN_VALUES)
        | {"Default unmatched declared value"},
        "source_of_funds": set(SOURCE_OF_FUNDS_SCORE_MAP)
        | set(SOURCE_OF_FUNDS_UNKNOWN_VALUES)
        | {"Default unmatched declared value"},
        "service_type": {"domestic + single", "cross_border = true", "Default unmatched or missing value"}
        | set(SERVICE_SCORE_2_KEYWORDS)
        | set(SERVICE_SCORE_3_KEYWORDS),
        "delivery_channel": set(DELIVERY_SCORE_1_KEYWORDS)
        | set(DELIVERY_SCORE_2_KEYWORDS)
        | set(DELIVERY_REMOTE_KEYWORDS)
        | set(DELIVERY_SCORE_4_KEYWORDS)
        | {"remote + incorporation country score >= 3", "Default unmatched or missing value"},
    }
    for family, labels in expected.items():
        assert {item["label"] for item in model["catalogs"][family]} == labels

    source = Path(__file__).resolve().parents[1].joinpath("rule_engine.py").read_text(encoding="utf-8")
    assert "for k, v in SOURCE_OF_WEALTH_SCORE_MAP.items()" in source
    assert "for k, v in SOURCE_OF_FUNDS_SCORE_MAP.items()" in source
    assert "ADVERSE_MEDIA_SCORE_4_KEYWORDS" in source
    assert "SERVICE_DOMESTIC_REQUIRED_KEYWORDS" in source
    assert "DELIVERY_SCORE_4_KEYWORDS" in source
    assert "_sow_map =" not in source
    assert "_sof_map =" not in source


def test_dimensions_thresholds_and_metadata_are_the_runtime_objects(temp_db):
    config, model = _runtime_model(temp_db)
    assert model["dimensions"] == config["dimensions"]
    assert model["thresholds"] == config["thresholds"]
    assert model["runtime_source"]["config_loader"] == "rule_engine.load_risk_config"
    assert model["runtime_source"]["scorer"] == "rule_engine.compute_risk_score"
    assert model["runtime_source"]["config_version"] == config["_config_version"]
    assert model["read_only"] is True
    assert model["message"] == READ_ONLY_MESSAGE


def test_projection_probes_do_not_emit_synthetic_risk_events(temp_db, caplog):
    _set_activation(True)
    config = load_risk_config()
    caplog.set_level("INFO", logger="arie")
    build_runtime_risk_model_view(config)
    messages = [record.getMessage() for record in caplog.records]
    assert not any("RISK FLOOR" in message for message in messages)
    assert not any("ELEVATION RULE" in message for message in messages)


def test_high_floors_and_explicit_no_floor_cases_match_runtime(temp_db):
    _set_activation(True)
    config = load_risk_config()

    sector = _base_input()
    sector["sector"] = "Crypto / Digital Assets Exchange"
    sector_result = compute_risk_score(sector, config_override=config)
    assert sector_result["dimensions"]["d4"] == 4
    assert sector_result["level"] in {"HIGH", "VERY_HIGH"}
    assert "floor_rule_high_risk_sector" in sector_result["escalations"]

    ownership = _base_input()
    ownership["ownership_structure"] = "Opaque — UBOs cannot be fully identified"
    ownership_result = compute_risk_score(ownership, config_override=config)
    assert ownership_result["level"] in {"HIGH", "VERY_HIGH"}
    assert "floor_rule_opaque_ownership" in ownership_result["escalations"]

    pep = _base_input()
    pep["directors"] = [{
        "client_declared_pep": True,
        "pep_declaration": {
            "client_declared_pep": True,
            "pep_status": "declared_yes",
            "pep_role_type": "foreign_pep",
        },
    }]
    pep_result = compute_risk_score(pep, config_override=config)
    assert pep_result["level"] in {"HIGH", "VERY_HIGH"}
    assert "floor_rule_declared_pep" in pep_result["escalations"]

    volume = _base_input()
    volume["monthly_volume"] = "Over USD 5,000,000 per month"
    volume_result = compute_risk_score(volume, config_override=config)
    assert "monthly_volume_score_4" in volume_result["escalations"]
    assert "sub_factor_score_4" not in volume_result["escalations"]
    assert volume_result["requires_compliance_approval"] is True
    assert volume_result["level"] in {"LOW", "MEDIUM"}

    unsolicited = _base_input()
    unsolicited["introduction_method"] = "Unsolicited / unknown referral source"
    unsolicited_result = compute_risk_score(unsolicited, config_override=config)
    assert unsolicited_result["dimensions"]["d5"] < 4  # 50% score-4 factor, 50% delivery score.
    assert "sub_factor_score_4" in unsolicited_result["escalations"]
    assert unsolicited_result["level"] in {"LOW", "MEDIUM"}


def test_every_hidden_rule_and_adjacent_policy_is_documented(temp_db):
    _, model = _runtime_model(temp_db, activation=True)
    rule_ids = {row["id"] for row in model["rules"]}
    assert {
        "sector_score_4_high_floor",
        "sector_keyword_high_floor",
        "opaque_ownership_high_floor",
        "declared_pep_high_floor",
        "monthly_volume_score_4_review",
        "unsolicited_referral_no_floor",
        "country_score_3_high_floor",
        "country_score_4_very_high_floor",
        "material_screening_high_floor",
        "screening_severe_combination",
        "unresolved_mapping_block",
        "composite_85_review",
    } <= rule_ids
    assert model["edd_policy"]["version"] == EDD_POLICY_VERSION
    assert model["edd_policy"]["triggers"] == list(ALL_TRIGGERS)
    assert model["approval_policy"]["direct_risk_levels"] == sorted(DIRECT_APPROVAL_RISK_LEVELS)
    assert model["approval_policy"]["routes"] == [
        APPROVAL_ROUTE_DIRECT_LOW_MEDIUM,
        APPROVAL_ROUTE_COMPLIANCE_REQUIRED,
        APPROVAL_ROUTE_DUAL_CONTROL_REQUIRED,
        APPROVAL_ROUTE_BLOCKED,
    ]
    assert model["monitoring_policy"]["review_frequency_months"] == RISK_FREQUENCY_MONTHS
    assert model["monitoring_policy"]["enhanced_review_floor_months"] == ENHANCED_REVIEW_FLOOR_MONTHS


def test_lane_b_is_excluded_from_every_active_catalog(temp_db):
    _, model = _runtime_model(temp_db, activation=True)
    active_labels = {item["label"] for item in model["catalogs"]["sector"]}
    lane_b = model["lane_b"]["items"]
    assert {item["label"] for item in lane_b} == set(UNRESOLVED_SECTOR_LABELS)
    assert active_labels.isdisjoint(UNRESOLVED_SECTOR_LABELS)
    assert all(item["runtime_score"] is None for item in lane_b)
    assert all(item["active_runtime_entry"] is False for item in lane_b)
    assert all(item["classification"] == "Lane B" for item in lane_b)


def test_backoffice_risk_model_has_no_ui_score_source_or_editor():
    html = Path(__file__).resolve().parents[2].joinpath("arie-backoffice.html").read_text(
        encoding="utf-8"
    )
    assert READ_ONLY_MESSAGE in html.replace("<br>\n      ", " ")
    assert "var RISK_THRESHOLDS = [];" in html
    assert "var RISK_DIMENSIONS = [];" in html
    assert "var SECTOR_RISK_CONFIG = [];" in html
    assert "var ENTITY_TYPE_SCORES = [];" in html
    assert "applyRuntimeRiskModelPayload(riskResp);" in html
    assert "var model = payload && payload.runtime_model;" in html
    assert "Runtime-owned risk model unavailable. No fallback model is displayed." in html
    assert "btn-edit-risk" not in html
    assert "btn-edit-countries" not in html
    assert "btn-edit-sectors" not in html
    assert "btn-edit-entities" not in html
    assert "toggleRiskEditMode" not in html
    assert "saveRiskModel" not in html
    assert "boApiCall('PUT', '/config/risk-model'" not in html


def test_risk_config_get_uses_runtime_loader_and_projection():
    source = Path(__file__).resolve().parents[1].joinpath("server.py").read_text(encoding="utf-8")
    start = source.index("class RiskConfigHandler")
    end = source.index("class CountryRiskConfigHandler", start)
    handler = source[start:end]
    assert "config = load_risk_config()" in handler
    assert "build_runtime_risk_model_view(config)" in handler
    assert '"runtime_model"' in handler
    assert 'SELECT * FROM risk_config WHERE id=1' not in handler
