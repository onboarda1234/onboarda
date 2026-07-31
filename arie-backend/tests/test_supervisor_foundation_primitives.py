"""Unit tests for the Supervisor foundation primitives.

Canonical serialisation, hashing, the policy contract and the review runner's
contract enforcement. The reproducibility harness proves these compose; these
tests pin each rule individually so a regression names itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

import pytest

from supervisor_foundation import (
    POLICY_KEYS,
    AvailabilityStatus,
    FindingStatus,
    PolicyProfile,
    canonical_bytes,
    canonical_json,
    compute_finding_id,
    compute_review_hash,
    run_review,
)
from supervisor_foundation.canonical_json import CanonicalJSONError
from supervisor_foundation.hashing import (
    ExcludedHashKeyError,
    assert_no_excluded_keys,
    compute_input_bundle_hash,
)
from supervisor_foundation.policy import UnknownPolicyKeyError, unconfigured_policy
from supervisor_foundation.review import ProbeContractError


# ── Canonical JSON ───────────────────────────────────────────────────


def test_keys_are_sorted_at_every_level():
    value = {"b": {"z": 1, "a": 2}, "a": 3}
    assert canonical_json(value) == '{"a":3,"b":{"a":2,"z":1}}'


def test_insertion_order_is_irrelevant():
    assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})


def test_no_insignificant_whitespace():
    assert " " not in canonical_json({"a": [1, 2], "b": {"c": 3}})


def test_floats_round_to_four_places():
    assert canonical_json({"x": 0.1 + 0.2}) == '{"x":0.3}'
    assert canonical_json({"x": 1 / 3}) == '{"x":0.3333}'


def test_negative_zero_normalises():
    assert canonical_json({"x": -0.0}) == '{"x":0.0}'
    assert canonical_json({"x": -0.00001}) == '{"x":0.0}'


def test_non_finite_floats_are_rejected():
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(CanonicalJSONError):
            canonical_json({"x": value})


def test_decimal_is_normalised_like_a_float():
    assert canonical_json({"x": Decimal("0.30000")}) == '{"x":0.3}'


def test_null_is_preserved_and_differs_from_absence():
    assert canonical_json({"a": None}) == '{"a":null}'
    assert canonical_json({"a": None}) != canonical_json({})


def test_booleans_are_canonical_and_not_integers():
    assert canonical_json({"a": True, "b": 1}) == '{"a":true,"b":1}'
    assert canonical_json({"a": True}) != canonical_json({"a": 1})


def test_datetimes_normalise_to_utc_seconds():
    naive = datetime(2026, 7, 31, 12, 30, 15, 123456)
    offset = datetime(
        2026, 7, 31, 16, 30, 15, tzinfo=timezone(timedelta(hours=4))
    )
    assert canonical_json({"t": naive}) == '{"t":"2026-07-31T12:30:15Z"}'
    assert canonical_json({"t": offset}) == '{"t":"2026-07-31T12:30:15Z"}'


def test_enums_serialise_by_value():
    class Colour(str, Enum):
        RED = "red"

    assert canonical_json({"c": Colour.RED}) == '{"c":"red"}'


def test_list_order_is_preserved_because_it_is_semantic():
    assert canonical_json([3, 1, 2]) == "[3,1,2]"


def test_sets_sort_deterministically_across_mixed_types():
    first = canonical_json({"s": {3, "a", 1, "b"}})
    second = canonical_json({"s": {"b", 1, "a", 3}})
    assert first == second


def test_non_string_keys_are_rejected():
    with pytest.raises(CanonicalJSONError, match="object keys must be strings"):
        canonical_json({1: "a"})


def test_bytes_are_rejected():
    with pytest.raises(CanonicalJSONError, match="bytes"):
        canonical_json({"b": b"data"})


def test_unsupported_type_is_rejected():
    with pytest.raises(CanonicalJSONError):
        canonical_json({"o": object()})


def test_canonical_bytes_is_utf8_without_escaping():
    assert canonical_bytes({"n": "Zoë"}) == '{"n":"Zoë"}'.encode("utf-8")


# ── Hashing ──────────────────────────────────────────────────────────


def test_bundle_hash_is_stable_and_sensitive():
    bundle = {"a": 1, "b": [1, 2]}
    assert compute_input_bundle_hash(bundle) == compute_input_bundle_hash(
        {"b": [1, 2], "a": 1}
    )
    assert compute_input_bundle_hash(bundle) != compute_input_bundle_hash(
        {"a": 1, "b": [2, 1]}
    )


def test_excluded_keys_are_rejected_before_hashing():
    for key in ("checked_at", "generated_at", "narrative", "request_id"):
        with pytest.raises(ExcludedHashKeyError, match=key):
            compute_input_bundle_hash({"section": {key: "x"}})


def test_excluded_key_detection_reaches_inside_lists():
    with pytest.raises(ExcludedHashKeyError, match=r"\$.rows\[1\]"):
        assert_no_excluded_keys({"rows": [{"a": 1}, {"generated_at": "x"}]})


def test_review_hash_changes_with_each_of_its_four_inputs():
    base = dict(
        policy_version="p1",
        probe_set_version="s1",
        input_bundle_hash="h1",
        findings=[],
    )
    baseline = compute_review_hash(**base)
    assert compute_review_hash(**{**base, "policy_version": "p2"}) != baseline
    assert compute_review_hash(**{**base, "probe_set_version": "s2"}) != baseline
    assert compute_review_hash(**{**base, "input_bundle_hash": "h2"}) != baseline
    assert (
        compute_review_hash(
            **{**base, "findings": [{"probe_id": "X", "claim": "c"}]}
        )
        != baseline
    )


def test_review_hash_ignores_finding_emission_order():
    findings = [
        {"probe_id": "B", "category": "risk", "claim": "b", "finding_id": "2"},
        {"probe_id": "A", "category": "risk", "claim": "a", "finding_id": "1"},
    ]
    forward = compute_review_hash(
        policy_version="p", probe_set_version="s", input_bundle_hash="h",
        findings=findings,
    )
    backward = compute_review_hash(
        policy_version="p", probe_set_version="s", input_bundle_hash="h",
        findings=list(reversed(findings)),
    )
    assert forward == backward


def test_finding_id_is_derived_not_random():
    kwargs = dict(
        subject_type="application",
        subject_id="app-1",
        probe_id="P-01",
        probe_version="v1",
        category="ownership",
        primary_evidence_ref="ubo:1",
    )
    assert compute_finding_id(**kwargs) == compute_finding_id(**kwargs)
    assert len(compute_finding_id(**kwargs)) == 16


def test_finding_id_separates_distinct_evidence():
    def make(ref):
        return compute_finding_id(
            subject_type="application",
            subject_id="app-1",
            probe_id="P-01",
            probe_version="v1",
            category="ownership",
            primary_evidence_ref=ref,
        )

    assert make("ubo:1") != make("ubo:2")


# ── Policy contract ──────────────────────────────────────────────────


def test_unconfigured_policy_reports_every_key_unconfigured():
    policy = unconfigured_policy()
    for key in POLICY_KEYS:
        assert policy.is_configured(key) is False
        assert policy.availability(key) == AvailabilityStatus.POLICY_NOT_CONFIGURED


def test_get_refuses_to_substitute_a_default():
    """No template value may silently become authoritative."""
    policy = unconfigured_policy()
    with pytest.raises(UnknownPolicyKeyError, match="policy_not_configured"):
        policy.get("ubo_identification_threshold")


def test_get_has_no_default_parameter():
    """A ``default=`` argument would be a hole in the no-defaults rule."""
    import inspect

    signature = inspect.signature(PolicyProfile.get)
    assert list(signature.parameters) == ["self", "key"]


def test_configured_key_is_reported_available():
    policy = PolicyProfile(
        policy_version="inst-1", values={"ubo_identification_threshold": 25}
    )
    assert policy.is_configured("ubo_identification_threshold")
    assert policy.get("ubo_identification_threshold") == 25
    assert policy.availability("ubo_identification_threshold") == (
        AvailabilityStatus.AVAILABLE
    )
    assert policy.availability("pep_evidence_requirements") == (
        AvailabilityStatus.POLICY_NOT_CONFIGURED
    )


def test_explicit_none_is_not_configured():
    policy = PolicyProfile(
        policy_version="inst-1", values={"ubo_identification_threshold": None}
    )
    assert policy.is_configured("ubo_identification_threshold") is False


def test_unknown_policy_key_is_rejected():
    with pytest.raises(UnknownPolicyKeyError):
        PolicyProfile(policy_version="inst-1", values={"not_a_real_key": 1})


def test_policy_values_are_immutable():
    policy = PolicyProfile(
        policy_version="inst-1", values={"ubo_identification_threshold": 25}
    )
    with pytest.raises(TypeError):
        policy.values["ubo_identification_threshold"] = 10


def test_availability_metadata_carries_no_configured_values():
    """Values must not enter the bundle — only whether a key is configured."""
    policy = PolicyProfile(
        policy_version="inst-1", values={"ubo_identification_threshold": 25}
    )
    metadata = policy.availability_metadata()
    assert "25" not in canonical_json(metadata)
    assert metadata["keys"]["ubo_identification_threshold"] == "available"
    assert metadata["configured_key_count"] == 1
    assert metadata["declared_key_count"] == len(POLICY_KEYS)


# ── Review runner contract ───────────────────────────────────────────


def _bundle():
    return {
        "meta": {
            "subject_type": "application",
            "subject_id": "app-1",
            "as_of": "2026-07-31T00:00:00Z",
        }
    }


def _draft(**overrides):
    draft = {
        "probe_id": "T-01",
        "probe_version": "v1",
        "category": "risk",
        "severity": "high",
        "status": "hit",
        "availability_status": "available",
        "confidence": 0.9,
        "claim": "c",
        "evidence_refs": ["risk:app-1#factor:x"],
        "source_modules": ["tests"],
        "why_it_matters": "w",
        "regulatory_or_policy_basis": "internal_policy_only",
        "officer_question": "q",
        "required_action": "a",
        "close_condition": "cc",
    }
    draft.update(overrides)
    return draft


def test_phase_0b1_default_runs_no_probes():
    review = run_review(_bundle())
    assert review["finding_count"] == 0
    assert review["probe_set_version"] == "probe-set-0b1-empty"
    assert review["review_completeness"]["probes_run"] == 0


def test_runner_rejects_a_draft_missing_required_fields():
    incomplete = _draft()
    del incomplete["close_condition"]
    with pytest.raises(ProbeContractError, match="close_condition"):
        run_review(_bundle(), probes=(lambda b, p: [incomplete],))


def test_runner_rejects_clear_status_on_an_unavailable_check():
    """The invariant: an unevaluable check is never a passing check."""
    draft = _draft(status="clear", availability_status="policy_not_configured")
    with pytest.raises(ProbeContractError, match="never 'clear'"):
        run_review(_bundle(), probes=(lambda b, p: [draft],))


def test_runner_accepts_clear_when_genuinely_available():
    review = run_review(
        _bundle(),
        probes=(lambda b, p: [_draft(status="clear", availability_status="available")],),
    )
    assert review["review_completeness"]["status_counts"]["clear"] == 1


def test_runner_rejects_unknown_status_and_availability():
    with pytest.raises(ProbeContractError, match="unknown status"):
        run_review(_bundle(), probes=(lambda b, p: [_draft(status="maybe")],))
    with pytest.raises(ProbeContractError, match="unknown availability_status"):
        run_review(_bundle(), probes=(lambda b, p: [_draft(availability_status="x")],))


def test_unevaluable_findings_are_counted_separately_from_clear():
    def probe(bundle, policy):
        return [
            _draft(probe_id="A", status="unavailable",
                   availability_status="credentials_absent",
                   evidence_refs=["registry_lookup:1"]),
            _draft(probe_id="B", status="not_replayable",
                   availability_status="snapshot_incomplete",
                   evidence_refs=["decision:1"]),
            _draft(probe_id="C", status="clear", availability_status="available",
                   evidence_refs=["risk:2"]),
        ]

    review = run_review(_bundle(), probes=(probe,))
    completeness = review["review_completeness"]
    assert completeness["unevaluable_count"] == 2
    assert completeness["status_counts"]["clear"] == 1


def test_runner_does_not_mutate_the_bundle():
    bundle = _bundle()
    snapshot = canonical_bytes(bundle)
    run_review(bundle, probes=(lambda b, p: [_draft()],))
    assert canonical_bytes(bundle) == snapshot


def test_runner_takes_no_database_handle():
    """The signature must offer no way to hand the runner a connection."""
    import inspect

    parameters = inspect.signature(run_review).parameters
    assert "db" not in parameters
    assert set(parameters) == {"bundle", "policy", "probes", "probe_set_version"}


def test_findings_carry_subject_and_policy_provenance():
    review = run_review(
        _bundle(),
        policy=PolicyProfile(policy_version="inst-9"),
        probes=(lambda b, p: [_draft()],),
    )
    finding = review["findings"][0]
    assert finding["subject_type"] == "application"
    assert finding["subject_id"] == "app-1"
    assert finding["policy_version"] == "inst-9"
    assert finding["finding_id"]


def test_every_finding_status_and_availability_value_is_reachable():
    """The closed vocabularies are complete and spelled as the framework says."""
    assert {member.value for member in FindingStatus} == {
        "hit", "clear", "unavailable", "not_applicable", "not_replayable",
    }
    assert {member.value for member in AvailabilityStatus} == {
        "available", "dependency_gated", "credentials_absent", "data_absent",
        "snapshot_incomplete", "policy_not_configured",
    }
