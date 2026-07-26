"""Pilot audit — the QA fixture seeder must fail CLOSED on unknown environments.

The original guard refused only the literal string ``"production"``. Every
other value passed: unset, empty, "prod", "PRODUCTION " with stray whitespace,
or any unrecognised label. A production database whose ``ENVIRONMENT`` was
never wired would therefore accept a fixture seed, and the applications INSERT
has no second barrier (the regulated-delete context wraps only the
``screening_reviews`` DELETE).

The guard is now an allow-list of known non-production environments.
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
    # Staging is intentionally allowed — it is where the QA fixtures are
    # legitimately seeded for validation runs.
    assert "staging" in allowed
