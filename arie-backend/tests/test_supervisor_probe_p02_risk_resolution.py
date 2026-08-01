"""P-02 — risk factor resolution integrity.

The claim under test is provenance, never rating. A test that asserted P-02
disagrees with a risk level would be asserting the wrong product.
"""

from __future__ import annotations

import pytest

from supervisor_foundation.probes import risk_resolution
from supervisor_probe_fixtures import (
    AS_OF,
    VALID_RISK_CONFIG,
    add_memo,
    approve,
    bundle_for,
    claims,
    factor,
    no_factor_evidence,
    only,
    risk_dimensions,
    run_probe,
    seed_application,
)

PROBE = risk_resolution.probe


def _run(db, **overrides):
    app_id = seed_application(db, **overrides)
    return run_probe(bundle_for(db, app_id), PROBE)


# ── Check 1: unresolved factors ──────────────────────────────────────


def test_unresolved_factor_is_a_hit(db):
    findings = _run(
        db,
        risk_dimensions=risk_dimensions(
            [
                factor("country_of_incorporation", dimension="D2", rule_score=4,
                       resolution_status="unresolved"),
                factor("entity_type"),
            ]
        ),
    )
    finding = only(findings)
    assert finding["status"] == "hit"
    assert finding["availability_status"] == "available"
    assert "country_of_incorporation" in finding["claim"]
    assert "'unresolved'" in finding["claim"]


def test_absent_resolution_status_is_not_treated_as_resolved(db):
    """The pre-schema shape. Silence is not a pass."""
    findings = _run(
        db,
        risk_dimensions=risk_dimensions(
            [factor("service_type", drop_resolution_status=True)]
        ),
    )
    finding = only(findings)
    assert finding["status"] == "hit"
    assert "carries no resolution status" in finding["claim"]


def test_mapped_is_a_resolved_status(db):
    """The corpus regression.

    ``risk_controlled_values`` writes ``mapped`` for a successful controlled
    lookup; only ``rule_engine`` writes ``resolved``. An earlier draft accepted
    ``resolved`` alone and reported 253 false positives across the 41-case
    canonical pilot corpus — every controlled-registry factor in the dataset.
    """
    findings = _run(
        db,
        risk_dimensions=risk_dimensions(
            [
                factor("industry_sector", dimension="D4", rule_score=3,
                       resolution_status="mapped"),
                factor("entity_type", resolution_status="mapped"),
            ]
        ),
    )
    assert only(findings)["status"] == "clear"


def test_resolved_vocabulary_matches_what_the_engine_actually_writes():
    """Derived from the writers, not asserted as a literal.

    ``risk_controlled_values.resolve_controlled_score`` returns ``mapped`` on a
    successful lookup and ``unresolved`` on a miss. Reading both out of the real
    resolver means a renamed status breaks this test rather than silently
    flipping every factor in the corpus to "defective".
    """
    from risk_controlled_values import resolve_controlled_score

    miss = resolve_controlled_score("entity_type", "a value no registry holds")
    assert miss.status in risk_resolution.UNRESOLVED_STATUSES

    hit = resolve_controlled_score("entity_type", "SME / Private Company")
    assert hit.controlled_id, "registry lookup failed — fixture value drifted"
    assert hit.status in risk_resolution.RESOLVED_STATUSES, (
        f"the controlled registry writes {hit.status!r} for a successful "
        "lookup and the probe does not recognise it as resolved"
    )


def test_unmatched_is_reported_as_a_fallback_score(db):
    """``unmatched`` means scored by a fallback rule, never mapped.

    ``rule_engine`` promotes it to ``unresolved`` only under strict mode, so
    outside strict mode it is exactly the silent default this probe exists for.
    """
    findings = _run(
        db,
        risk_dimensions=risk_dimensions(
            [factor("service_type", resolution_status="unmatched")]
        ),
    )
    finding = only(findings)
    assert finding["status"] == "hit"
    assert "scored by a fallback rule" in finding["claim"]


def test_unrecognised_status_is_neither_assumed_good_nor_bad(db):
    findings = _run(
        db,
        risk_dimensions=risk_dimensions(
            [factor("service_type", resolution_status="probably_fine")]
        ),
    )
    finding = only(findings)
    assert finding["status"] == "hit"
    assert "not a status this platform's risk engine is known to write" in finding["claim"]


@pytest.mark.parametrize("controlled_values_enabled", [False, True])
def test_probe_agrees_with_the_real_risk_engine_on_a_clean_input(
    db, monkeypatch, controlled_values_enabled
):
    """End-to-end guard against the vocabulary drifting again.

    Rather than trusting a hand-written status string, this scores a realistic
    application through ``rule_engine.compute_risk_score`` and feeds the
    evidence it actually produces to the probe. A new spelling of "resolved"
    appearing upstream fails here.

    Both sides of the controlled-value contract flag are exercised, because
    they write *different* success spellings: the legacy path writes
    ``resolved`` and the controlled registry writes ``mapped``. Testing only
    the default would have missed the bug this test was written for.
    """
    import json

    import environment
    import risk_controlled_values
    from rule_engine import compute_risk_score

    flag = risk_controlled_values.ACTIVATION_FLAG
    managers = (environment.flags, risk_controlled_values.flags)
    snapshots = [dict(manager._cache) for manager in managers]
    monkeypatch.setenv(flag, "true" if controlled_values_enabled else "false")
    for manager in managers:
        manager._cache[flag] = controlled_values_enabled

    try:
        result = compute_risk_score(
            {
                "company_name": "Probe Engine Ltd",
                "country": "Mauritius",
                "sector": "Management Consulting",
                "entity_type": "SME / Private Company",
                "ownership_structure": "Simple — direct identifiable UBOs",
                "primary_service": "Advisory",
                "monthly_volume": "Under USD 100k",
                "transaction_complexity": "Simple — single currency, domestic corridors",
                "introduction_method": "Direct application — client initiated",
            }
        )
    finally:
        for manager, snapshot in zip(managers, snapshots):
            manager._cache.clear()
            manager._cache.update(snapshot)

    evidence = (result.get("dimensions") or {}).get("factor_computation_evidence")
    assert evidence, "risk engine produced no factor evidence — fixture drifted"

    engine_factors = evidence.get("factors") or []
    assert engine_factors, "risk engine produced no factors"
    statuses = {str(item.get("resolution_status")) for item in engine_factors}
    if controlled_values_enabled:
        assert "mapped" in statuses, (
            "the controlled-value path no longer writes 'mapped'; this test is "
            "no longer covering the spelling it was written for"
        )

    findings = _run(
        db,
        risk_dimensions=json.dumps(result["dimensions"]),
        risk_score=result["score"],
    )
    reported = claims(findings)

    # The expected vocabulary is stated here, not read from the probe: comparing
    # the probe against its own constant would pass no matter what that constant
    # said, which is exactly how the original bug survived its first test.
    for item in engine_factors:
        key = str(item.get("factor_key") or "")
        status = str(item.get("resolution_status"))
        if not key:
            continue
        if status in {"mapped", "resolved"}:
            assert key not in reported, (
                f"{key} resolved as {status!r} and was still reported"
            )
        elif status in {"unresolved", "unmatched"}:
            assert key in reported, f"{key} was {status!r} and went unreported"


def test_resolution_is_never_inferred_from_a_normal_looking_score(db):
    """A factor scoring 1 with an unresolved status is still unresolved.

    This is the whole point of the probe: a silent default produces a perfectly
    ordinary score, so the score cannot be used as evidence of resolution.
    """
    findings = _run(
        db,
        risk_dimensions=risk_dimensions(
            [factor("target_markets", rule_score=1, resolution_status="defaulted")]
        ),
    )
    assert only(findings)["status"] == "hit"


@pytest.mark.parametrize(
    "factor_key,expected_category",
    [
        ("country_of_incorporation", "jurisdiction"),
        ("ubo_nationalities", "jurisdiction"),
        ("service_type", "products"),
        ("source_of_wealth", "risk"),
    ],
)
def test_category_follows_the_factor_domain(db, factor_key, expected_category):
    findings = _run(
        db,
        risk_dimensions=risk_dimensions(
            [factor(factor_key, resolution_status="unresolved")]
        ),
    )
    assert only(findings)["category"] == expected_category


def test_material_factor_scores_higher_severity(db):
    high = _run(
        db,
        risk_dimensions=risk_dimensions(
            [factor("source_of_wealth", rule_score=3, resolution_status="unresolved")]
        ),
    )
    low = _run(
        db,
        risk_dimensions=risk_dimensions(
            [factor("source_of_wealth", rule_score=2, resolution_status="unresolved")]
        ),
    )
    assert only(high)["severity"] == "high"
    assert only(low)["severity"] == "medium"


def test_one_finding_per_unresolved_factor_with_distinct_identity(db):
    """Three defective factors are three findings, not one.

    Their evidence references overlap heavily — same application, same
    configuration version — so this also guards the identity anchor.
    """
    findings = _run(
        db,
        risk_dimensions=risk_dimensions(
            [
                factor("country_of_incorporation", resolution_status="unresolved"),
                factor("service_type", resolution_status="unresolved"),
                factor("source_of_wealth", resolution_status="unresolved"),
                factor("entity_type"),
            ]
        ),
    )
    assert len(findings) == 3
    assert len({finding["finding_id"] for finding in findings}) == 3
    text = claims(findings)
    assert "entity_type" not in text


def test_unparseable_rule_score_does_not_crash_or_escalate(db):
    findings = _run(
        db,
        risk_dimensions=risk_dimensions(
            [factor("source_of_wealth", rule_score="n/a",
                    resolution_status="unresolved")]
        ),
    )
    assert only(findings)["severity"] == "medium"


# ── Check 2: configuration integrity ─────────────────────────────────


def test_missing_configuration_version_is_a_hit(db):
    findings = _run(db, risk_config_version=None)
    finding = only(findings)
    assert finding["severity"] == "high"
    assert finding["category"] == "policy"
    assert "No risk configuration version is recorded" in finding["claim"]


def test_malformed_configuration_version_is_a_hit(db):
    findings = _run(db, risk_config_version="v7")
    assert "not a valid timestamped edition" in only(findings)["claim"]


@pytest.mark.parametrize("constant_name", [
    "RISK_CONFIG_VERSION_RECOMPUTE_FAILED",
    "RISK_CONFIG_VERSION_CM_RECOMPUTE_PENDING",
])
def test_staleness_sentinels_are_reported_as_themselves(db, constant_name):
    """Both governed sentinels are recognised, not lumped in with 'malformed'.

    The sentinel value is the whole diagnosis — it says a recompute was started
    and never finished — so the finding names it. Read from ``rule_engine``
    rather than hardcoded, so a renamed sentinel fails here loudly instead of
    silently degrading the finding to "malformed version".
    """
    import rule_engine

    sentinel = getattr(rule_engine, constant_name)
    findings = _run(db, risk_config_version=sentinel)
    finding = only(findings)
    assert "staleness sentinel" in finding["claim"]
    assert sentinel in finding["claim"]


def test_valid_configuration_version_uses_the_governed_validator(db):
    """The probe defers to ``rule_engine`` rather than re-implementing the rule."""
    from rule_engine import _is_valid_risk_config_version

    assert _is_valid_risk_config_version(VALID_RISK_CONFIG)
    findings = _run(db, risk_config_version=VALID_RISK_CONFIG)
    assert only(findings)["status"] == "clear"


# ── Check 3: unexplained elevation ───────────────────────────────────


def test_escalations_without_a_reason_are_a_hit(db):
    import json

    findings = _run(
        db, risk_escalations=json.dumps(["elevated_jurisdiction"]),
        elevation_reason_text=None,
    )
    finding = only(findings)
    assert finding["category"] == "risk"
    assert "elevated_jurisdiction" in finding["claim"]


def test_upward_level_movement_without_a_reason_is_a_hit(db):
    findings = _run(db, base_risk_level="MEDIUM", final_risk_level="HIGH",
                    elevation_reason_text=None)
    assert "the level was raised from MEDIUM to HIGH" in only(findings)["claim"]


def test_recorded_elevation_reason_clears_the_check(db):
    import json

    findings = _run(
        db,
        risk_escalations=json.dumps(["elevated_jurisdiction"]),
        base_risk_level="MEDIUM",
        final_risk_level="HIGH",
        elevation_reason_text="jurisdiction_risk_tier=high",
    )
    assert only(findings)["status"] == "clear"


# ── Availability ─────────────────────────────────────────────────────


def test_absent_factor_evidence_is_not_replayable_never_clear(db):
    findings = _run(db, risk_dimensions=no_factor_evidence())
    finding = only(findings)
    assert finding["status"] == "not_replayable"
    assert finding["availability_status"] == "snapshot_incomplete"
    assert finding["status"] != "clear"


def test_unreplayable_case_runs_no_other_check(db):
    """No partial evaluation: an absent snapshot yields exactly one finding.

    Reporting "configuration version missing" alongside "cannot examine this
    case" would imply the other checks ran and found only that.
    """
    findings = _run(db, risk_dimensions=no_factor_evidence(),
                    risk_config_version=None)
    assert len(findings) == 1
    assert findings[0]["status"] == "not_replayable"


# ── Clear ────────────────────────────────────────────────────────────


def test_clean_case_records_an_explicit_clear(db):
    findings = _run(db)
    finding = only(findings)
    assert finding["status"] == "clear"
    assert finding["availability_status"] == "available"
    assert finding["severity"] == "info"


# ── Scope discipline ─────────────────────────────────────────────────


def test_probe_never_asserts_a_different_risk_rating(db):
    """P-02 has no standing to contradict the rule engine's conclusion."""
    findings = _run(
        db,
        risk_level="LOW",
        final_risk_level="LOW",
        risk_dimensions=risk_dimensions(
            [factor("country_of_incorporation", rule_score=4,
                    resolution_status="unresolved")]
        ),
    )
    text = claims(findings).lower()
    for forbidden in ("should be high", "understated", "incorrect rating",
                      "risk level is wrong", "should be rated"):
        assert forbidden not in text


def test_findings_are_stable_across_repeated_runs(db):
    app_id = seed_application(
        db,
        risk_dimensions=risk_dimensions(
            [
                factor("service_type", resolution_status="unresolved"),
                factor("target_markets", resolution_status="unresolved"),
            ]
        ),
        risk_config_version=None,
    )
    bundle = bundle_for(db, app_id)
    first = run_probe(bundle, PROBE)
    second = run_probe(bundle, PROBE)
    assert first == second


def test_approval_does_not_change_the_provenance_claim(db):
    """P-02's claims are about how the assessment was produced.

    Unlike P-03, P-04 and P-06, reliance does not change the finding: the
    provenance defect is identical whether or not anyone acted on it.
    """
    app_id = seed_application(
        db, risk_dimensions=risk_dimensions(
            [factor("service_type", resolution_status="unresolved")]
        )
    )
    before = run_probe(bundle_for(db, app_id), PROBE)
    add_memo(db, app_id)
    approve(db, app_id)
    after = run_probe(bundle_for(db, app_id), PROBE)
    assert [f["claim"] for f in before] == [f["claim"] for f in after]
    assert [f["severity"] for f in before] == [f["severity"] for f in after]


def test_as_of_does_not_affect_the_outcome(db):
    """P-02 reads no dates, so the review instant must not move its answer."""
    app_id = seed_application(db, risk_config_version=None)
    early = run_probe(bundle_for(db, app_id, as_of="2020-01-01T00:00:00Z"), PROBE)
    late = run_probe(bundle_for(db, app_id, as_of=AS_OF), PROBE)
    assert [f["claim"] for f in early] == [f["claim"] for f in late]


# ── Risk-movement direction ──────────────────────────────────────────
# An earlier build set "elevated" on any inequality between base and final, so
# a de-escalation was reported as "A risk elevation was applied". Direction is
# now established from the governed ladder, never from inequality.


def test_risk_ladder_matches_the_governed_levels():
    from periodic_review_policy import RISK_FREQUENCY_MONTHS

    assert set(risk_resolution.RISK_LADDER) == set(RISK_FREQUENCY_MONTHS)


@pytest.mark.parametrize(
    "base,final,expect_finding",
    [
        ("LOW", "HIGH", True),
        ("MEDIUM", "HIGH", True),
        ("LOW", "VERY_HIGH", True),
        ("HIGH", "LOW", False),
        ("VERY_HIGH", "MEDIUM", False),
        ("HIGH", "HIGH", False),
        ("LOW", "LOW", False),
    ],
)
def test_only_upward_movement_is_an_elevation(db, base, final, expect_finding):
    findings = _run(db, base_risk_level=base, final_risk_level=final,
                    elevation_reason_text=None)
    movement = [f for f in findings if "level was raised" in f["claim"]]
    assert bool(movement) is expect_finding, (
        f"{base} -> {final} produced {len(movement)} movement findings"
    )


def test_a_de_escalation_is_never_described_as_an_elevation(db):
    """The defect verbatim: HIGH → LOW must not read as an uplift."""
    findings = _run(db, base_risk_level="HIGH", final_risk_level="LOW",
                    elevation_reason_text=None)
    text = claims(findings).lower()
    assert "elevation was applied" not in text
    assert "raised from high to low" not in text
    assert only(findings)["status"] == "clear"


@pytest.mark.parametrize("base,final", [
    ("CRITICAL", "HIGH"), ("HIGH", "CRITICAL"), ("", "HIGH"), ("HIGH", ""),
    ("high", "very_high"),
])
def test_ungoverned_levels_never_manufacture_a_direction(db, base, final):
    """No direction can be established, so no movement claim is made.

    Lower-case governed levels are the exception — they normalise and are a
    genuine elevation, which is why ``high`` → ``very_high`` is included.
    """
    findings = _run(db, base_risk_level=base, final_risk_level=final,
                    elevation_reason_text=None)
    movement = [f for f in findings if "level was raised" in f["claim"]]
    if (base.upper(), final.upper()) == ("HIGH", "VERY_HIGH"):
        assert movement
    else:
        assert not movement, f"{base!r} -> {final!r} invented a direction"


def test_a_recorded_escalation_stands_without_level_movement(db):
    """A floor rule is upward by construction even when the level is unchanged."""
    import json

    findings = _run(
        db,
        base_risk_level="HIGH", final_risk_level="HIGH",
        risk_escalations=json.dumps(["floor_rule_declared_pep"]),
        elevation_reason_text=None,
    )
    finding = only(findings)
    assert finding["status"] == "hit"
    assert "floor_rule_declared_pep" in finding["claim"]


def test_escalation_and_upward_movement_are_reported_together(db):
    import json

    findings = _run(
        db,
        base_risk_level="LOW", final_risk_level="HIGH",
        risk_escalations=json.dumps(["floor_rule_declared_pep"]),
        elevation_reason_text=None,
    )
    claim = only(findings)["claim"]
    assert "floor_rule_declared_pep" in claim
    assert "raised from LOW to HIGH" in claim


def test_close_condition_names_every_authoritative_success_state(db):
    """An officer must not read the finding as rejecting a `mapped` factor."""
    findings = _run(
        db,
        risk_dimensions=risk_dimensions(
            [factor("service_type", resolution_status="unresolved")]
        ),
    )
    condition = only(findings)["close_condition"]
    for value in risk_resolution.RESOLVED_STATUSES:
        assert repr(value) in condition


def test_absent_configuration_version_cites_no_phantom_edition(db):
    findings = _run(db, risk_config_version=None)
    refs = only(findings)["evidence_refs"]
    assert "risk_config:None" not in refs
    assert not any(ref.startswith("risk_config:risk_config:") for ref in refs)
