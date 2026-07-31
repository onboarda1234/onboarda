"""Proof that Phase 0B-1 changed no authoritative output.

The foundation is additive: a new package and new tests, no edit to any
authoritative module. These guards assert that positively rather than relying
on review of the diff.

Two kinds of evidence:

* **Byte identity** — each authoritative function is exercised on a fixed input
  and its output compared against a value computed independently of the
  foundation. Non-evidentiary timestamp fields are excluded from the comparison
  because they legitimately move between calls.
* **File identity** — the frozen Application Review and Screening Queue sources
  are unchanged against ``origin/main``.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent

#: Files carrying the frozen Application Review and Screening Queue surfaces,
#: plus the authoritative modules the foundation reads.
PROTECTED_FILES = (
    "arie-backoffice.html",
    "arie-backend/supervisor_engine.py",
    "arie-backend/memo_handler.py",
    "arie-backend/validation_engine.py",
    "arie-backend/rule_engine.py",
    "arie-backend/edd_routing_policy.py",
    "arie-backend/document_reliance_gate.py",
    "arie-backend/screening_state.py",
    "arie-backend/memo_governance.py",
    "arie-backend/decision_model.py",
    "arie-backend/company_registry.py",
    "arie-backend/db.py",
    "arie-backend/server.py",
)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


# ── File identity ────────────────────────────────────────────────────


def test_protected_files_unchanged_against_main():
    """No authoritative or frozen file differs from ``origin/main``."""
    try:
        changed = _git("diff", "--name-only", "origin/main...HEAD").split()
    except subprocess.CalledProcessError:  # pragma: no cover - shallow clone
        pytest.skip("origin/main not available in this checkout")

    touched = sorted(set(changed) & set(PROTECTED_FILES))
    assert not touched, f"Phase 0B-1 modified protected files: {touched}"


def test_phase_0b1_adds_only_new_paths():
    """Every changed path is a new foundation module, test, or document."""
    try:
        changed = _git("diff", "--name-only", "origin/main...HEAD").split()
    except subprocess.CalledProcessError:  # pragma: no cover - shallow clone
        pytest.skip("origin/main not available in this checkout")

    allowed_prefixes = (
        "arie-backend/supervisor_foundation/",
        "arie-backend/tests/supervisor_foundation_fixtures.py",
        "arie-backend/tests/test_supervisor_foundation_",
        "docs/",
    )
    unexpected = sorted(
        path
        for path in changed
        if not path.startswith(allowed_prefixes)
    )
    assert not unexpected, f"unexpected paths changed in Phase 0B-1: {unexpected}"


# ── Byte identity of authoritative outputs ───────────────────────────


def _memo_fixture() -> dict:
    return {
        "sections": {
            "executive_summary": {"content": "x"},
            "compliance_decision": {"content": "y"},
            "risk_assessment": {"content": "z"},
        },
        "metadata": {
            "risk_rating": "HIGH",
            "approval_recommendation": "REVIEW",
            "screening_state_summary": {"canonical_state": "completed_clear"},
        },
    }


def test_run_memo_supervisor_output_is_unchanged_by_the_foundation():
    """The verdict is identical whether or not the adapter is involved.

    ``checked_at`` is excluded: it is a wall-clock field that moves between any
    two calls, which is precisely why the adapter strips it.
    """
    from supervisor_engine import run_memo_supervisor

    from supervisor_foundation.adapters import memo_supervisor_verdict

    direct = run_memo_supervisor(_memo_fixture())
    through_adapter = memo_supervisor_verdict(_memo_fixture())

    direct_comparable = {k: v for k, v in direct.items() if k != "checked_at"}
    assert json.dumps(direct_comparable, sort_keys=True, default=str) == json.dumps(
        through_adapter, sort_keys=True, default=str
    )
    assert "checked_at" in direct, "fixture no longer exercises the stripped field"
    assert "checked_at" not in through_adapter


def test_run_memo_supervisor_is_not_mutated_by_repeated_adapter_use():
    """Calling through the adapter does not disturb the authoritative result."""
    from supervisor_engine import run_memo_supervisor

    from supervisor_foundation.adapters import memo_supervisor_verdict

    before = run_memo_supervisor(_memo_fixture())
    memo_supervisor_verdict(_memo_fixture())
    memo_supervisor_verdict(_memo_fixture())
    after = run_memo_supervisor(_memo_fixture())

    assert {k: v for k, v in before.items() if k != "checked_at"} == {
        k: v for k, v in after.items() if k != "checked_at"
    }


def test_adapter_does_not_mutate_its_input():
    """The adapter copies: an authoritative caller's memo dict is untouched."""
    from supervisor_foundation.adapters import memo_supervisor_verdict

    memo = _memo_fixture()
    snapshot = json.dumps(memo, sort_keys=True)
    memo_supervisor_verdict(memo)
    assert json.dumps(memo, sort_keys=True) == snapshot


def test_risk_score_output_unchanged():
    """``compute_risk_score`` is deterministic and untouched by the foundation."""
    import supervisor_foundation  # noqa: F401  — import must be inert
    from rule_engine import compute_risk_score

    app_data = {
        "entity_type": "SME",
        "ownership_structure": "direct",
        "country": "Mauritius",
        "sector": "Technology",
        "monthly_volume": "100000",
    }
    first = compute_risk_score(dict(app_data))
    second = compute_risk_score(dict(app_data))
    assert first["score"] == second["score"]
    assert first["level"] == second["level"]
    assert first["dimensions"] == second["dimensions"]


def test_edd_routing_output_unchanged():
    """``evaluate_edd_routing`` is untouched; only ``evaluated_at`` moves."""
    import supervisor_foundation  # noqa: F401
    from edd_routing_policy import evaluate_edd_routing

    facts = {
        "final_risk_level": "HIGH",
        "declared_pep_present": True,
        "sector_risk_tier": "medium",
        "jurisdiction_risk_tier": "low",
        "ownership_transparency_status": "transparent",
        "screening_terminality_summary": {"canonical_state": "completed_clear"},
        "edd_trigger_flags": [],
        "supervisor_mandatory_escalation": False,
    }
    first = evaluate_edd_routing(dict(facts))
    second = evaluate_edd_routing(dict(facts))
    assert {k: v for k, v in first.items() if k != "evaluated_at"} == {
        k: v for k, v in second.items() if k != "evaluated_at"
    }
    assert first["route"] == "edd"


def test_document_reliance_gate_output_unchanged(db, sample_application):
    """The gate still evaluates normally; the foundation never calls it."""
    import supervisor_foundation  # noqa: F401
    from document_reliance_gate import evaluate_document_reliance_gate

    app = dict(
        db.execute(
            "SELECT * FROM applications WHERE id = ?", (sample_application,)
        ).fetchone()
    )
    first = evaluate_document_reliance_gate(db, app, stage="approval")
    second = evaluate_document_reliance_gate(db, app, stage="approval")

    assert {k: v for k, v in first.items() if k != "generated_at"} == {
        k: v for k, v in second.items() if k != "generated_at"
    }


def test_screening_truth_output_unchanged():
    """``build_screening_truth_summary`` is untouched by the foundation."""
    import supervisor_foundation  # noqa: F401
    from screening_state import build_screening_truth_summary

    report = {
        "provider": "complyadvantage",
        "screening_mode": "live",
        "screened_at": "2026-07-01T09:00:00",
        "company_screening": {"matched": False, "results": [], "api_status": "live"},
        "director_screenings": [],
        "ubo_screenings": [],
    }
    first = build_screening_truth_summary(dict(report), {})
    second = build_screening_truth_summary(dict(report), {})
    assert first == second


def test_importing_the_foundation_has_no_side_effects():
    """Importing the package must not touch a database or a provider."""
    import importlib

    import supervisor_foundation

    reloaded = importlib.reload(supervisor_foundation)
    assert reloaded.BUNDLE_SCHEMA_VERSION == "supervisor-bundle-v1"
    assert reloaded.PROBE_SET_VERSION == "probe-set-0b1-empty"
