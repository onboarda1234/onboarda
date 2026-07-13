"""RSMP Tier 0A exact controlled-value mapping and activation tests."""

import ast
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import environment
from risk_controlled_values import (
    ACTIVATION_FLAG,
    COMPLEXITY_RECORDS,
    ENTITY_TYPE_RECORDS,
    INTRODUCTION_RECORDS,
    MONTHLY_VOLUME_RECORDS,
    OWNERSHIP_RECORDS,
    SECTOR_RECORDS,
    UNRESOLVED_SECTOR_LABELS,
    mapping_fidelity_enabled,
    resolve_controlled_score,
)
from rule_engine import classify_country, compute_risk_score, normalize_country_key


def _portal_constant(name):
    source = Path(__file__).parents[1] / "server.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return tuple(ast.literal_eval(node.value))
    raise AssertionError(f"{name} not found")


@pytest.fixture
def mapping_fidelity(monkeypatch):
    monkeypatch.setitem(environment.flags._cache, ACTIVATION_FLAG, True)
    assert mapping_fidelity_enabled() is True


def _base_input(**overrides):
    data = {
        "entity_type": "Listed Company on Regulated Exchange",
        "ownership_structure": "Simple — direct identifiable UBOs",
        "country": "United Kingdom",
        "sector": "Software / SaaS",
        "directors": [],
        "ubos": [],
        "intermediary_shareholders": [],
        "operating_countries": [],
        "target_markets": [],
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


def test_activation_flag_is_off_by_default():
    assert environment._DEFAULT_FLAGS
    assert all(
        defaults[ACTIVATION_FLAG] is False
        for defaults in environment._DEFAULT_FLAGS.values()
    )


def test_every_portal_sector_has_an_explicit_mapped_or_unresolved_disposition():
    portal = set(_portal_constant("PORTAL_SECTOR_OPTIONS"))
    assert portal == set(SECTOR_RECORDS) | set(UNRESOLVED_SECTOR_LABELS)
    assert not (set(SECTOR_RECORDS) & set(UNRESOLVED_SECTOR_LABELS))


@pytest.mark.parametrize(
    "family,records",
    [
        ("sector", SECTOR_RECORDS),
        ("entity_type", ENTITY_TYPE_RECORDS),
        ("ownership", OWNERSHIP_RECORDS),
        ("complexity", COMPLEXITY_RECORDS),
        ("introduction", INTRODUCTION_RECORDS),
        ("monthly_volume", MONTHLY_VOLUME_RECORDS),
    ],
)
def test_every_approved_label_resolves_to_its_exact_seeded_score(family, records):
    for label, record in records.items():
        resolution = resolve_controlled_score(family, label)
        assert resolution.status == "mapped", label
        assert resolution.score == record["score"], label
        assert resolution.controlled_id == record["id"], label


def test_lane_b_sector_labels_are_unresolved_and_never_default_to_two():
    for label in UNRESOLVED_SECTOR_LABELS:
        resolution = resolve_controlled_score("sector", label)
        assert resolution.status == "unresolved", label
        assert resolution.score is None, label
        assert resolution.controlled_id == "", label


def test_entity_collisions_use_exact_config_keys(mapping_fidelity):
    configured = {
        "regulated": 1,
        "regulated fund": 2,
        "unregulated fund": 4,
    }
    regulated = resolve_controlled_score(
        "entity_type", "Regulated Fund (CIS / Licensed)", configured_scores=configured
    )
    unregulated = resolve_controlled_score(
        "entity_type", "Unregulated Fund / SPV", configured_scores=configured
    )
    assert regulated.score == 2
    assert unregulated.score == 4


def test_sector_collision_labels_use_exact_seed_keys(mapping_fidelity):
    configured = {
        "retail": 2,
        "forex": 3,
        "bank": 1,
        "banking": 2,
        "precious": 3,
        "precious metals": 4,
    }
    assert resolve_controlled_score(
        "sector", "Forex / FX Trading (Retail)", configured_scores=configured
    ).score == 3
    assert resolve_controlled_score(
        "sector", "Banking-as-a-Service", configured_scores=configured
    ).score == 2
    assert resolve_controlled_score(
        "sector", "Precious Metals / Gems", configured_scores=configured
    ).score == 4


def test_a9_is_rename_only_and_preserves_score_floor_and_legacy_alias(mapping_fidelity):
    current = compute_risk_score(
        _base_input(ownership_structure="Opaque — UBOs cannot be fully identified")
    )
    legacy = compute_risk_score(
        _base_input(ownership_structure="Complex multi-jurisdiction / opaque structure")
    )
    for result in (current, legacy):
        assert result["dimensions"]["d1"] == pytest.approx(1.6)
        assert result["level"] == "HIGH"
        assert "floor_rule_opaque_ownership" in result["escalations"]
        assert result["ownership_transparency_status"] == "opaque"
    assert current["score"] == legacy["score"]
    assert current["level"] == legacy["level"]


@pytest.mark.parametrize(
    "raw,canonical,score",
    [
        ("Hong Kong SAR", "hong kong", 1),
        ("Congo (DRC)", "democratic republic of congo", 3),
        ("Türkiye", "turkey", 2),
    ],
)
def test_d1a_geography_aliases_use_exact_seeded_score(mapping_fidelity, raw, canonical, score):
    configured = {
        "hong kong": 1,
        "democratic republic of congo": 3,
        "turkey": 2,
    }
    assert normalize_country_key(raw) == canonical
    assert classify_country(raw, configured) == score


def test_deferred_geography_is_not_remapped_by_tier0a(mapping_fidelity):
    assert normalize_country_key("Albania") == "albania"
    assert normalize_country_key("Africa") == "africa"


@pytest.mark.parametrize(
    "family,label,expected_score",
    [
        ("monthly_volume", "Under USD 50,000 per month", 1),
        ("monthly_volume", "USD 50,000 to USD 500,000 per month", 2),
        ("monthly_volume", "USD 500,000 to USD 5,000,000 per month", 3),
        ("monthly_volume", "Over USD 5,000,000 per month", 4),
        ("complexity", "Very complex — includes monitored corridors", 4),
        ("introduction", "Introduced by non-regulated intermediary", 3),
    ],
)
def test_formatted_portal_values_resolve_exactly(family, label, expected_score):
    resolution = resolve_controlled_score(family, label)
    assert resolution.status == "mapped"
    assert resolution.score == expected_score


def test_feature_flag_changes_formatted_volume_only_when_enabled(monkeypatch):
    monkeypatch.setitem(environment.flags._cache, ACTIVATION_FLAG, False)
    legacy = compute_risk_score(_base_input())
    monkeypatch.setitem(environment.flags._cache, ACTIVATION_FLAG, True)
    exact = compute_risk_score(_base_input())
    assert legacy["dimensions"]["d3"] == pytest.approx(1.35)
    assert exact["dimensions"]["d3"] == pytest.approx(1.0)
