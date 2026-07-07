"""P11-9 / BSA-018 — the CI coverage gate fails CLOSED.

The threshold step previously did `exit 0` when it could not parse a coverage
percentage — a broken coverage command (or changed output format) silently
disabled enforcement while CI stayed green. The empty-COV branch now exits 1.
"""
import os
import re
import subprocess

import yaml

_CI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".github", "workflows", "ci.yml")


def _coverage_step_script():
    with open(_CI, encoding="utf-8") as fh:
        wf = yaml.safe_load(fh)
    for job in wf.get("jobs", {}).values():
        for step in job.get("steps", []):
            if step.get("name") == "Coverage threshold check":
                return step["run"]
    raise AssertionError("Coverage threshold check step not found in ci.yml")


def test_empty_coverage_fails_the_gate():
    script = _coverage_step_script()
    empty_branch = re.search(r'if \[ -z "\$COV" \]; then(.*?)fi', script, re.DOTALL)
    assert empty_branch, "empty-COV guard missing from coverage step"
    body = empty_branch.group(1)
    assert "exit 1" in body, "unparseable coverage must FAIL the gate (BSA-018)"
    assert "exit 0" not in body


def test_threshold_branch_still_enforces_minimum():
    script = _coverage_step_script()
    assert 'if [ "$COV" -lt 30 ]' in script
    assert script.count("exit 1") >= 2  # empty-COV branch + below-threshold branch


def test_gate_logic_executes_fail_closed():
    """Run the gate's decision logic (extracted, with COV stubbed) to prove
    behaviour, not just source text: empty -> 1, below -> 1, ok -> 0."""
    script = _coverage_step_script()
    # keep only the decision logic (drop the pytest invocation line)
    logic = "\n".join(
        line for line in script.splitlines()
        if not line.strip().startswith("COV=$(pytest")
    )
    for cov, expected in (("", 1), ("12", 1), ("47", 0)):
        proc = subprocess.run(
            ["bash", "-c", f'COV="{cov}"\n{logic}'],
            capture_output=True, text=True)
        assert proc.returncode == expected, (
            f"COV={cov!r}: expected exit {expected}, got {proc.returncode}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
