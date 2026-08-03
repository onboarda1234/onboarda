"""Proof that the Supervisor foundation never reads a wall clock.

``as_of`` is injected by the caller and is the only permitted time source. Two
independent proofs, because either alone is escapable:

* **Static** — the AST of every module in the package is walked for calls to a
  clock. Catches code that is not exercised by a test.
* **Behavioural** — every clock function in ``datetime`` and ``time`` is
  replaced with a raising stub for the duration of a full assemble-and-review.
  Catches a transitive call through an authoritative module.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import supervisor_foundation
from supervisor_foundation import assemble_application_bundle, run_review
from supervisor_foundation_fixtures import AS_OF, SYNTHETIC_PROBES, complete_standard

PACKAGE_ROOT = pathlib.Path(supervisor_foundation.__file__).parent

#: Attribute calls that read a wall clock.
FORBIDDEN_ATTRIBUTES = {
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("datetime", "today"),
    ("date", "today"),
    ("time", "time"),
    ("time", "monotonic"),
    ("time", "time_ns"),
}

#: Bare names that read a wall clock if imported directly.
FORBIDDEN_NAMES = {"monotonic", "perf_counter", "time_ns"}


def _module_paths() -> list[pathlib.Path]:
    """Every module in the package, including the ``probes`` sub-package.

    Recursive by design: a probe is the code most likely to want a clock, and a
    non-recursive glob would have quietly exempted the whole probe set.
    """
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def test_package_has_modules_to_check():
    """Guard against the static scan silently passing on an empty file list."""
    names = {path.name for path in _module_paths()}
    assert len(names) >= 6
    # Named explicitly so a probe module dropping out of the scan is a failure,
    # not a silently smaller list.
    assert {
        "risk_resolution.py",
        "edd_divergence.py",
        "screening_defensibility.py",
        "monitoring_requirement.py",
        # The aggregation package renders every sentence an officer reads. A
        # clock reaching it would put the run time into review_hash's sibling
        # hash and make the artifact unreproducible.
        "engine.py",
        "checks.py",
        "limitations.py",
    } <= names


@pytest.mark.parametrize("path", _module_paths(), ids=lambda p: p.name)
def test_no_clock_call_in_source(path: pathlib.Path):
    """No module in the package calls a wall clock."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offences: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if (func.value.id, func.attr) in FORBIDDEN_ATTRIBUTES:
                offences.append(f"{func.value.id}.{func.attr}() at line {node.lineno}")
        elif isinstance(func, ast.Name) and func.id in FORBIDDEN_NAMES:
            offences.append(f"{func.id}() at line {node.lineno}")

    assert not offences, f"{path.name} reads a wall clock: {offences}"


def _warm_lazy_imports():
    """Import every lazily-imported dependency before any clock is patched.

    The foundation imports its authoritative helpers inside functions. If one of
    them were first imported while ``datetime.datetime`` was patched, it would
    capture the patched class permanently via ``from datetime import datetime``
    and poison the rest of the test session.
    """
    import company_registry  # noqa: F401
    import document_reliance_gate  # noqa: F401
    import environment  # noqa: F401
    import memo_governance  # noqa: F401
    import supervisor_engine  # noqa: F401


def test_assemble_and_review_complete_with_every_clock_disabled(db, monkeypatch):
    """A full run succeeds while every clock function raises.

    Run against an application with no memo, so the one audited adapter that
    reaches a clock transitively (``run_memo_supervisor``, for ``checked_at``)
    is not invoked. Everything else in the assembler and runner must complete
    with no clock available at all.

    This would fail if the assembler called
    ``evaluate_document_reliance_gate`` or ``build_screening_truth_summary``,
    both of which reach a clock transitively and both of which are deliberately
    not called (see ``supervisor_foundation.adapters``).
    """
    import datetime as datetime_module
    import time as time_module

    from supervisor_foundation_fixtures import missing_optional_data

    _warm_lazy_imports()
    application_id = missing_optional_data(db)

    class Tripwire(datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            raise AssertionError("supervisor foundation called datetime.now()")

        @classmethod
        def utcnow(cls):
            raise AssertionError("supervisor foundation called datetime.utcnow()")

        @classmethod
        def today(cls):
            raise AssertionError("supervisor foundation called datetime.today()")

    def forbidden_time(*args, **kwargs):
        raise AssertionError("supervisor foundation called a time function")

    monkeypatch.setattr(datetime_module, "datetime", Tripwire)
    monkeypatch.setattr(time_module, "time", forbidden_time)
    monkeypatch.setattr(time_module, "monotonic", forbidden_time)

    bundle = assemble_application_bundle(db, application_id, as_of=AS_OF)
    review = run_review(bundle, probes=SYNTHETIC_PROBES)

    assert review["as_of"] == AS_OF
    assert review["review_hash"]

    # And again with the real probe set: the shipped probes must complete with
    # no clock available at all, not merely avoid one in their source text.
    from supervisor_foundation.probes import PROBES

    with_probes = run_review(bundle, probes=PROBES)
    assert with_probes["finding_count"] == len(PROBES)
    assert with_probes["review_hash"]


def test_memo_supervisor_clock_cannot_influence_the_bundle(db, monkeypatch):
    """The one audited transitive clock is provably inert.

    ``run_memo_supervisor`` reads a clock for its terminal ``checked_at`` field.
    Rather than forbid the call — the merged specification sanctions it behind a
    stripping adapter — this proves the clock cannot reach the output: two
    assemblies under two different wall-clock instants are byte-identical.

    The monkeypatch here targets the test process only. No authoritative module
    is modified on disk to make it deterministic.
    """
    import datetime as datetime_module

    import supervisor_engine

    from supervisor_foundation import canonical_bytes

    _warm_lazy_imports()
    application_id = complete_standard(db)

    def clock_at(fixed_iso: str):
        class FixedClock(datetime_module.datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime_module.datetime.fromisoformat(fixed_iso)

        return FixedClock

    encodings = []
    for instant in ("2020-01-01T00:00:00", "2031-12-31T23:59:59"):
        monkeypatch.setattr(supervisor_engine, "datetime", clock_at(instant))
        bundle = assemble_application_bundle(db, application_id, as_of=AS_OF)
        encodings.append(canonical_bytes(bundle))

    assert encodings[0] == encodings[1], (
        "the memo-supervisor clock reached the bundle; checked_at is not being "
        "stripped"
    )

    # And the verdict really was produced — otherwise this proves nothing.
    bundle = assemble_application_bundle(db, application_id, as_of=AS_OF)
    assert bundle["supervisor_verdict"]["present"] is True
    assert "checked_at" not in bundle["supervisor_verdict"]["verdict"]


def test_as_of_is_required(db):
    """Omitting ``as_of`` fails loudly rather than defaulting to now."""
    from supervisor_foundation.bundle import BundleAssemblyError

    application_id = complete_standard(db)
    with pytest.raises(BundleAssemblyError, match="as_of is required"):
        assemble_application_bundle(db, application_id, as_of=None)


def test_as_of_rejects_unparseable_input(db):
    from supervisor_foundation.bundle import BundleAssemblyError

    application_id = complete_standard(db)
    with pytest.raises(BundleAssemblyError, match="ISO-8601"):
        assemble_application_bundle(db, application_id, as_of="not-a-timestamp")


def test_naive_and_offset_as_of_normalise_to_the_same_instant(db):
    """Equivalent instants produce one bundle, not three."""
    from supervisor_foundation import compute_input_bundle_hash

    _warm_lazy_imports()
    application_id = complete_standard(db)
    hashes = {
        compute_input_bundle_hash(
            assemble_application_bundle(db, application_id, as_of=value)
        )
        for value in (
            "2026-07-31T00:00:00Z",
            "2026-07-31T00:00:00",
            "2026-07-31T04:00:00+04:00",
        )
    }
    assert len(hashes) == 1
