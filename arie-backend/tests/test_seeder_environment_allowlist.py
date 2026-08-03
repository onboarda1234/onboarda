"""Pilot audit — the QA fixture seeder must fail CLOSED on unknown environments.

The original guard refused only the literal string ``"production"``. Every
other value passed it: unset, empty, "prod", "PRODUCTION " with stray
whitespace, or any unrecognised label.

Such a run nevertheless failed closed, because ``seed_screening_qa_fixtures``
wipes before it inserts and the wipe enters the ``fixture_cleanup_nonprod``
regulated-delete context, which refuses anything outside testing/test/staging —
so no SQL executed. This guard is therefore **defense in depth and legibility,
not the closing of a reachable production-write path**: the seeder now fails at
its own boundary with an actionable message rather than surfacing an opaque
ValueError from the deletion layer, and its declared policy no longer claims
more than the enforcing layer allows.

The allow-list must stay a subset of the regulated-deletion policy — pinned by
``test_allow_list_is_a_subset_of_the_regulated_deletion_policy`` below.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import seed_screening_qa_fixtures as seeder


def _guard_with(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("ENVIRONMENT", value)
    return seeder._guard_environment


# --- fail-closed on anything not explicitly allowed --------------------------

@pytest.mark.parametrize(
    "value",
    [
        None,            # unset — the production-misconfiguration case
        "",
        "   ",
        "production",
        "PRODUCTION",
        " production ",
        "prod",          # near-miss the old guard let through
        "pilot",
        "live",
        "unknown-env",
    ],
)
def test_seeder_refuses_unknown_or_production_environments(monkeypatch, value):
    guard = _guard_with(monkeypatch, value)
    with pytest.raises(RuntimeError) as exc:
        guard()
    # The error names what was seen and what is allowed.
    assert "refuses to run" in str(exc.value)
    assert "allowed only in" in str(exc.value)


def test_unset_environment_names_itself_in_the_error(monkeypatch):
    guard = _guard_with(monkeypatch, None)
    with pytest.raises(RuntimeError) as exc:
        guard()
    assert "(unset)" in str(exc.value)


# --- the legitimate non-production lanes still work --------------------------

@pytest.mark.parametrize("value", sorted(seeder.SEEDER_ALLOWED_ENVIRONMENTS))
def test_allowed_environments_pass(monkeypatch, value):
    _guard_with(monkeypatch, value)()


def test_allowed_environments_are_case_and_whitespace_insensitive(monkeypatch):
    _guard_with(monkeypatch, "  Staging  ")()


def test_allow_list_contains_no_production_like_label():
    allowed = seeder.SEEDER_ALLOWED_ENVIRONMENTS
    assert "production" not in allowed
    assert "prod" not in allowed
    assert "pilot" not in allowed
    # demo.regmind.co is a real deployed environment and must not be seedable.
    assert "demo" not in allowed
    # Staging is intentionally allowed — it is where the QA fixtures are
    # legitimately seeded for validation runs.
    assert "staging" in allowed


def test_allow_list_is_a_subset_of_the_regulated_deletion_policy():
    """Every advertised environment must actually survive the enforcing layer.

    ``seed_screening_qa_fixtures`` wipes before it inserts, and the wipe enters
    the ``fixture_cleanup_nonprod`` regulated-delete context. If this guard
    advertises an environment that context then rejects, the operator is sent
    down a dead end and the seeder's declared policy is wider than anything
    that enforces it. Asserted behaviourally against the real context rather
    than by duplicating its literal, so the two cannot drift.
    """
    for environment in sorted(seeder.SEEDER_ALLOWED_ENVIRONMENTS):
        with _cleanup_context(environment):
            pass  # entering the context must not raise for an advertised env


def test_environment_the_deletion_layer_rejects_is_not_advertised():
    # Counter-check: an env outside the enforcing policy must both be refused
    # by that policy AND be absent from the seeder's allow-list.
    for environment in ("development", "dev", "local", "demo", "production"):
        with pytest.raises(ValueError):
            with _cleanup_context(environment):
                pass
        assert environment not in seeder.SEEDER_ALLOWED_ENVIRONMENTS


def _cleanup_context(environment):
    from regulated_deletion import sanctioned_delete_context

    return sanctioned_delete_context(
        "fixture_cleanup_nonprod",
        actor_id="test",
        role="system",
        reason="allow-list subset check",
        allowed_tables=("screening_reviews",),
        environment=environment,
        is_fixture=True,
        confirmed=True,
    )
