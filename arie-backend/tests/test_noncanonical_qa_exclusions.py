"""Static contract for reviewed, noncanonical staging QA exclusions."""

import json
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
REGISTRY_PATH = BACKEND / "fixtures" / "noncanonical_qa_exclusions.json"
RUNNER_PATH = BACKEND / "fixtures" / "tier0c_b_runner.py"
MANIFEST_PATH = BACKEND / "fixtures" / "pilot_canonical_dataset_v1.json"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_pr840_h5_is_formally_noncanonical_and_not_runner_eligible():
    registry = _load(REGISTRY_PATH)

    assert registry["contract"] == "tier0c-b-noncanonical-qa-exclusions-v1"
    assert registry["environment"] == "staging"
    assert registry["selection_effect"] == "none"
    assert registry["runner_scope"] == "RM-PILOT-001 through RM-PILOT-041 only"
    assert registry["fixtures"] == [
        {
            "reference": "ARF-QAFIX-H5-PR840",
            "application_id": "f1xedprc840h5001",
            "classification": "excluded_noncanonical_qa_fixture",
            "origin_pr": 840,
            "origin_merge_sha": "6a769883cdcfc00425d803a1b1136a13fd7b790d",
            "purpose": "Post-merge H5 sanctions-first screening-bucket validation",
            "fixture": True,
            "synthetic": True,
            "non_production": True,
            "canonical_runner_eligible": False,
            "persistent_validation_dependency": False,
            "disposition": "retain_pending_sanctioned_targeted_cleanup",
            "cleanup_constraint": (
                "Do not delete until a reviewed exact-identity cleanup path exists"
            ),
        }
    ]


def test_exclusion_does_not_widen_or_feed_the_exact_41_runner():
    registry = _load(REGISTRY_PATH)
    manifest = _load(MANIFEST_PATH)
    runner_source = RUNNER_PATH.read_text(encoding="utf-8")

    excluded_refs = {row["reference"] for row in registry["fixtures"]}
    canonical_refs = {row["reference"] for row in manifest["scenarios"]}

    assert len(canonical_refs) == 41
    assert excluded_refs.isdisjoint(canonical_refs)
    assert all(not ref.startswith("RM-PILOT-") for ref in excluded_refs)
    assert "noncanonical_qa_exclusions" not in runner_source
    assert "ARF-QAFIX-H5-PR840" not in runner_source
