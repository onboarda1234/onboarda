"""Bundle v2 contract: the four authorised additions.

``supervisor-bundle-v1`` → ``supervisor-bundle-v2`` adds exactly four read-only
projections required by the approved 0B-2 probes:

1. ``screening.subjects[]``
2. ``risk.dimension_scores``
3. ``edd.routing_facts``
4. ``monitoring.periodic_reviews[].policy_version``

**No probes are implemented in this PR.** These tests cover the contract only.

The dedicated privacy review of ``screening.subjects[]`` lives here, because
that structure is the one v2 addition carrying per-person evidence.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import supervisor_foundation.bundle as bundle_module
from supervisor_foundation import assemble_application_bundle, canonical_json
from supervisor_foundation.bundle import (
    EDD_ROUTING_FACT_KEYS,
    EDD_SCREENING_SUMMARY_KEYS,
    PERIODIC_REVIEW_FIELDS,
    RISK_DIMENSION_SCORE_KEYS,
    SCHEMA_V2_ADDITIONS,
    SCREENING_SUBJECT_FIELDS,
)
from supervisor_foundation_fixtures import (
    AS_OF,
    complete_standard,
    missing_optional_data,
)

BUNDLE_SOURCE = pathlib.Path(bundle_module.__file__)


def _bundle(db, application_id):
    return assemble_application_bundle(db, application_id, as_of=AS_OF)


# ── Version ──────────────────────────────────────────────────────────


def test_schema_version_is_v2():
    from supervisor_foundation import BUNDLE_SCHEMA_VERSION

    assert BUNDLE_SCHEMA_VERSION == "supervisor-bundle-v2"


def test_probe_set_version_still_represents_an_empty_set():
    """v2 is a bundle change. No probes ship in this PR."""
    from supervisor_foundation import PROBE_SET_VERSION

    assert PROBE_SET_VERSION == "probe-set-0b2-empty"
    assert "empty" in PROBE_SET_VERSION


def test_no_probe_module_exists_yet():
    """Guard against probe code arriving in the bundle-contract PR."""
    package_root = BUNDLE_SOURCE.parent
    assert not (package_root / "probes").exists()
    modules = {path.name for path in package_root.glob("*.py")}
    for forbidden in (
        "risk_resolution.py",
        "edd_divergence.py",
        "screening_defensibility.py",
        "monitoring_requirement.py",
    ):
        assert forbidden not in modules


def test_v2_additions_are_declared_in_source():
    assert set(SCHEMA_V2_ADDITIONS) == {
        "screening.subjects[]",
        "risk.dimension_scores",
        "edd.routing_facts",
        "monitoring.periodic_reviews[].policy_version",
    }


# ── 1. screening.subjects[] ──────────────────────────────────────────


def test_subjects_are_assembled_for_every_screened_party(db):
    bundle = _bundle(db, complete_standard(db))
    subjects = bundle["screening"]["subjects"]

    assert bundle["screening"]["subject_count"] == len(subjects) == 4
    assert [item["subject_key"] for item in subjects] == [
        "company_screening",
        "director_screening_0",
        "director_screening_1",
        "ubo_screening_0",
    ]
    assert {item["subject_type"] for item in subjects} == {"company", "director", "ubo"}


def test_subject_projection_is_limited_to_the_whitelist(db):
    bundle = _bundle(db, complete_standard(db))
    for subject in bundle["screening"]["subjects"]:
        assert set(subject) == set(SCREENING_SUBJECT_FIELDS)


def test_subjects_carry_provider_mode_terminality_and_match_state(db):
    """The three states the aggregate summary could not answer."""
    bundle = _bundle(db, complete_standard(db))
    by_key = {item["subject_key"]: item for item in bundle["screening"]["subjects"]}

    # subject_key is positional within the stored report, not name-ordered: the
    # seeded directors are [matched, clean], so index 0 is the matched one.
    matched = by_key["director_screening_0"]
    assert matched["provider_mode"] == "live_provider"
    assert matched["matched"] is True
    assert matched["has_material_hit"] is True
    assert matched["result_count"] == 1
    assert matched["screening_state"]

    clean = by_key["ubo_screening_0"]
    assert clean["matched"] is False
    assert clean["has_material_hit"] is False
    assert clean["result_count"] == 0


def test_subjects_carry_raw_timestamps_not_a_derived_staleness_verdict(db):
    """Freshness is the probe's job, using ``as_of`` — not the assembler's."""
    bundle = _bundle(db, complete_standard(db))
    subject = bundle["screening"]["subjects"][0]

    assert subject["screened_at"] == "2026-07-01T09:00:00"
    assert subject["screening_valid_until"] == "2026-10-29T00:00:00"
    for derived in ("stale", "is_stale", "fresh", "freshness", "expired"):
        assert derived not in subject


def test_subjects_are_empty_without_a_report(db):
    bundle = _bundle(db, missing_optional_data(db))
    assert bundle["screening"]["subjects"] == []
    assert bundle["screening"]["subject_count"] == 0
    assert bundle["screening"]["report_present"] is False


def test_subjects_link_to_their_disposition_review(db):
    bundle = _bundle(db, complete_standard(db))
    by_key = {item["subject_key"]: item for item in bundle["screening"]["subjects"]}
    # The seeded review is for director "Jane Doe".
    linked = [
        item for item in by_key.values() if item["review_id"] is not None
    ]
    assert len(linked) == 1
    assert linked[0]["subject_type"] == "director"


def test_subjects_sort_by_subject_key_regardless_of_report_order(db):
    """Directors are seeded in reverse name order; output must be canonical."""
    bundle = _bundle(db, complete_standard(db))
    keys = [item["subject_key"] for item in bundle["screening"]["subjects"]]
    assert keys == sorted(keys)


# ── Privacy review of screening.subjects[] ───────────────────────────

#: Seeded into the screening report by ``complete_standard``. None may appear.
SEEDED_SUBJECT_IDENTIFIERS = ("Jane Doe", "Zara Late", "John Roe", "Fixture Holdings Ltd")

#: Keys that would carry a direct identifier or raw provider payload.
FORBIDDEN_SUBJECT_KEYS = (
    "name",
    "person_name",
    "subject_name",
    "company_name",
    "full_name",
    "date_of_birth",
    "dob",
    "address",
    "results",
    "raw",
    "raw_response",
    "payload",
    "match_content",
    "matches",
)


def test_no_direct_identifier_value_enters_screening_subjects(db):
    encoded = canonical_json(_bundle(db, complete_standard(db))["screening"])
    leaked = [value for value in SEEDED_SUBJECT_IDENTIFIERS if value in encoded]
    assert not leaked, f"direct identifier leaked into screening subjects: {leaked}"


def test_no_direct_identifier_key_enters_screening_subjects(db):
    bundle = _bundle(db, complete_standard(db))
    for subject in bundle["screening"]["subjects"]:
        present = sorted(set(subject) & set(FORBIDDEN_SUBJECT_KEYS))
        assert not present, f"forbidden key(s) in screening subject: {present}"


def test_raw_match_payload_is_replaced_by_a_count_and_a_conclusion(db):
    """``results`` carries match content, so only its conclusions are kept."""
    bundle = _bundle(db, complete_standard(db))
    encoded = canonical_json(bundle["screening"])
    assert "match_id" not in encoded, "raw provider match content entered the bundle"

    matched = [s for s in bundle["screening"]["subjects"] if s["matched"]][0]
    assert matched["result_count"] == 1
    assert matched["has_material_hit"] is True


def test_subject_identity_is_a_pseudonymous_fingerprint(db):
    bundle = _bundle(db, complete_standard(db))
    for subject in bundle["screening"]["subjects"]:
        fingerprint = subject["subject_name_fingerprint"]
        assert fingerprint is None or len(fingerprint) == 16
    # Stable references sufficient for later evidence lookup.
    assert all(subject["subject_key"] for subject in bundle["screening"]["subjects"])


def test_the_two_subject_references_have_different_stability_scopes(db):
    """``subject_key`` is report-positional; the fingerprint is person-stable.

    A re-screen that returns subjects in a different order would reassign
    ``subject_key`` while the fingerprint follows the person. A probe wanting to
    track one individual across screenings must key on the fingerprint; one
    citing evidence within a single report should cite ``subject_key``. Pinned
    here because getting it backwards would silently mislink evidence.
    """
    bundle = _bundle(db, complete_standard(db))
    subjects = bundle["screening"]["subjects"]

    directors = [s for s in subjects if s["subject_type"] == "director"]
    assert [s["subject_key"] for s in directors] == [
        "director_screening_0",
        "director_screening_1",
    ]
    # Distinct people carry distinct fingerprints.
    assert len({s["subject_name_fingerprint"] for s in directors}) == 2


def test_subject_helpers_used_by_the_assembler_are_clock_free():
    """Re-assert the audit that admitted ``screening_state`` to the import set."""
    import screening_state

    source = pathlib.Path(screening_state.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    clocked = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "_timestamp_is_past"
                ):
                    clocked.add(node.name)

    assert clocked == {"derive_screening_truth", "build_screening_truth_summary"}, (
        f"screening_state clock reach changed: {sorted(clocked)}"
    )
    for used in (
        "provider_mode_from_record",
        "derive_screening_state",
        "_entry_has_material_hit",
        "_entry_has_granular_material_fields",
    ):
        assert used not in clocked, f"{used} now reaches a clock; re-audit the import"


def test_assembler_does_not_call_the_clocked_screening_wrappers():
    source = BUNDLE_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "derive_screening_truth" not in called
    assert "build_screening_truth_summary" not in called


# ── 2. risk.dimension_scores ─────────────────────────────────────────


def test_dimension_scores_project_the_stored_values(db):
    bundle = _bundle(db, complete_standard(db))
    scores = bundle["risk"]["dimension_scores"]
    assert scores == {"d1": 2.0, "d2": 1.5}
    assert bundle["risk"]["dimension_scores_absent"] == ["d3", "d4", "d5"]


def test_dimension_scores_preserve_absent_distinctly(db):
    """A dimension the blob does not carry is absent, not zero."""
    bundle = _bundle(db, complete_standard(db))
    assert "d3" not in bundle["risk"]["dimension_scores"]
    assert "d3" in bundle["risk"]["dimension_scores_absent"]
    assert bundle["risk"]["dimension_scores_absent"] == sorted(
        bundle["risk"]["dimension_scores_absent"]
    )


def test_dimension_scores_add_no_derived_tier(db):
    """Stored scores only. Tiers are governed outputs, read from routing_facts."""
    bundle = _bundle(db, complete_standard(db))
    assert set(bundle["risk"]["dimension_scores"]) <= set(RISK_DIMENSION_SCORE_KEYS)
    for derived in ("sector_risk_tier", "jurisdiction_risk_tier", "tier"):
        assert derived not in bundle["risk"]


def test_risk_is_still_not_recomputed(db):
    assert _bundle(db, complete_standard(db))["risk"]["recomputed"] is False


# ── 3. edd.routing_facts ─────────────────────────────────────────────


def test_routing_facts_project_the_stored_contract(db):
    bundle = _bundle(db, complete_standard(db))
    routing = bundle["edd"]["routing_facts"]

    assert routing["present"] is True
    assert routing["availability_status"] == "available"
    assert routing["absent_fact_keys"] == []
    assert set(routing["facts"]) == set(EDD_ROUTING_FACT_KEYS)
    assert routing["recorded_at_memo_generation"] is True


def test_routing_facts_exclude_contract_keys_the_policy_does_not_read(db):
    routing = _bundle(db, complete_standard(db))["edd"]["routing_facts"]
    for excluded in ("decision_recommendation", "monitoring_tier", "sector_label"):
        assert excluded not in routing["facts"]
    encoded = canonical_json(routing)
    assert "Technology" not in encoded


def test_routing_facts_screening_summary_is_restricted(db):
    routing = _bundle(db, complete_standard(db))["edd"]["routing_facts"]
    summary = routing["facts"]["screening_terminality_summary"]
    assert set(summary) <= set(EDD_SCREENING_SUMMARY_KEYS)
    assert "blocking_reasons" not in summary
    assert summary["has_terminal_match"] is False
    assert summary["terminal"] is True


def test_routing_facts_trigger_flags_are_canonically_ordered(db):
    routing = _bundle(db, complete_standard(db))["edd"]["routing_facts"]
    flags = routing["facts"]["edd_trigger_flags"]
    assert flags == sorted(flags)
    assert flags == ["declared_pep_present", "risk_escalation:elevated_jurisdiction"]


def test_routing_facts_absent_when_no_contract_is_stored(db):
    routing = _bundle(db, missing_optional_data(db))["edd"]["routing_facts"]
    assert routing["present"] is False
    assert routing["availability_status"] == "data_absent"
    assert routing["absent_fact_keys"] == sorted(EDD_ROUTING_FACT_KEYS)
    assert routing["facts"] == {}


def test_missing_fact_is_reported_absent_not_defaulted_to_false(db):
    """``evaluate_edd_routing`` treats missing as incomplete_contract, not False."""
    import json

    application_id = complete_standard(db)
    row = db.execute(
        "SELECT id, memo_data FROM compliance_memos WHERE application_id = ?",
        (application_id,),
    ).fetchone()
    memo = json.loads(row["memo_data"])
    del memo["metadata"]["agent5_input_contract"]["declared_pep_present"]
    db.execute(
        "UPDATE compliance_memos SET memo_data = ? WHERE id = ?",
        (json.dumps(memo), row["id"]),
    )
    db.commit()

    routing = _bundle(db, application_id)["edd"]["routing_facts"]
    assert routing["absent_fact_keys"] == ["declared_pep_present"]
    assert "declared_pep_present" not in routing["facts"]
    assert routing["availability_status"] == "snapshot_incomplete"


def test_edd_policy_is_still_not_reevaluated(db):
    assert _bundle(db, complete_standard(db))["edd"]["policy_reevaluated"] is False


# ── EDD fact reconstruction proof ────────────────────────────────────


def test_every_required_fact_key_is_reconstructable_from_v2(db):
    """The gate the founder set before authorising implementation.

    Each of ``evaluate_edd_routing.REQUIRED_FACT_KEYS`` must be obtainable from
    bundle v2 plus versioned governed logic — no wall clock, no database, no
    provider call.
    """
    from edd_routing_policy import REQUIRED_FACT_KEYS, evaluate_edd_routing

    bundle = _bundle(db, complete_standard(db))
    facts = dict(bundle["edd"]["routing_facts"]["facts"])

    # Seven come straight from the stored contract; the eighth from the
    # supervisor verdict section, which is where it is recorded.
    facts["supervisor_mandatory_escalation"] = bool(
        (bundle["supervisor_verdict"].get("verdict") or {}).get("mandatory_escalation")
    )

    missing = sorted(set(REQUIRED_FACT_KEYS) - set(facts))
    assert not missing, f"still unreconstructable from bundle v2: {missing}"

    # And the governed policy accepts the reconstruction without reporting an
    # incomplete contract — the real proof that the facts are usable.
    routing = evaluate_edd_routing(facts)
    assert "incomplete_contract" not in (routing.get("triggers") or [])
    assert routing["route"] in ("edd", "standard")


def test_reconstruction_matches_the_route_the_memo_recorded(db):
    """Re-running the policy on the stored facts reproduces the stored route.

    This is the ``assert_routing_invariant`` property. It is asserted here as a
    contract check on the bundle, not as a probe — detecting and reporting
    divergence is P-03, which is not implemented in this PR.
    """
    from edd_routing_policy import evaluate_edd_routing

    bundle = _bundle(db, complete_standard(db))
    facts = dict(bundle["edd"]["routing_facts"]["facts"])
    facts["supervisor_mandatory_escalation"] = bool(
        (bundle["supervisor_verdict"].get("verdict") or {}).get("mandatory_escalation")
    )

    recomputed = evaluate_edd_routing(facts)
    assert recomputed["route"] == bundle["edd"]["stored_routing"]["route"]
    assert recomputed["policy_version"] == bundle["edd"]["stored_routing"]["policy_version"]


# ── 4. periodic review policy_version ────────────────────────────────


def test_periodic_review_policy_version_is_whitelisted():
    assert "policy_version" in PERIODIC_REVIEW_FIELDS


def test_periodic_review_carries_policy_version_or_reports_it_absent(db):
    bundle = _bundle(db, complete_standard(db))
    monitoring = bundle["monitoring"]
    review = monitoring["periodic_reviews"][0]

    # The column exists in this schema, so it is present-and-null rather than
    # absent — the distinction v2 must preserve.
    assert "policy_version" in review
    assert review["policy_version"] is None
    assert "policy_version" not in monitoring["absent_fields"]


# ── Determinism of the v2 additions ──────────────────────────────────


def test_v2_sections_are_stable_across_repeated_assembly(db):
    application_id = complete_standard(db)
    encodings = {
        canonical_json(
            {
                "screening": _bundle(db, application_id)["screening"],
                "risk": _bundle(db, application_id)["risk"],
                "edd": _bundle(db, application_id)["edd"],
                "monitoring": _bundle(db, application_id)["monitoring"],
            }
        )
        for _ in range(3)
    }
    assert len(encodings) == 1


def test_multi_subject_ordering_is_stable_under_reinsertion(db):
    """Rebuilding each subject mapping in reverse key order is a no-op."""
    from supervisor_foundation import compute_input_bundle_hash

    bundle = _bundle(db, complete_standard(db))

    def reinsert(value):
        if isinstance(value, dict):
            return {key: reinsert(value[key]) for key in reversed(list(value))}
        if isinstance(value, list):
            return [reinsert(item) for item in value]
        return value

    assert compute_input_bundle_hash(reinsert(bundle)) == compute_input_bundle_hash(
        bundle
    )


@pytest.mark.parametrize("section", ["screening", "risk", "edd", "monitoring"])
def test_changed_v2_evidence_moves_the_bundle_hash(db, section):
    """Each touched section is genuinely inside the hash."""
    from supervisor_foundation import compute_input_bundle_hash

    application_id = complete_standard(db)
    before = compute_input_bundle_hash(_bundle(db, application_id))

    mutations = {
        "screening": (
            "UPDATE screening_reviews SET disposition_code = ? WHERE application_id = ?",
            ("true_match", application_id),
        ),
        "risk": (
            "UPDATE applications SET risk_config_version = ? WHERE id = ?",
            ("stale:recompute_failed", application_id),
        ),
        "edd": (
            "UPDATE edd_cases SET stage = ? WHERE application_id = ?",
            ("edd_approved", application_id),
        ),
        "monitoring": (
            "UPDATE periodic_reviews SET next_review_date = ? WHERE application_id = ?",
            ("2028-01-01", application_id),
        ),
    }
    sql, params = mutations[section]
    db.execute(sql, params)
    db.commit()

    assert compute_input_bundle_hash(_bundle(db, application_id)) != before
