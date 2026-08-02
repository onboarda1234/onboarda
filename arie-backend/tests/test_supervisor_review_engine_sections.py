"""Review engine — the sections an officer acts on.

Contradictions, missing evidence, required actions, regulator questions,
limitations and the evidence index. The recurring risk in all six is
overstatement: a section that labels every finding a contradiction, or asks a
question nothing supports, is worse than no section, because it teaches the
officer to stop reading it.
"""

from __future__ import annotations

import copy
import json

import pytest

from supervisor_foundation.aggregation import aggregate_review
from supervisor_foundation.aggregation.checks import (
    CHECK_REGISTER,
    CHECK_UNAVAILABLE,
    CONTRADICTION_TYPES,
    EVIDENCE_ABSENT,
    EVIDENCE_MALFORMED,
    EVIDENCE_NOT_DEFENSIBLE,
    EVIDENCE_NOT_REPLAYABLE,
    MISSING_EVIDENCE_CLASSES,
    check_key,
    evidence_type,
    owner_for_category,
)
from supervisor_foundation.aggregation.limitations import (
    LIMITATION_KINDS,
    LIMITATIONS,
)
from supervisor_foundation.probes import PROBE_IDS
from supervisor_probe_fixtures import (
    add_edd_case,
    add_memo,
    add_periodic_review,
    add_screening,
    approve,
    factor,
    person_entry,
    risk_dimensions,
    routing_facts,
    screening_record,
    seed_application,
)
from supervisor_review_fixtures import (
    APPROVED_BUNDLE,
    MINIMAL_BUNDLE,
    finding,
    real_review,
    screening_subject_finding,
    synthetic_review,
)


def _aggregate(findings, bundle=None):
    return aggregate_review(
        synthetic_review(findings), bundle=bundle if bundle is not None else MINIMAL_BUNDLE
    )


def _bundle_with(**dependencies):
    bundle = copy.deepcopy(MINIMAL_BUNDLE)
    bundle["availability"] = {
        "dependencies": {**MINIMAL_BUNDLE["availability"]["dependencies"], **dependencies}
    }
    return bundle


# ── The register itself ──────────────────────────────────────────────


def test_every_registered_check_belongs_to_a_merged_probe():
    for probe_id, _key in CHECK_REGISTER:
        assert probe_id in PROBE_IDS


def test_check_ids_are_unique():
    ids = [check.check_id for check in CHECK_REGISTER.values()]
    assert len(ids) == len(set(ids))


def test_every_group_claim_template_renders():
    for (probe_id, key), check in CHECK_REGISTER.items():
        rendered = check.group_claim_template.format(
            count=3, noun=check.anchor_noun_plural, noun_singular=check.anchor_noun
        )
        assert rendered.strip(), f"{probe_id}/{key} renders empty"


def test_every_declared_contradiction_type_is_defined():
    for check in CHECK_REGISTER.values():
        if check.contradiction_type is not None:
            assert check.contradiction_type in CONTRADICTION_TYPES


def test_every_declared_missing_evidence_class_is_defined():
    for check in CHECK_REGISTER.values():
        if check.missing_evidence_class is not None:
            assert check.missing_evidence_class in MISSING_EVIDENCE_CLASSES


@pytest.mark.parametrize(
    "reference,expected",
    [
        ("screening:app#subject:s1#provider_mode", "provider_mode"),
        ("risk:app#factor:country_of_incorporation", "factor"),
        ("risk:app#factor:monthly_volume", "factor"),
        ("periodic_review:12", ""),
        ("periodic_review:12#next_review_date", "next_review_date"),
        ("application:app#edd_route", "edd_route"),
        ("", ""),
    ],
)
def test_check_key_derivation(reference, expected):
    assert check_key(reference) == expected


def test_the_register_covers_every_check_the_real_probes_emit(db):
    """The one test that catches reference-shape drift.

    A probe that renames an evidence anchor stops matching its register entry
    and silently loses its grouping. This fails instead.
    """
    seen: set[tuple[str, str]] = set()
    for builder in (
        _case_with_everything,
        _case_bare,
        _case_edd_divergent,
        _case_edd_case_missing,
        _case_edd_case_present,
    ):
        app_id = builder(db)
        review, _bundle = real_review(db, app_id)
        for item in review["findings"]:
            seen.add((item["probe_id"], check_key(item["primary_evidence_ref"])))

    missing = sorted(pair for pair in seen if pair not in CHECK_REGISTER)
    assert not missing, (
        f"probes emit checks the register does not know: {missing}. Grouping "
        "for these degrades to singletons until they are registered."
    )


# ── Contradictions ───────────────────────────────────────────────────


def test_an_ordinary_finding_is_not_a_contradiction():
    """Freshness expiry is a defect. Two facts do not conflict."""
    findings = [
        screening_subject_finding(
            "s1",
            primary_evidence_ref="screening:app-fixture#subject:s1#screening_valid_until",
        )
    ]
    assert _aggregate(findings)["contradictions"] == []


def test_an_undefensible_screening_relied_on_for_approval_is_a_contradiction():
    findings = [screening_subject_finding("s1", severity="critical")]
    entries = _aggregate(findings, bundle=APPROVED_BUNDLE)["contradictions"]
    assert [entry["contradiction_type"] for entry in entries] == [
        "approval_on_undefensible_screening"
    ]


def test_an_undefensible_screening_on_an_unapproved_case_is_not_a_contradiction():
    """Gated on the decision record. A critical severity does not unlock it."""
    findings = [screening_subject_finding("s1", severity="critical")]
    assert _aggregate(findings, bundle=MINIMAL_BUNDLE)["contradictions"] == []


def test_edd_routing_divergence_is_a_contradiction():
    findings = [
        finding(
            probe_id="P-03",
            category="policy",
            severity="critical",
            primary_evidence_ref="application:app-fixture#edd_route",
        )
    ]
    entries = _aggregate(findings)["contradictions"]
    assert entries[0]["contradiction_type"] == "edd_routing_divergence"
    assert entries[0]["conflict"] == CONTRADICTION_TYPES["edd_routing_divergence"]


def test_a_missing_edd_case_is_a_contradiction():
    findings = [
        finding(
            probe_id="P-03",
            category="edd",
            primary_evidence_ref="application:app-fixture#edd_case_existence",
        )
    ]
    assert _aggregate(findings)["contradictions"][0]["contradiction_type"] == "edd_case_absent"


def test_adverse_media_clear_on_absent_data_is_a_contradiction():
    findings = [
        finding(
            probe_id="P-04",
            category="adverse_media",
            primary_evidence_ref="risk:app-fixture#factor:adverse_media",
        )
    ]
    assert _aggregate(findings)["contradictions"][0]["contradiction_type"] == (
        "adverse_media_clear_on_absent_data"
    )


def test_an_unattributed_elevation_is_a_contradiction():
    findings = [
        finding(
            probe_id="P-02",
            category="risk",
            primary_evidence_ref="application:app-fixture#elevation_reason_text",
        )
    ]
    assert _aggregate(findings)["contradictions"][0]["contradiction_type"] == (
        "unattributed_risk_elevation"
    )


def test_a_non_hit_never_becomes_a_contradiction():
    findings = [
        finding(
            probe_id="P-03",
            category="edd",
            status="unavailable",
            availability_status="data_absent",
            primary_evidence_ref="application:app-fixture#edd_case_existence",
        )
    ]
    assert _aggregate(findings)["contradictions"] == []


def test_every_contradiction_carries_its_findings_and_evidence():
    findings = [screening_subject_finding(key, severity="critical") for key in ("s1", "s2")]
    entry = _aggregate(findings, bundle=APPROVED_BUNDLE)["contradictions"][0]
    assert len(entry["finding_ids"]) == 2
    assert entry["evidence_refs"]
    assert entry["affected_count"] == 2


# ── Missing evidence ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status,availability,expected",
    [
        ("unavailable", "data_absent", EVIDENCE_ABSENT),
        ("unavailable", "snapshot_incomplete", EVIDENCE_MALFORMED),
        ("unavailable", "credentials_absent", CHECK_UNAVAILABLE),
        ("unavailable", "dependency_gated", CHECK_UNAVAILABLE),
        ("unavailable", "policy_not_configured", CHECK_UNAVAILABLE),
        ("not_replayable", "snapshot_incomplete", EVIDENCE_NOT_REPLAYABLE),
    ],
)
def test_unevaluable_findings_are_classified_by_why(status, availability, expected):
    findings = [screening_subject_finding("s1", status=status, availability_status=availability)]
    entries = _aggregate(findings)["missing_evidence"]
    assert [entry["classification"] for entry in entries] == [expected]


def test_a_hit_on_an_evidence_check_is_present_but_not_defensible():
    findings = [screening_subject_finding("s1")]
    entries = _aggregate(findings)["missing_evidence"]
    assert entries[0]["classification"] == EVIDENCE_NOT_DEFENSIBLE


def test_the_five_classes_are_never_collapsed():
    findings = [
        screening_subject_finding("s1"),
        screening_subject_finding("s2", status="unavailable", availability_status="data_absent"),
        screening_subject_finding("s3", status="unavailable", availability_status="snapshot_incomplete"),
        screening_subject_finding("s4", status="unavailable", availability_status="credentials_absent"),
        screening_subject_finding("s5", status="not_replayable", availability_status="snapshot_incomplete"),
    ]
    classes = {entry["classification"] for entry in _aggregate(findings)["missing_evidence"]}
    assert classes == set(MISSING_EVIDENCE_CLASSES)


def test_every_missing_evidence_entry_answers_the_five_questions():
    findings = [screening_subject_finding("s1")]
    entry = _aggregate(findings)["missing_evidence"][0]
    assert entry["what_is_missing"]
    assert entry["why_it_matters"]
    assert entry["finding_ids"]
    assert entry["required_actions"]
    assert entry["close_conditions"]


def test_a_control_failure_that_is_not_an_evidence_gap_is_not_listed():
    """P-04's freshness-evaluation failure has no evidence class registered."""
    findings = [
        finding(
            probe_id="P-04",
            primary_evidence_ref="application:app-fixture#as_of",
            status="hit",
        )
    ]
    assert _aggregate(findings)["missing_evidence"] == []


# ── Required actions ─────────────────────────────────────────────────


def test_identical_actions_across_findings_deduplicate():
    findings = [screening_subject_finding(key) for key in ("s1", "s2", "s3")]
    actions = _aggregate(findings)["required_actions"]
    assert len(actions) == 1
    assert len(actions[0]["finding_ids"]) == 3


def test_different_actions_stay_separate():
    findings = [
        screening_subject_finding("s1", required_action="Re-run screening."),
        screening_subject_finding("s2", required_action="Record a disposition."),
    ]
    assert len(_aggregate(findings)["required_actions"]) == 2


def test_actions_are_ordered_by_priority():
    findings = [
        screening_subject_finding("s1", severity="medium", required_action="Medium work."),
        screening_subject_finding("s2", severity="critical", required_action="Critical work."),
    ]
    actions = _aggregate(findings)["required_actions"]
    assert [action["priority"] for action in actions] == ["critical", "medium"]


def test_an_action_takes_its_owner_from_its_own_findings_not_its_group():
    """A group can span categories; an action must not inherit all their owners."""
    findings = [
        finding(
            probe_id="P-02",
            category="jurisdiction",
            primary_evidence_ref="risk:app-fixture#factor:country_of_incorporation",
            required_action="Map the country value.",
        ),
        finding(
            probe_id="P-02",
            category="risk",
            primary_evidence_ref="risk:app-fixture#factor:monthly_volume",
            required_action="Re-score the assessment.",
        ),
    ]
    output = _aggregate(findings)
    assert len(output["grouped_findings"]) == 1, "these are one concern"
    by_action = {action["action"]: action["owner_roles"] for action in output["required_actions"]}
    assert by_action["Map the country value."] == [owner_for_category("jurisdiction")]
    assert by_action["Re-score the assessment."] == [owner_for_category("risk")]


def test_a_clear_finding_contributes_no_action():
    """Probes write "None." as the action on a passing check."""
    findings = [screening_subject_finding("s1", status="clear", required_action="None.")]
    assert _aggregate(findings)["required_actions"] == []


def test_an_unavailable_check_does_contribute_an_action():
    """Something has to be done about a control nobody could evaluate."""
    findings = [
        screening_subject_finding(
            "s1",
            status="unavailable",
            availability_status="credentials_absent",
            required_action="Configure the provider credentials.",
        )
    ]
    actions = _aggregate(findings)["required_actions"]
    assert [action["action"] for action in actions] == ["Configure the provider credentials."]


def test_actions_preserve_closure_conditions():
    findings = [
        screening_subject_finding("s1", close_condition="Live result for s1."),
        screening_subject_finding("s2", close_condition="Live result for s2."),
    ]
    action = _aggregate(findings)["required_actions"][0]
    assert action["close_conditions"] == ["Live result for s1.", "Live result for s2."]


def test_action_ids_are_stable_across_runs():
    findings = [screening_subject_finding("s1")]
    first = _aggregate(findings)["required_actions"][0]["action_id"]
    second = _aggregate(findings)["required_actions"][0]["action_id"]
    assert first == second


def test_action_ids_differ_between_different_actions():
    findings = [
        screening_subject_finding("s1", required_action="One thing."),
        screening_subject_finding("s2", required_action="Another thing."),
    ]
    ids = {action["action_id"] for action in _aggregate(findings)["required_actions"]}
    assert len(ids) == 2


def test_no_action_assigns_work_or_names_an_invented_role():
    findings = [screening_subject_finding("s1")]
    action = _aggregate(findings)["required_actions"][0]
    governed = {"Officer", "SCO", "MLRO", "Ops", "unassigned"}
    assert set(action["owner_roles"]) <= governed
    assert "assigned_to" not in action
    assert "due_date" not in action


# ── Regulatory challenges ────────────────────────────────────────────


def test_a_challenge_maps_to_findings_evidence_and_actions():
    findings = [screening_subject_finding(key, severity="critical") for key in ("s1", "s2")]
    challenge = _aggregate(findings)["potential_regulatory_challenges"][0]
    assert challenge["finding_ids"]
    assert challenge["evidence_refs"]
    assert challenge["action_ids"]
    assert challenge["question"].endswith("?")


def test_challenges_are_never_generic():
    findings = [screening_subject_finding("s1"), finding(probe_id="P-02",
                primary_evidence_ref="risk:app-fixture#factor:a")]
    generic = ("all aml risks", "considered all", "in general", "any other")
    for challenge in _aggregate(findings)["potential_regulatory_challenges"]:
        lowered = challenge["question"].lower()
        assert not any(phrase in lowered for phrase in generic)


def test_a_challenge_reads_correctly_for_one_and_for_many():
    """Count-driven templates are how "each of the 1 match" gets shipped."""
    one = _aggregate([screening_subject_finding("s1", severity="critical")])
    many = _aggregate(
        [screening_subject_finding(key, severity="critical") for key in ("s1", "s2", "s3")]
    )
    for output in (one, many):
        question = output["potential_regulatory_challenges"][0]["question"]
        assert " 1 " not in question
        assert "  " not in question
        assert "required required" not in question


def test_only_hits_raise_challenges():
    findings = [
        screening_subject_finding("s1", status="unavailable", availability_status="data_absent")
    ]
    assert _aggregate(findings)["potential_regulatory_challenges"] == []


def test_challenge_wording_is_deterministic():
    findings = [screening_subject_finding("s1", severity="critical")]
    first = [c["question"] for c in _aggregate(findings)["potential_regulatory_challenges"]]
    second = [c["question"] for c in _aggregate(findings)["potential_regulatory_challenges"]]
    assert first == second


def test_a_check_with_no_challenge_template_raises_none():
    findings = [
        finding(probe_id="P-04", primary_evidence_ref="application:app-fixture#as_of")
    ]
    assert _aggregate(findings)["potential_regulatory_challenges"] == []


# ── Limitations ──────────────────────────────────────────────────────


def test_limitation_kinds_are_from_the_closed_vocabulary():
    for limitation in LIMITATIONS:
        assert limitation.kind in LIMITATION_KINDS


def test_the_policy_probe_limitation_is_always_disclosed():
    ids = {entry["limitation_id"] for entry in _aggregate([])["limitations"]}
    assert "policy_dependent_probes_not_implemented" in ids


def test_the_p03_limitation_appears_when_p03_ran():
    findings = [finding(probe_id="P-03", primary_evidence_ref="application:app-fixture#edd_route")]
    ids = {entry["limitation_id"] for entry in _aggregate(findings)["limitations"]}
    assert "p03_asymmetric_replay" in ids


def test_the_p03_limitation_is_absent_when_p03_did_not_run():
    findings = [finding(probe_id="P-02", primary_evidence_ref="risk:app-fixture#factor:a")]
    ids = {entry["limitation_id"] for entry in _aggregate(findings)["limitations"]}
    assert "p03_asymmetric_replay" not in ids


def test_the_p04_live_provider_limitation_appears_on_a_screening_hit():
    ids = {
        entry["limitation_id"]
        for entry in _aggregate([screening_subject_finding("s1")])["limitations"]
    }
    assert "p04_live_provider_validation" in ids


def test_the_adverse_media_limitation_follows_the_category():
    findings = [
        finding(
            probe_id="P-04",
            category="adverse_media",
            primary_evidence_ref="risk:app-fixture#factor:adverse_media",
        )
    ]
    ids = {entry["limitation_id"] for entry in _aggregate(findings)["limitations"]}
    assert "adverse_media_source_coverage" in ids


def test_the_historic_replay_limitation_follows_not_replayable():
    findings = [
        screening_subject_finding(
            "s1", status="not_replayable", availability_status="snapshot_incomplete"
        )
    ]
    ids = {entry["limitation_id"] for entry in _aggregate(findings)["limitations"]}
    assert "historic_snapshot_not_reconstructable" in ids


def test_an_absent_dependency_is_disclosed_as_an_environment_gap():
    bundle = _bundle_with(public_registry="credentials_absent")
    entries = {
        entry["limitation_id"]: entry["kind"]
        for entry in _aggregate([], bundle=bundle)["limitations"]
    }
    assert entries.get("registry_credentials_absent") == "environment_gap"


def test_a_product_boundary_is_not_described_as_a_defect():
    entries = {entry["limitation_id"]: entry for entry in _aggregate([])["limitations"]}
    boundary = entries["policy_dependent_probes_not_implemented"]
    assert boundary["kind"] == "product_boundary"
    for word in ("defect", "bug", "broken", "failure"):
        assert word not in boundary["statement"].lower()


def test_limitations_do_not_soften_a_finding():
    """A limitation is disclosure. It never touches a severity or a status."""
    findings = [screening_subject_finding("s1", severity="critical")]
    output = _aggregate(findings)
    assert output["limitations"]
    assert output["overall_assessment"]["overall_status"] == "critical_concerns"
    assert output["grouped_findings"][0]["severity"] == "critical"


def test_limitation_order_is_stable():
    findings = [screening_subject_finding("s1")]
    assert [entry["limitation_id"] for entry in _aggregate(findings)["limitations"]] == sorted(
        entry["limitation_id"] for entry in _aggregate(findings)["limitations"]
    )


# ── Evidence index ───────────────────────────────────────────────────


def test_the_index_deduplicates_shared_references():
    findings = [screening_subject_finding(key) for key in ("s1", "s2")]
    index = _aggregate(findings)["evidence_index"]
    refs = [record["evidence_ref"] for record in index]
    assert len(refs) == len(set(refs))
    shared = [r for r in index if r["evidence_ref"] == "application:app-fixture#status"]
    assert len(shared) == 1
    assert len(shared[0]["finding_ids"]) == 2


def test_the_index_loses_no_reference():
    findings = [screening_subject_finding(key) for key in ("s1", "s2", "s3")]
    cited = {ref for item in findings for ref in item["evidence_refs"]}
    indexed = {record["evidence_ref"] for record in _aggregate(findings)["evidence_index"]}
    assert cited == indexed


def test_index_order_is_stable_and_sorted():
    findings = [screening_subject_finding(key) for key in ("s3", "s1", "s2")]
    refs = [record["evidence_ref"] for record in _aggregate(findings)["evidence_index"]]
    assert refs == sorted(refs)


@pytest.mark.parametrize(
    "reference,expected",
    [
        ("screening:app#subject:s1", "screening_evidence"),
        ("risk:app#factor:x", "risk_factor"),
        ("periodic_review:9", "periodic_review"),
        ("decision:4", "decision_record"),
        ("policy_profile:v1#ubo_identification_threshold", "policy_configuration"),
        ("something_new:1", "other"),
    ],
)
def test_evidence_types_come_from_an_explicit_prefix_table(reference, expected):
    assert evidence_type(reference) == expected


def test_the_index_carries_no_raw_payload_or_direct_identifier():
    """References only: a map of what was looked at, not a copy of it."""
    findings = [screening_subject_finding("s1")]
    for record in _aggregate(findings)["evidence_index"]:
        assert set(record) == {
            "action_ids",
            "availability_states",
            "evidence_ref",
            "evidence_type",
            "finding_ids",
            "source_modules",
        }


def test_the_index_links_references_to_actions():
    findings = [screening_subject_finding("s1")]
    index = _aggregate(findings)["evidence_index"]
    assert any(record["action_ids"] for record in index)


def test_a_real_case_review_exposes_no_person_name(db):
    """Subject keys, never names — across the whole review, not just the index.

    Scanning only ``evidence_index`` would pass while a name sat in a group
    claim, a required action or a regulator question. The guarantee is about the
    artifact, so the scan is over the serialised artifact.
    """
    app_id = _case_with_everything(db)
    review, bundle = real_review(db, app_id)
    blob = json.dumps(aggregate_review(review, bundle=bundle)).lower()
    for name in ("director one", "director two", "ubo one"):
        assert name not in blob


# ── Case builders shared by the register-coverage test ───────────────


def _case_with_everything(db) -> str:
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


def _case_bare(db) -> str:
    return seed_application(db)


def _case_edd_divergent(db) -> str:
    """A case whose stored route is standard while the facts require EDD.

    Reaches P-03's route and case-existence checks, which the other two builders
    never touch — without it the register-coverage test would pass while leaving
    P-03's two most important anchors unexercised.
    """
    app_id = seed_application(db, risk_level="VERY_HIGH", final_risk_level="VERY_HIGH")
    add_memo(
        db,
        app_id,
        facts=routing_facts(final_risk_level="VERY_HIGH", declared_pep_present=True),
        stored_route="standard",
    )
    add_periodic_review(db, app_id, status="pending")
    approve(db, app_id)
    return app_id


def _case_edd_case_missing(db) -> str:
    """Stored route is EDD; no EDD case row exists."""
    app_id = seed_application(db, risk_level="VERY_HIGH", final_risk_level="VERY_HIGH")
    add_memo(
        db,
        app_id,
        facts=routing_facts(final_risk_level="VERY_HIGH", declared_pep_present=True),
        stored_route="edd",
    )
    approve(db, app_id)
    return app_id


def _case_edd_case_present(db) -> str:
    app_id = seed_application(db, risk_level="VERY_HIGH", final_risk_level="VERY_HIGH")
    add_memo(
        db,
        app_id,
        facts=routing_facts(final_risk_level="VERY_HIGH", declared_pep_present=True),
        stored_route="edd",
    )
    add_edd_case(db, app_id)
    add_periodic_review(db, app_id, status="pending", next_review_date="2020-01-01")
    approve(db, app_id)
    return app_id


# ── Approval-dependent language ──────────────────────────────────────
# The review may say "approved" only where the structured bundle establishes
# approval. Gated on the decision record, never on severity: severity is another
# layer's encoding of reliance, and the original code asked the approval
# question unconditionally — putting a false statement about a decision into a
# document an officer may hand to a regulator.

APPROVAL_WORDS = ("approved", "approval", "relied on", "reliance was", "accepted the customer")


def _screening_case(db, *, status: str, decision: bool = False) -> str:
    app_id = seed_application(
        db, status=status, risk_level="HIGH", final_risk_level="HIGH"
    )
    add_memo(db, app_id)
    record = screening_record(api_status="sandbox")
    add_screening(
        db,
        app_id,
        company=record,
        directors=[person_entry("Director One", record), person_entry("Director Two", record)],
    )
    if decision:
        approve(db, app_id)
    return app_id


@pytest.mark.parametrize(
    "status",
    ["draft", "submitted", "in_review", "under_review", "kyc_documents", "rejected", "withdrawn"],
)
def test_no_question_claims_approval_on_an_unapproved_case(db, status):
    app_id = _screening_case(db, status=status)
    review, bundle = real_review(db, app_id)
    output = aggregate_review(review, bundle=bundle)

    assert output["potential_regulatory_challenges"], "the fixture must raise questions"
    for challenge in output["potential_regulatory_challenges"]:
        lowered = challenge["question"].lower()
        assert "approved" not in lowered, (
            f"status {status!r} produced an approval claim: {challenge['question']}"
        )


@pytest.mark.parametrize("status", ["draft", "in_review", "rejected"])
def test_no_contradiction_claims_approval_on_an_unapproved_case(db, status):
    app_id = _screening_case(db, status=status)
    review, bundle = real_review(db, app_id)
    output = aggregate_review(review, bundle=bundle)
    for entry in output["contradictions"]:
        assert entry["contradiction_type"] != "approval_on_undefensible_screening"
        assert "approved" not in entry["conflict"].lower()


def test_the_neutral_variant_still_asks_something_useful(db):
    app_id = _screening_case(db, status="in_review")
    review, bundle = real_review(db, app_id)
    output = aggregate_review(review, bundle=bundle)
    questions = [c["question"] for c in output["potential_regulatory_challenges"]]
    assert any("live, terminal screening result" in q for q in questions)


def test_the_approval_question_does_appear_once_approval_is_established(db):
    """The neutral variant must not simply suppress the question everywhere."""
    app_id = _screening_case(db, status="approved", decision=True)
    review, bundle = real_review(db, app_id)
    output = aggregate_review(review, bundle=bundle)
    questions = [c["question"] for c in output["potential_regulatory_challenges"]]
    assert any("approved" in q.lower() for q in questions)
    types = {entry["contradiction_type"] for entry in output["contradictions"]}
    assert "approval_on_undefensible_screening" in types


def test_a_decision_record_alone_establishes_approval(db):
    """Status and decision record are independent signals; either is sufficient."""
    app_id = _screening_case(db, status="in_review", decision=True)
    review, bundle = real_review(db, app_id)
    output = aggregate_review(review, bundle=bundle)
    assert any(
        "approved" in c["question"].lower()
        for c in output["potential_regulatory_challenges"]
    )


@pytest.mark.parametrize(
    "bundle_patch",
    [
        {"application": None, "decision": None},
        {"application": {"fields": None}, "decision": {"records": None}},
        {"application": "not-a-mapping", "decision": "not-a-mapping"},
        {"application": {"fields": {}}, "decision": {"records": "not-a-list"}},
        {"application": {"fields": {"status": None}}, "decision": {"records": [None, 42, "x"]}},
        {"application": {}, "decision": {}},
        {"application": {"fields": {"status": "pending"}}, "decision": {"records": []}},
    ],
    ids=[
        "both-none",
        "fields-none-records-none",
        "both-scalar",
        "records-scalar",
        "status-none-records-junk",
        "both-empty",
        "pending-with-no-records",
    ],
)
def test_malformed_decision_evidence_never_establishes_approval(db, bundle_patch):
    app_id = _screening_case(db, status="approved", decision=True)
    review, bundle = real_review(db, app_id)
    damaged = {**bundle, **bundle_patch}

    output = aggregate_review(review, bundle=damaged)
    for challenge in output["potential_regulatory_challenges"]:
        assert "approved" not in challenge["question"].lower()
    for entry in output["contradictions"]:
        assert entry["contradiction_type"] != "approval_on_undefensible_screening"


def test_approval_is_not_inferred_from_severity(db):
    """A critical finding on an unapproved case must not unlock approval wording."""
    findings = [
        screening_subject_finding("s1", severity="critical"),
        screening_subject_finding("s2", severity="critical"),
    ]
    output = aggregate_review(
        synthetic_review(findings),
        bundle={"meta": MINIMAL_BUNDLE["meta"], "availability": MINIMAL_BUNDLE["availability"]},
    )
    for challenge in output["potential_regulatory_challenges"]:
        assert "approved" not in challenge["question"].lower()
    assert output["contradictions"] == []


def test_every_approval_template_declares_a_neutral_alternative():
    """Static guard: a new approval-worded template cannot ship without one."""
    for (probe_id, key), check in CHECK_REGISTER.items():
        template = check.regulatory_challenge_template or ""
        if "approved" in template.lower() and "approved risk-configuration" not in template:
            if "approved review-frequency" in template:
                continue  # qualifies the policy version, not the customer
            assert check.regulatory_challenge_template_unapproved is not None, (
                f"{probe_id}/{key} asserts approval with no neutral variant"
            )


def test_no_group_claim_asserts_a_decision():
    """Group claims describe evidence. Only the probes may speak about a decision."""
    for (probe_id, key), check in CHECK_REGISTER.items():
        lowered = check.group_claim_template.lower()
        for word in ("approved", "relied on", "accepted"):
            assert word not in lowered, f"{probe_id}/{key} group claim says {word!r}"


def test_probe_claims_about_approval_are_the_probes_own_gate(db):
    """The findings' own approval sentences follow the probe's is_approved gate.

    Recorded rather than changed: the probes are merged and this layer copies
    their claims verbatim. If a probe's gate were wrong, this test fails here
    instead of the wording quietly reaching an officer through aggregation.
    """
    unapproved = _screening_case(db, status="in_review")
    review, _bundle = real_review(db, unapproved)
    for item in review["findings"]:
        assert "was approved" not in str(item["claim"]).lower()

    approved = _screening_case(db, status="approved", decision=True)
    review, _bundle = real_review(db, approved)
    assert any("approved" in str(item["claim"]).lower() for item in review["findings"])


@pytest.mark.parametrize("status", ["approved", "APPROVED", " approved ", "Approved"])
def test_a_padded_or_cased_approved_status_still_establishes_approval(db, status):
    """Normalisation, not leniency: a padded governed value is the same value."""
    app_id = _screening_case(db, status="in_review")
    review, bundle = real_review(db, app_id)
    patched = {**bundle, "application": {"fields": {"status": status}}, "decision": {"records": []}}
    output = aggregate_review(review, bundle=patched)
    assert any(
        "approved" in challenge["question"].lower()
        for challenge in output["potential_regulatory_challenges"]
    )


# ── Question quality ─────────────────────────────────────────────────


def _word_overlap(question: str, claim: str) -> float:
    question_words = set(question.lower().replace("?", "").split())
    claim_words = set(claim.lower().replace(".", "").split())
    return len(question_words & claim_words) / max(1, len(question_words))


def test_a_question_is_not_a_restatement_of_its_claim(db):
    """A question that echoes the finding adds a line and no information.

    Measured as word overlap against the group's own claim, over real probe
    output. The threshold is loose on purpose — a question *should* share the
    subject's vocabulary; it must not share the whole sentence.
    """
    app_id = _case_with_everything(db)
    review, bundle = real_review(db, app_id)
    output = aggregate_review(review, bundle=bundle)
    claims = {group["group_id"]: group["claim"] for group in output["grouped_findings"]}

    assert output["potential_regulatory_challenges"], "fixture must raise questions"
    for challenge in output["potential_regulatory_challenges"]:
        overlap = _word_overlap(challenge["question"], claims[challenge["group_id"]])
        assert overlap < 0.8, (
            f"question restates its finding (overlap {overlap:.2f}): "
            f"{challenge['question']}"
        )


def test_every_question_asks_for_something_the_finding_does_not_state(db):
    app_id = _case_with_everything(db)
    review, bundle = real_review(db, app_id)
    output = aggregate_review(review, bundle=bundle)
    claims = {group["group_id"]: group["claim"] for group in output["grouped_findings"]}
    for challenge in output["potential_regulatory_challenges"]:
        assert challenge["question"] != claims[challenge["group_id"]]
        assert challenge["question"].rstrip().endswith("?")


def test_no_question_implies_the_customer_is_non_compliant(db):
    app_id = _case_with_everything(db)
    review, bundle = real_review(db, app_id)
    output = aggregate_review(review, bundle=bundle)
    for challenge in output["potential_regulatory_challenges"]:
        lowered = challenge["question"].lower()
        for phrase in ("non-compliant", "in breach", "violation of", "illegal", "unlawful"):
            assert phrase not in lowered


def test_questions_are_unique_within_one_review(db):
    """Two identical questions means grouping failed somewhere upstream."""
    app_id = _case_with_everything(db)
    review, bundle = real_review(db, app_id)
    output = aggregate_review(review, bundle=bundle)
    questions = [c["question"] for c in output["potential_regulatory_challenges"]]
    assert len(questions) == len(set(questions))


# ── Limitations: absence is as important as presence ─────────────────


def test_the_p04_limitation_is_absent_when_p04_found_nothing_and_screening_is_present():
    bundle = _bundle_with(screening_report="available")
    findings = [finding(probe_id="P-02", primary_evidence_ref="risk:app-fixture#factor:a")]
    ids = {entry["limitation_id"] for entry in _aggregate(findings, bundle=bundle)["limitations"]}
    assert "p04_live_provider_validation" not in ids


def test_the_adverse_media_limitation_is_absent_without_an_adverse_media_finding():
    findings = [screening_subject_finding("s1")]
    ids = {entry["limitation_id"] for entry in _aggregate(findings)["limitations"]}
    assert "adverse_media_source_coverage" not in ids


def test_the_historic_limitation_is_absent_on_a_fully_current_review():
    bundle = _bundle_with(public_registry="available")
    findings = [screening_subject_finding("s1")]
    ids = {entry["limitation_id"] for entry in _aggregate(findings, bundle=bundle)["limitations"]}
    assert "historic_snapshot_not_reconstructable" not in ids


def test_the_dependency_limitation_is_absent_when_nothing_was_unavailable():
    findings = [screening_subject_finding("s1")]
    ids = {entry["limitation_id"] for entry in _aggregate(findings)["limitations"]}
    assert "probe_dependency_unavailable" not in ids


def test_no_limitation_is_worded_as_a_customer_defect():
    findings = [
        screening_subject_finding("s1"),
        finding(probe_id="P-03", primary_evidence_ref="application:app-fixture#edd_route"),
        finding(probe_id="P-04", category="adverse_media",
                primary_evidence_ref="risk:app-fixture#factor:adverse_media"),
    ]
    for entry in _aggregate(findings)["limitations"]:
        lowered = entry["statement"].lower()
        for phrase in ("the customer failed", "the customer did not", "customer defect"):
            assert phrase not in lowered
