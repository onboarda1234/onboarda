"""RSMP Tier 0A exact controlled-value mapping and activation tests."""

import ast
import hashlib
import os
from pathlib import Path
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import environment
import risk_controlled_values
from risk_controlled_values import (
    ACTIVATION_FLAG,
    APPROVED_EXACT_ALIAS_ROWS,
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


_MISSING = object()


def _activation_flag_targets():
    """Return every live flag singleton used by this collected test process."""
    targets = []
    for target in (environment.flags, risk_controlled_values.flags):
        if all(target is not existing for existing in targets):
            targets.append(target)
    return targets


def _set_mapping_fidelity_state(enabled):
    """Set the environment override and all collected singleton caches together."""
    os.environ[ACTIVATION_FLAG] = "true" if enabled else "false"
    for target in _activation_flag_targets():
        target._cache[ACTIVATION_FLAG] = bool(enabled)


@pytest.fixture(autouse=True)
def restore_mapping_fidelity_state():
    """Restore the flag environment and caches after every test deterministically.

    Several earlier full-suite tests reload ``environment``.  Reloading replaces
    ``environment.flags``, while already-imported modules retain the previous
    singleton.  Snapshot both identities so this module cannot leak into, or be
    affected by, collection order.
    """
    env_before = os.environ.get(ACTIVATION_FLAG, _MISSING)
    cache_before = [
        (target, target._cache.get(ACTIVATION_FLAG, _MISSING))
        for target in _activation_flag_targets()
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


def _portal_constant(name):
    source = Path(__file__).parents[1] / "server.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return tuple(ast.literal_eval(node.value))
    raise AssertionError(f"{name} not found")


def _actual_portal_sector_options():
    source = (Path(__file__).parents[2] / "arie-portal.html").read_text(encoding="utf-8")
    select = re.search(r'<select id="f-sector"[^>]*>(.*?)</select>', source, re.DOTALL)
    assert select, "portal sector select not found"
    values = []
    for attrs, text in re.findall(r"<option([^>]*)>(.*?)</option>", select.group(1), re.DOTALL):
        explicit = re.search(r'value="([^"]*)"', attrs)
        value = explicit.group(1) if explicit else re.sub(r"<[^>]+>", "", text).strip()
        if value:
            values.append(value)
    return tuple(values)


@pytest.fixture
def mapping_fidelity():
    _set_mapping_fidelity_state(True)
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
    portal = set(_actual_portal_sector_options())
    # Private Banking is approved in Gate 0 and resolvable behind the flag, but
    # is not exposed as a new portal option before deliberate activation.
    assert portal == (set(SECTOR_RECORDS) - {"Private Banking"}) | set(UNRESOLVED_SECTOR_LABELS)
    assert set(SECTOR_RECORDS) - portal == {"Private Banking"}
    assert not (set(SECTOR_RECORDS) & set(UNRESOLVED_SECTOR_LABELS))
    assert portal == set(_portal_constant("PORTAL_SECTOR_OPTIONS"))


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
def test_every_runtime_registry_label_resolves_to_its_exact_seeded_score(family, records):
    for label, record in records.items():
        resolution = resolve_controlled_score(family, label)
        assert resolution.status == "mapped", label
        assert resolution.score == record["score"], label
        assert resolution.controlled_id == record["id"], label


def test_runtime_unimplemented_sector_labels_are_unresolved_and_never_default_to_two():
    for label in UNRESOLVED_SECTOR_LABELS:
        resolution = resolve_controlled_score("sector", label)
        assert resolution.status == "unresolved", label
        assert resolution.score is None, label
        assert resolution.controlled_id == "", label


def test_entity_collisions_use_exact_config_keys(mapping_fidelity):
    configured = {
        "regulated": 1,
        "regulated fund": 2,
        "unregulated fund": 3,
    }
    regulated = resolve_controlled_score(
        "entity_type", "Regulated Fund (CIS / Licensed)", configured_scores=configured
    )
    unregulated = resolve_controlled_score(
        "entity_type", "Unregulated Fund / SPV", configured_scores=configured
    )
    assert regulated.score == 2
    assert unregulated.score == 3


def test_sector_collision_labels_use_exact_seed_keys(mapping_fidelity):
    configured = {
        "retail": 2,
        "forex": 3,
        "bank": 1,
        "banking": 2,
        "precious": 3,
        "precious metals": 3,
    }
    assert resolve_controlled_score(
        "sector", "Forex / FX Trading (Retail)", configured_scores=configured
    ).score == 3
    assert resolve_controlled_score(
        "sector", "Banking-as-a-Service", configured_scores=configured
    ).score == 2
    assert resolve_controlled_score(
        "sector", "Precious Metals / Gems", configured_scores=configured
    ).score == 3


def test_corrected_gate0_catalogue_is_internally_consistent_and_hash_verified():
    gate0_path = Path(__file__).parents[2] / "docs/risk-programme/RSMP_GATE0_V4_FOUNDER_APPROVAL.md"
    gate0 = gate0_path.read_text(encoding="utf-8")
    sector = gate0.split("### 3.1 Sector — exact labels and scores", 1)[1].split(
        "### 3.2 Entity type — exact labels and scores", 1
    )[0]
    approved, lane_b = sector.split(
        "The following 22 current portal options have no Gate 0 v4 score", 1
    )
    approved_rows = re.findall(r"^\| ([^|]+?) \| ([1-4]) \|$", approved, re.MULTILINE)
    lane_b_rows = re.findall(
        r"^\| ([^|]+?) \| QUARANTINE — FOUNDER/COMPLIANCE DECISION REQUIRED \|$",
        lane_b,
        re.MULTILINE,
    )
    assert len(approved_rows) == 39
    assert len(lane_b_rows) == 22
    approved_scores = dict(approved_rows)
    assert approved_scores["Investment Management"] == "3"
    assert approved_scores["Cloud Services"] == "2"
    assert approved_scores["Private Banking"] == "4"
    assert approved_scores["Precious Metals / Gems"] == "3"
    assert "Investment Management" not in lane_b_rows
    assert "Cloud Services" not in lane_b_rows
    assert "`Private Banking` is a score-4 sector and the existing sector-score-4 High floor applies." in sector

    entity = gate0.split("### 3.2 Entity type — exact labels and scores", 1)[1].split(
        "### 3.3 Ownership — exact labels and scores", 1
    )[0]
    assert "| Unregulated Fund / SPV | 3 |" in entity

    hash_match = re.search(
        r"(?m)^\*\*Canonical Markdown SHA-256:\*\* `([0-9a-f]{64})`$", gate0
    )
    assert hash_match
    canonical = gate0[: hash_match.start(1)] + "{{CANONICAL_SHA256}}" + gate0[hash_match.end(1) :]
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == hash_match.group(1)


@pytest.mark.parametrize(
    "relative_path",
    [
        "docs/risk-programme/RSMP_TIER0A_FOUNDER_ALIAS_DECISIONS.md",
        "docs/risk-programme/RSMP_LIVE_CONFIG_DISPOSITION.md",
        "docs/audits/RSMP_TIER0A_CI_ISOLATION_FIX.md",
        "docs/risk-programme/RSMP_TIER0A_POST_APPROVAL_DRY_RUN.md",
    ],
)
def test_amended_founder_artifact_canonical_hashes_are_verified(relative_path):
    text = (Path(__file__).parents[2] / relative_path).read_text(encoding="utf-8")
    hash_match = re.search(
        r"(?m)^\*\*Canonical Markdown SHA-256:\*\* `([0-9a-f]{64})`$", text
    )
    assert hash_match
    canonical = (
        text[: hash_match.start(1)]
        + "{{CANONICAL_SHA256}}"
        + text[hash_match.end(1) :]
    )
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == hash_match.group(1)


@pytest.mark.parametrize(
    "family,label,score",
    [
        ("sector", "Family Office / Wealth Management", 3),
        ("sector", "Private Banking", 4),
        ("sector", "Investment Management", 3),
        ("sector", "Cloud Services", 2),
        ("sector", "Precious Metals / Gems", 3),
        ("entity_type", "Unregulated Fund / SPV", 3),
    ],
)
def test_founder_approved_config_contract_scores_are_fixed_behind_flag(family, label, score):
    configured = {
        "wealth management": 1,
        "private banking": 1,
        "investment management": 1,
        "cloud services": 4,
        "precious metals": 4,
        "unregulated fund": 4,
    }
    resolution = resolve_controlled_score(family, label, configured_scores=configured)
    assert resolution.status == "mapped"
    assert resolution.score == score


def _founder_alias_sections():
    path = Path(__file__).parents[2] / "docs/risk-programme/RSMP_TIER0A_FOUNDER_ALIAS_DECISIONS.md"
    text = path.read_text(encoding="utf-8")

    def rows(prefix, start, end):
        section = text.split(start, 1)[1].split(end, 1)[0]
        parsed = []
        for line in section.splitlines():
            if not line.startswith(f"| {prefix}-"):
                continue
            parsed.append(tuple(value.strip() for value in line.strip("|").split("|")))
        return parsed

    return (
        rows("A", "## A.", "## B."),
        rows("B", "## B.", "## C."),
        rows("C", "## C.", "## D."),
    )


def test_all_77_founder_approved_alias_rows_match_runtime_and_exact_scores():
    approved_rows, _, _ = _founder_alias_sections()
    documented = tuple(
        (row_id, family, legacy, canonical, int(score))
        for row_id, family, legacy, canonical, score, _count, decision in approved_rows
        if decision == "APPROVED"
    )
    assert len(documented) == 77
    assert documented == APPROVED_EXACT_ALIAS_ROWS
    for row_id, family, legacy, canonical, score in documented:
        resolution = resolve_controlled_score(family, legacy)
        assert resolution.status == "mapped", row_id
        assert resolution.canonical_label == canonical, row_id
        assert resolution.score == score, row_id


def test_quarantined_and_rejected_rows_remain_unresolved_without_score_two_fallback():
    _, quarantined, rejected = _founder_alias_sections()
    assert len(quarantined) == 105
    assert len(rejected) == 9
    for row in quarantined + rejected:
        row_id, family, legacy = row[:3]
        resolution = resolve_controlled_score(family, legacy)
        assert resolution.status == "unresolved", row_id
        assert resolution.score is None, row_id
        assert resolution.controlled_id == "", row_id


def test_alias_matching_does_not_use_fuzzy_or_substring_rules():
    for family, value in (
        ("sector", "Software consulting"),
        ("entity_type", "SME holding trust"),
        ("ownership", "Simple-ish ownership"),
        ("complexity", "Predictable international arrangement"),
        ("introduction", "Directly referred"),
        ("monthly_volume", "Approximately USD 25,000 monthly"),
    ):
        resolution = resolve_controlled_score(family, value)
        assert resolution.status == "unresolved", (family, value)
        assert resolution.score is None, (family, value)


def test_private_banking_score_four_preserves_existing_sector_high_floor(mapping_fidelity):
    result = compute_risk_score(_base_input(sector="Private Banking"))
    assert result["dimensions"]["d4"] == 4
    assert result["level"] == "HIGH"
    assert "floor_rule_high_risk_sector" in result["escalations"]
    assert "very_high_risk_sector" in result["escalations"]


def test_founder_config_contract_changes_are_inert_while_flag_is_off():
    # Pin the legacy configuration instead of inheriting whichever disposable
    # test DB a preceding module happened to seed. Its key order mirrors the
    # existing seed and therefore preserves the flag-OFF substring behavior.
    legacy_config = {
        "country_risk_scores": {"united kingdom": 1},
        "sector_risk_scores": {"bank": 1, "software": 2},
        "entity_type_scores": {
            "regulated": 1,
            "unregulated fund": 4,
            "listed company": 1,
        },
    }
    _set_mapping_fidelity_state(False)
    legacy_sector = compute_risk_score(
        _base_input(sector="Private Banking"), config_override=legacy_config
    )
    legacy_entity = compute_risk_score(
        _base_input(entity_type="Unregulated Fund / SPV"),
        config_override=legacy_config,
    )
    assert legacy_sector["dimensions"]["d4"] == 1
    assert legacy_entity["dimensions"]["d1"] == pytest.approx(1.0)

    _set_mapping_fidelity_state(True)
    approved_sector = compute_risk_score(
        _base_input(sector="Private Banking"), config_override=legacy_config
    )
    approved_entity = compute_risk_score(
        _base_input(entity_type="Unregulated Fund / SPV"),
        config_override=legacy_config,
    )
    assert approved_sector["dimensions"]["d4"] == 4
    assert approved_entity["dimensions"]["d1"] == pytest.approx(1.4)


def test_unsolicited_score_four_requires_review_without_floor_or_volume_reason(mapping_fidelity):
    result = compute_risk_score(
        _base_input(introduction_method="Unsolicited / unknown referral source")
    )
    assert result["dimensions"]["d5"] == pytest.approx(2.5)
    assert result["base_risk_level"] == result["level"]
    assert result["level"] == "LOW"
    assert result["requires_compliance_approval"] is True
    assert "sub_factor_score_4" in result["escalations"]
    assert not any(reason.startswith("floor_rule_") for reason in result["escalations"])
    assert "monthly_volume_score_4" not in result["escalations"]


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


def test_feature_flag_changes_formatted_volume_only_when_enabled():
    _set_mapping_fidelity_state(False)
    legacy = compute_risk_score(_base_input())
    _set_mapping_fidelity_state(True)
    exact = compute_risk_score(_base_input())
    assert legacy["dimensions"]["d3"] == pytest.approx(1.35)
    assert exact["dimensions"]["d3"] == pytest.approx(1.0)
