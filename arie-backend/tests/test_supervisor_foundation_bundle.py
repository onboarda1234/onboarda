"""Bundle contract tests: sections, whitelists, and direct-identifier exclusion.

The bundle is hashed, will later be logged and exported, and is the input to
every probe. These tests pin what it must contain and — more importantly — what
it must never contain.

Scope note: these tests assert that **direct identifiers** are excluded. They do
not assert the bundle is anonymous, and it is not — stable internal IDs and name
fingerprints remain pseudonymous linkable identifiers, so the bundle is personal
data. See the module docstring of ``supervisor_foundation.bundle``.
"""

from __future__ import annotations

import json

import pytest

from supervisor_foundation import assemble_application_bundle, canonical_json
from supervisor_foundation.bundle import (
    APPLICATION_FIELDS,
    DOCUMENT_FIELDS,
    ORDERING_RULES,
    BundleAssemblyError,
    _project,
)
from supervisor_foundation.contracts import BUNDLE_SECTIONS, SubjectType
from supervisor_foundation_fixtures import AS_OF, complete_standard, missing_optional_data


def _bundle(db, application_id):
    return assemble_application_bundle(db, application_id, as_of=AS_OF)


# ── Shape ────────────────────────────────────────────────────────────


def test_all_declared_sections_are_present(db):
    bundle = _bundle(db, complete_standard(db))
    assert sorted(bundle) == sorted(BUNDLE_SECTIONS)


def test_sections_are_present_even_when_empty(db):
    """An empty section is an explicit structure, never an omitted key."""
    bundle = _bundle(db, missing_optional_data(db))
    assert sorted(bundle) == sorted(BUNDLE_SECTIONS)
    assert bundle["documents"]["active"] == []
    assert bundle["decision"]["records"] == []
    assert bundle["memo"]["present"] is False


def test_meta_carries_schema_version_subject_and_as_of(db):
    bundle = _bundle(db, complete_standard(db))
    meta = bundle["meta"]
    assert meta["bundle_schema_version"] == "supervisor-bundle-v2"
    assert meta["subject_type"] == SubjectType.APPLICATION.value
    assert meta["subject_id"]
    assert meta["as_of"] == AS_OF


def test_bundle_does_not_contain_its_own_hash(db):
    """A hash cannot contain itself; it lives on the review."""
    bundle = _bundle(db, complete_standard(db))
    assert "input_bundle_hash" not in bundle["meta"]
    assert "input_bundle_hash" not in bundle


def test_unknown_application_raises(db):
    with pytest.raises(BundleAssemblyError, match="application not found"):
        assemble_application_bundle(db, "does-not-exist", as_of=AS_OF)


def test_missing_application_id_raises(db):
    with pytest.raises(BundleAssemblyError, match="application_id is required"):
        assemble_application_bundle(db, "", as_of=AS_OF)


# ── Direct-identifier exclusion ──────────────────────────────────────

#: Direct identifiers and free text seeded by ``complete_standard`` that must
#: never reach the bundle.
SEEDED_DIRECT_IDENTIFIERS = (
    "Jane Doe",
    "John Roe",
    "Holdco SA",
    "/tmp/x.pdf",
    "Senior override recorded for this case",
    "generated prose",
)


def test_no_direct_identifier_reaches_the_bundle(db):
    """Names, file paths and officer prose are addressed, never copied."""
    encoded = canonical_json(_bundle(db, complete_standard(db)))
    leaked = [value for value in SEEDED_DIRECT_IDENTIFIERS if value in encoded]
    assert not leaked, f"direct identifier or free text leaked into the bundle: {leaked}"


def test_party_names_are_replaced_by_pseudonymous_ids(db):
    """Direct identifiers out; stable linkable IDs deliberately retained."""
    bundle = _bundle(db, complete_standard(db))
    for row in bundle["parties"]["ubos"]["rows"]:
        assert "full_name" not in row
        assert "first_name" not in row
        assert "date_of_birth" not in row
        assert "residential_address" not in row
        # Retained on purpose: probes need to address the party. This is a
        # pseudonymous identifier, so the bundle remains personal data.
        assert row["id"]


def test_bundle_is_documented_as_personal_data_not_anonymous(db):
    """The privacy posture is stated in the module, not left to inference."""
    import supervisor_foundation.bundle as bundle_module

    doc = bundle_module.__doc__ or ""
    assert "PII-minimised" in doc
    assert "pseudonymous" in doc.lower()
    assert "personal data" in doc.lower()
    # The docstring carries an explicit prohibition on the overclaim, so a naive
    # substring check would match the prohibition itself. Pin the prohibition.
    assert "should be described as PII-free" in doc
    assert "is PII-free" not in doc


def test_pep_declaration_is_reduced_to_a_boolean(db):
    bundle = _bundle(db, complete_standard(db))
    director = bundle["parties"]["directors"]["rows"][0]
    assert director["has_pep_declaration"] is True
    assert "pep_declaration" not in director


def test_document_checks_carry_outcomes_not_extracted_values(db):
    """Check results are evidence; extracted field values are PII."""
    bundle = _bundle(db, complete_standard(db))
    document = bundle["documents"]["active"][0]
    assert document["checks"] == [
        {"check_id": "DOC-01", "classification": "rule", "result": "pass"},
        {"check_id": "DOC-02", "classification": "rule", "result": "pass"},
    ]
    encoded = canonical_json(document)
    assert "extracted_value" not in encoded
    assert "Fixture Holdings Ltd" not in encoded


def test_screening_review_subject_is_fingerprinted(db):
    """Ordering by a personal name without carrying the name.

    The fingerprint is a pseudonym, not an anonymisation — it is stable and
    reversible against a candidate name list, which the docstring records.
    """
    bundle = _bundle(db, complete_standard(db))
    review = bundle["screening"]["reviews"][0]
    assert "subject_name" not in review
    assert len(review["subject_name_fingerprint"]) == 16

    from supervisor_foundation.bundle import _fingerprint

    doc = (_fingerprint.__doc__ or "").lower()
    assert "pseudonym" in doc
    assert "not an anonymisation" in doc


def test_override_reason_is_reduced_to_presence(db):
    bundle = _bundle(db, complete_standard(db))
    record = bundle["decision"]["records"][0]
    assert record["override_flag"] is True
    assert record["override_reason_present"] is True
    assert "override_reason" not in record


def test_memo_prose_is_excluded_but_section_keys_are_kept(db):
    bundle = _bundle(db, complete_standard(db))
    memo = bundle["memo"]
    assert memo["section_keys"] == [
        "compliance_decision",
        "executive_summary",
        "risk_assessment",
    ]
    assert "sections" not in memo
    assert "key_findings" not in canonical_json(memo)


# ── Whitelists ───────────────────────────────────────────────────────


def test_application_projection_is_limited_to_the_whitelist(db):
    bundle = _bundle(db, complete_standard(db))
    fields = set(bundle["application"]["fields"])
    assert fields <= set(APPLICATION_FIELDS)


def test_prescreening_blob_is_never_carried_wholesale(db):
    """``prescreening_data`` holds the screening report and applicant PII."""
    bundle = _bundle(db, complete_standard(db))
    assert "prescreening_data" not in bundle["application"]["fields"]
    assert "prescreening_data" not in canonical_json(bundle)


def test_document_projection_excludes_storage_locations(db):
    bundle = _bundle(db, complete_standard(db))
    document = bundle["documents"]["active"][0]
    assert set(document) <= set(DOCUMENT_FIELDS) | {"checks"}
    assert "file_path" not in document
    assert "s3_key" not in document


def test_project_distinguishes_null_from_absent():
    values, absent = _project({"a": None, "b": 1}, ("a", "b", "c"))
    assert values == {"a": None, "b": 1}
    assert absent == ["c"]


def test_project_reports_absent_fields_sorted():
    _, absent = _project({}, ("z", "a", "m"))
    assert absent == ["a", "m", "z"]


# ── Deliberate exclusions are declared, not silent ───────────────────


def test_document_gate_verdict_is_excluded_with_a_stated_reason(db):
    gate = _bundle(db, complete_standard(db))["document_gate"]
    assert gate["verdict_included"] is False
    assert "wall clock" in gate["verdict_exclusion_reason"]
    assert gate["expectations"], "gate expectations should still be assembled"
    assert gate["gate_policy_version"] == "document_reliance_gate_v2"


def test_document_gate_expectations_drop_the_name_bearing_label(db):
    """The gate's per-party labels embed full names; only IDs are carried."""
    gate = _bundle(db, complete_standard(db))["document_gate"]
    for expectation in gate["expectations"]:
        assert set(expectation) == {"doc_type", "person_id", "person_type"}
    assert "Jane Doe" not in canonical_json(gate)


def test_screening_truth_summary_is_excluded_with_a_stated_reason(db):
    screening = _bundle(db, complete_standard(db))["screening"]
    assert screening["truth_summary_included"] is False
    assert "wall clock" in screening["truth_summary_exclusion_reason"]
    assert screening["report_present"] is True


def test_risk_is_read_not_recomputed(db):
    risk = _bundle(db, complete_standard(db))["risk"]
    assert risk["recomputed"] is False
    assert risk["evidence_present"] is True
    assert risk["evidence_schema_version"] == "risk-factor-evidence-v1"
    assert [factor["factor_key"] for factor in risk["factors"]] == [
        "entity_type",
        "source_of_wealth",
        "country_of_incorporation",
    ]


def test_edd_policy_is_not_reevaluated(db):
    edd = _bundle(db, complete_standard(db))["edd"]
    assert edd["policy_reevaluated"] is False
    assert edd["stored_routing"]["route"] == "edd"
    assert edd["stored_routing"]["triggers"] == [
        "declared_pep_present",
        "high_or_very_high_risk",
    ]
    assert edd["case_count"] == 1


def test_supervisor_verdict_is_carried_without_its_timestamp(db):
    verdict_section = _bundle(db, complete_standard(db))["supervisor_verdict"]
    assert verdict_section["present"] is True
    assert verdict_section["verdict"]["verdict"] in (
        "CONSISTENT",
        "CONSISTENT_WITH_WARNINGS",
        "INCONSISTENT",
    )
    assert "checked_at" not in verdict_section["verdict"]


def test_supervisor_verdict_absent_reports_data_absent(db):
    verdict_section = _bundle(db, missing_optional_data(db))["supervisor_verdict"]
    assert verdict_section["present"] is False
    assert verdict_section["availability_status"] == "data_absent"


# ── Ordering rules are documented ────────────────────────────────────


def test_every_ordered_collection_has_a_documented_rule():
    """``ORDERING_RULES`` is the human-readable record of every sort key."""
    for path in (
        "parties.directors",
        "parties.ubos",
        "parties.intermediaries",
        "documents.active",
        "document_gate.expectations",
        "screening.reviews",
        "edd.cases",
        "decision.records",
        "monitoring.periodic_reviews",
        "risk.factors",
        "risk.dimensions",
    ):
        assert path in ORDERING_RULES, f"undocumented ordering for {path}"


def test_absent_fields_lists_are_sorted_everywhere(db):
    bundle = _bundle(db, complete_standard(db))

    def walk(node):
        if isinstance(node, dict):
            if "absent_fields" in node:
                assert node["absent_fields"] == sorted(node["absent_fields"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(bundle)


def test_bundle_is_json_serialisable_end_to_end(db):
    """Canonical JSON round-trips, so the bundle can be stored or exported."""
    bundle = _bundle(db, complete_standard(db))
    assert json.loads(canonical_json(bundle))
