"""Review engine — status, completeness, grouping and concern selection.

The properties here are the ones an officer's reading of the review rests on:
that the headline status follows a stated rule, that completeness cannot hide an
unevaluable check, that grouping never loses a finding, and that the summary
list is a defensible selection rather than the first few in the list.
"""

from __future__ import annotations

import pytest

from supervisor_foundation.aggregation import MATERIAL_CONCERN_LIMIT, aggregate_review
from supervisor_foundation.aggregation.engine import (
    CONCERNS_IDENTIFIED,
    CRITICAL_CONCERNS,
    INCOMPLETE_REVIEW,
    MATERIAL_CONCERNS,
    NO_MATERIAL_FINDINGS,
    OVERALL_STATUSES,
    AggregationInputError,
)
from supervisor_probe_fixtures import (
    add_memo,
    add_periodic_review,
    add_screening,
    approve,
    factor,
    person_entry,
    risk_dimensions,
    screening_record,
    seed_application,
)
from supervisor_review_fixtures import (
    MINIMAL_BUNDLE,
    finding,
    real_review,
    screening_subject_finding,
    synthetic_review,
)


def _aggregate(findings, **kwargs):
    return aggregate_review(synthetic_review(findings, **kwargs), bundle=MINIMAL_BUNDLE)


def _status(findings, **kwargs) -> str:
    return _aggregate(findings, **kwargs)["overall_assessment"]["overall_status"]


# ── Overall status ───────────────────────────────────────────────────


def test_a_critical_hit_gives_critical_concerns():
    assert _status([finding(severity="critical")]) == CRITICAL_CONCERNS


def test_a_high_hit_gives_material_concerns():
    assert _status([finding(severity="high")]) == MATERIAL_CONCERNS


def test_only_medium_hits_give_concerns_identified():
    assert _status([finding(severity="medium")]) == CONCERNS_IDENTIFIED


def test_only_low_hits_give_concerns_identified():
    assert _status([finding(severity="low")]) == CONCERNS_IDENTIFIED


def test_no_findings_with_every_probe_available_gives_no_material_findings():
    assert _status([finding(status="clear")]) == NO_MATERIAL_FINDINGS


def test_no_hits_with_an_unavailable_probe_gives_incomplete_review():
    findings = [
        finding(status="clear"),
        finding(
            status="unavailable",
            availability_status="credentials_absent",
            primary_evidence_ref="screening:app-fixture#subject:s2#provider_mode",
        ),
    ]
    assert _status(findings) == INCOMPLETE_REVIEW


def test_a_not_replayable_finding_also_gives_incomplete_review():
    assert _status([finding(status="not_replayable", availability_status="snapshot_incomplete")]) == (
        INCOMPLETE_REVIEW
    )


def test_a_critical_hit_outranks_an_unavailable_probe():
    """Severity wins where severity is known."""
    findings = [
        finding(severity="critical"),
        finding(
            status="unavailable",
            availability_status="data_absent",
            primary_evidence_ref="screening:app-fixture#subject:s2#provider_mode",
        ),
    ]
    assert _status(findings) == CRITICAL_CONCERNS


def test_an_unavailable_probe_outranks_a_medium_hit():
    """The documented judgement call, asserted so it cannot drift silently.

    A medium defect is a known quantity; a control nobody could evaluate is not,
    and ``concerns_identified`` would imply the review was complete.
    """
    findings = [
        finding(severity="medium"),
        finding(
            status="unavailable",
            availability_status="dependency_gated",
            primary_evidence_ref="screening:app-fixture#subject:s2#provider_mode",
        ),
    ]
    assert _status(findings) == INCOMPLETE_REVIEW


def test_not_applicable_alone_is_not_incomplete():
    """A check that does not apply ran to completion and answered."""
    assert _status([finding(status="not_applicable")]) == NO_MATERIAL_FINDINGS


def test_an_empty_finding_set_is_no_material_findings():
    assert _status([]) == NO_MATERIAL_FINDINGS


def test_overall_status_is_always_from_the_closed_vocabulary():
    for severity in ("critical", "high", "medium", "low", "info"):
        for status in ("hit", "clear", "unavailable", "not_applicable", "not_replayable"):
            availability = "available" if status in ("hit", "clear") else "data_absent"
            value = _status([finding(severity=severity, status=status, availability_status=availability)])
            assert value in OVERALL_STATUSES


@pytest.mark.parametrize(
    "banned",
    ["approved", "rejected", "compliant", "non_compliant", "safe", "acceptable"],
)
def test_overall_status_never_states_a_decision(banned):
    """The Supervisor is advisory. No status value is a verdict on the customer."""
    assert not any(banned in status for status in OVERALL_STATUSES)


# ── Review completeness ──────────────────────────────────────────────


def test_completeness_counts_every_severity_and_status():
    findings = [
        finding(severity="critical"),
        finding(severity="high", primary_evidence_ref="risk:app-fixture#factor:a", probe_id="P-02"),
        finding(severity="medium", primary_evidence_ref="risk:app-fixture#factor:b", probe_id="P-02"),
        finding(status="clear", primary_evidence_ref="application:app-fixture#status", probe_id="P-06"),
        finding(
            status="unavailable",
            availability_status="data_absent",
            probe_id="P-03",
            primary_evidence_ref="application:app-fixture#edd_route",
        ),
    ]
    completeness = _aggregate(findings)["review_completeness"]

    assert completeness["finding_count"] == 5
    assert completeness["critical_count"] == 1
    assert completeness["high_count"] == 1
    assert completeness["medium_count"] == 1
    assert completeness["status_counts"]["clear"] == 1
    assert completeness["status_counts"]["unavailable"] == 1
    assert completeness["unevaluable_count"] == 1
    assert completeness["probes_unavailable"] == ["P-03"]
    assert completeness["probes_expected"] == 4
    assert completeness["evidence_reference_count"] > 0


def test_severity_counts_describe_hits_only():
    """A clear finding has a severity field. It is not a concern."""
    completeness = _aggregate([finding(severity="critical", status="clear")])[
        "review_completeness"
    ]
    assert completeness["critical_count"] == 0
    assert completeness["status_counts"]["clear"] == 1


def test_a_probe_with_any_unevaluable_check_is_not_reported_completed():
    findings = [
        finding(probe_id="P-04", status="clear"),
        finding(
            probe_id="P-04",
            status="unavailable",
            availability_status="credentials_absent",
            primary_evidence_ref="screening:app-fixture#subject:s2#provider_mode",
        ),
    ]
    completeness = _aggregate(findings)["review_completeness"]
    assert "P-04" not in completeness["probes_completed"]
    assert completeness["probes_unavailable"] == ["P-04"]


def test_the_completeness_fraction_carries_its_own_definition():
    """A percentage quoted without its definition is how "complete" becomes "clean"."""
    definition = _aggregate([])["review_completeness"]["evaluated_fraction_definition"]
    assert "not whether the customer is acceptable" in definition


# ── Grouping ─────────────────────────────────────────────────────────


def test_multiple_screening_subjects_group_into_one_concern():
    findings = [screening_subject_finding(key) for key in ("s1", "s2", "s3")]
    groups = _aggregate(findings)["grouped_findings"]

    assert len(groups) == 1
    group = groups[0]
    assert group["affected_count"] == 3
    assert "3 required screening subjects" in group["claim"]
    assert len(group["finding_ids"]) == 3


def test_grouping_preserves_every_underlying_finding_id():
    findings = [screening_subject_finding(key) for key in ("s1", "s2", "s3")]
    output = _aggregate(findings)
    grouped_ids = {fid for group in output["grouped_findings"] for fid in group["finding_ids"]}
    assert grouped_ids == {item["finding_id"] for item in findings}
    assert output["finding_ids"] == sorted(item["finding_id"] for item in findings)


def test_grouping_preserves_every_primary_evidence_reference():
    findings = [screening_subject_finding(key) for key in ("s1", "s2", "s3")]
    group = _aggregate(findings)["grouped_findings"][0]
    assert set(group["primary_evidence_refs"]) == {
        item["primary_evidence_ref"] for item in findings
    }


def test_each_subject_remains_individually_traceable():
    """The group is a heading. The subjects underneath it stay addressable."""
    findings = [screening_subject_finding(key) for key in ("s1", "s2")]
    output = _aggregate(findings)
    index = {record["evidence_ref"] for record in output["evidence_index"]}
    for key in ("s1", "s2"):
        assert f"screening:app-fixture#subject:{key}#provider_mode" in index


def test_a_hit_and_an_unavailable_on_the_same_check_never_merge():
    """The failure that would discredit the artifact: a silent check absorbed."""
    findings = [
        screening_subject_finding("s1"),
        screening_subject_finding(
            "s2", status="unavailable", availability_status="data_absent", severity="medium"
        ),
    ]
    groups = _aggregate(findings)["grouped_findings"]
    assert len(groups) == 2
    assert {group["status"] for group in groups} == {"hit", "unavailable"}


def test_two_unavailable_reasons_never_merge():
    """"No credentials" and "no snapshot" are different problems with different owners."""
    findings = [
        screening_subject_finding("s1", status="unavailable", availability_status="credentials_absent"),
        screening_subject_finding("s2", status="unavailable", availability_status="snapshot_incomplete"),
    ]
    groups = _aggregate(findings)["grouped_findings"]
    assert len(groups) == 2
    assert {group["availability_status"] for group in groups} == {
        "credentials_absent",
        "snapshot_incomplete",
    }


def test_different_probes_never_merge():
    findings = [
        finding(probe_id="P-02", primary_evidence_ref="risk:app-fixture#factor:a"),
        finding(probe_id="P-04", primary_evidence_ref="risk:app-fixture#factor:a"),
    ]
    groups = _aggregate(findings)["grouped_findings"]
    assert len(groups) == 2
    assert {group["probe_id"] for group in groups} == {"P-02", "P-04"}


def test_different_checks_of_one_probe_never_merge():
    findings = [
        screening_subject_finding("s1"),
        screening_subject_finding(
            "s1",
            primary_evidence_ref="screening:app-fixture#subject:s1#screening_valid_until",
            category="screening",
        ),
    ]
    groups = _aggregate(findings)["grouped_findings"]
    assert len(groups) == 2
    assert {group["check_id"] for group in groups} == {
        "P-04.provider_mode",
        "P-04.screening_valid_until",
    }


def test_one_check_across_categories_groups_and_keeps_both_categories():
    """P-02's factor check reports jurisdiction and products on different factors.

    They are one concern — "risk inputs did not resolve" — and two owners. The
    group says both.
    """
    findings = [
        finding(
            probe_id="P-02",
            category="jurisdiction",
            primary_evidence_ref="risk:app-fixture#factor:country_of_incorporation",
        ),
        finding(
            probe_id="P-02",
            category="products",
            primary_evidence_ref="risk:app-fixture#factor:service_type",
        ),
    ]
    groups = _aggregate(findings)["grouped_findings"]
    assert len(groups) == 1
    assert groups[0]["categories"] == ["jurisdiction", "products"]
    assert groups[0]["owner_roles"] == ["Ops"]


def test_differing_close_conditions_are_all_exposed_on_the_group():
    findings = [
        screening_subject_finding("s1", close_condition="Live result for s1."),
        screening_subject_finding("s2", close_condition="Live result for s2."),
    ]
    group = _aggregate(findings)["grouped_findings"][0]
    assert group["close_conditions"] == ["Live result for s1.", "Live result for s2."]


def test_a_group_never_downgrades_severity():
    findings = [
        screening_subject_finding("s1", severity="critical"),
        screening_subject_finding("s2", severity="medium"),
    ]
    group = _aggregate(findings)["grouped_findings"][0]
    assert group["severity"] == "critical"


def test_a_single_finding_keeps_its_own_claim():
    """A template would replace a specific sentence with a vaguer one."""
    single = screening_subject_finding("s1")
    group = _aggregate([single])["grouped_findings"][0]
    assert group["claim"] == single["claim"]
    assert "1 required screening subject" not in group["claim"]


def test_an_unregistered_check_becomes_a_singleton_group():
    """A stale register degrades to less grouping, never to wrong grouping."""
    findings = [
        finding(probe_id="P-99", primary_evidence_ref="mystery:app-fixture#unknown_field:a"),
        finding(probe_id="P-99", primary_evidence_ref="mystery:app-fixture#unknown_field:b"),
    ]
    groups = _aggregate(findings)["grouped_findings"]
    # Same derived key, but no register entry: they still share a group, and the
    # group is flagged as unregistered and speaks in the finding's own words.
    assert all(group["check_registered"] is False for group in groups)
    assert groups[0]["claim"] == findings[0]["claim"]


def test_group_ordering_is_deterministic_regardless_of_input_order():
    findings = [
        screening_subject_finding("s1"),
        finding(probe_id="P-02", severity="high", primary_evidence_ref="risk:app-fixture#factor:a"),
        finding(probe_id="P-06", severity="medium", primary_evidence_ref="application:app-fixture#periodic_reviews"),
    ]
    forward = [group["group_id"] for group in _aggregate(findings)["grouped_findings"]]
    reverse = [group["group_id"] for group in _aggregate(list(reversed(findings)))["grouped_findings"]]
    assert forward == reverse


def test_groups_are_ordered_by_severity():
    findings = [
        finding(severity="low", probe_id="P-06", primary_evidence_ref="application:app-fixture#status"),
        finding(severity="critical", probe_id="P-04"),
        finding(severity="high", probe_id="P-02", primary_evidence_ref="risk:app-fixture#factor:a"),
    ]
    severities = [group["severity"] for group in _aggregate(findings)["grouped_findings"]]
    assert severities == ["critical", "high", "low"]


# ── Material concerns ────────────────────────────────────────────────


def test_material_concerns_are_ordered_critical_before_high():
    findings = [
        finding(severity="high", probe_id="P-02", primary_evidence_ref="risk:app-fixture#factor:a"),
        finding(severity="critical", probe_id="P-04"),
    ]
    concerns = _aggregate(findings)["material_concerns"]
    assert [concern["severity"] for concern in concerns] == ["critical", "high"]


def test_material_concerns_exclude_medium_and_below():
    findings = [finding(severity="medium"), finding(severity="low", probe_id="P-06",
                                                    primary_evidence_ref="application:app-fixture#status")]
    output = _aggregate(findings)
    assert output["material_concerns"] == []
    assert output["overall_assessment"]["material_concern_count"] == 0


def test_material_concerns_exclude_non_hits():
    """An unavailable check is reported in its own section, not as a concern."""
    findings = [finding(severity="critical", status="unavailable", availability_status="data_absent")]
    output = _aggregate(findings)
    assert output["material_concerns"] == []
    assert len(output["unavailable_checks"]) == 1


def test_material_concerns_are_capped_and_the_omission_is_stated():
    findings = [
        finding(
            severity="high",
            probe_id="P-02",
            primary_evidence_ref=f"risk:app-fixture#factor_{index}:x",
        )
        for index in range(MATERIAL_CONCERN_LIMIT + 3)
    ]
    output = _aggregate(findings)
    assessment = output["overall_assessment"]
    assert len(output["material_concerns"]) == MATERIAL_CONCERN_LIMIT
    assert assessment["material_concern_count"] == MATERIAL_CONCERN_LIMIT + 3
    assert assessment["material_concerns_omitted"] == 3
    # Nothing is actually lost — the omitted groups are still in the review.
    assert len(output["grouped_findings"]) == MATERIAL_CONCERN_LIMIT + 3


def test_the_cap_never_suppresses_a_critical_concern():
    findings = [
        finding(
            severity="critical",
            probe_id="P-02",
            primary_evidence_ref=f"risk:app-fixture#factor_{index}:x",
        )
        for index in range(MATERIAL_CONCERN_LIMIT + 4)
    ]
    output = _aggregate(findings)
    assert len(output["material_concerns"]) == MATERIAL_CONCERN_LIMIT + 4
    assert output["overall_assessment"]["material_concerns_omitted"] == 0


def test_criticals_displace_highs_from_the_capped_summary():
    # Distinct check fragments, so these are ten separate groups rather than two
    # — the cap acts on groups, and a test that produced two would prove nothing.
    criticals = [
        finding(severity="critical", probe_id="P-04",
                primary_evidence_ref=f"screening:app-fixture#crit_{index}:x")
        for index in range(5)
    ]
    highs = [
        finding(severity="high", probe_id="P-02",
                primary_evidence_ref=f"risk:app-fixture#high_{index}:x")
        for index in range(5)
    ]
    output = _aggregate(criticals + highs)
    concerns = output["material_concerns"]
    assert output["overall_assessment"]["material_concern_count"] == 10
    assert len(concerns) == MATERIAL_CONCERN_LIMIT
    assert sum(1 for concern in concerns if concern["severity"] == "critical") == 5
    assert output["overall_assessment"]["material_concerns_omitted"] == 3


def test_every_material_concern_links_to_findings_and_evidence():
    concerns = _aggregate([screening_subject_finding("s1"), screening_subject_finding("s2")])[
        "material_concerns"
    ]
    for concern in concerns:
        assert concern["finding_ids"]
        assert concern["evidence_refs"]
        assert concern["group_id"]


def test_material_concern_selection_is_not_the_first_n_findings():
    """Ordering is by severity, not by arrival."""
    findings = [
        finding(severity="high", probe_id="P-02", primary_evidence_ref=f"risk:app-fixture#f{i}:x")
        for i in range(MATERIAL_CONCERN_LIMIT)
    ] + [finding(severity="critical", probe_id="P-04")]
    concerns = _aggregate(findings)["material_concerns"]
    assert concerns[0]["severity"] == "critical"


# ── Critical findings ────────────────────────────────────────────────


def test_every_critical_group_appears_uncapped_with_its_findings():
    findings = [
        finding(
            severity="critical",
            probe_id="P-04",
            primary_evidence_ref=f"screening:app-fixture#subject:s{index}#provider_mode",
        )
        for index in range(MATERIAL_CONCERN_LIMIT + 5)
    ]
    critical = _aggregate(findings)["critical_findings"]
    # All twelve share one check, so they are one group carrying twelve findings.
    assert sum(entry["affected_count"] for entry in critical) == MATERIAL_CONCERN_LIMIT + 5
    for entry in critical:
        assert entry["finding_ids"]
        assert entry["required_actions"]
        assert entry["close_conditions"]
        assert entry["primary_evidence_refs"]


def test_critical_findings_are_empty_when_nothing_is_critical():
    assert _aggregate([finding(severity="high")])["critical_findings"] == []


# ── Input contract ───────────────────────────────────────────────────


def test_raw_probe_drafts_are_refused():
    """Aggregating un-normalised drafts would produce findings with no identity."""
    draft = finding()
    draft.pop("finding_id")
    with pytest.raises(AggregationInputError, match="finding_id"):
        aggregate_review(synthetic_review([draft]), bundle=MINIMAL_BUNDLE)


def test_a_non_mapping_review_is_refused():
    with pytest.raises(AggregationInputError):
        aggregate_review([], bundle=MINIMAL_BUNDLE)


def test_a_non_mapping_bundle_is_refused():
    with pytest.raises(AggregationInputError):
        aggregate_review(synthetic_review([]), bundle=None)


def test_a_review_without_findings_is_refused():
    with pytest.raises(AggregationInputError, match="findings"):
        aggregate_review({"findings": None}, bundle=MINIMAL_BUNDLE)


# ── Against the real probes ──────────────────────────────────────────


@pytest.fixture
def busy_case(db):
    """One approved case that trips all four probes at once."""
    app_id = seed_application(
        db,
        risk_level="HIGH",
        final_risk_level="HIGH",
        base_risk_level="MEDIUM",
        risk_config_version=None,
        elevation_reason_text=None,
        risk_dimensions=risk_dimensions(
            [
                factor("country_of_incorporation", rule_score=4, resolution_status="unresolved"),
                factor("service_selection", rule_score=4, resolution_status="unmatched"),
                factor("adverse_media", rule_score=1),
            ]
        ),
    )
    add_memo(db, app_id)
    record = screening_record(api_status="sandbox")
    add_screening(
        db,
        app_id,
        company=record,
        directors=[person_entry("Director One", record), person_entry("Director Two", record)],
        ubos=[person_entry("Ubo One", record, has_pep_hit=True)],
    )
    add_periodic_review(db, app_id, status="completed")
    approve(db, app_id)
    return app_id


def test_real_case_groups_fewer_than_it_finds(busy_case, db):
    """The whole point of the layer, measured on real probe output."""
    review, bundle = real_review(db, busy_case)
    output = aggregate_review(review, bundle=bundle)
    assert len(review["findings"]) > len(output["grouped_findings"])


def test_real_case_loses_no_finding(busy_case, db):
    review, bundle = real_review(db, busy_case)
    output = aggregate_review(review, bundle=bundle)
    grouped = {fid for group in output["grouped_findings"] for fid in group["finding_ids"]}
    assert grouped == {item["finding_id"] for item in review["findings"]}


def test_real_case_loses_no_evidence_reference(busy_case, db):
    review, bundle = real_review(db, busy_case)
    output = aggregate_review(review, bundle=bundle)
    cited = {ref for item in review["findings"] for ref in item["evidence_refs"]}
    indexed = {record["evidence_ref"] for record in output["evidence_index"]}
    assert cited == indexed


def test_real_case_screening_subjects_group(busy_case, db):
    review, bundle = real_review(db, busy_case)
    output = aggregate_review(review, bundle=bundle)
    provider = [
        group
        for group in output["grouped_findings"]
        if group["check_id"] == "P-04.provider_mode"
    ]
    assert len(provider) == 1
    assert provider[0]["affected_count"] >= 3
    assert "required screening subjects" in provider[0]["claim"]


def test_real_case_reaches_critical_concerns(busy_case, db):
    review, bundle = real_review(db, busy_case)
    output = aggregate_review(review, bundle=bundle)
    assert output["overall_assessment"]["overall_status"] == CRITICAL_CONCERNS


def test_a_bare_application_produces_a_review_that_says_what_did_not_run(db):
    """No hits must never read as a clean bill of health."""
    app_id = seed_application(db)
    review, bundle = real_review(db, app_id)
    output = aggregate_review(review, bundle=bundle)
    assert output["overall_assessment"]["overall_status"] in (
        INCOMPLETE_REVIEW,
        CONCERNS_IDENTIFIED,
        CRITICAL_CONCERNS,
        MATERIAL_CONCERNS,
    )
    assert output["unavailable_checks"], "an empty case has checks that could not run"
    assert output["review_completeness"]["unevaluable_count"] > 0


# ── Malformed and future reference shapes ────────────────────────────
# The check key is derived from a reference, so the derivation has to be total.
# A shape it cannot read must produce a visible singleton, never a crash and
# never a silent merge with something unrelated.


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "#",
        "###",
        "no-separator-at-all",
        "trailing#",
        "screening:app#subject:s1#",
        "screening:app#subject:s1#provider_mode#extra",
        "future_probe:app#brand_new_anchor",
        "future_probe:app#brand_new_anchor:sub:deep",
        "weird chars !@£$%^&*()#anchor",
        "unicode:app#anchor_ünïcødé",
        "x" * 500 + "#anchor",
    ],
)
def test_any_reference_shape_produces_a_visible_group(reference):
    output = _aggregate([finding(probe_id="P-99", primary_evidence_ref=reference)])
    groups = output["grouped_findings"]
    assert len(groups) == 1
    assert groups[0]["affected_count"] == 1
    assert groups[0]["finding_ids"]
    assert output["finding_ids"] == [groups[0]["finding_ids"][0]]


def test_a_future_reference_shape_does_not_merge_with_a_registered_check():
    """An unregistered anchor must not be absorbed by a registered one."""
    findings = [
        screening_subject_finding("s1"),
        finding(probe_id="P-04", primary_evidence_ref="screening:app-fixture#future_anchor"),
    ]
    groups = _aggregate(findings)["grouped_findings"]
    assert len(groups) == 2
    registered = {group["check_registered"] for group in groups}
    assert registered == {True, False}


def test_two_unregistered_findings_with_unrelated_anchors_do_not_merge():
    findings = [
        finding(probe_id="P-99", primary_evidence_ref="mystery:app#alpha"),
        finding(probe_id="P-99", primary_evidence_ref="mystery:app#beta"),
    ]
    assert len(_aggregate(findings)["grouped_findings"]) == 2


# ── Action register, remaining rules ─────────────────────────────────


def test_no_action_is_a_placeholder():
    """"None." must never reach the register, whatever status carried it."""
    findings = [
        screening_subject_finding("s1", status="unavailable",
                                  availability_status="data_absent", required_action="None."),
        screening_subject_finding("s2", required_action=""),
    ]
    actions = _aggregate(findings)["required_actions"]
    for action in actions:
        assert action["action"].strip() not in {"", "None.", "None", "N/A", "-", "TBC"}


def test_action_priority_is_the_highest_contributing_severity():
    findings = [
        screening_subject_finding("s1", severity="low", required_action="Same work."),
        screening_subject_finding("s2", severity="critical", required_action="Same work."),
        screening_subject_finding("s3", severity="medium", required_action="Same work."),
    ]
    actions = _aggregate(findings)["required_actions"]
    assert len(actions) == 1
    assert actions[0]["priority"] == "critical"
    assert len(actions[0]["finding_ids"]) == 3


def test_identical_action_text_with_different_closures_keeps_every_finding():
    """Merged for presentation, never merged away.

    The same sentence closing on two different pieces of evidence is one piece
    of work whose completion is judged twice. Both closure conditions and both
    findings stay attached, so closing one cannot silently close the other.
    """
    findings = [
        screening_subject_finding("s1", required_action="Re-run screening.",
                                  close_condition="Live result for s1."),
        screening_subject_finding("s2", required_action="Re-run screening.",
                                  close_condition="Live result for s2."),
    ]
    action = _aggregate(findings)["required_actions"][0]
    assert action["close_conditions"] == ["Live result for s1.", "Live result for s2."]
    assert len(action["finding_ids"]) == 2
    assert len(action["evidence_required"]) >= 2


def test_an_unknown_category_stays_visibly_unassigned():
    findings = [finding(category="not_a_governed_category")]
    output = _aggregate(findings)
    assert output["grouped_findings"][0]["owner_roles"] == ["unassigned"]
    assert output["required_actions"][0]["owner_roles"] == ["unassigned"]


# ── Overall status, mixed and degenerate cases ───────────────────────


@pytest.mark.parametrize(
    "severity,expected",
    [
        ("critical", CRITICAL_CONCERNS),
        ("high", MATERIAL_CONCERNS),
        ("medium", INCOMPLETE_REVIEW),
        ("low", INCOMPLETE_REVIEW),
        ("info", INCOMPLETE_REVIEW),
    ],
)
def test_status_when_a_hit_and_an_unavailable_check_coexist(severity, expected):
    findings = [
        finding(severity=severity),
        finding(
            status="unavailable",
            availability_status="data_absent",
            probe_id="P-03",
            primary_evidence_ref="application:app-fixture#edd_route",
        ),
    ]
    assert _status(findings) == expected


def test_only_not_applicable_is_not_incomplete_and_not_a_concern():
    findings = [
        finding(status="not_applicable", probe_id="P-06",
                primary_evidence_ref="application:app-fixture#status"),
        finding(status="not_applicable", probe_id="P-04"),
    ]
    output = _aggregate(findings)
    assert output["overall_assessment"]["overall_status"] == NO_MATERIAL_FINDINGS
    assert output["unavailable_checks"] == []
    assert output["review_completeness"]["probes_not_applicable"] == ["P-04", "P-06"]


def test_severity_winning_never_hides_the_incomplete_portion():
    """The precedence rule may pick a status; it may not quieten the caveat."""
    findings = [
        finding(severity="critical"),
        finding(
            status="unavailable",
            availability_status="credentials_absent",
            probe_id="P-03",
            primary_evidence_ref="application:app-fixture#edd_route",
        ),
        finding(
            status="not_replayable",
            availability_status="snapshot_incomplete",
            probe_id="P-06",
            primary_evidence_ref="periodic_review:1#next_review_date",
        ),
    ]
    output = _aggregate(findings)
    assert output["overall_assessment"]["overall_status"] == CRITICAL_CONCERNS
    # …and the incompleteness is still prominent, in three separate places.
    assert len(output["unavailable_checks"]) == 2
    assert output["review_completeness"]["unevaluable_count"] == 2
    assert output["review_completeness"]["probes_unavailable"] == ["P-03"]
    assert output["review_completeness"]["probes_not_replayable"] == ["P-06"]
    assert len(output["missing_evidence"]) >= 2


def test_a_probe_runtime_failure_lands_as_an_unavailable_check(db):
    """A crashed probe must reach the review as a visible could-not-run entry."""
    from supervisor_foundation import assemble_application_bundle, run_review
    from supervisor_foundation.policy import unconfigured_policy
    from supervisor_foundation.probes import PROBES

    def exploding_probe(bundle, policy):
        raise RuntimeError("probe blew up")

    exploding_probe.__module__ = "supervisor_foundation.probes.risk_resolution"

    app_id = seed_application(db)
    bundle = assemble_application_bundle(db, app_id, as_of="2026-07-31T00:00:00Z")
    review = run_review(
        bundle, policy=unconfigured_policy(), probes=[*PROBES, exploding_probe]
    )
    output = aggregate_review(review, bundle=bundle)

    execution = [
        entry for entry in output["unavailable_checks"] if entry["check_id"].endswith(".execution")
    ]
    assert execution, "a crashed probe must appear in unavailable_checks"
    assert output["overall_assessment"]["overall_status"] in (
        INCOMPLETE_REVIEW,
        CONCERNS_IDENTIFIED,
        MATERIAL_CONCERNS,
        CRITICAL_CONCERNS,
    )
    assert output["review_completeness"]["unevaluable_count"] >= 1
    # The failure is a platform defect, so it must not read as a customer concern.
    assert all(
        "could not run" not in concern["claim"].lower()
        for concern in output["material_concerns"]
    )
