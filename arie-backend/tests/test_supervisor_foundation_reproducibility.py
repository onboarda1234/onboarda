"""Phase 0B-1 reproducibility harness — the gate on the whole phase.

The invariant under test:

    identical canonical input bundle + identical policy version
    = identical finding set + identical review hash

If any test in this file cannot be made to pass, the Supervisor is not
shippable as specified and Phase 0B-2 must not begin.
"""

from __future__ import annotations

import json

import pytest

from supervisor_foundation import (
    assemble_application_bundle,
    canonical_bytes,
    compute_input_bundle_hash,
    run_review,
)
from supervisor_foundation.policy import unconfigured_policy
from supervisor_foundation_fixtures import AS_OF, SCENARIOS, SYNTHETIC_PROBES

RUNS = 3


@pytest.fixture(params=sorted(SCENARIOS))
def scenario(request, db):
    """Seed one fixture scenario and yield ``(name, application_id)``."""
    name = request.param
    return name, SCENARIOS[name](db)


def _assemble(db, application_id, **kwargs):
    return assemble_application_bundle(db, application_id, as_of=AS_OF, **kwargs)


# ── Core invariant ───────────────────────────────────────────────────


def test_bundle_hash_stable_across_repeated_assembly(scenario, db):
    """Re-assembling from unchanged rows produces the identical bundle."""
    _, application_id = scenario
    bundles = [_assemble(db, application_id) for _ in range(RUNS)]
    encodings = {canonical_bytes(bundle) for bundle in bundles}
    assert len(encodings) == 1, "assembly is not deterministic across runs"

    hashes = {compute_input_bundle_hash(bundle) for bundle in bundles}
    assert len(hashes) == 1


def test_review_stable_across_three_runs(scenario, db):
    """Three reviews of one bundle agree on hash, findings and encoding."""
    _, application_id = scenario
    bundle = _assemble(db, application_id)

    reviews = [
        run_review(bundle, policy=unconfigured_policy(), probes=SYNTHETIC_PROBES)
        for _ in range(RUNS)
    ]

    assert len({review["input_bundle_hash"] for review in reviews}) == 1
    assert len({review["review_hash"] for review in reviews}) == 1
    assert len({canonical_bytes(review) for review in reviews}) == 1

    finding_id_sets = [
        tuple(finding["finding_id"] for finding in review["findings"])
        for review in reviews
    ]
    assert len(set(finding_id_sets)) == 1, "finding identifiers are not stable"


def test_review_byte_identical_canonical_json(scenario, db):
    """Canonical JSON of the review is byte-identical run to run."""
    _, application_id = scenario
    bundle = _assemble(db, application_id)
    encodings = [
        canonical_bytes(run_review(bundle, probes=SYNTHETIC_PROBES))
        for _ in range(RUNS)
    ]
    assert encodings[0] == encodings[1] == encodings[2]


# ── Ordering independence ────────────────────────────────────────────


def test_shuffled_dict_insertion_order_does_not_change_hash(scenario, db):
    """Rebuilding every mapping in a different insertion order is a no-op."""
    _, application_id = scenario
    bundle = _assemble(db, application_id)

    def reinsert(value):
        if isinstance(value, dict):
            return {key: reinsert(value[key]) for key in reversed(list(value))}
        if isinstance(value, list):
            return [reinsert(item) for item in value]
        return value

    assert compute_input_bundle_hash(reinsert(bundle)) == compute_input_bundle_hash(
        bundle
    )


def test_shuffled_source_row_order_does_not_change_hash(db):
    """Rows inserted in reverse order still sort into the canonical order."""
    from supervisor_foundation_fixtures import unordered_rows

    application_id = unordered_rows(db)
    bundle = _assemble(db, application_id)

    ubo_keys = [row["person_key"] for row in bundle["parties"]["ubos"]["rows"]]
    assert ubo_keys == sorted(ubo_keys), "party ordering is not canonical"

    document_types = [row["doc_type"] for row in bundle["documents"]["active"]]
    assert document_types == sorted(document_types), "document ordering is not canonical"

    review_keys = [
        (row["subject_type"], row["subject_name_fingerprint"])
        for row in bundle["screening"]["reviews"]
    ]
    assert review_keys == sorted(review_keys), "screening review ordering is not canonical"


def test_probe_emission_order_does_not_change_review_hash(db):
    """A probe returning findings in a different order yields the same hash."""
    from supervisor_foundation_fixtures import complete_standard, synthetic_hit_probe

    application_id = complete_standard(db)
    bundle = _assemble(db, application_id)

    def reversed_probe(bundle_arg, policy_arg):
        return list(reversed(list(synthetic_hit_probe(bundle_arg, policy_arg))))

    forward = run_review(bundle, probes=(synthetic_hit_probe,))
    backward = run_review(bundle, probes=(reversed_probe,))
    assert forward["review_hash"] == backward["review_hash"]


# ── Hash sensitivity: the invariant must not be vacuous ──────────────


def test_changed_evidence_changes_bundle_hash(db):
    """A real evidence change must move the hash, or the hash proves nothing."""
    from supervisor_foundation_fixtures import complete_standard

    application_id = complete_standard(db)
    before = compute_input_bundle_hash(_assemble(db, application_id))

    db.execute(
        "UPDATE applications SET final_risk_level = ? WHERE id = ?",
        ("VERY_HIGH", application_id),
    )
    db.commit()

    assert compute_input_bundle_hash(_assemble(db, application_id)) != before


def test_changed_policy_version_changes_review_hash_only(db):
    """Policy version moves review_hash but leaves input_bundle_hash alone.

    This separation is what makes policy replay a hash comparison in a later
    phase: same evidence, different policy.
    """
    from supervisor_foundation.policy import PolicyProfile
    from supervisor_foundation_fixtures import complete_standard

    application_id = complete_standard(db)
    unconfigured = unconfigured_policy()
    configured = PolicyProfile(
        policy_version="inst-2026-01",
        policy_jurisdiction="MU",
        values={"ubo_identification_threshold": 25},
    )

    bundle_a = _assemble(db, application_id, policy=unconfigured)
    review_a = run_review(bundle_a, policy=unconfigured)

    # Same evidence, reviewed under a different policy version.
    review_b = run_review(bundle_a, policy=configured)

    assert review_a["input_bundle_hash"] == review_b["input_bundle_hash"]
    assert review_a["review_hash"] != review_b["review_hash"]


def test_different_as_of_changes_bundle_hash(db):
    """``as_of`` is an input, so changing it changes the bundle identity."""
    from supervisor_foundation_fixtures import complete_standard

    application_id = complete_standard(db)
    first = compute_input_bundle_hash(
        assemble_application_bundle(db, application_id, as_of=AS_OF)
    )
    second = compute_input_bundle_hash(
        assemble_application_bundle(db, application_id, as_of="2026-08-01T00:00:00Z")
    )
    assert first != second


# ── Availability states reach the bundle ─────────────────────────────


def test_default_environment_reports_expected_availability(db):
    """Credentials-absent, data-absent and policy-not-configured all land.

    ``gated_supervisor_package`` is asserted separately: the pilot-scope veto is
    off in the testing environment, so its value here is environment-dependent
    and both branches get their own test below.
    """
    from supervisor_foundation_fixtures import missing_optional_data

    application_id = missing_optional_data(db)
    bundle = _assemble(db, application_id)
    dependencies = bundle["availability"]["dependencies"]

    assert dependencies["public_registry"] == "credentials_absent"
    assert dependencies["screening_report"] == "data_absent"
    assert dependencies["memo"] == "data_absent"
    assert dependencies["historic_decision_snapshot"] == "snapshot_incomplete"

    assert set(bundle["availability"]["policy"].values()) == {"policy_not_configured"}


def test_gated_supervisor_reports_dependency_gated_under_pilot_scope(db, monkeypatch):
    """With the pilot-scope veto on, the enterprise package reads as gated."""
    import supervisor_foundation.bundle as bundle_module

    from supervisor_foundation_fixtures import missing_optional_data

    monkeypatch.setattr(
        bundle_module, "_gated_supervisor_available", lambda: False
    )
    bundle = _assemble(db, missing_optional_data(db))
    assert bundle["availability"]["dependencies"]["gated_supervisor_package"] == (
        "dependency_gated"
    )


def test_snapshot_incomplete_when_risk_evidence_absent(db):
    from supervisor_foundation_fixtures import snapshot_incomplete

    bundle = _assemble(db, snapshot_incomplete(db))
    assert bundle["risk"]["evidence_present"] is False
    assert bundle["availability"]["dependencies"]["risk_factor_evidence"] == (
        "snapshot_incomplete"
    )


def test_registry_available_branch(db, monkeypatch):
    """The available branch is real, not just the absent one."""
    import company_registry

    from supervisor_foundation_fixtures import missing_optional_data

    monkeypatch.setattr(company_registry, "COMPANIES_HOUSE_API_KEY", "test-key")
    bundle = _assemble(db, missing_optional_data(db))
    assert bundle["availability"]["dependencies"]["public_registry"] == "available"


def test_gated_supervisor_available_branch(db, monkeypatch):
    import environment

    from supervisor_foundation_fixtures import missing_optional_data

    monkeypatch.setattr(environment, "pilot_scope_active", lambda *a, **k: False)
    monkeypatch.setattr(environment.flags, "is_enabled", lambda flag: True)
    bundle = _assemble(db, missing_optional_data(db))
    assert bundle["availability"]["dependencies"]["gated_supervisor_package"] == (
        "available"
    )


# ── Null vs absent ───────────────────────────────────────────────────


def test_null_is_preserved_and_distinct_from_absent(db):
    from supervisor_foundation_fixtures import null_values

    bundle = _assemble(db, null_values(db))
    fields = bundle["application"]["fields"]

    # `risk_level` is deliberately not in the application whitelist — risk
    # values live in the risk section, so they are not carried twice.
    assert "risk_level" not in fields
    assert bundle["risk"]["risk_level"] is None

    assert "country" in fields and fields["country"] is None
    assert "sector" in fields and fields["sector"] is None
    assert "entity_type" in fields and fields["entity_type"] is None

    encoded = json.loads(canonical_bytes(bundle).decode("utf-8"))
    assert encoded["application"]["fields"]["country"] is None

    absent = bundle["application"]["absent_fields"]
    assert absent == sorted(absent)
    assert "country" not in absent, "a present-but-null field must not be absent"


# ── Float stability ──────────────────────────────────────────────────


def test_float_values_are_normalised_consistently(db):
    from supervisor_foundation_fixtures import stable_floats

    application_id = stable_floats(db)
    encodings = {canonical_bytes(_assemble(db, application_id)) for _ in range(RUNS)}
    assert len(encodings) == 1

    text = encodings.pop().decode("utf-8")
    assert "0.30000000000000004" not in text, "float was not rounded"
    assert "-0.0" not in text, "negative zero was not normalised"
