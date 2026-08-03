"""Behavioural (Node) tests for the zero-hit evidence detector.

The static suite (test_zero_hit_evidence_state_static.py) asserts on JS source
text. Both independent reviewers made the same point: substring assertions
cannot detect an ADDED accepting condition, and they proved it — two fail-open
mutations passed the static file, including one admitting ``cleared_by_officer``
(a state that has hits by construction).

These tests EXECUTE ``screeningQueueIsTerminalNoHits`` against real row shapes,
so an accepting branch that the source-text checks would happily allow still
fails here. "No provider matches" is an assertion on a compliance screen; the
check behind it must not be satisfiable by missing data.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BACKOFFICE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "arie-backoffice.html",
)


def _extract(html, name):
    start = html.index("function " + name)
    end = html.index("\nfunction ", start + 1)
    return html[start:end]


def _run_node(script):
    assert shutil.which("node"), "Node.js is required for runtime tests"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(script)
        script_path = handle.name
    try:
        result = subprocess.run(
            ["node", script_path],
            cwd=os.path.dirname(BACKOFFICE_PATH),
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def _detect(rows):
    with open(BACKOFFICE_PATH, encoding="utf-8") as handle:
        html = handle.read()
    script = (
        _extract(html, "screeningQueueIsTerminalNoHits")
        + "\nconst rows = "
        + json.dumps(rows)
        + ";\nconsole.log(JSON.stringify(rows.map(function (r) {"
        " return screeningQueueIsTerminalNoHits(r); })));\n"
    )
    return _run_node(script)


CLEAN = {"canonical_status_key": "clear", "total_hits": 0}


def test_the_one_accepted_shape_is_accepted():
    # Guards against over-tightening: the founder-reported card must still get
    # its honest empty state.
    assert _detect([CLEAN]) == [True]


# --- must NOT be accepted -----------------------------------------------------

REJECTED = [
    ("hits present", {"canonical_status_key": "clear", "total_hits": 3}),
    ("total_hits missing", {"canonical_status_key": "clear"}),
    ("total_hits null", {"canonical_status_key": "clear", "total_hits": None}),
    ("total_hits empty string", {"canonical_status_key": "clear", "total_hits": ""}),
    ("total_hits false", {"canonical_status_key": "clear", "total_hits": False}),
    ("total_hits empty list", {"canonical_status_key": "clear", "total_hits": []}),
    ("total_hits string zero", {"canonical_status_key": "clear", "total_hits": "0"}),
    ("review_required", {"canonical_status_key": "review_required", "total_hits": 0}),
    ("failed", {"canonical_status_key": "failed", "total_hits": 0}),
    ("stale", {"canonical_status_key": "stale", "total_hits": 0}),
    ("in_progress", {"canonical_status_key": "screening_in_progress", "total_hits": 0}),
    ("declared pep review", {"canonical_status_key": "declared_pep_review", "total_hits": 0}),
    # A subject cleared BY AN OFFICER has hits by construction — admitting it
    # would render "No provider matches" over real matches. This is the exact
    # mutation that passed the static suite.
    ("cleared_by_officer", {"canonical_status_key": "cleared_by_officer", "total_hits": 0}),
    ("canonical key absent", {"total_hits": 0}),
    # Legacy raw key only: server.py emits screened_no_match for NEVER-SCREENED
    # subjects, so it must not stand in for the canonical key.
    ("legacy status_key only", {"status_key": "clear", "total_hits": 0}),
    ("legacy screened_no_match", {"status_key": "screened_no_match", "total_hits": 0}),
    # Contradiction guards.
    ("evidence capped", {"canonical_status_key": "clear", "total_hits": 0,
                         "evidence_summary": {"evidence_capped": True}}),
    ("items present", {"canonical_status_key": "clear", "total_hits": 0,
                       "screening_evidence": {"items": [{"category": "pep"}]}}),
    ("empty row", {}),
]


@pytest.mark.parametrize("label,row", REJECTED, ids=[r[0] for r in REJECTED])
def test_non_clean_shapes_are_rejected(label, row):
    assert _detect([row]) == [False], (
        f"{label!r} was accepted as a terminal zero-hit subject — the card would "
        f"assert 'No provider matches' for it"
    )


def test_all_rejected_shapes_in_one_pass():
    # Single Node invocation over the whole table, so a regression shows up as
    # one readable diff rather than N separate failures.
    results = _detect([row for _, row in REJECTED])
    accepted = [REJECTED[i][0] for i, ok in enumerate(results) if ok]
    assert not accepted, f"fail-open on: {accepted}"
