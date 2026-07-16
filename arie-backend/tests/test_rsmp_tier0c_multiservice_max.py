"""Tier 0C-A hotfix: selected-service risk is the maximum selection risk."""

from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

import pytest

import environment
import risk_controlled_values
from prescreening.risk_inputs import build_prescreening_risk_input
from rule_engine import compute_risk_score, resolve_selected_service_risk
from security_hardening import APPROVAL_ROUTE_BLOCKED, classify_approval_route


ACTIVATION_FLAG = risk_controlled_values.ACTIVATION_FLAG
_MISSING = object()


def _flag_targets():
    targets = []
    for target in (environment.flags, risk_controlled_values.flags):
        if all(target is not existing for existing in targets):
            targets.append(target)
    return targets


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


def _set_activation(enabled):
    os.environ[ACTIVATION_FLAG] = "true" if enabled else "false"
    for target in _flag_targets():
        target._cache[ACTIVATION_FLAG] = enabled


def _config():
    return {
        "updated_at": "gate0-v4-multiservice-test",
        "_config_version": "risk_config:gate0-v4-multiservice-test",
        "country_risk_scores": {"united kingdom": 1},
        "sector_risk_scores": {"government": 1, "crypto": 4},
        "entity_type_scores": {"listed company": 1},
    }


def _base_input(**overrides):
    payload = {
        "application_id": "app-multiservice",
        "entity_type": "Listed Company on Regulated Exchange",
        "ownership_structure": "Simple — direct identifiable UBOs",
        "country": "United Kingdom",
        "sector": "Government / Public Sector",
        "directors": [],
        "ubos": [],
        "intermediary_shareholders": [],
        "operating_countries": [],
        "target_markets": [],
        "primary_service": "Domestic payments (single currency)",
        "service_required": "Domestic payments (single currency)",
        "monthly_volume": "Under USD 50,000 per month",
        "transaction_complexity": "Simple — single currency, domestic corridors",
        "source_of_wealth": "Business revenue",
        "source_of_funds": "Company bank transfer",
        "introduction_method": "Direct application — client initiated",
        "customer_interaction": "Face-to-face",
        "cross_border": False,
    }
    payload.update(overrides)
    return payload


def _enabled_score(services, **overrides):
    _set_activation(True)
    payload = _base_input(
        _service_selections=services,
        services_required=services,
        **overrides,
    )
    return compute_risk_score(payload, config_override=_config())


@pytest.mark.parametrize(
    ("services", "expected"),
    [
        (["Domestic payments (single currency)"], 1),
        (["Cross-border international transfers", "Domestic payments (single currency)"], 3),
        (["Domestic payments (single currency)", "Cross-border international transfers"], 3),
        (["Domestic payments (single currency)", "Multi-currency corporate accounts", "Cross-border international transfers"], 3),
        (["Domestic payments (single currency)", "Domestic payments (single currency)"], 1),
        (["  domestic PAYMENTS (single CURRENCY)  ", "  CROSS-BORDER International Transfers "], 3),
    ],
)
def test_native_array_uses_maximum_independent_of_order_case_and_duplicates(services, expected):
    result = _enabled_score(services)
    evidence = result["service_selection_evidence"]
    assert evidence["final_max_score"] == expected
    assert evidence["raw_services"] == [str(value) for value in services]
    assert evidence["selection_count"] == len(services)
    assert evidence["order_independent"] is True


@pytest.mark.parametrize(
    ("payload", "shape_prefix"),
    [
        ('["Domestic payments (single currency)", "Cross-border international transfers"]', "json_list"),
        ("['Domestic payments (single currency)', 'Cross-border international transfers']", "literal_list"),
        ("Domestic payments (single currency); Cross-border international transfers", "delimited_string"),
        ("Domestic payments (single currency)|Cross-border international transfers", "delimited_string"),
        ("Domestic payments (single currency), Cross-border international transfers", "comma_delimited_string"),
        ({"primary_services": ["Domestic payments (single currency)", "Cross-border international transfers"]}, "object.primary_services:list"),
    ],
)
def test_supported_legacy_payload_shapes_resolve_every_selection(payload, shape_prefix):
    evidence = resolve_selected_service_risk(_base_input(_service_selections=payload))
    assert evidence["final_max_score"] == 3
    assert evidence["selection_count"] == 2
    assert evidence["payload_shape"] == shape_prefix


def test_unknown_mixed_with_mapped_service_fails_closed_without_lowering_maximum():
    result = _enabled_score([
        "Domestic payments (single currency)",
        "Founder supplied unknown service",
        "Cross-border international transfers",
    ])
    evidence = result["service_selection_evidence"]
    unknown = next(
        row for row in evidence["individual_resolutions"]
        if row["raw_value"] == "Founder supplied unknown service"
    )
    assert evidence["final_max_score"] == 3
    assert evidence["resolution_status"] == "unresolved"
    assert unknown["resolution_status"] == "unresolved"
    assert unknown["runtime_rule"] == "legacy_default_score_2"
    assert unknown["sentinel"].startswith("stale:unmapped_service:")
    assert unknown["family"] == "service"
    assert len(unknown["hash"]) == 12
    assert unknown["application_id"] == "app-multiservice"
    assert unknown["config_version"] == "risk_config:gate0-v4-multiservice-test"
    assert unknown["sentinel"] in result["escalations"]
    assert "Founder supplied unknown service" not in unknown["sentinel"]

    route = classify_approval_route({
        "status": "compliance_review",
        "risk_level": result["level"],
        "final_risk_level": result["level"],
        "risk_escalations": result["escalations"],
    })
    assert route["route"] == APPROVAL_ROUTE_BLOCKED
    assert "unresolved_risk_mapping" in route["reasons"]


def test_selection_order_changes_evidence_order_but_not_result_or_sentinel_set():
    forward = _enabled_score([
        "Domestic payments (single currency)",
        "Founder supplied unknown service",
        "Cross-border international transfers",
    ])
    reverse = _enabled_score([
        "Cross-border international transfers",
        "Founder supplied unknown service",
        "Domestic payments (single currency)",
    ])
    assert forward["service_selection_evidence"]["final_max_score"] == 3
    assert reverse["service_selection_evidence"]["final_max_score"] == 3
    assert set(forward["service_selection_evidence"]["sentinels"]) == set(
        reverse["service_selection_evidence"]["sentinels"]
    )
    assert forward["score"] == reverse["score"]
    assert forward["level"] == reverse["level"]


def test_submission_replay_and_recompute_input_share_complete_selection_payload():
    selected = [
        "Domestic payments (single currency)",
        "Multi-currency corporate accounts",
        "Cross-border international transfers",
    ]
    scorer_input = build_prescreening_risk_input(
        application={
            "id": "app-path-parity",
            "country": "United Kingdom",
            "sector": "Government / Public Sector",
            "entity_type": "Listed Company on Regulated Exchange",
            "ownership_structure": "Simple — direct identifiable UBOs",
            "risk_escalations": [],
        },
        prescreening_data={
            "services_required": selected,
            "monthly_volume": "Under USD 50,000 per month",
            "transaction_complexity": "Simple — single currency, domestic corridors",
            "introduction_method": "Direct application — client initiated",
        },
        directors=[],
        ubos=[],
        intermediaries=[],
    )
    assert scorer_input["_service_selections"] == selected
    assert scorer_input["services_required"] == selected
    assert scorer_input["primary_service"] == selected[0]

    _set_activation(True)
    result = compute_risk_score(scorer_input, config_override=_config())
    assert result["service_selection_evidence"]["final_max_score"] == 3

    source = Path(__file__).resolve().parents[1].joinpath("rule_engine.py").read_text(encoding="utf-8")
    recompute_body = source[source.index("def recompute_risk("):source.index("def recompute_risk_for_active_apps(")]
    assert "build_prescreening_risk_input(" in recompute_body
    assert "compute_risk_score(scoring_input)" in recompute_body


def test_recompute_persists_the_same_maximum_and_complete_evidence(temp_db):
    from db import get_db
    from rule_engine import recompute_risk

    _set_activation(True)
    selected = [
        "Domestic payments (single currency)",
        "Multi-currency corporate accounts",
        "Cross-border international transfers",
    ]
    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-multiservice-recompute-{suffix}"
    db = get_db()
    db.execute(
        """INSERT INTO applications
           (id, ref, company_name, country, sector, entity_type,
            ownership_structure, status, risk_score, risk_level,
            risk_dimensions, onboarding_lane, prescreening_data)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            app_id,
            f"ARF-MULTI-{suffix}",
            "Multi-Service Recompute Test",
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
                "services_required": selected,
                "monthly_volume": "Under USD 50,000 per month",
                "transaction_complexity": "Simple — single currency, domestic corridors",
                "introduction_method": "Direct application — client initiated",
                "source_of_wealth": "Business revenue",
                "source_of_funds": "Company bank transfer",
                "customer_interaction": "Face-to-face",
            }),
        ),
    )
    db.commit()

    result = recompute_risk(
        db,
        app_id,
        "rsmp_multiservice_max_parity_test",
        apply_routing_policy=False,
    )
    row = db.execute(
        "SELECT risk_score, risk_dimensions FROM applications WHERE id=?",
        (app_id,),
    ).fetchone()
    db.close()

    assert result["recomputed"] is True
    assert row["risk_score"] == result["new_score"]
    evidence = json.loads(row["risk_dimensions"])["service_selection_evidence"]
    assert evidence["raw_services"] == selected
    assert evidence["normalized_services"] == [
        "domestic payments (single currency)",
        "multi-currency corporate accounts",
        "cross-border international transfers",
    ]
    assert [item["score"] for item in evidence["individual_resolutions"]] == [1, 2, 3]
    assert all(item["application_id"] == app_id for item in evidence["individual_resolutions"])
    assert all(item["config_version"].startswith("risk_config:") for item in evidence["individual_resolutions"])
    assert evidence["final_max_score"] == 3
    assert evidence["maximum_enforced"] is True


def test_flag_off_behavior_is_identical_when_plural_payload_is_present():
    _set_activation(False)
    selected = [
        "Domestic payments (single currency)",
        "Cross-border international transfers",
    ]
    legacy = compute_risk_score(_base_input(), config_override=_config())
    plural = compute_risk_score(
        _base_input(_service_selections=selected, services_required=selected),
        config_override=_config(),
    )
    assert plural == legacy
    assert plural["service_selection_evidence"] is None


def test_activation_does_not_change_legacy_single_service_alias_behavior():
    payload = _base_input(
        primary_service="Multi-currency account",
        service_required="Multi-currency account",
        _service_selections=["Domestic payments (single currency)"],
        services_required=["Domestic payments (single currency)"],
    )
    isolated = _config()
    isolated["dimensions"] = [{
        "id": "D3",
        "weight": 20,
        "subcriteria": [{"weight": 100}, {"weight": 0}, {"weight": 0}],
    }]
    _set_activation(False)
    before = compute_risk_score(payload, config_override=isolated)
    _set_activation(True)
    after = compute_risk_score(payload, config_override=isolated)
    assert after["dimensions"]["d3"] == before["dimensions"]["d3"]
    assert after["service_selection_evidence"]["maximum_enforced"] is False
    assert after["service_selection_evidence"]["final_max_score"] == 2


@pytest.mark.parametrize(
    ("case_id", "services", "cross_border", "maximum"),
    [
        ("ad872cca36f2775c", ["Virtual asset services", "Cross-border international transfers"], True, 3),
        ("8ad9f4d9495b02e9", ["Multi-currency account", "Cross-border payments", "Card issuing"], True, 3),
        ("bbf8b2ef7250e594", ["Domestic payments (single currency)", "Multi-currency corporate accounts", "Cross-border international transfers"], True, 3),
        ("2fbdaced8f6254c9", ["Domestic payments (single currency)", "Cross-border international transfers"], True, 3),
        ("badc55a396588846", ["payments", "virtual_accounts", "treasury"], True, 3),
        ("93a78ccab931e080", ["Payment Processing", "Virtual IBAN", "FX Conversion"], True, 3),
        ("37547b2af9f437d2", ["account_opening", "payments"], True, 3),
        ("c8c91233da0ee381", ["Domestic payments (single currency)", "Cross-border international transfers"], True, 3),
        ("21866a59b44efdef", ["Cross-border payments", "Virtual asset payments"], True, 3),
        ("1b49b4fd681d2a69", ["Domestic payments (single currency)", "Cross-border international transfers"], True, 3),
        ("206acc952fb6ff17", ["account_opening", "payments"], True, 3),
        ("ce3cc760523c5bd4", ["Domestic payments (single currency)", "Multi-currency corporate accounts", "Cross-border international transfers"], True, 3),
        ("e5a379c0f9d2ceb7", ["Account opening", "Compliance onboarding", "Domestic payments"], True, 3),
        ("15b7c1bee562858f", ["Domestic payments (single currency)", "Cross-border international transfers"], True, 3),
        ("102745b86c4f8ea6", ["Virtual asset payments", "Cross-border payments"], True, 3),
        ("3c543f72794acf3e", ["Corporate account", "Domestic payments"], False, 2),
        ("0e9c7931a9aecbe6", ["account_opening", "payments"], True, 3),
        ("ef7d4e085897c639", ["Corporate account", "Domestic payments"], False, 2),
        ("c4f3492cbed61e1d", ["Domestic payments (single currency)", "Multi-currency corporate accounts"], True, 2),
        ("80d9b1cc4fc553ec", ["account_opening", "payments"], True, 3),
        ("5c8d0d45909892ad", ["account_opening", "payments"], True, 3),
        ("d592337face7c474", ["account_opening", "payments"], True, 3),
        ("9e49ddb2122e8ffb", ["Virtual asset payments", "Cross-border payments"], True, 3),
        ("6419454f2c456972", ["account_opening", "payments"], True, 3),
        ("09b226dc35ab8cc8", ["account_opening", "payments"], True, 3),
        ("b0e5e2fd47effdaa", ["account_opening", "payments"], True, 3),
        ("002744595894452e", ["payments", "virtual_accounts", "treasury"], True, 3),
        ("a1cc994a772bea1e", ["Domestic payments (single currency)", "Multi-currency corporate accounts"], True, 2),
    ],
)
def test_all_28_tier0c_a_cases_now_resolve_to_maximum(case_id, services, cross_border, maximum):
    evidence = resolve_selected_service_risk(_base_input(
        application_id=case_id,
        _service_selections=services,
        cross_border=cross_border,
    ))
    assert evidence["final_max_score"] == maximum
    assert evidence["final_max_score"] == max(
        row["score"] for row in evidence["individual_resolutions"]
    )


def test_unrelated_floors_and_volume_policy_are_unchanged():
    high_service = [
        "Domestic payments (single currency)",
        "Cross-border international transfers",
    ]
    sector = _enabled_score(high_service, sector="Crypto / Virtual Assets")
    ownership = _enabled_score(
        high_service,
        ownership_structure="Opaque — UBOs cannot be fully identified",
    )
    pep = _enabled_score(high_service, directors=[{
        "client_declared_pep": True,
        "pep_declaration": {
            "client_declared_pep": True,
            "pep_status": "declared_yes",
            "pep_role_type": "domestic_pep",
        },
    }])
    volume = _enabled_score(
        high_service,
        monthly_volume="Over USD 5,000,000 per month",
    )
    assert sector["level"] == "HIGH"
    assert "floor_rule_high_risk_sector" in sector["escalations"]
    assert ownership["level"] == "HIGH"
    assert "floor_rule_opaque_ownership" in ownership["escalations"]
    assert pep["level"] == "HIGH"
    assert "floor_rule_declared_pep" in pep["escalations"]
    assert "monthly_volume_score_4" in volume["escalations"]
    assert "sub_factor_score_4" not in volume["escalations"]
