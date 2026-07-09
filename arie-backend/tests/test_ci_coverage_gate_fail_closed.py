"""P11-9 / BSA-018 — the CI coverage gate fails CLOSED.

The threshold step previously did `exit 0` when it could not parse a coverage
percentage — a broken coverage command (or changed output format) silently
disabled enforcement while CI stayed green. The gate now fails on an empty OR
non-numeric COV (review fold: `[ "$COV" -lt 30 ]` errors on garbage inside an
if-condition, which bash errexit exempts, so garbage previously fell through
to the PASS echo).

No YAML parser is used (review fold: PyYAML is not in the CI dependency
closure) — the step script is extracted textually from the workflow file.
"""
import os
import re
import subprocess

_CI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".github", "workflows", "ci.yml")


def _coverage_step_script():
    """Extract the 'Coverage threshold check' step's run block textually:
    the lines after its `run: |` that are indented deeper than the `run:` key."""
    with open(_CI, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    start = next(i for i, l in enumerate(lines)
                 if l.strip() == "- name: Coverage threshold check")
    run_idx = next(i for i in range(start, len(lines))
                   if lines[i].strip().startswith("run:"))
    run_indent = len(lines[run_idx]) - len(lines[run_idx].lstrip())
    body = []
    for line in lines[run_idx + 1:]:
        if line.strip() and (len(line) - len(line.lstrip())) <= run_indent:
            break
        body.append(line)
    script = "\n".join(body)
    assert script.strip(), "coverage step script extraction failed"
    return script


def test_gate_fails_closed_on_empty_and_garbage():
    script = _coverage_step_script()
    guard = re.search(r"case \"\$COV\" in(.*?)esac", script, re.DOTALL)
    assert guard, "empty/non-numeric COV guard missing from coverage step"
    assert "exit 1" in guard.group(1)
    assert "exit 0" not in guard.group(1)


def test_threshold_branch_still_enforces_minimum():
    script = _coverage_step_script()
    assert 'if [ "$COV" -lt 30 ]' in script
    assert script.count("exit 1") >= 2  # guard + below-threshold branch


def test_gate_logic_executes_fail_closed():
    """Execute the gate's decision logic (COV stubbed, the real COV-assignment
    line dropped) under `bash -e` — GitHub's default run shell is `bash -e {0}`.

    The dropped line is matched on the `COV=$(` command-substitution prefix, not
    a specific command, so it keeps working whether the step scrapes coverage via
    `pytest --cov-report=term` or reuses the data with `coverage report` — either
    would otherwise clobber the stubbed COV and defeat the fail-closed cases."""
    script = _coverage_step_script()
    logic = "\n".join(
        line for line in script.splitlines()
        if not line.strip().startswith("COV=$(")
    )
    cases = (
        ("", 1),            # empty — the original BSA-018 hole
        ("12", 1),          # numeric below threshold
        ("TOTAL", 1),       # review fold: non-numeric garbage
        ("12\n15", 1),      # review fold: multiple TOTAL lines
        ("garbage%", 1),    # review fold: unit suffix survived tr
        ("47", 0),          # healthy pass
    )
    for cov, expected in cases:
        proc = subprocess.run(
            ["bash", "-e", "-c", f'COV="{cov}"\n{logic}'],
            capture_output=True, text=True)
        assert proc.returncode == expected, (
            f"COV={cov!r}: expected exit {expected}, got {proc.returncode}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
