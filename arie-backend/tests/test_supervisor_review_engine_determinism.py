"""Review engine — determinism, identity, and the boundary.

The reproducibility claim is the regulatory claim: identical inputs give a
byte-identical review, on every host, under every hash seed. Everything else the
Supervisor asserts rests on it, so it is tested behaviourally (run it and
compare bytes) rather than by inspecting the code for things that look
non-deterministic.

The boundary tests are the other half: no clock, no database, no provider, no
write, and nothing authoritative importing this package.
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib
import random
import subprocess
import sys

import pytest

import supervisor_foundation
from supervisor_foundation import canonical_bytes
from supervisor_foundation.aggregation import aggregate_review
from supervisor_foundation.contracts import (
    AGGREGATION_SCHEMA_VERSION,
    BUNDLE_SCHEMA_VERSION,
    PROBE_SET_VERSION,
    REVIEW_SCHEMA_VERSION,
)
from supervisor_probe_fixtures import AS_OF
from supervisor_review_fixtures import (
    MINIMAL_BUNDLE,
    busy_case,  # noqa: F401 - pytest fixture, used by name
    finding,
    real_review,
    screening_subject_finding,
    synthetic_review,
)

PACKAGE_ROOT = pathlib.Path(supervisor_foundation.__file__).parent
AGGREGATION_ROOT = PACKAGE_ROOT / "aggregation"
BACKEND_ROOT = PACKAGE_ROOT.parent


def _aggregate(findings, **kwargs):
    return aggregate_review(synthetic_review(findings, **kwargs), bundle=MINIMAL_BUNDLE)


# ── Determinism ──────────────────────────────────────────────────────


def test_three_runs_are_byte_identical(busy_case, db):
    review, bundle = real_review(db, busy_case)
    outputs = [canonical_bytes(aggregate_review(review, bundle=bundle)) for _ in range(3)]
    assert outputs[0] == outputs[1] == outputs[2]


def test_shuffling_the_finding_order_changes_nothing(busy_case, db):
    """Input order is an accident of probe sequence. It must not reach the output."""
    review, bundle = real_review(db, busy_case)
    baseline = canonical_bytes(aggregate_review(review, bundle=bundle))

    rng = random.Random(20260802)
    for _ in range(5):
        shuffled = dict(review)
        items = list(review["findings"])
        rng.shuffle(items)
        shuffled["findings"] = items
        assert canonical_bytes(aggregate_review(shuffled, bundle=bundle)) == baseline


def test_shuffling_evidence_refs_within_a_finding_changes_nothing(busy_case, db):
    review, bundle = real_review(db, busy_case)
    baseline = canonical_bytes(aggregate_review(review, bundle=bundle))

    rng = random.Random(7)
    reordered = dict(review)
    items = []
    for item in review["findings"]:
        copy = dict(item)
        refs = list(copy["evidence_refs"])
        rng.shuffle(refs)
        copy["evidence_refs"] = refs
        items.append(copy)
    reordered["findings"] = items
    assert canonical_bytes(aggregate_review(reordered, bundle=bundle)) == baseline


def test_the_review_id_is_stable(busy_case, db):
    review, bundle = real_review(db, busy_case)
    first = aggregate_review(review, bundle=bundle)["review_id"]
    second = aggregate_review(review, bundle=bundle)["review_id"]
    assert first == second
    assert first.startswith("rev-")


def test_the_aggregation_hash_is_stable(busy_case, db):
    review, bundle = real_review(db, busy_case)
    hashes = {aggregate_review(review, bundle=bundle)["aggregation_hash"] for _ in range(3)}
    assert len(hashes) == 1


def test_changed_evidence_changes_the_review(busy_case, db):
    """Determinism is worthless without sensitivity: different input, different output."""
    review, bundle = real_review(db, busy_case)
    baseline = aggregate_review(review, bundle=bundle)

    mutated = dict(review)
    mutated["findings"] = list(review["findings"])[:-1]
    changed = aggregate_review(mutated, bundle=bundle)

    assert changed["aggregation_hash"] != baseline["aggregation_hash"]
    assert changed["finding_ids"] != baseline["finding_ids"]


def test_the_probe_review_hash_passes_through_unchanged(busy_case, db):
    """Aggregation reinterprets nothing. The finding-set hash is carried, not recomputed."""
    review, bundle = real_review(db, busy_case)
    output = aggregate_review(review, bundle=bundle)
    assert output["review_hash"] == review["review_hash"]
    assert output["input_bundle_hash"] == review["input_bundle_hash"]


def test_the_three_hashes_answer_three_different_questions(busy_case, db):
    review, bundle = real_review(db, busy_case)
    output = aggregate_review(review, bundle=bundle)
    assert len({output["input_bundle_hash"], output["review_hash"], output["aggregation_hash"]}) == 3


def test_the_review_id_moves_with_as_of(busy_case, db):
    """Two reviews of the same subject at two instants are two reviews."""
    early, bundle_early = real_review(db, busy_case, as_of="2026-07-01T00:00:00Z")
    late, bundle_late = real_review(db, busy_case, as_of=AS_OF)
    assert (
        aggregate_review(early, bundle=bundle_early)["review_id"]
        != aggregate_review(late, bundle=bundle_late)["review_id"]
    )


def test_as_of_reaches_only_the_time_fields(busy_case, db):
    """Changing the instant must not silently change what was found."""
    early, bundle_early = real_review(db, busy_case, as_of="2026-07-01T00:00:00Z")
    late, bundle_late = real_review(db, busy_case, as_of=AS_OF)

    first = aggregate_review(early, bundle=bundle_early)
    second = aggregate_review(late, bundle=bundle_late)

    assert first["as_of"] != second["as_of"]
    assert first["created_at"] == first["as_of"]
    # The claims themselves are the same case, so the grouped concerns match.
    assert [group["check_id"] for group in first["grouped_findings"]] == [
        group["check_id"] for group in second["grouped_findings"]
    ]


def test_versions_are_carried_and_not_redefined(busy_case, db):
    review, bundle = real_review(db, busy_case)
    output = aggregate_review(review, bundle=bundle)
    assert output["bundle_schema_version"] == BUNDLE_SCHEMA_VERSION
    assert output["probe_set_version"] == PROBE_SET_VERSION
    assert output["review_schema_version"] == REVIEW_SCHEMA_VERSION
    assert output["aggregation_schema_version"] == AGGREGATION_SCHEMA_VERSION
    assert AGGREGATION_SCHEMA_VERSION != REVIEW_SCHEMA_VERSION


def test_branding_and_environment_changes_do_not_reach_the_output(busy_case, db, monkeypatch):
    """Same lesson as the probe layer: configuration is not review input."""
    import branding

    review, bundle = real_review(db, busy_case)
    baseline = canonical_bytes(aggregate_review(review, bundle=bundle))

    original = dict(branding.BRAND)
    try:
        branding.BRAND.update({"platform_name": "Zephyr", "portal_name": "Zephyr Portal"})
        monkeypatch.setenv("BRAND_PORTAL_NAME", "Zephyr Portal")
        monkeypatch.setenv("ENVIRONMENT", "some-other-environment")
        assert canonical_bytes(aggregate_review(review, bundle=bundle)) == baseline
    finally:
        branding.BRAND.clear()
        branding.BRAND.update(original)


def test_the_output_is_stable_across_processes_and_hash_seeds(busy_case, db, tmp_path):
    """A different ``PYTHONHASHSEED`` reorders every set and dict iteration.

    Run in a subprocess rather than in-process: ``PYTHONHASHSEED`` is read at
    interpreter start, so an in-process monkeypatch would prove nothing.
    """
    review, bundle = real_review(db, busy_case)
    payload = tmp_path / "input.json"
    payload.write_text(json.dumps({"review": review, "bundle": bundle}), encoding="utf-8")

    script = (
        "import json,sys;"
        "sys.path.insert(0, sys.argv[1]);"
        "from supervisor_foundation import canonical_bytes;"
        "from supervisor_foundation.aggregation import aggregate_review;"
        "d=json.load(open(sys.argv[2]));"
        "sys.stdout.write("
        "canonical_bytes(aggregate_review(d['review'], bundle=d['bundle'])).hex())"
    )

    digests = set()
    for seed in ("0", "1", "12345"):
        result = subprocess.run(
            [sys.executable, "-c", script, str(BACKEND_ROOT), str(payload)],
            capture_output=True,
            text=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
            check=False,
            # A blocked child would otherwise stall CI with no diagnostic.
            timeout=300,
        )
        assert result.returncode == 0, result.stderr[-2000:]
        digests.add(result.stdout.strip())

    assert len(digests) == 1
    assert digests.pop() == canonical_bytes(aggregate_review(review, bundle=bundle)).hex()


def test_identifiers_are_content_derived_not_counters():
    """Different content, different identifier — and no positional reuse.

    A counter would give the first group of every review the same id, which is
    the failure that makes two reviews impossible to diff.
    """
    first = _aggregate([screening_subject_finding("s1")])
    second = _aggregate([screening_subject_finding("s2")])

    assert first["grouped_findings"][0]["group_id"] != second["grouped_findings"][0]["group_id"]
    # The action text is identical but the findings behind it are not, so the
    # action identity differs too: an action id names a specific piece of work.
    assert first["required_actions"][0]["action"] == second["required_actions"][0]["action"]
    assert first["required_actions"][0]["action_id"] != second["required_actions"][0]["action_id"]
    # ``review_id`` is a function of its declared inputs and nothing else. These
    # two fixtures share a ``review_hash``, so they share an id — which is the
    # property, not a coincidence: the id names the finding set, not the run.
    assert first["review_hash"] == second["review_hash"]
    assert first["review_id"] == second["review_id"]


# ── Safety and boundary ──────────────────────────────────────────────


def _aggregation_sources():
    return sorted(AGGREGATION_ROOT.rglob("*.py"))


def test_the_aggregation_package_has_modules_to_scan():
    names = {path.name for path in _aggregation_sources()}
    assert {"engine.py", "checks.py", "limitations.py", "__init__.py"} <= names


FORBIDDEN_CLOCK_ATTRIBUTES = {
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("datetime", "today"),
    ("date", "today"),
    ("time", "time"),
    ("time", "monotonic"),
}
FORBIDDEN_CLOCK_NAMES = {"now", "utcnow", "today", "monotonic", "perf_counter"}


@pytest.mark.parametrize("path", _aggregation_sources(), ids=lambda p: p.name)
def test_no_clock_in_the_aggregation_package(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offences = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if (func.value.id, func.attr) in FORBIDDEN_CLOCK_ATTRIBUTES:
                offences.append(f"{func.value.id}.{func.attr}() line {node.lineno}")
        elif isinstance(func, ast.Name) and func.id in FORBIDDEN_CLOCK_NAMES:
            offences.append(f"{func.id}() line {node.lineno}")
    assert not offences, f"{path.name} reads a clock: {offences}"


@pytest.mark.parametrize("path", _aggregation_sources(), ids=lambda p: p.name)
def test_no_database_or_provider_access_in_the_aggregation_package(path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    banned_calls = {"execute", "executemany", "commit", "cursor", "connect", "fetchone", "fetchall"}
    offences = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in banned_calls:
                offences.append(f"{node.func.attr}() line {node.lineno}")
    assert not offences, f"{path.name} touches a database: {offences}"

    for token in ("requests.", "httpx.", "urllib.request", "sqlite3", "psycopg"):
        assert token not in source, f"{path.name} references {token}"


@pytest.mark.parametrize("path", _aggregation_sources(), ids=lambda p: p.name)
def test_the_aggregation_package_imports_only_audited_modules(path):
    """Nothing authoritative, nothing that reaches a provider, nothing branded."""
    permitted_stdlib = {
        "__future__",
        "ast",
        "collections",
        "dataclasses",
        "enum",
        "hashlib",
        "json",
        "typing",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:  # relative: within the foundation, already audited
                continue
            root = (node.module or "").split(".")[0]
            assert root in permitted_stdlib, f"{path.name} imports {node.module}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root in permitted_stdlib, f"{path.name} imports {alias.name}"


def test_no_authoritative_module_imports_the_aggregation_package():
    """The one-way boundary. Authoritative behaviour cannot depend on advice."""
    # Recursive: authoritative code lives in sub-packages too, and a top-level
    # glob would have exempted every one of them. The foundation itself and the
    # tests are excluded — they are the two places that may import it.
    exempt = {PACKAGE_ROOT, BACKEND_ROOT / "tests"}
    importers = []
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        if any(parent in exempt for parent in path.parents):
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        if "supervisor_foundation.aggregation" in source or "from .aggregation" in source:
            importers.append(str(path.relative_to(BACKEND_ROOT)))
    assert importers == [], f"authoritative modules import the review engine: {importers}"
    assert len(list(BACKEND_ROOT.rglob("*.py"))) > 100, "the scan found almost nothing"


def test_the_probe_runner_does_not_import_the_aggregation_package():
    """Aggregation consumes the runner, never the reverse.

    Checked by AST rather than by substring: the word may legitimately appear in
    a docstring, and a test that fails on prose is a test people delete.
    """
    tree = ast.parse((PACKAGE_ROOT / "review.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "aggregation" not in (node.module or "")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert "aggregation" not in alias.name


def test_aggregating_writes_nothing(busy_case, db):
    counts_before = _table_counts(db)
    review, bundle = real_review(db, busy_case)
    aggregate_review(review, bundle=bundle)
    assert _table_counts(db) == counts_before


def _table_counts(db) -> dict[str, int]:
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    counts = {}
    for row in rows:
        name = row[0] if not isinstance(row, dict) else row["name"]
        # Table names come from sqlite_master, not from user input.
        counts[name] = db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]  # noqa: S608
    return counts


def test_aggregating_does_not_mutate_its_inputs(busy_case, db):
    review, bundle = real_review(db, busy_case)
    review_before = canonical_bytes(review)
    bundle_before = canonical_bytes(bundle)
    aggregate_review(review, bundle=bundle)
    assert canonical_bytes(review) == review_before
    assert canonical_bytes(bundle) == bundle_before


def test_the_engine_signature_takes_no_database_handle():
    parameters = set(inspect.signature(aggregate_review).parameters)
    assert parameters == {"review", "bundle"}


# ── No LLM ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", _aggregation_sources(), ids=lambda p: p.name)
def test_no_model_call_anywhere_in_the_package(path):
    source = path.read_text(encoding="utf-8").lower()
    for token in (
        "anthropic",
        "claude_client",
        "openai",
        "completion(",
        "chat(",
        "prompt=",
        "messages.create",
    ):
        assert token not in source, f"{path.name} references {token}"


def test_the_output_carries_no_free_prose_field(busy_case, db):
    """Framework §8.2: no summary, no narrative, no overall opinion."""
    review, bundle = real_review(db, busy_case)
    output = aggregate_review(review, bundle=bundle)
    for banned in ("summary", "narrative", "narration", "assessment_narrative",
                   "overall_opinion", "recommendation", "ai_source"):
        assert banned not in output


def test_no_output_text_is_unfalsifiable(busy_case, db):
    """The banned constructions from §8.2, asserted over real rendered output."""
    review, bundle = real_review(db, busy_case)
    output = aggregate_review(review, bundle=bundle)
    blob = json.dumps(output).lower()
    for phrase in (
        "appears satisfactory",
        "consider further review",
        "risk may be elevated",
        "no significant issues",
        "should be reviewed",
        "potentially problematic",
    ):
        assert phrase not in blob


def test_the_review_never_calls_the_customer_compliant(busy_case, db):
    review, bundle = real_review(db, busy_case)
    blob = json.dumps(aggregate_review(review, bundle=bundle)).lower()
    for phrase in ("is compliant", "non-compliant", "is not compliant", "customer is safe"):
        assert phrase not in blob
