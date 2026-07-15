"""RSMP Tier 0B fail-closed mapping and volume-routing acceptance tests."""

import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import environment
import risk_controlled_values
from observability import clear_request_id, set_request_id
from risk_controlled_values import (
    ACTIVATION_FLAG,
    UNMAPPED_SENTINEL_PREFIX,
    reconcile_mapping_staleness,
)
from rule_engine import compute_risk_score
from security_hardening import (
    APPROVAL_ROUTE_BLOCKED,
    APPROVAL_ROUTE_COMPLIANCE_REQUIRED,
    APPROVAL_ROUTE_DIRECT_LOW_MEDIUM,
    APPROVAL_ROUTE_DUAL_CONTROL_REQUIRED,
    ApprovalGateValidator,
    can_decide_application,
    classify_approval_route,
)


_MISSING = object()


def _activation_flag_targets():
    targets = []
    for target in (environment.flags, risk_controlled_values.flags):
        if all(target is not existing for existing in targets):
            targets.append(target)
    return targets


@pytest.fixture
def tier0b_enabled():
    """Enable Tier 0B across reloaded flag singletons, then restore all state."""
    env_before = os.environ.get(ACTIVATION_FLAG, _MISSING)
    cache_before = [
        (target, target._cache.get(ACTIVATION_FLAG, _MISSING))
        for target in _activation_flag_targets()
    ]
    os.environ[ACTIVATION_FLAG] = "true"
    for target, _previous in cache_before:
        target._cache[ACTIVATION_FLAG] = True
    try:
        yield
    finally:
        clear_request_id()
        if env_before is _MISSING:
            os.environ.pop(ACTIVATION_FLAG, None)
        else:
            os.environ[ACTIVATION_FLAG] = env_before
        for target, previous in cache_before:
            if previous is _MISSING:
                target._cache.pop(ACTIVATION_FLAG, None)
            else:
                target._cache[ACTIVATION_FLAG] = previous


def _config():
    return {
        "updated_at": "gate0-v4-test",
        "_config_version": "risk_config:gate0-v4-test",
        "country_risk_scores": {
            "united kingdom": 1,
            "hong kong": 1,
            "democratic republic of congo": 3,
            "turkey": 2,
        },
        "sector_risk_scores": {
            "government": 1,
            "software": 2,
            "crypto": 4,
        },
        "entity_type_scores": {
            "listed company": 1,
            "unregulated fund": 3,
        },
    }


def _base_input(**overrides):
    data = {
        "application_id": "app-tier0b",
        "entity_type": "Listed Company on Regulated Exchange",
        "ownership_structure": "Simple — direct identifiable UBOs",
        "country": "United Kingdom",
        "sector": "Government / Public Sector",
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


def _route_for_risk(risk, **overrides):
    app = {
        "id": "app-tier0b",
        "status": "compliance_review",
        "risk_level": risk["level"],
        "final_risk_level": risk["level"],
        "risk_escalations": json.dumps(risk["escalations"]),
    }
    app.update(overrides)
    return classify_approval_route(app)


def test_multiple_unresolved_fields_emit_distinct_safe_sentinels_and_structured_evidence(tier0b_enabled):
    set_request_id("req-tier0b-001")
    raw_sector = "Other / Founder supplied raw sector"
    raw_entity = "Legacy LLC"
    risk = compute_risk_score(
        _base_input(
            sector=raw_sector,
            entity_type=raw_entity,
            country="",
        ),
        config_override=_config(),
    )

    sentinels = [
        reason for reason in risk["escalations"]
        if reason.startswith(UNMAPPED_SENTINEL_PREFIX)
    ]
    assert len(sentinels) == 3
    assert len(set(sentinels)) == 3
    assert all(raw_sector not in sentinel and raw_entity not in sentinel for sentinel in sentinels)
    assert all(len(sentinel.rsplit(":", 1)[-1]) == 12 for sentinel in sentinels)

    evidence = risk["dimensions"]["controlled_mapping_evidence"]
    unresolved = [item for item in evidence if item["resolution_status"] == "unresolved"]
    assert {item["family"] for item in unresolved} == {
        "sector", "entity_type", "incorporation_country"
    }
    for item in unresolved:
        assert item["raw_value"] is not None
        assert item["normalized_value"] is not None
        assert item["hash"]
        assert item["application_id"] == "app-tier0b"
        assert item["request_id"] == "req-tier0b-001"
        assert item["config_version"] == "risk_config:gate0-v4-test"
        assert item["resolution_status"] == "unresolved"
        assert item["sentinel"] in sentinels

    route = _route_for_risk(risk)
    assert route["route"] == APPROVAL_ROUTE_BLOCKED
    assert "unresolved_risk_mapping" in route["reasons"]

    app = {
        "id": "app-tier0b",
        "status": "compliance_review",
        "risk_score": risk["score"],
        "risk_level": risk["level"],
        "final_risk_level": risk["level"],
        "risk_escalations": json.dumps(risk["escalations"]),
    }
    valid, message = ApprovalGateValidator.validate_approval(
        app, object(), approval_route=route
    )
    assert valid is False
    assert "every unresolved controlled risk mapping" in message
    allowed, status_code, reason, _meta = can_decide_application(
        {"role": "admin"},
        app,
        "approve",
        risk_level=risk["level"],
        approval_route=route,
    )
    assert allowed is False
    assert status_code == 403
    assert "resolve every controlled risk mapping" in reason.lower()


def test_resolving_one_field_preserves_other_mapping_and_unrelated_staleness(tier0b_enabled):
    previous = [
        "stale:recompute_failed",
        "stale:unmapped_sector:000000000000",
        "stale:unmapped_entity_type:111111111111",
    ]
    partly_resolved = compute_risk_score(
        _base_input(
            sector="Government / Public Sector",
            entity_type="Legacy LLC",
            _existing_risk_escalations=previous,
        ),
        config_override=_config(),
    )
    current_sentinels = [
        value for value in partly_resolved["escalations"]
        if value.startswith(UNMAPPED_SENTINEL_PREFIX)
    ]
    assert "stale:recompute_failed" in partly_resolved["escalations"]
    assert all("unmapped_sector" not in value for value in current_sentinels)
    assert any("unmapped_entity_type" in value for value in current_sentinels)
    assert _route_for_risk(partly_resolved)["route"] == APPROVAL_ROUTE_BLOCKED

    fully_resolved = compute_risk_score(
        _base_input(_existing_risk_escalations=partly_resolved["escalations"]),
        config_override=_config(),
    )
    assert "stale:recompute_failed" in fully_resolved["escalations"]
    assert not any(
        value.startswith(UNMAPPED_SENTINEL_PREFIX)
        for value in fully_resolved["escalations"]
    )
    assert _route_for_risk(fully_resolved)["route"] == APPROVAL_ROUTE_DIRECT_LOW_MEDIUM


def test_reconciler_never_overwrites_unrelated_stale_reason():
    evidence = [{
        "resolution_status": "unresolved",
        "sentinel": "stale:unmapped_sector:aaaaaaaaaaaa",
    }]
    result = reconcile_mapping_staleness(
        ["monthly_volume_score_4"],
        ["stale:cm_recompute_pending:req-7", "stale:unmapped_entity_type:bbbbbbbbbbbb"],
        evidence,
    )
    assert result == [
        "monthly_volume_score_4",
        "stale:cm_recompute_pending:req-7",
        "stale:unmapped_sector:aaaaaaaaaaaa",
    ]


def test_over_usd_5m_emits_specific_reason_requires_compliance_and_has_no_high_floor(tier0b_enabled):
    risk = compute_risk_score(
        _base_input(monthly_volume="Over USD 5,000,000 per month"),
        config_override=_config(),
    )
    assert "monthly_volume_score_4" in risk["escalations"]
    assert "sub_factor_score_4" not in risk["escalations"]
    assert risk["score"] < 55
    assert risk["level"] == "LOW"
    assert not any(reason.startswith("floor_rule") for reason in risk["escalations"])

    route = _route_for_risk(risk)
    assert route["route"] == APPROVAL_ROUTE_COMPLIANCE_REQUIRED
    assert route["requires_compliance_package"] is True
    assert route["requires_dual_control"] is False
    assert "monthly_volume_score_4" in route["escalation_reasons"]


def test_only_exact_approved_over_usd_5m_label_emits_volume_reason(tier0b_enabled):
    unresolved = compute_risk_score(
        _base_input(monthly_volume="Over six million monthly"),
        config_override=_config(),
    )
    assert "monthly_volume_score_4" not in unresolved["escalations"]
    assert any("unmapped_monthly_volume" in value for value in unresolved["escalations"])

    approved_band_three = compute_risk_score(
        _base_input(monthly_volume="USD 500,000 to USD 5,000,000 per month"),
        config_override=_config(),
    )
    assert "monthly_volume_score_4" not in approved_band_three["escalations"]


def test_generic_subfactor_reason_is_not_consumed_as_volume_policy():
    app = {
        "id": "app-generic-four",
        "status": "compliance_review",
        "risk_level": "LOW",
        "final_risk_level": "LOW",
        "risk_escalations": json.dumps(["sub_factor_score_4"]),
    }
    route = classify_approval_route(app)
    assert route["route"] == APPROVAL_ROUTE_DIRECT_LOW_MEDIUM
    assert "monthly_volume_score_4" not in route["escalation_reasons"]


def test_sector_score_4_floor_and_dual_control_are_unchanged(tier0b_enabled):
    risk = compute_risk_score(
        _base_input(sector="Crypto / Digital Assets Exchange"),
        config_override=_config(),
    )
    assert risk["level"] == "HIGH"
    assert risk["score"] >= 55
    assert "floor_rule_high_risk_sector" in risk["escalations"]
    assert "very_high_risk_sector" in risk["escalations"]
    assert "sub_factor_score_4" in risk["escalations"]
    assert "monthly_volume_score_4" not in risk["escalations"]
    assert _route_for_risk(risk)["route"] == APPROVAL_ROUTE_DUAL_CONTROL_REQUIRED


@pytest.mark.parametrize(
    "overrides",
    [
        {"sector": "Crypto / Digital Assets Exchange"},
        {"ownership_structure": "Opaque — UBOs cannot be fully identified"},
        {"directors": [{"is_pep": "Yes", "pep_type": "foreign"}]},
    ],
)
def test_sector_ownership_and_pep_score_four_never_emit_volume_reason(
    tier0b_enabled, overrides
):
    risk = compute_risk_score(_base_input(**overrides), config_override=_config())
    assert "monthly_volume_score_4" not in risk["escalations"]


def test_unregulated_fund_score_three_emits_no_score_four_reason(tier0b_enabled):
    risk = compute_risk_score(
        _base_input(entity_type="Unregulated Fund / SPV"),
        config_override=_config(),
    )
    assert risk["dimensions"]["d1"] == pytest.approx(1.4)
    assert "sub_factor_score_4" not in risk["escalations"]
    assert "monthly_volume_score_4" not in risk["escalations"]


def test_monthly_volume_reason_and_mapping_evidence_are_durably_persisted(tier0b_enabled, temp_db):
    from db import get_db
    from rule_engine import recompute_risk

    db = get_db()
    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-tier0b-persist-{suffix}"
    db.execute(
        """INSERT INTO applications
           (id, ref, company_name, country, sector, entity_type,
            ownership_structure, status, risk_score, risk_level,
            risk_dimensions, onboarding_lane, prescreening_data)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            app_id,
            f"ARF-TIER0B-{suffix}",
            "Tier 0B Persistence",
            "United Kingdom",
            "Government / Public Sector",
            "Listed Company on Regulated Exchange",
            "Simple — direct identifiable UBOs",
            "compliance_review",
            1.0,
            "LOW",
            json.dumps({"d1": 1, "d2": 1, "d3": 1, "d4": 1, "d5": 1}),
            "Fast Lane",
            json.dumps({
                "monthly_volume": "Over USD 5,000,000 per month",
                "transaction_complexity": "Simple — single currency, domestic corridors",
                "introduction_method": "Direct application — client initiated",
                "source_of_wealth": "business revenue",
                "source_of_funds": "company bank account",
                "customer_interaction": "face-to-face",
                "primary_service": "domestic payments only (single currency)",
            }),
        ),
    )
    db.commit()

    result = recompute_risk(
        db,
        app_id,
        "rsmp_tier0b_persistence_test",
        apply_routing_policy=False,
    )
    assert result["recomputed"] is True
    row = db.execute(
        "SELECT risk_level, risk_escalations, risk_dimensions FROM applications WHERE id=?",
        (app_id,),
    ).fetchone()
    db.close()

    escalations = json.loads(row["risk_escalations"])
    dimensions = json.loads(row["risk_dimensions"])
    assert row["risk_level"] == "LOW"
    assert "monthly_volume_score_4" in escalations
    assert not any(value.startswith(UNMAPPED_SENTINEL_PREFIX) for value in escalations)
    assert "controlled_mapping_evidence" in dimensions
    volume = next(
        item for item in dimensions["controlled_mapping_evidence"]
        if item["family"] == "monthly_volume"
    )
    assert volume["resolution_status"] == "mapped"
    assert volume["score"] == 4
